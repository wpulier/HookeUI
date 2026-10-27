"""What a lower CPU priority and a thread cap cost our own requests on the analysis worker (gpu_worker/boot.py).

The heavy worker (and micro-sam's Python, which inherits its priority and caps) runs at a lower priority so a
model loading there gives way to the live view (live 2026-10-11 02:38: load 142, frame analysis 14 ms -> 2.2 s).
That is kept only where our own requests slow by at most 20% (the plan's rule): this bench times them under each
setting, on a quiet machine and under an outside CPU load (one busy process per core, as the sim's `model-load`):

- "outline all the cells": Cellpose-SAM's load, then its run on two frames of the real June 14 recording;
- a cold micro-sam outline: its own Python started, its model loaded, one point outlined;
- the find-like local work: EdgeTAM's automatic masks over the eyepiece (pointing "masks", CELLS
  "edgetam-automask") and a batch of DINOv2 embeddings (grouping cells by look).

  .venv/bin/python -m bench.priority_bench [--settings none,caps,nice,background] [--loads quiet,busy]

Settings: "none" (no cap, no priority: as before 2026-10-11), "caps" (the role's thread cap only), "nice"
(cap, and os.nice on the worker and micro-sam's Python), "background" (cap and macOS background policy), "chosen"
(what ships: the cap, normal priority, micro-sam's Python niced). Results: docs/bench/priority.md. It starts its
own workers; run it with the app stopped and no gate running (timings mean little while the meter is red for
another reason). Its outside load ends with it, whatever happens.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import statistics
import subprocess
import sys
import time

import cv2
import numpy as np

from gpu_worker.boot import THREADS
from orchestrator.gpu_client import GpuClient

# setting -> (the analysis worker's threads, its priority, micro-sam's Python's priority); "chosen" is what ships.
CAP = str(THREADS["heavy"])
SETTINGS = {"none": ("0", "none", "none"), "caps": (CAP, "none", "none"), "nice": (CAP, "nice", "nice"),
            "background": (CAP, "background", "nice"), "chosen": (CAP, "none", "nice")}


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


def crops(jpeg: bytes, n: int = 32) -> list[bytes]:
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    h, w = image.shape[:2]
    out = []
    for k in range(n):
        x, y = int(w * (0.3 + 0.4 * (k % 8) / 8)), int(h * (0.3 + 0.4 * (k // 8) / 4))
        out.append(cv2.imencode(".jpg", cv2.resize(image[y:y + 96, x:x + 96], (224, 224)))[1].tobytes())
    return out


async def timed(coro) -> float:
    t0 = time.perf_counter()
    await coro
    return time.perf_counter() - t0


async def one(setting: str, jpegs: list[bytes]) -> dict:
    threads, priority, microsam = SETTINGS[setting]
    os.environ["MICROMIND_HEAVY_THREADS"], os.environ["MICROMIND_HEAVY_PRIORITY"] = threads, priority
    os.environ["MICROMIND_MICROSAM_PRIORITY"] = microsam
    heavy = GpuClient("micromind-gpu-heavy", warm=False)
    await heavy.start()
    out = {}
    try:
        out["cellpose load"] = await timed(heavy.call("load", timeout=300, model="cellpose-sam"))
        runs = [await timed(heavy.call("cells", timeout=300, frame_id=10 + i, jpeg=j, model="cellpose-sam"))
                for i, j in enumerate(jpegs[:2])]
        out["cellpose cells"] = statistics.median(runs)
        h, w = cv2.imdecode(np.frombuffer(jpegs[0], np.uint8), cv2.IMREAD_GRAYSCALE).shape
        r = min(h, w) * 0.45
        await heavy.call("load", timeout=120, model="edgetam")  # its load apart from its run
        out["automask"] = await timed(heavy.call("automask", timeout=120, frame_id=30, jpeg=jpegs[1], disc=[w / 2, h / 2, r]))
        batch = crops(jpegs[0])
        await heavy.call("load", timeout=120, model="dinov2-small")
        out["embed 32"] = statistics.median([await timed(heavy.call("embed", timeout=120, jpegs=batch, model="dinov2-small"))
                                             for _ in range(3)])
        try:
            out["micro-sam cold"] = await timed(heavy.call("segment", timeout=600, priority="interactive", frame_id=50,
                                                           jpeg=jpegs[0], model="micro-sam-lm", points=[[w / 2, h / 2]]))
        except Exception as exc:
            out["micro-sam cold"] = float("nan")
            print(f"  micro-sam: {type(exc).__name__}: {exc}"[:200], flush=True)
    finally:
        heavy.stop()
    return out


KEYS = ("cellpose load", "cellpose cells", "automask", "embed 32", "micro-sam cold")
SETTING_LIMIT_S = 240.0  # a setting whose requests are starved past this is said so, not waited for


def row(load: str, setting: str, values: dict, base: dict | None) -> str:
    cells = []
    for k in KEYS:
        v, b = values.get(k, float("nan")), (base or {}).get(k, float("nan"))
        ratio = v / b if b == b and b and v == v else float("nan")
        cells.append("-" if v != v else f"{v:.2f} s" + ("" if setting == "none" or ratio != ratio else f" ({ratio - 1:+.0%})"))
    return f"| {load} | {setting} | " + " | ".join(cells) + " |"


async def main(settings: list[str], loads: list[str], n: int) -> None:
    jpegs = frames(n)
    print("| load | setting | " + " | ".join(KEYS) + " |\n|---|---|" + "---|" * len(KEYS), flush=True)
    for load in loads:
        busy = []
        if load == "busy":
            from sim.harness import busy_process  # spins until this bench is gone, whatever happens to it

            busy = [busy_process() for _ in range(os.cpu_count() or 4)]
            await asyncio.sleep(1.0)
        base = None
        try:
            for setting in settings:
                try:
                    values = await asyncio.wait_for(one(setting, jpegs), SETTING_LIMIT_S)
                except asyncio.TimeoutError:
                    print(f"| {load} | {setting} | starved: not done in {SETTING_LIMIT_S:.0f} s |", flush=True)
                    continue
                base = values if setting == "none" else base
                print(row(load, setting, values, base), flush=True)
        finally:
            for p in busy:
                p.kill()
            for p in busy:
                p.wait()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--settings", default="none,caps,nice,background")
    parser.add_argument("--loads", default="quiet,busy")
    parser.add_argument("--frames", type=int, default=3)
    args = parser.parse_args()
    asyncio.run(main(args.settings.split(","), args.loads.split(","), args.frames))
