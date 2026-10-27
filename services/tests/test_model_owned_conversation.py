"""TEST precursor: actual App/COMMANDS/Brain answers remain model-owned.

Synthetic camera ingress, local retained records and scripted external replies.
The optional file barrier delegates the real Path.read_text after release. No
Brain synthesis, tool, receipt, Ticket, completion or Dialogue is replaced.
No server/GPU/model/browser is started. Passing tests establish response plumbing,
not model quality or painted pixels. Latest user direction supersedes ADR0006's
older instruction to delete sentences and insert application-authored prose.
"""
import asyncio
import copy
import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.app import App
from orchestrator.capture import captures
from orchestrator.commands import COMMANDS
from orchestrator.hub import Client
from orchestrator.paths import Paths
from orchestrator.ticket import current
from tests.test_resource_inspection import assert_supplied_answer_facts, decoded_answer_facts
from tests.waiting import until_async


INITIAL = 'The retained note can help explain this reference without identifying the current specimen.'
PLAN = 'I will read the retained note and explain its provenance.'
QUERY = 'The retained note describes a synthetic reference; it is not an observation of the current specimen.'
FINAL = 'The synthetic reference is useful background, while the current specimen remains unexamined.'
LATE = 'The retained note has now arrived; it describes the synthetic reference, not the current specimen.'
RECOVERY = 'The requested outline is absent, so it has not been selected.'
BAD_VISUAL = 'I visually examined every window and its controls.'
CORRECTED = 'No workspace image was supplied for this request, so I can explain the available facts without claiming a visual inspection.'
RETAINED = 'SYNTHETIC_ORIGINAL_REFERENCE_NOTE'


def response(answer, *, actions=(), scope='none', refs=(), intent=None):
    return {'answer': answer, 'targets': [], 'actions': list(actions),
            'intent': intent or ('request' if actions else 'question'),
            'needs_look': False, 'observation_scope': scope, 'input_refs': list(refs)}


def action(tool, **args):
    return {'tool': tool, 'args': args}


class ExternalAnswers:
    """Only the external provider's ask boundary; no semantic assertion in it."""
    def __init__(self, initial, *, final='valid', late='valid', correction=None, query_empty=False):
        self.initial = initial
        self.final, self.late, self.correction = final, late, correction
        self.query_empty = query_empty
        self.sends = []

    async def ask(self, images, prompt, **kwargs):
        ticket = current()
        admission = getattr(ticket, 'admission', None)
        attempts = admission.attempts if admission is not None else ()
        phase = attempts[-1].phase if attempts else None
        sent = SimpleNamespace(ticket=ticket, admission=admission,
            phase=phase, images=tuple(images), prompt=prompt)
        self.sends.append(sent)
        if len(self.sends) == 1:
            data = self.initial
        elif self.correction is not None:
            data = response(self.correction)
        elif phase == 'final':
            if self.final == 'timeout':
                sent.provider_error = asyncio.TimeoutError('synthetic external final provider unavailable')
                raise sent.provider_error
            if self.final == 'empty':
                data = response('')
            elif self.final == 'bad-scope':
                data = response(BAD_VISUAL, scope='workspace-pixels')
            else:
                data = response(FINAL)
        elif phase == 'late':
            if self.late == 'timeout':
                raise asyncio.TimeoutError('synthetic external late provider unavailable')
            data = response(LATE)
        elif phase == 'query':
            data = response('' if self.query_empty else QUERY)
        else:
            # The existing missing-outline path may replan from notes before final.
            data = response(RECOVERY)
        sent.reply_data = copy.deepcopy(data)
        return SimpleNamespace(data=copy.deepcopy(data), latency_ms=0.0,
                               model='scripted-external-provider', error=None)


