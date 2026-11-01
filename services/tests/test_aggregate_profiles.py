"""Tiny declared aggregate fixtures; no public dataset or scientific-quality claim."""
import json
import os
from pathlib import Path

import numpy as np
import pytest

from scientific_compute import aggregate_profiles as aggregate, engine
from scientific_compute.catalog import parameters


@pytest.mark.parametrize('helper', ['aggregate_profiles.py', 'bounded_h5.py'])
def test_changed_compute_helper_invalidates_frozen_capability_generation(monkeypatch, helper):
    from scientific_compute.catalog import descriptor
    previous = descriptor('aggregate-profile-prepare')['generation']
    read = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda path: read(path) + (b'\n# changed implementation\n' if path.name == helper else b''))
    assert descriptor('aggregate-profile-prepare')['generation'] != previous


def fixture(tmp_path, context='alpha', **changes):
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    folder = tmp_path / context; folder.mkdir(exist_ok=True)
    matrix = folder / 'means.h5ad'
    ad.AnnData(np.array([[3.5, 5, 7], [6.5, 3, 10], [10, 1, 5], [np.nan] * 3], dtype=np.float32),
        obs=pd.DataFrame({'cells': [2., 2., 1., np.nan], 'core': [True, False, False, False]},
                         index=['control-row', 'construct-a', 'construct-b', 'excluded-row']),
        var=pd.DataFrame({'symbol': ['g1', 'g2', 'g3']}, index=['id1', 'id2', 'id3'])).write_h5ad(matrix)
    cfg = {'format': 'aggregate-profile-config-v1', 'scientific_unit': 'mean-raw-counts-per-filtered-cell',
           'context': context, 'experiment': 'screen-one', 'row_id': '_index', 'weight_column': 'cells',
           'row_targets': {'construct-a': 'target-x', 'construct-b': 'target-x'},
           'control': {'column': 'core', 'equals': True, 'label': 'non-targeting'},
           'gene_id': '_index', 'gene_symbol': 'symbol', 'selected_genes': ['g2', 'g1'],
           'selected_targets': ['target-x'], 'normalization_genes': ['g1', 'g2', 'g3'],
           'normalization_total': 10000, 'pseudocount': .1, 'duplicate_symbols': 'refuse', 'missing_policy': 'refuse'}
    cfg.update(changes)
    protocol = folder / 'protocol.json'; protocol.write_text(json.dumps(cfg))
    return {'matrix': matrix, 'protocol': protocol}


def run(inputs, tmp_path, name='prepared'):
    output = tmp_path / name; output.mkdir()
    return aggregate.prepare(inputs, {}, output, lambda: None)


def test_mean_profiles_equal_explicit_cells_and_normalization_is_independent_of_output_axis(tmp_path):
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    data, summary = run(fixture(tmp_path), tmp_path)
    raw = tmp_path / 'cells.h5ad'
    ad.AnnData(np.array([[2, 4, 6], [5, 6, 8], [4, 2, 8], [9, 4, 12], [10, 1, 5]], dtype=np.float32),
        obs=pd.DataFrame({'context': ['alpha'] * 5, 'target_gene': ['non-targeting'] * 2 + ['target-x'] * 3},
                         index=[f'cell{i}' for i in range(5)]), var=pd.DataFrame(index=['g1', 'g2', 'g3'])).write_h5ad(raw)
    output = tmp_path / 'raw'; output.mkdir()
    expected, _ = engine.prepare({'matrix': raw}, parameters('matrix-prepare', {}), output, lambda: None)
    with np.load(data) as got, np.load(expected) as actual:
        np.testing.assert_allclose(got['effects'], actual['effects'][:, [1, 0]])
        assert got['genes'].tolist() == ['g2', 'g1'] and got['sizes'].tolist() == [3]
        meta = json.loads(got['metadata'].item())
        assert meta['coverage']['excludedRows'] == ['excluded-row']
        assert len(meta['provenance']['sourceContributions']) == 3
        assert meta['provenance']['geneMapping'] == {'g1': 'id1', 'g2': 'id2', 'g3': 'id3'}
        reference = got['effects'][0, 1]
    reduced, _ = run(fixture(tmp_path, 'reduced', selected_genes=['g1']), tmp_path, 'reduced-output')
    with np.load(reduced) as result:
        assert result['effects'][0, 0] == reference
    assert summary['provenanceRetained'] and summary['missingTargets'] == 0
    with pytest.raises(engine.Refused, match='whole counts|finite'):
        engine.inspect({'matrix': fixture(tmp_path)['matrix']}, {}, tmp_path, lambda: None)


