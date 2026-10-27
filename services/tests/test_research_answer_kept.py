"""A question to the user keeps its asker waiting for the answer, or hands the answer on (the end of the effort's
kind (h); the scaffolding plan's going-live note 3: "the researcher asked for 10 s of the live view … the user said
'Yes', but the researcher had already finished"). Its finish waits a little for an answer still to come, and an
answer that comes after it finished carries it on, never dropped."""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.capture import Memory
from orchestrator.held import Held
from orchestrator.research import ResearchManager, Session
from tests.fakes import Hub, research_tools
from tests.test_research import FakeHub
from tests.test_research_content import researcher

FOOTAGE = {"what": "10 s of the live view held still", "why": "to measure what moves", "seconds": 10}
DONE = {"title": "t", "summary": "s", "one_liner": "o", "significance": "low"}


def inbox(r, **note) -> None:
    with (r.dir / "inbox.jsonl").open("a") as f:
        f.write(json.dumps({"at": time.time(), "text": "x", **note}) + "\n")


def test_a_finish_waits_for_the_users_answer_and_reads_it_before_finishing(tmp_path):
    r, events = researcher(tmp_path, {"effort": "quick", "fake": {"answer_wait": 3.0}})

    async def go():
        await r.call_tool("request_evidence", FOOTAGE)
        finishing = asyncio.ensure_future(r.call_tool("finish", DONE))
        await asyncio.sleep(0.6)
        inbox(r, text="Your question q1 (10 s of the live view held still): yes", answers="q1")
        return await finishing

    out, _ = asyncio.run(go())
    assert out["ok"] is False and "next note" in out["not_finished"] and r.result is None  # not finished: it reads it
    assert r.take_notes() == ["Your question q1 (10 s of the live view held still): yes"]
    again, _ = asyncio.run(r.call_tool("finish", DONE))
    assert again["ok"] and r.result is not None  # answered: nothing more to wait for
    assert any(e.get("text", "").startswith("Waiting up to 3 s for the user (q1)") for e in events if e.get("event") == "step")


def test_a_finish_waits_once_and_then_finishes_without_an_answer_that_never_comes(tmp_path, wall_clock):
    r, events = researcher(tmp_path, {"effort": "quick", "fake": {"answer_wait": 0.6}})
    asyncio.run(r.call_tool("request_evidence", FOOTAGE))
    started = time.monotonic()
    out, _ = asyncio.run(r.call_tool("finish", DONE))
    assert out["ok"] and r.result is not None and time.monotonic() - started >= 0.5
    assert any("No answer came" in e.get("text", "") for e in events if e.get("event") == "step")
    r.result = None
    started = time.monotonic()
    asyncio.run(r.call_tool("finish", DONE))  # waited for once already: it finishes at once
    assert r.result is not None
    wall_clock.within(time.monotonic() - started, 0.4, "finishing")


def test_footage_still_being_held_is_waited_for_until_it_is_held(tmp_path):
    r, _ = researcher(tmp_path, {"effort": "quick", "fake": {"answer_wait": 3.0}})
    asyncio.run(r.call_tool("request_evidence", FOOTAGE))
    inbox(r, text="Your question q1 (…): yes", answers="q1")
    inbox(r, text="The app is holding 10 s of the live view for you as k17", holding="k17")
    r.take_notes()
    assert r.waiting_on() == ["k17"]

    async def go():
        finishing = asyncio.ensure_future(r.call_tool("finish", DONE))
        await asyncio.sleep(0.6)
        inbox(r, text="k17 is held: 20 frames over 10 s", held="k17")
        return await finishing

    out, _ = asyncio.run(go())
    assert out["ok"] is False and r.result is None and r.waiting_on() == []
    r.waited.clear()
    assert r.waiting_on() == []  # the held mark itself ends the hold's wait (#194 review M7), not only "waited once"


def test_no_wait_when_made_to_write_up_or_for_an_earlier_runs_question(tmp_path, wall_clock):
    last, _ = researcher(tmp_path / "last", {"effort": "quick", "fake": {"answer_wait": 3.0}})
    asyncio.run(last.call_tool("request_evidence", FOOTAGE))
    last.final_round = True  # made to write up (engines._final): no round after it could read the answer
    started = time.monotonic()
    asyncio.run(last.call_tool("finish", DONE))
    assert last.result is not None
    wall_clock.within(time.monotonic() - started, 0.4, "finishing")
    earlier, _ = researcher(tmp_path / "earlier", {"effort": "quick", "fake": {"answer_wait": 3.0}, "inbox_from": 1,
                                                    "requests_from": 1})
    inbox(earlier, text="Your question q1 (…): yes", answers="q1")  # an earlier run's answer, before this run
    asked, _ = asyncio.run(earlier.call_tool("request_evidence", FOOTAGE))  # this run's question, numbered on: q2
    assert asked["id"] == "q2" and earlier.waiting_on() == ["q2"]
    scripted, _ = researcher(tmp_path / "scripted", {"effort": "quick", "fake": {}})  # the harness's: never waits
    asyncio.run(scripted.call_tool("request_evidence", FOOTAGE))
    started = time.monotonic()
    asyncio.run(scripted.call_tool("finish", DONE))
    assert scripted.result is not None
    wall_clock.within(time.monotonic() - started, 0.4, "finishing")


