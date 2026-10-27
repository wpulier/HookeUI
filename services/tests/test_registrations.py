"""Volumes and their registrations (registrations.py; phase 3's E5, the review's corrections 5 and 6): a volume is kept
as it was with what its file states and what the user said, each with its source; a registration asks only for what is
missing (never guessing an orientation), records exactly what it ran on and with, keeps every output by its hash, is
cancelled whole, is interrupted (never resumed by itself) when the app stops, and is reopened without computing again.
A toy job entry standing in for brainreg (its adapter's inspect and register), a stand-in uv, a temporary catalog."""
import asyncio
import hashlib
import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import envs, jobs, registrations
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.volumes import add_volume_tools
from tests.test_env_runner import standin_uv
from tests.test_jobs import ATLAS, DATA, publish_data
from tests.waiting import until

TOY = '''id = "toy-reg"
name = "Toy registration"
runs = "this-mac"
version = "toy 0.1"
licence = ["brainreg"]
kind = "tool-function"
what = "Registers a toy volume"
runner = "python-env"
source = { url = "https://example.org/toy" }
adapter = "adapter:register"
env = { python = "3.13.5", lock = "env.lock", imports = ["json"] }
needs_to_run = ["install:toy-reg", "data:tiny-atlas"]
host = "own"
documented_mb = 10
load_limit_s = 10

[[implements]]
operation = "register-atlas"
impl = "toy-reg"
input = "volume.3d"
output = "registration.atlas"
'''
ADAPTER = '''
import json, time
from pathlib import Path

def inspect(request):
    out = Path(request["output"]); out.mkdir(parents=True, exist_ok=True)
    text = Path(request["input"]).read_text()
    (out / "preview.png").write_bytes(b"png:" + text.encode())
    stated = [50.0, 40.0, 40.0] if "stated" in text else None
    channels = 2 if "two channels" in text else 1
    if "two series" in text:  # each its own scale, axes and channels, as the real adapter reads them
        return {"series": [{"axes": "ZYX", "spatial": "ZYX", "shape": [4, 6, 7], "dtype": "uint8", "channels": 1,
                            "times": 1, "voxel_um": [2.0, 1.0, 1.0]},
                           {"axes": "ZCYX", "spatial": "ZYX", "shape": [3, 8, 9], "dtype": "uint16", "channels": 2,
                            "times": 1, "voxel_um": [5.0, 0.5, 0.5]},
                           {"axes": "YXZ", "spatial": "YXZ", "shape": [8, 9, 3], "dtype": "uint16", "channels": 1,
                            "times": 1, "voxel_um": None}],
                "voxel_um": None, "outputs": {"preview": "preview.png"}}
    return {"series": [{"axes": "ZCYX" if channels > 1 else "ZYX", "shape": [3, 4, 5], "dtype": "uint16",
                        "channels": channels, "times": 1}], "voxel_um": stated, "outputs": {"preview": "preview.png"}}

def register(request):
    out = Path(request["output"])
    if request.get("fixture"):
        return {"outputs": {}, "says": "registered its example"}
    if "slow" in Path(request["input"]).read_text():
        time.sleep(60)
    (out / "brainreg").mkdir(exist_ok=True)
    (out / "brainreg" / "volumes.csv").write_text("structure_name,left_volume_mm3,right_volume_mm3,total_volume_mm3\\n"
        "cortex,1.0,1.5,2.5\\nthalamus,0.2,0.3,0.5\\nhippocampus,0.6,0.7,1.3\\n")
    (out / "brainreg" / "picture.png").write_bytes(b"picture")
    (out / "brainreg" / "request.json").write_text(json.dumps(request))
    return {"outputs": {"volumes": "brainreg/volumes.csv", "picture": "brainreg/picture.png",
                        "seen": "brainreg/request.json"}, "picture": "brainreg/picture.png", "says": "registered it"}
'''


