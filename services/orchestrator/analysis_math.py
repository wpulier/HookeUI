"""Explicit table analyses. Call off-loop; SciPy loads only when an analysis is requested.

The user selects the test, columns and ordered groups. P-values are two-sided; no
threshold or automatic test selection labels a finding. Nonparametric effect CIs
use a recorded deterministic percentile bootstrap, never a model estimate.
"""
from __future__ import annotations

import itertools
import math
import warnings

from .datatables import DataTableRefused
from .stats import describe
from .table_selection import selected_rows

METHODS = ('welch', 'paired-t', 'mann-whitney', 'anova-tukey', 'kruskal-dunn', 'pearson', 'linear-regression')
BOOTSTRAPS = 499
BOOTSTRAP_SEED = 20261023
MAX_BOOTSTRAP_ROWS = 5000


def _refuse(message):
    raise DataTableRefused(message, next='review the selected test, groups and columns')


def _holm(values):
    order = sorted(range(len(values)), key=lambda index: (values[index], index))
    adjusted, previous = [0.0] * len(values), 0.0
    for rank, index in enumerate(order):
        previous = max(previous, min(1.0, (len(values) - rank) * values[index]))
        adjusted[index] = previous
    return adjusted


def _bootstrap(arrays, statistic):
    import numpy as np
    if sum(len(array) for array in arrays) > MAX_BOOTSTRAP_ROWS:
        return None, f'CI unavailable: percentile bootstrap supports at most {MAX_BOOTSTRAP_ROWS} observations'
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    estimates = []
    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        for _ in range(BOOTSTRAPS):
            sampled = [array[rng.integers(0, len(array), size=len(array))] for array in arrays]
            value = float(statistic(sampled))
            if math.isfinite(value):
                estimates.append(value)
    if len(estimates) != BOOTSTRAPS:
        return None, 'CI unavailable: degenerate bootstrap samples'
    return [float(v) for v in np.percentile(estimates, [2.5, 97.5])], f'percentile bootstrap; {BOOTSTRAPS} resamples; seed {BOOTSTRAP_SEED}'


def _effect(name, value, ci, method):
    return {'name': name, 'value': float(value), 'ci95': ci, 'ciMethod': method}


def _pair_groups(groups, pair):
    """Exact within-group keys, never positional pairing or inferred sorting."""
    indexes = []
    for group in groups:
        index = {}
        for row in group['rows']:
            value = row.get(pair)
            if value is None or isinstance(value, (dict, list)) or (isinstance(value, float) and not math.isfinite(value)):
                group['excluded'].append({'row': row['row'], 'reason': 'missing pair key'})
                continue
            key = (type(value).__name__, value)
            if key in index:
                _refuse(f'pair key {value!r} occurs more than once in a group')
            index[key] = row
        indexes.append(index)
    common = [key for key in indexes[0] if key in indexes[1]]
    common_keys = set(common)
    for group, index in zip(groups, indexes):
        group['excluded'].extend({'row': row['row'], 'reason': 'no matching pair in the other group'}
                                 for key, row in index.items() if key not in common_keys)
        group['rows'] = [index[key] for key in common]
        group['n'] = len(common)


