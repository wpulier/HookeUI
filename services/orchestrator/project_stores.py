"""Resolve scientific records by their captured Project, never by the visible stage.

A bound store retains its admitted origin/location. Moving or closing that host
makes it refuse; a later explicit opening creates a new binding. Shared workflow
library and runtime associations stay host-owned. This adapter does not migrate
legacy stores or certify complete Project collection.
"""
from contextlib import contextmanager
import threading

from .project_repository import document_root
from .project_roots import ProjectLocationError
from .scientific_projects import ProjectStore, InvalidProject


class _BoundStore(ProjectStore):
    def __init__(self, locations, project, runtime_root):
        self.locations, self.project = locations, project
        self._runtime = runtime_root
        # Reading a saved result must not register runtimes or acquire a writer.
        super().__init__(document_root(project))
        self._storage_identity = self._identity()

    def _identity(self):
        folder = document_root(self.project)
        try:
            info = folder.stat(follow_symlinks=False)
        except OSError as error:
            raise ProjectLocationError('Project artifact storage is missing; recover it before continuing') from error
        import stat
        if not stat.S_ISDIR(info.st_mode):
            raise ProjectLocationError('Project artifact storage is not a directory')
        return info.st_dev, info.st_ino

    @property
    def root(self):
        if self.locations.project(self.project.session) != self.project:
            raise ProjectLocationError('The Project location changed; reopen it before continuing')
        if self._identity() != self._storage_identity:
            raise ProjectLocationError('Project artifact storage changed; recover it before continuing')
        return self._root

    @root.setter
    def root(self, value):
        self._root = value

    def for_session(self, session, *, write=False):
        if session != self.project.session:
            raise InvalidProject('This captured store belongs to another Project')
        self.root  # validate the retained locator even for reads
        if write:
            self.locations.writable(session)
            self.runtime_root = self._runtime
        return self

    def _ensure_root(self):
        self.for_session(self.project.session, write=True)
        super()._ensure_root()

    @contextmanager
    def _locked(self, folder):
        self.for_session(self.project.session, write=True)
        with super()._locked(folder):
            yield

    def _publish(self, folder, session, *args, **kwargs):
        self.for_session(session, write=True)
        return super()._publish(folder, session, *args, **kwargs)

    def _read_head(self, folder, session):
        self.for_session(session)
        return super()._read_head(folder, session)


class ProjectStores:
    """One host's store resolver; arbitrary paths require an explicit Project binding."""
    def __init__(self, legacy, locations):
        self.legacy, self.locations = legacy, locations
        self.runtime_root = legacy.runtime_root
        self.workflow_library_root = legacy.workflow_library_root
        self._bound = {}
        self._lock = threading.RLock()

    @property
    def root(self):
        raise InvalidProject('Bind scientific storage to its captured Project before using a path')

    def _folder(self, identity):
        raise InvalidProject('Bind scientific storage to its captured Project before locating an artifact')

    def for_session(self, session, *, write=False):
        project = self.locations.project(session)
        if project is None:
            return self.legacy.for_session(session, write=write)
        with self._lock:
            key = (project.origin, project.path)
            if key not in self._bound:
                self._bound[key] = _BoundStore(self.locations, project, self.runtime_root)
            return self._bound[key].for_session(session, write=write)

    def register_runtime(self):
        # Host runtime maintenance can still inspect every known store. It never
        # grants write access or repairs an unavailable Project as a side effect.
        self.legacy.register_runtime()
        with self._lock:
            bound = tuple(self._bound.values())
        for store in bound:
            if store.runtime_root is not None:
                store.for_session(store.project.session, write=True).register_runtime()

    def read(self, identity, session, **options):
        return self.for_session(session).read(identity, session, **options)

    def create(self, session, document, **options):
        return self.for_session(session, write=True).create(session, document, **options)

    def save(self, identity, session, document, **options):
        return self.for_session(session, write=True).save(identity, session, document, **options)

    def scan(self, session):
        return self.for_session(session).scan(session)

    def list(self, session):
        return self.scan(session)[0]

    def scan_sessions(self, sessions):
        requested = set(sessions)
        routed = requested.intersection(self.locations.handles())
        result = self.legacy.scan_sessions(requested - routed)
        for session in routed:
            try:
                result.update(self.for_session(session).scan_sessions([session]))
            except (OSError, ValueError) as error:
                result[session] = ([], [{'id': session, 'reason': str(error)}])
        return result

    def inputs_by_session(self):
        result = self.legacy.inputs_by_session()
        for session in self.locations.handles():
            result.pop(session, None)
            # Unavailable is not zero inputs; callers retain their explicit error.
            counts = self.for_session(session).inputs_by_session()
            if session in counts:
                result[session] = counts[session]
        return result

    def copy_candidate(self, saved, prepared):
        return self.for_session(saved.session, write=True).copy_candidate(saved, prepared)

    def file_draft(self, identity, session, **options):
        return self.for_session(session, write=True).file_draft(identity, session, **options)

    def file_source(self, identity, session, revision, reference, **options):
        return self.for_session(session).file_source(identity, session, revision, reference, **options)

    def array_draft(self, identity, session, **options):
        return self.for_session(session, write=True).array_draft(identity, session, **options)

    def array_source(self, identity, session, revision, reference):
        return self.for_session(session).array_source(identity, session, revision, reference)

    def array_edit(self, identity, session, revision, reference, **options):
        return self.for_session(session, write=True).array_edit(identity, session, revision, reference, **options)
