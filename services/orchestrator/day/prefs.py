"""The user's settings, kept until changed: one append-only journal beside the day folders.

`<sessions>/preferences.jsonl` holds who leads, the look of outlines, how to explain, the research cap's
amount, the models chosen from now on and the scale. They are the user's, not a day's: a new day, a clear
and a cold start all keep them. Its events are `preferences-started` (once, with the day journal it was
first seeded from, or null) and `preference {key, value}`; the last value of a key wins. It is written
through the day journal's own append and load (journal.py), so a line torn by a crash is skipped.

It also keeps which session the app was last in (`current-session`, ADR 0017): the app opens it at start.

Only SESSION_KEYS stay in the day's journal: what belongs to the session, not the user (the researchers
taken off the shelf, presenter.SHELF_REMOVED). The store (store.py) shows both as one `prefs`.
"""
from __future__ import annotations

import time
from pathlib import Path

from .journal import Journal

FILE = "preferences.jsonl"
SESSION_KEYS = frozenset({"shelfRemoved"})  # presenter.SHELF_REMOVED: kept in the day's journal


def is_users(key) -> bool:
    """Whether a preference is the user's (kept here) rather than the session's (kept in its journal)."""
    return isinstance(key, str) and key not in SESSION_KEYS


class _File(Journal):
    """The day journal's append and load, on one file of its own; not a Project owner."""
    _owns_project = False

    def __init__(self, root: Path | None) -> None:
        super().__init__("", root)
        self.path = self.root / FILE


class Prefs:
    def __init__(self, root: Path | None = None) -> None:
        self.journal = _File(root)  # root None: Paths.from_env().sessions, as the day journals
        self.values: dict = {}
        self.started = False
        self.current: str | None = None  # the session the app was last in (`point`)
        for event in self.journal.load():
            self._apply(event)

    @property
    def path(self) -> Path:
        return self.journal.path

    def _apply(self, e: dict) -> None:
        if e.get("type") == "preferences-started":
            self.started = True
        elif e.get("type") == "preference" and is_users(e.get("key")):
            self.values[e["key"]] = e.get("value")
        elif e.get("type") == "current-session" and isinstance(e.get("session"), str):
            self.current = e["session"]

    def _record(self, event: dict) -> dict:
        try:
            event = self.journal.append(event)
        except (OSError, ValueError) as exc:  # disk full, permissions, unencodable text
            # Kept in memory; only a restart would lose it (as the day journal does).
            event = {"at": round(time.time(), 3), **event}
            if not getattr(self, "_write_failed", False):
                self._write_failed = True
                print(f"Preferences: could not write ({exc}); continuing in memory", flush=True)
        self._apply(event)
        return event

    def set(self, key: str, value) -> dict | None:
        """Keep `value` for `key` when the key is absent or its value differs (a cap set to None is kept too).
        Returns the event written, or None when nothing changed."""
        if key in self.values and self.values[key] == value:
            return None
        if not self.started:
            self._record({"type": "preferences-started", "from": None})
        return self._record({"type": "preference", "key": key, "value": value})

    def point(self, sid: str) -> None:
        """Keep `sid` as the session the app is in, so a start opens it again."""
        if self.current == sid:
            return
        if not self.started:
            self._record({"type": "preferences-started", "from": None})
        self._record({"type": "current-session", "session": sid})

    def migrate(self, upto: str) -> str | None:
        """Once, when there is no preferences file yet: seed it with the user's preferences of the day journal of
        `upto` (today), else of the newest earlier one that has any, each at its last value. A seeded copy is in
        force from now (`at`), says where it came from (`seededFrom`) and when it was set there (`setAt`).
        Returns the day seeded from; None when nothing was (nothing is written until the first `set`)."""
        if self.started or self.journal.path.exists():
            return None
        root = self.journal.root
        for day in Journal("", root).days():  # newest first
            if day > upto:
                continue
            found: dict[str, dict] = {}
            for event in Journal(day, root).load():
                if event.get("type") == "preference" and is_users(event.get("key")):
                    found[event["key"]] = event  # the last value wins, in the order the keys were first set
            if found:
                self._record({"type": "preferences-started", "from": day})
                for key, event in found.items():
                    self._record({"type": "preference", "key": key, "value": event.get("value"), "seededFrom": day,
                                  "setAt": event.get("at")})
                return day
        return None


def history(root: Path) -> list[dict]:
    """Every preference event in `root`'s preferences file, as written (the datasets' readers of the scale). A
    seeded copy (`seededFrom`) repeats a day journal's value from the time it was seeded: no change of its own."""
    return [e for e in _File(root).load() if e.get("type") == "preference"]
