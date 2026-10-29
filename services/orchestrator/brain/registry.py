"""One list of tools for the brain, whichever way you talk to it.

A tool is defined once and offered to the model in the form the brain mode needs: as a
section of the prompt (json-actions mode: the model returns `actions`), or as function
declarations for Gemini / OpenAI Responses (fc mode). The same handler runs either way, from
chat or from voice.

Effect tools change the view (zoom, dissect...) and never need a second model call; query
tools return information the model needs to finish its answer, so the loop calls the model
again with their results.
"""
from __future__ import annotations

import asyncio
import copy
import inspect
import json
import time
import uuid
from collections.abc import Mapping, Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal
from .. import execution
from .admission import AdmissionDenied


class ToolFailed(RuntimeError):
    """A tool could not do what was asked; the message is shown to the user, with `next`, the plain
    next step the user can take, when there is one (Amendment 4, item 2: never a dead end)."""

    def __init__(self, message: str = "", next: str | None = None) -> None:
        super().__init__(message)
        self.next = next

    def said(self) -> str:
        """What the user is told: what went wrong, then what to do."""
        message = str(self)
        return f"{message}; {self.next}" if self.next else message


@dataclass(frozen=True)
class CameraSelection:
    """Original requested id, distinct from snapshot's best-frame fallback."""
    requested_frame: int | None
    capture: Any


@dataclass
class ToolContext:
    channel: str  # "chat" | "voice" | "system"
    turn_id: int
    frame_id: int | None
    question: str = ""  # what the user said this turn (tools that need an explicit request check it)
    delegation_id: str | None = None
    objects: list[str] = field(default_factory=list)  # objects outlined by this turn's answer
    # The look's targets by the number the model gave them ("target 2"), None for one not outlined, so a
    # failed target never shifts the ones after it (M2.1 re-check SF2); empty when the look pointed at none.
    targets: list = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)  # replaced outline id -> its new outline
    seen_view: Any = None  # the running look the request was decided on (its s-ids), if any
    asked: Any = None  # day/model.Asked: the conversation the request was made in (late results go there)
    board: Any = None  # board.Board: what "that" means on screen (set where the request's tools run)
    # Composition within one answer (contract 9): the step being started now, by the number the model
    # gave it, and the answer's steps, so a later step can name an earlier one's output as "@1".
    step: int = 0
    steps: "Steps" = field(default_factory=lambda: Steps())
    # What the user was pointing at when they made the request (board.WhenAsked: typed, as the turn started; by
    # voice, when the words' final transcript came): "that" is what was pointed at then, not whatever was selected
    # or clicked while the request was being answered (board.py; the partner's Amendment 1, C2). None: now.
    when_asked: Any = None
    # What this request's own steps pointed at (select, magnify, dissect), newest last: "that" for the steps after.
    pointed: str | None = None
    # What this request's own zooms opened the magnifier on: it shows one thing, so a second zoom on another
    # in the same request shows the two side by side instead of replacing the first (going live G4).
    magnified: list[str] = field(default_factory=list)
    inspection_slots: list[None] = field(default_factory=list)  # shared across fast-pass/vision contexts, at most two
    admission: Any = None  # same request-owned answer allowance as its ticket, including late work
    client: Any = None  # accepted originating client; a raw report never supplies authority
    workspace_id: str | None = None
    request_snapshot: Any = None  # immutable Screen pin at Send/final transcript
    prepared_source_check: Any = field(default=None, repr=False, compare=False)  # server-only retained-source recheck before admission
    request_id: str | None = None  # standalone/manual request, retained once rather than inferred later
    scientific_input: Any = None  # exact held native Ask capture, never a live fallback
    research_selection: dict | None = None  # validated exact artifact/anchor captured by the research composer
    camera_selection: CameraSelection | None = None  # original held server view; never re-snapshot during acquisition
    voice_owner: Any = field(default=None, repr=False, compare=False)  # original local session; None never borrows current
    sight_selection: Any = None  # original Sight notes/weak capture, held by this request's real Ticket
    # Who starts the tools run in this context (ticket 66, S2; Data's "Made by"): "you" for the studio's own Run
    # (commands.run_tool, the studio's manual controls), "assistant" for the brain's chat and voice (Registry.start
    # says so from the channel), None when nobody it can name asked (a system step).
    actor: str | None = None
    _goal_json: str | None = field(default=None, init=False, repr=False)
    _requested_goal: "RequestedGoal | None" = field(default=None, init=False, repr=False)
    _plan_round: int = field(default=0, init=False, repr=False)
    # Runner-owned original output ancestry, never a constructor or wire grant.
    # Numeric @step continues to resolve only the current steps.
    prior_steps: tuple["Steps", ...] = field(default_factory=tuple, init=False, repr=False, compare=False)

    @property
    def goal(self) -> dict | None:
        """The first admitted desired declaration, detached; never completion proof."""
        return json.loads(self._goal_json) if self._goal_json is not None else None

    def retain_goal(self, declared, actions, *, raw_actions=Ellipsis) -> None:
        if self._goal_json is None:
            from .goals import parse

            goal = parse(declared, actions, raw_actions=raw_actions)
            if goal is not None:
                self._goal_json = json.dumps(goal, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                self._requested_goal = RequestedGoal(self._goal_json, actions)

    def carry_goal_from(self, earlier: "ToolContext") -> None:
        """Carry desired intent only between contexts of the same original request."""
        if (self._goal_json is None and self.admission is not None and self.admission is earlier.admission
                and self.request_id == earlier.request_id == self.admission.request_id
                and self.workspace_id == earlier.workspace_id and self.client is earlier.client
                and self.request_snapshot is earlier.request_snapshot):
            self._goal_json = earlier._goal_json
            self._requested_goal = earlier._requested_goal
            self._plan_round = max(self._plan_round, earlier._plan_round)


@dataclass(frozen=True)
class ActionOccurrence:
    """An intentional operation, allocated before queueing and reused by carried work."""
    request_id: str
    action_id: str


@dataclass(frozen=True)
class ToolOutput(Mapping):
    """A bounded immutable primitive output; reads return detached projections."""
    _json: str

    @classmethod
    def capture(cls, value: dict) -> "ToolOutput":
        kinds = {"presentation": "instanceId", "outline": "id", "resource": "resource",
                 "source": "source", "population": "id", "member": "id"}
        if not isinstance(value, dict) or value.get("kind") not in kinds:
            raise ToolFailed("the operation returned an unknown output kind", next=None)  # no next: the producer violated its typed output contract
        identifier = value.get(kinds[value["kind"]])
        if (value["kind"] in {"presentation", "outline", "population", "member"}
                and (not isinstance(identifier, str) or not identifier or len(identifier) > 256)):
            raise ToolFailed("the operation returned no bounded output identity", next=None)  # no next: the producer supplied no usable identity
        if value["kind"] in {"resource", "source"} and not isinstance(identifier, dict):
            raise ToolFailed("the operation returned no qualified output reference", next=None)  # no next: the producer omitted the original reference
        try:
            encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, RecursionError):
            raise ToolFailed("the operation returned nonprimitive output facts", next=None) from None  # no next: invalid producer data cannot be retained
        if len(encoded.encode("utf-8")) > 32768:
            raise ToolFailed("the operation's output facts exceed their retained bound", next=None)  # no next: the producer must bound its own output
        return cls(encoded)

    def wire(self) -> dict:
        return json.loads(self._json)

    def __getitem__(self, key):
        return self.wire()[key]

    def __iter__(self) -> Iterator:
        return iter(self.wire())

    def __len__(self):
        return len(self.wire())


