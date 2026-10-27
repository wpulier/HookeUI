"""Original accepted Screen pins with synthetic Studio ACKs; no browser/agent acceptance."""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ToolFailed
from orchestrator.datatables import TableOwner
from orchestrator.screen import Screen
from orchestrator.workspace_tabs import WorkspaceTabs
from tests.test_workspace_observation import CLIENT, report
from tests.waiting import HANG_S


class Studio:
    def __init__(self):
        self.sent = asyncio.Queue()

    def offer(self, message):
        self.sent.put_nowait(json.loads(message))


class Rig:
    def __init__(self, *, with_strip=True):
        self.client = Studio()
        self.screen = Screen(clock=lambda: 10)
        self.screen.accept_client(self.client, CLIENT, app_run_id='run-a',
                                  source_resolver=lambda *args: None, capture_authority=lambda *args: False)
        self.day = SimpleNamespace(sid='project-a', generation=1, current=lambda generation: generation == 1)
        self.owner = TableOwner(self.day, 'project-a', 1, None)
        self.app = SimpleNamespace(day=self.day, hub=SimpleNamespace(
            clients={self.client}, screen=self.screen,
            datatables=SimpleNamespace(capture=lambda sid: self.owner)))
        self.broker = WorkspaceTabs(self.app)
        self.seq = 0
        self.report(with_strip=with_strip)
        self.ctx = SimpleNamespace(client=self.client, asked=SimpleNamespace(session='project-a', routing_generation=1),
                                   request_id='request-a', admission=None,
                                   request_snapshot=self.screen.pin(self.client, request_id='request-a'))

    def report(self, *, generation=1, tab='main', with_strip=True):
        self.seq += 1
        raw = report(seq=self.seq)
        raw.update(sources=[], targets=[], edges=[], applied=[], user=[], commandResults=[],
                   interaction={'availability': 'partial', 'unknown': ['synthetic-tab-strip-only']})
        raw['items'] = [{
            'id': 'workspace-tabs', 'rootId': 'tabs-root', 'workspaceId': CLIENT.workspace_id,
            'kind': 'panel', 'lifetimeGeneration': 'tabs-lifetime', 'sourceGeneration': generation,
            'load': {'state': 'unknown'}, 'revision': self.seq, 'open': True, 'connected': True, 'visible': True,
            'view': {'legacy': {'schema': 'studio-surface-v1', 'scope': 'dom-observation',
                                'workspace': {'project': 'project-a', 'tab': tab}}},
        }] if with_strip else []
        self.screen.report(raw, client=self.client)

    def reply(self, command, *, client=None, pin=None):
        return self.broker.reply(client or self.client, {
            'id': command['id'], 'session': 'project-a',
            'workspacePin': command['workspacePin'] if pin is None else pin,
            'result': {'ok': True, 'saved': True, 'studioVisible': True}})


def test_original_strip_pin_allows_tab_switch_but_rejects_foreign_ack():
    async def run():
        rig = Rig()
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'switch', 'tab': 'other'}))
        command = await asyncio.wait_for(rig.client.sent.get(), HANG_S)
        assert command['workspacePin'] == {'client': CLIENT.wire(), 'id': 'workspace-tabs', 'rootId': 'tabs-root',
                                           'kind': 'panel', 'lifetimeGeneration': 'tabs-lifetime', 'sourceGeneration': 1}
        rig.report(tab='other')  # Ordinary Tab changes do not create another workspace opening.
        assert not rig.reply(command, client=Studio())
        assert not rig.reply(command, pin={**command['workspacePin'], 'sourceGeneration': 2})
        assert not task.done()
        assert rig.reply(command)
        assert (await task)['saved'] is True
        assert not rig.broker.pending
    asyncio.run(run())


@pytest.mark.parametrize('missing_original', [False, True])
def test_newer_workspace_report_cannot_rebase_request(missing_original):
    async def run():
        rig = Rig(with_strip=not missing_original)
        rig.report(generation=2)
        with pytest.raises(ToolFailed, match='original'):
            await rig.broker.run(rig.ctx, {'action': 'create', 'title': 'Results'})
        assert rig.client.sent.empty()
        assert not rig.broker.pending
    asyncio.run(run())


def test_project_reopening_during_resource_read_refuses_before_dispatch():
    async def run():
        rig = Rig()
        entered, released = asyncio.Event(), asyncio.Event()
        loop = asyncio.get_running_loop()
        def get(resource, session):
            assert (resource, session) == ('tb1', 'project-a')
            loop.call_soon_threadsafe(entered.set)
            asyncio.run_coroutine_threadsafe(released.wait(), loop).result(timeout=HANG_S)
            return SimpleNamespace(id='tb1', version=1, kind='dataset-table', title='Cells')
        rig.app.hub.resources = SimpleNamespace(get=get)
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'add', 'tab': 'main',
                                                                         'resource': {'id': 'tb1', 'version': 1}}))
        await asyncio.wait_for(entered.wait(), HANG_S)
        rig.report(generation=2)
        released.set()
        with pytest.raises(ToolFailed, match='opening changed'):
            await task
        assert rig.client.sent.empty()
    asyncio.run(run())


def test_reopened_workspace_cannot_confirm_pending_action():
    async def run():
        rig = Rig()
        task = asyncio.create_task(rig.broker.run(rig.ctx, {'action': 'list'}))
        command = await asyncio.wait_for(rig.client.sent.get(), HANG_S)
        rig.report(generation=2)
        assert not rig.reply(command)
        with pytest.raises(ToolFailed, match='opening changed'):
            await task
        assert not rig.broker.pending
    asyncio.run(run())
