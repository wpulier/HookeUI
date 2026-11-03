"""The scope bridge: every action on the microscope, checked, done, verified and receipted (AGENTS.md beside this file).

`ScopeBridge.do(action, approval)` is the one way anything moves:
1. **Shape**: the action is read as a typed action (`actions.parse`); anything malformed is refused.
2. **Limits** (`limits.py`, the user's): the target inside the stage's box on every axis, one move no larger than the
   step the user allows, an exposure inside its range, every position and exposure of a plan inside them. Refused with
   what would have been crossed; nothing moves.
3. **A yes** for the light going on and for a large acquisition (more frames or time than the user's limits, or a
   plan's travel between positions larger than one step): the first ask gets an offer (`needs-yes`, its id and the
   words to ask with); the same action again with that id as its approval, within OFFER_S, goes ahead. An offer is
   for that action only and is used once.
4. **Done and verified**: one action at a time; the stage is read back after a move (within TOLERANCE of the target,
   and inside the box), the light and exposure read back after they are set. `stop` interrupts whatever is running,
   at once, from any thread.
5. **A receipt** for every action, whatever happened (done, refused, needs-yes, failed, stopped): what was asked,
   what the microscope was before and after, whether it was verified, what was made. Kept in `receipts.jsonl` beside
   the limits; the orchestrator files each in the session's journal.

Autofocus is the bridge's own sweep (snap at each plane, sharpness, then the sharpest), so it never leaves the limits
whatever the microscope's own autofocus would do.
"""
from __future__ import annotations

import itertools
import json
import math
import struct
import threading
import time
import zlib
from pathlib import Path

from . import actions as A
from .backends import Frame, Unavailable
from .limits import AXES, Limits, LimitsFile

OFFER_S = 180.0
TOLERANCE = {"x": 1.0, "y": 1.0, "z": 0.25}  # µm: a move read back this close to its target is where it was sent


class Receipts:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self.lock = threading.Lock()
        self.kept: list[dict] = []

    def add(self, receipt: dict) -> None:
        with self.lock:
            self.kept.append(receipt)
            del self.kept[:-500]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a") as f:
                f.write(json.dumps(receipt, ensure_ascii=False) + "\n")

    def since(self, n: int) -> list[dict]:
        with self.lock:
            return [r for r in self.kept if r["n"] > n]


