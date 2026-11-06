"""Score a prediction against held-out real cells with the pinned upstream cell-eval2 (`cell-eval2 run`).

Runs the evaluator's own command entry point in this process (no shell, no child process), with the
declared preset and perturbation column, exactly as `cell-eval2 run -ap PRED -ar REAL --preset P
--pert-col C -o OUT` does. Before the real score it scores a tiny synthetic pair end to end in the same
runtime, so a broken environment (for example an AnnData that returns no matrix for a backed view,
which cost three 20-minute runs on 5 Nov 2026) fails in seconds instead of after the long run.

Returns the evaluator's raw aggregate and per-target metric values and its run record. These are raw
metrics on this fold, not anchored scaled scores, and not the organizer's leaderboard score.
"""
import contextlib
import io
import json
import math
from importlib.metadata import version
from pathlib import Path
import tempfile

import numpy as np

PINNED = {'cell-eval2': '0.20.0', 'anndata': '0.13.4'}


class Refused(ValueError):
    pass


def _evaluate(pred, real, preset, pert_col, outdir):
    from cell_eval2.cli import main
    log = io.StringIO()
    try:
        with contextlib.redirect_stdout(log):
            main(['run', '-ap', str(pred), '-ar', str(real), '--preset', preset, '--pert-col', pert_col, '-o', str(outdir)])
    except SystemExit as stop:
        if stop.code not in (0, None):
            raise Refused(f'cell-eval2 run refused: {stop.code}') from None
    return log.getvalue()[-2000:]


def _tiny_pair(folder, pert_col, control):
    """A small synthetic prediction/real pair with clear responses: an end-to-end smoke test, not evidence."""
    import anndata as ad
    import pandas as pd
    import scipy.sparse as sp
    rng = np.random.default_rng(0)
    genes = [f'g{i:03d}' for i in range(200)]
    base = rng.gamma(2.0, 2.0, len(genes))
    def cells(label, n, shift):
        lam = base.copy()
        if shift is not None:
            lam[shift] *= 4.0
        return sp.csr_matrix(rng.poisson(lam, (n, len(genes))).astype(np.float32)), [label] * n
    pairs = {}
    for side, seed_shift in (('real', 0), ('pred', 1)):
        blocks, labels = [], []
        # Targets are genes on the axis (cell-eval2 refuses targets it cannot resolve to a feature).
        for label, shift in ((control, None), ('g005', slice(0, 10)), ('g025', slice(20, 30)), ('g045', slice(40, 50))):
            X, lab = cells(label, 60, shift)
            blocks.append(X)
            labels += lab
        frame = pd.DataFrame({pert_col: labels}, index=[f'{side}{i}' for i in range(len(labels))])
        path = folder / f'{side}.h5ad'
        ad.AnnData(sp.vstack(blocks).tocsr(), obs=frame, var=pd.DataFrame(index=genes)).write_h5ad(path)
        pairs[side] = path
    return pairs


def _frames(outdir):
    import pandas as pd
    agg = pd.read_csv(outdir / 'agg_results.csv')
    if 'statistic' not in agg or not (agg['statistic'] == 'mean').any():
        raise Refused('The evaluator wrote no mean aggregate')
    mean = agg[agg['statistic'] == 'mean'].iloc[0].drop('statistic')
    per = pd.read_csv(outdir / 'results.csv')
    if not {'perturbation', 'metric', 'value'} <= set(per.columns):
        raise Refused('The evaluator wrote no per-target results')
    def plain(v):
        v = float(v)
        return v if math.isfinite(v) else None
    aggregate = {str(k): plain(v) for k, v in mean.items()}
    per_target = {}
    for row in per.itertuples(index=False):
        per_target.setdefault(str(row.metric), {})[str(row.perturbation)] = plain(row.value)
    return aggregate, per_target


def run(inputs, params, output, check):
    for package, pinned in PINNED.items():
        if version(package) != pinned:
            raise Refused(f'Install the pinned evaluation runtime: {package} is {version(package)}, not {pinned}')
    preset, pert_col, control = params['preset'], params['pert_col'], params['control_label']
    with tempfile.TemporaryDirectory(dir=output, prefix='preflight-') as scratch:
        scratch = Path(scratch)
        pair = _tiny_pair(scratch, pert_col, control)
        try:
            _evaluate(pair['pred'], pair['real'], preset, pert_col, scratch / 'out')
            tiny, _ = _frames(scratch / 'out')
        except Exception as error:
            raise Refused(f'The evaluation runtime failed its tiny end-to-end score before the real one: {error}') from error
    preflight = {'passed': True, 'metrics': sorted(k for k, v in tiny.items() if v is not None)}
    check()
    outdir = output / 'evaluation'
    _evaluate(inputs['prediction'], inputs['real'], preset, pert_col, outdir)
    aggregate, per_target = _frames(outdir)
    meta = json.loads((outdir / 'run_meta.json').read_text()) if (outdir / 'run_meta.json').is_file() else {}
    path = output / 'fold-score.json'
    value = {'format': 'vcc-fold-score-v1', 'preset': preset, 'pertCol': pert_col, 'controlLabel': control,
             'versions': {k: version(k) for k in PINNED}, 'aggregate': aggregate, 'perTarget': per_target,
             'runMeta': {k: meta.get(k) for k in ('cell_eval2_version', 'config_digest', 'comparator', 'created_utc')},
             'preflight': preflight,
             'meaning': 'raw upstream metrics on this fold; not anchored scaled scores or an organizer score'}
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False))
    return path, {k: value[k] for k in ('format', 'preset', 'versions', 'aggregate', 'preflight', 'meaning')} | {
        'targets': len(next(iter(per_target.values()), {}))}
