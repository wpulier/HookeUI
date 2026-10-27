"""The shared scene: everything currently pointed out on the specimen.

Every caller (clicks, chat, voice agents, workers) changes the picture only through this
store, and the browser only draws what is here. Outlines are stored in the coordinates of
the frame they were found on (their anchor). The browser moves them each displayed frame by
the motion offset difference, so they stay glued to the specimen without per-frame updates.

Objects are re-anchored with a local template match when tracking continuity breaks (a new
motion epoch) and periodically to cancel slow drift. If the match fails they are shown as
"re-finding" instead of being drawn somewhere wrong.
"""
from __future__ import annotations

import itertools
import time
from dataclasses import dataclass, field

import cv2
import numpy as np

from . import spaces
from .labels import label_key

STATUSES = ("outline", "looking", "unsure", "likely", "confirmed")
# On the naming bench (docs/bench/naming.md) names stated below 0.9 were right 45% of the time, at
# 0.9 and above 64-76%. That is told to the model as data (prompts.IDENTIFY); whether a name is
# "Not sure" is the model's own call ("unsure"), never a cut in code. "confirmed": the user agreed.
UNSURE_BELOW = 0.9


def status_for(confidence: float | None, cut: float | None = None) -> str:
    """"likely" for a name the model gave; "unsure" only below an explicit `cut` (benches)."""
    return "unsure" if cut is not None and isinstance(confidence, (int, float)) and confidence < cut else "likely"
TRACK_STATES = ("tracking", "refinding", "lost", "out_of_view")
UNDO_S = 30.0  # how long a removed outline is held so the user can take the removal back (Scene.restore)


@dataclass(frozen=True)
class AnchorGeometry:
    """The pixel basis of an outline for its scene lifetime; no JPEG, decoded picture or frame hold."""
    width: int | None
    height: int | None
    disc: float
    objective: object = None
    run: str | None = None
    epoch: int | None = None


def anchor_geometry(view, scale=None) -> AnchorGeometry | None:
    if view is None:
        return None
    from .capture import RUN

    width, height = getattr(view, "width", None), getattr(view, "height", None)
    if not width or not height:
        return None
    scale = getattr(view, "scale", None) if scale is None else scale
    return AnchorGeometry(width, height,
                          float(getattr(view, "disc", 0.0) or 0.0),
                          scale.get("objective") if isinstance(scale, dict) else None,
                          RUN if getattr(view, "epoch", None) is not None else None,
                          getattr(view, "epoch", None))


@dataclass(frozen=True)
class ProducingBasis:
    """The outline's original capture identity, independent of later placement."""
    frame: int | None
    epoch: int | None
    run: str | None


