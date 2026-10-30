"""Populations: many results of one operation at once (every cell, every automatic mask, every match
of an example), made into evidence the brain can query, compose and show.

docs/design/2026-10-06-captures-operations-evidence.md, contracts 3, 8 and 9; Amendment 1.

- `segment` runs a field operation (the cell finders, EdgeTAM's automatic masks) on a capture
  through the runner and keeps what it produced as a population: the specialist's own masks
  (a label map's labels exactly, or each automatic mask as EdgeTAM made it), measured by app code.
- `from_matches` keeps SAM 3.1's matches of an example the same way, `from_maskset` a background
  automatic-mask set a request used (it becomes evidence only then).
- `Layer` is the population shown over the live view. Each member is drawn live only after its own
  check (contract 3): its pixels, from the capture, matched at the place the slide's move puts it,
  within the check's validity window. Members not verified stay on their captured view. The
  population-wide check shows broad alignment only.
- `promote` makes members ordinary outlines (their own masks, a template from the capture, made-by
  "measured"), through apply.apply, and links each member to its outline (same object, promotion).
"""
from __future__ import annotations

import asyncio
import copy
import functools
import hashlib
import json
import threading
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass
from functools import wraps
from pathlib import Path
from typing import Callable
from uuid import uuid4

import cv2
import numpy as np

from . import spaces
from .capture import RUN, Capture, captures
# The studio's member palette (contract/overlay.json memberColours, read by population.ts too), BGR: a member's
# colour comes from its id (evidence.colour_of), the same in its rows, its close-up and its highlight (contract 10).
from .colours import PALETTE_BGR
from .evidence import Member, Population, decode_labels, evidence_of, label_workspace_bytes, masks_from_labels, polygons_of
from .measure import Mask

# A member's check holds this long and for this much slide travel (contract 3, "how long a check stays valid").
MEMBER_CHECK_S = 2.0
MEMBER_TRAVEL_PX = 16.0
MEMBER_SCORE = 0.6  # its own pixels match at least this well where the slide's move puts it
CHECK_EVERY_S = 0.3
SCALE = 0.5  # members are checked at half resolution: off the frame path, cheap for hundreds
MARGIN = 2  # px around a member's box in its template (at half resolution): the member, not its neighbourhood
SEARCH = 6  # half-resolution px searched around the predicted place...
AT_PLACE = 1  # ...and the best match must be within this of it (2 px at full size), or it is not verified
# The slide's offset can be a few px off after a pan (the hub nudges outlines by up to 10 px for it, after
# half a second of stillness). The members' own peaks say by how much: their median, at most this many
# half-resolution px (10 at full size) and from at least MIN_FOR_CORRECTION members, is the population's
# correction; each member is then checked against the corrected place, and drawn there.
MAX_CORRECTION = 5
MIN_FOR_CORRECTION = 3


def view_facts(capture) -> dict:
    fov = getattr(capture, "fov", None)
    return {"epoch": getattr(capture, "epoch", None), "dx": float(getattr(capture, "dx", 0.0)),
            "dy": float(getattr(capture, "dy", 0.0)),
            "disc": [round(fov.cx, 2), round(fov.cy, 2), round(fov.radius, 2)] if fov is not None else None,
            "discPx": float(getattr(capture, "disc", 0.0) or 0.0),
            "width": int(getattr(capture, "width", 0) or 0), "height": int(getattr(capture, "height", 0) or 0),
            "at": float(getattr(capture, "wall_at", 0.0) or getattr(capture, "at", 0.0) or time.time()),
            "seq": getattr(capture, "seq", None), "boot": getattr(capture, "boot", ""), "run": RUN,
            "tracked": bool(getattr(capture, "tracked", True)),  # the slide's motion registered on it (linking reads it)
            **({"sourceImage": copy.deepcopy(capture.source_image)} if getattr(capture, "source_image", None) else {})}


def members_from(output: dict, evidence, session: str | None = None, *, admit_masks=None,
                 admit_workspace=None, admit_contours=None) -> "OrderedDict[str, Member]":
    """The members of an operation's output, each with the specialist's own mask (ids new for `session`, the one it
    is made in):
    - a label map (the cell finders): each label exactly, with its score when the model gave one;
    - automatic masks: each mask as EdgeTAM made it, at the size it made it;
    - polygons only (an older worker, a test's stand-in): each filled, said in its extras."""
    members: OrderedDict[str, Member] = OrderedDict()
    if output.get("labels"):
        labels = decode_labels(output["labels"])
        origin = tuple(output["labels"].get("origin") or (0, 0))
        scale = float(output["labels"].get("scale") or 1)  # a large still's working copy (source_images.Working)
        scores = output.get("scores") or {}
        mask_bytes, instance_bytes, polygon_bytes = 0, 0, 0

        def plan_instances(distinct):
            nonlocal instance_bytes
            instance_bytes = distinct * 1024  # Mask/Member, IDs, boxes, extras and container slots
            if admit_workspace is not None:
                admit_workspace(distinct)

        def keep_masks(nbytes):
            nonlocal mask_bytes
            mask_bytes = nbytes
            if admit_masks is not None:
                admit_masks(mask_bytes + instance_bytes)

        def keep_polygon(points):
            nonlocal polygon_bytes
            # Coordinate pair lists/floats, list slots/overallocation, and the polygon's own list.
            polygon_bytes += points * 160 + 128
            if admit_masks is not None:
                admit_masks(mask_bytes + instance_bytes + polygon_bytes)

        for label, mask in masks_from_labels(labels, origin, admit=keep_masks, admit_workspace=plan_instances,
                                             scale=scale):
            extras = {}
            score = scores.get(label, scores.get(str(label)))
            if score is not None:
                extras["score"] = score
                extras["scoreIs"] = output.get("score") or "score"
            mid = evidence.member_id(session)
            members[mid] = Member(mid, int(label), mask,
                                  polygons_of(mask, admit=keep_polygon, admit_contours=admit_contours), mask.box, extras)
        return members
    if output.get("regions"):
        for n, region in enumerate(output["regions"], start=1):
            m = region.get("mask")
            if not m:
                continue
            h, w = m["shape"]
            bits = np.unpackbits(np.frombuffer(m["bits"], np.uint8))[: h * w].reshape(h, w).astype(bool)
            mask = Mask(bits, m["x0"], m["y0"], m["scale"])
            mid = evidence.member_id(session)
            members[mid] = Member(mid, n, mask, polygons_of(mask), mask.box,
                                  {"score": region.get("score"), "stability": region.get("stability"), "scoreIs": "iou"})
        return members
    for n, poly in enumerate(output.get("polygons") or [], start=1):
        pts = np.array(poly, np.float64)
        x0, y0 = np.floor(pts.min(axis=0)).astype(int)
        x1, y1 = np.ceil(pts.max(axis=0)).astype(int)
        bits = np.zeros((max(1, y1 - y0 + 1), max(1, x1 - x0 + 1)), np.uint8)
        cv2.fillPoly(bits, [np.round(pts - [x0, y0]).astype(np.int32)], 1)
        mask = Mask(bits.astype(bool), int(x0), int(y0))
        mid = evidence.member_id(session)
        members[mid] = Member(mid, n, mask, polygons_of(mask), mask.box, {"from": "polygon"})
    return members


async def segment(hub, *, capture=None, choice: str | None = None, policy: str | None = None,
                  origin: str = "brain-default", region=None, priority: str | None = None,
                  made_for: str | None = None, thread: str | None = None, compare_reference: bool = False,
                  operation: str = "cells", inputs: str | None = None) -> Population:
    """Every cell (CELLS) in a view, kept as a population (contract 8). Refused before it runs past
    the disk budget or when unsaved evidence fills its allowance (contracts 1 and 8). A researcher's own
    run (research/hands.py) passes priority "research", who it is for and its conversation, so the
    population is that from the start (its first save and the index's listeners see it so)."""
    from . import ticket as tickets
    from .contract import CLICK_RADIUS
    from .operations import Prompt, Unavailable, runner
    from .tools import ToolError

    evidence = evidence_of(hub)
    evidence.admit()
    store = captures(hub)
    from .source_images import for_analysis, working_copy

    if capture is None and getattr(hub, "stage_image_json", None) is None:
        info = getattr(hub, "latest_info", None)
        capture = store.get(info.id) if info is not None else None
    supplied_original = capture is not None and getattr(capture, "native", None) is not None
    capture = await for_analysis(hub, capture)
    if capture is not None and capture.native is not None and region is not None and not supplied_original:
        raise ToolError("the preview selection has no mapping to the original image",
                        next="analyze the whole original image, or make a separate cropped input in the source tool")
    if capture is None:
        raise ToolError("no fresh frame", next="wait for the view to come back, then try again")
    if region is None and capture.fov is not None:
        r = capture.fov.radius * CLICK_RADIUS  # as far as clicks reach (the studio's toFrame)
        x0, y0 = max(0, int(capture.fov.cx - r)), max(0, int(capture.fov.cy - r))
        x1, y1 = min(capture.width, int(capture.fov.cx + r)), min(capture.height, int(capture.fov.cy + r))
        region = (x0, y0, x1 - x0, y1 - y0)
    # A still larger than a camera frame is shown to the model as its working copy, every pixel an exact block of
    # the original's (source_images.working_copy); what it finds is kept on the original's own pixels, its masks at
    # the copy's scale, so it is measured, saved and drawn on the Stage's still as one found on the original is.
    copied = await asyncio.to_thread(working_copy, store, capture) if capture.native is not None else None
    # Held until it is saved, in the unsaved-evidence allowance (its worst case: the view, its label
    # map and masks, at the size the model is shown); refused, with the reason, when that is full.
    # A researcher's own run (priority "research") holds in its own allowance, never the user's.
    hold = store.hold(capture, "research" if priority == "research" else "unsaved", "evidence", "evidence being made",
                      grid=copied.size if copied is not None else None)
    shown_hold = None
    try:
        if copied is not None:  # its bytes, resident while the model is shown them
            shown_hold = store.hold(copied.capture, "analysis", "bytes", "the large still's working copy")
        disc = [capture.fov.cx, capture.fov.cy, capture.fov.radius] if capture.fov is not None else None
        crop = (copied.crop(region) if copied is not None else tuple(region)) if region else None
        try:
            done = await runner(hub).run(operation, copied.capture if copied is not None else capture, Prompt("field"),
                                         choice=choice, policy=policy, origin=origin,
                                         params={"compare_reference": compare_reference, "crop": crop, "disc": tuple(disc) if disc else None,
                                                 **({"priority": priority} if priority else {}),
                                                 **({"inputs": inputs} if inputs else {})}, made_for=made_for)
        except Unavailable as exc:
            raise ToolError(f"can't segment every cell here ({exc})" if operation == "cells" else f"can't run it here ({exc})",
                            next="click a cell to outline it" if operation == "cells" else "its card on the Models page") from exc
        finally:
            if shown_hold is not None:
                shown_hold.release()
        output = copied.to_original(done.output) if copied is not None else done.output
        request = tickets.current()
        session = _session(request, hub)
        # Decoding and bounds planning have transient room. Once bounds are known, the evidence
        # hold must cover their aggregate mask storage before any output masks are materialized.
        retained_output_bytes = 0

        def admit_masks(nbytes):
            nonlocal retained_output_bytes
            needed = len(capture.jpeg or b"") + len(capture.native or b"") + nbytes
            hold.reservation.grow(max(0, needed - hold.reservation.nbytes), "retaining instance masks and geometry")
            retained_output_bytes = nbytes

        workspace = label_workspace_bytes(output["labels"], distinct=0) if output.get("labels") else 0
        label_workspace = workspace
        with store.memory.reserve("research" if priority == "research" else "analysis", workspace,
                                  "decoding and extracting the returned instance labels") as working:
            def admit_workspace(distinct):
                nonlocal label_workspace
                needed = label_workspace_bytes(output["labels"], distinct=distinct)
                label_workspace = needed
                working.grow(max(0, needed - working.nbytes), "planning the returned instance bounds")

            def admit_contours(nbytes):
                working.grow(max(0, label_workspace + nbytes - working.nbytes), "extracting instance contours")

            members = members_from(output, evidence, session, admit_masks=admit_masks,
                                   admit_workspace=admit_workspace, admit_contours=admit_contours)
        from .operations import OPERATIONS

        impl = OPERATIONS[operation].impl(done.execution.executed or "") if operation in OPERATIONS else None
        crop_used = list(region) if region else [0, 0, int(capture.width), int(capture.height)]  # the whole frame without a disc
        made_by = {**done.made_by(), **({"for": made_for} if made_for else {})}
        population = Population(evidence.new_id(session), operation, done.execution.executed, made_by, capture.id,
                                view_facts(capture), capture.scale, tuple(region) if region else None, members,
                                ticket=getattr(request, "id", None), thread=thread if made_for else _thread(request, hub),
                                day=_day(hub), generation=_generation(request, hub), session=session,
                                capture=capture, overlapping=bool(output.get("regions")), hold=hold,
                                producer={**(output.get("producer") or {}),
                                          **({"capturedNativeInput": copy.deepcopy(capture.source_image)}
                                             if (capture.source_image or {}).get("nativeSource") else {}),
                                          **({"workingInput": copied.wire()} if copied is not None else {}),
                                          "implementation": done.execution.executed,
                                          # fine-tuned weights say which version made it (operations._cells)
                                          "version": output.get("version") or (impl.version if impl is not None else ""),
                                          "configuration": {"crop": crop_used,
                                                            "disc": [round(v, 2) for v in disc] if disc else None,
                                                            "scoreIs": output.get("score") or ("iou" if output.get("regions") else None)}},
                                transformations={"crop": crop_used,
                                                 "input": (f"the original imported image, area-averaged to {copied.size[0]} x "
                                                           f"{copied.size[1]} for the model (its working size: each pixel the mean "
                                                           f"of a {copied.scale} x {copied.scale} block of the original's)"
                                                           if copied is not None else
                                                           "the original imported image, decoded without resizing"
                                                           if capture.native is not None else "the camera's JPEG as received, decoded once"),
                                                 "masks": ((("Native ImageJ particle mask pixels retained exactly on the working copy's grid"
                                                             if done.execution.executed == "fiji-analyze-particles" else
                                                             "kept as the model returned them on the working copy")
                                                            + f", each mask pixel a {copied.scale} x {copied.scale} block of the "
                                                              "original's pixels; measured in the original's pixels")
                                                           if copied is not None else
                                                           "Native ImageJ particle mask pixels retained exactly on the input grid; pixel-center measurements"
                                                           if done.execution.executed == "fiji-analyze-particles" else
                                                           "kept as the model returned them, each in its box at the model's scale"),
                                                 "measuredOn": "the capture's grey picture, full size"})
        if capture.fov is not None:
            _inside_disc(population, capture.fov)
        # Instance records and derived polygons remain resident alongside the exact masks.
        masks_kept = sum(int(np.asarray(m.mask.bits).size) for m in members.values() if m.mask is not None)
        # The fixed measurement row, its revision-zero copy and table scaffolding are also resident.
        # Admit these before measure_all materializes them; summary ownership is separated at saving.
        admit_masks(max(masks_kept, retained_output_bytes) + 16384 + 8192 * len(members))
        hold.reservation.settle(len(capture.jpeg or b"") + len(capture.native or b"")
                                + max(masks_kept, retained_output_bytes))
        with store.hold(capture, "analysis", "hold", "measuring the members"):  # the gray decode, counted
            gray = await asyncio.to_thread(capture.gray)
            await asyncio.to_thread(population.measure_all, gray, done.execution.id)
        runner(hub).produced(done.execution, population.id)
    except BaseException:
        hold.release()
        raise
    evidence.add(population)
    evidence.schedule_save(population)
    return population


