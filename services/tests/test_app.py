"""The app as wired (app.py), what the chat shows and where (dialogue.py), and the one way a
day starts afresh. Before, replies, late results and narrated results could be filed under
whichever conversation was open, and the day was reset three different ways."""
import pytest
import asyncio
import json
import re
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from orchestrator.app import App
from orchestrator.brain.narrator import Line, Narrator
from orchestrator.brain.registry import Tool, schema
from orchestrator.brain.state import Conversation
from orchestrator.commands import COMMANDS
from orchestrator.day import DayStore
from orchestrator.dialogue import Dialogue
from orchestrator.paths import Paths
from orchestrator.scene import SceneObject
from orchestrator.server import create_app
from tests.fakes import announcer
from tests.test_brain import make

STUDIO = Path(__file__).resolve().parents[2] / "apps" / "studio" / "src"


def app(tmp_path) -> App:
    return App.build("http://127.0.0.1:1", Paths.under(tmp_path), use_gpu=False)


def test_a_directly_built_app_defaults_to_cellpose_without_an_edition_override(tmp_path, monkeypatch):
    monkeypatch.delenv("MICROMIND_EDITION", raising=False)
    from orchestrator import operations

    monkeypatch.setitem(operations.INSTALLED, "micro-sam", lambda: "")  # its own Python is this machine's install
    built = app(tmp_path)
    assert built.settings_wire()["settings"]["cells"] == "cellpose-sam"
    assert built.hub.settings.update({"cells": "micro-sam-lm", "outline": "micro-sam-lm"}) == []
    assert built.settings_wire()["settings"]["cells"] == "micro-sam-lm"
    assert built.settings_wire()["settings"]["outline"] == "micro-sam-lm"


def test_a_model_chosen_from_now_on_is_kept_for_the_day_and_said_on_a_restart(tmp_path, monkeypatch):
    """The user's Q15 (Slice 5): the Models menu or set_default (one helper) keeps the day's choice; a restart applies
    it, checked as runnable, says it in the conversation and hands it to the preload hook (defaults.RESTORED)."""
    from orchestrator import defaults
    from orchestrator.commands import change_settings

    first = app(tmp_path)
    assert change_settings(first, {"outline": "sam2.1-tiny"}, by="user") == []
    hooked = []
    monkeypatch.setattr(defaults, "RESTORED", [lambda hub, restored: hooked.append(restored)])
    again = app(tmp_path)  # a restart
    assert again.hub.settings.outline == "sam2.1-tiny"
    notes = [t.text for t in again.conversation.turns if t.role == "note"]
    assert any(n.startswith("Outlining still uses SAM 2.1 tiny (on this Mac), as you asked at ") for n in notes)
    assert hooked and hooked[0][0]["group"] == "outline" and hooked[0][0]["problem"] == ""


def test_the_app_builds_and_a_studio_is_sent_its_state_in_order(tmp_path):
    built = app(tmp_path)
    kinds = [m["type"] for m in built.snapshot()]
    assert kinds == ["hello", "settings", "capabilities", "scale", "coach", "arena", "scene", "layer", "tracks-layer", "magnifier", "notes", "presenter", "slidemap", "session", "dataset", "rig", "research-list",
                     "day", "thread", "execution-list"]
    json.dumps(built.snapshot())  # all of it goes over the wire


def test_every_command_the_studio_sends_has_a_handler():
    sent = set()
    for path in STUDIO.glob("*.ts"):
        if path.name.endswith(".test.ts"):
            continue  # tests send unknown commands on purpose (to check the type check refuses them)
        text = path.read_text()
        sent |= set(re.findall(r"send\(\{ ?type: '([a-z-]+)'", text)) | set(re.findall(r"request\('([a-z-]+)'", text))
    assert sent and sent <= set(COMMANDS), sent - set(COMMANDS)


# --- which conversation a line belongs to ----------------------------------------------------
def brain_over_a_day(tmp_path, replies, tools=()):
    brain, hub, agent = make(replies, tools)
    day = DayStore(root=tmp_path / "sessions")
    brain.conversation = agent.conversation = Conversation(store=day)
    shown = []
    return brain, hub, day, Dialogue(day, shown.append), shown


