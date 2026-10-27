"""New kinds of job added as folders, with no app code (kinds.py; C6's acceptance, Codex's cross-check at abf4afc7): a
denoiser and a chart-maker, each only a manifest, a lockfile and an adapter, installed and proven, become operations the
app runs through its own runner, asked for in plain words; what they make is kept and reopened only while it is the bytes
they made. A table-maker and a spot-finder too: their table opens in the measurements table, bound to its sha256; their
points become a measured population in the cells layer. The real app over stand-in workers (tests/capability_rig.py)."""
import asyncio
import time

import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, kinds, operations
from tests.test_env_runner import ENTRY, home, standin_uv  # noqa: F401 - a scratch catalog with the bench frame
from tests.waiting import until

IMPLEMENTS = 'operation = "cells"\nimpl = "dark-spots"\ninput = "image.2d.rgb8"\noutput = "labels.instance.2d"'

FOLDERS = {
    "denoiser": ("denoise", "image.2d.rgb8", '''
import numpy as np

def segment(picture, params):
    """A 3x3 mean: each pixel the average of its neighbours."""
    p = np.pad(picture.astype(np.float32), ((1, 1), (1, 1), (0, 0)), mode="edge")
    out = sum(p[y:y + picture.shape[0], x:x + picture.shape[1]] for y in range(3) for x in range(3)) / 9
    return out.round().clip(0, 255).astype(np.uint8)
'''),
    "chart-maker": ("brightness-chart", "image.2d.rgb8", '''
import numpy as np

def segment(picture, params):
    """A bar chart of the view's brightness in eight bands, top to bottom (no plotting library: bars drawn in numpy)."""
    bands = np.array_split(picture.mean(axis=2), 8, axis=0)
    chart = np.full((200, 320, 3), 255, np.uint8)
    for i, band in enumerate(bands):
        height = int(band.mean() / 255 * 180)
        chart[200 - height:, 10 + i * 38:10 + i * 38 + 30] = (40, 90, 200)
    return chart
'''),
    "band-table": ("brightness-table", "table.objects", '''
import numpy as np

def segment(picture, params):
    bands = np.array_split(picture.mean(axis=2), 4, axis=0)
    return {"columns": [{"name": "Band"}, {"name": "Brightness", "unit": "0-255", "definition": "mean grey of the band"}],
            "rows": [[i + 1, float(b.mean())] for i, b in enumerate(bands)]}
'''),
    "dark-outliner": ("dark-regions", "labels.instance.2d", '''
from scipy import ndimage

def segment(picture, params):
    labels, n = ndimage.label(picture.mean(axis=2) < 128)
    return labels.astype("int32")
'''),
    "spot-tracker": ("dark-spot-tracks", "tracks.graph", '''
import numpy as np
from scipy import ndimage

def segment(frames, params):
    """Each dark spot's centre in every frame it was given, linked to the nearest one in the frame before."""
    tracks = []
    for i, frame in enumerate(frames):
        dark = frame.mean(axis=2) < 128
        labels, n = ndimage.label(dark)
        sizes = ndimage.sum(dark, labels, range(1, n + 1))
        biggest = [int(k) + 1 for k in np.argsort(sizes)[::-1][:50] if sizes[k] >= 30]
        for y, x in (ndimage.center_of_mass(dark, labels, biggest) if biggest else []):
            near = [t for t in tracks if t[-1][0] == i - 1 and abs(t[-1][1] - x) + abs(t[-1][2] - y) < 20]
            (min(near, key=lambda t: abs(t[-1][1] - x) + abs(t[-1][2] - y)) if near else tracks.append([]) or tracks[-1]).append([i, float(x), float(y)])
    return {"tracks": [{"points": t} for t in tracks if len(t) >= 2]}
'''),
    "spot-finder": ("dark-spot-centres", "points.2d", '''
import numpy as np
from scipy import ndimage

def segment(picture, params):
    dark = picture.mean(axis=2) < 128
    labels, n = ndimage.label(dark)
    return {"points": [[float(x), float(y)] for y, x in ndimage.center_of_mass(dark, labels, range(1, n + 1))]}
'''),
}


