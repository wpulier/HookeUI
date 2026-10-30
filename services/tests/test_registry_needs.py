"""A refusal that carries the step that meets it (ToolFailed.needs): Hooke takes that step, then runs the call once
more, so the user is asked only for what is outside Hooke (Will, 30 Oct: "a magnified view of the original specimen"
answered "Outline the target structure first"; a refusal is right only for "that model isn't set up, want me to
download it?" or "out of credits")."""
from __future__ import annotations

import asyncio

from orchestrator.brain import registry as api
from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.admission import RequestAdmission


def context():
    return api.ToolContext("voice", 7, None, question="a magnified view of the original specimen",
                           admission=RequestAdmission("synthetic-request"))


def run_one(registry, name, args):
    async def late(*_):
        pass

    async def go():
        runs = await ActionRunner(registry, None, lambda: None, late).run([(name, args)], context())
        await asyncio.wait([r.task for r in runs])
        return runs[0]
    return asyncio.run(go())


def test_a_needed_step_is_taken_and_the_call_runs_again():
    found = []

    async def find(ctx, args):
        found.append(args)
        return "outlined the organism"

    async def zoom(ctx, args):
        if not found:
            raise api.ToolFailed("nothing is outlined yet", next=None, needs=("find", {"what": "the organism"}))
        return "the close-up is open"
    registry = api.Registry()
    registry.add(api.Tool("find", "find", {}, find, inline_ms=1))
    registry.add(api.Tool("zoom", "zoom", {}, zoom, inline_ms=1))
    run = run_one(registry, "zoom", {})
    assert run.ok is True and found == [{"what": "the organism"}]
    assert run.summary == "the close-up is open (first, outlined the organism)"


def test_a_needed_step_that_fails_is_what_is_said():
    async def find(ctx, args):
        raise api.ToolFailed("nothing like that is in view", next="say what it looks like")

    async def zoom(ctx, args):
        raise api.ToolFailed("nothing is outlined yet", next=None, needs=("find", {"what": "x"}))
    registry = api.Registry()
    registry.add(api.Tool("find", "find", {}, find, inline_ms=1))
    registry.add(api.Tool("zoom", "zoom", {}, zoom, inline_ms=1))
    run = run_one(registry, "zoom", {})
    assert run.ok is False and "nothing like that is in view" in run.summary and run.next == "say what it looks like"


def test_a_refusal_outside_hooke_passes_through_and_a_need_is_met_once():
    calls = []

    async def outline(ctx, args):
        raise api.ToolFailed("SAM 3.1 is not installed: a 3.5 GB download", next="say yes to download it")

    async def stubborn(ctx, args):
        calls.append(1)
        raise api.ToolFailed("still nothing outlined", next=None, needs=("noop", {}))

    async def noop(ctx, args):
        return "nothing to do"
    registry = api.Registry()
    for name, handler in (("outline", outline), ("stubborn", stubborn), ("noop", noop)):
        registry.add(api.Tool(name, name, {}, handler, inline_ms=1))
    run = run_one(registry, "outline", {})
    assert run.ok is False and run.next == "say yes to download it", "an external need is the user's, with its offer"
    run = run_one(registry, "stubborn", {})
    assert run.ok is False and len(calls) == 2, "the call runs once more after its need is met, never a loop"


def test_steps_of_a_tool_that_runs_alongside_its_own_kind_run_at_once_and_others_in_order():
    """Will, 30 Oct: "two images you find online" were found one after the other, 51 s then 40 s. Two such steps in a
    row, neither waiting on the other, run at once; ordinary steps still run in order."""
    async def late(*_):
        pass

    async def go():
        in_flight, most, order = [0], [0], []

        async def photo(ctx, args):
            in_flight[0] += 1
            most[0] = max(most[0], in_flight[0])
            await asyncio.sleep(0.05)
            in_flight[0] -= 1
            order.append(f"photo {args['n']}")
            return "kept"

        async def tab(ctx, args):
            order.append("tab")
            return "made"
        registry = api.Registry()
        registry.add(api.Tool("reference_photo", "photo", {}, photo, inline_ms=1, alongside=True))
        registry.add(api.Tool("tabs", "tabs", {}, tab, inline_ms=1))
        runs = await ActionRunner(registry, None, lambda: None, late).run(
            [("tabs", {}), ("reference_photo", {"n": 1}), ("reference_photo", {"n": 2}), ("tabs", {})], context())
        await runs[0].sequence
        assert most[0] == 2, "the two searches were in flight together"
        assert order[0] == "tab" and order[-1] == "tab" and sorted(order[1:3]) == ["photo 1", "photo 2"]
    asyncio.run(go())