@dataclass
class Steps:
    """One model answer's steps: each step's tool by the number the model gave it (1-based, in the
    order it listed them), and what each produced (evidence ids). "@n" names step n of the same answer,
    never a step of an earlier round of the query loop."""
    tools: dict[int, str] = field(default_factory=dict)
    produced: dict[int, list[str]] = field(default_factory=dict)
    occurrences: dict[int, ActionOccurrence] = field(default_factory=dict)
    outputs: dict[int, list[ToolOutput]] = field(default_factory=dict)
    runs: dict[int, "ToolRun"] = field(default_factory=dict, repr=False)
    round: int = 0  # original planning pass; carried runs keep their own Steps
    # The answer's whole plan, (tool, args) by the step's number, known before its first step runs (also when its
    # steps are run in more than one call): what a step can see its later steps name (clear keeps what they name).
    planned: dict[int, tuple[str, dict]] = field(default_factory=dict, repr=False)

    def named_later(self, number: int, ids) -> list[str]:
        """Of `ids` (outline ids on the board), those a step after `number` names in its arguments, exactly."""
        wanted = set(ids)
        found: list[str] = []

        def walk(value) -> None:
            if isinstance(value, str):
                if value.strip() in wanted and value.strip() not in found:
                    found.append(value.strip())
            elif isinstance(value, dict):
                for item in value.values():
                    walk(item)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    walk(item)

        for later in sorted(k for k in self.planned if k > number):
            walk(self.planned[later][1])
        return found

    @classmethod
    def carrying(cls, actions: list[tuple[str, dict]], done: set | dict, earlier: "Steps | None") -> "Steps":
        """A new answer's steps, with the steps another pass already ran for the same words (the fast
        pass's, `done` mapping each new step to its exact original occurrence, or legacy tool names) under
        this answer's numbers, so "@1" still names what that occurrence made. One earlier step's list is shared, so what it makes after this is still found."""
        steps = cls()
        if earlier is None:
            return steps
        for compact, action in enumerate(actions, start=1):
            name, _args = action
            i = action_number(action, compact)
            if i not in done and name not in done:
                continue
            if isinstance(done, dict):
                # Exact occurrence, including its original output list if the tool is still queued/running.
                at = done[i]
                number, original = at if isinstance(at, tuple) else (at, earlier)
                if original.tools.get(number) == name:
                    steps.tools[i] = name
                    steps.produced[i] = original.produced.setdefault(number, [])
                    steps._carry_outputs(i, number, original)
                continue
            theirs = [k for k, t in earlier.tools.items() if t == name]
            if len(theirs) != 1:
                continue  # a legacy name does not identify which occurrence to carry
            steps.tools[i] = name
            steps.produced[i] = earlier.produced.setdefault(theirs[0], [])
            steps._carry_outputs(i, theirs[0], earlier)
        return steps

    def _carry_outputs(self, number: int, original_number: int, original: "Steps") -> None:
        self.outputs[number] = original.outputs.setdefault(original_number, [])
        if original_number in original.occurrences:
            self.occurrences[number] = original.occurrences[original_number]
        if original_number in original.runs:
            self.runs[number] = original.runs[original_number]


