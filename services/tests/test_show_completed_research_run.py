"""A retained start handle may show its exact completed work, never replacement work.

Real Manager/Runner/Presenter with controlled external worker and structural Studio.
"""
import asyncio

import pytest

from tests.test_research_completion_dependencies import ResearchRig, resource
from tests.test_mission_manager_interleavings import dispatched
from tests.test_population import world  # noqa: F401

# These start real-model researchers, which need their model's key (conftest.research_key).
pytestmark = pytest.mark.usefixtures('research_key')


def test_show_completed_start_handle_preserves_original_result_and_ignores_newer_research(world, tmp_path):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            start, job = await rig.start()
            original = resource(start)
            rig.worker.finish(job, 'The original measured comparison')
            await asyncio.shield(rig.attempt.completion)
            await rig.manager.start('Unrelated later work', effort='quick')
            later = await dispatched(rig.manager, rig.worker, 1)
            rig.worker.finish(later, 'Unrelated newer comparison')
            showing = await rig.plan([{'tool': 'show_result',
                'args': {'researcher': {'step': 1, 'output': 0}}, 'after_steps': [1]}], [2])
            await rig.finish_sequence(showing)
            assert showing[0].ok is True, showing[0].wire(full=True)
            assert rig.presenter.open and rig.presenter.rid == job['rid']
            assert rig.studio.items['viewer']['shows'] == job['rid']
            assert resource(start) == original
            assert rig.ticket.admission.used == 0
        finally:
            await rig.close()
    asyncio.run(scenario())


@pytest.mark.parametrize('state', ['pending', 'failed', 'mutated', 'replaced'])
def test_start_handle_never_substitutes_unfinished_changed_or_replaced_work(world, tmp_path, state):
    async def scenario():
        rig = ResearchRig(world, tmp_path)
        try:
            _, job = await rig.start()
            if state == 'failed':
                rig.worker.finish(job, 'Failed finding', status='failed')
            elif state in ('mutated', 'replaced'):
                rig.worker.finish(job, 'Original completion')
                await asyncio.shield(rig.attempt.completion)
                if state == 'mutated':
                    rig.session.result['summary'] = 'Changed after original completion'
                else:
                    await rig.manager.followup(job['rid'], 'A new requested run', mode='continue',
                                               origin={'channel': 'chat', 'thread': rig.day.active_id})
                    replacement = await dispatched(rig.manager, rig.worker, 1)
                    rig.worker.finish(replacement, 'Replacement result')
            showing = await rig.plan([{'tool': 'show_result',
                'args': {'researcher': {'step': 1, 'output': 0}}, 'after_steps': [1]}], [2])
            await rig.finish_sequence(showing)
            assert showing[0].ok is False and not rig.presenter.open
            assert not showing[0].outputs
            assert rig.ticket.admission.used == 0
        finally:
            await rig.close()
    asyncio.run(scenario())
