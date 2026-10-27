"""One admission of an operation's typed inputs, for every actor (H01 slice d).

A capability whose contract declares several data inputs, or one of a type other than the live view's picture, gets
each port bound at Run to an exact retained resource:

- ``{"kind": "array", "session", "id", "revision", "component"}``: a native array component of one accepted
  scientific document revision (including an earlier operation's or Python's saved output), read exactly as stored;
- ``{"kind": "result", "session", "id", "revision", "component": "picture-0"}``: an earlier capability run's retained
  RGB picture (retained_inputs), for an RGB port only.

``bind`` checks every port before anything runs and refuses with each port's reason and what to do next: an unknown or
missing port, a resource that changed, a dtype or shape the port's type does not take (never converted: a 16-bit or
grey input is not expanded to RGB), a semantic the port does not take, and the member's own scientific contract
(semantic_resources.validate, with peer ports for ``same_as``). The arrays then cross to the adapter unchanged
(env_runner.run_typed), and the result records each input's reference and content digest.
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field

import numpy as np

# What each typed port takes: its dimensions, the dtypes it accepts as stored, and the meaning it requires when the
# resource says one. Nothing outside this is converted into it.
TYPES = {
    'image.2d.rgb8': {'ndim': 3, 'channels': 3, 'dtypes': ('|u1',), 'semantics': ('intensity',)},
    'image.2d.gray8': {'ndim': 2, 'dtypes': ('|u1',), 'semantics': ('intensity', 'probabilities')},
    'image.2d.gray16': {'ndim': 2, 'dtypes': ('<u2', '>u2'), 'semantics': ('intensity',)},
    'image.3d.zyx.float32': {'ndim': 3, 'dtypes': ('<f4', '>f4'), 'semantics': ('intensity', 'probabilities')},
    'labels.instance.2d': {'ndim': 2, 'dtypes': ('|u1', '<u2', '>u2', '<u4', '>u4', '<i4', '>i4'),
                           'semantics': ('instance-labels',)},
    'masks.2d': {'ndim': 2, 'dtypes': ('|b1', '|u1'), 'semantics': ('class-labels',)},
}
MAX_BYTES = 256 << 20  # every bound input together, held in the parent and the worker
# Each type and stored value kind in a researcher's words, never the wire's.
WORDS = {'image.2d.rgb8': 'an RGB picture', 'image.2d.gray8': 'an 8-bit grey image',
         'image.2d.gray16': 'a 16-bit grey image', 'image.3d.zyx.float32': 'a 3-D volume of 32-bit decimals',
         'labels.instance.2d': '2-D instance labels (one number per object)', 'masks.2d': 'a 2-D mask'}
SEMANTICS = {'intensity': 'intensities', 'probabilities': 'probabilities', 'instance-labels': 'instance labels',
             'class-labels': 'class labels'}


def value_words(dtype) -> str:
    """A stored value kind in plain words: '16-bit whole numbers', not '<u2'."""
    dtype = np.dtype(dtype)
    if dtype.kind == 'b':
        return 'true/false values'
    sign = {'u': 'nonnegative whole numbers', 'i': 'whole numbers', 'f': 'decimals'}.get(dtype.kind)
    return f'{dtype.itemsize * 8}-bit {sign}' if sign else f'values of kind {dtype.name}'


def type_words(kind) -> str:
    return WORDS.get(kind, kind)


class Refused(ValueError):
    """Every port's reason, said together, before anything runs."""

    def __init__(self, rows, next='choose a resource each input takes, or convert it explicitly first'):
        self.rows, self.next = list(rows), next
        super().__init__('; '.join(f"{row['port']}: {row['message']}" for row in self.rows))


@dataclass
class Bound:
    port: str
    type: str
    array: np.ndarray
    descriptor: dict
    reference: dict
    sha256: str

    def record(self) -> dict:
        return {'port': self.port, 'type': self.type, 'reference': dict(self.reference), 'sha256': self.sha256,
                'shape': list(self.array.shape), 'dtype': self.array.dtype.str, 'descriptor': self.descriptor}


