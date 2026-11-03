"""Guiding the user's hands to a thing on the slide ("find me a stoma and centre it"), until it is centred and sharp.

Hooke has no motors yet, so it says short lines ("move the slide right a little", "focus up", "stop, it's centred")
and watches what the hands did (docs/architecture/seeing-and-acting.md, "Guiding the user's hands"). Code measures;
the model judges (docs/design/2026-10-06-facts-not-verdicts.md):

- **Facts**, from what the hub already measures on every frame (nothing is decoded on the frame path): where the target
  is from the eyepiece's centre (`slidemap.place`, in eyepiece radii, µm with a scale), whether the slide is moving and
  how far the target came since the last line, its sharpness against the best this place has looked during the
  guidance (over the contrast squared, as the watcher's: light is not focus), whether the focus knob or the phone's
  lens is moving (the coach's sweep), and every line said with what followed it.
- **The slide and the picture** (`Relation`): which way the slide must go for the picture to move the way needed depends
  on the microscope (a compound one turns the picture round, so it moves opposite to the slide) and on how the phone
  sits on the eyepiece. It is assumed opposite until the user's own moves after the lines measure it.
- **The judge** (option GUIDE): a model, Gemini 3.5 Flash by default (the guide bench's fastest right one), reads the
  facts and answers the line, what it asks of the hands and whether it is done; "rule": code decides (a non-default
  option, benched against the models). When the
  model can't be reached or answers out of shape, the rule's line is said instead and logged as an override.
- **Checks**: "done" is held to the facts (centred within CENTRED_R and, when sharp was asked, at least SHARP_SHARE of
  the best a focus probe found here); refused, the model is told why at its next turn. A line the view has outrun
  while it was judged (the target moved on, or reached the centre) is not said; the view is judged again.

One guidance at a time (a new one ends the last). Every judgment is logged ("guide-judge") with its facts.
"""
from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import math
import time
from dataclasses import dataclass, field
from typing import Awaitable, Callable

CENTRED_R = 0.15  # the check: within this share of the eyepiece radius of its centre counts as centred
CENTRED_SAID = 0.1  # what the model is told centred means (the check leaves it room)
SHARP_SHARE = 0.85  # sharp: at least this share of the best a focus probe found at this place
SETTLE_S = 0.35  # still this long after a move: the hands stopped, judge the view
MOVING_PX = 1.2  # the slide moving: the picture moved more than this many px a frame...
MOVING_FRAMES = 3  # ...over this many frames
ARM_R = 0.25  # the target outside this (radii) since it last came into the centre: coming in again is a moment
QUIET_S = 7.0  # a line followed by nothing for this long: judge again (it may not have been heard or done)
LOST_S = 1.5  # the target's place unknown this long: tell the judge
GIVE_UP_LOST_S = 25.0  # ...and this long: the guidance ends, said
LONGEST_S = 300.0
MAX_LINES = 40
JUDGE_S = 4.0  # the model's answer is waited for this long at most; then the rule's line is said (an override)
STALE_R = 0.12  # the target moved this far (radii) while a line was judged: the line is outrun
STALE_DROPS = 2  # at most this many outrun lines in a row are dropped; then the newest is said
FOCUS_CHANGE = 0.1  # sharpness changed by this share and held: the focus moved, judge it
FOCUS_WAIT_S = 2.0  # after a focus line, judge once the sharpness has held this long, whatever it did
FOCUS_WINDOW_S = 0.6  # sharpness is the median of this long of frames, and held means held this long
FOCUS_HELD = 0.12  # held: the last SETTLE_S of frames within this share of each other
HERE_R = 0.35  # within this of the centre the target is "here": its sharpness counts for this place
AWAY_R = 0.6  # farther than this, the place's sharpness is forgotten (another part of the slide)
AMOUNTS = {"nudge": 0.08, "little": 0.2, "some": 0.45, "far": 0.9}  # radii of picture movement each word asks for
SLIDE_X, SLIDE_Y = ("right", "left"), ("away", "toward")
FOCUS = ("up", "down")
STATES = ("guiding", "done", "lost")
SETTLED = ("settled", "quiet", "focus-changed", "focus-held", "refused")  # moments at which the slide is still


def sharpness(info) -> float | None:
    """The frame's sharpness over its contrast squared (view_watch.sharpness: light scales both and leaves it)."""
    stats = getattr(info, "stats", ()) or ()
    if len(stats) < 3 or stats[0] <= 0:
        return None
    return float(stats[0]) / max(float(stats[2]), 1.0) ** 2


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


# --- the slide and the picture ---------------------------------------------------------------------------------------
HAND = {"right": (1.0, 0.0), "left": (-1.0, 0.0), "away": (0.0, -1.0), "toward": (0.0, 1.0)}  # hand frame, +y toward


def hand_vector(slide: dict | None) -> tuple[float, float] | None:
    """A line's slide direction as a unit vector in the hands' frame (x right, y toward the user), or None."""
    if not isinstance(slide, dict):
        return None
    x = HAND.get(str(slide.get("x") or ""), (0.0, 0.0))
    y = HAND.get(str(slide.get("y") or ""), (0.0, 0.0))
    vx, vy = x[0] + y[0], x[1] + y[1]
    n = math.hypot(vx, vy)
    return (vx / n, vy / n) if n > 0 else None


