"""Volume output acceptance through real job interfaces with bounded fixtures.

Codec headers can describe large planes without allocating pixels. Execution fixtures
use synthetic retained prerequisites and the existing interpreter, never an install,
proof, downloaded model or external service.
"""
from types import SimpleNamespace
import os

import numpy as np
import pytest
import tifffile

from orchestrator import job_outputs, jobs, machine
from orchestrator.loads import MARGIN_MB
from tests.test_env_runner import home  # noqa: F401 - isolated catalog, no installer
from tests.waiting import HANG_S


def volume_entry():
    return SimpleNamespace(name="Fixture volume", runner="python-env", implements=[
        SimpleNamespace(input="volume.3d", output="volume.3d")])


def test_volume_dtype_expansion_and_rendering_are_admitted_before_decoding(tmp_path, monkeypatch):
    path = tmp_path / "output.tif"
    path.write_bytes(b"synthetic header fixture; no large pixels")
    decoded = []

    def decode():
        decoded.append(True)
        raise AssertionError("large header reached allocation before memory admission")

    page = SimpleNamespace(asarray=decode)
    series = SimpleNamespace(axes="ZYX", shape=(2, 4096, 4096), dtype=np.dtype("uint8"),
                             pages=[page, page], keyframe=SimpleNamespace(shape=(4096, 4096)))

    class HeaderOnlyTiff:
        def __init__(self, _stream):
            self.series = [series]

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

    monkeypatch.setattr(tifffile, "TiffFile", HeaderOnlyTiff)
    # 100 MiB cannot cover the source plane, float conversion, percentile copy and rendering.
    monkeypatch.setattr(machine, "free_mb", lambda: MARGIN_MB + 100)
    with job_outputs.Folder(tmp_path) as folder:
        result = {"outputs": {"volume": path.name}, "outputFiles": {path.name: folder.identity(path.name)}}
        with pytest.raises(jobs.Failed, match="not enough free memory"):
            jobs.accept_kind(volume_entry(), result, folder)
    assert not decoded, "no pixel allocation is attempted when its peak cannot fit"
    assert jobs.MEMORY.reserved_mb == 0, "failed admission releases the encoded-file reservation"


def test_encoded_chunks_and_joined_copy_are_admitted_before_reading(tmp_path, monkeypatch):
    path = tmp_path / "output.tif"
    tifffile.imwrite(path, np.zeros((2, 8, 8), np.uint8), photometric="minisblack")
    path.write_bytes(path.read_bytes().ljust(8192, b"\0"))
    reads = []
    inode = path.stat().st_ino
    read = os.read

    def observed_read(fd, size):
        if os.fstat(fd).st_ino == inode:
            reads.append(size)
        return read(fd, size)

    with job_outputs.Folder(tmp_path) as folder:
        result = {"outputs": {"volume": path.name}, "outputFiles": {path.name: folder.identity(path.name)}}
        monkeypatch.setattr(os, "read", observed_read)
        # Enough for one encoded copy and overhead, but not chunks plus their joined result.
        monkeypatch.setattr(machine, "free_mb", lambda: MARGIN_MB + 16 + 12 / 1024)
        with pytest.raises(jobs.Failed, match="not enough free memory"):
            jobs.accept_kind(volume_entry(), result, folder)
    assert not reads, "encoded storage is refused before its first allocation"
    assert jobs.MEMORY.reserved_mb == 0


