"""A remote in a plain folder: an external drive, a NAS or any mounted share (format v2, ADR 0028).

It needs no network and no account. Record blobs, commits and bulk objects are all named by SHA-256 and written once.
The pointer moves by compare-and-swap under an exclusive lock, so two Macs pushing at once cannot both win.
Layout: remote.json, HEAD, commits/<id>.json, blobs/<aa>/<sha256>, objects/<aa>/<sha256>.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

from .file_locks import fcntl
from .project_remote import Diverged, Remote, RemoteFile
from .project_remote_layers import DIGEST, MAX_ENTRIES, ChangedOutsideHooke, RemoteRefused, canonical, sha256

FORMAT = 'hooke-folder-remote-v1'
COMMIT_FORMAT = 'hooke-remote-commit-v1'
MAX_COMMIT = 64 << 20


def _read(path: Path, limit: int) -> bytes:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as error:
        raise RemoteRefused('Something this remote names is missing') from error
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ChangedOutsideHooke('The remote holds a link or special file where a file belongs')
        body = stream.read(limit + 1)
    if len(body) > limit:
        raise ChangedOutsideHooke('A remote file is larger than it may be')
    return body


def _publish(path: Path, write):
    """Write through a temporary file, then rename into place and sync; never follow links."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix='.remote-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def _intact(path: Path, digest: str, size: int | None = None) -> bool:
    if path.is_symlink() or not path.is_file() or (size is not None and path.stat().st_size != size):
        return False
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest() == digest


