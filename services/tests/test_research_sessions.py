"""Researchers belong to a session (ADR 0017: a session is the project, not the day).

A researcher is numbered in, filed in and continued only in the session it was started in, on whichever days that
session runs; its spend is charged to the calendar day each of its runs happens on, and a day's cap counts every
session's spend that day. A researcher keeps working when the user opens another session: what it does is filed in
its own, and the session open gets one line about it.
"""
from __future__ import annotations

import asyncio
import json
import time

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.research import ResearchManager, ResearchRefused, Session
from tests.test_research import FakeHub, until
from tests.test_subject import as_real

DAY1, DAY2 = "2026-10-11", "2026-10-12"


def noon(day: str) -> float:
    return time.mktime(time.strptime(day + " 12:00", "%Y-%m-%d %H:%M"))


class Transport:
    """The research process's transport only: the real manager admits, dispatches and settles each run."""

    alive = True

    def __init__(self):
        self.spawned = []

    async def call(self, op, **payload):
        if op == "spawn":
            self.spawned.append(payload)
        return {"ok": True}

    def stop(self):
        self.alive = False


def test_each_session_starts_at_researcher_1_and_the_days_spend_counts_both(tmp_path, monkeypatch):
    """Two sessions on one date: each counts its researchers from 1, a number once given is never changed, and the
    day's spend (the daily cap) is both sessions' together, also after a restart."""
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")
    open_ = {"sid": DAY2}

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path, today=lambda: DAY2, session=lambda: open_["sid"])
        done = []
        manager.on_done.append(done.append)
        a1 = await manager.start("stomata", effort="quick", fake={"seconds": 0.2})
        assert await until(lambda: len(done) == 1)
        open_["sid"] = DAY2 + ".2"  # the user started another session the same day
        manager.session_event({"type": "session-opened", "session": open_["sid"]})
        b1 = await manager.start("blood smear", effort="quick", fake={"seconds": 0.2})
        assert await until(lambda: len(done) == 2)
        in_b = manager.lines()
        both = manager.spent_today()
        open_["sid"] = DAY2  # back to the first
        manager.session_event({"type": "session-opened", "session": DAY2})
        a2 = await manager.start("guard cells", effort="quick", fake={"seconds": 0.2})
        assert await until(lambda: len(done) == 3)
        manager.stop()
        return manager, a1, b1, a2, in_b, both

    manager, a1, b1, a2, in_b, both = asyncio.run(go())
    assert (a1.n, b1.n, a2.n) == (1, 1, 2)
    assert (a1.sitting, b1.sitting, a2.sitting) == (DAY2, DAY2 + ".2", DAY2)
    assert a1.day == b1.day == a2.day == DAY2  # one date folder for both sessions
    assert json.loads((tmp_path / DAY2 / b1.rid / "session.json").read_text())["session"] == DAY2 + ".2"
    assert in_b.startswith("Researcher 1 (") and "blood smear" in in_b and "stomata" not in in_b
    assert [s.rid for s in manager.researchers()] == [a1.rid, a2.rid]
    assert manager.by_number(1) is a1 and manager.by_number(2) is a2
    assert a1.spent > 0 and b1.spent > 0
    assert both == pytest.approx(a1.spent + b1.spent)  # the cap counts both sessions' spend that day
    assert manager.spent_today() == pytest.approx(a1.spent + b1.spent + a2.spent)
    # Its list: the session open's researchers and the descriptions; another session's only while it works.
    listed = {s["rid"] for s in manager.list_wire()["sessions"]}
    assert {a1.rid, a2.rid} <= listed and b1.rid not in listed

    as_real(tmp_path)
    again = ResearchManager(FakeHub(), root=tmp_path, today=lambda: DAY2, session=lambda: DAY2 + ".2")  # a restart
    assert [(s.rid, s.n) for s in again.researchers()] == [(b1.rid, 1)]
    assert [(s.rid, s.n) for s in again.researchers(session=DAY2)] == [(a1.rid, 1), (a2.rid, 2)]  # never renumbered
    assert again.spent_today() == pytest.approx(manager.spent_today())  # the other session's, from disk
    assert again._next_number(DAY2 + ".2") == 2


