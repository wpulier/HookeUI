"""Additive TEST: factual research lifecycle remains useful without app assistant prose.

Actual App/Brain/Registry/ResearchManager scheduler/WorkerAttempt/Client/Screen.
Only camera ingress, answer/voice SDK, external worker transport, artifact files
and renderer reports are synthetic. No worker process or hosted model is used.
The session node opens another session through the real App.new_session and its
listeners (ADR 0017). No Session, ToolRun, Line or ACK is fabricated;
no core callback, pump, provider budget or completion method is replaced.

Root alone executes. These tests do not certify browser paint, actual model
authorship of scripted worker results, remote speech or performance.
"""
from __future__ import annotations

import asyncio
import copy
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.brain.dispatch import in_answer_phase
from orchestrator.commands import COMMANDS
from orchestrator.research.files import research_file
from orchestrator.ticket import current
from orchestrator.voice.base import VoiceConfig
from orchestrator.voice.openai_live import OpenAILiveSession

from test_model_owned_announcements import ExternalLiveConnection, ExternalLiveContext
from test_model_owned_conversation import LocalApp, action, eventually, response
from tests.waiting import HANG_S

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')


START = 'Please start a researcher on guard-cell opening dynamics.'
PLAN = {'searches': ['guard-cell opening dynamics reference'],
        'sources': 'Synthetic reference source supplied by the external worker'}
QUESTION = {'what': 'the objective in use', 'why': 'interpret the retained field', 'seconds': 15}
FIRST = 'The synthetic researcher retained its original field and reference note.'
SECOND = 'The separate continuation concerns a different question.'
SOURCE = 'https://example.org/synthetic-guard-cell-reference'
MODEL_PLAN = 'I will use the requested research capability.'
MODEL_FINAL = 'The operation receipt is available in the research activity.'
MODEL_QUERY = 'The retained research facts answer this question.'


def finding(summary):
    # Canned external worker result, never labelled an actual remote model reply.
    return {'title': 'Synthetic guard-cell reference', 'summary': summary,
            'one_liner': summary, 'significance': 'low', 'details': [summary],
            'sources': [SOURCE], 'refs': [], 'limitations': ['The second evidence request was unanswered.']}


class ExternalAnswers:
    """Script only the external provider; real admission owns each entered send."""

    def __init__(self):
        self.actions = []
        self.sends = []
        self.memory = []

    async def ask(self, images, prompt, **kwargs):
        if not in_answer_phase():
            self.memory.append(prompt)  # independent Memory work, not an answer attempt
            return SimpleNamespace(data={'facts': [], 'summary': 'Synthetic memory summary'}, latency_ms=0.0)
        ticket = current()
        admission = ticket.admission if ticket is not None else None
        attempts = admission.attempts if admission is not None else ()
        phase = attempts[-1].phase if attempts else None
        if phase == 'fast':
            data = response(MODEL_PLAN, actions=self.actions)
        elif phase in ('query', 'late'):
            data = response(MODEL_QUERY)
        else:
            data = response(MODEL_FINAL)
        self.sends.append(SimpleNamespace(ticket=ticket, admission=admission, phase=phase,
            prompt=prompt, images=tuple(images), reply=copy.deepcopy(data)))
        return SimpleNamespace(data=copy.deepcopy(data), latency_ms=0.0, error=None)