@dataclass
class SceneObject:
    id: str
    kind: str  # "highlight" | "part" (cells are a population: evidence.py, population.py)
    polygons: list[list[list[float]]]  # anchor-frame pixel coordinates
    anchor_frame: int
    anchor_epoch: int
    anchor_offset: tuple[float, float]
    source: str
    label: str = ""
    status: str = "outline"
    confidence: float | None = None
    note: str = ""  # how it was outlined (model, score, time)
    seen: str = ""  # what the AI saw in this one, in words (shown on its card, not in the chat)
    group: str | None = None
    trial: str | None = None  # A/B/C arena trial this outline belongs to (services/orchestrator/arena.py)
    arm: str | None = None  # its blind letter in that trial
    parent: str | None = None  # for dissected parts: the object they are part of
    track: str = "tracking"
    revised_by: str | None = None  # who changed its label (an accepted researcher correction)
    maybe: list[str] = field(default_factory=list)  # when not sure: what else it may be (agent.identify_reading)
    pinned: str | None = None  # a researcher note the user pinned under its label
    # The closest of the labels the user taught ({"label", "similarity", "examples"}; taught.py):
    # shown apart from the model's name, never written into it.
    taught: dict | None = None
    created_at: float = field(default_factory=time.time)
    lost_since: float | None = None
    template: np.ndarray | None = None  # gray patch around the object at anchor time
    template_origin: tuple[int, int] = (0, 0)  # top-left of template in anchor frame
    # Where it was made (contract 2, ADR 0010), never moved by re-anchoring or following: the
    # capture's frame id and the outline in that capture's pixels. Kept off the wire.
    origin: int | None = None
    origin_polygons: list | None = None
    # The box it was outlined from on that capture (the look's or the running look's box), when it was: where it
    # was meant to be, which the outline check is told when others share its name (going live G4).
    origin_box: tuple | None = None
    # Bumped by a result that changes it (a repair, a name): a late result for an older revision
    # is not applied (apply.blocked). Moves by re-anchoring or following do not count.
    revision: int = 0
    # What made it (operations.Execution.made_by: the operation, the implementation that ran, its
    # policy and origin, the execution id, and what it fell back from). Kept off the wire until the
    # studio shows it (M1.5); never shown for an unvoted arena arm.
    made_by: dict | None = None
    # Who followed it on its own (objtrack.py): {"implementation", "model", "shape": "kept" (another model's
    # shape, moved) or "redrawn" (EdgeTAM's, redrawn by its video model), "since", "until"}; None if never
    # followed. For how it was made ("drawn by micro-sam, followed by EdgeTAM"). Kept off the wire.
    followed_by: dict | None = None
    # How its name was made (known.py): naming a click's model, view, notes, alternatives and what it
    # was told; a SAM 3.1 match's example and score; a promoted cell's population; and every rename.
    # Kept with the outline while it exists (not yet with the persisted named-outline entry). Off the
    # wire; read by resources(id=<its id>).
    naming: dict | None = None
    # The outline check's verdict on it (outline_check.py): the model that checked it, what it said, when, and when it
    # was redrawn after the check, what had drawn it before. Part of how it was made (known.chain). On the wire only as
    # `fit`, while a verdict that it does not fit stands ("kept", not redrawn).
    checked: dict | None = None
    # Why it was not drawn when it was made (its place on the live view unverified), for what the
    # brain and the voice are told; "" when it was placed. Kept off the wire.
    unplaced: str = ""
    anchor_geometry: AnchorGeometry | None = None  # retained off the wire after short-lived frame facts expire
    producing_basis: ProducingBasis | None = field(default=None, init=False, repr=False)

    @property
    def shown(self) -> bool:
        """Drawn on the live view now as far as the server knows: verified in its epoch (the studio
        also needs it inside the eyepiece)."""
        return self.track == "tracking"

    def not_shown_why(self) -> str:
        return self.unplaced or "its place on the live view is not verified"

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        pts = np.array([p for poly in self.polygons for p in poly], dtype=float)
        return float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()), float(pts[:, 1].max())

    @property
    def center(self) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        return (x0 + x1) / 2, (y0 + y1) / 2

    def to_wire(self, size: dict | None = None) -> dict:
        blind = self.trial is not None  # an unvoted arena arm must not reveal its model
        return {
            "id": self.id,
            "kind": self.kind,
            "polygons": [[[round(x, 1), round(y, 1)] for x, y in poly] for poly in self.polygons],
            "anchorFrame": self.anchor_frame,
            "anchorEpoch": self.anchor_epoch,
            "anchorOffset": [round(self.anchor_offset[0], 2), round(self.anchor_offset[1], 2)],
            "source": "arena" if blind else self.source,
            "label": self.label,
            "labelKey": label_key(self.label),
            "status": self.status,
            "confidence": self.confidence,
            "note": "" if blind else self.note,
            "seen": "" if blind else self.seen,
            "group": self.group,
            "trial": self.trial,
            "arm": self.arm,
            "parent": self.parent,
            "track": self.track,
            "revisedBy": self.revised_by,
            "maybe": list(self.maybe),
            # Its size in µm once the scale is known (orchestrator/scale.py); never for a blind arm.
            "size": None if blind or not size else {k: (round(v, 2) if isinstance(v, float) else v) for k, v in size.items()},
            "pinned": self.pinned,
            "taught": None if blind or not self.taught else dict(self.taught),
            "createdAt": self.created_at,
            # The outline check's verdict, only while it stands (it said the outline does not fit and nothing replaced
            # it: outline_check.OutlineCheck._unresolved): the studio draws it as uncertain and says why.
            **({"fit": {"fits": self.checked["fits"], "why": str(self.checked.get("why") or "")}}
               if not blind and self.checked and self.checked.get("kept") and not self.checked.get("redrawn") else {}),
        }


