"""Tracking bench: does an outline stay on its thing, and is it ever drawn in the wrong place?

An eyepiece tour of a still picture (bench/corpus/tour.py: rests, drifts, whip-pans, motion blur,
noise, compression) knows where the view is on every frame, so where an outline made earlier
should be now is known exactly. Every frame goes through the app's own analysis
(Hub._analyse_batch: the eyepiece disc, slide motion, epochs) as the live feed does, and is scored:

  shown      the app would draw an outline made 1 s earlier (same epoch: the offset is trusted, and
             the studio has not faded it out for speed: contract/overlay.json fadeStartPx..fadeEndPx
             over speedWindow frames, on the app's own measured speed, as the studio does)
  wrong      ...at least half visible, and more than WRONG_PX from where its thing really is
  drift p90  how far off the shown outlines are (px)
  epochs/min continuity given up (outlines then wait to be re-found, never drawn unverified)

The headline is `wrong`: an outline drawn in the wrong place is worse than one not drawn.

Conditions: a textured slide (histology), a sparse field (a still of the 2026-10-06 sperm slide),
and that sparse field with swimmers (dark cells moving on their own, as live sperm do), which is
what made the live tracker draw outlines in the wrong place on 2026-10-06.

    python -m bench.tracking                 # all conditions, the default seeds
    python -m bench.tracking --only swimmers --seconds 20

Pictures are not in git: the histology still is from the corpus (make corpus), the sperm field is
written by `--prepare RECORDING.mp4` into the assets store's bench/tracking/ (assets/README.md).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

import cv2
import numpy as np

from bench.corpus.tour import Look, Tour, centre_at, disc_mask, phone, plan_path

ROOT = Path(__file__).resolve().parents[2]
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
DATA = store().bench("tracking")
RESULTS = Path(__file__).resolve().parent / "results" / "tracking.json"
ORB_EVERY = 1  # option MOTION_ORB_EVERY: the consensus vote every this many frames (--orb-every)
WRONG_PX = 12.0  # an outline this far from its thing is in the wrong place (a stoma is ~40 px across)
AGE_FRAMES = 30  # outlines made this long ago (1 s) are checked on every frame

SOURCES = store().corpus_sources  # the public corpus as downloaded
PICTURES = {
    "textured": SOURCES / "berkshire-histology/Connective Tissue Adipose (41066512834).jpg",
    "sparse": DATA / "sperm-field-20261006.png",
    "swimmers": DATA / "sperm-field-20261006.png",
}


def swimmers(rng: np.random.Generator, n: int, size: tuple[int, int]) -> list[dict]:
    """Dark cells that swim on their own across the view (frame pixels; not tied to the slide)."""
    w, h = size
    return [{"x": rng.uniform(0, w), "y": rng.uniform(0, h), "vx": rng.normal(0, 4), "vy": rng.normal(0, 4),
             "r": rng.uniform(5, 9)} for _ in range(n)]


def draw_swimmers(view: np.ndarray, cells: list[dict], rng: np.random.Generator) -> None:
    h, w = view.shape[:2]
    for c in cells:
        c["vx"] += rng.normal(0, 0.6)
        c["vy"] += rng.normal(0, 0.6)
        c["x"], c["y"] = (c["x"] + c["vx"]) % w, (c["y"] + c["vy"]) % h
        cv2.ellipse(view, (int(c["x"]), int(c["y"])), (int(c["r"] * 1.6), int(c["r"])),
                    math.degrees(math.atan2(c["vy"], c["vx"])), 0, 360, (60, 60, 60), -1, cv2.LINE_AA)


def run(condition: str, seconds: float, seed: int) -> dict:
    os.environ.setdefault("MICROMIND_HOME", str(Path("/tmp") / "micromind-bench-tracking"))
    from orchestrator.frames import Frame
    from orchestrator.hub import Hub

    image = cv2.imread(str(PICTURES[condition]))
    if image is None:
        raise SystemExit(f"missing picture for {condition}: {PICTURES[condition]} (see the module docstring)")
    look, tour = Look(), Tour(seconds=seconds, seed=seed)
    path = plan_path((image.shape[1], image.shape[0]), [], tour, look)
    mask, rng = disc_mask(look), np.random.default_rng(seed)
    cells = swimmers(rng, 40, (look.width, look.height)) if condition == "swimmers" else []
    hub = Hub("http://127.0.0.1:9", use_gpu=False)
    hub.motion.orb_every = ORB_EVERY  # option MOTION_ORB_EVERY (--orb-every); the hub sets it per frame when live
    history, prev, ms = [], None, []
    n = int(seconds * look.fps)
    for i in range(n):
        cx, cy = centre_at(path, i / look.fps)
        speed = 0.0 if prev is None else math.hypot(cx - prev[0], cy - prev[1]) * look.zoom
        angle = 0.0 if prev is None else math.atan2(cy - prev[1], cx - prev[0])
        m = np.float32([[look.zoom, 0, look.width / 2 - cx * look.zoom], [0, look.zoom, look.height / 2 - cy * look.zoom]])
        view = cv2.warpAffine(image, m, (look.width, look.height), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        if cells:
            draw_swimmers(view, cells, rng)
        bgr = phone(view, mask, look, rng, speed, angle)
        jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        t0 = time.perf_counter()
        _analysed_frame, info = hub._analyse_batch([Frame(i + 1, jpeg, time.monotonic(), 1_800_000_000.0 + i / look.fps)])[0]
        ms.append((time.perf_counter() - t0) * 1000)
        # The slide's content moves opposite to the view centre: its true offset in frame pixels.
        history.append((info.epoch, info.dx, info.dy, -cx * look.zoom, -cy * look.zoom, speed))
        prev = (cx, cy)
    from orchestrator.contract import OVERLAY

    start, end, window = float(OVERLAY["fadeStartPx"]), float(OVERLAY["fadeEndPx"]), int(OVERLAY["speedWindow"])
    app_speed = [0.0] + [math.hypot(b[1] - a[1], b[2] - a[2]) if a[0] == b[0] else end
                         for a, b in zip(history, history[1:])]
    shown = wrong = 0
    errors = []
    for i in range(AGE_FRAMES, len(history)):
        e0, dx0, dy0, tx0, ty0, _ = history[i - AGE_FRAMES]
        e1, dx1, dy1, tx1, ty1, sp = history[i]
        opacity = 1.0 - min(1.0, max(0.0, (max(app_speed[max(0, i - window + 1):i + 1]) - start) / (end - start)))
        if e1 != e0 or opacity <= 0.0:
            continue  # continuity was given up (re-found later, never drawn unverified), or faded out
        shown += 1
        err = math.hypot((dx1 - dx0) - (tx1 - tx0), (dy1 - dy0) - (ty1 - ty0))
        errors.append(err)
        wrong += err > WRONG_PX and opacity >= 0.5
    checked = len(history) - AGE_FRAMES
    errors.sort()
    return {"condition": condition, "seed": seed, "frames": len(history),
            "shown": round(shown / max(checked, 1), 3), "wrong": round(wrong / max(checked, 1), 4),
            "drift_p50": round(errors[len(errors) // 2], 1) if errors else None,
            "drift_p90": round(errors[int(len(errors) * 0.9)], 1) if errors else None,
            "epochs_per_min": round((history[-1][0] - history[0][0]) / (len(history) / look.fps / 60), 1),
            "analysis_ms_p50": round(sorted(ms)[len(ms) // 2], 1)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=sorted(PICTURES))
    parser.add_argument("--seconds", type=float, default=40.0)
    parser.add_argument("--seeds", default="7,8")
    parser.add_argument("--prepare", help="a recording of the sperm slide: writes the sparse field picture")
    parser.add_argument("--at", type=float, default=1000.0, help="seconds into --prepare's recording")
    parser.add_argument("--orb-every", type=int, default=1, help="option MOTION_ORB_EVERY: vote every this many frames")
    args = parser.parse_args()
    global ORB_EVERY
    ORB_EVERY = args.orb_every
    if args.prepare:
        cap = cv2.VideoCapture(args.prepare)  # a file on disk, never the phone (AGENTS.md)
        cap.set(cv2.CAP_PROP_POS_MSEC, args.at * 1000)
        ok, frame = cap.read()
        if not ok:
            raise SystemExit("could not read the recording")
        DATA.mkdir(parents=True, exist_ok=True)
        crop = frame[152:708, 362:918]  # inside the eyepiece disc of the 2026-10-06 setup
        cv2.imwrite(str(PICTURES["sparse"]), cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC))
        return
    results = [run(c, args.seconds, int(s)) for c in ([args.only] if args.only else list(PICTURES))
               for s in args.seeds.split(",")]
    for r in results:
        print(f"{r['condition']:9} seed {r['seed']}: shown {r['shown']:.0%}  wrong {r['wrong']:.1%}  "
              f"drift p50/p90 {r['drift_p50']}/{r['drift_p90']} px  epochs/min {r['epochs_per_min']}  "
              f"analysis {r['analysis_ms_p50']} ms")
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
