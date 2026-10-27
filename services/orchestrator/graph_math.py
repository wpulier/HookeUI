"""D11 scientific graph values, computed from one retained table snapshot off the live loop.

The renderer positions these numbers; it never bins, fits, estimates or samples the data.
No significance threshold or test is chosen here. A graph retains every selected row ID.
"""
from __future__ import annotations

import json

import numpy as np

from .datatables import DataTableRefused
from .stats import describe
from .table_selection import selected_rows

KINDS = ('histogram', 'box', 'violin', 'bar', 'scatter')
MAX_POINTS = 20_000
MAX_GROUPS = 12


def _axis(column: dict) -> dict:
    return {'key': column['key'], 'label': column.get('label') or column['key'], 'unit': column.get('unit') or ''}


def graph(snapshot: dict, spec: dict, *, analysis: dict | None = None) -> dict:
    """Build finite scientific values; callers own exact snapshot/spec persistence and admission."""
    kind = spec.get('kind')
    if kind not in KINDS:
        raise DataTableRefused('choose histogram, box, violin, bar or scatter', next='choose a graph type')
    selected = selected_rows(snapshot, spec)
    groups = selected['groups']
    if len(groups) > MAX_GROUPS or sum(g['n'] for g in groups) > MAX_POINTS:
        raise DataTableRefused('this graph exceeds its supported groups or point budget',
                               next='choose at most 12 groups and 20000 retained rows')
    if not any(g['n'] for g in groups):
        raise DataTableRefused('no finite included rows remain for this graph', next='review the columns and exclusions')
    columns = selected['columns']
    value, x, y = spec.get('value'), spec.get('x'), spec.get('y')
    if kind == 'scatter':
        if not x or not y:
            raise DataTableRefused('a scatter graph needs two numeric columns', next='choose X and Y')
        axes = {'x': _axis(columns[x]), 'y': _axis(columns[y])}
    else:
        if not value or x or y:
            raise DataTableRefused('this graph needs one numeric value column', next='choose its measured value')
        measured = _axis(columns[value])
        axes = ({'x': measured, 'y': {'key': 'count', 'label': 'Count', 'unit': ''}} if kind == 'histogram'
                else {'x': {'key': spec.get('group') or '', 'label': 'Group', 'unit': ''}, 'y': measured})
    bins = spec.get('bins', 20)
    if kind == 'histogram' and (type(bins) is not int or not 1 <= bins <= 200):
        raise DataTableRefused('histograms support 1 to 200 bins', next='choose a whole number of bins')
    error = spec.get('error', 'sd')
    if kind == 'bar' and error not in ('sd', 'sem', 'ci95'):
        raise DataTableRefused('error bars are SD, SEM or a 95% t interval', next='choose the uncertainty to display')
    edges = None
    if kind == 'histogram':
        all_values = np.asarray([r[value] for g in groups for r in g['rows']], dtype=float)
        try:
            edges = np.histogram_bin_edges(all_values, bins=bins)
        except (ValueError, OverflowError):
            raise DataTableRefused('these values cannot be binned in the supported numeric range',
                                   next='choose another measured column') from None
    drawn = []
    for group in groups:
        rows = group['rows']
        item = {'label': group['label'], 'n': len(rows), 'points': [], 'summary': None}
        if kind == 'scatter':
            item['points'] = [{'row': r['row'], 'x': r[x], 'y': r[y]} for r in rows]
        else:
            values = np.asarray([r[value] for r in rows], dtype=float)
            summary = describe(values)
            item['summary'] = summary
            item['points'] = [{'row': r['row'], 'y': r[value]} for r in rows]
            if kind == 'histogram':
                counts, _ = np.histogram(values, bins=edges)
                item['bins'] = [{'low': float(lo), 'high': float(hi), 'count': int(n)}
                                for lo, hi, n in zip(edges[:-1], edges[1:], counts)]
            elif kind == 'box' and len(values):
                iqr = summary['q3'] - summary['q1']
                inside = values[(values >= summary['q1'] - 1.5 * iqr) & (values <= summary['q3'] + 1.5 * iqr)]
                item['whiskers'] = [float(inside.min()), float(inside.max())]
            elif kind == 'violin':
                if len(values) < 2 or summary['min'] == summary['max']:
                    item['density'] = []
                    item['densityUnavailable'] = 'A density needs at least two distinct finite values; individual values are shown.'
                else:
                    from scipy.stats import gaussian_kde
                    grid = np.linspace(summary['min'], summary['max'], 128)
                    try:
                        density = gaussian_kde(values, bw_method='scott')(grid)
                    except (ValueError, np.linalg.LinAlgError):
                        raise DataTableRefused('a density could not be estimated for these values',
                                               next='use a box graph to inspect the retained values') from None
                    item['density'] = [{'value': float(v), 'density': float(d)} for v, d in zip(grid, density)]
            elif kind == 'bar':
                if summary['mean'] is None or summary[error] is None:
                    item['interval'] = None
                    item['intervalUnavailable'] = 'At least two finite observations are needed for this uncertainty.'
                elif error == 'ci95':
                    item['interval'] = summary['ci95']
                else:
                    item['interval'] = [summary['mean'] - summary[error], summary['mean'] + summary[error]]
        drawn.append(item)
    annotations = []
    if analysis is not None:
        if kind not in ('box', 'violin', 'bar') or analysis.get('selection') != selected['selection']:
            raise DataTableRefused('the saved analysis must use this graph’s exact value and group selection',
                                   next='choose a matching saved analysis or omit annotations')
        observed = {json.dumps(g['label']): [r['row'] for r in g['rows']] for g in groups}
        if any(observed.get(json.dumps(g['label'])) != g['rows'] for g in analysis.get('groups', [])):
            raise DataTableRefused('the saved analysis used different included observations',
                                   next='graph those observations or omit annotations')
        pairs = analysis.get('pairs') or []
        if not pairs and len(analysis.get('groups', [])) == 2 and analysis.get('p') is not None:
            pairs = [{'groups': [g['label'] for g in analysis['groups']], 'p': analysis['p'],
                      'adjustment': 'unadjusted, selected two-group test'}]
        for pair in pairs:
            p = pair.get('pAdjusted') if pair.get('pAdjusted') is not None else pair.get('p')
            if p is not None:
                annotations.append({'groups': pair['groups'], 'p': p,
                                    'method': analysis['method'], 'adjustment': pair.get('adjustment')})
    result = {'schema': 1, 'kind': kind, 'axes': axes, 'groups': drawn, 'annotations': annotations,
              'selection': selected['selection'], 'excluded': selected['excluded'],
              'error': error if kind == 'bar' else None,
              'definitions': {'box': 'Linear quartiles; whiskers reach observations within 1.5 IQR.',
                              'violin': 'Gaussian kernel density, Scott bandwidth, evaluated at 128 points within the observed range.',
                              'histogram': 'Common equal-width bin edges across groups; final bin includes its upper edge.',
                              'bar': 'Arithmetic mean with the selected SD, SEM or 95% Student-t interval; individual observations retained.',
                              'scatter': 'Included paired X/Y values from each retained row; no fit is implied.'}[kind]}
    try:
        json.dumps(result, allow_nan=False)
    except (ValueError, OverflowError):
        raise DataTableRefused('graph calculations exceed the supported finite numeric range',
                               next='choose another measured column') from None
    return result
