"""Synthetic protocol/boundary checks; optional real evaluator qualification is separate."""
import json
from pathlib import Path

import numpy as np
import pytest
from scientific_compute import cell_evaluation as evaluation
from scientific_compute import catalog


def declared(tmp_path, **changes):
    value = {'preset': 'v2', 'context_column': 'context', 'target_column': 'target',
             'control_label': 'control', 'panels': {'context-one': ['targetA', 'targetB']}}
    value.update(changes)
    path = tmp_path / 'protocol.json'; path.write_text(json.dumps(value)); return path


@pytest.mark.parametrize('changes', [
    {'panels': {}}, {'panels': {'context-one': []}},
    {'panels': {'context-one': ['targetA', 'targetA']}},
    {'panels': {'context-one': ['control']}}, {'shell': 'arbitrary-command'},
])
def test_protocol_requires_full_explicit_unique_target_panels(tmp_path, changes):
    with pytest.raises(evaluation.Refused):
        evaluation.protocol(declared(tmp_path, **changes))


def test_protocol_records_exact_preset_and_unavailable_metrics_remain_unavailable(tmp_path):
    cfg = evaluation.protocol(declared(tmp_path))
    assert cfg['preset'] == 'v2' and cfg['panels']['context-one'] == ['targetA', 'targetB']
    assert evaluation._plain({'metric': float('nan')}) == {'metric': None}


def test_both_scientific_runtimes_use_catalog_setup_and_no_base_environment_fallback(tmp_path):
    env = {'MICROMIND_CATALOG': str(tmp_path / 'unprepared-catalog')}
    for capability, owner in [('response-fit', 'matrix-cpu-runtime'), ('matrix-evaluate-cell', 'cell-evaluation-runtime')]:
        item = catalog.descriptor(capability, env)
        assert item['ready'] is False and item['setup'] == {'model': owner, 'command': 'model-install'}
        assert item['runtime']['owner'] == owner and len(item['runtime']['lockSha256']) == 64
        with pytest.raises(ValueError, match='prepared scientific runtime changed'):
            catalog.execution_runtime(item['runtime'], env)


def test_real_upstream_admission_rejects_partial_panel_before_scoring(tmp_path, monkeypatch):
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    upstream = pytest.importorskip('cell_eval2')
    from scipy import sparse
    monkeypatch.setattr(upstream, 'compute_metrics', lambda *a, **k: pytest.fail('Partial panels must not be scored'))
    var = pd.DataFrame(index=['g1', 'g2'])
    for name, targets in [('prediction', ['targetA'] * 2), ('reference', ['targetA', 'targetB', 'control'] * 2)]:
        obj = ad.AnnData(sparse.csr_matrix(np.ones((len(targets), 2), dtype=np.float32)),
                        obs=pd.DataFrame({'context': ['context-one'] * len(targets), 'target': targets},
                                         index=[f'{name}{i}' for i in range(len(targets))]), var=var)
        obj.write_h5ad(tmp_path / f'{name}.h5ad')
    inputs = {'prediction': tmp_path / 'prediction.h5ad', 'reference': tmp_path / 'reference.h5ad',
              'config': declared(tmp_path)}
    with pytest.raises(evaluation.Refused, match='complete declared panel'):
        evaluation.evaluate(inputs, {}, tmp_path, lambda: None)


def test_evaluation_preflight_counts_sparse_storage_and_dense_shape_without_allocating():
    from types import SimpleNamespace
    matrix = SimpleNamespace(dtype=np.dtype('float32'), shape=(200000, 20000))
    source = SimpleNamespace(X=matrix, n_vars=20000)
    assert evaluation._allocation_bytes(source, range(200000)) > evaluation.MATERIALIZATION_BUDGET
    group = {'data': SimpleNamespace(shape=(100,)), 'indices': SimpleNamespace(shape=(100,)),
             'indptr': SimpleNamespace(shape=(11,))}
    source.X = SimpleNamespace(group=group)
    assert evaluation._allocation_bytes(source, range(10)) < evaluation.MATERIALIZATION_BUDGET


def test_capability_setup_points_to_its_declared_runtime_owner(tmp_path):
    from orchestrator import readiness
    env = {'MICROMIND_CATALOG': str(tmp_path / 'unprepared-catalog')}
    for capability, owner in [('response-fit', 'matrix-cpu-runtime'), ('matrix-evaluate-cell', 'cell-evaluation-runtime')]:
        result = readiness.model(capability, {}, env)
        assert any(step.kind == 'install' and step.model == owner for step in result.steps)


def test_projected_reader_ignores_auxiliary_arrays_and_refuses_nonfinite_counts(tmp_path):
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas'); h5py = pytest.importorskip('h5py')
    path = tmp_path / 'counts.h5ad'
    obj = ad.AnnData(np.array([[1., np.nan], [2., 3.]], dtype=np.float32),
                    obs=pd.DataFrame({'context': ['context-one'] * 2, 'target': ['targetA'] * 2}, index=['c1', 'c2']),
                    var=pd.DataFrame(index=['g1', 'g2']))
    obj.write_h5ad(path)
    with h5py.File(path, 'r+') as source:
        # Sparse-on-disk allocation fixture, not a real biological annotation.
        source['obsm'].create_dataset('unused', shape=(2, 10**9), dtype='f8', chunks=(1, 128), fillvalue=0)
    config = evaluation.protocol(declared(tmp_path))
    with evaluation._counts(path, config) as source:
        assert list(source.var_names) == ['g1', 'g2']
        assert evaluation._allocation_bytes(source, range(2)) < 1024
        with pytest.raises(evaluation.Refused, match='finite'):
            evaluation._context(source, np.array([0, 1]), config)


