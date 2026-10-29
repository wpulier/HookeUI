"""The Models page's rows (P3 of docs/design/2026-10-12-the-open-catalog.md): every catalog entry, why it is on or off,
said as facts each with its reference, what it is used for, and the group it belongs in.

One row per entry (the reviewed catalog and the user's own, `mine/`), built from what the capabilities wire already
worked out (each role's choice and options, their checks) and the facts readiness reads (orchestrator/readiness.py):

- **facts**, each `{kind, ok, says, ref}` (and `service` for a key, `model` for files, so the page offers the step):
  - its key, with where it comes from (key_admin.py's facts: saved by this app, from your keys file, set outside the app);
  - its files: downloaded (revision, size, the folder), in an older cache, or not downloaded yet (how much, into where);
  - its code: installed, or how to install it;
  - this copy: allowed in this edition, or why not;
  - the app's last check of it (model_checks.py), when there is one.
- **usedFor**: each role it serves, with whether it is the current choice there (a fixed role's model in use counts).
- **group**: "needs-you" (the current choice of a role you choose uses it and it can't run: what "All set" is about, the
  review's correction 1: your selections only), "ready", "available" (it can be turned on with a step: a key, a
  download, an install, and no choice of yours needs it now, the models fixed in the code included: Trackastra for
  linking, SAM 3.1 for find all like this) or "unavailable" (not in this edition, or broken and unused).

Reading it costs what readiness costs (manifests, file stats, the keys the caller has): nothing loads, starts, calls or
downloads. Nothing here is pushed into a model's prompt; the agent reads it when asked (P4).
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog
from gpu_worker import model_files

from . import kinds, licences, observed, readiness

GROUP_ORDER = ("needs-you", "ready", "available", "unavailable")
WHERE = {"this-mac": "on this Mac", "cpu": "on this Mac", "cloud": "in the cloud"}


def _home(path: Path | str) -> str:
    return str(path).replace(str(Path.home()), "~")


home_path = _home  # a path as the page says it: "~/MicroscopeMind/catalog/mine/models"


def _link(m: catalog.Manifest) -> str:
    if m.source.get("hf"):
        return f"https://huggingface.co/{m.source['hf']}"
    return str(m.credit.get("link") or "")


def _uses(roles: list[dict]) -> dict[str, list[dict]]:
    """Each catalog model's uses: the roles whose options run it (Option.model, Option.also) and the fixed roles that
    name it, with whether it is the current choice there."""
    from . import options

    out: dict[str, list[dict]] = {}

    def add(model_id: str, use: dict) -> None:
        """One use per role: its options that run the model folded together, current when any of them is."""
        found = next((u for u in out.get(model_id, []) if u["role"] == use["role"]), None)
        if found is None:
            out.setdefault(model_id, []).append({**use, "options": [use.pop("label")]})
            return
        if use["label"] not in found["options"]:
            found["options"].append(use["label"])
        found["current"] = found["current"] or use["current"]
        found["check"] = found.get("check") or use.get("check")

    for role in roles:
        if role.get("fixed"):
            for which, entry in (("model", role.get("primary") or {}), ("fallback", role.get("fallback") or {})):
                if not entry:
                    continue
                active = bool(entry.get("active")) if which == "fallback" else not (role.get("fallback") or {}).get("active")
                add(entry.get("model", ""), {"role": role["id"], "title": role["title"], "option": which,
                                             "label": entry.get("label", ""), "current": active, "fixed": True,
                                             "check": entry.get("check")})
            continue
        group = options.GROUPS.get(role["id"], {})
        for o in role.get("options", []):
            opt = group.get(o["id"])
            if opt is None:
                continue
            for model_id in [m for m in (opt.model, *opt.also) if m]:
                add(model_id, {"role": role["id"], "title": role["title"], "option": o["id"],
                               "label": o.get("said") or o.get("label", ""), "current": o["id"] == role.get("current"),
                               "fixed": False, "check": o.get("check") if model_id == (opt.model or opt.also[0]) else None})  # its own model's
    return out


FIXED_OPTION = {"model": "primary", "fallback": "fallback"}  # a fixed role's options as capability_shelf names them


def _option_id(use: dict) -> str:
    return FIXED_OPTION.get(use["option"], use["option"]) if use.get("fixed") else use["option"]


def abilities(m: catalog.Manifest, used: list[dict], environ: Mapping[str, str] = os.environ) -> list[dict]:
    """What an entry does in the app (Will's consolidation, 2026-10-13): every capability whose roles this entry really
    fills, read from the registered options that run it (`_uses`, never guessed from its name). Each says which role and
    which exact option of it is this entry's; none carries a Try of its own (a role's one demonstration is `tries`: a
    capability's title never promises an action its role's Try may not be). A card's details list these."""
    from . import capabilities

    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for use in used:
        for c in capabilities.load().get("capabilities", []):
            if use["role"] not in (c.get("roles") or []) or (c["id"], use["role"]) in seen:
                continue
            seen.add((c["id"], use["role"]))
            out.append({"id": c["id"], "title": c["title"], "what": c["what"], "actor": c["actor"],
                        "say": str((c.get("how") or {}).get("say") or ""),
                        **({"preview": f"/api/capabilities/media/{c['media']}"} if c.get("media") else {}),
                        "role": use["role"], "option": _option_id(use), "current": bool(use.get("current"))})
    kind = kind_info(m, environ)
    if kind is not None:
        out.append({"id": f"kind:{kind['operation']}", "title": m.name, "what": m.what, "actor": "you", "say": "",
                    "role": kind["operation"], "option": m.id, "current": True})
    for member in bundle_kinds(m, environ):
        out.append({'id': member['key'], 'title': member['name'], 'what': member['what'], 'actor': 'you',
                    'say': '', 'role': member['operation'], 'option': member['key'], 'current': member['proved']})
    return out


def bundle_kinds(m, environ):
    if not m.members:
        return []
    from . import bundle_runtime, env_runner
    from gpu_worker import bundle_contract
    installed = env_runner.in_use(m, environ)
    owner = installed.m if installed else m
    result = []
    for member in bundle_runtime.declarations(owner):
        active = bundle_contract.view(owner, member)
        description = _kind_description(active, installed, env_runner._note(installed) if installed else '')
        if description:
            result.append({'key': active.key, 'name': active.name, 'what': active.what, **description})
    return result


