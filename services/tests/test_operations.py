"""Contracts 4-6 (docs/design/2026-10-06-captures-operations-evidence.md; ADR 0011): operations
with per-implementation contracts, origin and policy kept apart, declared fallbacks only, shared jobs
with separate effects, one Execution per job, and a scheduler with bounds it can keep."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from orchestrator import tools
from orchestrator.capture import Capture
from orchestrator.operations import OPERATIONS, Prompt, Runner, Unavailable
from orchestrator.options import Settings
from orchestrator.scene import Scene

SQUARE = [[100.0, 200.0], [140.0, 200.0], [140.0, 230.0], [100.0, 230.0]]


class Gpu:
    ready = True

    def __init__(self, replies=None, delay=0.0):
        self.replies, self.delay, self.calls = dict(replies or {}), delay, []

    async def call(self, op, timeout=30.0, priority=None, **payload):
        self.calls.append({"op": op, "priority": priority, **payload})
        await asyncio.sleep(self.delay)
        polygons = self.replies.get(payload.get("model"), [SQUARE])
        return {"polygons": polygons, "score": 0.9, "ms": 5.0}


def hub(gpu=None, outline="edgetam"):
    settings = Settings()
    settings.outline = outline
    return SimpleNamespace(gpu=gpu or Gpu(), gpu_heavy=None, settings=settings, scene=Scene(), latest_info=None)


def view(n=1):
    return Capture(n, b"jpeg-bytes-of-view-%d" % n, SimpleNamespace(id=n, width=1280, height=720, epoch=0, dx=0.0, dy=0.0))


def run(coro):
    return asyncio.run(coro)


BOX = Prompt("box", 120, 215, (100, 200, 140, 230))
CLICK = Prompt("point", 120, 215)


def test_the_default_runs_by_route_and_an_explicit_choice_runs_raw():
    h = hub()
    default = run(Runner(h).run("outline", view(), CLICK, origin="click"))
    chosen = run(Runner(h).run("outline", view(2), CLICK, choice="sam2.1-tiny", origin="brain-chosen"))
    assert (default.execution.policy, default.execution.selection, default.execution.executed) == ("route", "settings", "edgetam")
    assert (chosen.execution.policy, chosen.execution.selection, chosen.execution.executed) == ("raw", "explicit", "sam2.1-tiny")
    assert default.execution.made_by() == {"operation": "outline", "implementation": "edgetam", "policy": "route",
                                           "origin": "click", "execution": default.execution.id,
                                           "version": "yonigozlan/EdgeTAM-hf",
                                           "producer": {"catalog": {"key": "edgetam", "name": "EdgeTAM",
                                               "version": "yonigozlan/EdgeTAM-hf", "from": "reviewed",
                                               "link": "https://github.com/facebookresearch/EdgeTAM"}}}


def test_an_explicit_choice_that_cannot_run_makes_no_call_and_says_what_can(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    h = hub()
    with pytest.raises(Unavailable) as no:
        run(Runner(h).run("outline", view(), BOX, choice="sam3.1-fal", origin="user-chosen"))
    assert not h.gpu.calls and "needs FAL_KEY" in str(no.value) and "EdgeTAM" in str(no.value)
    with pytest.raises(Unavailable) as cannot:
        run(Runner(h).run("outline", view(), Prompt("field"), origin="brain-default"))
    assert "can't take a field" in str(cannot.value)


def test_an_explicit_raw_choice_gets_no_fallback_and_a_route_gets_only_declared_ones(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")
    fal = []

    async def fake_fal(hub_, capture, jpeg, x, y, box, label):
        fal.append(box)
        return {"polygons": [SQUARE], "score": 0.95, "ms": 1400.0}

    monkeypatch.setattr(tools, "_segment_fal", fake_fal)
    raw = run(Runner(hub(Gpu({"edgetam": []}))).run("outline", view(), BOX, choice="edgetam", origin="user-chosen"))
    assert not fal and raw.execution.executed is None and raw.output["polygons"] == []  # nothing, honestly
    route = run(Runner(hub(Gpu({"edgetam": []}))).run("outline", view(), BOX, origin="brain-default"))
    assert len(fal) == 1 and route.execution.executed == "sam3.1-fal"
    assert [(a.impl, a.why) for a in route.execution.attempts] == [("edgetam", "chosen"), ("sam3.1-fal", "empty-box")]
    assert route.execution.made_by()["fallbackFrom"] == "edgetam"
    monkeypatch.delenv("FAL_KEY")
    keyless = run(Runner(hub(Gpu({"edgetam": []}))).run("outline", view(2), BOX, origin="brain-default"))
    assert keyless.output["polygons"] == [] and len(fal) == 1  # the declared fallback needs its key: none taken


def test_a_failed_attempt_gets_no_fallback_only_one_that_ran_and_found_nothing(monkeypatch):
    """Scaffolding review, M1.2 item 1: a refusal or an error is a failure, said as it is. The empty-box
    fallback is for a box the model looked in and found nothing (7 of 15 concurrent boxes, refused
    Busy, were outlined by SAM 3.1 under "EdgeTAM found nothing in the box")."""
    from orchestrator.gpu_client import Busy

    monkeypatch.setenv("FAL_KEY", "test")
    fal = []

    async def fake_fal(hub_, capture, jpeg, x, y, box, label):
        fal.append(box)
        return {"polygons": [SQUARE], "score": 0.95, "ms": 1400.0}

    class Refusing(Gpu):
        async def call(self, op, timeout=30.0, priority=None, **payload):
            raise Busy("8 segment requests are already waiting; try again in a moment")

    monkeypatch.setattr(tools, "_segment_fal", fake_fal)
    with pytest.raises(tools.ToolError, match="already waiting"):
        run(Runner(hub(Refusing())).run("outline", view(), BOX, origin="brain-default"))
    assert not fal  # no fallback after a failure
    spot = SimpleNamespace(radius=4.0)
    with pytest.raises(tools.ToolError):
        run(Runner(hub(Refusing())).run("outline", view(), Prompt("point", 120, 215, spot=spot), origin="click"))


def test_the_spot_ring_is_a_declared_fallback_recorded_as_an_attempt():
    h = hub(Gpu({"edgetam": []}))
    spot = SimpleNamespace(radius=4.0)
    done = run(Runner(h).run("outline", view(), Prompt("point", 120, 215, spot=spot), origin="click"))
    assert done.execution.executed == "spot-ring" and len(done.output["polygons"][0]) == 24
    raw = run(Runner(h).run("outline", view(2), Prompt("point", 120, 215, spot=spot), choice="edgetam", origin="arena",
                            policy="raw"))
    assert raw.execution.executed is None and raw.output["polygons"] == []  # raw: no ring


def test_one_job_is_shared_by_requests_and_each_use_is_recorded():
    async def go():
        h = hub(Gpu(delay=0.02))
        runner = Runner(h)
        logged = []
        runner.log = logged.append
        a = SimpleNamespace(id="ra", choices={})
        b = SimpleNamespace(id="rb", choices={})
        capture = view()
        first, second = await asyncio.gather(runner.run("outline", capture, CLICK, ticket=a),
                                             runner.run("outline", capture, CLICK, ticket=b))
        later = await runner.run("outline", capture, CLICK, ticket=b)
        return h, first, second, later, logged

    h, first, second, later, logged = run(go())
    assert len(h.gpu.calls) == 1  # one computation
    assert first.execution is second.execution is later.execution
    assert (first.use.reused, second.use.reused, later.use.reused) == (False, True, True)
    lines = [r for r in logged if r["kind"] == "execution"]
    uses = [(r["ticket"], r["reused"]) for r in logged if r["kind"] == "execution-use"]
    assert len(lines) == 1 and lines[0]["ticket"] == "ra"  # the request that started it
    assert sorted(uses) == [("ra", False), ("rb", True), ("rb", True)]  # every use, the producer's too
    assert first.made_by()["reused"] is False and second.made_by()["reused"] is True
    assert h.gpu.calls[0]["jpeg"] == b"jpeg-bytes-of-view-1" and first.execution.capture == 1  # the bytes it names


def test_a_click_and_a_highlight_of_one_thing_share_its_job_and_two_unticketed_requests_stay_two():
    """When work is scheduled is not part of the job (contract 4), and a request without a ticket is
    its own subscriber: cancelling one never drops the other's work."""
    async def go():
        h = hub(Gpu(delay=0.05))
        runner = Runner(h)
        capture = view()
        click = asyncio.ensure_future(runner.run("outline", capture, CLICK, origin="click", ticket=None,
                                                 params={"priority": "click"}))
        other = asyncio.ensure_future(runner.run("outline", capture, CLICK, origin="brain-default", ticket=None,
                                                 params={"priority": "interactive"}))
        await asyncio.sleep(0.01)
        other.cancel()
        done = await click
        return h, done

    h, done = run(go())
    assert len(h.gpu.calls) == 1 and done.output["polygons"]


