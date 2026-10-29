"""Sessions in the app (ADR 0017: a session is the project, not the day), through App.build over its own folder: the
`session` and `sessions` wires the studio draws (the header chip, its menu, the sessions rail) and the session list
agree; the studio's command starts, opens and names sessions; the one the app is in survives a restart; work asked
in one session is filed there and never shown in another; the brain's tools open one, list several or none; a session
is named from what the user names; and midnight does nothing."""
from __future__ import annotations

import asyncio
import json

import pytest

from orchestrator.app import App
from orchestrator.day import DayStore
from orchestrator.commands import COMMANDS, handle, run_tool
from orchestrator.paths import Paths
from tests.waiting import HANG_S, until, until_async

SESSION_KEYS = {"type", "id", "label", "started", "specimen", "name", "named", "lastActive", "days", "counts", "suggest"}
ROW_KEYS = {"id", "name", "named", "label", "started", "lastActive", "days", "counts", "specimens", "working"}  # and "thumb" when it has a picture


def build(root) -> App:
    return App.build("http://127.0.0.1:1", Paths.under(root), use_gpu=False)


def listen(built) -> list[dict]:
    sent: list[dict] = []
    built.hub.broadcast = built.subjects.publish = sent.append
    return sent


@pytest.mark.parametrize('in_flight', [False, True], ids=['pending-timer', 'in-flight-broadcast'])
def test_stopping_the_app_retires_its_session_updates(tmp_path, monkeypatch, in_flight):
    built = build(tmp_path)
    started, finished = asyncio.Event(), asyncio.Event()
    calls = []

    async def waiting_message():
        calls.append(True)
        started.set()
        try:
            await asyncio.Future()
        finally:
            finished.set()

    monkeypatch.setattr(built, 'sessions_message', waiting_message)

    async def lifecycle():
        built.sessions_changed(at_once=True)
        if in_flight:
            await asyncio.wait_for(started.wait(), HANG_S)
        await built.stop()
        assert finished.is_set() if in_flight else not calls
        count = len(calls)
        built.sessions_changed(at_once=True)
        await asyncio.sleep(0.01)
        assert len(calls) == count, 'session updates after stop cannot revive background work'

    asyncio.run(lifecycle())


def test_subject_change_only_constructs_background_work_on_a_running_loop(tmp_path, monkeypatch):
    """Synchronous session edits have no scheduler; live edits still warm and notify."""
    built = build(tmp_path)
    created, finished = [], []

    def work(kind, *args):
        async def complete():
            finished.append((kind, args))
        coroutine = complete()
        created.append(coroutine)
        return coroutine

    monkeypatch.setattr(built.agent.zeroshot, 'warm', lambda: work('warm'))
    monkeypatch.setattr(built.brain, 'note_to_voice', lambda text: work('voice', text))

    async def live_change():
        built.subjects.source_changed('leaf epidermis')
        await asyncio.sleep(0)

    # Establish the same absence of a current loop as after a command finishes.
    asyncio.run(asyncio.sleep(0))
    try:
        built.subjects.source_changed('blood smear')
        assert built.subjects.subject == 'blood smear'
        assert created == [], 'no coroutine should be created without a scheduler'
        asyncio.run(live_change())
        assert [kind for kind, _ in finished] == ['warm', 'voice']
        assert 'leaf epidermis' in finished[1][1][0]
    finally:
        for coroutine in created:
            coroutine.close()


def command(built, **msg) -> dict:
    return asyncio.run(COMMANDS["session"](built, None, {"type": "session", **msg}))


class Client:
    def __init__(self):
        self.replies = []

    def offer(self, message):
        self.replies.append(json.loads(message))


def work(built) -> None:
    """What a session holds, filed as its producers file it: two populations, motion tracks, a result Made in this
    session lists and two it does not (a measurement is an answer; a clearer picture is a source, ADR 0022), a
    bookmark, a researcher, a recording opened beside the live camera and a second conversation."""
    day = built.day
    day.record_evidence("e1", "evidence/e1", "t1", 0, ["c1", "c2"])
    day.record_evidence("e1", "evidence/e1", "t1", 1, ["c1"])  # a correction: the same population, saved again
    day.record_evidence("e2", "evidence/e2", "t1", 0, ["c3"])
    day.record_tracks("m1", "tracks/m1", "t1")
    day.record_result({"id": "d1", "tool": "reference_photo", "thread": "t1", "title": "Reference photo"})
    day.record_result({"id": "d2", "tool": "measure", "thread": "t1", "title": "A measurement"})  # not a product
    day.record_result({"id": "d3", "tool": "enhance", "thread": "t1", "title": "Clearer picture"})  # a source
    day.made("bookmark", "b1")
    day.link_research("r1", "t1")
    day.open_source("live")
    day.open_source("a4")  # one source beside the live camera
    day.open_thread("Second look")


