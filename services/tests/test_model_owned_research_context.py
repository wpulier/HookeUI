"""Additive TEST: long real research history stays out of bounded model context.

Actual App/Brain/Registry/ResearchManager/WorkerAttempt/Client/Screen/Conversation
and the unchanged a86c external camera/provider/worker/SDK/renderer seams. Eighty
questions are accepted worker protocol events: the manager allocates every q-ID,
run/origin and terminal status. No Session, receipt, note or completion is built.
Root alone executes after composing the shared Notice API/original-day metadata.
Scripted worker/model text is synthetic; no remote-model or browser proof.
"""
import asyncio
import pytest
import copy
import json

from test_model_owned_conversation import action
from test_model_owned_research_activity import ResearchApp, SOURCE, finding


QUESTION_COUNT = 80
# Existing Narrator body limit, not a larger allowance or a new model budget.
CONTEXT_LIMIT = 24576
LATEST = 'SYNTHETIC_CURRENT_COMPLETION_FACT_RETAIN_ME'
LIMITATION = 'Synthetic latest limitation: the measurement remains uncertain.'


def wrapped_body(text, tag):
    """Observe actual producer JSON inside its existing untrusted context wrapper."""
    opening = text.find('<' + tag + ' ')
    if opening < 0:
        return None
    beginning = text.find('\n', opening)
    end = text.find('\n</' + tag + '>', beginning)
    return text[beginning + 1:end] if beginning >= 0 and end >= 0 else None


def whole_payload(body, label, failures):
    try:
        payload = json.loads(body)
    except (TypeError, ValueError) as exc:
        failures.append(f'{label}: producer projection is not whole JSON ({type(exc).__name__})')
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get('facts'), dict):
        failures.append(f'{label}: event/facts projection is absent')
        return None
    if len(body) > CONTEXT_LIMIT:
        failures.append(f'{label}: {len(body)} characters exceed the existing {CONTEXT_LIMIT} context bound')
    return payload


def check_projection(payload, label, session, job, canonical, failures, *, completed=False):
    if payload is None:
        return
    facts = payload['facts']
    run = facts.get('run') or {}
    if (facts.get('researchId') != session.rid or facts.get('day') != job.day
            or facts.get('thread') != job.origin['thread']
            or run.get('runId') != job.attempt.id or run.get('run') != job.facts['run']):
        failures.append(f'{label}: original researcher/day/thread/current producing run is lost')
    if completed:
        result = facts.get('result') or {}
        if (facts.get('status') != 'done' or result.get('summary') != LATEST
                or result.get('sources') != [SOURCE] or result.get('limitations') != [LIMITATION]):
            failures.append(f'{label}: latest result/source/limitations/terminal status is lost')
    requests = facts.get('requests')
    omitted = facts.get('omitted') or {}
    if not isinstance(requests, list):
        failures.append(f'{label}: retained optional requests must be a list, possibly empty')
        return
    count = omitted.get('requests')
    if (not isinstance(count, int) or isinstance(count, bool) or count <= 0
            or count + len(requests) != len(canonical)):
        failures.append(f'{label}: exact omitted optional-request count is absent or incorrect')
    for retained in requests:
        if not isinstance(retained, dict):
            failures.append(f'{label}: optional question is not a whole factual entry')
            continue
        original = next((r for r in canonical if r['id'] == retained.get('id')), None)
        if original is None or retained != original:
            failures.append(f'{label}: an optional question is cut, altered or has no canonical identity')
    if len({r.get('id') for r in requests if isinstance(r, dict)}) != len(requests):
        failures.append(f'{label}: optional question identities are duplicated')
    ref = facts.get('read_result') or {}
    if (ref.get('tool') != 'read_result' or (ref.get('args') or {}).get('researcher') != session.rid):
        failures.append(f'{label}: actionable canonical read_result reference is absent')
    # Current-question data remains complete, ahead of optional archived history.
    if payload.get('event') == 'request':
        question = facts.get('request') or {}
        original = next((r for r in canonical if r['id'] == question.get('id')), None)
        # Compare against that actual event's asked snapshot, not its later
        # terminal unanswered projection.
        if original is None or question != original:
            failures.append(f'{label}: the event current question loses actual q-ID/reason/run facts')


