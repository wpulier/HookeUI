"""Saved science → original pixels, exclusive labels and precise measurements; no operation rerun.

Assemble and validate the whole download privately before returning any byte. Display
rounding and display overlap arbitration are never part of scientific exchange.
"""
from __future__ import annotations
import asyncio
import copy
import csv
import hashlib
import io
import json
import math
import zipfile
from pathlib import Path
import cv2
import numpy as np
from .evidence import Evidence, evidence_of
from .capture import captures
from .brain.registry import ToolFailed
from .source_images import MAX_BYTES, _shape

SCHEMA = 'micromind-particle-exchange/1'
FIELDS = ['memberID', 'label', 'areaPx2', 'centroidX', 'centroidY', 'meanIntensity']
DEFINITIONS = {
    'areaPx2': {'definition': 'Occupied pixel count in labels.tif', 'unit': 'px²'},
    'centroidX': {'definition': 'Mean column index + 0.5 over occupied pixels; origin at upper-left image corner', 'unit': 'px'},
    'centroidY': {'definition': 'Mean row index + 0.5 over occupied pixels; origin at upper-left image corner', 'unit': 'px'},
    'meanIntensity': {'definition': 'Arithmetic mean of unchanged 8-bit grayscale source pixels in the label', 'unit': 'intensity (0–255)'},
}
MAX_PIXELS = 16 * 1024 * 1024

class ExchangeRefused(ValueError):
    """The requested retained result cannot be faithfully exchanged."""

def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def source_pixels(data: bytes, source: dict) -> np.ndarray:
    if len(data) > MAX_BYTES:
        raise ExchangeRefused('the source exceeds the supported image size')
    w, h, channels, media = _shape(data)
    if channels != 1 or w * h > MAX_PIXELS:
        raise ExchangeRefused('exchange currently requires a single-plane 8-bit grayscale source within 16 megapixels')
    image = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    if image is None or image.shape != (h, w) or image.dtype != np.uint8:
        raise ExchangeRefused('the source cannot be decoded without conversion')
    if (source.get('width'), source.get('height'), source.get('dtype'), source.get('axes'), source.get('channels')) != (w, h, 'uint8', 'YX', 1):
        raise ExchangeRefused('source dimensions, axes or type disagree with the retained record')
    if source.get('sha256') != digest(data) or source.get('pixelSha256') != digest(image.tobytes()):
        raise ExchangeRefused('source bytes or decoded pixels changed')
    if source.get('mediaType') != media or source.get('units') != 'pixels':
        raise ExchangeRefused('source format or pixel units disagree with the retained record')
    return image

def measurements(labels: np.ndarray, gray: np.ndarray) -> dict[int, tuple]:
    """Full precision raster reductions, with pixel-center coordinates (not polygon statistics)."""
    y, x = np.nonzero(labels)
    values = labels[y, x].astype(np.int64)
    counts = np.bincount(values)
    xs = np.bincount(values, weights=x.astype(np.float64) + .5)
    ys = np.bincount(values, weights=y.astype(np.float64) + .5)
    intensities = np.bincount(values, weights=gray[y, x].astype(np.float64))
    return {int(n): (int(counts[n]), float(xs[n] / counts[n]), float(ys[n] / counts[n]), float(intensities[n] / counts[n]))
            for n in np.flatnonzero(counts) if n != 0}

