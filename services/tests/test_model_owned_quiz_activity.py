"""Additive TEST: actual quiz activity stays useful without app assistant prose.

Actual App/Brain/ResearchManager/worker attempt/Presenter/Quiz/Scene/DayStore/
Dialogue/Narrator/OpenAILiveSession. Reuse the authorized synthetic camera,
external worker transport, answer SDK, live SDK and renderer seams. The lesson
file/content and user outline geometry are declared retained unit inputs; actual
manager and add_outline allocate their resources. Only the external judge is
held/scripted; no Quiz/say callback, Session, ToolRun, Ticket, Asked, Line, ACK,
completion or journal method is replaced or fabricated. Another session opens through
the real App.new_session and its listeners; midnight advances the external calendar and
changes nothing (ADR 0017).

Judge text is explicitly canned model text, never proof of actual model quality.
Root alone executes. No browser paint, physical/live camera, hosted judge or live
audio proof. The notice API is pending: require replayable non-assistant quiz
facts without prescribing a notice type name or its presentation wording.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

from test_model_owned_announcements import logical_context_messages

import pytest

from orchestrator import tools
from orchestrator.app import App
from orchestrator.capture import captures
from orchestrator.commands import COMMANDS
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue
from orchestrator.presenter import Presenter
from orchestrator.vision import VisionResult

from test_model_owned_conversation import action, eventually
from test_model_owned_research_activity import ResearchApp

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')


FEEDBACK = ('Explicitly canned judge: these facts support a guard-cell interpretation.\n'
            '  Keep both spaces and this line break; the remaining text is deliberately long. '
            + 'Retained model explanation. ' * 20 + 'End of original feedback.')
ERROR = 'synthetic quiz judge transport unavailable'
INVALID = 'synthetic-undecided-verdict'
LESSON = {'kind': 'lesson', 'title': 'Synthetic retained guard-cell practice',
          'key_ideas': ['Two guard cells can surround a pore.'], 'steps': [
              {'id': 'q1', 'kind': 'find', 'prompt': 'Find a guard cell on your slide and click it.',
               'target': 'guard cell', 'answer': 'A bean-shaped cell beside a pore.',
               'hint': 'Look beside the slit.'},
              {'id': 'q2', 'kind': 'find', 'prompt': 'Find a second guard cell and click it.',
               'target': 'guard cell', 'answer': 'The other cell beside the same pore.'}]}


class ExternalJudge:
    """External vision provider boundary, with a real async I/O barrier only."""

    def __init__(self, rig, reply, error=None):
        self.rig, self.reply, self.error = rig, copy.deepcopy(reply), error
        self.other = rig.provider
        self.entered, self.release = asyncio.Event(), asyncio.Event()
        self.calls = []

    async def ask(self, images, prompt, **kwargs):
        if not prompt.startswith('A student is going through a quiz'):
            result = await self.other.ask(images, prompt, **kwargs)
            # The shared scripted answers omit metadata Agent's look reads.
            # Adapt only this external result shape; preserve its real call/data.
            return VisionResult(result.data, result.latency_ms,
                getattr(result, 'model', 'explicitly-canned-research-answers'),
                raw_text=getattr(result, 'raw_text', ''), error=getattr(result, 'error', ''),
                usage=getattr(result, 'usage', {}))
        row = SimpleNamespace(images=tuple(images), prompt=prompt,
            arguments=copy.deepcopy(kwargs), asked=self.rig.app.day.here(),
            day=self.rig.app.day.day, voice=self.rig.app.brain.voice,
            reply=copy.deepcopy(self.reply), error=self.error, completed=False)
        self.calls.append(row)
        self.entered.set()
        await self.release.wait()
        row.completed = True
        if self.error is not None:
            raise self.error
        return SimpleNamespace(data=copy.deepcopy(self.reply), error=None,
                               latency_ms=0.0, model='explicitly-canned-quiz-judge')


class QuizApp:
    def __init__(self, tmp_path, monkeypatch, reply, *, error=None):
        self.rig = ResearchApp(tmp_path, monkeypatch)
        self.app = self.rig.app
        self.judge = ExternalJudge(self.rig, reply, error)
        monkeypatch.setattr(self.app.agent, 'vision', lambda _option: self.judge)
        self.task = None

    async def request_lesson(self, words):
        # The shared research fixture certifies notes-only quick researchers.
        # A lesson has its own real admission and may actually enter a Look.
        self.lesson_origin, self.lesson_day = self.app.day.here(), self.app.day.day
        self.rig.provider.actions = [action('start_researcher', brief=words,
            effort='standard', deliverable='lesson', asked_with=words)]
        before = len(self.rig.provider.sends)
        result = await self.rig.local.ask(words)  # actual camera ingress/COMMANDS.ask/Brain
        self.lesson_sends = self.rig.provider.sends[before:]
        assert self.lesson_sends, 'SETUP: public lesson request must enter actual provider dispatch'
        admission = self.lesson_sends[0].admission
        assert admission is not None and all(s.admission is admission for s in self.lesson_sends)
        assert admission.used == len(self.lesson_sends) <= 3
        assert len({a.attempt_id for a in admission.attempts}) == len(self.lesson_sends)
        assert all(s.ticket is self.lesson_sends[0].ticket for s in self.lesson_sends)
        assert self.lesson_sends[0].phase == 'fast' and self.lesson_sends[0].images == ()
        assert self.lesson_sends[0].reply['actions'] == self.rig.provider.actions
        assert self.lesson_sends[0].reply['needs_look'] is False
        assert all(s.phase in ('fast', 'look', 'query', 'final') for s in self.lesson_sends)
        assert len([s for s in self.lesson_sends if s.phase == 'fast']) == 1
        assert len([s for s in self.lesson_sends if s.phase == 'look']) <= 1
        assert len([s for s in self.lesson_sends if s.phase == 'final']) <= 1
        for send in self.lesson_sends:
            if send.phase == 'look':
                assert len(send.images) == 1 and isinstance(send.images[0], bytes)
            else:
                assert send.images == (), 'SETUP: fast/final notes cannot gain camera pixels'
        starts = [(run, ctx) for run, ctx in self.rig.finished if run.tool == 'start_researcher']
        assert len(starts) == 1 and starts[0][0].ok is True, (
            'SETUP: supported lesson effort must produce an actual successful start receipt', starts)
        run, ctx = starts[0]
        # The accepted request frame stays authoritative even if the camera paused during the answer.
        frame = self.app.hub.source.get(ctx.frame_id)
        assert frame is not None, 'SETUP: the original request frame must remain in the fixture ring'
        self.lesson_capture = captures(self.app.hub).get(frame.id)
        assert self.lesson_capture is not None and self.lesson_capture.jpeg == frame.jpeg == self.rig.local.camera_jpeg
        assert self.lesson_capture.id == self.app.hub.latest_info.id
        assert self.lesson_capture.seq == frame.seq == self.rig.local.camera_seq
        assert self.lesson_capture.boot == frame.boot == 'synthetic-model-owned-camera'
        assert ctx.admission is admission and ctx.asked == self.lesson_origin
        assert ctx.frame_id == self.lesson_capture.id
        assert run.occurrence.request_id == admission.request_id
        assert len(result['actions']) == 1 and result['actions'][0]['ok'] is True
        assert run.occurrence.action_id == result['actions'][0]['actionId']
        assert result['answer'] in {s.reply['answer'] for s in self.lesson_sends}
        return ctx

    async def start(self):
        await self.rig.renderer.start()
        words = 'Please make a guard-cell practice lesson with find steps.'
        ctx = await self.request_lesson(words)
        await eventually(lambda: len(self.rig.worker.jobs) == 1,
                         'SETUP: actual research scheduler did not dispatch lesson worker')
        job = self.rig.worker.jobs[0]
        self.session = self.app.research.get(job.payload['rid'])
        await eventually(lambda: self.session.rid in self.app.research._watchdogs,
                         'SETUP: actual worker spawn must return before retained output')
        self.origin, self.original_day = self.app.day.here(), self.app.day.day
        assert self.session.day == job.day == self.original_day
        assert self.origin == ctx.asked == self.lesson_origin and job.day == self.lesson_day
        assert self.session.origin['thread'] == self.origin.thread
        assert self.session.origin['turn_id'] == ctx.turn_id and job.origin == self.session.origin
        assert self.session.admission['effort'] == 'standard' and self.session.admission['deliverable'] == 'lesson'
        assert self.session.admission['askedWith'] == words
        assert job.payload['snapshot']['frame_id'] == self.lesson_capture.id
        snapshot = (Path(job.payload['dir']) / job.payload['snapshot']['frame']).read_bytes()
        assert snapshot.startswith(b'\xff\xd8')
        for send in self.lesson_sends:
            if send.phase == 'look':
                assert send.images == (snapshot,), 'SETUP: any real Look must use the original retained camera crop'
        folder = Path(job.payload['dir']) / 'outputs'
        folder.mkdir(exist_ok=True)
        (folder / 'quiz.html').write_text('<!doctype html><title>Synthetic retained quiz</title>')
        # Canned external worker output, accepted/versioned/saved by the real manager.
        self.rig.worker.emit(job, 'output', kind='html', path='outputs/quiz.html',
            caption='Synthetic retained guard-cell practice', content=LESSON, generated=True)
        self.rig.worker.finish(job, 'The synthetic worker retained a guard-cell practice lesson.')
        assert job.attempt.completion.done() and job.attempt.completion.result()['status'] == 'done'
        await self.rig.published(self.session, lambda row: row['status'] == 'done')
        await self.rig.settle_delivery()
        assert self.session.deliverable == 'lesson' and self.session.steps() == LESSON['steps']
        assert self.rig.saved(self.session)['outputs'][0]['content'] == LESSON
        self.app.presenter.go_to_step(self.session.rid, 'q1')
        assert self.app.quiz.find_step()[0] is self.session
        # Actual outline resource creation on real accepted unit-camera pixels;
        # user geometry is input, never a fabricated SceneObject or model mask.
        capture = captures(self.app.hub).get(self.app.hub.latest_info.id)
        assert capture is not None, 'SETUP: actual accepted camera capture must remain available'
        self.outline = await tools.add_outline(self.app.hub, capture,
            [[[280, 280], [340, 280], [340, 340], [280, 340]]], source='click')
        assert self.app.hub.scene.objects[self.outline.id] is self.outline
        assert self.outline.origin == capture.id and self.outline.source == 'click'
        return job

    async def enter(self):
        # Public Quiz entry over actual App-owned dependencies; core callbacks stay intact.
        self.task = asyncio.create_task(self.app.quiz.clicked(self.outline.id))
        await eventually(self.judge.entered.is_set,
                         'SETUP: actual Quiz did not await external judge')
        assert len(self.judge.calls) == 1
        call = self.judge.calls[0]
        assert call.asked == self.origin and call.day == self.original_day
        assert not call.completed and not self.task.done()
        assert call.images == () and call.arguments == {'max_output_tokens': 600}
        assert 'Find a guard cell on your slide and click it.' in call.prompt
        assert f'{self.outline.id}, named no name yet' in call.prompt
        assert 'not named yet' in call.prompt and 'outlined by the user\'s click' in call.prompt
        assert self.app.day.outcomes_of(self.session.rid) == {}
        return call

    async def complete(self):
        self.judge.release.set()
        result = await asyncio.wait_for(asyncio.shield(self.task), 5)
        assert len(self.judge.calls) == 1 and self.judge.calls[0].completed
        await self.rig.settle_delivery()
        return result

    def restored(self):
        # Real readback of the originating day's journal, including after midnight.
        store = DayStore(root=self.app.day.root, day=self.original_day)
        store.switch(self.origin.thread)
        return store, Dialogue(store, lambda _message: None).history()

    def assert_activity(self, verdict, *, feedback=None, error=None, invalid=None):
        store, replay = self.restored()
        # Check still-useful outcomes and real lesson/readback before the pending
        # notice API. A missing notice must not mask an already-broken outcome.
        expected = {'q1': verdict} if verdict in ('right', 'wrong') else {}
        assert store.outcomes_of(self.session.rid) == expected
        outcomes = [e for e in store.journal.load() if e['type'] == 'quiz-outcome'
                    and e.get('rid') == self.session.rid]
        assert len(outcomes) == len(expected)
        if outcomes:
            assert outcomes[0]['thread'] == self.origin.thread and outcomes[0]['by'] == 'click'
        presenter = Presenter(self.app.hub, self.app.research, outcomes=store.outcomes_of)
        presenter.go_to_step(self.session.rid, 'q1')
        assert presenter.current_step()[1] == LESSON['steps'][0]
        if verdict == 'wrong':
            step, index, total = presenter.go_to_step(self.session.rid, 'missed')
            assert step == LESSON['steps'][0] and index == 0 and total == 2
        facts = [line for line in replay['lines']
                 if isinstance(line.get('quiz'), dict)
                 and line['quiz'].get('rid') == self.session.rid
                 and line['quiz'].get('step') == 'q1'
                 and line.get('role') != 'assistant']
        assert len(facts) == 1, (
            'MISSING_FEATURE: quiz facts require one independently replayable non-assistant notice', replay)
        notice = facts[0]
        assert notice['thread'] == self.origin.thread
        assert notice.get('objectIds') == [self.outline.id]
        assert notice['quiz'].get('verdict') == verdict
        if error is not None:
            assert error in json.dumps(notice['quiz']), (
                'MISSING_FEATURE: original judge failure must remain an app fact', notice)
        if invalid is not None:
            assert invalid in json.dumps(notice['quiz']), (
                'MISSING_FEATURE: invalid supplied verdict must not silently become unsure', notice)
        allowed = {send.reply['answer'] for send in self.rig.provider.sends if send.reply.get('answer')}
        if feedback is not None:
            allowed.add(feedback)
        assistant_lines = [line for line in replay['lines'] if line.get('role') == 'assistant']
        assistant_turns = [turn for thread in store.threads.values() for turn in thread.turns
                           if turn.role == 'assistant']
        published = [m for m in self.rig.renderer.messages if m.get('type') == 'chat'
                     and m.get('role') == 'assistant']
        assert all(line.get('text') in allowed for line in assistant_lines + published), (
            'MISSING_FEATURE: app facts must not synthesize assistant chat or wrap judge text', replay, published)
        assert all(turn.text in allowed for turn in assistant_turns), (
            'MISSING_FEATURE: conversation memory must not wrap judge text in app assistant prose', assistant_turns)
        if feedback is not None:
            # Delivery failure may prevent speech, never erase genuine model feedback from reload.
            assert len([line for line in assistant_lines if line.get('text') == feedback]) == 1
            assert len([turn for turn in assistant_turns if turn.text == feedback]) == 1
        for _voice, connection in self.rig.voices:
            assert all(row.args.get('content') in allowed for row in connection.messages('commentary')), (
                'MISSING_FEATURE: only intact genuine judge/model text may request speech', connection.sent)
        # Same-day notices must traverse actual Client JSON, not just persistence.
        if self.app.day.day == self.original_day:
            assert any(message == notice for message in self.rig.renderer.messages)
            assert self.app.presenter.wire()['outcomes'] == expected
        return notice, store

    async def close(self):
        self.judge.release.set()
        if self.task is not None and not self.task.done():
            await asyncio.wait_for(asyncio.shield(self.task), 5)
        await self.rig.close()


@pytest.mark.parametrize('reply,verdict,feedback,error,invalid', [
    ({'verdict': 'right', 'say': FEEDBACK}, 'right', FEEDBACK, None, None),
    ({'verdict': 'wrong', 'say': FEEDBACK}, 'wrong', FEEDBACK, None, None),
    ({'verdict': 'unsure', 'say': FEEDBACK}, 'unsure', FEEDBACK, None, None),
    ({'verdict': 'right'}, 'right', None, None, None),
    ({'verdict': 'wrong', 'say': ''}, 'wrong', None, None, None),
    ({'verdict': 'wrong', 'say': {'synthetic': 'non-text feedback'}}, 'wrong', None, None, None),
    ({'say': FEEDBACK}, None, FEEDBACK, None, None),
    ({'verdict': INVALID, 'say': FEEDBACK}, None, FEEDBACK, None, INVALID),
    ({}, None, None, None, None),
    (None, None, None, ERROR, None),
], ids=['right-feedback', 'wrong-feedback', 'unsure-feedback', 'right-no-feedback',
        'wrong-empty-feedback', 'wrong-nontext-feedback', 'missing-verdict-feedback',
        'invalid-verdict-feedback', 'empty-response', 'provider-exception'])
def test_actual_quiz_keeps_judge_feedback_and_distinct_app_facts(tmp_path, monkeypatch,
                                                              reply, verdict, feedback, error, invalid):
    rig = QuizApp(tmp_path, monkeypatch, reply, error=RuntimeError(error) if error else None)

    async def scenario():
        try:
            await rig.start()
            call = await rig.enter()
            assert call.voice is None
            await rig.complete()
            rig.assert_activity(verdict, feedback=feedback, error=error, invalid=invalid)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.local.close()


@pytest.mark.parametrize('delivery', ['idle', 'busy'])
@pytest.mark.parametrize('feedback', [FEEDBACK, None], ids=['model-feedback', 'no-feedback'])
def test_actual_quiz_voice_says_only_genuine_feedback_and_retains_replay(tmp_path, monkeypatch, delivery, feedback):
    rig = QuizApp(tmp_path, monkeypatch, {'verdict': 'wrong', **({'say': feedback} if feedback else {})})

    async def scenario():
        try:
            await rig.start()
            voice, connection = await rig.rig.voice(busy=delivery == 'busy')
            before = len(connection.sent)
            call = await rig.enter()
            assert call.voice is voice and not voice.closed
            await rig.complete()
            rows = [row for row in logical_context_messages(connection.sent[before:]) if row.complete and row.kind == 'commentary']
            assert [row.args['content'] for row in rows] == ([feedback] if feedback is not None else []), (
                'MISSING_FEATURE: original quiz voice must say genuine feedback exactly once, no canned fallback', rows)
            if feedback is None:
                contexts = [row.args['content'] for row in logical_context_messages(connection.sent[before:])
                            if row.complete and row.kind == 'thinking']
                assert any(rig.session.rid in text and 'q1' in text and rig.outline.id in text
                           for text in contexts), ('MISSING_FEATURE: original voice retains silent quiz facts', contexts)
            rig.assert_activity('wrong', feedback=feedback)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.local.close()


@pytest.mark.parametrize('transition', ['thread', 'closed-A-install-B', 'absent-install-B', 'new-session'])
@pytest.mark.parametrize('feedback', [FEEDBACK, None], ids=['model-feedback', 'no-feedback'])
def test_actual_judge_await_freezes_day_thread_and_voice_before_transitions(tmp_path, monkeypatch, transition, feedback):
    rig = QuizApp(tmp_path, monkeypatch, {'verdict': 'wrong', **({'say': feedback} if feedback else {})})

    async def scenario():
        try:
            await rig.start()
            original_voice = original_connection = None
            if transition != 'absent-install-B':
                original_voice, original_connection = await rig.rig.voice(busy=True)
            call = await rig.enter()
            assert call.voice is original_voice
            replacement_connection = None
            if transition == 'thread':
                await COMMANDS['thread'](rig.app, rig.rig.client,
                    {'type': 'thread', 'action': 'new', 'title': 'Different quiz conversation'})
                assert rig.app.day.active_id != rig.origin.thread
            else:
                if original_voice is not None:
                    await original_voice.close()
                if transition == 'new-session':
                    original_generation = rig.origin.generation
                    rig.app.new_session()  # the real App path: the view of the session left is cleared
                    assert rig.app.day.sid != rig.original_day and rig.app.day.generation != original_generation
                    assert not rig.app.presenter.open and rig.outline.id not in rig.app.hub.scene.objects
                replacement, replacement_connection = await rig.rig.voice()
                assert replacement is not original_voice and rig.app.brain.voice is replacement
            await asyncio.sleep(0)  # let actual context-switch recap work finish before observing quiz sends
            before_replacement = len(replacement_connection.sent) if replacement_connection else 0
            assert not call.completed and not rig.task.done()
            await rig.complete()
            if transition == 'thread':
                assert not [line for line in rig.app.day.active.lines if line.get('quiz')]
                rows = original_connection.messages('commentary')
                assert [row.args['content'] for row in rows] == ([feedback] if feedback else [])
            if replacement_connection is not None:
                later = [row for row in logical_context_messages(replacement_connection.sent[before_replacement:])
                         if row.complete and row.kind in ('thinking', 'commentary')]
                assert not later, ('MISSING_FEATURE: judgment must not borrow replacement B, even when A was absent', later)
            notice, _store = rig.assert_activity('wrong', feedback=feedback)
            assert notice['thread'] == rig.origin.thread and call.asked == rig.origin
            if transition == 'new-session':
                # Judged in the session it was asked in (assert_activity read it there); nothing in the one open.
                assert rig.app.day.outcomes_of(rig.session.rid) == {}
                assert not [line for thread in rig.app.day.threads.values() for line in thread.lines
                            if line.get('quiz') or (feedback is not None and line.get('text') == feedback)]
                assert not [turn for thread in rig.app.day.threads.values() for turn in thread.turns
                            if feedback is not None and turn.text == feedback]
                kept = rig.app.research.get(rig.session.rid)  # the researcher stays with its lesson
                assert kept is not None and kept.steps() == LESSON['steps']
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.local.close()


def test_actual_quiz_judged_across_midnight_stays_in_its_session_after_app_restart(tmp_path, monkeypatch):
    # External calendar boundary only: advance local-date conversion and the
    # no-argument export clock together. Real monotonic deadlines stay real;
    # App.build/import/restore are never replaced. Midnight does nothing (ADR 0017):
    # the session goes on, its date folder moves on, and a restart opens it again.
    localtime, strftime, now = time.localtime, time.strftime, time.time
    calendar_shift = [0]

    def calendar_localtime(stamp=None):
        return localtime((now() if stamp is None else stamp) + calendar_shift[0])

    def calendar_strftime(fmt, stamp=None):
        return strftime(fmt, calendar_localtime() if stamp is None else stamp)

    monkeypatch.setattr(time, 'localtime', calendar_localtime)
    monkeypatch.setattr(time, 'strftime', calendar_strftime)
    feedback = '\n  ' + FEEDBACK + '\n  '  # canned judge text, including outer whitespace
    rig = QuizApp(tmp_path, monkeypatch, {'verdict': 'wrong', 'say': feedback})
    restarted = None
    closed = False

    async def scenario():
        nonlocal restarted, closed
        try:
            await rig.start()
            call = await rig.enter()
            assert call.voice is None and not call.completed
            calendar_shift[0] = 2 * 86400
            assert rig.app.day.sid == rig.original_day and rig.app.day.date != rig.original_day
            assert rig.app.day.active_id == rig.origin.thread and rig.app.day.current(rig.origin.generation)
            await rig.complete()
            notice, store = rig.assert_activity('wrong', feedback=feedback)
            assert notice['day'] == rig.original_day
            paths = rig.app.paths
            exported = [json.loads(row) for row in paths.conversation_log.read_text().splitlines()]
            rows = [row for row in exported if row.get('quiz', {}).get('rid') == rig.session.rid]
            assert len(rows) == 2 and all(row['day'] == rig.original_day for row in rows)
            await rig.close()
            await rig.app.stop()
            rig.rig.local.close()
            closed = True
            restarted = App.build('http://127.0.0.1:1', paths, use_gpu=False)
            assert restarted.paths is paths and restarted.day.sid == rig.original_day  # the session it was in
            assert restarted.day.active_id == rig.origin.thread
            assert restarted.day.outcomes_of(rig.session.rid) == {'q1': 'wrong'}
            lines = restarted.dialogue.history()['lines']
            assert len([line for line in lines if line.get('text') == feedback]) == 1
            assert len([line for line in lines if isinstance(line.get('quiz'), dict) and line.get('role') != 'assistant'
                        and line['quiz'].get('rid') == rig.session.rid]) == 1  # replayed once, never imported again
            turns = [turn for thread in restarted.day.threads.values() for turn in thread.turns]
            assert len([turn for turn in turns if turn.role == 'assistant' and turn.text == feedback]) == 1
        finally:
            if not closed:
                await rig.close()
                await rig.app.stop()
                rig.rig.local.close()
            if restarted is not None:
                await restarted.stop()
                restarted.hub._analysis.shutdown(wait=True)
                restarted.hub._background.shutdown(wait=True)

    asyncio.run(scenario())