class Scene:
    def __init__(self) -> None:
        from .ticket import Generations

        self.objects: dict[str, SceneObject] = {}
        # What a clear supersedes (ticket.py): a request issued before it changes nothing there.
        self.generations = Generations()
        self.version = 0
        self.context: dict = {}  # specimen description etc., set by the specimen router
        self.selected: str | None = None  # the object the user (or the brain) selected
        self.selected_at = 0.0
        self.on_remove: list = []  # callbacks(object_id) when an object leaves the scene
        self._recently_removed: dict[str, tuple[float, list]] = {}  # removed in the last UNDO_S, for restore
        self.on_change: list = []  # callbacks(op, ids): "add", "remove", "select", "clear" (board.py keeps them)
        self.on_label: list = []  # callbacks(object_id) when an outline gets a name or a new one (seen.py keeps it)
        self._ids = itertools.count(1)
        self.geometry_at = None  # hub callback(frame id): compact basis when an anchor is created or changed

    def new_id(self, prefix: str = "o") -> str:
        return f"{prefix}{next(self._ids)}"

    def add(self, obj: SceneObject) -> SceneObject:
        if obj.anchor_geometry is None and self.geometry_at is not None:
            obj.anchor_geometry = self.geometry_at(obj.anchor_frame)
        if obj.producing_basis is None:
            frame = obj.origin if obj.origin is not None else obj.anchor_frame
            same_capture = frame == obj.anchor_frame
            geometry = obj.anchor_geometry if same_capture else (
                self.geometry_at(frame) if self.geometry_at is not None else None)
            # The anchor's epoch describes the producing frame only when they
            # are the same capture. Otherwise use that exact held view's facts,
            # never the newer placement's epoch or run.
            epoch = obj.anchor_epoch if same_capture else getattr(geometry, "epoch", None)
            run = getattr(geometry, "run", None)
            if same_capture and getattr(geometry, "epoch", None) is not None and geometry.epoch != epoch:
                epoch, run = None, None
            if epoch is None:
                run = None
            obj.producing_basis = ProducingBasis(frame, epoch, run)
        self.objects[obj.id] = obj
        self.version += 1
        self._changed("add", (obj.id,))
        return obj

    def _changed(self, op: str, ids: tuple[str, ...]) -> None:
        for callback in self.on_change:
            try:
                callback(op, ids)
            except Exception:
                pass

    def update(self, object_id: str, **changes) -> SceneObject | None:
        obj = self.objects.get(object_id)
        if obj is None:
            return None
        renamed = "label" in changes and changes["label"] != obj.label
        confirmed = changes.get("status") == "confirmed" and obj.status != "confirmed"
        if "anchor_frame" in changes and "anchor_geometry" not in changes:
            changes["anchor_geometry"] = self.geometry_at(changes["anchor_frame"]) if self.geometry_at is not None else None
        for key, value in changes.items():
            setattr(obj, key, value)
        if renamed or confirmed:
            # A new name, or the user confirming one, changes the object itself: a late result made
            # for its earlier revision (a model's name, a repair for the old name) is not applied.
            obj.revision += 1
        self.version += 1
        if renamed:
            for callback in self.on_label:
                try:
                    callback(object_id)
                except Exception:
                    pass
        return obj

    def revise(self, object_id: str, **changes) -> SceneObject | None:
        """A result that changes the object itself (not where it is): its revision goes up, so a
        result made for the earlier revision is not applied."""
        obj = self.update(object_id, **changes)
        if obj is not None:
            obj.revision += 1
        return obj

    def remove(self, object_id: str) -> bool:
        obj = self.objects.pop(object_id, None)
        removed = obj is not None
        if removed:
            # Parts go with the object they belong to.
            parts = [o.id for o in self.objects.values() if o.parent == object_id]
            kept = [obj, *(self.objects.pop(part) for part in parts if part in self.objects)]
            # Held a while, so the user's own removal can be undone as it was (restore): the same outline, its name,
            # how it was made and its parts (the user's notes of 2026-10-11: a click removes, so it can be taken back).
            self._recently_removed[object_id] = (time.monotonic(), kept)
            for old in [k for k, (at, _) in self._recently_removed.items() if time.monotonic() - at > UNDO_S]:
                del self._recently_removed[old]
            self._removed([object_id, *parts])
            if self.selected not in self.objects:
                self.selected = None
            self.version += 1
            self._changed("remove", (object_id, *parts))
        return removed

    def restore(self, object_id: str) -> SceneObject | None:
        """An outline removed in the last UNDO_S, back as it was (its parts with it); None when it is no longer held
        or its id is in use again."""
        held = self._recently_removed.pop(object_id, None)
        if held is None or time.monotonic() - held[0] > UNDO_S or object_id in self.objects:
            return None
        for obj in held[1]:
            self.add(obj)
        return held[1][0]

    def _removed(self, ids: list[str]) -> None:
        for callback in self.on_remove:
            for object_id in ids:
                try:
                    callback(object_id)
                except Exception:
                    pass

    def select(self, object_id: str | None) -> bool:
        if object_id is not None and object_id not in self.objects:
            return False
        self.selected = object_id
        self.selected_at = time.time()  # the newest thing pointed at is what "that" means (correct_cells)
        self.version += 1
        self._changed("select", (object_id,) if object_id else ())
        return True

    def clear(self, kind: str | None = None, by=None, keep=()) -> int:
        """Remove every object of `kind` (all when None), except those in `keep` and their parts. Requests made before
        it may not add that kind any more (their tickets are superseded); `by` is the ticket clearing, if any."""
        self.generations.bump_kind(kind, by=by)
        keep = set(keep)
        doomed = [oid for oid, o in self.objects.items() if (kind is None or o.kind == kind)
                  and oid not in keep and o.parent not in keep]
        for oid in doomed:
            del self.objects[oid]
        orphans = [oid for oid, o in self.objects.items() if o.parent and o.parent not in self.objects]
        for oid in orphans:
            del self.objects[oid]
        doomed += orphans
        self._removed(doomed)
        if self.selected not in self.objects:
            self.selected = None
        if doomed:
            self.version += 1
            self._changed("clear", tuple(doomed))
        return len(doomed)

    def to_wire(self) -> dict:
        sizer = getattr(self, "sizer", None)  # app.py: the outline's size from the day's scale
        return {"space": spaces.of_wire("scene"), "version": self.version, "context": self.context, "selected": self.selected,
                "objects": [o.to_wire(sizer(o) if sizer else None) for o in self.objects.values()]}


