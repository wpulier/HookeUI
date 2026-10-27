"""TEST-only original camera selection through actual App and SpokenRequests.

Synthetic JPEGs use real FrameSource ingress, Hub analysis, Captures and holds.
Only external provider replies/delivery and input timing are scripted. No fake
Brain, selection, Ticket, receipt, successful tool, allocator or workspace pin.
Root owns execution; this file is authored without imports, collection or runs.
Camera remains the specimen input; these tests grant no workspace observation.
"""
import asyncio
import copy
import hashlib
import json
import threading
from contextlib import suppress
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.app import App
from orchestrator.brain import speech
from orchestrator.brain.registry import CURRENT_RUN, ToolFailed
from orchestrator.brain.speech import SpokenRequests
from orchestrator.capture import RUN, captures
from orchestrator.commands import COMMANDS
from orchestrator.hub import Client
from orchestrator.inspection import image_context
from orchestrator.marks import fov_crop
from orchestrator.paths import Paths
from orchestrator.ticket import current
from orchestrator.voice.route import brain_tool
from orchestrator.workspace_observation import ObservationError
from tests.test_mixed_camera_retained_context import spatial_camera_jpeg
from tests.test_resource_inspection import assert_supplied_answer_facts
from tests.waiting import HANG_S, until_async


WORDS = 'Compare the specimen view with the selected evidence.'
CAMERA_ANSWER = 'I inspected only the original dated specimen camera crop.'
MIXED_ANSWER = 'I compared the original dated specimen crop with the exact retained picture.'
MISSING_CAMERA = 'The current camera view was requested but is not supplied to this call.'


def selected_camera_reply():
    return {'intent': 'request', 'needs_look': False,
            'answer': 'I am acquiring the original dated specimen view.',
            'actions': [{'tool': 'inspect_camera', 'args': {}}],
            'observation_scope': 'none', 'input_refs': []}


class ExternalVoice:
    """Delivery boundary only; application speech/brain paths stay real."""
    def __init__(self):
        self.closed = False
        self.delivered = []

    async def inject(self, text, **kwargs):
        self.delivered.append((text, kwargs))


class SpokenRig:
    def __init__(self, tmp_path, monkeypatch, response):
        self.app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
        settings = self.app.hub.settings
        settings.fast = 'gemini-3.5-flash'
        settings.compare = ''
        settings.speculate = 'off'
        settings.closeup = 'single'
        settings.pointing = 'box'
        settings.outcome_lines = 'off'
        self.voice = ExternalVoice()
        self.spoken = SpokenRequests(self.app.brain, self.app.dialogue, self.voice)
        self.adapter = brain_tool(self.spoken)
        self.sends, self.started, self.looks, self.snapshots, self.turns = [], [], [], [], []
        self.hands_entered, self.hands_release = asyncio.Event(), asyncio.Event()
        self.query_entered, self.query_release = asyncio.Event(), asyncio.Event()
        self.block_first_hands = True
        self.block_query = False
        self.owned = []
        self.gates = []
        actual_start = self.app.brain.registry.start
        actual_look = self.app.agent.look
        actual_snapshot = self.app.agent.snapshot
        actual_turn = self.app.brain.turn

        async def external_provider(images, prompt, **kwargs):
            ticket = current()
            attempt = ticket.admission.attempts[-1] if ticket is not None and ticket.admission else None
            sent = SimpleNamespace(ticket=ticket, admission=getattr(ticket, 'admission', None),
                                   attempt=attempt, phase=getattr(attempt, 'phase', None),
                                   images=tuple(images), prompt=prompt)
            self.sends.append(sent)
            if sent.phase == 'hands' and not self.hands_entered.is_set():
                self.hands_entered.set()
                if self.block_first_hands:
                    await self.hands_release.wait()
            if sent.phase == 'query':
                self.query_entered.set()
                if self.block_query:
                    await self.query_release.wait()
            try:
                data = response(sent)
            except BaseException as exc:
                sent.provider_error = exc
                raise
            sent.reply_data = copy.deepcopy(data)
            return SimpleNamespace(data=data, model='explicitly-canned-spoken-provider',
                                   latency_ms=0.0, error=None)

        def trace_start(name, args, ctx, run=None):
            made = actual_start(name, args, ctx, run=run)
            self.started.append((made, ctx, current()))
            return made

        async def trace_look(*args, **kwargs):
            self.looks.append((current(), args, kwargs))
            return await actual_look(*args, **kwargs)

        def trace_snapshot(*args, **kwargs):
            self.snapshots.append((current(), args, kwargs))
            return actual_snapshot(*args, **kwargs)

        async def trace_turn(*args, **kwargs):
            result = await actual_turn(*args, **kwargs)
            self.turns.append(result)
            return result

        monkeypatch.setattr(self.app.agent, 'vision', lambda _option: SimpleNamespace(ask=external_provider))
        monkeypatch.setattr(self.app.brain.registry, 'start', trace_start)
        monkeypatch.setattr(self.app.agent, 'look', trace_look)
        monkeypatch.setattr(self.app.agent, 'snapshot', trace_snapshot)
        monkeypatch.setattr(self.app.brain, 'turn', trace_turn)

    async def camera(self, *, value=235, boot='synthetic-spoken-original', seq=71):
        hub = self.app.hub
        count = 4 if hub.latest_info is None else 1
        data = spatial_camera_jpeg(value=value)
        for number in range(count):
            await hub.source._accept(data, seq=seq - count + 1 + number, boot=boot)
        frame_id = hub.source.next_id - 1
        capture = captures(hub).get(frame_id)
        assert capture is not None and capture.jpeg == data
        assert capture.fov is not None, 'FIXTURE: actual Hub must detect the synthetic eyepiece'
        assert hub.source.is_fresh()
        return capture

    def hands(self, words=WORDS):
        task = self.spoken.hands(words)
        self.owned.append(task)
        return task

    def handoff(self, delegation='synthetic-spoken-original-delegation', words=WORDS):
        task = asyncio.create_task(self.adapter.handler({'question': words, 'delegation_id': delegation}))
        self.owned.append(task)
        return task

    async def optics(self, objective):
        result = await COMMANDS['scale'](self.app, Client(None),
                                        {'objective': objective, 'fieldNumber': 18})
        assert result['ok'] is True

    async def close(self):
        self.hands_release.set()
        self.query_release.set()
        for gate in self.gates:
            gate.release.set()
        self.voice.closed = True
        self.spoken.close()
        tasks = [*self.owned, *self.spoken._handoffs.values(),
                 *getattr(self.app.brain, '_query_answers', ()),
                 *getattr(self.app.brain, '_drafts', ())]
        tickets = {sent.ticket for sent in self.sends if sent.ticket is not None}
        tasks += [task for ticket in tickets for task in ticket.tasks]
        tasks += [run.task for run, _ctx, _owner in self.started if run.task is not None]
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for ticket in tickets:
            ticket.close()  # teardown only, never used as proof of successful ownership
        self.app.hub._analysis.shutdown(wait=True)
        self.app.hub._background.shutdown(wait=True)


