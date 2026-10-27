"""TEST-only application activity contract, through actual owners.

Dialogue/DayStore/Client/App, actual Ticket close and actual wired late callback.
Direct notice inputs and SDK transport are explicitly synthetic; no model judges,
ToolRuns, Asked objects or completion receipts are fabricated. Callback tests call
the installed App callback, not OutlineCheck's repair body, and certify no repair.
Only the external answering/voice boundaries in existing fixtures are scripted.
No server lifecycle, GPU, physical camera or browser is started. Root executes.
"""
import asyncio
from copy import deepcopy
from datetime import datetime
import json

import pytest
from fastapi.testclient import TestClient

from orchestrator.day import DayStore
from orchestrator.brain.state import Conversation
from orchestrator.dialogue import Dialogue
from orchestrator.hub import Client
from orchestrator.outline_check import OutlineCheck
from orchestrator.paths import Paths
from orchestrator.server import create_app
from orchestrator.ticket import issue
from orchestrator.voice.base import VoiceConfig
from orchestrator.voice.openai_live import OpenAILiveSession
from test_model_owned_announcements import ExternalLiveConnection, ExternalLiveContext
from test_model_owned_conversation import LocalApp
from tests.waiting import HANG_S


SUMMARY = 'Synthetic status: the original camera request changed an outline.'


def notice(dialogue, **fields):
    emit = getattr(dialogue, 'notice', None)
    assert callable(emit), 'MISSING_FEATURE: Dialogue must file genuine app activity outside ToolRun'
    return emit(**fields)


def controls(client):
    out = []
    while not client.control.empty():
        message = client.control.get_nowait()
        out.append(json.loads(message) if isinstance(message, str) else message)
    return out


def assert_activity(row):
    assert row['type'] == 'app-notice', ('BEHAVIOR: app facts remain visibly separate activity', row)
    assert not ({'role', 'answer', 'tool', 'ok', 'turnId', 'requestId', 'actionId', 'noticeId', 'lineId'} & row.keys()), row
    assert isinstance(row['at'], (int, float)) and row['at'] > 0
    json.dumps(row, allow_nan=False)


def here_facts(day, asked):
    # Values come from the actual request origin, never a fixture-built Asked.
    assert getattr(asked, 'day', None) == day, 'MISSING_FEATURE: real Asked must retain its actual day'
    return {'thread': asked.thread, 'generation': asked.generation}


