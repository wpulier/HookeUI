"""One labeler (option NAMING, Will's decision of 2026-10-12): every outline the app draws is named by the same step as a
tapped one, whatever asked for it; live that morning a highlight kept the running look's quick name ("round cell")
while every tap was named by the naming model."""
import asyncio
from types import SimpleNamespace

from orchestrator.tools import name_like_a_tap


def hub_with(naming="one"):
    named = []
    return SimpleNamespace(settings=SimpleNamespace(naming=naming), namer=named.append), named


def test_every_outline_the_app_draws_goes_to_the_naming_step_a_tap_uses():
    hub, named = hub_with()
    for oid, source, extra in (("o1", "ai:sight", {}), ("o2", "ai:measured:e3", {}), ("o3", "ai:find", {})):
        name_like_a_tap(hub, SimpleNamespace(id=oid, source=source, trial=None, kind="highlight", **extra))
    assert named == ["o1", "o2", "o3"]
    # A tap names its own (commands.click); a blind arm waits for its vote; a part is its dissect's.
    name_like_a_tap(hub, SimpleNamespace(id="o4", source="click", trial=None, kind="highlight"))
    name_like_a_tap(hub, SimpleNamespace(id="o5", source="ai:sight", trial="t1", kind="highlight"))
    name_like_a_tap(hub, SimpleNamespace(id="o6", source="ai:dissect", trial=None, kind="part"))
    assert named == ["o1", "o2", "o3"]
    old, kept = hub_with("per-path")
    name_like_a_tap(old, SimpleNamespace(id="o7", source="ai:sight", trial=None, kind="highlight"))
    assert kept == []


def test_a_naming_that_fails_leaves_the_outline_with_the_name_it_had():
    from orchestrator.commands import name_drawn
    from orchestrator.scene import Scene, SceneObject

    scene = Scene()
    obj = SceneObject(id="o1", kind="highlight", polygons=[[[0, 0], [10, 0], [10, 10]]], anchor_frame=1, anchor_epoch=0,
                      anchor_offset=(0.0, 0.0), source="ai:sight", label="round cell", status="likely", confidence=0.88)
    scene.add(obj)
    asked = []

    async def identify(object_id, quiet=False):
        asked.append((object_id, quiet))
        scene.update(object_id, status="looking")
        raise RuntimeError("the vision model did not answer")

    logged = []
    app = SimpleNamespace(hub=SimpleNamespace(scene=scene), agent=SimpleNamespace(identify=identify, log=logged.append))
    asyncio.run(name_drawn(app, "o1"))
    now = scene.objects["o1"]
    assert asked == [("o1", True)] and (now.label, now.status) == ("round cell", "likely")
