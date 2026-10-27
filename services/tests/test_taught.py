"""Labels the user teaches (orchestrator/taught.py, option TAUGHT): examples are kept and read back
per day, matched by cosine similarity once a name has 3, given to naming as one line of data, and
shown apart from the model's name, which code never overwrites. Embeddings are stand-ins here
(bench/taught_bench.py measures the real DINOv2)."""
import asyncio
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator import taught
from orchestrator.agent import Agent
from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.scene import SceneObject
from orchestrator.taught import Example, Match, TaughtLabels, context_line, crop_for, nearest
from tests.fakes import Hub, announcer

DAY = "2026-10-06"
SQUARE = [[[600, 300], [660, 300], [660, 360], [600, 360]]]


def unit(*values):
    v = np.asarray(values, np.float32)
    return list(v / np.linalg.norm(v))


def jpeg_of(value: int) -> bytes:
    ok, enc = cv2.imencode(".jpg", np.full((32, 32, 3), value, np.uint8))
    return enc.tobytes()


class Embed:
    """A stand-in embedding: a crop's mean brightness picks one of three directions."""

    def __init__(self):
        self.calls = 0

    async def __call__(self, jpegs, model="dinov2-small"):
        self.calls += 1
        out = []
        for j in jpegs:
            v = float(cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR).mean())
            out.append(unit(1, 0.1, 0) if v < 85 else unit(0, 1, 0.1) if v < 170 else unit(0.1, 0, 1))
        return out


def store(tmp_path, embed=None, hub=None, day=DAY):
    return TaughtLabels(tmp_path / "taught", today=lambda: day, embed=embed or Embed(), hub=hub)


def ex(label, vector, at=0.0):
    return Example(id=f"t{at}", label=label, key=label, vector=vector, crop="", at=at)


# --- keeping and reading back -------------------------------------------------------------------
def test_examples_are_kept_per_day_and_read_back_after_a_restart(tmp_path):
    labels = store(tmp_path)
    for i in range(3):
        asyncio.run(labels.teach("guard cell", f"o{i}", crop_jpeg=jpeg_of(40), frame_id=10 + i))
    folder = tmp_path / "taught" / DAY
    assert sorted(p.name for p in folder.glob("*.jpg")) == ["t1.jpg", "t2.jpg", "t3.jpg"]
    lines = [json.loads(line) for line in (folder / "examples.jsonl").read_text().splitlines()]
    assert [r["label"] for r in lines] == ["guard cell"] * 3 and len(lines[0]["vector"]) == 3

    again = store(tmp_path)  # a restart
    assert again.counts() == {"guard cell": 3} and again.ready()
    assert asyncio.run(again.teach("Guard cells", "o9", crop_jpeg=jpeg_of(40))).id == "t4"  # ids never reused
    assert again.counts() == {"Guard cells": 4}  # one name: the same label key

    # Taught labels are the user's (ADR 0017): the next day, in any session, has every date's, newest first.
    tomorrow = store(tmp_path, day="2026-10-07")
    assert tomorrow.counts() == {"Guard cells": 4} and tomorrow.ready()
    assert asyncio.run(tomorrow.teach("guard cell", "o1", crop_jpeg=jpeg_of(40))).id == "t1"  # a date folder's own ids
    assert tomorrow.counts() == {"guard cell": 5} and (tmp_path / "taught" / "2026-10-07" / "t1.jpg").exists()
    assert (folder / "examples.jsonl").exists() and store(tmp_path, day="2026-10-08").counts() == {"guard cell": 5}


def test_a_line_cut_off_by_a_crash_does_not_lose_the_rest(tmp_path):
    labels = store(tmp_path)
    asyncio.run(labels.teach("stoma", "o1", crop_jpeg=jpeg_of(40)))
    with (tmp_path / "taught" / DAY / "examples.jsonl").open("a") as f:
        f.write('{"id": "t2", "label": "sto')
    assert store(tmp_path).counts() == {"stoma": 1}