def test_notice_files_original_asked_detaches_nested_facts_and_restarts_without_voice(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    client = Client(None)
    dialogue = Dialogue(day, client.offer)
    asked, original_day = day.here(), day.day
    day.open_thread('Moved on')
    producer = {'kind': 'watch', 'id': 'synthetic-external-watch'}
    facts = {'checks': 3, 'last': {'movingShare': 0.12}, 'observations': ['synthetic observation'], 'known': None}
    expected = deepcopy(facts)
    row = notice(dialogue, producer=producer, event='stopped', summary=SUMMARY,
                 facts=facts, asked=asked, tone='error')
    producer['id'] = 'changed-after-delivery'
    facts['last']['movingShare'] = 1.0
    facts['observations'].append('not supplied')
    assert_activity(row)
    assert row['day'] == original_day and row['thread'] == asked.thread != day.active_id
    assert row['producer'] == {'kind': 'watch', 'id': 'synthetic-external-watch'}
    assert row['summary'] == SUMMARY and row['event'] == 'stopped' and row['tone'] == 'error'
    assert row['facts'] == expected
    assert row['origin'] == {'day': original_day, 'asked': here_facts(original_day, asked)}
    assert controls(client) == [row]
    assert dialogue.history()['lines'] == []
    day.switch(asked.thread)
    assert dialogue.history()['lines'] == [row]
    again = DayStore(root=tmp_path / 'sessions', day=original_day)
    again.switch(asked.thread)
    replay = Dialogue(again, Client(None).offer).history()['lines']
    assert replay == [row], 'BEHAVIOR: journal restart replays the actual activity unchanged'
    assert all(t.role != 'assistant' for thread in again.threads.values() for t in thread.turns)


def test_notice_export_import_keeps_producer_research_origin_and_no_unavailable_ids(tmp_path):
    day = DayStore(root=tmp_path / 'source')
    log = tmp_path / 'conversation.jsonl'
    dialogue = Dialogue(day, Client(None).offer, log)
    origin = day.here()
    rid = 'synthetic-external-researcher'
    day.link_research(rid, origin.thread)
    day.open_thread('Elsewhere')
    row = notice(dialogue, producer={'kind': 'research', 'id': rid}, event='plan',
                 summary='Synthetic research plan is available.', facts={'searches': ['synthetic source']},
                 rid=rid, origin={'day': day.day, 'thread': origin.thread})
    assert_activity(row)
    assert row['thread'] == origin.thread
    assert row['origin'] == {'day': day.day, 'thread': origin.thread, 'researchId': rid}
    assert not {'asked', 'ticketId', 'requestId', 'actionId', 'turnId', 'delegationId'} & row['origin'].keys()
    records = dialogue.today_in_log()
    assert len(records) == 1 and records[0]['type'] == 'app-notice'
    target = DayStore(root=tmp_path / 'imported', day=day.day)
    imported = Dialogue(target, Client(None).offer, log)
    assert imported.import_log(records) == 1, 'MISSING_FEATURE: actual log import recognizes app activity'
    assert imported.history()['lines'] == [row]
    assert imported.import_log(records) == 0, 'BEHAVIOR: existing import guard also covers notices'


def test_status_without_original_request_does_not_fabricate_asked_or_entity_ids(tmp_path):
    day = DayStore(root=tmp_path / 'sessions')
    dialogue = Dialogue(day, Client(None).offer)
    row = notice(dialogue, producer={'kind': 'coach'}, event='focus', summary='Synthetic focus status.',
                 facts={'sharpness': 0.75})
    assert_activity(row)
    assert row['producer'] == {'kind': 'coach'}
    assert row['origin'] == {'day': None}, 'BEHAVIOR: current filing is not proof of an unknown request origin'
    assert row['day'] == day.day and row['thread'] == day.active_id
    assert dialogue.history()['lines'] == [row]


@pytest.mark.parametrize('transition', ['new-session', 'midnight'])
def test_actual_turn_reuse_retains_original_asked_before_and_after_lifecycle_change(tmp_path, transition):
    """Synthetic lifecycle control; no claim that a real Brain await crossed midnight. A new session files the earlier
    request's activity in the session it was asked in (ADR 0017), never in the one open; midnight changes nothing."""
    clock = [datetime(2026, 10, 8, 12).timestamp()]
    day = DayStore(root=tmp_path / 'sessions', clock=lambda: clock[0])
    conversation = Conversation(day)
    original = day.here()
    first = conversation.add_once('user', 'voice', 'Synthetic original request', asked=original)
    day.open_thread('Switched before the handoff')
    reused = conversation.add_once('user', 'voice', 'Synthetic original request and more')
    assert reused is first, 'SETUP: actual add_once must reuse/edit the original turn across a thread switch'
    before = conversation.asked_of(reused)
    assert before.thread == original.thread and before.generation == original.generation
    assert before.day == original.day == '2026-10-08', 'BEHAVIOR: reconstruction retains the known actual request day'
    if transition == 'new-session':
        assert day.new() == '2026-10-08.2'
    else:
        clock[0] += 24 * 60 * 60
        assert day.sid == '2026-10-08' and day.date == '2026-10-09', 'BEHAVIOR: midnight does nothing'
    day.open_thread('Current second conversation')
    after = conversation.asked_of(first)  # actual retained Turn, not a fixture-built origin
    assert after.thread == original.thread and after.generation == original.generation
    assert after.day == original.day and after.session == '2026-10-08'
    row = notice(Dialogue(day, Client(None).offer), producer={'kind': 'request'}, event='stopped',
                 summary='Synthetic earlier request stopped.', facts={}, asked=after)
    assert row['thread'] == original.thread and row['day'] == '2026-10-08'
    assert row['origin'] == {'day': '2026-10-08', 'asked': here_facts('2026-10-08', original)}
    if transition == 'new-session':
        assert day.generation != original.generation
        assert all(not t.lines for t in day.threads.values()), 'BEHAVIOR: nothing of it is filed in the session open'
        assert day.elsewhere('2026-10-08').threads[original.thread].lines == [row]
    else:
        assert day.generation == original.generation and day.threads[original.thread].lines == [row]


def test_known_journal_turn_replays_with_known_day_and_actual_current_load_ownership(tmp_path):
    clock = [datetime(2026, 10, 8, 12).timestamp()]
    day = DayStore(root=tmp_path / 'sessions', clock=lambda: clock[0])
    day.new()  # exercise a created-at generation different from a fresh process's load
    original = day.here()
    turn = Conversation(day).add('user', 'chat', 'Synthetic journal request', asked=original)
    again = DayStore(root=tmp_path / 'sessions', session=day.sid, clock=lambda: clock[0])
    replayed = again.active.turns[0]
    assert replayed is not turn and replayed.id == turn.id
    assert original.generation != again.generation, 'SETUP: journal replay has its real current-load ownership'
    reconstructed = Conversation(again).asked_of(replayed)
    assert reconstructed.day == original.day == '2026-10-08.2'
    assert reconstructed.thread == original.thread and reconstructed.generation == again.generation
    again.open_thread('Elsewhere')
    row = notice(Dialogue(again, Client(None).offer), producer={'kind': 'request'}, event='available',
                 summary='Synthetic journal request status.', facts={}, asked=reconstructed)
    assert row['thread'] == replayed.thread != again.active_id
    assert row['origin']['day'] == '2026-10-08.2'


@pytest.mark.parametrize('transition', ['new-session', 'midnight'])
def test_late_turn_filed_with_stale_asked_never_replays_as_new_same_id_thread(tmp_path, transition):
    """Synthetic lifecycle discriminator, using actual late add/reload/notice paths.

    A late Turn of a request asked before another session opened is written into the journal of the session it was
    asked in, in its own conversation, never into the open session's same-id t1, and a fresh load of either keeps it
    so (ADR 0017). Midnight changes nothing: the late Turn is its own conversation's."""
    clock = [datetime(2026, 10, 8, 12).timestamp()]
    day = DayStore(root=tmp_path / 'sessions', clock=lambda: clock[0])
    conversation = Conversation(day)
    original = day.here()
    conversation.add('user', 'chat', 'Synthetic original request.', asked=original)
    original_session = day.sid
    if transition == 'new-session':
        day.new()
        assert day.generation != original.generation and day.sid != original_session
    else:
        clock[0] += 24 * 60 * 60
        assert day.sid == original_session and day.generation == original.generation
    day.open_thread('Current second conversation')
    late = conversation.add('note', 'system', 'Synthetic late original-request status.', asked=original)
    assert late.thread == original.thread, 'BEHAVIOR: a late add goes to the conversation it was asked in'
    before_reload = conversation.asked_of(late)
    assert before_reload.thread == original.thread and before_reload.generation == original.generation
    assert before_reload.session == original_session
    if transition == 'new-session':
        assert day.threads[original.thread].turns == [], 'BEHAVIOR: the open session\'s t1 is unrelated and empty'

    again = DayStore(root=tmp_path / 'sessions', session=original_session, clock=lambda: clock[0])
    replayed = again.threads[original.thread].turns[-1]
    assert replayed is not late and replayed.id == late.id and replayed.thread == original.thread
    reconstructed = Conversation(again).asked_of(replayed)
    assert reconstructed.thread == original.thread and reconstructed.day == original_session
    dialogue = Dialogue(again, Client(None).offer)
    row = notice(dialogue, producer={'kind': 'request'}, event='stopped',
                 summary='Synthetic late original-request status.', facts={}, asked=reconstructed)
    assert_activity(row)
    assert row['thread'] == original.thread and row['day'] == original_session
    assert row['origin'] == {'day': original_session, 'asked': {'thread': original.thread,
                                                                'generation': reconstructed.generation}}
    if transition == 'new-session':
        opened = DayStore(root=tmp_path / 'sessions', session=day.sid, clock=lambda: clock[0])
        assert all(not t.turns and not t.lines for t in opened.threads.values()), 'BEHAVIOR: nothing healed into it'


def test_unknown_legacy_chat_origin_stays_unknown_through_replay_and_a_new_session(tmp_path):
    clock = [datetime(2026, 10, 8, 12).timestamp()]
    day = DayStore(root=tmp_path / 'sessions', clock=lambda: clock[0])
    conversation = Conversation(day)
    # Explicit synthetic legacy log lacks original Asked/day; current import
    # location is filing evidence, not the origin of these old words.
    assert conversation.restore([{'type': 'chat', 'role': 'user', 'text': 'Synthetic legacy request',
                                  'at': '2026-10-07T12:00:00'}]) == 1
    unknown = conversation.asked_of(day.active.turns[0])
    assert unknown.day is None, 'BEHAVIOR: legacy import must not invent today as the original day'
    again = DayStore(root=tmp_path / 'sessions', day='2026-10-08', clock=lambda: clock[0])
    reader = Conversation(again)
    retained = again.active.turns[0]
    original = reader.asked_of(retained)
    assert original.day is None and original.generation == again.generation
    again.new()
    again.open_thread('Current second conversation')
    after = reader.asked_of(retained)
    assert after.day is None and after.generation == original.generation != again.generation
    row = notice(Dialogue(again, Client(None).offer), producer={'kind': 'request'}, event='stopped',
                 summary='Synthetic legacy request status.', facts={}, asked=after)
    # An origin with no session is filed where the app is (never lost), in the open conversation.
    assert row['thread'] == again.active_id and row['day'] == again.sid
    assert row['origin'] == {'day': None, 'asked': {'thread': original.thread, 'generation': original.generation}}


@pytest.mark.parametrize('transition', ['new-session', 'midnight'])
def test_old_origin_is_retained_but_never_files_into_reused_thread_by_old_asked(tmp_path, transition):
    clock = [datetime(2026, 10, 8, 12).timestamp()]
    day = DayStore(root=tmp_path / 'sessions', clock=lambda: clock[0])
    dialogue = Dialogue(day, Client(None).offer)
    asked, old_session = day.here(), day.sid
    assert old_session == '2026-10-08'
    if transition == 'new-session':
        day.new()
    else:
        clock[0] += 24 * 60 * 60
        assert day.sid == old_session and day.date == '2026-10-09'
    day.open_thread('Current second conversation')
    row = notice(dialogue, producer={'kind': 'held', 'id': 'synthetic-external-clip'}, event='released',
                 summary='Synthetic held footage was released.', facts={'frames': 0, 'released': True}, asked=asked)
    assert_activity(row)
    assert row['day'] == old_session and row['thread'] == asked.thread
    assert row['origin'] == {'day': old_session, 'asked': here_facts(old_session, asked)}
    if transition == 'new-session':
        assert not any(t.lines for t in day.threads.values()), 'BEHAVIOR: old origin does not address new same-ID thread'
        assert dialogue.history()['lines'] == []
        assert day.elsewhere(old_session).threads[asked.thread].lines == [row]
    else:
        assert day.threads[asked.thread].lines == [row] and day.active_id != asked.thread


def test_actual_run_ids_survive_notice_as_origin_without_turn_or_tool_receipt(tmp_path, monkeypatch):
    rig = LocalApp(tmp_path, monkeypatch)
    completed = []
    rig.app.brain.registry.finished.append(lambda run, ctx: completed.append((run, ctx)))
    try:
        result = asyncio.run(rig.ask())
        run, ctx = next((run, ctx) for run, ctx in completed if run.tool == 'resources')
        assert run.ok and run.occurrence is not None
        assert ctx.asked is not None and run.occurrence.request_id == rig.provider.sends[0].admission.request_id
        model_texts = rig.assistant_texts()
        row = notice(rig.app.dialogue, producer={'kind': 'resource-read', 'id': rig.record['id']},
                     event='available', summary='Synthetic retained source is available.',
                     facts={'resourceId': rig.record['id'], 'read': run.ok}, asked=ctx.asked,
                     origin={'requestId': run.occurrence.request_id, 'actionId': run.occurrence.action_id,
                             'turnId': ctx.turn_id})
        assert_activity(row)
        assert row['origin'] == {'day': rig.app.day.day, 'asked': here_facts(rig.app.day.day, ctx.asked),
                                'requestId': run.occurrence.request_id, 'actionId': run.occurrence.action_id,
                                'turnId': result['turnId']}
        assert rig.assistant_texts() == model_texts, 'BEHAVIOR: actual model replies are neither replaced nor manufactured'
        assert not rig.app.hub.screen._accepted, 'SETUP: actual camera path has no accepted workspace'
    finally:
        rig.close()


@pytest.fixture
def wired(tmp_path):
    # No lifespan/context manager: actual ASGI endpoints without starting runtime servers.
    server = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    app = server.state.app
    client = Client(None)
    app.hub.clients.add(client)
    http = TestClient(server)
    checker = next(callback.__self__ for callback in app.hub.scene.on_change
                   if isinstance(getattr(callback, '__self__', None), OutlineCheck))
    try:
        yield app, client, checker, http
    finally:
        http.close()
        app.hub.clients.discard(client)
        app.hub._analysis.shutdown(wait=True)
        app.hub._background.shutdown(wait=True)


@pytest.mark.parametrize('channel', ['chat', 'voice'])
def test_real_app_late_callback_persists_activity_and_memory_in_original_conversation(wired, channel):
    app, client, checker, http = wired

    async def scenario():
        request = issue(app.hub, 'turn', asked=app.day.here(), channel=channel)
        asked, original_day = request.asked, app.day.day
        app.day.open_thread('Moved on before late status')
        controls(client)
        try:
            checker.tell(request, SUMMARY)  # installed producer callback; no core callback is replaced
            task = app.narrator._task
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), HANG_S)
            emitted = controls(client)
            rows = [row for row in emitted if row.get('type') == 'app-notice']
            assert len(rows) == 1, ('MISSING_FEATURE: App late status persists even with no voice', emitted)
            row = rows[0]
            assert_activity(row)
            assert row['producer'] == {'kind': 'outline-check'}
            assert row['event'] == 'repaired'
            assert row['summary'] == SUMMARY and row['thread'] == asked.thread
            assert row['origin']['day'] == original_day
            assert row['origin']['asked'] == here_facts(original_day, asked)
            assert row['origin']['ticketId'] == request.id
            assert row['origin']['requestId'] == request.admission.request_id
            assert 'actionId' not in row['origin'], 'BEHAVIOR: ticket alone does not invent an occurrence'
            assert not [line for line in emitted if line.get('role') == 'assistant']
            assert any(turn.role == 'note' and SUMMARY in turn.text for turn in app.day.threads[asked.thread].turns)
            assert all(turn.role != 'assistant' for turn in app.day.threads[asked.thread].turns)
            assert app.dialogue.history()['lines'] == []
            app.day.switch(asked.thread)
            assert app.dialogue.history()['lines'] == [row]
            assert http.get('/api/day').json()['active'] == asked.thread
            assert not app.hub.screen._accepted
        finally:
            request.seal()
    asyncio.run(scenario())


