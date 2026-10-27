"""Additive TEST-only spoken camera lifetime and concurrent-quota regressions.

Actual App/Hands/handoff, Capture, Ticket.track/on_close, registry, inspection and
admission paths are reused unchanged. Only canned external provider responses,
delivery, synthetic ingress and declared timing inputs are scripted. The parent
owns collection/execution and genuine RED; this file was authored statically.
Observer references are deliberately separate from production cache roots.
"""
import asyncio
import weakref
from types import SimpleNamespace

import pytest

from orchestrator.brain import speech
from orchestrator.brain.registry import CameraSelection, ToolContext
from orchestrator.ticket import current
from tests.test_mixed_camera_retained_context import spatial_camera_jpeg
from tests.test_spoken_camera_request_selection import (
    CAMERA_ANSWER, MIXED_ANSWER, SpokenRig, actual_pictures, assert_original_hands,
    assert_spoken_camera, eventually, interpretation_reply, open_another_session,
    selected_camera_reply,
)
from tests.waiting import HANG_S


class ResponseGate:
    """Hold one external provider response; all entered app work stays real."""
    def __init__(self, monkeypatch, rig, phase):
        self.entered, self.release, self.returned = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.sent = None
        actual_external = rig.app.agent.vision

        async def ask(option, images, prompt, **kwargs):
            owner = current()
            attempt = owner.admission.attempts[-1]
            # The frozen boundary records its actual entry before returning its
            # canned data. Its complete wrapped response remains pending here.
            result = await actual_external(option).ask(images, prompt, **kwargs)
            if attempt.phase == phase:
                self.sent = next(sent for sent in rig.sends if sent.ticket is owner
                                 and sent.attempt.attempt_id == attempt.attempt_id)
                self.entered.set()
                try:
                    await self.release.wait()
                finally:
                    self.returned.set()
            return result

        monkeypatch.setattr(rig.app.agent, 'vision',
                            lambda option: SimpleNamespace(ask=lambda *args, **kwargs: ask(option, *args, **kwargs)))
        rig.gates.append(self)  # frozen teardown releases only this external barrier

    async def wait(self):
        await asyncio.wait_for(self.entered.wait(), HANG_S)


def notes_reply():
    return {'intent': 'question', 'needs_look': False,
            'answer': 'The conversation notes answer this.', 'actions': [],
            'observation_scope': 'none', 'input_refs': []}


