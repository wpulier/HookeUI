"""Cellpose-SAM's device self-check (ticket 62, F-L8-1): a device that answers wrongly is never the one that makes a result,
and a result of nothing is said as nothing found, with how its device was proven."""
import asyncio
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import torch

from noncommercial import cellpose_device
from tests.test_capability_matrix import world  # noqa: F401 - keys, code and the cloud stood in for

ADAPTER = Path(__file__).resolve().parents[2] / "examples" / "beta-scientific-tools" / "cellposesam-panel" / "adapter.py"
CPU, GPU = torch.device("cpu"), torch.device("meta")  # "meta" stands in for a GPU: any device that isn't the CPU


def reference_masks():
    """A label map holding exactly the module's reference cells (discs at their centroids, of their areas)."""
    masks = np.zeros((128, 128), np.uint16)
    rows, cols = np.mgrid[0:128, 0:128]
    for n, (area, row, col) in enumerate(cellpose_device.REFERENCE, start=1):
        masks[(rows - row) ** 2 + (cols - col) ** 2 <= area / np.pi] = n
    return masks


class Model:
    """A stand-in Cellpose model: what it answers on the probe depends on where it runs and in which precision."""
    def __init__(self, device, bf16, answers):
        self.device, self.bf16, self.answers = device, bf16, answers

    def eval(self, picture, **settings):
        assert picture.shape == (128, 128) and settings == {"batch_size": 1, **cellpose_device.PROBE_SETTINGS}
        return self.answers(self.device, self.bf16), None, None


def maker(answers, built):
    def make(device, bf16):
        built.append((device.type, bf16))
        return Model(device, bf16, answers)
    return make


def test_the_reference_is_the_cpus_cells_on_the_pinned_probe():
    assert cellpose_device.REFERENCE and all(area > 15 for area, _, _ in cellpose_device.REFERENCE)
    assert cellpose_device.probe().shape == (128, 128)  # the bundled CC0 specimen's crop, at its pinned values


def test_agreement_is_the_cells_found_again_not_identical_bytes():
    masks = reference_masks()
    assert cellpose_device.agrees(masks)[0]
    assert cellpose_device.agrees(np.roll(masks, 1, axis=0))[0]  # a GPU's last-bit differences move edges by a pixel
    ok, seen = cellpose_device.agrees(np.zeros_like(masks))
    assert not ok and seen.startswith("found 0 cells")
    shuffled = np.roll(masks, 20, axis=1)
    assert not cellpose_device.agrees(shuffled)[0]


def test_a_gpu_that_answers_zero_cells_is_never_used_and_the_fallback_is_said(tmp_path):
    built = []
    zero_on_gpu = lambda device, bf16: np.zeros((128, 128), np.uint16) if device.type != "cpu" else reference_masks()
    model, proof = cellpose_device.choose(maker(zero_on_gpu, built), GPU, record=tmp_path / "device-check.json")
    assert model.device.type == "cpu" and built == [("meta", True), ("meta", False), ("cpu", True)]
    assert proof["device"] == "cpu" and proof["says"] == cellpose_device.ON_CPU_WORDS
    assert [t["agrees"] for t in proof["tried"]] == [False, False]
    kept = json.loads((tmp_path / "device-check.json").read_text())
    assert list(kept.values())[0]["device"] == "cpu"
    built.clear()  # the same machine and setup: the kept verdict, no probe
    model, again = cellpose_device.choose(maker(zero_on_gpu, built), GPU, record=tmp_path / "device-check.json")
    assert built == [("cpu", True)] and again["kept"] and again["says"] == cellpose_device.ON_CPU_WORDS


def test_a_gpu_wrong_only_in_half_precision_runs_in_full_precision(tmp_path):
    built = []
    half_wrong = lambda device, bf16: np.zeros((128, 128), np.uint16) if bf16 else reference_masks()
    model, proof = cellpose_device.choose(maker(half_wrong, built), GPU, record=tmp_path / "r.json")
    assert (model.device.type, model.bf16) == ("meta", False) and proof["dtype"] == "float32"
    assert "full precision" in proof["says"]


