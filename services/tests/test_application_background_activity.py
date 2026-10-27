"""TEST-only real background producers, with synthetic external inputs.

App/Brain/Registry/Ticket/Asked/Watcher/Held/Announcer/Dialogue and journal are
real. Camera JPEGs, answering provider, worker transport, voice SDK, measurement
check and time are the only substituted boundaries. No Watch, HeldClip, Session,
ToolRun, Future, completion receipt or installed producer callback is fabricated.
This is local lifecycle/replay evidence, not model quality, a browser or a phone.
Root alone executes this frozen checkpoint; the shared notice feature may be RED.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.capture import captures
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue
from orchestrator.held import held_of
from orchestrator.hub import Client
from orchestrator.voice.base import VoiceConfig
from orchestrator.voice.openai_live import OpenAILiveSession
from test_model_owned_announcements import ExternalLiveConnection, ExternalLiveContext
from test_model_owned_conversation import ExternalAnswers, LocalApp, action, eventually, response
from tests.waiting import HANG_S

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')


MODEL = 'I will use the requested background capability and report the available evidence.'
SEEN = 'Synthetic model observation: the dark object has separated into two.'
MEASURED = '2 of 10 followed objects are moving (share 0.2)'
DELEGATION = 'synthetic-background-input-delegation'


class ExternalWorker:
    """ProcessChannel transport only; the actual manager owns every session/event."""
    def __init__(self, _target, _name, event, request, exited):
        self.event, self.request, self.exited = event, request, exited
        self.alive = False
        self.calls = []

    async def start(self):
        self.alive = True

    async def call(self, op, **fields):
        self.calls.append((op, fields))
        if op == 'spawn':
            self.event({'rid': fields['rid'], 'event': 'status', 'status': 'running'})
            self.event({'rid': fields['rid'], 'event': 'request',
                        'what': '2 seconds of this field', 'why': 'observe movement', 'seconds': 2})
        return {'ok': True}

    def finish(self, rid):
        self.event({'rid': rid, 'event': 'done', 'status': 'cancelled', 'result': None})

    def stop(self):
        self.alive = False


class ExternalChecks:
    """One external measurement barrier plus advancing clock/sleep, never _run."""
    def __init__(self, outcome):
        self.outcome = outcome
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.now = time.monotonic()
        self.calls = 0

    async def measure(self):
        self.calls += 1
        self.entered.set()
        await self.release.wait()
        if self.outcome == 'failed':
            raise OSError('synthetic external measurement disconnected')
        return (0.2, MEASURED) if self.outcome == 'fired' else (0.8, '8 of 10 followed objects are moving')

    async def sleep(self, seconds):
        self.now += seconds
        await asyncio.sleep(0)


class BackgroundApp:
    def __init__(self, tmp_path, monkeypatch):
        self.rig = LocalApp(tmp_path, monkeypatch, initial=response(MODEL))
        self.app, self.monkeypatch = self.rig.app, monkeypatch
        self.voices = []
        self.completed = []
        self.model_answers = set()
        # Passive existing observer; no callback used for production is replaced.
        self.app.brain.registry.finished.append(lambda run, ctx: self.completed.append((run, ctx)))
        monkeypatch.setattr('orchestrator.research.manager.ProcessChannel', ExternalWorker)
        self.look_entered, self.look_release = asyncio.Event(), asyncio.Event()
        self.look_calls = []
        self.independent_sends = []
        boundary = self

        class Provider:
            async def ask(self, images, prompt, **kwargs):
                if kwargs.get('max_output_tokens') == 200 and images:
                    boundary.look_calls.append((tuple(images), prompt))
                    boundary.look_entered.set()
                    await boundary.look_release.wait()
                    return SimpleNamespace(data={'yes': True, 'seen': SEEN}, latency_ms=0,
                                           model='scripted-external-watch-provider', error=None)
                if prompt.startswith(("From today's conversations with a microscope assistant,",
                                      'You keep the running summary of one conversation')):
                    # Actual day Memory refresh is a separate producer; its
                    # external reply must not consume another turn's script.
                    boundary.independent_sends.append((tuple(images), prompt, dict(kwargs)))
                    return SimpleNamespace(data={'facts': [], 'summary': 'Synthetic day-memory summary.'},
                                           latency_ms=0, model='scripted-external-memory-provider', error=None)
                return await boundary.rig.provider.ask(images, prompt, **kwargs)

        monkeypatch.setattr(self.app.agent, 'vision', lambda _model: Provider())

    async def camera(self, *, wall_at=None):
        # The bridge ingress clock is an external time boundary; the real Hub
        # still produces FrameInfo and invokes the actual Held.frame callback.
        if wall_at is not None:
            self.monkeypatch.setattr('orchestrator.frames.time',
                SimpleNamespace(monotonic=time.monotonic, time=lambda: wall_at))
        self.rig.camera_seq += 1
        await self.app.hub.source._accept(self.rig.camera_jpeg, seq=self.rig.camera_seq,
                                         boot='synthetic-background-camera')
        frame = self.app.hub.source.latest()
        assert frame is not None and self.app.hub.latest_info.id == frame.id
        return frame

    async def ask(self, words, tool, *, channel='chat', owner=Ellipsis, **args):
        self.rig.provider = ExternalAnswers(response(MODEL, actions=[action(tool, **args)]))
        await self.camera()
        origin = self.app.day.here()
        options = {} if owner is Ellipsis else {'voice_owner': owner}
        result = await self.app.brain.turn(words, channel, asked=origin,
                                         delegation_id=DELEGATION if channel == 'voice' else None, **options)
        self.model_answers.update(send.reply_data['answer'] for send in self.rig.provider.sends
                                  if hasattr(send, 'reply_data') and send.reply_data.get('answer'))
        run, ctx = next((run, ctx) for run, ctx in reversed(self.completed) if run.tool == tool)
        assert run.ok is True, ('SETUP: actual public tool must succeed', tool, run.summary, result)
        assert ctx.asked == origin and run.occurrence is not None
        return run, ctx, result

    async def voice(self):
        connection = ExternalLiveConnection()
        voice = OpenAILiveSession(VoiceConfig(model='gpt-live-1'),
                                 connect=lambda: ExternalLiveContext(connection))
        await voice.start()
        self.voices.append((voice, connection))
        self.app.brain.voice = voice
        return voice, connection

    async def settle(self):
        # Actual held wiring schedules tell with call_soon; drain that scheduling
        # and await the existing narrator owner, without replacing either.
        for _ in range(5):
            await asyncio.sleep(0)
        task = self.app.narrator._task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), HANG_S)

    async def hold(self, *, channel='chat', owner=Ellipsis):
        await self.ask('Research how these objects move.', 'start_researcher',
                       brief='Research how these objects move.', effort='quick', channel=channel, owner=owner)
        await eventually(lambda: any(s.requests for s in self.app.research.sessions.values()),
                         'SETUP: actual manager must receive the external worker footage request')
        session = next(s for s in self.app.research.sessions.values() if s.requests)
        request = session.requests[-1]
        assert session.status == 'running' and request['status'] == 'asked'
        run, ctx, _ = await self.ask('Yes, hold 2 seconds for it.', 'researcher_note',
            researcher=session.rid, text='Yes, hold 2 seconds for it.', answers=request['id'],
            hold_seconds=2, asked_with='Yes, hold 2 seconds for it.', channel=channel, owner=owner)
        held = held_of(self.app.hub)
        clip = next(c for c in held.clips.values() if c.rid == session.rid)
        assert clip.said['asked'] == ctx.asked and clip.status == 'holding'
        assert request['status'] == 'answered' and request['answer'] == 'Yes, hold 2 seconds for it.'
        assert any(n.get('holding') == clip.id for n in session.notes)
        assert clip.seconds == 2 and clip.request == request['id']
        return held, clip, session, run, ctx

    def snapshot(self):
        return {(self.app.day.day, tid): list(thread.lines) for tid, thread in self.app.day.threads.items()}

    def added(self, before, session=None):
        store = self.app.day.elsewhere(session)  # the open session's, or another's (work filed where it was asked)
        return [row for tid, thread in store.threads.items()
                for row in thread.lines[len(before.get((store.sid, tid), [])):]]

    def assert_notice(self, rows, kind, entity, ctx, event, *, original_day, shown=True):
        notices = [row for row in rows if row.get('type') == 'app-notice'
                   and row.get('producer') == {'kind': kind, 'id': entity}]
        assert len(notices) == 1, ('MISSING_FEATURE: actual background result has one separate durable activity', rows)
        row = notices[0]
        assert row['event'] == event and row['summary']
        assert not {'role', 'answer', 'tool', 'ok', 'turnId', 'requestId', 'actionId', 'lineId', 'noticeId'} & row.keys()
        assert row['origin']['day'] == original_day
        assert row['origin']['asked'] == {'thread': ctx.asked.thread, 'generation': ctx.asked.generation}
        assert not any(r.get('type') == 'chat' and r.get('role') == 'assistant'
                       and r.get('text') not in self.model_answers for r in rows), rows
        assert row['at'] > 0
        json.dumps(row, allow_nan=False)
        delivered = []
        while not self.rig.client.control.empty():
            message = self.rig.client.control.get_nowait()
            delivered.append(json.loads(message) if isinstance(message, str) else message)
        if shown:
            assert row in delivered, 'BEHAVIOR: genuine activity also reaches the actual client transport'
        else:
            assert row not in delivered, 'BEHAVIOR: activity filed in another session is never shown in the one open'
        replay = DayStore(root=self.app.day.root, session=row['day'])
        replay.switch(row['thread'])
        assert row in Dialogue(replay, Client(None).offer).history()['lines']
        return row

    async def close(self):
        tasks = [w.task for w in self.app.services.watcher.active.values() if w.task is not None]
        self.app.services.watcher.stop()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        held_of(self.app.hub).clear()
        self.app.research.stop()
        self.app.brain.voice = None
        for voice, _connection in self.voices:
            await voice.close()
        await self.settle()


@pytest.fixture
def background(tmp_path, monkeypatch):
    rig = BackgroundApp(tmp_path, monkeypatch)
    try:
        yield rig
    finally:
        rig.rig.close()


async def start_watch(rig, outcome, *, channel='chat', owner=Ellipsis):
    checks = ExternalChecks(outcome)
    watcher = rig.app.services.watcher
    # External checks and clock are injectable dependencies, not watcher logic.
    watcher.measure_motion, watcher.clock, watcher.sleep = checks.measure, lambda: checks.now, checks.sleep
    run, ctx, result = await rig.ask('Tell me when fewer objects move.', 'watch',
        by='motion', what='fewer objects move', below=0.3, minutes=0.2, channel=channel, owner=owner)
    await asyncio.wait_for(checks.entered.wait(), HANG_S)
    watch = next(iter(watcher.active.values()))
    assert watch.origin['asked'] == ctx.asked and watch.checks == 1
    assert run.summary.startswith('watching for') and result['answer']
    return watcher, watch, checks, ctx


@pytest.mark.parametrize('outcome,event', [('fired', 'fired'), ('failed', 'failed'),
                                         ('timeout', 'timed-out'), ('cancelled', 'cancelled')])
def test_actual_watch_terminal_status_is_original_replayable_activity(background, outcome, event):
    rig = background
    async def scenario():
        try:
            watcher, watch, checks, ctx = await start_watch(rig, outcome)
            original_day = rig.app.day.day
            newer = rig.app.day.open_thread('Moved on before watch result')
            before = rig.snapshot()
            if outcome == 'cancelled':
                await rig.ask('Stop watching for fewer objects move.', 'stop_watching', which=watch.id)
                # The command has its own truthful tool receipt/model answer.
            else:
                checks.release.set()
            await asyncio.gather(watch.task, return_exceptions=True)
            await rig.settle()
            assert watch.id not in watcher.active
            assert checks.calls == (2 if outcome == 'fired' else 3 if outcome == 'timeout' else 1)
            if outcome == 'fired':
                entries = [r for r in rig.app.hub.resources.results.entries() if r.get('tool') == 'watch']
                assert len(entries) == 1
                entry = rig.app.hub.resources.results.get(entries[0]['id'], entries[0]['day'])
                assert entry['values'] == {'checks': 2, 'last': MEASURED, 'seconds': 5}
                assert entry['thread'] == ctx.asked.thread and MEASURED in entry['summary']
            row = rig.assert_notice(rig.added(before), 'watch', watch.id, ctx, event, original_day=original_day)
            assert row['thread'] == ctx.asked.thread != newer.id
            assert row['facts']['checks'] == watch.checks and row['facts']['last'] == watch.last
            assert row['facts']['seconds'] == round(checks.now - watch.started)
            assert row['facts']['fired'] is (outcome == 'fired')
            if outcome == 'failed':
                assert 'synthetic external measurement disconnected' in row['summary']
            assert not [r for r in newer.lines if r.get('type') == 'app-notice']
        finally:
            await rig.close()
    asyncio.run(scenario())


def test_actual_watch_look_preserves_model_seen_separately_from_app_status(background):
    rig = background
    async def scenario():
        try:
            _run, ctx, _ = await rig.ask('Tell me when it separates.', 'watch',
                by='look', what='it separates', question='Has it separated?', minutes=0.2)
            await asyncio.wait_for(rig.look_entered.wait(), HANG_S)
            watch = next(iter(rig.app.services.watcher.active.values()))
            before, original_day = rig.snapshot(), rig.app.day.day
            rig.look_release.set()
            await watch.task
            await rig.settle()
            assert len(rig.look_calls) == 1 and len(rig.look_calls[0][0]) == 1
            assert 'Has it separated?' in rig.look_calls[0][1]
            assert watch.last == SEEN and watch.checks == 1
            row = rig.assert_notice(rig.added(before), 'watch', watch.id, ctx, 'fired', original_day=original_day)
            assert row['facts']['last'] == SEEN
            assert row['facts']['observation'] == {'text': SEEN, 'source': 'model'}
            assert row['summary'] != SEEN, 'BEHAVIOR: measured application status is not relabelled model prose'
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('ending', ['no-frames', 'memory-full', 'run-ended', 'released-after-stop', 'new-session'])
def test_actual_early_hold_reports_count_span_and_release_reality(background, ending):
    rig = background
    async def scenario():
        blocker = None
        try:
            held, clip, session, _run, ctx = await rig.hold()
            original_day = rig.app.day.day
            newer = rig.app.day.open_thread('Moved on before held footage ends')
            before = rig.snapshot()
            if ending == 'memory-full':
                memory = captures(rig.app.hub).memory
                blocker = memory.reserve('unsaved', memory.allowances['unsaved'] - memory.reserved['unsaved'],
                                         'synthetic external memory pressure')
                await rig.camera(wall_at=clip.started_at + 0.25)
                assert memory.refused['unsaved'] >= 1
            elif ending in ('no-frames', 'released-after-stop'):
                held.tick(clip.started_at + clip.seconds + 2)
                if ending == 'released-after-stop':
                    # Same loop step: tell is queued, then the actual worker ends
                    # its run. Delivery must describe released, not kept, frames.
                    rig.app.research._worker.finish(session.rid)
            else:
                await rig.camera(wall_at=clip.started_at + 0.25)
                await rig.camera(wall_at=clip.started_at + 0.75)
                assert len(clip.frames) == 2 and clip.span() == 0.5
                if ending == 'new-session':
                    rig.app.new_session()  # actual App session listener clears holds
                else:
                    rig.app.research._worker.finish(session.rid)
            await rig.settle()
            released = ending in ('run-ended', 'released-after-stop', 'new-session')
            expected_count = 2 if ending in ('run-ended', 'new-session') else 0
            expected_span = 0.5 if expected_count else 0
            assert clip.status == ('released' if released else 'stopped')
            assert (clip.kept if released else (len(clip.frames), clip.span())) == (expected_count, expected_span)
            if released:
                assert not clip.frames and clip.reservation.released
            else:
                assert any(n.get('held') == clip.id for n in session.notes)
                inbox = (rig.app.research.root / session.day / session.rid / 'inbox.jsonl').read_text()
                assert clip.id in inbox and 'held' in inbox
            if ending == 'new-session':
                # Filed in the session its request was made in, never shown in the one open now (ADR 0017).
                assert rig.app.day.day != original_day
                filed = rig.app.day.elsewhere(original_day)
                rows = [r for tid, thread in filed.threads.items()
                        for r in thread.lines[len(before.get((original_day, tid), [])):]]
                notices = [r for r in rows if r.get('type') == 'app-notice'
                           and r.get('producer') == {'kind': 'held', 'id': clip.id}]
                assert len(notices) == 1 and notices[0]['event'] == 'released'
                row = notices[0]
                assert row['thread'] == ctx.asked.thread and row['day'] == original_day
                assert not [r for t in rig.app.day.threads.values() for r in t.lines
                            if r.get('producer', {}).get('kind') == 'held']
            else:
                rows = rig.added(before)
                # Other researcher outcome methods have a separate owner; only this
                # producer's assistant regression is considered here.
                held_rows = [r for r in rows if r.get('producer', {}).get('kind') == 'held'
                             or r.get('research') == session.rid and 'Holding footage' in r.get('text', '')]
                row = rig.assert_notice(held_rows, 'held', clip.id, ctx,
                                        'released' if released else 'stopped', original_day=original_day)
            facts = row['facts']
            assert facts['researchId'] == session.rid and facts['request'] == clip.request
            assert facts['seconds'] == 2 and facts['frames'] == expected_count and facts['span'] == expected_span
            assert facts['released'] is released and facts['status'] == clip.status and facts['why'] == clip.why
            if released:
                assert 'let go' in row['summary'] and 'kept for it' not in row['summary']
            if ending != 'new-session':
                assert row['thread'] == ctx.asked.thread != newer.id
                assert not [r for r in newer.lines if r.get('producer', {}).get('kind') == 'held']
        finally:
            if blocker is not None:
                blocker.release()
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('ending', ['finished', 'user-cancelled'])
def test_actual_held_utility_and_user_stop_do_not_manufacture_failure_notice(background, ending):
    rig = background
    async def scenario():
        try:
            held, clip, session, _run, _ctx = await rig.hold()
            before = rig.snapshot()
            for offset in (0.25, 0.75, 1.25, 1.75):
                await rig.camera(wall_at=clip.started_at + offset)
            assert len(clip.frames) == 4 and clip.span() == 1.5
            if ending == 'finished':
                held.tick(clip.started_at + 3.1)
                assert clip.status == 'held'
            else:
                run, _ctx, _result = await rig.ask('Stop holding footage for it.', 'researcher_note',
                    researcher=session.rid, text='Stop holding footage for it.', hold_seconds=0,
                    asked_with='Stop holding footage for it.')
                assert clip.by_user and clip.status == 'stopped'
                assert 'stopped holding footage' in run.summary
            await rig.settle()
            assert any(n.get('held') == clip.id for n in session.notes)
            assert held.picture(clip.id, 0) == rig.rig.camera_jpeg
            entry = rig.app.hub.resources.get(clip.id, clip.day)
            assert entry is not None and entry.kind == 'clip' and entry.state == clip.status
            assert len(entry.values['frames']) == 4 and len(entry.pictures) == 4
            assert all(f['seq'] is not None and f['boot'] == 'synthetic-background-camera'
                       for f in entry.values['frames'])
            assert clip.id in (rig.app.research.root / session.day / session.rid / 'inbox.jsonl').read_text()
            assert not [r for r in rig.added(before) if r.get('producer', {}).get('kind') == 'held'
                        and r.get('tone') == 'error']
            rig.app.research._worker.finish(session.rid)
            await rig.settle()
            assert clip.status == 'released' and not clip.frames and clip.kept == (4, 1.5)
            assert clip.reservation.released
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('producer', ['watch', 'held'])
@pytest.mark.parametrize('ownership', ['absent', 'closed', 'replaced', 'original'])
def test_background_facts_never_borrow_another_voice_session(background, producer, ownership):
    rig = background
    async def scenario():
        checks = None
        try:
            original, connection = await rig.voice()
            owner = None if ownership == 'absent' else original
            if producer == 'watch':
                _watcher, entity, checks, ctx = await start_watch(rig, 'fired', channel='voice', owner=owner)
            else:
                held, entity, _session, _run, ctx = await rig.hold(channel='voice', owner=owner)
            original_day, before = rig.app.day.day, rig.snapshot()
            await rig.settle()
            if ownership in ('closed', 'replaced'):
                await original.close()
            target = connection
            if ownership == 'replaced':
                _replacement, target = await rig.voice()
            counts = (len(target.messages('thinking')), len(target.messages('commentary')))
            if producer == 'watch':
                checks.release.set()
                await entity.task
                event = 'fired'
            else:
                held.tick(entity.started_at + entity.seconds + 2)
                event = 'stopped'
            await rig.settle()
            row = rig.assert_notice(rig.added(before), producer, entity.id, ctx, event, original_day=original_day)
            assert row['origin']['delegationId'] == DELEGATION
            new_context = target.messages('thinking')[counts[0]:]
            notifications = target.messages('commentary')[counts[1]:]
            if ownership == 'original':
                if producer == 'held':
                    assert not new_context and len(notifications) == 1
                    response = notifications[0].args
                    assert response['delegation_id'] == DELEGATION
                    assert response['content'].startswith('Requested notification: ')
                    assert entity.id in response['content'] and entity.why in response['content']
                    assert row['origin']['requestId'] in response['content']
                    assert row['origin']['actionId'] in response['content']
                else:
                    assert new_context and not notifications, 'ordinary watch facts remain silent context'
                    assert entity.id in ' '.join(str(r.args) for r in new_context)
            else:
                assert not new_context and not notifications, (
                    'BEHAVIOR: absent/closed/replaced original owner never borrows current B', new_context, notifications)
        finally:
            if checks is not None:
                checks.release.set()
            await rig.close()
    asyncio.run(scenario())
