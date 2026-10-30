"""Evidence: what an operation a request made found, kept with its view and its execution.

docs/design/2026-10-06-captures-operations-evidence.md, contract 8 and Amendment 1; ADR 0010.

A population (`e3`) is a table: one row per member (`c12`, ids that never repeat in a day), each with
the specialist's own instance mask exactly as it returned it, its extras (scores), and the outline
drawn from the mask for display. Measurements are one group of columns (`columns["measure"]`), made
by app code (measure.py) on each member's own mask and recorded with what made them; other operations
may later add their own groups (Amendment 1). Corrections make revisions; revision 0 is the model's
and never changes. Links record what is the same object (a member promoted to an outline) with their
basis. Counts are instances segmented in a stated region, never a biological count.

Only operations a request made become evidence. It is saved in the background, atomically (`.tmp`,
fsync, rename, the manifest last with a schema version and checksums), in the job's own date folder under
`Paths.evidence`, filed as a journal event of the session it was asked in (ADR 0017: a session spans days, and work
finished after a switch is filed where it was asked) and read back when that session is opened (ADR 0001). "Saved"
is said only after the rename. Nothing is deleted automatically; past the disk budget, new analyses are refused.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import itertools
import json
import os
import stat
import sys
import threading
import time
import weakref
import zlib
from contextlib import ExitStack, contextmanager
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from . import spaces
from .brain.registry import ToolFailed
from .measure import Mask, coverage, measure

SCHEMA = "evidence/1"
_tmp_ids = itertools.count(1)
DISK_GB = 10.0  # MICROMIND_EVIDENCE_GB: past it, new analyses are refused (nothing is deleted)
BOARD_LINES = 5  # evidence lines the brain sees at most (contract 9)
# Columns each picture model has its own of (K1): a member's look group, and its nearest named group. Read as
# "group@dinov2-small" where the model matters (enrichment.grouping chooses it); the prefix is the group's id.
PER_MODEL = {"group": "groups", "yourLabel": "named", "similarity": "named"}
# Files a population can be read back without (K1, review note): vectors can be made again; masks cannot.
OPTIONAL_FILES = ("vectors.npz",)


class EvidenceRefused(ToolFailed):
    """A new analysis cannot be kept now (the disk budget, or saving has failed); the reason says which."""


@dataclass
class Member:
    id: str
    instance: int  # the model's own number for it (its label, or its index in the model's output)
    mask: Mask | None  # the original instance mask (None while it is on disk only)
    polygons: list  # drawn from the mask, for display only (capture px)
    box: tuple[float, float, float, float]
    extras: dict = field(default_factory=dict)  # what the model said of it (score, IoU, stability...)
    status: str = "model"  # "model" | "added" (by a correction) | "deleted" | "merged"

    def row(self) -> dict:
        return {"id": self.id, "instance": self.instance, "box": [round(v, 1) for v in self.box],
                "extras": self.extras, "status": self.status}


def label_map(view: dict, members: list, path: Path | None, overlapping: bool, total: int,
              complete: bool = False) -> np.ndarray | None:
    """An exclusive label map of `members` ((id, mask or None, score), numbered 1.. in that order) on the view:
    a released mask is read from the saved masks for the map, never kept (B2); where members overlap, the
    highest score wins. `complete`: None when any member's mask is in neither place (linking can't tell then)."""
    if not view:
        return None
    saved = {}
    if path is not None and any(mask is None for _, mask, _ in members) and (path / "masks.npz").exists():
        with np.load(path / "masks.npz") as data:
            for mid, mask, _ in members:
                if mask is None and f"{mid}.bits" in data:
                    h, w, x0, y0, sc = data[f"{mid}.meta"]
                    bits = np.unpackbits(data[f"{mid}.bits"])[: int(h) * int(w)].reshape(int(h), int(w)).astype(bool)
                    saved[mid] = Mask(bits, int(x0), int(y0), float(sc))
    h, w = view["height"], view["width"]
    out = np.zeros((h, w), np.uint32 if total > 65535 else np.uint16)
    best = np.full((h, w), -np.inf, np.float32) if overlapping else None
    for n, (mid, mask, score) in enumerate(members, start=1):
        mask = mask if mask is not None else saved.get(mid)
        if mask is None:
            if complete:
                return None
            continue
        bits = _full_res(mask)
        y0, x0 = int(mask.y0), int(mask.x0)
        ys = slice(max(0, y0), min(h, y0 + bits.shape[0]))
        xs = slice(max(0, x0), min(w, x0 + bits.shape[1]))
        part = bits[ys.start - y0:ys.stop - y0, xs.start - x0:xs.stop - x0]
        if best is not None:
            take = part & (best[ys, xs] < score)
            best[ys, xs][take] = score
            out[ys, xs][take] = n
        else:
            out[ys, xs][part] = n
    return out


def linked_outline(hub, link: dict):
    """The outline a same-object link names, only while it is that outline: a link made in this run of the app, no
    older than the outline. Outline ids start again at o1 in every run, so a link read back from an earlier run names an
    outline of that run, never the new one with its id, whatever the clocks say (the M2.3 re-checks); a link from before
    runs were recorded is told apart by its time. None otherwise."""
    from .capture import RUN

    obj = hub.scene.objects.get(link.get("to")) if link.get("kind") == "same-object" else None
    if obj is None or link.get("run") not in (None, RUN):
        return None
    if float(link.get("at") or 0.0) + 1.0 < float(getattr(obj, "created_at", 0.0) or 0.0):
        return None
    return obj


@dataclass
class Population:
    id: str
    operation: str
    implementation: str | None
    made_by: dict
    capture_id: int
    view: dict  # the capture's facts: epoch, dx, dy, disc [cx, cy, r], width, height, at, seq, boot
    scale_id: dict | None
    region: tuple[int, int, int, int] | None  # (x, y, w, h) the specialist segmented, capture px
    members: "OrderedDict[str, Member]"
    ticket: str | None = None
    thread: str | None = None
    day: str = ""  # the calendar date folder it is filed under (evidence/<day>/e3)
    made_at: float = field(default_factory=time.time)
    revision: int = 0
    revisions: list = field(default_factory=list)  # [{"r", "change", "by", "at", "members"}]
    columns: dict = field(default_factory=dict)  # group -> {"execution", "implementation", "basis", "values"}
    original: dict | None = None  # revision-zero member rows and measurements, retained without recomputation
    # A vector group's numbers (enrichment.py, K1): gid -> {"ids", "array"}, resident until saved (vectors.npz),
    # under their own reservation in the unsaved allowance (`vector_hold`); read back from disk when needed.
    vectors: dict = field(default_factory=dict)
    vector_hold: Any = None
    links: list = field(default_factory=list)  # [{"kind": "same-object"|"same-kind", "from", "to", "basis", "confidence"}]
    status: str = "saving"  # "saving" | "saved" | "failed" | "unsaved"
    status_why: str = ""
    path: Path | None = None
    capture: Any = None  # the Capture while its pixels are resident (dropped once saved)
    example: str | None = None  # find-like: the member or outline the matches are like
    overlapping: bool = False  # members may overlap (automatic masks, SAM matches): a derived label map arbitrates
    _labels: np.ndarray | None = None
    hold: Any = None  # the capture hold (and reservation) while unsaved
    summary_hold: Any = field(default=None, repr=False, compare=False)  # resident table, including after pixels leave
    pins: int = 0  # corrections reading and changing its masks now: a save finishing meanwhile leaves them in memory
    release_pending: bool = False  # saved while pinned: its pixels leave memory when the last correction is done
    # Members whose masks its saved masks.npz holds (a member's mask never changes once made): only these leave
    # memory, so a member made while a save was written keeps its mask until a save writes it (finding 84).
    saved_masks: set = field(default_factory=set)
    # What made it, as interpreting it needs whatever the optional logs say (Amendment 4, item 3): the
    # implementation that ran with its version and configuration, and what was done to its input.
    producer: dict = field(default_factory=dict)
    transformations: dict = field(default_factory=dict)
    # The day's generation its conversation belongs to (population._generation): a new t1 after "start today
    # afresh" is not the old one (the partner's review of M3.2, S2).
    generation: int | None = None
    # The session it was made in (ADR 0017: the request's, else the one open); read back, its journal's. "" only for a
    # stand-in made with no session store.
    session: str = ""

    @property
    def made_for(self) -> str | None:
        """Who it was made for, when not for the user's own requests: a named comparison (M2.2) or a researcher's
        own reading (research/hands.py). Such a population is never "the cells" a request means unless it is shown
        or named (population.resolve)."""
        made = self.made_by or {}
        if made.get("origin") == "compare":
            return "a comparison"
        if made.get("origin") == "background" and made.get("for"):
            return str(made["for"])
        return None

    @property
    def member_noun(self) -> str:
        return {"fiji-analyze-particles": "particle", "basic-bright-objects": "bright region"}.get(self.implementation, "cell")

    @property
    def title(self) -> str:
        """Its plain title (Amendment 4, item 1): what it is, what made it and when, "Cell outlines · Cellpose-SAM ·
        18:04". The model is named (names.py) so "the cellpose ones" can be told from micro-sam's (live 2026-10-11)."""
        from .operations import label_of

        what = {"cells": "Cell outlines", "find-like": "Matches", "automask": "Automatic outlines"}.get(self.operation, "Outlines")
        if self.implementation == "fiji-analyze-particles":
            what = "Particles"
        if self.implementation == "basic-bright-objects":
            what = "Bright regions"
        config = ((self.producer or {}).get("configuration") or {}) if isinstance(self.producer, dict) else {}
        words = config.get("text") if not config.get("box") else None
        if self.operation == "find-like" and words:
            what = f'Matches for "{words}"'  # the words SAM 3.1 was given (M3.1 review S3)
        if self.made_for == "a comparison":  # made for a named comparison (M2.2), not the cells to use
            what += ", for a comparison"
        elif self.made_for:  # made for a researcher's own reading (research/hands.py)
            what += f", for {self.made_for}"
        model = f"{label_of(self.implementation)} · " if self.implementation else ""
        return f"{what} · {model}{time.strftime('%H:%M', time.localtime(self.view.get('at') or self.made_at))}"

    # --- the table ------------------------------------------------------------------------
    def live(self) -> list[Member]:
        return [m for m in self.members.values() if m.status not in ("deleted", "merged")]

    def count_words(self) -> str:
        """What the model segmented (revision 0), and, once the user corrected it, what is left: corrected counts
        are never credited to the model (M2.3 review B3)."""
        when = time.strftime("%H:%M:%S", time.localtime(self.view.get("at") or self.made_at))
        added = {a for r in self.revisions for a in (r.get("added") or [])}
        first = sum(1 for mid in self.members if mid not in added)
        region = ("in the original image" if self.view.get("sourceImage") else "inside the eyepiece disc of the view")
        words = f"{first} instances segmented by {self.implementation or self.operation} {region} at {when}"
        if self.implementation == "fiji-analyze-particles":
            words = f"{first} particles detected by Analyze Particles (Fiji) {region} at {when}"
            settings = (self.producer or {}).get("settings") or {}
            runtime = (self.producer or {}).get("runtime") or {}
            if settings and runtime:
                words += (f"; Fiji generation {runtime['generation']}, {settings['conversion']}, "
                          f"{settings['thresholdMethod']} threshold, minimum {settings['minimumSizePx2']:g} px²")
        if self.implementation == "basic-bright-objects":
            words = f"{first} bright regions detected by the basic local method {region} at {when}; not biological cell identities"
        comparison = (self.producer or {}).get("comparison")
        if comparison:
            words += ("; agreement with the ImageJ macro" if comparison.get("agreement") else "; disagrees with the ImageJ macro")
            words += "; same input and generation, not an accuracy assessment"
        if self.revision:
            words += f"; {len(self.live())} after your corrections (revision {self.revision})"
        # Which device made it and how that device was proven (noncommercial/cellpose_device.py, ticket 62 F-L8-1):
        # every surface that says this population's count says it too, fallbacks included.
        device = ((self.producer or {}).get("device") or {}) if isinstance(self.producer, dict) else {}
        if device.get("says"):
            words += f"; {device['says']}"
        elif device.get("device") and device["device"] != "cpu" and device.get("checked"):
            words += f"; on this Mac's GPU, checked first: it {device['checked']}"
        return words

    def measure_all(self, gray: np.ndarray | None, execution: str | None = None) -> None:
        """The measurement group: each live member's own mask, measured by app code (measure.py)."""
        disc = tuple(self.view["disc"]) if self.view.get("disc") else None
        values = {}
        for m in self.live():
            if m.mask is not None:
                values[m.id] = measure(m.mask, gray, disc, self.region)
        masks = [m.mask for m in self.live() if m.mask is not None]
        self.columns["measure"] = {"execution": execution, "implementation": "orchestrator.measure", "basis": self.basis(),
                                   "definitions": {"areaPx": "Retained mask pixel count multiplied by mask scale squared (capture px²)",
                                                   "centroid": "Mean of occupied pixel centers in the original capture grid; stored to two decimals",
                                                   "intensity": "Mean original-image grayscale intensity under the retained mask; stored to two decimals"},
                                   "revision": self.revision, "values": values,
                                   "coverage": coverage(masks, disc, (self.view["width"], self.view["height"])) if masks else 0.0}
        if self.revision == 0 and self.original is None:
            self.original = {"revision": 0, "members": copy.deepcopy([m.row() for m in self.live()]),
                             "columns": copy.deepcopy(self.columns)}

    def basis(self) -> dict:
        """What the measurements' units rest on: pixels always; micrometres only by the capture's scale_id."""
        scale = self.scale_id or {}
        um = scale.get("umPerPx")
        return {"unit": "px", "umPerPx": um, "scale": "measured" if scale.get("measured") else ("estimated" if um else None),
                "objective": scale.get("objective")}

    # --- column groups (session knowledge K1) ------------------------------------------------
    def stale(self, group: dict | None) -> bool:
        """The one stale check (K1, review B3): a column group made at revision r is stale once the members are
        at a later revision (a correction since). Every reader goes through it; the measurements are measured
        again with each revision, so they never are."""
        return group is not None and int(group.get("revision") or 0) < self.revision

    def column_group(self, column: str) -> tuple[str | None, dict | None]:
        """The group a column is read from: for "group@bioclip-2", that picture model's own; otherwise the newest
        current group that has the column, else the newest stale one (so a reader can say it is stale)."""
        base, _, model = str(column).partition("@")
        if model:
            gid = f"{PER_MODEL.get(base, base)}:{model}"
            return (gid, self.columns[gid]) if gid in self.columns else (None, None)
        found = []
        for gid, group in self.columns.items():
            declared = base in (group.get("columns") or {})
            measured = gid == "measure" and any(base in row for row in itertools.islice((group.get("values") or {}).values(), 3))
            if declared or measured:
                found.append((not self.stale(group), group.get("madeAt") or 0.0, gid, group))
        if not found:
            return None, None
        best = max(found, key=lambda f: (f[0], f[1]))
        return best[2], best[3]

    def column_type(self, column: str) -> str | None:
        """A column's declared type ("number", "category", "vector", "ranking"); the measurements are numbers."""
        gid, group = self.column_group(column)
        if group is None:
            return None
        declared = (group.get("columns") or {}).get(str(column).partition("@")[0]) or {}
        return declared.get("type") or ("number" if gid == "measure" else None)

    def known_columns(self) -> dict[str, str]:
        """Every column its groups have, by its lower-case name: its exact name ("meanred" -> "meanRed")."""
        out = {}
        for gid, group in self.columns.items():
            if gid == "measure":
                for row in itertools.islice((group.get("values") or {}).values(), 3):
                    out.update({c.lower(): c for c in row})
            out.update({c.lower(): c for c in (group.get("columns") or {})})
        return out

    def value(self, member: str, column: str):
        """A member's value in a column, never from a stale group (made before a correction). "group@model" reads
        that picture model's own; a plain per-model column ("group") the newest current one."""
        base, _, model = str(column).partition("@")
        if model:
            group = self.columns.get(f"{PER_MODEL.get(base, base)}:{model}")
            if group is None or self.stale(group):
                return None
            return ((group.get("values") or {}).get(member) or {}).get(base)
        best = None
        for group in self.columns.values():
            row = (group.get("values") or {}).get(member)
            if row is None or base not in row or self.stale(group):
                continue
            if best is None or (group.get("madeAt") or 0.0) > (best.get("madeAt") or 0.0):
                best = group
            if base not in PER_MODEL:
                break
        return None if best is None else best["values"][member][base]

    def labels(self) -> np.ndarray | None:
        """The derived exclusive label map (capture px of the region), for display, hit-testing and
        selection only: where members overlap, the highest score wins (stated, never used to measure)."""
        if self._labels is not None:
            return self._labels
        if not self.view:
            return None
        self._labels = label_map(self.view, [(m.id, m.mask, float(m.extras.get("score") or 0.0)) for m in self.live()],
                                 self.path, self.overlapping, len(self.members))
        return self._labels

    # --- what is told and shown ---------------------------------------------------------------
    def line(self, placement: str = "") -> str:
        cols = [c for c in ("areaPx", "elongation", "circularity", "solidity") if self.columns.get("measure")]
        state = {"saving": "saving", "saved": "saved", "failed": f"not saved ({self.status_why})",
                 "unsaved": f"not saved ({self.status_why})"}[self.status]
        return (f"{self.title} ({self.id}): {self.count_words()}"
                + (f"; {placement}" if placement else "")
                + (f"; measured {', '.join(cols)}" if cols else "")
                + "".join(f"; {k}" for k in _enrichment_kinds(self))
                + (f"; revision {self.revision}" if self.revision else "") + f"; {state}")

    def wire(self, placement: dict | None = None, *, revision: int | None = None) -> dict:
        """The studio's layer: members' outlines in capture coordinates, the capture's epoch and
        offset, each member's placement, measurements and a colour index derived from its id."""
        requested = self.revision if revision is None else revision
        if type(requested) is not int or requested not in (0, self.revision):
            raise ValueError("The requested revision has no retained measurement snapshot.")
        columns, members = self.columns, self.live()
        if requested == 0 and self.revision != 0:
            original = self.original
            if (not isinstance(original, dict) or original.get('revision') != 0 or
                    not isinstance(original.get('members'), list) or not isinstance(original.get('columns'), dict)):
                raise ValueError("The original population snapshot is unavailable.")
            ids = [row.get('id') for row in original['members'] if isinstance(row, dict)]
            if (len(ids) != len(original['members']) or any(not isinstance(mid, str) for mid in ids) or len(set(ids)) != len(ids) or
                    any(mid not in self.members for mid in ids)):
                raise ValueError("The original population members are unavailable.")
            # Corrections change status/add new IDs; the original members' geometry is retained.
            members = [self.members[mid] for mid in ids]
            columns = original['columns']
            measure = columns.get('measure')
            if (not isinstance(measure, dict) or measure.get('revision') != 0 or
                    not isinstance(measure.get('values'), dict) or
                    any(not isinstance(measure['values'].get(mid), dict) for mid in ids)):
                raise ValueError('The original population measurements are unavailable.')
        measured = (columns.get("measure") or {}).get("values", {})
        placement = placement or {}
        return {"type": "population", "space": spaces.of_wire("population"), "id": self.id, "title": self.title, "operation": self.operation, "madeBy": self.made_by,
                "capture": self.capture_id, "epoch": self.view.get("epoch"), "offset": [self.view.get("dx"), self.view.get("dy")],
                "at": self.view.get("at"), "revision": requested, "status": self.status, "count": len(members),
                "session": self.session, "originalCount": len(self.original["members"]) if self.original is not None else None,
                "canUndo": requested == self.revision and any(not r.get("undoes") and r["r"] not in {u.get("undoes") for u in self.revisions}
                               for r in self.revisions),
                "coverage": (columns.get("measure") or {}).get("coverage"),
                "basis": self.basis(),
                "members": [{"id": m.id, "polygons": [[[round(x, 1), round(y, 1)] for x, y in p] for p in m.polygons],
                             "colour": colour_of(m.id), "placement": placement.get(m.id, "unverified"),
                             "measure": _wire_measure(measured.get(m.id))} for m in members]}


