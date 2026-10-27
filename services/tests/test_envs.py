"""Environments the app makes (orchestrator/envs.py; phase 3's E2): built in place at their permanent path, every package
by its hash, proven where they will run, ready only by their receipt; a failed or stopped one is never used and is made
again; Install and Remove from the card; the model then runs from it. A stand-in uv in a temporary catalog (it records
what it is asked and makes a minimal environment), and one real build of an empty lockfile when uv and its Python are
here."""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from gpu_worker import manifests as catalog
from orchestrator import catalog_view, envs, readiness
from tests.waiting import until

ENTRY = '''id = "{id}"
name = "Boxed"
runs = "this-mac"
version = "v1"
licence = ["edgetam"]
kind = "model"
what = "Outlines cells in a Python of its own"
runner = "gpu-worker"
needs_to_run = ["install:boxed"]
workers = ["analysis"]
host = "own-python"
documented_mb = 1
load_limit_s = 1
env = {{ python = "{python}", lock = "env.lock", imports = {imports} }}
'''


@pytest.fixture
def home(tmp_path, monkeypatch):
    root = tmp_path / "repo" / "models"
    shutil.copytree(catalog.ROOT, root)  # the reviewed catalog as it is, beside the entry each test adds
    monkeypatch.setattr(catalog, "ROOT", root)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.delenv("MICROMIND_ENVS", raising=False)
    catalog.manifests.cache_clear()
    yield tmp_path
    catalog.manifests.cache_clear()


def entry(home: Path, lock: str = "pkg==1.0 --hash=sha256:" + "a" * 64 + "\n", python: str = "3.13.5",
          imports: str = '["json"]') -> catalog.Manifest:
    folder = home / "repo" / "models" / "boxed"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "model.toml").write_text(ENTRY.format(id="boxed", python=python, imports=imports))
    (folder / "env.lock").write_text(lock)
    catalog.manifests.cache_clear()
    m = catalog.get("boxed")
    assert m is not None, catalog.problems()
    return m


def fake_uv(home: Path, monkeypatch, fail_at: str = "") -> Path:
    """A uv that writes what it was asked to uv.log and makes a minimal environment: bin/python, this Python."""
    log = home / "uv.log"
    script = home / "bin" / "uv"
    script.parent.mkdir(exist_ok=True)
    script.write_text(f'''#!/bin/bash
echo "$@ | PATH=$PATH PYTHONPATH=${{PYTHONPATH:-}}" >> {log}
if [ "$1" = "--version" ]; then echo "uv 0.0.0 (stand-in)"; exit 0; fi
if [ "$1 $2" = "{fail_at}" ]; then echo "error: the stand-in failed here" >&2; exit 2; fi
if [ "$1" = "venv" ]; then
  [ -n "$(ls -A "${{@: -1}}" 2>/dev/null)" ] && {{ echo "exists, but it's not a virtual environment" >&2; exit 2; }}
  mkdir -p "${{@: -1}}/bin" && ln -s {sys.executable} "${{@: -1}}/bin/python"
fi
exit 0
''')
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setattr(envs, "uv", lambda: str(script))
    return log


def test_it_is_built_in_place_by_its_lockfile_and_ready_only_by_its_receipt(home, monkeypatch):
    m = entry(home)
    log = fake_uv(home, monkeypatch)
    assert envs.state(m)["state"] == "absent" and envs.python(m) is None
    assert [s.kind for s in readiness.model("boxed", {}).steps] == ["install"]
    assert readiness.model("boxed", {}).steps[0].model == "boxed", "its card's Install button names it"
    said = []
    d = envs.build(m, say=said.append)
    assert d == home / "catalog" / "envs" / "boxed" / envs.generation(m), "its permanent path: never built elsewhere"
    asked = log.read_text().splitlines()
    assert asked[0].startswith("python install --quiet 3.13.5")
    assert asked[1].startswith(f"venv --quiet --python 3.13.5 {d}")
    assert asked[2].startswith(f"pip sync --quiet --require-hashes --python {d}/bin/python "), "every package by its hash"
    assert all("PYTHONPATH= " in line or line.endswith("PYTHONPATH=") for line in asked), "nothing of this shell"
    receipt = json.loads((d / envs.RECEIPT).read_text())
    assert receipt["model"] == "boxed" and receipt["imports"] == ["json"] and receipt["packages"] == 1
    assert receipt['dependencyCheck']['ok'] is True
    assert any(line.startswith('pip check --python ') for line in asked)
    assert envs.state(m)["state"] == "ready" and envs.python(m) == d / "bin" / "python"
    assert said[0] == "making its own Python 3.13.5" and said[-1] == "checking it: json"
    assert list(readiness.model("boxed", {}).steps) == []


