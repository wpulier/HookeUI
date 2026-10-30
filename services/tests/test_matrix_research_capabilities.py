"""Tiny synthetic scientific calculations and real retained files; no contest evidence."""
import io
import json
import zipfile
from pathlib import Path

import numpy as np
import pytest
from scipy import sparse

from scientific_compute import engine
from scientific_compute.catalog import descriptor, parameters
from scientific_compute.reservation import heavy_work


def effects(path, rows, targets, contexts, genes=('g1', 'g2')):
    np.savez_compressed(path, format=np.array('response-effects-v1'), effects=np.asarray(rows, dtype=float),
                        targets=np.array(targets), contexts=np.array(contexts), genes=np.array(genes))
    return path


def output(tmp_path, name):
    folder = tmp_path / name; folder.mkdir(); return folder


def test_fitted_ridge_matches_known_weighted_solution_and_panel_size(tmp_path):
    for n in (1, 300):
        source = effects(tmp_path / f'effects{n}.npz', [[1., -2.]] * (n * 2),
                         [f't{i}' for i in range(n)] * 2, ['a'] * n + ['b'] * n)
        fitted, summary = engine.fit({'effects': source}, parameters('response-fit', {'regularization': .1}),
                                     output(tmp_path, f'fit{n}'), lambda: None)
        model = engine._model(fitted)
        np.testing.assert_allclose(model['effects'][0], [1 / 1.1, -2 / 1.1])
        assert summary['penaltyScale'] == 'per-target-observed-weight'
        assert summary['trainingContexts'] == ['a', 'b']


def test_whole_context_training_records_and_never_uses_excluded_labels(tmp_path):
    # The caller prepares an explicitly separated development fold; this is not a sealed evaluator test.
    source = effects(tmp_path / 'fold.npz', [[1., 2.], [2., 3.]], ['x', 'y'], ['development'] * 2)
    path, meta = engine.fit({'effects': source}, parameters('response-fit', {}), output(tmp_path, 'fit'), lambda: None)
    assert meta['trainingContexts'] == ['development']
    assert engine._model(path)['targets'].tolist() == ['x', 'y']


def test_combination_refuses_changed_gene_order(tmp_path):
    def model(name, genes):
        source = effects(tmp_path / f'{name}.npz', [[1., 2.]], ['x'], ['a'], genes)
        return engine.fit({'effects': source}, parameters('response-fit', {}), output(tmp_path, name), lambda: None)[0]
    with pytest.raises(engine.Refused, match='ordered'):
        engine.combine({'left': model('left', ['g1', 'g2']), 'right': model('right', ['g2', 'g1'])},
                       parameters('response-combine', {}), output(tmp_path, 'combine'), lambda: None)


@pytest.mark.parametrize('kind', ['nonfinite', 'duplicate', 'object', 'huge-header'])
def test_unsafe_numerical_artifacts_refuse(tmp_path, kind):
    path = effects(tmp_path / 'bad.npz', [[np.nan if kind == 'nonfinite' else 1, 2]], ['x'], ['a'])
    if kind == 'duplicate':
        path = effects(path, [[1, 2], [3, 4]], ['x', 'x'], ['a', 'a'])
    if kind == 'object':
        np.savez(path, format=np.array('response-effects-v1'), effects=np.array([object()], dtype=object),
                 genes=np.array(['g1']), targets=np.array(['x']), contexts=np.array(['a']))
    if kind == 'huge-header':
        header = io.BytesIO()
        np.lib.format.write_array_header_1_0(header, {'descr': '<f8', 'fortran_order': False, 'shape': (10 ** 12, 2)})
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('format.npy', header.getvalue())
    with pytest.raises((engine.Refused, ValueError)):
        engine.fit({'effects': path}, parameters('response-fit', {}), output(tmp_path, 'fit'), lambda: None)


def test_heavy_slot_preserves_existing_owner_and_releases_only_self(tmp_path, monkeypatch):
    slot = tmp_path / 'heavy'; monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(slot))
    with heavy_work('synthetic fit'):
        owner = (slot / 'owner.json').read_bytes()
        with pytest.raises(RuntimeError, match='occupied'):
            with heavy_work('second fit'):
                pytest.fail('A second job must not enter')
        assert (slot / 'owner.json').read_bytes() == owner
    assert not slot.exists()
    slot.mkdir(); (slot / 'owner').write_text('Beta')
    with pytest.raises(RuntimeError):
        with heavy_work('research'):
            pass
    assert (slot / 'owner').read_text() == 'Beta'


