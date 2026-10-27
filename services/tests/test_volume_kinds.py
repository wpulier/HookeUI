"""New kinds of job on volumes (kinds.py, jobs.py, registrations.py; C6): a folder whose `[[implements]]` takes
`volume.3d` and gives a volume or a table runs as a job on one of the session's volumes, with no app code. It is proven on
the app's own generated volume (never its own example), what it gives is read back by the app by its sha256 and checked
against what it declared, its record names the volume and facts it ran on and its environment, and its table opens in the
measurements table only while it is the bytes it gave. With no brainreg installed, the volume's file is read by the app's
own Python. A stand-in uv (its environment's Python the app's own), a temporary catalog."""
import asyncio
import time
from types import SimpleNamespace

import numpy as np
import pytest
import tifffile

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, jobs, kinds, registrations, tables
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.kinds import add_kind_tools
from tests.test_env_runner import ENTRY, home, standin_uv  # noqa: F401 - a scratch catalog

IMPLEMENTS = 'operation = "cells"\nimpl = "dark-spots"\ninput = "image.2d.rgb8"\noutput = "labels.instance.2d"'

FOLDERS = {
    "volume-smoother": ("smooth-volume", "volume.3d", '''
import numpy as np
import tifffile
from pathlib import Path

def run(request):
    """A 3x3x3 mean of the chosen stack, written as a TIFF."""
    volume = tifffile.imread(request["input"]).astype(np.float32)
    p = np.pad(volume, 1, mode="edge")
    z, y, x = volume.shape
    out = sum(p[a:a + z, b:b + y, c:c + x] for a in range(3) for b in range(3) for c in range(3)) / 27
    tifffile.imwrite(Path(request["output"]) / "smoothed.tif", out.astype(np.float32))
    return {"outputs": {"volume": "smoothed.tif"}, "says": "smoothed"}
'''),
    "spot-counter": ("count-spots-3d", "table.objects", '''
import csv
import numpy as np
import tifffile
from pathlib import Path
from scipy import ndimage

def run(request):
    """Each bright spot's centre (z, y, x) and size in voxels, with the volume's voxel sizes it was given."""
    volume = tifffile.imread(request["input"])
    labels, n = ndimage.label(volume > 1500)
    rows = []
    for i, (z, y, x) in enumerate(ndimage.center_of_mass(volume > 1500, labels, range(1, n + 1))):
        rows.append([i + 1, round(z, 2), round(y, 2), round(x, 2), int((labels == i + 1).sum())])
    with open(Path(request["output"]) / "spots.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Spot", "z", "y", "x", "Voxels", "Tag"])
        w.writerows([r + [("007" if r[0] == 1 else "9007199254740993")] for r in rows])
    return {"outputs": {"table": "spots.csv"}, "says": f"{n} spots"}
'''),
    "flat-liar": ("flatten-volume", "volume.3d", '''
import numpy as np
import tifffile
from pathlib import Path

def run(request):
    """Says it gives a volume; gives one plane."""
    tifffile.imwrite(Path(request["output"]) / "flat.tif", np.zeros((8, 8), np.uint8))
    return {"outputs": {"volume": "flat.tif"}}
'''),
}


def add_folder(home, entry: str) -> catalog.Manifest:
    operation, output, adapter = FOLDERS[entry]
    folder = home / "repo" / "models" / entry
    folder.mkdir(parents=True, exist_ok=True)
    text = (ENTRY.format().replace(IMPLEMENTS, f'operation = "{operation}"\nimpl = "{entry}"\ninput = "volume.3d"\n'
                                               f'output = "{output}"')
            .replace("dark-spots", entry).replace('adapter = "adapter:segment"', 'adapter = "adapter:run"'))
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
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline and queue.wire()[0]["state"] not in ("done", "failed"):
        time.sleep(0.05)
    return queue.wire()[0]


class Store:
    sid = "2026-10-13"
    ids_used: dict = {}

    def __init__(self):
        self.made_events = []

    def made(self, kind, pid, **kw):
        self.made_events.append((kind, pid))


