"""Effort-bounded research operations through the real worker, manager and Hands.

Only IPC and the existing synthetic cell-finder transport are external stand-ins.
Population/Capture/Runner production, session persistence and both budget gates
are real. These tests do not certify motion acquisition, comparison means,
physical footage, provider behavior or useful spoken/browser results.
"""
import asyncio
import json
import time
from types import SimpleNamespace

import pytest

from orchestrator.evidence import evidence_of
from orchestrator.population import layer_of
from orchestrator.research import ResearchManager, Session
from orchestrator.research.hands import Hands
from research_worker.budget import EFFORTS
from research_worker.researcher import Researcher
from research_worker.work import SCHEMAS
from tests.test_population import world  # noqa: F401 (external synthetic cell finder)
from tests.test_research import FakeHub
from tests.waiting import HANG_S


def rig(tmp_path, world, effort="standard", *, deliverable="", purpose="research", index=True):
    manager = ResearchManager(FakeHub(), root=tmp_path / "research")
    logged, sent, replies, emitted = [], [], [], []
    manager.hands = Hands(world, log=logged.append)
    session = Session(rid="r2", day="2026-10-09", n=2, brief="compare independent measurements",
                      deliverable=deliverable, effort=effort, engine="openai", purpose=purpose, label="",
                      object_id=None, origin={"channel": "chat", "thread": "t3"}, status="running")
    session.root = manager.root
    manager.sessions[session.rid] = session
    directory = manager.root / session.day / session.rid
    (directory / "inputs").mkdir(parents=True)
    (directory / "inputs" / "frame.jpg").write_bytes(world.ring[world.latest_info.id])

    async def request(op, **payload):
        # The process transport only: every result comes from the real app handler.
        sent.append({"op": op, **payload})
        reply = await manager._request({"op": op, **payload})
        replies.append(reply)
        return reply

    spec = {"rid": session.rid, "n": session.n, "dir": str(directory), "day": session.day,
            "effort": effort, "deliverable": deliverable, "purpose": purpose}
    if index:
        # SessionIndex construction only; no HTTP or read operation is performed.
        spec["resources_url"] = "http://127.0.0.1:8770"
    worker = Researcher(spec, emitted.append, request)
    return SimpleNamespace(manager=manager, session=session, worker=worker, sent=sent,
                           replies=replies, logged=logged, emitted=emitted, directory=directory)


async def segment(r, world, n):
    # A distinct original camera frame prevents Runner reuse being mistaken for
    # another completed operation. The actual Capture owns this frame's JPEG.
    world.frame(float(n), 0.0)
    fid = world.latest_info.id
    result, picture = await r.worker.call_tool("run", {"operation": "segment", "why": f"sample {n}"})
    return result, picture, fid


def test_standard_worker_and_hands_complete_five_original_operations(tmp_path, world):
    r = rig(tmp_path, world)

    async def go():
        return [await segment(r, world, n) for n in range(1, 6)]

    completed = asyncio.run(go())
    assert all(result.get("ok") for result, _, _ in completed), completed
    ids = [result["id"] for result, _, _ in completed]
    assert len(set(ids)) == 5 and r.worker.runs == 5 and world.gpu_heavy.calls == 5
    assert all(picture is None for _, picture, _ in completed)
    assert [row["id"] for row in r.session.ran] == ids
    assert [reply["capture"]["frame"] for reply in r.replies] == [fid for _, _, fid in completed]
    for eid, (_, _, fid) in zip(ids, completed):
        population = evidence_of(world).get(eid)
        assert population.capture_id == fid and population.revision == 0
        assert population.made_for == "Researcher 2" and population.made_by["origin"] == "background"
        assert population.thread == "t3" and len(population.live()) == 6
    assert layer_of(world).population() is None
    saved = json.loads((r.directory / "session.json").read_text())
    assert [row["id"] for row in saved["ran"]] == ids
    assert len([row for row in r.logged if row.get("ok")]) == 5
    assert r.worker.effort is EFFORTS["standard"]
    assert (r.worker.effort.seconds, r.worker.effort.cap_usd, r.worker.effort.max_rounds) == (360, 1.0, 16)
    assert r.worker.budget.spent == 0 and r.worker.budget.calls == []


@pytest.mark.parametrize("effort,limit,seconds,usd", [("quick", 8, 75, 0.25),
                                                       ("standard", 16, 360, 1.0),
                                                       ("deep", 32, 1080, 5.0)])
def test_both_real_gates_enforce_effort_boundary_and_keep_mission_slots_separate(tmp_path, world,
                                                                              effort, limit, seconds, usd):
    r = rig(tmp_path, world, effort)

    async def go():
        completed = [await segment(r, world, n) for n in range(1, limit + 1)]
        assert all(result.get("ok") for result, _, _ in completed), completed
        before = len(r.sent)
        refused, picture, _ = await segment(r, world, limit + 1)
        assert "error" in refused and str(limit) in refused["error"] and picture is None
        assert len(r.sent) == before  # the worker refuses before sending another RPC
        refused_by_app = await r.manager._request({"op": "run", "rid": "r2", "operation": "segment"})
        assert refused_by_app["ok"] is False and str(limit) in refused_by_app["error"]
        assert r.worker.runs == limit and len(r.session.ran) == limit and world.gpu_heavy.calls == limit
        # Actual scheduled work still runs; it is not an extra worker allowance.
        world.frame(float(limit + 2), 0.0)
        slot = await r.manager.hands.run(r.session, "segment", "scheduled checkpoint", scheduled=True)
        assert slot["ok"] and slot["id"] not in [result["id"] for result, _, _ in completed]
        assert r.session.ran[-1]["slot"] is True and r.session.ran[-1]["run"] == 0
        assert r.worker.runs == limit and len(r.session.ran) == limit + 1
        again = await r.manager._request({"op": "run", "rid": "r2", "operation": "segment"})
        assert again["ok"] is False and world.gpu_heavy.calls == limit + 1
        return completed

    completed = asyncio.run(go())
    assert len({result["id"] for result, _, _ in completed}) == limit
    assert (r.worker.effort.seconds, r.worker.effort.cap_usd, r.worker.effort.max_rounds) == (seconds, usd, limit)
    assert r.worker.budget.spent == 0 and r.worker.budget.calls == []
    assert r.manager.hands.busy is None and layer_of(world).population() is None