class ExternalWorker:
    """Replace only ProcessChannel transport; the real manager allocates/settles runs."""

    alive = True

    def __init__(self, manager):
        self.manager = manager
        self.jobs = []

    async def call(self, operation, **payload):
        if operation == 'cancel':
            job = next(j for j in reversed(self.jobs) if j.payload['rid'] == payload['rid'])
            self.emit(job, 'done', status='cancelled', result=None)
            return {'ok': True}
        if operation != 'spawn':
            raise RuntimeError(f'Unexpected synthetic transport operation {operation}')
        session = self.manager.get(payload['rid'])
        attempt = session._attempt
        self.jobs.append(SimpleNamespace(payload=copy.deepcopy(payload), attempt=attempt,
            facts=copy.deepcopy(attempt.facts()), day=session.day, origin=copy.deepcopy(session.origin)))
        self.emit(self.jobs[-1], 'status', status='running')
        return {'ok': True}

    def emit(self, job, event, **payload):
        self.manager._event({'rid': job.payload['rid'], 'event': event, **copy.deepcopy(payload)})

    def plan(self, job):
        self.emit(job, 'plan', **PLAN)

    def question(self, job, *, what=QUESTION['what']):
        self.emit(job, 'request', **{**QUESTION, 'what': what})
        return copy.deepcopy(self.manager.get(job.payload['rid']).requests[-1])

    def artifact(self, job):
        folder = Path(job.payload['dir'])
        original = folder / job.payload['snapshot']['frame']
        data = original.read_bytes()  # real original snapshot, not the latest camera
        (folder / 'outputs').mkdir(exist_ok=True)
        filename = f'outputs/kept-field-run-{job.facts["run"]}.jpg'
        (folder / filename).write_bytes(data)  # authorized external worker file boundary
        self.emit(job, 'output', kind='image', path=filename,
                  caption='Synthetic external researcher artifact from its original input', generated=False)
        return filename, data

    def finish(self, job, summary=FIRST, *, status='done'):
        self.emit(job, 'done', status=status, result=finding(summary) if status == 'done' else None,
                  spent=0.01, response_id=f'synthetic-response-{job.attempt.id}')

    def stop(self):
        self.alive = False


class Renderer:
    """Consume real Client JSON; report only this synthetic renderer's applied state."""

    def __init__(self, rig):
        self.rig = rig
        self.messages = []
        self.pending = []
        self.viewer = None
        self.hold = False
        self.wait_entered = asyncio.Event()
        self.task = None
        self.owned = False
        self.seq = 0
        self.applied = []
        self.changes = []

    async def start(self):
        self.task = asyncio.create_task(self.consume())
        await self.report()

    async def consume(self):
        while True:
            raw = await self.rig.client.next_message()
            if not isinstance(raw, str):
                continue
            message = json.loads(raw)
            self.messages.append(message)
            if message.get('type') == 'presenter':
                if self.hold:
                    self.pending.append(message)
                else:
                    self.viewer = copy.deepcopy(message)
                    await self.report()
            elif message.get('type') == 'present' and self.owned:
                if self.hold:
                    self.changes.append(message['change'])
                    self.wait_entered.set()
                else:
                    self.applied.append(message['change'])
                    await self.report()
            elif message.get('type') == 'screen-ask':
                if self.hold and self.pending:
                    self.wait_entered.set()  # emitted after real Screen.until registers
                elif not self.hold:
                    await self.report()

    async def report(self):
        if self.owned:
            from tests.test_workspace_observation import report
            from tests.test_legacy_workspace_projection import structural_item
            from tests.test_screen_workspace import REF
            from orchestrator.capture import RUN
            self.seq += 1
            raw = report(seq=self.seq)
            viewer = structural_item('viewer', 'viewer', {'shows': (self.viewer or {}).get('rid')},
                                     generation=self.seq, revision=self.seq)
            # The external renderer reports the exact item it applied from real
            # session/presenter JSON, not just the researcher's mutable identity.
            rows = self.research((self.viewer or {}).get('rid'))
            items = rows[-1].get('items', []) if rows else []
            index = (self.viewer or {}).get('index', 0)
            if 0 <= index < len(items):
                viewer['view']['legacy'].update(work=copy.deepcopy(items[index].get('work')), workReady=True)
            viewer['open'] = viewer['visible'] = bool((self.viewer or {}).get('open'))
            raw.update(client=REF.wire(), appRunId=RUN, items=[viewer], sources=[], targets=[], edges=[],
                       applied=list(self.applied), user=[], commandResults=[], complete=False,
                       omitted={'items': 1, 'sources': 0, 'targets': 0, 'edges': 0})
            raw['interaction'] = {'pointer': {'state': 'absent', 'modality': 'mouse', 'buttons': 0,
                'hitPath': [], 'targetCandidates': []}, 'contacts': [],
                'focus': {'presentationId': None, 'controlId': None, 'role': 'none', 'editable': False},
                'lastContentPointer': None, 'lastContentFocus': None, 'selected': [],
                'availability': 'partial', 'unknown': ['synthetic-renderer']}
            result = await COMMANDS['screen'](self.rig.app, self.rig.client, {'type': 'screen', **raw})
            assert result['ok'], result
            return
        items = [{'id': 'live', 'kind': 'live', 'visible': True, 'opacity': 1.0, 'z': 0}]
        if self.viewer is not None:
            items.append({'id': 'viewer', 'kind': 'viewer', 'open': self.viewer['open'],
                          'shows': self.viewer['rid'], 'z': 2})
        result = await COMMANDS['screen'](self.rig.app, self.rig.client,
            {'type': 'screen', 'items': items, 'visible': True, 'applied': []})
        assert result['ok'], f'SETUP: synthetic renderer report rejected: {result}'

    async def release(self):
        self.hold = False
        pending, self.pending = self.pending, []
        for message in pending:
            self.viewer = copy.deepcopy(message)  # apply the actual published viewer message
            await self.report()  # never set a future, applied ID or completion result
        self.applied.extend(self.changes)
        self.changes.clear()
        if self.owned:
            await self.report()
        await asyncio.sleep(0)

    def research(self, rid):
        rows = []
        for message in self.messages:
            if message.get('type') == 'research' and message['session']['rid'] == rid:
                rows.append(message['session'])
            if message.get('type') == 'research-list':
                rows.extend(s for s in message['sessions'] if s['rid'] == rid)
        return rows


