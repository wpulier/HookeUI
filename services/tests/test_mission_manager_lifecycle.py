"""I7 manager adoption regressions over synthetic camera/worker boundaries.

The real ResearchManager constructor, its Missions instance, Hands, population
production, worker-message accounting and session readback run unchanged. Only
scheduler time and the external researcher process are controlled. These are not
physical-camera, real-model, browser, timing or V4 acceptance tests.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from tests.waiting import until_async

from orchestrator.evidence import evidence_of
from orchestrator.population import layer_of
from orchestrator.research import ResearchManager, ResearchRefused, Session
from orchestrator.research.missions import Mission
from tests.test_population import world  # noqa: F401 — existing synthetic camera/cell-finder boundary

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')

DAY = "2026-10-07"
INITIAL = {"title": "Initial hypothesis", "summary": "No scheduled samples read yet.",
           "one_liner": "An initial hypothesis", "significance": "low"}


class MissionClock:
    """Inject time into the existing scheduler; never replace it or its callbacks."""

    def __init__(self, world, pause_at=None):
        self.t = 1000.0
        self.world = world
        self.pause_at = pause_at
        self.resume = asyncio.Event()
        self.last_frame_slot = 0
        self.stamp(world.latest_info)

    def stamp(self, info):
        info.wall_at, info.seq, info.boot = self.t - 0.125, 4000 + info.id, "synthetic-camera-boot"

    def __call__(self):
        return self.t

    async def sleep(self, seconds):
        target = self.t + max(0.0, seconds)
        if self.pause_at is not None and target > self.pause_at:
            await self.resume.wait()
        self.t = target
        # A genuinely new fixture frame at each ten-minute boundary, with the
        # same geometry. Captures keep its own timestamp, not model completion.
        slot = int((self.t - 1000.0) // 600)
        if slot > self.last_frame_slot:
            self.last_frame_slot = slot
            self.stamp(self.world.frame(0.0, 0.0, wall=self.t - 0.125))
        await asyncio.sleep(0)


class WorkerMessages:
    """Synthetic external process messages; all manager transitions are real."""

    alive = True

    def __init__(self, manager, mode="success", ask_first_checkpoint=False):
        self.manager = manager
        self.mode = mode
        self.ask_first_checkpoint = ask_first_checkpoint
        self.jobs = []
        self.pending = []
        self.release = asyncio.Event()

    async def call(self, operation, **job):
        if operation == "cancel":
            self.manager._event({"rid": job["rid"], "event": "done", "status": "cancelled"})
            return {}
        assert operation == "spawn"
        session = self.manager.get(job["rid"])
        mission = session.mission
        record = {**job, "phase": mission.status if mission else None,
                  "rows": len(mission.ledger) if mission else 0,
                  "committed": self.manager.spending.committed(),
                  "inbox": (Path(job["dir"]) / "inbox.jsonl").read_text()
                  if (Path(job["dir"]) / "inbox.jsonl").exists() else ""}
        self.jobs.append(record)
        self.manager._event({"rid": session.rid, "event": "status", "status": "running"})
        self.pending.append(asyncio.create_task(self.complete(record)))
        return {}

    async def complete(self, job):
        # Let the real _launch install its watchdog before a reply closes it.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        if self.mode == "hold" or (self.mode == "hold_checkpoint" and job["phase"] == "checkpoint"):
            await self.release.wait()
        session = self.manager.get(job["rid"])
        if session.status not in ("starting", "running"):
            return  # a cancellation never manufactures a later successful finding
        if self.ask_first_checkpoint and job["phase"] == "checkpoint" and not session.requests:
            self.manager._event({"rid": session.rid, "event": "request", "id": "q1",
                                 "what": "10 s of this held field", "why": "compare the movement", "seconds": 10})
        status = "done"
        result = {"title": "Synthetic worker finding", "summary": f"Read {job['rows']} scheduled rows.",
                  "one_liner": f"Read {job['rows']} rows", "significance": "low",
                  "evidence": [row.entry for row in (session.mission.ledger if session.mission else []) if row.entry]}
        if job["phase"] == "finishing" and self.mode == "partial_final":
            folder = Path(job["dir"])
            (folder / "partial.jpg").write_bytes(self.manager.hub.source.get(self.manager.hub.latest_info.id).jpeg)
            self.manager._event({"rid": session.rid, "event": "output", "kind": "image", "path": "partial.jpg",
                                 "caption": "Partial synthetic artifact", "generated": False})
            status, result = "timeout", None
        elif job["phase"] == "finishing" and self.mode == "failed_final":
            status, result = "failed", None
        self.manager._event({"rid": session.rid, "event": "done", "status": status, "result": result,
                             "spent": 0.01, "response_id": f"synthetic-{len(self.jobs)}"})

    def stop(self):
        self.alive = False
        for task in self.pending:
            task.cancel()


async def turns_until(predicate):
    """A bounded event-loop drain, then tests.waiting's wait; never a scheduler replacement.

    The manager writes each mission's snapshot in an executor thread before it spawns the worker, and thread work does
    not finish in a fixed number of loop turns: on Linux (the container run of 2026-10-21) every boundary was reached
    8-72 ms after 5000 turns, so turns alone failed 11 tests there. The wait's own clock and limit catch only a hang."""
    for _ in range(5000):
        if predicate():
            return
        await asyncio.sleep(0)
    await until_async(predicate, every=0.002, what="the synthetic worker/mission to reach the requested boundary")


