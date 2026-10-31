"""Hand-calculated count moments; synthetic arrays, not biological validation."""
import json
import pytest
from scientific_compute import engine
from scientific_compute.catalog import parameters
from tests.test_matrix_research_capabilities import adata as matrix_file, output


def adata(path, values, contexts, targets):
    return matrix_file(path, values, contexts, targets, genes=["g1", "g2"])


def compare(tmp_path, prediction, reference, **params):
    return engine.OPERATIONS['matrix-count-diagnostics'](
        {'prediction': prediction, 'reference': reference}, parameters('matrix-count-diagnostics', params),
        output(tmp_path, 'diagnostics'), lambda: None)


def test_hand_calculated_population_moments_and_distinct_labels(tmp_path):
    reference = adata(tmp_path/'reference.h5ad', [[0, 2], [2, 2]], ['a']*2, ['control']*2)
    prediction = adata(tmp_path/'prediction.h5ad', [[1, 1], [1, 3]], ['a']*2, ['null']*2)
    path, summary = compare(tmp_path, prediction, reference, normalization_total=4)
    row = summary['contexts'][0]
    assert row['metrics'] == {'meanRelativeL1': 0, 'varianceRelativeL1': 2,
                               'normalizedRelativeL1': .25, 'zeroFractionMae': .25}
    assert row['referenceDepth']['mean'] == 3
    assert row['referenceDepth']['stdPopulation'] == 1
    assert row['referenceDepth']['quantiles'] == pytest.approx([2, 2.1, 3, 3.9, 4])
    assert row['referencePopulation'] == 'control' and row['predictionPopulation'] == 'null'
    residuals = row['zeroResiduals']
    assert residuals['allGenes']['meanSignedError'] == -.25
    assert residuals['allGenes']['fewerZeros'] == 1
    assert residuals['referenceMeanRankCorrelation'] == pytest.approx(1)
    assert residuals['strata'][0]['meanSignedError'] == -.5
    assert residuals['strata'][-1]['meanSignedError'] == 0
    full = json.loads(path.read_text())
    assert full['details'][0]['reference']['variance'] == [1, 0]
    assert full['details'][0]['prediction']['variance'] == [0, 1]
    assert not summary['competitionScore'] and not summary['distributionEqualityEstablished']
    assert 'details' not in summary and 'geneAxis' not in summary


def test_residual_strata_keep_ties_and_zero_reference_genes_separate():
    import numpy as np
    from scientific_compute.count_diagnostics import _zero_residuals
    reference = {'mean': np.array([0., 1., 1., 10.]), 'zero': np.array([1., .5, .5, 0.])}
    prediction = {'zero': np.array([.5, .75, .75, 0.])}
    result = _zero_residuals(prediction, reference)
    assert result['zeroReferenceMean']['meanSignedError'] == -.5
    assert result['zeroReferenceMean']['genes'] == 1
    assert result['positiveReferenceGenes'] == 3
    assert result['referenceMeanRankCorrelation'] == pytest.approx(-1)
    populated = [row for row in result['strata'] if row['genes']]
    assert [row['genes'] for row in populated] == [2, 1]
    assert populated[0]['referenceMeanRange'] == [1, 1]
    assert populated[0]['meanSignedError'] == .25
    assert sum(row['genes'] for row in result['strata']) == 3
    assert all(row['meanSignedError'] is None for row in result['strata'] if not row['genes'])
    order = [3, 1, 0, 2]
    assert _zero_residuals({'zero': prediction['zero'][order]},
                          {k: v[order] for k, v in reference.items()}) == result


@pytest.mark.parametrize('means,zeros', [([0, 0], [1, 1]), ([1, 1], [.5, .7]),
                                        ([1, 2], [.5, .5]), ([1], [.7])])
def test_unavailable_residual_correlation_is_null_not_nan(means, zeros):
    import numpy as np
    from scientific_compute.count_diagnostics import _zero_residuals
    result = _zero_residuals({'zero': np.array(zeros)},
                             {'mean': np.array(means), 'zero': np.zeros(len(means))})
    assert result['referenceMeanRankCorrelation'] is None
    assert sum(row['genes'] for row in result['strata']) + result['zeroReferenceMean']['genes'] == len(means)
    json.dumps(result, allow_nan=False)