def test_the_apps_notes_carry_its_own_marks_for_the_worker(tmp_path):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-07")
    session = finished_with_a_question(manager, status="running")
    manager.note("r4", "yes", answers="q1")
    manager.note("r4", "The app is holding 10 s for you as k3", about={"holding": "k3", "answers": "q9", "x": 1})
    lines = [json.loads(x) for x in (tmp_path / session.day / "r4" / "inbox.jsonl").read_text().splitlines()]
    assert lines[0]["answers"] == "q1" and lines[1]["holding"] == "k3"
    assert "answers" not in lines[1] and "x" not in lines[1]  # only the app's own marks, never a caller's others


def finished_with_a_question(manager, status="done", effort="quick", deliverable=""):
    session = Session(rid="r4", day="2026-10-07", n=4, brief="is anything moving?", deliverable=deliverable, effort=effort,
                      engine="fake", purpose="research", label="", object_id=None, origin={"channel": "voice"},
                      status="running", result=None)
    manager.sessions[session.rid] = session
    manager._event({"rid": "r4", "event": "request", "id": "q1", **FOOTAGE})
    if status != "running":
        manager._finish(session, "done", {**DONE, "summary": "Motion not yet measured."})
    return session


def late_answer(tmp_path, **kind):
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-07")
    manager._pump = lambda: None  # no worker process
    session = finished_with_a_question(manager, **kind)
    hub = Hub()
    hub.held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    note = research_tools(hub, manager).tools["researcher_note"].handler
    return manager, session, hub, lambda said, args: asyncio.run(note(ToolContext("voice", 1, None, said),
                                                                     {"researcher": "4", **args}))


def test_a_yes_after_it_finished_carries_it_on_with_the_footage_never_dropped(tmp_path):
    manager, session, hub, run = late_answer(tmp_path)
    assert session.status == "done" and session.requests[0]["status"] == "unanswered"
    said = run("Yes", {"text": "Yes", "answers": "q1", "hold_seconds": 10, "asked_with": "Yes"})
    clip = next(iter(hub.held.clips.values()))
    assert said.startswith("Researcher 4 had finished before your answer came, so it carries on with it; holding 10 s")
    assert session.status == "queued" and session.history[-1]["result"]["summary"] == "Motion not yet measured."
    # The researcher is still named by its first request; the carry-on is its newest question.
    assert 'answered your question q1 (10 s of the live view held still) after you had finished: "Yes"' in session.asking
    assert session.requests[0]["status"] == "answered" and session.requests[0]["late"] is True
    assert clip.rid == "r4" and clip.status == "holding"
    assert any(n.get("holding") == clip.id for n in session.notes)  # its finish waits for it (waiting_on)


def test_a_late_plain_answer_carries_it_on_and_a_closed_or_unknown_question_is_refused(tmp_path):
    manager, session, hub, run = late_answer(tmp_path)
    said = run("40x", {"text": "it's 40x", "answers": "q1"})
    assert said == "Researcher 4 had finished before your answer came, so it carries on with it; it reports back when done"
    assert session.status == "queued" and not hub.held.clips
    other, s2, _, run2 = late_answer(tmp_path / "two")
    s2.history.append({"brief": "b", "status": "done"})  # a later run since: q1 was that earlier run's
    with pytest.raises(ToolFailed, match="no open question q1"):
        run2("yes", {"text": "yes", "answers": "q1"})
    with pytest.raises(ToolFailed, match="no open question q1"):  # never "did not ask for footage": it did, then
        run2("yes", {"text": "yes", "answers": "q1", "hold_seconds": 10, "asked_with": "yes"})


def test_a_late_yes_still_needs_the_users_words_to_remake_a_lesson(tmp_path):
    """The yes is the user's request to carry on, held to ADMIT like any continue: a lesson made again needs the user's
    own words for it; a bare "yes" is refused with the reason, and nothing is held."""
    manager, session, hub, run = late_answer(tmp_path, deliverable="lesson", effort="standard")
    with pytest.raises(ToolFailed, match="had finished before the answer came, and can't carry on"):
        run("Yes", {"text": "Yes", "answers": "q1", "hold_seconds": 10, "asked_with": "Yes"})
    assert session.status == "done" and not hub.held.clips and session.requests[0]["status"] == "unanswered"


