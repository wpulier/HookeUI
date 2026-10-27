"""Immutable native array components of retained scientific projects.

Only the ProjectStore head accepts work. Chunk writes and complete manifests are
private candidates until a document references them in a successful CAS save.
This storage provider performs bounded uncompressed reads, not arbitrary codecs.
Its internal API requires callers to admit disk/memory and own cancellation; it
is not an installer, import endpoint, array registry or interactive runtime.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import math
import os
import re
import uuid
from contextlib import contextmanager

import numpy as np

from env_worker import native_regions as wire
from .bounded_files import read_at
from .native_regions import Source
from .scientific_projects import DIGEST, InvalidProject, _json
from .semantic_resources import parse_descriptor, parse_reference

FORMAT = 'project-native-array-v1'
MAX_CHUNKS = 65536
MAX_MANIFEST = 8 << 20
MAX_SCIENTIFIC_METADATA = 128 << 10
MAX_CHUNKS_PER_READ = 256
MAX_SOURCE_BYTES_PER_READ = 8 << 20
# Explicit budgets of incremental storage (H01 slice c). A component's logical size is bounded by what
# its chunk index can address; a publication's work is bounded by the chunk bytes it newly writes,
# which are the only bytes it hashes again. Chunks an accepted revision already holds are immutable,
# content-addressed and re-verified by every read; publication checks only that they are present.
MAX_LOGICAL_BYTES = MAX_CHUNKS * wire.MAX_REGION_BYTES
MAX_NEW_BYTES = 1 << 30
SAVE_WORKING_BYTES = 128 << 20  # Includes bounded manifest decoding and Project JSON, not retained pixels.
MAX_NATIVE_COMPONENTS = 64
MAX_COMBINED_METADATA = 1 << 20
COMPONENT = re.compile(r'array\.[A-Za-z0-9][A-Za-z0-9._-]{0,95}')
DIRECTORY = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW


@contextmanager
def _directory(store, project, *, create=False):
    """Anchor every child open to owned directory descriptors, never a worker path."""
    store._folder(project)
    root = os.open(store.root, DIRECTORY)
    folder = arrays = None
    try:
        folder = os.open(project, DIRECTORY, dir_fd=root)
        if create:
            try:
                os.mkdir('arrays', mode=0o700, dir_fd=folder)
                os.fsync(folder)
            except FileExistsError:
                pass
        arrays = os.open('arrays', DIRECTORY, dir_fd=folder)
        yield arrays
    finally:
        for fd in (arrays, folder, root):
            if fd is not None:
                os.close(fd)


def _put(directory, body, suffix):
    digest = hashlib.sha256(body).hexdigest()
    target, temp = digest + suffix, '.write-' + uuid.uuid4().hex
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), 0o400)
        try:
            os.link(temp, target, src_dir_fd=directory, dst_dir_fd=directory, follow_symlinks=False)
        except FileExistsError:
            if read_at(directory, target, len(body)) != body:
                raise InvalidProject('An immutable scientific chunk has changed; restore its original bytes')
        os.fsync(directory)
    finally:
        os.unlink(temp, dir_fd=directory)
    return digest


def _layout(shape, dtype, axes, chunks):
    shape, dtype, axes = wire.layout(shape, dtype, axes)
    if (type(chunks) not in (list, tuple) or len(chunks) != len(shape)
            or any(type(n) is not int or not 0 < n <= max(1,size) for n, size in zip(chunks, shape))):
        raise InvalidProject('Array chunks must fit every declared dimension')
    if math.prod(chunks) * wire.DTYPES[dtype] > wire.MAX_REGION_BYTES:
        raise InvalidProject('Each stored chunk must fit the native region byte allowance')
    grid = tuple((size + chunk - 1) // chunk for size, chunk in zip(shape, chunks))
    if math.prod(max(1,n) for n in grid) > MAX_CHUNKS:
        raise InvalidProject('This array exceeds the bounded component chunk index')
    return shape, dtype, axes, tuple(chunks), grid


def _index(index, grid):
    if (type(index) not in (tuple, list) or len(index) != len(grid)
            or any(type(n) is not int or not 0 <= n < count for n, count in zip(index, grid))):
        raise InvalidProject('Chunk index lies outside the native array')
    return tuple(index)


def _key(index):
    return ','.join(map(str, index))


def _chunk_shape(index, shape, chunks):
    return tuple(min(chunk, size - n * chunk) for n, size, chunk in zip(index, shape, chunks))


def _descriptor(value, shape, dtype, axes):
    if len(_json(value)) > MAX_SCIENTIFIC_METADATA:
        raise InvalidProject('Native scientific metadata exceeds its bound')
    descriptor = parse_descriptor(value).wire()
    if 'reference' in descriptor:
        raise InvalidProject('The array store supplies the component reference after publication')
    observed = {'shape': list(shape), 'dtype': np.dtype(dtype).name, 'axes': list(axes),
                'storage_format': FORMAT, 'storage_version': '1'}
    for name, actual in observed.items():
        if descriptor.get(name) is not None and descriptor[name] != actual:
            raise InvalidProject(f'Scientific {name} disagrees with the retained array')
    return {**descriptor, **observed}


def _transport_descriptor(shape, dtype, axes, descriptor, reference):
    return wire.specification({'format': wire.FORMAT, 'token': 'validation', 'shape': list(shape),
                               'dtype': dtype, 'axes': list(axes),
                               'scientific': {**descriptor, 'reference': reference}})[3]


def _manifest(store, project, reference):
    reference = parse_reference(reference)
    if (reference['id'] != project or type(reference['revision']) is not str
            or not COMPONENT.fullmatch(reference.get('component', ''))):
        raise InvalidProject('Choose an exact native component of this scientific project')
    with _directory(store, project) as folder:
        body = read_at(folder, reference['revision'] + '.json', MAX_MANIFEST)
    if hashlib.sha256(body).hexdigest() != reference['revision']:
        raise InvalidProject('Native array metadata changed; restore its original bytes')
    try:
        result = json.loads(body)
        if (type(result) is not dict or set(result) != {'format', 'session', 'project', 'component', 'shape',
                'dtype', 'axes', 'chunks', 'scientific', 'parts'} or result['format'] != FORMAT
                or result['session'] != reference['session'] or result['project'] != project
                or result['component'] != reference['component']):
            raise ValueError('Native component identity does not match its retained reference')
        shape, dtype, axes, chunks, grid = _layout(result['shape'], result['dtype'], result['axes'], result['chunks'])
        if _descriptor(result['scientific'], shape, dtype, axes) != result['scientific']:
            raise ValueError('Native component metadata is incomplete')
        _transport_descriptor(shape, dtype, axes, result['scientific'], reference)
        parts = result['parts']
        if (type(parts) is not dict or len(parts) != math.prod(grid)
                or set(parts) != {_key(i) for i in itertools.product(*(range(n) for n in grid))}
                or any(type(digest) is not str or not DIGEST.fullmatch(digest) for digest in parts.values())):
            raise ValueError('Native component has an incomplete chunk map')
    except (ValueError, TypeError, KeyError, RecursionError) as exc:
        raise InvalidProject('Native array metadata is invalid') from exc
    return result


def accepted_parts(store, project, session, document):
    """Chunk digests the given accepted document already holds for this project's array components."""
    parts = set()
    if document is None:
        return parts
    for ref in document['resources']:
        if (ref.get('component', '').startswith('array.') and ref.get('id') == project
                and ref.get('session') == session):
            parts.update(_manifest(store, project, ref)['parts'].values())
    return parts


