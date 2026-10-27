"""TEST-ONLY request adoption through actual COMMANDS.ask/App/Brain/Registry.

Synthetic accepted sockets, canonical renderer reports and retained JPEGs reuse
unchanged Screen fixtures. A synthetic camera JPEG enters the real FrameSource
ring and Hub analysis. Only external answer providers are canned; no Agent,
Brain, executor, observer or successful ToolRun is substituted. Trace wrappers
call the original Screen.pin/Brain.moment and return their exact results.

This bounded proposal uses ask['screen'] for the inline canonical Send report,
Moment.request_snapshot for the immutable pin, and model observation_scope plus
input_refs for declared visual scope. Root owns transport/schema adoption.
Passing is synthetic application evidence, not browser, camera or model quality.
No lifespan, server, GPU, network or private dataset is started.
"""
import asyncio
import copy
import time
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ACTIVITY_KEYS, Tool, ToolFailed, schema
from orchestrator.commands import COMMANDS
from orchestrator.frames import Frame
from orchestrator.ticket import current
from orchestrator.workspace_observation import ClientRef, ObservationError, RequestSnapshot
from tests.test_screen_workspace import REF, Rig, jpeg
from tests.waiting import HANG_S


OTHER = ClientRef('peer-workspace', 'peer-client', 'peer-page', 'peer-socket')
CLAIM = 'I visually inspected every workspace window and all their controls.'
LIMIT = 'I cannot visually confirm the whole workspace: its pixels were not provided.'
PLAN = 'The registered windows can be arranged side by side; this is a structural plan.'


class CannedProvider:
    """External provider boundary only; dispatch/admission remain production code."""
    def __init__(self, rig, *, mode='observe', gate=False):
        self.rig, self.mode, self.gate = rig, mode, gate
        self.sends = []
        self.entered, self.release = asyncio.Event(), asyncio.Event()

    async def ask(self, images, prompt, **kwargs):
        ticket = current()
        assert ticket is not None and ticket.admission is not None, 'BEHAVIOR: provider send needs the real admitted Ticket'
        phase = ticket.admission.attempts[-1].phase
        self.sends.append(SimpleNamespace(ticket=ticket, admission=ticket.admission,
            phase=phase, images=tuple(images), prompt=prompt,
            pins=tuple(self.rig.pins), moments=tuple(self.rig.moments)))
        self.entered.set()
        if self.gate and len(self.sends) == 1:
            await self.release.wait()
        if self.mode == 'observe':
            data = {'answer': 'I am reading this request’s structural facts.', 'targets': [],
                'intent': 'request', 'needs_look': False, 'observation_scope': 'structural-workspace',
                'input_refs': [], 'actions': [{'tool': 'fixture_request_facts', 'args': {}}]}
        elif self.mode == 'plan':
            data = {'answer': PLAN, 'targets': [], 'actions': [], 'intent': 'question',
                'needs_look': False, 'observation_scope': 'structural-workspace', 'input_refs': []}
        else:
            # Deliberately unsupported first-call claim, never a canned successful
            # workspace inspection. Later replies may express the missing input.
            data = {'answer': LIMIT if phase in {'query', 'final', 'late'} else CLAIM,
                'targets': [], 'actions': [], 'intent': 'question', 'needs_look': False,
                'observation_scope': 'workspace-pixels', 'input_refs': []}
        return SimpleNamespace(data=data, model='explicitly-canned-external-provider',
                               latency_ms=0.0, error=None)


class RequestRig(Rig):
    def __init__(self, tmp_path, monkeypatch, *, fast='off', mode='observe', gate=False):
        super().__init__(tmp_path)
        self.surface_allowed = False  # no fixture grants workspace pixel authority
        self.accept()
        self.accept(self.other, OTHER)
        self.app.hub.settings.fast = fast
        self.app.hub.settings.compare = ''
        self.app.hub.settings.pointing = 'box'
        self.app.hub.settings.closeup = 'single'
        self.app.hub.settings.outcome_lines = 'off'
        self.pins, self.moments, self.contexts = [], [], []
        original_pin, original_moment = self.screen.pin, self.app.brain.moment

        def trace_pin(client, **kwargs):
            pin = original_pin(client, **kwargs)
            self.pins.append((client, kwargs['request_id'], pin))
            return pin

        def trace_moment(*args, **kwargs):
            moment = original_moment(*args, **kwargs)
            self.moments.append(moment)
            return moment

        monkeypatch.setattr(self.screen, 'pin', trace_pin)
        monkeypatch.setattr(self.app.brain, 'moment', trace_moment)
        self.provider = CannedProvider(self, mode=mode, gate=gate)
        monkeypatch.setattr(self.app.agent, 'vision', lambda _option: self.provider)

        async def request_facts(ctx, _args):
            # A test-only read capability, invoked by the real Registry/ActionRunner.
            # It observes ctx/current Ticket and makes no effect or success record.
            self.contexts.append((ctx, current()))
            return 'Synthetic request facts read; no visual inspection was performed.'

        self.app.brain.registry.add(Tool('fixture_request_facts',
            'Read request structural attribution (synthetic test capability).', schema({}),
            request_facts, kind='effect', family='view'))

    async def camera(self):
        now = time.monotonic()
        frame = Frame(1, jpeg(), now, time.time(), seq=1, boot='synthetic-camera-provider')
        self.app.hub.source.ring[frame.id] = frame
        self.app.hub.source.last_frame_at = now
        await self.app.hub._on_frame(frame)  # actual analysis and publication, no started feed

    async def send(self, *, client=None, screen=None, text='Read the facts for this workspace request.'):
        return await COMMANDS['ask'](self.app, self.client if client is None else client,
                                    {'type': 'ask', 'text': text, 'screen': screen or self.message()})

    def close(self):
        self.app.hub._analysis.shutdown(wait=True)
        self.app.hub._background.shutdown(wait=True)


