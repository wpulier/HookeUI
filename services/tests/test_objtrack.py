"""Outlines that follow their own object (orchestrator/objtrack.py), with a stand-in for the GPU
worker's `track` op (EdgeTAM's video model)."""
import asyncio
from types import SimpleNamespace

from orchestrator.objtrack import MISSES_TO_DROP, ObjectFollower
from orchestrator.options import Settings
from orchestrator.scene import Scene, SceneObject


def square(x, y, r=10):
    return [[[x - r, y - r], [x + r, y - r], [x + r, y + r], [x - r, y + r]]]


class Heavy:
    """The heavy worker: says where each followed object is, as scripted."""

    def __init__(self):
        self.calls, self.where, self.following = [], {}, set()

    async def ensure_started(self):
        return self

    async def call(self, op, timeout=0, **req):
        self.calls.append(req)
        objects = {}
        self.following = (self.following | {s["id"] for s in req["start"]}) - set(req["stop"])
        for oid in self.following:
            spot = self.where.get(oid)
            objects[oid] = ({"present": True, "polygons": square(*spot), "score": 9.0, "area": 400, "box": None}
                            if spot else {"present": False, "polygons": [], "score": -5.0, "area": 0, "box": None})
        return {"ok": True, "objects": objects, "ms": 90}


def pixels(hub, gray):
    """The camera's picture of every frame (PNG: exactly these pixels)."""
    import cv2

    png = cv2.imencode(".png", gray)[1].tobytes()
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=png))


def make(track="tracking", epoch=0):
    scene = Scene()
    scene.add(SceneObject(id="o1", kind="highlight", polygons=square(100, 100), anchor_frame=1, anchor_epoch=epoch,
                          anchor_offset=(0.0, 0.0), source="ai", track=track))
    info = SimpleNamespace(id=50, dx=5.0, dy=0.0, epoch=0)
    hub = SimpleNamespace(scene=scene, latest_info=info, gpu_heavy=Heavy(), settings=Settings(track="objects"), infos={50: info},
                          source=SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=b"jpeg")))
    return ObjectFollower(hub), hub


def test_a_followed_object_is_placed_where_edgetam_sees_it():
    follower, hub = make()
    hub.gpu_heavy.where["o1"] = (140, 120)  # it swam 35 px right and 20 down beyond the slide's 5 px
    asyncio.run(follower.step())
    start = hub.gpu_heavy.calls[0]["start"]
    assert start == [{"id": "o1", "box": [95.0, 90.0, 115.0, 110.0]}]  # seeded where the slide put it
    obj = hub.scene.objects["o1"]
    assert obj.polygons == square(140, 120) and obj.anchor_frame == 50 and obj.anchor_offset == (5.0, 0.0)
    assert follower.placed_recently("o1")


def test_not_seen_changes_nothing_and_is_dropped_after_misses():
    follower, hub = make()
    for _ in range(MISSES_TO_DROP):
        asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(100, 100)  # slide motion still places it
    assert "o1" not in follower.followed and not follower.placed_recently("o1")
    asyncio.run(follower.step())
    assert "o1" not in follower.followed  # not started again from where it was lost
    hub.scene.update("o1", anchor_frame=60)  # re-found by its template, say
    asyncio.run(follower.step())
    assert "o1" in follower.followed


def test_an_unverified_outline_is_never_used_to_start_following():
    for track, epoch in (("refinding", 0), ("tracking", 3)):
        follower, hub = make(track, epoch)
        asyncio.run(follower.step())
        assert not hub.gpu_heavy.calls and not follower.followed


def test_parts_move_with_their_object():
    follower, hub = make()
    hub.scene.add(SceneObject(id="p1", kind="part", polygons=square(98, 98, 3), anchor_frame=1, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="dissect", parent="o1"))
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    part = hub.scene.objects["p1"]
    assert part.polygons == square(138, 118, 3) and part.anchor_frame == 50  # 98 + 5 slide + 35 own, 98 + 20


