"""Calibrated area and comparing methods, not only models (ticket 59's MT6; the butterfly-wing forum case). Synthetic
masks with known areas; the calibrations are stand-ins in the labels' own calibration shape (axes, units, affine)."""
import json

import numpy as np
import pytest

from orchestrator import calibrated_area, method_comparison, scientific_resource_actions as actions, task_facts
from orchestrator.scientific_projects import ProjectStore
from tests.test_scientific_arrays import fill

SPACE = {"session": "s1", "id": "stage", "revision": 1}
ANISOTROPIC = {"space": SPACE, "axes": ["y", "x"], "units": ["µm", "nm"], "affine": [[0.5, 0, 0], [0, 250, 0]]}
SOURCE = {"id": "wing-scale-12", "revision": 4, "digest": "e" * 64}


def wing() -> tuple[np.ndarray, np.ndarray]:
    """A: the membrane only, 10×20 pixels; B: the membrane and its fringe scales, two more pixels on every side."""
    a = np.zeros((40, 60), bool)
    a[10:20, 20:40] = True
    b = np.zeros_like(a)
    b[8:22, 18:42] = True
    return a, b


def test_one_pixel_is_the_product_of_each_axis_scale_in_micrometres():
    assert calibrated_area.element_size(ANISOTROPIC, ["y", "x"]) == (0.5 * 0.25, "")
    mm = {"axes": ["z", "y", "x"], "units": ["mm", "um", "micron"], "affine": [[0.002, 0, 0, 0], [0, 0.1, 0, 0],
                                                                                [0, 0, 0.1, 0]]}
    size, _ = calibrated_area.element_size(mm, ["z", "y", "x"])
    assert size == pytest.approx(2.0 * 0.1 * 0.1) and calibrated_area.unit(3) == "µm³"


@pytest.mark.parametrize("calibration,words", [
    (None, "a count of pixels"),
    ({"axes": ["y", "x"], "units": ["µm", "µm"], "affine": [[0.5, 0.1, 0], [0, 0.5, 0]]}, "rotates or shears"),
    ({"axes": ["y", "x"], "units": ["µm", "pixel"], "affine": [[0.5, 0, 0], [0, 0.5, 0]]}, "not a length"),
    ({"axes": ["y", "x"], "units": ["µm", "µm"], "affine": [[0.5, 0, 0], [0, 0, 0]]}, "no scale"),
    ({"axes": ["y"], "units": ["µm"], "affine": [[0.5, 0]]}, "axis x has no calibration"),
])
def test_an_unusable_calibration_keeps_pixels_and_says_why(calibration, words):
    size, why = calibrated_area.element_size(calibration, ["y", "x"])
    assert size is None and words in why


def labelled(tmp_path, calibration):
    store = ProjectStore(tmp_path / "projects")
    original = store.create("s1", {"kind": "scene", "title": "Wing", "state": {}, "resources": [], "runtimes": []})
    data = np.zeros((4, 6), np.uint8)
    data[0:2, 0:3] = 1
    data[3, :] = 2
    scientific = {"semantic": "instance-labels", **({"calibration": calibration} if calibration else {})}
    draft = store.array_draft(original.id, "s1", component="array.labels", shape=data.shape, dtype=data.dtype.str,
                              axes=["y", "x"], chunks=[2, 6], scientific=scientific, max_written_bytes=data.nbytes)
    ref = fill(draft, data)
    doc = original.document
    doc["resources"] = [ref]
    doc["state"]["scientificView"] = {"inputs": {"labels": ref}}
    return store, store.save(original.id, "s1", doc, expected_revision=1)


def test_measurements_add_a_calibrated_area_only_from_the_labels_own_calibration(tmp_path):
    store, saved = labelled(tmp_path / "cal", ANISOTROPIC)
    measured = actions.measure(store, saved.id, "s1", saved.revision, "labels")
    table = actions.table(store, saved.id, "s1", measured.revision)
    assert table["columns"][-1] == "area_um2"
    assert [row[-1] for row in table["rows"]] == [pytest.approx(6 * 0.125), pytest.approx(6 * 0.125)]
    store, saved = labelled(tmp_path / "none", None)
    measured = actions.measure(store, saved.id, "s1", saved.revision, "labels")
    table = actions.table(store, saved.id, "s1", measured.revision)
    assert "area_um2" not in table["columns"], "no calibration: pixel counts only, never a guessed unit"


def arms(**more):
    a, b = wing()
    return [{"id": "a", "factor": "boundary-definition", "says": "membrane only", "mask": a, "corrections": []},
            {"id": "b", "factor": "boundary-definition", "says": "membrane including the fringe scales", "mask": b,
             "corrections": [{"kind": "erase", "pixels": 3}]}, *more.get("extra", [])]