class LocalApp:
    """Real local application, with no replacement manager or synthesis callback."""
    def __init__(self, tmp_path, monkeypatch, *, initial=None, final='valid', late='valid', correction=None, query_empty=False):
        self.app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
        settings = self.app.hub.settings
        settings.fast = settings.vision = 'gemini-3.5-flash'
        settings.compare = ''
        settings.pointing = 'box'
        settings.closeup = 'single'
        settings.outcome_lines = 'off'
        settings.speculate = 'off'
        # A real legacy local command socket, not invented accepted pixel authority.
        self.client = Client(None)
        self.app.hub.clients.add(self.client)
        self.app.hub.execution.set_enabled(False)
        self.camera_seq = 0
        pixels = np.full((640, 640), 5, np.uint8)
        disc = np.zeros_like(pixels)
        cv2.circle(disc, (320, 320), 280, 1, -1)
        texture = np.random.default_rng(641).integers(95, 210, pixels.shape, dtype=np.uint8)
        pixels[disc.astype(bool)] = texture[disc.astype(bool)]
        encoded_ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 96])
        assert encoded_ok, 'SETUP: synthetic camera pixels must encode as an actual JPEG'
        self.camera_jpeg = encoded.tobytes()
        self.record = self.app.hub.resources.results.add('compare', {}, RETAINED,
            values={'reference': RETAINED, 'basis': 'synthetic local retained note'},
            request='Retain the synthetic reference note.', thread=self.app.day.active_id)
        self.file = self.app.hub.resources.results.root / self.record['day'] / (self.record['id'] + '.json')
        self.provider = ExternalAnswers(initial or response(PLAN, actions=[action('resources', id=self.record['id'])]),
            final=final, late=late, correction=correction, query_empty=query_empty)
        monkeypatch.setattr(self.app.agent, 'vision', lambda _model: self.provider)

    async def ask(self, question='Read the retained reference note and explain what it establishes.'):
        # Legacy Brain snapshots before the notes-only fast pass. Supply that
        # prerequisite through actual intake; availability is not an attachment.
        self.camera_seq += 1
        await self.app.hub.source._accept(self.camera_jpeg, seq=self.camera_seq,
                                         boot='synthetic-model-owned-camera')
        frame = self.app.hub.source.latest()
        info = self.app.hub.latest_info
        assert frame is not None and info is not None and info.id == frame.id
        captured = captures(self.app.hub).get(frame.id)
        assert captured is not None and captured.jpeg == frame.jpeg == self.camera_jpeg
        assert captured.seq == frame.seq == self.camera_seq
        assert captured.boot == frame.boot == 'synthetic-model-owned-camera'
        return await COMMANDS['ask'](self.app, self.client, {'type': 'ask', 'text': question})

    def lines(self):
        return self.app.dialogue.history()['lines']

    def assistant_texts(self):
        return [row['text'] for row in self.lines()
                if row.get('type') == 'chat' and row.get('role') == 'assistant' and row.get('text')]

    def assert_budget(self, *, camera_recovery=False):
        sends = self.provider.sends
        assert sends, 'BEHAVIOR: real answer dispatch must reach the scripted external provider'
        admission = sends[0].admission
        assert admission is not None and all(send.admission is admission for send in sends)
        assert all(send.ticket is sends[0].ticket for send in sends)
        assert admission.used == len(sends) <= 3, 'BEHAVIOR: entered sends stay spent within original MAX3'
        assert len({attempt.attempt_id for attempt in admission.attempts}) == len(sends)
        if camera_recovery:
            looks = [send for send in sends if send.phase == 'look']
            assert len(looks) == 1 and len(looks[0].images) == 1
            assert all(not send.images or (send.phase == 'final' and 'original request camera frame' in send.prompt)
                       for send in sends if send.phase != 'look')
        else:
            assert all(not send.images for send in sends), 'BEHAVIOR: retained text is not camera/workspace pixels'
        return admission

    def close(self):
        self.app.hub._analysis.shutdown(wait=True)
        self.app.hub._background.shutdown(wait=True)


