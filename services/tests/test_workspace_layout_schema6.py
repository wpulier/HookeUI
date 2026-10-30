"""Layout schema 6: the reader Tab (ticket 66, S6), one thing of Data read full width with the specimen hidden.

Schema 5 layouts (and 2-4) load and round-trip unchanged; a reader Tab is accepted only in a schema-6 layout, and a
client that knows only schema 5 is told to reload rather than shown a layout it would rewrite. Real journal, no camera.
"""
import copy
import json

import pytest

from orchestrator import workspace_views as w
from orchestrator.day import DayStore
from tests.test_workspace_layout_schema5 import item, older, schema5, stored_schema, tab


def reader(day, tid='reader-1', rid='r1:table-1', **extra):
    view = {'id': tid, 'title': 'Region areas', 'kind': 'reader', 'specimen': {'visible': False, 'rect': None},
            'items': [{'resource': {'day': day.sid, 'id': rid, 'version': 2}, 'title': 'Region areas', **extra}],
            'notes': []}
    return view


def schema6(day):
    layout = schema5(day)
    layout['schema'] = 6
    layout['views'].append(reader(day))
    return layout


def test_the_reader_tab_kind_is_declared_beside_output_and_compare():
    assert w.SCHEMA >= 6 and 6 in w.SCHEMAS and w.TAB_KINDS == ('output', 'compare', 'reader')


@pytest.mark.parametrize('schema', [2, 3, 4, 5])
def test_older_layouts_load_unchanged_by_a_schema6_reader(tmp_path, schema):
    day = DayStore(root=tmp_path / 'sessions')
    layout = schema5(day) if schema == 5 else older(day, schema)
    w.save(day, layout, 0)
    reopened = DayStore(root=day.root, day=day.sid)
    assert w.read(reopened, schema=6)['layout'] == layout
    assert w.read(reopened, schema=schema)['layout'] == layout
    assert stored_schema(reopened) == schema


def test_a_reader_tab_is_saved_and_read_back_as_it_was(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    layout = schema6(day)
    assert w.save(day, layout, 0)['layout'] == layout
    reopened = DayStore(root=day.root, day=day.sid)
    assert w.read(reopened, schema=6)['layout'] == layout and stored_schema(reopened) == 6
    # Its item may carry a placement identity too, as every other window does.
    with_placement = schema6(day)
    with_placement['views'][-1] = reader(day, placementId='p-reader')
    assert w.checked_layout(with_placement, day.sid) == with_placement


def test_a_schema5_layout_moves_to_schema6_when_it_adds_a_reader_tab(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    w.save(day, schema5(day), 0)
    changed = copy.deepcopy(schema5(day))
    changed['views'].append(reader(day))
    with pytest.raises(ValueError, match='schema 6'):
        w.save(day, changed, 1)
    changed['schema'] = 6
    w.save(day, changed, 1)
    assert w.read(day, schema=6)['layout'] == changed
    migration = [e for e in day.journal.load() if e.get('type') == 'workspace-layout'][-1]['migration']
    assert migration == {'from': 5}


def test_a_client_that_knows_only_schema5_is_told_to_reload_never_given_the_newer_layout(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    w.save(day, schema6(day), 0)
    with pytest.raises(w.WorkspaceError, match='newer layout format') as refused:
        w.read(day, schema=5)
    assert refused.value.code == 'unsupported-schema'


def test_server_writes_keep_a_schema6_layout_and_its_reader(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    layout = schema6(day)
    w.save(day, layout, 0)
    removed = w.arrangement(day, 'remove', 'reader-1', 1)
    restored = w.arrangement(day, 'restore', 'reader-1', removed['revision'])
    assert restored['layout'] == layout and stored_schema(day) == 6
    # A completion lands in its own Tab; a reader Tab never takes a window (it reads one thing).
    origin = {'session': day.sid, 'tab': 'main', 'placementId': 'pending', 'run': 'run'}
    w.admit(day, origin)
    assert w.complete(day, origin, 'execution', item(day, 'ignored', 'e9'))['state'] == 'placed'
    into_reader = {'session': day.sid, 'tab': 'reader-1', 'placementId': 'pending-2', 'run': 'run-2'}
    w.admit(day, into_reader)
    kept = w.complete(day, into_reader, 'execution-2', item(day, 'ignored-2', 'e10'))
    assert kept['state'] == 'available'
    assert next(v for v in w.read(day, schema=6)['layout']['views'] if v['id'] == 'reader-1') == reader(day)


READER_REFUSED = [
    (lambda v: v.update(specimen={'visible': True, 'rect': None}), 'hides the specimen'),
    (lambda v: v.update(items=[]), 'exactly one item'),
    (lambda v: v.update(items=v['items'] * 2), 'exactly one item'),
    (lambda v: v.update(notes=[{'id': 'a' + '0' * 12, 'rect': [0, 0, 1, 1], 'placementId': 'n1'}]), 'exactly one item'),
    (lambda v: v.update(input={'source': 'live', 'position': 0}), 'one item only'),
    (lambda v: v.update(compare={'sync': True, 'lanes': []}), 'one item only'),
    (lambda v: v.update(provenance={'researcher': 'r1'}), 'one item only'),
    (lambda v: v['items'][0].update(zoom=1), 'its resource and title'),
    (lambda v: v['items'][0].update(presentation='masks'), 'its resource and title'),
    (lambda v: v['items'][0].pop('title'), 'its resource and title'),
    (lambda v: v['items'][0].update(title=''), 'bounded title'),
    (lambda v: v['items'][0]['resource'].pop('version'), 'day, id and version'),
    (lambda v: v['items'][0]['resource'].update(values=[1, 2]), 'day, id and version'),
    (lambda v: v['items'][0]['resource'].update(day='2001-01-01'), 'belong to this project'),
    (lambda v: v['items'][0]['resource'].update(version=-1), 'nonnegative'),
]


@pytest.mark.parametrize('change,reason', READER_REFUSED)
def test_an_invalid_reader_tab_is_refused_with_a_reason(tmp_path, change, reason):
    day = DayStore(root=tmp_path / 'sessions')
    w.save(day, schema6(day), 0)
    layout = schema6(day)
    change(layout['views'][-1])
    with pytest.raises(ValueError, match=reason):
        w.save(day, layout, 1)
    assert w.read(day, schema=6)['layout'] == schema6(day)


def test_a_reader_tab_kind_is_refused_in_a_schema5_layout(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    layout = schema5(day)
    layout['views'].append(reader(day))
    with pytest.raises(ValueError, match='schema 6'):
        w.checked_layout(layout, day.sid)
    layout['schema'] = 4
    with pytest.raises(ValueError, match='invalid tab fields'):
        w.checked_layout(layout, day.sid)


def test_schema6_stays_inside_the_layout_size_bound(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    assert len(json.dumps(schema6(day)).encode()) < 262144
    many = schema6(day)
    many['views'] = many['views'][:1] + [reader(day, tid=f'reader-{n}') for n in range(23)]
    assert w.checked_layout(many, day.sid) == many
