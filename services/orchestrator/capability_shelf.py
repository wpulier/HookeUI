"""What it can do, and one press to turn each on (docs/design/2026-10-13-capabilities-for-everyone.md, C2).

The Models page lists models; people think in jobs ("find every cell", "tell me what this is"). This is the same catalog
seen by job: one card per job (a model role of capabilities.json, in plain words), its options inside it, and one colour
with one meaning (section 1 of the design):

- **green** "on": one option is ready here and passed its current functional check;
- **yellow** "on its way": one is being downloaded, installed or proven, or is waiting for an answer from outside (a
  licence's approval being asked);
- **red** "off": one press turns it on here (`turn_on`: every step its chosen option needs, the app's own steps done by
  the app, the user's own steps, a key, a licence, an Allow, asked for in that order);
- **grey** "not here": nothing of it can run on this Mac, with the reason (this edition, this Mac's memory or system).

No red without a button that turns it on: a card whose every option is stopped by something the user can't change here
is grey. Each card says where your pictures go (on this Mac, or which cloud service under your own key) and what it
costs (free, or the service's own words), read from the catalog.

**Turning on** (`TurnOn`, one per app): a press picks the option to turn on (`plan`: the job's own choice when it can be
turned on here, else one with no key to add, on this Mac first, the fewest steps), keeps that intent, and moves it on
each time the catalog changes and every few seconds while any is open (`advance`):
- the app's own steps (a download into the models folder, an install of its own Python and its proof) are started in
  their own queues, one after another, as each becomes the next step;
- a step only the user can take (a key, a licence, an Allow for a community catalog's code) waits, said on the card;
- once its option runs, Activity and the conversation say so. The current model choice stays unchanged until
  the user explicitly selects the prepared option.
Nothing turns on without the press. Stop withdraws the intent and cancels only the queue work or functional check
owned by that press. Completion belongs to the original conversation. The agent reads wrapped catalog facts through
`models`; a scoped offer or an explicit named instruction authorizes setup, never an unrelated quoted word.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field

from gpu_worker import manifests as catalog

# Jobs on the shelf, in the order shown: role id, its plain name, what it does for you, and what to say to try it.
JOBS: tuple[tuple[str, str, str, str], ...] = (
    ("vision", "Tell you what you're looking at", "Names what you click and answers questions about the view",
     "what am I looking at?"),
    ("cells", "Find every cell", "Outlines and measures every cell in view at once", "outline every cell"),
    ("outline", "Outline what you click", "Draws the outline of whatever you click or box", ""),
    ("fixed:find-all", "Find every one like this", "Finds every match of a thing you point at", "find all like this one"),
    ("voice", "Talk with you", "Talk out loud while you look, hands free", ""),
    ("research", "Research for you", "Researchers that read, measure and write up for you", "research what this is"),
    ("embed", "Spot look-alikes", "Groups things that look alike and remembers what you named", "group the cells by look"),
    ("fixed:lesson-writer", "Write lessons", "Short interactive lessons and quizzes on what you see",
     "make me a short lesson on this"),
    ("image", "Make pictures", "Labelled pictures and illustrations, made when you ask", ""),
    ("fixed:video", "Make videos", "Short explainer videos, made when you ask", ""),
    ("zeroshot", "Name medical cells", "Extra naming help for blood and Pap smears", ""),
    ("fixed:link", "Link cells between views", "Says which cells in two views are the same ones", ""),
)
SHOWN = {job[0] for job in JOBS}
AUTO = {"download", "install", "check", "activate"}  # steps the app takes itself, in its own queues
THEIRS = {"key", "licence", "trust"}  # steps only the user can take, asked for on the card
RUNNING_DOWNLOAD = ("queued", "downloading", "checking")
RUNNING_INSTALL = ("queued", "installing")
ADVANCE_S = 3.0


def entry_role(row: dict) -> dict:
    """One catalog entry (a Models page row) as a job of its own, so a press can turn it on as a job's is: an entry just
    added from a link ("Turn it on" right after Add), or one New for you offers."""
    return {"id": f"model:{row['id']}", "kind": "model", "title": row["name"], "purpose": row.get("what", ""),
            "current": row["id"], "default": row["id"], "entry": row["id"],
            "options": [{"id": row["id"], "check": row.get("functionalCheck"), "proofStep": row.get("proofStep"), "label": row["name"], "said": row["name"], "runs": row.get("runs", ""),
                         "readiness": {"status": row.get("status", ""), "word": row.get("word", ""),
                                       "steps": row.get("steps") or []}}]}


def _roles(wire: dict) -> dict[str, dict]:
    """Every job a press can be for: the model roles, and each catalog entry as `model:<key>`."""
    out = {r["id"]: r for r in wire.get("roles") or []}
    for row in wire.get("models") or []:
        role = entry_role(row)
        out[role["id"]] = role
    return out


def _options(role: dict) -> list[dict]:
    """A role's options as the shelf weighs them: id, label, said, its readiness and the catalog model it runs."""
    from . import options as options_

    if role.get("entry"):
        return [{**o, "model": role["entry"], "models": [role["entry"]], "runs": o.get("runs", "")} for o in role["options"]]
    if role.get("fixed"):
        out = []
        for which in ("primary", "fallback"):
            o = role.get(which) or {}
            if o:
                out.append({"id": which, "label": o.get("label", ""), "said": o.get("label", ""), "model": o.get("model", ""),
                            "models": [o["model"]] if o.get("model") else [],
                            "check": o.get("check"), "readiness": o.get("readiness") or {}, "runs": o.get("runs", role.get("runs", ""))})
        return out
    from .catalog_view import own_proof

    group = options_.GROUPS.get(role["id"], {})
    out = []
    for o in role.get("options", []):
        opt = group.get(o["id"])
        model = (opt.model or (opt.also[0] if opt.also else "")) if opt is not None else ""
        # A contributed choice is checked again by its own functional proof, never by an app check it has none of (F-L8-2).
        step = own_proof(role["id"], o["id"])[1] if getattr(opt, "model", "") else None
        out.append({"id": o["id"], "label": o.get("label", ""), "said": o.get("said") or o.get("label", ""),
                    "model": model, "models": list(dict.fromkeys(x for x in (opt.model, *opt.also) if x)) if opt else [],
                    "check": o.get("check"), "readiness": o.get("readiness") or {}, "runs": o.get("runs", ""),
                    **({"proofStep": step} if step is not None else {})})
    return out


