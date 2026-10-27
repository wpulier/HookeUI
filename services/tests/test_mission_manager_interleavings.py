"""I7 review regressions: real manager, scheduler, hands and worker-message paths.

Synthetic camera/cell-finder and external worker messages are controlled. No
mission callback is replaced. These do not certify real models, UI or V4.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orchestrator.research import ResearchManager, ResearchRefused
from tests.test_mission_manager_lifecycle import (DAY, MissionClock, cleanup,
                                                 kept_researcher, turns_until)
from tests.test_population import world  # noqa: F401

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')


class ControlledWorker:
    """Hold external replies while the real manager decides dispatch and completion."""

    alive = True

    def __init__(self, manager):
        self.manager = manager
        self.jobs = []
        self.pending = []
        self.cancelled = []

    async def call(self, operation, **job):
        if operation == "cancel":
            self.cancelled.append(job["rid"])
            self.manager._event({"rid": job["rid"], "event": "done", "status": "cancelled"})
            return {}
        assert operation == "spawn"
        session = self.manager.get(job["rid"])
        self.jobs.append({**job, "history_len": len(session.history), "origin": dict(session.origin),
                          "committed": self.manager.spending.committed()})
        self.manager._event({"rid": session.rid, "event": "status", "status": "running"})
        return {}

    def question(self, job, what):
        self.manager._event({"rid": job["rid"], "event": "request", "what": what,
                             "why": "Synthetic evidence request", "seconds": 10})
        return self.manager.get(job["rid"]).requests[-1]

    def artifact(self, job):
        folder = Path(job["dir"])
        pixels = self.manager.hub.source.get(self.manager.hub.latest_info.id).jpeg
        (folder / "held-partial.jpg").write_bytes(pixels)
        self.manager._event({"rid": job["rid"], "event": "output", "kind": "image",
                             "path": "held-partial.jpg", "caption": "Synthetic partial artifact", "generated": False})
        return pixels

    def finish(self, job, summary, status="done"):
        result = {"title": summary, "summary": summary, "one_liner": summary} if status == "done" else None
        self.manager._event({"rid": job["rid"], "event": "done", "status": status, "result": result,
                             "spent": 0.01, "response_id": f"synthetic-controlled-{len(self.jobs)}"})

    def stop(self):
        self.alive = False


class PollBarrier:
    """Only the scheduler clock dependency is held; callbacks and transitions remain real."""

    def __init__(self, clock):
        self.clock = clock
        self.hold = False
        self.release = asyncio.Event()

    async def sleep(self, seconds):
        if self.hold:
            await self.release.wait()
        await self.clock.sleep(seconds)


def controlled_manager(world, tmp_path):
    manager = ResearchManager(world, root=tmp_path / "research", today=lambda: DAY)
    clock = MissionClock(world)
    barrier = PollBarrier(clock)
    manager.missions.clock, manager.missions.mono, manager.missions.sleep = clock, clock, barrier.sleep
    worker = ControlledWorker(manager)
    manager._worker = worker
    return manager, worker, barrier


async def dispatched(manager, worker, index, mission=None):
    # An early failed mission is observed rather than hidden behind a drain timeout.
    await turns_until(lambda: len(worker.jobs) > index
                      or (mission is not None and manager.missions.tasks[mission.rid].done()))
    assert len(worker.jobs) > index, mission.why_ended if mission is not None else "no dispatch"
    job = worker.jobs[index]
    await turns_until(lambda: job["rid"] in manager._watchdogs)
    return job


async def complete_plan(manager, worker, session, mission, start, label):
    """Three real slots; checkpoint after one and two, then a distinct final finding."""
    records = []
    for offset, phase in enumerate(("checkpoint", "checkpoint", "finishing")):
        job = await dispatched(manager, worker, start + offset, mission)
        question = worker.question(job, f"{label} {phase} {offset} held field")
        assert question["missionId"] == mission.id
        assert question["origin"] == mission.origin
        assert question["run"] == job["history_len"]
        summary = f"{label} {phase} {offset} finding"
        records.append((summary, phase, job["history_len"]))
        worker.finish(job, summary)
    await manager.missions.tasks[session.rid]
    assert mission.status == "done"
    return records


@pytest.mark.parametrize("terminal", ["partial", "failed"])
def test_an_unrelated_success_between_polls_cannot_complete_a_failed_mission_final(world, tmp_path, terminal):
    async def scenario():
        manager, worker, barrier = controlled_manager(world, tmp_path)
        session = kept_researcher(manager)
        failures = []
        manager.on_mission_failed.append(lambda s, m, origin: failures.append((m.id, dict(origin))))
        try:
            mission = manager.start_mission(session.rid, "segment", 60, 120)
            final = await dispatched(manager, worker, 0, mission)
            assert mission.status == "finishing" and len(mission.ledger) == 3
            barrier.hold = True
            await asyncio.sleep(0)  # permit an existing one-second poll to enter the clock barrier
            if terminal == "partial":
                pixels = worker.artifact(final)
            worker.finish(final, "No final finding", "timeout" if terminal == "partial" else "failed")
            assert session.status == terminal
            await manager.followup(session.rid, "An unrelated user continuation", mode="continue",
                                   origin={"channel": "chat", "thread": "unrelated-user-thread"})
            other = await dispatched(manager, worker, 1)
            worker.finish(other, "Unrelated user success")
            assert session.result["summary"] == "Unrelated user success"
            barrier.release.set()
            await manager.missions.tasks[session.rid]
            assert mission.status == "failed" and terminal in mission.why_ended
            assert failures == [(mission.id, mission.origin)]
            assert session.result["summary"] == "Unrelated user success"
            if terminal == "partial":
                assert (Path(final["dir"]) / "held-partial.jpg").read_bytes() == pixels
            assert len(mission.ledger) == 3
            assert manager.spending.committed() == pytest.approx(0.02)
        finally:
            barrier.release.set()
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("producer", ["initial", "user-continue"])
def test_a_worker_already_running_at_a_checkpoint_keeps_its_own_question_origin_and_lifetime(world, tmp_path, producer):
    async def scenario():
        manager, worker, barrier = controlled_manager(world, tmp_path)
        actual_origin = {"channel": "chat", "thread": "ordinary-request", "delegation_id": "ordinary-delegation"}
        try:
            if producer == "initial":
                session = await manager.start("Look into this field", origin=actual_origin)
                actual_origin = dict(session.origin)  # includes the real admission's asked fact
            else:
                session = kept_researcher(manager)
                await manager.followup(session.rid, "Unrelated user question", mode="continue", origin=actual_origin)
            ordinary = await dispatched(manager, worker, 0)
            mission = manager.start_mission(session.rid, "segment", 60, 120, checkpoint_every=1)
            await turns_until(lambda: mission.status == "checkpoint" and len(mission.ledger) == 1)
            question = worker.question(ordinary, "Ordinary worker's own field")
            assert question.get("missionId") is None
            assert question["origin"] == actual_origin
            assert question["run"] == ordinary["history_len"]
            worker.finish(ordinary, "Ordinary result")
            await complete_plan(manager, worker, session, mission, 1, "own-mission")
            assert manager.open_question(session, question["id"]) is None
            own = session.requests[1]
            assert manager.open_question(session, own["id"]) is own
            prior = next(h for h in session.history if (h.get("result") or {}).get("summary") == "Ordinary result")
            assert prior.get("missionId") is None
        finally:
            barrier.release.set()
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_history_keeps_the_actual_initial_checkpoint_and_final_across_two_mission_occurrences(world, tmp_path):
    async def scenario():
        manager, worker, barrier = controlled_manager(world, tmp_path)
        original = {"channel": "voice", "thread": "first-request", "delegation_id": "first-delegation"}
        try:
            session = await manager.start("Look into this field", origin=original)
            initial = await dispatched(manager, worker, 0)
            worker.finish(initial, "Actual initial finding")
            first = manager.start_mission(session.rid, "segment", 60, 120, checkpoint_every=1)
            first_records = await complete_plan(manager, worker, session, first, 1, "first")
            second = manager.start_mission(session.rid, "segment", 60, 120, checkpoint_every=1)
            assert second.id != first.id
            second_records = await complete_plan(manager, worker, session, second, 4, "second")
            # Archive the second final via the same public continue path; no manual history fabrication.
            await manager.followup(session.rid, "An ordinary later question", mode="continue",
                                   origin={"channel": "chat", "thread": "later-request"})
            later = await dispatched(manager, worker, 7)
            worker.finish(later, "Ordinary later finding")
            restored = ResearchManager(world, root=manager.root, today=lambda: DAY).get(session.rid)
            by_summary = {(h.get("result") or {}).get("summary"): h for h in restored.history}
            assert by_summary["Actual initial finding"].get("missionId") is None
            for mission, records in ((first, first_records), (second, second_records)):
                for summary, phase, run in records:
                    entry = by_summary[summary]
                    assert entry["missionId"] == mission.id
                    assert entry["missionPhase"] == phase
                    assert entry["run"] == run
                    assert entry["origin"] == mission.origin
            assert restored.past_missions[0].id == first.id and restored.mission.id == second.id
            assert session.result["summary"] == "Ordinary later finding"
        finally:
            barrier.release.set()
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["checkpoint", "finishing"])
@pytest.mark.parametrize("artifact", [False, True], ids=["no-artifact", "partial-artifact"])
def test_real_watchdog_exhaustion_is_a_failed_mission_with_original_timeout_and_partial_truth(world, tmp_path, phase, artifact):
    async def scenario():
        manager, worker, barrier = controlled_manager(world, tmp_path)
        session = kept_researcher(manager)
        failures = []
        manager.on_mission_failed.append(lambda s, m, origin: failures.append((m.id, dict(origin))))
        try:
            mission = manager.start_mission(session.rid, "segment", 60, 120,
                                            checkpoint_every=1 if phase == "checkpoint" else 3)
            job = await dispatched(manager, worker, 0, mission)
            assert mission.status == phase
            kept = [(row.entry, dict(row.capture)) for row in mission.ledger]
            pixels = worker.artifact(job) if artifact else None
            manager._event({"rid": session.rid, "event": "cost", "spent": 0.02, "estimated": 0.005})
            # Execute the real watchdog body at an injected expired deadline. The transport
            # acknowledges cancellation; it does not invent a timeout completion itself.
            await manager._watchdog(session.rid, 0)
            await turns_until(lambda: manager.missions.tasks[session.rid].done())
            assert worker.cancelled == [session.rid]
            assert mission.status == "failed" and "timeout" in mission.why_ended
            assert session.status == ("partial" if artifact else "timeout")
            assert failures == [(mission.id, mission.origin)]
            assert [(row.entry, row.capture) for row in mission.ledger] == kept
            if artifact:
                assert session.outputs[-1]["path"] == "held-partial.jpg" and session.result
                assert (Path(job["dir"]) / "held-partial.jpg").read_bytes() == pixels
            else:
                assert session.result is None
            assert session.spent == pytest.approx(0.02) and session.spent_estimated == pytest.approx(0.005)
            assert manager.spending.committed() == pytest.approx(0.025)
        finally:
            barrier.release.set()
            await cleanup(manager, worker)
    asyncio.run(scenario())


def test_explicit_user_cancel_of_a_held_final_remains_cancelled_and_keeps_its_artifact(world, tmp_path):
    async def scenario():
        manager, worker, barrier = controlled_manager(world, tmp_path)
        session = kept_researcher(manager)
        failures = []
        manager.on_mission_failed.append(lambda *args: failures.append(args))
        try:
            mission = manager.start_mission(session.rid, "segment", 60, 120)
            job = await dispatched(manager, worker, 0, mission)
            pixels = worker.artifact(job)
            assert await manager.cancel(session.rid)
            assert mission.status == "cancelled" and session.status == "cancelled"
            assert not failures and len(worker.jobs) == 1
            assert (Path(job["dir"]) / "held-partial.jpg").read_bytes() == pixels
            assert len(mission.ledger) == 3 and manager.spending.committed() == 0
        finally:
            barrier.release.set()
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("deliverable", ["video", "lesson"])
def test_a_quoted_standard_deliverable_keeps_its_admitted_authority_for_scheduled_runs(world, tmp_path, deliverable):
    async def scenario():
        manager, worker, barrier = controlled_manager(world, tmp_path)
        words = f"Make a {deliverable} from this field's changes"
        world.settings.research_cap = 4.0  # initial $1 maximum plus three scheduled $1 maxima
        try:
            session = await manager.start(words, effort="standard", deliverable=deliverable,
                                          words=words, asked_with=words,
                                          origin={"channel": "chat", "thread": "quoted-deliverable"})
            initial = await dispatched(manager, worker, 0)
            mission = manager.start_mission(session.rid, "segment", 60, 120, checkpoint_every=1)
            assert manager.spending.committed() == pytest.approx(4.0)
            assert session.effort == "standard" and session.deliverable == deliverable
            worker.finish(initial, "Admitted initial finding")
            await complete_plan(manager, worker, session, mission, 1, "admitted")
            assert all(job["effort"] == "standard" and job["deliverable"] == deliverable for job in worker.jobs)
            assert len(worker.jobs) == 4 and manager.spending.committed() == pytest.approx(0.04)
            before = session.result.copy()
            with pytest.raises(ResearchRefused, match="asked|quote"):
                await manager.followup(session.rid, "Unrelated unquoted continuation", mode="continue")
            assert session.result == before and len(worker.jobs) == 4 and mission.status == "done"
        finally:
            barrier.release.set()
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("deliverable", ["video", "lesson"])
def test_a_valid_initial_quote_never_authorizes_an_ordinary_unquoted_paid_continuation(world, tmp_path, deliverable):
    async def scenario():
        manager, worker, barrier = controlled_manager(world, tmp_path)
        words = f"Make a {deliverable} about this field"
        try:
            session = await manager.start(words, effort="standard", deliverable=deliverable,
                                          words=words, asked_with=words)
            initial = await dispatched(manager, worker, 0)
            worker.finish(initial, "Authorized initial result")
            with pytest.raises(ResearchRefused, match="asked|quote"):
                await manager.followup(session.rid, "An unquoted unrelated continuation", mode="continue")
            assert session.result["summary"] == "Authorized initial result"
            assert session.status == "done" and len(worker.jobs) == 1
            assert manager.spending.committed() == pytest.approx(0.01)
        finally:
            barrier.release.set()
            await cleanup(manager, worker)
    asyncio.run(scenario())


class UnconfirmedCancelWorker(ControlledWorker):
    """Cancel acknowledgement/error supplies no terminal receipt or invented usage."""

    def __init__(self, manager, cancel_error=None):
        super().__init__(manager)
        self.cancel_error = cancel_error

    async def call(self, operation, **job):
        if operation == "cancel":
            self.cancelled.append(job["rid"])
            if self.cancel_error is not None:
                raise self.cancel_error("synthetic cancellation transport failure")
            return {}  # acknowledgement only: the worker still owns its live attempt
        return await super().call(operation, **job)

    def late_terminal(self, job):
        # Genuine later external receipt: more usage than the earlier cost report,
        # no successful finding, and no substitution with a replacement's result.
        self.manager._event({"rid": job["rid"], "event": "done", "status": "cancelled",
                             "spent": 0.03, "spent_estimated": 0.004,
                             "response_id": "synthetic-late-original-terminal"})


@pytest.mark.parametrize("phase", ["checkpoint", "finishing"])
@pytest.mark.parametrize("artifact", [False, True], ids=["no-artifact", "partial-artifact"])
def test_cancel_ack_without_terminal_bounds_mission_failure_but_keeps_live_worker_hold(world, tmp_path, phase, artifact):
    async def scenario():
        manager, _, barrier = controlled_manager(world, tmp_path)
        worker = UnconfirmedCancelWorker(manager)
        manager._worker = worker
        session = kept_researcher(manager)
        failures, finished = [], []
        manager.on_mission_failed.append(lambda s, m, origin: failures.append((m.id, dict(origin))))
        manager.on_done.append(lambda s: finished.append((s.rid, s.status)))
        watchdog = None
        try:
            mission = manager.start_mission(session.rid, "segment", 60, 120,
                                            checkpoint_every=1 if phase == "checkpoint" else 3)
            job = await dispatched(manager, worker, 0, mission)
            assert mission.status == phase
            task = manager.missions.tasks[session.rid]
            question = worker.question(job, "Original worker's still-held evidence request")
            attempt = session._attempt
            original_run = question["runId"]
            kept = [(row.entry, dict(row.capture)) for row in mission.ledger]
            initial = (manager.root / DAY / session.rid / "initial.jpg").read_bytes()
            pixels = worker.artifact(job) if artifact else None
            manager._event({"rid": session.rid, "event": "cost", "spent": 0.02, "estimated": 0.005})

            # Expire the actual watchdog; only the external cancellation transport
            # is synthetic. Advance the existing injected monotonic dependency past
            # any bounded termination grace, rather than waiting on a real clock or
            # replacing a mission callback/declaring an ACK to be a terminal fact.
            watchdog = asyncio.create_task(manager._watchdog(session.rid, 0))
            await turns_until(lambda: worker.cancelled == [session.rid])
            assert not attempt.completion.done() and session.status == "running"
            assert question["status"] == "asked" and not finished
            barrier.clock.t += 3600
            await turns_until(lambda: mission.status == "failed")
            failure_why = mission.why_ended
            assert "timeout" in failure_why and "termination" in failure_why
            assert failures == [(mission.id, mission.origin)]
            assert session.status == "running" and not attempt.completion.done() and not finished
            assert question["status"] == "asked"
            assert question["missionId"] == mission.id and question["origin"] == mission.origin
            assert mission.model_runs[0]["runId"] == original_run
            assert mission.model_runs[0].get("completedAt") is None
            assert manager.spending.committed() == pytest.approx(0.25)  # future capacity freed, live cap retained
            assert [(row.entry, row.capture) for row in mission.ledger] == kept
            assert (manager.root / DAY / session.rid / "initial.jpg").read_bytes() == initial
            if artifact:
                assert (Path(job["dir"]) / "held-partial.jpg").read_bytes() == pixels
            saved = ResearchManager(world, root=manager.root, today=lambda: DAY).get(session.rid)
            assert saved.mission.status == "failed" and saved.mission.why_ended == failure_why
            assert saved.mission.model_runs[0].get("completedAt") is None

            worker.late_terminal(job)
            await turns_until(lambda: task.done())
            await asyncio.gather(task, return_exceptions=True)
            assert attempt.completion.done() and len(finished) == 1
            assert session.status == ("partial" if artifact else "timeout")
            assert session.result_run["runId"] == original_run and session.result_run["cause"] == "timeout"
            assert mission.model_runs[0]["cause"] == "timeout"
            assert mission.model_runs[0]["status"] == session.status
            assert mission.model_runs[0].get("completedAt") is not None
            assert mission.status == "failed" and mission.why_ended == failure_why
            assert failures == [(mission.id, mission.origin)] and worker.cancelled == [session.rid]
            assert question["status"] == "unanswered"
            assert session.spent == pytest.approx(0.03) and session.spent_estimated == pytest.approx(0.004)
            assert manager.spending.committed() == pytest.approx(0.034)
            saved = ResearchManager(world, root=manager.root, today=lambda: DAY).get(session.rid)
            assert saved.mission.status == "failed" and saved.mission.why_ended == failure_why
            assert saved.result_run["runId"] == original_run and saved.result_run["cause"] == "timeout"
            assert [(row.entry, row.capture) for row in saved.mission.ledger] == kept
        finally:
            barrier.release.set()
            if watchdog is not None:
                watchdog.cancel()
                await asyncio.gather(watchdog, return_exceptions=True)
            await cleanup(manager, worker)
    asyncio.run(scenario())


@pytest.mark.parametrize("phase", ["checkpoint", "finishing"])
@pytest.mark.parametrize("cancel_error", [RuntimeError, TimeoutError], ids=["rpc-error", "rpc-timeout"])
def test_failed_cancel_then_replacement_and_late_old_terminal_keeps_past_mission_failed(world, tmp_path, phase, cancel_error):
    async def scenario():
        manager, _, barrier = controlled_manager(world, tmp_path)
        worker = UnconfirmedCancelWorker(manager, cancel_error)
        manager._worker = worker
        session = kept_researcher(manager)
        failures = []
        manager.on_mission_failed.append(lambda s, m, origin: failures.append((m.id, dict(origin))))
        old_task = None
        try:
            old = manager.start_mission(session.rid, "segment", 60, 120,
                                        checkpoint_every=1 if phase == "checkpoint" else 3)
            old_job = await dispatched(manager, worker, 0, old)
            assert old.status == phase
            old_task = manager.missions.tasks[session.rid]
            old_question = worker.question(old_job, "Old occurrence's original held request")
            old_run = old_question["runId"]
            pixels = worker.artifact(old_job)
            kept = [(row.entry, dict(row.capture)) for row in old.ledger]
            manager._event({"rid": session.rid, "event": "cost", "spent": 0.02, "estimated": 0.005})
            await manager._watchdog(session.rid, 0)
            assert old.status == "failed" and "timeout" in old.why_ended and "termination" in old.why_ended
            failure_why = old.why_ended
            assert failures == [(old.id, old.origin)]
            assert session.status == "running" and not session._attempt.completion.done()
            assert manager.spending.committed() == pytest.approx(0.25)

            # The public admission permits replacing the ended mission, but the
            # actual worker remains held. Hold only the scheduler's clock wait so
            # late original completion is observed before a replacement dispatch.
            barrier.hold = True
            replacement = manager.start_mission(session.rid, "segment", 60, 120, checkpoint_every=1)
            assert replacement.id != old.id and session.past_missions == [old]
            await turns_until(lambda: replacement.status == "checkpoint")
            assert len(worker.jobs) == 1 and session._attempt.id == old_run
            assert manager.spending.committed() == pytest.approx(1.0)  # old $0.25 live cap + new three $0.25 maxima
            worker.late_terminal(old_job)
            await turns_until(lambda: old_task.done())
            await asyncio.gather(old_task, return_exceptions=True)
            assert session.mission is replacement and replacement.status == "checkpoint"
            assert session.past_missions[0] is old and old.status == "failed" and old.why_ended == failure_why
            assert failures == [(old.id, old.origin)] and worker.cancelled == [session.rid]
            assert session.status == "partial" and session.result_run["runId"] == old_run
            assert session.result_run["missionId"] == old.id and session.result_run["cause"] == "timeout"
            assert old_question["missionId"] == old.id and old_question["origin"] == old.origin
            assert old_question["status"] == "unanswered"
            assert old.model_runs[0]["runId"] == old_run and old.model_runs[0]["cause"] == "timeout"
            assert replacement.model_runs == [] and len(worker.jobs) == 1
            assert manager.spending.committed() == pytest.approx(0.784)  # true usage plus untouched new reservation
            assert [(row.entry, row.capture) for row in old.ledger] == kept
            assert (Path(old_job["dir"]) / "held-partial.jpg").read_bytes() == pixels
            assert session.wire()["pastMissions"][0]["status"] == "failed"
            assert session.wire()["pastMissions"][0]["whyEnded"] == failure_why

            # Real replacement workers and callbacks finish their own plan. Their
            # later saves must persist the old failure, never retrofit user cancel.
            barrier.release.set()
            records = await complete_plan(manager, worker, session, replacement, 1, "replacement")
            assert failures == [(old.id, old.origin)] and old.why_ended == failure_why
            assert session.mission is replacement and replacement.status == "done" and old.status == "failed"
            assert len(worker.jobs) == 4 and session.spent == pytest.approx(0.06)
            assert session.spent_estimated == pytest.approx(0.004)
            assert manager.spending.committed() == pytest.approx(0.064)
            saved = ResearchManager(world, root=manager.root, today=lambda: DAY).get(session.rid)
            assert saved.mission.id == replacement.id and saved.mission.status == "done"
            assert saved.past_missions[0].id == old.id and saved.past_missions[0].status == "failed"
            assert saved.past_missions[0].why_ended == failure_why
            assert [(row.entry, row.capture) for row in saved.past_missions[0].ledger] == kept
            prior = next(h for h in saved.history if h.get("runId") == old_run)
            assert prior["missionId"] == old.id and prior["origin"] == old.origin
            assert prior["status"] == "partial" and prior["cause"] == "timeout"
            assert saved.result_run["missionId"] == replacement.id and saved.result_run["runId"] != old_run
            assert all((h.get("result") or {}).get("summary") != records[-1][0]
                       for h in saved.history if h.get("missionId") == old.id)
            assert saved.requests[0]["missionId"] == old.id and saved.requests[0]["runId"] == old_run
        finally:
            barrier.release.set()
            if old_task is not None and not old_task.done():
                old_task.cancel()
                await asyncio.gather(old_task, return_exceptions=True)
            await cleanup(manager, worker)
    asyncio.run(scenario())