class ResearchApp:
    def __init__(self, tmp_path, monkeypatch):
        self.local = LocalApp(tmp_path, monkeypatch)
        self.app, self.client = self.local.app, self.local.client
        self.provider = ExternalAnswers()
        monkeypatch.setattr(self.app.agent, 'vision', lambda _model: self.provider)
        self.worker = ExternalWorker(self.app.research)
        self.app.research._worker = self.worker  # external transport, no process is launched
        self.finished = []
        self.app.brain.registry.finished.append(lambda run, ctx: self.finished.append((run, ctx)))
        self.renderer = Renderer(self)
        self.voices = []

    async def voice(self, *, busy=False):
        connection = ExternalLiveConnection()
        voice = OpenAILiveSession(VoiceConfig(model='gpt-live-1'),
            connect=lambda: ExternalLiveContext(connection))
        await voice.start()
        self.app.brain.voice = voice  # same session installation field as actual route
        self.voices.append((voice, connection))
        if busy:
            connection.busy_task = asyncio.create_task(connection.busy())
            await eventually(lambda: voice.last_speech_at() > 0,
                             'SETUP: real SDK audio must make original session busy')
            assert voice.idle_since() == 0
        return voice, connection

    async def ask(self, words, actions=(), *, channel='chat', delegation='research-origin'):
        self.provider.actions = list(actions)
        before = len(self.provider.sends)
        if channel == 'chat':
            if self.renderer.owned:
                await self.renderer.report()
            result = await self.local.ask(words)
        else:
            self.local.camera_seq += 1
            await self.app.hub.source._accept(self.local.camera_jpeg, seq=self.local.camera_seq,
                                             boot='synthetic-model-owned-camera')
            result = await self.app.brain.turn(words, channel, delegation_id=delegation,
                                               asked=self.app.day.here())
        sends = self.provider.sends[before:]
        assert sends, 'SETUP: public request must reach actual admitted provider dispatch'
        admission = sends[0].admission
        assert admission is not None and all(s.admission is admission for s in sends)
        assert admission.used == len(sends) <= 3, 'SETUP: original request MAX3 accounting differs'
        assert len({a.attempt_id for a in admission.attempts}) == len(sends)
        assert all(not s.images for s in sends), 'SETUP: these operations use notes, not image interpretation'
        assert result['answer'] in {MODEL_PLAN, MODEL_FINAL, MODEL_QUERY}
        return result

    async def start(self, *, channel='chat', owned=False):
        if owned:
            from tests.test_screen_workspace import REF
            self.renderer.owned = True
            self.app.accept_workspace(self.client, REF)
        await self.renderer.start()
        original = self.app.day.here()
        result = await self.ask(START, [action('start_researcher', brief='Guard-cell opening dynamics',
            effort='quick', deliverable='research', asked_with=START)], channel=channel)
        await eventually(lambda: len(self.worker.jobs) == 1,
                         'SETUP: real manager scheduler must dispatch original worker')
        session = self.app.research.get(self.worker.jobs[0].payload['rid'])
        job = self.worker.jobs[0]
        await eventually(lambda: session.rid in self.app.research._watchdogs,
                         'SETUP: actual spawn must return before completion controls')
        starts = [(r, c) for r, c in self.finished if r.tool == 'start_researcher']
        assert len(starts) == 1 and starts[0][0].ok is True
        run, ctx = starts[0]
        assert run.occurrence.request_id == ctx.admission.request_id
        assert run.occurrence.action_id == result['actions'][0]['actionId']
        assert ctx.asked == original and session.origin['turn_id'] == ctx.turn_id
        assert session.origin['thread'] == original.thread and session.origin['asked'] is True
        assert job.attempt is session._attempt and not job.attempt.completion.done()
        assert job.attempt.record['dispatchedAt'] and job.facts == job.attempt.facts()
        assert session.rid in self.app.research.spending._held
        assert self.app.research.spending._held_day[session.rid] == session.day
        assert job.day == session.day == self.app.day.day
        assert Path(job.payload['dir']) == self.app.research.root / job.day / session.rid
        assert job.payload['snapshot']['frame_id'] == ctx.frame_id
        assert (Path(job.payload['dir']) / job.payload['snapshot']['frame']).is_file()
        return session, job, ctx

    def saved(self, session):
        return json.loads((self.app.research.root / session.day / session.rid / 'session.json').read_text())

    async def request_open_work(self, session):
        reply = await COMMANDS['presenter'](self.app, self.client,
            {'type':'presenter', 'action':'show', 'rid':session.rid})
        assert reply['ok']
        assert self.app.presenter.for_client(self.client).open

    async def published(self, session, check):
        await eventually(lambda: any(check(row) for row in self.renderer.research(session.rid)),
                         'SETUP: actual Client JSON publication did not contain retained research facts')
        return [row for row in self.renderer.research(session.rid) if check(row)][-1]

    async def settle_delivery(self):
        await self.renderer.release()
        await eventually(lambda: not self.app.hub.screen._waiters,
                         'SETUP: actual viewer wait did not settle after renderer report')
        for _voice, connection in self.voices:
            connection.busy_stop.set()
        queued = len(self.app.narrator.waiting)
        accepted_events = sum(row.get('type') == 'app-notice' and row.get('producer', {}).get('kind') == 'research'
                              and row.get('event') in ('plan', 'request', 'completed')
                              for row in self.renderer.messages)
        assert queued <= accepted_events + len(self.finished), (
            'SETUP: synthetic drain allowance must be bounded by actual accepted producer events', queued, accepted_events)
        # Each synthetic SDK response crosses the real 0.1-second confirmation
        # poll. Preserve all eighty accepted questions and production timing.
        await eventually(lambda: not self.app.narrator.waiting,
                         'SETUP: actual research narration queue did not drain')
        if self.app.narrator._task is not None:
            await asyncio.wait_for(asyncio.shield(self.app.narrator._task), HANG_S)
        # Queue absence can precede SDK append/task publication by one scheduling turn.
        await asyncio.sleep(0)
        if self.renderer.task.done():
            self.renderer.task.result()  # expose external renderer failures, never swallow them

    def assert_no_app_assistant(self):
        allowed = {send.reply['answer'] for send in self.provider.sends if send.reply.get('answer')}
        posted = [m for m in self.renderer.messages if m.get('type') == 'chat'
                  and m.get('role') == 'assistant' and m.get('text') not in allowed]
        stored = [line for thread in self.app.day.threads.values() for line in thread.lines
                  if line.get('type') == 'chat' and line.get('role') == 'assistant'
                  and line.get('text') not in allowed]
        for _v, conn in self.voices:
            for row in conn.messages('commentary'):
                text = row.args.get('content', '')
                if text in allowed:
                    continue
                # An explicit input asking the Live model for its own words is
                # not assistant text. Check its original producer, not merely a prefix.
                assert text.startswith('Requested notification: ')
                header, wrapped = text.split('\n', 1)
                origin = json.loads(header[header.index('{'):])
                opening = '<voice-context source="app" untrusted="true">\n'
                assert wrapped.startswith(opening) and wrapped.endswith('\n</voice-context>')
                payload = json.loads(wrapped[len(opening):-len('\n</voice-context>')])
                facts = payload['facts']
                matching = [notice for notice in self.renderer.messages if notice.get('type') == 'app-notice'
                            and notice.get('producer') == {'kind': 'research', 'id': facts['researchId']}
                            and notice.get('event') == payload['event']
                            and (notice.get('facts', {}).get('run') or {}).get('runId') == facts['run']['runId']
                            and (notice.get('facts', {}).get('request') or {}).get('id') ==
                                (facts.get('request') or {}).get('id')]
                assert matching and payload['event'] in ('request', 'completed')
                assert any(all(origin[key] == notice['origin'].get(key)
                               for key in ('turnId', 'requestId', 'actionId'))
                           and row.args.get('delegation_id') == notice['origin'].get('delegationId')
                           for notice in matching)
                assert facts['status'] == matching[0]['facts']['status']
                assert facts['cause'] == matching[0]['facts']['cause']
        assert not (posted or stored), (
            'MISSING_FEATURE: research lifecycle data must not become app-authored assistant wrappers',
            posted, stored)

    def assert_frozen_result_publications(self, session, run_id, text):
        tied = []
        for row in self.renderer.research(session.rid):
            if (row.get('resultRun') or {}).get('runId') == run_id:
                tied.append(row.get('result'))
            tied.extend(entry.get('result') for entry in row.get('history') or []
                        if entry.get('runId') == run_id)
        assert tied and all((r or {}).get('summary') == text and r.get('sources') == [SOURCE]
                            and r.get('limitations') == finding(text)['limitations'] for r in tied), (
            'MISSING_FEATURE: original producing run must retain its own result in public activity', tied)

    async def close(self):
        await self.renderer.release()
        for voice, connection in self.voices:
            connection.busy_stop.set()
            if connection.busy_task is not None:
                await connection.busy_task
            await voice.close()
        self.app.research.stop()
        if self.renderer.task is not None:
            self.renderer.task.cancel()
            await asyncio.gather(self.renderer.task, return_exceptions=True)


