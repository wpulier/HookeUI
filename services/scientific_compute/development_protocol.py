"""Explicit development-fold admission and two-candidate metric-rank selection.

These checks qualify declared inputs and a local decision rule. They do not seal a
holdout, infer biological contexts, create reference anchors or award contest scores.
"""
import json
import math
import re
import zipfile

import numpy as np

if __package__:
    from .engine import Refused, _strings, _json, MAX_DENSE_BYTES
    from .aggregate_profiles import _effects, _digest
else:
    from engine import Refused, _strings, _json, MAX_DENSE_BYTES
    from aggregate_profiles import _effects, _digest

# cell-eval2 0.20.0 CATALOG, scored members of resolve_metrics('vcc2026', version='v2').
METRICS = {
    'pds_cosine': 'higher',
    'expr_mse_unbiased_capped_norm': 'lower',
    'de_wilcoxon_lfc_nmae': 'lower',
    'de_wilcoxon_direction_fidelity_yield_raw': 'higher',
    'de_wilcoxon_direction_reach_raw': 'higher',
    'de_wilcoxon_sig_jaccard': 'higher',
}
METHODS = ('null', 'existing-transfer', 'low-rank-transfer', 'embedding', 'blend')


def _decode(data):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise Refused('Duplicate JSON keys cannot define a scientific comparison')
            value[key] = item
        return value
    def constant(value):
        raise Refused('Nonfinite JSON constants are not valid scientific metadata')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


