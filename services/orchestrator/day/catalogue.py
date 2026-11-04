"""The sessions there are (ADR 0017), read from their journals: never a second store.

Each session's journal (`sessions/<sid>/journal.jsonl`) is read once and after that only where it grew: journals are
append-only, so a file that shrank or was replaced is read again from the start. Listing every session then costs a
`stat` per session. The app calls it off the event loop (`asyncio.to_thread`); a lock keeps two readers from folding
the same lines twice.

What it keeps of a session is what the session list shows and what finding one needs: its name, when it began and was
last used, the calendar days it touched, its conversations (titles), what was seen in it, its sources (ADR 0022), its
researchers, what it made (the products Made in this session lists, `produces.is_product`; and every "made" event's
claim, products or not, so a Record dataset is known to be its), and the highest id of each kind it gave on each day
(`used`, for `day/ids.py`). An old day journal has no header: its name is "" (the studio and the words say its
date, `sid.label`) and it began with its first event.
"""
from __future__ import annotations

import datetime
import json
import re
import threading
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

from . import sid as sids

ARCHIVE = "archive"  # sessions/archive/: journals moved aside by "start today afresh" before sessions existed
ID = re.compile(r"(tb|an|gr|[a-z])([0-9]+)")  # one letter, or a dataset table's "tb" (ids.PREFIXES)
ID_FIELDS = ("id", "lastMember")  # an event's own id fields (and "members", "ids": lists of ids)
SUMMARY_CHARS = 600  # of each conversation's running summary, kept for finding a Project by what was said in it


def local_day(at: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(at))


def days_before(now: float, n: int) -> str:
    """The calendar date `n` days before the local date `now` falls on: by the calendar, never n times 24 hours back
    (in the hour after a 23-hour day, when clocks go forward, 24 hours back is two dates back)."""
    return (datetime.date.fromtimestamp(now) - datetime.timedelta(days=n)).isoformat()


def _is_date(value) -> bool:
    """A calendar date "YYYY-MM-DD" (a session id without a number)."""
    return isinstance(value, str) and sids.is_sid(value) and sids.parse(value)[1] == 1


def day_of(event: dict) -> str:
    """The calendar date an event belongs to: its own "day" (a file it names is in that day's folder), else the
    local date of its time; "" with neither."""
    if _is_date(event.get("day")):
        return str(event["day"])
    at = event.get("at")
    return local_day(float(at)) if isinstance(at, (int, float)) and at else ""


def ids_in(event: dict) -> list[tuple[str, int]]:
    """(prefix, number) of every id an event names ("e3", "c40"...): its own id fields and its lists of ids."""
    values = [event.get(k) for k in ID_FIELDS] + list(event.get("members") or []) + list(event.get("ids") or [])
    found = [ID.fullmatch(v) if isinstance(v, str) else None for v in values]
    return [(m.group(1), int(m.group(2))) for m in found if m]


def product_of(event: dict) -> tuple[str, str] | None:
    """(kind, id) when a journal event files a product (CONTEXT.md "Product"; produces.is_product), else None. One
    place, so the session list's "made" and Made in this session count the same things (with `withdrawn_of`, the
    other half of the same rule):
    - "made": a product no other event files (a bookmark, a mission's camera clip, an individual, a screen
      recording), unless held in memory only ("kept": false: footage held for a researcher, gone at a restart). A
      snapshot's and a Record dataset's "made" are no products: they are the session's sources (ADR 0022), claimed by
      it all the same (`Info.claims`);
    - a population saved, motion tracks saved, a researcher started in the session, a dataset table made;
    - a result whose producer made a picture to keep that is no source (produces.PRODUCT_RESULTS: a reference photo),
      unless it was discarded."""
    from ..produces import PRODUCT_RESULTS, is_product

    kind = event.get("type")
    if kind == "made" and event.get("id") and event.get("kept") is not False and is_product(str(event.get("kind") or "")):
        return str(event["kind"]), str(event["id"])
    if kind == "evidence-saved" and event.get("id"):
        return "population", str(event["id"])
    if kind == "tracks-saved" and event.get("id"):
        return "tracks", str(event["id"])
    if kind == "research-linked" and event.get("rid"):
        return "research", str(event["rid"])
    if kind == "table-derivative" and event.get("id") and event.get("kind") in ("analysis", "graph"):
        return event["kind"], str(event["id"])
    if kind == "datatable" and event.get("id"):  # each version names the same table
        return "dataset-table", str(event["id"])
    if (kind == "result" and event.get("id") and event.get("tool") in PRODUCT_RESULTS
            and event.get("state") != "discarded"):
        return "result", str(event["id"])
    return None