def test_an_answer_goes_to_the_conversation_it_was_asked_in(tmp_path):
    brain, hub, day, dialogue, shown = brain_over_a_day(tmp_path, [{"answer": "A stoma.", "targets": [], "actions": []}])
    slow = brain.agent.look

    async def think_while_the_user_switches(*a, **k):
        day.open_thread("Something else")  # the user starts another conversation while it thinks
        return await slow(*a, **k)

    brain.agent.look = think_while_the_user_switches
    asked = brain.conversation.here()
    result = asyncio.run(brain.turn("what is that oval?", "chat", asked=asked))
    dialogue.show({"type": "chat", "role": "assistant", "text": result["answer"]}, asked=result["asked"])
    assert day.active_id == "t2"
    assert [(t.role, t.text) for t in day.threads["t1"].turns] == [("user", "what is that oval?"), ("assistant", "A stoma.")]
    assert day.threads["t2"].turns == [] and shown[-1]["thread"] == "t1"


def test_a_late_tool_result_goes_to_the_conversation_it_was_asked_in(tmp_path):
    async def slow(ctx, args):
        await asyncio.sleep(0.2)
        return "dissected it into 3 parts"

    brain, hub, day, dialogue, shown = brain_over_a_day(
        tmp_path, [{"answer": "Dissecting it.", "targets": [], "actions": [{"tool": "dissect", "args": {}}]}],
        [Tool("dissect", "", schema({}), slow, inline_ms=20)])
    a = announcer(tmp_path, research=None, day=day)
    a.announcer.dialogue = dialogue
    brain.report_late = a.announcer.late_tool

    async def go():
        await brain.turn("break it down", "chat")
        day.open_thread()  # the user moved on before it finished
        await asyncio.sleep(0.4)

    asyncio.run(go())
    assert any(t.role == "note" and "dissect finished" in t.text for t in day.threads["t1"].turns)
    late = [line for line in shown if line["type"] == "chat-action"]
    assert late and late[0]["thread"] == "t1" and late[0]["summary"] == "dissected it into 3 parts"


