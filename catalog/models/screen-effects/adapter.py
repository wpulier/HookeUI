"""Per-target log fold changes from one pseudobulk Perturb-seq screen, on a declared gene axis.

Ported unchanged in its arithmetic from the VCC 2026 Track E `effects.py` (5 Nov 2026), so a
screen gives bit-identical effects to the entry that was submitted:

- rows without a filtered-cell count are dropped; each row's mean raw counts times its cells
  gives pooled raw counts;
- rows are pooled by target (several guides or constructs), weighted by cells;
- each pooled target and the pooled controls are normalised to proportions over the screen's
  genes that are on the axis, with one pseudo-count over each pooled population;
- L = log(target proportion) - log(control proportion).

A row's target is the registry's mapping when it names the row, a control when its id holds the
control marker, else the id's second underscore-separated token (the Replogle 2022 row naming).
The strength of a target (optional) is the declared strength column and cell count of its row
with the most filtered cells. Nothing is fetched; the inputs are exact retained Project files.
"""
import json
import math
from pathlib import Path

import numpy as np

MAX_DENSE_BYTES = 4 << 30


class Refused(ValueError):
    pass


def _axis(path):
    import pandas as pd
    frame = pd.read_csv(path)
    if 'gene_name' not in frame or frame['gene_name'].isna().any():
        raise Refused('The gene axis is a CSV with a complete gene_name column')
    axis = frame['gene_name'].astype(str).tolist()
    if len(set(axis)) != len(axis):
        raise Refused('The gene axis names each gene once')
    return axis


def _registry(path):
    value = json.loads(Path(path).read_text())
    if not isinstance(value, dict) or not isinstance(value.get('row_targets'), dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in value['row_targets'].items()):
        raise Refused('The registry is JSON with row_targets: {row id: target}; use {"row_targets": {}} for none')
    return value['row_targets']


def run(inputs, params, output, check):
    import anndata as ad
    import pandas as pd
    axis = _axis(inputs['genes'])
    axis_index = {g: i for i, g in enumerate(axis)}
    registry = _registry(inputs['registry'])
    marker, pseudo = params['control_marker'], float(params['pseudocount'])
    cells_column, symbol_column = params['cells_column'], params['gene_symbol_column']
    strength_column = params['strength_column']

    def row_target(row_id):
        if row_id in registry:
            return registry[row_id]
        if marker in row_id:
            return marker
        parts = row_id.split('_')
        return parts[1] if len(parts) > 1 else row_id

    a = ad.read_h5ad(inputs['screen'])
    if cells_column not in a.obs or symbol_column not in a.var:
        raise Refused(f'The screen needs obs[{cells_column!r}] and var[{symbol_column!r}]')
    if strength_column and strength_column not in a.obs:
        raise Refused(f'The screen has no obs[{strength_column!r}] strength column')
    if a.n_obs * a.n_vars * 8 * 3 > MAX_DENSE_BYTES:
        raise Refused('This pseudobulk screen exceeds the 4 GiB dense working allowance')
    check()
    strength = {}
    if strength_column:
        values = a.obs[strength_column].to_numpy(dtype=np.float64)
        counted = a.obs[cells_column].to_numpy(dtype=np.float64)
        for row_id, value, cells in zip(a.obs_names.astype(str), values, counted):
            if marker in row_id or not np.isfinite(cells) or not np.isfinite(value):
                continue
            target = registry.get(row_id) or row_target(row_id)
            if target not in strength or cells > strength[target][1]:
                strength[target] = (value, cells)
    a = a[~a.obs[cells_column].isna().to_numpy()].copy()  # rows without a filtered-cell count are unusable
    X = np.asarray(a.X.toarray() if hasattr(a.X, 'toarray') else a.X, dtype=np.float64)
    if not np.isfinite(X).all() or (X < 0).any():
        raise Refused('Pseudobulk means must be finite and nonnegative')
    n = a.obs[cells_column].to_numpy(dtype=np.float64)
    targets = [row_target(r) for r in a.obs_names.astype(str)]
    symbols = a.var[symbol_column].astype(str).tolist()
    keep, axis_idx, seen = [], [], set()
    for j, g in enumerate(symbols):
        if g in axis_index and g not in seen:
            keep.append(j)
            axis_idx.append(axis_index[g])
            seen.add(g)
    if not keep:
        raise Refused('No screen gene is on the declared axis')
    keep = np.array(keep)
    check()
    totals = X * n[:, None]
    ctrl = np.array([t == marker for t in targets])
    if not ctrl.any() or ctrl.all():
        raise Refused('The screen needs both control rows and targeted rows')
    c0 = totals[ctrl].sum(0)
    p0 = (c0[keep] + pseudo) / (c0.sum() + pseudo * len(keep))
    df = pd.DataFrame(totals[:, keep])
    df['t'] = targets
    pooled = df[~ctrl].groupby('t').sum()
    cells = pd.Series(n[~ctrl]).groupby(np.array(targets)[~ctrl]).sum().reindex(pooled.index)
    lib = totals[~ctrl].sum(1)
    libs = pd.Series(lib).groupby(np.array(targets)[~ctrl]).sum().reindex(pooled.index)
    P = (pooled.to_numpy() + pseudo) / (libs.to_numpy()[:, None] + pseudo * len(keep))
    L = np.log(P) - np.log(p0)[None, :]
    if not np.isfinite(L).all():
        raise Refused('Effects are not finite; raise the pseudo-count')
    check()
    names = np.array([str(t) for t in pooled.index])
    best = [strength.get(t, (math.nan, math.nan)) for t in names]
    path = output / 'effects.npz'
    np.savez(path, targets=names, L=L.astype(np.float32), cells=cells.to_numpy(),
             axis_idx=np.array(axis_idx), ctrl_mean=X[ctrl].mean(0)[keep].astype(np.float32),
             strength=np.array([b[0] for b in best], dtype=np.float64),
             strength_cells=np.array([b[1] for b in best], dtype=np.float64),
             axis_length=np.array(len(axis)))
    return path, {'format': 'screen-effects-v1', 'targets': int(len(names)), 'genesOnAxis': int(len(keep)),
                  'axisLength': len(axis), 'controlRows': int(ctrl.sum()), 'pseudocount': pseudo,
                  'targetsWithStrength': int(sum(np.isfinite(b[0]) for b in best)),
                  'meaning': 'log proportion ratio of pooled target rows to pooled control rows; not a fitted model'}
