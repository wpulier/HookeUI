"""Bounded, invocation-scoped RGB reads for the regions-v1 worker bridge."""
from __future__ import annotations

import copy
import json
import math
import threading
from dataclasses import dataclass, field
from typing import Callable

MAX_REGION_BYTES = 1 << 20
MAX_READ_REQUESTS = 1024
MAX_READ_BYTES = 64 << 20
_MAX_PIXELS = 1 << 20
_MAX_METADATA_BYTES = 128 << 10


def _shape(value):
    if (type(value) not in (list, tuple) or len(value) != 3
            or any(type(n) is not int or not 0 < n <= _MAX_PIXELS for n in value)
            or value[2] != 3 or value[0] * value[1] > _MAX_PIXELS):
        raise ValueError('A region resource must be RGB8 with at most 1 Mi pixels')
    return tuple(value)


def validate_region(region, shape, max_bytes=MAX_REGION_BYTES):
    """Return slices, chunk shape and byte count without allocating pixel data."""
    shape = _shape(shape)
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_REGION_BYTES:
        raise ValueError('The region byte limit must be a positive integer at most 1 MiB')
    if type(region) not in (list, tuple) or len(region) != len(shape):
        raise ValueError('A region needs one (start, stop) pair per axis')
    slices, chunk_shape, nbytes = [], [], 1
    for bounds, size in zip(region, shape):
        if (type(bounds) not in (list, tuple) or len(bounds) != 2
                or any(type(n) is not int for n in bounds)):
            raise ValueError('Region bounds must be integer (start, stop) pairs with unit stride')
        start, stop = bounds
        if not 0 <= start < stop <= size:
            raise ValueError('Region bounds must be nonempty and inside the resource')
        length = stop - start
        nbytes *= length
        if nbytes > max_bytes:
            raise ValueError('The region exceeds its byte limit; request a smaller region')
        slices.append(slice(start, stop))
        chunk_shape.append(length)
    return tuple(slices), tuple(chunk_shape), nbytes


def _metadata(value):
    if type(value) is not dict:
        raise ValueError('Scientific metadata must be a JSON object')
    pending, nodes, text_size = [(value, 0)], 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if nodes > 8192 or depth > 32:
            raise ValueError('Scientific metadata is too large or deeply nested')
        if type(item) in (dict, list):
            if len(item) + len(pending) + nodes > 8192:
                raise ValueError('Scientific metadata contains too many values')
            if type(item) is dict:
                for key in item:
                    if type(key) is not str:
                        raise ValueError('Scientific metadata keys must be strings')
                    text_size += len(key)
                pending.extend((child, depth + 1) for child in item.values())
            else:
                pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            text_size += len(item)
        elif item is not None and type(item) is not bool:
            if (type(item) not in (int, float)
                    or type(item) is int and abs(item) >= 2**53
                    or type(item) is float and not math.isfinite(item)):
                raise ValueError('Scientific metadata must contain finite JSON values')
        if text_size > _MAX_METADATA_BYTES:
            raise ValueError('Scientific metadata text is too large')
    encoded = json.dumps(value, allow_nan=False).encode('utf-8')
    if len(encoded) > _MAX_METADATA_BYTES:
        raise ValueError('Scientific metadata exceeds 128 KiB')
    return json.loads(encoded)


@dataclass
class _Budget:
    requests: int = 0
    bytes: int = 0
    lock: object = field(default_factory=threading.Lock)

    def admit(self, nbytes):
        if self.requests >= MAX_READ_REQUESTS or self.bytes + nbytes > MAX_READ_BYTES:
            raise ValueError('This invocation has exhausted its regional read budget')
        self.requests += 1
        self.bytes += nbytes


@dataclass(frozen=True)
class ArrayResource:
    shape: tuple[int, int, int]
    dtype: str
    _descriptor: dict
    _token: str
    _request: Callable
    _budget: _Budget

    @property
    def descriptor(self):
        return copy.deepcopy(self._descriptor)

    @property
    def reference(self):
        return copy.deepcopy(self._descriptor.get('reference'))

    def read(self, region):
        slices, shape, nbytes = validate_region(region, self.shape)
        with self._budget.lock:
            self._budget.admit(nbytes)
            response = self._request({'op': 'read-region', 'token': self._token,
                                      'region': [[s.start, s.stop] for s in slices]})
        if type(response) is not tuple or len(response) != 2:
            raise ValueError('Invalid regional read response')
        header, payload = response
        expected = {'ok': True, 'kind': 'region', 'shape': list(shape), 'dtype': 'uint8'}
        if (type(header) is not dict or header != expected or header.get('ok') is not True
                or type(header.get('shape')) is not list
                or any(type(n) is not int for n in header['shape'])):
            raise ValueError('The regional read response does not match the requested region')
        if type(payload) is not bytes or len(payload) != nbytes:
            raise ValueError('The regional read returned an invalid byte count or mutable payload')
        import numpy as np

        return np.frombuffer(payload, dtype=np.uint8).reshape(shape)


def bind(specs, request) -> dict[str, ArrayResource]:
    """Bind host-issued tokens to one invocation's transport and shared read limits."""
    if type(specs) is not dict or not 0 < len(specs) <= 64 or not callable(request):
        raise ValueError('Resource binding requires a nonempty port map and a request callback')
    budget, resources = _Budget(), {}
    for port, spec in specs.items():
        if type(port) is not str or not 0 < len(port) <= 128:
            raise ValueError('Resource ports must be nonempty strings of at most 128 characters')
        if type(spec) is not dict or set(spec) != {'token', 'shape', 'dtype', 'scientific'}:
            raise ValueError('A resource specification needs token, shape, dtype and scientific metadata')
        if type(spec['token']) is not str or not 0 < len(spec['token']) <= 256:
            raise ValueError('A resource token must be a nonempty string of at most 256 characters')
        if spec['dtype'] != 'uint8' or type(spec['dtype']) is not str:
            raise ValueError('Region resources currently require uint8')
        shape = _shape(spec['shape'])
        descriptor = _metadata(spec['scientific'])
        if ('dtype' in descriptor and descriptor['dtype'] != 'uint8'
                or 'shape' in descriptor and (type(descriptor['shape']) is not list
                    or any(type(n) is not int for n in descriptor['shape'])
                    or descriptor['shape'] != list(shape))):
            raise ValueError('Scientific metadata contradicts the resource dtype or shape')
        resources[port] = ArrayResource(shape, 'uint8', descriptor, spec['token'], request, budget)
    return resources
