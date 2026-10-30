"""Captures: the view a piece of work is about, held for as long as the work needs it.

docs/design/2026-10-06-captures-operations-evidence.md, contract 1; ADR 0010.

The frame ring keeps a frame's pixels for 4 s, and a slow tool used to look its frame up again by
id after waiting (2026-10-06: "Find failed: 'NoneType' object has no attribute 'jpeg'", the cell
finder having taken 6-8 s). A `Capture` is taken once, when the work starts: the frame's bytes and
a copy of its facts (size, eyepiece disc, epoch, offset, the bridge's `(boot, seq)`) and the scale
as it was then. Slow code keeps the Capture and never asks for the frame by id again.

Memory is bounded at its peak, by reservation before allocation:
- every holder reserves the worst case of what it will keep resident (the JPEG, decoded images, a
  label map at w x h x 4 bytes, masks, temporary copies), computed from the capture's dimensions,
  in one of three allowances (interactive, analysis, unsaved evidence);
- a reservation that would go over its allowance is refused with the reason, before anything is
  allocated, and a holder that needs more asks for it first (`Reservation.grow`);
- nothing held is ever evicted: what is admitted fits.
GPU and worker-process memory are outside this bound (worker classes and queue limits govern them).

A Capture is one per frame id (`Captures.take` returns the one already taken). It stands in for the
frame's facts (`capture.id`, `.width`, `.fov`, `.epoch`...: every FrameInfo field), so code written
for a FrameInfo takes a Capture unchanged.
"""
from __future__ import annotations

import copy
import os
import sys
import threading
import time
import weakref
from collections import Counter, OrderedDict
from contextlib import contextmanager

import cv2
import numpy as np

from .brain.registry import ToolFailed

MB = 1024 * 1024
# The bound and its allowances. Waste is fine and loss is not: these are generous for one Mac and
# small beside its memory. At 1920x1080 a click reserves about 13 MB while it is outlined (its view
# held by the request, plus the outline's decode), a cell run about 47 MB.
# This run of the orchestrator: frame ids and motion epochs start again with every start, so a view
# saved in another run is never taken for the live frame that has its id now (Captures.read_back).
RUN = f"{os.getpid()}-{time.time():.0f}"

BOUND_MB = 376
# "research": a researcher's own operations (research/hands.py) and an experiment's model runs, apart from the user's:
# a run waiting behind the live view's work never takes the room the user's own analyses need (review of #173). One
# fits at 1080p.
# "kept": the tables of saved analyses made in the background (that "research" allowance's runs), resident for as long
# as their population is loaded. They were split from the run's own hold and stayed in "research", so ten runs of one
# experiment filled the room the next run needed (will-experiment, 29 Oct: "a researcher's analyses fill their
# allowance (24 of 24 MB)", Experiment 2 at frame 4); a table is about 1.5-3 MB at 160 cells.
ALLOWANCES_MB = {"interactive": 64, "analysis": 160, "unsaved": 32, "research": 24, "kept": 96}
CLASSES = tuple(ALLOWANCES_MB)
TEMPORARY_SHARE = 0.25  # copies made while working (crops, re-encodes) on top of what is kept
# Which decoded images each kind of hold reserves room for (footprint below). A Capture keeps a
# decode only while a hold covers it, and drops it when the last such hold is let go; a decode no
# hold covers is made for the caller alone, never kept, and counted in UNCOUNTED by where it was
# asked for (health's `captures.uncountedDecodes`, which the long-session scenario requires to stay 0).
COVERS = {"hold": {"gray"}, "outline": {"gray", "bgr"}, "look": {"bgr"}, "cells": {"gray", "bgr"}, "evidence": set(),
          "bytes": set()}
UNCOUNTED: Counter = Counter()
# Frames whose scale is remembered once it is first asked for (Captures.scale_of): far more than the frame ring's
# ~4 s keeps, so a frame still there is never measured in a scale decided after it was first asked.
SCALED_FRAMES = 2048


class Refused(ToolFailed):
    """Work that would go over its memory allowance; the message says why, for the user."""


class CaptureGone(ToolFailed):
    """The view a piece of work needed is no longer held anywhere."""


