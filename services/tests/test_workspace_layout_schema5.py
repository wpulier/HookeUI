"""Layout schema 5: output and compare Tabs, and table, analysis and graph windows.

Every schema-5 field is optional and accepted only in a schema-5 layout, so layouts 2-4
round-trip unchanged and an older client is told to reload. Real journal, no camera.
"""
import asyncio
import copy
import json
from types import SimpleNamespace

import pytest

from orchestrator import workspace_views as w
from orchestrator.commands import workspace_layout
from orchestrator.day import DayStore


def item(day, placement, rid='e1', presentation='masks'):
    value = {'placementId': placement, 'resource': {'day': day.sid, 'id': rid, 'version': 1, 'picture': None if presentation in ('datatable', 'analysis', 'graph') else 0},
             'title': f'Result {placement}', 'zoom': 1, 'pan': [.5, .5]}
    if presentation is not None:
        value['presentation'] = presentation
    return value


def tab(tid, title, items=()):
    return {'id': tid, 'title': title, 'specimen': {'visible': True, 'rect': None}, 'items': list(items), 'notes': []}


def older(day, schema):
    main = tab('main', 'Main', [item(day, 'p-main')])
    if schema == 3:
        main['items'][0]['display'] = {'background': False, 'sort': 'area', 'direction': 'desc'}
    if schema == 4:
        main['input'] = {'source': 'a7', 'position': 2.5}
    return {'schema': schema, 'coordinates': 'stage', 'views': [main, tab('second', 'Second', [item(day, 'p-second', 't2', 'table')])]}


def schema5(day):
    output = tab('o1', 'Researcher 2 · results', [item(day, 'p-out', 'r1', None)])
    output.update(kind='output', provenance={'researcher': 'r1', 'version': 2, 'run': 'run-7', 'from': 'main'})
    lanes = [item(day, 'lane-a', 'e2'), item(day, 'lane-b', 'e3'), item(day, 'lane-c', 'e4')]
    compare = tab('c1', 'Compare models', lanes)
    compare.update(kind='compare', compare={'sync': True, 'lanes': [
        {'placementId': 'lane-a', 'label': 'A', 'impl': 'cellpose-sam'},
        {'placementId': 'lane-b', 'label': 'B', 'impl': 'stardist'},
        {'placementId': 'lane-c', 'label': 'C', 'impl': 'sam-3.1'}]})
    tables = tab('tables', 'Counting', [item(day, 'p-table', 'tb1', 'datatable'),
                                        item(day, 'p-analysis', 'an1', 'analysis'),
                                        item(day, 'p-graph', 'gr1', 'graph')])
    main = tab('main', 'Main')
    main['input'] = {'source': 'live', 'position': 0}
    return {'schema': 5, 'coordinates': 'stage', 'views': [main, output, compare, tables]}


def stored_schema(day):
    events = [e for e in day.journal.load() if e.get('type', '').startswith('workspace-') and 'layout' in e]
    return events[-1]['layout']['schema']


@pytest.mark.parametrize('schema', [2, 3, 4])
def test_older_layouts_round_trip_unchanged(tmp_path, schema):
    day = DayStore(root=tmp_path / 'sessions')
    layout = older(day, schema)
    w.save(day, layout, 0)
    reopened = DayStore(root=day.root, day=day.sid)
    for client in range(schema, w.SCHEMA + 1):
        assert w.read(reopened, schema=client)['layout'] == layout
    assert stored_schema(reopened) == schema
    # Server writes keep the stored format: a removed and restored Tab, and a run's completion.
    removed = w.arrangement(reopened, 'remove', 'second', 1)
    restored = w.arrangement(reopened, 'restore', 'second', removed['revision'])
    assert restored['layout'] == layout and stored_schema(reopened) == schema
    origin = {'session': day.sid, 'tab': 'main', 'placementId': 'pending', 'run': 'run'}
    w.admit(reopened, origin)
    assert w.complete(reopened, origin, 'execution', item(day, 'ignored', 'e9'))['state'] == 'placed'
    assert w.read(reopened, schema=schema)['layout']['schema'] == schema
    assert stored_schema(reopened) == schema