@dataclass
class Relation:
    """How the picture moves when the hands move the slide: a rotation (and possibly a mirror) from the hands' frame to
    the picture's, measured from each line's asked direction and the picture's movement that followed. Until two moves
    agree it is assumed: the picture moves opposite to the slide (a compound microscope, the phone upright)."""
    pairs: list[tuple[tuple[float, float], tuple[float, float]]] = field(default_factory=list)  # (hand unit, picture px)

    MIN_PX = 6.0  # a smaller picture movement says nothing about the relation

    def add(self, hand: tuple[float, float], picture: tuple[float, float]) -> None:
        if math.hypot(*picture) >= self.MIN_PX:
            self.pairs.append((hand, picture))
            del self.pairs[:-8]

    def _fit(self) -> tuple[float, float, float, float]:
        """The rotation (or, once two directions were moved, the mirror) that best turns the hands' directions into the
        picture's (2D Kabsch on unit vectors)."""
        rot_c = rot_s = mir_c = mir_s = 0.0
        for (hx, hy), (px, py) in self.pairs:
            n = math.hypot(px, py)
            ux, uy = px / n, py / n
            rot_c += hx * ux + hy * uy
            rot_s += hx * uy - hy * ux
            mx, my = hx, -hy  # mirrored: picture = R(t) @ diag(1, -1) @ hand
            mir_c += mx * ux + my * uy
            mir_s += mx * uy - my * ux
        if self._two_directions() and math.hypot(mir_c, mir_s) > math.hypot(rot_c, rot_s) + 0.5:
            t = math.atan2(mir_s, mir_c)
            c, s = math.cos(t), math.sin(t)
            return c, s, s, -c
        t = math.atan2(rot_s, rot_c)
        c, s = math.cos(t), math.sin(t)
        return c, -s, s, c

    def matrix(self) -> tuple[tuple[float, float, float, float], bool]:
        """(a, b, c, d): picture direction = [[a, b], [c, d]] @ hand direction (both unit), and whether it is measured
        (else the assumption: opposite)."""
        if not self.pairs:
            return (-1.0, 0.0, 0.0, -1.0), False
        fit = self._fit()
        return (fit, True) if self._agrees(fit) else ((-1.0, 0.0, 0.0, -1.0), False)

    def _two_directions(self) -> bool:
        hands = [h for h, _ in self.pairs]
        return any(abs(a[0] * b[1] - a[1] * b[0]) > 0.5 for a in hands for b in hands)

    def _agrees(self, fit) -> bool:
        """Measured: at least two of the last four moves within 45 degrees of what the fit says."""
        if len(self.pairs) < 2:
            return False
        a, b, c, d = fit
        good = 0
        for (hx, hy), (px, py) in self.pairs[-4:]:
            ex, ey = a * hx + b * hy, c * hx + d * hy
            if (ex * px + ey * py) / math.hypot(px, py) > math.cos(math.radians(45)):
                good += 1
        return good >= 2

    def hand_for(self, picture: tuple[float, float]) -> tuple[float, float]:
        """The hand direction (unit) that moves the picture along `picture`."""
        (a, b, c, d), _ = self.matrix()
        det = a * d - b * c
        if abs(det) < 1e-9:
            return (-picture[0], -picture[1])
        hx = (d * picture[0] - b * picture[1]) / det
        hy = (-c * picture[0] + a * picture[1]) / det
        n = math.hypot(hx, hy)
        return (hx / n, hy / n) if n > 0 else (0.0, 0.0)

    def words(self) -> str:
        (a, b, c, d), measured = self.matrix()
        n = len(self.pairs)
        if not measured:
            seen = f" ({n} move{'s' if n != 1 else ''} seen so far)" if n else ""
            return ("assumed, not yet measured: on a compound microscope the picture moves opposite to the slide"
                    + seen)
        if a < -0.9 and d < -0.9:
            how = "the picture moves opposite to the slide"
        elif a > 0.9 and d > 0.9:
            how = "the picture moves the same way as the slide"
        else:
            how = "the picture moves turned from the slide's direction (the phone sits turned on the eyepiece)"
        return f"measured from your last {min(n, 4)} moves: {how}"


def slide_words(hand: tuple[float, float], share_r: float) -> dict:
    """A hand direction and the picture movement it should make (radii) as the facts give it: each axis that matters
    named, and the size."""
    hx, hy = hand
    out: dict = {}
    if abs(hx) >= 0.38:
        out["x"] = "right" if hx > 0 else "left"
    if abs(hy) >= 0.38:
        out["y"] = "toward" if hy > 0 else "away"
    out["radii"] = round(share_r, 2)
    return out


# --- one guidance --------------------------------------------------------------------------------------------------
@dataclass
class Reading:
    """What one frame said about the target (cheap: read off the hub's numbers)."""
    t: float
    frame: int
    known: bool  # its place is known from this view (same epoch, tracked)
    dx: float = 0.0  # its centre from the eyepiece's centre, camera px (+x right, +y down)
    dy: float = 0.0
    radius: float = 0.0
    in_view: bool = False
    offset: tuple[float, float] = (0.0, 0.0)  # the slide's offset (picture px) in its epoch
    epoch: int = 0
    sharp: float | None = None
    why: str = ""  # why its place is not known, when it is not

    @property
    def r(self) -> float:
        """Its distance from the centre, in eyepiece radii."""
        return math.hypot(self.dx, self.dy) / self.radius if self.radius > 0 else 0.0


@dataclass
class Said:
    """A line said, and what followed it."""
    t: float
    text: str
    slide: dict | None
    focus: str | None
    state: str
    offset: tuple[float, float]
    epoch: int
    r: float | None
    sharp: float | None
    then: str = ""  # what followed, in words, measured again at each judgment until the next line
    climb: str = ""  # the rule's focus step (probe, keep, reverse, back, same); "" for the model's
    still: bool = True  # the slide was still when it was said (what followed is this line's alone)
    paired: bool = False  # its move was counted in the relation