def footprint(width: int, height: int, jpeg_bytes: int, kind: str) -> int:
    """The worst case, in bytes, that a holder of a `width` x `height` view keeps resident for
    `kind` of work, temporary copies included (contract 1)."""
    px = int(width) * int(height)
    gray, bgr, labels = px, 3 * px, 4 * px
    kept = {
        "hold": jpeg_bytes + gray,  # a request keeping its view: bytes, plus a decode for placing
        "outline": jpeg_bytes + gray + bgr,  # outlining and naming: the template, the crops
        "look": jpeg_bytes + bgr,  # a vision look: the picture it is shown
        "cells": jpeg_bytes + gray + bgr + labels + labels,  # a label map, and masks as large again
        "evidence": jpeg_bytes + labels + labels,  # kept evidence: its view, label map and masks
        "bytes": jpeg_bytes,  # the view's bytes only, kept to send again (Molmo's read-ahead)
        # (An enhanced picture reserves its own measured peak, enhance.Plan.need, by how it is made.)
        # Linking two views (operations' `link`): per view a decode and a label map, cut to the overlap at once
        # (float32 and int32), then the model's own normalised and zeroed copies (about 16 bytes a pixel); the
        # partner's tracemalloc measure of the first version held 34 bytes a pixel (M3.5's re-check).
        "link": 2 * jpeg_bytes + 2 * (gray + 2 * px) + 16 * px + 16 * px,
    }.get(kind, jpeg_bytes + gray + bgr)
    return int(kept * (1 + TEMPORARY_SHARE))


class Reservation:
    """Bytes reserved in one allowance until released. Its holder may grow it (asking first) and
    settle it to what it really keeps."""

    def __init__(self, memory: "Memory", cls: str, nbytes: int, why: str) -> None:
        self.memory, self.cls, self.nbytes, self.why = memory, cls, int(nbytes), why
        self.released = False

    def grow(self, extra: int, why: str = "") -> None:
        """Take `extra` more bytes before allocating them; Refused when the allowance cannot."""
        if self.released:
            raise Refused("this work has already finished", next=None)  # no next: the app's own bookkeeping; nothing for the user to do
        self.memory._admit(self.cls, int(extra), why or self.why)
        self.nbytes += int(extra)

    def settle(self, actual: int) -> None:
        """Drop the reservation to what is really kept (never grows it)."""
        if self.released or actual >= self.nbytes:
            return
        self.memory._free(self.cls, self.nbytes - int(actual))
        self.nbytes = int(actual)

    def split(self, nbytes: int, why: str) -> "Reservation":
        """Transfer already admitted bytes to another lifetime without freeing or admitting them again."""
        if self.released or not 0 <= nbytes <= self.nbytes:
            raise ValueError("only held capacity can be transferred")
        self.nbytes -= nbytes
        return Reservation(self.memory, self.cls, nbytes, why)

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.memory._free(self.cls, self.nbytes)
            self.nbytes = 0

    def __enter__(self) -> "Reservation":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class Memory:
    """The resident-analysis-memory bound and its allowances (contract 1)."""

    def __init__(self, allowances_mb: dict[str, float] | None = None) -> None:
        allowances = dict(ALLOWANCES_MB if allowances_mb is None else allowances_mb)
        self.allowances = {cls: int(float(allowances.get(cls, 0)) * MB) for cls in CLASSES}
        self.reserved = {cls: 0 for cls in CLASSES}
        self.peak = {cls: 0 for cls in CLASSES}
        self.refused = {cls: 0 for cls in CLASSES}
        self.last_refusal: dict[str, str] = {}
        self._lock = threading.Lock()  # worker threads reserve too (choosing the sharpest view, crops)

    @classmethod
    def from_env(cls) -> "Memory":
        """MICROMIND_HELD_MB scales the whole bound (the allowances keep their shares)."""
        try:
            bound = float(os.environ.get("MICROMIND_HELD_MB") or BOUND_MB)
        except ValueError:
            bound = float(BOUND_MB)
        scale = bound / sum(ALLOWANCES_MB.values())
        return cls({k: v * scale for k, v in ALLOWANCES_MB.items()})

    @property
    def bound(self) -> int:
        return sum(self.allowances.values())

    def reserve(self, cls: str, nbytes: int, why: str) -> Reservation:
        """Reserve before allocating; Refused (with the reason) when it does not fit."""
        self._admit(cls, int(nbytes), why)
        return Reservation(self, cls, int(nbytes), why)

    def _admit(self, cls: str, nbytes: int, why: str) -> None:
        if cls not in self.allowances:
            raise ValueError(f"no memory allowance called {cls!r}")
        with self._lock:
            self._admit_locked(cls, nbytes, why)

    def _admit_locked(self, cls: str, nbytes: int, why: str) -> None:
        if self.reserved[cls] + nbytes > self.allowances[cls]:
            self.refused[cls] += 1
            used, allowed = self.reserved[cls] / MB, self.allowances[cls] / MB
            reason, next_step = {
                "interactive": (f"too many outlines are still being made ({used:.0f} of {allowed:.0f} MB held)",
                                "wait a moment, then click again"),
                "unsaved": (f"analyses not yet saved fill their allowance ({used:.0f} of {allowed:.0f} MB)",
                            "wait a moment for saving, or clear earlier analyses"),
                "research": (f"a researcher's analyses fill their allowance ({used:.0f} of {allowed:.0f} MB)",
                             "carry on without it, and say what could not be measured"),
                "kept": (f"the tables of analyses made in the background fill their allowance ({used:.0f} of "
                         f"{allowed:.0f} MB)", "carry on without it, and say what could not be measured"),
            }.get(cls, (f"held views are full: {used:.0f} of {allowed:.0f} MB", "clear earlier analyses, or wait for them to finish"))
            self.last_refusal[cls] = f"{why}: {reason}"
            raise Refused(reason, next=next_step)
        self.reserved[cls] += nbytes
        self.peak[cls] = max(self.peak[cls], self.reserved[cls])

    def _free(self, cls: str, nbytes: int) -> None:
        with self._lock:
            self.reserved[cls] = max(0, self.reserved[cls] - int(nbytes))

    def wire(self) -> dict:
        return {"boundMb": round(self.bound / MB, 1),
                "classes": {cls: {"allowanceMb": round(self.allowances[cls] / MB, 1),
                                  "reservedMb": round(self.reserved[cls] / MB, 2),
                                  "peakMb": round(self.peak[cls] / MB, 2), "refused": self.refused[cls]}
                            for cls in CLASSES}}