@pytest.mark.parametrize('route', ['queue', 'install', 'build'])
@pytest.mark.parametrize('where', ['needs_to_get', 'needs_to_run'])
def test_unsupported_machine_is_refused_before_install_side_effects(home, monkeypatch, route, where):
    from dataclasses import replace

    m = replace(entry(home), **{where: ('platform:unsupported-machine',)})
    original_get = catalog.get
    monkeypatch.setattr(catalog, 'get', lambda key: m if key == m.key else original_get(key))
    calls = []
    monkeypatch.setattr(envs, 'uv', lambda: calls.append('installer'))
    queue = envs.Installs()
    attempt = {'queue': lambda: queue.start(m.key), 'install': lambda: envs.install(m),
               'build': lambda: envs.build(m)}[route]
    state = readiness.model(m.key, {})
    assert state.status == 'unavailable'
    from orchestrator import capability_shelf
    role = capability_shelf.entry_role({'id': m.key, 'name': m.name, **state.wire()})
    assert capability_shelf.plan(role) is None, 'the same refusal must offer no Turn on action'
    with pytest.raises(envs.Failed, match='cannot be prepared.*unsupported-machine'):
        attempt()
    assert calls == [] and queue.wire() == []
    assert not envs.root(m).exists(), 'refusal creates no runtime, receipt or failed staging directory'


@pytest.mark.skipif(sys.platform != 'darwin', reason='actual macOS confinement probe')
def test_direct_fiji_install_warms_its_probe_before_checking_requirements(home, monkeypatch):
    from dataclasses import replace
    from orchestrator import channels, fiji_contributions, fiji_sandbox

    m = replace(entry(home), runner='fiji', namespace='mine', needs_to_run=())
    monkeypatch.setattr(channels, 'trusted', lambda *args: True)
    called = []
    monkeypatch.setattr(fiji_contributions, 'install', lambda *args, **kwargs: called.append(m.key) or (home, []))
    fiji_sandbox.supported.cache_clear()
    try:
        assert not fiji_sandbox.available()
        assert envs.install(m) == (home, [])
        assert fiji_sandbox.available() and called == [m.key]
    finally:
        fiji_sandbox.supported.cache_clear()
        fiji_sandbox.supported()


def test_machine_refusal_preserves_installed_generation_and_retry(home, monkeypatch):
    from dataclasses import replace

    m = entry(home)
    log = fake_uv(home, monkeypatch)
    folder = envs.build(m)
    receipt = (folder / envs.RECEIPT).read_bytes()
    calls = log.read_bytes()
    incompatible = replace(m, needs_to_run=('memory:1000000000',))
    with pytest.raises(envs.Failed, match='GB of memory'):
        envs.build(incompatible)
    assert (folder / envs.RECEIPT).read_bytes() == receipt
    assert log.read_bytes() == calls
    assert envs.build(m) == folder, 'corrected requirements can reuse and recheck the preserved installation'


def test_a_new_lockfile_is_a_new_generation_beside_the_old(home, monkeypatch):
    m = entry(home)
    fake_uv(home, monkeypatch)
    first = envs.build(m)
    m2 = entry(home, lock="pkg==2.0 --hash=sha256:" + "b" * 64 + "\n")
    assert envs.python(m2) is None and envs.state(m2)["state"] == "absent", "the old one is not this lockfile's"
    second = envs.build(m2)
    assert second != first and first.exists(), "never written over"