def test_spoken_hands_and_handoff_keep_original_workspace_and_admission(tmp_path, monkeypatch):
    from orchestrator.brain.speech import SpokenRequests
    rig = RequestRig(tmp_path, monkeypatch, fast='gemini-3.5-flash')

    async def run():
        await rig.camera()
        rig.screen.report(rig.message(), client=rig.client)
        rig.screen.report(rig.message(ref=OTHER), client=rig.other)
        spoken = SpokenRequests(rig.app.brain, client=rig.client)
        handled = await spoken.hands('Zoom that cell')
        original = handled['ctx']
        assert original.client is rig.client
        assert original.request_snapshot.observation.client == REF
        await spoken.handoff('Zoom that cell', 'original-voice-delegation')
        assert rig.contexts
        assert all(ctx.client is rig.client and ctx.request_snapshot is original.request_snapshot
                   and ctx.admission is original.admission for ctx, _ in rig.contexts)
        assert all(send.admission is original.admission for send in rig.provider.sends)
        assert all(moment.request_snapshot is original.request_snapshot for moment in rig.moments)
    try:
        asyncio.run(run())
    finally:
        rig.close()


def test_spoken_transcript_pin_survives_later_reports_and_fast_provider_failure(tmp_path, monkeypatch):
    from orchestrator.brain.speech import SpokenRequests
    rig = RequestRig(tmp_path, monkeypatch, fast='gemini-3.5-flash')
    ask = rig.provider.ask
    entered = 0

    async def fail_first(*args, **kwargs):
        nonlocal entered
        entered += 1
        if entered == 1:
            raise RuntimeError('synthetic provider outage')
        return await ask(*args, **kwargs)

    monkeypatch.setattr(rig.provider, 'ask', fail_first)

    async def run():
        await rig.camera()
        rig.screen.report(rig.message(seq=10), client=rig.client)
        spoken = SpokenRequests(rig.app.brain, client=rig.client)
        spoken.heard('Zoom that cell')  # its pin is the Studio's as of these words
        rig.screen.report(rig.message(seq=11), client=rig.client)  # a later report while they settle
        while not spoken.requests or spoken.requests[-1].work.get('hands') is None:
            await asyncio.sleep(0.01)
        handled = await spoken.requests[-1].work['hands']  # the request's own record carries its pin
        original = handled['ctx']
        assert handled['plan_failed']
        assert original.request_snapshot.observation.wire()['seq'] == 10
        assert original.request_snapshot.timing == 'server-final-transcript'
        await spoken.handoff('Zoom that cell', 'recovering-delegation')
        assert rig.contexts and all(ctx.client is rig.client and ctx.request_snapshot is original.request_snapshot
                                    for ctx, _ in rig.contexts)
        assert original.admission.used <= 3
        spoken.close()
    try:
        asyncio.run(run())
    finally:
        rig.close()


def assert_attribution(rig, expected, client):
    sends = rig.provider.sends
    assert sends, 'BEHAVIOR: the canned provider must be reached through real dispatch'
    first = sends[0]
    assert first.pins, 'MISSING_FEATURE: COMMANDS.ask must pin inline Send facts before its first provider dispatch'
    socket, request_id, pin = first.pins[-1]
    assert socket is client and isinstance(pin, RequestSnapshot)
    assert request_id == first.admission.request_id, 'MISSING_FEATURE: Send pin and Brain must retain one original Ticket/admission'
    assert pin.timing == 'client-send' and pin.observation.wire() == expected
    assert first.moments, 'BEHAVIOR: actual Brain planning must capture a Moment'
    assert getattr(first.moments[-1], 'request_snapshot', None) is pin, 'MISSING_FEATURE: actual Moment must retain the canonical originating request pin'
    assert rig.contexts, 'BEHAVIOR: real Registry must invoke the canned-provider-selected read capability'
    ctx, ticket = rig.contexts[-1]
    assert ticket is first.ticket and ctx.admission is first.admission
    assert getattr(ctx, 'client', None) is client, 'MISSING_FEATURE: actual ToolContext must retain the accepted originating socket'
    assert getattr(ctx, 'workspace_id', None) == pin.observation.client.workspace_id
    assert getattr(ctx, 'request_snapshot', None) is pin, 'MISSING_FEATURE: actual ToolContext must retain the same immutable Send pin'
    assert getattr(ctx, 'request_id', None) == request_id
    assert all(send.admission is first.admission for send in sends)
    assert first.admission.used == len(sends) <= 3, 'BEHAVIOR: request observation must preserve MAX_CALLS=3'
    return pin


