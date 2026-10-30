"""Real isolated folder copies and connector transport; synthetic connections/data."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.commands import COMMANDS
from orchestrator.tools import ToolError
from orchestrator.door import api, schema
from orchestrator.door.operations import Operations
from orchestrator.origin import studio_origin
from orchestrator.project_export import prepare, export, reveal_path
from orchestrator.project_roots import ProjectLocationError
from orchestrator.project_stores import ProjectStores
from orchestrator.scientific_projects import ProjectStore
from tests.test_agent_door import connect, bearer, BASE, HEADER, window_key
from tests.test_agent_projects import run, STUDIO
from tests.test_agent_operations import settle
from tests.test_project_locations import setup
from tests.waiting import until_async, HANG_S


@pytest.fixture
def export_door(tmp_path, monkeypatch):
    monkeypatch.setenv('HOOKE_STUDIO_TOKEN', 'synthetic-owner-window')
    day, locations = setup(tmp_path)
    home = tmp_path / 'host'
    built = SimpleNamespace(paths=SimpleNamespace(home=home), day=day,
        hub=SimpleNamespace(clients=set(), resources=SimpleNamespace(
            projects=ProjectStores(ProjectStore(home / 'documents'), locations))))
    app = FastAPI(); api.register(app, built, studio_origin, HEADER)
    try:
        with TestClient(app, base_url=BASE) as client: yield client, built
    finally: locations.close()


def test_preparation_and_export_share_contract_and_preserve_scope(tmp_path):
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Research')
        plan = prepare(day, sid)
        schema.check(plan, 'project-export-plan')
        assert plan['requiredFreeBytes'] > plan['bytes'] and plan['collection']['state'] == 'unverified'
        with pytest.raises(ProjectLocationError, match='acknowledge'):
            export(day, sid, 'op_' + 'a' * 24, acknowledge_incomplete=False)
        receipt = export(day, sid, 'op_' + 'a' * 24, acknowledge_incomplete=True)
        schema.check(receipt, 'project-export')
        assert reveal_path(day, sid, receipt) == Path(receipt['location'])
        assert not list(Path(receipt['location']).rglob('connections.json'))
        day.rename_session('More research')
        assert export(day, sid, 'op_' + 'a' * 24, acknowledge_incomplete=True) == receipt
        assert len(list((locations.parent / 'Exports').iterdir())) == 1
    finally: locations.close()


def test_door_export_requires_contribute_and_acknowledgement_then_reuses_receipt(export_door, monkeypatch):
    from orchestrator.door import listing
    from orchestrator import agent_runnable
    client, built = export_door
    monkeypatch.setattr(listing, 'gather', lambda built: [])
    first = connect(client); owner = bearer(first['token'])
    reader = connect(client, label='Reader', secret='export-reader-secret-01'); read_headers = bearer(reader['token'])
    sid = run(client, owner, 'project.create', {'name': 'Research'}, 'export-create-01')['outcome']['project']['session']
    request = {'tool': 'project.export', 'args': {'acknowledgeIncomplete': True}, 'project': sid, 'requestKey': 'export-copy-01'}
    assert client.post('/api/agent/v1/operations', json=request, headers=read_headers).status_code == 403
    monkeypatch.delenv('HOOKE_CONFIRM_KEY'); monkeypatch.setenv('HOOKE_DEV_CONFIRM', '1')
    grant = {'session': sid, 'connection': reader['connection']['id'], 'role': 'read'}
    assert client.post('/api/agent-project-access', json=grant, headers=STUDIO).status_code == 200
    plan = run(client, read_headers, 'project.export.prepare', {}, 'export-plan-01', sid)
    schema.check(plan['outcome']['export'], 'project-export-plan')
    assert client.post('/api/agent/v1/operations', json=request, headers=read_headers).status_code == 403
    assert client.post('/api/agent/v1/operations', json={**request, 'args': {'acknowledgeIncomplete': False}}, headers=owner).status_code == 400
    assert client.post('/api/agent-project-access', json={**grant, 'role': 'contribute'}, headers=STUDIO).status_code == 200
    result = run(client, read_headers, 'project.export', request['args'], request['requestKey'], sid)
    receipt = result['outcome']['export']; schema.check(receipt, 'project-export')
    copy = Path(receipt['location'])
    kept = json.loads((copy / 'history/operations' / (result['id'] + '.json')).read_text())
    assert kept['state'] == 'running' and kept['by'] == result['by']
    assert run(client, read_headers, 'project.export', request['args'], request['requestKey'], sid)['id'] == result['id']
    assert not list(copy.rglob('projects.json')) and not list(copy.rglob('connections.json'))
    monkeypatch.delitem(agent_runnable.DOOR_RESOURCES, 'project.export')
    assert client.get('/api/agent/v1/tools?q=project.export', headers=owner).json()['tools'][0]['id'] == 'project.export.prepare'
    assert client.post('/api/agent/v1/operations', json={**request, 'requestKey': 'not-declared-01'}, headers=owner).status_code == 403


def test_owner_export_keeps_original_project_after_navigation(tmp_path):
    day, locations = setup(tmp_path)
    async def scenario():
        sid = day.new('Research')
        other = day.create_retained('Other', actor='user').sid
        app = SimpleNamespace(day=day, agent_operations=Operations(tmp_path / 'host', locations=locations))
        reply = await COMMANDS['project-export'](app, None, {'session': sid, 'requestKey': 'owner-copy-01', 'acknowledgeIncomplete': True})
        day.switch_session(other)
        await settle(app.agent_operations)
        result = app.agent_operations.get(reply['operation']['id'])
        assert result['state'] == 'succeeded' and result['by'] == 'user'
        assert result['outcome']['export']['session'] == sid
        with pytest.raises(ToolError, match='Project changed'):
            await COMMANDS['project-export-status'](app, None, {'session': sid, 'operation': result['id']})
        day.switch_session(sid)
        assert (await COMMANDS['project-export-status'](app, None, {'session': sid, 'operation': result['id']}))['operation'] == result
    try: asyncio.run(scenario())
    finally: locations.close()


def test_export_keeps_incomplete_copy_and_original_on_cancellation(tmp_path):
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Research'); root = locations.project(sid)
        original = day.journal.path.read_bytes()
        def stopped():
            if (locations.parent / 'Exports' / f'{root.origin}—op_{"b" * 24}').exists():
                raise RuntimeError('cancelled')
        with pytest.raises(RuntimeError, match='cancelled'):
            export(day, sid, 'op_' + 'b' * 24, acknowledge_incomplete=True, check=stopped)
        assert day.journal.path.read_bytes() == original
        assert list((locations.parent / 'Exports').glob('*/.project-copy-in-progress'))
        with pytest.raises(ProjectLocationError, match='incomplete'):
            export(day, sid, 'op_' + 'b' * 24, acknowledge_incomplete=True)
    finally: locations.close()


def test_export_refuses_linked_destination_without_writing_elsewhere(tmp_path):
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Research')
        elsewhere = tmp_path / 'unrelated'; elsewhere.mkdir()
        (locations.parent / 'Exports').symlink_to(elsewhere, target_is_directory=True)
        with pytest.raises(ProjectLocationError, match='link'):
            export(day, sid, 'op_' + 'c' * 24, acknowledge_incomplete=True)
        assert not list(elsewhere.iterdir())
    finally: locations.close()


def test_export_refuses_insufficient_space_before_destination_creation(tmp_path, monkeypatch):
    from orchestrator import project_folder_copy
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Research')
        monkeypatch.setattr(project_folder_copy.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
        with pytest.raises(ProjectLocationError, match='no copy was started'):
            export(day, sid, 'op_' + 'd' * 24, acknowledge_incomplete=True)
        assert not list((locations.parent / 'Exports').iterdir())
    finally: locations.close()


def test_cancelled_export_waits_for_worker_and_keeps_a_publication_that_won():
    from threading import Event
    from orchestrator.project_export import owned_export
    started, release = Event(), Event()
    stops = []
    def work():
        started.set()
        assert release.wait(HANG_S)
        return {'publication': 'already-complete'}
    async def scenario():
        task = asyncio.create_task(owned_export(work, stopped=lambda: stops.append(True)))
        try:
            await until_async(started.is_set)
            task.cancel()
            await until_async(lambda: bool(stops))
            assert not task.done()
            task.cancel()
            await until_async(lambda: len(stops) == 2)
            assert not task.done()
        finally: release.set()
        assert await asyncio.wait_for(task, HANG_S) == {'publication': 'already-complete'}
    asyncio.run(scenario())


def test_export_parent_sync_failure_cannot_publish_a_copy(tmp_path, monkeypatch):
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Research')
        real_sync = ProjectStore._sync_directory
        def fail_parent(path):
            if Path(path) == locations.parent:
                raise OSError('injected Projects parent sync failure')
            return real_sync(path)
        monkeypatch.setattr(ProjectStore, '_sync_directory', fail_parent)
        with pytest.raises(OSError, match='injected'):
            export(day, sid, 'op_' + 'e' * 24, acknowledge_incomplete=True)
        assert not list((locations.parent / 'Exports').iterdir())
        monkeypatch.setattr(ProjectStore, '_sync_directory', real_sync)
        receipt = export(day, sid, 'op_' + 'e' * 24, acknowledge_incomplete=True)
        assert Path(receipt['location']).is_dir()
    finally: locations.close()
