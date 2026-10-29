"""Verified, immutable copies of an already routed Project folder.

This is the byte-copy stage, not the legacy dependency collector. Its manifest
explicitly makes no claim that external/shared dependencies were collected.
The caller must settle admitted work and release the host writer before copying.
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

from .project_repository import verify_owner
from .project_roots import COPY_INTENT, PARTS, SNAPSHOT, ProjectLocationError, ProjectRoot, writer
from .scientific_projects import ProjectStore

MAX_FILES = 100_000
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
    def visit(path):
        check()
        info = path.lstat()
        relative = path.relative_to(root).as_posix()
        if relative in EXCLUDED or re.fullmatch(r'artifacts/records/p[0-9a-f]{32}/\.write\.lock', relative):
            return
        if stat.S_ISDIR(info.st_mode):
            directories.append(relative)
            if len(directories) > MAX_FILES:
                raise ProjectLocationError('This Project folder exceeds the supported directory count')
            for child in sorted(path.iterdir()):
                visit(child)
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
        while True:
            check()
            block = source.read(1 << 20)
            if not block:
                break
            digest.update(block)
            if output is not None:
                output.write(block)
        if _stamp(os.fstat(source.fileno())) != _stamp(before) or _stamp(path.lstat()) != _stamp(before):
            raise ProjectLocationError('Project bytes changed during copying; keep the original and retry')
        return before.st_size, digest.hexdigest()


def estimate(root: ProjectRoot, *, check=lambda: None):
    """A bounded size estimate; admission repeats it under the writer lease."""
    verify_owner(root)
    members = _members(root.path, check)
    size = sum(member.size for member in members)
    return {'files': len(members), 'bytes': size, 'requiredFreeBytes': size + MAX_MANIFEST + HEADROOM,
            'scope': 'project-folder', 'externalDependencies': 'not-verified'}


def copy_snapshot(root: ProjectRoot, destination: Path, *, check=lambda: None):
    """Copy to a new directory, retaining an explicit incomplete marker on failure.

    An exclusive folder reservation prevents overwriting any existing destination.
    Removing the durable intent is the sole completion publication. The resulting
    snapshot remains read-only; it is never registered or automatically executed.
    """
    destination = Path(destination).expanduser()
    parent = destination.parent.resolve(strict=True)
    destination = parent / destination.name
    if destination == root.path or root.path in destination.parents:
        raise ProjectLocationError('Choose a snapshot destination outside the source Project')
    with writer(root):
        verify_owner(root)
        directories = []
        members = _members(root.path, check, directories)
        required = sum(item.size for item in members) + MAX_MANIFEST + HEADROOM
        if shutil.disk_usage(parent).free < required:
            raise ProjectLocationError(f'Project copying needs {required} free bytes; no copy was started')
        check()
        destination.mkdir(mode=0o700)
        intent = destination / COPY_INTENT
        ProjectStore._write(intent, b'Copy incomplete. Preserve the original Project.\n')
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
                output.flush(); os.fsync(output.fileno())
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
        ProjectStore._write(destination / SNAPSHOT, body, immutable=True)
        # Shared readers use these new empty inodes even on read-only media.
        # Never carry an original writer's lock inode or acquisition state.
        for section in ('history/sessions', 'artifacts/records'):
            ProjectStore._write(destination / section / '.bundle-visibility.lock', b'')
        for folder in sorted((p for p in destination.rglob('*') if p.is_dir()), reverse=True):
            ProjectStore._sync_directory(folder)
        ProjectStore._sync_directory(destination)
        check()
        intent.unlink()
        ProjectStore._sync_directory(destination)
        return ProjectRoot.open(destination)


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
    return {'origin': root.origin, 'files': len(actual), 'bytes': sum(m.size for m in actual.values()),
            'scope': 'project-folder', 'externalDependencies': 'not-verified'}