def test_teaching_needs_a_name_and_a_picture(tmp_path):
    labels = store(tmp_path)
    with pytest.raises(ValueError, match="no name"):
        asyncio.run(labels.teach("  ", "o1", crop_jpeg=jpeg_of(40)))
    with pytest.raises(ValueError, match="no longer here"):
        asyncio.run(labels.teach("stoma", "o1"))


def test_a_remembered_crop_is_taught_without_its_frame(tmp_path):
    embed = Embed()
    labels = store(tmp_path, embed)
    labels.remember("o5", jpeg_of(40), unit(1, 0.1, 0))
    taught_one = asyncio.run(labels.teach("guard cell", "o5"))
    assert embed.calls == 0  # its vector was kept with it
    assert (tmp_path / "taught" / DAY / taught_one.crop).read_bytes() == jpeg_of(40)


# --- matching -----------------------------------------------------------------------------------------
def test_nothing_is_matched_below_three_examples_of_a_name():
    examples = [ex("guard cell", unit(1, 0, 0), 1), ex("guard cell", unit(1, 0.1, 0), 2)]
    assert nearest(unit(1, 0, 0), examples) is None
    examples.append(ex("guard cell", unit(1, 0, 0.1), 3))
    m = nearest(unit(1, 0, 0), examples)
    assert m.label == "guard cell" and m.examples == 3 and m.similarity > 0.99


def test_the_closest_name_wins_with_the_runner_up_given():
    examples = ([ex("guard cell", unit(1, 0.05 * i, 0), i) for i in range(4)]
                + [ex("pore", unit(0, 1, 0.05 * i), 10 + i) for i in range(3)])
    m = nearest(unit(0.2, 1, 0), examples)
    assert (m.label, m.next_label) == ("pore", "guard cell")
    assert m.similarity > m.next_similarity
    assert m.wire() == {"label": "pore", "similarity": round(m.similarity, 2), "examples": 3}


def test_only_the_newest_ten_examples_of_a_name_count():
    old = [ex("cell", unit(0, 0, 1), i) for i in range(5)]  # taught first, then taught better
    new = [ex("cell", unit(1, 0, 0), 100 + i) for i in range(10)]
    assert nearest(unit(1, 0, 0), old + new).similarity == pytest.approx(1.0, abs=1e-5)
    assert nearest(unit(1, 0, 0), old + new).examples == 10


def test_matching_is_off_or_waits_for_examples_without_embedding(tmp_path):
    embed = Embed()
    hub = Hub()
    labels = store(tmp_path, embed, hub)
    assert asyncio.run(labels.match("o1", jpeg_of(40))) is None and embed.calls == 0  # nothing taught yet
    for i in range(3):
        asyncio.run(labels.teach("guard cell", f"t{i}", crop_jpeg=jpeg_of(40)))
    calls = embed.calls
    hub.settings.update({"taught": "off"})
    assert asyncio.run(labels.match("o1", jpeg_of(40))) is None and embed.calls == calls
    hub.settings.update({"taught": "on"})
    assert asyncio.run(labels.match("o1", jpeg_of(40))).label == "guard cell"


def test_the_crop_is_a_padded_square_around_the_outline():
    image = np.zeros((100, 200, 3), np.uint8)
    image[40:60, 0:20] = 255
    crop = crop_for(image, (0, 40, 20, 60), margin=1.5, side=30)
    assert crop.shape == (30, 30, 3)
    assert crop[:, :5].max() == 0  # off the frame's left edge: black
    assert crop[15, 15].min() > 200  # the thing in the middle
    assert crop_for(image, (5, 5, 6, 6)) is None


