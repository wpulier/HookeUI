"""Owned grants for typed regional reads from already authorized immutable sources.

Callers supply trusted, bounded regional readers after resolving resource ownership
and the exact revision. This module does not open paths, download data, authorize
resources, or make an arbitrary lazy library's decoder safe. Decoder admission and
source-level memory limits remain requirements of each future source provider.
"""
from __future__ import annotations

import copy
import secrets
import threading
from dataclasses import dataclass

import numpy as np

from env_worker import native_regions as wire


@dataclass(frozen=True)
class Source:
    shape: tuple[int, ...]
    dtype: str
    axes: tuple[str, ...]
    descriptor: dict
    read: object
    chunks: tuple[int, ...] | None = None


class NativeRegionInputs:
    def __init__(self, sources: dict[str, Source], *, allow_empty=False):
        if type(sources) is not dict or not (0 if allow_empty else 1) <= len(sources) <= 64:
            raise ValueError('Native reads require 1 to 64 authorized sources')
        self._specs, self._sources = {}, {}
        self._budget = wire.Budget()
        self._closed = False
        self._read_lock = threading.Lock()
        for port, source in sources.items():
            if not isinstance(source, Source) or not callable(source.read):
                raise ValueError('Native sources need a bounded regional reader')
            token = secrets.token_hex(24)
            spec = {'format': wire.FORMAT, 'token': token, 'shape': list(source.shape),
                    'dtype': source.dtype, 'axes': list(source.axes), 'scientific': source.descriptor}
            if source.chunks is not None:
                spec['chunks'] = list(wire.chunk_layout(source.chunks, source.shape))
            # Identical metadata validation at both ends; no invented scientific meaning.
            wire.bind({port: spec}, lambda _: None)
            self._specs[port] = copy.deepcopy(spec)
            self._sources[token] = (copy.deepcopy(spec), source.read)

    def specifications(self):
        with self._budget.lock:
            if self._closed:
                raise ValueError('This native input grant is closed')
            return copy.deepcopy(self._specs)

    def close(self):
        with self._budget.lock:
            self._closed = True
            self._sources.clear()

    def answer(self, header, payload_size=0):
        # Serialize decoder access without making revocation wait on external I/O.
        with self._read_lock:
            with self._budget.lock:
                if self._closed:
                    raise ValueError('This native input grant is closed')
                if (type(header) is not dict or set(header) not in ({'op', 'token', 'region'}, {'op', 'token', 'region', 'view'})
                        or header['op'] != 'read-native-region' or type(header['token']) is not str
                        or header.get('view', True) is not True
                        or header['token'] not in self._sources or type(payload_size) is not int or payload_size != 0):
                    raise ValueError('This native request has no valid payload-free input grant')
                spec, read = self._sources[header['token']]
                slices, shape, size = wire.region(header['region'], spec['shape'], spec['dtype'])
                # A view read (a lazy layer being navigated) is bounded per rolling window rather than over the
                # grant's life. The worker chooses which it asks for; either way the reads stay bounded.
                self._budget.admit(size, view='view' in header)
            data = read(slices)
            # Do not call asarray on arbitrary objects: that can materialize an entire
            # lazy dataset before its size is checked. The reader must return its chunk.
            with self._budget.lock:
                if self._closed:
                    raise ValueError('This native input grant closed before its read completed')
                if (type(data) is not np.ndarray or data.shape != shape
                        or data.dtype.str != spec['dtype'] or data.nbytes != size):
                    raise ValueError('The regional source returned a different shape, dtype or byte count')
                return {'ok': True, 'kind': wire.FORMAT, 'shape': list(shape), 'dtype': spec['dtype']}, data.tobytes(order='C')