def test_an_object_where_the_slide_puts_it_is_left_as_it_is():
    follower, hub = make()
    hub.gpu_heavy.where["o1"] = (111, 103)  # 6 px from where the slide's 5 px move draws it
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(100, 100) and not follower.placed_recently("o1")


def test_across_epochs_edgetam_is_taken_only_where_the_objects_looks_are():
    """Away from where an outline was made (another epoch), EdgeTAM can latch on to a look-alike:
    the outline moves there only if its template is found at that place (sim `refind`: 66 px)."""
    import numpy as np

    rng = np.random.default_rng(4)
    gray = (rng.random((400, 500)) * 255).astype(np.uint8)
    follower, hub = make()
    hub.latest_info = SimpleNamespace(id=50, dx=0.0, dy=0.0, epoch=1)  # continuity was lost since
    hub.infos = {50: hub.latest_info}
    pixels(hub, gray)
    obj = hub.scene.objects["o1"]
    follower.followed["o1"] = {"misses": 0, "area": 400.0}  # followed since before the loss
    hub.gpu_heavy.following = {"o1"}

    # A look-alike: nothing like the object's template where EdgeTAM puts it.
    obj.template, obj.template_origin = gray[300:340, 400:440].copy(), (80, 80)
    hub.gpu_heavy.where["o1"] = (210, 120)
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].anchor_epoch == 0  # not moved into the new epoch

    # The object itself: its template is right there.
    obj.template = gray[100:140, 190:230].copy()  # 20 px of surroundings around (210, 120)
    asyncio.run(follower.step())
    moved = hub.scene.objects["o1"]
    assert moved.anchor_epoch == 1 and moved.polygons == square(210, 120)


def test_a_moved_object_is_placed_where_its_looks_are_keeping_its_shape():
    """EdgeTAM says roughly where the object went; its mask's centre moves with its shape (sim
    `refind`: masks that grew lopsidedly put outlines 11-18 px off). The object's own template,
    searched near there, says exactly where, and the outline moves there with its own shape."""
    import numpy as np

    rng = np.random.default_rng(5)
    gray = (rng.random((400, 500)) * 255).astype(np.uint8)
    follower, hub = make()
    pixels(hub, gray)
    obj = hub.scene.objects["o1"]
    # It swam 30 px right and 20 down beyond the slide's 5 px: its looks are now around (135, 120).
    obj.template, obj.template_origin = gray[100:140, 115:155].copy(), (80, 80)
    hub.gpu_heavy.where["o1"] = (141, 117)  # EdgeTAM's mask, centred 6 px right and 3 px up of it
    asyncio.run(follower.step())
    moved = hub.scene.objects["o1"]
    assert moved.polygons == square(135, 120) and moved.anchor_frame == 50
    assert moved.template_origin == (115, 100)

    # Its looks are nowhere near where EdgeTAM puts it: left to the slide.
    follower, hub = make()
    pixels(hub, gray)
    obj = hub.scene.objects["o1"]
    obj.template, obj.template_origin = gray[300:340, 400:440].copy(), (80, 80)
    hub.gpu_heavy.where["o1"] = (141, 117)
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(100, 100) and not follower.placed_recently("o1")


def test_a_blurred_or_refocusing_view_leaves_outlines_to_the_slide():
    """While the phone refocuses by itself (the eyepiece disc changes size) or the view is flagged
    blurry, EdgeTAM's masks are unreliable: the follower moves nothing (sim `refind`: outlines moved
    during the phone's hunt at footage frames 6340-6460 were 10-16 px off once it was sharp)."""
    for disc_then, quality in ((399.3, ("blurry",)), (395.3, ())):
        follower, hub = make()
        hub.infos = {50 - k: SimpleNamespace(id=50 - k, dx=5.0, dy=0.0, epoch=0, quality=quality if k == 3 else (),
                                             disc=399.3 if k < 15 else disc_then) for k in range(30)}
        hub.latest_info = hub.infos[50]
        hub.gpu_heavy.where["o1"] = (140, 120)
        asyncio.run(follower.step())
        assert hub.scene.objects["o1"].polygons == square(100, 100) and not follower.placed_recently("o1")
    # The same view, sharp at a steady scale: moved.
    follower, hub = make()
    hub.infos = {50 - k: SimpleNamespace(id=50 - k, dx=5.0, dy=0.0, epoch=0, quality=(), disc=399.3) for k in range(30)}
    hub.latest_info = hub.infos[50]
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(140, 120)