def test_explicit_coverage_exclusion_does_not_fabricate_zero_effects(tmp_path):
    inputs = fixture(tmp_path, selected_genes=['g2', 'unmeasured'], selected_targets=['target-x', 'unknown'], missing_policy='exclude')
    path, summary = run(inputs, tmp_path)
    with np.load(path) as value:
        assert value['genes'].tolist() == ['g2'] and value['targets'].tolist() == ['target-x']
        meta = json.loads(value['metadata'].item())
        assert meta['coverage']['missingGenes'] == ['unmeasured']
        assert meta['coverage']['missingTargets'] == ['unknown']
    assert summary['missingGenes'] == summary['missingTargets'] == 1


@pytest.mark.parametrize('change,message', [
    ({'scientific_unit': 'z-score'}, 'mean raw counts'),
    ({'row_targets': {'invented': 'target-x'}}, 'does not exist'),
    ({'row_targets': {'control-row': 'target-x'}}, 'both a control'),
    ({'selected_targets': ['target-x', 'missing']}, 'fully measured'),
    ({'normalization_genes': ['g1', 'absent']}, 'normalization gene axis'),
    ({'duplicate_symbols': 'first'}, 'duplicate'),
    ({'python': 'exec(anything)'}, 'complete aggregate-profile protocol'),
])
def test_protocol_does_not_guess_sources_or_silently_change_meaning(tmp_path, change, message):
    with pytest.raises(engine.Refused, match=message):
        run(fixture(tmp_path, **change), tmp_path)


@pytest.mark.parametrize('kind', ['weight', 'nonfinite', 'negative', 'duplicate'])
def test_selected_invalid_values_weights_and_ambiguous_gene_symbols_refuse(tmp_path, kind):
    ad = pytest.importorskip('anndata')
    inputs = fixture(tmp_path); obj = ad.read_h5ad(inputs['matrix'])
    if kind == 'weight': obj.obs.loc['construct-a', 'cells'] = 1.5
    elif kind == 'nonfinite': obj.X[1, 1] = np.inf
    elif kind == 'negative': obj.X[1, 1] = -1
    else: obj.var['symbol'] = ['g1', 'g1', 'g3']
    obj.write_h5ad(inputs['matrix'])
    with pytest.raises(engine.Refused):
        run(inputs, tmp_path)


def test_legacy_categorical_gene_symbols_are_supported_without_auxiliary_array_reads(tmp_path):
    h5py = pytest.importorskip('h5py')
    inputs = fixture(tmp_path)
    with h5py.File(inputs['matrix'], 'r+') as h:
        del h['var']['symbol']
        categories = h['var'].create_dataset('symbol_categories', data=np.array(['g3', 'g1', 'g2'], dtype=h5py.string_dtype()))
        codes = h['var'].create_dataset('symbol', data=np.array([1, 2, 0], dtype=np.int8))
        codes.attrs['categories'] = categories.ref
        h['obsm'].create_dataset('unused', shape=(4, 10**9), chunks=(1, 128), dtype='f8')
    path, _ = run(inputs, tmp_path)
    with np.load(path) as value:
        assert value['genes'].tolist() == ['g2', 'g1']


@pytest.mark.parametrize('layout', ['csr', 'csc'])
@pytest.mark.parametrize('byte_budget', [64 << 20, 192])
def test_sparse_preparation_never_loads_the_complete_backed_matrix(tmp_path, monkeypatch, layout, byte_budget):
    ad = pytest.importorskip('anndata'); h5py = pytest.importorskip('h5py')
    from scipy import sparse
    from scientific_compute import bounded_h5
    monkeypatch.setattr(bounded_h5, 'BLOCK_BYTES', byte_budget)
    inputs = fixture(tmp_path)
    original, _ = run(inputs, tmp_path, 'dense')
    obj = ad.read_h5ad(inputs['matrix'])
    obj.X = sparse.csr_matrix(obj.X) if layout == 'csr' else sparse.csc_matrix(obj.X)
    obj.write_h5ad(inputs['matrix'])
    with h5py.File(inputs['matrix'], 'r') as h:
        backed_type = type(bounded_h5.matrix(h))
    monkeypatch.setattr(backed_type, 'to_memory', lambda *a: pytest.fail('A complete sparse input must never be materialized'))
    if layout == 'csc':
        with pytest.raises(engine.Refused, match='dense or CSR'):
            run(inputs, tmp_path, 'sparse')
    else:
        result, _ = run(inputs, tmp_path, 'sparse')
        with np.load(original) as left, np.load(result) as right:
            np.testing.assert_allclose(left['effects'], right['effects'])