def _wire_measure(values: dict | None) -> dict | None:
    if not values:
        return None
    keep = ("areaPx", "perimeterPx", "feretMaxPx", "feretMinPx", "elongation", "circularity", "solidity", "intensity",
            "edgeCut", "small", "components", "holeAreaPx")
    return {k: values.get(k) for k in keep if k in values}


def colour_of(member_id: str) -> int:
    """A member's colour, derived from its id (the same in its rows, its close-up and its highlight):
    an index into the studio's palette."""
    return int(hashlib.sha1(member_id.encode()).hexdigest()[:6], 16) % 12


def _full_res(mask: Mask) -> np.ndarray:
    bits = np.asarray(mask.bits, bool)
    if mask.scale == 1.0:
        return bits
    h, w = bits.shape
    return cv2.resize(bits.astype(np.uint8), (int(round(w * mask.scale)), int(round(h * mask.scale))),
                      interpolation=cv2.INTER_NEAREST).astype(bool)


def label_workspace_bytes(wire: dict, distinct: int | None = None) -> int:
    """Transient label decoding/extraction capacity, admitted before allocating (ADR 0010).

    Decoding and unique discovery need image-sized copies, but bounds planning needs only
    one row of indices and four coordinates per distinct native label. Before discovery,
    distinct=0 admits the decode/sort peak; the caller then grows to the discovered count
    before allocating bounds. With no count, cover the worst case of one label per pixel.
    This depends on image size and label count, never the largest native ID.
    """
    height, width = (int(n) for n in wire["shape"])
    if min(height, width) < 0:
        raise ValueError("a label map cannot have negative dimensions")
    value, index = np.dtype(wire["dtype"]).itemsize, np.dtype(np.intp).itemsize
    pixels = height * width
    if distinct is None:
        # Existing callers that do not stage admission reserve the original full-image envelope.
        return pixels * max(8 * value + 6 * index, 2 * value + 7 * index) + 16 * 1024
    count = int(distinct)
    # Label discovery: decoded input, sorted copy, boolean flag and native output.
    discovery = pixels * (3 * value + 1)
    # Instance records and derived polygons stay with the retained evidence, not this scratch hold.
    bounds = pixels * value + count * (value + 4 * index) + width * (value + 3 * index)
    return max(discovery, bounds) + 16 * 1024


