"""Captured-owner snapshot bytes; legacy layout stays compatible.

Publication is gated by a pending marker, with hashes for both annotated and clean
pictures. A snapshot is not a complete Project collection or a Library migration.
"""
import hashlib
import json
from pathlib import Path
import re
import stat

from .day import sid as sids
from .project_artifact_folder import ArtifactFolder
from .project_roots import ProjectLocationError
from .scientific_projects import ProjectStore

NAME = re.compile(r'snapshot-\d{6}-(?:[0-9a-f]{12}-)?f\d+')
MAX_META = 16 << 20
MAX_PICTURE = 128 << 20


class Snapshots:
    def __init__(self, legacy, day):
        self.day = day
        self.storage = ArtifactFolder(legacy, 'snapshots')
        self._folders = {}

    def portable(self, session):
        locations = getattr(self.day, 'locations', None)
        return locations is not None and locations.project(session) is not None

    def folder(self, session, date, *, write=False):
        if not sids.is_sid(date) or sids.parse(date)[1] != 1:
            raise ProjectLocationError('A snapshot needs its captured date')
        root = self.storage.root_for(self.day, session, write=write)
        folder = root / date
        if write:
            folder.mkdir(parents=True, exist_ok=True)
        if folder.exists() or folder.is_symlink():
            info = folder.stat(follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ProjectLocationError('Snapshot storage was replaced')
            identity = info.st_dev, info.st_ino
            if self._folders.setdefault(folder, identity) != identity:
                raise ProjectLocationError('Snapshot storage was replaced')
        return folder

    def save(self, session, date, name, facts, picture, clean=None, *, admitted=None):
        if not NAME.fullmatch(name) or facts.get('session') != session:
            raise ProjectLocationError('A snapshot needs its captured identity and owner')
        folder = self.folder(session, date, write=True)
        if admitted is not None and folder != admitted:
            raise ProjectLocationError('The snapshot Project moved before publication')
        portable = self.portable(session)
        if not portable:
            (folder / f'{name}.png').write_bytes(picture)
            if clean is not None:
                (folder / 'clean').mkdir(exist_ok=True)
                (folder / 'clean' / f'{name}.png').write_bytes(clean)
            (folder / f'{name}.json').write_text(json.dumps(facts, indent=1, default=float))
            return folder
        pending = folder / f'{name}.pending'
        if any((folder / f'{name}{suffix}').exists() or (folder / f'{name}{suffix}').is_symlink()
               for suffix in ('.json', '.png', '.pending')):
            raise ProjectLocationError('Snapshot identity already exists; preserve it and take a new snapshot')
        ProjectStore._write(pending, b'Snapshot publication incomplete.\n', immutable=True)
        # Make the uncertainty durable before writing any accepted-looking bytes.
        ProjectStore._sync_directory(folder)
        ProjectStore._sync_directory(folder.parent)
        images = {f'{name}.png': picture}
        if clean is not None:
            clean_folder = folder / 'clean'
            clean_folder.mkdir(exist_ok=True)
            if not stat.S_ISDIR(clean_folder.stat(follow_symlinks=False).st_mode):
                raise ProjectLocationError('Snapshot clean-picture storage was replaced')
            images[f'clean/{name}.png'] = clean
        for relative, data in images.items():
            if self.folder(session, date, write=True) != folder:
                raise ProjectLocationError('The snapshot Project moved before publication')
            ProjectStore._write(folder / relative, data, immutable=True)
        record = {**facts, 'files': {key: hashlib.sha256(data).hexdigest() for key, data in images.items()}}
        ProjectStore._write(folder / f'{name}.json', json.dumps(record, indent=1, default=float).encode(), immutable=True)
        self.folder(session, date, write=True)
        if clean is not None:
            ProjectStore._sync_directory(folder / 'clean')
        ProjectStore._sync_directory(folder)
        ProjectStore._sync_directory(folder.parent)
        # Last fallible action: failure leaves the durable marker. A crash may
        # conservatively restore it, but cannot expose a failed publication.
        pending.unlink()
        return folder

    def read(self, session, date, name):
        if not NAME.fullmatch(name):
            raise ProjectLocationError('Invalid snapshot identity')
        folder = self.folder(session, date)
        if (folder / f'{name}.pending').exists() or (folder / f'{name}.pending').is_symlink():
            return None
        record = folder / f'{name}.json'
        if not record.exists() and not record.is_symlink():
            return None
        facts = json.loads(ProjectStore._read(record, MAX_META))
        if not isinstance(facts, dict) or sids.session_of(facts, date) != session:
            return None
        if self.portable(session):
            files = facts.get('files')
            allowed = {f'{name}.png', f'clean/{name}.png'}
            if not isinstance(files, dict) or f'{name}.png' not in files or not set(files) <= allowed:
                raise ProjectLocationError('Snapshot has no retained picture identities')
        return facts

    def picture(self, session, date, name, *, clean=False, max_bytes=None):
        facts = self.read(session, date, name)
        if facts is None:
            return None
        folder = self.folder(session, date)
        relative = f'clean/{name}.png' if clean else f'{name}.png'
        if clean and not stat.S_ISDIR((folder / 'clean').stat(follow_symlinks=False).st_mode):
            raise ProjectLocationError('Snapshot clean-picture storage was replaced')
        data = ProjectStore._read(folder / relative, min(max_bytes, MAX_PICTURE) if max_bytes is not None else MAX_PICTURE)
        if self.portable(session) and hashlib.sha256(data).hexdigest() != facts['files'].get(relative):
            raise ProjectLocationError('Snapshot picture changed since publication')
        return data
