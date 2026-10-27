"""The GPU worker's client (orchestrator/gpu_client.py) never holds the event loop, says a model's load, and never
takes a load for a hang (live 2026-10-11 02:38-02:39: one frame's publish took 46.8 s, the follow worker was reported
hung after 42 s instead of 15, and micro-sam's 120 s cold start counted against the 180 s outline timeout)."""
import asyncio
import os
import queue
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from orchestrator.gpu_client import GRACE_S, GpuClient, Superseded
from tests.waiting import until_async


def run(coro):
    return asyncio.run(coro)


class SlowPipe:
    """A pipe the worker drains slowly: each send blocks until `drain` is set (a worker starved, or loading)."""

    def __init__(self):
        self.drain = threading.Event()
        self.sent = []

    def send(self, message):
        self.drain.wait(5)
        self.sent.append(message)


def writing(client, conn):
    """The client as `_start` leaves it: requests written by its writer thread."""
    client._loop = asyncio.get_running_loop()
    client._conn = conn
    client._outbox = queue.SimpleQueue()
    threading.Thread(target=client._writer, args=(client._outbox, conn), daemon=True).start()


async def loop_gaps(until: asyncio.Event) -> float:
    """The largest gap between ticks of the loop (s) while it runs, until `until` is set."""
    worst, last = 0.0, time.monotonic()
    while not until.is_set():
        await asyncio.sleep(0.005)
        now = time.monotonic()
        worst, last = max(worst, now - last), now
    return worst


def test_a_worker_slow_to_drain_its_pipe_never_holds_the_loop(wall_clock):
    async def go():
        client = GpuClient("micromind-gpu-follow")
        pipe = SlowPipe()
        writing(client, pipe)
        done = asyncio.Event()
        gaps = asyncio.ensure_future(loop_gaps(done))
        calls = [asyncio.ensure_future(client.call("segment", timeout=5.0, priority="click", jpeg=b"x" * 200_000))
                 for _ in range(3)]
        await asyncio.sleep(0.4)  # the worker drains nothing for 0.4 s
        pipe.drain.set()
        await until_async(lambda: len(pipe.sent) == 3)
        for message in pipe.sent:
            client._replied({"id": message["id"], "ok": True, "polygons": []})
        replies = await asyncio.gather(*calls)
        done.set()
        return await gaps, replies, pipe

    worst, replies, pipe = run(go())
    wall_clock.within(worst, 0.1, "the loop's worst gap")  # before: it waited inside conn.send while the worker drained
    assert all(r["ok"] for r in replies) and len(pipe.sent) == 3


def test_a_request_given_up_or_superseded_before_it_was_written_is_never_sent():
    async def go():
        client = GpuClient("micromind-gpu-follow")
        pipe = SlowPipe()
        writing(client, pipe)
        first = asyncio.ensure_future(client.call("track", timeout=5.0, priority="interactive", frame_id=1))
        await asyncio.sleep(0.05)  # being written (the pipe is full)
        second = asyncio.ensure_future(client.call("track", timeout=5.0, priority="interactive", frame_id=2))
        await asyncio.sleep(0.01)
        third = asyncio.ensure_future(client.call("track", timeout=5.0, priority="interactive", frame_id=3))
        await asyncio.sleep(0.01)
        with pytest.raises(Superseded):
            await second  # replaced by the third before it was written: answered here, never sent
        pipe.drain.set()
        await asyncio.sleep(0.2)
        for message in pipe.sent:
            if message.get("op") == "track":
                client._replied({"id": message["id"], "ok": True, "objects": {}})
        await asyncio.gather(first, third, return_exceptions=True)
        return pipe.sent

    sent = run(go())
    frames = [m.get("frame_id") for m in sent if m.get("op") == "track"]
    assert frames == [1, 3]  # the first was already in the pipe; the second never left


def test_a_load_moves_the_calls_deadline_and_the_run_keeps_the_time_it_had():
    """A call given 0.3 s whose model takes 0.5 s to load is not timed out: the load is said, and the run then has
    the time it had left when the load began."""
    async def go():
        client = GpuClient("micromind-gpu-heavy")
        client._loop = asyncio.get_running_loop()
        sent = []
        client._conn = SimpleNamespace(send=sent.append)
        call = asyncio.ensure_future(client.call("cells", timeout=0.3, priority="background", model="micro-sam-lm"))
        await asyncio.sleep(0.01)
        rid = sent[0]["id"]
        client._replied({"id": rid, "started": True})
        client._replied({"id": rid, "load": "loading", "model": "micro-sam-lm", "limitS": 300})
        loading = dict(client.loading)
        await asyncio.sleep(0.5)  # past the call's own timeout, while loading
        client._replied({"id": rid, "load": "loaded", "model": "micro-sam-lm", "seconds": 0.5, "memoryMb": 2400, "pid": 4321})
        await asyncio.sleep(0.1)
        client._replied({"id": rid, "ok": True, "polygons": []})
        reply = await call
        return client, loading, reply

    client, loading, reply = run(go())
    assert "micro-sam-lm" in loading and reply["ok"]
    assert client.resident["micro-sam-lm"]["sizeMb"] == 2400 and client.children["micro-sam-lm"] == 4321
    assert client.loading == {}
    # ...and once loaded, its time runs out as before.

    async def late():
        client = GpuClient("micromind-gpu-heavy")
        client._loop = asyncio.get_running_loop()
        sent = []
        client._conn = SimpleNamespace(send=sent.append)
        call = asyncio.ensure_future(client.call("cells", timeout=0.3, priority="background", model="micro-sam-lm"))
        await asyncio.sleep(0.01)
        rid = sent[0]["id"]
        client._replied({"id": rid, "started": True})
        client._replied({"id": rid, "load": "loading", "model": "micro-sam-lm", "limitS": 300})
        client._replied({"id": rid, "load": "loaded", "model": "micro-sam-lm", "seconds": 0.0, "memoryMb": 10})
        with pytest.raises(asyncio.TimeoutError):
            await call

    run(late())


