"""Structural success dependencies over the real parser, Registry and Runner.

Only tool I/O and provider replies are synthetic. No prose decides whether a
step succeeds, and no handler or completion path is replaced.
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from orchestrator.brain.actions import ActionRunner
from orchestrator.brain.dispatch import answer_phase
from orchestrator.brain.llm import FunctionCallingBrain, _normalise
from orchestrator.brain.registry import Registry, Tool, ToolContext, ToolFailed, parse_actions, schema
from orchestrator.ticket import issue, using
from tests.fakes import Hub
from tests.test_brain import make
from tests.waiting import HANG_S


def rig(handlers):
    hub, registry, late = Hub(), Registry(), []
    for name, handler in handlers.items():
        registry.add(Tool(name, name, schema({}), handler, inline_ms=5))

    async def reported(run, ctx):
        late.append((run.wire(), ctx))

    runner = ActionRunner(registry, hub, lambda: None, reported)
    ctx = ToolContext('chat', 1, None, 'Apply the declared plan.')
    ticket = issue(hub, 'turn')
    ctx.admission = ticket.admission
    return runner, ctx, ticket, late


async def settled(runs):
    await asyncio.wait_for(asyncio.shield(runs[0].sequence), HANG_S)
    await asyncio.sleep(0.03)  # original queued reporters poll every 0.02 s


def test_failed_dependency_skips_only_its_dependents_and_keeps_original_receipts():
    async def scenario():
        entered = []

        async def failed(ctx, args):
            entered.append('failed')
            raise ToolFailed('External operation refused its original input.')

        async def dependent(ctx, args):
            entered.append('dependent')
            return 'Dependent effect applied.'

        async def independent(ctx, args):
            entered.append('independent')
            return 'Independent effect applied.'

        runner, ctx, ticket, _ = rig(dict(failed=failed, dependent=dependent, independent=independent))
        try:
            with using(ticket):
                runs = await runner.run(parse_actions([
                    {'tool': 'failed', 'args': {}},
                    {'tool': 'dependent', 'args': {}, 'after_steps': [1]},
                    {'tool': 'independent', 'args': {}},
                ]), ctx)
                await settled(runs)
            assert entered == ['failed', 'independent']
            assert [r.ok for r in runs] == [False, False, True]
            assert 'depend' in runs[1].summary.lower() and '1' in runs[1].summary
            assert all(r.occurrence.request_id == ticket.admission.request_id for r in runs)
            assert len({r.occurrence.action_id for r in runs}) == 3
            assert runs[1].outputs == [] and runs[1].outcome is None
            assert ticket.admission.used == 0
        finally:
            ticket.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('dependency, permitted', [(1, False), (2, True)])
def test_actual_openai_sdk_keeps_malformed_function_call_slots(monkeypatch, dependency, permitted):
    """The external SDK response, not pre-normalised tuples, supplies the original slots."""
    import openai

    async def scenario():
        entered, sends = [], []

        async def effect(ctx, args):
            entered.append(args['label'])
            return 'Actual external effect completed.'

        runner, ctx, ticket, _ = rig(dict(effect=effect))

        async def create(**kwargs):
            sends.append((kwargs, ticket.admission.used))
            return SimpleNamespace(model='scripted-sdk-model', output_text='The declared plan.', output=[
                SimpleNamespace(type='function_call', name='effect', arguments='{"label":'),
                SimpleNamespace(type='function_call', name='effect',
                                arguments=json.dumps({'label': 'actual-second'})),
                SimpleNamespace(type='function_call', name='effect',
                                arguments=json.dumps({'label': 'dependent-third', 'after_steps': [dependency]})),
            ])

        monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-sdk-only')
        monkeypatch.setattr(openai, 'AsyncOpenAI', lambda **_kwargs:
                            SimpleNamespace(responses=SimpleNamespace(create=create)))
        try:
            with using(ticket):
                with answer_phase('look'):
                    data, _meta = await FunctionCallingBrain().ask(
                        'openai:scripted-sdk-model@low', b'synthetic-original-jpeg',
                        'Apply the original function-call plan.', runner.registry)
                runs = await runner.run(parse_actions(data['actions']), ctx)
                await settled(runs)
            assert len(sends) == 1 and sends[0][1] == ticket.admission.used == 1
            assert sends[0][0]['input'][0]['content'][0]['image_url'].startswith('data:image/jpeg;base64,')
            assert entered == ['actual-second'] + (['dependent-third'] if permitted else [])
            assert [run.step[0] for run in runs] == [2, 3]
            assert [run.ok for run in runs] == [True, permitted]
            if not permitted:
                assert runs[1].error is None and 'depend' in runs[1].summary.lower()
                assert not runs[1].outputs and runs[1].outcome is None
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_pending_dependency_is_reported_late_and_success_runs_the_effect_once():
    async def scenario():
        release, started = asyncio.Event(), asyncio.Event()
        entered = []

        async def held(ctx, args):
            started.set()
            await release.wait()
            entered.append('held')
            return 'Original external work completed.'

        async def effect(ctx, args):
            entered.append('effect')
            return 'Effect applied.'

        runner, ctx, ticket, late = rig(dict(held=held, effect=effect))
        try:
            with using(ticket):
                runs = await runner.run(parse_actions([
                    {'tool': 'held', 'args': {}},
                    {'tool': 'effect', 'args': {}, 'after_steps': [1]},
                ]), ctx)
                await started.wait()
                original = [r.occurrence for r in runs]
                assert entered == [] and all(r.ok is None for r in runs)
                assert not runs[0].sequence.done()
                release.set()
                await settled(runs)
            assert entered == ['held', 'effect'] and [r.ok for r in runs] == [True, True]
            assert [r.occurrence for r in runs] == original
            assert {(w['requestId'], w['actionId']) for w, owner in late} == {
                (o.request_id, o.action_id) for o in original}
            assert all(owner is ctx for _, owner in late)
        finally:
            release.set()
            ticket.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('dependency', [[2], [3], [0], [True], ['1'], [1, 1]])
def test_invalid_dependency_never_enters_the_dependent_handler(dependency):
    async def scenario():
        entered = []

        async def effect(ctx, args):
            entered.append(args['label'])
            return 'Applied.'

        runner, ctx, ticket, _ = rig(dict(effect=effect))
        try:
            with using(ticket):
                runs = await runner.run(parse_actions([
                    {'tool': 'effect', 'args': {'label': 'first'}},
                    {'tool': 'effect', 'args': {'label': 'invalid'}, 'after_steps': dependency},
                ]), ctx)
                await settled(runs)
            assert 'invalid' not in entered
            assert any(r.ok is False and 'depend' in r.summary.lower() for r in runs)
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_a_filtered_malformed_original_step_is_not_replaced_by_a_compacted_success():
    async def scenario():
        entered = []

        async def effect(ctx, args):
            entered.append(args['label'])
            return 'Applied.'

        runner, ctx, ticket, _ = rig(dict(effect=effect))
        try:
            with using(ticket):
                runs = await runner.run(parse_actions([
                    None,
                    {'tool': 'effect', 'args': {'label': 'actual-second'}},
                    {'tool': 'effect', 'args': {'label': 'must-not-run'}, 'after_steps': [1]},
                ]), ctx)
                await settled(runs)
            assert 'must-not-run' not in entered
            assert any(r.ok is False and 'depend' in r.summary.lower() for r in runs)
            successful = next(r for r in runs if r.ok is True)
            assert successful.step[0] == 2
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_declared_setup_dependency_is_not_moved_before_its_prerequisite():
    async def scenario():
        entered = []

        async def effect(ctx, args):
            entered.append(args['label'])
            return 'Applied.'

        runner, ctx, ticket, _ = rig(dict(effect=effect, use=effect))
        try:
            with using(ticket):
                runs = await runner.run(parse_actions([
                    {'tool': 'effect', 'args': {'label': 'original-first'}},
                    {'tool': 'use', 'args': {'label': 'dependent-choice'}, 'after_steps': [1]},
                ]), ctx)
                await settled(runs)
            assert entered == ['original-first', 'dependent-choice']
            assert all(r.ok is True for r in runs)
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_a_plan_without_dependencies_keeps_legacy_independent_failure_continuation():
    async def scenario():
        entered = []

        async def effect(ctx, args):
            entered.append(args['label'])
            if args['label'] == 'failed':
                raise ToolFailed('Original refusal.')
            return 'Applied.'

        runner, ctx, ticket, _ = rig(dict(effect=effect))
        try:
            with using(ticket):
                runs = await runner.run(parse_actions([
                    {'tool': 'effect', 'args': {'label': 'failed'}},
                    {'tool': 'effect', 'args': {'label': 'independent'}},
                ]), ctx)
                await settled(runs)
            assert entered == ['failed', 'independent'] and [r.ok for r in runs] == [False, True]
        finally:
            ticket.close()
    asyncio.run(scenario())


def test_brain_keeps_dependency_when_partitioning_clearing_before_look_targets():
    entered = []

    async def effect(ctx, args):
        entered.append(args['label'])
        return 'Applied.'

    brain, _, _ = make([{'answer': 'Applying the plan.',
        'targets': [{'label': 'synthetic target', 'box_2d': [10, 10, 20, 20]}],
        'actions': [{'tool': 'effect', 'args': {'label': 'first'}},
                    {'tool': 'close_result', 'args': {'label': 'dependent-close'}, 'after_steps': [1]}]}],
        [Tool('effect', '', schema({}), effect), Tool('close_result', '', schema({}), effect)])
    result = asyncio.run(brain.turn('Apply the declared steps.'))
    assert entered == ['first', 'dependent-close']
    assert [row['ok'] for row in result['actions']] == [True, True]
    assert result['calls'] <= 3


def test_actual_fc_dependency_metadata_is_retained_but_text_actions_never_execute():
    async def scenario():
        entered = []

        async def effect(ctx, args):
            assert 'after_steps' not in args  # transport metadata is not a tool argument
            entered.append(args['label'])
            if args['label'] == 'failed':
                raise ToolFailed('Actual function-call refusal.')
            return 'Applied.'

        runner, ctx, ticket, _ = rig(dict(effect=effect))
        data = _normalise('{"answer":"Plan","actions":[{"tool":"effect","args":{"label":"text-smuggled"}}]}',
                          [('effect', {'label': 'failed'}),
                           ('effect', {'label': 'dependent', 'after_steps': [1]})])
        try:
            with using(ticket):
                runs = await runner.run(parse_actions(data['actions']), ctx)
                await settled(runs)
            assert entered == ['failed'] and [r.ok for r in runs] == [False, False]
            assert runs[1].error is None and 'depend' in runs[1].summary.lower()
        finally:
            ticket.close()
    asyncio.run(scenario())