def test_initial_useful_model_answer_reaches_actual_chat_intact(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch, initial=response(INITIAL))
    async def scenario():
        result = await rig.ask('What can this retained reference tell me?')
        assert result['answer'] == INITIAL
        assert rig.assistant_texts() == [INITIAL]
        assert rig.app.conversation.turns[-1].text == INITIAL
        assert rig.app.agent is rig.app.brain.agent
        assert result['actions'] == []
        assert rig.assert_budget().used == 1
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_initial_incompatible_basis_is_explained_by_model_not_replaced_by_code(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch,
        initial=response(BAD_VISUAL, scope='workspace-pixels'), correction=CORRECTED)
    async def scenario():
        result = await rig.ask('Explain what is visible in the workspace.')
        # The invalid claim is never authorized; a remaining existing answer round
        # must let the model explain missing input rather than write an I-cannot reply.
        assert result['answer'] == CORRECTED, 'MISSING_FEATURE: scope evidence must reach a model-owned explanation'
        assert rig.assistant_texts() == [CORRECTED]
        assert BAD_VISUAL not in rig.assistant_texts()
        assert rig.assert_budget().used == 2
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_final_model_answer_keeps_actual_original_resource_receipt(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch)
    async def scenario():
        result = await rig.ask()
        assert result['answer'] == QUERY
        assert rig.assistant_texts() == [QUERY]
        assert rig.app.conversation.turns[-1].text == QUERY
        runs = result['actions']
        assert len(runs) == 1 and runs[0]['tool'] == 'resources' and runs[0]['ok'] is True
        assert RETAINED in runs[0]['summary'] and rig.record['id'] in runs[0]['summary']
        admission = rig.assert_budget()
        assert [send.phase for send in rig.provider.sends] == ['fast', 'query']
        assert [attempt.phase for attempt in admission.attempts] == ['fast', 'query']
        assert [attempt.status for attempt in admission.attempts] == ['done', 'done']
        assert admission.used == 2
        query = rig.provider.sends[1]
        assert RETAINED in query.prompt and query.reply_data == response(QUERY)
        assert_supplied_answer_facts(query, runs)
        assert rig.app.hub.resources.results.get(rig.record['id'], rig.record['day'])['summary'] == RETAINED
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('failure', ['timeout', 'empty', 'bad-scope'])
def test_unavailable_final_does_not_create_assistant_prose_and_keeps_tool_activity(tmp_path, monkeypatch, failure):
    # Timeout also carries an actual missing-outline error, rather than a fake
    # failure receipt. Empty/scope cases retain a successful actual resource read.
    initial = (response('I will select the requested outline.', actions=[action('select', object='o404')])
               if failure == 'timeout' else None)
    rig = LocalApp(tmp_path, monkeypatch, initial=initial, final=failure,
                   query_empty=failure in ('empty', 'bad-scope'))
    async def scenario():
        result = await rig.ask('Select outline o404.' if failure == 'timeout' else
                               'Read the retained reference note and explain what it establishes.')
        # The real failed select triggers the existing recovery look. Its entered
        # camera send is retained, not hidden to make this a notes-only fixture.
        admission = rig.assert_budget(camera_recovery=failure == 'timeout')
        finals = [send for send in rig.provider.sends if send.phase == 'final']
        assert len(finals) == 1 and 2 <= admission.used <= 3, 'BEHAVIOR: this discriminator must reach the actual final provider boundary'
        if failure == 'timeout':
            assert admission.attempts[-1].status == 'failed', 'BEHAVIOR: failed entered final is charged, never refunded'
            assert any(run['tool'] == 'select' and run['ok'] is False and run['summary'] for run in result['actions'])
            allowed = {RECOVERY}
        else:
            assert any(run['tool'] == 'resources' and run['ok'] is True and RETAINED in run['summary'] for run in result['actions'])
            assert [send.phase for send in rig.provider.sends] == ['fast', 'query', 'final']
            assert [attempt.phase for attempt in admission.attempts] == ['fast', 'query', 'final']
            assert [attempt.status for attempt in admission.attempts] == ['done', 'done', 'done']
            assert admission.used == 3
            query, final = rig.provider.sends[1:]
            assert query.reply_data == response('')
            assert final.reply_data == (response('') if failure == 'empty' else
                                        response(BAD_VISUAL, scope='workspace-pixels'))
            for send in (query, final):
                assert_supplied_answer_facts(send, result['actions'])
            assert result['answer'] == '' and result['answerStatus'] == 'not-published'
            assert rig.assistant_texts() == []
            allowed = set()
        # A previously licensed model answer may survive; an absent answer may be
        # represented as UI status. Neither path permits synthesized assistant text.
        assert result.get('answer') in {None, '', *allowed}, 'MISSING_FEATURE: unavailable interpretation may not become a canned assistant result'
        assert all(text in allowed for text in rig.assistant_texts()), 'MISSING_FEATURE: every displayed assistant result must be actual supported model text'
        history = rig.lines()
        activities = [run for row in history for run in row.get('actions', [])]
        activities += [row for row in history if row.get('type') == 'chat-action']
        assert any(run.get('tool') == result['actions'][0]['tool'] and
                   run.get('summary') == result['actions'][0]['summary'] for run in activities), (
            'BEHAVIOR: no answer must not erase the actual operation/error activity from reload history')
    try:
        asyncio.run(scenario())
    finally:
        rig.close()


