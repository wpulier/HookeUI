"""Name only what can be seen (orchestrator/quality.py, roadmap W1-1): blurry, empty, dark and
washed-out frames are flagged, the best frame of a moment is the one looked at, and the gate
stops Sight and naming a click from naming a bad view."""
import asyncio
from collections import OrderedDict
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator import quality as Q
from orchestrator.agent import Agent
from orchestrator.hub import FrameInfo, Hub
from orchestrator.options import Settings
from orchestrator.sight import Sight


def test_blur_is_judged_against_this_slides_own_peak():
    q = Q.Quality()
    assert q.flags(250, 140, 18, 0, epoch=0) == ()
    assert q.flags(120, 140, 17, 0, epoch=0) == ()  # half the peak: a 2 px blur, still fine
    assert q.flags(45, 140, 15, 0, epoch=0) == ("blurry",)  # a fifth: out of focus
    assert q.flags(45, 140, 15, 0, epoch=1) == ()  # the slide jumped: half the old peak counts
    assert q.flags(12, 140, 14, 0, epoch=2) == ("blurry",)  # jumped straight into a blur
    for _ in range(600):  # 20 s of a duller specimen in the same epoch: the old peak fades
        flags = q.flags(60, 140, 15, 0, epoch=1)
    assert flags == ()


def test_empty_dark_bright_and_glare():
    q = Q.Quality()
    assert q.flags(7, 150, 1.4, 0, epoch=0) == ("empty",)  # the blank disc of the calibration
    assert Q.Quality().flags(120, 20, 12, 0, epoch=0) == ("dark",)
    assert Q.Quality().flags(120, 240, 12, 0.6, epoch=0) == ("bright",)
    assert Q.Quality().flags(200, 160, 30, 0.4, epoch=0) == ("glare",)
    assert Q.unnameable(("glare", "blurry")) == ("blurry",)  # glare alone still lets it name
    assert Q.describe(("blurry", "dark")) == "out of focus and too dark"


def test_view_stats_on_pictures():
    rng = np.random.default_rng(0)
    textured = rng.integers(60, 200, (720, 1280), dtype=np.uint8)
    flat = np.full((720, 1280), 150, np.uint8)
    fov = SimpleNamespace(cx=640, cy=360, radius=330)
    assert Q.view_stats(textured, fov)[0] > 100 > Q.view_stats(flat, fov)[0]
    assert Q.view_stats(flat, fov)[1] == pytest.approx(150)


def info(fid, wall, focus, quality=(), steady=True, epoch=0):
    return FrameInfo(fid, 1280, 720, 0.0, 0.0, epoch, True, steady, None, wall, focus, wall, quality)


def frames_hub(infos):
    hub = Hub.__new__(Hub)
    hub.infos = OrderedDict((i.id, i) for i in infos)
    hub.latest_info = infos[-1]
    hub.source = SimpleNamespace(get=lambda fid: object() if fid in hub.infos else None)
    return hub


def test_best_frame_prefers_nameable_then_steady_then_sharp():
    hub = frames_hub([info(1, 9.8, 300), info(2, 10.5, 90, ("blurry",)), info(3, 10.6, 200, steady=False),
                      info(4, 10.8, 180), info(5, 11.0, 60, ("blurry",))])
    assert hub.best_frame(1.0).id == 4  # 1 is too old, 2 and 5 blurry, 3 moving
    assert hub.best_frame(0.1).id == 5  # nothing better in the window: the newest
    hub.latest_info = info(6, 11.1, 50, epoch=1)
    hub.infos[6] = hub.latest_info
    assert hub.best_frame(1.0).id == 6  # another epoch: positions differ, only its own frames


def test_with_the_gate_sight_says_what_is_wrong_instead_of_naming_a_bad_view():
    asked = []

    class Vision:
        async def ask(self, *a, **k):
            asked.append(a)
            return SimpleNamespace(data={}, latency_ms=1.0, error="")

    logged = []
    bad = info(9, 20.0, 30, ("blurry",))
    bad = FrameInfo(**{**bad.__dict__, "fov": SimpleNamespace(cx=640, cy=360, radius=300)})
    hub = frames_hub([bad])
    hub.settings, hub.scene = Settings(quality="gate"), SimpleNamespace(objects={}, context={})
    agent = SimpleNamespace(vision=lambda option: Vision(), log=logged.append)
    sight = Sight(hub, agent)
    view = asyncio.run(sight.look("gemini-3.8-flash"))
    assert view.summary == "Can't tell yet: the view is out of focus." and view.things == [] and not asked
    assert sight.recheck and logged == [{"kind": "sight-skip", "quality": ["blurry"], "frameId": 9}]
    asyncio.run(sight.look("gemini-3.8-flash"))
    assert len(logged) == 1  # the same trouble is logged once


