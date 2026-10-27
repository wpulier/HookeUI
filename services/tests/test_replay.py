"""Session bundles and the replay bench (session knowledge K4, contract 5; ADR 0013): a window or a dataset with its
evidence, executions, outcomes, corrections and journal, written read-only on its sources and saying what it lacks;
a cell finder run again on exactly the recorded views and prompts through the app's own path, judged by the user's
corrections where there are any and by agreement (never accuracy) elsewhere; winning governs only a recommendation."""
import asyncio
import hashlib
import json
import os
import time
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np
import pytest

from bench import bundle as bundles
from bench import live
from bench import replay_bench as replay
from gpu_worker.worker import encode_labels
from orchestrator.paths import Paths
from orchestrator.population import correct
from tests.test_corrections import saved
from tests.test_population import CELLS, cells, label_map, world  # noqa: F401 (the fixture)


def iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch).isoformat(timespec="seconds")


@pytest.fixture(autouse=True)
def app_stopped(monkeypatch):
    """The replay bench waits for the app (bench/live.py); here no app runs, whatever this machine is doing."""
    monkeypatch.setattr(live, "running", lambda: "")


def keeping(world, tmp_path):
    """The world's evidence, journal and interaction log under tmp_path (Paths.under), as the app keeps them."""
    from orchestrator.day.store import DayStore
    from orchestrator.evidence import Evidence
    from orchestrator.operations import runner

    paths = Paths.under(tmp_path / "home")
    paths.logs.mkdir(parents=True, exist_ok=True)
    world.evidence = Evidence(world, paths.evidence, day=DayStore(root=paths.sessions, day=time.strftime("%Y-%m-%d")))

    def log(record):
        line = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "day": time.strftime("%Y-%m-%d"), "thread": "t1", **record}
        with paths.interaction_log.open("a") as f:
            f.write(json.dumps(line) + "\n")

    runner(world).log = log
    world.scene.context["specimen"] = "onion epidermis"
    return paths


def corrected_population(world, tmp_path):
    """Six cells by Cellpose-SAM (the stand-in finder), one removed as not a cell and two merged, saved."""
    paths = keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    live = [m.id for m in population.live()]
    correct(world, population, "remove", [live[0]], by="user", why="not a cell")
    saved(population)  # each correction saved before the next, as a person makes them (no event loop here)
    correct(world, population, "merge", [live[1], live[2]], by="user", why="one cell")
    saved(population)
    return paths, population, live


def snapshot(*roots: Path) -> dict:
    out = {}
    for root in roots:
        for path in sorted(Path(root).rglob("*")) if Path(root).exists() else []:
            if path.is_file():
                out[str(path)] = (path.stat().st_size, path.stat().st_mtime_ns, hashlib.sha256(path.read_bytes()).hexdigest())
    return out


# --- the bundle ------------------------------------------------------------------------------------
def test_a_window_bundle_holds_evidence_executions_outcomes_and_corrections_read_only(world, tmp_path):
    paths, population, live = corrected_population(world, tmp_path)
    before = snapshot(paths.home)
    now = time.time()
    out = bundles.write(paths, tmp_path / "out" / "leaf", start=iso(now - 120), stop=iso(now + 60))
    assert snapshot(paths.home) == before  # read-only on every source: nothing written, moved or removed

    d = bundles.load(out)
    (p,) = d["populations"]
    assert p["id"] == population.id and p["status"] == "whole" and p["corrected"] and p["revision"] == 2
    assert p["execution"] == population.made_by["execution"] and p["subject"] == "onion epidermis"
    assert p["capture"]["file"] == f"{p['folder']}/capture.jpg" and (out / p["capture"]["file"]).is_file()
    assert p["prompt"]["kind"] == "field" and p["prompt"]["crop"] == [0, 0, 1280, 720]
    manifest = json.loads((out / p["folder"] / "manifest.json").read_text())
    for name, digest in manifest["files"].items():  # copied whole: the same checksums as the app saved
        assert hashlib.sha256((out / p["folder"] / name).read_bytes()).hexdigest() == digest
    corrections = [json.loads(l) for l in (out / "corrections.jsonl").read_text().splitlines()]
    assert [c["revision"] for c in corrections] == [1, 2] and corrections[0]["members"][live[0]]["current"] == "deleted"
    assert corrections[0]["masks"] == f"{p['folder']}/masks.npz"  # the rows point at the bundle's own copies
    outcomes = json.loads((out / "outcomes.json").read_text())
    (o,) = [t for t in outcomes if t["operation"] == "cells"]
    assert o["deleted"] == 1 and o["merged"] == 2 and o["members"] == 6 and o["subject"] == "onion epidermis"
    executions = [json.loads(l) for l in (out / "executions.jsonl").read_text().splitlines()]
    assert {"execution", "execution-use", "execution-output"} <= {e["kind"] for e in executions}
    assert d["captures"] == [{"execution": p["execution"], "operation": "cells", "capture": population.capture_id,
                              "source": "evidence", "file": p["capture"]["file"], "boot": "", "seq": None}]
    assert any(line.startswith("No dataset: the window was not collected by Record") for line in d["lacks"])
    assert not any("No corrections" in line for line in d["lacks"])
    records = [json.loads(l) for l in (out / "records.jsonl").read_text().splitlines()]
    assert {"journal", "interaction"} <= {r["stream"] for r in records}


