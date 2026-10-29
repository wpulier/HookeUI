"""TEST precursor: actual late result facts never become assistant announcements.

Actual App/Brain/Registry/Ticket/Dialogue/Announcer/Narrator/OpenAILiveSession.
Only synthetic camera ingress, the external answering/voice SDK boundaries and
one exact retained-file read are scripted. No ToolRun or Line is constructed,
no core method/state/clock is patched, and no additional Capture hold is taken.
This exercises public Brain voice-channel delivery, not microphone/Hands/route
installation, real model interpretation, audio quality or browser pixels.

The realistic breaks caught here are receipt-to-commentary synthesis, queued
receipt-to-assistant fallback, loss/retargeting of actual late activity, and loss
or rewriting of an eligible model answer. Root alone executes this checkpoint.
"""
import asyncio
import base64
import copy
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from orchestrator.brain.registry import CURRENT_RUN
from orchestrator.capture import captures
from orchestrator.ticket import current
from orchestrator.voice.base import VoiceConfig
from orchestrator.voice.openai_live import OpenAILiveSession

from test_model_owned_conversation import LATE, PLAN, RETAINED, LocalApp, eventually


DELEGATION = 'synthetic-original-announcement-delegation'
QUESTION = 'Read the retained reference note and explain what it establishes.'
# PCM speech is external SDK input. The real session decodes it and owns its
# speech/idle timestamps; the fixture never assigns those producer fields.
SPEECH = base64.b64encode(b'\x84\x03\x7c\xfc' * 240).decode()


class RetainedReadBarrier:
    """Delegate the real read; observe original ownership without acquiring it."""
    def __init__(self, monkeypatch, target):
        self.entered, self.release = threading.Event(), threading.Event()
        self.origins = []
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == target:
                self.origins.append((current(), CURRENT_RUN.get()))
                self.entered.set()
                if not self.release.wait(8):
                    raise TimeoutError('synthetic retained-file barrier was not released')
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', read)


class LateProviderBarrier:
    """Hold the actual deferred query I/O; original dispatch/admission stays real."""
    def __init__(self, monkeypatch, provider):
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.origins = []
        self.entries = []
        original = provider.ask

        async def ask(images, prompt, **kwargs):
            ticket = current()
            admission = ticket.admission if ticket is not None else None
            attempts = admission.attempts if admission is not None else ()
            attempt = attempts[-1] if attempts else None
            task = asyncio.current_task()
            coro = task.get_coro() if task is not None else None
            code = getattr(coro, 'cr_code', None)
            entry = SimpleNamespace(ticket=ticket, admission=admission, attempt=attempt,
                images=tuple(images), prompt=prompt, arguments=copy.deepcopy(kwargs),
                task_code=code, completion='entered', error=None)
            self.entries.append(entry)
            if attempt is not None and attempt.phase == 'query':
                self.origins.append((ticket, ticket.admission, tuple(images), prompt))
                self.entered.set()
                await self.release.wait()
            try:
                result = await original(images, prompt, **kwargs)
            except BaseException as exc:
                entry.completion, entry.error = 'raised', type(exc).__name__
                raise
            entry.completion = 'returned'
            return result

        monkeypatch.setattr(provider, 'ask', ask)


def logical_context_messages(rows):
    """Inspect exact SDK fragments as logical inputs; raw ``sent`` stays available.

    Framing assertions are independent of the producer. This does not synthesize
    provider replies or certify interpretation of the context by a hosted model.
    """
    result, groups = [], {}
    for row in rows:
        if not row.complete:
            continue
        text = row.args.get('content', '')
        try:
            payload = json.loads(text)
        except (ValueError, TypeError):
            payload = None
        if not isinstance(payload, dict) or 'live_context' not in payload:
            result.append(row)
            continue
        assert len(text.encode('utf-8')) <= 500
        identity = payload['live_context']
        if 'part' in payload:
            group = groups.setdefault(identity, [])
            assert payload['part'] == len(group)
            group.append(row)
            if len(group) == payload['parts']:
                args = dict(group[0].args)
                args['content'] = ''.join(json.loads(piece.args['content'])['text'] for piece in group)
                args['event_id'] = identity
                result.append(SimpleNamespace(kind=row.kind, args=args, complete=True))
        else:
            assembled = next(item for item in result if item.args.get('event_id') == identity)
            assert assembled.args['delegation_id'] == row.args['delegation_id']
            assembled.kind = row.kind  # one actual response request after all silent fragments
    return result


