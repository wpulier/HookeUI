"""An entry's own adapter, run in its own environment (orchestrator/env_runner.py; phase 3's E4). Started with that
environment's Python and a clean environment; speaks micro-sam's wire (a JSON header and a payload, each
length-prefixed) on stdin and stdout.

    {"op": "run", "shape": [H, W, 3], "params": {...}} + the picture (uint8 RGB)
        -> {"ok": true, "kind": "labels", "shape": [H, W]} + an int32 label map
        or {"ok": true, "kind": "polygons", "polygons": [[[x, y], ...], ...]}

A new kind of job (orchestrator/kinds.py) says its declared output in the request ("output": "image.2d.rgb8",
"table.objects", "text", "points.2d" or "tracks.graph", the last given frames: "shape" [n, H, W, 3] and "params" its
"times"), and the reply is that output, checked here (tracks: {"kind": "tracks"} + JSON [{"points": [[i, x, y]]}]): {"kind": "picture", "shape": [h, w,
3]} + its uint8 RGB bytes, {"kind": "table"} + JSON {"columns", "rows"}, {"kind": "text"} + UTF-8, {"kind": "points"} +
JSON [[x, y], ...]. The limits match kinds.py's (this file imports nothing of the app).

Catalog format 2 (ADR 0024): any other output type is a JSON value ({"kind": "data"} + JSON, at most 1 MB); several
outputs ("outputs": [{"id", "type"}, ...]) are a dict keyed by output id, replied as {"kind": "multi", "parts": [{"id",
"reply", "bytes"}]} + each part's bytes in order; and {"unsupported": "why"} (that one key) is the adapter refusing this
input, replied as {"kind": "unsupported", "why"}.

Typed inputs (H01 slice d): {"op": "run", "inputs": [{"id", "type", "shape", "dtype"}, ...], ...} + each input's exact
bytes in order; the adapter is called as `function({id: array}, params)` with read-only arrays of exactly those dtypes.

The adapter is `module:function` in the entry's own folder (argv[1], argv[2]): `function(picture, params)` returns a
label map (an array the picture's height and width, 0 for background) or {"polygons": [...]}. Nothing else of the app is
importable here; the network is refused when the app's proof asks (MICROMIND_REFUSE_NETWORK).
"""
from __future__ import annotations

import importlib
import json
import os
import struct
import sys


def write_message(stream, header: dict, payload=b"", *, compact=False) -> None:
    """`payload`: bytes or any contiguous buffer (a picture's memoryview), written as it is, never copied first."""
    data = (json.dumps(header, ensure_ascii=False, allow_nan=False, separators=(',', ':'))
            if compact else json.dumps(header)).encode()
    payload = memoryview(payload).cast("B")
    stream.write(struct.pack(">I", len(data)) + data + struct.pack(">I", payload.nbytes))
    stream.write(payload)
    stream.flush()


def _read_exact(stream, n: int) -> bytes:
    chunks, got = [], 0
    while got < n:
        chunk = stream.read(n - got)
        if not chunk:
            raise EOFError("closed")
        chunks.append(chunk)
        got += len(chunk)
    return b"".join(chunks)


def read_message(stream, max_header: int | None = None, max_payload: int | None = None, admit=None) -> tuple[dict, bytes]:
    """One message. With limits (the app reading a worker that runs code nobody reviewed), a length past them is refused
    before anything of it is read."""
    (n,) = struct.unpack(">I", _read_exact(stream, 4))
    if max_header is not None and n > max_header:
        raise ValueError(f"its reply's header is {n} bytes, past the {max_header} allowed")
    try:
        header = json.loads(_read_exact(stream, n))
    except RecursionError as exc:
        raise ValueError('The worker message header is too deeply nested') from exc
    (m,) = struct.unpack(">I", _read_exact(stream, 4))
    if max_payload is not None and m > max_payload:
        raise ValueError(f"its reply is {m} bytes, past the {max_payload} allowed")
    if admit is not None:
        admit(header, m)  # the parent reserves the decoded/encoded peak before reading any payload
    return header, _read_exact(stream, m) if m else b""


