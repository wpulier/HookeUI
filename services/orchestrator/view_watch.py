"""The watcher: what happens in the view, noticed without being asked (the live partner's item 4; the research: no live
model speaks up from video on its own, so "knowing what's going on" is Hooke's own; ADK's streaming tool
`monitor_video_stream` yields only when what it measures changes, and Vision-Agents forwards each consumer the newest
frame only). Watches the user asked for ("tell me when they stop moving") are watcher.py; this one always runs.

- **On the camera path, the newest frame only:** a listener on every published frame (`Hub.on_frame`) that reads the
  numbers the hub already measured (the slide's offset and motion epoch, steady, the eyepiece disc, sharpness): nothing
  is decoded, copied or queued, and a frame that comes late is simply the newest. The counted population is read once a
  second after a frame, from the layer's own member checks (population.Layer, every 0.3 s off the loop). Nothing it does
  waits, and the live view's frame rate and delay are unchanged (`bench/watch_bench.py` measures its cost per frame).
- **On the settled view:** nothing is judged while the slide moves. Each kind of event is a measured change, never a
  name or a verdict; the voice model is given the facts and decides how to say them:
  - **slide-moved:** the view settled (`sight.SETTLE_S` still) on a new place, more than `sight.MOVED_FRACTION` of the
    eyepiece's radius from the place it last settled on (Sight's own "the view changed"), or after continuity was lost
    (a new motion epoch: too fast to follow, or another picture), confirmed once the tracker had `CUT_SETTLE_S` to
    rejoin a place seen before (a rejoin to the same place is no move);
  - **focus-drifted:** on a settled field with texture, the sharpness (the Laplacian's variance over the contrast
    squared, so a change of light is not a change of focus) stayed under `DRIFT_SHARE` of the sharpest this field
    looked since it settled for `DRIFT_HOLD_S`, while nobody turned the knob (the coach's sweep); after the knob, the
    field's sharpest starts again; the phone's lens changing it (the eyepiece disc's size, coach.LENS_SHARE) is said;
  - **population-changed:** the population shown over the view (the counted cells) on a still view: members inside the
    eyepiece that were in place and are no longer where they were counted (their own pixels checked and not there), at
    least `POP_MIN` and `POP_SHARE` of them, on `POP_HOLD` readings in a row; with the counts, any come back, and the
    sizes of what is in place before and now (a new cell is not counted until the cells are found again);
  - **cell-divided:** an interface (`divided`): nothing in the app detects a division yet (`DIVISION`), and `status`
    says so.
- **Every event** carries when it happened (`eventT`: the frame at which it was established; `sinceT`: when its
  condition began), the frame, and the ids the voice can point at (`pointAt`: "frame" with a region of the camera
  picture, `population:<id>`, `member:<population>:<member>`, `outline:<id>`), which are drawn as it is said.
- **One delivery rule per kind** (`DELIVERY`, by option WATCH): "context" (told to the voice silently), "idle" (one
  sentence when nobody is talking) or "interrupt" (as soon as the user is not talking: the voice's own words give way).
  The user's "only what I asked" (`quieter`, "let me find things") makes every rule "context". A kind is told at
  most once in its `GAP_S`. Every event is logged ("watch-event"), delivered or not: the bench counts false alarms from them.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable

from .coach import LENS_SHARE
from .quality import MIN_PEAK
from .sight import MOVED_FRACTION, SETTLE_S

KINDS = ("slide-moved", "focus-drifted", "population-changed", "cell-divided")
# How each kind of event reaches the voice, by option WATCH (the one place; docs/architecture/seeing-and-acting.md):
#   "context"   told to the voice as a silent note: it knows, and says nothing unasked
#   "idle"      said in one short sentence when nobody is talking (no key held, nobody spoke for 1.5 s)
#   "interrupt" said as soon as the user is not talking: the voice's own words are stopped for it, never the user's
DELIVERY = {
    "quiet": {"slide-moved": "context", "focus-drifted": "idle", "population-changed": "idle", "cell-divided": "interrupt"},
    "chatty": {"slide-moved": "idle", "focus-drifted": "idle", "population-changed": "idle", "cell-divided": "interrupt"},
}
# A kind's next event is told to the voice at most this soon after its last (each is still logged, and counted).
GAP_S = {"slide-moved": 10.0, "focus-drifted": 60.0, "population-changed": 20.0, "cell-divided": 5.0}

CUT_SETTLE_S = 1.2  # after continuity was lost: still this long, so a rejoin of the same place (motion.py) comes first
FOCUS_WINDOW_S = 1.0  # sharpness is the median over this long (the scope's own breathing: 6% median, 29% at most a second)
DRIFT_SHARE = 0.6  # under this share of the field's sharpest...
DRIFT_HOLD_S = 3.0  # ...for this long, with nobody turning the knob: focus drifted
RECOVER_SHARE = 0.85  # said again only after it came back above this share (or on a new field)
POP_EVERY_S = 1.0  # the counted population is read this often
POP_MIN, POP_SHARE = 3, 0.15  # members no longer in place (or back): at least this many and this share of those in place
POP_HOLD = 3  # readings in a row
POP_SETTLE_S = 2.0  # the view still this long before its baseline: the layer's checks (every 0.3 s) caught up after a move
POP_TRAVEL_PX = 16.0  # the slide moved more than this since the population's baseline: a new baseline (population.py)
IN_VIEW = 0.9  # of the eyepiece's radius: a member's centre inside this is in view
SHOWN_IDS = 8  # member ids named in an event, at most
# Nothing in the app detects a division yet: following keeps one outline per thing (objtrack.py), and the linking model's
# links are kept one to one (operations.py), so a division is never made. A detector would call ViewWatch.divided.
DIVISION = None
NO_DIVISION = ("not available: nothing in the app detects a cell dividing yet (following keeps one outline per thing, "
               "and linking keeps links one to one)")
PARTS = ["right", "upper right", "top", "upper left", "left", "lower left", "bottom", "lower right"]


def sharpness(info) -> tuple[float, float] | None:
    """(the frame's sharpness over its contrast squared, its raw sharpness), or None unmeasured. Defocus lowers the
    first; a change of light scales sharpness and contrast squared together and leaves it."""
    stats = getattr(info, "stats", ()) or ()
    if len(stats) < 3 or stats[0] <= 0:
        return None
    focus, _, std = stats[0], stats[1], stats[2]
    return focus / max(std, 1.0) ** 2, focus


def radius_of(info) -> float:
    fov = getattr(info, "fov", None)
    return float(fov.radius) if fov is not None and fov.radius > 0 else min(info.width, info.height) / 2


def crop_of(info) -> list[int]:
    """The eyepiece crop the voice's camera picture is (marks.fov_crop, scene_tools.view_frame)."""
    from .marks import fov_crop

    c = fov_crop(getattr(info, "fov", None), info.width, info.height)
    return [c.x, c.y, c.width, c.height]


def toward(dx: float, dy: float) -> str:
    angle = math.degrees(math.atan2(-dy, dx)) % 360  # 0 right, 90 up (+y is down in the picture)
    return "toward the " + PARTS[int(((angle + 22.5) % 360) // 45)]


def hhmmss(t: float) -> str:
    return time.strftime("%H:%M:%S", time.localtime(t))


@dataclass
class Event:
    id: str  # v1, v2...: this run's events; the line about one carries it as its reference
    kind: str
    at: float  # eventT: wall time of the frame at which it was established
    since: float  # sinceT: when its condition began
    frame: int
    facts: dict
    point: list[str] = field(default_factory=list)  # ids the voice can point at
    region: list[float] | None = None  # [x, y, w, h], 0-1 of the camera picture (`crop`), for a "frame" mark
    crop: list[int] | None = None

    def text(self) -> str:
        """The facts the voice is given (it decides the words; nothing here is a sentence for it to read)."""
        facts = {"event": self.kind, "id": self.id, "at": hhmmss(self.at), **self.facts, "pointAt": self.point}
        if self.region is not None:
            facts["region"] = self.region
        return json.dumps(facts, ensure_ascii=False)

    def marks(self) -> list[dict]:
        """What is drawn as it is said: the region of the camera picture, else the first id."""
        out = []
        if self.region is not None:
            out.append({"target": "frame", "region": self.region, "style": "box"})
        out += [{"target": t, "style": "box"} for t in self.point if t != "frame"][:1]
        return out

    def frame_ref(self) -> dict | None:
        return {"id": self.frame, "crop": self.crop} if self.crop is not None else None


@dataclass
class _Field:
    epoch: int
    dx: float
    dy: float
    at: float


@dataclass
class _Baseline:
    key: tuple
    in_place: set
    epoch: int
    dx: float
    dy: float
    at: float
    streak: int = 0
    first: float = 0.0


class ViewWatch:
    def __init__(self, hub, *, deliver: Callable[[Event, str], str] | None = None,
                 mode: Callable[[], str] | None = None, quiet: Callable[[], bool] = lambda: False,
                 sweep: Callable[[], str | None] = lambda: None, um_per_px: Callable[[], float | None] = lambda: None,
                 log: Callable[[dict], None] = lambda record: None, schedule: Callable | None = None) -> None:
        self.hub = hub
        self.deliver = deliver  # (event, rule) -> where it went ("voice", "no voice"); None: logged only
        self.mode = mode or (lambda: getattr(getattr(hub, "settings", None), "watch", "quiet"))
        self.quiet = quiet  # the user's "only what I asked" (quieter, user_leads): every rule becomes context
        self.sweep = sweep  # the coach's sweep now: None, "knob" or "phone"
        self.um_per_px = um_per_px
        self.log = log
        self.schedule = schedule  # runs the population's reading after the frame's publish (loop.call_soon); None: now
        self._n = 0
        self.events: list[Event] = []  # the newest few, for tests and status
        self._last_told: dict[str, float] = {}
        self._reset()

    def _reset(self) -> None:
        self._field: _Field | None = None
        self._steady_since: float | None = None
        self._previous: tuple | None = None  # (epoch, dx, dy, steady) of the frame before
        self._epochs: set[int] = set()  # every motion epoch seen
        self._focus: deque = deque()
        self._best: tuple[float, float, float, float] | None = None  # (sharpness, raw, at, disc) of the field's sharpest
        self._low_since: float | None = None
        self._drift_said = False
        self._knob = False
        self._blurred = False
        self._pop: _Baseline | None = None
        self._pop_read = 0.0

    # --- every frame (Hub.on_frame): numbers only ---------------------------------------------------------------------
    def frame(self, info) -> None:
        mode = self.mode()
        if mode not in DELIVERY or getattr(self.hub, "view_held", False):
            if self._field is not None or self._pop is not None:
                self._reset()
            return
        t = float(info.wall_at)
        self._motion(info, t)
        self._sharpness(info, t)
        if t - self._pop_read >= POP_EVERY_S:
            self._pop_read = t
            if self.schedule is not None:
                try:
                    self.schedule(self.check_population)
                    return
                except RuntimeError:
                    pass  # no running loop: read it now
            self.check_population()

    def _settled(self, info, t: float, hold: float = SETTLE_S) -> bool:
        return bool(info.steady) and getattr(info, "tracked", True) is not False and \
            self._steady_since is not None and t - self._steady_since >= hold

    def _motion(self, info, t: float) -> None:
        previous, self._previous = self._previous, (info.epoch, float(info.dx), float(info.dy), bool(info.steady))
        seen = info.epoch in self._epochs
        self._epochs.add(info.epoch)
        if not info.steady or getattr(info, "tracked", True) is False:
            self._steady_since = None
            return
        if self._steady_since is None:
            self._steady_since = t
        anchor = self._field
        if (previous is not None and previous[3] and info.epoch != previous[0] and seen and anchor is not None
                and anchor.epoch == previous[0]):
            # The tracker rejoined a place seen before while the view stood still (motion.py: a new epoch always takes
            # a number never used, a rejoin goes back to an old one): the same place under its old name, not a move.
            self._field = _Field(info.epoch, info.dx + anchor.dx - previous[1], info.dy + anchor.dy - previous[2],
                                 anchor.at)
            return
        cut = anchor is not None and info.epoch != anchor.epoch
        if not self._settled(info, t, CUT_SETTLE_S if cut else SETTLE_S):
            return
        if anchor is None:
            self._new_field(info, t)
            return
        radius = radius_of(info)
        moved = None if cut else math.hypot(info.dx - anchor.dx, info.dy - anchor.dy)
        if moved is not None and moved < MOVED_FRACTION * radius:
            return  # still, or a nudge: the same field
        since = self._steady_since
        facts: dict = {"stillForS": round(t - self._steady_since, 1), "frame": info.id}
        if moved is None:
            facts["continuity"] = ("lost: the view changed too fast to follow (or to another picture), so how far it "
                                   "moved is not known")
        else:
            facts["moved"] = {"fieldsOfView": round(moved / (2 * radius), 2), "px": round(moved)}
            um = self.um_per_px()
            if um:
                from .scale import round_um

                facts["moved"]["um"] = round_um(moved * um)
            facts["contentMoved"] = toward(info.dx - anchor.dx, info.dy - anchor.dy)
        self._new_field(info, t)
        self._raise("slide-moved", info, t, since, facts, ["frame"], region=[0.04, 0.04, 0.92, 0.92])

    def _new_field(self, info, t: float) -> None:
        self._field = _Field(info.epoch, info.dx, info.dy, t)
        self._focus.clear()
        self._best, self._low_since, self._drift_said, self._blurred = None, None, False, False
        self._pop = None

    def _sharpness(self, info, t: float) -> None:
        measured = sharpness(info)
        if measured is None or self._field is None or not self._settled(info, t):
            return
        by = self.sweep()
        if by == "knob":  # the user is focusing: nothing is said, and the field's sharpest starts again after
            self._knob, self._focus, self._low_since = True, deque(), None
            return
        if self._knob and by is None:
            self._knob, self._best, self._drift_said = False, None, False
        self._focus.append((t, measured))
        while self._focus and t - self._focus[0][0] > FOCUS_WINDOW_S:
            self._focus.popleft()
        if len(self._focus) < 5 or t - self._focus[0][0] < 0.5 * FOCUS_WINDOW_S:
            return
        now = statistics.median(v[0] for _, v in self._focus)
        raw = statistics.median(v[1] for _, v in self._focus)
        if self._best is None or now > self._best[0]:
            self._best = (now, raw, t, float(getattr(info, "disc", 0.0) or 0.0))
        best, best_raw, best_at, best_disc = self._best
        if best_raw < MIN_PEAK:  # too little texture for sharpness to mean anything (quality.MIN_PEAK)
            self._blurred = False
            return
        share = now / best
        self._blurred = share < DRIFT_SHARE
        if share >= DRIFT_SHARE:
            self._low_since = None
            if share >= RECOVER_SHARE:
                self._drift_said = False
            return
        self._low_since = self._low_since if self._low_since is not None else t
        if self._drift_said or t - self._low_since < DRIFT_HOLD_S:
            return
        self._drift_said = True
        facts = {"sharpness": f"{round(100 * share)}% of the sharpest this field looked since it settled",
                 "sharpestAt": hhmmss(best_at), "lowForS": round(t - self._low_since, 1),
                 "knob": "nobody turned the focus knob", "frame": info.id}
        disc = float(getattr(info, "disc", 0.0) or 0.0)
        if disc > 0 and best_disc > 0 and abs(disc - best_disc) / best_disc > LENS_SHARE:
            facts["phoneLens"] = (f"the eyepiece disc's size changed by {100 * abs(disc - best_disc) / best_disc:.1f}% "
                                  "since: the phone's lens refocused (the knob never changes it)")
        self._raise("focus-drifted", info, t, self._low_since, facts, ["frame"], region=[0.04, 0.04, 0.92, 0.92])

    # --- the counted population, once a second -----------------------------------------------------------------------
    def check_population(self) -> None:
        """The population shown over the view, read from the layer's own checks of its members (population.Layer): which
        members inside the eyepiece are in place now, against a baseline taken on this still view."""
        hub = self.hub
        info, layer = getattr(hub, "latest_info", None), getattr(hub, "layer", None)
        population = layer.population() if layer is not None and getattr(layer, "shown", None) else None
        if info is None or population is None or population.view.get("sourceImage") or self.mode() not in DELIVERY:
            self._pop = None  # nothing counted on the live view (a population found on a still never moves)
            return
        t = float(info.wall_at)
        if not self._settled(info, t, POP_SETTLE_S) or self._blurred:
            return  # judged only on a still, sharp view: a blur fails every member's check (focus-drifted says it)
        try:
            if layer.drawn_frame_reason(info):
                self._pop = None
                return
            states = layer.states()
        except Exception:
            return
        view = layer.view or (population.view.get("epoch"), float(population.view.get("dx") or 0.0),
                              float(population.view.get("dy") or 0.0))
        cx, cy = layer.correction
        sx, sy = float(info.dx) - float(view[1]) + cx, float(info.dy) - float(view[2]) + cy
        fov = getattr(info, "fov", None)
        ox, oy = (fov.cx, fov.cy) if fov is not None else (info.width / 2, info.height / 2)
        reach = IN_VIEW * radius_of(info)
        boxes = {}
        for m in population.live():
            x0, y0, x1, y1 = m.box
            if math.hypot((x0 + x1) / 2 + sx - ox, (y0 + y1) / 2 + sy - oy) <= reach:
                boxes[m.id] = (x0 + sx, y0 + sy, x1 + sx, y1 + sy)
        in_place = {mid for mid in boxes if states.get(mid) == "verified"}
        failed = {mid for mid in boxes if (layer.placement.get(mid) or {}).get("state") == "unverified"
                  and (layer.placement.get(mid) or {}).get("frame") is not None}
        key = (population.id, population.revision)
        base = self._pop
        if (base is None or base.key != key or base.epoch != info.epoch
                or math.hypot(info.dx - base.dx, info.dy - base.dy) > POP_TRAVEL_PX):
            self._pop = _Baseline(key, in_place, info.epoch, float(info.dx), float(info.dy), t)
            return
        gone = sorted(base.in_place & failed, key=_member_order)
        back = sorted(in_place - base.in_place, key=_member_order)  # said with a change, never one by itself
        need = max(POP_MIN, math.ceil(POP_SHARE * max(len(base.in_place), 1)))
        if len(gone) < need:
            base.streak = 0
            return
        base.streak += 1
        base.first = base.first if base.streak > 1 else t
        if base.streak < POP_HOLD:
            return
        facts = {"population": population.id, "counted": len(population.live()),
                 "countedAt": hhmmss(float(population.view.get("at") or population.made_at)),
                 "inView": len(boxes), "inPlaceBefore": len(base.in_place), "inPlaceNow": len(in_place),
                 "baselineAt": hhmmss(base.at),
                 "measuredBy": "each outline's own pixels checked where the slide's move puts it (every 0.3 s)",
                 "frame": info.id}
        if gone:
            facts["noLongerWhereCounted"] = {"count": len(gone), "ids": gone[:SHOWN_IDS]}
        if back:
            facts["backInPlace"] = {"count": len(back), "ids": back[:SHOWN_IDS]}
        sizes = _sizes(population, base.in_place, in_place)
        if sizes:
            facts["medianArea"] = sizes
        crop = crop_of(info)
        changed = gone or back
        region = _region([boxes[m] for m in changed], crop)
        point = [f"population:{population.id}"] + [f"member:{population.id}:{m}" for m in in_place & set(back)][:3]
        self._pop = _Baseline(key, in_place, info.epoch, float(info.dx), float(info.dy), t)
        self._raise("population-changed", info, t, base.first, facts, point, region=region, crop=crop)

    # --- a division: the interface a detector would call --------------------------------------------------------------
    def divided(self, parent: str, children: list[str], info, *, by: str, confidence: float | None = None,
                region: list[float] | None = None) -> Event | None:
        """A tracked cell divided (`parent` the followed outline or member, `children` the new ones' ids), said by the
        detector `by`. No detector calls it yet (DIVISION)."""
        if self.mode() not in DELIVERY:
            return None
        t = float(info.wall_at)
        facts = {"parent": parent, "children": list(children), "detectedBy": by, "frame": info.id}
        if confidence is not None:
            facts["confidence"] = round(float(confidence), 2)
        return self._raise("cell-divided", info, t, t, facts, [_pointable(parent)] + [_pointable(c) for c in children],
                           region=region)

    def status(self) -> dict:
        """What is watched, how each kind is delivered now, and whether it can be detected here."""
        mode = self.mode()
        rules = DELIVERY.get(mode, {})
        return {"watch": mode, "quieter": bool(self.quiet()), "kinds": {
            kind: {"delivery": ("context" if self.quiet() else rules[kind]) if rules else "off",
                   **({"available": False, "why": NO_DIVISION} if kind == "cell-divided" and DIVISION is None
                      else {"available": True})} for kind in KINDS}}

    # --- raising one -------------------------------------------------------------------------------------------------
    def _raise(self, kind: str, info, t: float, since: float, facts: dict, point: list[str], *,
               region: list[float] | None = None, crop: list[int] | None = None) -> Event:
        self._n += 1
        event = Event(f"v{self._n}", kind, t, since, int(info.id), facts, point,
                      region=region, crop=crop if crop is not None else (crop_of(info) if region is not None else None))
        self.events = (self.events + [event])[-20:]
        rule = DELIVERY[self.mode()][kind]
        if self.quiet() and rule != "context":
            rule = "context"
        to = "not delivered"
        if t - self._last_told.get(kind, -1e9) < GAP_S[kind]:
            to = f"not told: one {kind} {t - self._last_told[kind]:.0f} s ago"
        elif self.deliver is not None:
            try:
                to = self.deliver(event, rule) or "no voice"
            except Exception as exc:  # delivery never stops watching; said in the log
                to = f"failed: {type(exc).__name__}: {exc}"[:200]
            if to == "voice":
                self._last_told[kind] = t
        self.log({"kind": "watch-event", "event": kind, "id": event.id, "eventT": round(t, 3), "sinceT": round(since, 3),
                  "frame": event.frame, "delivery": rule, "to": to, "pointAt": point, "facts": facts})
        return event


def _member_order(mid: str) -> tuple:
    digits = "".join(ch for ch in str(mid) if ch.isdigit())
    return (int(digits) if digits else 0, str(mid))


def _pointable(ref: str) -> str:
    return ref if ":" in ref else f"outline:{ref}"


def _sizes(population, before: set, now: set) -> dict | None:
    """The median area of the members in place before and now (µm² when the population has a scale, else px)."""
    def area(mid):
        try:
            v = population.value(mid, "areaPx")
        except Exception:
            return None
        return float(v) if isinstance(v, (int, float)) else None

    um = ((population.scale_id or {}).get("umPerPx") if isinstance(population.scale_id, dict) else None) or None
    out = {}
    for name, ids in (("inPlaceBefore", before), ("inPlaceNow", now)):
        values = [a for a in (area(m) for m in ids) if a is not None]
        if values:
            median = statistics.median(values)
            out[name] = round(median * um * um, 1) if um else round(median)
    if not out:
        return None
    out["unit"] = "µm²" if um else "px"
    return out


def _region(boxes: list, crop: list[int]) -> list[float] | None:
    """The boxes' bounds as [x, y, w, h], 0-1 of the camera picture's crop, kept inside it."""
    if not boxes:
        return None
    cx, cy, cw, ch = crop
    x0 = max(0.0, (min(b[0] for b in boxes) - cx) / cw)
    y0 = max(0.0, (min(b[1] for b in boxes) - cy) / ch)
    x1 = min(1.0, (max(b[2] for b in boxes) - cx) / cw)
    y1 = min(1.0, (max(b[3] for b in boxes) - cy) / ch)
    if x1 <= x0 or y1 <= y0:
        return None
    return [round(x0, 3), round(y0, 3), round(x1 - x0, 3), round(y1 - y0, 3)]
