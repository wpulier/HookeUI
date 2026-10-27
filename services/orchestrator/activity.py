"""Activity: what happened, newest first, each line from a fact the app recorded (CONTEXT.md "Activity").

The user's words (2026-10-11): the hover over a chat answer's (i) "would tell me the model used… I thought that'd be a
newest to old log of that great data so we could know what the heck was going on". His decision Q2 (plan
see-the-chats-and-sunny-dove, Slice 6): one entry per request (its words, the time, typed or spoken), each step one
line (what was done, its models, how long, whether it worked) that opens to plain details; and beside the requests,
the user's clicks and drags with the model that outlined them, the studio actions, setting changes by the user or by
the app, model loads and unloads, and researchers' starts, milestones and finishes.

What is here, and where each line comes from:
- **What ran, per step** (`models_of`): when a tool run ends (brain/registry.py `Registry.models`), the models that
  worked on it, each with its role: from the runner's executions (the implementation that really ran, a declared
  fallback said with what could not run) and from the settings it read (the vision model in effect for that request,
  `options.recording_reads`). Never a model of an unvoted blind trial: a run with one names none (arena.py).
- **A request** (`request_started`, `request_finished`, `request_scope`): the brain files its words, channel and
  time when the turn starts (brain/loop.py), then its steps, the answering model and the look's own outlines when
  it ends. A late step updates its step by occurrence (the chat's `chat-action`).
- **Everything else** (`activity`): one line, in plain words; `commands.py` files the studio's own commands
  (`command`) and clicks (`clicked`, `named`), `App.publish_settings` and the tool registry file setting changes
  (`settings_published`, the user's or the app's with the words that asked), and the research manager's changes
  file researchers' starts (`research_changed`; their questions and finishes are the announcer's notices, which the
  studio says in plain words).

Every line is a line of the conversation it happened in (dialogue.py: the day's journal, the conversation log and
every open studio), so a reload or a restart replays it (ADR 0001); a later line with the same `id` replaces it in
place (running, then done). Nothing here goes into a model's prompt.

**Model loads and unloads** (for the code that loads models; the meter's "our own load", Q10):

    from orchestrator import activity
    key = activity.model_loading(hub, "micro-sam", expected_s=120)  # "Loading micro-sam (usually about 2 min)", running
    activity.model_loaded(hub, key, seconds=118.4)                   # "micro-sam is ready (loaded in 1 min 58 s)", done
    activity.model_loaded(hub, key, ok=False, why="out of memory")   # "micro-sam did not load: out of memory", failed
    activity.model_unloaded(hub, "micro-sam", why="to free memory for Cellpose-SAM")

Each is safe from any thread (it is filed on the app's event loop) and does nothing on a hub without Activity (a test's
stand-in). Name the model as names.py says it (`names.said_impl`, or an option's name).
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import itertools
import time
import uuid
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any

KINDS = ("request", "click", "command", "setting", "model", "research")
STATES = ("done", "running", "failed")
TEXT_MAX = 400  # characters of a line's text (a longer one ends in "…")
WORDS_MAX = 1000  # characters of a request's words kept with it (the whole words stay in the turn)
MODELS_MAX = 8
STEPS_MAX = 32
DETAIL_LINES_MAX = 8
KEPT = 256  # recent lines kept in memory, so an update keeps what it does not change

# The role each operation's implementation plays on a step (known.chain's words: found by, drawn by, followed by).
ROLES = {"outline": "drawn by", "cells": "found by", "find-like": "found by", "track": "followed by",
         "motion": "measured by", "link": "linked by", "looks": "compared by", "groups": "grouped by",
         "light": "measured by", "names": "ranked by", "your-label": "matched by"}
# Settings a run read that name a model of their own (not an operation's choice, which its execution says).
READ_ROLES = {"vision": "seen by", "sight": "seen by", "fast": "decided by", "zeroshot": "ranked by",
              "embed": "compared by", "image": "made by", "research": "researched by", "presentation": "arranged by"}
# A step's facts as Activity keeps them with its request (the rest of a step's wire stays on its chat line).
STEP_KEYS = ("tool", "ok", "summary", "ms", "at", "next", "models", "requestId", "actionId")


def _cut(text: object, limit: int) -> str:
    line = " ".join(str(text or "").split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


# --- what ran, per step ------------------------------------------------------------------------
def _impl(impl_id: str | None):
    from .operations import OPERATIONS

    for op in OPERATIONS.values():
        found = op.impl(impl_id) if impl_id else None
        if found is not None:
            return found
    return None


def impl_model(role: str, impl_id: str | None, *, instead: str | None = None, failed: bool = False) -> dict | None:
    """One implementation as Activity names it: its one name (names.py), where it runs, said together."""
    from . import names

    if not impl_id:
        return None
    impl = _impl(impl_id)
    name = impl.label if impl is not None else str(impl_id)
    where = names.where_of(impl) if impl is not None else ""
    out = {"role": role, "name": name, "said": names.said(name, where)}
    if instead:
        out["instead"] = names.said_impl(instead)
    if failed:
        out["failed"] = True
    return out


def option_model(role: str, group: str, value) -> dict | None:
    """An option's model (the vision model in effect), or None for an option that runs no model ("off")."""
    from . import names

    entry = (names.index().get(f"option:{group}") or names.Namespace()).entries.get(str(value or ""))
    if entry is None or not entry.where:
        return None
    return {"role": role, "name": entry.name, "said": names.said(entry.name, entry.where)}


def blind(execution) -> bool:
    """An execution of an arena trial not voted on yet: which model ran is said only after the vote (arena.py)."""
    return getattr(execution, "trial", None) is not None and not getattr(execution, "revealed", False)


def execution_models(executions) -> list[dict] | None:
    """The models behind these executions, in order; None when one is an unvoted blind trial's (say none at all).
    An algorithm is named only when it ran as a declared fallback in a model's place (the spot ring)."""
    out: list[dict] = []
    for x in executions:
        if blind(x):
            return None
        role = ROLES.get(x.operation, "done by")
        made = x.made_by() if hasattr(x, "made_by") else {}
        if x.executed:
            impl = _impl(x.executed)
            fallback = made.get("fallbackFrom")
            if impl is not None and impl.kind == "algorithm" and not fallback:
                continue
            found = impl_model(role, x.executed, instead=fallback)
        else:
            ran = [a for a in getattr(x, "attempts", ()) if a.error and not a.not_run]
            found = impl_model(role, ran[-1].impl, failed=True) if ran else None
        if found is not None:
            out.append(found)
    return out


