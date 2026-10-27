"""Synthetic acting work must use the application's real result-retention boundary."""
import asyncio
import copy

import pytest

from bench.acting.run import load_cases
from bench.acting.world import World
from orchestrator.brain.registry import Tool, ToolContext, attach, schema
from tests.test_resources import jpeg


@pytest.mark.parametrize('failure', [False, True], ids=['saved-original', 'refused-original'])
def test_acting_observers_receive_the_confirmed_retention_outcome(monkeypatch, failure):
    world = World(copy.deepcopy(load_cases()[0]))
    store = world.hub.resources.results
    original = store._write
    if failure:
        monkeypatch.setattr(store, '_write', lambda day, name, body, **kwargs:
                            False if name.endswith('.jpg') else original(day, name, body, **kwargs))
    pixels = jpeg(79)
    async def produce(ctx, args):
        attach({'output': 'image.2d.rgb8'}, picture_bytes=pixels)
        return 'A synthetic produced image'
    world.brain.registry.add(Tool('run_capability', '', schema({}), produce))
    observed = []
    world.brain.registry.finished.append(lambda run, ctx: observed.append(copy.deepcopy(run.wire(full=True))))
    async def execute():
        run = world.brain.registry.start('run_capability', {}, ToolContext('system', 1, None))
        await run.task
        return run
    run = asyncio.run(execute())
    assert run.ok is (not failure), run.summary
    assert run.resource is not None
    assert observed == [run.wire(full=True)], 'the acting model must receive the same settled facts as the app'
    entry = world.hub.resources.get(run.resource['id'])
    assert entry.state == ('failed' if failure else 'done')
    if failure:
        assert run.resource['kind'] == 'Result not saved'
        assert 'storage' in str(run.next).lower()
    else:
        assert world.hub.resources.picture(run.resource['id'])[0] == pixels
