"""New kinds of job, without the app's code (docs/design/2026-10-13-capabilities-for-everyone.md, C6).

A model for an existing job (outlining, finding cells) is a folder. A **new kind of job** (denoising, a measurement of its
own, an analysis that writes a table) is a folder too: an entry with its own adapter (runner `python-env`) declares an
operation of its own in `[[implements]]`, taking the camera's view (`image.2d.rgb8`) and giving one of the outputs the app
already shows, keeps and hands to the agent (`manifests.KIND_OUTPUTS`):

| output | its adapter returns | shown | kept |
|---|---|---|---|
| `image.2d.rgb8` | an 8-bit RGB (or grey) picture, any size | in the magnifier, labelled as made by it (not the camera's view) | a result, with its picture |
| `table.objects` | `{"columns": [...], "rows": [[...]]}` or a list of dicts | the measurements table (CSV there) | a result, bound to its sha256 |
| `text` | words (`str`) | in the answer | a result |
| `points.2d` | `{"points": [[x, y], ...]}` in the picture's pixels | the frame it ran on, marked by the app (not the live view) | a result: its points in that frame's pixels (no sizes), their table |
| `tracks.graph` (takes `clip.2d.rgb8`) | `{"tracks": [{"points": [[frame, x, y], ...]}, ...]}`, frame an index into the frames it was given | the tracks layer | a tracks item (motion), in slide coordinates, its speeds measured by the app |
| `labels.instance.2d` | an int label map of the picture's size, 0 the background | the cells layer | a population ("Outlines"), measured, its table |

Once installed and proven (env_runner.prove checks its output is what it declared, on a real picture), it becomes an
**operation** the runner knows (env_runner.register): run through `operations.Runner` like every other, with an Execution,
what ran said in Activity, kept in the outcomes. The agent runs it with `run_capability` ("denoise this", "run the
intensity table on the view"), the studio with Try it on its card. Taken away, its operation goes with it.

What it makes is its makers' output, never the camera's view: a picture is labelled with what made it, and words and
tables reach the model only as untrusted data (untrusted.py), since nobody reviewed the code that wrote them.

The capability contract (catalog format 2, ADR 0024; docs/design/2026-10-15-one-way-in.md) makes this module **the one
door**: every proven capability, whatever runs it, is in `available()`, and every actor reads it (the Actions panel and
Try through the capabilities wire, the agent's `run_capability`, researchers' `run`). A runner plugs in once as a
**backend** (`BACKENDS`): python-env (env_runner.py) is the first, and Fiji registers the second without touching the
door. A format-2 capability declares its inputs (data and parameters in one list) and outputs with open types, as a
ComfyUI node does: an output of a type the app has no place for is kept and handed on as data (`DATA_OUTPUT`).
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from gpu_worker import manifests as catalog

OUTPUTS = catalog.KIND_OUTPUTS
MAX_TEXT = 20_000  # characters of words
MAX_ROWS = 10_000  # rows of a table
MAX_COLUMNS = 64
MAX_POINTS = 10_000
MAX_SIDE = 4096  # a picture's side, in px (at most 48 MB read back)
MAX_CLIP_FRAMES = 12  # a tracks kind is given at most this many frames of the last seconds, evenly spread
MAX_TRACKS = 500
MAX_DATA = 1 << 20  # bytes of a value of a type the app has no place for, kept as data (JSON)
DATA_OUTPUT = "data"  # how the app keeps and shows any output type it has no place for: its value, as data
# The data inputs the app can give a capability now, and how it gives each: the view (or the asset on stage), the last
# seconds of it, one of the session's volumes. A capability taking another type is listed, said as not runnable yet.
SUPPLIED = {"image.2d.rgb8": "the view", "clip.2d.rgb8": "the last seconds of the view", "volume.3d": "a volume"}
# Where a capability is listed in Actions (contract/actions.json groups): its category's first word, by a group's title,
# else by what it gives.
FAMILY_OF_OUTPUT = {"image.2d.rgb8": "seeing", "table.objects": "measuring", "points.2d": "finding",
                    "labels.instance.2d": "outlining", "tracks.graph": "motion", "text": "naming"}


@dataclass(frozen=True)
class Backend:
    """A runner, plugged into the door once (docs/design/2026-10-15-one-way-in.md): every entry it runs, the installed
    and proven version of one (or None), the operations adapter that runs it, and how a proven one's choice is said."""
    runner: str
    entries: Callable[[Mapping[str, str]], list]
    in_use: Callable[[Any, Mapping[str, str]], Any]  # an object with `.m` (the manifest it runs) and `.proof`
    adapter: Callable[[], Callable]  # the operations.ADAPTERS function that runs it, (run, capture, prompt, impl, params)
    note: Callable[[Any], str] = lambda installed: "proven here"
    expand: Callable[[Any], tuple] = lambda installed: (installed,)
    registers: bool = True  # false: the app's own code already registers what it runs (builtin-cpu), the door only lists it
    own_process: bool = False  # its adapter runs each entry in a process of its own, never on a GPU worker (Fiji)


