"""The watcher on footage (orchestrator/view_watch.py): which events it raises, how long after the thing happened, how
many when nothing happens (false alarms per hour), and what it costs the frame path.

Every frame goes through the hub's own per-frame analysis, as the live app does (Hub._analyse_batch: the eyepiece disc,
the slide's motion and epoch, sharpness and light, the disc's edge), then to the watcher with the coach beside it; the
clock is the footage's own, so a run is the same on a loaded machine. Nothing is sent anywhere and no model is called.

Synthetic footage (the default): the bundled example (assets/examples/human_mitosis.png, CC0) as a brightfield-like
slide seen through an eyepiece, with what a hand-held phone on a scope adds: sensor noise, sub-pixel shake and a jolt
now and then, the focus breathing (sharpness swings like the June 14 footage's: 6% median, up to about 30% within a
second), light flicker and JPEG at the bridge's quality. Each scenario knows when its event happened (the truth):

  steady   nothing happens for --minutes: every event is a false alarm
  pan      the slide moved by most of the eyepiece and stopped: one slide-moved
  cut      the view jumped (continuity lost) and stayed: one slide-moved
  defocus  the focus drifted to a blur and stayed: one focus-drifted
  light    the light dimmed by half and stayed: nothing (light is not focus)
  knob     the knob turned through focus and left as blurred as defocus: nothing (the user did it)

  cd services && .venv/bin/python -m bench.watch_bench                      # every scenario, 10 min steady
  cd services && .venv/bin/python -m bench.watch_bench --minutes 60 --only steady
  cd services && .venv/bin/python -m bench.watch_bench --video REC.mp4      # a real recording: every event listed

A real recording has no truth here: its events are listed with their times for a person to check against the video
(a steady stretch of one, with nothing happening, gives its false alarms per hour). Results: docs/bench/voice-journeys.md, "The watcher on footage".
"""
from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator import view_watch as W
from orchestrator.coach import Coach
from orchestrator.fov import FovTracker, edge_radius
from orchestrator.motion import MotionTracker
from orchestrator.quality import Quality, view_stats

ROOT = Path(__file__).resolve().parents[2]
STILL = ROOT / "assets" / "examples" / "human_mitosis.png"
W_, H_ = 1280, 720
CX, CY, R = 640.0, 360.0, 300.0
JPEG_QUALITY = 80


class Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def slide() -> np.ndarray:
    """The example as a brightfield-like slide (dark nuclei on a light ground), mirrored 3 x 3 and enlarged."""
    im = cv2.imread(str(STILL), cv2.IMREAD_GRAYSCALE).astype(np.float32)
    im = 225.0 - 150.0 * np.clip(im / max(float(np.percentile(im, 99.5)), 1.0), 0, 1)
    row = np.concatenate([im, im[:, ::-1], im], axis=1)
    tiled = np.concatenate([row, row[::-1], row], axis=0)
    return cv2.resize(tiled, None, fx=1.3, fy=1.3, interpolation=cv2.INTER_CUBIC)


