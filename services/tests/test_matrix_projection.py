"""Synthetic source projection, exact values/identities and explicit refusal boundaries."""
import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from scipy import sparse

from scientific_compute import engine, matrix_projection as projection
from scientific_compute.catalog import descriptor, parameters


def fixture(tmp_path, storage='dense', **changes):
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    x = np.array([[1, 2, 30], [4, 5, 60], [7, 8, 90], [10, 11, 120], [13, 14, 150]], dtype=np.float32)
    data = ad.AnnData(X={'dense': lambda: x, 'csr': lambda: sparse.csr_matrix(x),
                        'csc': lambda: sparse.csc_matrix(x)}[storage](),
        obs=pd.DataFrame({'guide': ['nt', 'ga', 'gb', 'ga', 'nt']}, index=['cell4', 'cell3', 'cell2', 'cell1', 'cell0']),
        var=pd.DataFrame({'symbol': ['a', 'b', 'c']}, index=['id-a', 'id-b', 'id-c']))
    source = tmp_path/'source.h5ad'; data.write_h5ad(source)
    cfg = {'format': 'raw-count-projection-v1', 'context': 'context-a', 'experiment': 'screen-a',
           'observation_id': '_index', 'target_column': 'guide', 'target_mapping': {'nt': 'control', 'ga': 'target-a'},
           'control_label': 'control', 'selected_targets': ['target-a'], 'gene_id': '_index',
           'gene_symbol': 'symbol', 'selected_genes': ['b', 'a'], 'cells_per_group': 0, 'seed': 7}
    cfg.update(changes)
    protocol = tmp_path/'protocol.json'; protocol.write_text(json.dumps(cfg))
    return {'matrix': source, 'protocol': protocol}


def run(tmp_path, inputs, name='output', params=None, check=lambda: None):
    output = tmp_path/name; output.mkdir()
    return projection.project(inputs, parameters('matrix-project-counts', params or {}), output, check)


@pytest.mark.parametrize('storage', ['dense', 'csr'])
def test_exact_counts_reordered_genes_and_source_identity_survive_multichunk_projection(tmp_path, monkeypatch, storage):
    ad = pytest.importorskip('anndata'); h5py = pytest.importorskip('h5py')
    inputs = fixture(tmp_path, storage)
    with h5py.File(inputs['matrix'], 'r+') as source:
        source['layers'].create_dataset('unused', shape=(10**9, 3), dtype='f4', chunks=(1, 3))
    before = hashlib.sha256(inputs['matrix'].read_bytes()).hexdigest()
    monkeypatch.setattr(engine, 'BLOCK_ROWS', 2)
    path, summary = run(tmp_path, inputs)
    got = ad.read_h5ad(path)
    np.testing.assert_array_equal(got.X.toarray(), [[2, 1], [5, 4], [11, 10], [14, 13]])
    assert got.obs_names.tolist() == ['cell4', 'cell3', 'cell1', 'cell0']
    assert got.var_names.tolist() == ['b', 'a']
    assert got.var['source_gene_id'].tolist() == ['id-b', 'id-a']
    assert got.obs['source_annotation'].tolist() == ['nt', 'ga', 'ga', 'nt']
    assert got.obs['target_gene'].tolist() == ['control', 'target-a', 'target-a', 'control']
    meta = json.loads(got.uns['projection'])
    assert meta['excludedUnmappedObservations'] == 1 and meta['excludedBySampling'] == 0
    assert meta['sourceSha256'] == before == hashlib.sha256(inputs['matrix'].read_bytes()).hexdigest()
    assert summary['shape'] == [4, 2] and summary['sampling'] == 'all'


def test_sampling_uses_cell_identity_not_source_order_or_expression(tmp_path):
    ad = pytest.importorskip('anndata')
    inputs = fixture(tmp_path, cells_per_group=1)
    first, _ = run(tmp_path, inputs, 'first')
    obj = ad.read_h5ad(inputs['matrix'])[::-1].copy(); obj.X[:] = 777; obj.write_h5ad(inputs['matrix'])
    second, report = run(tmp_path, inputs, 'second')
    left, right = ad.read_h5ad(first), ad.read_h5ad(second)
    assert set(left.obs_names) == set(right.obs_names) and len(left) == 2
    assert report['excludedBySampling'] == 2 and report['sampling'] == 'sha256-cell-identity-v1'
    assert all(g['selected'] == 1 and g['available'] == 2 for g in report['groups'])


