"""Exact Stage admission with synthetic connections and tool I/O, real door receipts."""
import asyncio

import pytest
from fastapi.testclient import TestClient

from orchestrator.door import work
from orchestrator.door.connections import Refused
from tests.test_agent_door import BASE, bearer, connect, window_key  # noqa: F401
from tests.test_agent_operations import KEY, door_with_capability


def retained(session, revision=1):
    return dict(origin='a' * 32, session=session, document='p' + 'b' * 32,
                revision=revision, digest=str(revision) * 64)


def test_door_receipt_pins_exact_revision_and_retry_preserves_it(tmp_path, monkeypatch):
    ran = []
    web, app = door_with_capability(tmp_path, monkeypatch, ran)
    app.day.on_stage_reference = retained(app.day.sid)
    original = work.stage_revision(app)
    with TestClient(web, base_url=BASE) as client:
        claimed = connect(client)
        actor = 'agent:' + claimed['connection']['id']
        work._remember_project(app, app.day.sid, actor)
        body = dict(tool='measure-things', project=app.day.sid, requestKey=KEY)
        first = client.post('/api/agent/v1/operations', json=body, headers=bearer(claimed['token']))
        assert first.status_code == 202, first.text
        record = first.json()['operation']
        assert record['inputRevision'] == original
        app.day.on_stage_reference = retained(app.day.sid, 2)
        again = client.post('/api/agent/v1/operations', json=body, headers=bearer(claimed['token']))
        assert again.status_code == 200 and not again.json()['made']
        assert again.json()['operation']['inputRevision'] == original
        assert again.json()['operation']['id'] == record['id']


@pytest.mark.parametrize('when', ['queued', 'progress'])
def test_same_alias_new_revision_cannot_retarget_an_admitted_operation(tmp_path, monkeypatch, when):
    from tests.capability_rig import Rig
    from orchestrator import commands
    app = Rig(tmp_path / 'rig').app
    actor = 'agent:ac_' + '3' * 24
    work._remember_project(app, app.day.sid, actor)
    app.day.on_stage = 'a1'
    app.day.on_stage_reference = retained(app.day.sid)
    record = dict(by=actor, project=app.day.sid, inputRevision=work.stage_revision(app))
    calls = []

    async def execute(*args, **kwargs):
        calls.append(args)
        return {'ok': True, 'summary': 'Done', 'executions': []}

    async def progress(*args):
        if when == 'progress':
            app.day.on_stage_reference = retained(app.day.sid, 2)

    monkeypatch.setattr(commands, 'run_tool', execute)
    if when == 'queued':
        app.day.on_stage_reference = retained(app.day.sid, 2)
    with pytest.raises(Refused) as refusal:
        asyncio.run(work.run_capability(app, record, progress, 'count', {}, {}))
    assert refusal.value.code == 'input-changed'
    assert not calls

