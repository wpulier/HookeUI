"""Stream a whole-count prediction (three contexts x every panel target x N cells) from per-target factors.

The non-learned emitter of the VCC 2026 entries (Track E `generate.py`, 5 Nov 2026), unchanged in
its arithmetic and random-number order, so the same factors, controls and seed give the same cells:
each predicted cell is a control cell of the same context, resampled with replacement, keeping its
library depth; counts move by the factor exactly in expectation, Binomial(c, f) down and
c + Poisson(c (f - 1)) up, so dispersion stays that of real cells.

Written context by context straight into the H5AD's CSR arrays (float32 counts, int32 gene indices,
int64 row pointers): no dense matrix, no second copy, and no worst-case dense disk reservation.
Disk is admitted against the controls' own density, the most stored entries the emitter can write
(a resampled cell never gains genes it had none of).
"""
import shutil

import numpy as np

CHUNK = 1 << 20


class Refused(ValueError):
    pass


def _column(path, name):
    import pandas as pd
    frame = pd.read_csv(path)
    if name not in frame or frame[name].isna().any():
        raise Refused(f'The CSV needs a complete {name} column')
    values = frame[name].astype(str).tolist()
    if len(set(values)) != len(values):
        raise Refused(f'{name} names each entry once')
    return values


def _controls(path, context, genes, column):
    import anndata as ad
    import scipy.sparse as sp
    C = ad.read_h5ad(path)
    if list(C.var_names) != genes:
        raise Refused(f'The {context} controls must use the exact ordered gene axis')
    if column not in C.obs or not (C.obs[column].astype(str) == context).all():
        raise Refused(f'The {context} controls file must hold only {context} cells in obs[{column!r}]')
    X = sp.csr_matrix(C.X, dtype=np.float32)
    if X.shape[0] < 1 or not np.isfinite(X.data).all() or (X.data < 0).any() or (X.data != np.rint(X.data)).any():
        raise Refused(f'The {context} controls must be raw whole counts')
    return X


def run(inputs, params, output, check):
    import anndata as ad
    import h5py
    import pandas as pd
    import scipy.sparse as sp
    contexts = [c.strip() for c in params['contexts'].split(',') if c.strip()]
    if len(contexts) != 3 or len(set(contexts)) != 3:
        raise Refused('Name the three contexts, in the order of the three control inputs (D,E,F)')
    cells_per, seed, column = int(params['cells_per_target']), int(params['seed']), params['context_column']
    genes = _column(inputs['genes'], 'gene_name')
    targets = _column(inputs['perts'], 'target_gene')
    with np.load(inputs['factors'], allow_pickle=False) as source:
        if set(source.files) != set(contexts):
            raise Refused(f'The factors name contexts {sorted(source.files)}, not {contexts}')
        if any(source[c].shape != (len(targets), len(genes)) or source[c].dtype != np.float32 for c in contexts):
            raise Refused('Factors are float32, one row per panel target and one column per gene')
        factors = {c: source[c] for c in contexts}
    if any(not np.isfinite(f).all() or (f < 0).any() for f in factors.values()):
        raise Refused('Factors must be finite and nonnegative')
    ports = ('controls_1', 'controls_2', 'controls_3')
    n_cells = len(contexts) * len(targets) * cells_per
    # Admission: the most entries this can store is cells x the densest control cell; plan on the mean.
    densest, mean = 0, 0.0
    for port, context in zip(ports, contexts):
        X = _controls(inputs[port], context, genes, column)
        per = np.diff(X.indptr)
        densest, mean = max(densest, int(per.max())), max(mean, float(per.mean()))
        del X
    expected = int(n_cells * mean * 8 + (n_cells + 1) * 8)
    if shutil.disk_usage(output).free < expected * 1.25 + (1 << 30):
        raise Refused(f'Reserve about {expected * 1.25 / 2**30:.1f} GiB for the emitted counts (measured control density)')
    check()
    rng = np.random.default_rng(seed)
    obs = pd.DataFrame({
        'target_gene': np.tile(np.repeat(targets, cells_per), len(contexts)),
        'context': np.repeat(contexts, len(targets) * cells_per),
    }, index=pd.Index([f'cell_{i:06d}' for i in range(n_cells)], dtype=object))
    obs['target_gene'] = obs['target_gene'].astype('category')
    obs['context'] = obs['context'].astype('category')
    path = output / 'prediction.h5ad'
    shell = ad.AnnData(X=sp.csr_matrix((n_cells, len(genes)), dtype=np.float32), obs=obs,
                       var=pd.DataFrame(index=pd.Index(genes, dtype=object)))
    # Object-dtype names keep AnnData's plain string-array encoding (pandas 3 would otherwise write
    # nullable strings, which older readers of a submitted entry do not expect).
    shell.write_h5ad(path)
    per_context = {}
    with h5py.File(path, 'r+') as h:
        del h['X']
        g = h.create_group('X')
        g.attrs['encoding-type'] = 'csr_matrix'
        g.attrs['encoding-version'] = '0.1.0'
        g.attrs['shape'] = (n_cells, len(genes))
        data = g.create_dataset('data', (0,), maxshape=(None,), dtype=np.float32, chunks=(CHUNK,))
        idx = g.create_dataset('indices', (0,), maxshape=(None,), dtype=np.int32, chunks=(CHUNK,))
        indptr = [0]
        nnz = 0
        for port, context in zip(ports, contexts):
            Xc = _controls(inputs[port], context, genes, column)
            Fc = factors[context]
            for ti in range(len(targets)):
                if ti % 16 == 0:
                    check()
                rows = rng.integers(0, Xc.shape[0], cells_per)
                sub = Xc[rows]
                c = np.rint(sub.data).astype(np.int64)
                f = Fc[ti][sub.indices].astype(np.float64)
                new = rng.binomial(c, np.clip(f, 0, 1)) + rng.poisson(c * np.clip(f - 1, 0, None))
                sub = sp.csr_matrix((new.astype(np.float32), sub.indices, sub.indptr), shape=sub.shape)
                sub.eliminate_zeros()
                n = sub.nnz
                data.resize((nnz + n,))
                idx.resize((nnz + n,))
                data[nnz:] = sub.data
                idx[nnz:] = sub.indices
                indptr.extend((sub.indptr[1:] + nnz).tolist())
                nnz += n
            per_context[context] = {'controlCells': int(Xc.shape[0]), 'storedEntriesSoFar': nnz}
            del Xc
        g.create_dataset('indptr', data=np.array(indptr, dtype=np.int64))
    return path, {'format': 'vcc-count-emission-v1', 'shape': [n_cells, len(genes)], 'storedEntries': nnz,
                  'contexts': per_context, 'cellsPerTarget': cells_per, 'seed': seed,
                  'admittedDiskBytes': expected, 'densestControlCellEntries': densest,
                  'meaning': 'resampled control cells moved by the factors in expectation; a statistical emitter, not a learned simulator'}