@pytest.mark.parametrize('fast', ['off', 'gemini-3.5-flash'])
def test_typed_send_pins_before_dispatch_and_attributes_actual_moment_and_context(tmp_path, monkeypatch, fast):
    rig = RequestRig(tmp_path, monkeypatch, fast=fast)
    async def scenario():
        await rig.camera()
        raw = rig.message(seq=11)
        await rig.ingest(raw)
        expected = rig.observed().wire()
        await rig.send(screen=copy.deepcopy(raw))  # exact inline retry is allowed
        pin = assert_attribution(rig, expected, rig.client)
        raw['items'][0]['rect'][0] = 901
        assert pin.observation.wire() == expected
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_originating_send_survives_peer_and_same_client_updates_while_provider_waits(tmp_path, monkeypatch):
    rig = RequestRig(tmp_path, monkeypatch, gate=True)
    async def scenario():
        await rig.camera()
        raw = rig.message(seq=11)
        await rig.ingest(raw)
        expected = rig.observed().wire()
        pending = asyncio.create_task(rig.send(screen=copy.deepcopy(raw)))
        try:
            await asyncio.wait_for(rig.provider.entered.wait(), HANG_S)
            peer = rig.message(seq=80, ref=OTHER)
            peer['items'][0]['rect'][0] = 700
            assert (await rig.ingest(peer, rig.other))['ok']
            newer = rig.message(seq=12)
            newer['items'][0]['rect'][0] = 800
            assert (await rig.ingest(newer))['ok']
            rig.provider.release.set()
            await pending
            first_pin = assert_attribution(rig, expected, rig.client)
            assert first_pin.observation.wire()['seq'] == 11
            rig.provider.gate = False
            rig.provider.sends.clear()
            rig.contexts.clear()
            peer_expected = rig.observed(rig.other).wire()
            await rig.send(client=rig.other, screen=copy.deepcopy(peer))
            peer_pin = assert_attribution(rig, peer_expected, rig.other)
            assert peer_pin is not first_pin and peer_pin.observation.client == OTHER
            assert first_pin.observation.wire() == expected
        finally:
            rig.provider.release.set()
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('field,value,code', [
    ('pageEpoch', 'wrong-page', 'CLIENT_MISMATCH'),
    ('appRunId', 'wrong-run', 'RUN_MISMATCH'),
    ('schema', 'invalid-schema', 'INVALID_REPORT'),
])
def test_invalid_inline_send_refuses_before_any_provider_dispatch(tmp_path, monkeypatch, field, value, code):
    rig = RequestRig(tmp_path, monkeypatch)
    async def scenario():
        await rig.camera()  # refusal cannot accidentally pass because camera is absent
        await rig.ingest()
        before = rig.observed().wire()
        bad = rig.message(seq=11)
        (bad['client'] if field == 'pageEpoch' else bad)[field] = value
        try:
            result = await rig.send(screen=bad)
        except ObservationError as exc:
            result = {'ok': False, 'code': exc.code}
        assert not rig.provider.sends, 'MISSING_FEATURE: invalid inline Send observation must be refused before dispatch, without latest-report fallback'
        assert result.get('ok') is False and result.get('code') == code
        assert rig.observed().wire() == before
        assert not rig.contexts
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('fast', ['off', 'gemini-3.5-flash'])
def test_no_action_whole_workspace_claim_requires_that_calls_actual_workspace_pixels(tmp_path, monkeypatch, fast):
    rig = RequestRig(tmp_path, monkeypatch, fast=fast, mode='unsupported')
    original_provider = rig.provider.ask
    returned = []

    async def eligible_external_correction(images, prompt, **kwargs):
        reply = await original_provider(images, prompt, **kwargs)
        phase = rig.provider.sends[-1].phase
        if phase == 'final':
            # Only this external response changes: its explanation claims no pixels.
            reply.data = {'answer': LIMIT, 'actions': [], 'observation_scope': 'none', 'input_refs': []}
        returned.append((phase, copy.deepcopy(reply.data)))
        return reply

    monkeypatch.setattr(rig.provider, 'ask', eligible_external_correction)
    async def scenario():
        await rig.camera()
        await rig.ingest()
        result = await rig.send(text='Describe visually the whole workspace and every window control.')
        sends = rig.provider.sends
        assert sends and not rig.surface_checks, 'BEHAVIOR: fixture supplies no workspace capture authority or pixels'
        if fast == 'off':
            assert sends[0].images, 'BEHAVIOR: actual Agent.look must encode the synthetic camera input'
        else:
            assert next(send for send in sends if send.phase == 'fast').images == (), 'BEHAVIOR: actual fast provider receives zero images'
        assert not rig.contexts, 'BEHAVIOR: deliberately no-action provider reply must remain no-action'
        answer = result['answer']
        assert CLAIM not in answer, 'MISSING_FEATURE: no-action replies cannot preserve unsupported whole-workspace visual claims from camera-only or zero-image calls'
        assert answer == LIMIT and result['answerStatus'] == 'model'
        assert returned[0][1]['answer'] == CLAIM and returned[0][1]['observation_scope'] == 'workspace-pixels'
        assert returned[0][1]['input_refs'] == []
        finals = [send for send in sends if send.phase == 'final']
        assert len(finals) == 1 and finals[0].images == ()
        assert "This call received no complete verified workspace pixels supporting the reply's declaration." in finals[0].prompt
        assert returned[-1] == ('final', {'answer': LIMIT, 'actions': [], 'observation_scope': 'none', 'input_refs': []})
        history = rig.app.dialogue.history()['lines']
        assert [row['text'] for row in history if row.get('role') == 'assistant' and row.get('turnId') == result['turnId']] == [LIMIT]
        assert sends[0].admission.used == len(sends) <= 3
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_no_camera_still_allows_structural_workspace_planning(tmp_path, monkeypatch):
    rig = RequestRig(tmp_path, monkeypatch, fast='gemini-3.5-flash', mode='plan')
    async def scenario():
        await rig.ingest()
        assert rig.app.hub.source.latest() is None
        try:
            result = await rig.send(text='Plan how to arrange the registered workspace windows side by side.')
        except ToolFailed as exc:
            if str(exc) == 'there is no fresh picture from the camera to look at':
                pytest.fail('MISSING_FEATURE: camera absence must not prevent structural workspace planning through actual COMMANDS.ask/Brain', pytrace=False)
            raise  # retain any unrelated dependency/product failure
        assert rig.provider.sends, 'MISSING_FEATURE: no-camera workspace planning must reach an admitted notes-only provider'
        assert all(not send.images for send in rig.provider.sends)
        assert result.get('ok') is True and result['answer'] == PLAN
        assert 'window-a' in rig.provider.sends[0].prompt, 'MISSING_FEATURE: no-camera planning must receive registered structural workspace facts'
        assert rig.provider.sends[0].admission.used == len(rig.provider.sends) <= 3
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('fast', ['off', 'gemini-3.5-flash'])
def test_accepted_ask_without_inline_report_pins_original_legacy_receive_timing(tmp_path, monkeypatch, fast):
    """An accepted fallback is an older server report, never a client-Send observation."""
    rig = RequestRig(tmp_path, monkeypatch, fast=fast, gate=True)
    original_provider_send = rig.provider.ask

    async def hold_canned_response(images, prompt, **kwargs):
        # Gate every external reply, including the fast sibling, without replacing
        # production dispatch, pinning, Moment capture or Registry execution.
        reply = await original_provider_send(images, prompt, **kwargs)
        await rig.provider.release.wait()
        return reply

    monkeypatch.setattr(rig.provider, 'ask', hold_canned_response)

    async def scenario():
        await rig.camera()
        assert (await rig.ingest())['ok'] is True
        original = rig.observed()
        expected = original.wire()
        assert original.received_at == 10 and expected['seq'] == 10
        rig.now = 10.25
        message = {'type': 'ask', 'text': 'Read the facts for this accepted workspace request.'}
        assert 'screen' not in message  # deliberately exercise the real no-inline path
        pending = asyncio.create_task(COMMANDS['ask'](rig.app, rig.client, message))
        entered = asyncio.create_task(rig.provider.entered.wait())
        try:
            completed, _ = await asyncio.wait((pending, entered), timeout=1,
                                              return_when=asyncio.FIRST_COMPLETED)
            if pending in completed and not rig.provider.sends:
                result = pending.result()  # retain unrelated exceptions rather than relabeling them
                assert result.get('ok') is True, (
                    'MISSING_FEATURE: accepted no-inline ask must adopt the original report with '
                    f'canonical server-receive-legacy timing; actual refusal precursor: {result!r}')
            assert rig.provider.sends, 'MISSING_FEATURE: accepted no-inline ask must pin the stored report before an admitted provider dispatch'
            first = rig.provider.sends[0]
            assert first.pins, 'MISSING_FEATURE: accepted no-inline ask must pin before its first provider dispatch'
            socket, request_id, pin = first.pins[-1]
            assert socket is rig.client and isinstance(pin, RequestSnapshot)
            assert pin.timing == 'server-receive-legacy', 'BEHAVIOR: no-inline server fallback cannot claim client-Send chronology'
            assert request_id == first.admission.request_id
            assert pin.requested_at == 10.25 and pin.report_age_ms == 250
            assert pin.observation.received_at == 10 and pin.observation.wire() == expected
            assert first.moments and first.moments[-1].request_snapshot is pin
            rig.now = 10.5
            peer = rig.message(seq=80, ref=OTHER)
            peer['items'][0]['rect'][0] = 700
            assert (await rig.ingest(peer, rig.other))['ok'] is True
            assert rig.observed(rig.other).wire()['seq'] == 80
            rig.provider.release.set()
            result = await pending
            assert result.get('ok') is True
            assert rig.contexts, 'BEHAVIOR: real Registry must execute the selected request-facts capability'
            ctx, ticket = rig.contexts[-1]
            assert ticket is first.ticket and ctx.admission is first.admission
            assert ctx.client is rig.client and ctx.request_id == request_id
            assert ctx.request_snapshot is pin and ctx.workspace_id == REF.workspace_id
            assert pin.observation.wire() == expected and pin.report_age_ms == 250
            assert pin.observation.received_at == 10 and pin.requested_at == 10.25
            assert rig.observed().wire() == expected, 'BEHAVIOR: a peer report must not refresh the original accepted report'
            assert all(send.admission is first.admission for send in rig.provider.sends)
            assert first.admission.used == len(rig.provider.sends) <= 3
        finally:
            rig.provider.release.set()
            for task in (pending, entered):
                if not task.done():
                    task.cancel()
            await asyncio.gather(pending, entered, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('late_images', ['none', 'retained-camera'])
def test_late_query_workspace_claim_requires_actual_workspace_inputs_on_that_late_call(tmp_path, monkeypatch, late_images):
    """Held real resource query; no pixels, or retained camera pixels, never workspace pixels."""
    import json
    from orchestrator.brain.registry import CURRENT_RUN

    rig = RequestRig(tmp_path, monkeypatch)
    query_entered, query_release = asyncio.Event(), asyncio.Event()
    query_contexts, delivered = [], []
    source_record = {}
    actual_resources = rig.app.brain.registry.tools['resources'].handler
    actual_dialogue_show = rig.app.dialogue.show

    def trace_delivery(line, **kwargs):
        shown = actual_dialogue_show(line, **kwargs)  # persist/publish the real result first
        if shown.get('type') == 'chat-action':
            delivered.append(copy.deepcopy(shown))
        return shown

    monkeypatch.setattr(rig.app.dialogue, 'show', trace_delivery)

    async def held_actual_resource_query(ctx, _args):
        run = CURRENT_RUN.get()  # the run made by actual Registry/ActionRunner, never hand-written
        assert run is not None and run.tool == 'fixture_late_resource_query'
        query_contexts.append((ctx, current(), run))
        query_entered.set()
        await query_release.wait()
        return await actual_resources(ctx, {'id': source_record['id'],
                                             'inspect': late_images == 'retained-camera', 'picture': 0})

    rig.app.brain.registry.add(Tool('fixture_late_resource_query',
        'Held actual resources query for synthetic deferred-answer regression.', schema({}),
        held_actual_resource_query, kind='query', inline_ms=0, family='view'))

    async def canned_late_scope_provider(images, prompt, **kwargs):
        ticket = current()
        assert ticket is not None and ticket.admission is not None
        phase = ticket.admission.attempts[-1].phase
        rig.provider.sends.append(SimpleNamespace(ticket=ticket, admission=ticket.admission,
            phase=phase, images=tuple(images), prompt=prompt, pins=tuple(rig.pins),
            moments=tuple(rig.moments), closed_at_send=ticket.closed))
        if phase in ('query', 'final'):
            data = {'answer': CLAIM, 'actions': [], 'observation_scope': 'workspace-pixels',
                    'input_refs': [1] if images else []}
        elif phase == 'look':
            data = {'answer': 'I am reading the retained camera resource.', 'targets': [],
                    'observation_scope': 'structural-workspace', 'input_refs': [],
                    'actions': [{'tool': 'fixture_late_resource_query', 'args': {}}]}
        else:
            data = {'answer': 'The query is still pending.', 'actions': [],
                    'observation_scope': 'none', 'input_refs': []}
        rig.provider.sends[-1].reply_data = copy.deepcopy(data)
        return SimpleNamespace(data=data, model='explicitly-canned-late-scope-provider',
                               latency_ms=0.0, error=None)

    monkeypatch.setattr(rig.provider, 'ask', canned_late_scope_provider)

    async def scenario():
        await rig.camera()
        await rig.ingest()
        expected = rig.observed().wire()
        frame = rig.app.hub.source.get(1)
        # Existing Results retains the actual synthetic camera-input JPEG with
        # its real Hub frame/bridge seq/boot facts. It is not a workspace capture.
        source_record.update(rig.app.hub.resources.results.add('snapshot', {},
            'Synthetic camera input retained for late query regression.',
            info=rig.app.hub.latest_info, picture_bytes=[frame.jpeg]))
        result = await rig.send(text='Read this retained camera resource and explain the observation limits.')
        assert result.get('ok') is True
        assert result['actions'] and result['actions'][0]['ok'] is None, 'BEHAVIOR: held real query must remain pending past inline_ms'
        await asyncio.wait_for(query_entered.wait(), HANG_S)
        assert query_contexts
        ctx, ticket, run = query_contexts[0]
        initial = rig.provider.sends[0]
        assert initial.phase == 'look' and initial.images, 'BEHAVIOR: initial real Agent call receives only the synthetic camera view'
        assert initial.pins and initial.moments
        socket, request_id, pin = initial.pins[-1]
        assert socket is rig.client and isinstance(pin, RequestSnapshot)
        assert pin.observation.wire() == expected and initial.moments[-1].request_snapshot is pin
        assert ticket is initial.ticket and ctx.admission is initial.admission
        assert ctx.client is rig.client and ctx.request_snapshot is pin
        assert ctx.request_id == request_id == initial.admission.request_id
        assert run.occurrence.request_id == request_id
        late_tasks = tuple(rig.app.brain._query_answers)
        assert late_tasks and not any(send.phase in ('query', 'final') for send in rig.provider.sends)
        peer = rig.message(seq=80, ref=OTHER)
        peer['items'][0]['rect'][0] = 700
        assert (await rig.ingest(peer, rig.other))['ok'] is True
        query_release.set()
        await asyncio.wait_for(asyncio.gather(*late_tasks), HANG_S)
        deferred = [send for send in rig.provider.sends if send.phase in ('query', 'final')]
        assert [send.phase for send in rig.provider.sends] == ['look', 'query', 'final']
        assert [(attempt.phase, attempt.status) for attempt in initial.admission.attempts] == [
            ('look', 'done'), ('query', 'done'), ('final', 'done')]
        assert len(deferred) == 2, 'BEHAVIOR: incompatible query answer requires the original protected final'
        assert all(send.ticket is ticket and send.admission is initial.admission and not send.closed_at_send
                   for send in deferred)
        assert all(send.reply_data['answer'] == CLAIM and send.reply_data['observation_scope'] == 'workspace-pixels'
                   for send in deferred)
        assert run.ok is True, f'BEHAVIOR: actual held resource read must complete, not a dependency/setup failure: {run.error!r}'
        for send in deferred:
            if late_images == 'none':
                assert send.images == () and run.images == [], 'BEHAVIOR: late notes-only call cannot borrow the earlier camera attachment'
            else:
                assert len(send.images) == len(run.images) == 1
                picture = run.images[0]
                assert send.images[0] == picture.jpeg  # actual PictureInput bytes supplied by the real resource handler
                assert picture.facts['id'] == source_record['id']
                assert picture.facts['source']['frame'] == frame.id
                assert picture.facts['source']['seq'] == frame.seq
                assert picture.facts['source']['boot'] == frame.boot
                assert picture.facts.get('channel') != 'workspace'
        assert not rig.surface_checks and ctx.request_snapshot is pin
        assert pin.observation.wire() == expected
        assert initial.admission.used == len(rig.provider.sends) == 3
        assert delivered, 'BEHAVIOR: real Announcer/Dialogue must publish the deferred activity on its original action'
        answer = delivered[-1].get('answer', '')
        assert delivered[-1]['turnId'] == result['turnId']
        assert CLAIM not in answer, 'MISSING_FEATURE: actual late-query answer cannot retain whole-workspace visual claims without that late call’s workspace pixels'
        assert answer == ''
        assert delivered[-1]['requestId'] == run.occurrence.request_id == initial.admission.request_id
        assert delivered[-1]['actionId'] == run.occurrence.action_id
        assert delivered[-1]['thread'] == ctx.asked.thread
        assert delivered[-1]['ok'] is True and delivered[-1]['summary'] == run.summary
        wrapper = '<answer-facts source="app" untrusted="true">'
        for send in deferred:
            assert send.prompt.count(wrapper) == 1
            body = send.prompt.split(wrapper, 1)[1].split('</answer-facts>', 1)[0]
            facts = json.loads(body)  # independently decode the actual external prompt, preserving escaped multiline text
            assert facts['omittedFacts'] == facts['omittedOperations'] == 0
            assert facts['operations'] == [{k: v for k, v in run.wire().items() if k not in ACTIVITY_KEYS}]
            assert facts['inputs']['images'] == [picture.facts for picture in run.images]
            assert facts['inputs']['legacyCameraCropSupplied'] is False
        history = rig.app.dialogue.history()['lines']
        updates = [row for row in history if row.get('type') == 'chat-action'
                   and row.get('requestId') == initial.admission.request_id
                   and row.get('actionId') == run.occurrence.action_id]
        assert updates and all(row.get('turnId') == result['turnId'] for row in updates)
        assert any(row.get('ok') is True and row.get('summary') == run.summary for row in updates)
        assert all(not row.get('answer') for row in updates)

    try:
        asyncio.run(scenario())
    finally:
        query_release.set()
        rig.close()


@pytest.mark.parametrize('scope,refs,answer', [
    ('structural-workspace', [], PLAN),
    ('specimen-camera', ['camera'], 'The supplied camera crop is visible.'),
    ('workspace-pixels', [], CLAIM),
    pytest.param('structural-workspace', [], 'The supplied structural facts describe the registered windows.',
                 id='structural-workspace-no-calls'),
])
def test_actual_fc_provider_return_preserves_scope_refs_and_function_call_order(tmp_path, monkeypatch, scope, refs, answer):
    """Actual FC OpenAI return/normalizer; only its external SDK boundary is canned."""
    import json
    import openai

    rig = RequestRig(tmp_path, monkeypatch)
    rig.app.hub.settings.brain = 'fc'
    rig.app.hub.settings.vision = 'gpt-6.1-sol'
    sdk_sends, normalized = [], []
    calls = ([SimpleNamespace(type='function_call', name='fixture_request_facts',
                              arguments=json.dumps({'ordinal': ordinal})) for ordinal in (1, 2)]
             if scope == 'structural-workspace' and answer == PLAN else [])
    final_answer = ('Both ordered request-fact reads completed; they did not inspect workspace pixels.'
                    if calls else LIMIT)
    final_reply = {'answer': final_answer, 'actions': [],
                   'observation_scope': 'structural-workspace' if calls else 'none', 'input_refs': []}
    final_returns = []
    if calls or scope == 'workspace-pixels':
        actual_provider = rig.provider.ask

        async def eligible_final(images, prompt, **kwargs):
            reply = await actual_provider(images, prompt, **kwargs)
            reply.data = copy.deepcopy(final_reply)  # configure only the external outcome/correction reply
            final_returns.append(copy.deepcopy(reply.data))
            return reply

        monkeypatch.setattr(rig.provider, 'ask', eligible_final)

    async def responses_create(**kwargs):
        ticket = current()
        sdk_sends.append((ticket, copy.deepcopy(kwargs), tuple(rig.pins)))
        return SimpleNamespace(model='explicitly-canned-fc-sdk-boundary', output=calls,
            output_text=json.dumps({'answer': answer, 'observation_scope': scope, 'input_refs': refs}))

    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-test-only-value')
    monkeypatch.setattr(openai, 'AsyncOpenAI',
        lambda **_kwargs: SimpleNamespace(responses=SimpleNamespace(create=responses_create)))
    actual_fc_ask = rig.app.agent.fc.ask

    async def trace_actual_fc(*args, **kwargs):
        returned = await actual_fc_ask(*args, **kwargs)
        normalized.append(returned)
        return returned

    monkeypatch.setattr(rig.app.agent.fc, 'ask', trace_actual_fc)

    async def scenario():
        await rig.camera()
        await rig.ingest()
        expected = rig.observed().wire()
        result = await rig.send(text='Explain only the observations actually supplied to this call.')
        assert len(sdk_sends) == len(normalized) == 1, 'BEHAVIOR: actual FC dispatch must send once without a compensating judge'
        ticket, sent, pins = sdk_sends[0]
        assert ticket is not None and pins and pins[-1][1] == ticket.admission.request_id
        pin = pins[-1][2]
        assert pin.observation.wire() == expected
        assert sent['input'][0]['content'][0]['type'] == 'input_image'
        data, _used = normalized[0]
        assert data.get('observation_scope') == scope, 'MISSING_FEATURE: actual FC response normalization must preserve the model-declared observation scope'
        assert data.get('input_refs') == refs, 'MISSING_FEATURE: actual FC response normalization must preserve actual-input references'
        assert data['answer'] == answer
        assert data['actions'] == [{'tool': c.name, 'args': json.loads(c.arguments)} for c in calls]
        if calls or scope == 'workspace-pixels':
            assert len(rig.provider.sends) == len(final_returns) == 1
            final = rig.provider.sends[0]
            assert final.phase == 'final'
            if final.images:
                assert 'original request camera frame' in final.prompt and len(final.images) == 1
            assert final.ticket is ticket and final.admission is ticket.admission
            assert final.pins[-1][2] is pin and final.moments[-1].request_snapshot is pin
            assert ticket.admission.used == len(sdk_sends) + len(rig.provider.sends) == 2
            assert [attempt.phase for attempt in ticket.admission.attempts] == ['look', 'final']
            assert all(attempt.status == 'done' for attempt in ticket.admission.attempts)
            assert final_returns == [final_reply] and result['answer'] == final_answer
            assert result['answerStatus'] == 'model' and CLAIM not in result['answer']
            if calls:
                assert len(rig.contexts) == len(result['actions']) == 2
                assert all(ctx.admission is ticket.admission and ctx.request_snapshot is pin
                           and owner is ticket for ctx, owner in rig.contexts)
                assert [(row['tool'], row['ok'], row['summary']) for row in result['actions']] == [
                    ('fixture_request_facts', True, 'Synthetic request facts read; no visual inspection was performed.')] * 2
                assert all(row['requestId'] == ticket.admission.request_id for row in result['actions'])
                assert len({row['actionId'] for row in result['actions']}) == 2
                assert all(json.dumps(row) in final.prompt for row in result['actions'])
                first, second = result['actions']
                assert final.prompt.index(first['actionId']) < final.prompt.index(second['actionId'])
                assert final.prompt.index("'ordinal': 1") < final.prompt.index("'ordinal': 2")
            else:
                assert not rig.contexts and not result['actions']
                assert "This call received no complete verified workspace pixels supporting the reply's declaration." in final.prompt
            history = rig.app.dialogue.history()['lines']
            assert [row['text'] for row in history if row.get('role') == 'assistant'
                    and row.get('turnId') == result['turnId']] == [final_answer]
        else:
            assert not rig.provider.sends, 'BEHAVIOR: supported no-call FC replies must not enter a JSON answer or judge'
            assert ticket.admission.used == len(sdk_sends) == 1
            assert not rig.contexts and not result['actions']
            assert result['answer'] == answer, 'BEHAVIOR: a supported FC declaration must retain its useful answer'
        assert pin.observation.wire() == expected
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('old_pin_kind', ['unscoped', 'peer-pin'])
def test_actual_carried_look_without_ctx_cannot_consume_another_pin_or_reset_spent_admission(tmp_path, monkeypatch, old_pin_kind):
    from orchestrator.ticket import issue, using

    rig = RequestRig(tmp_path, monkeypatch)
    old_answer = 'OLD UNQUALIFIED OR PEER LOOK WAS ACCEPTED FOR THIS REQUEST.'
    sends = []
    request = look = None

    async def carried_provider(images, prompt, **kwargs):
        ticket = current()
        phase = ticket.admission.attempts[-1].phase
        sends.append((ticket, phase, tuple(images), prompt))
        if phase == 'fast':
            data = {'answer': '', 'intent': 'question', 'needs_look': True, 'actions': [],
                    'observation_scope': 'none', 'input_refs': []}
        else:
            first_look = sum(sent[1] == 'look' for sent in sends) == 1
            data = {'answer': old_answer if first_look else PLAN, 'targets': [], 'actions': [],
                    'observation_scope': 'structural-workspace', 'input_refs': []}
        return SimpleNamespace(data=data, model='explicitly-canned-carried-look-provider', latency_ms=0.0, error=None)

    monkeypatch.setattr(rig.provider, 'ask', carried_provider)

    async def scenario():
        nonlocal request, look
        await rig.camera()
        raw = rig.message()
        await rig.ingest(raw)
        peer = rig.message(seq=80, ref=OTHER)
        peer['items'][0]['rect'][0] = 700
        await rig.ingest(peer, rig.other)
        asked = rig.app.conversation.here()
        request = issue(rig.app.hub, 'turn', asked=asked, channel='chat')
        budget = request.admission
        question = 'What structural workspace facts were actually supplied?'
        actual_moment = rig.app.brain.moment
        with using(request):
            decision = await rig.app.brain.quick(question, model=rig.app.hub.settings.vision)
            assert decision is not None and decision.needs_look
            peer_pin = rig.screen.pin(rig.other, request_id=budget.request_id, timing='client-send')
            if old_pin_kind == 'peer-pin':
                def peer_moment(*args, **kwargs):
                    kwargs['request_snapshot'] = peer_pin
                    return actual_moment(*args, **kwargs)  # real capture, explicit wrong-origin input
                monkeypatch.setattr(rig.app.brain, 'moment', peer_moment)
            look = rig.app.brain.start_look(question)
            monkeypatch.setattr(rig.app.brain, 'moment', actual_moment)
            await asyncio.wait_for(asyncio.shield(look.task), HANG_S)
        assert look.moment.request_snapshot is (peer_pin if old_pin_kind == 'peer-pin' else None)
        assert budget.used == len(sends) == 2
        pinned = rig.screen.pin(rig.client, request_id=budget.request_id, inline=raw, timing='client-send')
        expected_pin = pinned.wire()
        handled = {'ticket': request, 'look': look, 'decision': decision, 'runs': [], 'ctx': None, 'later': []}
        try:
            result = await rig.app.brain.turn(question, 'chat', asked=asked, handled=handled,
                client=rig.client, request_snapshot=pinned, request_ticket=request)
        except ObservationError as exc:
            assert exc.code in ('CLIENT_MISMATCH', 'INVALID_REPORT')
        else:
            assert old_answer not in result['answer'], 'MISSING_FEATURE: an existing carried Look dispatched without this pin cannot become this request’s structural evidence'
        assert not look.used, 'MISSING_FEATURE: refreshing Moment after dispatch must not authorize consumption of the old mismatched Look task'
        assert request.admission is budget and budget.used == len(sends) and 2 <= budget.used <= 3
        assert pinned.wire() == expected_pin
    try:
        asyncio.run(scenario())
    finally:
        if request is not None:
            request.seal()
        rig.close()


@pytest.mark.parametrize('staged', [False, True, None], ids=['bounded-false', 'true', 'unknown'])
def test_actual_scoped_provider_prompt_retains_snapshot_instance_view_and_explicit_omissions(tmp_path, monkeypatch, staged):
    import json

    rig = RequestRig(tmp_path, monkeypatch, mode='plan')
    async def scenario():
        await rig.camera()
        raw = rig.message()
        first = raw['items'][0]
        first.update(staged=staged, revision=43, lifetimeGeneration='fb11d804-e091-40c3-98ec-2c1e58f317a1',
                     clipRect=[150.25, 70.5, 200, 180], view={'zoom': 2.25, 'pan': [17, -9], 'fit': 'contain'})
        first['propertyVersions'].update(rect=31, opacity=37)
        first['viewTransform'].update(sourceToContent=[0.75, 0, 0, 0.75, 0, 0],
            sourceToWorkspace=[0.75, 0, 0, 0.75, 120.25, 60.5], transformRevision=23)
        if staged is False:
            for n in range(2, 10):
                drawn = copy.deepcopy(first)
                drawn['id'], drawn['rootId'] = f'window-extra-{n}', f'root:window-extra-{n}'
                drawn['lifetimeGeneration'] = f'fb11d804-e091-40c3-98ec-2c1e58f317a{n}'
                for control in drawn['controls']:
                    control['id'] = control['id'].replace('window-a', drawn['id'])
                    control['targetId'] = drawn['id']
                raw['items'].append(drawn)
        assert (await rig.ingest(raw))['ok'] is True
        await rig.send(screen=copy.deepcopy(raw), text='Describe the observed window incarnation and actual view, with missing detail explicit.')
        sent = rig.provider.sends[0]
        pin = sent.pins[-1][2]
        prefix = 'Originating workspace structural facts (no attached pixels): '
        assert prefix in sent.prompt, 'BEHAVIOR: actual scoped provider prompt must carry the original structural projection'
        projected, _end = json.JSONDecoder().raw_decode(sent.prompt.split(prefix, 1)[1])
        canonical = pin.observation.wire()
        assert projected.get('snapshotId') == pin.snapshot_id, 'MISSING_FEATURE: model-facing scoped facts must identify the actual immutable snapshot'
        item = next(row for row in projected['items'] if row['id'] == 'window-a')
        expected = canonical['items'][0]
        for key in ('id', 'kind', 'rect', 'visible', 'view'):
            assert key in item and item[key] == expected[key], f'MISSING_FEATURE: model-facing scoped instance must preserve observed {key}, including unknown values'
        assert projected['complete'] == canonical['complete']
        assert projected['omitted'] == canonical['omitted']
        assert projected['projectionOmittedItems'] == len(canonical['items']) - len(projected['items'])
        assert len(projected['items']) <= 24
        assert 'propertyOwners' not in item and 'propertyVersions' not in item
        assert 'content' in item and 'controls' in item
        assert pin.observation.wire() == canonical
        assert sent.admission.used == len(rig.provider.sends) <= 3
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_reference_windows_keep_distinct_source_identity_in_actual_provider_context(tmp_path, monkeypatch):
    import json

    rig = RequestRig(tmp_path, monkeypatch, mode='plan')
    async def scenario():
        await rig.camera()
        for key, site in [('source-a', 'original.example'), ('source-b', 'new.example')]:
            rig.sources[key]['media'] = 'reference'
            rig.sources[key]['provenance']['web'] = {
                'page': f'https://{site}/stoma', 'site': site, 'title': 'Stoma micrograph'}
        raw = rig.message()
        assert (await rig.ingest(raw))['ok']
        await rig.send(screen=copy.deepcopy(raw), text='Close the new photo and reopen the original.')
        prompt = rig.provider.sends[0].prompt
        prefix = 'Originating workspace structural facts (no attached pixels): '
        projected, _ = json.JSONDecoder().raw_decode(prompt.split(prefix, 1)[1])
        contents = [row['content'] for row in projected['items'] if row['content']['media'] == 'reference']
        assert {c['web']['site'] for c in contents} == {'original.example', 'new.example'}
        assert {c['resource']['id'] for c in contents} == {
            s['identity']['resource']['id'] for s in rig.sources.values()}
        assert 'do not create a duplicate unless asked' in prompt
    try:
        asyncio.run(scenario())
    finally:
        rig.close()