def _read(path, limit=16 << 20):
    with path.open('rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise Refused('Development metadata exceeds its bounded allowance')
    value = _decode(data)
    if type(value) is not dict:
        raise Refused('Development metadata must be an object')
    return value


def _labels(value, label, *, empty=False):
    if type(value) is not list or (not value and not empty):
        raise Refused(f'Declare {label} as an explicit list')
    if not value:
        return []
    return _strings(value, label, unique=True).tolist()


def check_fold(inputs, params, output, check):
    config = _read(inputs['config'], 1 << 20)
    required = {'format', 'target_regime', 'training_contexts', 'evaluation_contexts',
                'targets', 'genes', 'scale', 'normalization', 'expected_unseen_targets'}
    if set(config) != required or config['format'] != 'response-development-fold-v1':
        raise Refused('Declare a complete response-development-fold-v1 protocol')
    if config['target_regime'] not in ('seen', 'mixed', 'unseen'):
        raise Refused('Declare seen, mixed or unseen targets')
    train_contexts = _labels(config['training_contexts'], 'training contexts')
    eval_contexts = _labels(config['evaluation_contexts'], 'evaluation contexts')
    panel = _labels(config['targets'], 'complete evaluation targets')
    genes = _labels(config['genes'], 'ordered response genes')
    expected = _labels(config['expected_unseen_targets'], 'expected unseen targets', empty=True)
    if set(train_contexts) & set(eval_contexts):
        raise Refused('Training and evaluation biological contexts overlap')
    declared = 0
    for port in ('training', 'reference'):
        with zipfile.ZipFile(inputs[port]) as archive:
            declared += sum(item.file_size for item in archive.infolist())
    if declared * 4 > MAX_DENSE_BYTES:
        raise Refused('The combined fold artifacts exceed the compact working allowance')
    check()
    training, train_meta = _effects(inputs['training'])
    reference, ref_meta = _effects(inputs['reference'])
    for side, data, meta, contexts in [('training', training, train_meta, train_contexts),
                                     ('reference', reference, ref_meta, eval_contexts)]:
        if set(data['contexts'].tolist()) != set(contexts):
            raise Refused(f'{side} biological contexts differ from the declared fold')
        if data['genes'].tolist() != genes:
            raise Refused(f'{side} ordered response genes differ from the declared fold')
        if meta['scale'] != config['scale'] or meta['normalization'] != config['normalization']:
            raise Refused(f'{side} normalization or signed-effect scale differs from the declared fold')
    for context in eval_contexts:
        if set(reference['targets'][reference['contexts'] == context].tolist()) != set(panel):
            raise Refused('Evaluate the unchanged complete panel in every declared context')
    observed = set(training['targets'].tolist())
    unseen = sorted(set(panel) - observed)
    if config['target_regime'] == 'seen' and unseen:
        raise Refused('A seen-target comparison contains evaluated targets absent from training; zero priors would be uninformative')
    if config['target_regime'] == 'unseen' and set(unseen) != set(panel):
        raise Refused('An unseen-target comparison contains targets present in training')
    if config['target_regime'] == 'mixed' and (not unseen or len(unseen) == len(panel)):
        raise Refused('A mixed-target comparison requires both seen and unseen targets')
    if set(unseen) != set(expected):
        raise Refused('Expected zero-prior target coverage differs from the actual training fold')
    check()
    result = {'format': 'response-development-admission-v1', 'admitted': True,
              'protocol': config, 'trainingContexts': train_contexts, 'evaluationContexts': eval_contexts,
              'targets': len(panel), 'genes': len(genes), 'seenTargets': len(panel) - len(unseen),
              'unseenTargets': unseen, 'expectedZeroPriorTargets': unseen,
              'observedZeroEffectTrainingRows': int(np.sum(np.all(training['effects'] == 0, axis=1))),
              'declaredUncompressedInputBytes': declared,
              'inputs': {key: _digest(path, check) for key, path in inputs.items()},
              'qualification': 'declared development fold only; resource fit and independent review remain required'}
    return _json(output / 'development-admission.json', result), result


DERIVED = {'expr_mse_unbiased_capped_norm': ('expr_mse_unbiased_capped', 'expr_distance_unbiased')}


def _coverage(report):
    result = {}
    for context in report['contexts']:
        panel = context['targets']; allowed = set(panel)
        dependencies = {name for metric in METRICS for name in DERIVED.get(metric, (metric,))}
        values = {name: {} for name in dependencies}
        rows = context.get('metrics')
        if type(rows) is not list:
            raise Refused('Comparison requires retained raw upstream rows for coverage')
        for row in rows:
            name = row.get('metric')
            if name not in dependencies:
                continue
            target = row.get('perturbation'); value = row.get('value')
            if target not in allowed or target in values[name]:
                raise Refused('Metric support contains extra or duplicate target rows')
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
                raise Refused('Metric support must contain finite values or explicit nulls')
            values[name][target] = value
        support = {}
        for metric in METRICS:
            parts = {}
            for name in DERIVED.get(metric, (metric,)):
                observed = values[name]
                used = [target for target in panel if target in observed and observed[target] is not None]
                nonfinite = [target for target in panel if target in observed and observed[target] is None]
                missing = [target for target in panel if target not in observed]
                if not used:
                    raise Refused('A ranked metric has no finite raw support')
                parts[name] = {'n_rows': len(observed), 'n_used': len(used), 'n_nonfinite': len(nonfinite),
                               'n_missing': len(missing), 'usedTargets': used,
                               'nonfiniteTargets': nonfinite, 'missingTargets': missing}
            support[metric] = {'aggregation': 'ratio_of_sums' if metric in DERIVED else 'upstream aggregate',
                               'components': parts}
        result[context['context']] = support
    return result



def _report(value):
    if (value.get('format') != 'cell-evaluation-v1' or value.get('evaluator') != 'cell-eval2'
            or value.get('version') != '0.20.0'):
        raise Refused('Selection requires pinned cell-eval2 0.20.0 reports, not aggregate-effect diagnostics')
    hashes = value.get('inputs', {})
    if (type(hashes) is not dict or set(hashes) != {'prediction', 'reference', 'config'}
            or any(type(v) is not str or re.fullmatch(r'[a-f0-9]{64}', v) is None for v in hashes.values())):
        raise Refused('Evaluation reports need retained source hashes; re-evaluate legacy unhashed reports')
    scored = value.get('primaryScoredMetrics', [])
    if type(scored) is not list or len(scored) != 6 or set(scored) != set(METRICS):
        raise Refused('Selection requires every one of the six upstream scored metrics')
    protocol = value.get('protocol', {})
    config = value.get('config', {})
    if protocol.get('preset') != 'vcc2026' or config.get('metrics') != 'vcc2026' or config.get('version') != 'v2':
        raise Refused('Selection requires the pinned vcc2026 profile')
    if value.get('backend') != {'device': 'cpu', 'differentialExpression': 'scanpy', 'threads': 1}:
        raise Refused('Selection requires the qualified CPU/scanpy backend')
    panels = protocol.get('panels')
    if type(panels) is not dict or not 1 <= len(panels) <= 64:
        raise Refused('Selection needs the declared complete context panels')
    for targets in panels.values():
        _labels(targets, 'complete context targets')
    rows = value.get('contexts')
    if type(rows) is not list or len(rows) != len(panels):
        raise Refused('Selection reports are missing qualified contexts')
    contexts = {}
    for row in rows:
        context = row.get('context')
        if context not in panels or context in contexts or row.get('targets') != panels[context]:
            raise Refused('Selection must preserve every declared complete panel')
        metrics = {}
        for metric in row.get('aggregate', []):
            name = metric.get('metric')
            if name not in METRICS:
                continue
            number = metric.get('mean')
            if name in metrics or type(number) not in (int, float) or not math.isfinite(number):
                raise Refused('A required metric is duplicated, missing or nonfinite; no complete comparison')
            metrics[name] = number
        if set(metrics) != set(METRICS):
            raise Refused('Missing metrics prevent a complete six-metric comparison')
        contexts[context] = metrics
    return contexts


def select(inputs, params, output, check):
    config = _read(inputs['config'], 1 << 20)
    if set(config) != {'format', 'rule', 'left', 'right'} or config['format'] != 'development-selection-v1' or config['rule'] != 'equal-metric-ranks':
        raise Refused('Predeclare the equal-metric-ranks development selection rule')
    for side in ('left', 'right'):
        candidate = config[side]
        if (type(candidate) is not dict or set(candidate) != {'name', 'method'}
                or type(candidate['name']) is not str or not 0 < len(candidate['name']) <= 256
                or candidate['method'] not in METHODS):
            raise Refused('Declare each candidate name and simplicity class')
    if config['left']['name'] == config['right']['name']:
        raise Refused('Compared candidates require distinct names')
    left = _read(inputs['left']); right = _read(inputs['right']); check()
    a = _report(left); b = _report(right)
    if any(left['inputs'][port] != right['inputs'][port] for port in ('reference', 'config')):
        raise Refused('Candidates must use identical reference and protocol file hashes')
    for key in ('config', 'protocol', 'backend', 'resolvedMetrics'):
        if left.get(key) != right.get(key):
            raise Refused('Candidates must use identical pinned evaluator configuration and complete panels')
    support = _coverage(left)
    if _coverage(right) != support:
        raise Refused('Candidate metric support differs; investigate before selection')
    contexts = []
    for context in sorted(a):
        check(); metric_rows = []
        for metric, direction in METRICS.items():
            av, bv = a[context][metric], b[context][metric]
            ranks = (1.5, 1.5) if av == bv else ((1., 2.) if (av > bv) == (direction == 'higher') else (2., 1.))
            metric_rows.append({'metric': metric, 'direction': direction, 'left': av, 'right': bv,
                                'leftRank': ranks[0], 'rightRank': ranks[1], 'support': support[context][metric]})
        contexts.append({'context': context, 'metrics': metric_rows,
                         'leftRank': sum(row['leftRank'] for row in metric_rows) / 6,
                         'rightRank': sum(row['rightRank'] for row in metric_rows) / 6})
    # Sum half-integer ranks before division: equal mathematical ranks must stay exact ties.
    scores = {side: sum(metric[side + 'Rank'] for row in contexts for metric in row['metrics'])
              / (6 * len(contexts)) for side in ('left', 'right')}
    leaders = [side for side in scores if scores[side] == min(scores.values())]
    rank_tie = len(leaders) > 1
    if rank_tie:
        simplest = min(METHODS.index(config[side]['method']) for side in leaders)
        leaders = [side for side in leaders if METHODS.index(config[side]['method']) == simplest]
    result = {'format': 'development-selection-result-v1', 'rule': config,
              'measure': 'development selection measure', 'officialOverallScore': False,
              'weighting': 'equal metrics, midranks for ties, then equal qualified contexts',
              'candidates': [{**config[side], 'averageRank': scores[side]} for side in ('left', 'right')],
              'leaders': [config[side]['name'] for side in leaders], 'rankTie': rank_tie,
              'contexts': contexts, 'inputs': {key: _digest(path, check) for key, path in inputs.items()},
              'qualification': 'two-candidate development comparison; no reference scaling or official acceptance'}
    summary = {key: result[key] for key in ('format', 'measure', 'officialOverallScore', 'candidates', 'leaders', 'rankTie', 'inputs')}
    return _json(output / 'development-selection.json', result), summary