def validate_references(store, project, session, document, *, accepted=frozenset(), check=lambda: None, new_bytes=MAX_NEW_BYTES):
    """Own native references are checked before the existing atomic head update.

    ``accepted`` holds the chunk digests of the revision this one follows. Those chunks were verified
    when first accepted and are immutable; only their presence and size are checked here, and every
    later read verifies their bytes again. Every other chunk is read and hashed now.
    The publication owner may stop between bounded metadata and chunk operations.
    """
    if type(new_bytes) is not int or not 0 <= new_bytes <= MAX_LOGICAL_BYTES:
        raise InvalidProject('Native publication requires a bounded owner byte allowance')
    components, records, total, new, count, metadata = set(), [], 0, 0, 0, 0
    for ref in document['resources']:
        check()
        component = ref.get('component', '')
        if not component.startswith('array.'):
            continue
        if ref['id'] != project or ref['session'] != session:
            raise InvalidProject('Native components must belong to this scientific project and session')
        if component in components:
            raise InvalidProject('Choose one accepted revision for each native component')
        components.add(component)
        if len(components) > MAX_NATIVE_COMPONENTS:
            raise InvalidProject('This scientific document exceeds its native component allowance')
        record = _manifest(store, project, ref)
        metadata += len(_json(record['scientific']))
        total += math.prod(record['shape']) * wire.DTYPES[record['dtype']]
        count += len(record['parts'])
        shape, dtype, _, chunks, _ = _layout(record['shape'], record['dtype'], record['axes'], record['chunks'])
        new += sum(math.prod(_chunk_shape(tuple(map(int, key.split(','))), shape, chunks)) * wire.DTYPES[dtype]
                   for key, digest in record['parts'].items() if digest not in accepted)
        if (total > MAX_LOGICAL_BYTES or new > new_bytes or count > MAX_CHUNKS
                or metadata > MAX_COMBINED_METADATA):
            raise InvalidProject('Native publication exceeds the current bounded save allowance')
        records.append(record)
    if not records:
        return
    with _directory(store, project) as folder:
        verified = set()
        for record in records:
            shape, dtype, _, chunks, grid = _layout(record['shape'], record['dtype'], record['axes'], record['chunks'])
            for index in itertools.product(*(range(n) for n in grid)):
                check()
                digest = record['parts'][_key(index)]
                size = math.prod(_chunk_shape(index, shape, chunks)) * wire.DTYPES[dtype]
                if (digest, size) in verified:
                    continue
                if digest in accepted:
                    # Missing raises OSError, as a missing new chunk does.
                    if os.stat(digest + '.bin', dir_fd=folder, follow_symlinks=False).st_size != size:
                        raise InvalidProject('An accepted native chunk changed size; the previous scientific revision was kept')
                else:
                    body = read_at(folder, digest + '.bin', size)
                    if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                        raise InvalidProject('A native chunk changed; the previous scientific revision was kept')
                verified.add((digest, size))


