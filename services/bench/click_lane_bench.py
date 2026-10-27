"""Clicks beside background inference, under each way of putting them first (L1b part 2, item 3).

A click is an EdgeTAM point outline on the click worker. Alone it takes about 55 ms of the worker's time; in the sims of
2026-10-19/20 it took 94-549 ms while Cellpose-SAM found cells on the analysis worker and the follower stepped four
objects on its own worker (sim `cells-clicks`, `identity`). This bench starts the three workers the app runs, as the app
starts them (gpu_worker/boot.py roles), and times clicks:

- "alone": nothing else running;
- "beside": Cellpose-SAM outlining cells over and over on the analysis worker, and the follower stepping four objects
  ten times a second on the follow worker, as in `cells-clicks`.

Each under each setting:
- "today": as the app runs now (caps main 4, follow 4, analysis 2 threads; no lane);
- "lane-cells": MICROMIND_CLICK_LANE=cells (gpu_worker/lane.py: each Cellpose tile waits while a click is answered);
- "lane-all": MICROMIND_CLICK_LANE=all (the follower's steps wait too);
- "caps": a tighter cap beside the click worker (analysis 1 thread, follow 2);
- "lane-all+caps": both.

What it costs the background is timed beside it: Cellpose's run and the follower's step, and how long they gave way.
"busy" adds an outside CPU load of one busy process per core, as the sim's `model-load`.

  .venv/bin/python -m bench.click_lane_bench [--settings today,lane-cells,...] [--loads quiet,busy] [--clicks 24]

It starts its own workers (memory: about 3 GB); timings mean little while other heavy work runs: log `uptime` beside it.
Results: docs/bench/click-lane.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time

import cv2
import numpy as np

from gpu_worker import lane as lanes
from orchestrator.gpu_client import GpuClient

SETTINGS = {
    "today": {},
    "lane-cells": {"MICROMIND_CLICK_LANE": "cells"},
    "lane-all": {"MICROMIND_CLICK_LANE": "all"},
    "caps": {"MICROMIND_HEAVY_THREADS": "1", "MICROMIND_FOLLOW_THREADS": "2"},
    "lane-all+caps": {"MICROMIND_CLICK_LANE": "all", "MICROMIND_HEAVY_THREADS": "1", "MICROMIND_FOLLOW_THREADS": "2"},
}
KEYS = ("MICROMIND_CLICK_LANE", "MICROMIND_HEAVY_THREADS", "MICROMIND_FOLLOW_THREADS")
CLICK_GAP_S = 0.15  # as `cells-clicks`
STEP_S = 0.1  # the follower's pace (objtrack.STEP_S)


def frames(n: int) -> list[bytes]:
    from sim.harness import default_video

    video = cv2.VideoCapture(str(default_video()))
    total = int(video.get(cv2.CAP_PROP_FRAME_COUNT)) or 1000
    out = []
    for i in np.linspace(total * 0.2, total * 0.8, n).astype(int):
        video.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, image = video.read()
        if ok:
            out.append(cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
    video.release()
    return out


def pct(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else float("nan")


async def clicks(main: GpuClient, jpegs: list[bytes], spots: list[tuple[float, float]], n: int, base: int) -> dict:
    trips, worker = [], []
    for k in range(n):
        x, y = spots[k % len(spots)]
        jpeg = jpegs[k % len(jpegs)]
        t0 = time.perf_counter()
        reply = await main.call("segment", timeout=120, frame_id=base + k, jpeg=jpeg, model="edgetam", points=[[x, y]],
                                priority="click")
        trips.append((time.perf_counter() - t0) * 1000)
        worker.append(float(reply.get("ms") or 0.0))
        await asyncio.sleep(CLICK_GAP_S)
    return {"tripP50": pct(trips, 50), "tripP95": pct(trips, 95), "workerP50": pct(worker, 50), "workerP95": pct(worker, 95)}


async def background(heavy: GpuClient, follow: GpuClient, jpegs: list[bytes], boxes: list[dict], stop: asyncio.Event,
                     out: dict) -> None:
    async def cells() -> None:
        k = 0
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                reply = await heavy.call("cells", timeout=300, priority="background", frame_id=10_000 + k,
                                         jpeg=jpegs[k % len(jpegs)], model="cellpose-sam")
            except Exception as exc:  # said in the row, never the bench's end
                out["failed"].append(f"cells: {type(exc).__name__}")
                k += 1
                continue
            out["cells"].append(time.perf_counter() - t0)
            out["cellsWaitedMs"] += float(reply.get("laneWaitedMs") or 0.0)
            k += 1

    async def following() -> None:
        seq = 0
        while not stop.is_set():
            seq += 1
            t0 = time.perf_counter()
            try:
                reply = await follow.call("track", timeout=10.0, priority="interactive", frame_id=20_000 + seq,
                                          jpeg=jpegs[seq % len(jpegs)], start=boxes if seq == 1 else [], stop=[],
                                          session=out["session"], seq=seq, reset=seq == 1)
            except Exception as exc:  # a step past the follower's own limit (objtrack: 10 s), as the app counts it
                out["failed"].append(f"track: {type(exc).__name__}")
                await asyncio.sleep(STEP_S)
                continue
            out["steps"].append((time.perf_counter() - t0) * 1000)
            out["stepsWaitedMs"] += float(reply.get("laneWaitedMs") or 0.0)
            await asyncio.sleep(max(0.0, STEP_S - (time.perf_counter() - t0)))

    await asyncio.gather(cells(), following())


async def watch(marked: list, stop: asyncio.Event) -> None:
    """While the clicks run: the share of the time the lane said a click was being answered (its own check)."""
    seen = lanes.Lane(lanes.path(os.getpid()), role=None)
    while not stop.is_set():
        marked.append(1 if seen.others() else 0)
        await asyncio.sleep(0.01)


async def one(setting: str, jpegs: list[bytes], n: int, session: int) -> dict:
    saved = {key: os.environ.get(key) for key in KEYS}
    for key in KEYS:
        os.environ.pop(key, None)
    os.environ.update(SETTINGS[setting])
    main = GpuClient("micromind-gpu")
    heavy = GpuClient("micromind-gpu-heavy", warm=False)
    follow = GpuClient("micromind-gpu-follow", warm=False)
    try:
        await main.start()
        await heavy.start()
        await follow.start()
        await heavy.call("load", timeout=300, model="cellpose-sam")
        await follow.call("load", timeout=300, model="edgetam-video")
        h, w = cv2.imdecode(np.frombuffer(jpegs[0], np.uint8), cv2.IMREAD_GRAYSCALE).shape
        spots = [(w * fx, h * fy) for fx in (0.42, 0.5, 0.58) for fy in (0.42, 0.5, 0.58)]
        boxes = [{"id": f"o{i}", "box": [x - 25, y - 25, x + 25, y + 25]} for i, (x, y) in enumerate(spots[:4])]
        await clicks(main, jpegs, spots, 3, 90_000)  # first use apart
        alone = await clicks(main, jpegs, spots, n, 100_000)
        out = {"cells": [], "steps": [], "failed": [], "cellsWaitedMs": 0.0, "stepsWaitedMs": 0.0, "session": session}
        stop = asyncio.Event()
        running = asyncio.ensure_future(background(heavy, follow, jpegs, boxes, stop, out))
        await asyncio.sleep(2.0)  # Cellpose's first run under way, the follower stepping
        marked = []
        watching = asyncio.ensure_future(watch(marked, stop)) if lanes.mode() != "off" else None
        beside = await clicks(main, jpegs, spots, n, 200_000)
        stop.set()
        await asyncio.wait_for(running, 300)
        if watching is not None:
            await watching
        return {"alone": alone, "beside": beside, "cellsS": statistics.median(out["cells"]) if out["cells"] else float("nan"),
                "cellsRuns": len(out["cells"]), "stepP50": pct(out["steps"], 50), "stepP95": pct(out["steps"], 95),
                "cellsWaitedMs": round(out["cellsWaitedMs"]), "stepsWaitedMs": round(out["stepsWaitedMs"]),
                "markedShare": round(sum(marked) / len(marked), 2) if marked else None, "failed": out["failed"]}
    finally:
        for client in (main, heavy, follow):
            try:
                client.stop()
            except Exception:
                pass
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def row(load: str, setting: str, r: dict) -> str:
    a, b = r["alone"], r["beside"]
    return (f"| {load} | {setting} | {a['tripP50']:.0f} / {a['tripP95']:.0f} | {b['tripP50']:.0f} / {b['tripP95']:.0f} | "
            f"{b['workerP50']:.0f} / {b['workerP95']:.0f} | {r['cellsS']:.2f} s ({r['cellsRuns']}) | "
            f"{r['stepP50']:.0f} / {r['stepP95']:.0f} | {r['cellsWaitedMs']} / {r['stepsWaitedMs']} |"
            f" {'-' if r['markedShare'] is None else r['markedShare']} |"
            + (f" failed: {', '.join(r['failed'])}" if r["failed"] else ""))


async def main(settings: list[str], loads: list[str], n: int, frames_n: int) -> None:
    jpegs = frames(frames_n)
    print("| load | setting | click alone p50 / p95 ms | click beside p50 / p95 ms | worker's own beside p50 / p95 ms | "
          "Cellpose run, median (runs) | follower step p50 / p95 ms | gave way: cells / steps ms | clicks marked |\n"
          "|---|---|---|---|---|---|---|---|---|", flush=True)
    results = []
    session = 1
    for load in loads:
        busy = []
        if load == "busy":
            from sim.harness import busy_process  # spins until this bench is gone, whatever happens to it

            busy = [busy_process() for _ in range(os.cpu_count() or 4)]
            await asyncio.sleep(1.0)
        try:
            for setting in settings:
                session += 1
                r = await one(setting, jpegs, n, session)
                results.append({"load": load, "setting": setting, **r})
                print(row(load, setting, r), flush=True)
        finally:
            for p in busy:
                p.kill()
            for p in busy:
                p.wait()
    print(json.dumps(results))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", default=",".join(SETTINGS))
    parser.add_argument("--loads", default="quiet,busy")
    parser.add_argument("--clicks", type=int, default=24)
    parser.add_argument("--frames", type=int, default=6)
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.settings.split(","), args.loads.split(","), args.clicks, args.frames)))
