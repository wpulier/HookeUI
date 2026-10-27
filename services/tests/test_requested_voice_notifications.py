"""Actual requested notifications, without application-authored assistant text.

App/Brain/Registry/ResearchManager/WorkerAttempt/Held/Announcer/Narrator and
OpenAILiveSession are real. Existing external camera, worker, renderer, answering
provider and SDK fixtures are explicitly synthetic. Assertions inspect entered
SDK requests, not hosted-model interpretation or browser/audio acceptance.
No Line, Session, receipt, notification callback or core method is fabricated.
Root alone executes this additive precursor; production remains frozen.
"""
import asyncio
import copy
import json

from test_model_owned_announcements import logical_context_messages

import pytest

from test_application_background_activity import BackgroundApp, DELEGATION
from test_model_owned_conversation import eventually
from test_model_owned_research_activity import FIRST, PLAN, QUESTION, SOURCE, ResearchApp

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')


def sdk_rows(connection, start, *, contains):
    """Read the external SDK's detached recorded inputs; no response is manufactured."""
    return [row for row in logical_context_messages(connection.sent[start:]) if row.complete
            and row.kind in ('thinking', 'commentary')
            and contains in row.args.get('content', '')]


def research_notices(rig, rid, event):
    return [row for row in rig.renderer.messages if row.get('type') == 'app-notice'
            and row.get('producer') == {'kind': 'research', 'id': rid}
            and row.get('event') == event]


def no_app_assistant(rig):
    """Notification *inputs* are not assistant speech; actual chat remains model-owned."""
    allowed = {send.reply['answer'] for send in rig.provider.sends if send.reply.get('answer')}
    rows = [row for row in rig.renderer.messages if row.get('type') == 'chat'
            and row.get('role') == 'assistant']
    rows += [row for thread in rig.app.day.threads.values() for row in thread.lines
             if row.get('type') == 'chat' and row.get('role') == 'assistant']
    assert all(row.get('text') in allowed for row in rows), rows


async def completed_delivery(rig, session, job):
    await rig.published(session, lambda row: row['status'] == session.status)
    await eventually(lambda: bool(research_notices(rig, session.rid, 'completed')),
                     'SETUP: actual installed completion listener must publish original activity')
    await rig.settle_delivery()
    notice, = research_notices(rig, session.rid, 'completed')
    assert notice['facts']['run']['runId'] == job.attempt.id
    assert notice['origin']['delegationId'] == job.origin['delegation_id']
    assert notice['origin']['requestId'] == job.origin['request_id']
    assert notice['origin']['actionId'] == job.origin['action_id']
    assert notice['thread'] == job.origin['thread']
    assert session.rid not in rig.app.research.spending._held
    assert session.rid not in rig.app.research._watchdogs
    return notice


def assert_notification(rows, ctx, *, run_id):
    requested = [row for row in rows if row.kind == 'commentary']
    assert len(requested) == 1, (
        'MISSING_FEATURE: requested original facts need one model response event, not only silent context',
        [(row.kind, row.args) for row in rows])
    row = requested[0]
    assert row.args.get('delegation_id') == ctx.delegation_id
    body = row.args['content']
    assert run_id in body and ctx.admission.request_id in body
    assert 'Context only, do not read aloud:' not in body
    return body


@pytest.mark.parametrize('terminal', ['done', 'interrupted', 'timeout-with-output'])
def test_original_requested_research_terminal_facts_request_model_speech(tmp_path, monkeypatch, terminal):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            voice, connection = await rig.voice()
            session, job, ctx = await rig.start(channel='voice')
            used = ctx.admission.used
            await rig.settle_delivery()
            before = len(connection.sent)  # initial start reply cannot satisfy a late notification
            artifact = rig.worker.artifact(job) if terminal == 'timeout-with-output' else None
            rig.worker.finish(job, status='timeout' if artifact else terminal)
            receipt = copy.deepcopy(job.attempt.completion.result())
            notice = await completed_delivery(rig, session, job)
            assert rig.app.brain.voice is voice and not voice.closed
            assert job.attempt.completion.result() == receipt
            assert notice['facts']['status'] == receipt['status']
            assert notice['facts']['cause'] == receipt['cause']
            if terminal == 'done':
                assert receipt['result']['summary'] == FIRST
                assert notice['facts']['result']['sources'] == [SOURCE]
            elif terminal == 'interrupted':
                assert receipt['cause'] == 'interrupted' and receipt['result'] is None
                assert not notice['facts']['outputs']
            else:
                assert receipt['status'] == 'partial' and receipt['cause'] == 'timeout'
                assert notice['facts']['outputs'][0]['path'] == artifact[0]
                assert receipt['result']['partial'] is True
            no_app_assistant(rig)
            assert ctx.admission.used == used <= 3, 'notification must not create another Brain answer attempt'
            rows = sdk_rows(connection, before, contains=job.attempt.id)
            body = assert_notification(rows, ctx, run_id=job.attempt.id)
            assert 'completed' in body and session.rid in body
            if terminal == 'done':
                assert FIRST in body and SOURCE in body and 'unanswered' in body
            else:
                assert receipt['cause'] in body
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