# The step a running tool belongs to, set when it starts (Registry.start): read after a wait, ctx.step
# may already be a later step's, or another round's.
STEP: ContextVar[tuple[int, Steps] | None] = ContextVar("brain_step", default=None)


def current_step(ctx) -> tuple[int, Steps]:
    at = STEP.get()
    if at is not None:
        return at
    return getattr(ctx, "step", 0), getattr(ctx, "steps", None) or Steps()


def record_made(ctx, output_id: str) -> None:
    """What the running step produced, for "@n" in the steps after it."""
    n, steps = current_step(ctx)
    steps.produced.setdefault(n, []).append(output_id)


def record_output(ctx, output: dict) -> None:
    """Retain an actual typed output on its original run; binary data stays private."""
    run = CURRENT_RUN.get()
    if run is None:
        raise ToolFailed("no running operation owns this output", next=None)  # no next: an unowned output cannot acquire an original occurrence
    captured = ToolOutput.capture(output)
    if len(run.outputs) >= 16:
        raise ToolFailed("the operation returned too many typed outputs", next=None)  # no next: the producer exceeded its original output allowance
    run.outputs.append(captured)


def typed_step_output(ctx, ref: dict, *, kind: str | None = None) -> dict:
    """Resolve exactly one earlier successful output, without a title/source fallback."""
    now, steps = current_step(ctx)
    if (not isinstance(ref, dict) or set(ref) != {"step", "output"}
            or type(ref.get("step")) is not int or type(ref.get("output")) is not int):
        raise ToolFailed("an output reference needs an earlier step and its output index",
                         next="use an earlier step number and an output index it actually returned")
    number, index = ref["step"], ref["output"]
    if number < 1 or number not in steps.tools or (now and number >= now):
        raise ToolFailed("that output belongs to a missing, current or later step",
                         next="use an output from a completed successful earlier step")
    run = steps.runs.get(number)
    if run is None or run.ok is not True:
        raise ToolFailed("that earlier operation has not successfully produced the requested output",
                         next="use an output from a completed successful earlier step")
    outputs = steps.outputs.get(number, [])
    if index < 0 or index >= len(outputs):
        raise ToolFailed("that earlier operation has no output at this index",
                         next="use an output index returned by that earlier step")
    result = outputs[index].wire()
    if kind is not None and result["kind"] != kind:
        raise ToolFailed(f"that output is {result['kind']}, not {kind}",
                         next="use an earlier output of the required kind")
    return result


def step_output(ctx, ref: str) -> str:
    """The one thing "@n" names: what step n of this answer produced, or ToolFailed with why not."""
    n_now, steps = current_step(ctx)
    try:
        n = int(ref.strip()[1:])
    except ValueError:
        raise ToolFailed(f"{ref} is not a step: \"@1\" names what the answer's first step made",
                         next=None) from None  # no next: the model's own call; it corrects it
    if n not in steps.tools:
        raise ToolFailed(f"{ref}: this answer has no step {n}", next=None)  # no next: the model's own call; it corrects it
    if n_now and n >= n_now:
        raise ToolFailed(f"{ref} is this step or a later one ({steps.tools[n]}); only an earlier step's result can be used",
                         next=None)  # no next: the model's own call; it corrects it
    made = list(dict.fromkeys(steps.produced.get(n) or []))
    if not made:
        raise ToolFailed(f"{ref}: step {n} ({steps.tools[n]}) made nothing that can be used here (it failed or found nothing)",
                         next=None)  # no next: the model's own call; it corrects it
    if len(made) > 1:
        raise ToolFailed(f"{ref}: step {n} made {', '.join(made)}; name one by its id",
                         next=None)  # no next: the model's own call; it corrects it
    return made[0]


Handler = Callable[[ToolContext, dict[str, Any]], Awaitable[str]]


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, Any]  # JSON schema of the arguments (type: object)
    handler: Handler
    kind: Literal["effect", "query"] = "effect"
    inline_ms: int = 1500  # how long the turn waits for it before answering without it
    family: str = ""  # where it sits in the menu (menu.FAMILIES)
    uses: tuple[str, ...] = ()  # the models, weights and services it runs on (licences.py)
    sets: tuple[str, ...] = ()  # settings keys it changes (Settings.to_wire names): what "the agent can change" (capabilities.py)
    outline_refs: tuple[str, ...] = ()  # server-declared scalar slots that can require a Sight target as an outline
    # The values of its `do` argument (present's verbs: screen.VERBS). A model that calls one of them as a tool of its own
    # ("resize") is refused naming this tool and the verb, exactly, so its recovery can use it (live 2026-10-11 16:36:22:
    # "there is no resize tool" was answered "resizing is impossible", though present do="resize" worked).
    verbs: tuple[str, ...] = ()
    retained_input: bool = False  # handler explicitly preserves saved-input ownership or reads independent facts

    @property
    def produces(self) -> str | None:
        """What it makes, as declared in one table (produces.py; Amendment 3): an entry kind of the
        session's index, "temporary: ...", "none: ...", or None when undeclared (a test fails)."""
        from ..produces import TOOLS

        return TOOLS.get(self.name)


