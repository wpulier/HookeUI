"""Return-transition orchestration; scripted observations are not runtime identity evidence."""
import asyncio
from types import SimpleNamespace

import pytest

from sim import identity_probe


def sample(verified, *, false_placement=False):
    return {'coherent': True, 'reply': {'ok': True, 'summary':
        "c1 (i1): " + ('in view now; its own pixels matched there' if verified or false_placement
                       else "can't confirm it's still there")},
        'objects': {'o1': {'track': 'tracking' if verified else 'refinding'}}}


def test_refusal_during_return_is_observed_before_epoch_wait_completes(monkeypatch):
    async def scenario():
        started, refused, placed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        deadlines = []
        async def observe(stack, viewer, deadline, *, samples):
            deadlines.append(deadline)
            await started.wait()
            if not refused.is_set():
                refused.set()
                row = sample(False)
            else:
                placed.set()
                row = sample(True)
            samples.append(row)
            return row
        async def returning(deadline):
            deadlines.append(deadline)
            started.set()
            await refused.wait()
            await placed.wait()  # equivalent to waiting for the returned view to settle
        monkeypatch.setattr(identity_probe, 'observe', observe)
        result = await identity_probe.during_return(None, None, {'i1': 'o1'}, returning, seconds=5)
        assert result['passed'] and result['refused'] and result['placed']
        assert len(set(deadlines)) == 1
        assert result['transition']['probeStartedAt'] <= result['transition']['returnStartedAt']
        assert result['transition']['returnStartedAt'] <= result['transition']['returnCompletedAt']
    asyncio.run(scenario())


@pytest.mark.parametrize('verified,false_placement', [(True, False), (False, True)])
def test_already_placed_or_false_placement_still_fails(monkeypatch, verified, false_placement):
    async def scenario():
        async def observe(*args, samples):
            row = sample(verified, false_placement=false_placement)
            samples.append(row)
            return row
        async def returning(deadline):
            pass
        monkeypatch.setattr(identity_probe, 'observe', observe)
        result = await identity_probe.during_return(None, None, {'i1': 'o1'}, returning, seconds=.05)
        assert not result['passed'] and not result['refused']
        assert bool(result['violations']) is false_placement
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['timeout', 'transport', 'cancel'])
def test_return_failure_drains_its_observer_and_return_work(monkeypatch, failure):
    async def scenario():
        watching, returning_now = asyncio.Event(), asyncio.Event()
        stopped, returned = asyncio.Event(), asyncio.Event()
        async def observe(*args, samples):
            watching.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async def returning(deadline):
            assert watching.is_set(), 'observer must be armed before returning'
            returning_now.set()
            try:
                if failure == 'transport':
                    raise OSError('controlled return transport failure')
                await asyncio.Event().wait()
            finally:
                returned.set()
        monkeypatch.setattr(identity_probe, 'observe', observe)
        task = asyncio.create_task(identity_probe.during_return(None, None, {'i1': 'o1'}, returning,
                                                                seconds=.05 if failure == 'timeout' else 5))
        await returning_now.wait()
        if failure == 'cancel':
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            result = await task
            assert not result['passed'] and result['error']
        assert stopped.is_set() and returned.is_set()
    asyncio.run(scenario())


@pytest.mark.parametrize('phase', ['health-before', 'reply', 'health-after'])
def test_shared_deadline_retains_real_observers_partial_evidence(phase):
    async def scenario():
        blocked, stopped = asyncio.Event(), asyncio.Event()
        calls = 0
        async def hold():
            blocked.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()
        async def health():
            nonlocal calls
            calls += 1
            if (calls == 1 and phase == 'health-before') or (calls == 2 and phase == 'health-after'):
                await hold()
            return {'scene': {'version': 7}, 'motion': {'epoch': 0}}
        async def send(message):
            pass
        async def reply(*args, **kwargs):
            if phase == 'reply':
                await hold()
            return {'ok': True, 'summary': "c1 (i1): can't confirm it's still there"}
        async def returning(deadline):
            await blocked.wait()
            await asyncio.Event().wait()
        viewer = SimpleNamespace(scene_version=7, scene={'o1': {'track': 'refinding'}}, results=[],
                                 send=send, result_for=reply)
        result = await identity_probe.during_return(SimpleNamespace(orch_health=health), viewer,
                                                     {'i1': 'o1'}, returning, seconds=.05)
        assert stopped.is_set() and not result['passed'] and result['error']
        assert len(result['samples']) == 1
        partial = result['samples'][0]
        assert not partial['coherent'] and partial['error'] and partial['phase'] == phase
        if phase != 'health-before':
            assert partial['versions'] == [7, 7] and partial['objects']['o1']['track'] == 'refinding'
        if phase == 'health-after':
            assert partial['reply']['ok'] and partial['reply']['summary']
    asyncio.run(scenario())


@pytest.mark.parametrize('coherent,lie,passes', [(True, False, True), (False, False, False), (True, True, False)])
def test_continuity_loss_is_a_refusal_before_verified_return(monkeypatch, coherent, lie, passes):
    """The public tool's observed loss wording is as unconfirmed as its refinding wording."""
    async def scenario():
        calls = 0
        async def observe(*args, samples):
            nonlocal calls
            calls += 1
            row = sample(calls > 1)
            if calls == 1:
                row['coherent'] = coherent
                row['reply']['summary'] = "c1 (i1): where: can't tell: continuity was lost (the slide's movement since is not known)"
                if lie:
                    row['reply']['summary'] += '; in view now'
            samples.append(row)
            return row
        monkeypatch.setattr(identity_probe, 'observe', observe)
        result = await identity_probe.follow_return(None, None, {'i1': 'o1'}, seconds=.35)
        assert result['passed'] is passes
        assert bool(result['violations']) is lie
    asyncio.run(scenario())