def drawn_on_stage(hub, population: Population) -> bool:
    """Whether the cells layer can draw this population's members over the Stage: one made from the view itself, or one
    found on the original of the still the Stage shows now (its `sourceImage` is that asset's), placed there by the
    still's fit (`Layer.check`, still_fit.py). One kept on another original image is measured in that image's own pixels
    and is shown on its saved result, not on the Stage (check() says why), so nothing may say it is drawn there."""
    source = population.view.get("sourceImage")
    return not source or on_stage(getattr(hub, "stage_image_json", None), source)


KEPT_ON_ORIGINAL = "kept on the original image; open the saved result to inspect its particles"


# How long a This Tab run's window waits on its population's first check on the Stage's still (Layer.await_specimen):
# not checked there by then, its masks window is placed on its Tab as for any other run (S5b).
SPECIMEN_WAIT_S = 10.0


def placed_on_stage(hub, population: Population, shown: bool) -> bool:
    """A This Tab run's population found on the still the Stage shows, and shown there: the specimen is to be its window
    (Will's call, 2026-10-25, recorded by lane-uiux: it stays in Data, where Add to Tab… places it, and the run's footer
    opens it there), once its outlines are drawn on it. What is recorded now, before the population's save is filed on
    the event loop, is only that its window waits on that check (`workspace_views.specimen` "checking"), so reconcile
    never places its masks first; the layer's first check that verifies them on the Stage's still decides "drawn" (no
    window: it is kept in Data and listed as such), and one that does not come, or the layer hidden or replaced first,
    decides "placed" (its masks window, as before). A New Tab run keeps its window (the Tab it opened is for it), and a
    live camera run too: whether its outlines stay on the specimen is known only as the slide moves. A population with
    no members has nothing to draw. True when its window waits on the specimen."""
    if not shown or not population.view.get("sourceImage") or not population.live() or not drawn_on_stage(hub, population):
        return False
    from . import ticket as tickets

    request = tickets.current()
    origin = getattr(request, "workspace_origin", None)
    execution = (population.made_by or {}).get("execution")
    if not origin or not execution or origin.get("destination") == "new" or request.workspace_store is None:
        return False
    from .workspace_views import specimen

    try:
        if specimen(request.workspace_store(), origin, execution, population.id, "checking") != "checking":
            return False  # decided before (it never changes)
    except (OSError, ValueError):
        return False  # not recorded: its window is placed as any other run's
    layer_of(hub).await_specimen(population, request.workspace_store, origin, execution)
    request.on_specimen = origin["run"]
    return True


@functools.lru_cache(maxsize=16)
def stage_spec(spec_json: str | None) -> dict | None:
    """The still a frame shows (its intake tag, `FrameInfo.source_image_json`: asset, path, session), or the Stage's
    (`hub.stage_image_json`); None for the live camera and test footage."""
    if not spec_json:
        return None
    try:
        spec = json.loads(spec_json)
    except (TypeError, ValueError):
        return None
    return spec if isinstance(spec, dict) and spec.get("asset") and (spec.get("path") or isinstance(spec.get('retained'), dict)) else None


def on_stage(spec_json: str | None, source: dict | None) -> bool:
    """Whether the still `spec_json` names is the picture a population was found on (`source`, its sourceImage): the
    same asset. A population made from a retained result's picture names no asset, so it is never the Stage's."""
    spec = stage_spec(spec_json)
    if not (spec and isinstance(source, dict) and source.get('asset') == spec['asset']):
        return False
    if spec.get('retained') is not None or source.get('retained') is not None:
        return (isinstance(spec.get('retained'), dict) and spec['retained'] == source.get('retained')
                and spec.get('session') == source.get('session'))
    return True


def _inside_disc(population: Population, fov) -> None:
    """Members whose centre is outside the eyepiece are not part of the view's population (the
    specialist segmented a square around the disc): dropped before anything is counted."""
    for mid in [m.id for m in population.members.values()
                if not fov.contains((m.box[0] + m.box[2]) / 2, (m.box[1] + m.box[3]) / 2, margin=6)]:
        population.members.pop(mid)


def _thread(request, hub) -> str | None:
    """The conversation evidence a request made belongs to (populations and motion tracks): the one it was asked
    in. A session's conversations keep their ids for good (ADR 0017), so a request with its session is always filed
    under its own; one from before sessions only while its conversation is still the one loaded (one asked before
    "start today afresh" is filed under the day, never in the new conversation that only shares its id: as
    resources.Results._thread_of; the partner's review of M3.2, S2). Without a request, the conversation going on now."""
    asked = getattr(request, "asked", None)
    day = getattr(hub, "execution_day", None)  # the session store (app.py)
    if asked is not None and getattr(asked, "thread", None):
        if getattr(asked, "session", None):
            return asked.thread
        current = getattr(day, "current", None) if day is not None else None
        if current is not None and not current(getattr(asked, "routing_generation", getattr(asked, "generation", None))):
            return None
        return asked.thread
    return getattr(day, "active_id", None) or None if day is not None else None


def _session(request, hub) -> str:
    """The session evidence a request made belongs to (ADR 0017): the one it was asked in, else the one open now
    ("" with no session store: a test's stand-in hub)."""
    asked = getattr(request, "asked", None)
    if asked is not None and getattr(asked, "session", None):
        return str(asked.session)
    day = getattr(hub, "execution_day", None)
    return str(getattr(day, "sid", "") or "") if day is not None else ""


def filed_thread(hub, thread: str | None, session: str | None, generation) -> str | None:
    """The conversation a saved item is filed under in its session's journal: its own when it has a session (a
    session's conversations keep their ids for good); one from before sessions only while its conversation is still
    the one loaded (else filed under the session: S2)."""
    if session:
        return thread
    return thread if current_generation(hub, generation) else None


def _generation(request, hub) -> int | None:
    """The day's generation that conversation belongs to (kept on the evidence, so a list of "this conversation's"
    never takes an earlier generation's t1 for the new one)."""
    asked = getattr(request, "asked", None)
    if asked is not None and getattr(asked, "generation", None) is not None:
        return getattr(asked, "routing_generation", asked.generation)
    day = getattr(hub, "execution_day", None)
    return getattr(day, "generation", None) if day is not None else None


def current_generation(hub, generation) -> bool:
    """Whether evidence of `generation` belongs to the day as it is loaded now (None: always)."""
    day = getattr(hub, "execution_day", None)
    current = getattr(day, "current", None) if day is not None else None
    return True if current is None or generation is None else bool(current(generation))


def _day(hub) -> str:
    """The date folder evidence made now goes to: the session's date."""
    day = getattr(hub, "execution_day", None)
    return getattr(day, "date", "") or time.strftime("%Y-%m-%d") if day is not None else time.strftime("%Y-%m-%d")


def from_matches(hub, capture, masks: list[dict], made_by: dict, example: str | None, execution_id: str | None,
                 configuration: dict | None = None) -> Population:
    """SAM 3.1's matches of an example, kept as a population (each match's own mask, its score), held
    in the unsaved-evidence allowance until saved (contract 1)."""
    from . import ticket as tickets

    evidence = evidence_of(hub)
    evidence.admit()
    hold = captures(hub).hold(capture, "unsaved", "evidence", "evidence being made")
    request = tickets.current()
    session = _session(request, hub)
    members: OrderedDict[str, Member] = OrderedDict()
    for n, m in enumerate(masks, start=1):
        bits = np.asarray(m["mask"], bool)
        rows, cols = np.flatnonzero(bits.any(axis=1)), np.flatnonzero(bits.any(axis=0))
        if not len(rows):
            continue
        y0, y1, x0, x1 = int(rows[0]), int(rows[-1]) + 1, int(cols[0]), int(cols[-1]) + 1
        # A copy of the crop: a view would keep the whole frame-sized mask in memory, uncounted.
        mask = Mask(bits[y0:y1, x0:x1].copy(), x0, y0)
        mid = evidence.member_id(session)
        members[mid] = Member(mid, n, mask, polygons_of(mask), mask.box, {"score": m.get("score"), "scoreIs": "sam"})
    from .capture import decoding

    population = Population(evidence.new_id(session), "find-like", made_by.get("implementation"), made_by, capture.id,
                            view_facts(capture), getattr(capture, "scale", None), None, members,
                            ticket=getattr(request, "id", None), thread=_thread(request, hub), day=_day(hub),
                            generation=_generation(request, hub), session=session,
                            capture=capture, example=example, overlapping=True, hold=hold,
                            producer={"implementation": made_by.get("implementation"), "version": "fal-ai/sam-3-1/image-rle",
                                      "configuration": dict(configuration or {})},
                            transformations={"input": "the camera's JPEG as received", "masks": "SAM 3.1's own, full size",
                                             "measuredOn": "the capture's grey picture, full size"})
    hold.reservation.settle(len(capture.jpeg or b"") + sum(int(np.asarray(m.mask.bits).size) for m in members.values()))
    try:
        with decoding(hub, capture, "hold", owner="measuring the matches") as held:  # the gray decode, counted
            population.measure_all(held.gray(), execution_id)
    except BaseException:
        hold.release()
        raise
    evidence.add(population)
    evidence.schedule_save(population)
    return population


def from_maskset(hub, marks) -> tuple[Population | None, dict]:
    """A background automatic-mask set a request used (pointing "masks"): kept as evidence once, then,
    with its capture (the frame the masks were made on) and each region's own mask, as EdgeTAM made it
    (contract 8: background results become evidence only when a request uses one). Returns the
    population (None when its view is no longer held) and number -> member id."""
    from . import ticket as tickets

    kept = getattr(hub, "_masksets", None)
    if kept is None:
        kept = {}
        try:
            hub._masksets = kept
        except AttributeError:
            pass
    made_on = getattr(marks, "made_on", -1)
    if made_on in kept:
        return kept[made_on]
    capture = captures(hub).get(made_on) if made_on is not None and made_on >= 0 else None
    numbers = [n for n in sorted(marks.regions) if marks.regions[n].get("mask")]
    if capture is None or not numbers:
        return None, {}
    evidence = evidence_of(hub)
    evidence.admit()
    hold = captures(hub).hold(capture, "unsaved", "evidence", "evidence being made")
    try:
        request = tickets.current()
        session = _session(request, hub)
        members = members_from({"regions": [marks.regions[n] for n in numbers]}, evidence, session)
        by_number = dict(zip(numbers, members))
        from .operations import runner

        record = runner(hub).background("automask", "edgetam-automask", made_on, float(getattr(marks, "made_ms", 0.0) or 0.0))
        made_by = record.made_by()
        population = Population(evidence.new_id(session), "automask", "edgetam-automask", made_by, capture.id,
                                view_facts(capture), capture.scale, None, members, ticket=getattr(request, "id", None),
                                thread=_thread(request, hub), day=_day(hub), generation=_generation(request, hub),
                                session=session,
                                capture=capture, overlapping=True, hold=hold,
                                producer={"implementation": "edgetam-automask", "version": "yonigozlan/EdgeTAM-hf",
                                          "configuration": {"prompts": "a point grid and the detected spots",
                                                            "madeOn": made_on}},
                                transformations={"input": "the camera's JPEG as received",
                                                 "masks": "EdgeTAM's own, at its working size (scale per member)",
                                                 "measuredOn": "the capture's grey picture, full size"})
        from .capture import decoding

        with decoding(hub, capture, "hold", owner="measuring the automatic masks") as held:
            population.measure_all(held.gray(), None)
    except BaseException:
        hold.release()
        raise
    evidence.add(population)
    runner(hub).produced(record, population.id)
    evidence.schedule_save(population)
    kept[made_on] = (population, by_number)
    return population, by_number


# --- the layer over the live view ---------------------------------------------------------------
class _TemplateBatch:
    """One counted set of copies, owned by the cache and its actual CPU readers."""

    def __init__(self, memory) -> None:
        self.templates: dict[str, tuple] = {}
        self.reservation = memory.reserve("analysis", 0, "the shown members' template copies")
        self._interests = 1  # constructing caller, transferred to the cache only after authority checks
        self._lock = threading.Lock()

    def reader(self) -> "_TemplateUse":
        with self._lock:
            if not self._interests:
                raise RuntimeError("these member templates have retired")
            self._interests += 1
        return _TemplateUse(self)

    def release(self) -> None:
        with self._lock:
            self._interests -= 1
            if self._interests == 0:
                self.templates.clear()  # drop the actual arrays before their allowance
                self.reservation.release()


class _TemplateUse:
    """A queued or executing reader's interest, separate from permission to publish."""

    def __init__(self, batch: _TemplateBatch) -> None:
        self.batch = batch
        self._state = "queued"
        self._lock = threading.Lock()

    def begin(self) -> bool:
        with self._lock:
            if self._state != "queued":
                return False
            self._state = "running"
            return True

    def finish(self) -> None:
        with self._lock:
            if self._state != "running":
                return
            self._state = "finished"
        self.batch.release()  # only the actual CPU callable retires a running reader

    def discard_queued(self) -> None:
        with self._lock:
            if self._state != "queued":
                return
            self._state = "finished"
        self.batch.release()  # a later callable cannot begin after this atomic retirement


@dataclass(frozen=True)
class _TemplateInput:
    """Original pixels' address, immutable member boxes and the CPU's reservation interest."""
    capture: Capture | None
    path: object
    width: int
    height: int
    boxes: tuple
    use: _TemplateUse


