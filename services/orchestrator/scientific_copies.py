"""An owned frozen snapshot can become a complete separate scientific document."""
from __future__ import annotations

import hashlib
import itertools
import math
import os
import secrets
import shutil
import tempfile
import time

from . import scientific_arrays as arrays, scientific_files as files
from .bounded_files import read_at
from .downloads import SPARE
from .scientific_projects import ProjectStore, InvalidProject, Conflict, DIGEST, PROJECT_ID, _json, _document
from .semantic_resources import parse_reference
from .scientific_retention import publication


MAX_COPY_HISTORY = 32


def _copy_history(value, saved):
    import json
    try:
        value = json.loads(_json(value))
        if type(value) is not dict or type(value.get('format')) is not int:
            raise ValueError()
        if value['format'] == 1:
            history = [value]
        elif value['format'] == 2 and set(value) == {'format', 'history'}:
            history = value['history']
        else:
            raise ValueError()
        if type(history) is not list or not 0 < len(history) < MAX_COPY_HISTORY:
            raise ValueError()
        previous, seen = None, set()
        for entry in history:
            if type(entry) is not dict or type(entry.get('format')) is not int:
                raise ValueError()
            if entry['format'] == 1 and set(entry) == {'format', 'source', 'original', 'snapshot', 'target', 'transaction'}:
                changes = [entry]
            elif entry['format'] == 2 and set(entry) == {'format', 'source', 'changes', 'transaction'}:
                changes = entry['changes']
                if (type(changes) is not list or not 1 < len(changes) <= arrays.MAX_NATIVE_COMPONENTS
                        or any(type(row) is not dict or set(row) != {'original', 'snapshot', 'target'} for row in changes)):
                    raise ValueError()
            else:
                raise ValueError()
            source = entry['source']
            if (type(source) is not dict or set(source) != {'session','id','revision','digest'}
                    or type(source['id']) is not str or not PROJECT_ID.fullmatch(source['id'])
                    or type(source['revision']) is not int or source['revision'] < 1
                    or type(source['digest']) is not str or not DIGEST.fullmatch(source['digest'])
                    or source['session'] != saved.session
                    or type(entry['transaction']) is not str or not DIGEST.fullmatch(entry['transaction'])):
                raise ValueError()
            destination, components = None, set()
            for change in changes:
                original, snapshot, target = (parse_reference(change[key]) for key in ('original','snapshot','target'))
                if (any(ref['session'] != saved.session or type(ref['revision']) is not str
                        or not DIGEST.fullmatch(ref['revision'])
                        or not arrays.COMPONENT.fullmatch(ref.get('component','')) for ref in (original,snapshot,target))
                        or original['id'] != source['id'] or snapshot['id'] != source['id']
                        or not PROJECT_ID.fullmatch(target['id']) or target['id'] == source['id']
                        or original['component'] != snapshot['component'] or target['component'] != original['component']
                        or (previous is not None and source['id'] != previous) or target['id'] in seen
                        or (destination is not None and target['id'] != destination)
                        or original['component'] in components):
                    raise ValueError()
                destination = target['id']; components.add(original['component'])
            seen.add(source['id']); seen.add(destination); previous = destination
        if previous != saved.id:
            raise ValueError()
        return history
    except (ValueError, TypeError, KeyError) as error:
        raise InvalidProject('This document has incompatible or full copy lineage; its provenance was preserved and no copy was published') from error


class PreparedArray:
    def __init__(self, store, project, session, revision, original, reference, *, provenance=None):
        self._provenance = None if provenance is None else _json(provenance)
        store.array_source(project, session, revision, original)
        record = arrays._manifest(store, project, reference)
        if reference['session'] != session or reference['component'] != original['component']:
            raise InvalidProject('A prepared snapshot must retain its originating component')
        base = arrays._manifest(store, project, original)
        if any(record[key] != base[key] for key in ('shape', 'dtype', 'axes', 'chunks', 'scientific')):
            raise InvalidProject('A prepared snapshot cannot change the scientific input contract')
        self.store, self.project, self.session, self.revision = store, project, session, revision
        self._original, self._reference = _json(original), _json(reference)

    @property
    def provenance(self):
        import json
        return None if self._provenance is None else json.loads(self._provenance)

    @property
    def original(self):
        import json
        return json.loads(self._original)

    @property
    def reference(self):
        import json
        return json.loads(self._reference)


