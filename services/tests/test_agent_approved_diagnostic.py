"""Synthetic approval/selection and dispatch boundaries; real UI proof is separate."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import capability_identity, kinds, own_trials, commands
from orchestrator.door import listing, work, api
from orchestrator.door.connections import Refused
from tests.capability_rig import Rig


def manifest():
    return SimpleNamespace(key='shared/diagnostic', namespace='shared', runner='python-env', members=(),
                           implements=(SimpleNamespace(input='image.2d.rgb8', output='table.objects'),))


def test_discovery_requires_installed_explicit_approval_and_uses_fresh_readiness(monkeypatch):
    m = manifest()
    state = {'installed': True, 'approved': False, 'ready': True}
    monkeypatch.setattr('gpu_worker.manifests.get', lambda key: m)
    monkeypatch.setattr(kinds, 'available_with_manifests', lambda: (
        [{'key': m.key, 'runnable': state['ready'], 'why': 'needs data'}],
        {m.key: m} if state['installed'] else {}))
    monkeypatch.setattr(own_trials, 'explicitly_accepted', lambda value: state['approved'])
    monkeypatch.setattr(kinds, 'door_entry', lambda value: m.implements[0])
    monkeypatch.setattr(listing, 'locality', lambda *args: 'mac')
    monkeypatch.setattr(listing, 'describe_row', lambda row, *args: row)
    # No catalog field: legacy how.args must enforce the identical policy.
    rows = [({'id': 'catalog:'+m.key, 'available': True,
              'how': {'args': {'capability': m.key}}}, object(), {})]
    assert listing.describe_all(rows, {}) == []
    state['approved'] = True
    assert listing.describe_all(rows, {})[0]['available']
    state['ready'] = False
    assert not listing.describe_all(rows, {})[0]['available']
    state['installed'] = False
    assert listing.describe_all(rows, {}) == []


def test_trusted_generation_pin_survives_async_dispatch_and_resets(monkeypatch):
    m = manifest()
    current = {'generation': 'one', 'approved': True}
    monkeypatch.setattr(capability_identity, 'generation_of', lambda value: current['generation'])
    monkeypatch.setattr(own_trials, 'explicitly_accepted', lambda value: current['approved'])
    assert capability_identity.admitted_params(m) == {}
    async def exercise():
        with capability_identity.admitted_entry(m.key, 'one'):
            await asyncio.sleep(0)
            await asyncio.to_thread(capability_identity.check_admitted_entry, m)
            assert capability_identity.admitted_params(m) == {'expectedGeneration': 'one'}
            current['generation'] = 'two'
            with pytest.raises(ValueError, match='changed'):
                await asyncio.to_thread(capability_identity.check_admitted_entry, m)
            current.update(generation='one', approved=False)
            with pytest.raises(ValueError, match='changed'):
                capability_identity.check_admitted_entry(m)
    asyncio.run(exercise())
    assert capability_identity.admitted_params(m) == {}


@pytest.mark.parametrize('changed', [False, True])
def test_queued_imported_run_rechecks_and_passes_pin_to_tool(tmp_path, monkeypatch, changed):
    app = Rig(tmp_path / 'rig').app
    actor = 'agent:ac_' + '4' * 24
    work._remember_project(app, app.day.sid, actor)
    m = manifest()
    record = dict(by=actor, project=app.day.sid, inputRevision=work.stage_revision(app),
                  tool='catalog:'+m.key, generation='one')
    async def listed(_):
        return [dict(id=record['tool'], state='available', generation='two' if changed else 'one')]
    seen = []
    async def execute(*args):
        seen.append(capability_identity.admitted_params(m))
        return dict(ok=True, summary='Done', executions=[])
    async def progress(*args):
        pass
    monkeypatch.setattr(api, '_listed', listed)
    monkeypatch.setattr(commands, 'run_tool', execute)
    call = work.run_capability(app, record, progress, 'run_capability', {'capability': m.key}, {})
    if changed:
        with pytest.raises(Refused, match='changed'):
            asyncio.run(call)
        assert seen == []
    else:
        asyncio.run(call)
        assert seen == [{'expectedGeneration': 'one'}]
    assert capability_identity.admitted_params(m) == {}
