"""Missions (orchestrator/research/missions.py; the partner's Amendment 1 B): the plan, the ledger and the scheduler,
on a fake clock. One row per slot, written once with its real time; late within the grace, else missed; never
measured off the pinned field; slots never stack, never during their mission's checkpoint; a restart comes back
interrupted, and carrying on marks the slots due while the app was down as missed."""
from __future__ import annotations

import asyncio

import pytest

from orchestrator.research.missions import (MISSION_MAX_S, Mission, Missions, Slot, field_now, plan)

PIN = {"epoch": 0, "dx": 0.0, "dy": 0.0, "width": 1280, "height": 720, "discPx": 340.0, "run": "r1", "objective": 40}
OPS = ("segment", "enhance", "motion")


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    async def sleep(self, s: float) -> None:
        self.t += max(0.0, s)
        await asyncio.sleep(0)


def scheduler(clock, run=None, field=lambda m: "", checkpoint=None, finish=None):
    saved, ran = [], []

    async def run_slot(m, slot):
        ran.append((slot.n, clock()))
        if run is not None:
            return await run(m, slot)
        return {"ok": True, "id": f"e{slot.n}", "count": 10 + slot.n, "capture": {"frame": slot.n, "seq": 100 + slot.n}}

    s = Missions(run_slot, field, lambda m, slot: saved.append((slot.n if slot else None, m.status)),
                 checkpoint, finish, clock=clock, sleep=clock.sleep)
    return s, saved, ran


def through(start) -> None:
    """Runs a mission to its end: start() makes its task on the running loop."""
    async def go():
        await start()
    asyncio.run(go())


def mission(clock, every=60.0, window=180.0, checkpoint_every=3):
    return plan("r4", "segment", every, window, PIN, clock(), OPS, checkpoint_every)


def test_each_slot_runs_at_its_time_from_a_baseline_at_the_start_and_writes_one_row_once():
    """The playbook's section 14: a baseline at the start, then one every period (3 h every 10 min is 19 slots)."""
    clock = Clock()
    s, saved, ran = scheduler(clock)
    m = mission(clock)
    through(lambda: s.start(m))
    assert [r.n for r in m.ledger] == [0, 1, 2, 3] and all(r.outcome == "done" for r in m.ledger)
    assert [r.ran_at for r in m.ledger] == [1000.0, 1060.0, 1120.0, 1180.0] == [t for _, t in ran]
    assert [r.entry for r in m.ledger] == ["e0", "e1", "e2", "e3"] and m.ledger[0].capture == {"frame": 0, "seq": 100}
    assert [n for n, _ in saved if n is not None] == [0, 1, 2, 3]  # each row saved once, as it happened
    assert m.status == "done" and m.counts()["done"] == 4
    assert plan("r4", "segment", 600.0, 3 * 3600.0, PIN, 0.0, OPS).slots == 19 and m.wire()["convention"] == "t0+"


def test_a_late_slot_runs_with_its_real_time_and_one_past_its_grace_is_missed():
    clock = Clock()

    async def slow(m, slot):
        await clock.sleep({1: 70.0, 2: 100.0}.get(slot.n, 0.0))  # slot 1 ends at 1130, slot 2 at 1230
        return {"ok": True, "id": f"e{slot.n}"}

    s, saved, ran = scheduler(clock, run=slow)
    m = mission(clock, window=240.0)
    through(lambda: s.start(m))
    late, missed = m.ledger[2], m.ledger[3]
    assert late.outcome == "done" and late.due == 1120.0 and late.ran_at == 1130.0  # late, with its real time
    assert missed.outcome == "missed" and missed.ran_at is None and "within 30 s" in missed.why  # never backdated
    assert m.ledger[4].outcome == "done" and m.ledger[4].ran_at == 1240.0


def test_a_slot_that_finds_the_app_busy_waits_within_its_grace_then_is_missed():
    clock = Clock()

    async def busy(m, slot):
        if clock() < 1075.0:
            return {"ok": False, "busy": True}
        return {"ok": True, "id": f"e{slot.n}"}

    s, saved, ran = scheduler(clock, run=busy)
    m = mission(clock, window=120.0)
    through(lambda: s.start(m))
    assert m.ledger[0].outcome == "missed" and "held the app's one slot" in m.ledger[0].why  # busy past its grace
    assert m.ledger[1].outcome == "done" and 1075.0 <= m.ledger[1].ran_at <= 1077.0 and m.ledger[1].why == ""