def setup_manager(world, tmp_path, *, pause_at=None, mode="success", ask=False):
    manager = ResearchManager(world, root=tmp_path / "research", today=lambda: DAY)
    clock = MissionClock(world, pause_at)
    manager.missions.clock, manager.missions.mono, manager.missions.sleep = clock, clock, clock.sleep
    worker = WorkerMessages(manager, mode, ask)
    manager._worker = worker  # external-process transport boundary; no production class replacement
    return manager, clock, worker


def kept_researcher(manager, rid="r4", number=4):
    """Existing completed work; a mission must preserve it on refusal/cancel."""
    session = Session(rid=rid, n=number, brief="Watch this field", deliverable="research", effort="quick",
                      engine="gpt-6.1-sol", purpose="research", label="field", object_id=None,
                      origin={"channel": "voice", "thread": "original-conversation", "delegation_id": "original-delegation"},
                      status="done", day=DAY, result=dict(INITIAL), root=manager.root,
                      history=[{"brief": "Earlier unrelated question", "result": {"summary": "Earlier work"}, "status": "done"}])
    folder = manager.root / DAY / rid
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "initial.jpg").write_bytes(manager.hub.source.get(manager.hub.latest_info.id).jpeg)
    session.outputs = [{"id": "o1", "kind": "image", "path": "initial.jpg", "caption": "Initial retained picture",
                        "version": 1, "generated": False}]
    manager.sessions[rid] = session
    return session


async def cleanup(manager, worker):
    # Do not hide a failed assertion by leaving a long mission or watchdog behind.
    for task in manager.missions.tasks.values():
        if not task.done():
            task.cancel()
    await asyncio.gather(*manager.missions.tasks.values(), return_exceptions=True)
    worker.stop()
    await asyncio.gather(*worker.pending, return_exceptions=True)
    for task in manager._watchdogs.values():
        task.cancel()
    await asyncio.gather(*manager._watchdogs.values(), return_exceptions=True)


def assert_kept_initial(manager, session, before):
    assert (manager.root / DAY / session.rid / "initial.jpg").read_bytes() == before
    assert session.outputs[0]["id"] == "o1"
    assert session.history[0] == {"brief": "Earlier unrelated question", "result": {"summary": "Earlier work"}, "status": "done"}


