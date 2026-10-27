"""Accepted-revision boundary for one native component in one scientific view.

An admitted host owns this object; browser and worker messages never construct it.
The caller authenticates the client, resolves the Project, reserves memory/disk and
runs these filesystem operations off the event loop. A renderer supplies its checked
view description and acknowledges actual presentation before a gesture is admitted.
This boundary does not implement coordinate mapping, rendering or worker confinement.
"""
from __future__ import annotations

import copy
import secrets
import threading
from dataclasses import dataclass

from env_worker import native_edits as wire
from .scientific_projects import Conflict, InvalidProject, ProjectStore, _json

MAX_FRAME_CONTEXT = 16 << 10


@dataclass(frozen=True)
class Frame:
    identity: str
    view: str
    layer: str
    revision: int
    _context: bytes


class ViewEdits:
    def __init__(self, store: ProjectStore, project: str, session: str, revision: int, reference: dict):
        store.array_source(project, session, revision, reference)
        self.store, self.project, self.session, self.revision = store, project, session, revision
        self.reference = copy.deepcopy(reference)
        self._lock = threading.RLock()
        self._frame = None
        self._rendered = False
        self._edit = None
        self._closed = False

    def present(self, *, view: str, layer: str, context: dict) -> Frame:
        """Issue a frame identity only for a successfully rendered, validated view."""
        from .scientific_projects import _identity
        _identity(view, 'view')
        _identity(layer, 'layer')
        if type(context) is not dict:
            raise InvalidProject('A scientific frame needs its checked view context')
        body = _json(context)
        if len(body) > MAX_FRAME_CONTEXT:
            raise InvalidProject('Scientific frame context exceeds its allowance')
        with self._lock:
            self._open()
            if self._edit is not None:
                raise Conflict('Finish or cancel the current gesture before changing its view')
            self._frame = Frame(secrets.token_hex(32), view, layer, self.revision, body)
            self._rendered = False
            return self._frame

    def rendered(self, frame: Frame):
        with self._lock:
            self._current(frame)
            self._rendered = True

    def begin(self, frame: Frame, *, max_written_bytes: int, producer=None):
        return self._begin(frame, max_written_bytes=max_written_bytes, publication=True, producer=producer)

    def snapshot(self, frame: Frame, *, max_written_bytes: int, producer=None):
        return self._begin(frame, max_written_bytes=max_written_bytes, publication=False, producer=producer)

    def _begin(self, frame, *, max_written_bytes, publication, producer):
        with self._lock:
            self._current(frame)
            if not self._rendered:
                raise Conflict('Wait for this scientific view to finish displaying before editing')
            if self._edit is not None:
                raise Conflict('Finish or cancel the current gesture before starting another')
            if publication and self.store.read(self.project, self.session).revision != self.revision:
                raise Conflict('Scientific data changed. Reopen the accepted revision before editing.')
            draft = self.store.array_edit(self.project, self.session, self.revision, self.reference,
                                          max_written_bytes=max_written_bytes)
            self._edit = Edit(self, frame, draft, publication=publication, producer=producer)
            return self._edit

    def invalidate(self):
        """Focus loss, view changes and disconnect revoke pending gesture authority."""
        with self._lock:
            if self._edit is not None:
                self._edit.cancel()
            self._frame, self._rendered = None, False

    def close(self):
        with self._lock:
            self.invalidate()
            self._closed = True

    def _retire_basis(self):
        self._frame, self._rendered = None, False

    def _open(self):
        if self._closed:
            raise InvalidProject('This scientific view is closed; reopen its accepted data')

    def _current(self, frame):
        self._open()
        if not isinstance(frame, Frame) or frame != self._frame:
            raise Conflict('This frame is stale. Wait for the current scientific view before editing.')


@dataclass(frozen=True)
class ResourceRevision:
    identity: str
    layer: str
    revision: int


