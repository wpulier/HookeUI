"""A capability as every agent is told it, from its catalog declaration alone (ADR 0024, ADR 0026).

`describe` is the one place a declaration becomes the shared shape in contract/agent/capability.schema.json. The
local door lists with it; the hosted listing can call the same function, so a new capability reaches both with no
change here and the words keep one meaning.
"""
from __future__ import annotations

CONTRACT = "1"  # the Hooke-Contract version, the same header and value as the hosted service's

_SCHEMA_TYPES = {"int": "integer", "float": "number", "number": "number", "bool": "boolean", "text": "string",
                 "choice": "string", "string": "string"}


def input_schema(params: list[dict]) -> dict:
    """A capability's parameters as a JSON schema, leaving out what only the researcher sets (`agent = false`)."""
    properties, required = {}, []
    for p in params:
        if p.get("agent") is False:
            continue
        prop = {"type": _SCHEMA_TYPES.get(p["type"], "string"), "title": p.get("label", p["id"])}
        for key, name in (("tooltip", "description"), ("default", "default"), ("min", "minimum"), ("max", "maximum")):
            if p.get(key) is not None:
                prop[name] = p[key]
        if p.get("options"):
            values = [o["value"] if isinstance(o, dict) else o for o in p["options"]]
            prop["enum"] = values
            if all(type(v) is int for v in values):
                prop["type"] = "integer"
            elif all(type(v) in (int, float) for v in values):
                prop["type"] = "number"
        properties[p["id"]] = prop
        if not p.get("optional") and p.get("default") is None:
            required.append(p["id"])
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


def describe(m, entry, *, state: str, why: str = "", next: str = "") -> dict:
    """The shared shape for manifest `m` whose door entry is `entry` (kinds.door_entry)."""
    from .. import kinds
    from ..capability_identity import generation_of

    params = [kinds.param_wire(p) for p in m.params]
    outputs = ([{"id": o.id, "type": o.type, "label": o.label or o.id.replace("_", " ")} for o in m.outputs]
               if m.contract else [{"id": "out", "type": entry.output, "label": kinds.gives(entry.output)}])
    inputs = [{"type": entry.input, "label": kinds.gives(entry.input)}] if entry.input else []
    return {"id": m.id, "name": m.name, "task": m.what, "description": m.description, "inputs": inputs,
            "outputs": outputs, "inputSchema": input_schema(params),
            "credit": {k: str(v) for k, v in m.credit.items() if k in ("authors", "link", "paper", "cite")},
            "licence": list(m.licence), "generation": generation_of(m), "state": state,
            **({"why": why} if why else {}), **({"next": next} if next else {})}