# --- what the model and the studio are given ---------------------------------------------------------
def test_the_context_line_is_one_line_of_data():
    assert context_line(None) == ""
    line = context_line(Match('guard "cell"\n<b>', 0.8234, 4, "pore", 0.7111))
    assert "\n" not in line and "<" not in line
    assert '"guard cell b" examples (similarity 0.82, 4 examples)' in line and '"pore" examples (0.71)' in line
    assert "clue" in line  # data, not a verdict: the model names it


def make_hub_with_frame():
    hub = Hub()
    image = np.full((720, 1280, 3), 30, np.uint8)
    ok, jpeg = cv2.imencode(".jpg", image)
    hub.infos = {7: hub.latest_info}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg.tobytes()) if fid == 7 else None, is_fresh=lambda: True)
    return hub


def test_naming_a_click_is_told_the_match_and_the_model_keeps_its_name(tmp_path):
    hub = make_hub_with_frame()
    embed = Embed()
    labels = store(tmp_path, embed, hub)
    labels.watch(hub.scene)
    for i in range(3):
        asyncio.run(labels.teach("guard cell", f"t{i}", crop_jpeg=jpeg_of(40)))
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    agent.taught = labels
    prompts, logged = [], []

    async def ask(images, prompt, max_output_tokens=0):
        prompts.append(prompt)
        return SimpleNamespace(data={"label": "stoma", "answer": "A pore between two cells.", "confidence": 0.8},
                               model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = logged.append
    agent.keep_image = lambda *a, **k: None

    async def click():
        obj = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                                        anchor_offset=(0.0, 0.0), source="click"))
        return await agent.identify(obj.id)

    out = asyncio.run(click())
    obj = hub.scene.objects["o1"]
    assert 'Looks like the user\'s own "guard cell" examples' in prompts[0]
    assert out["label"] == obj.label == "stoma"  # the model's name, never the taught one
    assert obj.status == "likely" and obj.taught["label"] == "guard cell"
    assert obj.to_wire()["taught"] == obj.taught
    assert next(r for r in logged if r["kind"] == "identify")["taught"]["label"] == "guard cell"
    assert embed.calls == 3 + 1  # the click's outline was embedded once, for naming and the studio alike


def test_a_blind_arm_shows_no_match():
    obj = SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0, anchor_offset=(0, 0),
                      source="ai:x", taught={"label": "stoma", "similarity": 0.9, "examples": 3}, trial="a1", arm="A")
    assert obj.to_wire()["taught"] is None


def test_without_a_worker_naming_goes_on_and_says_why_in_the_log(tmp_path):
    hub = make_hub_with_frame()

    async def broken(jpegs, model="dinov2-small"):
        raise RuntimeError("no GPU worker")

    labels = store(tmp_path, broken, hub)
    labels.examples = [ex("cell", unit(1, 0, 0), i) for i in range(3)]
    logged = []
    labels.log = logged.append
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="click"))

    async def run():
        return await labels.match_object("o1")

    assert asyncio.run(run()) is None
    assert logged[-1]["kind"] == "taught-match" and "no GPU worker" in logged[-1]["error"]
    assert hub.scene.objects["o1"].taught is None


# --- teaching from what the user says ------------------------------------------------------------------
def test_the_user_naming_an_outline_confirms_it_and_keeps_an_example(tmp_path):
    hub = make_hub_with_frame()
    labels = store(tmp_path, Embed(), hub)
    registry = build(Services(hub=hub, taught=labels))
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="ai:x", label="stoma", status="unsure", maybe=["pore"]))
    ctx = ToolContext(channel="voice", turn_id=1, frame_id=7)
    said = asyncio.run(registry.tools["name_outline"].handler(ctx, {"object": "o1", "name": "guard cell"}))
    obj = hub.scene.objects["o1"]
    assert (obj.label, obj.status, obj.maybe) == ("guard cell", "confirmed", [])
    assert "kept as example 1 of 'guard cell'" in said and "from 3" in said
    assert labels.counts() == {"guard cell": 1}