def test_timed_out_hands_and_handoff_share_the_actual_two_picture_quota(tmp_path, monkeypatch):
    async def scenario():
        # A local handoff wait shorter than the fast-provider deadline
        # exposes a genuinely pending producer without changing Brain.quick.
        monkeypatch.setattr(speech, 'HANDS_RESULT_S', 0.05)
        record = {}

        def response(sent):
            if sent.phase == 'hands':
                return selected_camera_reply()
            if sent.phase == 'look':
                return {'answer': 'I am acquiring the two selected inputs.', 'targets': [],
                        'actions': [{'tool': 'inspect_camera', 'args': {}},
                                    {'tool': 'resources', 'args': {'id': record['id'], 'inspect': True}},
                                    {'tool': 'resources', 'args': {'id': record['third_id'], 'inspect': True}}],
                        'observation_scope': 'none', 'input_refs': []}
            return interpretation_reply(rig, MIXED_ANSWER)

        rig = SpokenRig(tmp_path, monkeypatch, response)
        final = ResponseGate(monkeypatch, rig, 'final')
        try:
            data = spatial_camera_jpeg(value=55)
            record.update(rig.app.hub.resources.results.add(
                'compare', {}, 'Synthetic timeout retained input', picture_bytes=[data]))
            third = rig.app.hub.resources.results.add(
                'compare', {}, 'Synthetic distinct third input',
                picture_bytes=[spatial_camera_jpeg(value=95)])
            record['third_id'] = third['id']
            assert third['id'] != record['id']
            original = await rig.camera()
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            original_ctx = rig.spoken._hands_requests[task].work['origin']['ctx']
            delegation = 'synthetic-pending-hands-quota'
            handoff = rig.handoff(delegation)
            await final.wait()
            receiving = final.sent.ticket
            # The handoff really proceeded past its declared wait while the
            # original provider stayed entered, charged, and unfinished.
            assert not task.done() and not rig.hands_release.is_set()
            assert origin.admission.attempts[0].status == 'running'
            assert receiving is not origin and receiving.admission is origin.admission
            assert receiving.held(original.id) is original and not receiving.closed
            assert [sent.phase for sent in rig.sends] == ['hands', 'look', 'final']
            receiving_runs = [(run, ctx) for run, ctx, owner in rig.started if owner is receiving]
            assert [run.tool for run, _ctx in receiving_runs] == ['inspect_camera', 'resources', 'resources']
            assert all(run.ok is True for run, _ctx in receiving_runs[:2])
            denied = receiving_runs[2][0]
            assert denied.args['id'] == third['id']
            assert denied.ok is False, 'BEHAVIOR: the third distinct actual inspection must be refused after the timeout handoff'
            assert 'at most two pictures' in denied.summary and not denied.images
            assert len(actual_pictures(rig)) == 2
            assert_spoken_camera(receiving_runs[0][0].images[0], original)
            assert receiving_runs[1][0].images[0].facts['id'] == record['id']
            # A transferred, still-entered Hands planner must settle without
            # executing its late camera plan beside the rightful receiving turn.
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, 3)
            assert not handoff.done() and not final.returned.is_set()
            original_runs = [run for run, ctx, owner in rig.started if owner is origin]
            assert handled is None and not original_runs
            shared = original_ctx.inspection_slots
            assert all(ctx.inspection_slots is shared for _run, ctx in receiving_runs), (
                'MISSING_FEATURE: both actual contexts must use the same original inspection-slot list')
            assert len(shared) == len(actual_pictures(rig)) == 2
            assert all(run.occurrence.request_id == origin.admission.request_id
                       for run, _ctx, _owner in rig.started)
            assert origin.admission.used == 3 and all(sent.admission is origin.admission for sent in rig.sends)
            final.release.set()
            result = await asyncio.wait_for(handoff, 3)
            assert result['answer'] == MIXED_ANSWER
            assert [attempt.status for attempt in origin.admission.attempts] == ['done'] * 3
            before = tuple(rig.sends)
            assert await asyncio.wait_for(rig.handoff(delegation), 2) is result
            assert tuple(rig.sends) == before and origin.admission.used == 3
            await eventually(lambda: origin.closed and receiving.closed,
                             'BEHAVIOR: original and receiving owners settle normally')
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('boundary', ['grace-expiry', 'new-session', 'spoken-cancel'])
def test_tracked_speculative_look_keeps_real_ticket_holds_until_provider_settlement(tmp_path, monkeypatch, boundary):
    async def scenario():
        # The real handoff grace, passed on the loop's own clock once Hands has returned: a short grace also bounded
        # how long Hands itself may take (hold to fast-pass return), which a slow runner exceeds.
        loop, ahead = asyncio.get_running_loop(), [0.0]
        real_time = loop.time
        monkeypatch.setattr(loop, 'time', lambda: real_time() + ahead[0])
        if boundary == 'grace-expiry':
            # The unused look still waits for its hand-off when the grace ends, as it did beside the shortened grace
            # (only lengthened: it bounds nothing Hands does). What is checked is the grace ending under tracked work.
            monkeypatch.setattr(speech, 'LOOK_KEPT_S', speech.HANDS_KEPT_S + HANG_S)

        def response(sent):
            if sent.phase == 'hands':
                if sent.ticket is rig.sends[0].ticket:
                    return {'intent': 'question', 'needs_look': True, 'answer': '', 'actions': []}
                return {'intent': 'cancel', 'needs_look': False, 'answer': '', 'actions': []}
            return {'answer': CAMERA_ANSWER, 'targets': [], 'actions': [],
                    'observation_scope': 'specimen-camera', 'input_refs': ['camera']}

        rig = SpokenRig(tmp_path, monkeypatch, response)
        rig.app.hub.settings.speculate = 'on'
        provider = ResponseGate(monkeypatch, rig, 'look')
        closes = []
        try:
            original = await rig.camera()
            task = rig.hands('What is in the specimen view?')
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            rig.hands_release.set()
            await provider.wait()
            handled = await asyncio.wait_for(task, HANG_S)
            look = handled['look']
            assert look is not None and look.task in origin.tasks and not look.task.done()
            assert origin.sealed and not origin.closed
            assert provider.sent.ticket is origin and provider.sent.admission is origin.admission
            assert provider.sent.phase == 'look' and provider.sent.images
            assert [attempt.status for attempt in origin.admission.attempts] == ['done', 'running']
            assert len(rig.looks) == 1 and len(rig.snapshots) == 1 and not rig.started
            real_holds = tuple(origin.holds)
            assert any(hold.kind == 'look' and hold.capture is look.view[0] for hold in real_holds)
            assert all(not hold.released for hold in real_holds)
            memory = origin.store.memory
            reserved = memory.reserved['analysis']
            assert reserved > 0 and origin.held(original.id) is original
            actual_closed = origin.on_close

            def record_close(owner):
                # Observe actual production closure without changing track,
                # close, guards, holds or settlement. Assertions stay outside.
                closes.append((owner, look.task.done(), provider.returned.is_set()))
                if actual_closed is not None:
                    actual_closed(owner)

            origin.on_close = record_close
            if boundary == 'new-session':
                open_another_session(rig)
                assert origin.superseded('session')
                await asyncio.sleep(0.2)
            elif boundary == 'spoken-cancel':
                cancelled = rig.hands('Never mind; call that request off.')
                cancel_owner = rig.spoken._hands_requests[cancelled]  # capture the actual identity before retirement
                assert await asyncio.wait_for(cancelled, HANG_S) is None
                assert cancel_owner is not origin and cancel_owner.admission is not origin.admission
                assert origin.cancelled and origin.superseded('session')
                assert [attempt.status for attempt in cancel_owner.admission.attempts] == ['done']
                await asyncio.sleep(0.2)
            else:
                keep = handled['camera_keep']
                assert not keep.done(), 'the grace keeps it for a handoff'
                ahead[0] = speech.HANDS_KEPT_S  # the grace passes with no handoff
                await eventually(keep.done, 'BEHAVIOR: the real handoff grace ends on its own clock')
                await asyncio.sleep(0.2)
            # The actual entered look still owns this Ticket's snapshots. Grace
            # ending cannot declare that tracked work finished or release it.
            assert not look.task.done() and not provider.returned.is_set()
            assert not closes, 'MISSING_FEATURE: grace/day/cancel must not forcibly close a Ticket whose actual tracked look is pending'
            assert not origin.closed and look.task in origin.tasks
            assert all(not hold.released and any(hold is member for member in origin.holds)
                       for hold in real_holds)
            assert memory.reserved['analysis'] == reserved and origin.held(original.id) is original
            assert origin.admission.attempts[1].status == 'running'
            provider.release.set()
            await asyncio.wait_for(look.task, HANG_S)
            await eventually(lambda: origin.closed and not origin.holds,
                             'BEHAVIOR: actual tracked look settlement releases its original owner')
            assert closes == [(origin, True, True)]
            assert all(hold.released for hold in real_holds)
            assert [attempt.status for attempt in origin.admission.attempts] == ['done', 'done']
            assert origin.admission.used == 2 and not rig.spoken._handoffs and not rig.started
            await eventually(lambda: memory.reserved['analysis'] == 0,
                             'BEHAVIOR: settled tracked snapshots release their actual reservations')
        finally:
            await rig.close()
    asyncio.run(scenario())