def opened_sources(record) -> list[str]:
    """The assets a session opened and did not remove, newest first: never the live camera (always a source). `record`:
    a catalogue Info or the open session's store (the same fields: sources, removed_sources)."""
    opened = dict(getattr(record, "sources", {}) or {})
    removed = set(getattr(record, "removed_sources", set()) or set())
    return [aid for aid, _at in sorted(opened.items(), key=lambda kv: -kv[1]) if aid != "live" and aid not in removed]


def withdrawn_of(event: dict) -> tuple[str, str] | None:
    """(kind, id) when a journal event takes back a product filed before (a result discarded after it was kept: the
    index no longer lists it), else None. The other half of `product_of`'s rule."""
    if event.get("type") == "result" and event.get("id") and event.get("state") == "discarded":
        return "result", str(event["id"])
    return None


@dataclass
class Info:
    """One session as the list shows it and as `find` matches it."""
    id: str
    starter_request: str | None = None
    bootstrap_only: bool = True  # no explicit Project start, opening, or researcher work in its journal
    name: str = ""  # its name; "" until it has one
    named: bool = False  # the user named it (not the app, from its first specimen or request)
    created: float = 0.0
    last_active: float = 0.0  # its newest event
    last_user: float = 0.0  # its newest request (a user turn): "the last session used"
    started_from: str | None = None  # the session it was started from (its header's "from")
    days: list[str] = field(default_factory=list)  # calendar dates its events fall on, oldest first
    titles: dict[str, str] = field(default_factory=dict)  # conversation id -> title
    specimens: list[str] = field(default_factory=list)  # what was seen, first seen first (not on a removed source)
    seen: list[tuple[str, str | None]] = field(default_factory=list)  # every (specimen, source it was seen on)
    sources: dict[str, float] = field(default_factory=dict)  # assets opened in it -> when last opened (ADR 0022)
    source_references: dict[str, dict] = field(default_factory=dict)
    on_stage_reference: dict | None = None
    removed_sources: set[str] = field(default_factory=set)  # those the user took out of it
    on_stage: str | None = None  # what was on stage when it was last used ("live", an asset id)
    positions: dict[str, float] = field(default_factory=dict)  # where each recording was when it left the stage
    researchers: list[str] = field(default_factory=list)  # researcher ids started in it
    made: dict[tuple[str, str], str] = field(default_factory=dict)  # products: (kind, id) -> the day it was made
    # Every "made" event's (kind, id) -> its day, products or not: what its journal says it made (a Record dataset, a
    # snapshot), so the index and the library know whose it is (resources.py `_made`, app.py `origin_of`).
    claims: dict[tuple[str, str], str] = field(default_factory=dict)
    ids: dict[tuple[str, str], int] = field(default_factory=dict)  # (day, prefix) -> the highest number given
    unavailable: str | None = None  # retained locator exists, but its research cannot currently be read
    summaries: dict[str, str] = field(default_factory=dict)  # conversation id -> its latest running summary (find)
    archived: bool = False  # put away by the user (`session-archived`): listed only when asked for, never deleted
    # The Project changes made in it that can be undone (`project-change`, brain/toolbox/projects.py): change id -> its record,
    # with "undone" once undone. One change may span Projects (archiving several): each keeps its own part.
    changes: dict[str, dict] = field(default_factory=dict)

    @property
    def label(self) -> str:
        """What it is called: its name, else "Untitled session" while it began today, else its date (sid.unnamed)."""
        return self.name or sids.unnamed(self.id, self.created, time.time())

    @property
    def counts(self) -> dict:
        """What the list says it holds. `sources`: the assets opened in it and not removed (`opened_sources`); the ones
        that joined on their own (its Record videos, snapshots, clearer pictures) are known only by a scan of the
        library (sources.rows), so the list leaves them out."""
        return {"sources": len(opened_sources(self)), "made": len(self.made), "researchers": len(self.researchers),
                "conversations": len(self.titles)}

    @property
    def picture(self) -> str | None:
        """The asset whose small picture stands for it in the session switcher: what was on stage when it was last used,
        else the newest it added; None when it only ever looked through the live camera (the switcher draws an icon)."""
        opened = opened_sources(self)
        if self.on_stage and self.on_stage in opened:
            return self.on_stage
        return opened[0] if opened else None

    def wire(self) -> dict:
        """A row of the studio's session list (protocol.ts SessionRow; `working` is the app's to add)."""
        row = {"id": self.id, "name": self.name, "named": self.named, "label": self.label, "started": self.created,
               "lastActive": self.last_active, "days": list(self.days), "counts": self.counts,
               "specimens": list(self.specimens)}
        if self.bootstrap_only and self.titles and not self.unavailable:
            row["launcherBootstrap"] = True
        if self.picture:
            row["thumb"] = f"/api/library/{self.picture}/thumbnail"  # the library's own small picture (thumbnails.py)
        if self.unavailable is not None:
            row['storageUnavailable'] = self.unavailable
        if self.archived:
            row["archived"] = True
        return row


