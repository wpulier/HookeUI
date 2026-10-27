"""Additive TEST precursor for real Registry occurrence publication.

Only the installed test tool's external wait/return is synthetic. Registry.start,
prequeue binding, task settlement, Steps carrying and ToolRun.wire are real.
This proves receipt identity, never camera/source/renderer/completion authority.
No answering provider, fake observer or private wire callback is substituted.
"""
import asyncio
import uuid

from orchestrator.brain.admission import RequestAdmission
from orchestrator.brain.registry import Registry, Steps, Tool, ToolContext, ToolRun, bind_occurrence


def test_registry_wire_retains_original_occurrences_when_same_tool_finishes_out_of_order_and_is_carried():
    async def scenario():
        release = {name: asyncio.Event() for name in ('first', 'second')}
        entered = []

        async def external_wait(_ctx, args):
            name = args['which']
            entered.append(name)
            await release[name].wait()
            return 'Synthetic transport returned ' + name

        registry = Registry()
        registry.add(Tool('synthetic_transport', 'Controlled external transport; no observed effect.',
                          {'type': 'object', 'properties': {'which': {'type': 'string'}}}, external_wait))
        admission = RequestAdmission('original-wire-request')
        ctx = ToolContext('chat', 27, None, admission=admission, request_id=admission.request_id)
        original_steps = ctx.steps
        original_steps.tools = {1: 'synthetic_transport', 2: 'synthetic_transport'}
        ctx.step = 1
        first = registry.start('synthetic_transport', {'which': 'first'}, ctx)
        ctx.step = 2
        second = registry.start('synthetic_transport', {'which': 'second'}, ctx)
        first_occurrence, second_occurrence = first.occurrence, second.occurrence
        try:
            assert first_occurrence is not None and second_occurrence is not None
            assert first_occurrence.request_id == second_occurrence.request_id == 'original-wire-request'
            assert uuid.UUID(first_occurrence.action_id).version == 4
            assert uuid.UUID(second_occurrence.action_id).version == 4
            assert first_occurrence.action_id != second_occurrence.action_id
            assert not entered, 'SETUP: actual start binds before the external handler can enter'
            assert first.ok is None and second.ok is None
            pending_first, pending_second = first.wire(), second.wire()
            assert pending_first.get('requestId') == pending_second.get('requestId') == 'original-wire-request'
            assert pending_first.get('actionId') == first_occurrence.action_id, (
                'MISSING_FEATURE: a pending public receipt must project its existing occurrence identity')
            assert pending_second.get('actionId') == second_occurrence.action_id

            # A later model round carries this exact earlier occurrence under a
            # new step ordinal. It neither restarts transport nor mints an ID.
            carried = Steps.carrying([('synthetic_transport', {'which': 'second'})],
                                     {1: (2, original_steps)}, original_steps)
            carried_ctx = ToolContext('chat', 27, None, admission=admission,
                                      request_id=admission.request_id, step=1, steps=carried)
            assert carried.runs[1] is second and carried.occurrences[1] is second_occurrence
            bind_occurrence(second, carried_ctx)
            assert second.occurrence is second_occurrence and second.step == (2, original_steps)

            release['second'].set()
            await second.task
            assert second.ok is True and first.ok is None
            assert second.summary == 'Synthetic transport returned second'
            late_second = carried.runs[1].wire()
            assert late_second.get('requestId') == 'original-wire-request'
            assert late_second.get('actionId') == pending_second['actionId']
            assert first.wire().get('actionId') == pending_first['actionId']
            release['first'].set()
            await first.task
            assert first.ok is True and first.summary == 'Synthetic transport returned first'
            assert first.wire().get('actionId') == pending_first['actionId']
            assert entered == ['first', 'second'] and admission.used == 0
        finally:
            for event in release.values():
                event.set()
            await asyncio.gather(first.task, second.task, return_exceptions=True)

    asyncio.run(scenario())


def test_legacy_unbound_tool_run_wire_does_not_mint_an_occurrence():
    legacy = ToolRun('legacy_transport', {}, ok=True, summary='Original legacy receipt')
    assert legacy.occurrence is None
    assert legacy.wire() == {'tool': 'legacy_transport', 'ok': True,
                             'summary': 'Original legacy receipt', 'ms': 0}
    assert legacy.wire(full=True) == {**legacy.wire(), 'args': {}}
    assert legacy.occurrence is None