class ExternalLiveConnection:
    """Documented SDK seam; assertions belong outside these callbacks."""
    def __init__(self):
        self.sent = []
        self.inbox = asyncio.Queue()
        self.busy_stop = asyncio.Event()
        self.busy_task = None
        connection = self

        class Part:
            def __init__(self, kind):
                self.kind = kind

            async def append(self, **kwargs):
                row = SimpleNamespace(kind=self.kind, args=copy.deepcopy(kwargs), complete=False)
                connection.sent.append(row)
                if self.kind == 'commentary':
                    # A scripted external model speech response, not an assertion
                    # that a receipt was interpreted correctly or a goal fulfilled.
                    await connection.inbox.put({'type': 'session.output_audio.delta', 'delta': SPEECH})
                row.complete = True

            async def mute(self, **kwargs):
                connection.sent.append(SimpleNamespace(kind='mute', args=kwargs, complete=True))

            async def unmute(self, **kwargs):
                connection.sent.append(SimpleNamespace(kind='unmute', args=kwargs, complete=True))

        class Session:
            input_audio = Part('input_audio')
            commentary = Part('commentary')
            thinking = Part('thinking')
            instructions = Part('instructions')

            async def start(self, session):
                connection.sent.append(SimpleNamespace(kind='start', args=copy.deepcopy(session), complete=True))
                await connection.inbox.put({'type': 'session.started', 'session': {'id': 'synthetic-live-sdk'}})

            async def close(self):
                connection.sent.append(SimpleNamespace(kind='close', args={}, complete=True))
                await connection.inbox.put({'type': 'session.closed', 'reason': 'close_requested'})
                await connection.inbox.put(None)

        self.session = Session()

    def __aiter__(self):
        return self

    async def __anext__(self):
        event = await self.inbox.get()
        if event is None:
            raise StopAsyncIteration
        return event

    async def busy(self):
        """Keep real session busy using incoming audio, not a patched idle getter."""
        while not self.busy_stop.is_set():
            await self.inbox.put({'type': 'session.output_audio.delta', 'delta': SPEECH})
            try:
                await asyncio.wait_for(self.busy_stop.wait(), 0.1)
            except asyncio.TimeoutError:
                pass

    def messages(self, kind):
        return [row for row in logical_context_messages(self.sent) if row.kind == kind]


class ExternalLiveContext:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, *_exc):
        return None