@pytest.mark.usefixtures('research_key')  # starts a real-model researcher, which needs its model's key
def test_a_researchers_result_goes_where_it_was_started_and_the_open_one_gets_a_line(tmp_path, monkeypatch):
    # The original result stays discoverable in research activity; it is not an
    # app-authored assistant duplicate in the currently open conversation.
    # Actual manager/worker lifecycle over synthetic external transport/pixels.
    from test_model_owned_research_activity import ResearchApp, FIRST, SOURCE, action, eventually

    rig = ResearchApp(tmp_path, monkeypatch)

    async def scenario():
        try:
            session, job, ctx = await rig.start()
            origin = ctx.asked
            newer = rig.app.day.open_thread("Onion")
            filename, pixels = rig.worker.artifact(job)
            rig.worker.finish(job)
            completion = job.attempt.completion.result()
            assert completion["run"] == job.facts and completion["result"]["summary"] == FIRST
            assert completion["result"]["sources"] == [SOURCE]
            await rig.published(session, lambda row: row["status"] == "done")
            await eventually(lambda: any(row.get("type") == "app-notice" and
                row.get("event") == "presentation" and row.get("producer", {}).get("id") == session.rid
                for row in rig.renderer.messages), "SETUP: actual completion must publish its viewer status")
            await rig.settle_delivery()
            notices = [row for row in rig.renderer.messages if row.get("type") == "app-notice"
                       and row.get("producer") == {"kind": "research", "id": session.rid}]
            completed = [row for row in notices if row["event"] == "completed"]
            assert len(completed) == 1
            row = completed[0]
            assert row["thread"] == origin.thread and row["tone"] == "info"
            assert row["origin"]["asked"] == {"thread": origin.thread, "generation": origin.generation}
            assert row["origin"]["turnId"] == ctx.turn_id
            assert row["origin"]["requestId"] == ctx.admission.request_id
            assert row["origin"]["actionId"] == rig.finished[0][0].occurrence.action_id
            assert row["facts"]["run"] == completion["run"]
            assert row["facts"]["result"] == completion["result"]
            assert row["facts"]["status"] == "done" and row["facts"]["day"] == session.day
            assert all(notice["thread"] == origin.thread for notice in notices)
            presentation = next(notice for notice in notices if notice["event"] == "presentation")
            assert presentation["facts"]["presentation"] == {"researchId": session.rid,
                "report": "retained in Researchers; original browser unavailable", "contentVerified": False}
            assert not rig.app.presenter.open  # this legacy fixture has no accepted browser owner
            assert not newer.lines and not newer.turns
            assert rig.app.dialogue.history()["thread"] == newer.id and rig.app.dialogue.history()["lines"] == []
            assert any(turn.role == "note" and FIRST in turn.text
                       for turn in rig.app.day.threads[origin.thread].turns)
            assert session.result["summary"] == FIRST and session.result_run["runId"] == job.attempt.id
            assert len(rig.worker.jobs) == 1 and session.rid not in rig.app.research.spending._held
            saved = rig.saved(session)
            assert saved["result"] == completion["result"] and saved["resultRun"]["runId"] == job.attempt.id
            output = session.outputs[0]
            assert output["path"] == filename and output["version"] == 1
            assert (Path(job.payload["dir"]) / filename).read_bytes() == pixels
            entry = rig.app.hub.resources.get(session.rid, day=session.day)
            assert entry.day == session.day and entry.thread == origin.thread
            assert entry.source["pictures"] == [{"researcher": session.rid,
                "output": output["id"], "version": output["version"], "path": filename}]
            assert rig.app.hub.resources.picture(session.rid, day=session.day)[0] == pixels
            assert FIRST in rig.app.hub.resources.read(session.rid, session.day)
            archived = next(item for item in rig.app.research.read_day(session.day, kept_only=False)
                            if item.rid == session.rid)
            assert archived.result == completion["result"] and archived.result_run["runId"] == job.attempt.id
            rig.assert_frozen_result_publications(session, job.attempt.id, FIRST)
            rig.app.day.switch(origin.thread)
            read_at = len(rig.provider.sends)
            answered = await rig.ask("Read the original research result.",
                [action("read_result", researcher=session.name)])
            assert any(FIRST in send.prompt and SOURCE in send.prompt for send in rig.provider.sends[read_at:])
            assert answered["answer"] == rig.provider.sends[-1].reply["answer"]
            rig.assert_no_app_assistant()
            assert not newer.lines and not newer.turns
            replay = rig.app.dialogue.history()["lines"]
            assert [line for line in replay if line.get("type") == "app-notice"] == notices
            restarted = DayStore(root=rig.app.day.root, day=session.day)
            restarted.switch(origin.thread)
            assert Dialogue(restarted, lambda _line: None).history()["lines"] == replay
        finally:
            await rig.close()

    try:
        asyncio.run(scenario())
    finally:
        rig.local.close()


