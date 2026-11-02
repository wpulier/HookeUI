"""Where maintenance keeps its records: `<catalog>/maintenance/`, this Mac's, beside the catalog it maintains.

    policies.json        each entry's recorded policy (ADR 0025), replaced atomically
    policies.jsonl       every policy change, appended, never rewritten
    checks.json          each entry's last update check: when, its outcome, what it found, when the next is due
    attempts/<key>.json  each entry's current or last attempt: its target, step, state and why
    decisions.jsonl      every promotion, rollback and undo, appended, never rewritten
    pins.json            durable holds on generations (pins.py)

Writers in the app and in the maintenance worker take one file lock (`locked`) around a read-modify-write.
"""
from __future__ import annotations

import copy
import json
import os
import tempfile
import threading
import time
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator, Mapping

from gpu_worker import manifests as catalog


def root(environ: Mapping[str, str] = os.environ) -> Path:
    return catalog.catalog_root(environ) / "maintenance"


def path(name: str, environ: Mapping[str, str] = os.environ) -> Path:
    return root(environ) / name


def safe(key: str) -> str:
    """An entry key as one file name (`mine/x` → `mine--x`)."""
    return key.replace("/", "--")


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


_seen: dict[str, tuple[tuple, object]] = {}  # what a file held, by its stat: a display read parses it once
_seen_lock = threading.Lock()
_DISPLAY_READS: ContextVar[bool] = ContextVar('maintenance_display_reads', default=False)


@contextmanager
def display_reads():
    """Only read-only display projections opt into observations; ordinary callers stay live."""
    token = _DISPLAY_READS.set(True)
    try:
        yield
    finally:
        _DISPLAY_READS.reset(token)


def _parsed(p: Path, parse):
    from .. import env_observations
    observed = (env_observations.display_fact(('maintenance-record', str(p)), None)
                if _DISPLAY_READS.get() else None)
    if observed is not None:
        return observed[0]  # (None,) records an observed missing/unreadable file.
    try:
        st = p.stat()
    except OSError:
        return None
    stamp = (st.st_mtime_ns, st.st_size, st.st_ino)
    with _seen_lock:
        kept = _seen.get(str(p))
        if kept is not None and kept[0] == stamp:
            return kept[1]
    try:
        value = parse(p.read_text())
    except (OSError, ValueError):
        return None
    with _seen_lock:
        _seen[str(p)] = (stamp, value)
    return value


def read(p: Path, default):
    """A copy of a JSON file's value, or `default` when it is missing, unreadable or of another type. The file is
    parsed again only when its stat changes (a write replaces it)."""
    value = _parsed(p, json.loads)
    return copy.deepcopy(value) if isinstance(value, type(default)) else default


def write(p: Path, value) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{p.name}-", dir=p.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=1)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, p)
    finally:
        Path(tmp).unlink(missing_ok=True)


def append(p: Path, row: dict) -> None:
    """One line added to a log that is never rewritten."""
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(row, separators=(",", ":")) + "\n"
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        os.write(fd, line.encode())
        os.fsync(fd)
    finally:
        os.close(fd)


def _rows(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue  # a line cut by a crash is not a record
        if isinstance(row, dict):
            out.append(row)
    return out


def lines(p: Path) -> list[dict]:
    return [dict(r) for r in (_parsed(p, _rows) or [])]


def display_records(keys, environ: Mapping[str, str] = os.environ) -> dict:
    """Collect raw records off-loop; display observations never authorize a mutation."""
    from .. import env_observations
    names = ['policies.json', 'checks.json', 'pins.json', 'decisions.jsonl']
    names.extend(f'attempts/{safe(key)}.json' for key in keys)
    with env_observations.live():
        return {('maintenance-record', str(path(name, environ))):
                (copy.deepcopy(_parsed(path(name, environ), _rows if name.endswith('.jsonl') else json.loads)),)
                for name in names}


_locks = threading.local()


@contextmanager
def locked(environ: Mapping[str, str] = os.environ) -> Iterator[None]:
    """One writer across threads/processes; the owning thread may compose nested maintenance writes."""
    from .. import env_observations
    with env_observations.live(), _locked(environ):
        yield


@contextmanager
def _locked(environ: Mapping[str, str]) -> Iterator[None]:
    import fcntl  # here, not at import: the module is read on systems without it (the Windows import probe)

    r = root(environ).resolve()
    held = getattr(_locks, "held", None)
    if held is None or getattr(_locks, "pid", None) != os.getpid():
        # A fork must acquire its own lock, never inherit the parent's reentrant permission.
        _locks.pid, _locks.held = os.getpid(), {}
        held = _locks.held
    if r in held:
        yield
        return
    r.mkdir(parents=True, exist_ok=True)
    fd = os.open(r / ".lock", os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        held[r] = fd
        yield
    finally:
        held.pop(r, None)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
