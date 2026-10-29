"""The session's one index (contract 10; Amendments 1.3, 2.7 and 3 of
docs/design/2026-10-06-captures-operations-evidence.md): everything the session made, found in one
place and read one way, by the brain (the `resources` tool), researchers (their `session` tool, over the
HTTP API), the studio and programs outside the app (`/api/resources`).

Entries are read from the stores that keep them, never copied: evidence (populations, and motion tracks
since M3.2), researchers' work, snapshots, named outlines (seen.py), taught labels (taught.py), the scale's calibrations,
datasets and recordings. What tools measure or make that has no store of its own (counts,
measurements, comparisons, clearer pictures, watches that fired...) becomes a **result** entry, built
from the tool run itself (`Results`): the tool and its arguments, the frame it was made on, the
executions it used, its summary, its values and its pictures by reference. Each producer declares
what it makes in produces.py.

Every entry has a plain title ("Cell outlines · Cellpose-SAM · 18:04", "Motion · 18:10", "Researcher 2's lesson")
beside its id (Amendment 4, item 1). Readers never write to the index; nothing from it is pushed into a
prompt (the brain pulls it: "context earns its place").

The index answers for a session (ADR 0017), not a day: every reader's `day` is a session id (None: the session open
now), and an entry is addressed by (session, id) (`Entry.day`; ids are new for the session, day/ids.py). A session's
entries are read from every calendar date it touched, each store's records kept by their own "session" (a record from
before sessions belongs to the session of its date: `sid.session_of`); where an entry is filed is its own date folder
(`Entry.date`), found from the session's journal. The session open now is read from memory, another from its journal
and files, read-only.
"""
from __future__ import annotations

from .retained_input import comparison_input_key

import hashlib
import copy
import heapq
import asyncio
import json
import math
import os
import re
import threading
import time
import weakref
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from .day import sid as sids
from .day.sid import session_of
from .produces import ASKED_FOR_ONLY, KINDS, LATER, TOOLS, is_product, kinds_of, role_of
from . import research_entries as work

PREVIEW_MAX = 1500  # characters of a result's values the journal keeps as a preview (the full values: d<n>.json)
TABLE_DISCOVERY_BYTES = 65 << 20  # legacy full record: at most the 64 MiB runner reply plus metadata
TABLE_DISCOVERY_CACHE = 128  # routing booleans only; no retained rows or trust in their contents
LIST_PREVIEW = 5  # items of a list a preview keeps ("and N more")
ARGS_MAX = 600
READ_MAX = 12000  # characters of a complete reading per model-facing page
SUMMARY_MAX = 600
PICTURES_MAX = 4  # a result keeps at most this many of the pictures its tool showed
COMPARISON_SAVE_S = 10.0  # a stalled disk cannot hold a request's display indefinitely
RESULT_SAVE_S = 30.0  # all original attachments share one completion deadline


@dataclass(frozen=True)
class RetainedComponent:
    """Private producing pixels and frozen facts; bytes never enter a tool's values or wire."""
    jpeg: bytes = field(repr=False)
    facts_json: str

    @classmethod
    def of(cls, jpeg: bytes, facts: dict):
        return cls(jpeg, json.dumps(facts, allow_nan=False))

    def facts(self):
        return json.loads(self.facts_json)


@dataclass
class _PreparedComparison:
    run: Any  # identity-checked weak reference (ToolRun is unhashable)
    reservation: Any
    stopped: threading.Event = field(default_factory=threading.Event)
    entry: dict | None = None
    consumed: bool = False

# A result's plain title by what made it (Amendment 4, item 1).
RESULT_TITLES = {
    "track_motion": "Motion", "measure": "Measurement", "count": "Count", "contrast": "Comparison",
    "compare": "Side by side", "enhance": "Clearer picture", "what_happened": "What happened",
    "where_is": "Where it is", "focus_check": "Focus check", "dissect": "Parts", "watch": "Watch",
    "reference_photo": "Reference photo",
}

# A researcher's deliverable in plain words (an entry's title uses only the app's words).
DELIVERABLE_WORDS = {"research": "notes", "lesson": "lesson", "video": "video", "image": "illustration",
                     "annotated_image": "annotated photo", "report": "report"}  # research.policy.DELIVERABLES

# What can be done with each kind of entry (the verbs come with presentations, M1.5b).
ACTIONS = {
    "population": ("open", "read", "show", "hide", "highlight", "query"),
    "member": ("open", "read"),
    "result": ("open", "read"),
    "research": ("open", "read", "follow up"),
    "snapshot": ("open", "read"),
    "named-outline": ("read",),
    "taught-label": ("read",),
    "calibration": ("read",),
    "dataset": ("read",),
    "recording": ("read",),
    "bookmark": ("open", "read"),
    "clip": ("open", "read"),
    "tracks": ("open", "read", "show", "hide"),
    "individual": ("read",),
    "outcome": ("read",),
    "fact": ("read",),
    "annotation": ("read",),
    "dataset-table": ("read",),
    "volume": ("open", "read"),
    "job": ("open", "read"),
    "project": ("read",),
    # A researcher's other work (research_entries; ticket 66): opened in the studio's reader, read by id.
    "lesson": ("open", "read"),
    "video": ("open", "read"),
    "figure": ("open", "read"),
    "finding-table": ("open", "read"),
    "finding-chart": ("open", "read"),
    "source": ("open", "read"),
}


# An index entry's id is one word ("e3", "d4", "r704811", "snapshot-101500-f12", "u1a2b3c"); words never are one.
ENTRY_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,79}")
HASHED_ID = re.compile(r"[uf][0-9a-f]{6}")  # an outcome's (u) or a settled fact's (f): made from what it is
WORK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._@-]{0,40}:(?:o\d+|table-\d+|chart-\d+|source-[0-9a-f]{12})")  # research_entries
KINDS_BY_ID = {"u": ("outcome",), "f": ("fact",)}


def _words(text: object, limit: int) -> str:
    """A researcher's words as one line of plain text for Data (never a model's line): whitespace collapsed, capped."""
    line = " ".join(str(text or "").split())
    return line[:limit]


def hhmm(at: float | None) -> str:
    return time.strftime("%H:%M", time.localtime(at or time.time()))


def _finite_time(value) -> float | None:
    """An explicitly kept producing time, including Unix zero; absence is not a clock."""
    if value is None or isinstance(value, bool):
        return None
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


def _research_picture_source(session, output: dict) -> dict:
    """An output's own immutable identity, copied before reading its retained file."""
    return {"researcher": session.rid, "output": output.get("id"),
            "version": output.get("version"), "path": str(output.get("path") or "")}


def _capped(value: Any, limit: int) -> Any:
    """`value` if its JSON fits in `limit` characters, else a note that it was cut (never a half value)."""
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        return None
    return value if len(text) <= limit else {"cut": f"{len(text)} characters, more than the {limit} kept"}


def preview(value: Any, depth: int = 0) -> Any:
    """A short view of a result's values for the journal and lists: every scalar kept, every list cut to its
    first few with how many more, nested as far as three levels (the full values are in the result's file)."""
    if isinstance(value, dict):
        return {k: preview(v, depth + 1) for k, v in value.items()} if depth < 3 else "{...}"
    if isinstance(value, (list, tuple)):
        items = [preview(v, depth + 1) for v in list(value)[:LIST_PREVIEW]]
        return items + ([f"and {len(value) - LIST_PREVIEW} more"] if len(value) > LIST_PREVIEW else [])
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value if not isinstance(value, str) else value[:200]
    return str(value)[:200]


@dataclass
class Entry:
    id: str
    kind: str
    title: str
    day: str  # the session it belongs to (its address is (session, id); the wire's "day")
    at: float
    summary: str = ""
    state: str = "done"  # done | running | saved | saving | failed | ...
    thread: str | None = None
    placement: str | None = None  # where it is shown now, in words ("shown, 298 of 312 verified in place")
    made_by: dict = field(default_factory=dict)
    source: dict = field(default_factory=dict)  # the frame it was made on: {frame, seq, boot}
    pictures: list[str] = field(default_factory=list)  # URLs
    values: Any = None
    detail: Callable[[], str] | None = None  # the full readable text, made when read
    untrusted: bool = False  # its summary and text are a researcher's (web-derived): to a prompt only inside untrusted.block
    plain: str = ""  # its row in the studio in plain words (Amendment 4.1), when the summary is technical
    version: int | None = None  # what it is now, when it can change (a population's revision): a reader records it
    media: str | None = None  # what kind of picture it holds when that is not the camera's (media.py: "reference")
    read_by: list[dict] = field(default_factory=list)  # successful exact-revision reads only
    time_known: bool | None = None  # result metadata distinguishes an absent time from genuine numeric zero
    date: str = ""  # the calendar date folder it is filed under, when it has one (a session can span days)
    portable: bool = False  # a result that says which capabilities made it (madeWith): it can be downloaded with them
    table: bool = False  # a result that keeps a table (a capability's: tables.result_table), read on the Measurements page
    # Ticket 66 (Data and research). `title` stays the app's own words, which every line for a model uses; `shown` is
    # the title Data shows when it is a researcher's (its report's title, a table's): untrusted, on the wire only.
    shown: str = ""
    maker: dict | None = None  # who made it (maker_of), set as the index is collected; None when the record cannot tell
    run: str | None = None  # the execution that made it, when it came from one run: entries sharing it are one Data row
    material: dict | None = None  # shared inventory projection; never an accepted-record edit
    forms: list[dict] | None = None  # a write-up's forms, newest first (a report's findings versions and presentation)

    @property
    def actions(self) -> tuple[str, ...]:
        return ACTIONS.get(self.kind, ("read",))

    def picture_source(self, n: int) -> dict | None:
        """The selected picture's basis; a researcher can have several independent output versions."""
        if self.kind == "result" and self.source.get("components"):
            if n == 0:
                return self.source
            return next((c for c in self.source["components"]
                         if (c.get("identity", {}).get("resource") or {}).get("picture") == n), None)
        if self.kind != "research":
            return self.source
        pictures = self.source.get("pictures") or []
        return pictures[n] if 0 <= n < len(pictures) else None

    @property
    def role(self) -> str | None:
        """Which of Data's sections it is in (produces.ROLES; ticket 66, S1)."""
        if self.kind == "project" and isinstance(self.values, dict):
            from .project_material import describe
            return (self.material or describe(self.values))['section']
        return role_of(self.kind, self.media)

    def wire(self, listed: bool = True) -> dict:
        """What every reader is sent. `listed` (the studio's Data, ticket 66): its role, maker and run, the title Data
        shows, a write-up's forms and a source's values. Without them, the reading a researcher pages through and whose
        digest it records (Index.document): unchanged by those fields, so a read before them still counts."""
        out = self._basis()
        if listed and self.kind == "project" and isinstance(self.values, dict):
            from .project_material import describe
            out['material'] = copy.deepcopy(self.material or describe(self.values))
        if not listed:
            return out
        return {**out, "title": self.shown or self.title, "role": self.role,
                "maker": copy.deepcopy(self.maker), "run": (out.get("material", {}).get("run") or (self.id if out.get("material", {}).get("kind") == "workflow" else self.run)),
                **({"forms": copy.deepcopy(self.forms)} if self.forms is not None else {}),
                **({"values": copy.deepcopy(self.values)} if self.kind == "source" else {})}

    def _basis(self) -> dict:
        return {"id": self.id, "kind": self.kind, "title": self.title, "day": self.day, "at": round(self.at, 3),
                "time": time.strftime("%H:%M:%S", time.localtime(self.at)) if self.at else None,
                "thread": self.thread, "state": self.state, "placement": self.placement, "madeBy": self.made_by,
                "source": self.source, "summary": self.summary, "actions": list(self.actions), "pictures": self.pictures,
                # A registration that did not finish made nothing: it stays on brainreg's panel, to run again.
                "product": is_product(self.kind, self.made_by) and (self.kind != "job" or self.state == "done"),
                **({"plain": self.plain} if self.plain else {}), **({"untrusted": True} if self.untrusted else {}),
                **({"version": self.version} if self.version is not None else {}),
                **({"media": self.media} if self.media else {}),
                **({"readBy": self.read_by} if self.read_by else {}),
                **({"date": self.date} if self.date else {}),
                **({"portable": True} if self.portable else {}),
                **({"table": True} if self.table else {})}

    def line(self) -> str:
        """One line for the brain: its title first, its id one step away (Amendment 4). An untrusted entry's
        summary is left out here: it reaches a prompt only inside its wrapper (the resources tool)."""
        parts = [f"{self.title} ({self.id})"]
        if self.summary and not self.untrusted:
            parts.append(self.summary)
        if self.kind == "research":
            parts.append(f"open with show_result(researcher='{self.id}'); written report available: {bool(self.source.get('written'))}; retained pictures: {len(self.pictures)}")
        if self.placement:
            parts.append(self.placement)
        if self.state not in ("done", "saved"):
            parts.append(self.state)
        return "; ".join(parts)


# Who made an entry, as Data's "Made by" says it (ticket 66, S2): the user, the Assistant or one researcher.
YOU = {"kind": "you", "name": "You"}
ASSISTANT = {"kind": "assistant", "name": "The Assistant"}
# A record's own "by" that names the user (an annotation's, a taught label's, Record's datasets and screen recordings, a
# table made in the studio) or the Assistant (a table tool run by the brain: "agent:chat", "agent:voice").
BY_YOU = frozenset({"user", "you", "Record"})
BY_ASSISTANT = frozenset({"agent:chat", "agent:voice"})


@dataclass(frozen=True)
class Researchers:
    """What a session's researchers say of themselves, for maker_of: each one's name by its id, and what each had the
    app make for it (its `ran`, the app's own record: entry id -> researcher id)."""
    names: dict = field(default_factory=dict)
    ran: dict = field(default_factory=dict)

    @classmethod
    def of(cls, sessions) -> "Researchers":
        names, ran = {}, {}
        for s in sessions or ():
            rid = getattr(s, "rid", None)
            if not isinstance(rid, str):
                continue
            names[rid] = str(getattr(s, "name", "") or rid)
            for row in getattr(s, "ran", None) or ():
                if isinstance(row, dict) and isinstance(row.get("id"), str):
                    ran.setdefault(row["id"], rid)
        return cls(names, ran)


def maker_of(kind: str, eid: str, made_by: dict | None, researchers: Researchers | None = None) -> dict | None:
    """Who made an entry, only from what its record says (ADR 0006: never a guess, so an older record that cannot tell
    is None and Data says nothing). In order: a researcher's own work (its report, `made_by.researcher`); who started the
    run (`made_by.actor`, recorded where the run begins since ticket 66); a researcher it was made for (`for`: its hands'
    runs and the footage held or captured for it; its id only when the researcher's own `ran`, or the id itself, says);
    a record's own "by"; a click (origin "click" is the user's own)."""
    made = made_by if isinstance(made_by, dict) else {}
    who = researchers or Researchers()
    if kind == "research":
        return {"kind": "researcher", "id": eid, "name": who.names.get(eid, eid)}
    rid = made.get("researcher")
    if isinstance(rid, str) and rid:
        return {"kind": "researcher", "id": rid, "name": str(made.get("name") or who.names.get(rid) or rid)}
    actor = made.get("actor")
    if actor == "you":
        return dict(YOU)
    from .door import actor as door_actor
    if door_actor.is_agent(actor):  # an outside agent's work, by its label as it names itself (ADR 0026)
        return door_actor.maker(actor)
    if actor == "assistant":
        return dict(ASSISTANT)
    made_for = made.get("for")
    if isinstance(made_for, str) and made_for:
        if made_for in who.names:  # an id (a camera clip captured for a researcher's mission)
            return {"kind": "researcher", "id": made_for, "name": who.names[made_for]}
        found = who.ran.get(eid)
        if found is not None and who.names.get(found) == made_for:
            return {"kind": "researcher", "id": found, "name": made_for}
        return {"kind": "researcher", "name": made_for}
    by = made.get("by")
    if by in BY_YOU:
        return dict(YOU)
    if by in BY_ASSISTANT:
        return dict(ASSISTANT)
    if made.get("origin") == "click":
        return dict(YOU)
    return None


def single_run(executions) -> str | None:
    """The run an entry came from (ticket 66, S3): its execution when it came from exactly one (a result over several,
    a comparison of models, is a row of its own)."""
    found = [e for e in (executions or ()) if isinstance(e, str) and e]
    return found[0] if len(found) == 1 else None


def _capability_title(args, values) -> str | None:
    """A capability's result is titled by the capability ("Spot measure · 19:32"), not by the tool that ran it ("Run
    capability · 19:32", live 2026-10-15: every row of Assets and the index read the same). Its name came with the run
    (kinds.said_name, capped plain words), so the catalog is never read again here."""
    name = values.get("name") if isinstance(values, dict) else None
    return str(name) if name else None