def test_actual_late_activity_survives_busy_voice_replacement_in_original_conversation(tmp_path, monkeypatch):
    # Synthetic external provider/SDK/camera/file inputs over the real App and
    # registered resources producer. Reuse its actual owners and barriers; no
    # fabricated Line, Ticket, ToolRun, receipt or narrator chat fallback.
    import time

    from orchestrator.voice.base import VoiceConfig
    from orchestrator.voice.openai_live import OpenAILiveSession
    from test_model_owned_announcements import (
        AnnouncementApp, DELEGATION, ExternalLiveConnection, ExternalLiveContext,
        LATE, RETAINED, eventually,
    )

    rig = AnnouncementApp(tmp_path, monkeypatch, late="valid")
    connection = ExternalLiveConnection()
    replacement = OpenAILiveSession(VoiceConfig(model="gpt-live-1"),
        connect=lambda: ExternalLiveContext(connection))

    async def scenario():
        try:
            began = time.time()  # real external clock; no producer clock patch
            await rig.start(busy=True)
            initial_send = rig.rig.provider.sends[0]
            assert rig.initial["answer"] == initial_send.reply_data["answer"]
            ticket, occurrence = rig.ticket, rig.run.occurrence
            await rig.finish_read()
            await eventually(lambda: bool(rig.app.narrator.waiting),
                             "SETUP: real late result must queue while original voice is busy")
            assert rig.voice.idle_since() == 0 and not ticket.closed
            queued = list(rig.app.narrator.waiting)  # passive observation, not queue insertion
            assert len(queued) == 1 and queued[0].provenance == "data"
            line = queued[0]
            assert line.voice_owner is rig.voice and line.asked == rig.origin
            assert (line.turn_id, line.request_id, line.action_id, line.delegation_id) == (
                rig.initial["turnId"], occurrence.request_id, occurrence.action_id, DELEGATION)
            opening = '<answer-facts source="app" untrusted="true">\n'
            assert line.text.count(opening) == 1
            facts = json.loads(line.text.split(opening, 1)[1].split('\n</answer-facts>', 1)[0])
            assert len(facts["operations"]) == 1
            raw = facts["operations"][0]
            assert raw["tool"] == "resources" and raw["ok"] is True
            assert raw["requestId"] == occurrence.request_id and raw["actionId"] == occurrence.action_id
            assert RETAINED in raw["summary"] and rig.run.answer is None
            raw_rows = rig.assert_original_activity()
            assert all(row.get("answer") in (None, "") for row in raw_rows)
            assert not rig.connection.messages("commentary")

            newer = rig.app.day.open_thread("Another conversation before queued late delivery")
            await rig.voice.close()
            rig.connection.busy_stop.set()
            await replacement.start()
            rig.app.brain.voice = replacement  # public route-owned installation field
            assert rig.voice.closed and not replacement.closed
            assert rig.run.occurrence is occurrence and not rig.late.release.is_set()
            admission = await rig.settle()
            assert [attempt.phase for attempt in admission.attempts] == ["fast", "query"]
            assert all(attempt.status == "done" for attempt in admission.attempts)
            late_send = next(send for send in rig.rig.provider.sends
                             if send.admission is admission and send.phase == "query")
            assert late_send.reply_data["answer"] == LATE and RETAINED in late_send.prompt
            assert rig.run.occurrence is occurrence and rig.run.answer is None
            rows = rig.assert_original_activity()
            assert all(row["thread"] == rig.origin.thread for row in rows)
            assert any(row.get("answer") == late_send.reply_data["answer"] for row in rows)
            assert any(row.get("answer") in (None, "") for row in rows)
            rig.assert_no_generated_assistant(model_answers=(late_send.reply_data["answer"],))
            assert not newer.lines and not newer.turns
            assert not rig.connection.messages("commentary")
            assert not connection.messages("commentary") and not connection.messages("thinking")
            assert any(how == "unavailable" and text == line.text
                       for _at, how, text in rig.app.narrator.delivered)
            assert rig.app.dialogue.history()["thread"] == newer.id
            assert rig.app.dialogue.history()["lines"] == []

            # Observe actual Client JSON, export and the original journal owner.
            wire = []
            while not rig.rig.client.control.empty():
                wire.append(json.loads(rig.rig.client.control.get_nowait()))
            def original_receipt(row):
                return (row.get("type") == "chat-action" and
                        row.get("requestId") == occurrence.request_id and
                        row.get("actionId") == occurrence.action_id)
            assert [row for row in wire if original_receipt(row)] == rows
            exported = [json.loads(raw) for raw in rig.app.dialogue.log.read_text().splitlines()]
            read_at = time.time()
            exported_rows = [row for row in exported if original_receipt(row)]
            assert len(exported_rows) == len(rows)
            for saved, row in zip(exported_rows, rows):
                # Recorded event time now travels with wire and journal. Export time
                # remains separate; a restart must not retime the actual receipt.
                assert "day" not in row
                assert began <= row["at"] <= read_at
                assert saved == {**row, "day": rig.app.day.day, "loggedAt": saved["loggedAt"]}
            journal = [event["line"] for event in rig.app.day.journal.load()
                       if event.get("type") == "line" and original_receipt(event["line"])]
            assert journal == rows
            assert not any(row.get("role") == "assistant" and
                           row.get("text") not in (initial_send.reply_data["answer"], late_send.reply_data["answer"])
                           for row in wire + exported)
            rig.app.day.switch(rig.origin.thread)
            replay = rig.app.dialogue.history()["lines"]
            assert replay == rig.original_rows()
            restarted = DayStore(root=rig.app.day.root, day=rig.app.day.day)
            restarted.switch(rig.origin.thread)
            assert Dialogue(restarted, lambda _row: None).history()["lines"] == replay
        finally:
            await replacement.close()
            await rig.close()

    try:
        asyncio.run(scenario())
    finally:
        rig.rig.close()