class ArrayDraft:
    """One owned candidate; partial chunks never become an accepted scientific array.

    Immutable chunks left by interrupted candidates are retained, not erased behind
    another writer. Future garbage collection must honor all accepted revisions.
    No locks or long-lived file descriptors remain after write/finish/close.
    """
    def __init__(self, store, project, session, *, component, shape, dtype, axes, chunks,
                 scientific, max_written_bytes, base=None):
        store.read(project, session)
        if type(component) is not str or not COMPONENT.fullmatch(component):
            raise InvalidProject('Native components use an array. name')
        self.shape, self.dtype, self.axes, self.chunks, self.grid = _layout(shape, dtype, axes, chunks)
        total = math.prod(self.shape) * wire.DTYPES[self.dtype]
        if total > MAX_LOGICAL_BYTES:
            raise InvalidProject('This array exceeds the bounded logical size of a retained component')
        if type(max_written_bytes) is not int or not 0 <= max_written_bytes <= total:
            raise InvalidProject('A native write needs an explicit bounded byte allowance')
        self.store, self.project, self.session, self.component = store, project, session, component
        self.scientific = _descriptor(scientific, self.shape, self.dtype, self.axes)
        _transport_descriptor(self.shape, self.dtype, self.axes, self.scientific,
                              {'session': session, 'id': project, 'component': component, 'revision': '0' * 64})
        self.parts, self.attempted_bytes, self.allowance, self.closed = {}, 0, max_written_bytes, False
        self._finishing = None
        self._base_parts = None
        if base is not None:
            revision, reference = base
            source(store, project, session, revision, reference)
            old = _manifest(store, project, reference)
            for name, value in (('shape', list(self.shape)), ('dtype', self.dtype), ('axes', list(self.axes)),
                                ('chunks', list(self.chunks)), ('component', component)):
                if old[name] != value:
                    raise InvalidProject('Incremental edits must preserve the base array layout and component')
            if old['scientific'] != self.scientific:
                raise InvalidProject('A change of scientific meaning requires an explicit conversion into a new component')
            self.parts = dict(old['parts'])
            self._base_parts = dict(old['parts'])

    def base_digests(self):
        """The base's chunk digests in grid order, 16 bytes each, for a worker to skip unchanged chunks.

        None without a base. A worker's claim that a chunk is unchanged is checked by ``keep`` against
        the full digest; these prefixes only let it avoid sending bytes.
        """
        if self._base_parts is None:
            return None
        return b''.join(bytes.fromhex(self._base_parts[_key(i)])[:16]
                        for i in itertools.product(*(range(n) for n in self.grid)))

    def keep(self, index, digest):
        """An unchanged chunk: the base's own chunk, confirmed by its full digest, with no new bytes."""
        if self.closed or self._finishing is not None:
            raise InvalidProject('This native array draft is closed or already being finalized')
        key = _key(_index(index, self.grid))
        if self._base_parts is None or type(digest) is not str or self._base_parts.get(key) != digest \
                or self.parts.get(key) != digest:
            raise InvalidProject('A kept chunk must be exactly its accepted base chunk')

    def keep_base(self,index):
        """Resolve an untouched chunk from this draft's exact accepted base only."""
        key=_key(_index(index,self.grid))
        if self._base_parts is None:raise InvalidProject('An untouched chunk requires an accepted base')
        self.keep(index,self._base_parts[key])

    def write(self, index, data):
        if self.closed or self._finishing is not None:
            raise InvalidProject('This native array draft is closed or already being finalized')
        index = _index(index, self.grid)
        shape = _chunk_shape(index, self.shape, self.chunks)
        count = math.prod(shape) * wire.DTYPES[self.dtype]
        if self.attempted_bytes + count > self.allowance:
            raise InvalidProject('This native write exhausted its admitted byte allowance')
        if type(data) is not np.ndarray or data.shape != shape or data.dtype.str != self.dtype:
            raise InvalidProject('Chunk values must have the exact native shape and dtype')
        self.attempted_bytes += count
        try:
            with _directory(self.store, self.project, create=True) as folder:
                digest = _put(folder, data.tobytes(order='C'), '.bin')
        except BaseException:
            self.close()
            raise
        self.parts[_key(index)] = digest

    def finish(self):
        if self.closed or len(self.parts) != math.prod(self.grid):
            raise InvalidProject('Native array is closed or still missing chunks')
        record = {'format': FORMAT, 'session': self.session, 'project': self.project, 'component': self.component,
                  'shape': list(self.shape), 'dtype': self.dtype, 'axes': list(self.axes), 'chunks': list(self.chunks),
                  'scientific': self.scientific, 'parts': self.parts}
        try:
            if self._finishing is None:
                self._finishing = _encode_manifest(record)
            with _directory(self.store, self.project, create=True) as folder:
                digest = _put(folder, self._finishing, '.json')
        except OSError:
            raise
        except BaseException:
            self.close()
            raise
        self.closed = True
        return {'session': self.session, 'id': self.project, 'revision': digest, 'component': self.component}

    def close(self):
        self.closed = True