BACKENDS: dict[str, Backend] = {}


def plug(backend: Backend) -> None:
    """A runner's one line into the door."""
    BACKENDS[backend.runner] = backend


def backends() -> dict[str, Backend]:
    from . import env_runner, fiji_runner  # noqa: F401 - each plugs itself in: python-env, then Fiji

    return BACKENDS


def contributed(group: str, option_id: str) -> bool:
    """Whether a choice is one a backend registered (`register`: a contributed or AI-made capability, made a choice only
    once its own functional proof passed), not one of the app's own. Reads only what is registered; builds nothing."""
    from .operations import ADAPTERS, OPERATIONS

    adapters = {b.adapter() for b in backends().values() if b.registers}
    return any(op.selected_by == group and ADAPTERS.get((op.id, option_id)) in adapters for op in list(OPERATIONS.values()))


def implements(m: catalog.Manifest | None) -> catalog.Implements | None:
    """The new kind of job an entry does (its first implements with a kind's output), or None."""
    if m is None or m.runner not in backends():
        return None
    from .operations import OPERATIONS

    if m.contract:  # format 2: any output type (types are open), its own operation
        i = m.implements[0]
        return i if i.operation not in OPERATIONS or i.operation in registered() else None
    for i in m.implements:
        if i.input == "volume.3d" and i.output in catalog.VOLUME_KIND_OUTPUTS:  # a job on a volume (registrations.py)
            return i
        if i.output in OUTPUTS and i.input == catalog.KIND_INPUT.get(i.output, "image.2d.rgb8") and (
                i.operation not in OPERATIONS or i.operation in registered()):
            return i
    return None


def door_entry(m: catalog.Manifest | None) -> catalog.Implements | None:
    """What a capability does through the door: a new kind of job (implements), or, written in the contract, a way of
    doing one of the app's own jobs (`operation = "cells"`): listed, with its form, as any capability is."""
    found = implements(m)
    if found is not None or m is None or not m.contract or m.runner not in backends() or not m.implements:
        return found
    from .operations import OPERATIONS

    i = m.implements[0]
    return i if i.operation in OPERATIONS else None


def on_volumes(i: catalog.Implements | None) -> bool:
    return i is not None and i.input == "volume.3d"


def is_kind(m: catalog.Manifest | None) -> bool:
    return implements(m) is not None


_registered: set[str] = set()  # the operations made for new kinds (env_runner.register), so only those are taken away


def registered() -> set[str]:
    return _registered


def gives(output: str) -> str:
    """An output type in a few words: one the app knows by what it is, any other by its type id, as data."""
    return OUTPUTS.get(output) or catalog.VOLUME_KIND_OUTPUTS.get(output) or f"{output} data"


def file_workflow(m: catalog.Manifest) -> bool:
    """Reviewed file adapters use WorkflowService, not the live-view runner."""
    if m.runner != 'builtin-cpu' or m.namespace:
        return False
    from scientific_compute.catalog import IDS
    return m.id in IDS