def test_an_answer_asked_in_another_session_is_filed_there_never_in_a_conversation_that_only_shares_its_id(tmp_path):
    day = DayStore(root=tmp_path / "sessions")
    first = day.sid
    asked = day.here()
    day.open_thread("second")
    day.new()  # another session: it has a t1 too, but not the one asked in
    day.open_thread("new second")
    turn = day.add_turn("assistant", "chat", "the late answer", asked=asked)
    assert turn.thread == "t1" and not any(t.text == "the late answer" for th in day.threads.values() for t in th.turns)
    kept = DayStore(root=tmp_path / "sessions", session=first)
    assert [t.text for t in kept.threads["t1"].turns] == ["the late answer"]
    day.switch_session(first)  # back in the session it was asked in: its conversation is the one asked in again
    assert day.thread_for(asked) == "t1"


# --- what the chat showed survives a reload and a restart ------------------------------------------
def test_the_replay_is_what_was_shown_in_that_conversation(tmp_path):
    day = DayStore(root=tmp_path / "sessions")
    dialogue = Dialogue(day, lambda line: None, tmp_path / "conversation.jsonl")
    dialogue.show({"type": "chat", "role": "user", "text": "what is this?"})
    dialogue.show({"type": "chat", "role": "assistant", "text": "A stoma.", "model": "Gemini", "thinkMs": 900, "turnId": 2})
    dialogue.show({"type": "chat-action", "turnId": 2, "tool": "zoom", "ok": True, "summary": "zoomed"})
    day.open_thread("Onion")
    dialogue.show({"type": "chat", "role": "user", "text": "and this?"})
    again = Dialogue(DayStore(root=tmp_path / "sessions"), lambda line: None)
    assert [line["text"] for line in again.history()["lines"]] == ["and this?"]
    again.day.switch("t1")
    replay = again.history()["lines"]
    assert [line.get("text") or line["summary"] for line in replay] == ["what is this?", "A stoma.", "zoomed"]
    assert replay[1]["model"] == "Gemini" and all(line["thread"] == "t1" for line in replay)
    log = [json.loads(line) for line in (tmp_path / "conversation.jsonl").read_text().splitlines()]
    assert [r["thread"] for r in log] == ["t1", "t1", "t1", "t2"] and all(r["at"] and r["day"] for r in log)


def test_todays_chat_log_is_imported_once_into_a_day_without_lines(tmp_path):
    day = DayStore(root=tmp_path / "sessions")
    log = tmp_path / "conversation.jsonl"
    stamp = f"{day.day}T10:00:00"
    log.write_text("\n".join(json.dumps(r) for r in [
        {"at": stamp, "type": "chat", "role": "user", "text": "old line"},
        {"at": stamp, "type": "session-cleared"},
        {"at": stamp, "type": "chat", "role": "user", "text": "kept", "thread": "t1"},
        {"at": "2026-01-01T10:00:00", "type": "chat", "role": "user", "text": "another day"}]) + "\n")
    dialogue = Dialogue(day, lambda line: None, log)
    assert dialogue.import_log(dialogue.today_in_log()) == 1
    assert dialogue.import_log(dialogue.today_in_log()) == 0
    assert [line["text"] for line in Dialogue(DayStore(root=tmp_path / "sessions"), None).history()["lines"]] == ["kept"]


# --- one way another session clears the view --------------------------------------------------
def outline(built):
    return built.hub.scene.add(SceneObject(id=built.hub.scene.new_id(), kind="highlight", polygons=[[[1, 1], [9, 1], [5, 9]]],
                                           anchor_frame=1, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label="stoma"))


def test_a_new_session_and_a_switch_clear_the_same_things_and_midnight_clears_nothing(tmp_path):
    for how in ("new", "switch", "midnight"):
        built = app(tmp_path / how)
        first = built.day.sid
        if how == "switch":
            built.new_session()
        sent = []
        built.hub.broadcast = built.subjects.publish = sent.append
        built.subjects.name("lucerne stem")
        obj = outline(built)
        built.magnifier.zoom(obj.id)
        built.conversation.add("user", "chat", "what is this?")
        if how == "midnight":
            now = built.day.clock() + 24 * 3600
            built.day.clock = lambda: now
            built.conversation.add("user", "chat", "and after midnight?")
            assert built.hub.scene.objects and built.magnifier.open and built.subjects.subject == "lucerne stem"
            assert built.day.sid == first and [t.text for t in built.day.active.turns if t.role == "user"] == [
                "what is this?", "and after midnight?"]
            continue
        if how == "new":
            built.new_session()
        else:
            built.switch_session(first)
        assert not built.hub.scene.objects and not built.magnifier.open and not built.presenter.open, how
        assert built.subjects.subject == "" and "specimen" not in built.hub.scene.context, how
        assert built.day.active.turns == [] and list(built.day.threads) == ["t1"], how
        kinds = [m["type"] for m in sent]
        assert {"research-list", "notes", "day", "thread", "session", "session-cleared"} <= set(kinds), (how, kinds)