def analyze(snapshot: dict, spec: dict) -> dict:
    import numpy as np
    from scipy import stats

    method = spec.get('method')
    if method not in METHODS:
        _refuse('choose an explicitly supported analysis')
    selected = selected_rows(snapshot, spec)
    groups = selected['groups']
    columns = selected['columns']
    xy = method in ('pearson', 'linear-regression')
    if xy:
        if not spec.get('x') or not spec.get('y') or len(groups) != 1:
            _refuse('correlation and regression require x/y columns and one selected group')
    elif not spec.get('value'):
        _refuse('this analysis requires a value column')
    if not xy and (len(groups) != 2 if method in ('welch', 'paired-t', 'mann-whitney') else len(groups) < 2):
        _refuse('choose two groups for a two-group test, or at least two for an omnibus test')
    if method == 'paired-t':
        pair = spec.get('pair')
        if not isinstance(pair, str) or pair not in columns:
            _refuse('paired t requires an explicit pair-key column')
        _pair_groups(groups, pair)
    if any(group['n'] < (3 if xy else 2) for group in groups):
        _refuse('each selected group needs at least two usable observations; correlation/regression needs three')
    arrays = [np.asarray([row[spec['value']] for row in group['rows']], dtype=float) for group in groups] if not xy else [
        np.asarray([row[key] for row in groups[0]['rows']], dtype=float) for key in (spec['x'], spec['y'])]
    if any(not np.isfinite(array).all() for array in arrays):
        _refuse('the selected observations exceed the supported numeric range')
    labels = [group['label'] for group in groups]
    result = {'implementation': 'table-analysis-v1', 'method': method, 'selection': selected['selection'],
              'units': {key: columns[key].get('unit', '') for key in (spec['x'], spec['y'])} if xy else {spec['value']: columns[spec['value']].get('unit', '')}, 'groups': [
        {'label': group['label'], 'n': group['n'], 'rows': [row['row'] for row in group['rows']],
         'excluded': group['excluded'], 'summary': describe(arrays[index]) if not xy else
         {spec['x']: describe(arrays[0]), spec['y']: describe(arrays[1])}}
        for index, group in enumerate(groups)],
        'exclusions': selected['excluded'], 'statistic': None, 'df': None, 'p': None,
        'effect': None, 'pairs': [], 'assumptions': {}, 'notes': ['Two-sided tests; 95% confidence intervals.']}
    if method == 'paired-t':
        result['exclusions'] = selected['excluded'] + [fact for group in groups for fact in group['excluded']
                                                       if fact not in selected['excluded']]
        result['pair'] = spec['pair']

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', RuntimeWarning)
        if method in ('welch', 'paired-t'):
            a, b = arrays
            test = stats.ttest_rel(a, b) if method == 'paired-t' else stats.ttest_ind(a, b, equal_var=False)
            interval = test.confidence_interval(confidence_level=.95)
            result.update(statistic=float(test.statistic), df=float(test.df), p=float(test.pvalue),
                          effect=_effect('mean difference (first minus second)', np.mean(a) - np.mean(b),
                                         [float(interval.low), float(interval.high)], 'paired t' if method == 'paired-t' else 'Welch t'))
        elif method == 'mann-whitney':
            a, b = arrays
            test = stats.mannwhitneyu(a, b, alternative='two-sided', method='asymptotic')
            def effect(samples):
                left, right = samples
                u = stats.mannwhitneyu(left, right, alternative='two-sided', method='asymptotic').statistic
                return 2 * u / (len(left) * len(right)) - 1
            ci, how = _bootstrap(arrays, effect)
            result.update(statistic=float(test.statistic), p=float(test.pvalue),
                          effect=_effect('rank-biserial correlation (first minus second)', effect(arrays), ci, how))
            result['notes'].append('Mann–Whitney uses tie-corrected asymptotic p with continuity correction.')
        elif method == 'anova-tukey':
            test = stats.f_oneway(*arrays)
            n, k = sum(map(len, arrays)), len(arrays)
            def eta(samples):
                combined = np.concatenate(samples)
                mean = np.mean(combined)
                total = np.sum((combined - mean) ** 2)
                return sum(len(a) * (np.mean(a) - mean) ** 2 for a in samples) / total if total else float('nan')
            ci, how = _bootstrap(arrays, eta)
            result.update(statistic=float(test.statistic), df=[k - 1, n - k], p=float(test.pvalue),
                          effect=_effect('eta squared', eta(arrays), ci, how))
            post = stats.tukey_hsd(*arrays)
            interval = post.confidence_interval(confidence_level=.95)
            for i, j in itertools.combinations(range(k), 2):
                result['pairs'].append({'groups': [labels[i], labels[j]], 'statistic': float(post.statistic[i, j]),
                    'p': None, 'pAdjusted': float(post.pvalue[i, j]), 'adjustment': 'Tukey HSD',
                    'effect': _effect('mean difference (first minus second)', post.statistic[i, j],
                                      [float(interval.low[i, j]), float(interval.high[i, j])], 'Tukey simultaneous interval')})
        elif method == 'kruskal-dunn':
            combined = np.concatenate(arrays)
            if np.ptp(combined) == 0:
                _refuse('Kruskal–Wallis is undefined when every selected value is identical')
            test = stats.kruskal(*arrays)
            n, k = len(combined), len(arrays)
            def epsilon(samples):
                try:
                    h = stats.kruskal(*samples).statistic
                except ValueError:
                    return float('nan')
                return (h - k + 1) / (sum(map(len, samples)) - k)
            ci, how = _bootstrap(arrays, epsilon)
            result.update(statistic=float(test.statistic), df=k - 1, p=float(test.pvalue),
                          effect=_effect('eta squared H', epsilon(arrays), ci, how))
            ranks = stats.rankdata(combined)
            _, ties = np.unique(combined, return_counts=True)
            variance = n * (n + 1) / 12 - np.sum(ties ** 3 - ties) / (12 * (n - 1))
            means, offset = [], 0
            for array in arrays:
                means.append(float(np.mean(ranks[offset:offset + len(array)])))
                offset += len(array)
            for i, j in itertools.combinations(range(k), 2):
                z = (means[i] - means[j]) / math.sqrt(variance * (1 / len(arrays[i]) + 1 / len(arrays[j])))
                result['pairs'].append({'groups': [labels[i], labels[j]], 'statistic': float(z),
                    'p': float(2 * stats.norm.sf(abs(z))), 'pAdjusted': None, 'adjustment': 'Dunn, Holm', 'effect': None})
            for pair, p in zip(result['pairs'], _holm([pair['p'] for pair in result['pairs']])):
                pair['pAdjusted'] = p
        elif method == 'pearson':
            x, y = arrays
            if np.ptp(x) == 0 or np.ptp(y) == 0:
                _refuse('correlation requires variation in both selected columns')
            test = stats.pearsonr(x, y)
            interval = test.confidence_interval(confidence_level=.95)
            result.update(statistic=float(test.statistic), df=len(x) - 2, p=float(test.pvalue),
                          effect=_effect('Pearson r', test.statistic, [float(interval.low), float(interval.high)], 'Fisher transformation'))
        else:
            x, y = arrays
            if np.ptp(x) == 0:
                _refuse('regression requires variation in x')
            test = stats.linregress(x, y)
            half = float(stats.t.ppf(.975, len(x) - 2)) * test.stderr
            t = test.slope / test.stderr if test.stderr else float('nan')
            result.update(statistic=t, df=len(x) - 2, p=float(test.pvalue),
                          effect=_effect('slope', test.slope, [test.slope - half, test.slope + half], 'Student t slope interval'),
                          regression={'intercept': float(test.intercept), 'r': float(test.rvalue), 'rSquared': float(test.rvalue ** 2),
                                      'slopeSE': float(test.stderr), 'interceptSE': float(test.intercept_stderr),
                                      'xUnit': columns[spec['x']].get('unit', ''), 'yUnit': columns[spec['y']].get('unit', '')})

        # Facts accompany the selected test; they do not choose another test or declare a result.
        checked = [arrays[0] - arrays[1]] if method == 'paired-t' else arrays
        if method == 'linear-regression':
            checked = [arrays[1] - (test.intercept + test.slope * arrays[0])]
        shapiro = []
        for index, array in enumerate(checked):
            if not 3 <= len(array) <= 5000 or np.ptp(array) == 0:
                shapiro.append({'group': 'regression residuals' if method == 'linear-regression' else ('paired differences' if method == 'paired-t' else (['x', 'y'][index] if xy else labels[index])),
                                'statistic': None, 'p': None, 'reason': 'requires 3–5000 observations with variation'})
            else:
                value = stats.shapiro(array)
                shapiro.append({'group': 'regression residuals' if method == 'linear-regression' else ('paired differences' if method == 'paired-t' else (['x', 'y'][index] if xy else labels[index])),
                                'statistic': float(value.statistic), 'p': float(value.pvalue), 'reason': None})
        spreads = [float(np.std(array, ddof=1)) for array in arrays]
        levene = stats.levene(*arrays, center='median') if not xy and method != 'paired-t' else None
        result['assumptions'] = {'shapiro': shapiro,
            'levene': {'statistic': float(levene.statistic), 'p': float(levene.pvalue), 'center': 'median'} if levene else
                       {'statistic': None, 'p': None, 'reason': 'not an independent-group variance comparison'},
            'sdRatio': max(spreads) / min(spreads) if not xy and min(spreads) > 0 else None,
            'sdRatioReason': 'x/y axes are not a group variance comparison' if xy else (None if min(spreads) > 0 else 'a selected group has zero SD')}
    unavailable = []
    def plain(value, path='result'):
        if isinstance(value, dict):
            return {key: plain(item, f'{path}.{key}') for key, item in value.items()}
        if isinstance(value, list):
            return [plain(item, f'{path}[{index}]') for index, item in enumerate(value)]
        if isinstance(value, (float, np.floating)):
            if not math.isfinite(value):
                unavailable.append(path)
                return None
            return float(value)
        if isinstance(value, np.integer):
            return int(value)
        return value
    out = plain(result)
    if unavailable:
        out['notes'].append('Unavailable non-finite estimates (degenerate data or numeric range): ' + ', '.join(unavailable))
    return out
