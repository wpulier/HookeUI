"""Bounded numeric conformance and explicit pairing/selection; synthetic data, no model or UI claims."""
import json
import math

import numpy as np
import pytest
from scipy import stats

from orchestrator.analysis_math import analyze
from orchestrator.datatables import DataTableRefused
from orchestrator.table_selection import selected_rows


def snapshot(groups):
    return {'columns': [{'key': 'value', 'type': 'number', 'unit': 'µm'}, {'key': 'group', 'type': 'text'},
                        {'key': 'pair', 'type': 'text'}, {'key': 'x', 'type': 'number', 'unit': 's'}],
            'rows': [{'row': f'{label}:{index}', 'group': label, 'value': value, 'x': index + 1, 'pair': str(index)}
                     for label, values in groups.items() for index, value in enumerate(values)]}


def test_welch_numbers_and_exclusions_are_one_population():
    table = snapshot({'A': [1., 2., 3., float('nan')], 'B': [4., 5., 6.]})
    result = analyze(table, {'method': 'welch', 'value': 'value', 'group': 'group', 'groups': ['A', 'B']})
    assert [group['n'] for group in result['groups']] == [3, 3]
    assert result['df'] == 4
    assert result['statistic'] == pytest.approx(-3 / math.sqrt(2 / 3), rel=1e-12)
    assert result['p'] == pytest.approx(stats.ttest_ind([1, 2, 3], [4, 5, 6], equal_var=False).pvalue, rel=1e-12)
    half = stats.t.ppf(.975, 4) * math.sqrt(2 / 3)
    assert result['effect']['ci95'] == pytest.approx([-3 - half, -3 + half], rel=1e-12)
    assert result['exclusions'] == [{'row': 'A:3', 'reason': 'missing or non-finite numeric value: value'}]
    json.dumps(result, allow_nan=False)


def test_paired_test_joins_explicit_keys_and_refuses_duplicates():
    table = snapshot({'A': [1., 4., 7., 100.], 'B': [2., 6., 8.]})
    table['rows'][4:] = list(reversed(table['rows'][4:]))
    spec = {'method': 'paired-t', 'value': 'value', 'group': 'group', 'groups': ['A', 'B'], 'pair': 'pair'}
    result = analyze(table, spec)
    assert [group['n'] for group in result['groups']] == [3, 3]
    assert result['statistic'] == pytest.approx(stats.ttest_rel([1, 4, 7], [2, 6, 8]).statistic, rel=1e-12)
    assert result['groups'][1]['rows'] == ['B:0', 'B:1', 'B:2']
    assert any(row['reason'].startswith('no matching') for row in result['exclusions'])
    with pytest.raises(DataTableRefused, match='explicit pair'):
        analyze(table, {key: value for key, value in spec.items() if key != 'pair'})
    table['rows'][1]['pair'] = '0'
    with pytest.raises(DataTableRefused, match='more than once'):
        analyze(table, spec)


def test_mann_whitney_ties_and_effect_direction():
    table = snapshot({'A': [1., 1., 2.], 'B': [4., 5., 5.]})
    result = analyze(table, {'method': 'mann-whitney', 'value': 'value', 'group': 'group'})
    expected = stats.mannwhitneyu([1, 1, 2], [4, 5, 5], method='asymptotic', alternative='two-sided')
    assert result['statistic'] == expected.statistic == 0
    assert result['p'] == pytest.approx(expected.pvalue, rel=1e-12)
    assert result['effect']['value'] == -1 and result['effect']['ci95'] == [-1, -1]


@pytest.mark.parametrize('method', ['anova-tukey', 'kruskal-dunn'])
def test_omnibus_and_all_declared_pair_adjustments(method):
    groups = [[1., 2.], [3., 4.], [5., 6.]]
    result = analyze(snapshot(dict(zip('ABC', groups))), {'method': method, 'value': 'value', 'group': 'group'})
    expected = stats.f_oneway(*groups) if method == 'anova-tukey' else stats.kruskal(*groups)
    assert result['statistic'] == pytest.approx(expected.statistic, rel=1e-12)
    assert result['p'] == pytest.approx(expected.pvalue, rel=1e-12)
    assert [pair['groups'] for pair in result['pairs']] == [['A', 'B'], ['A', 'C'], ['B', 'C']]
    if method == 'anova-tukey':
        assert result['effect']['value'] == pytest.approx(16 / 17.5)
        assert result['pairs'][0]['pAdjusted'] == pytest.approx(stats.tukey_hsd(*groups).pvalue[0, 1], rel=1e-12)
    else:
        assert result['effect']['name'] == 'eta squared H'
        raw_close = 2 * stats.norm.sf(2 / math.sqrt(3.5))
        raw_far = 2 * stats.norm.sf(4 / math.sqrt(3.5))
        assert [pair['p'] for pair in result['pairs']] == pytest.approx([raw_close, raw_far, raw_close], rel=1e-12)
        assert [pair['pAdjusted'] for pair in result['pairs']] == pytest.approx([2 * raw_close, 3 * raw_far, 2 * raw_close], rel=1e-12)
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('method', ['pearson', 'linear-regression'])
def test_xy_analysis_reports_known_slope_and_correlation(method):
    result = analyze(snapshot({'A': [3., 5., 7., 10.]}), {'method': method, 'x': 'x', 'y': 'value'})
    x, y = [1, 2, 3, 4], [3, 5, 7, 10]
    if method == 'pearson':
        assert result['effect']['value'] == pytest.approx(stats.pearsonr(x, y).statistic, rel=1e-12)
    else:
        assert result['effect']['value'] == pytest.approx(2.3, rel=1e-12)
        assert result['regression']['intercept'] == pytest.approx(.5, rel=1e-12)
        assert result['p'] == pytest.approx(stats.linregress(x, y).pvalue, rel=1e-12)
        residuals = np.asarray(y) - (.5 + 2.3 * np.asarray(x))
        fact = result['assumptions']['shapiro'][0]
        assert fact['group'] == 'regression residuals'
        assert fact['p'] == pytest.approx(stats.shapiro(residuals).pvalue, rel=1e-12)
    assert result['assumptions']['sdRatio'] is None
    json.dumps(result, allow_nan=False)


def test_shared_selection_keeps_user_exclusions_and_does_not_parse_strings():
    table = snapshot({'A': [1., 2., '3', True], 'B': [5.]})
    table['rows'][1]['excluded'] = 'out of focus'
    selected = selected_rows(table, {'value': 'value', 'group': 'group', 'groups': ['A']})
    assert selected['groups'][0]['n'] == 1
    assert selected['groups'][0]['rows'][0]['row'] == 'A:0'
    assert [row['reason'] for row in selected['excluded']] == ['out of focus',
        'missing or non-finite numeric value: value', 'missing or non-finite numeric value: value', 'group not selected']