class ResourceEdits:
    """An admitted resource publication, independent of camera or rendered pixels.

    Only the authenticated host constructs this owner from an accepted input. It
    cannot issue frame receipts or authorize a pointer gesture. Publication still
    uses the same native writer, revision CAS, cancellation and replay boundary.
    """
    def __init__(self, store, project, session, revision, reference, *, layer):
        from .scientific_projects import _identity
        _identity(layer, 'layer')
        store.array_source(project, session, revision, reference)
        self.store, self.project, self.session, self.revision = store, project, session, revision
        self.reference = copy.deepcopy(reference)
        self._lock = threading.RLock()
        self._basis = ResourceRevision(secrets.token_hex(32), layer, revision)
        self._closed = False
        self._edit = None

    def begin(self, *, max_written_bytes, producer=None):
        return self._begin(max_written_bytes=max_written_bytes, producer=producer, publication=True)

    def snapshot(self, *, max_written_bytes, producer=None):
        return self._begin(max_written_bytes=max_written_bytes, producer=producer, publication=False)

    def _begin(self, *, max_written_bytes, producer, publication):
        with self._lock:
            self._current(self._basis)
            if self._edit is not None:
                raise Conflict('Finish or cancel the current scientific save before starting another')
            if publication and self.store.read(self.project, self.session).revision != self.revision:
                raise Conflict('Scientific data changed. Reopen the accepted revision before editing.')
            draft = self.store.array_edit(self.project, self.session, self.revision, self.reference,
                                         max_written_bytes=max_written_bytes)
            self._edit = Edit(self, self._basis, draft, publication=publication, producer=producer)
            return self._edit

    def _current(self, basis):
        if self._closed:
            raise InvalidProject('This scientific save is closed; reopen its accepted data')
        if not isinstance(basis, ResourceRevision) or basis is not self._basis:
            raise Conflict('This resource admission ended. Reopen the accepted revision before saving.')

    def _retire_basis(self):
        self._basis = None

    def close(self):
        with self._lock:
            if self._edit is not None:
                self._edit.cancel()
            self._retire_basis()
            self._closed = True


