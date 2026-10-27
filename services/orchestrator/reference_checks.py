"""Bounded, pure reference comparisons; passing is evidence only for the supplied case.

Callers own loading, runtime admission and reference identity. This module never loads
files or executes adapters. Tables must name their identity and measured columns;
all other cells and all schema metadata remain exact. Row/point order is meaningful.
"""
from __future__ import annotations

import math

import numpy as np

MAX_ELEMENTS = 4_194_304
MAX_JSON_BYTES = 8 * 1024 * 1024
MAX_NODES = 262_144
MAX_DEPTH = 32
_CHUNK = 65_536


class _Invalid(ValueError):
    pass


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise _Invalid(reason)


def _json(value) -> None:
    """Validate without coercion or an unbounded serialization allocation."""
    nodes = size = 0
    active: set[int] = set()

    def walk(v, depth):
        nonlocal nodes, size
        nodes += 1
        _require(nodes <= MAX_NODES and depth <= MAX_DEPTH, "reference data exceeds structural limits")
        t = type(v)
        if v is None or t is bool:
            size += 5
        elif t is int:
            _require(v.bit_length() <= 64, "integer exceeds supported range")
            size += 22
        elif t is float:
            _require(math.isfinite(v), "reference data contains a nonfinite number")
            size += 25
        elif t is str:
            _require(len(v) <= MAX_JSON_BYTES, "reference text exceeds byte limit")
            size += len(v.encode("utf-8")) + 2
        elif t in (list, dict):
            _require(id(v) not in active, "reference data is cyclic")
            _require(len(v) <= MAX_NODES, "reference data exceeds structural limits")
            active.add(id(v))
            size += 2 + len(v)
            if t is dict:
                for key, item in v.items():
                    _require(type(key) is str, "reference keys must be strings")
                    walk(key, depth + 1)
                    walk(item, depth + 1)
            else:
                for item in v:
                    walk(item, depth + 1)
            active.remove(id(v))
        else:
            raise _Invalid("reference data must contain only JSON values")
        _require(size <= MAX_JSON_BYTES, "reference data exceeds byte limit")

    walk(value, 0)


def _exact(a, b) -> bool:
    if type(a) is not type(b):
        return False
    if type(a) is dict:
        return a.keys() == b.keys() and all(_exact(a[k], b[k]) for k in a)
    if type(a) is list:
        return len(a) == len(b) and all(_exact(x, y) for x, y in zip(a, b))
    return a == b


def _number(v) -> bool:
    return type(v) in (int, float) and (type(v) is int or math.isfinite(v))


def _tolerances(tolerance: dict, extra=()) -> tuple[float, float]:
    _require(type(tolerance) is dict and set(tolerance) == {"atol", "rtol", *extra},
             "declare exactly atol and rtol and any required column roles")
    a, r = tolerance["atol"], tolerance["rtol"]
    _require(_number(a) and _number(r), "tolerances must be finite numbers")
    _require(0 <= a <= np.finfo(np.float64).max and 0 <= r <= np.finfo(np.float64).max,
             "tolerances must be finite and nonnegative")
    return float(a), float(r)


def _scalar(a, b, atol, rtol) -> tuple[bool, float]:
    _require(_number(a) and _number(b), "numeric cells must contain finite numbers")
    if type(a) is not type(b):
        integer = a if type(a) is int else b
        _require(abs(integer) <= 2**53, "mixed integer and float comparison exceeds exact integer range")
    # Subtract integers before conversion: adjacent uint64 identities must not round equal.
    error = abs(a - b)
    bound = atol + rtol * abs(b)
    _require(math.isfinite(error) and math.isfinite(bound), "comparison arithmetic exceeds supported range")
    return error <= bound, float(error)


def _arrays(a, b, atol, rtol) -> dict:
    _require(type(a) is np.ndarray and type(b) is np.ndarray, "pictures must be numpy arrays")
    _require(a.shape == b.shape and a.dtype == b.dtype, "picture shape or dtype differs from reference")
    _require(a.dtype.kind in "biuf" and 0 < a.size <= MAX_ELEMENTS, "picture type or size is unsupported")
    maximum = 0.0
    mismatches = 0
    # Flat slices bound temporary allocations even for a noncontiguous array.
    for offset in range(0, a.size, _CHUNK):
        x, y = a.flat[offset:offset + _CHUNK], b.flat[offset:offset + _CHUNK]
        _require(bool(np.isfinite(x).all() and np.isfinite(y).all()), "picture contains nonfinite numbers")
        if a.dtype.kind in "biu":
            for av, bv in zip(x, y):
                good, error = _scalar(int(av), int(bv), atol, rtol)
                maximum = max(maximum, error)
                mismatches += not good
        else:
            with np.errstate(over="ignore", invalid="ignore"):
                error = np.abs(x.astype(np.float64) - y.astype(np.float64))
                bound = atol + rtol * np.abs(y.astype(np.float64))
            _require(bool(np.isfinite(error).all() and np.isfinite(bound).all()),
                     "comparison arithmetic exceeds supported range")
            maximum = max(maximum, float(error.max()))
            mismatches += int(np.count_nonzero(error > bound))
    return {"elements": int(a.size), "mismatches": mismatches, "max_absolute_error": maximum}


