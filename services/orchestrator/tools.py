"""Actions on the scene. Clicks, chat, voice agents and workers all call these, so every
caller gets the same behaviour."""
from __future__ import annotations

import asyncio
import os
import time
from types import SimpleNamespace

import numpy as np

from .brain.registry import ToolFailed
from .capture import Capture, captures
from .contract import CLICK_RADIUS
from .hub import Hub
from .scene import SceneObject, anchor_geometry
from . import execution
from . import ticket as tickets


class ToolError(ToolFailed):
    """Something on the scene could not be done; the message is the user's (one error class:
    a tool that hits it fails with this message, brain/registry.py)."""


def snap_to_candidate(hub: Hub, frame_id: int, x: float, y: float, max_px: float = 14.0):
    """The nearest candidate spot to a click, if the click is on or right next to it."""
    best, best_d = None, None
    for cand in hub.candidates_in(frame_id):
        d = float(np.hypot(cand.x - x, cand.y - y))
        if d <= max(max_px, cand.radius * 1.6) and (best_d is None or d < best_d):
            best, best_d = cand, d
    return best


ORIGINS_BY_SOURCE = {"click": "click", "arena": "arena"}


async def outline_at(
    hub: Hub,
    view: "int | Capture",
    x: float,
    y: float,
    *,
    box: tuple[float, float, float, float] | None = None,
    source: str = "click",
    label: str = "",
    status: str = "outline",
    snap: bool = True,
    model: str | None = None,
    policy: str | None = None,
    origin: str | None = None,
    trial: str | None = None,
    arm: str | None = None,
    confidence: float | None = None,
    naming: dict | None = None,
) -> SceneObject:
    """Outline the thing at (x, y) [or inside `box`] on `view` (a Capture, or the id of the frame
    the user saw) and add it to the scene. A frame id no longer held is mapped onto the latest
    frame with motion tracking. The outline operation runs through the runner (operations.py:
    the default by route, an explicit `model` raw unless `policy` says otherwise; ADR 0011), and
    the outline is written through apply.apply: placed on the live view only where it is verified
    (contract 3)."""
    from .operations import Prompt, Unavailable, runner

    if not isinstance(view, (Capture, int)) and hasattr(view, "id"):
        view = view.id  # a frame's facts: its id
    store = captures(hub)
    capture = view if isinstance(view, Capture) else store.get(view)
    if capture is None:
        latest = hub.latest_info
        if latest is None:
            raise ToolError("no fresh frame to outline on", next="wait for the view to come back, then try again")
        mapped = hub.map_point(x, y, view, latest.id)
        if mapped is None:
            raise ToolError("that frame is too old to map onto the current view", next="click the thing again")
        if box is not None:
            dx, dy = mapped[0] - x, mapped[1] - y
            box = (box[0] + dx, box[1] + dy, box[2] + dx, box[3] + dy)
        x, y = mapped
        capture = store.take(latest.id)
    cls = "interactive" if source == "click" else "analysis"
    request = tickets.current()
    if request is not None:
        request.hold(capture, cls)  # the request keeps the view it was outlined on (naming, checks)
    # An arena arm's outline (a clicked trial, or an ask arm pointing) is the arena's, whoever's words started it.
    origin = origin or ("arena" if trial else None) or ORIGINS_BY_SOURCE.get(source) or ("brain-chosen" if model else "brain-default")
    with store.hold(capture, cls, "outline", f"outline ({source})"):
        prefer_small = False
        cand = None
        if snap and box is None:
            cand = snap_to_candidate(hub, capture.id, x, y)
            if cand is not None:
                x, y = cand.x, cand.y
                prefer_small = cand.radius < 10
        prompt = Prompt("box" if box is not None else "point", x, y, box, label, prefer_small, cand)
        try:
            done = await runner(hub).run("outline", capture, prompt, choice=model, policy=policy, origin=origin,
                                         trial=trial, params={"priority": "click" if source == "click" else "interactive"})
        except Unavailable as exc:
            raise ToolError(str(exc), next=getattr(exc, "next", None)) from exc  # next: passed on
        result = done.output
        if not result.get("polygons"):
            raise ToolError("nothing distinct to outline there; try clicking right on a structure",
                            next=None)  # no next: the message says what to do
        executed = done.execution.executed
        made_by = done.made_by()
        if trial is not None:
            # An unvoted arena arm: what ran stays off every surface until the vote (contract 6).
            made_by = {"operation": made_by["operation"], "policy": made_by["policy"], "origin": "arena",
                       "execution": made_by["execution"], "arm": arm, "reused": made_by["reused"]}
        if done.use.reused:
            # Another request's result on this same view: said, never presented as a fresh run.
            note = f"{executed} outline, reused (made a moment ago for another request; no new run)"
        else:
            note = result.get("note") or f"{executed} outline, score {result.get('score', 0):.2f}, {result.get('ms', 0):.0f} ms"
        # A boxed region (a band or ring of tissue) can come in several pieces; a click is one thing.
        obj = await add_outline(hub, capture, result["polygons"][:8 if box is not None else 3], source=source, label=label,
                                 status=status, trial=trial, arm=arm, confidence=confidence, made_by=made_by,
                                 note=note if trial is None else "", naming=naming, origin_box=box)
    runner(hub).produced(done.execution, obj.id)
    return obj


