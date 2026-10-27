"""Bounded NPY intake shared by headless runs and install proofs; axes come only from explicit descriptors."""
from __future__ import annotations

from contextlib import ExitStack
import hashlib
import io
import math
import os
from pathlib import Path
import stat

import numpy as np

from . import admission, jobs

MAX_HEADER = 16 << 10


def _stamp(value):
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _header(stream, size):
    """Bound the length before NumPy's header parser reads it; never allocate from untrusted shape arithmetic."""
    version = np.lib.format.read_magic(stream)
    widths = {(1, 0): 2, (2, 0): 4}
    if version not in widths:
        raise ValueError('use a numeric NPY version 1 or 2 input; this format is unsupported')
    width = widths[version]
    length = stream.read(width)
    if len(length) != width or not 0 < int.from_bytes(length, 'little') <= MAX_HEADER:
        raise ValueError('the NPY header exceeds its bounded metadata allowance or is truncated')
    header = stream.read(int.from_bytes(length, 'little'))
    if len(header) != int.from_bytes(length, 'little'):
        raise ValueError('the NPY header is truncated')
    reader = np.lib.format.read_array_header_1_0 if version == (1, 0) else np.lib.format.read_array_header_2_0
    shape, order, dtype = reader(io.BytesIO(length + header), max_header_size=MAX_HEADER)
    if (dtype.hasobject or dtype.fields is not None or not 1 <= len(shape) <= 16
            or any(type(n) is not int or not 0 <= n <= admission.MAX_BYTES for n in shape)):
        raise ValueError('the NPY needs bounded numeric dimensions without objects or structured values')
    decoded = math.prod(shape) * dtype.itemsize
    if decoded > admission.MAX_BYTES:
        raise ValueError('the decoded NPY exceeds the typed input memory allowance')
    if stream.tell() + decoded != size:
        raise ValueError('the NPY payload size disagrees with its header (truncated or trailing bytes)')
    return shape, dtype, decoded, hashlib.sha256(header).hexdigest()


def load(manifest, files, *, maximum_file=None):
    """Admit {port: {path, descriptor, reference, sha256?}} as one group before payload reads.

    Open regular files stay owned across preflight/read. Each file is bounded and checked against its opened identity,
    path identity and optional authorized digest. The transient reservation covers encoded buffers, decoded arrays
    and admission's content-hash copy; the runner owns its separate execution reservation afterwards.
    """
    reason = admission.why_not(manifest)
    if reason:
        raise admission.Refused([{'port': '*', 'code': 'unsupported', 'message': reason}],
                                next='choose a capability that accepts these input types')
    ports = admission.data_inputs(manifest)
    rows = [{'port': name, 'code': 'unknown', 'message': 'this capability has no input of that name'}
            for name in sorted(set(files) - {port.id for port in ports})]
    prepared, encoded, decoded = {}, 0, 0
    with ExitStack() as stack:
        for port in ports:
            if port.id not in files:
                rows.append({'port': port.id, 'code': 'missing', 'message': 'choose an explicit input for this port'})
                continue
            source = files[port.id]
            try:
                if isinstance(source, Exception):
                    raise source
                path = Path(source['path'])
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                stream = stack.enter_context(os.fdopen(fd, 'rb', buffering=0))
                before = os.fstat(stream.fileno())
                limit = maximum_file if maximum_file is not None else admission.MAX_BYTES + MAX_HEADER + 12
                if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                    raise ValueError('the input needs a bounded regular NPY file')
                layout = _header(stream, before.st_size)
                shape, dtype, size, _header_digest = layout
                problem = admission.check_layout(port, shape, dtype, source['descriptor'])
                if problem:
                    raise ValueError(problem)
                from gpu_worker.semantic_resources import parse_descriptor
                descriptor = parse_descriptor(source['descriptor']).wire()
                prepared[port.id] = (source, path, stream, before, descriptor, layout)
                encoded += before.st_size
                decoded += size
            except (ValueError, OSError, TypeError, KeyError) as exc:
                rows.append({'port': port.id, 'code': 'unavailable', 'message': str(exc)})
        if decoded > admission.MAX_BYTES:
            rows.append({'port': '*', 'code': 'too-large',
                         'message': f'the inputs are {decoded} bytes together; at most {admission.MAX_BYTES}'})
        if rows:
            raise admission.Refused(rows, next='choose a resource each input takes, or convert it explicitly first')
        try:
            with jobs.MEMORY.reserve((2 * encoded + 2 * decoded) / (1 << 20)):
                resolved = {}
                for name, (source, path, stream, before, descriptor, layout) in prepared.items():
                    shape, dtype, _size, _header_digest = layout
                    try:
                        if _stamp(os.fstat(stream.fileno())) != _stamp(before) or _stamp(path.lstat()) != _stamp(before):
                            raise ValueError('the input changed after preflight; authorize it again')
                        stream.seek(0)
                        body = stream.read(before.st_size + 1)
                        digest = hashlib.sha256(body).hexdigest()
                        if len(body) != before.st_size or (source.get('sha256') and digest != source['sha256']):
                            raise ValueError('the input changed after it was authorized: its bytes are not the ones asked for')
                        if _stamp(os.fstat(stream.fileno())) != _stamp(before) or _stamp(path.lstat()) != _stamp(before):
                            raise ValueError('the input changed while it was read; authorize it again')
                        # NumPy allocates from the header in this buffer, not the
                        # earlier file header. Recheck bounds and the complete
                        # header (including storage order) before allowing it to load.
                        buffered = io.BytesIO(body)
                        if _header(buffered, len(body)) != layout:
                            raise ValueError('the NPY header changed after preflight; authorize it again')
                        buffered.seek(0)
                        array = np.load(buffered, allow_pickle=False, max_header_size=MAX_HEADER)
                        if (_stamp(os.fstat(stream.fileno())) != _stamp(before) or _stamp(path.lstat()) != _stamp(before)
                                or array.shape != shape or array.dtype != dtype):
                            raise ValueError('the input changed while it was read; authorize it again')
                        resolved[name] = (array, descriptor, {**source['reference'], 'sha256': digest})
                        del body
                    except (ValueError, OSError, TypeError) as exc:
                        raise admission.Refused([{'port': name, 'code': 'changed', 'message': str(exc)}],
                                                next='select and authorize the current input again') from exc
                return admission.admit(manifest, resolved)
        except jobs.MemoryRefused as exc:
            raise admission.Refused([{'port': '*', 'code': 'memory', 'message': str(exc)}],
                                    next='wait for other work to finish') from exc