@pytest.fixture
def local_job(home, monkeypatch):
    import json
    import shlex
    import sys
    import threading

    from orchestrator import envs, registrations
    from tests.test_volume_kinds import Store, add_folder

    m = add_folder(home, "volume-smoother")
    folder = envs.folder(m)
    kept = folder / "entry" / m.id
    kept.mkdir(parents=True)
    envs.copy(m, kept)
    (folder / "bin").mkdir()
    python = folder / "bin/python"
    python.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    python.chmod(0o755)
    (folder / "receipt.json").write_text(json.dumps({"at": "2000-01-01", "synthetic": True}))
    folder.with_name(folder.name + ".proof.json").write_text(json.dumps({"ok": True, "synthetic": True}))
    monkeypatch.setattr(machine, "free_mb", lambda: MARGIN_MB + 4096)
    store, terminal, transitions = Store(), threading.Event(), []

    def said(record):
        transitions.append(dict(record))
        if record["state"] in ("done", "failed", "cancelled", "interrupted"):
            terminal.set()

    regs = registrations.Registrations(home / "jobs", store=store, said=said)
    path = home / "tiny.tif"
    tifffile.imwrite(path, np.arange(128, dtype=np.uint16).reshape(2, 8, 8), photometric="minisblack")
    volume = regs.add_volume(str(path))
    regs.describe(volume["id"], voxel_um=[1, 1, 1])
    yield regs, volume, store, terminal, transitions
    regs.stop(wait_s=2)


@pytest.mark.parametrize("action", ["cancel", "shutdown"])
def test_cancel_accepted_during_final_preview_write_never_publishes_done(local_job, monkeypatch, action):
    import threading
    from pathlib import Path
    from orchestrator import registrations

    regs, volume, store, terminal, transitions = local_job
    writing, release = threading.Event(), threading.Event()
    write = Path.write_bytes

    def pause_picture(path, data):
        if path.name == "made.png.tmp":
            writing.set()
            assert release.wait(HANG_S), "test did not release the preview write"
        return write(path, data)

    monkeypatch.setattr(Path, "write_bytes", pause_picture)
    record = regs.start_kind(volume["id"], "volume-smoother")
    sid, jid = record["session"], record["id"]
    try:
        assert writing.wait(HANG_S), transitions
        store.sid = "another-session"
        if action == "cancel":
            assert regs.cancel(jid, session=sid)["state"] == "running"
        else:
            regs.stop(wait_s=0)
    finally:
        release.set()
    assert terminal.wait(HANG_S), transitions
    done = regs.get(jid, sid)
    assert done["state"] == ("cancelled" if action == "cancel" else "interrupted")
    assert "made" not in done and "outputs" not in done
    assert done["resources"]["outcome"] == "cancelled", "accepted cancellation cannot retain done resource evidence"
    assert all(r["state"] != "done" for r in transitions)
    assert regs.get(jid) is None, "the original session owns the terminal record"
    assert jobs.MEMORY.reserved_mb == 0
    with pytest.raises(registrations.CantDo, match="not running"):
        regs.cancel(jid, session=sid)


@pytest.mark.parametrize("publication", ["preview", "terminal-write", "terminal-rename"])
def test_final_publication_keeps_the_original_deadline(local_job, monkeypatch, publication):
    import time
    from pathlib import Path

    regs, volume, _store, terminal, transitions = local_job
    import json
    write, replace, clock, advanced = Path.write_bytes, Path.replace, time.monotonic, False

    def success_record(path):
        return path.name == "record.json.tmp" and json.loads(path.read_bytes()).get("state") == "done"

    def cross_deadline(path, data):
        nonlocal advanced
        result = write(path, data)
        if ((publication == "preview" and path.name == "made.png.tmp") or
                (publication == "terminal-write" and success_record(path))):
            advanced = True
        return result

    def rename_past_deadline(path, target):
        nonlocal advanced
        success = publication == "terminal-rename" and success_record(path)
        result = replace(path, target)
        if success:
            advanced = True
        return result

    monkeypatch.setattr(Path, "write_bytes", cross_deadline)
    monkeypatch.setattr(Path, "replace", rename_past_deadline)
    monkeypatch.setattr(time, "monotonic", lambda: clock() + (jobs.PROOF_LIMIT_S + 1 if advanced else 0))
    record = regs.start_kind(volume["id"], "volume-smoother")
    assert terminal.wait(HANG_S), transitions
    done = regs.get(record["id"])
    assert advanced and done["state"] == "failed" and "past its limit" in done["error"]
    assert "made" not in done and "outputs" not in done and done["resources"]["outcome"] == "failed"
    assert jobs.MEMORY.reserved_mb == 0


