"""Object tracking bench: does an outline stay on a thing that moves on its own? (option TRACK)

An eyepiece tour (bench/corpus/tour.py) carries a target whose true place in every frame is known:
  swimmer   a dark cell swimming on its own over the sparse sperm field, among other swimmers
  fixed     a dark cell drawn on the textured slide itself (it moves only with the slide)
The target is outlined at 1 s (as a click would), then every frame goes through the app's analysis
(Hub._analyse_batch) and, with TRACK "objects", the object follower (orchestrator/objtrack.py)
with EdgeTAM's video model in this process (gpu_worker/objects.py, as the heavy worker runs it).

Scored as the studio draws it (the same epoch, faded by speed as contract/overlay.json says):
  on target  visible frames whose outline centre is within the target's radius of the truth
  wrong      visible frames more than 2 radii off (an outline on the wrong thing)
  shown      share of frames the outline is visible at all
and its trail (orchestrator/trail.py), with TRACK "objects":
  trail      its own path along the kept segments against the truth's own path over the slide (the
             swimmer's; the fixed cell's is 0, so its trail must say nothing), how far each kept
             place is from the truth where it was kept, and its segments

    python -m bench.object_tracking                     # both conditions, both TRACK options
    python -m bench.object_tracking --only swimmer --seconds 20
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from bench.corpus.tour import Look, Tour, centre_at, disc_mask, phone, plan_path
from bench.tracking import PICTURES, draw_swimmers, swimmers

RESULTS = Path(__file__).resolve().parent / "results" / "object-tracking.json"
STEP_EVERY = 3  # frames between follower steps (10 a second at 30 fps, as objtrack.STEP_S)
TARGET_R = 9.0


async def run(condition: str, track: str, seconds: float, seed: int, session=None) -> dict:
    os.environ.setdefault("MICROMIND_HOME", "/tmp/micromind-bench-objtrack")
    from orchestrator.contract import OVERLAY
    from orchestrator.frames import Frame
    from orchestrator.hub import Hub
    from orchestrator.objtrack import ObjectFollower
    from orchestrator.options import Settings
    from orchestrator.scene import SceneObject, capture_template

    picture = "textured" if condition == "fixed" else "swimmers"
    image = cv2.imread(str(PICTURES[picture]))
    look, tour = Look(), Tour(seconds=seconds, seed=seed, whip_every=0)
    rng = np.random.default_rng(seed)
    if condition == "fixed":  # a dark cell on the slide itself, near the tour's start
        path = plan_path((image.shape[1], image.shape[0]), [], tour, look)
        sx, sy = centre_at(path, 0.0)
        target_on_slide = (sx + 60, sy - 30)
        image = image.copy()
        cv2.ellipse(image, (int(target_on_slide[0]), int(target_on_slide[1])), (14, 9), 20, 0, 360, (40, 40, 40), -1, cv2.LINE_AA)
    path = plan_path((image.shape[1], image.shape[0]), [], tour, look)
    mask, cells = disc_mask(look), swimmers(rng, 30, (look.width, look.height)) if condition == "swimmer" else []
    target = {"x": look.width / 2 + 40, "y": look.height / 2, "vx": 2.5, "vy": 1.0}
    hub = Hub("http://127.0.0.1:9", use_gpu=False)
    hub.settings = Settings(track=track)
    frames: dict[int, bytes] = {}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=frames[fid]) if fid in frames else None)

    class Heavy:  # the heavy worker's `track` op, in this process
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, **req):
            return {"ok": True, **session.step(req["jpeg"], req.get("start"), req.get("stop"))}

    hub.gpu_heavy = Heavy()
    follower = ObjectFollower(hub)
    clock = {"t": 0.0}
    hub.trails.clock = lambda: 1_800_000_000.0 + clock["t"]  # the tour's time, not this run's
    true_path, last_true, kept_errors, kept_seen = 0.0, None, [], 0
    true_known, kept_at = 0.0, None  # the truth's path over the spans the trail kept: (segment, its own place then)
    start_px, end_px, window = float(OVERLAY["fadeStartPx"]), float(OVERLAY["fadeEndPx"]), int(OVERLAY["speedWindow"])
    speeds, scored, prev = [], [], None
    n = int(seconds * look.fps)
    for i in range(n):
        t = i / look.fps
        cx, cy = centre_at(path, t)
        m = np.float32([[look.zoom, 0, look.width / 2 - cx * look.zoom], [0, look.zoom, look.height / 2 - cy * look.zoom]])
        view = cv2.warpAffine(image, m, (look.width, look.height), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        if condition == "swimmer":
            draw_swimmers(view, cells, rng)
            target["vx"] += rng.normal(0, 0.3)
            target["vy"] += rng.normal(0, 0.3)
            target["vx"], target["vy"] = float(np.clip(target["vx"], -4, 4)), float(np.clip(target["vy"], -4, 4))
            target["x"] = float(np.clip(target["x"] + target["vx"], 380, 900))
            target["y"] = float(np.clip(target["y"] + target["vy"], 160, 560))
            cv2.ellipse(view, (int(target["x"]), int(target["y"])), (14, 9), 0, 0, 360, (35, 35, 35), -1, cv2.LINE_AA)
            truth = (target["x"], target["y"])
        else:
            truth = ((target_on_slide[0] - cx) * look.zoom + look.width / 2, (target_on_slide[1] - cy) * look.zoom + look.height / 2)
        speed = 0.0 if prev is None else math.hypot(cx - prev[0], cy - prev[1]) * look.zoom
        bgr = phone(view, mask, look, rng, speed, 0.0 if prev is None else math.atan2(cy - prev[1], cx - prev[0]))
        prev = (cx, cy)
        clock["t"] = t
        jpeg = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 85])[1].tobytes()
        frames[i + 1] = jpeg
        frame = Frame(i + 1, jpeg, time.monotonic(), 1_800_000_000.0 + t)
        frame, info = hub._analyse_batch([frame])[0]
        # Published as the app does (Hub._publish keeps the frame's facts in `infos`, which captures
        # need: without it the follower found no view and never placed anything, and the slide's
        # speed for the fade was never measured; 2026-10-07).
        hub._publish(frame, info)
        last = hub.infos.get(i) if i else None
        speeds.append(math.hypot(info.dx - last.dx, info.dy - last.dy) if last and last.epoch == info.epoch else (end_px if last else 0.0))
        if i == 30:  # outlined at 1 s, where the target really is (as a click on it would)
            x, y = truth
            obj = SceneObject(id="o1", kind="highlight", polygons=[[[x - 14, y - 9], [x + 14, y - 9], [x + 14, y + 9], [x - 14, y + 9]]],
                              anchor_frame=info.id, anchor_epoch=info.epoch, anchor_offset=(info.dx, info.dy), source="user")
            captured = capture_template(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE), obj.bbox)
            if captured is not None:  # as a click does (tools.outline_at), so the follower's looks check runs
                obj.template, obj.template_origin = captured
            hub.scene.add(obj)
        if i >= 30 and track == "objects" and i % STEP_EVERY == 0:
            await follower.step()
            # The truth's own place over the slide (view px plus the slide's true shift) and where the
            # trail kept its newest place, drawn as the studio would (+ the tracker's offset now).
            own = (truth[0] + cx * look.zoom, truth[1] + cy * look.zoom)
            if last_true is not None:
                true_path += math.dist(own, last_true)
            last_true = own
            trail = hub.trails.trails.get("o1")
            points = sum(len(seg.points) for seg in trail.segments) if trail else 0
            if trail and points > kept_seen and trail.segments[-1].epoch == info.epoch:
                x, y = trail.segments[-1].points[-1][:2]
                kept_errors.append(math.hypot(x + info.dx - truth[0], y + info.dy - truth[1]))
                segment = trail.segments[-1]
                if kept_at is not None and kept_at[0] is segment:
                    true_known += math.dist(own, kept_at[1])
                kept_at = (segment, own)
            elif trail and trail.segments and trail.segments[-1].end != "open":
                kept_at = None
            kept_seen = points
        obj = hub.scene.objects.get("o1")
        if i > 30 and obj is not None:
            opacity = 1.0 - min(1.0, max(0.0, (max(speeds[-window:]) - start_px) / (end_px - start_px)))
            visible = obj.track == "tracking" and obj.anchor_epoch == info.epoch and opacity > 0.5
            if visible:
                pts = np.array([p for poly in obj.polygons for p in poly], float).mean(axis=0)
                drawn = (pts[0] + info.dx - obj.anchor_offset[0], pts[1] + info.dy - obj.anchor_offset[1])
                scored.append(math.hypot(drawn[0] - truth[0], drawn[1] - truth[1]))
            else:
                scored.append(None)
    seen = [e for e in scored if e is not None]
    trail = hub.trails.trails.get("o1")
    trail_out = None
    if track == "objects":
        kept_errors.sort()
        trail_out = {"path_px": round(trail.path_px(), 1) if trail else 0.0, "true_path_px": round(true_path, 1),
                     "true_known_px": round(true_known, 1),
                     "segments": len(trail.segments) if trail else 0,
                     "kept_error_p50": round(kept_errors[len(kept_errors) // 2], 1) if kept_errors else None,
                     "kept_error_p90": round(kept_errors[int(len(kept_errors) * 0.9)], 1) if kept_errors else None,
                     "words": hub.trails.words("o1", None)}
    return {"condition": condition, "track": track, "seed": seed, "frames": len(scored), "trail": trail_out,
            "shown": round(len(seen) / max(len(scored), 1), 3),
            "on_target": round(sum(e <= 1.5 * TARGET_R for e in seen) / max(len(scored), 1), 3),
            "wrong": round(sum(e > 3 * TARGET_R for e in seen) / max(len(scored), 1), 3),
            "error_p50": round(sorted(seen)[len(seen) // 2], 1) if seen else None}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", choices=["swimmer", "fixed"])
    parser.add_argument("--seconds", type=float, default=20.0)
    parser.add_argument("--seeds", default="7,8")
    args = parser.parse_args()
    from gpu_worker.objects import ObjectSession

    results = []
    for condition in [args.only] if args.only else ["swimmer", "fixed"]:
        for track in ("slide", "objects"):
            for seed in [int(s) for s in args.seeds.split(",")]:
                session = ObjectSession() if track == "objects" else None
                r = asyncio.run(run(condition, track, args.seconds, seed, session))
                results.append(r)
                print(f"{condition:8} {track:8} seed {seed}: on target {r['on_target']:.0%}  wrong {r['wrong']:.0%}  "
                      f"shown {r['shown']:.0%}  error p50 {r['error_p50']} px", flush=True)
                if r["trail"]:
                    tr = r["trail"]
                    print(f"{'':8} trail: path {tr['path_px']} px (truth {tr['true_known_px']} over the same spans, "
                          f"{tr['true_path_px']} in all), {tr['segments']} segments, "
                          f"kept places off by p50 {tr['kept_error_p50']} / p90 {tr['kept_error_p90']} px; says: {tr['words']}",
                          flush=True)
    RESULTS.parent.mkdir(parents=True, exist_ok=True)
    RESULTS.write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