class Capture:
    """One view, as it was when the work started: its bytes, its facts and its scale. It answers
    every FrameInfo attribute (id, width, height, fov, epoch, dx, dy, disc, seq, boot, ...)."""

    __slots__ = ("id", "jpeg", "info", "scale", "at", "native", "source_image", "_gray", "_bgr", "_covered", "_frozen", "__weakref__")

    def __init__(self, frame_id: int, jpeg: bytes, info, scale: dict | None = None,
                 native: bytes | None = None, source_image: dict | None = None) -> None:
        self.id = int(frame_id)
        self.jpeg = jpeg
        self.info = copy.copy(info)  # later changes to the hub's FrameInfo never reach a capture
        self.scale = scale
        self.native, self.source_image = native, copy.deepcopy(source_image)
        self.at = float(getattr(info, "wall_at", 0.0) or time.time())
        self._gray = None
        self._bgr = None
        self._covered: Counter = Counter()  # decoded kinds ("gray", "bgr") -> holds reserving room for them
        self._frozen = True

    def __setattr__(self, name, value) -> None:
        # A view as it was: its bytes and facts never change once taken (only its own caches do).
        if not name.startswith("_") and getattr(self, "_frozen", False):
            raise AttributeError(f"a Capture is the view as it was; its {name!r} cannot change")
        object.__setattr__(self, name, value)

    def __getattr__(self, name):  # FrameInfo's fields, so a Capture stands in for one
        if name in Capture.__slots__:
            raise AttributeError(name)
        return getattr(self.info, name)

    def __repr__(self) -> str:
        return f"Capture({self.id}, epoch={getattr(self.info, 'epoch', None)}, {len(self.jpeg or b'')} bytes)"

    def gray(self) -> np.ndarray | None:
        return self._decoded("_gray", "gray", cv2.IMREAD_GRAYSCALE)

    def bgr(self) -> np.ndarray | None:
        return self._decoded("_bgr", "bgr", cv2.IMREAD_COLOR)

    @property
    def analysis_bytes(self) -> bytes:
        return self.native if self.native is not None else self.jpeg

    def _decoded(self, slot: str, kind: str, flag: int) -> np.ndarray | None:
        """The decode, kept while a hold reserves room for it (COVERS); otherwise made for this
        caller only and counted as uncounted where it was asked for."""
        kept = object.__getattribute__(self, slot)
        if kept is not None or not self.jpeg:
            return kept
        image = cv2.imdecode(np.frombuffer(self.analysis_bytes, np.uint8),
                             flag | cv2.IMREAD_IGNORE_ORIENTATION if self.native is not None else flag)
        if self._covered[kind] > 0:
            object.__setattr__(self, slot, image)
        else:
            caller = sys._getframe(2)
            UNCOUNTED[f"{os.path.basename(caller.f_code.co_filename)}:{caller.f_code.co_name}:{kind}"] += 1
        return image

    def _cover(self, kind: str, by: int) -> None:
        """A hold of `kind` taken (by=1) or let go (by=-1): decodes no hold covers any more are dropped."""
        for decoded in COVERS.get(kind, {"gray", "bgr"}):
            self._covered[decoded] = max(0, self._covered[decoded] + by)
            if self._covered[decoded] == 0:
                object.__setattr__(self, f"_{decoded}", None)

    def footprint(self, kind: str) -> int:
        w = int(getattr(self.info, "width", 0) or 0)
        h = int(getattr(self.info, "height", 0) or 0)
        return footprint(w, h, len(self.jpeg or b"") + len(self.native or b""), kind)

    @property
    def resident(self) -> int:
        """Bytes this capture keeps now (its JPEG and whatever was decoded)."""
        return len(self.jpeg or b"") + len(self.native or b"") + sum(a.nbytes for a in (self._gray, self._bgr) if a is not None)


