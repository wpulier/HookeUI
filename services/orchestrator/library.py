"""The library: every recording and picture of the user's own, each an asset with a library-wide id and its metadata
(docs/design/2026-10-12-session-sources.md; ADR 0022).

A session looks at sources: the live camera, or one of these assets. Which are a session's is its journal's (day/
store.py `source` events, and what it made); this module knows only what each asset is.

- **What is an asset:** the real microscope recordings (assets/manifest.json, `microscope` true), files added in the
  studio (`footage/added/`, each with its .json), every Record dataset's camera video (each part), snapshots (their
  clean picture, `<date>/clean/<name>.png`, when the app drew outlines on one) and clearer pictures (enhance results, registered when made).
  Example data, the public corpus's recordings, are assets too (group "example"), opened from the side bar's Assets.
  The example that comes with the app (examples.py, Human cells) is one of them before anything is downloaded: copied
  where the bridge plays from and registered once under the walkthrough's own key, so both open the same asset.
  Test footage played from the camera menu (the corpus there, bench clips, camera tests off the microscope) stands for
  the live camera and the app never knows it (AGENTS.md "Architecture").
- **Ids** (`a1`, `a2`...) are given once per stable key and never reused, in `library.jsonl` (an asset):
  `footage:<file stem>` (an added file keeps its id when `make assets-adopt` moves it among the real recordings),
  `example:<file stem>` (the corpus),
  `dataset:<ds>/<n>`, `snapshot:<date>/<name>`, `result:<date>/<rid>/<n>`.
- **Metadata** is what the app knows from each kind's own record (the manifest, the added file's .json, dataset.json,
  the snapshot's .json, the result as registered), merged with the user's edits (edit lines in the same file, the
  newest per field wins). The user's own fields: specimen, objective, eyepiece, fieldNumber, umPerPx, title, notes,
  category and categoryName.
- **Categories** (docs/design/2026-10-12-workspace-and-assets.md; CONTEXT.md "Category"): where the library files an
  asset by what is in it, a fixed top level (CATEGORIES) the vision model picks from, with its own specific name below
  ("Invertebrates › Rotifers"). The app's look (library_looks.py) is a `looked` line; an example's is its specimen's
  (one look per specimen, a `looked-specimen` line); the user's correction (asset-meta) wins.
- **The trash** (a `trash` line, Undo a `restore` line): a trashed asset leaves the library and every session's list; the
  work made from it stays. A file the library holds (one added in the studio) is moved into `added/.trash/`, and back on
  restore; nothing is ever deleted.
- **Yours** (scope "mine") is the user's own, and the examples a session has used (`used`: the catalogue's sources).

Reading scans files, so it is done off the event loop (asyncio.to_thread), cached for a few seconds.
"""
from __future__ import annotations

import json
import copy
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

VIDEO = (".mp4", ".mov", ".m4v", ".mkv", ".webm")
STILL = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
ID = re.compile(r"^a(\d+)$")
# The fields a user may set on an asset (asset-meta), each a short text or a number; anything else is refused.
USER_TEXT = {"specimen": 80, "title": 120, "notes": 600, "camera": 80, "microscope": 80, "category": 40, "categoryName": 80}
USER_NUMBER = {"objective": (1.0, 200.0), "eyepiece": (1.0, 40.0), "fieldNumber": (5.0, 30.0), "umPerPx": (1e-4, 100.0)}
OPTICS = ("objective", "eyepiece", "fieldNumber", "umPerPx")
SCAN_S = 5.0  # how long a scan is reused before the folders are read again
# The library's top level (CONTEXT.md "Category"): fixed, so the first screen never reshuffles; the vision model picks
# one and names what it is below it. "Other" holds what fits none.
CATEGORIES = ("Plant tissue", "Blood", "Human & animal tissue", "Invertebrates", "Protists & algae", "Bacteria & fungi", "Other")
NOT_SURE = "Not sure yet"  # the card of what has had no look yet, or one that could not tell


