"""Save the tables, embeddings and artifacts Python or a plugin drafted in a review (H01 slice a3).

The worker lists its drafts (env_worker.scientific_family_drafts); the listing is untrusted and only shapes
what is asked for. On Save, every part arrives as bounded messages in the declared order and must match its
declared size and SHA-256. The parent then rebuilds each family's declaration from the listing, resolves an
object space, region or coordinates only through this review's own granted inputs, and publishes everything
with ``scientific_families.publish``: one Project CAS on the reviewed document, which checks every value,
key and relation again. Nothing is accepted unless all of it is.
"""
from __future__ import annotations

import copy
import hashlib
import re
import secrets
import shutil

import numpy as np

from env_worker.native_regions import DTYPES, MAX_REGION_BYTES
from .downloads import SPARE
from .scientific_arrays import SAVE_WORKING_BYTES
from .scientific_host import CaptureRefused, Failed
from .scientific_projects import Conflict, InvalidProject
from .scientific_provenance import frozen
from .scientific_saves import SaveNotStarted, SaveUnavailable, admission

NAME = re.compile(r'[a-z][a-z0-9_]{0,63}')
SHA = re.compile(r'[0-9a-f]{64}')
MAX_DRAFTS = 16
MAX_BYTES = 256 << 20
FIELDS = {
    'table': {'name', 'family', 'semantic', 'key', 'rows', 'objectSpace', 'columns', 'producer', 'parts'},
    'embeddings': {'name', 'family', 'table', 'key', 'rows', 'dims', 'dtype', 'unit', 'region', 'coordinates',
                   'producer', 'parts'},
    'artifact': {'name', 'family', 'semantic', 'mediaType', 'fileName', 'description', 'compatibility', 'producer',
                 'parts'},
}
PRODUCERS = ('python', 'layer-features')  # Python in the console, or a layer's napari features (where plugins leave them)


def _parts(draft):
    parts = draft['parts']
    expected = ([c['name'] for c in draft['columns']] if draft['family'] == 'table'
                else ['matrix'] if draft['family'] == 'embeddings' else ['content'])
    if (type(parts) is not list or [type(p) is dict and p.get('part') for p in parts] != expected
            or any(set(p) != {'part', 'bytes', 'sha256'} or type(p['bytes']) is not int or not 0 <= p['bytes'] <= MAX_BYTES
                   or type(p['sha256']) is not str or not SHA.fullmatch(p['sha256']) for p in parts)):
        raise ValueError(f'Draft {draft["name"]} lists its parts incorrectly')


def drafts(value):
    """The worker's draft listing, checked for shape only; the family checks run again on Save."""
    if type(value) is not list or len(value) > MAX_DRAFTS:
        raise ValueError('The worker listed an invalid set of drafts')
    names = set()
    for draft in value:
        if (type(draft) is not dict or draft.get('family') not in FIELDS or set(draft) != FIELDS[draft['family']]
                or type(draft['name']) is not str or not NAME.fullmatch(draft['name']) or draft['name'] in names
                or type(draft['producer']) is not dict or draft['producer'].get('kind') not in PRODUCERS
                or set(draft['producer']) != {'kind'}):
            raise ValueError('The worker listed an invalid draft')
        names.add(draft['name'])
        if draft['family'] == 'table' and (type(draft['columns']) is not list or not draft['columns']
                                           or any(type(c) is not dict or type(c.get('name')) is not str
                                                  or c.get('dtype') not in DTYPES for c in draft['columns'])):
            raise ValueError(f'Draft {draft["name"]} lists its columns incorrectly')
        _parts(draft)
    if sum(p['bytes'] for d in value for p in d['parts']) > MAX_BYTES:
        raise ValueError('The worker listed more draft bytes than a session holds')
    return copy.deepcopy(value)