def _add(out: list[dict], found: dict | None) -> None:
    if found is not None and len(out) < MODELS_MAX and not any(
            (m["role"], m["said"]) == (found["role"], found["said"]) for m in out):
        out.append(found)


def models_of(hub, run) -> list[dict]:
    """The models that worked on one tool run, each {role, name, said} (+ "instead": what a fallback ran in place of;
    "failed": it ran and failed), from its executions, the executions of the outlines it put on the board (a kept
    comparison's outline was drawn by its arm's model, in the comparison's run) and the settings it read for its
    request. Bounded and in a fixed order: what saw, then what made. [] when one of them is an unvoted blind trial's."""
    from .options import GROUPS, unrecorded_reads

    runner = getattr(hub, "runner", None)
    get = getattr(runner, "get", None)
    executions = [x for x in (get(e) for e in (getattr(run, "executions", None) or ())) if x is not None] if get else []
    made = execution_models(executions)
    if made is None:
        return []
    drawn = outline_models(hub, run, {x.id for x in executions})
    if drawn is None:
        return []
    made += drawn
    chosen_by = {getattr(op, "selected_by", None) for op in _operations()}
    drew = any(x.operation in ("outline", "find-like", "cells") for x in executions)
    out: list[dict] = []
    settings = getattr(hub, "settings", None)
    with unrecorded_reads():  # what this reads is Activity's, never the run's
        for group in getattr(run, "reads", None) or ():
            if group not in READ_ROLES or group not in GROUPS or group in chosen_by or settings is None:
                continue  # an operation's choice is said by its execution, never by the setting
            role = ("found by" if drew else "seen by") if group == "vision" else READ_ROLES[group]
            _add(out, option_model(role, group, getattr(settings, group, None)))
    for found in made:
        _add(out, found)
    return out


def outline_models(hub, run, seen: set) -> list[dict] | None:
    """What drew the outlines a run filed as its outputs (common.record_outline), from each outline's own record
    (`made_by`: its execution, else its implementation) when its run did not itself run that execution; None when one
    is of an unvoted blind trial."""
    objects = getattr(getattr(hub, "scene", None), "objects", None) or {}
    get = getattr(getattr(hub, "runner", None), "get", None)
    out: list[dict] = []
    for output in getattr(run, "outputs", None) or ():
        item = output.wire() if hasattr(output, "wire") else output
        obj = objects.get(item.get("id")) if isinstance(item, dict) and item.get("kind") == "outline" else None
        if obj is None:
            continue
        if getattr(obj, "trial", None) is not None:
            return None
        made = getattr(obj, "made_by", None) or {}
        if made.get("execution") in seen:
            continue
        execution = get(made.get("execution")) if get is not None and made.get("execution") else None
        if execution is not None:
            found = execution_models([execution])
            if found is None:
                return None
            seen.add(execution.id)
            out += found
        elif made.get("implementation"):
            role = ROLES.get(made.get("operation") or "outline", "drawn by")
            found_one = impl_model(role, made["implementation"], instead=made.get("fallbackFrom"))
            if found_one is not None:
                out.append(found_one)
    return out


def _operations():
    from .operations import OPERATIONS

    return OPERATIONS.values()


def step_wire(wire: dict) -> dict:
    """A step as Activity keeps it with its request: what it did, how it went, its models, its occurrence."""
    return {k: copy.deepcopy(wire[k]) for k in STEP_KEYS if k in wire}


def brief(models: list[dict]) -> str:
    """The models of a line, briefly ("Gemini 3.8 Flash → EdgeTAM"): what the studio shows on one line."""
    return " → ".join(dict.fromkeys(m["name"] for m in models))


