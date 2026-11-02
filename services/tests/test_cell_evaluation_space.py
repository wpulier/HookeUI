"""Synthetic evaluation views; no experimental reference or historical parity claim."""
import hashlib
import json

import numpy as np
import pytest

from scientific_compute import cell_evaluation as evaluation


def protocol(tmp_path, **changes):
    value = {'format': 'cell-evaluation-config-v2', 'preset': 'v1', 'metrics': 'vcc',
             'normalization': 'per-matrix-median-log1p-v1',
             'context_column': 'context', 'target_column': 'target',
             'control_label': 'control', 'panels': {'one': ['a', 'b']}}
    value.update(changes)
    path = tmp_path / 'protocol.json'; path.write_text(json.dumps(value))
    return path


@pytest.mark.parametrize('change', [
    {'format': 'other'}, {'normalization': 'guess'}, {'metrics': []},
    {'metrics': ''}, {'extra': True}, {'normalization': None},
])
def test_explicit_protocol_refuses_unrecognized_or_incomplete_meaning(tmp_path, change):
    with pytest.raises(evaluation.Refused):
        evaluation.protocol(protocol(tmp_path, **change))


def test_explicit_config_pins_backend_and_preserves_preset_semantics(tmp_path):
    pytest.importorskip('cell_eval2')
    cfg = evaluation._evaluation_config(evaluation.protocol(protocol(tmp_path)))
    assert cfg.metrics == 'vcc' and cfg.version == 'v1'
    assert cfg.control_source == 'pred' and cfg.input_type == 'lognorm'
    assert cfg.de.backend == 'scanpy' and cfg.de.fdr_scope == 'global'
    assert cfg.device == 'cpu' and cfg.num_threads == cfg.gather_threads == 1
    with pytest.raises(evaluation.Refused, match='explicit versioned'):
        evaluation._evaluation_config({'preset': 'v1'})
    with pytest.raises(evaluation.Refused, match='v1 lognorm'):
        evaluation._evaluation_config(evaluation.protocol(protocol(tmp_path, preset='v2')))


def source(path, *, reference, multiplier=1):
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    from scipy import sparse
    labels = ['a', 'a', 'b', 'b'] + (['control', 'control'] if reference else [])
    values = np.array([[2, 8, 1], [5, 5, 3], [4, 20, 2], [9, 19, 2]] +
                      ([[8, 8, 7], [2, 3, 1]] if reference else []), dtype=np.float32) * multiplier
    obj = ad.AnnData(sparse.csr_matrix(values),
        obs=pd.DataFrame({'target': labels, 'context': ['one'] * len(labels)},
                         index=[f'cell{i}' for i in range(len(labels))]),
        var=pd.DataFrame(index=['x', 'y', 'z']))
    obj.write_h5ad(path)
    return path, values


def test_views_use_full_side_medians_and_leave_input_artifacts_unchanged(tmp_path, monkeypatch):
    upstream = pytest.importorskip('cell_eval2')
    from cell_eval2.norm import guess_is_lognorm
    pred, p_values = source(tmp_path / 'prediction.h5ad', reference=False, multiplier=3)
    real, r_values = source(tmp_path / 'reference.h5ad', reference=True)
    original = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (pred, real)}
    # Capture the real admission/normalization path. Scoring is a separate test.
    class Measured:
        def to_dicts(self): return []
    captured = []
    def compute(p, r, config):
        assert guess_is_lognorm(p) and guess_is_lognorm(r)
        expected_p = np.concatenate([p_values, r_values[-2:]])
        for data, raw in [(p, expected_p), (r, r_values)]:
            totals = raw.sum(axis=1)
            expected = np.log1p(raw / totals[:, None] * np.median(totals))
            np.testing.assert_allclose(data.X.toarray(), expected, rtol=1e-6)
        assert config.de.backend == 'scanpy' and config.metrics == 'vcc'
        captured.append(True)
        return Measured()
    monkeypatch.setattr(upstream, 'compute_metrics', compute)
    monkeypatch.setattr(upstream, 'aggregate_metrics', lambda *a, **k: Measured())
    path, result = evaluation.evaluate({'prediction': pred, 'reference': real,
        'config': protocol(tmp_path)}, {}, tmp_path, lambda: None)
    assert captured == [True]
    views = result['contexts'][0]['evaluationViews']
    assert views['prediction']['targetSum'] == np.median(np.concatenate([p_values, r_values[-2:]]).sum(axis=1))
    assert views['reference']['targetSum'] == np.median(r_values.sum(axis=1))
    assert views['prediction']['targetSum'] != views['reference']['targetSum']
    assert result['officialAcceptance'] is False
    assert json.loads(path.read_text())['contexts'][0]['evaluationViews'] == views
    assert original == {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in original}


def test_normalization_ambiguity_and_stop_refuse(tmp_path, monkeypatch):
    ad = pytest.importorskip('anndata'); norm = pytest.importorskip('cell_eval2.norm')
    data = ad.AnnData(np.array([[1., 4.], [3., 2.]], dtype=np.float32))
    monkeypatch.setattr(norm, 'guess_is_lognorm', lambda _: False)
    with pytest.raises(evaluation.Refused, match='ambiguous'):
        evaluation._normalize_evaluation_view(data, lambda: None)
    def stop(): raise InterruptedError('stop')
    with pytest.raises(InterruptedError):
        evaluation._normalize_evaluation_view(data, stop)