def test_a_readiness_response_hashes_an_environment_once_then_reads_changes(home, monkeypatch):
    from contextvars import copy_context
    from dataclasses import replace

    m = replace(entry(home), runner="python-env", adapter="adapter.py:run")
    adapter = catalog.entry_folder(m) / "adapter.py"
    adapter.write_text("def run(): return 1\n")
    reads = []
    read = Path.read_bytes

    def counted(path):
        reads.append(path)
        return read(path)

    monkeypatch.setattr(Path, "read_bytes", counted)
    with readiness.batch():
        first = envs.generation(m)
        once = list(reads)
        for _ in range(3):
            with readiness.batch():
                assert envs.generation(m) == first
                assert envs.python(m) is None
                assert envs.state(m)["state"] == "absent"
        assert reads == once, "one response must not keep re-reading the same lock and adapter"
        other = {**os.environ, "MICROMIND_ENVS": str(home / "other-envs")}
        assert envs.folder(m, other).parent == home / "other-envs" / m.key
        assert envs.generation(replace(m, env={**m.env, "python": "3.13.6"})) != first
        inherited = copy_context()  # a task/thread must not keep the completed response's hashes
    adapter.write_text("def run(): return 2\n")
    assert inherited.run(envs.generation, m) != first
    with readiness.batch():
        second = envs.generation(m)
        assert second != first, "the next response hashes changed adapter bytes"
    envs.lock_file(m).write_text("different lock\n")
    assert envs.generation(m) != second, "an execution outside the response reads current bytes"
    adapter.unlink()
    assert envs.generation(m) != envs.generation(replace(m, adapter="another.py:run"))


def test_a_failed_install_is_never_used_says_why_and_is_made_again(home, monkeypatch):
    m = entry(home)
    fake_uv(home, monkeypatch, fail_at="pip sync")
    with pytest.raises(envs.Failed, match="installing its packages failed: error: the stand-in failed here"):
        envs.build(m)
    st = envs.state(m)
    assert st["state"] == "failed" and "the stand-in failed here" in st["says"] and envs.python(m) is None
    step = readiness.model("boxed", {}).steps[0]
    assert step.says == "Install Boxed again" and "the stand-in failed here" in step.how
    fake_uv(home, monkeypatch)
    assert envs.build(m).exists() and envs.state(m)["state"] == "ready", "the retry makes it from the start"


def test_a_proof_that_fails_leaves_it_unused(home, monkeypatch):
    m = entry(home, imports='["no_such_module_anywhere"]')
    fake_uv(home, monkeypatch)
    with pytest.raises(envs.Failed, match="ModuleNotFoundError"):
        envs.build(m)
    assert envs.python(m) is None and not (envs.folder(m) / envs.RECEIPT).exists()


def test_incompatible_dependencies_never_publish_install_receipt_and_retry_preserves_other_generation(home, monkeypatch):
    first = entry(home)
    fake_uv(home, monkeypatch)
    good = envs.build(first)
    receipt = (good / envs.RECEIPT).read_bytes()
    next_version = entry(home, lock='pkg==2 --hash=sha256:' + 'b' * 64 + '\n')
    fake_uv(home, monkeypatch, fail_at='pip check')
    with pytest.raises(envs.Failed, match='Correct the pinned lock and retry'):
        envs.build(next_version)
    assert envs.python(next_version) is None
    assert (good / envs.RECEIPT).read_bytes() == receipt
    fake_uv(home, monkeypatch)
    repaired = envs.build(next_version)
    assert repaired != good and envs.python(next_version) == repaired / 'bin/python'
    assert (good / envs.RECEIPT).read_bytes() == receipt


def test_an_install_that_stopped_halfway_is_said_and_never_used(home, monkeypatch):
    m = entry(home)
    d = envs.folder(m)
    d.mkdir(parents=True)
    envs._marker(d, envs.BUILDING).write_text(json.dumps({"pid": 999_999_999}))  # a process that is gone
    st = envs.state(m)
    assert st["state"] == "failed" and st["says"].startswith("An install stopped before it finished")
    assert envs.python(m) is None


