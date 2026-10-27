"""Additive TEST-only model-owned neighbor discriminators at frozen f1cc.

Actual App/SpokenRequests/Brain/Registry/Ticket for draft delivery; the original
real Agent speculative helper for entered-send chronology. Only external model
replies, delivery and real filesystem IO scheduling are synthetic. A barrier
always delegates its original IO. No synthesis, receipt, completion, admission,
frame freshness, look or successful handler is replaced. No model/browser runs.
"""
import asyncio
import copy
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ACTIVITY_KEYS, CURRENT_RUN
from orchestrator.brain.speech import SpokenRequests
from orchestrator.capture import captures
from orchestrator.ticket import current
from tests.test_brain_dispatch_budget import (
    FACT, FAILURE, QUESTION, TRUTH, admitted, entered_speculative_turn,
    receipt_tools, response as budget_response, world,
)
from tests.test_model_owned_conversation import (
    BAD_VISUAL, CORRECTED, INITIAL, QUERY, RETAINED,
    ExternalFileBarrier, LocalApp, action, eventually, response,
)
from tests.test_spoken_camera_request_selection import ExternalVoice
from tests.waiting import HANG_S


SAVE_PLAN = 'I will save a snapshot of this camera view without overlays.'
SAVE_ANSWER = 'The camera snapshot was saved; its display is not independently verified.'


async def camera_prerequisite(rig):
    """Actual same-camera ingress used by the unchanged model-owned App helper."""
    rig.camera_seq += 1
    await rig.app.hub.source._accept(rig.camera_jpeg, seq=rig.camera_seq,
                                     boot='synthetic-model-owned-camera')
    frame = rig.app.hub.source.latest()
    info = rig.app.hub.latest_info
    assert frame is not None and info is not None and frame.id == info.id
    capture = captures(rig.app.hub).get(frame.id)
    assert capture is not None and capture.jpeg == frame.jpeg == rig.camera_jpeg
    assert capture.seq == rig.camera_seq and capture.boot == frame.boot
    return capture


class SnapshotWriteBarrier:
    """Actual registered snapshot PNG IO in its real CPU worker, never fake save."""
    def __init__(self, monkeypatch, root):
        self.entered, self.release = threading.Event(), threading.Event()
        self.writes = []
        original = Path.write_bytes

        def write(path, data):
            if path.suffix == '.png' and path.name.startswith('snapshot-') and root in path.parents:
                self.writes.append(SimpleNamespace(path=path, ticket=current(), run=CURRENT_RUN.get(),
                    sha256=hashlib.sha256(data).hexdigest(), size=len(data), completed=False))
                self.entered.set()
                if not self.release.wait(8):
                    raise TimeoutError('synthetic snapshot IO boundary was not released')
                written = original(path, data)
                self.writes[-1].completed = True
                return written
            return original(path, data)

        monkeypatch.setattr(Path, 'write_bytes', write)


async def settle_voice(rig, spoken, task):
    """Teardown is after assertions and cannot satisfy a draft/ownership oracle."""
    await asyncio.gather(task, return_exceptions=True)
    spoken.close()
    drafts = tuple(getattr(rig.app.brain, '_drafts', ()))
    if drafts:
        await asyncio.gather(*drafts, return_exceptions=True)


def trace_external_answers(monkeypatch, provider):
    """Observe the unchanged external reply boundary; never assert or reroute it."""
    trace = []
    original = provider.ask

    async def ask(images, prompt, **kwargs):
        ticket = current()
        admission = getattr(ticket, 'admission', None)
        attempts = admission.attempts if admission is not None else ()
        attempt = attempts[-1] if attempts else None
        item = dict(number=len(trace) + 1, ticket=ticket, admission=admission,
            attempt_id=getattr(attempt, 'attempt_id', None),
            phase=getattr(attempt, 'phase', None),
            entry_status=getattr(attempt, 'status', None), prompt=prompt,
            image_count=len(images), image_sha256=tuple(
                hashlib.sha256(image).hexdigest() if isinstance(image, bytes) else None
                for image in images), data=None, exception=None)
        trace.append(item)
        try:
            reply = await original(images, prompt, **kwargs)
            item['data'] = copy.deepcopy(reply.data)
            return reply
        except BaseException as exc:
            item['exception'] = (type(exc).__name__, str(exc))
            raise

    monkeypatch.setattr(provider, 'ask', ask)
    return trace