@pytest.mark.parametrize('changes,match', [
    ({'selected_genes': ['absent']}, 'Every selected gene'),
    ({'target_mapping': {'nt': 'control', 'absent': 'target-a'}}, 'annotation is absent'),
    ({'cells_per_group': 3}, 'requested number'),
    ({'cells_per_group': True}, 'Cells per group'),
    ({'seed': -1}, 'seed'),
    ({'selected_targets': ['target-a', 'control']}, 'distinct'),
    ({'selected_genes': ['a', 'a']}, 'unique'),
    ({'executable': 'something'}, 'complete'),
])
def test_no_implicit_panel_changes_or_executable_protocol(tmp_path, changes, match):
    with pytest.raises(engine.Refused, match=match):
        run(tmp_path, fixture(tmp_path, **changes))


@pytest.mark.parametrize('kind', ['fractional', 'negative', 'nonfinite', 'float32-loss', 'duplicate-gene', 'duplicate-cell', 'csc'])
def test_invalid_counts_or_ambiguous_identity_refuse(tmp_path, kind):
    ad = pytest.importorskip('anndata')
    inputs = fixture(tmp_path, 'csc' if kind == 'csc' else 'dense')
    if kind != 'csc':
        obj = ad.read_h5ad(inputs['matrix'])
        if kind == 'duplicate-gene': obj.var['symbol'] = ['a', 'a', 'c']
        elif kind == 'duplicate-cell': obj.obs_names = ['same'] * len(obj)
        else: obj.X[0, 0] = {'fractional': .1, 'negative': -1, 'nonfinite': np.nan, 'float32-loss': 2**25}[kind]
        obj.write_h5ad(inputs['matrix'])
    with pytest.raises(engine.Refused):
        run(tmp_path, inputs)


def test_resource_and_cancellation_refuse_without_final_result(tmp_path, monkeypatch):
    from types import SimpleNamespace
    inputs = fixture(tmp_path)
    with pytest.raises(engine.Refused, match='stored-entry'):
        run(tmp_path, inputs, 'entries', {'maximum_stored_entries': 1})
    monkeypatch.setattr(projection.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(engine.Refused, match='Free space'):
        run(tmp_path, inputs, 'disk')
    def cancel(): raise InterruptedError('stop requested')
    with pytest.raises(InterruptedError):
        run(tmp_path, inputs, 'cancel', check=cancel)
    assert not list(tmp_path.glob('*/projected-counts.h5ad'))


def test_projection_code_is_part_of_the_frozen_generation(monkeypatch):
    before = descriptor('matrix-project-counts')['generation']; read = Path.read_bytes
    monkeypatch.setattr(Path, 'read_bytes', lambda p: read(p) + (b'\n# changed\n' if p.name == 'matrix_projection.py' else b''))
    assert descriptor('matrix-project-counts')['generation'] != before


def test_prepared_confined_projection_retains_exact_inputs_and_retries(tmp_path, monkeypatch):
    import os
    prepared = os.environ.get('HOOKE_MATRIX_TEST_CATALOG')
    if not prepared:
        pytest.skip('Needs an explicitly prepared catalog; no installation in this test')
    from orchestrator.matrix_capability_runs import run_matrix
    from orchestrator.scientific_file_intake import FileIntake
    from orchestrator.scientific_projects import ProjectStore
    monkeypatch.setenv('MICROMIND_CATALOG', prepared)
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(tmp_path/'isolated-slot'))
    store = ProjectStore(tmp_path/'projects')
    refs = {}
    for port, path in fixture(tmp_path).items():
        body = path.read_bytes()
        intake = FileIntake(store, 'projection-test', 'projection-fixture-'+port, name=path.name,
                           size=len(body), max_bytes=len(body))
        try:
            intake.write(body); saved = intake.publish()
            refs[port] = {'id': saved.id, 'session': 'projection-test', 'revision': saved.revision, 'digest': saved.digest}
        finally:
            intake.close()
    args = dict(store=store, session='projection-test', capability='matrix-project-counts',
                generation=descriptor('matrix-project-counts')['generation'], inputs=refs,
                params={}, request='projection-confined-result-v1')
    result = run_matrix(**args)
    record = result.document['state']['matrixOperation']
    assert record['inputs']['matrix']['digest'] == refs['matrix']['digest']
    assert record['inputs']['protocol']['digest'] == refs['protocol']['digest']
    assert record['runtime']['owner'] == 'matrix-cpu-runtime'
    assert record['output']['summary']['shape'] == [4, 2]
    assert run_matrix(**args) == result
    assert len(store.list('projection-test')) == 3


