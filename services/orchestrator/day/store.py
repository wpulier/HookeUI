"""SessionStore: the session's threads, subjects, preferences and digest, rebuilt from its journal.

A session is the project, not the day (ADR 0017): a day can hold several and a session can span days. Its id
(day/sid.py) is the date it began, with ".N" for a date's later ones, and every day journal written before
sessions existed is the session whose id is its date. `sid` is the session's identity (routing, `Asked`, the
wire, the journal); `date` is today's calendar folder, where new per-day files go; `days` are the dates the
session touched. (DayStore is its old name.)

A new session starts only when the user asks (`new`); `switch_session` opens another; midnight does nothing.
Work asked in one session is filed in it whatever session is open when it ends (`elsewhere`).

Every change is an event, appended to the session's journal first and then applied, so what is
in memory is always what a restart would rebuild. One thread is active: the conversation
the user is having now (chat and voice share it). Threads start only when the user asks
("new topic", the studio's New thread); a different specimen within a thread is recorded in
it as a new subject, not a new thread.

The user's settings are not the day's: they are kept until changed in preferences.jsonl beside
the day folders (prefs.py), and `prefs` shows them with the session's own (prefs.SESSION_KEYS).
"""
from __future__ import annotations

import json
import math
import re
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from . import sid as sids
from .catalogue import Catalogue, day_of, ids_in
from .journal import Journal
from .model import Asked, Thread, Turn
from .prefs import SESSION_KEYS, Prefs

TITLE_WORDS = 7
LINES_KEPT = 1500  # shown lines kept in memory per thread (the journal keeps all)


def today(clock: Callable[[], float] = time.time) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(clock()))


def auto_title(text: str) -> str:
    words = text.replace("\n", " ").split()
    title = " ".join(words[:TITLE_WORDS]).strip(" ,.?!")
    return (title[:1].upper() + title[1:] + ("…" if len(words) > TITLE_WORDS else ""))[:60]