def wait(regs, jid, states=("done", "failed", "cancelled")) -> dict:
    for _ in range(600):
        record = regs.get(jid)
        if record["state"] in states:
            return record
        time.sleep(0.05)
    raise AssertionError(regs.get(jid))


@pytest.fixture
def on(home, monkeypatch):
    standin_uv(home, monkeypatch)
    for entry in ("volume-smoother", "spot-counter"):
        add_folder(home, entry)
        row = install(entry)
        assert row["state"] == "done", row
    monkeypatch.setattr(registrations, "ENTRY", "not-installed-here")  # no brainreg: the app reads the volume itself
    return home


def a_volume(home):
    path = home / "from-the-user" / "embryo.tif"
    path.parent.mkdir(exist_ok=True)
    jobs.fixture_volume(path)
    return path


def test_a_volume_kind_is_proven_on_the_apps_own_volume_and_is_no_picture_operation(on):
    smoother, counter = catalog.get("volume-smoother"), catalog.get("spot-counter")
    for m, words in ((smoother, "made a volume 24 × 96 × 96 (float32)"), (counter, "made a table of 5 rows")):
        proof = env_runner.proof(m)
        assert proof["ok"] and proof["says"].startswith(words), proof
        assert proof["output"] == m.implements[0].output and "app's example" in proof["input"]
    assert jobs.is_job(smoother) and jobs.is_job(counter)
    assert env_runner.register()["added"] == [], "a job on a volume is never an operation on the view"
    listed = {k["key"]: k for k in kinds.available()}
    assert listed["spot-counter"]["input"] == "volume.3d" and "on one of your volumes" in listed["spot-counter"]["says"]


def test_one_that_gives_a_plane_for_a_volume_is_never_on(home, monkeypatch):
    standin_uv(home, monkeypatch)
    add_folder(home, "flat-liar")
    install("flat-liar")
    proof = env_runner.proof(catalog.get("flat-liar"))
    assert proof is not None and not proof["ok"] and "not one volume of three spatial axes" in proof["why"], proof
    assert env_runner.in_use(catalog.get("flat-liar")) is None


def test_it_runs_on_a_volume_of_the_session_and_what_it_gave_is_read_back_by_its_sha256(on, tmp_path):
    store = Store()
    regs = registrations.Registrations(tmp_path / "jobs", store=store)
    volume = regs.add_volume(str(a_volume(on)))
    assert volume["series"][0]["shape"] == [24, 96, 96] and volume["outputs"]["preview"]["sha256"]
    assert regs.missing(volume, orientation=False) == ["its voxel sizes in µm (z, y, x): the file doesn't state them"]
    assert any("orientation" in n for n in regs.missing(volume)), "only a registration asks for an orientation"
    with pytest.raises(registrations.CantDo, match="voxel sizes"):
        regs.start_kind(volume["id"], "spot-counter")
    regs.describe(volume["id"], voxel_um=[2, .5, .5])

    smoothed = wait(regs, regs.start_kind(volume["id"], "volume-smoother")["id"])
    assert smoothed["state"] == "done", smoothed.get("error")
    assert smoothed["made"]["shape"] == [24, 96, 96] and smoothed["kindOf"]["output"] == "volume.3d"
    assert smoothed["environment"]["generation"] and smoothed["volume"]["sha256"] == volume["sha256"]
    assert regs.picture(smoothed["id"])[:8] == b"\x89PNG\r\n\x1a\n", "its middle plane, drawn by the app"
    assert "24 × 96 × 96" in regs.words(smoothed)

    counted = wait(regs, regs.start_kind(volume["id"], "spot-counter")["id"])
    assert counted["state"] == "done", counted.get("error")
    got = regs.table(counted["id"])
    assert [c["name"] for c in got["columns"]] == ["Spot", "z", "y", "x", "Voxels", "Tag"] and len(got["rows"]) == 5
    assert got["rows"][0][5] == "007" and got["rows"][1][5] == "9007199254740993", "ids kept exactly as written"
    assert isinstance(got["rows"][0][0], int) and isinstance(got["rows"][0][4], int)
    hub = SimpleNamespace(resources=SimpleNamespace(registrations=regs))
    table = tables.table_of(hub, counted["id"])
    assert table["count"] == 5 and "read back by its sha256" in table["note"]
    assert ("job", counted["id"]) in store.made_events

    out = regs.folder(counted) / "out" / "spots.csv"
    out.write_text(out.read_text() + "6,1,1,1,1,x\n")  # changed after it ended
    assert regs.table(counted["id"]) is None and tables.table_of(hub, counted["id"]) is None

    again = wait(regs, regs.again(smoothed["id"])["id"])
    assert again["state"] == "done" and again["again"] == smoothed["id"]
    assert again["facts"] == smoothed["facts"] and again["environment"] == smoothed["environment"]


