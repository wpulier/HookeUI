"""Descriptive population-count diagnostics, never a perturbation-response score."""
from contextlib import ExitStack
import hashlib
import numpy as np
from scipy import sparse
from scipy.stats import rankdata

if __package__:
    from . import engine, bounded_h5
else:
    import engine, bounded_h5


def _population(file, params, check):
    matrix = bounded_h5.matrix(file, check)
    if getattr(matrix, 'format', None) == 'csc':
        raise engine.Refused('Count diagnostics require dense or CSR storage')
    obs, var = file['obs'], file['var']
    axis = var.attrs.get('_index', '_index')
    genes = engine._strings(bounded_h5.text(var[axis], matrix.shape[1]), 'Genes', unique=True)
    labels = []
    for column in (params['context_column'], params['target_column']):
        if column not in obs:
            raise engine.Refused('Count diagnostics require explicit context and population labels')
        labels.append(engine._strings(bounded_h5.text(obs[column], matrix.shape[0]), column))
    contexts, targets = labels
    groups = sorted(set(contexts))
    if len(groups) > 64:
        raise engine.Refused('Count diagnostics support at most 64 contexts')
    engine._dense_budget(len(groups), len(genes), copies=20)
    engine._dense_budget(engine.BLOCK_ROWS, len(genes), copies=5)
    records = {}
    for context in groups:
        populations = set(targets[contexts == context])
        if len(populations) != 1:
            raise engine.Refused('Select one population per context; pooling perturbations hides distribution differences')
        if np.sum(contexts == context) < 2:
            raise engine.Refused('Each population needs at least two cells')
        records[context] = {'population': str(next(iter(populations))), 'depths': [],
                            'mean': np.zeros(len(genes)), 'm2': np.zeros(len(genes)),
                            'detected': np.zeros(len(genes)), 'normalized': np.zeros(len(genes))}
    for start in range(0, matrix.shape[0], engine.BLOCK_ROWS):
        check()
        end = min(start + engine.BLOCK_ROWS, matrix.shape[0])
        values = bounded_h5.row_block(matrix, start, end, list(range(len(genes))))
        engine._finite(values, counts=True)
        values = sparse.csr_matrix(values, dtype=np.float64)
        values.sum_duplicates(); values.eliminate_zeros()
        for context in set(contexts[start:end]):
            block = values[contexts[start:end] == context]
            depth = np.asarray(block.sum(axis=1)).ravel()
            if np.any(depth <= 0) or not np.isfinite(depth).all():
                raise engine.Refused('Normalized diagnostics need positive finite cell depths')
            record = records[context]
            previous = len(record['depths']); count = block.shape[0]
            batch_mean = np.asarray(block.sum(axis=0)).ravel() / count
            # Center within this bounded row block, then combine moments. E[X²]-E[X]²
            # would lose small real variance at large count offsets.
            centered = block.toarray() - batch_mean
            batch_m2 = np.einsum('ij,ij->j', centered, centered)
            delta = batch_mean - record['mean']
            record['mean'] += delta * (count / (previous + count))
            record['m2'] += batch_m2 + delta ** 2 * (previous * count / (previous + count))
            record['depths'].extend(depth.tolist())
            record['detected'] += np.asarray((block > 0).sum(axis=0)).ravel()
            record['normalized'] += np.asarray(block.multiply((params['normalization_total'] / depth)[:, None]).sum(axis=0)).ravel()
    for record in records.values():
        n = len(record['depths'])
        mean = record.pop('mean')
        variance = record.pop('m2') / n
        normalized = record.pop('normalized') / n
        zero = 1 - record.pop('detected') / n
        if not all(np.isfinite(a).all() for a in (mean, variance, normalized, zero)):
            raise engine.Refused('Count moments exceed finite numerical range')
        depth = np.asarray(record.pop('depths'))
        record.update(cells=n, mean=mean, variance=variance, normalized=normalized, zero=zero,
                      depth={'mean': float(depth.mean()), 'stdPopulation': float(depth.std()),
                             'quantiles': np.quantile(depth, [0, .05, .5, .95, 1]).tolist()})
    return genes.tolist(), records