def test_its_card_says_which_python_runs_it(home, monkeypatch):
    m = entry(home)
    from orchestrator import operations

    monkeypatch.setitem(operations.INSTALLED, "boxed", lambda: "not installed here (boxed: its Install button)")
    fact = next(f for f in catalog_view.facts(m, {}, lambda k: False, []) if f["kind"] == "code")
    assert fact["ok"] is False and fact["says"] == "Not installed yet"
    monkeypatch.setitem(operations.INSTALLED, "boxed", lambda: "")  # one made by hand runs it
    fact = next(f for f in catalog_view.facts(m, {}, lambda k: False, []) if f["kind"] == "code")
    assert fact["ok"] is True and fact["install"] == "boxed" and "made by hand" in fact["says"]
    fake_uv(home, monkeypatch)
    envs.build(m)
    fact = next(f for f in catalog_view.facts(m, {}, lambda k: False, []) if f["kind"] == "code")
    assert fact["ok"] is True and fact["says"].startswith("Installed: its own Python") and "install" not in fact


def test_the_queue_installs_says_it_in_activity_and_remove_takes_it_away(home, monkeypatch):
    m = entry(home)
    fake_uv(home, monkeypatch)
    lines, changed = [], []

    from orchestrator import activity

    monkeypatch.setattr(activity, "activity", lambda hub, kind, text, **kw: lines.append((kw.get("state"), text)))
    queue = envs.Installs(hub=object(), changed=lambda: changed.append(1))
    queue.start("boxed")
    until(lambda: queue.wire()[0]["state"] in ("done", "failed"))
    assert queue.wire()[0]["state"] == "done" and changed
    assert lines[0] == ("running", "Installing Boxed (1 packages, its own Python)") and lines[-1] == ("done", "Installed Boxed")
    assert envs.remove(m) and envs.state(m)["state"] == "absent"



def test_an_install_that_raises_anything_fails_said_and_the_queue_goes_on(home, monkeypatch):
    """Not only Failed: any error (a folder gone, a disk error) ends that install as failed, said, and the next queued one
    still runs. It once ended the queue's thread with the row "installing" forever (the gate of 2026-10-14)."""
    entry(home)
    fake_uv(home, monkeypatch)
    calls = []
    real = envs.install

    def install(m, environ=None, **kw):
        calls.append(m.key)
        if len(calls) == 1:
            raise FileNotFoundError(2, "No such file or directory", "proof.tmp")
        return real(m, environ, **kw)

    monkeypatch.setattr(envs, "install", install)
    queue = envs.Installs()
    queue.start("boxed")
    until(lambda: queue.wire()[0]["state"] in ("done", "failed"))
    row = queue.wire()[0]
    assert row["state"] == "failed" and "FileNotFoundError" in row["says"], row
    queue.start("boxed")  # tried again: the queue is still alive and installs it
    until(lambda: queue.wire()[0]["state"] in ("done", "failed"))
    assert queue.wire()[0]["state"] == "done", queue.wire()
    assert queue.stop()

def test_the_environments_can_live_apart_from_a_scratch_catalog(home, monkeypatch):
    """make catalog-proof: a scratch catalog with the real environments (they are built in place and can't be copied)."""
    m = entry(home)
    monkeypatch.setenv("MICROMIND_ENVS", str(home / "real-envs"))
    assert envs.folder(m).is_relative_to(home / "real-envs")


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv isn't installed here")
def test_a_real_environment_from_an_empty_lockfile(home):
    """The real uv, its own Python (already on this Mac, or the test is skipped: it never downloads one), an empty
    lockfile: built in place, proven, ready by its receipt."""
    managed = sorted((Path.home() / ".local" / "share" / "uv" / "python").glob("cpython-3.13.*-macos-*"))
    if not managed:
        pytest.skip("no Python 3.13 of uv's own here (the test never downloads one)")
    version = managed[-1].name.split("-")[1]
    m = entry(home, lock="", python=version)
    d = envs.build(m)
    receipt = json.loads((d / envs.RECEIPT).read_text())
    assert receipt["python"] == version and receipt["packages"] == 0 and receipt["uv"].startswith("uv ")
    assert subprocess.run([str(envs.python(m)), "-c", "import json"], env=envs.clean_env(m, d)).returncode == 0


