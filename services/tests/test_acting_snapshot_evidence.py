"""The synthetic journey scores retained capture identity, not snapshot captions."""
import asyncio
import copy
import json

from bench.acting.run import holds, load_cases, typed
from bench.acting.world import World
from orchestrator.screen import image_shown


def test_snapshot_score_requires_this_request_receipt_pixels_and_display():
    case = copy.deepcopy(next(c for c in load_cases() if c["id"] == "journey-1-highlight-zoom-snapshot-three-requests"))
    world = World(case)
    predicate = {"snapshot_shown": "snapshot-request"}

    async def scenario():
        await typed(world, "take a snapshot", "earlier-request")
        earlier = dict(world.magnifier.picture_now())
        assert not holds(predicate, world.board())  # an older snapshot cannot satisfy the new request
        world.hub.slide.move(dx=30)  # the next request sees a different source frame
        await typed(world, "take another snapshot", "snapshot-request")
        current = dict(world.magnifier.picture_now())
        assert current["url"] != earlier["url"]
        assert holds(predicate, world.board())
        await image_shown(world.magnifier, current["url"], "Any caption", kind_of="capture")
        assert holds(predicate, world.board())
        await image_shown(world.magnifier, earlier["url"], "Snapshot of the stoma", kind_of="capture")
        assert not holds(predicate, world.board())  # a prior capture is not this request's result
        await image_shown(world.magnifier, current["url"], "Any caption", kind_of="capture")
        run, ctx = world.snapshot_runs[-1]
        path = world.root / run.values["file"]
        sidecar = path.with_suffix(".json")
        source = sidecar.read_bytes()
        facts = json.loads(source)
        facts["frameId"] = 999999
        sidecar.write_text(json.dumps(facts))
        assert not holds(predicate, world.board())
        sidecar.write_bytes(source)
        assert holds(predicate, world.board())
        path.unlink()
        assert not holds(predicate, world.board())

    asyncio.run(scenario())
