"""The brain's microscope tools (brain/toolbox/scope.py) through a real scope bridge on the harness's virtual OpenFlexure
microscope: what they say, what they refuse, the user's yes, the user's own words for new limits, and a receipt filed
for every action."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.scope import add_scope_tools
from orchestrator.scope import ScopeClient, from_environment, said
from scope_bridge.backends.openflexure import OpenFlexure
from scope_bridge.bridge import ScopeBridge
from scope_bridge.limits import Limits
from scope_bridge.server import serve
from sim.virtual_scope import VirtualScope


@pytest.fixture
def tools(tmp_path):
    import cv2

    rng = np.random.default_rng(2)
    slide = cv2.cvtColor(cv2.normalize(cv2.GaussianBlur(rng.uniform(0, 255, (900, 900)).astype(np.float32), (0, 0), 5),
                                       None, 30, 230, cv2.NORM_MINMAX).astype(np.uint8), cv2.COLOR_GRAY2BGR)
    scope = VirtualScope(slide, size=(320, 240), disc=(160.0, 120.0, 110.0), zoom=1.0, fps=10, motor_speed=1e6,
                         motor_z_speed=1e4)
    scope.serve()
    bridge = ScopeBridge(OpenFlexure(scope.url, um_per_z_step=0.01), tmp_path)
    x, y, z = bridge.backend.position()
    bridge.limits = Limits(x=(x - 300, x + 300), y=(y - 300, y + 300), z=(z - 5, z + 5), step_xy=200, step_z=2,
                           frames=4, seconds=30)
    server = serve(bridge, 0)
    filed: list[dict] = []
    registry = Registry()
    add_scope_tools(registry, SimpleNamespace(scope=ScopeClient(f"http://127.0.0.1:{server.server_address[1]}"),
                                              scope_journal=lambda receipt, ctx: filed.append(receipt)))
    yield registry, scope, filed
    server.shutdown()
    scope.close()


def run(registry, name: str, args: dict, question: str = "", turn: int = 1) -> str:
    ctx = ToolContext(channel="chat", turn_id=turn, frame_id=None, question=question)
    return asyncio.run(registry.tools[name].handler(ctx, args))


def test_the_tools_are_there_only_with_a_scope_bridge():
    registry = Registry()
    add_scope_tools(registry, SimpleNamespace())
    assert not registry.tools
    assert from_environment({}) is None and from_environment({"MICROMIND_SCOPE_URL": "http://example.com:1"}) is None
    assert from_environment({"MICROMIND_SCOPE_URL": "http://127.0.0.1:8780"}).url == "http://127.0.0.1:8780"


def test_move_reads_the_state_and_files_a_receipt_for_every_action(tools):
    registry, scope, filed = tools
    assert "openflexure: stage at x" in run(registry, "microscope", {})
    x0 = scope.position()[0]
    said_ = run(registry, "move_stage", {"x": 50})
    assert said_.startswith("move done;") and scope.position()[0] == pytest.approx(x0 + 50, abs=1)
    with pytest.raises(ToolFailed, match="your limit is 200"):
        run(registry, "move_stage", {"x": 250})
    with pytest.raises(ToolFailed, match="outside your limits"):
        run(registry, "move_stage", {"x": 5000, "relative": False})
    assert [r["status"] for r in filed] == ["done", "refused", "refused"]
    assert run(registry, "scope_snap", {}).startswith("snap done, 1 picture made")
    assert run(registry, "stop_scope", {}).startswith("stop done")
    assert len(filed) == 5


def test_a_large_acquisition_waits_for_the_users_own_yes(tools):
    registry, scope, filed = tools
    plan = {"time_plan": {"interval": 0, "loops": 3}, "z_plan": {"range": 2, "step": 1}}
    asked = run(registry, "scope_acquire", {"plan": plan}, question="take a z stack three times", turn=1)
    assert "waiting for the user's yes" in asked and "9 pictures" in asked and "confirms='y-" in asked
    offer = asked.split("confirms='", 1)[1].split("'", 1)[0]
    with pytest.raises(ToolFailed, match="ask them first"):  # the model answering its own question, in the same request
        run(registry, "scope_acquire", {"plan": plan, "confirms": offer, "asked_with": "take a z stack"},
            question="take a z stack three times", turn=1)
    with pytest.raises(ToolFailed, match="own words"):  # a yes the user did not say
        run(registry, "scope_acquire", {"plan": plan, "confirms": offer, "asked_with": "yes go ahead"}, question="hmm",
            turn=2)
    with pytest.raises(ToolFailed, match="own words"):  # a quote that is only a piece of a word
        run(registry, "scope_acquire", {"plan": plan, "confirms": offer, "asked_with": "es"}, question="yes", turn=2)
    with pytest.raises(ToolFailed, match="not asked about"):
        run(registry, "scope_acquire", {"plan": plan, "confirms": "y-made-up", "asked_with": "yes"}, question="yes",
            turn=2)
    done = run(registry, "scope_acquire", {"plan": plan, "confirms": offer, "asked_with": "yes, go ahead"},
               question="Yes, go ahead", turn=2)
    assert done.startswith("acquire done, 9 pictures made")
    assert [r["status"] for r in filed] == ["needs-yes", "done"]


def test_limits_change_only_on_the_users_words_and_unavailable_things_are_said(tools):
    registry, scope, filed = tools
    with pytest.raises(ToolFailed, match="own words"):
        run(registry, "scope_limits", {"set": {"step_xy": 400}, "asked_with": "make it 400"}, question="what?")
    asked = run(registry, "scope_limits", {"set": {"step_xy": 400}, "asked_with": "let it move 400 microns"},
                question="Let it move 400 microns at a time", turn=1)
    assert asked.startswith("waiting for the user's yes") and "400 µm across" in asked  # loosening asks first
    offer = asked.split("confirms='", 1)[1].split("'", 1)[0]
    now = run(registry, "scope_limits", {"set": {"step_xy": 400}, "asked_with": "yes", "confirms": offer},
              question="yes", turn=2)
    assert '"step_xy": 400' in now
    tighter = run(registry, "scope_limits", {"set": {"step_xy": 50}, "asked_with": "only 50"}, question="only 50 please")
    assert '"step_xy": 50' in tighter  # tightening: at once
    with pytest.raises(ToolFailed, match="not switched from here"):  # this microscope's light is not the bridge's
        run(registry, "scope_light", {"on": False})


def test_a_receipt_in_words():
    assert said({"kind": "move", "status": "refused", "why": "x 9 is outside"}) == "not done: x 9 is outside"
    assert said({"kind": "move", "status": "done", "verified": False, "why": "the stage stopped short on x",
                 "after": {"x": 1, "y": 2, "z": 3}}).startswith("move done (not confirmed: the stage stopped short on x)")
    assert "waiting for the user's yes: Light on?" == said({"status": "needs-yes", "ask": "Light on?"})