class Hold:
    """A holder's claim on a capture: the capture stays findable by id, and its reservation is
    counted, until released."""

    def __init__(self, store: "Captures", capture: Capture, reservation: Reservation, owner: str, kind: str = "hold") -> None:
        self.store, self.capture, self.reservation, self.owner, self.kind = store, capture, reservation, owner, kind
        self.released = False

    def release(self) -> None:
        if not self.released:
            self.released = True
            self.reservation.release()
            self.store._let_go(self)

    def __enter__(self) -> "Hold":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class Captures:
    """The hub's captures: one per frame id, findable by id while anyone holds it."""

    def __init__(self, hub, memory: Memory | None = None) -> None:
        self.hub = hub
        self.memory = memory or Memory.from_env()
        self._live: weakref.WeakValueDictionary[int, Capture] = weakref.WeakValueDictionary()
        self._read_back: weakref.WeakValueDictionary[tuple, Capture] = weakref.WeakValueDictionary()
        self._saved_ids = 0  # views read back from another run: -1, -2...
        self._held: dict[int, Capture] = {}
        self._holds: dict[int, int] = {}
        self._scales: OrderedDict[int, dict] = OrderedDict()  # frame id -> its scale, decided once (scale_of)
        self._lock = threading.RLock()  # taken from worker threads too (adopt, hold)

    def take(self, frame_id: int | None = None) -> Capture:
        """The capture of `frame_id` (the latest frame when None): the one already taken, or a new
        one while the frame is still in the ring. CaptureGone when neither."""
        if frame_id is None:
            info = getattr(self.hub, "latest_info", None)
            if info is None:
                from .frames import no_picture
                why, next_step = no_picture(self.hub, "there is no picture from the camera yet",
                                             "check the camera is connected and the view is live, then ask again")
                raise CaptureGone(why, next=next_step)
            frame_id = info.id
        found = self.get(frame_id)
        if found is None:
            raise CaptureGone("that view is no longer held: the slide may have moved on; try again on the view now", next=None)  # no next: the message says what to do
        return found

    def get(self, frame_id) -> Capture | None:
        """The capture of `frame_id` if one is held or the frame is still in the ring, else None.
        `frame_id` may be a frame's facts (anything with an `id`), or a Capture (returned as is)."""
        if frame_id is None:
            return None
        if isinstance(frame_id, Capture):
            return frame_id
        if not isinstance(frame_id, int) and hasattr(frame_id, "id"):
            frame_id = frame_id.id
        frame_id = int(frame_id)
        with self._lock:
            known = self._held.get(frame_id) or self._live.get(frame_id)
            if known is not None:
                return known
            source = getattr(self.hub, "source", None)
            frame = source.get(frame_id) if source is not None else None
            infos = getattr(self.hub, "infos", None) or {}
            info = infos.get(frame_id) if hasattr(infos, "get") else None
            if frame is None or info is None:
                return None
            capture = Capture(frame_id, getattr(frame, "jpeg", b"") or b"", info, self.scale_of(info))
            self._live[frame_id] = capture
            return capture

    def adopt(self, info, jpeg: bytes, scale: dict | None = None, *, native: bytes | None = None,
              source_image: dict | None = None) -> Capture:
        """The capture of the frame `info` describes, made from `jpeg` (bytes a worker thread already
        read with these facts, or a saved view read back with its own `scale`) unless one was already
        taken: one Capture per frame id, whoever reads it."""
        with self._lock:
            known = self._held.get(int(info.id)) or self._live.get(int(info.id))
            if known is not None:
                return known
            capture = Capture(int(info.id), jpeg or b"", info, scale if scale is not None else self.scale_of(info),
                              native, source_image)
            self._live[capture.id] = capture
            return capture

    def original(self, info, jpeg: bytes, scale: dict, native: bytes, source_image: dict) -> Capture:
        """An imported image owns a separate grid/id; it never impersonates a live camera frame."""
        with self._lock:
            self._saved_ids -= 1
            info.id = self._saved_ids
            capture = Capture(info.id, jpeg, info, scale, native, source_image)
            self._live[capture.id] = capture
            return capture

    def read_back(self, info, jpeg: bytes, scale: dict | None, run: str | None, *, native: bytes | None = None,
                  source_image: dict | None = None) -> Capture:
        """A saved view read back (evidence whose pixels left memory once saved, or after a restart).
        Made in this run (`run` is RUN), it is that frame: its live capture if one is still taken, else
        adopted under its own id. Made in another run, its id and epoch mean nothing now (frame ids
        start at 1 on every start): it gets an id no live frame has (negative) and no epoch, so
        nothing places it on the live view, and it is never swapped for the live frame with its id."""
        if run == RUN:
            return self.adopt(info, jpeg, scale, native=native, source_image=source_image)
        key = (run, getattr(info, "boot", None), getattr(info, "seq", None), int(info.id))
        with self._lock:
            known = self._read_back.get(key)
            if known is not None:
                return known
            self._saved_ids -= 1
            facts = copy.copy(info)
            facts.id, facts.epoch, facts.tracked = self._saved_ids, None, False
            capture = Capture(self._saved_ids, jpeg or b"", facts, scale, native, source_image)
            self._live[capture.id] = capture
            self._read_back[key] = capture
            return capture

    def scale_of(self, info) -> dict | None:
        """One scale per frame, from its intake optics and measured geometry. Legacy/synthetic info without
        intake facts retains first-reader behavior; saved readback carries its supplied scale through adopt.
        Later readers receive a copy, also after the objective changed."""
        fid = int(info.id)
        with self._lock:
            known = self._scales.get(fid)
            if known is None:
                known = scale_now(self.hub, info)
                if known is None:  # no scale to decide from yet: nothing is fixed
                    return None
                self._scales[fid] = known
                while len(self._scales) > SCALED_FRAMES:
                    self._scales.popitem(last=False)
            return copy.deepcopy(known)

    def best(self, window_s: float = 1.0) -> Capture:
        """The best view of the last `window_s` seconds (Hub.best_frame), taken."""
        pick = getattr(self.hub, "best_frame", None)
        info = pick(window_s) if pick is not None else None
        return self.take(info.id if info is not None else None)

    def hold(self, capture: Capture, cls: str, kind: str, owner: str) -> Hold:
        """Reserve what holding `capture` for `kind` of work costs, in allowance `cls`, and keep it
        findable by id until the hold is released. Refused when it does not fit."""
        with self._lock:
            if self._held.get(capture.id, capture) is not capture or self._live.get(capture.id, capture) is not capture:
                # One Capture per frame id: a second object for the same frame would keep its own bytes
                # and decodes uncounted beside the first (scaffolding review, M1.1 item 7).
                raise ValueError(f"view {capture.id} was taken twice; take it through the capture store")
            reservation = self.memory.reserve(cls, capture.footprint(kind), f"{owner} holding view {capture.id}")
            self._held[capture.id] = capture
            self._live[capture.id] = capture
            self._holds[capture.id] = self._holds.get(capture.id, 0) + 1
            capture._cover(kind, +1)
            return Hold(self, capture, reservation, owner, kind)

    def _let_go(self, hold: Hold) -> None:
        with self._lock:
            cid = hold.capture.id
            hold.capture._cover(hold.kind, -1)
            left = self._holds.get(cid, 1) - 1
            if left <= 0:
                self._holds.pop(cid, None)
                self._held.pop(cid, None)
            else:
                self._holds[cid] = left

    def held(self) -> list[Capture]:
        with self._lock:  # holds are let go from other threads (live 2026-10-10: /health raised "dictionary changed size")
            return list(self._held.values())

    def wire(self) -> dict:
        held = self.held()
        return {"held": len(held), "heldMb": round(sum(c.resident for c in held) / MB, 2),
                "uncountedDecodes": dict(UNCOUNTED), **self.memory.wire()}