# --- the sink ----------------------------------------------------------------------------------
class Activity:
    """Files Activity lines through the Dialogue (journal, conversation log, every studio), keeps the recent ones so an
    update keeps what it does not change, and remembers what settings and researchers were, to say what changed."""

    def __init__(self, hub, dialogue, *, clock=None) -> None:
        # The clock read when a line is filed (never a default bound at import: test_golden's clock guard).
        self.hub, self.dialogue, self.clock = hub, dialogue, clock or (lambda: time.time())
        self.recent: OrderedDict[str, dict] = OrderedDict()
        self.where: dict[str, Any] = {}  # an id's conversation (Asked), so its update is filed where it began
        self.loop: asyncio.AbstractEventLoop | None = None
        self.known: dict = {}  # the settings as last said (settings_published)
        self.changed_in: dict[int, list[tuple[str, Any, Any]]] = {}  # settings a running tool changed, by id(run)
        self.researchers: dict[str, tuple] = {}  # each researcher's (working, run kind, runs) as last seen
        self.running: dict[str, tuple[str, str]] = {}  # a researcher's own running line: its id and words
        self.models: dict[str, str] = {}  # a model load's key -> the model's name (model_loading)
        self.registry = None  # the brain's tools (wire): which settings a tool declares it sets
        self.counter = itertools.count(1)

    # --- filing ---------------------------------------------------------------------------
    def add(self, kind: str, text: str, *, id: str | None = None, at: float | None = None, state: str = "done",
            details: dict | None = None, ref: dict | None = None, steps: list | None = None, asked=None,
            rid: str | None = None) -> dict:
        """File one line (or replace the line with this `id`). Returns it as filed (or as it will be, when called from
        another thread)."""
        if kind not in KINDS:
            raise ValueError(f"an activity kind is one of {', '.join(KINDS)}")
        if state not in STATES:
            raise ValueError(f"an activity state is one of {', '.join(STATES)}")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("an activity line needs its words")
        line = {"type": "activity", "id": id or f"{kind}:{uuid.uuid4().hex[:12]}", "kind": kind,
                "text": _cut(text, TEXT_MAX), "at": round(at if at is not None else self.clock(), 3), "state": state}
        if details:
            line["details"] = _details(details)
        if ref:
            line["ref"] = {k: v for k, v in ref.items() if isinstance(v, (str, int, float, bool)) and v is not None}
        if steps is not None:
            line["steps"] = [step_wire(s) for s in steps[:STEPS_MAX]]
        line["lineId"] = line["id"]  # the journal replaces a line with the same lineId in place (day/store.py)
        if asked is None and rid is None and line["id"] in self.where:
            asked, rid = self.where[line["id"]]  # an update is filed where its line began
        self._file(line, asked, rid)
        return line

    def update(self, id: str, **changes) -> dict | None:
        """File again the line with this id, changed; None when it is no longer kept."""
        line = self.recent.get(id)
        if line is None:
            return None
        fields = {k: copy.deepcopy(line.get(k)) for k in ("text", "at", "state", "details", "ref", "steps") if k in line}
        fields.update(changes)
        return self.add(line["kind"], fields.pop("text"), id=id, **fields)

    def _file(self, line: dict, asked, rid) -> None:
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if here is None and self.loop is not None and self.loop.is_running():
            # Another thread (a model loading): filed on the app's loop, in order.
            self.loop.call_soon_threadsafe(self._show, line, asked, rid)
            return
        if here is not None:
            self.loop = here
        self._show(line, asked, rid)

    def _show(self, line: dict, asked, rid) -> None:
        self.recent[line["id"]] = line
        self.recent.move_to_end(line["id"])
        while len(self.recent) > KEPT:
            gone, _ = self.recent.popitem(last=False)
            self.where.pop(gone, None)
        if line["id"] not in self.where:
            if asked is None and rid is None:  # the conversation open now: its update goes there too
                with contextlib.suppress(Exception):
                    asked = self.dialogue.here()
            self.where[line["id"]] = (asked, rid)
        try:
            day = getattr(self.dialogue, "day", None)
            self.dialogue.show({**line, "day": getattr(day, "sid", None)}, asked=asked, rid=rid)
        except Exception:  # Activity never breaks what it describes
            pass

    # --- settings -------------------------------------------------------------------------
    def snapshot(self) -> dict:
        settings = getattr(self.hub, "settings", None)
        wire = dict(settings.to_wire()) if settings is not None else {}
        scale = getattr(self.hub, "scale", None)
        if scale is not None and hasattr(scale, "objective"):
            wire["objective"] = scale.objective
        return wire

    def settings_published(self) -> None:
        """Settings changed (App.publish_settings): inside a tool's run they are the app's, said when the run ends with
        the words that asked (`run_finished`); otherwise the user's (the studio's settings command)."""
        from .brain.registry import CURRENT_RUN

        changes = self._diff()
        run = CURRENT_RUN.get()
        if run is not None:
            self.changed_in.setdefault(id(run), []).extend(changes)
            return
        for key, old, new in changes:
            self._setting(key, old, new, by_user=True)

    def objective_set(self) -> None:
        """The studio's objective picker (the scale command): said as the user's at once."""
        for key, old, new in self._diff():
            self._setting(key, old, new, by_user=True)

    def _diff(self) -> list[tuple[str, Any, Any]]:
        now = self.snapshot()
        changes = [(k, self.known.get(k), v) for k, v in now.items() if k in self.known and self.known[k] != v]
        self.known = now
        return changes

    def run_finished(self, run, ctx) -> None:
        """A tool run ended (Registry.finished): the settings it changed are the app's, with the words that asked."""
        changes = self.changed_in.pop(id(run), [])
        tool = (getattr(self.registry, "tools", None) or {}).get(run.tool)
        if getattr(run, "ok", None) is True and getattr(tool, "sets", ()) and "objective" in tool.sets:
            changes += [c for c in self._diff() if c[0] == "objective"]  # set_objective changes the scale, not settings
        by_hand = BY_HAND.get()
        for key, old, new in changes:
            self._setting(key, old, new, by_user=by_hand, words="" if by_hand else getattr(ctx, "question", "") or "",
                          asked=getattr(ctx, "asked", None), turn=getattr(ctx, "turn_id", None),
                          menu="Actions" if by_hand else None)

    def _setting(self, key: str, old, new, *, by_user: bool, words: str = "", asked=None, turn=None,
                 menu: str | None = None) -> None:
        title, its_menu = setting_title(key)
        menu = menu or its_menu
        value, before = setting_value(key, new), setting_value(key, old)
        if by_user:
            text = (f"You turned {title} {value}" if isinstance(new, bool) else
                    f"You chose {value} for {title.lower()}" + (f" ({menu})" if menu else ""))
        else:
            text = (f"The app turned {title} {value}" if isinstance(new, bool) else f"The app set {title.lower()} to {value}")
            if words:
                text += f" (you asked: “{_cut(words, 120)}”)"
        self.add("setting", text, details={"lines": [f"Before: {before}", f"Now: {value}",
                                                     f"By {'you' if by_user else 'the app, for your request'}"],
                                           "ids": {"setting": key, **({"turnId": turn} if turn else {})}},
                 asked=asked)

    # --- researchers ----------------------------------------------------------------------
    def seed_researchers(self, sessions) -> None:
        """The researchers there are at start (restored): nothing is said of them until they change."""
        for s in sessions:
            self.researchers[s.rid] = _research_state(s)

    def research_changed(self, session) -> None:
        """A researcher started, began another run, or stopped (ResearchManager.on_change). A run a request started
        is that request's step already (start_researcher, researcher_followup, revise_work: its origin names the
        request's turn and occurrence), so it is said once, there. A run nothing in the panel started (a mission's
        slot, a run with no request) is a line of its own, running while it works and done or failed when it stops.
        Its plan, its questions and its finish are the announcer's notices either way. Descriptions under labels are
        not researchers here."""
        if getattr(session, "purpose", "research") != "research":
            return
        now = _research_state(session)
        before = self.researchers.get(session.rid)
        self.researchers[session.rid] = now
        if before is not None and before[:2] == now[:2]:
            return
        working, kind, runs = now
        if working and (before is None or not before[0] or before[1] != kind):
            self.running.pop(session.rid, None)
            if started_by_request(session):
                return
            key, text = f"research:{session.rid}:{runs + (1 if kind else 0)}", research_started(session)
            self.running[session.rid] = (key, text)
            self.add("research", text, id=key, state="running", details={"lines": [f"Effort: {session.effort}"]},
                     ref={"researcher": session.name}, rid=session.rid, asked=_research_asked(session))
        elif not working and before is not None and before[0] and session.rid in self.running:
            key, text = self.running.pop(session.rid)
            ended = f"{session.name}: {RESEARCH_ENDED.get(session.status, session.status.replace('_', ' '))}"
            self.add("research", text, id=key, state="done" if session.status in ("done", "partial") else "failed",
                     details={"lines": [f"Effort: {session.effort}", ended]}, ref={"researcher": session.name},
                     rid=session.rid, asked=_research_asked(session))