def test_a_new_session_by_its_endpoint_needs_the_studios_own_header(tmp_path):
    client = TestClient(create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path)))
    first = client.get("/api/sessions").json()["current"]
    for path in ("/api/session/new", "/api/day/archive"):  # the old "start today afresh" is its other name
        assert client.post(path).status_code == 403
        assert client.post(path, headers={"X-MicroscopeMind": "new-session", "Origin": "https://example.org"}
                           ).status_code == 403
    done = client.post("/api/session/new", headers={"X-MicroscopeMind": "new-session"}, json={"name": "Kidney"})
    assert done.status_code == 200 and done.json()["ok"] and done.json()["previous"] == first
    assert done.json()["name"] == "Kidney" and done.json()["session"] != first
    again = client.post("/api/day/archive", headers={"X-MicroscopeMind": "clear-today"})
    assert again.status_code == 200 and again.json()["previous"] == done.json()["session"]
    listed = client.get("/api/sessions").json()
    assert listed["current"] == again.json()["session"] and len(listed["sessions"]) == 3


def test_back_to_another_conversation_and_a_question_is_answered_from_it(tmp_path):
    brain, hub, day, dialogue, shown = brain_over_a_day(tmp_path, [
        {"answer": "Going back to it.", "targets": [], "actions": [{"tool": "switch_thread", "args": {"thread": "blood smear"}}]},
        {"answer": "We concluded the platelet looked green to you.", "targets": [], "actions": []}])
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.ticket import current

    entered = []
    actual_ask = brain.agent.fast_model.ask

    async def observe_provider(images, prompt, **kwargs):
        ticket = current()
        entry = {"ticket": ticket, "admission": ticket.admission,
                 "attempt": ticket.admission.attempts[-1], "images": tuple(images),
                 "prompt": prompt, "reply": None, "error": None}
        entered.append(entry)
        try:
            reply = await actual_ask(images, prompt, **kwargs)
        except BaseException as exc:
            entry["error"] = (type(exc), str(exc))
            raise
        entry["reply"] = reply.data
        return reply

    brain.agent.fast_model.ask = observe_provider  # delegate the existing external model boundary

    brain.services = Services(hub=hub, agent=brain.agent, day=day)
    brain.registry = build(brain.services)
    brain.conversation.add("user", "voice", "is that a platelet? it looks green")
    brain.conversation.add("assistant", "voice", "Yes, a platelet; the green is the stain.")
    day.set_subject("blood smear", "look")
    day.open_thread("Onion")
    brain.agent.fast_model.rewrite = {"answer": "We concluded the platelet looked green to you."}
    result = asyncio.run(brain.turn("back to the blood smear, what did we conclude?", "chat"))
    assert [entry["attempt"].phase for entry in entered] == ["fast", "final"]
    initial, final = entered
    budget = initial["admission"]
    assert final["ticket"] is initial["ticket"] and final["admission"] is budget
    assert budget.used == result["calls"] == 3
    assert [attempt.phase for attempt in budget.attempts] == ["fast", "look", "final"]
    assert [attempt.status for attempt in budget.attempts] == ["failed", "done", "done"]
    assert initial["error"] == (asyncio.TimeoutError, "no model for this prompt in the test")
    assert initial["reply"] is None and final["error"] is None
    assert len(brain.agent.calls) == 1  # the failed fast attempt is charged, never refunded for a second look
    assert final["images"] == () and "back to the blood smear, what did we conclude?" in final["prompt"]
    opening = '<answer-facts source="app" untrusted="true">'
    assert final["prompt"].count(opening) == 1
    facts = json.loads(final["prompt"].split(opening, 1)[1].split('</answer-facts>', 1)[0])
    # The model is told each step's wire less what only Activity says (its models; registry.ACTIVITY_KEYS).
    from orchestrator.brain.registry import ACTIVITY_KEYS

    told = [{k: v for k, v in a.items() if k not in ACTIVITY_KEYS} for a in result["actions"]]
    assert facts["operations"] == told and len(result["actions"]) == 1
    receipt = result["actions"][0]
    assert receipt["tool"] == "switch_thread" and receipt["ok"] is True
    assert receipt["requestId"] == budget.request_id and receipt["actionId"]
    assert "(t1," in receipt["summary"]
    assert "is that a platelet? it looks green" in receipt["summary"]
    assert "Yes, a platelet; the green is the stain." in receipt["summary"]
    assert facts["inputs"] == {"legacyCameraCropSupplied": False, "images": []}
    assert facts["omittedFacts"] == facts["omittedOperations"] == 0 and facts["limitations"] == []
    assert final["reply"] == {"answer": "We concluded the platelet looked green to you."}
    assert result["answer"] == final["reply"]["answer"] and result["answerStatus"] == "model"
    assert day.active_id == "t1" and result["asked"].thread == "t1"
    assert result["asked"].generation == day.generation
    assert [t.text for t in day.threads["t1"].turns][-2:] == ["back to the blood smear, what did we conclude?",
                                                              "We concluded the platelet looked green to you."]
    assert not any(t.role == "assistant" for t in day.threads["t2"].turns)