def test_a_view_that_is_not_the_pinned_field_is_not_comparable_and_runs_nothing():
    clock = Clock()
    s, saved, ran = scheduler(clock, field=lambda m: "the slide moved 90 px from the pinned field" if clock() > 1100 else "")
    m = mission(clock)
    through(lambda: s.start(m))
    assert [r.outcome for r in m.ledger] == ["done", "done", "not comparable", "not comparable"]
    assert m.ledger[2].why == "the slide moved 90 px from the pinned field" and m.ledger[2].ran_at == 1120.0
    assert [n for n, _ in ran] == [0, 1]  # its operation never ran off the field


def test_the_view_a_slot_ran_on_off_the_field_is_said_and_its_entry_kept():
    """#196 review SF8: the field is checked before the slot, then the view its operation took is checked too."""
    clock = Clock()

    async def drifted(m, slot):
        return {"ok": True, "id": f"e{slot.n}", **({"off_field": "the view it ran on: the slide moved 40 px"}
                                                    if slot.n == 1 else {})}

    s, _, _ = scheduler(clock, run=drifted)
    m = mission(clock, window=120.0)
    through(lambda: s.start(m))
    assert [(r.outcome, r.entry) for r in m.ledger] == [("done", "e0"), ("not comparable", "e1"), ("done", "e2")]
    assert m.ledger[1].why == "the view it ran on: the slide moved 40 px"


@pytest.mark.parametrize("now, objective, why", [
    (PIN, 40, ""),
    ({**PIN, "dx": 20.0, "dy": 10.0}, 40, ""),  # within a tenth of the eyepiece's radius
    ({**PIN, "dx": 60.0}, 40, "the slide moved 60 px"),
    ({**PIN, "epoch": 1}, 40, "lost continuity"),
    ({**PIN, "run": "r2"}, 40, "the app restarted"),
    ({**PIN, "width": 1920, "height": 1080}, 40, "picture size changed"),
    ({**PIN, "discPx": 341.3}, 40, ""),  # 0.4%: within contract 3's 0.5%
    ({**PIN, "discPx": 342.1}, 40, "eyepiece's scale changed"),  # 0.6%
    ({**PIN, "discPx": 346.8}, 40, "eyepiece's scale changed"),  # 2%: the phone refocusing (#196 review B1)
    ({**PIN, "tracked": False}, 40, "not registered"),  # an unregistered view only carries the last offset over
    (PIN, 10, "the objective changed (pinned at 40x, now 10x)"),
    ({}, 40, "no live view"),
])
def test_the_field_is_the_pinned_one_or_said_why_not(now, objective, why):
    said = field_now(PIN, now, objective)
    assert (said == "") if not why else (why in said)


def test_a_checkpoint_comes_after_every_n_done_slots_and_no_slot_runs_during_it():
    clock = Clock()
    during, checkpoints = [], []

    async def check(m):
        checkpoints.append((clock(), [r.n for r in m.ledger]))
        await clock.sleep(50.0)  # the model reads; the next slot waits until it is over

    async def run(m, slot):
        during.append(m.status)
        return {"ok": True, "id": f"e{slot.n}"}

    s, saved, ran = scheduler(clock, run=run, checkpoint=check)
    m = mission(clock, window=300.0, checkpoint_every=2)
    through(lambda: s.start(m))
    assert checkpoints == [(1060.0, [0, 1]), (1180.0, [0, 1, 2, 3])] and "checkpoint" not in during
    assert m.ledger[5].ran_at == 1300.0 and m.ledger[5].outcome == "done"
    clock2, at_end = Clock(), []

    async def check2(m):
        at_end.append(len(m.ledger))

    s2, _, _ = scheduler(clock2, checkpoint=check2)
    m2 = mission(clock2, window=180.0, checkpoint_every=2)  # four slots: the fourth would complete one
    through(lambda: s2.start(m2))
    assert at_end == [2]  # none after the last slot: the end writes the finding instead