@dataclass
class TypedCapture:
    """The runner's input for typed ports: its id names exactly these inputs, so equal inputs share a job."""
    typed: list
    id: int = 0
    source_image: dict = field(default_factory=dict)

    def __post_init__(self):
        identity = hashlib.sha256(repr([row.record() for row in self.typed]).encode()).hexdigest()
        self.id = int(identity[:15], 16)
        self.source_image = {'resources': [row.reference for row in self.typed]}

    @property
    def width(self):
        return int(self.typed[0].array.shape[-1])

    @property
    def height(self):
        return int(self.typed[0].array.shape[-2])


def data_inputs(manifest):
    return [port for port in manifest.inputs if not port.widget]


def typed(manifest) -> bool:
    """Whether a capability takes typed inputs bound at Run rather than the live view's picture (the one definition,
    bundle_contract.typed, which install-time proof uses too)."""
    from gpu_worker.bundle_contract import typed as contract_typed
    return contract_typed(manifest)


def why_not(manifest) -> str:
    """Why typed admission cannot give a capability its inputs ('' when it can)."""
    ports = data_inputs(manifest)
    unknown = [port.type for port in ports if port.type not in TYPES]
    if unknown:
        return f"it takes {', '.join(unknown)}, which no retained resource gives it yet"
    # Points and pictures are shown on the view they were found on; a typed run has no view, so it keeps none of them
    # until a result can keep its input references instead (ticket 47, slice d: typed outputs are open).
    if len(manifest.outputs) != 1 or manifest.outputs[0].type in ('image.2d.rgb8', 'labels.instance.2d', 'masks.2d',
                                                                    'points.2d'):
        return 'a capability with several or native inputs gives one table, text or data result for now'
    return ''


def check_layout(port, shape, dtype, descriptor) -> str:
    """Why a port does not take a resource of this shape, dtype and meaning ('' when it does); reads no values."""
    spec, dtype = TYPES[port.type], np.dtype(dtype)
    wanted = type_words(port.type)
    if len(shape) != spec['ndim'] or ('channels' in spec and shape[-1] != spec['channels']):
        return (f"it is {len(shape)}-D ({' × '.join(str(n) for n in shape)}); this input takes {wanted}, "
                f"{spec['ndim']}-D{' with 3 colour channels' if 'channels' in spec else ''}")
    if dtype.str not in spec['dtypes']:
        takes = sorted({value_words(d) for d in spec['dtypes']})
        return (f"it stores {value_words(dtype)}; this input takes {wanted}, stored as {' or '.join(takes)} "
                "(convert it explicitly first; nothing is converted for you)")
    from gpu_worker.semantic_resources import parse_descriptor
    try:
        descriptor = parse_descriptor(descriptor or {}).wire()
    except ValueError as exc:
        return f'its scientific descriptor is invalid ({exc}); declare its axes and meaning explicitly'
    axes = ['z', 'y', 'x'] if port.type == 'image.3d.zyx.float32' else (['y', 'x', 'c'] if 'channels' in spec else ['y', 'x'])
    if descriptor.get('axes') != axes:
        return f'its axes must be explicitly ordered {axes}; choose matching data or convert it explicitly first'
    if 'shape' in descriptor and descriptor['shape'] != list(shape):
        return 'its declared shape disagrees with the stored array; authorize the current data again'
    try:
        if 'dtype' in descriptor and np.dtype(descriptor['dtype']) != dtype:
            return 'its declared stored value kind disagrees with the array; authorize the current data again'
    except (TypeError, ValueError):
        return 'its declared stored value kind is invalid; authorize the current data again'
    semantic = descriptor.get('semantic')
    if semantic is not None and semantic not in spec['semantics']:
        return (f"it holds {SEMANTICS.get(semantic, semantic)}; this input takes "
                f"{' or '.join(SEMANTICS.get(s, s) for s in spec['semantics'])}")
    return ''


def check_array(port, array, descriptor) -> str:
    """Why a port does not take this array as stored ('' when it does). Nothing is converted."""
    problem = check_layout(port, array.shape, array.dtype, descriptor)
    if problem:
        return problem
    if port.type == 'labels.instance.2d' and array.dtype.kind == 'i' and array.size and array.min() < 0:
        return 'instance labels are nonnegative object numbers (zero is background)'
    return ''


