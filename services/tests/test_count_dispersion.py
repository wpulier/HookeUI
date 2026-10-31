"""Synthetic moment identities; not evidence of biological generative quality."""
import numpy as np
import pytest
from scipy import sparse

from scientific_compute import engine
from scientific_compute.catalog import parameters


class Counts:
    def __init__(self, values):
        self.X = sparse.csr_matrix(values, dtype=float)
        self.n_vars = self.X.shape[1]
        self.n_obs = self.X.shape[0]


@pytest.mark.parametrize('block_rows', [1, 3, 100])
def test_depth_scaled_poisson_subtraction_matches_explicit_moments(monkeypatch, block_rows):
    monkeypatch.setattr(engine, 'BLOCK_ROWS', block_rows)
    # One excluded context, variable depths, a zero gene and overdispersed genes.
    y = np.array([[0, 2, 0], [8, 2, 0], [1, 39, 0], [80, 20, 0]], dtype=float)
    data = Counts(np.vstack(([999, 1, 0], y)))
    labels = np.array(['excluded'] + ['a'] * len(y))
    depths, proportions, dispersion, mean = engine._control_moments(
        data, labels, 'a', lambda: None, 'depth-scaled-poisson-v1')
    d = y.sum(axis=1); scale = np.median(d) / d
    z = y * scale[:, None]
    expected_noise = np.mean(y * scale[:, None] ** 2, axis=0)
    expected = np.maximum(0, (np.var(z, axis=0) - expected_noise) / np.maximum(z.mean(axis=0) ** 2, 1e-12))
    np.testing.assert_array_equal(depths, d)
    np.testing.assert_allclose(proportions, y.sum(axis=0) / y.sum())
    np.testing.assert_allclose(mean, z.mean(axis=0))
    np.testing.assert_allclose(dispersion, expected)
    legacy = engine._control_moments(data, labels, 'a', lambda: None)[2]
    assert np.any(np.abs(legacy - dispersion) > .01)
    assert dispersion[-1] == 0


def test_equal_depths_retain_legacy_dispersion():
    data = Counts([[0, 10], [2, 8], [8, 2], [10, 0]])
    labels = np.array(['a'] * 4)
    legacy = engine._control_moments(data, labels, 'a', lambda: None)
    alternative = engine._control_moments(data, labels, 'a', lambda: None, 'depth-scaled-poisson-v1')
    for left, right in zip(legacy, alternative):
        np.testing.assert_array_equal(left, right)


def test_estimator_is_explicit_and_unknown_estimator_refuses():
    assert parameters('response-predict', {})['dispersion_estimator'] == 'normalized-moments-v1'
    assert parameters('response-predict', {'dispersion_estimator': 'depth-scaled-poisson-v1'})['dispersion_estimator'] == 'depth-scaled-poisson-v1'
    with pytest.raises(ValueError):
        parameters('response-predict', {'dispersion_estimator': 'unknown'})
    with pytest.raises(engine.Refused, match='estimator'):
        engine._control_moments(Counts([[1, 1]]), np.array(['a']), 'a', lambda: None, 'unknown')


def test_emission_keeps_legacy_default_and_retains_selected_estimator(tmp_path):
    anndata = pytest.importorskip('anndata')
    pd = pytest.importorskip('pandas')
    data = anndata.AnnData(sparse.csr_matrix([[0, 2], [8, 2], [1, 39], [80, 20]], dtype=np.float32),
        obs=pd.DataFrame({'context': ['a'] * 4, 'target_gene': ['non-targeting'] * 4},
                         index=['a', 'b', 'c', 'd']), var=pd.DataFrame(index=['g1', 'g2']))
    controls = tmp_path / 'controls.h5ad'; data.write_h5ad(controls)
    model = tmp_path / 'model.npz'
    np.savez_compressed(model, format=np.array('response-model-v1'), effects=np.zeros((1, 2)),
                        genes=np.array(['g1', 'g2']), targets=np.array(['null']), metadata=np.array('{}'))
    panel = tmp_path / 'panel.json'; panel.write_text('["null"]')
    matrices = []
    for index, choice in enumerate([None, 'normalized-moments-v1', 'depth-scaled-poisson-v1']):
        folder = tmp_path / str(index); folder.mkdir()
        supplied = {'cells_per_target': 100, 'seed': 4}
        if choice is not None:
            supplied['dispersion_estimator'] = choice
        path, summary = engine.predict({'controls': controls, 'model': model, 'panel': panel},
            parameters('response-predict', supplied), folder, lambda: None)
        matrices.append(anndata.read_h5ad(path).X.toarray())
        assert summary['dispersionEstimator'] == (choice or 'normalized-moments-v1')
    np.testing.assert_array_equal(matrices[0], matrices[1])
    assert not np.array_equal(matrices[1], matrices[2])
