"""Measure motion-tracking accuracy and speed on a recording.

Accuracy is checked against an independent method: ORB features + RANSAC translation
between frame i and frame i+gap at full resolution, inside the eyepiece disc. The
tracker's chained offsets should agree with that direct estimate.
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

import cv2
import numpy as np

from orchestrator.fov import FovTracker
from orchestrator.motion import MotionTracker, orb_translation

REPO = Path(__file__).resolve().parents[2]
from orchestrator.assets import store  # noqa: E402  (the assets store: assets/README.md)
DEFAULT = store().real_file("2026-06-14-leaf-epidermis-3m43s")  # assets/manifest.json


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--video", default=str(DEFAULT))
    parser.add_argument("--gap", type=int, default=15, help="frames between ORB checks")
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.video)
    fovs, tracker = FovTracker(), MotionTracker()
    grays: dict[int, np.ndarray] = {}
    times, errors, magnitudes, untracked, breaks = [], [], [], 0, 0
    frame_id = 0
    while True:
        ok, img = cap.read()
        if not ok or (args.max_frames and frame_id >= args.max_frames):
            break
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        t0 = time.perf_counter()
        fov = fovs.update(gray)
        sample = tracker.update(frame_id, gray, fov)
        times.append((time.perf_counter() - t0) * 1000)
        untracked += 0 if sample.tracked else 1

        if frame_id % args.gap == 0:
            grays[frame_id] = gray
            prev = frame_id - args.gap
            if prev in grays and fov is not None:
                mask = fov.mask(gray.shape[1], gray.shape[0], erode_px=int(fov.radius * 0.08))
                ref = orb_translation(grays[prev], gray, mask)
                a, b = tracker.offset(prev), tracker.offset(frame_id)
                if ref and a and b and not tracker.same_epoch(prev, frame_id):
                    breaks += 1
                elif ref and a and b:
                    est = (b[0] - a[0], b[1] - a[1])
                    errors.append(float(np.hypot(est[0] - ref[0], est[1] - ref[1])))
                    magnitudes.append(float(np.hypot(*ref[:2])))
                grays.pop(prev - args.gap, None)
        frame_id += 1

    e = np.array(errors) if errors else np.array([np.nan])
    m = np.array(magnitudes) if magnitudes else np.array([np.nan])
    moving = e[m > 2] if errors else e
    result = {
        "video": Path(args.video).name,
        "frames": frame_id,
        "trackerMsPerFrame": {"p50": round(float(np.percentile(times, 50)), 2), "p95": round(float(np.percentile(times, 95)), 2)},
        "untrackedFrames": untracked,
        "continuityBreaks": breaks,
        "epochs": tracker.epoch + 1,
        "checks": len(errors),
        "checkGapFrames": args.gap,
        "motionPxPerCheck": {"p50": round(float(np.nanpercentile(m, 50)), 1), "p95": round(float(np.nanpercentile(m, 95)), 1)},
        "errorPxVsOrb": {
            "p50": round(float(np.nanpercentile(e, 50)), 2),
            "p90": round(float(np.nanpercentile(e, 90)), 2),
            "max": round(float(np.nanmax(e)), 2),
        },
        "errorPxWhileMoving": {
            "count": int(moving.size),
            "p50": round(float(np.nanpercentile(moving, 50)), 2) if moving.size else None,
            "p90": round(float(np.nanpercentile(moving, 90)), 2) if moving.size else None,
        },
        "finalOffsetPx": tracker.offset(frame_id - 1),
    }
    print(json.dumps(result, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
