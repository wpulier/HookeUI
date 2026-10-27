"""Lazy, chunk-cached native arrays for napari layers (H08, P5 large data).

A `LazyNative` stands where a numpy array would in `viewer.add_image(...)`: it has napari's LayerDataProtocol
(`shape`, `dtype`, `__getitem__`) and nothing else that reads. Indexing reads only the chunks the slice touches,
through the bounded native regional transport (native_regions.NativeArray.read), and keeps them in a shared
`ChunkCache` whose resident bytes never exceed its capacity (least recently used first out). A whole-array
conversion (`np.asarray(lazy)`) is refused in words above SMALL_BYTES, so no supported path materializes a large
array by accident; a slice larger than MAX_SLICE_BYTES is refused before any read. Fancy and boolean indexing are
refused too: their reads cannot be bounded from the key alone.

`Multiscale` is the ordered list of levels napari takes with `multiscale=True`, finest first, each a LazyNative with
its downsample factors; every level shares the cache, so the working set is bounded across levels.

Counts (`stats`) are of actual transport reads, so tests and the large-data bench measure reads, not intentions.
"""
from __future__ import annotations

import itertools
import math
import threading
from collections import OrderedDict
from numbers import Integral

import numpy as np

from .native_regions import DTYPES, MAX_REGION_BYTES

SMALL_BYTES = 16 << 20          # np.asarray of a whole lazy array is allowed only below this
MAX_SLICE_BYTES = 256 << 20     # one indexing result; napari asks for one displayed slice at a time
DEFAULT_CACHE_BYTES = 256 << 20


class MaterializationRefused(ValueError):
    """A full or unbounded read of a lazy array; ask for a slice or a coarser level instead."""


class ChunkCache:
    """Chunks by (array id, level, chunk index), least recently used out first, bytes never above capacity."""

    def __init__(self, capacity_bytes: int = DEFAULT_CACHE_BYTES):
        if type(capacity_bytes) is not int or capacity_bytes < MAX_REGION_BYTES:
            raise ValueError("A chunk cache needs at least one region of capacity")
        self.capacity = capacity_bytes
        self._items: OrderedDict = OrderedDict()
        self._bytes = 0
        self._lock = threading.RLock()
        self.stats = {"hits": 0, "misses": 0, "readBytes": 0, "evictions": 0, "residentBytes": 0, "peakBytes": 0}

    def get(self, key, load):
        with self._lock:
            found = self._items.get(key)
            if found is not None:
                self._items.move_to_end(key)
                self.stats["hits"] += 1
                return found
        value = load()                                    # outside the lock: a read can take a while
        with self._lock:
            if key in self._items:                        # another thread read it meanwhile
                self._items.move_to_end(key)
                return self._items[key]
            self.stats["misses"] += 1
            self.stats["readBytes"] += value.nbytes
            self._items[key] = value
            self._bytes += value.nbytes
            while self._bytes > self.capacity and len(self._items) > 1:
                _, old = self._items.popitem(last=False)
                self._bytes -= old.nbytes
                self.stats["evictions"] += 1
            self.stats["residentBytes"] = self._bytes
            self.stats["peakBytes"] = max(self.stats["peakBytes"], self._bytes)
            return value

    def clear(self):
        with self._lock:
            self._items.clear()
            self._bytes = 0
            self.stats["residentBytes"] = 0


