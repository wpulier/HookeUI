"""The record and bulk layers of a sealed Project snapshot, and safe remote paths (format v2, ADR 0028).

Classification is by path and size only. Bulk members are content-addressed chunks and anything over 1 MiB; every
other member is the record layer. Nothing here reads research meaning from a file.
"""
from __future__ import annotations

import hashlib
import json
import re

from .project_roots import ProjectLocationError

BULK_BYTES = 1 << 20
MAX_RECORD_BLOB = 5 << 20
MAX_ENTRIES = 100_000
MAX_PATH = 1024
CHUNK = re.compile(r'artifacts/records/p[0-9a-f]{32}/(?:files|arrays)/([0-9a-f]{64})\.bin')
DIGEST = re.compile(r'[0-9a-f]{64}')
ORIGIN = re.compile(r'[0-9a-f]{32}')
# Root names a remote tree may hold besides project/ and .hooke/ (format v2, section 3).
ROOT_FILES = frozenset({'README.md', 'LICENSE', 'CITATION.cff', 'ro-crate-metadata.json'})
HOOKE_FILES = frozenset({'.hooke/snapshot.json', '.hooke/bulk.json'})
BULK_FORMAT = 'hooke-bulk-v1'
SNAPSHOT_FORMAT = 'hooke-project-folder-snapshot-v1'


class RemoteRefused(ProjectLocationError):
    """A remote, its contents or a request was refused; nothing was changed."""


class ChangedOutsideHooke(RemoteRefused):
    pass


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def is_bulk(entry) -> bool:
    return CHUNK.fullmatch(entry['path']) is not None or entry['bytes'] > BULK_BYTES


def classify(manifest):
    """(record entries, bulk entries) of a validated snapshot manifest, each sorted by path."""
    record, bulk = [], []
    for entry in manifest['entries']:
        (bulk if is_bulk(entry) else record).append(entry)
    return sorted(record, key=lambda e: e['path']), sorted(bulk, key=lambda e: e['path'])


def snapshot_manifest(raw: bytes, *, origin=None):
    """Parse and shape-check snapshot manifest bytes from any source (local or remote)."""
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ChangedOutsideHooke('The snapshot manifest is unreadable') from error
    if (type(value) is not dict or set(value) != {'format', 'origin', 'scope', 'externalDependencies', 'directories', 'entries'}
            or value['format'] != SNAPSHOT_FORMAT or not isinstance(value['origin'], str)
            or not ORIGIN.fullmatch(value['origin']) or (origin is not None and value['origin'] != origin)
            or type(value['entries']) is not list or len(value['entries']) > MAX_ENTRIES
            or type(value['directories']) is not list or len(value['directories']) > MAX_ENTRIES):
        raise ChangedOutsideHooke('The snapshot manifest is not a Hooke Project snapshot')
    seen = set()
    for directory in value['directories']:
        project_path(directory)
    for entry in value['entries']:
        if (type(entry) is not dict or set(entry) != {'path', 'bytes', 'sha256'} or type(entry['bytes']) is not int
                or entry['bytes'] < 0 or not isinstance(entry['sha256'], str) or not DIGEST.fullmatch(entry['sha256'])):
            raise ChangedOutsideHooke('The snapshot manifest lists an invalid member')
        project_path(entry['path'])
        folded = entry['path'].casefold()
        if folded in seen:
            raise ChangedOutsideHooke('The snapshot lists two members whose names differ only by case')
        seen.add(folded)
        chunk = CHUNK.fullmatch(entry['path'])
        if chunk and chunk.group(1) != entry['sha256']:
            raise ChangedOutsideHooke('A content-addressed member does not match its name')
    return value


def project_path(path: str) -> str:
    """A snapshot-relative path that is safe to create under a new folder."""
    if (not isinstance(path, str) or not path or len(path) > MAX_PATH or path.startswith('/') or '\\' in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        raise ChangedOutsideHooke('A Project path is not portable')
    parts = path.split('/')
    if any(part in ('', '.', '..') for part in parts):
        raise ChangedOutsideHooke('A Project path is not portable')
    top = parts[0]
    if top not in ('project.json', 'README.md', 'inputs', 'artifacts', 'history', 'workspace', 'exports'):
        raise ChangedOutsideHooke('A Project path is outside the portable sections')
    return path


def remote_path(path: str) -> str:
    """A remote tree path: a root file, a .hooke file, or project/<snapshot path>."""
    if path in ROOT_FILES or path in HOOKE_FILES:
        return path
    if isinstance(path, str) and path.startswith('project/'):
        project_path(path[len('project/'):])
        return path
    raise ChangedOutsideHooke('The remote holds a file outside a Hooke Project layout')


def bulk_document(origin, snapshot_sha, bulk_entries, stores):
    return canonical({'format': BULK_FORMAT, 'origin': origin, 'snapshotSha256': snapshot_sha,
                      'entries': [dict(e) for e in bulk_entries], 'stores': list(stores)})


def bulk_manifest(raw: bytes, *, origin, snapshot_sha, expected):
    """Parse hooke-bulk.json and require it to list exactly the snapshot's bulk members."""
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ChangedOutsideHooke('The bulk manifest is unreadable') from error
    if (type(value) is not dict or set(value) != {'format', 'origin', 'snapshotSha256', 'entries', 'stores'}
            or value['format'] != BULK_FORMAT or value['origin'] != origin or value['snapshotSha256'] != snapshot_sha
            or type(value['entries']) is not list or type(value['stores']) is not list or len(value['stores']) > 16):
        raise ChangedOutsideHooke('The bulk manifest does not belong to this snapshot')
    if value['entries'] != [dict(e) for e in expected]:
        raise ChangedOutsideHooke('The bulk manifest does not list exactly the snapshot’s large members')
    return value


TRAILERS = ('Hooke-Snapshot', 'Hooke-Origin', 'Hooke-Format')


def commit_message(summary: str, snapshot_sha: str, origin: str) -> str:
    summary = ' '.join(str(summary).split())[:200] or 'Hooke snapshot'
    return f'{summary}\n\nHooke-Snapshot: {snapshot_sha}\nHooke-Origin: {origin}\nHooke-Format: 2\n'


def trailers(message: str):
    """The Hooke trailers of a commit message; a missing or malformed one means it was not made by Hooke."""
    found = {}
    for line in str(message).splitlines()[-8:]:
        key, sep, value = line.partition(': ')
        if sep and key in TRAILERS:
            found[key] = value.strip()
    if (set(found) != set(TRAILERS) or not DIGEST.fullmatch(found['Hooke-Snapshot'])
            or not ORIGIN.fullmatch(found['Hooke-Origin']) or found['Hooke-Format'] != '2'):
        raise ChangedOutsideHooke('This version was not made by Hooke, or was changed outside Hooke')
    return found['Hooke-Snapshot'], found['Hooke-Origin']