def test_trackastra_runs_in_its_own_python_from_the_catalogs_revision(tmp_path, monkeypatch):
    """noncommercial/trackastra_server.py, started by trackastra_link in the environment the app made: the app's own
    Python never imports Trackastra. A stand-in environment: a Python that finds a position-based stand-in Trackastra."""
    import numpy as np

    from noncommercial import trackastra_link

    standin = tmp_path / "site" / "trackastra"
    standin.mkdir(parents=True)
    (standin / "__init__.py").write_text("")
    (standin / "model.py").write_text('''
import networkx as nx, numpy as np
class Trackastra:
    @classmethod
    def from_folder(cls, folder, device="cpu"):
        assert str(folder).endswith("files-3b6f6a3599074e9c"), folder  # the catalog's pinned revision
        return cls()
    def track(self, views, labels, mode="greedy"):
        g = nx.DiGraph()
        for t in (0, 1):
            for n in np.unique(labels[t]):
                if n:
                    g.add_node((t, int(n)), time=t, label=int(n))
        for n in np.unique(labels[0]):
            if n and (1, int(n)) in g:
                g.add_edge((0, int(n)), (1, int(n)), weight=0.9)
        return g, labels
''')
    env = tmp_path / "env"
    (env / "bin").mkdir(parents=True)
    exe = env / "bin" / "python"
    site = os.pathsep.join([str(tmp_path / "site"), *[p for p in sys.path if p.endswith("site-packages")]])
    exe.write_text(f"#!/bin/bash\nPYTHONPATH={site} exec {sys.executable} \"$@\"\n")
    exe.chmod(0o755)
    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")
    monkeypatch.setattr(trackastra_link, "catalog_env", lambda: exe)
    monkeypatch.setattr(trackastra_link, "_own", trackastra_link._OwnPython())
    labels = np.zeros((2, 32, 32), np.int32)
    labels[0, 2:8, 2:8] = labels[1, 3:9, 3:9] = 1
    labels[0, 20:26, 20:26] = labels[1, 21:27, 21:27] = 2
    edges = trackastra_link.link(np.zeros((2, 32, 32), np.float32), labels)
    assert sorted(edges) == [(0, 1, 1, 1, 0.9), (0, 2, 1, 2, 0.9)]
    assert "trackastra" not in sys.modules, "never imported into the app's own Python"
    trackastra_link._own._process.kill()


def test_package_hash_refusal_keeps_the_reason_and_expected_and_received_hashes(home, monkeypatch):
    m = entry(home)
    fake_uv(home, monkeypatch, fail_at="pip sync")
    script = home / "bin" / "uv"
    text = script.read_text().replace("error: the stand-in failed here", "error: Failed to install pkg==1.0\nCaused by: Hash mismatch\nExpected: sha256:aaaaaaaa\nComputed: sha256:bbbbbbbb")
    script.write_text(text)
    with pytest.raises(envs.Failed, match="Hash mismatch"):
        envs.build(m)
    state = envs.state(m)
    assert state["state"] == "failed"
    assert "Failed to install pkg==1.0" in state["says"]
    assert "Expected: sha256:aaaaaaaa" in state["says"]
    assert "Computed: sha256:bbbbbbbb" in state["says"]
    assert envs.python(m) is None


def test_code_nobody_reviewed_installs_ready_built_packages_only(home, monkeypatch):
    """A package built from source runs its build script while it installs, outside the sandbox and with the network:
    for code nobody reviewed uv is told never to build (C7); the app's reviewed catalog installs as before."""
    from orchestrator import sandbox

    m = entry(home)
    log = fake_uv(home, monkeypatch)
    monkeypatch.setattr(sandbox, "required", lambda entry: False)
    envs.build(m)
    assert "--no-build" not in next(line for line in log.read_text().splitlines() if line.startswith("pip sync"))
    envs.remove(m)
    log.unlink()
    monkeypatch.setattr(sandbox, "required", lambda entry: True)
    monkeypatch.setattr(sandbox, "available", lambda: True)
    monkeypatch.setattr(sandbox, "wrap", lambda cmd, read, write, **kwargs: cmd)  # the import check's own boundary: test_sandbox
    envs.build(m)
    sync = next(line for line in log.read_text().splitlines() if line.startswith("pip sync"))
    assert sync.startswith("pip sync --quiet --require-hashes --no-build "), sync