class Edit:
    def __init__(self, owner, frame, draft, *, publication=True, producer=None):
        from .scientific_provenance import frozen
        self._producer = frozen(producer)
        self._provenance = None
        self._owner, self._basis, self._draft = owner, frame, draft
        self.identity = secrets.token_hex(32)
        self._publication_attempt = None
        self._prepared = None
        self._finished_reference = None
        self._publication = publication
        self._closed = False
        self._receipt = None

    def specification(self):
        with self._owner._lock:
            self._active()
            return {'format': wire.FORMAT, 'token': self.identity, 'shape': list(self._draft.shape),
                    'dtype': self._draft.dtype, 'axes': list(self._draft.axes), 'chunks': list(self._draft.chunks),
                    'reference': copy.deepcopy(self._owner.reference), 'projectRevision': self._basis.revision}

    def admit_message(self, header, payload_size):
        """Use as the framed pipe decoder's admission hook, before reading payload bytes."""
        with self._owner._lock:
            if (type(header) is not dict or header.get('token') != self.identity
                    or type(payload_size) is not int or payload_size < 0):
                raise InvalidProject('This native edit message has no valid grant')
            if header.get('op') == 'commit-native-edit':
                if set(header) != {'op', 'token'} or payload_size:
                    raise InvalidProject('Native edit completion takes no payload or additional authority')
                if self._receipt is None:
                    self._active()
                return
            self._active()
            if header.get('op') in ('keep-native-chunk','keep-native-base-chunk'):
                expected={'op','token','index'} | ({'sha256'} if header['op']=='keep-native-chunk' else set())
                if (set(header) != expected or payload_size
                        or self._finished_reference is not None):
                    raise InvalidProject('A kept chunk names its index and digest and carries no bytes')
                return
            if (set(header) != {'op', 'token', 'index', 'shape', 'dtype'}
                    or header['op'] != 'write-native-chunk' or self._finished_reference is not None):
                raise InvalidProject('This native edit does not accept another chunk')
            shape, size = wire.chunk(header['index'], self._draft.shape, self._draft.dtype,
                                      self._draft.chunks, self._draft.grid)
            if (type(header['shape']) is not list or any(type(n) is not int for n in header['shape'])
                    or header['shape'] != list(shape) or header['dtype'] != self._draft.dtype or payload_size != size):
                raise InvalidProject('Native chunk payload disagrees with its admitted shape or dtype')
            if self._draft.attempted_bytes + size > self._draft.allowance:
                raise InvalidProject('This native write exhausted its admitted byte allowance')

    def answer(self, header, payload=b''):
        import numpy as np
        with self._owner._lock:
            if type(payload) is not bytes:
                raise InvalidProject('A native edit message needs bounded binary bytes')
            self.admit_message(header, len(payload))
            if header['op'] == 'commit-native-edit':
                saved = self.commit()
                reference = next(ref for ref in saved.document['resources']
                                 if ref.get('component') == self._draft.component)
                return {'ok': True, 'kind': wire.FORMAT, 'reference': reference,
                        'projectRevision': saved.revision}, b''
            if header['op'] in ('keep-native-chunk','keep-native-base-chunk'):
                if header['op']=='keep-native-base-chunk':self.keep_base(header['index'])
                else:self.keep(header['index'], header['sha256'])
                return {'ok': True, 'kind': wire.FORMAT, 'index': list(header['index']), 'bytes': 0, 'kept': True}, b''
            values = np.frombuffer(payload, dtype=self._draft.dtype).reshape(header['shape'])
            self.write(header['index'], values)
            return {'ok': True, 'kind': wire.FORMAT, 'index': list(header['index']), 'bytes': len(payload)}, b''

    def base_digests(self):
        """Prefixes of the base's chunk digests, so the worker sends only chunks it changed."""
        with self._owner._lock:
            self._active()
            return self._draft.base_digests()

    def keep(self, index, digest):
        with self._owner._lock:
            self._active()
            if self._finished_reference is not None:
                raise InvalidProject('This edit is already prepared; retry its save or cancel it')
            try:
                self._draft.keep(index, digest)
            except BaseException:
                self.cancel()
                raise

    def keep_base(self,index):
        with self._owner._lock:
            self._active()
            if self._finished_reference is not None:
                raise InvalidProject('This edit is already prepared; retry its save or cancel it')
            try:self._draft.keep_base(index)
            except BaseException:
                self.cancel();raise

    def write(self, index, values):
        with self._owner._lock:
            self._active()
            if self._finished_reference is not None:
                raise InvalidProject('This edit is already prepared; retry its save or cancel it')
            try:
                self._draft.write(index, values)
            except BaseException:
                self.cancel()
                raise

    def prepared(self):
        from .scientific_copies import PreparedArray
        with self._owner._lock:
            if self._prepared is None:
                self._active()
                if self._finished_reference is None:
                    self._finished_reference = self._draft.finish()
                if self._provenance is None:
                    from .scientific_provenance import snapshot
                    self._provenance = snapshot(self._owner.store, self._owner.project, self._owner.session,
                        self._basis.revision, self._owner.reference, self._finished_reference,
                        self._basis, self.identity, self._producer)
                self._prepared = PreparedArray(self._owner.store, self._owner.project,
                    self._owner.session, self._basis.revision, self._owner.reference, self._finished_reference,
                    provenance=self._provenance)
            return self._prepared

    def commit(self):
        with self._owner._lock:
            if self._receipt is not None:
                return self._receipt
            self._active()
            if not self._publication:
                raise InvalidProject('This snapshot can only be saved as a separate document')
            owner = self._owner
            try:
                if self._publication_attempt is None:
                    from .scientific_publication import ArrayPublication
                    saved = owner.store.read(owner.project, owner.session, revision=self._basis.revision)
                    self._publication_attempt = ArrayPublication(owner.store, saved, [self.prepared()], self.identity)
                receipt = self._publication_attempt.publish()
            except Conflict:
                self.cancel()
                raise
            except (OSError, InvalidProject):
                # Publication may have succeeded before the acknowledgement failed.
                # Keep the exact document and transaction for the store's replay check.
                if self._finished_reference is None and self._draft.closed:
                    self.cancel()
                raise
            self._receipt = receipt
            self._closed = True
            owner.revision = receipt.revision
            owner.reference = next(ref for ref in receipt.document['resources']
                                   if ref.get('component') == owner.reference['component'])
            owner._edit = None
            owner._retire_basis()
            return receipt

    @property
    def retryable(self):
        with self._owner._lock:
            return self._receipt is not None or (not self._closed and self._owner._edit is self)

    def cancel(self):
        with self._owner._lock:
            if self._receipt is not None:
                return
            self._draft.close()
            self._closed = True
            if self._owner._edit is self:
                self._owner._edit = None
                self._owner._retire_basis()

    def _active(self):
        if self._closed or self._owner._edit is not self:
            raise InvalidProject('This scientific edit ended; its accepted data remains available')
        self._owner._current(self._basis)
