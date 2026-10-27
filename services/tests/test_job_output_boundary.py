"""Contributed jobs expose only their own regular output files, including on reopen.

Toy adapters execute in actual child processes; their installer is the existing stand-in uv.
These tests establish parent-side path confinement, not an operating-system sandbox.
"""
import os

import pytest

from gpu_worker import manifests as catalog
from orchestrator import envs, jobs
from tests.test_env_runner import standin_uv
from tests.test_jobs import contribute, home, publish_data


@pytest.mark.parametrize("kind", ["absolute", "parent", "leaf-link", "directory-link", "directory", "fifo",
                                  "missing", "hardlink", "picture", "preview"])
def test_job_refuses_files_outside_its_owned_regular_outputs(home, monkeypatch, kind):
    contribute(home)
    (home / "repo/models/toy-reg/adapter.py").write_text('''
import os
from pathlib import Path

def work(request):
    if request.get("fixture"):
        return {"outputs": {}}
    out, outside = Path(request["output"]), Path(request["outside"])
    kind = request["kind"]
    name = "result.bin"
    if kind in ("absolute", "picture", "preview"):
        name = str(outside)
    elif kind == "parent":
        name = "../canary.txt"
    elif kind == "leaf-link":
        (out / name).symlink_to(outside)
    elif kind == "directory-link":
        (out / "linked").symlink_to(outside.parent, target_is_directory=True)
        name = "linked/canary.txt"
    elif kind == "directory":
        (out / name).mkdir()
    elif kind == "fifo":
        os.mkfifo(out / name)
    elif kind == "hardlink":
        os.link(outside, out / name)
    if kind in ("picture", "preview"):
        return {"outputs": {}, kind: name}
    return {"outputs": {"value": name}}
''')
    catalog.manifests.cache_clear()
    publish_data()
    standin_uv(home, monkeypatch)
    m = catalog.get("toy-reg")
    envs.install(m)
    canary = home / "canary.txt"
    canary.write_text("private canary")
    with pytest.raises(jobs.Failed, match="output"):
        jobs.run(m, {"kind": kind, "outside": str(canary)}, home / "job")
    assert canary.read_text() == "private canary"


@pytest.mark.parametrize("kind", ["absolute", "parent", "leaf-link", "directory-link", "root-link", "directory",
                                  "fifo", "missing", "hardlink", "changed", "unchanged"])
@pytest.mark.parametrize("prefix", ["j", "v"])
def test_reopened_pictures_confine_paths_and_verify_retained_content(tmp_path, kind, prefix):
    import hashlib
    from orchestrator.registrations import Registrations

    regs = Registrations(tmp_path / "jobs", today=lambda: "2026-10-13")
    record = {"id": prefix + "1", "session": "2026-10-13", "date": "2026-10-13", "state": "done"}
    subfolder, key = ("out", "picture") if prefix == "j" else ("inspect", "preview")
    root = regs.folder(record) / subfolder
    root.mkdir(parents=True)
    outside = tmp_path / "private"
    outside.mkdir()
    canary = outside / "canary.png"
    canary.write_bytes(b"private canary")
    name = "nested/picture.png"
    (root / "nested").mkdir()
    path = root / name
    original = b"retained scientific picture"
    if kind == "absolute":
        name = str(canary)
    elif kind == "parent":
        (root.parent / "canary.png").write_bytes(canary.read_bytes())
        name = "../canary.png"
    elif kind == "leaf-link":
        path.symlink_to(canary)
    elif kind == "directory-link":
        (root / "nested").rmdir()
        (root / "nested").symlink_to(outside, target_is_directory=True)
        name = "nested/canary.png"
    elif kind == "root-link":
        (root / "nested").rmdir()
        root.rmdir()
        root.symlink_to(outside, target_is_directory=True)
        name = "canary.png"
    elif kind == "directory":
        path.mkdir()
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "hardlink":
        os.link(canary, path)
    elif kind != "missing":
        path.write_bytes(original if kind == "unchanged" else b"changed since the run")
    record[key] = name
    # Even a matching digest never authorizes a path outside the job's own output tree.
    digest = original if kind in ("unchanged", "changed") else canary.read_bytes()
    record["outputs"] = {key: {"path": f"{subfolder}/{name}", "sha256": hashlib.sha256(digest).hexdigest()}}
    regs._write(record)
    reopened = Registrations(regs.root, today=lambda: "2026-10-13")
    assert reopened.picture(record["id"]) == (original if kind == "unchanged" else None)


