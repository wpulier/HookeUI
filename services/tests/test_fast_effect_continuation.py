"""Logged fast-effect → camera handoff regression; scripted models/pixels only.

The production Brain, Registry, Finder and scene tools execute the original
clear/find sequence. These tests qualify planner input freshness, not segmentation
quality or the eventual model's interpretation of those inputs.
"""
import asyncio

import pytest

from orchestrator.brain.loop import Brain
from orchestrator.brain.toolbox import Services, build
from tests.fakes import Hub, conversation
from tests.test_brain import FakeAgent, FakeVision
from tests.test_finder import Agent_, outline, outlines  # noqa: F401


def test_fast_actions_are_in_the_camera_continuation_context(outlines):
    hub = Hub()
    hub.settings.fast = 'gemini-3.8-flash'
    hub.latest_info.width, hub.latest_info.height = 1280, 720
    day = conversation()
    before = [outline(hub, 'person', (100, 100, 600, 700)),
              outline(hub, 'right eye', (200, 200, 250, 230)),
              outline(hub, 'left eye', (300, 200, 350, 230))]
    finder = Agent_(hub, [('right eye', (200, 200, 250, 230), .95),
                          ('left eye', (300, 200, 350, 230), .94)])
    agent = FakeAgent(hub, day, [{'answer': 'Only his eyes.', 'targets': [], 'actions': []}])
    agent.fast_model = FakeVision(fast={
        'intent': 'request', 'needs_look': True, 'answer': 'Marking the eyes.',
        'actions': [{'tool': 'clear', 'args': {'what': 'outlines'}},
                    {'tool': 'find', 'args': {'what': 'eyes', 'all': True}}],
    })
    agent.look_in = finder.look_in
    agent.snapshot = finder.snapshot
    services = Services(hub=hub, agent=agent)
    brain = Brain(hub, agent, build(services), day)
    brain.services = services
    look = agent.look

    async def select_new_eye(*args, **kwargs):
        eye = next(o for o in hub.scene.objects.values() if o.label == 'left eye')
        agent.replies[0]['actions'] = [{'tool': 'select', 'args': {'object': eye.id}}]
        return await look(*args, **kwargs)

    agent.look = select_new_eye
    result = asyncio.run(brain.turn('only his eyes not the head'))
    assert [a['tool'] for a in result['actions']] == ['clear', 'find', 'select']
    assert all(a['ok'] for a in result['actions'])
    table = agent.calls[0]['tools'][1]
    assert 'box x ' not in table  # these identities need not lie on the held camera picture
    for o in before:
        assert f'{o.id} ' not in table, table
    for o in hub.scene.objects.values():
        assert f'{o.id} ' in table, table
    assert 'cleared 3 outlines' in agent.calls[0]['question']
    assert 'outlined 2' in agent.calls[0]['question']
    assert all(a['actionId'] in agent.calls[0]['question'] for a in result['actions'][:2])
    assert hub.scene.objects[hub.scene.selected].label == 'left eye'


def test_failed_fast_continuation_preserves_original_pointing():
    from orchestrator.board import Board
    from orchestrator.brain.registry import Tool, ToolFailed, schema

    hub = Hub()
    hub.settings.fast = 'gemini-3.8-flash'
    day = conversation()
    agent = FakeAgent(hub, day, [{'answer': 'The lookup failed.', 'targets': [], 'actions': []}])
    agent.fast_model = FakeVision(fast={
        'intent': 'request', 'needs_look': False, 'answer': 'Checking.',
        'actions': [{'tool': 'selection_probe', 'args': {}}],
    })
    services = Services(hub=hub, agent=agent)
    brain = Brain(hub, agent, build(services), day)
    brain.services, brain.board = services, Board(hub)
    original = outline(hub, 'left eye', (300, 200, 350, 230), source='click')
    newer = outline(hub, 'other selection', (700, 200, 750, 230), source='click')
    hub.scene.select(original.id)
    heard = brain.board.when_asked()

    async def fail_after_selection(ctx, args):
        assert ctx.when_asked is heard
        hub.scene.select(newer.id)
        raise ToolFailed('the original lookup failed', next='look again')

    brain.registry.add(Tool('selection_probe', 'Test external failure.', schema({}), fail_after_selection))
    result = asyncio.run(brain.turn('check that', when_asked=heard))
    assert result['actions'][0]['ok'] is False
    assert 'the original lookup failed' in agent.calls[0]['question']
    assert agent.calls[0]['tools'][2] == f' (selected: {original.id} when the user asked, {newer.id} now)'
    assert set(hub.scene.objects) == {original.id, newer.id}


