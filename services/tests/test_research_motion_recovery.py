"""Actual researcher/Hands acquisition failure and a separate later camera attempt.

Synthetic ingress only: no replaced handler, manufactured result or model retry.
"""
import asyncio

import pytest

from orchestrator.motiontracks import tracks_of
from tests.test_motion_tracks import moving_slide
from tests.test_research_fresh_motion import feed_next, motion_hub, worker
from tests.waiting import HANG_S


@pytest.mark.parametrize('failure', ['registration', 'epoch'])
def test_broken_interval_offers_new_acquisition_without_reusing_failed_frames(tmp_path, failure):
    async def scenario():
        hub = motion_hub()
        researcher, manager, original, messages, _ = worker(tmp_path, hub)
        samples = moving_slide(seconds=1.6)
        request = {'operation': 'motion', 'seconds': 1.5, 'fresh': True, 'why': 'compare independent intervals'}
        first = asyncio.create_task(researcher.tool_run(request))
        await asyncio.sleep(1 / 30)  # next external camera frame, after acquisition starts
        broken = hub.add(samples[0].image, 500, -300, wall=104.0,
                         epoch=1 if failure == 'epoch' else 0)
        if failure == 'registration':
            broken.tracked = False
        refused, picture = await asyncio.wait_for(first, HANG_S)
        assert refused.get('error') and picture is None
        assert not original.ran and not tracks_of(hub).items and researcher.runs == 0
        assert manager.hands.busy is None and not hub.captures.held()
        assert hub.captures.memory.reserved['research'] == 0
        assert 'new upcoming interval' in refused.get('next', ''), (
            'a lost interval does not mean the researcher must abandon future acquisition')
        # A deliberate second tool call gets its own boundary and real frames.
        # No implicit retry was issued by app code while the researcher decided.
        assert len(messages) == 1
        hub.add(samples[0].image, 500, -300, wall=105.0)
        second = asyncio.create_task(researcher.tool_run(request))
        emitted = await feed_next(hub, samples, 105.1)
        result, picture = await asyncio.wait_for(second, HANG_S)
        assert result.get('ok') and picture is None, result
        item = tracks_of(hub).items[result['id']]
        used = {row[0] for row in item.frames}
        assert used and used <= set(emitted) and broken.id not in used
        assert item.span['from'] > broken.wall_at
        assert researcher.runs == 1 and len(original.ran) == 1 and len(messages) == 2
        assert manager.hands.busy is None
    asyncio.run(scenario())


@pytest.mark.parametrize('unavailable', ['camera', 'retired'])
def test_unavailable_camera_or_retired_owner_does_not_offer_interval_retry(tmp_path, unavailable):
    async def scenario():
        hub = motion_hub()
        researcher, manager, original, _, _ = worker(tmp_path, hub)
        if unavailable == 'camera':
            hub.source.is_fresh = lambda: False
        else:
            original.status = 'done'
        result, picture = await researcher.tool_run({'operation': 'motion', 'fresh': True,
                                                    'seconds': 1.5, 'why': 'read this run'})
        assert result.get('error') and picture is None
        assert 'new upcoming interval' not in result.get('next', '')
        assert not original.ran and not tracks_of(hub).items and researcher.runs == 0
        assert manager.hands.busy is None
    asyncio.run(scenario())