@dataclass
class _Read:
    """Where reading a journal got to: its file's identity and the offset of the first byte not yet folded."""
    info: Info
    inode: int = 0
    device: int = 0
    path: str = ''
    offset: int = 0
    size: int = 0
    mtime: float = 0.0


class Catalogue:
    def __init__(self, root: Path | str | None = None, *, locations=None) -> None:
        if root is None:
            from ..paths import Paths

            root = Paths.from_env().sessions
        self.root = Path(root)
        self.locations = locations
        self._reads: dict[str, _Read] = {}
        self._lock = threading.Lock()

    # --- reading -----------------------------------------------------------------------------
    def _sids(self) -> list[str]:
        from ..scientific_import_transaction import require_visible
        require_visible(self.root)
        try:
            names = [p.name for p in self.root.iterdir() if p.name != ARCHIVE and p.is_dir()]
        except OSError:
            names = []
        legacy = {n for n in names if sids.is_sid(n) and (self.root / n / sids.JOURNAL).is_file()}
        return sorted(legacy | (set(self.locations.handles()) if self.locations is not None else set()))

    def _refresh(self, sid: str) -> Info | None:
        from ..scientific_import_transaction import reading
        if self.locations is not None:
            from ..project_roots import ProjectLocationError
            try:
                root = self.locations.root_for(sid)
            except (ProjectLocationError, OSError) as error:
                if sid not in self.locations.handles():
                    raise
                prior = self._reads.get(sid)
                return replace(prior.info if prior else Info(sid), unavailable=str(error))
        else:
            root = self.root
        with reading(root):
            return self._read_journal(sid, root)

    def _read_journal(self, sid, root=None):
        from ..scientific_import_transaction import require_visible
        root = self.root if root is None else root
        require_visible(root)
        path = root / sid / sids.JOURNAL
        try:
            stat = path.stat()
        except OSError as error:
            if self.locations is not None and sid in self.locations.handles():
                prior = self._reads.get(sid)
                return replace(prior.info if prior else Info(sid), unavailable=f'Project journal is unavailable: {error}')
            self._reads.pop(sid, None)
            return None
        read = self._reads.get(sid)
        if (read is None or read.inode != stat.st_ino or read.device != stat.st_dev
                or read.path != str(path) or stat.st_size < read.offset):
            read = self._reads[sid] = _Read(Info(sid), inode=stat.st_ino, device=stat.st_dev, path=str(path))
        if stat.st_size == read.size and stat.st_mtime == read.mtime:
            return read.info
        try:
            with path.open("rb") as f:
                f.seek(read.offset)
                chunk = f.read()
        except OSError as error:
            if self.locations is not None and sid in self.locations.handles():
                return replace(read.info, unavailable=f'Project journal is unreadable: {error}')
            return read.info
        end = chunk.rfind(b"\n") + 1  # whole lines only: a line still being written is read next time
        for line in chunk[:end].splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue  # a line torn by a crash (the journal ends it before its next write)
            if isinstance(event, dict) and event.get("type"):
                try:
                    self._fold(read.info, event)
                except Exception:
                    continue  # one odd event never hides a session
        read.offset += end
        read.size, read.mtime = stat.st_size, stat.st_mtime
        return read.info

    @staticmethod
    def _fold(info: Info, e: dict) -> None:
        kind = e["type"]
        line = e.get("line") if isinstance(e.get("line"), dict) else {}
        automatic = (kind == "session-origin" or
                     (kind == "thread-opened" and e.get("thread") == "t1" and not e.get("title")) or
                     (kind == "line" and line.get("type") == "activity" and
                      str(line.get("id", "")).startswith("catalog-peek:")) or
                     (kind == "line" and line.get("type") == "app-notice" and line.get("event") == "resumed"
                      and line.get("producer") == {"kind":"session", "id":info.id}))
        if not automatic:
            info.bootstrap_only = False
        at = float(e.get("at") or 0.0)
        day = day_of(e)
        if at:
            info.created = at if not info.created else min(info.created, at)
            info.last_active = max(info.last_active, at)
        if day and day not in info.days:
            info.days.append(day)
            info.days.sort()
        if kind == "session-imported":
            from .ownership import validate
            meta = validate(e.get("owner"), info.id)["metadata"]
            info.name, info.named, info.created = meta["name"], meta["named"], meta["created"]
        elif kind == "session-started":
            starter = e.get("researchStarter")
            if isinstance(starter, dict):
                info.starter_request = starter.get("requestId")
            info.name = str(e.get("name") or "")[:80]
            info.named = bool(info.name) and e.get("by") == "user"
            info.started_from = e.get("from")
            if e.get("created"):
                info.created = float(e["created"])
        elif kind == "session-renamed":
            info.name = str(e.get("name") or "")[:80]
            info.named = bool(info.name) and not e.get("auto")
        elif kind == "thread-opened" and e.get("thread"):
            info.titles.setdefault(str(e["thread"]), str(e.get("title") or ""))
        elif kind == "thread-renamed" and e.get("thread"):
            info.titles[str(e["thread"])] = str(e.get("title") or "")
        elif kind == "subject" and e.get("name") and not e.get("carried"):
            info.seen.append((str(e["name"]), e.get("source")))
            if e.get("source") not in info.removed_sources and e["name"] not in info.specimens:
                info.specimens.append(str(e["name"]))
        elif kind == "source" and e.get("asset"):
            aid, before = str(e["asset"]), set(info.removed_sources)
            if e.get("op") in ("opened", "retained"):
                info.sources.pop(aid, None)
                info.sources[aid] = at
                info.removed_sources.discard(aid)
                if e.get('op') == 'opened':
                    info.on_stage = aid
                    info.on_stage_reference = dict(e['retained']) if isinstance(e.get('retained'), dict) else None
                if isinstance(e.get('retained'), dict):
                    info.source_references[aid] = dict(e['retained'])
            elif e.get("op") == "removed" and aid != "live":
                info.removed_sources.add(aid)
            elif e.get("op") == "restored":
                info.removed_sources.discard(aid)
            elif e.get("op") == "at" and isinstance(e.get("seconds"), (int, float)):
                info.positions[aid] = float(e["seconds"])
            if info.removed_sources != before:  # a removed source's specimens are not the session's
                info.specimens = []
                for name, source in info.seen:
                    if source not in info.removed_sources and name not in info.specimens:
                        info.specimens.append(name)
        elif kind == "turn" and e.get("role") == "user" and at:
            info.last_user = max(info.last_user, at)
        elif kind == "research-linked" and e.get("rid") and e["rid"] not in info.researchers:
            info.researchers.append(str(e["rid"]))
        elif kind == "summary" and e.get("thread"):
            info.summaries[str(e["thread"])] = str(e.get("text") or "")[:SUMMARY_CHARS]
        elif kind == "session-archived":
            info.archived = e.get("archived") is not False
        elif kind == "project-change" and isinstance(e.get("change"), str) and isinstance(e.get("steps"), list):
            info.changes[e["change"]] = {"at": at, "what": str(e.get("what") or ""), "says": str(e.get("says") or ""),
                                         "steps": list(e["steps"]), "by": str(e.get("by") or ""), "undone": False,
                                         "asked": e.get("asked") if isinstance(e.get("asked"), dict) else None}
        elif kind == "project-change-undone" and e.get("change") in info.changes:
            info.changes[e["change"]]["undone"] = True
        if kind == "made" and e.get("id") and e.get("kind"):
            info.claims.setdefault((str(e["kind"]), str(e["id"])), day)
        made = product_of(e)
        if made is not None:
            info.made.setdefault(made, day)
        withdrawn = withdrawn_of(e)
        if withdrawn is not None:
            info.made.pop(withdrawn, None)
        if day:
            for prefix, number in ids_in(e):
                info.ids[(day, prefix)] = max(info.ids.get((day, prefix), 0), number)

    # --- what it answers ---------------------------------------------------------------------
    def list(self) -> list[Info]:
        """Every session, the most recently active first."""
        from ..scientific_import_transaction import reading
        with self._lock, reading(self.root):
            infos = [i for i in (self._refresh(s) for s in self._sids()) if i is not None]
            for gone in set(self._reads) - {i.id for i in infos}:
                self._reads.pop(gone, None)
        return sorted(infos, key=lambda i: (i.last_active, sids.sort_key(i.id)), reverse=True)

    def get(self, sid: str) -> Info | None:
        if not sids.is_sid(sid):
            return None
        with self._lock:
            return self._refresh(sid)

    def last_used(self) -> Info | None:
        """The session the user last made a request in; with no request anywhere, the most recently active."""
        infos = [info for info in self.list() if info.unavailable is None]
        asked = [i for i in infos if i.last_user]
        return max(asked, key=lambda i: i.last_user) if asked else (infos[0] if infos else None)

    def used(self, day: str, prefix: str) -> int:
        """The highest number of an id with `prefix` that any session gave on calendar `day` (0: none): a new id on
        that day takes a higher one, so two sessions never share an id in one day's folder (day/ids.py)."""
        infos = self.list()
        if any(i.unavailable is not None for i in infos):
            from ..project_roots import ProjectLocationError
            raise ProjectLocationError('A Project is unavailable; reconnect it before allocating shared legacy result IDs')
        return max((i.ids.get((day, prefix), 0) for i in infos), default=0)

    def find(self, ref: str, *, current: str | None = None, now: float | None = None) -> list[Info]:
        """The sessions `ref` may mean, best first ([] when none): `rank`'s answer as sessions. One clear match is the
        only one returned (switch_session opens it); when unsure, those matching every word (else the partial ones),
        best first, for the model to ask which."""
        ranked = self.rank(ref, current=current, now=now)
        sure = [m.info for m in ranked.matches if m.sure]
        full = [m.info for m in ranked.matches if m.full]
        return sure[:1] if sure else full or [m.info for m in ranked.matches]

    def rank(self, ref: str, *, current: str | None = None, now: float | None = None) -> "Ranked":
        """The sessions `ref` may mean, best first, each with why: its id; "previous" (the most recent other than
        `current`); a time ("2026-10-11", "Oct 11", "yesterday", a weekday within the last week, "last week", "this
        month", "in October": `span_in`) narrowing to the sessions that touched it; and words matched against its name
        and conversations' titles (3 each), what was seen in it (2) and its conversations' summaries (1), a plural or
        singular alike ("leaves" is "leaf").

        Those that match every word in their name, conversations or specimens (`full`) come first, then the partial
        ones (a word only in a summary, or not every word). One full match alone, in the time named, is `sure` (`find`
        returns it alone); otherwise nothing decides among them: the model asks. When a time was named and nothing in it matches the words, the
        matches outside it are given, each `inSpan` False, so "the leaf one from last week" said of one from the week
        before is still found and said to be from then. A reading of words and dates, not of what was meant."""
        text = " ".join(str(ref or "").lower().split())
        if not text:
            return Ranked([], None, [])
        infos = self.list()
        exact = [i for i in infos if i.id == text]
        if exact:
            return Ranked([Match(exact[0], sure=True)], None, [])
        # A session called exactly that, and only one: it (the partner bench of 2026-10-29: "Open the Nuclei project"
        # also matched another session that had looked at nuclei, and nothing was opened).
        named = [i for i in infos if " ".join(str(i.name or "").lower().split()) == text]
        if len(named) == 1:
            return Ranked([Match(named[0], sure=True)], None, [])
        others = [i for i in infos if i.id != current]
        if text in ("previous", "the previous one", "the last one", "last one", "the other one", "before"):
            return Ranked([Match(i, sure=True) for i in others[:1]], None, [])
        span = span_in(text, now if now is not None else time.time())
        words = sorted({w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2} - _NOT_NAMES - _span_words(span))
        if current and re.search(r"\b(?:other|another|different)\b", text):
            infos = [i for i in infos if i.id != current]  # "the other leaf one": not the one open now
        if not words:
            pool = [Match(i, in_span=True) for i in infos if span is not None and span.holds(i)]
            return Ranked(pool, span, [])
        # The Project open now by its name and what was seen in it only: its conversations hold the very request being
        # asked (live, 3 Nov: "what Projects do I have on guard cells" matched the open one by its own question's title).
        scored = [m for m in (_match(i, words, own=i.id == current) for i in infos) if m.score > 0]
        if span is not None:
            for m in scored:
                m.in_span = span.holds(m.info)
            inside = [m for m in scored if m.in_span]
            # Only a full match in the time decides it; else what matches only in part inside it is given with the full
            # matches outside it (review of #189: "the onion one from last week" opened a Project whose summary named
            # onions, from last week, over Onion skin, from the week before).
            scored = inside if any(m.full for m in inside) else inside + [m for m in scored if m.full and not m.in_span]
            if not scored:
                scored = [m for m in (_match(i, words, own=i.id == current) for i in infos) if m.score > 0]
                for m in scored:
                    m.in_span = False
        scored.sort(key=lambda m: (m.full, m.score, m.info.last_active), reverse=True)
        full = [m for m in scored if m.full]
        if len(full) == 1 and full[0].in_span is not False:
            full[0].sure = True
        return Ranked(scored, span, words)