class Scope:
    """One frame of the eyepiece view: where on the slide, the blur, the light and the disc."""

    def __init__(self, seed: int = 3) -> None:
        self.slide = slide()
        self.rng = np.random.default_rng(seed)
        self.x, self.y = self.slide.shape[1] / 2, self.slide.shape[0] / 2
        self.blur, self.gain, self.disc = 0.0, 1.0, R
        yy, xx = np.mgrid[0:H_, 0:W_]
        dist = np.hypot(xx - CX, yy - CY)
        self._dist = dist.astype(np.float32)

    def frame(self, t: float) -> np.ndarray:
        # The hand-held phone: sub-pixel shake; the focus breathing (sharpness swings); light flicker; the disc.
        sx, sy = self.rng.normal(0, 0.35, 2)
        breathe = 0.35 + 0.18 * math.sin(2 * math.pi * t / 6.5) + self.rng.normal(0, 0.04)
        sigma = max(0.0, math.hypot(self.blur, breathe))
        gain = self.gain * (1 + 0.02 * math.sin(2 * math.pi * t * 1.3))
        r = self.disc * (1 + 0.0005 * math.sin(2 * math.pi * t / 9))
        half = int(R + 40)
        x0, y0 = self.x + sx - half, self.y + sy - half
        m = np.float32([[1, 0, -x0], [0, 1, -y0]])
        view = cv2.warpAffine(self.slide, m, (2 * half, 2 * half), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REFLECT)
        if sigma > 0.15:
            view = cv2.GaussianBlur(view, (0, 0), sigma)
        canvas = np.zeros((H_, W_), np.float32)
        cy0, cx0 = int(CY) - half, int(CX) - half
        canvas[cy0:cy0 + 2 * half, cx0:cx0 + 2 * half] = view * gain
        lit = np.clip((r - self._dist) / 3.0, 0, 1) * (1 - 0.25 * (self._dist / r) ** 2)
        out = canvas * lit + self.rng.normal(0, 2.5, (H_, W_)).astype(np.float32)
        return np.clip(out, 0, 255).astype(np.uint8)


def scenario(name: str, minutes: float) -> tuple[list, list[tuple[str, str]]]:
    """(steps, expected): steps are (seconds, change) with changes applied smoothly over the step; expected is
    [(kind, the step at whose end it happened)]."""
    still = [(4.0, {})]
    if name == "steady":
        jolts = []
        for k in range(int(minutes * 60 / 25)):  # a jolt of the table every 25 s: the slide shakes and comes back
            jolts += [(24.6, {}), (0.2, {"dx": 3.0}), (0.2, {"dx": -3.0})]
        return still + jolts, []
    if name == "pan":
        return still + [(1.5, {"dx": 260.0, "dy": -90.0}), (8.0, {})], [("slide-moved", 1)]
    if name == "cut":
        return still + [(0.0, {"jump": (900.0, 600.0)}), (8.0, {})], [("slide-moved", 1)]
    if name == "defocus":
        return still + [(3.0, {"blur": 2.6}), (10.0, {})], [("focus-drifted", 1)]
    if name == "light":
        return still + [(3.0, {"gain": 0.5}), (10.0, {})], []
    if name == "knob":
        return still + [(0.8, {"blur": 2.5}), (0.8, {"blur": -2.3}), (0.6, {"blur": 1.9}), (10.0, {})], []
    raise ValueError(name)


def analyse(frames, fps: float, *, mode: str = "chatty", t0: float = 1000.0):
    """Every frame through the hub's analysis and the watcher; yields (t, info) and returns what it raised."""
    fov_tracker, motion, quality = FovTracker(), MotionTracker(), Quality()
    clock = Clock(t0)
    coach = Coach(lambda m: None, clock=clock)
    hub = SimpleNamespace(latest_info=None, layer=None, view_held=False)
    logged = []
    watch = W.ViewWatch(hub, mode=lambda: mode, log=logged.append,
                        sweep=lambda: None if coach.sweeping_since is None else ("phone" if coach.phone else "knob"))
    spent, analysed = [], 0
    for i, gray in enumerate(frames):
        t = t0 + i / fps
        clock.t = t
        ok, jpeg = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        gray = cv2.imdecode(jpeg, cv2.IMREAD_GRAYSCALE)
        fov = fov_tracker.update(gray)
        sample = motion.update(i + 1, gray, fov)
        focus, mean, std, saturated = view_stats(gray, fov)
        info = SimpleNamespace(id=i + 1, width=gray.shape[1], height=gray.shape[0], dx=sample.dx, dy=sample.dy,
                               epoch=sample.epoch, tracked=sample.tracked, steady=sample.steady, fov=fov, wall_at=t,
                               focus=focus, quality=quality.flags(focus, mean, std, saturated, sample.epoch),
                               stats=(focus, mean, std, saturated), peak=quality.peak,
                               disc=edge_radius(gray, fov) or 0.0)
        hub.latest_info = info
        coach.frame(info)
        start = time.perf_counter()
        watch.frame(info)
        spent.append(time.perf_counter() - start)
        analysed += 1
    events = [r for r in logged if r["kind"] == "watch-event"]
    return events, spent, analysed