RESEARCH_ENDED = {"done": "finished", "partial": "finished part of its work", "timed_out": "ran out of time",
                  "timeout": "ran out of time", "failed": "stopped: it failed", "cancelled": "was stopped",
                  "interrupted": "was interrupted"}


def _research_asked(session):
    """Where a researcher's own line is filed: the session it was started in (its `sitting`; ADR 0017) and the
    conversation that started it, whatever session is open when it starts or ends. None for one with no session."""
    from .day.model import Asked

    sid = getattr(session, "sitting", "") or ""
    if not sid:
        return None
    return Asked(str((getattr(session, "origin", None) or {}).get("thread") or ""), 0, day=sid, session=sid)


def started_by_request(session) -> bool:
    """Whether the researcher's run now was started by a request's step (its origin names the request's turn and the
    step's occurrence: brain/toolbox/research.py `origin`), so the request's entry says it. A mission's slot runs on
    its schedule, whoever started the mission: never a request's step."""
    attempt = getattr(session, "_attempt", None)
    if attempt is not None and getattr(attempt, "mission", None) is not None:
        return False
    origin = getattr(session, "origin", None) or {}
    turn = origin.get("turn_id")
    return isinstance(turn, int) and not isinstance(turn, bool) and turn > 0 and bool(origin.get("action_id"))


def _research_state(session) -> tuple:
    run = getattr(session, "run", None)
    return (session.status in ("queued", "starting", "running"), getattr(run, "kind", None) if run is not None else None,
            len(getattr(session, "history", None) or ()))


def research_started(session) -> str:
    """A researcher's start in plain words: what it was asked, never ids or characters."""
    from .untrusted import inline

    run = getattr(session, "run", None)
    if run is not None and run.kind == "answer":
        return f"{session.name} is answering your follow-up: {inline(run.question, 100)}"
    if run is not None and run.kind == "revise":
        return f"{session.name} is making a new version of its {run.kind_name or 'work'}: {inline(run.change, 100)}"
    if getattr(session, "history", None):
        return f"{session.name} is carrying on: {inline(getattr(session, 'asking', '') or session.brief, 100)}"
    return f"{session.name} started: {inline(session.label or session.brief, 100)}"


def setting_title(key: str) -> tuple[str, str]:
    """A setting's title as the menus show it (contract/capabilities.json) and the menu it is in."""
    from . import capabilities

    wire = capabilities.load()
    for role in wire.get("roles", ()):
        if role.get("id") == key:
            return role.get("title") or key, "Models menu" if role.get("kind") == "model" else "Behaviour menu"
    for pref in wire.get("preferences", ()):
        if pref.get("id") == key:
            return pref.get("title") or key, "Behaviour menu" if key != "objective" else "objective menu"
    return key, ""


def setting_value(key: str, value) -> str:
    """A setting's value in words: an option by its one name (names.py), a cap in dollars, a switch on or off."""
    from . import names

    if isinstance(value, bool):
        return "on" if value else "off"
    if value is None or value == "":
        return {"researchCap": "no limit", "objective": "not set"}.get(key, "none")
    if key == "researchCap":
        return f"${float(value):.2f} a day"
    if key == "objective":
        return f"{value}×"
    group = "vision" if key == "compare" else key  # a second model answers too: one of the vision models
    entry = (names.index().get(f"option:{group}") or names.Namespace()).entries.get(str(value))
    return names.said(entry.name, entry.where) if entry is not None else str(value)


def _details(details: dict) -> dict:
    """Plain facts shown when a line is opened: models, how long, lines of words, and ids behind "technical"."""
    out: dict = {}
    if isinstance(details.get("models"), list):
        out["models"] = [{k: str(m[k])[:120] for k in ("role", "name", "said", "instead") if m.get(k)}
                         | ({"failed": True} if m.get("failed") else {})
                         for m in details["models"][:MODELS_MAX] if isinstance(m, dict) and m.get("name")]
    if isinstance(details.get("ms"), (int, float)):
        out["ms"] = round(details["ms"])
    if isinstance(details.get("lines"), list):
        out["lines"] = [_cut(x, 300) for x in details["lines"][:DETAIL_LINES_MAX] if str(x or "").strip()]
    if isinstance(details.get("ids"), dict):
        out["ids"] = {str(k)[:40]: v if isinstance(v, (int, float)) else str(v)[:120]
                      for k, v in list(details["ids"].items())[:8] if v is not None}
    for key in ("answer", "look"):  # a request's own facts (request_finished)
        if isinstance(details.get(key), dict):
            out[key] = copy.deepcopy(details[key])
    return out


# --- the one call for producers ----------------------------------------------------------------
def sink(hub) -> Activity | None:
    found = getattr(hub, "activities", None)
    return found if isinstance(found, Activity) else None


def activity(hub, kind: str, text: str, *, at: float | None = None, state: str = "done", details: dict | None = None,
             ref: dict | None = None, id: str | None = None, rid: str | None = None, asked=None) -> dict | None:
    """File one Activity line for something that is not a chat request (see the module's docstring). Returns the line,
    or None on a hub without Activity. A later call with the same `id` replaces it (running, then done)."""
    found = sink(hub)
    if found is None:
        return None
    try:
        return found.add(kind, text, at=at, state=state, details=details, ref=ref, id=id, rid=rid, asked=asked)
    except ValueError:
        raise
    except Exception:
        return None