@pytest.mark.parametrize('delivery', ['typed', 'idle', 'busy', 'closed'])
def test_plan_and_actual_evidence_questions_remain_discoverable_without_app_wrappers(tmp_path, monkeypatch, delivery):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            original_voice = replacement = None
            if delivery != 'typed':
                original_voice, _ = await rig.voice(busy=delivery == 'busy')
            session, job, ctx = await rig.start(channel='chat' if delivery == 'typed' else 'voice')
            if delivery == 'closed':
                await original_voice.close()
                replacement, replacement_connection = await rig.voice()
                assert original_voice.closed and replacement is not original_voice
            rig.worker.plan(job)
            q1 = rig.worker.question(job)
            assert q1['id'] == 'q1' and q1['status'] == 'asked'
            assert q1['runId'] == job.attempt.id and q1['run'] == job.facts['run']
            assert q1['origin'] == job.origin and q1['origin']['turn_id'] == ctx.turn_id
            row = await rig.published(session, lambda r: r.get('plan') == PLAN and bool(r.get('requests')))
            assert row['requests'][0] == q1 and row['thread'] == ctx.asked.thread
            assert rig.saved(session)['plan'] == PLAN and rig.saved(session)['requests'][0] == q1
            assert 'q1' in rig.app.brain.moment().researchers
            status_at = len(rig.provider.sends)
            await rig.ask('What does the researcher need?', [action('research_status', researcher=session.name)])
            assert any('q1' in s.prompt and QUESTION['what'] in s.prompt for s in rig.provider.sends[status_at:])
            await rig.ask(f'Tell {session.name} the objective is 40x.',
                         [action('researcher_note', researcher=session.name, text='40x', answers=q1['id'])])
            assert session.requests[0]['status'] == 'answered' and session.requests[0]['answer'] == '40x'
            assert any(n.get('answers') == q1['id'] and '40x' in n['text'] for n in session.notes)
            q2 = rig.worker.question(job, what='a second reference field')
            assert q2['id'] == 'q2' and q2['runId'] == q1['runId']
            rig.worker.finish(job)
            completion = job.attempt.completion.result()
            assert completion['run'] == job.facts and completion['result']['summary'] == FIRST
            await rig.published(session, lambda r: r['status'] == 'done')
            assert session.requests[1]['status'] == 'unanswered'
            saved = rig.saved(session)
            assert saved['requests'][0]['answer'] == '40x' and saved['requests'][1]['status'] == 'unanswered'
            assert saved['result']['limitations'] == finding(FIRST)['limitations']
            rig.app.hub.broadcast(rig.app.research_list())  # actual reconnect/list producer
            await rig.published(session, lambda r: r.get('resultRun', {}).get('runId') == job.attempt.id)
            assert len(rig.worker.jobs) == 1 and job.attempt.completion.done()
            assert session.rid not in rig.app.research.spending._held
            assert session.spent == pytest.approx(0.01)
            await rig.settle_delivery()
            rig.assert_frozen_result_publications(session, job.attempt.id, FIRST)
            rig.assert_no_app_assistant()
            if delivery in ('idle', 'busy'):
                contexts = [r.args['content'] for r in rig.voices[0][1].messages('thinking')
                            + rig.voices[0][1].messages('commentary')]
                assert any(q1['id'] in text and q1['runId'] in text and QUESTION['why'] in text for text in contexts), (
                    'MISSING_FEATURE: original voice context must retain factual q-ID and producing run', contexts)
            if replacement is not None:
                crossed = [r.args for r in replacement_connection.messages('thinking')
                           + replacement_connection.messages('commentary')
                           if PLAN['searches'][0] in r.args.get('content', '')
                           or QUESTION['what'] in r.args.get('content', '')]
                assert not crossed, ('MISSING_FEATURE: closed original research voice must not borrow replacement B', crossed)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