def test_cancel_racing_terminal_publication_observes_the_committed_state(local_job, monkeypatch):
    import json
    import threading
    from concurrent.futures import ThreadPoolExecutor, TimeoutError
    from pathlib import Path
    from orchestrator import registrations

    regs, volume, _store, terminal, transitions = local_job
    writing, release, asking = threading.Event(), threading.Event(), threading.Event()
    write = Path.write_bytes

    def pause_commit(path, data):
        if path.name == "record.json.tmp" and json.loads(data).get("state") == "done":
            writing.set()
            assert release.wait(HANG_S), "test did not release terminal publication"
        return write(path, data)

    monkeypatch.setattr(Path, "write_bytes", pause_commit)
    record = regs.start_kind(volume["id"], "volume-smoother")
    jid, sid = record["id"], record["session"]

    def cancel():
        asking.set()
        return regs.cancel(jid, session=sid)

    with ThreadPoolExecutor(max_workers=1) as caller:
        try:
            assert writing.wait(HANG_S), transitions
            attempt = caller.submit(cancel)
            assert asking.wait(HANG_S)
            with pytest.raises(TimeoutError):
                attempt.result(timeout=.05)  # the terminal transition owns the decision until its record is durable
        finally:
            release.set()
        with pytest.raises(registrations.CantDo, match="done, not running"):
            attempt.result(timeout=HANG_S)
    assert terminal.wait(HANG_S), transitions
    done = regs.get(jid, sid)
    assert done["state"] == "done" and done["resources"]["outcome"] == "done"
    assert regs.picture(jid, sid).startswith(b"\x89PNG\r\n\x1a\n")
    assert sum(r["state"] == "done" for r in transitions) == 1
    assert jobs.MEMORY.reserved_mb == 0


@pytest.mark.parametrize("dtype", ["uint8", "uint16", "float64"])
def test_an_admitted_tiny_volume_returns_its_actual_shape_and_middle_plane(tmp_path, monkeypatch, dtype):
    import cv2

    path = tmp_path / "output.tif"
    tifffile.imwrite(path, np.arange(128).reshape(2, 8, 8).astype(dtype), photometric="minisblack")
    monkeypatch.setattr(machine, "free_mb", lambda: MARGIN_MB + 1024)
    with job_outputs.Folder(tmp_path) as folder:
        result = {"outputs": {"volume": path.name}, "outputFiles": {path.name: folder.identity(path.name)}}
        accepted = jobs.accept_kind(volume_entry(), result, folder)
    assert accepted["shape"] == [2, 8, 8] and accepted["dtype"] == dtype
    picture = cv2.imdecode(np.frombuffer(accepted["picture"], np.uint8), cv2.IMREAD_GRAYSCALE)
    assert picture.shape == (8, 8) and picture[0, 0] == 0 and picture[-1, -1] == 255
    assert jobs.MEMORY.reserved_mb == 0