def test_sight_search_scope_is_not_a_visible_outline(outlines):
    from types import SimpleNamespace
    from orchestrator.sight import SightView, Seen

    hub = Hub()
    hub.settings.fast = 'gemini-3.8-flash'
    hub.latest_info.width, hub.latest_info.height = 1280, 720
    day, info = conversation(), hub.latest_info
    seen = SightView(info.id, info.epoch, info.dx, info.dy, 300., 'Person in view', 'person',
                     [Seen('s1', 'person', 'centre', (100, 100, 600, 700), .9)], 'sharp')
    agent = FakeAgent(hub, day, [{'answer': 'Done.', 'targets': [], 'actions': []}])
    agent.fast_model = FakeVision(fast={
        'intent': 'request', 'needs_look': False, 'answer': 'Finding the left eye.',
        'actions': [{'tool': 'find', 'args': {'what': 'left eye', 'inside': 's1'}}],
    })
    finder = Agent_(hub, [('left eye', (300, 200, 350, 230), .94)])
    agent.snapshot = finder.snapshot
    user = outline(hub, "user's mark", (900, 200, 950, 250), source='click')
    intermediate = []

    async def look_in(*args, **kwargs):
        intermediate.extend(o.id for o in hub.scene.objects.values())
        return await finder.look_in(*args, **kwargs)

    agent.look_in = look_in
    services = Services(hub=hub, agent=agent, sight=SimpleNamespace(view=seen))
    brain = Brain(hub, agent, build(services), day)
    brain.services, brain.sight = services, services.sight
    result = asyncio.run(brain.turn('highlight his left eye only'))
    assert all(a['ok'] for a in result['actions']), result['actions']
    assert intermediate == [user.id]
    assert {o.label for o in hub.scene.objects.values()} == {'left eye', "user's mark"}
    assert len(result['actions'][0]['outputs']) == 1
    assert finder.calls[0][1] is not None


def test_sight_region_and_concept_matcher_share_the_original_capture(outlines):
    import copy
    from orchestrator.finder import Finder, FindRef
    from orchestrator.brain.registry import ToolContext
    from orchestrator.sight import SightView, Seen

    hub = Hub()
    held = copy.copy(hub.latest_info)
    seen = SightView(held.id, held.epoch, held.dx, held.dy, 300., 'Original view', 'person',
                     [Seen('s1', 'person', 'centre', (100, 100, 600, 700), .9)], 'sharp')
    agent = Agent_(hub, [])
    snapshot = agent.snapshot()
    agent.snapshot = lambda frame_id=None: (held, snapshot[1], snapshot[2])
    hub.latest_info = copy.copy(held)
    hub.latest_info.id += 1
    hub.latest_info.epoch += 1  # the live camera moved; the request still owns its old picture
    called = []

    async def segment(hub_, capture, box, words):
        called.append((capture, box, words))
        return []

    ctx = ToolContext('chat', 1, held.id, 'find inside that person', seen_view=seen)
    asyncio.run(Finder(hub, agent, segment_all=segment).find(FindRef('eye', inside='s1', by='concept'), ctx))
    assert called == [(held, None, 'eye')]
    assert not hub.scene.objects


def test_missing_or_moved_original_sight_region_never_becomes_an_outline(outlines):
    import copy
    import pytest
    from orchestrator import tools
    from orchestrator.finder import Finder, FindRef
    from orchestrator.brain.registry import ToolContext
    from orchestrator.sight import SightView, Seen

    for case in ('missing', 'unknown', 'moved'):
        hub = Hub()
        info = hub.latest_info
        seen = SightView(info.id, info.epoch, info.dx, info.dy, 300., 'Person', 'person',
                         [Seen('s1', 'person', 'centre', (100, 100, 600, 700), .9)], 'sharp')
        agent = Agent_(hub, [('eye', (300, 200, 350, 230), .94)])
        context = ToolContext('chat', 1, info.id, 'find eye', seen_view=None if case == 'missing' else seen)
        if case == 'moved':
            hub.latest_info = copy.copy(info)
            hub.latest_info.epoch += 1
        with pytest.raises(tools.ToolError, match='search region'):
            asyncio.run(Finder(hub, agent).find(FindRef('eye', inside='s2' if case == 'unknown' else 's1'), context))
        assert not hub.scene.objects and not agent.calls


def test_original_sight_region_does_not_reuse_a_new_epoch_outline(outlines):
    import copy
    from orchestrator.finder import Finder, FindRef
    from orchestrator.brain.registry import ToolContext
    from orchestrator.sight import SightView, Seen

    hub = Hub()
    held = copy.copy(hub.latest_info)
    seen = SightView(held.id, held.epoch, held.dx, held.dy, 300., 'Person', 'person',
                     [Seen('s1', 'person', 'centre', (100, 100, 600, 700), .9)], 'sharp')
    wrong = outline(hub, 'eye', (300, 200, 350, 230), source='click')
    wrong.anchor_epoch += 1
    wrong.anchor_frame += 1
    agent = Agent_(hub, [('eye', (300, 200, 350, 230), .94)])
    snapshot = agent.snapshot()
    agent.snapshot = lambda frame_id=None: (held, snapshot[1], snapshot[2])
    ctx = ToolContext('chat', 1, held.id, 'find eye', seen_view=seen)
    found = asyncio.run(Finder(hub, agent).find(FindRef('eye', inside='s1'), ctx))
    assert agent.calls and found.objects[0] is not wrong
    assert hub.scene.objects[wrong.id] is wrong