def default_chunks(shape, dtype) -> tuple[int, ...]:
    """One plane tile per chunk: up to 256 x 256 on the last two axes, 1 elsewhere, within one native region."""
    size = DTYPES[dtype]
    chunks = [1] * len(shape)
    for axis in range(max(0, len(shape) - 2), len(shape)):
        chunks[axis] = max(1, min(shape[axis], 256))
    while math.prod(chunks) * size > MAX_REGION_BYTES:
        i = max(range(len(chunks)), key=chunks.__getitem__)
        chunks[i] = max(1, chunks[i] // 2)
    return tuple(chunks)


def _normalize(key, shape):
    """(per-axis start, stop, step, integer?) for basic indexing only."""
    if not isinstance(key, tuple):
        key = (key,)
    if any(not isinstance(k, (Integral, slice, type(Ellipsis))) or isinstance(k, (bool, np.bool_)) for k in key):
        raise TypeError("Fancy, boolean and new-axis indexing of a lazy native array is refused: its reads cannot "
                        "be bounded from the index; use integers and slices")
    if any(k is None for k in key):
        raise TypeError("Lazy native arrays don't insert new axes; index the result instead")
    if sum(k is Ellipsis for k in key) > 1:
        raise IndexError("An index can only have a single ellipsis")
    if any(k is Ellipsis for k in key):
        i = next(n for n, k in enumerate(key) if k is Ellipsis)
        key = key[:i] + (slice(None),) * (len(shape) - len(key) + 1) + key[i + 1:]
    if len(key) > len(shape):
        raise IndexError("Too many indices for this array")
    key = key + (slice(None),) * (len(shape) - len(key))
    out = []
    for k, n in zip(key, shape):
        if isinstance(k, (bool, np.bool_)):
            raise TypeError("Boolean indexing of a lazy native array is refused: its reads cannot be bounded")
        if isinstance(k, Integral):
            i = int(k) + (n if k < 0 else 0)
            if not 0 <= i < n:
                raise IndexError("Index out of range")
            out.append((i, i + 1, 1, True))
        elif isinstance(k, slice):
            start, stop, step = k.indices(n)
            if step < 1:
                raise TypeError("Lazy native arrays read forward slices only")
            stop = max(start, stop)
            out.append((start, stop, step, False))
        else:
            raise TypeError("Fancy indexing of a lazy native array is refused: its reads cannot be bounded")
    return out


class LazyNative:
    """A read-through, chunk-cached view of one native array (see the module docstring)."""

    def __init__(self, source, cache: ChunkCache, *, chunks=None, ident: str | None = None, level: int = 0):
        self._source = source
        self._cache = cache
        self.shape = tuple(source.shape)
        self.dtype = np.dtype(source.dtype)
        self._wire_dtype = source.dtype
        self.chunks = tuple(chunks) if chunks is not None else default_chunks(self.shape, source.dtype)
        if len(self.chunks) != len(self.shape) or any(type(c) is not int or c < 1 for c in self.chunks):
            raise ValueError("Lazy chunks need one positive size per axis")
        if math.prod(self.chunks) * DTYPES[source.dtype] > MAX_REGION_BYTES:
            raise ValueError("A lazy chunk must fit one native region")
        self._ident = ident or f"{id(source):x}"
        self.level = level
        self.requests = 0

    ndim = property(lambda self: len(self.shape))
    size = property(lambda self: math.prod(self.shape))
    nbytes = property(lambda self: self.size * self.dtype.itemsize)

    def __len__(self):
        return self.shape[0]

    def __repr__(self):
        return f"LazyNative(shape={self.shape}, dtype={self.dtype}, chunks={self.chunks}, level={self.level})"

    def _chunk(self, index):
        bounds = [[i * c, min((i + 1) * c, n)] for i, c, n in zip(index, self.chunks, self.shape)]

        def load():
            self.requests += 1
            return np.ascontiguousarray(self._source.read(bounds, view=True))
        return self._cache.get((self._ident, self.level, index), load)

    def __getitem__(self, key):
        parts = _normalize(key, self.shape)
        box = [(start, stop) for start, stop, _, _ in parts]
        count = math.prod(stop - start for start, stop in box)
        if count * self.dtype.itemsize > MAX_SLICE_BYTES:
            raise MaterializationRefused(f"This slice needs {count * self.dtype.itemsize / 2**20:.0f} MiB at once; "
                                         f"ask for a smaller region or a coarser level (at most "
                                         f"{MAX_SLICE_BYTES >> 20} MiB per read)")
        out = np.empty([stop - start for start, stop in box], dtype=self.dtype)
        if count:
            ranges = [range(start // c, -(-stop // c)) for (start, stop), c in zip(box, self.chunks)]
            for index in itertools.product(*ranges):
                chunk = self._chunk(index)
                src, dst = [], []
                for (start, stop), i, c in zip(box, index, self.chunks):
                    lo, hi = max(start, i * c), min(stop, (i + 1) * c)
                    src.append(slice(lo - i * c, hi - i * c))
                    dst.append(slice(lo - start, hi - start))
                out[tuple(dst)] = chunk[tuple(src)]
        stepped = out[tuple(slice(None, None, step) for _, _, step, _ in parts)]
        squeeze = tuple(axis for axis, (*_, integer) in enumerate(parts) if integer)
        return np.squeeze(stepped, axis=squeeze) if squeeze else stepped

    def __array__(self, dtype=None, copy=None):
        if self.nbytes > SMALL_BYTES:
            raise MaterializationRefused(f"Refusing to read the whole {self.nbytes / 2**20:.0f} MiB array at once; "
                                         "it is read lazily, by slice")
        whole = self[...]
        return whole.astype(dtype) if dtype is not None else whole

    @property
    def stats(self):
        return {"requests": self.requests, **self._cache.stats}


class Multiscale(list):
    """Levels finest first, as napari takes them with `multiscale=True`; one shared cache bounds them all."""

    def __init__(self, sources, cache: ChunkCache, *, ident: str = "pyramid"):
        levels = [LazyNative(s, cache, ident=ident, level=i) for i, s in enumerate(sources)]
        if not levels:
            raise ValueError("A multiscale array needs at least one level")
        for finer, coarser in zip(levels, levels[1:]):
            if coarser.ndim != finer.ndim or any(c > f for c, f in zip(coarser.shape, finer.shape)):
                raise ValueError("Multiscale levels must keep their axes and never grow")
            if coarser.dtype != finer.dtype:
                raise ValueError("Multiscale levels must share one dtype")
        super().__init__(levels)
        self.cache = cache

    @property
    def downsample(self) -> list[tuple[float, ...]]:
        top = self[0].shape
        return [tuple(t / s if s else 1.0 for t, s in zip(top, level.shape)) for level in self]

    @property
    def stats(self):
        return {"requests": sum(level.requests for level in self), **self.cache.stats}