BACKEND = """from pathlib import Path

Path({marker!r}).write_text("its build script ran")  # what a hostile build hook does first


def get_requires_for_build_wheel(config_settings=None):
    return []


def build_wheel(wheel_directory, config_settings=None, metadata_directory=None):
    raise RuntimeError("never builds")
"""


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv isn't installed here")
def test_a_build_hook_never_runs_for_code_nobody_reviewed(home, monkeypatch):
    """A real uv and a package published only as source, whose build script writes a marker: it runs for the app's
    reviewed catalog (the baseline: the hook is real), and for code nobody reviewed uv refuses before running it."""
    import tarfile

    from orchestrator import sandbox

    managed = sorted((Path.home() / ".local" / "share" / "uv" / "python").glob("cpython-3.13.*-macos-*"))
    if not managed:
        pytest.skip("no Python 3.13 of uv's own here (the test never downloads one)")
    version = managed[-1].name.split("-")[1]

    def package(marker: Path) -> str:
        src = home / f"src-{marker.name}" / "hooked-0.1"
        src.mkdir(parents=True)
        (src / "pyproject.toml").write_text('[build-system]\nrequires = []\nbuild-backend = "backend"\n'
                                            'backend-path = ["."]\n\n[project]\nname = "hooked"\nversion = "0.1"\n')
        (src / "backend.py").write_text(BACKEND.format(marker=str(marker)))
        sdist = src.parent / "hooked-0.1.tar.gz"
        with tarfile.open(sdist, "w:gz") as t:
            t.add(src, arcname="hooked-0.1")
        digest = __import__("hashlib").sha256(sdist.read_bytes()).hexdigest()
        return f"hooked @ {sdist.as_uri()} --hash=sha256:{digest}\n"

    reviewed_marker = home / "reviewed-marker"
    m = entry(home, lock=package(reviewed_marker), python=version)
    monkeypatch.setattr(sandbox, "required", lambda entry: False)
    with pytest.raises(envs.Failed):
        envs.build(m)
    assert reviewed_marker.exists(), "the baseline: building it runs its hook"

    marker = home / "unreviewed-marker"
    m = entry(home, lock=package(marker), python=version)
    monkeypatch.setattr(sandbox, "required", lambda entry: True)
    with pytest.raises(envs.Failed, match="ready-built packages"):
        envs.build(m)
    assert not marker.exists(), "its build script never ran"


def test_a_python_error_is_said_by_its_last_line():
    """Acceptance 2026-10-13: a failed check showed the card eight lines of traceback frames; Python's reason is its
    last line. Other tools' last lines (uv explaining a hash mismatch) still keep their context."""
    import sys

    program = "def f():\n    raise PermissionError(1, 'Operation not permitted')\nf()"
    with pytest.raises(envs.Failed) as caught:
        envs._run([sys.executable, "-c", program], {}, lambda: False, "its proof")
    assert str(caught.value) == "its proof failed: PermissionError: [Errno 1] Operation not permitted"


def test_a_library_macos_wont_load_is_said_by_package_and_reason(tmp_path):
    """Python's words name the file's whole path three times before macOS's reason (a record cut them off: C4's builder,
    2026-10-14). Said plainly: the package and its version (from the record its wheel installed, inside the environment
    only), the file, and each reason once; a library it needs that isn't there is the reason."""
    import sys

    site = tmp_path / "env" / "lib" / "python3.13" / "site-packages"
    so = site / "scipy" / "_lib" / "_ccallback_c.cpython-313-darwin.so"
    (site / "scipy-1.15.3.dist-info").mkdir(parents=True)
    (site / "scipy-1.15.3.dist-info" / "RECORD").write_text("scipy/_lib/_ccallback_c.cpython-313-darwin.so,sha256=x,1\n")
    why = "section '__DATA/__thread_bss' has a zero-fill section type, but offset field is not zero"
    text = (f"ImportError: dlopen({so}, 0x0002): tried: '{so}' ({why}), '/System/Volumes/Preboot/Cryptexes/OS{so}' "
            f"(no such file), '{so}' ({why})")
    assert envs.native_words(text, tmp_path / "env") == (
        f"ImportError: macOS couldn't load scipy 1.15.3's compiled part _ccallback_c.cpython-313-darwin.so: {why}")
    assert envs.native_words(text) == f"ImportError: macOS couldn't load scipy's compiled part {so.name}: {why}"
    missing = (f"ImportError: dlopen({so}, 0x0002): Library not loaded: @rpath/libgfortran.5.dylib\n  Referenced from: "
               f"<UUID> {so}\n  Reason: tried: '{site}/scipy/.dylibs/libgfortran.5.dylib' (no such file), "
               "'/usr/lib/libgfortran.5.dylib' (no such file, not in dyld cache)")
    assert envs.native_words(missing).endswith("it needs @rpath/libgfortran.5.dylib, which isn't there")
    assert envs.native_words("ValueError: nothing native") == "ValueError: nothing native"
    with pytest.raises(envs.Failed) as caught:  # an install's proof says it the same way
        envs._run([sys.executable, "-c", f"raise ImportError({text[len('ImportError: '):]!r})"], {}, lambda: False,
                  "its proof")
    assert str(caught.value) == f"its proof failed: ImportError: macOS couldn't load scipy's compiled part {so.name}: {why}"