def test_frames_the_motion_tracker_could_not_place_move_nothing():
    """In the first frames of a cut the tracker cannot register the view and carries the last offset
    over, so the slide looks still while the view is already elsewhere; EdgeTAM latched on to the new
    view there and the outline came back 72 px off (sim `refind`). Nothing moves until the tracker
    has placed the last frames again."""
    follower, hub = make()
    hub.infos = {50 - k: SimpleNamespace(id=50 - k, dx=5.0, dy=0.0, epoch=0, tracked=k > 2) for k in range(30)}
    hub.latest_info = hub.infos[50]
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(100, 100) and not follower.placed_recently("o1")
    # Registered again for the last frames: moved.
    for info in hub.infos.values():
        info.tracked = True
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(140, 120)


def test_after_a_new_day_the_follower_follows_again_under_a_new_ticket():
    """A day event (or a tracking reset) supersedes the follower's ticket; the next step starts again
    under a new one, so its writes are applied (scaffolding review, M1.1 item 1)."""
    follower, hub = make()
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(140, 120)
    hub.scene.generations.bump("track", "session", why="a new day started")
    hub.gpu_heavy.where["o1"] = (170, 135)
    asyncio.run(follower.step())
    assert hub.gpu_heavy.calls[-1]["reset"] is True  # the worker's session starts again too
    assert hub.scene.objects["o1"].polygons == square(170, 135) and follower.placed_recently("o1")


def test_a_refused_write_never_marks_an_object_placed():
    follower, hub = make()
    hub.gpu_heavy.where["o1"] = (140, 120)
    call = hub.gpu_heavy.call

    async def reset_meanwhile(op, timeout=0, **req):
        hub.scene.generations.bump("track", why="tracking was reset")  # while EdgeTAM was working
        return await call(op, timeout, **req)

    hub.gpu_heavy.call = reset_meanwhile
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(100, 100)  # the move was refused
    assert not follower.placed_recently("o1")  # so the hub's own check still covers it


def test_a_reply_to_an_older_call_changes_nothing():
    follower, hub = make()
    hub.gpu_heavy.where["o1"] = (140, 120)
    call = hub.gpu_heavy.call

    async def older(op, timeout=0, **req):
        reply = await call(op, timeout, **req)
        return {**reply, "session": req["session"], "seq": req["seq"] - 1}

    hub.gpu_heavy.call = older
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(100, 100)


L_SHAPE = [[[90, 90], [120, 90], [120, 100], [100, 100], [100, 115], [90, 115]]]  # micro-sam's own outline


def test_another_models_shape_is_moved_never_redrawn_by_edgetam():
    """Live 2026-10-11: "outline it with micro-sam" showed EdgeTAM's shape about 0.1 s later. Now another model's
    outline keeps its shape and moves by how far EdgeTAM's mask moved on the slide."""
    follower, hub = make()
    obj = hub.scene.objects["o1"]
    obj.polygons, obj.made_by = [list(map(list, p)) for p in L_SHAPE], {"implementation": "micro-sam-lm"}
    # Its first step: EdgeTAM's mask (a square) is not micro-sam's shape, so its centre is off the outline's by
    # ~13 px, more than a move. Nothing has moved: the outline stays (the old rule moved and reshaped it here).
    hub.gpu_heavy.where["o1"] = (118, 110)
    asyncio.run(follower.step())
    assert obj.polygons == L_SHAPE
    assert obj.followed_by["implementation"] == "edgetam-video" and obj.followed_by["shape"] == "kept"
    assert obj.followed_by["until"] is None
    # Then the thing swims 30 px right: the outline is micro-sam's shape, moved by the slide's 5 px and its own 30.
    hub.gpu_heavy.where["o1"] = (148, 110)
    asyncio.run(follower.step())
    assert obj.polygons == [[[x + 35, y] for x, y in L_SHAPE[0]]] and obj.polygons != square(148, 110)
    assert follower.placed_recently("o1")
    # Its next move is measured from there, not from where it began.
    hub.gpu_heavy.where["o1"] = (150, 110)  # 2 px: still where it is
    before = obj.polygons
    asyncio.run(follower.step())
    assert obj.polygons == before