@dataclass
class ToolRun:
    tool: str
    args: dict
    ok: bool | None = None  # None while still running
    summary: str = ""
    ms: float = 0.0
    task: asyncio.Task | None = None
    sequence: asyncio.Task | None = field(default=None, repr=False)  # the ordered plan, including steps not started yet
    answer: str | None = None  # the brain's later answer from completed queries; summary remains the tool's receipt
    reads: list[str] = field(default_factory=list)  # the option groups it read while it ran (options.recording_reads)
    next: str | None = None  # a refusal's plain next step (ToolFailed.next), also in its summary
    # What it is doing while it runs, when that is worth saying ("loading micro-sam (usually about 2 min)":
    # loads.py), so the answer and the voice say it is being done and why it takes long; None once done.
    progress: str | None = None
    error: str | None = None  # an unexpected failure's real error: for the interaction log, never the user's words
    # What it made, as it ran (attach): structured values, the pictures it showed (magnifier keys) and the
    # executions it used, so a result entry is built from the run itself (resources.Results; Amendment 3).
    values: dict = field(default_factory=dict)
    pictures: list[str] = field(default_factory=list)
    picture_bytes: list[bytes] = field(default_factory=list, repr=False)  # bounded producing pixels, consumed by Results; never wire/context
    picture_sources: list[dict] = field(default_factory=list, repr=False)  # producing instances, not content hashes
    executions: list[str] = field(default_factory=list)
    # The models that worked on it, each {role, name, said}, named when it ended (activity.models_of; none of an
    # unvoted blind trial's): what Activity says of the step (the user's decision Q2, 2026-10-11).
    models: list[dict] = field(default_factory=list)
    step: tuple[int, Steps] | None = field(default=None, repr=False)  # bound when planned, before it waits its turn
    resource: dict | None = None  # exact retained artifact made or reopened by this step
    images: list[Any] = field(default_factory=list, repr=False)  # bounded query input; never raw bytes on wire
    occurrence: ActionOccurrence | None = None
    outputs: list[ToolOutput] = field(default_factory=list, repr=False)
    outcome: dict | None = None  # adapter's causal observed receipt, not inferred from ok/commit
    _requested_goal: "RequestedGoal | None" = field(default=None, repr=False)
    _facets: list[dict] = field(default_factory=list, repr=False)
    _argument_error: ToolFailed | None = field(default=None, repr=False)  # actual reference-adapter failure, before handler effects
    after_steps: list | None = field(default=None, repr=False)  # original explicit sequencing metadata, never handler args
    _on_progress: Callable | None = field(default=None, repr=False, compare=False)  # bound by Registry.start
    late: bool = False  # still running when its answer was written: what it does next is news (actions.ActionRunner)
    # The user pressed Stop on it (user_stop.py; the Actions panel's Stop): it did not complete and is no failure. Nothing
    # after the Stop is applied (its finished listeners are not told, so no result entry is made of it).
    stopped: bool = False
    # Who started it (ToolContext.actor, fixed in Registry.start): kept with what it makes as `made_by.actor`
    # (resources.Results, operations.Execution), never on its wire.
    actor: str | None = field(default=None, repr=False)

    def report_progress(self, words: str | None) -> None:
        """What it is waiting on, as that changes: a step of the request's lifecycle (a model loading, the studio out of
        view), told to whoever follows the request (Registry.progressed: its chat row, and said once by voice)."""
        if words == self.progress or self.ok is not None:
            return
        self.progress = words
        if self._on_progress is not None:
            try:
                self._on_progress(self)
            except Exception:  # what is told of a run never breaks it
                pass

    def wire(self, full: bool = False) -> dict:
        out = {"tool": self.tool, "ok": self.ok, "summary": self.summary, "ms": round(self.ms)}
        if self.occurrence is not None:
            out["requestId"] = self.occurrence.request_id
            out["actionId"] = self.occurrence.action_id
        if self.outputs:
            out["outputs"] = [output.wire() for output in self.outputs]
        if self.outcome is not None:
            out["outcome"] = copy.deepcopy(self.outcome)
        if self._requested_goal is not None:
            out["goal"] = self._requested_goal.project()
            if self._facets:
                out["facets"] = [copy.deepcopy(facet["row"]) for facet in self._facets]
        if self.resource:
            out["resource"] = self.resource
        if self.images:
            out["inspected"] = [facts for _, facts in self.images]
        if self.answer:
            out["answer"] = self.answer
        if self.next:
            out["next"] = self.next
        if self.models:
            out["models"] = copy.deepcopy(self.models)
        if self.progress and self.ok is None:
            out["progress"] = self.progress
        if self.stopped:
            out["stopped"] = True
        if full:  # the interaction log keeps what the tool was asked to do, and which settings it used
            out["args"] = self.args
            if self.reads:
                out["reads"] = self.reads
            if self.error:
                out["error"] = self.error
        return out