def test_long_unmapped_annotations_do_not_expand_every_row_to_the_widest_label(tmp_path):
    """Real-sized annotation axis, tiny selected panel; counts stay untouched."""
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    inputs = fixture(tmp_path)
    n = 100_000
    labels = ['nt', 'ga'] + ['unmapped-' + 'x' * 231] * (n - 2)
    obj = ad.AnnData(X=sparse.csr_matrix((n, 3), dtype=np.float32),
                    obs=pd.DataFrame({'guide': pd.Categorical(labels)},
                                     index=[f'cell-{i}' for i in range(n)]),
                    var=pd.DataFrame({'symbol': ['a', 'b', 'c']}, index=['id-a', 'id-b', 'id-c']))
    obj.write_h5ad(inputs['matrix'])
    path, report = run(tmp_path, inputs)
    got = ad.read_h5ad(path)
    assert got.obs_names.tolist() == ['cell-0', 'cell-1']
    assert got.obs['source_annotation'].tolist() == ['nt', 'ga']
    assert report['excludedUnmappedObservations'] == n - 2
    assert report['shape'] == [2, 2] and got.X.nnz == 0


@pytest.mark.parametrize('encoding', ['categorical', 'legacy', 'plain', 'nullable'])
def test_compact_annotations_keep_exact_unicode_and_refuse_invalid_values(tmp_path, encoding, monkeypatch):
    h5py = pytest.importorskip('h5py')
    from scientific_compute import bounded_h5
    strings = ['short', 'λ' * 240, 'short']
    with h5py.File(tmp_path/'labels.h5', 'w') as h:
        if encoding in ('categorical', 'legacy'):
            values = h.create_dataset('values', data=np.array(strings[:2], dtype=h5py.string_dtype()))
            if encoding == 'categorical':
                node = h.create_group('labels'); node.attrs['encoding-type'] = 'categorical'
                node['categories'] = values
                codes = node.create_dataset('codes', data=[0, 1, 0])
            else:
                node = h.create_dataset('labels', data=[0, 1, 0]); node.attrs['categories'] = values.ref
                codes = node
        elif encoding == 'nullable':
            node = h.create_group('labels'); node.attrs['encoding-type'] = 'nullable-string-array'
            values = node.create_dataset('values', data=np.array(strings, dtype=h5py.string_dtype()))
            node.create_dataset('mask', data=[False]*3)
        else:
            node = values = h.create_dataset('labels', data=np.array(strings, dtype=h5py.string_dtype()))
        result = bounded_h5.text(node, 3, compact=True)
        assert result.dtype == object and result.tolist() == strings
        with monkeypatch.context() as scoped:
            scoped.setattr(bounded_h5, 'METADATA_BYTES', 32)
            with pytest.raises(engine.Refused, match='allocation'):
                bounded_h5.text(node, 3, compact=True)
        if encoding in ('categorical', 'legacy'):
            codes[1] = -1
        elif encoding == 'nullable':
            node['mask'][1] = True
        else:
            values[1] = 'x' * 1025
        with pytest.raises(engine.Refused, match='missing|width'):
            bounded_h5.text(node, 3, compact=True)


def test_compact_annotations_preserve_long_selected_labels_in_saved_projection(tmp_path):
    ad = pytest.importorskip('anndata')
    long_label = 'λ' * 240
    inputs = fixture(tmp_path, target_mapping={'nt': 'control', long_label: 'target-a'})
    obj = ad.read_h5ad(inputs['matrix'])
    obj.obs['guide'] = ['nt', long_label, 'gb', long_label, 'nt']
    obj.write_h5ad(inputs['matrix'])
    path, _ = run(tmp_path, inputs)
    assert ad.read_h5ad(path).obs['source_annotation'].tolist() == ['nt', long_label, long_label, 'nt']


def test_explicit_control_only_projection_preserves_counts_and_excludes_perturbations(tmp_path):
    ad = pytest.importorskip('anndata')
    inputs = fixture(tmp_path, selected_targets=[], target_mapping={'nt': 'control'})
    path, report = run(tmp_path, inputs)
    result = ad.read_h5ad(path)
    assert result.obs_names.tolist() == ['cell4', 'cell0']
    assert result.obs['target_gene'].tolist() == ['control', 'control']
    np.testing.assert_array_equal(result.X.toarray(), [[2, 1], [14, 13]])
    assert report['groups'] == [{'target': 'control', 'available': 2, 'selected': 2}]
    assert report['excludedUnmappedObservations'] == 3
    assert json.loads(result.uns['projection'])['protocol']['selected_targets'] == []


@pytest.mark.parametrize('changes', [
    {'selected_targets': [], 'target_mapping': {'nt': 'control', 'ga': 'target-a'}},
    {'selected_targets': [], 'target_mapping': {'nt': 'control'}, 'selected_genes': []},
])
def test_control_only_request_cannot_hide_mapped_perturbations_or_drop_gene_axis(tmp_path, changes):
    with pytest.raises(engine.Refused):
        run(tmp_path, fixture(tmp_path, **changes))
