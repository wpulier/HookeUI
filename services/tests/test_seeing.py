"""Seeing better and snapshots through the tools (brain/toolbox/seeing.py), on a real hub fed
synthetic camera frames: the sweep buffer keeps a turn of the focus knob, `enhance` averages,
picks or stacks real frames and fails with what to do, and `snapshot` draws the outlines where the
studio does (moved by the slide offset, only in the frame's epoch) and saves the picture."""
import asyncio
import json
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.fov import FieldOfView
from orchestrator.frames import Frame
from orchestrator.hub import FrameInfo, Hub
from orchestrator.magnifier import Magnifier
from orchestrator.scale import Reading
from orchestrator.scene import SceneObject
from orchestrator.snapshot import OUTLINE_BGR
from orchestrator.sweep import MAX_FRAMES, SweepBuffer

T0 = 1_760_000_000.0
W, H = 320, 240


def picture(seed: int = 0, noise: float = 12.0, blur: float = 0.0) -> bytes:
    rng = np.random.default_rng(seed)
    base = np.full((H, W, 3), 70, np.uint8)
    cv2.circle(base, (160, 120), 40, (30, 140, 200), -1)
    image = base.astype(np.float32) + rng.normal(0, noise, base.shape)
    image = np.clip(image, 0, 255).astype(np.uint8)
    if blur:
        image = cv2.GaussianBlur(image, (0, 0), blur)
    return cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 95])[1].tobytes()


def make_hub() -> Hub:
    hub = Hub("http://127.0.0.1:1", use_gpu=False)
    hub.scale_reading = lambda: None
    return hub


def feed(hub: Hub, n: int, *, start: int = 1, steady=True, epoch: int = 0, dx=lambda i: 0.0, dy=lambda i: 0.0,
         focus=lambda i: 100.0, fov=None, listeners=(), fps: float = 30.0) -> list[FrameInfo]:
    """n frames into the hub's ring and frame records, as the camera and the analysis would."""
    infos = []
    for k in range(n):
        i = start + k
        at = T0 + i / fps
        hub.source.ring[i] = Frame(i, picture(i), 0.0, at)
        info = FrameInfo(i, W, H, dx(i), dy(i), epoch, True, steady(i) if callable(steady) else steady, fov, at,
                         focus(i))
        hub.infos[i] = info
        hub.latest_info = info
        for listener in listeners:
            listener(info)
        infos.append(info)
    return infos


def tools(hub, tmp_path, sweep=None):
    magnifier = Magnifier(hub)
    return build(Services(hub=hub, magnifier=magnifier, sweep=sweep, snapshots=tmp_path / "snapshots")), magnifier


def call(registry, name, args, question=""):
    async def go():
        run = registry.start(name, args, ToolContext("chat", 1, None, question))
        await run.task
        return run

    return asyncio.run(go())


# --- the sweep buffer -----------------------------------------------------------------------------
def test_the_sweep_buffer_keeps_one_turn_of_the_focus_knob():
    hub = make_hub()
    sweep = SweepBuffer(hub.source.get, clock=lambda: T0 + 30)
    # Still and sharpness flat: no sweep.
    feed(hub, 30, focus=lambda i: 100.0, listeners=[sweep.frame])
    assert sweep.latest() is None
    # Turning the knob: sharpness rises and falls over two seconds, then stays.
    curve = lambda i: 100.0 + 400.0 * np.sin(np.pi * min(1.0, (i - 31) / 60.0))
    feed(hub, 60, start=31, focus=curve, listeners=[sweep.frame])
    during = sweep.latest()
    assert during is not None and during.ended_at is None
    feed(hub, 120, start=91, focus=lambda i: 100.0, listeners=[sweep.frame])  # 4 s of nothing changing: it ended
    done = sweep.latest()
    assert done is not None and done.ended_at is not None
    ids = [f.id for f in done.frames]
    assert ids[0] < 45 and max(ids) >= 85  # from before it was noticed to the end of the turn
    gaps = np.diff([f.wall_at for f in done.frames])
    assert gaps.min() >= 0.9 / 15  # about 15 a second, not every frame
    assert all(f.jpeg is hub.source.ring[f.id].jpeg for f in done.frames)  # references, not copies
    assert len(done.frames) <= MAX_FRAMES


