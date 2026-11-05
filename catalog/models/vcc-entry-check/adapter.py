"""Independent integrity check of an emitted entry against the factors it was made from (Track E `check.py`).

Reads the entry from disk, never the emitter's memory:
- labels: three context blocks, every panel target x N cells in panel order, the exact gene axis;
- counts: finite, whole, nonnegative, every cell total within the limit (sampled targets);
- effect: for sampled targets, log(mean predicted / mean control) over well-expressed genes
  correlates with the log factor. The correlations are reported, not judged: they are a sanity
  check that the factors reached the counts, not a measure of predictive quality.
A structural failure refuses, so nothing downstream runs on a broken entry.
"""
import json

import numpy as np


class Refused(ValueError):
    pass


def _column(path, name):
    import pandas as pd
    frame = pd.read_csv(path)
    if name not in frame or frame[name].isna().any():
        raise Refused(f'The CSV needs a complete {name} column')
    return frame[name].astype(str).tolist()


def _rows(h, start, stop, n_genes):
    import scipy.sparse as sp
    ip = h['X/indptr'][start:stop + 1]
    d = h['X/data'][ip[0]:ip[-1]]
    i = h['X/indices'][ip[0]:ip[-1]]
    return sp.csr_matrix((d, i, ip - ip[0]), shape=(stop - start, n_genes))


def run(inputs, params, output, check):
    import anndata as ad
    import h5py
    contexts = [c.strip() for c in params['contexts'].split(',') if c.strip()]
    if len(contexts) != 3:
        raise Refused('Name the three contexts, in the order of the three control inputs')
    per, sampled, limit = int(params['cells_per_target']), int(params['sampled_targets']), float(params['max_cell_total'])
    genes = _column(inputs['genes'], 'gene_name')
    targets = _column(inputs['perts'], 'target_gene')
    a = ad.read_h5ad(inputs['entry'], backed='r')
    try:
        block = len(targets) * per
        if a.shape != (len(contexts) * block, len(genes)):
            raise Refused(f'The entry is {a.shape}, not {(len(contexts) * block, len(genes))}')
        if list(a.var_names) != genes:
            raise Refused('The entry does not use the exact ordered gene axis')
        if not (a.obs['target_gene'].astype(str).to_numpy() == np.tile(np.repeat(targets, per), len(contexts))).all():
            raise Refused('Target labels are not every panel target x N cells in panel order')
        if not (a.obs['context'].astype(str).to_numpy() == np.repeat(contexts, block)).all():
            raise Refused('Context labels are not the three context blocks in order')
    finally:
        a.file.close()
    check()
    with np.load(inputs['factors'], allow_pickle=False) as source:
        factors = {c: source[c] for c in contexts}
    rng = np.random.default_rng(int(params['seed']))
    report = {}
    with h5py.File(inputs['entry'], 'r') as h:
        for ci, (context, port) in enumerate(zip(contexts, ('controls_1', 'controls_2', 'controls_3'))):
            control = ad.read_h5ad(inputs[port]).X
            cmean = np.asarray(control.mean(0)).ravel()
            cors, picked = [], []
            for ti in rng.choice(len(targets), sampled, replace=False):
                check()
                s = ci * block + ti * per
                X = _rows(h, s, s + per, len(genes))
                d = X.data
                if not (np.isfinite(d).all() and (d >= 0).all() and (d == np.rint(d)).all()):
                    raise Refused(f'{context}/{targets[ti]}: counts are not finite nonnegative whole numbers')
                if X.sum(1).max() > limit:
                    raise Refused(f'{context}/{targets[ti]}: a cell total exceeds {limit:g}')
                pm = np.asarray(X.mean(0)).ravel()
                g = cmean > 0.5
                lf = np.log(factors[context][ti][g])
                picked.append(targets[ti])
                cors.append(float(np.corrcoef(np.log((pm[g] + 0.01) / (cmean[g] + 0.01)), lf)[0, 1]) if lf.std() > 0 else None)
            report[context] = {'sampledTargets': picked, 'effectCorrelation': [None if c is None else round(c, 3) for c in cors]}
    path = output / 'entry-check.json'
    value = {'format': 'vcc-entry-check-v1', 'passed': True, 'shape': [len(contexts) * block, len(genes)],
             'contexts': report, 'maxCellTotal': limit,
             'meaning': 'structure and sampled count checks passed; correlations show the factors reached the counts, not predictive quality'}
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False))
    return path, value
