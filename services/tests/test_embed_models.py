"""Which model turns an outline's picture into a vector (option EMBED: DINOv2 small or BioCLIP 2,
gpu_worker/embed.py): every vector says which model made it, vectors of two models are never
compared, and after a change the day's taught examples and kept outlines are embedded again from
their crops; each model has its own thresholds and calibration. Embeddings are stand-ins here
(bench/taught_bench.py and bench/seen_bench.py measure the real models)."""
import asyncio
import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator import licences, seen as seen_module, taught as taught_module
from orchestrator.options import EMBED, GROUPS, Settings
from orchestrator.scene import SceneObject
from orchestrator.seen import Kept, SeenToday, Similar
from orchestrator.taught import Example, Match, TaughtLabels, context_line, gpu_embed, nearest
from tests.fakes import Hub

DAY = "2026-10-06"
SQUARE = [[[600, 300], [660, 300], [660, 360], [600, 360]]]
NINE = time.mktime((2026, 10, 6, 9, 12, 0, 0, 0, -1))


def unit(*values):
    v = np.asarray(values, np.float32)
    return list(v / np.linalg.norm(v))


def jpeg_of(value: int, side: int = 32) -> bytes:
    return cv2.imencode(".jpg", np.full((side, side, 3), value, np.uint8))[1].tobytes()


class Embed:
    """A stand-in for both models: a crop's brightness picks a direction, and each model puts it
    somewhere else (so a vector of one compared with a vector of the other would be nonsense)."""

    def __init__(self):
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, jpegs, model="dinov2-small"):
        self.calls.append((model, len(jpegs)))
        out = []
        for j in jpegs:
            v = float(cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR).mean())
            base = [1, 0.1, 0] if v < 85 else [0, 1, 0.1] if v < 170 else [0.1, 0, 1]
            out.append(unit(*base, 0, 0, 0) if model == "dinov2-small" else unit(0, 0, 0, *base))
        return out


def hub_with_frame(value=30):
    hub = Hub()
    jpeg = cv2.imencode(".jpg", np.full((720, 1280, 3), value, np.uint8))[1].tobytes()
    hub.infos = {7: SimpleNamespace(id=7, dx=0.0, dy=0.0, epoch=0, fov=None, quality=())}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg) if fid == 7 else None, is_fresh=lambda: True)
    return hub


def outline(hub, oid):
    return hub.scene.add(SceneObject(id=oid, kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                                     anchor_offset=(0.0, 0.0), source="click"))


# --- the option -----------------------------------------------------------------------------------
def test_embed_is_an_option_group_with_dinov2_by_default_and_both_open():
    assert GROUPS["embed"] is EMBED and set(EMBED) == {"dinov2-small", "bioclip-2", "dinov3-small"}
    settings = Settings()
    assert settings.embed == "dinov2-small" and settings.to_wire()["embed"] == "dinov2-small"
    assert settings.update({"embed": "bioclip-2"}) == [] and settings.embed == "bioclip-2"
    assert settings.update({"embed": "clip"}) and settings.embed == "bioclip-2"
    assert EMBED["bioclip-2"].uses == ("bioclip-2",) and licences.COMPONENTS["bioclip-2"].licence == "MIT"
    assert licences.permitted(["bioclip-2"], "commercial")
    # DINOv3 (M3.5): Meta's own licence, tier "terms" (offered in the commercial edition too), gated weights.
    assert licences.COMPONENTS["dinov3-small"].tier == "terms" and licences.permitted(["dinov3-small"], "commercial")


def test_the_worker_op_runs_on_the_model_asked_for(monkeypatch):
    from gpu_worker.worker import OP_COMPONENTS, licence_refusal

    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    assert OP_COMPONENTS["embed"]({"op": "embed"}) == ("dinov2-small",)  # the default
    assert OP_COMPONENTS["embed"]({"op": "embed", "model": "bioclip-2"}) == ("bioclip-2",)
    assert licence_refusal({"op": "embed", "jpegs": [], "model": "bioclip-2"}) is None


def test_gpu_embed_asks_the_heavy_worker_for_the_model():
    calls = []

    class Heavy:
        async def ensure_started(self):
            return self

        async def call(self, op, timeout, **req):
            calls.append((op, req["model"]))
            return {"vectors": [[1.0, 0.0]], "model": req["model"]}

    hub = SimpleNamespace(gpu_heavy=Heavy())
    assert asyncio.run(gpu_embed(hub, [b"x"], model="bioclip-2")) == [[1.0, 0.0]]
    assert calls == [("embed", "bioclip-2")]

    class Wrong(Heavy):
        async def call(self, op, timeout, **req):
            return {"vectors": [[1.0]], "model": "dinov2-small"}

    with pytest.raises(RuntimeError, match="not bioclip-2"):
        asyncio.run(gpu_embed(SimpleNamespace(gpu_heavy=Wrong()), [b"x"], model="bioclip-2"))


