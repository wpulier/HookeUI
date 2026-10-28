"""Projected H5AD access: validate allocation layout before decoding required data.

Only X and explicitly requested columns are read. Auxiliary layers/raw/embeddings
are not inputs. Label reads use bounded HDF5 conversions, including legacy
categoricals; arbitrary-width strings never reach a whole-array decoder.
"""
import numpy as np

if __package__:
    from .engine import Refused, MAX_ROWS, MAX_GENES
else:
    from engine import Refused, MAX_ROWS, MAX_GENES

METADATA_BYTES = 256 << 20
LABEL_BYTES = 1024  # at most 256 Unicode characters
SPARSE_CHUNK = 65536
BLOCK_BYTES = 64 << 20


def matrix(source, check=lambda: None):
    import h5py
    import anndata as ad
    node = source['X']
    if isinstance(node, h5py.Dataset):
        shape = node.shape
    else:
        shape = tuple(node.attrs.get('shape', ()))
    if (len(shape) != 2 or any(not isinstance(n, (int, np.integer)) for n in shape)
            or not 0 < shape[0] <= MAX_ROWS or not 0 < shape[1] <= MAX_GENES):
        raise Refused('The matrix exceeds its declared shape allowance')
    if isinstance(node, h5py.Group):
        encoding = node.attrs.get('encoding-type')
        if encoding not in ('csr_matrix', 'csc_matrix'):
            raise Refused('Unsupported sparse matrix encoding')
        for name in ('data', 'indices', 'indptr'):
            if name not in node or not isinstance(node[name], h5py.Dataset) or len(node[name].shape) != 1:
                raise Refused('Sparse matrix storage has inconsistent dimensions')
        nnz = node['data'].shape[0]
        major = shape[0] if encoding == 'csr_matrix' else shape[1]
        if (node['indices'].shape != (nnz,) or node['indptr'].shape != (major + 1,)
                or node['indices'].dtype.kind not in 'iu' or node['indptr'].dtype.kind not in 'iu'):
            raise Refused('Sparse matrix pointer/index layout is inconsistent')
        if nnz * (node['data'].dtype.itemsize + node['indices'].dtype.itemsize) > 64 << 30:
            raise Refused('Declared sparse storage exceeds the retained-file allowance')
        if node['indptr'][0] != 0 or node['indptr'][-1] != nnz:
            raise Refused('Sparse matrix pointer endpoints disagree with storage')
        previous = 0
        for start in range(0, major + 1, SPARSE_CHUNK):
            check(); pointers = node['indptr'][start:start + SPARSE_CHUNK]
            if (np.any(pointers < previous) or np.any(pointers > nnz)
                    or np.any(pointers[1:] < pointers[:-1])):
                raise Refused('Sparse matrix pointers must be monotone within stored entries')
            previous = int(pointers[-1])
        minor = shape[1] if encoding == 'csr_matrix' else shape[0]
        for start in range(0, nnz, SPARSE_CHUNK):
            check(); indices = node['indices'][start:start + SPARSE_CHUNK]
            if np.any(indices < 0) or np.any(indices >= minor):
                raise Refused('Sparse matrix indices exceed their declared axis')
        dtype = node['data'].dtype
        if dtype.kind not in 'iuf':
            raise Refused('A real numeric matrix is required')
        return ad.io.sparse_dataset(node)
    if node.dtype.kind not in 'iuf':
        raise Refused('A real numeric matrix is required')
    return node


def row_block(matrix, start, stop, columns):
    """Read a CSR row interval directly; avoid AnnData fancy-index full loads."""
    from scipy import sparse
    if not hasattr(matrix, 'group'):
        return np.asarray(matrix[start:stop, columns])
    group = matrix.group
    if group.attrs.get('encoding-type') != 'csr_matrix':
        raise Refused('Bounded aggregate preparation supports dense or CSR matrices; CSC needs an explicitly prepared conversion')
    pointers = group['indptr'][start:stop + 1]
    begin, end = int(pointers[0]), int(pointers[-1])
    # Include data/index copies and sparse column-selection intermediates.
    if (end - begin) * max(16, group['data'].dtype.itemsize + group['indices'].dtype.itemsize) * 4 > BLOCK_BYTES:
        raise Refused('The sparse row block exceeds its bounded allocation allowance')
    block = sparse.csr_matrix((group['data'][begin:end], group['indices'][begin:end], pointers - begin),
                              shape=(stop - start, matrix.shape[1]))
    return block[:, columns]


def numeric(node, length, *, integer=False):
    import h5py
    if (not isinstance(node, h5py.Dataset) or node.shape != (length,)
            or node.dtype.kind not in ('iu' if integer else 'biuf')
            or node.dtype.itemsize * length > METADATA_BYTES):
        raise Refused('Metadata has an unsupported numeric layout or allocation')
    return node[:]


def text(node, length=None, depth=0):
    """Read only a bounded string axis; refuse missing/oversized labels."""
    import h5py
    if depth > 2:
        raise Refused('Unsupported nested label encoding')
    if isinstance(node, h5py.Group):
        if node.attrs.get('encoding-type') == 'nullable-string-array' and set(node.keys()) == {'values', 'mask'}:
            values = text(node['values'], length, depth + 1)
            mask = numeric(node['mask'], len(values))
            if mask.dtype.kind != 'b' or mask.any():
                raise Refused('Required labels cannot be missing')
            return values
        if node.attrs.get('encoding-type') != 'categorical' or set(node.keys()) != {'codes', 'categories'}:
            raise Refused('Unsupported label encoding')
        categories, codes = node['categories'], node['codes']
    elif 'categories' in node.attrs:  # legacy AnnData categorical dataset
        categories, codes = node.file[node.attrs['categories']], node
    else:
        categories = codes = None
    if categories is not None:
        if len(codes.shape) != 1 or (length is not None and codes.shape != (length,)):
            raise Refused('Label axis has an inconsistent shape')
        values = text(categories, depth=depth + 1)
        ids = numeric(codes, codes.shape[0], integer=True)
        if not len(values) or np.any(ids < 0) or np.any(ids >= len(values)):
            raise Refused('Categorical labels contain missing or invalid codes')
        _text_budget(len(ids), max(map(len, values)))
        return values[ids]
    if (not isinstance(node, h5py.Dataset) or len(node.shape) != 1 or node.shape[0] > MAX_ROWS
            or (length is not None and node.shape != (length,))
            or h5py.check_string_dtype(node.dtype) is None):
        raise Refused('Label axis has an unsupported shape or type')
    if node.dtype.kind == 'S' and node.dtype.itemsize > LABEL_BYTES:
        raise Refused('Label storage exceeds the bounded string width')
    values = []; width = 0
    for start in range(0, node.shape[0], 256):
        # Convert in HDF5, rather than first allocating an unbounded vlen array.
        raw = node.astype(f'S{LABEL_BYTES + 1}')[start:start + 256]
        for item in raw:
            if len(item) > LABEL_BYTES:
                raise Refused('A label exceeds its bounded string width')
            value = bytes(item).decode('utf-8')
            if not value or len(value) > 256:
                raise Refused('Labels must be nonempty text of at most 256 characters')
            values.append(value); width = max(width, len(value))
        _text_budget(node.shape[0], width)
    return np.asarray(values, dtype=str)


def _text_budget(length, width):
    if length * max(1, width) * 4 * 3 + length * 64 > METADATA_BYTES:
        raise Refused('Decoded labels exceed the metadata allocation allowance')