def test_a_working_gpu_is_used_as_it_is_and_says_nothing_extra(tmp_path):
    built = []
    model, proof = cellpose_device.choose(maker(lambda d, b: reference_masks(), built), GPU, record=tmp_path / "r.json")
    assert built == [("meta", True)] and proof["device"] == "meta" and "says" not in proof
    assert proof["checked"].startswith(f"found the {len(cellpose_device.REFERENCE)} cells")


def test_a_check_that_cant_run_is_not_a_pass(tmp_path, monkeypatch):
    monkeypatch.setattr(cellpose_device, "PROBE_FILE", tmp_path / "missing.png")
    built = []
    model, proof = cellpose_device.choose(maker(lambda d, b: reference_masks(), built), GPU, record=tmp_path / "r.json")
    assert built == [("cpu", True)] and "check picture couldn't be read" in proof["says"] and "wrong results" not in proof["says"]


def test_a_device_that_raises_on_the_probe_is_not_called_wrong_and_is_probed_again(tmp_path):
    def raises(device, bf16):
        if device.type != "cpu":
            raise RuntimeError("MPS backend out of memory")
        return reference_masks()
    record = tmp_path / "r.json"
    model, proof = cellpose_device.choose(maker(raises, []), GPU, record=record)
    assert proof["device"] == "cpu" and "RuntimeError" in proof["tried"][0]["seen"]
    assert proof["says"] == cellpose_device.CHECK_FAILED_WORDS and "wrong results" not in proof["says"]
    assert not record.exists()  # never kept: the next load probes again
    built = []
    cellpose_device.choose(maker(lambda d, b: reference_masks(), built), GPU, record=record)
    assert built == [("meta", True)]


def test_a_verdict_is_for_one_machine_a_new_one_probes_again(tmp_path, monkeypatch):
    record, built = tmp_path / "r.json", []
    zero_on_gpu = lambda device, bf16: np.zeros((128, 128), np.uint16) if device.type != "cpu" else reference_masks()
    monkeypatch.setattr(cellpose_device, "hardware", lambda: {"model": "VirtualMac2,1", "vm": "1"})
    cellpose_device.choose(maker(zero_on_gpu, built), GPU, record=record)
    assert built == [("meta", True), ("meta", False), ("cpu", True)]
    built.clear()
    monkeypatch.setattr(cellpose_device, "hardware", lambda: {"model": "Mac15,9", "vm": "0"})  # same software, a real Mac
    _, proof = cellpose_device.choose(maker(lambda d, b: reference_masks(), built), GPU, record=record)
    assert built == [("meta", True)] and proof["device"] == "meta" and "kept" not in proof
    assert set(cellpose_device.signature(GPU, None)) >= {"model", "vm", "macos", "torch", "cellpose", "device"}