def _duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.0f} s" if seconds >= 10 else f"{seconds:.1f} s"
    minutes, rest = divmod(round(seconds), 60)
    return f"{minutes} min" + (f" {rest} s" if rest else "")


def model_loading(hub, name: str, *, expected_s: float | None = None, first: bool = False, key: str | None = None) -> str:
    """A model began loading: a running line ("Loading micro-sam (usually about 2 min)"; `first`: never loaded on this
    Mac before, "… for the first time"). Returns the key for `model_loaded`."""
    key = key or f"model:{name}:{uuid.uuid4().hex[:8]}"
    found = sink(hub)
    if found is not None:
        found.models[key] = name
        while len(found.models) > KEPT:
            found.models.pop(next(iter(found.models)))
    usual = f" (usually about {_duration(expected_s)})" if expected_s else " for the first time" if first else ""
    activity(hub, "model", f"Loading {name}{usual}", id=key, state="running", ref={"model": name},
             details={"lines": [f"Expected to take about {_duration(expected_s)}"]} if expected_s else
             {"lines": ["Not loaded on this Mac before: how long it takes is not known yet"]} if first else None)
    return key


def model_loaded(hub, key: str, *, ok: bool = True, seconds: float | None = None, memory_mb: float | None = None,
                 why: str | None = None) -> None:
    """The load `key` ended: ready (done, how long it took and how much memory it holds) or not (failed, with why)."""
    found = sink(hub)
    name = (found.models.pop(key, None) if found is not None else None) or "the model"
    took = f" (loaded in {_duration(seconds)})" if seconds is not None else ""
    text = f"{name} is ready{took}" if ok else f"{name} did not load" + (f": {why}" if why else "")
    held = isinstance(memory_mb, (int, float)) and not isinstance(memory_mb, bool) and memory_mb > 0
    lines = (([f"Loaded in {_duration(seconds)}"] if seconds is not None else [])
             + ([f"It holds {memory_mb / 1024:.1f} GB of memory"] if ok and held else [])
             + ([f"Why: {why}"] if why and not ok else []))
    activity(hub, "model", text, id=key, state="done" if ok else "failed", ref={"model": name},
             details={"lines": lines} if lines else None)


def model_unloaded(hub, name: str, *, why: str | None = None) -> None:
    """A model was taken out of memory (done), with why ("to free memory for Cellpose-SAM")."""
    activity(hub, "model", f"Unloaded {name}" + (f" {why}" if why and why.startswith("to ") else f": {why}" if why else ""),
             ref={"model": name})


# --- requests (brain/loop.py) -----------------------------------------------------------------
_REQUEST: ContextVar[dict | None] = ContextVar("activity_request", default=None)
# A tool run by the user's own hand (the Actions panel's `action` command): the settings it changes are theirs.
BY_HAND: ContextVar[bool] = ContextVar("activity_by_hand", default=False)


@contextlib.contextmanager
def request_scope(hub):
    """Around one brain turn: what `request_started` files is finished by `request_finished`, or marked failed here
    when the turn raises (its words stay, with what went wrong in plain words)."""
    holder: dict = {}
    token = _REQUEST.set(holder)
    try:
        yield holder
    except BaseException as exc:
        if holder.get("id") and not holder.get("finished"):
            from .brain.registry import ToolFailed

            said = exc.said() if isinstance(exc, ToolFailed) else (
                "it was stopped" if isinstance(exc, asyncio.CancelledError) else "something went wrong in the app")
            found = sink(hub)
            if found is not None:
                with contextlib.suppress(Exception):
                    found.add("request", holder["words"], id=holder["id"], at=holder["at"], state="failed",
                              ref=holder["ref"], asked=holder["asked"], steps=[],
                              details={"lines": [f"The request could not finish: {said}"]})
        raise
    finally:
        _REQUEST.reset(token)


def request_started(hub, turn_id: int, words: str, channel: str, asked) -> None:
    """A request's words, channel and time, filed as it starts (running)."""
    holder, found = _REQUEST.get(), sink(hub)
    if holder is None or found is None or not isinstance(turn_id, int):
        return
    day = getattr(getattr(found.dialogue, "day", None), "sid", "") or ""
    holder.update(id=f"request:{day}:{turn_id}", words=_cut(words, WORDS_MAX) or "(no words)", at=found.clock(),
                  asked=asked, ref={"turnId": turn_id, "channel": "voice" if channel == "voice" else "chat"})
    with contextlib.suppress(Exception):
        found.add("request", holder["words"], id=holder["id"], at=holder["at"], state="running", ref=holder["ref"],
                  asked=asked)


def request_finished(hub, result: dict, *, look: list | tuple = (), look_model: str | None = None) -> None:
    """The request's steps, the answering model and the look's own outlines (found by the vision model, drawn by what
    their executions ran), filed when the turn's result is assembled. Late steps update their occurrence."""
    holder, found = _REQUEST.get(), sink(hub)
    if not holder or not holder.get("id") or found is None:
        return
    holder["finished"] = True
    look_models: list[dict] = []
    if look:
        runner = getattr(hub, "runner", None)
        executions = [runner.get((getattr(o, "made_by", None) or {}).get("execution")) for o in look] if runner else []
        if any(getattr(o, "trial", None) is not None for o in look) or any(x is not None and blind(x) for x in executions):
            look_models = []
        else:
            if look_model:
                _add(look_models, option_model("found by", "vision", look_model))
            for made in execution_models([x for x in executions if x is not None]) or []:
                _add(look_models, made)
    details = {"answer": {"published": bool(result.get("answer")), "model": str(result.get("model") or "")[:120],
                          "thinkMs": result.get("thinkMs"), "totalMs": result.get("totalMs"), "calls": result.get("calls"),
                          "at": round(found.clock(), 3)},  # when the answer and its steps so far were filed
               **({"look": {"outlined": len(look), "models": look_models}} if look else {})}
    steps = list(result.get("actions") or [])
    with contextlib.suppress(Exception):
        found.add("request", holder["words"], id=holder["id"], at=holder["at"], state="done", ref=holder["ref"],
                  asked=holder["asked"], steps=steps, details=details)
    # A request that took the user to another conversation ("back to the blood smear: what did we conclude?"), or to
    # another session, has its answer filed there (brain/loop.py: the request belongs there now). Its entry, with the
    # words it was asked with, goes there too, so neither conversation shows it without them (the lead's review of
    # 2026-10-11). Two sessions each have a t1: the session counts too.
    moved = result.get("asked")
    started = holder["asked"]
    if moved is not None and started is not None and (
            (getattr(moved, "thread", None), getattr(moved, "session", None))
            != (getattr(started, "thread", None), getattr(started, "session", None))):
        with contextlib.suppress(Exception):
            found.add("request", holder["words"], id=holder["id"], at=holder["at"], state="done", ref=holder["ref"],
                      asked=moved, steps=steps, details=details)