@dataclass
class Match:
    """One session `rank` found, with why: the words of the reference it matched and where (name, conversation,
    specimen, summary), whether it matched them all outside its summaries (`full`), and whether it touched the time named
    (`in_span`: None when none was named)."""
    info: Info
    score: int = 0
    matched: dict[str, str] = field(default_factory=dict)  # word -> where it matched best
    full: bool = False
    in_span: bool | None = None
    sure: bool = False


@dataclass
class Ranked:
    matches: list[Match]
    span: "Span | None"
    words: list[str]  # the words of the reference that name it, not a time or the asking

    @property
    def sure(self) -> "Match | None":
        """The one match meant beyond doubt, else None."""
        return next((m for m in self.matches if m.sure), None)


@dataclass(frozen=True)
class Span:
    """A run of calendar dates a reference names, first and last included ("YYYY-MM-DD"), and how it was said."""
    first: str
    last: str
    said: str  # the words it was read from ("last week")

    def holds(self, info: Info) -> bool:
        return any(self.first <= day <= self.last for day in info.days)

    @property
    def words(self) -> str:
        """The span in the words the model is told: "Oct 26 to Nov 1", or one date."""
        def short(day: str) -> str:
            date = datetime.date.fromisoformat(day)
            return f"{date.strftime('%b')} {date.day}"
        return short(self.first) if self.first == self.last else f"{short(self.first)} to {short(self.last)}"