@dataclass
class Guidance:
    id: str
    target: str  # the outline's id
    name: str  # what it is called ("stoma")
    asked: str  # the user's words
    until: str  # "centred" or "sharp"
    origin: dict = field(default_factory=dict)
    started: float = 0.0
    deadline: float = 0.0
    lines: list[Said] = field(default_factory=list)
    relation: Relation = field(default_factory=Relation)
    readings: list[Reading] = field(default_factory=list)  # the last couple of seconds
    task: asyncio.Task | None = None
    event: str = ""  # done | stopped | gave-up | failed | replaced
    outcome: str = ""
    # moments the frame path noticed (the loop waits on them)
    moment: asyncio.Event | None = None
    reason: str = ""
    last_motion_t: float = 0.0
    moved_since_judged: bool = False
    judged_offset: tuple[float, float] | None = None
    judged_epoch: int | None = None
    judged_sharp: float | None = None
    judged_r: float | None = None
    unknown_since: float | None = None
    lost_told: bool = False  # this loss was judged once already
    # this place's focus
    best_here: float | None = None
    probes_here: int = 0  # focus lines said while the target was here
    armed: bool = False  # the target was clearly outside the centre since it last came in
    refused: str = ""  # why the last "done" was refused (told at the next turn)
    first: asyncio.Event | None = None  # set once the first line is ready
    hold_first: bool = False  # the first line is kept for the tool's answer, not said
    held: dict | None = None
    overrides: int = 0
    judgments: int = 0

    def latest(self) -> Reading | None:
        return self.readings[-1] if self.readings else None

    def summary(self) -> dict:
        return {"id": self.id, "target": self.target, "name": self.name, "until": self.until, "event": self.event,
                "lines": len(self.lines), "seconds": round(max(0.0, (self.lines[-1].t if self.lines else self.started)
                                                               - self.started), 1),
                "judgments": self.judgments, "overrides": self.overrides}