def test_a_hold_that_ends_tells_its_researcher_with_the_held_mark(tmp_path):
    from types import SimpleNamespace

    from orchestrator.held import tell

    hub = Hub()
    held = Held(hub, memory=Memory({"interactive": 1, "analysis": 1, "unsaved": 1}))
    clip = held.start("r4", "Researcher 4", "q1", 2, "2026-10-07")
    held._finish(clip, "held")
    told = []
    tell(clip, SimpleNamespace(note=lambda rid, text, **marks: told.append((rid, marks))), SimpleNamespace())
    assert told == [("r4", {"about": {"held": clip.id}})]  # a finish waiting on it goes on


def test_one_numbering_across_runs_so_an_answer_in_a_later_run_is_seen(tmp_path):
    """#194 review B1: the app numbers questions across the researcher (q1, q2...) and marks the answer with that id;
    a later run's worker numbers its own on from the earlier runs' (requests_from), so the mark is its own id and
    its finish never waits for a question already answered."""
    manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: "2026-10-07")
    session = finished_with_a_question(manager, status="running")  # its first run asked q1
    session.history.append({"brief": "b", "status": "done"})  # a later run (a continue): its questions go on
    r, _ = researcher(tmp_path / "w", {"effort": "quick", "fake": {"answer_wait": 3.0}, "requests_from": len(session.requests)})
    asked, _ = asyncio.run(r.call_tool("request_evidence", FOOTAGE))
    manager._event({"rid": "r4", "event": "request", "id": asked["id"], **FOOTAGE})
    assert asked["id"] == session.requests[-1]["id"] == "q2"  # the worker's and the app's id are one
    inbox(r, text="Your question q2 (…): yes", answers="q2")
    assert r.waiting_on() == []

    # The app's side of it: the run it starts is told how many the earlier runs asked (#194 re-check SF).
    spawned = {}

    class Worker:
        async def call(self, op, **kwargs):
            spawned.update(kwargs, op=op)

    async def ensure():
        return Worker()

    async def watchdog(rid, limit):
        return None

    manager._ensure_worker, manager._watchdog = ensure, watchdog
    asked_before = len(session.requests)
    asyncio.run(manager._launch(session))
    assert spawned["op"] == "spawn" and spawned["requests_from"] == asked_before >= 1


def test_a_stopped_researcher_is_never_carried_on_and_a_late_no_starts_no_run(tmp_path):
    """#194 review SF2 and SF3: a researcher the user stopped is not started again by a later yes; a "no" to its footage
    after it finished is filed on its question, with no paid run for it."""
    manager, session, hub, run = late_answer(tmp_path)
    said = run("no thanks", {"text": "no thanks", "answers": "q1", "hold_seconds": 0})
    assert said.startswith("Researcher 4 had finished; your answer is filed on its question q1")
    assert session.status == "done" and session.requests[0]["status"] == "answered" and not hub.held.clips
    stopped, s2, _, run2 = late_answer(tmp_path / "two")
    s2.status = "cancelled"
    with pytest.raises(ToolFailed, match="was stopped, so it doesn't carry on"):
        run2("yes", {"text": "yes", "answers": "q1", "hold_seconds": 10, "asked_with": "yes"})
    assert s2.status == "cancelled"


def test_a_finish_past_its_efforts_time_still_waits_for_the_answer(tmp_path, wall_clock):
    """The clock never cuts work (budget.py): long past its effort's time and rounds, a finish waits its whole wait for
    an answer the user is giving, and finishes without it when none comes."""
    r, _ = researcher(tmp_path, {"effort": "quick", "fake": {"answer_wait": 1.5}})
    asyncio.run(r.call_tool("request_evidence", FOOTAGE))
    r.started_at = time.monotonic() - 3 * r.effort.seconds
    r.round_n = 3 * r.effort.max_rounds
    started = time.monotonic()
    asyncio.run(r.call_tool("finish", DONE))
    took = time.monotonic() - started
    assert r.result is not None and took > 1.2
    wall_clock.within(took, 4.0, "finishing")


def test_only_a_researcher_long_enough_to_read_an_answer_is_offered_to_ask(tmp_path):
    for effort, offered in (("instant", False), ("quick", True), ("standard", True)):
        r, _ = researcher(tmp_path / effort, {"effort": effort})
        assert ("request_evidence" in r.tool_names()) is offered, effort