def test_the_session_and_sessions_wires_agree_with_the_session_list(tmp_path):
    built = build(tmp_path)
    work(built)
    built.research.working_by_session = lambda: {built.day.sid: 1}  # one researcher working in it now
    session = built.subjects.wire()
    assert set(session) == SESSION_KEYS and session["type"] == "session" and session["suggest"] is None
    assert session["counts"] == {"sources": 1, "made": 6, "researchers": 1, "conversations": 2}  # e1, e2, m1, d1, b1, r1
    assert session["id"] == built.day.sid and session["days"] == [built.day.date]
    info = built.day.catalogue.get(built.day.sid)
    assert session["counts"] == info.counts and session["started"] == info.created
    listing = built.sessions_wire()
    assert listing["type"] == "sessions" and listing["current"] == built.day.sid
    [row] = listing["sessions"]
    assert set(row) == ROW_KEYS | {"thumb"} and row["working"] == 1  # it added an asset: its picture (catalogue.Info.picture)
    assert row["thumb"].startswith("/api/library/") and row["thumb"].endswith("/thumbnail")
    assert {k: row[k] for k in ("id", "counts", "started", "days")} == {k: session[k] for k in ("id", "counts", "started",
                                                                                               "days")}
    built.new_session("Kidney section")
    listing = built.sessions_wire()
    assert [r["name"] for r in listing["sessions"]] == ["Kidney section", ""]  # the newest first; the first unnamed
    assert listing["sessions"][1]["counts"]["made"] == 6 and listing["sessions"][0]["counts"]["made"] == 0
    assert built.subjects.wire()["label"] == "Kidney section" and built.subjects.wire()["named"] is True


def test_new_switch_rename_and_keep_by_the_studios_command(tmp_path):
    built = build(tmp_path)
    first = built.day.sid
    reply = command(built, action="new", name="Kidney section")
    second = reply["session"]
    assert reply["ok"] and second != first and built.day.name == "Kidney section" and built.day.named
    assert command(built, action="switch", id=first)["session"] == first and built.day.sid == first
    assert command(built, action="rename", id=first, name="Leaf epidermis")["changed"]
    assert built.day.name == "Leaf epidermis" and built.day.named
    # Another session's name goes in its own journal, never this one's.
    assert command(built, action="rename", id=second, name="Kidney, H&E")["changed"]
    assert built.day.catalogue.get(second).name == "Kidney, H&E" and built.day.catalogue.get(second).named
    assert not [e for e in built.day.journal.load() if e["type"] == "session-renamed" and e["name"] == "Kidney, H&E"]
    command(built, action="keep")
    assert [e["type"] for e in built.day.journal.load()][-1] == "session-kept"
    client = Client()
    asyncio.run(handle(built, client, {"type": "session", "action": "switch", "id": "2020-01-01.9", "requestId": "x"}))
    assert client.replies[-1]["ok"] is False and built.day.sid == first


def test_activity_says_each_session_command_in_plain_words(tmp_path):
    built = build(tmp_path)
    first = built.day.sid
    client = Client()

    def said(**msg) -> str:
        asyncio.run(handle(built, client, {"type": "session", **msg}))
        return [line for line in built.day.active.lines if line.get("type") == "activity"][-1]["text"]

    assert said(action="new", name="Kidney section") == "You started a new session “Kidney section”"
    second = built.day.sid
    assert said(action="switch", id=first) == "You opened “" + built.day.label + "”"
    assert said(action="rename", id=first, name="Leaf epidermis") == "You renamed this session “Leaf epidermis”"
    assert said(action="rename", id=second, name="Kidney") == "You renamed another session “Kidney”"
    assert said(action="keep") == "You kept working in this session"
    assert said(action="new") == "You started a new session"