@dataclass(frozen=True)
class _MemberCheck:
    """One real check, shared by its placements; not a history or a live-source grant."""
    ref: str
    checked_at_mono: float
    basis_json: str
    population: object  # weak reference: identity authority, never a copied/newest population
    revision: int
    lifetime: object
    epoch: int | None

    def __deepcopy__(self, memo):
        return self  # the immutable sample remains one shared basis in copied placements too


@dataclass(frozen=True)
class _MatchInput:
    use: _TemplateUse
    basis_json: str
    population: object
    revision: int
    lifetime: object

    @property
    def templates(self) -> tuple:
        # The work keeps the owner, not an uncounted tuple after that owner has retired.
        return tuple(self.use.batch.templates.items())


def _frame_objective(info):
    """Known intake optics only; a knob moved after this bitmap is not its objective."""
    frozen = getattr(info, "optics_json", None)
    if frozen is None:
        return None
    try:
        return (json.loads(frozen).get("objective") or {}).get("value")
    except (TypeError, ValueError, AttributeError):
        return None


class Layer:
    """The population shown over the live view, its style, and which members are verified in place
    now (contract 3). The studio draws a member live only when it is verified."""

    def __init__(self, hub, broadcast=None) -> None:
        self.hub = hub
        self.broadcast = broadcast or getattr(hub, "broadcast", lambda payload: None)
        self.shown: str | None = None
        self.style = {"opacity": 1.0, "colour": "id"}
        self.placement: dict[str, dict] = {}  # member id -> {"state", "at", "dx", "dy", "score"}
        self.templates: dict[str, tuple] = {}  # member id -> (template, origin) at SCALE, for the shown population
        self._template_batch: _TemplateBatch | None = None
        self._task: asyncio.Task | None = None
        self._sent: tuple = (frozenset(), "", (0, 0), None, None)
        # A population found on a still's original pixels, drawn on the Stage while it shows that still (check_still):
        # the still's fit as last published with the view, and the reference its frames are checked against.
        self.fit = None  # still_fit.Fit
        self._still: tuple | None = None  # (key, still_fit.Reference or None, why none) for the shown population
        self._still_seen: tuple | None = None  # (frame JPEG digest, reference key, score): a repeated frame is not decoded again
        # A This Tab run's window waiting on the shown population's first check on the Stage's still (placed_on_stage):
        # {population, store, origin, execution, until}; and how each such run was decided, by its run, newest last.
        self._awaiting: dict | None = None
        self.specimen_runs: OrderedDict[str, str] = OrderedDict()  # run -> "checking" | "drawn" | "placed"
        self.templates_of: str | None = None  # the population the templates are of (a newer show replaces them)
        self._templates_authority = None  # (actual population object, revision, shown lifetime)
        self._shown_lifetime = object()
        self.selected: str | None = None  # the member a click selected
        self.selected_at = 0.0
        self.style_for: str | None = None  # the population the style was made for (a hide and show keeps it)
        self.confirmed_style: dict = dict(self.style)  # the style the studio last showed (an undo goes back to it)
        self.correction: tuple[float, float] = (0.0, 0.0)  # full-size px added to the slide's move (see MAX_CORRECTION)
        self.view: tuple | None = None  # (epoch, dx, dy) of the shown population's view now (a rejoin may carry it)
        # Told (population id or None, the session it belongs to) whenever the layer shows or hides one: the app
        # journals it in that session while it is the one open (day/store.py record_layer), so hiding it because another
        # session opened changes nothing in the session left, and reopening that session can show it again on its picture.
        self.journal: Callable[[str | None, str], None] | None = None
        self._shown_session = ""
        scene = getattr(hub, "scene", None)
        if scene is not None and isinstance(getattr(scene, "on_change", None), list):
            scene.on_change.append(self._scene_changed)

    def _scene_changed(self, op: str, ids) -> None:
        """An outline selected (a click, the brain's select): the cell selected before is no longer what
        "that" means (M1.5c review item 1)."""
        if op == "select" and ids and self.selected is not None:
            self.select(None)

    def population(self) -> Population | None:
        return evidence_of(self.hub).get(self.shown)

    def show(self, population: Population) -> None:
        if self._awaiting is not None and self._awaiting["population"] != population.id:
            self._settle_specimen("placed", "another layer was shown before its outlines were checked on the specimen")
        self._shown_lifetime = object()  # showing the same id again still retires pending work
        self._drop_templates()
        if self.style_for != population.id:  # the style was made for another (not just hidden and shown again)
            # Another population: a selection and per-member values were about the old one's members, so
            # they go (colour by a column goes back to each member's own colour); opacity, fill and one
            # colour for all are the look of the layer and stay (M1.5b re-check N1).
            style = {k: v for k, v in self.style.items()
                     if k not in ("emphasis", "values", "legend", "column", "columnGroup", "columnExecution")}
            if style.get("colour") in ("column", "category"):  # by a column or a category (K1 review SF4): the old members'
                style["colour"] = "id"
            self.style = style
            self.confirmed_style = dict(style)
            self.style_for = population.id
            self.selected = None  # a cell of the other population is no longer "that cell" (re-check S3)
        self.shown, self.placement, self.templates, self._sent = population.id, {}, {}, (frozenset(), "", (0, 0), None, None)
        self.templates_of = None
        self._templates_authority = None
        self.correction, self.view = (0.0, 0.0), None
        self.fit, self._still, self._still_seen = None, None, None
        self._shown_session = population.session
        self.broadcast(population.wire(self.states()))
        self.broadcast(self.wire())
        self.start()
        self._journaled(population.id)

    def _journaled(self, eid: str | None) -> None:
        if self.journal is None:
            return
        try:
            self.journal(eid, self._shown_session)
        except Exception:
            pass  # what a reopened session shows again; never a reason for the layer not to show or hide now

    def forget_stale_style(self, population: Population) -> bool:
        """The column group the layer is coloured by is stale (a correction: K1 review B3) or was replaced since
        the colours were made (regrouped, a group named again: its execution is not the style's; the re-check
        SF2): back to each member's own colour, now and as the studio last showed it, so no stale or replaced
        category or legend is drawn or put back. For the population the style was made for, shown or hidden;
        `wire` runs it, so every send of the style is checked (a hidden layer shown again too)."""
        if self.style_for != population.id and self.shown != population.id:
            return False
        changed = False
        for name in ("style", "confirmed_style"):
            style = getattr(self, name)
            gid = style.get("columnGroup")
            if style.get("colour") not in ("column", "category") or not gid:
                continue
            group = population.columns.get(gid)
            made = style.get("columnExecution")
            if group is None or population.stale(group) or (made is not None and group.get("execution") != made):
                kept = {k: v for k, v in style.items()
                        if k not in ("values", "legend", "column", "columnGroup", "columnExecution", "value")}
                setattr(self, name, {**kept, "colour": "id"})
                changed = True
        return changed

    def hide(self) -> None:
        self._settle_specimen("placed", "the layer was hidden before its outlines were checked on the specimen")
        self._shown_lifetime = object()
        self._drop_templates()
        if self.shown is not None:
            self.shown = None
            self.placement = {}
            self.fit, self._still, self._still_seen = None, None, None
            self.broadcast(self.wire())
            self._journaled(None)

    def _drop_templates(self) -> None:
        batch, self._template_batch = self._template_batch, None
        self.templates, self.templates_of, self._templates_authority = {}, None, None
        if batch is not None:
            batch.release()  # a running reader retains its own interest until its CPU finally

    async def _await_pixels(self, fn, *args, use: _TemplateUse):
        # Shield ordinary to_thread work from its caller's cancellation. Its actual callable
        # releases a running reader; completion can only retire a reader that never began.
        try:
            job = asyncio.create_task(asyncio.to_thread(fn, *args))
        except BaseException:
            use.discard_queued()
            raise

        def finished(task):
            use.discard_queued()
            if not task.cancelled():
                task.exception()  # observe late failures without making them successful results

        job.add_done_callback(finished)
        return await asyncio.shield(job)

    def wire(self) -> dict:
        shown = self.population() if self.shown is not None else None
        if shown is not None:
            self.forget_stale_style(shown)  # never send colours by a group that is stale or replaced (the K1 re-check)
        at = time.monotonic()
        # This is the retained publication, including its view. A rejoin being checked must not
        # mix the next frame's epoch with the previous complete publication (going-live contract).
        verified = sorted(m for m, p in self.placement.items() if self._retained(p, shown, at))
        # Why the rest are not drawn now (the last check's reason), so the studio can say it.
        reasons = sorted({p.get("why") for m, p in self.placement.items() if p["state"] != "verified" and p.get("why")})
        view = self.view
        wire = {"type": "layer", "space": spaces.of_wire("layer"), "population": self.shown, "style": dict(self.style), "verified": verified,
                "correction": [round(self.correction[0], 1), round(self.correction[1], 1)],
                "view": {"epoch": view[0], "offset": [round(view[1], 2), round(view[2], 2)]} if view is not None else None,
                # A population found on the Stage's still: where the still is in the frames of this view (still_fit.py),
                # mapping its members' original-image pixels to frame pixels; None for every other layer.
                "fit": self.fit.wire() if self.fit is not None and shown is not None else None,
                "why": reasons[0] if reasons else "", "memberChecks": None}
        screen = getattr(self.hub, "screen", None)
        last_change = screen.last_change(f"layer:{self.shown}") if screen is not None and self.shown else None
        if last_change is not None:
            wire["lastChange"] = last_change
        wire["memberChecks"] = self._member_checks(wire, shown, verified)
        return wire

    def _current(self, population, revision, lifetime, placement) -> bool:
        return (self.population() is population and population.revision == revision
                and self._shown_lifetime is lifetime and self.placement is placement)

    def _retained(self, placement, population, at=None) -> bool:
        check = placement.get("check")
        if (population is None or placement.get("state") != "verified" or not isinstance(check, _MemberCheck)
                or check.population() is not population or check.revision != population.revision
                or check.lifetime is not self._shown_lifetime):
            return False
        age = (time.monotonic() if at is None else at) - check.checked_at_mono
        return 0 <= age <= MEMBER_CHECK_S

    def _verified(self, placement, population, info, at=None) -> bool:
        return (self._retained(placement, population, at) and info is not None
                and getattr(info, "tracked", True) is not False and placement["check"].epoch == info.epoch
                and np.hypot(float(info.dx) - placement["dx"], float(info.dy) - placement["dy"]) <= MEMBER_TRAVEL_PX)

    def _member_checks(self, wire, population, verified):
        """Group the complete retained positive set. Collection never renews a sample."""
        if population is None or not verified or len(population.live()) > 512 or len(verified) > 512:
            return None
        samples, rows = {}, []
        for mid in verified:
            placement = self.placement[mid]
            check = placement["check"]
            if check.ref not in samples:
                if len(samples) >= 8:
                    return None
                samples[check.ref] = {"ref": check.ref, "checkedAtMono": check.checked_at_mono,
                                      "checkedBasis": json.loads(check.basis_json)}
            rows.append({"id": mid, "checkRef": check.ref, "score": placement["score"],
                         "placement": {"offset": [placement["dx"], placement["dy"]],
                                       "correction": list(placement["correction"])}})
        proof = {"schema": "member-checks-v1", "populationId": population.id, "revision": population.revision,
                 "samples": list(samples.values()), "members": rows}
        # Use the existing canonical JSON budgets, without installing any Screen/source authority.
        from .workspace_observation import Limits, ObservationError, _preflight

        candidate = {**wire, "memberChecks": proof}
        try:
            _preflight(candidate, Limits(wire_bytes=64 * 1024))
            if len(json.dumps(candidate, allow_nan=False).encode()) > 64 * 1024:
                return None
        except (ObservationError, TypeError, ValueError):
            return None  # complete proof unavailable, never silently truncated
        return proof

    def member_hit(self, info, x: float, y: float) -> Member | None:
        """The shown member drawn under (x, y) in the pixels of frame `info`: only a verified member, in
        the population's epoch (a click inside a shown member selects it; the design's decision)."""
        population = self.population()
        _, vdx, vdy = self._drawn_view(population)
        if info is None or self.drawn_frame_reason(info):
            return None
        if self.fit is not None:  # found on the Stage's still: back through its fit, to the original's pixels
            lx, ly = self.fit.to_source(float(x), float(y))
        else:
            lx = x - (float(info.dx) - vdx) - self.correction[0]
            ly = y - (float(info.dy) - vdy) - self.correction[1]
        hits = [m for m in population.live() if self._verified(self.placement.get(m.id, {}), population, info) and
                any(cv2.pointPolygonTest(np.asarray(poly, np.float32).reshape(-1, 1, 2), (float(lx), float(ly)), False) >= 0
                    for poly in m.polygons if len(poly) >= 3)]
        # Where members overlap (automatic masks, matches), the highest score wins, as in the derived map.
        return max(hits, key=lambda m: float(m.extras.get("score") or 0.0)) if hits else None

    def select(self, member: Member | None) -> None:
        """Select a member (or none): the studio shows it thicker, with its measurements in words."""
        population = self.population()
        words = row_words(population, member, ["areaPx", "feretMaxPx", "elongation", "circularity"], plain=True) \
            if population is not None and member is not None else ""
        self.selected = member.id if member is not None else None
        self.selected_at = time.time()  # the newest thing pointed at is what "that" means (correct_cells)
        self.broadcast({"type": "member-selected", "population": population.id if population is not None else None,
                        "member": self.selected, "words": words})

    def drawable(self, info) -> list:
        """The shown members verified in place, each as (its colour, BGR, as the studio draws it under the layer's
        style; its polygons in the pixels of frame `info`, moved as the slide moved from their capture), for a
        snapshot drawn as the studio draws them: none while the layer is faded out, none the style hides."""
        from .colours import bgr, drawn

        population = self.population()
        _, vdx, vdy = self._drawn_view(population)
        style = self.drawn_style()
        if info is None or self.drawn_frame_reason(info) or not self.drawn_visible(style):
            return []
        sx = float(info.dx) - vdx + self.correction[0]
        sy = float(info.dy) - vdy + self.correction[1]
        fit = self.fit
        out = []
        for m in population.live():
            colour = drawn(m.id, style)
            if colour is not None and self._verified(self.placement.get(m.id, {}), population, info):
                out.append((bgr(colour), [[list(fit.to_frame(x, y)) for x, y in poly] for poly in m.polygons] if fit is not None
                            else [[[x + sx, y + sy] for x, y in poly] for poly in m.polygons]))
        return out

    def drawn_style(self) -> dict | None:
        """The existing Screen report's style, not a requested change waiting for acknowledgement.
        Reports omit the potentially large member values and legend. Recover those only from a style
        with the same reported controls; a new queued style must not recolour yesterday's report.
        Without a Screen (unit/bench consumers), the layer itself is the drawing consumer.
        """
        screen = getattr(self.hub, "screen", None)
        if screen is None:
            return self.style
        item = screen.item(f"layer:{self.shown}")
        if item is None:
            return None
        reported = item.get("style") or {}
        controls = lambda style: {k: v for k, v in style.items() if k not in ("opacity", "values", "legend")}
        style = dict(reported)
        for source in (self.confirmed_style, self.style):
            if controls(source) == controls(reported):
                style = {**source, **reported}
                break
        else:
            if style.get("colour") in ("column", "category") and "values" not in style:
                return None  # the report cannot establish these members' colours
        style["opacity"] = item.get("opacity", 1.0) if item.get("visible", True) else 0.0
        return style

    @staticmethod
    def drawn_visible(style: dict | None) -> bool:
        if style is None:
            return False
        try:
            return float(style.get("opacity", 1.0)) > 0.0
        except (TypeError, ValueError):
            return True

    def visible(self) -> bool:
        """Whether the layer is drawn at all: not faded out to 0."""
        try:
            return float(self.style.get("opacity", 1.0)) > 0.0
        except (TypeError, ValueError):
            return True

    def _view_now(self, population) -> tuple:
        if population is None:
            return (None, 0.0, 0.0)
        view = _ViewOf(population, getattr(self.hub, "epoch_links", None), _epoch_now(self.hub))
        return (view.epoch, view.dx, view.dy)

    def _drawn_view(self, population) -> tuple:
        """The published layer basis, not a rejoin the next member check has yet to publish."""
        if self.view is not None:
            return self.view
        v = population.view if population is not None else {}
        return (v.get("epoch"), float(v.get("dx") or 0.0), float(v.get("dy") or 0.0))

    def drawn_frame_reason(self, info=None) -> str | None:
        """The renderer's immediate frame gate, before the next throttled member check.
        Studio uses the layer view on its wire (else the population's capture epoch) and
        the frame's registration. A known rejoin is not drawn until that layer view is published.
        """
        population = self.population()
        info = getattr(self.hub, "latest_info", None) if info is None else info
        if population is None or info is None:
            return "not in place on the live view now (there is no current view of these cells)"
        if population.view.get("sourceImage"):
            return self._still_frame_reason(population, info)
        if getattr(info, "tracked", True) is False:
            return "not in place on the live view now (the slide's motion is not registered)"
        epoch = self._drawn_view(population)[0]
        if epoch != getattr(info, "epoch", None):
            return "not in place on the live view now (their layer is in another motion epoch)"
        from .apply import geometry_reason

        why = geometry_reason(_ViewOf(population), info, _frame_objective(info))
        if why:
            return f"not in place on the live view now ({why})"
        return None

    def states(self) -> dict[str, str]:
        population, info = self.population(), getattr(self.hub, "latest_info", None)
        at = time.monotonic()
        compatible = self.drawn_frame_reason(info) is None
        return {m: ("verified" if compatible and self._verified(p, population, info, at) else "unverified")
                for m, p in self.placement.items()}

    def verified_count(self) -> tuple[int, int]:
        population = self.population()
        if population is None:
            return 0, 0
        live = population.live()
        now, at = getattr(self.hub, "latest_info", None), time.monotonic()
        verified = sum(1 for m in live if self._verified(self.placement.get(m.id, {}), population, now, at)) \
            if self.drawn_frame_reason() is None else 0
        return verified, len(live)

    def start(self) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if self._task is None or self._task.done():
            self._task = loop.create_task(self._loop())

    async def _loop(self) -> None:
        while self.shown is not None:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # a failed check leaves members unverified; the next one tries again
            await asyncio.sleep(CHECK_EVERY_S)

    async def check(self) -> None:
        """One pass: every shown member checked against the current picture by its own pixels;
        a member whose check has lapsed (time, travel, geometry) and could not be checked again is
        unverified. The studio is told when the check renews, even with the same verified set."""
        from .apply import preconditions
        from .capture import Refused

        population, now = self.population(), getattr(self.hub, "latest_info", None)
        if population is None or now is None:
            self._specimen_overdue()  # no picture on the Stage to check them on: its window is not kept waiting
            return
        store = captures(self.hub)
        latest = store.get(now.id)  # the view now, taken before anything is awaited (ADR 0010)
        now = latest if latest is not None else now
        if population.view.get("sourceImage"):
            await self._check_still(population, now, latest)
            return
        revision, lifetime, previous = population.revision, self._shown_lifetime, self.placement
        members = tuple((m.id, tuple(m.box)) for m in population.live())
        view = _ViewOf(population, getattr(self.hub, "epoch_links", None), _epoch_now(self.hub))
        basis = (view.epoch, view.dx, view.dy)
        correction = self.correction
        scale = copy.deepcopy(latest.scale) if latest is not None else None
        objective = scale.get("objective") if isinstance(scale, dict) else _frame_objective(now)
        why = preconditions(view, now, objective)
        if not why and population.view.get("run") != RUN:
            # Epochs and offsets start again with every start: one from another run (or none recorded, as
            # capture_of reads it back) is not this run's, whatever its number (finding 89).
            why = "they were measured before the app restarted, so where they are on the slide now is not known"
        if not why and not getattr(now, "tracked", True):
            # The offset now was carried over, not measured: nothing is checked against it.
            why = "the slide's motion was not registered on the view now"
        verified: dict[str, dict] = {}
        failed: set[str] = set()
        if not why and latest is not None and latest.jpeg:
            authority = self._templates_authority
            if (self.templates_of != population.id or authority is None or authority[0] is not population
                    or authority[1] != revision or authority[2] is not lifetime):
                self._drop_templates()
                batch = _TemplateBatch(store.memory)
                use = batch.reader()
                template_input = _TemplateInput(population.capture, population.path,
                                                population.view.get("width") or 0,
                                                population.view.get("height") or 0, members, use)
                try:
                    await self._await_pixels(self._templates, template_input, use=use)
                    if not self._current(population, revision, lifetime, previous):
                        return  # only ownership, never publication authority, survived the work
                    self._drop_templates()
                    self._template_batch, self.templates, self.templates_of = batch, batch.templates, population.id
                    self._templates_authority = (population, revision, lifetime)
                    batch = None  # the constructing caller's interest now belongs to the cache
                finally:
                    if batch is not None:
                        batch.release()
            fov = getattr(latest, "fov", None)
            checked_basis = {"frameId": latest.id, "captureId": None, "run": RUN,
                             "boot": getattr(latest, "boot", None), "seq": getattr(latest, "seq", None),
                             "epoch": latest.epoch, "imageSize": [latest.width, latest.height],
                             "disc": [fov.cx, fov.cy, fov.radius] if fov is not None else None,
                             "offset": [float(latest.dx), float(latest.dy)], "objective": objective,
                             "umPerPx": scale.get("umPerPx") if isinstance(scale, dict) else None,
                             "scaleId": None, "scale": scale}
            basis_json = json.dumps(checked_basis, allow_nan=False)
            shift = (float(now.dx) - view.dx, float(now.dy) - view.dy)
            use = self._template_batch.reader()
            work = _MatchInput(use, basis_json, weakref.ref(population), revision, lifetime)
            try:
                verified, failed, correction = await self._await_pixels(self._check_pixels, store, latest,
                                                                        shift, work, correction, use=use)
                if not self._current(population, revision, lifetime, previous):
                    return
            except Refused as exc:
                why = f"its members could not be checked now ({exc})"
        if not self._current(population, revision, lifetime, previous):
            return
        t, mono = time.time(), time.monotonic()
        placement = {mid: previous[mid] for mid, _ in members if mid in previous}
        for mid, _ in members:
            got = verified.get(mid)
            if got is not None:
                placement[mid] = got
                continue
            old = previous.get(mid)
            # Checked this pass and not where its pixels are: unverified at once (contract 3). The time and
            # travel window only carries members this pass could not check (out of the picture, no template).
            lapsed = mid in failed or old is None or not self._verified(old, population, now, mono) or bool(why)
            if lapsed:
                placement[mid] = {"state": "unverified", "at": t, "dx": float(now.dx), "dy": float(now.dy),
                                             "why": why or "its pixels did not match where the slide's move puts it",
                                             # checked on this frame and not there (not merely not checked)
                                             "frame": getattr(now, "id", None) if mid in failed else None}
        # Nothing awaits between committing the complete check basis and publishing it. Until this point,
        # readers and the renderer retain the previous view, placements and correction together.
        self.view, self.placement, self.correction = basis, placement, correction
        # A renewed sample is a publication even when all the same members still match. Carried placements
        # retain their original sample, so collecting or a pass that cannot check does not renew them.
        self._publish(population, now, mono, why)

    def _publish(self, population, now, mono, why) -> None:
        current = (frozenset((m, p["check"].ref) for m, p in self.placement.items()
                             if self._verified(p, population, now, mono)), why or "",
                   (round(self.correction[0], 1), round(self.correction[1], 1)),
                   (self.view[0], round(self.view[1], 2), round(self.view[2], 2)) if self.view else None,
                   self.fit.matrix if self.fit is not None else None)
        if current != self._sent:
            self._sent = current
            self.broadcast(self.wire())

    # --- a population found on the Stage's still ------------------------------------------------------------------------
    def _still_frame_reason(self, population, info) -> str | None:
        """The renderer's frame gate for a population found on a still's original pixels: drawn only on a frame of the
        still it was found on (the frame's own intake tag), of the size and motion epoch its fit was checked on, that
        the motion tracker registered (a cut to the camera first goes unregistered, then starts an epoch)."""
        fit = self.fit
        if fit is None or self.view is None:
            return f"not in place on the Stage now ({KEPT_ON_ORIGINAL.split(';')[0]})"
        if not on_stage(getattr(info, "source_image_json", None), population.view.get("sourceImage")):
            return "not in place on the Stage now (it shows another picture)"
        if (getattr(info, "width", None), getattr(info, "height", None)) != fit.frame:
            return "not in place on the Stage now (the Stage's picture size changed)"
        if getattr(info, "tracked", True) is False or getattr(info, "epoch", None) != self.view[0]:
            return "not in place on the Stage now (the picture on the Stage changed)"
        return None

    async def _check_still(self, population, now, latest) -> None:
        """One pass for a population found on a still's original pixels (`sourceImage`): drawn on the Stage only while
        the Stage shows that still (the frame's intake tag names its asset). Its place is the still's fit (still_fit.py:
        the bridge's orientation, scale and letterbox), and its members are checked together, by the frame's picture
        matching the still's own pixels where the fit puts them (a still never moves, so its members cannot move apart).
        Otherwise it is kept on the original image, as before, and nothing is drawn."""
        from .capture import Refused
        from .still_fit import STILL_SCORE

        source = population.view.get("sourceImage")
        revision, lifetime, previous = population.revision, self._shown_lifetime, self.placement
        members = [m.id for m in population.live()]
        spec = stage_spec(getattr(now, "source_image_json", None))
        fit, matched, why = None, 0.0, ""
        if not on_stage(getattr(now, "source_image_json", None), source):
            why = KEPT_ON_ORIGINAL
        elif getattr(now, "tracked", True) is False:
            why = "the picture on the Stage is changing"
        elif latest is None or not latest.jpeg:
            why = "there is no picture on the Stage to check them on now"
        else:
            try:
                fit, matched, problem = await asyncio.to_thread(self._still_pixels, population, latest, spec, source)
            except Refused as exc:
                fit, why = None, f"its members could not be checked now ({exc})"
            if not self._current(population, revision, lifetime, previous):
                return
            if not why and fit is None:
                why = problem or "the picture on the Stage is not the original image they were found on"
            elif not why and matched < STILL_SCORE:
                why = "the picture on the Stage does not match the original image they were found on"
        if not self._current(population, revision, lifetime, previous):
            return
        t, mono = time.time(), time.monotonic()
        placement: dict[str, dict] = {}
        if not why:
            fov = getattr(latest, "fov", None)
            scale = copy.deepcopy(latest.scale)
            basis = {"frameId": latest.id, "captureId": None, "run": RUN, "boot": getattr(latest, "boot", None),
                     "seq": getattr(latest, "seq", None), "epoch": latest.epoch, "imageSize": [latest.width, latest.height],
                     "disc": [fov.cx, fov.cy, fov.radius] if fov is not None else None,
                     "offset": [float(latest.dx), float(latest.dy)],
                     "objective": scale.get("objective") if isinstance(scale, dict) else None,
                     "umPerPx": scale.get("umPerPx") if isinstance(scale, dict) else None, "scaleId": None, "scale": scale,
                     "fit": fit.wire()}  # the one check of all its members was made with this fit (the proof says it once)
            sample = _MemberCheck("member-check:" + uuid4().hex, mono, json.dumps(basis, allow_nan=False),
                                  weakref.ref(population), revision, lifetime, latest.epoch)
            for mid in members:
                placement[mid] = {"state": "verified", "at": t, "dx": float(latest.dx), "dy": float(latest.dy),
                                  "score": round(matched, 3), "check": sample, "frame": latest.id, "correction": (0.0, 0.0)}
        else:
            for mid in members:
                placement[mid] = {"state": "unverified", "at": t, "dx": float(now.dx), "dy": float(now.dy), "why": why,
                                  "frame": getattr(now, "id", None) if fit is not None else None}
        # Committed together, nothing awaited since: the renderer never sees one view's fit with another's placements.
        self.placement, self.correction = placement, (0.0, 0.0)
        self.view, self.fit = ((latest.epoch, float(latest.dx), float(latest.dy)), fit) if not why else (None, None)
        self._publish(population, now, mono, why)
        awaiting = self._awaiting
        if awaiting is not None and awaiting["population"] == population.id:
            if not why and placement:
                self._settle_specimen("drawn")  # verified on the Stage's still: the specimen is its window
            else:
                self._specimen_overdue(why)

    # --- a This Tab run's window, waiting on its outlines' first check on the Stage's still --------------------------
    def await_specimen(self, population, store, origin: dict, execution: str) -> None:
        """placed_on_stage: the run's window waits on this shown population's first check on the Stage's still, at most
        SPECIMEN_WAIT_S. `store` gives the run's Project's journal when it is decided (workspace_views.specimen)."""
        if self._awaiting is not None and self._awaiting["population"] != population.id:
            self._settle_specimen("placed", "another layer was shown before its outlines were checked on the specimen")
        self._awaiting = {"population": population.id, "store": store, "origin": origin, "execution": execution,
                          "until": time.monotonic() + SPECIMEN_WAIT_S}
        self._specimen_run(origin["run"], "checking")

    def _specimen_overdue(self, why: str = "") -> None:
        awaiting = self._awaiting
        if awaiting is not None and time.monotonic() >= awaiting["until"]:
            self._settle_specimen("placed", why or "its outlines were not checked on the specimen in time")

    def _settle_specimen(self, state: str, why: str = "") -> None:
        """Decide the waiting run's window, once: "drawn" (its outlines verified on the Stage's still: kept in Data, no
        window) or "placed" (its masks window on its Tab, as for any other run). Journaled in its Project
        (workspace_views.specimen, which reconciles at once), and every studio told, so the run's footer says where it
        is (`action-placed`). A journal that cannot be written leaves the run waiting (said as kept in Data, never as on a
        Tab); the next start places it."""
        awaiting, self._awaiting = self._awaiting, None
        if awaiting is None:
            return
        from .workspace_views import specimen

        run = awaiting["origin"]["run"]
        try:
            decided = specimen(awaiting["store"](), awaiting["origin"], awaiting["execution"], awaiting["population"],
                               state, why)
        except (OSError, ValueError):
            return
        if decided not in ("drawn", "placed"):
            return
        self._specimen_run(run, decided)
        self.broadcast({"type": "action-placed", "run": run, "placed": "specimen" if decided == "drawn" else "tab"})

    def _specimen_run(self, run: str, state: str) -> None:
        self.specimen_runs[run] = state
        self.specimen_runs.move_to_end(run)
        while len(self.specimen_runs) > 64:
            self.specimen_runs.popitem(last=False)

    def _still_pixels(self, population, latest, spec, source):
        """In a thread: the still's fit in this frame (the picture file the bridge plays, read for its size,
        orientation and chroma subsampling), how well the frame's picture matches it there, and why there is no fit
        (None: that file is not the original the population was found on, another size)."""
        from . import still_fit
        from .capture import CaptureGone

        path = Path(str(spec["path"]))
        try:
            stat_ = path.stat()
        except OSError:
            return None, 0.0, "the picture on the Stage could not be read to check them"
        key = (population.id, str(path), stat_.st_size, stat_.st_mtime_ns, int(latest.width), int(latest.height))
        cached = self._still
        if cached is None or cached[0] != key:
            layout = still_fit.read_layout(path)
            fit = still_fit.fit(layout, int(latest.width), int(latest.height)) if layout is not None else None
            ref, problem = None, None
            if fit is not None and (layout.width, layout.height) == (source.get("width"), source.get("height")):
                ref = self._still_reference(population, fit)
                problem = None if ref is not None else "their original image could not be read to check them"
            cached = self._still = (key, ref, problem)
        ref = cached[1]
        if ref is None:
            return None, 0.0, cached[2]
        digest = hashlib.blake2b(latest.jpeg, digest_size=16).digest()
        seen = self._still_seen
        if seen is not None and seen[0] == digest and seen[1] == key:
            return ref.fit, seen[2], None  # the bridge plays one picture: the same frame bytes again, the same match
        store = captures(self.hub)
        try:
            with store.hold(latest, "analysis", "hold", "checking the still's members on the Stage"):
                gray = latest.gray()
                matched = still_fit.score(ref, gray)
        except CaptureGone:
            return None, 0.0, "there is no picture on the Stage to check them on now"
        finally:
            gray = None
        self._still_seen = (digest, key, matched)
        return ref.fit, matched, None

    def _still_reference(self, population, fit):
        """The still's own pixels fitted into the frame and reduced (still_fit.reference), from the population's view
        of its original (its preview: the original's pixels, kept with the evidence); None when it cannot be read."""
        from . import still_fit

        jpeg = evidence_of(self.hub).capture_jpeg(population)
        if not jpeg:
            return None
        memory = captures(self.hub).memory
        width, height = int(population.view.get("width") or 0), int(population.view.get("height") or 0)
        with memory.reserve("analysis", width * height + fit.frame[0] * fit.frame[1] * 2, "fitting the still on the Stage"):
            gray = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
            if gray is None or gray.shape[:2] != (height, width):
                return None
            return still_fit.reference(gray, fit)

    def _templates(self, work: _TemplateInput) -> dict[str, tuple]:
        if not work.use.begin():
            return {}
        gray = template = out = None
        try:
            jpeg = evidence_of(self.hub).capture_jpeg(work)
            if jpeg is None:
                return {}
            memory = captures(self.hub).memory
            full = int(work.width) * int(work.height)
            half = ((int(work.width) + 1) // 2) * ((int(work.height) + 1) // 2)
            with memory.reserve("analysis", full + half, "decoding the shown members' templates"):
                try:
                    gray = _half(cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE))
                    out = work.use.batch.templates
                    if gray is None:
                        return out
                    for mid, box in work.boxes:
                        x0, y0, x1, y1 = (v * SCALE for v in box)
                        ox, oy = max(0, int(x0) - MARGIN), max(0, int(y0) - MARGIN)
                        ex, ey = min(gray.shape[1], int(x1) + MARGIN), min(gray.shape[0], int(y1) + MARGIN)
                        if ex - ox >= 6 and ey - oy >= 6:
                            work.use.batch.reservation.grow((ex - ox) * (ey - oy))
                            template = gray[oy:ey, ox:ex].copy()
                            template.setflags(write=False)
                            out[mid] = (template, (ox, oy))
                    return out
                finally:
                    gray = template = None  # the half-frame ends before its temporary allowance
        finally:
            out = None
            work.use.finish()

    def _match(self, gray: np.ndarray, shift: tuple[float, float], now, work: _MatchInput) -> tuple[dict, set, tuple[float, float]]:
        """Verified: the member's own pixels match best AT the place the slide's move puts it (within
        AT_PLACE of a SEARCH-px search), well enough. A thing that moved on its own while the slide was
        still (a template that is mostly its still surroundings matched nearby: sim cells-then-move, a
        member drawn 8.6 px off) is not verified, whatever its neighbourhood does."""
        t = time.time()
        sample = _MemberCheck("member-check:" + uuid4().hex, time.monotonic(), work.basis_json,
                              work.population, work.revision, work.lifetime, now.epoch)
        h, w = gray.shape[:2]
        peaks: dict[str, tuple[float, float, float]] = {}
        checked: list[str] = []  # members in the picture with a template: checked this pass, verified or not
        for mid, (template, origin) in work.templates:
            th, tw = template.shape[:2]
            # Where the slide's move puts it (unrounded), and its peak against that, to a fraction of a pixel.
            fx, fy = origin[0] + shift[0] * SCALE, origin[1] + shift[1] * SCALE
            px, py = int(round(fx)), int(round(fy))
            x0, y0 = px - SEARCH, py - SEARCH
            if x0 < 0 or y0 < 0 or x0 + tw + 2 * SEARCH > w or y0 + th + 2 * SEARCH > h:
                continue
            checked.append(mid)
            res = cv2.matchTemplate(gray[y0:y0 + th + 2 * SEARCH, x0:x0 + tw + 2 * SEARCH], template, cv2.TM_CCOEFF_NORMED)
            _, score, _, loc = cv2.minMaxLoc(res)
            if score < MEMBER_SCORE:
                continue
            sx, sy = _subpixel(res, loc)
            peaks[mid] = (x0 + sx - fx, y0 + sy - fy, float(score))
        offsets = np.array([(p[0], p[1]) for p in peaks.values()], float)
        cx, cy = (np.median(offsets, axis=0) if len(offsets) >= MIN_FOR_CORRECTION else (0.0, 0.0))
        if np.hypot(cx, cy) > MAX_CORRECTION:
            cx, cy = 0.0, 0.0  # more than the slide's offset is ever this far off: no correction
        correction = (float(cx) / SCALE, float(cy) / SCALE)
        out = {}
        for mid, (ox, oy, score) in peaks.items():
            if abs(ox - cx) <= AT_PLACE and abs(oy - cy) <= AT_PLACE:
                out[mid] = {"state": "verified", "at": t, "dx": float(now.dx), "dy": float(now.dy), "score": round(score, 3),
                            "check": sample,
                            "frame": getattr(now, "id", None),  # the very frame its pixels were found on
                            "correction": (float(cx) / SCALE, float(cy) / SCALE)}  # this pass's own (never another's)
        return out, set(checked) - set(out), correction

    # Decorate the named matcher worker boundary with its decode and reservation ownership.
    # Delivery still happens after the real matcher returns; no pixels escape in its result.
    @wraps(_match)
    def _check_pixels(self, store, latest: Capture, shift: tuple[float, float], work: _MatchInput,
                      correction: tuple[float, float]) -> tuple[dict, set, tuple[float, float]]:
        # The CPU callable owns the Hold: cancelling its asyncio caller cannot release it
        # while decode or matching is still using the picture. The caller alone publishes.
        if not work.use.begin():
            return {}, set(), correction
        try:
            with store.hold(latest, "analysis", "hold", "checking the shown members"):
                half = ((int(latest.width) + 1) // 2) * ((int(latest.height) + 1) // 2)
                with store.memory.reserve("analysis", half, "the shown members' half-resolution check"):
                    gray = None
                    try:
                        gray = _half(latest.gray())
                        if gray is None:
                            return {}, set(), correction
                        gray.setflags(write=False)
                        return self._match(gray, shift, latest, work)
                    finally:
                        gray = None  # before both the half allowance and the Capture Hold retire
        finally:
            work.use.finish()  # cache replacement cannot retire this reader's template aliases


def _epoch_now(hub):
    """The epoch the view is in now (None: no frame yet), where a rejoin's carry stops."""
    info = getattr(hub, "latest_info", None)
    return getattr(info, "epoch", None)


class _ViewOf:
    """A population's view as frame facts, for apply.preconditions: in the epoch a verified rejoin
    carried it to, if one did (hub.epoch_links), with its offset translated by the rejoin's delta."""

    def __init__(self, population: Population, links: dict | None = None, now=None) -> None:
        from .epochs import follow_links

        v = population.view
        self.epoch, self.dx, self.dy = follow_links(links, v.get("epoch"), float(v.get("dx") or 0.0),
                                                    float(v.get("dy") or 0.0), now=now)  # never past `now`, as outlines
        self.width, self.height, self.disc = v.get("width"), v.get("height"), v.get("discPx") or 0.0
        self.scale = population.scale_id


def _subpixel(res: np.ndarray, loc: tuple[int, int]) -> tuple[float, float]:
    """The peak of a match surface to a fraction of a pixel (a parabola through it and its neighbours)."""
    x, y = loc

    def offset(a: float, b: float, c: float) -> float:
        d = a - 2 * b + c
        return 0.0 if d == 0 else max(-0.5, min(0.5, 0.5 * (a - c) / d))

    h, w = res.shape[:2]
    dx = offset(res[y, x - 1], res[y, x], res[y, x + 1]) if 0 < x < w - 1 else 0.0
    dy = offset(res[y - 1, x], res[y, x], res[y + 1, x]) if 0 < y < h - 1 else 0.0
    return x + dx, y + dy


def _half(gray):
    if gray is None:
        return None
    return cv2.resize(gray, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)


def layer_of(hub) -> Layer:
    found = getattr(hub, "layer", None)
    if not isinstance(found, Layer):
        found = Layer(hub)
        try:
            hub.layer = found
        except AttributeError:
            pass
    return found


# --- what the brain is told (contract 9) ------------------------------------------------------------
def evidence_lines(hub, thread: str | None = None, most: int = 5) -> tuple[str, ...]:
    """The evidence the brain sees on its board (contract 9): what is shown over the live view, what
    is being made now, and what this conversation made (populations and motion tracks), newest first,
    at most `most` lines. Each says
    what made it, when, where it is shown, its revision and whether it is saved; the rest is reached
    through query_population."""
    from .operations import runner

    lines: list[str] = []
    layer = getattr(hub, "layer", None)
    shown = layer.population() if isinstance(layer, Layer) else None
    if shown is not None:
        verified, total = layer.verified_count()
        from .colours import words as colouring

        # How the layer is coloured, as the studio draws it: what the outlines' colours are (going live, G3).
        style = layer.drawn_style()
        drawn = layer.drawn_visible(style) and layer.drawn_frame_reason() is None
        still = bool(shown.view.get("sourceImage"))  # found on a still's original: drawn only on the Stage's still
        where = ("drawn on the picture on the Stage" if still else "shown over the live view") if drawn \
            else ("not drawn on the Stage now" if still else "not drawn over the live view now")
        colours = colouring(style) if style is not None else "its drawn colours not confirmed by the screen"
        kept = "kept on the original image" if still else "kept on the view they were made on"
        lines.append(shown.line(f"{where}, {colours}, {verified} verified in place, "
                                f"{total - verified} unverified (not drawn; {kept})"))
    jobs = getattr(getattr(hub, "runner", None), "jobs", None) or {}
    for job in list(jobs.values()):
        if job.execution.operation == "cells" and not job.task.done() and job.execution.origin != "background":
            # (a researcher's own run is not one for this conversation: its population is made for it)
            started = time.strftime("%H:%M:%S", time.localtime(job.execution.at))
            lines.append(f"segmenting every cell now with {job.execution.requested} (since {started}); "
                         "its population comes when it is done")
    # This conversation's populations and motion tracks (M3.2; the tracks shown over the live view first),
    # newest first.
    from .motiontracks import lines as tracks_lines

    kept: list[tuple[float, str]] = []
    store = getattr(hub, "evidence", None)
    if store is not None and hasattr(store, "items"):
        for population in reversed(list(store.items.values())):
            if shown is not None and population.id == shown.id:
                continue
            if thread is not None and (population.thread != thread or not current_generation(hub, population.generation)):
                continue  # another conversation's, or an earlier generation's that only shares its id
            if population.made_for not in (None, "a comparison"):
                # Made for a researcher's own reading: never on the user's board unless shown. A named comparison's
                # stay, as on main: the user asked for them (#186 re-check SF2).
                continue
            kept.append((float(population.made_at), population.line("not shown")))
    kept += reversed(tracks_lines(hub, thread))
    lines.extend(line for _at, line in sorted(kept, key=lambda k: -k[0]))  # stable: equal times stay newest first
    return tuple(lines[:most])


# --- promoting members to outlines ------------------------------------------------------------
def capture_of(hub, population: Population, max_bytes: int | None = None) -> Capture | None:
    """The population's view as a Capture: still resident, or read back from its saved JPEG."""
    if population.capture is not None:
        return population.capture
    jpeg = evidence_of(hub).capture_jpeg(population, max_bytes=max_bytes)
    if jpeg is None:
        return None
    from types import SimpleNamespace

    from .fov import FieldOfView

    v = population.view
    native = evidence_of(hub).capture_native(population, max_bytes=max_bytes)
    fov = FieldOfView(*v["disc"], 1.0) if v.get("disc") else None
    info = SimpleNamespace(id=population.capture_id, width=v["width"], height=v["height"], epoch=v["epoch"], dx=v["dx"],
                           dy=v["dy"], fov=fov, disc=v.get("discPx") or 0.0, wall_at=v.get("at"), seq=v.get("seq"),
                           boot=v.get("boot"), tracked=bool(v.get("tracked", True)))
    # The live capture of that frame only in the run it was made in (Captures.read_back).
    return captures(hub).read_back(info, jpeg, population.scale_id, v.get("run"), native=native,
                                   source_image=v.get("sourceImage"))


async def promote(hub, population: Population, member_ids: list[str], why: str = "", label: str = "", *,
                  require_live: bool = False) -> list:
    """Members as ordinary outlines: each member's own outline, a template from its capture, "made
    by" measured; placed live only where verified (contract 3), through apply.apply. Each member is
    linked to its outline (same object, by promotion). With `require_live`, refuse before insertion
    if placement cannot be verified; a paired magnifier cannot show a historical cell beside a live one."""
    from .tools import ToolError, add_outline

    capture = capture_of(hub, population)
    if capture is None:
        raise ToolError(f"the view {population.id} was made on is not available",
                        next="ask me to outline them again on the view now")
    evidence = evidence_of(hub)
    # A member the shown layer has just verified in place by its own pixels (contract 3) is placed where the
    # layer draws it, on the view now: verified, so it is drawn at once (acceptance flow 9 on footage: five
    # cells drawn on the layer were outlined "not where the live view can show it" until re-found).
    layer = layer_of(hub)
    live, base = None, (0.0, 0.0)
    if layer.shown == population.id:
        # Only a placement found by the member's own pixels on the very frame it is placed on (contract 3;
        # M1.5c re-check N2). The view now is taken first (invariant 1); when the last check was on another
        # frame, the layer checks again, and only a check on that same frame counts.
        now = getattr(hub, "latest_info", None)
        live = captures(hub).get(now.id) if now is not None else None
        if live is not None and any((layer.placement.get(m) or {}).get("frame") != live.id for m in member_ids):
            await layer.check()
        epoch, vdx, vdy = layer._view_now(population)
        if live is not None and getattr(live, "epoch", None) == epoch:
            base = (float(live.dx) - vdx, float(live.dy) - vdy)
        else:
            live = None
    # Each member re-found by its own pixels, cut tight from the view it was made on (never mostly its
    # surroundings: a swimmer that left matched them; M1.5c re-check N2).
    templates = await asyncio.to_thread(_own_templates, hub, population, capture, member_ids)
    made = []
    for mid in member_ids:
        member = population.members.get(mid)
        if member is None or member.status in ("deleted", "merged") or not member.polygons:
            continue
        made_by = {**population.made_by, "how": "measured", "evidence": population.id, "member": mid}
        if why:
            made_by["basis"] = why
        on, polygons, own, unplaced = capture, member.polygons, templates.get(mid), None
        checked = layer.placement.get(mid) or {}
        if live is not None and checked.get("frame") == live.id:
            if checked.get("state") == "verified":
                cx, cy = checked.get("correction") or (0.0, 0.0)  # the correction of the check that found it
                shift = (base[0] + cx, base[1] + cy)
                on = live
                polygons = [[[x + shift[0], y + shift[1]] for x, y in poly] for poly in member.polygons]
                if own is not None:
                    own = (own[0], (own[1][0] + shift[0], own[1][1] + shift[1]))
            else:
                unplaced = checked.get("why") or "its pixels are not where the slide's move puts it now"
        from .known import promoted_record

        obj = await add_outline(hub, on, polygons, source=f"ai:measured:{population.id}", label=label,
                                status="likely", made_by=made_by, unplaced=unplaced, template=own,
                                naming=promoted_record(label=label, population=population.id, member=mid, basis=why),
                                note=f"{mid} of {population.id} ({population.implementation})" + (f": {why}" if why else ""),
                                require_verified=require_live)
        evidence.link(population, "same-object", mid, obj.id, "promotion")
        made.append(obj)
    return made


OWN_MARGIN = 4  # px of the view around a member's own pixels in its template (the layer's checks use the same)


def _own_templates(hub, population: Population, capture, member_ids: list[str]) -> dict[str, tuple]:
    """Each member's own pixels, cut tight (OWN_MARGIN) from the view it was made on: {member: (gray, (x, y)
    in that view's px)}; a member whose view cannot be read now gets none (the usual crop then)."""
    from .capture import CaptureGone, Refused, decoding

    try:
        with decoding(hub, capture, "look", owner="a cell's own pixels") as held:
            gray = held.gray()
    except (CaptureGone, Refused):
        return {}
    if gray is None:
        return {}
    h, w = gray.shape[:2]
    out = {}
    for mid in member_ids:
        member = population.members.get(mid)
        if member is None:
            continue
        x0, y0, x1, y1 = member.box
        ax, ay = max(0, int(x0) - OWN_MARGIN), max(0, int(y0) - OWN_MARGIN)
        bx, by = min(w, int(np.ceil(x1)) + OWN_MARGIN), min(h, int(np.ceil(y1)) + OWN_MARGIN)
        if bx - ax >= 6 and by - ay >= 6:
            out[mid] = (gray[ay:by, ax:bx].copy(), (ax, ay))
    return out


MAX_HIGHLIGHT = 15
GUESS_IOU = 0.3  # a member and an outline this much alike (box IoU, on the slide) are taken as the same thing


def _on_slide(box, dx: float, dy: float) -> tuple[float, float, float, float]:
    """A box in a frame's px, in slide px (where it stays put): drawn at p + (offset now - its frame's offset)."""
    return (box[0] - dx, box[1] - dy, box[2] - dx, box[3] - dy)


def _iou(a, b) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    if w <= 0 or h <= 0:
        return 0.0
    inter = w * h
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter)