def test_a_bundle_is_never_written_inside_the_datasets_folder_or_over_anything(world, tmp_path):
    paths, _, _ = corrected_population(world, tmp_path)
    now = time.time()
    window = {"start": iso(now - 60), "stop": iso(now + 60)}
    with pytest.raises(SystemExit, match="never written inside the datasets folder"):
        bundles.write(paths, paths.datasets / "mine", **window)
    for home in (paths.evidence / "mine", paths.recordings / "b", paths.snapshots / "2026-10-07" / "b", paths.models / "b",
                 paths.logs / "b", paths.home / "b"):  # anywhere under the app's saves (the review's P8)
        with pytest.raises(SystemExit, match="never written inside the app's saves"):
            bundles.write(paths, home, **window)
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_text("mine")
    with pytest.raises(SystemExit, match="never written over"):
        bundles.write(paths, taken, **window)
    assert (taken / "keep.txt").read_text() == "mine" and not paths.datasets.exists()


def test_without_corrections_or_populations_the_bundle_says_what_it_lacks(world, tmp_path):
    paths = keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    now = time.time()
    lacks = bundles.load(bundles.write(paths, tmp_path / "b1", start=iso(now - 60), stop=iso(now + 60)))["lacks"]
    assert any(line.startswith("No corrections of segmented cells: the replay bench can report agreement") for line in lacks)
    empty = bundles.load(bundles.write(paths, tmp_path / "b2", start=iso(now - 9000), stop=iso(now - 8000)))["lacks"]
    assert any(line.startswith("No population in this window") for line in empty)
    assert any(line.startswith("No executions were logged") for line in empty)


def dataset(paths: Paths, t0: float, reconstructed: bool = False) -> Path:
    """A collected dataset: one run, one camera part of 20 frames (frame i lit 10 x i), its sidecar and frame facts."""
    folder = paths.datasets / f"ds-{time.strftime('%Y%m%d-%H%M%S', time.localtime(t0))}"
    (folder / "video").mkdir(parents=True)
    movie = folder / "video" / "part-1.mp4"
    writer = cv2.VideoWriter(str(movie), cv2.VideoWriter_fourcc(*"mp4v"), 30, (64, 48))
    for i in range(20):
        writer.write(np.full((48, 64, 3), 10 * i, np.uint8))
    writer.release()
    sidecar = [{"type": "header", "bootId": "b1"}] + [{"i": i, "seq": 100 + i, "bridgeAt": t0 + i} for i in range(20)]
    (folder / "video" / "part-1.frames.jsonl").write_text("".join(json.dumps(x) + "\n" for x in sidecar))
    (folder / "frames.jsonl").write_text("".join(json.dumps({"run": 1, "id": 500 + i, "seq": 100 + i, "boot": "b1"}) + "\n"
                                                 for i in range(20)))
    summary = {"schema": 1, "id": folder.name, "status": "complete", "startedAtEpoch": t0, "stoppedAtEpoch": t0 + 100,
               "endedBy": "user", "endCheck": {"ok": True, "problems": []},
               "runs": [{"n": 1, "startedAtEpoch": t0, "endedAtEpoch": t0 + 100}],
               "video": {"parts": [{"n": 1, "video": "video/part-1.mp4", "sidecar": "video/part-1.frames.jsonl", "boot": "b1"}]}}
    if reconstructed:
        summary.update(reconstructed=True, endedBy="reconstruction", reconstruction={"notCaptured": ["no per-frame facts"]})
        summary.pop("endCheck")
    (folder / "dataset.json").write_text(json.dumps(summary))
    return folder