# --- clicks and the studio's commands (commands.py) --------------------------------------------
def clicked(app, msg: dict, record: dict) -> None:
    """A click or a dragged box, from the click's own record: what outlined it and how long it took. During a blind
    trial no model is named (arena.py: identities stay hidden until the vote)."""
    hub = app.hub
    found = sink(hub)
    if found is None:
        return
    what = "You dragged a box" if msg.get("box") is not None else "You clicked"
    ms = record.get("ms")
    took = f" · {_ms(ms)}" if isinstance(ms, (int, float)) else ""
    oid = record.get("objectId")
    key = f"click:{oid}" if oid else f"click:{uuid.uuid4().hex[:12]}"
    ids = {k: record.get(k) for k in ("objectId", "frameId", "executionId", "trialId", "member") if record.get(k)}
    if record.get("trialId"):
        text = f"{what}: each option of the blind comparison outlined it (which is which is said after your vote){took}"
        models: list[dict] = []
    elif record.get("member"):
        text = f"{what}: selected cell {record['member']}{took}"
        models = []
    elif record.get("ok") and oid:
        runner = getattr(hub, "runner", None)
        execution = runner.get(record.get("executionId")) if runner is not None and record.get("executionId") else None
        models = (execution_models([execution]) or []) if execution is not None else []
        drawn = next((m for m in models if m["role"] == "drawn by"), None)
        text = f"{what}: outlined" + (f" by {drawn['said']}" if drawn else "") + took
        if drawn and drawn.get("instead"):
            text += f", after {drawn['instead']} could not"
    else:
        text = f"{what}: nothing was outlined" + (f" ({_cut(record.get('error'), 160)})" if record.get("error") else "")
        models = []
    found.add("click", text, id=key, state="done" if record.get("ok") else "failed",
              details={"models": models, "ms": ms, "ids": ids}, ref={"objectId": oid} if oid else None)


def named(app, object_id: str) -> None:
    """Naming a click ended: its line says the name and the model that gave it (from the outline's own record)."""
    found = sink(app.hub)
    key = f"click:{object_id}"
    line = found.recent.get(key) if found is not None else None
    obj = app.hub.scene.objects.get(object_id) if line is not None else None
    if obj is None or getattr(obj, "trial", None) is not None:
        return
    naming = getattr(obj, "naming", None) or {}
    model = option_model("named by", "vision", naming.get("option") or naming.get("model")) if naming.get("by") == "model" else None
    if not obj.label or model is None:
        return
    details = copy.deepcopy(line.get("details") or {})
    models = [m for m in details.get("models", []) if m.get("role") != "named by"] + [model]
    details["models"] = models
    text = line["text"].split(", named ")[0] + f", named “{_cut(obj.label, 60)}” by {model['said']}"
    found.update(key, text=text, details=details)


def _ms(ms: float) -> str:
    return f"{ms / 1000:.1f} s" if ms >= 1000 else f"{round(ms)} ms"


def before_command(app, kind: str, msg: dict) -> dict:
    """What a command acts on, read before it runs (an outline's name goes with the outline)."""
    try:
        objects = getattr(getattr(app.hub, "scene", None), "objects", None) or {}
        oid = msg.get("id")
        obj = objects.get(oid) if isinstance(oid, str) else None
        return {"label": (obj.label or "an unnamed outline") if obj is not None else None}
    except Exception:  # Activity never stops a command
        return {}


MAGNIFIER = {"close": "You closed the magnifier", "image": "You opened a researcher's picture in the magnifier"}
PRESENT_VERBS = {"show": "showed", "hide": "hid", "opacity": "faded", "colour": "coloured", "color": "coloured",
                 "fill": "filled", "edge": "changed the edge of", "emphasise": "emphasised", "emphasize": "emphasised",
                 "open": "opened", "close": "closed", "move": "moved", "resize": "resized", "front": "brought forward"}
# Studio commands that are not the user's deliberate act, so Activity says nothing of them: the pointer resting on an
# outline (focus), and the studio telling the server the magnifier's object lost its place (magnifier "lost") or
# which live source it shows (magnifier "index", sent by the studio itself when the live view is chosen).
SILENT = {("focus", None), ("magnifier", "lost"), ("magnifier", "index")}


