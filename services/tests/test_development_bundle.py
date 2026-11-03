"""Synthetic joint ranking and bundle-integrity checks; no contest quality evidence."""
import copy
import hashlib
import json
import zipfile

import pytest

from scientific_compute import development_bundle as bundle, development_protocol as dev, engine
from scientific_compute.catalog import parameters
from test_development_protocol import report


def with_rows(values=None):
    value = report(values=values)
    context = value['contexts'][0]
    names = [source for metric in dev.METRICS for source in bundle.DERIVED.get(metric, (metric,))]
    context['metrics'] = [{'metric': name, 'perturbation': target, 'value': 1.}
                          for name in names for target in ['x', 'y']]
    return value


def files(tmp_path, reports, methods=None):
    methods = methods or ['existing-transfer', 'low-rank-transfer', 'null'][:len(reports)]
    candidates = []
    path = tmp_path / 'reports.zip'
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for i, (value, method) in enumerate(zip(reports, methods)):
            raw = json.dumps(value).encode(); filename = f'candidate-{i}.json'
            archive.writestr(filename, raw)
            candidates.append({'name': f'candidate-{i}', 'method': method, 'file': filename,
                               'sha256': hashlib.sha256(raw).hexdigest()})
    config = tmp_path / 'config.json'
    config.write_text(json.dumps({'format': 'development-ranking-v1', 'rule': 'equal-metric-ranks', 'candidates': candidates}))
    return {'reports': path, 'config': config}


def run(tmp_path, inputs):
    output = tmp_path / 'output'; output.mkdir(exist_ok=True)
    path, summary = engine.OPERATIONS['cell-evaluation-rank'](inputs, parameters('cell-evaluation-rank', {}), output, lambda: None)
    return json.loads(path.read_text()), summary


def test_joint_three_candidate_rank_matches_independent_oracle(tmp_path):
    strengths = [[3, 3, 3, 1, 1, 1], [2, 2, 2, 3, 3, 3], [1, 1, 1, 2, 2, 2]]
    reports = [with_rows({metric: number if direction == 'higher' else -number
                         for (metric, direction), number in zip(dev.METRICS.items(), values)}) for values in strengths]
    result, summary = run(tmp_path, files(tmp_path, reports))
    assert [candidate['averageRank'] for candidate in result['candidates']] == [2., 1.5, 2.5]
    assert summary['leaders'] == ['candidate-1']
    assert summary['measure'] == 'development selection measure' and summary['officialOverallScore'] is False
    assert len(summary['candidateSetSha256']) == 64


def test_joint_midranks_and_simplicity_ties(tmp_path):
    result, summary = run(tmp_path, files(tmp_path, [with_rows()] * 3))
    assert [candidate['averageRank'] for candidate in result['candidates']] == [2., 2., 2.]
    assert summary['rankTie'] and summary['leaders'] == ['candidate-2']


def test_upstream_reference_ineligibility_preserved_without_balancing_panel(tmp_path):
    a = with_rows()
    metric = 'de_wilcoxon_sig_jaccard'
    row = next(row for row in a['contexts'][0]['metrics'] if row['metric'] == metric and row['perturbation'] == 'y')
    row['value'] = None
    result, _ = run(tmp_path, files(tmp_path, [a, copy.deepcopy(a)]))
    support = next(row for row in result['contexts'][0]['metrics'] if row['metric'] == metric)['support']['components'][metric]
    assert support == {'n_rows': 2, 'n_used': 1, 'n_nonfinite': 1, 'n_missing': 0,
                       'usedTargets': ['x'], 'nonfiniteTargets': ['y'], 'missingTargets': []}
    derived = next(row for row in result['contexts'][0]['metrics'] if row['metric'] in bundle.DERIVED)['support']
    assert derived['aggregation'] == 'ratio_of_sums' and set(derived['components']) == set(bundle.DERIVED['expr_mse_unbiased_capped_norm'])


def test_unexplained_candidate_support_mismatch_refuses(tmp_path):
    a = with_rows(); b = with_rows(); b['contexts'][0]['metrics'][0]['value'] = None
    with pytest.raises(engine.Refused, match='support differs'):
        run(tmp_path, files(tmp_path, [a, b]))


def test_reference_ineligible_omitted_rows_are_explicit_not_silently_dropped(tmp_path):
    a = with_rows(); a['contexts'][0]['metrics'].pop()
    result, _ = run(tmp_path, files(tmp_path, [a, a]))
    support = result['contexts'][0]['metrics'][-1]['support']['components']['de_wilcoxon_sig_jaccard']
    assert support['n_missing'] == 1 and support['missingTargets'] == ['y']


@pytest.mark.parametrize('bad', ['extra', 'duplicate', 'wrong-hash', 'missing-rows', 'duplicate-row', 'all-null', 'bad-key', 'oversized'])
def test_bundle_and_coverage_refusals(tmp_path, bad, monkeypatch):
    a = with_rows(); b = with_rows()
    if bad == 'missing-rows': b['contexts'][0].pop('metrics')
    if bad == 'duplicate-row': b['contexts'][0]['metrics'].append(copy.deepcopy(b['contexts'][0]['metrics'][0]))
    if bad == 'all-null':
        for row in b['contexts'][0]['metrics']: row['value'] = None
    inputs = files(tmp_path, [a, b])
    if bad in ('extra', 'duplicate'):
        with zipfile.ZipFile(inputs['reports'], 'a') as archive:
            with pytest.warns(UserWarning) if bad == 'duplicate' else __import__('contextlib').nullcontext():
                archive.writestr('candidate-0.json' if bad == 'duplicate' else 'extra.json', '{}')
    if bad == 'wrong-hash':
        config = json.loads(inputs['config'].read_text()); config['candidates'][0]['sha256'] = '0' * 64
        inputs['config'].write_text(json.dumps(config))
    if bad == 'bad-key': inputs['config'].write_text('{"format":"x","format":"development-ranking-v1"}')
    if bad == 'oversized': monkeypatch.setattr(bundle, 'MAX_REPORT_BYTES', 1)
    with pytest.raises(engine.Refused): run(tmp_path, inputs)


def test_candidate_set_digest_is_order_independent_and_membership_sensitive(tmp_path):
    inputs = files(tmp_path, [with_rows()] * 3)
    first, _ = run(tmp_path, inputs)
    config = json.loads(inputs['config'].read_text()); config['candidates'].reverse()
    inputs['config'].write_text(json.dumps(config)); reordered, _ = run(tmp_path, inputs)
    assert first['candidateSetSha256'] == reordered['candidateSetSha256']
    inputs = files(tmp_path, [with_rows()] * 2)
    reduced, _ = run(tmp_path, inputs)
    assert first['candidateSetSha256'] != reduced['candidateSetSha256']


def test_duplicate_report_json_keys_refuse_even_with_matching_hash(tmp_path):
    inputs = files(tmp_path, [with_rows()] * 2)
    raw = ('{"version":"wrong",' + json.dumps(with_rows())[1:]).encode()
    config = json.loads(inputs['config'].read_text()); config['candidates'][0]['sha256'] = hashlib.sha256(raw).hexdigest()
    inputs['config'].write_text(json.dumps(config))
    with zipfile.ZipFile(inputs['reports'], 'w') as archive:
        archive.writestr('candidate-0.json', raw)
        archive.writestr('candidate-1.json', json.dumps(with_rows()))
    with pytest.raises(engine.Refused, match='Duplicate JSON'):
        run(tmp_path, inputs)
