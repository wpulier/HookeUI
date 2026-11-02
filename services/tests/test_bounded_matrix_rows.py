"""Synthetic stored-entry budgets; no user data or scientific quality claims."""
import numpy as np
import pytest
from scipy import sparse

from scientific_compute import bounded_h5, engine


def test_wide_csr_reads_fit_existing_byte_limit_without_dropping_values(tmp_path):
    ad = pytest.importorskip('anndata'); h5py = pytest.importorskip('h5py')
    # Ordinary row width/density can exceed 64 MiB's conservative allocation
    # estimate even though each individual row fits comfortably.
    rows, stored_columns, columns = 128, 9000, 18080
    source = sparse.csr_matrix((np.ones(rows * stored_columns, dtype=np.float32),
        np.tile(np.arange(stored_columns), rows),
        np.arange(rows + 1) * stored_columns), shape=(rows, columns))
    path = tmp_path/'wide.h5ad'; ad.AnnData(source).write_h5ad(path)
    with h5py.File(path, 'r') as file:
        matrix = bounded_h5.matrix(file)
        with pytest.raises(engine.Refused, match='sparse row block'):
            bounded_h5.row_block(matrix, 0, rows, list(range(columns)))
        intervals = list(bounded_h5.row_ranges(matrix, rows))
        assert len(intervals) > 1
        blocks = [bounded_h5.row_block(matrix, a, b, list(range(columns))) for a, b in intervals]
    actual = sparse.vstack(blocks, format='csr')
    assert actual.shape == source.shape and (actual != source).nnz == 0
    assert intervals[0][0] == 0 and intervals[-1][1] == rows
    assert all(a[1] == b[0] for a, b in zip(intervals, intervals[1:]))


@pytest.mark.parametrize('storage', ['dense', 'csr'])
def test_empty_rows_and_cancellation_preserve_row_coverage(tmp_path, monkeypatch, storage):
    ad = pytest.importorskip('anndata'); h5py = pytest.importorskip('h5py')
    source = np.array([[0, 0], [1, 2], [0, 0], [3, 4], [0, 0]], dtype=np.float32)
    path = tmp_path/'rows.h5ad'
    ad.AnnData(sparse.csr_matrix(source) if storage == 'csr' else source).write_h5ad(path)
    monkeypatch.setattr(bounded_h5, 'BLOCK_BYTES', 2 * 64)
    with h5py.File(path, 'r') as file:
        matrix = bounded_h5.matrix(file)
        ranges = list(bounded_h5.row_ranges(matrix, 3))
        assert [i for a, b in ranges for i in range(a, b)] == list(range(5))
        got = sparse.vstack([sparse.csr_matrix(bounded_h5.row_block(matrix, a, b, [0, 1]))
                             for a, b in ranges]).toarray()
        np.testing.assert_array_equal(got, source)
        def stopped(): raise InterruptedError('cancelled')
        with pytest.raises(InterruptedError, match='cancelled'):
            list(bounded_h5.row_ranges(matrix, 3, stopped))