def test_with_the_gate_naming_a_click_on_a_bad_view_says_what_is_wrong(tmp_path):
    hub = frames_hub([info(3, 5.0, 8, ("empty",))])
    hub.settings = Settings(quality="gate")
    updates = []
    obj = SimpleNamespace(anchor_frame=3)
    hub.scene = SimpleNamespace(objects={"o1": obj}, update=lambda oid, **kw: updates.append(kw), context={})
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=b"") if fid == 3 else None)
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    from orchestrator.tools import ToolError

    with pytest.raises(ToolError) as error:
        asyncio.run(agent.identify("o1"))
    assert "can't tell yet: the view is empty" in str(error.value)
    assert updates[-1] == {"status": "outline"}


def test_how_long_the_picture_is_unchanged_is_measured():
    content = Q.Content()
    still = np.zeros((64, 64), np.float32)
    noisy = still + 0.01  # a live camera: always a little different
    assert content.update(still, 1.0) == 1.0
    assert content.update(noisy, 1.1) == 1.1
    assert content.update(noisy, 1.2) == 1.1  # the same picture again
    assert content.update(noisy, 3.0) == 1.1


def test_a_still_slide_on_the_live_phone_counts_as_changing():
    """The iPhone on a still slide, measured live (2026-10-06): its picture differs by only
    0.0024-0.0066 a frame. That is a live picture; only an exact repeat is unchanged."""
    content = Q.Content()
    rng = np.random.default_rng(0)
    base = rng.uniform(80, 160, (64, 64)).astype(np.float32)
    t = 0.0
    content.update(base, t)
    for step in range(60):  # 2 s at 30 fps
        t += 1 / 30
        changed = content.update(base + np.float32(0.0024 if step % 2 else -0.0024), t)
    assert t - changed < 0.1
    for _ in range(60):
        t += 1 / 30
        changed_at = content.update(base + np.float32(0.0024), t)  # the very same picture, repeated
    assert t - changed_at > 1.5


def test_a_still_picture_is_never_frozen_or_refused(tmp_path):
    """A still picture is the normal state of a microscope: no flag, health stays live, and the
    brain looks at it (2026-10-06: a "frozen" state blanked the live view of a still slide)."""
    q = Q.Quality()
    assert q.flags(250, 140, 18, 0, epoch=0) == ()
    still = FrameInfo(1, 1280, 720, 0, 0, 0, True, True, None, 100.0, 200, 100.0, (), 1.0)  # unchanged for 99 s
    hub = Hub.__new__(Hub)
    hub.latest_info = still
    hub.source = SimpleNamespace(health=lambda: {"state": "live", "stateLabel": "Live frames are arriving"})
    hub.gpu, hub.gpu_error, hub.clients = None, None, set()
    hub.scene = SimpleNamespace(objects={}, version=0)
    hub.machine = SimpleNamespace(wire=dict)  # the performance meter's facts (machine.py)
    assert hub.health()["state"] == "live"


def test_not_sure_is_the_models_call_not_a_cut():
    from orchestrator.scene import UNSURE_BELOW, status_for

    assert status_for(0.6) == "likely" and status_for(None) == "likely"  # code never overrides the model
    assert status_for(0.85, cut=UNSURE_BELOW) == "unsure"  # a cut only when a bench asks for one


def test_by_default_the_model_is_told_the_measured_view_and_decides():
    """QUALITY "tell" (the default): a blurry view is still looked at, and the prompt carries the
    app's numbers (sharpness against this slide's best, brightness), never a verdict."""
    prompts = []

    class Vision:
        async def ask(self, images, prompt, *a, **k):
            prompts.append(prompt)
            return SimpleNamespace(data={"summary": "Soft view of plant cells.", "things": []}, latency_ms=1.0, error="")

    bad = info(9, 20.0, 30, ("blurry",))
    bad = FrameInfo(**{**bad.__dict__, "fov": SimpleNamespace(cx=640, cy=360, radius=300),
                       "stats": (20.0, 140.0, 18.0, 0.0), "peak": 200.0})
    hub = frames_hub([bad])
    import cv2
    jpeg = cv2.imencode(".jpg", np.full((720, 1280, 3), 140, np.uint8))[1].tobytes()
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg) if fid == 9 else None)
    hub.settings, hub.scene = Settings(), SimpleNamespace(objects={}, context={})
    assert hub.settings.quality == "tell"
    sight = Sight(hub, SimpleNamespace(vision=lambda option: Vision(), log=lambda record: None))
    view = asyncio.run(sight.look("gemini-3.8-flash"))
    assert prompts and "sharpness 10% of the sharpest this slide has looked recently" in prompts[0]
    assert "out of focus" not in prompts[0] and not view.summary.startswith("Can't tell")