def captures(hub) -> Captures:
    """The hub's capture store (made on first use, so a test's stand-in hub gets one too)."""
    store = getattr(hub, "captures", None)
    if not isinstance(store, Captures):
        store = Captures(hub)
        try:
            hub.captures = store
        except AttributeError:  # a frozen stand-in: a store for this call only
            pass
    return store


@contextmanager
def decoding(hub, view, kind: str = "look", cls: str = "analysis", owner: str = "a decode"):
    """A short hold on `view` (a Capture, a frame's facts or its id) while it is decoded and used: the
    decode is counted in `cls` and dropped when the block ends. Refused when it does not fit,
    CaptureGone when the view is no longer held or in the ring. Safe in a worker thread."""
    store = captures(hub)
    capture = store.get(view)
    if capture is None:
        raise CaptureGone("that view is no longer held: the slide may have moved on; try again on the view now", next=None)  # no next: the message says what to do
    with store.hold(capture, cls, kind, owner):
        yield capture


@contextmanager
def reserving(hub, width: int, height: int, kind: str = "look", owner: str = "a decode", cls: str = "analysis"):
    """Room for decoding camera bytes that are not a held view (the clip's frames, a focus sweep's,
    candidates scored one at a time): reserved before, released after. Refused when it does not fit."""
    with captures(hub).memory.reserve(cls, footprint(width, height, 0, kind), owner) as reservation:
        yield reservation


