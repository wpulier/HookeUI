"""Synthetic unit regressions through the shared Registry and ActionRunner.

Transport/tools are local stand-ins. This does not certify a rendered workspace.
These checks protect occurrence identity and typed dependencies across queued and
carried work, independently of the existing string-valued @n compatibility path.
"""
import asyncio
import copy

import pytest

from orchestrator.brain import registry as api
from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.admission import RequestAdmission


def required(name):
    value = getattr(api, name, None)
    assert callable(value), f"MISSING_FEATURE: shared Registry.{name} is required"
    return value


def context():
    return api.ToolContext("chat", 7, None, admission=RequestAdmission("synthetic-request"))


def runner(registry):
    async def late(*_):
        pass
    return ActionRunner(registry, None, lambda: None, late)


def test_intentional_equal_steps_have_distinct_occurrences_before_queued_work_runs():
    async def go():
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def tool(ctx, args):
            calls.append(api.CURRENT_RUN.get())
            if len(calls) == 1:
                entered.set()
                await release.wait()
            return "local unit effect"
        registry = api.Registry()
        registry.add(api.Tool("same", "unit tool", {}, tool, inline_ms=1))
        ctx = context()
        runs = await runner(registry).run([("same", {}), ("same", {})], ctx)
        try:
            assert entered.is_set() and len(calls) == 1
            occurrences = [getattr(run, "occurrence", None) for run in runs]
            assert all(occurrences), "MISSING_FEATURE: allocate occurrence before queueing"
            assert {o.request_id for o in occurrences} == {"synthetic-request"}
            assert occurrences[0].action_id != occurrences[1].action_id
            assert ctx.steps.occurrences[1] is occurrences[0]
            assert ctx.steps.occurrences[2] is occurrences[1]
            release.set()
            await runs[0].sequence
            assert [run.occurrence for run in runs] == occurrences
        finally:
            release.set()
            await runs[0].sequence
    asyncio.run(go())


def test_carried_exact_occurrence_shares_late_typed_outputs_without_tool_name_collapse():
    async def go():
        release = asyncio.Event()
        async def tool(ctx, args):
            await release.wait()
            required("record_output")(ctx, {"kind": "presentation", "instanceId": args["id"]})
            api.record_made(ctx, args["id"])
            return "local unit effect"
        registry = api.Registry()
        registry.add(api.Tool("same", "unit tool", {}, tool, inline_ms=1))
        ctx = context()
        runs = await runner(registry).run([("same", {"id": "window-a"}), ("same", {"id": "window-b"})], ctx)
        try:
            assert hasattr(ctx.steps, "outputs"), "MISSING_FEATURE: typed step outputs"
            carried = api.Steps.carrying([("same", {}), ("same", {})],
                {1: (2, ctx.steps), 2: (1, ctx.steps)}, ctx.steps)
            assert carried.outputs[1] is ctx.steps.outputs[2]
            assert carried.occurrences[1] is runs[1].occurrence
            assert carried.outputs[2] is ctx.steps.outputs[1]
            assert api.Steps.carrying([("same", {})], {"same"}, ctx.steps).tools == {}
            release.set()
            await runs[0].sequence
            later = context()
            later.steps, later.step = carried, 3
            selected = required("typed_step_output")(later, {"step": 1, "output": 0}, kind="presentation")
            assert selected["instanceId"] == "window-b"
            assert api.step_output(later, "@1") == "window-b"
        finally:
            release.set()
            await runs[0].sequence
    asyncio.run(go())


def test_output_wire_and_dependency_reads_are_detached_from_input_and_each_other():
    async def go():
        given = {"kind": "presentation", "instanceId": "window-a", "source": {"revision": 0}}
        async def tool(ctx, args):
            required("record_output")(ctx, given)
            given["source"]["revision"] = 9
            return "local unit effect"
        registry = api.Registry()
        registry.add(api.Tool("open", "unit tool", {}, tool))
        ctx = context()
        runs = await runner(registry).run([("open", {})], ctx)
        assert runs[0].ok is True, runs[0].summary
        first = runs[0].wire()
        assert first["outputs"][0]["source"]["revision"] == 0
        first["outputs"][0]["source"]["revision"] = 10
        full = runs[0].wire(full=True)
        assert full["outputs"][0]["source"]["revision"] == 0
        later = context()
        later.steps, later.step = ctx.steps, 2
        output = required("typed_step_output")(later, {"step": 1, "output": 0}, kind="presentation")
        output["source"]["revision"] = 11
        assert required("typed_step_output")(later, {"step": 1, "output": 0}, kind="presentation")["source"]["revision"] == 0
        # Legacy string outputs are a separate API, not the typed occurrence store.
        ctx.steps.produced.clear()
        assert runs[0].wire()["outputs"] == full["outputs"]
    asyncio.run(go())


@pytest.mark.parametrize("ref,kind", [({"step": 2, "output": 0}, "presentation"),
    ({"step": 1, "output": 9}, "presentation"), ({"step": 1, "output": 0}, "outline")])
def test_typed_dependency_refuses_future_missing_or_wrong_kind(ref, kind):
    async def go():
        async def tool(ctx, args):
            required("record_output")(ctx, {"kind": "presentation", "instanceId": "window-a"})
            return "local unit effect"
        registry = api.Registry()
        registry.add(api.Tool("open", "unit tool", {}, tool))
        ctx = context()
        runs = await runner(registry).run([("open", {})], ctx)
        assert runs[0].ok is True, runs[0].summary
        ctx.step = 2
        before = copy.deepcopy(runs[0].wire())
        with pytest.raises(api.ToolFailed):
            required("typed_step_output")(ctx, ref, kind=kind)
        assert runs[0].wire() == before
    asyncio.run(go())


def test_failed_producer_cannot_supply_a_successful_typed_dependency():
    async def go():
        async def tool(ctx, args):
            required("record_output")(ctx, {"kind": "presentation", "instanceId": "window-a"})
            raise api.ToolFailed("synthetic postcondition failed")
        registry = api.Registry()
        registry.add(api.Tool("open", "unit tool", {}, tool))
        ctx = context()
        runs = await runner(registry).run([("open", {})], ctx)
        assert runs[0].ok is False
        ctx.step = 2
        with pytest.raises(api.ToolFailed):
            required("typed_step_output")(ctx, {"step": 1, "output": 0}, kind="presentation")
    asyncio.run(go())


@pytest.mark.parametrize("payload", [{"kind": "made-up", "id": "x"},
    {"kind": "presentation", "instanceId": "window-a", "pixels": b"PRIVATE PIXELS"},
    {"kind": "presentation", "instanceId": "window-a", "value": float("nan")}])
def test_typed_outputs_refuse_unbounded_or_nonprimitive_records(payload):
    async def go():
        async def tool(ctx, args):
            with pytest.raises(api.ToolFailed):
                required("record_output")(ctx, payload)
            return "local unit validation"
        registry = api.Registry()
        registry.add(api.Tool("open", "unit tool", {}, tool))
        ctx = context()
        runs = await runner(registry).run([("open", {})], ctx)
        assert runs[0].ok is True, runs[0].summary
        assert runs[0].wire().get("outputs", []) == []
    asyncio.run(go())
