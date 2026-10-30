"""Project-owned append-only event files for retained scientific history.

This is a storage adapter, not a second event model. The owning service keeps its
existing event schema and replay rules. Missing/relocated/replaced roots refuse;
only a host holding the Project writer lease can append.
"""
import asyncio
import json
import os
import re
import stat
import threading

from .project_artifact_folder import ArtifactFolder
from .project_roots import ProjectLocationError
from .scientific_projects import ProjectStore


MAX_EVENT_BYTES = 1 << 20


class ProjectEvents:
    def __init__(self, legacy, name):
        self.folders = ArtifactFolder(legacy, name)
        self._identities = {}
        self._lock = threading.RLock()

    def _pin(self, path, *, directory):
        info = path.stat(follow_symlinks=False)
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1
        if not valid:
            raise ProjectLocationError('Project history storage was replaced or linked')
        identity = (info.st_dev, info.st_ino)
        if self._identities.setdefault(str(path), identity) != identity:
            raise ProjectLocationError('Project history storage changed; reopen its original location')
        return identity

    def path(self, store, session, date, *, write=False):
        if not isinstance(date, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
            raise ProjectLocationError('Retained history needs its original date')
        root = self.folders.root_for(store, session, write=write)
        folder = root / date
        if write:
            folder.mkdir(exist_ok=True)
            ProjectStore._sync_directory(root)
        if folder.exists() or folder.is_symlink():
            self._pin(folder, directory=True)
        elif str(folder) in self._identities:
            raise ProjectLocationError('Project history folder is missing; recover it before continuing')
        path = folder / 'events.jsonl'
        if path.exists() or path.is_symlink():
            self._pin(path, directory=False)
        elif str(path) in self._identities:
            raise ProjectLocationError('Project history is missing; recover it before continuing')
        return path

    @staticmethod
    def _off_loop():
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise ProjectLocationError(
            "Portable identity history requires its background execution adapter; no change was admitted")

    @staticmethod
    def _settled(path):
        pending = path.with_name("pending.json")
        if pending.exists() or pending.is_symlink():
            raise ProjectLocationError(
                "Project history has an uncertain write; retain its files and reconcile before continuing")
        return pending

    def read(self, store, session, date):
        self._off_loop()
        with self._lock:
            path = self.path(store, session, date)
            self._settled(path)
            if not path.exists():
                return []
            # Read through the same descriptor that was pinned. A partial final
            # line remains uncertain; it is retained but never replayed as an event.
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, 'r', encoding='utf-8') as stream:
                before = os.fstat(stream.fileno())
                if (before.st_dev, before.st_ino) != self._pin(path, directory=False):
                    raise ProjectLocationError('Project history changed while opening')
                rows = []
                remaining = before.st_size
                while remaining:
                    line = stream.buffer.readline(min(remaining, MAX_EVENT_BYTES + 1))
                    remaining -= len(line)
                    if not line or len(line) > MAX_EVENT_BYTES:
                        raise ProjectLocationError('Project history contains an unsupported event; keep its original file')
                    if not line.endswith(b'\n'):
                        if remaining:
                            raise ProjectLocationError('Project history contains an unsupported event; keep its original file')
                        continue
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
                after = os.fstat(stream.fileno())
                self.path(store, session, date)
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise ProjectLocationError('Project history changed while reading; retry')
                self._settled(path)
                return rows

    def append(self, store, session, date, record):
        self._off_loop()
        raw = (json.dumps(record, ensure_ascii=False) + '\n').encode('utf-8')
        if len(raw) > MAX_EVENT_BYTES:
            raise ProjectLocationError('Project history event exceeds its supported size')
        with self._lock:
            path = self.path(store, session, date, write=True)
            pending = self._settled(path)
            # Publish uncertainty before touching the journal. A complete line is
            # not accepted merely because it reached the filesystem. The marker
            # also excludes retries after a lost/failed durability acknowledgement.
            marker = json.dumps({"version": 1, "offset": path.stat().st_size if path.exists() else 0}).encode()
            marker_fd = os.open(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            with os.fdopen(marker_fd, 'wb') as stream:
                stream.write(marker)
                stream.flush()
                os.fsync(stream.fileno())
            ProjectStore._sync_directory(path.parent)
            fd = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            with os.fdopen(fd, 'r+b') as stream:
                info = os.fstat(stream.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_dev, info.st_ino) != self._pin(path, directory=False):
                    raise ProjectLocationError('Project history changed while opening')
                # Keep a crash-torn tail separate from the new complete event.
                if info.st_size:
                    stream.seek(-1, os.SEEK_END)
                    if stream.read(1) != b'\n':
                        stream.write(b'\0\n')
                self.path(store, session, date, write=True)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            ProjectStore._sync_directory(path.parent)
            self.path(store, session, date, write=True)
            # Last operation: no fallible work follows acceptance. A crash may
            # restore this marker because the unlink is deliberately not synced;
            # that conservatively requires reconciliation, never blind replay.
            pending.unlink()