WEIGHTS = {"name": 3, "conversation": 3, "specimen": 2, "summary": 1}


def stem(word: str) -> str:
    """A word as plural and singular are matched: "leaves" and "leaf", "cells" and "cell", "stomata" and "stoma",
    "nuclei" and "nucleus", "studies" and "study". A light rule, never a dictionary."""
    w = word.lower()
    if len(w) <= 3:
        return w
    for end, to in (("ies", "y"), ("ves", "f"), ("ata", "a"), ("ches", "ch"), ("shes", "sh"), ("sses", "ss"),
                    ("xes", "x")):
        if w.endswith(end) and len(w) > len(end) + 1:
            return w[:-len(end)] + to
    if w.endswith("i") and len(w) > 4:
        return w[:-1] + "us"
    if w.endswith("s") and not w.endswith(("ss", "us", "is")):
        return w[:-1]
    return w


def _stems(text: str) -> set[str]:
    return {stem(t) for t in re.findall(r"[a-z0-9]+", text.lower())}


def _match(info: Info, words: list[str], own: bool = False) -> Match:
    fields = {"name": info.name, "conversation": "" if own else " ".join(info.titles.values()),
              "specimen": " ".join(info.specimens), "summary": "" if own else " ".join(info.summaries.values())}
    stems = {where: _stems(value) for where, value in fields.items()}
    lowered = {where: value.lower() for where, value in fields.items()}
    match = Match(info)
    for word in words:
        s = stem(word)
        for where in ("name", "conversation", "specimen", "summary"):
            # Whole words, plural or singular; a longer word also inside one ("stomat" of "stomatal"), never a short
            # one ("test" is not in "latest").
            if s in stems[where] or (len(word) >= 5 and (word in lowered[where]
                                                         or any(t.startswith(s) for t in stems[where]))):
                match.matched[word] = where
                match.score += WEIGHTS[where]
                break
    match.full = bool(words) and all(match.matched.get(w) in ("name", "conversation", "specimen") for w in words)
    return match