@pytest.mark.usefixtures('research_key')  # starts a real-model researcher, which needs its model's key
def test_latest_completion_survives_long_actual_question_history_in_voice_and_next_typed_prompt(tmp_path, monkeypatch):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            assert callable(getattr(rig.app.dialogue, 'notice', None)), (
                'SETUP: Root must compose the frozen shared Notice API before this exact gate')
            original_voice, connection = await rig.voice()
            session, job, _ctx = await rig.start(channel='voice')
            for number in range(1, QUESTION_COUNT + 1):
                what = f'Synthetic archived field {number:02d}: '.ljust(160, 'W')
                why = f'Synthetic archived evidence reason {number:02d}: '.ljust(240, 'Y')
                rig.worker.emit(job, 'request', what=what, why=why, seconds=15)
            canonical_asked = copy.deepcopy(session.requests)
            assert len(canonical_asked) == QUESTION_COUNT
            assert [r['id'] for r in canonical_asked] == [f'q{i}' for i in range(1, QUESTION_COUNT + 1)]
            assert all(r['status'] == 'asked' and r['runId'] == job.attempt.id
                       and r['origin'] == job.origin and len(r['what']) == 160 and len(r['why']) == 240
                       for r in canonical_asked), 'SETUP: real worker events must retain manager-owned question facts'
            result = finding(LATEST)  # canned external worker result, not remote-model authorship
            result['limitations'] = [LIMITATION]
            rig.worker.emit(job, 'done', status='done', result=result, spent=0.01,
                            response_id=f'synthetic-long-history-response-{job.attempt.id}')
            assert job.attempt.completion.result()['result'] == result
            assert session.result == result and session.result_run['runId'] == job.attempt.id
            canonical = copy.deepcopy(session.requests)
            assert all(r['status'] == 'unanswered' for r in canonical)
            assert [{k: v for k, v in r.items() if k != 'status'} for r in canonical] == [
                {k: v for k, v in r.items() if k != 'status'} for r in canonical_asked]
            row = await rig.published(session, lambda r: r['status'] == 'done')
            assert row['requests'] == canonical and row['result'] == result
            assert rig.saved(session)['requests'] == canonical
            read_back = next(s for s in rig.app.research.read_day(job.day, kept_only=False) if s.rid == session.rid)
            assert read_back.requests == canonical and read_back.result == result
            assert rig.app.research.get(session.rid).wire()['requests'] == canonical
            assert session.rid not in rig.app.research._watchdogs
            assert session.rid not in rig.app.research.spending._held and session.spent == 0.01
            await rig.settle_delivery()
            assert rig.app.brain.voice is original_voice and not original_voice.closed

            # Read actual voice SDK input, never synthesize a completion callback.
            completed_voice = []
            for record in connection.messages('thinking') + connection.messages('commentary'):
                body = wrapped_body(record.args['content'], 'voice-context')
                if body is not None and '"completed"' in body[:100]:
                    completed_voice.append(body)
            assert len(completed_voice) == 1, (
                'SETUP: exactly one eligible original completion input must reach actual SDK', len(completed_voice))
            notes = [(turn, wrapped_body(turn.text, 'researchers')) for turn in rig.app.brain.conversation.window()
                     if turn.role == 'note' and 'source="research lifecycle"' in turn.text.split('\n')[0]]
            assert notes and all(body is not None for _turn, body in notes)
            typed_at = len(rig.provider.sends)
            await rig.ask('Explain the latest research finding and what it cannot settle.')
            entered = rig.provider.sends[typed_at:]
            first_typed = entered[0].prompt
            assert all(body in first_typed for _turn, body in notes), (
                'SETUP: observed actual retained notes must enter the next admitted typed provider prompt')

            # The public reader still supplies useful canonical completion facts.
            # Evidence q-IDs are manager questions, not read_result's lesson q1/q2.
            read_at = len(rig.provider.sends)
            await rig.ask('Read the retained current research finding.',
                          [action('read_result', researcher=session.rid)])
            assert any(LATEST in send.prompt and SOURCE in send.prompt and LIMITATION in send.prompt
                       for send in rig.provider.sends[read_at:]), 'SETUP: actual canonical read_result utility is lost'
            assert session.requests == canonical and rig.saved(session)['requests'] == canonical
            rig.assert_no_app_assistant()

            # Collect both path failures before asserting; one RED node diagnoses
            # voice truncation and typed repetition without masking either by setup.
            failures = []
            voice_payload = whole_payload(completed_voice[0], 'original voice completion', failures)
            check_projection(voice_payload, 'original voice completion', session, job, canonical, failures, completed=True)
            completed_typed = []
            total = 0
            for index, (_turn, body) in enumerate(notes, 1):
                total += len(body)
                payload = whole_payload(body, f'typed note {index}', failures)
                if payload is not None:
                    if payload.get('event') == 'completed':
                        completed_typed.append(payload)
                    # Notes for earlier request events have only questions accepted
                    # up to that actual event, rather than the later total of eighty.
                    question = payload['facts'].get('request', {})
                    number = next((n for n, r in enumerate(canonical_asked, 1) if r['id'] == question.get('id')), None)
                    available = canonical_asked[:number] if payload.get('event') == 'request' and number is not None else canonical
                    check_projection(payload, f'typed note {index}', session, job, available, failures,
                                     completed=payload.get('event') == 'completed')
            if not completed_typed or not all(value in first_typed for value in (LATEST, SOURCE, LIMITATION)):
                failures.append('next typed provider input loses latest completion/source/limitations')
            if total > len(notes) * CONTEXT_LIMIT:
                failures.append(f'next typed provider repeats {total} characters of unbounded lifecycle projections')
            assert not failures, ('MISSING_FEATURE: bounded whole current research facts must precede optional history', failures)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()
