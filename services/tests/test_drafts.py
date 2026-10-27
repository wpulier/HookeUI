"""The builder's workbench (orchestrator/drafts.py; C4): a draft's adapter and requirements written and checked, its
weights pinned by their hash, its requirements locked as wheels only, and each try run inside macOS's sandbox on the
user's picture, its result or its error said back; only a draft that ran becomes one of your models. The builder is
admitted only on the studio's own press. uv's steps are stood in (the environment wraps the app's own Python)."""
import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import drafts, readiness, sandbox
from orchestrator.research.policy import ResearchRefused, admit
from tests.test_env_runner import ADAPTER

macos = pytest.mark.skipif(sys.platform != "darwin" or not sandbox.available(), reason="macOS's sandbox only")


@pytest.fixture
def bench(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    catalog.manifests.cache_clear()

    def run(cmd, what, env=None):  # uv, stood in: compile writes a lock, venv makes a Python, sync installs nothing
        if cmd[1:3] == ["pip", "compile"]:
            assert "--only-binary" in cmd and ":all:" in cmd, "wheels only: no package's build script runs"
            Path(cmd[cmd.index("-o") + 1]).write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
        elif cmd[1] == "venv":
            exe = Path(cmd[-1]) / "bin" / "python"
            exe.parent.mkdir(parents=True)
            exe.write_text(f'#!/bin/bash\nexec {sys.executable} "$@"\n')
            exe.chmod(0o755)
        elif cmd[1:3] == ["pip", "sync"]:
            assert "--require-hashes" in cmd and "--only-binary" in cmd
        return ""

    monkeypatch.setattr(drafts, "_run", run)
    yield tmp_path
    catalog.manifests.cache_clear()


def test_a_draft_is_checked_before_it_is_kept(bench):
    did = drafts.create("https://github.com/maya/diatom-counter")
    with pytest.raises(drafts.DraftError, match="defines segment"):
        drafts.write(did, "print('hello')", "numpy==2.0.0", {})
    with pytest.raises(drafts.DraftError, match="no requirement"):
        drafts.write(did, ADAPTER, "git+https://github.com/x/y.git", {})
    with pytest.raises(drafts.DraftError, match="write the adapter first"):
        drafts.attempt(did)
    assert drafts.write(did, ADAPTER, "numpy==2.0.0\n# a comment\n", {"name": "Diatom counter"})["requirements"] == ["numpy==2.0.0"]


@macos
def test_each_try_runs_inside_the_sandbox_and_only_a_draft_that_ran_is_used(bench, monkeypatch):
    did = drafts.create("https://github.com/maya/diatom-counter")
    drafts.write(did, "def segment(picture, params):\n    raise ValueError('its weights are missing')\n", "numpy==2.0.0",
                 {"name": "Diatom counter", "what": "Outlines every diatom", "licence": "MIT", "tier": "open"})
    failed = drafts.attempt(did)
    assert not failed["ok"] and "its weights are missing" in failed["error"], "the error, for the next try"
    with pytest.raises(drafts.DraftError, match="hasn't run"):
        drafts.promote(did)
    wrapped = []
    real = sandbox.wrap
    monkeypatch.setattr(sandbox, "wrap", lambda cmd, read, write, **kwargs: wrapped.append(cmd) or real(cmd, read, write, **kwargs))
    drafts.write(did, ADAPTER, "numpy==2.0.0", {"name": "Diatom counter", "what": "Outlines every diatom", "licence": "MIT",
                                                 "tier": "open", "tags": ["diatoms"]})
    ran = drafts.attempt(did)
    assert ran["ok"] and ran["count"] >= 1 and wrapped, ran
    import base64
    import hashlib
    import json
    import zlib
    import numpy as np

    kept = json.loads((drafts.folder(did) / 'result.json').read_text())
    labels = kept['labels']
    native = np.frombuffer(zlib.decompress(base64.b64decode(labels['zlibBase64'])), dtype=labels['dtype'])
    assert native.size == labels['shape'][0] * labels['shape'][1]
    assert hashlib.sha256(native.astype(np.int32).tobytes()).hexdigest() == kept['sha256']
    used = drafts.promote(did, reviewed=drafts.review(did)["digest"])
    m = catalog.mine()[used["model"]]
    assert m.runner == "python-env" and "drafted by AI" in m.component["what"] and m.tags == ("diatoms",)
    assert sandbox.required(m), "nobody reviewed it: it runs sandboxed as one of yours too"
    assert [s.kind for s in readiness.model(m.key, {}).steps] == ["install"]



@macos
def test_a_try_whose_adapter_cant_import_says_why_for_the_next_try(bench):
    """C4's builder, 2026-10-14: a first try ended "didn't start (closed)" (its worker ended before its hello, its words
    discarded), so the builder spent a paid round guessing. Its reason now comes back; a compiled library macOS refused is
    said by package and reason in the first few hundred characters, where the records cut it."""
    did = drafts.create("https://github.com/maya/diatom-counter")
    fields = {"name": "Diatom counter", "what": "Outlines every diatom", "licence": "MIT", "tier": "open"}
    so = "/lib/python3.13/site-packages/scipy/sparse/linalg/_propack/_spropack.cpython-313-darwin.so"
    refused = (f"dlopen({drafts.folder(did)}/env{so}, 0x0002): tried: '{drafts.folder(did)}/env{so}' (section "
               f"'__DATA/__thread_bss' has a zero-fill section type, but offset field is not zero), "
               f"'/System/Volumes/Preboot/Cryptexes/OS{drafts.folder(did)}/env{so}' (no such file)")
    drafts.write(did, f"raise ImportError({refused!r})\n" + ADAPTER, "numpy==2.0.0", fields)
    failed = drafts.attempt(did)
    assert not failed["ok"] and "(closed)" not in failed["error"], failed
    assert failed["error"][:300].endswith(
        "stopped before it was ready: ImportError: macOS couldn't load scipy's compiled part _spropack.cpython-313-darwin.so: "
        "section '__DATA/__thread_bss' has a zero-fill section type, but offset field is not zero"), failed
    drafts.write(did, "import not_a_package_here\n" + ADAPTER, "numpy==2.0.0", fields)
    failed = drafts.attempt(did)
    assert failed["error"].endswith("ModuleNotFoundError: No module named 'not_a_package_here'"), failed

def test_a_pinned_file_is_hashed_from_a_public_https_address(bench):
    did = drafts.create("https://github.com/maya/diatom-counter")

    class Stream:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def iter_bytes(self, n):
            yield b"weights" * 10

    http = SimpleNamespace(stream=lambda *a, **k: Stream())
    pinned = drafts.pin(did, "https://example.org/w/model.pt?download=1", http=http)
    assert pinned["path"] == "model.pt" and pinned["bytes"] == 70 and len(pinned["sha256"]) == 64
    with pytest.raises(drafts.DraftError, match="public https"):
        drafts.pin(did, "file:///Users/me/secret")


def test_the_builder_starts_only_on_the_studios_own_press():
    assert admit("start", effort="build", deliverable="adapter", confirmed=True).effort == "build"
    for kw in ({"effort": "build", "deliverable": "adapter"}, {"effort": "quick", "deliverable": "adapter", "confirmed": True},
               {"effort": "build", "deliverable": "research", "confirmed": True}):
        with pytest.raises(ResearchRefused):
            admit("start", **kw)


def test_the_manager_answers_only_a_running_builder_with_a_draft(bench):
    from orchestrator.research.manager import ResearchManager

    did = drafts.create("https://github.com/maya/diatom-counter")
    manager = SimpleNamespace(drafts={"r1": did}, _changed=lambda s: None)
    session = SimpleNamespace(rid="r1", deliverable="adapter", status="running", step="")
    op = ResearchManager._draft_op
    written = asyncio.run(op(manager, session, {"op": "draft_write", "adapter": ADAPTER, "requirements": "numpy==2.0.0",
                                                "fields": {"name": "x"}}))
    assert written["ok"]
    other = SimpleNamespace(rid="r2", deliverable="research", status="running", step="")
    assert "drafts no adapter" in asyncio.run(op(manager, other, {"op": "draft_try"}))["error"]
    # Through the manager's own request gate, as the research worker sends it (acceptance 2026-10-14: the gate let only
    # snapshot, run and save_findings through, so every real builder run was told "unknown request draft_write").
    gated = SimpleNamespace(drafts={"r1": did}, _changed=lambda s: None, sessions={"r1": session},
                            _draft_op=lambda s, m: ResearchManager._draft_op(gated, s, m))
    via_gate = asyncio.run(ResearchManager._request(gated, {"op": "draft_write", "rid": "r1", "adapter": ADAPTER,
                                                            "requirements": "numpy==2.0.0", "fields": {"name": "x"}}))
    assert via_gate.get("ok"), via_gate


# --- owned work, and what was tried is what is used (Codex's C4 cross-check of 613e738d) ----------------------------
@pytest.fixture
def owned(tmp_path, monkeypatch):
    """A scratch catalog with uv stood in by a program that only blocks (the real _run, its real child process)."""
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    catalog.manifests.cache_clear()
    pidfile = tmp_path / "uv.pid"
    blocked = tmp_path / "uv"
    blocked.write_text(f"#!/bin/sh\necho $$ > {pidfile}\nexec sleep 60\n")
    blocked.chmod(0o755)
    monkeypatch.setattr(drafts, "_uv", lambda: str(blocked))
    yield SimpleNamespace(root=tmp_path, pidfile=pidfile)
    catalog.manifests.cache_clear()


def _alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _later(seconds: float, event) -> None:
    import threading

    threading.Timer(seconds, event.set).start()


@macos
def test_a_try_blocked_in_uv_ends_on_stop_and_its_child_is_reaped(owned):
    import threading
    import time

    did = drafts.create("https://github.com/maya/diatom-counter")
    drafts.write(did, ADAPTER, "numpy==2.0.0", {"name": "Diatom counter"})
    stop = threading.Event()
    _later(0.5, stop)
    t0 = time.monotonic()
    out = drafts.attempt(did, stop=stop.is_set)
    assert time.monotonic() - t0 < 5, "Stop ended it, not uv's own time"
    assert not out["ok"] and "stopped" in out["error"] and "cancelled" in out["error"], out
    assert not _alive(int(owned.pidfile.read_text())), "its process group was reaped"
    state = drafts._state(did)
    assert state["state"] == "drafting" and "stopped" in state["tries"][-1]["error"]


@macos
def test_the_builders_budget_covers_the_whole_try_not_each_step(owned):
    import time

    did = drafts.create("https://github.com/maya/diatom-counter")
    drafts.write(did, ADAPTER, "numpy==2.0.0", {"name": "Diatom counter"})
    state = drafts._state(did)
    state["createdAt"] = time.time() - drafts.BUDGET_S + 0.5  # its budget nearly spent by earlier steps
    drafts._save_state(did, state, __import__("os").environ)
    t0 = time.monotonic()
    out = drafts.attempt(did)
    assert time.monotonic() - t0 < 5 and not out["ok"] and "timed out" in out["error"], out
    assert not _alive(int(owned.pidfile.read_text()))


def test_a_download_blocked_on_the_network_ends_on_stop_and_keeps_nothing(owned):
    import threading
    import time

    did = drafts.create("https://github.com/maya/diatom-counter")
    closed = threading.Event()

    class Blocked:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def close(self):
            closed.set()

        def iter_bytes(self, n):
            yield b"w" * 1024
            closed.wait(30)  # a read blocked on the network: only closing the connection ends it
            raise OSError("the connection was closed")

    stop = threading.Event()
    _later(0.5, stop)
    t0 = time.monotonic()
    with pytest.raises(drafts.DraftError, match="stopped"):
        drafts.pin(did, "https://example.org/w/model.pt", http=SimpleNamespace(stream=lambda *a, **k: Blocked()),
                   stop=stop.is_set)
    assert time.monotonic() - t0 < 5 and closed.is_set()
    assert list((owned.root / "catalog" / "drafts" / did / "files").iterdir()) == [], "nothing of it is kept"
    assert drafts._state(did)["files"] == []


@macos
def test_an_edit_after_a_try_that_ran_is_not_what_is_used(bench):
    did = drafts.create("https://github.com/maya/diatom-counter")
    fields = {"name": "Diatom counter", "what": "Outlines every diatom", "licence": "MIT", "tier": "open"}
    drafts.write(did, ADAPTER, "numpy==2.0.0", fields)
    assert drafts.attempt(did)["ok"]
    changed = ADAPTER + "\n# changed after it ran\n"
    drafts.write(did, changed, "numpy==2.0.0", fields)
    with pytest.raises(drafts.DraftError, match="changed since it last ran"):
        drafts.promote(did)
    assert drafts.attempt(did)["ok"]
    used = drafts.promote(did, reviewed=drafts.review(did)["digest"])
    m = catalog.mine()[used["model"]]
    assert (Path(m.folder) / "adapter.py").read_text() == changed, "what is used is exactly what ran last"


@macos
def test_a_draft_changed_while_its_try_ran_is_not_a_success(bench, monkeypatch):
    from orchestrator import env_runner

    did = drafts.create("https://github.com/maya/diatom-counter")
    drafts.write(did, ADAPTER, "numpy==2.0.0", {"name": "Diatom counter"})
    real = env_runner.Client.run

    def edited_meanwhile(self, picture, params=None, output="", *, admit=None):
        out = real(self, picture, params, output, admit=admit)
        drafts.write(did, ADAPTER + "\n# edited during the try\n", "numpy==2.0.0", {"name": "Diatom counter"})
        return out

    monkeypatch.setattr(env_runner.Client, "run", edited_meanwhile)
    out = drafts.attempt(did)
    assert not out["ok"] and "changed while it was being tried" in out["error"]
    with pytest.raises(drafts.DraftError):
        drafts.promote(did)


def test_a_draft_is_named_after_its_repository_never_the_commit_or_a_file(bench):
    """Acceptance 2026-10-13: a draft of a repository at a commit was named "f0a392a0…", one of a raw file "threshold
    methods.py"."""
    commit = "f0a392a0" + "1" * 32
    did = drafts.create(f"https://github.com/haesleinhuepf/napari-segment-blobs-and-things-with-membranes/tree/{commit}")
    assert did == "napari-segment-blobs-and-things-with-membranes"[:40].strip("-")
    row = next(r for r in drafts.wire() if r["id"] == did)
    assert row["name"] == "napari-segment-blobs-and-things-with-membranes"
    raw = drafts.create(f"https://raw.githubusercontent.com/maya/diatom-tools/{commit}/threshold_methods.py")
    assert raw == "diatom-tools" and next(r for r in drafts.wire() if r["id"] == raw)["name"] == "diatom-tools"
    drafts.write(raw, ADAPTER, "numpy==2.0.0", {})  # the builder gave it no name of its own
    state = drafts._state(raw)
    assert drafts._manifest(raw, drafts.folder(raw), state).name == "diatom-tools", "its entry is named so too"