def test_a_researcher_of_another_session_is_refused_with_its_name(tmp_path):
    """Only this session's work is continued: a run, note, answer, kept question or composer request for a researcher
    of another session is refused, naming that session; nothing is reserved."""
    store = SessionStore(tmp_path / "sessions", clock=lambda: noon(DAY2))
    store.rename_session("Blood smear")
    blood = store.sid
    manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: store.date, store=store)
    session = Session(rid="r5", n=2, brief="b", deliverable="research", effort="quick", engine="gpt-6.1-sol",
                      purpose="research", label="", object_id=None, origin={"channel": "chat"}, day=DAY2, status="done",
                      sitting=blood, result={"title": "t", "summary": "s"})
    session.requests.append({"id": "q1", "run": 0, "what": "the objective", "status": "unanswered"})
    manager.sessions[session.rid] = session
    store.new("Leaf epidermis")
    manager.session_event({"type": "session-opened", "session": store.sid})
    held = []
    manager.spending.hold = lambda *a, **k: held.append(a)
    words = "Researcher 2 belongs to “Blood smear”; switch to it to continue"
    with pytest.raises(ResearchRefused) as refused:
        asyncio.run(manager.followup(session.rid, "and the pore?"))
    assert str(refused.value) == words
    for attempt in (lambda: manager.note(session.rid, "focus on the pore"),
                    lambda: asyncio.run(manager.carry_on(session.rid, "q1", "yes")),
                    lambda: manager.close_question(session.rid, "q1", "no"),
                    lambda: asyncio.run(manager.followup(session.rid, "shorter", mode="revise"))):
        with pytest.raises(ResearchRefused, match=words):
            attempt()

    async def compose():
        await manager.interact(session.rid, "request-1", {"text": "more"}, dispatch=lambda: None)

    with pytest.raises(ResearchRefused, match=words):
        asyncio.run(compose())
    assert held == [] and session.status == "done" and not session.interaction_jobs
    assert manager.researchers() == [] and manager.by_number(2) is None  # not this session's Researcher 2
    store.switch_session(blood)  # back in its own session, it is continued as any other
    manager.session_event({"type": "session-opened", "session": blood})
    manager._this_session_only(session)
    assert manager.researchers() == [session]


def _saved(root, day: str, rid: str, sid: str, **extra) -> None:
    folder = root / day / rid
    folder.mkdir(parents=True)
    (folder / "session.json").write_text(json.dumps({
        "rid": rid, "n": 1, "brief": "stomata", "effort": "quick", "engine": "gpt-6.1-sol", "purpose": "research",
        "deliverable": "research", "status": "done", "session": sid, "created": noon(day), "spent": 0.10,
        "result": {"title": "Stomata", "summary": "Stomata regulate gas exchange."}, **extra}))


def test_this_sessions_researcher_from_yesterday_is_continued_and_charged_today(tmp_path):
    """P4: resume yesterday's slide. A researcher this session started yesterday is continued today; its follow-up is
    charged to today's ledger, never yesterday's, and today's spend still finds it after a restart in another
    session (research/<today>/runs.jsonl), though its folder is yesterday's."""
    clock = {"at": noon(DAY1)}
    store = SessionStore(tmp_path / "sessions", clock=lambda: clock["at"])
    sid = store.sid
    assert sid == DAY1
    _saved(tmp_path / "research", DAY1, "r1", sid)
    store.link_research("r1")  # started in its first conversation, yesterday
    clock["at"] = noon(DAY2)  # the next day, the same session: midnight did nothing

    async def go():
        manager = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: store.date, store=store)
        transport = Transport()
        manager._worker = transport
        session = manager.get("r1")
        assert session is not None and session.sitting == sid and session.day == DAY1
        await manager.followup("r1", "and the pore?")
        assert await until(lambda: transport.spawned)
        run = session._attempt.id
        manager._event({"rid": "r1", "runId": run, "event": "cost", "spent": 0.03, "eventSequence": 0})
        manager._event({"rid": "r1", "runId": run, "event": "done", "status": "done", "spent": 0.03,
                        "eventSequence": 1, "result": {"title": "Pore", "summary": "It opens in light."}})
        return manager, session, transport.spawned[0], run

    manager, session, spawned, run = asyncio.run(go())
    assert session.history[-1]["mode"] == "answer" and session.history[-1]["status"] == "done"
    assert spawned["session"] == sid and spawned["day"] == DAY1  # its own session's index; its files stay put
    assert session.usage_runs[run]["day"] == DAY2
    assert manager.spending.spent(DAY2) == pytest.approx(0.03)
    assert manager.spending.spent(DAY1) == pytest.approx(0.10)
    pointer = [json.loads(line) for line in (tmp_path / "research" / DAY2 / "runs.jsonl").read_text().splitlines()]
    assert pointer[0]["rid"] == "r1" and pointer[0]["day"] == DAY1
    store.new("Blood smear")  # then the app restarts in another session: r1 is not loaded, but today counts it
    later = ResearchManager(FakeHub(), root=tmp_path / "research", today=lambda: store.date, store=store)
    assert later.get("r1") is None
    assert later.spending.spent(DAY2) == pytest.approx(0.03) and later.spending.spent(DAY1) == pytest.approx(0.10)


