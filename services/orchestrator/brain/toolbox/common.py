"""What the brain's tools share: how a tool finds the object or the researcher it is about."""
from __future__ import annotations

import copy
import re
from typing import Any

from ...contract import IN_VIEW_RADIUS
from ..registry import ToolContext, ToolFailed

OBJECT = {"type": "string", "description": 'object id, "target N", or a label'}
# "Highlight the cortex too": keep the earlier highlights.
RESEARCHER = {"type": "string", "description": 'researcher number ("2") or id, "this" (the one on screen), or how the user '
                                              'named it ("the quiz", "the one about stomata")'}


def log_measurement(services, ctx: ToolContext, what: str, *, population=None, **values) -> None:
    """A measurement as numbers, not only as the sentence the tool returns: one interaction record
    (kind "measurement") with its values and units, the scale it used and where that came from, and
    the frame it was measured on (the bridge's frame number too, so a dataset joins it to the video;
    docs/datasets.md). Never fails a tool."""
    log = getattr(services, "log", None)
    hub = services.hub
    if population is not None:
        # Retained measurements belong to the producing capture, even after a
        # camera/scale switch. Missing historical scale stays unknown.
        view, basis = population.view, population.scale_id
        measured_on = {"frameId": population.capture_id, "seq": view.get("seq"),
                       "boot": view.get("boot") or None, "wallAt": view.get("at"), "run": view.get("run")}
        scale_facts = (None if not basis or basis.get("umPerPx") is None else {
            "umPerPx": basis["umPerPx"], "objective": basis.get("objective"),
            "source": "measured" if basis.get("measured") else "estimate",
            "fieldNumber": basis.get("fieldNumber"), "fieldNumberSource": basis.get("fieldNumberSource")})
        values = {**values, "evidence": population.id, "revision": population.revision}
    else:
        info = getattr(hub, "latest_info", None)
        reading = hub.scale_reading() if hasattr(hub, "scale_reading") else None
        scale = getattr(hub, "scale", None)
        measured_on = {"frameId": getattr(info, "id", None), "seq": getattr(info, "seq", None),
                       "boot": getattr(info, "boot", None) or None, "wallAt": getattr(info, "wall_at", None)}
        scale_facts = None if reading is None else {
            "umPerPx": round(reading.um_per_px, 5), "objective": getattr(reading, "objective", None),
            "source": "measured" if getattr(reading, "measured", False) else "estimate",
            "fieldNumber": getattr(scale, "field_number", None),
            "fieldNumberSource": getattr(scale, "field_number_source", None)}
    record = {"kind": "measurement", "what": what, "channel": ctx.channel, "turnId": ctx.turn_id,
              "frameId": ctx.frame_id, "measuredOn": measured_on, "scale": scale_facts, **values}
    record = copy.deepcopy(record)  # later producer mutation cannot change an attached receipt
    from ..registry import attach

    attach(values={what: {k: v for k, v in record.items() if k not in ("kind", "channel", "turnId")}})
    if log is None:
        return
    try:
        log(record)
    except Exception:
        pass


def record_outline(ctx: ToolContext, o) -> None:
    """File an outline a tool made as its run's output, with the six facts the day's journal keeps for it (its id,
    run, frame, epoch, when it was made and the turn: day/store.py). Every tool that outlines files the same record
    (live 2026-10-11: `find`'s outlines carried only id and frame and were dropped from the app's facts)."""
    from ..registry import record_output

    basis = getattr(o, "producing_basis", None)
    record_output(ctx, {"kind": "outline", "id": o.id, "run": getattr(basis, "run", None),
                        "frame": basis.frame if basis is not None else o.origin,
                        "epoch": getattr(basis, "epoch", None), "created_at": o.created_at,
                        "turn_id": ctx.turn_id if type(ctx.turn_id) is int and ctx.turn_id > 0 else None})


def in_view(obj, info) -> bool:
    """Drawn on the live view right now: tracked in this motion epoch and inside the eyepiece."""
    if info is None:
        return True
    if obj.track != "tracking" or obj.anchor_epoch != getattr(info, "epoch", obj.anchor_epoch):
        return False
    fov = getattr(info, "fov", None)
    if fov is None:
        return True
    cx, cy = obj.center
    cx += info.dx - obj.anchor_offset[0]
    cy += info.dy - obj.anchor_offset[1]
    return ((cx - fov.cx) ** 2 + (cy - fov.cy) ** 2) ** 0.5 <= fov.radius * IN_VIEW_RADIUS


def visible_object(hub, ref: Any, ctx: ToolContext):
    """resolve_object, for tools that show the thing now (select, zoom): not one out of view."""
    obj = resolve_object(hub, ref, ctx)
    if not in_view(obj, hub.latest_info):
        raise ToolFailed(f"{describe(obj)} is out of view right now", next="bring it back into view, then ask again")
    return obj


def resolve_object(hub, ref: Any, ctx: ToolContext):
    """The outline a tool's `object` argument means, now (board.Board.resolve: an id, "target
    2", "the second one", "the one I clicked", "that", a label; nothing named means "that")."""
    from ...board import Board

    board = ctx.board if ctx.board is not None else Board(hub, listen=False)
    return board.resolve(ref, ctx)


def describe(obj) -> str:
    if obj.label:
        return f"the {obj.label}"
    made = getattr(obj, "made_by", None) or {}
    member = made.get("member")
    if not member:
        return "that outline"
    # A population's member outlined is said by its id, as what its population holds (going live, flow 5).
    noun = {"cells": "cell", "find-like": "match"}.get(made.get("operation") or "", "member")
    return f"{noun} {member}"