# What a run's wire says for Activity only (the models that worked on it): never told to a model (claims.answer_facts).
ACTIVITY_KEYS = ("models",)
STOPPED_SUMMARY = "stopped: you pressed Stop"  # a run the user stopped (ToolRun.stopped): its receipt, never a failure's
STOPPING_SUMMARY = "stopping: you pressed Stop, and its work hasn't ended yet"  # stopped work still ending (user_stop.Stop.late)


# The run of the tool running now (set in Registry.start), so what a tool makes deep inside it (a
# measurement, a picture, an execution) is attached to its run without passing it along.
CURRENT_RUN: ContextVar["ToolRun | None"] = ContextVar("brain_tool_run", default=None)

ACTORS = ("you", "assistant")
BRAIN_CHANNELS = ("chat", "voice")  # the brain's own requests: what it runs, the Assistant started


def actor_of(ctx) -> str | None:
    """Who starts a run in this context, where the run begins (ticket 66, S2): the context's own word (the studio's
    Run says "you"), else the Assistant for the brain's chat and voice, else None (nobody it can name)."""
    actor = getattr(ctx, "actor", None)
    if actor in ACTORS:
        return actor
    return "assistant" if getattr(ctx, "channel", None) in BRAIN_CHANNELS else None


def run_actor() -> str | None:
    """Who started the tool running now (CURRENT_RUN), for what it keeps beside its run (a snapshot, a bookmark)."""
    return getattr(CURRENT_RUN.get(), "actor", None)


class RequestedGoal:
    """Original desired intent bound once to real planned occurrences, never a later plan.

    This retains execution bindings only. The existing completion comparator owns
    causal proof and current-state validation; handler success supplies neither.
    """
    def __init__(self, declaration: str, actions) -> None:
        self.id = str(uuid.uuid4())
        self._declaration = declaration
        self._actions = copy.deepcopy(list(actions))
        self.steps: Steps | None = None
        self.ticket = None
        self.report: Callable[[], dict | None] | None = None

    def anchor(self, steps: Steps) -> None:
        if self.steps is None:
            self.steps = steps

    def bind(self, run: ToolRun, ticket) -> None:
        number, steps = run.step
        if (steps is self.steps and 1 <= number <= len(self._actions)
                and self._actions[number - 1] == (run.tool, run.args)):
            run._requested_goal = self
            if self.ticket is None:
                self.ticket = ticket

    def expected(self, run: ToolRun, targets: list[str], *, publishes: bool) -> tuple[list[dict], bool]:
        """Goal selectors use published original outputs, separately from argument binding.

        A current output is allowed here after publication. A future occurrence
        cannot lend its output or command receipt to this occurrence's facet.
        """
        expected, unresolved = [], False
        number, steps = run.step
        for index, condition in enumerate(json.loads(self._declaration)["conditions"]):
            if condition["action"] != number:
                continue
            ids, missing = [], False
            for selector in condition["targets"]:
                if isinstance(selector, str):
                    ids.append(selector)
                    continue
                producer = steps.runs.get(selector["step"])
                outputs = steps.outputs.get(selector["step"], ())
                if (selector["step"] > number or producer is None
                        or producer._requested_goal is not self
                        or producer.step is None or producer.step[0] != selector["step"]
                        or producer.step[1] is not steps
                        or steps.occurrences.get(selector["step"]) is not producer.occurrence
                        or (producer is not run and producer.ok is not True)
                        or selector["output"] >= len(outputs)):
                    missing = True
                    continue
                output = outputs[selector["output"]].wire()
                if output.get("kind") != "presentation":
                    missing = True
                else:
                    ids.append(output["instanceId"])
            if missing:
                # Output-producing commands own unresolved desired output conditions;
                # a separate parent close cannot pretend to prove those children.
                unresolved = unresolved or (publishes and condition["essential"])
                continue
            if not set(ids).issubset(targets):
                continue
            expected.append({"id": f"{self.id}:{index}",
                             **{k: v for k, v in condition.items() if k != "action"}, "targets": ids})
        return expected, unresolved

    def project(self) -> dict:
        from ..completion import _goal_current, _goal_row

        declaration = json.loads(self._declaration)
        report = self.report() if self.report is not None else None
        confirmed = report is not None and self.steps is not None
        if self.ticket is not None and self.ticket.superseded("session"):
            confirmed = False
        for index, condition in enumerate(declaration["conditions"]):
            if not condition["essential"]:
                continue
            run = self.steps.runs.get(condition["action"]) if self.steps is not None else None
            predicate_id = f"{self.id}:{index}"
            if (run is None or run._requested_goal is not self
                    or not any(predicate_id in {e["id"] for e in facet["row"]["goal"]["expected"]}
                               and _goal_current(_goal_row(facet["row"]), report)
                               for facet in run._facets)):
                confirmed = False
        return {"id": self.id, "declaration": declaration, "status": "confirmed" if confirmed else "unmet"}