class ProjectCopy:
    def __init__(self, store, saved, prepared):
        prepared = prepared if type(prepared) is list else [prepared]
        # Shared admission rejects mixed bases, duplicate components and incomplete
        # provenance before copying any bytes. The single-array format is retained.
        from .scientific_publication import ArrayPublication
        self.identity, self.transaction = 'p' + secrets.token_hex(16), secrets.token_hex(32)
        self.publication = ArrayPublication(store, saved, prepared, self.transaction)
        document = saved.document
        view = document['state'].get('scientificView')
        if (document['kind'] != 'scene' or type(view) is not dict or set(view) not in ({'runtime', 'inputs'}, {'runtime','inputs','layers'}, {'runtime','inputs','files'}, {'runtime','inputs','layers','tool'}, {'runtime','inputs','layers','initializeTool'}, {'runtime','inputs','layers','initializeTool','recipeTool'})
                or type(view['inputs']) is not dict
                or any(ref not in document['resources'] for ref in view['inputs'].values())):
            raise InvalidProject('This scientific document needs a declared copy integration before it can be copied')
        if 'files' in view and (type(view['files']) is not dict
                or any(ref not in document['resources'] for ref in view['files'].values())):
            raise InvalidProject('Reader bindings must name accepted source files before copying')
        if 'tool' in view or 'initializeTool' in view:
            # Use normal retained-scene admission, including codec/layer identity.
            # Copying never executes the integration or replays its callbacks.
            from .scientific_project_review import _resolve
            _resolve(store, saved.id, saved.session, saved.revision)
        self.history = _copy_history(document['state']['hookeCopy'], saved) if 'hookeCopy' in document['state'] else []
        self.store, self.saved, self.prepared = store, saved, prepared
        self.stage = self.body = self.receipt = None
        self.closed = False

    def _check(self, stopping, deadline):
        if stopping():
            raise InvalidProject('Copy stopped before publication; the original scientific work is unchanged')
        if time.monotonic() > deadline:
            raise InvalidProject('Copy exceeded its time allowance; retry the same captured values')

    def _build(self, stopping, deadline):
        document = self.publication.document
        arrays.validate_references(self.store, self.saved.id, self.saved.session, document)
        records = [(ref, arrays._manifest(self.store, self.saved.id, ref)) for ref in document['resources']
                   if ref.get('component', '').startswith('array.')]
        file_records=files.copy_records(self.store,self.saved.id,self.saved.session,document,check=lambda:self._check(stopping,deadline))
        required = sum(math.prod(record['shape']) * arrays.wire.DTYPES[record['dtype']]
                       + len(arrays._encode_manifest(record)) for _, record in records)
        required += sum(record['size']+len(files._encode(record)) for _,record in file_records)
        if shutil.disk_usage(self.store.root).free < required + arrays.SAVE_WORKING_BYTES + SPARE:
            raise InvalidProject('There is not enough free space to copy this scientific document; free space and retry')
        self.stage = tempfile.mkdtemp(prefix='.scientific-copy-', dir=self.store.root)
        from pathlib import Path
        stage = ProjectStore(Path(self.stage))
        folder = stage._folder(self.identity)
        folder.mkdir(mode=0o700)
        mapping = {}
        with arrays._directory(self.store, self.saved.id) as source, arrays._directory(stage, self.identity, create=True) as target:
            for ref, record in records:
                self._check(stopping, deadline)
                shape, dtype, _, chunks, grid = arrays._layout(record['shape'], record['dtype'], record['axes'], record['chunks'])
                for index in itertools.product(*(range(n) for n in grid)):
                    self._check(stopping, deadline)
                    digest = record['parts'][arrays._key(index)]
                    size = math.prod(arrays._chunk_shape(index, shape, chunks)) * arrays.wire.DTYPES[dtype]
                    body = read_at(source, digest + '.bin', size)
                    if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                        raise InvalidProject('A source chunk changed; no copy was published')
                    arrays._put(target, body, '.bin')
                record = {**record, 'project': self.identity}
                digest = arrays._put(target, arrays._encode_manifest(record), '.json')
                mapping[_json(ref)] = {**ref, 'id': self.identity, 'revision': digest}
        mapping.update(files.copy_into(self.store,self.saved.id,stage,self.identity,file_records,
                                       lambda:self._check(stopping,deadline)))
        # Records of what was measured follow the copied bytes, never the edit replacing them.
        from . import scientific_families
        scientific_families.remap(document['state'], dict(mapping))
        for prepared in self.prepared:
            mapping[_json(prepared.original)] = mapping[_json(prepared.reference)]
        document['resources'] = [mapping.get(_json(ref), ref) for ref in document['resources']]
        view = document['state']['scientificView']
        view['inputs'] = {name: mapping.get(_json(ref), ref) for name, ref in view['inputs'].items()}
        if 'files' in view:
            view['files']={name:mapping.get(_json(ref),ref) for name,ref in view['files'].items()}
        provenance = document['state'].get('hookeEdit')
        if provenance is not None:
            # Unchanged values copied for other work keep the record of how they were made.
            records = provenance['changes'] if provenance.get('format') == 2 else [provenance]
            for record in records:
                record['output'] = mapping.get(_json(record['output']), record['output'])
        document['title'] = (document['title'][:225] + ' (saved copy)')
        changes = [{'original': row.original, 'snapshot': row.reference,
                    'target': mapping[_json(row.original)]} for row in self.prepared]
        lineage = {'format': 1 if len(changes) == 1 else 2,
            'source': {'session': self.saved.session, 'id': self.saved.id,
                       'revision': self.saved.revision, 'digest': self.saved.digest},
            **(changes[0] if len(changes) == 1 else {'changes': changes}), 'transaction': self.transaction}
        document['state']['hookeCopy'] = ({'format': 2, 'history': [*self.history, lineage]}
                                        if self.history or len(changes) > 1 else lineage)
        body = _document(document)
        self._check(stopping, deadline)
        stage._publish(folder, self.saved.session, body, [], transaction=self.transaction,check=lambda:self._check(stopping,deadline))
        self.body = body

    def publish(self, stopping=lambda: False):
        if self.receipt is not None:
            return self.receipt
        if self.closed:
            raise InvalidProject('This copy attempt is closed')
        deadline = time.monotonic() + 60
        target = self.store._folder(self.identity)
        with self.store._locked(self.store.root):
            if target.exists():
                first = self.store.read(self.identity, self.saved.session, revision=1)
                if self.body is None or first._document != self.body or first.transaction != self.transaction:
                    raise Conflict('This document identity already belongs to different work; nothing was overwritten')
                self.store._sync_directory(self.store.root)
                self.receipt = first
                return first
        self._check(stopping, deadline)
        if self.body is None:
            if self.stage is not None:
                shutil.rmtree(self.stage)
                self.stage = None
            self._build(stopping, deadline)
        with self.store._locked(self.store.root):
            if target.exists() or target.is_symlink():
                raise Conflict('The copy destination changed; nothing was overwritten')
            with publication(self.store.root, self.store.runtime_root):
                self._check(stopping, deadline)
                os.rename(os.path.join(self.stage, self.identity), target)
                self.store._sync_directory(self.store.root)
                self.receipt = self.store.read(self.identity, self.saved.session, revision=1)
        return self.receipt

    def close(self):
        self.closed = True
        if self.stage is not None:
            shutil.rmtree(self.stage)
            self.stage = None
