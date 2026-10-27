"""Bounded native chunk writes through an already admitted parent-owned edit.

The worker receives an opaque grant, not a retained path or save authority. Only the
parent can accept a revision. This transport does not create a host or grant an edit.
"""
from __future__ import annotations

import copy
import math
import threading
from dataclasses import dataclass, field

from . import native_regions as regions
from gpu_worker.semantic_resources import parse_reference

FORMAT = 'native-array-edits-v1'
OUTPUT_FORMAT = 'native-array-output-write-v1'
MAX_CHUNKS = 65536


def layout(shape, dtype, axes, chunks):
    shape, dtype, axes = regions.layout(shape, dtype, axes)
    if (type(chunks) not in (tuple, list) or len(chunks) != len(shape)
            or any(type(n) is not int or not 0 < n <= max(1,size) for n, size in zip(chunks, shape))):
        raise ValueError('Native edit chunks must fit each declared dimension')
    if math.prod(chunks) * regions.DTYPES[dtype] > regions.MAX_REGION_BYTES:
        raise ValueError('Native edit chunk exceeds its byte allowance')
    grid = tuple((size + chunk - 1) // chunk for size, chunk in zip(shape, chunks))
    if math.prod(max(1,n) for n in grid) > MAX_CHUNKS:
        raise ValueError('Native edit has too many chunks')
    return shape, dtype, axes, tuple(chunks), grid


def chunk(index, shape, dtype, chunks, grid):
    if (type(index) not in (tuple, list) or len(index) != len(grid)
            or any(type(n) is not int or not 0 <= n < limit for n, limit in zip(index, grid))):
        raise ValueError('Native edit chunk lies outside its component')
    dimensions = tuple(min(size - i * c, c) for i, c, size in zip(index, chunks, shape))
    return dimensions, math.prod(dimensions) * regions.DTYPES[dtype]


@dataclass(frozen=True)
class Kept:
    """A chunk whose frozen bytes hash to its accepted base chunk: nothing to send."""
    sha256: str


@dataclass(frozen=True)
class BaseChunk:
    """Untouched regional chunk; the parent resolves its exact admitted base digest."""


@dataclass(frozen=True)
class NativeEdit:
    shape: tuple[int, ...]
    dtype: str
    axes: tuple[str, ...]
    chunks: tuple[int, ...]
    grid: tuple[int, ...]
    project_revision: int
    _reference: dict | None
    _token: str
    _request: object
    _format: str = FORMAT
    _lock: object = field(default_factory=threading.RLock, init=False, repr=False)

    @property
    def reference(self):
        return copy.deepcopy(self._reference)

    def write(self, index, values):
        import numpy as np
        shape, size = chunk(index, self.shape, self.dtype, self.chunks, self.grid)
        if type(values) is not np.ndarray or values.shape != shape or values.dtype.str != self.dtype:
            raise ValueError('Native edit values must match the exact shape and dtype')
        with self._lock:
            response = self._request({'op': 'write-native-chunk', 'token': self._token, 'index': list(index),
                                      'shape': list(shape), 'dtype': self.dtype}, values.tobytes(order='C'))
        expected = {'ok': True, 'kind': self._format, 'index': list(index), 'bytes': size}
        if (type(response) is not tuple or len(response) != 2 or response[1] != b''
                or type(response[0]) is not dict or response[0] != expected or response[0].get('ok') is not True
                or type(response[0].get('bytes')) is not int
                or any(type(n) is not int for n in response[0].get('index', []))):
            raise ValueError('Native chunk acknowledgement does not match this edit')

    def keep(self, index, sha256):
        """Claim one chunk is unchanged from the accepted base; the parent checks the full digest."""
        chunk(index, self.shape, self.dtype, self.chunks, self.grid)
        if self._format != FORMAT:
            raise ValueError('Only an edit of an accepted base can keep chunks')
        with self._lock:
            response = self._request({'op': 'keep-native-chunk', 'token': self._token, 'index': list(index),
                                      'sha256': sha256}, b'')
        expected = {'ok': True, 'kind': self._format, 'index': list(index), 'bytes': 0, 'kept': True}
        if type(response) is not tuple or len(response) != 2 or response[1] != b'' or response[0] != expected:
            raise ValueError('Native chunk acknowledgement does not match this edit')

    def put(self, index, item):
        """One staged chunk: its values, or a Kept claim from an incremental snapshot."""
        if isinstance(item, BaseChunk):
            chunk(index,self.shape,self.dtype,self.chunks,self.grid)
            if self._format!=FORMAT:raise ValueError('Only an accepted edit can retain its base chunks')
            with self._lock:
                response=self._request({'op':'keep-native-base-chunk','token':self._token,'index':list(index)},b'')
            expected={'ok':True,'kind':FORMAT,'index':list(index),'bytes':0,'kept':True}
            if type(response) is not tuple or len(response)!=2 or response!=(expected,b''):
                raise ValueError('Native base chunk acknowledgement does not match this edit')
        elif isinstance(item, Kept):
            self.keep(index, item.sha256)
        else:
            self.write(index, item)

    def commit(self):
        if self._format != FORMAT:
            raise ValueError("Only the parent may publish a new output")
        with self._lock:
            response = self._request({'op': 'commit-native-edit', 'token': self._token}, b'')
        if type(response) is not tuple or len(response) != 2 or response[1] != b'':
            raise ValueError('Invalid native edit acknowledgement')
        header = response[0]
        if (type(header) is not dict or set(header) != {'ok', 'kind', 'reference', 'projectRevision'}
                or header['ok'] is not True or header['kind'] != FORMAT
                or type(header['projectRevision']) is not int or header['projectRevision'] != self.project_revision + 1):
            raise ValueError('Invalid native edit acknowledgement')
        reference = parse_reference(header['reference'])
        if (any(reference.get(key) != self._reference[key] for key in ('session', 'id', 'component'))
                or type(reference['revision']) is not str):
            raise ValueError('Native edit acknowledgement belongs to another component')
        return copy.deepcopy(header)


def bind(spec, request):
    if (type(spec) is not dict or set(spec) != {'format', 'token', 'shape', 'dtype', 'axes', 'chunks', 'reference', 'projectRevision'}
            or spec['format'] != FORMAT or type(spec['token']) is not str or not 1 <= len(spec['token']) <= 256
            or type(spec['projectRevision']) is not int or not 1 <= spec['projectRevision'] < 4096
            or not callable(request)):
        raise ValueError('A native edit needs a complete versioned grant')
    shape, dtype, axes, chunks, grid = layout(spec['shape'], spec['dtype'], spec['axes'], spec['chunks'])
    reference = parse_reference(spec['reference'])
    if 'component' not in reference or type(reference['revision']) is not str:
        raise ValueError('A native edit needs an exact retained component revision')
    return NativeEdit(shape, dtype, axes, chunks, grid, spec['projectRevision'], reference, spec['token'], request)


def bind_output(spec, request):
    if (type(spec) is not dict or set(spec) != {'format','token','shape','dtype','axes','chunks'}
            or spec['format'] != OUTPUT_FORMAT or type(spec['token']) is not str
            or not 1 <= len(spec['token']) <= 256 or not callable(request)):
        raise ValueError('A new output needs its own bounded write grant')
    shape,dtype,axes,chunks,grid = layout(spec['shape'],spec['dtype'],spec['axes'],spec['chunks'])
    return NativeEdit(shape,dtype,axes,chunks,grid,0,None,spec['token'],request,OUTPUT_FORMAT)