def _refuse_network() -> None:
    if os.environ.get("MICROMIND_REFUSE_NETWORK") != "1":
        return
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("micromind_offline", Path(__file__).resolve().parent.parent / "gpu_worker" / "offline.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.refuse()


MAX_TEXT, MAX_ROWS, MAX_COLUMNS, MAX_POINTS, MAX_SIDE, MAX_CELL = 20_000, 10_000, 64, 10_000, 4096, 500


def _picture(got, picture):
    import numpy as np

    arr = np.asarray(got.get("picture") if isinstance(got, dict) else got)
    if arr.ndim == 2:
        arr = np.repeat(arr[:, :, None], 3, axis=2)
    if arr.ndim != 3 or arr.shape[2] not in (3, 4) or arr.dtype != np.uint8:
        raise ValueError("a picture is 8-bit RGB: a (height, width, 3) uint8 array")
    if min(arr.shape[:2]) < 1 or max(arr.shape[:2]) > MAX_SIDE:
        raise ValueError(f"a picture is 1 to {MAX_SIDE} px a side, not {arr.shape[1]}x{arr.shape[0]}")
    arr = np.ascontiguousarray(arr[:, :, :3])
    return {"kind": "picture", "shape": list(arr.shape)}, arr.tobytes()


def _cell(value):
    import math

    value = value.item() if hasattr(value, "item") else value
    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("a table's numbers are finite")
        return value
    if isinstance(value, str) and len(value) <= MAX_CELL:
        return value
    raise ValueError(f"a table's cells are numbers, words (at most {MAX_CELL} characters), true/false or empty")


def _table(got, picture):
    if isinstance(got, list):
        columns = list(dict.fromkeys(k for row in got if isinstance(row, dict) for k in row))
        rows = [[row.get(c) for c in columns] for row in got if isinstance(row, dict)]
    elif isinstance(got, dict) and isinstance(got.get("columns"), list) and isinstance(got.get("rows"), list):
        columns, rows = got["columns"], got["rows"]
    else:
        raise ValueError('a table is {"columns": [...], "rows": [[...], ...]} or a list of dicts')
    named = []
    for c in columns:
        c = {"name": c} if isinstance(c, str) else c
        if not isinstance(c, dict) or not isinstance(c.get("name"), str) or not 0 < len(c["name"]) <= 80:
            raise ValueError("each column has a name (at most 80 characters), and may have a unit and a definition")
        named.append({k: str(c[k])[:200] for k in ("name", "unit", "definition") if c.get(k)})
    if not named or len(named) > MAX_COLUMNS or len(rows) > MAX_ROWS:
        raise ValueError(f"a table has 1 to {MAX_COLUMNS} columns and at most {MAX_ROWS} rows")
    if any(not isinstance(r, (list, tuple)) or len(r) != len(named) for r in rows):
        raise ValueError("each row has one cell per column")
    return {"kind": "table"}, json.dumps({"columns": named, "rows": [[_cell(v) for v in r] for r in rows]}).encode()


def _text(got, picture):
    text = got.get("text") if isinstance(got, dict) else got
    if not isinstance(text, str) or len(text) > MAX_TEXT:
        raise ValueError(f"words are a string of at most {MAX_TEXT} characters")
    return {"kind": "text"}, text.encode()


def _points(got, picture):
    import math

    import numpy as np

    raw = got.get("points") if isinstance(got, dict) else got
    points = np.asarray(raw, dtype=float).tolist() if raw is not None and len(raw) else []
    h, w = picture.shape[:2]
    if len(points) > MAX_POINTS or any(len(p) != 2 or not all(math.isfinite(v) for v in p) for p in points):
        raise ValueError(f"points are at most {MAX_POINTS} [x, y] pairs")
    if any(not (0 <= x <= w and 0 <= y <= h) for x, y in points):
        raise ValueError("its points lie outside the picture")
    return {"kind": "points"}, json.dumps(points).encode()


def _tracks(got, frames):
    """Tracks over the frames it was given (frames: n, h, w, 3): each a list of [frame index, x, y], indices rising."""
    import math

    raw = got.get("tracks") if isinstance(got, dict) else got
    if not isinstance(raw, list) or len(raw) > 500:
        raise ValueError('tracks are {"tracks": [{"points": [[frame, x, y], ...]}, ...]}, at most 500')
    n, h, w = frames.shape[0], frames.shape[1], frames.shape[2]
    out = []
    for track in raw:
        points = track.get("points") if isinstance(track, dict) else track
        if not isinstance(points, list) or not 0 < len(points) <= n:
            raise ValueError("each track has one to as many points as there are frames")
        clean, last = [], -1
        for p in points:
            if len(p) != 3 or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in p):
                raise ValueError("a track's point is [frame, x, y]")
            i, x, y = int(p[0]), float(p[1]), float(p[2])
            if i != p[0] or not last < i < n or not (0 <= x <= w and 0 <= y <= h):
                raise ValueError("a track's frames rise within the frames it was given, its points on them")
            clean.append([i, x, y])
            last = i
        out.append({"points": clean})
    return {"kind": "tracks"}, json.dumps(out).encode()