@pytest.mark.parametrize('selected', ['camera-only'])
def test_spoken_selected_camera_hands_holds_original_before_provider(tmp_path, monkeypatch, selected):
    """First distinct feature RED: real ownership before the external fast await."""
    async def scenario():
        rig = SpokenRig(tmp_path, monkeypatch, lambda _sent: selected_camera_reply())
        try:
            original = await rig.camera()
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            assert selected == 'camera-only'
            sent = rig.sends[0]
            assert sent.phase == 'hands' and sent.ticket is rig.spoken._hands_requests[task]
            assert sent.admission is sent.ticket.admission and sent.admission.used == 1
            assert sent.ticket.held(original.id) is original, (
                'MISSING_FEATURE: SpokenRequests must hold the original real camera Capture '
                'before its first fast provider await, not choose latest after the decision')
            assert not sent.images and not rig.started and not rig.looks and not rig.snapshots
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            assert isinstance(handled, dict) and handled['ticket'] is sent.ticket
            runs = handled['runs']
            assert len(runs) == 1 and runs[0].tool == 'inspect_camera' and runs[0].ok is True
            assert len(runs[0].images) == 1
            assert handled['ctx'].camera_selection.capture is original
            assert handled['ctx'].camera_selection.requested_frame is None
            assert runs[0].images[0].facts['timing'] == 'server-selected-held-frame'
            assert runs[0].images[0].facts['source']['frame'] == original.id
            assert [attempt.phase for attempt in sent.admission.attempts] == ['hands']
            assert [attempt.status for attempt in sent.admission.attempts] == ['done']
            assert not rig.looks and not rig.snapshots
        finally:
            await rig.close()
    asyncio.run(scenario())


def actual_pictures(rig):
    return [image for run, _ctx, _ticket in rig.started if run.ok is True for image in run.images]


def interpretation_reply(rig, answer=CAMERA_ANSWER):
    pictures = actual_pictures(rig)
    return {'intent': 'question', 'needs_look': False, 'answer': answer, 'actions': [],
            'observation_basis': [{'scope': image.facts['channel'], 'input_refs': [number]}
                                  for number, image in enumerate(pictures, 1)]}


def assert_original_hands(rig, task, original):
    first = rig.sends[0]
    assert first.phase == 'hands' and first.ticket is rig.spoken._hands_requests[task]
    assert first.admission is first.ticket.admission and first.admission.used == 1
    assert first.ticket.held(original.id) is original, 'MISSING_FEATURE: original spoken camera Capture was not held before provider entry'
    assert not first.images and not rig.started and not rig.looks and not rig.snapshots
    return first.ticket