def adata(path, matrix, contexts, targets, genes=('g1', 'g2', 'unmeasured')):
    anndata = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    obj = anndata.AnnData(sparse.csr_matrix(matrix, dtype=np.float32),
          obs=pd.DataFrame({'context': contexts, 'target_gene': targets}, index=[f'cell{i}' for i in range(len(matrix))]),
          var=pd.DataFrame(index=list(genes)))
    obj.write_h5ad(path)
    return path


def test_prepare_retains_explicit_contexts_and_refuses_invalid_counts(tmp_path):
    data = adata(tmp_path / 'train.h5ad', [[10, 20], [20, 10], [15, 15], [30, 0]], ['a', 'a', 'b', 'b'],
                 ['non-targeting', 'x'] * 2, genes=['g1', 'g2'])
    path, summary = engine.prepare({'matrix': data}, parameters('matrix-prepare', {}), output(tmp_path, 'prepare'), lambda: None)
    with np.load(path) as result:
        assert result['contexts'].tolist() == ['a', 'b']
        expected = np.log2((np.array([20, 10]) / 30 * 10000 + .1) / (np.array([10, 20]) / 30 * 10000 + .1))
        np.testing.assert_allclose(result['effects'][0], expected)
    bad = adata(tmp_path / 'bad.h5ad', [[-.5, 1]], ['a'], ['non-targeting'], ['g1', 'g2'])
    with pytest.raises(engine.Refused, match='whole counts'):
        engine.inspect({'matrix': bad}, {}, output(tmp_path, 'inspect'), lambda: None)


def test_streaming_emission_keyed_seeds_null_counts_and_axis(tmp_path, monkeypatch):
    anndata = pytest.importorskip('anndata')
    source = effects(tmp_path / 'effects.npz', [[0., 0.]], ['known'], ['development'])
    model, _ = engine.fit({'effects': source}, parameters('response-fit', {}), output(tmp_path, 'fit'), lambda: None)
    controls = adata(tmp_path / 'controls.h5ad', [[10, 20, 5], [11, 19, 5], [9, 21, 5], [10, 20, 5]],
                     ['new'] * 4, ['non-targeting'] * 4)
    args = parameters('response-predict', {'cells_per_target': 100, 'seed': 4})
    results = []
    for index, (panel, chunk) in enumerate([(['known', 'unknown'], 31), (['unknown', 'known'], 47)]):
        targets = tmp_path / f'panel{index}.json'; targets.write_text(json.dumps(panel))
        monkeypatch.setattr(engine, 'BLOCK_ROWS', chunk)
        path, summary = engine.predict({'controls': controls, 'model': model, 'panel': targets}, args,
                                       output(tmp_path, f'predict{index}'), lambda: None)
        result = anndata.read_h5ad(path)
        assert sparse.issparse(result.X) and result.X.dtype == np.float32
        assert result.var_names.tolist() == ['g1', 'g2', 'unmeasured']
        assert summary['unseenTargets'] == ['unknown'] and summary['unmeasuredGenes'] == ['unmeasured']
        assert np.isfinite(result.X.data).all() and (result.X.data == np.floor(result.X.data)).all()
        assert summary['fullShapeQualified'] is False
        results.append(result)
    for target in ['known', 'unknown']:
        a = results[0][results[0].obs.target_gene == target].X.toarray()
        b = results[1][results[1].obs.target_gene == target].X.toarray()
        np.testing.assert_array_equal(a, b)
        np.testing.assert_allclose(a.mean(axis=0), [10, 20, 5], atol=2)
        assert len(np.unique(a, axis=0)) > 80


def test_evaluation_is_explicit_diagnostic_and_panel_mismatch_refuses(tmp_path):
    data = adata(tmp_path / 'sample.h5ad', [[10, 20, 5], [11, 19, 5]], ['a'] * 2, ['x'] * 2)
    path, meta = engine.evaluate({'prediction': data, 'reference': data}, parameters('matrix-evaluate', {}),
                                output(tmp_path, 'evaluate'), lambda: None)
    assert meta['competitionScore'] is False and meta['rows'][0]['mse'] == 0
    with pytest.raises(engine.Refused, match='not installed'):
        engine.evaluate({}, {'profile': 'unknown'}, tmp_path, lambda: None)