def _where(models: list[str], runs: str) -> tuple[str, str, str]:
    """Every component's destination and billing; a local first step cannot hide a later provider."""
    entries = [m for key in models if (m := catalog.get(key)) is not None]
    services = {m.service: catalog.services()[m.service] for m in entries if m.service in catalog.services()}
    local = any(m.runs == "this-mac" for m in entries) or runs == "both"
    if services:
        destinations, bills = [], []
        for service in services.values():
            name = service.name.removesuffix(" API")
            destinations.append(f"Sends pictures to {name} (your key)")
            costs = service.costs.split(";")[0].strip() if service.costs else "billed by the provider"
            bills.append((f"{name}: " if len(services) > 1 else "") + costs[0].upper() + costs[1:])
        return "; ".join((["Runs on this Mac"] if local else []) + destinations), "; ".join(bills), next(iter(services))
    if runs in ("cloud", "both") or any(m.runs == "cloud" for m in entries):
        return "Runs on this Mac and in the cloud" if local else "Runs in the cloud", "Provider billing is not recorded", ""
    return "Runs on this Mac", "Free", ""


def _status(o: dict) -> str:
    return (o.get("readiness") or {}).get("status", "")


def _proven(o: dict) -> bool:
    check = o.get("check") or {}
    return _status(o) == "ready" and check.get("state") == "works" and not check.get("earlierBuild")


def _steps(o: dict) -> list[dict]:
    steps = list((o.get("readiness") or {}).get("steps") or [])
    if _status(o) == "ready" and not _proven(o):
        steps.append(o.get("proofStep") or {"kind": "check", "says": "Run its functional check on the public synthetic example",
                      "how": "Check it here", "model": o.get("model", "")})
    return steps


def _doable(o: dict) -> bool:
    """Whether one press (and the user's own steps it asks for) turns it on here."""
    steps = _steps(o)
    return _status(o) in ("ready", "needs-setup", "broken") and bool(steps) and all(s.get("kind") in AUTO | THEIRS for s in steps)