def test_the_session_the_app_is_in_survives_a_restart(tmp_path):
    built = build(tmp_path)
    first = built.day.sid
    second = built.new_session("Kidney section")["session"]
    again = build(tmp_path)
    assert again.day.sid == second and again.day.name == "Kidney section"
    again.switch_session(first)
    assert build(tmp_path).day.sid == first


def test_a_late_answer_after_a_switch_is_filed_in_its_own_session_and_not_shown(tmp_path):
    built = build(tmp_path)
    first = built.day.sid
    asked = built.day.here()
    built.conversation.add("user", "chat", "what is this?", asked=asked)
    built.new_session()
    sent = listen(built)
    built.dialogue.show({"type": "chat", "role": "assistant", "text": "A late answer."}, asked=asked)
    built.conversation.add("assistant", "chat", "A late answer.", asked=asked)
    assert not [m for m in sent if m.get("text") == "A late answer."]  # nothing of it reaches a studio
    assert not any(t.lines or t.turns for t in built.day.threads.values())  # nor the session open
    left = built.day.elsewhere(first)
    assert [line["text"] for line in left.threads[asked.thread].lines] == ["A late answer."]
    assert [t.text for t in left.threads[asked.thread].turns] == ["what is this?", "A late answer."]
    log = [json.loads(line) for line in built.paths.conversation_log.read_text().splitlines()]
    assert [r["day"] for r in log if r.get("text") == "A late answer."] == [first]  # exported under its own session


def test_a_switch_is_broadcast_in_order_with_the_list(tmp_path):
    built = build(tmp_path)
    first = built.day.sid
    built.new_session("Kidney section")
    sent = listen(built)

    async def go():
        built.switch_session(first)
        await until_async(lambda: any(m["type"] == "sessions" for m in sent))  # the list is read in a thread

    asyncio.run(go())
    kinds = [m["type"] for m in sent]
    assert kinds.index("session-cleared") < kinds.index("day") < kinds.index("thread") < kinds.index("sessions")
    assert [m["id"] for m in sent if m["type"] == "session"][-1] == first
    listing = next(m for m in sent if m["type"] == "sessions")
    assert listing["current"] == first and len(listing["sessions"]) == 2
    # Newest first by when each was last used, and opening one is using it: this one, then the session left.
    assert [r["name"] for r in listing["sessions"]] == ["", "Kidney section"]


def test_counts_are_sent_again_when_the_open_session_makes_something(tmp_path):
    built = build(tmp_path)
    sent = listen(built)

    async def go():
        built.day.made("bookmark", "b1")
        built.day.made("bookmark", "b2")  # one send for both, at most every 2 s
        await until_async(lambda: any(m["type"] == "sessions" for m in sent))
        await asyncio.sleep(0.05)

    asyncio.run(go())
    assert [m["type"] for m in sent].count("sessions") == 1
    assert [m["counts"]["made"] for m in sent if m["type"] == "session"][-1] == 2


def test_counts_are_sent_again_when_a_source_is_opened(tmp_path):
    """The session row's "1 source" follows the journal's `source` events (ADR 0022), as its "made" does."""
    built = build(tmp_path)
    sent = listen(built)

    async def go():
        built.day.open_source("live")  # the live camera is always a source: never counted
        built.day.open_source("a4")
        await until_async(lambda: any(m["type"] == "sessions" for m in sent))
        await asyncio.sleep(0.05)

    asyncio.run(go())
    [row] = [m for m in sent if m["type"] == "sessions"][-1]["sessions"]
    assert row["counts"]["sources"] == 1
    assert [m["counts"]["sources"] for m in sent if m["type"] == "session"][-1] == 1


def test_switch_session_opens_one_clear_match_with_what_it_knows(tmp_path):
    built = build(tmp_path)
    leaf = built.day.sid
    built.day.rename_session("Leaf epidermis")
    built.conversation.add("user", "chat", "what are these bean-shaped cells?")
    built.conversation.add("assistant", "chat", "Guard cells around a stoma.")
    built.day.set_summary("t1", "We looked at stomata on onion epidermis.", upto=2)
    built.day.set_digest("The specimen is onion epidermis (you said)")
    built.new_session("Blood smear")
    done = asyncio.run(run_tool(built, "switch_session", {"session": "the leaf session"}))
    assert done["ok"] and built.day.sid == leaf
    words = done["summary"]
    assert 'opened the session "Leaf epidermis"' in words and "The specimen is onion epidermis (you said)" in words
    assert "We looked at stomata on onion epidermis." in words and "Guard cells around a stoma." in words
    assert words.endswith("The live view started fresh; its outlines are in its pictures")
    assert built.memory.facts == ["The specimen is onion epidermis (you said)"]  # the opened session's, not the left's