def production_camera_paths(spoken, capture):
    """Inspect only the named production cache roots, not the Rig or observers.

    Completed Task results are followed independently of cached origin dicts;
    removing just one of done/_hands_requests cannot hide the other root.
    Actual live Ticket Holds are inspected separately by the test as ownership.
    """
    paths = []

    def visit(value, path, seen):
        if value is capture:
            paths.append(path)
            return
        if id(value) in seen:
            return
        seen.add(id(value))
        if isinstance(value, ToolContext):
            visit(value.camera_selection, path + '.camera_selection', seen)
        elif isinstance(value, CameraSelection):
            visit(value.capture, path + '.capture', seen)
        elif isinstance(value, asyncio.Task):
            if value.done() and not value.cancelled() and value.exception() is None:
                visit(value.result(), path + '.result()', seen)
        elif isinstance(value, dict):
            for number, (key, item) in enumerate(value.items()):
                visit(key, path + '.key', seen)
                label = f'[{key!r}]' if isinstance(key, str) else f'.value[{number}]'
                visit(item, path + label, seen)
        elif isinstance(value, (tuple, list, set)):
            for number, item in enumerate(value):
                visit(item, path + f'[{number}]', seen)

    # Separate seen sets make independent surviving roots visible in diagnostics.
    visit([request.work for request in spoken.requests], 'SpokenRequests.requests[].work', set())
    visit(spoken._hands_requests, 'SpokenRequests._hands_requests', set())
    visit(spoken._handoffs, 'SpokenRequests._handoffs', set())
    return paths


