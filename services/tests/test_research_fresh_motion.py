"""Synthetic CPU camera / real researcher, manager, Hands and operation boundaries.

No provider/model responses, handler patches, manufactured measurements or live paint proof.
`fresh` is a proposed explicit run argument; omitted keeps the assistant's rolling behavior.
"""
import asyncio
import json
import threading
from types import SimpleNamespace

import cv2
import pytest

from orchestrator.evidence import evidence_of
from orchestrator.motiontracks import track_layer_of, tracks_of
from orchestrator.research import ResearchManager
from research_worker.budget import EFFORTS, operation_limit
from research_worker.researcher import Researcher
from tests.test_motion_tracks import MotionHub, moving_slide
from tests.test_population import world  # noqa: F401 (external synthetic cell finder)
from tests.test_research_samples import DAY, session
from tests.waiting import HANG_S, until_async


def worker(tmp_path, hub):
    """The worker's external process-message seam delegates to the actual app manager."""
    hub.execution_day = SimpleNamespace(day=DAY, sid=DAY, date=DAY, active_id="t3")
    manager = ResearchManager(hub, root=tmp_path / "research", today=lambda: DAY)
    original = session(day=DAY)
    original.origin["thread"] = "t3"
    manager.sessions[original.rid] = original
    messages, replies, events = [], [], []

    async def request(op, **payload):
        message = json.loads(json.dumps({"op": op, **payload}))
        messages.append(message)
        reply = await manager._request(message)
        replies.append(json.loads(json.dumps(reply)))
        return json.loads(json.dumps(reply))

    researcher = Researcher({"rid": original.rid, "dir": str(tmp_path / "worker"),
                             "day": DAY, "effort": "quick", "purpose": "research"}, events.append, request)
    return researcher, manager, original, messages, replies


def motion_hub():
    return MotionHub(moving_slide(), disc=False, base=(500.0, -300.0))


def test_distinct_actual_app_results_are_progress_before_the_stall_round(tmp_path, world):
    async def go():
        r, manager, original, _, _ = worker(tmp_path, world)
        ids = []
        assert not r.stalled(1)
        for round_n in range(1, 4):
            world.frame(float(round_n), 0.0)
            result, picture = await r.tool_run({"operation": "segment", "why": "compare captures"})
            assert result["ok"] and picture is None
            population = evidence_of(world).get(result["id"])
            assert population is not None and len(population.live()) == 6
            assert population.made_for == original.name and population.thread == "t3"
            ids.append(population.id)
            assert len(set(ids)) == round_n
            assert not r.stalled(round_n + 1), "a new actual result is useful work, not an empty round"
        assert r.progress() == 3 and r.runs == 3 and len(original.ran) == 3
        assert manager.hands.busy is None
        # New results cannot disable the original finite no-progress rule forever.
        assert not r.stalled(6)
        assert r.stalled(7)

    asyncio.run(go())


def test_upcoming_retains_the_acquired_original_after_external_ring_eviction():
    """Real forward acquisition owns its bytes before waiting for another external frame.

    Ring eviction is external ingress behavior; no capture, gather, clock or budget is patched.
    A later missing frame is not replaced by another camera view under that frame's identity.
    """
    from orchestrator.brain.toolbox.motion import upcoming

    async def go():
        hub = motion_hub()
        baseline = dict(hub.captures.memory.reserved)
        boundary = hub.latest_info.id
        samples = moving_slide(seconds=.1)
        task = asyncio.create_task(upcoming(hub, 1.5))
        frames = None
        try:
            await asyncio.sleep(0)  # let the actual acquisition select its original boundary
            first = hub.add(samples[0].image, 500.0, -300.0, wall=104.0)
            original_bytes = hub.ring[first.id]
            await until_async(lambda: first.id in {c.id for c in hub.captures.held()}, every=1 / 120)
            held = hub.captures.get(first.id)
            assert held is not None and first.id in {c.id for c in hub.captures.held()}
            assert not task.done() and first.id > boundary
            assert hub.captures.memory.reserved["research"] >= len(original_bytes)
            # The bridge ring and registration index no longer contain the acquired input.
            hub.ring.clear()
            hub.infos.pop(first.id)
            assert hub.source.get(first.id) is None
            missing = hub.add(samples[1].image, 501.0, -300.0, wall=104.8)
            hub.ring.pop(missing.id)  # it disappears before the next collector wake
            final = hub.add(samples[2].image, 502.0, -300.0, wall=105.6)
            frames = await asyncio.wait_for(task, 1)
            assert [info.id for _, info in frames] == [first.id, final.id]
            assert frames[0][1] is held and frames[0][0].jpeg == original_bytes
            assert (held.wall_at, held.epoch, held.boot, held.seq, held.dx, held.dy) == (
                104.0, 0, "b0", 5000 + first.id, 500.0, -300.0)
            assert all(frame.id == info.id for frame, info in frames)
            assert {c.id for c in hub.captures.held()} == {first.id, final.id}
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if frames is not None:
                frames.release()
        assert not hub.captures.held() and hub.captures.memory.reserved == baseline

    asyncio.run(go())