@pytest.mark.parametrize('channel', ['chat', 'voice'])
def test_real_ticket_close_reports_unused_choice_once_as_app_activity(wired, channel):
    app, client, _checker, _http = wired

    async def scenario():
        request = issue(app.hub, 'turn', asked=app.day.here(), channel=channel,
                        choices={'outline': {'implementation': 'sam2.1-tiny', 'label': 'SAM 2.1 tiny', 'used': False}})
        asked = request.asked
        app.day.open_thread('Another conversation')
        controls(client)
        request.seal()  # actual Ticket -> UNUSED_CHOICE -> installed App callback
        request.seal()  # actual close guard, not hand-called callbacks
        task = app.narrator._task
        if task is not None:
            await asyncio.wait_for(asyncio.shield(task), HANG_S)
        emitted = controls(client)
        rows = [row for row in emitted if row.get('type') == 'app-notice']
        assert request.closed and len(rows) == 1, ('MISSING_FEATURE: genuine unused choice has one durable notice', emitted)
        row = rows[0]
        assert_activity(row)
        assert row['thread'] == asked.thread and row['origin']['ticketId'] == request.id
        assert row['origin']['requestId'] == request.admission.request_id
        assert row['event'] == 'unused-choice'
        assert row['facts']['operation'] == 'outline' and row['facts']['implementation'] == 'sam2.1-tiny'
        assert row['facts']['used'] is False
        assert not [line for line in emitted if line.get('role') == 'assistant']
        assert any(turn.role == 'note' and 'was chosen' in turn.text for turn in app.day.threads[asked.thread].turns)
        app.day.switch(asked.thread)
        assert app.dialogue.history()['lines'] == [row]
    asyncio.run(scenario())