def kind_info(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict | None:
    """A C6 adapter's declared input/output, shared by its card and the exact trial action."""
    try:
        from . import kinds
    except ImportError:
        return None
    # Whatever runs it (the one door's backends: python-env, Fiji, the app's own), asked whether it is proven here.
    backend = kinds.backends().get(m.runner)
    from . import fiji_display
    installed = (fiji_display.in_use(m, environ) if m.runner == 'fiji' else
                 backend.in_use(m, environ) if backend is not None else None)
    active = installed.m if installed is not None else m
    return _kind_description(active, installed, backend.note(installed) if installed is not None and m.runner != 'python-env' else '')


def _kind_description(active, installed, proof=''):
    from . import kinds
    kind = kinds.door_entry(active)
    if kind is None:
        return None
    words = {'image.2d.rgb8': 'current still image', 'clip.2d.rgb8': 'recent frames',
             'volume.3d': 'selected volume'}
    outputs = getattr(catalog, 'VOLUME_KIND_OUTPUTS', {}) if kind.input == 'volume.3d' else kinds.OUTPUTS
    from . import admission
    typed = admission.typed(active)  # several or native inputs, each chosen and bound at Run (H01 slice d)
    # What it makes in its own declared words (its output's label, "Bright regions (not cells)", and tooltip): the run
    # page says these, never words of the app's own about it.
    made = next((o for o in getattr(active, 'outputs', ()) or () if o.type == kind.output), None)
    # Its declared settings (its non-picture [[inputs]], as every capability's form gets them: capabilities.field_of),
    # so its run page shows them before it is set up too (S11; disabled there until it is), never invented.
    from .capabilities import field_of
    fields = [field_of(kinds.param_wire(p)) for p in getattr(active, 'params', ()) or ()]
    return {'operation': kind.operation, 'input': kind.input, 'output': kind.output,
            **({'fields': fields} if fields else {}),
            'gives': outputs.get(kind.output, kind.output),
            **({'made': made.label} if made is not None and made.label else {}),
            **({'madeNote': made.tooltip} if made is not None and made.tooltip else {}),
            'on': 'chosen saved inputs' if typed else words.get(kind.input, kind.input),
            **({'typed': True, 'inputs': [{'id': p.id, 'type': p.type} for p in admission.data_inputs(active)]}
               if typed else {}),
            'proved': installed is not None, **({'activeVersion': active.version} if installed is not None else {}),
            **({'proof': proof} if proof else {})}


def execution_block(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict | None:
    from . import kinds, own_trials

    backend = kinds.backends().get(m.runner)
    from . import fiji_display
    installed = (fiji_display.in_use(m, environ) if m.runner == 'fiji' else
                 backend.in_use(m, environ) if backend else None)
    active = installed.m if installed else m
    if kinds.door_entry(active) is not None and (why := kinds.takes_why(active)):
        return {'action': 'unsupported', 'why': why}
    if own_trials.pending(active, environ):
        if active.members:
            return {'action': 'review-bundle', 'why': 'Review each tool’s checked results before enabling this package.'}
        job = next((job for job, (_, output) in own_trials.JOBS.items()
                    if any(i.input == 'image.2d.rgb8' and i.output == output for i in active.implements)), None)
        if job is None:
            return {'action': 'unsupported', 'why': 'Comparison does not support this capability’s input and output yet.'}
        picture = own_trials.awaiting_picture(m.key, environ)
        return {'action': 'choose-comparison-input' if picture else 'compare', 'job': job,
                'why': 'Choose a picture for comparison before using this capability.' if picture else
                       'Compare your picture and accept the result before using this capability.'}
    return None


def tries(m: catalog.Manifest, used: list[dict], environ: Mapping[str, str] = os.environ) -> list[dict]:
    """The Try on this card, one per role it fills: that role's demonstration with exactly this entry's option (the
    studio's `capability-try`, job and option, which refuses an option without a current passed check and asks for what
    the role needs first), labelled by the role, never by one of its capabilities. A new kind of job: its own exact
    `capability-run`."""
    from .capability_trials import TRIALS

    out = [{"role": use["role"], "option": _option_id(use), "title": f"Try {use['title']} with {m.name}",
            "try": {"command": "capability-try", "job": use["role"], "option": _option_id(use)}} for use in used if use["role"] in TRIALS]
    kind = kind_info(m, environ)
    if kind is not None:
        out.append({"role": kind['operation'], "option": m.id, "title": f"Try {m.name} on the {kind['on']}",
                    "try": {"command": "capability-run", "model": m.key}})
    for member in bundle_kinds(m, environ):
        out.append({'role': member['operation'], 'option': member['key'],
                    'title': f"Try {member['name']} on the {member['on']}",
                    'try': {'command': 'capability-run', 'model': member['key']}})
    return out


def _setup_plan(role: dict, option: dict) -> dict:
    """The same exact recheck plan and receipt for role-backed and roleless cards."""
    from . import capability_shelf

    proved = capability_shelf._proven(option)
    chosen = capability_shelf.plan(role, [{**option, 'check': None} if proved else option])
    return {'plan': chosen, 'ready': proved, 'recheck': proved, 'check': option.get('check'),
            'recipe': capability_shelf.recipe(option), 'current': role.get('current', '')}


def setups(used: list[dict], roles: list[dict]) -> list[dict]:
    """Exact option recipes for this entry, independent of the role's current/default choice."""
    from . import capability_shelf, capability_trials

    out = []
    for use in used:
        role = next((r for r in roles if r['id'] == use['role']), None)
        option = next((o for o in capability_shelf._options(role) if o['id'] == _option_id(use)), None) if role else None
        if option is None:
            continue
        planned = _setup_plan(role, option)
        out.append({'role': role['id'], 'title': role['title'], 'option': option['id'], **planned,
                    'canTry': planned['recheck'] and role['id'] in capability_trials.TRIALS})
    return out


def entry_check(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict | None:
    """The functional receipt of a tool without a model role; installation alone is never proof."""
    if m.runner == 'python-env':
        from . import env_runner
        found = env_runner.in_use(m, environ)
        if found is not None:
            p = found.proof
            return {'state': 'works', 'at': p.get('at', ''), 'detail': p.get('says') or
                    f'Adapter check passed, active version {found.m.version}',
                    **({'ms': round(p['seconds'] * 1000)} if isinstance(p.get('seconds'), (int, float)) else {})}
    if m.runner == 'fiji':
        from . import fiji_display as fiji
        if m.namespace:
            found = fiji.in_use(m, environ)
            if found is not None:
                return {'state': 'works', 'at': found.proof.get('at', ''),
                        'detail': f'Confined functional proof passed on Fiji generation {found.n}'}
            return None
        n = fiji.current(environ)
        receipt = fiji.receipt(n, environ) if n is not None else None
        p = (receipt or {}).get('proofs') or {}
        proven = bool(p.get('smoke')) if m.kind == 'tool' else not fiji.proven(m.key, environ)
        if receipt and proven:
            return {'state': 'works', 'at': receipt.get('at', ''), 'detail': f'Functional proof passed on Fiji generation {n}'}
    if m.runner == 'cellpose-file':
        from . import mine
        saved = mine.state(m, environ)
        if saved.get('on') and (saved.get('check') or {}).get('ok'):
            return {'state': 'works', 'at': saved['check'].get('at', ''), 'detail': 'Its own model check passed'}
    return None


def shareable(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    if m.runner == 'python-env':
        from . import env_runner
        from .bundle_admission import integration_problem

        # Export retains the accepted snapshot while a replacement is prepared.
        installed = env_runner.in_use(m, environ) if m.members else None
        return integration_problem(installed.m if installed is not None else m) is None
    if m.runner == 'fiji' and m.fiji:
        from . import fiji_sandbox, fiji_display

        return fiji_sandbox.available() and fiji_display.in_use(m, environ) is not None
    return False


def proof_step(m: catalog.Manifest) -> dict | None:
    from .bundle_admission import local_workflow
    if local_workflow(m):
        return {'kind':'workflow', 'says':'Observe controls, Save tool and reopen on a saved Project', 'model':m.key}
    if m.runner in ('python-env', 'fiji'):
        return {'kind': 'install', 'says': f'Check {m.name} by running its own functional proof',
                'model': m.key, 'recheck': True}
    if m.runner == 'cellpose-file':
        return {'kind': 'activate', 'says': 'Check its weights on the public synthetic example', 'model': m.key}
    return None


def own_proof(role_id: str, option_id: str, environ: Mapping[str, str] = os.environ) -> tuple[dict | None, dict | None]:
    """A choice the app has no check of its own for (a contributed or AI-made capability, which kinds.py makes a choice
    only once its own functional proof passed; model_checks builds no entry for it): that proof is its functional check,
    and running it again is how it is checked again. (its check, its recheck step), or (None, None) for one of the app's
    own choices, which its own checks cover.

    F-L8-2: an AI-made tool was Ready on the Models page and in Tools (its own proof passed), while its role's setup
    asked for "its functional check on the public synthetic example", which no adapter of the app's could run, so it
    showed "Needs you: no functional check adapter yet" under Ready. One readiness now, on every surface."""
    from . import options

    option = options.GROUPS.get(role_id, {}).get(option_id)
    if not getattr(option, "model", "") or not kinds.contributed(role_id, option_id):
        return None, None
    m = catalog.get(option.model)
    if m is None:
        return None, None
    step = proof_step(m)
    if step is None:
        return None, None
    return entry_check(m, environ), step


def face(m: catalog.Manifest, r: readiness.Readiness, used: list[dict],
         environ: Mapping[str, str] = os.environ) -> dict:
    """The card's face (Will's consolidation): its state, the line that says why, and the one action that changes it.
    Obtained, runnable and proved are kept apart (Codex's review of 14054603): "on" only with a current passed check of
    this build; ready to run with none is "unchecked", its action Check it here; data runs nothing, so it is "obtained"
    with no check. Not ready: its next step."""
    from . import own_trials, env_runner
    installed = env_runner.in_use(m, environ) if m.runner == "python-env" else None
    if r.status == "ready" and own_trials.pending(installed.m if installed else m, environ):
        if own_trials.awaiting_picture(m.key, environ):
            return {"state": "unchecked", "says": "Setup checked; choose a picture of yours and Set up for comparison (Add "
                    "your own), then Use it to add the choice", "action": None}
        return {"state": "unchecked", "says": "Setup checked; compare your chosen picture, then Use it to add the choice", "action": None}
    check = next((u.get("check") for u in used if u.get("check")), None) or entry_check(m, environ)
    if r.status == "ready":
        keys = [n.split(":", 1)[1] for n in (*m.needs_to_get, *m.needs_to_run) if n.startswith("key:")]
        change = [{"kind": "change-key", "says": "Change key", "key": k} for k in keys]
        if m.kind == "runtime":
            return {"state": "obtained", "says": "Dependencies prepared; declared import checks passed (no operation proof)",
                    "action": None, "also": change}
        if m.kind == "data":
            return {"state": "obtained", "says": "Downloaded and checked by its files (data runs nothing)", "action": None,
                    "also": change}
        kind = kind_info(m, environ) if m.runner == "python-env" else None  # bundled and Fiji ones: their checks below
        if kind is not None:
            if kind['proved']:
                return {'state': 'on', 'says': f"On · {kind['proof']}" if kind.get('proof') else
                        f"On · adapter check passed, active version {kind['activeVersion']}",
                        'action': None, 'also': change}
            return {'state': 'needs-setup', 'says': 'Its adapter needs a functional check before it can run',
                    'action': {'kind': 'install', 'says': 'Turn on and check', 'model': m.key}, 'also': change}
        recovery = proof_step(m)
        if recovery is not None:
            if check and check.get('state') == 'works':
                return {'state': 'on', 'says': 'On · ' + check.get('detail', 'Functional check passed'),
                        'action': None, 'also': [recovery] + change}
            return {'state': 'unchecked', 'says': 'Ready to run; its functional proof is not recorded here',
                    'action': recovery, 'also': change}
        proved = bool(check) and check.get("state") == "works" and not check.get("earlierBuild")
        if not proved:
            said = (f"Last check: {check.get('state')}" + (" (an earlier build)" if check.get("earlierBuild") else "")
                    if check else "Ready to run; not checked on this Mac yet")
            return {"state": "unchecked", "says": said, "action": {"kind": "check", "says": "Check it here", "model": m.key},
                    "also": change}
        says = "On · last check: works" + (f" · {check['at'][11:16]}" if check.get("at") else "")
        return {"state": "on", "says": says, "action": None,
                "also": [{"kind": "check", "says": "Check again", "model": m.key}] + change}
    first = r.steps[0].wire() if r.steps else None
    return {"state": r.status, "says": (first or {}).get("says") or r.word, "action": first, "also": []}


def _key_fact(key: str, facts: Mapping[str, dict], have: bool, only_to_get: bool,
              environ: Mapping[str, str] = os.environ) -> dict:
    f = facts.get(key) or {}
    short = f.get("short") or key
    obs = observed.latest("key", key, environ)
    tested = f" · {obs['says']} (checked {observed.clock(obs.get('at', ''))})" if obs and obs.get("state") != "no" else ""
    if obs and obs.get("state") == "no":  # there, and refused when tested: not a working key
        return {"kind": "key", "ok": False, "says": f"Your {short} key: {obs['says']} (checked {observed.clock(obs.get('at', ''))})",
                "service": f.get("service", ""), "ref": f.get("getKey", ""), "observed": "key"}
    if have:
        return {"kind": "key", "ok": True, "says": f"Your {short} key: {f.get('sourceSays') or 'there'}{tested}",
                "service": f.get("service", ""), "ref": f.get("getKey", ""), **({"observed": "key"} if obs else {})}
    why = " (only to download it)" if only_to_get else ""
    return {"kind": "key", "ok": False, "says": f"Your {short} key isn't added yet{why}",
            "service": f.get("service", ""), "ref": f.get("getKey", "")}


def _read(path: Path | None) -> tuple[bytes | None, str]:
    """A file's bytes and why it couldn't be read: (None, "") when it isn't there (or was removed while the page is built),
    (None, why) when it is there and can't be read (a permission). Contained either way: one entry's file never breaks
    the catalog's answer."""
    if path is None:
        return None, ""
    try:
        return path.read_bytes(), ""
    except FileNotFoundError:
        return None, ""
    except OSError as exc:
        return None, exc.strerror or type(exc).__name__


def _classes_said(data: bytes, names: dict) -> tuple[list | None, str, str]:
    """A classes.json as the template's adapter reads it: (notThings, source, why it can't be used). The same checks as
    catalog/templates/hf-segmentation's `_not_things`, so the card never endorses a file the adapter would refuse."""
    import json

    try:
        said = json.loads(data)
        ids = said["notThings"]
        if not isinstance(ids, list) or not all(isinstance(i, int) and not isinstance(i, bool) for i in ids):
            return None, "", "notThings must be a list of class numbers"
    except (ValueError, KeyError, TypeError) as exc:
        return None, "", f"it can't be read ({exc})"
    unknown = sorted(set(ids) - {int(k) for k in names}) if names else []
    if unknown:
        return None, "", f"it names classes the model doesn't have: {unknown}"
    return ids, str(said.get("source") or ""), ""


def classes_fact(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> list[dict]:
    """What a semantic model's classes mean, said where it can't name them itself: a model whose config names its classes
    only by number (Coralscapes' SegFormer: "13": 13) counts every class as something found, unless the person says which
    aren't things in classes.json beside its adapter (catalog/templates/hf-segmentation). Said of the generation that
    runs (env_runner.in_use: its installed snapshot and pinned files), never of an edit not installed yet, which is
    said apart (a changed classes.json is a new version, proven before it runs)."""
    import json

    from . import env_runner

    if "hf-segmentation" not in str((m.pins or {}).get("adapter") or ""):
        return []
    installed = env_runner.in_use(m, environ) if m.runner == "python-env" else None
    try:
        files = env_runner.files_dir(installed, environ) if installed else model_files.revision_dir(m, environ)
        names = json.loads((files / "config.json").read_text()).get("id2label") or {}
    except (OSError, ValueError, model_files.NotDownloaded):
        return []  # not downloaded yet: nothing to read
    if not names or any(str(v).strip() != str(k).strip() for k, v in names.items()):
        return []  # it names its classes: background is the classes named so
    running = installed.entry / "classes.json" if installed else None
    edited = Path(m.folder) / "classes.json" if m.folder else None
    out: list[dict] = []
    (running_said, running_why), (edited_said, edited_why) = _read(running), _read(edited)  # each read once, contained
    if running_why:
        out.append({"kind": "classes", "ok": False, "says": f"Its installed classes.json can't be read ({running_why}): "
                    "the adapter won't run until it can", "ref": _home(running)})
    elif running_said is not None:
        ids, source, why = _classes_said(running_said, names)
        out.append({"kind": "classes", "ok": False, "says": f"Its classes.json can't be used: {why}", "ref": _home(running)}
                   if ids is None else
                   {"kind": "classes", "ok": True, "says": f"Classes it doesn't count as things: {ids}"
                    + (f" (from {source})" if source else ""), "ref": _home(running)})
    else:
        out.append({"kind": "classes", "ok": None,
                    "says": f"Its {len(names)} classes are numbers only (its config names none), so every class counts as "
                            "something found. If some aren't things (background, too dark), say which in classes.json in "
                            "its folder, from its model or dataset card"})
    if edited_why:
        out.append({"kind": "classes", "ok": False, "ref": _home(edited),
                    "says": f"Its classes.json in your folder can't be read ({edited_why})"})
    elif edited_said is not None and edited_said != running_said:
        ids, _, why = _classes_said(edited_said, names)
        what = f"it can't be used ({why})" if ids is None else f"it would leave out {ids}"
        out.append({"kind": "classes", "ok": False if ids is None else None, "ref": _home(edited),
                    "says": f"Its classes.json in your folder isn't in use yet: {what}. "
                            + ("Install the new version to check it and use it" if installed else "Turning it on installs it")})
    return out


def facts(m: catalog.Manifest, key_facts: Mapping[str, dict], have, checks: list[dict],
          environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Why it is on or off, each fact with its reference (see the module's docstring)."""
    from .operations import INSTALLED

    out: list[dict] = []
    if m.members:
        from gpu_worker.bundle_contract import parse_members
        for member in parse_members(list(m.members), m):
            if member.resources:
                out.append({'kind': 'resources', 'ok': None,
                            'says': f"{member.name}: up to {member.resources['memory_mb'] / 1024:g} GiB memory per open tool. "
                                    'Available memory is checked before starting.'})
    if m.pins.get("output_conversion"):
        out.append({"kind": "output", "ok": None, "says": str(m.pins["output_conversion"])})
    got, missing = readiness.obtained(m, environ)
    if m.namespace == "mine":  # one of yours (mine.py): its entry, what runs it, its maker's licence words
        out.append({"kind": "entry", "ok": True, "says": "Its entry reads: model.toml in your folder",
                    "ref": _home(model_files.folder(m, environ))})
        if m.runner == "python-env":
            from .env_runner import adapter_fact

            out.append(adapter_fact(m, environ))
            out.extend(classes_fact(m, environ))
        else:
            out.append({"kind": "runs", "ok": None if m.runner else False,
                        "says": "Cellpose's own code runs its weights, on this Mac, in a worker of its own, with no keys"
                        if m.runner == "cellpose-file" else "It needs an adapter written for it before it can run"})
        if m.licence_note:
            out.append({"kind": "licence", "ok": None, "says": f"Its maker's licence: {m.licence_note}"})
    if m.channel:  # it came through a channel (channels.py): which, at what commit, and whether it is reviewed
        from . import channels

        c = next((c for c in channels.wire(environ) if c["name"] == m.channel), {})
        if c.get("reviewed"):
            out.append({"kind": "entry", "ok": True, "ref": c.get("url", ""),
                        "says": f"From the reviewed catalog at {c.get('commit', '')[:7]}, newer than this app"})
        else:
            at = "" if c.get("source") == "shared-file" else f" at {c.get('commit', '')[:7]}"  # a file's hash says nothing
            out.append({"kind": "entry", "ok": True if c.get("trusted") else None, "ref": c.get("url", ""),
                        "says": f"From {channels.said(m.channel, environ)}{at}: not reviewed here"
                                + ("; you allowed code from it" if c.get("trusted") else ""),
                        **({} if c.get("trusted") else {"trust": m.channel, "trustLabel": c.get("label") or m.channel})})
    if m.service:
        service = catalog.services().get(m.service)
        if service is not None and service.key:
            out.append(_key_fact(service.key, key_facts, have(service.key), False, environ))
    for need in m.needs_to_run:
        if need.startswith("key:") and not any(f.get("kind") == "key" and need[4:] in f.get("says", "") for f in out):
            key = need.split(":", 1)[1]
            if not (m.service and catalog.services().get(m.service) and catalog.services()[m.service].key == key):
                out.append(_key_fact(key, key_facts, have(key), False, environ))
    from . import observed

    for need in m.needs_to_get:
        if need.startswith("key:") and got is False:
            out.append(_key_fact(need.split(":", 1)[1], key_facts, have(need.split(":", 1)[1]), True, environ))
        elif need.startswith("licence:"):
            # What Hugging Face said when asked (observed.py), with when; asked again on its own, or on Check again.
            obs = observed.latest("access", m.key, environ)
            state = (obs or {}).get("state")
            when = f" (checked {observed.clock(obs.get('at', ''))})" if obs else ""
            if got is not False and state is None:
                continue  # downloaded: its licence was accepted to get it
            if observed.askable(m.key):
                local = state in (None, "unknown") and observed.asking_refused()  # the Example project asks no one (F-L8-4)
                out.append({"kind": "licence", "ok": True if state == "yes" else False if state in ("no", "no-key") else None,
                            "says": observed.NOT_ASKED_HERE if local else (obs["says"] + when) if obs
                            else "Its licence on Hugging Face: not confirmed yet (asked in a moment)",
                            "ref": need.split(":", 1)[1], "observed": "access"})
            else:  # a page no probe can read: only the user can say they accepted it (observed.SOURCES "user")
                out.append({"kind": "licence", "ok": True if state == "yes" else None,
                            "says": (obs["says"] + when) if obs else "Its licence: accept it on its page, then say so here",
                            "ref": need.split(":", 1)[1], "confirm": "access"})
    files = model_files.files(m)
    if files:
        owner = model_files.owner(m)
        total = sum(f["bytes"] for f in files)
        revision = catalog.short_revision(owner)
        folder = model_files.folder(owner, environ)
        where = readiness.where(m, environ) if got else None
        if where == "folder":
            out.append({"kind": "files", "ok": True, "ref": _home(model_files.revision_dir(owner, environ)),
                        "says": f"Downloaded: {readiness.size_words(total)}, revision {revision}"})
        elif got:
            from .bundle_admission import scientific

            if scientific(m):
                # Scientific workers admit only published, verified artifacts; ordinary GPU loaders may
                # still use legacy caches. The existing download command adopts cached bytes atomically.
                out.append({"kind": "files", "ok": False, "ref": _home(folder), "model": owner.key,
                            "cached": True,
                            "says": f"Found in an older cache at revision {revision}. Prepare cached files to verify "
                                    "and publish the pinned files before opening this tool."})
            else:
                # Said to a person: where its files are, never a command to type (`make catalog-adopt` moves them).
                out.append({"kind": "files", "ok": True, "ref": _home(folder),
                            "says": f"In an older cache on this Mac, at its pinned revision {revision}"})
        else:
            checked = model_files.verification(owner, environ)
            if checked is not None and checked.get("ok") is False:  # published, then found changed (downloads.verify_all)
                says = (f"A file changed since it was downloaded ({checked.get('problem')}, checked "
                        f"{observed.clock(checked.get('at', ''))}): download it again")
            else:
                says = f"Not downloaded yet: {readiness.size_words(missing)}, revision {revision}"
            out.append({"kind": "files", "ok": False, "ref": _home(folder), "model": owner.key, "says": says})
    if m.runner == "fiji" and m.kind == "tool":  # its generations (fiji_generations.py): in use, what it holds, proven
        from . import fiji_display as fiji_generations

        n = fiji_generations.current(environ)
        if n is not None:
            r = fiji_generations.receipt(n, environ) or {}
            earlier = [g["n"] for g in fiji_generations.generations(environ) if g["state"] == "kept" and g["n"] < n]
            out.append({"kind": "generation", "ok": True, "ref": _home(fiji_generations.root(environ) / str(n)),
                        "says": f"In use: generation {n} ({', '.join(s['name'] for s in r.get('sites', []))}; "
                                f"{len(r.get('files', []))} files, each by its checksum; proven: Fiji "
                                f"{r.get('proofs', {}).get('smoke', '')})",
                        **({"rollback": earlier[-1]} if earlier else {})})
    for need in m.needs_to_run:
        if need.startswith("install:") and m.env:  # its own Python, made by the app (envs.py)
            from . import envs

            st = envs.state(m, environ)
            from . import channels
            from .bundle_admission import local_problem, local_workflow
            from . import env_runner
            trial = local_workflow(m) and env_runner.in_use(m, environ) is None
            if local_problem(m) or trial:
                prepared = st['state'] == 'ready'
                out.append({'kind': 'code', 'ok': None,
                            'says': ('Prepared: choose this tool for saved Project data, use its controls, Save tool and reopen; then review its local workflow in Models.'
                                     if prepared and trial else 'Dependencies prepared for private review; workflow qualification remains open.'
                                     if prepared else st['says']),
                            **({'prepare': m.key} if channels.trusted(m, environ) and not readiness.misfit(m) else {})})
                continue
            what = need.split(":", 1)[1]
            by_hand = st["state"] != "ready" and what in INSTALLED and not INSTALLED[what]()
            from . import env_runner

            found = env_runner.in_use(m, environ) if m.runner == "python-env" else None
            if found is None or found.current:  # else the update's own fact below says this version isn't installed yet
                out.append({"kind": "code", "ok": True if st["state"] == "ready" or by_hand else False,
                            "ref": _home(st.get("folder", "")),
                            "says": st["says"] if not by_hand else
                            "A Python made by hand runs it (not the catalog's pinned one): Install makes that one",
                            **({"install": m.key} if by_hand else {})})
            if m.runner == "python-env":  # its own adapter, proven by running it; a new version beside the one in use
                if found is not None:
                    p = found.proof
                    what = p.get("says") or f"found {p.get('found')} on {env_runner.proof_input_says(p)} ({p.get('picture')})"
                    out.append({"kind": "check", "ok": True, "ref": _home(str(found.folder)),
                                "says": f"Its proof{'' if found.current else f' ({found.m.version}, in use)'}: {what} "
                                        f"({observed.clock(p.get('at', ''))})"})
                    if not found.current:
                        # No Install link once the newest is installed and proven: the user went back from it, and
                        # Use the newest switches to it (C7 Window B, finding 4).
                        waiting = env_runner.update_waiting(m, environ)
                        if waiting:  # "" when the catalog is behind what maintenance made active (ticket 59)
                            out.append({"kind": "code", "ok": True, "says": waiting,
                                        **({} if env_runner.newest_installed(m, environ) else {"install": m.key})})
                        failed = env_runner.update_failed(m, environ)
                        if failed:  # said on the card, not only in Activity (finding 1)
                            out.append({"kind": "check", "ok": False, "says": failed})
                elif st["state"] == "ready":
                    out.append({"kind": "check", "ok": False, "says": env_runner.ready(m, environ)})
        elif need.startswith("install:") and m.runner == "fiji" and m.kind == "tool-function":  # a Fiji binding
            from . import fiji_display as fiji_generations

            why = fiji_generations.proven(m.key, environ)
            n = fiji_generations.current(environ)
            proof = ((fiji_generations.receipt(n, environ) or {}).get("proofs") or {}).get(m.id, "") if n else ""
            if m.namespace:
                from . import fiji_display

                installed = fiji_display.in_use(m, environ)
                if installed is not None:
                    n, proof = installed.n, installed.proof
                    if not installed.current:
                        out.append({"kind": "code", "ok": True,
                                    "says": "The previous proven version is still in use. Check the candidate before switching.",
                                    "install": m.key})
            proof = proof.get("summary", "native pixel proof") if isinstance(proof, dict) else proof
            out.append({"kind": "check", "ok": not why,
                        "says": f"Proven on Fiji generation {n}: {proof}" if not why else why})
        elif need.startswith("install:"):
            what = need.split(":", 1)[1]
            why = INSTALLED[what]() if what in INSTALLED else f"not installed here ({what})"
            out.append({"kind": "code", "ok": not why, "says": "Its code is installed" if not why else why})
        elif need.startswith("weights:"):
            from . import finetuned

            why = finetuned.missing(need.split(":", 1)[1])
            out.append({"kind": "weights", "ok": not why, "says": "Its weights were made here" if not why else why})
    edition = licences.why_not(m.licence)
    out.append({"kind": "edition", "ok": not edition,
                "says": f"Allowed in this edition ({licences.edition()})" if not edition else edition})
    if m.namespace == "mine" and m.runner == "cellpose-file":
        from . import mine

        st = mine.state(m, environ)
        last = st.get("check")
        if last:
            out.append({"kind": "check", "ok": bool(last.get("ok")),
                        "says": ("Its check passed: " if last.get("ok") else "Its check didn't pass: ")
                                + "; ".join(s.get("says", "") for s in last.get("steps", []))})
        out.append({"kind": "on", "ok": st["on"], "says": "Turned on: a choice under Cells" if st["on"] else
                    "Off: nothing runs it until you turn it on, which checks it first"})
        return out
    check = next((c for c in checks if c), None)
    if check:
        from .model_checks import at_words

        ok = check.get("state") == "works"
        out.append({"kind": "check", "ok": ok if check.get("state") in ("works", "broken") else None,
                    "says": (f"Worked when checked at {at_words(check.get('at', ''))}" if ok else
                             f"Its last check failed: {check.get('why') or 'it failed'}" if check.get("state") == "broken"
                             else f"Last check: {check.get('why') or check.get('state')}")
                            + (" (an earlier build)" if check.get("earlierBuild") else "")})
    return out


def _channel_fields(name: str, environ: Mapping[str, str]) -> dict:
    """Which channel a row came through, with its source and label, so the studio names it as what it is ("from a
    shared file", never "community: shared-1a2b3c"; model-status.ts `sourceWords`)."""
    from . import channels

    return {"channel": name, "channelSource": channels.source(name, environ), "channelLabel": channels.label(name, environ)}


def rows(roles: list[dict], key_facts: Mapping[str, dict], keys: Mapping[str, bool], downloads: list[dict] | None = None,
         environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Every catalog entry as the Models page shows it (see the module's docstring), in group order, then by name."""
    have = lambda k: bool(keys.get(k))  # noqa: E731
    uses = _uses(roles)
    fetching = {d.get("model"): d for d in (downloads or [])}
    out = []
    entries = {**catalog.manifests(), **catalog.mine(environ)}
    with readiness.batch():
        for key, m in entries.items():
            used = uses.get(key, [])
            checks = [u.get("check") for u in used if u.get("check")]
            r = readiness.model(key, keys, environ)
            broken_check = next((c for c in checks if c.get("state") == "broken" and not c.get("earlierBuild")), None)
            r = readiness.broken(r, broken_check)
            in_use = any(u["current"] for u in used)
            chosen = any(u["current"] and not u["fixed"] for u in used)  # a selection of yours, not a fixed model
            if r.status == "ready":
                group = "ready"
            elif r.status == "unavailable":
                group = "unavailable"
            elif chosen:
                group = "needs-you"
            else:
                group = "available" if r.status == "needs-setup" else "unavailable"
            owner = model_files.owner(m)
            row = {"id": key, "name": m.name, "runs": m.runs, "where": WHERE.get(m.runs, m.runs), "what": m.what,
                   "kind": m.kind, "version": m.version, "credit": dict(m.credit), "link": _link(m),
                   "licence": licences.licence_text(m.licence), "tier": licences.tier(m.licence),
                   "mine": m.namespace == "mine", **(_channel_fields(m.channel, environ) if m.channel else {}),
                   # An adapter the builder drafted says so on its card (C4; drafts.py writes it into its component).
                   **({"draftedByAi": True} if "drafted by AI" in str((m.component or {}).get("what", "")) else {}), **r.wire(), "group": group, "inUse": in_use, "chosen": chosen,
                   "facts": facts(m, key_facts, have, checks, environ),
                   "usedFor": [{k: v for k, v in u.items() if k != "check"} for u in used],
                   "size": sum(f["bytes"] for f in model_files.files(m)),
                   "files": [{"path": f["path"], "bytes": f["bytes"]} for f in model_files.files(m)][:40],
                   "folder": _home(model_files.folder(owner, environ)) if model_files.files(m) else "",
                   "revision": catalog.revision_of(owner) if model_files.files(m) else "", "shareable": shareable(m, environ)}
            if m.env:
                from . import envs

                destination = envs.state(m, environ).get("folder")
                if destination:
                    row["installFolder"] = _home(destination)
            elif m.runner == "fiji":
                from . import fiji_display as fiji_generations

                row["installFolder"] = _home(fiji_generations.root(environ))
            blocked = execution_block(m, environ) if r.status == 'ready' else None
            if blocked:
                row['execution'] = blocked
                row['group'] = 'unavailable' if blocked['action'] == 'unsupported' else 'available'
            row["abilities"] = abilities(m, used, environ)
            row["tries"] = [] if blocked else tries(m, used, environ)
            row["setups"] = setups(used, roles)
            row['demonstration'] = ({'url': f'/api/catalog/example/{m.key}', 'kind': 'image',
                                     'caption': f'{m.name}: publisher demonstration, not a result from this Mac'}
                                    if m.example else None)
            row["face"] = ({'state': 'unavailable' if blocked['action'] == 'unsupported' else 'unchecked',
                            'says': blocked['why'], 'action': None} if blocked else face(m, r, used, environ))
            if row['group'] == 'ready' and row['face']['state'] not in ('on', 'obtained'):
                row['group'] = 'available'
            row['functionalCheck'] = entry_check(m, environ)
            row['proofStep'] = proof_step(m)
            if not used and row['proofStep'] is not None:
                from . import capability_shelf
                role = capability_shelf.entry_role(row)
                option = capability_shelf._options(role)[0]
                row['setups'].append({'role': role['id'], 'title': m.name, 'option': m.key,
                                      **_setup_plan(role, option), 'canTry': False})
            if m.members:
                row['memberKinds'] = bundle_kinds(m, environ)
            if kind := kind_info(m, environ):
                row["newKind"] = kind
            if m.runner == "python-env":
                from . import env_runner

                kept = env_runner.versions(m, environ)
                if kept:
                    row["versions"] = kept
                # What its card's Turn off and Remove act on (C7 Window B, finding 5): on is a proven version running
                # as a choice; installed is any version made here (Remove takes them all away, its entry stays).
                from . import envs

                row["env"] = {"on": env_runner.in_use(m, environ) is not None,
                              "installed": bool(envs.generations(m, environ))}
            if m.runner == "fiji" and m.namespace:
                from . import fiji_display

                row["env"] = {"on": fiji_display.in_use(m, environ) is not None, "installed": False}
            if owner.key in fetching:
                row["download"] = fetching[owner.key]
            out.append(row)
    return sorted(out, key=lambda r: (GROUP_ORDER.index(r["group"]), r["name"].lower()))


def problems(environ: Mapping[str, str] = os.environ) -> list[dict]:
    """Entries that can't be read (a manifest's mistakes), and entries made for another version of the app
    (`incompatible`: manifests.incompatible's words): shown, never run."""
    found = {**catalog.problems(environ), **{f"services/{k}": v for k, v in catalog.service_problems().items()}}
    return [{"entry": k, "problems": [str(p) for p in v],
             **({"incompatible": True} if any(isinstance(p, catalog.Incompatible) for p in v) else {})}
            for k, v in sorted(found.items())]


# --- in words, for the agent (the `models` tool; P4) ------------------------------------------------------------------
def _norm(text: str) -> str:
    import re

    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def uses_words(row: dict) -> str:
    """The jobs it can do, each said as chosen or not: a model that is on (ready here) is not the job's model until it is
    chosen (live 2026-10-28: "I just enabled BioCLIP 2" was taken for "it is the model for comparing")."""
    return " · ".join(f"{u['title']} ({'in use' if u['fixed'] else 'your choice'})" if u["current"]
                      else f"{u['title']} (not chosen: the job uses another model)" for u in row.get("usedFor", []))


def job_of(row: dict) -> str:
    """The job it does for the user now: the first current choice that uses it."""
    current = [u for u in row.get("usedFor", []) if u["current"]]
    return next((u["title"] for u in current if not u["fixed"]), current[0]["title"] if current else "")


def named(row: dict) -> str:
    """Its name with where it runs, once ("SAM 3.1 (cloud)" says it already)."""
    from .names import says_where

    return row["name"] if says_where(row["name"]) else f"{row['name']} ({row['where']})"


def state_words(row: dict) -> str:
    if blocked := (row.get('execution') or {}).get('why'):
        return blocked
    if row.get('status') == 'ready':
        return (row.get('face') or {}).get('says') or row['word']
    return row['word']


def row_words(row: dict) -> str:
    """One model as the Models page says it: its status, what it does, its uses, why (each fact) and its next step."""
    lines = [f"{named(row)}: {state_words(row)}."]
    if row.get("what"):
        lines.append(f"What it does: {row['what']}.")
    if row.get("usedFor"):
        lines.append(f"Used for: {uses_words(row)}.")
    facts = [("yes: " if f["ok"] is True else "not yet: " if f["ok"] is False else "") + f["says"] for f in row.get("facts", [])]
    if facts:
        lines.append("Why: " + "; ".join(facts) + ".")
    nxt = row.get('next') if row.get('status') in ('needs-setup', 'broken') else (row.get('face') or {}).get('action')
    if nxt:
        lines.append(f"Next step: {nxt['says']}" + (f" ({nxt['how']})" if nxt.get("how") and nxt["how"] != nxt["says"] else "") + ".")
    if row.get("download"):
        d = row["download"]
        lines.append(f"Download now: {d.get('state')}" + (f", {round(100 * d['got'] / d['total'])}%" if d.get("total") else "")
                     + (f" ({d['says']})" if d.get("state") == "failed" else "") + ".")
    return " ".join(lines)


def matching(rows: list[dict], about: str) -> list[dict]:
    """The rows a request names: a model by its name or id (a family named alone is all its variants: "SAM 3.1"), or a job
    by its title ("outlining", "seeing")."""
    key = _norm(about)
    if not key:
        return []
    named = [r for r in rows if key in (_norm(r["name"]), _norm(r["id"]))]
    if named:
        return named
    named = [r for r in rows if _norm(r["name"]).startswith(key) or _norm(r["id"]).startswith(key)]
    if named:
        return named
    return [r for r in rows if any(_norm(u["title"]) == key or _norm(u["title"]).startswith(key) for u in r.get("usedFor", []))]


def key_matching(key_facts: Mapping[str, dict], about: str) -> dict | None:
    """A key the request names ("my Google key", "OpenAI", "Hugging Face")."""
    key = _norm(about).removeprefix("my").removesuffix("keys").removesuffix("key").removesuffix("apikey")
    if not key:
        return None
    for name, f in key_facts.items():
        if key in (_norm(f.get("short", "")), _norm(f.get("service", "")), _norm(name)) or _norm(f.get("name", "")).startswith(key):
            return {"key": name, **f}
    return None


def overview(rows: list[dict], key_facts: Mapping[str, dict]) -> str:
    """What the user's setup needs, in words: what needs them (their current choices), what is ready, what is available
    to add, and their keys. "All set" only when nothing their choices use is missing."""
    needs = [r for r in rows if r["group"] == "needs-you"]
    ready = [r for r in rows if r["group"] == "ready"]
    available = [r for r in rows if r["group"] == "available"]
    out = []
    if needs:
        out.append("Needs the user (their current choices use these and they can't run yet):")
        for r in needs:
            nxt = r.get("next") or {}
            out.append(f"- {job_of(r) or r['name']}: {r['name']} {r['word'][0].lower()}{r['word'][1:]}"
                       + (f" ({nxt['how']})" if nxt.get("how") and nxt.get("kind") != "key" else "") + ".")
    else:
        out.append("All set: every model the user's current choices use can run here.")
    out.append(f"Ready here: {len(ready)} models" + (f" ({', '.join(r['name'] for r in ready[:8])}{', …' if len(ready) > 8 else ''})" if ready else "") + ".")
    if available:
        out.append(f"Setup and checks ({len(available)}; follow the step shown to prepare, check or review):")
        out += [f"- {named(r)}: " + (f"{r['what']}; " if r.get("what") else "") + f"{state_words(r)}."
                for r in available[:12]]
        if len(available) > 12:
            out.append(f"- and {len(available) - 12} more on the Models page")
    if key_facts:
        have = [f.get("short", k) for k, f in key_facts.items() if f.get("present")]
        missing = [f"{f.get('short', k)} ({f.get('enables') or f.get('name', '')})" for k, f in key_facts.items() if not f.get("present")]
        out.append("Keys added: " + (", ".join(have) if have else "none") + ".")
        if missing:
            out.append("Keys not added, and what each would turn on: " + "; ".join(missing) + ".")
    return "\n".join(out)


def key_words(key: dict, rows: list[dict]) -> str:
    """What one key turns on: the catalog's own words, and the models that need it (never the key itself)."""
    service = key.get("service", "")
    needing = [r for r in rows if any(f.get("kind") == "key" and f.get("service") == service for f in r.get("facts", []))]
    state = f"added ({key.get('sourceSays')})" if key.get("present") else "not added yet"
    return (f"The {key.get('short') or key['key']} key: {state}. It turns on: {key.get('enables') or 'its service'}. "
            f"Models that use it: {', '.join(r['name'] for r in needing) or 'none in the catalog'}. "
            "It turns on only what it lists: other jobs need their own keys.")


def main() -> int:
    """make catalog-check: every entry that can't be used as written (reviewed and yours), with why; 1 when any."""
    found = problems()
    for p in found:
        print(f"{p['entry']}: {'; '.join(p['problems'])}")
    print("Every entry reads cleanly." if not found else f"{len(found)} entr{'y' if len(found) == 1 else 'ies'} to fix.")
    return 1 if found else 0


if __name__ == "__main__":
    raise SystemExit(main())