def capture_template(gray: np.ndarray, bbox: tuple[float, float, float, float], margin: int = 24) -> tuple[np.ndarray, tuple[int, int]] | None:
    h, w = gray.shape[:2]
    x0, y0 = max(0, int(bbox[0]) - margin), max(0, int(bbox[1]) - margin)
    x1, y1 = min(w, int(bbox[2]) + margin), min(h, int(bbox[3]) + margin)
    if x1 - x0 < 16 or y1 - y0 < 16:
        return None
    return gray[y0:y1, x0:x1].copy(), (x0, y0)


def refind(
    gray: np.ndarray,
    template: np.ndarray,
    predicted_origin: tuple[float, float],
    search_px: int,
    min_score: float = 0.6,
) -> tuple[float, float, float] | None:
    """Look for the template near where motion tracking says it should be.
    Returns (dx, dy, score): the correction to apply to the prediction, or None."""
    th, tw = template.shape[:2]
    h, w = gray.shape[:2]
    x0 = int(round(predicted_origin[0])) - search_px
    y0 = int(round(predicted_origin[1])) - search_px
    x1, y1 = x0 + tw + 2 * search_px, y0 + th + 2 * search_px
    cx0, cy0, cx1, cy1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if cx1 - cx0 < tw or cy1 - cy0 < th:
        return None
    region = gray[cy0:cy1, cx0:cx1]
    result = cv2.matchTemplate(region, template, cv2.TM_CCOEFF_NORMED)
    _, score, _, loc = cv2.minMaxLoc(result)
    if score < min_score:
        return None
    # Reject ambiguous matches (repeating cell patterns): the runner-up away from the
    # peak must be clearly weaker.
    suppressed = result.copy()
    r = max(4, min(tw, th) // 3)
    cv2.rectangle(suppressed, (loc[0] - r, loc[1] - r), (loc[0] + r, loc[1] + r), -1.0, -1)
    if suppressed.size and float(suppressed.max()) > score - 0.08:
        return None
    found_x, found_y = cx0 + loc[0], cy0 + loc[1]
    return found_x - predicted_origin[0], found_y - predicted_origin[1], float(score)


def match_at(gray: np.ndarray, template: np.ndarray, origin: tuple[float, float], radius: int = 3) -> float:
    """How well the template matches at `origin` (its top-left), allowing `radius` px: whether the
    pixels are where the slide's move puts them, whatever look-alikes are near (refind() rejects an
    ambiguous match; verifying a place does not need it to be unique). -1 when it falls off the view."""
    th, tw = template.shape[:2]
    h, w = gray.shape[:2]
    x0, y0 = int(round(origin[0])) - radius, int(round(origin[1])) - radius
    x1, y1 = x0 + tw + 2 * radius, y0 + th + 2 * radius
    if x0 < 0 or y0 < 0 or x1 > w or y1 > h:
        return -1.0
    return float(cv2.matchTemplate(gray[y0:y1, x0:x1], template, cv2.TM_CCOEFF_NORMED).max())