def takes_why(m: catalog.Manifest) -> str:
    """Why the app can't give a capability its inputs yet ("" when it can): a data input of a type nothing here
    supplies, or more data inputs than one (a second input from the session comes in a later part of the contract)."""
    if not m.contract:
        return ""
    if file_workflow(m):
        return 'Use Scientific workflows with exact retained Project files. Discover current input contracts and runtime readiness with find_workflows(capabilities=true).'
    from . import admission
    if admission.typed(m):  # several or native inputs: each bound at Run to a retained resource (admission.bind)
        return admission.why_not(m)
    data = m.data_inputs
    if not data:
        return "it takes no picture or other data, and the app runs capabilities on data for now"
    if len(data) > 1:
        return (f"it takes {len(data)} inputs ({', '.join(d.type for d in data)}); the app gives one for now (a second "
                "input from the session comes later)")
    if data[0].type not in SUPPLIED:
        return f"it takes {data[0].type}, which nothing here gives it yet"
    gives_tracks = any(o.type == "tracks.graph" for o in m.outputs)
    if (data[0].type == "clip.2d.rgb8") != gives_tracks:
        return ("a capability that takes the last seconds of the view gives tracks, and tracks come from those seconds, "
                "for now")
    if len(m.outputs) > 1 and data[0].type != "image.2d.rgb8":
        return "several outputs are kept for capabilities that take the view, for now"
    return ""


def family(m: catalog.Manifest, i: catalog.Implements) -> str:
    """Where it is listed in Actions: its category's first word matched to a group's title, else by what it gives."""
    import json

    from .capabilities import ROOT  # the repository's contract/ folder, where the capability list is read

    groups = json.loads((ROOT / "contract" / "actions.json").read_text())["groups"]
    head = (m.category or "").split("/")[0].strip().lower()
    for g in groups:
        if head and head in (g["title"].lower(), g["family"]):
            return g["family"]
    return FAMILY_OF_OUTPUT.get(i.output, "measuring")


def param_wire(p: catalog.Input) -> dict:
    """A parameter as every actor is told it: the form's field, the agent's and a researcher's argument."""
    return {k: v for k, v in (("id", p.id), ("type", p.type), ("label", p.said), ("tooltip", p.tooltip),
                              ("optional", p.optional), ("default", p.default), ("min", p.min), ("max", p.max),
                              ("step", p.step), ("options", list(p.options)), ("unit", p.unit),
                              ("advanced", p.advanced), ("multiline", p.multiline), ("agent", p.agent))
            if (k == "agent" and v is False) or (k != "agent" and (k in ("id", "type", "label", "default") and v is not None
                                                                   or not (v is None or v is False
                                                                           or (isinstance(v, (str, list)) and not v))))}


def _typed(m) -> bool:
    from . import admission
    return admission.typed(m)


def described(kind: dict) -> str:
    """Everything an actor needs to run it well, in words (the agent's describe_capability): what it does, takes and
    gives, each setting with its range or choices, unit, default and meaning (and whether only the user sets it), what an
    empty result means and what it refuses. Contributed words: the caller wraps them as untrusted data."""
    outputs = kind.get("outputs") or [{"label": gives(kind.get("output", "")), "type": kind.get("output", "")}]
    taken = kind.get("input", "image.2d.rgb8")
    takes = (", ".join(f"{p['id']} ({p['type']}: a retained resource bound at Run)" for p in kind["inputs"])
             if kind.get("typed") or kind.get('executionRoute')=='workflow' else SUPPLIED.get(taken, taken))
    lines = [f"{kind['name']}: {kind.get('what') or ''}", *([kind["description"]] if kind.get("description") else []),
             f"Takes {takes}; gives " + ", ".join(
                 f"{o.get('label') or gives(o['type'])} ({gives(o['type'])})" for o in outputs) + "."]
    for p in kind.get("params") or []:
        lines.append("- " + params_words([p]) + ("; only the user sets it" if p.get("agent") is False else ""))
    if not kind.get("params"):
        lines.append("It has no settings.")
    for what, said in (kind.get("outcomes") or {}).items():
        lines.append(f"{'Found none means' if what == 'empty' else 'It refuses'}: {said}")
    if kind.get('scientific'):
        import json
        lines.append('Scientific resource requirements (unknown metadata does not satisfy them): ' +
                     json.dumps(kind['scientific'], sort_keys=True))
    if kind.get('executionRoute')=='workflow':
        lines.append('Execution route: '+kind['why'])
    elif not kind.get("runnable", True):
        lines.append(f"It can't run here yet: {kind.get('why')}")
    return "\n".join(lines)