def add_folder(home, entry: str) -> catalog.Manifest:
    """What a contributor writes: one folder, nothing else."""
    operation, output, adapter = FOLDERS[entry]
    folder = home / "repo" / "models" / entry
    folder.mkdir(parents=True, exist_ok=True)
    takes = catalog.KIND_INPUT.get(output, "image.2d.rgb8")
    text = ENTRY.format().replace(IMPLEMENTS, f'operation = "{operation}"\nimpl = "{entry}"\ninput = "{takes}"\n'
                                              f'output = "{output}"').replace("dark-spots", entry)
    (folder / "model.toml").write_text(text)
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(adapter)
    catalog.manifests.cache_clear()
    m = catalog.get(entry)
    assert m is not None, catalog.problems().get(entry)
    return m


def install(entry: str) -> dict:
    queue = envs.Installs()
    queue.start(entry)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and queue.wire()[0]["state"] not in ("done", "failed"):
        time.sleep(0.05)
    return queue.wire()[0]


@pytest.fixture
def rig(home, monkeypatch, tmp_path):
    from tests.capability_rig import Rig

    standin_uv(home, monkeypatch)
    for entry in FOLDERS:
        add_folder(home, entry)
        assert install(entry)["state"] == "done", entry
    made = Rig(tmp_path / "rig")
    made.app.catalog_changed()  # what an Install's end does: proven entries become operations, the tools refreshed
    yield made
    for op in list(kinds.registered()):
        operations.OPERATIONS.pop(op, None)
    kinds.registered().clear()


def ran(rig, words: str, capability: str):
    runs = asyncio.run(rig.turn([("run_capability", {"capability": capability, "by": "user"})], words))
    run = next(r for r in runs if r.tool == "run_capability")
    if not run.ok:  # the real error, kept in its execution (the runner says a raw one plainly)
        records = [rig.hub.runner.get(e) for e in run.executions]
        run.summary += " | " + "; ".join(a.error for x in records if x for a in x.attempts if a.error)
    return run


def test_a_denoiser_and_a_chart_maker_added_as_folders_run_with_no_app_code(rig):
    assert {"denoise", "brightness-chart"} <= set(operations.OPERATIONS), "each its own operation, from its folder"
    assert "run_capability" in rig.brain.registry.tools, "the agent has them once one is on"
    results = rig.hub.resources.results
    for words, capability, title in (("denoise the view", "denoiser", "denoiser"),
                                     ("chart the brightness", "chart-maker", "chart-maker")):
        run = ran(rig, words, capability)
        assert run.ok, run.summary
        assert "not the camera's picture" in run.summary or "made" in run.summary
        execution = rig.hub.runner.get(run.executions[0])
        assert execution.executed == capability and execution.operation in ("denoise", "brightness-chart")
        entry = next(e for e in reversed(results.entries()) if e.get("tool") == "run_capability")
        values = results.values(entry["id"])
        assert values["producer"]["implementation"] == capability and len(values["producer"]["sha256"]) == 64
        assert results.picture(entry["id"]) is not None, "kept, and reopened while it is the bytes it made"
        kept = results.root / entry["day"] / entry["pictures"][0]
        until(kept.exists)
        kept.write_bytes(kept.read_bytes()[:-3] + b"xyz")  # changed after it was kept
        results._pending.clear()
        assert results.picture(entry["id"]) is None, "never shown as if it were what it made"


def test_a_table_maker_opens_in_the_measurements_table_bound_to_its_sha256(rig):
    from orchestrator.tables import TableRefused, read_table, table_of

    run = ran(rig, "make a table of the brightness", "band-table")
    assert run.ok, run.summary
    entry = next(e for e in reversed(rig.hub.resources.results.entries()) if e.get("tool") == "run_capability")
    table = table_of(rig.hub, entry["id"])
    assert table["kind"] == "result-table" and table["count"] == 4
    assert [c["label"] for c in table["columns"]] == ["Band", "Brightness"] and table["columns"][1]["type"] == "number"
    assert table["definitions"] == {"brightness": "mean grey of the band"}
    read = asyncio.run(read_table(rig.app, entry["id"], rig.app.day.sid, 0))
    assert read["rows"] == table["rows"]
    values = rig.hub.resources.results.values(entry["id"])
    values["rows"][0][1] = 999.0  # what is kept is no longer what it gave
    rig.hub.resources.results.values = lambda rid, day=None: values
    with pytest.raises(TableRefused, match="sha256"):
        asyncio.run(read_table(rig.app, entry["id"], rig.app.day.sid, 0))