def _running(model: str, downloads: list[dict], installs: list[dict]) -> dict | None:
    """Its download or install under way, if any (keyed by the model whose files or Python it is)."""
    from gpu_worker import model_files

    m = catalog.get(model) if model else None
    owner = model_files.owner(m).key if m is not None else model
    for d in downloads:
        if d.get("model") in (owner, model) and d.get("state") in RUNNING_DOWNLOAD:
            pct = f" {round(100 * d['got'] / d['total'])}%" if d.get("total") else ""
            return {"kind": "download", "says": f"Downloading{pct}", "state": d["state"]}
    for i in installs:
        if i.get("model") == model and not i.get("site") and i.get("state") in RUNNING_INSTALL:
            return {"kind": "install", "says": "Installing and proving it", "state": i["state"]}
    return None


def recipe(option: dict) -> str:
    """Material catalog facts, including companion files/data; prerequisite completion does not change them."""
    entries, pending = {}, list(option['models'])
    while pending:
        key = pending.pop()
        if key in entries:
            continue
        model = catalog.get(key)
        entries[key] = asdict(model) if model else None
        if model:
            pending.extend(x for x in (model.files_from, model.source.get('tool')) if x)
            pending.extend(need.partition(':')[2] for need in (*model.needs_to_get, *model.needs_to_run)
                           if need.startswith('data:'))
    facts = {'models': entries, 'runs': option['runs'], 'where': _where(option['models'], option['runs'])}
    return hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()


def plan(role: dict, opts: list[dict] | None = None) -> dict | None:
    """The option a press turns on (see the module's docstring), with its steps each marked the app's or yours; None
    when nothing of it can be turned on here."""
    opts = _options(role) if opts is None else opts
    doable = [o for o in opts if _doable(o)]
    if not doable:
        return None
    current = role.get("current")

    def rank(o: dict) -> tuple:
        steps = _steps(o)
        own = o["id"] in (current, role.get("default")) or (role.get("fixed") and o["id"] == "primary")
        return (not own, any(s.get("kind") == "key" for s in steps), o.get("runs") == "cloud", len(steps))

    o = sorted(doable, key=rank)[0]
    privacy, cost, service = _where(o["models"], o["runs"])
    # `service`: the cloud service whose key it uses, so the sheet before setup says where that key comes from.
    return {"option": o["id"], "label": o["said"] or o["label"], "model": o["model"], "recipe": recipe(o),
            "privacy": privacy, "cost": cost, **({"service": service} if service else {}),
            "steps": [{**s, "by": "app" if s.get("kind") in AUTO else "you"} for s in _steps(o)]}