def test_effect_merge_preserves_contexts_provenance_and_rejects_duplicate_or_incompatible_sources(tmp_path):
    left, _ = run(fixture(tmp_path), tmp_path, 'left')
    right, _ = run(fixture(tmp_path, 'beta'), tmp_path, 'right')
    folder = tmp_path / 'merge'; folder.mkdir()
    merged, _ = aggregate.merge({'left': left, 'right': right}, {}, folder, lambda: None)
    with np.load(merged) as result:
        assert result['contexts'].tolist() == ['alpha', 'beta']
        meta = json.loads(result['metadata'].item())
        assert len(meta['provenance']['sources']) == 2
        assert all(len(v['sha256']) == 64 for v in meta['provenance']['sources'])
    fitted, report = engine.fit({'effects': merged}, parameters('response-fit', {}), folder, lambda: None)
    assert fitted.exists() and report['trainingContexts'] == ['alpha', 'beta']
    with pytest.raises(engine.Refused, match='Duplicate biological'):
        aggregate.merge({'left': left, 'right': left}, {}, folder, lambda: None)
    different, _ = run(fixture(tmp_path, 'different', normalization_genes=['g1', 'g2']), tmp_path, 'other')
    with pytest.raises(engine.Refused, match='normalization meaning'):
        aggregate.merge({'left': left, 'right': different}, {}, folder, lambda: None)
    with np.load(right) as source:
        data = dict(source); metadata = json.loads(data['metadata'].item()); del metadata['normalization']
        data['metadata'] = np.array(json.dumps(metadata)); np.savez(right, **data)
    with pytest.raises(engine.Refused, match='reprepare legacy effects'):
        aggregate.merge({'left': left, 'right': right}, {}, folder, lambda: None)


def test_prepared_catalog_confined_aggregate_merge_fit_retains_exact_input_chain(tmp_path, monkeypatch):
    prepared = os.environ.get('HOOKE_MATRIX_TEST_CATALOG')
    if not prepared:
        pytest.skip('Requires an explicitly prepared isolated catalog; no setup is performed by this test')
    from orchestrator.matrix_capability_runs import run_matrix
    from orchestrator.scientific_file_intake import FileIntake
    from orchestrator.scientific_projects import ProjectStore
    from scientific_compute.catalog import descriptor
    monkeypatch.setenv('MICROMIND_CATALOG', prepared)
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(tmp_path / 'worker-slot'))
    store = ProjectStore(tmp_path / 'projects')
    def ref(saved):
        return {'id': saved.id, 'session': 'aggregate-study', 'revision': saved.revision, 'digest': saved.digest}
    def retain(path, request):
        data = path.read_bytes()
        intake = FileIntake(store, 'aggregate-study', request, name=path.name, size=len(data), max_bytes=len(data))
        try:
            intake.write(data); return ref(intake.publish())
        finally:
            intake.close()
    def invoke(capability, inputs, request):
        return run_matrix(store, 'aggregate-study', capability, descriptor(capability)['generation'], inputs, {}, request)
    outputs = []
    for context in ['first', 'second']:
        inputs = {port: retain(path, f'{context}-{port}-fixture-012345') for port, path in fixture(tmp_path, context).items()}
        saved = invoke('aggregate-profile-prepare', inputs, f'{context}-prepare-012345')
        record = saved.document['state']['matrixOperation']
        assert record['inputs']['protocol']['digest'] == inputs['protocol']['digest']
        assert record['runtime']['owner'] == 'matrix-cpu-runtime'
        assert record['output']['summary']['contexts'] == [context]
        outputs.append(ref(saved))
    merged = invoke('response-effects-merge', dict(zip(('left', 'right'), outputs)), 'merge-effects-012345')
    fitted = invoke('response-fit', {'effects': ref(merged)}, 'fit-effects-012345')
    record = fitted.document['state']['matrixOperation']
    assert record['output']['summary']['trainingContexts'] == ['first', 'second']
    assert invoke('response-fit', {'effects': ref(merged)}, 'fit-effects-012345') == fitted
    assert len(store.list('aggregate-study')) == 8