def validate(files: dict[str, bytes]) -> dict:
    """Validate decoded bundle contents, by label/ID rather than CSV row order."""
    try:
        manifest = json.loads(files['manifest.json'])
        if (any(not isinstance(manifest.get(k), str) or not manifest[k] for k in ('session', 'evidence', 'execution'))
                or type(manifest.get('revision')) is not int or manifest['revision'] < 0
                or not isinstance(manifest.get('producer'), dict) or not manifest['producer']):
            raise ExchangeRefused('the exchange lacks its session, evidence, revision or execution provenance')
        name = manifest['sourceFile']
        if name not in ('source.png', 'source.jpg') or set(files) != {name, 'labels.tif', 'measurements.csv', 'manifest.json'}:
            raise ExchangeRefused('the exchange bundle has missing or unexpected files')
        if manifest['schema'] != SCHEMA or manifest['definitions'] != DEFINITIONS:
            raise ExchangeRefused('unsupported exchange schema or measurement definitions')
        if set(manifest['files']) != {name, 'labels.tif', 'measurements.csv'}:
            raise ExchangeRefused('the exchange manifest omits a file')
        for path, expected in manifest['files'].items():
            if digest(files[path]) != expected:
                raise ExchangeRefused(f'{path} is missing or changed')
        gray = source_pixels(files[name], manifest['source'])
        decoded, planes = cv2.imdecodemulti(np.frombuffer(files['labels.tif'], np.uint8), cv2.IMREAD_UNCHANGED)
        if not decoded or len(planes) != 1:
            raise ExchangeRefused('labels.tif must contain exactly one single YX plane')
        labels = planes[0]
        if labels is None or labels.dtype != np.uint16 or labels.shape != gray.shape:
            raise ExchangeRefused('labels.tif must be uint16 on the unchanged source grid')
        if manifest['labels'] != {'dtype': 'uint16', 'axes': 'YX', 'background': 0, 'units': 'pixels',
                                  'pixelSha256': digest(labels.astype('<u2').tobytes())}:
            raise ExchangeRefused('label pixels, type or units disagree with the manifest')
        mapping = manifest['members']
        numbers, ids = [m['label'] for m in mapping], [m['memberID'] for m in mapping]
        if (len(ids) != len(set(ids)) or any(not isinstance(mid, str) or not mid for mid in ids)
                or any(type(n) is not int or not 1 <= n <= 65535 for n in numbers) or len(numbers) != len(set(numbers))):
            raise ExchangeRefused('labels and member IDs must map one-to-one')
        actual = measurements(labels, gray)
        if set(actual) != set(numbers):
            raise ExchangeRefused('the label image has missing or unexpected members')
        reader = csv.DictReader(io.StringIO(files['measurements.csv'].decode('utf-8')))
        if reader.fieldnames != FIELDS:
            raise ExchangeRefused('the measurement columns are unsupported')
        rows = list(reader)
        if len(rows) != len(mapping):
            raise ExchangeRefused('the measurement row count disagrees with the labels')
        seen, by_label = set(), dict(zip(numbers, ids))
        for row in rows:
            n = int(row['label'])
            if n in seen or n not in actual or row['memberID'] != by_label[n]:
                raise ExchangeRefused('measurement labels or IDs are missing, duplicated or inconsistent')
            seen.add(n)
            area, cx, cy, mean = actual[n]
            if int(row['areaPx2']) != area:
                raise ExchangeRefused('measurement area disagrees with the label pixels')
            for field, expected in zip(FIELDS[3:], (cx, cy, mean)):
                value = float(row[field])
                if not math.isfinite(value) or abs(value - expected) > 1e-6:
                    raise ExchangeRefused(f'{field} disagrees with the unchanged source and labels')
        return manifest
    except (KeyError, TypeError, ValueError, cv2.error, UnicodeError) as exc:
        if isinstance(exc, ExchangeRefused):
            raise
        raise ExchangeRefused(f'the exchange is inconsistent: {exc}') from exc

def mask_arrays(body: bytes, members: list[str], pixels: int):
    try:
        return _mask_arrays(body, members, pixels)
    except (zipfile.BadZipFile, KeyError, EOFError) as exc:
        raise ExchangeRefused('The retained mask archive is missing, truncated or malformed') from exc