def test_zero_depth_is_counted_and_all_zero_refuses():
    ad = pytest.importorskip('anndata'); pytest.importorskip('cell_eval2')
    data = ad.AnnData(np.array([[0., 0.], [1., 3.]], dtype=np.float32))
    receipt = evaluation._normalize_evaluation_view(data, lambda: None)
    assert receipt['targetSum'] == 4 and receipt['zeroDepthObservations'] == 1
    with pytest.raises(evaluation.Refused, match='positive library'):
        evaluation._normalize_evaluation_view(ad.AnnData(np.zeros((2, 2))), lambda: None)
    with pytest.raises(evaluation.Refused, match='maximum cell total'):
        evaluation._normalize_evaluation_view(ad.AnnData(np.array([[2e9, 1.]])), lambda: None)


def test_new_conversion_preserves_memory_admission_before_allocations(tmp_path, monkeypatch):
    pytest.importorskip('cell_eval2')
    pred, _ = source(tmp_path / 'prediction.h5ad', reference=False)
    real, _ = source(tmp_path / 'reference.h5ad', reference=True)
    monkeypatch.setattr(evaluation, 'MATERIALIZATION_BUDGET', 1)
    monkeypatch.setattr(evaluation, '_context', lambda *a: pytest.fail('Memory admission must precede allocations'))
    with pytest.raises(evaluation.Refused, match='materialization allowance'):
        evaluation.evaluate({'prediction': pred, 'reference': real,
            'config': protocol(tmp_path)}, {}, tmp_path, lambda: None)


def test_profile_with_no_metrics_for_the_selected_version_refuses_before_input_reads(tmp_path, monkeypatch):
    pytest.importorskip('cell_eval2')
    monkeypatch.setattr(evaluation, '_counts', lambda *a: pytest.fail('Metric admission must precede input reads'))
    with pytest.raises(evaluation.Refused, match='no compatible metrics'):
        evaluation.evaluate({'prediction': tmp_path / 'unused-prediction',
            'reference': tmp_path / 'unused-reference', 'config': protocol(tmp_path, metrics='cell')},
            {}, tmp_path, lambda: None)


def test_control_pool_detection_cannot_silently_change_normalization(tmp_path, monkeypatch):
    upstream = pytest.importorskip('cell_eval2')
    norm = pytest.importorskip('cell_eval2.norm')
    pred, _ = source(tmp_path / 'prediction.h5ad', reference=False)
    real, _ = source(tmp_path / 'reference.h5ad', reference=True)
    monkeypatch.setattr(norm, 'guess_is_lognorm', lambda data: data.n_obs > 2)
    monkeypatch.setattr(upstream, 'compute_metrics', lambda *a, **k: pytest.fail('Ambiguous controls must not be scored'))
    with pytest.raises(evaluation.Refused, match='control pool is ambiguous'):
        evaluation.evaluate({'prediction': pred, 'reference': real,
            'config': protocol(tmp_path)}, {}, tmp_path, lambda: None)


def test_real_upstream_scores_identity_and_detects_swapped_target_responses(tmp_path):
    """Synthetic sensitivity check through the whole capability, not historical parity."""
    pytest.importorskip('cell_eval2')
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    from scipy import sparse
    rng = np.random.default_rng(719)
    labels = ['control', 'g0', 'g1', 'g2']
    blocks = []
    for i, _ in enumerate(labels):
        proportions = np.full(24, 12.)
        if i:
            proportions[8 * (i - 1):8 * (i - 1) + 4] = 20
            proportions[8 * (i - 1) + 4:8 * i] = 4
        blocks.append(rng.multinomial(288, proportions / proportions.sum(), size=80).astype(np.float32))
    reference = ad.AnnData(sparse.csr_matrix(np.concatenate(blocks)),
        obs=pd.DataFrame({'target': np.repeat(labels, 80), 'context': ['one'] * 320},
                         index=[f'cell{i}' for i in range(320)]),
        var=pd.DataFrame(index=[f'g{i}' for i in range(24)]))
    real = tmp_path / 'reference.h5ad'; reference.write_h5ad(real)
    config = protocol(tmp_path, panels={'one': ['g0', 'g1', 'g2']})
    scores = {}
    for name, order in [('identity', [1, 2, 3]), ('swapped', [2, 3, 1])]:
        prediction = reference[80:].copy()
        prediction.X = sparse.csr_matrix(np.concatenate([blocks[i] for i in order]))
        path = tmp_path / f'{name}.h5ad'; prediction.write_h5ad(path)
        _, result = evaluation.evaluate({'prediction': path, 'reference': real, 'config': config},
                                        {}, tmp_path, lambda: None)
        scores[name] = {row['metric']: row['mean'] for row in result['contexts'][0]['aggregate']}
    assert scores['identity']['mae'] == 0
    for metric in ['discrimination_score_l1', 'overlap_at_N']:
        assert scores['identity'][metric] == 1
        assert scores['swapped'][metric] < scores['identity'][metric]
    assert scores['swapped']['mae'] > 0
