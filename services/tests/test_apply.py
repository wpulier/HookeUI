"""Contract 2 and placement (contract 3, for outlines): a result changes the screen only through
apply.apply, for a request that still stands, on a target that is still there, at a place its own
pixels verify (docs/design/2026-10-06-captures-operations-evidence.md; ADR 0010)."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator import tools
from orchestrator.capture import Captures, Memory
from orchestrator.options import Settings
from orchestrator.scene import Scene, SceneObject
from orchestrator.ticket import issue, using
from tests.waiting import HANG_S, until_async

W, H, MARGIN = 1280, 720, 400
TEXTURE = cv2.GaussianBlur(np.random.default_rng(5).integers(0, 255, (H + 2 * MARGIN, W + 2 * MARGIN), dtype=np.uint8),
                           (0, 0), 2.0)


class Gpu:
    ready = True

    def __init__(self, delay=0.0):
        self.delay, self.calls = delay, []

    async def call(self, op, timeout=30.0, **payload):
        self.calls.append((op, payload))
        await asyncio.sleep(self.delay)
        x0, y0, x1, y1 = payload.get("box") or (payload["points"][0][0] - 20, payload["points"][0][1] - 20,
                                                payload["points"][0][0] + 20, payload["points"][0][1] + 20)
        return {"polygons": [[[x0 + 2, y0 + 2], [x1 - 2, y0 + 2], [x1 - 2, y1 - 2], [x0 + 2, y1 - 2]]], "score": 0.9, "ms": 5.0}


class World:
    """A hub over a textured slide: each frame is the texture moved by its offset (another epoch is
    another part of the slide); the ring can be emptied as the 4 s ring is."""

    def __init__(self):
        self.scene, self.settings = Scene(), Settings()
        self.settings.outline = "edgetam"
        self.gpu = Gpu()
        self.gpu_heavy = None
        self.infos, self.ring, self.n = {}, {}, 0
        self.latest_info = None
        self.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=self.ring[fid]) if fid in self.ring else None,
                                      is_fresh=lambda: True)
        self.captures = Captures(self, Memory())
        self.last_change = None
        self.frame(0.0, 0.0)

    def frame(self, dx, dy, epoch=0, wall=None):
        self.n += 1
        x0 = int(MARGIN - dx) + 300 * epoch
        y0 = int(MARGIN - dy) + 300 * epoch
        x0, y0 = max(0, min(TEXTURE.shape[1] - W, x0)), max(0, min(TEXTURE.shape[0] - H, y0))
        self.ring[self.n] = cv2.imencode(".jpg", TEXTURE[y0:y0 + H, x0:x0 + W], [int(cv2.IMWRITE_JPEG_QUALITY), 92])[1].tobytes()
        info = SimpleNamespace(id=self.n, width=W, height=H, dx=dx, dy=dy, epoch=epoch, fov=None, disc=0.0, tracked=True,
                               wall_at=wall if wall is not None else 1000.0 + self.n)
        self.infos[self.n] = info
        self.latest_info = info
        return info

    def candidates_in(self, frame_id):
        return []

    def map_point(self, x, y, a, b):
        a, b = self.infos.get(a), self.infos.get(b)
        return None if a is None or b is None or a.epoch != b.epoch else (x + b.dx - a.dx, y + b.dy - a.dy)


def run(coro):
    return asyncio.run(coro)


def test_an_outline_made_on_the_view_now_is_placed_there():
    world = World()
    obj = run(tools.outline_at(world, world.latest_info.id, 300, 200, box=(280, 180, 320, 220), source="click"))
    assert obj.track == "tracking" and obj.origin == 1 and obj.template is not None


def test_a_late_outline_is_checked_against_the_picture_now_and_moved_with_the_slide():
    world = World()
    capture = world.captures.take(1)
    world.frame(37.0, -21.0)  # the slide moved 43 px while the outline was made: past the check's window
    obj = run(tools.add_outline(world, capture, [[[300, 200], [340, 200], [340, 240], [300, 240]]], source="ai:test"))
    # Rechecked on the view now by its own pixels, and anchored where it was made: the slide's
    # offset carries it there (drawn at 337, 179).
    assert obj.track == "tracking" and obj.anchor_frame == 1 and obj.polygons[0][0] == [300, 200]
    assert obj.anchor_offset[0] + 37.0 == pytest.approx(world.latest_info.dx)


def test_a_late_outline_whose_pixels_are_not_where_the_slide_puts_it_is_not_drawn():
    world = World()
    capture = world.captures.take(1)
    world.frame(37.0, -21.0)
    world.latest_info.dx += 25.0  # the slide's measured move is 25 px off what the picture shows
    obj = run(tools.add_outline(world, capture, [[[300, 200], [340, 200], [340, 240], [300, 240]]], source="ai:test"))
    assert obj.track == "refinding" and obj.anchor_frame == 1  # unverified: kept, not drawn


def test_an_outline_whose_place_cannot_be_verified_is_kept_but_not_drawn():
    world = World()
    capture = world.captures.take(1)
    world.frame(0.0, 0.0, epoch=1)  # continuity lost since
    obj = run(tools.add_outline(world, capture, [[[300, 200], [340, 200], [340, 240], [300, 240]]], source="ai:test"))
    assert obj.track == "refinding"  # the studio draws only "tracking" outlines of the current epoch
    assert obj.anchor_frame == 1 and obj.template is not None  # the hub's check can still find it


def test_a_clear_then_a_new_request_then_the_old_requests_result_changes_nothing():
    async def go():
        world = World()
        world.gpu.delay = 0.05
        old = issue(world, "turn")
        with using(old):
            slow = asyncio.ensure_future(tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:look"))
        await asyncio.sleep(0.01)
        world.scene.clear("highlight")  # the user clears while it is being outlined
        new = issue(world, "turn")
        with using(new):
            fresh = await tools.outline_at(world, 1, 600, 300, box=(580, 280, 620, 320), source="ai:look")
        with pytest.raises(tools.ToolError) as late:
            await slow
        return world, fresh, late

    world, fresh, late = run(go())
    assert list(world.scene.objects) == [fresh.id]
    assert "superseded" in str(late.value) and "cleared" in str(late.value)


def test_a_newer_answer_replacing_the_apps_outlines_supersedes_an_older_request_but_not_itself():
    async def go():
        world = World()
        old, new = issue(world, "turn"), issue(world, "turn")
        world.scene.generations.bump("outline:app", why="a newer answer replaced the app's outlines", by=new)
        with using(new):
            mine = await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:look")
        with using(old), pytest.raises(tools.ToolError):
            await tools.outline_at(world, 1, 600, 300, box=(580, 280, 620, 320), source="ai:look")
        # The user's own click is another kind of outline: a replacement of the app's does not touch it.
        click = issue(world, "click")
        world.scene.generations.bump("outline:app", why="again")
        with using(click):
            theirs = await tools.outline_at(world, 1, 900, 300, box=(880, 280, 920, 320), source="click")
        return world, mine, theirs

    world, mine, theirs = run(go())
    assert set(world.scene.objects) == {mine.id, theirs.id}


def test_a_cancelled_request_and_another_session_change_nothing_on_screen():
    async def go():
        world = World()
        called_off, yesterday = issue(world, "hands"), issue(world, "turn")
        called_off.cancel("the user called it off")
        world.scene.generations.bump("session", why="another session was opened")
        out = []
        for request in (called_off, yesterday):
            with using(request):
                try:
                    await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:look")
                except tools.ToolError as exc:
                    out.append(str(exc))
        return world, out

    world, out = run(go())
    assert not world.scene.objects
    assert "called off" in out[0] and "another session" in out[1]


def test_the_cell_finders_view_is_held_however_long_it_takes(monkeypatch):
    """The 2026-10-06 crash: find by="cells" looked its frame up again after the cell finder's 6-8 s,
    the ring had dropped it, and `.jpeg` of None failed. Now the view is a capture held for the run."""
    from orchestrator.finder import Finder, FindRef

    world = World()
    world.settings.cells = "cellpose-sam"

    class Heavy(Gpu):
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=30.0, **payload):
            assert op == "cells"
            await asyncio.sleep(0.02)  # the cell finder's seconds: meanwhile the ring and the facts move on
            world.ring.clear()
            world.infos.clear()
            world.frame(0.0, 0.0, wall=world.latest_info.wall_at + 0.5)
            return {"polygons": [[[100, 100], [140, 100], [140, 140], [100, 140]],
                                 [[300, 100], [340, 100], [340, 140], [300, 140]]], "count": 2, "ms": 8000}

    world.gpu_heavy = Heavy()
    monkeypatch.setattr("orchestrator.options.resolve", lambda group, value: (value, ""))
    monkeypatch.setenv("MICROMIND_EDITION", "research")  # the cell finders are research-edition models

    async def ask(images, prompt, max_output_tokens=0):
        return SimpleNamespace(data={"numbers": [1, 2]})

    agent = SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask))
    ctx = SimpleNamespace(board=None, question="the cells", frame_id=None, objects=[])
    found = run(Finder(world, agent).find(FindRef("cell", quantity="all", by="cells"), ctx))
    assert len(found.objects) == 2 and not found.errors
    assert all(o.template is not None and o.origin == 1 for o in found.objects)  # re-findable, made on view 1


def test_the_outline_check_repairs_on_the_view_it_checked_after_the_outline_was_moved():
    from orchestrator.outline_check import OutlineCheck

    async def go():
        world = World()
        world.settings.outline_check, world.settings.fast = "fix", "gemini-3.5-flash"
        replies = [{"fits": "too_big", "box_2d": [250, 220, 350, 290]}, {"fits": "yes"}]

        async def ask(images, text, max_output_tokens=0):
            await asyncio.sleep(0.01)
            return SimpleNamespace(data=replies.pop(0) if replies else {"fits": "yes"})

        better = [[[300.0, 200.0], [360.0, 200.0], [360.0, 240.0], [300.0, 240.0]]]

        async def outline_again(capture, box, label, made_by=None):
            return better

        logged = []
        checker = OutlineCheck(world, SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask)), outline_again,
                               log=logged.append)
        request = issue(world, "turn")
        with using(request):
            obj = await tools.add_outline(world, world.captures.take(1), [[[250, 150], [450, 150], [450, 350], [250, 350]]],
                                          source="ai:look", label="stoma")
        # Re-anchoring moves it while the check runs (anchor and offset change; its origin does not).
        world.frame(15.0, 0.0)
        world.scene.update(obj.id, anchor_frame=2, anchor_offset=(15.0, 0.0),
                           polygons=[[[x + 15, y] for x, y in p] for p in obj.polygons])
        await until_async(lambda: not checker.tasks)
        request.seal()
        return world, obj, logged

    world, obj, logged = run(go())
    fixed = world.scene.objects[obj.id]
    assert logged[-1]["done"] == "replaced" and fixed.revision == 1
    # Written with the anchor it was made for (its capture), never mixed with another.
    shift = fixed.anchor_offset[0] - world.infos[1].dx
    assert fixed.polygons[0][0][0] == pytest.approx(300.0 + shift, abs=1.5) and fixed.origin == 1


def test_the_outline_check_changes_nothing_when_its_outline_was_removed_or_revised():
    from orchestrator.outline_check import OutlineCheck

    async def go(change):
        world = World()
        world.settings.outline_check, world.settings.fast = "fix", "gemini-3.5-flash"
        gate = asyncio.Event()
        replies = [{"fits": "too_big", "box_2d": [250, 220, 350, 290]}, {"fits": "yes"}]

        async def ask(images, text, max_output_tokens=0):
            await gate.wait()
            return SimpleNamespace(data=replies.pop(0))

        async def outline_again(capture, box, label, made_by=None):
            return [[[300.0, 200.0], [360.0, 200.0], [360.0, 240.0], [300.0, 240.0]]]

        logged = []
        checker = OutlineCheck(world, SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask)), outline_again,
                               log=logged.append)
        obj = await tools.add_outline(world, world.captures.take(1), [[[250, 150], [450, 150], [450, 350], [250, 350]]],
                                      source="ai:look", label="stoma")
        await asyncio.sleep(0.01)
        change(world, obj)
        gate.set()
        await until_async(lambda: not checker.tasks)
        return logged

    removed = run(go(lambda w, o: w.scene.remove(o.id)))
    revised = run(go(lambda w, o: w.scene.revise(o.id, polygons=o.polygons)))
    assert "removed" in removed[-1]["done"]
    assert "changed since" in revised[-1]["done"]


def test_after_a_failed_step_the_follower_resets_and_a_reply_from_before_changes_nothing():
    from orchestrator.objtrack import ObjectFollower

    async def go():
        world = World()
        world.settings.track = "objects"
        obj = await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="click")
        calls = []

        class Heavy:
            async def ensure_started(self):
                return self

            async def call(self, op, timeout=10.0, **payload):
                calls.append(payload)
                if len(calls) == 1:
                    raise TimeoutError("track timed out")
                if len(calls) == 2:
                    return {"objects": {obj.id: {"present": True, "polygons": [[[0, 0], [9, 0], [9, 9]]], "area": 81}},
                            "session": payload["session"] - 1, "ms": 5}  # a reply from before the reset
                return {"objects": {}, "session": payload["session"], "ms": 5}

        world.gpu_heavy = Heavy()
        logged = []
        follower = ObjectFollower(world, log=logged.append)
        with pytest.raises(TimeoutError):
            await follower.step()
        follower.resync("TimeoutError: track timed out")
        before = (follower.followed.copy(), follower.reset_next, world.scene.generations.value("track"))
        await follower.step()  # restarts from verified positions, resetting the worker's session
        moved = world.scene.objects[obj.id].polygons
        return calls, before, moved, obj, logged

    calls, before, moved, obj, logged = run(go())
    followed, reset_next, track_generation = before
    assert not followed and reset_next and track_generation == 1
    assert calls[1]["reset"] is True and calls[1]["start"] and calls[1]["session"] == 1
    assert moved == obj.polygons  # the stale reply moved nothing
    assert logged[0]["resync"] is True


def test_the_follower_follows_highlights_only():
    from orchestrator.objtrack import KINDS

    assert KINDS == ("highlight",)


def test_live_placement_needs_every_frame_since_registered():
    """Offsets carry over on a frame the motion tracker could not register, so the view may be
    elsewhere though the offsets say it has not moved (scaffolding review, M1.1 item 4a)."""
    world = World()
    capture = world.captures.take(1)
    world.frame(0.0, 0.0).tracked = False  # registration failed: the offset was carried over
    world.frame(0.0, 0.0)
    obj = run(tools.add_outline(world, capture, [[[300, 200], [340, 200], [340, 240], [300, 240]]], source="ai:test"))
    assert obj.track == "refinding"  # unverified: kept, not drawn, until the hub's check finds it by its pixels


def test_the_preconditions_refuse_a_changed_geometry_or_objective():
    from orchestrator.apply import preconditions

    world = World()
    capture = world.captures.take(1)
    now = world.frame(0.0, 0.0)
    assert preconditions(capture, now) is None
    assert "picture size" in preconditions(capture, SimpleNamespace(**{**vars(now), "width": now.width // 2}))
    capture_disc = SimpleNamespace(**{**vars(world.infos[1]), "disc": 400.0})
    assert "scale changed" in preconditions(capture_disc, SimpleNamespace(**{**vars(now), "disc": 404.0}))  # 1% > 0.5%
    assert preconditions(capture_disc, SimpleNamespace(**{**vars(now), "disc": 401.0})) is None  # 0.25%
    on_10x = SimpleNamespace(**{**vars(world.infos[1]), "scale": {"objective": "10"}})
    assert "objective" in preconditions(on_10x, now, objective="40")
    assert preconditions(on_10x, now, objective="10") is None


def test_a_name_given_or_confirmed_meanwhile_is_never_overwritten():
    """Renames and confirmations change an outline's revision; naming writes for the revision it was
    asked about (scaffolding review, M1.1 item 4b)."""
    from orchestrator.apply import apply

    world = World()
    obj = run(tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="click"))
    asked = obj.revision
    world.scene.update(obj.id, label="guard cell", status="confirmed")  # the user names it meanwhile
    done = run(apply(world, lambda _: world.scene.update(obj.id, label="model's name"), scope="outline:user",
                     target=(obj.id, asked)))
    assert not done.ok and world.scene.objects[obj.id].label == "guard cell"


def test_a_cancelled_request_writes_nothing():
    from orchestrator.apply import apply
    from orchestrator.ticket import issue

    world = World()
    obj = run(tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="click"))
    request = issue(world, "voice")
    request.cancel("never mind")
    done = run(apply(world, lambda _: world.scene.update(obj.id, label="late"), scope="outline:user",
                     ticket=request, target=(obj.id, obj.revision)))
    assert not done.ok and "called off" in done.reason



def test_a_late_result_never_reopens_a_magnifier_closed_since():
    from orchestrator.magnifier import Magnifier

    world = World()
    world.broadcast = lambda message: None
    magnifier = Magnifier(world)
    slow = issue(world, "turn")  # asked for a clearer picture, still working
    with using(issue(world, "command")):
        magnifier.close()  # the user closes the magnifier meanwhile
    with using(slow), pytest.raises(tools.ToolError):
        magnifier.show_picture(b"jpeg", "Averaged: 24 real frames")
    assert not magnifier.open
    with using(issue(world, "turn")):  # a request made after the close may open it
        magnifier.show_picture(b"jpeg", "Averaged: 24 real frames")
    assert magnifier.open


def test_cells_superseded_by_a_clear_in_between_add_nothing(monkeypatch):
    """outline_cells with a clear while the cell finder runs: its cells change nothing on screen."""
    world = World()
    world.settings.cells = "cellpose-sam"

    class Heavy:
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, **payload):
            world.scene.generations.bump_kind("cell", why="the cells were cleared")  # the user clears meanwhile
            return {"polygons": [[[300, 200], [340, 200], [340, 240], [300, 240]]]}

    world.gpu_heavy = Heavy()
    monkeypatch.setenv("MICROMIND_EDITION", "research")  # Cellpose-SAM runs only there
    with using(issue(world, "command")):
        population, shown = run(tools.outline_cells(world))
    # The population is kept (evidence of what ran), and nothing is shown over the live view.
    assert population.live() and not shown
    from orchestrator.population import layer_of

    assert layer_of(world).shown is None


def test_a_rejoin_carries_outlines_made_since_the_loss_over_unverified():
    """A return to a place seen before rejoins its epoch (motion.py, W5): a verified translation. An
    outline made in the epoch it leaves is carried over by it, and drawn again only once its own
    pixels match there (contract 3, "a rejoin")."""
    from orchestrator.hub import Hub
    from orchestrator.population import _ViewOf

    world = World()
    obj = run(tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="click"))
    world.scene.update(obj.id, anchor_epoch=3, anchor_offset=(10.0, 5.0))
    fake = SimpleNamespace(motion=SimpleNamespace(rejoins=[{"from": 3, "to": 0, "delta": (100.0, -20.0)}]),
                           scene=world.scene, _rejoins_seen=0, epoch_links={}, _rejoined={})
    Hub._carry_over(fake)
    moved = world.scene.objects[obj.id]
    assert (moved.anchor_epoch, moved.anchor_offset, moved.track) == (0, (110.0, -15.0), "refinding")
    population = SimpleNamespace(view={"epoch": 3, "dx": 1.0, "dy": 2.0}, scale_id=None)
    view = _ViewOf(population, fake.epoch_links)
    assert (view.epoch, view.dx, view.dy) == (0, 101.0, -18.0)
    Hub._carry_over(fake)  # each rejoin is carried over once
    assert world.scene.objects[obj.id].anchor_offset == (110.0, -15.0)


def test_an_outline_that_lands_after_a_rejoin_is_carried_over_too():
    """sim `arena`, flaky in full runs right after `footage-switch` (2026-10-07: "outline B measured while fully
    shown (0 samples)", made in epoch 2 with the view in epoch 1 throughout): the seek to the pan is a new epoch,
    the click is made on it, and motion confirms the place seen before (rejoins epoch 1) while an arm is still
    being segmented (SAM 2.1 tiny, 0.8-1 s). The arm already there was carried over and drawn within 2 px; the one
    that landed after the rejoin stayed anchored in the epoch left, which the view never came back to, and was
    never drawn. Whatever lands in an epoch a rejoin left is carried over by it too, unverified, and drawn once its
    own pixels match there (contract 3); nothing is carried out of the epoch the view is in."""
    from orchestrator.hub import Hub

    async def go():
        world = World()
        world.gpu.delay = 0.05  # the arm is still being segmented when the rejoin is seen
        made_on = world.frame(300.0, 300.0, epoch=2)  # the seek's new epoch: the click is made on its view
        hub = SimpleNamespace(motion=SimpleNamespace(rejoins=[]), scene=world.scene, _rejoins_seen=0,
                              epoch_links={}, _rejoined={}, latest_info=made_on)
        arm = asyncio.ensure_future(tools.outline_at(world, made_on.id, 600, 400, box=(580, 380, 620, 420),
                                                     source="click"))
        await asyncio.sleep(0.01)
        # A place seen before: motion rejoins epoch 1, where this view's offset is (0, 0), and the slide has moved
        # 10 px right and 5 px up since the click. The hub carries the rejoin over before the arm lands.
        hub.latest_info = world.frame(10.0, -5.0, epoch=1)
        hub.motion.rejoins.append({"from": 2, "to": 1, "delta": (-300.0, -300.0)})
        Hub._carry_over(hub)
        return world, hub, await arm

    world, hub, obj = run(go())
    assert (obj.anchor_epoch, obj.track) == (2, "refinding")  # made on the epoch left, it landed after the rejoin
    Hub._carry_over(hub)  # the next frame
    carried = world.scene.objects[obj.id]
    assert (carried.anchor_epoch, carried.anchor_offset, carried.track) == (1, (0.0, 0.0), "refinding")
    assert obj.id in hub._rejoined  # said as a rejoin once its own pixels match
    now = hub.latest_info
    gray = cv2.imdecode(np.frombuffer(world.ring[now.id], np.uint8), cv2.IMREAD_GRAYSCALE)
    changes = dict(Hub._reanchor(None, [carried], gray, now))
    assert changes[obj.id]["track"] == "tracking"  # its pixels are where the rejoin and the slide's move put it
    # Never out of the epoch the view is in: back in the epoch a rejoin left (a later return can rejoin it), an
    # outline made there stays where it was made.
    hub.latest_info = world.frame(300.0, 300.0, epoch=2)
    here = run(tools.outline_at(world, hub.latest_info.id, 600, 400, box=(580, 380, 620, 420), source="click"))
    Hub._carry_over(hub)
    assert (world.scene.objects[here.id].anchor_epoch, world.scene.objects[here.id].track) == (2, "tracking")


def _outline_in(world, epoch, offset=(0.0, 0.0), x=600):
    obj = run(tools.outline_at(world, world.latest_info.id, x, 400, box=(x - 20, 380, x + 20, 420), source="click"))
    world.scene.update(obj.id, anchor_epoch=epoch, anchor_offset=offset, track="tracking")
    return obj.id


def _carry_hub(world, links=None, rejoins=()):
    from orchestrator.hub import Hub

    hub = Hub.__new__(Hub)  # only what the carry needs
    hub.scene, hub.motion, hub._rejoins_seen = world.scene, SimpleNamespace(rejoins=list(rejoins)), 0
    hub.epoch_links, hub._rejoined, hub.latest_info = dict(links or {}), {}, world.latest_info
    return hub


def test_an_outline_check_that_writes_after_a_rejoin_is_carried_and_verified_by_its_new_pixels():
    """A repair resets the anchor to its capture after the rejoin already carried the original. The next
    frame must carry the repaired outline too, keeping it hidden until the repair's own template matches.
    Catches carrying only when a rejoin first arrives, and marking the carry tracking without a pixel check."""
    from orchestrator.hub import Hub
    from orchestrator.outline_check import OutlineCheck

    async def go():
        world = World()
        made_on = world.frame(300.0, 300.0, epoch=2)
        world.settings.outline_check, world.settings.fast = "fix", "gemini-3.5-flash"
        checking, finish = asyncio.Event(), asyncio.Event()
        replies = [{"fits": "too_big", "box_2d": [500, 450, 600, 500]}, {"fits": "yes"}]

        async def ask(images, text, max_output_tokens=0):
            checking.set()
            await finish.wait()
            return SimpleNamespace(data=replies.pop(0))

        better = [[[580.0, 380.0], [620.0, 380.0], [620.0, 420.0], [580.0, 420.0]]]

        async def outline_again(capture, box, label, made_by=None):
            return better

        logged = []
        checker = OutlineCheck(world, SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask)), outline_again,
                               log=logged.append)
        obj = await tools.add_outline(world, world.captures.take(made_on.id),
                                      [[[550, 350], [650, 350], [650, 450], [550, 450]]], source="ai:look", label="cell")
        await asyncio.wait_for(checking.wait(), HANG_S)
        world.frame(10.0, -5.0, epoch=1)
        hub = _carry_hub(world, rejoins=[{"from": 2, "to": 1, "delta": (-300.0, -300.0)}])
        hub._carry_over()
        assert (world.scene.objects[obj.id].anchor_epoch, world.scene.objects[obj.id].track) == (1, "refinding")
        gray = cv2.imdecode(np.frombuffer(world.ring[world.latest_info.id], np.uint8), cv2.IMREAD_GRAYSCALE)
        held_match = Hub._reanchor(None, [world.scene.objects[obj.id]], gray, world.latest_info)
        assert dict(held_match)[obj.id]["track"] == "tracking"  # the ORIGINAL outline's match is ready but not applied
        finish.set()
        await asyncio.wait_for(asyncio.gather(*checker.tasks), HANG_S)
        return world, hub, obj.id, better, logged, made_on.id, held_match

    world, hub, oid, better, logged, origin, held_match = run(go())
    repaired = world.scene.objects[oid]
    assert logged[-1]["done"] == "replaced" and repaired.revision == 1
    assert (repaired.anchor_epoch, repaired.anchor_offset, repaired.track) == (2, (300.0, 300.0), "refinding")
    assert repaired.origin == origin and repaired.origin_polygons == better
    assert repaired.template is not None  # the new outline, rather than the original, can now be re-found
    hub._carry_over()  # the repair landed after the rejoin was consumed
    carried = world.scene.objects[oid]
    assert (carried.anchor_epoch, carried.anchor_offset, carried.track) == (1, (0.0, 0.0), "refinding")
    now = world.latest_info
    heard = []
    hub.on_carry = [lambda *args: heard.append(args)]
    template, template_origin = carried.template, carried.template_origin
    Hub._apply_reanchor(hub, held_match, now)  # the original verifier returns AFTER the repair and its carry
    assert (carried.revision, carried.anchor_epoch, carried.anchor_offset, carried.track) == (1, 1, (0.0, 0.0), "refinding")
    assert carried.polygons == better and carried.template is template and carried.template_origin == template_origin
    assert not carried.shown and heard == [] and oid in hub._rejoined
    missing = dict(Hub._reanchor(None, [carried], np.zeros((H, W), np.uint8), now))
    assert missing.get(oid, {}).get("track", carried.track) == "refinding"
    gray = cv2.imdecode(np.frombuffer(world.ring[now.id], np.uint8), cv2.IMREAD_GRAYSCALE)
    found = Hub._reanchor(None, [carried], gray, now)
    assert dict(found)[oid]["track"] == "tracking"
    Hub._apply_reanchor(hub, found, now)  # only the repaired template's match may make it visible
    assert carried.shown and carried.revision == 1
    assert len(heard) == 1 and heard[0][:2] == (oid, "rejoin") and heard[0][2] >= 0.6
    assert oid not in hub._rejoined


@pytest.mark.parametrize("changed", ["revision", "anchor_frame", "anchor_epoch", "anchor_offset", "polygons",
                                     "template", "template_pixels", "template_origin", "track", "lost_since", "removed"])
def test_a_reanchor_result_is_refused_when_the_state_it_checked_changes(changed):
    """Even without a repair revision, following, carrying or replacing geometry invalidates the earlier match.
    A stale verifier must change neither the outline nor its carry provenance."""
    from orchestrator.hub import Hub

    world = World()
    oid = _outline_in(world, 0)
    world.scene.update(oid, track="refinding")
    obj = world.scene.objects[oid]
    gray = cv2.imdecode(np.frombuffer(world.ring[world.latest_info.id], np.uint8), cv2.IMREAD_GRAYSCALE)
    held_match = Hub._reanchor(None, [obj], gray, world.latest_info)
    assert dict(held_match)[oid]["track"] == "tracking"
    if changed == "revision":
        world.scene.revise(oid, polygons=obj.polygons)
    elif changed == "polygons":
        obj.polygons[0][0][0] += 1  # the snapshot must not alias mutable geometry
    elif changed == "template_pixels":
        obj.template.flat[0] ^= 255  # even an in-place template mutation cannot inherit an old match
    elif changed == "removed":
        world.scene.remove(oid)
    else:
        replacement = {"anchor_frame": obj.anchor_frame + 1, "anchor_epoch": 2, "anchor_offset": (1.0, 0.0),
                       "template": obj.template.copy(), "template_origin": (1, 1), "track": "lost", "lost_since": 123.0}
        world.scene.update(oid, **{changed: replacement[changed]})
    before = obj.to_wire()
    heard = []
    hub = _carry_hub(world)
    hub.on_carry = [lambda *args: heard.append(args)]
    hub._apply_reanchor(held_match, world.latest_info)
    assert obj.to_wire() == before and not obj.shown and heard == []


def test_a_queued_reanchor_matches_its_snapshot_even_if_the_live_outline_changes_before_it_starts():
    """Worker input is detached before queueing: the match and its output cannot mix old and repaired geometry."""
    from orchestrator.hub import Hub, _ReanchorSnapshot

    world = World()
    oid = _outline_in(world, 0)
    world.scene.update(oid, track="refinding")
    obj = world.scene.objects[oid]
    checked = _ReanchorSnapshot.take(obj)
    original = [[p[:] for p in poly] for poly in obj.polygons]
    obj.polygons[0][0][0] += 100
    obj.template[:] = 0
    gray = cv2.imdecode(np.frombuffer(world.ring[world.latest_info.id], np.uint8), cv2.IMREAD_GRAYSCALE)
    matched = Hub._reanchor(None, [checked], gray, world.latest_info)
    assert dict(matched)[oid]["track"] == "tracking"  # matched the snapshot's original pixels
    assert checked.object.polygons == original and checked.object.template.max() > 0
    hub = _carry_hub(world)
    hub.on_carry = []
    hub._apply_reanchor(matched, world.latest_info)
    assert not obj.shown and obj.polygons != original and obj.template.max() == 0


def test_a_chain_of_rejoins_is_followed_in_one_frame_up_to_the_epoch_the_view_is_in():
    """Review of #203, SF1: epoch 0 was rejoined by 1, which was rejoined by 2: an outline made in 0 is carried
    all the way to the epoch the view is in (2), with both translations, not one hop."""
    world = World()
    oid = _outline_in(world, 0, (1.0, 2.0))
    world.frame(0.0, 0.0, epoch=2)
    hub = _carry_hub(world, rejoins=[{"from": 0, "to": 1, "delta": (10.0, 0.0)}, {"from": 1, "to": 2, "delta": (0.0, 5.0)}])
    hub._carry_over()
    o = world.scene.objects[oid]
    assert (o.anchor_epoch, o.anchor_offset, o.track) == (2, (11.0, 7.0), "refinding")


def test_the_carry_stops_on_a_cycle_and_ends_where_the_walk_repeats():
    """Review of #203, SF1: links 1->2 and 2->1 (the view left an epoch and a later rejoin went back) never loop."""
    world = World()
    oid = _outline_in(world, 1)
    world.frame(0.0, 0.0, epoch=5)
    hub = _carry_hub(world, links={1: (2, (3.0, 0.0)), 2: (1, (-3.0, 0.0))})
    hub._carry_over()  # returns
    o = world.scene.objects[oid]
    assert o.anchor_epoch in (1, 2)
    assert o.anchor_offset in ((0.0, 0.0), (3.0, 0.0))


def test_a_rejoin_without_a_delta_is_not_a_link():
    """Review of #203, SF1: a rejoin that measured no translation carries nothing (and never stores None)."""
    world = World()
    oid = _outline_in(world, 1)
    world.frame(0.0, 0.0, epoch=0)
    hub = _carry_hub(world, rejoins=[{"from": 1, "to": 0, "delta": None}])
    hub._carry_over()
    assert hub.epoch_links == {} and world.scene.objects[oid].anchor_epoch == 1 and world.scene.objects[oid].track == "tracking"


def test_published_frames_carry_to_the_epoch_of_the_frame_just_published_through_the_real_hub():
    """Review of #203, SF1: the real Hub._publish. A rejoin motion has already seen (analysis runs ahead of
    publishing) is not applied to the frames of the old epoch still being published, and is from the first frame
    of the new one: the carry runs after latest_info is set."""
    from orchestrator.frames import Frame
    from orchestrator.hub import FrameInfo, Hub

    world = World()
    oid = _outline_in(world, 2, (4.0, 0.0))
    hub = Hub("http://bridge.invalid", use_gpu=False)
    hub.scene = world.scene
    hub.motion = SimpleNamespace(rejoins=[{"from": 2, "to": 1, "delta": (100.0, 0.0)}])

    def publish(n, epoch):
        info = FrameInfo(id=n, width=W, height=H, dx=0.0, dy=0.0, epoch=epoch, tracked=True, steady=True, fov=None,
                         wall_at=2000.0 + n)
        hub._publish(Frame(n, b"", 0.0, 2000.0 + n), info)

    publish(1, 2)  # still in the epoch the rejoin leaves: nothing is carried out of it
    assert world.scene.objects[oid].anchor_epoch == 2 and world.scene.objects[oid].track == "tracking"
    publish(2, 1)  # the first frame of the epoch rejoined
    o = world.scene.objects[oid]
    assert (o.anchor_epoch, o.anchor_offset, o.track) == (1, (104.0, 0.0), "refinding")
    late = _outline_in(world, 2)  # an arm that lands later, made on a view of the epoch left
    publish(3, 1)
    assert world.scene.objects[late].anchor_epoch == 1


def test_a_highlight_reuses_only_an_outline_that_is_drawn_and_verified():
    """Review of #203, SF2 (ADR 0006): a carried outline is not on the screen ("refinding"); a request for the same
    thing must make its own and not be told the unseen one is highlighted."""
    from orchestrator.brain.actions import outline_seen

    world = World()
    info = world.latest_info
    box = (580, 380, 620, 420)
    first = run(tools.outline_at(world, info.id, 600, 400, box=box, source="ai:sight", label="cell"))
    thing = SimpleNamespace(id="s1", name="cell", confidence=0.9)
    view = SimpleNamespace(things=[thing], box_now=lambda t, i: box)
    ctx = lambda: SimpleNamespace(aliases={}, seen_view=view, objects=[])  # noqa: E731
    assert world.scene.objects[first.id].kind == "highlight"
    assert run(outline_seen(world, lambda: view, "s1", ctx())) == first.id  # drawn and verified: reused
    world.scene.update(first.id, track="refinding")  # carried over by a rejoin, not drawn yet
    again = run(outline_seen(world, lambda: view, "s1", ctx()))
    assert again != first.id and world.scene.objects[again].track == "tracking"
