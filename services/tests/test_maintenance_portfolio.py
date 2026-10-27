"""Many entries under maintenance at once (ticket 59's MT4): twelve entries of one channel, each update with its own
outcome, run one after another as the Maintainer runs them. One look at the channel serves them all; one entry's failure
never stops another's; a second pass makes no new decisions; an entry with pinned weight files (the second integration)
goes through downloads with no change to the maintenance code. A scratch upstream repository as the reviewed channel, a
stand-in uv and a local file server."""
import hashlib
import http.server
import os
import threading
import time

import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, downloads, env_runner, envs
from orchestrator.maintenance import policy, prepare, promote, revocations, status
from tests.test_channels import _git, home  # noqa: F401 - `home` is the fixture
from tests.test_env_runner import ADAPTER, ENTRY, WRONG, standin_uv

IDS = [f"spots-{i:02d}" for i in range(1, 13)]
FIX = ADAPTER.replace("< 128", "< 129")


def manifest(i: str, v: str, *, licence: str = "MIT", files: str = "") -> str:
    text = (ENTRY.format().replace('"dark-spots 0.1"', f'"{i} {v}"').replace('id = "dark-spots"', f'id = "{i}"')
            .replace('impl = "dark-spots"', f'impl = "{i}"').replace('"install:dark-spots"', f'"install:{i}"')
            .replace('licence = ["dark-spots"]', f'licence = ["{i}"]').replace('licence = "MIT"', f'licence = "{licence}"'))
    return text.replace('[[implements]]', files + '\n[[implements]]') if files else text


def publish(home, entries: dict[str, tuple[str, str]], notices: str = "") -> str:
    """One upstream commit: each entry's manifest and adapter."""
    models = home / "upstream" / "catalog" / "models"
    for i, (text, adapter) in entries.items():
        folder = models / i
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "model.toml").write_text(text)
        (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
        (folder / "adapter.py").write_text(adapter)
    if notices:
        (models / revocations.FILE).write_text(notices)
    _git("add", "-A", ".", cwd=home / "upstream")
    _git("commit", "-q", "-m", "catalog", cwd=home / "upstream")
    return _git("rev-parse", "HEAD", cwd=home / "upstream")


@pytest.fixture
def served(home):
    """A file server for the entry with pinned weight files."""
    root = home / "served"
    root.mkdir()
    handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(root), **k)  # noqa: E731
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield root, f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def weights(root, url: str, name: str, data: bytes, *, pinned: bytes | None = None) -> str:
    (root / name).write_bytes(data)
    sha = hashlib.sha256(pinned if pinned is not None else data).hexdigest()
    return f'files = [{{ path = "weights.bin", bytes = {len(data)}, sha256 = "{sha}", url = "{url}/{name}" }}]\n'


def install_all(home, monkeypatch) -> None:
    standin_uv(home, monkeypatch)
    queue = envs.Installs()
    for i in IDS:
        m = catalog.get(i)
        if m.files:
            downloads.download(m)
        queue.start(i)
    deadline = time.monotonic() + 120  # completion wait, not a performance assertion
    while time.monotonic() < deadline and any(r["state"] not in ("done", "failed") for r in queue.wire()):
        time.sleep(0.05)
    assert all(r["state"] == "done" for r in queue.wire()), queue.wire()


