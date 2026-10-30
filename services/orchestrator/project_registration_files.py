"""Owner-captured volume/job files during the staged portable Project conversion.

Metadata snapshots preserve prior facts, not independent scientific acceptance.
The current registration record and existing result-publication rules still own status.
"""
import hashlib
import json
import re
import stat

from .day import sid as sids
from .project_artifact_folder import ArtifactFolder
from .project_roots import ProjectLocationError
from .scientific_projects import ProjectStore
from . import job_outputs


class RegistrationFiles:
    def __init__(self, legacy, day):
        self.day = day
        self.storage = ArtifactFolder(legacy, 'volumes')
        self._folders = {}

    def root_for(self, session, *, write=False):
        return self.storage.root_for(self.day, session, write=write)

    def portable(self, session):
        locations = getattr(self.day, 'locations', None)
        return locations is not None and locations.project(session) is not None

    def _directory(self, folder, *, create=False):
        if create:
            folder.mkdir(exist_ok=True)
        if folder.exists() or folder.is_symlink():
            info = folder.stat(follow_symlinks=False)
            identity = info.st_dev, info.st_ino
            if not stat.S_ISDIR(info.st_mode) or self._folders.setdefault(folder, identity) != identity:
                raise ProjectLocationError('Volume storage changed; reopen its original Project location')
        elif folder in self._folders:
            raise ProjectLocationError('Volume storage is missing; restore its original folder')
        return folder

    def date_folder(self, session, date, *, write=False):
        if not sids.is_sid(date) or sids.parse(date)[1] != 1:
            raise ProjectLocationError('Volume work needs its captured date')
        root = self.root_for(session, write=write)
        if write:
            root.mkdir(parents=True, exist_ok=True)
        return self._directory(root / date, create=write)

    def folder(self, record, *, write=False):
        if not re.fullmatch(r'[vj][1-9][0-9]*', record['id']):
            raise ProjectLocationError('Volume work needs its retained identity')
        parent = self.date_folder(record['session'], record['date'], write=write)
        return self._directory(parent / record['id'])

    def read(self, session, date, rid):
        folder = self.folder({'session': session, 'date': date, 'id': rid})
        record = json.loads(ProjectStore._read(folder / 'record.json', 16 << 20) if self.portable(session)
                            else (folder / 'record.json').read_text())
        if not isinstance(record, dict) or record.get('date') != date or record.get('id') != rid:
            raise ProjectLocationError('Volume metadata does not match its retained location')
        return record

    def write(self, record, check, atomic):
        folder = self.folder(record, write=True)
        path = folder / 'record.json'
        portable = self.portable(record['session'])
        if portable and path.exists():
            old = ProjectStore._read(path, 16 << 20)
            revisions = self._directory(folder / 'history', create=True)
            destination = revisions / (hashlib.sha256(old).hexdigest() + '.json')
            if destination.exists():
                if ProjectStore._read(destination, 16 << 20) != old:
                    raise ProjectLocationError('Volume metadata history changed')
            else:
                ProjectStore._write(destination, old, immutable=True)
        check()
        self.folder(record, write=True)
        body = json.dumps(record, indent=1).encode()
        if portable:
            self._sync_dependencies(record, folder, check)
            # Flush newly created item/date directories and their entries in the
            # captured service root before publishing metadata that refers to them.
            root = self.root_for(record['session'], write=True)
            current = folder
            while current != root:
                ProjectStore._sync_directory(current)
                current = current.parent
            ProjectStore._sync_directory(root)
            check()
            ProjectStore._write(path, body)
            check()
        else:
            atomic(path, body, check)

    def _sync_dependencies(self, record, folder, check):
        dependencies = {}
        if record.get('kind') == 'volume':
            dependencies[record.get('file')] = record.get('sha256')
        for output in record.get('outputs', {}).values():
            dependencies[output.get('path')] = output.get('sha256')
        made = record.get('made', {})
        if made.get('file'):
            dependencies['out/' + made['file']] = made.get('sha256')
        if made.get('picture'):
            dependencies[made['picture'].get('path')] = made['picture'].get('sha256')
        directories = set()
        with job_outputs.Folder(folder) as files:
            files.check = check
            for name, digest in dependencies.items():
                parts = job_outputs.parts(name)
                if not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
                    raise ProjectLocationError('Volume dependency has no retained digest')
                current = folder
                for part in parts[:-1]:
                    current = self._directory(current / part)
                    directories.add(current)
                files.sync(name, expected=digest)
        for directory in sorted(directories, key=lambda p: len(p.parts), reverse=True):
            check()
            self._directory(directory)
            ProjectStore._sync_directory(directory)

    def recoverable_jobs(self):
        """Only this host's writable Projects can be reconciled; never resume a copied job."""
        roots = [(None, self.storage.legacy)]
        locations = getattr(self.day, 'locations', None)
        if locations is not None:
            for session in locations.handles():
                # A reader must not kill or mark work owned by another live host.
                try:
                    root = self.root_for(session, write=True)
                except ProjectLocationError:
                    continue
                roots.append((session, root))
        for session, root in roots:
            for path in sorted(root.glob('*/j*/record.json')) if root.is_dir() else ():
                try:
                    record = json.loads(ProjectStore._read(path, 16 << 20))
                    owner = record['session']
                    if session is not None and owner != session:
                        continue
                    if self.folder(record) != path.parent:
                        continue
                    yield record
                except (OSError, ValueError, KeyError):
                    continue
