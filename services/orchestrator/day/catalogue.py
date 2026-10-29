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
from dataclasses import dataclass, field
from pathlib import Path

from . import sid as sids

ARCHIVE = "archive"  # sessions/archive/: journals moved aside by "start today afresh" before sessions existed
ID = re.compile(r"(tb|an|gr|[a-z])([0-9]+)")  # one letter, or a dataset table's "tb" (ids.PREFIXES)
ID_FIELDS = ("id", "lastMember")  # an event's own id fields (and "members", "ids": lists of ids)


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
    removed_sources: set[str] = field(default_factory=set)  # those the user took out of it
    on_stage: str | None = None  # what was on stage when it was last used ("live", an asset id)
    positions: dict[str, float] = field(default_factory=dict)  # where each recording was when it left the stage
    researchers: list[str] = field(default_factory=list)  # researcher ids started in it
    made: dict[tuple[str, str], str] = field(default_factory=dict)  # products: (kind, id) -> the day it was made
    # Every "made" event's (kind, id) -> its day, products or not: what its journal says it made (a Record dataset, a
    # snapshot), so the index and the library know whose it is (resources.py `_made`, app.py `origin_of`).
    claims: dict[tuple[str, str], str] = field(default_factory=dict)
    ids: dict[tuple[str, str], int] = field(default_factory=dict)  # (day, prefix) -> the highest number given

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
        if self.picture:
            row["thumb"] = f"/api/library/{self.picture}/thumbnail"  # the library's own small picture (thumbnails.py)
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
        root = self.locations.root_for(sid) if self.locations is not None else self.root
        with reading(root):
            return self._read_journal(sid, root)

    def _read_journal(self, sid, root=None):
        from ..scientific_import_transaction import require_visible
        root = self.root if root is None else root
        require_visible(root)
        path = root / sid / sids.JOURNAL
        try:
            stat = path.stat()
        except OSError:
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
        except OSError:
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
            if e.get("op") == "opened":
                info.sources.pop(aid, None)
                info.sources[aid] = at
                info.removed_sources.discard(aid)
                info.on_stage = aid
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
        infos = self.list()
        asked = [i for i in infos if i.last_user]
        return max(asked, key=lambda i: i.last_user) if asked else (infos[0] if infos else None)

    def used(self, day: str, prefix: str) -> int:
        """The highest number of an id with `prefix` that any session gave on calendar `day` (0: none): a new id on
        that day takes a higher one, so two sessions never share an id in one day's folder (day/ids.py)."""
        return max((i.ids.get((day, prefix), 0) for i in self.list()), default=0)

    def find(self, ref: str, *, current: str | None = None, now: float | None = None) -> list[Info]:
        """The sessions `ref` may mean, best first ([] when none): its id; "previous" (the most recent other than
        `current`); a date ("2026-10-11", "Oct 11", "October 11", "today", "yesterday", a weekday within the last
        week) narrowing to the sessions that touched it; and words matched against its name and conversations (twice)
        and what was seen in it (once)."""
        text = " ".join(str(ref or "").lower().split())
        if not text:
            return []
        infos = self.list()
        exact = [i for i in infos if i.id == text]
        if exact:
            return exact
        others = [i for i in infos if i.id != current]
        if text in ("previous", "the previous one", "the last one", "last one", "the other one", "before"):
            return others[:1]
        day = _day_in(text, now if now is not None else time.time())
        pool = [i for i in infos if day in i.days] if day else infos
        words = {w for w in re.findall(r"[a-z0-9]+", text) if len(w) > 2} - _NOT_NAMES

        def score(i: Info) -> int:
            main = " ".join([i.name, *i.titles.values()]).lower()
            rest = " ".join(i.specimens).lower()
            return sum(2 if w in main else 1 if w in rest else 0 for w in words)

        if not words:
            return pool if day else []
        ranked = sorted(((score(i), i.last_active, i) for i in pool), key=lambda s: (s[0], s[1]), reverse=True)
        return [i for s, _at, i in ranked if s > 0]


# Words that name the asking, not a session ("go back to yesterday's leaf session": "leaf" is what it is called).
_NOT_NAMES = {"the", "one", "session", "sessions", "back", "about", "with", "that", "this", "from", "earlier", "our",
              "today", "yesterday", "morning", "afternoon", "evening", "night", "last", "previous", "project", "go",
              "and", "where", "were", "was", "what", "did", "find", "found", "looked", "looking", "january",
              "february", "march", "april", "june", "july", "august", "september", "october", "november", "december",
              "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "monday", "tuesday",
              "wednesday", "thursday", "friday", "saturday", "sunday", "mon", "tue", "tues", "wed", "thu", "thur",
              "thurs", "fri", "sat", "sun"}
_MONTHS = {m: n for n, names in enumerate((("jan", "january"), ("feb", "february"), ("mar", "march"), ("apr", "april"),
                                           ("may",), ("jun", "june"), ("jul", "july"), ("aug", "august"),
                                           ("sep", "sept", "september"), ("oct", "october"), ("nov", "november"),
                                           ("dec", "december")), 1) for m in names}
_WEEKDAYS = {w: n for n, names in enumerate((("mon", "monday"), ("tue", "tues", "tuesday"), ("wed", "wednesday"),
                                             ("thu", "thur", "thurs", "thursday"), ("fri", "friday"),
                                             ("sat", "saturday"), ("sun", "sunday"))) for w in names}


def _day_in(text: str, now: float) -> str | None:
    """The calendar date a reference names, if it names one: an ISO date, a month and day ("Oct 11"), "today",
    "yesterday", or a weekday (the most recent one, today included). A reading of dates, not of what was meant."""
    found = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text)
    if found:
        return found.group(0) if _is_date(found.group(0)) else None
    words = re.findall(r"[a-z]+|\d+", text)
    if "today" in words:
        return local_day(now)
    if "yesterday" in words:
        return days_before(now, 1)
    for i, w in enumerate(words):
        if w in _MONTHS and i + 1 < len(words) and words[i + 1].isdigit():
            year = time.localtime(now).tm_year
            candidate = f"{year:04d}-{_MONTHS[w]:02d}-{int(words[i + 1]):02d}"
            if _is_date(candidate):
                if candidate > local_day(now):  # "Dec 30" said in January: last year's
                    candidate = f"{year - 1:04d}-{_MONTHS[w]:02d}-{int(words[i + 1]):02d}"
                return candidate if _is_date(candidate) else None
    for w in words:
        if w in _WEEKDAYS:
            back = (time.localtime(now).tm_wday - _WEEKDAYS[w]) % 7
            return days_before(now, back)
    return None

