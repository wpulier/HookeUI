"""Fine-tuning Cellpose-SAM on the user's corrected cells (session knowledge K4, contract 5; ADR 0005, 0013): an explicit
job outside the app, trained with cellpose's own trainer on the corrections export, whole days held out (a session is a
day, and never a view it trained on), written as a new version (never over an earlier one) with its licence tier, and a
CELLS implementation that is available only once its weights exist, refused in the commercial edition, runnable in the
research one, told apart by version in the outcomes, and judged by the replay bench only on what it did not train on."""
import asyncio
import json
import time
from types import SimpleNamespace

import numpy as np
import pytest
import torch

from bench import bundle as bundles
from bench import finetune_cells as job
from bench import live
from bench import replay_bench as replay
from gpu_worker.worker import Models, licence_refusal
from orchestrator import finetuned, licences, population as populations
from orchestrator.operations import OPERATIONS, availability
from orchestrator.options import CELLS, Settings, catalogue
from orchestrator.outcomes import Outcomes
from orchestrator.paths import Paths
from orchestrator.population import correct
from tests.test_corrections import saved
from tests.test_population import CELLS as SIX, cells, world  # noqa: F401 (the fixture)
from tests.test_replay import Finders, iso, keeping

# cellpose's trainer asks for autocast at the network's dtype; the stand-in is float32 on the CPU, which autocast skips
pytestmark = pytest.mark.filterwarnings("ignore:In CPU autocast")
# Established by the autouse fixture, never by module import (collection can precede midnight).
TODAY: str
YESTERDAY: str


@pytest.fixture(autouse=True)
def _today_is_when_the_test_runs():
    """Today as the code under test reads it, taken as each test starts (a run that crossed midnight took
    yesterday's at import)."""
    global TODAY, YESTERDAY
    now = time.time()  # sample once: the two dates must describe the same instant
    TODAY = time.strftime("%Y-%m-%d", time.localtime(now))
    YESTERDAY = time.strftime("%Y-%m-%d", time.localtime(now - 86400))


@pytest.fixture(autouse=True)
def app_stopped(monkeypatch):
    """The jobs wait for the app (bench/live.py); here no app runs, whatever this machine is doing."""
    monkeypatch.setattr(live, "running", lambda: "")


class TinyNet(torch.nn.Module):
    """A small stand-in for Cellpose-SAM's network with what cellpose.train.train_seg uses of it (its device, dtype,
    backbone, the diameters it keeps, its three outputs, save_model), so the test runs cellpose's real trainer in
    seconds on the CPU."""

    def __init__(self):
        super().__init__()
        self.conv = torch.nn.Conv2d(3, 3, 3, padding=1)
        self.device, self.dtype, self.backbone = torch.device("cpu"), torch.float32, "tiny"
        self.diam_labels = torch.nn.Parameter(torch.tensor([30.0]), requires_grad=False)
        self.diam_mean = torch.nn.Parameter(torch.tensor([30.0]), requires_grad=False)

    def forward(self, x):
        return self.conv(x), None

    def save_model(self, filename):
        torch.save(self.state_dict(), filename)


@pytest.fixture
def models_dir(tmp_path, monkeypatch):
    """Where the app looks for fine-tuned weights: the models folder of the test's own saves (keeping's Paths)."""
    monkeypatch.setenv("MICROMIND_MODELS_DIR", str(tmp_path / "home" / "models"))
    return tmp_path / "home" / "models"


def corrected(world, merge: bool = False):
    population, _ = cells(world)
    saved(population)
    live_ids = [m.id for m in population.live()]
    correct(world, population, "remove", [live_ids[0]], by="user", why="not a cell")
    saved(population)  # each correction saved before the next, as a person makes them (no event loop here)
    if merge:
        correct(world, population, "merge", [live_ids[1], live_ids[2]], by="user", why="one cell")
        saved(population)
    return population, live_ids


def two_days(world, tmp_path, same_view: bool = False):
    """Two corrected populations, yesterday's and today's (a session is a day), each on its own view of the slide
    (or both on the same view): one cell removed in each, and two merged in today's."""
    paths = keeping(world, tmp_path)
    made = []
    for n, day in enumerate((YESTERDAY, TODAY)):
        world.execution_day = SimpleNamespace(day=day, sid=day, date=day, active_id=None)  # the day the app files them in
        if not same_view:
            world.frame(60.0 * n, 0.0)  # another part of the slide
        made.append(corrected(world, merge=day == TODAY))
    world.execution_day = None
    return paths, made