class FamilyStaging:
    """Each part of each chosen draft once, in order, in bounded messages of exactly the declared bytes."""

    def __init__(self, chosen):
        self.expected = []
        for draft in chosen:
            for part in draft['parts']:
                count = max(1, -(-part['bytes'] // MAX_REGION_BYTES))
                for index in range(count):
                    size = min(MAX_REGION_BYTES, part['bytes'] - index * MAX_REGION_BYTES)
                    self.expected.append((draft['name'], part['part'], index, count, size))
        self.names = [d['name'] for d in chosen]
        self.parts = {(d['name'], p['part']): p for d in chosen for p in d['parts']}
        self.bodies = {key: bytearray() for key in self.parts}
        self.received = 0
        self.closed = False

    def _next(self, header):
        if self.closed or self.received >= len(self.expected):
            raise Failed('The worker sent a draft part after its last one')
        name, part, index, count, size = self.expected[self.received]
        if header != {'op': 'write-family-part', 'draft': name, 'part': part, 'index': index, 'count': count}:
            raise Failed('The worker sent a draft part out of order')
        return size

    def admit(self, header, size):
        if size != self._next(header):
            raise Failed('A draft part has a size other than declared')

    def answer(self, header, body):
        if len(body) != self._next(header):
            raise Failed('A draft part has a size other than declared')
        self.bodies[(header['draft'], header['part'])] += body
        self.received += 1
        return {'ok': True}, b''

    def refuse(self, header, payload):
        if (not self.received and not payload and type(header) is dict
                and set(header) == {'ok', 'code', 'error'} and header['ok'] is False
                and header['code'] == 'NATIVE_CAPTURE_NOT_STARTED'
                and type(header['error']) is str and 0 < len(header['error']) <= 2000):
            raise CaptureRefused(header['error'])
        raise Failed('The draft transfer failed after it began or returned an invalid refusal')

    def finish(self, header, payload):
        if payload or header != {'ok': True, 'staged': self.names} or self.received != len(self.expected):
            raise Failed('The worker did not finish sending its drafts')
        for key, part in self.parts.items():
            body = self.bodies[key]
            if len(body) != part['bytes'] or hashlib.sha256(body).hexdigest() != part['sha256']:
                raise Failed(f'Draft {key[0]} part {key[1]} differs from what was declared')

    def cancel(self):
        self.closed = True
        self.bodies.clear()


class FamilySave:
    """Python and plugin drafts published together onto the reviewed revision, or nothing."""
    kind = 'families'

    def __init__(self, store, saved, identity, names, *, inputs=None, producer=None):
        from .scientific_history import CELL
        if (type(identity) is not str or not CELL.fullmatch(identity)
                or store.read(saved.id, saved.session, revision=saved.revision) != saved):
            raise InvalidProject('A draft save needs its exact revision and request')
        if (type(names) is not list or not 0 < len(names) <= MAX_DRAFTS or len(set(names)) != len(names)
                or any(type(n) is not str or not NAME.fullmatch(n) for n in names)):
            raise InvalidProject('Choose the drafts to save, each once')
        # The accepted input each of this session's layers came from (scientific_rebase.Track.bases), else the
        # inputs the saved revision binds: an object space, region or coordinates resolves only to one of these.
        spec = saved.document['state'].get('scientificView')
        bound = spec.get('inputs', {}) if type(spec) is dict else {}
        self.inputs = copy.deepcopy(inputs if inputs is not None else bound)
        self.store, self.saved, self.identity, self.names = store, saved, identity, list(names)
        self.producer = frozen(producer)
        self.transaction = secrets.token_hex(32)
        self.save_workspace = None
        self.families = None
        self.receipt = self.failure = self.edit = self.worker_frame = None
        self.copy_receipt = None
        self.granular = self.onto = None
        self.closed = False

    def _input(self, name, what):
        if name is None:
            return None
        if name not in self.inputs:
            raise ValueError(f'{what} names an input this review was not granted')
        return copy.deepcopy(self.inputs[name])

    def _producer(self, draft):
        return {**self.producer, 'family': draft['family'], 'draft': draft['name'], 'by': draft['producer']['kind']}

    def _build(self, chosen, bodies):
        from . import scientific_artifacts, scientific_embeddings, scientific_tables
        tables, embeddings, artifacts = {}, {}, {}
        for draft in chosen:
            name, family = draft['name'], draft['family']
            body = lambda part: bytes(bodies[(name, part)])
            if family == 'table':
                values = {}
                for column in draft['columns']:
                    raw = body(column['name'])
                    if len(raw) % DTYPES[column['dtype']] or len(raw) // DTYPES[column['dtype']] != draft['rows']:
                        raise ValueError(f'Draft {name} column {column["name"]} has a byte count other than its rows')
                    values[column['name']] = np.frombuffer(raw, dtype=column['dtype']).copy()
                columns = [{k: copy.deepcopy(column[k]) for k in ('name', 'dtype', 'role', 'unit', 'description',
                                                                  'missing', 'vocabulary', 'references')}
                           for column in draft['columns']]
                declaration = scientific_tables.declare(
                    semantic=draft['semantic'], columns=columns, key=draft['key'],
                    object_space=self._input(draft['objectSpace'], f'Table {name} object space'),
                    producer=self._producer(draft))
                tables[name] = (declaration, values)
            elif family == 'embeddings':
                raw = body('matrix')
                if (draft['dtype'] not in DTYPES or type(draft['rows']) is not int or type(draft['dims']) is not int
                        or len(raw) != draft['rows'] * draft['dims'] * DTYPES[draft['dtype']]):
                    raise ValueError(f'Draft {name} has a byte count other than its rows and dimensions')
                matrix = np.frombuffer(raw, dtype=draft['dtype']).reshape(draft['rows'], draft['dims']).copy()
                declaration = scientific_embeddings.declare(
                    table=draft['table'], key=draft['key'], unit=draft['unit'],
                    region=self._input(draft['region'], f'Embedding {name} region'),
                    coordinates=self._input(draft['coordinates'], f'Embedding {name} coordinates'),
                    producer=self._producer(draft))
                embeddings[name] = (declaration, matrix)
            else:
                declaration = scientific_artifacts.declare(
                    semantic=draft['semantic'], media_type=draft['mediaType'], file_name=draft['fileName'],
                    description=draft['description'], compatibility=draft['compatibility'],
                    producer=self._producer(draft))
                artifacts[name] = (declaration, body('content'))
        return tables, embeddings, artifacts

    def _unchanged(self, chosen):
        """Each input the drafts name, with its accepted chunk layout and digests, for the worker to check."""
        import itertools
        from .scientific_arrays import _key, _manifest
        names = []
        for draft in chosen:
            for field in ('objectSpace', 'region', 'coordinates'):
                name = draft.get(field)
                if name is not None and name not in names:
                    self._input(name, f'Draft {draft["name"]} {field}')
                    names.append(name)
        unchanged, base = [], []
        for name in names:
            reference = self.inputs[name]
            manifest = _manifest(self.store, reference['id'], reference)
            grid = [-(-n // c) for n, c in zip(manifest['shape'], manifest['chunks'])]
            unchanged.append({'input': name, 'chunks': list(manifest['chunks'])})
            base.append(b''.join(bytes.fromhex(manifest['parts'][_key(i)])[:16]
                                 for i in itertools.product(*(range(n) for n in grid))))
        return unchanged, base

    def capture(self, worker):
        self.save_workspace = getattr(worker, 'save_workspace', None)
        if self.families is not None:
            raise InvalidProject('Retry the captured drafts without capturing them again')
        with admission(self.save_workspace):
            staging = None
            try:
                listed = {d['name']: d for d in worker.family_drafts()}
                missing = [n for n in self.names if n not in listed]
                if missing:
                    raise ValueError(f'These drafts are not in this session: {", ".join(missing)}')
                chosen = [listed[n] for n in self.names]
                count = sum(p['bytes'] for d in chosen for p in d['parts'])
                if shutil.disk_usage(self.store.root).free < 2 * count + SAVE_WORKING_BYTES + SPARE:
                    raise ValueError('Free disk space before saving these drafts')
                unchanged, base = self._unchanged(chosen)
                staging = FamilyStaging(chosen)
                worker.stage_families(self.names, staging, unchanged=unchanged, base=base)
                self.families = self._build(chosen, staging.bodies)
            except (CaptureRefused, ValueError, InvalidProject) as error:
                self.close()
                raise SaveNotStarted(f'No drafts were saved. Private work remains available. {error}') from error
            except BaseException:
                self.close()
                raise
            finally:
                if staging is not None:
                    staging.cancel()

    def publish(self, stopping=lambda: False):
        from .scientific_families import publish
        if self.receipt is not None:
            return self.status()
        if self.closed or self.families is None:
            raise SaveUnavailable('Capture the drafts before saving them')
        if stopping():
            raise SaveUnavailable('Saving the drafts stopped. Accepted work is unchanged')
        tables, embeddings, artifacts = self.families

        def check():
            if stopping():
                raise InvalidProject('Saving the drafts stopped before acceptance')
        with admission(self.save_workspace):
            try:
                self.receipt = publish(self.store, self.saved, tables=tables or None, embeddings=embeddings or None,
                                       artifacts=artifacts or None, transaction=self.transaction, check=check)
            except Conflict as error:
                # Nothing was accepted and the drafts are still in the worker: saving again places them on the newer one.
                self.close()
                raise SaveNotStarted(f'A newer revision was accepted while saving; nothing was saved. '
                                     f'Save the drafts again to add them to it. {error}') from error
            except InvalidProject as error:
                # publish is one Project transaction: refused (a name already saved, a value or relation refused),
                # nothing was accepted, so this save never started and the session stays usable.
                self.close()
                raise SaveNotStarted(f'No drafts were saved. Private work remains available. {error}') from error
        return self.status()

    def status(self):
        return {'request': self.identity, 'kind': self.kind, 'state': 'saved' if self.receipt else 'staged',
                'drafts': list(self.names), 'resource': None if self.receipt is None else
                {'day': self.receipt.session, 'id': self.receipt.id, 'version': self.receipt.revision, 'picture': None}}

    def copy(self, worker, frame, stopping):
        raise InvalidProject('Drafts are saved onto their reviewed document; reopen its newest revision to save them again')

    def close(self):
        self.closed = True
        self.families = None
        return True

    def release(self):
        return self.close()