def test_switch_session_lists_several_and_opens_nothing(tmp_path):
    built = build(tmp_path)
    built.day.rename_session("Leaf epidermis, morning")
    morning = built.day.sid
    built.new_session("Leaf epidermis, evening")
    evening = built.day.sid
    built.new_session("Blood smear")
    here = built.day.sid
    done = asyncio.run(run_tool(built, "switch_session", {"session": "leaf epidermis"}))
    assert done["ok"] and built.day.sid == here  # nothing was opened
    assert done["summary"].startswith("nothing was opened") and f"id {morning}" in done["summary"]
    assert f"id {evening}" in done["summary"] and f"id {here}" not in done["summary"]


def test_switch_session_with_no_match_says_so_with_the_most_recent(tmp_path):
    built = build(tmp_path)
    built.day.rename_session("Leaf epidermis")
    built.new_session("Blood smear")
    done = asyncio.run(run_tool(built, "switch_session", {"session": "kidney"}))
    assert done["ok"] is False and "no session matches 'kidney'" in done["summary"]
    assert '"Leaf epidermis"' in done["summary"] and '"Blood smear"' in done["summary"]
    assert asyncio.run(run_tool(built, "switch_session", {"session": built.day.sid}))["summary"].startswith("already in")


def test_new_session_by_the_tool_opens_one_named_as_asked(tmp_path):
    built = build(tmp_path)
    first = built.day.sid
    done = asyncio.run(run_tool(built, "new_session", {"name": "Kidney section"}))
    assert done["ok"] and built.day.sid != first and built.day.name == "Kidney section" and built.day.named
    assert "the live view started fresh" in done["summary"] and first in [s.id for s in built.day.catalogue.list()]


def test_a_new_project_is_new_project_and_never_a_tab_with_new_session_kept_as_its_alias(tmp_path):
    """Voice log 2026-10-28: "create a new project ... call it experiment" made a Tab called Experiment, because
    new_session was "only ... in so many words" and tabs said "this Project's Tabs". new_project starts the Project;
    new_session stays, the same, so saved workflows and tool ids run; tabs is a Tab inside this Project."""
    from orchestrator.brain.loop import MOVES

    built = build(tmp_path)
    tools = built.brain.registry.tools
    first = built.day.sid
    done = asyncio.run(run_tool(built, "new_project", {"name": "Experiment"}))
    assert done["ok"] and built.day.sid != first and built.day.name == "Experiment"
    assert done["summary"].startswith('started a new Project "Experiment"')
    again = asyncio.run(run_tool(built, "new_session", {"name": "Kidney section"}))  # the alias, as it was
    assert again["ok"] and built.day.name == "Kidney section" and again["summary"].startswith('started a new session')
    assert tools["new_session"].params == tools["new_project"].params
    assert {"new_project", "new_session"} <= set(MOVES)  # the request goes on in the Project it opened
    assert "new project" in tools["new_project"].description and "tabs" in tools["new_project"].description
    assert "in so many words" not in tools["new_project"].description + tools["new_session"].description
    assert tools["tabs"].description.startswith("List the Tabs inside this Project (a Tab arranges this Project’s "
                                                "windows; a new Project is new_project)")


def test_a_session_is_named_from_what_the_user_names_never_over_their_own_name(tmp_path):
    built = build(tmp_path)
    built.hub.scene.context["specimen"] = "onion epidermis"  # what is in view at the first request
    built.conversation.add("user", "chat", "what is this?")
    assert built.day.name == "Onion epidermis" and not built.day.named
    built.subjects.name("leaf epidermis")  # the first specimen the user names takes its place, once
    assert built.day.name == "Leaf epidermis" and not built.day.named
    built.subjects.name("kidney section")
    assert built.day.name == "Leaf epidermis"
    built.new_session("My project")
    built.hub.scene.context["specimen"] = "blood smear"
    built.conversation.add("user", "chat", "and this?")
    built.subjects.name("blood smear")
    assert built.day.name == "My project" and built.day.named  # the user's own name stays