# Words that name the asking, not a session ("go back to yesterday's leaf session": "leaf" is what it is called).
_NOT_NAMES = {"the", "one", "session", "sessions", "back", "about", "with", "that", "this", "from", "earlier", "our",
              "today", "yesterday", "morning", "afternoon", "evening", "night", "last", "previous", "project", "go",
              "and", "where", "were", "was", "what", "did", "find", "found", "looked", "looking", "january",
              "february", "march", "april", "june", "july", "august", "september", "october", "november", "december",
              "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "monday", "tuesday",
              "wednesday", "thursday", "friday", "saturday", "sunday", "mon", "tue", "tues", "wed", "thu", "thur",
              "thurs", "fri", "sat", "sun",
              # times and the asking for Projects ("what Projects do I have on guard cells", "the one from last week")
              "projects", "week", "weeks", "month", "months", "year", "days", "day", "ago", "past", "recent",
              "recently", "open", "have", "any", "which", "all", "ones", "show", "list", "some", "few", "couple", "two",
              "three", "four", "five", "six", "seven", "for", "please", "can", "you", "are", "there", "those", "these",
              "them", "its", "into", "out", "made", "had", "since", "during", "before", "after", "other", "another",
              "different", "tonight", "weekend", "weekends"}
_MONTHS = {m: n for n, names in enumerate((("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
                                           ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
                                           ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
                                           ("dec", "december")), 1) for m in names}
_FULL_MONTHS = {"january", "february", "march", "april", "june", "july", "august", "september", "october", "november",
                "december"}  # said alone, a month ("in October"); "may" and the short forms only after "in", "from"...
_MONTH_AFTER = {"in", "from", "during", "of", "since", "early", "late", "mid"}
_WEEKDAYS = {w: n for n, names in enumerate((("mon", "monday"), ("tue", "tues", "tuesday"), ("wed", "wednesday"),
                                             ("thu", "thur", "thurs", "thursday"), ("fri", "friday"),
                                             ("sat", "saturday"), ("sun", "sunday"))) for w in names}
_COUNTS = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10, "fourteen": 14}