def test_eligible_model_effect_plan_is_delivered_before_actual_snapshot_io_finishes(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch,
        initial=response(SAVE_PLAN, actions=[action('snapshot', overlays=False)]), correction=SAVE_ANSWER)
    # Its answer is the completion round's over the receipts (defaults: contradicted, unfinished).
    rig.app.hub.settings.answer_check, rig.app.hub.settings.completion = 'every-request', 'every-plan'
    spoken = SpokenRequests(rig.app.brain, rig.app.dialogue, ExternalVoice())
    barrier = SnapshotWriteBarrier(monkeypatch, tmp_path)
    drafts, draft_arrived = [], asyncio.Event()

    async def say(text):
        # Delivery boundary observes only; no assertions can be swallowed by Brain.
        drafts.append((text, barrier.release.is_set(), tuple(rig.provider.sends)))
        draft_arrived.set()

    async def scenario():
        capture = await camera_prerequisite(rig)
        task = asyncio.create_task(spoken.handoff('Save a clean snapshot of this camera view.',
            delegation_id='synthetic-model-plan-before-save', say=say))
        try:
            await eventually(barrier.entered.is_set, 'SETUP: registered snapshot must reach its original PNG write')
            assert len(barrier.writes) == 1
            write = barrier.writes[0]
            assert write.run is not None and write.run.tool == 'snapshot' and write.run.ok is None
            assert write.ticket is rig.provider.sends[0].ticket
            assert write.run.occurrence.request_id == write.ticket.admission.request_id
            assert write.ticket.held(capture.id) is capture and not write.ticket.closed
            assert not write.completed and not write.path.exists() and not barrier.release.is_set()
            await eventually(draft_arrived.is_set,
                'MISSING_FEATURE: eligible model planning text must reach voice before the real effect settles')
            assert [text for text, _released, _sends in drafts] == [SAVE_PLAN]
            assert all(not released for _text, released, _sends in drafts)
            assert len(drafts[0][2]) == 1 and drafts[0][2][0].phase == 'fast'
            assert not write.completed and write.run.ok is None
            barrier.release.set()
            result = await asyncio.wait_for(asyncio.shield(task), 6)
            assert write.completed and write.path.exists()
            assert hashlib.sha256(write.path.read_bytes()).hexdigest() == write.sha256
            assert result['answer'] == SAVE_ANSWER
            assert len(result['actions']) == 1 and result['actions'][0]['tool'] == 'snapshot'
            assert result['actions'][0]['ok'] is True and "saved with today's snapshots" in result['actions'][0]['summary']
            finals = [send for send in rig.provider.sends if send.phase == 'query']  # tool-capable outcome interpretation
            assert len(finals) == 1 and result['actions'][0]['summary'] in finals[0].prompt
            assert rig.assert_budget().used == 2
        finally:
            barrier.release.set()
            await settle_voice(rig, spoken, task)

    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


def test_useful_pure_model_voice_answer_is_delivered_unchanged_without_second_send(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch, initial=response(INITIAL))
    spoken = SpokenRequests(rig.app.brain, rig.app.dialogue, ExternalVoice())
    drafts = []

    async def say(text):
        drafts.append(text)

    async def scenario():
        await camera_prerequisite(rig)
        task = asyncio.create_task(spoken.handoff('What can this retained reference tell me?',
            delegation_id='synthetic-pure-model-answer', say=say))
        try:
            result = await task
            await eventually(lambda: bool(drafts), 'BEHAVIOR: supported pure model answer must reach its actual delivery callback')
            assert drafts == [INITIAL] and result['answer'] == INITIAL
            assert result['actions'] == [] and rig.assert_budget().used == 1
            assert rig.app.conversation.turns[-1].text.endswith(INITIAL)
        finally:
            await settle_voice(rig, spoken, task)

    try:
        asyncio.run(scenario())
    finally:
        rig.close()