def test_midnight_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_HOME", str(tmp_path))  # the session clock's offset is honoured only there
    offset = tmp_path / "clock-offset"
    offset.write_text("0")
    built = build(tmp_path)
    sid = built.day.sid
    built.conversation.add("user", "chat", "before midnight")
    obj_count = len(built.hub.scene.objects)
    sent = listen(built)
    today = built.day.date
    offset.write_text(str(24 * 3600))  # the store's clock moves on a day
    until(lambda: built.day.date != today, every=0.05)
    assert built.day.date != today and built.day.sid == sid
    built.conversation.add("user", "chat", "after midnight")
    assert not [m for m in sent if m["type"] == "session-cleared"]
    assert len(built.hub.scene.objects) == obj_count
    assert [t.text for t in built.day.active.turns] == ["before midnight", "after midnight"]
    assert built.subjects.wire()["days"] == [today, built.day.date]
    again = build(tmp_path)
    assert again.day.sid == sid and again.day.days == [today, built.day.date]


def test_back_to_another_session_and_the_request_and_its_answer_are_filed_there(tmp_path):
    from orchestrator.brain.toolbox import Services
    from orchestrator.brain.toolbox import build as tools
    from tests.test_app import brain_over_a_day

    brain, hub, day, dialogue, shown = brain_over_a_day(tmp_path, [
        {"answer": "Going back to it.", "targets": [], "actions": [{"tool": "switch_session",
                                                                    "args": {"session": "the leaf session"}}]},
        {"answer": "We concluded they were guard cells.", "targets": [], "actions": []}])
    brain.services = Services(hub=hub, agent=brain.agent, day=day)
    brain.registry = tools(brain.services)
    day.rename_session("Leaf epidermis")
    leaf = day.sid
    brain.conversation.add("user", "chat", "are those guard cells?")
    brain.conversation.add("assistant", "chat", "Yes, two guard cells around a stoma.")
    blood = day.new("Blood smear")
    brain.agent.fast_model.rewrite = {"answer": "We concluded they were guard cells."}
    question = "back to the leaf session: what did we conclude?"
    result = asyncio.run(brain.turn(question, "chat"))
    receipt = result["actions"][0]
    assert receipt["tool"] == "switch_session" and receipt["ok"] is True
    assert "Yes, two guard cells around a stoma." in receipt["summary"]
    assert result["answer"] == "We concluded they were guard cells."
    assert day.sid == leaf and result["asked"].session == leaf  # where the user ended up
    dialogue.show({"type": "chat", "role": "assistant", "text": result["answer"]}, asked=result["asked"])
    assert shown[-1]["text"] == result["answer"]  # shown, in the session opened
    turns = [(t.role, t.text) for t in day.active.turns]
    assert ("user", question) in turns and ("assistant", result["answer"]) in turns
    assert (day.elsewhere(blood).active.turns[0].role, day.elsewhere(blood).active.turns[0].text) == ("user", question)


def resumed(lines: list[dict]) -> list[dict]:
    return [line for line in lines if line.get("type") == "app-notice" and line.get("event") == "resumed"]


def test_opening_a_session_with_history_says_once_that_its_live_outlines_are_not_back(tmp_path):
    built = build(tmp_path)
    first = built.day.sid
    built.conversation.add("user", "chat", "what is this?")
    built.day.rename_session("Leaf epidermis")
    built.new_session("Kidney section")
    assert resumed(built.dialogue.history()["lines"]) == []  # a new session has nothing to come back to
    built.switch_session(first)
    [line] = resumed(built.dialogue.history()["lines"])
    assert line["producer"] == {"kind": "session", "id": first} and line["facts"]["liveOutlines"] is False
    assert line["summary"].startswith("Back in 'Leaf epidermis' (last used ")
    assert line["summary"].endswith("Its outlines are in its pictures; the live view starts fresh.")
    again = build(tmp_path)  # a restart comes back to it: said once for this load
    assert again.day.sid == first and len(resumed(again.dialogue.history()["lines"])) == 2
    again.snapshot(), again.snapshot()  # studios connecting are sent the replay, nothing is filed again
    assert len(resumed(again.dialogue.history()["lines"])) == 2


