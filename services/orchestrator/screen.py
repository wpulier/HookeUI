"""What is actually on screen, and one set of verbs for everything on it (contract 10; Amendment 2,
points 1-5, and Amendment 4, items 4-6, of docs/design/2026-10-06-captures-operations-evidence.md).

- **The studio reports what it displays** (`Screen.report`): every layer and panel, what it shows,
  where it is (workspace px), its stacking order and its style (visible, opacity, colour, fill, an
  emphasised selection). The Board reads it: one line per thing shown.
- **Presentations** are what can be acted on: the live view, the population layer, the app's
  outlines, the magnifier, the result viewer, the slide map and the details card. Each kind takes some
  of one set of verbs (show, hide, opacity, colour, fill, edge, emphasise, open, close, move, resize,
  front); a verb that does not apply fails with the reason (never succeeds on something else).
- **Done means the screen shows it** (ADR 0006 extended to the screen): a change is sent with an id and
  counts as done only once a report says it was applied and shows it. Until then the run is still
  being done; if no report shows it in time, it failed, and that is said.
- **Who changed what** (Amendment 4, item 4): every property a change sets records who set it and what
  it replaced, the user's own changes (reported by the studio) included, so a later showing can restore
  only its own changes the user has not touched since.
- **A look at the workspace** (`look_at_screen`) is a separate channel: the studio composes a picture
  of what is shown, which goes to that one vision call only, never to Sight, naming or any specimen
  model, and is kept only as a performance-log picture.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import copy
import hashlib
import io
import itertools
import json
import math
import time
import weakref
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable

from . import spaces
from .workspace_observation import (
    ClientRef, Limits, Observation, ObservationError, RequestSnapshot,
    check_target_current, normalize_capture_reply, normalize_capture_unavailable, normalize_observation, pin_request,
)


def _workspace_error(code: str, detail: str):
    raise ObservationError(code, detail)


def _workspace_id(value):
    if not isinstance(value, str) or not value or len(value) > 256:
        _workspace_error("INVALID_REPORT", "expected a bounded workspace identifier")
    return value


# Fixed registered widget recipes, not a parser that grants capabilities from an ID.
# A server adapter can supply its explicit id -> {name, role} registration instead.
CONTROL_ROLES = {"opacity": "slider", "move": "button", "resize": "button", "zoom": "button",
                 "pan": "button", "front": "button", "close": "button"}


@dataclass
class _AcceptedWorkspace:
    ref: ClientRef
    app_run_id: str
    source_resolver: Callable
    capture_authority: Callable
    limits: Limits
    control_names: dict | None
    closed_source_resolver: Callable | None = None  # metadata only, never a media grant
    failed_source_resolver: Callable | None = None  # open error state only, never pixel authority
    captures: OrderedDict = field(default_factory=OrderedDict)  # bounded weak acquisition receipts, no owned pixels
    changes: OrderedDict = field(default_factory=OrderedDict)  # original structural Changes, not native outcomes

VERBS = ("show", "hide", "opacity", "colour", "fill", "edge", "emphasise", "open", "close", "move", "resize", "front")

# What each kind of presentation takes (Amendment 2, point 3; Amendment 4, items 5 and 6).
TAKES = {
    "document": ("close", "move", "resize", "front"),
    "entry": ("open", "show"),  # an index entry ("show me the motion one"): opened as its presentation
    "live": ("opacity",),
    "population": ("show", "hide", "opacity", "colour", "fill", "edge", "emphasise"),
    # Motion tracks (M3.2, motiontracks.py): lines, drawn live only where their place is verified.
    "tracks": ("show", "hide", "opacity", "colour", "emphasise"),
    "outlines": ("show", "hide", "opacity", "colour", "fill", "edge"),
    "magnifier": ("open", "close", "move", "resize", "front"),
    "viewer": ("open", "close", "front"),
    "slidemap": ("open", "close"),
    "card": ("close", "front"),
    "made-today": ("close", "front"),  # what this session made (madetoday.ts): the sidebar's Made section
    # The left sidebar is the session (sidebar.ts): one section at a time (sources: this session's assets, made,
    # researchers, activity), reported with the section as `shows` and its name as `title`.
    "sidebar": ("open", "close", "hide"),
    "sessions": ("open", "close"),  # the sessions list: the session's title's menu (session-menu.ts), its own item
}
WHY_NOT = {  # a verb a kind does not take, said with the reason and what to do instead
    # In a Tab the live view is the specimen, which tabs floats or hides (never on Main): present only dims it.
    ("live", "hide"): ("the live view is never hidden: it is the microscope",
                       "dim it instead (opacity); to float or hide it in a Tab, tabs action specimen"),
    ("live", "close"): ("the live view is never closed: it is the microscope",
                        "dim it instead (opacity); to float or hide it in a Tab, tabs action specimen"),
    ("viewer", "move"): ("the result viewer sits in the side column and does not move", "close it, or open it again"),
    ("viewer", "resize"): ("the result viewer takes the side column's width", "close it to give the view its room"),
    ("tracks", "fill"): ("tracks are lines, not areas: they can't be filled", "colour them, fade them or keep the fast ones"),
    ("tracks", "edge"): ("tracks are always drawn as lines", "colour them, fade them or keep the fast ones"),
}
# How each kind of entry in the session's index is shown (Amendment 2, point 6: horizontal by construction):
# the presentation that opens it, or None with why it has none. tests/test_screen.py checks every kind.
# READER is the Data reader on the Tab you are on (ticket 66, D6; data_reader.py), a document surface of the studio's
# report: the kinds Data lists open there, as a person's click on their row opens them. A studio without the reader
# shows them as WITHOUT_READER says. Cell outlines and motion tracks are layers: show draws them on the picture (the
# reader's Show on picture), and open puts them in the reader.
READER = "document"
PRESENTED_AS = {
    "population": ("population", ""),
    "member": ("magnifier", ""),
    "result": (READER, ""),
    "snapshot": (READER, ""),
    "research": (READER, ""),
    "named-outline": (None, "a thing named earlier is shown by outlining it again (find or highlight)"),
    "taught-label": (None, "a label you taught is used when naming, not shown"),
    "calibration": (None, "the scale shows as the scale bar on the live view"),
    "dataset": (READER, ""),
    "recording": (READER, ""),
    "bookmark": (READER, ""),
    "volume": (READER, ""),
    "job": ("magnifier", ""),  # a registration's picture, in brainreg's own downsampled space (registrations.py)
    "clip": (READER, ""),
    "tracks": ("tracks", ""),  # over the live view where verified, else on the view they were measured on
    "outcome": (None, "an outcome is read: how a model has done here"),
    "fact": (None, "a fact is read: what was settled today and by whom"),
    "annotation": (READER, ""),
    "dataset-table": (READER, ""),
    "analysis": (READER, ""),
    "graph": (READER, ""),
    "project": (READER, ""),
    # An individual's outline is on the live view only while its own pixels match where it is: not while it is being
    # re-found or lost, nor one from before a restart (the K2 re-check, note 4).
    "individual": (None, "a followed thing is read: where it is now and whether it changed; its outline is drawn on the "
                         "live view only while its own pixels match where it is"),
    "asset": (READER, ""),  # one of the session's sources (ADR 0022); put on stage from its Assets
    # A researcher's other work (research_entries.py; ticket 66).
    "lesson": (READER, ""),
    "video": (READER, ""),
    "figure": (READER, ""),
    "finding-table": (READER, ""),
    "finding-chart": (READER, ""),
    "source": (READER, ""),
}
_NO_READER = (None, "it opens in the Data reader, which this studio does not have")
WITHOUT_READER = {
    "result": ("magnifier", ""),  # its picture, in the magnifier
    "snapshot": ("magnifier", ""),
    "research": ("viewer", ""),
    "dataset": (None, "datasets are in the Datasets tab"),
    "recording": (None, "recordings are in the Datasets tab"),
    "bookmark": ("magnifier", ""),  # the view it kept, in the magnifier (way_to_bookmark says which way it is)
    "volume": ("magnifier", ""),  # its middle slice, as it was added
    "clip": ("magnifier", ""),  # held footage: a frame of it, in the magnifier
    "annotation": (None, "a user note is read; open its retained source separately"),
    "dataset-table": (None, "open the exact saved table from Data or a named work Tab; the live present verb does not open table windows"),
    "analysis": (None, "open the exact accepted analysis from Data or a named work Tab; numeric reads never recompute it"),
    "graph": (None, "open the exact accepted graph from Data or a named work Tab; its values and settings are retained"),
    "project": (None, "a scientific project's saved state can be read; it has no screen viewer here"),
    # It is put on stage by the user's Sources section, where it plays in the live view's place, not by present.
    "asset": (None, "a recording or picture of the session is put on stage from its Assets (the sidebar) or All assets, where it plays in the live view"),
    "lesson": _NO_READER, "video": _NO_READER, "figure": _NO_READER, "finding-table": _NO_READER,
    "finding-chart": _NO_READER, "source": _NO_READER,
}


def presented(kind: str, reader: bool = True) -> tuple[str | None, str]:
    """How an entry of `kind` is shown: PRESENTED_AS, or in a studio without the Data reader, WITHOUT_READER."""
    shown_as, why = PRESENTED_AS.get(kind, (None, "it has no presentation"))
    if shown_as == READER and not reader:
        return WITHOUT_READER.get(kind, _NO_READER)
    return shown_as, why
NAMES = {"document": "the Project document", "live": "the live view", "population": "the cell outlines", "outlines": "the app's outlines", "magnifier": "the magnifier",
         "viewer": "the result viewer", "slidemap": "the slide map", "card": "the details card",
         "made-today": "what was made in this session", "tracks": "the motion tracks",
         "sidebar": "the sidebar", "sessions": "the sessions list"}
# The next step said when the studio's report cannot confirm something (ToolFailed.next). The studio is this app's own
# window: there is nothing to open, refresh or restart, and what was asked is saved (live 2026-10-28: next steps telling
# the user to check or to open the app's window became "I can't see your screen ... quit and reopen the app"). Asking
# again reads the next report.
REPORT_NEXT = "ask again in a moment: saved work is kept, and the next report is read"
CONFIRM_S = 4.0  # how long a change waits for the report that shows it
REPORT_FRESH_S = 30.0  # a studio that has not reported for this long is not counted as showing anything
REFRESH_S = 0.5  # a request with no report of its own waits at most this for the studio's report now (Screen.refresh)
REFRESH_ASK_EVERY_S = 0.25
REFRESH_FRESH_S = 0.15  # a report taken this recently is already what the studio shows now
# A request whose studio has not reported for REPORT_FRESH_S asks it for a report and waits this long for one before
# its steps run, so its tools read what it shows now instead of failing as "stale" (live 2026-10-29).
STALE_REFRESH_S = 2.0


@dataclass
class Change:
    id: str
    target: str
    kind: str
    verb: str
    set: dict
    by: str
    at: float = field(default_factory=time.time)
    actor: str | None = None  # user control vs assistant; ownership tokens remain unchanged
    before: dict = field(default_factory=dict)  # the item as reported before it was sent
    _structural: _StructuralChange | None = field(default=None, repr=False, compare=False)

    def wire(self) -> dict:
        return {"type": "present", "change": self.id, "id": self.target, "kind": self.kind, "verb": self.verb, "set": self.set}


@dataclass
class _StructuralChange:
    change: Change
    client: Any
    profile: _AcceptedWorkspace
    target: str
    kind: str
    root: str
    lifetime: str | int
    issued_seq: int
    signature: str
    owners: dict
    pending: bool = True  # The original confirmation lifecycle, not a native success certificate.
    completed: bool | None = None
    confirmation: asyncio.Future | None = field(default=None, repr=False)  # one owner's terminal result; joins only read it


BOOT = format(int(time.time() * 1000) % 0xFFFFFF, "x")  # change ids are this run's own: a reconnecting studio's
# remembered ids from before a restart never confirm a new change (M1.5b review S1)
ITEMS_MAX = 40  # what one report may list (the studio's own report; anything more is left out)
COLUMN_WORDS = {"areaPx": "size", "feretMaxPx": "length", "feretMinPx": "width", "perimeterPx": "perimeter",
                "elongation": "elongation", "circularity": "roundness", "solidity": "solidity", "intensity": "brightness",
                "net": "net speed", "path": "speed along the path", "straightness": "straightness"}


class Screen:
    """The studios' reports (one per connected studio; the visible one counts), the changes sent to them,
    and who set each presentation's properties."""

    def __init__(self, broadcast: Callable[[dict], None] | None = None, clock: Callable[[], float] | None = None) -> None:
        self.broadcast = broadcast or (lambda payload: None)
        # The wall clock, read when it is asked (`time.time` looked up at each call). A default of `time.time`
        # was bound once, when this module was first imported: imported while a test had `time.time` patched
        # (test_golden), every later Screen kept that frozen clock, its reports never newer than a change, and
        # every wait on the screen timed out (M3.2's tests, run after test_golden).
        self._clock = clock if clock is not None else (lambda: time.time())
        self._observation_clock = clock if clock is not None else (lambda: time.monotonic())
        self._reports: dict[Any, dict] = {}  # client -> {"items", "viewport", "seq", "at", "visible", "applied"}
        self.owners: dict[str, dict[str, dict]] = {}  # presentation id -> property -> {"by", "was", "at"}
        self._ids = itertools.count(1)
        self._waiters: list[tuple[str, Callable[[dict], bool], asyncio.Future, _StructuralChange | None]] = []
        self._looks: dict[str, Any] = {}  # legacy futures or client-bound pending acquisitions
        self._accepted: dict[Any, _AcceptedWorkspace] = {}
        self._capture_work: set[asyncio.Task] = set()  # owned decode jobs, never observer/model jobs
        self._partial: dict[Any, dict | None] = {}  # client -> what its last report left out (Screen.partial)

    @property
    def clock(self):
        return self._clock

    @clock.setter
    def clock(self, value):
        # Explicit clock injection controls both domains; defaults keep old wall
        # timestamps separate from request observation's server monotonic clock.
        self._clock = self._observation_clock = value

    def accept_client(self, client, ref: ClientRef, *, app_run_id, source_resolver,
                      capture_authority, limits=None, control_names=None, closed_source_resolver=None,
                      failed_source_resolver=None) -> None:
        """Server-only binding. No report/flag/transport attribute registers authority."""
        if not isinstance(ref, ClientRef) or client is None:
            _workspace_error("CLIENT_MISMATCH", "accepted connection needs an explicit ClientRef")
        if any(c is not client and p.ref == ref for c, p in self._accepted.items()):
            _workspace_error("CLIENT_MISMATCH", "connection identity is already bound to another socket")
        _workspace_id(app_run_id)
        if not callable(source_resolver) or not callable(capture_authority):
            _workspace_error("INVALID_REPORT", "source and acquisition authority need trusted callbacks")
        if closed_source_resolver is not None and not callable(closed_source_resolver):
            _workspace_error("INVALID_REPORT", "closed metadata authority needs a trusted callback")
        if failed_source_resolver is not None and not callable(failed_source_resolver):
            _workspace_error("INVALID_REPORT", "failed metadata authority needs a trusted callback")
        limits = limits or Limits()
        if not isinstance(limits, Limits):
            _workspace_error("INVALID_REPORT", "workspace limits need the canonical bounded value")
        if control_names is not None:
            if not isinstance(control_names, dict) or len(control_names) > 40:
                _workspace_error("INVALID_REPORT", "registered control metadata exceeds its bound")
            for cid, spec in control_names.items():
                _workspace_id(cid)
                if not isinstance(spec, dict) or set(spec) != {"name", "role"}:
                    _workspace_error("INVALID_REPORT", "registered controls need explicit name and role")
                _workspace_id(spec["name"])
                _workspace_id(spec["role"])
        self.gone(client)  # binding replacement cannot inherit an older page's reports/captures
        self._accepted[client] = _AcceptedWorkspace(ref, app_run_id, source_resolver, capture_authority,
                                                   limits, copy.deepcopy(control_names), closed_source_resolver,
                                                   failed_source_resolver)

    def _profile(self, client):
        profile = self._accepted.get(client)
        if profile is None:
            _workspace_error("CLIENT_MISMATCH", "this socket has no accepted workspace binding")
        return profile

    def observation(self, client) -> Observation | None:
        self._profile(client)
        return (self._reports.get(client) or {}).get("observation")

    @staticmethod
    def _closed_source_rows(wire, key, *, shared_source=False):
        """Only inactive native metadata, with no current consumer of its pixels.

        These bounded normalized facts establish eligibility, not authority:
        the trusted callback must still match every row to its actual closed Core.
        Historical command results remain dated facts under their original fences.
        """
        rows = [item for item in wire["items"] if item.get("sourceKey") == key]
        if not rows or any(
                item["open"] or item["visible"] or item["connected"]
                or "legacy" in item.get("view", {})
                or item.get("load", {}).get("state") != "empty"
                or item.get("liveSample") is not None or item.get("viewDrawRef") is not None
                or item.get("load", {}).get("drawToken") is not None
                for item in rows):
            return None
        # An independently checked error sibling may share this source. Its
        # pointer does not make the closed sibling an active pixel consumer.
        refs = set() if shared_source else {key}
        for item in rows:
            refs.update((item["id"], item["rootId"]))
            refs.update(control["id"] for control in item["controls"])
            if any(control.get("present") and control["enabled"] for control in item["controls"]):
                return None
        if any(target.get("sourceKey") == key or target.get("presentationId") in refs
               for target in wire["targets"]):
            return None
        if any(edge["from"] in refs or edge["to"] in refs for edge in wire["edges"]):
            return None
        interaction = wire["interaction"]
        if refs.intersection(interaction["selected"]):
            return None
        focus = interaction["focus"]
        if focus["presentationId"] in refs or focus["controlId"] in refs:
            return None
        for point in [interaction["pointer"], *interaction["contacts"]]:
            if (any(point.get(field) in refs for field in ("presentationId", "sourceKey", "capturedBy"))
                    or refs.intersection(point["hitPath"] + point["targetCandidates"])):
                return None
        return rows

    @staticmethod
    def _failed_source_rows(wire, key):
        """Error and closed metadata only; no represented consumer claims pixels.

        Each closed sibling still meets the inactive metadata contract. The App
        independently verifies every Core fence and each open source token.
        """
        rows = [item for item in wire["items"] if item.get("sourceKey") == key]
        errors = [item for item in rows if item["open"]]
        closed = [item for item in rows if not item["open"]]
        if not errors or any(not item["connected"] or "legacy" in item.get("view", {})
                or item.get("load", {}).get("state") != "error"
                or item.get("load", {}).get("drawToken") is not None
                or item.get("liveSample") is not None or item.get("heldSample") is not None
                or item.get("viewDrawRef") is not None for item in errors):
            return None
        if closed:
            refs = {ref for item in closed for ref in (item["id"], item["rootId"])}
            refs.update(control["id"] for item in closed for control in item["controls"])
            # Test inactive siblings against their own consumers/interaction;
            # error siblings may still expose Close and remain selected.
            inactive = {**wire, "items": closed,
                "targets": [t for t in wire["targets"] if t.get("presentationId") in refs
                            or t.get("sourceKey") == key and t.get("presentationId") is None],
                "edges": [e for e in wire["edges"] if e["from"] in refs or e["to"] in refs]}
            if Screen._closed_source_rows(inactive, key, shared_source=True) is None:
                return None
        return rows

    @staticmethod
    def _without_sources(wire, keys):
        """The normalized report without the windows showing `keys`, and without what points at them (targets, edges,
        the pointer and focus on them): an incomplete report of the rest, its omitted counts raised so its totals still
        add up. Nothing of the left-out windows is kept, so they can confirm nothing."""
        wire = copy.deepcopy(wire)
        dropped = {item["id"] for item in wire["items"] if item.get("sourceKey") in keys}
        controls = {control["id"] for item in wire["items"] if item["id"] in dropped for control in item["controls"]}
        roots = {item.get("rootId") for item in wire["items"] if item["id"] in dropped}
        targets = {target["id"] for target in wire["targets"]
                   if target.get("presentationId") in dropped or target.get("sourceKey") in keys
                   or target.get("controlId") in controls}
        gone = dropped | controls | targets | set(keys)
        kept = {"items": [item for item in wire["items"] if item["id"] not in dropped],
                "sources": [source for source in wire["sources"] if source["key"] not in keys],
                "targets": [target for target in wire["targets"] if target["id"] not in targets],
                "edges": [edge for edge in wire["edges"] if edge["from"] not in gone and edge["to"] not in gone]}
        omitted = dict(wire.get("omitted") or {})
        for key, rows in kept.items():
            # `sources` lost the unresolved keys before this call (they were never authorised): they are omitted too.
            left_out = len(keys) if key == "sources" else len(wire[key]) - len(rows)
            omitted[key] = int(omitted.get(key, 0)) + left_out
            wire[key] = rows
        wire["omitted"], wire["complete"] = omitted, False
        interaction = wire.get("interaction") or {}
        changed = False
        selected = [ref for ref in interaction.get("selected", []) if ref not in targets]
        changed |= selected != interaction.get("selected", [])
        interaction["selected"] = selected
        for point in [interaction.get("pointer"), *interaction.get("contacts", [])]:
            if not isinstance(point, dict):
                continue
            hits = [ref for ref in point.get("hitPath", []) if ref not in gone]
            candidates = [ref for ref in point.get("targetCandidates", []) if ref not in targets]
            if hits != point.get("hitPath", []) or candidates != point.get("targetCandidates", []):
                point["hitPath"], point["targetCandidates"], changed = hits, candidates, True
            for key in ("presentationId", "sourceKey", "capturedBy"):
                if point.get(key) is not None and point[key] in gone:
                    point[key], changed = None, True
                    if key == "presentationId":
                        point.pop("sourceGeneration", None)
        focus = interaction.get("focus") or {}
        if focus.get("presentationId") in dropped or focus.get("controlId") in controls:
            focus["presentationId"], focus["controlId"], changed = None, None, True
        if changed:
            interaction.setdefault("unknown", []).append("source-unresolved")
        if "foreground" in wire:
            wire["foreground"] = {**wire["foreground"], "complete": False,
                                  "roots": [root for root in wire["foreground"]["roots"] if root["rootId"] not in roots]}
        return wire

    def partial(self, client):
        """What the last report from `client` left out, and why (windows whose source has no grant here); None when
        it was whole."""
        return copy.deepcopy(self._partial.get(client))

    def _normalize_workspace(self, message, client):
        profile = self._profile(client)
        received = self._observation_clock()
        previous = self.observation(client)
        retransmitted = previous is not None and message.get("seq") == previous._data["seq"]
        if retransmitted:
            received = previous.received_at  # an identical transport retry cannot refresh observed facts
        # Bound/validate before invoking source authority; no raw flags survive as authority.
        first = normalize_observation(message, client=profile.ref, app_run_id=profile.app_run_id,
                                      received_at=received, previous=None if retransmitted else previous,
                                      limits=profile.limits)
        wire = first.wire()
        authoritative, unresolved = [], []
        for supplied in wire["sources"]:
            try:
                closed_rows = self._closed_source_rows(wire, supplied["key"]) if profile.closed_source_resolver else None
                failed_rows = (self._failed_source_rows(wire, supplied["key"])
                    if profile.failed_source_resolver and supplied["kind"] == "live" else None)
                if failed_rows is not None:
                    source = profile.failed_source_resolver(client, copy.deepcopy(supplied), copy.deepcopy(failed_rows))
                elif closed_rows is not None:
                    source = profile.closed_source_resolver(client, copy.deepcopy(supplied), copy.deepcopy(closed_rows))
                else:
                    source = profile.source_resolver(client, copy.deepcopy(supplied))
                if source is None:
                    _workspace_error("SOURCE_UNRESOLVED", "registered source identity is unavailable")
                if callable(getattr(source, "to_wire", None)):
                    source = source.to_wire()
                if (not isinstance(source, dict) or source.get("key") != supplied["key"] or
                        source.get("kind") != supplied["kind"] or source.get("identity") != supplied["identity"]):
                    _workspace_error("SOURCE_UNRESOLVED", "source authority returned another identity")
            except ObservationError as exc:
                if exc.code == "SOURCE_UNRESOLVED" and getattr(exc, "ended", False):
                    # A window whose grant has ended here (its pin released when it was closed, or never granted to
                    # this connection) is left out of this report, never the whole report: refusing it froze what
                    # Hooke knew of the screen, and every later action failed as "stale" while the studio kept
                    # reporting (live 2026-10-29 17:17-17:26: two closes, a highlight, a table, a researcher's
                    # result). That window is not represented, so it grants nothing; the rest of the screen is.
                    # Facts that differ from a source still prepared here (a changed digest, provenance or
                    # metadata) are a report that is wrong, and are still refused whole.
                    unresolved.append({"sourceKey": supplied["key"], "reason": str(exc)[:300]})
                    continue
                if exc.code == "SOURCE_UNRESOLVED":
                    # Display correlation only, after bounded original report/origin
                    # validation. The obsolete open/media report remains refused.
                    exc.screen_report = {"client": profile.ref.wire(), "appRunId": profile.app_run_id,
                                         "seq": wire["seq"], "sourceKey": supplied["key"]}
                raise
            authoritative.append(source)
        wire["sources"] = authoritative
        if unresolved:
            wire = self._without_sources(wire, {row["sourceKey"] for row in unresolved})
            for row in unresolved:
                row["items"] = [item["id"] for item in first.wire()["items"] if item.get("sourceKey") == row["sourceKey"]]
        observed = normalize_observation(wire, client=profile.ref, app_run_id=profile.app_run_id,
                                        received_at=received, previous=None if retransmitted else previous,
                                        limits=profile.limits)
        self._partial[client] = {"client": profile.ref.wire(), "appRunId": profile.app_run_id, "seq": wire["seq"],
                                 "unresolved": unresolved} if unresolved else None
        if retransmitted:
            current_facts = json.dumps(observed.wire(), allow_nan=False, sort_keys=True, separators=(",", ":"))
            prior_facts = json.dumps(previous.wire(), allow_nan=False, sort_keys=True, separators=(",", ":"))
            if current_facts != prior_facts:
                _workspace_error("REPORT_REPLAYED", "a repeated sequence contains different observed facts")
            return previous
        return observed

    def pin(self, client, *, request_id, inline=None, timing="client-send", max_report_age_ms=1000):
        profile = self._profile(client)
        _workspace_id(request_id)
        if inline is not None:
            self.report(inline, client=client)  # synchronous, before any tool/provider await
        observed = self.observation(client)
        if observed is None:
            _workspace_error("OBSERVATION_INCOMPLETE", "no request-time workspace observation")
        sid = self._snapshot_id(profile, request_id, observed, timing)
        return pin_request(observed, snapshot_id=sid, timing=timing,
                           requested_at=self._observation_clock(), max_report_age_ms=max_report_age_ms)

    @staticmethod
    def _snapshot_id(profile, request_id, observed, timing):
        identity = [*profile.ref.wire().values(), profile.app_run_id, request_id, observed._data["seq"], timing]
        return "workspace:" + hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()

    def _origin(self, snapshot):
        if not isinstance(snapshot, RequestSnapshot):
            _workspace_error("INVALID_REPORT", "workspace operation needs its immutable request pin")
        matches = [(c, p) for c, p in self._accepted.items() if p.ref == snapshot.observation.client]
        if len(matches) != 1:
            _workspace_error("CLIENT_MISMATCH", "request's accepted connection is gone")
        client, profile = matches[0]
        if profile.app_run_id != snapshot.observation.app_run_id:
            _workspace_error("RUN_MISMATCH", "request belongs to an earlier app run")
        return client, profile

    def request_report(self, snapshot, client, *, request_id, target=None):
        """Current structural facts on the original pinned request connection."""
        origin, profile = self._origin(snapshot)
        if origin is not client:
            _workspace_error("CLIENT_MISMATCH", "request pin belongs to another accepted connection")
        _workspace_id(request_id)
        expected = self._snapshot_id(profile, request_id, snapshot.observation, snapshot.timing)
        if snapshot.snapshot_id != expected:
            _workspace_error("CLIENT_MISMATCH", "request pin belongs to another request")
        observed = self.observation(client)
        if (not self._current_observation(observed)
                or observed.wire()["seq"] < snapshot.observation.wire()["seq"]):
            _workspace_error("OBSERVATION_INCOMPLETE", "original studio observation is unavailable or stale")
        report = self._report_view(client)
        if target is not None:
            original = next((item for item in snapshot.observation.wire()["items"] if item["id"] == target), None)
            item = next((item for item in report["items"] if item["id"] == target), None)
            if original is not None and not self._same_structural_root(original, item):
                _workspace_error("TARGET_GONE", "original structural target lifetime ended")
        return report

    async def request_item_ready(self, snapshot, client, *, request_id, target, guard):
        """Await structural representation on this request's original socket only.

        The caller retains its producer/operation authority. This observes a
        bounded current report; it neither rebases the Send pin nor issues a
        Change or supplies source/paint authority.
        """
        deadline = asyncio.get_running_loop().time() + CONFIRM_S
        while True:
            reason = guard()
            if reason:
                _workspace_error("REQUEST_STALE", reason)
            report = self.request_report(snapshot, client, request_id=request_id, target=target)
            item = next((item for item in report["items"] if item["id"] == target), None)
            if self._structural_item(item):
                return item
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                _workspace_error("OBSERVATION_INCOMPLETE", "original studio has not represented this structural target")
            await asyncio.sleep(min(.025, remaining))

    def _current_observation(self, observed) -> bool:
        """An accepted studio's last observation still says what it shows: one reported in the last REPORT_FRESH_S,
        or one from a studio in the background, which reports only when it changes or is shown again (as
        _current_client counts it), so its age is no doubt."""
        if observed is None:
            return False
        if self._observation_clock() - observed.received_at < REPORT_FRESH_S:
            return True
        return observed.wire()["viewport"].get("visible") is False

    def target(self, snapshot, explicit_target_id, *, properties=()):
        client, _profile = self._origin(snapshot)
        current = self.observation(client)
        if not self._current_observation(current):
            _workspace_error("OBSERVATION_INCOMPLETE", "current client observation is unavailable or stale")
        return check_target_current(snapshot, explicit_target_id, current, properties=properties)

    @staticmethod
    def _property_map(values):
        values = dict(values)
        if "geometry" in values:
            if "rect" in values and values["rect"] != values["geometry"]:
                _workspace_error("INVALID_REPORT", "competing geometry/rect projection values")
            values["rect"] = values.pop("geometry")
        return values

    def _completion(self, observed, profile):
        wire = observed.wire()
        ref = profile.ref
        client = {"workspaceId": ref.workspace_id, "clientId": ref.client_id,
                  "clientEpoch": ref.page_epoch, "connectionEpoch": ref.connection_epoch}
        sources = {s["key"]: s for s in wire["sources"]}
        for item in wire["items"]:
            source = sources.get(item.get("sourceKey"))
            item["sourceRef"] = copy.deepcopy(source)
            load = item.get("load") or {}
            item["phase"] = "closed" if item["open"] is False else load.get("state", "unknown")
            item["drawRef"] = load.get("drawToken")
            if source and source["kind"] == "live":
                sample = item.get("liveSample")
                item["drawRef"] = (copy.deepcopy(sample) if load.get("state") == "drawn" and sample
                    and sample.get("boot") is not None and sample.get("seq") is not None else None)
                held = item.get("heldSample")
                if load.get("state") == "drawn" and held and held.get("boot") is not None and held.get("seq") is not None:
                    # Verifiable held pixels for window controls, never a live
                    # registration sample (whose boot/seq are at the top level).
                    item["drawRef"] = {"held": copy.deepcopy(held)}
            item["mediaSize"] = copy.deepcopy(load.get("naturalSize"))
            if load.get("errorCode") is not None:
                item["errorCode"] = load["errorCode"]
                item["error"] = {"code": load["errorCode"]}
            if item["visible"] and not wire["viewport"]["visible"] and "visibility" not in item:
                item["visibility"] = "tab-hidden"  # laid out shown, in a studio in the background (a legacy surface)
            item["visible"] = item["visible"] and wire["viewport"]["visible"]
            item["propertyVersions"] = self._property_map(item["propertyVersions"])
            item["propertyOwners"] = self._property_map(item["propertyOwners"])
            projected = {}
            for control in item["controls"]:
                if profile.control_names is None:
                    roles = {**CONTROL_ROLES, "move": "drag-handle", "resize": "resize-handle"} if item["id"].startswith("w-") else CONTROL_ROLES
                    candidates = [(name, role) for name, role in roles.items()
                                  if control["id"] == f"{name}:{item['id']}"]
                else:
                    spec = profile.control_names.get(control["id"])
                    candidates = [(spec["name"], spec["role"])] if spec else []
                for name, role in candidates:
                    if control["role"] != role or control["targetId"] != item["id"] or control.get("editable"):
                        continue
                    # Raw widget role was checked against server registration
                    # above; the consumer's effect role is that trusted name.
                    # Keep missing explicit editability unknown, not proof.
                    state = {"id": control["id"], "role": name, "widgetRole": role,
                             "editable": control.get("editable"),
                             "target": control["targetId"], "present": control.get("present"),
                             "enabled": control["enabled"] and control.get("present") is True,
                             "reportedEnabled": control["enabled"]}
                    for key in ("stateRevision", "observedValue", "unknown"):
                        if key in control:
                            state[key] = copy.deepcopy(control[key])
                    projected[name] = state
                    if "observedValue" in control:
                        item[name] = control["observedValue"]
            if profile.control_names is None:
                # Native pan is two real sliders, not a reported pan button.
                # This fixed server recipe requires both axes on this instance.
                axes = [next((c for c in item["controls"] if c["id"] == f"pan-{axis}:{item['id']}"), None)
                        for axis in ("x", "y")]
                version = item["propertyVersions"].get("pan")
                if type(version) is int and all(c is not None and c["role"] == "slider"
                        and c["targetId"] == item["id"] and c.get("editable") is False
                        and c.get("present") is True and c["enabled"] is True
                        and c.get("stateRevision") == version
                        and type(c.get("observedValue")) in (int, float)
                        and math.isfinite(c["observedValue"]) and 0 <= c["observedValue"] <= 1
                        for c in axes):
                    projected["pan"] = {"id": axes[0]["id"], "ids": [c["id"] for c in axes],
                        "role": "pan", "widgetRole": "slider-pair", "editable": False,
                        "target": item["id"], "present": True, "enabled": True,
                        "reportedEnabled": True, "stateRevision": version,
                        "observedValue": [c["observedValue"] for c in axes]}
            item["controls"] = projected
            legacy = (item.get("view") or {}).get("legacy")
            if legacy is not None:
                # Only the normalizer's closed DOM scope reaches legacy readers.
                # These labels/locators/work facts never populate sourceRef or native results.
                item.update({key: copy.deepcopy(value) for key, value in legacy.items()
                             if key not in ("schema", "scope")})
        results = []
        occurrences = set()
        for row in wire["commandResults"]:
            if not isinstance(row, dict):
                _workspace_error("INVALID_REPORT", "causal renderer results must be records")
            row = copy.deepcopy(row)
            occurrence = (_workspace_id(row.get("commandId")), _workspace_id(row.get("presentationId")))
            if occurrence in occurrences:
                _workspace_error("DUPLICATE_ID", "one latest causal result per command and presentation is required")
            occurrences.add(occurrence)
            for key in ("requestId", "actionId", "appRunId"):
                _workspace_id(row.get(key))
            if row["appRunId"] != profile.app_run_id:
                _workspace_error("RUN_MISMATCH", "causal result belongs to another app run")
            for key in ("sourceGeneration", "revision", "committedRevision"):
                if key in row and (type(row[key]) is not int or not 0 <= row[key] <= 2**53 - 1):
                    _workspace_error("INVALID_REPORT", "causal generation and revision must be safe integers")
            if "lifetimeGeneration" in row:
                value = row["lifetimeGeneration"]
                if isinstance(value, str):
                    _workspace_id(value)
                elif type(value) is not int or not 0 <= value <= 2**53 - 1:
                    _workspace_error("INVALID_REPORT", "causal lifetime needs an opaque ID or safe legacy integer")
            if "reportSeq" in row and (type(row["reportSeq"]) is not int or
                    not 0 <= row["reportSeq"] <= wire["seq"]):
                _workspace_error("INVALID_REPORT", "causal result needs an actual nonfuture report sequence")
            # Only the normalized, server-resolved table can provide a SourceRef.
            row.pop("sourceRef", None)
            # These are observed current maps, never committed maps copied from a fence.
            for key, alias in (("propertyVersions", "observedPropertyVersions"),
                               ("propertyOwners", "observedPropertyOwners")):
                if key in row:
                    if not isinstance(row[key], dict):
                        _workspace_error("INVALID_REPORT", "observed causal property maps must be records")
                    for prop, value in row[key].items():
                        _workspace_id(prop)
                        if key == "propertyVersions":
                            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                                _workspace_error("INVALID_REPORT", "observed property version must be a safe integer")
                        elif value is not None:
                            _workspace_id(value)
                    row[alias] = self._property_map(row[key])
                else:
                    row.pop(alias, None)  # no raw alias can stand in for current observed maps
            for key in ("committedPropertyVersions", "committedPropertyOwners", "basePropertyVersions"):
                if key in row:
                    if not isinstance(row[key], dict):
                        _workspace_error("INVALID_REPORT", "causal property fence must be a record")
                    for prop, value in row[key].items():
                        _workspace_id(prop)
                        if key != "committedPropertyOwners":
                            if type(value) is not int or not 0 <= value <= 2**53 - 1:
                                _workspace_error("INVALID_REPORT", "causal property fence needs safe integer versions")
                        elif value is not None:
                            _workspace_id(value)
                    row[key] = self._property_map(row[key])
            if isinstance(row.get("client"), dict):
                raw = row["client"]
                if any(raw.get(k) != v for k, v in ref.wire().items()):
                    _workspace_error("CLIENT_MISMATCH", "causal result belongs to another accepted client")
                row["client"] = dict(client)
            if row.get("sourceKey") in sources:
                row["sourceRef"] = copy.deepcopy(sources[row["sourceKey"]])
            results.append(row)
        wire.update(client=client, **client, visible=wire["viewport"]["visible"],
                    receivedAtMono=observed.received_at, commandResults=results)
        return wire

    def completion_report(self, client):
        profile = self._profile(client)
        observed = self.observation(client)
        return self._completion(observed, profile) if observed is not None else None

    def _report_view(self, client):
        stored = self._reports.get(client)
        if stored is not None and "observation" in stored:
            return {**self.completion_report(client), "at": stored["at"], "visible": stored["visible"],
                    "applied": set(stored["applied"])}
        return stored

    @staticmethod
    def _structural_item(item):
        return item is not None and "legacy" in (item.get("view") or {})

    @staticmethod
    def _same_structural_root(before, after):
        return (Screen._structural_item(before) and Screen._structural_item(after)
                and all(before.get(key) == after.get(key) for key in ("id", "rootId", "lifetimeGeneration", "kind")))

    @staticmethod
    def _structural_property(item, prop):
        if prop == "front":
            return item.get("z")
        style = item.get("style") or {}
        return style.get(prop, item.get(prop))

    def _change_active(self, binding):
        if (self._accepted.get(binding.client) is not binding.profile
                or binding.profile.changes.get(binding.change.id) is not binding
                or binding.change._structural is not binding):
            return False
        try:
            original = {"wire": binding.change.wire(), "by": binding.change.by, "before": binding.change.before}
            return json.dumps(original, sort_keys=True, allow_nan=False) == binding.signature
        except (TypeError, ValueError):
            return False

    def _change_report(self, binding):
        if not self._change_active(binding):
            return None
        report = self._report_view(binding.client)
        if report is None or (self.clock() - report["at"] >= REPORT_FRESH_S and report.get("visible") is not False):
            return None
        item = next((item for item in report["items"] if item["id"] == binding.target), None)
        if (not self._structural_item(item) or item["rootId"] != binding.root
                or item["lifetimeGeneration"] != binding.lifetime or item["kind"] != binding.kind):
            return None
        return report

    @staticmethod
    def _finish_change(binding, ok):
        if binding.completed is None:
            binding.completed = bool(ok)
        binding.pending = False
        if binding.confirmation is not None and not binding.confirmation.done():
            binding.confirmation.set_result(binding.completed)

    def _prune_changes(self, profile, client):
        applied = (self._reports.get(client) or {}).get("applied", set())
        report = self._report_view(client)
        items = {item["id"]: item for item in (report or {}).get("items", [])}
        for key, binding in list(profile.changes.items()):
            item = items.get(binding.target)
            retired = (not self._structural_item(item) or item["rootId"] != binding.root
                       or item["lifetimeGeneration"] != binding.lifetime or item["kind"] != binding.kind)
            if retired:
                self._finish_change(binding, False)
                profile.changes.pop(key)
                for _key, _check, future, waiting in self._waiters:
                    if waiting is binding and not future.done():
                        future.set_result(False)
            elif not binding.pending and key not in applied:
                profile.changes.pop(key)

    def _structural_events(self, wire, client, profile, projected):
        """Validate the whole update before report, owners or waiters change."""
        if len(wire["applied"]) > ITEMS_MAX or len(wire["user"]) > ITEMS_MAX:
            _workspace_error("REPORT_TOO_LARGE", "structural event bounds exceeded")
        items = {item["id"]: item for item in projected["items"]}
        before = self._report_view(client)
        prior = {item["id"]: item for item in (before or {}).get("items", [])}
        for change_id in wire["applied"]:
            _workspace_id(change_id)
            binding = profile.changes.get(change_id)
            item = items.get(binding.target) if binding is not None else None
            if (binding is None or not self._change_active(binding) or not self._structural_item(item)
                    or item["rootId"] != binding.root or item["lifetimeGeneration"] != binding.lifetime
                    or item["kind"] != binding.kind or wire["seq"] <= binding.issued_seq):
                _workspace_error("INVALID_REPORT", "applied ID has no original same-root structural Change")
        changes, seen = [], set()
        properties = {"rect", "open", "front", "z", "shows", "source", "beside", "level", "pictures", "step",
                      "placement", "covers", "opacity", "colour", "color", "stroke", "fill", "fillOpacity",
                      "lineWidth", "emphasis"}
        for entry in wire["user"]:
            if not isinstance(entry, dict) or set(entry) != {"id", "prop"}:
                _workspace_error("INVALID_REPORT", "structural user change needs its exact target and property")
            pid, prop = _workspace_id(entry["id"]), _workspace_id(entry["prop"])
            item, old = items.get(pid), prior.get(pid)
            if prop not in properties:
                _workspace_error("INVALID_REPORT", "user change has no represented original structural property")
            # A change there is no accepted original to own against (its item was in a report not taken here), or
            # one that changed nothing, is left unowned, never a reason to refuse the whole report: the studio judges
            # ownership against the last report it sent, not the last one taken (live 2026-10-29: "User change has
            # no represented original structural property" in Activity, and the screen's report refused).
            if not self._same_structural_root(old, item):
                continue
            was, now = self._structural_property(old, prop), self._structural_property(item, prop)
            if was == now:
                continue
            if (pid, prop) not in seen:
                changes.append((pid, prop, copy.deepcopy(was)))
                seen.add((pid, prop))
        return changes

    # --- the report ----------------------------------------------------------------------------
    def report(self, message: dict, client: Any = None) -> None:
        """A report from a studio: what it shows now, the changes it has applied, whether its page is
        visible, and changes the user made there (a drag, a resize, a toggle), which become theirs
        (Amendment 4, item 4)."""
        if not isinstance(message, dict):
            _workspace_error("INVALID_REPORT", "screen report needs an envelope")
        if message.get("schema") is not None:
            if message.get("schema") != "screen-observation-v2":
                _workspace_error("INVALID_REPORT", "unknown workspace observation schema")
            observed = self._normalize_workspace(message, client)
            if observed is self.observation(client):
                return  # already admitted identical retry: no timestamps, owners or waiters change
            wire = observed.wire()
            if len(wire["commandResults"]) > 40:
                _workspace_error("REPORT_TOO_LARGE", "causal renderer result bound exceeded")
            profile = self._profile(client)
            projected = self._completion(observed, profile)
            ownership = self._structural_events(wire, client, profile, projected)
            self._reports[client] = {"observation": observed, "at": self.clock(),
                "visible": wire["viewport"]["visible"], "applied": set(wire["applied"][:ITEMS_MAX])}
            if client is self._current_client():
                for pid, prop, was in ownership:
                    self._own(pid, prop, "user", was)
            self._prune_changes(profile, client)
            self._notify()
            return
        if client in self._accepted:
            _workspace_error("INVALID_REPORT", "accepted workspace client cannot downgrade to a raw legacy report")
        items = []
        for i in (message.get("items") or [])[:ITEMS_MAX]:
            if isinstance(i, dict) and i.get("id"):
                item = copy.deepcopy(i)
                if isinstance(item.get("title"), str):
                    item["title"] = item["title"][:120]
                # Its contents' space (invariant 14; spaces.py): the studio states it, else its kind's. The rect
                # is always the workspace's.
                if not spaces.valid(item.get("space")):
                    item["space"] = spaces.item_space(str(item.get("kind") or ""), item.get("source"))
                items.append(item)
        before = self._reports.get(client)
        previous = {i["id"]: i for i in (before or {}).get("items", [])}
        applied = set((before or {}).get("applied", set())) if before else set()
        applied.update(str(c)[:40] for c in (message.get("applied") or [])[:ITEMS_MAX])
        self._reports[client] = {"items": items, "viewport": message.get("viewport"), "seq": message.get("seq"),
                                 "at": self.clock(), "visible": message.get("visible", True) is not False, "applied": applied}
        if client is self._current_client():
            for change in (message.get("user") or [])[:ITEMS_MAX]:
                if isinstance(change, dict) and change.get("id") and change.get("prop"):
                    before_item = previous.get(change["id"]) or {}
                    # A style property (colour, fill) is read from the item's style, as confirm reads it (re-check N6).
                    was = (before_item.get("style") or {}).get(change["prop"], before_item.get(change["prop"]))
                    self._own(str(change["id"])[:60], str(change["prop"])[:40], "user", was)
        self._notify()

    def _notify(self):
        latest = self.latest
        for key, check, future, binding in list(self._waiters):
            report = self._change_report(binding) if binding is not None else latest
            if future.done() or report is None:
                continue
            if key == "" or key in report["applied"]:
                try:
                    if check(report):
                        future.set_result(True)
                except Exception:
                    pass

    def _current_client(self):
        """The studio whose report counts: the visible one that reported last (else any that reported last).
        A hidden tab draws no frames, so it reports only when asked or when shown again; while it is connected
        it still counts, as it is what the user sees on coming back (M1.5b re-check, new item 1)."""
        fresh = [(r["at"], c) for c, r in self._reports.items()
                 if self.clock() - r["at"] < REPORT_FRESH_S or not r["visible"]]
        visible = [(at, c) for at, c in fresh if self._reports[c]["visible"]]
        pool = visible or fresh
        return max(pool, key=lambda x: x[0])[1] if pool else None

    def current_accepted(self):
        """The studio whose report counts, when it is an accepted workspace (data_reader.py: where a tool run no studio
        sent opens things), else None."""
        client = self._current_client()
        return client if client in self._accepted else None

    def gone(self, client: Any) -> None:
        """A studio disconnected: its report no longer counts."""
        self._reports.pop(client, None)
        self._partial.pop(client, None)
        profile = self._accepted.pop(client, None)
        if profile is not None:
            for binding in profile.changes.values():
                self._finish_change(binding, False)
            profile.changes.clear()
            for _key, _check, future, binding in self._waiters:
                if binding is not None and binding.profile is profile and not future.done():
                    future.set_result(False)
        for entry in list(self._looks.values()):
            if isinstance(entry, dict) and entry["client"] is client:
                entry["future"].cancel()
                if entry["reservation"] is not None:
                    entry["reservation"].release()
                    entry["reservation"] = None

    @property
    def latest(self) -> dict | None:
        current = self._current_client()
        return self._report_view(current) if current is not None or None in self._reports else None

    @property
    def reported_at(self) -> float:
        latest = self.latest
        return latest["at"] if latest is not None else 0.0

    @property
    def applied(self) -> set:
        latest = self.latest
        return latest["applied"] if latest is not None else set()

    def showing(self) -> bool:
        return self.latest is not None

    def item(self, pid: str) -> dict | None:
        return next((i for i in (self.latest or {}).get("items", []) if i.get("id") == pid), None)

    def items(self) -> list[dict]:
        return list((self.latest or {}).get("items", []))

    def lines(self, most: int = 8) -> list[str]:
        """What is on screen now, one line per thing shown that is not as it always is (the Board;
        Amendment 2, point 1): the live view and the app's outlines are listed only when dimmed, hidden
        or restyled, so a plain screen costs nothing ("context earns its place")."""
        if not self.showing():
            return []
        out = []
        if (self.latest or {}).get("visible") is False:
            # Its items report nothing visible (nothing is painted there); what is laid out shown is listed as shown.
            out.append("the studio is in the background (another app or tab in front): nothing in it is painted now")

        def shown(i):
            return i.get("visible", True) or laid_out(i)

        for i in sorted(self.items(), key=lambda i: -(i.get("z") or 0)):
            kind = i.get("kind")
            style = i.get("style") or {}
            opacity = i.get("opacity")
            dimmed = isinstance(opacity, (int, float)) and opacity < 0.99
            if kind in ("live", "outlines") and shown(i) and not dimmed:
                continue  # as it always is
            if kind in ("magnifier", "viewer"):
                continue  # said by the scene table's screen line (one source for what they show)
            if kind == "population" and not (dimmed or style.get("fill") or style.get("emphasis")):
                continue  # the Evidence line says where it is shown and how it is coloured (going live, G3)
            if kind == "tracks" and not (dimmed or style.get("emphasis") or style.get("colour") != "column"
                                         or style.get("column") not in (None, "net")):
                continue  # drawn as it always is (by net speed): the Evidence line says where it is shown
            if kind == "outlines" and not shown(i):
                out.append("outlines: the app's outlines are hidden")
                continue
            if not (i.get("open", True) and shown(i)):
                continue
            words = [i.get("title") or NAMES.get(kind, kind or "")]
            if i.get("shows"):
                words.append(f"showing {i['shows']}")
            if dimmed:
                words.append(f"at {round(opacity * 100)}% opacity")
            if isinstance(style.get("emphasis"), dict):
                words.append(f"{len(style['emphasis'].get('members') or [])} kept, the rest {'hidden' if style['emphasis'].get('rest') == 'hide' else 'faded'}")
            elif style.get("emphasis"):
                words.append(f"emphasis {str(style['emphasis'])[:40]}")
            if i.get("placement"):
                words.append(str(i["placement"]))
            out.append(f"{i['id']}: " + ", ".join(w for w in words if w))
            if len(out) >= most:
                break
        return out

    # --- changes ------------------------------------------------------------------------------
    def send(self, target: str, kind: str, verb: str, values: dict, by: str, *,
             client=None, snapshot=None, request_id=None) -> Change:
        """A change of a presentation, sent to the studio. Who set it is recorded once a report shows
        it (`confirm`), and only for the properties that changed."""
        scoped = client is not None or snapshot is not None or request_id is not None
        if scoped:
            report = self.request_report(snapshot, client, request_id=request_id, target=target)
            before = next((item for item in report["items"] if item["id"] == target), None)
            if not self._structural_item(before):
                _workspace_error("OBSERVATION_INCOMPLETE", "original studio has not represented this structural target")
        else:
            before = self.item(target)
            client = self._current_client()
            if client in self._accepted and not self._structural_item(before):
                _workspace_error("OBSERVATION_INCOMPLETE", "accepted studio has not represented this structural target")
        change = Change(f"c{BOOT}-{next(self._ids)}", target, kind, verb, dict(values), by)
        from .ticket import current
        request = current()
        change.actor = ("user" if by == "user" or request is not None and request.kind == "command"
                        else "assistant" if request is not None and request.kind in {"turn", "hands"}
                        or by == "brain" or by.startswith("showing:") else None)
        change.before = copy.deepcopy(before or {})
        profile = self._accepted.get(client)
        if profile is not None and self._structural_item(change.before):
            self._prune_changes(profile, client)
            if len(profile.changes) >= 2 * ITEMS_MAX or sum(binding.pending for binding in profile.changes.values()) >= ITEMS_MAX:
                _workspace_error("REPORT_TOO_LARGE", "original structural Changes await confirmation")
            original = {"wire": change.wire(), "by": change.by, "before": change.before}
            signature = json.dumps(original, sort_keys=True, allow_nan=False)
            if len(signature.encode()) > profile.limits.normalized_bytes:
                _workspace_error("REPORT_TOO_LARGE", "original structural Change exceeds its bound")
            binding = _StructuralChange(change, client, profile, target, change.before["kind"],
                change.before["rootId"], change.before["lifetimeGeneration"], self.observation(client).wire()["seq"], signature,
                copy.deepcopy(self.owners.get(target, {})))
            change._structural = binding
            profile.changes[change.id] = binding  # before broadcast: its actual synchronous ACK may arrive there
        try:
            if change._structural is not None:
                client.offer(json.dumps(change.wire()))  # only this original socket may report its causal ACK
            else:
                self.broadcast(change.wire())  # preserved raw, unaccepted legacy transport
        except BaseException:
            if change._structural is not None:
                change._structural.profile.changes.pop(change.id, None)
            raise
        return change

    async def confirm(self, change: Change, check: Callable[[dict], bool], timeout: float | None = None) -> bool:
        """Whether the studio that counts shows `change` applied and `check(report)` holds, within `timeout`;
        then who set what is recorded."""
        ok = False
        binding = change._structural
        if binding is not None and (binding.change is not change or not self._change_active(binding)):
            return False
        if binding is not None and binding.completed is not None:
            report = self._change_report(binding)
            return bool(binding.completed and report is not None and change.id in report["applied"] and check(report))
        if binding is not None and binding.confirmation is not None:
            # Only the first caller owns confirmation. A joining caller may stop
            # waiting, but cannot cancel its owner's waiter or change its verdict.
            try:
                verdict = await asyncio.wait_for(asyncio.shield(binding.confirmation),
                                                CONFIRM_S if timeout is None else timeout)
            except asyncio.TimeoutError:
                return False
            report = self._change_report(binding)
            return bool(verdict and report is not None and change.id in report["applied"] and check(report))
        if binding is None and (self._current_client() in self._accepted
                or any(change.id in profile.changes for profile in self._accepted.values())):
            return False  # A copied Change/ID cannot use the raw legacy confirmation path.
        if binding is not None:
            binding.confirmation = asyncio.get_running_loop().create_future()
        try:
            report = self._change_report(binding) if binding is not None else self.latest
            if binding is not None and report is None:
                return False
            if report is not None and change.id in report["applied"] and check(report):
                ok = True
            else:
                future = asyncio.get_running_loop().create_future()
                entry = (change.id, check, future, binding)
                self._waiters.append(entry)
                try:
                    ok = await asyncio.wait_for(future, CONFIRM_S if timeout is None else timeout)
                except asyncio.TimeoutError:
                    ok = False
                finally:
                    self._waiters.remove(entry)
            # An await cannot transfer this Change to a replaced root/socket or another caller.
            if binding is not None:
                report = self._change_report(binding)
                if report is None or change.id not in report["applied"] or not check(report):
                    ok = False
            if ok and (binding is None or self._current_client() is binding.client):
                before = getattr(change, "before", {}) or {}
                for prop, value in change.set.items():
                    if binding is not None and self.owners.get(change.target, {}).get(prop) != binding.owners.get(prop):
                        continue  # A concrete later user/request change wins the ownership race.
                    was = (before.get("style") or {}).get(prop, before.get(prop))
                    if was != value:
                        self._own(change.target, prop, change.by, was, actor=change.actor)
            return ok
        finally:
            if binding is not None:
                self._finish_change(binding, ok)
                self._prune_changes(binding.profile, binding.client)

    async def refresh(self, client, timeout: float = REFRESH_S, *, newer: bool = False) -> bool:
        """Ask the studio on `client` for a report now and wait (at most `timeout`) for one newer than its last taken:
        a request that arrives without one of its own (a voice hand-off, a tool the voice calls) is pinned to what
        the studio shows now, as a typed Send is (its inline report), not to the last periodic report. Whether a
        newer one came; without one the last report stands, as before. `newer`: the last report disagrees with the
        app about something, so however recent it is, only a later one will do."""
        try:
            before = self.observation(client)
        except ObservationError:
            return False
        if not newer and before is not None and self._observation_clock() - before.received_at < REFRESH_FRESH_S:
            return True  # taken a moment ago: that is what it shows now
        seq = before._data["seq"] if before is not None else -1
        ask = json.dumps({"type": "screen-ask"})
        offer = getattr(client, "offer", None)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            if callable(offer):
                with contextlib.suppress(Exception):
                    offer(ask)
            else:
                self.broadcast({"type": "screen-ask"})
            asked = loop.time()
            while loop.time() - asked < REFRESH_ASK_EVERY_S:
                try:
                    now = self.observation(client)
                except ObservationError:
                    return False
                if now is not None and now._data["seq"] > seq:
                    return True
                if loop.time() >= deadline:
                    return False
                await asyncio.sleep(0.01)

    async def current_report(self, client=None, *, now: bool = False, timeout: float | None = None) -> bool:
        """One way in (the live partner spec, 4.8 horizontal 1): before any actor's request acts, the report it reads
        is current, asked for by Hooke itself, never failed as stale by each tool in turn and never the user's to
        repeat. Every request comes through here (`fresh_report`): a typed Send's steps (`ActionRunner`), a voice
        hand-off and every voice tool (`now`), a researcher showing its work (`Announcer._open_beside`) and a
        connected agent's run through the door (`commands.run_tool`).
        - `now`: what the studio shows at this moment, for a request that carries no report of its own (the voice):
          asked for unless one came in the last REFRESH_FRESH_S.
        - otherwise only a report older than REPORT_FRESH_S (a studio that stopped reporting for a while, or whose
          reports were not taken) is asked for again.
        `client`: the studio the request came from; None for one no studio sent (the door's runs): the accepted studio
        whose report counts, else the accepted one that reported last. Waits at most `timeout` (REFRESH_S for one
        current already, STALE_REFRESH_S for a stale one). Whether a current report is there now; without one the last
        stands and the request goes on, its changes uncertain until a report shows them (present's STUDIO_UNREPORTED)."""
        if client is None:
            client = self._asked_studio()
            if client is None:
                return await self._legacy_current(now=now, timeout=timeout)
        try:
            observed = self.observation(client)
        except ObservationError:
            return False
        current = self._current_observation(observed)
        if current and not now:
            return True
        wait = timeout if timeout is not None else (REFRESH_S if current else STALE_REFRESH_S)
        return await self.refresh(client, timeout=wait)

    def _asked_studio(self):
        """The accepted studio a request no studio sent is answered by: the one whose report counts, else the accepted
        one that reported last (its report gone stale)."""
        current = self.current_accepted()
        if current is not None:
            return current
        reported = [(r["at"], c) for c, r in self._reports.items() if c in self._accepted]
        return max(reported, key=lambda x: x[0])[1] if reported else None

    async def _legacy_current(self, *, now: bool, timeout: float | None) -> bool:
        """A studio that reports without an accepted workspace (an older studio, a test's fixture): asked by broadcast
        when its last report no longer counts (or `now`); nothing is waited for when no studio has reported at all."""
        if not self._reports:
            return False
        if self.latest is not None and not now:
            return True
        before = max(r["at"] for r in self._reports.values())
        return await self.until(lambda report: True, before,
                                timeout=timeout if timeout is not None else (REFRESH_S if now else STALE_REFRESH_S))

    async def until(self, check: Callable[[dict], bool], after: float, timeout: float | None = None) -> bool:
        """Whether a report made after `after` (a `reported_at` read before the change) shows `check`
        within `timeout`: for changes made through the app's own state (the magnifier, the result viewer,
        the slide map), which the studio reports as it draws them. The studio is asked for a report at
        once, so a change it had no reason to report (already so) is never waited out (re-check N2)."""
        if self.latest is not None and self.reported_at > after and check(self.latest):
            return True
        future = asyncio.get_running_loop().create_future()
        entry = ("", lambda report: self.reported_at > after and check(report), future, None)
        self._waiters.append(entry)
        self.broadcast({"type": "screen-ask"})
        try:
            return await asyncio.wait_for(future, CONFIRM_S if timeout is None else timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._waiters.remove(entry)

    def _own(self, pid: str, prop: str, by: str, was: Any, *, actor: str | None = None) -> None:
        self.owners.setdefault(pid, {})[prop] = {"by": by, "was": was, "at": round(self.clock(), 3),
                                                "actor": "user" if by == "user" else actor}

    def last_change(self, pid: str) -> dict | None:
        """Recorded origin, never an inference from request IDs or historical ownership tokens."""
        known = [owner for owner in self.owners.get(pid, {}).values()
                 if type(owner.get("at")) in (int, float) and math.isfinite(owner["at"])]
        if not known:
            return None
        latest = max(known, key=lambda owner: owner["at"])
        if latest.get("actor") not in {"user", "assistant"}:
            return None
        return {"by": latest["actor"], "at": latest["at"]}

    def owner(self, pid: str, prop: str) -> str | None:
        return (self.owners.get(pid, {}).get(prop) or {}).get("by")

    # --- a look at the workspace --------------------------------------------------------------
    async def look(self, timeout: float = 5.0, *, snapshot=None, client=None, request_id=None,
                   targets=(), memory=None):
        """Acquire only; v2 is scoped/leased. Legacy bytes are the approximate compositor."""
        if snapshot is not None or client is not None or request_id is not None:
            return await self._workspace_look(snapshot, client, request_id, targets, memory, timeout)
        if self._current_client() in self._accepted:
            _workspace_error("CAPTURE_MISMATCH", "accepted workspace capture needs its originating request pin")
        lid = f"l{next(self._ids)}"
        future = asyncio.get_running_loop().create_future()
        self._looks[lid] = future
        self.broadcast({"type": "screen-look", "id": lid})
        try:
            return await asyncio.wait_for(future, timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            self._looks.pop(lid, None)

    def looked(self, lid: str, jpeg: bytes) -> None:
        """Legacy approximate-compositor reply; it cannot finish a v2 acquisition."""
        future = self._looks.get(lid)
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_result(jpeg)

    async def _workspace_look(self, snapshot, client, request_id, targets, memory, timeout):
        from .capture import Memory, Refused

        origin, profile = self._origin(snapshot)
        if client is not origin:
            _workspace_error("CLIENT_MISMATCH", "capture is not for the request's accepted socket")
        _workspace_id(request_id)
        if snapshot.snapshot_id != self._snapshot_id(profile, request_id, snapshot.observation, snapshot.timing):
            _workspace_error("CAPTURE_MISMATCH", "capture request differs from the originating Screen pin")
        if (not isinstance(targets, (tuple, list)) or len(targets) > 8 or
                any(not isinstance(t, str) for t in targets) or len(set(targets)) != len(targets)):
            _workspace_error("INVALID_REPORT", "capture targets must be bounded explicit IDs")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            _workspace_error("INVALID_REPORT", "capture timeout must be finite and within 30 seconds")
        if not isinstance(memory, Memory):
            _workspace_error("INVALID_REPORT", "workspace capture needs the existing shared memory allowance")
        for target in targets:
            _workspace_id(target)
            self.target(snapshot, target)
        key = (request_id, snapshot.snapshot_id)
        if key in profile.captures:
            receipt = profile.captures[key]
            if receipt["targets"] != tuple(targets) or receipt["memory"] is not memory:
                _workspace_error("CAPTURE_MISMATCH", "reused acquisition changed its targets or memory allowance")
            held = receipt["image"]()
            if held is None:
                _workspace_error("CAPTURE_ALREADY_USED", "this request's acquired workspace input is no longer held")
            return held
        entry = next((e for e in self._looks.values() if isinstance(e, dict) and e["client"] is client), None)
        if entry is not None and entry["key"] != key:
            _workspace_error("CAPTURE_PENDING", "this client already has another workspace acquisition pending")
        if entry is not None and (entry["targets"] != tuple(targets) or entry["memory"] is not memory):
            _workspace_error("CAPTURE_MISMATCH", "duplicate acquisition changed its targets or memory allowance")
        if entry is None:
            limits = profile.limits
            # Account for encoded bytes, their base64 transport and bounded metadata
            # before asking the browser; grow under the same lease before pixel decode.
            transport = ((limits.image_bytes + 2) // 3) * 4
            try:
                reserved = memory.reserve("analysis", 2 * limits.image_bytes + 2 * transport + limits.wire_bytes,
                                          "workspace capture input and transport copies")
            except Refused as error:
                raise Refused("workspace capture memory: " + str(error), next=error.next) from None
            lid = f"l{next(self._ids)}"
            expected = {"lookId": lid, "requestId": request_id, "appRunId": profile.app_run_id,
                "originSnapshotId": snapshot.snapshot_id, "layoutRevision": snapshot.observation._data["layoutRevision"]}
            entry = {"client": client, "profile": profile, "snapshot": snapshot, "targets": tuple(targets),
                     "key": key, "expected": expected, "future": asyncio.get_running_loop().create_future(),
                     "delivered": asyncio.get_running_loop().create_future(),
                     "reservation": reserved, "memory": memory, "users": 0, "decoding": False, "id": lid,
                     "acquisitionRequestedAtMono": self._observation_clock()}
            self._looks[lid] = entry
            try:
                client.offer(json.dumps({"type": "screen-look", "schema": "workspace-capture-v1", "id": lid, **expected,
                    "client": profile.ref.wire(), "targetIds": list(targets), "maxBytes": limits.image_bytes,
                    "maxPixels": limits.image_pixels, "maxInputEdge": limits.image_edge,
                    "deadlineMono": self._observation_clock() + timeout, "timeoutMs": timeout * 1000}))
            except BaseException:
                self._looks.pop(lid, None)
                reserved.release()
                raise
        entry["users"] += 1
        try:
            return await asyncio.wait_for(asyncio.shield(entry["future"]), timeout)
        except asyncio.TimeoutError:
            return None
        finally:
            entry["users"] -= 1
            if entry["users"] == 0:
                self._looks.pop(entry["id"], None)
                if entry["reservation"] is not None:
                    entry["reservation"].release()
                    entry["reservation"] = None
                future = entry["future"]
                if not future.done():
                    future.cancel()
                elif not future.cancelled():
                    future.exception()  # observe a refusal even when the last caller was canceled
                if not entry["delivered"].done():
                    entry["delivered"].set_result(None)

    def _capture_current(self, entry):
        if self._accepted.get(entry["client"]) is not entry["profile"]:
            _workspace_error("CLIENT_MISMATCH", "capture's accepted connection changed")
        observed = self.observation(entry["client"])
        if observed is None:
            _workspace_error("OBSERVATION_INCOMPLETE", "capture client has no current observation")
        if observed._data["layoutRevision"] != entry["expected"]["layoutRevision"]:
            _workspace_error("LAYOUT_CHANGED", "current client layout no longer matches the pending capture")
        for target in entry["targets"]:
            self.target(entry["snapshot"], target)
        return observed

    @staticmethod
    def _capture_image(raw, metadata, claimed_digest, reserved, limits):
        """Worker-only header/decode/hash work; the existing PictureInput owns the lease."""
        from PIL import Image
        from .inspection import PictureInput

        try:
            if not raw.startswith(b"\xff\xd8") or len(raw) > limits.image_bytes:
                _workspace_error("INVALID_REPORT", "workspace bytes must be a bounded JPEG")
            actual_digest = hashlib.sha256(raw).hexdigest()
            if claimed_digest != actual_digest:
                _workspace_error("CAPTURE_MISMATCH", "workspace JPEG digest differs from supplied metadata")
            with Image.open(io.BytesIO(raw)) as original:
                if original.format != "JPEG":
                    _workspace_error("INVALID_REPORT", "workspace bytes are not a JPEG")
                width, height = original.size  # header probe, before decompression
                if width * height > limits.image_pixels or max(width, height) > limits.image_edge:
                    _workspace_error("IMAGE_TOO_LARGE", "actual JPEG header exceeds workspace pixel/edge bounds")
                if [width, height] != metadata["inputSize"]:
                    _workspace_error("CAPTURE_MISMATCH", "actual JPEG dimensions differ from capture metadata")
                reserved.grow(width * height * 12 + 2 * limits.image_bytes, "workspace JPEG decode and output copy peak")
                with original.convert("RGB") as decoded:
                    decoded.load()
                    output = io.BytesIO()
                    decoded.save(output, format="JPEG", quality=85)
                    jpeg = output.getvalue()
                    if len(jpeg) > limits.image_bytes:
                        _workspace_error("IMAGE_TOO_LARGE", "decoded workspace output exceeds encoded bounds")
            facts = {**metadata, "channel": "workspace", "sha256": actual_digest,
                     "inputSha256": hashlib.sha256(jpeg).hexdigest()}
            # Keep the conservative transport/decode allowance until the last
            # PictureInput consumer releases it; no naked encoded/decoded result.
            return PictureInput(jpeg, facts, reserved)
        except BaseException:
            reserved.release()
            raise

    def _capture_finished(self, task):
        self._capture_work.discard(task)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                error.__traceback__ = error.__context__ = error.__cause__ = None

    async def looked_workspace(self, client, message):
        """V2 receive seam; caller passes the raw envelope, never unleased decoded bytes."""
        if not isinstance(message, dict):
            _workspace_error("INVALID_REPORT", "capture reply needs an envelope")
        lid = _workspace_id(message.get("lookId", message.get("id")))
        entry = self._looks.get(lid)
        if not isinstance(entry, dict):
            _workspace_error("CAPTURE_MISMATCH", "no matching pending workspace capture")
        if client is not entry["client"]:
            _workspace_error("CLIENT_MISMATCH", "another socket cannot answer this workspace capture")
        if self._accepted.get(client) is not entry["profile"]:
            _workspace_error("CLIENT_MISMATCH", "capture's accepted connection changed")
        if message.get("id", lid) != lid:
            _workspace_error("CAPTURE_MISMATCH", "capture look identifiers disagree")
        if entry["future"].done() or entry["decoding"]:
            _workspace_error("CAPTURE_MISMATCH", "capture already answered or decoding")
        profile = entry["profile"]
        # A correlated refusal grants no image. Validate the original owner
        # before settling it, but do not require the success layout to survive.
        if message.get("status") == "unavailable":
            code = normalize_capture_unavailable(message, client=profile.ref,
                                                expected=entry["expected"], limits=profile.limits)
            entry["future"].set_exception(ObservationError("CAPTURE_UNAVAILABLE", "workspace capture unavailable: " + code))
            await asyncio.shield(entry["delivered"])
            return
        observed = self._capture_current(entry)
        # Validate metadata/causal fences under the pure seam before any byte decode.
        metadata = normalize_capture_reply({**message, "jpeg": b"\xff\xd8"}, client=profile.ref,
                                          expected=entry["expected"], limits=profile.limits).wire()
        basis = metadata.get("acquisitionBasis")
        if basis is not None:
            if (basis["structuralReportSeq"] != observed._data["seq"]
                    or basis["viewport"] != observed.wire()["viewport"]):
                _workspace_error("CAPTURE_MISMATCH", "capture basis differs from the paired accepted report")
        snapshot = entry["snapshot"]
        metadata.update(originTiming=snapshot.timing, originReportAgeMs=snapshot.report_age_ms,
                        originAvailability=snapshot.availability, originRequestedAtMono=snapshot.requested_at,
                        acquisitionRequestedAtMono=entry["acquisitionRequestedAtMono"],
                        acquisitionReceivedAtMono=self._observation_clock())
        if profile.capture_authority(client, copy.deepcopy(entry["expected"]), copy.deepcopy(metadata)) is not True:
            _workspace_error("CAPTURE_WRONG_SURFACE", "trusted acquisition adapter did not authorize this surface")
        encoded = message.get("jpeg")
        if not isinstance(encoded, str) or not encoded or not encoded.isascii():
            _workspace_error("INVALID_REPORT", "capture needs bounded base64 JPEG bytes")
        if len(encoded) > ((profile.limits.image_bytes + 2) // 3) * 4:
            _workspace_error("IMAGE_TOO_LARGE", "workspace encoded transport bound exceeded")
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, UnicodeError):
            _workspace_error("INVALID_REPORT", "workspace JPEG transport could not be read")
        if len(raw) > profile.limits.image_bytes:
            _workspace_error("IMAGE_TOO_LARGE", "workspace JPEG byte bound exceeded")
        entry["decoding"] = True
        reserved, entry["reservation"] = entry["reservation"], None  # worker owns it through completion/cancel
        task = asyncio.create_task(asyncio.to_thread(self._capture_image, raw, metadata,
                                                    message.get("sha256"), reserved, profile.limits))
        self._capture_work.add(task)
        task.add_done_callback(self._capture_finished)
        try:
            image = await asyncio.shield(task)
            self._capture_current(entry)
            if entry["future"].done():
                del image
                _workspace_error("CAPTURE_MISMATCH", "pending capture ended before its input was admitted")
            profile.captures[entry["key"]] = {"image": weakref.ref(image), "targets": entry["targets"],
                                               "memory": entry["memory"]}
            while len(profile.captures) > 32:
                profile.captures.popitem(last=False)
            entry["future"].set_result(image)
            # Drain acquisition continuations before acknowledging receipt. Their
            # Tasks are then complete for callers that await/gather them, instead
            # of image-bearing completion wakeups retaining the last lease.
            del image, task
            await asyncio.shield(entry["delivered"])
        except asyncio.CancelledError:
            # The owned worker continues under its reservation and disposes its
            # result when unreachable. It never touches Screen/Future state.
            raise
        except Exception as error:
            if "image" in locals():
                del image
            code = error.code if isinstance(error, ObservationError) else "INVALID_REPORT"
            detail = "workspace image could not be admitted: " + type(error).__name__
            if not entry["future"].done():
                entry["future"].set_exception(ObservationError(code, detail))
            error.__traceback__ = error.__context__ = error.__cause__ = None
            if "task" in locals():
                del task  # do not retain a rejected worker result in a caller's error traceback
            raise ObservationError(code, detail) from None


# "Close that" names no window: it is the window in front (Slice 5). Words that name no window, said as a target.
UNNAMED_WINDOW = {"", "that", "it", "this", "that one", "this one", "that window", "this window", "the window",
                  "the front window", "the frontmost window", "the one in front"}


def frontmost(items: list[dict] | None) -> dict | None:
    """The window in front (the highest stacking) among those open and shown that can be closed (TAKES), from the
    studio's report: what "close that" closes, whichever closing tool the model picked (present close or hide,
    close_result, close_magnifier), so they agree."""
    windows = [i for i in items or [] if closable(i)
               and i.get("open", True) and (i.get("visible", True) or laid_out(i))]
    return max(windows, key=lambda i: i.get("z") or 0, default=None)


def closable(item: dict) -> bool:
    """A window "close that" can close: a kind that takes close, or any native window (a `w-` instance: a
    photo, comparison, video, lesson or written result), which TAKES never listed, so a photo in front was passed
    over for the window behind it."""
    return "close" in TAKES.get(str(item.get("kind") or ""), ()) or str(item.get("id") or "").startswith("w-")


def laid_out(item: dict) -> bool:
    """Shown in the studio's layout while the studio is in the background (nothing is painted there, so nothing
    reports visible): still the window in front of the others, there to close."""
    visibility = item.get("visibility")
    state = visibility.get("state") if isinstance(visibility, dict) else visibility
    return state == "tab-hidden"


async def close_frontmost(services, ctx, instead_of: str) -> str:
    """A closing tool whose own window is not open: the window in front is closed instead (one rule for "close
    that"), said with what it was; when nothing is open to close, that is said, so no answer says it closed something
    (live 2026-10-11: "" read as done, and the answer said "I closed the magnifier"); "" only with no studio at all.
    Logged as code choosing for the model."""
    from .brain.toolbox.present import _original_screen, close_window

    screen = screen_of(services.hub)
    scope = _original_screen(screen, ctx) if ctx is not None else None
    report = screen.request_report(**scope) if scope else screen.latest
    front = frontmost((report or {}).get("items"))
    if front is None:
        if report is None:
            return ""
        return f"{instead_of} was not open, and nothing else is open to close: nothing was closed"
    log = getattr(services, "log", None)
    if callable(log):
        from .brain.intent import log_override

        log_override(log, "close-frontmost", asked=instead_of, closed=front.get("id"))
    said = await close_window(services, ctx, str(front["id"]))
    return f"{instead_of} was not open; {said} (the window in front)"


def live_magnifications(items: list[dict] | None) -> list[dict]:
    """The live magnification windows open and shown (native windows on a live capture of an outline: zoom's
    "… · live magnification"; live_sources.object_source), the one in front first: what "the magnifier" means once no
    magnifier panel is open. Never a window of another kind (a photo, a comparison, live motion)."""
    windows = [i for i in items or [] if str(i.get("id") or "").startswith("w-") and i.get("open", True)
               and (i.get("visible", True) or laid_out(i))
               and (i.get("sourceRef") or {}).get("kind") == "live" and (i.get("sourceRef") or {}).get("media") == "capture"]
    return sorted(windows, key=lambda i: i.get("z") or 0, reverse=True)


async def close_live_magnification(services, ctx) -> str:
    """close_magnifier with no magnifier panel open: the live magnification window in front, closed by its instance;
    when none is open, that is said and nothing is closed (live 2026-10-11 16:36:38: the window in front of any kind
    was closed instead, a reference photo, and the answer said the live magnification was closed). "" only with no
    studio at all."""
    from .brain.toolbox.present import _original_screen, close_window

    screen = screen_of(services.hub)
    scope = _original_screen(screen, ctx) if ctx is not None else None
    report = screen.request_report(**scope) if scope else screen.latest
    if report is None:
        return ""
    windows = live_magnifications(report.get("items"))
    if not windows:
        return "the magnifier was not open, and no live magnification window is open: nothing was closed"
    return f"the live magnification window: {await close_window(services, ctx, str(windows[0]['id']))}"


def open_state(pid: str, open_: bool, shows: str | None = None) -> Callable[[dict], bool]:
    """A check that the report shows panel `pid` open (showing `shows`) or closed."""
    def check(report: dict) -> bool:
        item = next((i for i in report.get("items", []) if i.get("id") == pid), None)
        if not open_:
            return item is None or not item.get("open")
        return item is not None and bool(item.get("open")) and (shows is None or item.get("shows") == shows)
    return check


async def on_screen(hub, check: Callable[[dict], bool], after: float, what: str, next_step: str) -> None:
    """Done only once the screen shows it (Amendment 2, point 4): with a studio showing the view, wait for
    its report; ToolFailed when it does not show `what` in time. Without one (a voice-only test, a
    bench without a studio), nothing to wait for."""
    from .brain.registry import ToolFailed

    screen = getattr(hub, "screen", None)
    if not isinstance(screen, Screen) or not screen.showing():
        return
    if not await screen.until(check, after):
        raise ToolFailed(f"{what} on screen: the studio has not shown it", next=next_step)


def magnifier_source(source: str, object_id: str | None = None) -> Callable[[dict], bool]:
    """A check that the open magnifier shows `source` (a picture's URL, or "held" or "live") and, when
    given, the object `object_id`."""
    def check(report: dict) -> bool:
        item = next((i for i in report.get("items", []) if i.get("id") == "magnifier"), None)
        return (item is not None and bool(item.get("open")) and item.get("source") == source
                and (object_id is None or item.get("shows") == object_id))
    return check


def magnifier_beside(url: str | None, object_id: str | None = None) -> Callable[[dict], bool]:
    """A check that the open magnifier shows the picture at `url` beside its close-up (of `object_id`)."""
    def check(report: dict) -> bool:
        item = next((i for i in report.get("items", []) if i.get("id") == "magnifier"), None)
        return (item is not None and bool(item.get("open")) and bool(url) and item.get("beside") == url
                and (object_id is None or item.get("shows") == object_id))
    return check


def magnifier_level(level: float, object_id: str | None = None) -> Callable[[dict], bool]:
    """A check that the open magnifier is at `level` (x the fitted view), on `object_id` when given."""
    def check(report: dict) -> bool:
        item = next((i for i in report.get("items", []) if i.get("id") == "magnifier"), None)
        return (item is not None and bool(item.get("open")) and isinstance(item.get("level"), (int, float))
                and abs(float(item["level"]) - level) < 0.01 and (object_id is None or item.get("shows") == object_id))
    return check


def magnifier_arrangement(source: str, object_id: str | None, pictures: list[str], beside: str | None = None):
    """Exact selection/subject and loaded image panes, rather than requested URLs alone."""
    def check(report):
        item = next((i for i in report.get("items", []) if i.get("id") == "magnifier"), {})
        loaded = {p.get("source") for p in item.get("pictures", [])}
        return (bool(item.get("open")) and item.get("visible") is not False
                and item.get("source") == source and item.get("shows") == object_id
                and item.get("beside") == beside and set(pictures) <= loaded)
    return check


async def picture_shown(magnifier, jpeg: bytes, label: str, object_id: str | None = None, **kind) -> str:
    """Magnifier.show_picture, done only once the studio's report shows that picture in the open magnifier
    (flow 7; M1.5b re-check N5). Its URL, or ToolFailed when the studio does not show it. `kind`: show_picture's
    kind_of and credit (a reference photo from the web)."""
    hub = getattr(magnifier, "hub", None)
    if hub is None:  # no hub, no studio: nothing to wait for
        return magnifier.show_picture(jpeg, label, object_id=object_id, **kind)
    after = screen_of(hub).reported_at
    url = magnifier.show_picture(jpeg, label, object_id=object_id, **kind)
    primary = magnifier.sources[magnifier.index]
    selected = primary.get("url") if primary.get("kind") == "image" else primary.get("kind")
    peer = magnifier.shown_beside()
    pictures = [s["url"] for s in [primary, peer] if s and s.get("kind") == "image"]
    check = magnifier_arrangement(selected, magnifier.displayed_object, pictures, (peer or {}).get("url"))
    await on_screen(hub, check, after, "the picture arrangement is not shown in the magnifier",
                    "look at the magnifier, or ask again")
    return url


async def image_shown(magnifier, url: str, label: str, credit: str = "", object_id: str | None = None,
                      kind_of: str = "unknown") -> None:
    """Magnifier.show_image (a researcher's picture), done only once the studio's report shows it."""
    hub = getattr(magnifier, "hub", None)
    if hub is None:
        magnifier.show_image(url, label, credit=credit, object_id=object_id, kind_of=kind_of)
        return
    after = screen_of(hub).reported_at
    magnifier.show_image(url, label, credit=credit, object_id=object_id, kind_of=kind_of)
    await on_screen(hub, magnifier_arrangement(url, magnifier.displayed_object, [url]), after, "the picture is not shown in the magnifier",
                    "look at the magnifier, or ask again")


async def fresh_report(hub, client=None, *, now: bool = False, timeout: float | None = None) -> bool:
    """The one way every actor's request gets its current report (`Screen.current_report`): a typed Send, a voice
    hand-off or voice tool (`now`), a researcher's own studio action, a connected agent's run. A hub without a Screen
    (a test's stand-in) has nothing to ask: False, and the request goes on as before."""
    screen = getattr(hub, "screen", None)
    if not isinstance(screen, Screen):
        return False
    try:
        return bool(await screen.current_report(client, now=now, timeout=timeout))
    except Exception:  # asking for a report never breaks the request: the last report stands
        return False


def screen_of(hub) -> Screen:
    found = getattr(hub, "screen", None)
    if not isinstance(found, Screen):
        found = Screen(getattr(hub, "broadcast", None))
        try:
            hub.screen = found
        except AttributeError:
            pass
    return found


# --- which presentation the words mean -------------------------------------------------------------
WORDS = (
    ("live", ("live view", "live", "video feed", "feed", "camera", "microscope view")),
    ("magnifier", ("magnifier", "close-up", "closeup", "zoom", "enlarged")),
    ("viewer", ("notes", "research", "lesson", "video", "result", "report", "researcher", "viewer", "graph")),
    ("slidemap", ("slide map", "map")),
    ("card", ("card", "description", "details")),
    ("made-today", ("made today", "made-today", "made in this session")),
    ("sessions", ("sessions list", "session list", "sessions rail", "sessions")),
    ("sidebar", ("sidebar", "side bar", "left panel")),
    ("population", ("cells", "cell outlines", "outlined cells", "layer", "population", "segmentation", "matches",
                    "masks", "heatmap", "heat map", "colours", "legend")),
    ("tracks", ("tracks", "motion tracks", "tracks layer", "motion", "trajectories", "paths")),
    ("outlines", ("outlines", "highlights", "outline", "highlight")),
)


class UnclearReference(ValueError):
    """The words fit several things on screen: the user is asked which (never one picked at random)."""


def resolve(hub, what: str, ctx=None, entry=...) -> tuple[str, str]:
    """(presentation id, kind) for what the brain gave (M1.5b review item 4): first an id the studio's
    report names, an index entry's id, a population's id ("e3", "layer:e3") or a step's ("@1"); then the
    user's words, only when they fit exactly one kind (whole words: "the heatmap" is not "map"); words
    that fit several raise UnclearReference with the candidates. `entry`: the index entry `what` names, looked up
    by the caller off the event loop (None: none); left out, it is looked up here."""
    import re

    from .population import layer_of

    raw = str(what or "").strip()
    key = " ".join(raw.lower().split())
    screen = screen_of(hub)
    scoped = ctx is not None and (getattr(ctx, "client", None) is not None or getattr(ctx, "request_snapshot", None) is not None)
    if scoped:
        admission = getattr(ctx, "admission", None)
        report = screen.request_report(ctx.request_snapshot, ctx.client,
            request_id=admission.request_id if admission is not None else ctx.request_id)
        shown = report["items"]
    else:
        shown = screen.items()
    if not key:
        raise ValueError("which thing on screen?")
    found = next((item for item in shown if item["id"] in (key, raw)), None)
    if found is not None:
        return str(found["id"]), str(found.get("kind") or key)
    if key.startswith("@") and ctx is not None:
        from .brain.registry import step_output
        from .population import resolve as population_of

        if re.fullmatch(r"m\d+", step_output(ctx, key) or ""):  # the step kept motion tracks (M3.2)
            return f"tracks:{step_output(ctx, key)}", "tracks"
        population = population_of(hub, key, ctx)  # ToolFailed with why when the step made nothing
        return f"layer:{population.id}", "population"
    if key.startswith("layer:") or (key[:1] == "e" and key[1:].isdigit()):
        return f"layer:{key.split(':')[-1]}", "population"
    if key.startswith("tracks:") or re.fullmatch(r"m\d+", key):
        return f"tracks:{key.split(':')[-1]}", "tracks"
    from .resources import resources_of

    if (resources_of(hub).get(raw) if entry is ... else entry) is not None:
        return f"entry:{raw}", "entry"  # an index entry, opened as its presentation
    # The words, longest phrase first, each taking the words it covers ("cell outlines" is the cells' layer,
    # not also "outlines"); a thing's own title is one more phrase ("the cell outlines" is "Cell outlines ·
    # 18:04"), so words beside it that mean something else are still asked about (M1.5b re-check N3).
    phrases = [(w, k, None) for k, words in WORDS for w in words]
    for item in shown:
        base = str(item.get("title") or "").lower().split(" · ")[0].strip()
        if base:
            phrases.append((base, str(item.get("kind") or ""), str(item["id"])))
    rest, matched, items = f" {key} ", [], {}
    for phrase, kind, item_id in sorted(phrases, key=lambda p: -len(p[0])):
        pattern = rf"\b{re.escape(phrase)}\b"
        if re.search(pattern, rest):
            rest = re.sub(pattern, " ", rest)
            if kind not in matched:
                matched.append(kind)
            if item_id is not None:
                items[kind] = item_id
    if matched == ["outlines"] and not any(o.kind in ("highlight", "part") for o in hub.scene.objects.values()):
        from .population import resolve as population_of

        if layer_of(hub).shown is not None or population_of(hub, None, ctx) is not None:
            matched = ["population"]  # "the outlines" with none of the app's drawn: the cells' (shown or not)
    if len(matched) == 1 and matched[0] in items:
        return items[matched[0]], matched[0]
    if len(matched) > 1:
        raise UnclearReference("which one: " + " or ".join(NAMES.get(k, k) for k in matched) + "?")
    if not matched:
        raise ValueError(f"nothing on screen is {raw!r}")
    kind = matched[0]
    if kind == "tracks":
        from .motiontracks import track_layer_of, tracks_of

        shown = track_layer_of(hub).shown
        if shown is None:
            newest = tracks_of(hub).latest()
            shown = newest.id if newest is not None else None
        if shown is None:
            raise ValueError("no motion has been measured here yet (track_motion measures it)")
        return f"tracks:{shown}", "tracks"
    if kind == "population":
        layer = layer_of(hub)
        shown = layer.shown
        if shown is None:
            from .population import resolve as population_of

            newest = population_of(hub, None, ctx)
            shown = newest.id if newest is not None else None
        if shown is None:
            from .population import nothing_here

            raise ValueError(nothing_here(hub))  # none made, or only cells made for a comparison (M2.2 re-check)
        return f"layer:{shown}", "population"
    if kind == "sessions":
        return "sessions", "sessions"  # the sessions list: the session's title's menu, reported as an item of its own
    return kind, kind


def why_not(kind: str, verb: str) -> tuple[str, str] | None:
    """(why, next step) when `kind` does not take `verb`, else None."""
    if verb in TAKES.get(kind, ()):
        return None
    if (kind, verb) in WHY_NOT:
        return WHY_NOT[(kind, verb)]
    takes = TAKES.get(kind, ())
    return (f"{NAMES.get(kind, kind)} can't be {verb_words(verb)}",
            f"it can be: {', '.join(verb_words(v) for v in takes)}" if takes else "ask what is on screen")


def verb_words(verb: str) -> str:
    return {"show": "shown", "hide": "hidden", "opacity": "faded or dimmed", "colour": "coloured", "fill": "filled",
            "edge": "drawn as edges", "emphasise": "narrowed to a selection", "open": "opened", "close": "closed",
            "move": "moved", "resize": "resized", "front": "brought forward"}.get(verb, verb)
