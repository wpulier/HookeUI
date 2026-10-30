"""Verified, immutable copies of an already routed Project folder.

This is the byte-copy stage, not the legacy dependency collector. Its manifest
explicitly makes no claim that external/shared dependencies were collected.
Inactive copies acquire their own writer lease. The owning host may copy under
its existing lease; every admitted byte and membership is checked for change.
No journal event, accepted revision, grant or registry entry is rewritten here.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

from .project_repository import document_root, session_root, verify_owner
from .project_roots import COPY_INTENT, PARTS, SNAPSHOT, ProjectLocationError, ProjectRoot, writer
from .scientific_projects import ProjectStore
from .project_snapshot_integrity import review_dependencies, verify_records

MAX_FILES = 100_000
MAX_DEPTH = 64
MAX_MANIFEST = 32 << 20
HEADROOM = 64 << 20
# These host associations and lock inodes are recreated on an explicit working
# import. They are neither research evidence nor transferable authority.
EXCLUDED = {'artifacts/records/.runtime-roots.json', 'artifacts/records/.runtime-bindings.lock',
            'artifacts/records/.bundle-visibility.lock', 'history/sessions/.bundle-visibility.lock',
            'history/sessions/.owners.lock'}


def _stamp(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


@dataclass(frozen=True)
class Member:
    name: str
    size: int
    stamp: tuple


def _members(root, check, directories=None):
    found = []
    directories = [] if directories is None else directories
    def visit(path, depth=0):
        check()
        if depth > MAX_DEPTH:
            raise ProjectLocationError('Project folders exceed the supported nesting depth; reorganize working files before collection')
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if relative in EXCLUDED or re.fullmatch(r'artifacts/records/p[0-9a-f]{32}/\.write\.lock', relative):
            return
        if stat.S_ISDIR(info.st_mode):
            directories.append(relative)
            if len(directories) > MAX_FILES:
                raise ProjectLocationError('This Project folder exceeds the supported directory count')
            for child in sorted(path.iterdir()):
                visit(child, depth + 1)
        elif stat.S_ISREG(info.st_mode):
            found.append(Member(relative, info.st_size, _stamp(info)))
            if len(found) > MAX_FILES:
                raise ProjectLocationError('This Project folder exceeds the supported file count')
        else:
            raise ProjectLocationError('A Project copy cannot follow linked or special files; collect the dependency explicitly')
    # Only the documented Project sections are portable. Host configuration
    # accidentally placed beside them is not swept into research exports.
    for name in ('project.json', 'README.md', *PARTS):
        visit(root / name)
    return found


def _stream(path, expected=None, output=None, check=lambda: None):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode) or (expected and _stamp(before) != expected):
            raise ProjectLocationError('Project bytes changed during copying; keep the original and retry')
        digest = hashlib.sha256()
        remaining = before.st_size
        while remaining:
            check()
            block = source.read(min(remaining, 1 << 20))
            if not block:
                raise ProjectLocationError('Project bytes changed during copying; keep the original and retry')
            remaining -= len(block)
            digest.update(block)
            if output is not None:
                output.write(block)
        check()
        if source.read(1):
            raise ProjectLocationError('Project bytes changed during copying; keep the original and retry')
        if _stamp(os.fstat(source.fileno())) != _stamp(before) or _stamp(path.lstat()) != _stamp(before):
            raise ProjectLocationError('Project bytes changed during copying; keep the original and retry')
        return before.st_size, digest.hexdigest()


def estimate(root: ProjectRoot, *, check=lambda: None):
    """A bounded size estimate; admission repeats it under the writer lease."""
    verify_owner(root)
    members = _members(root.path, check)
    size = sum(member.size for member in members)
    return {'files': len(members), 'bytes': size, 'requiredFreeBytes': size + MAX_MANIFEST + HEADROOM,
            'scope': 'project-folder', 'externalDependencies': 'not-verified',
            'dependencyReview': review_dependencies(root, check=check)}


def copy_snapshot(root: ProjectRoot, destination: Path, *, check=lambda: None):
    """Copy to a new directory, retaining an explicit incomplete marker on failure.

    An exclusive folder reservation prevents overwriting any existing destination.
    Removing the durable intent is the sole completion publication. The resulting
    snapshot remains read-only; it is never registered or automatically executed.
    """
    with writer(root):
        return _copy_admitted(root, destination, check=check)


def copy_owned_snapshot(locations, session, destination: Path, *, check=lambda: None):
    """Copy under this host's retained writer lease, without releasing or replaying work.

    Concurrent source mutations refuse publication; a stopped/relocated host does
    too. This copies retained folder bytes only, not external dependencies.
    """
    root = locations.project(session)
    if root is None:
        raise ProjectLocationError('Collect legacy dependencies before exporting a Project folder')
    locations.writable(session)
    def owned():
        check()
        locations.check_writer(root)
    owned()
    return _copy_admitted(root, destination, check=owned)


def _copy_admitted(root, destination, *, check):
    # Bundle publication spans both stores. A host writer lease excludes other
    # hosts, not its own importer; keep both existing visibility guards through
    # acceptance so a stable pending transaction cannot become a sealed copy.
    from .scientific_import_transaction import reading
    with reading(document_root(root)), reading(session_root(root)):
        return _copy_visible(root, destination, check=check)


def restore_snapshot(location: Path, destination: Path, *, check=lambda: None):
    """Make an explicitly requested working copy; do not register, grant or run it.

    This preserves the original Project identity. Choosing this location over an
    existing one remains a separate registry action, with writer/collision checks.
    A partial snapshot stays partial: its closure status survives in the receipt.
    """
    from .scientific_import_transaction import reading
    root = ProjectRoot.open(location)
    with reading(document_root(root)), reading(session_root(root)):
        snapshot_stamp = _stamp((root.path / SNAPSHOT).lstat())
        snapshot = ProjectStore._read(root.path / SNAPSHOT, MAX_MANIFEST)
        verify_snapshot(location, check=check)

        def unchanged():
            check()
            if _stamp((root.path / SNAPSHOT).lstat()) != snapshot_stamp:
                raise ProjectLocationError('The snapshot manifest changed during restoration; preserve the original')

        unchanged()
        return _copy_visible(root, destination, check=unchanged, restored_from=snapshot)


def _copy_visible(root, destination, *, check, restored_from=None):
    destination = Path(destination).expanduser()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination == root.path or root.path in destination.parents:
        raise ProjectLocationError('Choose a snapshot destination outside the source Project')
    verify_owner(root)
    directories = []
    members = _members(root.path, check, directories)
    required = sum(item.size for item in members) + MAX_MANIFEST + HEADROOM
    if shutil.disk_usage(parent).free < required:
        raise ProjectLocationError(f'Project copying needs {required} free bytes; no copy was started')
    verify_records(root, check=check)
    check()
    destination.mkdir(mode=0o700)
    intent = destination / COPY_INTENT
    ProjectStore._write(intent, b'Copy incomplete. Preserve the original Project.\n')
    ProjectStore._sync_directory(destination)
    ProjectStore._sync_directory(parent)
    entries = []
    # Empty retained folders are part of the storage layout too.
    for directory in directories:
        (destination / directory).mkdir(parents=True, exist_ok=True)
    for member in members:
        check()
        target = destination / member.name
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open('xb') as output:
            size, digest = _stream(root.path / member.name, member.stamp, output, check)
            # Immutable scientific objects must remain eligible for retained-source
            # intake after restore. Older snapshots lost this mode, so reconstruct
            # it from the store-owned object layout, not arbitrary source modes.
            immutable = re.fullmatch(r'artifacts/records/p[0-9a-f]{32}/(?:files|arrays)/[0-9a-f]{64}\.(?:bin|json)', member.name)
            output.flush()
            os.fchmod(output.fileno(), 0o400 if immutable else 0o600)
            os.fsync(output.fileno())
        if _stream(target, check=check) != (size, digest):
            raise ProjectLocationError('A copied file failed its hash check; the original remains intact')
        entries.append({'path': member.name, 'bytes': size, 'sha256': digest})
    current_directories = []
    if _members(root.path, check, current_directories) != members or current_directories != directories:
        raise ProjectLocationError('Project membership changed during copying; preserve the incomplete copy and retry')
    # Detect writes after an individual member was copied, before sealing.
    for member, entry in zip(members, entries):
        if _stream(root.path / member.name, member.stamp, check=check)[1] != entry['sha256']:
            raise ProjectLocationError('Project content changed before the snapshot was sealed')
    manifest = {'format': 'hooke-project-folder-snapshot-v1', 'origin': root.origin,
                'scope': 'project-folder', 'externalDependencies': 'not-verified',
                'directories': directories, 'entries': entries}
    body = json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()
    if len(body) > MAX_MANIFEST:
        raise ProjectLocationError('Project snapshot manifest exceeds its supported size')
    if restored_from is None:
        ProjectStore._write(destination / SNAPSHOT, body, immutable=True)
    else:
        if manifest != json.loads(restored_from):
            raise ProjectLocationError('Restored bytes do not match the source snapshot; preserve the incomplete copy')
        # The snapshot remains a source record, never an active root-level seal.
        # Retain its exact bytes and limitations without changing any journal or
        # accepted document. No host paths or new credentials enter this receipt.
        digest = hashlib.sha256(restored_from).hexdigest()
        receipt = destination / 'history' / 'restorations' / digest
        receipt.mkdir(parents=True, exist_ok=True)
        previous = receipt / SNAPSHOT
        if previous.exists():
            if ProjectStore._read(previous, MAX_MANIFEST) != restored_from:
                raise ProjectLocationError('Conflicting snapshot restoration evidence; preserve the incomplete copy')
        else:
            ProjectStore._write(previous, restored_from, immutable=True)
    # Shared readers use these new empty inodes even on read-only media.
    # Never carry an original writer's lock inode or acquisition state.
    for section in ('history/sessions', 'artifacts/records'):
        ProjectStore._write(destination / section / '.bundle-visibility.lock', b'')
    for folder in sorted((p for p in destination.rglob('*') if p.is_dir()), reverse=True):
        ProjectStore._sync_directory(folder)
    ProjectStore._sync_directory(destination)
    ProjectStore._sync_directory(parent)
    check()
    # These exact identity bytes were copied and checked above. Do not make
    # a further fallible read/sync after publishing completion: a reported
    # failure must still have the durable incomplete marker on restart.
    copied = ProjectRoot(destination, root.origin, root.session, root.name)
    intent.unlink()
    return copied


def verify_snapshot(location: Path, *, check=lambda: None):
    """Validate a sealed copy without replay, registration, or installed software."""
    root = ProjectRoot.open(location)
    raw = ProjectStore._read(root.path / SNAPSHOT, MAX_MANIFEST)
    try:
        value = json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ProjectLocationError('Project snapshot manifest is unreadable') from error
    if (type(value) is not dict or set(value) != {'format', 'origin', 'scope', 'externalDependencies', 'directories', 'entries'}
            or value['format'] != 'hooke-project-folder-snapshot-v1' or value['origin'] != root.origin
            or value['scope'] != 'project-folder' or value['externalDependencies'] != 'not-verified'
            or type(value['entries']) is not list or len(value['entries']) > MAX_FILES
            or type(value['directories']) is not list or len(value['directories']) > MAX_FILES):
        raise ProjectLocationError('Unsupported Project snapshot manifest')
    directories = []
    actual = {member.name: member for member in _members(root.path, check, directories)}
    if value['directories'] != directories:
        raise ProjectLocationError('Project snapshot directory membership does not match its manifest')
    if len(value['entries']) != len(actual):
        raise ProjectLocationError('Project snapshot membership does not match its manifest')
    seen = set()
    for item in value['entries']:
        if (type(item) is not dict or set(item) != {'path', 'bytes', 'sha256'}
                or not isinstance(item['path'], str) or item['path'] not in actual or item['path'] in seen
                or type(item['bytes']) is not int or item['bytes'] < 0
                or not isinstance(item['sha256'], str)):
            raise ProjectLocationError('Invalid Project snapshot member')
        seen.add(item['path'])
        if _stream(root.path / item['path'], check=check) != (item['bytes'], item['sha256']):
            raise ProjectLocationError('Project snapshot content does not match its manifest')
    verify_owner(root)
    verify_records(root, check=check)
    return {'origin': root.origin, 'files': len(actual), 'bytes': sum(m.size for m in actual.values()),
            'scope': 'project-folder', 'externalDependencies': 'not-verified'}
