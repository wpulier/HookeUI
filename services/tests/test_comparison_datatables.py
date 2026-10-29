"""Synthetic populations, real saved Compare Tab and table publisher; no scientific execution."""
import asyncio
from types import SimpleNamespace
import pytest
from orchestrator import workspace_views as w
from orchestrator.datatables import DataTables, DataTableRefused
from orchestrator.day import DayStore


@pytest.fixture
def comparison(tmp_path, monkeypatch):
    day = DayStore(root=tmp_path/'sessions')
    populations = {f'e{i}': SimpleNamespace(session=day.sid, revision=i, capture_id=10, view={'boot': 'camera-one', 'seq': 3, 'run': 'run-one'}, implementation=f'method{i}') for i in (1, 2)}
    app = SimpleNamespace(day=day, hub=SimpleNamespace(evidence=SimpleNamespace(get=populations.get)))
    service = DataTables(app, tmp_path/'tables')
    layout = w.read(day, schema=5)['layout']; layout['schema'] = 5
    view = layout['views'][0]; view['kind'] = 'compare'
    view['items'] = [{'placementId': f'place{i}', 'resource': {'day': day.sid, 'id': f'e{i}', 'version': i, 'picture': 0},
                      'title': f'Method {i}', 'pan': [.5, .5], 'zoom': 1, 'presentation': 'masks'} for i in (1, 2)]
    view['compare'] = {'sync': True, 'lanes': [{'placementId': f'place{i}', 'label': label, 'impl': f'method{i}'} for i, label in ((1, 'A'), (2, 'B'))]}
    w.save(day, layout, 0)
    async def read(app, eid, session, revision):
        return {'id': eid, 'kind': 'population', 'title': eid, 'revision': revision, 'columns': [
            {'key': 'cell', 'type': 'text', 'unit': ''}, {'key': 'area', 'type': 'number', 'unit': 'µm²'}],
                'rows': [{'cell': 'c1', 'area': 10.5 if eid == 'e1' else 12.25}]}
    monkeypatch.setattr('orchestrator.tables.read_table', read)
    # Pure synthetic members are sufficient: these tables have no correction lineage.
    monkeypatch.setattr(service, '_lineage', lambda sources: {})
    return app, service, populations


def test_from_comparison_keeps_exact_rows_methods_and_nonindependence_on_reopen(comparison):
    app, service, populations = comparison
    async def go():
        made = await service.from_comparison('main', 1)
        snapshot = service.read(made['id'])
        assert made['mode'] == 'frozen'
        assert [(s['id'], s['revision']) for s in made['sources']] == [('e1', 1), ('e2', 2)]
        assert [(r['row'], r['area'], r['method']) for r in snapshot['rows']] == [('e1:c1', 10.5, 'A · method1'), ('e2:c1', 12.25, 'B · method2')]
        assert 'not independent samples' in snapshot['comparison']['note']
        assert snapshot['comparison']['sourceRevisions'] == {'e1': 1, 'e2': 2}
        app.day = DayStore(root=app.day.root, day=app.day.sid)
        assert service.read(made['id']) == snapshot
    asyncio.run(go())


@pytest.mark.parametrize('change', ['layout', 'revision', 'input', 'implementation', 'project'])
def test_comparison_refuses_changed_or_unrelated_sources_before_publication(comparison, change):
    app, service, populations = comparison
    if change == 'revision': populations['e1'].revision = 3
    if change == 'input': populations['e1'].capture_id = 'different-input'
    if change == 'implementation': populations['e1'].implementation = 'other-method'
    if change == 'project': populations['e1'].session = '2026-01-01'
    async def go():
        with pytest.raises(DataTableRefused):
            await service.from_comparison('main', 2 if change == 'layout' else 1)
        assert not app.day.datatables and not list(service.root.glob('*/tb*/v*.json'))
    asyncio.run(go())


def test_project_change_during_shared_measurement_read_refuses(comparison, monkeypatch):
    app, service, _ = comparison
    async def read(*args):
        app.day.generation += 1
        return {'id': args[1], 'kind': 'population', 'title': 'Fixture', 'revision': args[3], 'columns': [], 'rows': []}
    monkeypatch.setattr('orchestrator.tables.read_table', read)
    async def go():
        with pytest.raises(DataTableRefused, match='Project changed'):
            await service.from_comparison('main', 1)
        assert not app.day.datatables
    asyncio.run(go())


def test_ordinary_comparison_route_keeps_exact_layout_and_native_rows(comparison):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from orchestrator.table_routes import add_table_routes
    app, service, _ = comparison
    app.hub.datatables = service
    http = FastAPI()
    add_table_routes(http, app, lambda origin: True, 'X-MicroscopeMind')
    client = TestClient(http)
    body = {'session': app.day.sid, 'tab': 'main', 'layoutRevision': 1}
    response = client.post('/api/datatables/comparison', json=body, headers={'X-MicroscopeMind': 'datatable'})
    assert response.status_code == 200, response.text
    table = response.json()
    assert table['mode'] == 'frozen' and table['rows'][1]['area'] == 12.25
    assert table['comparison']['sourceRevisions'] == {'e1': 1, 'e2': 2}
    assert client.post('/api/datatables/comparison', json={**body, 'layoutRevision': 2}, headers={'X-MicroscopeMind': 'datatable'}).status_code == 409


def test_saved_image_comparison_survives_independent_captures_and_restart(comparison):
    _, service, populations = comparison
    image = {'asset': 'a2', 'session': populations['e1'].session,
             'sha256': 'a'*64, 'pixelSha256': 'b'*64, 'width': 512, 'height': 512,
             'dtype': 'uint8', 'axes': 'YX', 'channels': 1, 'units': 'pixels'}
    for i, population in enumerate(populations.values(), 1):
        population.capture_id = -i
        population.view = {'sourceImage': dict(image), 'boot': '', 'seq': None, 'run': f'run-{i}'}
    async def go():
        made = await service.from_comparison('main', 1)
        assert service.read(made['id'])['comparison']['inputKey']
        populations['e2'].view['sourceImage']['pixelSha256'] = 'c'*64
        populations['e2'].revision += 1
        with pytest.raises(DataTableRefused):
            await service.update(made['id'])
    asyncio.run(go())


@pytest.mark.parametrize('change', ['boot', 'seq', 'run', 'unknown'])
def test_equal_temporary_capture_numbers_are_not_input_identity(comparison, change):
    app, service, populations = comparison
    if change == 'unknown':
        for p in populations.values(): p.view = {}
    else:
        populations['e2'].view[change] = 'other'
    async def go():
        with pytest.raises(DataTableRefused):
            await service.from_comparison('main', 1)
        assert not app.day.datatables
    asyncio.run(go())


@pytest.mark.parametrize('field,value', [('asset', 'another'), ('session', 'another'),
    ('sha256', 'c'*64), ('pixelSha256', 'c'*64), ('width', 256), ('height', 256),
    ('units', 'micrometers'), ('axes', 'XY'), ('dtype', 'uint16'), ('channels', 3)])
def test_same_capture_cannot_override_changed_saved_image_metadata(comparison, field, value):
    _, service, populations = comparison
    image = {'asset': 'a2', 'session': populations['e1'].session,
             'sha256': 'a'*64, 'pixelSha256': 'b'*64, 'width': 512, 'height': 512,
             'dtype': 'uint8', 'axes': 'YX', 'channels': 1, 'units': 'pixels'}
    for population in populations.values(): population.view['sourceImage'] = dict(image)
    populations['e2'].view['sourceImage'][field] = value
    with pytest.raises(DataTableRefused):
        asyncio.run(service.from_comparison('main', 1))