def masks_from_labels(labels: np.ndarray, origin: tuple[int, int] = (0, 0), *,
                      admit: Callable[[int], None] | None = None,
                      admit_workspace: Callable[[int], None] | None = None) -> list[tuple[int, Mask]]:
    """Each native label's own mask, in its bounding box: [(label, Mask)]. Numeric lookup IDs and
    bounds stay inside label_workspace_bytes, independent of the largest native ID. Admit the sum
    of all retained mask rectangles before allocating any; disconnected instances can overlap in
    bounding-box storage even though their occupied pixels never overlap."""
    if not labels.size or not labels.any():
        return []
    # Keep discovery inside its admitted scratch bound, including the first call. np.unique
    # imports numpy.ma lazily in recent NumPy, retaining about 1 MiB beyond this workspace.
    ordered = np.sort(labels, axis=None)
    first = np.empty(ordered.size, bool)
    first[0] = True
    np.not_equal(ordered[1:], ordered[:-1], out=first[1:])
    native = ordered[first]
    del ordered, first
    if admit_workspace is not None:
        admit_workspace(native.size)
    lower = np.full((native.size, 2), labels.shape, dtype=np.intp)
    upper = np.zeros((native.size, 2), dtype=np.intp)
    columns = np.arange(labels.shape[1], dtype=np.intp)
    for y, row in enumerate(labels):
        indices = np.searchsorted(native, row)
        np.minimum.at(lower[:, 0], indices, y)
        np.maximum.at(upper[:, 0], indices, y)
        np.minimum.at(lower[:, 1], indices, columns)
        np.maximum.at(upper[:, 1], indices, columns)
    del indices, columns
    # Each bool mask owns its complete rectangle, not merely the pixels occupied by its label.
    retained = sum((int(hi[0]) - int(lo[0]) + 1) * (int(hi[1]) - int(lo[1]) + 1)
                   for label, lo, hi in zip(native, lower, upper) if label > 0)
    if admit is not None:
        admit(retained)
    out = []
    for label, lo, hi in zip(native, lower, upper):
        if label <= 0:
            continue
        y0, x0 = int(lo[0]), int(lo[1])
        bits = labels[y0:int(hi[0]) + 1, x0:int(hi[1]) + 1] == label
        out.append((int(label), Mask(bits, origin[0] + x0, origin[1] + y0)))
    return out