def test_cancelling_one_request_does_not_cancel_the_work_another_needs():
    async def go():
        h = hub(Gpu(delay=0.05))
        runner = Runner(h)
        capture = view()
        a = asyncio.ensure_future(runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id="ra", choices={})))
        b = asyncio.ensure_future(runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id="rb", choices={})))
        await asyncio.sleep(0.01)
        a.cancel()
        result = await b
        return a, result

    a, result = run(go())
    assert a.cancelled() and result.execution.executed == "edgetam"


def test_a_request_choice_covers_its_run_and_a_default_changed_mid_flight_changes_no_dispatched_job():
    async def go():
        h = hub(Gpu(delay=0.03))
        runner = Runner(h)
        request = SimpleNamespace(id="ra", choices={"outline": {"implementation": "sam2.1-tiny", "policy": "raw"}})
        chosen = asyncio.ensure_future(runner.run("outline", view(), CLICK, ticket=request, origin="user-chosen"))
        default = asyncio.ensure_future(runner.run("outline", view(2), CLICK, ticket=None))
        await asyncio.sleep(0.005)
        h.settings.outline = "sam2.1-tiny"  # changed while both run
        return await chosen, await default

    chosen, default = run(go())
    assert (chosen.execution.executed, chosen.execution.selection, chosen.execution.policy) == ("sam2.1-tiny", "request", "raw")
    assert default.execution.executed == "edgetam"  # resolved when it was dispatched