class AnnouncementApp:
    def __init__(self, tmp_path, monkeypatch, *, late='timeout'):
        self.late_mode = late
        # Deferred work now has a query opportunity before its reserved final.
        # Keep the useful late text exact; an empty query exercises the real
        # failed final provider rather than an unreachable phase-specific stub.
        self.rig = LocalApp(tmp_path, monkeypatch, late=late,
            correction=LATE if late == 'valid' else None, query_empty=late == 'timeout',
            final='timeout' if late == 'timeout' else 'valid')
        self.app = self.rig.app
        self.file = RetainedReadBarrier(monkeypatch, self.rig.file)
        self.late = LateProviderBarrier(monkeypatch, self.rig.provider)
        self.finished = []
        # Existing passive completion observer; it creates no receipt or hold.
        self.app.brain.registry.finished.append(lambda run, ctx: self.finished.append((run, ctx)))
        self.connection = ExternalLiveConnection()
        self.voice = OpenAILiveSession(VoiceConfig(model='gpt-live-1'),
            connect=lambda: ExternalLiveContext(self.connection))
        self.events = []
        self.event_task = None
        self.request = None
        self.initial = None
        self.origin = None
        self.frame_facts = None

    async def start(self, *, busy=False, voice_owner=Ellipsis):
        assert self.app.brain.report_late.__self__ is self.app.announcer
        assert self.app.brain.narrator is self.app.narrator
        await self.voice.start()
        self.app.brain.voice = self.voice  # the same public route-owned session field

        async def observe_events():
            async for event in self.voice.events():
                self.events.append(event)

        self.event_task = asyncio.create_task(observe_events())
        if busy:
            self.connection.busy_task = asyncio.create_task(self.connection.busy())
            await eventually(lambda: self.voice.last_speech_at() > 0,
                             'SETUP: actual SDK audio must make the real session busy')
            assert self.voice.idle_since() == 0
        self.rig.camera_seq += 1
        await self.app.hub.source._accept(self.rig.camera_jpeg, seq=self.rig.camera_seq,
                                         boot='synthetic-model-owned-camera')
        frame = self.app.hub.source.latest()
        info = self.app.hub.latest_info
        assert frame is not None and info is not None and info.id == frame.id
        capture = captures(self.app.hub).get(frame.id)
        assert capture is not None and capture.jpeg == frame.jpeg == self.rig.camera_jpeg
        assert capture.seq == frame.seq == self.rig.camera_seq
        self.frame_facts = (capture.id, capture.boot, capture.seq)
        # No Capture object is retained by this observer after the entry returns.
        self.origin = self.app.day.here()
        self.request = asyncio.create_task(self.app.brain.turn(QUESTION, 'voice',
            delegation_id=DELEGATION, asked=self.origin, voice_owner=voice_owner))
        await eventually(self.file.entered.is_set,
                         'SETUP: real resource query must enter the exact original file read')
        self.initial = await asyncio.wait_for(asyncio.shield(self.request), 4)
        assert self.initial['answer'] == PLAN
        assert self.initial['asked'] == self.origin
        assert len(self.initial['actions']) == 1
        row = self.initial['actions'][0]
        assert row['tool'] == 'resources' and row['ok'] is None
        ticket, run = self.file.origins[0]
        assert ticket is self.rig.provider.sends[0].ticket
        assert ticket.channel == 'voice' and ticket.asked == self.origin and not ticket.closed
        assert run is not None and run.tool == 'resources' and run.ok is None
        assert run.occurrence.request_id == ticket.admission.request_id == row['requestId']
        assert run.occurrence.action_id == row['actionId']
        held = ticket.held(self.frame_facts[0])
        assert held is not None and (held.id, held.boot, held.seq) == self.frame_facts
        assert held.jpeg == self.rig.camera_jpeg
        assert self.app.hub.execution.enabled is False

    @property
    def ticket(self):
        return self.file.origins[0][0]

    @property
    def run(self):
        return self.file.origins[0][1]

    async def finish_read(self):
        self.file.release.set()
        await eventually(lambda: bool(self.finished) and self.late.entered.is_set(),
                         'SETUP: real completed query must reach actual deferred provider I/O')
        assert len(self.finished) == 1
        run, ctx = self.finished[0]
        assert run is self.run and run.ok is True and run.task.done()
        assert RETAINED in run.summary and self.rig.record['id'] in run.summary
        assert ctx.channel == 'voice' and ctx.asked == self.origin
        assert ctx.turn_id == self.initial['turnId'] and ctx.delegation_id == DELEGATION
        assert ctx.admission is self.ticket.admission
        assert ctx.steps.runs[run.step[0]] is run
        assert ctx.steps.occurrences[run.step[0]] is run.occurrence
        assert not self.ticket.closed, 'BEHAVIOR: genuine deferred I/O still owns the original request'
        assert self.late.origins[0][0] is self.ticket
        assert self.late.origins[0][1] is self.ticket.admission
        assert self.late.origins[0][2] == ()
        assert RETAINED in self.late.origins[0][3]

    async def settle(self):
        self.late.release.set()
        await eventually(lambda: self.ticket.closed,
                         'BEHAVIOR: actual original deferred work must settle')
        await eventually(lambda: not self.app.narrator.waiting and
                         (self.app.narrator._task is None or self.app.narrator._task.done()),
                         'BEHAVIOR: actual narration must finish its owned delivery')
        admission = self.assert_provider_accounting()
        assert admission.used == (2 if self.late_mode == 'valid' else 3)
        assert len(self.late.origins) == 1
        assert not self.ticket.holds and not self.ticket.tasks
        assert self.run.ok is True and self.run.task.done()
        return admission

    def assert_provider_accounting(self):
        """Keep every external entry, separated by its real dispatch ownership."""
        admission = self.ticket.admission
        entries = self.late.entries
        original = [entry for entry in entries if entry.admission is admission]
        others = [entry for entry in entries if entry.admission is not admission]
        sends = self.rig.provider.sends
        original_sends = [send for send in sends if send.admission is admission]
        other_sends = [send for send in sends if send.admission is not admission]
        attempts = {attempt.attempt_id: attempt for attempt in admission.attempts}
        # Retain full prompts/phase/ownership and the independent producer in
        # captured pytest evidence. No entry disappears behind the MAX3 scope.
        manifest = []
        for entry in entries:
            actual = attempts.get(entry.attempt.attempt_id) if entry.attempt is not None else None
            manifest.append({'owner': 'original request' if entry.admission is admission else 'independent producer',
                'ticketId': entry.ticket.id if entry.ticket is not None else None,
                'requestId': entry.admission.request_id if entry.admission is not None else None,
                'attemptId': entry.attempt.attempt_id if entry.attempt is not None else None,
                'phase': entry.attempt.phase if entry.attempt is not None else None,
                'attemptStatus': actual.status if actual is not None else None,
                'task': entry.task_code.co_qualname if entry.task_code is not None else None,
                'producerFile': entry.task_code.co_filename if entry.task_code is not None else None,
                'images': len(entry.images), 'arguments': entry.arguments,
                'completion': entry.completion, 'error': entry.error, 'prompt': entry.prompt})
        self.provider_accounting = manifest
        print('PROVIDER_ACCOUNTING ' + json.dumps(manifest))
        assert original and admission.used == len(original) == len(original_sends) <= 3, (
            'BEHAVIOR: original entered attempts remain spent within original MAX3', manifest)
        assert all(entry.ticket is self.ticket and entry.attempt is not None for entry in original)
        assert {entry.attempt.attempt_id for entry in original} == set(attempts)
        assert len({entry.attempt.attempt_id for entry in original}) == len(original)
        expected_phases = ['fast', 'query'] if self.late_mode == 'valid' else ['fast', 'query', 'final']
        expected_statuses = ['done', 'done'] if self.late_mode == 'valid' else ['done', 'done', 'failed']
        assert [entry.attempt.phase for entry in original] == expected_phases
        assert [send.phase for send in original_sends] == expected_phases
        assert [attempt.phase for attempt in admission.attempts] == expected_phases
        assert [attempt.status for attempt in admission.attempts] == expected_statuses
        if self.late_mode == 'valid':
            assert original_sends[-1].reply_data['answer'] == LATE
        else:
            assert original_sends[1].reply_data['answer'] == ''
            assert isinstance(original_sends[-1].provider_error, asyncio.TimeoutError)
            assert original[-1].completion == 'raised' and original[-1].error == 'TimeoutError'
        assert all(not entry.images for entry in entries), (
            'BEHAVIOR: neither retained text nor independent day-memory facts supply camera/workspace pixels', manifest)
        assert len(sends) == len(entries) and len(other_sends) == len(others)
        # DayStore.open_thread triggers actual Memory.refresh_facts. App's
        # ask_quietly is direct background provider I/O with no turn Ticket or
        # RequestAdmission, not an uncharged fourth answer on the original turn.
        for entry in others:
            assert entry.ticket is None and entry.admission is None and entry.attempt is None, manifest
            assert entry.task_code is not None and entry.task_code.co_qualname == 'Memory.refresh_facts', manifest
            assert entry.task_code.co_filename.endswith('/orchestrator/day/memory.py'), manifest
            assert entry.prompt.startswith("From today's conversations with a microscope assistant, list what the user learned or"), manifest
            assert f'Thread {self.origin.thread} ' in entry.prompt and QUESTION in entry.prompt, manifest
            assert self.app.day.active_id != self.origin.thread and f'Thread {self.app.day.active_id} ' in entry.prompt, manifest
            assert entry.arguments.get('max_output_tokens') == 800 and entry.completion == 'returned', manifest
        return admission

    def original_rows(self):
        return self.app.day.threads[self.origin.thread].lines

    def assert_no_generated_assistant(self, *, model_answers=()):
        allowed = {PLAN, *model_answers}
        original = self.app.day.threads[self.origin.thread]
        texts = [row['text'] for row in original.lines
                 if row.get('type') == 'chat' and row.get('role') == 'assistant' and row.get('text')]
        texts += [turn.text for turn in original.turns if turn.role == 'assistant' and turn.text]
        assert all(text in allowed for text in texts), (
            'MISSING_FEATURE: original result facts cannot become application-authored assistant prose', texts)

    def assert_original_activity(self):
        rows = [row for row in self.original_rows() if row.get('type') == 'chat-action'
                and row.get('requestId') == self.run.occurrence.request_id
                and row.get('actionId') == self.run.occurrence.action_id]
        assert rows, 'MISSING_FEATURE: voice delivery must preserve original real operation activity'
        assert all(row['turnId'] == self.initial['turnId'] and row['tool'] == 'resources' for row in rows)
        assert any(row.get('ok') is True and RETAINED in row.get('summary', '') for row in rows)
        assert self.app.hub.resources.results.get(self.rig.record['id'], self.rig.record['day'])['summary'] == RETAINED
        return rows

    async def close(self):
        self.file.release.set()
        self.late.release.set()
        self.connection.busy_stop.set()
        if self.request is not None:
            await asyncio.gather(self.request, return_exceptions=True)
        # Close delivery through its real public owner; never cancel Registry work.
        await self.voice.close()
        if self.connection.busy_task is not None:
            await self.connection.busy_task
        if self.event_task is not None:
            await asyncio.wait_for(self.event_task, 2)
        if self.file.origins:
            await eventually(lambda: self.ticket.closed,
                             'CLEANUP: original request work must finish')
        task = self.app.narrator._task  # passive reference to existing owner, not a replacement
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), 10)