def test_a_session_reads_its_researchers_from_every_date_it_touched(tmp_path):
    """A session's researchers are in their dates' folders; another session's on the same dates are not its own, and
    one saved before sessions sits in the session of its date."""
    clock = {"at": noon(DAY1)}
    store = SessionStore(tmp_path / "sessions", clock=lambda: clock["at"])
    root = tmp_path / "research"
    _saved(root, DAY1, "r1", DAY1)
    store.link_research("r1")
    clock["at"] = noon(DAY2)
    other = store.new("Blood smear")
    _saved(root, DAY2, "r2", other)  # the other session's, today
    store.switch_session(DAY1)
    _saved(root, DAY2, "r3", DAY1, n=2)
    store.link_research("r3")
    legacy = root / DAY1 / "r0"
    legacy.mkdir()
    (legacy / "session.json").write_text(json.dumps({"rid": "r0", "n": 3, "brief": "old", "effort": "quick",
                                                     "engine": "gpt-6.1-sol", "purpose": "research", "status": "done",
                                                     "result": {"title": "Old", "summary": "s"}}))
    manager = ResearchManager(FakeHub(), root=root, today=lambda: store.date, store=store)
    assert {s.rid for s in manager.researchers()} == {"r0", "r1", "r3"}
    assert manager.get("r0").sitting == DAY1 and manager.get("r2") is None
    assert [s.rid for s in manager.researchers(session=other)] == ["r2"]
    assert manager._next_number(DAY1) == 4


@pytest.mark.parametrize("asked", ["by-its-request", "no-request"])
def test_a_researcher_finishing_after_a_switch_is_filed_in_its_session_with_one_line_in_the_open_one(tmp_path, monkeypatch,
                                                                                                    asked):
    """A researcher started in session A keeps working when the user starts session B. Its result is filed in A (its
    notice, and its facts in the conversation it was asked in); B gets one line about it, saying which session it is
    from (fromSession), and nothing of it opens or is said there. The real App, Brain, manager and Dialogue; only the
    model, the research process's transport and the studio are scripted (test_model_owned_research_activity). With
    no request to file it by (a mission's checkpoint run), it is filed by its session and conversation."""
    from test_model_owned_conversation import eventually
    from test_model_owned_research_activity import FIRST, ResearchApp

    # A set-up app: the researchers' model has its key (without it the start is refused: test_research_engine_key.py).
    monkeypatch.setenv("OPENAI_API_KEY", "test-openai-key")
    rig = ResearchApp(tmp_path, monkeypatch)

    def notices(store, rid):
        return [line for thread in store.threads.values() for line in thread.lines
                if line.get("type") == "app-notice" and line.get("producer") == {"kind": "research", "id": rid}]

    async def scenario():
        try:
            session, job, ctx = await rig.start()
            a = rig.app.day.sid
            rig.app.day.rename_session("Blood smear")
            in_a = len(notices(rig.app.day, session.rid))
            rig.app.new_session()
            b = rig.app.day.sid
            assert b != a and session.sitting == a and rig.app.research.get(session.rid) is session
            assert session.status in ("starting", "running")  # it keeps working
            assert any(s["rid"] == session.rid and s["sitting"] == a for s in rig.app.research.list_wire()["sessions"])
            assert rig.app.research.working_by_session() == {a: 1}
            assert rig.app.research.lines() == "" and rig.app.research._next_number(b) == 1
            if asked == "no-request":
                session._delivery = None
            rig.worker.finish(job)
            await eventually(lambda: notices(rig.app.day, session.rid), "the open session must get its one line")
            await asyncio.sleep(0.2)  # anything more it would file or send
            here = notices(rig.app.day, session.rid)
            assert len(here) == 1, here
            line = here[0]
            assert line["fromSession"] == {"id": a, "name": "Blood smear"} and line["event"] == "completed"
            assert line["day"] == b and line["thread"] == rig.app.day.active_id
            assert "Blood smear" not in line["summary"] and session.name in line["summary"]  # the studio says where
            assert line["facts"]["result"]["one_liner"] == FIRST and line["facts"]["status"] == "done"
            filed = rig.app.day.elsewhere(a)
            there = notices(filed, session.rid)
            assert len(there) == in_a + 1 and there[-1]["event"] == "completed" and there[-1]["day"] == a
            assert there[-1]["thread"] == ctx.asked.thread and "fromSession" not in there[-1]
            assert any("research lifecycle" in turn.text for turn in filed.threads[ctx.asked.thread].turns)
            await eventually(lambda: line in rig.renderer.messages, "the one line must reach the studio")
            assert [m for m in rig.renderer.messages if m.get("type") == "app-notice"
                    and (m.get("producer") or {}).get("id") == session.rid] == [line]  # only the one line is shown
            rig.assert_no_app_assistant()
            assert not rig.app.presenter.open
            assert session.status == "done" and rig.app.research.spending.spent(session.day) == pytest.approx(0.01)
        finally:
            await rig.close()

    asyncio.run(scenario())