@pytest.mark.parametrize("new_day", [False, True])
def test_a_real_camera_or_day_refusal_is_not_progress_or_an_operation(tmp_path, new_day):
    async def go():
        hub = motion_hub()
        r, manager, original, _, _ = worker(tmp_path, hub)
        if new_day:
            hub.execution_day.day = hub.execution_day.sid = hub.execution_day.date = "2026-10-08"
        else:
            hub.source.is_fresh = lambda: False  # external camera availability, not a replaced handler
        before = r.progress()
        result, picture = await r.tool_run({"operation": "motion", "why": "compare captures"})
        assert result.get("error") and picture is None
        assert r.progress() == before and r.runs == 0 and not original.ran
        assert not tracks_of(hub).items and manager.hands.busy is None
        assert r.stalled(4)

    asyncio.run(go())


def test_external_ipc_redelivery_of_one_actual_result_does_not_fake_new_progress(tmp_path):
    """Replay an actual app reply at the external seam; completed motion is not cached by the app."""
    async def go():
        hub = motion_hub()
        r, _, original, messages, replies = worker(tmp_path, hub)
        args = {"operation": "motion", "why": "read the retained interval"}
        first, _ = await r.tool_run(args)
        before = r.progress()
        assert first["ok"] and before == 1
        actual_packet = json.dumps(replies[0])

        async def redeliver(op, **payload):
            assert op == "run" and payload["rid"] == original.rid and payload["operation"] == "motion"
            return json.loads(actual_packet)  # byte-identical actual producer facts, not a manufactured result

        r.request = redeliver
        second, _ = await r.tool_run(args)
        assert second == first and first["id"] == second["id"]
        assert len(tracks_of(hub).items) == 1 and len(original.ran) == 1
        assert len(messages) == len(replies) == 1  # only one actual manager/Hands/CPU operation
        assert r.progress() == before
        assert r.runs == 2  # two received successes still use the existing worker operation allowance

    asyncio.run(go())


def test_motion_result_returns_the_actual_small_measurement_and_original_basis(tmp_path):
    async def go():
        hub = motion_hub()
        r, _, original, messages, replies = worker(tmp_path, hub)
        result, picture = await r.tool_run({"operation": "motion", "why": "read speed means"})
        assert result["ok"] and picture is None and len(messages) == 1
        item = tracks_of(hub).items[result["id"]]
        expected = {"summary": item.summary, "span": item.span, "scale": item.scale, "producer": item.producer}
        assert result.get("values") == expected
        assert result.get("capture") == {"frame": item.capture_id, "seq": item.view.get("seq"),
                                         "boot": item.view.get("boot") or None, "at": item.view.get("at")}
        assert result.get("view") == item.view
        assert result.get("words") == replies[0]["words"]
        assert result["values"]["summary"]["speedStatistics"]["unit"] == "px/s"
        assert result["values"]["summary"]["speedStatistics"]["allCounted"]["count"] == 9
        assert result["values"]["scale"]["umPerPx"] is None
        assert item.made_for == original.name and item.thread == "t3"
        assert track_layer_of(hub).shown is None and not hub.sent
        assert len(json.dumps(result)) < 6000 and "tracks" not in result["values"]
        result["values"]["summary"]["counted"] = -1
        assert item.summary["counted"] == 9  # crossing the process boundary is detached

    asyncio.run(go())


def test_run_schema_exposes_explicit_fresh_motion_without_increasing_caps(tmp_path):
    r, _, _, _, _ = worker(tmp_path, motion_hub())
    offered = r.schema("run")
    fresh = offered["parameters"]["properties"].get("fresh")
    assert fresh is not None and fresh.get("type") == "boolean"
    description = (offered["description"] + " " + fresh.get("description", "")).lower()
    assert "upcoming" in description and "motion" in description and "last" in description
    assert (r.effort.seconds, r.effort.cap_usd, r.effort.max_rounds, operation_limit("quick")) == (75, .25, 8, 8)
    assert EFFORTS["standard"].max_rounds == 16 and EFFORTS["deep"].max_rounds == 32


async def feed_next(hub, frames, start_wall):
    """External camera ingress at its normal cadence; neither gather nor clocks are patched."""
    ids = []
    for n, f in enumerate(frames):
        await asyncio.sleep(1 / 30)
        info = hub.add(f.image, f.dx + 500.0, f.dy - 300.0, wall=start_wall + n / 30)
        ids.append(info.id)
    return ids