def card(role: dict, title: str, what: str, say: str, downloads: list[dict], installs: list[dict],
         turning: dict | None = None) -> dict:
    """One job as the shelf shows it (see the module's docstring)."""
    opts = _options(role)
    selected = next((o for o in opts if o["id"] == role.get("current")), None)
    if role.get("fixed"):
        selected = next((o for o in opts if o["id"] == ("fallback" if (role.get("fallback") or {}).get("active") else "primary")), None)
    ready = [o for o in opts if _proven(o)]
    moving = [(o, r) for o in opts if (r := _running(o["model"], downloads, installs))]
    waiting = [o for o in opts if any(s.get("kind") == "licence" for s in _steps(o)) and turning
               and turning.get("option") == o["id"]]
    retained = next((o for o in opts if turning and turning.get('state') != 'failed' and o['id'] == turning.get('option')), None)
    turn_on = plan(role, [retained]) if retained else plan(role, opts)
    if ready:
        used = selected if selected in ready else ready[0]
        colour, word = "green", "On" if selected in ready else f"On: {used['said'] or used['label']} is available"
    elif retained and turning.get('state') in ('starting', 'running'):
        used = retained
        colour, word = 'yellow', f"On its way: {turning.get('says', 'working').lower()}"
    elif moving:
        used, r = moving[0]
        colour, word = "yellow", f"On its way: {r['says'].lower()}"
    elif turning and turning.get("state") == "waiting" and waiting:
        used = waiting[0]
        from . import observed

        # In the Example project no one is asked, so nothing is on its way: said as that, with what to do (F-L8-4).
        here = any(s.get("says") == observed.NOT_ASKED_STEP for s in _steps(used))
        colour, word = "yellow", (f"Waiting: {observed.NOT_ASKED_STEP[0].lower()}{observed.NOT_ASKED_STEP[1:]}" if here
                                  else "On its way: waiting for its licence to be approved")
    elif turn_on is not None:
        used = next(o for o in opts if o["id"] == turn_on["option"])
        first = turn_on["steps"][0]
        colour, word = "red", f"Off: {first.get('says', 'one step turns it on')}"
    else:
        used = opts[0] if opts else {"model": "", "runs": ""}
        why = next((s.get("says", "") for o in opts for s in _steps(o)), "") or "nothing of it runs on this Mac"
        colour, word = "grey", f"Not on this Mac: {why}"
    disclosed = retained or used
    privacy, cost, service = _where(disclosed.get("models", []), disclosed.get("runs", ""))
    if turning and turning.get('state') != 'failed':
        privacy, cost = turning.get('privacy', privacy), turning.get('cost', cost)
    return {"id": role["id"], "title": title, "what": what or role.get("purpose", ""), "colour": colour, "word": word,
            "using": (selected.get("said") or selected.get("label", "")) if selected and _proven(selected) else "",
            "privacy": privacy, "cost": cost, "service": service, "try": say,
            "tryOption": used.get("id", "") if ready and role["id"] in SHOWN else "",
            "check": disclosed.get("check"),
            "options": [{"id": o["id"], "label": o["said"] or o["label"], "status": _status(o),
                         "word": (o.get("readiness") or {}).get("word", ""), "where": _where(o["models"], o["runs"])[0],
                         "default": o["id"] == (role.get("default") or "primary"), "check": o.get("check"),
                         "cost": _where(o["models"], o["runs"])[1]}
                        for o in opts],
            "turnOn": turn_on if colour in ("red", "yellow") else None,
            **({"turning": turning} if turning else {})}


def cards(roles: list[dict], downloads: list[dict], installs: list[dict], turning: dict[str, dict] | None = None,
          capabilities: list[dict] | None = None, outcomes: dict[str, list[str]] | None = None) -> dict:
    """The shelf: the jobs people turn on (JOBS, in that order), then what runs behind them ("behind the scenes": the
    other model roles), each a card."""
    by_id = {r["id"]: r for r in roles}
    turning = turning or {}
    shown = [card(by_id[i], title, what, say, downloads, installs, turning.get(i)) for i, title, what, say in JOBS
             if i in by_id]
    behind = [card(r, r["title"], r.get("purpose", ""), "", downloads, installs, turning.get(r["id"]))
              for r in roles if r.get("kind") == "model" and r["id"] not in SHOWN]
    for row in [*shown, *behind]:
        row['outcomes'] = (outcomes or {}).get(row['id'], [])
        role = by_id[row['id']]
        model = next((catalog.get(o['model']) for o in _options(role)
                      if o['id'] == (row.get('tryOption') or (row.get('turnOn') or {}).get('option'))), None)
        demo = next((c for c in capabilities or [] if row['id'] in c.get('roles', []) and c.get('media')), None)
        if model is not None and model.example:
            row['demonstration'] = {'url': f'/api/catalog/example/{model.key}', 'kind': 'image',
                                    'caption': f'{model.name}: publisher demonstration, not a result from this Mac'}
        elif demo:
            row['demonstration'] = {'url': f"/api/capabilities/media/{demo['media']}",
                                    'kind': 'video' if demo['media'].endswith(('.mp4', '.webm')) else 'image',
                                    'caption': f"Recorded demonstration: {demo.get('title', demo['id'])}"}
        else:
            row['demonstration'] = None  # no unrelated substitute or invented result
    counts = {c: sum(1 for x in shown if x["colour"] == c) for c in ("green", "yellow", "red", "grey")}
    return {"cards": shown, "behind": behind, "counts": counts}


def observations(hub) -> dict[str, list[str]]:
    """Read the existing outcome ledger off the loop; preserve specimen, version, policy and sample-size caveats."""
    from .outcomes import outcomes_of
    from . import options

    by_operation = {}
    for key, group in options.GROUPS.items():
        ops = {impl.operation for option in group.values() for model in (option.model, *option.also)
               if model and (m := catalog.get(model)) is not None for impl in m.implements}
        by_operation[key] = ops
    rows = outcomes_of(hub).tallies()
    today = time.strftime('%Y-%m-%d')
    return {job: [row.words(today, subject=True) for row in rows if row.operation in operations][:3]
            for job, operations in by_operation.items()}


