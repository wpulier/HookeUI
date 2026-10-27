"""MedSigLIP's zero-shot ranking when naming a click (orchestrator/zeroshot.py, option ZEROSHOT): which
names are ranked for which subject, the one line of data naming is told, and that the model's name is
never replaced. The model is a stand-in here (bench/zeroshot_bench.py measures MedSigLIP)."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from gpu_worker.zeroshot import pixels, softmax_shares
from orchestrator import licences, zeroshot
from orchestrator.agent import Agent, identify_context
from orchestrator.options import GROUPS, ZEROSHOT, Settings
from orchestrator.scene import SceneObject
from orchestrator.zeroshot import Ranking, ZeroShotHint, by_id, candidates_for, context_line
from tests.fakes import Hub

SQUARE = [[[600, 300], [660, 300], [660, 360], [600, 360]]]


class Rank:
    """A stand-in for the worker's `zeroshot` op: the names in reverse order, shares falling."""

    def __init__(self):
        self.calls = []

    async def __call__(self, jpegs, names, template):
        self.calls.append((jpegs, names, template))
        shares = np.linspace(0.3, 0.05, len(names))
        return [[[n, float(s)] for n, s in zip(reversed(names), shares)] for _ in jpegs]


def hint(option="medsiglip-subject", subject="a blood smear (Giemsa stain)", rank=None):
    settings = Settings()
    settings.zeroshot = option
    return ZeroShotHint(rank or Rank(), settings=lambda: settings, subject=lambda: subject)


# --- which names, for which subject --------------------------------------------------------------
@pytest.mark.parametrize("subject, wanted", [
    ("a blood smear (Giemsa stain)", "blood"),
    ("Peripheral blood with many erythrocytes", "blood"),
    ("a Pap smear (cervical cytology)", "cervical"),
    ("a Pap smear with some red blood cells", "cervical"),  # the more specific list first
    ("a leaf epidermis", None),
    ("pond water with a paramecium", None),
    ("", None),
    (None, None),
])
def test_the_subject_picks_the_list_and_other_specimens_get_none(subject, wanted):
    got = candidates_for(subject, "subject")
    assert (got.id if got else None) == wanted


def test_always_ranks_every_list_whatever_the_subject_and_off_ranks_nothing():
    everything = candidates_for("a leaf epidermis", "always")
    assert everything is by_id("always")
    assert {"neutrophil", "parabasal cell", "plant cell"} <= set(everything.names)
    assert len(everything.names) == len(set(everything.names))
    assert candidates_for("a blood smear", "off") is None


def test_the_lists_are_names_to_rank_not_diagnoses():
    for c in zeroshot.CANDIDATES:
        assert "{}" in c.template and c.calibration
        assert not any(w in " ".join(c.names) for w in ("carcinoma", "lsil", "hsil", "malignant", "koilocyte"))


# --- the line --------------------------------------------------------------------------------------
def test_the_line_is_one_line_of_data_with_what_it_has_been_worth():
    ranking = Ranking(by_id("blood"), (("neutrophil", 0.171), ("erythroblast", 0.15), ("basophil", 0.12), ("platelet", 0.1)))
    line = context_line(ranking)
    assert "\n" not in line
    assert "neutrophil 0.17, erythroblast 0.15, basophil 0.12" in line and "platelet" not in line.split("Measured")[0]
    assert "zero-shot" in line and "32%" in line  # what its first name has been worth, measured
    assert "clue, not its name" in line  # data, not a verdict: the model names it
    assert context_line(None) == "" and context_line(Ranking(by_id("blood"), ())) == ""
    assert ranking.wire() == {"model": "medsiglip", "list": "blood",
                              "top": [["neutrophil", 0.17], ["erythroblast", 0.15], ["basophil", 0.12]]}


def test_the_line_goes_after_the_specimen_and_nothing_changes_without_it():
    line = context_line(Ranking(by_id("blood"), (("neutrophil", 0.2),)))
    told = identify_context("a blood smear", zeroshot=line).splitlines()
    assert told[0].startswith("Earlier in this session you described the view as: a blood smear") and told[1] == line
    assert identify_context("a blood smear") == identify_context("a blood smear", zeroshot="")


# --- ranking a click -------------------------------------------------------------------------------
def frame():
    image = np.full((720, 1280, 3), 30, np.uint8)
    cv2.circle(image, (630, 330), 25, (200, 120, 180), -1)
    return image


def test_a_blood_click_sends_a_448_px_crop_and_the_subjects_names():
    rank = Rank()
    got = asyncio.run(hint(rank=rank).rank_crop(frame(), (600, 300, 660, 360), "o1", 7))
    (jpegs, names, template), = rank.calls
    crop = cv2.imdecode(np.frombuffer(jpegs[0], np.uint8), cv2.IMREAD_COLOR)
    assert crop.shape == (448, 448, 3)
    assert names == list(by_id("blood").names) and template == "a {} in a blood smear"
    assert got.candidates.id == "blood" and got.ranked[0] == ("red blood cell", pytest.approx(0.3))


@pytest.mark.parametrize("option, subject", [("off", "a blood smear"), ("medsiglip-subject", "a leaf epidermis"),
                                             ("medsiglip-subject", None)])
def test_nothing_is_ranked_when_off_or_for_another_specimen(option, subject):
    rank = Rank()
    assert asyncio.run(hint(option, subject, rank).rank_crop(frame(), (600, 300, 660, 360))) is None
    assert rank.calls == []


