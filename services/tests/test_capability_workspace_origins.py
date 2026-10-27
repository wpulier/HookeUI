"""Real synthetic workers through workspace command admission and retained completion."""
import copy

import pytest

from orchestrator import retained_inputs, workspace_views
from orchestrator.day import DayStore
from tests.test_bundle_actors import command, results, rig  # noqa: F401
from tests.test_env_runner import home  # noqa: F401


@pytest.mark.parametrize('actor', ['action', 'try'])
@pytest.mark.parametrize('stage', [None, 'unrelated-asset'])
def test_workspace_capability_keeps_exact_input_and_table_after_reopen(rig, actor, stage):
    made = command(rig, {'type': 'action', 'capability': 'catalog:image-tools#invert', 'args': {}})
    assert made.get('ok') is not False, str(made)
    day = rig.app.day
    ref = retained_inputs.reference_for(rig.hub.resources, results(rig)[-1]['id'], day.sid)
    source_values = copy.deepcopy(rig.hub.resources.results.values(ref['id']))
    layout = workspace_views.read(day, schema=2)['layout']
    workspace_views.save(day, layout, 0)
    day.on_stage = stage
    origin = {'session': day.sid, 'tab': 'main', 'placementId': 'pending-table',
              'run': 'retained-workspace', 'input': {'resource': ref}}
    message = ({'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}}
               if actor == 'action' else {'type': 'capability-run', 'model': 'image-tools#mean',
                                          'session': day.sid, 'input': 'image.2d.rgb8'})
    reply = command(rig, {**message, 'run': origin['run'], 'resource': ref, 'workspaceOrigin': origin})
    assert reply.get('ok') is not False, str(reply)
    assert day.on_stage == stage
    result = results(rig)[-1]
    assert rig.hub.resources.results.values(result['id'])['inputResources'] == [ref]
    assert rig.hub.resources.results.values(ref['id']) == source_values
    reopened = DayStore(root=day.root, day=day.sid)
    workspace_views.reconcile(reopened)
    workspace_views.reconcile(reopened)
    completions = workspace_views.read(reopened, schema=2)['completions']
    assert len(completions) == 1
    assert completions[0]['origin'] == origin
    assert completions[0]['item']['resource']['id'] == result['id']
    assert completions[0]['item']['presentation'] == 'table'


@pytest.mark.parametrize('actor', ['action', 'try'])
def test_workspace_origin_cannot_claim_a_different_resource(rig, actor):
    day = rig.app.day
    workspace_views.save(day, workspace_views.read(day, schema=2)['layout'], 0)
    ref = {'session': day.sid, 'id': 'd1', 'revision': 'a' * 64, 'component': 'picture-0'}
    origin = {'session': day.sid, 'tab': 'main', 'placementId': 'pending-table',
              'run': 'wrong-resource', 'input': {'resource': ref}}
    message = ({'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}}
               if actor == 'action' else {'type': 'capability-run', 'model': 'image-tools#mean',
                                          'session': day.sid, 'input': 'image.2d.rgb8'})
    before = results(rig)
    reply = command(rig, {**message, 'run': origin['run'], 'workspaceOrigin': origin})
    assert reply.get('ok') is False
    assert 'input' in str(reply).lower()
    assert results(rig) == before
    assert 'wrong-resource' not in day.workspace_origins


@pytest.mark.parametrize('actor', ['action', 'try'])
def test_completion_returns_to_initiating_project_after_switch(rig, monkeypatch, actor):
    import asyncio
    import threading
    from orchestrator import env_runner
    from orchestrator.commands import handle
    from tests.test_sources import Client
    from tests.waiting import HANG_S

    assert command(rig, {'type': 'action', 'capability': 'catalog:image-tools#invert', 'args': {}})['ok']
    owner = rig.app.day.sid
    ref = retained_inputs.reference_for(rig.hub.resources, results(rig)[-1]['id'], owner)
    workspace_views.save(rig.app.day, workspace_views.read(rig.app.day, schema=2)['layout'], 0)
    entered, release = threading.Event(), threading.Event()
    original = env_runner.Client.run
    def blocked(worker, *args, **kwargs):
        entered.set()
        assert release.wait(HANG_S), 'worker barrier was not released'
        return original(worker, *args, **kwargs)
    monkeypatch.setattr(env_runner.Client, 'run', blocked)
    origin = {'session': owner, 'tab': 'main', 'placementId': 'pending-delayed',
              'run': 'delayed', 'input': {'resource': ref}}
    message = ({'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}}
               if actor == 'action' else {'type': 'capability-run', 'model': 'image-tools#mean',
                                          'session': owner, 'input': 'image.2d.rgb8'})
    async def go():
        client = Client()
        pending = asyncio.create_task(handle(rig.app, client,
            {**message, 'run': 'delayed', 'resource': ref, 'workspaceOrigin': origin}))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            await handle(rig.app, Client(), {'type': 'session', 'action': 'new', 'name': 'Unrelated Project'})
            assert rig.app.day.sid != owner
        finally:
            release.set()
        await asyncio.wait_for(pending, HANG_S)
        assert client.replies[-1].get('ok') is not False, str(client.replies[-1])
        assert workspace_views.read(rig.app.day, schema=2)['completions'] == []
    asyncio.run(go())
    reopened = DayStore(root=rig.app.day.root, day=owner)
    workspace_views.reconcile(reopened)
    workspace_views.reconcile(reopened)
    completed = workspace_views.read(reopened, schema=2)['completions']
    assert len(completed) == 1
    assert completed[0]['origin'] == origin
    assert completed[0]['item']['presentation'] == 'table'


def test_legacy_try_rechecks_project_after_catalog_lookup(rig, monkeypatch):
    import asyncio
    import threading
    from orchestrator import capabilities
    from orchestrator.commands import handle
    from tests.test_sources import Client
    from tests.waiting import HANG_S

    owner = rig.app.day.sid
    entered, release = threading.Event(), threading.Event()
    original = capabilities.contributed
    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(HANG_S), 'catalog barrier was not released'
        return original(*args, **kwargs)
    monkeypatch.setattr(capabilities, 'contributed', blocked)
    async def go():
        client = Client()
        pending = asyncio.create_task(handle(rig.app, client, {'type': 'capability-run',
            'model': 'image-tools#mean', 'session': owner, 'input': 'image.2d.rgb8'}))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            rig.app.day.open('2026-10-18' if owner == '2026-10-17' else '2026-10-17')
        finally:
            release.set()
        await asyncio.wait_for(pending, HANG_S)
        assert client.replies[-1]['ok'] is False, str(client.replies[-1])
        assert 'Project changed' in client.replies[-1]['error']
        assert results(rig) == []
    asyncio.run(go())


def test_layout_job_reconciliation_cannot_follow_a_project_switch(rig, monkeypatch):
    import asyncio
    import threading
    from orchestrator.commands import handle
    from tests.test_sources import Client
    from tests.waiting import HANG_S

    day = rig.app.day
    owner = day.sid
    workspace_views.save(day, workspace_views.read(day, schema=2)['layout'], 0)
    origin = {'session': owner, 'tab': 'main', 'placementId': 'pending-job',
              'run': 'background-job', 'input': {'volume': 'v1'}}
    workspace_views.admit(day, origin)
    entered, release = threading.Event(), threading.Event()
    original = workspace_views.reconcile_jobs
    def blocked(store, records):
        entered.set()
        assert release.wait(HANG_S)
        assert store.sid == owner
        return original(store, records)
    monkeypatch.setattr(workspace_views, 'reconcile_jobs', blocked)
    monkeypatch.setattr(rig.app.registrations, 'records', lambda *args: [
        {'id': 'j1', 'state': 'done', 'workspaceOrigin': origin, 'kindOf': {'output': 'table.objects'}}])
    async def go():
        client = Client()
        pending = asyncio.create_task(handle(rig.app, client,
            {'type': 'workspace-layout', 'session': owner, 'schema': 2}))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            day.open('2026-10-18' if owner == '2026-10-17' else '2026-10-17')
        finally:
            release.set()
        await asyncio.wait_for(pending, HANG_S)
        assert client.replies[-1]['ok'] is False
        assert workspace_views.read(day, schema=2)['completions'] == []
    asyncio.run(go())
    reopened = DayStore(root=day.root, day=owner)
    assert len(workspace_views.read(reopened, schema=2)['completions']) == 1