def test_cancelling_stops_its_future_slots_and_keeps_what_ran():
    clock = Clock()
    s, saved, ran = scheduler(clock)
    m = mission(clock, window=600.0)

    async def go():
        task = s.start(m)
        while len(m.ledger) < 2:
            await asyncio.sleep(0)
        s.cancel("r4", "the user cancelled it")
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(go())
    assert [r.n for r in m.ledger] == [0, 1] and m.status == "cancelled" and m.why_ended == "the user cancelled it"
    assert not s.active


def test_a_mission_that_ends_while_it_waits_is_cancelled_with_no_row():
    """#196 review SF5: what its owner says ends it ends it at once (checked every second while it waits), never as a
    row. (The research manager's own `ends` says nothing ends one early: midnight does nothing, ADR 0017.)"""
    clock = Clock()
    over = {"why": ""}
    s, saved, ran = scheduler(clock)
    s.ends = lambda m: over["why"]
    m = mission(clock, every=600.0, window=1800.0)  # the next slot is 10 minutes away

    async def go():
        task = s.start(m)
        while len(m.ledger) < 1:
            await asyncio.sleep(0)
        over["why"] = "its researcher is no longer kept"  # between slots
        await task

    asyncio.run(go())
    assert [r.n for r in m.ledger] == [0] and m.status == "cancelled" and m.why_ended == "its researcher is no longer kept"
    assert clock() < 1010.0  # noticed within a second, never only at the next slot's time (1600)


def test_a_wall_clock_step_neither_bursts_nor_stalls_the_slots():
    """#196 review note: due times are labels; waiting is on the monotonic clock from the start."""
    clock = Clock()
    mono = {"t": 0.0}

    async def sleep(s):
        clock.t += s
        mono["t"] += s
        await asyncio.sleep(0)

    s = Missions(lambda m, slot: _done(slot), lambda m: "", lambda m, slot: None, clock=clock, sleep=sleep,
                 mono=lambda: mono["t"])
    m = mission(clock, window=180.0)

    async def go():
        task = s.start(m)
        while len(m.ledger) < 1:
            await asyncio.sleep(0)
        clock.t -= 3600.0  # the wall clock steps back an hour
        await task

    asyncio.run(go())
    assert [r.outcome for r in m.ledger] == ["done"] * 4 and mono["t"] == pytest.approx(180.0)


async def _done(slot):
    return {"ok": True, "id": f"e{slot.n}"}


def test_after_a_restart_it_is_interrupted_and_carrying_on_marks_the_slots_it_missed():
    clock = Clock()
    s, saved, ran = scheduler(clock)
    m = mission(clock, window=360.0)

    async def first_two():
        task = s.start(m)
        while len(m.ledger) < 2:
            await asyncio.sleep(0)
        task.cancel()  # the app stopped
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(first_two())
    wire = {**m.wire(), "status": "watching"}  # as saved before the stop
    again = Mission.from_wire("r4", wire)
    assert again.status == "interrupted" and [r.n for r in again.ledger] == [0, 1]
    clock.t = 1000.0 + 4 * 60.0 + 40.0  # the app is back at 1280: slots 2 (1120), 3 (1180) and 4 (1240) passed
    s2, _, ran2 = scheduler(clock)
    through(lambda: s2.carry_on(again))
    assert [(r.n, r.outcome) for r in again.ledger] == [(0, "done"), (1, "done"), (2, "missed"), (3, "missed"),
                                                        (4, "missed"), (5, "done"), (6, "done")]
    assert again.ledger[2].why == "the app was not running" and again.ledger[2].ran_at is None
    assert [n for n, _ in ran2] == [5, 6]  # never run late after the fact
    with pytest.raises(ValueError, match="only an interrupted mission"):
        s2.carry_on(again)  # done now: never restarted


def test_a_plan_out_of_bounds_is_refused_with_the_reason():
    clock = Clock()
    with pytest.raises(ValueError, match="at least 60 s apart"):
        plan("r4", "segment", 30.0, 600.0, PIN, clock(), OPS)
    with pytest.raises(ValueError, match="at most 4 hours"):
        plan("r4", "segment", 600.0, MISSION_MAX_S + 1, PIN, clock(), OPS)
    with pytest.raises(ValueError, match="one of: segment"):
        plan("r4", "find", 600.0, 3600.0, PIN, clock(), OPS)
    with pytest.raises(ValueError, match="no live view"):
        plan("r4", "segment", 600.0, 3600.0, {}, clock(), OPS)
    with pytest.raises(ValueError, match="at least one slot"):
        plan("r4", "segment", 600.0, 300.0, PIN, clock(), OPS)
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValueError, match="numbers of seconds"):
            plan("r4", "segment", bad, 3600.0, PIN, clock(), OPS)