def _residual_group(means, residual):
    count = len(residual)
    return {'genes': count,
            'referenceMeanRange': [float(means.min()), float(means.max())] if count else None,
            'meanSignedError': float(residual.mean()) if count else None,
            'meanAbsoluteError': float(np.abs(residual).mean()) if count else None,
            'excessZeros': int(np.count_nonzero(residual > 0)),
            'fewerZeros': int(np.count_nonzero(residual < 0)),
            'unchanged': int(np.count_nonzero(residual == 0))}


def _zero_residuals(prediction, reference):
    """Descriptive expression strata, with no hypothesis test or causal inference.

    Equal reference means always stay together. Empty quantile intervals remain
    visible; a tied or small axis must not look like ten equal-sized populations.
    """
    residual = prediction['zero'] - reference['zero']
    means = reference['mean']
    positive = means > 0
    x, y = means[positive], residual[positive]
    cuts = np.quantile(x, np.arange(1, 10) / 10) if len(x) else np.array([])
    membership = np.searchsorted(cuts, x, side='left')
    strata = [{'stratum': i + 1, **_residual_group(x[membership == i], y[membership == i])}
              for i in range(10)]
    correlation = None
    if len(x) >= 2 and np.ptp(x) > 0 and np.ptp(y) > 0:
        a, b = rankdata(x, method='average'), rankdata(y, method='average')
        a -= a.mean(); b -= b.mean()
        correlation = float(np.dot(a, b) / np.sqrt(np.dot(a, a) * np.dot(b, b)))
    return {'meaning': 'prediction zero fraction minus reference zero fraction',
            'allGenes': _residual_group(means, residual),
            'zeroReferenceMean': _residual_group(means[~positive], residual[~positive]),
            'positiveReferenceGenes': len(x),
            'referenceMeanRankCorrelation': correlation,
            'correlationMeaning': 'Average-rank correlation over positive-reference-mean genes; null for fewer than two genes or a constant axis. Descriptive, no p-value.',
            'stratification': 'Positive reference means; linear-interpolated decile cuts; ties at a cut go to the lower interval. Empty intervals are retained.',
            'referenceMeanQuantileCuts': cuts.tolist(), 'strata': strata}


def compare(inputs, params, output, check):
    import h5py
    if params['context_column'] == params['target_column']:
        raise engine.Refused('Context and population need distinct annotation columns')
    with ExitStack() as stack:
        files = {k: stack.enter_context(h5py.File(inputs[k], 'r')) for k in ('prediction', 'reference')}
        axes, records = {}, {}
        for key, file in files.items():
            axes[key], records[key] = _population(file, params, check)
    if axes['prediction'] != axes['reference'] or set(records['prediction']) != set(records['reference']):
        raise engine.Refused('Count diagnostics require identical ordered genes and complete context sets')
    details = []; summaries = []
    for context in sorted(records['reference']):
        check()
        prediction, reference = (records[k][context] for k in ('prediction', 'reference'))
        metrics = {}
        for key in ('mean', 'variance', 'normalized'):
            denominator = float(np.abs(reference[key]).sum())
            metrics[key + 'RelativeL1'] = float(np.abs(prediction[key] - reference[key]).sum() / denominator) if denominator else None
        metrics['zeroFractionMae'] = float(np.abs(prediction['zero'] - reference['zero']).mean())
        summaries.append({'context': context, 'predictionPopulation': prediction['population'],
                          'referencePopulation': reference['population'], 'metrics': metrics,
                          'predictionCells': prediction['cells'], 'referenceCells': reference['cells'],
                          'predictionDepth': prediction['depth'], 'referenceDepth': reference['depth'],
                          'zeroResiduals': _zero_residuals(prediction, reference)})
        details.append({'context': context, **{k: {name: row[name].tolist() for name in ('mean', 'variance', 'normalized', 'zero')}
                        for k, row in [('prediction', prediction), ('reference', reference)]}})
    summary = {'format': 'count-population-diagnostics-v1', 'genes': len(axes['reference']),
               'contexts': summaries, 'normalizationTotal': params['normalization_total'],
               'varianceConvention': 'population-second-central-moment', 'quantileProbabilities': [0, .05, .5, .95, 1],
               'competitionScore': False, 'distributionEqualityEstablished': False,
               'geneAxisSha256': hashlib.sha256('\n'.join(axes['reference']).encode()).hexdigest(),
               'detailsRetained': True}
    return engine._json(output / 'count-diagnostics.json', {**summary, 'geneAxis': axes['reference'], 'details': details}), summary