def test_the_agent_runs_it_on_the_newest_volume_and_asks_for_what_is_missing(on, tmp_path):
    regs = registrations.Registrations(tmp_path / "jobs", store=Store())
    registry = Registry()
    add_kind_tools(registry, SimpleNamespace(kinds=kinds.available, hub=SimpleNamespace(), registrations=regs))
    tool = registry.tools["run_capability"]
    assert "volume" in tool.params["properties"]
    ctx = ToolContext("chat", 1, None, "count the spots in this stack")
    with pytest.raises(ToolFailed, match="this session has none"):
        asyncio.run(tool.handler(ctx, {"capability": "spot-counter"}))
    regs.add_volume(str(a_volume(on)))
    with pytest.raises(ToolFailed, match="voxel sizes"):
        asyncio.run(tool.handler(ctx, {"capability": "spot-counter"}))
    said = asyncio.run(tool.handler(ctx, {"capability": "spot-counter", "voxel_um": [2, .5, .5]}))
    jid = said.split("as job ")[1].split(",")[0]
    assert wait(regs, jid)["state"] == "done" and "a table" in said


def test_a_volume_whose_planes_are_too_large_is_refused_from_its_headers_before_any_is_decoded(on, tmp_path, monkeypatch):
    regs = registrations.Registrations(tmp_path / "jobs", store=Store())
    volume = regs.add_volume(str(a_volume(on)))
    regs.describe(volume["id"], voxel_um=[2, .5, .5])
    monkeypatch.setattr(jobs, "PLANE_MAX_BYTES", 1000)
    decoded = []
    import tifffile.tifffile as tiff

    real = tiff.TiffPage.asarray
    monkeypatch.setattr(tiff.TiffPage, "asarray", lambda self, *a, **k: decoded.append(1) or real(self, *a, **k))
    record = wait(regs, regs.start_kind(volume["id"], "volume-smoother")["id"])
    assert record["state"] == "failed" and "planes of" in record["error"] and not decoded


def test_a_cancel_while_its_output_is_checked_is_never_published_as_done(on, tmp_path, monkeypatch):
    regs = registrations.Registrations(tmp_path / "jobs", store=Store())
    volume = regs.add_volume(str(a_volume(on)))
    regs.describe(volume["id"], voxel_um=[2, .5, .5])
    real = jobs.accept_kind

    def cancelled_meanwhile(m, result, folder):
        for jid in list(regs._stop):
            regs._stop[jid] = True  # the user's cancel, while the app checks what it gave
        return real(m, result, folder)

    monkeypatch.setattr(jobs, "accept_kind", cancelled_meanwhile)
    record = wait(regs, regs.start_kind(volume["id"], "volume-smoother")["id"])
    assert record["state"] == "cancelled" and "made" not in record


def test_a_replay_runs_what_its_generation_declared_or_refuses(on, tmp_path):
    import json

    regs = registrations.Registrations(tmp_path / "jobs", store=Store())
    volume = regs.add_volume(str(a_volume(on)))
    regs.describe(volume["id"], voxel_um=[2, .5, .5])
    done = wait(regs, regs.start_kind(volume["id"], "volume-smoother")["id"])
    assert done["kindOf"] == {"operation": "smooth-volume", "output": "volume.3d"}
    done["kindOf"] = {"operation": "another-operation", "output": "volume.3d"}  # what an earlier version declared
    (regs.folder(done) / "record.json").write_text(json.dumps(done))
    with pytest.raises(registrations.CantDo, match="no longer declares what it ran"):
        regs.again(done["id"])
