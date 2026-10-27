"""The one way a result changes what is on screen.

docs/design/2026-10-06-captures-operations-evidence.md, contracts 2 and 3; ADR 0010.

`apply(hub, write, ...)` checks, immediately before writing (no await in between):
- the request's ticket: not cancelled, not superseded for the scope it writes (a clear of that
  kind, a newer request replacing the app's outlines, a tracking reset, a new day);
- the target, when the result changes an existing object: still there and not revised since;
- for a write onto the live view with new geometry, where it goes: `place_outline` verifies the
  result against the current picture (contract 3).

What fails a check changes nothing visible; the reason comes back (`Applied.reason`), for the brain
and the announcer to say. The destinations:
- LIVE: the scene over the live feed. New geometry needs verified placement. An outline whose
  place could not be verified is still kept, but stored as "refinding": the studio draws nothing
  until the hub's continuous check (template re-find) verifies it. Nothing is drawn unverified.
- CAPTURED: something shown on the view it was made on (a close-up of a capture): needs the right
  capture and a valid request and target, not live placement.
- EVIDENCE: recorded only; nothing visible changes.
- WORKSPACE: a UI instance with its own source/lifetime/property fences. The
  request and day still govern its writer; no biological placement is implied.

Writers that are not results (the user's own edits made at once: clear, remove, rename, pin) write
the scene directly and bump generations through it; the hub's re-anchor loop is the one listed
exception among results (it verifies by template itself). tests/test_apply.py lists every direct
writer.
"""
from __future__ import annotations

import asyncio
import copy
from dataclasses import dataclass, field, replace
from typing import Any, Callable

import numpy as np

from . import ticket as tickets

LIVE, CAPTURED, EVIDENCE, WORKSPACE = "live", "captured", "evidence", "workspace"

# How long a view's own pixels count as the check of a result made on it (contract 3, "how long a
# check stays valid"): within this time and this much slide travel, same epoch and geometry, a
# result made on the view is where the view says. Past either, it is checked again against the
# current picture before it is drawn live.
CHECK_VALID_S = 1.5
CHECK_TRAVEL_PX = 12.0
GEOMETRY_SHARE = 0.005  # disc radius within 0.5% (the phone's own refocusing changes it 1-2.4%)
RECHECK_SEARCH_PX = 40  # as the hub's same-epoch re-anchoring
RECHECK_SCORE = 0.6
RECHECK_MAX_SHIFT_PX = 10.0


@dataclass
class Applied:
    ok: bool
    reason: str = ""
    value: Any = None
    placement: "Placement | None" = None


@dataclass
class Placement:
    """Where a result made on `capture` goes on the live view now."""
    verified: bool
    how: str  # "same view", "rechecked", or why it is unverified
    anchor: Any  # the frame facts it is anchored to: always its capture
    polygons: list = field(default_factory=list)  # in the anchor's coordinates
    template: Any = None
    template_origin: tuple[int, int] = (0, 0)
    checked_view: Any = None  # immutable facts of the view whose pixels verified it (not its anchor)
    checked_objective: Any = None  # optics when that check was started


def blocked(hub, ticket, scope: str | None, target: tuple[str, int] | None = None) -> str | None:
    """Why a result may not be written now, or None: the ticket's scope and the target."""
    if ticket is not None and scope is not None:
        why = ticket.superseded(scope)
        if why:
            return why
    if target is not None:
        oid, revision = target
        obj = hub.scene.objects.get(oid)
        if obj is None:
            return "the outline it was for has been removed"
        if revision is not None and getattr(obj, "revision", 0) != revision:
            return "the outline it was for has changed since"
    return None


async def apply(hub, write: Callable[["Placement | None"], Any], *, scope: str, destination: str = LIVE,
                capture=None, ticket=..., target: tuple[str, int] | None = None,
                place: Callable[[], Any] | None = None) -> Applied:
    """Check, then write. `write(placement)` runs synchronously right after the checks; `place`
    (an async callable returning a Placement) runs first for live geometry."""
    if not scope:
        raise ValueError("every result write names the scope it changes (ticket.SCOPES)")
    if destination not in (LIVE, CAPTURED, EVIDENCE, WORKSPACE):
        raise ValueError("result destination must be live, captured, evidence or workspace")
    ticket = tickets.current() if ticket is ... else ticket
    placement = None
    if destination == LIVE and place is not None:
        placement = await place()
    elif destination == CAPTURED and capture is None:
        return Applied(False, "there is no captured view to show it on")
    why = blocked(hub, ticket, scope, target)
    if why:
        return Applied(False, why, placement=placement)
    if destination == EVIDENCE:
        return Applied(True, "recorded", value=None, placement=placement)
    if destination == LIVE and placement is not None:
        # The placement worker awaited with a frozen view. Revalidate its certificate against the
        # view now, synchronously with the write: a new frame may have expired it during that await.
        placement = [current_placement(hub, p) for p in placement] if isinstance(placement, list) \
            else current_placement(hub, placement)
    return Applied(True, "", value=write(placement), placement=placement)


