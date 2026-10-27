"""Successive close-ups (option CLOSEUP, orchestrator/agent.py): the model may ask to see part of the
close-up closer; the app cuts it from the full-resolution frame and asks again, at most 3 calls."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator.agent import (CLOSER_ROUNDS, Agent, answer_closer, close_region, closer_box, name_closer)
from orchestrator.coords import Crop
from orchestrator.prompts import IDENTIFY
from orchestrator.scene import SceneObject
from tests.fakes import Hub


def frame() -> np.ndarray:
    image = np.full((720, 1280, 3), 180, np.uint8)
    cv2.circle(image, (640, 360), 20, (90, 40, 120), -1)
    cv2.circle(image, (646, 354), 3, (20, 20, 20), -1)  # the small part the model wants to see
    return image


def reply(data, ms=10.0):
    return SimpleNamespace(data=data, latency_ms=ms, model="stand-in", error=None)


def test_a_closer_box_is_cut_from_the_frame_inside_it_and_never_below_the_finest_size():
    on = Crop(560, 280, 160, 160)
    box = closer_box([400, 400, 600, 600], on, (720, 1280, 3))
    assert 620 < box.x < 640 and 340 < box.y < 360 and 32 <= box.width <= 40  # the middle fifth, with a margin
    tiny = closer_box([500, 500, 501, 501], on, (720, 1280, 3))
    assert tiny.width >= 12  # nothing finer than the camera's pixels is asked for
    assert closer_box(["a", 1, 2, 3], on, (720, 1280, 3)) is None


def test_the_model_may_look_closer_twice_and_then_must_name():
    image = frame()
    calls = []

    async def ask(images, prompt):
        calls.append((len(images), prompt))
        return reply({"closer": [300, 300, 700, 700]})  # it would keep asking

    data, result, steps, shown = asyncio.run(name_closer(ask, image, b"view", close_region(image, (620, 340, 660, 380)),
                                                         "PROMPT"))
    assert len(calls) == CLOSER_ROUNDS == 3
    assert [n for n, _ in calls] == [2, 3, 3]  # the view and the close-up; then also the closer look
    assert "2 closer looks left" in calls[0][1] and "1 closer look left" in calls[1][1]
    assert "No more closer looks: name it now." in calls[2][1] and "Closer look:" not in calls[2][1]
    assert len(shown) == 3 and [s["closer"] is not None for s in steps] == [True, True, False]
    first, second = steps[0]["closer"], steps[1]["closer"]
    assert (second[2] - second[0]) < (first[2] - first[0])  # each look is closer than the one before


def test_a_name_on_the_first_look_costs_one_call():
    image = frame()
    calls = []

    async def ask(images, prompt):
        calls.append(prompt)
        return reply({"label": "lymphocyte", "confidence": 0.9})

    data, _, steps, shown = asyncio.run(name_closer(ask, image, b"view", close_region(image, (620, 340, 660, 380)), "P"))
    assert data["label"] == "lymphocyte" and len(calls) == 1 and len(steps) == 1 and len(shown) == 1


def test_an_answer_may_look_closer_and_keeps_its_boxes_on_the_whole_view():
    image = frame()
    calls = []

    async def ask(images, prompt):
        calls.append((len(images), prompt))
        if len(calls) == 1:
            return reply({"closer": [450, 450, 550, 550]})
        return reply({"answer": "A dark granule.", "targets": []})

    data, _, steps = asyncio.run(answer_closer(ask, image, b"view", Crop(310, 30, 660, 660), "ASK"))
    assert data["answer"] == "A dark granule." and [n for n, _ in calls] == [1, 2]
    assert "never on image 2" in calls[1][1]


def identify_with(tmp_path, closeup: str):
    hub = Hub()
    hub.settings.closeup = closeup
    ok, jpeg = cv2.imencode(".jpg", frame())
    hub.infos = {7: hub.latest_info}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg.tobytes()) if fid == 7 else None, is_fresh=lambda: True)
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[620, 340], [660, 340], [660, 380], [620, 380]]],
                              anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click"))
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    calls, logged = [], []

    async def ask(images, prompt, max_output_tokens=0):
        calls.append((len(images), prompt))
        if closeup != "single" and len(calls) == 1:
            return reply({"closer": [400, 400, 600, 600]}, 7.0)
        return reply({"label": "basophil", "answer": "Dark granules over the nucleus.", "confidence": 0.9}, 5.0)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = logged.append
    agent.keep_image = lambda *a, **k: "crops/x.jpg"
    out = asyncio.run(agent.identify("o1"))
    return calls, next(r for r in logged if r["kind"] == "identify"), out


def test_naming_a_click_with_closer_looks_logs_each_round(tmp_path):
    calls, record, out = identify_with(tmp_path, "naming")
    assert out["label"] == "basophil" and len(calls) == 2 and out["thinkMs"] == 12
    assert record["rounds"] == 2 and len(record["closer"]) == 1 and record["closerImages"] == ["crops/x.jpg"]


def test_one_close_up_is_the_prompt_as_before(tmp_path):
    calls, record, _ = identify_with(tmp_path, "single")
    assert len(calls) == 1 and calls[0][0] == 2 and "Closer look" not in calls[0][1]
    assert calls[0][1].startswith(IDENTIFY.split("{context}")[0]) and record["rounds"] == 1