def test_every_declared_fallback_is_in_the_catalogue_and_every_catalogue_route_is_declared():
    import json
    from pathlib import Path

    from orchestrator.capabilities import _declared

    catalogue = json.loads((Path(__file__).resolve().parents[2] / "contract" / "capabilities.json").read_text())
    routes = [fb["route"] for c in catalogue["capabilities"] for fb in c.get("fallbacks", [])]
    assert routes and all(_declared(r) for r in routes)
    for op in OPERATIONS.values():
        for fb in op.fallbacks:
            assert any(r["operation"] == op.id and set(r["after"]) == set(fb.after) and r["when"] == fb.when
                       and r["to"] == fb.to for r in routes), (op.id, fb)


def test_each_implementation_says_what_it_accepts_where_it_runs_and_has_an_adapter():
    from orchestrator.operations import ADAPTERS, WORKERS

    for op in OPERATIONS.values():
        for impl in op.implementations:
            assert impl.accepts and impl.worker in WORKERS
            if op.id in ("outline", "cells", "find-like") and impl.id != "vision-boxes":
                assert (op.id, impl.id) in ADAPTERS, (op.id, impl.id)
    # Long segments never run on the click worker (contract 5).
    assert {i.id for i in OPERATIONS["outline"].implementations if i.worker == "interactive"} == {"edgetam", "sam2.1-tiny"}


# --- the scheduler (contract 5) ----------------------------------------------------------------
def test_the_worker_queue_runs_the_newest_click_first_then_short_work_then_background():
    from gpu_worker.inbox import Inbox

    box = Inbox({"ping", "segment"})
    for i, cls in enumerate(["background", "click", "interactive", "click", "background", "click"]):
        box.put({"id": i, "op": "segment" if cls != "background" else "cells", "_priority": cls})
    assert [box.get()["id"] for _ in range(6)] == [5, 3, 1, 2, 0, 4]


def test_queued_work_past_its_deadline_or_replaced_never_runs_and_tracking_steps_stay_in_order():
    from gpu_worker.inbox import Inbox

    box = Inbox({"ping", "segment"})
    assert box.refusal({"id": 1, "op": "cells", "_deadline": time.time() - 1})["expired"]
    box.put({"id": 0, "op": "drop", "ids": [2]})
    assert box.refusal({"id": 2, "op": "automask"})["superseded"]
    assert box.refusal({"id": 3, "op": "track", "session": 1, "seq": 5}) is None
    assert box.refusal({"id": 4, "op": "track", "session": 1, "seq": 4})["superseded"]  # from before
    assert box.refusal({"id": 5, "op": "track", "session": 2, "seq": 1}) is None  # after a reset
    assert box.refusal({"id": 6, "op": "track", "session": 1, "seq": 9})["superseded"]  # the old session