def test_two_boundary_definitions_are_measured_and_never_ranked(tmp_path):
    summary = method_comparison.summarize(SOURCE, ANISOTROPIC, arms())
    a, b = summary["arms"]
    assert (a["pixels"], b["pixels"]) == (200, 336) and a["unit"] == "µm²" and summary["calibrated"] is True
    assert a["area"] == pytest.approx(200 * 0.125) and b["area"] == pytest.approx(336 * 0.125)
    diff = summary["differences"][0]
    assert diff["absolute"] == pytest.approx(136 * 0.125) and diff["relative"] == pytest.approx(136 / 200)
    assert summary["disagreement"][0]["onlyA"] == 0 and summary["disagreement"][0]["onlyB"] == pytest.approx(17.0)
    assert summary["ranked"] is False and "never which is more accurate" in summary["says"]
    assert b["corrections"] == [{"kind": "erase", "pixels": 3}]

    with pytest.raises(method_comparison.Invalid, match="rationale"):
        method_comparison.choose(summary, "b", "  ")
    chosen = method_comparison.choose(summary, "b", "the hypothesis is about the scale's whole optical footprint")
    assert chosen["chosen"]["arm"] == "b" and chosen["chosen"]["says"].endswith("fringe scales")
    assert [r["id"] for r in chosen["arms"]] == ["a", "b"], "every alternative stays in the record"
    kept = method_comparison.export(chosen, tmp_path / "comparison.json")
    back = json.loads((tmp_path / "comparison.json").read_text())
    assert back["source"] == SOURCE and back["calibration"] == ANISOTROPIC and back["chosen"]["rationale"]
    assert len(kept["sha256"]) == 64


def test_no_scale_keeps_pixels_said_and_a_third_algorithm_arm_is_agreement_not_accuracy():
    a, _ = wing()
    c = np.roll(a, 1, axis=1)
    summary = method_comparison.summarize(SOURCE, None, arms(extra=[
        {"id": "c", "factor": "algorithm", "says": "Otsu threshold on the same definition", "mask": c}]))
    assert summary["calibrated"] is False and "count of pixels" in summary["why"]
    assert {r["unit"] for r in summary["arms"]} == {"pixel"}
    assert summary["disagreement"][1] == {"arms": ["a", "c"], "onlyA": 10.0, "onlyC": 10.0, "unit": "pixel"}
    assert summary["ranked"] is False and "not accuracy" in summary["says"]


@pytest.mark.parametrize("change,words", [
    (lambda r: r[1].pop("factor"), "declares the factor"),
    (lambda r: r[1].update(says=" "), "says what it is"),
    (lambda r: r[1].update(mask=np.zeros((3, 3), bool)), "differ in size"),
    (lambda r: r[1].update(id="c"), "compare 2 to 6 arms"),
])
def test_an_arm_must_declare_its_factor_on_the_same_frozen_source(change, words):
    rows = arms()
    change(rows)
    with pytest.raises(method_comparison.Invalid, match=words):
        method_comparison.summarize(SOURCE, ANISOTROPIC, rows)
    with pytest.raises(method_comparison.Invalid, match="freeze the source"):
        method_comparison.summarize({"id": "x"}, ANISOTROPIC, arms())


def test_what_stands_between_a_task_and_doing_it_is_told_apart():
    ready = {"key": "otsu", "status": "ready"}
    setup = {"key": "cellpose", "name": "Cellpose", "status": "needs-setup", "next": "Set up"}
    away = {"key": "cuda-only", "status": "unavailable", "why": "needs an NVIDIA GPU", "steps": [{"kind": "platform", "says": "needs an NVIDIA GPU"}]}
    say = task_facts.blockers
    assert say(have_input=False, calibrated=None, needs_calibration=True, camera=False, candidates=[ready])[
        "blockers"] == [{"reason": "missing-input", "upload": True, "capture": False}]
    assert say(have_input=True, calibrated=False, needs_calibration=True, camera=True, candidates=[ready])[
        "blockers"] == [{"reason": "missing-calibration", "pixelsOnly": True}]
    assert say(have_input=True, calibrated=True, needs_calibration=True, camera=True, candidates=[ready]) == {
        "ready": ["otsu"], "blockers": []}
    assert [b["reason"] for b in say(have_input=True, calibrated=True, needs_calibration=True, camera=False,
                                     candidates=[setup, away])["blockers"]] == ["unprepared", "unavailable-hardware"]
    hardware = say(have_input=True, calibrated=True, needs_calibration=False, camera=False, candidates=[away])['blockers'][0]
    assert hardware['reason'] == 'unavailable-hardware'
    assert hardware['capabilities'][0]['why'] == 'needs an NVIDIA GPU'
    assert hardware['capabilities'][0]['steps'] == away['steps']
    told = say(have_input=True, calibrated=True, needs_calibration=False, camera=False, candidates=[],
               permission="read the shared drive", judgment="whether the fringe belongs to the scale")
    assert [b["reason"] for b in told["blockers"]] == ["missing-integration", "missing-permission",
                                                       "scientific-judgment"]