class SessionStore:
    def __init__(self, root: Path | None = None, clock: Callable[[], float] = time.time,
                 day: str | None = None, *, session: str | None = None, prefs: Prefs | None = None,
                 catalogue: Catalogue | None = None, _header: dict | None = None, locations=None) -> None:
        self.root = root
        self.locations = locations
        self.clock = clock
        self.listeners: list[Callable[[dict], None]] = []  # called with each event, after it is applied
        self.generation = 0  # bumped each time the session is (re)loaded: background work started
        #                      before then (a summary, the digest) must not write into the new one
        sid = session or day or today(clock)  # `day=`: the old name of `session=`
        # The user's settings, kept until changed (prefs.py): seeded once from the newest day journal that had
        # them, before anything reads them. `prefs` is one dict for good: its readers may hold it.
        self.user_prefs = prefs if prefs is not None else Prefs(root)
        if prefs is None:
            self.user_prefs.migrate(upto=today(clock))
        self.catalogue = catalogue if catalogue is not None else Catalogue(Journal("", root).root, locations=locations)
        self.prefs: dict = {}
        self._elsewhere: dict[str, SessionStore] = {}  # other sessions' stores, for work filed there
        # A session opening, and work filed from another thread choosing its session and writing to it, one at a time:
        # else a save finishing on its writer thread could choose "this one" just before the user's new session opens
        # and write into that one (CI, 2026-10-26: test_motion_tracks, 1 run in 8). Listeners are told outside it.
        self._switching = threading.RLock()
        self._load(sid, _header)

    @classmethod
    def resume(cls, root: Path | None = None, clock: Callable[[], float] = time.time, *, locations=None) -> "SessionStore":
        """The store the app starts in (ADR 0017: on open, the session you were in): the session it was last in (the
        pointer in preferences.jsonl), else the one a request was last made in, else today's. Kept as the one it is in."""
        prefs = Prefs(root)
        prefs.migrate(upto=today(clock))
        catalogue = Catalogue(Journal("", root).root, locations=locations)
        sid = prefs.current
        if not (sid and sids.is_sid(sid) and ((locations.root_for(sid) if locations is not None else catalogue.root) / sid / sids.JOURNAL).is_file()):
            last = catalogue.last_used()
            sid = last.id if last is not None else today(clock)
        store = cls(root, clock, session=sid, prefs=prefs, catalogue=catalogue, locations=locations)
        prefs.point(store.sid)
        return store

    @property
    def date(self) -> str:
        """Today's calendar date by the store's clock: the folder new per-day files go to (evidence, tracks, results,
        bookmarks; "today" for taught, seen, identity and research). It follows the clock: after midnight the session
        goes on, and what it makes then is filed under the new date."""
        return today(self.clock)

    @property
    def days(self) -> list[str]:
        """The calendar dates the session touched, oldest first (today's once it has written today)."""
        return sorted(self._days)

    def holds(self, day: str | None) -> bool:
        """Whether a calendar date is one of the session's dates: one its events fall on, or today's."""
        return bool(day) and (day == self.date or day in self._days)

    @property
    def day(self) -> str:
        """The session id by its old name, for tests only: the app reads `sid` or `date`
        (tests/test_session_names.py)."""
        return self.sid

    # --- loading -------------------------------------------------------------------------
    def _load(self, sid: str, header: dict | None = None) -> None:
        from .ownership import namespace
        from ..scientific_import_transaction import reading, require_visible
        root = self.root_for(sid)
        if self.locations is not None:
            self.locations.writable(sid)
        with reading(root):
            journal = Journal(sid, root)
            events = journal.load(complete_only=True)
            # An existing conversation can be reconstructed while another Project is being created.
            # Empty/origin-only/torn creation still needs the creation lock before it can gain events.
            established = any(
                e.get("type") == "thread-opened" and isinstance(e.get("thread"), str)
                and type(e.get("n")) is int and type(e.get("at")) in (int, float)
                for e in events)
            if established:
                self._load_owned(sid, header, journal=journal, events=events)
                return
        with namespace(root):
            require_visible(root)
            self._load_owned(sid, header)

    def _load_owned(self, sid, header, *, journal=None, events=None):
        self.generation += 1
        self.sid = sid
        self.journal = journal if journal is not None else Journal(sid, self.root_for(sid))
        self.name = ""  # its name ("" until it has one: an old day journal has none, sid.label says its date)
        self.guide = None
        self.local_only = False  # provider admission belongs to the session, never global preferences
        self.named = False  # the user named it (the app's own name, from its first specimen or request, is not)
        self.created = 0.0
        self.started_from: str | None = None  # the session it was started from
        self._days: set[str] = set()
        self.ids_used: dict[str, int] = {}  # prefix -> the highest id number this session gave, on any day (ids.py)
        self.last_user = 0.0  # when the user last asked something in it
        # The app's suggestion of a new session, pending ({"specimen", "hours"}; day/subject.py), and the specimens the
        # user kept working on here when one was suggested (never suggested again for them).
        self.suggestion: dict | None = None
        self.kept_specimens: set[str] = set()
        self.threads: dict[str, Thread] = {}
        self.active_id = ""
        self.subjects: dict[str, dict] = {}  # name -> {"name", "named_by", "first", "last", "threads"}
        # Its sources (library.py; ADR 0022): the assets opened in it, by when last opened, those the user removed
        # (a removed one's specimens are not the session's: `_subject_events` are applied again without them), and
        # what is on stage now ("live", an asset id, or None before anything was opened in it).
        self.sources: dict[str, float] = {}
        self.removed_sources: set[str] = set()
        self.on_stage: str | None = None
        # The population the cells' layer showed in it last (population.py Layer, `record_layer`), or None: a reopened
        # session shows it again once the Stage lands on the picture its cells were found on (App.reopen_layer).
        self.layer_shown: str | None = None
        self.positions: dict[str, float] = {}  # where each recording was when it left the stage (seconds)
        # The app's name the session had before a source's removal took it (asset id -> that name), so Undo restores it.
        self.name_before_removal: dict[str, str] = {}
        self._subject_events: list[dict] = []
        self.prefs.clear()  # the user's (kept until changed), then the session's from its journal
        self.prefs.update(self.user_prefs.values)
        self.digest = ""
        self.digest_at = 0.0  # when the digest (the day's settled facts) was last written: the index's facts are dated by it
        self.seq = 0  # the last turn id given today
        # How the user did on lesson steps today (quiz.py): rid -> step id -> {"outcome", "by", "at"}, the latest
        self.outcomes: dict[str, dict[str, dict]] = {}
        # Evidence saved today (evidence.py): id -> {"path", "thread", "revision", "members"}, the latest save
        self.evidence: dict[str, dict] = {}
        self.evidence_ids: dict[str, str] = {}  # every population id made today -> its last member id (never reused)
        # Motion tracks saved today (motiontracks.py): id -> {"path", "thread"}, the latest save; and every
        # tracks id made today, whatever became of its save (never reused)
        self.tracks: dict[str, dict] = {}
        self.tracks_ids: set[str] = set()
        self.workspace_layout: dict | None = None
        self.workspace_removed: dict = {}
        self.workspace_origins: dict = {}
        self.workspace_completions: dict = {}
        self.annotations: dict[str, list[dict]] = {}  # user observations, every journaled revision
        self.table_derivatives: dict[str, list[dict]] = {}
        self.datatables: dict[str, list[dict]] = {}  # dataset tables (datatables.py), every journaled version
        self.results: dict[str, dict] = {}  # what tools measured or made today (resources.Results), by id
        self.last_turn: Turn | None = None  # the latest turn in any thread
        events = self.journal.load() if events is None else events
        for event in events:
            try:
                self._apply(event, replay=True)
            except Exception:
                continue  # one bad event never loses the session
        if header is not None and not events:
            self._record(header, strict=header.get("localOnly") is True)  # a new session: its header is its first line
        if not self.created:
            self.created = round(self.clock(), 3)
        if not self.threads:
            self.open_thread()
        elif self.active_id not in self.threads:
            self.active_id = max(self.threads.values(), key=lambda t: t.last).id

    def root_for(self, sid):
        return self.locations.root_for(sid) if self.locations is not None else Journal("", self.root).root

    def current(self, generation: int | None) -> bool:
        """Whether work started at this generation still belongs to the session as it is now."""
        return generation is None or generation == self.generation

    @property
    def active(self) -> Thread:
        return self.threads[self.active_id]

    def here(self) -> Asked:
        """Where a request made now belongs: the active conversation, in the session as loaded."""
        return Asked(self.active_id, self.generation, day=self.sid, session=self.sid)

    def thread_for(self, asked: Asked | None = None, rid: str | None = None) -> str:
        """The conversation something belongs to: the one it was asked in (when it was asked in this session, or,
        for an origin from before sessions, in this load of it), else the one its researcher was started in, else
        the active one. Work of another session is filed there (`elsewhere`) before this is asked."""
        if asked is not None and asked.thread in self.threads and (
                asked.session == self.sid if asked.session else asked.routing_generation == self.generation):
            return asked.thread
        if rid:
            started = self.thread_of_research(rid)
            if started is not None:
                return started.id
        return self.active_id

    # --- the one way state changes ---------------------------------------------------------
    def _record(self, event: dict, *, strict: bool = False) -> dict:
        event = {"at": round(self.clock(), 3), **event}  # the store's clock: the day an event falls on is its own
        from ..scientific_import_transaction import ImportPending
        if self.locations is not None:
            self.locations.writable(self.sid)
            if self.root_for(self.sid) != self.journal.root:
                raise ImportPending('The Project location changed; reopen before writing')
            # A routed Project never reports a retained conversation after a failed write.
            strict = True
        try:
            event = self.journal.append(event)
        except ImportPending:
            raise  # transaction admission is retryable, never an event retained only in memory
        except (OSError, ValueError) as exc:  # disk full, permissions, unencodable text
            if strict:
                raise
            # The conversation goes on in memory; only a restart would lose this event.
            event = {"at": round(self.clock(), 3), **event}
            if not getattr(self, "_write_failed", False):
                self._write_failed = True
                print(f"Day journal: could not write ({exc}); continuing in memory", flush=True)
        self._apply(event)
        if event.get('type') in ('evidence-saved', 'tracks-saved', 'result'):
            try:
                from ..workspace_views import reconcile
                reconcile(self)
            except (OSError, ValueError):
                # The retained result remains successful; reconnect retries presentation.
                pass
        if getattr(self, "_quiet", False):
            return event  # a session being opened: listeners hear one "session-opened" when it is ready
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:
                pass
        return event

    # --- sessions ------------------------------------------------------------------------
    def open(self, sid: str, why: str = "switch", header: dict | None = None) -> None:
        """Load session `sid` (generation + 1; a new one with its `header`), keep it as the one the app is in, then
        tell listeners once: "session-opened". Its live outlines are never restored (the view starts fresh, said
        by the app); its conversations, memory and work are its journal's."""
        from .ownership import namespace
        from ..scientific_import_transaction import require_visible
        target_root = self.root_for(sid)
        if self.locations is not None:
            self.locations.writable(sid)
        with namespace(target_root):
            require_visible(target_root)
            self._open(sid, why, header)

    def _open(self, sid, why, header):
        with self._switching:  # work being filed from another thread finishes in the session it chose first
            previous = self.sid
            self._elsewhere.clear()  # their copies may be stale now; the store being left is read afresh if needed
            self._quiet = True
            try:
                self._load(sid, header=header)
                if header is None:
                    # Opening it is using it: the session list puts it first, and today is one of its days.
                    self._record({"type": "opened", "why": why, "from": previous})
            finally:
                self._quiet = False
            self.user_prefs.point(self.sid)
        event = {"type": "session-opened", "session": self.sid, "previous": previous, "why": why,
                 "restored": {"liveOutlines": False}}
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:
                pass

    def new(self, name: str = "", by: str = "user", *, local_only: bool = False, guide: str | None = None) -> str:
        """A new session (only when the user asks: ADR 0017), begun today, with one empty conversation; it is the one
        the app is in now. Returns its id."""
        from .ownership import namespace
        if self.locations is not None:
            with self._switching:
                root = self.locations.create(name, date=self.date, preferences=self.user_prefs,
                    actor=by, clock=self.clock, local_only=local_only, started_from=self.sid, guide=guide)
                self.open(root.session, "new")
                return root.session
        with namespace(self.journal.root):
            return self._new(name, by, local_only=local_only, guide=guide)

    def _new(self, name, by, *, local_only, guide):
        sid = sids.next_sid(self.journal.root, self.date)
        name = " ".join(str(name or "").split())[:80]
        self.open(sid, "new", header={"type": "session-started", "session": sid, "name": name,
                                      "created": round(self.clock(), 3), "from": self.sid, "by": by, "localOnly": local_only, "guide": guide})
        return sid

    def create_retained(self, name: str, *, actor: str) -> "SessionStore":
        """Create a local-only Project without changing active session, stage or preferences."""
        from .ownership import namespace
        if self.locations is not None:
            with self._switching:
                root = self.locations.create(name, date=self.date, preferences=self.user_prefs,
                    actor=actor, clock=self.clock)
                store = SessionStore(self.root, self.clock, session=root.session, prefs=self.user_prefs,
                    catalogue=self.catalogue, locations=self.locations)
                self._elsewhere[root.session] = store
                return store
        with self._switching, namespace(self.journal.root):
            sid = sids.next_sid(self.journal.root, self.date)
            header = {"type": "session-started", "session": sid, "name": " ".join(name.split())[:80],
                      "created": round(self.clock(), 3), "from": None, "by": actor, "localOnly": True}
            store = SessionStore(self.root, self.clock, session=sid, prefs=self.user_prefs,
                                 catalogue=self.catalogue, _header=header)
            self._elsewhere[sid] = store
            return store

    def switch_session(self, sid: str) -> bool:
        """Open another session; False when it is the one open. KeyError when there is no such session."""
        if sid == self.sid:
            return False
        if not sids.is_sid(sid) or not (self.root_for(sid) / sid / sids.JOURNAL).is_file():
            raise KeyError(sid)
        self.open(sid, "switch")
        return True

    def rename_session(self, name: str, by: str = "user", auto: bool = False) -> bool:
        """Name the session. The app's own name (`auto`: from its first specimen or request) never replaces one the
        user gave. False when nothing changed."""
        name = " ".join(str(name or "").split())[:80]
        if auto:
            name = name[:1].upper() + name[1:]  # the app's name reads as a title, as a conversation's does
        if not name or name == self.name or (auto and self.named):
            return False
        self._record({"type": "session-renamed", "name": name, "by": by, "auto": auto})
        return True

    def keep_session(self, by: str = "user", specimen: str = "") -> None:
        """The user keeps working here when the app suggests a new session (the studio's "Keep here"): journaled, so
        the suggestion is not made again for that subject."""
        self._record({"type": "session-kept", "by": by, **({"specimen": specimen[:80]} if specimen else {})})

    def suggest(self, specimen: str, hours: float) -> None:
        """The app suggests a new session for `specimen` (day/subject.py decides when; it never starts one): journaled,
        so the studio shows it (the chip's dot, the menu's row) until the user acts on it, keeps working here or opens
        another session."""
        self._record({"type": "session-suggested", "specimen": " ".join(str(specimen).split())[:80],
                      "hours": round(float(hours), 1)})

    @property
    def label(self) -> str:
        """What the session is called: its name, else "Untitled session" while it began today, else its date
        (sid.unnamed, the same words the studio shows)."""
        return self.name or sids.unnamed(self.sid, self.created, self.clock())

    def elsewhere(self, sid: str | None) -> "SessionStore":
        """The store of session `sid`, for work asked there and finished while another is open: its own journal,
        never this one's, and nothing of it is told to this store's listeners. This store itself for its own sid
        (or none). KeyError when there is no such session."""
        if not sid or sid == self.sid:
            return self
        store = self._elsewhere.get(sid)
        if store is None:
            if not sids.is_sid(sid) or not (self.root_for(sid) / sid / sids.JOURNAL).is_file():
                raise KeyError(sid)
            store = SessionStore(self.root, self.clock, session=sid, prefs=self.user_prefs, catalogue=self.catalogue, locations=self.locations)
            self._elsewhere[sid] = store
        return store

    def made(self, kind: str, pid: str, day: str | None = None, thread: str | None = None, *,
             session: str | None = None, kept: bool = True) -> None:
        """Something the session made that no other event files (a bookmark, a snapshot, held footage, an individual,
        a dataset, a screen recording): journaled, so the session claims it (catalogue `Info.claims`: a dataset and a
        snapshot are its sources, ADR 0022), its list counts the products among them as Made in this session lists them
        (catalogue.product_of), and its id is never given again. `kept` False: held in memory only (footage held for a researcher), gone at a
        restart, so the list never counts it. `session`: the session it was made in, when that is another one (filed
        there; here when it can't be found)."""
        with self._filing(session) as store:
            store._record({"type": "made", "kind": kind, "id": pid, "day": day or self.date,
                           "thread": thread if thread in store.threads else store.active_id, **({} if kept else {"kept": False})})

    def _apply(self, e: dict, *, replay: bool = False) -> None:
        kind, at = e["type"], float(e.get("at") or self.clock())
        day = day_of(e)
        if day:
            self._days.add(day)
        for prefix, number in ids_in(e):
            self.ids_used[prefix] = max(self.ids_used.get(prefix, 0), number)
        if e.get("at"):
            self.created = at if not self.created else min(self.created, at)
        if kind == "session-imported":
            from .ownership import validate
            meta = validate(e.get("owner"), self.sid)["metadata"]
            self.name, self.named, self.created = meta["name"], meta["named"], meta["created"]
            self.local_only = meta["localOnly"]
        elif kind == "session-started":
            self.local_only = e.get("localOnly") is True
            self.guide = e.get("guide")
            self.name = " ".join(str(e.get("name") or "").split())[:80]
            self.named = bool(self.name) and e.get("by") == "user"
            self.started_from = e.get("from")
            if e.get("created"):
                self.created = float(e["created"])
        elif kind == "session-renamed":
            self.name = " ".join(str(e.get("name") or "").split())[:80]
            self.named = bool(self.name) and not e.get("auto")
            removed = (e.get("because") or {}).get("removed") if isinstance(e.get("because"), dict) else None
            if removed:
                self.name_before_removal[str(removed)] = str(e["because"].get("was") or "")
            else:
                self.name_before_removal.clear()  # renamed since: Undo no longer puts an earlier name back
        elif kind == "session-suggested":
            self.suggestion = {"specimen": str(e.get("specimen") or "")[:80], "hours": e.get("hours")}
        elif kind == "session-kept":
            self.suggestion = None
            if e.get("specimen"):
                self.kept_specimens.add(str(e["specimen"]).lower())
        elif kind == "opened":
            self.suggestion = None  # a suggestion is of the moment it was made: not carried to a later opening
        elif kind == "thread-opened":
            n = int(e.get("n") or len(self.threads) + 1)
            self.threads[e["thread"]] = Thread(e["thread"], n, title=e.get("title") or "",
                                               auto_title=not e.get("title"), started=at, last=at)
            self.active_id = e["thread"]
        elif kind == "thread-switched":
            if e["thread"] in self.threads:
                self.active_id = e["thread"]
                self.threads[e["thread"]].last = at
        elif kind == "thread-renamed":
            thread = self.threads[e["thread"]]
            thread.title, thread.auto_title = e.get("title") or "", bool(e.get("auto"))
        elif kind == "turn":
            thread = self.threads[e["thread"]]
            original = e.get("asked")
            if isinstance(original, dict):
                # Only a writer's positive ownership proof can rebase an origin.
                # Stale or unmarked origins retain their facts without gaining
                # authority over a later load's same-numbered conversation.
                was_current = e.get("asked_current") is True
                asked = Asked(original["thread"], self.generation if replay and was_current else original["generation"],
                              day=original.get("day"), stale=not was_current, session=original.get("session"))
            else:
                # Legacy journal turns have this load's ownership, but no
                # retained proof of their original day. Do not stamp today.
                asked = Asked(thread.id, self.generation)
            turn = Turn(int(e["id"]), e["role"], e["channel"], e["text"], at=at, frame_id=e.get("frame_id"),
                        refs=e.get("refs") or {}, thread=thread.id, asked=asked)
            thread.turns.append(turn)
            self.last_turn = turn
            if turn.role == "user":
                self.last_user = max(self.last_user, at)
            thread.last = at
            self.seq = max(self.seq, turn.id)
        elif kind == "seq":  # the session was opened afresh: turn ids keep counting (late updates name old ids)
            self.seq = max(self.seq, int(e.get("seq") or 0))
        elif kind == "turn-edited":
            thread = self.threads[e["thread"]]
            for turn in reversed(thread.turns):
                if turn.id == int(e["id"]):
                    turn.text = e["text"]
                    break
        elif kind == "subject":
            self._subject_events.append(e)
            if e.get("source") in self.removed_sources:
                return  # seen only on a source the user took out of the session
            self._apply_subject(e, at)
        elif kind == "source":
            self._apply_source(e, at)
        elif kind == "layer-shown":
            self.layer_shown = str(e.get("population") or "") or None
        elif kind == "summary":
            thread = self.threads[e["thread"]]
            thread.summary, thread.summary_upto = e.get("text") or "", int(e.get("upto") or 0)
        elif kind == "digest":
            self.digest, self.digest_at = e.get("text") or "", at
        elif kind == "preference":
            if e["key"] in SESSION_KEYS:  # the user's are in preferences.jsonl: an older journal's copy is ignored
                self.prefs[e["key"]] = e.get("value")
        elif kind == "line":
            thread = self.threads[e["thread"]]
            line, lid = e["line"], e["line"].get("lineId")
            # A spoken line is filed when it begins and again whole (dialogue.py, brain/speech.py): the same
            # lineId replaces the line in place, so it keeps its place in the conversation.
            at_ = next((i for i in range(len(thread.lines) - 1, -1, -1) if thread.lines[i].get("lineId") == lid), None) if lid else None
            if at_ is not None:
                thread.lines[at_] = line
            else:
                thread.lines.append(line)
                del thread.lines[: max(0, len(thread.lines) - LINES_KEPT)]
        elif kind == "quiz-outcome":
            self.outcomes.setdefault(e["rid"], {})[e["step"]] = {"outcome": e["outcome"], "by": e.get("by") or "", "at": at}
        elif kind == "evidence-saved":
            self.evidence[e["id"]] = {"path": e["path"], "at": at, "thread": e.get("thread"), "revision": e.get("revision", 0),
                                      "members": e.get("members") or [],
                                      **({"manifestSha256": e["manifestSha256"]} if "manifestSha256" in e else {})}
        elif kind == "evidence-made":
            self.evidence_ids[e["id"]] = e.get("lastMember") or ""
        elif kind == "tracks-saved":
            self.tracks[e["id"]] = {"path": e["path"], "thread": e.get("thread"),
                                      **({"manifestSha256": e["manifestSha256"]} if "manifestSha256" in e else {})}
            self.tracks_ids.add(e["id"])
        elif kind == "tracks-made":
            self.tracks_ids.add(e["id"])
        elif kind in ("workspace-layout", "workspace-arrangement", "workspace-completion"):
            self.workspace_layout = dict(e)
            if kind == "workspace-arrangement":
                if e['action'] == 'remove': self.workspace_removed[e['tab']] = dict(e['removed'])
                else: self.workspace_removed.pop(e['tab'], None)
            if kind == "workspace-completion": self.workspace_completions[e['completion']['id']] = dict(e['completion'])
        elif kind == "workspace-origin":
            self.workspace_origins[e['origin']['run']] = dict(e['origin'])
        elif kind == "annotation":
            self.annotations.setdefault(e["id"], []).append(dict(e))
        elif kind == "table-derivative":
            self.table_derivatives.setdefault(e["id"], []).append(dict(e))
        elif kind == "datatable":
            self.datatables.setdefault(e["id"], []).append(dict(e))
        elif kind == "result":
            self.results[e["id"]] = {k: v for k, v in e.items() if k != "type"}
        elif kind == "research-linked":
            thread = self.threads.get(e.get("thread") or self.active_id)
            if thread is not None and e["rid"] not in thread.research:
                thread.research.append(e["rid"])

    # --- threads -------------------------------------------------------------------------
    def _apply_subject(self, e: dict, at: float) -> None:
        thread = self.threads[e["thread"]]
        name = e["name"]
        thread.last = max(thread.last, at)
        if not thread.subjects or thread.subjects[-1]["name"] != name:
            thread.subjects.append({"name": name, "named_by": e.get("named_by") or "look", "at": at,
                                    "carried": bool(e.get("carried")), **({"source": e["source"]} if e.get("source") else {})})
        elif e.get("named_by") == "user":
            thread.subjects[-1]["named_by"] = "user"
        seen = self.subjects.setdefault(name, {"name": name, "named_by": e.get("named_by") or "look", "first": at,
                                               "last": at, "threads": []})
        seen["last"] = at
        if e.get("named_by") == "user":
            seen["named_by"] = "user"
        if thread.id not in seen["threads"]:
            seen["threads"].append(thread.id)

    def _apply_source(self, e: dict, at: float) -> None:
        """An asset opened in the session (it is the session's from then on, and on stage), removed from it by the
        user, or put back (Undo). Removing or restoring one applies the subjects again, so a removed source's
        specimens leave the session (its list, its conversations' subjects, the digest's facts) and come back with it."""
        aid, op = str(e.get("asset") or ""), e.get("op")
        if not aid:
            return
        before = set(self.removed_sources)
        if op == "opened":
            self.sources.pop(aid, None)
            self.sources[aid] = at  # newest last
            self.removed_sources.discard(aid)
            self.on_stage = aid
        elif op == "removed" and aid != "live":
            self.removed_sources.add(aid)
        elif op == "restored":
            self.removed_sources.discard(aid)
        elif op == "at" and isinstance(e.get("seconds"), (int, float)):
            self.positions[aid] = float(e["seconds"])
        if self.removed_sources != before:
            self._reapply_subjects()

    def _reapply_subjects(self) -> None:
        for thread in self.threads.values():
            thread.subjects = []
        self.subjects = {}
        for e in self._subject_events:
            if e.get("source") not in self.removed_sources and e.get("thread") in self.threads:
                self._apply_subject(e, float(e.get("at") or self.clock()))

    # --- its sources (library.py; ADR 0022) -----------------------------------------------------
    def open_source(self, aid: str, by: str = "user") -> None:
        """An asset (or "live") put on stage: the session's from then on. Opening a removed one brings it back."""
        self._record({"type": "source", "asset": aid, "op": "opened", "by": by})

    def record_layer(self, eid: str | None) -> None:
        """The population the cells' layer shows now in this session (None: none), journaled when it changes. Hiding
        it because another session opened is not a change in this one (population.py Layer.journal), so a session
        reopened knows what its layer showed when it was left."""
        eid = str(eid or "") or None
        if eid != self.layer_shown:
            self._record({"type": "layer-shown", "population": eid})

    def remove_source(self, aid: str, by: str = "user") -> bool:
        """The user takes an asset out of the session (the library keeps it, and what was made from it stays as the
        session's history). False when it is not one of its sources, or is the live camera."""
        if aid == "live" or aid in self.removed_sources:
            return False
        self._record({"type": "source", "asset": aid, "op": "removed", "by": by})
        # The app named the session after a specimen seen only on the source taken out: the name goes with it, to the
        # next specimen the session shows, else none (its date, or "Untitled session"). A name the user gave stays.
        if not self.named and self.name and self.name.lower() not in {n.lower() for n in self.subjects}:
            following = next(iter(self.subjects), "")
            self._record({"type": "session-renamed", "name": following[:1].upper() + following[1:], "by": "app",
                          "auto": True, "because": {"removed": aid, "was": self.name}})
        return True

    def source_left_at(self, aid: str, seconds: float) -> None:
        """Where a recording was when it left the stage (another source opened, or another session): a returning
        session lands there."""
        if aid and aid != "live" and seconds >= 0:
            self._record({"type": "source", "asset": aid, "op": "at", "seconds": round(float(seconds), 2)})

    def restore_source(self, aid: str, by: str = "user") -> bool:
        if aid not in self.removed_sources:
            return False
        self._record({"type": "source", "asset": aid, "op": "restored", "by": by})
        was = self.name_before_removal.get(aid)
        if was and not self.named and self.name != was:  # its removal took the app's name: Undo puts it back
            self._record({"type": "session-renamed", "name": was, "by": "app", "auto": True})
        return True

    def open_thread(self, title: str = "") -> Thread:
        """A new conversation (the user asked for one). What is in view carries over as its
        first subject, so "the same slide" still means something."""
        carried = self.threads[self.active_id].subject if self.active_id in self.threads else ""
        n = len(self.threads) + 1
        tid = f"t{n}"
        while tid in self.threads:
            n += 1
            tid = f"t{n}"
        self._record({"type": "thread-opened", "thread": tid, "n": n, "title": title.strip()[:60]})
        if carried:
            named_by = self.subjects.get(carried, {}).get("named_by", "look")
            source = (self.threads[self.active_id].subjects[-1].get("source") if self.active_id in self.threads
                      and self.threads[self.active_id].subjects else None)
            self._record({"type": "subject", "thread": tid, "name": carried, "named_by": named_by, "carried": True,
                          **({"source": source} if source else {})})
        return self.active

    def switch(self, tid: str) -> Thread:
        if tid not in self.threads:
            raise KeyError(tid)
        if tid != self.active_id:
            self._record({"type": "thread-switched", "thread": tid})
        return self.active

    def rename(self, tid: str, title: str, auto: bool = False) -> None:
        if tid in self.threads:
            self._record({"type": "thread-renamed", "thread": tid, "title": title.strip()[:60], "auto": auto})

    def find_thread(self, ref: str) -> Thread | None:
        """A thread from how people refer to one: "t2", "2", its title or a subject in it
        ("the blood smear one"), "the last one"/"previous"."""
        text = str(ref or "").strip().lower()
        if not text:
            return None
        if text in self.threads:
            return self.threads[text]
        digits = "".join(c for c in text if c.isdigit())
        if digits and f"t{digits}" in self.threads and len(text) <= 12:
            return self.threads[f"t{digits}"]
        others = sorted((t for t in self.threads.values() if t.id != self.active_id), key=lambda t: (t.last, t.n), reverse=True)
        if any(w in text for w in ("previous", "last one", "before", "earlier one", "other one")) and others:
            return others[0]
        words = {w for w in "".join(c if c.isalnum() else " " for c in text).split() if len(w) > 2}
        words -= {"the", "one", "thread", "conversation", "topic", "back", "about", "with", "that", "chat"}

        def score(t: Thread) -> int:
            # Its title and what it is about now count double; subjects it only began with
            # (carried over when it was opened) or passed through, and its summary, once.
            own = [x["name"] for x in t.subjects if not x.get("carried")]  # not just what was in view when it began
            main = " ".join([t.title, own[-1] if own else ""]).lower()
            rest = " ".join([*own[:-1], t.summary[:400]]).lower()
            return sum(2 if w in main else 1 if w in rest else 0 for w in words)

        ranked = sorted(self.threads.values(), key=lambda t: (score(t), t.last), reverse=True)
        return ranked[0] if ranked and score(ranked[0]) > 0 else None

    # --- turns ---------------------------------------------------------------------------
    def add_turn(self, role: str, channel: str, text: str, *, frame_id: int | None = None, refs: dict | None = None,
                 asked: Asked | None = None, rid: str | None = None, verbatim: bool = False,
                 filing_asked: Asked | None = None) -> Turn:
        """File a turn; genuine model feedback may retain its text and original
        Asked while a reopened earlier journal supplies its own filing token. A turn of a request asked in another
        session is filed in that session (`elsewhere`)."""
        text = text if verbatim else text.strip()
        filing = filing_asked if filing_asked is not None else asked
        other = self._other_session(filing)
        if other is not None:
            return other.add_turn(role, channel, text, frame_id=frame_id, refs=refs, asked=asked, rid=rid,
                                  verbatim=True, filing_asked=filing_asked)
        tid = self.thread_for(filing, rid)
        original = asked if asked is not None else Asked(tid, self.generation, day=self.sid, session=self.sid)
        # Its origin is this session's own (a request of an earlier load of it too: its conversations keep their ids
        # for good) or, for an origin from before sessions, this load's.
        asked_current = original.thread in self.threads and (
            original.session == self.sid if original.session else self.current(original.routing_generation))
        if not text:
            retained = Asked(original.thread, original.generation, day=original.day, stale=not asked_current,
                             session=original.session)
            return Turn(0, role, channel, "", frame_id=frame_id, refs=refs or {}, thread=tid, asked=retained)  # type: ignore[arg-type]
        self._record({"type": "turn", "thread": tid, "id": self.seq + 1, "role": role, "channel": channel, "text": text,
                      "frame_id": frame_id, "refs": _jsonable(refs or {}),
                      "asked": {"thread": original.thread, "generation": original.generation, "day": original.day,
                                **({"session": original.session} if original.session else {})},
                      "asked_current": asked_current})
        target = self.threads[tid]
        if role == "user" and target.auto_title and not target.title:
            self.rename(tid, auto_title(text), auto=True)
        return target.turns[-1]

    def add_line(self, line: dict, asked: Asked | None = None, rid: str | None = None) -> dict:
        """A chat line as shown (dialogue.py), filed with its conversation (in the session it was asked in);
        returns it with its "thread" (and its "session" when that is another one)."""
        other = self._other_session(asked)
        if other is not None:
            return {**other.add_line(line, asked, rid), "session": other.sid}
        tid = self.thread_for(asked, rid)
        line = {**line, "thread": tid}
        self._record({"type": "line", "thread": tid, "line": line})
        return line

    def _other_session(self, asked: Asked | None) -> "SessionStore | None":
        """The store of the other session `asked` was made in, or None (this one, or an origin without a session).
        A session that can't be found (its folder gone) is None too: the work is filed here, never lost."""
        sid = getattr(asked, "session", None)
        if not sid or sid == self.sid:
            return None
        try:
            return self.elsewhere(sid)
        except KeyError:
            return None

    def edit_turn(self, turn: Turn, text: str) -> None:
        if turn.id and turn.text != text.strip():
            self._record({"type": "turn-edited", "thread": turn.thread or self.active_id, "id": turn.id, "text": text.strip()})

    # --- what the session knows ----------------------------------------------------------
    def set_subject(self, name: str, named_by: str = "look", thread: str | None = None) -> None:
        name = " ".join(str(name).split())[:80]
        tid = thread if thread in self.threads else self.active_id
        current = self.threads[tid].subjects[-1] if self.threads[tid].subjects else None
        if not name or (current and current["name"] == name and (named_by != "user" or current["named_by"] == "user")):
            return
        # Seen on the source on stage (an asset, or "live"): removing that asset from the session takes it back out.
        source = self.on_stage
        self._record({"type": "subject", "thread": tid, "name": name, "named_by": named_by, **({"source": source} if source else {})})

    def set_summary(self, tid: str, text: str, upto: int, generation: int | None = None) -> None:
        if tid in self.threads and self.current(generation):
            self._record({"type": "summary", "thread": tid, "text": text.strip()[:2000], "upto": int(upto)})

    def set_digest(self, text: str, generation: int | None = None) -> None:
        if text.strip() != self.digest and self.current(generation):
            self._record({"type": "digest", "text": text.strip()[:1500]})

    def set_pref(self, key: str, value) -> None:
        """Keep a preference when it is new or changed: the session's in its journal, the user's in
        preferences.jsonl (prefs.py), whose change listeners hear as the same "preference" event."""
        if key in SESSION_KEYS:
            if key not in self.prefs or self.prefs[key] != value:
                self._record({"type": "preference", "key": key, "value": value})
            return
        event = self.user_prefs.set(key, value)
        if event is None:
            return
        self.prefs[key] = value
        if getattr(self, "_quiet", False):
            return
        for listener in list(self.listeners):
            try:
                listener(event)
            except Exception:
                pass

    def record_outcome(self, rid: str, step: str, outcome: str, by: str = "brain", *, asked: Asked | None = None) -> None:
        """How the user did on a lesson step (quiz.py OUTCOMES: right, wrong, skipped), as the model
        judged it: `by` "click" (a judged click on a find step) or "brain" (mark_step)."""
        from ..quiz import OUTCOMES

        if outcome not in OUTCOMES:
            raise ValueError(f"an outcome is one of {', '.join(OUTCOMES)}")
        other = self._other_session(asked)
        if other is not None:
            return other.record_outcome(rid, step, outcome, by, asked=asked)
        self._record({"type": "quiz-outcome", "rid": str(rid), "step": str(step), "outcome": outcome, "by": by,
                      "thread": self.thread_for(asked)})

    def outcomes_of(self, rid: str | None) -> dict[str, str]:
        """{step id: its latest outcome} for a researcher's lesson today."""
        return {step: o["outcome"] for step, o in self.outcomes.get(str(rid), {}).items()} if rid else {}

    @contextmanager
    def _filing(self, session: str | None):
        """The store work of `session` is filed in (`_session_store`), held until it is written: a session opening on
        another thread meanwhile waits, so the write never lands in the session opened after it was chosen."""
        with self._switching:
            yield self._session_store(session)

    def _session_store(self, session: str | None) -> "SessionStore":
        """The store work of `session` is filed in: this one, or the other session's (`elsewhere`); this one when
        that session can't be found, so nothing is lost."""
        try:
            return self.elsewhere(session)
        except KeyError:
            return self

    def record_evidence(self, eid: str, path: str, thread: str | None, revision: int, members: list[str], *,
                        day: str | None = None, session: str | None = None,
                        manifestSha256: str | None = None) -> None:
        """A population saved (evidence.py): filed in its session's journal with the date folder it is in (`day`),
        so a restart reads it back (ADR 0001)."""
        # `thread` as given: None is filed under the session (asked in no conversation of it), never a new t1.
        with self._filing(session) as store:
            store._record({"type": "evidence-saved", "id": eid, "path": path, "thread": thread, "day": day or self.date,
                           "revision": revision, "members": members,
                           **({"manifestSha256": manifestSha256} if manifestSha256 else {})}, strict=True)

    def record_annotation(self, source: dict, text: str, title: str = "", *, annotation_id: str = "", creation_id: str = "",
                          asked: Asked | None = None) -> dict:
        """A user observation, durable before success; display naming never confirms an identification."""
        import copy
        import uuid

        other = self._other_session(asked)
        if other is not None:
            return other.record_annotation(source, text, title, annotation_id=annotation_id, creation_id=creation_id, asked=asked)
        if asked is not None and not asked.session and not self.current(asked.routing_generation):
            raise ValueError("the session changed before the note could be saved")
        if creation_id and (creation_id in self.annotations or annotation_id):
            raise ValueError('that note identity already exists')
        previous = self.annotations.get(annotation_id, []) if annotation_id else []
        if annotation_id and not previous:
            raise ValueError("that annotation is not saved in this session")
        if previous and source != previous[-1]["source"]:
            raise ValueError("an annotation's source cannot change; create a separate note")
        if (not previous and len(self.annotations) >= 1000) or len(previous) >= (10000 if source.get("scope") == "session" else 100):
            raise ValueError("the session's annotation limit was reached")
        if not (text.strip() or title.strip()) or len(text) > 3000 or len(title) > 80:
            raise ValueError("give a note up to 3000 characters and a display title up to 80 characters")
        return self._record({"type": "annotation", "id": annotation_id or creation_id or "a" + uuid.uuid4().hex[:12],
                             "version": len(previous) + 1, "thread": self.thread_for(asked),
                             "source": copy.deepcopy(source), "text": text, "title": title,
                             "by": "user", "status": "tentative user interpretation"}, strict=True)

    def new_datatable_id(self) -> str:
        """A dataset table's id (`tb<n>`): new for the session and for its day's folder (ids.py)."""
        from .ids import next_number

        tid = f"tb{next_number(self, 'tb', max((int(t[2:]) for t in self.datatables), default=0))}"
        # Reserve before any await; failed saves never reuse a number (also after restart).
        self._record({'type': 'made', 'kind': 'dataset-table', 'id': tid, 'kept': False,
                      'day': self.date, 'thread': self.active_id}, strict=True)
        return tid

    def record_datatable(self, record: dict) -> dict:
        """A dataset table's version (datatables.py), durable before success: its rows are in the snapshot file it
        names with their SHA-256; a version is never edited, the next one is journaled after it."""
        import copy

        previous = self.datatables.get(record["id"], [])
        if record["version"] != (previous[-1]["version"] + 1 if previous else 1):
            raise ValueError(f"{record['id']} is at version {previous[-1]['version'] if previous else 0}")
        if not previous and len(self.datatables) >= 1000:
            raise ValueError("the session's dataset table limit was reached")
        return self._record({"type": "datatable", **copy.deepcopy(record), "thread": record.get("thread", self.active_id), "day": self.date},
                            strict=True)

    def reserve_table_derivative_id(self, kind: str) -> str:
        from .ids import next_number
        prefix = {'analysis': 'an', 'graph': 'gr'}[kind]
        rid = f"{prefix}{next_number(self, prefix)}"
        self._record({'type': 'made', 'kind': kind, 'id': rid, 'kept': False,
                      'day': self.date, 'thread': self.active_id}, strict=True)
        return rid

    def record_table_derivative(self, record: dict) -> dict:
        previous = self.table_derivatives.get(record['id'], [])
        if record['version'] != (previous[-1]['version'] + 1 if previous else 1):
            raise ValueError('analysis or graph revision changed')
        if not previous and len(self.table_derivatives) >= 1000:
            raise ValueError('the Project analysis/graph limit was reached')
        return self._record({'type': 'table-derivative', **record, 'day': self.date}, strict=True)

    def record_result(self, entry: dict, *, strict: bool = False) -> None:
        """What a tool measured or made (resources.Results): filed in its session's journal (the entry's "session",
        else this one) with its thread, so the index has it after a restart (ADR 0001); its pictures are files
        beside it (paths.results)."""
        # Its own conversation, or none when it was asked in no conversation of this session.
        with self._filing(entry.get("session")) as store:
            store._record({"type": "result", "thread": entry["thread"] if "thread" in entry else store.active_id,
                           **{k: v for k, v in entry.items() if k != "thread"}}, strict=strict)

    def record_evidence_made(self, eid: str, last_member: str, *, day: str | None = None,
                             session: str | None = None) -> None:
        """A population's ids, journaled when it is made (before it is saved), so no id is ever given
        twice in a session or a day's folder, whatever becomes of its save (scaffolding review, M1.3 item 4)."""
        with self._filing(session) as store:
            store._record({"type": "evidence-made", "id": eid, "lastMember": last_member, "day": day or self.date})

    def record_tracks_made(self, tid: str, *, day: str | None = None, session: str | None = None) -> None:
        """A motion tracks item's id, journaled when it is made (before it is saved): never given twice."""
        with self._filing(session) as store:
            store._record({"type": "tracks-made", "id": tid, "day": day or self.date})

    def record_tracks(self, tid: str, path: str, thread: str | None, *, day: str | None = None,
                      session: str | None = None, manifestSha256: str | None = None) -> None:
        """Motion tracks saved (motiontracks.py): filed in their session's journal, so a restart reads them back."""
        with self._filing(session) as store:  # thread None: under the session
            store._record({"type": "tracks-saved", "id": tid, "path": path, "thread": thread, "day": day or self.date,
                           **({"manifestSha256": manifestSha256} if manifestSha256 else {})},
                          strict=True)

    def link_research(self, rid: str, thread: str | None = None, *, session: str | None = None,
                      day: str | None = None) -> str:
        """A researcher started in a conversation of the session (`session`: the one it was asked in)."""
        with self._filing(session) as store:
            tid = thread if thread in store.threads else store.active_id
            store._record({"type": "research-linked", "thread": tid, "rid": rid, "day": day or self.date})
        return tid

    def thread_of_research(self, rid: str) -> Thread | None:
        return next((t for t in self.threads.values() if rid in t.research), None)

    def wire(self) -> dict:
        label = sids.label(self.sid)
        threads = sorted(self.threads.values(), key=lambda t: t.n)
        return {"type": "day", "id": self.sid, "label": label, "active": self.active_id,
                "threads": [t.wire(self.sid) for t in threads], "subjects": [s["name"] for s in self.subjects.values()],
                "prefs": self.prefs, "localOnly": self.local_only,
                # When this session began: its first conversation's start. "Start today afresh" and midnight begin a new
                # journal and so a new session; a restart reads the same journal back (Made today lists what is newer).
                "sessionStart": min((t.started for t in threads), default=None)}