# --- taught: one model's vectors at a time --------------------------------------------------------
def test_nearest_compares_only_vectors_of_the_same_model():
    dino = [Example(id=f"t{i}", label="cell", key="cell", vector=unit(1, 0, 0), crop="", at=i) for i in range(3)]
    bio = [Example(id=f"t{i}", label="pore", key="pore", vector=unit(1, 0, 0), crop="", at=i, model="bioclip-2")
           for i in range(3, 6)]
    assert nearest(unit(1, 0, 0), dino + bio).label == "cell"
    m = nearest(unit(1, 0, 0), dino + bio, model="bioclip-2")
    assert (m.label, m.model) == ("pore", "bioclip-2")
    assert nearest(unit(1, 0, 0), dino, model="bioclip-2") is None


def test_each_model_has_its_own_calibration_in_the_line():
    dino = context_line(Match("guard cell", 0.88, 4))
    bio = context_line(Match("guard cell", 0.95, 4, model="bioclip-2"))
    assert taught_module.CALIBRATIONS["dinov2-small"] in dino and taught_module.CALIBRATIONS["bioclip-2"] in bio
    assert taught_module.CALIBRATION == taught_module.CALIBRATIONS["dinov2-small"]
    assert "\n" not in bio and "clue" in bio


def test_lines_from_before_embed_load_as_dinov2(tmp_path):
    folder = tmp_path / "taught" / DAY
    folder.mkdir(parents=True)
    old = {"id": "t1", "label": "cell", "key": "cell", "vector": [1.0, 0.0], "crop": "t1.jpg", "at": 1.0}
    (folder / "examples.jsonl").write_text(json.dumps(old) + "\n")
    labels = TaughtLabels(tmp_path / "taught", today=lambda: DAY, embed=Embed())
    assert labels.examples[0].model == "dinov2-small"


def test_after_embed_changes_the_examples_are_embedded_again_from_their_crops(tmp_path):
    hub = Hub()
    embed = Embed()
    labels = TaughtLabels(tmp_path / "taught", today=lambda: DAY, embed=embed, hub=hub)
    for i in range(3):
        asyncio.run(labels.teach("guard cell", f"o{i}", crop_jpeg=jpeg_of(40)))
    assert {e.model for e in labels.examples} == {"dinov2-small"} and embed.calls == [("dinov2-small", 1)] * 3

    hub.settings.update({"embed": "bioclip-2"})
    m = asyncio.run(labels.match("o9", jpeg_of(40)))
    assert (m.label, m.model, m.examples) == ("guard cell", "bioclip-2", 3) and m.similarity > 0.99
    assert embed.calls[3:] == [("bioclip-2", 1), ("bioclip-2", 3)]  # the click, then the day's examples in one call
    assert labels.counts() == {"guard cell": 3}  # three examples, whatever models embedded them

    asyncio.run(labels.match("o10", jpeg_of(40)))
    assert embed.calls[-1] == ("bioclip-2", 1)  # only the click: the examples have their BioCLIP vectors

    again = TaughtLabels(tmp_path / "taught", today=lambda: DAY, embed=embed, hub=hub)  # a restart
    assert sorted((e.id, e.model) for e in again.examples) == sorted(
        [(f"t{i}", m) for i in (1, 2, 3) for m in ("dinov2-small", "bioclip-2")])
    assert again.counts() == {"guard cell": 3} and again.examples_of("guard cell") == 3
    assert asyncio.run(again.teach("guard cell", "o11", crop_jpeg=jpeg_of(40))).id == "t4"


def test_a_vector_of_another_model_is_not_reused(tmp_path):
    hub = hub_with_frame()
    embed = Embed()
    labels = TaughtLabels(tmp_path / "taught", today=lambda: DAY, embed=embed, hub=hub)
    labels.remember("o5", jpeg_of(40), unit(1, 0.1, 0, 0, 0, 0), "dinov2-small")
    hub.settings.update({"embed": "bioclip-2"})
    taught_one = asyncio.run(labels.teach("guard cell", "o5"))
    assert taught_one.model == "bioclip-2" and embed.calls == [("bioclip-2", 1)]

    async def both():
        return (await labels.object_vectors(["o5"], "bioclip-2")["o5"], await labels.object_vectors(["o5"], "dinov2-small")["o5"])

    bio, dino = asyncio.run(both())
    assert bio[3] == "bioclip-2" and dino[3] == "dinov2-small" and bio[1] != dino[1]