def test_a_hand_edited_ledger_keeps_only_rows_in_order_and_whole():
    rows = [{"n": 0, "due": 1000.0, "operation": "segment", "outcome": "done"},
            {"n": 1, "due": 1060.0, "operation": "segment", "outcome": "invented"},  # an outcome there is not
            {"n": 1, "due": "soon", "operation": "segment", "outcome": "done"},  # a time that isn't one
            {"n": 1, "due": 1060.0, "operation": "segment", "outcome": "missed", "why": "x"},
            {"n": 3, "due": 1180.0, "operation": "segment", "outcome": "done"}]  # out of order: slot 2 has no row
    good = {"operation": "segment", "everyS": 60, "windowS": 300, "startedAt": 1000.0, "pinned": PIN, "status": "done",
            "convention": "t0+", "ledger": rows}
    m = Mission.from_wire("r4", good)
    assert [(r.n, r.outcome) for r in m.ledger] == [(0, "done"), (1, "missed")] and m.status == "done"
    assert Slot(1, 1.0, "segment", "done").row() == {"n": 1, "due": 1.0, "operation": "segment", "outcome": "done"}
    for broken in ({"everyS": 0}, {"everyS": "x"}, {"windowS": float("nan")}, {"convention": "t1+"}, {"status": "odd"}):
        got = Mission.from_wire("r4", {**good, **broken})
        assert got is None or (broken == {"status": "odd"} and got.status == "interrupted"), broken


# --- the app's side: a researcher's mission through its hands -------------------------------------------------------
from tests.test_population import world  # noqa: E402,F401  (the fixture: cells over a textured slide)


def researcher_with_missions(world, tmp_path, clock):
    from orchestrator.research import ResearchManager
    from tests.test_research_samples import DAY, session

    manager = ResearchManager(world, root=tmp_path / "research", today=lambda: DAY)
    manager.missions = Missions(manager._mission_slot, manager._mission_field, manager._mission_saved,
                                clock=clock, sleep=clock.sleep, ends=manager._mission_ends)
    s = session(day=DAY)
    manager.sessions[s.rid] = s
    return manager, s


def test_a_mission_runs_through_the_researchers_hands_and_its_ledger_is_kept_with_it(world, tmp_path):
    """Five slots of segment (more than a run's 3 operations: a mission's slots are the app's schedule), each a
    population made for the researcher, its row saved atomically with the researcher as it happens (with the frame's
    own time), and read back after a restart as interrupted."""
    import json

    from orchestrator.evidence import evidence_of
    from orchestrator.research import ResearchManager

    clock = Clock()
    manager, s = researcher_with_missions(world, tmp_path, clock)

    async def go():
        mission = manager.start_mission(s.rid, "segment", 60.0, 240.0)
        await manager.missions.tasks[s.rid]
        return mission

    m = asyncio.run(go())
    assert m.pinned["epoch"] == world.latest_info.epoch and m.pinned["run"]
    assert [(r.n, r.outcome) for r in m.ledger] == [(n, "done") for n in range(5)], m.wire()
    made = [evidence_of(world).get(r.entry) for r in m.ledger]
    assert all(p is not None and p.made_for == s.name for p in made) and m.ledger[0].count == len(made[0].live())
    assert m.ledger[0].capture["frame"] == made[0].capture_id and m.ledger[0].capture["at"] == made[0].view["at"]
    assert len([r for r in s.ran if r.get("slot")]) == 5  # never limited to a run's 3
    folder = tmp_path / "research" / s.day / s.rid
    saved = json.loads((folder / "session.json").read_text())
    assert saved["mission"]["status"] == "done" and len(saved["mission"]["ledger"]) == 5
    assert not list(folder.glob("*.tmp"))  # written beside it, then moved over it (#196 review SF4)
    s.engine = "gpt-6.1-sol"
    s.mission.status = "watching"  # as if the app stopped mid-mission
    manager._save(s)
    again = ResearchManager(world, root=tmp_path / "research", today=lambda: s.day).get(s.rid)
    assert again.mission.status == "interrupted" and len(again.mission.ledger) == 5
    data = json.loads((folder / "session.json").read_text())
    data["mission"]["everyS"] = 0  # a hand-edited mission: dropped, never its researcher (#196 review SF2)
    (folder / "session.json").write_text(json.dumps(data))
    kept = ResearchManager(world, root=tmp_path / "research", today=lambda: s.day).get(s.rid)
    assert kept is not None and kept.mission is None