def words(shelf: dict) -> str:
    """The shelf for the agent: each job, on or not, and its one step."""
    lines = []
    for c in shelf.get("cards", []):
        step = (c.get("turnOn") or {}).get("steps") or []
        lines.append(f"- {c['title']}: {c['word']}" + (f" (using {c['using']})" if c.get("using") else "")
                     + (f"; turning it on: {', '.join(s.get('says', '') for s in step)}" if step and c["colour"] == "red" else "")
                     + f"; {c['privacy'].lower()}")
    from .untrusted import block

    return "What the app can do here, by job:\n" + block("\n".join(lines), tag="capability-facts",
                                                        source="catalog and local checks", limit=12000)


@dataclass
class TurnOn:
    """The presses being carried through (see the module's docstring). `app` gives the wire, the queues and the
    settings. Queue admission only queues work. Synchronous cancellation may wait for an activation's final
    rename; production callers run it off the live loop."""
    app: object
    intents: dict[str, dict] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _closed: bool = False
    _checks: set = field(default_factory=set)  # loop-owned Tasks, awaited before app dependencies stop

    def start(self, job: str, option: str | None = None, *, asked=None, expected: dict | None = None, recheck: bool = False) -> dict:
        asked = asked or (self.app.day.here() if getattr(self.app, "day", None) else None)
        with self._lock:
            if self._closed or getattr(self.app, "_stopping", False):
                raise ValueError("The app is stopping; setup is closed")
            current = self.intents.get(job)
            if current and current["state"] != "failed":
                return self.wire_intent(current)
        wire = self.app.capabilities_wire(counts=False)
        role = _roles(wire).get(job)
        if role is None:
            raise ValueError(f"there is no job called {job}")
        opts = _options(role)
        if recheck:
            if expected is None or option is None:
                raise ValueError("Review the exact check and its terms first")
            opts = [{**o, "check": None} if o["id"] == option else o for o in opts]
        if any(_proven(o) and o["id"] == (option or role.get("current") or "primary") for o in opts):
            return {"state": "on", "says": "It's on already"}
        chosen = plan(role, [o for o in opts if option is None or o["id"] == option])
        if expected is not None and chosen != expected:
            raise ValueError("Its setup changed; review the capability card before turning it on")
        if chosen is None:
            raise ValueError("nothing of it can be turned on on this Mac")
        with self._lock:
            if self._closed or getattr(self.app, "_stopping", False):
                raise ValueError("The app is stopping; setup is closed")
            current = self.intents.get(job)  # another press may have been admitted while the wire was read
            if current and current['state'] != 'failed':
                return self.wire_intent(current)
            intent = self.intents[job] = {"job": job, "option": chosen["option"], "label": chosen["label"],
                                 "model": chosen["model"], "privacy": chosen["privacy"], "cost": chosen["cost"], "state": "starting", "says": "Starting", "at": time.time(),
                                 "_recipe": chosen['recipe'], "_recheck": recheck,
                                 "_asked": asked, "_selected": role.get("current"), "_owner": f"capability:{uuid.uuid4().hex}"}
        self.advance(wire)
        return self.wire_intent(intent if self.intents.get(job) is intent else {"state": "on"})

    def cancel(self, job: str, *, attempt: str | None = None) -> bool:
        """Withdraw this exact attempt off-loop; false when its receipt already won the terminal transition."""
        with self._lock:
            intent = self.intents.get(job)
            if attempt is not None and (intent is None or intent.get('_owner') != attempt):
                return False
        activation = intent.get('_activation') if intent else None
        if activation is not None and not activation.cancel():
            return False
        with self._lock:
            if self.intents.get(job) is not intent:
                return False
            self.intents.pop(job, None)
        if intent and intent.get("_work"):
            self._cancel_work(intent["_work"], intent["_owner"])
        if intent and isinstance(intent.get("_check"), asyncio.Task):
            self.app._loop.call_soon_threadsafe(intent["_check"].cancel)
        return intent is not None

    async def close(self) -> None:
        """Close admission and drain owned proof coroutines before their queues/workers are shut down."""
        with self._lock:
            self._closed = True
            jobs = list(self.intents)
        for job in jobs:
            await asyncio.to_thread(self.cancel, job)  # a final activation rename may hold its terminal lock
        await asyncio.sleep(0)  # scheduled proof admissions see closed/retired before tasks are collected
        checks = tuple(self._checks)
        with self._lock:
            committed = {i.get('_check') for i in self.intents.values()
                         if i.get('_activation') is not None and i['_activation'].committed}
        for task in checks:
            if task not in committed and not task.cancelling():
                task.cancel()
        if checks:
            await asyncio.gather(*checks, return_exceptions=True)

    def _retained(self, intent: dict) -> bool:
        completed = intent.get('_activation') is not None and intent['_activation'].committed
        return (not self._closed or completed) and intent['state'] != 'failed' and self.intents.get(intent['job']) is intent

    def _cancel_work(self, work: dict, owner: str, why: str = "") -> None:
        queue = self.app.downloads if work["kind"] == "download" else self.app.installs
        try:
            queue.cancel(work["model"], by=owner, **({"why": why} if why and work["kind"] != "download" else {}))
        except Exception:  # the queue may have completed; ownership is checked inside its cancellation lock
            pass

    @staticmethod
    def wire_intent(intent: dict) -> dict:
        return {**{k: v for k, v in intent.items() if not k.startswith("_")},
                **({"attempt": intent["_owner"]} if intent.get("_owner") else {})}

    def wire(self) -> dict[str, dict]:
        with self._lock:
            return {k: self.wire_intent(v) for k, v in self.intents.items()}

    def advance(self, wire: dict | None = None) -> None:
        """Each open press one step further (see the module's docstring)."""
        if not self.intents:
            return
        wire = wire or self.app.capabilities_wire(counts=False)
        setup = wire.get("setup") or {}
        downloads, installs = setup.get("downloads") or [], setup.get("installs") or []
        roles = _roles(wire)
        with self._lock:
            active = list(self.intents.items())
        for job, intent in active:
            if intent['state'] == 'failed':
                continue
            role = roles.get(job)
            o = next((x for x in _options(role) if x["id"] == intent["option"]), None) if role else None
            if o is None:
                self._retire(intent, 'It is no longer in the catalog; review the available options')
                continue
            if recipe(o) != intent['_recipe']:
                self._retire(intent, 'Its setup changed; review the capability card and press Turn on again')
                continue
            if intent.get('_activation_pending'):
                continue  # this owned coroutine settles from its own result, never an older persisted check
            with self._lock:  # with _begin's end of admission: a skip is either seen there or never made
                admitting = intent.get('_admitting')
                if admitting:
                    intent['_skipped'] = True
            if admitting:
                continue  # its queue's reply first (_begin), which may carry its one line; _begin advances it then
            if intent.get('_recheck'):
                completed = next((r for r in installs if r.get('model') == o['model']
                                  and r.get('by') == intent['_owner'] and r.get('state') == 'done'), None)
                if completed is not None:
                    intent['_recheck'] = False
                else:
                    o = {**o, 'check': None}
            if _proven(o):
                self._done(intent, role, o)
                continue
            failed = self._failed(o, downloads, installs, intent)
            if failed:
                self._set(intent, state="failed", says=failed)
                continue
            if _running(o["model"], downloads, installs):
                self._set(intent, state="running", says=_running(o["model"], downloads, installs)["says"])
                continue
            steps = _steps(o)
            nxt = steps[0] if steps else None
            if nxt is None:
                self._set(intent, state="failed", says=(o.get("readiness") or {}).get("word") or "It can't be turned on here")
            elif nxt.get("kind") in THEIRS:
                self._set(intent, state="waiting", says=nxt.get("says", ""), step=nxt)
            elif nxt.get("kind") == "download":
                self._begin(intent, "download", nxt.get("model") or o["model"])
            elif nxt.get("kind") == "install":
                self._begin(intent, "install", nxt.get("model") or o["model"], recheck=nxt.get("recheck") is True)
            elif nxt.get("kind") == "activate":
                self._begin(intent, "activate", nxt.get("model") or o["model"])
            elif nxt.get("kind") == "check":
                self._check(intent, o)
            else:
                self._set(intent, state="failed", says=nxt.get("says") or "It can't be turned on here")

    # --- inside ----------------------------------------------------------------------------------------------------
    def _retire(self, intent: dict, says: str) -> None:
        if intent.get('_activation') is not None:
            with self._lock:
                if intent.get('_retiring'):
                    return
                intent['_retiring'] = True
            async def retire():
                from .owned_process import off_loop
                if await off_loop(intent['_activation'].cancel):
                    self._retire_work(intent, says)
                else:
                    self._set(intent, state='failed', says=says)  # the old receipt committed; the new plan needs consent
                if not self._closed:
                    self.app.hub.broadcast(self.app.capabilities_wire())
            def launch():
                task = self.app._loop.create_task(retire())
                self._checks.add(task)
                task.add_done_callback(self._checks.discard)
            self.app._loop.call_soon_threadsafe(launch)
            return
        self._retire_work(intent, says)

    def _retire_work(self, intent: dict, says: str) -> None:
        self._set(intent, state='failed', says=says)
        if intent.get('_work'):
            self._cancel_work(intent['_work'], intent['_owner'], why='its setup changed')
        if isinstance(intent.get('_check'), asyncio.Task):
            self.app._loop.call_soon_threadsafe(intent['_check'].cancel)

    def _set(self, intent: dict, **kw) -> None:
        with self._lock:
            if self._retained(intent):
                intent.update(kw)
                if "step" not in kw:
                    intent.pop("step", None)

    def _begin(self, intent: dict, kind: str, model: str, *, recheck: bool = False) -> None:
        with self._lock:
            if not self._retained(intent) or intent.get("began") == f"{kind}:{model}":
                return
            intent['began'] = f"{kind}:{model}"  # claim once before the queue callback can yield to Stop
            if kind == 'activate':
                from .mine import Activation
                intent['_activation'] = Activation()
            else:
                # Being admitted: no advance completes it until its queue's reply is with it. A queue may finish at once
                # and say so before it returns (a kept version on again: its Activity line, then `changed`, whose
                # catalog_changed -> advance runs on the loop while this press is off it), and that reply's line is the
                # press's own (the Spec review of 87ff1e59: completed first, it was said twice).
                intent['_admitting'] = True
        try:
            if kind == "activate":
                intent['_activation_pending'] = True
                self._check(intent, {'model': model, '_activate': True})
                return
            queue = self.app.downloads if kind == "download" else self.app.installs
            row = None
            try:
                row = queue.start(model, by=intent["_owner"], **({"recheck": True} if recheck else {}))
            finally:  # admitted, or its start raised: either way it is no longer being admitted
                with self._lock:
                    if kind == "install" and row and row.get("onAgain") and row.get("state") == "done":
                        # A version kept after Turn off, on again at once: its queue said so in Activity, and that line
                        # is the press's one line, its words kept (Window C, 2026-10-14: two lines for one Turn on).
                        # Recorded before the admission ends, so no advance completes it without them.
                        intent["_on_again"] = {"says": row.get("says", ""), "line": row.get("activity", "")}
                    intent.pop('_admitting', None)
                    skipped = intent.pop('_skipped', False)
            work = {"kind": kind, "model": row.get("model", model), "activity": row.get("activity", "")}
            with self._lock:
                retained = self._retained(intent)
                if retained:
                    intent["_work"] = work
            if not retained:
                self._cancel_work(work, intent["_owner"])
                return
        except Exception as exc:
            self._set(intent, state="failed", says=getattr(exc, "says", "") or str(exc))
            return
        self._set(intent, state="running", says="Downloading" if kind == "download" else "Installing and proving it")
        if skipped or intent.get("_on_again"):
            # On already, or its queue moved on while it was admitted (an advance then passed it over): advanced now,
            # not at the next tick (ADVANCE_S). No lock is held here; a second _begin of it returns at once (`began`),
            # and _done refuses an intent already done.
            self.advance()

    def _check(self, intent: dict, option: dict) -> None:
        """Schedule one owned Task on the app loop; closure drains the actual coroutine, not a cancelled proxy."""
        with self._lock:
            if not self._retained(intent) or intent.get("_check") is not None:
                return
            loop = getattr(self.app, "_loop", None)
            if loop is None or not loop.is_running():
                intent.update(state="failed", says="Open the running Studio to check this capability")
                return
            intent.update(state="running", says="Checking it on the public synthetic example", _check=True)

        def launch():
            with self._lock:
                if not self._retained(intent):
                    return
                task = loop.create_task(self._run_check(intent['job'], option, intent))
                intent['_check'] = task
                self._checks.add(task)
                task.add_done_callback(self._checks.discard)
        loop.call_soon_threadsafe(launch)

    async def _run_check(self, job: str, option: dict, intent: dict) -> None:
        from . import capability_example, model_checks

        if not self._retained(intent):
            return
        if option.get('_activate'):
            try:
                check = await self.app.activate_mine(option['model'], asked=intent.get('_asked'),
                                                     attempt=intent['_activation'])
                intent['_activation_pending'] = False
                if not check.get('ok'):
                    self._set(intent, state='failed', says='Its weights did not pass the functional check')
                elif self._retained(intent):
                    intent['_recheck'] = False
                    self.advance()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self._set(intent, state='failed', says=f'Its functional check could not finish: {exc}')
            finally:
                if not self._closed:
                    self.app.hub.broadcast(self.app.capabilities_wire())
            return
        store = self.app.hub.checks
        entry = model_checks.find(job, "model" if option["id"] == "primary" else option["id"])
        if entry is None:
            self._set(intent, state="failed", says="This capability has no functional check adapter yet; open its model card")
            return
        try:
            while store.running is not None:
                if not self._retained(intent):
                    return
                self._set(intent, state="running", says="Waiting for the current functional check")
                await asyncio.sleep(.1)
            if not self._retained(intent):
                return
            # Reserve before the file read yields, so another request cannot claim the same check slot.
            store.running = {"by": intent["_owner"], "done": 0, "of": 1, "now": entry.name}
            image = await asyncio.to_thread(capability_example.picture, self.app.paths)
            checker = model_checks.Checker(self.app.hub, self.app.agent, store, by=intent["_owner"],
                                          jpeg=image.read_bytes(), public_example=True, live=True, video=True,
                                          log=self.app.agent.log)
            lines = await checker.run([entry])
            result = lines[0]
            if result["state"] != "works":
                self._set(intent, state="failed", says=result.get("why") or "Its functional check did not pass")
            elif self._retained(intent):
                intent["_recheck"] = False
                self.advance()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._set(intent, state="failed", says=f"Its functional check could not finish: {exc}")
        finally:
            if store.running and store.running.get("by") == intent["_owner"]:
                store.running = None
            if not self._closed:
                self.app.hub.broadcast(self.app.capabilities_wire())

    def _failed(self, o: dict, downloads: list[dict], installs: list[dict], intent: dict) -> str:
        """The words of the download or install this press started, when it failed or was cancelled."""
        began = intent.get("began", "")
        if not began:
            return ""
        kind, _, model = began.partition(":")
        from gpu_worker import model_files

        m = catalog.get(model)
        if kind == "activate":
            return ""  # its owned coroutine supplies this attempt's failure
        keys = {model, model_files.owner(m).key if m is not None else model}
        rows = downloads if kind == "download" else installs
        row = next((r for r in rows if r.get("model") in keys and not r.get("site")), None)
        if row is not None and row.get("state") in ("failed", "cancelled"):
            return row.get("says") or f"The {kind} didn't finish"
        return ""

    def _done(self, intent: dict, role: dict, o: dict) -> None:
        job = intent['job']
        with self._lock:
            if not self._retained(intent):
                return
            self.intents.pop(job)
        title = next((t for i, t, _, _ in JOBS if i == job), role.get("title", job))
        from . import own_trials, env_runner
        model = catalog.get(o.get("model") or "")
        installed = env_runner.in_use(model) if model is not None and model.runner == "python-env" else None
        awaiting = model is not None and own_trials.pending(installed.m if installed else model)
        again = intent.get("_on_again") or {}
        says = (f"{title} setup checked; compare your chosen picture, then Use it to add the choice" if awaiting
                else again['says'] if again.get("says")
                else f"{title} is ready ({o['said'] or o['label']}); your model choice is unchanged")
        on_said = getattr(self.app, "turned_on", None)
        if on_said is not None:
            on_said(job, says, asked=intent.get("_asked"), **({"line": again["line"]} if again.get("line") else {}))