KINDS = {"image.2d.rgb8": _picture, "table.objects": _table, "text": _text, "points.2d": _points,
         "tracks.graph": _tracks}
MAX_DATA = 1 << 20  # a value of a type the app has no place for (types are open: catalog format 2), as JSON


def _data(got, picture):
    """Any other output type (catalog format 2's open types, as ComfyUI's): its value as JSON, kept and handed on as
    data by the app."""
    try:
        body = json.dumps(got, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o), allow_nan=False).encode()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"a value of its own type is JSON (a dict, a list, words or numbers): {exc}") from exc
    if len(body) > MAX_DATA:
        raise ValueError(f"a value of its own type is at most {MAX_DATA} bytes as JSON")
    return {"kind": "data"}, body


def _labels(got, picture):
    """Outlines: a label map the picture's size (0 the background), or {"polygons": [...]}."""
    import numpy as np

    if isinstance(got, dict) and "polygons" in got:
        return {"kind": "polygons", "polygons": got["polygons"]}, b""
    labels = np.asarray(got)
    if labels.shape != picture.shape[:2]:
        raise ValueError(f"its label map is {labels.shape}, not the picture's {picture.shape[:2]}")
    if labels.dtype.kind not in "iu" or labels.min(initial=0) < 0 or labels.max(initial=0) > np.iinfo(np.int32).max:
        raise ValueError("its labels must be nonnegative int32 instance numbers (zero is background)")
    labels = np.ascontiguousarray(labels, np.int32)
    return {"kind": "labels", "shape": list(labels.shape)}, labels.tobytes()


def _one(output: str, got, picture):
    return (KINDS.get(output) or (_labels if output in ("labels.instance.2d", "masks.2d") else _data))(got, picture)


def _unsupported(got) -> str | None:
    """Its refusal of this input ({"unsupported": "why"}, that one key), or None."""
    if isinstance(got, dict) and set(got) == {"unsupported"}:
        return str(got["unsupported"])[:300] or "it does not take this input"
    return None


def _typed_inputs(specs, payload):
    """Typed inputs: [{id, type, shape, dtype}] and their bytes in order, each a read-only array of exactly that dtype.
    Nothing is converted: a grey or 16-bit input stays what it is (no RGB expansion)."""
    import numpy as np
    if not isinstance(specs, list) or not 1 <= len(specs) <= 16:
        raise ValueError('Typed inputs need one to sixteen declared ports')
    inputs, at, view = {}, 0, memoryview(payload)
    for spec in specs:
        if (not isinstance(spec, dict) or set(spec) != {'id', 'type', 'shape', 'dtype'} or not isinstance(spec['id'], str)
                or spec['id'] in inputs or not isinstance(spec['shape'], list)
                or not all(isinstance(n, int) and n > 0 for n in spec['shape'])):
            raise ValueError('Every typed input needs its id, type, shape and dtype')
        dtype = np.dtype(spec['dtype'])
        size = int(np.prod(spec['shape'])) * dtype.itemsize
        if at + size > len(view):
            raise ValueError('The typed inputs are shorter than their declared shapes')
        array = np.frombuffer(view[at:at + size], dtype).reshape(spec['shape'])
        array.setflags(write=False)
        inputs[spec['id']] = array
        at += size
    if at != len(view):
        raise ValueError('The typed inputs are longer than their declared shapes')
    return inputs