def bind_occurrence(run: ToolRun, ctx: ToolContext) -> None:
    """Bind once to the real admission and original step before any awaited work."""
    from ..ticket import current
    ticket = current()
    if ctx.admission is None and ticket is not None:
        ctx.admission = ticket.admission
    if run.occurrence is None:
        request_id = getattr(ctx.admission, "request_id", None) or ctx.request_id
        if request_id is None:
            # Standalone local tools still get an explicit identity; this creates
            # no accepted browser/capture authority or new answer allowance.
            request_id = ctx.request_id = "standalone/" + str(uuid.uuid4())
        run.occurrence = ActionOccurrence(request_id, str(uuid.uuid4()))
    if run.step is None:
        run.step = (ctx.step, ctx.steps)
    number, steps = run.step
    if number > 0:
        existing = steps.runs.get(number)
        if existing is not None and existing is not run:
            raise ToolFailed("an earlier operation already owns this step", next=None)  # no next: an existing original occurrence cannot be reassigned
        steps.occurrences[number] = run.occurrence
        steps.outputs[number] = run.outputs
        steps.runs[number] = run
    if ctx._requested_goal is not None:
        ctx._requested_goal.bind(run, ticket)


PICTURE_BYTES_MAX = 10 * 1024 * 1024  # total private producing bytes per run, before result retention
PICTURE_ATTACHMENTS_MAX = 4


def attach(values: dict | None = None, picture: str | None = None, execution: str | None = None,
           *, picture_bytes: bytes | None = None) -> None:
    """Attach producing facts/pixels; private pixels must be immutable and bounded."""
    run = CURRENT_RUN.get()
    if run is None:
        return
    if picture_bytes is not None:
        if (not isinstance(picture_bytes, bytes) or not picture_bytes
                or len(run.picture_bytes) >= PICTURE_ATTACHMENTS_MAX
                or sum(map(len, run.picture_bytes)) + len(picture_bytes) > PICTURE_BYTES_MAX):
            raise ToolFailed("the produced pictures exceed the retained image allowance", next=None)  # no next: invalid or excessive producer bytes cannot enter the original retained allowance
        run.picture_bytes.append(picture_bytes)
    if values:
        run.values.update(values)
    if picture and picture not in run.pictures:
        run.pictures.append(picture)
    if execution and execution not in run.executions:
        run.executions.append(execution)