DayStore = SessionStore  # its old name


def _qualified_output_refs(value) -> list[dict]:
    """A closed receipt shape, not permission to persist arbitrary nested refs.

    Identity is retained whole or refused; unavailable run/capture/turn proof stays
    None. Qualification happens at the real completion write, never on reload by
    looking at today's scene. This projection owns no pixels, prompts or objects.
    """
    if not isinstance(value, (list, tuple)):
        return []
    keys = ("id", "run", "frame", "epoch", "created_at", "turn_id")
    out, seen = [], set()
    integer_max = (1 << 63) - 1
    for row in value:
        if not isinstance(row, dict) or not all(key in row for key in keys):
            continue
        oid, run = row["id"], row["run"]
        if not isinstance(oid, str) or len(oid) > 80 or re.fullmatch(r"[opc][1-9][0-9]*", oid) is None:
            continue
        if run is not None and (not isinstance(run, str) or not run or len(run) > 128):
            continue
        if any(row[key] is not None and (type(row[key]) is not int or not 0 <= row[key] <= integer_max)
               for key in ("frame", "epoch")):
            continue
        turn = row["turn_id"]
        if turn is not None and (type(turn) is not int or not 1 <= turn <= integer_max):
            continue
        at = row["created_at"]
        if type(at) not in (int, float) or not 0 <= at <= integer_max or not math.isfinite(at):
            continue
        identity = tuple(row[key] for key in keys)
        if identity in seen:
            continue
        # Copy only approved scalar fields: even a valid row's private extras are
        # excluded and later mutation of the caller cannot alter the receipt.
        qualified = dict(zip(keys, identity))
        if len(json.dumps([*out, qualified], ensure_ascii=True)) > 8192:
            continue
        out.append(qualified)
        seen.add(identity)
        if len(out) == 8:
            break
    return out