def test_raw_late_result_of_a_spoken_request_is_said_from_its_facts_on_its_delegation(tmp_path, monkeypatch):
    """The outcome of what was asked by voice is said, on the delegation that asked, in the voice's own words from the
    result's facts (OpenAI's live-delegation guide: commentary is for results GPT-Live should say; Completed and
    Failed are spoken states). It was silent context before, and on 2026-10-11 12:21 a failed photo left the voice to
    improvise "I found the photo, it's on your screen". No assistant chat line is made of it, and its Activity is the
    original's."""
    rig = AnnouncementApp(tmp_path, monkeypatch)
    async def scenario():
        try:
            await rig.start()
            await rig.finish_read()
            admission = await rig.settle()
            assert admission.attempts[-1].status == 'failed'
            rig.assert_no_generated_assistant()
            rig.assert_original_activity()
            spoken = rig.connection.messages('commentary')
            assert spoken, 'the requested outcome must reach the voice as something to say'
            content = ' '.join(str(row.args.get('content', '')) for row in spoken)
            assert 'Requested notification' in content and 'Use your own words' in content  # facts, not app prose
            assert RETAINED in content
            assert rig.run.occurrence.request_id in content and rig.run.occurrence.action_id in content
            assert all(row.args.get('delegation_id') == DELEGATION for row in spoken)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()


