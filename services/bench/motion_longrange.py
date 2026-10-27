"""Long-range overlay accuracy: does an outline placed N seconds ago still sit on the same
structure? Compares the tracker's offset difference between frames 2, 5 and 10 s apart
with a direct ORB match between those frames (same motion epoch only)."""
from __future__ import annotations

import argparse
import json
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
    parser.add_argument("--every", type=int, default=30)
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    cap = cv2.VideoCapture(args.video)
    fovs, tracker = FovTracker(), MotionTracker()
    kept: dict[int, tuple[np.ndarray, object]] = {}
    fid = 0
    while True:
        ok, img = cap.read()
        if not ok:
            break
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        fov = fovs.update(gray)
        tracker.update(fid, gray, fov)
        if fid % args.every == 0:
            kept[fid] = (gray, fov, tracker.samples[fid])
        fid += 1

    results = {}
    for seconds in (2, 5, 10):
        gap = seconds * 30
        errors, moved = [], []
        for a in sorted(kept):
            b = a + gap
            if b not in kept:
                continue
            (ga, fa, sa), (gb, fb, sb) = kept[a], kept[b]
            if sa.epoch != sb.epoch or fb is None:
                continue
            ref = orb_translation(ga, gb, fb.mask(gb.shape[1], gb.shape[0], erode_px=int(fb.radius * 0.08)), min_inliers=25)
            if ref is None:
                continue
            est = (sb.dx - sa.dx, sb.dy - sa.dy)
            errors.append(float(np.hypot(est[0] - ref[0], est[1] - ref[1])))
            moved.append(float(np.hypot(*ref[:2])))
        e, m = np.array(errors), np.array(moved)
        big = e[m > 30]
        results[f"{seconds}s"] = {
            "pairs": int(e.size),
            "p50": round(float(np.median(e)), 1) if e.size else None,
            "p90": round(float(np.percentile(e, 90)), 1) if e.size else None,
            "max": round(float(e.max()), 1) if e.size else None,
            "afterPans": {"pairs": int(big.size), "p50": round(float(np.median(big)), 1) if big.size else None, "p90": round(float(np.percentile(big, 90)), 1) if big.size else None},
        }
    report = {"video": Path(args.video).name, "loopClosures": len(tracker.corrections), "epochs": tracker.epoch + 1, "errorPx": results}
    print(json.dumps(report, indent=2))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