def test_a_spot_finders_points_stay_points_in_the_pixels_of_the_frame_it_ran_on(rig):
    """Points are never invented outlines (Codex's review): no area, no population, only where each is."""
    from orchestrator.evidence import evidence_of
    from orchestrator.tables import table_of

    run = ran(rig, "mark the dark spots", "spot-finder")
    assert run.ok, run.summary
    assert evidence_of(rig.hub).latest("dark-spot-centres") is None, "no population, so no sizes are claimed"
    entry = next(e for e in reversed(rig.hub.resources.results.entries()) if e.get("tool") == "run_capability")
    values = rig.hub.resources.results.values(entry["id"])
    assert values["points"] and values["frame"]["units"] == "px of this frame" and values["frame"]["width"] > 0
    table = table_of(rig.hub, entry["id"])
    assert [c["label"] for c in table["columns"]] == ["Point", "x", "y"] and table["count"] == len(values["points"])
    assert "no sizes" in table["note"]


def test_an_outliners_outlines_are_a_measured_population_in_the_cells_layer(rig):
    from orchestrator.evidence import evidence_of

    run = ran(rig, "outline the dark regions", "dark-outliner")
    assert run.ok, run.summary
    population = evidence_of(rig.hub).latest("dark-regions")
    assert population is not None and population.operation == "dark-regions" and len(population.live()) >= 1
    assert population.producer.get("output") == "labels.instance.2d" and len(population.producer.get("sha256") or "") == 64
    assert population.title.startswith("Outlines"), "named as outlines, never as cells"


def test_a_trackers_tracks_are_a_motion_item_in_slide_coordinates_with_speeds_the_app_measured(rig):
    from orchestrator.motiontracks import tracks_of

    rig.still()  # the slide at rest for the last few seconds: frames to give it
    run = ran(rig, "track the dark spots", "spot-tracker")
    assert run.ok, run.summary
    item = tracks_of(rig.hub).latest()
    assert item is not None and item.operation == "dark-spot-tracks" and item.implementation == "spot-tracker"
    assert item.rows and all(r["values"]["moving"] is None for r in item.rows), "the kind says where; the app measures"
    assert item.unit in ("px/s", "µm/s") and len(item.frames) >= 4 and item.producer.get("sha256")
    assert "tracks" in run.summary


def test_a_trackers_frames_are_admitted_before_they_are_decoded_and_held_until_its_worker_ends(rig, monkeypatch):
    """Codex's diagnosis of 621d0721: the clip's stack, a decode and the reply's limit reserved in the analysis allowance
    before anything is decoded, released once the owned worker has finished."""
    from orchestrator.capture import captures

    memory = captures(rig.hub).memory
    seen = []
    real = env_runner.Client.run

    def watched(self, picture, params=None, output=""):
        seen.append(memory.reserved["analysis"])
        return real(self, picture, params, output)

    monkeypatch.setattr(env_runner.Client, "run", watched)
    rig.still()
    before = memory.reserved["analysis"]
    run = ran(rig, "track the dark spots", "spot-tracker")
    assert run.ok, run.summary
    assert seen and seen[0] - before >= 4 * 8 * 8 * 3, "reserved before the worker was given anything"
    assert memory.reserved["analysis"] == before, "released once it ended"


def test_a_tracker_is_refused_whole_when_its_frames_dont_fit_the_allowance(rig, monkeypatch):
    from orchestrator.capture import captures

    from orchestrator.capture import Refused

    memory = captures(rig.hub).memory
    real = memory.reserve
    asked = []

    def full(cls, nbytes, why):
        if why.endswith("'s frames"):  # only the clip's own admission is refused: the request's look still fits
            asked.append(nbytes)
            raise Refused("held views are full", next="clear earlier analyses, or wait for them to finish")
        return real(cls, nbytes, why)

    monkeypatch.setattr(memory, "reserve", full)
    sent = []
    monkeypatch.setattr(env_runner.Client, "run", lambda self, *a, **k: sent.append(1))
    rig.still()
    run = ran(rig, "track the dark spots", "spot-tracker")
    assert asked and not sent, "refused before anything was decoded or sent"
    assert not run.ok and "held views are full" in run.summary
