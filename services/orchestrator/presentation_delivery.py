"""Prepare original native sources and lease their bounded delivery bytes.

Index/Results/research own producer identities. This store owns bounded immutable
byte pins and independent leases only. Call prepare under the original request
Ticket, then bind resolve to the accepted context before presentation admission.
No source key, URL, ordinal or digest is a delivery token.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import io
import json
import math
import re
import threading
import traceback
import weakref
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from uuid import uuid4

from PIL import Image, ImageColor, ImageDraw

from .capture import Capture, Refused
from .presentations import PresentationContext, PresentationError, ResolvedSource, ResourceRef, SourceRef
from .ticket import current
from .workspace_observation import ObservationError, normalize_source

MAX_BYTES = 10 * 1024 * 1024
MAX_PINS = 64
MAX_PENDING = 8
MAX_LIVE_PIXELS = 4_000_000
LIVE_SNAPSHOT_BYTES = 12 * 1024 * 1024  # bounded producer rows plus their transient JSON/detached copy
PREPARE_SECONDS = 10.0
_SNAPSHOT = re.compile(r'snapshot-\d{6}-(?:[0-9a-f]{12}-)?f\d+')  # the index's snapshot ids (resources.Index.picture)


def written_source(session, address, output_id=None):
    """Canonical immutable source shared by explicit research opens and source verification."""
    resource = {'day': address.day, 'id': address.id, 'version': address.version, 'picture': None}
    if output_id is not None:
        from .research.presentation import retained_document
        output = session.output(output_id)
        if output is None or output.get('kind') != 'presentation' or output.get('version', 1) != address.version:
            _fail('source_unavailable', 'the requested presentation version is not retained')
        result = retained_document(session, output)
        resource['outputId'] = output_id
    else:
        if not session.result:
            _fail('source_unavailable', 'the researcher has no written result yet')
        result = session.wire()['result']
        if address.version is not None:
            revision = next((r for r in session.finding_revisions if r['version'] == address.version), None)
            if revision is None:
                _fail('source_unavailable', 'the requested findings revision is not retained')
            from .research.findings import reference
            historical = copy.copy(session)
            historical.result = {**revision['result'], 'findingRevision': reference(revision)}
            result = historical.wire()['result']
        resource['version'] = (result.get('findingRevision') or {}).get('version')
    text = json.dumps(result, ensure_ascii=False, sort_keys=True)
    raw = text.encode('utf-8')
    if len(raw) > 128000:
        _fail('source_too_large', 'the written result exceeds the delivery limit')
    digest = hashlib.sha256(raw).hexdigest()
    source = SourceRef.from_wire({
        'key': f'{address.day}/{address.id}/written/{digest}',
        'kind': 'written', 'media': 'written', 'availability': 'retained',
        'identity': {'resource': resource},
        'provenance': {'researcher': address.id, 'contentDigest': digest}, 'digest': digest})
    return source, text


class DeliveryUnavailable(PresentationError):
    """A typed source refusal, not a presentation completion verdict."""


def _fail(code, reason):
    raise DeliveryUnavailable(code, reason)


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _source_json(value):
    """Full typed source identity, with equivalent JSON number spellings equal.

    Browsers serialize integral floats as integers. Preserve boolean types and
    every field/array position; converting only exact integral values does not
    round fractional provenance or accept a changed source.
    """
    def numbers(node):
        if isinstance(node, dict):
            return {key: numbers(child) for key, child in node.items()}
        if isinstance(node, list):
            return [numbers(child) for child in node]
        if type(node) is float and node.is_integer():
            return int(node)
        return node

    return _json(numbers(normalize_source(value)))


def _origin(context):
    if not isinstance(context, PresentationContext):
        _fail('context_mismatch', 'delivery needs the accepted presentation context')
    for value in (context.workspace_id, context.client_id, context.client_epoch,
                  context.request_id, context.owner):
        if not isinstance(value, str) or not value or len(value) > 256:
            _fail('context_mismatch', 'delivery context is incomplete or unbounded')
    if context.connection_epoch is not None and (
            not isinstance(context.connection_epoch, str) or not context.connection_epoch
            or len(context.connection_epoch) > 256):
        _fail('context_mismatch', 'delivery connection epoch is invalid')
    return context.workspace_id, context.client_id, context.client_epoch, context.connection_epoch


@dataclass(frozen=True)
class _Selection:
    address: ResourceRef
    source: SourceRef | None
    kind: str
    basis: str
    media: str
    path: object
    mime: str | None
    output_id: str | None = None
    ordinal: bool = False


@dataclass
class _Read:
    raw: bytes
    digest: str
    size: tuple[int, int] | None
    mime: str
    source: SourceRef | None = None
    sample: dict | None = None
    live_state: str = 'live'


@dataclass(frozen=True)
class _CaptureSelection:
    captured: object  # weak reference; the real worker hold owns bytes
    rect: tuple[int, int, int, int]
    basis: str

    @property
    def capture(self):
        return self.captured()


class _Job:
    def __init__(self, reservation):
        self.reservation = reservation
        self.stopped = threading.Event()
        self.lock = threading.RLock()
        self.discarded = False
        self.capture_hold = None
        self.snapshot = None

    def release_capture(self):
        with self.lock:
            if self.capture_hold is not None:
                self.capture_hold.release()
                self.capture_hold = None
            if self.snapshot is not None:
                self.snapshot.release()
                self.snapshot = None

    def discard(self, future):
        # A concurrent Future becomes done only after the real thread exits, or
        # after a queued call is successfully cancelled before it starts. An
        # asyncio Future being cancelled is not evidence of either condition.
        if not future.done():
            return
        with self.lock:
            if self.discarded or self.reservation is None:
                return
            self.discarded = True
            if not future.cancelled():
                try:
                    future.result().raw = b''
                except BaseException:
                    pass
            self.reservation.release()
            self.reservation = None


@dataclass
class _Pin:
    source: SourceRef
    token: str
    raw: bytes
    binding: object
    size: tuple[int, int] | None
    mime: str
    origin: tuple
    reservation: object
    holders: int = 0
    visible_holders: int = 0
    live: object = None
    day: str | None = None
    generation: int | None = None


class PreparedDeliveryLease:
    """An independently releasable handle; metadata survives its own release."""
    def __init__(self, store, pin, *, visibility=True):
        self._store = store
        self._source, self._token = pin.source, pin.token
        self._binding, self._size, self._mime = pin.binding, pin.size, pin.mime
        self._text = pin.raw.decode('utf-8') if pin.mime == 'text/plain' else None
        self._report = json.loads(pin.raw) if pin.mime == 'application/vnd.micromind.findings+json' else None
        self._visibility = visibility
        self._finalize = weakref.finalize(self, store._release, pin.token, visibility)

    @property
    def source(self):
        return self._source

    @property
    def token(self):
        return self._token

    @property
    def payload_binding(self):
        return self._binding

    def to_wire(self):
        wire = {'sourceRef': self.source.to_wire(), 'sourceToken': self.token,
                'payloadBinding': dict(self.payload_binding),
                'transform': [1, 0, 0, 1, 0, 0], 'mediaType': self._mime}
        if self._size is not None:
            wire['originalSize'] = list(self._size)
        if self._text is not None:
            wire['text'] = self._text
        if self._report is not None:
            wire['report'] = self._report
        return wire

    def retain(self):
        if not self._finalize.alive:
            _fail('delivery_revoked', 'this delivery handle was released')
        return self._store._retain(self.token, visibility=self._visibility)

    def release(self):
        self._finalize()


def _release_snapshot_response(data, reservation, lease):
    data.raw = b''
    try:
        reservation.release()
    finally:
        lease.release()


class _SnapshotResponseLease:
    """An encoded original sample owned until actual ASGI response settlement."""
    def __init__(self, data, reservation, lease, check):
        self.data, self.lease, self.check = data, lease, check
        self.headers = MappingProxyType({
            'X-Micromind-Live-Sample': _json(data.sample),
            'X-Micromind-Live-State': data.live_state,
            'X-Micromind-Image-Size': _json(list(data.size)),
            'X-Micromind-Content-Sha256': data.digest,
            'X-Micromind-Source-Token': lease.token})
        self._finalize = weakref.finalize(self, _release_snapshot_response, data, reservation, lease)

    @property
    def raw(self):
        return self.data.raw

    def to_wire(self):
        return self.lease.to_wire()

    def release(self):
        self._finalize()


class PreparedSources:
    def __init__(self, index, memory, app_run, *, live=None):
        if not isinstance(app_run, str) or not app_run or len(app_run) > 256:
            _fail('context_mismatch', 'delivery needs the owning app run')
        self.index, self.memory, self.app_run = index, memory, app_run
        self.live = live
        self._lock = threading.RLock()
        self._pins = {}
        self._sources = {}
        self._jobs = {}
        self._pending = 0
        # The underlying concurrent Future, rather than its asyncio wrapper,
        # owns cancellation cleanup. The pool starts threads only on submission.
        self._pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='prepared-source')
        self._pool_cleanup = weakref.finalize(self, self._pool.shutdown, wait=False, cancel_futures=True)

    def _request_guard(self, ticket, context, generation, day, stopped):
        if (stopped.is_set() or ticket is None or ticket.closed or ticket.cancelled
                or ticket.admission is None or ticket.admission.request_id != context.request_id
                or ticket.superseded('session')):
            _fail('request_stale', 'the original delivery request no longer applies')
        owner = self.index.day
        # `day`: the session open when it was asked (ADR 0017: midnight changes nothing; another session does).
        if owner is not None and (getattr(owner, 'sid', day) != day or not owner.current(generation)
                                  or not owner.current(getattr(ticket.asked, 'routing_generation', getattr(ticket.asked, 'generation', None)))):
            _fail('request_stale', 'the session changed since delivery was requested')

    def _session(self, rid, day):
        """The researcher `rid` of session `day` (an index address is (session, id): ADR 0017)."""
        from .resources import _sitting

        manager = self.index.research
        session = manager.get(rid) if manager is not None else None
        if session is None or _sitting(session) != day:
            session = next((s for s in self.index._researchers(day) if s.rid == rid), None) if manager else None
        if session is None or _sitting(session) != day:
            _fail('source_unavailable', 'the exact researcher is unavailable')
        return session

    def _selection(self, ref):
        try:
            return self._select(ref)
        except DeliveryUnavailable:
            raise
        except (OSError, ValueError, TypeError, KeyError, RuntimeError):
            _fail('source_unavailable', 'the exact producer metadata could not be read')

    def _select(self, ref):
        supplied = ref if isinstance(ref, SourceRef) else None
        if supplied is not None:
            if supplied.kind not in ('saved-image', 'composite', 'written') or supplied.availability not in ('retained', 'temporary'):
                _fail('source_unavailable', 'delivery needs an exact retained image')
            wire = supplied.to_wire()
            address_wire = wire['identity'].get('resource')
            try:
                address = ResourceRef.from_wire(address_wire)
            except PresentationError:
                _fail('source_unavailable', 'the image has no exact retained resource address')
        elif isinstance(ref, ResourceRef):
            try:
                address = ResourceRef.from_wire(dict(day=ref.day, id=ref.id, version=ref.version, picture=ref.picture))
            except PresentationError:
                _fail('source_unavailable', 'the retained resource address is invalid')
            wire, address_wire = None, None
        else:
            _fail('source_unavailable', 'delivery needs a ResourceRef or SourceRef')
        from .day.sid import is_sid

        if not is_sid(address.day):
            _fail('unsafe_source', 'delivery needs an exact session')
        if address.picture is None:
            session = self._session(address.id, address.day)
            canonical, text = written_source(session, address, (address_wire or {}).get('outputId'))
            if supplied is not None and supplied != canonical:
                _fail('source_identity_mismatch', 'the written result changed since it was selected')
            return _Selection(address, canonical, 'written', text, 'written', None, 'application/vnd.micromind.findings+json')
        if type(address.picture) is not int:
            _fail('unsafe_source', 'delivery needs an exact day and picture ordinal')
        entry = self.index.get(address.id, address.day)
        if entry is None or entry.day != address.day or entry.state in ('discarded', 'failed'):
            _fail('source_unavailable', 'the exact retained entry is unavailable')
        n = address.picture
        if entry.kind == 'research':
            from . import media
            from .research import files
            from .resources import _research_picture_source

            session = self._session(entry.id, entry.day)
            if supplied is not None:
                oid = address_wire.get('outputId')
                if not isinstance(oid, str) or not oid or len(oid) > 256:
                    _fail('source_identity_mismatch', 'an exact research source needs a nonempty bounded output ID')
                output = session.output(oid)
                if output is not None and output.get('id') != oid:
                    _fail('source_identity_mismatch', 'the selected research output ID differs')
            else:
                images = [o for o in session.current_outputs() if o.get('kind') == 'image']
                output = images[n] if 0 <= n < len(images) else None
            if output is None or output.get('kind') != 'image':
                _fail('source_unavailable', 'the exact image output is unavailable')
            basis = _research_picture_source(session, output)
            if address.version is not None and address.version != basis['version']:
                _fail('source_identity_mismatch', 'the selected research output version differs')
            if supplied is not None:
                if (supplied.kind != 'saved-image' or supplied.components or wire['provenance'] != basis
                        or address.version != basis['version']):
                    _fail('source_identity_mismatch', 'the exact research output provenance differs')
            sub, separator, name = basis['path'].partition('/')
            found = files.research_file(Path(session.root), session.day, session.rid, sub, name) if separator else None
            if found is None:
                _fail('unsafe_source', 'the exact research path is outside the file allow-list or missing')
            path, mime = found
            expected = Path(session.root).resolve() / session.day / session.rid / sub / name
            if path != expected:
                _fail('unsafe_source', 'the research file aliases another location')
            if supplied is not None and supplied.media != media.of_output(output):
                _fail('source_identity_mismatch', 'the recorded image classification differs')
            canonical = supplied or SourceRef.from_wire({
                'key': f'{entry.day}/{entry.id}/{output["id"]}/v{basis["version"]}',
                'kind': 'saved-image', 'media': media.of_output(output), 'availability': 'retained',
                'identity': {'resource': {'day': entry.day, 'id': entry.id, 'version': basis['version'],
                                          'picture': n, 'outputId': output['id']}}, 'provenance': basis})
            return _Selection(address, canonical, 'research', _json(basis), canonical.media, path, mime,
                              output['id'], supplied is None)
        if n < 0 or n >= len(entry.pictures) or address.version != entry.version:
            _fail('source_identity_mismatch', 'the selected picture or resource revision differs')
        if ((entry.kind == 'population' and re.fullmatch(r'e\d+', entry.id) and n == 0)
                or (entry.kind == 'clip' and (re.fullmatch(r'k\d+', entry.id) or entry.id.startswith('acq-')))):
            # These readers return the producer's whole original JPEG, never a
            # newly cropped member or pixels from the current camera. Frame
            # ordinals alone are insufficient: bind the selected frame's facts.
            frames = (entry.values or {}).get('frames', []) if isinstance(entry.values, dict) else []
            frame = frames[n] if 0 <= n < len(frames) else None
            if entry.kind == 'clip' and not isinstance(frame, dict):
                _fail('source_unavailable', 'the original clip frame has no producing identity')
            basis = json.loads(_json({'basis': 'captured-still', 'source': entry.source,
                'frame': frame, 'revision': entry.version}))
            canonical = SourceRef.from_wire({'key': f'{entry.day}/{entry.id}/{n}',
                'kind': 'saved-image', 'media': 'capture',
                'availability': 'retained' if entry.state in ('saved', 'retained') else 'temporary',
                'identity': {'resource': {'day': entry.day, 'id': entry.id, 'version': entry.version, 'picture': n}},
                'provenance': basis, **({'digest': frame['sha256']} if frame and frame.get('sha256') else {})})
            if supplied is not None:
                original, current = supplied.to_wire(), canonical.to_wire()
                original.pop('availability', None)
                current.pop('availability', None)
                if original != current:
                    _fail('source_identity_mismatch', 'the original captured source differs from its producer')
                canonical = supplied
            return _Selection(address, canonical, 'capture', _json({'source': basis}), canonical.media,
                              None, 'image/jpeg')
        if entry.kind == 'tracks' and re.fullmatch(r'm\d+', entry.id):
            from .media import LABELLED
            from .motiontracks import tracks_of

            store = tracks_of(self.index.hub)
            item = store.get(entry.id)
            if item is not None and (item.session or item.day) != entry.day:
                item = None  # another session's item with this id: this entry's is read from its own folder
            if item is None:
                retained = self.index.captured_manifest(entry.id, entry.day)
                if retained is None:
                    _fail('source_unavailable', 'the original motion item is unavailable')
                record, folder = retained
                basis = json.loads(_json({'basis': 'captured-still', 'frame': record.get('capture'),
                    'view': record.get('view'), 'span': record.get('span'), 'scale': record.get('scale'),
                    'producer': record.get('producer') or {}, 'transformations': record.get('transformations') or {}}))
                path = folder / 'tracks.jpg'
            else:
                basis = json.loads(_json({'basis': 'captured-still', 'frame': item.capture_id,
                    'view': item.view, 'span': item.span, 'scale': item.scale,
                    'producer': item.producer, 'transformations': item.transformations}))
                path = None
            # The producer already drew this bitmap on its own captured view.
            # Neither live placement nor today's camera/control state is its basis.
            if item is not None and store.root is not None:
                folder = store.root.resolve() / item.day / entry.id  # its own date folder
                path = folder / 'tracks.jpg'
                if folder.resolve() != folder or path.resolve() != path:
                    _fail('unsafe_source', 'the original motion file aliases another location')
                if item.path is not None and Path(item.path).resolve() != folder:
                    _fail('unsafe_source', 'the original motion folder differs from its producer')
            elif item is not None and item.path is not None:
                _fail('unsafe_source', 'the original motion file has no owning store root')
            canonical = None
            if supplied is not None:
                declared = supplied.to_wire()
                declared.pop('digest', None)
                declared.pop('availability', None)
                expected = {'key': f'{entry.day}/{entry.id}/{n}', 'kind': 'saved-image',
                    'media': LABELLED, 'identity': {'resource': {'day': entry.day,
                        'id': entry.id, 'version': entry.version, 'picture': n}}, 'provenance': basis}
                if declared != expected:
                    _fail('source_identity_mismatch', 'the captured motion source differs from its producer')
                canonical = supplied
            # Storage completion is not a new producing identity: the writer can
            # move these same bytes from resident memory to the declared file.
            return _Selection(address, canonical, 'tracks', _json({'source': basis}), LABELLED,
                              path, 'image/jpeg')
        if entry.kind == 'snapshot' and _SNAPSHOT.fullmatch(entry.id) and n == 0:
            # A snapshot (Made today's product; its result copy is not listed there) is its own PNG under
            # snapshots/<day>/: a real frame, with the app's outlines, labels and scale bar when it has overlays
            # (live 2026-10-11: Made today's Open of "Snapshot · 16:22" was refused, only a result's picture being
            # deliverable). Its bytes' digest is bound once read, as a result's is.
            from .media import LABELLED

            root = self.index.snapshots
            if root is None:
                _fail('source_unavailable', 'snapshots are not kept here')
            folder = Path(root).resolve() / (entry.date or entry.day)  # its own date folder
            path = folder / f'{entry.id}.png'
            if path.resolve() != path:
                _fail('unsafe_source', 'the snapshot file aliases another location')
            media = LABELLED if (entry.values or {}).get('overlays') is not False else 'capture'
            basis = json.loads(_json({'basis': 'snapshot', 'frame': (entry.source or {}).get('frame'),
                                      'revision': entry.version}))
            canonical = None
            if supplied is not None:
                declared = supplied.to_wire()
                declared.pop('digest', None)
                declared.pop('availability', None)
                expected = {'key': f'{entry.day}/{entry.id}/{n}', 'kind': 'saved-image', 'media': media,
                            'identity': {'resource': {'day': entry.day, 'id': entry.id,
                                                      'version': entry.version, 'picture': n}},
                            'provenance': basis}
                if declared != expected:
                    _fail('source_identity_mismatch', 'the selected snapshot differs from its file')
                canonical = supplied
            return _Selection(address, canonical, 'snapshot', _json({'source': basis}), media, path, 'image/png')
        if entry.kind in ('job', 'volume') and re.fullmatch(r'[jv]\d+', entry.id) and n == 0:
            # A registration's picture (three slices of the volume in brainreg's downsampled space with the registered
            # atlas's boundaries, drawn by its adapter) or a volume's middle slice: its own PNG in its folder, written
            # once (live 2026-10-13: Open of a done registration was refused, no deliverable original).
            from .media import LABELLED

            regs = getattr(self.index, 'registrations', None)
            record = regs.get(entry.id, entry.day) if regs is not None else None
            job = entry.kind == 'job'
            name = (record or {}).get('picture' if job else 'preview')
            if record is None or not isinstance(name, str) or not name or (job and record.get('state') != 'done'):
                _fail('source_unavailable', 'it has no picture: a registration has one only once it is done')
            from . import job_outputs

            try:
                job_outputs.parts(name)
            except job_outputs.Invalid:
                _fail('unsafe_source', 'the picture aliases another location')
            # Keep the name in the selection identity; Registrations owns all filesystem access through descriptors.
            path = regs.folder(record) / ('out' if job else 'inspect') / name
            media = LABELLED if job else 'capture'
            basis = json.loads(_json({'basis': 'registration' if job else 'volume', 'id': entry.id,
                                      'volume': (record.get('volume') or {}).get('sha256') if job else record.get('sha256'),
                                      'output': ((record.get('outputs') or {}).get('picture' if job else 'preview') or {}).get('sha256'),
                                      'revision': entry.version}))
            expected = {'key': f'{entry.day}/{entry.id}/{n}', 'kind': 'saved-image', 'media': media,
                        'identity': {'resource': {'day': entry.day, 'id': entry.id,
                                                  'version': entry.version, 'picture': n}},
                        'provenance': basis}
            canonical = None
            if job or basis['output'] is not None:
                if not isinstance(basis['output'], str) or not re.fullmatch(r'[0-9a-f]{64}', basis['output']):
                    _fail('source_unavailable', 'the registration picture has no recorded producer digest')
                canonical = SourceRef.from_wire({**expected, 'availability': 'retained', 'digest': basis['output']})
            if supplied is not None:
                declared = supplied.to_wire()
                supplied_digest = declared.pop('digest', None)
                declared.pop('availability', None)
                if declared != expected or (basis['output'] is not None and supplied_digest != basis['output']):
                    _fail('source_identity_mismatch', 'the selected picture differs from its file')
                canonical = supplied
            return _Selection(address, canonical, entry.kind, _json({'source': basis}), media, path, 'image/png')
        basis = entry.picture_source(n)
        if basis is None:
            _fail('source_unavailable', 'the selected picture has no producer facts')
        canonical = SourceRef.from_wire(basis) if all(k in basis for k in (
            'key', 'kind', 'media', 'identity', 'provenance', 'availability')) else None
        if canonical is not None:
            if ResourceRef.from_wire(canonical.to_wire()['identity'].get('resource')) != address:
                _fail('source_identity_mismatch', 'the producer descriptor names another retained picture')
            if supplied is not None and supplied.to_wire() != canonical.to_wire():
                _fail('source_identity_mismatch', 'the selected retained source descriptor differs')
        elif supplied is not None:
            # Legacy records have facts rather than a stored SourceRef. Validate
            # the deterministic projection against those facts, then verify its
            # optional digest against actual bytes in the worker.
            declared = supplied.to_wire()
            declared.pop('digest', None)
            declared.pop('availability', None)
            expected = {'key': f'{entry.day}/{entry.id}/{n}', 'kind': 'saved-image',
                        'media': entry.media or 'unknown',
                        'identity': {'resource': {'day': entry.day, 'id': entry.id,
                                                  'version': entry.version, 'picture': n}},
                        'provenance': basis}
            if declared != expected:
                _fail('source_identity_mismatch', 'the legacy source projection differs from its retained facts')
            canonical = supplied
        if entry.kind != 'result' or not re.fullmatch(r'd\d+', entry.id):
            # Index.picture(cN) recrops a member. That is preparation of a new
            # crop, not delivery of an existing original, and is not this store.
            _fail('source_unavailable', 'this entry has no immutable retained original for delivery')
        record = self.index.results.get(entry.id, entry.day)
        names = (record or {}).get('pictures') or []
        name = names[n] if 0 <= n < len(names) else None
        # Results retains native PNG outputs as well as older JPEG pictures.
        # Accept only this result's canonical ordinal, never an arbitrary path.
        mime = {f'{entry.id}-{n}.jpg': 'image/jpeg', f'{entry.id}-{n}.png': 'image/png'}.get(name) if isinstance(name, str) else None
        if mime is None or self.index.results.root is None:
            _fail('unsafe_source', 'the retained result file address is invalid')
        root = self.index.results.root_for(entry.day).resolve()
        path = root / ((record or {}).get('day') or entry.date or entry.day) / name  # its own date folder
        if path.resolve() != path:
            _fail('unsafe_source', 'the retained original aliases another location')
        facts = {'source': basis, 'version': entry.version, 'file': name,
                 'media': entry.media, 'state': entry.state}
        recorded = (record or {}).get('digests') or []
        if n < len(recorded):  # the picture's own sha256 as it was kept: delivered only while it is those bytes
            facts['digest'] = recorded[n]
        return _Selection(address, canonical, entry.kind, _json(facts), canonical.media if canonical else entry.media or 'unknown',
                          path, mime)

    def _source_guard(self, selection, ref):
        try:
            latest = self._selection(ref)
        except DeliveryUnavailable as exc:
            if exc.code == 'source_identity_mismatch':
                raise
            _fail('source_identity_mismatch', 'the selected producer changed while its original was read')
        if selection.kind == 'capture' and latest.source is not None and selection.source is not None:
            # Saving these same original bytes changes storage availability, not
            # producing identity. Keep the selected descriptor stable in flight.
            from dataclasses import replace
            latest = replace(latest, source=SourceRef.from_wire({**latest.source.to_wire(),
                'availability': selection.source.availability}))
        if latest != selection:
            _fail('source_identity_mismatch', 'the selected producer changed while its original was read')

    def _read(self, selection, ref, job, guard):
        raw, buffer, image = b'', None, None
        try:
            guard()
            if selection.kind == 'written':
                self._source_guard(selection, ref)
                raw = selection.basis.encode('utf-8')
                return _Read(raw, hashlib.sha256(raw).hexdigest(), None, selection.mime)
            try:
                if (selection.kind not in ('job', 'volume') and selection.path is not None
                        and selection.path.stat().st_size > MAX_BYTES):
                    _fail('source_too_large', 'the native image exceeds the bounded delivery size')
            except FileNotFoundError:
                pass  # Results may still own the original bytes in its pending writer.
            if selection.kind == 'research':
                from .resources import read_picture_bytes
                raw = read_picture_bytes(selection.path, MAX_BYTES)
                mime = selection.mime
            else:
                found = self.index.picture(selection.address.id, selection.address.picture,
                                           selection.address.day, max_bytes=MAX_BYTES)
                raw, mime = found if found else (None, None)
                found = None
            guard()
            self._source_guard(selection, ref)
            if not raw:
                _fail('source_unavailable', 'the exact original image is missing or unreadable')
            if len(raw) > MAX_BYTES:
                _fail('source_too_large', 'the native image exceeds the bounded delivery size')
            sha = hashlib.sha256(raw).hexdigest()
            expected = selection.source.to_wire().get('digest') if selection.source else None
            kept = json.loads(selection.basis).get('digest') if selection.kind == 'result' else None
            if (expected and expected != sha) or (kept and kept != sha):
                _fail('source_identity_mismatch', 'the retained original digest differs from its producer')
            job.reservation.grow(len(raw), 'read native image container metadata')
            buffer = io.BytesIO(raw)  # the initial reservation includes this native-byte copy
            with Image.open(buffer) as image:
                width, height = image.size
                measured_mime = Image.MIME.get(image.format)
                if mime != measured_mime or measured_mime not in ('image/jpeg', 'image/png', 'image/webp'):
                    _fail('source_unavailable', 'the actual native image type differs from its recorded type')
                if width <= 0 or height <= 0:
                    _fail('source_unavailable', 'the native image has no usable dimensions')
                if selection.kind == 'tracks':
                    view = json.loads(selection.basis)['source']['view']
                    original = [view.get('width'), view.get('height')]
                    if all(type(v) is int and v > 0 for v in original) and original != [width, height]:
                        _fail('source_identity_mismatch', 'the motion bitmap differs from its original captured geometry')
                size_fact = selection.source.to_wire()['provenance'].get('naturalSize') if selection.source else None
                if size_fact is not None and size_fact != [width, height]:
                    _fail('source_identity_mismatch', 'the native image dimensions differ from its producer')
                job.reservation.grow(width * height * 12, 'validate native prepared image pixels')
                image.load()  # validate the original without re-encoding or drawing it
            guard()
            self._source_guard(selection, ref)
            return _Read(raw, sha, (width, height), measured_mime)
        except DeliveryUnavailable:
            raise
        except Refused as exc:
            exc.__traceback__ = None
            exc.__context__ = None
            exc.__cause__ = None
            _fail('source_too_large', 'the native image does not fit its reserved memory allowance')
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            exc.__traceback__ = None
            exc.__context__ = None
            exc.__cause__ = None
            _fail('source_unavailable', 'the exact native image could not be read or decoded')
        finally:
            # Failed Futures may retain tracebacks. Do not leave pixel buffers in
            # those frames after their reservation is released by actual completion.
            raw = b''
            if image is not None:
                image.close()
            image = None
            if buffer is not None:
                buffer.close()
            buffer = None

    def _capture_selection(self, capture, rect, provenance):
        ticket = current()
        if (not isinstance(capture, Capture) or capture.id <= 0 or ticket is None or ticket.store is None
                or ticket.store.memory is not self.memory or ticket.held(capture.id) is not capture):
            _fail('source_unavailable', 'the original running look has no request-held capture')
        if (type(capture.width) is not int or type(capture.height) is not int
                or capture.width <= 0 or capture.height <= 0):
            _fail('source_unavailable', 'the original captured geometry is unavailable')
        if (not isinstance(rect, (tuple, list)) or len(rect) != 4
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in rect)):
            _fail('invalid_action', 'the original region needs four finite frame coordinates')
        x0, y0, x1, y1 = rect
        if not (0 <= x0 < x1 <= capture.width and 0 <= y0 < y1 <= capture.height):
            _fail('invalid_action', 'the original region is empty or outside its captured frame')
        rect = (math.floor(x0), math.floor(y0), math.ceil(x1), math.ceil(y1))
        if not isinstance(provenance, dict) or set(provenance) != {'sight', 'epoch', 'offset'}:
            _fail('invalid_action', 'captured region provenance needs its original running look')
        sight = provenance['sight']
        if (not isinstance(sight, dict) or set(sight) != {'id', 'at'}
                or not isinstance(sight['id'], str) or not re.fullmatch(r's[1-8]', sight['id'])
                or type(sight['at']) not in (int, float) or not math.isfinite(sight['at']) or sight['at'] <= 0):
            _fail('invalid_action', 'the original running-look identity is unavailable')
        offset = provenance['offset']
        if (type(provenance['epoch']) is not int or provenance['epoch'] != capture.epoch
                or not isinstance(offset, (tuple, list)) or len(offset) != 2
                or any(type(v) not in (int, float) or not math.isfinite(v) for v in offset)
                or list(offset) != [capture.dx, capture.dy]):
            _fail('source_identity_mismatch', 'the original running look differs from its captured geometry')
        try:
            facts = _json({'run': self.app_run, 'frame': capture.id, 'seq': getattr(capture, 'seq', None),
                'boot': getattr(capture, 'boot', None), 'at': capture.wall_at, 'scale': copy.deepcopy(capture.scale),
                'sight': sight, 'epoch': provenance['epoch'], 'offset': list(offset),
                'originalSize': [capture.width, capture.height], 'naturalSize': [rect[2] - rect[0], rect[3] - rect[1]],
                'rect': list(rect), 'cropAffine': [1, 0, 0, 1, -rect[0], -rect[1]],
                'basis': 'captured-still', 'encoding': {'format': 'jpeg', 'quality': 90}, 'padding': 0})
        except (TypeError, ValueError):
            _fail('source_unavailable', 'the original captured facts are not finite primitive data')
        return _CaptureSelection(weakref.ref(capture), rect, facts)

    def _capture_guard(self, selection, ticket):
        captured = selection.capture
        if captured is None or ticket.held(captured.id) is not captured:
            _fail('source_unavailable', 'the original running-look capture is no longer held')

    def _read_capture(self, selection, ref, job, guard):
        capture, buffer, image, cropped, encoded, raw = None, None, None, None, None, b''
        try:
            guard()
            capture = selection.capture
            if capture is None or not capture.jpeg:
                _fail('source_unavailable', 'the original camera JPEG is unavailable')
            if len(capture.jpeg) > MAX_BYTES:
                _fail('source_too_large', 'the original camera JPEG exceeds the native byte bound')
            job.reservation.grow(len(capture.jpeg) + capture.width * capture.height * 12,
                                 'decode and encode the original captured region')
            buffer = io.BytesIO(capture.jpeg)
            with Image.open(buffer) as image:
                if image.size != (capture.width, capture.height):
                    _fail('source_identity_mismatch', 'the original JPEG differs from its frame geometry')
                image.load()
                guard()
                cropped = image.convert('RGB').crop(selection.rect)
                encoded = io.BytesIO()
                cropped.save(encoded, format='JPEG', quality=90)
                raw = encoded.getvalue()
            guard()
            if not raw or len(raw) > MAX_BYTES:
                _fail('source_too_large', 'the prepared region exceeds the native byte bound')
            sha = hashlib.sha256(raw).hexdigest()
            facts = json.loads(selection.basis)
            facts['parentDigest'] = hashlib.sha256(capture.jpeg).hexdigest()
            identity = {key: facts[key] for key in ('run', 'frame', 'seq', 'boot', 'sight', 'rect')}
            key = 'capture/' + hashlib.sha256(_json(identity).encode('utf-8')).hexdigest()
            source = SourceRef.from_wire({'key': key, 'kind': 'saved-image', 'media': 'capture',
                'availability': 'temporary', 'identity': identity, 'provenance': facts, 'digest': sha})
            return _Read(raw, sha, tuple(facts['naturalSize']), 'image/jpeg', source)
        except BaseException as exc:
            pending, seen = [exc], set()
            while pending:
                error = pending.pop()
                if id(error) in seen:
                    continue
                seen.add(id(error))
                # Drop deeper codec locals before the actual Future can settle
                # its reservation; retain original error/chain/traceback facts.
                traceback.clear_frames(error.__traceback__)
                pending.extend(e for e in (error.__cause__, error.__context__) if e is not None)
            if isinstance(exc, DeliveryUnavailable):
                raise  # typed guards (including stale) are also ValueErrors
            if isinstance(exc, Refused):
                _fail('source_too_large', 'the captured region does not fit its reserved memory allowance')
            if isinstance(exc, (OSError, ValueError, Image.DecompressionBombError)):
                _fail('source_unavailable', 'the original captured region could not be decoded or encoded')
            raise
        finally:
            # Future failures must not retain pixel buffers in worker tracebacks.
            capture, raw = None, b''
            for container in (image, cropped, encoded, buffer):
                if container is not None:
                    container.close()
            image = cropped = encoded = buffer = container = None

    async def prepare_capture(self, capture, rect, provenance, context):
        """Trusted request-held Capture to native still; no model input or saved entry."""
        _origin(context)
        try:
            selection = self._capture_selection(capture, rect, provenance)
            return await self._prepare(None, context, captured=selection)
        finally:
            capture = None

    async def prepare(self, ref, context):
        if isinstance(ref, SourceRef) and ref.kind == 'live':
            return self._prepare_live(ref, context)
        return await self._prepare(ref, context)

    def _live_guard(self, pin, origin):
        if (self._pins.get(pin.token) is not pin or pin.origin != origin
                or pin.visible_holders <= 0 or pin.live is None or not pin.live.current()):
            _fail('delivery_revoked', 'the original current-camera visibility owner is no longer active')
        owner = self.index.day
        if self.index.session() != pin.day or (owner is not None and not owner.current(pin.generation)):
            _fail('delivery_revoked', 'the session changed since this live source was admitted')

    def _prepare_live(self, source, context):
        from .brain.registry import ToolFailed

        origin = _origin(context)
        ticket, owner = current(), self.index.day
        generation, day = getattr(owner, 'generation', None), self.index.session()
        stopped = threading.Event()
        guard = lambda: self._request_guard(ticket, context, generation, day, stopped)
        guard()
        if (self.live is None or source.identity.get('run') != self.app_run
                or source.to_wire()['provenance'].get('basis') not in ('current-camera', 'tracked-object')
                or source.identity.get('resource', {}).get('day', source.identity.get('day')) != day):
            _fail('source_unavailable', 'this original live source has no owning current-camera producer')
        claim = None
        try:
            with self._lock:
                for token in self._sources.get(source, ()):
                    pin = self._pins[token]
                    if pin.origin == origin:
                        if pin.visible_holders <= 0:
                            continue  # A settling response owns old bytes, never new visibility.
                        self._live_guard(pin, origin)
                        return self._lease(pin)
                if len(self._pins) >= MAX_PINS or self._pending >= MAX_PENDING:
                    _fail('source_too_large', 'prepared source capacity is full')
                token = uuid4().hex
                claim = self.live.acquire_live(source, token)  # exact producer identity, never raw client authority
                guard()
                pin = _Pin(source, token, b'', MappingProxyType({
                    'servedDigest': None, 'originalDigest': None, 'contentType': 'image/jpeg'}),
                    None, 'image/jpeg', origin, None, live=claim, day=day, generation=generation)
                self._pins[token] = pin
                self._sources.setdefault(source, set()).add(token)
                claim = None  # visibility handles now own this original producer claim
                return self._lease(pin)
        except ToolFailed as exc:
            _fail('source_unavailable', str(exc))
        except Refused:
            _fail('source_too_large', 'the current-camera producer does not fit its memory allowance')
        finally:
            if claim is not None:
                claim.release()

    def _read_live(self, snapshot, job, guard):
        """Rasterize only an independently held original frame and its own tracks."""
        import cv2
        import numpy as np
        from .contract import MEMBER_PALETTE, MEMBER_UNMEASURED

        capture = image = drawing = pixels = encoded = None
        raw = b''
        try:
            guard()
            capture, wire = snapshot.capture, snapshot.wire
            width, height = capture.width, capture.height
            sample = wire.get('sample') or {}
            if (wire.get('basis') != 'camera-pixels' or sample.get('run') != self.app_run
                    or sample.get('frameId') != capture.id or sample.get('boot') != capture.boot
                    or sample.get('seq') != capture.seq or sample.get('epoch') != capture.epoch
                    or sample.get('imageSize') != [width, height]):
                _fail('source_identity_mismatch', 'the live tracks do not belong to their held original camera frame')
            pixels = capture.bgr()  # its independent snapshot Hold covers this exact decode
            if pixels is None or pixels.shape[:2] != (height, width):
                _fail('source_unavailable', 'the held live camera JPEG could not be decoded at its original geometry')
            image = Image.fromarray(cv2.cvtColor(pixels, cv2.COLOR_BGR2RGB))
            drawing = ImageDraw.Draw(image, 'RGBA')
            style = wire.get('style') or {}
            opacity = style.get('opacity', 1.0)
            if type(opacity) not in (int, float) or not math.isfinite(opacity) or not 0 <= opacity <= 1:
                _fail('source_unavailable', 'the original live trajectory opacity is invalid')
            tracks = wire.get('tracks')
            if not isinstance(tracks, list) or len(tracks) > 400:
                _fail('source_too_large', 'live trajectory count exceeds its producer bound')
            start, end = wire.get('from'), wire.get('to')
            if type(start) not in (int, float) or type(end) not in (int, float) or not all(map(math.isfinite, (start, end))):
                _fail('source_unavailable', 'the original live trajectory interval is unknown')
            for track in tracks:
                guard()
                identifier = track.get('id')
                if not isinstance(identifier, str) or len(identifier) > 256:
                    _fail('source_unavailable', 'the live trajectory identity is invalid')
                colour_index = 0
                for char in identifier:
                    colour_index = (colour_index * 31 + ord(char)) & 0xffffffff
                colour = (style.get('value') if style.get('colour') == 'uniform' else MEMBER_UNMEASURED
                          if style.get('colour') == 'column' else MEMBER_PALETTE[colour_index % len(MEMBER_PALETTE)])
                try:
                    rgb = ImageColor.getrgb(colour)
                except (ValueError, TypeError):
                    _fail('source_unavailable', 'the original live trajectory colour is invalid')
                segments = track.get('segments')
                if not isinstance(segments, list) or len(segments) > 60:
                    _fail('source_too_large', 'live trajectory segments exceed their producer bound')
                for segment in segments:
                    points = segment.get('points')
                    if not isinstance(points, list) or len(points) > 60:
                        _fail('source_too_large', 'live trajectory points exceed their producer bound')
                    previous = None
                    for point in points:
                        if (not isinstance(point, list) or len(point) != 3
                                or any(type(v) not in (int, float) or not math.isfinite(v) for v in point)):
                            _fail('source_unavailable', 'the actual live trajectory coordinates are invalid')
                        xy = (point[0], point[1])  # camera pixels; no registered-field offset/correction
                        age = .25 + .75 * min(1, max(0, (point[2] - start) / (end - start))) if end > start else 1
                        if previous is not None:
                            drawing.line((previous, xy), fill=(*rgb[:3], round(255 * opacity * age)), width=2)
                        previous = xy
                    if previous is not None:
                        x, y = previous
                        if segment.get('end') in ('open', 'end'):
                            drawing.ellipse((x - 2, y - 2, x + 2, y + 2), fill=(*rgb[:3], round(255 * opacity)))
                        elif segment.get('end') == 'lost':
                            drawing.line((x - 4, y - 4, x + 4, y + 4), fill=(*rgb[:3], round(255 * opacity)), width=1)
                            drawing.line((x - 4, y + 4, x + 4, y - 4), fill=(*rgb[:3], round(255 * opacity)), width=1)
                        if segment.get('start') == 'reacquired' and points:
                            x, y = points[0][:2]
                            drawing.ellipse((x - 4, y - 4, x + 4, y + 4), outline=(*rgb[:3], round(255 * opacity)), width=1)
            guard()
            rect = wire.get('crop')
            if rect is not None:
                if (not isinstance(rect, list) or len(rect) != 4 or any(type(v) is not int for v in rect)
                        or not 0 <= rect[0] < rect[2] <= width or not 0 <= rect[1] < rect[3] <= height):
                    _fail('source_identity_mismatch', 'the current object crop is outside its camera frame')
                original = image
                image = original.crop(rect)
                original.close()
                width, height = image.size
            pixels = cv2.cvtColor(np.asarray(image), cv2.COLOR_RGB2BGR)
            ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 88])
            if not ok or encoded is None:
                _fail('source_unavailable', 'the original live snapshot could not be encoded')
            if encoded.nbytes > MAX_BYTES:
                _fail('source_too_large', 'the live JPEG exceeds its bounded delivery size')
            raw = encoded.tobytes()
            guard()
            live_state = snapshot.accept() if hasattr(snapshot, 'accept') else wire.get('liveState', 'live')
            return _Read(raw, hashlib.sha256(raw).hexdigest(), (width, height), 'image/jpeg', sample={
                'run': sample['run'], 'boot': sample['boot'], 'seq': sample['seq'],
                'frame': sample['frameId'], 'epoch': sample['epoch']}, live_state=live_state)
        except BaseException as exc:
            # Failed concurrent Futures must not retain original pixels in tracebacks
            # after their real worker Hold has settled.
            pending, seen = [exc], set()
            while pending:
                error = pending.pop()
                if id(error) in seen:
                    continue
                seen.add(id(error))
                traceback.clear_frames(error.__traceback__)
                pending.extend(e for e in (error.__cause__, error.__context__) if e is not None)
            if isinstance(exc, (cv2.error, OSError, ValueError, TypeError)) and not isinstance(exc, DeliveryUnavailable):
                _fail('source_unavailable', 'the original live camera snapshot could not be rendered or encoded')
            raise
        finally:
            capture = snapshot = pixels = drawing = encoded = None
            raw = b''
            if image is not None:
                image.close()
            image = None

    async def snapshot(self, token, context):
        """Acquire before the first await; real CPU settlement owns capture cleanup."""
        from .brain.registry import ToolFailed

        origin = _origin(context)
        lease = self.borrow(token, context)  # HTTP borrow does not own continuing visibility
        snapshot = reservation = job = future = None
        pending = False
        try:
            with self._lock:
                pin = self._pins[token]
                self._live_guard(pin, origin)
                if self._pending >= MAX_PENDING:
                    _fail('source_pending', 'native snapshot workers are occupied')
                reservation = self.memory.reserve('analysis', LIVE_SNAPSHOT_BYTES,
                                                  'detach original native live snapshot metadata')
                snapshot_owner = 'native snapshot ' + token
                snapshot = (pin.live.acquire_snapshot(snapshot_owner) if hasattr(pin.live, 'acquire_snapshot')
                            else self.live.acquire_snapshot(pin.source, snapshot_owner))
                if snapshot is None:
                    _fail('source_pending', 'this original producer has no completed current-camera sample yet')
                width, height = snapshot.capture.width, snapshot.capture.height
                if (type(width) is not int or type(height) is not int or min(width, height) <= 0
                        or width * height > MAX_LIVE_PIXELS):
                    _fail('source_too_large', 'the original live camera geometry exceeds its delivery bound')
                self._pending += 1
                pending = True
            # Covers raster copies and two maximum encoded buffers before allocation.
            reservation.grow(width * height * 12 + 2 * (MAX_BYTES + 1),
                             'encode original native live snapshot')
            job = _Job(reservation)
            job.snapshot = snapshot
            snapshot = None  # only the actual worker job owns this original Hold

            def guard():
                if job.stopped.is_set():
                    _fail('delivery_revoked', 'the original live snapshot reader stopped')
                with self._lock:
                    self._live_guard(pin, origin)

            future = self._pool.submit(self._read_live, job.snapshot, job, guard)
            with self._lock:
                self._jobs[future] = job
            owned_lease = lease

            def finished(done):
                job.release_capture()
                with self._lock:
                    self._jobs.pop(done, None)
                    self._pending -= 1
                if done.cancelled():
                    job.stopped.set()
                if job.stopped.is_set() or (not done.cancelled() and done.exception() is not None):
                    job.discard(done)
                    owned_lease.release()

            future.add_done_callback(finished)
            wrapped = asyncio.wrap_future(future)
            wrapped.add_done_callback(lambda done: None if done.cancelled() else done.exception())
            data = await asyncio.wait_for(asyncio.shield(wrapped), PREPARE_SECONDS)
            guard()
            with job.lock:
                guard()
                reservation.settle(len(data.raw))
                result = _SnapshotResponseLease(data, reservation, lease, guard)
                job.reservation = None
                lease = None  # ASGI now owns the original token and encoded-byte reservation
                return result
        except BaseException as exc:
            if job is not None:
                job.stopped.set()
            if future is not None:
                future.cancel()  # queued work may settle; running CPU still owns its Hold
                job.discard(future)
            else:
                if job is not None:
                    job.release_capture()
                if snapshot is not None:
                    snapshot.release()
                if reservation is not None:
                    reservation.release()
                if pending:
                    with self._lock:
                        self._pending -= 1
            if isinstance(exc, Refused):
                _fail('source_pending', 'the native snapshot does not fit its current memory allowance')
            if isinstance(exc, ToolFailed):
                _fail('delivery_revoked', str(exc))
            if isinstance(exc, TimeoutError):
                _fail('source_pending', 'native live encoding exceeded its wait deadline')
            raise
        finally:
            if lease is not None:
                lease.release()

    async def _prepare(self, ref, context, *, captured=None):
        origin = _origin(context)
        ticket = current()
        owner = self.index.day
        generation, day = getattr(owner, 'generation', None), self.index.session()
        stopped = threading.Event()
        guard = lambda: self._request_guard(ticket, context, generation, day, stopped)
        guard()
        with self._lock:
            if self._pending >= MAX_PENDING or len(self._pins) >= MAX_PINS:
                _fail('source_too_large', 'prepared source capacity is full')
            self._pending += 1
        reservation, job, future = None, None, None
        try:
            # Covers a bounded read plus BytesIO's copy before any binary I/O.
            reservation = self.memory.reserve('analysis', 2 * (MAX_BYTES + 1), 'prepare native image delivery')
            selection = captured if captured is not None else self._selection(ref)
            guard()
            job = _Job(reservation)
            job.stopped = stopped
            if captured is not None:
                self._capture_guard(selection, ticket)
                job.capture_hold = ticket.store.hold(selection.capture, 'analysis', 'bytes', 'prepare native captured region')
            reader = self._read_capture if captured is not None else self._read
            future = self._pool.submit(reader, selection, ref, job, guard)
            with self._lock:
                self._jobs[future] = job

            def finished(done):
                job.release_capture()  # only a real concurrent Future can settle CPU ownership
                with self._lock:
                    self._jobs.pop(done, None)
                    self._pending -= 1
                if done.cancelled():
                    stopped.set()
                if stopped.is_set():
                    job.discard(done)
                elif done.exception() is not None:
                    # An awaiting request will receive the same typed failure.
                    job.discard(done)

            future.add_done_callback(finished)
            wrapped = asyncio.wrap_future(future)

            def received(done):
                # shield detaches its own reader when the awaiting task stops;
                # the real wrapper still receives the eventual worker failure.
                if not done.cancelled():
                    done.exception()

            wrapped.add_done_callback(received)
            data = await asyncio.wait_for(asyncio.shield(wrapped), PREPARE_SECONDS)
            guard()
            if captured is not None:
                self._capture_guard(selection, ticket)
            else:
                self._source_guard(selection, ref)
            source = data.source if captured is not None else selection.source
            if source is None:
                facts = json.loads(selection.basis)['source']
                source = SourceRef.from_wire({
                    'key': f'{selection.address.day}/{selection.address.id}/{selection.address.picture}',
                    'kind': 'saved-image', 'media': selection.media,
                    'availability': ('retained' if selection.path is not None and selection.path.is_file() and
                                     (selection.kind == 'tracks' or
                                      not self.index.results.picture_pending(selection.path.parent.name, selection.path.name, selection.address.day)) else 'temporary'),
                    'identity': {'resource': {'day': selection.address.day, 'id': selection.address.id,
                                              'version': selection.address.version, 'picture': selection.address.picture}},
                    'provenance': facts, 'digest': data.digest})
            with job.lock, self._lock:
                guard()
                for token in self._sources.get(source, ()):
                    pin = self._pins[token]
                    if pin.origin == origin:
                        job.discard(future)
                        return self._lease(pin)
                # The same picture whose file finished writing since an earlier window opened it (temporary, now
                # retained) is still that source: one key keeps one descriptor per origin, since the studio reports
                # one per key (a second descriptor made its report fail with source_mismatch, or left the first
                # window's row describing another source).
                for pin in self._pins.values():
                    if (pin.origin == origin and pin.live is None and pin.source.same_identity(source)
                            and pin.binding['originalDigest'] == data.digest
                            and {pin.source.availability, source.availability} <= {'retained', 'temporary'}):
                        job.discard(future)
                        return self._lease(pin)
                # An unchanged, unversioned producing identity must not acquire
                # replacement pixels merely because its original file was rewritten.
                for pin in self._pins.values():
                    a, b = pin.source.to_wire(), source.to_wire()
                    if (a['identity'] == b['identity'] and a['provenance'] == b['provenance']
                            and pin.binding['originalDigest'] != data.digest):
                        _fail('source_identity_mismatch', 'an already-pinned producer has different original bytes')
                if len(self._pins) >= MAX_PINS:
                    _fail('source_too_large', 'prepared source capacity is full')
                reservation.settle(len(data.raw))
                pin = _Pin(source, uuid4().hex, data.raw,
                           MappingProxyType({'servedDigest': data.digest, 'originalDigest': data.digest}),
                           data.size, data.mime, origin, reservation)
                self._pins[pin.token] = pin
                self._sources.setdefault(source, set()).add(pin.token)
                data.raw = b''  # only the resident pin owns bytes after transfer
                job.reservation = None
                return self._lease(pin)
        except BaseException as exc:
            stopped.set()
            if future is not None:
                job.discard(future)
            else:
                if job is not None:
                    job.release_capture()
                if reservation is not None:
                    reservation.release()
                with self._lock:
                    self._pending -= 1
            if isinstance(exc, Refused):
                _fail('source_too_large', 'the native image does not fit its reserved memory allowance')
            if isinstance(exc, TimeoutError):
                _fail('source_unavailable', 'native image preparation exceeded its wait deadline')
            raise

    def _lease(self, pin, *, visibility=True):
        pin.holders += 1
        if pin.live is not None and visibility:
            pin.visible_holders += 1
        return PreparedDeliveryLease(self, pin, visibility=visibility)

    def _retain(self, token, *, visibility=True):
        with self._lock:
            pin = self._pins.get(token)
            if pin is None:
                _fail('delivery_revoked', 'the prepared delivery is no longer owned')
            if pin.live is not None:
                self._live_guard(pin, pin.origin)
            return self._lease(pin, visibility=visibility)

    def _release(self, token, visibility=True):
        with self._lock:
            pin = self._pins.get(token)
            if pin is None:
                return
            pin.holders -= 1
            if pin.live is not None and visibility:
                pin.visible_holders -= 1
                if pin.visible_holders == 0:
                    pin.live.release()  # response borrows never prolong continuing work
            if pin.holders:
                return
            self._pins.pop(token)
            tokens = self._sources[pin.source]
            tokens.remove(token)
            if not tokens:
                self._sources.pop(pin.source)
            pin.raw = b''
            if pin.reservation is not None:
                pin.reservation.release()

    def resolve(self, ref, *, context=None):
        """Borrow exact prepared bytes only; bind context in a multi-client adapter.

        With no context, a source with more than one prepared origin refuses. It
        never chooses the newest client, newest token or current resource ordinal.
        """
        if not isinstance(ref, SourceRef):
            _fail('source_not_prepared', 'resolution needs the exact prepared SourceRef')
        origin = _origin(context) if context is not None else None
        try:
            projected = _source_json(ref.to_wire())
        except ObservationError:
            _fail('source_not_prepared', 'resolution needs a valid prepared source projection')
        with self._lock:
            pins = []
            # At most MAX_PINS resident originals; no producer lookup or I/O.
            # Preserve exact fields, nonnumber types and child order. JSON numbers
            # compare by their exact value, not Python/browser integral spelling.
            for pin in self._pins.values():
                if origin is not None and pin.origin != origin:
                    continue
                try:
                    candidate = _source_json(pin.source.to_wire())
                except ObservationError:
                    continue  # a pin outside the wire contract grants no projected access
                if candidate == projected:
                    pins.append(pin)
            if not pins:
                _fail('source_not_prepared', 'the exact source has not been prepared for this origin')
            if len(pins) != 1:
                _fail('context_mismatch', 'bind the originating context before resolving this shared source')
            if pins[0].live is not None:
                self._live_guard(pins[0], pins[0].origin)
            lease = self._lease(pins[0])
            return ResolvedSource(lease.source, lease.release)

    def prepared_key(self, key, context) -> bool:
        """Whether any source is prepared under this exact key for this context's origin (metadata only: it grants
        nothing). A report naming a key with nothing prepared behind it shows a window whose grant has ended."""
        try:
            origin = _origin(context)
        except PresentationError:
            return False
        with self._lock:
            return any(pin.origin == origin and pin.source.key == key for pin in self._pins.values())

    def get(self, token, context):
        origin = _origin(context)
        with self._lock:
            pin = self._pins.get(token) if isinstance(token, str) and len(token) == 32 else None
            if pin is None:
                _fail('delivery_revoked', 'the prepared token is unavailable or no longer owned')
            if pin.origin != origin:
                _fail('context_mismatch', 'the token belongs to another accepted client context')
            if pin.live is not None:
                _fail('source_pending', 'live delivery needs an acquired original frame snapshot')
            return pin.raw

    def borrow(self, token, context, *, closing=False) -> PreparedDeliveryLease:
        """Retain an exact origin-owned token for the duration of its response.

        The caller supplies its trusted accepted context. Socket retirement and
        response completion remain the App/HTTP adapter's responsibility.
        Closing needs the original metadata even if its producer has stopped;
        it never renews visibility or authorizes fetching current pixels.
        """
        origin = _origin(context)
        with self._lock:
            pin = self._pins.get(token) if isinstance(token, str) and len(token) == 32 else None
            if pin is None:
                _fail('delivery_revoked', 'the prepared token is unavailable or no longer owned')
            if pin.origin != origin:
                _fail('context_mismatch', 'the token belongs to another accepted client context')
            if pin.live is not None and not closing:
                self._live_guard(pin, origin)
            return self._lease(pin, visibility=False)
