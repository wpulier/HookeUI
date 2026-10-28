"""Pinned upstream cellular-response evaluation with an explicit retained protocol.

The Project supplies a shipped preset and full target panels. The host fixes the
CPU/scanpy backend. Upstream owns all metrics and their aggregation; reference
scaling and competition acceptance are deliberately not inferred here.
"""
from dataclasses import replace
from contextlib import contextmanager
from importlib import metadata
from types import SimpleNamespace
import json
import math

import numpy as np

if __package__:
    from .engine import Refused, _json, _strings, _finite, MAX_ROWS, MAX_GENES
    from . import bounded_h5
else:
    from engine import Refused, _json, _strings, _finite, MAX_ROWS, MAX_GENES
    import bounded_h5

VERSION = '0.20.0'
MATERIALIZATION_BUDGET = 8 << 30


@contextmanager
def _counts(path, config, check=lambda: None):
    """Read only X and required axes with upstream AnnData decoders.

    A backed read_h5ad still reads auxiliary representations. These are neither
    evaluator inputs nor authority to allocate their possibly very large arrays.
    """
    import h5py
    import pandas as pd
    with h5py.File(path, 'r') as source:
        x = bounded_h5.matrix(source, check)
        var = source['var']; gene_field = var.attrs.get('_index', '_index')
        genes = _strings(bounded_h5.text(var[gene_field], x.shape[1]), 'Evaluation genes', unique=True)
        columns = {}
        for name in (config['context_column'], config['target_column']):
            if name not in source['obs']:
                raise Refused(f'Evaluation input lacks complete {name} labels')
            columns[name] = bounded_h5.text(source['obs'][name], x.shape[0])
        yield SimpleNamespace(X=x, obs=pd.DataFrame(columns), var_names=genes, n_vars=x.shape[1])


def _allocation_bytes(data, rows):
    """Conservative bound before allocating a context or upstream temporary copies."""
    matrix = data.X
    if hasattr(matrix, 'group'):
        group = matrix.group
        nnz = group['data'].shape[0]  # whole-file bound; never underestimate a selected context
        if group['indices'].shape != (nnz,) or len(group['indptr'].shape) != 1:
            raise Refused('Sparse evaluation storage has inconsistent dimensions')
        # Allow float64 conversion and 64-bit sparse indexing even for float32 inputs.
        payload = nnz * 16 + (len(rows) + data.n_vars + 2) * 8
    else:
        payload = len(rows) * data.n_vars * max(8, matrix.dtype.itemsize)
    # Reference-control concat, normalization and DE may retain several copies.
    return payload * 8


def _context(data, rows, config):
    import anndata as ad
    import pandas as pd
    x = data.X[rows, :]
    _finite(x, counts=True)
    return ad.AnnData(x, obs=data.obs.iloc[rows].copy(), var=pd.DataFrame(index=data.var_names))


def protocol(path):
    if path.stat().st_size > 1 << 20:
        raise Refused('The evaluation protocol exceeds its metadata allowance')
    config = json.loads(path.read_text())
    if type(config) is not dict or set(config) != {'preset', 'context_column', 'target_column', 'control_label', 'panels'}:
        raise Refused('Declare the preset, context/target columns, control label and complete panels')
    if any(type(config[k]) is not str or not 0 < len(config[k]) <= 256 for k in config if k != 'panels'):
        raise Refused('Evaluation protocol names must be nonempty text')
    panels = config['panels']
    if type(panels) is not dict or not 1 <= len(panels) <= 64:
        raise Refused('Declare one complete target panel per biological context')
    for context, targets in panels.items():
        if type(context) is not str or not 0 < len(context) <= 256 or type(targets) is not list:
            raise Refused('A panel maps a context label to its explicit target list')
        _strings(targets, 'Evaluation targets', unique=True)
        if config['control_label'] in targets:
            raise Refused('Target panels exclude the control group')
    return config


def _plain(value):
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


