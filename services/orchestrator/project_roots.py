"""Portable Project locations, independent of research records and host authority.

This foundation is not an export/migration path. A folder must contain its journal
and every retained dependency before a higher layer may declare it collected.
Registry entries locate an origin; they never grant access or reconstruct history.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
import tempfile

from .day.ownership import encode
from .day.sid import is_sid
from .file_locks import fcntl

LIMIT = 1 << 20
PARTS = ('inputs', 'artifacts', 'history', 'workspace', 'exports')
COPY_INTENT = '.project-copy-in-progress'
SNAPSHOT = 'snapshot.json'


class ProjectLocationError(ValueError):
    pass


def _read(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ProjectLocationError('Project metadata must be a regular file')
        raw = stream.read(LIMIT + 1)
    if len(raw) > LIMIT:
        raise ProjectLocationError('Project metadata exceeds its supported size')
    try:
        return json.loads(raw)
    except (ValueError, RecursionError) as error:
        raise ProjectLocationError('Project metadata is unreadable; keep the original folder') from error


def _atomic(path: Path, value):
    raw = encode(value)
    if len(raw) > LIMIT:
        raise ProjectLocationError('Project metadata exceeds its supported size')
    fd, name = tempfile.mkstemp(prefix='.project-write-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


def _origin(value):
    if not isinstance(value, str) or not re.fullmatch('[a-f0-9]{32}', value):
        raise ProjectLocationError('Invalid Project origin')
    return value


@dataclass(frozen=True)
class ProjectRoot:
    path: Path
    origin: str
    session: str
    name: str

    @classmethod
    def open(cls, location: Path):
        path = Path(location).expanduser()
        if path.is_symlink() or not path.is_dir():
            raise ProjectLocationError('Choose the Project folder itself')
        path = path.resolve(strict=True)
        if (path / COPY_INTENT).exists() or (path / COPY_INTENT).is_symlink():
            raise ProjectLocationError('This Project copy is incomplete; keep the original and retry collection to a new folder')
        value = _read(path / 'project.json')
        if (type(value) is not dict or set(value) != {'format', 'origin', 'session', 'name', 'storage'}
                or value['format'] != 1 or type(value['format']) is not int or value['storage'] != 'hooke-project-v1'
                or not isinstance(value['session'], str) or not is_sid(value['session'])
                or not isinstance(value['name'], str) or not 0 < len(value['name']) <= 240):
            raise ProjectLocationError('Unsupported Project manifest; keep this folder and use a compatible Hooke version')
        root = cls(path, _origin(value['origin']), value['session'], value['name'])
        for part in PARTS:
            root.folder(part)
        return root

    def folder(self, part: str) -> Path:
        if part not in PARTS:
            raise ProjectLocationError('Unknown Project storage section')
        result = self.path / part
        if result.is_symlink() or not result.is_dir():
            raise ProjectLocationError(f'Project {part} folder is missing or replaced; recover it before writing')
        return result


def create_layout(parent: Path, name: str, origin: dict) -> ProjectRoot:
    """Reserve a new layout from an already minted journal origin, without copying any research.

    The caller owns creation/migration publication. This function never registers a
    location or claims that an empty layout contains the Project's evidence.
    """
    if (type(origin) is not dict or origin.get('type') != 'session-origin'
            or not isinstance(origin.get('session'), str) or not is_sid(origin['session'])):
        raise ProjectLocationError('A Project layout requires its journal origin')
    identity = _origin(origin.get('origin'))
    if not isinstance(name, str) or not 0 < len(name.strip()) <= 240:
        raise ProjectLocationError('Give the Project a name of at most 240 characters')
    parent = Path(parent).expanduser()
    parent.mkdir(parents=True, exist_ok=True)
    # The display name remains exact in the manifest; its filesystem label is bounded.
    label = re.sub(r'[\\/:\x00-\x1f\x7f]', '-', name).strip(' .')[:80] or 'Project'
    path = parent / f'{label}—{identity[:8]}'
    path.mkdir(mode=0o700)  # never replace an existing folder, even with the same origin
    try:
        for part in PARTS:
            (path / part).mkdir(mode=0o700)
        (path / 'README.md').write_text(f'# {name}\n\nOpen this Project in Hooke. Accepted research records are managed by Hooke.\n')
        _atomic(path / 'project.json', {'format': 1, 'origin': identity, 'session': origin['session'], 'name': name, 'storage': 'hooke-project-v1'})
        return ProjectRoot.open(path)
    except BaseException:
        # Retain partial work for explicit recovery; never recursively remove a user's folder.
        raise


@contextmanager
def writer(root: ProjectRoot):
    """One writer on a filesystem with advisory locking; crash releases the lease.

    Missing locking support refuses. Remote/sync filesystem semantics still need
    qualification; this is not a distributed-lock or remote collaboration claim.
    Never unlink the lock inode: replacing it would let two hosts both hold locks.
    """
    if (root.path / SNAPSHOT).exists() or (root.path / SNAPSHOT).is_symlink():
        raise ProjectLocationError('This is a retained snapshot. Open read-only or explicitly create a working copy.')
    actual = ProjectRoot.open(root.path)
    if actual != root:
        raise ProjectLocationError('The Project location changed; reopen before writing')
    path = root.path / '.writer.lock'
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ProjectLocationError('The Project writer lock is invalid')
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ProjectLocationError('Another Hooke host is writing this Project. Open read-only until it closes.') from error
        yield root
    finally:
        os.close(fd)


class ProjectRegistry:
    """Host-local origin→location pointers. Research and grants never live here."""
    def __init__(self, home: Path):
        self.home = Path(home)
        self.path = self.home / 'project-locations.json'

    def _entries(self, *, recovering=None):
        try:
            self.path.lstat()
        except FileNotFoundError:
            return {}
        value = _read(self.path)
        if type(value) is not dict or set(value) != {'format', 'locations'} or type(value['format']) is not int or value['format'] not in (1, 2) or type(value['locations']) is not dict:
            raise ProjectLocationError('The Project location registry is unreadable; do not replace it')
        result = {}
        for key, location in value['locations'].items():
            _origin(key)
            if value['format'] == 1:
                if not isinstance(location, str) or not Path(location).is_absolute():
                    raise ProjectLocationError('The Project location registry contains an invalid path')
                # The old locator did not retain the host handle. A missing old location
                # must be explicitly recovered before allocating any possibly colliding handle.
                if recovering is not None and key == recovering.origin and not Path(location).exists() and not Path(location).is_symlink():
                    root = recovering
                else:
                    root = ProjectRoot.open(Path(location))
                if root.origin != key:
                    raise ProjectLocationError('The folder at this location belongs to another Project')
                location = {'path': location, 'session': root.session}
            if (type(location) is not dict or set(location) != {'path', 'session'}
                    or not isinstance(location['path'], str) or not Path(location['path']).is_absolute()
                    or not is_sid(location['session'])):
                raise ProjectLocationError('The Project location registry contains an invalid locator')
            result[key] = dict(location)
        return result

    def locations(self):
        """Host locators, including unavailable folders; no research or authority is cached here."""
        return self._entries()

    def locate(self, origin: str) -> ProjectRoot | None:
        location = self._entries().get(_origin(origin))
        if location is None:
            return None
        root = ProjectRoot.open(Path(location['path']))
        if root.origin != origin or root.session != location['session']:
            raise ProjectLocationError('The folder at this location belongs to another Project')
        return root

    def register(self, root: ProjectRoot, *, replace_inactive=False, require_known=False):
        self.home.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.home / '.project-locations.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            entries = self._entries(recovering=root if replace_inactive else None)
            if ProjectRoot.open(root.path) != root:
                raise ProjectLocationError('Project identity changed before registration')
            previous = entries.get(root.origin)
            if require_known and previous is None:
                raise ProjectLocationError('This Project is not registered here. Importing a new Project requires separate owner-established access.')
            if require_known and len({item['session'] for item in entries.values()}) != len(entries):
                raise ProjectLocationError('Two registered Project origins use the same host handle; the locator was not changed')
            if previous and previous['session'] != root.session:
                raise ProjectLocationError('A Project origin cannot change its retained session identity')
            def publish():
                if require_known:
                    from .project_repository import verify_owner
                    verify_owner(root)
                entries[root.origin] = {'path': str(root.path), 'session': root.session}
                _atomic(self.path, {'format': 2, 'locations': entries})
            if previous and Path(previous['path']) != root.path:
                if not replace_inactive:
                    raise ProjectLocationError('This Project is already known elsewhere. Choose read-only, replace its inactive location, or create an independent Project.')
                old_path = Path(previous['path'])
                if old_path.is_symlink():
                    raise ProjectLocationError('The previous Project location was replaced by a link')
                old = ProjectRoot.open(old_path) if old_path.exists() else None
                if old is not None:
                    if old.origin != root.origin:
                        raise ProjectLocationError('The previous location now contains another Project')
                    with writer(old):
                        with writer(root):
                            publish()
                    return
            with writer(root):
                publish()
        finally:
            os.close(fd)