def test_a_worker_running_past_its_limit_is_hung_and_calls_fail_fast_with_the_reason():
    from orchestrator.gpu_client import Busy, GpuClient

    async def go():
        client = GpuClient("micromind-gpu")
        told = []
        client.on_problem.append(told.append)
        client._loop = asyncio.get_running_loop()
        sent = []
        client._conn = SimpleNamespace(send=sent.append)
        client._outstanding[7] = ["cells", "background", 185.0, time.monotonic() - 200, None]  # a cell run past its hard limit
        client._outstanding[8] = ["embed", "background", 65.0, None, None]  # queued behind it all along: never counts
        client.watch()  # the hang check's thread (it no longer waits for the loop)
        await asyncio.sleep(1.1)
        with pytest.raises(RuntimeError) as fast:
            await client.call("segment", timeout=1.0)
        client._replied({"id": 7, "ok": True})  # it finally answered
        await asyncio.sleep(1.1)
        client.unwatch()
        return client, told, fast, sent

    client, told, fast, sent = run(go())
    assert len(told) == 2 and told[0].startswith("the outline worker is not responding (cells running for 20")
    assert told[1] == ""  # and told again when it recovered, so what needs it is offered again
    assert "not responding" in str(fast.value) and not sent  # failed fast: nothing was sent
    assert client.hung == ""  # recovered once it answered

    async def busy():
        client = GpuClient("micromind-gpu")
        client._loop = asyncio.get_running_loop()
        client._conn = SimpleNamespace(send=lambda m: None)
        for rid in range(1, 3):
            client._pending[rid] = client._loop.create_future()
            client._outstanding[rid] = ["cells", "background", 185.0, None, None]
        with pytest.raises(Busy):
            await client.call("cells", timeout=1.0)

    run(busy())


class InboxWorker:
    """A stand-in GPU worker behind a real GpuClient: the real Inbox orders its queue, each op takes
    `op_s`, and it says when it starts each call, as gpu_worker/worker.py does."""

    def __init__(self, client, op_s=0.02, release=None):
        from gpu_worker.inbox import Inbox

        self.client, self.op_s, self.inbox, self.ran = client, op_s, Inbox({"ping", "segment"}), []
        self.release = release
        self.started = []
        self.refused = []
        client._conn = SimpleNamespace(send=self.inbox.put)

    async def serve(self):
        import queue as q

        while True:
            try:
                _, req = self.inbox.get_nowait()
            except q.Empty:
                await asyncio.sleep(0.001)
                continue
            refused = self.inbox.refusal(req)
            if refused is not None:
                self.refused.append(req["id"])
                self.client._replied(refused)
                continue
            self.client._replied({"id": req["id"], "started": True})
            self.started.append(req["id"])
            if self.release is not None:
                await self.release.wait()
            await asyncio.sleep(self.op_s)
            self.ran.append(req["id"])
            self.client._replied({"id": req["id"], "ok": True, "polygons": [], "ms": self.op_s * 1000})


def test_the_app_outlining_fifteen_things_never_refuses_a_click_and_the_click_goes_next(wall_clock):
    """Scaffolding review, M1.2 item 2: a look's 15 targets (the app's own segments) and an arena ask
    once counted against one bound of 8 with clicks; the 9th and later were refused, a click too."""
    from orchestrator.gpu_client import GpuClient

    async def go():
        client = GpuClient("micromind-gpu")
        client._loop = asyncio.get_running_loop()
        worker = InboxWorker(client)
        serving = asyncio.ensure_future(worker.serve())
        targets = [asyncio.ensure_future(client.call("segment", timeout=5.0, priority="interactive", model="edgetam"))
                   for _ in range(15)]
        await asyncio.sleep(0.005)
        t0 = time.perf_counter()
        await client.call("segment", timeout=5.0, priority="click", model="edgetam")
        click_s = time.perf_counter() - t0
        done = await asyncio.gather(*targets, return_exceptions=True)
        serving.cancel()
        return click_s, done, worker

    click_s, done, worker = run(go())
    assert all(isinstance(r, dict) and r["ok"] for r in done)  # every target outlined (none refused at 15)
    # at most the one op running when it came, then its own (op 20 ms)
    wall_clock.within(click_s, 3 * 0.02 + 0.03, "the click")


def test_a_call_given_up_before_the_worker_starts_it_never_runs():
    from orchestrator.gpu_client import GpuClient
    from tests.waiting import HANG_S, until_async

    async def go():
        client = GpuClient("micromind-gpu-heavy")
        client._loop = asyncio.get_running_loop()
        release = asyncio.Event()
        worker = InboxWorker(client, op_s=0, release=release)
        serving = asyncio.create_task(worker.serve())
        first = asyncio.create_task(client.call("cells", timeout=HANG_S, priority="background", model="cellpose-sam"))
        second = asyncio.create_task(client.call("embed", timeout=HANG_S, priority="background"))
        try:
            # Hold the running first call until cancellation of the queued call is observed.
            # A sleep cannot establish this ordering when CI stalls the event loop.
            await until_async(lambda: worker.started == [1] and 2 in client._pending)
            second.cancel()
            with pytest.raises(asyncio.CancelledError):
                await second
            release.set()
            await first
            await until_async(lambda: worker.refused == [2])
            return worker
        finally:
            release.set()
            for task in (first, second, serving):
                task.cancel()
            await asyncio.gather(first, second, serving, return_exceptions=True)

    worker = run(go())
    assert worker.ran == [1]  # the cancelled call was dropped from the queue, never run


