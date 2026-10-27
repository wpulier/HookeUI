"""What the motion tool measures on real footage, and how long it takes (orchestrator/movement.py).

Plays a stretch of a recording through the hub's own eyepiece and slide-motion trackers (as
the live app does), then measures what moves. Prints the measurements the model would be
given and writes the trails picture beside the results.

  cd services && .venv/bin/python -m bench.motion_bench VIDEO [--start 600] [--seconds 4] [--objective 40]

Without a truth file, the numbers are for a person to check against the picture; with the
synthetic dots in tests/test_movement.py the speeds are checked against the truth.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

import cv2

from orchestrator.fov import FovTracker
from orchestrator.motion import MotionTracker
from orchestrator.movement import ClipFrame, follow, measure, trails, words
from orchestrator.scale import Scale

BENCH = Path(__file__).resolve().parent


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video")
    parser.add_argument("--start", type=float, default=0.0)
    parser.add_argument("--seconds", type=float, default=4.0)
    parser.add_argument("--objective", type=float, default=0.0, help="the objective, for µm (0: px)")
    args = parser.parse_args()
    cap = cv2.VideoCapture(args.video)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cap.set(cv2.CAP_PROP_POS_MSEC, args.start * 1000)
    fov_tracker, motion = FovTracker(), MotionTracker()
    frames, last_bgr, fov = [], None, None
    for i in range(int(args.seconds * fps)):
        ok, bgr = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
        fov = fov_tracker.update(gray) or fov
        sample = motion.update(i, gray, fov)
        frames.append(ClipFrame(i / fps, gray, sample.dx, sample.dy))
        last_bgr = bgr
    if not frames:
        raise SystemExit("no frames read")
    um_per_px, scale_words = None, ""
    if args.objective and fov is not None:
        scale = Scale()
        scale.set_objective(args.objective)
        reading = scale.reading(fov.radius)
        if reading:
            um_per_px, scale_words = reading.um_per_px, f"{args.objective:g}x objective (estimate)"
    t0 = time.perf_counter()
    tracks = follow(frames, fov)
    m = measure(frames, tracks, um_per_px)
    ms = (time.perf_counter() - t0) * 1000
    print(words(m, scale_words))
    print(f"[{len(frames)} frames, {ms:.0f} ms to measure, {ms / len(frames):.1f} ms a frame]")
    out = BENCH / "results" / f"motion-{Path(args.video).stem[:40]}-{args.start:g}s.jpg"
    offset = (frames[-1].dx - frames[0].dx, frames[-1].dy - frames[0].dy)
    cv2.imwrite(str(out), trails(last_bgr, m, offset))
    print(f"trails: {out}")


if __name__ == "__main__":
    main()