def test_a_dataset_bundle_takes_an_executions_view_from_its_video_by_boot_and_seq(tmp_path):
    paths = Paths.under(tmp_path / "home")
    t0 = time.time() - 200
    folder = dataset(paths, t0)
    paths.logs.mkdir(parents=True)
    record = {"at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t0 + 50)), "kind": "execution", "id": "xa-1",
              "operation": "outline", "requested": "edgetam", "executed": "edgetam", "capture": 507, "startedAt": t0 + 50,
              "subject": "onion epidermis", "attempts": [], "ms": 40.0, "policy": "route", "origin": "click"}
    paths.interaction_log.write_text(json.dumps(record) + "\n")
    out = bundles.write(paths, tmp_path / "out", dataset=folder.name)
    d = bundles.load(out)
    (view,) = d["captures"]
    assert view["source"] == "video" and (view["boot"], view["seq"]) == ("b1", 107) and "not the JPEG" in view["note"]
    picture = cv2.imread(str(out / view["file"]))
    assert abs(float(picture.mean()) - 70) < 8  # frame 7 of the part: the sidecar's index for seq 107
    assert os.path.samefile(out / "dataset" / "video" / "part-1.mp4", folder / "video" / "part-1.mp4")  # linked
    assert not os.path.samefile(out / "dataset" / "dataset.json", folder / "dataset.json")  # copied
    assert d["window"]["of"] == f"dataset {folder.name}" and d["dataset"]["files"]["linked"] == 1
    assert not any(line.startswith("No dataset") for line in d["lacks"])
    rebuilt = dataset(paths, t0 - 1000, reconstructed=True)
    lacks = bundles.load(bundles.write(paths, tmp_path / "out2", dataset=rebuilt.name))["lacks"]
    assert any("reconstructed afterwards from the logs" in line and "no per-frame facts" in line for line in lacks)


def test_a_dataset_still_being_saved_is_not_bundled_yet(tmp_path):
    """Record stopped, but the bridge still finishes its video and the end check is written last (the review's P7)."""
    paths = Paths.under(tmp_path / "home")
    folder = dataset(paths, time.time() - 200)
    summary = json.loads((folder / "dataset.json").read_text())
    summary.pop("endCheck")
    (folder / "dataset.json").write_text(json.dumps(summary))
    with pytest.raises(SystemExit, match="is still being saved"):
        bundles.write(paths, tmp_path / "out", dataset=folder.name)
    summary["status"] = "collecting"
    (folder / "dataset.json").write_text(json.dumps(summary))
    with pytest.raises(SystemExit, match="is still collecting"):
        bundles.write(paths, tmp_path / "out", dataset=folder.name)
    assert not (tmp_path / "out").exists()


def test_a_bundle_keeps_its_own_copies_and_shares_only_a_datasets_video(tmp_path):
    """Pictures, snapshots and researchers' work are copied, so nothing done to the bundle reaches the user's files
    (the review's P3); only the dataset's camera video is the same file."""
    paths = Paths.under(tmp_path / "home")
    day, now = time.strftime("%Y-%m-%d"), time.time()
    (paths.sessions / day).mkdir(parents=True)
    (paths.sessions / day / "journal.jsonl").write_text(json.dumps({"type": "research-linked", "rid": "r1", "at": now}) + "\n")
    work = paths.research / day / "r1"
    work.mkdir(parents=True)
    (work / "session.json").write_text('{"kept": "the user\'s work"}')
    (paths.snapshots / day).mkdir(parents=True)
    (paths.snapshots / day / "s1.json").write_text(json.dumps({"wallAt": now}))
    (paths.snapshots / day / "s1.png").write_bytes(b"png")
    folder = dataset(paths, now - 50)
    out = bundles.write(paths, tmp_path / "out", dataset=folder.name)
    files = [f for f in out.rglob("*") if f.is_file()]
    assert (out / "research" / "r1" / "session.json").exists() and any(f.name == "s1.png" for f in files)
    shared = [str(f.relative_to(out)) for f in files if f.stat().st_nlink > 1]
    assert shared == ["dataset/video/part-1.mp4"]
    (out / "research" / "r1" / "session.json").write_text("{}")  # a program tidying the bundle
    assert (work / "session.json").read_text() == '{"kept": "the user\'s work"}'


