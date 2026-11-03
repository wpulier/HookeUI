"""Synthetic numerical/refusal oracles; no biological prediction-quality claim."""
import copy
import json

import numpy as np
import pytest

from scientific_compute import aggregate_profiles, development_protocol as dev, engine
from scientific_compute.catalog import descriptor, parameters


def write(tmp_path, name, value):
    path = tmp_path / name
    path.write_text(json.dumps(value))
    return path


def prepared(tmp_path, name, *, context, targets=('x', 'y'), genes=('a', 'b'), total=10000, zeros=False):
    folder = tmp_path / name; folder.mkdir()
    normalization = {'genes': list(genes), 'total': total, 'pseudocount': .1, 'method': 'aggregate-count-proportions'}
    meta = {'format': 'response-effects-v1', 'shape': [len(targets), len(genes)], 'contexts': [context],
            'scale': 'log2-normalized-count-ratio', 'normalization': normalization, 'provenance': {'fixture': True}}
    values = np.zeros((len(targets), len(genes))) if zeros else np.array([[1., -2.]] * len(targets))
    return aggregate_profiles._save(folder, values, list(genes), [context] * len(targets), list(targets), [10] * len(targets), meta)[0], normalization


def fold(tmp_path, *, targets=('x', 'y'), regime='seen', expected=(), zeros=False):
    train, normalization = prepared(tmp_path, 'train', context='training', targets=targets, zeros=zeros)
    reference, _ = prepared(tmp_path, 'reference', context='held-out')
    config = {'format': 'response-development-fold-v1', 'target_regime': regime,
              'training_contexts': ['training'], 'evaluation_contexts': ['held-out'],
              'targets': ['x', 'y'], 'genes': ['a', 'b'], 'scale': 'log2-normalized-count-ratio',
              'normalization': normalization, 'expected_unseen_targets': list(expected)}
    return {'training': train, 'reference': reference, 'config': write(tmp_path, 'fold.json', config)}


def invoke(tmp_path, operation, inputs):
    output = tmp_path / 'output'; output.mkdir()
    return engine.OPERATIONS[operation](inputs, parameters(operation, {}), output, lambda: None)


def test_seen_fold_distinguishes_real_zero_effects_from_missing_target_priors(tmp_path):
    _, result = invoke(tmp_path, 'response-development-check', fold(tmp_path, zeros=True))
    assert result['admitted'] and result['unseenTargets'] == []
    assert result['observedZeroEffectTrainingRows'] == 2
    assert result['seenTargets'] == 2 and result['genes'] == 2


def test_disjoint_training_panel_refuses_claimed_seen_target_comparison(tmp_path):
    with pytest.raises(engine.Refused, match='absent from training'):
        invoke(tmp_path, 'response-development-check', fold(tmp_path, targets=('other', 'different')))


@pytest.mark.parametrize('regime,targets,expected', [('mixed', ('x',), ('y',)), ('unseen', ('other',), ('x', 'y'))])
def test_declared_unseen_targets_are_kept_and_reported(tmp_path, regime, targets, expected):
    _, result = invoke(tmp_path, 'response-development-check', fold(tmp_path, targets=targets, regime=regime, expected=expected))
    assert result['unseenTargets'] == list(expected)


def test_expected_zero_prior_coverage_mismatch_refuses(tmp_path):
    with pytest.raises(engine.Refused, match='Expected zero-prior'):
        invoke(tmp_path, 'response-development-check', fold(tmp_path, targets=('x',), regime='mixed'))


@pytest.mark.parametrize('change,match', [('training_contexts', 'contexts'), ('genes', 'ordered response genes'), ('normalization', 'normalization'), ('targets', 'complete panel')])
def test_declared_fold_mismatches_refuse(tmp_path, change, match):
    inputs = fold(tmp_path); config = json.loads(inputs['config'].read_text())
    config[change] = {'training_contexts': ['different'], 'genes': ['b', 'a'], 'normalization': {}, 'targets': ['x']}[change]
    inputs['config'].write_text(json.dumps(config))
    with pytest.raises(engine.Refused, match=match):
        invoke(tmp_path, 'response-development-check', inputs)


def report(contexts=('one',), values=None):
    values = values or {name: 1. for name in dev.METRICS}
    return {'format': 'cell-evaluation-v1', 'evaluator': 'cell-eval2', 'version': '0.20.0',
            'inputs': {'prediction': 'a' * 64, 'reference': 'b' * 64, 'config': 'c' * 64},
            'primaryScoredMetrics': list(dev.METRICS), 'resolvedMetrics': list(dev.METRICS),
            'backend': {'device': 'cpu', 'differentialExpression': 'scanpy', 'threads': 1},
            'protocol': {'preset': 'vcc2026', 'panels': {context: ['x', 'y'] for context in contexts}},
            'config': {'version': 'v2', 'metrics': 'vcc2026'},
            'contexts': [{'context': context, 'targets': ['x', 'y'],
                'metrics': [{'metric': source, 'perturbation': target, 'value': 1.}
                    for metric in dev.METRICS for source in dev.DERIVED.get(metric, (metric,)) for target in ['x', 'y']],
                'aggregate': [
                {'metric': name, 'mean': value, 'agg': 'mean'} for name, value in values.items()]} for context in contexts]}


def selection(tmp_path, left, right, *, left_method='low-rank-transfer', right_method='existing-transfer'):
    return {'left': write(tmp_path, 'left.json', left), 'right': write(tmp_path, 'right.json', right),
            'config': write(tmp_path, 'selection.json', {'format': 'development-selection-v1', 'rule': 'equal-metric-ranks',
                'left': {'name': 'challenger', 'method': left_method}, 'right': {'name': 'baseline', 'method': right_method}})}