def test_busy_queued_raw_result_survives_voice_close_as_original_activity(tmp_path, monkeypatch):
    rig = AnnouncementApp(tmp_path, monkeypatch)
    async def scenario():
        try:
            await rig.start(busy=True)
            await rig.finish_read()
            await eventually(lambda: bool(rig.app.narrator.waiting),
                             'SETUP: actual late callback must leave its raw result queued while voice is busy')
            assert rig.voice.idle_since() == 0
            assert not rig.connection.messages('commentary')
            assert any(line.asked == rig.origin and line.delegation_id == DELEGATION
                       for line in rig.app.narrator.waiting)
            newer = rig.app.day.open_thread('A different conversation before queued delivery')
            await rig.voice.close()
            rig.connection.busy_stop.set()
            admission = await rig.settle()
            assert admission.attempts[-1].status == 'failed'
            rig.assert_no_generated_assistant()
            rig.assert_original_activity()
            assert not newer.lines and not newer.turns
            assert rig.app.dialogue.history()['thread'] == newer.id
            assert rig.app.dialogue.history()['lines'] == []
            assert not rig.connection.messages('commentary')
            rig.app.day.switch(rig.origin.thread)
            assert rig.app.dialogue.history()['lines'] == rig.original_rows()
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()


def test_voice_already_closed_keeps_actual_original_late_receipt(tmp_path, monkeypatch):
    rig = AnnouncementApp(tmp_path, monkeypatch)
    async def scenario():
        try:
            await rig.start()
            await rig.voice.close()
            await rig.finish_read()
            admission = await rig.settle()
            assert admission.attempts[-1].status == 'failed'
            rows = rig.assert_original_activity()
            assert all(row.get('answer') in (None, '') for row in rows)
            rig.assert_no_generated_assistant()
            assert not rig.connection.messages('commentary')
            assert rig.app.dialogue.history()['lines'] == rig.original_rows()
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()