# --- seen: per model ------------------------------------------------------------------------------
def make_seen(tmp_path, hub, embed, clock):
    taught = TaughtLabels(tmp_path / "taught", today=lambda: DAY, embed=embed, hub=hub)
    taught.watch(hub.scene)
    s = SeenToday(tmp_path / "seen", tmp_path / "logs", today=lambda: DAY, thread=lambda: "t1",
                  title=lambda tid: "", taught=taught, hub=hub, clock=lambda: clock[0])
    s.watch(hub.scene)
    return s


async def settle():
    for _ in range(20):
        await asyncio.sleep(seen_module.BATCH_S)


def test_each_model_has_its_own_threshold_floor_and_calibration():
    assert seen_module.threshold() == seen_module.THRESHOLD == 0.93
    assert seen_module.threshold("bioclip-2") == seen_module.THRESHOLDS["bioclip-2"] > 0.93
    assert seen_module.list_floor("bioclip-2") == seen_module.LIST_FLOORS["bioclip-2"]
    kept = Kept(id="s1", object_id="o1", label="neutrophil", key="neutrophil", status="likely", thread="t1",
                vector=[], crop="", at=NINE, model="bioclip-2")
    line = seen_module.context_line(Similar(kept, 0.97))
    assert seen_module.CALIBRATIONS["bioclip-2"] in line and seen_module.CALIBRATIONS["dinov2-small"] not in line
    listing = seen_module.listing([Similar(kept, 0.97)], lambda t: "", "it")
    assert f"From {seen_module.LIST_FLOORS['bioclip-2']:.2f}" in listing


def test_seen_compares_only_the_same_model_and_embeds_the_day_again_after_a_change(tmp_path):
    hub = hub_with_frame()
    embed = Embed()
    clock = [NINE]
    s = make_seen(tmp_path, hub, embed, clock)

    async def name_one(oid):
        outline(hub, oid)
        await settle()
        hub.scene.update(oid, label="neutrophil", status="likely")
        await settle()

    asyncio.run(name_one("o1"))
    assert [(k.id, k.model) for k in s.kept] == [("s1", "dinov2-small")] and s.kept[0].crop

    clock[0] += 3600
    hub.settings.update({"embed": "bioclip-2"})

    async def click():
        outline(hub, "o2")
        first = await s.match("o2")  # nothing of BioCLIP's yet: the kept one is embedded again meanwhile
        await settle()
        return first, await s.match("o2")

    first, second = asyncio.run(click())
    assert first is None
    assert second is not None and second.kept.id == "s1" and second.kept.model == "bioclip-2" and second.similarity > 0.99
    assert ("bioclip-2", 1) in embed.calls
    assert sorted((k.id, k.model) for k in s.kept) == [("s1", "bioclip-2"), ("s1", "dinov2-small")]
    assert len(s.earlier()) == 1  # one outline, whatever models embedded it

    again = SeenToday(tmp_path / "seen", tmp_path / "logs", today=lambda: DAY, thread=lambda: "t1",
                      title=lambda tid: "", taught=s.taught, hub=hub, clock=lambda: clock[0])  # a restart
    assert sorted((k.id, k.model) for k in again.kept) == [("s1", "bioclip-2"), ("s1", "dinov2-small")]


def test_a_renamed_outline_is_renamed_for_every_model(tmp_path):
    hub = hub_with_frame()
    s = make_seen(tmp_path, hub, Embed(), [NINE])
    s.kept = [Kept(id="s1", object_id="o1", label="cell", key="cell", status="likely", thread="t1", vector=[1.0, 0.0],
                   crop="", at=NINE, model=m) for m in ("dinov2-small", "bioclip-2")]
    s._by_object["o1"] = "s1"
    outline(hub, "o1")
    hub.settings.update({"embed": "bioclip-2"})

    async def rename():
        await settle()
        return await s._keep("o1", dict(label="neutrophil", status="confirmed", thread="t1", at=NINE, kind="highlight",
                                        source="click"))

    kept = asyncio.run(rename())
    assert kept.model == "bioclip-2" and {(k.label, k.model) for k in s.kept} == {("neutrophil", "dinov2-small"),
                                                                                   ("neutrophil", "bioclip-2")}