def test_public_manager_runs_nineteen_real_slots_then_reads_checkpoints_and_a_fresh_final(world, tmp_path):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path)
        session = kept_researcher(manager)
        before = (manager.root / DAY / session.rid / "initial.jpg").read_bytes()
        try:
            mission = manager.start_mission(session.rid, "segment", 600, 10800)
            await manager.missions.tasks[session.rid]
            assert [(job["phase"], job["rows"]) for job in worker.jobs] == [
                ("checkpoint", 3), ("checkpoint", 6), ("checkpoint", 9), ("checkpoint", 12),
                ("checkpoint", 15), ("checkpoint", 18), ("finishing", 19)]
            assert mission.status == "done" and session.result["summary"] == "Read 19 scheduled rows."
            assert mission.grace == 120 and [row.due for row in mission.ledger] == [
                1000, 1600, 2200, 2800, 3400, 4000, 4600, 5200, 5800, 6400,
                7000, 7600, 8200, 8800, 9400, 10000, 10600, 11200, 11800]
            assert [row.n for row in mission.ledger] == list(range(19))
            assert all(row.outcome == "done" for row in mission.ledger)
            assert len({row.capture["frame"] for row in mission.ledger}) == 19
            populations = [evidence_of(world).get(row.entry) for row in mission.ledger]
            assert all(population is not None and population.made_for == session.name for population in populations)
            for row, population in zip(mission.ledger, populations):
                assert row.capture["frame"] == population.capture_id
                assert row.capture["at"] == population.view["at"] == row.due - 0.125
                assert row.capture["boot"] == "synthetic-camera-boot"
                assert row.capture["seq"] == 4000 + population.capture_id
            assert session.result["evidence"] == [row.entry for row in mission.ledger]
            assert layer_of(world).population() is None  # evidence was never presented as a user's overlay
            assert manager.spending.committed() == pytest.approx(0.07)
            assert_kept_initial(manager, session, before)
            saved = ResearchManager(world, root=manager.root, today=lambda: DAY).get(session.rid)
            assert saved.mission.wire()["ledger"] == mission.wire()["ledger"]
            assert saved.result == session.result and saved.outputs[0]["id"] == "o1"
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_maximum_scheduled_model_budget_is_admitted_up_front_without_spawning_on_refusal(world, tmp_path):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path)
        session = kept_researcher(manager)
        world.settings.research_cap = 1.74  # seven quick model runs need $1.75, before any actual spend
        try:
            with pytest.raises(ResearchRefused, match="budget"):
                manager.start_mission(session.rid, "segment", 600, 10800)
            assert session.mission is None and session.result == INITIAL and not worker.jobs
            assert manager.spending.committed() == 0
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_maximum_reservation_counts_once_releases_unused_and_initial_worker_is_separate(world, tmp_path):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path)
        session = kept_researcher(manager)
        world.settings.research_cap = 2.0
        try:
            mission = manager.start_mission(session.rid, "segment", 600, 10800)
            assert manager.spending.committed() == pytest.approx(1.75)
            await manager.missions.tasks[session.rid]
            assert worker.jobs[0]["committed"] == pytest.approx(1.75)
            assert manager.spending.committed() == pytest.approx(0.07)
            # Positive external-channel control: the real public start path and
            # initial worker accounting still work after the mission has ended.
            started = await manager.start("Look into this field", effort="quick")
            await turns_until(lambda: started.status == "done")
            assert worker.jobs[-1]["phase"] is None and started.result["summary"] == "Read 0 scheduled rows."
            assert manager.spending.committed() == pytest.approx(0.08)
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("mode, terminal", [("partial_final", "partial"), ("failed_final", "failed")])
def test_partial_or_failed_worker_final_cannot_complete_the_mission(world, tmp_path, mode, terminal):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path, mode=mode)
        session = kept_researcher(manager)
        before = (manager.root / DAY / session.rid / "initial.jpg").read_bytes()
        try:
            mission = manager.start_mission(session.rid, "segment", 60, 120)
            await manager.missions.tasks[session.rid]
            assert any(job["phase"] == "finishing" for job in worker.jobs)
            assert mission.status == "failed" and terminal in mission.why_ended
            assert session.status == terminal
            assert len(mission.ledger) == 3 and all(row.outcome == "done" for row in mission.ledger)
            if terminal == "partial":
                assert session.outputs[-1]["path"] == "partial.jpg" and session.result
                assert (manager.root / DAY / session.rid / "partial.jpg").read_bytes()
            else:
                assert session.result is None
            assert manager.spending.committed() == pytest.approx(session.spent + session.spent_estimated)
            assert_kept_initial(manager, session, before)
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_lowered_cap_refuses_final_without_relabelling_prior_work_or_losing_source(world, tmp_path):
    async def scenario():
        manager, clock, worker = setup_manager(world, tmp_path, pause_at=1000)
        session = kept_researcher(manager)
        world.settings.research_cap = 1.0
        try:
            mission = manager.start_mission(session.rid, "segment", 60, 120)
            await turns_until(lambda: len(mission.ledger) == 1)
            entry = mission.ledger[0].entry
            world.settings.research_cap = 0
            clock.resume.set()
            await manager.missions.tasks[session.rid]
            assert mission.status == "failed" and "budget" in mission.why_ended
            assert not worker.jobs and session.result == INITIAL
            assert evidence_of(world).get(entry) is not None
            assert manager.spending.committed() == 0
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_idle_cancellation_preserves_completed_evidence_and_another_mission(world, tmp_path):
    async def scenario():
        manager, clock, worker = setup_manager(world, tmp_path, pause_at=1000)
        first = kept_researcher(manager)
        sibling = kept_researcher(manager, "r5", 5)
        before = (manager.root / DAY / first.rid / "initial.jpg").read_bytes()
        try:
            a = manager.start_mission(first.rid, "segment", 600, 1800)
            await turns_until(lambda: len(a.ledger) == 1)
            b = manager.start_mission(sibling.rid, "segment", 600, 1800)
            await turns_until(lambda: len(b.ledger) == 1)
            entry = a.ledger[0].entry
            assert first.status == "done"  # worker is idle, but scheduled work is active
            assert await manager.cancel(first.rid)
            assert a.status == "cancelled" and len(a.ledger) == 1
            assert evidence_of(world).get(entry) is not None and b.status == "watching"
            assert_kept_initial(manager, first, before)
            clock.resume.set()
            await manager.missions.tasks[sibling.rid]
            assert b.status == "done" and len(b.ledger) == 4
            assert sibling.result["summary"] == "Read 4 scheduled rows."
            assert len(a.ledger) == 1
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_a_restored_interrupted_mission_can_be_cancelled_without_restarting_paid_work(world, tmp_path):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path, pause_at=1000)
        session = kept_researcher(manager)
        try:
            mission = manager.start_mission(session.rid, "segment", 600, 1800)
            await turns_until(lambda: len(mission.ledger) == 1)
            restored = ResearchManager(world, root=manager.root, today=lambda: DAY)
            restored_worker = WorkerMessages(restored)
            restored._worker = restored_worker
            back = restored.get(session.rid)
            assert back.mission.status == "interrupted" and not restored.missions.tasks
            assert await restored.cancel(back.rid)
            assert back.mission.status == "cancelled" and len(back.mission.ledger) == 1
            assert not restored_worker.jobs and back.result == INITIAL and back.outputs[0]["id"] == "o1"
            reread = ResearchManager(world, root=manager.root, today=lambda: DAY).get(back.rid)
            assert reread.mission.status == "cancelled"
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_notes_between_checkpoints_reach_the_next_real_worker_inbox(world, tmp_path):
    async def scenario():
        manager, clock, worker = setup_manager(world, tmp_path, pause_at=1000)
        session = kept_researcher(manager)
        try:
            mission = manager.start_mission(session.rid, "segment", 600, 1800)
            await turns_until(lambda: len(mission.ledger) == 1)
            manager.note(session.rid, "Compare sizes, not just the count")
            clock.resume.set()
            await manager.missions.tasks[session.rid]
            assert worker.jobs[0]["phase"] == "checkpoint"
            assert "Compare sizes, not just the count" in worker.jobs[0]["inbox"]
            assert mission.status == "done"
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("broken", [
    {"pinned": 7}, {"pinned": ["bad pin"]}, {"ledger": 7},
    {"ledger": [{"n": 0, "due": 1000, "outcome": "done"}]},
    {"checkpointEvery": float("inf")}, {"checkpointEvery": float("nan")},
], ids=["scalar-pin", "list-pin", "scalar-ledger", "missing-row-operation", "infinite-checkpoint", "nan-checkpoint"])
def test_bad_saved_mission_never_drops_its_researcher_outputs_or_valid_sibling(world, tmp_path, broken):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path, pause_at=1000)
        session = kept_researcher(manager)
        try:
            mission = manager.start_mission(session.rid, "segment", 600, 1800)
            await turns_until(lambda: len(mission.ledger) == 1)
            path = manager.root / DAY / session.rid / "session.json"
            data = json.loads(path.read_text())
            data["mission"].update(broken)
            path.write_text(json.dumps(data))
            sibling = {**data, "rid": "r5", "n": 5, "mission": mission.wire()}
            sibling_folder = manager.root / DAY / "r5"
            sibling_folder.mkdir()
            (sibling_folder / "session.json").write_text(json.dumps(sibling))
            restored = ResearchManager(world, root=manager.root, today=lambda: DAY)
            back, good = restored.get(session.rid), restored.get("r5")
            assert back is not None and back.result == INITIAL and back.outputs[0]["id"] == "o1"
            assert good is not None and good.mission.status == "interrupted" and len(good.mission.ledger) == 1
            assert not restored.missions.tasks and not worker.jobs
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_a_late_answer_to_an_earlier_checkpoint_keeps_its_exact_requester_and_question(world, tmp_path):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path, ask=True)
        session = kept_researcher(manager)
        sibling = kept_researcher(manager, "r5", 5)
        try:
            mission = manager.start_mission(session.rid, "segment", 600, 3600)
            await manager.missions.tasks[session.rid]
            assert session.requests and session.requests[0]["status"] == "unanswered"
            question = session.requests[0]
            assert question["run"] < len(session.history)
            await manager.carry_on(session.rid, question["id"], "Yes, use the held field",
                                   origin={"channel": "chat", "thread": "later-conversation"},
                                   words="Yes, use the held field", asked_with="Yes, use the held field")
            await turns_until(lambda: session.status == "done")
            assert question["status"] == "answered" and question["late"]
            assert worker.jobs[-1]["rid"] == session.rid and question["id"] in worker.jobs[-1]["brief"]
            assert sibling.result == INITIAL and not sibling.requests and sibling.status == "done"
            assert mission.status == "done"  # answering a question does not restart scheduled slots
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_initial_worker_positive_control_uses_real_start_messages_and_releases_only_unused_money(world, tmp_path):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path)
        world.settings.research_cap = 0.25
        try:
            session = await manager.start("Look into this field", effort="quick")
            assert manager.spending.committed() == pytest.approx(0.25)
            await turns_until(lambda: session.status == "done")
            assert len(worker.jobs) == 1 and worker.jobs[0]["phase"] is None
            assert session.result["summary"] == "Read 0 scheduled rows."
            assert manager.spending.committed() == pytest.approx(0.01)
            restored = ResearchManager(world, root=manager.root, today=lambda: DAY).get(session.rid)
            assert restored.result == session.result and restored.spent == pytest.approx(0.01)
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("cap, admitted", [(1.99, False), (2.0, True)], ids=["initial-plus-mission-refused", "exact-full-budget"])
def test_initial_paid_run_and_all_scheduled_runs_share_the_full_daily_budget(world, tmp_path, cap, admitted):
    async def scenario():
        manager, _, worker = setup_manager(world, tmp_path, mode="hold")
        world.settings.research_cap = cap
        try:
            session = await manager.start("Look into this field", effort="quick")
            await turns_until(lambda: session.status == "running")
            assert len(worker.jobs) == 1 and manager.spending.committed() == pytest.approx(0.25)
            if not admitted:
                with pytest.raises(ResearchRefused, match="budget"):
                    manager.start_mission(session.rid, "segment", 600, 10800)
                assert session.mission is None and session.status == "running"
                assert manager.spending.committed() == pytest.approx(0.25)
            else:
                manager.start_mission(session.rid, "segment", 600, 10800)
                assert manager.spending.committed() == pytest.approx(2.0)
            assert len(worker.jobs) == 1  # requested mission never duplicates the already-paid initial run
            assert await manager.cancel(session.rid)
            assert session.status == "cancelled"
            assert manager.spending.committed() == 0
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_cancellation_during_a_real_checkpoint_stops_future_slots_and_preserves_their_source(world, tmp_path):
    async def scenario():
        manager, clock, worker = setup_manager(world, tmp_path, mode="hold_checkpoint")
        session = kept_researcher(manager)
        before = (manager.root / DAY / session.rid / "initial.jpg").read_bytes()
        try:
            mission = manager.start_mission(session.rid, "segment", 600, 3600)
            await turns_until(lambda: bool(worker.jobs) and worker.jobs[-1]["phase"] == "checkpoint")
            assert mission.status == "checkpoint" and len(mission.ledger) == 3
            kept = [(row.entry, row.capture.copy()) for row in mission.ledger]
            assert await manager.cancel(session.rid)
            assert mission.status == "cancelled" and len(mission.ledger) == 3
            clock.t += 3600
            worker.release.set()
            await asyncio.gather(*worker.pending)
            await asyncio.sleep(0)
            assert len(mission.ledger) == 3 and len(worker.jobs) == 1
            for entry, source in kept:
                population = evidence_of(world).get(entry)
                assert population.capture_id == source["frame"] and population.view["at"] == source["at"]
            assert_kept_initial(manager, session, before)
            assert manager.spending.committed() == pytest.approx(session.spent + session.spent_estimated)
        finally:
            await cleanup(manager, worker)
    asyncio.run(scenario())