def test_schema5_fields_are_accepted_and_replayed(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    layout = schema5(day)
    assert w.save(day, layout, 0)['layout'] == layout
    reopened = DayStore(root=day.root, day=day.sid)
    assert w.read(reopened, schema=5)['layout'] == layout
    # A completion still lands in a schema-5 Tab and keeps its kind and lanes.
    origin = {'session': day.sid, 'tab': 'c1', 'placementId': 'pending', 'run': 'run'}
    w.admit(reopened, origin)
    assert w.complete(reopened, origin, 'execution', item(day, 'ignored', 'e9'))['state'] == 'placed'
    after = w.read(reopened, schema=5)['layout']
    assert after['views'][2]['kind'] == 'compare' and after['views'][2]['compare'] == layout['views'][2]['compare']
    assert stored_schema(reopened) == 5


def test_a_schema4_layout_moves_to_schema5_when_it_writes_a_schema5_field(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    layout = older(day, 4)
    w.save(day, layout, 0)
    changed = copy.deepcopy(layout)
    changed['views'][1]['kind'] = 'output'
    with pytest.raises(ValueError, match='invalid tab fields'):
        w.save(day, changed, 1)
    changed['schema'] = 5
    w.save(day, changed, 1)
    assert w.read(day, schema=5)['layout'] == changed
    migration = [e for e in day.journal.load() if e.get('type') == 'workspace-layout'][-1]['migration']
    assert migration == {'from': 4}


@pytest.mark.parametrize('schema', [2, 3, 4])
@pytest.mark.parametrize('field,value', [('kind', 'output'), ('provenance', {'researcher': 'r1'}),
    ('compare', {'sync': False, 'lanes': []})])
def test_schema5_tab_fields_are_refused_in_older_layouts(tmp_path, schema, field, value):
    day = DayStore(root=tmp_path / 'sessions')
    layout = older(day, schema)
    layout['views'][0][field] = value
    with pytest.raises(ValueError, match='invalid tab fields'):
        w.checked_layout(layout, day.sid)


@pytest.mark.parametrize('schema', [2, 3, 4])
@pytest.mark.parametrize('presentation', ['datatable', 'analysis', 'graph'])
def test_schema5_presentations_are_refused_in_older_layouts(tmp_path, schema, presentation):
    day = DayStore(root=tmp_path / 'sessions')
    layout = older(day, schema)
    layout['views'][0]['items'][0]['presentation'] = presentation
    with pytest.raises(ValueError, match='unknown representation'):
        w.checked_layout(layout, day.sid)


def edit(day, change):
    layout = schema5(day)
    change({v['id']: v for v in layout['views']})
    return layout


def lanes(views):
    return views['c1']['compare']['lanes']


REFUSED = [
    # Tab kinds
    (lambda v: v['o1'].update(kind='researcher'), 'tab kind'),
    (lambda v: v['o1'].update(kind=''), 'tab kind'),
    (lambda v: v['o1'].update(kind=None), 'tab kind'),
    (lambda v: v['o1'].update(kind=['output']), 'tab kind'),
    # Provenance
    (lambda v: v['o1'].update(provenance='r1'), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'researcher': 'r1', 'pixels': [1]}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'researcher': ''}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'researcher': 'r' * 129}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'run': 7}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'from': ' '}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'version': 0}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'version': True}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'version': 1.5}), 'tab provenance'),
    (lambda v: v['o1'].update(provenance={'version': 10 ** 10}), 'tab provenance'),
    # Compare
    (lambda v: v['c1'].update(kind='output'), 'only to a compare Tab'),
    (lambda v: v['c1'].pop('kind'), 'only to a compare Tab'),
    (lambda v: v['c1'].update(compare=[]), 'compare settings'),
    (lambda v: v['c1']['compare'].update(sync=1), 'compare settings'),
    (lambda v: v['c1']['compare'].pop('sync'), 'compare settings'),
    (lambda v: v['c1']['compare'].update(zoom=2), 'compare settings'),
    (lambda v: v['c1']['compare'].update(lanes=lanes(v)[:1]), '2 or 3 lanes'),
    (lambda v: v['c1']['compare'].update(lanes=lanes(v) + [dict(lanes(v)[0], label='D')]), '2 or 3 lanes'),
    (lambda v: v['c1']['compare'].update(lanes='A,B'), '2 or 3 lanes'),
    (lambda v: lanes(v)[0].update(label='D'), 'lane label'),
    (lambda v: lanes(v)[0].update(label='a'), 'lane label'),
    (lambda v: lanes(v)[1].update(label='A'), 'lane label'),
    (lambda v: lanes(v)[0].update(placementId='p-table'), "this Tab's windows"),
    (lambda v: lanes(v)[0].update(placementId='missing'), "this Tab's windows"),
    (lambda v: lanes(v)[1].update(placementId='lane-a'), 'different window'),
    (lambda v: lanes(v)[0].update(impl=''), 'lane implementation'),
    (lambda v: lanes(v)[0].update(impl='m' * 129), 'lane implementation'),
    (lambda v: lanes(v)[0].update(impl=None), 'lane implementation'),
    (lambda v: lanes(v)[0].update(pixels=[1]), 'lane fields'),
    (lambda v: lanes(v)[0].pop('impl'), 'lane fields'),
    (lambda v: lanes(v).__setitem__(0, 'lane-a'), 'lane fields'),
    # Presentations
    (lambda v: v['tables']['items'][0].update(presentation='chart'), 'unknown representation'),
    (lambda v: v['tables']['items'][0].update(presentation='Graph'), 'unknown representation'),
]