class Registry:
    def __init__(self, menu: Callable[[], str] | None = None) -> None:
        self.tools: dict[str, Tool] = {}
        # The declarations a function-calling turn sends, by provider, with the tools they were made from: made again
        # only when a tool, its arguments or its description changed (toolbox.refresh), not on every turn.
        self._declared: dict[str, tuple[tuple, list]] = {}
        # Where an unexpected failure's real error is written when it happens (the interaction log,
        # toolbox.build): a step that ends after the answer was given is logged too.
        self.log: Callable[[dict], None] | None = None
        self.menu = menu or (lambda: "flat")  # how the menu is shown to the model (options.MENU)
        # Retention is part of execution; observers receive the final receipt only after it settles.
        self.retaining: list[Callable[["ToolRun", ToolContext], Awaitable[None] | None]] = []
        self.finished: list[Callable[["ToolRun", ToolContext], Awaitable[None] | None]] = []
        # A running step's progress changed (ToolRun.report_progress): told to whoever follows its request.
        self.progressed: list[Callable[["ToolRun", ToolContext], None]] = []
        # The models that worked on a run, named when it ends (activity.models_of, set by toolbox.build).
        self.models: Callable[["ToolRun"], list[dict]] | None = None

    def add(self, tool: Tool) -> Tool:
        properties = tool.params.get("properties", {})
        if (not isinstance(tool.outline_refs, tuple)
                or any(not isinstance(key, str) for key in tool.outline_refs)
                or len(set(tool.outline_refs)) != len(tool.outline_refs)
                or any(key not in properties or properties[key].get("type") != "string"
                       for key in tool.outline_refs)):
            raise ValueError(f"{tool.name}: outline references must name distinct scalar string properties")
        self.tools[tool.name] = tool
        return tool

    def outline_reference_fields(self, name: str) -> tuple[str, ...]:
        tool = self.tools.get(name)
        return tool.outline_refs if tool is not None else ()

    def names(self) -> list[str]:
        return list(self.tools)

    # --- what the model is told ---------------------------------------------------------
    def prompt_section(self, menu: str | None = None) -> str:
        """Tools as a prompt section for json-actions mode, shown the way the MENU option says
        (brain/menu.py): every tool is always there; only how it is grouped and how much of
        each is spelled out changes."""
        from .menu import render

        return render(list(self.tools.values()), menu or self.menu())

    def _declarations(self, provider: str, make: Callable[["Tool"], Any]) -> list:
        from ..looplag import off_loop

        made_from = tuple((name, id(t), id(t.params), t.description) for name, t in self.tools.items())
        kept = self._declared.get(provider)
        if kept is None or kept[0] != made_from or not off_loop():
            kept = self._declared[provider] = (made_from, [make(t) for t in self.tools.values()])
        return list(kept[1])

    def gemini_declarations(self) -> list:
        from google.genai import types

        return self._declarations("gemini", lambda t: types.FunctionDeclaration(
            name=t.name, description=t.description, parameters_json_schema=call_schema(t.params)))

    def responses_tools(self) -> list[dict]:
        return self._declarations("openai", lambda t: {"type": "function", "name": t.name, "description": t.description,
                                                       "parameters": call_schema(t.params), "strict": False})

    def anthropic_tools(self) -> list[dict]:
        """The same tools as Anthropic's Messages API takes them (name, description, input_schema)."""
        return self._declarations("anthropic", lambda t: {"name": t.name, "description": t.description,
                                                          "input_schema": call_schema(t.params)})

    # --- running ------------------------------------------------------------------------
    def _log_failure(self, name: str, run: "ToolRun", ctx: "ToolContext") -> bool:
        """An unexpected failure's real error, written to the log when it happens. Whether it was written: only
        then is the user told it is in the log."""
        if self.log is None:
            return False
        try:
            self.log({"kind": "tool-error", "tool": name, "turn": ctx.turn_id, "channel": ctx.channel, **run.wire(full=True)})
            return True
        except Exception:  # the log itself failing must not hide the answer
            return False

    def start(self, name: str, args: dict, ctx: ToolContext, run: ToolRun | None = None) -> ToolRun:
        """Start one tool; `run`: the record to fill (a planned step that was waiting its turn)."""
        from ..ticket import current

        if ctx.admission is None and current() is not None:
            ctx.admission = current().admission
        if run is None:
            run = ToolRun(name, args if isinstance(args, dict) else {})
        else:
            run.args = args if isinstance(args, dict) else {}
        bind_occurrence(run, ctx)
        if run.actor is None:
            run.actor = actor_of(ctx)
        tool = self.tools.get(name)
        if tool is None:
            # A verb of another tool, by its exact name, is refused naming that tool (Tool.verbs); never a near match.
            owner = next((t for t in self.tools.values() if name in t.verbs), None)
            refused = ToolFailed(f"there is no {name} tool", next=f"use {owner.name} with do={name!r}" if owner else None)
            run.ok, run.summary, run.next = False, refused.said(), refused.next
            with execution.span("tool:" + name, provider="app", adapter="brain.registry.Registry"):
                execution.finish("failed", errorType="UnknownTool")
            return run

        at = run.step or (ctx.step, ctx.steps)  # the step this run is, fixed now (a later step or round changes ctx.step)

        run.step = at

        def progressed(changed: "ToolRun") -> None:
            for listener in list(self.progressed):
                try:
                    listener(changed, ctx)
                except Exception:  # what is told of a run never breaks it
                    pass

        run._on_progress = progressed
        retries = getattr(self, "operation_retries", None)
        retry_candidate = retries.capture(run, ctx) if retries is not None else None

        async def execute() -> None:
            from ..options import recording_reads

            STEP.set(at)
            CURRENT_RUN.set(run)
            t0 = time.perf_counter()
            with recording_reads() as seen:  # which settings (so which models) this run really used
                try:
                    if run._argument_error is not None:
                        raise run._argument_error
                    if ctx.scientific_input is not None and not tool.retained_input:
                        raise ToolFailed('This action does not support the attached saved input.', next='use a declared capability, or remove the attachment to work on the Stage')
                    run.summary = await tool.handler(ctx, run.args)
                    run.ok = True
                except ToolFailed as exc:
                    run._argument_error = None  # retain the receipt, not adapter traceback/source frames
                    run.ok, run.summary, run.next = False, exc.said(), exc.next
                except asyncio.CancelledError:
                    from ..user_stop import STOP, requested

                    if requested():  # the user's Stop, never a waiter that went away: said as stopped, not failed
                        # Work it stopped that has not ended yet is "stopping", never claimed stopped (Runner._stopped).
                        said = STOPPING_SUMMARY if STOP.get().late else STOPPED_SUMMARY
                        run.ok, run.stopped, run.summary, run.next = False, True, said, None
                        run.ms = (time.perf_counter() - t0) * 1000
                    raise
                except AdmissionDenied as exc:
                    reason = ("this request reached its answer limit before the requested interpretation"
                              if exc.reason in {"budget-exhausted", "final-reserved"} else
                              "this request is no longer available for the requested interpretation")
                    failed = ToolFailed(reason, next="ask again in a new request")
                    run.ok, run.summary, run.next = False, failed.said(), failed.next
                    run.error = f"AdmissionDenied: {exc.reason}"
                except Exception as exc:  # a broken tool must not break the turn
                    # Plain words for the user (the partner §1c: never a dead end, no raw exception); the
                    # real error is written to the log here, when it happens, and kept on the run.
                    run.ok, run.error = False, f"{type(exc).__name__}: {exc}"[:300]
                    logged = self._log_failure(name, run, ctx)
                    failed = ToolFailed("something went wrong in the app",
                                        next="try asking another way" + ("; if it keeps happening, it is in the log"
                                                                         if logged else ""))
                    run.summary, run.next = failed.said(), failed.next
            run.reads = sorted(seen)
            run.ms = (time.perf_counter() - t0) * 1000
            if self.models is not None:
                try:
                    run.models = self.models(run)
                except Exception:  # saying what ran never breaks the run
                    run.models = []
            for listener in list(self.retaining):
                try:
                    notification = listener(run, ctx)
                    if inspect.isawaitable(notification):
                        await notification
                except Exception:  # what is told of a run never breaks it
                    pass
            run.ms = (time.perf_counter() - t0) * 1000
            if run.ok is True and run.picture_bytes and not run.resource:
                # A private-pixel producer has no displayed image to fall back
                # on. Only the original result writer supplies its receipt.
                failed = ToolFailed("this produced picture could not be retained",
                                    next="check local storage and try again")
                run.ok, run.summary, run.next = False, failed.said(), failed.next
            for listener in list(self.finished):
                try:
                    notification = listener(run, ctx)
                    if inspect.isawaitable(notification):
                        await notification
                except Exception:  # an observer cannot invalidate the settled execution receipt
                    pass

        async def go() -> None:
            try:
                with execution.span("tool:" + name, provider="app", adapter="brain.registry.Registry"):
                    await execute()
                    if not run.ok:  # what went wrong shows behind ⓘ (an unexpected error's type, never its text)
                        execution.finish("failed", **({"errorType": run.error.split(":", 1)[0]} if run.error else {}))
            finally:
                # The result writer/pending file cache owns retained bytes after
                # finished listeners; failed/cancelled runs keep no hidden buffer.
                run.picture_bytes.clear()
                if retries is not None:
                    retries.settled(retry_candidate)

        run.task = asyncio.create_task(go())
        return run

    def kind(self, name: str) -> str:
        tool = self.tools.get(name)
        return tool.kind if tool else "effect"

    def inline_ms(self, name: str) -> int:
        tool = self.tools.get(name)
        return tool.inline_ms if tool else 0

    def skip(self, run: ToolRun, ctx: ToolContext, why: str) -> ToolRun:
        """Settle an already bound operation that never entered its handler."""
        async def report():
            run.ok, run.summary = False, why
            for listener in [*self.retaining, *self.finished]:
                try:
                    notification = listener(run, ctx)
                    if inspect.isawaitable(notification):
                        await notification
                except Exception:
                    pass
        run.task = asyncio.create_task(report())
        return run