def test_reopened_picture_rejects_a_directory_swapped_to_an_alias_during_open(tmp_path, monkeypatch):
    import hashlib
    from orchestrator.registrations import Registrations

    regs = Registrations(tmp_path / "jobs", today=lambda: "2026-10-13")
    record = {"id": "j1", "session": "2026-10-13", "date": "2026-10-13", "state": "done",
              "picture": "nested/picture.png", "outputs": {"picture": {"sha256": hashlib.sha256(b"safe").hexdigest()}}}
    nested = regs.folder(record) / "out/nested"
    nested.mkdir(parents=True)
    (nested / "picture.png").write_bytes(b"safe")
    outside = tmp_path / "private"
    outside.mkdir()
    (outside / "picture.png").write_bytes(b"private")
    regs._write(record)
    real_open = os.open
    swapped = []

    def open_at(name, flags, *args, **kwargs):
        if name == "nested" and "dir_fd" in kwargs and not swapped:
            nested.rename(nested.with_name("old"))
            nested.symlink_to(outside, target_is_directory=True)
            swapped.append(True)
        return real_open(name, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", open_at)
    assert regs.picture("j1") is None
    assert swapped, "exercise the race at the filesystem boundary, not just a pre-existing bad path"


@pytest.mark.parametrize("mutation", ["grow", "leaf-swap", "oversize"])
def test_reopened_picture_reads_one_bounded_file_identity(tmp_path, monkeypatch, mutation):
    import hashlib
    from orchestrator.registrations import Registrations

    regs = Registrations(tmp_path / "jobs", today=lambda: "2026-10-13")
    record = {"id": "j1", "session": "2026-10-13", "date": "2026-10-13", "state": "done",
              "picture": "picture.png", "outputs": {"picture": {"sha256": hashlib.sha256(b"safe").hexdigest()}}}
    path = regs.folder(record) / "out/picture.png"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"safe")
    canary = tmp_path / "private.png"
    canary.write_bytes(b"private")
    inode, canary_inode = path.stat().st_ino, canary.stat().st_ino
    regs._write(record)
    real_read, reads = os.read, []

    def read(fd, size):
        current = os.fstat(fd).st_ino
        assert current != canary_inode, "the parent must never read the alias target"
        if current == inode:
            if not reads:
                if mutation == "grow":
                    with path.open("ab") as stream:
                        stream.write(b" growing")
                elif mutation == "leaf-swap":
                    path.rename(path.with_suffix(".old"))
                    path.symlink_to(canary)
            reads.append(size)
        return real_read(fd, size)

    monkeypatch.setattr(os, "read", read)
    answer = regs.picture("j1", max_bytes=2 if mutation == "oversize" else 100)
    if mutation == "leaf-swap":
        assert answer in (None, b"safe")  # a pinned descriptor may finish its original file; never the new alias
    else:
        assert answer is None
    assert len(reads) <= 2
    if mutation == "oversize":
        assert reads == [], "reject by fstat before allocating or reading the large picture"