def choices(index, session, manifest, *, limit=64):
    """Per input: the session's retained arrays, each with whether this port takes it and, if not, why (check_layout's
    own words), read from their metadata only. What the Studio's pickers offer; bind checks everything again at Run."""
    store = getattr(index, 'projects', None)
    arrays = []
    if store is not None:
        documents, _problems = store.scan(session)
        for saved in documents[:limit]:
            for ref in saved.document['resources']:
                component = ref.get('component', '')
                if not component.startswith('array.'):
                    continue
                try:
                    source = store.array_source(saved.id, session, saved.revision, ref)
                except (ValueError, OSError):
                    continue
                arrays.append((saved, component, source))
    rows = []
    for port in data_inputs(manifest):
        found = []
        for saved, component, source in arrays:
            descriptor = {k: v for k, v in source.descriptor.items() if k != 'reference'}
            says = check_layout(port, tuple(source.shape), source.dtype, descriptor)
            found.append({'title': f"{saved.document.get('title') or saved.id} · {component[len('array.'):]} "
                                   f"· revision {saved.revision}",
                          'reference': {'kind': 'array', 'session': session, 'id': saved.id, 'revision': saved.revision,
                                        'component': component},
                          'shape': list(source.shape), 'dtype': np.dtype(source.dtype).str,
                          'fits': not says, 'says': says or f'this input takes it as stored ({type_words(port.type)})'})
        rows.append({'port': port.id, 'type': port.type, 'choices': found})
    return rows


def _session(reference, session):
    if type(session) is not str or not session:
        raise ValueError('this run has no session to take its inputs from')
    if reference.get('session') != session:
        raise ValueError("it belongs to another session's data; choose one of this session's results")


def native_source(store, reference, session):
    """An array input's metadata (shape, stored kind, meaning), with no value read: (source, descriptor, reference)."""
    from .scientific_projects import InvalidProject
    if (type(reference) is not dict or set(reference) != {'kind', 'session', 'id', 'revision', 'component'}
            or type(reference['revision']) is not int or reference['revision'] < 1):
        raise ValueError('an array input names its document, session, exact revision and component')
    _session(reference, session)
    if store is None:
        raise ValueError('there is no saved Project data here')
    try:
        saved = store.read(reference['id'], session, revision=reference['revision'])
    except InvalidProject as exc:
        raise ValueError(f'its document revision is not available ({exc})') from exc
    ref = next((r for r in saved.document['resources'] if r.get('component') == reference['component']
                and r.get('component', '').startswith('array.')), None)
    if ref is None:
        raise ValueError(f"revision {reference['revision']} has no array called {reference['component'][len('array.'):]}")
    source = store.array_source(reference['id'], session, reference['revision'], ref)
    descriptor = {k: v for k, v in source.descriptor.items() if k != 'reference'}
    return source, descriptor, {**reference, 'revisionDigest': saved.digest, 'resource': ref}


def _result(hub, reference, session, shape):
    import cv2
    from . import retained_inputs
    if type(reference) is not dict or reference.get('kind') != 'result':
        raise ValueError('a result input names an earlier run’s retained picture')
    _session(reference, session)
    plain = {k: v for k, v in reference.items() if k != 'kind'}
    capture, hold = retained_inputs.resolve(hub, plain, session, expected_shape=shape)
    try:
        bgr = cv2.imdecode(np.frombuffer(capture.native, np.uint8), cv2.IMREAD_UNCHANGED)
        array = np.ascontiguousarray(bgr[..., ::-1])  # the PNG's exact RGB bytes (OpenCV decodes PNG as BGR)
        descriptor = dict(capture.source_image.get('scientific') or {})
    finally:
        hold.release()
    return array, descriptor, dict(reference)