# --- placement of an outline (contract 3, for outlines) ----------------------------------------
def objective_now(hub):
    scale = getattr(hub, "scale", None)
    return getattr(scale, "objective", None) if scale is not None else None


def preconditions(capture, now, objective=None) -> str | None:
    """Why `capture` and the view `now` cannot be related by the slide's move alone, or None.
    `objective`: the objective in use now."""
    if now is None:
        return "there is no live view now"
    if getattr(capture, "epoch", None) != getattr(now, "epoch", None):
        return "the slide's motion lost continuity since the view it was made on"
    return geometry_reason(capture, now, objective)


def geometry_reason(capture, now, objective=None) -> str | None:
    """Why two views cannot share pixel geometry, independently of a known epoch translation."""
    if (getattr(capture, "width", None), getattr(capture, "height", None)) != (getattr(now, "width", None),
                                                                               getattr(now, "height", None)):
        return "the camera's picture size changed since"
    a = float(getattr(capture, "disc", 0.0) or 0.0)
    b = float(getattr(now, "disc", 0.0) or 0.0)
    if a > 0 and b > 0 and abs(a - b) / max(a, b) > GEOMETRY_SHARE:
        return "the eyepiece's scale changed since (the lens refocused or zoomed)"
    scale = getattr(capture, "scale", None)
    then = scale.get("objective") if isinstance(scale, dict) else None
    if then is not None and objective is not None and str(then) != str(objective):
        return "the objective changed since"
    return None


def continuity(hub, capture, now) -> str | None:
    """Why the slide's move from `capture` to `now` is not known frame by frame, or None: the motion
    tracker must have registered the capture's frame and every frame since (an unregistered frame
    only carries the last offset over, so the view may already be elsewhere; contract 3's "tracked
    continuity"). Frames whose facts are no longer kept (30 s) cannot show it either. A stand-in hub
    with no frame facts at all is not checked."""
    infos = getattr(hub, "infos", None)
    if infos is None or not hasattr(infos, "get") or now is None:
        return None
    if not getattr(capture, "tracked", True):
        return "the slide's motion was not registered on the view it was made on"
    for fid in range(int(capture.id) + 1, int(now.id) + 1):
        info = infos.get(fid)
        if info is None:
            return "the slide's motion since the view it was made on is no longer known"
        if not getattr(info, "tracked", True):
            return "the slide's motion was not registered on every frame since the view it was made on"
    return None


def within_window(capture, now, hub=None) -> bool:
    """The capture's own pixels still count as the check (time, slide travel, observed change)."""
    if now is None:
        return False
    if float(getattr(now, "wall_at", 0.0) or 0.0) - float(getattr(capture, "wall_at", 0.0) or 0.0) > CHECK_VALID_S:
        return False
    travel = float(np.hypot(float(now.dx) - float(capture.dx), float(now.dy) - float(capture.dy)))
    if travel > CHECK_TRAVEL_PX:
        return False
    change = getattr(hub, "last_change", None) if hub is not None else None
    if change and change[0] == "content" and change[1] > float(getattr(capture, "wall_at", 0.0) or 0.0):
        return False  # something new appeared in the view since: check again
    return True


def current_placement(hub, placement: Placement) -> Placement:
    """A verified placement stays verified only while its check still governs the live view.
    Expired results retain their capture pixels/template for historical and re-finding callers;
    callers requiring live placement can refuse before allocating an outline or linking identity.
    """
    if not placement.verified:
        return placement
    now = getattr(hub, "latest_info", None)
    checked = placement.checked_view if placement.checked_view is not None else placement.anchor
    objective = objective_now(hub)
    why = preconditions(placement.anchor, now, objective)
    if not why and getattr(now, "tracked", True) is False:
        why = "the slide's motion is not registered on the current view"
    if not why and placement.checked_objective is not None and objective is not None \
            and str(placement.checked_objective) != str(objective):
        why = "the objective changed since its place was checked"
    if not why:
        why = preconditions(checked, now, objective) or continuity(hub, checked, now)
    if not why and not within_window(checked, now, hub):
        why = "its placement check expired before it could be written (time, slide travel or new content)"
    return replace(placement, verified=False, how=why) if why else placement