def scale_at(hub, info) -> dict | None:
    """The scale a live frame is measured in (contract 8's scale_id), shared by every reader of it."""
    if info is None:
        return None
    try:
        int(info.id)
    except (AttributeError, TypeError, ValueError):
        return scale_now(hub, info)
    return captures(hub).scale_of(info)


def scale_now(hub, info) -> dict | None:
    """This view's intake optics and geometry; current knobs only for legacy info without intake facts."""
    if info is None:
        return None
    frozen = getattr(info, "optics_json", None)
    if frozen is not None:
        import json
        from .scale import Scale
        try:
            facts = json.loads(frozen)
        except (TypeError, ValueError):
            return None  # a broken intake snapshot cannot borrow later optics
        calibrations = facts.get("calibrated") or {}
        read = lambda radius: Scale.reading_from(facts, radius)
    else:
        scale = getattr(hub, "scale", None)
        if scale is None:
            return None
        try:
            facts = scale.facts()
        except Exception:
            return None
        calibrations = getattr(scale, "calibrations", {}) or {}
        read = scale.reading
    objective = (facts.get("objective") or {}).get("value")
    disc = float(getattr(info, "disc", 0.0) or 0.0) or float(getattr(getattr(info, "fov", None), "radius", 0.0) or 0.0)
    out = {"objective": objective, "objectiveSource": (facts.get("objective") or {}).get("source"),
           "fieldNumber": (facts.get("fieldNumber") or {}).get("value"),
           "fieldNumberSource": (facts.get("fieldNumber") or {}).get("source"),
           "calibratedAt": (calibrations.get(str(objective)) or {}).get("at") if objective is not None else None,
           "frame": [int(getattr(info, "width", 0) or 0), int(getattr(info, "height", 0) or 0)],
           "discPx": round(disc, 2)}
    if frozen is not None:
        out["optics"] = facts  # objective/FN setters and calibration length/time provenance
    try:
        reading = read(disc) if disc > 0 else None
    except Exception:
        reading = None
    if reading is not None:
        out.update(umPerPx=reading.um_per_px, measured=bool(reading.measured))
    return out