def test_completed_attempt_keeps_original_result_across_real_viewer_wait_and_later_continuation(tmp_path, monkeypatch):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            session, first, ctx = await rig.start(owned=True)
            filename, data = rig.worker.artifact(first)
            rig.renderer.hold = True
            rig.worker.finish(first)
            # Explicit user open before the asynchronous completion listener runs.
            # Completion alone must not reopen a closed viewer or switch from an
            # independently selected picture to the written finding.
            await rig.request_open_work(session)
            original = copy.deepcopy(first.attempt.completion.result())
            await eventually(rig.renderer.wait_entered.is_set,
                             'SETUP: original completion must enter real Screen.until behind held renderer')
            assert rig.app.hub.screen._waiters and rig.renderer.pending[0]['rid'] == session.rid
            await rig.published(session, lambda r: r.get('resultRun', {}).get('runId') == first.attempt.id)
            assert original['run']['origin']['thread'] == ctx.asked.thread
            await COMMANDS['thread'](rig.app, rig.client, {'type': 'thread', 'action': 'new', 'title': 'Different continuation'})
            assert rig.app.day.active_id != ctx.asked.thread
            await rig.ask(f'Continue {session.name} with a different reference question.',
                         [action('researcher_followup', researcher=session.name,
                                 question='Use a different reference question.', mode='continue',
                                 asked_with=f'Continue {session.name} with a different reference question.')])
            await eventually(lambda: len(rig.worker.jobs) == 2, 'SETUP: actual follow-up must dispatch a distinct attempt')
            second = rig.worker.jobs[1]
            await eventually(lambda: session.rid in rig.app.research._watchdogs, 'SETUP: continuation spawn must settle')
            assert second.attempt is not first.attempt and second.attempt.id != first.attempt.id
            assert second.origin['turn_id'] != first.origin['turn_id']
            rig.worker.finish(second, SECOND)
            assert second.attempt.completion.result()['result']['summary'] == SECOND
            assert session.result['summary'] == SECOND and session.history[0]['result']['summary'] == FIRST
            assert first.attempt.completion.result() == original
            assert session.rid not in rig.app.research.spending._held
            assert session.spent == pytest.approx(0.02)
            assert (Path(first.payload['dir']) / filename).read_bytes() == data
            await rig.published(session, lambda r: r.get('resultRun', {}).get('runId') == second.attempt.id)
            await rig.settle_delivery()
            rig.assert_frozen_result_publications(session, first.attempt.id, FIRST)
            rig.assert_frozen_result_publications(session, second.attempt.id, SECOND)
            assert rig.saved(session)['history'][0]['runId'] == first.attempt.id
            read_at = len(rig.provider.sends)
            await rig.ask('Read that original research history.', [action('read_result', researcher=session.name, part='history')])
            assert any(FIRST in s.prompt for s in rig.provider.sends[read_at:])
            rig.assert_no_app_assistant()
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