def test_edgetams_own_outline_takes_its_video_models_shape_and_following_ends_said():
    follower, hub = make()
    obj = hub.scene.objects["o1"]
    obj.made_by = {"implementation": "edgetam"}
    hub.gpu_heavy.where["o1"] = (140, 120)
    asyncio.run(follower.step())
    assert obj.polygons == square(140, 120) and obj.followed_by["shape"] == "redrawn"
    hub.scene.update("o1", kind="part")  # no longer one of those followed: following stops, and says when
    asyncio.run(follower.step())
    assert obj.followed_by["until"] is not None and "o1" not in follower.followed


def test_the_followers_model_loads_once_the_click_worker_is_ready_and_frames_come(monkeypatch):
    """READY_WHEN "frames": EdgeTAM's video model is loaded on the follow worker before anything is outlined, through
    the preload's own path (loads.py `_ready`: logged `model-preload`, quiet, in the background)."""
    import orchestrator.objtrack as objtrack

    monkeypatch.setattr(objtrack, "READY_POLL_S", 0.01)
    follower, hub = make()
    readied = []

    class Loads:
        async def _ready(self, client, model):
            readied.append((client, model))
            return True

    hub.gpu, hub.gpu_follow, hub.loads, hub.latest_info = SimpleNamespace(ready=False), object(), Loads(), None

    async def run():
        task = asyncio.ensure_future(follower.ready())
        await asyncio.sleep(0.05)
        assert not readied  # no frame yet, and the click worker still warming
        hub.latest_info = SimpleNamespace(id=1)
        await asyncio.sleep(0.05)
        assert not readied  # a frame, but the click worker is not ready
        hub.gpu.ready = True
        return await asyncio.wait_for(task, 1.0)

    assert asyncio.run(run()) is True
    assert readied == [(hub.gpu_follow, "edgetam-video")]


def test_the_followers_model_waits_for_following_and_first_use_is_kept_as_an_option(monkeypatch):
    import orchestrator.objtrack as objtrack

    monkeypatch.setattr(objtrack, "READY_POLL_S", 0.01)
    follower, hub = make()
    hub.settings = Settings(track="slide")
    hub.gpu, hub.gpu_follow, hub.latest_info = SimpleNamespace(ready=True), object(), SimpleNamespace(id=1)
    hub.loads = SimpleNamespace(_ready=None)

    async def run():
        task = asyncio.ensure_future(follower.ready())
        await asyncio.sleep(0.05)
        done = task.done()
        task.cancel()
        return done

    assert asyncio.run(run()) is False  # following is off: nothing is loaded for it
    assert objtrack.ready_when({}) == "first-use"
    assert objtrack.ready_when({"MICROMIND_FOLLOW_READY": "frames"}) == "frames"
    assert objtrack.ready_when({"MICROMIND_FOLLOW_READY": "nonsense"}) == "first-use"

    async def started(env):
        monkeypatch.setenv("MICROMIND_FOLLOW_READY", env)
        follower._readying = None
        follower.start()
        readying = follower._readying
        follower.stop()
        return readying

    assert asyncio.run(started("first-use")) is None
    assert asyncio.run(started("frames")) is not None