def test_a_day_is_bundled_from_one_midnight_to_the_next():
    start, stop, _, _ = bundles.window_of(Paths.under("/nonexistent"), day="2026-03-08")  # 23 hours where clocks spring forward
    assert datetime.fromtimestamp(start) == datetime(2026, 3, 8)
    assert datetime.fromtimestamp(stop + 0.001) == datetime(2026, 3, 9)


# --- comparing ------------------------------------------------------------------------------------------
def ellipses(spec, size=(400, 300)):
    """{id: instance} for ellipses (x, y, a, b), each its own full-frame mask."""
    from orchestrator.measure import Mask

    out = {}
    for n, (x, y, a, b) in enumerate(spec, start=1):
        canvas = np.zeros(size[::-1], np.uint8)
        cv2.ellipse(canvas, (x, y), (a, b), 0, 0, 360, 1, -1)
        ys, xs = np.nonzero(canvas)
        x0, y0 = xs.min(), ys.min()
        out[f"m{n}"] = replay.instance(Mask(canvas[y0:ys.max() + 1, x0:xs.max() + 1].astype(bool), int(x0), int(y0)))
    return out


def test_agreement_matches_instances_one_to_one_and_never_more():
    a = ellipses([(50, 50, 20, 20), (150, 50, 20, 20), (250, 50, 20, 20)])
    same = replay.agreement(a, a)
    assert (same["matched"], same["onlyRecorded"], same["onlyReplayed"], same["agreement"], same["meanIoU"]) == (3, 0, 0, 1.0, 1.0)
    b = ellipses([(52, 50, 20, 20), (150, 50, 10, 10), (350, 200, 20, 20), (351, 200, 20, 20)])
    got = replay.agreement(a, b)
    # the slightly moved one matches; the half-size one (IoU 0.25) does not
    assert (got["matched"], got["onlyRecorded"], got["onlyReplayed"]) == (1, 2, 3)
    assert got["agreement"] == round(2 * 1 / 7, 3)
    # two overlapping recorded masks (automatic masks may overlap) and one replayed: one match, never two
    twice = replay.agreement(ellipses([(50, 50, 20, 20), (51, 50, 20, 20)]), ellipses([(50, 50, 20, 20)]))
    assert (twice["matched"], twice["onlyRecorded"], twice["onlyReplayed"]) == (1, 1, 0)
    assert (replay.agreement(ellipses([(50, 50, 20, 20)]), ellipses([(50, 50, 20, 20), (51, 50, 20, 20)]))["matched"]) == 1


def test_corrections_are_the_truth_and_the_recorded_run_agrees_with_none(world, tmp_path):
    paths, population, live = corrected_population(world, tmp_path)
    now = time.time()
    (rec,) = bundles.recorded(bundles.write(paths, tmp_path / "out", start=iso(now - 120), stop=iso(now + 60)))
    items = replay.truth(rec)
    assert [(i["kind"], i["member"]) for i in items][0] == ("not-a-cell", live[0])
    assert items[1]["kind"] == "one-cell" and sorted(items[1]["parts"]) == sorted(live[1:3])
    recorded_set = {mid: replay.instance(rec.masks[mid]) for mid in rec.model_members()}
    on_its_own = replay.judge(items, rec.masks, recorded_set)
    assert (on_its_own["judged"], on_its_own["agreed"], on_its_own["repeated"]) == (2, 0, 2)  # by construction
    fixed = {k: v for k, v in recorded_set.items() if k not in (live[0], live[1], live[2])}
    fixed[items[1]["member"]] = replay.instance(rec.masks[items[1]["member"]])  # the merged one found as one
    assert replay.judge(items, rec.masks, fixed)["agreed"] == 2
    neither = {k: v for k, v in recorded_set.items() if k not in (live[0], live[1], live[2])}
    assert [v["verdict"] for v in replay.judge(items, rec.masks, neither)["items"]] == ["agrees", "differs"]


# --- replaying through the app's path -------------------------------------------------------------------
class Finders:
    """The analysis worker, per cell finder: Cellpose-SAM gives the recorded six cells again; micro-sam leaves the
    first out and finds the second and third as one cell (what the user's corrections said)."""

    def __init__(self):
        self.calls = []

    async def ensure_started(self):
        return self

    async def call(self, op, timeout=0, **payload):
        self.calls.append({"op": op, **{k: v for k, v in payload.items() if k != "jpeg"}})
        labels, origin = label_map()
        if payload.get("model") == "micro-sam-lm":
            labels = labels.copy()
            labels[labels == 1] = 0
            labels[labels == 3] = 2
        return {"labels": encode_labels(labels, origin), "scores": {}, "score": "iou", "polygons": [[[0, 0], [1, 0], [1, 1]]],
                "count": int(len(np.unique(labels)) - 1), "ms": 700}


