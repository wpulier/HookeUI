"""Inherited owners of a started Project, and the journal lines that list their results (ADR 0028, format v2 section 9).

A Project started from a publication holds the publication's owners read-only. Each one's journal is exactly what the
owner-aware import writes (`ownership.journal_bytes`: its origin and its import), followed only by the lines that list
its published results: evidence and tracks saved, dataset tables, analyses and graphs, and notes. A publication keeps
those lines for each owner without their conversation link, and nothing else a person said.

Journals are read here without the owner namespace lock: a reader holds a store's reading lock, which an import waits
on, so taking the namespace lock inside a read could deadlock.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import threading

INDEX = frozenset({'evidence-saved', 'tracks-saved', 'datatable', 'table-derivative', 'annotation'})
MAX_JOURNAL = 64 << 20
MAX_LINE = 4 << 20
LINKED = ('evidence-saved', 'tracks-saved')  # their path names the Project that saved them

_CACHE: dict[str, tuple[tuple, dict]] = {}
_LOCK = threading.Lock()


def read_journal(path: Path, limit: int = MAX_JOURNAL) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        import stat
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError('An inherited journal must be a regular file within its allowance')
        return stream.read(limit + 1)


def index_line(event) -> bytes | None:
    """One result-listing event as a publication keeps it, or None when it lists nothing that can travel."""
    if type(event) is not dict or event.get('type') not in INDEX or not isinstance(event.get('id'), str):
        return None
    if event['type'] in LINKED and not str(event.get('path') or '').startswith('project:'):
        return None  # a location on this Mac, never a portable one: it would only say where the user keeps files
    from .day.ownership import encode
    kept = {key: value for key, value in event.items() if key != 'thread'}  # the conversation it was made in stays
    try:
        line = encode(kept)
    except (TypeError, ValueError):
        return None
    return line if len(line) < MAX_LINE else None


def split(body: bytes, sid: str):
    """(owner, its result lines) when `body` is an inherited owner's journal, else None. A torn last line (an update
    still appending) is left out, never mistaken for something else."""
    from .day.ownership import journal_bytes, validate
    lines = body.split(b'\n')
    if len(lines) < 3:
        return None
    try:
        imported = json.loads(lines[1])
        if type(imported) is not dict or imported.get('type') != 'session-imported':
            return None
        owner = validate(imported.get('owner'), sid)
    except (ValueError, RecursionError):
        return None
    prefix = journal_bytes(owner)
    if not body.startswith(prefix):
        return None
    rest = body[len(prefix):].split(b'\n')
    kept = []
    for raw in rest[:-1]:  # every complete line after the import
        try:
            event = json.loads(raw)
        except (ValueError, RecursionError):
            return None
        if index_line(event) != raw:
            return None  # anything else (a conversation, a save of its own): not an inherited owner
        kept.append(raw)
    return owner, kept


def owners(layout: Path, own: str) -> dict:
    """{sid: owner envelope} of the inherited owners a Project folder holds for its own handle `own`; kept until its
    manifest or sessions folder changes."""
    from .day.sid import is_sid
    layout = Path(layout)
    sessions = layout / 'history' / 'sessions'
    try:
        manifest = (layout / 'project.json').stat()
        listing = sessions.stat(follow_symlinks=False)
    except OSError:
        return {}
    key = (own, manifest.st_mtime_ns, manifest.st_ino, listing.st_mtime_ns, listing.st_ino)
    with _LOCK:
        cached = _CACHE.get(str(layout))
        if cached is not None and cached[0] == key:
            return cached[1]
    found = {}
    try:
        with os.scandir(sessions) as entries:
            for entry in entries:
                if entry.name == own or not is_sid(entry.name) or entry.is_symlink() or not entry.is_dir():
                    continue
                try:
                    shape = split(read_journal(Path(entry.path) / 'journal.jsonl'), entry.name)
                except (OSError, ValueError):
                    continue
                if shape is not None:
                    found[entry.name] = shape[0]
    except OSError:
        found = {}
    with _LOCK:
        _CACHE[str(layout)] = (key, found)
    return found


def origin_of(owner: dict) -> str:
    """The Project origin an inherited owner's saved results name: the origin it had when it was that Project's own."""
    return owner['origin']['origin']


def published(journal: Path) -> bytes:
    """The result-listing lines a publication keeps from one owner's journal, in order, each once."""
    out, seen = [], set()
    try:
        body = read_journal(journal)
    except (OSError, ValueError):
        return b''
    for raw in body.split(b'\n'):
        try:
            line = index_line(json.loads(raw))
        except (ValueError, RecursionError):
            continue
        if line is not None and line not in seen:
            seen.add(line)
            out.append(line + b'\n')
    return b''.join(out)


def notes(lines) -> list[dict]:
    """The person's own words among published result lines (their notes), for the publication review."""
    out = []
    for raw in lines:
        try:
            event = json.loads(raw)
        except (ValueError, RecursionError):
            continue
        if type(event) is dict and event.get('type') == 'annotation':
            out.append({'id': event['id'], 'version': event.get('version'),
                        'title': str(event.get('title') or ''), 'text': str(event.get('text') or '')})
    return out


def bring_in(sessions: Path, sid: str, owner: dict, lines: list[bytes]) -> int:
    """Add a publication's result lines for `sid` to this Project's inherited owner, each once; returns how many were
    added. Creates the owner when the import did not (a publication owner with results and no documents). Refuses a
    session folder here that is not an inherited owner of the same origin."""
    from .day import ownership
    from .scientific_projects import ProjectStore
    sessions = Path(sessions)
    folder = sessions / sid
    with ownership.namespace(sessions):
        if folder.is_symlink():
            raise ValueError('A session folder here is a link')
        if not folder.exists():
            if not lines:
                return 0
            staged = sessions / f'.{sid}.inherit-{os.getpid()}-{threading.get_ident()}'
            staged.mkdir(mode=0o700)
            try:
                ProjectStore._write(staged / 'journal.jsonl',
                                    ownership.journal_bytes(owner) + b''.join(line + b'\n' for line in lines))
                os.rename(staged, folder)
            except BaseException:
                import shutil
                shutil.rmtree(staged, ignore_errors=True)
                raise
            ProjectStore._sync_directory(sessions)
            return len(lines)
        journal = folder / 'journal.jsonl'
        shape = split(read_journal(journal), sid)
        if shape is None or shape[0]['originDigest'] != owner['originDigest']:
            raise ValueError('A different session already uses this ID here')
        have = set(shape[1])
        new = [line for line in lines if line not in have]
        if not new:
            return 0
        body = read_journal(journal)
        fd = os.open(journal, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW)
        try:
            if not body.endswith(b'\n'):
                os.ftruncate(fd, body.rfind(b'\n') + 1)  # an interrupted update's torn line: it is written again below
            os.write(fd, b''.join(line + b'\n' for line in new))
            os.fsync(fd)
        finally:
            os.close(fd)
        return len(new)
