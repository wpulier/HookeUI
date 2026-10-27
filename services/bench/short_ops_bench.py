"""The short ops' times, pinned (contract 5 of docs/design/2026-10-06-captures-operations-evidence.md).

The click bound rests on them: the newest click waits at most for the one short op running when it
came, then its own, so a click stays under its 300 ms p95 only while every short op's p99 stays under
SHORT_OP_P99_LIMIT_MS (operations.py). This bench times, on frames of the real June 14 recording, each
interactive outline implementation (a point on a new frame, a second point on the same frame, a box)
and fails (exit 1) when a p99 passes the limit. Each model is used once first (its first use loads
it). It also times a tracking step of four objects on the follow worker: that step decides where
`track` runs (contract 5: on the click worker only if its p99 keeps the click bound, else on its own
follow worker), so it is reported against the same limit but never fails the bench. The worker's own
time (`ms`) is what the bound needs; the round trip is shown beside it.

  .venv/bin/python -m bench.short_ops_bench [--frames 20]

Timings mean little while the performance meter is red (swap, load): say which it was.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import cv2
import numpy as np

from orchestrator.gpu_client import GpuClient
from orchestrator.operations import OPERATIONS, SHORT_OP_P99_LIMIT_MS


def frames(path: str, n: int) -> list[bytes]:
    """`n` frames spread over the recording (a file, never the camera)."""
    video = cv2.VideoCapture(path)
    total = int(video.get(cv2.CAP_PROP_FRAME_COUNT)) or 1000
    out = []
    for i in np.linspace(total * 0.1, total * 0.9, n).astype(int):
        video.set(cv2.CAP_PROP_POS_FRAMES, int(i))
        ok, image = video.read()
        if ok:
            out.append(cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 90])[1].tobytes())
    video.release()
    return out


def p(values: list[float], q: float) -> float:
    return float(np.percentile(values, q)) if values else float("nan")


async def main(n: int) -> int:
    from sim.harness import default_video

    jpegs = frames(str(default_video()), n)
    h, w = cv2.imdecode(np.frombuffer(jpegs[0], np.uint8), cv2.IMREAD_GRAYSCALE).shape
    spots = [(w * fx, h * fy) for fx in (0.4, 0.5, 0.6) for fy in (0.4, 0.5, 0.6)]
    rows, failed = [], []
    clicks = GpuClient("micromind-gpu")
    await clicks.start()
    interactive = [i for i in OPERATIONS["outline"].implementations if i.worker == "interactive"]
    for impl in interactive:
        await clicks.call("segment", timeout=120, frame_id=99_999, jpeg=jpegs[0], model=impl.id,  # loads the model
                          points=[list(spots[4])], priority="click")
        times: dict[str, list[float]] = {"point, new frame": [], "point, same frame": [], "box": []}
        trips: dict[str, list[float]] = {k: [] for k in times}
        for f, jpeg in enumerate(jpegs):
            for k, (x, y) in enumerate(spots[:3]):
                case = "point, new frame" if k == 0 else "point, same frame"
                t0 = time.perf_counter()
                reply = await clicks.call("segment", frame_id=100_000 + f, jpeg=jpeg, model=impl.id, points=[[x, y]],
                                          priority="click")
                trips[case].append((time.perf_counter() - t0) * 1000)
                times[case].append(float(reply.get("ms") or 0.0))
            x, y = spots[4]
            t0 = time.perf_counter()
            reply = await clicks.call("segment", frame_id=100_000 + f, jpeg=jpeg, model=impl.id,
                                      box=[x - 30, y - 30, x + 30, y + 30], priority="click")
            trips["box"].append((time.perf_counter() - t0) * 1000)
            times["box"].append(float(reply.get("ms") or 0.0))
        for case, values in times.items():
            rows.append({"op": f"{impl.id}: {case}", "n": len(values), "p50": p(values, 50), "p99": p(values, 99),
                         "tripP99": p(trips[case], 99)})
    clicks.stop()

    follow = GpuClient("micromind-gpu-follow", warm=False)
    await follow.start()
    boxes = [{"id": f"o{i}", "box": [x - 25, y - 25, x + 25, y + 25]} for i, (x, y) in enumerate(spots[:4])]
    steps, trips = [], []
    for f, jpeg in enumerate(jpegs * 3):
        t0 = time.perf_counter()
        reply = await follow.call("track", timeout=10.0, priority="interactive", frame_id=200_000 + f, jpeg=jpeg,
                                  start=boxes if f == 0 else [], stop=[], session=1, seq=f + 1, reset=f == 0)
        if f:
            trips.append((time.perf_counter() - t0) * 1000)
            steps.append(float(reply.get("ms") or 0.0))
    follow.stop()
    rows.append({"op": "track: a step of 4 objects (follow worker)", "n": len(steps), "p50": p(steps, 50),
                 "p99": p(steps, 99), "tripP99": p(trips, 99), "decides": "where track runs"})

    print(f"{'op':48s} {'n':>4s} {'p50 ms':>8s} {'p99 ms':>8s} {'trip p99':>9s}  (limit {SHORT_OP_P99_LIMIT_MS} ms)")
    for r in rows:
        over = r["p99"] > SHORT_OP_P99_LIMIT_MS
        if r.get("decides"):
            verdict = ("  over the limit: it needs its own follow worker" if over
                       else "  under the limit: it could share the click worker")
        else:
            failed += [r["op"]] if over else []
            verdict = "  OVER" if over else ""
        print(f"{r['op']:48s} {r['n']:4d} {r['p50']:8.1f} {r['p99']:8.1f} {r['tripP99']:9.1f}{verdict}")
    print(json.dumps({"rows": rows, "limitMs": SHORT_OP_P99_LIMIT_MS, "over": failed}))
    return 1 if failed else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--frames", type=int, default=20)
    sys.exit(asyncio.run(main(parser.parse_args().frames)))
