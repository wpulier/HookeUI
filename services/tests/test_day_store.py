"""The day store: everything said and decided today is an event in the day's journal, and a
restart rebuilds exactly the same state (day/store.py)."""
import time

from orchestrator.brain.state import Conversation
from orchestrator.day import DayStore
from tests.test_finder import outlines  # noqa: F401  (the fixture: outlines drawn as asked, no GPU)


def day(tmp_path, clock=None):
    return DayStore(root=tmp_path, clock=clock or time.time)


def test_a_restart_rebuilds_the_same_day(tmp_path):
    store = day(tmp_path)
    c = Conversation(store=store)
    c.add("user", "voice", "what are we looking at?")
    c.add("assistant", "voice", "A plant stem cross-section.")
    store.set_subject("dicot stem", "look")
    store.open_thread("Blood smear")
    c.add("user", "chat", "outline the white cells")
    store.set_summary("t1", "Talked about a dicot stem.", upto=2)
    store.set_pref("userLeads", True)
    store.link_research("r123")

    again = day(tmp_path)
    assert again.active_id == "t2" and [t.id for t in again.threads.values()] == ["t1", "t2"]
    assert [(t.id, t.role, t.text) for t in again.threads["t1"].turns] == [
        (1, "user", "what are we looking at?"), (2, "assistant", "A plant stem cross-section.")]
    assert again.threads["t1"].summary == "Talked about a dicot stem." and again.threads["t1"].summary_upto == 2
    assert again.threads["t2"].subject == "dicot stem"  # what was in view carried into the new thread
    assert again.threads["t2"].research == ["r123"] and again.prefs == {"userLeads": True}
    assert again.threads["t1"].title == "What are we looking at"  # named from its first request
    assert Conversation(store=again).add("user", "chat", "and the red ones?").id == 4  # ids never reused


def test_a_torn_last_line_is_skipped(tmp_path):
    store = day(tmp_path)
    Conversation(store=store).add("user", "chat", "hello")
    with store.journal.path.open("a") as f:
        f.write('{"type": "turn", "thread": "t1", "id": 2, "role": "us')  # a crash mid-write
    again = day(tmp_path)
    assert [t.text for t in again.active.turns] == ["hello"]
    assert Conversation(store=again).add("user", "chat", "still here").id == 2


def test_the_conversation_is_the_active_thread(tmp_path):
    store = day(tmp_path)
    c = Conversation(store=store)
    c.add("user", "voice", "first topic question")
    store.open_thread()
    assert c.turns == [] and c.text() == "(none)"
    c.add("user", "chat", "second topic question")
    store.switch("t1")
    assert [t.text for t in c.turns] == ["first topic question"]
    c.add_once("user", "voice", "first topic question and more")  # a joined piece edits the turn, journaled
    assert [t.text for t in day(tmp_path).threads["t1"].turns] == ["first topic question and more"]


def test_the_chat_log_is_imported_once_into_an_empty_day(tmp_path):
    records = [{"type": "chat", "role": "user", "text": "what is this?", "voice": True, "at": "2026-10-04T15:00:00"},
               {"type": "chat", "role": "assistant", "text": "A dicot stem.", "at": "2026-10-04T15:00:03"}]
    store = day(tmp_path)
    assert Conversation(store=store).restore(records) == 2
    again = day(tmp_path)
    assert Conversation(store=again).restore(records) == 0 and len(again.active.turns) == 2


def test_threads_are_found_the_way_people_refer_to_them(tmp_path):
    store = day(tmp_path)
    c = Conversation(store=store)
    c.add("user", "chat", "what's on this slide?")
    store.set_subject("blood smear", "look")
    store.open_thread("Housefly leg")
    store.set_subject("Musca leg", "user")
    store.open_thread()
    assert store.find_thread("the blood smear one").id == "t1"
    assert store.find_thread("go back to the housefly").id == "t2"
    assert store.find_thread("thread 2").id == "t2"
    assert store.find_thread("the previous one").id == "t2"
    assert store.find_thread("the moon one") is None


def test_midnight_does_nothing_the_session_goes_on(tmp_path):
    """A session is the project, not the day (ADR 0017): past midnight the same session and conversation go on."""
    now = {"t": time.mktime((2026, 10, 4, 23, 59, 0, 0, 0, -1))}
    store = day(tmp_path, clock=lambda: now["t"])
    Conversation(store=store).add("user", "chat", "late question")
    now["t"] += 120
    Conversation(store=store).add("user", "chat", "after midnight")
    assert store.sid == "2026-10-04" and store.date == "2026-10-05" and store.days == ["2026-10-04", "2026-10-05"]
    assert [t.text for t in store.active.turns] == ["late question", "after midnight"]