def test_twelve_entries_each_get_their_own_outcome_and_none_stops_another(home, monkeypatch, served):
    root, url = served
    v1 = {i: (manifest(i, "0.1"), ADAPTER) for i in IDS[:10]}
    v1["spots-11"] = (manifest("spots-11", "0.1", files=weights(root, url, "a1.bin", b"weights one")), ADAPTER)
    v1["spots-12"] = (manifest("spots-12", "0.1", files=weights(root, url, "b1.bin", b"weights uno")), ADAPTER)
    publish(home, v1)
    channels.update()
    install_all(home, monkeypatch)
    for i in IDS:
        policy.record(catalog.get(i), "auto", by="you", how="screen")
    before = {i: env_runner.selected(catalog.get(i)).folder.name for i in IDS}

    v2 = {i: (manifest(i, "0.2"), FIX) for i in IDS[:6]}  # compatible fixes
    v2["spots-07"] = (manifest("spots-07", "0.2", licence="GPL-3.0"), FIX)  # a person accepts a licence
    v2["spots-08"] = (manifest("spots-08", "0.2"), WRONG)  # fails its proof
    v2["spots-09"] = (manifest("spots-09", "0.2"), FIX)  # revoked by its maintainers
    v2["spots-11"] = (manifest("spots-11", "0.2", files=weights(root, url, "a2.bin", b"weights two")), FIX)
    v2["spots-12"] = (manifest("spots-12", "0.2", files=weights(root, url, "b2.bin", b"weights dos",
                                                               pinned=b"not these")), FIX)  # its file doesn't match
    publish(home, v2, notices='[[revoked]]\nentry = "spots-09"\nversion = "spots-09 0.2"\nwhy = "a regression"\n')

    looks, updates, fetches = [], [], []
    real_newest, real_update, real_fetch = channels.newest, channels.update, channels.fetch
    monkeypatch.setattr(channels, "newest", lambda url: looks.append(url) or real_newest(url))
    monkeypatch.setattr(channels, "update", lambda *a, **k: updates.append(1) or real_update(*a, **k))
    monkeypatch.setattr(channels, "fetch", lambda *a, **k: fetches.append(a[1]) or real_fetch(*a, **k))
    pinned = next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"]
    rows = {i: prepare.run(i) for i in IDS}  # as the Maintainer runs them: one after another
    states = {i: r["state"] for i, r in rows.items()}
    assert states == {**{i: "applied" for i in IDS[:6]}, "spots-07": "needs-review", "spots-08": "failed",
                      "spots-09": "unavailable", "spots-10": "none", "spots-11": "needs-review", "spots-12": "failed"}, rows
    assert len(looks) == 1 and len(fetches) == 1, "one look at the channel and one candidate snapshot served them all"
    assert updates == [] and next(c for c in catalog.channels() if c["name"] == "reviewed")["commit"] == pinned, \
        "the shared channel never moved"
    # New weights are a change only a person accepts (ADR 0025): downloaded, verified and proven first, then accepted.
    assert rows["spots-11"]["prepared"] is True and "its weights and files changed" in rows["spots-11"]["says"]
    from orchestrator.maintenance import review

    review.accept(catalog.get("spots-11"), rows["spots-11"]["fingerprint"], rows["spots-11"]["review"], by="you",
                  how="screen")
    assert prepare.run("spots-11")["state"] == "applied"
    assert "didn't match" in rows["spots-12"]["why"] or "match" in rows["spots-12"]["says"], rows["spots-12"]
    for i in ("spots-07", "spots-08", "spots-09", "spots-10", "spots-12"):
        assert env_runner.selected(catalog.get(i)).folder.name == before[i], f"{i} keeps the version it had"
    for i in [*IDS[:6], "spots-11"]:
        assert env_runner.selected(catalog.get(i)).m.version == f"{i} 0.2"
    m11 = env_runner.selected(catalog.get("spots-11"))
    assert (env_runner.files_dir(m11) / "weights.bin").read_bytes() == b"weights two", "its new weights, verified"

    decided = len(promote.decisions())
    again = {i: prepare.run(i)["state"] for i in IDS}
    assert len(promote.decisions()) == decided, "a second pass with nothing new decides nothing"
    assert all(again[i] == "none" for i in [*IDS[:6], "spots-10", "spots-11"]), again
    assert {i: status.of(catalog.get(i))["state"] for i in ("spots-07", "spots-08", "spots-09")} == {
        "spots-07": "needs-review", "spots-08": "failed", "spots-09": "unavailable"}


def test_an_entry_removed_upstream_is_kept_and_the_others_still_update(home, monkeypatch):
    publish(home, {i: (manifest(i, "0.1"), ADAPTER) for i in IDS[:3]})
    channels.update()
    standin_uv(home, monkeypatch)
    queue = envs.Installs()
    for i in IDS[:3]:
        queue.start(i)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and any(r["state"] not in ("done", "failed") for r in queue.wire()):
        time.sleep(0.05)
    for i in IDS[:3]:
        policy.record(catalog.get(i), "auto", by="you", how="screen")
    _git("rm", "-r", "-q", f"catalog/models/{IDS[2]}", cwd=home / "upstream")
    publish(home, {i: (manifest(i, "0.2"), FIX) for i in IDS[:2]})
    rows = {i: prepare.run(i) for i in IDS[:3]}
    assert [rows[i]["state"] for i in IDS[:2]] == ["applied", "applied"], rows
    assert "no longer has it; it keeps working here" in rows[IDS[2]]["says"]
    assert env_runner.selected(catalog.get(IDS[2])).m.version == f"{IDS[2]} 0.1", "it keeps working here"
    assert status.of(catalog.get(IDS[2]))["maintained"]["state"] == "unmaintained"


def test_an_attempt_already_running_for_an_entry_is_not_started_twice(home, monkeypatch):
    import fcntl

    from orchestrator.maintenance import store

    lock = store.path(f"attempts/{IDS[0]}.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)  # another app's attempt of the same entry
    try:
        assert prepare.run(IDS[0]) == {"key": IDS[0], "state": "busy", "says": "Its update is being prepared already"}
    finally:
        os.close(fd)


def test_too_little_disk_holds_only_that_entry(home, monkeypatch):
    publish(home, {i: (manifest(i, "0.1"), ADAPTER) for i in IDS[:1]})
    channels.update()
    standin_uv(home, monkeypatch)
    queue = envs.Installs()
    queue.start(IDS[0])
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and queue.wire()[0]["state"] not in ("done", "failed"):
        time.sleep(0.05)
    policy.record(catalog.get(IDS[0]), "auto", by="you", how="screen")
    publish(home, {IDS[0]: (manifest(IDS[0], "0.2"), FIX)})
    monkeypatch.setattr(prepare, "SPARE_BYTES", 10 ** 18)  # a disk with no room left
    row = prepare.run(IDS[0])
    assert row["state"] == "unavailable" and row["says"].startswith("Update unavailable here: it needs "), row
    assert env_runner.selected(catalog.get(IDS[0])).m.version == f"{IDS[0]} 0.1"
