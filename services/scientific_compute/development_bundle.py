"""Joint development ranking from a bounded ZIP of exact upstream JSON reports.

The ZIP is an ordinary retained input file. Nothing is extracted or executed;
member hashes and the fixed candidate-set digest make selection scope explicit.
"""
import hashlib
import json
import math
import re
import stat
import zipfile

if __package__:
    from .engine import Refused, _json
    from .aggregate_profiles import _digest
    from .development_protocol import METRICS, METHODS, _read, _decode, _report
else:
    from engine import Refused, _json
    from aggregate_profiles import _digest
    from development_protocol import METRICS, METHODS, _read, _decode, _report

MAX_BUNDLE_BYTES = 96 << 20
MAX_REPORT_BYTES = 16 << 20
DERIVED = {'expr_mse_unbiased_capped_norm': ('expr_mse_unbiased_capped', 'expr_distance_unbiased')}


def _coverage(report):
    result = {}
    for context in report['contexts']:
        panel = context['targets']; allowed = set(panel)
        dependencies = {name for metric in METRICS for name in DERIVED.get(metric, (metric,))}
        values = {name: {} for name in dependencies}
        rows = context.get('metrics')
        if type(rows) is not list:
            raise Refused('Joint comparison requires retained raw upstream rows for coverage')
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


def rank_bundle(inputs, params, output, check):
    config = _read(inputs['config'], 1 << 20)
    if (set(config) != {'format', 'rule', 'candidates'} or config['format'] != 'development-ranking-v1'
            or config['rule'] != 'equal-metric-ranks'):
        raise Refused('Predeclare the joint equal-metric-ranks development rule')
    candidates = config['candidates']
    if type(candidates) is not list or not 2 <= len(candidates) <= 6:
        raise Refused('Joint selection requires two to six frozen candidates')
    names, files = set(), set()
    for candidate in candidates:
        if (type(candidate) is not dict or set(candidate) != {'name', 'method', 'file', 'sha256'}
                or type(candidate['name']) is not str or not 0 < len(candidate['name']) <= 256
                or candidate['method'] not in METHODS or type(candidate['file']) is not str
                or re.fullmatch(r'[A-Za-z0-9_-]+\.json', candidate['file']) is None
                or type(candidate['sha256']) is not str or re.fullmatch(r'[a-f0-9]{64}', candidate['sha256']) is None):
            raise Refused('Declare each candidate name, simplicity, flat JSON member and exact SHA256')
        if candidate['name'] in names or candidate['file'] in files:
            raise Refused('Candidate names and report members must be unique')
        names.add(candidate['name']); files.add(candidate['file'])
    if inputs['reports'].stat().st_size > MAX_BUNDLE_BYTES:
        raise Refused('The report bundle exceeds its compressed allowance')
    reports = []
    with zipfile.ZipFile(inputs['reports']) as archive:
        entries = archive.infolist()
        if len(entries) != len(files) or {entry.filename for entry in entries} != files:
            raise Refused('The report bundle has missing, extra or duplicate members')
        if sum(entry.file_size for entry in entries) > MAX_BUNDLE_BYTES:
            raise Refused('The report bundle exceeds its uncompressed allowance')
        for entry in entries:
            if entry.is_dir() or stat.S_ISLNK(entry.external_attr >> 16) or entry.file_size > MAX_REPORT_BYTES:
                raise Refused('Each report must be a bounded regular JSON member')
        for candidate in candidates:
            check()
            with archive.open(candidate['file']) as stream:
                raw = stream.read(MAX_REPORT_BYTES + 1)
            if len(raw) > MAX_REPORT_BYTES or hashlib.sha256(raw).hexdigest() != candidate['sha256']:
                raise Refused('A bundled report does not match its declared bytes')
            report = _decode(raw)
            if type(report) is not dict:
                raise Refused('A bundled report must be a JSON object')
            values = _report(report)
            coverage = _coverage(report)
            reports.append((report, values, coverage))
    first = reports[0][0]; support = reports[0][2]
    for report, values, coverage in reports[1:]:
        if any(first[key] != report[key] for key in ('config', 'protocol', 'backend', 'resolvedMetrics')):
            raise Refused('Joint candidates require identical pinned configurations and complete panels')
        if any(first['inputs'][key] != report['inputs'][key] for key in ('reference', 'config')):
            raise Refused('Joint candidates require identical reference and protocol file hashes')
        if coverage != support:
            raise Refused('Candidate metric support differs; investigate and retain the inconsistent support before selection')
    contexts = []; totals = [0.] * len(candidates)
    for context in sorted(reports[0][1]):
        metrics = []
        for metric, direction in METRICS.items():
            check(); values = [report[1][context][metric] for report in reports]
            ranks = []
            for i, value in enumerate(values):
                better = sum(other > value if direction == 'higher' else other < value for other in values)
                equal = sum(other == value for other in values)
                rank = better + (equal + 1) / 2
                ranks.append(rank); totals[i] += rank
            metrics.append({'metric': metric, 'direction': direction,
                            'candidates': [{'name': candidate['name'], 'value': value, 'rank': rank}
                                           for candidate, value, rank in zip(candidates, values, ranks)],
                            'support': support[context][metric]})
        contexts.append({'context': context, 'metrics': metrics})
    minimum = min(totals)
    rank_leaders = [i for i, score in enumerate(totals) if score == minimum]
    simplest = min(METHODS.index(candidates[i]['method']) for i in rank_leaders)
    leaders = [candidates[i]['name'] for i in rank_leaders if METHODS.index(candidates[i]['method']) == simplest]
    candidate_set = sorted(candidates, key=lambda candidate: candidate['name'])
    result = {'format': 'development-ranking-result-v1', 'rule': config,
              'candidateSetSha256': hashlib.sha256(json.dumps(candidate_set, sort_keys=True, separators=(',', ':')).encode()).hexdigest(),
              'measure': 'development selection measure', 'officialOverallScore': False,
              'weighting': 'joint metric midranks, equal six metrics, equal qualified contexts',
              'candidates': [{**candidate, 'averageRank': score / (6 * len(contexts))}
                             for candidate, score in zip(candidates, totals)],
              'leaders': leaders, 'rankTie': len(rank_leaders) > 1, 'contexts': contexts,
              'inputs': {key: _digest(path, check) for key, path in inputs.items()},
              'qualification': 'joint development comparison; complete panels retained; upstream metric eligibility preserved; no official acceptance'}
    summary = {key: result[key] for key in ('format', 'candidateSetSha256', 'measure', 'officialOverallScore', 'candidates', 'leaders', 'rankTie', 'inputs')}
    return _json(output / 'development-ranking.json', result), summary
