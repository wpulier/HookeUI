"""Jobs and native recipes (orchestrator/jobs.py, envs.py; phase 3's E5): an entry whose work is a job (brainreg's
registration) runs its adapter in its own environment as a process group, reads the data it names from the catalog and
nothing from the network, is proven by a real run on its own fixture, and a cancel ends all of it; a native recipe's
pinned packages are checked by their hashes and unpacked into the environment, which its commands then prove. A
stand-in uv (tests/test_envs.fake_uv) and toy entries in a temporary catalog."""
import hashlib
import json
import os
import shutil
import tarfile
import time
import zipfile

import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import env_runner, envs, jobs, readiness
from tests.test_env_runner import standin_uv
from tests.waiting import until

ATLAS = b"a tiny atlas\n"
DATA = '''id = "tiny-atlas"
name = "Tiny atlas"
runs = "this-mac"
version = "tiny 1"
licence = ["allen-mouse-atlas"]
kind = "data"
what = "A tiny atlas"
source = {{ url = "https://example.org/atlas" }}
needs_to_get = ["download"]
files = [{{ path = "atlas.txt", bytes = {n}, sha256 = "{sha}", url = "https://example.org/atlas.txt" }}]
'''
JOB = '''id = "toy-reg"
name = "Toy registration"
runs = "this-mac"
version = "toy 0.1"
licence = ["brainreg"]
kind = "tool-function"
what = "Registers a toy volume"
runner = "python-env"
source = {{ url = "https://example.org/toy" }}
adapter = "adapter:work"
env = {{ python = "3.13.5", lock = "env.lock", imports = ["json"]{native} }}
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
import json, os, subprocess, sys, time
from pathlib import Path

def work(request):
    out = Path(request["output"])
    atlas = (Path(request["data"]["tiny-atlas"]) / "atlas.txt").read_text()
    if request.get("sleep"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        (out / "child.pid").write_text(str(child.pid))
        time.sleep(60)
    if request.get("lie"):
        return {"outputs": {"atlas": "missing.tiff"}}
    (out / "registered.txt").write_text(atlas.upper())
    what = "its example" if request.get("fixture") else request["input"]
    return {"outputs": {"atlas": "registered.txt"}, "network": os.environ.get("MICROMIND_REFUSE_NETWORK"),
            "says": f"registered {what}: 1 region measured"}
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "models"
    shutil.copytree(catalog.ROOT, root)
    monkeypatch.setattr(catalog, "ROOT", root)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.delenv("MICROMIND_ENVS", raising=False)
    catalog.manifests.cache_clear()
    yield tmp_path
    catalog.manifests.cache_clear()


def contribute(home, native: str = "") -> catalog.Manifest:
    for entry, text in (("tiny-atlas", DATA.format(n=len(ATLAS), sha=hashlib.sha256(ATLAS).hexdigest())),
                        ("toy-reg", JOB.format(native=native))):
        folder = home / "repo" / "models" / entry
        folder.mkdir(exist_ok=True)
        (folder / "model.toml").write_text(text)
    folder = home / "repo" / "models" / "toy-reg"
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(ADAPTER)
    catalog.manifests.cache_clear()
    m = catalog.get("toy-reg")
    assert m is not None, catalog.problems()
    return m


def publish_data() -> None:
    d = catalog.get("tiny-atlas")
    folder = model_files.revision_dir(d)
    folder.mkdir(parents=True)
    (folder / "atlas.txt").write_bytes(ATLAS)
    (folder / model_files.RECEIPT).write_text(json.dumps({"revision": catalog.revision_of(d)}))


def test_a_job_reads_its_data_from_the_catalog_and_is_proven_by_a_real_run(home, monkeypatch):
    m = contribute(home)
    assert jobs.is_job(m)
    monkeypatch.delenv("MICROMIND_MODEL_FILES", raising=False)  # the tests' stand-in counts every model as downloaded
    step = readiness.model("toy-reg", {}).steps[0]
    assert step.kind == "download" and step.says.startswith("Download Tiny atlas") and step.model == "tiny-atlas"
    publish_data()
    assert [s.kind for s in readiness.model("toy-reg", {}).steps] == ["install"]
    standin_uv(home, monkeypatch)
    d, lines = envs.install(m)
    assert lines[-1] == "Its proof: registered its example: 1 region measured"
    found = env_runner.in_use(m)
    assert found is not None and found.current and found.proof["ok"]
    assert list(readiness.model("toy-reg", {}).steps) == []
    assert env_runner.register()["added"] == [], "a job is no picture operation: never a Cells choice"
    out = home / "job"
    r = jobs.run(m, {"input": "brain.tif"}, out)
    assert (out / "registered.txt").read_text() == ATLAS.decode().upper()
    assert r["network"] == "1", "its code never reaches the network"
    request = json.loads((out / "request.json").read_text())
    assert request["data"] == {"tiny-atlas": str(model_files.published(catalog.get("tiny-atlas")))}
    with pytest.raises(jobs.Failed, match="said it wrote missing.tiff, which isn't there"):
        jobs.run(m, {"input": "x", "lie": True}, home / "job2")


def test_a_cancel_ends_the_whole_job(home, monkeypatch):
    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    out = home / "job"
    t0 = time.monotonic()
    with pytest.raises(jobs.Cancelled):
        jobs.run(m, {"input": "x", "sleep": True}, out, stop=lambda: (out / "child.pid").exists())
    assert time.monotonic() - t0 < 30
    child = int((out / "child.pid").read_text())
    assert _gone(child), "the job's own child outlived the cancel"


def test_without_its_data_a_job_says_what_to_download(home, monkeypatch):
    m = contribute(home)
    with pytest.raises(jobs.Failed, match="Tiny atlas isn't downloaded"):
        jobs.data_dirs(m)


def _package(home, name: str, files: dict[str, bytes], kind: str) -> tuple[dict, "os.PathLike"]:
    src = home / f"pkg-{name}"
    for rel, data in {**files, "info/index.json": b"{}"}.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)
        if rel.startswith("bin/"):
            (src / rel).chmod(0o755)
    path = home / f"{name}.{kind}"
    if kind == "tar.bz2":
        with tarfile.open(path, "w:bz2") as t:
            for p in sorted(src.rglob("*")):
                t.add(p, arcname=str(p.relative_to(src)), recursive=False)
    else:
        with zipfile.ZipFile(path, "w") as z:  # a .conda's outer zip; what is inside never matters: it is refused
            z.writestr("metadata.json", "{}")
    data = path.read_bytes()
    return {"name": name, "url": f"https://example.org/{path.name}", "bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest()}, path


def test_a_native_recipe_is_checked_unpacked_and_proven_by_its_commands(home, monkeypatch):
    tool, tool_path = _package(home, "tool", {"bin/tool": b"#!/bin/sh\n[ -n \"$CONDA_PREFIX\" ] && echo ok\n"}, "tar.bz2")
    lib, lib_path = _package(home, "lib", {"lib/libx.1.dylib": b"x"}, "tar.bz2")
    by_url = {tool["url"]: tool_path, lib["url"]: lib_path}

    def fetch(pkg, cache, stop=lambda: False):
        path = by_url[pkg["url"]]
        if hashlib.sha256(path.read_bytes()).hexdigest() != pkg["sha256"]:
            raise envs.Failed("not the file the catalog pins")
        return path

    monkeypatch.setattr(envs, "fetch_native", fetch)
    native = ", commands = [\"tool -v\"], native = [" + ", ".join(
        "{{ name = \"{name}\", url = \"{url}\", bytes = {bytes}, sha256 = \"{sha256}\" }}".format(**p) for p in (tool, lib)) + "]"
    m = contribute(home, native)
    publish_data()
    standin_uv(home, monkeypatch)
    d = envs.build(m)
    assert (d / "bin" / "tool").exists() and (d / "lib" / "libx.1.dylib").read_bytes() == b"x"
    assert not (d / "info").exists(), "a package's own metadata is not part of the environment"
    receipt = json.loads((d / envs.RECEIPT).read_text())
    assert [p["name"] for p in receipt["native"]] == ["tool", "lib"]
    assert envs.clean_env(m, d)["CONDA_PREFIX"] == str(d), "brainreg takes NiftyReg from the environment's prefix"


def test_a_native_package_that_is_not_the_one_pinned_is_refused(home, tmp_path):
    good, path = _package(home, "tool", {"bin/tool": b"#!/bin/sh\n"}, "tar.bz2")
    pinned = {**good, "url": path.as_uri(), "sha256": "0" * 64}
    with pytest.raises(envs.Failed, match="not the file the catalog pins"):
        envs.fetch_native(pinned, tmp_path / "cache")
    assert envs.fetch_native({**good, "url": path.as_uri()}, tmp_path / "cache").read_bytes() == path.read_bytes()


def test_a_native_package_is_its_tar_bz2_build_never_a_conda_needing_this_macs_zstd(home, tmp_path):
    """The app unpacks a package with Python's own tarfile: a .conda's zstd needed whatever zstd the system's tar found
    (Homebrew's here, 2026-10-13), so a recipe pins the .tar.bz2 build and a .conda is refused, in the manifest too."""
    _, path = _package(home, "lib", {"lib/libx.1.dylib": b"x"}, "conda")
    with pytest.raises(envs.Failed, match="pinned as its .tar.bz2 build"):
        envs.unpack_native(path, tmp_path / "prefix")
    pkg = '{ name = "lib", url = "https://example.org/lib.conda", bytes = 1, sha256 = "' + "0" * 64 + '" }'
    with pytest.raises(AssertionError, match=r"\.tar\.bz2 build"):
        contribute(home, ", native = [" + pkg + "]")  # the entry is not in the catalog: its problem says why


def _gone(pid: int) -> bool:
    def ended() -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    return until(ended, every=0.1, what=f"process {pid} to end")


def test_a_job_past_its_limit_is_ended_whole_and_fails_saying_so(home, monkeypatch):
    """The review of 2026-10-13, finding 4: a proof never runs unbounded. Past its limit the job's whole process group
    ends (its own child too) and it fails, said."""
    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    out = home / "job"
    with pytest.raises(jobs.Failed, match="ran past its limit of 2 s and was ended"):
        jobs.run(m, {"input": "x", "sleep": True}, out, limit_s=2)
    assert _gone(int((out / "child.pid").read_text())), "the job's own child outlived its limit"


def test_a_proof_ends_with_its_install_and_past_its_limit(home, monkeypatch):
    """Install's Cancel reaches the proof (envs.install → env_runner.prove → jobs.prove → jobs.run), which keeps no
    proof; a proof past its limit is a failed proof, kept."""
    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    asked = {}

    def cancelled(*a, **kw):
        asked.update(kw)
        raise jobs.Cancelled("cancelled")

    monkeypatch.setattr(jobs, "run", cancelled)
    proof = env_runner.proof_path(m)
    proof.unlink()
    stop = lambda: True  # noqa: E731
    with pytest.raises(jobs.Cancelled):
        env_runner.prove(m, stop=stop)
    assert asked["stop"] is stop and asked["limit_s"] == jobs.PROOF_LIMIT_S and not proof.exists()
    monkeypatch.setattr(jobs, "run", lambda *a, **kw: (_ for _ in ()).throw(jobs.Failed("toy ran past its limit")))
    assert jobs.prove(m) == {**json.loads(proof.read_text()), "ok": False, "why": "toy ran past its limit"}


def test_job_cancellation_inherited_from_its_caller_ends_only_its_owned_child(home, monkeypatch, wall_clock):
    """An off-loop request cancelled by its owner releases the job, even without a separate job Cancel callback."""
    import threading
    from orchestrator import owned_process

    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    out = home / "caller-owned-job"
    # A bounded rescue keeps the red implementation from leaving a process behind.
    rescue = threading.Event()
    timer = threading.Timer(2, rescue.set)
    timer.start()
    began = time.monotonic()
    try:
        with pytest.raises(jobs.Cancelled):
            with owned_process.scope(stop=lambda: (out / "child.pid").exists()):
                jobs.run(m, {"input": "x", "sleep": True}, out, stop=rescue.is_set)
    finally:
        rescue.set()
        timer.cancel()
    wall_clock.within(time.monotonic() - began, 1.5, "the cancellation")  # it reached its job before the rescue
    assert _gone(int((out / "child.pid").read_text()))


def test_job_group_ends_when_its_proof_supervisor_disappears(home, monkeypatch):
    """Killing one proof supervisor cannot leave its native children computing or kill a different proof."""
    import subprocess
    import sys

    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    out = home / "owned-proof"
    script = f"""
from pathlib import Path
from gpu_worker import manifests as catalog
from orchestrator import jobs
catalog.ROOT = Path({str(catalog.ROOT)!r})
catalog.manifests.cache_clear()
folder = Path({str(out)!r})
jobs.run(catalog.get('toy-reg'), {{'input': 'x', 'sleep': True}}, folder,
         started=lambda pid: (folder / 'job.pid').write_text(str(pid)))
"""
    parent = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        until(lambda: (out / "child.pid").exists() or parent.poll() is not None)
        assert (out / "child.pid").exists(), parent.poll()
        parent.kill()
        parent.wait(timeout=2)
        assert _gone(int((out / "child.pid").read_text())), "native child outlived its proof supervisor"
    finally:
        if parent.poll() is None:
            parent.kill()
        parent.wait(timeout=2)
        if (out / "job.pid").exists():
            jobs.end(int((out / "job.pid").read_text()))
        parent.stderr.close()


def test_job_refuses_memory_pressure_before_start_and_releases_after_completion(home, monkeypatch):
    from orchestrator import machine

    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    out = home / "pressure-job"
    started = []
    monkeypatch.setattr(machine, "free_mb", lambda: 1.0)
    with pytest.raises(jobs.Failed, match="memory"):
        jobs.run(m, {"input": "x"}, out, started=started.append)
    assert started == [] and not (out / "result.json").exists()
    monkeypatch.setattr(machine, "free_mb", lambda: 65536.0)
    result = jobs.run(m, {"input": "x"}, out, started=started.append)
    assert result["ok"] and result["resources"]["reservedMb"] > 0
    assert json.loads((out / "resources.json").read_text())["outcome"] == "done"
    assert jobs.MEMORY.reserved_mb == 0


def test_concurrent_jobs_reserve_memory_until_cancellation_finishes(home, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from orchestrator import machine

    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    monkeypatch.setattr(machine, "free_mb", lambda: 800.0)  # fits one 256 MB job and the existing 512 MB margin
    first, stop = home / "first", threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(jobs.run, m, {"input": "x", "sleep": True}, first, stop=stop.is_set, limit_s=3)
        try:
            until(lambda: (first / "child.pid").exists() or future.done())
            assert (first / "child.pid").exists()
            started = []
            with pytest.raises(jobs.Failed, match="memory"):
                jobs.run(m, {"input": "x"}, home / "second", started=started.append)
            assert started == [] and jobs.MEMORY.reserved_mb > 0
        finally:
            stop.set()
            with pytest.raises(jobs.Cancelled):
                future.result(timeout=4)
    assert _gone(int((first / "child.pid").read_text())) and jobs.MEMORY.reserved_mb == 0
    assert jobs.run(m, {"input": "x"}, home / "retry")["resources"]["outcome"] == "done"


def test_observed_job_overrun_keeps_evidence_and_ends_its_native_child(home, monkeypatch):
    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    out = home / "overrun"
    # Controlled sensor, real process group: no large allocation is needed to exercise the safety outcome.
    monkeypatch.setattr(jobs, "process_memory_mb", lambda pid: (2048.0 if (out / "child.pid").exists() else 1.0,
                                                               "test controlled footprint"))
    with pytest.raises(jobs.Failed, match="exceeded its reserved memory"):
        jobs.run(m, {"input": "x", "sleep": True}, out, limit_s=3)
    record = json.loads((out / "resources.json").read_text())
    assert record["outcome"] == "failed" and record["peakMb"] == 2048 and record["samples"] >= 1
    assert "reserved memory" in record["why"] and _gone(int((out / "child.pid").read_text()))
    assert jobs.MEMORY.reserved_mb == 0


@pytest.mark.parametrize("pinned_data", [False, True])
def test_downloaded_data_cannot_bypass_its_edition_at_the_job_boundary(home, monkeypatch, pinned_data):
    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    request = {"input": "x"}
    if pinned_data:
        request["data"] = jobs.data_dirs(m)  # replay's exact revision still carries its component restriction
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    started = []
    with pytest.raises(jobs.Failed, match="non-commercial"):
        jobs.run(m, request, home / "not-permitted", started=started.append)
    assert started == [], "already downloaded and previously proven does not authorize restricted data"


def test_downloaded_data_restriction_is_visible_in_the_parent_models_readiness(home, monkeypatch):
    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    ready = readiness.model(m.key, {})
    assert ready.status == "unavailable" and not ready.available
    assert ready.next.kind == "edition" and "Allen Mouse Brain Atlas" in ready.why


def test_the_same_job_twice_at_once_runs_once_and_the_second_never_starts(home, monkeypatch):
    """L7b (ticket 62 row 12, F094): a second click on the same job (same entry, generation, input and settings) while
    the first runs is refused before anything starts; the first carries on, and once it has ended the same job runs."""
    from concurrent.futures import ThreadPoolExecutor
    import threading

    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    first, stop = home / "first", threading.Event()
    asked = {"input": "brain.tif", "sleep": True}
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(jobs.run, m, dict(asked), first, stop=stop.is_set, limit_s=10)
        try:
            until(lambda: (first / "child.pid").exists() or future.done())
            assert (first / "child.pid").exists()
            started = []
            # The admission workspace is memory to reserve, not the work: still the same job.
            with pytest.raises(jobs.Duplicate, match="already running .* this one was not started"):
                jobs.run(m, {**asked, "workspace_bytes": 1}, home / "second", started=started.append)
            assert started == [] and not (home / "second").exists(), "nothing of the second was started or written"
            assert not future.done(), "the running one carries on"
            other = jobs.run(m, {"input": "another.tif"}, home / "other")  # other work is never held back
            assert other["resources"]["outcome"] == "done"
        finally:
            stop.set()
            with pytest.raises(jobs.Cancelled):
                future.result(timeout=8)
    assert jobs.run(m, {"input": "brain.tif"}, home / "again")["resources"]["outcome"] == "done"
    assert jobs.identity(m, {"input": "a", "workspace_bytes": 5}, None) == jobs.identity(m, {"input": "a"}, None)
    assert jobs.identity(m, {"input": "a"}, None, "inspect") != jobs.identity(m, {"input": "a"}, None)