def _table(actual, expected, tolerance) -> dict:
    atol, rtol = _tolerances(tolerance, ("numeric_columns", "identity_columns"))
    _require(set(actual) == set(expected) == {"columns", "rows"}, "table fields are incomplete or unsupported")
    ac, ec = actual["columns"], expected["columns"]
    _require(type(ac) is list and type(ec) is list and bool(ec) and _exact(ac, ec),
             "table schema differs from reference")
    _require(all(type(c) is dict and type(c.get("name")) is str for c in ec), "table schema is invalid")
    names = [c["name"] for c in ec]
    _require(len(set(names)) == len(names), "table column names must be unique")
    numeric, identities = tolerance["numeric_columns"], tolerance["identity_columns"]
    for role in (numeric, identities):
        _require(type(role) is list and all(type(n) is str for n in role)
                 and len(set(role)) == len(role) and set(role) <= set(names), "table column roles are invalid")
    _require(bool(identities) and not set(numeric) & set(identities), "declare separate table identity columns")
    ar, er = actual["rows"], expected["rows"]
    _require(type(ar) is list and type(er) is list and len(ar) == len(er), "table row count differs from reference")
    _require(all(type(row) is list and len(row) == len(names) for row in ar + er), "table row shape is invalid")
    positions = [names.index(n) for n in identities]
    seen = set()
    for row in er:
        key = tuple(row[i] for i in positions)
        _require(all(type(v) in (str, int) for v in key), "table identities must be strings or integers")
        _require(key not in seen, "table reference identities are not unique")
        seen.add(key)
    maximum = 0.0
    mismatches = 0
    for row, reference in zip(ar, er):
        _require(all(_exact(row[i], reference[i]) for i in positions), "table row identities or order differ")
        for name, a, b in zip(names, row, reference):
            if name in numeric:
                good, error = _scalar(a, b, atol, rtol)
                maximum = max(maximum, error)
                mismatches += not good
            else:
                mismatches += not _exact(a, b)
    return {"rows": len(er), "mismatches": mismatches, "max_absolute_error": maximum}


def compare(actual: dict, expected: dict, tolerance: dict, *, output_type: str) -> dict:
    """Compare one validated runtime result with its explicit, caller-loaded reference.

    image.2d.rgb8 uses picture (+ optional truthful shape); table.objects requires
    numeric_columns and identity_columns alongside atol/rtol; points.2d uses atol/
    rtol; text and all other declared data types require exact comparison and {}.
    Returns a bounded, JSON-native result, including failures for malformed inputs.
    """
    try:
        _require(type(actual) is dict and type(expected) is dict and type(tolerance) is dict,
                 "comparison inputs must be dictionaries")
        _require(type(output_type) is str and 0 < len(output_type) <= 128, "declare an output type")
        _json(tolerance)
        if output_type == "image.2d.rgb8":
            atol, rtol = _tolerances(tolerance)
            for value in (actual, expected):
                _require(all(type(key) is str for key in value), "picture keys must be strings")
                _require(set(value) in ({"picture"}, {"picture", "shape"}), "picture fields are incomplete or unsupported")
                pic = value["picture"]
                _require(type(pic) is np.ndarray, "pictures must be numpy arrays")
                if "shape" in value:
                    _json(value["shape"])
                    _require(_exact(value["shape"], list(pic.shape)), "picture shape metadata is inconsistent")
            metrics = _arrays(actual["picture"], expected["picture"], atol, rtol)
        else:
            _json(actual)
            _json(expected)
            if output_type == "table.objects":
                metrics = _table(actual, expected, tolerance)
            elif output_type == "points.2d":
                atol, rtol = _tolerances(tolerance)
                _require(set(actual) == set(expected) == {"points"}, "point fields are incomplete or unsupported")
                a, b = actual["points"], expected["points"]
                _require(type(a) is list and type(b) is list and len(a) == len(b), "point count differs from reference")
                _require(all(type(p) is list and len(p) == 2 for p in a + b), "points must have two coordinates")
                errors = [_scalar(x, y, atol, rtol) for p, q in zip(a, b) for x, y in zip(p, q)]
                metrics = {"points": len(b), "mismatches": sum(not ok for ok, _ in errors),
                           "max_absolute_error": max((e for _, e in errors), default=0.0)}
            else:
                _require(not tolerance, "exact outputs do not accept numeric tolerances")
                field = "text" if output_type == "text" else "tracks" if output_type == "tracks.graph" else "data"
                _require(set(actual) == set(expected) == {field}, "output fields are incomplete or unsupported")
                if field == "text":
                    _require(type(actual[field]) is str and type(expected[field]) is str, "text output must be text")
                metrics = {"mismatches": int(not _exact(actual, expected))}
        ok = metrics["mismatches"] == 0
        return {"ok": ok, "why": "matches reference" if ok else "output differs from reference", "metrics": metrics}
    except (_Invalid, OverflowError, UnicodeError, RecursionError) as exc:
        return {"ok": False, "why": str(exc)[:240], "metrics": {}}