def test_removal_cannot_race_python_preparation_after_queue_admission(home, monkeypatch):
    """An interleaving below the queue: Remove must not succeed then be undone by the installer."""
    m = entry(home)
    fake_uv(home, monkeypatch)
    original_run = envs._run
    removals = []
    sibling = envs.root(m).parent / 'unrelated' / 'accepted'
    sibling.mkdir(parents=True)
    kept = sibling / 'receipt.json'
    kept.write_bytes(b'unchanged')

    def during_preparation(cmd, env, stop, what, cwd=None):
        if what == 'getting its Python':
            try:
                removals.append(envs.remove(m))
            except envs.Failed as error:
                removals.append(str(error))
        return original_run(cmd, env, stop, what, cwd=cwd)

    monkeypatch.setattr(envs, '_run', during_preparation)
    prepared = envs.build(m)
    assert len(removals) == 1 and isinstance(removals[0], str), 'Remove succeeded during preparation; Install resurrected the removed runtime'
    assert 'running work' in removals[0]
    assert (prepared / envs.RECEIPT).is_file()
    assert kept.read_bytes() == b'unchanged'
    assert envs.remove(m), 'completed preparation must release its ownership'
    assert not envs.root(m).exists() and kept.read_bytes() == b'unchanged'
    assert envs.build(m) == prepared, 'explicit reinstall still works after removal'


@pytest.mark.parametrize('proof_ok', [True, False])
def test_install_owns_runtime_until_operation_proof_finishes(home, monkeypatch, proof_ok):
    from dataclasses import replace
    from orchestrator import env_runner

    m = replace(entry(home), runner='python-env')
    prepared = envs.folder(m)
    prepared.mkdir(parents=True)
    (prepared / 'prepared').write_bytes(b'kept')
    monkeypatch.setattr(envs, 'build', lambda *args, **kwargs: prepared)
    monkeypatch.setattr(env_runner, 'preserve_active', lambda *args: None)
    attempts = []

    def prove(*args, **kwargs):
        try:
            attempts.append(envs.remove(m))
        except envs.Failed as error:
            attempts.append(str(error))
        return {'ok': proof_ok, 'says': 'Synthetic operation proof', 'why': 'synthetic proof refusal'}

    monkeypatch.setattr(env_runner, 'prove', prove)
    if proof_ok:
        envs.install(m)
    else:
        with pytest.raises(envs.Failed, match='synthetic proof refusal'):
            envs.install(m)
    assert len(attempts) == 1 and isinstance(attempts[0], str), 'Remove succeeded while the operation proof was running'
    assert (prepared / 'prepared').read_bytes() == b'kept'
    assert envs.remove(m)


def test_failed_preparation_releases_removal_and_retry(home, monkeypatch):
    m = entry(home)
    fake_uv(home, monkeypatch, fail_at='python install')
    with pytest.raises(envs.Failed, match='stand-in failed'):
        envs.build(m)
    assert envs.remove(m)
    fake_uv(home, monkeypatch)
    assert (envs.build(m) / envs.RECEIPT).is_file()


