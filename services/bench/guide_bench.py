"""Guiding the user's hands (orchestrator/guide.py) in a closed loop: a synthetic eyepiece view of a slide, a simulated
person who follows each line with imperfect hands, and every frame through the hub's own analysis (the eyepiece disc,
the slide's motion and epoch, sharpness), as the live app does. Each run knows the truth: where the target is and how
far the focus is from sharp. Nothing is sent anywhere; a model is called only by the model arms.

  cd services && .venv/bin/python -m bench.guide_bench                       # the rule, every case
  cd services && .venv/bin/python -m bench.guide_bench --judge model         # the fast model (needs its key)
  cd services && .venv/bin/python -m bench.guide_bench --judge model --model gemini-3.5-flash-lite

The cases: the target off in each of four directions, near and far; the phone sitting upright (the picture moves
opposite to the slide, as a compound microscope's does) and turned a quarter on the eyepiece (directions must be
learned from the user's moves); starting in and out of focus. The person: reacts 0.6 s after a line, moves the slide
the asked way by roughly the asked amount (60-140%), at hand speed, and turns the fine focus a step per focus line.
Results: docs/bench/guide.md.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator import guide as G
from orchestrator.coach import Coach
from orchestrator.fov import FovTracker
from orchestrator.motion import MotionTracker
from orchestrator.quality import view_stats

ROOT = Path(__file__).resolve().parents[2]
STILL = ROOT / "assets" / "examples" / "human_mitosis.png"  # CC0, bundled
W_, H_ = 960, 540
CX, CY, R = 480.0, 270.0, 240.0
FPS = 20.0
JPEG_QUALITY = 80
AMOUNT_R = {"nudge": 0.1, "little": 0.22, "some": 0.45, "far": 0.85}  # what the simulated hand means by each word
FOCUS_STEP = 0.55  # one focus line turns the knob this far (z units; blur sigma = BLUR_PER_Z * |z|)
BLUR_PER_Z = 1.6
HAND_SPEED_R = 0.7  # radii of picture per second while the hand moves
REACT_S = 0.6


def slide() -> np.ndarray:
    """The bundled example as a brightfield-like slide, mirrored 3 x 3 so it has room to move over."""
    im = cv2.imread(str(STILL), cv2.IMREAD_GRAYSCALE).astype(np.float32)
    im = 225.0 - 150.0 * np.clip(im / max(float(np.percentile(im, 99.5)), 1.0), 0, 1)
    row = np.concatenate([im, im[:, ::-1], im], axis=1)
    tiled = np.concatenate([row, row[::-1], row], axis=0)
    return cv2.resize(tiled, None, fx=1.3, fy=1.3, interpolation=cv2.INTER_CUBIC)


SLIDE: np.ndarray | None = None


class Stage:
    """The view: where on the slide the eyepiece's centre is (`x`, `y`, slide px), the focus (`z`, 0 sharp) and how the
    picture sits relative to the hands (`turn`: quarter turns of the phone on the eyepiece). The hands move the slide;
    the picture moves opposite (a compound microscope), turned by `turn`."""

    def __init__(self, x: float, y: float, z: float = 0.0, turn: int = 0, seed: int = 1) -> None:
        global SLIDE
        if SLIDE is None:
            SLIDE = slide()
        self.slide = SLIDE
        self.x, self.y, self.z, self.turn = x, y, z, turn
        self.rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:H_, 0:W_]
        d = np.hypot(xx - CX, yy - CY).astype(np.float32)
        self.lit = np.clip((R - d) / 3.0, 0, 1) * (1 - 0.25 * (d / R) ** 2)

    def picture_of(self, sx: float, sy: float) -> tuple[float, float]:
        """Where a slide point is in the picture now (camera px), the phone's turn included."""
        dx, dy = sx - self.x, sy - self.y
        for _ in range(self.turn % 4):
            dx, dy = -dy, dx
        return CX + dx, CY + dy

    def hand(self, hx: float, hy: float) -> None:
        """The hands moved the slide by (hx, hy) slide px in their own frame: the view's centre moves the same way on
        the slide (the picture moves opposite)."""
        self.x += hx
        self.y += hy

    def frame(self) -> np.ndarray:
        half = int(R + 20)  # the disc and a margin; fits the frame
        sx, sy = self.rng.normal(0, 0.3, 2)  # the phone's sub-pixel shake
        m = np.float32([[1, 0, -(self.x + sx - half)], [0, 1, -(self.y + sy - half)]])
        view = cv2.warpAffine(self.slide, m, (2 * half, 2 * half), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        for _ in range(self.turn % 4):
            view = cv2.rotate(view, cv2.ROTATE_90_CLOCKWISE)
        sigma = BLUR_PER_Z * abs(self.z) + 0.3
        view = cv2.GaussianBlur(view, (0, 0), sigma)
        canvas = np.zeros((H_, W_), np.float32)
        canvas[int(CY) - half:int(CY) + half, int(CX) - half:int(CX) + half] = view
        out = canvas * self.lit + self.rng.normal(0, 2.0, (H_, W_)).astype(np.float32)
        return np.clip(out, 0, 255).astype(np.uint8)


@dataclass
class Person:
    """Follows each line: after REACT_S, moves the slide the asked way by about the asked amount, at hand speed;
    a focus line turns the knob one step. "Stop" stops the hand where it is."""
    stage: Stage
    rng: np.random.Generator
    moves: list = field(default_factory=list)  # (start_t, end_t, hx, hy) slide px over the move
    focus_at: list = field(default_factory=list)  # (t, dz)
    heard: list = field(default_factory=list)

    def hear(self, t: float, line: dict) -> None:
        self.heard.append((t, line))
        if line.get("event") != "line":
            return
        say = (line.get("say") or "").lower()
        if say.startswith("stop"):
            self.moves = [(s, min(e, t + 0.25), hx * (min(e, t + 0.25) - s) / max(e - s, 1e-6),
                           hy * (min(e, t + 0.25) - s) / max(e - s, 1e-6)) for s, e, hx, hy in self.moves if s < t + 0.25]
        hand = G.hand_vector(line.get("slide"))
        if hand is not None:
            amount = AMOUNT_R.get((line.get("slide") or {}).get("amount") or "little", 0.22)
            dist = amount * R * float(self.rng.uniform(0.6, 1.4))
            start = t + REACT_S
            end = start + dist / (HAND_SPEED_R * R)
            self.moves.append((start, end, hand[0] * dist, hand[1] * dist))
        if line.get("focus") in ("up", "down"):
            small = any(word in say for word in ("touch", "hair", "tiny", "slightly", "back a little"))
            self.focus_at.append((t + REACT_S, FOCUS_STEP * (0.5 if small else 1.0) * float(self.rng.uniform(0.7, 1.3))
                                  * (1 if line["focus"] == "up" else -1)))

    def step(self, t: float, dt: float) -> None:
        for s, e, hx, hy in self.moves:
            a, b = max(s, t - dt), min(e, t)
            if b > a:
                share = (b - a) / max(e - s, 1e-6)
                self.stage.hand(hx * share, hy * share)
        self.moves = [m for m in self.moves if m[1] > t]
        for when, dz in list(self.focus_at):
            if when <= t:
                self.stage.z += dz
                self.focus_at.remove((when, dz))


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


@dataclass
class Case:
    name: str
    off: tuple[float, float]  # where the target starts, in picture radii from the centre (+x right, +y down)
    z: float = 0.0
    turn: int = 0
    until: str = "sharp"


CASES = [
    Case("right-near", (0.45, 0.0)), Case("left-far", (-0.8, 0.1)), Case("up", (0.1, -0.6)),
    Case("down-right", (0.5, 0.5)), Case("out-of-focus", (0.4, -0.3), z=1.4), Case("focus-other-way", (0.2, 0.4), z=-1.2),
    Case("phone-turned", (0.6, 0.2), turn=1), Case("centred-only", (-0.5, -0.4), until="centred"),
]


async def run(case: Case, *, judge: str = "rule", ask_model=None, seed: int = 1, seconds: float = 90.0,
              realtime: bool | None = None) -> dict:
    """One guidance from start to its end, in footage time (paced in real time when a model judges, so its answers
    take the time they take). Returns what happened and the truth at the end."""
    realtime = judge == "model" if realtime is None else realtime
    started_wall = time.monotonic()
    rng = np.random.default_rng(seed)
    clock = Clock()
    # The target: a slide point, put `off` radii from the centre of the picture at the start.
    stage = Stage(0.0, 0.0, z=case.z, turn=case.turn, seed=seed)
    stage.x, stage.y = SLIDE.shape[1] / 2, SLIDE.shape[0] / 2
    ox, oy = case.off[0] * R, case.off[1] * R
    for _ in range((4 - case.turn % 4) % 4):  # the slide point that shows at picture offset (ox, oy)
        ox, oy = -oy, ox
    target_slide = (stage.x + ox, stage.y + oy)
    person = Person(stage, rng)
    fov_tracker, motion = FovTracker(), MotionTracker()
    coach = Coach(lambda m: None, clock=clock)
    scene = SimpleNamespace(objects={})
    hub = SimpleNamespace(latest_info=None, scene=scene)
    logged: list[dict] = []
    guide = G.Guide(hub, judge=lambda: judge, ask_model=ask_model, say=lambda g, line: person.hear(clock.t, line),
                    sweep=lambda: None if coach.sweeping_since is None else ("phone" if coach.phone else "knob"),
                    log=logged.append, clock=clock, wall=clock)
    started = None
    g = None
    frames = 0
    dt = 1.0 / FPS
    while clock.t - 1000.0 < seconds:
        person.step(clock.t, dt)
        gray = stage.frame()
        ok, jpeg = cv2.imencode(".jpg", gray, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        gray = cv2.imdecode(jpeg, cv2.IMREAD_GRAYSCALE)
        frames += 1
        fov = fov_tracker.update(gray)
        sample = motion.update(frames, gray, fov)
        focus, mean, std, saturated = view_stats(gray, fov)
        info = SimpleNamespace(id=frames, width=W_, height=H_, dx=sample.dx, dy=sample.dy, epoch=sample.epoch,
                               tracked=sample.tracked, steady=sample.steady, fov=fov, wall_at=clock.t, focus=focus,
                               stats=(focus, mean, std, saturated), peak=0.0, disc=0.0)
        hub.latest_info = info
        coach.frame(info)
        if g is None and frames >= int(1.5 * FPS) and fov is not None and sample.steady:
            # The target is outlined on this frame: its centre where the slide point shows now.
            px, py = stage.picture_of(*target_slide)
            scene.objects["o1"] = SimpleNamespace(id="o1", center=(px, py), anchor_offset=(sample.dx, sample.dy),
                                                  anchor_epoch=sample.epoch, label="target")
            started = clock.t
            g = guide.start("o1", name="target", asked="centre it", until=case.until)
        guide.frame(info)
        if realtime:  # a model's answer takes real time: the footage keeps its own pace meanwhile
            await asyncio.sleep(max(0.0, (started_wall + (clock.t - 1000.0)) - time.monotonic()))
        for _ in range(30):  # the guidance's loop runs to its next wait (a rule's answer takes no time)
            await asyncio.sleep(0)
        if g is not None and g.event:
            # let the hand settle: the last line's move ends before the truth is read
            break
        clock.t += dt
    if g is not None and not g.event:
        guide.stop("the bench ran out of time")
    px, py = stage.picture_of(*target_slide)
    off_r = math.hypot(px - CX, py - CY) / R
    judged = [r for r in logged if r["kind"] == "guide-judge"]
    return {"case": case.name, "judge": judge, "seed": seed, "event": g.event if g else "never-started",
            "seconds": round(clock.t - (started or clock.t), 1), "lines": len(g.lines) if g else 0,
            "offR": round(off_r, 3), "blur": round(abs(stage.z) * BLUR_PER_Z, 2),
            "centred": off_r <= G.CENTRED_R, "sharp": abs(stage.z) * BLUR_PER_Z <= 0.9,
            "said": [line.get("say") for _, line in person.heard],
            "overrides": sum(1 for r in logged if r["kind"] == "override"),
            "judgeMs": [r["ms"] for r in judged], "frames": frames,
            "trace": [{k: r.get(k) for k in ("reason", "how", "ms", "facts", "reply", "check", "outrun")} for r in judged]}


def ok(result: dict, until: str) -> bool:
    return result["event"] == "done" and result["centred"] and (until != "sharp" or result["sharp"])


async def bench(judge: str, ask_model, seeds: int, only: list[str] | None = None, trace: Path | None = None) -> list[dict]:
    out = []
    for case in CASES:
        if only and case.name not in only:
            continue
        for seed in range(1, seeds + 1):
            r = await run(case, judge=judge, ask_model=ask_model, seed=seed)
            r["ok"] = ok(r, case.until)
            out.append(r)
            if trace is not None:
                with trace.open("a") as f:
                    f.write(json.dumps({"case": case.name, "seed": seed, "said": r["said"], "trace": r["trace"]}) + "\n")
            print(json.dumps({k: v for k, v in r.items() if k not in ("said", "trace")}), flush=True)
    return out


def model_asker(option: str):
    """The fast model, as the app asks it (guide.py through app.py: agent.vision(option).ask), text only."""
    from orchestrator.keys import load_keys
    from orchestrator.options import VISION
    from orchestrator.vision import make_vision

    load_keys()
    vision = make_vision(VISION[option].spec, temperature=0.2)

    async def ask(prompt: str):
        result = await vision.ask([], prompt, max_output_tokens=300)
        return result.data if isinstance(result.data, dict) else None

    return ask


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--judge", choices=("rule", "model"), default="rule")
    parser.add_argument("--model", default="gemini-3.8-flash", help="the fast model option for --judge model")
    parser.add_argument("--seeds", type=int, default=2)
    parser.add_argument("--only", default="", help="comma-separated case names")
    parser.add_argument("--trace", type=Path, help="append every judgment (facts, answer, check) to this JSONL file")
    args = parser.parse_args()
    ask = model_asker(args.model) if args.judge == "model" else None
    t0 = time.time()
    results = asyncio.run(bench(args.judge, ask, args.seeds, [c for c in args.only.split(",") if c], args.trace))
    good = [r for r in results if r["ok"]]
    ms = [m for r in results for m in r["judgeMs"]]
    print(json.dumps({"judge": args.judge, "model": args.model if args.judge == "model" else None,
                      "runs": len(results), "ok": len(good),
                      "medianSeconds": statistics.median([r["seconds"] for r in good]) if good else None,
                      "medianLines": statistics.median([r["lines"] for r in good]) if good else None,
                      "judgeMsP50": statistics.median(ms) if ms else None,
                      "overrides": sum(r["overrides"] for r in results), "wall": round(time.time() - t0, 1)}))


if __name__ == "__main__":
    main()