def test_a_new_session_starts_with_one_conversation_and_the_one_left_is_kept(tmp_path):
    store = day(tmp_path)
    left = store.sid
    Conversation(store=store).add("user", "chat", "before the new session")
    store.open_thread("Second")
    events = []
    store.listeners.append(events.append)
    sid = store.new()
    assert sid == f"{left}.2" and store.sid == sid and list(store.threads) == ["t1"] and store.active.turns == []
    assert [e["type"] for e in events] == ["session-opened"] and events[0]["previous"] == left
    kept = DayStore(root=tmp_path, session=left)
    assert list(kept.threads) == ["t1", "t2"] and kept.threads["t1"].turns[0].text == "before the new session"
    assert DayStore.resume(root=tmp_path).sid == sid  # a restart opens the session the app was in


def test_a_new_session_begins_later_and_a_restart_keeps_it(tmp_path):
    """Made in this session: the day's wire says when the session began, its first conversation's start. A new
    session begins a new one; a restart reads the same journal and keeps it."""
    store = day(tmp_path)
    first = store.wire()["sessionStart"]
    time.sleep(0.02)
    store.open_thread("Second")
    assert first is not None and store.wire()["sessionStart"] == first  # a later conversation is the same session
    assert day(tmp_path).wire()["sessionStart"] == first  # a restart keeps it
    time.sleep(0.02)
    store.new()
    assert store.wire()["sessionStart"] > first  # a new session


def test_a_torn_line_never_swallows_the_next_event(tmp_path):
    store = DayStore(root=tmp_path)
    store.add_turn("user", "chat", "first")
    with store.journal.path.open("ab") as f:
        f.write('{"type": "turn", "text": "µ'.encode()[:-1])  # cut mid-line and mid-character
    again = DayStore(root=tmp_path)
    again.add_turn("user", "chat", "still here")
    third = DayStore(root=tmp_path)
    assert [t.text for t in third.active.turns] == ["first", "still here"]
    assert [t.id for t in third.active.turns] == [1, 2]


def test_sessions_of_one_date_number_on_and_keep_their_own_turns(tmp_path):
    store = DayStore(root=tmp_path)
    date = store.sid
    store.add_turn("user", "chat", "one")
    assert store.new() == f"{date}.2"
    store.add_turn("user", "chat", "two")
    assert store.new() == f"{date}.3"
    assert store.add_turn("user", "chat", "three").id == 1
    assert [t.text for t in DayStore(root=tmp_path, session=date).active.turns] == ["one"]
    assert [t.text for t in DayStore(root=tmp_path, session=f"{date}.2").active.turns] == ["two"]


def test_a_reply_goes_to_the_conversation_its_question_was_asked_in(tmp_path):
    from orchestrator.brain.state import Conversation

    store = DayStore(root=tmp_path)
    conversation = Conversation(store=store)
    asked = conversation.add("user", "chat", "what is this?")
    store.open_thread()
    conversation.add("assistant", "chat", "a stoma", asked=conversation.asked_of(asked))
    assert [t.text for t in store.threads["t1"].turns] == ["what is this?", "a stoma"]
    assert store.active.turns == [] and conversation.asked_of(asked).thread == "t1"


def test_a_journal_that_cannot_be_written_does_not_stop_the_conversation(tmp_path, monkeypatch):
    store = DayStore(root=tmp_path)

    def broken(event):
        raise OSError("disk full")

    monkeypatch.setattr(store.journal, "append", broken)
    assert store.add_turn("user", "chat", "still answered").text == "still answered"


def test_what_find_outlined_reaches_the_days_facts(tmp_path, outlines):
    """Live 2026-10-11: `find` filed its outlines with only their id and frame, and the day's journal (which keeps an
    outline's receipt only with its six facts: id, run, frame, epoch, when, turn) dropped them, so the app's facts never
    held what `find` outlined. It files the same record highlight does (toolbox.common.record_outline)."""
    import asyncio

    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.day.store import _qualified_output_refs
    from tests.fakes import Hub
    from tests.test_finder import Agent_

    hub = Hub()
    hub.latest_info.width, hub.latest_info.height = 1280, 720
    registry = build(Services(hub=hub, agent=Agent_(hub, [("stoma", (400, 250, 560, 400), 0.9)])))

    async def go():
        run = registry.start("find", {"what": "stoma"}, ToolContext("chat", 3, 7, "find the stoma"))
        await run.task
        return run

    run = asyncio.run(go())
    rows = [o.wire() for o in run.outputs]
    keys = ("id", "run", "frame", "epoch", "created_at", "turn_id")
    assert run.ok and rows and set(keys) <= set(rows[0])
    kept = _qualified_output_refs([{k: row.get(k) for k in keys} for row in rows])
    assert [r["id"] for r in kept] == [row["id"] for row in rows] and kept[0]["turn_id"] == 3