def assert_spoken_camera(image, original):
    """Independent original-pixel oracle; never calls the acquisition encoder."""
    facts = image.facts
    digest = lambda data: hashlib.sha256(data).hexdigest()
    assert facts['channel'] == 'specimen-camera'
    assert facts['timing'] == 'server-selected-held-frame'
    assert facts['sha256'] == digest(original.jpeg)
    assert facts['inputSha256'] == digest(image.jpeg)
    assert facts['source'] == {'run': RUN, 'boot': original.boot, 'seq': original.seq,
                               'frame': original.id, 'epoch': original.epoch, 'at': original.wall_at}
    assert facts['scale'] == original.scale
    assert facts['originalSize'] == [original.width, original.height]
    crop = fov_crop(original.fov, original.width, original.height)
    assert crop.x >= 160 and crop.y > 0 and max(crop.width, crop.height) > 1024
    assert facts['crop'] == [crop.x, crop.y, crop.width, crop.height]
    factor = min(1, 1024 / max(crop.width, crop.height))
    size = [round(crop.width * factor), round(crop.height * factor)]
    assert facts['inputSize'] == size and max(size) == 1024
    raw = cv2.imdecode(np.frombuffer(original.jpeg, np.uint8), cv2.IMREAD_COLOR)
    shown = cv2.imdecode(np.frombuffer(image.jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert raw is not None and shown is not None
    region = raw[crop.y:crop.y + crop.height, crop.x:crop.x + crop.width]
    expected = cv2.resize(region, tuple(size), interpolation=cv2.INTER_AREA)
    assert shown.shape == expected.shape
    error = np.abs(shown.astype(np.int16) - expected.astype(np.int16))
    assert float(error.mean()) < 3 and float(np.percentile(error, 95)) < 8
    wrong = cv2.resize(raw[crop.y:crop.y + crop.height,
                          crop.x - 160:crop.x - 160 + crop.width], tuple(size), interpolation=cv2.INTER_AREA)
    full = cv2.resize(raw, tuple(size), interpolation=cv2.INTER_AREA)
    for alternative in (wrong, full):
        assert float(np.abs(expected.astype(np.int16) - alternative.astype(np.int16)).mean()) > 12


def assert_shared_interpretations(rig, origin, handled, result, *, answer=CAMERA_ANSWER):
    budget = origin.admission
    assert [sent.phase for sent in rig.sends] == ['hands', 'query']
    assert [attempt.phase for attempt in budget.attempts] == ['hands', 'query']
    assert [attempt.status for attempt in budget.attempts] == ['done', 'done']
    assert budget.used == 2 and rig.turns[-1]['calls'] == 2
    assert all(sent.admission is budget for sent in rig.sends)
    assert len({attempt.attempt_id for attempt in budget.attempts}) == 2
    assert all(attempt.request_id == budget.request_id for attempt in budget.attempts)
    assert rig.sends[1].ticket is not origin
    assert rig.sends[1].ticket.admission is origin.admission
    assert not rig.looks and rig.app.hub.settings.speculate == 'off'
    assert result['answer'] == answer
    assert handled['ctx'].admission is budget and handled['ctx'].request_snapshot is None
    assert handled['ctx'].client is None and handled['ctx'].workspace_id is None
    images = actual_pictures(rig)
    assert 1 <= len(images) <= 2
    assert_supplied_answer_facts(rig.sends[1], [run.wire() for run, _ctx, _owner in rig.started], images)
    assert rig.sends[1].reply_data == interpretation_reply(rig, answer)
    for sent in rig.sends[1:]:
        assert sent.images == tuple(image.jpeg for image in images)
        assert image_context(images) in sent.prompt
        for number, image in enumerate(images, 1):
            assert json.dumps({'image': number, **image.facts}, default=str) in sent.prompt
        assert MISSING_CAMERA not in sent.prompt
    for run, ctx, owner in rig.started:
        assert run.ok is True and owner is origin and ctx is handled['ctx']
        assert ctx.admission is budget and run.occurrence.request_id == budget.request_id
    assert len(handled['ctx'].inspection_slots) == len(images)


async def eventually(predicate, message):
    await until_async(predicate, what=message)


class EncoderGate:
    """Pause only the actual selected camera's external allocator, then delegate."""
    def __init__(self, monkeypatch, rig):
        self.entered, self.release, self.returned = threading.Event(), threading.Event(), threading.Event()
        actual = cv2.imencode

        def blocked(*args, **kwargs):
            run = CURRENT_RUN.get()
            if run is None or run.tool != 'inspect_camera':
                return actual(*args, **kwargs)
            self.entered.set()
            try:
                if not self.release.wait(10):
                    raise TimeoutError('actual camera encoder gate was not released')
                return actual(*args, **kwargs)
            finally:
                self.returned.set()

        monkeypatch.setattr(cv2, 'imencode', blocked)
        rig.gates.append(self)

    async def wait(self):
        assert await asyncio.to_thread(self.entered.wait, HANG_S), 'BEHAVIOR: the actual camera encoder did not enter its external allocator gate'


@pytest.mark.parametrize('mixed', [False, True], ids=['camera-only', 'camera-and-retained'])
def test_spoken_selected_inputs_use_real_handlers_and_shared_handoff_budget(tmp_path, monkeypatch, mixed):
    async def scenario():
        actions = [{'tool': 'inspect_camera', 'args': {}}]
        rig = SpokenRig(tmp_path, monkeypatch,
            lambda sent: {**selected_camera_reply(), 'actions': actions} if sent.phase == 'hands'
                         else interpretation_reply(rig, MIXED_ANSWER if mixed else CAMERA_ANSWER))
        try:
            retained = None
            if mixed:
                data = spatial_camera_jpeg(value=55)
                retained = rig.app.hub.resources.results.add('compare', {}, 'Synthetic spoken retained picture',
                                                             picture_bytes=[data])
                actions.append({'tool': 'resources', 'args': {'id': retained['id'], 'inspect': True}})
            original = await rig.camera()
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            assert handled['ticket'] is origin and all(run.ok is True for run in handled['runs'])
            assert origin.held(original.id) is original, 'MISSING_FEATURE: completed Hands must retain the original Capture for its bounded handoff window'
            result = await asyncio.wait_for(rig.handoff(), HANG_S)
            assert_shared_interpretations(rig, origin, handled, result,
                                          answer=MIXED_ANSWER if mixed else CAMERA_ANSWER)
            pictures = actual_pictures(rig)
            assert_spoken_camera(pictures[0], original)
            if mixed:
                facts = pictures[1].facts
                assert facts['channel'] == 'retained-source' and facts['id'] == retained['id']
                assert facts['day'] == retained['day'] and facts['picture'] == 0
                assert facts['sha256'] == hashlib.sha256(data).hexdigest()
                assert pictures[0].jpeg != pictures[1].jpeg
            assert rig.turns[-1]['actions'] == result['actions']
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('change', ['slide', 'optics', 'slide-and-optics'])
def test_spoken_delayed_decision_keeps_original_pixels_and_intake_optics(tmp_path, monkeypatch, change):
    async def scenario():
        rig = SpokenRig(tmp_path, monkeypatch,
            lambda sent: selected_camera_reply() if sent.phase == 'hands' else interpretation_reply(rig))
        try:
            await rig.optics(10)
            original = await rig.camera()
            original_scale = copy.deepcopy(original.scale)
            assert original_scale is not None
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            if change in ('optics', 'slide-and-optics'):
                await rig.optics(40)
            rig.app.hub.source.ring_seconds = 0  # external feed retention input, not a replacement lookup
            later = await rig.camera(value=25 if change != 'optics' else 235,
                                     boot='synthetic-spoken-later', seq=1)
            assert original.id not in rig.app.hub.source.ring and origin.held(original.id) is original
            assert later.id != original.id and later.boot != original.boot
            if change != 'optics':
                assert later.jpeg != original.jpeg
            if change != 'slide':
                assert later.scale != original_scale
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            selection = handled['ctx'].camera_selection
            assert selection.capture is original and selection.requested_frame is None
            assert all(run.ok is True for run in handled['runs'])
            assert_spoken_camera(actual_pictures(rig)[0], original)
            assert actual_pictures(rig)[0].facts['scale'] == original_scale
            result = await asyncio.wait_for(rig.handoff(), HANG_S)
            assert_shared_interpretations(rig, origin, handled, result)
            assert handled['ctx'].camera_selection is selection
            assert original.scale == original_scale
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('stage', ['completed-hands', 'pending-fast', 'pending-encoder'])
def test_spoken_handoff_adopts_same_original_hold_and_replay_never_resends(tmp_path, monkeypatch, stage):
    async def scenario():
        from orchestrator.capture import Captures, Hold
        from orchestrator.ticket import Ticket

        rig = SpokenRig(tmp_path, monkeypatch,
            lambda sent: selected_camera_reply() if sent.phase == 'hands' else interpretation_reply(rig))
        gate = EncoderGate(monkeypatch, rig) if stage == 'pending-encoder' else None
        events, ticket_holds = [], []
        origin = None
        actual_ticket_hold, actual_store_hold, actual_release = Ticket.hold, Captures.hold, Hold.release

        def trace_ticket_hold(owner, *args, **kwargs):
            before = tuple(owner.holds)
            try:
                result = actual_ticket_hold(owner, *args, **kwargs)
            except BaseException as exc:
                events.append({'event': 'ticket-hold-error', 'ticket': owner, 'error': type(exc)})
                raise
            for hold in owner.holds:
                if not any(hold is prior for prior in before):
                    ticket_holds.append((owner, hold))
                    events.append({'event': 'ticket-acquired', 'ticket': owner, 'hold': hold,
                                   'capture': hold.capture, 'admission': owner.admission,
                                   'snapshot_started': any(row[0] is owner for row in rig.snapshots)})
            return result

        def trace_store_hold(store, *args, **kwargs):
            try:
                hold = actual_store_hold(store, *args, **kwargs)
            except BaseException as exc:
                events.append({'event': 'store-hold-error', 'store': store, 'error': type(exc)})
                raise
            events.append({'event': 'store-acquired', 'store': store, 'hold': hold,
                           'capture': hold.capture})
            return hold

        def trace_release(hold, *args, **kwargs):
            # Read actual Ticket membership at the release boundary. A worker's
            # store Hold, a reachable Capture or a later snapshot cannot supply it.
            turns = tuple((owner, other) for owner, other in ticket_holds
                          if origin is not None and owner is not origin and owner.kind == 'turn'
                          and owner.admission is origin.admission and other.capture is hold.capture
                          and not other.released and any(other is member for member in owner.holds))
            events.append({'event': 'release-enter', 'hold': hold, 'capture': hold.capture,
                           'was_released': hold.released, 'turn_holds': turns,
                           'turn_owned_same_capture': bool(turns)})
            try:
                result = actual_release(hold, *args, **kwargs)
            except BaseException as exc:
                events.append({'event': 'release-error', 'hold': hold, 'error': type(exc)})
                raise
            events.append({'event': 'release-return', 'hold': hold, 'released': hold.released})
            return result

        try:
            original = await rig.camera()
            monkeypatch.setattr(Ticket, 'hold', trace_ticket_hold)
            monkeypatch.setattr(Captures, 'hold', trace_store_hold)
            monkeypatch.setattr(Hold, 'release', trace_release)
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            originals = [hold for owner, hold in ticket_holds if owner is origin
                         and hold.capture is original and not hold.released
                         and any(hold is member for member in origin.holds)]
            assert len(originals) == 1, 'BEHAVIOR: identify the actual original Hands Ticket Hold before handoff'
            original_hold = originals[0]
            original_stores = [number for number, event in enumerate(events)
                               if event['event'] == 'store-acquired' and event['hold'] is original_hold
                               and event['store'] is origin.store and event['capture'] is original]
            original_acquisitions = [number for number, event in enumerate(events)
                                     if event['event'] == 'ticket-acquired' and event['hold'] is original_hold
                                     and event['ticket'] is origin and event['capture'] is original]
            assert len(original_stores) == len(original_acquisitions) == 1
            assert original_stores[0] < original_acquisitions[0]
            rig.block_query = True
            if stage != 'pending-fast':
                rig.hands_release.set()
                if gate is not None:
                    await gate.wait()
                else:
                    handled = await asyncio.wait_for(task, 3)
                    assert origin.held(original.id) is original
            delegation = 'synthetic-spoken-hold-transfer'
            handoff = rig.handoff(delegation)
            await asyncio.sleep(0)  # the actual adapter starts alongside Hands; no fake handled result
            rig.app.hub.source.ring_seconds = 0
            later = await rig.camera(value=25, boot='synthetic-handoff-later', seq=1)
            assert later.id != original.id and original.id not in rig.app.hub.source.ring
            rig.hands_release.set()
            if gate is not None:
                gate.release.set()
            handled = await asyncio.wait_for(task, 3)
            selection = handled['ctx'].camera_selection
            slots = handled['ctx'].inspection_slots
            occurrence = handled['runs'][0].occurrence
            await asyncio.wait_for(rig.query_entered.wait(), HANG_S)
            query = next(sent for sent in rig.sends if sent.phase == 'query')
            assert query.ticket is not origin and query.admission is origin.admission
            assert query.ticket.held(original.id) is original, 'MISSING_FEATURE: handoff must adopt the original hold before interpretation, not hold only its later snapshot'
            assert handled['ctx'].camera_selection is selection and selection.capture is original
            assert handled['ctx'].inspection_slots is slots and len(slots) == 1
            assert len(rig.started) == 1 and rig.started[0][0].occurrence is occurrence
            assert_spoken_camera(actual_pictures(rig)[0], original)
            await eventually(lambda: origin.closed, 'BEHAVIOR: consumed original Hands hold must release after gap-free transfer')
            # All ordering/ownership assertions are outside the passive wrappers.
            released = [(number, event) for number, event in enumerate(events)
                        if event['event'] == 'release-enter' and event['hold'] is original_hold
                        and event['was_released'] is False]
            assert len(released) == 1 and original_hold.released
            release_at, boundary = released[0]
            assert boundary['turn_owned_same_capture'] is True, (
                'MISSING_FEATURE: at original Hands Hold.release, the actual distinct turn '
                'Ticket must already own its own unreleased Hold of the same Capture')
            # Agent.snapshot may later own another correctly reserved Hold of the
            # same Capture. The original lease transfer is the actual bytes Hold;
            # its existing acquisition/order checks must precede that snapshot.
            transferred = [hold for owner, hold in boundary['turn_holds']
                           if owner is query.ticket and hold.kind == 'bytes']
            assert len(transferred) == 1, 'BEHAVIOR: transfer must reach this actual handoff turn, not another ticket or worker'
            for turn_hold in transferred:
                assert turn_hold is not original_hold and turn_hold.capture is original
                assert not turn_hold.released and any(turn_hold is member for member in query.ticket.holds)
                stores = [number for number, event in enumerate(events)
                          if event['event'] == 'store-acquired' and event['hold'] is turn_hold
                          and event['store'] is query.ticket.store and event['capture'] is original]
                acquired = [(number, event) for number, event in enumerate(events)
                            if event['event'] == 'ticket-acquired' and event['hold'] is turn_hold
                            and event['ticket'] is query.ticket and event['capture'] is original]
                assert len(stores) == len(acquired) == 1
                acquire_at, acquisition = acquired[0]
                assert original_acquisitions[0] < stores[0] < acquire_at < release_at, 'BEHAVIOR: successful real turn Hold adoption must precede original release'
                assert acquisition['snapshot_started'] is False, 'BEHAVIOR: a new Agent.snapshot Hold is not original-request lease transfer'
            rig.query_release.set()
            result = await asyncio.wait_for(handoff, 4)
            assert_shared_interpretations(rig, origin, handled, result)
            before = tuple(rig.sends)
            replay = await asyncio.wait_for(rig.handoff(delegation), 2)
            assert replay is result and tuple(rig.sends) == before
            assert origin.admission.used == 2 and len(rig.started) == 1
            assert query.ticket.closed and not query.ticket.holds
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('absent', ['no-frame', 'stale-frame', 'offline-bridge'])
def test_spoken_original_camera_absence_is_not_healed_by_later_fresh_feed(tmp_path, monkeypatch, absent):
    async def scenario():
        def response(sent):
            if sent.phase == 'hands':
                return selected_camera_reply()
            failures = [run.summary for run, _ctx, _owner in rig.started if run.ok is False]
            return {'intent': 'question', 'needs_look': False,
                    'answer': ' '.join([MISSING_CAMERA, *failures]), 'actions': [],
                    'observation_scope': 'none', 'input_refs': []}

        rig = SpokenRig(tmp_path, monkeypatch, response)
        try:
            source = rig.app.hub.source
            freshness = source.stale_seconds
            if absent == 'stale-frame':
                await rig.camera()
                source.stale_seconds = 0  # external feed freshness state before original selection
                assert not source.is_fresh()
            elif absent == 'offline-bridge':
                source.health_failures = source.offline_after_failures
                assert source.bridge_offline() and not source.is_fresh()
            else:
                assert source.latest() is None and rig.app.hub.latest_info is None
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = rig.sends[0].ticket
            assert origin is rig.spoken._hands_requests[task] and not origin.holds
            source.stale_seconds = freshness
            later = await rig.camera(value=25, boot='synthetic-fresh-after-absence', seq=1)
            assert source.is_fresh() and later.id > 0
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            assert handled['ctx'].camera_selection.capture is None
            assert handled['ctx'].camera_selection.requested_frame is None
            assert len(handled['runs']) == 1 and handled['runs'][0].ok is False
            assert not handled['runs'][0].images and handled['runs'][0].summary
            result = await asyncio.wait_for(rig.handoff(), HANG_S)
            assert [sent.phase for sent in rig.sends] == ['hands', 'query']
            assert all(sent.admission is origin.admission for sent in rig.sends)
            assert [attempt.status for attempt in origin.admission.attempts] == ['done'] * 2
            assert origin.admission.used == 2 and not any(sent.images for sent in rig.sends)
            assert not actual_pictures(rig) and not rig.looks and len(rig.started) == 1
            assert MISSING_CAMERA in result['answer'] and handled['runs'][0].summary in result['answer']
            assert result['answer'] != CAMERA_ANSWER
        finally:
            await rig.close()
    asyncio.run(scenario())


def open_another_session(rig):
    original, generation = rig.app.day.sid, rig.app.day.generation
    rig.app.new_session()  # the actual App path: the view of the session left is cleared (ADR 0017)
    assert rig.app.day.sid != original and rig.app.day.generation > generation


@pytest.mark.parametrize('stage', ['fast-await', 'encoder-await'])
@pytest.mark.parametrize('revocation', ['spoken-cancel', 'new-session'])
def test_spoken_original_request_revocation_refuses_publication_and_keeps_thread_lease(tmp_path, monkeypatch, stage, revocation):
    async def scenario():
        def response(sent):
            if sent.phase == 'hands':
                # Provider return order differs from entry order while the first
                # request is held. Only the actual original Ticket gets its plan.
                if sent.ticket is not rig.sends[0].ticket:
                    return {'intent': 'cancel', 'needs_look': False, 'answer': '', 'actions': []}
                return selected_camera_reply()
            return interpretation_reply(rig)

        rig = SpokenRig(tmp_path, monkeypatch, response)
        gate = EncoderGate(monkeypatch, rig) if stage == 'encoder-await' else None
        try:
            original = await rig.camera()
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            memory = captures(rig.app.hub).memory
            if gate is not None:
                rig.hands_release.set()
                await gate.wait()
                assert memory.reserved['analysis'] > 0
            if revocation == 'spoken-cancel':
                cancelled = rig.hands('Never mind; call that request off.')
                assert await asyncio.wait_for(cancelled, 2) is None
                cancel_origin = rig.spoken._hands_requests[cancelled]
                cancel_sends = [sent for sent in rig.sends if sent.ticket is cancel_origin]
                assert origin.cancelled and len(cancel_sends) == 1
                assert cancel_sends[0].phase == 'hands' and cancel_origin is not origin
                assert cancel_sends[0].admission is cancel_origin.admission
                assert cancel_origin.admission is not origin.admission
                assert [attempt.status for attempt in cancel_origin.admission.attempts] == ['done']
            else:
                open_another_session(rig)
            assert origin.superseded('session')
            assert origin.admission.used == 1
            if gate is not None:
                assert not gate.returned.is_set() and memory.reserved['analysis'] > 0, (
                    'BEHAVIOR: actual camera encoder still owns its allocation while revoked work waits')
                gate.release.set()
            rig.hands_release.set()
            try:
                handled = await asyncio.wait_for(task, 3)
            except (asyncio.CancelledError, ToolFailed, ObservationError):
                handled = None  # an actual refusal/cancellation, never a scripted success
            camera_runs = [run for run, _ctx, _owner in rig.started if run.tool == 'inspect_camera']
            await asyncio.gather(*(run.task for run in camera_runs if run.task is not None), return_exceptions=True)
            assert not any(run.images for run in camera_runs)
            assert all(run.ok is not True for run in camera_runs)
            assert not actual_pictures(rig) and not rig.looks
            assert all(sent.phase == 'hands' and not sent.images for sent in rig.sends)
            assert origin.admission.used == 1 and len(origin.admission.attempts) == 1
            assert origin.admission.attempts[0].status in ('done', 'canceled')
            if isinstance(handled, dict):
                assert all(run.ok is not True for run in handled['runs'])
            await eventually(lambda: origin.closed and not origin.holds,
                             'BEHAVIOR: revoked spoken request must release its bounded original hold')
            if gate is not None:
                assert gate.returned.is_set()
                await eventually(lambda: memory.reserved['analysis'] == 0,
                                 'BEHAVIOR: revoked real encoder must release reservations after its thread returns')
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('hold', ['as-it-comes', 'released-first'])
@pytest.mark.parametrize('handoff_delay', [0, speech.RECENT_VOICE_S + 1], ids=['recent', 'late'])
def test_spoken_handoff_of_the_session_left_cannot_adopt_a_fresh_camera_or_new_answer_budget(tmp_path, monkeypatch, handoff_delay, hold):
    async def scenario():
        rig = SpokenRig(tmp_path, monkeypatch,
            lambda sent: selected_camera_reply() if sent.phase == 'hands' else interpretation_reply(rig))
        try:
            original = await rig.camera()
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, 3)
            assert handled['runs'][0].ok is True and origin.held(original.id) is original
            before = tuple(rig.sends)
            open_another_session(rig)
            assert origin.superseded('session')
            await rig.camera(value=25, boot='synthetic-next-day-camera', seq=1)
            # Advance only the external delivery clock; no camera or request
            # identity changes. Reproduces a late handoff without host-load sleeps.
            if handoff_delay or hold == 'released-first':
                await eventually(lambda: origin.closed and not origin.holds, 'old-session Hands retires before delayed delivery')
                assert 'hands' not in origin.work and 'origin' not in origin.work
            clock = rig.spoken.clock
            rig.spoken.clock = lambda: clock() + handoff_delay
            try:
                result = await asyncio.wait_for(rig.handoff('synthetic-old-day-handoff'), 3)
                refusal = result['answer']
                assert result['answer'] != CAMERA_ANSWER
            except (ToolFailed, ObservationError) as exc:
                refusal = str(exc)
            assert refusal and any(w in refusal.lower() for w in ('session', 'day', 'conversation'))
            assert tuple(rig.sends) == before and origin.admission.used == 1
            assert len(rig.started) == 1 and not rig.looks
            await eventually(lambda: origin.closed and not origin.holds,
                             'BEHAVIOR: old-day original hold may not linger for a fresh handoff')
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('disconnect', ['talk-close', 'delivery-task-cancel'])
def test_spoken_delivery_disconnect_keeps_original_delegation_and_cleanup(tmp_path, monkeypatch, disconnect):
    async def scenario():
        rig = SpokenRig(tmp_path, monkeypatch,
            lambda sent: selected_camera_reply() if sent.phase == 'hands' else interpretation_reply(rig))
        try:
            original = await rig.camera()
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            rig.block_query = True
            delegation = 'synthetic-spoken-delivery-disconnect'
            delivery = rig.handoff(delegation)
            await asyncio.wait_for(rig.query_entered.wait(), HANG_S)
            delegate = rig.spoken._handoffs[delegation]
            query = next(sent for sent in rig.sends if sent.phase == 'query')
            assert query.ticket.held(original.id) is original
            assert query.admission is origin.admission and origin.admission.used == 2
            rig.voice.closed = True
            rig.spoken.close()  # actual Talk teardown files speech; it does not cancel authorized work
            if disconnect == 'delivery-task-cancel':
                delivery.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await delivery
            assert not delegate.done() and not delegate.cancelling()
            assert not origin.cancelled and not query.ticket.cancelled
            rig.app.hub.source.ring_seconds = 0
            await rig.camera(value=25, boot='synthetic-after-talk-close', seq=1)
            rig.query_release.set()
            result = await asyncio.wait_for(asyncio.shield(delegate), HANG_S)
            assert_shared_interpretations(rig, origin, handled, result)
            assert_spoken_camera(actual_pictures(rig)[0], original)
            assert query.ticket.closed and not query.ticket.holds
            await eventually(lambda: origin.closed, 'BEHAVIOR: delivery detach must not leak the original Hands hold')
            assert await asyncio.wait_for(rig.handoff(delegation), HANG_S) is result
            assert len(rig.sends) == 2 and len(rig.started) == 1
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('selected', ['camera', 'notes'])
def test_spoken_unconsumed_original_hold_expires_without_forced_acquisition(tmp_path, monkeypatch, selected):
    async def scenario():
        # The real handoff grace, passed on the loop's own clock once Hands has returned: a short grace also bounded
        # how long Hands itself may take (hold to fast-pass return), which a slow runner exceeds.
        loop, ahead = asyncio.get_running_loop(), [0.0]
        real_time = loop.time
        monkeypatch.setattr(loop, 'time', lambda: real_time() + ahead[0])
        reply = selected_camera_reply() if selected == 'camera' else {
            'intent': 'question', 'needs_look': False, 'answer': 'The conversation notes answer this.',
            'actions': [], 'observation_scope': 'none', 'input_refs': []}
        rig = SpokenRig(tmp_path, monkeypatch, lambda _sent: reply)
        try:
            original = await rig.camera()
            task = rig.hands()
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            if selected == 'camera':
                assert handled['runs'][0].ok is True
                assert_spoken_camera(actual_pictures(rig)[0], original)
            else:
                assert handled['runs'] == [] and not rig.started and not actual_pictures(rig)
            assert not origin.closed and origin.held(original.id) is original, 'the grace keeps it for a handoff'
            ahead[0] = speech.HANDS_KEPT_S  # the grace passes with no handoff
            await eventually(lambda: origin.closed and not origin.holds,
                             'MISSING_FEATURE: original spoken JPEG hold must expire without a handoff')
            assert origin.held(original.id) is None
            assert not rig.looks and not rig.snapshots and not rig.spoken._handoffs
            assert len(rig.sends) == origin.admission.used == 1
            assert origin.admission.attempts[0].status == 'done'
            assert not rig.sends[0].images
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_spoken_retained_only_selection_does_not_acquire_camera_or_workspace(tmp_path, monkeypatch):
    async def scenario():
        record = {}
        answer = 'I inspected only the exact retained picture.'

        def response(sent):
            if sent.phase == 'hands':
                return {**selected_camera_reply(), 'actions': [
                    {'tool': 'resources', 'args': {'id': record['id'], 'inspect': True}}]}
            return interpretation_reply(rig, answer)

        rig = SpokenRig(tmp_path, monkeypatch, response)
        try:
            data = spatial_camera_jpeg(value=55)
            record.update(rig.app.hub.resources.results.add('compare', {}, 'Synthetic retained-only voice input',
                                                            picture_bytes=[data]))
            original = await rig.camera()
            task = rig.hands()  # identical words across camera/mixed/retained selections
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            assert len(rig.started) == 1 and rig.started[0][0].tool == 'resources'
            result = await asyncio.wait_for(rig.handoff(), HANG_S)
            assert_shared_interpretations(rig, origin, handled, result, answer=answer)
            images = actual_pictures(rig)
            assert len(images) == 1 and images[0].facts['channel'] == 'retained-source'
            assert images[0].facts['id'] == record['id']
            assert images[0].facts['sha256'] == hashlib.sha256(data).hexdigest()
            assert not any(run.tool == 'inspect_camera' for run, _ctx, _owner in rig.started)
            assert not any(image.facts['channel'] == 'workspace' for image in images)
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_spoken_specimen_question_uses_camera_default_without_workspace_authority(tmp_path, monkeypatch):
    async def scenario():
        def response(sent):
            if sent.phase == 'hands':
                return {'intent': 'question', 'needs_look': True, 'answer': '', 'actions': []}
            return {'answer': CAMERA_ANSWER, 'targets': [], 'actions': [],
                    'observation_scope': 'specimen-camera', 'input_refs': ['camera']}

        rig = SpokenRig(tmp_path, monkeypatch, response)
        try:
            original = await rig.camera()
            task = rig.hands('What is in the specimen view?')
            await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
            origin = assert_original_hands(rig, task, original)
            rig.hands_release.set()
            handled = await asyncio.wait_for(task, HANG_S)
            assert handled['decision'].needs_look is True and handled['runs'] == []
            result = await asyncio.wait_for(rig.handoff(words='What is in the specimen view?'), HANG_S)
            assert [sent.phase for sent in rig.sends] == ['hands', 'look']
            assert len(rig.looks) == 1 and rig.sends[1].images
            assert all(sent.admission is origin.admission for sent in rig.sends)
            assert [attempt.status for attempt in origin.admission.attempts] == ['done', 'done']
            assert origin.admission.used == 2 and result['answer'] == CAMERA_ANSWER
            assert not rig.started and not actual_pictures(rig)
            assert 'whole-workspace pixels' not in result['answer']
            assert not rig.app.hub.clients  # no accepted browser or workspace authority fixture is supplied
        finally:
            await rig.close()
    asyncio.run(scenario())