def test_a_sweep_is_forgotten_after_two_minutes_and_a_moving_slide_is_no_sweep():
    hub = make_hub()
    now = [T0]
    sweep = SweepBuffer(hub.source.get, clock=lambda: now[0])
    feed(hub, 60, focus=lambda i: 100.0 + 20.0 * i, listeners=[sweep.frame])
    feed(hub, 2, start=61, steady=False, listeners=[sweep.frame])  # the slide moved: the sweep is over
    assert sweep.latest() is not None
    now[0] = T0 + 2 + 121
    assert sweep.latest() is None
    moving = SweepBuffer(hub.source.get, clock=lambda: T0)
    feed(hub, 60, start=100, steady=False, focus=lambda i: 100.0 + 20.0 * i, listeners=[moving.frame])
    assert moving.latest() is None


def test_a_long_sweep_is_thinned_evenly():
    hub = make_hub()
    sweep = SweepBuffer(hub.source.get, clock=lambda: T0)
    feed(hub, 400, focus=lambda i: 100.0 + 300.0 * (i % 40) / 40.0, listeners=[sweep.frame])  # 13 s of turning
    kept = sweep.latest().frames
    assert MAX_FRAMES // 2 <= len(kept) <= MAX_FRAMES
    assert kept[0].id < 30 and kept[-1].id > 370  # still the whole turn


# --- enhance -----------------------------------------------------------------------------------
def test_enhance_averages_the_still_frames_and_shows_them(tmp_path):
    hub = make_hub()
    feed(hub, 40, dx=lambda i: float(i % 3), dy=lambda i: -float(i % 2), fov=FieldOfView(160, 120, 100, 1.0))
    registry, magnifier = tools(hub, tmp_path)
    run = call(registry, "enhance", {}, "can you clean up the picture?")
    assert run.ok, run.summary
    assert "averaged" in run.summary and "real frames" in run.summary and "magnifier" in run.summary
    assert magnifier.open and magnifier.sources[-1]["label"].startswith("Averaged: ")
    jpeg = magnifier.pictures[magnifier.sources[-1]["url"].rsplit("/", 1)[1]]
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and image.shape[0] <= 2 * 102 + 2  # cut to the eyepiece disc
    # Less noise than one frame: the flat surround's spread is smaller.
    one = cv2.imdecode(np.frombuffer(hub.source.ring[40].jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image[:30, :30].std() < 0.6 * one[20:50, 60:90].std()


def test_enhance_says_what_to_do_when_it_cannot(tmp_path):
    hub = make_hub()
    registry, _ = tools(hub, tmp_path)
    assert "no live picture" in call(registry, "enhance", {}).summary
    feed(hub, 40, steady=lambda i: i < 39)
    moving = call(registry, "enhance", {"how": "average"})
    assert moving.ok is False and "hold the slide still" in moving.summary
    no_sweep = call(registry, "enhance", {"how": "stack"})
    assert no_sweep.ok is False and "turn the fine focus slowly" in no_sweep.summary
    assert "not 'magic'" in call(registry, "enhance", {"how": "magic"}).summary


def test_enhance_sharpest_and_stack(tmp_path):
    hub = make_hub()
    sweep = SweepBuffer(hub.source.get, clock=lambda: T0 + 10)
    feed(hub, 90, focus=lambda i: 100.0 + 400.0 * np.sin(np.pi * i / 90.0), listeners=[sweep.frame])
    registry, magnifier = tools(hub, tmp_path, sweep=sweep)
    sharp = call(registry, "enhance", {"how": "sharpest"})
    assert sharp.ok and "sharpest of" in sharp.summary and magnifier.sources[-1]["label"].startswith("Sharpest: 1 real frame")
    stack = call(registry, "enhance", {"how": "stack"}, "stack the focus for me")
    assert stack.ok, stack.summary
    assert "focus-stacked" in stack.summary and magnifier.sources[-1]["label"].startswith("Focus stack: ")


def test_enhance_zooms_to_an_object(tmp_path):
    hub = make_hub()
    feed(hub, 40)
    obj = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[140, 100], [180, 100], [180, 140], [140, 140]]],
                                    anchor_frame=40, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="test", label="cell"))
    registry, magnifier = tools(hub, tmp_path)
    run = call(registry, "enhance", {"object": "o1"})
    assert run.ok, run.summary
    assert magnifier.object_id == obj.id
    jpeg = magnifier.pictures[magnifier.sources[-1]["url"].rsplit("/", 1)[1]]
    image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert image.shape[1] < W and image.shape[0] < H  # the object with room around it, not the whole view


