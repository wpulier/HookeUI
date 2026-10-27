"""OME-Zarr (NGFF 0.4, zarr v2) multiscale images as native regional sources, local or remote (H08, P5).

The parent reads and decodes; the confined worker only ever receives native regions through the existing grant
(orchestrator.native_regions), so it needs no network, no path and no codec. `open_multiscale(store)` reads the
image's `.zattrs` and each level's `.zarray` and returns a `Pyramid` whose levels each become a
`native_regions.Source` (`Pyramid.sources()`), finest first, for a lazy multiscale layer in the worker
(env_worker.lazy_arrays.Multiscale).

SelectedStore resolves one accepted source-directory document through ProjectStore.
Its existing content-addressed file manifest and complete directory index pin every
pixel and missing key. Opening checks ownership and metadata; each requested file
chunk is digest-verified before decoding. Raw LocalStore/RemoteStore are bounded
transport readers only: open_multiscale refuses them without immutable capture.
Remote reads reject redirects, retain granted credentials in the parent and retry
boundedly. Audit byte counts describe returned entry bytes, not physical disk I/O.

Raw, zlib and gzip chunk decoding has a declared-output ceiling. Cached chunks
belong to the accepted revision; newly read chunks can never mix another revision.
No ordinary-UI lazy-open, remote immutable intake or full H08 qualification is implied.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import math
import os
import posixpath
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from env_worker.native_regions import DTYPES

MAX_CHUNK_BYTES = 64 << 20
MAX_CHUNKS_PER_READ = 64
MAX_METADATA_BYTES = 1 << 20
CACHE_BYTES = 64 << 20
CODECS = (None, "zlib", "gzip")


class NgffError(ValueError):
    """The dataset can't be read as declared; the message says why in the person's terms."""


class RemoteUnavailable(NgffError):
    pass


class Cancelled(NgffError):
    pass


# --- stores ----------------------------------------------------------------------------------------------------

def _clean_key(key: str) -> str:
    if type(key) is not str or not key or key.startswith("/") or "\\" in key or "\x00" in key:
        raise NgffError(f"Not a dataset key: {key!r}")
    norm = posixpath.normpath(key)
    if norm == ".." or norm.startswith("../") or posixpath.isabs(norm) or norm == ".":
        raise NgffError(f"A key may not leave the dataset: {key!r}")
    return norm


class LocalStore:
    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise NgffError("Choose the OME-Zarr folder itself")
        self.audit: list[dict] = []
        self._lock = threading.Lock()

    @property
    def location(self) -> str:
        return f"file:{self.root.name}"

    def get(self, key: str, limit: int = MAX_CHUNK_BYTES) -> bytes | None:
        key = _clean_key(key)
        from .bounded_files import open_directory, read_at
        folder = open_directory(self.root)
        try:
            for part in key.split('/')[:-1]:
                next_folder = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=folder)
                os.close(folder); folder = next_folder
            body = read_at(folder, key.split('/')[-1], limit)
        except FileNotFoundError:
            self._log(key, 'missing', 0)
            return None
        except (ValueError, OSError) as error:
            raise NgffError(f'{key} is outside the chosen folder, unsafe or over its read allowance') from error
        finally:
            os.close(folder)
        self._log(key, "ok", len(body), body)
        return body

    def _log(self, key, status, size, body=None):
        with self._lock:
            self.audit.append({"key": key, "status": status, "bytes": size,
                               "sha256": hashlib.sha256(body).hexdigest() if body is not None else None})


class SelectedStore:
    """Bounded keys from a folder accepted by the existing immutable Project intake.

    The complete index (including absent keys) and content-addressed file chunks belong
    to one accepted document revision. No network/path authority is passed to readers.
    """
    def __init__(self, projects, project, session, revision):
        from env_worker.source_directory import index
        saved = projects.read(project, session, revision=revision)
        if saved.document['kind'] != 'source-directory':
            raise NgffError('Choose an accepted scientific folder')
        reference = saved.document['state']['sourceFile']['reference']
        self.source = projects.file_source(project, session, revision, reference, verify_content=False)
        description = index(saved.document['state']['sourceDirectory'], self.source.size)
        self._entries = {row['path']: row for row in description['entries']}
        self.reference = self.source.reference
        self.revision = saved.digest
        self.audit = []
        self._lock = threading.Lock()

    @property
    def location(self):
        return self.source.name

    def get(self, key, limit=MAX_CHUNK_BYTES):
        from .scientific_files import CHUNK_BYTES
        key = _clean_key(key)
        row = self._entries.get(key)
        body = None
        if row is not None:
            if row['size'] > limit:
                raise NgffError(f'{key} exceeds its bounded read allowance')
            body = b''.join(self.source.read(row['offset'] + at, min(CHUNK_BYTES, row['size'] - at))
                            for at in range(0, row['size'], CHUNK_BYTES))
        with self._lock:
            self.audit.append({'key': key, 'status': 'missing' if body is None else 'ok',
                               'bytes': 0 if body is None else len(body),
                               'sha256': None if body is None else hashlib.sha256(body).hexdigest()})
        return body


@dataclass
class Broker:
    """What remote reads are allowed, and the credentials they carry, held on the parent side only.

    `grants` maps an origin ("https://host[:port]") to (path prefix, headers). Header values are never logged,
    returned or sent to the worker; `describe()` names only what is granted."""
    grants: dict = field(default_factory=dict)
    attempts: int = 4
    backoff_s: float = 0.25
    timeout_s: float = 10.0

    def grant(self, base_url: str, headers: dict | None = None) -> None:
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ("https", "http") or not parts.hostname:
            raise NgffError("Remote datasets need an http(s) address")
        if parts.scheme == "http" and parts.hostname not in ("127.0.0.1", "localhost", "::1"):
            raise NgffError("Plain http is allowed only for this computer; use https for remote data")
        if parts.username or parts.password or parts.query or parts.fragment:
            raise NgffError("Put credentials in the broker, not the address")
        origin = f"{parts.scheme}://{parts.netloc}"
        self.grants[origin] = (parts.path.rstrip("/") + "/", dict(headers or {}))

    def headers_for(self, url: str) -> dict:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self.grants or not parts.path.startswith(self.grants[origin][0]) or parts.query:
            raise NgffError(f"{origin}{parts.path} is not inside a granted dataset")
        return dict(self.grants[origin][1])

    def describe(self) -> list[str]:
        return [f"{origin}{prefix} ({'with credentials' if headers else 'public'})"
                for origin, (prefix, headers) in self.grants.items()]


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # A selected origin/path never delegates its grant or credentials to a redirect.
        return None


class RemoteStore:
    def __init__(self, base_url: str, broker: Broker, *, cancel: threading.Event | None = None, opener=None):
        self.base = base_url.rstrip("/") + "/"
        self.broker = broker
        broker.headers_for(self.base + ".zattrs")          # refuse an ungranted dataset before any request
        self.cancel = cancel or threading.Event()
        self.audit: list[dict] = []
        self._lock = threading.Lock()
        self._open = opener or urllib.request.build_opener(_NoRedirects()).open
        self.requests = 0

    @property
    def location(self) -> str:
        parts = urllib.parse.urlsplit(self.base)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"

    def get(self, key: str, limit: int = MAX_CHUNK_BYTES) -> bytes | None:
        key = _clean_key(key)
        url = self.base + urllib.parse.quote(key)
        headers = self.broker.headers_for(url)
        last = None
        for attempt in range(1, self.broker.attempts + 1):
            if self.cancel.is_set():
                self._log(key, "cancelled", 0, attempt, 0)
                raise Cancelled(f"Reading {key} was cancelled")
            started = time.monotonic()
            try:
                self.requests += 1
                with self._open(urllib.request.Request(url, headers=headers), timeout=self.broker.timeout_s) as reply:
                    declared = reply.headers.get("Content-Length")
                    if declared is not None and int(declared) > limit:
                        raise NgffError(f"{key} is {int(declared) >> 20} MiB, over the {limit >> 20} MiB a chunk may be")
                    body = reply.read(limit + 1)
                    if len(body) > limit:
                        raise NgffError(f"{key} is over the {limit >> 20} MiB a chunk may be")
                self._log(key, "ok", len(body), attempt, started, body)
                return body
            except urllib.error.HTTPError as error:
                if error.code == 404:
                    self._log(key, "missing", 0, attempt, started)
                    return None
                last = f"HTTP {error.code}"
                self._log(key, last, 0, attempt, started)
                if error.code in (401, 403):
                    raise RemoteUnavailable(f"The server refused {key} ({last}): check the dataset's access") from error
                if error.code < 500 and error.code != 429:
                    raise RemoteUnavailable(f"The server can't serve {key} ({last})") from error
            except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as error:
                if isinstance(error, NgffError):
                    raise
                last = type(getattr(error, "reason", error)).__name__
                self._log(key, f"failed: {last}", 0, attempt, started)
            if attempt < self.broker.attempts:
                if self.cancel.wait(self.broker.backoff_s * 2 ** (attempt - 1)):
                    self._log(key, "cancelled", 0, attempt, 0)
                    raise Cancelled(f"Reading {key} was cancelled")
        raise RemoteUnavailable(f"The dataset's server didn't answer for {key} after {self.broker.attempts} attempts "
                                f"({last}); nothing was changed, and reading can be retried")

    def _log(self, key, status, size, attempt, started, body=None):
        with self._lock:
            self.audit.append({"key": key, "status": status, "bytes": size, "attempt": attempt,
                               "ms": round((time.monotonic() - started) * 1000, 1) if started else 0,
                               "sha256": hashlib.sha256(body).hexdigest() if body is not None else None})


# --- arrays ----------------------------------------------------------------------------------------------------

@dataclass
class Level:
    store: object
    path: str
    shape: tuple[int, ...]
    chunks: tuple[int, ...]
    dtype: np.dtype
    codec: str | None
    fill: object
    separator: str
    scale: tuple[float, ...]
    translation: tuple[float, ...]
    cache: "ChunkLRU"

    @property
    def wire_dtype(self) -> str:
        return self.dtype.str if self.dtype.itemsize > 1 else "|" + self.dtype.str[1:]

    def _key(self, index) -> str:
        return posixpath.join(self.path, self.separator.join(str(i) for i in index)) if self.path else \
            self.separator.join(str(i) for i in index)

    def _chunk(self, index) -> np.ndarray:
        def load():
            body = self.store.get(self._key(index), MAX_CHUNK_BYTES)
            if body is None:
                return np.full(self.chunks, self.fill, dtype=self.dtype)
            expected = math.prod(self.chunks) * self.dtype.itemsize
            if self.codec:
                decoder = zlib.decompressobj(zlib.MAX_WBITS + (16 if self.codec == 'gzip' else 0))
                try:
                    raw = decoder.decompress(body, expected + 1)
                except zlib.error as error:
                    raise NgffError(f'Chunk {self._key(index)} has invalid compressed data') from error
                # Never flush without a ceiling; extra members/trailing data are not a zarr chunk.
                if len(raw) > expected or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
                    raise NgffError(f'Chunk {self._key(index)} exceeds its decoded allowance or is incomplete')
            else:
                raw = body
            if len(raw) != expected:
                raise NgffError(f"Chunk {self._key(index)} decodes to {len(raw)} bytes, not {expected}")
            chunk = np.frombuffer(raw, dtype=self.dtype).reshape(self.chunks)
            chunk.flags.writeable = False
            return chunk
        return self.cache.get((id(self), index), load)

    def read(self, slices) -> np.ndarray:
        """The region `slices` (step 1, inside the array), assembled from the chunks it touches."""
        box = [(s.start, s.stop) for s in slices]
        ranges = [range(a // c, -(-b // c)) if b > a else range(0) for (a, b), c in zip(box, self.chunks)]
        count = math.prod(len(r) for r in ranges)
        if count > MAX_CHUNKS_PER_READ:
            raise NgffError(f"This region touches {count} chunks; read smaller regions")
        out = np.empty([b - a for a, b in box], dtype=self.dtype)
        for index in itertools.product(*ranges):
            chunk = self._chunk(index)
            src, dst = [], []
            for (a, b), i, c in zip(box, index, self.chunks):
                lo, hi = max(a, i * c), min(b, (i + 1) * c)
                src.append(slice(lo - i * c, hi - i * c))
                dst.append(slice(lo - a, hi - a))
            out[tuple(dst)] = chunk[tuple(src)]
        return out


class ChunkLRU:
    def __init__(self, capacity: int = CACHE_BYTES):
        self.capacity, self._items, self._bytes = capacity, OrderedDict(), 0
        self._lock = threading.Lock()
        self.decoded = 0

    def get(self, key, load):
        with self._lock:
            if key in self._items:
                self._items.move_to_end(key)
                return self._items[key]
        value = load()
        with self._lock:
            self.decoded += 1
            self._items[key] = value
            self._bytes += value.nbytes
            while self._bytes > self.capacity and len(self._items) > 1:
                _, old = self._items.popitem(last=False)
                self._bytes -= old.nbytes
            return value


@dataclass
class Pyramid:
    levels: list[Level]
    axes: tuple[str, ...]
    units: tuple[str, ...]
    revision: str
    name: str
    location: str
    reference: dict

    def metadata(self, level=0):
        """Native layer metadata in the accepted NGFF coordinate system."""
        item = self.levels[level]
        origin = list(item.translation)
        basis = [origin, *[[value + (item.scale[axis] if i == axis else 0)
                           for i, value in enumerate(origin)] for axis in range(len(origin))]]
        if (not all(math.isfinite(v) for row in basis for v in row)
                or any(not math.isclose(basis[i+1][i] - origin[i], item.scale[i], rel_tol=1e-12, abs_tol=0)
                       for i in range(len(origin)))):
            raise NgffError('The NGFF translation/scale exceeds representable native coordinate precision')
        return {'format': 'native-layer-output-v1', 'shape': list(item.shape), 'dtype': item.wire_dtype,
                'name': self.name, 'type': 'image', 'visible': True, 'opacity': 1.,
                'geometry': {'basis': basis, 'axisLabels': list(self.axes), 'units': list(self.units)}}

    def sources(self, *, session: str | None = None, resource: str | None = None):
        """One native regional Source per level, finest first."""
        from .native_regions import Source
        from env_worker.scientific_output_wire import calibration
        out = []
        space = {'session': session or self.reference['session'], 'id': resource or self.reference['id'],
                 'revision': self.revision}
        for i, level in enumerate(self.levels):
            descriptor = {"reference": {"session": session or self.reference["session"], "id": resource or self.reference["id"], "revision": self.revision,
                                        "component": f"level-{i}"},
                          "dtype": level.dtype.name, "shape": list(level.shape), "axes": list(self.axes),
                          "semantic": "intensity", "calibration": calibration(self.metadata(i), self.axes, space)}
            out.append(Source(level.shape, level.wire_dtype, self.axes, descriptor, level.read, level.chunks))
        return out


def _json(store, key) -> dict:
    body = store.get(key, MAX_METADATA_BYTES)
    if body is None:
        raise NgffError(f"This isn't an OME-Zarr image: {key} is missing")
    try:
        value = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise NgffError(f"{key} isn't valid JSON") from error
    if type(value) is not dict:
        raise NgffError(f"{key} must be a JSON object")
    return value


def _array(store, path, scale, translation, cache) -> Level:
    meta = _json(store, posixpath.join(path, ".zarray") if path else ".zarray")
    if meta.get("zarr_format") != 2:
        raise NgffError("Only zarr format 2 arrays are supported here")
    if meta.get("order", "C") != "C" or meta.get("filters") not in (None, []):
        raise NgffError("Only C-ordered arrays without filters are supported here")
    try:
        dtype = np.dtype(meta["dtype"])
    except (KeyError, TypeError) as error:
        raise NgffError("The array declares no supported dtype") from error
    wire = dtype.str if dtype.itemsize > 1 else "|" + dtype.str[1:]
    if wire not in DTYPES:
        raise NgffError(f"The dtype {meta['dtype']} isn't a supported scientific scalar type")
    compressor = meta.get("compressor")
    codec = None if compressor is None else compressor.get("id") if type(compressor) is dict else "?"
    if codec not in CODECS:
        raise NgffError(f"The {codec} codec isn't supported here (raw, zlib and gzip are); "
                        "convert the dataset or use a reader that brings its own codecs")
    shape, chunks = tuple(meta.get("shape") or ()), tuple(meta.get("chunks") or ())
    if (not shape or len(shape) != len(chunks) or any(type(n) is not int or n < 0 for n in shape)
            or any(type(c) is not int or c < 1 for c in chunks)):
        raise NgffError("The array's shape and chunks don't agree")
    if math.prod(chunks) * dtype.itemsize > MAX_CHUNK_BYTES:
        raise NgffError(f"Its chunks are {math.prod(chunks) * dtype.itemsize >> 20} MiB; at most "
                        f"{MAX_CHUNK_BYTES >> 20} MiB is supported")
    return Level(store, path, shape, chunks, dtype, codec, meta.get("fill_value") or 0,
                 meta.get("dimension_separator", "."), scale, translation, cache)


def _transform(dataset, root, dimensions):
    """NGFF dataset transforms, then root transforms, in their declared order.

    Match the standalone bounded reader's scale/translation composition. Other
    transform types need an explicit implementation; they never become identity.
    """
    scale, translation = [1.] * dimensions, [0.] * dimensions
    if any(type(chain) is not list or len(chain) > 32 for chain in (dataset, root)):
        raise NgffError('Choose an NGFF image with at most 32 explicit transforms per chain')
    for transform in [*dataset, *root]:
        kind = transform.get('type') if type(transform) is dict else None
        if kind not in ('scale', 'translation') or set(transform) != {'type', kind}:
            raise NgffError('This reader supports only explicit NGFF scale and translation transforms')
        values = transform[kind]
        try:
            valid = (type(values) is list and len(values) == dimensions
                     and all(type(v) in (int, float) and math.isfinite(v)
                             and (kind != 'scale' or v > 0) for v in values))
        except OverflowError:
            valid = False
        if not valid:
            raise NgffError(f'Choose an NGFF image with a finite, dimension-matched {kind} transform')
        if kind == 'scale':
            scale = [a*b for a, b in zip(scale, values)]
            translation = [a*b for a, b in zip(translation, values)]
        else:
            translation = [a+b for a, b in zip(translation, values)]
        if not all(math.isfinite(v) for v in [*scale, *translation]) or any(v <= 0 for v in scale):
            raise NgffError('The composed NGFF transform exceeds finite coordinate bounds')
    return tuple(scale), tuple(translation)


def open_multiscale(store, *, image: str = "", cache_bytes: int = CACHE_BYTES) -> Pyramid:
    """The first multiscale image of an NGFF 0.4 group (`image` is its path inside the store)."""
    if not isinstance(store, SelectedStore):
        raise NgffError('Choose an accepted immutable folder; raw local/remote metadata cannot identify pixel content')
    attrs = _json(store, posixpath.join(image, ".zattrs") if image else ".zattrs")
    scales = attrs.get("multiscales")
    if type(scales) is not list or not scales or type(scales[0]) is not dict:
        raise NgffError("This group has no NGFF multiscales")
    ms = scales[0]
    if ms.get("version") not in ("0.4",):
        raise NgffError(f"NGFF {ms.get('version')!r} isn't supported here (0.4 is)")
    axes_meta = ms.get("axes") or []
    axes = tuple(a.get("name") if type(a) is dict else a for a in axes_meta)
    units = tuple((a.get("unit") or "pixel") if type(a) is dict else "pixel" for a in axes_meta)
    datasets = ms.get("datasets") or []
    if not datasets:
        raise NgffError("The multiscale image lists no levels")
    cache = ChunkLRU(cache_bytes)
    levels, texts = [], [store.revision, image, json.dumps(attrs, sort_keys=True)]
    for d in datasets:
        path = posixpath.join(image, d["path"]) if image else d["path"]
        scale, translation = _transform(d.get('coordinateTransformations', []),
                                        ms.get('coordinateTransformations', []), len(axes))
        level = _array(store, path, scale, translation, cache)
        if len(level.shape) != len(axes):
            raise NgffError("A level's dimensions don't match the declared axes")
        levels.append(level)
        texts.append(json.dumps(_json(store, posixpath.join(path, ".zarray")), sort_keys=True))
    for finer, coarser in zip(levels, levels[1:]):
        if any(c > f for c, f in zip(coarser.shape, finer.shape)) or coarser.dtype != finer.dtype:
            raise NgffError("Levels must go from finest to coarsest with one dtype")
    # The existing native multiscale renderer derives level coordinates from shape
    # ratios. Refuse transforms it cannot represent instead of silently realigning data.
    finest = levels[0]
    for level in levels[1:]:
        if (level.translation != finest.translation or any(n <= 0 for n in level.shape)
                or any(not math.isclose(s, first * full / small, rel_tol=1e-12, abs_tol=0)
                       for s, first, full, small in zip(level.scale, finest.scale, finest.shape, level.shape))):
            raise NgffError('These level transforms cannot be represented by the current multiscale view; '
                            'open each calibrated level through the ordinary reader instead')
    revision = hashlib.sha256("\n".join(texts).encode()).hexdigest()
    return Pyramid(levels, axes, units, revision, ms.get("name") or "image", store.location, store.reference)