def test_requested_evidence_question_uses_original_response_event_and_actual_question(tmp_path, monkeypatch):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            _voice, connection = await rig.voice()
            session, job, ctx = await rig.start(channel='voice')
            await rig.settle_delivery()
            before, used = len(connection.sent), ctx.admission.used
            question = rig.worker.question(job)
            assert question['status'] == 'asked' and question['runId'] == job.attempt.id
            await eventually(lambda: bool(research_notices(rig, session.rid, 'request')),
                             'SETUP: actual worker question must publish its notice')
            await rig.settle_delivery()
            notice, = research_notices(rig, session.rid, 'request')
            assert notice['facts']['request'] == question
            assert not job.attempt.completion.done(), 'a question is not completion'
            no_app_assistant(rig)
            assert ctx.admission.used == used <= 3
            body = assert_notification(sdk_rows(connection, before, contains=job.attempt.id),
                                       ctx, run_id=job.attempt.id)
            assert question['id'] in body and QUESTION['what'] in body and QUESTION['why'] in body
            assert question['origin']['action_id'] in body
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


def test_requested_completion_waits_on_real_busy_original_voice_before_response_event(tmp_path, monkeypatch):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            voice, connection = await rig.voice(busy=True)
            session, job, ctx = await rig.start(channel='voice')
            before, used = len(connection.sent), ctx.admission.used
            rig.worker.finish(job)
            await eventually(lambda: bool(research_notices(rig, session.rid, 'completed')),
                             'SETUP: requested completion must reach actual listener while A is busy')
            await eventually(lambda: any(job.attempt.id in line.text for line in rig.app.narrator.waiting),
                             'SETUP: original requested completion must remain in actual queue during SDK audio')
            queued, = [line for line in rig.app.narrator.waiting if job.attempt.id in line.text]
            assert queued.voice_owner is voice and queued.asked == ctx.asked
            assert queued.delegation_id == ctx.delegation_id and queued.provenance == 'data'
            assert voice.idle_since() == 0
            assert not sdk_rows(connection, before, contains=job.attempt.id)
            await completed_delivery(rig, session, job)  # existing helper stops external busy audio
            no_app_assistant(rig)
            assert ctx.admission.used == used <= 3
            assert_notification(sdk_rows(connection, before, contains=job.attempt.id),
                                ctx, run_id=job.attempt.id)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


@pytest.mark.parametrize('retirement', ['closed', 'replacement'])
def test_retired_original_completion_never_requests_speech_on_new_voice(tmp_path, monkeypatch, retirement):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            voice, connection = await rig.voice()
            session, job, ctx = await rig.start(channel='voice')
            await rig.settle_delivery()
            await voice.close()
            targets = [(connection, len(connection.sent))]
            if retirement == 'replacement':
                replacement, replacement_connection = await rig.voice()
                assert rig.app.brain.voice is replacement and replacement is not voice
                targets.append((replacement_connection, len(replacement_connection.sent)))
            used = ctx.admission.used
            rig.worker.finish(job)
            notice = await completed_delivery(rig, session, job)
            assert notice['facts']['result']['summary'] == FIRST
            no_app_assistant(rig)
            assert ctx.admission.used == used <= 3
            assert all(not sdk_rows(target, start, contains=job.attempt.id) for target, start in targets)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


def test_actual_research_plan_context_remains_silent_without_notification_intent(tmp_path, monkeypatch):
    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            _voice, connection = await rig.voice()
            session, job, ctx = await rig.start(channel='voice')
            await rig.settle_delivery()
            before, used = len(connection.sent), ctx.admission.used
            rig.worker.plan(job)
            await eventually(lambda: bool(research_notices(rig, session.rid, 'plan')),
                             'SETUP: actual installed plan listener must publish data')
            await rig.settle_delivery()
            rows = sdk_rows(connection, before, contains=job.attempt.id)
            assert rows and all(row.kind == 'thinking' for row in rows)
            assert PLAN['searches'][0] in rows[0].args['content']
            assert not connection.messages('commentary')[sum(row.kind == 'commentary'
                                                             for row in connection.sent[:before]):]
            assert not job.attempt.completion.done()
            no_app_assistant(rig)
            assert ctx.admission.used == used <= 3
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


def test_actual_early_held_stop_requests_model_update_with_original_count_and_cause(tmp_path, monkeypatch):
    rig = BackgroundApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            _voice, connection = await rig.voice()
            held, clip, session, run, ctx = await rig.hold(channel='voice')
            await rig.settle()
            before, saved = len(connection.sent), rig.snapshot()
            used = ctx.admission.used
            held.tick(clip.started_at + clip.seconds + 2)  # actual held clock boundary; no terminal callback stub
            await rig.settle()
            row = rig.assert_notice(rig.added(saved), 'held', clip.id, ctx, 'stopped',
                                    original_day=rig.app.day.day)
            assert clip.status == 'stopped' and not clip.cut_short and not clip.frames
            assert row['facts']['frames'] == 0 and row['facts']['span'] == 0
            assert row['facts']['why'] == clip.why and row['facts']['request'] == clip.request
            assert row['origin']['delegationId'] == ctx.delegation_id == DELEGATION
            assert row['origin']['requestId'] == run.occurrence.request_id
            assert row['origin']['actionId'] == run.occurrence.action_id
            assert ctx.admission.used == used <= 3
            # A hold's own existing clip ID is the operation identity; no synthetic run is made.
            rows = sdk_rows(connection, before, contains=clip.id)
            responses = [r for r in rows if r.kind == 'commentary']
            assert len(responses) == 1, (
                'MISSING_FEATURE: requested early hold failure needs an original model response event',
                [(r.kind, r.args) for r in rows])
            assert responses[0].args.get('delegation_id') == DELEGATION
            body = responses[0].args['content']
            assert session.rid in body and clip.request in body and clip.why in body
            assert run.occurrence.request_id in body and run.occurrence.action_id in body
            assert 'Context only, do not read aloud:' not in body
            json.dumps(row, allow_nan=False)
        finally:
            await rig.close()
    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()