def test_a_worker_is_started_once_however_many_ask():
    """Scaffolding review, M1.2 item 3: a composite outline during warm-up started a second click worker."""
    from orchestrator.gpu_client import GpuClient

    starts = []

    class Counted(GpuClient):
        async def _start(self):
            starts.append(1)
            await asyncio.sleep(0.05)
            self.ready = True

    async def go():
        client = Counted("micromind-gpu")
        await asyncio.gather(client.start(), client.ensure_started(), client.ensure_started())
        await client.ensure_started()

    run(go())
    assert len(starts) == 1


def test_a_composite_is_available_only_when_every_step_can_run(monkeypatch):
    from orchestrator.operations import OUTLINE, availability

    monkeypatch.setenv("FAL_KEY", "test")
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    warming = Gpu()
    warming.ready = False  # the click worker still loading its model
    h = hub(warming)
    assert "still loading" in availability(h, OUTLINE.impl("edgetam>sam3.1-fal"))


def test_a_click_waits_at_most_for_the_short_op_running_when_it_came():
    """The newest click goes next whatever else is queued (other clicks, short work, background)."""
    from gpu_worker.inbox import Inbox

    box = Inbox({"ping", "segment"})
    for i in range(5):
        box.put({"id": f"bg{i}", "op": "regions", "_priority": "background"})
        box.put({"id": f"short{i}", "op": "segment", "_priority": "interactive"})
    box.put({"id": "old-click", "op": "segment", "_priority": "click"})
    box.put({"id": "new-click", "op": "segment", "_priority": "click"})
    assert box.get()["id"] == "new-click" and box.get()["id"] == "old-click"


def test_an_arena_arm_leaves_no_trace_of_its_model_on_any_surface_before_its_vote(tmp_path, monkeypatch):
    """Contract 6: until the vote, no arm's implementation is on the scene's wire (source, note,
    made-by), the trial's wire, the arm's error text, the execution inspector or what is broadcast."""
    import json as _json
    import random

    from orchestrator import execution
    from orchestrator.arena import Arena
    from orchestrator.options import OUTLINE
    from tests.test_apply import World

    class Failing(World):
        pass

    world = Failing()
    world.sent = []
    world.broadcast = lambda payload: world.sent.append(_json.loads(_json.dumps(payload)))

    class Gpu:
        ready = True

        async def call(self, op, timeout=30.0, priority=None, **payload):
            if payload["model"] == "sam2.1-tiny":
                raise RuntimeError("sam2.1-tiny exploded: facebook/sam2.1-hiera-tiny out of memory")
            x0, y0, x1, y1 = payload.get("box") or (payload["points"][0][0] - 20, payload["points"][0][1] - 20,
                                                    payload["points"][0][0] + 20, payload["points"][0][1] + 20)
            return {"polygons": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]], "score": 0.9, "ms": 5.0}

    world.gpu = Gpu()
    log = execution.ExecutionLog(enabled=True)
    world.execution = log
    arena = Arena(world, agent=None, rng=random.Random(2), votes_log=tmp_path / "votes.jsonl")
    arena.configure({"enabled": True, "outline": ["edgetam", "sam2.1-tiny"], "policy": "raw"})

    from orchestrator.capabilities import wire as capability_wire
    from orchestrator.operations import runner

    interactions = []
    runner(world).log = interactions.append  # the day's interaction log, as app.py wires it
    capabilities_before = _json.dumps(capability_wire(world.settings, {}))

    async def go():
        with log.request("click") as root:
            trial = await arena.outline_trial(1, 300, 200)
        return trial, root

    trial, root = asyncio.run(go())
    names = {"edgetam", "sam2.1", "hiera", "EdgeTAM", "SAM 2.1"} | set(OUTLINE) | {o.label for o in OUTLINE.values()}
    surfaces = {"broadcast": world.sent, "scene": world.scene.to_wire(), "trial": trial.blind(),
                "inspector": log.inspect(root.id), "arena trial": arena.state()["trial"],
                "made_by": [o.made_by for o in world.scene.objects.values()],
                "interaction log": list(interactions)}
    # The capability wire names every model in its menus by design; a trial adds nothing to it.
    assert _json.dumps(capability_wire(world.settings, {})) == capabilities_before
    for where, surface in surfaces.items():
        text = _json.dumps(surface)
        leaked = [n for n in names if n in text]
        assert not leaked, (where, leaked)
    assert trial.policy == "raw" and any(a.error for a in trial.arms.values())
    result = arena.vote(trial.id, "A")
    assert {r["option"] for r in result["reveal"].values()} == {"edgetam", "sam2.1-tiny"}  # revealed by the vote
    voted = _json.loads((tmp_path / "votes.jsonl").read_text().splitlines()[-1])
    assert voted["policy"] == "raw" and set(voted["executed"].values()) <= {"edgetam"}
    assert voted["executions"] and all(voted["executions"].values())  # linkable to what ran
    revealed = [r for r in interactions if r["kind"] == "execution-revealed"]
    assert revealed and {r["requested"] for r in revealed} <= {"edgetam", "sam2.1-tiny"}  # the log in full after the vote