def command(app, kind: str, msg: dict, reply: dict, before: dict | None = None, *, asked=None) -> None:
    """One of the studio's commands (commands.USER_ACTIONS), said in plain words from its message and its reply, filed
    where `asked` says (else where the user is now). An action the user stopped files its own line, with its own
    `asked` (commands.action): the one after its reply (no `asked`) is then skipped."""
    found = sink(app.hub)
    if found is None or (kind, None) in SILENT or (kind, msg.get("action")) in SILENT:
        return
    if kind == "settings":
        # What changed is said per setting by App.publish_settings (settings_published); a refusal is said here.
        if reply.get("ok") is False and reply.get("error"):
            found.add("command", f"You tried to change a setting: it didn't work ({_cut(reply['error'], 200)})",
                      state="failed", details={"ids": {"command": kind}})
        return
    if kind == "scale":
        if reply.get("ok") is not False:
            found.objective_set()
            return
    ok = reply.get("ok") is not False
    label = (before or {}).get("label")
    text = _command_text(app, kind, msg, reply, label)
    if text is None:
        return
    if not ok:
        text = f"{text}: it didn't work" + (f" ({_cut(reply.get('error'), 160)})" if reply.get("error") else "")
    details: dict = {"ids": {"command": kind, **{k: msg[k] for k in ("id", "action", "do", "what", "kind")
                                                 if isinstance(msg.get(k), (str, int, float))}}}
    if not ok:
        refused = refused_details(reply)
        details["ids"].update(refused.pop("ids", {}))
        details.update(refused)
    if kind == "cells" and ok:
        details["models"] = _population_models(app, reply.get("population"))
    if kind == "magnifier" and msg.get("action") == "level":
        # A wheel or pinch sends many levels: one line, the latest level, while they keep coming.
        last = next(reversed(found.recent.values()), None) if found.recent else None
        if last is not None and last.get("id", "").startswith("magnifier-level:") and found.clock() - last["at"] < 10:
            found.add("command", text, id=last["id"], state="done" if ok else "failed", details=details)
            return
        found.add("command", text, id=f"magnifier-level:{next(found.counter)}", state="done" if ok else "failed",
                  details=details)
        return
    if kind == "action" and reply.get("outcome") and asked is None:
        return  # a Stop's lines (stopping, stopped, ended on its own, failed) are the action's own: commands.action
    found.add("command", text, state="done" if ok else "failed", details=details, asked=asked)


STOPPING_WORDS = "Stopping: “{t}” hasn't ended yet (you pressed Stop)"
STOP_WORDS = {"stopped": "Stopped: “{t}” (you pressed Stop)",
              "completed": "“{t}” ended on its own after you pressed Stop: nothing it made was used",
              "failed": "“{t}” did not stop cleanly after you pressed Stop"}


def stopping_words(title: str) -> str:
    """The line of an action the user stopped whose work has not ended yet (commands.action)."""
    return STOPPING_WORDS.format(t=_cut(title, 60))


def stop_outcome(outcomes: list[str]) -> str:
    """How work the user stopped ended, from each of its jobs' ends (operations.Runner._stopped: "stopped",
    "completed" or "failed: …"): failed if any failed, stopped if all were stopped, else completed. None ended late:
    stopped."""
    if any(o.startswith("failed") for o in outcomes):
        return "failed"
    return "stopped" if all(o == "stopped" for o in outcomes) else "completed"


def action_stop_ended(hub, key: str, title: str, outcomes: list[str], *, asked=None, said: str = "") -> tuple[str, str]:
    """Work the user stopped that had not ended when their Stop was answered has now ended: its "Stopping" line says
    how, filed where the action was asked (`asked`). Returns (outcome, the words said), which the studio's row is told
    too (commands.action, `action-settled`)."""
    outcome = stop_outcome(outcomes)
    text = STOP_WORDS[outcome].format(t=_cut(title, 60))
    activity(hub, "command", text, id=key, state="failed" if outcome == "failed" else "done",
             details={"lines": [f"How its work ended: {', '.join(outcomes)}", *([said] if said else [])],
                      "ids": {"command": "action"}}, asked=asked)  # `said`: what the Stop did (user_stop.report, L7b)
    return outcome, text


def refused_details(reply: dict) -> dict:
    """Why a command of the studio's did not work, for its line's details: the reason as a plain sentence (with what to
    do next when the reason does not already say it), and its code under Technical (commands.refusal)."""
    from .commands import refusal

    refused = refusal(reply)
    why, upnext = refused.get("error"), refused.get("next")
    lines = [f"Why: {_sentence(why)}"] if why else []
    if upnext and upnext not in (why or ""):
        lines.append(f"What to do: {_sentence(upnext)}")
    return {**({"lines": lines} if lines else {}), **({"ids": {"code": refused["code"]}} if refused.get("code") else {})}


def _sentence(words: str) -> str:
    words = " ".join(str(words).split())
    words = words[:1].upper() + words[1:]
    return words if words.endswith((".", "!", "?", "…")) else words + "."