def outline_scope(source: str) -> str:
    """Which outlines a source makes (ticket scopes): the user's own (a click, a dragged box, an
    arena trial they clicked), or the app's."""
    return "outline:user" if source in ("click", "arena") else "outline:app"


async def add_outline(hub: Hub, capture: Capture, polygons, *, source: str, label: str = "", status: str = "outline",
                      note: str = "", trial: str | None = None, arm: str | None = None, group: str | None = None,
                      confidence: float | None = None, scope: str | None = None, made_by: dict | None = None,
                      unplaced: str | None = None, template: tuple | None = None,
                      naming: dict | None = None, origin_box: tuple | None = None,
                      require_verified: bool = False) -> SceneObject:
    """An outline made on `capture` (polygons in its pixels) added to the scene through
    apply.apply, with the template that re-finds it after the slide moves. Placed where verified;
    an outline whose place cannot be verified now is kept unverified ("refinding", not drawn) until
    the hub's continuous check finds it. ToolError when the request may no longer add it.
    `unplaced`: it is kept unverified for this reason, with no other check of its place (a check already
    made said it is not there). `template`: (pixels, origin in the capture's px) that re-find it, instead of
    a crop around it (a promoted cell's own pixels: M1.5c re-check N2). `origin_box`: the box it was outlined
    from, in the capture's pixels (kept for the outline check: where it was meant to be).
    `require_verified`: a presentation needing live placement refuses at the write boundary, before
    an outline is added; an earlier placement preflight cannot govern a later write."""
    from .apply import Placement, apply, next_for, place_outline

    if not isinstance(capture, Capture):  # a frame id: its capture, while one is held or it is in the ring
        found = captures(hub).get(capture)
        if found is None:
            raise ToolError("the view it was made on is no longer held", next="click the thing again")
        capture = found

    def write(placement):
        if require_verified and not placement.verified:
            raise ToolError(f"not shown on the live view: {placement.how}",
                            next="show the cell on its own, or outline the cells again on the view now")
        anchor = placement.anchor
        obj = SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=placement.polygons, anchor_frame=anchor.id,
                          anchor_epoch=anchor.epoch, anchor_offset=(anchor.dx, anchor.dy), source=source, label=label,
                          status=status, note=note, trial=trial, arm=arm, group=group, confidence=confidence,
                          origin=capture.id, origin_polygons=polygons, made_by=made_by, naming=naming,
                          anchor_geometry=anchor_geometry(anchor),
                          origin_box=tuple(float(v) for v in origin_box) if origin_box is not None else None)
        if not placement.verified:
            obj.track, obj.lost_since, obj.unplaced = "refinding", time.time(), placement.how
        if template is not None:
            # The thing's own pixels, from its capture, moved into the anchor frame's px.
            ox = int(round(template[1][0] + float(anchor.dx) - float(capture.dx)))
            oy = int(round(template[1][1] + float(anchor.dy) - float(capture.dy)))
            obj.template, obj.template_origin = template[0], (ox, oy)
        elif placement.template is not None:
            obj.template, obj.template_origin = placement.template, placement.template_origin
        added = hub.scene.add(obj)
        execution.bind(object_id=obj.id, trial_id=trial)
        return added

    scope = scope or outline_scope(source)

    async def kept_unplaced():
        return Placement(False, str(unplaced), capture, polygons)

    applied = await apply(hub, write, scope=scope, capture=capture,
                          place=kept_unplaced if unplaced else
                          (lambda: place_outline(hub, capture, polygons, "interactive" if scope == "outline:user" else "analysis")))
    if not applied.ok:
        raise ToolError(f"not shown: {applied.reason}", next=next_for(applied.reason, "try it again"))
    name_like_a_tap(hub, applied.value)
    return applied.value