def test_a_mission_never_measures_off_its_field_and_is_refused_without_a_live_view(world, tmp_path):
    from types import SimpleNamespace

    clock = Clock()
    manager, s = researcher_with_missions(world, tmp_path, clock)

    async def go():
        mission = manager.start_mission(s.rid, "segment", 60.0, 120.0)
        world.frame(500.0, 0.0, epoch=1)  # continuity lost: another field, before even the baseline
        await manager.missions.tasks[s.rid]
        return mission

    m = asyncio.run(go())
    assert [r.outcome for r in m.ledger] == ["not comparable"] * 3 and not [r for r in s.ran if r.get("slot")]
    assert "lost continuity" in m.ledger[0].why
    other, s2 = researcher_with_missions(world, tmp_path / "two", clock)

    async def twice():
        first = other.start_mission(s2.rid, "segment", 60.0, 600.0)
        for _ in range(5):
            await asyncio.sleep(0)  # it has started: waiting for its next slot
        with pytest.raises(Exception, match="already has a mission running"):
            other.start_mission(s2.rid, "segment", 60.0, 600.0)
        assert s2.mission is first  # the running one's ledger is never replaced by the refused one
        other.missions.cancel(s2.rid)
        again = other.start_mission(s2.rid, "segment", 60.0, 600.0)  # a cancelled one is kept, with its ledger
        assert s2.past_missions == [first] and s2.mission is again and "pastMissions" in s2.wire()
        for _ in range(20):
            await asyncio.sleep(0)  # the cancelled one's task ends meanwhile
        assert other.missions.active.get(s2.rid) is again  # never taken off by the old one's end
        other.missions.cancel(s2.rid)
        s2.mission.status = "interrupted"
        with pytest.raises(Exception, match="interrupted by the app stopping"):
            other.start_mission(s2.rid, "segment", 60.0, 600.0)
        s2.mission.status = "cancelled"
        fresh = world.source
        world.source = SimpleNamespace(get=fresh.get, is_fresh=lambda: False)
        with pytest.raises(Exception, match="no fresh picture"):  # a dead camera pins nothing (#196 review SF7)
            other.start_mission(s2.rid, "segment", 60.0, 600.0)
        world.source = fresh
        world.latest_info = None
        with pytest.raises(Exception, match="no live view"):
            other.start_mission(s2.rid, "segment", 60.0, 600.0)

    asyncio.run(twice())


def test_midnight_never_ends_a_mission_and_another_session_makes_its_slots_not_comparable(world, tmp_path):
    """ADR 0017: a session spans days, so midnight does nothing to a mission (it was "a new day ends it", #196 review
    SF5). While another session is open its slots are "not comparable", said why (the live view is that session's),
    and they run again once its own session is open again; nothing ends it early but a cancel or the app stopping."""
    clock = Clock()
    manager, s = researcher_with_missions(world, tmp_path, clock)
    own = s.sitting
    manager.today = lambda: s.day if clock() < 1030 else "2026-10-08"  # midnight, after the first slot
    manager.session = lambda: own + ".2" if 1050 <= clock() < 1150 else own  # another session, for two slots

    async def go():
        mission = manager.start_mission(s.rid, "segment", 60.0, 240.0)
        await manager.missions.tasks[s.rid]
        return mission

    m = asyncio.run(go())
    assert [(r.n, r.outcome) for r in m.ledger] == [(0, "done"), (1, "not comparable"), (2, "not comparable"),
                                                   (3, "done"), (4, "done")], m.wire()
    assert m.ledger[1].why.startswith("another session was open")
    assert m.status == "done" and len([r for r in s.ran if r.get("slot")]) == 3