def test_the_replay_runs_each_finder_on_the_recorded_view_and_prompt_and_keeps_nothing(world, tmp_path, monkeypatch):
    paths, population, live = corrected_population(world, tmp_path)
    now = time.time()
    out = bundles.write(paths, tmp_path / "out", start=iso(now - 120), stop=iso(now + 60))
    before = snapshot(paths.home, out)
    gpu = Finders()
    result = asyncio.run(replay.run([out], ["cellpose-sam", "micro-sam-lm"], gpu=gpu))
    assert [c["model"] for c in gpu.calls] == ["cellpose-sam", "micro-sam-lm"]
    assert all(c["op"] == "cells" and c["crop"] is None for c in gpu.calls)  # the recorded prompt: the whole field
    # never logged as the app's (so never an outcome), never kept as evidence, the bundle unchanged
    assert snapshot(paths.home, out) == before
    rows = {r["impl"]: r for r in result["runs"]}
    same, other = rows["cellpose-sam"], rows["micro-sam-lm"]
    assert same["execution"]["origin"] == "replay" and same["execution"]["policy"] == "raw"
    assert (same["recorded"], same["replayed"], same["matched"], same["agreement"]) == (6, 6, 6, 1.0)
    assert same["corrections"]["agreed"] == 0 and same["corrections"]["repeated"] == 2
    # the merged one matches the larger of its pieces (IoU 0.57); the removed one and the smaller piece are only recorded
    assert (other["replayed"], other["matched"], other["onlyRecorded"], other["onlyReplayed"]) == (4, 4, 2, 0)
    assert other["corrections"]["agreed"] == 2 and other["uncorrected"] == {"n": 3, "found": 3, "share": 1.0}
    summary = result["summary"]["onion epidermis"]
    assert summary["cellpose-sam"]["recommendation"].startswith("the implementation that made the recorded cells")
    assert summary["micro-sam-lm"]["recommendation"].startswith("no recommendation: 2 of your corrections")
    assert "not accuracy" in summary["micro-sam-lm"]["recommendation"]
    doc = replay.report({**result, "out": "x.json"})
    assert "## Subject: onion epidermis" in doc and "Agreement is not accuracy" in doc


def test_a_finder_that_cannot_run_here_is_said_not_run(world, tmp_path, monkeypatch):
    paths, _, _ = corrected_population(world, tmp_path)
    now = time.time()
    out = bundles.write(paths, tmp_path / "out", start=iso(now - 120), stop=iso(now + 60))
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    result = asyncio.run(replay.run([out], ["micro-sam-lm"], gpu=Finders()))
    (row,) = result["runs"]
    assert not row["ok"] and row["error"].startswith("not run: not in the commercial edition")
    assert result["summary"]["onion epidermis"]["micro-sam-lm"]["recommendation"].startswith("no recommendation: it did not run")


def test_another_days_population_with_the_same_id_is_never_judged_by_these_corrections(tmp_path):
    """Population and member ids start again each day: a bundle across midnight holds two e1 (the review's P2)."""
    root = tmp_path / "bundle"

    def population(day, members, revisions):
        folder = root / "evidence" / day / "e1"
        folder.mkdir(parents=True)
        arrays = {}
        for n, m in enumerate(members):
            bits = np.zeros((20, 20), bool)
            bits[2:18, 2:18] = True
            arrays[f"{m['id']}.bits"] = np.packbits(bits)
            arrays[f"{m['id']}.meta"] = np.array([20, 20, 30 * n, 0, 1.0])
        np.savez(folder / "masks.npz", **arrays)
        (folder / "manifest.json").write_text(json.dumps({"id": "e1", "day": day, "members": members, "revisions": revisions,
                                                          "revision": len(revisions)}))
        (folder / "capture.jpg").write_bytes(b"x")

    population("2026-10-06", [{"id": "c1", "status": "model"}, {"id": "c2", "status": "model"}, {"id": "c3", "status": "model"}], [])
    population("2026-10-07", [{"id": "c1", "status": "merged"}, {"id": "c2", "status": "merged"},
                              {"id": "c3", "status": "added", "extras": {"mergedFrom": ["c1", "c2"]}}],
               [{"r": 1, "change": "merge", "by": "user", "was": {"c1": "model", "c2": "model"}, "added": ["c3"]}])
    (root / "bundle.json").write_text(json.dumps({"populations": [
        {"id": "e1", "day": d, "status": "whole", "folder": f"evidence/{d}/e1", "subject": "onion"} for d in ("2026-10-06", "2026-10-07")]}))
    first, second = bundles.recorded(root)
    assert replay.truth(first) == []  # never corrected
    assert [(i["kind"], i["member"]) for i in replay.truth(second)] == [("one-cell", "c3")]