@pytest.mark.parametrize("caller", ["command", "agent"])
def test_async_cancellation_keeps_the_loop_live_and_the_original_session(local_job, monkeypatch, caller):
    import asyncio
    import json
    import threading
    from pathlib import Path
    from orchestrator import commands
    from orchestrator.brain.registry import Registry, ToolFailed
    from orchestrator.brain.toolbox.volumes import add_volume_tools

    regs, volume, store, terminal, transitions = local_job
    writing, release, progressed = threading.Event(), threading.Event(), threading.Event()
    write = Path.write_bytes

    def pause_commit(path, data):
        if path.name == "record.json.tmp" and json.loads(data).get("state") == "done":
            writing.set()
            assert release.wait(HANG_S), "test did not release publication"
        return write(path, data)

    monkeypatch.setattr(Path, "write_bytes", pause_commit)
    record = regs.start_kind(volume["id"], "volume-smoother")
    assert writing.wait(HANG_S)
    sid, jid = record["session"], record["id"]
    registry = Registry()
    add_volume_tools(registry, SimpleNamespace(registrations=regs))
    ctx = SimpleNamespace(asked=SimpleNamespace(session=sid))

    async def use():
        async def loop_progress():
            await asyncio.sleep(.02)
            progressed.set()
            store.sid = "different-session"
            release.set()
        heartbeat = asyncio.create_task(loop_progress())
        try:
            if caller == "command":
                result = await commands.job_cancel(SimpleNamespace(registrations=regs), None, {"id": jid})
            else:
                result = await registry.tools["register_volume"].handler(ctx, {"cancel": True})
        except ToolFailed as exc:
            result = str(exc)
        await heartbeat
        return result

    # A stuck event loop must fail finitely instead of deadlocking the regression runner.
    heartbeat_before_release = []
    def watchdog_release():
        heartbeat_before_release.append(progressed.is_set())
        release.set()
    watchdog = threading.Timer(HANG_S, watchdog_release)
    watchdog.start()
    try:
        result = asyncio.run(use())
        assert progressed.is_set() and terminal.wait(HANG_S)
        assert regs.get(jid, sid)["state"] in ("done", "cancelled")
        assert "no job" not in str(result) and "no registration" not in str(result)
        assert not heartbeat_before_release or heartbeat_before_release[0], "the async cancellation caller blocked independent loop progress"
    finally:
        release.set()
        watchdog.cancel()


@pytest.mark.parametrize("fault", ["write", "rename-after", "persistent", "persistent-both"])
def test_terminal_storage_failure_is_visible_and_retires_cancellation(local_job, monkeypatch, fault):
    import json
    from pathlib import Path
    from orchestrator import registrations

    regs, volume, store, terminal, transitions = local_job
    write, replace, rename, failed = Path.write_bytes, Path.replace, os.rename, False

    def refused_write(path, data):
        nonlocal failed
        if path.name == "record.json.tmp":
            state = json.loads(data).get("state")
            if state == "done" and fault != "rename-after":
                failed = True
                raise OSError("controlled terminal storage failure")
            if failed and fault.startswith("persistent"):
                raise OSError("controlled disk still unavailable")
        return write(path, data)

    def refused_rename(path, target):
        nonlocal failed
        is_done = path.name == "record.json.tmp" and json.loads(path.read_bytes()).get("state") == "done"
        result = replace(path, target)
        if is_done and fault == "rename-after":
            failed = True
            assert regs.get(record_id[0])["state"] == "running", "unaccepted disk bytes cannot appear as a result"
            raise OSError("controlled terminal storage failure after rename")
        return result

    def refused_resource_replace(source, target, **kwargs):
        if failed and fault == "persistent-both" and target == "resources.json":
            raise OSError("controlled resource storage failure")
        return rename(source, target, **kwargs)

    monkeypatch.setattr(Path, "write_bytes", refused_write)
    monkeypatch.setattr(Path, "replace", refused_rename)
    monkeypatch.setattr(os, "rename", refused_resource_replace)
    record_id = []
    record = regs.start_kind(volume["id"], "volume-smoother")
    record_id.append(record["id"])
    sid, jid = record["session"], record["id"]
    assert terminal.wait(HANG_S), "a storage failure must still notify a terminal outcome"
    done = regs.get(jid, sid)
    assert failed and done["state"] == "failed" and "storage failure" in done["error"]
    assert "made" not in done and "outputs" not in done and regs.picture(jid, sid) is None
    assert done["resources"]["outcome"] == "failed" and "pid" not in done
    assert all(r["state"] != "done" for r in transitions)
    assert sum(r["state"] == "failed" for r in transitions) == 1
    with pytest.raises(registrations.CantDo, match="failed, not running"):
        regs.cancel(jid, sid)
    if fault.startswith("persistent"):
        assert done["persistence"]["saved"] is False
        assert "restart" in done["persistence"]["warning"] and "restart" in regs.words(done)
    else:
        reopened = registrations.Registrations(regs.root, store=store).get(jid, sid)
        assert reopened["state"] == "failed" and reopened["resources"]["outcome"] == "failed"
    if fault == "persistent-both":
        assert "resource storage failure" in done["resources"]["persistenceError"]
    assert jobs.MEMORY.reserved_mb == 0