def _scientific_input_ref(value) -> dict:
    """Closed accepted-input receipt; never resolve its identity again from today's Project."""
    from gpu_worker.semantic_resources import parse_descriptor

    if (not isinstance(value, dict) or set(value) != {'channel', 'source', 'revisionDigest', 'scientific', 'sha256'}
            or value['channel'] != 'retained-source'):
        raise ValueError('invalid saved scientific input receipt')
    source = value['source']
    if (not isinstance(source, dict) or set(source) != {'kind', 'session', 'id', 'revision', 'component'}
            or source['kind'] != 'array' or type(source['revision']) is not int or source['revision'] < 1
            or any(not isinstance(source[k], str) or not source[k] or len(source[k]) > 128 for k in ('session', 'id', 'component'))
            or not source['component'].startswith('array.')
            or any(not isinstance(value[k], str) or re.fullmatch('[0-9a-f]{64}', value[k]) is None for k in ('revisionDigest', 'sha256'))):
        raise ValueError('invalid saved scientific input identity')
    parse_descriptor(value['scientific'])
    encoded = json.dumps(value, allow_nan=False)
    if len(encoded) > 16384:
        raise ValueError('saved scientific input receipt exceeds its bound')
    return json.loads(encoded)


def _jsonable(refs: dict) -> dict:
    out = {}
    for key, value in refs.items():
        if key == "scientificInput":
            out[key] = _scientific_input_ref(value)
        elif key == "outputRefs":
            out[key] = _qualified_output_refs(value)
        elif isinstance(value, (str, int, float, bool)) or value is None:
            out[key] = value
        elif isinstance(value, (list, tuple)):
            out[key] = [v for v in value if isinstance(v, (str, int, float, bool))]
    return out
