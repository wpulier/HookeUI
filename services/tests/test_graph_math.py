"""Synthetic numeric conformance of retained graph values, not UI/scientific qualification."""
import json
import math

import pytest

from orchestrator.datatables import DataTableRefused
from orchestrator.graph_math import graph


def table():
    return {'columns': [{'key': 'v', 'label': 'Area', 'type': 'number', 'unit': 'µm²'},
                        {'key': 'x', 'label': 'Time', 'type': 'number', 'unit': 's'},
                        {'key': 'group', 'type': 'text'}],
            'rows': [{'row': f'r{i}', 'v': v, 'x': i, 'group': 'A' if i < 4 else 'B'}
                     for i, v in enumerate([1., 2., 3., 4., 10., 10.])]}


def test_histogram_common_edges_and_final_boundary_preserve_counts():
    source = table()
    source['rows'][1]['excluded'] = 'out of focus'
    result = graph(source, {'kind': 'histogram', 'value': 'v', 'group': 'group', 'bins': 3})
    assert [[b['count'] for b in g['bins']] for g in result['groups']] == [[2, 1, 0], [0, 0, 2]]
    assert [(b['low'], b['high']) for b in result['groups'][0]['bins']] == [(1, 4), (4, 7), (7, 10)]
    assert result['excluded'] == [{'row': 'r1', 'reason': 'out of focus'}]
    assert result['axes']['x']['unit'] == 'µm²'
    assert result == graph(source, {'kind': 'histogram', 'value': 'v', 'group': 'group', 'bins': 3})


def test_box_quartiles_whiskers_and_actual_row_identities():
    result = graph(table(), {'kind': 'box', 'value': 'v', 'group': 'group'})
    group = result['groups'][0]
    assert [group['summary'][key] for key in ('q1', 'median', 'q3')] == [1.75, 2.5, 3.25]
    assert group['whiskers'] == [1, 4]
    assert [p['row'] for p in group['points']] == ['r0', 'r1', 'r2', 'r3']


@pytest.mark.parametrize('error,half', [('sd', math.sqrt(5 / 3)), ('sem', math.sqrt(5 / 12)),
                                      ('ci95', 3.182446305284263 * math.sqrt(5 / 12))])
def test_mean_uncertainty_is_declared_not_guessed(error, half):
    result = graph(table(), {'kind': 'bar', 'value': 'v', 'group': 'group', 'error': error})
    assert result['groups'][0]['interval'] == pytest.approx([2.5 - half, 2.5 + half], rel=1e-12)
    assert result['error'] == error


def test_violin_degenerate_group_retains_observations_without_fake_density():
    result = graph(table(), {'kind': 'violin', 'value': 'v', 'group': 'group'})
    assert len(result['groups'][0]['density']) == 128
    assert all(p['density'] > 0 for p in result['groups'][0]['density'])
    assert result['groups'][1]['density'] == []
    assert result['groups'][1]['n'] == len(result['groups'][1]['points']) == 2
    assert 'distinct' in result['groups'][1]['densityUnavailable']
    json.dumps(result, allow_nan=False)


def test_scatter_pairs_same_rows_and_refuses_invalid_inputs():
    source = table()
    source['rows'][0]['x'] = None
    result = graph(source, {'kind': 'scatter', 'x': 'x', 'y': 'v'})
    assert result['groups'][0]['points'][0] == {'row': 'r1', 'x': 1, 'y': 2}
    assert result['axes']['x']['unit'] == 's'
    assert result['excluded'][0]['row'] == 'r0'
    with pytest.raises(DataTableRefused, match='two numeric'):
        graph(source, {'kind': 'scatter', 'value': 'v'})
    with pytest.raises(DataTableRefused, match='1 to 200'):
        graph(source, {'kind': 'histogram', 'value': 'v', 'bins': True})
    for row in source['rows']:
        row['v'] = 1e308
    with pytest.raises(DataTableRefused, match='finite numeric range'):
        graph(source, {'kind': 'bar', 'value': 'v'})
