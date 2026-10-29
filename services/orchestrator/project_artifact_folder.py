"""Captured Project routing for legacy artifact services during the staged conversion."""
from pathlib import Path
import stat
import threading

from .project_roots import ProjectLocationError


class ArtifactFolder:
    def __init__(self, legacy, name):
        if not isinstance(name, str) or not name or Path(name).name != name or name in ('.', '..'):
            raise ValueError('An artifact service needs one fixed folder name')
        self.legacy, self.name = Path(legacy), name
        self._identities = {}
        self._lock = threading.RLock()

    def root_for(self, day, session, *, write=False):
        locations = getattr(day, 'locations', None)
        if locations is None:
            return self.legacy
        project = locations.project(session)
        if project is None:
            return self.legacy
        with self._lock:
            if write:
                locations.writable(session)
            folder = project.folder('artifacts') / self.name
            key = (project.origin, project.path)
            if write and key not in self._identities:
                folder.mkdir(exist_ok=True)
                from .scientific_projects import ProjectStore
                ProjectStore._sync_directory(folder.parent)
            try:
                info = folder.stat(follow_symlinks=False)
            except FileNotFoundError:
                if key in self._identities:
                    raise ProjectLocationError('Project artifact folder is missing; restore it before continuing') from None
                return folder  # A new Project may not have produced this artifact kind yet.
            if not stat.S_ISDIR(info.st_mode):
                raise ProjectLocationError('Project artifact storage was replaced; restore its original folder')
            identity = (info.st_dev, info.st_ino)
            if self._identities.setdefault(key, identity) != identity:
                raise ProjectLocationError('Project artifact folder changed; reopen its original location')
            return folder