def test_successive_fresh_worker_motion_uses_upcoming_nonoverlapping_original_frames(tmp_path):
    async def go():
        hub = motion_hub()
        r, manager, original, messages, _ = worker(tmp_path, hub)
        upcoming = moving_slide(seconds=3.3)
        items = []
        for n in range(2):
            boundary = hub.latest_info
            task = asyncio.create_task(r.tool_run({"operation": "motion", "seconds": 1.5,
                                                   "fresh": True, "why": "compare distinct sequences"}))
            emitted = await feed_next(hub, upcoming[n * 48:(n + 1) * 48], 104.0 + n * 1.6)
            result, picture = await asyncio.wait_for(task, HANG_S)
            assert result.get("ok") is True, result
            assert picture is None
            item = tracks_of(hub).items[result["id"]]
            used = {row[0] for row in item.frames}
            assert used and used <= set(emitted) and min(used) > boundary.id
            assert item.span["from"] > boundary.wall_at and item.span["seconds"] >= 1.2
            assert item.view["boot"] == "b0" and item.view["epoch"] == boundary.epoch
            assert item.capture_id == max(used)
            assert all(row[1] == "b0" and row[2] == 5000 + row[0] for row in item.frames)
            assert item.summary["unit"] == "px/s" and item.made_for == original.name
            assert result.get("values", {}).get("span") == item.span
            assert result.get("view") == item.view
            items.append(item)
        assert items[0].id != items[1].id
        assert not ({f[0] for f in items[0].frames} & {f[0] for f in items[1].frames})
        assert items[0].span["to"] < items[1].span["from"]
        assert all(m.get("fresh") is True for m in messages)
        assert r.runs == 2 and len(original.ran) == 2 and r.progress() == 2
        assert manager.hands.busy is None and track_layer_of(hub).shown is None and not hub.sent

    asyncio.run(go())


def test_omitted_fresh_still_measures_the_existing_rolling_window(tmp_path):
    async def go():
        hub = motion_hub()
        boundary = hub.latest_info.id
        r, _, _, messages, _ = worker(tmp_path, hub)
        result, _ = await asyncio.wait_for(r.tool_run({"operation": "motion", "why": "what moves now"}), HANG_S)
        assert result["ok"] and messages[0].get("fresh") in (None, False)
        item = tracks_of(hub).items[result["id"]]
        assert item.capture_id == boundary and len(item.frames) == 120
        assert item.span["seconds"] > 3.8 and item.summary["counted"] == 9
        assert track_layer_of(hub).shown is None

    asyncio.run(go())


@pytest.mark.parametrize("cancel", [False, True])
def test_forward_camera_bytes_stay_budgeted_until_actual_cpu_reader_settles(tmp_path, monkeypatch, cancel):
    """Only the external decoder is held/failed; the actual runner and caller are not replaced."""
    from orchestrator.operations import runner

    hub = motion_hub()
    entered, release = threading.Event(), threading.Event()
    real_decode = cv2.imdecode

    def decode(data, mode):
        if threading.current_thread() is not threading.main_thread() and not entered.is_set():
            entered.set()
            assert release.wait(HANG_S), "test did not release the external decoder"
            if not cancel:
                raise ValueError("synthetic external codec failure")
        return real_decode(data, mode)

    monkeypatch.setattr(cv2, "imdecode", decode)

    async def go():
        r, manager, original, _, _ = worker(tmp_path, hub)
        boundary = hub.latest_info.id
        task = asyncio.create_task(r.tool_run({"operation": "motion", "seconds": 1.5,
                                               "fresh": True, "why": "read the next interval"}))
        camera = asyncio.create_task(feed_next(hub, moving_slide(seconds=1.6), 104.0))
        try:
            await until_async(entered.is_set)
            assert entered.is_set(), "actual operation must enter its real CPU decoder"
            [job] = [j for j in runner(hub).jobs.values() if j.execution.operation == "motion"]
            held = {c.id: c for c in hub.captures.held() if c.id > boundary}
            assert len(held) >= 10, "all acquired original JPEGs need real held-byte accounting"
            minimum_bytes = sum(len(c.jpeg) for c in held.values())
            assert hub.captures.memory.reserved["research"] >= minimum_bytes
            if cancel:
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert not job.task.done()  # begun CPU work is still reading the original window
                assert held.keys() <= {c.id for c in hub.captures.held()}
                assert hub.captures.memory.reserved["research"] >= minimum_bytes
            release.set()
            settled = await asyncio.wait_for(asyncio.gather(asyncio.shield(job.task), return_exceptions=True), 5)
            if not cancel:
                assert isinstance(settled[0], Exception)
                result, picture = await task
                assert result.get("error") and picture is None
            assert not tracks_of(hub).items and not original.ran and r.runs == 0
            assert manager.hands.busy is None
            assert not hub.captures.held() and hub.captures.memory.reserved["research"] == 0
        finally:
            release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, camera, return_exceptions=True)

    asyncio.run(go())