class ScopeBridge:
    def __init__(self, backend, folder: Path, *, clock=time.time) -> None:
        self.backend = backend
        self.folder = Path(folder)
        self.clock = clock
        self.limits_file = LimitsFile(self.folder / "limits.json")
        loaded = self.limits_file.load()
        if loaded is None:  # first run: a small box around where the stage is, until the user widens it
            loaded = Limits.around(*backend.position())
            self.limits_file.save(loaded)
        self.limits = loaded
        self.receipts = Receipts(self.folder / "receipts.jsonl")
        self.lock = threading.Lock()  # one action at a time
        self.stopping = threading.Event()
        self.offers: dict[str, tuple[str, float, str]] = {}  # id -> (the action, as JSON; expires; why)
        self._n = itertools.count(1)
        self._offer_ids = itertools.count(1)
        self.busy: str | None = None

    # --- reading -------------------------------------------------------------------------------------------------------
    def state(self) -> dict:
        b = self.backend
        x, y, z = b.position()
        return {"x": round(x, 3), "y": round(y, 3), "z": round(z, 3), "exposureMs": b.exposure(), "channel": b.channel(),
                "light": b.light()}

    def wire(self) -> dict:
        return {"backend": self.backend.describe(), "state": self.state(), "channels": self.backend.channels(),
                "limits": self.limits.wire(), "busy": self.busy}

    def set_limits(self, data: dict, words: str) -> dict:
        """The user's new limits (their words kept with them). Never while something runs."""
        words = (words or "").strip()
        if not words:
            raise ValueError("limits change on the user's own words, quoted")
        new = Limits.from_wire({**self.limits.wire(), **data})
        new.notes = (self.limits.notes + [words[:300]])[-20:]
        with self.lock:
            self.limits = new
            self.limits_file.save(new)
        return new.wire()

    # --- doing ---------------------------------------------------------------------------------------------------------
    def do(self, data, approval: str | None = None) -> dict:
        started = time.monotonic()
        try:
            action = A.parse(data)
        except ValueError as exc:
            return self._receipt(data, None, "refused", why=str(exc), started=started)
        if isinstance(action, A.Stop):
            return self.stop()
        with self.lock:
            self.stopping.clear()
            self.busy = action.kind
            try:
                before = self.state()
                why = self.check(action, before)
                if why:
                    return self._receipt(data, action, "refused", why=why, before=before, started=started)
                ask = self.needs_yes(action, before)
                if ask and not self._approved(approval, action):
                    offer = f"y{next(self._offer_ids)}"
                    self.offers[offer] = (json.dumps(action.wire(), sort_keys=True), self.clock() + OFFER_S, ask)
                    return self._receipt(data, action, "needs-yes", why=ask, before=before, started=started,
                                         offer=offer, ask=f"{ask}. Go ahead?")
                try:
                    made, verified, note = self._run(action, before)
                except Unavailable as exc:
                    return self._receipt(data, action, "refused", why=str(exc), before=before, started=started)
                except Exception as exc:  # the microscope failed: said, with where it is now
                    after = self._safe_state()
                    return self._receipt(data, action, "failed", why=f"{type(exc).__name__}: {exc}"[:300],
                                         before=before, after=after, started=started)
                after = self.state()
                outside = self.outside(after)
                if outside:  # never expected: the bridge checked first; stop everything and say it
                    self.backend.stop()
                    return self._receipt(data, action, "failed", why=f"the stage is outside the limits: {outside}",
                                         before=before, after=after, started=started, breach=True)
                status = "stopped" if self.stopping.is_set() else "done"
                return self._receipt(data, action, status, before=before, after=after, made=made, started=started,
                                     verified=verified, why=note or None)
            finally:
                self.busy = None

    def stop(self) -> dict:
        """Stop whatever is moving or acquiring, now (not waiting for the action it interrupts)."""
        self.stopping.set()
        try:
            self.backend.stop()
            why = None
        except Exception as exc:
            why = f"{type(exc).__name__}: {exc}"[:200]
        return self._receipt({"kind": "stop"}, A.Stop(), "done" if why is None else "failed", why=why,
                             after=self._safe_state(), started=time.monotonic())

    # --- the checks ----------------------------------------------------------------------------------------------------
    def target(self, action: A.Move, here: dict) -> dict[str, float]:
        out = {}
        for axis in AXES:
            v = getattr(action, axis)
            if v is not None:
                out[axis] = here[axis] + v if action.relative else v
        return out

    def check(self, action: A.Action, here: dict) -> str:
        """Why the limits forbid it, or ""."""
        lim = self.limits
        if isinstance(action, A.Move):
            for axis, v in self.target(action, here).items():
                box = lim.box(axis)
                if box is None:
                    return f"no limits are set for {axis}, so it does not move"
                if not lim.inside(axis, v):
                    return f"{axis} {v:.1f} µm is outside your limits ({box[0]:.1f} to {box[1]:.1f} µm)"
            dx = self.target(action, here).get("x", here["x"]) - here["x"]
            dy = self.target(action, here).get("y", here["y"]) - here["y"]
            dz = self.target(action, here).get("z", here["z"]) - here["z"]
            if math.hypot(dx, dy) > lim.step_xy + 1e-9:
                return f"that moves {math.hypot(dx, dy):.0f} µm across the slide in one go; your limit is {lim.step_xy:g} µm"
            if abs(dz) > lim.step_z + 1e-9:
                return f"that moves the focus {abs(dz):.1f} µm in one go; your limit is {lim.step_z:g} µm"
            return ""
        if isinstance(action, A.Exposure):
            lo, hi = lim.exposure_ms
            return "" if lo <= action.ms <= hi else f"{action.ms:g} ms is outside your exposure limits ({lo:g} to {hi:g} ms)"
        if isinstance(action, A.Channel):
            names = self.backend.channels()
            return "" if action.name in names else f"there is no channel called {action.name!r} (there are {', '.join(names) or 'none'})"
        if isinstance(action, A.Autofocus):
            if lim.z is None:
                return "no focus limits are set, so the focus does not move"
            return ""
        if isinstance(action, A.Acquire):
            plan = action.plan
            names = self.backend.channels()
            for name, exposure in plan.channels:
                if name not in names:
                    return f"the plan's channel {name!r} is not one of this microscope's ({', '.join(names) or 'none'})"
                if exposure is not None and not lim.exposure_ms[0] <= exposure <= lim.exposure_ms[1]:
                    return f"the plan's exposure {exposure:g} ms is outside your exposure limits"
            for px, py, pz, z in self._plan_points(plan, here):
                for axis, v in (("x", px), ("y", py), ("z", z)):
                    if lim.box(axis) is None:
                        return f"no limits are set for {axis}, so nothing moves there"
                    if not lim.inside(axis, v):
                        return f"the plan goes to {axis} {v:.1f} µm, outside your limits"
            return ""
        return ""

    def _plan_points(self, plan: A.Plan, here: dict):
        positions = list(plan.positions) or [(None, None, None)]
        for px, py, pz in positions:
            x = here["x"] if px is None else px
            y = here["y"] if py is None else py
            base = here["z"] if pz is None else pz
            for off in plan.z_offsets:
                yield x, y, base, base + off

    def needs_yes(self, action: A.Action, here: dict) -> str:
        """Why the user's yes is needed first, or ""."""
        if isinstance(action, A.Light) and action.on:
            return "This turns the microscope's light on and keeps it on"
        if isinstance(action, A.Acquire):
            plan, lim = action.plan, self.limits
            exposure = here.get("exposureMs") or 10.0
            reasons = []
            if plan.frames > lim.frames:
                reasons.append(f"{plan.frames} pictures (your limit without asking is {lim.frames})")
            seconds = plan.seconds(exposure)
            if seconds > lim.seconds:
                reasons.append(f"about {seconds / 60:.1f} minutes (your limit without asking is {lim.seconds / 60:.1f})")
            travel = self._plan_travel(plan, here)
            if travel > lim.step_xy:
                reasons.append(f"moves up to {travel:.0f} µm between positions (more than one step of {lim.step_xy:g})")
            if reasons:
                return "This acquisition takes " + " and ".join(reasons)
        return ""

    def _plan_travel(self, plan: A.Plan, here: dict) -> float:
        points = [(x, y) for x, y, _, _ in self._plan_points(plan, here)]
        hops = [math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in zip([(here["x"], here["y"])] + points, points)]
        return max(hops or [0.0])

    def _approved(self, approval: str | None, action: A.Action) -> bool:
        if not approval:
            return False
        kept = self.offers.pop(approval, None)
        if kept is None:
            return False
        wanted, expires, _ = kept
        return expires >= self.clock() and wanted == json.dumps(action.wire(), sort_keys=True)

    def outside(self, state: dict) -> str:
        bad = [f"{a} {state[a]:.1f}" for a in AXES
               if self.limits.box(a) is not None and not self.limits.inside(a, state[a], tol=TOLERANCE[a])]
        return ", ".join(bad)

    # --- running -------------------------------------------------------------------------------------------------------
    def _run(self, action: A.Action, before: dict) -> tuple[list[str], bool, str]:
        b = self.backend
        if isinstance(action, A.Move):
            target = self.target(action, before)
            b.move_to(target.get("x"), target.get("y"), target.get("z"))
            after = self.state()
            off = [a for a, v in target.items() if abs(after[a] - v) > TOLERANCE[a]]
            return [], not off, (f"the stage stopped short on {', '.join(off)}" if off else "")
        if isinstance(action, A.Snap):
            return [self._save(b.snap(), "snap")], True, ""
        if isinstance(action, A.Exposure):
            b.set_exposure(action.ms)
            got = b.exposure()
            return [], got is not None and abs(got - action.ms) < 1e-3, ""
        if isinstance(action, A.Channel):
            b.set_channel(action.name)
            return [], b.channel() == action.name, ""
        if isinstance(action, A.Light):
            b.set_light(action.on)
            return [], b.light() == action.on, ""
        if isinstance(action, A.Autofocus):
            return self._autofocus(action, before)
        if isinstance(action, A.Acquire):
            return self._acquire(action, before)
        raise ValueError(f"nothing does {action.kind}")

    def _step_to(self, x: float | None, y: float | None, z: float | None) -> None:
        """A move the bridge makes for itself, in steps no larger than the user allows, each inside the box."""
        lim = self.limits
        cx, cy, cz = self.backend.position()
        tx, ty, tz = cx if x is None else x, cy if y is None else y, cz if z is None else z
        for axis, v in (("x", tx), ("y", ty), ("z", tz)):
            if lim.box(axis) is not None and not lim.inside(axis, v):
                raise ValueError(f"{axis} {v:.1f} µm is outside the limits")
        n = max(1, math.ceil(math.hypot(tx - cx, ty - cy) / lim.step_xy - 1e-9),
                math.ceil(abs(tz - cz) / lim.step_z - 1e-9))
        for i in range(1, n + 1):
            if self.stopping.is_set():
                return
            f = i / n
            self.backend.move_to(cx + (tx - cx) * f if x is not None else None,
                                 cy + (ty - cy) * f if y is not None else None,
                                 cz + (tz - cz) * f if z is not None else None)

    def _autofocus(self, action: A.Autofocus, before: dict) -> tuple[list[str], bool, str]:
        lo, hi = self.limits.z
        z0 = before["z"]
        planes = [z0 - action.range / 2 + i * action.step for i in range(int(round(action.range / action.step)) + 1)]
        planes = [p for p in planes if lo <= p <= hi]
        if not planes:
            raise Unavailable("there is no room inside your focus limits to search")
        scores = []
        for z in planes:
            if self.stopping.is_set():
                break
            self._step_to(None, None, z)
            scores.append((sharpness(self.backend.snap()), z))
        if not scores:
            return [], False, "stopped before it looked"
        best_score, best = max(scores)
        self._step_to(None, None, best)
        at = self.backend.position()[2]
        return [], abs(at - best) <= TOLERANCE["z"], f"sharpest at z {best:.2f} µm of {len(scores)} planes"

    def _acquire(self, action: A.Acquire, before: dict) -> tuple[list[str], bool, str]:
        run = f"acq-{time.strftime('%Y%m%d-%H%M%S')}-{next(self._n)}"
        made: list[str] = []
        exposure = before.get("exposureMs") or 10.0

        def on_frame(frame: Frame, index: dict) -> None:
            made.append(self._save(frame, f"{run}/" + "-".join(f"{k}{v}" for k, v in sorted(index.items())
                                                                 if isinstance(v, int))))

        engine = getattr(self.backend, "acquire", None)
        here = (before["x"], before["y"], before["z"])
        if callable(engine):
            n = engine(action.plan, here=here, exposure_ms=exposure, on_frame=on_frame, stopping=self.stopping)
        else:
            from .backends import acquire_by_steps

            n = acquire_by_steps(self.backend, action.plan, here=here, exposure_ms=exposure, on_frame=on_frame,
                                 stopping=self.stopping, move=self._step_to)
        complete = n == action.plan.frames
        return made, complete, "" if complete else f"made {n} of {action.plan.frames} pictures"

    # --- files and receipts --------------------------------------------------------------------------------------------
    def _save(self, frame: Frame, name: str) -> str:
        folder = self.folder / "pictures"
        path = folder / (name + (".jpg" if frame.jpeg is not None else ".png"))
        path.parent.mkdir(parents=True, exist_ok=True)
        if frame.jpeg is not None:
            path.write_bytes(frame.jpeg)
        else:
            path.write_bytes(png(frame.pixels))
        return str(path.relative_to(self.folder))

    def _safe_state(self) -> dict | None:
        try:
            return self.state()
        except Exception:
            return None

    def _receipt(self, asked, action, status: str, *, why: str | None = None, before=None, after=None,
                 made=None, started: float, verified: bool | None = None, offer: str | None = None,
                 ask: str | None = None, breach: bool = False) -> dict:
        receipt = {"n": next(self._n), "id": f"r{int(self.clock() * 1000)}", "at": self.clock(),
                   "kind": getattr(action, "kind", None) or (asked.get("kind") if isinstance(asked, dict) else None),
                   "asked": asked if _jsonable(asked) else repr(asked)[:300],
                   "action": action.wire() if action is not None else None, "status": status,
                   "ms": round(1000 * (time.monotonic() - started), 1)}
        for key, value in (("why", why), ("before", before), ("after", after), ("made", made),
                           ("verified", verified), ("offer", offer), ("ask", ask)):
            if value is not None:
                receipt[key] = value
        if breach:
            receipt["breach"] = True
        self.receipts.add(receipt)
        return receipt


