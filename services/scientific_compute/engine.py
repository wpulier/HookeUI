"""Explicit raw-count preparation, regularized effects and conditional count emission.

Inputs are already-authorized materialized files, never paths from agent arguments.
No orchestration or attempt state lives here. Every result is a file plus bounded
metadata; matrix values are not transported through a table or a chat response.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import zipfile
import shutil

import numpy as np
from scipy import linalg, sparse

MAX_DENSE_BYTES = 512 << 20
MAX_ROWS = 1_000_000
MAX_GENES = 100_000
BLOCK_ROWS = 128


class Refused(ValueError):
    pass


def _json(path, value):
    path.write_text(json.dumps(value, sort_keys=True, allow_nan=False), encoding='utf-8')
    return path


def _strings(values, name, *, unique=False):
    result = np.asarray(values)
    if (result.ndim != 1 or result.dtype.kind not in 'US' or not len(result)
            or any(not v or len(v) > 256 for v in result.tolist())):
        raise Refused(f'{name} must contain nonempty explicit text labels')
    if unique and len(set(result.tolist())) != len(result):
        raise Refused(f'{name} must be unique; duplicates are not silently renamed')
    return result.astype(str)


def _finite(values, *, counts=False):
    values = values.data if sparse.issparse(values) else np.asarray(values)
    if values.dtype.kind not in 'biuf':
        raise Refused('Counts and response effects require real numeric values')
    if not np.isfinite(values).all():
        raise Refused('Matrix values must be finite')
    if counts and (np.any(values < 0) or np.any(values != np.floor(values))):
        raise Refused('This operation requires raw nonnegative whole counts')


def _dense_budget(rows, columns, *, copies=1):
    if rows * columns * 8 * copies > MAX_DENSE_BYTES:
        raise Refused('This compact operation exceeds its 512 MiB dense working allowance')


def _npz(path, required):
    with zipfile.ZipFile(path) as archive:
        names = [i.filename for i in archive.infolist()]
        if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > MAX_DENSE_BYTES:
            raise Refused('The numerical artifact has duplicate entries or exceeds its working allowance')
        for key in required:
            name = key + '.npy'
            if name not in names:
                raise Refused('The numerical artifact has the wrong declared meaning')
            with archive.open(name) as member:
                version = np.lib.format.read_magic(member)
                if version not in ((1, 0), (2, 0)):
                    raise Refused('Unsupported numerical artifact header version')
                reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
                shape, _, dtype = reader(member, max_header_size=16384)
                size = dtype.itemsize
                for dimension in shape:
                    size *= dimension
                if dtype.hasobject or size > MAX_DENSE_BYTES or member.tell() + size != archive.getinfo(name).file_size:
                    raise Refused('Unsafe or inconsistent numerical artifact layout')
    with np.load(path, allow_pickle=False) as source:
        result = {key: source[key] for key in required}
    return result


@contextmanager
def _adata(path):
    try:
        import anndata
    except ImportError as error:
        raise Refused('Prepare the pinned AnnData runtime before reading matrices') from error
    data = anndata.read_h5ad(path, backed='r')
    try:
        if not 0 < data.n_obs <= MAX_ROWS or not 0 < data.n_vars <= MAX_GENES:
            raise Refused('The annotated matrix exceeds the declared row/gene allowance')
        _strings(data.var_names.to_numpy(dtype=str), 'Gene axis', unique=True)
        yield data
    finally:
        data.file.close()


def _columns(data, params):
    names = [params['context_column'], params['target_column']]
    if any(name not in data.obs or data.obs[name].isna().any() for name in names):
        raise Refused('Every observation needs the selected context and target columns')
    return [_strings(data.obs[name].to_numpy(dtype=str), name) for name in names]


def _rows(data, check, *, counts=True):
    for start in range(0, data.n_obs, BLOCK_ROWS):
        check()
        block = data.X[start:start + BLOCK_ROWS]
        _finite(block, counts=counts)
        yield start, sparse.csr_matrix(block, dtype=np.float64)


def inspect(inputs, params, output, check):
    with _adata(inputs['matrix']) as data:
        totals = []; nnz = 0; whole_nonnegative = True
        require_counts = params.get('require_raw_counts', True)
        if type(require_counts) is not bool:
            raise Refused('Choose whether inspection requires whole nonnegative counts')
        for _, block in _rows(data, check, counts=require_counts):
            whole_nonnegative = whole_nonnegative and bool(np.all(block.data >= 0) and np.all(block.data == np.floor(block.data)))
            nnz += block.nnz
            totals.extend(np.asarray(block.sum(axis=1)).ravel().tolist())
        result = {'format': 'annotated-matrix-summary-v1', 'shape': list(data.shape),
                  'dtype': str(data.X.dtype), 'sparse': getattr(data.X, 'format', None) in ('csr', 'csc'),
                  'genes': data.n_vars, 'observations': data.n_obs, 'storedEntries': nnz,
                  'rawCounts': whole_nonnegative, 'observationColumns': list(data.obs.columns),
                  ('libraryDepth' if require_counts else 'rowTotals'): {'min': min(totals), 'max': max(totals), 'mean': float(np.mean(totals))},
                  'geneAxisSha256': hashlib.sha256('\n'.join(data.var_names).encode()).hexdigest()}
        if not require_counts:
            result['valueSemantics'] = 'unverified'
            result['interpretation'] = 'Finite numeric values inspected without raw-count admission. Whole-valued entries alone do not establish experimental counts or preprocessing history.'
    return _json(output / 'summary.json', result), result


def prepare(inputs, params, output, check):
    """Aggregate raw counts by explicit context/target, then form log2 CP10k effects."""
    with _adata(inputs['matrix']) as data:
        contexts, targets = _columns(data, params)
        pairs = sorted(set(zip(contexts.tolist(), targets.tolist())))
        _dense_budget(len(pairs), data.n_vars, copies=4)
        indices = {pair: index for index, pair in enumerate(pairs)}
        sums = np.zeros((len(pairs), data.n_vars), dtype=np.float64)
        sizes = np.zeros(len(pairs), dtype=np.int64)
        for start, block in _rows(data, check):
            row_groups = np.asarray([indices[(contexts[i], targets[i])] for i in range(start, start + block.shape[0])])
            for group in np.unique(row_groups):
                selected = row_groups == group
                sums[group] += np.asarray(block[selected].sum(axis=0)).ravel()
                sizes[group] += int(selected.sum())
        total = sums.sum(axis=1)
        if np.any(total <= 0):
            raise Refused('Every context/target aggregate must have positive library depth')
        normalized = sums / total[:, None] * params['normalization_total']
        control = params['control_label']; pseudo = params['pseudocount']
        if any((context, control) not in indices for context, _ in pairs):
            raise Refused('Every biological context needs a non-targeting control aggregate')
        retained = [i for i, (_, target) in enumerate(pairs) if target != control]
        if not retained:
            raise Refused('Training needs perturbed observations as well as controls')
        effects = np.stack([np.log2((normalized[i] + pseudo) /
                         (normalized[indices[(pairs[i][0], control)]] + pseudo)) for i in retained])
        result = {'format': 'response-effects-v1', 'shape': list(effects.shape),
                  'aggregation': params['aggregation'], 'contexts': sorted(set(contexts.tolist())),
                  'normalizationTotal': params['normalization_total'], 'pseudocount': pseudo,
                  'scale': 'log2-normalized-count-ratio', 'rawCountsAggregated': True}
        path = output / 'effects.npz'
        np.savez_compressed(path, format=np.array('response-effects-v1'), effects=effects,
            genes=data.var_names.to_numpy(dtype=str), contexts=np.array([pairs[i][0] for i in retained]),
            targets=np.array([pairs[i][1] for i in retained]), sizes=sizes[retained],
            metadata=np.array(json.dumps(result, sort_keys=True)))
    return path, result


def fit(inputs, params, output, check):
    source = _npz(inputs['effects'], ('format', 'effects', 'genes', 'contexts', 'targets'))
    if source['format'].item() != 'response-effects-v1':
        raise Refused('Fit requires prepared response effects')
    values = source['effects']; contexts = _strings(source['contexts'], 'Contexts')
    targets = _strings(source['targets'], 'Targets'); genes = _strings(source['genes'], 'Genes', unique=True)
    if values.shape != (len(targets), len(genes)) or len(contexts) != len(targets):
        raise Refused('Effect labels do not match the matrix dimensions')
    _finite(values); _dense_budget(*values.shape, copies=5)
    if len(set(zip(contexts, targets))) != len(targets):
        raise Refused('Prepare one aggregate for each biological context and target')
    unique_contexts = sorted(set(contexts.tolist())); unique_targets = sorted(set(targets.tolist()))
    # Contexts have equal total influence, even when their panels differ in size.
    weights = np.array([1 / (len(unique_contexts) * np.sum(contexts == c)) for c in contexts])
    coefficients = np.zeros((len(unique_targets), len(genes)))
    for i, target in enumerate(unique_targets):
        check(); selected = targets == target
        coefficients[i] = (weights[selected, None] * values[selected]).sum(axis=0) / (weights[selected].sum() * (1 + params['regularization']))
    rank = params['rank']
    if rank:
        if rank > min(coefficients.shape):
            raise Refused('Requested rank exceeds the fitted target/gene dimensions')
        check(); u, s, vh = linalg.svd(coefficients, full_matrices=False, check_finite=True)
        coefficients = (u[:, :rank] * s[:rank]) @ vh[:rank]
    result = {'format': 'response-model-v1', 'shape': list(coefficients.shape), 'trainingContexts': unique_contexts,
              'regularization': params['regularization'], 'rank': rank, 'contextWeighting': 'equal-total-weight',
              'penaltyScale': 'per-target-observed-weight',
              'effectContext': 'context-independent', 'unseenTargetPrior': 'zero-effect',
              'missingGenePrior': 'zero-effect', 'scale': 'log2-normalized-count-ratio'}
    # Legacy preparation remains fit-capable, but cannot certify normalized-effect transfer.
    with zipfile.ZipFile(inputs['effects']) as archive:
        has_metadata = 'metadata.npy' in archive.namelist()
    if has_metadata:
        declared = _npz(inputs['effects'], ('metadata',))['metadata']
        metadata = json.loads(declared.item())
        if 'normalization' in metadata:
            if __package__:
                from .aggregate_profiles import _effects, _digest
            else:
                from aggregate_profiles import _effects, _digest
            _, preparation = _effects(inputs['effects'])
            result['normalization'] = preparation['normalization']
            result['trainingEffectsSha256'] = _digest(inputs['effects'], check)
    path = output / 'model.npz'
    np.savez_compressed(path, format=np.array('response-model-v1'), effects=coefficients,
                        genes=genes, targets=np.array(unique_targets), metadata=np.array(json.dumps(result, sort_keys=True)))
    return path, result


def _model(path):
    model = _npz(path, ('format', 'effects', 'genes', 'targets', 'metadata'))
    if model['format'].item() != 'response-model-v1':
        raise Refused('Choose a fitted response-model artifact')
    genes = _strings(model['genes'], 'Model genes', unique=True); targets = _strings(model['targets'], 'Model targets', unique=True)
    if model['effects'].shape != (len(targets), len(genes)):
        raise Refused('Model axes disagree with its effects')
    _finite(model['effects'])
    return model


def _control_moments(data, contexts, context, check):
    depths = []; total = np.zeros(data.n_vars)
    for start, block in _rows(data, check):
        selected = block[contexts[start:start + block.shape[0]] == context]
        if selected.shape[0]:
            depths.extend(np.asarray(selected.sum(axis=1)).ravel().tolist())
            total += np.asarray(selected.sum(axis=0)).ravel()
    depths = np.asarray(depths)
    if len(depths) < 2 or np.any(depths <= 0):
        raise Refused('Each count generator needs at least two positive-depth control cells')
    first = np.zeros(data.n_vars); second = np.zeros(data.n_vars)
    for start, block in _rows(data, check):
        selected = block[contexts[start:start + block.shape[0]] == context]
        if selected.shape[0]:
            d = np.asarray(selected.sum(axis=1)).ravel()
            normalized = selected.multiply((np.median(depths) / d)[:, None]).tocsr()
            first += np.asarray(normalized.sum(axis=0)).ravel()
            second += np.asarray(normalized.power(2).sum(axis=0)).ravel()
    mean = first / len(depths)
    variance = second / len(depths) - mean ** 2
    dispersion = np.maximum(0, (variance - mean) / np.maximum(mean ** 2, 1e-12))
    return depths, total / total.sum(), dispersion, mean


def _concat(parts, path, check):
    from anndata.experimental import concat_on_disk
    generation = 0
    while len(parts) > 1:
        next_parts = []
        for start in range(0, len(parts), 32):
            check(); group = parts[start:start + 32]
            target = path if len(parts) <= 32 else path.with_name(f'merge_{generation}_{start}.h5ad')
            concat_on_disk(group, target, max_loaded_elems=1_000_000, axis=0, join='inner', merge='same')
            next_parts.append(target)
            for part in group:
                part.unlink()
        parts = next_parts; generation += 1
    if parts[0] != path:
        parts[0].rename(path)


def predict(inputs, params, output, check):
    import anndata
    import pandas as pd
    model = _model(inputs['model'])
    if inputs['panel'].stat().st_size > 1 << 20:
        raise Refused('The target panel exceeds its metadata allowance')
    targets = _strings(json.loads(inputs['panel'].read_text()), 'Requested targets', unique=True)
    if params['control_label'] in targets:
        raise Refused('Predictions must not include the non-targeting control label')
    model_targets = {v: i for i, v in enumerate(model['targets'])}
    model_genes = {v: i for i, v in enumerate(model['genes'])}
    if __package__:
        from .count_emission import proportions as convert_effect
    else:
        from count_emission import proportions as convert_effect
    model_meaning = json.loads(model['metadata'].item())
    parts = []; missing = []; projected_genes = 0
    with _adata(inputs['controls']) as data:
        contexts, labels = _columns(data, params)
        if set(labels.tolist()) != {params['control_label']}:
            raise Refused('The conditioning matrix must contain only declared control observations')
        nout = len(set(contexts)) * len(targets) * params['cells_per_target']
        if nout > MAX_ROWS:
            raise Refused('Prediction exceeds the declared observation allowance')
        _dense_budget(BLOCK_ROWS, data.n_vars, copies=5)
        gene_indices = np.array([model_genes.get(g, -1) for g in data.var_names]); covered = gene_indices >= 0
        # CSR float32 value/index pairs plus indptr; allow chunks + merged output + safety margin.
        worst_case_bytes = nout * data.n_vars * 8 + (nout + 1) * 8
        if shutil.disk_usage(output).free < worst_case_bytes * 2 + (512 << 20):
            raise Refused('Free space for sparse prediction chunks and the merged output before inference')
        for context in sorted(set(contexts)):
            depths, proportions, dispersion, mean = _control_moments(data, contexts, context, check)
            prior = np.median(dispersion[mean > 0])
            dispersion = (dispersion * len(depths) + prior * params['dispersion_shrinkage']) / (len(depths) + params['dispersion_shrinkage'])
            over = dispersion > 1e-9
            for target in targets:
                check(); effect = np.zeros(data.n_vars)
                if target in model_targets:
                    effect[covered] = model['effects'][model_targets[target], gene_indices[covered]]
                else:
                    missing.append(str(target))
                exponent = effect * params['response_strength']
                if np.any(np.abs(exponent) > 50):
                    raise Refused('Fitted effects are outside the emitter numerical range; calibrate explicitly')
                probability, projected = convert_effect(proportions, exponent, data.var_names.tolist(), model_meaning, params['effect_interpretation'])
                projected_genes += projected
                for first in range(0, params['cells_per_target'], BLOCK_ROWS):
                    check(); count = min(BLOCK_ROWS, params['cells_per_target'] - first)
                    rows = []
                    for cell in range(first, first + count):
                        seed = int.from_bytes(hashlib.sha256(f'{params["seed"]}\0{context}\0{target}\0{cell}'.encode()).digest()[:8], 'little')
                        rng = np.random.default_rng(seed)
                        mu = rng.choice(depths) * probability
                        counts = rng.poisson(mu)
                        if np.any(over):
                            shape = 1 / dispersion[over]
                            counts[over] = rng.negative_binomial(shape, shape / (shape + mu[over]))
                        if counts.sum() > params['maximum_cell_counts'] or np.any(counts > 2 ** 24):
                            raise Refused('Emitted counts exceed the declared allowance; no clipping or pruning applied')
                        rows.append(sparse.csr_matrix(counts, dtype=np.float32))
                    block = sparse.vstack(rows, format='csr'); block.eliminate_zeros()
                    obs = pd.DataFrame({'context': [str(context)] * count, 'target_gene': [str(target)] * count},
                        index=[hashlib.sha256(f'{context}\0{target}\0{i}'.encode()).hexdigest() for i in range(first, first + count)])
                    part = output / f'chunk_{len(parts)}.h5ad'
                    anndata.AnnData(X=block, obs=obs, var=pd.DataFrame(index=data.var_names.copy())).write_h5ad(part, compression='gzip')
                    parts.append(part)
        result = {'format': 'annotated-count-predictions-v1', 'shape': [nout, data.n_vars], 'dtype': 'float32',
                  'sparse': True, 'rawCounts': True, 'seed': params['seed'], 'responseStrength': params['response_strength'],
                  'unseenTargets': sorted(set(missing)), 'unmeasuredGenes': data.var_names[~covered].tolist(),
                  'generator': 'control-conditioned-negative-binomial-v1', 'seedScope': 'context-target-cell',
                  'effectInterpretation': params['effect_interpretation'], 'negativeExpressionProjections': projected_genes,
                  'compositionalClosure': 'whole-output-gene-axis',
                  'libraryDepthMeaning': 'sampled expected depth; emitted total also varies',
                  'independentGeneSampling': True,
                  'assembly': 'anndata-concat-on-disk', 'fullShapeQualified': False}
        path = output / 'prediction.h5ad'; _concat(parts, path, check)
        with _adata(path) as produced:
            if list(produced.var_names) != list(data.var_names) or list(produced.shape) != result['shape']:
                raise Refused('Output assembly changed the requested shape or gene order')
    return path, result


def combine(inputs, params, output, check):
    left, right = _model(inputs['left']), _model(inputs['right'])
    if not np.array_equal(left['genes'], right['genes']) or not np.array_equal(left['targets'], right['targets']):
        raise Refused('Combine requires identical ordered gene and target axes')
    check(); effects = left['effects'] * params['left_weight'] + right['effects'] * (1 - params['left_weight'])
    result = {'format': 'response-model-v1', 'shape': list(effects.shape), 'leftWeight': params['left_weight'],
              'combination': 'effect-space-weighted-mean', 'scale': 'log2-normalized-count-ratio'}
    path = output / 'combined.npz'
    np.savez_compressed(path, format=np.array('response-model-v1'), effects=effects, genes=left['genes'],
                        targets=left['targets'], metadata=np.array(json.dumps(result, sort_keys=True)))
    return path, result


def evaluate(inputs, params, output, check):
    if params['profile'] != 'expression-mean-v1':
        raise Refused('That evaluator is not installed and qualified; no substitute score was computed')
    with _adata(inputs['prediction']) as predicted, _adata(inputs['reference']) as reference:
        if list(predicted.var_names) != list(reference.var_names):
            raise Refused('Evaluation requires identical ordered gene axes')
        def means(data):
            contexts, targets = _columns(data, params)
            pairs = sorted(set(zip(contexts.tolist(), targets.tolist())))
            _dense_budget(len(pairs), data.n_vars, copies=4)
            result = {}
            for pair in pairs:
                check(); ids = np.flatnonzero((contexts == pair[0]) & (targets == pair[1]))
                values = sparse.csr_matrix(data.X[ids], dtype=np.float64); _finite(values, counts=True)
                depths = np.asarray(values.sum(axis=1)).ravel()
                if np.any(depths <= 0):
                    raise Refused('Mean-profile evaluation requires positive cell library depths')
                result[pair] = np.asarray(values.multiply((params['normalization_total'] / depths)[:, None]).mean(axis=0)).ravel()
            return result
        p, r = means(predicted), means(reference)
        if set(p) != set(r):
            raise Refused('Evaluate complete matching context/target panels; missing or extra groups are refused')
        rows = [{'context': c, 'target': t, 'mse': float(np.mean((p[c, t] - r[c, t]) ** 2)),
                 'mae': float(np.mean(np.abs(p[c, t] - r[c, t])))} for c, t in sorted(p)]
        result = {'format': 'matrix-evaluation-v1', 'profile': 'expression-mean-v1', 'rows': rows,
                  'normalizationTotal': params['normalization_total'], 'competitionScore': False,
                  'purpose': 'Local mean-expression diagnostic, not differential-expression or competition scoring'}
    return _json(output / 'evaluation.json', result), result


def export(inputs, params, output, check):
    inspect(inputs, {}, output, check)
    destination = output / 'matrix.h5ad'
    with inputs['matrix'].open('rb') as source, destination.open('xb') as target:
        while part := source.read(1 << 20):
            check(); target.write(part)
    return destination, {'format': 'annotated-matrix-export-v1', 'sourceBytesPreserved': True,
                         'mediaType': 'application/x-hdf5', 'packagedForSubmission': False}


def evaluate_cell(inputs, params, output, check):
    if __package__:
        from .cell_evaluation import evaluate as upstream
    else:
        from cell_evaluation import evaluate as upstream
    return upstream(inputs, params, output, check)


def prepare_aggregates(inputs, params, output, check):
    if __package__:
        from .aggregate_profiles import prepare as operation
    else:
        from aggregate_profiles import prepare as operation
    return operation(inputs, params, output, check)


def merge_effects(inputs, params, output, check):
    if __package__:
        from .aggregate_profiles import merge as operation
    else:
        from aggregate_profiles import merge as operation
    return operation(inputs, params, output, check)


def evaluate_effects(inputs, params, output, check):
    if __package__:
        from .effect_evaluation import evaluate as operation
    else:
        from effect_evaluation import evaluate as operation
    return operation(inputs, params, output, check)


def concatenate_counts(inputs, params, output, check):
    if __package__:
        from .matrix_assembly import concatenate
    else:
        from matrix_assembly import concatenate
    return concatenate(inputs, params, output, check)


def compare_count_populations(inputs, params, output, check):
    if __package__:
        from .count_diagnostics import compare
    else:
        from count_diagnostics import compare
    return compare(inputs, params, output, check)


OPERATIONS = {'matrix-inspect': inspect, 'matrix-prepare': prepare, 'response-fit': fit,
              'response-predict': predict, 'matrix-evaluate': evaluate, 'response-combine': combine,
              'matrix-export': export, 'matrix-evaluate-cell': evaluate_cell,
              'aggregate-profile-prepare': prepare_aggregates, 'response-effects-merge': merge_effects,
              'response-effect-evaluate': evaluate_effects, 'matrix-concatenate': concatenate_counts, 'matrix-count-diagnostics': compare_count_populations}