def test_cancel_during_parent_output_read_keeps_the_job_failure_contract(local_job, monkeypatch):
    import threading

    regs, volume, _store, terminal, transitions = local_job
    reading, release = threading.Event(), threading.Event()
    read, target_inode = os.read, []

    def pause_output(fd, size):
        # Discover the actual adapter's output through the fixture's filesystem, without replacing its runner.
        if not target_inode:
            outputs = list(regs.root.glob("*/j*/out/*.tif"))
            target_inode.extend(p.stat().st_ino for p in outputs)
        if os.fstat(fd).st_ino in target_inode:
            reading.set()
            assert release.wait(HANG_S)
        return read(fd, size)

    monkeypatch.setattr(os, "read", pause_output)
    record = regs.start_kind(volume["id"], "volume-smoother")
    try:
        assert reading.wait(HANG_S), transitions
        assert regs.cancel(record["id"], record["session"])["state"] == "running"
    finally:
        release.set()
    assert terminal.wait(HANG_S), transitions
    done = regs.get(record["id"], record["session"])
    assert done["state"] == "cancelled" and done["resources"]["outcome"] == "cancelled"
    assert "made" not in done and "outputs" not in done and "pid" not in done
    assert jobs.MEMORY.reserved_mb == 0


def test_picture_reader_spanning_rejected_publication_cannot_return_the_preview(local_job, monkeypatch):
    import json
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path

    regs, volume, _store, terminal, transitions = local_job
    renamed, captured = threading.Event(), threading.Event()
    replace, read_text, clock = Path.replace, Path.read_text, time.monotonic
    advanced = False

    def hold_done_rename(path, target):
        nonlocal advanced
        is_done = path.name == "record.json.tmp" and json.loads(path.read_bytes()).get("state") == "done"
        result = replace(path, target)
        if is_done:
            renamed.set()
            assert captured.wait(HANG_S), "public reader did not capture the provisional record"
            advanced = True  # expires the original bound before publication can be accepted
        return result

    def hold_captured_record(path, *args, **kwargs):
        data = read_text(path, *args, **kwargs)
        if (threading.current_thread().name.startswith("public-picture") and path.name == "record.json"
                and json.loads(data).get("state") == "done"):
            captured.set()
            assert terminal.wait(HANG_S), "publication did not settle before the public reader resumed"
        return data

    monkeypatch.setattr(Path, "replace", hold_done_rename)
    monkeypatch.setattr(Path, "read_text", hold_captured_record)
    monkeypatch.setattr(time, "monotonic", lambda: clock() + (jobs.PROOF_LIMIT_S + 1 if advanced else 0))
    record = regs.start_kind(volume["id"], "volume-smoother")
    sid, jid = record["session"], record["id"]
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="public-picture") as reader:
        assert renamed.wait(HANG_S), transitions
        picture = reader.submit(regs.picture, jid, sid)
        assert terminal.wait(HANG_S), transitions
        assert regs.get(jid, sid)["state"] == "failed"
        assert regs.get(jid, sid)["resources"]["outcome"] == "failed"
        assert picture.result(timeout=HANG_S) is None, "a reader crossing rollback exposed the rejected preview"
    assert captured.is_set() and advanced
    assert regs.picture(jid, sid) is None
    assert all(r["state"] != "done" for r in transitions)
    assert jobs.MEMORY.reserved_mb == 0