def test_always_ranks_a_leaf_click_too():
    rank = Rank()
    got = asyncio.run(hint("medsiglip-always", "a leaf epidermis", rank).rank_crop(frame(), (600, 300, 660, 360)))
    assert got.candidates.id == "always" and rank.calls[0][2] == "a microscopy image of a {}"


def test_it_warms_once_and_only_for_a_subject_it_has_names_for():
    rank = Rank()
    leaf = hint("medsiglip-subject", "a leaf epidermis", rank)
    asyncio.run(leaf.warm())
    assert rank.calls == []
    blood = hint(rank=rank)
    asyncio.run(blood.warm())
    asyncio.run(blood.warm())
    assert len(rank.calls) == 1


def make_hub(subject="a blood smear (Giemsa stain)"):
    hub = Hub()
    ok, jpeg = cv2.imencode(".jpg", frame())
    hub.infos = {7: hub.latest_info}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg.tobytes()) if fid == 7 else None,
                                 is_fresh=lambda: True)
    hub.scene.context["specimen"] = subject
    hub.settings.zeroshot = "medsiglip-subject"
    return hub


def name_a_click(hub, rank, tmp_path):
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    agent.zeroshot = ZeroShotHint(rank, settings=lambda: hub.settings, subject=lambda: hub.scene.context.get("specimen"),
                                  log=lambda r: logged.append(r))
    prompts, logged = [], []

    async def ask(images, prompt, max_output_tokens=0):
        prompts.append(prompt)
        return SimpleNamespace(data={"label": "neutrophil", "answer": "A cell with a lobed nucleus.", "confidence": 0.9},
                               model="stand-in", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    agent.log = logged.append
    agent.keep_image = lambda *a, **k: None

    async def click():
        obj = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                                        anchor_offset=(0.0, 0.0), source="click"))
        return await agent.identify(obj.id)

    return asyncio.run(click()), prompts, logged


def test_naming_a_click_is_told_the_ranking_and_the_model_keeps_its_name(tmp_path):
    hub = make_hub()
    out, prompts, logged = name_a_click(hub, Rank(), tmp_path)
    assert "MedSigLIP" in prompts[0] and "red blood cell 0.30" in prompts[0]
    assert out["label"] == hub.scene.objects["o1"].label == "neutrophil"  # the model's name, never the ranking's
    identify = next(r for r in logged if r["kind"] == "identify")
    assert identify["zeroshot"]["top"][0] == ["red blood cell", 0.3] and identify["zeroshot"]["list"] == "blood"
    assert any(r["kind"] == "zeroshot" and r["objectId"] == "o1" for r in logged)


def test_a_leaf_click_is_told_nothing(tmp_path):
    rank = Rank()
    _, prompts, logged = name_a_click(make_hub("a leaf epidermis"), rank, tmp_path)
    assert "MedSigLIP" not in prompts[0] and rank.calls == []
    assert next(r for r in logged if r["kind"] == "identify")["zeroshot"] is None


def test_without_a_worker_or_in_time_naming_goes_on_and_says_why_in_the_log(tmp_path, monkeypatch):
    async def broken(jpegs, names, template):
        raise RuntimeError("no GPU worker")

    out, prompts, logged = name_a_click(make_hub(), broken, tmp_path)
    assert out["label"] == "neutrophil" and "MedSigLIP" not in prompts[0]
    assert any(r["kind"] == "zeroshot" and "no GPU worker" in r.get("error", "") for r in logged)

    async def slow(jpegs, names, template):
        await asyncio.sleep(5)

    monkeypatch.setattr("orchestrator.agent.ZEROSHOT_WAIT_S", 0.05)
    out, prompts, logged = name_a_click(make_hub(), slow, tmp_path)
    assert out["label"] == "neutrophil" and "MedSigLIP" not in prompts[0]
    assert any(r["kind"] == "zeroshot" and "TimeoutError" in r.get("error", "") for r in logged)


# --- the option, its licence and the worker -----------------------------------------------------------
def test_the_option_runs_on_medsiglip_under_its_terms_in_every_edition(monkeypatch):
    assert GROUPS["zeroshot"] is ZEROSHOT and "off" in ZEROSHOT
    for option in ("medsiglip-subject", "medsiglip-always"):
        assert ZEROSHOT[option].uses == ("medsiglip",) and ZEROSHOT[option].tier == "terms"
    component = licences.COMPONENTS["medsiglip"]
    assert component.tier == "terms" and "Health AI Developer Foundations" in component.licence
    from gpu_worker.worker import licence_refusal

    for edition in ("commercial", "research"):
        monkeypatch.setenv("MICROMIND_EDITION", edition)
        assert licence_refusal({"op": "zeroshot", "jpegs": [], "names": []}) is None
        assert ZEROSHOT["medsiglip-subject"].permitted
    assert "zeroshot" in Settings().to_wire()


def test_the_worker_scales_crops_as_siglip_was_trained_and_shares_sum_to_one():
    batch = pixels([np.zeros((448, 448, 3), np.uint8), np.full((100, 60, 3), 255, np.uint8)])
    assert batch.shape == (2, 3, 448, 448) and batch.min() == -1.0 and batch.max() == 1.0
    shares = softmax_shares(np.array([[10.0, 9.0, 1.0], [0.0, 0.0, 0.0]]))
    assert np.allclose(shares.sum(axis=1), 1.0) and shares[0].argmax() == 0 and np.allclose(shares[1], 1 / 3)
