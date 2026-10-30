"""A session's journal: every event of the session, one JSON line each, append-only.

It lives at sessions/<sid>/journal.jsonl (day/sid.py): a day journal written before sessions existed is
the session whose id is its date.

Lines are written whole and flushed; a line torn by a crash is skipped on load, and the next
write starts on a fresh line so the torn one never swallows it. The journal
lives next to the researchers' work and, like it, is never committed (services/sessions/).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from ..paths import Paths
from . import sid as sids


class Journal:
    _owns_project = True

    def __init__(self, sid: str | None = None, root: Path | None = None, *, day: str | None = None) -> None:
        sid = sid if sid is not None else day  # `day=`: its old name
        if sid is None:
            raise TypeError("Journal() needs the session id")
        self.sid = sid
        self.root = Path(root) if root is not None else Paths.from_env().sessions
        self.path = self.root / sid / "journal.jsonl"
        self._tail_checked = False
        self._admitted_inode = None

    @property
    def day(self) -> str:
        """The session id, by its old name (one session per day until sessions are many)."""
        return self.sid

    def append(self, event: dict, *, durable=False) -> dict:
        if not self._owns_project:
            return self._append(event, durable=durable)
        if self._admitted_inode is not None:
            # Import never rewrites an already admitted owner journal. Keep its live events durable while
            # unrelated new owners are staged, without granting access to an uncommitted imported journal.
            return self._append(event, admitted=True, durable=durable)
        from .ownership import namespace
        with namespace(self.root):
            from ..scientific_import_transaction import require_visible
            require_visible(self.root)
            return self._append(event, durable=durable)

    def _append(self, event: dict, *, admitted=False, durable=False) -> dict:
        record = {"at": round(time.time(), 3), **event}
        line = json.dumps(record, ensure_ascii=False)
        if admitted:
            from ..scientific_import_transaction import ImportPending
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
            except OSError as error:
                import errno
                if error.errno in (errno.ENOENT, errno.ELOOP):
                    raise ImportPending('The admitted Project journal changed; reopen it before writing') from error
                raise
            try:
                opened = os.fstat(fd)
                try:
                    current = self.path.stat(follow_symlinks=False)
                except FileNotFoundError as error:
                    raise ImportPending('The admitted Project journal changed; reopen it before writing') from error
                if ((opened.st_dev, opened.st_ino) != self._admitted_inode
                        or (current.st_dev, current.st_ino) != self._admitted_inode):
                    raise ImportPending('The admitted Project journal changed; reopen it before writing')
                stream = os.fdopen(fd, 'a', encoding='utf-8')
            except BaseException:
                os.close(fd)
                raise
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            stream = self.path.open('a', encoding='utf-8')
        with stream as f:
            created = f.tell() == 0
            if created and self._owns_project:
                from .ownership import origin
                f.write(json.dumps(origin(self.sid, record["at"])) + "\n")
            if not self._tail_checked:
                self._tail_checked = True
                if not self._ends_cleanly():
                    f.write("\n")  # a line torn by a crash: end it, so this event is a line of its own
            f.write(line + "\n")
            f.flush()
            if created or durable:
                os.fsync(f.fileno())  # the immutable origin must survive once creation returns
            observed = os.fstat(f.fileno())
            self._admitted_inode = (observed.st_dev, observed.st_ino)
        if created or durable:
            # A prior directory-sync failure may leave visible but uncommitted
            # entries. Strict retries reconfirm the complete enclosing path.
            folders = (self.path.parent, *self.path.parent.parents) if durable else (self.path.parent, self.root)
            for folder in folders:
                fd = os.open(folder, os.O_RDONLY)
                try:
                    os.fsync(fd)
                finally:
                    os.close(fd)
        return record

    def _ends_cleanly(self) -> bool:
        try:
            with self.path.open("rb") as f:
                f.seek(0, 2)
                if f.tell() == 0:
                    return True
                f.seek(-1, 2)
                return f.read(1) == b"\n"
        except OSError:
            return True

    def load(self, *, complete_only=False) -> list[dict]:
        if not self._owns_project:
            return self._load(complete_only=complete_only)
        from ..scientific_import_transaction import reading
        with reading(self.root):
            return self._load(complete_only=complete_only)

    def _load(self, *, complete_only=False):
        from ..scientific_import_transaction import require_visible
        if self._owns_project:
            require_visible(self.root)
        events = []
        try:
            with self.path.open(encoding="utf-8", errors="replace") as f:  # a tail cut mid-character too
                for line in f:
                    if complete_only and not line.endswith("\n"):
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        continue  # a torn line (crash mid-write): the rest still counts
                    if isinstance(event, dict) and event.get("type"):
                        events.append(event)
                observed = os.fstat(f.fileno())
                if observed.st_size:
                    self._admitted_inode = (observed.st_dev, observed.st_ino)
        except OSError:
            pass
        return events

    def sessions(self) -> list[str]:
        """Sessions that have a journal, newest first (by date, then number: day/sid.py); a folder whose name is
        not a session id is not a session."""
        from ..scientific_import_transaction import require_visible
        require_visible(self.root)
        try:
            names = [p.name for p in self.root.iterdir() if sids.is_sid(p.name) and (p / "journal.jsonl").exists()]
        except OSError:
            return []
        return sorted(names, key=sids.sort_key, reverse=True)

    days = sessions  # its old name