def name_like_a_tap(hub, obj) -> None:
    """Every outline the app draws is named by the same step as a tapped one (option NAMING "one", Will's decision of
    2026-10-12): the look's, the request's or a finder's name shows only until the naming model answers. A tap names
    its own (commands.click); a blind arm stays unnamed until its vote; a dissected part is its object's dissect's."""
    namer = getattr(hub, "namer", None)
    if namer is None or obj is None or getattr(obj, "source", "") in ("click", "arena") or getattr(obj, "trial", None) \
            or getattr(obj, "kind", "") == "part":
        return
    if getattr(getattr(hub, "settings", None), "naming", "per-path") != "one":
        return
    namer(obj.id)


# The seeded composites (operations.py "edgetam>sam3.1-*") pad EdgeTAM's box by this much before
# SAM 3.1 redraws it: SAM 3.1 outlines what fits its box, so a fixed box around a click often
# catches only part of a stoma (one guard cell); a box from EdgeTAM's mask fits the whole object.
SEED_PAD = 0.10


async def segment(
    hub: Hub,
    model: str | None,
    frame_id: int,
    jpeg: bytes,
    x: float,
    y: float,
    *,
    box=None,
    label: str = "",
    prefer_small: bool = False,
    capture: Capture | None = None,
    policy: str = "route",
    origin: str = "brain-chosen",
) -> dict:
    """One outline on a view, through the runner (operations.py): {"polygons", "score", "ms",
    "note"?, "madeBy"}. `model` None: the default. `policy` "route" (the default here, for callers
    that outlined this way before the runner) lets declared fallbacks run; "raw" runs exactly
    `model` or fails (ADR 0011)."""
    from .operations import Prompt, runner

    if capture is None:
        capture = captures(hub).get(frame_id) or Capture(frame_id, jpeg, SimpleNamespace(id=frame_id))
    prompt = Prompt("box" if box is not None else "point", x, y, tuple(box) if box is not None else None, label, prefer_small)
    done = await runner(hub).run("outline", capture, prompt, choice=model, policy=policy, origin=origin)
    return {**done.output, "madeBy": done.made_by()}


def padded_bbox(polygons, pad: float) -> tuple[float, float, float, float]:
    pts = np.array([p for poly in polygons for p in poly], dtype=float)
    x0, y0 = pts.min(axis=0)
    x1, y1 = pts.max(axis=0)
    px, py = max((x1 - x0) * pad, 4.0), max((y1 - y0) * pad, 4.0)
    return float(x0 - px), float(y0 - py), float(x1 + px), float(y1 + py)