def test_a_slot_waits_while_another_researchers_operation_holds_the_app(world, tmp_path):
    """The app's one research operation at a time: a slot that finds it taken waits within its grace (never a failed
    row), and runs once it is free; one that can't get it within its grace is missed."""
    clock = Clock()
    manager, s = researcher_with_missions(world, tmp_path, clock)
    manager.hands.busy = "r9"

    async def go():
        mission = manager.start_mission(s.rid, "segment", 60.0, 60.0)
        for _ in range(10_000):  # bounded: a scheduler that never waits must fail, never hang
            if clock() >= 1075.0:
                break
            await asyncio.sleep(0)
        manager.hands.busy = None  # the other researcher's operation ended 15 s after slot 1 was due
        await manager.missions.tasks[s.rid]
        return mission

    m = asyncio.run(go())
    assert m.ledger[0].outcome == "missed" and "held the app's one slot" in m.ledger[0].why
    assert m.ledger[1].outcome == "done" and 1075.0 <= m.ledger[1].ran_at <= 1078.0, m.wire()


def test_a_save_that_fails_half_way_leaves_the_last_whole_file(world, tmp_path, monkeypatch):
    """#196 review SF4: written beside it, then moved over it; a write that dies leaves the file as it was."""
    import json
    from pathlib import Path

    clock = Clock()
    manager, s = researcher_with_missions(world, tmp_path, clock)
    manager._save(s)
    folder = tmp_path / "research" / s.day / s.rid
    before = (folder / "session.json").read_text()
    real = Path.write_text

    def dies(self, text, *a, **k):
        real(self, text[: len(text) // 2], *a, **k)  # half of it, then the app stops
        raise OSError("stopped mid-write")

    monkeypatch.setattr(Path, "write_text", dies)
    s.brief = "changed"
    with pytest.raises(OSError):
        manager._save(s)
    monkeypatch.setattr(Path, "write_text", real)
    assert (folder / "session.json").read_text() == before and json.loads(before)


def test_a_slot_whose_operation_ran_on_another_view_is_not_comparable(world, tmp_path):
    """#196 review SF8: the view the operation took is checked against the pin too; its entry is kept, never compared."""
    clock = Clock()
    manager, s = researcher_with_missions(world, tmp_path, clock)
    real = manager.hands.run

    async def run(session, operation, why="", **kw):
        reply = await real(session, operation, why, **kw)
        if reply.get("ok"):
            reply["view"] = {**reply["view"], "epoch": 9}  # the frame it took was on another view of the slide
        return reply

    manager.hands.run = run

    async def go():
        mission = manager.start_mission(s.rid, "segment", 60.0, 60.0)
        await manager.missions.tasks[s.rid]
        return mission

    m = asyncio.run(go())
    assert [r.outcome for r in m.ledger] == ["not comparable", "not comparable"] and all(r.entry for r in m.ledger)
    assert m.ledger[0].why.startswith("the view it ran on: the slide's motion lost continuity")


def test_a_window_that_is_not_a_whole_number_of_periods_ends_at_the_last_slot_inside_it():
    """#196 review survivor: 150 s every 60 s is the baseline and two more (0, 60, 120), never a slot past the window."""
    m = plan("r4", "segment", 60.0, 150.0, PIN, 1000.0, OPS)
    assert m.slots == 3 and m.due(m.slots - 1) <= 1000.0 + 150.0


def test_the_objective_in_use_is_pinned_and_a_change_of_it_is_off_the_field(world, tmp_path):
    clock = Clock()
    manager, s = researcher_with_missions(world, tmp_path, clock)
    objective = {"x": 40}
    manager._objective = lambda: objective["x"]

    async def go():
        mission = manager.start_mission(s.rid, "segment", 60.0, 60.0)
        for _ in range(10_000):  # bounded: until the baseline's row is written
            if mission.ledger:
                break
            await asyncio.sleep(0)
        objective["x"] = 10  # the user turned to 10x after the baseline
        await manager.missions.tasks[s.rid]
        return mission

    m = asyncio.run(go())
    assert m.pinned["objective"] == 40 and m.ledger[0].outcome == "done"
    assert m.ledger[1].outcome == "not comparable" and "objective changed (pinned at 40x, now 10x)" in m.ledger[1].why