def two_runs_of_one_day(world, tmp_path, monkeypatch):
    """Two corrected populations made today by two runs of the app (a restart between), on the same view."""
    paths = keeping(world, tmp_path)
    made = []
    for run in ("111-1", "222-2"):
        monkeypatch.setattr(populations, "RUN", run)
        made.append(corrected(world, merge=run == "222-2"))
    return paths, made


def test_the_training_set_is_each_corrected_population_as_it_stands_now(world, tmp_path):
    paths, [(first, _), (second, live_ids)] = two_days(world, tmp_path)
    adapter = licences.adapter("cellpose-sam-tuned")
    found = job.examples([paths.evidence], adapter.picture)
    assert [e.population for e in found] == [first.id, second.id] and [e.session for e in found] == [YESTERDAY, TODAY]
    e = found[1]
    assert e.image.shape == (720, 1280) and e.labels.shape == (720, 1280) and e.crop == [0, 0, 1280, 720]
    assert e.members == 4 and int(e.labels.max()) == 4 and (e.removed, e.merged) == (1, 2)  # 6 - 1 removed - 2 merged + 1
    x, y, _, _ = SIX[0]
    assert e.labels[y, x] == 0  # the cell removed as not a cell is background
    (x2, y2, _, _), (x3, y3, _, _) = SIX[1], SIX[2]
    assert e.labels[y2, x2] == e.labels[y3, x3] != 0  # the two merged are one cell
    assert found[0].sha256 != found[1].sha256
    train, held = job.split(found, hold_out=1)
    assert [t.population for t in train] == [first.id] and [h.population for h in held] == [second.id]  # the newest day held out
    train, held = job.split(found, days=[YESTERDAY])
    assert [h.population for h in held] == [first.id]
    with pytest.raises(job.Refused, match=f"no corrected cells on 2026-01-01 to hold out; the days with corrected cells are {YESTERDAY}"):
        job.split(found, days=["2026-01-01"])  # a mistyped day is said so (the K4 review's P9)


def test_two_runs_of_the_app_on_one_day_are_one_session(world, tmp_path, models_dir, monkeypatch):
    paths, _ = two_runs_of_one_day(world, tmp_path, monkeypatch)
    adapter = licences.adapter("cellpose-sam-tuned")
    found = job.examples([paths.evidence], adapter.picture)
    assert {e.session for e in found} == {TODAY}  # the K4 review's B1: a run of the app is not a session
    train, held = job.split(found, hold_out=1)
    assert not train and len(held) == 2  # both held out together, never one trained and its twin held out
    said = []
    assert job.finetune(paths, net=TinyNet(), say=said.append) is None
    assert said[-1].startswith("Fine-tuning needs corrected cells from at least two days") and not models_dir.exists()


def test_a_held_out_view_that_is_on_the_training_side_refuses_the_split(world, tmp_path, models_dir):
    paths, _ = two_days(world, tmp_path, same_view=True)
    adapter = licences.adapter("cellpose-sam-tuned")
    found = job.examples([paths.evidence], adapter.picture)
    with pytest.raises(job.Refused, match="is the same picture as"):
        job.split(found, hold_out=1)
    said = []
    assert job.finetune(paths, net=TinyNet(), say=said.append) is None
    assert said[-1].startswith("Not fine-tuned: the held-out") and "Nothing was written" in said[-1] and not models_dir.exists()
    found[0].sha256, found[0].boot, found[0].seq = "another picture", "b1", 7  # the same bridge frame, re-encoded
    found[1].boot, found[1].seq = "b1", 7
    with pytest.raises(job.Refused, match=r"the same bridge frame \(b1, 7\)"):
        job.split(found, hold_out=1)


def test_with_no_corrected_cells_nothing_is_trained_or_written(world, tmp_path, models_dir):
    paths = keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    said = []
    assert job.finetune(paths, net=TinyNet(), say=said.append) is None
    assert said[0].startswith("No corrected cells yet") and "Nothing was written" in said[0]
    assert not models_dir.exists() and finetuned.current(job.NAME) is None


def test_it_waits_for_the_app_and_for_room_and_says_so(world, tmp_path, models_dir, monkeypatch):
    paths, _ = two_days(world, tmp_path)
    monkeypatch.setattr(live, "running", lambda: "the live app answers on http://127.0.0.1:8770")
    said = []
    assert job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=said.append) is None
    assert said[-1].startswith("Not fine-tuned: Fine-tuning waits for the app: the live app answers") and "--beside-live" in said[-1]
    assert not models_dir.exists()
    monkeypatch.setattr(job.shutil, "disk_usage", lambda place: SimpleNamespace(free=1000))
    said.clear()
    assert job.finetune(paths, net=TinyNet(), epochs=1, block=64, beside_live=True, say=said.append) is None
    assert said[-1].startswith("Not fine-tuned: fine-tuning writes about") and "is free at" in said[-1] and not models_dir.exists()