def _bbox(polygons) -> tuple[float, float, float, float]:
    pts = np.array([p for poly in polygons for p in poly], dtype=float)
    return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())


def _place_all(capture, gray, now, now_gray, outlines, same_view: bool, why: str | None) -> list[Placement]:
    """Placements for each outline (a list of polygons, capture px); runs in a worker thread."""
    from .scene import capture_template, match_at, refind

    out = []
    for polygons in outlines:
        template, origin = (None, (0, 0))
        if gray is not None and polygons:
            made = capture_template(gray, _bbox(polygons))
            if made is not None:
                template, origin = made
        if why:
            out.append(Placement(False, why, capture, polygons, template, origin))
            continue
        if same_view:
            out.append(Placement(True, "same view", capture, polygons, template, origin))
            continue
        if template is None or now_gray is None:
            out.append(Placement(False, "too small to check against the current picture" if template is None
                                 else "the current picture could not be read", capture, polygons, template, origin))
            continue
        shift = (float(now.dx) - float(capture.dx), float(now.dy) - float(capture.dy))
        predicted = (origin[0] + shift[0], origin[1] + shift[1])
        found = refind(now_gray, template, predicted, RECHECK_SEARCH_PX, RECHECK_SCORE)
        if found is None or float(np.hypot(found[0], found[1])) > RECHECK_MAX_SHIFT_PX:
            # No unique nudge (look-alikes nearby, or nothing): it is verified only if its pixels
            # match where the slide's move puts it.
            if match_at(now_gray, template, predicted) < RECHECK_SCORE:
                out.append(Placement(False, "its pixels did not match the current picture where the slide's move puts it",
                                     capture, polygons, template, origin))
                continue
        # Verified: it stays anchored to its capture, and the slide's offsets carry it as they carry
        # every outline. The match only gates drawing; folding its nudge into the polygons would bake
        # the newest frame's offset error (a frame mid-pan is off by several px) into the outline for
        # good (arena scenario: a late arm drawn 5 px off for its whole life).
        out.append(Placement(True, "rechecked", capture, polygons, template, origin))
    return out


async def place_outlines(hub, capture, outlines: list, cls: str = "analysis") -> list[Placement]:
    """Where outlines made on `capture` go now (contract 3): verified on the same view within the
    check's window; else each is checked again against the current picture by its own pixels;
    when that fails or cannot be done, unverified (kept, not drawn, until the hub's continuous
    check finds it). Both views are held while they are decoded and compared (invariant 3), in
    allowance `cls` (a click's: "interactive")."""
    from contextlib import ExitStack

    from .capture import Capture, Refused, captures

    store = captures(hub)
    now = copy.copy(getattr(hub, "latest_info", None))
    objective = objective_now(hub)
    why = preconditions(capture, now, objective) or continuity(hub, capture, now)
    same_view = not why and (now.id == capture.id or within_window(capture, now, hub))
    checked_view = capture if same_view else now
    latest = None if why or same_view else store.get(now.id)
    with ExitStack() as held:
        readable = []
        try:
            for view in (capture, latest):
                if isinstance(view, Capture) and view.jpeg:
                    held.enter_context(store.hold(view, cls, "hold", "the placement check"))
                    readable.append(view)
        except Refused as exc:
            # No room to check it now: kept unverified (not drawn) until the hub's own check finds it.
            why = f"its place could not be checked now ({exc})"

        def work():
            # The capture's own pixels make the template even when it cannot be placed now: the hub's
            # continuous check finds it by them later.
            gray = capture.gray() if capture in readable else None
            now_gray = latest.gray() if latest in readable and not why else None
            return _place_all(capture, gray, now, now_gray, outlines, same_view, why)

        placements = await asyncio.get_running_loop().run_in_executor(None, work)
        return [replace(p, checked_view=checked_view, checked_objective=objective) for p in placements]


async def place_outline(hub, capture, polygons, cls: str = "analysis") -> Placement:
    return (await place_outlines(hub, capture, [polygons], cls))[0]


def next_for(reason: str | None, again: str) -> str | None:
    """What to do after a write was refused (the partner §1c): nothing when the request was called off or
    superseded (a newer request, a clear, a new day or the user's own change stands), otherwise `again`."""
    text = str(reason or "")
    if text.startswith(("the request was called off", "superseded")):
        return None
    return again