class Guide:
    """Guidance runs, fed by every frame (`frame`), judged and said off the frame path."""

    def __init__(self, hub, *, judge: Callable[[], str] = lambda: "gemini-3.5-flash",
                 ask_model: Callable[[str], Awaitable[dict | None]] | None = None,
                 say: Callable[[Guidance, dict], None] = lambda g, line: None,
                 sweep: Callable[[], str | None] = lambda: None, um_per_px: Callable[[], float | None] = lambda: None,
                 log: Callable[[dict], None] = lambda r: None, clock: Callable[[], float] | None = None,
                 wall: Callable[[], float] | None = None) -> None:
        self.hub = hub
        # A frame's own time (FrameInfo.wall_at) is on `wall`; the loop's on `clock` (both read when made, never a
        # default kept from import time).
        self.wall = wall if wall is not None else time.time
        self.judge_option = judge  # the GUIDE option now: a model's id, or "rule"
        self.ask_model = ask_model  # prompt -> the model's JSON (None: it could not answer)
        self.say = say  # (guidance, line) -> said to the user (the voice, the narrator or the chat)
        self.sweep = sweep  # the coach's: "knob", "phone" or None
        self.um_per_px = um_per_px
        self.log = log
        self.clock = clock if clock is not None else time.monotonic
        self.active: Guidance | None = None
        self._ids = itertools.count(1)

    # --- starting and stopping ---------------------------------------------------------------------------------------
    def start(self, target: str, *, name: str, asked: str, until: str = "sharp", origin: dict | None = None,
              seconds: float = LONGEST_S, hold_first: bool = False) -> Guidance:
        """Start guiding to an outline. `hold_first`: the first line is kept for the caller's answer (`first_line`)
        instead of said, so it is heard once."""
        if until not in ("centred", "sharp"):
            raise ValueError("until is centred or sharp")
        if self.active is not None:
            old = self.active
            self._finish(old, "replaced", "Stopped guiding: a new guidance replaced it.")
            if old.task is not None and not old.task.done():
                old.task.cancel()  # it ended: it says nothing more
        now = self.clock()
        g = Guidance(f"g{next(self._ids)}", target, name or "it", asked, until, origin=dict(origin or {}),
                     started=now, deadline=now + max(20.0, min(LONGEST_S, seconds)))
        g.moment = asyncio.Event()
        g.first = asyncio.Event()
        g.hold_first = hold_first
        g.reason = "start"
        g.moment.set()
        self.active = g
        info = getattr(self.hub, "latest_info", None)
        if info is not None:
            self.frame(info)
        g.task = asyncio.ensure_future(self._run(g))
        return g

    async def first_line(self, g: Guidance, timeout: float) -> dict | None:
        """The first line, held for the caller (None when it did not come in time: then it is said when it comes)."""
        assert g.first is not None
        try:
            await asyncio.wait_for(g.first.wait(), timeout)
        except asyncio.TimeoutError:
            pass
        g.hold_first = False
        return g.held

    def _deliver(self, g: Guidance, line: dict) -> None:
        if g.hold_first and g.held is None and g.first is not None and not g.first.is_set():
            g.held = line  # the caller says it, in its answer: filed as the guidance's first line, not said again
            g.first.set()
            self.say(g, {**line, "told": True})
            return
        if g.first is not None:
            g.first.set()
        self.say(g, line)

    def stop(self, why: str = "you asked") -> Guidance | None:
        g = self.active
        if g is None:
            return None
        if g.task is not None and not g.task.done():
            g.task.cancel()
        self._finish(g, "stopped", f"Stopped guiding to the {g.name}: {why}.")
        return g

    def describe(self) -> str:
        g = self.active
        if g is None:
            return "not guiding"
        now = self.clock()
        last = g.lines[-1].text if g.lines else "nothing yet"
        reading = g.latest()
        where = (f"{reading.r:.2f} of the eyepiece radius from the centre" if reading is not None and reading.known
                 else "its place is not known from this view")
        return (f"{g.id}: guiding to the {g.name} ({g.target}) for {now - g.started:.0f} s, {len(g.lines)} lines; "
                f"last: \"{last}\"; it is {where}")

    # --- the frame path: numbers only ---------------------------------------------------------------------------------
    def frame(self, info) -> None:
        g = self.active
        if g is None or info is None:
            return
        reading = self._read(g, info)
        prev = g.latest()
        g.readings.append(reading)
        t = reading.t
        while g.readings and t - g.readings[0].t > 3.0:
            g.readings.pop(0)
        # Moving: the picture went MOVING_PX a frame, on average over the last few (one frame's jitter is not a move).
        back = g.readings[-MOVING_FRAMES - 1] if len(g.readings) > MOVING_FRAMES else None
        moving = (back is not None and back.epoch == reading.epoch
                  and math.hypot(reading.offset[0] - back.offset[0], reading.offset[1] - back.offset[1])
                  > MOVING_PX * MOVING_FRAMES)
        if moving or (prev is not None and prev.epoch != reading.epoch):
            g.last_motion_t = t
        if g.judged_offset is not None and (g.judged_epoch != reading.epoch or math.hypot(
                reading.offset[0] - g.judged_offset[0], reading.offset[1] - g.judged_offset[1]) > 0.04 * max(reading.radius, 1.0)):
            g.moved_since_judged = True
        # This place's sharpness: the best (a median of a few frames: one frame's noise is not a best) seen while the
        # target was near the centre; forgotten far away.
        if reading.known and reading.sharp is not None and not moving:
            if reading.r <= HERE_R:
                recent = [x.sharp for x in g.readings[-5:] if x.sharp is not None and x.known and x.r <= HERE_R]
                steady = _median(recent) if len(recent) >= 3 else None
                if steady is not None:
                    g.best_here = steady if g.best_here is None else max(g.best_here, steady)
            elif reading.r > AWAY_R:
                g.best_here, g.probes_here = None, 0
        if reading.known and reading.r > ARM_R:
            g.armed = True  # outside: coming into the centre again is a moment (not hovering at its edge)
        if not reading.known:
            g.unknown_since = g.unknown_since if g.unknown_since is not None else t
        else:
            g.unknown_since, g.lost_told = None, False
        if g.moment is None or g.moment.is_set():
            return
        still = t - g.last_motion_t >= SETTLE_S and bool(getattr(info, "steady", True))
        if g.moved_since_judged and still:
            self._raise(g, "settled")
        elif moving and reading.known and g.armed and reading.r <= CENTRED_R:
            g.armed = False
            self._raise(g, "centre-reached")  # came into the centre while moving: a stop is due now
        elif moving and reading.known and prev is not None and prev.known and prev.in_view and not reading.in_view:
            self._raise(g, "left-view")
        elif (g.unknown_since is not None and t - g.unknown_since >= LOST_S and g.judged_r is not None
              and not g.lost_told):
            g.lost_told = True  # once per loss: said again only after it was found and lost again
            self._raise(g, "lost")
        elif still and reading.known and reading.sharp is not None and g.judged_sharp:
            recent = [x.sharp for x in g.readings if x.sharp is not None and t - x.t <= FOCUS_WINDOW_S]
            now_sharp = _median(recent)
            # Changed, and held: every frame of the last FOCUS_WINDOW_S agrees (a turn half seen is not judged).
            held = len(recent) >= 3 and now_sharp and (max(recent) - min(recent)) / now_sharp <= FOCUS_HELD
            since_line = t - (g.lines[-1].t if g.lines else g.started)
            if held and abs(now_sharp / g.judged_sharp - 1) >= FOCUS_CHANGE and since_line >= 0.6:
                self._raise(g, "focus-changed")
                return
            if held and g.lines and g.lines[-1].focus and since_line >= FOCUS_WAIT_S:
                self._raise(g, "focus-held")  # a small turn near the sharpest changes little: judge it anyway
                return
        if g.lines and t - max(g.lines[-1].t, g.last_motion_t) >= QUIET_S and not (g.unknown_since is not None
                                                                                  and g.lost_told):
            # Nothing happened after the line: it may not have been heard or done. (A loss already said waits for the
            # target to be found again, or for the give-up.)
            self._raise(g, "quiet")

    def _read(self, g: Guidance, info) -> Reading:
        from .slidemap import place

        wall_at = float(getattr(info, "wall_at", 0.0) or 0.0)
        # The frame's own time on the loop's clock: frames analysed in a batch keep their spacing.
        t = self.clock() - max(0.0, self.wall() - wall_at) if wall_at else self.clock()
        fov = getattr(info, "fov", None)
        radius = float(fov.radius) if fov is not None and getattr(fov, "radius", 0) > 0 else 0.0
        reading = Reading(t, int(getattr(info, "id", 0) or 0), False, radius=radius,
                          offset=(float(info.dx), float(info.dy)), epoch=int(getattr(info, "epoch", 0) or 0),
                          sharp=sharpness(info))
        scene = getattr(self.hub, "scene", None)
        obj = scene.objects.get(g.target) if scene is not None else None
        if obj is None:
            reading.why = "its outline is gone"
            return reading
        try:
            p = place(obj, info)
        except Exception as exc:
            p, reading.why = None, f"its place could not be read ({type(exc).__name__})"
        if p is None:
            reading.why = reading.why or (
                "the eyepiece circle is not found" if fov is None else
                "this frame's slide movement is not registered" if getattr(info, "tracked", None) is False else
                "the view lost track of the slide's movement since it was outlined"
                if getattr(obj, "anchor_epoch", None) != reading.epoch else "its place could not be read")
            return reading
        reading.known, reading.dx, reading.dy, reading.in_view = True, float(p.dx), float(p.dy), bool(p.in_view)
        return reading

    @staticmethod
    def _raise(g: Guidance, reason: str) -> None:
        g.reason = reason
        if g.moment is not None:
            g.moment.set()

    # --- the loop: judge, check, say ----------------------------------------------------------------------------------
    async def _run(self, g: Guidance) -> None:
        try:
            await self._guide(g)
        except asyncio.CancelledError:
            if not g.event:
                self._finish(g, "stopped", f"Stopped guiding to the {g.name}.")
            raise
        except Exception as exc:  # a broken guidance ends and says so
            self._finish(g, "failed", f"Stopped guiding to the {g.name}: {type(exc).__name__}: {exc}"[:200])

    async def _guide(self, g: Guidance) -> None:
        drops = refusals = 0
        while True:
            now = self.clock()
            if now >= g.deadline or len(g.lines) >= MAX_LINES:
                self._finish(g, "gave-up", f"Stopped guiding to the {g.name} after {now - g.started:.0f} s without "
                                           f"it getting there ({len(g.lines)} lines).")
                return
            if g.unknown_since is not None and now - g.unknown_since >= GIVE_UP_LOST_S:
                self._finish(g, "gave-up", f"Stopped guiding: I lost where the {g.name} is for "
                                           f"{now - g.unknown_since:.0f} s. Ask me to find one again.")
                return
            assert g.moment is not None
            try:  # the frame path raises every moment, quiet included; this wait only covers frames that stopped coming
                await asyncio.wait_for(g.moment.wait(), timeout=max(0.05, min(2 * QUIET_S, g.deadline - now)))
            except asyncio.TimeoutError:
                if g.unknown_since is not None and g.lost_told:
                    continue  # lost and said so: wait to find it again, or give up
                g.reason = "quiet"
            if g.event:
                return  # ended meanwhile (stopped, replaced): nothing more is said
            reason, g.reason = g.reason or "quiet", ""
            g.moment.clear()
            reading = g.latest()
            if reading is None:
                await asyncio.sleep(0.2)
                continue
            self._note_followed(g, reading, reason)
            facts = self.facts(g, reason)
            started = self.clock()
            if reason == "centre-reached":
                # A hand crosses the centre in a fraction of a second, faster than any model answers (a guide bench
                # judgment takes 0.6-4 s): "stop" is the app's reflex, as the focus meter's "past the sharpest" is. The
                # judge decides what comes next once the slide is still. Said over the voice's own words, never dropped.
                reply = {"say": "Stop.", "slide": None, "focus": None, "state": "guiding", "critical": True}
                how = "reflex"
            elif reason == "start":
                # The first line is the app's own reading of where the target is (the measured way to move the slide),
                # said at once: the hands start moving while the judge is asked for the lines after (Will's bar, 3 Nov:
                # the first direction within seconds of the request). The judge decides every line from the first
                # settle on, and every "done".
                reply, how = rule(g, facts), "first"
                if reply.get("state") == "done":
                    reply = {**reply, "state": "guiding"}  # only the judge (and the checks) end a guidance
            else:
                reply, how = await self._judge(g, facts)
                g.judgments += 1
            if g.event:
                return
            ms = round(1000 * (self.clock() - started))
            check = self._check(g, reply, facts)
            newest = g.latest()
            outrun = (reply.get("state") != "done" and newest is not None and newest.known and reading.known
                      and (math.hypot(newest.dx - reading.dx, newest.dy - reading.dy) / max(newest.radius, 1.0) > STALE_R
                           or (newest.r <= CENTRED_R < reading.r)))
            self.log({"kind": "guide-judge", "id": g.id, "reason": reason, "how": how, "ms": ms, "facts": facts,
                      "reply": reply, "check": check, "outrun": outrun})
            if outrun and drops < STALE_DROPS:  # not said: the next moment (the slide still, the target in) is judged
                drops += 1
                continue
            drops = 0
            if check:  # a "done" the facts don't bear out: not said; judged again with why (the rule's after twice)
                g.refused = check
                g.overrides += 1
                self.log({"kind": "override", "where": "guide", "id": g.id, "why": check, "model": reply})
                refusals += 1
                if refusals < 2:
                    self._raise(g, "refused")
                    continue
                reply = rule(g, self.facts(g, "refused"))
                if reply.get("state") == "done" and self._check(g, reply, facts):  # never said as done; nothing to ask
                    reply = {"say": "Hold it there a moment.", "slide": None, "focus": None, "state": "guiding"}
            else:
                g.refused = ""
            refusals = 0
            if reply.get("state") == "done":
                self._said(g, reply, reading)
                self._finish(g, "done", reply.get("say") or f"The {g.name} is centred.", line=reply)
                return
            self._said(g, reply, reading)
            self._deliver(g, self._line(g, reply, "line"))
            if not reading.known:
                g.lost_told = True  # a line about a lost target was said, whichever moment asked for it
            # What happened while it was decided is in the facts the next line is judged on; a moment raised meanwhile
            # would judge again before the hands could follow this line.
            g.moment.clear()
            newest = g.latest() or reading
            g.judged_offset, g.judged_epoch = newest.offset, newest.epoch
            g.judged_r = newest.r if newest.known else g.judged_r
            g.judged_sharp = self._sharp_now(g)
            g.moved_since_judged = False

    def _sharp_now(self, g: Guidance) -> float | None:
        """The sharpness now: a median over FOCUS_WINDOW_S of frames (a few frames are noise, more so on a slow feed)."""
        reading = g.latest()
        if reading is None:
            return None
        return _median([x.sharp for x in g.readings if x.sharp is not None and reading.t - x.t <= FOCUS_WINDOW_S])

    def _note_followed(self, g: Guidance, reading: Reading, reason: str) -> None:
        """What the hands did after the last line, measured again at each judgment until the next line: the picture's
        movement, the target's distance, the sharpness. Its move counts in the relation once the slide is still again,
        and only when the slide was still as it was said (else part of the movement was the line before's)."""
        if not g.lines:
            return
        last = g.lines[-1]
        bits = []
        if last.epoch == reading.epoch:
            px = (reading.offset[0] - last.offset[0], reading.offset[1] - last.offset[1])
            hand = hand_vector(last.slide)
            if hand is not None and last.still and not last.paired and reason in SETTLED:
                g.relation.add(hand, px)
                last.paired = True
            moved = math.hypot(*px) / max(reading.radius, 1.0)
            if moved >= 0.03:
                bits.append(f"the picture moved {moved:.2f} radii {_toward(px[0], px[1])}")
            else:
                bits.append("the slide did not move")
        else:
            bits.append("the view lost track of the slide's movement")
        if last.r is not None and reading.known:
            change = last.r - reading.r
            if abs(change) >= 0.02:
                bits.append(f"the {g.name} came {abs(change):.2f} radii {'closer to' if change > 0 else 'farther from'} "
                            "the centre")
        sharp = self._sharp_now(g)
        if last.sharp and sharp:
            ratio = sharp / last.sharp
            if abs(ratio - 1) >= 0.05:
                bits.append(f"sharpness {'rose' if ratio > 1 else 'fell'} {abs(ratio - 1) * 100:.0f}%")
        last.then = "; ".join(bits)

    def turns_here(self, g: Guidance) -> list[tuple[str, str]]:
        """The focus lines said with the target here, each with what it did: (up|down, rose|fell|same)."""
        out = []
        for s_ in g.lines:
            if s_.focus and s_.r is not None and s_.r <= HERE_R:
                out.append((s_.focus, "rose" if "sharpness rose" in s_.then else
                            "fell" if "sharpness fell" in s_.then else "same"))
        return out

    def peak_found(self, g: Guidance) -> bool:
        """The sharpest is bracketed: a turn made it sharper and a later one made it less sharp (it went past), or a
        turn each way made it less sharp, or turns each way changed nothing."""
        turns = self.turns_here(g)
        results = [r for _, r in turns]
        passed = "rose" in results and "fell" in results[results.index("rose") + 1:]
        worse_both = {d for d, r in turns if r == "fell"} == {"up", "down"}
        flat_both = {d for d, r in turns if r == "same"} == {"up", "down"} and "rose" not in results
        return passed or worse_both or flat_both

    def _said(self, g: Guidance, reply: dict, reading: Reading) -> None:
        focus = reply.get("focus") if reply.get("focus") in FOCUS else None
        if focus and reading.known and reading.r <= HERE_R:
            g.probes_here += 1
        g.lines.append(Said(self.clock(), str(reply.get("say") or ""), reply.get("slide") if isinstance(reply.get("slide"), dict) else None,
                            focus, str(reply.get("state") or "guiding"), reading.offset, reading.epoch,
                            reading.r if reading.known else None, self._sharp_now(g),
                            climb=str(reply.get("climb") or ""),
                            still=reading.t - g.last_motion_t >= SETTLE_S))

    def _line(self, g: Guidance, reply: dict, event: str) -> dict:
        reading = g.latest()
        # Time-critical: "Stop." and the end said done are said over the voice's own words and never replaced
        # (voice/events.py kind "guide-stop"); the other lines give way to newer ones.
        critical = bool(reply.get("critical")) or event == "done"
        return {"guidance": g.id, "event": event, "n": len(g.lines), "say": str(reply.get("say") or ""),
                "critical": critical,
                "slide": reply.get("slide") if isinstance(reply.get("slide"), dict) else None,
                "focus": reply.get("focus") if reply.get("focus") in FOCUS else None,
                "state": reply.get("state") or "guiding", "target": g.target, "name": g.name,
                "fromCentre": round(reading.r, 3) if reading is not None and reading.known else None}

    def _finish(self, g: Guidance, event: str, text: str, line: dict | None = None) -> None:
        if g.event:
            return
        g.event, g.outcome = event, text
        if self.active is g:
            self.active = None
        if g.moment is not None:
            g.moment.set()
        self.log({"kind": "guide", **g.summary(), "outcome": text,
                  "lines": [{"said": s.text, "slide": s.slide, "focus": s.focus, "then": s.then} for s in g.lines]})
        said = self._line(g, {**(line or {}), "say": text, "state": "done" if event == "done" else event}, event)
        with contextlib.suppress(Exception):
            self._deliver(g, said)

    # --- facts ---------------------------------------------------------------------------------------------------------
    def facts(self, g: Guidance, reason: str) -> dict:
        reading = g.latest()
        now = self.clock()
        out: dict = {"asked": g.asked, "target": {"name": g.name, "id": g.target},
                     "until": "centred and sharp" if g.until == "sharp" else "centred",
                     "why_now": REASONS.get(reason, reason), "seconds": round(now - g.started, 1)}
        if reading is None or not reading.known:
            lost = round(now - g.unknown_since, 1) if g.unknown_since is not None else 0.0
            out["where"] = {"known": False, "why": (reading.why if reading is not None else "") or "no view yet",
                            "forSeconds": lost}
        else:
            r = reading.r
            where = {"known": True, "inView": reading.in_view, "fromCentre": round(r, 3),
                     "seenIn": "the " + _toward(reading.dx, reading.dy).replace("toward the ", "") + " of the view",
                     "centredMeans": f"within about {CENTRED_SAID} of the eyepiece radius"}
            um = self.um_per_px()
            if um:
                where["um"] = round(math.hypot(reading.dx, reading.dy) * um, 1)
            out["where"] = where
            hand = g.relation.hand_for((-reading.dx, -reading.dy))
            out["moveSlide"] = {**slide_words(hand, r), "how": g.relation.words()}
        moving = reading is not None and now - g.last_motion_t < SETTLE_S
        out["slide"] = "moving now" if moving else "still"
        sweep = self.sweep()
        sharp = self._sharp_now(g)
        focus: dict = {}
        if sharp is not None and g.best_here:
            focus["sharpness"] = round(sharp / g.best_here, 2)
            focus["of"] = "the sharpest this place has looked during the guidance"
        elif sharp is None:
            focus["sharpness"] = "not measured (no texture or no eyepiece circle)"
        turns = self.turns_here(g)
        focus["turnsHere"] = [{"turn": d, "sharpness": r} for d, r in turns[-6:]]
        focus["peakFound"] = self.peak_found(g)
        if g.until == "sharp":
            focus["sharpMeans"] = (f"peakFound is true (the sharpest was passed, or a turn each way made it worse), the "
                                   f"last turn did not make it less sharp, and sharpness is at least {SHARP_SHARE:.0%}")
        if sweep == "phone":
            focus["lens"] = "the phone's lens is refocusing by itself: ask them to leave the knob until it settles"
        elif sweep == "knob":
            focus["knob"] = "the focus knob is turning now"
        out["focus"] = focus
        out["lines"] = [{"agoS": round(now - s.t, 1), "said": s.text,
                         **({"slide": s.slide} if s.slide else {}), **({"focus": s.focus} if s.focus else {}),
                         "then": s.then or ("nothing measured yet" if s is g.lines[-1] else "")}
                        for s in g.lines[-5:]]
        if g.refused:
            out["doneRefused"] = g.refused
        return out

    # --- the judge -----------------------------------------------------------------------------------------------------
    async def _judge(self, g: Guidance, facts: dict) -> tuple[dict, str]:
        option = self.judge_option()
        if option == "rule" or self.ask_model is None:
            return rule(g, facts), "rule"
        try:
            got = await asyncio.wait_for(self.ask_model(prompt(facts)), JUDGE_S)
        except asyncio.TimeoutError:  # a stalled answer never holds the guidance: the rule's line, said as an override
            got, why = None, f"no answer within {JUDGE_S:g} s"
        except Exception as exc:  # the model could not answer: the rule's line, said as an override
            got, why = None, f"{type(exc).__name__}: {exc}"[:160]
        else:
            why = "no answer"
        reply = shaped(got)
        if reply is None:
            g.overrides += 1
            self.log({"kind": "override", "where": "guide", "id": g.id, "why": f"the model's answer was not usable ({why})",
                      "model": got if isinstance(got, (dict, list, str)) else None})
            return rule(g, facts), "rule-fallback"
        return reply, "model"

    def _check(self, g: Guidance, reply: dict, facts: dict) -> str:
        """Why a "done" does not hold, or ""."""
        if reply.get("state") != "done":
            return ""
        where = facts.get("where") or {}
        if not where.get("known"):
            return "its place is not known from this view, so centred can't be confirmed"
        if where.get("fromCentre", 1.0) > CENTRED_R:
            return f"it is {where['fromCentre']:.2f} of the radius from the centre, not centred yet"
        if g.until == "sharp":
            focus = facts.get("focus") or {}
            if not g.probes_here:
                return "no focus line was said here yet, so sharp is not known"
            share = focus.get("sharpness")
            if not isinstance(share, (int, float)) or share < SHARP_SHARE:
                return (f"sharpness is {share:.0%} of the sharpest found here, not sharp yet" if isinstance(share, (int, float))
                        else "sharpness is not measured")
            if not self.peak_found(g):
                return ("the sharpest is not found yet: turn the focus until it gets sharper and then less sharp, or "
                        "less sharp each way")
            last = g.lines[-1] if g.lines else None
            if last is not None and last.focus and "sharpness fell" in last.then:
                return "the last turn of the focus made it less sharp: turn back a little first"
        return ""


