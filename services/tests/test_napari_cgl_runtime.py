"""Opt-in actual upstream rendering with the existing macOS sandbox, no installs.

NAPARI_PROOF_PYTHON must name an already prepared, pinned private runtime.
Without it this test is skipped, not claimed as rendering proof. No API or production
profile is accessed. The parent bounds and reaps the complete owned process group.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from orchestrator import sandbox, owned_process


@pytest.mark.parametrize("runtime", ["NAPARI_PROOF_PYTHON", "NAPARI_REFERENCE_PYTHON"])
def test_actual_confined_renderer_and_native_control(tmp_path, monkeypatch, record_property, runtime):
    executable = os.environ.get(runtime)
    if not executable:
        pytest.skip("requires an explicitly supplied pinned napari proof runtime")
    if sys.platform != "darwin" or not sandbox.available():
        pytest.fail("requested real rendering proof requires the macOS sandbox")
    # Full-suite fixtures allow test bootstrap reads. This runtime proof explicitly
    # removes that override and grants only the interpreter, probe and backend.
    monkeypatch.delenv("MICROMIND_SANDBOX_READ", raising=False)
    executable = Path(executable).absolute()
    assert executable.is_file(), "proof interpreter does not exist; nothing will be installed"
    probe = Path(__file__).with_name("napari_cgl_runtime_probe.py")
    backend = probe.parents[1] / "env_worker/napari_cgl.py"
    work = tmp_path / "worker"
    work.mkdir()
    protected = tmp_path / "protected.txt"
    protected.write_text("synthetic canary; not user data")
    reads = [executable.parent.parent, sandbox.python_home(executable), probe, backend]
    env = sandbox.confine({"PATH": "/usr/bin:/bin", "QT_QPA_PLATFORM": "offscreen", "QT_API": "pyside6",
                           "NAPARI_CONFIG": str(work / "napari.yaml"), "NAPARI_ASYNC": "0",
                           "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "VECLIB_MAXIMUM_THREADS": "2",
                           "NUMBA_NUM_THREADS": "2", "HF_HUB_OFFLINE": "1"}, work)
    outside = subprocess.Popen(["/bin/sleep", "90"], start_new_session=True)
    proc = None
    peak = 0
    reason = None
    started = time.monotonic()
    try:
        reference = []
        if runtime == "NAPARI_REFERENCE_PYTHON":
            from env_worker.napari_cgl import REFERENCE_REVISION
            reference = [REFERENCE_REVISION]
        cmd = sandbox.wrap([str(executable), "-I", "-u", str(probe), str(backend), str(protected), str(outside.pid),
                            *reference], reads, [work])
        with (work / "stdout.log").open("wb") as stdout, (work / "stderr.log").open("wb") as stderr:
            proc = subprocess.Popen(cmd, cwd=work, env=env, stdout=stdout, stderr=stderr,
                                    close_fds=True, start_new_session=True)
            while proc.poll() is None:
                table = subprocess.run(["/bin/ps", "-axo", "pid=,ppid=,pgid=,rss="], capture_output=True,
                                       text=True, timeout=2, check=True)
                rows = [tuple(map(int, line.split())) for line in table.stdout.splitlines()]
                owned_ids = {proc.pid} | {pid for pid, _, group, _ in rows if group == proc.pid}
                while True:
                    children = {pid for pid, parent, _, _ in rows if parent in owned_ids} - owned_ids
                    if not children:
                        break
                    owned_ids.update(children)
                peak = max(peak, sum(rss * 1024 for pid, _, _, rss in rows if pid in owned_ids))
                if time.monotonic() - started > 90:
                    reason = "90 second deadline"
                elif peak > 2 * 1024**3:
                    reason = "2 GiB tree RSS allowance"
                elif stdout.tell() + stderr.tell() > 1024**2:
                    reason = "1 MiB log allowance"
                if reason:
                    break
                time.sleep(.05)
        if reason:
            pytest.fail(reason)
        output = (work / "stdout.log").read_text()
        errors = (work / "stderr.log").read_text()
        assert proc.returncode == 0, output + "\n" + errors
        result = json.loads(output.splitlines()[-1])
        assert result["passed"] and result["viewers"] == (4 if reference else 3)
        assert result["reference"] == (reference[0] if reference else None)
        if reference:
            assert result['volume_changed_components'] > 256
        assert outside.poll() is None
        record_property("peak_tree_rss_bytes", peak)
        record_property("elapsed_seconds", round(time.monotonic() - started, 2))
    finally:
        for owned in (proc, outside):
            if owned is not None:
                owned_process.terminate(owned)
        if proc is not None:
            groups = subprocess.run(["/bin/ps", "-axo", "pid=,pgid="], capture_output=True, text=True, check=True)
            assert not any(line.split()[1] == str(proc.pid) for line in groups.stdout.splitlines())
