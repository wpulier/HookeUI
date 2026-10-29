"""Recoverable publication of bundle documents and their original journal owners.

The intent is temporary transaction state, not an ownership registry. Journals remain authoritative.
Readers refuse while an intent exists. On restart a pending transaction rolls back; a committed one only
finishes removing its visibility barriers. Each filesystem mutation is idempotently recoverable.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import json
import os
from pathlib import Path
import shutil
import threading

from .scientific_projects import ProjectStore, InvalidProject, PROJECT_ID, MAX_DOCUMENT, _json
from .day import ownership
from .file_locks import fcntl

INTENT = '.bundle-import.json'
_local = threading.local()


class ImportPending(InvalidProject):
    """Retryable unavailability, never an empty Project catalogue."""


def visible(root):
    return Path(root).resolve() in getattr(_local, 'roots', set()) or not (Path(root) / INTENT).exists()


def require_visible(root):
    if not visible(root):
        raise ImportPending('A bundle import needs completion or restart recovery before this work can be opened')


@contextmanager
def reading(root):
    """A public read cannot straddle publication or rollback, including an intent created and removed mid-read."""
    root = Path(root).resolve()
    held = getattr(_local, 'reading', set())
    if root in held or root in getattr(_local, 'roots', set()) or not root.exists():
        yield
        return
    # Runtime-removal readers already hold a retention lock. Waiting here would invert publication's order;
    # refuse promptly so removal stays closed and the importer can acquire that lock.
    with _visibility_lock(root, fcntl.LOCK_SH | fcntl.LOCK_NB):
        require_visible(root)
        _local.reading = held | {root}
        try:
            yield
        finally:
            _local.reading = held


@contextmanager
def _visibility_lock(root, mode):
    path = Path(root) / '.bundle-visibility.lock'
    # Shared readers need no write permission. Use the same retained inode as
    # publishers; only initialize a missing lock in a writable legacy store.
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    import stat
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise ImportPending('Project publication lock is not a regular file')
    try:
        try:
            fcntl.flock(fd, mode)
        except BlockingIOError as error:
            raise ImportPending('A bundle import is publishing; retry this Project read after it completes') from error
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def publishing(*roots):
    with ExitStack() as held:
        for root in roots:
            held.enter_context(_visibility_lock(root, fcntl.LOCK_EX))
        yield


@contextmanager
def internal(*roots):
    previous = getattr(_local, 'roots', set())
    _local.roots = previous | {Path(root).resolve() for root in roots}
    try:
        yield
    finally:
        _local.roots = previous


class Import:
    """Caller holds the session namespace, project root and every affected document lock throughout."""
    def __init__(self, store, sessions, plan, new_owners, stage):
        self.store, self.sessions = store, Path(sessions).resolve()
        self.value = {'format': 1, 'state': 'pending', 'sessions': str(self.sessions), 'stage': stage.name,
                      'documents': [{'id': identity, 'session': owner, 'before': before, 'after': after}
                                    for identity, owner, after, before in plan],
                      'owners': {folder.name: envelope for folder, envelope in new_owners}}

    def begin(self):
        require_visible(self.store.root)
        require_visible(self.sessions)
        body = _json(self.value)  # bounded before either visibility barrier is published
        self.store._write(self.store.root / INTENT, body)
        self.store._write(self.sessions / INTENT, _json({'format': 1}))

    def commit(self):
        committed = {**self.value, 'state': 'committed'}
        try:
            self.store._write(self.store.root / INTENT, _json(committed))
        except BaseException:
            # replace can succeed before its directory fsync reports failure. Never undo a visible commit record.
            previous = self.value
            self.value = committed  # unreadable outcome also stays closed for restart recovery
            if self.store._read(self.store.root / INTENT, MAX_DOCUMENT) == _json(previous):
                self.value = previous
            raise
        self.value = committed
        self.finish()

    def rollback(self):
        _rollback(self.store, self.sessions, self.value)
        self.finish()

    def finish(self):
        staged = self.store.root / self.value['stage']
        if staged.is_symlink():
            raise InvalidProject('Import staging folder changed; recovery refused')
        if staged.exists():
            shutil.rmtree(staged)
        (self.sessions / INTENT).unlink(missing_ok=True)
        self.store._sync_directory(self.sessions)
        (self.store.root / INTENT).unlink(missing_ok=True)
        self.store._sync_directory(self.store.root)


def _rollback(store, sessions, value):
    for row in reversed(value['documents']):
        folder = store._folder(row['id'])
        if not folder.exists():
            if row['before'] is not None:
                raise InvalidProject('Interrupted import lost a pre-existing document; recovery refused')
            continue
        current = store._head(folder, row['session'])
        if current not in (row['before'], row['after']):
            raise InvalidProject('Interrupted import document changed; recovery refused')
        if row['before'] is None:
            withdrawn = store.root / value['stage'] / (folder.name + '.withdrawn')
            if withdrawn.exists():
                raise InvalidProject('Interrupted import withdrawal changed; recovery refused')
            # Detach atomically before cleanup: a crash during rmtree must not leave a half-deleted accepted folder.
            os.rename(folder, withdrawn)
            store._sync_directory(withdrawn.parent)
        elif current != row['before']:
            store._write(folder / 'head.json', _json({'format': 1, 'session': row['session'], 'revisions': row['before']}))
    for sid, envelope in value['owners'].items():
        folder = sessions / sid
        if not folder.exists():
            continue
        if folder.is_symlink() or set(p.name for p in folder.iterdir()) - {'journal.jsonl'}:
            raise InvalidProject('Interrupted import Project owner changed; recovery refused')
        journal = folder / 'journal.jsonl'
        if journal.exists() and store._read(journal, ownership.MAX_LINE) != ownership.journal_bytes(envelope):
            raise InvalidProject('Interrupted import Project journal changed; recovery refused')
        journal.unlink(missing_ok=True)
        folder.rmdir()
    store._sync_directory(store.root)
    store._sync_directory(sessions)


def recover(store, sessions):
    """Before SessionStore resumes, recover only this profile's bounded, validated transaction intent."""
    sessions = Path(sessions).resolve()
    if not (store.root / INTENT).exists():
        if (sessions / INTENT).exists():
            raise InvalidProject('Bundle import recovery intent is missing; keep this profile unchanged')
        return
    with ownership.namespace(sessions), store._locked(store.root), internal(store.root, sessions), ExitStack() as held:
        # Another process may have finished while this one waited for the namespace.
        if not (store.root / INTENT).exists():
            return
        try:
            value = json.loads(store._read(store.root / INTENT, MAX_DOCUMENT))
            if (type(value) is not dict or set(value) != {'format', 'state', 'sessions', 'stage', 'documents', 'owners'}
                    or value['format'] != 1 or value['state'] not in ('pending', 'committed')
                    or value['sessions'] != str(sessions) or type(value['stage']) is not str
                    or not value['stage'].startswith('.scientific-import-') or Path(value['stage']).name != value['stage']
                    or type(value['documents']) is not list
                    or not 0 < len(value['documents']) <= 256 or type(value['owners']) is not dict
                    or len(value['owners']) > 256):
                raise ValueError('invalid intent')
            ids = set()
            from .scientific_projects import DIGEST, MAX_REVISIONS
            for row in value['documents']:
                if (type(row) is not dict or set(row) != {'id', 'session', 'before', 'after'}
                        or type(row['id']) is not str or PROJECT_ID.fullmatch(row['id']) is None
                        or row['id'] in ids or not ownership.is_sid(row['session'])):
                    raise ValueError('invalid document intent')
                ids.add(row['id'])
                for history in [row['after']] + ([row['before']] if row['before'] is not None else []):
                    if (type(history) is not list or not 0 < len(history) <= MAX_REVISIONS
                            or any(type(d) is not str or DIGEST.fullmatch(d) is None for d in history)):
                        raise ValueError('invalid revision intent')
                folder = store._folder(row['id'])
                if folder.exists():
                    held.enter_context(store._locked(folder))
            for sid, owner in value['owners'].items():
                ownership.validate(owner, sid)
                if sid not in {row['session'] for row in value['documents']}:
                    raise ValueError('unrelated owner intent')
        except (ValueError, TypeError, KeyError, RecursionError) as error:
            raise InvalidProject('Bundle import recovery intent is invalid; keep this profile unchanged') from error
        with publishing(store.root, sessions):
            transaction = Import(store, sessions, [], [], store.root / value['stage'])
            transaction.value = value
            if value['state'] == 'pending':
                transaction.rollback()
            else:
                transaction.finish()
