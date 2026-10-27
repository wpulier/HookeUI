"""A look's actual output remains addressable by the next step of that request."""
from types import SimpleNamespace

import pytest

from orchestrator import tools
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.view import add_highlight
from orchestrator.ticket import issue, using
from tests.test_apply import World, run


@pytest.mark.parametrize('arguments', [{'things': ['target 2']}, {'things': 'target 2'}, {'thing': 'target 2'}])
def test_highlight_reuses_request_target_without_another_sight_or_segmentation(arguments):
    hub = World()
    with using(issue(hub, "command")):
        obj = run(tools.outline_at(hub, hub.latest_info.id, 300, 200, label="Central debris"))
    registry = Registry()

    async def unexpected_sight(ref, ctx):
        raise AssertionError("a produced target is not an older Sight id")

    add_highlight(registry, hub, SimpleNamespace(view=None), unexpected_sight)
    ctx = ToolContext("chat", 1, hub.latest_info.id, objects=[obj.id], targets=[None, obj.id])
    calls = len(hub.gpu.calls)
    with using(issue(hub, "command")):
        summary = run(registry.tools["highlight"].handler(ctx, {**arguments, "keep": True}))
        assert "Central debris" in summary
        assert 'could not be shown' not in summary
        assert len(hub.gpu.calls) == calls
        with pytest.raises(ToolFailed):
            run(registry.tools["highlight"].handler(ctx, {"things": ["target 1"], "keep": True}))


@pytest.mark.parametrize('things', [42, 0, False, {}, {'s1': True}, ['s1', {}], ['s1', 2]])
def test_malformed_highlight_targets_fail_before_any_outline_or_rename(things):
    hub = World()
    registry = Registry()
    touched = []

    async def unexpected_sight(ref, ctx):
        touched.append(ref)
        raise AssertionError('invalid target collection must be rejected before outlining')

    add_highlight(registry, hub, SimpleNamespace(view=None), unexpected_sight)
    ctx = ToolContext('chat', 1, hub.latest_info.id)
    with pytest.raises(ToolFailed, match='target'):
        run(registry.tools['highlight'].handler(ctx, {'things': things, 'thing': 's1', 'label': 'changed'}))
    assert touched == []