@pytest.mark.parametrize('values,whole', [([.25, 1.5], False), ([-1., 2.], False), ([1., 2.], True)])
def test_numeric_inspection_is_explicit_and_does_not_relax_count_operations(tmp_path, values, whole):
    source = adata(tmp_path / 'values.h5ad', [values], ['a'], ['x'], ['g1', 'g2'])
    params = parameters('matrix-inspect', {'require_raw_counts': False})
    _, result = engine.inspect({'matrix': source}, params, output(tmp_path, 'describe'), lambda: None)
    assert result['shape'] == [1, 2] and result['wholeNonnegativeValues'] is whole
    assert 'rawCounts' not in result
    assert result['valueSemantics'] == 'unverified' and 'libraryDepth' not in result
    assert result['rowTotals']['mean'] == sum(values)
    if not whole:
        with pytest.raises(engine.Refused, match='whole counts'):
            engine.inspect({'matrix': source}, parameters('matrix-inspect', {}), output(tmp_path, 'strict'), lambda: None)
        with pytest.raises(engine.Refused, match='whole counts'):
            engine.export({'matrix': source}, {}, output(tmp_path, 'export'), lambda: None)


def test_numeric_inspection_still_refuses_nonfinite_values(tmp_path):
    source = adata(tmp_path / 'values.h5ad', [[np.nan, 1.]], ['a'], ['x'], ['g1', 'g2'])
    with pytest.raises(engine.Refused, match='finite'):
        engine.inspect({'matrix': source}, {'require_raw_counts': False}, output(tmp_path, 'inspect'), lambda: None)


@pytest.mark.parametrize('representation', ['dense', 'csr'])
@pytest.mark.parametrize('block_rows', [1, 3])
def test_inspection_measures_depth_reciprocal_not_reciprocal_of_mean(tmp_path, monkeypatch, representation, block_rows):
    anndata = pytest.importorskip('anndata')
    source = adata(tmp_path / 'depth.h5ad', [[1, 1], [2, 2], [4, 4]], ['a'] * 3, ['x'] * 3, ['g1', 'g2'])
    if representation == 'dense':
        obj = anndata.read_h5ad(source); obj.X = obj.X.toarray(); obj.write_h5ad(source)
    monkeypatch.setattr(engine, 'BLOCK_ROWS', block_rows)
    path, summary = engine.inspect({'matrix': source}, {}, output(tmp_path, 'inspect'), lambda: None)
    depth = summary['libraryDepth']
    assert depth['median'] == 4 and depth['zeroObservations'] == 0
    assert depth['meanReciprocal'] == pytest.approx(7 / 24)
    assert depth['meanReciprocal'] != pytest.approx(1 / depth['mean'])
    assert json.loads(path.read_text())['libraryDepth'] == depth


def test_inspection_does_not_drop_zero_depth_observations(tmp_path):
    source = adata(tmp_path / 'depth.h5ad', [[0, 0], [1, 3]], ['a'] * 2, ['x'] * 2, ['g1', 'g2'])
    _, summary = engine.inspect({'matrix': source}, {}, output(tmp_path, 'inspect'), lambda: None)
    depth = summary['libraryDepth']
    assert depth['median'] == 2 and depth['mean'] == 2
    assert depth['zeroObservations'] == 1 and depth['meanReciprocal'] is None
    assert 'undefined' in depth['reciprocalMeaning']


def test_transformed_inspection_never_describes_reciprocal_as_count_depth(tmp_path):
    source = adata(tmp_path / 'transformed.h5ad', [[-2, 0], [.25, .75]], ['a'] * 2, ['x'] * 2, ['g1', 'g2'])
    _, summary = engine.inspect({'matrix': source}, {'require_raw_counts': False}, output(tmp_path, 'inspect'), lambda: None)
    assert summary['rowTotals']['median'] == -.5
    assert 'meanReciprocal' not in summary['rowTotals'] and 'libraryDepth' not in summary
