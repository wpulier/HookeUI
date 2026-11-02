"""Synthetic final-format and refusal checks; no biological run or submission."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace

import numpy as np
import pytest
from scipy import sparse

from scientific_compute import engine, vcc_packaging as packaging, resource_profile


def matrix(path, genes, targets, count):
    ad = pytest.importorskip('anndata'); pd = pytest.importorskip('pandas')
    labels = np.tile(np.repeat(targets, count), 3)
    contexts = np.repeat(['D', 'E', 'F'], len(targets) * count)
    rows = len(labels)
    data = sparse.csr_matrix((np.ones(rows, dtype=np.float32), (np.arange(rows), np.zeros(rows, dtype=np.int32))), shape=(rows, len(genes)))
    ad.AnnData(data, obs=pd.DataFrame({'context': contexts, 'target_gene': labels},
        index=[f'cell{i}' for i in range(rows)]), var=pd.DataFrame(index=genes)).write_h5ad(path)
    return path


@pytest.fixture
def tiny(tmp_path, monkeypatch):
    monkeypatch.setattr(packaging, 'CELLS', 2)
    path = matrix(tmp_path / 'counts.h5ad', ['g1', 'g2'], ['t1'], 2)
    return path


def test_sparse_materialization_envelope_uses_actual_64_bit_index_cost():
    node = {'data': SimpleNamespace(shape=(4_750_000_000,)),
            'indices': SimpleNamespace(dtype=np.dtype('i4')),
            'indptr': SimpleNamespace(dtype=np.dtype('i8'))}
    result = packaging._envelope(node, 360000)
    assert result['csrPayloadBytes'] == 57_002_880_008
    assert 80 << 30 < result['estimatedPeakMemoryBytes'] < 96 << 30
    assert result['storedEntries'] == 4_750_000_000


def test_independent_count_hashes_and_nnz(tiny, tmp_path):
    result = packaging.validate(tiny, ['g1', 'g2'], ['t1'], tmp_path, lambda: None)
    assert result['shape'] == [6, 2] and result['storedEntries'] == 6
    assert result['maximumCellCounts'] == 1
    assert len(set(result['canonicalCountHashes'])) == 3


@pytest.mark.parametrize('fault', ['fraction', 'negative', 'nonfinite', 'depth', 'zero', 'target', 'context', 'duplicate-cell', 'gene-order', 'extra-array', 'external-link'])
def test_invalid_input_refuses_independently(tiny, tmp_path, fault):
    import h5py
    ad = pytest.importorskip('anndata')
    if fault == 'external-link':
        with h5py.File(tiny, 'r+') as file: file['extra'] = h5py.ExternalLink('/not-permitted', '/')
    else:
        data = ad.read_h5ad(tiny)
        if fault == 'fraction': data.X.data[0] = .5
        if fault == 'negative': data.X.data[0] = -1
        if fault == 'nonfinite': data.X.data[0] = np.nan
        if fault == 'depth': data.X.data[0] = 1_000_001
        if fault == 'zero': data.X.data[0] = 0
        if fault == 'target': data.obs['target_gene'] = ['non-targeting'] * 6
        if fault == 'context': data.obs['context'] = ['A'] * 6
        if fault == 'duplicate-cell': data.obs_names = ['duplicate'] * 6
        if fault == 'gene-order': data.var_names = ['g2', 'g1']
        if fault == 'extra-array': data.layers['extra'] = data.X.copy()
        data.write_h5ad(tiny)
    with pytest.raises(engine.Refused): packaging.validate(tiny, ['g1', 'g2'], ['t1'], tmp_path, lambda: None)


@pytest.mark.parametrize('boundary', ['memory', 'disk', 'density'])
def test_refusal_precedes_upstream_materialization(tiny, tmp_path, monkeypatch, boundary):
    ad = pytest.importorskip('anndata')
    monkeypatch.setattr(ad, 'read_h5ad', lambda *_: pytest.fail('No whole input may materialize'))
    if boundary == 'memory':
        monkeypatch.setattr(resource_profile, 'current', lambda: {**resource_profile.DEFAULT, 'materialization_bytes': 1})
    if boundary == 'disk': monkeypatch.setattr(packaging.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    if boundary == 'density': monkeypatch.setattr(packaging, 'MAX_NNZ', 5)
    with pytest.raises(engine.Refused): packaging.validate(tiny, ['g1', 'g2'], ['t1'], tmp_path, lambda: None)


def test_catalog_requires_separate_official_runtime(tmp_path):
    from scientific_compute import catalog
    from orchestrator import readiness
    env = {'MICROMIND_CATALOG': str(tmp_path / 'catalog')}
    assert catalog.runtime('matrix-package-vcc', env)['owner'] == 'vcc-packaging-runtime'
    result = readiness.model('matrix-package-vcc', {}, env)
    assert any(step.kind == 'install' and step.model == 'vcc-packaging-runtime' for step in result.steps)
    assert set(engine.OPERATIONS) == set(catalog.IDS)


@pytest.mark.parametrize('fault', ['upstream-failure', 'changed-packaged-counts'])
def test_packaging_failure_restores_temporary_scope_and_refuses_corruption(tiny, tmp_path, monkeypatch, fault):
    prep = pytest.importorskip('vcc.prep')
    inputs = {'matrix': tiny}
    for key, body in [('genes', 'gene_name\ng1\ng2\n'), ('perts', 'target_gene\nt1\n'), ('manifest', '{}')]:
        inputs[key] = tmp_path / (key + '.txt'); inputs[key].write_text(body)
    # Tiny internal corruption oracle. Public package admission retains its fixed
    # final dimensions; the separate complete-shape test runs official defaults.
    monkeypatch.setattr(packaging, '_panel', lambda _: (['g1', 'g2'], ['t1'], {'panel_id': 'vcc2026-test-1'}))
    original = prep.run_prep
    if fault == 'upstream-failure':
        def run(**kwargs): raise RuntimeError('synthetic upstream failure')
        expected = RuntimeError
    else:
        def run(**kwargs): return original(**kwargs, cells_per_pert=2, expected_gene_dim=2)
        writer = prep._write_vcc
        def corrupt(matrix, *args, **kwargs):
            matrix.X.data[0] += 1
            return writer(matrix, *args, **kwargs)
        monkeypatch.setattr(prep, '_write_vcc', corrupt)
        expected = engine.Refused
    monkeypatch.setattr(prep, 'run_prep', run)
    output = tmp_path / 'output'; output.mkdir()
    before_tmp, before_env = tempfile.tempdir, os.environ.get('TMPDIR')
    with pytest.raises(expected): packaging.package(inputs, {}, output, lambda: None)
    assert tempfile.tempdir == before_tmp and os.environ.get('TMPDIR') == before_env
    assert not any(path.is_dir() for path in output.iterdir())


def test_official_complete_shape_package_roundtrip_format_only(tmp_path, monkeypatch):
    prep = pytest.importorskip('vcc.prep')
    from importlib.metadata import version
    if version('vcc-cli') != '0.3.0': pytest.skip('Pinned official runtime required')
    genes = [f'g{i}' for i in range(18533)]; targets = [f't{i}' for i in range(300)]
    inputs = {'matrix': matrix(tmp_path / 'counts.h5ad', genes, targets, 400)}
    for key, header, values in [('genes', 'gene_name', genes), ('perts', 'target_gene', targets)]:
        inputs[key] = tmp_path / f'{key}.csv'; inputs[key].write_text(header + '\n' + '\n'.join(values) + '\n')
    manifest = {'season': '2026', 'partition': 'test', 'panel_id': 'vcc2026-test-1',
                'contexts': ['D', 'E', 'F'], 'pert_col': 'target_gene', 'context_col': 'context',
                'control_label': 'non-targeting', 'n_genes': 18533, 'n_constructs': 300, 'cells_per_pert': 400}
    inputs['manifest'] = tmp_path / 'manifest.json'; inputs['manifest'].write_text(json.dumps(manifest))
    original = prep.run_prep; called = []
    output = tmp_path / 'output'; output.mkdir()
    before_tmp, before_env = tempfile.tempdir, os.environ.get('TMPDIR')
    def official(**kwargs):
        assert Path(tempfile.gettempdir()).is_relative_to(output)
        assert os.environ['TMPDIR'] == tempfile.gettempdir()
        assert 'dry_run' not in kwargs and kwargs['required_contexts'] == ('D', 'E', 'F')
        called.append(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(prep, 'run_prep', official)
    path, result = packaging.package(inputs, {}, output, lambda: None)
    assert called and path.suffix == '.vcc' and path.stat().st_size > 0
    assert result['validation']['shape'] == [360000, 18533]
    assert result['validation']['storedEntries'] == 360000
    assert result['independentlyCheckedPackagedCounts'] and result['packagedForSubmission']
    assert not result['submitted'] and not result['organizerAccepted'] and not result['fullShapeResourceQualified']
    assert tempfile.tempdir == before_tmp and os.environ.get('TMPDIR') == before_env
    assert list(output.iterdir()) == [path]