def bind(manifest, references, *, store=None, hub=None, session=None) -> TypedCapture:
    """Every declared data port bound to its exact retained resource of this run's own session, checked, or Refused
    with each port's reason. Every input's metadata is checked, and their total size, before any value is read; the
    values are then read under a job-memory reservation."""
    from . import jobs
    reason = why_not(manifest)
    if reason:
        raise Refused([{'port': '*', 'code': 'unsupported', 'message': reason}], next='choose a capability that takes these inputs, or one with a table, text or data result')
    if type(references) is not dict:
        raise Refused([{'port': '*', 'code': 'missing', 'message': 'choose one resource for each input'}],
                      next='choose one of this session’s saved results for each input')
    resolved, sources, pictures, total = {}, {}, {}, 0
    for port in data_inputs(manifest):
        reference = references.get(port.id)
        if reference is None:
            continue
        try:
            if type(reference) is dict and reference.get('kind') == 'result':
                from . import retained_inputs
                if port.type != 'image.2d.rgb8':
                    raise ValueError(f'an earlier run’s picture is RGB; this input takes {type_words(port.type)}')
                _session(reference, session)
                descriptor = retained_inputs.layout(hub, {k: v for k, v in reference.items() if k != 'kind'}, session)
                shape = tuple(descriptor['shape'])
                problem = check_layout(port, shape, descriptor['dtype'], descriptor)
                if problem:
                    raise ValueError(problem)
                pictures[port.id] = (reference, shape)
                total += math.prod(shape)
            else:
                source, descriptor, ref = native_source(store, reference, session)
                problem = check_layout(port, tuple(source.shape), source.dtype, descriptor)
                if problem:
                    raise ValueError(problem)
                sources[port.id] = (source, descriptor, ref)
                total += math.prod(source.shape) * np.dtype(source.dtype).itemsize
        except (ValueError, OSError) as exc:
            resolved[port.id] = exc
    # Everything that metadata alone can refuse is refused before any value is read.
    early = [{'port': name, 'code': 'unknown', 'message': 'this capability has no input of that name'}
             for name in sorted(set(references) - {port.id for port in data_inputs(manifest)})]
    early += [{'port': port.id, 'code': 'missing', 'message': f'choose a resource for it: it takes {type_words(port.type)}'}
              for port in data_inputs(manifest) if references.get(port.id) is None]
    early += [{'port': name, 'code': 'unavailable', 'message': str(found)}
              for name, found in resolved.items() if isinstance(found, Exception)]
    if total > MAX_BYTES:  # from metadata alone: nothing has been read
        early.append({'port': '*', 'code': 'too-large',
                      'message': f'the inputs are {total >> 20} MiB together; at most {MAX_BYTES >> 20} MiB'})
    if early:
        raise Refused(early, next='choose a resource each input takes, or convert it explicitly first')
    try:
        # Retained arrays, a decoder's temporary BGR pixels and the RGB/hash copy coexist.
        # The runner takes its separate execution reservation after this transient admission hold.
        with jobs.MEMORY.reserve(3 * total / (1 << 20)):
            for name, (reference, shape) in pictures.items():
                try:
                    resolved[name] = _result(hub, reference, session, shape)
                except (ValueError, OSError) as exc:
                    resolved[name] = exc
            for name, (source, descriptor, ref) in sources.items():
                try:
                    resolved[name] = (np.asarray(source.read(tuple(slice(0, n) for n in source.shape))), descriptor, ref)
                except (ValueError, OSError) as exc:
                    resolved[name] = exc
            return admit(manifest, resolved)
    except jobs.MemoryRefused as exc:
        raise Refused([{'port': '*', 'code': 'memory', 'message': str(exc)}], next='wait for other work to finish') from exc