def member_at(population: Population, obj) -> tuple[Member | None, str, float]:
    """The live member an outline (a look's first guess) is, by where both lie on the slide: the one whose
    box overlaps it most, at least GUESS_IOU; else None and why; and that overlap (box IoU), for the log
    (M2: a provisional answer is checked by the measurement, never assumed right)."""
    v = population.view
    if getattr(obj, "anchor_epoch", None) != v.get("epoch"):
        return None, "the slide lost track between the guess and the measured view, so they can't be compared", 0.0
    guess = _on_slide(obj.bbox, *obj.anchor_offset)
    best, score = None, 0.0
    for m in population.live():
        s = _iou(_on_slide(m.box, float(v.get("dx") or 0.0), float(v.get("dy") or 0.0)), guess)
        if s > score:
            best, score = m, s
    if best is None or score < GUESS_IOU:
        return None, "no measured cell lies where the guess is now", score
    return best, "", score


def member_bgr(hub, population: Population, member_id: str) -> tuple[int, int, int]:
    """The colour a member is outlined in on its captured view: as the layer draws it while its population is the one
    shown (colours.drawn under the layer's style: chosen as "the red ones", it is red there too; the review of G3,
    B1), else its own colour from its id; one the style hides is shown in its own colour."""
    from .colours import bgr, drawn, own

    layer = getattr(hub, "layer", None)
    style = layer.drawn_style() if isinstance(layer, Layer) and layer.shown == population.id else None
    return bgr(drawn(member_id, style) or own(member_id))