@pytest.mark.parametrize('encoding', ['csr_matrix', 'csc_matrix'])
@pytest.mark.parametrize('malformation', ['huge-pointer', 'float-pointer', 'float-index'])
def test_sparse_layout_refuses_before_upstream_pointer_cache(tmp_path, monkeypatch, encoding, malformation):
    ad = pytest.importorskip('anndata'); h5py = pytest.importorskip('h5py')
    from scientific_compute import bounded_h5
    path = tmp_path / 'bad.h5ad'
    with h5py.File(path, 'w') as h:
        x = h.create_group('X'); x.attrs['encoding-type'] = encoding; x.attrs['shape'] = [2, 3]
        x.create_dataset('data', data=[1.])
        x.create_dataset('indices', data=np.array([0], dtype='f8' if malformation == 'float-index' else 'i8'))
        major = 2 if encoding == 'csr_matrix' else 3
        if malformation == 'huge-pointer':
            x.create_dataset('indptr', shape=(10**9,), chunks=(128,), dtype='i8')
        else:
            x.create_dataset('indptr', data=np.array([0] * major + [1], dtype='f8' if malformation == 'float-pointer' else 'i8'))
    monkeypatch.setattr(ad.io, 'sparse_dataset', lambda *a: pytest.fail('Must refuse before pointer caching'))
    with h5py.File(path, 'r') as h, pytest.raises(evaluation.Refused, match='pointer/index'):
        bounded_h5.matrix(h)


@pytest.mark.parametrize('kind', ['huge-categories', 'huge-fixed-width', 'oversized-vlen', 'invalid-category'])
def test_bounded_labels_refuse_predictable_decoder_allocations(tmp_path, kind):
    h5py = pytest.importorskip('h5py')
    from scientific_compute import bounded_h5
    with h5py.File(tmp_path / 'labels.h5', 'w') as h:
        if kind in ('huge-categories', 'invalid-category'):
            node = h.create_group('labels'); node.attrs['encoding-type'] = 'categorical'
            node.create_dataset('codes', data=np.array([3 if kind == 'invalid-category' else 0], dtype='i8'))
            if kind == 'huge-categories':
                node.create_dataset('categories', shape=(10**9,), chunks=(128,), dtype='S1')
            else:
                node.create_dataset('categories', data=np.array(['valid'], dtype=h5py.string_dtype()))
        elif kind == 'huge-fixed-width':
            node = h.create_dataset('labels', shape=(1,), dtype='S100000000')
        else:
            node = h.create_dataset('labels', data=np.array(['a' * 4096], dtype=h5py.string_dtype()))
        with pytest.raises(evaluation.Refused):
            bounded_h5.text(node, 1)


@pytest.mark.parametrize('pointers,indices', [([0, 2, 1, 2], [0, 1]), ([0, 3, 2, 2], [0, 1]),
                                            ([0, 1, 1, 2], [-1, 1]), ([0, 1, 1, 2], [0, 3])])
def test_sparse_interior_pointers_and_indices_refuse_before_native_decoding(tmp_path, monkeypatch, pointers, indices):
    ad = pytest.importorskip('anndata'); h5py = pytest.importorskip('h5py')
    from scientific_compute import bounded_h5
    with h5py.File(tmp_path / 'malformed.h5ad', 'w') as h:
        x = h.create_group('X'); x.attrs['encoding-type'] = 'csr_matrix'; x.attrs['shape'] = [3, 3]
        x.create_dataset('data', data=[1., 2.]); x.create_dataset('indices', data=indices)
        x.create_dataset('indptr', data=pointers)
        monkeypatch.setattr(ad.io, 'sparse_dataset', lambda *a: pytest.fail('Must refuse malformed data before native decoding'))
        with pytest.raises(evaluation.Refused, match='pointers|indices'):
            bounded_h5.matrix(h)


def test_sparse_validation_is_cancellable_before_native_decoding(tmp_path, monkeypatch):
    ad = pytest.importorskip('anndata'); h5py = pytest.importorskip('h5py')
    from scientific_compute import bounded_h5
    with h5py.File(tmp_path / 'stopped.h5ad', 'w') as h:
        x = h.create_group('X'); x.attrs['encoding-type'] = 'csr_matrix'; x.attrs['shape'] = [1, 1]
        x.create_dataset('data', data=[1.]); x.create_dataset('indices', data=[0]); x.create_dataset('indptr', data=[0, 1])
        monkeypatch.setattr(ad.io, 'sparse_dataset', lambda *a: pytest.fail('Stopped before decoding'))
        def stopped(): raise RuntimeError('stop requested')
        with pytest.raises(RuntimeError, match='stop requested'):
            bounded_h5.matrix(h, stopped)