def schema(properties: dict[str, dict], required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or []}


class Action(tuple):
    """A legacy-compatible tool/args pair with its original plan envelope."""
    def __new__(cls, name, args, *, number=None, after_steps=None, dependency_declared=False):
        value = super().__new__(cls, (name, args))
        value.number = number
        value.after_steps = after_steps
        value.dependency_declared = dependency_declared
        return value

    def wire(self):
        value = {"tool": self[0], "args": self[1]}
        if self.dependency_declared:
            value["after_steps"] = copy.deepcopy(self.after_steps)
        return value

    def __getnewargs_ex__(self):
        # RequestedGoal copies the actual plan; tuple reconstruction must not
        # erase its envelope or reinterpret the pair as a single argument.
        return ((self[0], self[1]), {"number": self.number, "after_steps": self.after_steps,
                                   "dependency_declared": self.dependency_declared})


def action_number(action, fallback):
    return getattr(action, "number", None) or fallback


def action_wire(action):
    return action.wire() if isinstance(action, Action) else {"tool": action[0], "args": action[1]}


def actions_wire(actions):
    """Preserve original empty slots when a parser/intent filter removed an action."""
    out = []
    for i, action in enumerate(actions, 1):
        number = action_number(action, i)
        while len(out) < number - 1:
            out.append(None)
        out.append(action_wire(action))
    return out


def dependent(action):
    return getattr(action, "dependency_declared", False)


def call_schema(params):
    """FC-only structural metadata, removed before a tool receives its arguments."""
    result = copy.deepcopy(params)
    result.setdefault("properties", {})["after_steps"] = {
        "type": "array", "items": {"type": "integer", "minimum": 1}, "uniqueItems": True,
        "description": "Optional earlier action numbers in this same plan that must succeed before this call."
    }
    return result


def parse_actions(raw: Any) -> list[tuple[str, dict]]:
    """`actions` from a json-actions reply: [{"tool": name, "args": {...}}] (lenient)."""
    out = []
    items = raw if isinstance(raw, list) else []
    original_numbers = any(isinstance(item, dict) and "after_steps" in item for item in items)
    for number, item in enumerate(items, 1):
        if not isinstance(item, dict):
            continue
        name = item.get("tool") or item.get("name")
        args = item.get("args") or item.get("arguments") or {}
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if isinstance(name, str) and name:
            out.append(Action(name, args if isinstance(args, dict) else {},
                              number=number if original_numbers else None,
                              after_steps=copy.deepcopy(item["after_steps"]) if "after_steps" in item else None,
                              dependency_declared="after_steps" in item))
    return out
