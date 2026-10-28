"""Sight: the app's running look at the view, so the brain and the voice already know what is
there before anyone asks.

When the slide has settled after a change (a new motion epoch, a move of more than a third
of the eyepiece, or a minute without a look on a picture that changed since), one fast vision call describes the view: a
summary, the specimen, and up to eight notable things with boxes. The result is kept with the
frame it came from, so the boxes follow the slide like outlines do (p + offset_now -
offset_then) and are dropped when continuity is lost.

Used by: the voice (as silent context through GPT-Live's thinking.append, so "what are we
looking at?" is answered at once), the brain (context, and its fast pass can highlight a seen
thing straight from its box without another vision call), and the studio (nothing drawn; the
view is the user's).

Option SIGHT (options.py): which model looks, or off. Runs only while someone is connected.
"""
from __future__ import annotations

import asyncio
import copy
import math
import time
import traceback
import weakref
from dataclasses import dataclass, field
from typing import Callable

import numpy as np

from .coords import gemini_box_to_frame
from . import execution
from .marks import encode_crop, fov_crop
from .quality import describe, measured, unnameable

SETTLE_S = 0.6          # steady this long before looking
MIN_GAP_S = 4.0         # at most one look per this many seconds while the view stays put
CHANGED_GAP_S = 1.5     # after the view changed, look again as soon as it settles (this far apart)
REFRESH_S = 60.0        # look again after this long if the picture changed at all (light, focus); never on an unchanged one
STILL_LOOKS = 4         # at most this many looks a minute because something changed in a still view
MOVED_FRACTION = 0.35   # of the eyepiece radius
QUESTION_AGE_S = 20.0   # the user starts talking and the look is older than this: look again (ADR 0016's See loop)
QUESTION_LOOKS = 6      # at most this many looks a minute because the user started talking
MOVING_SHARE = 0.2      # this share of the small things in view moving counts as things moving (movement.py)
MOTION_FRESH_S = 15.0   # how long a movement reading is told to the voice

SIGHT = """You are the eyes of ObservationUI, a live microscope assistant. The image is what its
camera sees now; the camera is usually on a light microscope's eyepiece.{context}
Describe the view so the assistant can talk about it naturally and point at things.
- "summary": one or two plain sentences on what the view shows (say "likely" when unsure).
- "specimen": a few words for the whole view (e.g. "blood smear", "onion skin", "not a slide:
  a desk"). No magnification or size in micrometres: the app measures sizes itself, so say
  how big things look next to each other instead.
The specimen can be anything (tissue, blood, microbes, pollen, fibres, crystals, insects...).
Name what you see, and for cells the cell type when the image supports it.
- "things": up to 8 distinct, notable things someone might ask about, most notable first,
  each with "name" (a 1-4 word best guess), "where" (a few words like "centre" or "upper
  left"), a tight "box_2d" [ymin, xmin, ymax, xmax] on a 0-1000 scale of the image, and a
  calibrated "confidence".
- "focus": "sharp", "soft" or "blurry".{same}
Describe only what this image shows. It may not be a microscope slide at all (the camera can
be pointed at a room, a table, the floor or a blank view): then say so plainly and list no
cells. Never invent detail you cannot see.

Reply with JSON only:
{{"summary": "...", "specimen": "...", "things": [{{"name": "...", "where": "...", "box_2d": [0, 0, 0, 0], "confidence": 0.0}}], "focus": "sharp"{same_key}}}"""
# A specimen the user named (day/subject.py SubjectTracker.name): a strong hint, not a gag.
USER_NAMED = """
The user, who has the slide in hand, says it is: {name}. Use that for "specimen" and name
things as parts of it, unless the image plainly shows something else (then say what you see)."""
SAME = """
- "same_as_before": true if this is still the same specimen as before ({specimen}), false if
  it now shows something else (another slide or specimen, no slide, the room, a blank view)."""


def sight_prompt(specimen: str | None, named: str | None, scale: str = "", view: str = "") -> str:
    """What the running look is told: the subject is only asked about (`specimen`), a name the
    user gave is a strong hint (`named`), the field width when the scale is known (`scale`,
    scale.Reading.words), and what the app measured on the picture (`view`, quality.measured:
    numbers it weighs, never a verdict) (tests/golden/)."""
    from .agent import scale_line

    context = ((USER_NAMED.format(name=named) if named else "") + (f"\n{scale_line(scale)}" if scale else "")
               + (f"\n{view}" if view else ""))
    return SIGHT.format(context=context,
                        same=SAME.format(specimen=specimen) if specimen else "",
                        same_key=', "same_as_before": true' if specimen else "")