def captured_picture(hub, population: Population, member_ids: list[str], *, outlines: bool = True,
                     max_bytes: int | None = None, with_facts: bool = False, retained=None) -> bytes | tuple[bytes, dict] | None:
    """The members on the view they were made on (contract 2's captured view: their own pixels, not
    where the live view is now), each outlined in the colour it is drawn in (member_bgr), cropped around them;
    a JPEG or None."""
    from .capture import CaptureGone, Refused, decoding

    capture = capture_of(hub, population, max_bytes=max_bytes)
    members = [population.members[m] for m in member_ids if m in population.members]
    if capture is None or not members:
        return None
    # Freeze the producing basis, including a read-back's original run/frame (its local
    # negative Capture id is not that original frame). Never recover these from the live hub.
    import copy
    view, scale = copy.deepcopy(population.view), copy.deepcopy(population.scale_id)
    revision, boxes = population.revision, [tuple(m.box) for m in members]
    try:
        with decoding(hub, capture, "look", owner="a captured view") as held:
            image = held.bgr()
            if image is None:
                return None
            h, w = image.shape[:2]
            x0 = min(b[0] for b in boxes); y0 = min(b[1] for b in boxes)
            x1 = max(b[2] for b in boxes); y1 = max(b[3] for b in boxes)
            pad = max(40.0, 0.25 * max(x1 - x0, y1 - y0))
            cx0, cy0 = int(max(0, x0 - pad)), int(max(0, y0 - pad))
            cx1, cy1 = int(min(w, x1 + pad)), int(min(h, y1 + pad))
            if retained is not None:
                retained.grow((cx1 - cx0) * (cy1 - cy0) * 11 + 4096, "retain a clean cell crop")
            out = image[cy0:cy1, cx0:cx1].copy()
            from .drawing import outline

            for m in members if outlines else []:
                pts = [np.round(np.asarray(p, float) - (cx0, cy0)).astype(np.int32).reshape(-1, 1, 2) for p in m.polygons]
                outline(out, pts, member_bgr(hub, population, m.id), 2)  # on a dark casing (drawing.py)
            ok, jpeg = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 92])
            data = jpeg.tobytes() if ok else None
            del image, out, jpeg  # the decode/copy are gone before their hold is released
    except (CaptureGone, Refused):
        return None
    if data is None:
        return None
    if not with_facts:
        return data
    return data, {"frame": population.capture_id, "run": view.get("run"),
                  "seq": view.get("seq"), "boot": view.get("boot"), "at": view.get("at"),
                  "population": population.id, "members": list(member_ids), "revision": revision,
                  "scale": scale, "originalSize": [w, h], "naturalSize": [cx1 - cx0, cy1 - cy0],
                  "rect": [cx0, cy0, cx1, cy1], "cropAffine": [1, 0, 0, 1, -cx0, -cy0],
                  "padding": {"pixels": pad, "rule": "max(40, 0.25 * longest member-box side)"},
                  "clipped": [x0 - pad < 0, y0 - pad < 0, x1 + pad > w, y1 + pad > h],
                  "quality": 92, "outlines": outlines}


