"""Actual App/Hands/handoff with a delayed synthetic external planning reply.

Use the real six-second shielded handoff wait and real original capture lease.
No clock, planner, admission, runner, Scale mutation or receipt is substituted.
This is scheduling evidence, not actual GPT-Live or browser acceptance.
"""
import asyncio
import copy
import json
from pathlib import Path

import pytest

from orchestrator.brain.registry import CURRENT_RUN
from orchestrator.brain.dispatch import admitted_facts
from orchestrator.ticket import current
from tests.test_spoken_camera_request_selection import SpokenRig, assert_original_hands
from tests.test_spoken_camera_request_selection import eventually
from tests.test_model_owned_neighbor_discriminators import SnapshotWriteBarrier
from tests.waiting import HANG_S


WORDS = "Set the microscope objective to 40x."
PLAN = "I will record the objective as 40x."
ANSWER = "The objective was recorded as 40x."
ACTION = {'tool': 'set_objective', 'args': {'objective': 40}}


@pytest.mark.parametrize('late_hands', [False, True], ids=['timely-hands', 'handoff-takes-pending-plan'])
def test_spoken_original_request_has_one_effect_owner_after_handoff(tmp_path, monkeypatch, late_hands):
    async def scenario():
        def reply(sent):
            if sent.phase in {'hands', 'look'}:
                return {'intent': 'request', 'needs_look': False, 'answer': PLAN,
                        'actions': [copy.deepcopy(ACTION)], 'targets': [],
                        'observation_scope': 'specimen-camera' if sent.images else 'none',
                        'input_refs': ['camera'] if sent.images else []}
            return {'answer': ANSWER, 'actions': [], 'targets': [],
                    'observation_scope': 'none', 'input_refs': []}

        rig = SpokenRig(tmp_path, monkeypatch, reply)
        rig.app.hub.settings.speculate = 'on'
        mutations = []
        sends = []
        original = None
        external = rig.app.agent.vision(rig.app.hub.settings.fast).ask

        async def observe_provider(images, prompt, **kwargs):
            row = {'admitted': admitted_facts(), 'ticket': current(),
                   'images': tuple(images), 'prompt': prompt, 'status': 'running'}
            sends.append(row)
            try:
                result = await external(images, prompt, **kwargs)
            except BaseException as error:
                row['status'] = 'canceled' if isinstance(error, asyncio.CancelledError) else 'failed'
                row['error'] = type(error).__name__
                raise
            row['status'] = 'done'
            return result

        # Passive external seam: preserve every invocation, including specialist
        # work whose last Ticket attempt alone does not establish admission.
        from types import SimpleNamespace
        monkeypatch.setattr(rig.app.agent, 'vision', lambda _option: SimpleNamespace(ask=observe_provider))

        def observed_scale_change():
            owner = current()
            mutations.append({'run': CURRENT_RUN.get(), 'ticket': owner,
                'facts': copy.deepcopy(rig.app.hub.scale.facts()),
                'original_held': owner is not None and owner.held(original.id) is original})

        rig.app.hub.scale.listeners.append(observed_scale_change)
        try:
            original = await rig.camera()
            frozen_optics = copy.deepcopy(original.scale)
            hands = rig.hands(WORDS)
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            owner = assert_original_hands(rig, hands, original)
            if not late_hands:
                rig.hands_release.set()
                handled = await asyncio.wait_for(hands, HANG_S)
                assert len(handled['runs']) == 1 and handled['runs'][0].ok is True
            handoff = rig.handoff(words=WORDS, delegation='same-original-pending-plan')
            result = await asyncio.wait_for(asyncio.shield(handoff), HANG_S)
            # In the late case the handoff crossed its actual six-second wait;
            # LOOK's actual effect settled while the original FAST was gated.
            assert len(mutations) == 1
            assert result['answer'] == ANSWER
            assert len(result['actions']) == 1 and result['actions'][0]['tool'] == 'set_objective'
            assert result['actions'][0]['ok'] is True
            assert result['actions'][0]['requestId'] == owner.admission.request_id
            assert result['actions'][0]['actionId']
            admitted = [row for row in sends if row['admitted']]
            if late_hands:
                assert [row['admitted']['phase'] for row in admitted] == ['hands', 'look', 'final'], sends
                assert admitted[1]['images'] and len(rig.looks) == 1
                # Retirement may cancel the old send or let it return without
                # actions. Neither outcome permits it to acquire effects again.
                assert not hands.done() or hands.cancelled()
            else:
                assert [row['admitted']['phase'] for row in admitted] == ['hands', 'query'], sends
                assert not rig.looks
            rig.hands_release.set()
            settled = (await asyncio.gather(hands, return_exceptions=True))[0]
            if isinstance(settled, BaseException):
                assert isinstance(settled, asyncio.CancelledError)
            await asyncio.sleep(0)
            assert len(mutations) == 1, 'late original Hands and the delegated turn both executed the same request'
            actual_runs = [(run, ctx, ticket) for run, ctx, ticket in rig.started
                           if run.tool == 'set_objective']
            assert len(actual_runs) == 1
            run, ctx, ticket = actual_runs[0]
            assert run.ok is True and run.occurrence.request_id == owner.admission.request_id
            assert run.wire()['actionId'] == result['actions'][0]['actionId']
            assert ctx.camera_selection.capture is original
            assert mutations[0]['run'] is run and mutations[0]['ticket'] is ticket
            assert mutations[0]['original_held'] is True
            assert mutations[0]['facts']['objective']['value'] == 40
            assert mutations[0]['facts']['objective']['via'] == 'voice'
            assert original.scale == frozen_optics
            assert all(row['admitted']['requestId'] == owner.admission.request_id for row in admitted)
            assert len({row['admitted']['attemptId'] for row in admitted}) == len(admitted)
            assert owner.admission.used == len(admitted) <= 3
            assert owner.admission.attempts[0].status in {'done', 'canceled'}
            assert all(attempt.status == 'done' for attempt in owner.admission.attempts[1:])
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_spoken_handoff_reuses_hands_effect_already_entered_before_wait_expires(tmp_path, monkeypatch):
    """FAST settles before handoff timeout, but actual snapshot IO is still held."""
    async def scenario():
        def reply(sent):
            if sent.phase in {'hands', 'look'}:
                return {'intent': 'request', 'needs_look': False, 'answer': 'I will save this camera view.',
                    'actions': [{'tool': 'snapshot', 'args': {'overlays': False}}], 'targets': [],
                    'observation_scope': 'specimen-camera' if sent.images else 'none',
                    'input_refs': ['camera'] if sent.images else []}
            completed = any(run.tool == 'snapshot' and run.ok is True for run, _ctx, _owner in rig.started)
            return {'answer': 'The snapshot was saved.' if completed else 'The snapshot is still pending.',
                    'actions': [], 'targets': [], 'observation_scope': 'none', 'input_refs': []}

        rig = SpokenRig(tmp_path, monkeypatch, reply)
        rig.app.hub.settings.speculate = 'on'
        barrier = SnapshotWriteBarrier(monkeypatch, tmp_path)
        try:
            original = await rig.camera()
            words = 'Save one clean snapshot of the specimen camera view.'
            hands = rig.hands(words)
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            owner = assert_original_hands(rig, hands, original)
            handoff = rig.handoff(words=words, delegation='same-original-already-started-effect')
            # Real provider latency plus the existing 2.5 s snapshot inline
            # window straddles the unchanged six-second shielded handoff wait.
            await asyncio.sleep(5.2)
            assert not rig.started
            selected_frame = rig.app.hub.best_frame()
            rig.hands_release.set()
            await eventually(barrier.entered.is_set, 'SETUP: real snapshot must enter its PNG writer')
            assert len(barrier.writes) == 1
            first = barrier.writes[0]
            assert first.ticket is owner and first.run.tool == 'snapshot' and first.run.ok is None
            assert first.run.occurrence.request_id == owner.admission.request_id
            assert owner.held(original.id) is original
            assert not first.completed and not first.path.exists()
            await asyncio.sleep(1.2)  # cross actual handoff timeout with real effect still owned
            snapshots = [(run, ctx, ticket) for run, ctx, ticket in rig.started if run.tool == 'snapshot']
            assert len(snapshots) == 1, 'handoff dispatched a second snapshot while original Hands already owned its real IO'
            assert len(barrier.writes) == 1 and first.run.ok is None and not barrier.release.is_set()
            newer = await rig.camera(value=210, seq=81)  # real continuing external camera ingress
            assert newer.id != original.id and newer.jpeg != original.jpeg
            assert owner.held(original.id) is original
            barrier.release.set()
            await asyncio.wait_for(asyncio.gather(hands, handoff), 6)
            assert len([run for run, _ctx, _owner in rig.started if run.tool == 'snapshot']) == 1
            assert len(barrier.writes) == 1 and first.completed and first.path.exists()
            saved = json.loads(first.path.with_suffix('.json').read_text())
            assert saved['frameId'] == selected_frame.id and saved['frameId'] != newer.id
            assert first.run.ok is True
            assert snapshots[0][1].camera_selection.capture is original
            assert first.run.occurrence.request_id == owner.admission.request_id
            assert owner.admission.used <= 3
            assert [attempt.phase for attempt in owner.admission.attempts].count('hands') == 1
        finally:
            barrier.release.set()
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('failure', ['failed', 'canceled'])
def test_spoken_handoff_does_not_replay_an_already_done_failed_effect_owner(tmp_path, monkeypatch, failure):
    """Original producer remains live after its Hands joining task has failed."""
    async def scenario():
        def reply(sent):
            if sent.phase in {'hands', 'look'}:
                return {'intent': 'request', 'needs_look': False, 'answer': 'I will save one snapshot.',
                    'actions': [{'tool': 'snapshot', 'args': {'overlays': False}}], 'targets': [],
                    'observation_scope': 'specimen-camera' if sent.images else 'none',
                    'input_refs': ['camera'] if sent.images else []}
            return {'answer': 'The original snapshot is still pending.', 'actions': [], 'targets': [],
                    'observation_scope': 'none', 'input_refs': []}

        rig = SpokenRig(tmp_path, monkeypatch, reply)
        rig.app.hub.settings.speculate = 'on'
        barrier = SnapshotWriteBarrier(monkeypatch, tmp_path)
        from orchestrator.looplag import Lines

        actual_append = Lines.append
        faults = []
        owner = None

        def external_log_io(lines, path, line):
            # Actual append failure only AFTER the original runner entered real
            # snapshot IO and returned its still-pending receipt at inline expiry.
            # The line is handed over where Hands logs it (looplag.LINES: a disk
            # failure there is counted, never raised), so the fault is raised there.
            if (failure == 'failed' and not faults
                    and Path(path) == rig.app.agent.interaction_log and owner is not None
                    and current() is owner and CURRENT_RUN.get() is None and barrier.entered.is_set()):
                faults.append((path, current(), tuple(rig.started)))
                raise OSError('synthetic original Hands interactions append failed')
            return actual_append(lines, path, line)

        monkeypatch.setattr(Lines, 'append', external_log_io)
        try:
            original = await rig.camera()
            words = 'Save one clean snapshot from this camera view.'
            hands = rig.hands(words)
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            owner = assert_original_hands(rig, hands, original)
            rig.hands_release.set()
            await eventually(barrier.entered.is_set, 'SETUP: real original snapshot writer must be held')
            assert len(barrier.writes) == 1
            first = barrier.writes[0]
            assert first.ticket is owner and first.run.ok is None and not first.completed
            if failure == 'canceled':
                hands.cancel()
            outcome = (await asyncio.wait_for(asyncio.gather(hands, return_exceptions=True), 4))[0]
            expected = OSError if failure == 'failed' else asyncio.CancelledError
            assert isinstance(outcome, expected)
            assert hands.done() and (hands.cancelled() if failure == 'canceled' else hands.exception() is outcome)
            assert bool(faults) is (failure == 'failed')
            assert first.run.task is not None and not first.run.task.done()
            assert not owner.closed and owner.held(original.id) is original
            await rig.camera(value=210, seq=81)  # no stale-camera fallback can mask ownership
            handoff = rig.handoff(words=words, delegation='original-done-owner-must-not-replay')
            with pytest.raises(expected):
                await asyncio.wait_for(asyncio.shield(handoff), 4)
            assert len([run for run, _ctx, _ticket in rig.started if run.tool == 'snapshot']) == 1
            assert len(barrier.writes) == 1 and not first.completed
            assert owner.admission.used == 1 and owner.admission.attempts[0].status == 'done'
            barrier.release.set()
            await asyncio.wait_for(asyncio.shield(first.run.task), 4)
            assert first.completed and first.path.exists() and first.run.ok is True
        finally:
            barrier.release.set()
            await rig.close()
    asyncio.run(scenario())