@pytest.mark.parametrize('change,reason', REFUSED)
def test_invalid_schema5_fields_are_refused_with_a_reason(tmp_path, change, reason):
    day = DayStore(root=tmp_path / 'sessions')
    w.save(day, schema5(day), 0)
    with pytest.raises(ValueError, match=reason):
        w.save(day, edit(day, change), 1)
    assert w.read(day, schema=5)['layout'] == schema5(day)


def test_schema5_stays_inside_the_layout_size_bound(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    layout = schema5(day)
    assert len(json.dumps(layout).encode()) < 262144
    layout['views'] = [dict(copy.deepcopy(layout['views'][1]), id=f'o{n}', items=[item(day, f'p{n}', 'r1', None)])
                       for n in range(25)]
    with pytest.raises(ValueError, match='between 1 and 24 tabs'):
        w.checked_layout(layout, day.sid)


def test_an_older_client_is_told_to_reload(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    w.save(day, schema5(day), 0)
    for schema in (1, 2, 3, 4):
        with pytest.raises(w.WorkspaceError, match='Reload this app: the Project uses a newer layout format.'):
            w.read(day, schema=schema)
    app = SimpleNamespace(day=day)
    reply = asyncio.run(workspace_layout(app, None, {'session': day.sid, 'schema': 4}))
    assert reply['ok'] is False and reply['code'] == 'unsupported-schema'
    assert reply['error'] == 'Reload this app: the Project uses a newer layout format.'
    # An older client saving its own schema-4 draft over the schema-5 layout is refused the same way.
    draft = older(day, 4)
    reply = asyncio.run(workspace_layout(app, None, {'session': day.sid, 'schema': 4, 'layout': draft, 'base': 1}))
    assert reply['ok'] is False and reply['code'] == 'unsupported-schema'
    assert w.read(day, schema=5)['layout'] == schema5(day)
    # A schema-5 layout sent by a client that says it understands only schema 4 is refused too.
    fresh = DayStore(root=tmp_path / 'other')
    reply = asyncio.run(workspace_layout(SimpleNamespace(day=fresh), None,
                                         {'session': fresh.sid, 'schema': 4, 'layout': schema5(fresh), 'base': 0}))
    assert reply['ok'] is False and reply['code'] == 'unsupported-schema'
    assert w.read(fresh, schema=5)['revision'] == 0


def test_a_newer_than_known_schema_is_refused(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    layout = schema5(day)
    layout['schema'] = w.SCHEMA + 1
    with pytest.raises(w.WorkspaceError, match='Reload this app'):
        w.checked_layout(layout, day.sid)
    with pytest.raises(w.WorkspaceError, match='Reload this app'):
        w.read(day, schema=w.SCHEMA + 1)