def test_actual_eligible_late_model_answer_reaches_original_voice_delegation_intact(tmp_path, monkeypatch):
    rig = AnnouncementApp(tmp_path, monkeypatch, late='valid')
    async def scenario():
        try:
            await rig.start()
            await rig.finish_read()
            # Separate the actual raw notice from the model notice, so coalescing
            # cannot accidentally turn this useful model-answer control into a
            # second negative for the raw-result behavior covered above.
            await eventually(lambda: not rig.app.narrator.waiting and
                             (bool(rig.app.narrator.delivered) or bool(rig.connection.messages('thinking'))),
                             'SETUP: original raw notice must finish delivery before late model reply')
            assert not rig.ticket.closed
            admission = await rig.settle()
            assert admission.attempts[-1].status == 'done'
            # Part 2.5 (live 2026-10-28, one outline's outcome said three times): one outcome is said once, by its
            # identity. The raw notice above already said this step's outcome, so the model's own later answer about
            # the same step reaches the voice intact, once, as context, and is not said a second time.
            spoken = rig.connection.messages('commentary')
            assert len(spoken) == 1 and spoken[0].args.get('delegation_id') == DELEGATION
            assert 'Requested notification' in spoken[0].args.get('content')
            assert not [row for row in spoken if row.args.get('content') == LATE]
            told = [row for row in rig.connection.messages('thinking') if LATE in (row.args.get('content') or '')]
            assert len(told) == 1, 'BEHAVIOR: actual eligible late model text must reach the voice intact once'
            assert any(event.kind == 'audio' and event.audio for event in rig.events)
            assert rig.run.answer is None, 'BEHAVIOR: raw producer receipt was not rewritten into the model notice'
            rig.assert_no_generated_assistant(model_answers=(LATE,))
            assert RETAINED in rig.rig.provider.sends[-1].prompt
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()