def adapter():
    spec = importlib.util.spec_from_file_location("cellposesam_adapter_device", ADAPTER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_panel_checks_with_the_same_probe_reference_and_rule():
    panel = adapter()
    assert panel.REFERENCE == cellpose_device.REFERENCE and panel.PROBE_SETTINGS == cellpose_device.PROBE_SETTINGS
    assert panel.PROBE_SHA256 == cellpose_device.PROBE_SHA256 and panel.ON_CPU_WORDS == cellpose_device.ON_CPU_WORDS
    assert (panel.CENTROID_PX, panel.AREA_RATIO) == (cellpose_device.CENTROID_PX, cellpose_device.AREA_RATIO)
    masks = reference_masks()
    for case in (masks, np.zeros_like(masks), np.roll(masks, 20, axis=1)):
        assert panel.probe_agrees(case)[0] == cellpose_device.agrees(case)[0]
    from PIL import Image  # the package ships the probe as its example, at the pinned values
    assert np.array_equal(np.asarray(Image.open(panel.PROBE_FILE)), cellpose_device.probe())


def test_the_panel_never_runs_on_a_gpu_that_answers_zero_cells():
    panel = adapter()
    assign = lambda gpu: (GPU, True)
    made = []

    def make(device, bf16):
        made.append((device.type, bf16))
        return Model(device, bf16, lambda d, b: np.zeros((128, 128), np.uint16))
    device, bf16, said = panel.check_device("cpsam_v2", assign, make)
    assert device.type == "cpu" and made == [("meta", True), ("meta", False)]
    assert said["says"].startswith(panel.ON_CPU_WORDS)
    made.clear()
    device, bf16, said = panel.check_device("cpsam_v2", assign, lambda d, b: Model(d, b, lambda *_: reference_masks()))
    assert device.type == "meta" and bf16 and said["says"].startswith("Checked this Mac's GPU for Cellpose-SAM: it found")


def test_nothing_found_is_said_as_nothing_found_with_how_its_device_was_proven(world, monkeypatch):
    from orchestrator.commands import handle
    from orchestrator.evidence import evidence_of
    from tests import capability_rig as setups
    from tests.capability_rig import Rig
    from tests.standin_workers import StandInWorker
    from tests.test_actions_panel import Client, reply

    from gpu_worker.worker import encode_labels

    proven = {"device": "mps", "dtype": "bfloat16", "checked": "found the 6 cells the CPU finds on the check picture"}
    monkeypatch.setattr(StandInWorker, "_cells", lambda self, payload: {
        "labels": encode_labels(np.zeros((720, 1280), np.uint16)), "scores": {}, "score": "cellprob", "polygons": [],
        "count": 0, "producer": {"device": dict(proven)}})
    rig = Rig(world["root"] / "none")
    asyncio.run(setups.prepare(rig, ("still",)))
    client = Client()
    asyncio.run(handle(rig.app, client, {"type": "action", "capability": "cells", "args": {}}))
    said = reply(client)["said"]
    assert said.startswith("Found no cells") and "Outlined and measured" not in said
    assert "Its device was checked first: it found the 6 cells the CPU finds on the check picture." in said
    assert evidence_of(rig.hub).latest().producer["device"]["device"] == "mps"

    fallback = {"device": "cpu", "dtype": "bfloat16", "checked": "the CPU is the reference device",
                "says": cellpose_device.ON_CPU_WORDS}
    monkeypatch.setattr(StandInWorker, "_cells", lambda self, payload: {
        "labels": encode_labels(np.zeros((720, 1280), np.uint16)), "scores": {}, "score": "cellprob", "polygons": [],
        "count": 0, "producer": {"device": dict(fallback)}})
    client = Client()
    asyncio.run(handle(rig.app, client, {"type": "action", "capability": "cells", "args": {}}))
    said = reply(client)["said"]
    assert said.startswith("Found no cells") and "It ran on the CPU, the reference device." in said
    assert f"{cellpose_device.ON_CPU_WORDS}." in said


@pytest.mark.skipif(not __import__("os").environ.get("MICROMIND_REAL_CELLPOSE"),
                    reason="runs Cellpose-SAM itself: set MICROMIND_REAL_CELLPOSE=1 on a machine with its pinned weights")
def test_the_real_cpu_finds_the_reference_on_the_probe():
    from cellpose import models

    from noncommercial.cellpose_cells import pinned_weights

    model = models.CellposeModel(gpu=False, device=CPU, pretrained_model=str(pinned_weights()))
    masks = model.eval(cellpose_device.probe(), batch_size=1, **cellpose_device.PROBE_SETTINGS)[0]
    assert cellpose_device.agrees(masks)[0]
    import hashlib
    assert hashlib.sha256(np.asarray(masks).astype("<u2").tobytes()).hexdigest().startswith(cellpose_device.REFERENCE_MASKS_SHA256)


class Network:
    """A plugin- or worker-shaped stand-in for cellpose.models.CellposeModel: zero cells on any GPU, the reference on
    the CPU. `made` keeps what each instance was built with."""
    made: list = []

    def __init__(self, gpu=False, pretrained_model="cpsam_v2", model_type=None, diam_mean=None, device=None, nchan=None,
                 use_bfloat16=True):
        self.device = device if device is not None else CPU
        Network.made.append({"device": self.device.type, "bf16": use_bfloat16, "weights": str(pretrained_model)})
        self.net = self

    def eval(self, image, **settings):
        if self.device.type != "cpu":
            return np.zeros(image.shape[:2], np.uint16), [None, None, np.full(image.shape[:2], -0.5, np.float32)], None
        if image.shape[:2] == (128, 128):
            return reference_masks(), [None, None, np.zeros((128, 128), np.float32)], None
        labels = np.zeros(image.shape[:2], np.uint16)
        labels[10:20, 10:20], labels[30:40, 30:45] = 1, 2
        return labels, [None, None, np.zeros(image.shape[:2], np.float32)], None


@pytest.fixture
def worker_stand_in(tmp_path, monkeypatch):
    """The worker's Cellpose path over the stand-in network, with the pinned weights in a read-only published revision."""
    import cellpose.models

    from gpu_worker import model_files
    from noncommercial import cellpose_cells

    model_folder = tmp_path / "models" / "cellpose-sam"
    revision = model_folder / "7c61431b"
    revision.mkdir(parents=True)
    (revision / "cpsam_v2").write_bytes(b"weights")
    revision.chmod(0o555)  # a published revision is read-only
    Network.made = []
    monkeypatch.setattr(cellpose.models, "CellposeModel", Network)
    monkeypatch.setattr(cellpose_cells, "pinned_weights", lambda: revision / "cpsam_v2")
    monkeypatch.setattr(model_files, "folder", lambda m, environ=None: model_folder)
    yield model_folder
    revision.chmod(0o755)


def test_the_worker_never_outlines_with_a_gpu_that_answers_zero_cells(worker_stand_in):
    import cv2

    from noncommercial.cellpose_cells import CellposeCells

    finder = CellposeCells(GPU)
    assert [m["device"] for m in Network.made] == ["meta", "meta", "cpu"]
    jpeg = cv2.imencode(".jpg", np.full((60, 80, 3), 120, np.uint8))[1].tobytes()
    out = finder.outline({"jpeg": jpeg})
    assert out["count"] == 2 and out["producer"]["device"]["device"] == "cpu"
    assert out["producer"]["device"]["says"] == cellpose_device.ON_CPU_WORDS and "tried" not in out["producer"]["device"]
    # The verdict is kept beside the revisions (the revision itself is read-only), so the next load doesn't probe.
    assert (worker_stand_in / cellpose_device.RECORD).is_file()
    Network.made = []
    again = CellposeCells(GPU)
    assert [m["device"] for m in Network.made] == ["cpu"] and again.proof["kept"]


def test_your_own_cellpose_model_runs_without_the_base_download_on_the_cpu_said(tmp_path, monkeypatch):
    import cellpose.models

    from gpu_worker.model_files import NotDownloaded
    from noncommercial import cellpose_cells

    class Entry:
        id, namespace, name = "cellpose-sam", "", "Cellpose-SAM"

    def missing():
        raise NotDownloaded(Entry(), 1)
    Network.made = []
    monkeypatch.setattr(cellpose.models, "CellposeModel", Network)
    monkeypatch.setattr(cellpose_cells, "pinned_weights", missing)
    finder = cellpose_cells.CellposeTunedCells(GPU, str(tmp_path / "mine" / "weights"))
    assert Network.made == [{"device": "cpu", "bf16": True, "weights": str(tmp_path / "mine" / "weights")}]
    assert finder.proof["says"] == cellpose_device.NO_BASE_WORDS and "kept" not in finder.proof
    with pytest.raises(NotDownloaded):
        cellpose_cells.CellposeCells(GPU)  # the base network itself still needs its download


def test_the_panel_passes_the_proven_precision_to_a_plugin_that_reads_the_signature(monkeypatch):
    """The pinned plugin passes use_bfloat16 only when inspect.signature(models.CellposeModel) lists it."""
    import inspect

    import cellpose.core
    import cellpose.models

    panel = adapter()
    half_wrong = []

    class HalfWrong(Network):
        def eval(self, image, **settings):
            if self.device.type != "cpu" and Network.made[-1]["bf16"]:
                return np.zeros(image.shape[:2], np.uint16), None, None
            return reference_masks(), None, None

    monkeypatch.setattr(cellpose.core, "_hooke_originals", (lambda gpu=True: (GPU, True), HalfWrong), raising=False)
    saved = (cellpose.core.assign_device, cellpose.models.CellposeModel)
    try:
        Network.made = []
        verdict = panel.guard_device("cpsam_v2")

        def plugin_run():  # the shape of napari_cellposesam._dock_widget.run_cellpose under cellpose 4
            from cellpose import models
            from cellpose.core import assign_device
            device, gpu = assign_device(gpu=True)
            kwargs = dict(gpu=gpu, device=device, pretrained_model="cpsam_v2")
            if "use_bfloat16" in inspect.signature(models.CellposeModel).parameters:
                kwargs["use_bfloat16"] = device.type != "cpu"
            return models.CellposeModel(**kwargs)
        plugin_run()
        assert verdict["device"].type == "meta" and verdict["bf16"] is False
        assert Network.made[-1] == {"device": "meta", "bf16": False, "weights": "cpsam_v2"}  # full precision, as proven
        assert "full precision" in verdict["said"]["says"]
    finally:
        cellpose.core.assign_device, cellpose.models.CellposeModel = saved


def test_the_agents_words_carry_the_device_and_its_proof():
    from orchestrator.evidence import Population

    fields = Population.__dataclass_fields__
    def population(device):
        p = Population.__new__(Population)
        for name, f in fields.items():
            default = f.default if f.default is not __import__("dataclasses").MISSING else (
                f.default_factory() if f.default_factory is not __import__("dataclasses").MISSING else None)
            setattr(p, name, default)
        p.members, p.revisions, p.revision, p.view, p.made_at = {}, [], 0, {"at": 0}, 0
        p.implementation, p.operation, p.producer = "cellpose-sam", "cells", {"device": device}
        return p
    words = population({"device": "cpu", "says": cellpose_device.ON_CPU_WORDS}).count_words()
    assert words.startswith("0 instances segmented by cellpose-sam") and words.endswith(cellpose_device.ON_CPU_WORDS)
    words = population({"device": "mps", "checked": "found the 6 cells the CPU finds on the check picture"}).count_words()
    assert words.endswith("on this Mac's GPU, checked first: it found the 6 cells the CPU finds on the check picture")


def test_a_crash_in_half_precision_is_not_wrong_results_and_its_verdict_is_not_kept(tmp_path):
    record, built = tmp_path / "r.json", []
    def half_crashes(device, bf16):
        if device.type != "cpu" and bf16:
            raise RuntimeError("MPS backend out of memory")
        return reference_masks()
    model, proof = cellpose_device.choose(maker(half_crashes, built), GPU, record=record)
    assert (model.device.type, model.bf16) == ("meta", False) and proof["says"] == cellpose_device.HALF_UNCHECKED_WORDS
    assert "wrong results" not in proof["says"] and not record.exists()


def test_an_unreadable_check_picture_is_not_wrong_results(tmp_path, monkeypatch):
    monkeypatch.setattr(cellpose_device, "PROBE_FILE", tmp_path / "missing.png")
    _, proof = cellpose_device.choose(maker(lambda d, b: reference_masks(), []), GPU, record=tmp_path / "r.json")
    assert proof["says"] == cellpose_device.NO_PICTURE_WORDS and "wrong results" not in proof["says"]


def test_the_panel_words_a_probe_crash_as_a_check_that_couldnt_finish():
    panel = adapter()
    assert (panel.CHECK_FAILED_WORDS, panel.NO_PICTURE_WORDS, panel.HALF_UNCHECKED_WORDS) == (
        cellpose_device.CHECK_FAILED_WORDS, cellpose_device.NO_PICTURE_WORDS, cellpose_device.HALF_UNCHECKED_WORDS)
    def crashes(device, bf16):
        raise RuntimeError("MPS backend out of memory")
    device, bf16, said = panel.check_device("cpsam_v2", lambda gpu: (GPU, True), crashes)
    assert device.type == "cpu" and said["says"].startswith(panel.CHECK_FAILED_WORDS) and "wrong results" not in said["says"]
    def half_crashes(device, bf16):
        if bf16:
            raise RuntimeError("MPS backend out of memory")
        return Model(device, bf16, lambda *_: reference_masks())
    device, bf16, said = panel.check_device("cpsam_v2", lambda gpu: (GPU, True), half_crashes)
    assert (device.type, bf16) == ("meta", False) and said["says"].startswith(panel.HALF_UNCHECKED_WORDS)


def test_each_process_writes_the_verdict_through_its_own_temporary_file(tmp_path, monkeypatch):
    import os
    seen = []
    real = os.replace
    monkeypatch.setattr(os, "replace", lambda a, b: (seen.append(str(a)), real(a, b)))
    cellpose_device._write(tmp_path / "r.json", {"k": 1})
    assert seen == [str((tmp_path / "r.json").with_suffix(f".{os.getpid()}.tmp"))]