@pytest.mark.parametrize('route', ['no-handoff', 'consumed-handoff'])
def test_retired_spoken_caches_do_not_own_unreserved_camera_after_ring_eviction(tmp_path, monkeypatch, route):
    async def scenario():
        # The real handoff grace on both routes. With no handoff it is passed on the loop's own clock once Hands has
        # returned: a short grace also bounded how long Hands itself may take, which a slow runner exceeds.
        loop, ahead = asyncio.get_running_loop(), [0.0]
        real_time = loop.time
        monkeypatch.setattr(loop, 'time', lambda: real_time() + ahead[0])
        rig = SpokenRig(tmp_path, monkeypatch, lambda _sent: notes_reply())
        try:
            original = await rig.camera()
            observer_ref = weakref.ref(original)
            task = rig.hands('What did we discuss?')
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            held = tuple(origin.holds)  # observation only, never a production cache or fabricated lease
            assert len(held) == 1 and held[0].capture is original and not held[0].released
            assert origin.store._holds[original.id] >= 1
            assert production_camera_paths(rig.spoken, original)
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            assert handled['ctx'].camera_selection.capture is original
            assert handled['runs'] == [] and not rig.started and not actual_pictures(rig)
            if route == 'consumed-handoff':
                delegation = 'synthetic-cache-retirement'
                result = await asyncio.wait_for(rig.handoff(delegation, words='What did we discuss?'), HANG_S)
                assert result['answer'] == notes_reply()['answer']
                assert rig.turns and not rig.started and not actual_pictures(rig)
                before = tuple(rig.sends)
                assert await asyncio.wait_for(rig.handoff(delegation, words='What did we discuss?'), HANG_S) is result
                assert tuple(rig.sends) == before  # retiring camera roots must preserve delegation replay
            else:
                assert not origin.closed and origin.held(original.id) is original, 'the grace keeps it for a handoff'
                ahead[0] = speech.HANDS_KEPT_S  # the grace passes with no handoff
            await eventually(lambda: origin.closed and not origin.holds,
                             'BEHAVIOR: the original grace/consumed handoff has relinquished its real Hold')
            assert all(hold.released for hold in held)
            rig.app.hub.source.ring_seconds = 0
            later = await rig.camera(value=25, boot='synthetic-after-camera-retirement', seq=1)
            assert later.id != original.id and original.id not in rig.app.hub.source.ring
            assert original.id not in origin.store._held and not origin.store._holds.get(original.id)
            assert all(sent.ticket.closed and not sent.ticket.holds for sent in rig.sends)
            await eventually(lambda: origin.store.memory.reserved['analysis'] == 0,
                             'BEHAVIOR: no actual request reservation owns the retired camera bytes')
            # Observer original/task/handled/Hold and rig.owned may intentionally
            # keep this object alive. They confer no app ownership and are not
            # scanned. The live SpokenRequests caches must retire their roots.
            assert observer_ref() is original
            await eventually(lambda: not production_camera_paths(rig.spoken, original),
                             'MISSING_FEATURE: production done/Hands-task-result roots must retire at expiry or consumption')
            assert not production_camera_paths(rig.spoken, original)
            assert all(sent.admission is origin.admission for sent in rig.sends)
            assert origin.admission.used == len(rig.sends) <= 3
            assert all(attempt.status == 'done' for attempt in origin.admission.attempts)
            if route == 'no-handoff':
                assert len(rig.sends) == 1 and not rig.looks and not rig.snapshots and not rig.spoken._handoffs
        finally:
            await rig.close()
    asyncio.run(scenario())