def test_scores_are_never_pooled_across_policies_and_defaults_come_from_route_votes():
    from orchestrator.arena import scoreboard

    def vote(policy, winner_option, n):
        out = []
        for _ in range(n):
            v = {"kind": "outline", "arms": {"A": "edgetam", "B": "sam2.1-tiny"}, "winner": "A" if winner_option == "edgetam" else "B",
                 "ms": {"A": 50, "B": 60}, "errors": {}}
            if policy:
                v["policy"] = policy
            out.append(v)
        return out

    board = scoreboard(vote(None, "sam2.1-tiny", 5) + vote("raw", "sam2.1-tiny", 5) + vote("route", "edgetam", 3))
    rows = {(o["option"], o["policy"]): o for o in board["kinds"][0]["options"]}
    assert rows[("edgetam", "route")]["trials"] == 3 and rows[("edgetam", "raw")]["trials"] == 5
    assert rows[("sam2.1-tiny", "route-legacy")]["wins"] == 5
    assert board["kinds"][0]["recommended"] == "edgetam"  # from route votes only


def test_the_runner_does_the_same_with_the_execution_log_off_on_or_failing():
    """The execution log only observes (contract 6; scaffolding review, M1.2 item 8): with it off, on,
    or failing (its spans and the interaction log both raising), the same implementation runs, the same
    attempts are recorded and the same output comes back."""
    from orchestrator import execution

    class Failing(execution.ExecutionLog):
        def _span(self, operation, parent, facts):
            raise RuntimeError("the execution log broke")

    def raising(record):
        raise RuntimeError("the interaction log broke")

    def once(log, interactions):
        async def go():
            h = hub(Gpu({"edgetam": []}))  # finds nothing: the spot ring runs, a declared fallback
            runner = Runner(h)
            runner.log = interactions
            ask = Prompt("point", 120, 215, spot=SimpleNamespace(x=120, y=215, radius=4.0))
            if log is None:
                return await runner.run("outline", view(), ask, origin="click")
            with log.request("click"):
                return await runner.run("outline", view(), ask, origin="click")

        done = run(go())
        e = done.execution
        return (e.operation, e.requested, e.policy, e.origin, e.executed,
                [(a.impl, a.ok, a.empty, a.why) for a in e.attempts], len(done.output["polygons"][0]))

    off = once(None, lambda record: None)
    on = once(execution.ExecutionLog(enabled=True), lambda record: None)
    failing = once(Failing(enabled=True), raising)
    assert off == on == failing and off[4] == "spot-ring"


def test_the_short_op_limit_keeps_the_click_bound():
    """A click waits at most for the short op running when it came, then its own (contract 5): under the
    300 ms click bound while each short op's p99 stays under SHORT_OP_P99_LIMIT_MS
    (bench/short_ops_bench.py measures them against it)."""
    from orchestrator.operations import SHORT_OP_P99_LIMIT_MS

    assert 2 * SHORT_OP_P99_LIMIT_MS <= 300


def test_a_point_given_to_sam_3_1_says_how_it_is_taken():
    for impl in ("sam3.1-local", "sam3.1-fal"):
        assert "32 px box" in OPERATIONS["outline"].impl(impl).note


def test_an_execution_line_keeps_the_logs_own_time_field():
    """Every interaction-log reader parses "at" as the log's time string (agent.log); an execution's
    own start time is "startedAt", so its line never overrides it (a float "at" broke the dataset
    importer, which skipped those lines)."""
    from orchestrator.operations import Execution

    assert "at" not in Execution("outline", "edgetam", "route", "click", 1, "settings").wire()


# --- the user's own Stop (user_stop.py; the Actions panel's Stop) -------------------------------------------------
class Begun(Gpu):
    """A worker that takes each call from its queue at once (it says so, as gpu_worker/worker.py does) and runs it for
    `delay`; `finished` counts the calls it ran to the end."""

    def __init__(self, delay=0.2):
        super().__init__(delay=delay)
        self.finished = 0

    async def call(self, op, timeout=30.0, priority=None, **payload):
        from orchestrator.gpu_client import STARTED

        STARTED.get()()  # taken from the queue: its work has begun
        reply = await super().call(op, timeout, priority, **payload)
        self.finished += 1
        return reply