def _registration(path: Path, title: str, at: float, origin: str | None, optics: dict | None, group: str,
                  specimen: str = "") -> dict:
    """A registered asset's facts, as library.jsonl keeps them (`register`)."""
    return {"path": str(path), "title": title, "at": round(at, 3), "origin": origin, "group": group,
            **({"optics": optics} if optics else {}), **({"specimen": specimen} if specimen else {})}


def is_asset_id(value) -> bool:
    return isinstance(value, str) and bool(ID.match(value))


def when_words(epoch: float | None) -> str:
    """"Oct 12, 1:44 am", as the studio and the bridge say a recording's start."""
    if not epoch:
        return ""
    text = time.strftime("%b %-d, %-I:%M %p", time.localtime(epoch))
    return text[:-2] + text[-2:].lower()


def said_specimen(asset: "Asset") -> str:
    """Its specimen as said (the user's word, else its record's), one space between words; "" when not named."""
    return " ".join(asset.specimen_now().split())


def by_specimen(assets: list["Asset"]) -> list[tuple[str, int, "Asset"]]:
    """The specimens a list of assets shows, however each is written: its name, how many, and its first (the list is
    newest first, so its newest), by name, the unnamed last. Example data's clips are offered this way (the side bar's
    Assets: about 1,500 of them, 1,503 of one specimen)."""
    groups: dict[str, list] = {}
    for asset in assets:
        name = said_specimen(asset)
        group = groups.setdefault(name.lower(), [name, 0, asset])
        group[1] += 1
    return sorted(((name, n, first) for name, n, first in groups.values()), key=lambda g: (not g[0], g[0].lower()))


def kind_of(path: Path) -> str | None:
    suffix = path.suffix.lower()
    return "recording" if suffix in VIDEO else "still" if suffix in STILL else None


@dataclass
class Asset:
    id: str
    key: str
    kind: str  # recording | still
    path: Path
    group: str  # where it came from: recording (a real microscope recording), added, record, snapshot, clearer
    title: str
    at: float | None = None  # when it was captured or made
    seconds: float | None = None  # a recording's length
    captured_on: str = ""  # the camera, when the record says
    microscope: bool | None = None  # through the microscope (None: not known)
    optics: dict = field(default_factory=dict)  # the app's own optics at capture: objective, fieldNumber, umPerPx...
    origin: str | None = None  # the session it was made in, when it was made in one
    specimen: str = ""  # what its own record says it shows
    user: dict = field(default_factory=dict)  # the user's edits, newest per field
    looked: dict = field(default_factory=dict)  # the app's look: {"category", "name", "sure", "model", "at"}

    def category(self) -> dict | None:
        """Where the library files it: the user's category over the app's look ({"group", "name", "by", "sure"}), or None
        while it has had no look (the "Not sure yet" card)."""
        group = self.user.get("category") or self.looked.get("category")
        if group not in CATEGORIES:
            return None
        by = "user" if self.user.get("category") else "app"
        name = self.user.get("categoryName") or (self.looked.get("name") if by == "app" or not self.user.get("categoryName") else "")
        return {"group": group, "name": str(name or ""), "by": by, "sure": True if by == "user" else bool(self.looked.get("sure"))}

    def metadata(self) -> dict:
        """Everything known about it, the user's word over the app's (the studio's row, the agent's `resources`)."""
        optics = {k: v for k, v in self.optics.items() if v is not None}
        optics.update({k: self.user[k] for k in OPTICS if self.user.get(k) is not None})
        return {"id": self.id, "kind": self.kind, "title": self.user.get("title") or self.title, "from": self.group,
                "capturedAt": round(self.at, 3) if self.at else None, "captured": when_words(self.at),
                "seconds": self.seconds, "capturedOn": self.user.get("camera") or self.captured_on or None,
                "microscope": self.microscope, "optics": optics,
                "specimen": self.user.get("specimen") or self.specimen or None,
                "specimenBy": "user" if self.user.get("specimen") else ("record" if self.specimen else None),
                "notes": self.user.get("notes") or None, "origin": self.origin, "file": self.path.name,
                "category": self.category()}

    def title_now(self) -> str:
        return self.user.get("title") or self.title

    def specimen_now(self) -> str:
        return self.user.get("specimen") or self.specimen

    def user_specimen(self) -> str:
        """The specimen the user gave it ("" when only the app's record or looks say): it wins over the looks."""
        return str(self.user.get("specimen") or "")

    def scale_optics(self) -> dict:
        """Its magnification for the scale while it is on stage: the user's, else its record's (scale.py)."""
        meta = self.metadata()["optics"]
        return {k: meta[k] for k in OPTICS if meta.get(k) is not None}