# --- snapshot ----------------------------------------------------------------------------------
def outline(hub, oid, epoch=0, offset=(0.0, 0.0), label="stoma", box=(100, 80, 160, 140)):
    x0, y0, x1, y1 = box
    return hub.scene.add(SceneObject(id=oid, kind="highlight", polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                                     anchor_frame=1, anchor_epoch=epoch, anchor_offset=offset, source="test", label=label))


def is_pink(px) -> bool:
    b, g, r = (int(v) for v in px)
    pink = OUTLINE_BGR["pink"]
    return abs(b - pink[0]) < 60 and abs(g - pink[1]) < 60 and abs(r - pink[2]) < 60


def test_snapshot_draws_outlines_where_the_slide_moved_them_and_saves(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_HOME", str(tmp_path))
    hub = make_hub()
    hub.scale_reading = lambda: Reading(0.5, 160.0, 40, False)
    feed(hub, 10, dx=lambda i: 30.0, dy=lambda i: 10.0)  # the slide moved (30, 10) since the outline was made
    outline(hub, "o1")
    outline(hub, "o2", epoch=3, label="old")  # from before continuity was lost: not drawn anywhere
    magnifier = Magnifier(hub)
    registry = build(Services(hub=hub, magnifier=magnifier, day=SimpleNamespace(day="2026-10-06", sid="2026-10-06", date="2026-10-06")))
    run = call(registry, "snapshot", {}, "take a picture of this with the labels")
    assert run.ok, run.summary
    assert "1 outline" in run.summary and "their labels" in run.summary and "25 µm scale bar" in run.summary
    assert "1 being re-found" in run.summary
    folder = tmp_path / "snapshots" / "2026-10-06"
    pngs = list(folder.glob("snapshot-*.png"))
    # The file is one step away (the run's values, the index), not in the words (M1.5c review S7).
    assert len(pngs) == 1 and run.values.get("file") == str(pngs[0].relative_to(tmp_path))
    assert "saved with today's snapshots" in run.summary
    image = cv2.imread(str(pngs[0]))
    assert image.shape[:2] == (H, W)  # no eyepiece disc known: the whole frame
    assert is_pink(image[140, 130])  # the left edge, moved by (30, 10): x 100 -> 130, y 80..140 -> 90..150
    assert not is_pink(image[140, 100])  # not where it was drawn on its anchor frame
    meta = json.loads((folder / pngs[0].name.replace(".png", ".json")).read_text())
    assert meta["frameId"] in hub.infos and pngs[0].stem.endswith(f"-f{meta['frameId']}") and [o["id"] for o in meta["outlines"]] == ["o1"]
    assert meta["leftOut"] == {"being re-found": 1} and meta["scale"]["um"] == 25 and meta["scale"]["measured"] is False
    assert "real camera frame" in meta["made"]
    assert magnifier.open and magnifier.sources[-1]["label"].startswith("Snapshot: real frame")


def test_snapshot_without_labels_or_scale(tmp_path):
    hub = make_hub()
    feed(hub, 5)
    outline(hub, "o1")
    registry, magnifier = tools(hub, tmp_path)
    run = call(registry, "snapshot", {"labels": False})
    assert run.ok and "their labels" not in run.summary and "no scale bar" in run.summary
    assert list((tmp_path / "snapshots").rglob("*.png"))


def test_camera_only_snapshot_preserves_pixels_and_keeps_annotations_on_the_board(tmp_path):
    hub = make_hub()
    hub.scale_reading = lambda: Reading(0.5, 160.0, 40, False)
    feed(hub, 5, fov=FieldOfView(160, 120, 100, 1.0))
    obj = outline(hub, "o1")
    registry, magnifier = tools(hub, tmp_path)
    run = call(registry, "snapshot", {"overlays": False})
    assert run.ok, run.summary
    png = next((tmp_path / "snapshots").rglob("*.png"))
    meta = json.loads(png.with_suffix(".json").read_text())
    raw = cv2.imdecode(np.frombuffer(hub.source.get(meta["frameId"]).jpeg, np.uint8), cv2.IMREAD_COLOR)
    x0, y0, x1, y1 = meta["crop"]
    assert np.array_equal(cv2.imread(str(png)), raw[y0:y1, x0:x1])
    assert meta["overlays"] is False and meta["labels"] is False
    assert meta["outlines"] == [] and meta["scale"] is None
    assert "without overlays" in run.summary and "without overlays" in magnifier.sources[-1]["label"]
    assert hub.scene.objects["o1"] is obj


# --- a snapshot of what the user was looking at (live 2026-10-10 20:31: "a snapshot of the zoomed in eye") -----
def window_row(iid, source_key, *, content=(0, 0, 200, 150), affine=(1, 0, 0, 1, 0, 0), size=(200, 150)):
    return {"id": iid, "sourceKey": source_key, "open": True, "visible": True,
            "contentRect": list(content), "viewTransform": {"sourceToContent": list(affine), "originalSize": list(size)}}


def observation(rows, sources, focus=None):
    return {"items": rows, "sources": [{"key": key, "identity": identity} for key, identity in sources.items()],
            "interaction": {"lastContentFocus": {"presentationId": focus} if focus else None}}


def test_the_window_meant_is_the_one_showing_what_was_pointed_at_else_the_focused_else_the_only_one():
    from orchestrator.brain.toolbox.seeing import choose_window

    rows = [window_row("w-a", "live/o1"), window_row("w-b", "live/o3")]
    seen = observation(rows, {"live/o1": {"object": "o1"}, "live/o3": {"object": "o3"}}, focus="w-a")
    assert choose_window(seen, {"w-a", "w-b"}, pointed="o3")[0]["id"] == "w-b"
    assert choose_window(seen, {"w-a", "w-b"}, pointed=None)[0]["id"] == "w-a"  # the browser's focused one
    unfocused = observation(rows, {"live/o1": {"object": "o1"}, "live/o3": {"object": "o3"}})
    row, why = choose_window(unfocused, {"w-a", "w-b"}, pointed=None)
    assert row is None and "2 windows" in why
    assert choose_window(unfocused, {"w-a"}, pointed=None)[0]["id"] == "w-a"  # another browser's windows are not mine
    assert choose_window(unfocused, {"w-a", "w-b"}, pointed="o9", named=True) == (None, "")


def test_the_region_shown_follows_the_windows_zoom_and_pan():
    from orchestrator.brain.toolbox.seeing import shown_region

    crop = (100, 100, 300, 250)  # the window's source: this crop of the frame, delivered at 200 x 150
    assert shown_region(window_row("w", "k"), crop) == crop  # fitted: all of it
    # Zoomed 2x and panned to the middle: x' = 2x - 100, y' = 2y - 75 over a 200 x 150 content box.
    zoomed = window_row("w", "k", affine=(2, 0, 0, 2, -100, -75))
    assert shown_region(zoomed, crop) == (150, 137, 250, 212)
    assert shown_region({"id": "w"}, crop) == crop  # no transform reported: the crop as delivered


def test_a_snapshot_of_a_named_object_is_of_that_object_not_the_whole_view(tmp_path):
    hub = make_hub()
    feed(hub, 5)
    outline(hub, "o1", box=(100, 80, 160, 140))
    registry, _ = tools(hub, tmp_path)
    run = call(registry, "snapshot", {"object": "o1"}, "take a snapshot of the stoma")
    assert run.ok, run.summary
    png = next((tmp_path / "snapshots").rglob("*.png"))
    meta = json.loads(png.with_suffix(".json").read_text())
    x0, y0, x1, y1 = meta["crop"]
    assert x0 <= 100 and y0 <= 80 and x1 >= 160 and y1 >= 140  # all of it, with room around it
    assert (x1 - x0) < W and (y1 - y0) < H  # not the whole view
    assert cv2.imread(str(png)).shape[:2] == (y1 - y0, x1 - x0)


def test_without_windows_a_snapshot_of_it_is_of_what_the_magnifier_showed_when_asked(tmp_path):
    """No native windows, only the one magnifier (acting bench, live cases: "take a snapshot of it" with a stoma
    magnified took the whole view): what it showed when the user asked is what they were looking at."""
    from orchestrator.board import WhenAsked

    hub = make_hub()
    feed(hub, 5)
    outline(hub, "o1", box=(100, 80, 160, 140))
    registry, magnifier = tools(hub, tmp_path)
    magnifier.zoom("o1")

    async def go(asked):
        run = registry.start("snapshot", {}, ToolContext("chat", 1, None, "take a snapshot of it", when_asked=asked))
        await run.task
        return run

    run = asyncio.run(go(WhenAsked(at=0.0, magnified="o1")))
    assert run.ok, run.summary
    assert magnifier.sources[-1]["label"].startswith("Snapshot of ")
    x0, y0, x1, y1 = json.loads(next((tmp_path / "snapshots").rglob("*.json")).read_text())["crop"]
    assert (x1 - x0) < W and (y1 - y0) < H  # the stoma with room round it, not the whole view
    # Nothing magnified when asked: the view.
    whole = asyncio.run(go(WhenAsked(at=0.0)))
    assert whole.ok and magnifier.sources[-1]["label"].startswith("Snapshot: real frame")


def test_a_snapshot_of_a_live_window_is_its_shown_region_of_the_frame_held_when_asked_and_opens_beside_it(tmp_path):
    """The window the user was looking at when they asked, the frame held for the request (never a newer one),
    kept as a result by its own id and opened beside that window."""
    from orchestrator.brain.registry import CameraSelection
    from orchestrator.brain.toolbox import present
    from orchestrator.capture import Capture
    from orchestrator.magnifier import object_box_in, padded_rect

    hub = make_hub()
    infos = feed(hub, 5)
    obj = outline(hub, "o1", box=(100, 80, 160, 140))
    asked = infos[2]  # the frame held when the user asked; newer frames came after
    capture = Capture(asked.id, hub.source.get(asked.id).jpeg, asked)
    crop = padded_rect(object_box_in(obj, asked), .15, W, H)
    rows = [window_row("w-eye", "live/o1", affine=(2, 0, 0, 2, -(crop[2] - crop[0]) / 2, -(crop[3] - crop[1]) / 2),
                       size=(crop[2] - crop[0], crop[3] - crop[1]),
                       content=(0, 0, crop[2] - crop[0], crop[3] - crop[1]))]
    pin = SimpleNamespace(observation=SimpleNamespace(
        wire=lambda: observation(rows, {"live/o1": {"object": "o1"}}),
        client=SimpleNamespace(workspace_id="ws")))
    opened, kept = [], []

    async def native_present(services, ctx, action):
        opened.append(action)
        return "shown on screen (open, confirmed by the studio): saved-image capture d7 in window w-new"

    def from_run(run, ctx, hub_, produced=False):
        kept.append(run)
        run.resource = {"day": "2026-10-10", "id": "d7"}  # as the app's results writer records it on the run
        return run.resource

    original = present._native_present
    present._native_present = native_present
    hub.resources = SimpleNamespace(results=SimpleNamespace(from_run=from_run))
    try:
        magnifier = Magnifier(hub)
        services = Services(hub=hub, magnifier=magnifier, snapshots=tmp_path / "snapshots")
        services.presentations = SimpleNamespace(list=lambda workspace: [SimpleNamespace(id="w-eye")])
        registry = build(services)

        async def go():
            ctx = ToolContext("voice", 1, asked.id, "can you take a snapshot of it")
            ctx.client, ctx.request_snapshot = object(), pin
            ctx.camera_selection = CameraSelection(None, capture)
            run = registry.start("snapshot", {}, ctx)
            await run.task
            return run

        run = asyncio.run(go())
    finally:
        present._native_present = original
    assert run.ok, run.summary
    png = next((tmp_path / "snapshots").rglob("*.png"))
    meta = json.loads(png.with_suffix(".json").read_text())
    assert meta["frameId"] == asked.id  # the frame held when asked, not the newest
    x0, y0, x1, y1 = meta["crop"]
    assert (x1 - x0) <= (crop[2] - crop[0]) // 2 + 2 and (y1 - y0) <= (crop[3] - crop[1]) // 2 + 2  # the 2x zoomed part
    assert kept and opened and opened[0]["do"] == "open" and opened[0]["source"]["id"] == "d7"
    assert opened[0]["relation"] == {"kind": "beside", "reference": "w-eye"}
    assert "d7" in run.summary and "w-eye" in run.summary


def test_a_snapshot_whose_asked_frame_is_gone_says_so_and_takes_no_newer_one(tmp_path):
    from orchestrator.brain.registry import CameraSelection

    hub = make_hub()
    feed(hub, 5)
    outline(hub, "o1")
    registry, _ = tools(hub, tmp_path)

    async def go():
        ctx = ToolContext("chat", 1, None, "a snapshot of that")
        ctx.camera_selection = CameraSelection(3, None)  # asked on frame 3, which is no longer held
        run = registry.start("snapshot", {"object": "o1"}, ctx)
        await run.task
        return run

    run = asyncio.run(go())
    assert run.ok is False and "no longer kept" in run.summary
    assert not list((tmp_path / "snapshots").rglob("*.png"))


def test_repeated_saves_of_one_frame_keep_each_picture_retrievable(tmp_path):
    from orchestrator.resources import Index

    hub = make_hub()
    feed(hub, 5)
    outline(hub, "o1")
    registry, _ = tools(hub, tmp_path)
    first = call(registry, "snapshot", {})
    path = next(p for p in (tmp_path / "snapshots").rglob("*.png") if p.parent.name != "clean")
    annotated = path.read_bytes()
    second = call(registry, "snapshot", {"overlays": False})
    third = call(registry, "snapshot", {"overlays": False})
    assert all(run.ok for run in (first, second, third))
    assert len({run.values["file"] for run in (first, second, third)}) == 3
    assert path.read_bytes() == annotated
    index = Index(hub, snapshots=tmp_path / "snapshots")
    # Each snapshot's own picture; the clean copy of the one with outlines (clean/) is the library's, not the index's.
    assert len(list((tmp_path / "snapshots").rglob("clean/*.png"))) == 1
    for saved in (p for p in (tmp_path / "snapshots").rglob("*.png") if p.parent.name != "clean"):
        assert index.picture(saved.stem, day=saved.parent.name) == (saved.read_bytes(), "image/png")
    clean = [e for e in index.list(kind="snapshot", day=path.parent.name) if "without overlays" in e.summary]
    assert len(clean) == 2


def test_the_users_enhance_reserves_and_encodes_as_before(tmp_path, monkeypatch):
    """#186 review U16/U17: the user's own enhance is unchanged by a researcher's hands sharing its plans: the whole
    frame, never cut to the eyepiece, in the user's analysis allowance, and the same JPEG quality (94). What it
    reserves is its own measured peak by how it is made (finding 90: enhance.Plan.need; test_enhance holds it to the
    peak with tracemalloc)."""
    from orchestrator import enhance as methods
    from orchestrator.capture import captures

    hub = make_hub()
    sweep = SweepBuffer(hub.source.get, clock=lambda: T0 + 10)
    feed(hub, 90, focus=lambda i: 100.0 + 400.0 * np.sin(np.pi * i / 90.0), listeners=[sweep.frame],
         fov=FieldOfView(160, 120, 100, 1.0))
    registry, magnifier = tools(hub, tmp_path, sweep=sweep)
    reserved = []
    memory = captures(hub).memory
    real = memory.reserve
    monkeypatch.setattr(memory, "reserve", lambda cls, nbytes, why: (reserved.append((cls, nbytes)), real(cls, nbytes, why))[1])
    for how in ("average", "sharpest", "stack"):
        assert call(registry, "enhance", {"how": how}).ok, how
    whole = W * H  # never cut: its pixels worked on are the frame's, and each frame is decoded whole
    assert reserved == [("analysis", int((whole * methods.PER_PX[how] + whole * methods.DECODE_PX) * methods.MARGIN))
                        for how in ("average", "sharpest", "stack")]
    image = np.full((40, 40, 3), 90, np.uint8)
    cut = methods.jpeg_of(methods.Enhanced(image, 1, "x"), (0, 0, 40, 40))
    assert cut == cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 94])[1].tobytes()