def test_remove_preserves_original_proof_inputs_choices_and_unmanaged_files(home, monkeypatch):
    m = entry(home)
    fake_uv(home, monkeypatch)
    prepared = envs.build(m)
    originals = {
        prepared.parent / (prepared.name + '.capture.proof-input.npy'): b'original scientific pixels',
        prepared.parent / 'chosen.json': b'{"generation":"kept-choice"}',
        prepared.parent / 'notes' / 'work.txt': b'private work',
    }
    for path, data in originals.items():
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(data)
    assert envs.remove(m)
    assert not prepared.exists()
    assert {path: path.read_bytes() for path in originals} == originals
    assert not envs.remove(m), 'no runtime copies remain; preserved files are not software'
    assert envs.build(m) == prepared
    assert {path: path.read_bytes() for path in originals} == originals


def test_remove_rejects_a_symlinked_model_root(home):
    m = entry(home)
    external = home / 'scientific-work'
    external.mkdir()
    original = external / 'original.npy'
    original.write_bytes(b'keep')
    envs.root(m).parent.mkdir(parents=True)
    envs.root(m).symlink_to(external, target_is_directory=True)
    with pytest.raises(envs.Failed, match='symbolic link'):
        envs.remove(m)
    assert original.read_bytes() == b'keep'


def test_partial_uninstall_withdraws_before_cleanup_and_public_command_reports_recovery(home, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import Mock
    from orchestrator import capability_space, commands, runtime_cleanup
    from orchestrator.brain.registry import ToolFailed
    m = entry(home)
    fake_uv(home, monkeypatch)
    prepared = envs.build(m)
    original = prepared.parent / 'retained.proof-input.npy'
    original.write_bytes(b'original')
    app = SimpleNamespace(hub=SimpleNamespace(resources=None), installs=SimpleNamespace(wire=lambda: []),
                          catalog_changed=Mock())
    def partial(folder):
        assert runtime_cleanup.pending(folder)
        assert not prepared.exists(), 'withdraw before deleting any executable files'
        (folder / envs.RECEIPT).unlink()
        raise PermissionError('controlled interrupted deletion')
    with monkeypatch.context() as patch:
        patch.setattr(runtime_cleanup, 'discard', partial)
        with pytest.raises(ToolFailed) as failure:
            asyncio.run(commands.model_uninstall(app, None, {'model': m.key}))
        assert 'finish the pending removal' in failure.value.next
    app.catalog_changed.assert_called_once()
    assert envs.python(m) is None
    pending = [r for r in capability_space.inventory()['items'] if r.get('pendingRemoval')]
    assert len(pending) == 1 and pending[0]['models'] == [m.key]
    assert envs.remove(m)
    assert original.read_bytes() == b'original'
    assert not [r for r in capability_space.inventory()['items'] if r.get('pendingRemoval')]
    assert envs.build(m) == prepared


def test_withdrawn_and_symlinked_copies_never_appear_as_installed_generations(home, monkeypatch):
    from orchestrator import runtime_cleanup
    m = entry(home)
    fake_uv(home, monkeypatch)
    prepared = envs.build(m)
    [withdrawn] = runtime_cleanup.withdraw([prepared])
    assert (withdrawn / envs.RECEIPT).is_file(), 'interrupted removal may keep a complete old receipt'
    assert envs.generations(m) == []
    prepared.symlink_to(withdrawn, target_is_directory=True)
    assert envs.generations(m) == []
    assert envs.python(m) is None
    assert envs.state(m)['state'] == 'failed'


def test_removal_says_the_space_it_freed_and_leaves_preserved_files_out_of_it(home, monkeypatch):
    """L7d: the card's Remove can say what it freed; proof inputs beside the generation are neither removed nor counted."""
    m = entry(home)
    fake_uv(home, monkeypatch)
    prepared = envs.build(m)
    expected = sum(p.stat().st_size for p in prepared.rglob('*') if p.is_file() and not p.is_symlink()
                   and p.stat().st_nlink == 1)
    kept = prepared.parent / (prepared.name + '.capture.proof-input.npy')
    kept.write_bytes(b'original scientific pixels')
    done = envs.removal(m)
    assert done['removed'] and done['filesBytes'] == expected and expected > 0
    assert done['freeBefore'] > 0 and done['freeAfter'] > 0  # measured on the disk around the removal
    assert kept.read_bytes() == b'original scientific pixels'
    assert envs.removal(m) == {'removed': False, 'filesBytes': 0, 'freeBefore': 0, 'freeAfter': 0}