# --- corrections (M2): a new revision, revision 0 never changed --------------------------------------
CHANGES = ("remove", "merge", "undo")


class CorrectionRefused(ValueError):
    def __init__(self, message: str, next: str | None) -> None:
        super().__init__(message)
        self.next = next


def _plain_ids(ids: list[str]) -> str:
    return ids[0] if len(ids) == 1 else ", ".join(ids[:-1]) + " and " + ids[-1]


def _union(masks: list[Mask]) -> Mask:
    """The members' masks as one, at full capture resolution."""
    from .evidence import _full_res

    boxes = [m.box for m in masks]
    x0, y0 = int(np.floor(min(b[0] for b in boxes))), int(np.floor(min(b[1] for b in boxes)))
    x1, y1 = int(np.ceil(max(b[2] for b in boxes))), int(np.ceil(max(b[3] for b in boxes)))
    canvas = np.zeros((y1 - y0, x1 - x0), bool)
    for m in masks:
        full = _full_res(m)
        ox, oy = int(round(m.x0)) - x0, int(round(m.y0)) - y0
        h, w = min(full.shape[0], canvas.shape[0] - oy), min(full.shape[1], canvas.shape[1] - ox)
        canvas[oy:oy + h, ox:ox + w] |= full[:h, :w]
    return Mask(canvas, x0, y0, 1.0)


def first_segmented(population: Population) -> int:
    """How many members the model segmented (revision 0): every member no correction added."""
    added = {a for r in population.revisions for a in (r.get("added") or [])}
    return sum(1 for mid in population.members if mid not in added)


def prepare_correction(hub, population: Population, change: str):
    """The heavy part of a correction, off the event loop (M2.3 review S5): every released mask read back
    (coverage and the label map need them all, B2), and the view's grey picture when cells are measured again."""
    from .brain.registry import ToolFailed
    try:
        evidence_of(hub).require_retained(population)
    except ToolFailed as exc:
        raise CorrectionRefused(f"their retained data could not be read back from disk: {exc}", next=exc.next) from None
    if any(m.mask is None for m in population.members.values()):
        if not evidence_of(hub).reload_masks(population):
            raise CorrectionRefused("their masks could not be read back from disk", next="outline the cells again")
    return _gray(hub, population) if change in ("merge", "undo") else None


def correct(hub, population: Population, change: str, member_ids: list[str], by: str, why: str = "",
            gray=..., broadcast: bool = True, asked: str = "", thread: str | None = None) -> tuple[str, list[str]]:
    """A correction the user asked for (M2): remove members ("that's not a cell"), merge members into one
    ("these two are one cell"), or undo the latest correction. A new revision each time: revision 0, the
    model's own output, never changes (its masks stay saved); only the members touched are measured again.
    Returns the words for it and the members whose outlines (promoted) no longer stand."""
    # Its masks stay in memory until it is done: off the loop a save finishing on its writer thread would release
    # them between reading them back and using them (the tool holds them already; this is for direct callers).
    evidence = evidence_of(hub)
    revision = population.revision
    with evidence.keeping_pixels(population):
        # Disk reads and image decoding happen before the revision transaction.
        if gray is ...:
            gray = prepare_correction(hub, population, change)
        elif any(m.mask is None for m in population.members.values()) and not evidence.reload_masks(population):
            raise CorrectionRefused("their masks could not be read back from disk", next="outline the cells again")
        try:
            with evidence.exact_revision(population, revision):
                return _correct(hub, population, change, member_ids, by, why, gray, broadcast, asked, thread)
        except ValueError as exc:
            if isinstance(exc, CorrectionRefused):
                raise
            raise CorrectionRefused(str(exc), next="inspect the current revision before correcting again") from exc


