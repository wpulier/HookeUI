"""Everything this copy of Hooke can run for an agent, described one way (ADR 0026; Will, 2026-10-28: "a general
resource that holds who knows what").

The door names no capability. The listing is whatever this researcher's Hooke has, local or cloud, built in or added:
- every agent capability of the one list (`App.capability_status()`: contract/capabilities.json's rows and the
  catalog's proven entries, `catalog:<key>`);
- each described from its own declaration: the Assistant's tool (`Registry.tools[...]`: its parameters' JSON schema,
  whether it only reads, the models it uses, what it makes, `produces.py`), or the catalog entry's manifest;
- with whether it can run here now, and why not.

A capability whose tool changes a setting or a preference (`Tool.sets`, or `produces` saying so) is not offered: the
door gives an agent no way to change the researcher's settings (ADR 0026). That is a property each tool declares, never
a list of names here.
"""
from __future__ import annotations


WORKSPACE = {"type": "workspace", "label": "the agent's open Project and what is on its stage"}


def _produces(tool) -> str:
    value = getattr(tool, "produces", None)
    return (value() if callable(value) else value) or ""


def changes_settings(tool) -> bool:
    produces = _produces(tool)
    return bool(getattr(tool, "sets", ())) or produces.startswith(("none: changes a setting", "none: changes a preference"))


def _outputs(tool) -> list[dict]:
    from ..produces import KINDS
    produces = _produces(tool)
    if not produces or produces.startswith(("none:", "temporary:")):
        return []
    return [{"type": kind.strip(), "label": KINDS.get(kind.strip(), kind.strip())} for kind in produces.split(",") if kind.strip()]


def _schema(params: dict | None) -> dict:
    """A tool's own parameters, as the shared contract shapes them (an object, nothing undeclared)."""
    params = params if isinstance(params, dict) else {}
    return {"type": "object", "properties": dict(params.get("properties") or {}),
            "required": list(params.get("required") or []), "additionalProperties": False}


def app_generation(build: dict) -> str:
    """A built-in capability runs the app's own code: its generation is the app's source identity."""
    revision = (build.get("revision") or "source")[:12] or "source"
    digest = (build.get("sourceDigest") or "0" * 16)[:16]
    return f"app-{revision}.{digest}"


def describe_row(row: dict, tool, build: dict, catalog_entry=None) -> dict:
    """One capability of the one list, in the shared shape (contract/agent/capability.schema.json)."""
    from ..untrusted import inline
    from .contract import describe

    state = "available" if row.get("available", True) else "unavailable"
    why = str(row.get("why") or row.get("said") or "")
    if catalog_entry is not None:  # a catalog entry: described from its manifest, as the hosted listing does
        m, entry = catalog_entry
        shaped = describe(m, entry, state=state, why=why)
        shaped["id"] = row["id"]
    else:
        shaped = {"id": row["id"], "name": inline(str(row.get("title") or row["id"]), 120),
                  "task": inline(str(row.get("what") or ""), 240),
                  "description": inline(str(getattr(tool, "description", "") or ""), 1200),
                  "inputs": [WORKSPACE], "outputs": _outputs(tool), "inputSchema": _schema(getattr(tool, "params", None)),
                  "credit": {}, "licence": [], "generation": app_generation(build), "state": state,
                  **({"why": why} if why and state != "available" else {})}
    shaped["family"] = str(row.get("family") or "")
    shaped["reads"] = getattr(tool, "kind", "effect") == "query"  # it only reads what exists
    shaped["uses"] = list(getattr(tool, "uses", ()) or ())
    if (row.get("how") or {}).get("say"):
        shaped["say"] = inline(str(row["how"]["say"]), 240)
    return shaped


def everything(built, build: dict) -> list[dict]:
    """The listing. Reads the app's one list fresh; call it on the event loop, as the app's own readers do."""
    from gpu_worker import manifests as catalog
    from .. import kinds

    registry = built.brain.registry
    rows = built.capability_status()["capabilities"]
    out, seen = [], set()
    for row in rows:
        if row.get("actor") != "agent" or row["id"] in seen:
            continue
        name = (row.get("how") or {}).get("tool")
        tool = registry.tools.get(name) if name else None
        if tool is None or changes_settings(tool):
            continue
        seen.add(row["id"])
        entry = None
        key = row.get("catalog")
        if key:
            m = catalog.get(key)
            door_entry = kinds.door_entry(m) if m is not None else None
            entry = (m, door_entry) if door_entry is not None else None
        out.append(describe_row(row, tool, build, entry))
    return out