def contour_workspace_bytes(bits: np.ndarray) -> int:
    """Bound contour discovery from exposed pixel edges, counted with one row of scratch.

    A contour cannot have more points than exposed grid edges, or more separate contours
    than edges/4. Cover OpenCV's image copies, point arrays, hierarchy, Python wrappers,
    sorting/set bookkeeping and one approximated contour; derived Python polygons are
    admitted separately. This bound depends on topology, never the number of native IDs.
    """
    edges = 0
    previous = np.zeros(bits.shape[1], bool)
    for row in bits:
        edges += int(np.count_nonzero(row != previous))
        edges += int(np.count_nonzero(row[1:] != row[:-1])) + int(row[0]) + int(row[-1])
        previous = row
    edges += int(np.count_nonzero(previous))
    return 2 * (bits.shape[0] + 2) * (bits.shape[1] + 2) + 128 * edges + 16 * 1024


def polygons_of(mask: Mask, min_area: float = 4.0, *, admit=None, admit_contours=None) -> list:
    """The outlines drawn from a mask, for display: every outer boundary and hole, capture px. An object too small for
    a contour (1-4 px, or a line one pixel wide: its contour has no area) is outlined by the hull of its largest piece's
    pixel squares, so every member with pixels has an outline (L13, #359: tiny blobs had none, and the masks panel
    refused the whole overlay). Its measurements come from its pixels (measure.py), never from this outline."""
    from gpu_worker.worker import mask_to_polygons

    bits = np.asarray(mask.bits, bool)
    if admit_contours is not None:
        admit_contours(contour_workspace_bytes(bits))
    polygons = mask_to_polygons(bits, offset=(mask.x0, mask.y0), min_area=min_area, scale=float(mask.scale), admit=admit)
    if polygons or not bits.any():
        return polygons
    return [_pixel_hull(bits, (mask.x0, mask.y0), float(mask.scale), admit)]


def _pixel_hull(bits: np.ndarray, offset, scale: float, admit=None) -> list:
    """The convex hull of the largest 8-connected piece's pixel squares, in the outline's own coordinates (a pixel's
    index is its centre, as in a contour): never larger than the piece's pixels reach."""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(bits.astype(np.uint8), connectivity=8)
    piece = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    ys, xs = np.nonzero(labels == piece)
    corners = np.concatenate([np.stack([xs + dx, ys + dy], axis=1) for dx in (-.5, .5) for dy in (-.5, .5)])
    hull = cv2.convexHull(corners.astype(np.float32)).reshape(-1, 2)
    if admit is not None:
        admit(len(hull))
    return [[float(x * scale + offset[0]), float(y * scale + offset[1])] for x, y in hull]


def decode_labels(wire: dict) -> np.ndarray:
    """A label map as the worker sends it: {"dtype", "shape", "zlib"} (bytes of the array)."""
    raw = zlib.decompress(wire["zlib"])
    return np.frombuffer(raw, dtype=np.dtype(wire["dtype"])).reshape(wire["shape"]).copy()


def encode_labels(labels: np.ndarray) -> dict:
    dtype = np.uint32 if labels.max(initial=0) > 65535 else np.uint16
    arr = np.ascontiguousarray(labels.astype(dtype))
    return {"dtype": str(arr.dtype), "shape": list(arr.shape), "zlib": zlib.compress(arr.tobytes(), 3)}