@dataclass
class Seen:
    id: str            # s1, s2... (new ids on every look)
    name: str
    where: str
    box: tuple[float, float, float, float]  # frame pixels at the look's frame
    confidence: float | None


@dataclass
class SightView:
    frame_id: int
    epoch: int
    dx: float
    dy: float
    radius: float
    summary: str
    specimen: str
    things: list[Seen]
    focus: str
    at: float = field(default_factory=time.time)
    ms: float = 0.0
    same: bool | None = None  # still the session's specimen (None when there was none to compare)
    frame_at: float = 0.0  # wall time its frame was taken (FrameInfo.wall_at): how old a note made from it is (voice-note)

    def box_now(self, thing: Seen, info) -> tuple[float, float, float, float] | None:
        """Where the thing is in frame `info`, or None if continuity was lost since."""
        if info is None or info.epoch != self.epoch:
            return None
        sx, sy = info.dx - self.dx, info.dy - self.dy
        x0, y0, x1, y1 = thing.box
        return x0 + sx, y0 + sy, x1 + sx, y1 + sy

    def current(self, info) -> bool:
        """Still a fair description of what is in view."""
        if info is None or info.epoch != self.epoch:
            return False
        return float(np.hypot(info.dx - self.dx, info.dy - self.dy)) < MOVED_FRACTION * max(self.radius, 1.0)

    def text(self, info=None) -> str:
        if not self.current(info):
            # Nothing from an old look may be pointed at: the slide has moved since.
            return (f"Before the slide last moved: {self.summary} Specimen: {self.specimen or 'not sure'}. "
                    "The slide moved since this look; positions are from before.")
        things = "; ".join(f"{t.id} {t.name} ({t.where})" for t in self.things[:8]) or "nothing distinct"
        return f"{self.summary} Specimen: {self.specimen or 'not sure'}. Things in view: {things}. Focus: {self.focus}."

    def wire(self) -> dict:
        return {"frameId": self.frame_id, "summary": self.summary, "specimen": self.specimen, "focus": self.focus,
                "things": [{"id": t.id, "name": t.name, "where": t.where, "confidence": t.confidence} for t in self.things],
                "at": self.at, "ms": round(self.ms)}


@dataclass(frozen=True)
class SightSelection:
    """Detached original notes; only real publisher/Ticket holds own their JPEG."""
    view: SightView | None = None
    _capture: object = field(default=None, repr=False)
    error: str | None = None

    @property
    def capture(self):
        return self._capture() if self._capture is not None else None


