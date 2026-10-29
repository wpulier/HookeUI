"""The one-brain voice's tools: a curated part of the brain's registry, called by the model with its own arguments, plus
three of its own (`mark`, `look_hard`, `do_in_hooke`). live-partner-contract.md "Tools".

Each registry tool is offered in the form the realtime API takes (`Registry.responses_tools`, reshaped) and run the way
the brain runs it: one request of its own (a voice Ticket with its admission and request id), a voice `ToolContext`
whose actor is the Assistant and whose voice owner is this session (researchers it starts report back to it), the
accepted studio's pin when there is one, and the brain's `ActionRunner` (outline references, the board, late steps).
What it did goes into the chat as the brain's steps do (`chat-action` rows) and its receipt back to the model.

Act, then look: after a tool that changes the screen, the studio is asked for a fresh report and a fresh window picture,
which go back with the result (`_look`), so the model sees whether it worked before it says so. A tool still running
after TOOL_WAIT_S is answered "still working"; its outcome is said later as one line (a long tool done).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import time
import uuid
from types import SimpleNamespace
from typing import Any

from .base import ToolSpec
from . import knowing
from .hands import Hands, Intent, intent_of
from .records import Records

# The registry's tools the voice gets, by name (tests/golden/tool_schemas.json): those this registry has are offered.
# Web search is retrieve_page; a capability by name is run_capability (there once one is turned on); pictures are
# reference_photo; documents and windows are present.
PARTNER_TOOLS = ("present", "tabs", "show_measurements", "highlight", "find", "outline_cells", "select", "zoom", "clear",
                 "start_researcher", "research_status", "researcher_note", "cancel_researcher", "show_me", "resources",
                 "models_in_use", "capabilities", "workflow", "new_project", "switch_session", "retrieve_page",
                 "run_capability", "reference_photo", "use", "set_default")
# What `think`'s backend may call besides the voice's own (voice/think.py): the brain's longer tools, comparisons of
# models among them. Never think itself, mark or look_hard (the voice points; the backend has the pictures).
# Every capability the Actions panel can run is reachable here too (tests/test_partner_reach.py): the panel's own tools
# that the voice does not call directly go to think, which acts through Hands as the voice does.
THINK_TOOLS = PARTNER_TOOLS + ("compare_models", "count", "measure", "query_population", "group_cells",
                               "describe_groups", "read_result", "summarize_research", "researcher_followup",
                               "revise_work", "make_table", "edit_table", "analyze", "make_graph", "read_analysis",
                               "recall", "where_is", "magnifier_picture", "show_result", "close_result",
                               "close_magnifier", "show_in_magnifier", "dissect", "contrast", "enhance", "snapshot",
                               "outcomes", "individuals", "follow_cells", "track_motion", "watch", "watching",
                               "stop_watching", "what_happened", "hold", "focus_check", "set_style", "name_outline",
                               "bookmark", "way_to_bookmark", "show_map", "calibrate_scale", "set_objective",
                               "measure_saved_objects", "retain_population_labels", "compare_retained_methods",
                               "register_volume", "describe_capability", "find_workflows", "assets", "add_asset")
# The tools whose call may come back asking permission (Hands: a paid model, a long researcher run): they take the
# approval back.
PERMISSION_TOOLS = frozenset({"present", "tabs", "show_measurements", "highlight", "select", "zoom", "clear",
                              "outline_cells", "find", "start_researcher", "run_capability"})
HANDS_TOOLS = PERMISSION_TOOLS  # the name the tests knew
TURN_CHOICE_S = 20.0  # a voice `use` holds its choice for the later steps of its turn this long
APPROVED = {"approved": {"type": "string", "description": (
    "Only after Hands asked permission for this (its result had result \"permission\") and the user said yes: that "
    "approval's id.")}}
MAX_RESEARCHERS_A_TURN = 3  # deliberately distinct researchers one request may start (distinct_from)
DISTINCT = {"distinct_from": {"type": "string", "description": (
    "Only when the user asked for more than one researcher in this request: the brief of the other researcher this "
    "one is not. Leave it out otherwise: one request starts one researcher.")}}
# Tools that change what is on screen: the model gets a fresh look after them (act, then look).
SCREEN_CHANGING = frozenset({"present", "tabs", "show_measurements", "highlight", "find", "outline_cells", "select",
                             "zoom", "clear", "new_project", "switch_session", "reference_photo", "show_me",
                             "run_capability"})
# One call per user turn: the same call again in the same turn returns the first one's result (one request, one
# researcher; live 2026-10-28: two researchers from one request).
ONCE_PER_TURN = frozenset({"start_researcher", "new_project", "switch_session", "cancel_researcher"})
TOOL_WAIT_S = 12.0  # a tool still running after this is answered "still working"; its outcome follows as one line
# Slow tools the voice starts and goes on from, as it does think: "started" at once (one short acknowledgement), a
# progress line while they run, their result said once when it lands, with a mark on what they showed (think-broad-2:
# a reference photo took ~20 s, a web lookup timed out at 12 s; both were narrated as waits and landed in later steps).
BACKGROUND = frozenset({"reference_photo", "retrieve_page"})
STARTED_TOOLS = frozenset({"think"}) | BACKGROUND  # answered "started": their result is said on its own (openai_realtime)
THINK_TOOL_WAIT_S = 70.0  # behind the voice, think waits this long for a long tool's real outcome (a comparison)
WORDS_WAIT_S = 1.5  # a tool waits at most this long for the user's words of its turn (their transcription)
REPORT_WAIT_S = 2.0  # act, then look: the studio's fresh report, at most
MARKS_WAIT_S = 1.0  # the studio's marks-shown, at most
# A Tab tool called while the studio is landing the Project just opened (a voice switch_session or new_project) waits
# this long for the studio's report to show that Project's Tabs, then is tried once more (bench think-broad-1).
LANDING_WAIT_S = 5.0
TAB_TOOLS = frozenset({"show_measurements", "tabs", "present"})
LANDING_WORDS = "waiting for the studio to finish opening this Project's Tabs, then trying once more"
STYLES = ("box", "point", "underline")

MARK = {
    "type": "object",
    "properties": {
        "targets": {"type": "array", "minItems": 1, "maxItems": 6, "items": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": (
                    "What to point at, by Hooke's own id: outline:<id> (one outline), population:<id> (a whole layer of "
                    "cell outlines, e.g. population:e1), member:<population>:<member>, window:<id>, "
                    "document:<placement>, tab:<id or exact title>, researcher:<number or id>, panel:<name> "
                    "(magnifier, viewer, slidemap, card, made-today, sidebar, live, workspace-tabs), or \"frame\" with "
                    "a region or polygon in the camera picture you saw.")},
                "region": {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4,
                           "description": "frame only: [x, y, w, h], 0-1 of the camera picture"},
                "polygon": {"type": "array", "items": {"type": "array", "items": {"type": "number"},
                                                       "minItems": 2, "maxItems": 2},
                            "description": "frame only: [[x, y], ...], 0-1 of the camera picture"},
            },
            "required": ["target"]}},
        "style": {"type": "string", "enum": list(STYLES)},
        "label": {"type": "string", "maxLength": 60},
    },
    "required": ["targets"],
}


def realtime_tool(declaration: dict) -> dict:
    """A Responses function declaration as the realtime API takes it: name, description and parameters, without the
    brain's plan metadata (after_steps)."""
    params = json.loads(json.dumps(declaration.get("parameters") or {"type": "object"}))
    params.get("properties", {}).pop("after_steps", None)
    return {"name": declaration["name"], "description": declaration.get("description") or "", "parameters": params}