# --- the store -----------------------------------------------------------------------------------
class Evidence:
    """The session's evidence: the one writer of populations, their saving and their reading back. `items` are the
    populations of the session open now (and those this run made in it); another session's are read back when it is
    opened (`reload`), and one made for another session is filed there, never listed here."""

    def __init__(self, hub, root: Path | None, day=None, log: Callable[[dict], None] | None = None,
                 keep: Callable[[Path, str], None] | None = None) -> None:
        self.hub = hub
        self.root = Path(root) if root is not None else None
        self.day = day  # day.SessionStore: files each saved item as a journal event, read back at start
        self.log = log or (lambda record: None)
        self.keep = keep  # Services.keep: mirror into a dataset being recorded
        self.items: OrderedDict[str, Population] = OrderedDict()
        self.unavailable: dict[str, str] = {}  # journal-owned entries retained even when their files cannot be loaded
        self.listeners: list[Callable[[Population], None]] = []  # a population added or changed
        # The highest population and member numbers given or read back in this run (never lower: ids are new for the
        # session and for the date folder, day/ids.py), and each (prefix, session, date)'s floor, read once a run.
        self._given = {"e": 0, "c": 0}
        self._floors: dict[tuple, int] = {}
        # Populations of a session not open now that are still being saved, by (session, id): listed again if their
        # session is opened before their save is filed and read back.
        self._away: dict[tuple[str, str], Population] = {}
        self._lock = threading.RLock()  # the writer slot, the status, the pins and the release, decided together
        self._writing: set[str] = set()  # populations whose save is running (one writer each)
        self._dirty: set[str] = set()  # ...and asked to save again meanwhile
        self.disk_gb = float(os.environ.get("MICROMIND_EVIDENCE_GB") or DISK_GB)
        self._disk_used: tuple[float, int] | None = None
        if self.day is not None:
            self.read_back()
            listeners = getattr(self.day, "listeners", None)
            if isinstance(listeners, list):  # another session opened: its populations are the ones listed
                listeners.append(self._session_event)

    # --- ids ----------------------------------------------------------------------------------
    def new_id(self, session: str | None = None) -> str:
        """The next population id: new for the session it is made in (`session`, else the one open) on any of its
        days and for today's folder (day/ids.py), and never one whose folder is already on disk (a population kept
        there is never written over: scaffolding review, M1.3 item 4)."""
        while True:
            eid = f"e{self._next('e', session)}"
            if self.root is None or not any((folder / eid).exists() for folder in self._day_folders()):
                return eid

    def _date(self) -> str:
        return getattr(self.day, "date", None) or time.strftime("%Y-%m-%d")  # today's folder, by the session's clock

    def _day_folders(self) -> list[Path]:
        return [self.root / self._date()] if self.root is not None else []

    def member_id(self, session: str | None = None) -> str:
        return f"c{self._next('c', session)}"

    def _next(self, prefix: str, session: str | None) -> int:
        from .day.ids import floor, owner

        store = owner(self.day, session)
        key = (prefix, getattr(store, "sid", None), self._date())
        with self._lock:
            if key not in self._floors:  # ids given before this run (any session on today's date, this one on any)
                self._floors[key] = floor(store, prefix, self._date())
            self._given[prefix] = max(self._floors[key], self._given[prefix]) + 1
            return self._given[prefix]

    def _seen_ids(self, eid: str, members: list[str]) -> None:
        n = int(eid[1:]) if eid[1:].isdigit() else 0
        m = max((int(x[1:]) for x in members if x[1:].isdigit()), default=0)
        with self._lock:
            self._given["e"] = max(self._given["e"], n)
            self._given["c"] = max(self._given["c"], m)

    def _session(self) -> str:
        return str(getattr(self.day, "sid", "") or "")

    def _here(self, population: Population) -> bool:
        """Whether a population belongs to the session open now (one made with no session store always does)."""
        return not population.session or not self._session() or population.session == self._session()

    # --- admission ----------------------------------------------------------------------------
    def admit(self) -> None:
        """Before an analysis runs: refuse it (with the reason) past the disk budget, or while
        unsaved evidence fills its allowance (contract 1)."""
        used = self.disk_used()
        if used >= self.disk_gb * 1024 ** 3:
            raise EvidenceRefused(f"the evidence folder holds {used / 1024 ** 3:.1f} GB, its budget is {self.disk_gb:g} GB, "
                                  "and nothing is deleted automatically", next="clear older days' evidence to make room")
        failing = [p for p in self.items.values() if p.status == "failed"]
        if len(failing) >= 3:
            raise EvidenceRefused(f"saving evidence has failed {len(failing)} times ({failing[-1].status_why}), so new "
                                  "analyses wait until it works", next="check the disk has room, then ask again")

    def disk_used(self) -> float:
        if self.root is None or not self.root.exists():
            return 0.0
        now = time.monotonic()
        if self._disk_used is not None and now - self._disk_used[0] < 30:
            return self._disk_used[1]
        total = 0
        for path in self.root.rglob("*"):
            try:
                info = path.stat()
            except FileNotFoundError:  # an atomic saver can rename its temporary file during this walk
                continue
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
        self._disk_used = (now, total)
        return total

    # --- adding -------------------------------------------------------------------------------
    def add(self, population: Population) -> Population:
        with self._lock:
            if self._here(population):
                self.items[population.id] = population
            else:
                self._away[(population.session, population.id)] = population
        # Made for another session (asked there, finished after a switch): saved and filed in that session's journal,
        # never listed in the one open now (ADR 0017).
        record = getattr(self.day, "record_evidence_made", None)
        if record is not None:
            try:
                record(population.id, list(population.members)[-1] if population.members else "",
                       day=population.day or None, session=population.session or None)
            except Exception:
                pass
        self._changed(population)
        return population

    def _changed(self, population: Population) -> None:
        if not self._here(population):
            return  # another session's: nothing on this session's screen is about it
        for listener in list(self.listeners):
            try:
                listener(population)
            except Exception:
                pass

    def get(self, eid: str | None) -> Population | None:
        return self.items.get(eid) if eid else None

    def find_source_result(self, *, session: str, asset: str, implementation: str,
                           sha256: str | None = None, executions: list[str] | None = None) -> Population | None:
        """Find retained/in-flight work by captured owner and input, even after a session switch."""
        for p in reversed([*self.items.values(), *self._away.values()]):
            source = p.view.get("sourceImage") or {}
            if (p.session == session and p.implementation == implementation and source.get("asset") == asset
                    and (sha256 is None or source.get("sha256") == sha256)
                    and (executions is None or p.made_by.get("execution") in executions)):
                return p
        return None

    def latest(self, operation: str | None = None) -> Population | None:
        for p in reversed(list(self.items.values())):
            if operation is None or p.operation == operation:
                return p
        return None

    def member(self, mid: str) -> tuple[Population, Member] | tuple[None, None]:
        for p in reversed(list(self.items.values())):
            m = p.members.get(mid)
            if m is not None:
                return p, m
        return None, None

    def link(self, population: Population, kind: str, source: str, target: str, basis: str, confidence: float = 1.0) -> None:
        """A typed link (Amendment 1; ADR 0012's kinds): what is the same object, or the same kind, and how it is known."""
        from .capture import RUN

        # The run it was made in: outline ids start again at o1 in every run of the app, so a link read back from an
        # earlier run names an outline of that run, never the new one with the same id (the M2.3 re-check).
        population.links.append({"kind": kind, "from": source, "to": target, "basis": basis, "confidence": confidence,
                                 "at": round(time.time(), 3), "run": RUN})
        self.schedule_save(population)

    @contextmanager
    def exact_revision(self, population: Population, revision: int):
        """Serialize a captured population with corrections and writer status transitions.

        Prepare pixel I/O before entering. This boundary protects only the final
        identity check and publication, or one in-memory correction transaction.
        """
        with self._lock:
            retained = (self.items.get(population.id) if self._here(population)
                        else self._away.get((population.session, population.id)))
            if retained is not population or population.revision != revision:
                raise ValueError('The population changed; select its current revision again')
            yield

    # --- revisions ----------------------------------------------------------------------------
    def revise(self, population: Population, change: str, by: str, members: list[str], gray=None,
               was: dict | None = None, added: list[str] | None = None, undoes: int | None = None,
               asked: str = "", thread: str | None = None) -> Population:
        """A correction: a new revision; revision 0 (the model's) never changes; only the members
        it touches are measured again. `was`: each touched member's status before (so it can be undone
        exactly); `added`: members this revision made (a merge); `undoes`: the revision it undoes. Earlier
        revisions are never edited. It names the execution it corrects."""
        population.revision += 1
        population.revisions.append({"r": population.revision, "change": change, "by": by, "members": members,
                                     "was": dict(was or {}), "added": list(added or []), "undoes": undoes,
                                     "execution": (population.made_by or {}).get("execution"), "at": round(time.time(), 3),
                                     **({"asked": asked} if asked else {}),  # the user's own words for it (M2.3 review S7)
                                     **({"thread": thread} if thread else {})})  # the conversation it was asked in (re-check S3)
        population._labels = None
        # Every other column group was made from the members as they were: stale from now (Population.stale, by
        # revision), made again on demand; the measurements are measured again here.
        measured = population.columns.get("measure")
        if measured is not None:
            disc = tuple(population.view["disc"]) if population.view.get("disc") else None
            for mid in members:
                m = population.members.get(mid)
                if m is not None and m.status not in ("deleted", "merged") and m.mask is not None:
                    measured["values"][mid] = measure(m.mask, gray, disc, population.region)
                else:
                    measured["values"].pop(mid, None)
            masks = [m.mask for m in population.live() if m.mask is not None]
            measured["coverage"] = coverage(masks, disc, (population.view["width"], population.view["height"])) if masks else 0.0
            measured["revision"] = population.revision
        self.schedule_save(population)
        self._changed(population)
        return population

    # --- saving -------------------------------------------------------------------------------
    def _keep_summary(self, population: Population) -> None:
        """The table outlives its pixels, until the last reader lets this population go."""
        from .capture import captures

        needed = summary_bytes(population)
        if population.summary_hold is not None:
            population.summary_hold.grow(max(0, needed - population.summary_hold.nbytes), "retaining evidence's table")
            population.summary_hold.settle(needed)
            return
        if population.hold is not None and population.hold.reservation.cls in BACKGROUND:
            # A background run's table (a researcher's, an experiment's) is kept in an allowance of its own, so its
            # working allowance is free again once the run is saved (capture.ALLOWANCES_MB "kept"); with no room
            # there, it stays with its hold as below.
            from .capture import Refused as MemoryRefused

            try:
                kept = population.hold.reservation.memory.reserve("kept", needed, "a background run's evidence table")
            except (MemoryRefused, ValueError):
                kept = None
            if kept is not None:
                population.summary_hold = kept
                weakref.finalize(population, kept.release)
                return
        if population.hold is not None:
            # Transfer its existing capacity; the pixel hold still owns every resident image/mask.
            pixels = sum(m.mask.bits.nbytes for m in population.members.values() if m.mask is not None)
            if population.capture is not None:
                pixels += population.capture.resident
            reservation = population.hold.reservation
            reservation.grow(max(0, pixels + needed - reservation.nbytes), "retaining evidence's table")
            kept = reservation.split(needed, "resident evidence table")
        else:
            kept = captures(self.hub).memory.reserve("unsaved", needed, "resident evidence table")
        population.summary_hold = kept
        weakref.finalize(population, kept.release)

    def schedule_save(self, population: Population) -> None:
        """Save it in the background. One writer per population (scaffolding review, M1.3 item 2): a save
        asked for while one runs marks it dirty, and it is saved once more when that one is done."""
        if self.root is None:
            population.status, population.status_why = "unsaved", "no evidence folder in this run"
            return
        with self._lock:
            self._keep_summary(population)
            population.status = "saving"
            if population.id in self._writing:
                self._dirty.add(population.id)
                return
            self._writing.add(population.id)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        # The files are written off the loop; filing the saved item (the journal, listeners) happens
        # back on it, where the day's state lives.
        threading.Thread(target=self._save, args=(population, loop), daemon=True, name=f"evidence-{population.id}").start()

    def _done_writing(self, population: Population) -> bool:
        """The writer finished; True when another save was asked for meanwhile (it is started now)."""
        with self._lock:
            self._writing.discard(population.id)
            again = population.id in self._dirty
            self._dirty.discard(population.id)
        if again:
            self.schedule_save(population)
        return again

    def _on_loop(self, loop, fn, *args) -> None:
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(fn, *args)
        else:
            fn(*args)

    def _save(self, population: Population, loop=None) -> None:
        try:
            folder = self.root / (population.day or time.strftime("%Y-%m-%d")) / population.id
            folder.mkdir(parents=True, exist_ok=True)
            files = {}
            if population.capture is not None and getattr(population.capture, "jpeg", None):
                files["capture.jpg"] = population.capture.jpeg
            elif (folder / "capture.jpg").exists():
                files["capture.jpg"] = (folder / "capture.jpg").read_bytes()
            if population.view.get("sourceImage"):
                files["source.image"] = self.capture_native(population)
            files["masks.npz"], written = self._masks_bytes(population)
            if population.vectors or (folder / "vectors.npz").exists():
                files["vectors.npz"] = self._vectors_bytes(population, folder)
            sums = {}
            for name, data in files.items():
                _atomic(folder / name, data)
                sums[name] = hashlib.sha256(data).hexdigest()
            manifest = self.manifest(population, sums)
            _atomic(folder / "manifest.json", json.dumps(manifest).encode())  # the manifest last
        except Exception as exc:
            self._on_loop(loop, self._failed, population, f"{type(exc).__name__}: {exc}"[:200])
            return
        self._on_loop(loop, self._saved, population, folder, written)

    def _saved(self, population: Population, folder: Path, written: set[str] = frozenset()) -> None:
        population.path = folder
        self._disk_used = None
        # Off the loop this runs on the writer thread while others change the population: the slot is freed, the
        # status said and the pixels released at once, and only masks this file holds are released, so a save
        # asked for meanwhile never finds a mask it has to write gone (finding 84).
        with self._lock:
            population.saved_masks.update(written)
            if population.id in self._dirty and self._done_writing(population):
                return  # changed while it was written (a link, a revision): saved again, said once that is done
        # It is saved once its session's journal files it: a restart reads it back from there. Until then the writer
        # slot stays taken (a save asked for meanwhile waits for it) and the status says "saving", so whoever waits for
        # "saved" (the walkthrough, a restart's read_back) finds it filed. A filing that did not finish is said as
        # failed, never as saved, and keeps its pixels (a restart would not find it).
        why = "its session's journal did not file it"
        try:
            if self.day is not None:
                record = getattr(self.day, "record_evidence", None)
                if record is not None:
                    from .population import filed_thread

                    # Filed in the session it was made in, under its conversation (a session's conversations keep
                    # their ids for good); one from before sessions, only while that conversation is still the one
                    # loaded (S2).
                    record(population.id, str(folder), filed_thread(self.hub, population.thread, population.session,
                                                                    population.generation),
                           population.revision, list(population.members), day=population.day or None,
                           session=population.session or None)
            why = ""
        except Exception as exc:
            why = f"its session's journal did not file it ({type(exc).__name__}: {exc})"[:200]
        finally:
            with self._lock:
                again = self._done_writing(population)
                if not again:
                    if why:
                        population.status, population.status_why = "failed", why
                    else:
                        population.status, population.status_why = "saved", ""
                        self._release_pixels(population)
        if again:
            return  # changed while it was filed: saved again, said once that is done
        if why:
            self.log({"kind": "evidence-save-failed", "id": population.id, "error": why})
            self._changed(population)
            return
        if self.keep is not None:
            for name in ("manifest.json", "capture.jpg", "source.image"):
                if (folder / name).exists():
                    try:
                        # Unique names: a dataset's pictures folder is flat, and a name already there is skipped.
                        self.keep(folder / name, f"evidence-{population.day}-{population.id}-r{population.revision}-{name}")
                    except Exception:
                        pass
        self.log({"kind": "evidence-saved", "id": population.id, "revision": population.revision,
                  "members": len(population.live()), "path": str(folder)})
        self._changed(population)

    def _failed(self, population: Population, why: str) -> None:
        with self._lock:
            if self._done_writing(population):
                return  # the next save may succeed: its outcome is the one said
            population.status, population.status_why = "failed", why
        self.log({"kind": "evidence-save-failed", "id": population.id, "error": why})
        self._changed(population)

    def _masks_bytes(self, population: Population) -> tuple[bytes, set[str]]:
        """Every member's mask ever made: what is saved already (pixels released after a save), with the
        resident ones over it, so a revision saved later (a merged member added) never drops the rest. With the
        members whose masks it holds (only those are released once it is saved)."""
        arrays = {}
        saved = population.path / "masks.npz" if population.path is not None else None
        if saved is not None and saved.exists():
            with np.load(saved) as data:
                arrays.update({name: data[name] for name in data.files})
        for m in list(population.members.values()):  # a copy: a correction on another thread may add one meanwhile
            if m.mask is None:
                continue
            bits = np.asarray(m.mask.bits, bool)
            arrays[f"{m.id}.bits"] = np.packbits(bits)
            arrays[f"{m.id}.meta"] = np.array([bits.shape[0], bits.shape[1], m.mask.x0, m.mask.y0, m.mask.scale], np.float64)
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        return buffer.getvalue(), {name[:-len(".bits")] for name in arrays if name.endswith(".bits")}

    def manifest(self, population: Population, sums: dict) -> dict:
        made = population.made_by or {}
        return {"schema": SCHEMA, "id": population.id, "title": population.title, "operation": population.operation,
                "implementation": population.implementation, "madeBy": population.made_by,
                # Interpreting it needs none of the optional logs (Amendment 4, item 3; the partner, §1f).
                "execution": made.get("execution"), "policy": made.get("policy"), "origin": made.get("origin"),
                "producer": population.producer, "transformations": population.transformations,
                "source": {"capture": population.capture_id, "boot": population.view.get("boot"),
                           "seq": population.view.get("seq"),
                           **({"image": population.view["sourceImage"]} if population.view.get("sourceImage") else {})},
                "capturedAt": population.view.get("at"),
                "capture": population.capture_id, "view": population.view, "scaleId": population.scale_id,
                "region": list(population.region) if population.region else None, "ticket": population.ticket,
                "thread": population.thread, "day": population.day, "session": population.session,
                "madeAt": population.made_at,
                "revision": population.revision, "revisions": population.revisions, "example": population.example,
                "overlapping": population.overlapping, "members": [m.row() for m in list(population.members.values())],
                "polygons": {m.id: m.polygons for m in list(population.members.values())},
                "columns": population.columns, "original": population.original,
                "links": population.links, "files": sums}

    def _release_pixels(self, population: Population) -> None:
        """Saved: its pixels leave memory (they reload from disk when needed); the table stays. Only masks its saved
        file holds: a member made since that save keeps its mask until a save writes it. Not while a correction
        holds them (`keeping_pixels`): then once it is done."""
        with self._lock:
            if population.pins:
                population.release_pending = True
                return
            population.capture = None
            population._labels = None
            for m in list(population.members.values()):
                if m.id in population.saved_masks:
                    m.mask = None
            population.vectors = {}  # reloaded from vectors.npz
            if population.vector_hold is not None:
                population.vector_hold.release()
                population.vector_hold = None
            if population.hold is not None:
                population.hold.release()
                population.hold = None

    def _vectors_bytes(self, population: Population, folder: Path) -> bytes:
        """Every vector group: the saved ones, with the resident ones over them (a later group never drops one)."""
        arrays = {}
        saved = folder / "vectors.npz"
        if saved.exists():
            try:
                with np.load(saved) as data:
                    arrays.update({name: data[name] for name in data.files})
            except (OSError, ValueError, zlib.error):
                pass  # unreadable (said at read-back, its looks dropped): what is resident is saved over it
        for gid, kept in list(population.vectors.items()):
            arrays[f"{gid}.ids"] = np.array(kept["ids"], dtype=str)
            arrays[f"{gid}.v"] = np.asarray(kept["array"], np.float32)
        buffer = io.BytesIO()
        np.savez_compressed(buffer, **arrays)
        return buffer.getvalue()

    def vectors_of(self, population: Population, gid: str) -> dict | None:
        """A vector group's {"ids", "array"}: resident, or read from its saved file (not kept resident). It reads
        a file: callers run it off the event loop, in room they reserved (`vectors_bytes`)."""
        if gid in population.vectors:
            return population.vectors[gid]
        path = population.path / "vectors.npz" if population.path is not None else None
        if path is None or not path.exists():
            return None
        with np.load(path) as data:
            if f"{gid}.v" not in data.files:
                return None
            return {"ids": [str(i) for i in data[f"{gid}.ids"]], "array": data[f"{gid}.v"].astype(np.float32)}

    @staticmethod
    def vectors_bytes(population: Population, gid: str) -> int:
        """What reading a vector group takes, from its column's dimension: n x dim float32 (twice, the file's
        copy and the array), so it is reserved before it is read (invariant 3)."""
        dim = int(((population.columns.get(gid) or {}).get("columns") or {}).get("look", {}).get("dim") or 1024)
        return 2 * len(population.members) * dim * 4

    def masks_for(self, population: Population, members: list[Member]) -> dict[str, Mask]:
        """Each member's original mask: resident, or read from the saved masks.npz and not kept (the population's
        pixels stay released). Reads a file: off the event loop, inside room reserved for `mask_bytes`."""
        out = {m.id: m.mask for m in members if m.mask is not None}
        wanted = [m for m in members if m.mask is None]
        if wanted and population.path is not None and (population.path / "masks.npz").exists():
            with np.load(population.path / "masks.npz") as data:
                for m in wanted:
                    if f"{m.id}.bits" in data.files:
                        h, w, x0, y0, sc = data[f"{m.id}.meta"]
                        bits = np.unpackbits(data[f"{m.id}.bits"])[: int(h) * int(w)].reshape(int(h), int(w)).astype(bool)
                        out[m.id] = Mask(bits, int(x0), int(y0), float(sc))
        return out

    @staticmethod
    def mask_bytes(members: list[Member]) -> int:
        """The room reading members' masks back takes at most: each mask and its full-resolution copy, from its
        box in capture px (a mask is never larger than its member's box at full resolution)."""
        total = 0
        for m in members:
            if m.mask is None:
                x0, y0, x1, y1 = m.box
                total += 2 * int(max(1.0, x1 - x0 + 2) * max(1.0, y1 - y0 + 2))
        return total

    def keep_vectors(self, population: Population, gid: str, ids: list[str], array: np.ndarray) -> None:
        """A vector group resident until its population is saved, reserved first (invariant 3): Refused
        when the unsaved allowance cannot take it."""
        array = np.asarray(array, np.float32)
        nbytes = int(array.nbytes)
        # Installing resident vectors, reserving them and asking for their save
        # must precede (or follow) a writer's release as one transition.
        with self._lock:
            if population.vector_hold is None:
                from .capture import captures

                population.vector_hold = captures(self.hub).memory.reserve("unsaved", nbytes, f"{population.title}'s looks, until saved")
            else:
                population.vector_hold.grow(nbytes, f"{population.title}'s looks, until saved")
            population.vectors[gid] = {"ids": list(ids), "array": array}
            self.schedule_save(population)

    @contextmanager
    def keeping_pixels(self, population: Population):
        """Its masks stay in memory from the read back until the correction is done (the M2.3 re-check's race: the
        previous correction's save finished while the masks were read in a thread, and released them before the
        correction used them)."""
        with self._lock:  # a writer thread deciding to release meanwhile sees the pin, or has released before it
            population.pins += 1
        try:
            yield population
        finally:
            with self._lock:
                population.pins -= 1
                # The hold ends: pixels a save released meanwhile go now, and so do those read back for a correction
                # that changed nothing (still saved; a correction that did change it is being saved, and that save
                # releases them).
                if not population.pins and (population.release_pending or population.status == "saved"):
                    population.release_pending = False
                    if population.status == "saved":
                        self._release_pixels(population)

    def reload_masks(self, population: Population) -> bool:
        """The members' original masks, from disk, when they were released."""
        if population.path is None or not (population.path / "masks.npz").exists():
            return False
        data = np.load(population.path / "masks.npz")
        population.saved_masks.update(name[:-len(".bits")] for name in data.files if name.endswith(".bits"))
        for m in population.members.values():
            if m.mask is None and f"{m.id}.bits" in data:
                h, w, x0, y0, s = data[f"{m.id}.meta"]
                bits = np.unpackbits(data[f"{m.id}.bits"])[: int(h) * int(w)].reshape(int(h), int(w)).astype(bool)
                m.mask = Mask(bits, int(x0), int(y0), float(s))
        return True

    def capture_jpeg(self, population: Population, max_bytes: int | None = None) -> bytes | None:
        from .resources import read_picture_bytes
        if population.capture is not None and getattr(population.capture, "jpeg", None):
            return population.capture.jpeg
        if population.path is not None and (population.path / "capture.jpg").exists():
            return read_picture_bytes(population.path / "capture.jpg", max_bytes)

    def require_retained(self, population: Population) -> None:
        """Check persisted scientific inputs before reopening or correcting; never substitute a preview."""
        if population.path is None or population.status == "saving":
            return  # a resident result, or the app's own atomic save still publishing its files
        try:
            self.checked_manifest(population.path)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            population.status = "unavailable"
            population.status_why = f"{population.id} is unavailable: {exc}; restore its evidence folder from backup"
            raise ToolFailed(population.status_why, next="restore the retained data, then reopen the session") from None

    @staticmethod
    def read_retained(path: Path, limit: int) -> bytes:
        """Bounded regular evidence bytes without following a replaced file symlink."""
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('Retained evidence must be a regular file')
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError('Retained evidence exceeds the requested byte allowance')
        return data

    @staticmethod
    def checked_manifest(folder: Path, *, max_bytes: int | None = None) -> dict:
        """Validate the required retained files; optional enrichment loss is handled separately."""
        read = (lambda path: Evidence.read_retained(path, max_bytes)) if max_bytes is not None else lambda path: path.read_bytes()
        record = json.loads(read(folder / "manifest.json"))
        if not isinstance(record, dict) or record.get("schema") != SCHEMA:
            raise ValueError("the evidence manifest cannot be read")
        if not isinstance(record.get("view"), dict) or not isinstance(record.get("files"), dict):
            raise ValueError("the evidence manifest has invalid view or files metadata")
        required = {"capture.jpg", "masks.npz"}
        if record.get("view", {}).get("sourceImage"):
            required.add("source.image")
        sums = record.get("files") or {}
        if not required <= sums.keys():
            raise ValueError("the evidence manifest omits a required file")
        for name, digest in sums.items():
            if name in OPTIONAL_FILES:
                continue
            if name not in required:
                raise ValueError("the evidence manifest contains an unknown file")
            path = folder / name
            if path.is_symlink() or hashlib.sha256(read(path)).hexdigest() != digest:
                raise ValueError(f"{name} is missing or changed")
        return record

    def capture_native(self, population: Population, max_bytes: int | None = None) -> bytes | None:
        """Original imported bytes, verified against their capture; no preview fallback."""
        from .capture import CaptureGone
        from .resources import read_picture_bytes

        source = population.view.get("sourceImage")
        if source is None:
            return None
        native = getattr(population.capture, "native", None)
        if native is None:
            try:
                native = read_picture_bytes(population.path / "source.image", max_bytes) if population.path else None
            except OSError:
                native = None
        if native is None or hashlib.sha256(native).hexdigest() != source.get("sha256"):
            raise CaptureGone("the retained original picture is missing or changed",
                              next="restore source.image from your backup, or rerun the analysis from the original asset")
        return native

    # --- reading back -------------------------------------------------------------------------
    def _session_event(self, event: dict) -> None:
        if event.get("type") == "session-opened":
            self.reload()

    def reload(self) -> list[str]:
        """Another session was opened (ADR 0017): the populations listed are now its own, read back from its journal
        (every date it touched) as history, never shown live (the app hides the layer). The ones left stay filed in
        their session; one still being saved finishes and is filed there."""
        with self._lock:
            saving = {(p.session, p.id): p for p in self.items.values() if p.session and p.status == "saving"}
            self._away = {k: p for k, p in {**self._away, **saving}.items() if p.status == "saving"}
            self.items = OrderedDict()
        loaded = self.read_back()
        with self._lock:  # its own still being saved: listed again until their save is filed
            for (sid, pid), p in list(self._away.items()):
                if sid == self._session():
                    self.items.setdefault(pid, p)
                    del self._away[(sid, pid)]
        return loaded

    def read_back(self) -> list[str]:
        """The session's saved evidence, from its journal events (ADR 0001), whatever date folder each is in: each
        manifest checked against its files; a partial one is reported "incomplete, not loaded"."""
        loaded, problems = [], []
        self.unavailable = {}
        # Every id the session made is used, whatever became of its save (a torn one included).
        for eid, last in (getattr(self.day, "evidence_ids", {}) or {}).items():
            self._seen_ids(eid, [last] if last else [])
        for eid, entry in (getattr(self.day, "evidence", {}) or {}).items():
            self._seen_ids(eid, list(entry.get("members") or []))
            with ExitStack() as scope:
                folder = Path(entry["path"])
                try:
                    from .capture import Refused, captures

                    # JSON's smallest scalars/containers can occupy far more than their bytes on disk.
                    # This covers parsing, manifest plus Member wrappers, and the retained table's admission.
                    reading = captures(self.hub).memory.reserve(
                        "analysis", (folder / "manifest.json").stat().st_size * 64 + 16384,
                        "reading saved evidence's table")
                    scope.enter_context(reading)
                    population = self._read_population(eid, entry, folder, problems)
                except Refused as exc:
                    problems.append(f"{eid}: not loaded in memory ({exc})"[:200])
                    self.unavailable[eid] = f"{eid} is unavailable in memory: {exc}; close earlier analyses and reopen"[:300]
                    continue
                except Exception as exc:
                    problems.append(f"{eid}: incomplete, not loaded ({type(exc).__name__}: {exc})"[:200])
                    self.unavailable[eid] = f"{eid} is unavailable: {exc}; restore its evidence folder from backup"[:300]
                    continue
                self.items[population.id] = population
                self._seen_ids(population.id, list(population.members))
                loaded.append(population.id)
        if loaded or problems:
            self.log({"kind": "evidence-read-back", "loaded": loaded, "problems": problems})
        self.problems = problems
        return loaded


    def _read_population(self, eid: str, entry: dict, folder: Path, problems: list[str]) -> Population:
        """Parse and admit one saved table while read_back owns its parsing reservation.

        Rejected locals and their traceback die inside that reservation, before another entry
        can allocate. A returned population has its own resident summary reservation.
        """
        lost: list[str] = []
        manifest = self.checked_manifest(folder)
        for name, digest in (manifest.get("files") or {}).items():
            try:
                same = hashlib.sha256((folder / name).read_bytes()).hexdigest() == digest
            except OSError:
                same = False
            if not same and name in OPTIONAL_FILES:
                lost.append(name)  # read back without it (K1 review note): the rest is the evidence
            elif not same:
                raise ValueError(f"{name} does not match its checksum")
        if manifest.get("schema") != SCHEMA:
            raise ValueError(f"schema {manifest.get('schema')}")
        members = OrderedDict()
        polygons = manifest.get("polygons") or {}
        for row in manifest["members"]:
            members[row["id"]] = Member(row["id"], row["instance"], None, polygons.get(row["id"], []),
                                        tuple(row["box"]), row.get("extras") or {}, row.get("status") or "model")
        population = Population(manifest["id"], manifest["operation"], manifest.get("implementation"),
                                manifest.get("madeBy") or {}, manifest["capture"], manifest["view"],
                                manifest.get("scaleId"), tuple(manifest["region"]) if manifest.get("region") else None,
                                # The journal's conversation, not the manifest's (ADR 0001; M3.2 re-check, S1).
                                members, ticket=manifest.get("ticket"), thread=entry.get("thread", manifest.get("thread")),
                                day=manifest.get("day") or "", made_at=manifest.get("madeAt") or 0.0,
                                revision=manifest.get("revision") or 0, revisions=manifest.get("revisions") or [],
                                columns=manifest.get("columns") or {}, links=manifest.get("links") or [],
                                original=manifest.get("original"),
                                status="saved", path=folder, example=manifest.get("example"),
                                overlapping=bool(manifest.get("overlapping")), producer=manifest.get("producer") or {},
                                transformations=manifest.get("transformations") or {},
                                generation=getattr(self.day, "generation", None),  # filed in this journal: as loaded
                                session=self._session())
        if population.revision == 0 and population.original is None:
            population.original = {"revision": 0, "members": copy.deepcopy([m.row() for m in population.live()]),
                                   "columns": copy.deepcopy(population.columns)}
        if "vectors.npz" in lost:
            # Its looks' vectors are gone: those groups go (they are made again when asked); the masks, the
            # measurements, the revisions and the other groups are read back.
            for gid in [g for g in population.columns if g.startswith("looks:")]:
                population.columns.pop(gid)
            problems.append(f"{eid}: its looks were not read back (vectors.npz missing or changed); they are "
                            "made again when asked")
        self._keep_summary(population)
        return population


