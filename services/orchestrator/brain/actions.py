"""Doing what a request asked for, in the order it asked: each step starts only when the one
before it has finished, so it sees the board that step left ("close the zoom, then highlight the
cortex"; "clear those and outline the xylem"). The answer waits for the steps up to the longest
step's inline_ms, as it always has; a step still running then, or still waiting its turn, is
reported on the channel that asked when it finishes (`on_late`). A thing the running look saw
(s2) becomes an outline when its step's turn comes (`outline_seen`): the outline already on
screen if one covers it, else a new one from its box, one per thing per request."""
from __future__ import annotations

import asyncio
import copy
import re
from typing import Awaitable, Callable

from .. import tools
from ..scene import status_for
from .registry import Registry, Steps, ToolContext, ToolRun, action_number, bind_occurrence, dependent
from .admission import MAX_CALLS

SEEN_ID = re.compile(r"s\d+")
CLEARING = ("clear", "close_magnifier", "close_result")  # steps that take things off the screen
SETUP = ("use",)  # steps that choose how the request's own work is done (contract 7): before its outlines too


def first_choices(actions) -> list[int]:
    """The steps that choose how the request's work is done (SETUP) and move first: the first `use` of each operation
    (or setting); a later one of the same keeps its place, so the steps between run with the earlier one."""
    seen: set[str] = set()
    out = []
    for k, action in enumerate(actions):
        name, args = action[0], action[1]
        if name not in SETUP or dependent(action):
            continue
        role = str(((args if isinstance(args, dict) else {}).get("operation") or "")).strip().lower()
        if role in seen:
            continue
        seen.add(role)
        out.append(k)
    return out


async def outline_seen(hub, view_now: Callable[[], object], ref: str, ctx: ToolContext) -> str:
    """A thing from the running look (s2) as an outline id. The s-ids mean the things of the
    look the request was decided on (`ctx.seen_view`); a newer look numbers its things afresh.
    Raises tools.ToolError saying why when it cannot (the reason reaches the user)."""
    from ..agent import _box_in, _same_thing
    from ..ticket import reusable

    # The outline an earlier step made of it stands in, unless a step since chose another model to outline with
    # ("outline the stoma with SAM 2.1, then with micro-sam": the second step draws it again; journey 6, 2026-10-11).
    if ref in ctx.aliases and ctx.aliases[ref] in hub.scene.objects \
            and reusable("outline", hub.scene.objects[ctx.aliases[ref]].made_by):
        return ctx.aliases[ref]
    view = ctx.seen_view  # missing original Sight is not a later look's freshly numbered targets
    info = hub.latest_info
    if view is None or info is None:
        raise tools.ToolError("I have not had a look at this view yet", next="ask me what is in view first, or click the thing")
    thing = next((t for t in view.things if t.id == ref), None)
    if thing is None:
        raise tools.ToolError(f"{ref} is not something I saw in my last look", next=None)  # no next: the model's own call; the app looks again
    box = view.box_now(thing, info)
    if box is None:
        raise tools.ToolError(f"the slide moved since I saw the {thing.name}", next=None)  # no next: the app looks again on its own
    # Only an outline drawn and verified ("tracking") stands in: a carried or lost one is not on the screen, and
    # the request must not be told it is highlighted (ADR 0006).
    existing = next((o for o in hub.scene.objects.values() if o.kind == "highlight" and o.trial is None
                     and o.anchor_epoch == info.epoch and o.track == "tracking"
                     and _same_thing(box, _box_in(o, info))
                     and reusable("outline", o.made_by)), None)  # under a choice: made again with it
    if existing is not None:
        oid = existing.id
    else:
        x0, y0, x1, y1 = box
        try:
            obj = await tools.outline_at(hub, info.id, (x0 + x1) / 2, (y0 + y1) / 2, box=box, source="ai:sight",
                                         label=thing.name, status=status_for(thing.confidence),
                                         confidence=thing.confidence)
        except tools.ToolError as exc:
            raise tools.ToolError(f"could not outline the {thing.name}: {exc}", next=getattr(exc, "next", None)) from exc  # next: passed on
        oid = obj.id
    ctx.aliases[ref] = oid
    if oid not in ctx.objects:
        ctx.objects.append(oid)
    return oid