class PartnerTools:
    """The tools of one partner session. `partner` (voice/partner.py) gives the turn's words, the studio link, the
    camera and the screen as text."""

    def __init__(self, brain, hub, partner, *, client=None, dialogue=None) -> None:
        self.brain, self.hub, self.partner, self.client, self.dialogue = brain, hub, partner, client, dialogue
        self._once: dict[tuple, asyncio.Future] = {}  # (turn, tool) -> its result, for ONCE_PER_TURN
        self._screen = asyncio.Lock()  # tools that change the screen run one after another (parallel tool calls)
        self.reported: set[str] = set()  # action ids whose outcome went back to the model
        self.held: set[str] = set()  # action ids think waits on: their outcome goes back to think, not said alone
        self.fail_next: str | None = None  # the bench's fault hook (MICROMIND_BENCH_FAULTS): the next call fails
        self.running: dict[object, tuple] = {}  # calls still going: (tool, started, run), for a specific progress line
        self.thinking: dict[str, asyncio.Task] = {}  # think's tasks by id
        self._task_started: dict[str, float] = {}  # task behind the voice -> when it started (its progress line)
        self._task_now: dict[str, str] = {}  # task -> what it is doing now, in words
        # Hands: actions in Hooke typed, cued, verified and yielding to the user (hands.py); None runs tools directly.
        self.hands: Hands | None = Hands(send=self._send_studio, observe=self._observation,
                                         log=lambda record: self.partner.log(record), hub=hub,
                                         records=Records(hub, services=getattr(brain, "services", None),
                                                         shown=self.project_shown, landed=self.project_landed))
        self.hands.project = self._project
        self.hands.turn = lambda: self.partner.turn()
        self.hands.reconciled = self._reconciled
        self._chosen: dict[int, tuple[dict, dict]] = {}  # turn -> a voice `use`'s choices, for its turn's later steps
        self._steps: dict[str, int] = {}  # think task -> its calls so far (a call's step when the backend names none)
        self.backend = None  # think's backend factory (option -> backend); tests pass a fake

    def running_facts(self) -> str:
        """What is still running, for a progress line that says something: the tool, how long so far, its own words
        of what it waits on (ToolRun.progress), the models it runs on."""
        now = time.monotonic()
        rows = []
        for name, started, run in list(self.running.values()):
            row = f"{name}, {now - started:.0f} s so far"
            if getattr(run, "progress", None):
                row += f", {run.progress}"
            rows.append(row)
        return "; ".join(rows)

    # --- what the model is offered ------------------------------------------------------------------
    def names(self) -> list[str]:
        tools = getattr(self.brain.registry, "tools", {})
        return [name for name in PARTNER_TOOLS if name in tools]

    def specs(self) -> list[ToolSpec]:
        declared = {d["name"]: d for d in self.brain.registry.responses_tools()}
        specs = []
        for name in self.names():
            shaped = realtime_tool(declared[name])
            specs.append(ToolSpec(name, shaped["description"], shaped["parameters"], self._runner(name)))
        specs.append(ToolSpec("mark", "Point at things on Hooke's screen while you talk about them: an outline, window, "
                              "Tab, researcher card or panel by its id, or a region of the camera picture. Returns which "
                              "marks the studio drew.", MARK, self.mark))
        specs.append(ToolSpec("look_hard", "Look closely at the camera picture (and Hooke's window if needed) with the "
                              "app's most exact naming model when a detail or a name matters and you are unsure. It "
                              "takes a few seconds, so tell the user you are taking a closer look. Returns its answer.",
                              {"type": "object", "properties": {
                                  "question": {"type": "string"},
                                  "window": {"type": "boolean", "description": "also send Hooke's window picture"}},
                               "required": ["question"]}, self.look_hard))
        specs.append(ToolSpec("activity", knowing.DESCRIPTION, knowing.SPEC, self.activity))
        specs.append(ToolSpec("think", "Hand a hard question, a request with several steps, a comparison of models or a "
                              "close look to Hooke's frontier mind, which sees this turn's pictures, uses Hooke's tools, "
                              "checks the screen and reports back. Returns at once that it started; its result is told "
                              "to you to say.",
                              {"type": "object", "properties": {
                                  "request": {"type": "string", "description": "what the user asked, in their words"},
                                  "why": {"type": "string", "description": "why it needs more than your own tools"}},
                               "required": ["request"]}, self.think))
        for spec in specs:
            if spec.name == "start_researcher":  # a second, deliberately distinct researcher is the model's to declare
                params = json.loads(json.dumps(spec.parameters))
                params.setdefault("properties", {}).update(DISTINCT)
                spec.parameters = params
            if self.hands is not None and spec.name in PERMISSION_TOOLS:  # an approval Hands asked for, given back
                params = json.loads(json.dumps(spec.parameters))
                params.setdefault("properties", {}).update(APPROVED)
                spec.parameters = params
        return specs

    async def activity(self, args: dict) -> dict:
        """Recent Activity lines of the Project open, newest first (knowing.py)."""
        return knowing.read(self.hub, args or {}, project=self._project())

    async def _reconciled(self, intent: Intent, receipt: dict) -> None:
        """An action cut off by the user's takeover has ended: its real receipt goes to the voice as context, so a later
        question about it is answered from it (its uncertain receipt was what was said)."""
        facts = {"tool": intent.tool, "receipt": receipt.get("result"), "said": receipt.get("said"),
                 **({"error": receipt["error"]} if receipt.get("error") else {})}
        with contextlib.suppress(Exception):
            await self.partner.happened("hands-reconciled", "An action cut off when the user took over has now ended "
                                        "(context only, say nothing unless asked): "
                                        + json.dumps(facts, ensure_ascii=False, default=str), delivery="context")

    def _registry_words(self, name: str) -> dict:
        """The registry's own words for a tool, for its intent: kind (effect or query), what it produces, its family."""
        tool = (getattr(self.brain.registry, "tools", {}) or {}).get(name)
        if tool is None:
            return {}
        words: dict = {"kind": getattr(tool, "kind", "effect"), "family": getattr(tool, "family", ""),
                       "sets": tuple(getattr(tool, "sets", ()) or ())}
        with contextlib.suppress(Exception):
            words["produces"] = tool.produces
        return words

    def intent(self, name: str, args: dict) -> Intent | None:
        """The call's typed intent (hands.intent_of), None for a look or without Hands."""
        if self.hands is None:
            return None
        found = intent_of(name, args, **self._registry_words(name))
        if found is not None and found.kind in ("outline", "find") and not found.model:
            # outline(model, target): the model it outlines with, a choice this turn made (use) or the setting.
            group = "cells" if name == "outline_cells" else "outline"
            chosen = (self._chosen.get(self.partner.turn()) or ({}, {}))[1]
            found.model = str(chosen.get(group) or getattr(getattr(self.hub, "settings", None), group, "") or "")
        return found

    def _project(self) -> str | None:
        with contextlib.suppress(Exception):
            from ..resources import resources_of

            return resources_of(self.hub).session()
        return None

    async def _send_studio(self, message: dict) -> None:
        if getattr(self.partner, "send", None) is not None:
            await self.partner.send(message)

    def _observation(self) -> dict | None:
        """The bound studio's latest report (Hands' facts), or the studio whose report counts."""
        screen = getattr(self.hub, "screen", None)
        if screen is None:
            return None
        client = self.client if self.client is not None else getattr(screen, "current_accepted", lambda: None)()
        observed = screen.observation(client) if client is not None and hasattr(screen, "observation") else None
        return observed.wire() if observed is not None else None

    def think_tools(self) -> list[dict]:
        """The tools think's backend may call, as {name, description, parameters}."""
        tools = getattr(self.brain.registry, "tools", {})
        declared = {d["name"]: d for d in self.brain.registry.responses_tools()}
        return ([realtime_tool(declared[name]) for name in THINK_TOOLS if name in tools and name in declared]
                + [{"name": "activity", "description": knowing.DESCRIPTION, "parameters": knowing.SPEC}])

    def _runner(self, name: str):
        async def run(args: dict) -> dict:
            return await self.run(name, args)
        return run

    # --- a registry tool ---------------------------------------------------------------------------
    async def run(self, name: str, args: dict, *, n: int | None = None, patient: bool = False,
                  action_id: str | None = None) -> dict:
        """One call of a registry tool. Every acting one goes through Hands (its contract and receipt); a look runs
        directly and its result is its answer. `patient`: a caller behind the voice (think) that waits for a long
        tool's outcome: past TOOL_WAIT_S the result carries `_pending` for `outcome`, and the outcome is held from being
        said on its own. `action_id`: the action's id (the voice's call id, or think's task and step): a retry is never
        a second effect."""
        call_id = str((args or {}).get("_call_id") or "") or action_id or None
        args = {k: v for k, v in (args or {}).items() if not k.startswith("_") and k != "after_steps"}
        n = self.partner.turn() if n is None else n
        if name in BACKGROUND and not patient:
            return self.background(name, args, n)
        distinct = str(args.pop("distinct_from", "") or "").strip() if name == "start_researcher" else ""
        approved = str(args.pop("approved", "") or "") or None
        if self.fail_next is not None:
            why, self.fail_next = self.fail_next, None
            return {"ok": False, "error": why, "_log": {"fault": "bench"}}
        if name not in ONCE_PER_TURN:
            return await self._act(name, args, n, patient, call_id, approved)
        # One per request (one researcher, one Project): the turn's slot is taken before anything awaits, so a second
        # call of it in the same turn, in parallel or later and whatever its words, gets the first's receipt.
        key = (n, name)
        if distinct:
            # A researcher the model declares distinct from another this request asked for ("make two researchers, one
            # does X, the other Y"): a slot of its own, up to MAX_RESEARCHERS_A_TURN; a repeat of the same one still
            # waits for the first (the check is the declared other brief differing from this one's).
            brief = json.dumps(args, sort_keys=True, default=str)
            others = [k for k in self._once if k[:2] == (n, name)]
            if distinct.lower() in brief.lower() or len(others) >= MAX_RESEARCHERS_A_TURN:
                key = (n, name)  # not distinct after all, or too many: the turn's one slot
            else:
                key = (n, name, brief)
        first = self._once.get(key)
        if first is not None:
            result = await asyncio.shield(first)
            return {**{k: v for k, v in result.items() if not k.startswith("_")},
                    "note": "already done for this request; not done again"}
        self._once = {k: v for k, v in self._once.items() if k[0] == n}  # only this turn's are kept
        slot = self._once[key] = asyncio.get_running_loop().create_future()
        try:
            result = await self._act(name, args, n, patient, call_id, approved)
        except BaseException as exc:
            if not slot.done():
                slot.set_result({"ok": False, "error": f"it did not finish ({type(exc).__name__})"})
            raise
        if not slot.done():
            slot.set_result(result)
        return result

    async def _act(self, name: str, args: dict, n: int, patient: bool, call_id: str | None,
                   approved: str | None) -> dict:
        """The call under its contract (Hands), or a look run directly."""
        from .. import user_stop

        intent = self.intent(name, args)
        if intent is None:
            result = await self._effect(name, args, n, patient, None)()
            result.pop("_run", None)
            return result
        hands = self.hands
        # Planned against what was seen: the voice's turn at its key-down (or its last look after an action), think's
        # call at its start (the spec, 4.2: checked at the moment of dispatch against what it was planned on).
        plan = hands.plan(None if patient else n, fresh=patient)
        holder = user_stop.Stop() if intent.ui and not intent.background else None
        receipt = await hands.act(name, args, self._effect(name, args, n, patient, holder), approved=approved,
                                  from_think=patient, action_id=call_id, plan=plan,
                                  stop=holder.stop if holder is not None else None, intent=intent)
        if receipt.get("_look") is not None and plan.epoch == hands.epoch:
            hands.fresh_look()  # it saw the screen after its action, and nobody took over meanwhile
        return receipt

    def _effect(self, name: str, args: dict, n: int, patient: bool, holder):
        """The tool's run, as Hands runs it: changes on screen one after another, each with its own look after it; under
        `holder` (user_stop.Stop) where a takeover may stop it."""
        async def effect() -> dict:
            from .. import user_stop

            token = user_stop.STOP.set(holder) if holder is not None else None
            try:
                if name in SCREEN_CHANGING:
                    async with self._screen:
                        return await self._run(name, args, n, patient)
                return await self._run(name, args, n, patient)
            finally:
                if token is not None:
                    user_stop.STOP.reset(token)
        return effect

    async def settle(self, name: str, args: dict, early: dict, pending: tuple, action_id: str | None = None) -> dict:
        """A long tool's outcome, waited for behind the voice (outcome), with its receipt checked again as it landed."""
        result = await self.outcome(name, early, *pending)
        intent = self.intent(name, args)
        if result is early or intent is None:
            result.pop("_run", None)
            return result
        receipt = await self.hands.verify(intent, result)
        receipt.pop("_run", None)
        self.hands.keep(action_id, receipt)
        return receipt

    async def _run(self, name: str, args: dict, n: int, patient: bool = False) -> dict:
        words = await self.partner.words(n, WORDS_WAIT_S)
        before = self._reported_at()
        key = object()
        self.running[key] = (name, time.monotonic(), None)  # from its start: the runner waits its inline time first
        landing = None
        try:
            run, ctx = await self._start(name, args, words, n)
            self.running[key] = (name, self.running[key][1], run)
            self._stoppable(run)
            if run.ok is None and run.task is not None:
                await asyncio.wait([run.task], timeout=TOOL_WAIT_S)
            if run.ok is False and name in TAB_TOOLS and self.project_shown() is False:
                # Called while the studio lands the Project just opened: its Tabs are not showing yet. Said as waiting
                # (the progress line's facts), then tried once more when they show; never failed for a studio a moment
                # behind, and never retried twice.
                self.running[key] = (name, self.running[key][1], SimpleNamespace(progress=LANDING_WORDS))
                landing = await self.project_landed()
                if landing:
                    run, ctx = await self._start(name, args, words, n)
                    self.running[key] = (name, self.running[key][1], run)
                    self._stoppable(run)
                    if run.ok is None and run.task is not None:
                        await asyncio.wait([run.task], timeout=TOOL_WAIT_S)
        finally:
            self.running.pop(key, None)
        action = getattr(getattr(run, "occurrence", None), "action_id", None)
        if patient and action and run.task is not None and not run.task.done():
            self.held.add(action)  # before anything awaits: landing now, it is filed quietly (_late), not said
        result = await self._answer(name, run, ctx, before, landing=landing)
        if action in self.held:
            result["_pending"] = (run, ctx, before, action)
        return result

    @staticmethod
    def _stoppable(run) -> None:
        """The run a takeover's Stop cancels (user_stop.Stop.task), cancelled now if the Stop came while it started."""
        from .. import user_stop

        holder = user_stop.current()
        if holder is None or run.task is None:
            return
        holder.task = run.task
        if holder.requested and not run.task.done():
            run.task.cancel()

    async def _answer(self, name: str, run, ctx, before: float, *, file: bool = True,
                      landing: bool | None = None) -> dict:
        """A run's result for the model: its receipt, filed in the chat, and, after a change on screen, a fresh look."""
        log: dict[str, Any] = {"requestId": getattr(getattr(run, "occurrence", None), "request_id", None)}
        result = self._result(run)
        if landing is not None:
            log["landing"] = "landed, tried again" if landing else "not landed"
        if landing is False:
            result["summary"] = ("not done: the studio is still opening this Project's Tabs (it has not shown them for "
                                 f"{LANDING_WAIT_S:.0f} s)")
            result["next"] = "ask again in a moment"
        if name == "models_in_use":  # the app's own list leaves out the voice answering: this one
            result["this_voice"] = (f"{self.voice_label()}: the voice the user hears, which also sees the camera and "
                                    "Hooke's window and acts with Hooke's tools")
        if file:
            self._file(run, ctx)
        if run.ok is not None and getattr(run.occurrence, "action_id", None):
            self.reported.add(run.occurrence.action_id)
        if name == "start_researcher" and run.ok:
            rid = self._researcher_of(run)
            if rid:
                log.update(rid=rid, ridT=time.time())
                result["researcher"] = rid
        if name in SCREEN_CHANGING and run.ok is not False:
            look, facts = await self.look_after(name, before)
            result["_look"] = look
            log.update(facts)
            points = self.points_of(run)
            if points and run.ok:
                # Pointed at by the app as the answer about it is heard: the model need not call mark for it.
                result["_point"], result["pointed_at"] = points, points
                result["pointing"] = "Hooke points at this as you speak about it: do not call mark for it."
        result["_log"] = log
        result["_run"] = run  # for Hands' receipt (records.py); never sent to the model
        return result

    async def outcome(self, name: str, early: dict, run, ctx, before: float, action: str) -> dict:
        """Behind the voice, think waits for a long tool's real outcome (a comparison of three models), up to
        THINK_TOOL_WAIT_S, and gets its result as the voice would have; past that it is told it is still running, and the
        outcome is said as one line when it lands, as any long tool's is (think-broad-1: "they're still running")."""
        try:
            await asyncio.wait([run.task], timeout=THINK_TOOL_WAIT_S)
        finally:
            self.held.discard(action)
        if not run.task.done():
            return early
        filed = action in self.reported  # _late filed it already, quietly, while it was held
        if name in SCREEN_CHANGING:
            async with self._screen:
                result = await self._answer(name, run, ctx, before, file=not filed)
        else:
            result = await self._answer(name, run, ctx, before, file=not filed)
        result["_log"] = {**early.get("_log", {}), **result.get("_log", {}), "waited": True}
        return result

    def project_shown(self) -> bool | None:
        """Whether the bound studio's latest report shows the open Project's Tab strip (data_reader._strip); None when
        there is no bound studio report to read (nothing to wait for)."""
        from ..data_reader import _strip
        from ..resources import resources_of

        screen = getattr(self.hub, "screen", None)
        if self.client is None or screen is None:
            return None
        try:
            observation = screen.observation(self.client)
        except Exception:
            return None
        if observation is None:
            return None
        return _strip(observation, resources_of(self.hub).session()) is not None

    async def project_landed(self) -> bool:
        """The studio's report showing the open Project's Tabs, within LANDING_WAIT_S (it is asked for one at once)."""
        screen = getattr(self.hub, "screen", None)
        with contextlib.suppress(Exception):
            screen.broadcast({"type": "screen-ask"})
        deadline = time.monotonic() + LANDING_WAIT_S
        while time.monotonic() < deadline:
            if self.project_shown():
                return True
            await asyncio.sleep(0.05)
        return bool(self.project_shown())

    def task_facts(self, task: str, what: str) -> str:
        """What one task behind the voice is doing now and for how long, for its own progress line only (not every
        call running: think-check-2 mixed a comparison and a photo in one line)."""
        started, now = self._task_started.setdefault(task, time.monotonic()), self._task_now.get(task) or what
        return f"{now}, {time.monotonic() - started:.0f} s so far"

    def background(self, name: str, args: dict, n: int) -> dict:
        """A slow tool started behind the voice (BACKGROUND): answered at once, its result said when it lands."""
        task = f"{name.split('_')[0][:4]}-{uuid.uuid4().hex[:10]}"
        self._task_started[task] = time.monotonic()
        job = self.partner.spawn(self._background(name, args, n, task))
        session = getattr(self.partner, "session", None)
        if session is not None and hasattr(session, "tell_progress"):
            self.partner.spawn(session.tell_progress(task, lambda: not job.done(), what=name, lines=1,
                                                     facts=lambda: self.task_facts(task, name)))
        return {"started": True, "task": task, "_log": {"task": task, "startedT": time.time()},
                "note": "Started; it takes a while. Say only a two- or three-word acknowledgement now, nothing about "
                        "its result (do not say it is found or shown): it is told to you to say when it lands."}

    async def _background(self, name: str, args: dict, n: int, task: str) -> None:
        """Its receipt is checked as its result lands (a reference photo: its window in the studio's report)."""
        result = await self.run(name, args, n=n, patient=True)
        pending = result.pop("_pending", None)
        if pending is not None:
            key = object()
            self.running[key] = (name, time.monotonic(), pending[0])
            try:
                result = await self.settle(name, args, result, pending)
            finally:
                self.running.pop(key, None)
        points = list(result.get("_point") or [])
        if name == "reference_photo" and result.get("ok"):  # its own window, named in its receipt
            points += [f"window:{w}" for w in re.findall(r"\bin window (w-[0-9a-f-]+)", str(result.get("summary")))]
        facts = {"tool": name, "asked": args, **{k: v for k, v in result.items() if not k.startswith("_")},
                 **({"shown_confirmed": result["_log"].get("confirmed")} if "confirmed" in result.get("_log", {}) else {})}
        log = getattr(self.partner, "log", lambda record: None)
        log({"kind": "partner-background", "task": task, "name": name, "n": n, "ok": result.get("ok"),
             "pointAt": points, "doneT": time.time()})
        await self.said_when_landed(task, name, facts, "", points)

    async def said_when_landed(self, task: str, what: str, facts: dict, say: str, points: list[str]) -> None:
        """A result from behind the voice (think, a slow tool), said once: RealtimeSession prepares and says it with its
        marks; any other partner session (Gemini 3.8 Live) says it as the one proactive line every session says."""
        session = self.partner.session
        if hasattr(session, "deliver"):
            await session.deliver(task, facts=facts, say=say, points=points, what=what)
        else:
            text = json.dumps({"tool": what, "task": task, **facts}, ensure_ascii=False, default=str)
            await self.partner.happened(f"{what}-done", text, ref=task, mark=points[0] if points else None)

    def voice_label(self) -> str:
        from ..options import VOICE

        model = getattr(getattr(self.partner, "session", None), "config", None)
        model = getattr(model, "model", "gpt-realtime-2.1")
        option = VOICE.get("gpt-realtime-2.1")
        return f"{model} ({option.label if option else 'one brain'}, cloud)"

    async def _start(self, name: str, args: dict, words: str, n: int | None = None):
        """The tool as the brain runs it, in a voice request of its own, attributed to the Assistant by voice. A `use`
        earlier in the same turn holds for this call (its choices on this request's ticket): "use SAM 3.1 for this"
        then the outline it was for."""
        from ..brain.actions import ActionRunner
        from ..brain.registry import ToolContext
        from ..ticket import issue, using

        brain, hub = self.brain, self.hub
        conversation = brain.conversation
        request = issue(hub, "turn", asked=conversation.here(), channel="voice")
        chosen = self._chosen.get(n) if n is not None else None
        if chosen is not None:  # the same dicts: what this step uses is marked used on the choice itself
            request.choices.update(chosen[0])
            request.overrides.update({k: v for k, v in chosen[1].items() if k not in request.overrides})
        with using(request):
            try:
                info = hub.latest_info
                board = getattr(brain, "board", None)
                # "That" is what was pointed at when the user pressed talk (board.WhenAsked), not after.
                asked_at = self.partner.when_asked(self.partner.turn())
                if asked_at is None and board is not None:
                    asked_at = board.when_asked()
                ctx = ToolContext("voice", 0, info.id if info else None, question=words,
                                  asked=request.asked, admission=request.admission,
                                  request_id=request.admission.request_id, voice_owner=self.partner.session,
                                  when_asked=asked_at)
                if self.client is not None:
                    with contextlib.suppress(Exception):  # no current report: the tool runs on the app's own view
                        pin = hub.screen.pin(self.client, request_id=request.admission.request_id,
                                             timing="server-handoff-arrival")
                        ctx.client, ctx.request_snapshot = self.client, pin
                        ctx.workspace_id = pin.observation.client.workspace_id
                selector = getattr(getattr(brain, "sight", None), "select", None)
                if callable(selector):
                    with contextlib.suppress(Exception):
                        ctx.sight_selection = selector(request)
                runner = ActionRunner(brain.registry, hub, lambda: brain.sight, self._late, board,
                                      log=getattr(brain.agent, "log", None))
                runs = await runner.run([(name, args)], ctx)
                if name == "use" and runs and runs[0].ok and n is not None and request.choices:
                    # Held for the turn's later steps; a choice none of them uses is still said (ticket.UNUSED_CHOICE).
                    request.keep_choices(TURN_CHOICE_S)
                    self._chosen = {n: (request.choices, request.overrides)}
            finally:
                request.seal()
        return runs[0], ctx

    @staticmethod
    def _result(run) -> dict:
        out: dict[str, Any] = {"ok": run.ok, "summary": run.summary}
        if run.ok is None:
            out["still_working"] = True
            out["note"] = "still running; its outcome is told to you when it lands. Do not say it is done."
            if run.progress:
                out["progress"] = run.progress
        if run.next:
            out["next"] = run.next
        if run.answer:
            out["answer"] = run.answer
        if run.outputs:
            out["outputs"] = [o.wire() for o in run.outputs][:8]
        if run.resource:
            out["resource"] = run.resource
        if run.ok is False and not run.summary:
            out["error"] = "it did not work"
        if getattr(run, "stopped", False):
            out["stopped"] = True
        return out

    def _file(self, run, ctx) -> None:
        """Into the chat as the brain's steps are (a chat-action row by its action id), the same by voice as typed."""
        if self.dialogue is None:
            return
        with contextlib.suppress(Exception):
            self.dialogue.show({"type": "chat-action", "turnId": None, **run.wire(), "standalone": True}, asked=ctx.asked)
        if run.ok is not None:
            with contextlib.suppress(Exception):
                self.brain._remember_run_outputs(run, ctx, unbound=True)

    async def _late(self, run, ctx) -> None:
        """A step that finished after its call was answered "still working": its row and receipt, and one line. One
        that finished within the call's own wait was filed and answered already."""
        action = getattr(run.occurrence, "action_id", None)
        if action in self.reported:
            return
        self._file(run, ctx)
        if action:
            self.reported.add(action)
        if action in self.held:  # think is waiting for it: its outcome goes back to think and is said in think's result
            return
        status = "done" if run.ok else ("stopped" if run.stopped else "failed")
        text = json.dumps({"tool": run.tool, "status": status, "summary": run.summary,
                           **({"next": run.next} if run.next else {})}, ensure_ascii=False)
        await self.partner.happened("tool-done", text, ref=action or run.tool)

    @staticmethod
    def points_of(run) -> list[str]:
        """What a run put on screen, as mark targets: a layer of cell outlines, an outline."""
        out = []
        for output in run.outputs or []:
            wire = output.wire()
            if wire.get("kind") == "population" and wire.get("id"):
                out.append(f"population:{wire['id']}")
            elif wire.get("kind") == "outline" and wire.get("id"):
                out.append(f"outline:{wire['id']}")
        return out[:4]

    @staticmethod
    def _researcher_of(run) -> str | None:
        """The researcher a start_researcher run made, from its typed output (manager.attempt_ref)."""
        for output in run.outputs or []:
            ref = output.wire().get("resource")
            if isinstance(ref, dict) and ref.get("kind") == "research-run" and isinstance(ref.get("researcherId"), str):
                return ref["researcherId"]
        return None

    def _reported_at(self) -> float:
        screen = getattr(self.hub, "screen", None)
        try:
            return float(screen.reported_at) if screen is not None else 0.0
        except Exception:
            return 0.0

    async def look_after(self, name: str, before: float) -> tuple[dict, dict]:
        """Act, then look: the studio's report newer than the change, then its window picture and the screen as text."""
        screen = getattr(self.hub, "screen", None)
        report_at = None
        if screen is not None and hasattr(screen, "until"):
            with contextlib.suppress(Exception):
                if await screen.until(lambda report: True, before, timeout=REPORT_WAIT_S):
                    report_at = time.time()
        shot = await self.partner.snapshot()
        text = f"After {name}, Hooke's screen now: " + (self.partner.screen_now() or "(no report from the studio)")
        images = [(shot["jpeg"], f"Hooke's window after {name} ({shot.get('how') or 'composed'})")] if shot else []
        facts = {"screenChange": True, "reportT": report_at, "confirmed": report_at is not None}
        # Full detail: whether the change shows is read from this picture (at low detail the model took the new layer
        # for "hidden behind the tabs panel", the partner bench of 2026-10-29).
        return {"images": images, "text": text, "detail": "auto"}, facts

    # --- its own tools -----------------------------------------------------------------------------
    async def mark(self, args: dict) -> dict:
        """Draw marks in the studio; what it reports drawn decides what the model is told."""
        style = args.get("style") if args.get("style") in STYLES else "box"
        marks, frame = [], None
        for target in (args.get("targets") or [])[:6]:
            if isinstance(target, str):
                target = {"target": target}
            if not isinstance(target, dict) or not isinstance(target.get("target"), str):
                continue
            mark = {"target": target["target"][:120], "style": style}
            if args.get("label"):
                mark["label"] = str(args["label"])[:60]
            for key in ("region", "polygon"):
                if target["target"] == "frame" and isinstance(target.get(key), list):
                    mark[key] = target[key]
                    frame = self.partner.last_frame
            marks.append(mark)
        if not marks:
            return {"ok": False, "error": "no target to mark"}
        shown = await self.partner.marks(marks, frame=frame, timeout=MARKS_WAIT_S)
        if shown is None:
            return {"ok": False, "error": "the studio did not say it drew them; do not say they are shown",
                    "_log": {"marks": [m["target"] for m in marks]}}
        resolved = [r for r in shown.get("resolved") or [] if isinstance(r, dict)]
        drawn = [r.get("target") for r in resolved if r.get("ok")]
        missed = [{"target": r.get("target"), "why": r.get("why") or "not drawn"} for r in resolved if not r.get("ok")]
        return {"ok": bool(drawn), "shown": drawn, **({"not_shown": missed} if missed else {}),
                **({"note": "Marked. If your words before it already answered the user, end now without a word; "
                            "otherwise answer them now in one short sentence, without mentioning the mark."}
                   if drawn else {})}

    async def _bounded(self, name: str, work, call_id: str | None) -> dict:
        """A tool of its own with the registry tools' limit: past TOOL_WAIT_S it is answered "still working", and what
        it found is said as one line when it lands (a long tool done)."""
        task = asyncio.ensure_future(work)
        done, _ = await asyncio.wait({task}, timeout=TOOL_WAIT_S)
        if task in done:
            return task.result()

        def landed(t: asyncio.Task) -> None:
            if t.cancelled():
                return
            if t.exception() is not None:
                found = {"tool": name, "status": "failed", "error": "something went wrong in the app"}
            else:
                found = {"tool": name, **{k: v for k, v in t.result().items() if not k.startswith("_")}}
            asyncio.ensure_future(self.partner.happened("tool-done", json.dumps(found, ensure_ascii=False, default=str),
                                                        ref=call_id or name))
        task.add_done_callback(landed)
        return {"ok": None, "still_working": True,
                "note": "still running; its outcome is told to you when it lands. Do not say it is done."}

    async def look_hard(self, args: dict) -> dict:
        return await self._bounded("look_hard", self._look_hard(args), args.get("_call_id"))

    async def _look_hard(self, args: dict) -> dict:
        """The frame (and the window, when asked) with the question, to the close-look model (option LOOK_HARD)."""
        question = str(args.get("question") or "").strip()
        if not question:
            return {"ok": False, "error": "no question"}
        images = []
        seen = await self.partner.camera(1024)
        if seen is not None:
            images.append(seen[0])
        if args.get("window"):
            shot = await self.partner.snapshot()
            if shot:
                images.append(shot["jpeg"])
        if not images:
            return {"ok": False, "error": "no fresh camera frame and no window picture to look at"}
        option = getattr(self.hub.settings, "look_hard", None)
        try:
            vision = self.brain.agent.vision(option)
            result = await vision.ask(images, "Answer the question about the picture(s) as JSON {\"answer\": \"...\"}: "
                                      "one or two plain sentences, saying what you are unsure of.\nQuestion: " + question,
                                      max_output_tokens=600)
        except Exception as exc:  # a missing key or a provider error is said, plainly
            return {"ok": False, "error": f"the vision model could not look ({type(exc).__name__})"}
        if result.error:
            return {"ok": False, "error": result.error[:300]}
        answer = (result.data or {}).get("answer") if isinstance(result.data, dict) else None
        return {"ok": True, "answer": str(answer or result.raw_text or "")[:1500], "model": result.model}

    async def do_in_hooke(self, args: dict) -> dict:
        return await self._bounded("do_in_hooke", self._do_in_hooke(args), args.get("_call_id"))

    async def _do_in_hooke(self, args: dict) -> dict:
        """A many-step request handed to the brain whole, in the user's words; what it really did comes back (its
        failures said as failures), with a fresh look when it changed the screen."""
        text = str(args.get("request") or "").strip()
        if not text:
            return {"ok": False, "error": "no request"}
        from ..ticket import issue

        brain, hub = self.brain, self.hub
        kwargs: dict[str, Any] = {"voice_owner": self.partner.session}
        if self.client is not None:
            request = issue(hub, "turn", asked=brain.conversation.here(), channel="voice")
            try:
                pin = hub.screen.pin(self.client, request_id=request.admission.request_id, timing="server-handoff-arrival")
                kwargs.update(client=self.client, request_snapshot=pin, request_ticket=request, asked=request.asked)
            except Exception:
                request.seal()
        before = self._reported_at()
        result = await brain.turn(text, "voice", **kwargs)
        actions = [{"tool": a.get("tool"), "ok": a.get("ok"), "summary": a.get("summary")}
                   for a in (result.get("actions") or [])][:8]
        answer = str(result.get("answer") or "")[:1800]
        failed = [f"{a['tool']}: {a['summary']}" for a in actions if a["ok"] is False]
        working = [a["tool"] for a in actions if a["ok"] is None]
        out: dict[str, Any] = {"ok": None if working and not failed else (not failed and bool(answer or actions)),
                               "answer": answer, "actions": actions}
        if failed:
            out["failed"] = failed
        if result.get("error"):
            out["ok"], out["error"] = False, str(result["error"])[:300]
        changed = [a["tool"] for a in actions if a["ok"] and a["tool"] in SCREEN_CHANGING]
        if changed:  # act, then look: whether it shows is read from the screen, not from the brain's own answer
            look, facts = await self.look_after(changed[-1], before)
            out["_look"], out["_log"] = look, facts
        return out

    # --- think: the frontier mind behind the voice (voice/think.py) ----------------------------------------------
    async def think(self, args: dict) -> dict:
        """Started at once (the voice says a short acknowledgement); the backend works in a task of its own and its
        result is said when it lands, once (RealtimeSession.deliver)."""
        from .think import ThinkTask

        request = str(args.get("request") or "").strip()
        if not request:
            return {"ok": False, "error": "no request to think about"}
        n = self.partner.turn()
        task = ThinkTask(request, str(args.get("why") or "").strip())
        started = time.time()
        job = self.thinking[task.id] = self.partner.spawn(self._think(task, n, args.get("_call_id")))
        session = getattr(self.partner, "session", None)
        if session is not None and hasattr(session, "tell_progress"):  # what is under way, said while it works
            self.partner.spawn(session.tell_progress(task.id, lambda: not job.done(),
                                                     facts=lambda: self.task_facts(task.id, "thinking it through")))
        return {"started": True, "task": task.id, "_log": {"task": task.id, "startedT": started},
                "note": "Started. Say only a two- or three-word acknowledgement now ('On it.'), nothing about the "
                        "result: it is told to you to say when it lands."}

    def _think_option(self) -> str:
        return str(getattr(self.hub.settings, "think", "claude-opus-5-5") or "claude-opus-5-5")

    async def _think(self, task, n: int, call_id: str | None) -> None:
        from ..prompts import THINK
        from . import think as thinking

        session = self.partner.session
        option = self._think_option()
        key = object()
        self.running[key] = (f"thinking about it with {option}", time.monotonic(), None)  # from its start
        try:
            task.words = await self.partner.words(n, WORDS_WAIT_S)
        except BaseException:
            self.running.pop(key, None)
            raise
        task.pictures = self.partner.pictures(n)
        task.brief = getattr(session, "brief_text", "") or ""
        task.history = self.partner.history()
        task.notes = list(getattr(session, "notes", []) or [])
        step = {"now": ""}

        self._task_started[task.id] = time.monotonic()

        def progress(tool: str) -> None:
            step["now"] = tool
            self._task_now[task.id] = f"thinking it through, now running {tool}"
            self.running[key] = (f"thinking with {option}, now running {tool}", self.running[key][1], None)

        async def call_tool(name: str, args: dict) -> dict:
            args = dict(args or {})
            # The action's id: the task and its step (the backend's own id for the call), so a retry of the same step
            # returns its receipt and is never a second effect.
            step = str(args.pop("_call_id", "") or "")
            if not step:
                self._steps[task.id] = self._steps.get(task.id, 0) + 1
                step = f"s{self._steps[task.id]}"
            if name == "activity":
                return knowing.read(self.hub, args, project=self._project())
            if name not in THINK_TOOLS:
                return {"ok": False, "error": f"there is no {name} tool here"}
            action = f"{task.id}:{step}"
            result = await self.run(name, args, n=n, patient=True, action_id=action)
            pending = result.pop("_pending", None)
            if pending is None:
                return result
            return await self.settle(name, args, result, pending, action)

        log = getattr(self.partner, "log", lambda record: None)
        missing = "ANTHROPIC_API_KEY" if not option.startswith("gpt-") else "OPENAI_API_KEY"
        try:
            if not os.environ.get(missing):
                result = thinking.ThinkResult("", False, model=option, error=(
                    f"thinking with {option} needs its key ({missing}); set it under Keys, or choose the other model "
                    "for thinking"))
            else:
                backend = self.backend(option) if self.backend is not None else thinking.thinker(option)
                result = await thinking.think(task, backend, self.think_tools(), call_tool, progress, THINK)
        finally:
            self.running.pop(key, None)
            self._steps.pop(task.id, None)
        if result.refused:
            other = "GPT-6.1 Sol" if not option.startswith("gpt-") else "Claude Opus 5.5"
            result.say = result.say or ""
            result.error = (f"{option} refused this request on safety grounds ({result.refused}); nothing was done. "
                            f"Offer to try it with {other} for thinking, if the user wants.")
        log({"kind": "partner-think", "task": task.id, "callId": call_id, "n": n, "model": result.model,
             "ok": result.ok, "checked": result.checked, "refused": result.refused, "error": result.error,
             "rounds": result.rounds, "tools": result.tools, "pointAt": result.points, "ms": result.usage.get("ms"),
             "usage": {k: v for k, v in result.usage.items() if k != "ms"}, "doneT": time.time()})
        await self.said_when_landed(task.id, "think", result.facts(), result.say, result.points)