class FolderRemote:
    """The record side of a folder remote."""

    def __init__(self, base: Path, *, create=False):
        self.base = Path(base).expanduser()
        marker = self.base / 'remote.json'
        if create and not marker.exists():
            self.base.mkdir(parents=True, exist_ok=True)
            if any(p.name not in ('.DS_Store',) for p in self.base.iterdir()):
                raise RemoteRefused('Choose an empty folder for a new Hooke remote')
            _publish(marker, lambda s: s.write(canonical({'format': FORMAT})))
        try:
            value = json.loads(_read(marker, 4096))
        except (ValueError, RemoteRefused) as error:
            raise RemoteRefused('This folder is not a Hooke remote') from error
        if value != {'format': FORMAT}:
            raise RemoteRefused('This folder holds an unsupported Hooke remote')

    def describe(self):
        return {'kind': 'folder', 'base': str(self.base)}

    def _blob(self, digest: str) -> Path:
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ChangedOutsideHooke('A remote name is not a SHA-256')
        return self.base / 'blobs' / digest[:2] / digest

    def head(self):
        path = self.base / 'HEAD'
        if not path.exists() and not path.is_symlink():
            return None
        value = _read(path, 128).decode().strip()
        if not DIGEST.fullmatch(value):
            raise ChangedOutsideHooke('The remote pointer is damaged')
        return value

    def read_commit(self, commit: str) -> dict:
        if not isinstance(commit, str) or not DIGEST.fullmatch(commit):
            raise RemoteRefused('Name a version by its id')
        raw = _read(self.base / 'commits' / f'{commit}.json', MAX_COMMIT)
        if sha256(raw) != commit:
            raise ChangedOutsideHooke('A version on this remote was changed outside Hooke')
        try:
            value = json.loads(raw)
        except (ValueError, RecursionError) as error:
            raise ChangedOutsideHooke('A version on this remote is unreadable') from error
        if (type(value) is not dict or set(value) != {'format', 'parent', 'message', 'entries'}
                or value['format'] != COMMIT_FORMAT or type(value['entries']) is not list
                or len(value['entries']) > MAX_ENTRIES + 16 or not isinstance(value['message'], str)
                or not (value['parent'] is None or (isinstance(value['parent'], str) and DIGEST.fullmatch(value['parent'])))):
            raise ChangedOutsideHooke('A version on this remote is not a Hooke version')
        entries = []
        for item in value['entries']:
            if type(item) is not dict or set(item) != {'path', 'sha256', 'bytes'}:
                raise ChangedOutsideHooke('A version on this remote lists an invalid file')
            entries.append({'path': item['path'], 'ref': item['sha256'], 'bytes': item['bytes']})
        return {'commit': commit, 'parent': value['parent'], 'message': value['message'], 'entries': entries}

    def read_blob(self, ref: str, max_bytes: int) -> bytes:
        body = _read(self._blob(ref), max_bytes)
        if sha256(body) != ref:
            raise ChangedOutsideHooke('A file on this remote was changed outside Hooke')
        return body

    def write_commit(self, files: list[RemoteFile], *, parent, message, check=lambda: None) -> str:
        entries = []
        for item in files:
            check()
            body = item.read()
            digest = sha256(body)
            if item.sha256 is not None and digest != item.sha256:
                raise RemoteRefused(f'{item.path} changed while sending; nothing was sent')
            target = self._blob(digest)
            if not _intact(target, digest, len(body)):
                _publish(target, lambda s, b=body: s.write(b))
            entries.append({'path': item.path, 'sha256': digest, 'bytes': len(body)})
        raw = canonical({'format': COMMIT_FORMAT, 'parent': parent, 'message': message,
                         'entries': sorted(entries, key=lambda e: e['path'])})
        commit = sha256(raw)
        target = self.base / 'commits' / f'{commit}.json'
        if not _intact(target, commit):
            _publish(target, lambda s: s.write(raw))
        return commit

    def advance(self, old, new):
        if not DIGEST.fullmatch(new):
            raise RemoteRefused('Name a version by its id')
        self.read_commit(new)
        fd = os.open(self.base / '.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            if self.head() != old:
                raise Diverged('This project changed in two places: another version arrived first. Nothing was changed.')
            _publish(self.base / 'HEAD', lambda s: s.write(new.encode() + b'\n'))
        finally:
            os.close(fd)


class FolderBulk:
    """The bulk side of a folder remote (or any folder of objects)."""

    def __init__(self, base: Path):
        self.base = Path(base).expanduser()

    def describe(self):
        return {'kind': 'folder', 'base': str(self.base), 'visibility': 'private'}

    def _object(self, digest: str) -> Path:
        if not isinstance(digest, str) or not DIGEST.fullmatch(digest):
            raise ChangedOutsideHooke('An object name is not a SHA-256')
        return self.base / 'objects' / digest[:2] / digest

    def missing(self, digests):
        return [d for d in digests if not self._object(d).is_file()]

    def put(self, digest, path, size, *, check=lambda: None):
        target = self._object(digest)
        if _intact(target, digest, size):
            return
        def write(stream):
            value = hashlib.sha256()
            written = 0
            with open(path, 'rb') as source:
                for block in iter(lambda: source.read(1 << 20), b''):
                    check()
                    value.update(block)
                    written += len(block)
                    stream.write(block)
            if written != size or value.hexdigest() != digest:
                raise RemoteRefused('A large file changed while sending; nothing was sent')
        _publish(target, write)

    def get(self, digest, size, target, *, check=lambda: None):
        source = self._object(digest)
        if not source.is_file():
            raise RemoteRefused('A large file of this version is not in its store')
        value = hashlib.sha256()
        written = 0
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as output, open(source, 'rb') as stream:
            for block in iter(lambda: stream.read(1 << 20), b''):
                check()
                written += len(block)
                if written > size:
                    raise ChangedOutsideHooke('A large file in the store is not the size its version says')
                value.update(block)
                output.write(block)
            output.flush()
            os.fsync(output.fileno())
        if written != size or value.hexdigest() != digest:
            raise ChangedOutsideHooke('A large file in the store was changed outside Hooke')


def folder_remote(base: Path, *, create=False) -> Remote:
    base = Path(base).expanduser()
    return Remote(FolderRemote(base, create=create), FolderBulk(base))