async def _segment_fal(hub: Hub, info, jpeg: bytes, x: float, y: float, box, label: str) -> dict:
    """`info`: the view's facts (a Capture), or its frame id, looked up before any wait."""
    from . import fal_segment
    from gpu_worker.worker import mask_to_polygons

    if isinstance(info, int):
        info = hub.infos[info]
    if box is None:
        r = 16.0
        box = (x - r, y - r, x + r, y + r)
    out = await fal_segment.segment_box(jpeg, box, info.width, info.height, label)
    polygons = mask_to_polygons(out["mask"]) if out["mask"] is not None else []
    return {"polygons": polygons, "score": out["score"], "ms": out["ms"]}


def cells_choice(hub: Hub) -> str:
    """The cell finder this run uses, or ToolError saying why none can run here (the runner records
    what it chose and ran)."""
    from . import licences
    from .options import CELLS, resolve

    choice, why = resolve("cells", hub.settings.cells)
    if choice == "off":
        offered = [o for o in CELLS.values() if o.id != "off"]
        if not why and not any(o.permitted for o in offered):  # nothing this edition may use
            why = licences.why_not(offered[0].uses)
        detail = why or "outlining every cell at once is switched off in the settings"
        raise ToolError(f"can't outline all the cells at once here ({detail})", next="click a cell to outline it")
    if hub.gpu is None or not hub.gpu.ready:
        raise ToolError("outline model is still loading", next="try again in a few seconds")
    return choice


async def cell_polygons(hub: Hub, capture: Capture | None = None, hold=None):
    """Every cell-like structure in a view from the cell finder (option CELLS: Cellpose-SAM or
    micro-sam; research edition only: orchestrator/licences.py): (capture, polygons in its pixels).
    The view is the latest unless `capture` is given; it is held for the run. Raises ToolError when
    it cannot run here."""
    cells_choice(hub)  # refused with the reason when no cell finder can run here
    store = captures(hub)
    if capture is None:
        info = hub.latest_info
        capture = store.get(info.id) if info is not None else None
        if capture is None:
            raise ToolError("no fresh frame", next="wait for the view to come back, then try again")
    crop = None
    if capture.fov is not None:
        r = capture.fov.radius * CLICK_RADIUS  # as far as clicks reach (the studio's toFrame)
        x0, y0 = max(0, int(capture.fov.cx - r)), max(0, int(capture.fov.cy - r))
        x1, y1 = min(capture.width, int(capture.fov.cx + r)), min(capture.height, int(capture.fov.cy + r))
        crop = [x0, y0, x1 - x0, y1 - y0]
    from contextlib import nullcontext

    from .operations import Prompt, Unavailable, runner

    # The worst case is reserved before the run (its label map and masks); `hold`: the caller's own,
    # which the caller settles once it knows what it keeps (outline_cells).
    with (store.hold(capture, "analysis", "cells", "the cell finder") if hold is None else nullcontext()):
        # A cell finder takes about a second or more in one call; it runs on the analysis worker so
        # clicks stay instant. Through the runner (operations.py): the default by route, recorded.
        try:
            done = await runner(hub).run("cells", capture, Prompt("field"), params={"crop": tuple(crop) if crop else None})
        except Unavailable as exc:
            raise ToolError(f"can't outline all the cells at once here ({exc})",
                            next="click the cells you want, one at a time") from exc
        result = done.output
    polygons = result.get("polygons") or []
    if capture.fov is not None:
        polygons = [p for p in polygons
                    if capture.fov.contains(float(np.mean([q[0] for q in p])), float(np.mean([q[1] for q in p])), margin=6)]
    return capture, polygons


async def outline_cells(hub: Hub, source: str | None = None, *, compare_reference: bool = False):
    """Every cell-like structure in the current view, kept as a population (evidence, contract 8) and
    shown as the layer over the live view through apply.apply: a request superseded by a clear since
    it began keeps its population but shows nothing. Returns (population, shown)."""
    from .apply import apply
    from .population import layer_of, placed_on_stage, segment

    population = await segment(hub, compare_reference=compare_reference)
    done = await apply(hub, lambda _: layer_of(hub).show(population), scope="cell")
    placed_on_stage(hub, population, done.ok)  # nothing awaited since the show: recorded before its save is filed
    return population, done.ok