def _enrichment_kinds(population) -> list[str]:
    from .enrichment import kinds

    return kinds(population)


# Allowances a run in the background holds its evidence in (capture.py): its table, once saved, is kept apart ("kept").
BACKGROUND = frozenset({"research"})


def summary_bytes(population: Population) -> int:
    """Resident records/geometry/measurements, excluding separately held pixels and vectors.

    Shared immutable values are deliberately counted at every occurrence. These are acyclic
    saved-data trees; walking them needs no image-sized copy or identity set.
    """
    def size(value):
        total = sys.getsizeof(value)
        if isinstance(value, dict):
            total += sum(size(k) + size(v) for k, v in value.items())
        elif isinstance(value, (list, tuple, set)):
            total += sum(size(v) for v in value)
        elif isinstance(value, Member):
            total += sys.getsizeof(value.__dict__) + sum(size(v) for k, v in value.__dict__.items() if k != "mask")
        return total

    omitted = {"capture", "hold", "summary_hold", "vector_hold", "vectors", "_labels", "saved_masks"}
    # A successful save adds these IDs; reserve the set and its strings before that transition too.
    saved_ids = sys.getsizeof(set()) + 128 * len(population.members)
    return saved_ids + sys.getsizeof(population) + sys.getsizeof(population.__dict__) + sum(
        size(v) for k, v in population.__dict__.items() if k not in omitted)


def _atomic(path: Path, data: bytes) -> None:
    # A name of its own (process, thread, count): two writers never share a temporary file.
    tmp = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}-{next(_tmp_ids)}.tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def evidence_of(hub) -> Evidence:
    """The hub's evidence store (made on first use, without saving, for a test's stand-in hub)."""
    store = getattr(hub, "evidence", None)
    if not isinstance(store, Evidence):
        store = Evidence(hub, None)
        try:
            hub.evidence = store
        except AttributeError:
            pass
    return store