async def _stoppable(runner, capture, ticket_id):
    """A request run as the Actions panel runs one: inside user_stop.stoppable(), its holder returned beside its task."""
    from orchestrator import user_stop

    with user_stop.stoppable() as stop:
        task = asyncio.ensure_future(runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id=ticket_id, choices={})))
    return stop, task


def test_the_users_stop_of_a_jobs_only_request_stops_its_begun_work():
    async def go():
        h = hub(Begun())
        runner = Runner(h)
        logged = []
        runner.log = logged.append
        stop, task = await _stoppable(runner, view(), "ra")
        await asyncio.sleep(0.02)
        job = next(iter(runner.jobs.values()))
        assert job.started
        stop.requested = True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.sleep(0.3)  # past the call's own length: it never finished
        return h, job, logged

    h, job, logged = run(go())
    assert job.task.cancelled() and job.stopped and h.gpu.finished == 0
    attempts = [r["attempts"] for r in logged if r["kind"] == "execution"][0]
    assert attempts[0]["notRun"] == "stopped"  # it began and was stopped: not "never ran", not a failure


def test_the_users_stop_drops_only_its_own_subscription_when_another_request_needs_the_job():
    async def go():
        h = hub(Begun())
        runner = Runner(h)
        capture = view()
        stop, mine = await _stoppable(runner, capture, "ra")
        other = asyncio.ensure_future(runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id="rb", choices={})))
        await asyncio.sleep(0.02)
        stop.requested = True
        mine.cancel()
        result = await other
        return h, mine, result

    h, mine, result = run(go())
    assert mine.cancelled() and h.gpu.finished == 1 and result.execution.executed == "edgetam"


def test_a_waiter_going_away_without_the_users_stop_lets_begun_work_complete():
    """Contract 4 unchanged: an ordinary cancellation (a disconnect, a request called off) of a job's only request
    drops its subscription; work already begun completes."""
    async def go():
        h = hub(Begun())
        runner = Runner(h)
        stop, task = await _stoppable(runner, view(), "ra")  # stoppable, but nobody pressed Stop
        await asyncio.sleep(0.02)
        job = next(iter(runner.jobs.values()))
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await job.task
        return h, job

    h, job = run(go())
    assert not job.task.cancelled() and not job.stopped and h.gpu.finished == 1


def test_the_users_stop_abandons_a_begun_gpu_call_without_stopping_the_shared_worker():
    """A GPU worker also serves clicks: a stopped call it has begun is abandoned by its client (its late reply dropped),
    never by stopping the worker; the next call is answered."""
    from orchestrator.gpu_client import GpuClient

    async def go():
        client = GpuClient("micromind-gpu")
        client._loop = asyncio.get_running_loop()
        client.ready = True
        worker = InboxWorker(client, op_s=0.15)
        serving = asyncio.ensure_future(worker.serve())
        h = hub(client)
        runner = Runner(h)
        stop, task = await _stoppable(runner, view(), "ra")
        await asyncio.sleep(0.05)
        job = next(iter(runner.jobs.values()))
        assert job.started
        stop.requested = True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert job.task.cancelled() and not client._pending  # nobody waits on it any more
        await asyncio.sleep(0.2)  # the worker finishes the call it began; its reply goes nowhere
        after = await client.call("segment", timeout=5.0, priority="click", model="edgetam")
        serving.cancel()
        return client, worker, after

    client, worker, after = run(go())
    assert after["ok"] and len(worker.ran) == 2 and not client.hung


class SlowToStop(Begun):
    """Begun work that takes `ending` to end once it is cancelled (a child still being reaped)."""

    def __init__(self, delay=0.5, ending=0.3):
        super().__init__(delay=delay)
        self.ending = ending

    async def call(self, op, timeout=30.0, priority=None, **payload):
        try:
            return await super().call(op, timeout, priority, **payload)
        except asyncio.CancelledError:
            await asyncio.sleep(self.ending)
            raise