def show_first_guess(hub: Hub, frame_id: int, text: str, source: str) -> SceneObject | None:
    """Pointing "snap-first": an amber "looking" ring on the detected spot that best fits the
    question's words, drawn at once while the vision model works (orchestrator/snap.py)."""
    from .snap import pick_spot, ring

    info = hub.infos.get(frame_id)
    if info is None:
        return None
    t0 = time.perf_counter()
    cand = pick_spot(text, hub.candidates_in(frame_id), info.fov)
    if cand is None:
        return None
    obj = SceneObject(
        id=hub.scene.new_id(),
        kind="highlight",
        polygons=[ring(cand)],
        anchor_frame=frame_id,
        anchor_epoch=info.epoch,
        anchor_offset=(info.dx, info.dy),
        anchor_geometry=anchor_geometry(info, captures(hub).scale_of(info)),
        source=source + ":first-guess",
        status="looking",
        note=f"first guess from detected spots, {(time.perf_counter() - t0) * 1000:.0f} ms",
    )
    return hub.scene.add(obj)


def resolve_first_guess(hub: Hub, guess: SceneObject | None, outlined: list[SceneObject]) -> bool | None:
    """Remove the first-guess ring now that the model has answered. Returns whether the
    guess was on something the model outlined (for the A/B log), or None without a guess."""
    if guess is None:
        return None
    hub.scene.remove(guess.id)
    gx, gy = guess.center
    for obj in outlined:
        x0, y0, x1, y1 = obj.bbox
        # Both are in anchor-frame coordinates; map the guess onto the outline's frame.
        dx = obj.anchor_offset[0] - guess.anchor_offset[0]
        dy = obj.anchor_offset[1] - guess.anchor_offset[1]
        if x0 - 12 <= gx + dx <= x1 + 12 and y0 - 12 <= gy + dy <= y1 + 12:
            return True
    return False


def clear(hub: Hub, kind: str | None = None) -> int:
    return hub.scene.clear(kind)


async def segment_all(hub: Hub, capture: "Capture | int", box=None, text: str = "") -> list[dict]:
    """Every instance of a thing in a view (SAM 3.1 on fal): like the exemplar in `box`, and/or
    matching `text`. [{"polygons", "score"}], best first. Raises operations.Unavailable when it
    cannot run here (this edition, no key: its `fallback` names the declared one), ToolError when it
    ran and failed (a failure is said, never substituted)."""
    from .operations import Prompt, runner

    if not isinstance(capture, Capture):
        capture = captures(hub).get(capture)
        if capture is None:
            raise ToolError("that frame is no longer available", next="ask again in a moment")
    prompt = Prompt("example" if box is not None else "text", box=box, text=text)
    from . import ticket as tickets

    # Unavailable before any memory is reserved, with its declared fallback; the request's own choice
    # (`use`) is what is checked when there is one (scaffolding review, M1.4 item 4).
    runner(hub).choose("find-like", prompt, ticket=tickets.current() if tickets.chosen("find-like") else None)
    from .evidence import evidence_of

    evidence_of(hub).admit()  # its matches are kept as evidence: refused before the paid run, not after it
    with captures(hub).hold(capture, "analysis", "cells", "finding every match"):
        done = await runner(hub).run("find-like", capture, prompt)
        matches = [dict(m) for m in done.output.get("matches") or []]
        masks = done.output.get("masks") or []
        if masks:
            # The matches are evidence (contract 8): a population of SAM 3.1's own masks, measured;
            # each match says which member it is.
            from .population import from_matches

            population = from_matches(hub, capture, masks, done.made_by(), example=None, execution_id=done.execution.id,
                                      configuration={"box": [round(v, 1) for v in box] if box else None, "text": text or None})
            runner(hub).produced(done.execution, population.id)
            ids = list(population.members)
            for match, mid in zip(matches, ids):
                match.update(population=population.id, member=mid)
    return matches