@pytest.mark.parametrize('terminal', ['timeout-with-output', 'interrupted-without-output'])
def test_partial_artifact_utility_and_empty_interruption_are_separate_facts(tmp_path, monkeypatch, terminal):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            session, job, ctx = await rig.start()
            if terminal == 'timeout-with-output':
                filename, data = rig.worker.artifact(job)
                rig.worker.finish(job, status='timeout')
                assert session.status == 'partial' and session.result['partial'] is True
                assert job.attempt.completion.result()['cause'] == 'timeout'
                assert job.attempt.completion.result()['status'] == 'partial'
                assert rig.saved(session)['resultRun']['runId'] == job.attempt.id
                output = session.outputs[0]
                assert output['version'] == 1 and output['path'] == filename
                found = research_file(rig.app.research.root, job.day, session.rid,
                                      'outputs', Path(filename).name)
                assert found is not None and found[0].read_bytes() == data and found[1] == 'image/jpeg'
                picture = rig.app.hub.resources.picture(session.rid, day=job.day)
                assert picture is not None and picture[0] == data
                entry = rig.app.hub.resources.get(session.rid, day=job.day)
                assert entry.day == job.day and entry.thread == ctx.asked.thread
                assert entry.source['pictures'] == [{'researcher': session.rid,
                    'output': output['id'], 'version': output['version'], 'path': filename}]
                assert (Path(job.payload['dir']) / filename).read_bytes() == data
                read_at = len(rig.provider.sends)
                await rig.ask('Read the kept partial research.', [action('read_result', researcher=session.name)])
                assert any('described from what it found and made' in s.prompt for s in rig.provider.sends[read_at:])
                assert 'described from what it found and made' in rig.app.hub.resources.read(session.rid, job.day)
                summary = session.result['summary']  # app partial_result, not a provider answer
                assert summary not in {s.reply['answer'] for s in rig.provider.sends}
            else:
                rig.worker.finish(job, status='interrupted')
                assert session.status == 'interrupted' and session.result is None and not session.outputs
                assert job.attempt.completion.result()['cause'] == 'interrupted'
                assert job.attempt.completion.result()['result'] is None
                assert rig.saved(session)['resultRun']['runId'] == job.attempt.id
                assert rig.app.hub.resources.picture(session.rid, day=job.day) is None
                assert 'No written result' in rig.app.hub.resources.read(session.rid, job.day)
            row = await rig.published(session, lambda r: r['status'] == session.status)
            assert row['thread'] == ctx.asked.thread and row['resultRun']['runId'] == job.attempt.id
            assert len(rig.worker.jobs) == 1 and session.rid not in rig.app.research._watchdogs
            assert session.rid not in rig.app.research.spending._held
            assert session.spent == pytest.approx(0.01)
            await rig.settle_delivery()
            rig.assert_no_app_assistant()
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