class Sight:
    def __init__(self, hub, agent, wanted: Callable[[], bool] | None = None, compare_to: Callable[[], str] | None = None,
                 user_named: Callable[[], str] | None = None) -> None:
        self.hub = hub
        self.agent = agent
        self.view: SightView | None = None
        self._hold = None
        self._publication = 0
        self.version = 0
        self.listeners: list[Callable[[SightView], None]] = []
        self.wanted = wanted or (lambda: bool(hub.clients))
        self._task: asyncio.Task | None = None
        self._unsteady_at = 0.0
        self._last_look = 0.0
        self._looked_changed_at: float | None = None  # FrameInfo.changed_at of the last look's frame (hub.py)
        self._looked_labels: tuple = ()
        self._busy = False
        self.looks = 0  # looks the running look started by itself, failed ones too (/api/health: the sim counts them)
        self.recheck = False  # the last look saw something new: look again soon to confirm
        self._question_looks: list[float] = []
        self._question_pending = False
        self.question_view: SightView | None = None  # the newest look a question asked for (voice/route.py sends it at once)
        self._content_since_look = False  # something new appeared since the last look (quality.StillChange)
        self.motion: tuple[float, str] | None = None  # (when, words): the movement measured when the user last spoke
        self.compare_to = compare_to  # the subject to ask "still the same?" about (day/subject.py)
        self.user_named = user_named  # what the user says the specimen is (day/subject.py)

    def select(self, request=None) -> SightSelection:
        """Bind once, including absence; never take a newer frame from the ring."""
        if request is not None and hasattr(request, "sight_selection"):
            return request.sight_selection
        view = copy.deepcopy(self.view)
        held = self._hold
        capture = (held.capture if held is not None and not held.released and view is not None
                   and held.capture.id == view.frame_id else None)
        error = None
        if request is not None and capture is not None:
            from .capture import Refused

            if request.closed or request.store is not held.store:
                error, capture = "the original running look is no longer held", None
            else:
                try:
                    request.hold(capture, "analysis", "bytes")
                except Refused as exc:
                    error, capture = str(exc), None
        selected = SightSelection(view, weakref.ref(capture) if capture is not None else None, error)
        if request is not None:
            request.sight_selection = selected
        return selected

    def still_changed(self, kind: str, info) -> None:
        """Hub: something changed while the slide stayed still (quality.StillChange). Something
        new in view ("content") is looked at again soon, within a budget; a change of light or
        focus is not a new subject. Every decision is logged."""
        now = time.monotonic()
        self._still_looks = [t for t in getattr(self, "_still_looks", []) if now - t < 60.0]
        if kind == "content":
            self._content_since_look = True
        look = kind == "content" and len(self._still_looks) < STILL_LOOKS and self.wanted()
        if look:
            self._still_looks.append(now)
            self.recheck = True
        if self.agent is not None and hasattr(self.agent, "log"):
            self.agent.log({"kind": "still-change", "what": kind, "frameId": getattr(info, "id", None), "lookAgain": look,
                            "why": None if look else ("budget" if kind == "content" else f"a {kind} change, not new content")})

    def _cannot_tell(self, info, bad: tuple[str, ...]) -> SightView:
        self._publication += 1
        if self._hold is not None:
            self._hold.release()
            self._hold = None
        radius = info.fov.radius if info.fov is not None else min(info.width, info.height) / 2
        reason = describe(bad)
        unchanged = self.view is not None and self.view.summary.startswith("Can't tell") and self.view.focus == reason
        self.view = SightView(info.id, info.epoch, info.dx, info.dy, float(radius),
                              f"Can't tell yet: the view is {reason}.", "", [], reason,
                              frame_at=getattr(info, "wall_at", 0.0))
        self.recheck = True
        if not unchanged:  # one log line per stretch of the same trouble
            self.version += 1
            self.agent.log({"kind": "sight-skip", "quality": list(bad), "frameId": info.id})
            for listener in list(self.listeners):
                try:
                    listener(self.view)
                except Exception:
                    pass
        return self.view

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop())

    def stop(self) -> None:
        self._publication += 1  # an in-flight provider may not publish after stop
        if self._hold is not None:
            self._hold.release()
            self._hold = None
        if self._task is not None:
            self._task.cancel()

    def _labels(self) -> tuple:
        return tuple(sorted(o.label for o in self.hub.scene.objects.values() if o.label and o.trial is None))

    async def question_coming(self, measure_motion=None) -> str | None:
        """The user started talking (the voice's first words of an utterance): look again now when the view may
        have changed since the current look, so the voice's notes are fresh by the time they finish. Decided from
        what the app already measures, never by classifying every frame: the slide moved, something new appeared,
        things are moving (`measure_motion`, movement.py's share, measured off the loop), or the look is old. On a
        still slide with a recent look nothing happens. Looks go through `due()` like every other; returns why."""
        now = time.monotonic()
        info, view = self.hub.latest_info, self.view
        reason = ("no look yet" if view is None else "the slide moved" if not view.current(info)
                  else "something new appeared" if self._content_since_look else None)
        share = None
        if reason is None and measure_motion is not None:
            # A still slide with a current look: only what moves on it can have changed (a swimmer, a cell
            # dividing). Measured off the loop (movement.py, the last 4 s), and told to the voice while fresh.
            try:
                got = await measure_motion()
            except Exception:
                got = None
            if got is not None:
                share, words = got
                self.motion = (time.time(), words)
                if share >= MOVING_SHARE:
                    reason = "things are moving"
        if reason is None and now - self._last_look > QUESTION_AGE_S:
            reason = "the look is old"
        self._question_looks = [t for t in self._question_looks if now - t < 60.0]
        look = reason is not None and len(self._question_looks) < QUESTION_LOOKS and self.wanted()
        if look:
            self._question_looks.append(now)
            self.recheck, self._question_pending = True, True
        if self.agent is not None and hasattr(self.agent, "log"):
            self.agent.log({"kind": "sight-question", "reason": reason, "lookAgain": look,
                            "movingShare": round(share, 3) if share is not None else None,
                            "lookAgeS": round(now - self._last_look, 1) if self._last_look else None})
        return reason if look else None

    def motion_words(self) -> str:
        """What moved, as measured when the user last spoke, while fresh (for the voice's notes)."""
        if self.motion is None or time.time() - self.motion[0] > MOTION_FRESH_S:
            return ""
        return self.motion[1]

    def due(self, info, now: float) -> bool:
        """Whether to look now: settled, not too soon, and something changed. An unchanged picture (a still on
        the Stage, a frozen feed) is never looked at again just because time passed: each look is a paid call."""
        if info is None or not info.steady:
            self._unsteady_at = now
            return False
        if now - self._unsteady_at < SETTLE_S:
            return False
        view = self.view
        if view is None or not view.current(info) or self.recheck:
            return now - self._last_look >= CHANGED_GAP_S  # the view changed: look again soon
        if now - self._last_look < MIN_GAP_S:
            return False
        return (now - self._last_look > REFRESH_S and self._picture_changed(info)) or self._labels() != self._looked_labels

    def _picture_changed(self, info) -> bool:
        """The picture changed since the last look (the hub's `changed_at`, quality.Content). Unknown counts as changed."""
        changed_at = getattr(info, "changed_at", 0.0)
        return not changed_at or self._looked_changed_at is None or changed_at != self._looked_changed_at

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(0.25)
            option = getattr(self.hub.settings, "sight", "off")
            if option == "off" or self._busy or not self.wanted() or not self.hub.source.is_fresh():
                continue
            now = time.monotonic()
            if self.due(self.hub.latest_info, now):
                self._busy = True
                self.looks += 1
                try:
                    await self.look(option)
                except Exception:
                    pass
                finally:
                    self._busy = False

    @execution.traced("sight", request="sight")
    async def look(self, option: str | None = None) -> SightView | None:
        """One look at the current view (also called on demand)."""
        hub = self.hub
        option = option or getattr(hub.settings, "sight", "gemini-3.8-flash")
        # The sharpest steady frame of the settle, not just the newest (orchestrator/quality.py).
        info = hub.best_frame(SETTLE_S) if hasattr(hub, "best_frame") else hub.latest_info
        frame = hub.source.get(info.id) if info else None
        if frame is None:
            return None
        self._last_look = time.monotonic()
        self._looked_changed_at = getattr(info, "changed_at", 0.0) or None
        self.recheck = False
        self._content_since_look = False
        self._looked_labels = self._labels()
        bad = unnameable(getattr(info, "quality", ()))
        if bad and getattr(hub.settings, "quality", "off") == "gate":
            # Nothing is named from a view that cannot show it: say what is wrong, and look
            # again as soon as the view is good (no model call until then).
            return self._cannot_tell(info, bad)
        from .capture import CaptureGone, Refused, captures

        store = captures(hub)
        capture = store.get(info.id)
        if capture is None:
            return None
        hold = None
        self._publication += 1
        publication = self._publication
        try:
            hold = store.hold(capture, "analysis", "bytes", "the running look")
            return await self._look_held(option, capture, hold, publication)
        except (CaptureGone, Refused) as exc:
            self.agent.log({"kind": "sight-skip", "frameId": info.id, "why": str(exc)})
            return None
        finally:
            if hold is not None and self._hold is not hold:
                hold.release()

    async def _look_held(self, option, capture, hold, publication):
        hub, info = self.hub, capture.info
        decode = hold.store.hold(capture, "analysis", "look", "prepare the running look")

        def prepare():
            image = capture.bgr()
            crop = fov_crop(info.fov, image.shape[1], image.shape[0])
            return crop, encode_crop(image, crop, max_side=768)[0], encode_crop(image, crop, max_side=384, jpeg_quality=80)[0]

        try:
            future = asyncio.get_running_loop().run_in_executor(None, prepare)
        except BaseException:
            decode.release()
            raise
        # Shield keeps the executor wrapper alive until actual CPU settlement.
        # A canceled caller cannot release room while its decode still runs.
        def decoded(done):
            if not done.cancelled():
                error = done.exception()  # retrieve even when the awaiter was canceled
                pending, seen = ([error] if error is not None else []), set()
                while pending:
                    error = pending.pop()
                    if id(error) in seen:
                        continue
                    seen.add(id(error))
                    # Keep exception/chain/traceback diagnostics, but not pixels
                    # in completed worker/codec frames after their room is freed.
                    traceback.clear_frames(error.__traceback__)
                    pending.extend(e for e in (error.__cause__, error.__context__) if e is not None)
            decode.release()

        future.add_done_callback(decoded)
        crop, jpeg, small = await asyncio.shield(future)
        # Its result is here, so the decode has settled: let it go now, not when the callback's turn comes.
        # A preparation that finished before it was wrapped (CPython 3.13.12+, gh-105836) comes back done:
        # the await does not yield, and the callback would run only after this look had gone on or returned.
        decode.release()
        # The session's specimen is only asked about, never given as a description: a camera
        # moved to something else must not be told it is still looking at a leaf.
        specimen = self.compare_to() if self.compare_to is not None else hub.scene.context.get("specimen")
        named = self.user_named() if self.user_named is not None else ""
        from .scale import Reading

        scale = capture.scale or {}
        values = [scale.get(key) for key in ('umPerPx', 'discPx', 'objective')]
        reading = (Reading(values[0], values[0] * values[1] * 2, values[2], scale.get('measured') is True)
                   if all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in values) else None)
        view = measured(info) if getattr(hub.settings, "quality", "") == "tell" else ""
        dust_line = self.agent.dust_line(info, crop) if hasattr(self.agent, "dust_line") else ""
        view = "\n".join(part for part in (view, dust_line) if part)  # where spots stay put: dust.py, option DUST
        prompt = sight_prompt(specimen, named, reading.words if reading else "", view)
        result = await self.agent.vision(option).ask([jpeg], prompt, max_output_tokens=900)
        if publication != self._publication:
            return None
        data = result.data or {}
        things = []
        for i, t in enumerate([t for t in data.get("things") or [] if isinstance(t, dict)][:8]):
            box = t.get("box_2d")
            if not (isinstance(box, list) and len(box) == 4):
                continue
            try:
                frame_box = gemini_box_to_frame([float(v) for v in box], crop)
            except (TypeError, ValueError):
                continue
            if getattr(hub.settings, "dust", "off") == "filter" and self.agent.on_dust(frame_box):
                # A code rule, a non-default option (DUST "filter"): it has to beat the model told.
                self.agent.log({"kind": "override", "rule": "dust-filter", "model": str(t.get("name") or ""),
                                "did": "left out of the look: it lies on a spot that stays put while the slide moves",
                                "frameId": info.id})
                continue
            conf = t.get("confidence")
            things.append(Seen(f"s{i + 1}", str(t.get("name") or "thing")[:40], str(t.get("where") or "")[:30], frame_box,
                               float(conf) if isinstance(conf, (int, float)) else None))
        radius = info.fov.radius if info.fov is not None else min(info.width, info.height) / 2
        self.view = SightView(info.id, info.epoch, info.dx, info.dy, float(radius), str(data.get("summary") or "")[:400],
                              str(data.get("specimen") or "")[:80], things, str(data.get("focus") or "")[:10],
                              ms=result.latency_ms, frame_at=getattr(info, "wall_at", 0.0),
                              same=data.get("same_as_before") if isinstance(data.get("same_as_before"), bool) and specimen else None)
        previous, self._hold = self._hold, hold
        if previous is not None:
            previous.release()
        self.version += 1
        if self._question_pending:  # the look a user's starting to talk asked for: its note goes out at once
            self.question_view, self._question_pending = self.view, False
        self.agent.log({"kind": "sight", "option": option, "ms": round(result.latency_ms), "summary": self.view.summary,
                        "quality": list(getattr(info, "quality", ())),
                        "specimen": self.view.specimen, "named": named or None,
                        "things": [t.name for t in things], "focus": self.view.focus, "same": self.view.same,
                        "frameId": info.id, "epoch": info.epoch, "asked": specimen or None,
                        "confidences": [t.confidence for t in things],
                        "image": self.agent.keep_image("sight", small, info.id) if hasattr(self.agent, "keep_image") else None})
        for listener in list(self.listeners):
            try:
                listener(self.view)
            except Exception:
                pass
        return self.view
