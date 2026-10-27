"""Native-array regional transport for the forthcoming interactive host bridge.

This is a transport primitive, not a registered runtime or a general array loader.
The existing RGB regions-v1 contract is unchanged. Host-issued grants identify exact
retained revisions; workers receive no source paths. Arrays cannot be coerced into
an implicit full read. Limits apply across every port bound to one invocation.
"""
from __future__ import annotations

import copy
import contextvars
from contextlib import contextmanager
import math
import re
import threading
import time
from dataclasses import dataclass, field

from .region_reader import _metadata
from gpu_worker.semantic_resources import parse_descriptor, parse_reference

FORMAT = 'native-array-regions-v1'
MAX_REGION_BYTES = 1 << 20
MAX_READ_BYTES = 64 << 20
MAX_REQUESTS = 1024
# Viewing a large array lazily (env_worker.lazy_arrays) reads as the user navigates, so its reads are bounded per
# rolling window, not over the invocation's whole life; resident memory is bounded by the chunk cache instead.
VIEW_WINDOW_S = 10.0
MAX_VIEW_BYTES = 512 << 20
MAX_VIEW_REQUESTS = 8192
MAX_AXES = 8
DTYPES = {f'{order}{kind}{size}': size for order in ('<', '>')
          for kind, sizes in (('i', (2, 4, 8)), ('u', (2, 4, 8)), ('f', (2, 4, 8)))
          for size in sizes} | {'|u1': 1, '|i1': 1, '|b1': 1}
IDENTITY = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}')


def layout(shape, dtype, axes):
    if (type(shape) not in (list, tuple) or not 1 <= len(shape) <= MAX_AXES
            or any(type(size) is not int or not 0 <= size < 2**53 for size in shape)):
        raise ValueError('Native arrays require 1 to 8 bounded dimensions with nonnegative lengths')
    if type(dtype) is not str or dtype not in DTYPES:
        raise ValueError('Native dtype must declare a supported scalar type and exact byte order')
    if math.prod(max(1,n) for n in shape) * DTYPES[dtype] >= 2**63:
        raise ValueError('The logical array exceeds the supported address range')
    if (type(axes) not in (list, tuple) or len(axes) != len(shape)
            or any(type(axis) is not str or IDENTITY.fullmatch(axis) is None for axis in axes)
            or len(set(axes)) != len(axes)):
        raise ValueError('Each dimension needs a distinct declared axis identity')
    return tuple(shape), dtype, tuple(axes)


def region(bounds, shape, dtype):
    """Validate every bound and account for item size before a source can be read."""
    if type(bounds) not in (list, tuple) or len(bounds) != len(shape):
        raise ValueError('A native region needs one start/stop pair per axis')
    slices, dimensions = [], []
    for pair, size in zip(bounds, shape):
        if (type(pair) not in (list, tuple) or len(pair) != 2
                or any(type(n) is not int for n in pair) or not 0 <= pair[0] <= pair[1] <= size):
            raise ValueError('Native region bounds must be ordered integer ranges inside the resource')
        slices.append(slice(*pair))
        dimensions.append(pair[1] - pair[0])
    count = math.prod(dimensions) * DTYPES[dtype]
    if count > MAX_REGION_BYTES:
        raise ValueError('The native region exceeds its byte allowance; request a smaller region')
    return tuple(slices), tuple(dimensions), count


def chunk_layout(chunks, shape):
    """Storage boundaries guide requests; they never increase transport or decoder limits."""
    if (type(chunks) not in (list, tuple) or len(chunks) != len(shape)
            or any(type(n) is not int or not 1 <= n < 2**53 for n in chunks)
            or math.prod(chunks) >= 2**63):
        raise ValueError('Native storage chunks need one bounded positive size per axis')
    return tuple(chunks)


def specification(value):
    required = {'format', 'token', 'shape', 'dtype', 'axes', 'scientific'}
    if type(value) is not dict or set(value) not in (required, required | {'chunks'}) or value['format'] != FORMAT:
        raise ValueError('A native array needs a versioned, complete resource specification')
    if type(value['token']) is not str or not 1 <= len(value['token']) <= 256:
        raise ValueError('A native array needs an opaque read grant')
    shape, dtype, axes = layout(value['shape'], value['dtype'], value['axes'])
    if 'chunks' in value:
        chunk_layout(value['chunks'], shape)
    descriptor = parse_descriptor(_metadata(value['scientific'])).wire()
    ref = parse_reference(descriptor.get('reference'))
    if 'component' not in ref:
        raise ValueError('A native array needs an exact retained component')
    if descriptor.get('shape') != list(shape) or descriptor.get('axes') != list(axes):
        raise ValueError('Scientific metadata must match the native array shape and axes')
    # Descriptor dtype is the scientific scalar name; wire dtype additionally fixes byte order.
    import numpy as np
    if descriptor.get('dtype') != np.dtype(dtype).name:
        raise ValueError('Scientific metadata must match the native scalar dtype')
    return shape, dtype, axes, descriptor


class ViewSuperseded(RuntimeError):
    """Only the current display read was superseded; retained inputs stay valid."""


_paced = contextvars.ContextVar('native-save-paced-reads', default=None)


@contextmanager
def paced_view_reads(check):
    token = _paced.set(check)
    try:
        check()
        yield
        check()
    finally:
        _paced.reset(token)