def test_completion_waiting_for_real_renderer_does_not_acquire_the_session_opened(tmp_path, monkeypatch):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            session, job, ctx = await rig.start(owned=True)
            filename, data = rig.worker.artifact(job)
            rig.renderer.hold = True
            rig.worker.finish(job)
            await rig.request_open_work(session)
            original = copy.deepcopy(job.attempt.completion.result())
            await eventually(rig.renderer.wait_entered.is_set,
                             'SETUP: completion must enter actual viewer await before another session opens')
            assert rig.app.hub.screen._waiters and rig.renderer.pending[0]['rid'] == session.rid
            old_session, old_generation = rig.app.day.sid, rig.app.day.generation
            # The user opens another session (ADR 0017) through the real App lifecycle, which clears the view.
            # No day/context/session field is relabelled.
            rig.app.new_session()
            assert rig.app.day.sid != old_session and rig.app.day.generation != old_generation
            assert session.sitting == old_session and session.origin['thread'] == ctx.asked.thread
            assert not rig.app.presenter.open
            assert job.attempt.completion.result() == original
            assert session.rid not in rig.app.research.spending._held
            # Both sessions are on one calendar day: its spend is the researcher's, wherever it is filed.
            assert rig.app.research.spending.spent(session.day) == pytest.approx(0.01)
            assert session not in rig.app.research.researchers()
            assert session in rig.app.research.researchers(session=old_session)
            kept = next(s for s in rig.app.research.read_day(session.day, kept_only=False) if s.rid == session.rid)
            assert kept.result['summary'] == FIRST and kept.result_run['runId'] == job.attempt.id
            assert kept.sitting == old_session
            assert (Path(job.payload['dir']) / filename).read_bytes() == data
            before_release = len(rig.renderer.messages)
            await rig.settle_delivery()
            later = rig.renderer.messages[before_release:]
            other_session_posts = [m for m in later if m.get('type') == 'chat' and m.get('role') == 'assistant'
                                   and m.get('research') == session.rid]
            assert not other_session_posts, ('MISSING_FEATURE: completed research may not post app prose into another session',
                                              other_session_posts)
            historical = rig.app.hub.resources.get(session.rid, day=old_session)
            assert historical is not None
            assert FIRST in rig.app.hub.resources.read(session.rid, old_session)
            rig.assert_frozen_result_publications(session, job.attempt.id, FIRST)
            rig.assert_no_app_assistant()
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()