def run_scenario(name: str, minutes: float, fps: float) -> dict:
    steps, expected = scenario(name, minutes)
    counts = [max(1, int(round(seconds * fps))) if seconds > 0 else 0 for seconds, _ in steps]
    ends = list(np.cumsum(counts) / fps)  # when each step ended, on the footage's clock
    total = sum(counts)

    def frames():  # rendered one at a time: an hour of frames is never held
        scope, t = Scope(), 0.0
        for (seconds, change), n in zip(steps, counts):
            if "jump" in change:
                scope.x += change["jump"][0]
                scope.y += change["jump"][1]
            for _ in range(n):
                scope.x += change.get("dx", 0.0) / n
                scope.y += change.get("dy", 0.0) / n
                scope.blur = max(0.0, scope.blur + change.get("blur", 0.0) / n)
                scope.gain += change.get("gain", 0.0) / n
                yield scope.frame(t)
                t += 1 / fps

    events, spent, n = analyse(frames(), fps)
    t0 = 1000.0
    matched, latencies = [], []
    for kind, step in expected:
        happened = t0 + ends[step]
        hit = next((e for e in events if e["event"] == kind and e["eventT"] >= happened - 0.5
                    and e not in matched), None)
        if hit is not None:
            matched.append(hit)
            latencies.append(round(hit["eventT"] - happened, 2))
    false = [e for e in events if e not in matched]
    hours = (total / fps) / 3600
    return {"scenario": name, "seconds": round(total / fps, 1), "expected": [k for k, _ in expected],
            "found": len(matched), "missed": len(expected) - len(matched), "latencyS": latencies,
            "falseAlarms": len(false), "falsePerHour": round(len(false) / hours, 2) if name == "steady" else None,
            "falseEvents": [{"event": e["event"], "atS": round(e["eventT"] - t0, 1), "facts": e["facts"]}
                            for e in false][:5],
            "frameCostUs": {"p50": round(1e6 * float(np.percentile(spent, 50)), 1),
                            "p99": round(1e6 * float(np.percentile(spent, 99)), 1),
                            "max": round(1e6 * max(spent), 1)}}


def run_video(path: str, fps_cap: float | None) -> dict:
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    def frames():
        while True:
            ok, bgr = cap.read()
            if not ok:
                return
            yield cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    events, spent, n = analyse(frames(), fps)
    hours = n / fps / 3600
    return {"video": path, "seconds": round(n / fps, 1),
            "events": [{"event": e["event"], "atS": round(e["eventT"] - 1000.0, 1), "facts": e["facts"]}
                       for e in events],
            "perHour": round(len(events) / hours, 2) if hours else None,
            "frameCostUs": {"p50": round(1e6 * float(np.percentile(spent, 50)), 1),
                            "p99": round(1e6 * float(np.percentile(spent, 99)), 1)}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=float, default=10.0, help="the steady scenario's length")
    parser.add_argument("--fps", type=float, default=30.0)
    parser.add_argument("--only", default="", help="comma-separated scenarios")
    parser.add_argument("--video", default="", help="a recording instead of the synthetic scenarios")
    parser.add_argument("--json", default="", help="write the results here too")
    args = parser.parse_args()
    if args.video:
        results = [run_video(args.video, None)]
    else:
        names = args.only.split(",") if args.only else ["pan", "cut", "defocus", "light", "knob", "steady"]
        results = []
        for name in names:
            start = time.monotonic()
            result = run_scenario(name, args.minutes, args.fps)
            result["benchS"] = round(time.monotonic() - start, 1)
            results.append(result)
            print(json.dumps(result, ensure_ascii=False))
    if args.video:
        print(json.dumps(results[0], ensure_ascii=False, indent=1))
    if args.json:
        Path(args.json).write_text(json.dumps(results, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
