"""Sparse-index disk refusals; synthetic metadata, not full-density qualification."""
from contextlib import contextmanager
import json
from types import SimpleNamespace

import numpy as np
import pytest

from scientific_compute import engine
from scientific_compute.catalog import parameters


def test_sparse_payload_accounts_for_int64_index_transition():
    # No arrays with billions of entries are allocated.
    assert engine._count_output_bytes(1, 2 ** 31 - 1) == 17_179_869_192
    assert engine._count_output_bytes(1, 2 ** 31) == 25_769_803_792
    assert engine._count_output_bytes(120_000, 18_533) == 26_688_480_008
    assert engine._count_output_bytes(360_000, 18_533) == 80_065_440_008
    assert engine._count_output_bytes(3, 2) == 80  # Existing small-output allowance.


def test_prediction_refuses_old_eight_byte_disk_envelope_before_emission(tmp_path, monkeypatch):
    pytest.importorskip('anndata')
    genes = np.array([f'g{i}' for i in range(18_533)])
    controls = SimpleNamespace(n_vars=len(genes), var_names=genes)
    @contextmanager
    def source(_):
        yield controls
    monkeypatch.setattr(engine, '_adata', source)
    monkeypatch.setattr(engine, '_columns', lambda *_: (np.array(['D']), np.array(['non-targeting'])))
    monkeypatch.setattr(engine, '_control_moments', lambda *_: pytest.fail('Disk refusal must precede emission'))
    model = tmp_path / 'model.npz'
    np.savez_compressed(model, format=np.array('response-model-v1'), effects=np.zeros((1, 1)),
                        genes=np.array(['g0']), targets=np.array(['t0']), metadata=np.array('{}'))
    panel = tmp_path / 'panel.json'; panel.write_text(json.dumps([f't{i}' for i in range(300)]))
    folder = tmp_path / 'output'; folder.mkdir()
    monkeypatch.setattr(engine.shutil, 'disk_usage', lambda _: SimpleNamespace(free=36_122_150_929))
    with pytest.raises(engine.Refused, match='Free space for sparse prediction'):
        engine.predict({'model': model, 'panel': panel, 'controls': tmp_path / 'metadata-only'},
                       parameters('response-predict', {'cells_per_target': 400}), folder, lambda: None)
    assert not list(folder.iterdir())


def test_assembly_refuses_old_disk_envelope_before_reading_count_blocks(tmp_path, monkeypatch):
    anndata = pytest.importorskip('anndata')
    import pandas as pd
    from scipy import sparse
    from scientific_compute import matrix_assembly, bounded_h5
    inputs = {}
    # Zero stored values: exercise real large-shape metadata without a large matrix.
    for side in ('left', 'right'):
        path = tmp_path / f'{side}.h5ad'
        anndata.AnnData(sparse.csr_matrix((60_000, 18_533), dtype=np.float32),
            obs=pd.DataFrame({'context': [side] * 60_000, 'target_gene': ['t'] * 60_000},
                             index=[f'{side}{i}' for i in range(60_000)]),
            var=pd.DataFrame(index=[f'g{i}' for i in range(18_533)])).write_h5ad(path)
        inputs[side] = path
    folder = tmp_path / 'output'; folder.mkdir()
    monkeypatch.setattr(engine.shutil, 'disk_usage', lambda _: SimpleNamespace(free=36_122_150_929))
    monkeypatch.setattr(bounded_h5, 'row_block', lambda *_: pytest.fail('Disk refusal must precede count reads'))
    with pytest.raises(engine.Refused, match='Free space for count assembly'):
        matrix_assembly.concatenate(inputs, parameters('matrix-concatenate', {}), folder, lambda: None)
    assert not list(folder.iterdir())
