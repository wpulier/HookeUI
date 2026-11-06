"""An .h5ad's annotations and counts read with h5py alone: no AnnData object, nothing written, X in row blocks.

Kept in each folder that reads .h5ad files (a folder capability versions only itself, ADR 0031): `target-overlap`
and `profile-match` hold the same file.
Reads AnnData's current encodings (categorical groups, string arrays, nullable strings and numbers) and the 0.7 one
(`__categories`). An obs stored as an old compound table is refused rather than guessed.
"""
import h5py
import numpy as np

ROWS = 2048  # rows of X read at a time


class Refused(ValueError):
    pass


def _strings(dataset):
    if isinstance(dataset, h5py.Group):  # AnnData 0.11+: a nullable string array is values plus a mask
        kind = dataset.attrs.get('encoding-type')
        kind = kind.decode() if isinstance(kind, bytes) else kind
        if kind != 'nullable-string-array':
            raise Refused(f'A text column is stored as {kind!r}, which this capability does not read')
        values = _strings(dataset['values'])
        values[dataset['mask'][...].astype(bool)] = None
        return values
    if h5py.check_string_dtype(dataset.dtype) is not None or dataset.dtype.kind == 'O':
        return np.asarray(dataset.asstr()[...], dtype=object)
    if dataset.dtype.kind == 'S':
        return np.asarray([v.decode('utf-8', 'replace') for v in dataset[...]], dtype=object)
    return dataset[...]


def frame(file, name):
    item = file.get(name)
    if item is None:
        raise Refused(f'This .h5ad has no {name}')
    if not isinstance(item, h5py.Group):
        raise Refused(f'This .h5ad stores {name} as an old compound table; save it again with a current AnnData')
    return item


def index(group):
    key = group.attrs.get('_index', '_index')
    key = key.decode() if isinstance(key, bytes) else str(key)
    if key not in group:
        raise Refused('This .h5ad has no row names')
    return _strings(group[key])


def columns(group):
    order = group.attrs.get('column-order')
    if order is None:
        return sorted(k for k in group if k not in ('_index', '__categories'))
    return [v.decode() if isinstance(v, bytes) else str(v) for v in np.atleast_1d(order)]


def column(group, name):
    """One column: text labels (None where missing) or numbers (NaN where missing)."""
    if type(name) is not str or name not in group or name in ('__categories',):
        raise Refused(f'There is no column {name!r} (columns: {", ".join(columns(group)[:20])})')
    item = group[name]
    if isinstance(item, h5py.Group):
        kind = item.attrs.get('encoding-type')
        kind = kind.decode() if isinstance(kind, bytes) else kind
        if kind == 'categorical':
            codes, categories = item['codes'][...].astype(np.int64), _strings(item['categories'])
            out = np.full(len(codes), None, dtype=object)
            out[codes >= 0] = categories[codes[codes >= 0]]
            return out
        if kind in ('nullable-integer', 'nullable-float', 'nullable-boolean'):
            values = item['values'][...].astype(np.float64)
            values[item['mask'][...].astype(bool)] = np.nan
            return values
        raise Refused(f'Column {name!r} is stored as {kind!r}, which this capability does not read')
    values = _strings(item)
    legacy = group.get('__categories')
    if legacy is not None and name in legacy:
        categories = _strings(legacy[name])
        codes = np.asarray(values, dtype=np.int64)
        out = np.full(len(codes), None, dtype=object)
        out[codes >= 0] = categories[codes[codes >= 0]]
        return out
    return values


def shape(file):
    x = file.get('X')
    if x is None:
        raise Refused('This .h5ad has no X matrix')
    if isinstance(x, h5py.Dataset):
        return tuple(int(v) for v in x.shape), 'dense'
    kind = x.attrs.get('encoding-type') or x.attrs.get('h5sparse_format')
    kind = kind.decode() if isinstance(kind, bytes) else str(kind)
    size = x.attrs.get('shape', x.attrs.get('h5sparse_shape'))
    if size is None or kind not in ('csr_matrix', 'csc_matrix', 'csr', 'csc'):
        raise Refused('X is neither a dense, CSR nor CSC matrix')
    return (int(size[0]), int(size[1])), kind[:3]


def column_sums(file, selected, check=lambda: None):
    """The sum of X over the selected rows, one value per column (float64); X is read a block of rows at a time."""
    (rows, genes), kind = shape(file)
    if len(selected) != rows:
        raise Refused('The row selection does not match X')
    x, total = file['X'], np.zeros(genes, dtype=np.float64)
    if kind == 'dense':
        for start in range(0, rows, ROWS):
            check()
            keep = selected[start:start + ROWS]
            if keep.any():
                total += np.asarray(x[start:start + ROWS], dtype=np.float64)[keep].sum(axis=0)
        return total
    indptr = x['indptr'][...].astype(np.int64)
    if kind == 'csr':
        for start in range(0, rows, ROWS):
            check()
            end = min(rows, start + ROWS)
            keep = selected[start:end]
            if not keep.any():
                continue
            a, b = indptr[start], indptr[end]
            data, cols = x['data'][a:b].astype(np.float64), x['indices'][a:b]
            row = np.repeat(np.arange(end - start), np.diff(indptr[start:end + 1]))
            mask = keep[row]
            total += np.bincount(cols[mask], weights=data[mask], minlength=genes)
        return total
    for start in range(0, genes, ROWS):  # CSC: blocks of columns, rows chosen inside each
        check()
        end = min(genes, start + ROWS)
        a, b = indptr[start], indptr[end]
        data, row = x['data'][a:b].astype(np.float64), x['indices'][a:b]
        col = np.repeat(np.arange(end - start), np.diff(indptr[start:end + 1]))
        mask = selected[row]
        total[start:end] += np.bincount(col[mask], weights=data[mask], minlength=end - start)
    return total
