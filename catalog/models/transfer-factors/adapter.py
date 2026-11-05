"""Per-target multiplicative expression factors transferred from one screen's effects.

The model of the VCC 2026 entries m1, C2 and C3 (Track E `build_factors.py`, campaign `c3.py`):

  Lhat_t = mu + w_t (L_t - mu),  w_t = cells_t / (cells_t + k)   [rank r > 0: truncated SVD of the shrunk part]
  m      = mean of Lhat over the panel's targets the screen covers   (the panel-mean shift)
  log f_t(a) = a * m + s * (Lhat_t - m)   for covered targets,  a * m  for the others
  f_t(a) = exp(clip(log f_t(a), low, high)), kept at float32

Genes the screen did not measure keep factor 1. The same factors are written for every context.

Per-target strength weight (`strength_weight = "saturating"`): a_t = a * n_t / (n_t + half), with
n_t = strength_t * scale / strength_cells_t (the screen's DE count per `scale` cells); targets the
screen lacks get the median n_t of the covered panel targets. As C3 was built, the result is the
log-space interpolation between the float32 factor sets at shift 0 and at shift a:
  f_t = exp(log f_t(0) + (n_t / (n_t + half)) (log f_t(a) - log f_t(0)))
This is not bit-identical to evaluating a_t directly in one pass (float32 rounding of the two ends).
"""
import json
import zipfile

import numpy as np

KEYS = ('targets', 'L', 'cells', 'axis_idx', 'strength', 'strength_cells', 'axis_length')
MAX_BYTES = 4 << 30


class Refused(ValueError):
    pass


def _effects(path):
    with zipfile.ZipFile(path) as archive:
        names = {i.filename for i in archive.infolist()}
        if sum(i.file_size for i in archive.infolist()) > MAX_BYTES:
            raise Refused('The effects file exceeds its 4 GiB working allowance')
    missing = [k for k in KEYS if k + '.npy' not in names]
    if missing:
        raise Refused(f'Choose a screen-effects result; this file lacks {missing}')
    with np.load(path, allow_pickle=False) as source:
        return {k: source[k] for k in KEYS}


def _column(path, name):
    """One complete column of a plain CSV (the official gene and target lists), in file order."""
    import csv
    with open(path, newline='', encoding='utf-8') as stream:
        rows = list(csv.DictReader(stream))
    if not rows or name not in rows[0] or any(not row[name] for row in rows):
        raise Refused(f'The CSV needs a complete {name} column')
    values = [row[name] for row in rows]
    if len(set(values)) != len(values):
        raise Refused(f'{name} names each entry once')
    return values


def _build(e, targets, n_genes, a, s, rank, k, low, high):
    L = e['L'].astype(np.float64)
    cells, axis = e['cells'], e['axis_idx']
    tix = {t: i for i, t in enumerate(e['targets'].tolist())}
    w = cells / (cells + k)
    mu = L.mean(0)
    C = (L - mu) * w[:, None]
    if rank:
        U, S, Vt = np.linalg.svd(C, full_matrices=False)
        Lhat = mu + (U[:, :rank] * S[:rank]) @ Vt[:rank]
    else:
        Lhat = mu + C
    covered = [tix[t] for t in targets if t in tix]
    if not covered:
        raise Refused('The screen covers none of the panel targets')
    m = Lhat[covered].mean(0)
    logf = np.zeros((len(targets), n_genes), dtype=np.float64)
    for i, t in enumerate(targets):
        logf[i, axis] = a * m + (s * (Lhat[tix[t]] - m) if t in tix else 0.0)
    return np.exp(np.clip(logf, low, high)).astype(np.float32), len(covered)


def run(inputs, params, output, check):
    e = _effects(inputs['effects'])
    genes = _column(inputs['genes'], 'gene_name')
    targets = _column(inputs['perts'], 'target_gene')
    if int(e['axis_length']) != len(genes) or e['axis_idx'].max(initial=-1) >= len(genes):
        raise Refused('The effects were made on a different gene axis; prepare them on this one')
    contexts = [c.strip() for c in params['contexts'].split(',') if c.strip()]
    if not contexts or len(set(contexts)) != len(contexts) or not all(c.isidentifier() for c in contexts):
        raise Refused('Contexts are distinct names separated by commas, such as D,E,F')
    a, s, rank, k = float(params['a']), float(params['s']), int(params['rank']), float(params['k'])
    low, high = float(params['clip_low']), float(params['clip_high'])
    if not low < 0 < high:
        raise Refused('The log-factor clip range must hold 0')
    check()
    f, covered = _build(e, targets, len(genes), a, s, rank, k, low, high)
    weight = params['strength_weight']
    summary = {'format': 'transfer-factors-v1', 'targets': len(targets), 'covered': covered, 'genes': len(genes),
               'contexts': contexts, 'parameters': {'a': a, 's': s, 'rank': rank, 'k': k, 'clip': [low, high],
                                                    'strength_weight': weight}}
    if weight == 'saturating':
        half, scale = float(params['strength_half']), float(params['strength_scale'])
        tix = {t: i for i, t in enumerate(e['targets'].tolist())}
        n = {}
        for t in targets:
            if t in tix and np.isfinite(e['strength'][tix[t]]) and e['strength_cells'][tix[t]] > 0:
                n[t] = e['strength'][tix[t]] * scale / e['strength_cells'][tix[t]]
        if not n:
            raise Refused('The effects carry no strength for any panel target; prepare them with a strength column')
        median = float(np.median(list(n.values())))
        nt = np.array([n.get(t, median) for t in targets])
        weights = nt / (nt + half)
        check()
        f0, _ = _build(e, targets, len(genes), 0.0, s, rank, k, low, high)
        l0, l1 = np.log(f0.astype(np.float64)), np.log(f.astype(np.float64))
        f = np.exp(l0 + weights[:, None] * (l1 - l0)).astype(np.float32)
        summary['strength'] = {'half': half, 'scale': scale, 'withStrength': len(n), 'medianFilled': len(targets) - len(n),
                               'weightQuantiles': np.percentile(weights, [0, 10, 50, 90, 100]).round(4).tolist()}
    elif weight != 'none':
        raise Refused('strength_weight is none or saturating')
    if not np.isfinite(f).all() or (f <= 0).any():
        raise Refused('Factors must be finite and positive')
    path = output / 'factors.npz'
    np.savez(path, **{c: f for c in contexts})
    summary.update({'factorRange': [float(f.min()), float(f.max())],
                    'meaning': 'multiplicative change in expected expression per target and gene; 1 = unchanged'})
    return path, summary