@pytest.mark.parametrize("bound", ["cancel", "inherited-cancel", "deadline", "inherited-deadline"])
@pytest.mark.parametrize("moment", ["chunk", "eof", "report"])
def test_parent_output_verification_obeys_the_original_job_bound(home, monkeypatch, bound, moment):
    import json
    import time
    from contextlib import nullcontext
    from orchestrator import owned_process

    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    folder = home / "job"
    target = folder / "registered.txt"
    stopped, read_sizes, clock_offset = [], [], [0]
    real_read, real_rename, monotonic = os.read, os.rename, time.monotonic

    def read(fd, size):
        if target.exists() and os.fstat(fd).st_ino == target.stat().st_ino:
            # One-byte chunks make the parent boundary deterministic without a large or slow scientific fixture.
            answer = real_read(fd, 1)
            read_sizes.append(len(answer))
            if (moment == "chunk" and answer) or (moment == "eof" and not answer):
                stopped.append(True)
                clock_offset[0] = 60
            return answer
        return real_read(fd, size)

    def rename(source, destination, *args, **kwargs):
        answer = real_rename(source, destination, *args, **kwargs)
        if moment == "report" and destination == "resources.json":
            stopped.append(True)
            clock_offset[0] = 60
        return answer

    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(os, "rename", rename)
    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + clock_offset[0])
    stop = lambda: bool(stopped) and "cancel" in bound
    caller = (owned_process.scope(seconds=10 if bound == "inherited-deadline" else None, stop=stop)
              if bound.startswith("inherited") else nullcontext())
    expected = jobs.Cancelled if "cancel" in bound else jobs.Failed
    with caller:
        with pytest.raises(expected, match="cancelled" if "cancel" in bound else "past its limit"):
            jobs.run(m, {"input": "brain.tif"}, folder, stop=stop if bound == "cancel" else lambda: False,
                     limit_s=10 if bound == "deadline" else None)
        # The caller scope also checks on exit; observe the job's own result separately.
        stopped.clear()
        clock_offset[0] = 0
    assert read_sizes, "the child finished and the parent began reading its scientific output"
    if moment == "chunk":
        assert len(read_sizes) == 1, "cancellation/deadline is checked between output chunks"
    evidence = json.loads((folder / "resources.json").read_text())
    assert evidence["outcome"] == ("cancelled" if "cancel" in bound else "failed")
    assert jobs.MEMORY.reserved_mb == 0


@pytest.mark.parametrize("name", ["resources.json", "Resources.JSON", "request.json", "result.json", "job.log",
                                  "science/resources.json"])
@pytest.mark.parametrize("field", ["outputs", "picture", "preview"])
def test_job_control_files_are_not_scientific_outputs(home, monkeypatch, name, field):
    import hashlib

    contribute(home)
    (home / "repo/models/toy-reg/adapter.py").write_text('''
from pathlib import Path

def work(request):
    if request.get("fixture"):
        return {"outputs": {}}
    name = request["name"]
    path = Path(request["output"]) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"scientific bytes")
    field = request["field"]
    return {"outputs": {"value": name}} if field == "outputs" else {"outputs": {}, field: name}
''')
    catalog.manifests.cache_clear()
    publish_data()
    standin_uv(home, monkeypatch)
    m = catalog.get("toy-reg")
    envs.install(m)
    folder = home / "job"
    request = {"name": name, "field": field}
    if name.startswith("science/"):
        result = jobs.run(m, request, folder)
        retained = (folder / name).read_bytes()
        assert result["outputFiles"][name] == {"sha256": hashlib.sha256(retained).hexdigest(), "bytes": len(retained)}
    else:
        with pytest.raises(jobs.Failed, match="reserved.*control"):
            jobs.run(m, request, folder)


@pytest.mark.parametrize("bound", ["cancel", "deadline"])
def test_a_stopped_parent_hash_cannot_publish_a_successful_proof(home, monkeypatch, bound):
    import json
    import time
    from orchestrator import env_runner
    from tests.test_jobs import ATLAS

    m = contribute(home)
    publish_data()
    standin_uv(home, monkeypatch)
    envs.install(m)
    proof = env_runner.proof_path(m)
    before = proof.read_bytes()
    stopped, clock_offset = [], [0]
    real_read, monotonic = os.read, time.monotonic

    def read(fd, size):
        answer = real_read(fd, size)
        if answer == ATLAS.upper():  # the toy adapter's scientific output, after its child has ended
            stopped.append(True)
            clock_offset[0] = 60
        return answer

    monkeypatch.setattr(os, "read", read)
    monkeypatch.setattr(time, "monotonic", lambda: monotonic() + clock_offset[0])
    if bound == "cancel":
        with pytest.raises(jobs.Cancelled):
            jobs.prove(m, stop=lambda: bool(stopped), limit_s=10)
        assert proof.read_bytes() == before, "cancellation never publishes a new proof"
    else:
        failed = jobs.prove(m, limit_s=10)
        assert not failed["ok"] and "past its limit" in failed["why"]
        assert not json.loads(proof.read_text())["ok"]
        assert failed["resources"]["outcome"] == "failed"
    assert stopped
    assert jobs.MEMORY.reserved_mb == 0
