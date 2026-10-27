"""Pointing "molmo" (orchestrator/molmo.py, gpu_worker/molmo.py): Molmo 2 4B points at what the user
asked about, and each point is outlined as a click. The vision model still answers and says whether
anything is pointed at and what it is called; Molmo says where. Stand-in models and GPU here (no
model is loaded); bench/grounding.py and bench/find_bench.py measure the real one."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from gpu_worker.molmo import causal_mask, finished, parse_points, view_of
from orchestrator.agent import Agent, target_box
from orchestrator.coords import Crop
from orchestrator.fov import FieldOfView
from orchestrator.molmo import MolmoPointer, Read, assign, query_for
from tests.fakes import Hub

W, H = 1280, 720


def square(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]


def info(id=7, dx=0.0, dy=0.0, epoch=0, fov=None, steady=True):
    return SimpleNamespace(id=id, dx=dx, dy=dy, epoch=epoch, fov=fov, width=W, height=H, steady=steady)


def box_2d(x0, y0, x1, y1, crop):
    return [(y0 - crop.y) / crop.height * 1000, (x0 - crop.x) / crop.width * 1000,
            (y1 - crop.y) / crop.height * 1000, (x1 - crop.x) / crop.width * 1000]


# --- reading Molmo's answer (pure) -----------------------------------------------------------------
def test_points_are_read_in_the_pictures_pixels():
    assert parse_points('<points coords="1 1 696 548">oval</points>', 1000, 500) == [[696.0, 274.0]]
    many = parse_points('<points coords="1 1 100 200 2 300 400 3 0500 0600">specks</points>', 1000, 1000)
    assert many == [[100.0, 200.0], [300.0, 400.0], [500.0, 600.0]]


def test_no_points_other_images_and_repeats_give_nothing_extra():
    assert parse_points("There are none in this image.", 100, 100) == []
    assert parse_points('<points coords="2 1 100 200">', 100, 100) == []  # another image's points
    assert parse_points('<points coords="1 1 100 200 2 100 200">', 1000, 1000) == [[100.0, 200.0]]
    assert parse_points('<points coords="1 1 1200 200">', 1000, 1000) == []  # outside the picture
    assert parse_points("", 10, 10) == [] and parse_points(None, 10, 10) == []


def test_an_answer_cut_off_by_the_word_limit_keeps_its_whole_points():
    cut = '<points coords="1 1 039 302 2 062 546 3 062 7'  # many red cells, stopped mid-point
    assert parse_points(cut, 1000, 1000) == [[39.0, 302.0], [62.0, 546.0]]


def test_generation_stops_once_the_points_are_written():
    assert not finished('<points coords="1 1 69')
    assert finished('<points coords="1 1 696 548"')
    assert finished('<points coords="1 1 696 548">oval')
    assert not finished("There are")


def test_the_view_is_cut_to_the_eyepiece_and_its_offset_kept():
    frame = np.zeros((100, 200, 3), np.uint8)
    frame[10:20, 30:40] = (0, 0, 255)  # red in BGR
    jpeg = cv2.imencode(".png", frame)[1].tobytes()
    rgb, offset = view_of({"jpeg": jpeg, "crop": [30, 10, 50, 40]})
    assert offset == (30, 10) and rgb.shape == (40, 50, 3)
    assert tuple(rgb[0, 0]) == (255, 0, 0)  # RGB now
    with pytest.raises(ValueError):
        view_of({"jpeg": b"not a picture"})


def test_the_attention_mask_is_causal_with_the_image_seeing_itself():
    torch = pytest.importorskip("torch")
    embeds = torch.zeros((1, 4, 8))
    image = torch.tensor([[0, 1, 1, 0]])

    def both_ways(b, h, q, k):  # what the model's code passes: image tokens see each other
        safe = torch.where(k < image.shape[1], k, 0)
        return (image[b, q] == 1) & (image[b, safe] == 1)

    mask = causal_mask(input_embeds=embeds, attention_mask=torch.ones((1, 4)), cache_position=torch.arange(4),
                       or_mask_function=both_ways)
    assert mask.shape == (1, 1, 4, 4)
    assert mask[0, 0].tolist() == [[True, False, False, False],
                                   [True, True, True, False],  # image token 1 sees image token 2
                                   [True, True, True, False],
                                   [True, True, True, True]]
    # Reading on from a cache: the new token sees everything before it.
    step = causal_mask(input_embeds=torch.zeros((1, 1, 8)), attention_mask=torch.ones((1, 5)), cache_position=torch.tensor([4]))
    assert step.shape == (1, 1, 1, 5) and bool(step.all())
    assert causal_mask(input_embeds=embeds, attention_mask=mask) is mask  # one already made stays as it is


# --- what Molmo is asked, and whose label a point takes (pure) -------------------------------------
def test_molmo_is_asked_about_the_users_own_words():
    assert query_for("What's the oval on the right?") == 'Point to what this request is about: "What\'s the oval on the right?"'
    assert query_for('say "hi"  there') == "Point to what this request is about: \"say 'hi' there\""
    assert query_for("What's that?", how="question") == "What's that?"


def test_a_point_takes_the_label_of_the_box_that_holds_it_else_the_nearest():
    crop = Crop(0, 0, 1000, 1000)
    targets = [{"box_2d": box_2d(100, 100, 300, 300, crop), "label": "stoma", "confidence": 0.8},
               {"box_2d": box_2d(150, 150, 200, 200, crop), "label": "nucleus", "confidence": 0.6},
               {"box_2d": box_2d(700, 700, 800, 800, crop), "label": "hair", "confidence": 0.7}]
    out = assign([[160, 170], [250, 250], [600, 650]], targets, crop)
    assert [t["label"] for t in out] == ["nucleus", "stoma", "hair"]  # the smallest box holding it; else the nearest
    assert [t["inBox"] for t in out] == [True, True, False]
    assert out[0]["point"] == [160.0, 170.0] and out[0]["confidence"] == 0.6
    assert "box_2d" in out[0] and "box_2d" not in out[2]  # a point outside every box keeps none


def test_the_model_decides_whether_anything_is_pointed_at():
    assert assign([[10, 10]], [], Crop(0, 0, 100, 100)) == []
    out = assign([[10, 10]], [{"label": "speck"}], Crop(0, 0, 100, 100))  # a target with no box
    assert out == [{"label": "speck", "confidence": None, "point": [10.0, 10.0], "inBox": False}]


def test_a_point_without_a_box_is_matched_as_a_small_box_around_it():
    crop = Crop(0, 0, 1000, 1000)
    assert target_box({"point": [100.0, 200.0], "label": "x"}, crop) == (88.0, 188.0, 112.0, 212.0)
    with_box = {"point": [100.0, 200.0], "box_2d": box_2d(50, 150, 150, 250, crop)}
    assert target_box(with_box, crop) == pytest.approx((50, 150, 150, 250))


# --- the look and its outlines ---------------------------------------------------------------------
class Gpu:
    def __init__(self, points=()):
        self.calls, self.points, self.ready = [], [list(p) for p in points], True

    async def call(self, op, timeout=30.0, **payload):
        self.calls.append((op, payload))
        if op == "molmo_read":
            return {"ok": True, "ms": 2500.0}
        if op == "molmo_point":
            return {"ok": True, "points": self.points, "text": "<points coords=...>", "tokens": 16, "read": 14, "ms": 900.0}
        x, y = payload.get("points", [[420, 420]])[0]
        return {"ok": True, "polygons": square(x - 20, y - 20, x + 20, y + 20), "score": 0.9, "area": 1600, "ms": 20.0}


def make_agent(tmp_path, answer, points=()):
    hub = Hub()
    fov = FieldOfView(640, 360, 340, 1.0)
    frame = info(fov=fov)
    hub.latest_info, hub.infos = frame, {7: frame}
    hub.best_frame = lambda window: frame
    hub.gray = lambda fid: None
    hub.candidates_in = lambda fid: []
    hub.gpu, hub.gpu_molmo = Gpu(), Gpu(points)
    hub.settings.pointing = "molmo"
    jpeg = cv2.imencode(".jpg", np.full((H, W, 3), 90, np.uint8))[1].tobytes()
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg) if fid in hub.infos else None, is_fresh=lambda: True)
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")

    async def client(_hub):
        return hub.gpu_molmo

    logged = []
    agent.log = logged.append
    agent.molmo = MolmoPointer(hub, log=agent.log, client=client)
    calls = []

    async def ask(images, prompt, max_output_tokens=0):
        calls.append((len(images), prompt))
        return SimpleNamespace(data=answer, model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    return hub, agent, calls, logged


def test_molmos_points_are_outlined_as_clicks_with_the_models_labels(tmp_path):
    hub, agent, calls, logged = make_agent(tmp_path, None, points=[(640, 380)])
    view = agent.snapshot(7)
    crop = view[2]
    answer = {"answer": "That is likely a stoma.", "specimen": "leaf",
              "targets": [{"box_2d": box_2d(600, 300, 700, 400, crop), "label": "stoma", "confidence": 0.8}]}
    agent.vision = lambda _o: SimpleNamespace(ask=lambda images, prompt, **kw: asyncio.sleep(0, SimpleNamespace(
        data=dict(answer), model="stand-in", latency_ms=5.0, error=None)))

    async def run():
        data, used = await agent.look("what's the oval in the middle?", view, option="gemini-3.5-flash", pointing="molmo")
        return data, await agent.point(data["targets"], 7, crop, used["marks"], used["model"])

    data, objects = asyncio.run(run())
    op, payload = hub.gpu_molmo.calls[0]
    assert op == "molmo_point" and payload["query"] == query_for("what's the oval in the middle?")
    assert payload["crop"] == [crop.x, crop.y, crop.width, crop.height]  # the eyepiece, not the black around it
    assert data["targets"] == [{"label": "stoma", "confidence": 0.8, "point": [640.0, 380.0], "inBox": True,
                                "box_2d": answer["targets"][0]["box_2d"]}]
    assert data["modelTargets"] == answer["targets"]  # the model's own boxes are kept beside them
    segment = [p for op, p in hub.gpu.calls if op == "segment"]
    assert len(segment) == 1 and segment[0]["points"] == [[640.0, 380.0]] and segment[0].get("box") is None  # a click
    assert len(objects) == 1 and objects[0].label == "stoma" and objects[0].status == "likely"
    record = next(r for r in logged if r["kind"] == "molmo-point")
    assert record["used"] == "points" and record["inBox"] == [True] and record["points"] == [[640, 380]]


def test_when_molmo_points_at_nothing_the_models_boxes_are_outlined_and_it_says_so(tmp_path):
    hub, agent, calls, logged = make_agent(tmp_path, None, points=[])
    view = agent.snapshot(7)
    targets = [{"box_2d": box_2d(600, 300, 700, 400, view[2]), "label": "stoma"}]
    agent.vision = lambda _o: SimpleNamespace(ask=lambda images, prompt, **kw: asyncio.sleep(0, SimpleNamespace(
        data={"answer": "A stoma.", "targets": targets}, model="stand-in", latency_ms=5.0, error=None)))
    data, used = asyncio.run(agent.look("what's that?", view, option="gemini-3.5-flash", pointing="molmo"))
    assert data["targets"] == targets
    assert logged[-1]["kind"] == "molmo-none" and "boxes are outlined" in logged[-1]["why"]


def test_nothing_is_pointed_at_when_the_model_meant_nothing(tmp_path):
    hub, agent, calls, logged = make_agent(tmp_path, {"answer": "Closed.", "targets": []}, points=[(640, 380)])
    view = agent.snapshot(7)
    data, used = asyncio.run(agent.look("close the magnifier", view, option="gemini-3.5-flash", pointing="molmo"))
    assert data["targets"] == []
    assert logged[-1]["kind"] == "molmo-point" and logged[-1]["used"] == "none"


def test_a_molmo_that_fails_or_is_missing_leaves_a_box_look(tmp_path):
    hub, agent, calls, logged = make_agent(tmp_path, {"answer": "A stoma.", "targets": [{"box_2d": [1, 2, 3, 4], "label": "s"}]})

    async def broken(_hub):
        raise RuntimeError("the Molmo worker exited")

    agent.molmo.client = broken
    view = agent.snapshot(7)
    data, _ = asyncio.run(agent.look("what's that?", view, option="gemini-3.5-flash", pointing="molmo"))
    assert data["targets"] == [{"box_2d": [1, 2, 3, 4], "label": "s"}]
    assert logged[-1]["kind"] == "molmo-none" and "exited" in logged[-1]["why"]
    agent.molmo = None
    asyncio.run(agent.look("what's that?", view, option="gemini-3.5-flash", pointing="molmo"))
    assert logged[-1] == {"kind": "molmo-none", "why": "no Molmo in this app", "question": "what's that?"}


# --- reading the settled view ahead ----------------------------------------------------------------
def test_the_settled_view_is_read_ahead_and_a_question_on_it_moves_with_the_slide(tmp_path):
    hub, agent, calls, logged = make_agent(tmp_path, None, points=[(640, 380)])
    pointer = agent.molmo
    assert pointer.enabled()
    hub.settings.pointing = "box"
    assert not pointer.enabled()  # nothing is read for "box"
    pointer.also = lambda: True  # a blind comparison has a "molmo" arm
    read = asyncio.run(pointer.read())
    assert hub.gpu_molmo.calls[0][0] == "molmo_read" and read.frame_id == 7 and logged[-1]["kind"] == "molmo-read"
    # The slide moved 30 px right since: the question is answered on the view read, the points moved along.
    later = info(id=9, dx=30.0, fov=FieldOfView(640, 360, 340, 1.0))
    hub.infos[9], hub.latest_info = later, later
    image = np.full((H, W, 3), 90, np.uint8)
    out = asyncio.run(pointer.point("what's that?", (later, image, Crop(300, 20, 680, 680))))
    op, payload = hub.gpu_molmo.calls[-1]
    assert op == "molmo_point" and payload["frame_id"] == 7 and out["ahead"] and out["frameId"] == 7
    assert out["points"] == [[670.0, 380.0]]
    # Moved far, or continuity lost: the view itself is read.
    far = info(id=10, dx=400.0, fov=FieldOfView(640, 360, 340, 1.0))
    hub.infos[10] = far
    out = asyncio.run(pointer.point("what's that?", (far, image, Crop(300, 20, 680, 680))))
    assert hub.gpu_molmo.calls[-1][1]["frame_id"] == 10 and not out["ahead"] and out["points"] == [[640.0, 380.0]]
    assert hub.gpu_molmo.calls[-1][1]["crop"] == [300, 20, 680, 680]


def test_reading_waits_for_the_slide_to_settle_and_again_when_it_moved():
    pointer = MolmoPointer(Hub())
    assert not pointer.due(info(steady=False), 10.0)
    assert not pointer.due(info(), 10.2)
    assert pointer.due(info(), 10.7)
    pointer.current, pointer._last = Read(7, 0, 0.0, 0.0, 300.0, Crop(0, 0, 10, 10)), 10.7
    assert not pointer.due(info(), 12.0)
    assert pointer.due(info(dx=200.0), 12.0)
    assert pointer.due(info(epoch=1), 12.0)
    pointer.still_changed("content", info())
    assert pointer.due(info(), 12.0)


def test_without_a_gpu_nothing_is_read():
    hub = Hub()
    hub.best_frame = lambda window: hub.latest_info
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=b"x"), is_fresh=lambda: True)

    async def none(_hub):
        return None

    assert asyncio.run(MolmoPointer(hub, client=none).read()) is None


# --- licence, options, arena -------------------------------------------------------------------------
def test_molmo_is_open_in_every_edition_and_runs_in_its_own_worker(monkeypatch):
    from gpu_worker.worker import licence_refusal
    from orchestrator import licences
    from orchestrator.options import POINTING

    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    for op in ("molmo_load", "molmo_read", "molmo_point"):
        assert licence_refusal({"op": op}) is None
    assert licences.COMPONENTS["molmo2-4b"].licence == "Apache-2.0"
    assert POINTING["molmo"].uses == ("molmo2-4b",) and POINTING["molmo"].tier == "open"
    from orchestrator.hub import Hub as RealHub

    hub = RealHub("http://127.0.0.1:1", use_gpu=True)
    assert hub.gpu_molmo is not None and hub.gpu_molmo is not hub.gpu_heavy and not hub.gpu_molmo.warm
    assert RealHub("http://127.0.0.1:1", use_gpu=False).gpu_molmo is None


def test_molmo_is_offered_as_a_blind_comparison_arm():
    from orchestrator.arena import Arena, ask_arm_ids

    assert "gemini-3.5-flash/molmo" in ask_arm_ids()
    arena = Arena(Hub(), agent=None)
    assert arena.configure({"ask": ["gemini-3.5-flash/box", "gemini-3.5-flash/molmo"]}) == []
    assert arena.arms["ask"] == ["gemini-3.5-flash/box", "gemini-3.5-flash/molmo"]