def test_a_new_specimen_long_after_the_session_was_used_is_suggested_never_started(tmp_path):
    """The suggestion (ADR 0017): the running look confirms a different specimen (two looks), the session has not seen
    it, and the session was last asked in 3 hours or more ago. It is shown and told once; nothing starts a session; "Keep
    here" dismisses it for that specimen, and opening a session clears it."""
    from types import SimpleNamespace

    built = build(tmp_path)
    clock = {"now": built.day.clock()}
    built.day.clock = lambda: clock["now"]
    sid = built.day.sid
    look = lambda name, same: SimpleNamespace(specimen=name, same=same)
    built.subjects.observe(look("leaf epidermis", None))
    built.conversation.add("user", "chat", "what are these pores?")
    clock["now"] += 3600  # an hour later, another slide: no suggestion
    built.subjects.observe(look("onion skin", False))
    built.subjects.observe(look("onion skin", False))
    assert built.subjects.subject == "onion skin" and built.day.suggestion is None
    clock["now"] += 4 * 3600  # hours later, a third slide nobody asked about in this session
    built.subjects.observe(look("kidney section", False))
    built.subjects.observe(look("kidney section", False))
    assert built.day.suggestion == {"specimen": "kidney section", "hours": 5.0} and built.day.sid == sid
    assert built.subjects.wire()["suggest"] == {"specimen": "kidney section", "hours": 5.0}
    told = [t.text for t in built.day.active.turns if t.role == "note" and "new specimen" in t.text]
    assert told == ['This looks like a new specimen (kidney section), 5 hours after this session was last used ("Leaf '
                    'epidermis"). A new session for it is the user\'s to start: it may be offered once, never started.']
    command(built, action="keep")
    assert built.day.suggestion is None and "kidney section" in built.day.kept_specimens
    assert built.subjects.wire()["suggest"] is None
    # Back to the kidney section later: kept here, never suggested again for it.
    built.subjects.observe(look("onion skin", False))
    built.subjects.observe(look("onion skin", False))
    clock["now"] += 4 * 3600
    built.subjects.observe(look("kidney section", False))
    built.subjects.observe(look("kidney section", False))
    assert built.day.suggestion is None
    # A restart keeps what was kept; opening another session clears a pending suggestion.
    again = DayStore(root=built.day.root, session=sid, clock=lambda: clock["now"])
    assert again.kept_specimens == {"kidney section"}
    again.suggest("blood smear", 4.0)
    other = again.new()
    again.switch_session(sid)
    assert other != sid and again.suggestion is None


def test_recall_reads_another_session_the_user_names_without_opening_it(tmp_path):
    built = build(tmp_path)
    built.conversation.add("user", "chat", "the platelet looks green to me")
    built.conversation.add("assistant", "chat", "The green is the stain.")
    built.day.rename_session("Blood smear")
    first = built.day.sid
    built.new_session("Leaf epidermis")
    built.conversation.add("user", "chat", "what are these pores?")
    def ask(args):
        done = asyncio.run(run_tool(built, "recall", args))
        return done.get("summary") or ""

    text = ask({"query": "platelet green", "session": "blood smear"})
    assert text.startswith('In the session "Blood smear"') and "not opened" in text and "platelet looks green" in text
    assert built.day.sid != first and built.day.name == "Leaf epidermis"  # nothing was opened
    here = ask({"query": "platelet green"})
    assert "no conversation or research text matches" in here and "other sessions are searched only when named" in here


def test_the_sessions_wire_says_where_projects_are_saved_and_finder_shows_it(tmp_path, monkeypatch):
    # Thesis question 12 (ticket 67, fix 10): nothing on screen said where Projects are saved, or offered Show in Finder.
    import subprocess
    import sys
    from pathlib import Path

    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    built = build(tmp_path / "MicroscopeMind")
    assert built.sessions_wire()["root"] == "~/MicroscopeMind/sessions"
    opened = []
    monkeypatch.setattr(subprocess, "Popen", lambda args, **kw: opened.append(args))
    monkeypatch.setattr(sys, "platform", "darwin")
    reply = asyncio.run(COMMANDS["sessions-reveal"](built, None, {"type": "sessions-reveal"}))
    assert reply == {"ok": True, "path": str(built.paths.sessions)}
    assert opened == [["open", "-R", str(built.paths.sessions)]]
    monkeypatch.setattr(sys, "platform", "linux")
    elsewhere = asyncio.run(COMMANDS["sessions-reveal"](built, None, {"type": "sessions-reveal"}))
    assert elsewhere["ok"] is False and str(built.paths.sessions) in elsewhere["error"]
