"""A pending effect keeps the last answer slot until its original outcome settles."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import Tool, ToolFailed, record_made, schema
from orchestrator.brain.toolbox import Services
from orchestrator.ticket import current
from tests.test_brain import FakeVision, make


@pytest.mark.parametrize("found", [True, False])
def test_pending_fast_find_and_unplaced_camera_target_keep_final_for_completed_effect(found):
    async def go():
        release = asyncio.Event()
        calls, contexts, reports, prompts = [], [], [], []

        async def find(ctx, args):
            contexts.append(ctx)
            await release.wait()
            if not found:
                raise ToolFailed('no requested part found', next='try a clearer view')
            record_made(ctx, 'o-retained-stoma')
            return 'outlined the stoma as o-retained-stoma'

        brain, hub, agent = make([{'answer': 'Marking the stoma.',
            'targets': [{'label': 'stoma', 'box_2d': [1, 2, 3, 4]}], 'actions': []}],
            [Tool('find', 'Find the requested part.', schema({}), find, inline_ms=1)])
        hub.settings.fast = 'gemini-3.8-flash'
        brain.services = Services(hub=hub, agent=agent)
        agent.fast_model = FakeVision(fast={'intent': 'request', 'needs_look': True,
            'answer': 'Finding a stoma.', 'actions': [{'tool': 'find', 'args': {}}]})
        original_ask, original_point = agent.fast_model.ask, agent.point

        async def point(*args, **kwargs):
            made = await original_point(*args, **kwargs)
            for obj in made:
                obj.track = 'lost'
                obj.unplaced = 'the eyepiece scale changed since the captured view'
            return made

        async def ask(images, prompt, **kwargs):
            phase = current().admission.attempts[-1].phase
            calls.append(phase)
            if phase == 'fast':
                return await original_ask(images, prompt, **kwargs)
            prompts.append(prompt)
            # The old implementation spends the final slot on pending facts.
            # Let that original effect settle while its stale model answer is in flight.
            if 'still in progress' in prompt:
                release.set()
                await asyncio.sleep(0)
                return SimpleNamespace(data={'answer': 'The search is still in progress.'}, latency_ms=1)
            settled = ('The search finished and kept the stoma outline; the earlier camera outline could not be placed.'
                       if found else 'The search found no requested part, and the earlier camera outline could not be placed.')
            return SimpleNamespace(data={'answer': settled}, latency_ms=1)

        async def report(ctx, run):
            if any(row.get('kind') == 'query-answer' for row in agent.logged):
                reports.append((ctx, run.wire()))

        agent.point, agent.fast_model.ask, brain.report_late = point, ask, report
        result = await asyncio.wait_for(brain.turn('highlight a stoma'), 1)
        for obj in hub.scene.objects.values():
            obj.label, obj.track, obj.unplaced = 'renamed later', 'tracking', ''
        release.set()
        await asyncio.wait_for(asyncio.gather(*getattr(brain, '_query_answers', ())), 1)
        published = [result['answer'], *(r.get('answer', '') for _, r in reports)]
        assert 'The search is still in progress.' not in published, 'MISSING_FEATURE: final slot interpreted pending effects and left stale progress after completion'
        assert reports[-1][1].get('answer', '').startswith('The search finished' if found else 'The search found no requested part'), 'MISSING_FEATURE: completed original effect needs the reserved final answer'
        assert len(contexts) == 1 and contexts[0].admission.used == 3
        assert calls == ['fast', 'late']  # the camera look is the second original call
        assert ('outlined the stoma as o-retained-stoma' if found else 'no requested part found') in prompts[-1]
        assert reports[-1][1]['ok'] is found
        assert 'eyepiece scale changed' in prompts[-1], 'MISSING_FEATURE: late success must not erase earlier direct-outline placement failure'
        assert reports[-1][0] is contexts[0] or reports[-1][0].admission is contexts[0].admission

    asyncio.run(go())


def test_late_planning_and_final_keep_original_no_object_pointing_failure():
    async def go():
        release = asyncio.Event()
        prompts, phases, reports, effects = [], [], [], []

        async def find(ctx, args):
            await release.wait()
            return 'search completed with retained result'

        async def follow(ctx, args):
            effects.append('follow')
            return 'follow-up completed'

        brain, _, agent = make([{'answer': 'Finding a stoma.',
            'targets': [{'label': 'stoma', 'box_2d': [1, 2, 3, 4]}],
            'actions': [{'tool': 'find', 'args': {}}]}], [
                Tool('find', '', schema({}), find, kind='query', inline_ms=1),
                Tool('follow', '', schema({}), follow)])

        async def failed_point(*args, **kwargs):
            kwargs['errors'].append('original capture cannot be placed after scale change')
            return []

        async def ask(images, prompt, **kwargs):
            phase = current().admission.attempts[-1].phase
            phases.append(phase)
            prompts.append(prompt)
            actions = [{'tool': 'follow', 'args': {}}] if phase == 'query' else []
            return SimpleNamespace(data={'answer': 'The search completed; the earlier direct outline was not placed.',
                                         'actions': actions}, latency_ms=1)

        async def report(ctx, run):
            if run.answer:
                reports.append(run)

        agent.point, agent.fast_model.ask, brain.report_late = failed_point, ask, report
        result = await brain.turn('highlight a stoma')
        assert result['calls'] == 1 and result['actions'][0]['ok'] is None
        release.set()
        await asyncio.wait_for(asyncio.gather(*brain._query_answers), 1)
        assert phases == ['query', 'final'] and effects == ['follow']
        assert all('original capture cannot be placed after scale change' in prompt for prompt in prompts), \
            'MISSING_FEATURE: late planning final must retain original direct-point failure even when no object exists'
        assert len(reports) == 1 and reports[0].answer

    asyncio.run(go())


def test_spare_progress_rewrite_does_not_spend_settled_outcome_reservation():
    """The progress answer, written on a spare call while the step runs, leaves the reserved call for the outcome:
    the step finishes after it, and the late answer says the picture is complete."""
    async def go():
        release = asyncio.Event()
        phases, prompts, reports, contexts = [], [], [], []

        async def slow(ctx, args):
            contexts.append(ctx)
            await release.wait()
            return 'picture completed'

        brain, _, agent = make([{'answer': 'Preparing the picture.', 'targets': [],
                                'actions': [{'tool': 'enhance', 'args': {}}]}],
                              [Tool('enhance', '', schema({}), slow, inline_ms=1)])

        async def ask(images, prompt, **kwargs):
            phase = current().admission.attempts[-1].phase
            phases.append(phase)
            prompts.append(prompt)
            if phase == 'final':
                assert current().admission.remaining == 0  # final reservation still held
                return SimpleNamespace(data={'answer': 'The picture is still being prepared.'}, latency_ms=1)
            return SimpleNamespace(data={'answer': 'The picture is complete.'}, latency_ms=1)

        async def report(ctx, run):
            if run.answer:
                reports.append(run.answer)

        agent.fast_model.ask, brain.report_late = ask, report
        result = await brain.turn('prepare the picture')
        release.set()  # the original work completes after its progress answer went out
        await asyncio.wait_for(asyncio.gather(*brain._query_answers), 1)
        assert result['answer'] == 'The picture is still being prepared.'
        assert phases == ['final', 'late']
        assert 'still in progress' in prompts[0] and 'picture completed' in prompts[-1]
        assert reports[-1] == 'The picture is complete.'
        assert len(contexts) == 1 and contexts[0].admission.used == 3

    asyncio.run(go())


def test_a_progress_answer_whose_step_finished_while_it_was_written_is_written_again_settled():
    """T4 (live 2026-10-11 16:28:55, 16:42:22, 16:42:57): the step finished while its progress answer was being
    written, and "still being prepared" went out, the result a second later. "Still in progress" is never said of a
    finished step: the answer is written again from the settled receipts on the reserved call the late answer would
    have used, and no late answer follows."""
    async def go():
        release = asyncio.Event()
        phases, prompts, reports, contexts = [], [], [], []

        async def slow(ctx, args):
            contexts.append(ctx)
            await release.wait()
            return 'picture completed'

        brain, _, agent = make([{'answer': 'Preparing the picture.', 'targets': [],
                                'actions': [{'tool': 'enhance', 'args': {}}]}],
                              [Tool('enhance', '', schema({}), slow, inline_ms=1)])

        async def ask(images, prompt, **kwargs):
            phases.append(current().admission.attempts[-1].phase)
            prompts.append(prompt)
            if 'still in progress' in prompt.split('Current app and workspace facts')[0]:
                assert current().admission.remaining == 0  # final reservation still held
                release.set()  # original work completes while this progress rewrite is in flight
                await asyncio.sleep(0.01)
                return SimpleNamespace(data={'answer': 'The picture is still being prepared.'}, latency_ms=1)
            return SimpleNamespace(data={'answer': 'The picture is complete.'}, latency_ms=1)

        async def report(ctx, run):
            if run.answer:
                reports.append(run.answer)

        agent.fast_model.ask, brain.report_late = ask, report
        result = await brain.turn('prepare the picture')
        await asyncio.sleep(0.05)
        assert result['answer'] == 'The picture is complete.'
        assert phases == ['final', 'final']
        assert 'picture completed' in prompts[-1]
        assert not getattr(brain, '_query_answers', None) and not reports  # no second, late answer
        assert len(contexts) == 1 and contexts[0].admission.used == 3
        assert any(r.get('rule') == 'progress-settled' for r in agent.logged)

    asyncio.run(go())