def test_a_set_cap_and_who_leads_survive_a_new_session_and_midnight(tmp_path):
    clock = {"now": 1_790_000_000.0}
    day = DayStore(root=tmp_path / "sessions", clock=lambda: clock["now"])
    day.set_pref("researchCap", 10.0)
    day.set_pref("userLeads", True)
    heard = []
    day.listeners.append(lambda e: heard.append(e["type"]))
    day.add_turn("user", "chat", "hello")
    day.new()
    assert heard[-1] == "session-opened" and heard.count("session-opened") == 1 and "thread-opened" not in heard
    assert DayStore.resume(root=tmp_path / "sessions", clock=lambda: clock["now"]).prefs == {"researchCap": 10.0,
                                                                                            "userLeads": True}
    clock["now"] += 86_400
    assert DayStore.resume(root=tmp_path / "sessions", clock=lambda: clock["now"]).prefs["researchCap"] == 10.0


def test_the_agent_reads_exactly_what_the_capabilities_page_shows(tmp_path):
    from orchestrator.brain.registry import ToolContext

    built = app(tmp_path)
    built.verdicts.record("track-motion", "broken", "lost the fast ones")

    async def go(args):
        run = built.brain.registry.start("capabilities", args, ToolContext("voice", 1, 7, "what can you do?"))
        await run.task
        return run

    whole = asyncio.run(go({}))
    page = built.capabilities_wire()
    assert whole.ok and whole.summary == page["agentText"]  # one list, two readers (ADR 0007)
    assert "checked: BROKEN (lost the fast ones)" in whole.summary and page["pending"] == 1
    only = asyncio.run(go({"actor": "researcher"}))
    assert "Make a video" in only.summary and "Highlight what it sees" not in only.summary


def test_capability_previews_are_served_by_bare_name_and_known_kind_only(tmp_path, monkeypatch):
    from orchestrator import capabilities
    MEDIA = tmp_path / 'synthetic-media'
    MEDIA.mkdir()
    (MEDIA / 'fixture.jpg').write_bytes(b'synthetic HTTP transport fixture')
    monkeypatch.setattr(capabilities, 'MEDIA', MEDIA)

    client = TestClient(create_app("http://127.0.0.1:1", use_gpu=False, paths=Paths.under(tmp_path)))
    shown = next(p.name for p in MEDIA.iterdir() if p.suffix in (".mp4", ".jpg"))
    ok = client.get(f"/api/capabilities/media/{shown}")
    assert ok.status_code == 200 and ok.headers["x-content-type-options"] == "nosniff"
    for bad in ("..%2F..%2Fcontract%2Fcapabilities.json", "capabilities.json", "missing.mp4", "zoom.html"):
        assert client.get(f"/api/capabilities/media/{bad}").status_code == 404, bad
    assert client.get("/api/capabilities").json()["type"] == "capabilities"


