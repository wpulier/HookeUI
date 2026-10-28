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


LOCAL_ONLY = ("It uses a cloud service with the researcher's own keys, which may cost them money. Through the door "
              "only work on this Mac runs; the researcher can run it in Hooke.")


def locality(row: dict, tool, roles: dict, manifest=None) -> str:
    """Where it runs: "mac" (this Mac only), "cloud" (a hosted service at least in part) or "unknown". Read from the
    models it runs with now: each of its roles' current choice, its tool's declared models, a catalog entry's
    components. Unknown counts as cloud: the door never assumes work is free."""
    from ..capabilities import runs
    from .. import licences
    places = set()
    for role_id in row.get("roles") or ():
        role = roles.get(role_id)
        if role is None:
            return "unknown"
        if role.get("runs") is not None:
            places.add(role["runs"])
            continue
        current = next((o for o in role.get("options") or () if o.get("id") == role.get("current")), None)
        if current is None:
            return "unknown"
        places.add(current.get("runs") or "")
    uses = tuple(getattr(tool, "uses", ()) or ())
    if any(u not in licences.COMPONENTS and not _builtin(u) for u in uses):
        return "unknown"  # a model the licence table doesn't know: never assumed free
    places.add(runs(uses))
    if manifest is not None:
        if getattr(manifest, "runner", "") == "builtin-cpu":
            places.add("mac")
        else:
            places.add(runs(licences.model_uses(manifest)))
    if places & {"cloud", "both"}:
        return "cloud"
    return "mac"


def _builtin(model_id: str) -> bool:
    from gpu_worker.manifests import get
    entry = get(model_id)
    return entry is not None and entry.runner == "builtin-cpu"


def model_places(built) -> dict[str, str]:
    """Every model or way of running a job this Hooke knows, by id, with where it runs ("mac" or "cloud"): role
    options, the operations' implementations, licence components and catalog entries. Used to check the models an
    agent's arguments name (work.check_models)."""
    from gpu_worker import manifests as catalog
    from .. import licences
    from ..capabilities import runs
    from ..operations import OPERATIONS
    places: dict[str, str] = {}

    def put(key, where):
        if key:
            places[str(key)] = "cloud" if where in ("cloud", "both", "unknown") else "mac"

    for component, facts in licences.COMPONENTS.items():
        put(component, "cloud" if getattr(facts, "kind", "") == "service" else "mac")
    for op in OPERATIONS.values():
        for impl in op.implementations:
            where = "cloud" if impl.worker == "cloud" else "mac"
            put(getattr(impl, "id", None), where)
            put(getattr(impl, "option", None), where)
    for role in (built.capabilities_wire(counts=False).get("roles") or ()):
        for option in role.get("options") or ():
            put(option.get("id"), option.get("runs") or "mac")
    for m in catalog.manifests().values():
        put(m.id, "mac" if m.runner == "builtin-cpu" else runs(licences.model_uses(m)) or "mac")
    return places


def describe_row(row: dict, tool, build: dict, catalog_entry=None, where: str = "mac") -> dict:
    """One capability of the one list, in the shared shape (contract/agent/capability.schema.json)."""
    from ..untrusted import inline
    from .contract import describe

    state = "available" if row.get("available", True) else "unavailable"
    why = str(row.get("why") or row.get("said") or "")
    if where != "mac":
        state, why = "unavailable", LOCAL_ONLY
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
    shaped["reads"] = _produces(tool).startswith("none: reads")  # it only reads what exists (its own declaration)
    shaped["runsOn"] = "this Mac" if where == "mac" else "a cloud service" if where == "cloud" else "not known"
    shaped["cost"] = "none" if where == "mac" else "may cost the researcher (their own keys)"
    if (row.get("how") or {}).get("say"):
        shaped["say"] = inline(str(row["how"]["say"]), 240)
    return shaped


def _manifest(key: str):
    from gpu_worker import manifests as catalog
    return catalog.get(key)


def gather(built) -> list[tuple]:
    """What the listing describes, read on the event loop as the app's own readers read it: each agent capability an
    agent may run (agent_runnable.py), with its tool, the roles it runs with, and its catalog entry if it has one."""
    from .. import agent_runnable

    registry = built.brain.registry
    wire = built.capabilities_wire(counts=False)
    roles = {r["id"]: r for r in wire.get("roles") or () if isinstance(r, dict) and r.get("id")}
    out, seen = [], set()
    for row in wire["capabilities"]:
        if row.get("actor") != "agent" or row["id"] in seen:
            continue
        name = (row.get("how") or {}).get("tool")
        tool = registry.tools.get(name) if name else None
        if tool is None or not agent_runnable.runnable(name) or changes_settings(tool):
            continue  # default no: only what is declared runnable for agents, and never what changes a setting
        key = row.get("catalog") or ((row.get("how") or {}).get("args") or {}).get("capability")
        if name == "run_capability" and not (key and agent_runnable.catalog_runnable(_manifest(key))):
            continue  # a catalog capability: only a reviewed entry, named by its row (agent_runnable.catalog_runnable)
        seen.add(row["id"])
        out.append((row, tool, roles))
    return out


def describe_all(gathered: list[tuple], build: dict) -> list[dict]:
    """The listing from what `gather` read. Reads manifests and hashes entry folders: call it in a thread."""
    from gpu_worker import manifests as catalog
    from .. import kinds

    out = []
    with kinds.read_once():
        for row, tool, roles in gathered:
            entry, manifest = None, None
            key = row.get("catalog")
            if key:
                manifest = catalog.get(key)
                door_entry = kinds.door_entry(manifest) if manifest is not None else None
                entry = (manifest, door_entry) if door_entry is not None else None
            out.append(describe_row(row, tool, build, entry, locality(row, tool, roles, manifest)))
    return out


def everything(built, build: dict) -> list[dict]:
    """The listing in one call (tests, scripts); the door gathers on the loop and describes in a thread."""
    return describe_all(gather(built), build)
