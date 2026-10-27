"""Pointing "masks" (orchestrator/masks.py, gpu_worker/automask.py): segmenter-first Set-of-Mark.
The settled view's masks are kept with their frame and follow the slide; a look numbers them, the
model names numbers (and boxes what it means), and a named number becomes an outline straight from
its mask, with no second segmentation, unless it is not the thing the model boxed. Stand-in models
and GPU here; bench/grounding.py measures the real ones (docs/bench/finding.md)."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from gpu_worker import automask
from orchestrator.agent import Agent
from orchestrator.coords import Crop
from orchestrator.fov import FieldOfView
from orchestrator.marks import mask_labels, render_masks
from orchestrator.masks import Masker, MaskSet, RegionMarks, agrees, region_box
from tests.fakes import Hub

W, H = 1280, 720


def square(x0, y0, x1, y1):
    return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]


def region(x0, y0, x1, y1, inner=None, score=0.9):
    return {"polygons": square(x0, y0, x1, y1), "label": [(x0 + x1) / 2, (y0 + y1) / 2],
            "inner": inner if inner is not None else min(x1 - x0, y1 - y0) / 2, "score": score, "stability": 0.9}


def info(id=7, dx=0.0, dy=0.0, epoch=0, fov=None, steady=True):
    return SimpleNamespace(id=id, dx=dx, dy=dy, epoch=epoch, fov=fov, width=W, height=H, steady=steady)


# --- the masks themselves (pure) ---------------------------------------------------------------
def test_the_grid_stays_inside_the_disc():
    pts = automask.disc_points(W, H, (640, 360, 300), n=16)
    assert 150 < len(pts) < 256
    assert all(np.hypot(x - 640, y - 360) <= 300 * automask.DISC_KEEP for x, y in pts)
    assert len(automask.disc_points(100, 80, None, n=4)) == 16  # no disc: the whole picture


def test_specks_the_whole_field_and_rim_artefacts_are_not_kept():
    valid = 10_000
    assert automask.passes(400, 400, valid, 0.9, 0.9)
    assert not automask.passes(4, 4, valid, 0.9, 0.9)  # a speck of noise
    assert not automask.passes(5_000, 5_000, valid, 0.9, 0.9)  # half the field
    assert not automask.passes(400, 200, valid, 0.9, 0.9)  # mostly outside the disc
    assert not automask.passes(400, 400, valid, 0.3, 0.9)  # the model itself doubts it
    assert not automask.passes(400, 400, valid, 0.9, 0.2)  # unstable


def test_overlapping_masks_keep_the_better_and_numbers_read_top_to_bottom():
    def m(x0, y0, x1, y1):
        out = np.zeros((200, 200), bool)
        out[y0:y1, x0:x1] = True
        return out

    cands = [{"mask": m(10, 150, 40, 180), "score": 0.9, "stability": 0.9},
             {"mask": m(100, 10, 130, 40), "score": 0.8, "stability": 0.9},
             {"mask": m(101, 10, 130, 41), "score": 0.95, "stability": 0.95},  # the same thing, better
             {"mask": m(10, 10, 40, 40), "score": 0.7, "stability": 0.9}]
    kept = automask.select(cands)
    assert len(kept) == 3
    assert kept[1]["score"] == 0.95  # the better of the two overlapping ones
    assert [round(k["cy"]) < 100 for k in kept] == [True, True, False]  # top band first
    assert kept[0]["cx"] < kept[1]["cx"]  # then left to right
    assert len(automask.select(cands, cap=1)) == 1


def test_a_cells_number_is_not_put_on_its_nucleus():
    cell = np.zeros((100, 100), bool)
    cell[10:90, 10:90] = True
    nucleus = np.zeros((100, 100), bool)
    nucleus[35:65, 35:65] = True
    x, y, r = automask.label_point(cell, [nucleus])
    assert not nucleus[int(y), int(x)] and cell[int(y), int(x)] and r > 0
    x, y, r = automask.label_point(cell)
    assert abs(x - 49.5) <= 1 and abs(y - 49.5) <= 1 and r >= 35


def test_a_small_things_number_goes_beside_it():
    crop = Crop(0, 0, W, H)
    big, small = region(100, 100, 300, 300), region(600, 300, 606, 306, inner=2)
    (bx, by), (sx, sy) = mask_labels([big, small], crop)
    assert (bx, by) == (200, 200)  # inside the big one
    assert sx > 606 and 300 <= sy <= 306  # beside the speck, never over it
    clean, marked, scale = render_masks(np.zeros((H, W, 3), np.uint8), crop, [big, small])
    assert clean[:2] == marked[:2] == b"\xff\xd8" and clean != marked and scale == pytest.approx(0.8)


# --- the set follows the slide -------------------------------------------------------------------
def test_masks_follow_the_slide_and_are_dropped_when_it_moved_far_or_continuity_was_lost():
    fov = FieldOfView(640, 360, 300, 1.0)
    masks = MaskSet(5, 0, 10.0, 0.0, 300.0, [region(600, 300, 640, 340), region(930, 340, 960, 380)])
    now = masks.now(info(dx=30.0, dy=5.0, fov=fov))
    assert len(now) == 1  # the second one has moved out of the disc
    assert now[0]["polygons"][0][0] == [620.0, 305.0] and now[0]["label"] == [640.0, 325.0]
    assert masks.now(info(dx=10.0 + 0.4 * 300, fov=fov)) is None  # moved more than a third of the disc
    assert masks.now(info(epoch=1, fov=fov)) is None  # continuity lost


def test_a_named_number_must_be_the_thing_the_model_boxed():
    assert agrees((100, 100, 140, 140), (98, 97, 145, 142))
    assert agrees((100, 100, 110, 110), (96, 96, 114, 114))  # a speck in a looser box
    assert not agrees((300, 100, 320, 120), (100, 100, 140, 140))  # a nearby outline
    assert not agrees((0, 0, 400, 400), (100, 100, 120, 120))  # a large region around it


# --- the look and its outlines ---------------------------------------------------------------------
class Gpu:
    def __init__(self, regions=()):
        self.calls, self.regions, self.ready = [], list(regions), True

    async def call(self, op, timeout=30.0, **payload):
        self.calls.append((op, payload))
        if op == "automask":
            return {"ok": True, "regions": self.regions, "prompts": 200, "candidates": 30, "ms": 900.0}
        return {"ok": True, "polygons": square(400, 400, 440, 440), "score": 0.9, "area": 1600, "ms": 20.0}


def make_agent(tmp_path, answer, regions):
    hub = Hub()
    fov = FieldOfView(640, 360, 340, 1.0)
    frame = info(fov=fov)
    hub.latest_info = frame
    hub.infos = {7: frame}
    hub.gray = lambda fid: None
    hub.candidates_in = lambda fid: []
    hub.gpu = Gpu()
    hub.settings.pointing = "masks"
    jpeg = cv2.imencode(".jpg", np.full((H, W, 3), 90, np.uint8))[1].tobytes()
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg) if fid == 7 else None, is_fresh=lambda: True)
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    agent.masks = Masker(hub)
    agent.masks.current = MaskSet(7, 0, 0.0, 0.0, 340.0, regions)
    calls, logged = [], []

    async def ask(images, prompt, max_output_tokens=0):
        calls.append((len(images), prompt))
        return SimpleNamespace(data=answer, model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = logged.append
    return hub, agent, calls, logged


def box_2d(x0, y0, x1, y1, crop):
    return [(y0 - crop.y) / crop.height * 1000, (x0 - crop.x) / crop.width * 1000,
            (y1 - crop.y) / crop.height * 1000, (x1 - crop.x) / crop.width * 1000]


def test_a_named_number_is_outlined_from_its_mask_with_no_second_segmentation(tmp_path):
    stoma = region(600, 300, 680, 380)
    hub, agent, calls, logged = make_agent(tmp_path, None, [region(500, 200, 520, 220), stoma])
    view = agent.snapshot(7)
    crop = view[2]
    agent_answer = {"answer": "That is likely a stoma.", "specimen": "leaf",
                    "targets": [{"mark": 2, "box_2d": box_2d(598, 298, 684, 382, crop), "label": "stoma", "confidence": 0.8}]}

    async def ask(images, prompt, max_output_tokens=0):
        calls.append((len(images), prompt))
        return SimpleNamespace(data=agent_answer, model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)

    async def run():
        data, used = await agent.look("what's that oval?", view, option="gemini-3.5-flash", pointing="masks")
        return used, await agent.point(data["targets"], 7, crop, used["marks"], used["model"])

    used, objects = asyncio.run(run())
    n_images, prompt = calls[0]
    assert n_images == 2 and "numbered outlines around the things an outline model found" in prompt
    assert isinstance(used["marks"], RegionMarks) and set(used["marks"].regions) == {1, 2}
    assert len(objects) == 1 and objects[0].polygons == stoma["polygons"] and objects[0].label == "stoma"
    assert "automatic mask 2" in objects[0].note
    assert hub.gpu.calls == []  # the mask is the outline: nothing was segmented again


def test_a_number_that_is_not_what_the_model_boxed_is_not_taken(tmp_path):
    hub, agent, calls, logged = make_agent(tmp_path, None, [region(900, 300, 930, 330)])
    crop = agent.snapshot(7)[2]
    targets = [{"mark": 1, "box_2d": box_2d(400, 400, 440, 440, crop), "label": "speck"}]
    marks = RegionMarks(b"", crop, {1: region(900, 300, 930, 330)}, 7, 0, 0.0, 0.0)
    objects = asyncio.run(agent.point(targets, 7, crop, marks, "stand-in"))
    assert [op for op, _ in hub.gpu.calls] == ["segment"]  # outlined from the model's own box
    assert objects[0].polygons == square(400, 400, 440, 440)
    assert any(r.get("kind") == "override" and r.get("rule") == "mask-disagrees-with-box" for r in logged)


def test_with_no_masks_ready_the_look_is_a_box_look_and_says_so(tmp_path):
    answer = {"answer": "A stoma.", "targets": []}
    hub, agent, calls, logged = make_agent(tmp_path, answer, [])
    agent.masks.current = None
    view = agent.snapshot(7)
    data, used = asyncio.run(agent.look("what's that?", view, option="gemini-3.5-flash", pointing="masks"))
    assert calls[0][0] == 1 and "numbered" not in calls[0][1]
    assert used["marks"] is None
    assert logged[-1]["kind"] == "masks-none"


# --- masking the settled view ---------------------------------------------------------------------
def test_the_settled_view_is_masked_on_the_heavy_worker_with_the_spots_as_prompts(tmp_path):
    hub = Hub()
    fov = FieldOfView(640, 360, 340, 1.0)
    frame = info(fov=fov)
    hub.latest_info, hub.infos = frame, {7: frame}
    hub.best_frame = lambda window: frame
    hub.candidates_in = lambda fid: [SimpleNamespace(x=650.0, y=370.0)]
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=b"jpeg"), is_fresh=lambda: True)
    hub.gpu, hub.gpu_heavy = Gpu(), Gpu([region(600, 300, 680, 380)])
    logged = []
    masker = Masker(hub, log=logged.append)
    assert not masker.enabled()  # pointing is "box": nothing is masked
    masker.also = lambda: True  # a blind comparison has a "masks" arm
    assert masker.enabled()
    masks = asyncio.run(masker.run())
    op, payload = hub.gpu_heavy.calls[0]
    assert op == "automask" and payload["disc"] == [640, 360, 340] and payload["points"] == [[650.0, 370.0]]
    assert hub.gpu.calls == []  # never on the click worker when the heavy one exists
    assert masks.frame_id == 7 and len(masks.regions) == 1 and logged[-1]["regions"] == 1


def test_masking_waits_for_the_slide_to_settle_and_runs_again_when_it_moved():
    hub = Hub()
    masker = Masker(hub)
    assert not masker.due(info(steady=False), 10.0)
    assert not masker.due(info(), 10.2)  # not settled long enough
    assert masker.due(info(), 10.7)
    masker.current, masker._last = MaskSet(7, 0, 0.0, 0.0, 300.0, []), 10.7
    assert not masker.due(info(), 12.0)  # still covers the view
    assert masker.due(info(dx=200.0), 12.0)  # moved far
    masker.still_changed("content", info())
    assert masker.due(info(), 12.0)  # something new swam in


def test_the_automask_op_is_open_in_every_edition(monkeypatch):
    from gpu_worker.worker import licence_refusal
    from orchestrator.options import POINTING

    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    assert licence_refusal({"op": "automask"}) is None
    assert POINTING["masks"].permitted and POINTING["masks"].tier == "open"


def test_region_box():
    assert region_box(region(1, 2, 30, 40)) == (1, 2, 30, 40)