def admit(manifest, resolved, *, extra=()) -> TypedCapture:
    """The one check of typed inputs, whatever resolved them: {port: (array, descriptor, reference) or the reason it
    could not be read}. Each port's dtype, shape, meaning and the member's own scientific contract, or Refused."""
    from gpu_worker.semantic_resources import parse_contract, parse_descriptor, validate
    reason = why_not(manifest)
    if reason:
        raise Refused([{'port': '*', 'code': 'unsupported', 'message': reason}], next='choose a capability that takes these inputs, or one with a table, text or data result')
    rows = [{'port': name, 'code': 'unknown', 'message': 'this capability has no input of that name'} for name in extra]
    bound, total = [], 0
    for port in data_inputs(manifest):
        found = resolved.get(port.id)
        if found is None:
            rows.append({'port': port.id, 'code': 'missing', 'message': f'choose a resource for it: it takes {type_words(port.type)}'})
            continue
        if isinstance(found, Exception):
            rows.append({'port': port.id, 'code': 'unavailable', 'message': str(found)})
            continue
        array, descriptor, ref = found
        problem = check_array(port, array, descriptor)
        if problem:
            rows.append({'port': port.id, 'code': 'incompatible', 'message': problem})
            continue
        total += array.nbytes
        array.setflags(write=False)
        bound.append(Bound(port.id, port.type, array, parse_descriptor(descriptor).wire(), dict(ref),
                           hashlib.sha256(array.dtype.str.encode() + repr(array.shape).encode() + array.tobytes()).hexdigest()))
    if total > MAX_BYTES:
        rows.append({'port': '*', 'code': 'too-large', 'message': f'the inputs are {total >> 20} MiB together; at most {MAX_BYTES >> 20} MiB'})
    contracts = (getattr(manifest, 'scientific', None) or {}).get('inputs', {}) if not rows else {}
    peers = {row.port: {**row.descriptor, 'dtype': row.array.dtype.name, 'shape': list(row.array.shape)} for row in bound}
    for row in bound:
        if row.port in contracts:
            for problem in validate(peers[row.port], parse_contract(contracts[row.port]), peers=peers, resource=row.port):
                rows.append({'port': row.port, 'code': problem.code, 'message': problem.message, 'next': problem.next})
    if rows:
        raise Refused(rows, next='choose a resource each input takes, or convert it explicitly first')
    return TypedCapture(bound)


def check_declaration(manifest, typed_inputs):
    """At dispatch: the bound inputs are exactly this capability's ports, in order, of their types."""
    ports = data_inputs(manifest)
    if [(p.id, p.type) for p in ports] != [(row.port, row.type) for row in typed_inputs]:
        raise Refused([{'port': '*', 'code': 'changed', 'message': 'the bound inputs no longer match this capability’s inputs'}],
                      next='bind its inputs again')


INPUTS_SCHEMA = {'type': 'object', 'description': 'for a capability with several or native inputs: one retained resource for '
                 'each input by its id, an array of a saved scientific document {"kind": "array", "session", "id", '
                 '"revision", "component"} or an earlier run\'s picture {"kind": "result", ...}; describe_capability '
                 'lists its inputs and their types',
                 'additionalProperties': {'type': 'object'}}


async def bind_at_run(hub, manifest, references, *, session):
    """bind, off the event loop, against the app's own retained stores and the actor's own session; the caller turns
    Refused into its words. The session is checked again after the off-loop bind: a run whose session changed while
    its inputs were read is refused, never run on the other session's data."""
    import asyncio
    from .resources import resources_of
    index = resources_of(hub)
    capture = await asyncio.to_thread(bind, manifest, references, store=getattr(index, 'projects', None), hub=hub,
                                      session=session)
    if any(row.reference.get('session') != session for row in capture.typed):
        raise Refused([{'port': '*', 'code': 'changed', 'message': "an input is from another session's data"}],
                      next='choose this session’s results again')
    return capture


def inputs_words(capture) -> str:
    """The inputs a typed run was bound to, in words: "its inputs labels (array labels of doc-1, revision 2), ..."."""
    said = []
    for row in capture.typed:
        ref = row.reference
        what = (f"array {ref['component'][len('array.'):]}" if str(ref.get('component', '')).startswith('array.')
                else "picture")
        said.append(f"{row.port} ({what} of {ref.get('id')}, revision {ref.get('revision')})")
    return "its inputs " + ", ".join(said)


def refusal_words(exc: Refused) -> str:
    said = [row['message'] if row['port'] == '*' else f"{row['port']}: {row['message']}" for row in exc.rows]
    return 'its inputs were not taken: ' + '; '.join(said)