def test_it_trains_with_cellposes_trainer_and_writes_a_new_version_each_time(world, tmp_path, models_dir):
    paths, made = two_days(world, tmp_path)
    said = []
    first = job.finetune(paths, net=TinyNet(), epochs=2, block=64, say=said.append)
    assert first["version"] == "v1" and first["whole"] and finetuned.verify(first)
    meta = json.loads((models_dir / job.NAME / "v1" / finetuned.META).read_text())
    assert meta["tier"] == "noncommercial" and meta["base"]["implementation"] == "cellpose-sam"
    assert meta["split"]["basis"] == "day" and meta["split"]["heldOutDays"] == [TODAY] and meta["heldOut"] == [TODAY]
    assert [t["population"] for t in meta["trainedOn"]] == [made[0][0].id] and meta["trainedOn"][0]["day"] == YESTERDAY
    assert len(meta["trainedOn"][0]["captureSha256"]) == 64 and "capture" not in meta["trainedOn"][0]  # the view, not a path
    assert [h["population"] for h in meta["heldOutPopulations"]] == [made[1][0].id]
    assert meta["settings"]["heldOutGivenToTrainer"] is False and "test" not in meta["losses"]
    assert len(meta["losses"]["train"]) == 2 and all(np.isfinite(meta["losses"]["train"]))
    assert meta["settings"]["trainer"] == "cellpose.train.train_seg" and "recommended" not in meta
    assert meta["recommendation"].startswith("recommended only if it wins on its held-out days")
    assert "cells you did not correct are taken as the model made them" in meta["labels"]
    weights = (models_dir / job.NAME / "v1" / finetuned.WEIGHTS).read_bytes()
    assert set(torch.load(models_dir / job.NAME / "v1" / finetuned.WEIGHTS)) == {"conv.weight", "conv.bias", "diam_labels",
                                                                               "diam_mean"}
    second = job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=said.append)
    assert second["version"] == "v2" and finetuned.current(job.NAME)["version"] == "v2"
    assert (models_dir / job.NAME / "v1" / finetuned.WEIGHTS).read_bytes() == weights  # never written over
    assert "make replay-bench" in said[-1]


def test_a_version_that_is_not_whole_is_never_used(models_dir):
    folder = models_dir / job.NAME / "v1"
    folder.mkdir(parents=True)
    (folder / finetuned.WEIGHTS).write_bytes(b"half")
    (folder / finetuned.META).write_text(json.dumps({"bytes": 1000}))
    assert finetuned.current(job.NAME) is None and finetuned.versions(job.NAME)[0]["why"].startswith("its weights file")
    assert finetuned.missing(job.NAME).startswith("no fine-tuned weights here yet")


def test_the_tuned_finder_is_refused_in_the_commercial_edition(monkeypatch, models_dir):
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    impl = OPERATIONS["cells"].impl("cellpose-sam-tuned")
    assert impl is not None and CELLS["cellpose-sam-tuned"].tier == "noncommercial"
    assert licences.COMPONENTS["cellpose-sam-tuned"].tier == "noncommercial"
    assert "non-commercial" in availability(SimpleNamespace(settings=None), impl)
    assert "non-commercial" in Settings().update({"cells": "cellpose-sam-tuned"})[0]
    assert licence_refusal({"op": "cells", "model": "cellpose-sam-tuned"})["licence"]
    with pytest.raises(licences.NotInEdition):
        licences.adapter("cellpose-sam-tuned")
    with pytest.raises(SystemExit, match="not possible here"):
        job.finetune(Paths.under(models_dir.parent))


def populations_of(world, choice):
    from orchestrator.ticket import issue, using
    from tests.test_apply import run

    with using(issue(world, "command")):
        return run(populations.segment(world, choice=choice))