def keeps_table(values) -> bool:
    """Whether a result keeps a table the Measurements page reads (tables.result_table): a capability's columns and rows,
    or its points, bound to its producer's sha256. The page reads it and checks that digest; this only lists it."""
    if (not isinstance(values, dict) or not isinstance(values.get("producer"), dict)
            or not values['producer'].get('sha256')):
        return False
    return (isinstance(values.get('columns'), list) and isinstance(values.get('rows'), list)
            or isinstance(values.get('points'), list))


def _table_descriptor(values):
    """A bounded routing hint, not verification of the retained table's values."""
    if not keeps_table(values):
        return None
    digest = values['producer']['sha256']
    if not isinstance(digest, str) or re.fullmatch(r'[0-9a-f]{64}', digest) is None:
        return None
    return {'format': 1, 'sha256': digest}


# --- results: what tools measured or made ---------------------------------------------------------------
class Results:
    """Result entries, built from tool runs (Amendment 3, point 2), filed in the journal of the session they were
    asked in (ADR 0001 and 0017: a restart, and reopening the session, have them) with their pictures as files under
    `root/<date>/` (paths.results), each entry's own "day" its date folder and its "session" the session."""

    def __init__(self, root: Path | None, day=None, pictures: Callable[[str], bytes | None] | None = None) -> None:
        self.root = Path(root) if root is not None else None
        self.day = day  # the SessionStore
        self.pictures = pictures or (lambda key: None)  # magnifier key -> JPEG bytes
        self._lock = threading.Lock()
        self._mine: dict[str, dict] = {}  # without a day store (tests, benches): kept in memory
        self._pending: dict[tuple[str, str], bytes] = {}  # pictures still being written: (day, file name) -> bytes
        self._picture_writes: dict[tuple[str, str], threading.Event] = {}
        self._prepared: dict[int, _PreparedComparison] = {}
        self._consumed: dict[int, tuple[Any, dict]] = {}
        self._discarded: set[tuple[str, str]] = set()
        self._retention_checked: set[tuple[str, str]] = set()
        self._table_candidates: OrderedDict[tuple, bool] = OrderedDict()
        self._given = 0  # the highest "d" number given in this run (ids are new for the session and the folder)
        self._floors: dict[tuple, int] = {}  # (session, date) -> the floor read once a run (day/ids.py)
        # Told each result kept (its entry): a clearer picture becomes one of the user's assets (App: library.py).
        self.on_saved: list[Callable[[dict], None]] = []
        self._project_roots = {}

    def root_for(self, session: str | None = None, *, write=False):
        """Resolve result bytes using their owner, independent of the visible Project.

        Legacy results retain their shared date layout. Routed results keep that
        layout beneath their own artifacts/results. A host-held locator or folder
        inode may not change under an admitted result writer.
        """
        locations = getattr(self.day, 'locations', None)
        if locations is None:
            return self.root
        session = session or self._session()
        project = locations.project(session)
        if project is None:
            return self.root
        if write:
            locations.writable(session)
        folder = project.folder('artifacts') / 'results'
        known = (project.origin, project.path) in self._project_roots
        if write and not known:
            folder.mkdir(exist_ok=True)
        if folder.is_symlink():
            from .project_roots import ProjectLocationError
            raise ProjectLocationError('Project result storage was replaced by a link')
        if folder.exists():
            import stat
            from .project_roots import ProjectLocationError
            info = folder.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ProjectLocationError('Project result storage is not a folder')
            key, identity = (project.origin, project.path), (info.st_dev, info.st_ino)
            prior = self._project_roots.setdefault(key, identity)
            if prior != identity:
                raise ProjectLocationError('Project result storage changed; recover it before continuing')
        elif (project.origin, project.path) in self._project_roots:
            from .project_roots import ProjectLocationError
            raise ProjectLocationError('Project result storage is missing; recover it before continuing')
        return folder

    def folder_for(self, date, session=None, *, write=False):
        root = self.root_for(session, write=write)
        if root is None:
            return None
        if not isinstance(date, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) is None:
            raise ValueError('The retained result date is invalid')
        folder = root / date
        if folder.is_symlink() or (folder.exists() and not folder.is_dir()):
            from .project_roots import ProjectLocationError
            raise ProjectLocationError('Result date storage was replaced; recover it before continuing')
        return folder

    def _file_key(self, date, name, session=None):
        # Cleanup must wake original waiters even when a locator is gone/closed.
        # A routed host uses stable owner keys, never mutable filesystem paths.
        return (f'{session or self._session()}:{date}' if getattr(self.day, 'locations', None) is not None else date, name)

    def picture_pending(self, date, name, session=None):
        return self._file_key(date, name, session) in self._pending

    # --- making ---------------------------------------------------------------------------------
    async def stage_comparison(self, run, ctx, hub, jpeg: bytes, components: tuple[RetainedComponent, ...],
                               reservation, guard: Callable[[], None], summary: str) -> None:
        """Durable original bytes/metadata before the existing display effect, never in a listener.

        The worker owns the reservation through timeout/cancellation; a stopped worker discards
        only its own record. The finished listener later consumes this exact producing id once.
        """
        from .brain.registry import ToolFailed

        prepared = _PreparedComparison(weakref.ref(run), reservation)
        if len(components) != 2 or self.root is None or getattr(self.day, "journal", None) is None:
            reservation.release()
            raise ToolFailed("the comparison cannot retain its two originals here", next="use a writable local result store")
        key = id(run)
        if key in self._prepared:
            reservation.release()
            raise ToolFailed("this comparison already has a prepared result", next=None)  # no next: this producer already owns its one staged result
        self._prepared[key] = prepared
        def orphaned(_):
            if self._prepared.get(key) is prepared:
                self._prepared.pop(key, None)
                self._discard_later(prepared)
        prepared.run = weakref.ref(run, orphaned)

        def current():
            if prepared.stopped.is_set():
                raise ToolFailed("the comparison was stopped before its originals were saved", next="ask again")
            guard()

        def save():
            try:
                current()
                source = {"frame": None, "seq": None, "boot": None,
                          "captures": [c.facts() for c in components],
                          "requestId": getattr(getattr(ctx, "admission", None), "request_id", None),
                          "step": run.step[0] if run.step is not None else None}
                prepared.entry = self.add(run.tool, run.args, summary, values=run.values or None,
                                          picture_bytes=(jpeg, *(c.jpeg for c in components)), source=source,
                                          actor=getattr(run, "actor", None),
                                          thread=self._thread_of(getattr(ctx, "asked", None)), request=ctx.question,
                                          components=components, durable_guard=current,
                                          session=self._session_of_asked(getattr(ctx, "asked", None)))
                current()
            except BaseException:
                self._discard_comparison(prepared)
                raise

        worker = asyncio.create_task(asyncio.to_thread(save))

        def stopped_done(done):
            # Retrieve worker errors even when its awaiting request timed out or was canceled.
            if not done.cancelled():
                done.exception()
            if prepared.stopped.is_set():
                if self._prepared.get(key) is prepared:
                    self._prepared.pop(key, None)
                reservation.release()

        worker.add_done_callback(stopped_done)
        try:
            await asyncio.wait_for(asyncio.shield(worker), COMPARISON_SAVE_S)
            current()  # last request/day/member check on the event loop, before showing pixels
        except BaseException as exc:
            prepared.stopped.set()
            if worker.done():
                if self._prepared.get(key) is prepared:
                    self._prepared.pop(key, None)
                try:
                    await asyncio.to_thread(self._discard_comparison, prepared)
                finally:
                    reservation.release()
            if isinstance(exc, (TimeoutError, OSError, ValueError)):
                raise ToolFailed("the comparison originals could not be saved", next="check local storage and try again") from None
            raise

        # Cancellation of Registry.execute can bypass its finished listeners entirely.
        def producer_done(task):
            owner = prepared.run()
            if not prepared.consumed and (task.cancelled() or owner is None or owner.ok is not True):
                if self._prepared.get(key) is prepared:
                    self._prepared.pop(key, None)
                self._discard_later(prepared)

        if run.task is not None:
            run.task.add_done_callback(producer_done)

    def _discard_later(self, prepared) -> None:
        if prepared.consumed:
            return
        prepared.consumed = True
        if prepared.entry is not None:
            self._discarded.add(self._file_key(prepared.entry["day"], prepared.entry["id"], session_of(prepared.entry, prepared.entry["day"])))
        prepared.reservation.release()
        threading.Thread(target=self._discard_comparison, args=(prepared,), daemon=True).start()

    def _discard_comparison(self, prepared) -> None:
        entry = prepared.entry
        if entry is None:
            return  # add cleans a failed save before it returns a prepared entry
        self._discarded.add(self._file_key(entry["day"], entry["id"], session_of(entry, entry["day"])))
        self._discard_files(entry)

    def _discard_files(self, entry) -> None:
        """A discarded producer remains a small tombstone, never readable constituent pixels."""
        day, rid = entry["day"], entry["id"]
        session = session_of(entry, day)
        self.root_for(session, write=True)
        gone = {**entry, "state": "discarded", "pictures": [], "source": {"availability": "missing"}, "values": None}
        # This tombstone also protects a restart when the journal itself is unwritable.
        self._write(day, f"{rid}.json", json.dumps({"id": rid, "values": None, "state": "discarded"}).encode(), durable=True, session=session)
        # A normal result event with the same id replaces it on restart; no new artifact id.
        try:
            # Its own session's journal: the one loaded, else that session's (an old entry's session is its date).
            if self.day is not None and getattr(self.day, "sid", None) == session_of(entry, day):
                if self.day.current(entry.get("dayGeneration")):
                    self.day.journal.append({"type": "result", **gone})
            else:
                from .day.journal import Journal
                journal = getattr(self.day, "journal", None)
                if journal is not None:
                    Journal(session, self.day.root_for(session) if hasattr(self.day, 'root_for') else journal.root).append({"type": "result", **gone})
        except (OSError, ValueError):
            pass  # still release private pixel partials and keep the independent tombstone
        for name in entry.get("pictures", []):
            self._pending.pop(self._file_key(day, name, session), None)
            try:
                (self.folder_for(day, session, write=True) / name).unlink(missing_ok=True)
            except OSError:
                continue  # the discarded entry never advertises an undeleted private partial

    async def finish_run(self, run, ctx, hub=None) -> dict | None:
        """An image-producing operation completes only after its original save settles.

        Writing remains owned by this store. Stopping a waiter does not discard
        producing bytes or cancel another result's save.
        """
        retaining = run.tool == 'run_capability' or bool(run.picture_bytes or run.pictures)
        entry = self.from_run(run, ctx, hub, retaining=retaining)
        if entry is None or entry.get('state') not in ('saving', 'failed') or not retaining:
            return entry
        run.ok = None
        run.report_progress('Saving the original result')
        if run.resource:
            run.resource['kind'] = 'Result being saved'
        try:
            entry = await self.confirm_save(entry)
            run.ok = entry['state'] == 'done'
            if not run.ok:
                run.summary, run.next = entry['summary'], 'check local storage and rerun this tool'
            if run.resource:
                run.resource['kind'] = 'Saved result' if run.ok else 'Result not saved'
            return entry
        except asyncio.CancelledError:
            entry = self.get(entry['id'], entry.get('session')) or entry
            run.ok, run.summary, run.next = False, entry['summary'], None
            if run.resource:
                run.resource['kind'] = 'Result save interrupted'
            raise
        finally:
            consumed = self._consumed.get(id(run))
            if consumed is not None and consumed[0]() is run:
                self._consumed[id(run)] = (consumed[0], entry)

    async def confirm_save(self, entry: dict) -> dict:
        """One save boundary for foreground and researcher capability outputs."""
        from .brain.registry import ToolFailed

        cancelled = threading.Event()
        try:
            if entry.get('state') == 'failed':
                raise ValueError(entry['summary'])
            if self.root is None:
                raise ValueError('The result has no retained storage.')
            deadline = time.monotonic() + RESULT_SAVE_S
            for name in entry['pictures']:
                await asyncio.to_thread(self.wait_for_picture, entry['day'], name,
                                        timeout=max(0, deadline - time.monotonic()), cancelled=cancelled, session=session_of(entry, entry['day']))
            entry = {**entry, 'state': 'done'}
            self.day.record_result(entry, strict=True)
        except (OSError, ValueError):
            failed = ToolFailed('The result could not be saved', next='check local storage and rerun this tool')
            entry = {**entry, 'state': 'failed', 'summary': failed.said()}
            self.day.record_result(entry)
        except asyncio.CancelledError:
            entry = {**entry, 'state': 'failed',
                     'summary': 'Stopped while saving. Check this result before rerunning the tool.'}
            self.day.record_result(entry)
            raise
        finally:
            cancelled.set()
        if entry['state'] == 'done':
            self._notify_saved(entry)
        return entry

    def from_run(self, run, ctx, hub=None, *, produced=False, retaining=False) -> dict | None:
        """Retain a declared result. A producer may finish before its presentation.

        `produced` is used only after acquisition has returned actual bytes. It
        does not mark the tool or its subsequent display as successful.
        """
        consumed = self._consumed.get(id(run))
        if consumed is not None and consumed[0]() is run:
            # Acquisition can be retained before its display settles. Publish
            # the final summary against that same identity when Registry finishes;
            # never leave a completed photo looking like an active producer.
            if not produced and run.ok is not None:
                entry = consumed[1]
                summary = str(run.summary or ('Retained result' if run.ok else 'Display did not complete'))[:SUMMARY_MAX]
                if entry.get('summary') != summary:
                    entry = {**entry, 'summary': summary}
                    try:
                        if (self.day.sid == session_of(entry, entry['day'])
                                and self.day.current(entry.get('dayGeneration'))):
                            self.day.record_result(entry)
                            self._consumed[id(run)] = (consumed[0], entry)
                    except (OSError, ValueError):
                        pass  # retained originals remain available if enrichment fails
            return self._consumed[id(run)][1]
        prepared = self._prepared.pop(id(run), None)
        if prepared is not None and prepared.run() is not run:
            self._discard_later(prepared)
            prepared = None
        if (run.ok is not True and not produced) or "result" not in kinds_of(TOOLS.get(run.tool)) or run.tool in LATER:
            if prepared is not None:
                self._discard_later(prepared)
            return None
        info = None
        web = (run.values or {}).get("web") if (run.values or {}).get("media") == "reference" else None
        if hub is not None and web is None:  # a reference photo from the web was made on no frame of this slide
            info = (getattr(hub, "infos", {}) or {}).get(ctx.frame_id) if ctx.frame_id is not None else None
            info = info or getattr(hub, "latest_info", None)
        capture_sources = (run.values or {}).get("captureSources")
        input_resources = (run.values or {}).get("inputResources") if run.tool == "run_capability" else None
        source = None
        if isinstance(web, dict):
            source = {"frame": None, "seq": None, "boot": None, "web": copy.deepcopy(web)}
            description = (run.values or {}).get("description")
            if isinstance(description, str) and description:
                source["description"] = description[:600]
        elif isinstance(capture_sources, list):
            # Multi-capture outputs cannot be attributed to the unrelated current request frame.
            source = {"frame": None, "seq": None, "boot": None, "captures": capture_sources[:8]}
        elif isinstance(input_resources, list):
            source = {"frame": None, "seq": None, "boot": None, "resources": copy.deepcopy(input_resources[:8])}
        # What made it, kept with it (partner §1f): each execution's own facts and the settings the run read,
        # so its provenance never rests on the interaction log or the runner's memory alone.
        made_by = []
        runner = getattr(hub, "runner", None) if hub is not None else None
        for eid in run.executions[:8]:
            record = runner.get(eid) if runner is not None and hasattr(runner, "get") else None
            if record is not None:
                made_by.append(record.made_by())
        settings = getattr(hub, "settings", None) if hub is not None else None
        read = {}
        for group in run.reads:
            try:
                read[group] = getattr(settings, group)
            except Exception:
                continue
        if prepared is not None:
            prepared.consumed = True
            prepared.reservation.release()
            entry = {**prepared.entry, "summary": str(run.summary or "")[:SUMMARY_MAX],
                     "executions": list(run.executions)[:8], "madeBy": made_by, "settings": read}
            # Essential source metadata already persisted before display; this enrichment is
            # optional. A failed journal enrichment cannot remove the prepared originals.
            try:
                if self.day.sid == session_of(entry, entry["day"]) and self.day.current(entry.get("dayGeneration")):
                    self.day.record_result(entry)
            except (OSError, ValueError):
                pass  # the required source record was already saved strictly, before success
            key = id(run)
            self._consumed[key] = (weakref.ref(run, lambda _: self._consumed.pop(key, None)), entry)
        else:
            entry = self.add(run.tool, run.args, run.summary, values=run.values or None, pictures=run.pictures,
                             picture_bytes=run.picture_bytes,
                             retaining=retaining,
                             executions=run.executions, info=info, thread=self._thread_of(getattr(ctx, "asked", None)),
                             request=ctx.question, made_by=made_by, settings=read, source=source,
                             made_with=_made_with(hub, run.executions),
                             session=self._session_of_asked(getattr(ctx, "asked", None)),
                             actor=getattr(run, "actor", None),
                             **({"web": web} if isinstance(web, dict) else {}))
        # Its address in the index: (session, id), whatever date folder its pictures are in.
        address = session_of(entry, entry["day"])
        run.resource = {"id": entry["id"], "day": address, "title": entry["title"],
                        "version": None, "picture": 0 if entry.get("pictures") else None,
                        "kind": "Reference photo" if web else "Saved result"}
        key = id(run)
        self._consumed[key] = (weakref.ref(run, lambda _: self._consumed.pop(key, None)), entry)
        from .brain.registry import record_made, record_output
        record_made(ctx, entry["id"])
        # Publish the retained original address, never a guessed next result ID.
        for picture in range(min(4, len(entry.get("pictures", [])))):
            record_output(ctx, {"kind": "source", "source": {
                "day": address, "id": entry["id"], "version": None, "picture": picture}})
        return entry

    def _session(self) -> str:
        """The session open now (the calendar date with no session store: a test's, a bench's)."""
        return getattr(self.day, "sid", None) or self._day_name()

    def _session_of_asked(self, asked) -> str | None:
        """The session a request was asked in (ADR 0017), else None (the one open)."""
        return getattr(asked, "session", None) or None

    def _thread_of(self, asked) -> str | None:
        """The conversation a result belongs to: the one it was asked in. A session's conversations keep their ids
        for good (ADR 0017), so a request with its session is always filed under its own; one from before sessions
        only while that conversation is still the one loaded (ids started again each day; ADR 0001). Otherwise none:
        it is filed under the session."""
        if asked is None or not getattr(asked, "thread", None):
            return None
        if getattr(asked, "session", None):
            return asked.thread
        current = getattr(self.day, "current", None)
        if current is not None and not current(getattr(asked, "routing_generation", getattr(asked, "generation", None))):
            return None
        return asked.thread

    def add(self, tool: str, args: dict | None, summary: str, *, values=None, pictures=(), executions=(),
            info=None, thread: str | None = None, request: str = "", made_by=None, settings=None,
            picture_bytes=(), made_for: str | None = None, web: dict | None = None, source: dict | None = None,
            components: tuple[RetainedComponent, ...] = (), durable_guard: Callable[[], None] | None = None,
            session: str | None = None, made_with: list[dict] | None = None, retaining: bool = False,
            actor: str | None = None) -> dict:
        """`pictures`: magnifier keys of pictures the run showed; `picture_bytes`: pictures made for no screen (a
        researcher's, research/hands.py). `made_for`: who it was made for when not the user's request ("Researcher
        2"), in its own field and its title, so no reader or filter takes it for the user's. `web`: where a
        reference photo from the web came from (site, page, title, licence, author), so it is kept and shown as one
        (media.REFERENCE) and never as the camera's. `session`: the session it was asked in (default: the one open);
        it is filed in that session's journal, its pictures in today's folder. `actor`: who started the run that made
        it ("you", "assistant"; ticket 66, S2), kept as its "actor"."""
        from .brain.registry import ToolFailed

        durable = bool(components)
        if durable and (len(components) != 2 or durable_guard is None or
                        self.root is None or getattr(self.day, "journal", None) is None):
            raise ToolFailed("the comparison needs a guarded writable source store", next="use the app's local result store")
        if durable_guard is not None:
            durable_guard()
        day = self._day_name()
        session = session or self._session()
        day_generation = getattr(self.day, "generation", None)
        journal = getattr(self.day, "journal", None) if durable else None
        if durable and session != getattr(self.day, "sid", session):
            from .day.ids import owner  # the comparison's session's own journal (never the one open now)

            journal = getattr(owner(self.day, session), "journal", journal)
        self.root_for(session, write=True)
        written = self.root is None
        with self._lock:
            rid = self._new_id(session)
            if self.root is not None:  # the full values, and the id taken for good (a day started afresh keeps it)
                initial = ({"id": rid, "values": None, "state": "reserved"} if durable else
                           {"id": rid, "values": values, **({"madeWith": list(made_with)} if made_with else {})})
                written = self._write(day, f"{rid}.json", json.dumps(initial, default=str).encode(),
                                      durable=durable, session=session)
                if durable and not written:
                    raise ToolFailed("the comparison metadata could not be saved", next="check local storage and try again")
        at = time.time()
        files, writes = [], []
        made = [self.pictures(key) for key in list(pictures)[:PICTURES_MAX]] + list(picture_bytes)
        if durable and (len(components) != 2 or len(made) != 3 or any(not data for data in made)):
            raise ToolFailed("the comparison needs its composite and two clean originals", next=None)  # no next: missing producer bytes cannot be replaced by another source
        for n, data in enumerate(made[:PICTURES_MAX]):
            if data and self.root is not None:
                # Native original bytes and their digest are immutable evidence. Portable copies are an explicit
                # admitted worker export; sharing payload failures cannot prevent this result from being retained.
                extension = "png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "jpg"
                filename = f"{rid}-{n}.{extension}"
                files.append(filename)
                writes.append((filename, data))
        if writes and not durable:  # comparisons already run on a worker and must finish saving first
            with self._lock:
                for name, data in writes:
                    self._pending[self._file_key(day, name, session)] = data
                    self._picture_writes[self._file_key(day, name, session)] = threading.Event()

            def finished(name: str) -> None:
                with self._lock:
                    event = self._picture_writes.pop(self._file_key(day, name, session), None)
                    if event is not None:
                        event.set()

            def write_all() -> None:
                try:
                    for name, data in writes:
                        if self._write(day, name, data, session=session):  # a write that fails keeps its picture readable from memory
                            self._pending.pop(self._file_key(day, name, session), None)
                        finished(name)
                except (OSError, ValueError):
                    pass  # retained pending bytes make confirmation fail honestly
                finally:
                    # Failure wakes consumers too; pending bytes remain display-only.
                    for name, _ in writes:
                        finished(name)

            threading.Thread(target=write_all, daemon=True).start()
        named = (_capability_title(args, values) if tool == "run_capability" else None) or RESULT_TITLES.get(
            tool, tool.replace('_', ' ').capitalize())
        named += f", for {made_for}" if made_for else ""
        table_descriptor = _table_descriptor(values)
        entry = {"id": rid, "tool": tool, "title": f"{named} · {hhmm(at)}", **({"for": made_for} if made_for else {}),
                 **({"actor": actor} if actor and not made_for else {}),
                 "args": _capped({k: v for k, v in (args or {}).items() if v not in (None, "")}, ARGS_MAX),
                 "summary": str(summary or "")[:SUMMARY_MAX], "at": round(at, 3), "day": day, "session": session,
                 "request": str(request or "")[:200],
                 "source": source if source is not None else {"frame": getattr(info, "id", None), "seq": getattr(info, "seq", None),
                            "boot": getattr(info, "boot", None) or None},
                 "executions": list(executions)[:8], "madeBy": list(made_by or [])[:8], "settings": dict(settings or {}),
                 "values": _capped(preview(values), PREVIEW_MAX) if values else None,
                 "full": bool(values) and self.root is not None, "pictures": files, "thread": thread,
                 **({'tableDescriptor': table_descriptor} if table_descriptor else {}),
                 # Each kept picture's sha256 as it was made: a reopen reads it only while it is still those bytes.
                 **({"digests": [hashlib.sha256(data).hexdigest() for _name, data in writes]} if writes else {}),
                 **({"madeWith": list(made_with)} if made_with else {}),
                 **({"media": "reference", "web": dict(web)} if web else {})}
        if retaining and not durable:
            entry['state'] = 'saving' if written else 'failed'
            if not written:
                entry['summary'] = 'The result metadata could not be saved. Check local storage and rerun this tool.'
        if durable:
            from .capture import RUN
            from .presentations import SourceRef
            import io
            from PIL import Image

            with Image.open(io.BytesIO(made[0])) as composite:
                composite_size = list(composite.size)  # header only; composition already validated pixels

            children = []
            for n, component in enumerate(components, 1):
                facts = component.facts()
                child = {"key": f"{day}/{rid}/picture/{n}", "kind": "saved-image", "media": "capture",
                         "availability": "retained", "digest": hashlib.sha256(component.jpeg).hexdigest(),
                         "identity": {"resource": {"day": day, "id": rid, "version": None, "picture": n}},
                         "provenance": {"operation": tool, "producerRun": RUN, **facts}}
                children.append(SourceRef.from_wire(child).to_wire())
            entry["source"] = SourceRef.from_wire({**(source or {}), "key": f"{day}/{rid}/picture/0",
                "kind": "composite", "media": "capture", "availability": "retained",
                "digest": hashlib.sha256(made[0]).hexdigest(),
                "identity": {"resource": {"day": day, "id": rid, "version": None, "picture": 0}},
                "provenance": {"operation": tool, "producerRun": RUN, "displayScale": "same camera-pixel scale",
                               "quality": 90, "componentsInOrder": True, "naturalSize": composite_size,
                               "requestId": (source or {}).get("requestId"), "step": (source or {}).get("step")},
                "components": children}).to_wire()
            entry.update(state="saved", dayGeneration=day_generation)
            try:
                for name, data in writes:
                    durable_guard()
                    if not self._write(day, name, data, durable=True, session=session):
                        raise ToolFailed("a comparison original could not be saved", next="check local storage and try again")
                durable_guard()
                metadata = json.dumps({"id": rid, "values": values, "source": entry["source"],
                                       **({"madeWith": list(made_with)} if made_with else {})}, allow_nan=False).encode()
                if len(metadata) > 128 * 1024 or not self._write(day, f"{rid}.json", metadata, durable=True, session=session):
                    raise ToolFailed("the comparison source metadata could not be saved", next="check local storage and try again")
                durable_guard()
                # Persist without Day.record_result's tolerant I/O or firing application listeners
                # on this worker. from_run publishes this same record on the event loop later.
                journal.append({"type": "result", **entry})
                with journal.path.open("rb") as persisted:
                    os.fsync(persisted.fileno())
                durable_guard()
                return entry
            except BaseException:
                self._discarded.add(self._file_key(day, rid, session))
                try:
                    self._discard_files(entry)
                finally:
                    raise
        if self.day is not None and hasattr(self.day, "record_result"):
            if retaining:
                try:
                    self.day.record_result(entry, strict=True)
                except (OSError, ValueError):
                    entry.update(state='failed', summary='The result journal could not be saved. Check local storage and rerun this tool.')
                    self.day.record_result(entry)
            else:
                self.day.record_result(entry)
        else:
            self._mine[rid] = {**entry, "values": values}  # in memory (no day store): the values as they are
        if not retaining:
            self._notify_saved(entry)
        return entry

    def _notify_saved(self, entry: dict) -> None:
        for hook in list(self.on_saved):
            try:
                hook(entry)
            except Exception:
                pass  # the result stands whatever happens to what follows it

    def retention_status(self, entry: dict) -> tuple[str, str]:
        """Reconstruct an interrupted save from owned writes and retained files.

        Old records keep their interpretation. This is a storage-status check;
        scientific consumers still verify original digests and complete metadata.
        """
        state, summary = entry.get('state') or 'done', entry.get('summary') or ''
        if state != 'saving':
            return state, summary
        date, names = entry.get('day'), entry.get('pictures') or []
        session = session_of(entry, date)
        with self._lock:
            if any(self._file_key(date, name, session) in self._picture_writes for name in names):
                return 'saving', 'Saving the original result. It is not yet confirmed as retained.'
            pending = any(self._file_key(date, name, session) in self._pending for name in names)
        valid = (self.root is not None and isinstance(date, str)
                 and re.fullmatch(r'\d{4}-\d{2}-\d{2}', date)
                 and isinstance(entry.get('id'), str) and ENTRY_ID.fullmatch(entry['id'])
                 and all(isinstance(name, str) and '/' not in name and '\\' not in name
                                   and name not in ('.', '..') for name in names))
        if valid and not pending:
            try:
                paths = [self.folder_for(date, session) / name for name in [*names, entry['id'] + '.json']]
                if all(path.is_file() and not path.is_symlink() for path in paths):
                    return 'done', summary
            except OSError:
                pass
        return 'failed', 'The result could not be saved. Check local storage and rerun this tool.'

    def wait_for_picture(self, day: str, name: str, *, timeout: float = 30,
                         cancelled: threading.Event | None = None, session: str | None = None) -> None:
        """Off-loop admission waits only for this original, never another result's I/O.

        Completion permits a disk read, not trust in preview bytes. The caller still
        verifies the retained digest, metadata and revision after this wait.
        """
        with self._lock:
            event = self._picture_writes.get(self._file_key(day, name, session))
        deadline = time.monotonic() + timeout
        while event is not None and not event.is_set():
            if cancelled is not None and cancelled.is_set():
                raise asyncio.CancelledError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError('This result is still being saved. Retry after it has been retained.')
            event.wait(min(.05, remaining))
        if cancelled is not None and cancelled.is_set():
            raise asyncio.CancelledError()
        if self._file_key(day, name, session) in self._pending:
            raise ValueError('This original could not be saved. Check local storage and rerun its producer.')

    def table_candidate(self, entry: dict, session: str, *, discover: bool = False) -> bool:
        """Route an owned result without mistaking its truncated preview for its type.

        New journals retain a small hint. Ordinary synchronous metadata lookups
        use only that hint, previews and cached routing; an off-loop caller opts
        into bounded legacy discovery. No rows are added to the index. The table
        reader still checks its producer digest before showing values or CSV.
        """
        if session_of(entry, entry.get('day', '')) != session:
            return False
        hint = entry.get('tableDescriptor')
        if (isinstance(hint, dict) and set(hint) == {'format', 'sha256'}
                and type(hint['format']) is int and hint['format'] == 1
                and isinstance(hint['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', hint['sha256'])):
            return True
        if keeps_table(entry.get('values')):
            return True
        preview = entry.get('values')
        if (not entry.get('full') or not isinstance(preview, dict) or set(preview) != {'cut'}
                or self.root is None):
            return False
        date, rid = entry.get('day'), entry.get('id')
        if (not isinstance(date, str) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) is None
                or not isinstance(rid, str) or re.fullmatch(r'd\d+', rid) is None):
            return False
        if not discover:
            with self._lock:
                # Routing only: a later full read independently verifies values.
                return next((value for key, value in reversed(self._table_candidates.items())
                             if key[:3] == (session, date, rid)), False)
        try:
            import stat
            path = self.folder_for(date, session) / f'{rid}.json'
            if path.parent.is_symlink():
                return False
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'rb') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_size > TABLE_DISCOVERY_BYTES:
                    return False
                key = (session, date, rid, info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
                with self._lock:
                    if key in self._table_candidates:
                        self._table_candidates.move_to_end(key)
                        return self._table_candidates[key]
                body = stream.read(TABLE_DISCOVERY_BYTES + 1)
            if len(body) > TABLE_DISCOVERY_BYTES:
                return False
            record = json.loads(body)
            found = (isinstance(record, dict) and record.get('id') == rid
                     and keeps_table(record.get('values')))
            with self._lock:
                self._table_candidates[key] = found
                self._table_candidates.move_to_end(key)
                while len(self._table_candidates) > TABLE_DISCOVERY_CACHE:
                    self._table_candidates.popitem(last=False)
            return found
        except (OSError, ValueError, RecursionError):
            return False

    def values(self, rid: str, day: str | None = None) -> Any:
        """A result's full values (its file, in its own date folder), else what the journal keeps. `day`: its
        session (None: the one open)."""
        entry = self.get(rid, day)
        if entry is None:
            return None
        if entry.get("full") and self.root is not None:
            folder = self.folder_for(entry.get("day") or self._day_name(), session_of(entry, entry.get("day") or self._session()))
            try:
                return json.loads((folder / f"{rid}.json").read_text())["values"]
            except (OSError, ValueError, KeyError):
                pass
        return entry.get("values")

    def _day_name(self) -> str:
        """The date folder new pictures go to: the session's date (day/store.py `date`)."""
        return getattr(self.day, "date", None) or time.strftime("%Y-%m-%d")

    def _known(self) -> dict[str, dict]:
        """The session open now's results (every date it touched), from its journal as loaded."""
        entries = dict(getattr(self.day, "results", None) or {}) if self.day is not None else dict(self._mine)
        return {rid: entry for rid, entry in entries.items() if not self._is_discarded(entry)}

    def _is_discarded(self, entry) -> bool:
        day, rid = entry.get("day", self._day_name()), entry["id"]
        session = session_of(entry, day)
        key = self._file_key(day, rid, session)
        if entry.get("state") == "discarded" or key in self._discarded:
            return True
        if key not in self._retention_checked and self.root is not None and (entry.get("source") or {}).get("components"):
            try:
                metadata = read_picture_bytes(self.folder_for(day, session) / f"{rid}.json", 128 * 1024)
                if metadata and json.loads(metadata).get("state") == "discarded":
                    self._discarded.add(self._file_key(day, rid, session))
                    return True
            except (OSError, ValueError):
                pass  # unavailable source metadata does not make the named artifact disappear
            self._retention_checked.add(key)
        return False

    def _new_id(self, session: str | None = None) -> str:
        """"d1", "d2"...: new for the session it is made in on any of its days and for today's folder (day/ids.py),
        never one the session's journal has, nor one whose picture or values are on disk."""
        from .day.ids import floor, owner

        used = set(self._known())
        date = self._day_name()
        store = owner(self.day, session)
        key = (getattr(store, "sid", None), date)
        if key not in self._floors:
            self._floors[key] = floor(store, "d", date)
        folder = self.folder_for(date, session) if self.root is not None else None
        n = max([self._floors[key], self._given] + [int(i[1:]) for i in used if i[1:].isdigit()]) + 1
        while f"d{n}" in used or (folder is not None and (any(folder.glob(f"d{n}-*")) or (folder / f"d{n}.json").exists())):
            n += 1
        self._given = n
        return f"d{n}"

    def _write(self, day: str, name: str, data: bytes, *, durable: bool = False, session: str | None = None) -> bool:
        folder = self.folder_for(day, session, write=True)
        try:
            folder.mkdir(parents=True, exist_ok=True)
            tmp = folder / f".{name}.{os.getpid()}.tmp"
            with tmp.open("wb") as stream:
                stream.write(data)
                if durable:
                    stream.flush()
                    os.fsync(stream.fileno())
            os.replace(tmp, folder / name)
            if durable:
                directory = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            return True
        except OSError:
            try:
                tmp.unlink(missing_ok=True)
            except (OSError, UnboundLocalError):
                pass
            return False

    # --- reading --------------------------------------------------------------------------------
    def entries(self, day: str | None = None) -> list[dict]:
        """A session's results (`day`: its id; None, the one open), from every date it touched."""
        day = day or self._session()
        if day == self._session():
            return list(self._known().values())
        return self._journal_of(day)

    def get(self, rid: str, day: str | None = None) -> dict | None:
        return next((e for e in self.entries(day) if e.get("id") == rid), None)

    def _journal_of(self, day: str) -> list[dict]:
        """Another session's results, read from its journal (read-only)."""
        return self._read_journal(day)[0]

    def metadata(self, day: str) -> tuple[list[dict], list[str]]:
        """Current producer records or retained journal records, with unreadable metadata stated.

        The journal audit reads no result values or pictures. Current in-memory
        records still include successful producers whose optional journal write failed.
        """
        retained, unreadable = self._read_journal(day)
        return (self.entries(day) if day == self._session() else retained), unreadable

    def _read_journal(self, day: str) -> tuple[list[dict], list[str]]:
        """A session's results as its journal (`sessions/<sid>/journal.jsonl`) keeps them, read-only."""
        root = getattr(getattr(self.day, "journal", None), "root", None)
        if root is None or not sids.is_sid(day):
            return [], []
        if hasattr(self.day, 'root_for'):
            root = self.day.root_for(day)
        out: dict[str, dict] = {}
        invalid = 0
        try:
            with (Path(root) / day / "journal.jsonl").open(encoding="utf-8") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                    except ValueError:
                        invalid += 1
                        continue
                    if not isinstance(event, dict):
                        invalid += 1
                        continue
                    if not isinstance(event.get("type"), str):
                        invalid += 1
                        continue
                    if event["type"] == "result":
                        rid = event.get("id")
                        if not isinstance(rid, str) or not ENTRY_ID.fullmatch(rid):
                            invalid += 1
                            continue
                        out[rid] = {k: v for k, v in event.items() if k != "type"}
        except (OSError, UnicodeError) as exc:
            return [], [f"results journal ({type(exc).__name__})"]
        unreadable = [f"results journal ({invalid} unreadable records)"] if invalid else []
        return [entry for entry in out.values() if not self._is_discarded(entry)], unreadable

    def picture(self, rid: str, n: int = 0, day: str | None = None, max_bytes: int | None = None) -> bytes | None:
        """A result's picture (`day`: its session), read from the date folder its entry names."""
        entry = self.get(rid, day)
        if entry is None or self.root is None or not (0 <= n < len(entry.get("pictures") or [])):
            return None
        folder_day = entry.get("day") or self._day_name()
        pending = self._pending.get(self._file_key(folder_day, entry["pictures"][n], session_of(entry, folder_day)))
        if pending is not None:
            data = pending if max_bytes is None or len(pending) <= max_bytes else None
        else:
            try:
                data = read_picture_bytes(self.folder_for(folder_day, session_of(entry, folder_day)) / entry["pictures"][n], max_bytes)
            except OSError:
                return None
        digests = entry.get("digests") or []
        if data is not None and n < len(digests) and hashlib.sha256(data).hexdigest() != digests[n]:
            return None  # not the picture that was kept: never shown as if it were
        return data

    def export_picture(self, rid: str, n: int, day: str, memory) -> dict:
        """A portable copy of immutable original evidence, prepared on the caller's worker."""
        from .result_entries import export_image
        entry = self.get(rid, day)
        if entry is None or self.root is None or not 0 <= n < len(entry.get("pictures") or []):
            raise ValueError("that original result picture is no longer available")
        folder_day = entry.get("day") or self._day_name()
        filename = entry["pictures"][n]
        pending = self._pending.get(self._file_key(folder_day, filename, session_of(entry, folder_day)))
        maximum = len(pending) if pending is not None else (self.folder_for(folder_day, session_of(entry, folder_day)) / filename).stat().st_size
        return export_image(lambda limit: self.picture(rid, n, day, max_bytes=limit), maximum,
                            entry.get("madeWith") or [], memory)


def _made_with(hub, executions) -> list[dict]:
    """The catalog entries a run's executions really ran (made_with.of_executions), for its kept pictures."""
    if hub is None or not executions:
        return []
    try:
        from .made_with import of_executions

        return of_executions(hub, executions)
    except Exception:  # noqa: BLE001 - a result is kept whatever happens to what names its makers
        return []


def read_picture_bytes(path: Path, max_bytes: int | None = None) -> bytes | None:
    if max_bytes is None:
        return path.read_bytes()
    with path.open("rb") as stream:
        data = stream.read(max_bytes + 1)
    return data if len(data) <= max_bytes else None


class Unread(Exception):
    """A store that can't be read for the day asked (said in the summary as "couldn't read: ...")."""


# --- the index -------------------------------------------------------------------------------------------
def named_words(kept) -> str:
    """Who named a thing kept today, in the index's own voice, read by the user, the brain and researchers
    alike (seen.named_by speaks to the naming model as "you": never shown here; acceptance flow 11)."""
    if kept.status == "confirmed":
        return "named by the user"
    return "named by the app" + (", not sure" if kept.status == "unsure" else "") + (" from a click" if kept.source == "click" else "")


class Index:
    """Everything the session made, from the stores that keep it (never copied), read one way."""

    def __init__(self, hub, *, results: Results | None = None, research=None, snapshots: Path | None = None,
                 seen=None, taught=None, scale=None, datasets: Path | None = None, recordings: Path | None = None,
                 day=None, registry=None, bookmarks=None, identity=None, memory=None, registrations=None,
                 projects=None) -> None:
        self.hub = hub
        self.projects = projects  # user-owned scientific state, independent of installed packages
        self.registrations = registrations  # registrations.Registrations: volumes added and jobs run on them (E5)
        self.results = results or Results(None)
        self.research = research
        self.snapshots = Path(snapshots) if snapshots is not None else None
        self.seen = seen
        self.taught = taught
        self.scale = scale
        self.datasets = Path(datasets) if datasets is not None else None
        self.recordings = Path(recordings) if recordings is not None else None
        self.day = day
        self.registry = registry  # what could be made here now: the tools there are
        self.bookmarks = bookmarks  # bookmarks.Bookmarks (M3)
        self.identity = identity  # identity.Identity (K2): things followed over time
        self.memory = memory  # day/memory.Memory: facts settled today, with their sources (K3)
        self._datasets_cache: tuple[float, list] | None = None
        self._local = threading.local()  # one collect's shared reads (collect)

    def today(self) -> str:
        """Today's calendar date (day/store.py `date`): where new files go. What the index reads by default is the
        session open now (`session`)."""
        return getattr(self.day, "date", None) or time.strftime("%Y-%m-%d")

    def session(self) -> str:
        """The session open now: what every reader's `day` means when it gives none (the calendar date with no
        session store: a test's, a bench's, where a day was a session)."""
        return getattr(self.day, "sid", None) or self.today()

    def is_open(self, sid: str | None) -> bool:
        """Whether `sid` (None: the default) is the session open now, read from memory."""
        return not sid or sid == self.session()

    def dates(self, sid: str | None = None) -> list[str]:
        """The calendar dates a session touched, oldest first (its date folders): the open one's from its store,
        another's from the session list (catalogue.py), always with the date it began on."""
        sid = sid or self.session()
        if self.day is not None and sid == getattr(self.day, "sid", None):
            return sorted(set(getattr(self.day, "days", []) or []) | {self.today()})
        dates = {sids.date_of(sid)} if sids.is_sid(sid) else set()
        catalogue = getattr(self.day, "catalogue", None)
        info = catalogue.get(sid) if catalogue is not None and sids.is_sid(sid) else None
        if info is not None:
            dates |= set(info.days)
        return sorted(dates)

    def _journal(self, sid: str) -> dict:
        """What a session's journal files of its work (read-only; the open session's from its store): evidence and
        tracks saved ({id: the latest event}), annotations ({id: [revisions]}) and what its "made" events claim
        ({(kind, id): date}, products or not: a Record dataset is a source, not a product, and still its; catalogue
        `Info.claims`)."""
        store = self.day
        catalogue = getattr(store, "catalogue", None)
        info = catalogue.get(sid) if catalogue is not None and sids.is_sid(sid) else None
        made = dict(info.claims) if info is not None else {}
        if store is not None and sid == getattr(store, "sid", None):
            return {"evidence": dict(getattr(store, "evidence", {}) or {}), "tracks": dict(getattr(store, "tracks", {}) or {}),
                    "annotations": dict(getattr(store, "annotations", {}) or {}),
                    "datatables": dict(getattr(store, "datatables", {}) or {}),
                    "table_derivatives": dict(getattr(store, "table_derivatives", {}) or {}), "made": made}
        root = store.root_for(sid) if store is not None and hasattr(store, 'root_for') and sids.is_sid(sid) else \
            getattr(getattr(store, "journal", None), "root", None)
        found = _journal_records(Path(root), sid) if root is not None and sids.is_sid(sid) else \
            {"evidence": {}, "tracks": {}, "annotations": {}, "datatables": {}, "table_derivatives": {}}
        return {**found, "made": made}

    def _made(self, kind: str, sid: str) -> set[str]:
        """The ids of `kind` a session made by its journal's own word ("made" events: datasets, recordings...)."""
        return {pid for (k, pid) in self._journal(sid)["made"] if k == kind}

    def _claimed(self, kind: str) -> set[str]:
        """The ids of `kind` any session's journal says it made: one none claims is its date's old day session's."""
        catalogue = getattr(self.day, "catalogue", None)
        if catalogue is None:
            return set()
        return {pid for info in catalogue.list() for (k, pid) in info.claims if k == kind}

    # --- the one read interface ------------------------------------------------------------------
    def _providers(self):
        """(name, provider, the kinds it makes): a reader asking for some kinds reads only their stores (K3 review SF1:
        outcomes read the log and the evidence manifests, so they are read only when asked for)."""
        return (("cell outlines", self._populations, ("population",)), ("motion tracks", self._tracks, ("tracks",)),
                ("results", self._results, ("result",)), ("your notes", self._annotations, ("annotation",)),
                ("dataset tables", self._datatables, ("dataset-table",)),
                ("table analyses and graphs", self._table_derivatives, ("analysis", "graph")),
                ("researchers' work", self._research, ("research",)),
                ("researchers' lessons, pictures, tables and sources", self._research_work, tuple(sorted(ASKED_FOR_ONLY))),
                ("snapshots", self._snapshots, ("snapshot",)),
                ("things named", self._named, ("named-outline",)), ("labels taught", self._taught, ("taught-label",)),
                ("the scale", self._scale, ("calibration",)), ("datasets", self._datasets, ("dataset",)),
                ("recordings", self._recordings, ("recording",)), ("sources", self._sources, ("asset",)),
                ("footage held for researchers", self._held, ("clip",)),
                ("scheduled camera clips", self._acquisitions, ("clip",)),
                ("bookmarks", self._bookmarks, ("bookmark",)), ("things followed", self._individuals, ("individual",)),
                ("outcomes", self._outcomes, ("outcome",)), ("facts settled", self._facts, ("fact",)),
                ("volumes and registrations", self._registrations, ("volume", "job")))

    def collect(self, day: str | None = None, kinds=None, *, include_reads: bool = False,
                discover_tables: bool = False) -> tuple[list[Entry], list[str]]:
        """Every entry of session `day` (None: the one open), from every date it touched (of `kinds` only, when
        given), and the stores that could not be read, each with why (said, never hidden: a summary that leaves a
        store out says so; ADR 0006). Off-loop presentation callers may opt into
        legacy table discovery; default metadata lookups never load table rows. Each entry says who made it
        (`maker`, maker_of)."""
        day = day or self.session()
        local = self._local
        outer = getattr(local, "pass_", None) is None
        if outer:  # one call: its nested reads share what they read (the researchers; the read badges' collects)
            local.pass_ = {"researchers": {}, "collects": None}
        try:
            memo = local.pass_["collects"]
            key = (day, tuple(sorted(kinds)) if kinds is not None else None, bool(discover_tables))
            if memo is not None and not include_reads and key in memo:
                entries, unreadable = memo[key]
                return list(entries), list(unreadable)
            out, unreadable = self._collect(day, kinds, discover_tables)
            if memo is not None and not include_reads:
                memo[key] = (list(out), list(unreadable))
            if include_reads and self.research is not None:
                self._read_badges(day, out, unreadable)
            return out, unreadable
        finally:
            if outer:
                local.pass_ = None

    @contextmanager
    def one_pass(self):
        """Reads inside share one reading of each store: a reader that needs several views of one moment (project_state:
        the entries and the researchers) reads each store once, as one collect's nested reads do."""
        local = self._local
        if getattr(local, "pass_", None) is not None:
            yield
            return
        local.pass_ = {"researchers": {}, "collects": {}}
        try:
            yield
        finally:
            local.pass_ = None

    def researchers_of(self, sid: str) -> list:
        """Every researcher of session `sid` (`_researchers`): shared with a collect inside one pass."""
        return self._researchers(sid)

    def _collect(self, day: str, kinds, discover_tables: bool) -> tuple[list[Entry], list[str]]:
        out: list[Entry] = []
        unreadable: list[str] = []
        if self.projects is not None and (kinds is None or "project" in kinds):
            try:
                projects, problems = self.projects.scan(day)
                out.extend(self.project_entry(item) for item in projects)
                unreadable.extend(f"scientific project {p['id']} ({p['reason']})" for p in problems)
            except Exception:
                unreadable.append("scientific projects (unavailable)")
        for name, provider, makes in self._providers():
            if kinds is not None and not set(makes) & set(kinds):
                continue
            try:
                out.extend(provider(day, discover_tables=True) if name == 'results' and discover_tables else provider(day))
            except Exception as exc:  # one store that cannot be read never hides the others
                unreadable.append(f"{name} ({exc})" if isinstance(exc, Unread) else f"{name} ({type(exc).__name__})")
        from .project_material import attach_usage
        unreadable.extend(attach_usage(out, self.projects))
        who = None
        for entry in out:
            made = entry.made_by if isinstance(entry.made_by, dict) else {}
            if who is None and (entry.kind == "research" or made.get("for") or made.get("researcher")):
                try:
                    who = Researchers.of(self._researchers(day))
                except Exception:
                    who = Researchers()  # names unknown: a researcher's own work still says who by its id
            entry.maker = maker_of(entry.kind, entry.id, made, who)
        return sorted(out, key=lambda e: e.at), unreadable

    def _read_badges(self, day: str, out: list[Entry], unreadable: list[str]) -> None:
        """Which researchers read each entry at its current content (`read_by`). The research journal is the authority.
        Listing/presenting a resource never counts as reading it. The documents compared are each built once from one
        reading of the stores (the collects they make are shared within this pass), so a listing costs no more for them."""
        local = self._local
        try:
            sessions = self._researchers(day)
            reads = {}
            for session in sessions:
                for read in getattr(session, "evidence_reads", []):
                    version, at, how = read.get("version"), read.get("at"), read.get("how", "")
                    if (version is not None and (not isinstance(version, int) or isinstance(version, bool))) or _finite_time(at) is None:
                        continue
                    # Immutable results need no integer revision. Their exact document
                    # digest below is authoritative; historical reads without one stay unknown.
                    if how not in ("read", "read page") and not re.fullmatch(r"picture \d+", how):
                        continue
                    reads.setdefault((read.get("id"), version), []).append({
                        "researcher": session.rid, "name": session.name, "at": at, "version": version,
                        "how": how, **({"contentRevision": read["contentRevision"]} if read.get("contentRevision") else {})})
            if not reads:
                return
            local.pass_["collects"] = {}
            try:
                for entry in out:
                    candidates = reads.get((entry.id, entry.version), [])
                    if candidates:
                        # Same canonical document as the paginated HTTP reader. readBy is
                        # excluded: observing a read must never change the resource revision.
                        document = self.document(entry.id, day)
                        digest = hashlib.sha256(json.dumps(document, ensure_ascii=False, sort_keys=True,
                            separators=(',', ':'), allow_nan=False).encode()).hexdigest()
                        entry.read_by = [r for r in candidates if r["how"] in ("read", "read page")
                                         and r.get("contentRevision") == digest]
                    # Picture reads without an exact resource revision remain unknown.
            finally:
                local.pass_["collects"] = None
        except Exception:
            # Do not infer 'unread' from a missing read journal.
            unreadable.append("recorded resource reads (unavailable)")

    def listing(self, day: str | None = None, *, kind: str | None = None, query: str | None = None,
                since: str | None = None, researcher: bool = False) -> dict:
        """`GET /api/resources` (built off the event loop): the session's entries with their `revision`, a digest of the
        whole listing that changes when any entry changes (ticket 66, S7). Given the revision it last had (`since`), an
        unchanged index answers `{day, revision, unchanged: true}` with no entries, so the studio's poll sends and draws
        nothing. `researcher`: a researcher's own reading (the session tool), as it was before ticket 66: no listing
        fields, no revision, and a researcher's sources, tables, charts and other work only when asked for by kind."""
        entries, unreadable = self.collect(day, include_reads=True, discover_tables=True)
        day = day or self.session()
        words = [w for w in re.findall(r"[a-z0-9]+", (query or "").lower()) if len(w) > 1]

        def wanted(e: Entry) -> bool:
            words_of = f"{e.id} {e.kind} {e.title} {e.summary}" + ("" if researcher else f" {e.shown}")  # Data's title too
            return ((not kind or e.kind == kind) and (bool(kind) or not researcher or e.kind not in ASKED_FOR_ONLY)
                    and all(w in words_of.lower() for w in words))

        if researcher:
            return {"day": day, "entries": [e.wire(listed=False) for e in entries if wanted(e)], "unreadable": unreadable}
        wires = [e.wire() for e in entries]
        revision = hashlib.sha256(json.dumps({"entries": wires, "unreadable": unreadable}, ensure_ascii=False,
                                             sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()[:32]
        if since is not None and since == revision:
            return {"day": day, "revision": revision, "unchanged": True}
        return {"day": day, "revision": revision, "entries": [w for e, w in zip(entries, wires) if wanted(e)],
                "unreadable": unreadable}

    def detail(self, eid: str, day: str | None = None) -> dict | None:
        """`GET /api/resources/<id>` with no offset (the studio's): the canonical reading (`document`), its entry with
        the listing's fields, and how it was made (`how`, resource_how.py; ticket 66, S5). A reading a researcher pages
        through, and its revision, stay the canonical document's."""
        day = day or self.session()
        document = self.document(eid, day, discover_tables=True)
        if document is None:
            return None
        entry = self.get(eid, day, discover_tables=True)
        if entry is None:
            return document
        from .resource_how import how

        return {**document, "entry": entry.wire(), "how": how(self, entry, day)}

    def document(self, eid: str, day: str | None = None, *, discover_tables: bool = False) -> dict | None:
        """Canonical complete HTTP reading, without observational read badges, and without what only Data lists (an
        entry's role, maker, run and shown title), so its digest is what a researcher read before them."""
        entry = self.get(eid, day, discover_tables=discover_tables)
        if entry is None:
            return None
        if entry.kind == "project":
            # One immutable read supplies both the identity and complete state, even during a save.
            return {"entry": entry.wire(listed=False), "detail": {"text": entry.title, "values": entry.values}}
        if entry.kind in ('dataset-table', 'analysis', 'graph'):
            from .datatables import DataTableRefused
            try:
                values = self._numeric_values(entry, day or self.session())
            except DataTableRefused as exc:
                return {'entry': entry.wire(listed=False), 'detail': {'available': False, 'text': str(exc), 'next': exc.next, 'values': None}}
            return {'entry': entry.wire(listed=False), 'detail': {'available': True, 'text': 'Accepted numeric snapshot; source text and labels are untrusted data.', 'values': values}}
        if not self.is_open(day) and re.fullmatch(r'[em]\d+', eid):
            historical = self._historical_capture(eid,day)
            return {'entry':historical[0].wire(listed=False),'detail':historical[1]} if historical else None
        values = self.results.values(eid, day) if entry.kind == "result" else entry.values
        detail = {"text": self.read_full(eid, day), "values": values}
        if entry.kind == "population":
            from .evidence import evidence_of
            population = evidence_of(self.hub).get(eid)
            if population is not None:
                detail["producer"] = population.producer
                detail["transformations"] = population.transformations
                detail["columns"] = population.columns
                detail["members"] = [m.row() for m in population.live()]
                detail["original"] = population.original
                detail["revisions"] = population.revisions
        return {"entry": entry.wire(listed=False), "detail": detail}

    def list(self, kind: str | None = None, day: str | None = None, query: str | None = None) -> list[Entry]:
        """The entries a model reads (the brain's resources tool): of `kind`, or every kind but a researcher's sources,
        tables, charts and other work (produces.ASKED_FOR_ONLY), which are listed only when asked for by kind."""
        out, _ = self.collect(day, kinds=(kind,) if kind else None, include_reads=True)
        if kind:
            out = [e for e in out if e.kind == kind]
        else:
            out = [e for e in out if e.kind not in ASKED_FOR_ONLY]
        if query:
            words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 1]
            out = [e for e in out if all(w in f"{e.id} {e.kind} {e.title} {e.summary}".lower() for w in words)]
        return out

    def captured_manifest(self, eid: str, day: str):
        """Read an original evidence owner's manifest, never attach it to the live store. `day`: the session it
        belongs to; its folder is the one its session's journal filed it in (evidence/<date>/<id>), and its manifest
        must say the same id, date and session."""
        if not re.fullmatch(r'[em]\d+', eid or '') or not sids.is_sid(day or ''):
            return None
        from .evidence import evidence_of
        from .motiontracks import tracks_of
        store = evidence_of(self.hub) if eid.startswith('e') else tracks_of(self.hub)
        if store.root is None:
            return None
        root = store.root_for(day).resolve()
        filed = self._journal(day)['evidence' if eid.startswith('e') else 'tracks'].get(eid)
        if filed is None and self.day is None:  # no session store (a test, a bench): a day was a session
            filed = {'path': str(root / day / eid)}
        if filed is None or not filed.get('path'):
            return None
        try:
            from .project_evidence import resolve
            folder = resolve(store, day, filed)
            portable = str(filed['path']).startswith('project:')
            date = folder.parent.parent.name if portable else folder.parent.name
            expected = root / date / eid
            if not sids.is_sid(date) or sids.parse(date)[1] != 1 or (folder.parent if portable else folder) != expected:
                return None
            path = folder / 'manifest.json'
            if path.resolve() != path:
                return None
            raw = read_picture_bytes(path, 4*1024*1024)
            record = json.loads(raw) if raw else None
            if not isinstance(record, dict) or record.get('id') != eid or record.get('day') != date:
                return None
            if session_of(record, date) != day:
                return None  # another session's with the same id in that folder: never this one
            if record.get('schema') != ('evidence/1' if eid.startswith('e') else 'tracks/1'):
                return None
            name = 'capture.jpg' if eid.startswith('e') else 'tracks.jpg'
            if not re.fullmatch(r'[a-f0-9]{64}', str(record.get('files', {}).get(name, ''))):
                return None
            return record, folder
        except (OSError, ValueError, TypeError):
            return None

    def _historical_capture(self, eid: str, day: str):
        retained = self.captured_manifest(eid,day)
        if retained is None:
            return None
        record, _ = retained
        population = eid.startswith('e')
        view = record.get('view') or {}
        values = None if population else {'summary':record.get('summary') or {},'span':record.get('span') or {},
            'scale':record.get('scale') or {},'producer':record.get('producer') or {},
            'tracks':[{ 'id':row['id'], **row['values'], 'segments':row['segments']} for row in record.get('tracks',[])]}
        entry = Entry(eid,'population' if population else 'tracks',record.get('title') or 'Captured evidence',day,
            float(view.get('at') or record.get('madeAt') or 0),state='saved',thread=record.get('thread'),
            version=record.get('revision',0) if population else None,
            source={'frame':record.get('capture'),'seq':view.get('seq'),'boot':view.get('boot') or None,
                    **({'comparisonInput': comparison_input_key(record.get('capture'), view)} if population else {}),
                    **({'sourceImage': view['sourceImage']} if view.get('sourceImage') else {})},
            media='raw' if view.get('sourceImage') else None,
            pictures=[f'/api/resources/{eid}/picture?day={day}'],values=values,
            made_by=record.get('madeBy') or {},placement='on its original captured view',date=record.get('day') or '')
        detail = {'text':'Original retained evidence of another session; not on the live scene.','values':values}
        if population:
            detail.update(producer=record.get('producer') or {}, transformations=record.get('transformations') or {},
                          original=record.get('original'), revisions=record.get('revisions') or [],
                          columns=record.get('columns') or {},members=[row for row in record.get('members',[])
                if row.get('status') not in ('deleted','merged')])
        return entry,detail

    def get(self, eid: str, day: str | None = None, *, discover_tables: bool = False) -> Entry | None:
        """One entry by its id. Words that are no id ("the motion one") are never looked up (a reader resolving the
        user's words asks here first); an outcome's or a fact's id reads only its store, any other id every other."""
        eid = str(eid or "").strip()
        if not ENTRY_ID.fullmatch(eid):
            return None
        if re.fullmatch(r'p[0-9a-f]{32}', eid):
            if self.projects is None:
                return None
            from .scientific_projects import InvalidProject
            try:
                return self.project_entry(self.projects.read(eid, day or self.session()))
            except (OSError, InvalidProject):
                return None
        if not self.is_open(day) and re.fullmatch(r'[em]\d+', eid):
            historical = self._historical_capture(eid,day)
            return self._with_maker(historical[0], day) if historical else None
        if re.fullmatch(r"c\d+", eid):
            return self._with_maker(self._member(eid, day or self.session()), day)
        if WORK_ID.fullmatch(eid):  # a researcher's work: only its provider is read
            entries, _ = self.collect(day, kinds=tuple(sorted(ASKED_FOR_ONLY)), discover_tables=discover_tables)
            found = next((e for e in entries if e.id == eid), None)
            if found is not None:
                return found
        kinds = KINDS_BY_ID.get(eid[:1]) if HASHED_ID.fullmatch(eid) else None
        kinds = kinds or tuple(k for k in KINDS if k not in ("outcome", "fact", "project"))
        entries, _ = self.collect(day, kinds=kinds, discover_tables=discover_tables)
        return next((e for e in entries if e.id == eid), None)

    def _with_maker(self, entry: Entry | None, day: str | None) -> Entry | None:
        """An entry read on its own (a member, another session's saved outlines) says who made it as a listed one does."""
        if entry is not None and entry.maker is None:
            made = entry.made_by if isinstance(entry.made_by, dict) else {}
            who = Researchers.of(self._researchers(day or self.session())) if made.get("for") else None
            entry.maker = maker_of(entry.kind, entry.id, made, who)
        return entry

    @staticmethod
    def _entry_time(entry: Entry) -> float | None:
        if entry.time_known is False:
            return None
        # Other kinds retain their existing zero-as-missing convention.
        if entry.time_known is None and not entry.at:
            return None
        return _finite_time(entry.at)

    def require_original(self, entry: Entry) -> None:
        """Refuse changed selected metadata instead of pairing it with an old source receipt."""
        from .brain.registry import ToolFailed

        current = self.get(entry.id, entry.day)

        def basis(value):
            return (value.id, value.day, value.kind, value.version, value.thread, value.media, value.made_by,
                    self._entry_time(value), value.source, value.pictures, value.values)
        if current is None or basis(current) != basis(entry):
            raise ToolFailed("the selected saved resource changed while it was being read",
                             next="read the original resource again before opening it")

    def chronology(self, entry: Entry) -> dict:
        """Metadata facts for this selected day/kind/producer cohort, never pixel proof."""
        entries, unreadable = self.collect(entry.day, kinds=(entry.kind,))
        producer = entry.made_by.get("tool")
        peers = [e for e in entries if e.kind == entry.kind and e.made_by.get("tool") == producer]
        if entry.kind == "result":
            _, missing = self.results.metadata(entry.day)
            unreadable = list(dict.fromkeys([*unreadable, *missing]))
        known = [(e, self._entry_time(e)) for e in peers]
        known = [(e, at) for e, at in known if at is not None]
        first = min((at for _, at in known), default=None)
        last = max((at for _, at in known), default=None)
        first_count = sum(at == first for _, at in known)
        last_count = sum(at == last for _, at in known)
        selected_at = self._entry_time(entry)
        if entry.kind == "result":
            self.require_original(entry)
        return {"day": entry.day, "kind": entry.kind, "producer": producer, "at": selected_at,
                "total": len(peers), "earlier": sum(at < selected_at for _, at in known) if selected_at is not None else None,
                "sameTime": sum(at == selected_at for _, at in known) if selected_at is not None else None,
                "earliest": heapq.nsmallest(5, (e.id for e, at in known if at == first)),
                "latest": heapq.nsmallest(5, (e.id for e, at in known if at == last)),
                "earliestOmitted": max(0, first_count - 5), "latestOmitted": max(0, last_count - 5),
                "unknownTimes": len(peers) - len(known),
                "complete": bool(producer and not unreadable and len(known) == len(peers)
                                 and selected_at is not None and any(e.id == entry.id for e in peers)),
                "unreadable": unreadable}

    def read(self, eid: str, day: str | None = None, *, offset: int = 0, revision: str | None = None) -> str:
        """Bounded reading with explicit, content-stable continuation; small callers are unchanged."""
        from .brain.registry import ToolFailed
        text = self.read_full(eid, day)
        digest = hashlib.sha256(text.encode()).hexdigest()
        if type(offset) is not int or offset < 0 or offset > len(text) or (offset and not revision):
            raise ToolFailed("continuation needs a valid offset and the original revision", next="read the resource at offset 0")
        if revision is not None and revision != digest:
            raise ToolFailed("the saved reading changed", next="restart reading at offset 0; do not combine different revisions")
        if len(text) <= READ_MAX and offset == 0:
            return text
        from .untrusted import block
        end = min(len(text), offset + READ_MAX)
        page = {"id": eid, "offset": offset, "nextOffset": end if end < len(text) else None,
                "totalChars": len(text), "revision": digest}
        # Each fragment gets its own boundary, including fragments cut inside a
        # researcher's original untrusted wrapper. Source prose cannot close it.
        return "Resource page: " + json.dumps(page) + "\n" + block(text[offset:end], source="saved resource fragment")

    def read_full(self, eid: str, day: str | None = None) -> str:
        """Complete reading for the HTTP reader; model tools use bounded read()."""
        day = day or self.session()
        entry = self.get(eid, day)
        if entry is None:
            return self._outline_facts(eid, day)
        entry = copy.deepcopy(entry)  # source/time metadata must not follow a later producer mutation
        # Who started it (made_by.actor, ticket 66) is Data's, not new words in a model's reading.
        made = ", ".join(f"{k} {v}" for k, v in entry.made_by.items() if v not in (None, "", [], {}) and k != "actor")
        at = self._entry_time(entry)
        when = f"made {time.strftime('%H:%M:%S', time.localtime(at))}" if at is not None else "made at a time not kept"
        head = [entry.line(), when + (f" by {made}" if made else ""),
                "retained resource: " + json.dumps({"id": entry.id, "day": entry.day, "version": entry.version,
                                                    "source": entry.source}, default=str)]
        if entry.kind == "result":
            from .retained_inputs import reference_for
            input_ref = reference_for(self, entry.id, day)
            if input_ref is not None:
                head.append("Exact original input for run_capability.resource (dispatch checks compatibility again): "
                            + json.dumps(input_ref, sort_keys=True))
        if entry.pictures:
            head.append("Picture references (metadata only; not visually inspected): " + "; ".join(entry.pictures[:4])
                        + ". Reopen this existing artifact with present; do not recreate it from its description.")
        body = entry.detail() if entry.detail is not None else ""
        values = self.results.values(entry.id, day) if entry.kind == "result" else entry.values
        if entry.kind in ('dataset-table', 'analysis', 'graph'):
            # Explicit reads only: metadata listing stays cheap. Historical Projects use
            # their journal pin and immutable bytes without rebinding the live session.
            from .datatables import DataTableRefused
            from .brain.registry import ToolFailed
            try:
                values = self._numeric_values(entry, day)
            except DataTableRefused as exc:
                raise ToolFailed(str(exc), next=exc.next) from exc
        chronology = self.chronology(entry)
        head.append("Saved entry chronology (this cohort only): " + json.dumps(chronology, allow_nan=False))
        if entry.kind == "result":
            head.append("Retained byte/source association: selected producer metadata revalidated; "
                        "file byte identity is not independently verified by this read.")
        if values not in (None, {}, []):
            body += ("\n" if body else "") + "values: " + json.dumps(values, default=str)
        if entry.untrusted or entry.kind in ('dataset-table', 'analysis', 'graph'):  # labels and source text remain data, not instructions
            from .untrusted import block

            body = block("\n".join(t for t in (entry.summary, body) if t))
        notes = [a for a in self._annotations(day or self.session()) if (a.source.get("member") == eid or a.source.get("id") == eid)]
        if notes:
            from .untrusted import block
            head.append("User annotations (not scientific confirmation):\n" + block("\n".join(
                f"{a.id}: {a.title}: {a.summary}" for a in notes[-8:]), source="user notes"))
        if entry.kind == "result":
            self.require_original(entry)
        return "\n".join(head + ([body] if body else []))

    def _numeric_values(self, entry: Entry, day: str) -> dict:
        from .datatables import DataTableRefused
        from .table_reading import read_accepted
        service = getattr(self.hub, 'datatables' if entry.kind == 'dataset-table' else 'table_derivatives', None)
        if service is None:
            raise DataTableRefused('saved numeric storage is unavailable', next='restore the local table service')
        ledger = 'datatables' if entry.kind == 'dataset-table' else 'table_derivatives'
        record = next((record for record in self._journal(day).get(ledger, {}).get(entry.id, [])
                       if record['version'] == entry.version), None)
        if record is None:
            raise DataTableRefused('the selected saved numeric revision is unavailable')
        return read_accepted(service.root_for(day), record, day, entry.kind)

    def _outline_facts(self, eid: str, day: str | None) -> str:
        """An outline on the board, read one step behind it (known.py: "how do you know?"). A
        read-only lookup, not an entry: outlines are not listed in the summary."""
        if not self.is_open(day):
            return ""  # outlines are the live view's, of the session open now
        obj = getattr(getattr(self.hub, "scene", None), "objects", {}).get(eid)
        if obj is None:
            return ""
        if getattr(obj, "trial", None) is not None:
            return f"{eid} is a blind test's answer: how it was made is shown after the user's vote."
        from .known import outline_facts

        return outline_facts(self.hub, obj)

    def summary(self, day: str | None = None) -> dict:
        """What there is (Amendment 3, point 4): each kind with how many and the newest, what made them,
        what is shown live and what sits on its own view, what is still running, what could be made now. Of session
        `day` (None: the one open), everything made in it, on every date it touched."""
        day = day or self.session()
        entries, unreadable = self.collect(day)
        # A researcher's sources, tables, charts and other work are its own: listed when asked for by kind (or read by
        # id), never in what a model is told there is (context earns its place; ticket 66).
        entries = [e for e in entries if e.kind not in ASKED_FOR_ONLY]
        kinds = []
        for kind, name in KINDS.items():
            mine = [e for e in entries if e.kind == kind]
            if mine:
                newest = mine[-1]
                kinds.append({"kind": kind, "name": name, "count": len(mine),
                              "newest": {"id": newest.id, "title": newest.title,
                                         "summary": "" if newest.untrusted else newest.summary,
                                         "placement": newest.placement}})
        from .operations import OPERATIONS

        labels = {i.id: i.label for op in OPERATIONS.values() for i in op.implementations}
        made: dict[str, int] = {}
        for e in entries:  # by the model's or the maker's plain name (Amendment 4.1), never a tool's id
            impl = e.made_by.get("implementation")
            who = labels.get(impl, impl) or e.made_by.get("by")
            if who:
                made[str(who)] = made.get(str(who), 0) + 1
        return {"day": day, "kinds": kinds, "madeBy": made,
                "live": [f"{e.title} ({e.placement})" for e in entries if e.placement and e.placement.startswith("shown")],
                "ownView": [e.title for e in entries if e.kind in ("population", "tracks")
                            and not (e.placement or "").startswith("shown")],
                "running": [e.title for e in entries if e.state in ("running", "queued", "starting", "saving", "revising", "recording")],
                "unreadable": unreadable, "couldMake": self.could_make()}

    def summary_text(self, day: str | None = None) -> str:
        s = self.summary(day)
        if not s["kinds"]:
            lines = ["Nothing has been made in this session yet."]
        else:
            lines = ["What has been made in this session:"]
            for k in s["kinds"]:
                newest = k["newest"]
                # A saved Project document's title is its maker's (an imported file's name, an agent's): data, said by
                # the Project's view only inside its untrusted wrapper (project_state.text), never here.
                title = newest["title"] if k["kind"] != "project" else "a saved document, titled in its own words"
                lines.append(f"- {k['name']}: {k['count']} (newest: {title} ({newest['id']})"
                             + (f", {newest['summary']}" if newest["summary"] else "")
                             + (f"; {newest['placement']}" if newest.get("placement") else "") + ")")
        if s["running"]:
            lines.append("Still being made: " + ", ".join(s["running"]))
        if s["live"]:
            lines.append("Shown over the live view: " + ", ".join(s["live"]))
        if s["ownView"]:
            lines.append("On the view they were made on (not drawn live): " + ", ".join(s["ownView"]))
        if s["madeBy"]:
            lines.append("Made with: " + ", ".join(f"{k} ({v})" for k, v in s["madeBy"].items()))
        if s["unreadable"]:
            lines.append("Couldn't read: " + "; ".join(s["unreadable"]))
        if s["couldMake"]:
            lines.append("Could also be made here now: " + "; ".join(s["couldMake"]))
        return "\n".join(lines)

    def could_make(self) -> list[str]:
        """What could be produced here now, from the operations that can run and the tools there are."""
        out = []
        from .operations import Prompt, Unavailable, runner

        for op_id, prompt, words in (("cells", Prompt("field"), "outline every cell"),
                                     ("find-like", Prompt("example", box=(0, 0, 1, 1)), "find every one like an example")):
            try:  # what the operation would run now, by the settings (never a fallback alone, never one switched off)
                from .options import _not_recorded

                with _not_recorded():  # the defaults, read for listing, not a setting this run uses
                    _op, impl, _policy, _sel = runner(self.hub).choose(op_id, prompt)
                out.append(f"{words} ({impl.label})")
            except (Unavailable, Exception):
                continue
        tools = set(getattr(self.registry, "tools", {}) or {})
        for tool, words in (("track_motion", "measure how things move"), ("measure", "measure an outlined thing"),
                            ("enhance", "a clearer picture from real frames"), ("snapshot", "a snapshot of the view"),
                            ("start_researcher", "a researcher's notes or lesson")):
            if tool in tools:
                out.append(words)
        return out

    def _saved_capture_picture(self, eid: str, n: int, day: str, max_bytes: int | None):
        retained = self.captured_manifest(eid,day)
        if retained is None or n != 0:
            return None
        record,folder = retained
        name = 'capture.jpg' if eid.startswith('e') else 'tracks.jpg'
        path = folder/name
        if path.resolve() != path:
            return None
        raw = read_picture_bytes(path,max_bytes)
        return (raw,'image/jpeg') if raw and hashlib.sha256(raw).hexdigest() == record['files'][name] else None

    def picture(self, eid: str, n: int = 0, day: str | None = None, max_bytes: int | None = None) -> tuple[bytes, str] | None:
        """An entry's picture by reference: a population's view, a result's picture, a snapshot, a
        researcher's picture. (bytes, media type) or None. `day`: the session it belongs to (None: the one open)."""
        day = day or self.session()
        if not self.is_open(day) and re.fullmatch(r'[em]\d+', eid):
            return self._saved_capture_picture(eid,n,day,max_bytes)
        if WORK_ID.fullmatch(eid):  # a researcher's picture or a web picture it cited (research_entries)
            return self._work_picture(eid, n, day, max_bytes)
        if re.fullmatch(r"c\d+", eid):
            from .evidence import evidence_of
            from .population import captured_picture
            entry = self._member(eid, day)
            if entry is None or n != 0:
                return None
            population = evidence_of(self.hub).get(entry.source["population"])
            data = captured_picture(self.hub, population, [eid], outlines=False, max_bytes=max_bytes)
            latest = self._member(eid, day)
            if latest is None or latest.version != entry.version or latest.source != entry.source:
                return None  # the child changed while its original pixels were being read
            return (data, "image/jpeg") if data else None
        if eid.startswith("e") and eid[1:].isdigit():
            from .evidence import evidence_of

            store = evidence_of(self.hub)
            population = store.get(eid)
            if population is None or (population.session or day) != day:
                return None  # ids are a session's own: another session's e3 is not this one
            if n != 0:
                return None
            if not getattr(population.capture, 'jpeg', None) and population.path is not None:
                return self._saved_capture_picture(eid,n,day,max_bytes)
            jpeg = store.capture_jpeg(population, max_bytes=max_bytes)
            return (jpeg, "image/jpeg") if jpeg and (max_bytes is None or len(jpeg) <= max_bytes) else None
        if re.fullmatch(r"m\d+", eid):  # motion tracks: the view they were measured on, the tracks drawn
            from .motiontracks import tracks_of

            store = tracks_of(self.hub)
            item = store.get(eid)
            if item is None or (item.session or day) != day:
                return None
            if n != 0:
                return None
            if not item.picture and item.path is not None:
                return self._saved_capture_picture(eid,n,day,max_bytes)
            data = store.picture(item, max_bytes=max_bytes)
            return (data, "image/jpeg") if data and (max_bytes is None or len(data) <= max_bytes) else None
        if re.fullmatch(r"d\d+", eid):
            data = self.results.picture(eid, n, day, max_bytes)
            return (data, "image/png" if data.startswith(b"\x89PNG\r\n\x1a\n") else "image/jpeg") if data else None
        if re.fullmatch(r"b\d+", eid) and self.bookmarks is not None:
            mark = next((m for m in self.bookmarks.session_marks(day, self.dates(day)) if m.get("id") == eid), None)
            data = self.bookmarks.picture(eid, mark.get("day"), max_bytes=max_bytes) if mark is not None else None
            return (data, "image/jpeg") if data else None
        if re.fullmatch(r"[vj]\d+", eid) and self.registrations is not None:
            data = self.registrations.picture(eid, day, max_bytes=max_bytes)
            return (data, "image/png") if data and (max_bytes is None or len(data) <= max_bytes) else None
        if re.fullmatch(r"k\d+", eid):
            from .held import held_of

            clip = held_of(self.hub).clips.get(eid)
            data = held_of(self.hub).picture(eid, n) if clip is not None and self._clip_session(clip) == day else None
            return (data, "image/jpeg") if data else None
        if eid.startswith("acq-") and self.research is not None:
            from .research.acquisition import picture
            entry = next((e for e in self._acquisitions(day) if e.id == eid), None)
            data = picture(self.research.root / "acquisitions", entry.date, eid, n, max_bytes) if entry is not None else None
            return (data, "image/jpeg") if data else None
        if re.fullmatch(r"snapshot-\d{6}-(?:[0-9a-f]{12}-)?f\d+", eid) and self.snapshots is not None and sids.is_sid(day):
            entry = next((e for e in self._snapshots(day) if e.id == eid), None)
            path = self.snapshots / entry.date / f"{eid}.png" if entry is not None else None
            return (read_picture_bytes(path, max_bytes), "image/png") if path is not None and path.is_file() else None
        if self.research is not None:
            session = self.research.get(eid)
            if session is not None and _sitting(session) != day:  # another session's researcher with this id
                session = next((s for s in self._researchers(day) if s.rid == eid), None)
            if session is not None and _sitting(session) == day:
                images = [o for o in session.current_outputs() if o.get("kind") == "image"]
                if 0 <= n < len(images):
                    from .research.files import research_file

                    source = _research_picture_source(session, images[n])
                    sub, _, name = source["path"].partition("/")
                    found = research_file(Path(session.root), session.day, session.rid, sub, name)  # its allow-list
                    if found is not None:
                        path, kind = found
                        data = read_picture_bytes(path, max_bytes)
                        latest = self.research.get(eid)
                        if latest is None or _sitting(latest) != day:
                            latest = next((s for s in self._researchers(day) if s.rid == eid), None)
                        current = ([o for o in latest.current_outputs() if o.get("kind") == "image"]
                                   if latest is not None and _sitting(latest) == day else [])
                        if not 0 <= n < len(current) or _research_picture_source(latest, current[n]) != source:
                            return None  # this ordinal no longer names the output whose file was read
                        return (data, kind) if data else None
        return None

    def work_session(self, eid: str, day: str):
        """The researcher of session `day` whose work `eid` ("<rid>:<part>") is, and its part; (None, None) if none."""
        rid, _, part = eid.partition(":")
        if self.research is None or not part:
            return None, None
        session = next((s for s in self._researchers(day) if getattr(s, "rid", None) == rid), None)
        return (session, part) if session is not None and _sitting(session) == day else (None, None)

    def _work_picture(self, eid: str, n: int, day: str, max_bytes: int | None):
        session, part = self.work_session(eid, day)
        if session is None or n != 0:
            return None
        root = getattr(session, "root", None) or getattr(self.research, "root", None)
        found = work.picture_file(session, part, Path(root) if root is not None else None)
        if found is None:
            return None
        path, kind = found
        data = read_picture_bytes(path, max_bytes)
        again, _ = self.work_session(eid, day)  # still the same file of the same line after the read
        if again is None or work.picture_file(again, part, Path(root) if root is not None else None) != found:
            return None
        return (data, kind) if data else None

    def masks(self, eid: str, day: str | None = None) -> Path | None:
        """A saved population's masks file (masks.npz, the specialist's own masks; docs/saves.md), of session `day`
        (None: the one open): another session's from the folder its journal filed it in."""
        from .evidence import evidence_of

        if not self.is_open(day):
            retained = self.captured_manifest(eid, day) if re.fullmatch(r"e\d+", eid or "") else None
            return retained[1] / "masks.npz" if retained is not None and (retained[1] / "masks.npz").is_file() else None
        population = evidence_of(self.hub).get(eid)
        if population is not None and (population.session or self.session()) != (day or self.session()):
            return None
        path = getattr(population, "path", None)
        return Path(path) / "masks.npz" if path is not None and (Path(path) / "masks.npz").is_file() else None

    # --- the stores, read in ---------------------------------------------------------------------
    def _member(self, eid: str, day: str) -> Entry | None:
        """A population's child, resolved only by its exact ID; never duplicated in the global list. Of the session
        open now (its populations are the ones in memory)."""
        import copy
        from .evidence import evidence_of

        if not self.is_open(day):
            return None
        found = [(p, p.members[eid]) for p in list(evidence_of(self.hub).items.values())
                 if eid in p.members and (p.session or day) == day]
        if len(found) != 1:
            return None
        population, member = found[0]
        return Entry(eid, "member", f"Captured member {eid} · {hhmm(population.view.get('at'))}", day,
                     float(population.view.get("at") or population.made_at), state=population.status,
                     thread=population.thread, version=population.revision,
                     summary=f"A segmentation instance in {population.id} revision {population.revision}; status {member.status}. "
                             "Not a validated biological identification. Picture: original camera pixels cropped around this member, no outline.",
                     source={"population": population.id, "member": eid, "revision": population.revision,
                             "box": list(member.box), "view": copy.deepcopy(population.view)},
                     pictures=[f"/api/resources/{eid}/picture?day={day}"], values=copy.deepcopy(member.row()),
                     made_by=copy.deepcopy(population.made_by), date=population.day)

    def _populations(self, day: str) -> list[Entry]:
        from .evidence import evidence_of
        from .population import layer_of, summary

        if not self.is_open(day):
            return self._historical(day, "evidence")
        evidence_store = evidence_of(self.hub)
        layer = layer_of(self.hub) if getattr(self.hub, "layer", None) is not None else None
        shown = layer.population() if layer is not None else None
        out = []
        for p in list(evidence_store.items.values()):
            if (p.session or day) != day:
                continue
            placement = None
            plain = f"{len(p.live())} outlined, not shown now"
            if shown is not None and shown.id == p.id:
                verified, total = layer.verified_count()
                plain = (f"{total} outlined, {verified} still in place" if layer.placement
                         else f"{total} outlined, being checked in place")  # before the first check (re-check note)
                placement = (f"shown over the live view, {verified} of {total} verified in place" if verified
                             else f"on the layer, none of {total} verified in place now (kept on the view it was made on)")
            else:
                placement = "not shown; on the view it was made on"
            out.append(Entry(
                p.id, "population", p.title, day, float(p.view.get("at") or p.made_at),
                summary=p.status_why if p.status == "unavailable" else p.count_words(), state=p.status,
                thread=p.thread, placement=placement, plain=p.status_why if p.status == "unavailable" else plain,
                version=int(getattr(p, "revision", 0) or 0),  # a correction is a new revision (M2.3)
                made_by={k: v for k, v in (p.made_by or {}).items() if k in ("operation", "implementation", "policy", "origin", "execution", "for", "actor")},
                run=single_run([(p.made_by or {}).get("execution")]),
                source={"frame": p.capture_id, "seq": p.view.get("seq"), "boot": p.view.get("boot") or None,
                        "comparisonInput": comparison_input_key(p.capture_id, p.view),
                        **({"sourceImage": p.view["sourceImage"]} if p.view.get("sourceImage") else {})},
                media="raw" if p.view.get("sourceImage") else None,
                pictures=[f"/api/resources/{p.id}/picture?day={day}"], date=p.day,
                detail=(lambda p=p: summary(p, ["areaPx", "feretMaxPx", "elongation", "circularity"]))))
        for eid, reason in evidence_store.unavailable.items():
            if eid not in evidence_store.items:
                filed = (getattr(evidence_store.day, "evidence", {}) or {}).get(eid, {})
                out.append(Entry(eid, "population", f"Unavailable result {eid}", day, float(filed.get("at") or 0),
                                 state="unavailable", summary=reason, plain=reason, thread=filed.get("thread"),
                                 version=filed.get("revision")))
        return out

    def _historical(self, sid: str, kind: str) -> list[Entry]:
        """Another session's populations or motion tracks (`kind` "evidence" or "tracks"), from the manifests its
        journal filed: read-only history, never on the live scene."""
        out = []
        for eid in self._journal(sid)[kind]:
            found = self._historical_capture(eid, sid)
            if found is not None:
                out.append(found[0])
        return out

    def _tracks(self, day: str) -> list[Entry]:
        """Motion tracks (motiontracks.py): what track_motion measured, and followed outlines' trails kept."""
        from .motiontracks import TrackLayer, tracks_of

        if not self.is_open(day):
            return self._historical(day, "tracks")
        store = tracks_of(self.hub)
        layer = getattr(self.hub, "track_layer", None)
        out = []
        for item in list(store.items.values()):
            if (item.session or day) != day:
                continue
            shown = isinstance(layer, TrackLayer) and layer.shown == item.id
            placement = layer.placement_words() if shown else "not shown; on the view it was measured on"
            out.append(Entry(
                item.id, "tracks", item.title, day, float(item.span.get("from") or item.made_at), summary=item.words(),
                state=item.status, thread=item.thread, placement=placement,
                plain=item.plain() + ("" if not shown else (", shown" if layer.verified() else ", on its own view")),
                made_by={k: v for k, v in (item.made_by or {}).items() if k in ("operation", "implementation", "policy",
                                                                                "origin", "execution", "how", "for", "actor")},
                run=single_run([(item.made_by or {}).get("execution")]),
                source={"frame": item.capture_id, "seq": item.view.get("seq"), "boot": item.view.get("boot") or None},
                pictures=[f"/api/resources/{item.id}/picture?day={day}"], date=item.day,
                values={"summary": item.summary, "span": item.span, "scale": item.scale, "producer": item.producer,
                        "tracks": [{"id": r["id"], **r["values"], "segments": [{k: v for k, v in g.items()} for g in r["segments"]]}
                                   for r in item.rows]},
                detail=(lambda item=item: f"{item.words()}\nhow: " + "; ".join(f"{k}: {v}" for k, v in item.transformations.items()))))
        return out

    def _acquisitions(self, day: str) -> list[Entry]:
        """Durable scheduled frame sequences; original pixels remain readable after restart. A session's, from each
        date folder it touched (each manifest's own "session"; one without is its date's session)."""
        from .research.acquisition import read_clip
        if self.research is None or not sids.is_sid(day):
            return []
        root = self.research.root / "acquisitions"
        out = []
        for date in self.dates(day):
            for path in (root / date).glob("acq-*/manifest.json"):
                record = read_clip(root, date, path.parent.name)
                if record is None:
                    raise Unread("a retained camera clip manifest could not be read")
                if session_of(record, date) != day:
                    continue
                out.append(self._acquisition(record, day, date))
        return out

    def _acquisition(self, record: dict, day: str, date: str) -> Entry:
        eid = record["id"]
        return Entry(eid, "clip", f"Camera clip · slot {record['slot'] + 1} · {hhmm(record['from'])}",
                day, record["from"], summary=f"{len(record['frames'])} original frames; "
                f"{record['to'] - record['from']:.2f} s captured", state="retained", date=date,
                made_by={"operation": "capture", "for": record["rid"], "mission": record["missionId"]},
                source={"mission": record["missionId"], "slot": record["slot"]}, values=record,
                pictures=[f"/api/resources/{eid}/picture?day={day}&n={n}" for n in range(len(record["frames"]))],
                detail=lambda: "Original camera JPEG sequence. Read frames by n; timestamps and bridge identities "
                               "are retained. This does not include microphone, tab audio or screen recording.")

    def _clip_session(self, clip) -> str:
        """The session footage was held in (held.py; one held with no session store: the one open)."""
        return getattr(clip, "session", "") or self.session()

    def _held(self, day: str) -> list[Entry]:
        """Footage held for a researcher on the user's yes (held.py): real frames, in memory for this run, listed
        in the session it was held in."""
        from .held import held_of

        out = []
        for clip in list(held_of(self.hub).clips.values()):
            if self._clip_session(clip) != day:
                continue
            frames = [f.facts(n) for n, f in enumerate(clip.frames)]
            out.append(Entry(
                clip.id, "clip", f"Held footage · {clip.seconds:g} s · {hhmm(clip.started_at)}", day, clip.started_at,
                summary=clip.words(), state=clip.status, date=clip.day,
                made_by={"by": f"your yes to {clip.researcher}'s question {clip.request}", "for": clip.researcher},
                source={"frame": clip.frames[0].frame if clip.frames else None},
                pictures=[f"/api/resources/{clip.id}/picture?day={day}&n={n}" for n in range(len(clip.frames))],
                values={"frames": frames, "everyS": 0.5},
                detail=(lambda clip=clip: "Real frames of the live view (the camera's own pictures), with the slide's offset "
                        "and epoch for each: " + ("read a frame's picture by its n." if clip.frames else
                                                  "its frames were let go (see its summary); only what it held is said here."))))
        return out

    def _results(self, day: str, *, discover_tables: bool = False) -> list[Entry]:
        from urllib.parse import urlsplit
        out = []
        for r in self.results.entries(day):
            made = (r.get("madeBy") or [{}])[0] or {}
            state, summary = self.results.retention_status(r)
            at = _finite_time(r.get("at"))
            plain = ""
            if r.get("media") == "reference" and r.get("pictures"):
                # Historical tool summaries can be provisional ("still working").
                # A retained resource row describes its content, never live task state.
                try:
                    host = urlsplit(str((r.get("web") or {}).get("page") or "")).hostname
                except ValueError:
                    host = None
                plain = "Reference photo" + (f" · {host}" if host else "")
            out.append(Entry(
                r["id"], "result", r.get("title") or r["id"], day, at if at is not None else 0.0, summary=summary,
                state=state, plain=plain,
                thread=r.get("thread"),
                made_by={"tool": r.get("tool"), "implementation": made.get("implementation"),
                         "execution": (r.get("executions") or [None])[0], "settings": r.get("settings") or None,
                         **({"requestedBy": "user"} if r.get("request") and not r.get("for") else {}),
                         **({"for": r["for"]} if r.get("for") else {}),
                         **({"actor": r["actor"]} if r.get("actor") in ("you", "assistant") else {}),
                         **({"web": r["web"]} if r.get("web") else {})},  # a reference photo's source and licence
                media=r.get("media"), run=single_run(r.get("executions")),
                source=copy.deepcopy(r.get("source") or {}), values=copy.deepcopy(r.get("values")), time_known=at is not None,
                pictures=[f"/api/resources/{r['id']}/picture?day={day}&n={n}" for n in range(len(r.get("pictures") or []))],
                date=str(r.get("day") or ""), portable=bool(r.get("madeWith")), table=self.results.table_candidate(r, day, discover=discover_tables), detail=(lambda r=r: f"asked: {r.get('request')}\nwith: {json.dumps(r.get('args'), default=str)}" if r.get("request") else "")))
        return out

    def _research(self, day: str) -> list[Entry]:
        if self.research is None:
            return []
        from .untrusted import inline

        out = []
        for s in self._researchers(day):
            # The title is the app's words only (its number and what it was asked to make); what it wrote is
            # a researcher's (web-derived) and reaches a prompt only inside its wrapper (`untrusted`).
            title = f"{s.name}'s {DELIVERABLE_WORDS.get(s.deliverable, 'work')}"
            images = [_research_picture_source(s, o) for o in s.current_outputs() if o.get("kind") == "image"]
            out.append(Entry(
                s.rid, "research", title, day, float(s.created or 0.0), summary=inline(s.line(), 300), state=s.status,
                thread=(s.origin or {}).get("thread"),
                version=((s.result or {}).get('findingRevision') or {}).get('version'),
                made_by={"tool": "start_researcher", "effort": s.effort, "engine": s.engine},
                source={"researcher": s.rid, "pictures": images, "written": bool(s.result)},
                pictures=[f"/api/resources/{s.rid}/picture?day={day}&n={n}" for n in range(len(images))],
                detail=(lambda s=s: s.readable(limit=None)), untrusted=True, date=str(getattr(s, "day", "") or ""),
                # Data shows its report by the title it wrote (its words: untrusted, on the wire only), with its forms.
                shown=_words((s.result or {}).get("title"), 200), forms=work.report_forms(s)))
        return out

    def _research_work(self, day: str) -> list[Entry]:
        """A researcher's work besides its report (ticket 66, S4), read from its research session and folder (never
        changed here; research/manager.py owns it): each lesson, video and picture it made (`<rid>:<output id>`, the
        first version's id, at its current version), the tables and charts of its findings (`<rid>:table-<n>`,
        `<rid>:chart-<n>`) and each page and web picture it cited (`<rid>:source-<sha1 of its address, 12 hex>`). All
        of it is the researcher's words, so untrusted; its title for a model is the app's own ("Researcher 2's table 1")."""
        if self.research is None:
            return []
        out = []
        for s in self._researchers(day):
            out.extend(work.entries(s, day, root=getattr(s, "root", None) or getattr(self.research, "root", None)))
        return out

    def _researchers(self, sid: str) -> list:
        """Every researcher of session `sid`, any status, from every date folder it touched (research/manager.py). Read
        once in one collect (another session's are read from disk)."""
        if self.research is None:
            return []
        memo = (getattr(self._local, "pass_", None) or {}).get("researchers")
        if memo is not None and sid in memo:
            return list(memo[sid])
        found = self._read_researchers(sid)
        if memo is not None:
            memo[sid] = list(found)
        return found

    def _read_researchers(self, sid: str) -> list:
        try:
            return list(self.research.researchers(session=sid))
        except TypeError:  # a manager from before sessions: its date folders, each researcher's sitting (or date)
            dates = self.dates(sid)
            found = []
            for date in dates:
                found += self.research.researchers(day=date) if date == self.today() else self.research.read_day(date, kept_only=False)
            return [s for s in found if _sitting(s) == sid]

    def _individuals(self, day: str) -> list[Entry]:
        """Things followed over time (K2): each with its nodes and how it is known; read gives where it is and
        whether it changed."""
        identity = self.identity
        if identity is None or not self.is_open(day):
            return []  # the open session's things followed are the ones loaded (identity.py)
        from .identity import change_words, how_words, thing_words, where_words

        out = []
        titles = {"follow_cells": "Followed", "same_one": "Said to be one", "query_population": "Linked"}
        for i in identity.individuals():
            made = identity.made.get(i) or {}
            by = made.get("by") or "follow_cells"  # what made it: following, the user's "same one", or linking (SF5)
            nodes = sorted(identity.nodes_of(i))
            weakest, basis = identity.confidence(i)
            out.append(Entry(i, "individual", f"{titles.get(by, 'Followed')} · {time.strftime('%H:%M', time.localtime(made.get('at') or 0))} · "
                             f"{thing_words(identity, i)}", day, float(made.get("at") or 0.0),
                             summary=how_words(identity, i), made_by={"tool": by}, date=str(made.get("day") or ""),
                             detail=(lambda i=i: f"where: {where_words(self.hub, identity, i)}\nchanged: {change_words(identity, i)}"),
                             values={"nodes": nodes, "observations": len(identity.history(i)), "confidence": weakest,
                                     "weakestBasis": basis or None}))
        return out

    def _outcomes(self, day: str) -> list[Entry]:
        """How each model has done on the dates of session `day` (K3): one entry per date, specimen, operation, model,
        policy and whether it was asked for, made when its newest run started. Outcomes are read by calendar date
        (the runner's records carry no session), so a date another session shared counts its runs too; ids are made
        from their date (an entry's numbers change as runs come in: a reader records when it read them)."""
        from .operations import label_of
        from .outcomes import outcomes_of

        out = []
        for date in self.dates(day):
            for t in outcomes_of(self.hub).tallies(days="today", today=date):
                key = "|".join((date, t.subject, t.operation, t.implementation, t.policy, "background" if t.background else "asked")
                               + ((t.version,) if t.version else ()))  # a fine-tune's versions are entries apart (K4)
                eid = "u" + hashlib.sha1(key.encode()).hexdigest()[:6]
                out.append(Entry(eid, "outcome", f"Outcome · {label_of(t.implementation)}{f' {t.version}' if t.version else ''}, {t.operation}"
                                 + (", background" if t.background else "") + f", {t.subject or 'no specimen named'}",
                                 day, t.last_at, summary=t.words(self.today(), subject=True), made_by={"tool": "runner"},
                                 date=date,
                                 values={"runs": t.runs, "empty": t.empty, "failed": t.failed, "notRun": dict(t.not_run),
                                         "votes": {"won": t.won, "lost": t.lost, "ties": t.ties, "noneGood": t.none_good},
                                         "executions": t.executions[-10:]}))
        return out

    def _annotations(self, day: str) -> list[Entry]:
        if self.day is None:
            return []
        revisions = self._journal(day)["annotations"]  # the session's own journal (the open one's as loaded)
        from .untrusted import inline
        entries = []
        for aid, history in revisions.items():
            record = history[-1]
            source = record["source"]
            target = source.get("member") or source.get("id") or "Session note"
            entries.append(Entry(aid, "annotation", "Your note · " + inline(record.get("title") or target, 80),
                                 day, record["at"], summary=record["text"] if source.get("scope") == "session" else f"Tentative user interpretation of {target}: {record['text']}",
                                 thread=record.get("thread"), made_by={"by": "user"}, source=source,
                                 values={"text": record["text"], "status": record["status"], "history": history},
                                 version=record["version"], untrusted=True))
        return entries

    def _datatables(self, day: str) -> list[Entry]:
        """Dataset tables (datatables.py): the latest version of each, with whether it is current, updating, stale or
        frozen; its rows stay in their snapshot file, read by the routes (GET /api/datatables/<id>)."""
        if self.day is None:
            return []
        from .datatables import DataTables, datatables_of, freshness

        tables = datatables_of(self.hub)
        entries = []
        for tid, history in (self._journal(day).get("datatables") or {}).items():
            record = history[-1]
            if tables is not None and self.is_open(day):
                fresh = tables.freshness(record)
            else:
                now = DataTables(SimpleNamespace(hub=self.hub, day=self.day), Path(".")).revisions_now(record)
                fresh = freshness(record, now)
            snap = record.get("snapshot") or {}
            sources = ", ".join(s["id"] for s in record.get("sources") or [])
            left = f", {snap['excluded']} left out" if snap.get("excluded") else ""
            summary = f"{snap.get('rows', 0)} rows pooled from {sources}{left} · version {record['version']} · {fresh['words']}"
            entries.append(Entry(tid, "dataset-table", record.get("title") or tid, day, record["at"], summary=summary,
                                 thread=record.get("thread"), made_by={"by": record.get("by") or "user"},
                                 values={**{k: v for k, v in record.items() if k not in ("type", "values", "excluded")},
                                         "freshness": fresh}, version=record["version"],
                                 plain=f"Table of {snap.get('rows', 0)} rows from {sources} · {fresh['words']}"))
        return entries

    def _table_derivatives(self, day: str) -> list[Entry]:
        from .table_derivatives import freshness
        journal = self._journal(day)
        entries = []
        service = getattr(self.hub, 'table_derivatives', None)
        for rid, history in journal.get('table_derivatives', {}).items():
            record = history[-1]
            source = record['source']
            tables = journal.get('datatables', {}).get(source['id']) or []
            fresh = service.wire(record)['freshness'] if service is not None and self.is_open(day) else freshness(record, tables[-1] if tables else None)
            values = {**record, 'freshness': fresh}
            summary = f"{source['id']} version {source['version']} · {fresh['words']}"
            entries.append(Entry(rid, record['kind'], record['title'], day, record['at'], summary=summary,
                                 thread=record.get('thread'), made_by={'by': record.get('by', 'user')},
                                 values=values, version=record['version'], plain=summary))
        return entries

    def _facts(self, day: str) -> list[Entry]:
        """Facts settled today, each with its source (day/memory.py: the user's or a researcher's, never a guess of
        the app's), made when they were last settled. A researcher's (a model's paraphrase of web-derived work) is
        untrusted: its words reach a prompt only inside their wrapper; every fact is one line without brackets, as
        the day's digest gives it (untrusted.inline; K3 review SF2)."""
        memory = self.memory
        if memory is None or not self.is_open(day):
            return []  # the open session's facts are the ones loaded (day/memory.py)
        from .day.memory import SETTLED_BY
        from .untrusted import inline

        store = getattr(memory, "store", None)
        at = float(getattr(store, "digest_at", 0.0) or 0.0)
        out = []
        for fact in (getattr(memory, "facts", None) or []):
            text = inline(fact)
            researcher = text.endswith(f"({SETTLED_BY['researcher']})")
            # Who settled it, only as its own words say (a digest from before sources were kept says nobody: unknown).
            by = "a researcher" if researcher else "you" if text.endswith(f"({SETTLED_BY['user']})") else "unknown"
            out.append(Entry("f" + hashlib.sha1(fact.encode()).hexdigest()[:6], "fact",
                             "Fact · from a researcher, with a source" if researcher else f"Fact · {text[:60]}", day, at,
                             summary=text, made_by={"tool": "memory", "by": by}, untrusted=researcher))
        return out

    def _bookmarks(self, day: str) -> list[Entry]:
        if self.bookmarks is None:
            return []
        from .bookmarks import conversation_of

        out = []
        for mark in self.bookmarks.session_marks(day, self.dates(day)):
            when = time.strftime("%H:%M", time.localtime(mark.get("at") or 0))
            words = (mark.get("note") or "a place on the slide") + (
                f" (kept without a picture: {mark['pictureMissing']})" if mark.get("pictureMissing") else "")
            out.append(Entry(mark["id"], "bookmark", f"Bookmark · {when}" + (f" · {words}" if mark.get("note") else ""), day,
                             float(mark.get("at") or 0.0), summary=words + (f" ({mark['subject']})" if mark.get("subject") else ""),
                             thread=conversation_of(mark, self.day, day),
                             made_by={"tool": "bookmark", **({"actor": mark["actor"]} if mark.get("actor") in ("you", "assistant") else {})},
                             source={"frame": mark.get("frame"), "seq": mark.get("seq"), "boot": mark.get("boot")},
                             date=str(mark.get("day") or ""),
                             pictures=[f"/api/resources/{mark['id']}/picture?day={day}"] if mark.get("picture") else [],
                             values={k: mark.get(k) for k in ("objective", "epoch", "offset", "centre")}))
        return out

    @staticmethod
    def project_entry(item) -> Entry:
        document = item.document
        imported = document['state'].get('sourceImport', {})
        producer = imported.get('producer', {}) if isinstance(imported, dict) else {}
        # The intake publisher owns sourceImport; generic document edits cannot forge it.
        made = ({"actor": producer['actor'], "operation": producer['operation']}
                if isinstance(producer, dict) and producer.get('kind') == 'agent-import'
                and isinstance(producer.get('actor'), str) and isinstance(producer.get('operation'), str)
                else {})
        if document['kind'] == 'project-material-membership':
            membership = document['state'].get('materialMembership', {})
            if isinstance(membership, dict) and isinstance(membership.get('by'), str):
                made = {'actor': membership['by']}
        return Entry(item.id, "project", document['title'], item.session, item.at,
                     version=item.revision, state="saved", untrusted=True, made_by=made,
                     summary=f"{document['kind']} project · revision {item.revision}",
                     source={"sha256": item.digest, "parent": item.parent,
                             "resources": document['resources'], "runtimes": document['runtimes']},
                     values=document)

    def _registrations(self, day: str) -> list[Entry]:
        """The session's volumes and the registrations run on them (registrations.py), from each date it touched."""
        if self.registrations is None:
            return []
        out = []
        for prefix in ("v", "j"):
            for record in self.registrations.records(prefix, day, self.dates(day)):
                words = self.registrations.words(record)
                if prefix == "v":
                    title = f"Volume · {record['name']}"
                    state, picture = "saved", record.get("preview")
                else:
                    title = f"Registration · {record['volume']['name']} · {time.strftime('%H:%M', time.localtime(record['at']))}"
                    state, picture = {"done": "done", "failed": "failed", "cancelled": "cancelled",
                                      "interrupted": "interrupted"}.get(record["state"], "running"), record.get("picture")
                out.append(Entry(record["id"], "volume" if prefix == "v" else "job", title, day, float(record["at"]),
                                 summary=words, state=state, thread=record.get("thread"),
                                 made_by={"tool": "register_volume", "implementation": record.get("entry", "")}
                                 if prefix == "j" else {"how": "added"},
                                 pictures=[f"/api/resources/{record['id']}/picture?day={day}"] if picture else [],
                                 values={k: record.get(k) for k in ("facts", "volume", "atlas", "environment",
                                                                    "parameters", "outputs", "regions", "series")
                                         if record.get(k) is not None},
                                 date=record["date"]))
        return out

    def _snapshots(self, day: str) -> list[Entry]:
        """A session's snapshots, from each date folder it touched (snapshots/<date>/), each by its own "session" (one
        from before sessions is its date's session)."""
        if self.snapshots is None:
            return []
        out = []
        for date in self.dates(day):
            folder = self.snapshots / date
            if folder.is_dir():
                out += self._snapshots_in(folder, date, day)
        return out

    def _snapshots_in(self, folder: Path, date: str, day: str) -> list[Entry]:
        out = []
        for path in sorted(folder.glob("snapshot-*.json")):
            try:
                facts = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(facts, dict) or session_of(facts, date) != day:
                continue
            at = float(facts.get("wallAt") or path.stat().st_mtime)
            outlines = facts.get("outlines") or []
            named = [o.get("label") for o in outlines if isinstance(o, dict) and o.get("label")]
            out.append(Entry(
                path.stem, "snapshot", f"Snapshot · {hhmm(at)}", day, at,
                summary=("Camera crop without overlays" if facts.get("overlays") is False else
                         f"{len(outlines)} outline{'s' if len(outlines) != 1 else ''}" + (f" ({', '.join(named[:5])})" if named else "")),
                made_by={"tool": "snapshot", **({"actor": facts["actor"]} if facts.get("actor") in ("you", "assistant") else {})},
                source={"frame": facts.get("frameId")}, date=date,
                pictures=[f"/api/resources/{path.stem}/picture?day={day}"],
                values={"scale": facts.get("scale"), "overlays": facts.get("overlays") is not False}))
        return out

    def _named(self, day: str) -> list[Entry]:
        """Outlines named in session `day` (seen.py keeps every session's, each line with its own; ids are a date
        folder's, so one named on another date than the session began is told apart by its date)."""
        seen = self.seen
        if seen is None:
            return []
        begun = sids.date_of(day) if sids.is_sid(day) else ""
        newest: dict[tuple, Any] = {}
        for k in list(getattr(seen, "kept", [])):
            date = getattr(k, "day", "") or begun
            if session_of({"session": getattr(k, "session", "")}, date) != day:
                continue
            newest[(date, k.id)] = k  # a rename appends a line: the newest wins
        return [Entry(f"named-{k.id}" if date == begun else f"named-{date}-{k.id}", "named-outline",
                      f"{k.label} · {hhmm(k.at)}", day, float(k.at), date=date,
                      summary=named_words(k), thread=k.thread,
                      # Which model drew it, as kept when it was named (seen.Kept.made_by; 2026-10-11).
                      made_by={"by": "user" if k.status == "confirmed" else "app",
                               **{key: value for key, value in (getattr(k, "made_by", None) or {}).items()
                                  if key in ("operation", "implementation", "actor")}},
                      source={"frame": k.frame_id, "outline": k.object_id}) for (date, _), k in newest.items()]

    def _taught(self, day: str) -> list[Entry]:
        """Labels taught in session `day` (taught.py keeps the user's from every session; each example says the
        session it was taught in, one from before sessions its date's)."""
        taught = self.taught
        if taught is None:
            return []
        groups: dict[str, list] = {}
        for ex in taught._taught():
            if session_of({"session": getattr(ex, "session", "")}, getattr(ex, "day", "") or day) == day:
                groups.setdefault(ex.key, []).append(ex)
        out = []
        for key, examples in groups.items():
            newest = max(examples, key=lambda e: e.at)
            slug = re.sub(r'[^a-z0-9]+', '_', key.lower()).strip('_')
            out.append(Entry(f"label-{slug}-{hashlib.sha1(key.encode()).hexdigest()[:6]}", "taught-label", f"Your label: {newest.label}", day, float(newest.at),
                             summary=f"{len(examples)} example{'s' if len(examples) != 1 else ''} you taught",
                             made_by={"by": "you"}))
        return out

    def _scale(self, day: str) -> list[Entry]:
        """The scale as it stands (the objective and the field number, each with where it came from: the
        user, the studio, a calibration, or the default) and every calibration made, as the index's
        "calibration" entries (set_objective, scale and rig change the first; calibrate_scale adds one)."""
        scale = self.scale
        if scale is None or not hasattr(scale, "facts") or not self.is_open(day):
            return []  # the scale is the user's, kept until changed (day/prefs.py): listed in the session open now
        facts = scale.facts()
        out = []
        objective, field_number = facts.get("objective") or {}, facts.get("fieldNumber") or {}
        if objective.get("value") is not None:
            reading = self.hub.scale_reading() if hasattr(self.hub, "scale_reading") else None
            measured = bool(getattr(reading, "measured", False))
            at = max(float(objective.get("at") or 0.0), float(field_number.get("at") or 0.0))
            out.append(Entry("scale-now", "calibration",
                             f"Scale · {objective['value']}x ({'measured' if measured else 'estimated'})", day, at,
                             summary=f"objective {objective['value']}x (said by {objective.get('source') or 'unknown'}); "
                                     f"field number {field_number.get('value')} mm ({field_number.get('source') or 'default'})",
                             made_by={"by": objective.get("source") or "unknown"}, values=facts))
        for objective, c in (facts.get("calibrated") or {}).items():
            out.append(Entry(f"scale-{objective}", "calibration", f"Scale for {objective}x · measured", day, float(c.get("at") or 0.0),
                             summary=f"{c.get('lengthUm')} µm over {c.get('lengthPx')} px; field {round(float(c.get('fieldUm') or 0), 1)} µm",
                             made_by={"tool": "calibrate_scale"}, values=c))
        return out

    def session_datasets(self, day: str, fresh: bool = False) -> list[dict]:
        """The Record datasets session `day` holds, as the Datasets tab's rows (datasets/store.row, without sizes),
        newest first: those its journal says it started, and those no session claims (from before sessions) by the
        date they began. The one rule for the index's dataset entries and a session's recordings
        (datasets/recordings.py). `fresh`: read now rather than from the last 10 s (a Record just stopped)."""
        if self.datasets is None or not self.datasets.is_dir():
            return []
        now = time.monotonic()
        if fresh or self._datasets_cache is None or now - self._datasets_cache[0] > 10:
            from .datasets import manifest, store

            self._datasets_cache = (now, [store.row(folder, summary, sizes=False) for folder, summary in manifest.all_in(self.datasets)])
        mine, claimed = self._made("dataset", day), self._claimed("dataset")
        out = []
        for row in self._datasets_cache[1]:
            started = float(row.get("startedAtEpoch") or 0.0)
            # The session whose journal says it started it; one no session claims (from before sessions), its date's.
            date = time.strftime("%Y-%m-%d", time.localtime(started)) if started else ""
            if started and (str(row.get("id")) in mine or (str(row.get("id")) not in claimed and date == day)):
                out.append(row)
        return out

    def _datasets(self, day: str) -> list[Entry]:
        out = []
        for row in self.session_datasets(day):
            started = float(row.get("startedAtEpoch") or 0.0)
            date = time.strftime("%Y-%m-%d", time.localtime(started))
            counts = row.get("counts") or {}
            out.append(Entry(str(row.get("id")), "dataset", f"Dataset · {hhmm(started)}" + (f" ({row['name']})" if row.get("name") else ""),
                             day, started, date=date, state="recording" if row.get("status") == "collecting" else str(row.get("status") or "done"),
                             summary=", ".join(f"{v} {k}" for k, v in list(counts.items())[:5] if v),
                             made_by={"by": "Record"}))
        return out

    def _sources(self, day: str) -> list[Entry]:
        """The session's sources (sources.py; ADR 0022): each asset it opened or made, with its metadata as the user
        and the app know it (library.py). Read in full by id ("a7")."""
        library = getattr(self.hub, "library", None)
        if library is None or self.day is None:
            return []
        from .sources import rows, stage_line

        record = self.day if day == self.day.sid else self.day.catalogue.get(day)
        if record is None:
            return []
        out = []
        for row in rows(day, library.assets(), record)["sources"]:
            meta = {k: v for k, v in row.items() if k not in ("thumb", "joined", "opened", "position", "alsoIn")}
            out.append(Entry(row["id"], "asset", row.get("title") or row["id"], day, float(row.get("capturedAt") or 0.0),
                             summary=stage_line(meta).split(": ", 1)[-1], made_by={"from": row.get("from")},
                             detail=lambda meta=meta, row=row: json.dumps({**meta, "position": row.get("position"),
                                                                           "joinedOnItsOwn": row.get("joined")}, indent=1)))
        return out

    def _recordings(self, day: str) -> list[Entry]:
        if self.recordings is None or not self.recordings.is_dir():
            return []
        mine, claimed = self._made("recording", day), self._claimed("recording")
        groups: dict[str, list[Path]] = {}
        for date in self.dates(day):
            for path in sorted(self.recordings.glob(f"MicroscopeMind-{date.replace('-', '')}-*")):
                if path.suffix in (".mp4", ".webm"):
                    rid = path.name.split(".")[0].removesuffix("-play")  # a recording and its playable copy: one
                    # The session whose journal says it started it; one no session claims, its date's.
                    if rid in mine or (rid not in claimed and date == day):
                        groups.setdefault(rid, []).append(path)
        out = []
        for rid, paths in groups.items():
            try:
                at = time.mktime(time.strptime(rid.split("-", 1)[1][:15], "%Y%m%d-%H%M%S"))  # its id is when it started
            except (ValueError, IndexError):
                at = min(p.stat().st_mtime for p in paths)
            size = max(p.stat().st_size for p in paths)
            # Still being recorded: no playable copy yet, and its file changed in the last seconds (recordings.py).
            going = not any(p.stem.endswith("-play") for p in paths) and time.time() - max(p.stat().st_mtime for p in paths) < 10
            from .recordings import recording_label
            label = recording_label(self.recordings / f"{rid}.channels.jsonl")
            out.append(Entry(rid, "recording", f"{label} · {hhmm(at)}", day, at, state="recording" if going else "done",
                             summary=f"{size // (1024 * 1024)} MB", made_by={"by": "Record"},
                             date=time.strftime("%Y-%m-%d", time.localtime(at))))
        return out


def _sitting(session) -> str:
    """The session a researcher belongs to (research/manager.py `sitting`; its date folder before sessions)."""
    return str(getattr(session, "sitting", "") or getattr(session, "day", "") or "")


_JOURNALS: dict[str, tuple[tuple, dict]] = {}  # journal path -> ((inode, size, mtime), what it files)
_JOURNALS_LOCK = threading.Lock()


def _journal_records(root: Path, sid: str) -> dict:
    """What another session's journal files of its work, read-only and kept until the file changes: evidence and
    tracks saved ({id: the latest event, with its path}), annotations and dataset tables ({id: [revisions]})."""
    path = root / sid / "journal.jsonl"
    empty = {"evidence": {}, "tracks": {}, "annotations": {}, "datatables": {}, "table_derivatives": {}}
    try:
        stat = path.stat()
    except OSError:
        return empty
    key = (stat.st_ino, stat.st_size, stat.st_mtime)
    with _JOURNALS_LOCK:
        cached = _JOURNALS.get(str(path))
        if cached is not None and cached[0] == key:
            return cached[1]
    out = {"evidence": {}, "tracks": {}, "annotations": {}, "datatables": {}, "table_derivatives": {}}
    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    event = json.loads(line)
                except ValueError:
                    continue  # a torn line
                if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                    continue
                kind, eid = event["type"], event.get("id")
                if kind == "evidence-saved" and isinstance(eid, str) and event.get("path"):
                    out["evidence"][eid] = event
                elif kind == "tracks-saved" and isinstance(eid, str) and event.get("path"):
                    out["tracks"][eid] = event
                elif kind == "annotation" and isinstance(eid, str):
                    out["annotations"].setdefault(eid, []).append(event)
                elif kind == "table-derivative" and isinstance(eid, str):
                    out["table_derivatives"].setdefault(eid, []).append(event)
                elif kind == "datatable" and isinstance(eid, str):
                    out["datatables"].setdefault(eid, []).append(event)
    except (OSError, UnicodeError):
        return empty
    with _JOURNALS_LOCK:
        _JOURNALS[str(path)] = (key, out)
    return out


def resources_of(hub) -> Index:
    """The hub's index, or one over the hub alone (a test's stand-in hub)."""
    index = getattr(hub, "resources", None)
    if isinstance(index, Index):
        return index
    return Index(hub)