def test_what_made_the_recorded_cells_run_again_is_said_apart_and_never_counted():
    """A finder run on its own populations reproduces itself: never a candidate's figures (the review's P6); a
    fine-tune's v2 run on what v1 made is a candidate (R6)."""
    def row(pop, rec_impl, rec_version, impl, version, rec, rep, m):
        return {"ok": True, "subject": "onion", "impl": impl, "bundle": "b", "population": pop, "day": "2026-10-07", "recordedImpl": rec_impl,
                "recordedVersion": rec_version, "implVersion": version, "self": rec_impl == impl and version == rec_version,
                "recorded": rec, "replayed": rep, "matched": m, "meanIoU": 1.0, "agreement": 1.0, "ms": 3000, "firstCall": False,
                "medianAreaRecorded": 200, "medianAreaReplayed": 200,
                "corrections": {"judged": 0, "agreed": 0, "repeated": 0, "differs": 0}, "uncorrected": {"n": rec, "found": m}}

    rows = [row("e1", "cellpose-sam", "cellpose cpsam", "cellpose-sam", "cellpose cpsam", 200, 200, 200),
            row("e2", "micro-sam-lm", "vit_b_lm", "cellpose-sam", "cellpose cpsam", 10, 200, 2)]
    entry = replay.summarise(rows)["onion"]["cellpose-sam"]
    assert (entry["n"], entry["recorded"], entry["matched"], entry["uncorrectedFound"]) == (1, 10, 2, 0.2)
    assert entry["itself"]["n"] == 1 and entry["itself"]["agreement"] == 1.0
    assert entry["recommendation"].startswith("no recommendation: 0 of your corrections")
    only_itself = replay.summarise(rows[:1])["onion"]["cellpose-sam"]
    assert only_itself["recommendation"].startswith("the implementation that made the recorded cells")
    tuned = [row("e3", "cellpose-sam-tuned", "cellpose cpsam, fine-tuned v1", "cellpose-sam-tuned", "cellpose cpsam, fine-tuned v2",
                 50, 50, 50)]
    assert replay.summarise(tuned)["onion"]["cellpose-sam-tuned"]["n"] == 1  # another version: a candidate
    report = replay.report({"at": "t", "policy": "raw", "threshold": 0.5, "rule": {"minCorrections": 20, "agrees": 0.5, "keeps": 0.9},
                            "bundles": [], "summary": replay.summarise(rows), "runs": rows, "besideLive": "the live app answers"})
    assert "Cellpose-SAM, itself (reproducing what it made)" in report and "Run beside the app" in report


def test_the_replay_bench_waits_for_the_app(world, tmp_path, monkeypatch):
    paths, _, _ = corrected_population(world, tmp_path)
    now = time.time()
    out = bundles.write(paths, tmp_path / "out", start=iso(now - 120), stop=iso(now + 60))
    monkeypatch.setattr(live, "running", lambda: "the live app answers on http://127.0.0.1:8770")
    gpu = Finders()
    with pytest.raises(SystemExit, match="The replay bench waits for the app: the live app answers"):
        asyncio.run(replay.run([out], ["cellpose-sam"], gpu=gpu))
    assert gpu.calls == []
    result = asyncio.run(replay.run([out], ["cellpose-sam"], gpu=gpu, beside_live=True))
    assert result["besideLive"] == "the live app answers on http://127.0.0.1:8770" and gpu.calls