def test_naming_an_outline_whose_picture_is_gone_still_names_it_and_says_so(tmp_path):
    hub = Hub()  # no frames
    labels = store(tmp_path, Embed(), hub)
    registry = build(Services(hub=hub, taught=labels))
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=3, anchor_epoch=1,
                              anchor_offset=(0.0, 0.0), source="click", label="cell"))
    said = asyncio.run(registry.tools["name_outline"].handler(ToolContext("chat", 1, None), {"object": "o1", "name": "guard cell"}))
    assert hub.scene.objects["o1"].label == "guard cell"
    assert "could not be kept as an example" in said and labels.counts() == {}


def test_accepting_a_correction_teaches_its_name(tmp_path):
    hub = make_hub_with_frame()
    labels = store(tmp_path, Embed(), hub)
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="ai:x", label="stoma"))
    correction = {"id": "c1", "object_id": "o1", "label": "stoma", "to_label": "guard cell", "reason": "two kidney shapes"}
    research = SimpleNamespace(decide=lambda cid, accept: (SimpleNamespace(name="Researcher 1", rid="r1"), correction))
    s = announcer(tmp_path, research, scene=hub.scene)
    s.announcer.taught = labels

    async def accept():
        out = s.announcer.decided("c1", True)
        await asyncio.sleep(0.05)
        return out

    assert asyncio.run(accept())["relabelled"] == ["o1"]
    assert hub.scene.objects["o1"].status == "confirmed"
    assert labels.counts() == {"guard cell": 1} and labels.examples[0].source == "correction"


def test_a_new_look_never_renames_what_the_user_named(tmp_path):
    hub = make_hub_with_frame()
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="ai:x", label="guard cell", status="confirmed"))
    box = [300 / 720 * 1000, 600 / 1280 * 1000, 360 / 720 * 1000, 660 / 1280 * 1000]  # the same square, 0-1000
    from orchestrator.coords import Crop

    asyncio.run(agent.point([{"box_2d": box, "label": "stoma", "confidence": 0.9}], 7, Crop(0, 0, 1280, 720)))
    obj = hub.scene.objects["o1"]
    assert (obj.label, obj.status) == ("guard cell", "confirmed")


def test_the_embedding_op_is_open_in_every_edition(monkeypatch):
    from gpu_worker.worker import licence_refusal

    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    assert licence_refusal({"op": "embed", "jpegs": []}) is None
    from orchestrator import licences

    assert licences.COMPONENTS["dinov2-small"].tier == "open"


def test_the_brain_sees_the_match_beside_the_name():
    from orchestrator.board import Item
    from orchestrator.brain.context import _outlined

    item = Item("o1", "highlight", "stoma", (600, 300, 660, 360), 0.004, "likely", "ai:x", 0.8, True, None, 0.0,
                {"label": "guard cell", "similarity": 0.823, "examples": 4})
    state = SimpleNamespace(outlines=[item], items=[item], selected=None, magnifier={"object": None})
    row = _outlined(state, SimpleNamespace(width=1280, height=720), positions=True)[0]
    assert row.startswith('o1 "stoma" (') and 'closest to the user\'s own "guard cell" examples (similarity 0.82)' in row


def test_an_outline_is_taught_from_its_own_frame_after_the_buffer_moved_on(tmp_path):
    hub = make_hub_with_frame()
    labels = store(tmp_path, Embed(), hub)
    labels.watch(hub.scene)

    async def outline_then_lose_the_frame():
        hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                                  anchor_offset=(0.0, 0.0), source="click"))
        hub.source = SimpleNamespace(get=lambda fid: None, is_fresh=lambda: True)  # the ring moved on
        hub.latest_info = SimpleNamespace(id=99, dx=50.0, dy=0.0, width=1280, height=720, epoch=3, fov=None)
        return await labels.teach("guard cell", "o1")

    example = asyncio.run(outline_then_lose_the_frame())
    assert example.frame_id == 7 and labels.counts() == {"guard cell": 1}