def test_look_deeper_asks_about_the_carded_outline_not_the_newest_of_its_name(tmp_path):
    """Look deeper (notes.ts) sends its outline with the ask: it becomes the selection, so "this one" is
    that outline even when a newer one has the same name, or when the user clicked elsewhere."""
    import pytest

    from orchestrator.brain.registry import ToolContext
    from orchestrator.tools import ToolError

    built = app(tmp_path)
    older, newer = outline(built), outline(built)  # two "stoma" outlines; the older one's card is open
    seen = {}

    async def turn(text, channel, **kwargs):
        seen["text"], seen["selected"] = text, built.hub.scene.selected
        seen["resolved"] = built.board.resolve("this one", ToolContext("chat", 1, None, text)).id
        return {"answer": "Looking closer.", "objectIds": [], "turnId": 1, "actions": [], "thinkMs": 0, "totalMs": 0,
                "model": "test", "asked": kwargs.get("asked")}

    built.brain.turn = turn
    built.hub.broadcast = lambda *_: None
    asyncio.run(COMMANDS["ask"](built, None, {"type": "ask", "text": "Look into this one more closely", "objectId": older.id}))
    assert seen["selected"] == older.id and seen["resolved"] == older.id != newer.id
    with pytest.raises(ToolError) as gone:
        asyncio.run(COMMANDS["ask"](built, None, {"type": "ask", "text": "Look into this one more closely", "objectId": "o999"}))
    assert gone.value.next  # says what to do instead


def test_an_outline_the_check_redrew_is_said_where_its_request_was_made(tmp_path):
    """Synthetic installed-callback plumbing, not an accepted repair or real Ticket proof."""
    from orchestrator.outline_check import OutlineCheck

    built = app(tmp_path)
    checker = next(f.__self__ for f in built.hub.scene.on_change if isinstance(getattr(f, "__self__", None), OutlineCheck))
    asked = built.day.here()
    built.day.open_thread("Something else")  # the user moved on before the check finished
    summary = "Redrew the stoma outline: the first one was on something else."
    checker.tell(SimpleNamespace(asked=asked, channel="chat"), summary)
    line = built.day.threads[asked.thread].lines[-1]
    assert type(line["at"]) in (int, float)
    assert line == {"type": "app-notice", "day": built.day.day, "at": line["at"],
        "producer": {"kind": "outline-check"}, "event": "repaired", "summary": summary,
        "tone": "info", "facts": {}, "thread": asked.thread,
        "origin": {"day": asked.day, "asked": {"thread": asked.thread, "generation": asked.generation}}}
    assert any(t.role == "note" and t.text == summary for t in built.day.threads[asked.thread].turns)
    assert not built.day.threads["t2"].lines
    # The same path says a model chosen for a request that nothing in it used (contract 7).
    from orchestrator import ticket as tickets

    choice = {"operation": "outline", "implementation": "sam2.1-tiny", "label": "SAM 2.1 tiny", "used": False}
    tickets.UNUSED_CHOICE[0](SimpleNamespace(id="r1", asked=asked, channel="chat"), [choice])
    unused = built.day.threads[asked.thread].lines[-1]
    assert unused["type"] == "app-notice" and unused["thread"] == asked.thread and unused["tone"] == "info"
    assert unused["producer"] == {"kind": "model-choice", "id": "sam2.1-tiny"}
    assert unused["event"] == "unused-choice" and unused["facts"] == choice
    assert unused["summary"] == "SAM 2.1 tiny was chosen for that request, but nothing in it was outlining, so it was not used"
    assert unused["origin"] == {"day": asked.day, "asked": {"thread": asked.thread, "generation": asked.generation},
                                "ticketId": "r1"}  # supplied synthetic callback argument, not a real receipt
    # An absent original voice owner still preserves activity, never assistant
    # prose or borrowed speech. The actual narrator callback remains installed.
    lines = len(built.day.threads[asked.thread].lines)
    voice_summary = "Redrew the stoma outline: the old one was on something else."
    checker.tell(SimpleNamespace(asked=asked, channel="voice"), voice_summary)
    voiced = built.day.threads[asked.thread].lines[-1]
    assert voiced == {**line, "at": voiced["at"], "summary": voice_summary}
    assert len(built.day.threads[asked.thread].lines) == lines + 1
    assert built.day.threads[asked.thread].turns[-1].role == "note"
    assert not built.narrator.waiting and built.narrator._task is None
    assert not built.day.threads["t2"].lines and not built.day.threads["t2"].turns
    assert all(t.role != "assistant" for t in built.day.threads[asked.thread].turns)
    assert all(row.get("role") != "assistant" and "text" not in row for row in built.day.threads[asked.thread].lines)