def test_stopped_work_still_ending_past_the_wait_is_never_said_stopped_and_how_it_ended_is_kept(monkeypatch):
    """Codex's pre-review of 36a0237b, 1: past STOP_WAIT_S the Stop is not a confirmed stop; the job stays supervised
    and how it finally ended is logged and told to the Stop."""
    from orchestrator import operations

    monkeypatch.setattr(operations, "STOP_WAIT_S", 0.05)

    async def go():
        h = hub(SlowToStop())
        runner = Runner(h)
        logged = []
        runner.log = logged.append
        told = []
        stop, task = await _stoppable(runner, view(), "ra")
        stop.listeners.append(told.append)
        await asyncio.sleep(0.02)
        job = next(iter(runner.jobs.values()))
        stop.requested = True
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        answered = (set(stop.late), set(runner.stopping), job.task.done(), list(told))
        await asyncio.sleep(0.4)  # it ends (its own cancellation, 0.3 s)
        return job, answered, stop, runner, logged, told

    job, (late, stopping, done, told_then), stop, runner, logged, told = run(go())
    assert late == {job.task} and stopping == {job.task} and not done and told_then == []  # not a confirmed stop
    assert not stop.late and not runner.stopping and job.task.cancelled()
    assert told == [["stopped"]]  # how it really ended, once it did
    late_lines = [r for r in logged if r["kind"] == "stop-ended-late"]
    assert len(late_lines) == 1 and late_lines[0]["outcome"] == "stopped"


def test_a_request_that_has_its_result_no_longer_shares_the_job():
    """Codex's pre-review of 36a0237b, 2: every finished request's subscription goes, not only a cancelled one's."""
    async def go():
        h = hub(Gpu(delay=0.02))
        runner = Runner(h)
        capture = view()
        a = asyncio.ensure_future(runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id="ra", choices={})))
        b = asyncio.ensure_future(runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id="rb", choices={})))
        await asyncio.sleep(0)
        job = next(iter(runner.jobs.values()))
        await asyncio.sleep(0.005)
        held = len(job.subscribers)
        await asyncio.gather(a, b)
        return job, held

    job, held = run(go())
    assert held == 2 and job.subscribers == set()


def test_once_the_other_request_has_left_the_remaining_ones_stop_ends_the_begun_work():
    """Two requests share begun work; one goes away (its subscription removed, the work goes on: contract 4); the
    remaining one is then the only one, so its Stop ends the work."""
    async def go():
        h = hub(Begun())
        runner = Runner(h)
        capture = view()
        other = asyncio.ensure_future(runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id="rb", choices={})))
        stop, mine = await _stoppable(runner, capture, "ra")
        await asyncio.sleep(0.02)
        job = next(iter(runner.jobs.values()))
        other.cancel()
        await asyncio.sleep(0.01)
        alive = not job.task.done() and len(job.subscribers) == 1
        stop.requested = True
        mine.cancel()
        with pytest.raises(asyncio.CancelledError):
            await mine
        return h, job, alive

    h, job, alive = run(go())
    assert alive and job.task.cancelled() and job.stopped and h.gpu.finished == 0


def test_a_request_never_joins_a_job_that_is_being_stopped():
    """Codex's review of 8bd09b6a, 1: the same work asked for while a stopped job is still ending starts a fresh job;
    joining it would inherit its cancellation."""
    from orchestrator import operations

    async def go():
        h = hub(SlowToStop(delay=0.3, ending=0.3))
        runner = Runner(h)
        capture = view()
        stop, first = await _stoppable(runner, capture, "ra")
        await asyncio.sleep(0.02)
        dying = next(iter(runner.jobs.values()))
        stop.requested = True
        first.cancel()
        waiting = asyncio.ensure_future(asyncio.wait({first}))
        await asyncio.sleep(0.01)  # the Stop is waiting for the dying job (STOP_WAIT_S)
        again = await runner.run("outline", capture, CLICK, ticket=SimpleNamespace(id="rb", choices={}))
        await waiting
        return h, dying, again

    h, dying, again = run(go())
    assert dying.task.cancelled() and again.execution is not dying.execution and again.output["polygons"]
    assert len(h.gpu.calls) == 2 and h.gpu.finished == 1


def test_work_waits_for_its_model_while_it_loads_rather_than_refusing(monkeypatch):
    """Live, 30 Oct: the first "outline the stoma" after a restart was answered "the local outline model is still
    loading … or we can wait a moment". The work waits for the load (bounded), then runs."""
    import orchestrator.operations as operations

    warming = Gpu({"edgetam": []})
    warming.ready = False
    h = hub(warming)

    async def go():
        async def loaded():
            await asyncio.sleep(0.6)
            warming.ready = True
        asyncio.ensure_future(loaded())
        return await Runner(h).run("outline", view(), CLICK, origin="click")
    result = run(go())
    assert result.output["score"] == 0.9 and result.execution.attempts[-1].ok, "it ran once its model was ready"
    monkeypatch.setattr(operations, "LOADING_WAIT_S", 0.3)
    warming.ready = False
    with pytest.raises(operations.Unavailable, match="still loading"):
        run(Runner(h).run("outline", view(2), CLICK, origin="click"))
