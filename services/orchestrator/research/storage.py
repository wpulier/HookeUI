"""Retained researcher files follow their Project; host billing remains separate.

This staged adapter preserves previous session records and exact file locations.
It does not collect shared acquisitions or authorize provider execution in portable
Projects: that requires host-local spending and worker-lifetime qualification.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import stat

from ..project_artifact_folder import ArtifactFolder
from ..project_roots import ProjectLocationError
from ..scientific_projects import ProjectStore

MAX_RECORD = 16 << 20


class ResearchStorage:
    def __init__(self, legacy, day):
        self.day = day
        self.storage = ArtifactFolder(legacy, 'research')
        self._folders = {}

    def portable(self, session):
        locations = getattr(self.day, 'locations', None)
        return locations is not None and locations.project(session) is not None

    def root_for(self, session, *, write=False):
        return self.storage.root_for(self.day, session, write=write)

    def _directory(self, path, *, create=False):
        if create:
            path.mkdir(exist_ok=True)
        if path.exists() or path.is_symlink():
            info = path.stat(follow_symlinks=False)
            identity = info.st_dev, info.st_ino
            if not stat.S_ISDIR(info.st_mode) or self._folders.setdefault(path, identity) != identity:
                raise ProjectLocationError('Research storage changed; reopen its original Project')
        elif path in self._folders:
            raise ProjectLocationError('Research storage is missing; restore the retained folder')
        return path

    def directory(self, session, *, write=False):
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', session.day) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,120}', session.rid):
            raise ProjectLocationError('Research needs its original date and identity')
        root = self.root_for(session.sitting, write=write)
        if session.root is not None and Path(session.root) != root:
            raise ProjectLocationError('The researcher’s Project location changed; reopen its saved work')
        if not self.portable(session.sitting):
            return root / session.day / session.rid
        parent = self._directory(root / session.day, create=write)
        return self._directory(parent / session.rid, create=write)

    def roots(self, session=None):
        if session is not None:
            return [(session if self.portable(session) else None, self.root_for(session))]
        roots = [(None, self.storage.legacy)]
        locations = getattr(self.day, 'locations', None)
        if locations is not None:
            roots.extend((sid, self.root_for(sid)) for sid in locations.handles())
        return roots

    def records(self, date, session=None):
        for owner, root in self.roots(session):
            parent = self._directory(root / date) if owner else root / date
            for path in sorted(parent.glob('*/session.json')):
                if owner:
                    self._directory(path.parent)
                    record = self.read(path)
                    if record.get('session') != owner or record.get('rid') != path.parent.name:
                        raise ProjectLocationError('Retained research identity does not match its Project')
                yield path

    @staticmethod
    def check_identities(found, existing=()):
        identities = {}
        for session in [*existing, *found]:
            identity = (session.sitting, session.day, session.root)
            if session.rid in identities and (identities[session.rid][:2] != identity[:2]
                    or (identities[session.rid][2] is not None and identity[2] is not None
                        and identities[session.rid][2] != identity[2])):
                raise ProjectLocationError('Two retained researchers share an ambiguous identity; open their Projects separately')
            identities[session.rid] = identity

    @staticmethod
    def read(path):
        pending = path.parent / '.publication-pending'
        if pending.exists() or pending.is_symlink():
            raise ProjectLocationError('Research publication is incomplete; preserve its earlier revisions for recovery')
        data = json.loads(ProjectStore._read(path, MAX_RECORD))
        if pending.exists() or pending.is_symlink():
            raise ProjectLocationError('Research publication changed while reading; retry after recovery')
        if not isinstance(data, dict):
            raise ProjectLocationError('Retained research metadata is invalid')
        return data

    def save(self, session, data):
        folder = self.directory(session, write=True)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'session.json'
        body = json.dumps(data, indent=2).encode()
        if not self.portable(session.sitting):
            tmp = folder / 'session.json.tmp'
            tmp.write_bytes(body)
            os.replace(tmp, path)
            return
        if len(body) > MAX_RECORD:
            raise ProjectLocationError('Research metadata exceeds its retained record limit')
        pending = folder / '.publication-pending'
        if pending.exists() or pending.is_symlink():
            raise ProjectLocationError('Research publication needs recovery before another save')
        history = self._directory(folder / 'history', create=True)
        if path.exists() or path.is_symlink():
            old = ProjectStore._read(path, MAX_RECORD)
            archived = history / (hashlib.sha256(old).hexdigest() + '.json')
            if archived.exists():
                if ProjectStore._read(archived, MAX_RECORD) != old:
                    raise ProjectLocationError('Earlier research metadata changed')
            else:
                ProjectStore._write(archived, old, immutable=True)
        ProjectStore._write(pending, b'Research publication incomplete.\n', immutable=True)
        ProjectStore._sync_directory(folder)
        ProjectStore._sync_directory(folder.parent)
        self.directory(session, write=True)
        ProjectStore._write(path, body)
        for directory in (history, folder, folder.parent, folder.parent.parent):
            ProjectStore._sync_directory(directory)
        self.directory(session, write=True)
        pending.unlink()  # sole acceptance action; no fallible work after this

    def transcript(self, session, legacy):
        if self.portable(session.sitting):
            return str(self.directory(session, write=True) / 'transcript.jsonl')
        return str(legacy / session.day / f'{session.rid}.jsonl') if legacy is not None else None