def test_a_replay_line_is_never_an_outcome(tmp_path):
    from orchestrator.outcomes import Outcomes

    log = tmp_path / "interactions.jsonl"
    line = {"kind": "execution", "id": "xr-1", "operation": "cells", "requested": "micro-sam-lm", "executed": "micro-sam-lm",
            "policy": "raw", "origin": "replay", "attempts": [{"impl": "micro-sam-lm", "ok": True}], "ms": 600,
            "startedAt": time.time(), "subject": "onion"}
    log.write_text(json.dumps(line) + "\n" + json.dumps({**line, "id": "xr-2", "origin": "brain-chosen"}) + "\n")
    assert [t.executions for t in Outcomes([log], None, None).tallies()] == [["xr-2"]]


def test_winning_needs_enough_held_out_corrections_agreed_without_finding_less():
    entry = lambda judged, agreed, keeps: {"ran": 3, "recorded": 100, "corrections": {"judged": judged, "agreed": agreed},  # noqa: E731
                                           "uncorrectedFound": keeps}
    rows = [{"ok": True, "recordedImpl": "cellpose-sam", "impl": "micro-sam-lm"}]
    assert replay.recommendation(entry(30, 20, 0.95), "micro-sam-lm", rows).startswith("wins on days it did not train on")
    assert replay.recommendation(entry(30, 20, 0.80), "micro-sam-lm", rows).startswith("does not win")
    assert replay.recommendation(entry(30, 10, 0.99), "micro-sam-lm", rows).startswith("does not win")
    assert replay.recommendation(entry(replay.MIN_CORRECTIONS - 1, 19, 1.0), "micro-sam-lm", rows).startswith("no recommendation")
    # a fine-tune is never judged on the sessions it trained on
    trained = [{"ok": True, "impl": "cellpose-sam-tuned", "subject": "s", "trainedOn": True, "population": "e1",
                "recordedImpl": "cellpose-sam"}]
    summary = replay.summarise(trained)["s"]["cellpose-sam-tuned"]
    assert summary["trainedOn"] == ["e1"] and "recorded" not in summary
    assert summary["recommendation"] == "no recommendation: every day here was one it trained on, or what it made itself"


def test_replays_let_their_views_go_so_many_populations_can_be_replayed(world, tmp_path):
    paths, _, _ = corrected_population(world, tmp_path)
    now = time.time()
    out = bundles.write(paths, tmp_path / "out", start=iso(now - 120), stop=iso(now + 60))
    tiles, hub = {}, replay.ReplayHub(Finders())
    # each replay holds its view in the unsaved allowance while it runs, and lets it go once compared
    result = asyncio.run(replay.run([out], ["cellpose-sam"] * 6, tiles=tiles, hub=hub))
    assert len(result["runs"]) == 6 and all(r["ok"] for r in result["runs"])
    reserved = hub.captures.memory.reserved
    assert {"interactive", "analysis", "unsaved"} <= set(reserved) and not any(reserved.values()) and not hub.evidence.items
    (key,) = tiles
    assert key[1] == "cellpose-sam" and tiles[key].shape[1] == 420


def test_an_undone_correction_is_no_truth(world, tmp_path):
    paths = keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    first = population.live()[0].id
    correct(world, population, "remove", [first], by="user", why="not a cell")
    saved(population)
    correct(world, population, "undo", [], by="user")
    saved(population)
    now = time.time()
    (rec,) = bundles.recorded(bundles.write(paths, tmp_path / "out", start=iso(now - 120), stop=iso(now + 60)))
    assert rec.current()[first] == "model" and replay.truth(rec) == []  # read from the latest state (finding 87)


def test_a_population_whose_copy_no_longer_matches_its_manifest_is_marked_incomplete(world, tmp_path, monkeypatch):
    """A save landing while the bundle copies a population (the review's note 12): the copy is checked, not the source."""
    import shutil as real

    paths, population, _ = corrected_population(world, tmp_path)
    copy2 = real.copy2

    def torn(source, target, *args, **kwargs):
        copy2(source, target, *args, **kwargs)
        if str(source).endswith("masks.npz"):
            Path(target).write_bytes(b"a later save's masks")

    monkeypatch.setattr(bundles.shutil, "copy2", torn)
    now = time.time()
    d = bundles.load(bundles.write(paths, tmp_path / "out", start=iso(now - 120), stop=iso(now + 60)))
    (p,) = d["populations"]
    assert p["status"] == "incomplete" and p["why"].startswith("masks.npz changed while it was copied")
    assert any(line.startswith(f"Population {population.id} is not saved whole") for line in d["lacks"])
    assert bundles.recorded(tmp_path / "out") == []  # never replayed as whole