def _correct(hub, population: Population, change: str, member_ids: list[str], by: str, why: str, gray,
             broadcast: bool, asked: str, thread: str | None) -> tuple[str, list[str]]:
    evidence = evidence_of(hub)
    if gray is ...:  # called without the prepared part (tests, a script): done here
        gray = prepare_correction(hub, population, change)
    elif any(m.mask is None for m in population.members.values()) and not evidence.reload_masks(population):
        # Released after they were read back (the caller holds them with `keeping_pixels`; this is the last guard):
        # coverage and a merge need every mask, so nothing is changed without them.
        raise CorrectionRefused("their masks could not be read back from disk", next="outline the cells again")
    live = {m.id for m in population.live()}
    gone: list[str] = []
    if change == "undo":
        undone = {r.get("undoes") for r in population.revisions if r.get("undoes")}
        # The latest correction made in the conversation asking (a correction made in another one is not "that";
        # the M2.3 re-check), or in any when it is not said.
        last = next((r for r in reversed(population.revisions)
                     if (r.get("was") or r.get("added")) and not r.get("undoes") and r["r"] not in undone
                     and (thread is None or (r.get("thread") or population.thread) == thread)), None)
        if last is None:
            elsewhere = any((r.get("was") or r.get("added")) and not r.get("undoes") and r["r"] not in undone
                            for r in population.revisions)
            raise CorrectionRefused(f"{population.title} has no correction made in this conversation to undo"
                                    + (" (its corrections were made in another one)" if elsewhere else "") if thread is not None
                                    else f"{population.title} has no correction to undo", next=None)  # no next: nothing to do here
        was = {mid: population.members[mid].status for mid in [*last.get("was", {}), *last.get("added", [])] if mid in population.members}
        for mid, status in (last.get("was") or {}).items():
            if mid in population.members:
                population.members[mid].status = status
        for mid in last.get("added") or []:
            if mid in population.members:
                population.members[mid].status = "deleted"
                gone.append(mid)
        evidence.revise(population, f"undid revision {last['r']}", by, list(was), gray=gray, was=was,
                        undoes=last["r"], asked=asked, thread=thread)
        back = [mid for mid, status in (last.get("was") or {}).items() if status not in ("deleted", "merged")]
        words = f"your correction {last['r']} undone" + (f": {_plain_ids(back)} back" if back else "")
    else:
        ids = [m for m in dict.fromkeys(str(i) for i in member_ids) if m]
        missing = [m for m in ids if m not in live]
        if not ids:
            raise CorrectionRefused("which cells? none was named or selected", next="click the cell, then say what is wrong with it")
        earlier = [m for m in missing if m in population.members]
        if missing and len(earlier) == len(missing):  # taken out by an earlier correction: done, not to be clicked again
            how = {"deleted": "removed", "merged": "merged into another"}
            said = "; ".join(f"{mid} was already {how.get(population.members[mid].status, 'changed')}" for mid in earlier)
            raise CorrectionRefused(f"{said} in {population.title} ({population.id}) by an earlier correction",
                                    next=None)  # no next: already done
        if missing:
            raise CorrectionRefused(f"{', '.join(missing)} {'is' if len(missing) == 1 else 'are'} not among "
                                    f"{population.title} ({population.id})'s cells now", next="click the cell you mean")
        was = {mid: population.members[mid].status for mid in ids}
        if change == "remove":
            for mid in ids:
                population.members[mid].status = "deleted"
            gone = ids
            evidence.revise(population, f"removed {', '.join(ids)}" + (f": {why}" if why else ""), by, ids,
                            gray=None, was=was, asked=asked, thread=thread)
            words = f"{_plain_ids(ids)} removed" + (f" ({why})" if why else "")
        elif change == "merge":
            if len(ids) < 2:
                raise CorrectionRefused("merging takes two or more cells", next="say which cells are one")
            members = [population.members[mid] for mid in ids]
            if any(m.mask is None for m in members):
                raise CorrectionRefused("their masks are not available to merge", next=None)  # no next: they could not be read back
            union = _union([m.mask for m in members])
            new = Member(evidence.member_id(population.session or None), 0, union, polygons_of(union), union.box,
                         {"mergedFrom": ids, "by": by}, status="added")
            population.members[new.id] = new
            for m in members:
                m.status = "merged"
            gone = ids
            evidence.revise(population, f"merged {', '.join(ids)} into {new.id}" + (f": {why}" if why else ""), by,
                            [new.id, *ids], gray=gray, was=was, added=[new.id], asked=asked, thread=thread)
            pieces = (population.value(new.id, "components") or 1)
            area = population.value(new.id, "areaPx")
            words = (f"{_plain_ids(ids)} merged into one" + (f" ({why})" if why else "") +
                     (f"; they do not touch, so it is {pieces} pieces" if pieces and pieces > 1 else "") +
                     (f"; its area {area:,.0f} px²" if isinstance(area, (int, float)) else ""))
        else:
            raise CorrectionRefused(f"a correction is one of {', '.join(CHANGES)}", next=None)  # no next: the model's own call
    layer = layer_of(hub)
    if layer.selected in gone:  # a removed or merged cell is no longer selected (M2.3 review S1)
        layer.selected = None
    layer.forget_stale_style(population)  # coloured by a group made before this revision: each cell's own colour again
    if layer.shown == population.id:
        layer._drop_templates()  # a merged member gets its own; CPU readers retain the old copies until done
        for mid in gone:
            layer.placement.pop(mid, None)
        if broadcast:
            layer.broadcast(layer.wire())
    # Revision 0 is kept once a save of it finished (its folder), whatever the save this correction started is doing (re-check S6).
    kept = "kept" if population.path is not None else "being saved" if population.status == "saving" else "kept here (not saved)"
    return (f"{population.title} ({population.id}): {words}; {population.count_words()} "
            f"(what the model segmented is {kept} as revision 0)"), gone


def _gray(hub, population: Population):
    """The population's view in grey, for measuring a member again (None when it cannot be read)."""
    from .capture import CaptureGone, Refused, decoding

    capture = capture_of(hub, population)
    if capture is None:
        return None
    try:
        with decoding(hub, capture, "look", owner="measuring a corrected member") as held:
            return held.gray()
    except (CaptureGone, Refused):
        return None


# --- finding a population, selecting and describing members (contract 9) -------------------------
COLUMNS = {"area": "areaPx", "size": "areaPx", "perimeter": "perimeterPx", "length": "feretMaxPx", "feret": "feretMaxPx",
           "width": "feretMinPx", "elongation": "elongation", "roundness": "circularity", "circularity": "circularity",
           "solidity": "solidity", "intensity": "intensity", "brightness": "intensity", "score": "score"}
UNITS = {"areaPx": "px²", "holeAreaPx": "px²", "perimeterPx": "px", "feretMaxPx": "px", "feretMinPx": "px"}
ROWS_MAX = 20
# Not a measurement: the colour a member is drawn in now, by its plain name (colours.py). `where` takes it as
# {"colour": "red"}, resolved against the layer shown and its style (going live, G3).
SHOWN_COLOUR = "shownColour"


def resolve(hub, ref: str | None, ctx=None) -> Population | None:
    """The population a request means: "@n" (what step n of this request produced), an id ("e3"),
    or, with nothing named, the one shown, else this conversation's newest, else the newest."""
    evidence = evidence_of(hub)
    ref = (ref or "").strip()
    if ref.startswith("@") and ctx is not None:
        from .brain.registry import ToolFailed, step_output

        made = step_output(ctx, ref)  # or ToolFailed with why it names nothing
        found = evidence.get(made)
        if found is None:
            raise ToolFailed(f"{ref} is {made}, which is not a population of cells",
                             next=None)  # no next: the model's own call; it corrects it
        return found
    if ref and ref[0] == "e" and ref[1:].isdigit():
        return evidence.get(ref)
    from .ticket import reusable

    from .ticket import chosen

    def usable(p, shown: bool = False) -> bool:  # under a request's choice of model (`use`), only what that model made stands in
        if p is None:
            return False
        config = ((p.producer or {}).get("configuration") or {}) if isinstance(p.producer, dict) else {}
        if not shown and p.operation == "find-like" and config.get("text") and not config.get("box"):
            return False  # matches for words (M3.1): "the cells" only when shown or named (review S3)
        if not shown and p.made_for:  # made for a comparison or a researcher: by its id, "@n" or shown, never "the cells"
            return False  # made for a named comparison: by its id, "@n" or shown, never "the cells" (M2.2 review B3)
        group = "find-like" if p.operation == "find-like" else "cells"
        other = "cells" if group == "find-like" else "find-like"
        if chosen(other) and not chosen(group):
            return False  # the request chose a model for the other kind: this kind does not stand in for it
        return reusable(group, p.made_by)

    shown = layer_of(hub).population()
    if usable(shown, shown=True):  # what is shown stands, whatever made it (M2.2 re-check, SF1)
        return shown
    thread = getattr(getattr(ctx, "asked", None), "thread", None)
    every = [p for p in evidence.items.values() if usable(p)]
    mine = [p for p in every if thread and p.thread == thread and current_generation(hub, p.generation)]
    return (mine or every or [None])[-1]


def nothing_here(hub) -> str:
    """Why no population stands in for "the cells": none made, or only ones made for someone else, a comparison
    or a researcher (named by their ids then, so one can be named)."""
    made: dict[str, list[str]] = {}
    for p in sorted(evidence_of(hub).items.values(), key=lambda p: p.made_for != "a comparison"):  # comparisons first
        if p.made_for:
            made.setdefault(p.made_for, []).append(p.id)
    if made:
        (first, ids), *rest = made.items()
        more = "".join(f", and for {who} ({', '.join(others[-4:])})" for who, others in rest)
        return (f"only cells segmented for {first} are here ({', '.join(ids[-4:])}){more}; they stand in only when "
                "named, so name one, or outline every cell")
    return "no cells have been segmented here yet (outline_cells segments every cell, about 1-8 s)"


def column_of(name: str | None, population: Population | None = None, hub=None) -> str | None:
    """The column a word names: a measurement's plain word ("size" is areaPx); with the population, any column its
    groups have, its case kept ("meanred" is "meanRed"), and a look group or your label from the one grouping
    every reader uses ("group@dinov2-small"; enrichment.resolve_column). ToolFailed when the population has it
    only from before a correction (K1 review B3, SF3). Otherwise the word as given (its case kept)."""
    if not name:
        return None
    text = str(name).strip()
    if text.lower().replace("color", "colour").replace("_", " ") in ("colour", "shown colour", "drawn colour", "showncolour"):
        return SHOWN_COLOUR  # the colour a member is drawn in now, not a column it has (going live, G3)
    if text.lower() in COLUMNS:
        return COLUMNS[text.lower()]
    if population is not None:
        from .enrichment import resolve_column

        found = resolve_column(hub, population, text)
        if found:
            return found
    return text


def column_words(column: str | None) -> str:
    """A column in the user's words: "area", "look group (DINOv2 small)"."""
    if not column:
        return ""
    base, _, model = str(column).partition("@")
    plain = PLAIN_COLUMNS.get(base, base)
    if model:
        from .enrichment import model_label

        return f"{plain} ({model_label(model)})"
    return plain


def drawn_colours(population: Population, layer) -> tuple[dict[str, str], dict[str, str], str]:
    """The colour each member is drawn in NOW, by its plain name (colours.py): only members the studio draws, verified
    in place, on a layer not faded out (`population.ts` drawable; the review of G3, B1). Also the colour the others
    would be drawn in, and why they are not drawn now. Members the style hides are in neither. A population not
    shown is drawn in no colour: ToolFailed."""
    from .brain.registry import ToolFailed
    from .colours import drawn, name_of

    if layer is None or getattr(layer, "shown", None) is None:
        raise ToolFailed("no cells are shown now, so none is drawn in any colour", next="show the cells, then ask again")
    if layer.shown != population.id:
        raise ToolFailed(f"{population.title} is not shown now, so it is drawn in no colour", next="show it, then ask again")
    style = layer.drawn_style()
    if style is None:
        raise ToolFailed("the screen has not confirmed these cells' drawn colours", next="show the cells, then ask again")
    visible = layer.drawn_visible(style)
    frame_reason = layer.drawn_frame_reason()
    info, at = getattr(layer.hub, "latest_info", None), time.monotonic()
    now, not_now = {}, {}
    for m in population.live():
        colour = drawn(m.id, style)
        if colour is None:
            continue
        placed = layer._verified(layer.placement.get(m.id, {}), population, info, at)
        (now if visible and frame_reason is None and placed else not_now)[m.id] = name_of(colour)
    why = "the layer is faded out" if not visible else frame_reason or "not in place on the live view now"
    return now, not_now, why