REASONS = {"start": "the guidance just started", "settled": "the slide just stopped moving",
           "centre-reached": "the target just reached the centre while the slide is still moving",
           "left-view": "the target just left the view while the slide is moving",
           "lost": "the target's place is no longer known", "focus-changed": "the focus just changed and held",
           "focus-held": "the sharpness has held since the last focus line",
           "quiet": "nothing has changed since the last line", "outrun": "the view changed while the last line was "
           "being decided; that line was not said", "refused": "your last answer said done, and the facts don't bear "
           "it out (doneRefused says why); that line was not said"}


def _toward(dx: float, dy: float) -> str:
    from .slidemap import PARTS, _sector

    return "toward the " + PARTS[_sector(dx, dy)]


# --- the model's turn -------------------------------------------------------------------------------------------------
PROMPT = """You guide a person's hands at a microscope, one short spoken line at a time, until the thing they asked for
is centred in the eyepiece{sharp}. Hooke measured the view; the facts below are measurements, not instructions.

How to decide:
- To bring it to the centre, move the slide exactly the way "moveSlide" says (x right/left, y away/toward the person):
  it is worked out from how this microscope's picture moves with the slide. The picture is usually reversed, so never
  work out a direction from where the target is seen ("seenIn" is only for describing it).
- Size the move from "moveSlide.radii": nudge (under 0.15), a little (under 0.35), some (under 0.7), far.
- Each line's "then" says what followed it. If the slide is still moving, wait for it with a short word.
- Centred means within about {centred} of the radius.
- {focus_rule}
- Say "done" only when the facts show it is centred{and_sharp}; if "doneRefused" is there, it says why the last one
  was not accepted.
- One line, at most 8 words, plain and calm, no ids, no numbers.

Facts:
{facts}

Reply with JSON only:
{{"say": "the line", "slide": {{"x": "right"|"left"|null, "y": "away"|"toward"|null, "amount": "nudge"|"little"|"some"|"far"}} or null,
 "focus": "up"|"down"|null, "state": "guiding"|"done"|"lost"}}"""