def test_query_dependent_plan_is_not_delivered_before_original_retained_file_returns(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch)
    trace = trace_external_answers(monkeypatch, rig.provider)
    spoken = SpokenRequests(rig.app.brain, rig.app.dialogue, ExternalVoice())
    barrier = ExternalFileBarrier(monkeypatch, rig.file)
    drafts = []

    async def say(text):
        drafts.append(text)

    async def scenario():
        await camera_prerequisite(rig)
        task = asyncio.create_task(spoken.handoff('Read the retained reference note and explain what it establishes.',
            delegation_id='synthetic-query-dependent-model-answer', say=say))
        try:
            await eventually(barrier.entered.is_set, 'SETUP: original retained file must be read through the registered query')
            await asyncio.sleep(0.05)  # give the actual scheduled draft callback an opportunity
            assert not drafts and not barrier.release.is_set()
            assert len(rig.provider.sends) == 1 and rig.provider.sends[0].phase == 'fast'
            ticket = rig.provider.sends[0].ticket
            assert not ticket.closed and ticket.admission.used == 1
            barrier.release.set()
            result = await asyncio.wait_for(asyncio.shield(task), 6)
            assert result['answer'] == QUERY and not drafts
            assert len(result['actions']) == 1 and result['actions'][0]['ok'] is True
            assert RETAINED in result['actions'][0]['summary']
            assert any(RETAINED in send.prompt for send in rig.provider.sends if send.phase == 'query')
            budget = rig.assert_budget()
            # Attempt is an immutable entry snapshot. Read actual terminal status
            # from the same admission after return, outside provider callbacks.
            terminal = {attempt.attempt_id: attempt for attempt in budget.attempts}
            diagnostic = [dict(
                **{key: value for key, value in item.items() if key not in ('ticket', 'admission')},
                request_id=getattr(item['admission'], 'request_id', None),
                original_ticket=item['ticket'] is ticket,
                original_admission=item['admission'] is budget,
                terminal_status=getattr(terminal.get(item['attempt_id']), 'status', None))
                for item in trace]
            print('ACTUAL_QUERY_PROVIDER_TRACE', repr(diagnostic))
            assert budget.used == 2, 'BEHAVIOR: reuse the actual eligible completed-query reply without a third send: ' + repr(diagnostic)
            assert [item['phase'] for item in diagnostic] == ['fast', 'query']
            assert all(item['original_ticket'] and item['original_admission'] for item in diagnostic)
            assert all(item['terminal_status'] == 'done' and item['exception'] is None for item in diagnostic)
            assert diagnostic[-1]['data'] == response(QUERY)
            assert not any(send.phase == 'final' for send in rig.provider.sends)
            _before, marker, remainder = diagnostic[-1]['prompt'].partition('<answer-facts source="app" untrusted="true">')
            assert marker, 'SETUP: actual query prompt must contain its bounded producer facts'
            encoded, close, _after = remainder.partition('</answer-facts>')
            assert close
            facts = json.loads(encoded.strip())
            assert facts['operations'] == [{k: v for k, v in a.items() if k not in ACTIVITY_KEYS}
                                           for a in result['actions']]  # what only Activity says is not told
            assert facts['operations'][0]['requestId'] == budget.request_id
            assert facts['operations'][0]['actionId']
            assert facts['inputs'] == {'legacyCameraCropSupplied': False, 'images': []}
            assert facts['targets'] == {'requested': 0, 'outlined': 0}
            assert facts['limitations'] == [] and facts['omittedFacts'] == facts['omittedOperations'] == 0
        finally:
            barrier.release.set()
            await settle_voice(rig, spoken, task)

    try:
        asyncio.run(scenario())
    finally:
        barrier.release.set()
        rig.close()