def test_a_load_has_its_own_hang_limit():
    client = GpuClient("micromind-gpu-heavy")
    told = []
    client.on_problem.append(told.append)
    now = time.monotonic()
    client._outstanding[7] = ["segment", "interactive", 30.0 + GRACE_S, now - 100, None]
    client._loading_for[7] = {"model": "micro-sam-lm", "since": now - 100, "limitS": 300.0, "left": 30.0}
    client.check_hung(now)
    assert client.hung == ""  # 100 s into a load allowed 300 s: loading, not hung
    client.check_hung(now + 210)
    assert client.hung.startswith("loading micro-sam") and "running for 310 s" in client.hung
    assert told and "not responding" in told[0]
    client._loading_for.pop(7)
    client._outstanding[7][3] = now + 210  # loaded: its run's own limit counts from now
    client.check_hung(now + 211)
    assert client.hung == "" and told[-1] == ""


def test_the_hang_check_runs_on_its_own_thread_while_the_loop_is_busy():
    """Live 02:38: the worker was reported hung after 42 s, not 15, because the check ran on the stalled loop."""
    async def go():
        client = GpuClient("micromind-gpu-follow")
        client._loop = asyncio.get_running_loop()
        client._outstanding[3] = ["track", "interactive", 0.2, time.monotonic(), None]
        client.watch()
        time.sleep(1.5)  # the loop held: nothing else runs
        marked = client.hung
        client.unwatch()
        return marked

    assert run(go()).startswith("track running for")


def test_the_worker_starts_capped_and_at_its_roles_priority(monkeypatch):
    from gpu_worker import boot

    # The live roles get more threads; measured, no worker is niced (micro-sam's own Python is: microsam_cells.NICE).
    assert boot.threads("main") > boot.threads("heavy") and boot.priority("heavy") == "" == boot.priority("main")
    assert boot.caps(None) == {} and boot.priority(None) == ""  # a bench's own worker starts as before
    monkeypatch.setenv("MICROMIND_HEAVY_THREADS", "6")
    monkeypatch.setenv("MICROMIND_HEAVY_PRIORITY", "background")
    assert boot.threads("heavy") == 6 and boot.priority("heavy") == "background"
    monkeypatch.setenv("MICROMIND_HEAVY_THREADS", "0")
    monkeypatch.setenv("MICROMIND_HEAVY_PRIORITY", "none")
    assert boot.caps("heavy") == {} and boot.priority("heavy") == ""
    monkeypatch.delenv("OMP_NUM_THREADS", raising=False)
    with boot.spawn_env("main"):
        assert os.environ["OMP_NUM_THREADS"] == str(boot.THREADS["main"])
    assert "OMP_NUM_THREADS" not in os.environ  # put back after the spawn


def test_a_process_the_worker_started_for_a_model_ends_with_it():
    import subprocess

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    client = GpuClient("micromind-gpu-heavy")
    client.children["micro-sam-lm"] = child.pid
    client.stop()
    assert child.wait(timeout=5) is not None and client.children == {}


def test_micro_sams_start_is_governed_by_its_own_limit(tmp_path):
    """STARTUP_S (unused before) bounds micro-sam's start; past it the process is stopped and the load fails, said."""
    from noncommercial.microsam_cells import MicroSamCells

    silent = MicroSamCells(command=[sys.executable, "-c", "import time; time.sleep(30)"])
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="did not start within"):
        silent.load("micro-sam-lm", limit_s=0.5)
    assert time.monotonic() - t0 < 5 and silent.pid is None


def test_a_warm_up_that_cannot_load_the_outline_model_never_stops_the_worker_serving():
    # 2026-10-12: on a Mac without EdgeTAM downloaded, the warm-up raised and the click worker never became ready, so
    # every click waited on "Loading the outline model" even after the download. The warm-up is a head start only.
    client = GpuClient()

    async def refuses(op, **kwargs):
        raise RuntimeError("NotDownloaded: EdgeTAM isn't downloaded yet (56 MB)")

    client.call = refuses
    run(client.warm_up())
    assert "isn't downloaded yet" in client.warm_error

    async def loads(op, **kwargs):
        return {}

    client.call = loads
    run(client.warm_up())
    assert client.warm_error == ""