class ActionRunner:
    def __init__(self, registry: Registry, hub, sight: Callable[[], object | None],
                 on_late: Callable[[ToolRun, ToolContext], Awaitable[None]], board=None,
                 log: Callable[[dict], None] | None = None) -> None:
        self.registry = registry
        self.hub = hub
        self.sight = sight  # the running look (its current view), or None
        self.on_late = on_late
        self.board = board  # board.Board: what the request's tools resolve "that" against
        self.log = log  # the interaction log (override lines)

    def _view(self):
        sight = self.sight()
        return sight.view if sight is not None else None

    async def _current_report(self, ctx: ToolContext) -> None:
        """The requesting studio's report is current before its steps run: one older than REPORT_FRESH_S (a studio
        that stopped reporting for a while, or whose reports were not taken) is asked for again, never failed as
        stale by each tool in turn. The one way in every actor's request takes (screen.fresh_report)."""
        from ..screen import fresh_report

        client = getattr(ctx, "client", None)
        if client is not None:
            await fresh_report(self.hub, client)

    async def run(self, actions: list[tuple[str, dict]], ctx: ToolContext, numbers: list[int] | None = None) -> list[ToolRun]:
        """Run one answer's steps in order. `numbers`: the number the model gave each step, when the
        answer's steps are run in more than one call (the brain runs a look's clearing steps before
        its outlines and the rest after them); without it these steps are a new answer, numbered 1..."""
        if ctx.board is None:
            ctx.board = self.board
        if actions:
            await self._current_report(ctx)
        # In the order the model gave them (its judgment; a clear it lists after a highlight
        # runs after it).
        runs = [ToolRun(name, dict(args) if isinstance(args, dict) else {}) for name, args in actions]
        for run, action in zip(runs, actions):
            run.after_steps = copy.deepcopy(getattr(action, "after_steps", None))
        if not runs:
            # An empty numbered pass is still part of this original declaration,
            # not permission for a later answer to take over its occurrence slots.
            if numbers is not None and ctx._requested_goal is not None:
                if ctx.steps.round == 0:
                    ctx._plan_round += 1
                    ctx.steps.round = ctx._plan_round
                ctx._requested_goal.anchor(ctx.steps)
            return runs
        if numbers is None:
            if ctx.steps.runs:
                # Keep actual outputs/occurrences, never aliases under the new
                # round's numbers. At most the original answer-round bound.
                ctx.prior_steps = (*ctx.prior_steps, ctx.steps)[-MAX_CALLS:]
            ctx.steps, numbers = Steps(), [action_number(action, i) for i, action in enumerate(actions, 1)]
        if ctx.steps.round == 0:
            ctx._plan_round += 1
            ctx.steps.round = ctx._plan_round
        if ctx._requested_goal is not None:
            ctx._requested_goal.anchor(ctx.steps)
        # The plan as the model gave it, by number, before any step runs: a clear keeps what a later step names.
        ctx.steps.planned.update((n, (action[0], action[1])) for n, action in zip(numbers, actions))
        # A choice of model (`use`) covers the request's own work, so it goes first on every path (the
        # brain's look moves it before its outlines too); each step keeps the number the model gave it. Only the
        # first choice for an operation moves: a later one stays where it was asked, for the steps after it ("outline
        # it with SAM 2.1, then with micro-sam"; Slice 5).
        setup = first_choices(actions)
        if setup and setup != list(range(len(setup))):
            order = setup + [k for k in range(len(runs)) if k not in setup]
            from .intent import log_override

            log_override(self.log, "choice-first",
                         asked=[r.tool for r in runs], ran=[runs[k].tool for k in order])
            runs, numbers, actions = [runs[k] for k in order], [numbers[k] for k in order], [actions[k] for k in order]
        for n, run in zip(numbers, runs):
            ctx.steps.tools[n] = run.tool
            run.step = (n, ctx.steps)  # planned identity survives queued work and another pass
            bind_occurrence(run, ctx)
        made: dict[str, str | None] = {}

        def alongside(k: int) -> bool:
            """Whether step k may start with the next one still to come: both the same tool that runs alongside its
            own kind, and the next one waiting on no step (Tool.alongside)."""
            if k + 1 >= len(runs) or runs[k + 1].tool != runs[k].tool or dependent(actions[k + 1]):
                return False
            tool = self.registry.tools.get(runs[k].tool)
            return bool(getattr(tool, "alongside", False))

        async def in_order() -> None:
            together: list = []  # steps started alongside one another, waited for as one
            for k, (n, run, action) in enumerate(zip(numbers, runs, actions)):
                dependencies = getattr(action, "after_steps", None)
                reason = None
                if dependent(action):
                    from ..ticket import current
                    request = current()
                    if (not isinstance(dependencies, list)
                            or any(type(number) is not int or number < 1 or number >= n for number in dependencies)
                            or len(set(dependencies)) != len(dependencies)):
                        reason = f"step {n} skipped: invalid dependency numbers"
                    else:
                        for number in dependencies:
                            prerequisite = run.step[1].runs.get(number)
                            if (prerequisite is None or prerequisite.occurrence is None
                                    or prerequisite.occurrence.request_id != run.occurrence.request_id):
                                reason = f"step {n} skipped: dependency {number} is missing from its original plan"
                                break
                            if (prerequisite.task is None and prerequisite.sequence is not None
                                    and prerequisite.sequence is not asyncio.current_task()):
                                await asyncio.wait([prerequisite.sequence])
                            if prerequisite.task is not None and not prerequisite.task.done():
                                await asyncio.wait([prerequisite.task])
                            if prerequisite.ok is not True:
                                reason = f"step {n} skipped: dependency {number} did not succeed"
                                break
                    if request is not None and (request.closed or request.expired() or request.superseded("session")):
                        reason = f"step {n} skipped: its dependency request was canceled or retired"
                if reason is not None:
                    self.registry.skip(run, ctx, reason)
                    await asyncio.wait([run.task])
                    continue
                args = await self._seen_as_outlines(run.tool, run.args, ctx, made, run)
                ctx.step = n  # what this step produces is filed under n ("@n" to the steps after it)
                self.registry.start(run.tool, args, ctx, run=run)
                if holder is not None:
                    holder.started(run.task)  # a stop of the whole request (stop_work) cancels it
                if run.task is not None:
                    together.append(run.task)
                if together and not alongside(k):
                    await asyncio.wait(together)
                    together = []
            if together:
                await asyncio.wait(together)

        from .. import user_stop

        holder = user_stop.current()  # the request's stop holder (a voice hand-off's), when it has one
        steps = asyncio.ensure_future(in_order())
        if holder is not None:
            holder.started(steps)
        for run in runs:
            run.sequence = steps
        from ..ticket import current

        if current() is not None:
            current().track(steps)  # the request stays open (its views held) until its last step is done
        wait = max(self.registry.inline_ms(r.tool) for r in runs) / 1000
        await asyncio.wait([steps], timeout=wait)
        for run in runs:
            if run.ok is None:
                run.summary = run.summary or ("still working" if run.task is not None else "waiting its turn")
                run.late = True
                asyncio.ensure_future(self._late_when_done(run, ctx, steps))
        return runs

    async def _late_when_done(self, run: ToolRun, ctx: ToolContext, steps: asyncio.Future) -> None:
        """A step the answer could not wait for: reported once it has run."""
        while run.task is None and not steps.done():
            await asyncio.sleep(0.02)  # waiting for the steps before it
        if run.task is None:
            return
        await asyncio.wait([run.task])
        await self.on_late(run, ctx)

    async def _seen_as_outlines(self, name: str, args: dict, ctx: ToolContext,
                                made: dict[str, str | None], run: ToolRun) -> dict:
        """Adapt only the installed capability's declared outline-reference slots.

        Literal text and opaque identities keep their supplied meaning. A declared
        Sight target is outlined at its step, using this request's original look.
        """
        args = dict(args)
        for key in self.registry.outline_reference_fields(name):
            value = args.get(key)
            if isinstance(value, str) and SEEN_ID.fullmatch(value.strip()):
                ref = value.strip()
                if ref not in made or made[ref] not in self.hub.scene.objects:
                    try:
                        made[ref] = await outline_seen(self.hub, self._view, ref, ctx)
                    except tools.ToolError as exc:
                        made[ref] = None
                        run._argument_error = exc  # real Registry failure; never reinterpret a missing target as a label
                        break
                if made[ref]:
                    args[key] = made[ref]
        return args