def test_replication_and_dense_storage_do_not_change_moments(tmp_path):
    anndata = pytest.importorskip('anndata')
    source = adata(tmp_path/'source.h5ad', [[0, 2], [2, 2]], ['a']*2, ['x']*2)
    replica = adata(tmp_path/'replica.h5ad', [[0, 2], [2, 2]]*3, ['a']*6, ['x']*6)
    data = anndata.read_h5ad(replica); data.X = data.X.toarray(); data.write_h5ad(replica)
    _, summary = compare(tmp_path, replica, source)
    assert all(v == 0 for v in summary['contexts'][0]['metrics'].values())
    assert summary['contexts'][0]['predictionCells'] == 6


def test_zero_variance_reference_reports_unavailable_ratio(tmp_path):
    source = adata(tmp_path/'source.h5ad', [[1, 1]]*2, ['a']*2, ['x']*2)
    _, summary = compare(tmp_path, source, source)
    assert summary['contexts'][0]['metrics']['varianceRelativeL1'] is None


@pytest.mark.parametrize('fault', ['genes', 'contexts', 'mixed-populations', 'zero-depth', 'fraction', 'negative', 'one-cell', 'csc', 'same-column'])
def test_incompatible_count_populations_refuse(tmp_path, fault):
    anndata = pytest.importorskip('anndata')
    reference = adata(tmp_path/'reference.h5ad', [[0, 2], [2, 2]], ['a']*2, ['control']*2)
    prediction = adata(tmp_path/'prediction.h5ad', [[1, 1], [1, 3]], ['a']*2, ['null']*2)
    data = anndata.read_h5ad(prediction); params = {}
    if fault == 'genes': data.var_names = list(reversed(data.var_names))
    if fault == 'contexts': data.obs['context'] = ['b']*2
    if fault == 'mixed-populations': data.obs['target_gene'] = ['x', 'y']
    if fault == 'zero-depth': data.X[0, :] = 0
    if fault == 'fraction': data.X[0, 0] = .5
    if fault == 'negative': data.X[0, 0] = -1
    if fault == 'one-cell': data = data[:1].copy()
    if fault == 'csc': data.X = data.X.tocsc()
    if fault == 'same-column': params['target_column'] = 'context'
    data.write_h5ad(prediction)
    with pytest.raises(engine.Refused): compare(tmp_path, prediction, reference, **params)


def test_contexts_are_compared_separately_with_no_population_weighting(tmp_path):
    source = adata(tmp_path/'source.h5ad', [[1, 1]]*2 + [[2, 2]]*3, ['a']*2 + ['b']*3, ['x']*5)
    _, summary = compare(tmp_path, source, source)
    assert [r['referenceCells'] for r in summary['contexts']] == [2, 3]
    assert 'overall' not in summary


def test_bounded_metadata_and_auxiliary_array_exclusion(tmp_path):
    h5py = pytest.importorskip('h5py')
    source = adata(tmp_path/'source.h5ad', [[1, 2]]*2, ['a']*2, ['x']*2)
    with h5py.File(source, 'r+') as f:
        f['layers'].create_dataset('never-read', shape=(10**9,), dtype='f8', chunks=(1,), fillvalue=0)
    _, summary = compare(tmp_path, source, source)
    assert summary['contexts'][0]['metrics']['meanRelativeL1'] == 0


def test_malformed_pointer_refuses_before_reading_counts(tmp_path):
    h5py = pytest.importorskip('h5py')
    source = adata(tmp_path/'source.h5ad', [[1, 2]]*2, ['a']*2, ['x']*2)
    with h5py.File(source, 'r+') as f: f['X/indptr'][0] = 1
    with pytest.raises(engine.Refused): compare(tmp_path, source, source)


@pytest.mark.parametrize('cells', [2, 258])
def test_large_offsets_preserve_small_variance_across_blocks(tmp_path, cells):
    anndata = pytest.importorskip('anndata')
    import numpy as np
    from scipy import sparse
    source = adata(tmp_path/'source.h5ad', [[1, 2]]*cells, ['a']*cells, ['x']*cells)
    data = anndata.read_h5ad(source)
    data.X = sparse.csr_matrix(np.tile([[100000000, 1], [100000001, 1]], (cells//2, 1)), dtype=np.float64)
    data.write_h5ad(source)
    path, summary = compare(tmp_path, source, source)
    assert json.loads(path.read_text())['details'][0]['reference']['variance'] == pytest.approx([.25, 0])
    assert summary['contexts'][0]['metrics']['varianceRelativeL1'] == 0