def test_replacement_before_late_callback_never_borrows_new_voice_for_original_model_answer(tmp_path, monkeypatch):
    rig = AnnouncementApp(tmp_path, monkeypatch, late='valid')
    connection = ExternalLiveConnection()
    replacement = OpenAILiveSession(VoiceConfig(model='gpt-live-1'),
        connect=lambda: ExternalLiveContext(connection))

    async def scenario():
        try:
            await rig.start()
            ticket, occurrence = rig.ticket, rig.run.occurrence
            assert rig.run.ok is None and not rig.file.release.is_set()
            assert not rig.finished and not rig.app.narrator.waiting
            assert not rig.connection.messages('commentary')
            # Public SDK session lifecycle and the same route-owned installation
            # field as AnnouncementApp.start; no websocket-installation claim.
            await rig.voice.close()
            await replacement.start()
            rig.app.brain.voice = replacement
            assert rig.voice.closed and not replacement.closed
            assert rig.app.brain.voice is replacement and replacement is not rig.voice
            assert rig.run.ok is None and rig.ticket is ticket and not ticket.closed
            assert not rig.file.release.is_set() and not rig.finished
            await rig.finish_read()
            assert not rig.late.release.is_set() and not ticket.closed
            admission = await rig.settle()
            assert admission.attempts[-1].status == 'done'
            assert rig.run.occurrence is occurrence and rig.run.answer is None
            rows = rig.assert_original_activity()
            assert all(row['thread'] == rig.origin.thread and
                       row['requestId'] == occurrence.request_id and
                       row['actionId'] == occurrence.action_id for row in rows)
            assert any(row.get('answer') == LATE for row in rows), (
                'BEHAVIOR: actual eligible late model text remains on its original operation activity', rows)
            rig.assert_no_generated_assistant(model_answers=(LATE,))
            assert RETAINED in rig.rig.provider.sends[-1].prompt
            assert not rig.connection.messages('commentary')
            crossed = [row.args for row in connection.messages('commentary')
                       if row.args.get('delegation_id') == DELEGATION or row.args.get('content') == LATE]
            assert not crossed, (
                'MISSING_FEATURE: replacement session cannot borrow an original model answer or delegation', crossed)
        finally:
            await replacement.close()
            await rig.close()

    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()


def test_explicit_absent_voice_owner_keeps_late_model_activity_without_borrowing_current_session(tmp_path, monkeypatch):
    rig = AnnouncementApp(tmp_path, monkeypatch, late='valid')
    connection = ExternalLiveConnection()
    replacement = OpenAILiveSession(VoiceConfig(model='gpt-live-1'),
        connect=lambda: ExternalLiveContext(connection))

    async def scenario():
        try:
            # Public explicit None is absence even with an available session at entry.
            await rig.start(voice_owner=None)
            ticket, occurrence = rig.ticket, rig.run.occurrence
            assert not rig.voice.closed and not rig.file.release.is_set()
            await replacement.start()
            rig.app.brain.voice = replacement
            assert not replacement.closed and rig.run.ok is None and not ticket.closed
            await rig.finish_read()
            assert rig.finished[0][1].voice_owner is None
            admission = await rig.settle()
            assert admission.attempts[-1].status == 'done'
            assert rig.run.occurrence is occurrence and rig.run.answer is None
            rows = rig.assert_original_activity()
            assert all(row['thread'] == rig.origin.thread and
                       row['requestId'] == occurrence.request_id and
                       row['actionId'] == occurrence.action_id for row in rows)
            assert any(row.get('answer') == LATE for row in rows)
            rig.assert_no_generated_assistant(model_answers=(LATE,))
            assert not rig.connection.messages('commentary')
            crossed = [row.args for row in connection.messages('commentary')
                       if row.args.get('delegation_id') == DELEGATION or row.args.get('content') == LATE]
            assert not crossed, 'BEHAVIOR: explicit absent owner must not capture the replacement session'
        finally:
            await replacement.close()
            await rig.close()

    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()