def evaluate(inputs, params, output, check):
    if metadata.version('cell-eval2') != VERSION:
        raise Refused('Restore the pinned cell-eval2 runtime before evaluation')
    from cell_eval2 import EvalConfig, compute_metrics, aggregate_metrics
    from cell_eval2.catalog import resolve_metrics, CATALOG
    config = protocol(inputs['config'])
    cfg = EvalConfig.from_preset(config['preset'])
    if cfg.control_source != 'real' or cfg.input_type != 'counts':
        raise Refused('This count evaluator requires a preset using real reference controls')
    cfg = replace(cfg, pert_col=config['target_column'], control=config['control_label'],
                  device='cpu', num_threads=1, gather_threads=1,
                  outdir=None, cache_real=None, cache_pred=None,
                  de=replace(cfg.de, backend='scanpy'))
    selected, missing = resolve_metrics(cfg.metrics, version=cfg.version)
    if missing:
        raise Refused('The selected upstream evaluator preset has unavailable metrics')
    contexts = []
    with _counts(inputs['prediction'], config, check) as prediction, _counts(inputs['reference'], config, check) as reference:
        if list(prediction.var_names) != list(reference.var_names):
            raise Refused('Cell evaluation requires the same ordered gene axis on both inputs')
        for side, data in [('prediction', prediction), ('reference', reference)]:
            for column in (config['context_column'], config['target_column']):
                if column not in data.obs or data.obs[column].isna().any():
                    raise Refused(f'{side} lacks complete {column} labels')
            if set(data.obs[config['context_column']].astype(str)) != set(config['panels']):
                raise Refused(f'{side} has missing or extra biological contexts')
        for context, panel in sorted(config['panels'].items()):
            check()
            p_rows = np.flatnonzero(prediction.obs[config['context_column']].astype(str).to_numpy() == context)
            r_rows = np.flatnonzero(reference.obs[config['context_column']].astype(str).to_numpy() == context)
            p_targets = set(prediction.obs.iloc[p_rows][config['target_column']].astype(str))
            r_targets = set(reference.obs.iloc[r_rows][config['target_column']].astype(str))
            if p_targets != set(panel) or r_targets != {*panel, config['control_label']}:
                raise Refused('Evaluate the complete declared panel with reference controls; missing and extra targets refuse')
            estimated = _allocation_bytes(prediction, p_rows) + _allocation_bytes(reference, r_rows)
            if estimated > MATERIALIZATION_BUDGET:
                raise Refused('Whole-context evaluation exceeds the conservative 8 GiB materialization allowance; qualify a larger or streaming evaluator without splitting the target panel')
            # One entire context per upstream call: arbitrary target chunks alter discrimination.
            p = _context(prediction, p_rows, config); r = _context(reference, r_rows, config)
            # Upstream structural validation requires identical group sets even
            # when the preset explicitly uses the reference control pool. Attach
            # that pool only to this transient evaluation input, never the retained
            # prediction or export. No perturbed reference labels enter predictions.
            import anndata as ad
            controls = r[r.obs[config['target_column']].astype(str) == config['control_label']]
            p = ad.concat([p, controls], join='inner', merge='same', index_unique='-evaluation-')
            measured = compute_metrics(p, r, config=cfg)
            aggregated = aggregate_metrics(measured, metrics=selected)
            contexts.append({'context': context, 'targets': panel, 'estimatedMaterializationBytes': estimated,
                             'metrics': measured.to_dicts(),
                             'aggregate': aggregated.to_dicts()})
            del p, r
    result = {'format': 'cell-evaluation-v1', 'evaluator': 'cell-eval2', 'version': VERSION,
              'config': cfg.to_dict(), 'protocol': config, 'contexts': _plain(contexts),
              'backend': {'device': 'cpu', 'differentialExpression': 'scanpy', 'threads': 1},
              'resolvedMetrics': selected,
              'primaryScoredMetrics': [name for name in selected if CATALOG[name].scoring.scored],
              'diagnosticOrDependencyMetrics': [name for name in selected if not CATALOG[name].scoring.scored],
              'referenceScaled': False, 'officialAcceptance': False,
              'evaluationInputConstruction': 'Reference controls appended transiently for upstream group compatibility; prediction artifact unchanged',
              'nonfiniteValues': 'Upstream nonfinite measurements remain unavailable as JSON null',
              'qualification': 'local-compute-only'}
    return _json(output / 'cell-evaluation.json', result), result