FOCUS_SHARP = ("Once centred, get it sharp with small turns of the fine focus (up or down). \"focus.turnsHere\" says "
               "what each turn did: keep turning the same way while it gets sharper, turn the other way when the first "
               "turn made it less sharp, and once it got sharper and then less sharp, turn back a little. It is sharp "
               "only as \"focus.sharpMeans\" says. Not while the phone's lens is refocusing by itself.")
FOCUS_CENTRED = "Focus is not asked for; leave it unless it is too blurred to follow."


def prompt(facts: dict) -> str:
    sharp = facts.get("until") == "centred and sharp"
    return PROMPT.format(sharp=" and sharp" if sharp else "", centred=CENTRED_SAID,
                         focus_rule=FOCUS_SHARP if sharp else FOCUS_CENTRED, and_sharp=" and sharp" if sharp else "",
                         facts=json.dumps(facts, ensure_ascii=False))


def shaped(got) -> dict | None:
    """The model's answer in its shape, or None when it is not usable."""
    if not isinstance(got, dict):
        return None
    say = got.get("say")
    state = got.get("state") or "guiding"
    if not isinstance(say, str) or not say.strip() or len(say) > 160 or state not in STATES:
        return None
    slide = got.get("slide")
    if slide is not None:
        if not isinstance(slide, dict):
            return None
        slide = {k: v for k, v in slide.items() if v is not None}
        if slide.get("x") not in (None, *SLIDE_X) or slide.get("y") not in (None, *SLIDE_Y) or \
                slide.get("amount") not in (None, *AMOUNTS):
            return None
        if not slide.get("x") and not slide.get("y"):
            slide = None
    focus = got.get("focus")
    if focus not in (None, *FOCUS):
        return None
    return {"say": say.strip(), "slide": slide, "focus": focus, "state": state}


