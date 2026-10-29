"""The shared agent contract's JSON schemas (contract/agent/, ADR 0026) and a checker for them.

The checker understands only what those files use: type, enum, required, properties, additionalProperties: false,
items, minItems, maxItems, uniqueItems, minimum, maximum, pattern, minLength, maxLength and $ref (to `#/$defs/...` or a sibling file). It is small on purpose, so the
private hosted service can check its answers against the same files with the same code, without a new dependency.
"""
from __future__ import annotations

import json
import math
import re
from functools import lru_cache
from pathlib import Path

from ..paths import REPO

FOLDER = REPO / "contract" / "agent"
NAMES = ("capability", "connection", "generation", "operation", "refusal", "request-key",
         "artifact-reference", "study-reference", "study-question", "study-answer", "annotated-matrix", "experiment-reference",
         "operation-execution", "harness-readiness", "evidence-reference", "claim", "claim-change", "workflow-capability", "project-reference", "workflow-admission", "project-state")

_TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


@lru_cache(maxsize=None)
def load(name: str, folder: Path = FOLDER) -> dict:
    return json.loads((folder / f"{name}.schema.json").read_text())


def problems(value, name: str, folder: Path = FOLDER) -> list[str]:
    """Every way `value` breaks the named schema ("" path is the value itself); [] when it fits."""
    root = load(name, folder)
    out: list[str] = []
    _check(value, root, root, "", out, folder)
    return out


def check(value, name: str, folder: Path = FOLDER) -> None:
    found = problems(value, name, folder)
    if found:
        raise ValueError(f"not a valid {name}: " + "; ".join(found))


def validate(value, declared: dict) -> None:
    """Validate a discovered input contract, including shared artifact references."""
    out = []
    _check(value, declared, declared, '', out, FOLDER)
    if out:
        raise ValueError('; '.join(out))


def _is(value, kind: str) -> bool:
    if kind == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if kind == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return isinstance(value, _TYPES[kind]) and not (kind != "boolean" and isinstance(value, bool))


def _resolve(ref: str, root: dict, folder: Path) -> tuple[dict, dict]:
    if ref.startswith("#/$defs/"):
        return root["$defs"][ref[len("#/$defs/"):]], root
    if ref.endswith(".schema.json") and "/" not in ref:
        other = load(ref[: -len(".schema.json")], folder)
        return other, other
    raise ValueError(f"unsupported $ref {ref!r}")


def _check(value, schema: dict, root: dict, path: str, out: list[str], folder: Path) -> None:
    where = path or "value"
    if "$ref" in schema:
        target, target_root = _resolve(schema["$ref"], root, folder)
        _check(value, target, target_root, path, out, folder)
        return
    kinds = schema.get("type")
    if kinds is not None:
        kinds = [kinds] if isinstance(kinds, str) else kinds
        if not any(_is(value, k) for k in kinds):
            out.append(f"{where} should be {' or '.join(kinds)}")
            return
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where} should be one of {schema['enum']}")
    if type(value) in (int, float):
        if 'minimum' in schema and value < schema['minimum']:
            out.append(f'{where} is below its minimum')
        if 'maximum' in schema and value > schema['maximum']:
            out.append(f'{where} exceeds its maximum')
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0) or len(value) > schema.get('maxItems', len(value)):
            out.append(f'{where} has an unsupported number of items')
        if schema.get('uniqueItems') and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            out.append(f'{where} contains duplicate items')
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            out.append(f"{where} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{where} is too long")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{where} does not match {schema['pattern']}")
    if isinstance(value, dict):
        for key in schema.get("required", ()):
            if key not in value:
                out.append(f"{where} is missing {key}")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                _check(item, properties[key], root, f"{path}.{key}" if path else key, out, folder)
            elif schema.get("additionalProperties") is False:
                out.append(f"{where} has unexpected {key}")
    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            _check(item, schema["items"], root, f"{path}[{index}]", out, folder)