def _span_words(span: "Span | None") -> set[str]:
    """The words a span was read from, so "last week" or "in may" are never matched as names."""
    return set(re.findall(r"[a-z0-9]+", span.said)) if span is not None else set()


def _month_span(year: int, month: int) -> tuple[str, str]:
    first = datetime.date(year, month, 1)
    after = datetime.date(year + (month == 12), month % 12 + 1, 1)
    return first.isoformat(), (after - datetime.timedelta(days=1)).isoformat()


def span_in(text: str, now: float) -> Span | None:
    """The calendar dates a reference names, if it names any: one day (an ISO date, "Oct 11", "today", "yesterday", "3
    days ago", a weekday: the most recent one, today included) or a run of them ("this week", "last week": the calendar
    week before this one, Monday to Sunday; "the last 10 days", "past week"; "this month", "last month"; a month alone,
    "in October": the latest October not after today). A reading of dates, not of what was meant."""
    text = str(text or "").lower()
    today = datetime.date.fromtimestamp(now)

    def day(back: int) -> str:
        return days_before(now, back)

    def one(date: str, said: str) -> Span:
        return Span(date, date, said)

    found = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if found:
        return one(found.group(0), found.group(0)) if _is_date(found.group(0)) else None
    words = re.findall(r"[a-z]+|\d+", text)
    joined = " ".join(words)
    if re.search(r"\bday before yesterday\b", joined):
        return one(day(2), "day before yesterday")
    if "today" in words or re.search(r"\bthis (morning|afternoon|evening)\b|\btonight\b", joined):
        return one(day(0), "today")
    if "yesterday" in words:
        return one(day(1), "yesterday")
    ago = re.search(r"\b(\d+|a|one|two|three|four|five|six|seven|ten|fourteen) (day|days|week|weeks) ago\b", joined)
    if ago:
        n = 1 if ago.group(1) in ("a", "one") else _COUNTS.get(ago.group(1)) or int(ago.group(1))
        back = n * (7 if ago.group(2).startswith("week") else 1)
        if ago.group(2).startswith("week"):  # "two weeks ago": that calendar week
            monday = today - datetime.timedelta(days=today.weekday() + 7 * n)
            return Span(monday.isoformat(), (monday + datetime.timedelta(days=6)).isoformat(), ago.group(0))
        return one(day(back), ago.group(0))
    recent = re.search(r"\b(?:last|past) (\d+|two|three|four|five|six|seven|ten|fourteen) (day|days|week|weeks)\b", joined)
    if recent:
        n = _COUNTS.get(recent.group(1)) or int(recent.group(1))
        back = n * (7 if recent.group(2).startswith("week") else 1)
        return Span(day(back - 1), day(0), recent.group(0))
    if re.search(r"\b(?:past|this past) week\b|\blast seven days\b", joined):
        return Span(day(6), day(0), "past week")
    if re.search(r"\bthis week\b|\bearlier this week\b", joined):
        monday = today - datetime.timedelta(days=today.weekday())
        return Span(monday.isoformat(), today.isoformat(), "this week")
    if re.search(r"\bweek before last\b", joined):
        monday = today - datetime.timedelta(days=today.weekday() + 14)
        return Span(monday.isoformat(), (monday + datetime.timedelta(days=6)).isoformat(), "week before last")
    weekend = re.search(r"\b(?:last|this past|past|this) weekend\b|\bthe weekend\b", joined)
    if weekend:  # the latest Saturday and Sunday not after today
        saturday = today - datetime.timedelta(days=(today.weekday() - 5) % 7)
        if weekend.group(0).startswith("last") and today.weekday() >= 5:
            saturday -= datetime.timedelta(days=7)  # said on a weekend: the one before
        last = min(saturday + datetime.timedelta(days=1), today)
        return Span(saturday.isoformat(), last.isoformat(), weekend.group(0))
    if re.search(r"\blast week\b|\bprevious week\b|\bweek before\b", joined):
        monday = today - datetime.timedelta(days=today.weekday() + 7)
        return Span(monday.isoformat(), (monday + datetime.timedelta(days=6)).isoformat(), "last week")
    if re.search(r"\bthis month\b|\bearlier this month\b", joined):
        return Span(today.replace(day=1).isoformat(), today.isoformat(), "this month")
    if re.search(r"\blast month\b|\bprevious month\b", joined):
        year, month = (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)
        return Span(*_month_span(year, month), "last month")
    for i, w in enumerate(words):
        if w in _MONTHS and i + 1 < len(words) and words[i + 1].isdigit():
            year = today.year
            candidate = f"{year:04d}-{_MONTHS[w]:02d}-{int(words[i + 1]):02d}"
            if _is_date(candidate):
                if candidate > today.isoformat():  # "Dec 30" said in January: last year's
                    candidate = f"{year - 1:04d}-{_MONTHS[w]:02d}-{int(words[i + 1]):02d}"
                return one(candidate, f"{w} {words[i + 1]}") if _is_date(candidate) else None
    for i, w in enumerate(words):
        if w in _MONTHS and (w in _FULL_MONTHS or (i and words[i - 1] in _MONTH_AFTER)):
            month = _MONTHS[w]
            year = today.year if month <= today.month else today.year - 1  # "in December" said in November: last year's
            if i + 1 < len(words) and re.fullmatch(r"(?:19|20)\d\d", words[i + 1]):
                year = int(words[i + 1])  # "in October 2025"
                if (year, month) > (today.year, today.month):
                    return None
                first, last = _month_span(year, month)
                return Span(first, min(last, today.isoformat()), f"{w} {year}")
            first, last = _month_span(year, month)
            return Span(first, min(last, today.isoformat()), (f"{words[i - 1]} {w}" if i and words[i - 1] in _MONTH_AFTER else w))
    for w in words:
        if w in _WEEKDAYS:
            back = (today.weekday() - _WEEKDAYS[w]) % 7
            return one(day(back), w)
    return None


def _day_in(text: str, now: float) -> str | None:
    """The one calendar date a reference names, if it names a single day (`span_in`); None for none or a run of days."""
    span = span_in(text, now)
    return span.first if span is not None and span.first == span.last else None
