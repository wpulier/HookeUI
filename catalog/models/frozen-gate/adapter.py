"""Decide baseline vs challenger on two fold scores under a rule frozen before either score existed.

The app checks the rule's provenance before this runs: its `rule` input must be a Study's frozen
rule, frozen before every other input was made (ADR 0031). This function only applies the rule.

Rule (`frozen-gate-v1`, JSON):
  {"format": "frozen-gate-v1", "name": "...", "baseline": "m1r", "challenger": "c3",
   "metrics": {"pds_cosine": {"direction": "higher", "span": 0.455}, ...},
   "ratioMetrics": {"expr_mse_unbiased_capped_norm": ["expr_mse_unbiased_capped", "expr_distance_unbiased"]},
   "subsets": {"weakest-third": ["TARGET", ...]},
   "conditions": [{"measure": "overall", "min": -0.005},
                  {"measure": "subset", "subset": "weakest-third", "min": 0.02},
                  {"measure": "each-metric", "min": -0.10}]}

Scaled gain of a metric = (challenger - baseline) / span, sign-corrected so + is better. `overall` is
their mean over the rule's metrics on the whole fold; `each-metric` is the smallest of them; `subset`
is the mean over metrics of the scaled gain of per-target means on the subset's targets (a ratio metric
uses sum(numerator) / sum(denominator) over the subset). The challenger passes only if every
condition holds. Spans are the rule's declared approximation, not the organizer's anchors.
"""
import json
import math

MEASURES = ('overall', 'each-metric', 'subset')


class Refused(ValueError):
    pass


def _rule(path):
    rule = json.loads(path.read_text())
    if not isinstance(rule, dict) or rule.get('format') != 'frozen-gate-v1':
        raise Refused('The rule is a frozen-gate-v1 JSON document')
    metrics = rule.get('metrics')
    if not isinstance(metrics, dict) or not metrics or not all(
            isinstance(v, dict) and v.get('direction') in ('higher', 'lower') and isinstance(v.get('span'), (int, float))
            and v['span'] > 0 for v in metrics.values()):
        raise Refused('Each rule metric has a direction (higher or lower) and a positive span')
    subsets = rule.get('subsets', {})
    if not isinstance(subsets, dict) or not all(isinstance(v, list) and v and all(isinstance(t, str) for t in v)
                                                for v in subsets.values()):
        raise Refused('Subsets name their targets explicitly')
    conditions = rule.get('conditions')
    if not isinstance(conditions, list) or not conditions:
        raise Refused('The rule needs at least one condition')
    for c in conditions:
        if (not isinstance(c, dict) or c.get('measure') not in MEASURES or not isinstance(c.get('min'), (int, float))
                or (c['measure'] == 'subset') != ('subset' in c) or ('subset' in c and c['subset'] not in subsets)):
            raise Refused('A condition is {measure: overall|each-metric|subset, min, subset (for subset)}')
    ratios = rule.get('ratioMetrics', {})
    if not isinstance(ratios, dict) or not all(isinstance(v, list) and len(v) == 2 for v in ratios.values()):
        raise Refused('ratioMetrics maps a metric to [numerator, denominator]')
    for key in ('baseline', 'challenger', 'name'):
        if not isinstance(rule.get(key), str) or not rule[key]:
            raise Refused(f'The rule names its {key}')
    return rule


def _score(path):
    value = json.loads(path.read_text())
    if value.get('format') != 'vcc-fold-score-v1':
        raise Refused('Compare two vcc-fold-score results')
    return value


def _gain(metric, spec, challenger, baseline):
    if challenger is None or baseline is None:
        return None
    delta = (challenger - baseline) * (1 if spec['direction'] == 'higher' else -1)
    return delta / spec['span']


def _subset_value(score, metric, targets, ratios):
    per = score['perTarget']
    if metric in ratios:
        num, den = (per.get(m, {}) for m in ratios[metric])
        n = [num.get(t) for t in targets]
        d = [den.get(t) for t in targets]
        pairs = [(a, b) for a, b in zip(n, d) if a is not None and b is not None]
        total = sum(b for _, b in pairs)
        return sum(a for a, _ in pairs) / total if pairs and total else None
    values = [per.get(metric, {}).get(t) for t in targets]
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def run(inputs, params, output, check):
    rule = _rule(inputs['rule'])
    baseline, challenger = _score(inputs['baseline']), _score(inputs['challenger'])
    if (baseline['preset'], baseline['versions']) != (challenger['preset'], challenger['versions']):
        raise Refused('Both scores must come from the same evaluator, version and preset')
    metrics, ratios = rule['metrics'], rule.get('ratioMetrics', {})
    gains = {}
    for metric, spec in metrics.items():
        gains[metric] = _gain(metric, spec, challenger['aggregate'].get(metric), baseline['aggregate'].get(metric))
        if gains[metric] is None:
            raise Refused(f'{metric} is missing from a score')
    overall = sum(gains.values()) / len(gains)
    subsets = {}
    for name, targets in rule.get('subsets', {}).items():
        part = [_gain(m, s, _subset_value(challenger, m, targets, ratios), _subset_value(baseline, m, targets, ratios))
                for m, s in metrics.items()]
        part = [g for g in part if g is not None]
        subsets[name] = sum(part) / len(part) if part else None
    results = []
    for c in rule['conditions']:
        value = {'overall': overall, 'each-metric': min(gains.values()), 'subset': subsets.get(c.get('subset'))}[c['measure']]
        results.append({**c, 'value': value, 'holds': value is not None and math.isfinite(value) and value >= c['min']})
    passed = all(r['holds'] for r in results)
    decision = {'format': 'frozen-gate-decision-v1', 'rule': rule['name'], 'baseline': rule['baseline'],
                'challenger': rule['challenger'], 'passed': passed,
                'decision': rule['challenger'] if passed else rule['baseline'],
                'scaledGain': gains, 'overall': overall, 'subsets': subsets, 'conditions': results,
                'meaning': 'raw-metric differences over the rule\'s declared spans; an approximation of scaled scores, decided by a rule frozen before the scores'}
    path = output / 'decision.json'
    path.write_text(json.dumps(decision, sort_keys=True, allow_nan=False))
    return path, decision
