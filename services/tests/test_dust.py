"""Dust on the optics (orchestrator/dust.py): spots that stay put in the eyepiece while the slide
moves are found from views at different places, and told to the models as data (option DUST)."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator import dust
from orchestrator.agent import Agent
from orchestrator.coords import Crop
from orchestrator.fov import FieldOfView
from orchestrator.scene import SceneObject
from tests.fakes import Hub

FOV = FieldOfView(640.0, 360.0, 330.0, 1.0)
SPOT = (0.4, -0.3, 9.0, 0.25)  # u, v (disc radii), radius (px), depth


def slide(seed: int = 0) -> np.ndarray:
    """A textured slide much bigger than the view: blobs of different darkness on a bright ground."""
    rng = np.random.default_rng(seed)
    big = np.full((2400, 3200), 200, np.float32)
    for _ in range(1500):
        x, y, r = rng.uniform(0, 3200), rng.uniform(0, 2400), rng.uniform(6, 30)
        cv2.circle(big, (int(x), int(y)), int(r), float(rng.uniform(90, 230)), -1)
    return cv2.GaussianBlur(big, (0, 0), 2)


def view(big: np.ndarray, x: int, y: int, spots=(SPOT,), fov: FieldOfView = FOV) -> np.ndarray:
    """The camera frame with the slide at (x, y), the eyepiece disc, and dust fixed in the disc."""
    frame = big[y:y + 720, x:x + 1280].copy()
    yy, xx = np.mgrid[0:720, 0:1280].astype(np.float32)
    for u, v, radius, depth in spots:
        cx, cy = fov.cx + u * fov.radius, fov.cy + v * fov.radius
        frame *= 1 - depth * np.exp(-((xx - cx) ** 2 + (yy - cy) ** 2) / (2 * (radius / 2) ** 2))
    frame[np.hypot(xx - fov.cx, yy - fov.cy) > fov.radius] = 0
    return np.clip(frame, 0, 255).astype(np.uint8)


def positions(n: int, step: int = 90) -> list[tuple[int, int]]:
    return [(100 + (i % 6) * step * 2, 100 + (i // 6) * step * 2) for i in range(n)]


def aligned_views(n: int, spots=(SPOT,), seed: int = 0) -> list[np.ndarray]:
    big = slide(seed)
    return [dust.aligned(view(big, x, y, spots), FOV) for x, y in positions(n)]


def test_a_spot_that_stays_put_while_the_slide_moves_is_found_where_it_is():
    found = dust.find_spots(aligned_views(15))
    assert len(found) == 1
    spot = found[0]
    assert abs(spot.u - SPOT[0]) < 0.03 and abs(spot.v - SPOT[1]) < 0.03
    assert spot.depth > 0.1 and spot.seen >= 0.8 * spot.views and spot.views == 15


def test_a_clean_view_has_no_dust_and_few_views_say_nothing():
    assert dust.find_spots(aligned_views(15, spots=())) == []
    assert dust.find_spots(aligned_views(dust.MIN_VIEWS - 1)) == []


def test_a_dark_thing_on_the_slide_seen_in_a_few_views_is_not_dust():
    big = slide(1)
    views = []
    for i, (x, y) in enumerate(positions(15)):
        frame = view(big, x, y, spots=())
        if i < 5:  # a third of the views have a dark thing at the same eyepiece place (say, a still slide)
            cv2.circle(frame, (int(FOV.cx + 0.4 * FOV.radius), int(FOV.cy - 0.3 * FOV.radius)), 6, 40, -1)
        views.append(dust.aligned(frame, FOV))
    assert dust.find_spots(views) == []


def test_the_fact_names_boxes_on_image_one_and_says_when_the_outline_is_on_one():
    spot = dust.Spot(0.4, -0.3, 0.03, 0.2, 20, 22)
    crop = Crop(300, 20, 680, 680)
    text = dust.line([spot], FOV, crop)
    assert "22 views at different places on the slide" in text and "likely dust or smudges on the optics" in text
    assert "0-1000 of image 1" in text and "The outlined thing" not in text
    x0, y0, x1, y1 = spot.frame_box(FOV)
    assert "The outlined thing is at the place of one of these spots." in dust.line([spot], FOV, crop, (x0 - 5, y0 - 5, x1 + 5, y1 + 5))
    big_cell = (x0 - 100, y0 - 100, x1 + 100, y1 + 100)  # a cell with a speck over it is not the speck
    assert dust.spots_on([spot], FOV, big_cell) == [] and dust.line([], FOV, crop) == ""


def jpeg_source(frames: dict[int, np.ndarray]):
    encoded = {i: cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes() for i, f in frames.items()}
    return lambda i: SimpleNamespace(jpeg=encoded[i]) if i in encoded else None


def info(i: int, dx: float, dy: float, fov=FOV, epoch: int = 0, at: float | None = None):
    return SimpleNamespace(id=i, fov=fov, dx=dx, dy=dy, epoch=epoch, wall_at=at if at is not None else i * 0.5)


def test_the_map_keeps_views_only_when_the_slide_moved_and_measures_off_the_frame_path():
    big = slide(2)
    places = positions(14)
    frames = {i: view(big, x, y) for i, (x, y) in enumerate(places)}
    dm = dust.DustMap(jpeg_source(frames))
    for i, (x, y) in enumerate(places):
        dm.frame(info(i, -x, -y))
        dm.frame(info(i, -x - 3, -y, at=i * 0.5 + 0.3))  # the same place again: not kept
    assert len(dm.kept) == 14 and dm.spots == []  # nothing is decoded or measured on the frame path
    found = dm.estimate()
    assert len(found) == 1 and dm.ref is not None and dm.views == 14
    assert dm.wire()["spots"][0]["seen"] >= 11


def test_a_disc_that_moved_or_changed_size_starts_afresh():
    frames = {i: np.zeros((720, 1280), np.uint8) for i in range(4)}
    dm = dust.DustMap(jpeg_source(frames))
    dm.frame(info(0, 0, 0))
    dm.frame(info(1, -200, 0))
    assert len(dm.kept) == 2
    dm.frame(info(2, -400, 0, fov=FieldOfView(640, 360, 380, 1.0)))  # the phone zoomed
    assert [k["id"] for k in dm.kept] == [2]
    dm.frame(info(3, -600, 0, fov=FieldOfView(700, 360, 380, 1.0)))  # the phone shifted
    assert [k["id"] for k in dm.kept] == [3]


def agent_with_dust(tmp_path, setting: str):
    hub = Hub()
    hub.settings.dust = setting
    image = np.full((720, 1280, 3), 200, np.uint8)
    ok, jpeg = cv2.imencode(".jpg", image)
    hub.latest_info = SimpleNamespace(id=7, dx=0.0, dy=0.0, width=1280, height=720, epoch=0, fov=FOV, stats=())
    hub.infos = {7: hub.latest_info}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg.tobytes()) if fid == 7 else None, is_fresh=lambda: True)
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    agent.dust = dust.DustMap(lambda _i: None)
    agent.dust.spots, agent.dust.ref = [dust.Spot(0.4, -0.3, 0.02, 0.2, 20, 22)], FOV
    agent.keep_image = lambda *a, **k: None
    return hub, agent


def name_dust_click(tmp_path, setting: str):
    hub, agent = agent_with_dust(tmp_path, setting)
    x0, y0, x1, y1 = agent.dust.spots[0].frame_box(FOV)
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[x0 - 4, y0 - 4], [x1 + 4, y0 - 4], [x1 + 4, y1 + 4], [x0 - 4, y1 + 4]]],
                              anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click"))
    prompts, logged = [], []

    async def ask(images, prompt, max_output_tokens=0):
        prompts.append(prompt)
        return SimpleNamespace(data={"label": "dust speck", "group": "artefact", "answer": "A blurry dark speck.",
                                     "confidence": 0.9}, model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = logged.append
    out = asyncio.run(agent.identify("o1"))
    return prompts, logged, out


def test_naming_a_click_is_told_where_dust_is_and_the_model_names_it(tmp_path):
    prompts, logged, out = name_dust_click(tmp_path, "tell")
    assert "likely dust or smudges on the optics" in prompts[0]
    assert "The outlined thing is at the place of one of these spots." in prompts[0]
    assert out["label"] == "dust speck"  # the model's own name: code never names it dust
    assert next(r for r in logged if r["kind"] == "identify")["dust"]["spots"][0]["seen"] == 20


def test_with_dust_off_nothing_is_told(tmp_path):
    prompts, logged, _ = name_dust_click(tmp_path, "off")
    assert "dust" not in prompts[0].lower()
    assert next(r for r in logged if r["kind"] == "identify")["dust"] is None


def test_the_filter_option_drops_the_apps_outlines_on_dust_and_logs_it(tmp_path, monkeypatch):
    from orchestrator import agent as agent_module

    hub, agent = agent_with_dust(tmp_path, "filter")
    logged, outlined = [], []
    agent.log = logged.append

    async def outline_at(hub_, frame_id, x, y, box=None, **kw):
        outlined.append(box)
        return hub.scene.add(SceneObject(id=f"a{len(outlined)}", kind="highlight", polygons=[[[x, y], [x + 1, y], [x, y + 1]]],
                                         anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:x"))

    monkeypatch.setattr(agent_module.tools, "outline_at", outline_at)
    crop = Crop(310, 30, 660, 660)
    from orchestrator.coords import frame_box_to_gemini

    on_spot = frame_box_to_gemini(agent.dust.spots[0].frame_box(FOV), crop)
    targets = [{"box_2d": on_spot, "label": "dark granule"}, {"box_2d": [100, 100, 300, 300], "label": "cell"}]
    asyncio.run(agent.point(targets, 7, crop))
    assert len(outlined) == 1  # the cell; the granule on the dust spot is left out
    assert any(r["kind"] == "override" and r["rule"] == "dust-filter" for r in logged)
    hub.settings.dust = "tell"
    outlined.clear()
    asyncio.run(agent.point(targets, 7, crop))
    assert len(outlined) == 2  # told, not filtered: the model's targets stand


def test_the_running_look_is_told_and_only_the_filter_option_drops_a_thing_on_dust(tmp_path):
    from orchestrator.coords import frame_box_to_gemini
    from orchestrator.marks import fov_crop
    from orchestrator.sight import Sight

    hub, agent = agent_with_dust(tmp_path, "tell")
    crop = fov_crop(FOV, 1280, 720)
    on_spot = frame_box_to_gemini(agent.dust.spots[0].frame_box(FOV), crop)
    prompts, logged = [], []

    async def ask(images, prompt, max_output_tokens=0):
        prompts.append(prompt)
        return SimpleNamespace(data={"summary": "Leaf epidermis.", "specimen": "leaf", "focus": "sharp",
                                     "things": [{"name": "dark granule", "box_2d": on_spot},
                                                {"name": "stoma", "box_2d": [400, 400, 600, 600]}]},
                               model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = logged.append
    sight = Sight(hub, agent)
    seen = asyncio.run(sight.look("gemini-3.5-flash"))
    assert "likely dust or smudges on the optics" in prompts[0]
    assert [t.name for t in seen.things] == ["dark granule", "stoma"]  # told; the model's look stands
    hub.settings.dust = "filter"
    seen = asyncio.run(sight.look("gemini-3.5-flash"))
    assert [t.name for t in seen.things] == ["stoma"]
    assert any(r.get("rule") == "dust-filter" for r in logged)