def test_in_research_it_is_available_once_its_weights_exist_and_runs_with_them(world, tmp_path, models_dir):
    impl = OPERATIONS["cells"].impl("cellpose-sam-tuned")
    assert availability(world, impl).startswith("no fine-tuned weights here yet (make finetune-cells")
    entry = next(e for e in catalogue({})["cells"] if e["id"] == "cellpose-sam-tuned")
    assert not entry["available"] and entry["why"].startswith("no fine-tuned weights")
    assert "no fine-tuned weights" in Settings().update({"cells": "cellpose-sam-tuned"})[0]

    paths, _ = two_days(world, tmp_path)
    job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)
    assert availability(world, impl) == ""
    assert next(e for e in catalogue({})["cells"] if e["id"] == "cellpose-sam-tuned")["available"]
    settings = Settings()
    assert settings.update({"cells": "cellpose-sam-tuned"}) == [] and settings.cells == "cellpose-sam-tuned"

    calls = []
    finder = world.gpu_heavy
    original = finder.call

    async def call(op, timeout=0, **payload):
        calls.append(payload)
        return await original(op, timeout, **payload)

    finder.call = call
    population = populations_of(world, "cellpose-sam-tuned")
    current = finetuned.current("cellpose-sam-tuned")
    assert calls[-1]["model"] == "cellpose-sam-tuned" and calls[-1]["weights"] == current["weights"]
    assert calls[-1]["version"] == "v1" and population.producer["version"] == "cellpose cpsam, fine-tuned v1"
    assert population.made_by["implementation"] == "cellpose-sam-tuned"


def test_each_fine_tune_version_is_its_own_outcome_line(world, tmp_path, models_dir):
    paths, _ = two_days(world, tmp_path)
    world.scene.context["specimen"] = "onion epidermis"
    for _ in range(2):
        job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)
        populations_of(world, "cellpose-sam-tuned")
    runs = [json.loads(line) for line in paths.interaction_log.read_text().splitlines()]
    runs = [r for r in runs if r.get("kind") == "execution" and r.get("executed") == "cellpose-sam-tuned"]
    assert [r.get("version") for r in runs] == ["v1", "v2"]  # the K4 review's SF4: the version is on the record
    tallies = [t for t in Outcomes([paths.interaction_log], paths.evidence, None).tallies() if t.implementation == "cellpose-sam-tuned"]
    assert sorted((t.version, t.runs) for t in tallies) == [("v1", 1), ("v2", 1)]
    assert {t.label() for t in tallies} == {"Cellpose-SAM fine-tuned on your corrections v1 (raw)",
                                            "Cellpose-SAM fine-tuned on your corrections v2 (raw)"}


def test_a_job_keyed_for_one_version_is_never_shared_with_the_next(world, tmp_path, models_dir):
    """A v1 job still running is never shared with a request made once v2 exists (the K4 review's O4)."""
    from orchestrator.capture import captures
    from orchestrator.operations import Prompt, runner

    paths, _ = two_days(world, tmp_path)
    job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)
    seen = []

    class Slow:
        async def ensure_started(self):
            return self

        async def call(self, op, timeout=0, **payload):
            seen.append(payload.get("version"))
            await asyncio.sleep(0.05)
            return {"polygons": [[[0, 0], [1, 0], [1, 1]]], "count": 1, "ms": 1.0}

    world.gpu_heavy = Slow()
    capture = captures(world).take()

    async def both():
        first = asyncio.ensure_future(runner(world).run("cells", capture, Prompt("field"), choice="cellpose-sam-tuned",
                                                        origin="brain-chosen", ticket=None))
        await asyncio.sleep(0.01)
        job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)  # v2 written meanwhile
        second = await runner(world).run("cells", capture, Prompt("field"), choice="cellpose-sam-tuned", origin="brain-chosen",
                                         ticket=None)
        return await first, second

    a, b = asyncio.run(both())
    assert seen == ["v1", "v2"] and not b.use.reused and a.output.get("weightsVersion") == "v1"


def test_the_replay_bench_never_counts_a_day_the_version_that_ran_trained_on(world, tmp_path, models_dir):
    paths, made = two_days(world, tmp_path)
    job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)
    now = time.time()
    out = bundles.write(paths, tmp_path / "out", start=iso(now - 86400 - 120), stop=iso(now + 60))
    result = asyncio.run(replay.run([out], ["cellpose-sam-tuned"], gpu=Finders()))
    rows = {r["day"]: r for r in result["runs"]}
    assert rows[YESTERDAY]["trainedOn"] is True and rows[TODAY]["trainedOn"] is False  # kills the review's R2
    assert rows[TODAY]["splitBasis"] == "day" and rows[TODAY]["implVersion"] == "cellpose cpsam, fine-tuned v1"
    (subject,) = result["summary"].values()
    entry = subject["cellpose-sam-tuned"]
    assert entry["trainedOn"] == [made[0][0].id] and entry["n"] == 1 and entry["recorded"] == rows[TODAY]["recorded"]
    assert "days it did not train on" in entry["recommendation"]