@pytest.mark.parametrize(('quantity_args', 'expected'), [
    ({}, {'right eye', 'left eye'}),
    ({'all': False}, {'right eye'}),
    ({'all': True}, {'right eye', 'left eye'}),
])
def test_omitted_quantity_preserves_the_models_requested_instances(outlines, quantity_args, expected):
    """Actual acceptance plan omitted all; the Finder returned two compact eyes."""
    from orchestrator.brain.registry import ToolContext

    hub = Hub()
    agent = Agent_(hub, [('right eye', (200, 200, 250, 230), .95),
                         ('left eye', (300, 200, 350, 230), .94)])
    registry = build(Services(hub=hub, agent=agent))
    context = ToolContext('chat', 1, hub.latest_info.id, 'highlight his eyes')
    asyncio.run(registry.tools['find'].handler(context, {'what': 'eyes', **quantity_args}))
    assert {o.label for o in hub.scene.objects.values()} == expected
    assert 'highlight his eyes' in agent.calls[0][0]


@pytest.mark.parametrize(('source', 'replace', 'matches', 'container_stays'), [
    ('ai:gemini', False, True, True),
    ('ai:gemini', True, True, False),
    ('click', True, True, True),
    ('ai:gemini', True, False, True),
])
def test_explicit_container_replacement_requires_a_result_and_preserves_user_marks(
    outlines, source, replace, matches, container_stays,
):
    from orchestrator.brain.registry import ToolContext, ToolFailed

    hub = Hub()
    container = outline(hub, 'both eyes', (100, 100, 500, 300), source=source)
    other = outline(hub, 'another app result', (700, 100, 800, 200))
    boxes = [('left eye', (300, 200, 350, 230), .94)] if matches else []
    agent = Agent_(hub, boxes)
    registry = build(Services(hub=hub, agent=agent))
    context = ToolContext('chat', 1, hub.latest_info.id, 'highlight his left eye only')
    args = {'what': 'left eye', 'inside': container.id, 'keep': True}
    if replace:
        args['keep_container'] = False
    call = registry.tools['find'].handler(context, args)
    if matches:
        said = asyncio.run(call)
        assert any(o.label == 'left eye' for o in hub.scene.objects.values())
        if not container_stays:
            assert container.id in said and 'removed' in said
    else:
        with pytest.raises(ToolFailed, match="couldn't find"):
            asyncio.run(call)
    assert (container.id in hub.scene.objects) is container_stays
    assert hub.scene.objects[other.id] is other


def test_original_sight_region_refuses_an_example_from_another_picture(outlines):
    from orchestrator import tools
    from orchestrator.brain.registry import ToolContext
    from orchestrator.finder import Finder, FindRef
    from orchestrator.sight import SightView, Seen

    hub = Hub()
    info = hub.latest_info
    seen = SightView(info.id, info.epoch, info.dx, info.dy, 300., 'Person', 'person',
                     [Seen('s1', 'person', 'centre', (100, 100, 600, 700), .9)], 'sharp')
    example = outline(hub, 'eye', (300, 200, 350, 230), source='click')
    example.anchor_frame += 1
    agent = Agent_(hub, [])
    ctx = ToolContext('chat', 1, info.id, 'find eyes like this', seen_view=seen)
    with pytest.raises(tools.ToolError, match='different pictures'):
        asyncio.run(Finder(hub, agent).find(FindRef('eye', inside='s1', like=example.id), ctx))
    assert list(hub.scene.objects.values()) == [example]
    assert not agent.calls


def test_container_returned_as_a_match_is_not_deleted_or_reported_missing():
    from orchestrator.brain.registry import ToolContext

    hub = Hub()
    container = outline(hub, 'region', (100, 100, 500, 300))
    agent = Agent_(hub, [])

    async def matched_container(hub_, capture, box, words):
        # The real Finder resolves a concept match to its existing outline.
        return [{'polygons': container.polygons, 'score': .95}]

    registry = build(Services(hub=hub, agent=agent, segment_all=matched_container))
    context = ToolContext('chat', 1, hub.latest_info.id, 'find matching regions')
    said = asyncio.run(registry.tools['find'].handler(context, {
        'what': 'region', 'inside': container.id, 'by': 'concept', 'keep_container': False,
    }))
    assert hub.scene.objects[container.id] is container
    assert context.objects == [container.id]
    assert 'removed' not in said