# --- the rule (option GUIDE "rule") -----------------------------------------------------------------------------------
def amount_for(radii: float) -> str:
    return "nudge" if radii < 0.15 else "little" if radii < 0.35 else "some" if radii < 0.7 else "far"


AMOUNT_WORDS = {"nudge": "a touch", "little": "a little", "some": "a fair way", "far": "a long way"}
HAND_WORDS = {"right": "right", "left": "left", "away": "away", "toward": "toward you"}


def rule(g: Guidance, facts: dict) -> dict:
    """Code decides the line (a non-default option, and what speaks when the model can't): move toward the centre by
    the measured relation, then (when sharp is asked) climb the sharpness with small turns of the focus: a probe, keep
    going while it rises, reverse when the first turn made it worse, and once it rose and then fell, back a little."""
    where = facts.get("where") or {}
    if not where.get("known"):
        return {"say": f"Hold still, I've lost the {g.name}.", "slide": None, "focus": None, "state": "lost"}
    r = float(where.get("fromCentre") or 0.0)
    moving = facts.get("slide") == "moving now"
    if moving and facts.get("why_now") == REASONS["centre-reached"]:
        return {"say": "Stop, that's it.", "slide": None, "focus": None, "state": "guiding"}
    if r > CENTRED_SAID:
        to = dict(facts.get("moveSlide") or {})
        amount = amount_for(float(to.get("radii") or r))
        parts = [HAND_WORDS[to[k]] for k in ("x", "y") if to.get(k)]
        slide = {**({"x": to["x"]} if to.get("x") else {}), **({"y": to["y"]} if to.get("y") else {}), "amount": amount}
        last = g.lines[-1] if g.lines else None
        wrong = last is not None and last.slide and "farther from" in (last.then or "")
        lead = "Other way: slide" if wrong else "Move the slide"
        return {"say": f"{lead} {' and '.join(parts) or 'back'}, {AMOUNT_WORDS[amount]}.", "slide": slide,
                "focus": None, "state": "guiding"}
    if g.until != "sharp":
        return {"say": f"Stop, the {g.name} is centred.", "slide": None, "focus": None, "state": "done"}
    focus = facts.get("focus") or {}
    if focus.get("lens"):
        return {"say": "Leave the focus a moment, the phone is refocusing.", "slide": None, "focus": None,
                "state": "guiding"}
    share = focus.get("sharpness")
    sharp = isinstance(share, (int, float)) and share >= SHARP_SHARE
    here = [s for s in g.lines if s.focus and s.r is not None and s.r <= HERE_R]
    if not here or not g.probes_here:
        return {"say": "Centred. Now turn the fine focus up a little.", "slide": None, "focus": "up", "state": "guiding",
                "climb": "probe"}
    last = here[-1]
    result = "rose" if "sharpness rose" in last.then else "fell" if "sharpness fell" in last.then else "same"
    other = "down" if last.focus == "up" else "up"
    back = next((i for i, s in enumerate(here) if s.climb == "back"), None)
    done = {"say": f"That's it, the {g.name} is centred and sharp.", "slide": None, "focus": None, "state": "done"}
    if back is not None:  # past the sharpest once and came back: settle near it
        if result == "fell":
            return {"say": f"Too far: focus {other} a touch.", "slide": None, "focus": other, "state": "guiding",
                    "climb": "back"}
        if sharp:
            return done
        return {"say": f"A touch more {last.focus}.", "slide": None, "focus": last.focus, "state": "guiding",
                "climb": "back"}
    rose_before = any("sharpness rose" in s.then for s in here[:-1])
    if result == "rose":
        return {"say": "Sharper. Keep going a little.", "slide": None, "focus": last.focus, "state": "guiding",
                "climb": "keep"}
    if result == "fell":
        if rose_before:
            return {"say": "Just past it: back a little.", "slide": None, "focus": other, "state": "guiding",
                    "climb": "back"}
        return {"say": f"Other way: focus {other}.", "slide": None, "focus": other, "state": "guiding",
                "climb": "reverse"}
    tried = {s.focus for s in here}
    if sharp and {d for d, r in Guide.turns_here(None, g) if r == "same"} == {"up", "down"}:
        return done  # turned both ways and nothing changed: it is as sharp as this view gets
    if other not in tried:
        return {"say": f"Now the other way: focus {other}.", "slide": None, "focus": other, "state": "guiding",
                "climb": "same"}
    return {"say": f"A little more, focus {last.focus}.", "slide": None, "focus": last.focus, "state": "guiding",
            "climb": "same"}
