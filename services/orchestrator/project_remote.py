"""Push a sealed Project snapshot to a remote, and fetch one back as a verified sealed snapshot (ADR 0028).

A remote is a record store (commits of small files) plus a bulk store (objects named by SHA-256). The order of a push
is fixed: bulk objects first, then one commit, then a compare-and-swap of the remote's pointer, so a commit never
names an object that is missing and a retry is always safe. A fetch writes nothing visible until every byte has been
checked against the snapshot manifest and `verify_snapshot` passes.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import os
from pathlib import Path
from typing import Callable, Protocol

from .project_folder_copy import MAX_MANIFEST, verify_snapshot
from .project_remote_layers import (HOOKE_FILES, MAX_ENTRIES, MAX_RECORD_BLOB, ChangedOutsideHooke, RemoteRefused,
                                    bulk_document, bulk_manifest, classify, commit_message, remote_path, sha256,
                                    snapshot_manifest, trailers)
from .project_roots import COPY_INTENT, SNAPSHOT, ProjectRoot
from .project_secret_scan import blocking, said, scan

NOTICE = ('# Hooke Project\n\nThis folder holds a Hooke Project snapshot. Open it in Hooke with '
          '“Start from this project”; Hooke checks every file before showing it.\n')


class Diverged(RemoteRefused):
    pass


class SecretsFound(RemoteRefused):
    def __init__(self, findings):
        super().__init__(said(blocking(findings)) + ' Nothing was sent.')
        self.findings = findings


class RecordRemote(Protocol):
    def head(self) -> str | None: ...
    def read_commit(self, commit: str) -> dict: ...
    def read_blob(self, ref: str, max_bytes: int) -> bytes: ...
    def write_commit(self, files: list, *, parent: str | None, message: str, check=lambda: None) -> str: ...
    def advance(self, old: str | None, new: str) -> None: ...
    def describe(self) -> dict: ...


class BulkStore(Protocol):
    def missing(self, digests: list[str]) -> list[str]: ...
    def put(self, digest: str, path: Path, size: int, *, check=lambda: None) -> None: ...
    def get(self, digest: str, size: int, target: Path, *, check=lambda: None) -> None: ...
    def describe(self) -> dict: ...


@dataclass
class Remote:
    records: RecordRemote
    bulk: BulkStore
    stores: list = field(default_factory=list)  # extra advisory stores for hooke-bulk.json

    def describe(self):
        return {'records': self.records.describe(), 'bulk': self.bulk.describe()}


@dataclass(frozen=True)
class RemoteFile:
    """One file of a commit: its remote path, size and a reader returning its exact bytes."""
    path: str
    size: int
    read: Callable[[], bytes]
    sha256: str | None = None


def _reader(path: Path, size: int, digest: str):
    def read():
        body = path.read_bytes()
        if len(body) != size or sha256(body) != digest:
            raise RemoteRefused('A snapshot file changed after sealing; nothing was sent')
        return body
    return read


def sealed_manifest(sealed: Path):
    root = ProjectRoot.open(sealed)
    raw = (root.path / SNAPSHOT).read_bytes()
    if len(raw) > MAX_MANIFEST:
        raise RemoteRefused('The snapshot manifest is too large to send')
    return root, raw, snapshot_manifest(raw, origin=root.origin)


_SAME = object()


def push_sealed(sealed: Path, remote: Remote, *, parent: str | None, summary: str, readme: bytes | None = None,
                root_files: dict | None = None, scan_findings=None, expected_head=_SAME, check=lambda: None):
    """Send one verified sealed snapshot. Returns a receipt; raises before moving the pointer on any refusal.

    `parent` is the new commit's parent. `expected_head` is the pointer it replaces, normally the parent; a new Git
    host repository's own first commit is the one exception (its records remote replaces only that placeholder).
    """
    expected = parent if expected_head is _SAME else expected_head
    sealed = Path(sealed)
    verify_snapshot(sealed, check=check)
    root, raw, manifest = sealed_manifest(sealed)
    snapshot_sha = sha256(raw)
    record, bulk = classify(manifest)
    too_big = [e['path'] for e in record if e['bytes'] > MAX_RECORD_BLOB]
    if too_big:
        raise RemoteRefused(f'{too_big[0]} is too large for the record layer; nothing was sent')
    findings = scan(root.path, record, check=check) if scan_findings is None else scan_findings
    if blocking(findings):
        raise SecretsFound(findings)
    head = remote.records.head()
    if head != expected:
        raise Diverged('This project changed in two places: the remote has a version this Mac did not send. '
                       'Nothing was sent. Start an independent copy, or update first.')
    # 1. Bulk objects, each verified by the store; one object serves every path with its content.
    first = {}
    for entry in bulk:
        first.setdefault(entry['sha256'], entry)
    wanted = sorted(first)
    uploaded = 0
    for digest in remote.bulk.missing(wanted):
        check()
        entry = first[digest]
        remote.bulk.put(digest, root.path / entry['path'], entry['bytes'], check=check)
        uploaded += entry['bytes']
    # 2. One commit naming the record layer, the manifest and the bulk manifest.
    stores = [remote.bulk.describe(), *remote.stores]
    bulk_raw = bulk_document(root.origin, snapshot_sha, bulk, stores)
    files = [RemoteFile('README.md', len(readme or NOTICE.encode()), (lambda b=readme or NOTICE.encode(): b)),
             RemoteFile('.hooke/snapshot.json', len(raw), (lambda: raw), snapshot_sha),
             RemoteFile('.hooke/bulk.json', len(bulk_raw), (lambda: bulk_raw), sha256(bulk_raw))]
    for name, body in sorted((root_files or {}).items()):
        files.append(RemoteFile(remote_path(name), len(body), (lambda b=body: b), sha256(body)))
    for entry in record:
        files.append(RemoteFile('project/' + entry['path'], entry['bytes'],
                                _reader(root.path / entry['path'], entry['bytes'], entry['sha256']), entry['sha256']))
    if len({f.path for f in files}) != len(files):
        raise RemoteRefused('Two files would share one remote path; nothing was sent')
    commit = remote.records.write_commit(files, parent=parent, message=commit_message(summary, snapshot_sha, root.origin),
                                         check=check)
    # 3. Compare-and-swap: refuse rather than overwrite a version sent from elsewhere meanwhile.
    remote.records.advance(expected, commit)
    return {'commit': commit, 'parent': parent, 'origin': root.origin, 'snapshotSha256': snapshot_sha,
            'recordFiles': len(record), 'bulkObjects': len(wanted), 'uploadedBytes': uploaded,
            'bytes': sum(e['bytes'] for e in manifest['entries']), 'findings': findings,
            'remote': remote.describe()}


def _write_new(path: Path, body: bytes, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body)
        stream.flush()
        os.fsync(stream.fileno())


def fetch(remote: Remote, destination: Path, *, commit: str | None = None, check=lambda: None):
    """Fetch one commit into a new folder as a sealed snapshot. Returns (ProjectRoot, details)."""
    commit = commit or remote.records.head()
    if commit is None:
        raise RemoteRefused('Nothing has been sent to this remote yet')
    value = remote.records.read_commit(commit)
    snapshot_sha, origin = trailers(value.get('message', ''))
    entries = value.get('entries')
    if type(entries) is not list or len(entries) > MAX_ENTRIES + 16:
        raise ChangedOutsideHooke('This version lists more files than a Hooke Project can hold')
    by_path, folded = {}, set()
    for item in entries:
        if type(item) is not dict or not isinstance(item.get('path'), str) or type(item.get('bytes')) is not int:
            raise ChangedOutsideHooke('This version lists an invalid file')
        path = remote_path(item['path'])
        if path.casefold() in folded:
            raise ChangedOutsideHooke('This version holds two files whose names differ only by case')
        folded.add(path.casefold())
        by_path[path] = item
    if not HOOKE_FILES <= set(by_path):
        raise ChangedOutsideHooke('This version is not a Hooke Project')
    raw = remote.records.read_blob(by_path['.hooke/snapshot.json']['ref'], MAX_MANIFEST)
    if sha256(raw) != snapshot_sha:
        raise ChangedOutsideHooke('The snapshot manifest does not match the version that named it')
    manifest = snapshot_manifest(raw, origin=origin)
    record, bulk = classify(manifest)
    bulk_raw = remote.records.read_blob(by_path['.hooke/bulk.json']['ref'], MAX_MANIFEST)
    bulk_value = bulk_manifest(bulk_raw, origin=origin, snapshot_sha=snapshot_sha, expected=bulk)
    expected = {'project/' + e['path']: e for e in record}
    held = {p for p in by_path if p.startswith('project/')}
    if held != set(expected):
        raise ChangedOutsideHooke('The files of this version do not match its snapshot manifest')
    for path, entry in expected.items():
        if by_path[path]['bytes'] != entry['bytes']:
            raise ChangedOutsideHooke('A file of this version does not match its snapshot manifest')
    destination = Path(destination).expanduser()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    destination.mkdir(mode=0o700)
    intent = destination / COPY_INTENT
    _write_new(intent, b'Fetch incomplete. Nothing here is verified.\n')
    for directory in manifest['directories']:
        (destination / directory).mkdir(parents=True, exist_ok=True)
    for path, entry in expected.items():
        check()
        body = remote.records.read_blob(by_path[path]['ref'], entry['bytes'])
        if len(body) != entry['bytes'] or sha256(body) != entry['sha256']:
            raise ChangedOutsideHooke(f'{entry["path"]} does not match its snapshot manifest')
        _write_new(destination / entry['path'], body)
    for entry in bulk:
        check()
        target = destination / entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        remote.bulk.get(entry['sha256'], entry['bytes'], target, check=check)
        digest = hashlib.sha256()
        with target.open('rb') as stream:
            for block in iter(lambda: stream.read(1 << 20), b''):
                digest.update(block)
        if target.stat().st_size != entry['bytes'] or digest.hexdigest() != entry['sha256']:
            raise ChangedOutsideHooke(f'{entry["path"]} arrived damaged')
        os.chmod(target, 0o400)
    _write_new(destination / SNAPSHOT, raw, 0o400)
    # Readers take these shared locks even on read-only media; they are not snapshot members.
    for section in ('history/sessions', 'artifacts/records'):
        if (destination / section).is_dir() and not (destination / section / '.bundle-visibility.lock').exists():
            _write_new(destination / section / '.bundle-visibility.lock', b'')
    intent.unlink()
    try:
        verify_snapshot(destination, check=check)
    except BaseException:
        _write_new(intent, b'Fetch failed verification. Nothing here is trusted.\n')
        raise
    return ProjectRoot.open(destination), {'commit': commit, 'origin': origin, 'snapshotSha256': snapshot_sha,
                                           'files': len(manifest['entries']), 'stores': bulk_value['stores']}