def test_six_metric_directions_and_equal_context_weight_are_independent_oracle(tmp_path):
    # Challenger wins 4/6 metrics in context one and 1/6 in context two.
    left = report(('one', 'two')); right = copy.deepcopy(left)
    for i, row in enumerate(left['contexts'][0]['aggregate']):
        direction = dev.METRICS[row['metric']]
        row['mean'] = 2 if (i < 4) == (direction == 'higher') else 0
    for i, row in enumerate(left['contexts'][1]['aggregate']):
        direction = dev.METRICS[row['metric']]
        row['mean'] = 2 if (i < 1) == (direction == 'higher') else 0
    path, summary = invoke(tmp_path, 'cell-evaluation-select', selection(tmp_path, left, right))
    result = json.loads(path.read_text())
    assert result['candidates'][0]['averageRank'] == pytest.approx(19/12)
    assert result['candidates'][1]['averageRank'] == pytest.approx(17/12)
    assert summary['leaders'] == ['baseline'] and summary['officialOverallScore'] is False


def test_metric_ties_use_midranks_and_simpler_method_wins(tmp_path):
    path, summary = invoke(tmp_path, 'cell-evaluation-select', selection(tmp_path, report(), report()))
    result = json.loads(path.read_text())
    assert result['candidates'][0]['averageRank'] == 1.5 and result['rankTie']
    assert summary['leaders'] == ['baseline']
    assert all(row['leftRank'] == row['rightRank'] == 1.5 for row in result['contexts'][0]['metrics'])


def test_equal_simplicity_tie_preserves_both_leaders(tmp_path):
    _, result = invoke(tmp_path, 'cell-evaluation-select', selection(tmp_path, report(), report(), left_method='null', right_method='null'))
    assert result['leaders'] == ['challenger', 'baseline']


@pytest.mark.parametrize('bad', ['missing', 'null', 'nan', 'duplicate', 'panel', 'config', 'context', 'aggregate-only', 'version', 'reference-hash', 'missing-hash'])
def test_incomplete_or_incompatible_reports_never_get_a_rank(tmp_path, bad):
    left = report(); right = report()
    rows = right['contexts'][0]['aggregate']
    if bad == 'missing': rows.pop()
    if bad == 'null': rows[0]['mean'] = None
    if bad == 'nan': rows[0]['mean'] = float('nan')
    if bad == 'duplicate': rows.append(copy.deepcopy(rows[0]))
    if bad == 'panel': right['contexts'][0]['targets'] = ['x']
    if bad == 'config': right['config']['different'] = True
    if bad == 'context': right['contexts'] = []
    if bad == 'aggregate-only': right['format'] = 'response-effect-evaluation-v1'
    if bad == 'version': right['version'] = '0.19.0'
    if bad == 'reference-hash': right['inputs']['reference'] = 'd' * 64
    if bad == 'missing-hash': right.pop('inputs')
    with pytest.raises(engine.Refused):
        invoke(tmp_path, 'cell-evaluation-select', selection(tmp_path, left, right))


def test_upstream_pinned_catalog_directions_are_exact_when_installed():
    pytest.importorskip('cell_eval2')
    from importlib.metadata import version
    from cell_eval2.catalog import resolve_metrics, CATALOG
    if version('cell-eval2') != '0.20.0': pytest.skip('Pinned evaluator runtime not installed')
    names, missing = resolve_metrics('vcc2026', version='v2')
    assert not missing
    assert dev.METRICS == {name: CATALOG[name].scoring.direction for name in names if CATALOG[name].scoring.scored}


def test_discovery_pins_both_new_capabilities():
    for capability in ('response-development-check', 'cell-evaluation-select'):
        d = descriptor(capability)
        assert d['generation'].startswith('matrix-v1.') and len(d['inputs']) == 3


def test_context_overlap_is_refused_before_array_read(tmp_path):
    inputs = fold(tmp_path)
    config = json.loads(inputs['config'].read_text())
    config['evaluation_contexts'] = ['training']
    inputs['config'].write_text(json.dumps(config))
    with pytest.raises(engine.Refused, match='overlap'):
        invoke(tmp_path, 'response-development-check', inputs)


def test_aggregate_profile_does_not_substitute_a_missing_primary_metric(tmp_path):
    left = report(); right = report()
    right['contexts'][0]['aggregate'].pop()
    right['contexts'][0]['aggregate'].append({'metric': 'diagnostic', 'mean': 99.})
    with pytest.raises(engine.Refused, match='Missing metrics'):
        invoke(tmp_path, 'cell-evaluation-select', selection(tmp_path, left, right))


@pytest.mark.parametrize('kind', ['missing', 'null', 'duplicate', 'absent-rows'])
def test_pair_selection_checks_same_metric_support_as_joint_ranking(tmp_path, kind):
    left = report(); right = report()
    rows = right['contexts'][0]['metrics']
    if kind == 'missing': rows.pop()
    elif kind == 'null': rows[-1]['value'] = None
    elif kind == 'duplicate': rows.append(copy.deepcopy(rows[-1]))
    else: right['contexts'][0].pop('metrics')
    with pytest.raises(engine.Refused):
        invoke(tmp_path, 'cell-evaluation-select', selection(tmp_path, left, right))


def test_pair_preserves_shared_ineligible_targets(tmp_path):
    left = report()
    left['contexts'][0]['metrics'][-1]['value'] = None
    path, _ = invoke(tmp_path, 'cell-evaluation-select', selection(tmp_path, left, copy.deepcopy(left)))
    result = json.loads(path.read_text())
    support = result['contexts'][0]['metrics'][-1]['support']['components']['de_wilcoxon_sig_jaccard']
    assert support['nonfiniteTargets'] == ['y']
    assert support['usedTargets'] == ['x']