class Library:
    def __init__(self, path: Path | None, *, footage: Path | None = None, manifest: Path | None = None,
                 corpus: Path | None = None,
                 datasets: Path | None = None, snapshots: Path | None = None, origin_of: Callable[[str, str, float | None], str | None] | None = None,
                 clock: Callable[[], float] | None = None) -> None:
        """`path`: library.jsonl (None: ids in memory only, a test). `footage`: the assets store's footage folder (its
        real/ and added/); `manifest`: assets/manifest.json; `datasets`, `snapshots`: the app's folders. `origin_of(kind,
        id, when)`: the session a dataset was made in (the index's rule: its journal says so, else the session of its
        date), for its metadata."""
        self.path, self.footage, self.manifest, self.corpus = path, footage, manifest, corpus
        self.datasets, self.snapshots = datasets, snapshots
        self.origin_of = origin_of or (lambda kind, ident, at: None)
        self.clock = clock if clock is not None else (lambda: time.time())  # read when called, never kept from import
        self.lock = threading.RLock()
        self._ids: dict[str, str] = {}  # key -> id
        self._keys: dict[str, str] = {}  # id -> key
        self._edits: dict[str, dict] = {}  # id -> the user's fields
        self._registered: dict[str, dict] = {}  # key -> facts of an asset registered when made (a clearer picture)
        self._looked: dict[str, dict] = {}  # id -> the app's look (its category)
        self._looked_specimens: dict[str, dict] = {}  # an example's specimen (lower case) -> the look of its cover
        self._trashed: dict[str, dict] = {}  # id -> how it was trashed ({"at", "moved": [from, to] or None})
        self.used: Callable[[], set[str]] = lambda: set()  # the assets any session has (the catalogue): examples among Yours
        self._next = 0
        self._scan: tuple[float, dict[str, Asset]] | None = None
        self._scan_revision = 0
        self._read()

    # --- the file -----------------------------------------------------------------------------------------------
    def _read(self) -> None:
        if self.path is None or not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue  # a line cut by a crash: the rest stand
            if isinstance(e, dict) and isinstance(e.get("looked-specimen"), dict) and isinstance(e.get("specimen"), str):
                self._looked_specimens[e["specimen"].lower()] = e["looked-specimen"]  # an example specimen's look
                continue
            if not isinstance(e, dict) or not is_asset_id(e.get("id")):
                continue
            aid = e["id"]
            self._next = max(self._next, int(ID.match(aid).group(1)))
            if isinstance(e.get("key"), str):
                self._ids.setdefault(e["key"], aid)
                self._keys.setdefault(aid, e["key"])
                if isinstance(e.get("facts"), dict):
                    self._registered[e["key"]] = e["facts"]
            if isinstance(e.get("set"), dict):
                edits = self._edits.setdefault(aid, {})
                for k, v in e["set"].items():
                    if v in (None, ""):
                        edits.pop(k, None)
                    else:
                        edits[k] = v
            if isinstance(e.get("looked"), dict):
                self._looked[aid] = e["looked"]
            if e.get("trash") is True:
                self._trashed[aid] = {"at": e.get("at"), "moved": e.get("moved")}
            elif e.get("trash") is False:
                self._trashed.pop(aid, None)

    def _append(self, line: dict) -> None:
        if self.path is None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")

    def id_for(self, key: str, facts: dict | None = None) -> str:
        """The asset id of `key`, given now when it has none (written once, never reused)."""
        with self.lock:
            aid = self._ids.get(key)
            if aid is None:
                self._next += 1
                aid = f"a{self._next}"
                self._ids[key], self._keys[aid] = aid, key
                line = {"id": aid, "key": key, "at": round(self.clock(), 3)}
                if facts:
                    line["facts"] = facts
                    self._registered[key] = facts
                self._append(line)
            return aid

    # --- registering what is made now ---------------------------------------------------------------------------
    def register(self, key: str, path: Path, *, title: str, at: float, origin: str | None, optics: dict | None = None,
                 group: str = "clearer", specimen: str = "") -> str:
        """An asset made now that no folder scan finds (a clearer picture): its facts kept with its id."""
        aid = self.id_for(key, _registration(path, title, at, origin, optics, group, specimen))
        with self.lock:
            self._scan = None
            self._scan_revision += 1
        return aid

    def relocate_registered(self, aid: str, path: Path) -> None:
        """Keep a registered asset's identity/metadata when its storage location moves."""
        with self.lock:
            key = self._keys.get(aid)
            previous = self._registered.get(key)
            if previous is None or not path.is_file():
                raise ValueError("the registered asset or its new file is unavailable")
            if aid in self._trashed:
                raise ValueError("Restore this asset from the trash before moving its location")
            if previous.get("path") == str(path):
                return
            facts = {**previous, "path": str(path)}
            self._append({"id": aid, "key": key, "facts": facts, "at": round(self.clock(), 3)})
            self._registered[key] = facts
            self._scan = None
            self._scan_revision += 1

    # --- what there is --------------------------------------------------------------------------------------------
    def assets(self, fresh: bool = False) -> dict[str, Asset]:
        """Every asset by id (a scan, reused for a few seconds). Blocking: call it off the event loop."""
        with self.lock:
            now = time.monotonic()
            if not fresh and self._scan is not None and now - self._scan[0] < SCAN_S:
                return self._scan[1]
            self._scan_revision += 1
            revision = self._scan_revision
        found: list[Asset] = []
        for reader in (self._footage, self._examples, self._datasets_found, self._snapshots_found, self._registered_found):
            try:
                found.extend(reader())
            except Exception:
                continue  # one store that can't be read leaves the others listed
        out = {}
        for asset in found:
            if asset.id in self._trashed:
                continue  # in the trash: out of the library and every session's list until restored
            asset.user = dict(self._edits.get(asset.id, {}))
            asset.looked = dict(self._looked.get(asset.id) or (self._looked_specimens.get(asset.specimen.lower(), {})
                                                               if asset.group == "example" else {}))
            out[asset.id] = asset
        with self.lock:
            if revision == self._scan_revision:
                self._scan = (time.monotonic(), out)
        return out

    def get(self, aid: str) -> Asset | None:
        if not is_asset_id(aid):
            return None
        found = self.assets().get(aid)
        if found is None and aid in self._keys:
            found = self.assets(fresh=True).get(aid)  # made since the last scan
        return found

    def by_key(self, key: str) -> Asset | None:
        aid = self._ids.get(key)
        return self.get(aid) if aid else None

    def edits(self, aid: str | None) -> dict:
        """The user's fields of an asset, from memory (no scan: safe on the event loop)."""
        with self.lock:
            return dict(self._edits.get(aid or "", {}))

    def key_of(self, aid: str) -> str | None:
        return self._keys.get(aid)

    def retention_state(self, asset: Asset) -> dict:
        """Small source-owned capture guard; no scan or filesystem work under publication."""
        with self.lock:
            key = self._keys.get(asset.id)
            return copy.deepcopy({'key': key, 'edits': self._edits.get(asset.id, {}),
                'registered': self._registered.get(key), 'looked': self._looked.get(asset.id, {}),
                'sharedLooked': self._looked_specimens.get(asset.specimen.lower(), {}) if asset.group == 'example' else {},
                'trashed': self._trashed.get(asset.id)})

    def search(self, query: str = "", kind: str | None = None, fresh: bool = False, scope: str | None = None) -> list[Asset]:
        """Every asset matching the words (title, specimen, file, notes), newest first; the examples show each
        specimen's newest clip before a second clip of any. `fresh`: the folders are read again now, not the scan of the
        last few seconds (the studio, finding a file it has just added)."""
        words = [w for w in re.split(r"\s+", (query or "").lower()) if w]
        used = self.used() if scope == "mine" else set()
        out = []
        for asset in self.assets(fresh=fresh).values():
            if kind and asset.kind != kind:
                continue
            if scope == "mine" and asset.group == "example" and asset.id not in used or scope == "example" and asset.group != "example":
                continue  # All assets: yours (and the examples a session used) apart from the examples
            category = asset.category() or {}
            text = " ".join([asset.title_now(), asset.specimen_now(), asset.path.name, str(asset.user.get("notes") or ""),
                             str(category.get("group") or ""), str(category.get("name") or "")]).lower()
            if all(w in text for w in words):
                out.append(asset)
        newest = sorted(out, key=lambda a: (-(a.at or 0.0), a.id))
        if scope != "example":
            return newest
        # The examples: every specimen's newest clip, then the other clips. Most of the corpus is one specimen's clips
        # (1,506 of 1,566 are Gram-stained bacteria, the newest), which newest-first put before every other specimen.
        seen: set[str] = set()
        firsts = [a for a in newest if (a.specimen_now() or a.id) not in seen and not seen.add(a.specimen_now() or a.id)]
        chosen = {a.id for a in firsts}
        return firsts + [a for a in newest if a.id not in chosen]

    # --- the user's edits -------------------------------------------------------------------------------------------
    def set_meta(self, aid: str, fields: dict, by: str = "user") -> dict:
        """The user's fields of an asset (`fields`: a value, or None or "" to clear it). ValueError, in words, for an
        unknown asset or field, or a value out of range. Returns the fields as they stand now."""
        if aid not in self._keys:
            raise ValueError(f"No asset called {aid!r}")
        clean: dict = {}
        for k, v in (fields or {}).items():
            if k in USER_TEXT:
                clean[k] = " ".join(str(v or "").split())[:USER_TEXT[k]]
                if k == "category" and clean[k] and clean[k] not in CATEGORIES:
                    raise ValueError(f"a category is one of {', '.join(CATEGORIES)}")
            elif k in USER_NUMBER:
                if v in (None, ""):
                    clean[k] = None
                    continue
                try:
                    number = float(v)
                except (TypeError, ValueError):
                    raise ValueError(f"{k} is a number") from None
                low, high = USER_NUMBER[k]
                if not low <= number <= high:
                    raise ValueError(f"{k} is between {low:g} and {high:g}")
                clean[k] = number
            else:
                raise ValueError(f"{k} is not a field of an asset (specimen, title, notes, camera, microscope, "
                                 "objective, eyepiece, fieldNumber, umPerPx, category, categoryName)")
        if not clean:
            raise ValueError("nothing to set")
        with self.lock:
            edits = self._edits.setdefault(aid, {})
            for k, v in clean.items():
                if v in (None, ""):
                    edits.pop(k, None)
                else:
                    edits[k] = v
            self._append({"id": aid, "set": clean, "by": by, "at": round(self.clock(), 3)})
            if self._scan is not None and aid in self._scan[1]:
                self._scan[1][aid].user = dict(edits)
        return dict(self._edits.get(aid, {}))

    # --- the app's look, and the trash ----------------------------------------------------------------------------------
    def looked(self, aid: str, category: str, name: str, sure: bool, model: str) -> dict:
        """The app's look at an asset (library_looks.py): its category, one of CATEGORIES ("Other" for anything else), and
        the specific name the model gave. Never over the user's own category, which stays."""
        facts = {"category": category if category in CATEGORIES else "Other", "name": " ".join(str(name or "").split())[:80],
                 "sure": bool(sure), "model": str(model or ""), "at": round(self.clock(), 3)}
        with self.lock:
            self._looked[aid] = facts
            self._append({"id": aid, "looked": facts})
            self._scan = None
            self._scan_revision += 1
        return facts

    def looked_specimen(self, specimen: str, category: str, name: str, sure: bool, model: str) -> dict:
        """The app's look at an example specimen's cover: every example of that specimen is filed by it (the corpus is
        about 1,500 clips of some 30 specimens: one look each)."""
        facts = {"category": category if category in CATEGORIES else "Other", "name": " ".join(str(name or "").split())[:80],
                 "sure": bool(sure), "model": str(model or ""), "at": round(self.clock(), 3)}
        with self.lock:
            self._looked_specimens[specimen.lower()] = facts
            self._append({"specimen": specimen, "looked-specimen": facts})
            self._scan = None
            self._scan_revision += 1
        return facts

    def unlooked(self) -> list[Asset]:
        """What still waits for a look, oldest first: each of the user's own with no category, and one example (its cover)
        for each specimen that has none. Blocking: a scan."""
        mine, examples, seen = [], [], set()
        for asset in sorted(self.assets().values(), key=lambda a: (a.at or 0.0, a.id)):
            if asset.key.startswith("onboarding:") or asset.user.get("category") or asset.looked:
                continue
            if asset.group == "example":
                key = asset.specimen.lower()
                if key in seen:
                    continue
                seen.add(key)
                examples.append(asset)
            else:
                mine.append(asset)
        return mine + examples

    def trash(self, aid: str) -> dict:
        """Move an asset to the trash: out of the library and every session's list. A file the library holds (added in
        the studio) moves into `added/.trash/` with its record; others are only marked. ValueError, in words, when there
        is no such asset or it is in the trash already."""
        asset = self.get(aid)
        if asset is None:
            raise ValueError("No asset called %r" % aid if aid not in self._trashed else "It is in the trash already")
        moved = None
        if asset.group == "added" and self.footage is not None and asset.path.parent == self.footage / "added":
            bin_ = asset.path.parent / ".trash"
            bin_.mkdir(parents=True, exist_ok=True)
            target = bin_ / asset.path.name
            asset.path.rename(target)
            record = asset.path.with_name(asset.path.name + ".json")
            if record.is_file():
                record.rename(bin_ / record.name)
            moved = [str(asset.path), str(target)]
        with self.lock:
            self._trashed[aid] = {"at": round(self.clock(), 3), "moved": moved}
            self._append({"id": aid, "trash": True, "at": round(self.clock(), 3), **({"moved": moved} if moved else {})})
            self._scan = None
            self._scan_revision += 1
        return {"id": aid, "title": asset.title_now(), "moved": bool(moved)}

    def restore(self, aid: str) -> dict:
        """Undo of the trash: back in the library and in the sessions that had it (their journals never let it go)."""
        with self.lock:
            facts = self._trashed.get(aid)
        if facts is None:
            raise ValueError("That is not in the trash")
        moved = facts.get("moved")
        if isinstance(moved, list) and len(moved) == 2:
            source, target = Path(moved[0]), Path(moved[1])
            if target.is_file() and not source.exists():
                target.rename(source)
                record = target.with_name(target.name + ".json")
                if record.is_file():
                    record.rename(source.with_name(source.name + ".json"))
        with self.lock:
            self._trashed.pop(aid, None)
            self._append({"id": aid, "trash": False, "at": round(self.clock(), 3)})
            self._scan = None
            self._scan_revision += 1
        asset = self.get(aid)
        return {"id": aid, "title": asset.title_now() if asset else aid}

    def trashed(self) -> list[str]:
        with self.lock:
            return list(self._trashed)

    # --- the readers, one per kind --------------------------------------------------------------------------------
    def _footage(self) -> list[Asset]:
        """The real microscope recordings (manifest) and the files added in the studio (footage/added, a .json beside
        each). Camera tests off the microscope and the corpus are test footage, never assets."""
        out: list[Asset] = []
        if self.footage is None:
            return out
        root = self.footage.parent  # the assets store: manifest paths are relative to it
        real: list = []
        if self.manifest is not None:
            try:
                real = json.loads(self.manifest.read_text()).get("real") or []
            except (OSError, ValueError, AttributeError):
                real = []
        for item in real:
            if not isinstance(item, dict) or item.get("microscope") is False:
                continue
            path = root / str(item.get("file") or "")
            if kind_of(path) is None or not path.is_file():
                continue
            optics = item.get("optics") or {}
            at = _date_epoch(item.get("date")) or path.stat().st_mtime
            out.append(Asset(self.id_for(f"footage:{path.stem}"), f"footage:{path.stem}", kind_of(path), path, "recording",
                             str(item.get("title") or path.stem), at, _number(item.get("seconds")),
                             str(optics.get("camera") or ""), True if item.get("microscope") else None,
                             {k: _number(optics.get(k)) for k in OPTICS}, None, str(item.get("specimen") or "")))
        added = self.footage / "added"
        if added.is_dir():
            for path in sorted(added.iterdir()):
                kind = kind_of(path)
                if path.name.startswith('.') or kind is None or not path.is_file():
                    continue
                try:
                    meta = json.loads(path.with_name(path.name + ".json").read_text())
                except (OSError, ValueError):
                    meta = {}
                meta = meta if isinstance(meta, dict) else {}
                optics = meta.get("optics") or {}
                at = path.stat().st_mtime  # when it was added here (its record's `date` is only a day)
                out.append(Asset(self.id_for(f"footage:{path.stem}"), f"footage:{path.stem}", kind, path, "added",
                                 str(meta.get("title") or path.stem), at, _number(meta.get("seconds")),
                                 str(optics.get("camera") or ""),
                                 None if "microscope" not in meta else bool(meta.get("microscope")),
                                 {k: _number(optics.get(k)) for k in OPTICS}, None, str(meta.get("specimen") or "")))
        return out

    def _bundled(self) -> None:
        """The example that comes with the app (examples.py): copied where the camera bridge plays from and registered
        once under the walkthrough's key, so Examples lists it before any download (ticket 67: the empty Stage says
        "open the examples in the Library") and the walkthrough opens this same asset. Listed by `_registered_found`."""
        from . import examples

        if self.snapshots is None:
            return  # no folder the bridge plays from (a test library)
        facts = self._registered.get(examples.KEY)
        if facts is not None and Path(str(facts.get("path") or "")).is_file():
            return
        image, _ = examples.placed(self.snapshots)
        if facts is None:
            self.id_for(examples.KEY, _registration(image, examples.TITLE, 0, None, None, "example", examples.SPECIMEN))
        elif facts.get("path") != str(image):
            self.relocate_registered(self._ids[examples.KEY], image)  # refused while in the trash: it stays there

    def _examples(self) -> list[Asset]:
        """Example data: the public corpus's recordings (footage/corpus/, services/bench/corpus/corpus.json), each with its
        specimen and, where its source says, its scale. Opened from the side bar's Assets, one is a session's source like
        any other (ADR 0022's amendment); played from the camera menu's test footage it still stands for the live
        camera. The bundled example is registered here first (`_bundled`)."""
        try:
            self._bundled()
        except (OSError, ValueError):
            pass  # damaged or missing: the walkthrough says so when asked; the corpus is still listed
        out: list[Asset] = []
        if self.footage is None or self.corpus is None or not (self.footage / "corpus").is_dir():
            return out
        try:
            items = json.loads(self.corpus.read_text()).get("items") or []
        except (OSError, ValueError, AttributeError):
            return out
        known = {str(i["id"]): i for i in items if isinstance(i, dict) and i.get("id")}
        ids = sorted(known, key=len, reverse=True)  # the longest id that fits ("pond-seah" before "pond")
        for path in sorted((self.footage / "corpus").iterdir()):
            if kind_of(path) != "recording" or not path.stem.startswith("corpus-"):
                continue
            rest = path.stem[len("corpus-"):]
            cid = next((c for c in ids if rest == c or (rest.startswith(c + "-") and rest[len(c) + 1:].isdigit())), None)
            if cid is None:
                continue
            item, n = known[cid], rest[len(cid) + 1:]
            specimen = str(item.get("specimen") or cid)
            optics = item.get("optics") or {}
            key = f"example:{path.stem}"
            out.append(Asset(self.id_for(key), key, "recording", path, "example",
                             f"{specimen[:1].upper()}{specimen[1:]}" + (f" ({int(n) + 1})" if n else ""), path.stat().st_mtime,
                             None, "", True, {k: _number(optics.get(k)) for k in OPTICS}, None, specimen))
        return out

    def _datasets_found(self) -> list[Asset]:
        """Each part of every Record dataset's camera video, with the dataset's facts at its start (its `rig`)."""
        out: list[Asset] = []
        if self.datasets is None or not self.datasets.is_dir():
            return out
        from .datasets import manifest as ds_manifest
        from .datasets.snapshot import sidecar_span

        for folder, summary in ds_manifest.all_in(self.datasets):
            dataset = str(summary.get("id") or folder.name)
            name = " ".join(str(summary.get("name") or "").split())
            rig = summary.get("rig") or {}
            optics = {"objective": _rig(rig, "objective"), "eyepiece": _rig(rig, "eyepiece"),
                      "fieldNumber": _rig(rig, "fieldNumber", known_only=True)}
            camera = _rig(rig, "camera")
            for part in (summary.get("video") or {}).get("parts") or []:
                n = part.get("n") if isinstance(part, dict) else None
                if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                    continue
                video = folder / "video" / f"part-{n}.mp4"
                if not video.is_file():
                    continue
                span = sidecar_span(folder / str(part["sidecar"])) if part.get("sidecar") else None
                begun = span[1] if span is not None else part.get("noticedAtEpoch") or summary.get("startedAtEpoch")
                begun = float(begun) if isinstance(begun, (int, float)) else video.stat().st_mtime
                seconds = round(span[2] - span[1], 1) if span is not None and span[2] > span[1] else None
                key = f"dataset:{dataset}/{n}"
                out.append(Asset(self.id_for(key), key, "recording", video, "record",
                                 name or f"Recording · {when_words(begun)}", begun, seconds,
                                 str(camera or ""), None, {k: _number(v) for k, v in optics.items()},
                                 self.origin_of("dataset", dataset, begun), ""))
        return out

    def _snapshots_found(self) -> list[Asset]:
        """Every snapshot: its clean picture when the app drew outlines on it and kept one, else its picture."""
        out: list[Asset] = []
        if self.snapshots is None or not self.snapshots.is_dir():
            return out
        from .day.sid import session_of

        for folder in sorted(p for p in self.snapshots.iterdir() if p.is_dir()):
            for meta_path in sorted(folder.glob("snapshot-*.json")):
                try:
                    facts = json.loads(meta_path.read_text())
                except (OSError, ValueError):
                    continue
                if not isinstance(facts, dict):
                    continue
                clean = folder / "clean" / f"{meta_path.stem}.png"  # its clean copy, when the app drew on it
                picture = clean if clean.is_file() else meta_path.with_suffix(".png")
                if not picture.is_file():
                    continue
                at = _number(facts.get("wallAt")) or picture.stat().st_mtime
                scale = facts.get("scale") or {}
                optics = {"objective": _number(scale.get("objective")), "umPerPx": _number(scale.get("umPerPx"))}
                key = f"snapshot:{folder.name}/{meta_path.stem}"
                drawn = facts.get("overlays") is not False and picture == meta_path.with_suffix(".png")
                out.append(Asset(self.id_for(key), key, "still", picture, "snapshot",
                                 f"Snapshot · {when_words(at)}" + (" (outlines drawn)" if drawn else ""), at, None, "",
                                 None, optics, session_of(facts, folder.name) or None, ""))
        return out

    def _registered_found(self) -> list[Asset]:
        out: list[Asset] = []
        for key, facts in list(self._registered.items()):
            path = Path(str(facts.get("path") or ""))
            kind = kind_of(path)
            if kind is None or not path.is_file():
                continue
            optics = facts.get("optics") or {}
            out.append(Asset(self._ids[key], key, kind, path, str(facts.get("group") or "clearer"),
                             str(facts.get("title") or path.stem), _number(facts.get("at")), None, "", None,
                             {k: _number(optics.get(k)) for k in OPTICS}, facts.get("origin"),
                             str(facts.get("specimen") or "")))
        return out


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _rig(rig: dict, name: str, known_only: bool = False):
    """A dataset's fact at its start ({value, source}); `known_only`: not the app's default."""
    fact = rig.get(name) if isinstance(rig, dict) else None
    if not isinstance(fact, dict) or (known_only and fact.get("source") in ("default", "unknown")):
        return None
    return fact.get("value")


def _date_epoch(date) -> float | None:
    try:
        return time.mktime(time.strptime(str(date), "%Y-%m-%d"))
    except (TypeError, ValueError):
        return None