class Store:
    """A session store's two parts the registrations use: its id and its journal's "made" events."""
    sid = "2026-10-13"
    ids_used: dict = {}

    def __init__(self):
        self.made_events = []

    def made(self, kind, pid, **kw):
        self.made_events.append((kind, pid))


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "models"
    shutil.copytree(catalog.ROOT, root)
    monkeypatch.setattr(catalog, "ROOT", root)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.delenv("MICROMIND_ENVS", raising=False)
    for entry, text in (("tiny-atlas", DATA.format(n=len(ATLAS), sha=hashlib.sha256(ATLAS).hexdigest())), ("toy-reg", TOY)):
        (root / entry).mkdir()
        (root / entry / "model.toml").write_text(text)
    (root / "toy-reg" / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (root / "toy-reg" / "adapter.py").write_text(ADAPTER)
    catalog.manifests.cache_clear()
    monkeypatch.setattr(registrations, "ENTRY", "toy-reg")
    monkeypatch.setattr(registrations, "ATLAS", "tiny-atlas")
    publish_data()
    standin_uv(tmp_path, monkeypatch)
    envs.install(catalog.get("toy-reg"))
    yield tmp_path
    catalog.manifests.cache_clear()


def volume_file(home, text: str, name: str = "brain.tif") -> Path:
    path = home / "from-the-user" / name
    path.parent.mkdir(exist_ok=True)
    path.write_text(text)
    return path


def wait(regs, jid, states=("done", "failed", "cancelled")) -> dict:
    def ended() -> dict | None:
        record = regs.get(jid)
        return record if record["state"] in states else None

    return until(ended, every=0.05, what=f"{jid} to be {' or '.join(states)}")


def test_a_volume_is_kept_with_what_its_file_states_and_registered_once_told_the_rest(home):
    store, said = Store(), []
    regs = registrations.Registrations(home / "jobs", store=store, said=lambda r: said.append(r["state"]))
    original = volume_file(home, "a brain, voxel sizes stated")
    v = regs.add_volume(str(original))
    assert v["id"] == "v1" and (regs.folder(v) / "brain.tif").read_text() == original.read_text()
    assert v["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
    assert v["facts"]["voxel_um"] == {"value": [50.0, 40.0, 40.0], "source": "file"}
    assert regs.missing(v) == ["its orientation, BrainGlobe's three letters for its axes (e.g. psl: towards posterior, "
                               "superior and left)"], "the orientation is always asked, never read from the file"
    with pytest.raises(registrations.CantDo, match="before it can be registered, say its orientation"):
        regs.start("v1")
    with pytest.raises(registrations.CantDo, match="one of a/p, one of s/i, one of l/r"):
        regs.describe("v1", orientation="pps")
    regs.describe("v1", orientation="PSL")
    job = regs.start("v1", thread="t1")
    done = wait(regs, job["id"])
    assert done["state"] == "done" and said[:1] == ["queued"] and said[-1] == "done"
    assert done["volume"]["sha256"] == v["sha256"] and done["facts"]["orientation"]["value"] == "psl"
    env = done["environment"]
    assert env["generation"] == envs.folder(catalog.get("toy-reg")).name and len(env["lockSha256"]) == 64
    assert done["atlas"]["id"] == "tiny-atlas" and done["atlas"]["revision"].startswith("files-")
    out = regs.folder(done)
    for key, o in done["outputs"].items():
        assert hashlib.sha256((out / o["path"]).read_bytes()).hexdigest() == o["sha256"], key
    asked = json.loads((out / "out" / "brainreg" / "request.json").read_text())
    assert asked["voxel_um"] == [50.0, 40.0, 40.0] and asked["orientation"] == "psl" and asked["data"]["tiny-atlas"]
    assert [r["name"] for r in done["regions"]] == ["cortex", "hippocampus", "thalamus"], "largest first"
    assert regs.picture(job["id"]) == b"picture" and regs.picture("v1").startswith(b"png:")
    assert ("volume", "v1") in store.made_events and ("job", job["id"]) in store.made_events
    words = regs.words(done)
    assert "registered to the Allen mouse atlas" in words and "cortex 2.5 mm³" in words and "downsampled space" in words


def test_what_the_file_doesnt_state_is_asked_and_kept_as_the_users(home):
    regs = registrations.Registrations(home / "jobs", store=Store())
    v = regs.add_volume(str(volume_file(home, "a brain in two channels")))
    assert regs.missing(v) == ["which channel to register (it holds 2)",
                               "its voxel sizes in µm (z, y, x): the file doesn't state them",
                               "its orientation, BrainGlobe's three letters for its axes (e.g. psl: towards posterior, "
                               "superior and left)"]
    v = regs.describe("v1", voxel_um=[10, 5, 5], orientation="asr", channel=1)
    assert v["facts"]["voxel_um"]["source"] == "user" and v["facts"]["channel"] == {**v["facts"]["channel"], "value": 1}
    assert regs.missing(v) == []
    with pytest.raises(registrations.CantDo, match="three numbers in µm"):
        regs.describe("v1", voxel_um=[10, 5])
    with pytest.raises(registrations.CantDo, match="a TIFF or OME-TIFF"):
        regs.add_volume(str(volume_file(home, "x", "notes.txt")))


def test_a_cancel_ends_the_job_and_it_can_be_run_again(home):
    regs = registrations.Registrations(home / "jobs", store=Store())
    regs.add_volume(str(volume_file(home, "a slow brain, voxel sizes stated")))
    regs.describe("v1", orientation="psl")
    job = regs.start("v1")
    wait(regs, job["id"], ("running",))
    until(lambda: regs.get(job["id"]).get("pid"), every=0.05)
    regs.cancel(job["id"])
    record = wait(regs, job["id"])
    assert record["state"] == "cancelled" and "never a result" in record["says"]
    again = regs.again(job["id"])
    assert again["again"] == job["id"] and again["volume"]["id"] == "v1" and again["id"] != job["id"]
    regs.cancel(again["id"])
    wait(regs, again["id"])


def test_a_job_running_when_the_app_stopped_is_interrupted_never_resumed(home):
    regs = registrations.Registrations(home / "jobs", store=Store())
    regs.add_volume(str(volume_file(home, "a brain, voxel sizes stated")))
    regs.describe("v1", orientation="psl")
    done = wait(regs, regs.start("v1")["id"])
    path = regs.folder(done) / "record.json"
    path.write_text(json.dumps({**done, "state": "running", "pid": 999999}))
    after = registrations.Registrations(home / "jobs", store=Store())  # the app started again
    found = after.interrupted()
    assert [r["id"] for r in found] == [done["id"]] and after.get(done["id"])["state"] == "interrupted"
    assert "pid" not in after.get(done["id"]) and "run it again" in after.get(done["id"])["says"]


def test_the_agent_registers_asking_only_for_what_is_missing(home):
    regs = registrations.Registrations(home / "jobs", store=Store())
    registry = Registry()
    add_volume_tools(registry, SimpleNamespace(registrations=regs))
    ctx = ToolContext("chat", 1, None, "register this brain to the Allen atlas")
    run = registry.tools["register_volume"].handler
    with pytest.raises(ToolFailed, match="the live view is a 2D picture"):
        asyncio.run(run(ctx, {}))
    regs.add_volume(str(volume_file(home, "a brain")))
    with pytest.raises(ToolFailed) as refused:
        asyncio.run(run(ctx, {}))
    assert "voxel sizes in µm" in str(refused.value) and "orientation" in str(refused.value)
    assert refused.value.next == "ask the user for exactly these, then call register_volume again with them"
    said = asyncio.run(run(ctx, {"voxel_um": [50, 40, 40], "orientation": "psl"}))
    assert said.startswith("Started registering brain (v1) to the Allen mouse atlas") and "job j1" in said
    record = wait(regs, "j1")
    assert record["by"] == "agent" and record["facts"]["voxel_um"]["source"] == "user"


@pytest.mark.parametrize("switch_at", ["before_tool", "thread_dispatch"])
def test_the_agent_keeps_the_asking_sessions_volume_through_dispatch(home, monkeypatch, switch_at):
    from orchestrator.day.model import Asked

    store = Store()
    origin = store.sid
    regs = registrations.Registrations(home / "jobs", store=store)
    original = regs.add_volume(str(volume_file(home, "voxel sizes stated", "original.tif")))
    store.sid = "another-session"
    regs.add_volume(str(volume_file(home, "voxel sizes stated", "different.tif")))
    store.sid = origin if switch_at == "thread_dispatch" else "another-session"
    dispatch = asyncio.to_thread

    async def switch_then_dispatch(fn, *args, **kwargs):
        store.sid = "another-session"
        return await dispatch(fn, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", switch_then_dispatch)
    registry = Registry()
    add_volume_tools(registry, SimpleNamespace(registrations=regs))
    ctx = ToolContext("chat", 1, None, "register this brain", asked=Asked("t1", 1, session=origin))
    try:
        said = asyncio.run(registry.tools["register_volume"].handler(ctx, {"orientation": "psl"}))
        assert "original" in said
        assert regs.records("j", "another-session") == []
        record = regs.records("j", origin)[0]
        assert record["volume"]["id"] == original["id"] and record["thread"] == "t1"
    finally:
        regs.stop()


def test_job_reserves_decode_workspace_for_unselected_channels_too(home):
    """A non-paged series must be decoded before channel selection; admission covers that temporary array too."""
    regs = registrations.Registrations(home / "jobs", store=Store())
    volume = regs.add_volume(str(volume_file(home, "two channels, voxel sizes stated")))
    regs.describe(volume["id"], orientation="psl", channel=1)
    record = wait(regs, regs.start(volume["id"])["id"])
    assert record["state"] == "done"
    request = json.loads((regs.folder(record) / "out" / "request.json").read_text())
    # The inspected series has 3×4×5 uint16 voxels per channel, two channels, and a 308-voxel target atlas grid.
    assert request["workspace_bytes"] >= 2 * (3 * 4 * 5 * 2 * 2) + 48 * 308


def test_a_stopping_app_ends_its_jobs_and_one_left_by_a_crash_is_ended_at_start(home):
    """A job runs in a process group of its own, so it would outlive the app (live 2026-10-13): the app's stop ends it,
    recorded interrupted, and at start one a crash left running is ended, only when the pid is still our job's."""
    regs = registrations.Registrations(home / "jobs", store=Store())
    regs.add_volume(str(volume_file(home, "a slow brain, voxel sizes stated")))
    regs.describe("v1", orientation="psl")
    job = regs.start("v1")
    until(lambda: regs.get(job["id"]).get("pid"), every=0.05)
    pid = regs.get(job["id"])["pid"]
    assert jobs.ours(pid, regs.folder(job)), "the pid is kept on disk as soon as the job starts"
    assert not jobs.ours(pid, home / "elsewhere")
    regs.stop()
    record = regs.get(job["id"])
    assert record["state"] == "interrupted" and "run it again" in record["says"]
    assert not jobs.ours(pid, regs.folder(job)), "ended (a zombie not yet reaped is no longer our job's command)"

    crashed = registrations.Registrations(home / "jobs", store=Store())
    again = crashed.again(job["id"])
    until(lambda: crashed.get(again["id"]).get("pid"), every=0.05)
    pid = crashed.get(again["id"])["pid"]
    after = registrations.Registrations(home / "jobs", store=Store())  # started again; `crashed` never stopped
    assert [r["id"] for r in after.interrupted()] == [again["id"]]
    assert not jobs.ours(pid, regs.folder(again))
    crashed._stop.clear()


def test_a_file_of_several_series_is_told_which_and_a_series_never_keeps_anothers_facts(home):
    """Finding 2 of the review of 2026-10-13: a series' scale, axes, channel and time are its own. Several series: the
    user says which first; changing it clears what the other series was told."""
    regs = registrations.Registrations(home / "jobs", store=Store())
    v = regs.add_volume(str(volume_file(home, "two series")))
    assert regs.missing(v) == ["which series to register (it holds 3: 0: 4 × 6 × 7 (ZYX); 1: 3 × 8 × 9 (ZCYX); "
                               "2: 8 × 9 × 3 (YXZ))"]
    with pytest.raises(registrations.CantDo):
        regs.start("v1")
    with pytest.raises(registrations.CantDo, match="say which series first"):
        regs.describe("v1", channel=1)
    v = regs.describe("v1", series=1)
    assert v["facts"]["voxel_um"] == {"value": [5.0, 0.5, 0.5], "source": "file"} and v["facts"]["axes"]["value"] == "ZCYX"
    v = regs.describe("v1", channel=1, orientation="psl")
    assert regs.missing(v) == []
    v = regs.describe("v1", series=0)  # another series: series 1's channel is not series 0's
    assert v["facts"]["voxel_um"] == {"value": [2.0, 1.0, 1.0], "source": "file"} and v["facts"]["channel"]["value"] is None
    assert v["facts"]["orientation"]["value"] == "psl", "the same spatial axes: the orientation still holds"
    assert v["facts"]["series"]["cleared"]["voxel_um"]["value"] == [5.0, 0.5, 0.5], "what was cleared is kept"
    v = regs.describe("v1", voxel_um=[9, 9, 9])
    v = regs.describe("v1", series=2)  # no stated scale and other axes: the user's scale and orientation are asked again
    assert v["facts"]["voxel_um"]["value"] is None and v["facts"]["orientation"]["value"] is None
    assert "its voxel sizes in µm (z, y, x): the file doesn't state them" in regs.missing(v)


def test_run_again_repeats_the_recorded_job_or_says_exactly_what_changed(home):
    """Finding 3 of the review of 2026-10-13: Run again is the recorded job (its facts, environment generation and atlas
    revision), whatever the volume or the catalog say now; when any of them is gone it says which, and runs nothing."""
    regs = registrations.Registrations(home / "jobs", store=Store())
    regs.add_volume(str(volume_file(home, "a brain, voxel sizes stated")))
    regs.describe("v1", orientation="psl")
    first = wait(regs, regs.start("v1")["id"])
    regs.describe("v1", voxel_um=[10, 10, 10], orientation="asr")  # the volume's facts change since
    m = catalog.get("toy-reg")
    lock = catalog.ROOT / "toy-reg" / "env.lock"  # and the catalog's brainreg: a new generation, now in use
    lock.write_text("numpy==2.0.1 --hash=sha256:" + "b" * 64 + "\n")
    catalog.manifests.cache_clear()
    envs.install(catalog.get("toy-reg"))
    from orchestrator import env_runner

    env_runner.choose(catalog.get("toy-reg"), None)  # preparing an update no longer switches implicitly
    assert env_runner.in_use(catalog.get("toy-reg")).folder.name != first["environment"]["generation"]
    again = wait(regs, regs.again(first["id"])["id"])
    assert again["state"] == "done" and again["again"] == first["id"]
    assert again["environment"] == first["environment"] and again["atlas"] == first["atlas"]
    asked = json.loads((regs.folder(again) / "out" / "brainreg" / "request.json").read_text())
    assert asked["voxel_um"] == [50.0, 40.0, 40.0] and asked["orientation"] == "psl", "the recorded facts, not today's"
    fresh = wait(regs, regs.start("v1")["id"])  # Register is the new run, with what is here now
    assert fresh["facts"]["orientation"]["value"] == "asr" and fresh["environment"] != first["environment"]

    shutil.rmtree(home / "catalog" / "envs" / "toy-reg" / first["environment"]["generation"])
    (regs.folder(regs.get("v1")) / "brain.tif").write_text("another brain")
    with pytest.raises(registrations.CantDo) as refused:
        regs.again(first["id"])
    said = str(refused.value)
    assert "file is not the one it ran on" in said and f"environment {first['environment']['generation']}" in said
    assert "Register the volume to run it as a new job" in said
    assert len(regs.records("j")) == 3, "nothing was run"
    del m


@pytest.mark.parametrize("damage", ["same-size-change", "invalid-verification"])
def test_exact_replay_rejects_a_changed_or_invalid_recorded_atlas(home, damage):
    from gpu_worker import model_files

    regs = registrations.Registrations(home / "jobs", store=Store())
    regs.add_volume(str(volume_file(home, "voxel sizes stated")))
    regs.describe("v1", orientation="psl")
    record = wait(regs, regs.start("v1")["id"])
    atlas = catalog.get("tiny-atlas")
    if damage == "same-size-change":
        (model_files.revision_dir(atlas) / "atlas.txt").write_bytes(b"x" * len(ATLAS))
    else:
        model_files.verification_path(atlas).write_text(json.dumps({"ok": False, "problem": "changed"}))
    try:
        with pytest.raises(registrations.CantDo, match="atlas"):
            regs.again(record["id"])
        assert len(regs.records("j")) == 1, "no replay starts with changed recorded data"
    finally:
        regs.stop()


def test_exact_replay_uses_recorded_file_identities_after_the_atlas_catalog_updates(home):
    regs = registrations.Registrations(home / "jobs", store=Store())
    regs.add_volume(str(volume_file(home, "voxel sizes stated")))
    regs.describe("v1", orientation="psl")
    record = wait(regs, regs.start("v1")["id"])
    manifest = catalog.ROOT / "tiny-atlas" / "model.toml"
    manifest.write_text(manifest.read_text().replace(hashlib.sha256(ATLAS).hexdigest(), "b" * 64))
    catalog.manifests.cache_clear()
    replay = wait(regs, regs.again(record["id"])["id"])
    assert replay["state"] == "done" and replay["atlas"] == record["atlas"]
    assert record["atlas"]["files"][0]["sha256"] == hashlib.sha256(ATLAS).hexdigest()


def test_published_queued_job_already_owns_its_runtime(home):
    m = catalog.get('toy-reg')
    checked = []
    def said(record):
        if record['state'] == 'queued':
            with pytest.raises(envs.Failed, match='running work'):
                envs.remove(m)
            checked.append(record['id'])
    regs = registrations.Registrations(home / 'jobs', store=Store(), said=said)
    volume = regs.add_volume(str(volume_file(home, 'voxel sizes stated')))
    regs.describe(volume['id'], orientation='psl')
    job = regs.start(volume['id'])
    assert wait(regs, job['id'])['state'] == 'done'
    assert checked == [job['id']]