def test_naming_shows_its_best_guess_marked_unsure_when_it_is_not_sure():
    from orchestrator.agent import identify_reading

    sure = identify_reading({"group": "white blood cell", "label": "neutrophil", "confidence": 0.95,
                             "alternatives": [{"name": "band neutrophil", "why": "looks similar"}]})
    assert sure["label"] == "neutrophil" and sure["status"] == "likely" and sure["maybe"] == []
    assert sure["alternatives"] == ["band neutrophil"]
    low = identify_reading({"group": "white blood cell", "label": "monocyte", "confidence": 0.8})
    assert low["label"] == "monocyte" and low["status"] == "likely"  # a low confidence alone is the model's to weigh
    unsure = identify_reading({"group": "white blood cell", "label": "monocyte", "confidence": 0.8, "unsure": True,
                               "alternatives": [{"name": "Lymphocyte", "why": "looks similar"}, "white blood cell"]})
    assert unsure["label"] == "monocyte" and unsure["status"] == "unsure" and unsure["group"] == "white blood cell"
    assert unsure["maybe"] == ["Lymphocyte"]  # neither the name nor the group itself is a "maybe"
    only_group = identify_reading({"group": "white blood cell", "label": "", "unsure": True})
    assert only_group["label"] == "white blood cell" and only_group["status"] == "unsure"
    said = identify_reading({"group": "", "label": "speck", "confidence": 0.95, "unsure": True})
    assert said["label"] == "speck" and said["status"] == "unsure"
    old_reply = identify_reading({"label": "stoma", "confidence": 0.92})  # the reply before W1-3b
    assert old_reply["label"] == "stoma" and old_reply["status"] == "likely"


def test_something_new_in_a_still_view_is_noticed():
    rng = np.random.default_rng(1)
    base = rng.integers(70, 190, (720, 1280)).astype(np.uint8)
    base = cv2_blur(base)
    fov = SimpleNamespace(cx=640, cy=360, radius=330)
    change = Q.StillChange()
    events = []
    for n in range(40):
        frame = base.copy()
        frame += rng.integers(0, 2, frame.shape, dtype=np.uint8)  # sensor noise
        if n >= 20:  # something swims in, 36 px across
            x = 500 + 6 * (n - 20)
            frame[340:376, x:x + 36] = 28
        events.append(change.update(frame, fov, True, 0, (0.0, 0.0), 200.0, n / 30))
    assert "content" not in events[:20] and "content" in events[20:]
    # A change of light alone is not new content.
    light = Q.StillChange()
    kinds = [light.update(np.clip(base.astype(int) + (25 if n >= 10 else 0), 0, 255).astype(np.uint8), fov, True, 0,
                          (0.0, 0.0), 200.0, n / 30) for n in range(20)]
    assert "light" in kinds and "content" not in kinds
    # Moving resets it: nothing is reported while the slide moves.
    assert change.update(base, fov, False, 0, (0.0, 0.0), 200.0, 2.0) is None


def cv2_blur(image):
    import cv2

    return cv2.GaussianBlur(image, (0, 0), 2)


def test_sight_looks_again_within_its_budget():
    logged = []
    sight = Sight(SimpleNamespace(clients={1}, scene=SimpleNamespace(objects={})), SimpleNamespace(log=logged.append))
    for _ in range(6):
        sight.recheck = False
        sight.still_changed("content", SimpleNamespace(id=1))
    assert [r["lookAgain"] for r in logged] == [True, True, True, True, False, False]
    sight.still_changed("light", SimpleNamespace(id=2))
    assert logged[-1]["lookAgain"] is False and "light" in logged[-1]["why"]


def test_the_close_up_of_a_small_click_is_enlarged():
    import cv2

    from orchestrator.agent import CLOSE_MIN_SIDE, identify_images
    from orchestrator.coords import Crop

    image = np.random.default_rng(3).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
    tiny = [[[640, 360], [650, 360], [650, 370], [640, 370]]]  # a 10 px socket
    _, close = identify_images(image, tiny, Crop(0, 0, 1280, 720), (640, 360, 650, 370))
    decoded = cv2.imdecode(np.frombuffer(close, np.uint8), cv2.IMREAD_COLOR)
    assert max(decoded.shape[:2]) == CLOSE_MIN_SIDE  # 3x10+60 = 90 px, enlarged
    big = [[[400, 200], [700, 200], [700, 500], [400, 500]]]
    _, close = identify_images(image, big, Crop(0, 0, 1280, 720), (400, 200, 700, 500))
    assert max(cv2.imdecode(np.frombuffer(close, np.uint8), cv2.IMREAD_COLOR).shape[:2]) <= 512  # still capped
