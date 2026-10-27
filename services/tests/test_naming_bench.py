"""The naming bench's scoring and eyepiece composite (bench/naming): no models, no downloads."""
import numpy as np

from bench.naming import composite
from bench.naming.score import abstained, consistency, context_leak, ece, judge, summarise
from orchestrator.agent import identify_images
from orchestrator.marks import fov_crop

BLOOD = "acevedo_et_al_2020"


def test_fine_and_coarse_matches_use_the_apps_label_rules():
    assert judge(BLOOD, "neutrophil", "Neutrophils (likely)") == {"abstained": False, "fine": True, "coarse": True}
    assert judge(BLOOD, "neutrophil", "band neutrophil")["fine"]
    assert judge(BLOOD, "neutrophil", "lymphocyte") == {"abstained": False, "fine": False, "coarse": True}  # both white cells
    assert judge(BLOOD, "neutrophil", "nucleus") == {"abstained": False, "fine": False, "coarse": False}
    assert judge(BLOOD, "neutrophil", "white blood cell") == {"abstained": False, "fine": False, "coarse": True}
    assert judge(BLOOD, "neutrophil", "leukocyte")["coarse"]  # the app's SYNONYMS
    assert judge(BLOOD, "ig", "metamyelocyte")["fine"]
    assert judge(BLOOD, "platelet", "thrombocyte")["fine"]
    assert not judge(BLOOD, "basophil", "red blood cell")["coarse"]
    assert judge(BLOOD, "erythroblast", "lymphocyte") == {"abstained": False, "fine": False, "coarse": True}  # a blood cell
    assert judge("hussain_et_al_2019", "low-grade-lsil", "parabasal cell") == {"abstained": False, "fine": None, "coarse": True}
    assert judge("icpr2020_pollen", "Corylus avellana (well-developed)", "hazel pollen grain")["fine"]
    assert judge("icpr2020_pollen", "Alnus (well-developed)", "pollen grain") == {"abstained": False, "fine": False, "coarse": True}


def test_unsure_answers_are_abstentions():
    for name in ("", "Not sure", "unclear structure", "can't tell yet"):
        assert abstained(name)
    assert not abstained("stoma")
    assert judge(BLOOD, "monocyte", "not sure") == {"abstained": True, "fine": False, "coarse": False}


def test_ece():
    assert ece([(0.9, True), (0.9, True), (0.1, False), (0.1, False)]) == 0.1
    assert ece([(1.0, False), (1.0, False)]) == 1.0
    assert ece([(None, True)]) is None


def row(image, condition, name, repeat=0, label="neutrophil", confidence=0.8):
    return {"option": "m/identify", "dataset": BLOOD, "label": label, "image": image, "condition": condition,
            "repeat": repeat, "name": name, "confidence": confidence, "ms": 100}


def test_context_leak_and_consistency():
    rows = [row("a", "right", "neutrophil"), row("a", "none", "neutrophil"), row("a", "wrong", "guard cell"),
            row("a", "grey", "white blood cell"), row("b", "right", "lymphocyte"), row("b", "none", "monocyte"),
            row("b", "wrong", "lymphocyte"), row("b", "grey", "not sure"), row("a", "right", "neutrophil", repeat=1)]
    leak = context_leak(rows)
    assert leak == {"changedWithoutContext": 0.5, "changedWithWrongContext": 0.5, "greyImageNamedFromContext": 0.5, "pictures": 2}
    assert consistency(rows) == 1.0
    summary = summarise(rows)["m/identify"]
    assert summary["right"]["n"] == 3 and summary["right"]["fine"] == 0.667
    assert summary["grey"]["abstained"] == 0.5
    assert summary["right"]["confusions"] == {"neutrophil": {"lymphocyte": 1}}


def test_the_composite_looks_like_an_eyepiece():
    picture = np.full((360, 360, 3), (90, 60, 200), np.uint8)
    frame = composite.eyepiece(picture, seed=1)
    assert frame.shape == (composite.HEIGHT, composite.WIDTH, 3)
    cx, cy, r = int(composite.CENTRE[0]), int(composite.CENTRE[1]), composite.RADIUS
    assert frame[5, 5].max() < 10  # black surround
    centre = frame[cy - 5 : cy + 5, cx - 5 : cx + 5].mean()
    edge = frame[cy - 5 : cy + 5, cx + r - 30 : cx + r - 20].mean()
    assert centre > edge * 1.2  # vignetting
    grey = composite.eyepiece(None, seed=1)
    assert abs(float(grey[cy, cx].mean()) - 150) < 15


def test_the_app_names_the_composite_with_its_own_images():
    frame = composite.eyepiece(np.full((300, 300, 3), 120, np.uint8))
    polygons, box = composite.object_outline((300, 300))
    view, close = identify_images(frame, polygons, fov_crop(composite.fov(), frame.shape[1], frame.shape[0]), box)
    assert view[:2] == b"\xff\xd8" and close[:2] == b"\xff\xd8"
    polygons, box = composite.object_outline((300, 300), bbox=[100, 100, 200, 200])
    assert round(box[2] - box[0], 6) == round(box[3] - box[1], 6) == 220.0