def _mask_arrays(body: bytes, members: list[str], pixels: int):
    """Decode only bounded retained mask arrays after their exact headers pass preflight."""
    from .typed_files import _header, MAX_HEADER
    arrays = {}
    total = 0
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = archive.infolist()
        if len(entries) > 131070 or len({item.filename for item in entries}) != len(entries):
            raise ExchangeRefused('The mask archive has duplicate or excessive entries')
        for mid in members:
            for kind in ('meta', 'bits'):
                name = f'{mid}.{kind}'
                item = archive.getinfo(name + '.npy')
                limit = (40 if kind == 'meta' else (pixels + 7)//8) + MAX_HEADER + 12
                total += item.file_size
                if item.file_size > limit or total > MAX_BYTES:
                    raise ExchangeRefused('The mask archive exceeds its decoded byte allowance')
                with archive.open(item) as stream:
                    body = stream.read(limit + 1)
                if len(body) != item.file_size or len(body) > limit:
                    raise ExchangeRefused('The mask archive payload changed or exceeds its allowance')
                shape, dtype, _, _ = _header(io.BytesIO(body), len(body))
                if ((kind == 'meta' and (shape != (5,) or dtype != np.dtype('float64')))
                        or (kind == 'bits' and (len(shape) != 1 or dtype != np.dtype('uint8') or shape[0] > (pixels+7)//8))):
                    raise ExchangeRefused('The mask archive has an unsupported native layout')
                arrays[name] = body
    return {name: np.load(io.BytesIO(body), allow_pickle=False, max_header_size=MAX_HEADER)
            for name, body in arrays.items()}


def snapshot(folder: Path, expected: dict, *, preserve_instances=False):
    """Checked original grid and masks for exchange or native acceptance; no preview conversion."""
    record = Evidence.checked_manifest(folder, max_bytes=MAX_BYTES)
    if any(record.get(key) != value for key, value in expected.items()):
        raise ExchangeRefused('the saved population changed; reopen its current revision')
    source = record['view'].get('sourceImage')
    if not source:
        raise ExchangeRefused('this result has no retained original imported image')
    raw = Evidence.read_retained(folder / 'source.image', MAX_BYTES)
    gray = source_pixels(raw, source)
    active = [m for m in record['members'] if m['status'] not in ('deleted', 'merged')]
    if len(active) > 65535 or len({m['id'] for m in active}) != len(active):
        raise ExchangeRefused('the members cannot be represented by unique uint16 labels')
    labels, mapping = np.zeros(gray.shape, np.uint16), []
    masks_bytes = Evidence.read_retained(folder / 'masks.npz', MAX_BYTES)
    if digest(masks_bytes) != record['files']['masks.npz']:
        raise ExchangeRefused('the retained masks changed during export')
    masks = mask_arrays(masks_bytes, [m['id'] for m in active], gray.size)
    instances = set()
    for n, member in enumerate(active, 1):
        mid = member['id']
        if preserve_instances:
            n = member.get('instance')
            if type(n) is not int or not 1 <= n <= 65535 or n in instances:
                raise ExchangeRefused('Native labels require unique positive uint16 instance IDs; convert explicitly first')
            instances.add(n)
        if f'{mid}.meta' not in masks or f'{mid}.bits' not in masks:
            raise ExchangeRefused(f'{mid} has no retained mask')
        meta, packed = masks[f'{mid}.meta'], masks[f'{mid}.bits']
        if meta.shape != (5,) or not np.all(np.isfinite(meta)) or meta[4] != 1 or np.any(meta[:4] != np.floor(meta[:4])):
            raise ExchangeRefused(f'{mid} has a scaled or noninteger mask grid')
        h, w, x, y = (int(v) for v in meta[:4])
        if min(h, w) < 1 or min(x, y) < 0 or y + h > gray.shape[0] or x + w > gray.shape[1]:
            raise ExchangeRefused(f'{mid} has an out-of-bounds mask')
        if packed.dtype != np.uint8 or packed.ndim != 1 or packed.size != (h * w + 7) // 8:
            raise ExchangeRefused(f'{mid} has an incomplete mask')
        bits = np.unpackbits(packed)[:h*w].reshape(h, w).astype(bool)
        target = labels[y:y+h, x:x+w]
        if not bits.any() or np.any(target[bits]):
            raise ExchangeRefused(f'{mid} is empty or overlaps another member; an exclusive label TIFF cannot represent it')
        target[bits] = n
        mapping.append({'memberID': mid, 'label': n})
    if Evidence.checked_manifest(folder, max_bytes=MAX_BYTES) != record:
        raise ExchangeRefused('The saved population changed while its native values were read')
    return record, raw, gray, labels, mapping


def build(folder: Path, expected: dict) -> bytes:
    """Read persisted science; a thread may finish after cancellation but publishes nothing."""
    record, raw, gray, labels, mapping = snapshot(folder, expected)
    source = record['view']['sourceImage']
    rows, table = measurements(labels, gray), io.StringIO(newline='')
    writer = csv.writer(table, lineterminator='\n')
    writer.writerow(FIELDS)
    for item in mapping:
        area, *values = rows[item['label']]
        writer.writerow([item['memberID'], item['label'], area, *(format(v, '.17g') for v in values)])
    ok, encoded = cv2.imencode('.tif', labels, [cv2.IMWRITE_TIFF_COMPRESSION, 1])
    if not ok:
        raise ExchangeRefused('the label TIFF could not be written')
    source_name = 'source.png' if source['mediaType'] == 'image/png' else 'source.jpg'
    files = {source_name: raw, 'labels.tif': encoded.tobytes(), 'measurements.csv': table.getvalue().encode()}
    manifest = {'schema': SCHEMA, 'session': record['session'], 'evidence': record['id'], 'revision': record['revision'],
                'execution': record['execution'], 'capture': record['capture'], 'producer': record['producer'],
                'revisions': record['revisions'], 'transformations': record['transformations'],
                'source': source, 'sourceFile': source_name, 'members': mapping, 'definitions': DEFINITIONS,
                'labels': {'dtype': 'uint16', 'axes': 'YX', 'background': 0, 'units': 'pixels',
                           'pixelSha256': digest(labels.astype('<u2').tobytes())},
                'files': {name: digest(data) for name, data in files.items()}}
    files['manifest.json'] = json.dumps(manifest, ensure_ascii=False, indent=2, allow_nan=False).encode()
    validate(files)
    if Evidence.checked_manifest(folder, max_bytes=MAX_BYTES) != record:
        raise ExchangeRefused('the saved population changed during export')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()

def build_reserved(folder: Path, expected: dict, memory) -> bytes:
    w, h = expected['view'].get('width'), expected['view'].get('height')
    if type(w) is not int or type(h) is not int or min(w, h) < 1 or w * h > MAX_PIXELS:
        raise ExchangeRefused('the retained image exceeds the supported exchange grid')
    # Keep the reservation on the worker thread until it ends, even if the download is cancelled.
    with memory.reserve('analysis', w * h * 96 + MAX_BYTES * 4, 'exporting retained masks and measurements'):
        return build(folder, expected)

async def export(app, eid: str, session: str, revision: int) -> bytes:
    store, result = evidence_of(app.hub), evidence_of(app.hub).get(eid)
    def current():
        if (app.day.sid != session or result is None or store.get(eid) is not result or result.session != session
                or result.revision != revision or result.status != 'saved' or result.path is None):
            raise ExchangeRefused('the saved session or revision changed; reopen the result and export again')
    current()
    expected = copy.deepcopy({key: value for key, value in store.manifest(result, {}).items()
                              if key in ('id', 'session', 'revision', 'execution', 'producer', 'view', 'members', 'revisions')})
    try:
        data = await asyncio.to_thread(build_reserved, result.path, expected, captures(app.hub).memory)
    except (OSError, ValueError, KeyError, TypeError, cv2.error, ToolFailed) as exc:
        raise ExchangeRefused(f'Export unavailable: {exc}') from exc
    current()
    return data
