"""The slide map (orchestrator/slidemap.py, W5-4): steady views land where they are on the slide,
memory stays bounded, and where_is says where an outline is from the view, or that it can't tell."""
from __future__ import annotations

import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator import slidemap as sm
from orchestrator.brain import toolbox
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services
from orchestrator.scene import SceneObject
from orchestrator.slidemap import SCALE, Place, SlideMap, place, place_words
from tests.fakes import Hub

W, H, R = 1280, 720, 300.0
FOV = SimpleNamespace(cx=640.0, cy=360.0, radius=R)


def slide(seed: int = 3, size: int = 4000) -> np.ndarray:
    """A textured slide: smoothed noise with structure at many scales (so a misplaced paste shows)."""
    rng = np.random.default_rng(seed)
    out = np.zeros((size, size), np.float32)
    for k in (9, 31, 101):  # brightness at several scales (JPEG keeps it; colour detail it halves)
        noise = rng.random((size // k + 2, size // k + 2)).astype(np.float32)
        out += cv2.resize(noise, (size + 2 * k, size + 2 * k), interpolation=cv2.INTER_CUBIC)[k:k + size, k:k + size]
    gray = np.clip(out / 3, 0, 1)
    return (np.dstack([gray * 200 + 30, gray * 150 + 60, gray * 230 + 20])).astype(np.uint8)  # a stain's tint


def frame_at(world: np.ndarray, sx: int, sy: int) -> bytes:
    """The camera's picture with its top-left on slide px (sx, sy): the eyepiece disc, black around."""
    view = world[sy:sy + H, sx:sx + W].copy()
    mask = np.zeros((H, W), np.uint8)
    cv2.circle(mask, (int(FOV.cx), int(FOV.cy)), int(R), 255, -1)
    view[mask == 0] = 0
    return cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


class Feed:
    """Frames by id, and the infos the hub would publish for them."""

    def __init__(self) -> None:
        self.jpegs: dict[int, bytes] = {}
        self.n = 0

    def get(self, fid):
        return SimpleNamespace(jpeg=self.jpegs[fid]) if fid in self.jpegs else None

    def info(self, jpeg: bytes, sx: float, sy: float, epoch: int = 0, steady: bool = True, quality=()):
        # Frame px p shows slide px p + (sx, sy); the offset says content at p is at slide p - offset.
        self.n += 1
        self.jpegs[self.n] = jpeg
        return SimpleNamespace(id=self.n, dx=-sx, dy=-sy, epoch=epoch, steady=steady, fov=FOV, quality=quality)


def test_steady_frames_land_where_they_are_on_the_slide():
    world, feed, clock = slide(), Feed(), [0.0]
    m = SlideMap(feed.get, clock=lambda: clock[0], background=False)
    spots = [(1000, 1000), (1300, 1100), (1150, 1400)]
    for sx, sy in spots:
        m.frame(feed.info(frame_at(world, sx, sy), sx, sy))
    assert m.pasted == 3
    wire = m.wire()
    ox, oy = wire["origin"]
    w, h = wire["size"]
    got = cv2.imdecode(np.frombuffer(m.jpeg(), np.uint8), cv2.IMREAD_COLOR)
    assert got.shape[:2] == (h, w)
    # The picture the studio asks for is exactly the rectangle it was told, even after another
    # paste grew the map (the new paste shows only where it falls inside it).
    m.frame(feed.info(frame_at(world, 1600, 1000), 1600, 1000))
    told = cv2.imdecode(np.frombuffer(m.jpeg(0, (ox, oy, w, h)), np.uint8), cv2.IMREAD_COLOR)
    assert told.shape[:2] == (h, w) and m.wire()["size"] != [w, h]
    # Map px (u, v) shows slide px ((u + ox) / SCALE, (v + oy) / SCALE).
    small = cv2.resize(world, None, fx=SCALE, fy=SCALE, interpolation=cv2.INTER_AREA)
    # Pasted pixels, away from the black around them (JPEG blurs that edge).
    used = cv2.erode((got.sum(axis=2) > 40).astype(np.uint8), np.ones((5, 5), np.uint8)) > 0

    def error(shift: int) -> float:
        expect = small[oy + shift:oy + shift + h, ox:ox + w].astype(float)
        return float(np.abs(got.astype(float) - expect)[used].mean())

    assert error(0) < 6, error(0)  # JPEG twice and resampling: a few grey levels
    assert error(0) * 2.5 < error(2)  # two map px (8 camera px) off is clearly worse
    # The painted part spans the three discs: from the first's left edge to the second's right.
    left = (1000 + FOV.cx - R * sm.DISC_SHARE) * SCALE
    right = (1300 + FOV.cx + R * sm.DISC_SHARE) * SCALE
    assert abs(ox - left) <= 2 and abs(ox + w - right) <= 2


def test_only_steady_sharp_frames_that_moved_or_aged_are_pasted():
    world, feed, clock = slide(), Feed(), [0.0]
    m = SlideMap(feed.get, clock=lambda: clock[0], background=False)
    jpeg = frame_at(world, 1000, 1000)
    m.frame(feed.info(jpeg, 1000, 1000, steady=False))
    m.frame(feed.info(jpeg, 1000, 1000, quality=("blurry",)))
    assert m.pasted == 0
    m.frame(feed.info(jpeg, 1000, 1000))
    m.frame(feed.info(jpeg, 1010, 1000))  # moved 10 px: the same place
    assert m.pasted == 1
    clock[0] = sm.REFRESH_S + 0.1  # still there a while later: pasted again (the light may have changed)
    m.frame(feed.info(jpeg, 1010, 1000))
    m.frame(feed.info(frame_at(world, 1100, 1000), 1100, 1000))  # moved past MOVE_PX
    assert m.pasted == 3


def test_memory_is_bounded_and_the_oldest_epochs_go_first():
    world, feed, clock = slide(size=3000), Feed(), [0.0]
    m = SlideMap(feed.get, clock=lambda: clock[0], background=False)
    jpeg = frame_at(world, 100, 100)
    # One epoch wandering far across the slide (the same picture, at offsets 10 fields apart).
    for i in range(40):
        clock[0] += 1
        m.frame(feed.info(jpeg, i * 2000, (i % 3) * 1500, epoch=0))
    canvas = m.maps[0]
    assert max(canvas.size) <= sm.MAX_SIDE
    assert canvas.painted() is not None  # the newest view is kept
    # The newest paste is on the map; the first one was cut off.
    mx, my = int(39 * 2000 * SCALE + FOV.cx * SCALE) - canvas.ox, int(FOV.cy * SCALE) - canvas.oy
    assert 0 <= mx < canvas.size[0] and 0 <= my < canvas.size[1] and canvas.mask[my, mx] == 255
    for epoch in range(1, sm.EPOCHS_KEPT + 3):
        clock[0] += 1
        m.frame(feed.info(jpeg, 0, 0, epoch=epoch))
    assert len(m.maps) == sm.EPOCHS_KEPT
    assert list(m.maps) == list(range(3, sm.EPOCHS_KEPT + 3))
    total = sum(c.img.nbytes + c.mask.nbytes for c in m.maps.values())
    assert total <= sm.EPOCHS_KEPT * sm.MAX_SIDE ** 2 * 4


def test_a_view_that_rejoins_its_epoch_goes_on_with_its_map():
    world, feed, clock = slide(), Feed(), [0.0]
    m = SlideMap(feed.get, clock=lambda: clock[0], background=False)
    m.frame(feed.info(frame_at(world, 1000, 1000), 1000, 1000, epoch=0))
    clock[0] += 5
    m.frame(feed.info(frame_at(world, 2500, 2500), 2500, 2500, epoch=1))  # lost track: a map of its own
    clock[0] += 5
    m.frame(feed.info(frame_at(world, 1400, 1000), 1400, 1000, epoch=0))  # recognised: back on epoch 0's map
    assert set(m.maps) == {0, 1}
    assert m.wire()["epoch"] == 0 and m.wire()["size"][0] > 2 * R * sm.DISC_SHARE * SCALE + 50


def test_the_panel_is_announced_when_opened_and_then_at_most_once_a_second():
    world, feed, clock, sent = slide(), Feed(), [0.0], []
    m = SlideMap(feed.get, sent.append, clock=lambda: clock[0], background=False)
    m.set_open(True)
    assert sent[-1]["open"] is True and sent[-1]["url"] is None  # nothing seen yet
    m.frame(feed.info(frame_at(world, 1000, 1000), 1000, 1000))
    m.frame(feed.info(frame_at(world, 1100, 1000), 1100, 1000))
    clock[0] = 1.5
    m.frame(feed.info(frame_at(world, 1100, 1000), 1100, 1000))
    assert sent[-1]["url"].startswith("/api/slidemap.jpg?epoch=0&x=") and len(sent) <= 3
    m.set_open(False)
    assert sent[-1]["open"] is False


# --- where a thing is --------------------------------------------------------------------------
def outline(hub, label, cx, cy, epoch=0, offset=(0.0, 0.0), created=0.0):
    obj = SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=[[[cx - 10, cy - 10], [cx + 10, cy - 10], [cx, cy + 10]]],
                      anchor_frame=1, anchor_epoch=epoch, anchor_offset=offset, source="click", label=label,
                      created_at=created)
    obj.polygons = [[[cx - 10, cy - 10], [cx + 10, cy - 10], [cx + 10, cy + 10], [cx - 10, cy + 10]]]
    return hub.scene.add(obj)


def test_place_words():
    assert place_words(Place(-500, -500, 1.18, False)) == "about 1.2 fields up and to the left of the view"
    assert place_words(Place(600, 0, 1.0, False)) == "about 1.0 field to the right of the view"
    assert place_words(Place(0, 900, 1.5, False), um_per_px=0.5) == "about 1.5 fields below the view (about 450 µm)"
    assert place_words(Place(5, -3, 0.01, True)) == "in view now, near the centre"
    assert place_words(Place(-200, 10, 0.33, True)) == "in view now, toward the left of the view"


def test_where_an_outline_is_after_the_slide_moved():
    hub = Hub()
    stoma = outline(hub, "stoma", 640, 360)  # outlined in the middle of the view at offset (0, 0)
    # The slide moved: content moved 600 px right and 600 px down, so the stoma is up and left of the view.
    hub.latest_info = SimpleNamespace(id=9, dx=-600.0, dy=-600.0, epoch=0, fov=FOV, width=W, height=H)
    p = place(stoma, hub.latest_info)
    assert (round(p.dx), round(p.dy)) == (-600, -600) and not p.in_view and abs(p.fields - 1.41) < 0.01
    hub.latest_info = SimpleNamespace(id=9, dx=0.0, dy=0.0, epoch=1, fov=FOV, width=W, height=H)
    assert place(stoma, hub.latest_info) is None  # another epoch: can't be told


def run(registry, name, args):
    async def go():
        run_ = registry.start(name, args, ToolContext("chat", 1, 9))
        await run_.task
        return run_

    return asyncio.run(go())


def tools(hub, slidemap=None):
    return toolbox.build(Services(hub=hub, slidemap=slidemap))


def test_where_is_answers_from_the_offsets_and_says_when_it_cannot():
    hub = Hub()
    outline(hub, "stoma", 640, 360)
    registry = tools(hub)
    assert "show_map" not in registry.tools  # no map, no map panel; where_is needs only the scene
    hub.latest_info = SimpleNamespace(id=9, dx=-600.0, dy=-600.0, epoch=0, fov=FOV, width=W, height=H)
    r = run(registry, "where_is", {"object": "stoma"})
    assert r.ok and r.summary.startswith("the stoma (o1) is about 1.4 fields up and to the left of the view."), r.summary
    hub.scale_reading = lambda: SimpleNamespace(um_per_px=0.69)
    assert "(about 590 µm)" in run(registry, "where_is", {"object": "o1"}).summary
    hub.latest_info = SimpleNamespace(id=9, dx=0.0, dy=0.0, epoch=2, fov=FOV, width=W, height=H)
    r = run(registry, "where_is", {"object": "stoma"})
    assert r.ok and "can't tell from here" in r.summary
    assert r.values['place']['places'] == [{'objectId': 'o1', 'label': 'stoma', 'known': False}]
    assert r.values['place']['measuredOn']['frameId'] == 9
    hub.latest_info = SimpleNamespace(id=9, dx=0.0, dy=0.0, epoch=2, fov=None, width=W, height=H)
    r = run(registry, "where_is", {"object": "stoma"})
    assert r.ok is False and "eyepiece" in r.summary


def test_where_is_lists_every_outline_a_name_means_nearest_first():
    hub = Hub()
    outline(hub, "stoma", 640 + 900, 360, created=1)  # o1: 1.5 fields right
    outline(hub, "stoma", 640, 360 - 200, created=2)  # o2: in view, toward the top
    outline(hub, "stoma", 640, 360, epoch=5, created=3)  # o3: another epoch
    hub.latest_info = SimpleNamespace(id=9, dx=0.0, dy=0.0, epoch=0, fov=FOV, width=W, height=H)
    summary = run(tools(hub), "where_is", {"object": "stoma"}).summary
    assert summary.index("(o2) is in view now, toward the top") < summary.index("(o1) is about 1.5 fields to the right") \
        < summary.index("(o3): can't tell"), summary


def test_show_map_opens_the_panel_and_says_what_is_on_it():
    hub, sent = Hub(), []
    m = SlideMap(lambda fid: None, sent.append, background=False)
    registry = tools(hub, m)
    r = run(registry, "show_map", {})
    assert r.ok and m.open and "empty so far" in r.summary and sent[-1]["open"] is True
    world, feed = slide(), Feed()
    m.get_frame = feed.get
    m.frame(feed.info(frame_at(world, 1000, 1000), 1000, 1000))
    assert "the still views seen so far" in run(registry, "show_map", {}).summary
    r = run(registry, "show_map", {"open": False})
    assert r.ok and not m.open and sent[-1]["open"] is False
