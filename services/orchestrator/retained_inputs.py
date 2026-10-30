"""Exact retained capability pictures admitted through the existing result store.

Only a capability's lossless RGB result is an input here. A preview, composite,
camera capture or a class-label rendering cannot masquerade as scientific pixels.
Legacy scientific meaning remains unknown. No new resource registry is created.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import asyncio
import os
import stat
import threading
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace

from .brain.registry import ToolFailed
from .semantic_resources import Contract, parse_descriptor, parse_reference, validate

MAX_BYTES = 16 << 20
MAX_PIXELS = 1 << 20
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {key: {'type': 'string'} for key in ('session', 'id', 'revision', 'component')},
          'required': ['session', 'id', 'revision', 'component'],
          'description': 'An exact retained capability input from the input picker; never a preview URL or latest revision.'}


PICTURE_SCHEMA = SCHEMA
from .native_picture_inputs import SCHEMA as NATIVE_SCHEMA
SCHEMA = {"anyOf": [PICTURE_SCHEMA, NATIVE_SCHEMA]}


def supported(kind):
    return (kind.get('input') == 'image.2d.rgb8'
            and kind.get('output') in ('image.2d.rgb8', 'table.objects', 'text', 'points.2d')
            and len(kind.get('outputs') or ()) <= 1)


def _ref(value, session):
    value = parse_reference(value)
    if (value['session'] != session or re.fullmatch(r'd[0-9]+', value['id']) is None
            or value.get('component') != 'picture-0' or type(value['revision']) is not str):
        raise ValueError('Choose an exact retained capability picture in this session.')
    return value


def _facts(index, rid, session):
    record = index.results.get(rid, session)
    if (not record or record.get('tool') != 'run_capability' or len(record.get('pictures') or []) != 1
            or len(record.get('digests') or []) != 1):
        raise ValueError('This result has no single retained scientific picture to use.')
    metadata = json.loads(_read(index.results, record, rid + '.json', 1 << 20))
    if not isinstance(metadata, dict):
        raise ValueError('This result has invalid retained metadata. Restore it or rerun its producer.')
    values = metadata.get('values')
    if not isinstance(values, dict) or values.get('output') != 'image.2d.rgb8' or 'outputs' in values:
        raise ValueError('This result is not a supported lossless RGB capability output.')
    # This component's content revision includes the original digest and complete
    # scientific/producer values. Display prose and read badges cannot change it.
    document = {'values': values, 'sha256': record['digests'][0], 'source': record.get('source')}
    revision = hashlib.sha256(json.dumps(document, sort_keys=True, ensure_ascii=False,
                                        allow_nan=False, separators=(',', ':')).encode()).hexdigest()
    return record, values, revision


def reference_for(index, rid, session):
    """Public resource readers expose the same component identity as the Studio picker."""
    try:
        _record, _values, revision = _facts(index, rid, session)
    except (ValueError, OSError, TypeError):
        return None
    return {'session': session, 'id': rid, 'revision': revision, 'component': 'picture-0'}


@contextmanager
def _directory(store, record):
    date = record.get('day')
    if store.root is None or type(date) is not str or re.fullmatch(r'\d{4}-\d{2}-\d{2}', date) is None:
        raise ValueError('The result has no valid retained storage location.')
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    root = os.open(store.root_for(record.get('session') or date), flags)
    try:
        folder = os.open(date, flags, dir_fd=root)
        try:
            yield folder
        finally:
            os.close(folder)
    finally:
        os.close(root)


def _read(store, record, filename, maximum):
    from .bounded_files import read_at
    with _directory(store, record) as folder:
        return read_at(folder, filename, maximum)


@contextmanager
def _original(store, record, filename, memory, allowance):
    from .bounded_files import read_at
    with _directory(store, record) as folder:
        if type(filename) is not str or '/' in filename or '\\' in filename:
            raise ValueError('Invalid original filename')
        info = os.stat(filename, dir_fd=folder, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            raise ValueError('The original must be a bounded regular file')
        with memory.reserve(allowance, 2 * info.st_size, 'reading retained capability input'):
            # A file growing after stat is refused by the same bounded no-follow reader.
            yield read_at(folder, filename, info.st_size)


def choices(index, session, declaration=None):
    """Metadata only. Dispatch still verifies the original bytes and current revision."""
    entries = index.list(kind='result', day=session)
    result = []
    for entry in entries[-100:]:
        try:
            record, values, revision = _facts(index, entry.id, session)
        except (ValueError, OSError, TypeError):
            continue
        from .scientific_ports import compatibility
        try:
            check = compatibility(declaration, values.get('scientific')) if declaration else None
        except ValueError:
            check = {'compatible': False, 'says': 'The recorded scientific metadata is invalid. Rerun its producer.'}
        result.append({'title': record['title'], **({'compatibility': check} if check else {}), 'reference': {
            'session': session, 'id': entry.id, 'revision': revision, 'component': 'picture-0'}})
    return result


def layout(hub, reference, session):
    """Inspect retained RGB metadata and its fixed PNG header without reading or decoding pixel data.

    This is preflight only. resolve still verifies the complete original, digest and revision before use.
    """
    from .resources import resources_of
    from .source_images import _shape

    ref = _ref(reference, session)
    index = resources_of(hub)
    record, values, revision = _facts(index, ref['id'], session)
    if revision != ref['revision']:
        raise ValueError('The selected result changed. Choose its current exact revision again.')
    name = record['pictures'][0]
    if type(name) is not str or '/' in name or '\\' in name:
        raise ValueError('Invalid original filename')
    index.results.wait_for_picture(record['day'], name, session=session)
    with _directory(index.results, record) as folder:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
                raise ValueError('The original must be a bounded regular file')
            header = os.read(fd, 33)
        finally:
            os.close(fd)
    width, height, channels, media = _shape(header)
    if media != 'image/png' or channels != 3 or not 0 < width * height <= MAX_PIXELS:
        raise ValueError('This input needs a lossless RGB PNG of at most one megapixel; conversion must be explicit.')
    observed = {'dtype': 'uint8', 'axes': ['y', 'x', 'c'], 'shape': [height, width, 3], 'storage_format': 'png'}
    declared = parse_descriptor(values.get('scientific') or {}).wire()
    problems = validate(observed, Contract(exact={k: declared[k] for k in observed if declared.get(k) is not None}))
    if problems:
        raise ValueError(problems[0].message)
    return parse_descriptor({**declared, **observed, 'reference': ref}).wire()


def resolve(hub, reference, session, *, allowance="analysis", cancelled=None, expected_shape=None):
    """Read and admit exact native bytes off-loop; caller releases the returned hold."""
    import cv2
    import numpy as np
    from .capture import captures
    from .resources import resources_of
    from .source_images import _shape

    ref = _ref(reference, session)
    index, store = resources_of(hub), captures(hub)
    record, values, revision = _facts(index, ref['id'], session)
    if revision != ref['revision']:
        raise ValueError('The selected result changed. Choose its current exact revision again.')
    name = record['pictures'][0]
    if index.results.root is None:
        raise ValueError('This result has no retained storage. Rerun it with local storage available.')
    index.results.wait_for_picture(record['day'], name, cancelled=cancelled, session=session)
    with _original(index.results, record, name, store.memory, allowance) as body:
        if not body or hashlib.sha256(body).hexdigest() != record['digests'][0]:
            raise ValueError('The retained original is missing or changed. Restore its original bytes or rerun its producer.')
        width, height, channels, media = _shape(body)
        if media != 'image/png' or channels != 3 or not 0 < width * height <= MAX_PIXELS:
            raise ValueError('This input needs a lossless RGB PNG of at most one megapixel; conversion must be explicit.')
        if expected_shape is not None and tuple(expected_shape) != (height, width, 3):
            raise ValueError('The retained picture layout changed after input admission. Choose it again.')
        with store.memory.reserve(allowance, width * height * 6, 'validating retained input'):
            pixels = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_UNCHANGED)
            if pixels is None or pixels.shape != (height, width, 3) or pixels.dtype != np.uint8:
                raise ValueError('The retained pixels disagree with their declared shape or encoding.')
            observed = {'dtype': 'uint8', 'axes': ['y', 'x', 'c'], 'shape': [height, width, 3], 'storage_format': 'png'}
            declared = parse_descriptor(values.get('scientific') or {}).wire()
            problems = validate(observed, Contract(exact={k: declared[k] for k in observed if declared.get(k) is not None}))
            if problems:
                raise ValueError(problems[0].message)
            descriptor = parse_descriptor({**declared, **observed, 'reference': ref}).wire()
            # Recheck after disk work. A changed session is checked by the caller before dispatch.
            if _facts(index, ref['id'], session)[2] != revision:
                raise ValueError('The selected result changed while opening it. Select it again.')
            info = SimpleNamespace(width=width, height=height, epoch=None, dx=0., dy=0., fov=None,
                                   disc=0., tracked=False, steady=True, wall_at=time.time(), seq=None, boot='')
            capture = store.original(info, body, {'units': 'pixels', 'frame': [width, height]}, body,
                                     {'resource': ref, 'scientific': descriptor, 'mediaType': media,
                                      'sha256': record['digests'][0], 'width': width, 'height': height})
            hold = store.hold(capture, allowance, 'bytes', 'retained capability input')
    return capture, hold


def refusal(exc):
    return ToolFailed(str(exc), next='Choose a retained result again or rerun the capability that produced it.')


@asynccontextmanager
async def admitted(hub, reference, session, *, allowance="analysis"):
    if reference is None:
        yield None
        return
    stop = threading.Event()
    from .native_picture_inputs import resolve as native_resolve
    reader = native_resolve if isinstance(reference, dict) and reference.get("kind") == "array" else resolve
    task = asyncio.create_task(asyncio.to_thread(reader, hub, reference, session,
                                               allowance=allowance, cancelled=stop))
    result, cancelled = None, False
    try:
        while True:
            try:
                result = await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                cancelled = True
                stop.set()
                if task.done():
                    result = task.result()
                    break
        if cancelled:
            raise asyncio.CancelledError()
        from .resources import resources_of
        if resources_of(hub).session() != session:
            raise ValueError('The session changed while opening the input. Choose it again in the current session.')
        yield result[0]
    except (ValueError, OSError) as exc:
        raise refusal(exc) from exc
    finally:
        if result is not None:
            result[1].release()
