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
