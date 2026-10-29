"""Host-owned routing of retained Project journals, without changing their identities.

The host keeps writer leases until it shuts down. A saved locator never grants
access; the door still authorizes actors separately. Missing portable locations
refuse rather than recreating empty research under the old sessions directory.
"""
from contextlib import ExitStack
from pathlib import Path
import threading

from .day import sid as sids
from .day.ownership import namespace, retained_owner
from .project_repository import create, session_root, verify_owner
from .project_roots import ProjectLocationError, ProjectRegistry, writer


class ProjectLocations:
    def __init__(self, legacy: Path, registry: ProjectRegistry, parent: Path):
        self.legacy, self.registry, self.parent = Path(legacy), registry, Path(parent)
        self._lock = threading.RLock()
        self._leases = {}
        self._held = ExitStack()
        self.closed = False

    def _check(self):
        if self.closed:
            raise ProjectLocationError('This Hooke host has closed its Project locations')

    def handles(self):
        """Include offline handles so a new Project cannot reuse one accidentally."""
        self._check()
        result = {}
        for origin, locator in self.registry.locations().items():
            sid = locator['session']
            if sid in result and result[sid] != origin:
                raise ProjectLocationError('Two Project origins use the same host handle; open the copy separately before continuing')
            result[sid] = origin
        return result

    def project(self, sid):
        if not sids.is_sid(sid):
            raise ProjectLocationError('Choose a valid Project handle')
        origin = self.handles().get(sid)
        if origin is None:
            return None
        root = self.registry.locate(origin)
        if self._leases.get(origin) == root:
            # Journal.append additionally checks its admitted inode. Do not rescan
            # a growing conversation on every line while this host owns the lease.
            return root
        verify_owner(root)
        old = retained_owner(self.legacy, sid)
        if old is not None and old['origin']['origin'] != origin:
            raise ProjectLocationError('This handle belongs to different legacy and portable Projects; neither was changed')
        # A legacy journal without an origin is not evidence of the same Project.
        if old is None and (self.legacy / sid / sids.JOURNAL).exists():
            raise ProjectLocationError('A legacy Project with unverified identity already uses this handle')
        return root

    def root_for(self, sid):
        root = self.project(sid)
        return session_root(root) if root is not None else self.legacy

    def writable(self, sid):
        """Capture one host lease; later calls verify the same location and journal owner."""
        with self._lock:
            self._check()
            root = self.project(sid)
            if root is None:
                return
            prior = self._leases.get(root.origin)
            if prior is not None and prior != root:
                raise ProjectLocationError('The active Project location changed; finish its work and reopen it')
            if prior is None:
                lease = self._held.enter_context(writer(root))
                self._leases[root.origin] = lease
                verify_owner(root)

    def create(self, name, *, date, preferences, actor, clock, local_only=True, started_from=None, guide=None):
        """Allocate a compatible handle over legacy and portable Projects together."""
        with self._lock, namespace(self.legacy):
            self._check()
            candidate = sids.next_sid(self.legacy, date)
            day, number = sids.parse(candidate)
            used = self.handles()
            while candidate in used:
                number += 1
                candidate = day + '.' + str(number)
            root = create(self.parent, name or 'Untitled project', session=candidate,
                          host_preferences=preferences, actor=actor, clock=clock, local_only=local_only,
                          started_from=started_from, guide=guide)
            self.registry.register(root)
            self.writable(candidate)
            return root

    def close(self):
        with self._lock:
            if not self.closed:
                self.closed = True
                self._held.close()
                self._leases.clear()