def _counts(names) -> str:
    counts: dict[str, int] = {}
    for name in names:
        counts[name] = counts.get(name, 0) + 1
    return ", ".join(f"{name} {n}" for name, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def _drawn_in(population: Population, members: list[Member], wanted, layer, notes: list | None = None
              ) -> tuple[list[Member], str]:
    """`members` drawn now in the colour(s) asked for. The facts go with it (`notes`): the other colours drawn, with
    counts, and how many more of that colour are not drawn now, and why; the model judges what the user's word
    meant. A colour none is drawn in is refused with the colours that are."""
    from .brain.registry import ToolFailed
    from .colours import asked

    names = list(dict.fromkeys(asked(w) for w in (wanted if isinstance(wanted, (list, tuple)) else [wanted])
                               if str(w or "").strip()))
    now, not_now, why = drawn_colours(population, layer)
    there = _counts(now.values())
    later = sum(1 for n in not_now.values() if n in names)
    said = " or ".join(names)
    faded = why == "the layer is faded out"
    reason = why if faded else f"{'it is' if later == 1 else 'they are'} {why}"
    if not names:
        raise ToolFailed("which colour?", next=f"say one they are drawn in: {there}" if there else "say a colour")
    if not any(n in names for n in now.values()):
        held = f" ({later} would be, but {reason})" if later else ""
        if there:
            raise ToolFailed(f"none of {population.title} is drawn {said} now{held}; they are drawn {there}",
                             next="say one of those colours")
        raise ToolFailed(f"none of {population.title} is drawn on the live view now{held or f' ({why})'}",
                         next="fade the cells in again, then ask again" if faded
                         else "hold the slide still a moment, then ask again")
    if notes is not None:
        others = _counts(n for n in now.values() if n not in names)
        if others:
            notes.append(f"also drawn: {others}")
        if later:
            notes.append(f"{later} more would be {said} but {reason}")
    return [m for m in members if now.get(m.id) in names], f"drawn {said}"


def value(population: Population, member: Member, column: str):
    if column == "score":
        return member.extras.get("score")
    return population.value(member.id, column)


def _retired_words(population: Population, column: str, wanted: list[str]) -> str:
    """A group name asked for that is no longer one (the second K1 re-check's note): replaced by a newer name for
    its cells, or dropped when none of its cells was left, said with the answer, so "0 members" is never read as
    no cell being it."""
    from .untrusted import inline

    _gid, group = population.column_group(column)
    retired = (group or {}).get("retired") or {}
    names_now = {e.get("name") for e in (group or {}).get("named", [])}
    out = []
    for name in wanted:
        was = retired.get(name)
        if was is None or name in names_now:
            continue
        out.append(f"\"{inline(name, 60)}\" was replaced by \"{inline(was.get('by', ''), 60)}\" for its example cells"
                   if was.get("how") == "replaced" else
                   f"\"{inline(name, 60)}\" was dropped: none of its example cells is left after your corrections")
    return "".join(f"; {o}" for o in out)


def _plain_number(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else str(v)


def _number(bound, column: str):
    """A range's bound as a number ("100" is 100, as before K1); ToolFailed in words when it is not one."""
    from .brain.registry import ToolFailed

    if bound is None:
        return None
    try:
        return float(bound)
    except (TypeError, ValueError):
        raise ToolFailed(f"{bound!r} is not a number, and {column_words(column)} is a measured number",
                         next="give a range of numbers, e.g. [100, null]") from None


def select(population: Population, *, ids=None, where: dict | None = None, by: str | None = None, order: str = "desc",
           top: int | None = None, outliers: str | None = None, hub=None, notes: list | None = None
           ) -> tuple[list[Member], str]:
    """Members by explicit ids, measured ranges (`where`: {column: [min, max]}, or a lower bound alone), a
    category column's values (`where`: {"group": "g2"} or {"group": ["g1", "g2"]}, K1), the colour they are drawn
    in now (`where`: {"colour": "red"}, on the layer `hub` shows; going live, G3), outliers of a column against
    this population (median +- 3 MAD), sorted by a column, the first `top`. Columns are read as `column_of`
    resolves them (`hub`: the one grouping a request reads); one made before a correction is refused with the
    reason. Returns the members and the words for how they were chosen (the measured basis, or the colour
    drawn, never a guess); `notes` gets the facts a colour pick goes with (the other colours drawn, those not
    drawn now)."""
    from .brain.registry import ToolFailed

    members = [m for m in population.live()]
    how = []
    if ids:
        wanted = [str(i) for i in ids]
        members = [m for m in members if m.id in wanted]
        how.append("by id")
    for name, bounds in (where or {}).items():
        column = column_of(name, population, hub)
        if column == SHOWN_COLOUR:
            members, words = _drawn_in(population, members, bounds, layer_of(hub) if hub is not None else None, notes)
            how.append(words)
            continue
        shown = column_words(column) if "@" in column else column
        if population.column_type(column) == "category":  # its values ("g2"), one or several (K1 review SF3)
            wanted = [str(b) for b in (bounds if isinstance(bounds, (list, tuple, set)) else [bounds]) if b is not None]
            members = [m for m in members if value(population, m, column) is not None
                       and str(value(population, m, column)) in wanted]
            how.append(f"{shown} {' or '.join(wanted)}" + _retired_words(population, column, wanted))
            continue
        lo, hi = (list(bounds) + [None, None])[:2] if isinstance(bounds, (list, tuple)) else (bounds, None)
        lo, hi = _number(lo, column), _number(hi, column)
        keep = []
        for m in members:
            v = value(population, m, column)
            if not isinstance(v, (int, float)):
                continue
            if (lo is None or v >= lo) and (hi is None or v <= hi):
                keep.append(m)
        bound = (f"{'≥ ' + _plain_number(lo) if lo is not None else ''}{' and ' if lo is not None and hi is not None else ''}"
                 f"{'≤ ' + _plain_number(hi) if hi is not None else ''}")
        if notes is not None and bound:
            line = range_words(population, members, keep, column, lo, hi, bound)
            if line:
                notes.append(line)
        members = keep
        how.append(f"{shown} {bound}".strip())
    for given in (outliers, by):
        if given and column_of(given, population, hub) == SHOWN_COLOUR:  # never a silent 0 or every member (N3)
            raise ToolFailed("the colour a cell is drawn in is not a measurement to sort or compare by",
                             next="pick them by the colour they are drawn in, or by size")
    if outliers:
        column = column_of(outliers, population, hub)
        if population.column_type(column) == "category":
            raise ToolFailed(f"{column_words(column)} is a category, and outliers are of a measured number",
                             next="ask for outliers of size, elongation or roundness")
        values = np.array([v for v in (value(population, m, column) for m in members) if v is not None], float)
        if len(values) >= 5:
            median = float(np.median(values))
            mad = float(np.median(np.abs(values - median))) or 1e-9
            members = [m for m in members if value(population, m, column) is not None
                       and abs(value(population, m, column) - median) / (1.4826 * mad) > 3.0]
            how.append(f"outliers of {column} (more than 3 robust deviations from the median {median:.3g})")
    column = column_of(by, population, hub)
    ranked = None
    if column:
        ascending = str(order or "").strip().lower() in ("asc", "ascending", "smallest", "least", "lowest", "increasing")
        scored = [m for m in members if value(population, m, column) is not None]
        members = ranked = sorted(scored, key=lambda m: value(population, m, column), reverse=not ascending)
        how.append(f"{'smallest' if ascending else 'largest'} {column_words(column) if '@' in column else column} first")
    if top:
        members = members[: int(top)]
        how.append(f"first {int(top)}")
    if ranked is not None and notes is not None:
        line = ranking_words(population, ranked, len(members), column)
        if line:
            notes.append(line)
    return members, ", ".join(h for h in how if h)


def past_bound_first(population: Population, members: list[Member], where: dict | None, hub=None) -> tuple[list[Member], str]:
    """Members a one-sided measured range chose (`where`: {column: [lo, None]} or [None, hi]), farthest past its bound
    first, with the words saying so: when more fit than can be outlined, the ones shown are those that fit it best
    ("the round ones": the roundest), never the first by id (live 2026-10-11: c990-c1006 of 161 were outlined and
    called "the roundest"). Anything else (no range, two bounds, several columns, a category) is left as it was."""
    numeric = []
    for name, bounds in (where or {}).items():
        column = column_of(name, population, hub)
        if column == SHOWN_COLOUR or population.column_type(column) == "category":
            continue
        lo, hi = (list(bounds) + [None, None])[:2] if isinstance(bounds, (list, tuple)) else (bounds, None)
        numeric.append((column, _number(lo, column), _number(hi, column)))
    if len(numeric) != 1 or (numeric[0][1] is None) == (numeric[0][2] is None):
        return members, ""
    column, lo, _hi = numeric[0]
    scored = [m for m in members if isinstance(value(population, m, column), (int, float))]
    ordered = sorted(scored, key=lambda m: value(population, m, column), reverse=lo is not None)
    shown = column_words(column) if "@" in column else column
    return ordered + [m for m in members if m not in scored], f"{'largest' if lo is not None else 'smallest'} {shown} first"


RANKING_SHOWN = 8  # at most this many values in a ranking's line: the chosen, then the next ones not chosen


def _shown_value(v: float) -> str:
    return f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3g}"


def range_words(population: Population, before: list[Member], kept: list[Member], column: str, lo, hi, bound: str) -> str:
    """The measured values behind a range (`where`): those inside it, at most 5 (the ones nearest its bound kept when
    there are more), and the nearest few outside it, as facts, so a range the model chose ("the round ones":
    roundness ≥ 0.85) can be judged against what was measured. Nothing here changes what was chosen."""
    def bound_distance(v: float) -> float:
        return min(abs(v - b) for b in (lo, hi) if b is not None)

    inside = [(m, value(population, m, column)) for m in kept]
    outside = [(m, value(population, m, column)) for m in before if m not in kept]
    outside = [(m, v) for m, v in outside if isinstance(v, (int, float)) and not isinstance(v, bool)]
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) for _, v in inside) or not (inside or outside):
        return ""
    inside.sort(key=lambda mv: bound_distance(float(mv[1])), reverse=True)  # the farthest in first, the bound's last
    outside.sort(key=lambda mv: bound_distance(float(mv[1])))  # the nearest the bound first
    pair = lambda mv: f"{mv[0].id} {_shown_value(float(mv[1]))}"
    shown = ([pair(mv) for mv in inside] if len(inside) <= 5 else
             [pair(inside[0]), pair(inside[1]), f"{len(inside) - 5} more", *(pair(mv) for mv in inside[-3:])])
    line = f"{column_words(column)} {bound}: " + (", ".join(shown) or "none")
    near = outside[:min(3, RANKING_SHOWN - min(len(inside), 5))]
    if near:
        line += "; nearest outside: " + ", ".join(pair(mv) for mv in near) + (
            f", {len(outside) - len(near)} more" if len(outside) > len(near) else "")
    return line


def ranking_words(population: Population, ranked: list[Member], chosen: int, column: str) -> str:
    """The measured values behind a cut by a column: each chosen member's value, the next few not chosen, and a tie
    at the cut, as facts (live 2026-10-11, "highlight the round ones" 3 of 3: the receipt said only "largest circularity
    first, first 2: c2, c4", and the completion round called them the two roundest while c1 and c6 were as round).
    The model judges whether the cut is right; nothing here changes what was chosen. "" for a column of no numbers."""
    values = [value(population, m, column) for m in ranked]
    if not ranked or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return ""
    shown = [_shown_value(float(v)) for v in values]
    pair = lambda k: f"{ranked[k].id} {shown[k]}"
    if chosen <= 5:
        picked = [pair(k) for k in range(chosen)]
    else:  # the first two and the three at the cut, the rest counted
        picked = [pair(0), pair(1), f"{chosen - 5} more", *(pair(k) for k in range(chosen - 3, chosen))]
    rest = list(range(chosen, len(ranked)))
    room = RANKING_SHOWN - min(chosen, 5)
    after = [pair(k) for k in rest[:min(room, 4)]]  # the next few: where the cut could have fallen
    unit = UNITS.get(column, "")
    line = f"{column_words(column)}{f' ({unit})' if unit else ''}: " + ", ".join(picked)
    if after:
        line += "; next: " + ", ".join(after) + (f", {len(rest) - len(after)} more" if len(rest) > len(after) else "")
    if chosen and rest:
        last = chosen - 1
        ties = [ranked[k].id for k in rest if shown[k] == shown[last]]
        if ties:  # equal as shown: the cut fell inside them
            named = ties[:5] + ([f"{len(ties) - 5} more"] if len(ties) > 5 else [])
            who = named[0] if len(named) == 1 else f"{', '.join(named[:-1])} and {named[-1]}"
            line += f"; {who} {'ties' if len(ties) == 1 else 'tie'} {ranked[last].id} at the cut"
    return line


# What roundness measures, as a fact for the models (live 2026-10-11, "highlight the round ones" 0 of 3: the plans cut
# by rank, top 2-3, knowing the column's name only). Measured with measure.py on drawn shapes: a circle of radius 20
# 1.05, of radius 10 1.12 (the Kulpa perimeter of a small shape is a little short); 2:1 ovals 0.86-0.92; 3:1 0.69-0.71.
ROUNDNESS = ('Roundness as measured here: a circle about 1 (a small one a little over), a 2:1 oval 0.86-0.92, 3:1 about '
             '0.7; "the round ones" are those above a value you choose ({"roundness": [value, null]}), not the first n.')

PLAIN_COLUMNS = {"areaPx": "area", "holeAreaPx": "hole area", "perimeterPx": "perimeter", "feretMaxPx": "length",
                 "feretMinPx": "width", "circularity": "roundness", "group": "look group",
                 # the nearest group the user named, by look: never read as a label the user gave that cell (re-check note 6)
                 "yourLabel": "nearest group you named"}


def row_words(population: Population, member: Member, columns: list[str], plain: bool = False) -> str:
    """A member's measurements in words; `plain`: the user's words for the columns (the studio's line for a
    selected cell; acceptance flow 4 on footage showed "areaPx 4988 px², feretMaxPx 106.2 px")."""
    basis = population.basis()
    parts = []
    for column in columns:
        v = value(population, member, column)
        if v is None:
            continue
        unit = UNITS.get(column, "")
        name = column_words(column) if plain or "@" in column else column
        shown = (f"{v:,.0f}" if abs(v) >= 100 else f"{v:.3g}") if plain and isinstance(v, (int, float)) else (
            f"{v:.4g}" if isinstance(v, (int, float)) else str(v))
        text = f"{name} {shown}{(' ' + unit) if unit and isinstance(v, (int, float)) else ''}"
        um = basis.get("umPerPx")
        if um and column in ("areaPx", "holeAreaPx"):
            text += f" ({v * um * um:.4g} µm², {basis['scale']})"
        elif um and column in ("perimeterPx", "feretMaxPx", "feretMinPx"):
            text += f" ({v * um:.4g} µm, {basis['scale']})"
        parts.append(text)
    flags = [f for f in ("edgeCut", "small") if population.value(member.id, f)]
    return f"{member.id}: " + ", ".join(parts) + (f" [{', '.join(flags)}]" if flags else "")


def summary(population: Population, columns: list[str]) -> str:
    """Count, and per column the mean, the median and the middle half, with what they rest on. Each is a fact: which one
    answers a question ("the average" is the mean) is the reader's to judge (just-average, think-broad-2: the median was
    said as the average when only the median was here)."""
    live = population.live()
    out = [population.count_words() + ("" if population.revision else " (revision 0)")]  # a corrected one says its revision
    for column in columns:
        values = np.array([v for v in (value(population, m, column) for m in live) if isinstance(v, (int, float))], float)
        if len(values):
            q1, med, q3 = np.percentile(values, [25, 50, 75])
            out.append(f"{column}: mean {float(np.mean(values)):.4g}, median {med:.4g}, middle half {q1:.4g}-{q3:.4g} "
                       f"{UNITS.get(column, '')} (n {len(values)})")
    cov = (population.columns.get("measure") or {}).get("coverage")
    if cov is not None:
        region = "original image" if population.view.get("sourceImage") else "eyepiece disc"
        out.append(f"coverage: {cov * 100:.1f}% of the {region} (the union of members; overlaps once)")
    basis = population.basis()
    out.append("units: lengths in px, areas in px²" + (f"; lengths in µm, areas in µm² by the {basis['scale']} scale ({basis['umPerPx']:.4g} µm/px, objective {basis['objective']})"
                              if basis.get("umPerPx") else "; no scale, so no µm"))
    return "; ".join(out)


def review(population: Population) -> list[tuple[str, str]]:
    """Members worth a second look, by facts the app has: cut by the view's edge, too small for
    shape, low solidity (maybe two merged), a low model score."""
    out = []
    for m in population.live():
        why = []
        if population.value(m.id, "edgeCut"):
            why.append("cut by the edge")
        if population.value(m.id, "small"):
            why.append("too small for shape")
        solidity = population.value(m.id, "solidity")
        if isinstance(solidity, (int, float)) and solidity < 0.8:
            why.append(f"solidity {solidity:.2f} (maybe two merged)")
        score = m.extras.get("score")
        if isinstance(score, (int, float)) and score < 0.5:
            why.append(f"{m.extras.get('scoreIs') or 'score'} {score:.2f}")
        if why:
            out.append((m.id, ", ".join(why)))
    return out
