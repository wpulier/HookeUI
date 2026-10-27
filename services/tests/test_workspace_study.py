"""Controlled timing only; retained pixels and CPU Bright objects use production code."""
import asyncio

import cv2
import numpy as np
import pytest

from orchestrator import population
from orchestrator.commands import handle
from tests.test_original_picture_analysis import prepare
from tests.test_sources import Client


@pytest.mark.parametrize('enabled,value', [(False, 10), (True, 0), (True, 15001), (True, True), (True, '10')])
def test_delay_refuses_outside_review_or_with_invalid_duration(tmp_path, monkeypatch, enabled, value):
    if enabled:
        monkeypatch.setenv('MICROMIND_WORKSPACE_STUDY', '1')
    else:
        monkeypatch.delenv('MICROMIND_WORKSPACE_STUDY', raising=False)
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    client = Client()
    asyncio.run(handle(built, client, {'type': 'action', 'capability': 'cells',
        'workspaceStudyDelayMs': value, 'use': {'cells': 'basic-bright-objects'}}))
    assert not client.replies[-1]['ok']
    assert 'controlled workspace delay' in client.replies[-1]['error']
    assert not population.evidence_of(built.hub).items
    assert not worker.inputs


def test_delayed_real_operation_keeps_original_input_and_session(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_WORKSPACE_STUDY', '1')
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    pixels = np.zeros((96, 96), np.uint8)
    pixels[:, :20] = 255
    pixels[:, 76:] = 255
    cv2.imwrite(str(image), pixels)

    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        origin = built.day.sid
        captured = asyncio.Event()

        class ReviewClient(Client):
            def offer(self, message):
                super().offer(message)
                if self.replies[-1].get('type') == 'workspace-study-delay':
                    captured.set()

        client = ReviewClient()
        pending = asyncio.create_task(handle(built, client, {'type': 'action', 'run': 'review-delayed',
            'capability': 'cells', 'workspaceStudyDelayMs': 1500, 'use': {'cells': 'basic-bright-objects'}}))
        await asyncio.wait_for(captured.wait(), 5)
        notice = client.replies[-1]
        assert notice['source'] == aid and notice['session'] == origin
        assert notice['run'] == 'review-delayed' and notice['phase'] == 'captured'
        assert not pending.done() and not population.evidence_of(built.hub).items
        switch = Client()
        await handle(built, switch, {'type': 'session', 'action': 'new', 'name': 'Other session'})
        assert switch.replies[-1]['ok'] and built.day.sid != origin
        await asyncio.wait_for(pending, 5)
        assert client.replies[-1]['ok'], client.replies[-1]
        assert population.layer_of(built.hub).shown is None
        for _ in range(200):
            entries = built.hub.resources.list(kind='population', day=origin)
            if entries:
                break
            await asyncio.sleep(.01)
        assert len(entries) == 1
        await handle(built, Client(), {'type': 'session', 'action': 'switch', 'id': origin})
        result = population.evidence_of(built.hub).get(entries[0].id)
        assert result.session == origin
        assert result.view['sourceImage']['asset'] == aid
        assert result.view['sourceImage']['session'] == origin
        assert result.made_by['execution'] == notice['execution']
        assert sorted(result.value(m.id, 'areaPx') for m in result.live()) == [1920, 1920]
        assert result.scale_id['units'] == 'pixels'
        assert population.layer_of(built.hub).shown is None
        assert not worker.inputs
        assert len([m for m in client.replies if m.get('type') == 'workspace-study-delay']) == 1

    asyncio.run(go())


def test_stop_during_controlled_delay_never_executes_worker(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_WORKSPACE_STUDY', '1')
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await handle(built, Client(), {'type': 'source', 'open': aid})
        captured = asyncio.Event()

        class ReviewClient(Client):
            def offer(self, message):
                super().offer(message)
                if self.replies[-1].get('type') == 'workspace-study-delay':
                    captured.set()

        client = ReviewClient()
        pending = asyncio.create_task(handle(built, client, {'type': 'action', 'run': 'review-stop',
            'capability': 'cells', 'workspaceStudyDelayMs': 15000, 'use': {'cells': 'basic-bright-objects'}}))
        await asyncio.wait_for(captured.wait(), 5)
        await handle(built, client, {'type': 'action-cancel', 'run': 'review-stop'})
        await asyncio.wait_for(pending, 5)
        assert any(m.get('stopped') is True for m in client.replies), client.replies
        assert not population.evidence_of(built.hub).items
        assert not worker.inputs

    asyncio.run(go())


def test_delay_is_once_per_action_and_does_not_escape_context(monkeypatch):
    from types import SimpleNamespace
    from orchestrator.workspace_study import action_delay, before_execution, delaying
    monkeypatch.setenv('MICROMIND_WORKSPACE_STUDY', '1')
    client = Client()

    async def go():
        delay = action_delay({'workspaceStudyDelayMs': 1, 'run': 'one'}, client, 'original')
        capture = SimpleNamespace(id=21, source_image={'asset': 'a1'})
        execution = SimpleNamespace(id='x1')
        with delaying(delay):
            await before_execution(None, execution)
            await before_execution(capture, execution)
            await before_execution(capture, execution)
        await before_execution(capture, execution)
        assert len(client.replies) == 1
        assert action_delay({}, client, 'other') is None

    asyncio.run(go())


def test_profile_identity_is_stable_distinct_and_contains_no_path(tmp_path, monkeypatch):
    from orchestrator.paths import Paths
    from orchestrator.workspace_study import profile_id
    from orchestrator.brain.registry import ToolFailed
    monkeypatch.setenv('MICROMIND_WORKSPACE_STUDY', '1')
    first = Paths.under(tmp_path / 'one')
    second = Paths.under(tmp_path / 'two')
    identity = profile_id(first)
    assert len(identity) == 32 and all(c in '0123456789abcdef' for c in identity)
    assert identity == profile_id(Paths.under(tmp_path / 'one'))
    assert identity != profile_id(second)
    monkeypatch.delenv('MICROMIND_WORKSPACE_STUDY')
    with pytest.raises(ToolFailed, match='isolated review profile'):
        profile_id(first)


def test_profile_command_returns_only_an_opaque_namespace(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_WORKSPACE_STUDY', '1')
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    client = Client()
    asyncio.run(handle(built, client, {'type': 'workspace-study-profile', 'requestId': 'profile'}))
    reply = client.replies[-1]
    assert reply['ok'] and len(reply['profile']) == 32
    assert str(tmp_path) not in str(reply)