def _jsonable(v) -> bool:
    try:
        json.dumps(v)
        return True
    except (TypeError, ValueError):
        return False


def sharpness(frame: Frame) -> float:
    """How sharp a picture is: the variance of its Laplacian over its contrast squared (light is not focus)."""
    import numpy as np

    if frame.pixels is not None:
        img = np.asarray(frame.pixels, dtype=np.float32)
    else:
        img = _decode_jpeg(frame.jpeg)
    if img.ndim == 3:
        img = img.mean(axis=2)
    img = img[::2, ::2]
    lap = img[1:-1, 1:-1] * 4 - img[:-2, 1:-1] - img[2:, 1:-1] - img[1:-1, :-2] - img[1:-1, 2:]
    return float(lap.var() / max(float(img.std()), 1.0) ** 2)


def _decode_jpeg(data: bytes):
    import numpy as np

    try:
        import cv2

        return cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_GRAYSCALE).astype(np.float32)
    except ImportError:  # the bridge's own environment has no OpenCV; Pillow is the other way
        import io

        from PIL import Image

        return np.asarray(Image.open(io.BytesIO(data)).convert("L"), dtype=np.float32)


def png(pixels) -> bytes:
    """A PNG of a 2D (8- or 16-bit grey) or 3D (8-bit RGB) array, standard library and numpy only."""
    import numpy as np

    a = np.asarray(pixels)
    if a.ndim == 3:
        a = a[..., :3].astype(np.uint8)
        colour, depth, row = 2, 8, lambda r: r.tobytes()
    elif a.dtype.itemsize > 1:
        a = a.astype(">u2")
        colour, depth, row = 0, 16, lambda r: r.tobytes()
    else:
        a = a.astype(np.uint8)
        colour, depth, row = 0, 8, lambda r: r.tobytes()
    h, w = a.shape[:2]
    raw = b"".join(b"\x00" + row(a[i]) for i in range(h))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, colour, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