@pytest.mark.parametrize('producer', ['late', 'unused-choice'])
def test_unavailable_original_voice_never_delivers_old_notice_to_replacement(wired, producer):
    app, client, checker, _http = wired

    async def scenario():
        original_connection, replacement_connection = ExternalLiveConnection(), ExternalLiveConnection()
        original = OpenAILiveSession(VoiceConfig(model='gpt-live-1'),
                                    connect=lambda: ExternalLiveContext(original_connection))
        replacement = OpenAILiveSession(VoiceConfig(model='gpt-live-1'),
                                       connect=lambda: ExternalLiveContext(replacement_connection))
        request = None
        try:
            await original.start()
            app.brain.voice = original
            choices = {'outline': {'implementation': 'sam2.1-tiny', 'used': False}} if producer == 'unused-choice' else None
            request = issue(app.hub, 'turn', asked=app.day.here(), channel='voice', choices=choices)
            await original.close()
            await replacement.start()
            app.brain.voice = replacement
            controls(client)
            if producer == 'late':
                checker.tell(request, SUMMARY)
            else:
                request.seal()
            task = app.narrator._task  # passive observation of existing queue owner
            if task is not None:
                await asyncio.wait_for(asyncio.shield(task), HANG_S)
            rows = [row for row in controls(client) if row.get('type') == 'app-notice']
            assert len(rows) == 1, 'MISSING_FEATURE: missing original voice does not lose factual activity'
            assert not replacement_connection.messages('thinking') and not replacement_connection.messages('commentary'), (
                'BEHAVIOR: no unavailable original-owner notice can borrow the replacement session', replacement_connection.sent)
            assert any(turn.role == 'note' for turn in app.day.active.turns)
            assert all(turn.role != 'assistant' for turn in app.day.active.turns)
        finally:
            if request is not None:
                request.seal()
            app.brain.voice = None
            await original.close()
            await replacement.close()
    asyncio.run(scenario())