def _encode_manifest(record):
    body = json.dumps(record, sort_keys=True, ensure_ascii=False, allow_nan=False,
                      separators=(',', ':')).encode('utf-8')
    if len(body) > MAX_MANIFEST:
        raise InvalidProject('Native component metadata exceeds its bound')
    return body


def source(store, project, session, revision, reference):
    """Resolve only components accepted in an exact scientific document revision."""
    if type(revision) is not int or revision < 1:
        raise InvalidProject('Select an exact accepted scientific document revision')
    accepted = store.read(project, session, revision=revision)
    reference = parse_reference(reference)
    if reference not in accepted.document['resources'] or reference['session'] != session:
        raise InvalidProject('This component is not accepted in the selected scientific project revision')
    record = _manifest(store, project, reference)
    shape, dtype, axes, chunks, grid = _layout(record['shape'], record['dtype'], record['axes'], record['chunks'])
    descriptor = {**copy.deepcopy(record['scientific']), 'reference': reference}

    def read(slices):
        bounds = [[part.start, part.stop] for part in slices]
        if any(part.step is not None for part in slices):
            raise InvalidProject('Native component reads use contiguous unit-stride regions')
        _, region_shape, size = wire.region(bounds, shape, dtype)
        if size==0:return np.empty(region_shape,dtype=dtype)
        ranges = [range(start // chunk, (stop - 1) // chunk + 1) for (start, stop), chunk in zip(bounds, chunks)]
        if math.prod(map(len, ranges)) > MAX_CHUNKS_PER_READ:
            raise InvalidProject('This region touches too many chunks; request a smaller aligned region')
        indices = list(itertools.product(*ranges))
        decoded = sum(math.prod(_chunk_shape(i, shape, chunks)) * wire.DTYPES[dtype] for i in indices)
        if decoded > MAX_SOURCE_BYTES_PER_READ:
            raise InvalidProject('This region exceeds its source-read budget; request a smaller aligned region')
        output = np.empty(region_shape, dtype=dtype)
        with _directory(store, project) as folder:
            for index in indices:
                chunk_shape = _chunk_shape(index, shape, chunks)
                count = math.prod(chunk_shape) * wire.DTYPES[dtype]
                digest = record['parts'][_key(index)]
                body = read_at(folder, digest + '.bin', count)
                if len(body) != count or hashlib.sha256(body).hexdigest() != digest:
                    raise InvalidProject('A native chunk changed; restore it before continuing')
                values = np.frombuffer(body, dtype=dtype).reshape(chunk_shape)
                offsets = tuple(i * c for i, c in zip(index, chunks))
                starts = [max(start, offset) for (start, _), offset in zip(bounds, offsets)]
                stops = [min(stop, offset + n) for (_, stop), offset, n in zip(bounds, offsets, chunk_shape)]
                destination = tuple(slice(start - bound[0], stop - bound[0]) for start, stop, bound in zip(starts, stops, bounds))
                origin = tuple(slice(start - offset, stop - offset) for start, stop, offset in zip(starts, stops, offsets))
                output[destination] = values[origin]
        return output

    return Source(shape, dtype, axes, descriptor, read, chunks=chunks)


def edit(store, project, session, revision, reference, *, max_written_bytes):
    source(store, project, session, revision, reference)
    record = _manifest(store, project, reference)
    return ArrayDraft(store, project, session, component=record['component'], shape=record['shape'],
                      dtype=record['dtype'], axes=record['axes'], chunks=record['chunks'],
                      scientific=record['scientific'], max_written_bytes=max_written_bytes,
                      base=(revision, reference))