@dataclass
class Budget:
    """Operation reads: bounded over the invocation's life. View reads: bounded per rolling VIEW_WINDOW_S."""
    requests: int = 0
    bytes: int = 0
    lock: object = field(default_factory=threading.RLock)
    views: list = field(default_factory=list)          # (monotonic time, bytes) of view reads in the window
    clock: object = time.monotonic

    def finished_view(self):
        if self.views:
            _, size = self.views[-1]
            self.views[-1] = (self.clock(), size)

    def admit(self, size, *, view=False):
        if view:
            if size > MAX_VIEW_BYTES:
                raise ValueError('The native region exceeds its navigation window allowance')
            check = _paced.get()
            while check is not None:
                check()
                now = self.clock()
                self.views = [(t,n) for t,n in self.views if now-t < VIEW_WINDOW_S]
                if len(self.views) < MAX_VIEW_REQUESTS and sum(n for _,n in self.views)+size <= MAX_VIEW_BYTES:
                    break
                time.sleep(min(.05, max(.001, self.views[0][0]+VIEW_WINDOW_S-now)))
            now = self.clock()
            self.views = [(t, n) for t, n in self.views if now - t < VIEW_WINDOW_S]
            if len(self.views) >= MAX_VIEW_REQUESTS or sum(n for _, n in self.views) + size > MAX_VIEW_BYTES:
                raise ValueError('Navigation is reading faster than its allowance; wait a moment, then continue')
            self.views.append((now, size))
            return
        if self.requests >= MAX_REQUESTS or self.bytes + size > MAX_READ_BYTES:
            raise ValueError('This invocation exhausted its native regional read allowance')
        self.requests += 1
        self.bytes += size


@dataclass(frozen=True)
class NativeArray:
    shape: tuple[int, ...]
    dtype: str
    axes: tuple[str, ...]
    _descriptor: dict
    _token: str
    _request: object
    _budget: Budget
    chunks: tuple[int, ...] | None = None

    @property
    def descriptor(self):
        return copy.deepcopy(self._descriptor)

    @property
    def reference(self):
        return copy.deepcopy(self._descriptor['reference'])

    def regions(self):
        """Bounded regions aligned to storage; iteration allocates no pixel data.

        Adjacent small chunks share a request (at most 64, within both current
        decoder chunk-count bounds). Larger chunks are subdivided internally.
        Parent decoder, transport and rolling budgets still admit every read.
        """
        storage = list(self.chunks or tuple(max(1, n) for n in self.shape))
        if self.chunks is not None:
            count = 1
            for axis in reversed(range(len(storage))):
                available = -(-self.shape[axis] // self.chunks[axis])
                factor = 1
                while factor < available:
                    candidate = min(factor * 2, available)
                    enlarged = storage.copy()
                    enlarged[axis] = self.chunks[axis] * candidate
                    chunks = count // factor * candidate
                    if chunks > 64 or math.prod(enlarged) * DTYPES[self.dtype] > MAX_REGION_BYTES:
                        break
                    storage, count, factor = enlarged, chunks, candidate

        def starts(shape, steps, axis=0, prefix=()):
            # Unlike itertools.product, do not pool logical-dimension ranges.
            if axis == len(shape):
                yield prefix
            else:
                for start in range(0, shape[axis], steps[axis]):
                    yield from starts(shape, steps, axis + 1, (*prefix, start))

        for origin in starts(self.shape, storage):
            extent = [min(c, n - start) for start, c, n in zip(origin, storage, self.shape)]
            tile = extent.copy()
            while math.prod(tile) * DTYPES[self.dtype] > MAX_REGION_BYTES:
                axis = max(range(len(tile)), key=tile.__getitem__)
                tile[axis] = max(1, tile[axis] // 2)
            for offset in starts(extent, tile):
                yield [[start + part, start + min(part + c, n)]
                       for start, part, c, n in zip(origin, offset, tile, extent)]

    def read(self, bounds, *, view=False):
        """`view=True` for lazy display reads (env_worker.lazy_arrays): admitted per rolling window."""
        slices, shape, size = region(bounds, self.shape, self.dtype)
        with self._budget.lock:
            self._budget.admit(size, view=view)
            reply = self._request({'op': 'read-native-region', 'token': self._token,
                                   'region': [[part.start, part.stop] for part in slices], **({'view': True} if view else {})})
            if view:
                self._budget.finished_view()
        expected = {'ok': True, 'kind': FORMAT, 'shape': list(shape), 'dtype': self.dtype}
        if type(reply) is not tuple or len(reply) != 2:
            raise ValueError('Invalid native read reply')
        header, payload = reply
        if (type(header) is not dict or header != expected or header.get('ok') is not True
                or type(header.get('shape')) is not list or any(type(n) is not int for n in header['shape'])
                or type(payload) is not bytes or len(payload) != size):
            raise ValueError('Native read reply does not match the admitted region')
        import numpy as np
        return np.frombuffer(payload, dtype=self.dtype).reshape(shape)


def bind(specs, request, *, allow_empty=False):
    if type(specs) is not dict or not (0 if allow_empty else 1) <= len(specs) <= 64 or not callable(request):
        raise ValueError('Native bindings require a bounded port map and transport')
    result, budget = {}, Budget()
    for port, spec in specs.items():
        if type(port) is not str or IDENTITY.fullmatch(port) is None:
            raise ValueError('Invalid native resource port')
        shape, dtype, axes, descriptor = specification(spec)
        result[port] = NativeArray(shape, dtype, axes, descriptor, spec['token'], request, budget,
                                   chunk_layout(spec['chunks'], shape) if 'chunks' in spec else None)
    return result