def _command_text(app, kind: str, msg: dict, reply: dict, label: str | None) -> str | None:
    if kind == "clear":
        what = {None: "the outlines", "cell": "the cells", "user": "your outlines", "app": "the app's outlines"}.get(
            msg.get("kind"), f"the {msg.get('kind')} outlines")
        removed = reply.get("removed")
        return f"You cleared {what}" + (f" ({removed} taken off)" if isinstance(removed, int) else "")
    if kind == "remove":
        return f"You removed {label or 'an outline'}"
    if kind == "restore":
        back = (getattr(getattr(app.hub, "scene", None), "objects", None) or {}).get(msg.get("id"))
        return f"You brought back {(back.label or 'an outline') if back is not None else 'an outline'}"
    if kind == "select":
        return f"You selected {label or 'an outline'}" if msg.get("id") else "You cleared the selection"
    if kind == "cells":
        count = reply.get("count")
        return "You asked to outline every cell" + (f": {count} found" if isinstance(count, int) else "")
    if kind == "magnifier":
        action = msg.get("action")
        if action == "zoom":
            return f"You magnified {label or 'an outline'}"
        if action == "level":
            level = reply.get("level", msg.get("level"))
            return f"You zoomed the magnifier to {level:.1f}×" if isinstance(level, (int, float)) else "You zoomed the magnifier"
        return MAGNIFIER.get(action)
    if kind == "present":
        verb = PRESENT_VERBS.get(str(msg.get("do") or ""), "changed")
        return f"You {verb} {_present_what(app, msg)}"
    if kind == "slidemap":
        return "You closed the slide map" if msg.get("open") is False else "You opened the slide map"
    if kind == "presenter":
        name = _researcher(app, msg.get("rid"))
        return {"close": "You closed the result viewer", "show": f"You opened {name or 'a result'}",
                "remove": f"You took {name or 'a researcher'} off the Working strip",
                "restore": f"You put {name or 'a researcher'} back on the Working strip"}.get(msg.get("action"))
    if kind == "showing":
        return "You stopped the showing"
    if kind == "thread":
        action = msg.get("action")
        if action == "new":
            return "You started a new conversation"
        if action == "switch":
            title = getattr(getattr(getattr(app, "day", None), "active", None), "title", "") if reply.get("ok") is not False else ""
            return f"You went back to “{_cut(title, 60)}”" if title else "You went back to another conversation"
        if action == "rename":
            return f"You renamed the conversation “{_cut(msg.get('title'), 60)}”"
        return None
    if kind == "session":
        return _session_text(app, msg, reply)
    if kind == "flag":
        return "You flagged this moment" + (f": {_cut(msg.get('note'), 120)}" if msg.get("note") else "")
    if kind == "pin-note":
        return (f"You pinned a note under {label or 'an outline'}" if msg.get("text")
                else f"You unpinned the note under {label or 'an outline'}")
    if kind == "dataset":
        return "You started recording a dataset" if msg.get("on") else "You stopped recording the dataset"
    if kind == "view":
        # A new session's home covering the live view, or the view shown again (commands.py `view`): nothing the user
        # did. The opening is said as the session's ("You started a new session") and the choice as the `source` that
        # ends it ("You went live", "You put … on stage").
        return None
    if kind == "source":
        # A session's sources (commands.py; ADR 0022): what the user put on stage, took out or put back, in its title.
        if msg.get("open") == "live":
            return "You went live"
        return f"You put “{_cut(reply.get('title') or msg.get('open'), 60)}” on stage"
    if kind == "source-remove":
        return f"You took “{_cut(reply.get('title') or msg.get('asset'), 60)}” out of this session"
    if kind == "source-restore":
        return f"You put “{_cut(reply.get('title') or msg.get('asset'), 60)}” back in this session"
    if kind == "asset-meta":
        fields = msg.get("set") if isinstance(msg.get("set"), dict) else {}
        title = _cut(reply.get("title") or msg.get("asset"), 60)
        if set(fields) == {"specimen"} and fields.get("specimen"):
            return f"You said “{title}” shows {_cut(fields['specimen'], 60)}"
        return f"You changed the details of “{title}”"
    if kind == "action":
        if reply.get("stopped"):  # the user's Stop (commands.action_cancel): stopped, never a failure
            return f"Stopped: “{_cut(reply.get('title') or msg.get('capability'), 60)}” (you pressed Stop)"
        if reply.get("stopping"):  # stopped work that had not ended yet: never claimed stopped (action_stop_ended)
            return stopping_words(reply.get("title") or msg.get("capability"))
        if reply.get("outcome") == "nothing":  # the Stop ended none of its work (none ran, or it is shared): said so
            return _cut(reply.get("summary") or f"You pressed Stop on “{reply.get('title') or msg.get('capability')}”", 200)
        if reply.get("outcome") == "completed":  # its work ended on its own just as the Stop reached it
            return STOP_WORDS["completed"].format(t=_cut(reply.get("title") or msg.get("capability"), 60))
        if reply.get("outcome") == "failed":  # said with its reason (", it didn't work (…)")
            return f"You pressed Stop on “{_cut(reply.get('title') or msg.get('capability'), 60)}”"
        said = f"You ran “{_cut(reply.get('title') or msg.get('capability'), 60)}”"
        words = reply.get("said") or reply.get("summary")  # what it measured in plain words (tables.made_words), else its summary
        return f"{said}: {_cut(words, 160)}" if words else said
    if kind == "asset-trash":
        return f"You moved “{_cut(reply.get('title') or msg.get('asset'), 60)}” to the trash"
    if kind == "asset-restore":
        return f"You took “{_cut(reply.get('title') or msg.get('asset'), 60)}” out of the trash"
    if kind == "scale":
        return "You set the objective"
    return None


def _session_text(app, msg: dict, reply: dict) -> str | None:
    """The header chip's menu and the sessions rail (commands.py `session`), said after the command ran: a new or
    opened session by its name, as the studio shows it."""
    day = getattr(app, "day", None)
    action = msg.get("action")
    ok = reply.get("ok") is not False
    if action == "new":
        name = (getattr(day, "name", "") or "") if ok else ""
        return f"You started a new session “{_cut(name, 60)}”" if name else "You started a new session"
    if action == "switch":
        label = (getattr(day, "label", "") or "") if ok else ""
        return f"You opened “{_cut(label, 60)}”" if label else "You opened another session"
    if action == "rename":
        mine = not msg.get("id") or msg.get("id") == getattr(day, "sid", None)
        return f"You renamed {'this session' if mine else 'another session'} “{_cut(msg.get('name'), 60)}”"
    if action == "keep":
        return "You kept working in this session"
    return None


def _present_what(app, msg: dict) -> str:
    what = msg.get("what")
    if isinstance(msg.get("title"), str) and msg["title"].strip():
        return _cut(msg["title"], 80)
    if not isinstance(what, str) or not what:
        return "something on screen"
    if what.startswith("tracks:") or what == "tracks":
        return "the motion tracks"
    try:
        from .evidence import evidence_of

        population = evidence_of(app.hub).get(what)
        if population is not None:
            return _cut(getattr(population, "title", "") or "the cells", 80)
    except Exception:
        pass
    return "the cells" if what in ("the cells", "cells") else _cut(what, 80)


def _researcher(app, rid) -> str | None:
    research = getattr(app, "research", None)
    session = research.get(str(rid)) if research is not None and rid else None
    return getattr(session, "name", None) if session is not None else None


def _population_models(app, population_id) -> list[dict]:
    try:
        from .evidence import evidence_of

        population = evidence_of(app.hub).get(population_id) if population_id else None
        execution_id = (getattr(population, "made_by", None) or {}).get("execution") if population is not None else None
        runner = getattr(app.hub, "runner", None)
        execution = runner.get(execution_id) if runner is not None and execution_id else None
        return (execution_models([execution]) or []) if execution is not None else []
    except Exception:
        return []


def wire(app) -> None:
    """Connect Activity to what it says (App._wire): the settings as they are now, the researchers there are, every
    researcher's change, and every tool run's end."""
    found = sink(app.hub)
    if found is None:
        return
    found.known = found.snapshot()
    found.registry = app.brain.registry
    found.seed_researchers(getattr(app.research, "sessions", {}).values())
    app.research.on_change.append(found.research_changed)
    app.brain.registry.finished.append(found.run_finished)