class ExternalFileBarrier:
    """Hold one actual retained-file read, then return the original bytes/text."""
    def __init__(self, monkeypatch, target):
        self.entered, self.release = threading.Event(), threading.Event()
        original = Path.read_text

        def read(path, *args, **kwargs):
            if path == target:
                self.entered.set()
                if not self.release.wait(8):
                    raise TimeoutError('synthetic external file barrier was not released')
            return original(path, *args, **kwargs)

        monkeypatch.setattr(Path, 'read_text', read)


async def eventually(predicate, message):
    await until_async(predicate, what=message)


@pytest.mark.parametrize('late', ['valid', 'timeout'])
def test_late_actual_query_uses_only_model_answer_or_operation_status(tmp_path, monkeypatch, late):
    # A useful action-free query answer needs no final send. An empty query
    # leaves interpretation to the reserved final, whose actual provider fails.
    rig = LocalApp(tmp_path, monkeypatch, late=late,
                   correction=LATE if late == 'valid' else None,
                   query_empty=late == 'timeout', final='timeout' if late == 'timeout' else 'valid')
    barrier = ExternalFileBarrier(monkeypatch, rig.file)
    async def scenario():
        request = asyncio.create_task(rig.ask())
        try:
            await eventually(barrier.entered.is_set, 'BEHAVIOR: real resource query must reach the actual retained-file barrier')
            initial = await asyncio.wait_for(asyncio.shield(request), 4)
            assert initial['answer'] == PLAN
            assert initial['actions'][0]['tool'] == 'resources' and initial['actions'][0]['ok'] is None
            assert rig.assistant_texts() == [PLAN]
            ticket = rig.provider.sends[0].ticket
            assert not ticket.closed, 'BEHAVIOR: pending original work keeps its actual Ticket alive'
            barrier.release.set()
            expected_phases = ['fast', 'query'] if late == 'valid' else ['fast', 'query', 'final']
            expected_statuses = ['done', 'done'] if late == 'valid' else ['done', 'done', 'failed']
            await eventually(lambda: len(rig.provider.sends) == len(expected_phases) and ticket.closed,
                             'BEHAVIOR: actual deferred interpretation and original cleanup must finish')
            admission = rig.assert_budget()
            assert admission.used == len(expected_phases)
            assert [send.phase for send in rig.provider.sends] == expected_phases
            assert [attempt.phase for attempt in admission.attempts] == expected_phases
            assert [attempt.status for attempt in admission.attempts] == expected_statuses
            rows = [row for row in rig.lines() if row.get('type') == 'chat-action' and row.get('tool') == 'resources']
            assert rows and any(row.get('ok') is True and RETAINED in row.get('summary', '') for row in rows)
            assert all(row.get('turnId') == initial['turnId'] for row in rows)
            assert rig.assistant_texts() == [PLAN], 'BEHAVIOR: late tool receipt is activity on its original turn, not an inserted assistant post'
            if late == 'valid':
                assert any(row.get('answer') == LATE for row in rows), 'BEHAVIOR: actual late model answer remains intact'
                assert RETAINED in rig.provider.sends[-1].prompt
                assert rig.provider.sends[-1].reply_data == response(LATE)
            else:
                assert admission.attempts[-1].status == 'failed'
                assert rig.provider.sends[1].reply_data == response('')
                assert isinstance(rig.provider.sends[-1].provider_error, asyncio.TimeoutError)
                assert 'Do not replay actions.' in rig.provider.sends[-1].prompt
                assert all(row.get('answer') in (None, '') for row in rows), (
                    'MISSING_FEATURE: failed late interpretation must not attach code-authored answer prose to the tool receipt')
            actual_receipt = next(row for row in rows if row.get('ok') is True and RETAINED in row.get('summary', ''))
            assert len({row.get('actionId') for row in rows}) == 1
            assert actual_receipt['requestId'] == admission.request_id
            for send in rig.provider.sends[1:]:
                facts = decoded_answer_facts(send)
                assert facts['omittedOperations'] == 0 and len(facts['operations']) == 1
                operation = facts['operations'][0]
                assert {key: operation[key] for key in ('requestId', 'actionId', 'tool', 'ok', 'summary')} == {
                    key: actual_receipt[key] for key in ('requestId', 'actionId', 'tool', 'ok', 'summary')}
        finally:
            barrier.release.set()
            await asyncio.gather(request, return_exceptions=True)
    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()