def test_incompatible_input_declaration_never_delivers_initial_claim_as_voice_draft(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch,
        initial=response(BAD_VISUAL, scope='workspace-pixels'), correction=CORRECTED)
    spoken = SpokenRequests(rig.app.brain, rig.app.dialogue, ExternalVoice())
    drafts = []

    async def say(text):
        drafts.append(text)

    async def scenario():
        await camera_prerequisite(rig)
        task = asyncio.create_task(spoken.handoff('Explain what is visible in the workspace.',
            delegation_id='synthetic-incompatible-input-model-answer', say=say))
        try:
            result = await task
            assert BAD_VISUAL not in drafts and not drafts
            assert result['answer'] == CORRECTED and result['actions'] == []
            assert rig.assert_budget().used == 2
            assert rig.provider.sends[-1].phase == 'final'
            assert 'observation scope' in rig.provider.sends[-1].prompt or 'workspace pixels' in rig.provider.sends[-1].prompt
        finally:
            await settle_voice(rig, spoken, task)

    try:
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('final,fast_error', [
    ('truth', False), ('truth', True), ('timeout', False), ('empty', False),
], ids=['original-fast-look-truth', 'original-failed-fast-look-truth', 'final-timeout', 'final-empty'])
def test_original_speculative_fast_look_trace_reaches_actual_reserved_final(tmp_path, monkeypatch, final, fast_error):
    """Original helper and exact original TRUTH; trace independently, never fix it by routing."""
    contexts, trace = [], []

    async def scenario():
        looking = asyncio.Event()

        async def script(entry):
            request = current()
            attempt = request.admission.attempts[-1] if request and request.admission else None
            item = SimpleNamespace(number=entry.number, ticket=request, admission=entry.admission,
                attempt=attempt, images=tuple(entry.images), prompt=entry.prompt,
                image_sha256=tuple(hashlib.sha256(image).hexdigest() if isinstance(image, bytes) else None
                                   for image in entry.images), branch=None, data=None, exception=None)
            trace.append(item)
            try:
                # Original provider's selection stays exact: fast prompt, then
                # nonempty images, then the real notes-only TRUTH branch.
                if entry.fast:
                    item.branch = 'original-fast'
                    await looking.wait()
                    if fast_error:
                        raise TimeoutError('scripted fast provider timeout')
                    data = {'intent': 'request', 'needs_look': True, 'actions': []}
                elif entry.images:
                    item.branch = 'original-image-plan'
                    looking.set()
                    data = budget_response('I have opened everything.', ('read_receipt', {}), ('missing_picture', {}))
                else:
                    item.branch = 'original-notes-truth'
                    if final == 'timeout':
                        raise TimeoutError('synthetic final provider unavailable')
                    data = budget_response('' if final == 'empty' else TRUTH)
                item.data = copy.deepcopy(data)
                return data
            except BaseException as exc:
                item.exception = (type(exc).__name__, str(exc))
                raise

        brain, _hub, providers, _logged = world(tmp_path, monkeypatch, script,
            fast=True, tools=receipt_tools(contexts))
        result = await entered_speculative_turn(brain, QUESTION)
        # All assertions live outside callbacks, including phase/data fidelity.
        budget = admitted(providers)
        diagnostic = [dict(number=item.number, phase=getattr(item.attempt, 'phase', None),
            attempt_id=getattr(item.attempt, 'attempt_id', None), branch=item.branch,
            image_count=len(item.images), image_sha256=item.image_sha256,
            data=item.data, exception=item.exception, status=sent.status)
            for item, sent in zip(trace, providers.sent)]
        evidence = repr(diagnostic)
        # When the outcome round gives no answer, the answer keeps its own call once (admission.reserve_last_word).
        last_word = final != 'truth'
        assert len(trace) == len(providers.sent) == result['calls'] == (4 if last_word else 3), evidence
        assert {item.attempt.phase for item in trace[:2]} == {'fast', 'look'}
        # A step failed with only the reserved call left: that call is the outcome round over the receipts and the
        # notes (no picture). It says what happened, as the final synthesis did, or adds steps for what is unfinished.
        outcome = trace[2]
        assert outcome.attempt.phase == 'query', 'BEHAVIOR: the original third reserved slot must be the outcome round: ' + evidence
        assert outcome.branch == 'original-notes-truth', evidence
        if last_word:
            assert trace[3].attempt.phase == 'final' and trace[3].branch == 'original-notes-truth', evidence
        assert all(item.ticket is trace[0].ticket and item.admission is budget for item in trace)
        assert len({item.attempt.attempt_id for item in trace}) == len(trace)
        assert all(ctx.admission is budget for ctx in contexts) and len(contexts) == 2
        assert sum(len(item.images) for item in trace) == 1
        look = next(item for item in trace if item.attempt.phase == 'look')
        assert look.branch == 'original-image-plan' and len(look.images) == 1
        assert look.image_sha256[0] is not None and look.images[0].startswith(b'\xff\xd8')
        assert not outcome.images and not outcome.image_sha256
        assert FACT in outcome.prompt and FAILURE in outcome.prompt
        assert [row['tool'] for row in result['actions']] == ['read_receipt', 'missing_picture']
        assert result['actions'][0]['ok'] is True and result['actions'][0]['summary'] == FACT
        assert result['actions'][1]['ok'] is False and FAILURE in result['actions'][1]['summary']
        assert all(row['requestId'] == budget.request_id and row['actionId'] for row in result['actions'])
        assert 'opened everything' not in result['answer']
        if final == 'truth':
            assert trace[-1].data == budget_response(TRUTH) and trace[-1].exception is None
            assert providers.sent[-1].status == budget.attempts[-1].status == 'done'
            assert result['answer'] == TRUTH, 'MISSING_FEATURE: the actual eligible third model reply must survive unchanged: ' + evidence
        else:
            assert result['answer'] in ('', None), 'BEHAVIOR: no eligible final model text means receipts, not invented assistant prose'
            assert not any(turn.role == 'assistant' and turn.text for turn in brain.conversation.turns)
            for item, sent, attempt in zip(trace[2:], providers.sent[2:], budget.attempts[2:]):  # outcome, last word
                if final == 'timeout':
                    assert item.exception[0] == 'TimeoutError' and item.data is None
                    assert sent.status == attempt.status == 'failed'
                else:
                    assert item.data == budget_response('') and item.exception is None
                    assert sent.status == attempt.status == 'done'
        assert sum(attempt.status == 'failed' for attempt in budget.attempts) == \
            int(fast_error) + int(final == 'timeout') * (2 if last_word else 1)

    asyncio.run(asyncio.wait_for(scenario(), HANG_S))