def test_the_gpu_worker_keeps_one_cellpose_network_and_loads_the_weights_the_call_names(monkeypatch, tmp_path):
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    import cellpose.models

    loaded = []

    class Stub:
        def __init__(self, gpu=True, device=None, pretrained_model=None, use_bfloat16=True):
            loaded.append(pretrained_model)

        def eval(self, image, **kwargs):
            labels = np.zeros(image.shape, np.uint16)
            labels[10:20, 10:20], labels[30:40, 30:45] = 1, 2
            return labels, [None, None, np.zeros(image.shape, np.float32)], None

    monkeypatch.setattr(cellpose.models, "CellposeModel", Stub)
    # The device self-check has its own tests (test_cellpose_device.py): here the device is already proven.
    from noncommercial import cellpose_device
    monkeypatch.setattr(cellpose_device, "verdict", lambda make, device, **kw: (
        {"device": str(device), "dtype": "bfloat16", "checked": "stand-in"}, None))
    import cv2

    jpeg = cv2.imencode(".jpg", np.full((60, 80, 3), 120, np.uint8))[1].tobytes()
    models = Models()
    with pytest.raises(ValueError, match="needs the fine-tuned weights"):
        models.cells({"model": "cellpose-sam-tuned", "jpeg": jpeg})
    models.cells({"model": "cellpose-sam", "jpeg": jpeg})
    a = models.cells({"model": "cellpose-sam-tuned", "jpeg": jpeg, "weights": str(tmp_path / "v1" / "weights"), "version": "v1"})
    from noncommercial.cellpose_cells import pinned_weights

    # The base model from its pinned file in the models folder (gpu_worker/model_files.py), never one Cellpose fetches.
    assert loaded == [str(pinned_weights()), str(tmp_path / "v1" / "weights")] and a["count"] == 2 and a["weights"] == "v1"
    assert list(models._cells) == [f"cellpose-sam-tuned@{tmp_path / 'v1' / 'weights'}"]  # the base let go (the review's P10)
    models.cells({"model": "cellpose-sam-tuned", "jpeg": jpeg, "weights": str(tmp_path / "v1" / "weights")})
    assert len(loaded) == 2  # kept while the same file is named
    models.cells({"model": "cellpose-sam-tuned", "jpeg": jpeg, "weights": str(tmp_path / "v2" / "weights")})
    assert loaded[-1].endswith("v2/weights") and list(models._cells) == [f"cellpose-sam-tuned@{tmp_path / 'v2' / 'weights'}"]
    models.cells({"model": "cellpose-sam", "jpeg": jpeg})
    assert list(models._cells) == ["cellpose-sam"]


def test_saying_cellpose_means_its_default_and_says_the_tuned_one_is_the_other(world, tmp_path, models_dir):
    """Since 2026-10-11 (the user's Q18) a family named alone is its default variant, and the receipt says which: the
    tuned one is chosen by its own name."""
    from orchestrator.brain.toolbox.choosing import pick, pick_said

    op = OPERATIONS["cells"]
    assert pick(world, op, "cellpose").id == "cellpose-sam"
    paths, _ = two_days(world, tmp_path)
    job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)
    impl, said = pick_said(world, op, "cellpose")
    assert impl.id == "cellpose-sam" and said.endswith("the other: Cellpose-SAM fine-tuned on your corrections")
    assert pick(world, op, "Cellpose-SAM fine-tuned on your corrections").id == "cellpose-sam-tuned"
    assert pick(world, op, "cellpose-sam").id == "cellpose-sam"


def test_a_newer_version_replayed_on_what_an_older_one_made_is_a_candidate(world, tmp_path, models_dir):
    """Itself is the same implementation and version: v2 run on what v1 made is judged as a candidate (the review's R6)."""
    paths, _ = two_days(world, tmp_path)
    job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)
    made_by_v1 = populations_of(world, "cellpose-sam-tuned")
    saved(made_by_v1)
    now = time.time()
    before = bundles.write(paths, tmp_path / "out1", start=iso(now - 120), stop=iso(now + 60))
    rows = asyncio.run(replay.run([before], ["cellpose-sam-tuned"], gpu=Finders()))["runs"]
    again = next(r for r in rows if r["population"] == made_by_v1.id)
    assert again["self"] is True and again["implVersion"] == again["recordedVersion"] == "cellpose cpsam, fine-tuned v1"
    job.finetune(paths, net=TinyNet(), epochs=1, block=64, say=lambda _: None)
    after = bundles.write(paths, tmp_path / "out2", start=iso(now - 120), stop=iso(time.time() + 60))
    rows = asyncio.run(replay.run([after], ["cellpose-sam-tuned"], gpu=Finders()))["runs"]
    other = next(r for r in rows if r["population"] == made_by_v1.id)
    assert other["self"] is False and other["implVersion"] == "cellpose cpsam, fine-tuned v2" and other["trainedOn"] is False
