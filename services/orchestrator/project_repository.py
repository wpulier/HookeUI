"""Project-folder adapters over the existing journals and scientific revision store.

This is the storage seam for new Projects, not legacy migration or a complete
collector. Host preferences, connection grants and runtime installations are
supplied from outside the Project. The live App is not routed here yet.
"""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import os
import shutil
import tempfile
import time

from .day.journal import Journal
from .day.ownership import retained_owner
from .day.prefs import Prefs
from .day.sid import is_sid
from .day.store import SessionStore
from .project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot, create_layout, writer
from .scientific_projects import ProjectStore


def session_root(root: ProjectRoot) -> Path:
    path = root.folder('history') / 'sessions'
    if path.is_symlink():
        raise ProjectLocationError('Project session storage was replaced by a link')
    return path


def document_root(root: ProjectRoot) -> Path:
    path = root.folder('artifacts') / 'records'
    if path.is_symlink():
        raise ProjectLocationError('Project artifact storage was replaced by a link')
    return path


def verify_owner(root: ProjectRoot):
    # This reader does not mint an origin or create a host preference/namespace file.
    actual = ProjectRoot.open(root.path)
    if actual != root:
        raise ProjectLocationError('Project location changed; reopen before continuing')
    owner = retained_owner(session_root(root), root.session)
    if owner is None or owner['origin']['origin'] != root.origin:
        raise ProjectLocationError('Project manifest and retained journal identity do not match')
    return owner


def create(parent: Path, name: str, *, session: str, host_preferences: Prefs,
           actor: str = 'user', clock=time.time, local_only=True, started_from=None, guide=None) -> ProjectRoot:
    """Create a fresh journal-owned Project, without changing host selection or grants.

    The host allocates its compatible session handle. A temporary journal mints
    the origin through ordinary SessionStore creation, then its exact bytes are
    retained in the exclusively created folder. No accepted existing Project moves.
    Publication to a host registry is a separate explicit action.
    """
    if not is_sid(session) or not isinstance(name, str) or not 0 < len(name.strip()) <= 80:
        raise ProjectLocationError('A new Project needs a valid session handle and a name of at most 80 characters')
    if not isinstance(actor, str) or not 0 < len(actor) <= 128:
        raise ProjectLocationError('A new Project needs its creator identity')
    parent = Path(parent).expanduser()
    parent.mkdir(parents=True, exist_ok=True)
    # This temporary directory contains only this call's new, unpublished journal.
    with tempfile.TemporaryDirectory(prefix='.project-creation-', dir=parent) as temporary:
        staging = Path(temporary)
        store = SessionStore(staging, clock=clock, session=session, prefs=host_preferences,
            _header={'type': 'session-started', 'session': session, 'name': name.strip(),
                     'created': round(clock(), 3), 'from': started_from, 'by': actor, 'localOnly': local_only, 'guide': guide})
        owner = retained_owner(staging, session)
        if owner is None:
            raise ProjectLocationError('Project journal creation did not retain its identity')
        root = create_layout(parent, name.strip(), owner['origin'])
        target = session_root(root) / session
        target.mkdir(parents=True)
        # A mutable journal must never share an inode with an original or snapshot.
        with store.journal.path.open('rb') as source, (target / 'journal.jsonl').open('xb') as output:
            shutil.copyfileobj(source, output)
            output.flush()
            os.fsync(output.fileno())
        document_root(root).mkdir()
        # Readers can acquire the shared publication locks even on read-only media.
        # Publish every new directory entry before the staging journal is discarded.
        for folder in (session_root(root), document_root(root)):
            with (folder / '.bundle-visibility.lock').open('xb') as lock:
                lock.flush()
                os.fsync(lock.fileno())
        for folder in (target, session_root(root), root.folder('history'),
                       document_root(root), root.folder('artifacts'), root.path, root.path.parent):
            ProjectStore._sync_directory(folder)
        verify_owner(root)
        return root


class ProjectRepository:
    """Read saved research without starting an App, worker or installed runtime."""
    def __init__(self, root: ProjectRoot):
        self.root = root
        verify_owner(root)

    def owner(self):
        return verify_owner(self.root)

    def events(self):
        verify_owner(self.root)
        return Journal(self.root.session, session_root(self.root)).load(complete_only=True)

    def documents(self):
        verify_owner(self.root)
        return ProjectStore(document_root(self.root)).scan(self.root.session)

    def read(self, identity: str, revision=None):
        verify_owner(self.root)
        return ProjectStore(document_root(self.root)).read(identity, self.root.session, revision=revision)

    @contextmanager
    def edit(self, host_preferences: Prefs, *, registry: ProjectRegistry, host_runtime_root: Path, clock=time.time):
        """Keep the filesystem writer lease for the entire admitted host edit scope."""
        def located():
            if registry.locate(self.root.origin) != self.root:
                raise ProjectLocationError('This location is not the host’s writable Project; open read-only or choose its location explicitly')
        located()
        with writer(self.root):
            located()
            verify_owner(self.root)
            session = SessionStore(session_root(self.root), clock=clock, session=self.root.session,
                                   prefs=host_preferences)
            editor = _Editor(self.root, session, ProjectStore(document_root(self.root), runtime_root=host_runtime_root))
            try:
                yield editor
            finally:
                editor.closed = True


class _Editor:
    def __init__(self, root, session, documents):
        self.root, self._session, self._documents = root, session, documents
        self.closed = False

    def _check(self):
        if self.closed:
            raise ProjectLocationError('This Project writer has closed; open a new edit scope')
        verify_owner(self.root)

    def append(self, event):
        self._check()
        # Internal services supply journal events; this is not an arbitrary-event door action.
        return self._session._record(event, strict=True)

    def create_document(self, document, *, identity=None):
        self._check()
        return self._documents.create(self.root.session, document, identity=identity, check=self._check)

    def save_document(self, identity, document, *, expected_revision, transaction=None):
        self._check()
        return self._documents.save(identity, self.root.session, document,
                                    expected_revision=expected_revision, transaction=transaction,
                                    check=self._check)