def test_run_schema_describes_each_selected_effort_without_mutating_other_workers(tmp_path, world):
    original = json.loads(json.dumps(SCHEMAS["run"]))
    workers = [rig(tmp_path / effort, world, effort).worker for effort in ("quick", "standard", "deep")]
    descriptions = [worker.schema("run")["description"] for worker in workers]
    for description, limit in zip(descriptions, (8, 16, 32)):
        assert f"at most {limit} in this run" in description
        assert "one at a time" in description and "nothing is shown" in description
    assert workers[0].schema("run")["description"] == descriptions[0]
    assert SCHEMAS["run"] == original  # no selected-effort mutation of the shared schema


@pytest.mark.parametrize("effort", ["instant", "describe", "revise", "unknown", None])
def test_hands_refuses_efforts_without_run_before_producing_anything(tmp_path, world, effort):
    r = rig(tmp_path, world)
    r.session.effort = effort  # public input: absent/unknown remains unavailable, never quick by default
    reply = asyncio.run(r.manager.hands.run(r.session, "segment"))
    assert reply.get("ok") is False and reply.get("error"), reply
    assert r.session.ran == [] and world.gpu_heavy.calls == 0
    assert r.manager.hands.busy is None and layer_of(world).population() is None
    if effort in EFFORTS:
        worker = rig(tmp_path / effort, world, effort).worker
        result, picture = asyncio.run(worker.call_tool("run", {"operation": "segment"}))
        assert "not available" in result["error"] and picture is None and worker.runs == 0


@pytest.mark.parametrize("options", [{"deliverable": "answer"}, {"purpose": "description"}, {"index": False}])
def test_existing_worker_no_run_contexts_remain_unavailable(tmp_path, world, options):
    r = rig(tmp_path, world, **options)
    result, picture = asyncio.run(r.worker.call_tool("run", {"operation": "segment"}))
    assert "not available" in result["error"] and picture is None
    assert r.sent == [] and r.worker.runs == 0 and r.session.ran == [] and world.gpu_heavy.calls == 0


def test_held_external_worker_keeps_one_research_operation_and_research_priority(tmp_path, world):
    r = rig(tmp_path, world)
    original_call = world.gpu_heavy.call
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def held(op, **payload):
        calls.append((op, payload))
        entered.set()
        await release.wait()
        return await original_call(op, **payload)

    world.gpu_heavy.call = held  # external transport only; the original finder supplies actual masks

    async def go():
        first = asyncio.create_task(segment(r, world, 1))
        try:
            await asyncio.wait_for(entered.wait(), HANG_S)
            assert r.manager.hands.busy == "r2" and r.worker.runs == 0 and r.session.ran == []
            refused, picture, _ = await segment(r, world, 2)
            assert "another researcher's operation" in refused["error"] and picture is None
            assert len(calls) == 1 and r.worker.runs == 0 and r.session.ran == []
        finally:
            release.set()
        result, picture, fid = await first
        assert result["ok"] and picture is None
        assert evidence_of(world).get(result["id"]).capture_id == fid
        assert r.worker.runs == 1 and len(r.session.ran) == 1 and r.manager.hands.busy is None

    asyncio.run(go())
    assert calls[0][0] == "cells" and calls[0][1]["priority"] == "research"
    assert world.gpu_heavy.calls == 1


@pytest.mark.parametrize("refusal", ["camera", "session", "input"])
def test_source_and_session_refusals_do_not_spend_an_operation(tmp_path, world, refusal):
    r = rig(tmp_path, world)
    original_source = world.source
    args = {"operation": "segment"}
    if refusal == "camera":
        world.source = None
        expected = "no fresh picture from the camera"
    elif refusal == "session":  # another session is open (ADR 0017)
        world.execution_day = SimpleNamespace(day="2026-10-10.2", sid="2026-10-10.2", date="2026-10-10")
        expected = "another session is open now"
    else:
        args["input"] = "e999"
        expected = "there is no e999"
    result, picture = asyncio.run(r.worker.call_tool("run", args))
    assert expected in result["error"] and picture is None
    assert len(r.sent) == 1 and r.worker.runs == 0 and r.session.ran == [] and world.gpu_heavy.calls == 0
    world.source = original_source
    world.execution_day = SimpleNamespace(day=r.session.day, sid=r.session.day, date=r.session.day)
    completed, _, _ = asyncio.run(segment(r, world, 1))
    assert completed["ok"] and r.worker.runs == 1 and len(r.session.ran) == 1


def test_past_its_efforts_time_an_operation_still_runs_without_changing_effort_budget(tmp_path, world):
    r = rig(tmp_path, world)
    # Long past its effort's time: no clock refuses the app's operations (budget.py: the clock never cuts work).
    r.worker.started_at = time.monotonic() - 2 * r.worker.effort.seconds
    result, picture = asyncio.run(r.worker.call_tool("run", {"operation": "segment"}))
    assert result.get("ok") is True, result
    assert r.worker.runs == 1 and len(r.session.ran) == 1
    assert (r.worker.effort.seconds, r.worker.effort.cap_usd, r.worker.effort.max_rounds) == (360, 1.0, 16)