def params_words(params: list[dict]) -> str:
    """A capability's parameters in a line, for the agent and researchers: "threshold (whole number 0-255, default 128)"."""
    out = []
    for p in params:
        bits = [catalog.WIDGETS.get(p["type"], p["type"])]
        if p.get("options"):
            bits = ["one of " + "/".join(str(o) for o in p["options"])]
        if p.get("min") is not None or p.get("max") is not None:
            bits.append(f"{p.get('min', '')}-{p.get('max', '')}")
        if p.get("unit"):
            bits.append(p["unit"])
        if p.get("default") is not None:
            bits.append(f"default {p['default']}")
        elif p.get("optional"):
            bits.append("optional")
        out.append(f"{p['id']} ({', '.join(str(b) for b in bits)})" + (f": {p['tooltip']}" if p.get("tooltip") else ""))
    return "; ".join(out)


_ONCE: ContextVar[dict | None] = ContextVar("kinds_read_once", default=None)


@contextmanager
def read_once():
    """A scope (`with kinds.read_once(): ...`) in which what is on here is read once: available() and each capability's
    manifest (manifest_of) come from one reading, which reads every entry's installed generation, receipts and proof
    from disk. One run of a capability is one scope, entered in a thread (asyncio.to_thread), never on the event loop.
    Nested scopes share the outer one."""
    token = _ONCE.set({}) if _ONCE.get() is None else None
    try:
        yield
    finally:
        if token is not None:
            _ONCE.reset(token)


def _reading(environ: Mapping[str, str]) -> tuple[list[dict], dict]:
    memo = _ONCE.get()
    if memo is None:
        return _available(environ)
    if id(environ) not in memo:
        memo[id(environ)] = (environ, *_available(environ))  # the environ kept: its id is never reused meanwhile
    return memo[id(environ)][1:]


def available_with_manifests(environ: Mapping[str, str] = os.environ) -> tuple[list[dict], dict]:
    """available(), with the manifest each listed capability runs, by its key (the agent door describes each from its
    declaration: door/contract.py). The same reading as available() inside read_once."""
    return _reading(environ)