def main() -> None:
    _refuse_network()
    import importlib.util
    from pathlib import Path
    from types import SimpleNamespace

    spec = importlib.util.spec_from_file_location('observationui_region_reader', Path(__file__).with_name('region_reader.py'))
    regions = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = regions
    spec.loader.exec_module(regions)
    folder, target = sys.argv[1], sys.argv[2]
    out = os.fdopen(os.dup(sys.stdout.fileno()), "wb")
    os.dup2(sys.stderr.fileno(), sys.stdout.fileno())  # anything the adapter prints goes to stderr
    sys.path.insert(0, folder)
    module_name, _, function_name = target.partition(":")
    function = getattr(importlib.import_module(module_name), function_name)
    import numpy as np

    write_message(out, {"ok": True, "ready": target})
    stdin = sys.stdin.buffer
    while True:
        try:
            header, payload = read_message(stdin)
        except EOFError:
            return
        try:
            if header.get("op") != "run":
                raise ValueError(f"unknown op {header.get('op')}")
            semantic = 'scientific' in header
            if 'regionInputs' in header:
                if not semantic or payload:
                    raise ValueError('Regional access needs scientific metadata and no inline pixels')
                def request_region(request):
                    write_message(out, request)
                    return read_message(stdin, max_header=4096, max_payload=regions.MAX_REGION_BYTES)
                resources = regions.bind(header['regionInputs'], request_region)
                picture = SimpleNamespace(shape=tuple(header['shape']))
                got = function(resources, header.get('params') or {})
            elif 'inputs' in header:  # typed inputs (H01 slice d): each port's native array, exactly as bound
                inputs = _typed_inputs(header['inputs'], payload)
                if (len(header.get("outputs") or []) > 1
                        or header.get("output") in ("image.2d.rgb8", "points.2d", "labels.instance.2d", "masks.2d")):
                    raise ValueError("typed inputs give one table, text or data result")
                picture = None  # no typed result is bounded by an input's size: none is a picture, points or labels
                got = function(inputs, header.get("params") or {})
            else:
                picture = np.frombuffer(payload, np.uint8).reshape(header["shape"])
                got = (function(picture, header.get("params") or {}, header['scientific']) if semantic
                       else function(picture, header.get("params") or {}))
            refused = _unsupported(got)
            descriptor = None
            if semantic and refused is None:
                if not isinstance(got, dict) or set(got) != {'value', 'scientific'}:
                    raise ValueError('A scientific adapter returns exactly value and scientific metadata')
                descriptor, got = got['scientific'], got['value']
                if not isinstance(descriptor, dict) or len(json.dumps(descriptor, allow_nan=False).encode()) > 128 << 10:
                    raise ValueError('Scientific output metadata must be an object of at most 128 KiB')
                if header.get('output') == 'image.2d.rgb8':
                    array = np.asarray(got.get('picture') if isinstance(got, dict) else got)
                    if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] != 3:
                        raise ValueError('Scientific pictures must be exactly RGB8; grayscale expansion and alpha removal require explicit conversion.')
            outputs = header.get("outputs") or []
            if refused is not None:  # it refused this input, in words: unsupported, never a failure
                write_message(out, {"ok": True, "kind": "unsupported", "why": refused})
            elif len(outputs) > 1:  # several outputs: a dict keyed by each output's id, each checked as its type
                if not isinstance(got, dict) or set(got) != {o["id"] for o in outputs}:
                    raise ValueError(f"it has {len(outputs)} outputs: return a dict with exactly "
                                     f"{', '.join(o['id'] for o in outputs)}")
                parts, blobs = [], []
                for o in outputs:
                    reply, data = _one(o["type"], got[o["id"]], picture)
                    parts.append({"id": o["id"], "reply": reply, "bytes": len(data)})
                    blobs.append(data)
                write_message(out, {"ok": True, "kind": "multi", "parts": parts}, b"".join(blobs))
            elif header.get("output") and header.get("output") != "labels.instance.2d":
                reply, data = _one(header["output"], got, picture)  # its declared output, checked
                write_message(out, {"ok": True, **reply, **({'scientific': descriptor} if semantic else {})}, data)
            elif isinstance(got, dict) and "polygons" in got:
                write_message(out, {"ok": True, "kind": "polygons", "polygons": got["polygons"]})
            else:
                labels = np.asarray(got)
                if labels.shape != picture.shape[:2]:
                    raise ValueError(f"its label map is {labels.shape}, not the picture's {picture.shape[:2]}")
                if (labels.dtype.kind not in "iu" or labels.min(initial=0) < 0
                        or labels.max(initial=0) > np.iinfo(np.int32).max):
                    raise ValueError("its labels must be nonnegative int32 instance numbers (zero is background)")
                labels = np.ascontiguousarray(labels, np.int32)
                write_message(out, {"ok": True, "kind": "labels", "shape": list(labels.shape)}, labels.tobytes())
        except Exception as exc:  # noqa: BLE001 - every failure goes back as the reply
            write_message(out, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})


if __name__ == "__main__":
    main()