def available(environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Every capability on here (installed and proven), whatever runs it: the one registry every actor reads.
    {"key", "id", "name", "what", "operation", "input", "output", "outputs", "params", "family", "says", "runner",
    "runnable", "why", ...}. It reads files: a request reads it in a thread, once (read_once)."""
    return _reading(environ)[0]


def _available(environ: Mapping[str, str] = os.environ) -> tuple[list[dict], dict]:
    """available()'s reading, with the manifest each listed capability runs, by its key."""
    from . import own_trials

    out, manifests = [], {}
    for b in backends().values():
        for discovered in b.entries(environ):
            from . import fiji_display
            installed = (fiji_display.in_use(discovered, environ) if discovered.runner == 'fiji' else
                         b.in_use(discovered, environ))
            if installed is None or own_trials.pending(installed.m, environ):
                continue
            blocked: tuple[str, str] | None = None  # the package's readiness, read once for all its members
            for member in b.expand(installed):
                m = member.m
                i = door_entry(m)
                if i is None:
                    continue
                why = takes_why(m)
                said = ""
                if not why:  # installed and proven, and still the resolver's facts: its edition, this Mac, a key or data
                    blocked = blocked if blocked is not None else _not_ready(installed.m, environ)
                    why, said = blocked
                out.append(_row(m, i, why, said))
                manifests[m.key] = m
    # A contract for an adapter already shipped by the app needs no backend
    # replacement. The normal readiness resolver still owns install/proof gates.
    from . import readiness
    for m in _app_owned(environ):
        if m.key in manifests:
            continue
        status = readiness.model(m.key, environ=environ)
        why = takes_why(m) or ("" if status.status == "ready" else status.why or status.word)
        out.append(_row(m, door_entry(m), why, readiness.said(status.status, status.word, status.why)))
        manifests[m.key] = m
    return out, manifests


def _app_owned(environ):
    """Only built-in declarations backed by an already registered app adapter.

    Never imports code or registers a replacement; contributed folders cannot borrow
    a built-in identity. Backend-managed implementations retain their proof lifecycle.
    """
    from .operations import ADAPTERS, OPERATIONS
    managed = {b.adapter() for b in backends().values() if b.registers}
    for m in catalog.manifests().values():
        if not m.contract or m.namespace or len(m.implements) != 1 or door_entry(m) is None:
            continue
        declared = m.implements[0]
        operation = OPERATIONS.get(declared.operation)
        adapter = ADAPTERS.get((declared.operation, declared.impl))
        if (declared.impl == m.id and operation is not None and operation.impl(m.id) is not None
                and adapter is not None and adapter not in managed):
            yield m


def _row(m, i, why, said):
    """One declaration display for backend-owned and already app-owned adapters."""
    params = [param_wire(p) for p in m.params]
    outputs = ([{"id": o.id, "type": o.type, "label": o.label or o.id.replace("_", " ")} for o in m.outputs]
               if m.contract else [{"id": "out", "type": i.output, "label": gives(i.output)}])
    says = (f"{m.name}: {m.what} (gives " + ", ".join(gives(o["type"]) for o in outputs)
            + (", on one of your volumes)" if on_volumes(i) else ")")
            + (f"; parameters: {params_words(params)}" if params else ""))
    brief = (f"{m.name}: {m.what} (gives " + ", ".join(gives(o["type"]) for o in outputs) + ")"
             + (f"; settings: {', '.join(p['id'] for p in params)}" if params else ""))
    return {"key": m.key, "id": m.id, "name": m.name, "what": m.what, "description": m.description,
                "brief": brief,
                "operation": i.operation, "input": i.input, "output": i.output, "outputs": outputs,
                "params": params, "family": family(m, i), "category": m.category, "runner": m.runner,
                "aliases": list(m.search_aliases), "experimental": m.experimental,
                "derivedFrom": dict(m.derived_from), "hidden": list(m.hidden),
                **({'executionRoute':'workflow'} if file_workflow(m) else {}),
                **({'inputs':[{'id':p.id,'type':p.type} for p in m.data_inputs]} if file_workflow(m) else {}),
                **({'scientific': m.scientific} if getattr(m, 'scientific', {}) else {}),
                # Several or native inputs, each bound at Run to a retained resource (admission.py).
                **({"typed": True, "inputs": [{"id": p.id, "type": p.type} for p in m.data_inputs]}
                   if _typed(m) else {}),
                "outcomes": dict(m.outcomes), "runnable": not why, "why": why, "says": says,
                **({"said": said} if said else {})}


def _not_ready(m: catalog.Manifest, environ: Mapping[str, str]) -> tuple[str, str]:
    """Why an installed, proven capability still can't run here, from the one resolver (readiness.py, ADR 0019): its
    edition, this Mac, a key or data it still needs. ("", "") when nothing stops it; else (why, the status sentence).
    A contributed row offered Run while its card said Needs setup or Unavailable here (L7b, ticket 62 row 13)."""
    from . import licences, readiness

    edition = licences.why_not(licences.model_uses(m), environ=environ)
    if edition:
        return edition, readiness.said("unavailable", "Not in this edition", edition)
    not_here = readiness.misfit(m)
    if not_here:
        return not_here, readiness.said("unavailable", "Not on this Mac", not_here)
    # Installed and proven is in_use's to say (it is why this capability is listed); the resolver adds what in_use does
    # not read: a key, a licence, data or weights it still needs.
    steps = [s for s in readiness.model_steps(m, readiness._keys(None, environ), environ)[0]
             if s.kind in ("key", "licence", "download", "weights")]
    if not steps:
        return "", ""
    first = steps[0]
    why = first.says + (f": {first.how}" if first.how and first.how != first.says else "")
    return why, readiness.said("needs-setup", first.says)


def said_name(kind: dict) -> str:
    """A capability's name as the app says it outside the untrusted wrapper (its makers' words): one plain line, capped,
    with nothing that could open or close a wrapper or a quote."""
    from .untrusted import inline

    return inline(kind.get("name") or kind.get("key"), NAME_MAX) or "it"


NAME_MAX = 60  # characters of a contributed capability's name where the app says it


def said_words(words: object, limit: int = 300) -> str:
    """Words that carry a capability's own (a refusal naming its parameter's label, how it says "found none") as the app
    says them outside the wrapper: one plain line, capped."""
    from .untrusted import inline

    return inline(words, limit)


def find(key: str, environ: Mapping[str, str] = os.environ) -> dict | None:
    """One capability on here, by its key, id or name."""
    return match(available(environ), key)


def match(rows: list[dict], key: str) -> dict | None:
    """Structured member identities are exact; legacy names retain their existing search."""
    import re
    if '#' in str(key):
        return next((row for row in rows if row['key'] == key), None)
    norm = lambda text: re.sub(r'[^a-z0-9]', '', str(text or '').lower())
    want = norm(key)
    exact = [row for row in rows if want and want in (norm(row['key']), norm(row['id']), norm(row['name']))]
    candidates = exact or [row for row in rows if want and want in norm(row['name'])]
    return candidates[0] if len(candidates) == 1 else None


def manifest_of(kind: dict, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    """The manifest the proven version of a capability runs: from this scope's reading (read_once) when it is listed
    there, else read."""
    if _ONCE.get() is not None:
        found = _reading(environ)[1].get(kind.get("key"))
        if found is not None:
            return found
    return _scan(kind, environ)


def _scan(kind: dict, environ: Mapping[str, str] = os.environ) -> catalog.Manifest | None:
    owned = next((m for m in _app_owned(environ) if m.key == kind['key']), None)
    if owned is not None:
        return owned
    b = backends().get(kind.get("runner") or "")
    if b is None:
        return None
    owner_key = kind['key'].partition('#')[0]
    m = next((x for x in b.entries(environ) if x.key == owner_key), None)
    installed = b.in_use(m, environ) if m is not None else None
    return next((one.m for one in b.expand(installed) if one.m.key == kind['key']), None) if installed is not None else None


UNREAD = object()  # inputs_for's `m` when its reading found no manifest: nothing to read again


def values_for(kind: dict, given: dict | None, environ: Mapping[str, str] = os.environ, *,
               m: catalog.Manifest | None = None, agent: bool = False) -> dict:
    """One run's parameter values, checked against its declaration (manifests.params_given): the one check every actor
    goes through. `m`: its manifest, already read; `agent`: the agent or a researcher gave them (a user-only parameter
    is refused). Raises ValueError with the problems in plain words."""
    m = m or manifest_of(kind, environ)
    if m is None:
        raise ValueError(f"{kind.get('name')} is no longer on here")
    values, problems = catalog.params_given(m, given, agent=agent)
    if problems:
        raise ValueError("; ".join(problems))
    return values


def inputs_for(kind: dict, given: dict | None, hub=None, environ: Mapping[str, str] = os.environ, *,
               values: dict | None = None, m=None, agent: bool = False) -> str | None:
    """One run's `inputs` runner param: its checked values (values_for) and the hidden values it asked for, read now,
    as sorted JSON: part of the job's identity, so a run under another scale or objective is another job."""
    import json

    from . import env_runner

    if m is None:  # `m` (its manifest, or UNREAD when there was none) and `values`: already read and checked
        m = manifest_of(kind, environ)
    m = None if m is UNREAD else m
    if values is None:
        values = values_for(kind, given, environ, m=m, agent=agent) if kind.get("params") or given else {}
    hidden = env_runner.hidden_values(m, hub) if m is not None and hub is not None else {}
    return json.dumps({**values, **hidden}, sort_keys=True) if values or hidden or (m is not None and m.contract) else None


def register(settings=None, environ: Mapping[str, str] = os.environ) -> dict:
    """Every backend's proven entries made choices of the operations they implement, and each new kind of job an
    operation of its own; one no longer proven taken away (a setting that chose it goes back to the default). The same
    for every runner. Returns {"added", "removed", "reset"}."""
    import dataclasses

    from . import jobs, names, operations, options, own_trials

    wanted = {}
    for b in backends().values():
        if not b.registers:
            continue  # the app's own code registers it (builtin-cpu)
        for m in b.entries(environ):
            if jobs.is_job(m):
                continue  # a job is no picture operation
            found = b.in_use(m, environ)
            if found is not None and not own_trials.pending(found.m, environ):
                for member in b.expand(found):
                    wanted[member.m.key] = (b, member)
    adapters = {b.adapter() for b in BACKENDS.values() if b.registers}
    added, removed, reset = [], [], False
    for op in list(operations.OPERATIONS.values()):
        if op.id in registered():
            continue  # a new kind's own operation: below
        # The registered callables own these identities even after their manifests disappear or fail parsing.
        ours = {mid for (operation, mid), run in operations.ADAPTERS.items() if operation == op.id and run in adapters}
        want = {inst.m.id: (b, inst) for b, inst in wanted.values() if any(i.operation == op.id for i in inst.m.implements)}
        if not ours and not want:
            continue
        theirs, put, runs = (), {}, {}
        for mid, (b, inst) in sorted(want.items()):
            m = inst.m
            theirs += (operations.Impl(mid, _implementation_name(m), "model", accepts_of(m), "analysis", mid, tuple(m.licence),
                                       version=m.version, note="its own adapter, in its own environment",
                                       own_process=b.own_process),)
            runs[mid] = b.adapter()
            put[mid] = options.Option(  # said again when another version is in use
                mid, _implementation_name(m), f"contributed ({m.version}); {b.note(inst)}", uses=tuple(m.licence), model=m.key)
        gone = ours - set(want)
        if op.selected_by:  # never in place: the loop may be reading this group (catalogue)
            options.change_choices(op.selected_by, put=put, drop=gone)
        for mid in gone:
            if op.selected_by:
                if settings is not None and getattr(settings, op.selected_by, None) == mid:
                    settings.update({op.selected_by: options.Settings().__dict__[op.selected_by]})
                    reset = True
        added += sorted(set(want) - ours)
        removed += sorted(gone)

        def with_theirs(now, ours=ours, theirs=theirs):  # built on the operation as it is now: another writer's change kept
            if now is None:
                return None
            impls = tuple(i for i in now.implementations if i.id not in ours) + theirs
            same = {i.id: i for i in impls} == {i.id: i for i in now.implementations}
            return now if same else dataclasses.replace(now, implementations=impls)

        if operations.change_operation(op.id, with_theirs, put=runs, drop=gone):
            names.index.cache_clear()
    # New kinds of job: each proven one an operation of its own, run by the runner as any other; one no longer proven
    # takes its operation with it. An operation of the app's own is never replaced by one.
    want_kinds: dict[str, list] = {}
    for b, inst in wanted.values():
        i = implements(inst.m)
        if i is not None:
            want_kinds.setdefault(i.operation, []).append((b, inst, i))
    for op_id in sorted(registered() - set(want_kinds)):
        op = operations.OPERATIONS.get(op_id)
        ids = [one.id for one in (op.implementations if op is not None else ())]
        operations.change_operation(op_id, lambda now: None, drop=ids)
        removed += ids
        registered().discard(op_id)
        names.index.cache_clear()
    for op_id, found in sorted(want_kinds.items()):
        if op_id in operations.OPERATIONS and op_id not in registered():
            continue
        found = sorted(found, key=lambda one: one[1].m.id)
        impls = tuple(operations.Impl(inst.m.id, _implementation_name(inst.m), "model", accepts_of(inst.m), "analysis", None,
                                      tuple(inst.m.licence), version=inst.m.version,
                                      note="a new kind of job, in its own environment", own_process=b.own_process)
                      for b, inst, _i in found)
        first = found[0][2]
        new = operations.Operation(op_id, found[0][1].m.what or op_id, f"{first.output}/1", None, impls,
                                   empty_is_result=True, output=KIND_KEYS)
        before = operations.OPERATIONS.get(op_id)
        added += sorted({x.id for x in impls} - {x.id for x in (before.implementations if before else ())})
        if operations.change_operation(op_id, lambda now, new=new: new, put={inst.m.id: b.adapter() for b, inst, _i in found}):
            names.index.cache_clear()
        registered().add(op_id)
    return {"added": added, "removed": removed, "reset": reset}


# What a new kind's run may hold (operations._has_output: none of these holding anything is an empty result).
KIND_KEYS = ("picture", "rows", "text", "points", "polygons", "tracks", "data", "outputs")


def _implementation_name(m) -> str:
    return f"{m.name} ({m.key})" if "#" in m.key else m.name


def accepts_of(m: catalog.Manifest) -> tuple[str, ...]:
    """The prompts it takes, from what it declares (never assumed): a clip for a clip, else the view (a field); a
    format-1 tracks kind took a clip too."""
    first = next((d.type for d in m.data_inputs), "") if m.contract else next(
        (i.input for i in m.implements), "")
    return ("clip",) if first == "clip.2d.rgb8" else ("field",)


def made_words(output: str, got: dict) -> str:
    """What one run made, in a few words (its proof, the tool's answer)."""
    if "outputs" in got and isinstance(got["outputs"], dict):  # several outputs, each said
        return "; ".join(made_words(part.get("output", ""), part) for part in got["outputs"].values())
    if output not in OUTPUTS and output not in catalog.VOLUME_KIND_OUTPUTS:  # open types: its value, kept as data
        return f"{output} data"
    if output == "image.2d.rgb8":
        h, w = got["shape"][:2]
        return f"a picture {w}x{h}"
    if output == "table.objects":
        n = len(got["rows"])
        names = ", ".join(said_words(c["name"], 40) for c in got["columns"][:6])  # the adapter's own words: one plain line
        return f"a table of {n} row{'s' if n != 1 else ''} ({names})"
    if output == "text":
        return f"{len(got['text'])} characters of words"
    if output == "tracks.graph":
        n = len(got.get("tracks") or [])
        return f"{n} track{'s' if n != 1 else ''}"
    if output == "labels.instance.2d":
        n = int(got.get("count") or len(got.get("polygons") or []))
        return f"{n} outline{'s' if n != 1 else ''}"
    n = len(got["points"])
    return f"{n} point{'s' if n != 1 else ''}"


@dataclass(frozen=True)
class Bundled:
    """An algorithm bundled with the app (runner builtin-cpu): nothing to install, always there."""
    m: catalog.Manifest
    proof: dict


def _plug_builtin() -> None:
    """The app's own algorithms written in the contract (runner builtin-cpu): listed through the door with their forms,
    run by the operation adapter the app already registers for them (registers=False)."""
    def entries(environ):
        return [m for m in catalog.manifests().values() if m.runner == "builtin-cpu" and m.contract]

    def in_use(m, environ):
        return Bundled(m, {"ok": True, "says": "bundled with the app"}) if m is not None and m.runner == "builtin-cpu" \
            and m.contract else None

    plug(Backend("builtin-cpu", entries, in_use, lambda: None, lambda installed: "bundled with the app",
                 registers=False))


_plug_builtin()
