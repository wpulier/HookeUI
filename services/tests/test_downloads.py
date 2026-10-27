"""The models folder (orchestrator/downloads.py, gpu_worker/model_files.py; P2 of the open catalog): staging, verify,
publish; resume where the server supports it and a safe restart where it doesn't; nothing unverified ever published; a
revision never written over; adopt from an old cache; the app's queue. A stand-in Hugging Face server (httpx's
MockTransport, honouring Range like the real one) and a catalog of our own in a temporary folder: never this Mac's
downloads or models folder."""
import hashlib
import importlib
import json
import os
import socket
import stat
import threading
from pathlib import Path

import httpx
import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import downloads as d
from orchestrator import readiness
from tests.waiting import HANG_S

REV_A, REV_B = "a" * 40, "b" * 40
WEIGHTS = bytes(range(256)) * 40  # 10240 bytes
CONFIG = b'{"model_type": "bert"}'


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def manifest(root: Path, rev: str = REV_A, weights: bytes = WEIGHTS, gated: bool = False) -> None:
    folder = root / "test-finder"
    folder.mkdir(parents=True, exist_ok=True)
    get = '["key:HF_TOKEN", "licence:https://huggingface.co/org/test-finder", "download"]' if gated else '["download"]'
    (folder / "model.toml").write_text(f'''id = "test-finder"
name = "Test finder"
runs = "this-mac"
version = "org/test-finder"
licence = ["edgetam"]
kind = "model"
what = "Outlines what you click"
runner = "gpu-worker"
source = {{ hf = "org/test-finder", revision = "{rev}" }}
files = [
  {{ path = "config.json", bytes = {len(CONFIG)}, git_sha1 = "{git(CONFIG)}" }},
  {{ path = "model.safetensors", bytes = {len(weights)}, sha256 = "{sha(weights)}" }},
  {{ path = "companions/org/backbone/config.json", bytes = {len(CONFIG)}, git_sha1 = "{git(CONFIG)}", from = {{ hf = "org/backbone", revision = "{'c' * 40}", path = "config.json" }} }},
]
needs_to_get = {get}
needs_to_run = []
workers = ["analysis"]
documented_mb = 1
load_limit_s = 1
''')
    catalog.manifests.cache_clear()


@pytest.fixture
def home(tmp_path, monkeypatch):
    # The fixed reviewed operation registry needs its catalog before this test replaces it.
    importlib.import_module('orchestrator.operations')
    monkeypatch.setattr(catalog, "ROOT", tmp_path / "repo" / "models")
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "old"))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setattr(d, "CHUNK", 1024)
    monkeypatch.setattr(d, "SPARE", 0)
    manifest(tmp_path / "repo" / "models")
    yield tmp_path
    catalog.manifests.cache_clear()


class Server:
    """A stand-in for huggingface.co: each file of the repos at their revisions, Range answered with 206 (or ignored,
    `ranges=False`), a status forced for every request (`status`), and what each request asked for kept."""

    def __init__(self, weights: bytes = WEIGHTS) -> None:
        self.files = {"org/test-finder": {"config.json": CONFIG, "model.safetensors": weights},
                      "org/backbone": {"config.json": CONFIG}}
        self.ranges, self.status, self.headers, self.asked = True, 0, {}, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.asked.append((str(request.url), request.headers.get("range", ""), request.headers.get("authorization", "")))
        if self.status:
            return httpx.Response(self.status, headers=self.headers)
        parts = request.url.path.strip("/").split("/")
        repo, path = "/".join(parts[:2]), "/".join(parts[4:])
        data = self.files.get(repo, {}).get(path)
        if data is None:
            return httpx.Response(404)
        wanted = request.headers.get("range", "")
        if wanted and self.ranges:
            start = int(wanted.removeprefix("bytes=").rstrip("-"))
            return httpx.Response(206, content=data[start:], headers={"content-range": f"bytes {start}-{len(data) - 1}/{len(data)}"})
        return httpx.Response(200, content=data)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def model():
    return catalog.get("test-finder")


@pytest.mark.parametrize('route', ['queue', 'download'])
@pytest.mark.parametrize('where', ['needs_to_get', 'needs_to_run'])
def test_incompatible_download_is_refused_before_network_or_staging(home, monkeypatch, route, where):
    from dataclasses import replace

    m = replace(model(), **{where: ('platform:unsupported-machine',)})
    monkeypatch.setattr(catalog, 'get', lambda key: m if key == m.key else None)
    server = Server()
    queue = d.Downloads()
    attempt = (lambda: queue.start(m.key)) if route == 'queue' else (lambda: d.download(m, client=server.client()))
    with pytest.raises(d.Failed, match='cannot be prepared.*unsupported-machine'):
        attempt()
    assert server.asked == [] and queue.wire() == []
    assert not d.staging(m).exists()


def test_corrected_machine_requirement_retries_without_changing_other_downloads(home):
    from dataclasses import replace

    m = model()
    server = Server()
    with pytest.raises(d.Failed, match='GB of memory'):
        d.download(replace(m, needs_to_run=('memory:1000000000',)), client=server.client())
    assert server.asked == []
    folder = d.download(m, client=server.client())
    receipt = (folder / 'receipt.json').read_bytes()
    with pytest.raises(d.Failed, match='GB of memory'):
        d.download(replace(m, needs_to_run=('memory:1000000000',)), client=server.client())
    assert (folder / 'receipt.json').read_bytes() == receipt
    assert model_files.published(m) == folder


def test_a_download_is_verified_published_read_only_and_what_the_loaders_read(home):
    server = Server()
    where = d.download(model(), client=server.client())
    assert where == home / "catalog" / "models" / "test-finder" / REV_A
    receipt = json.loads((where / "receipt.json").read_text())
    assert receipt["revision"] == REV_A and receipt["how"] == "downloaded" and len(receipt["files"]) == 3
    assert (where / "model.safetensors").read_bytes() == WEIGHTS
    assert not (where / "model.safetensors").stat().st_mode & stat.S_IWUSR, "published files are read-only"
    assert not list(where.parent.glob(".staging-*")), "the staging folder became the revision"
    assert model_files.model_dir("test-finder") == where and readiness.obtained(model()) == (True, 0)
    assert any("org/backbone/resolve/" + "c" * 40 + "/config.json" in url for url, _, _ in server.asked), \
        "a companion comes from its own repository at its own pinned revision"


def test_an_interrupted_download_resumes_where_it_stopped(home):
    server = Server()
    progress = d.Progress()
    with pytest.raises(d.Cancelled):
        d.download(model(), client=server.client(), progress=progress, stop=lambda: progress.got > 4000)
    assert model_files.published(model()) is None and readiness.obtained(model())[0] is False
    part = d.staging(model()) / "model.safetensors.part"
    have = part.stat().st_size
    assert 0 < have < len(WEIGHTS)
    server.asked.clear()
    d.download(model(), client=server.client())
    assert next(r for url, r, _ in server.asked if url.endswith("model.safetensors")) == f"bytes={have}-"
    assert model_files.published(model()) is not None


def test_a_server_that_cannot_resume_starts_the_file_again_safely(home):
    server = Server()
    progress = d.Progress()
    with pytest.raises(d.Cancelled):
        d.download(model(), client=server.client(), progress=progress, stop=lambda: progress.got > 4000)
    server.ranges = False  # answers 200 with the whole file to a Range request
    where = d.download(model(), client=server.client())
    assert (where / "model.safetensors").read_bytes() == WEIGHTS, "never the old part with the whole file appended"


def test_a_file_that_does_not_match_its_pin_is_never_published(home):
    server = Server(weights=WEIGHTS[:-1] + b"X")  # the right size, another content
    with pytest.raises(d.Failed) as failed:
        d.download(model(), client=server.client())
    assert "didn't match" in failed.value.says and failed.value.step == "retry"
    assert model_files.published(model()) is None and not (model_files.revision_dir(model())).exists()
    assert not (d.staging(model()) / "model.safetensors").exists(), "the wrong file is deleted, so a retry fetches it"
    assert (d.staging(model()) / "config.json").exists(), "the right ones are kept for the retry"
    d.download(model(), client=Server().client())
    assert model_files.published(model()) is not None


def test_a_new_revision_is_a_new_folder_and_the_working_one_is_never_touched(home):
    d.download(model(), client=Server().client())
    first = model_files.revision_dir(model())
    before = {p.name: p.read_bytes() for p in first.iterdir() if p.is_file()}
    other = WEIGHTS[::-1]
    manifest(home / "repo" / "models", rev=REV_B, weights=other)
    assert model_files.published(model()) is None, "the manifest pins another revision now: not downloaded"
    progress = d.Progress()
    with pytest.raises(d.Cancelled):
        d.download(model(), client=Server(weights=other).client(), progress=progress, stop=lambda: progress.got > 3000)
    assert {p.name: p.read_bytes() for p in first.iterdir() if p.is_file()} == before, "a cancel never touches it"
    second = d.download(model(), client=Server(weights=other).client())
    assert second.name == REV_B and first.is_dir() and (first / "model.safetensors").read_bytes() == WEIGHTS
    assert model_files.model_dir("test-finder") == second, "the loaders read exactly the pinned revision"


def test_a_gated_model_says_the_key_or_the_licence_it_needs(home, monkeypatch):
    manifest(home / "repo" / "models", gated=True)
    with pytest.raises(d.Failed) as failed:
        d.download(model(), client=Server().client())
    assert failed.value.step == "key" and "Hugging Face key" in failed.value.says
    monkeypatch.setenv("HF_TOKEN", "hf_test_value")
    server = Server()
    server.status = 403
    with pytest.raises(d.Failed) as failed:
        d.download(model(), client=server.client())
    assert failed.value.step == "licence" and failed.value.url == "https://huggingface.co/org/test-finder"
    assert server.asked[0][2] == "Bearer hf_test_value"
    assert "hf_test_value" not in failed.value.says


def test_a_refused_key_is_never_said_as_a_licence_and_hugging_faces_own_words_are_kept(home, monkeypatch):
    """Hugging Face answers a key it doesn't accept with 401 and an account without access with 403 (both GatedRepo;
    observed 2026-10-20), so a mistyped or revoked key never sends the researcher to accept a licence they accepted
    (F057). What Hugging Face said is kept, one bounded line, so "awaiting review" and "ask for access" are told apart."""
    manifest(home / "repo" / "models", gated=True)
    monkeypatch.setenv("HF_TOKEN", "hf_revoked_value")
    server = Server()
    server.status, server.headers = 401, {"x-error-code": "GatedRepo", "x-error-message": "Access to model org/test-finder "
                                          "is restricted. You must have access to it and be authenticated to access it."}
    with pytest.raises(d.Failed) as failed:
        d.download(model(), client=server.client())
    assert failed.value.step == "key" and "didn't accept your key" in failed.value.says
    assert "licence" not in failed.value.says and "hf_revoked_value" not in failed.value.says
    server.status, server.headers = 403, {"x-error-code": "GatedRepo", "x-error-message": "Your request to access model "
                                          "org/test-finder is awaiting a review from the repo authors.\nhf_revoked_value"}
    with pytest.raises(d.Failed) as failed:
        d.download(model(), client=server.client())
    assert failed.value.step == "licence" and failed.value.url == "https://huggingface.co/org/test-finder"
    assert "awaiting a review from the repo authors" in failed.value.says
    assert "hf_revoked_value" not in failed.value.says and "\n" not in failed.value.says


def test_the_token_goes_only_to_hugging_face(home, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_test_value")
    assert d._headers("https://huggingface.co/org/x/resolve/r/f", os.environ) == {"Authorization": "Bearer hf_test_value"}
    assert d._headers("https://uk1s3.embassy.ebi.ac.uk/f.pt", os.environ) == {}
    assert d._headers("https://huggingface.co.example.com/f", os.environ) == {}


def test_no_room_on_the_disk_is_said_before_anything_is_fetched(home, monkeypatch):
    monkeypatch.setattr(d.shutil, "disk_usage", lambda p: type("U", (), {"free": 100})())
    server = Server()
    with pytest.raises(d.Failed) as failed:
        d.download(model(), client=server.client())
    assert failed.value.step == "disk" and not server.asked


def test_turn_on_s_sheet_reads_the_free_space_where_models_go_and_each_download_s_bytes(home, monkeypatch):
    # The sheet before setup (capabilities-for-everyone C2) weighs a plan's downloads against this, as download() does.
    seen = []
    monkeypatch.setattr(d.shutil, "disk_usage", lambda p: seen.append(Path(p)) or type("U", (), {"free": 5_000_000})())
    assert d.disk() == {"freeBytes": 5_000_000, "spareBytes": d.SPARE}
    assert seen == [home]  # the catalog folder isn't made yet: the nearest folder of it that is
    monkeypatch.setattr(d.shutil, "disk_usage", lambda p: (_ for _ in ()).throw(OSError("unreadable")))
    assert d.disk() == {}
    steps, got, missing = readiness.model_steps(model(), lambda key: True)
    step = next(s for s in steps if s.kind == "download")
    assert got is False and step.bytes == missing == 2 * len(CONFIG) + len(WEIGHTS)
    assert step.wire()["bytes"] == missing


def test_one_writer_at_a_time(home):
    with d.Lock(model()):
        with pytest.raises(d.Failed, match="already being downloaded"):
            d.download(model(), client=Server().client())


def _old_cache(home: Path) -> None:
    """The model's files in an old Hugging Face cache at the pinned revisions (blobs named by content id)."""
    for repo, rev, files in (("org/test-finder", REV_A, {"config.json": CONFIG, "model.safetensors": WEIGHTS}),
                             ("org/backbone", "c" * 40, {"config.json": CONFIG})):
        base = home / "old" / "hf" / f"models--{repo.replace('/', '--')}"
        (base / "blobs").mkdir(parents=True, exist_ok=True)
        (base / "snapshots" / rev).mkdir(parents=True, exist_ok=True)
        for name, data in files.items():
            blob = sha(data) if name.endswith("safetensors") else git(data)
            (base / "blobs" / blob).write_bytes(data)
            (base / "snapshots" / rev / name).symlink_to(Path("..") / ".." / "blobs" / blob)


def test_the_old_cache_is_read_at_the_pinned_revision_until_adopted(home):
    _old_cache(home)
    assert model_files.location(model()) == ("old cache", 0)
    snapshot = model_files.model_dir("test-finder")
    assert snapshot.name == REV_A and "old" in snapshot.parts
    where, cloned = d.adopt(model())
    assert where == model_files.revision_dir(model()) and json.loads((where / "receipt.json").read_text())["how"] in (
        "adopted", "copied")
    assert model_files.location(model()) == ("folder", 0) and model_files.model_dir("test-finder") == where
    assert (where / "companions/org/backbone/config.json").read_bytes() == CONFIG


def test_adopt_refuses_what_the_old_cache_has_only_in_part(home):
    _old_cache(home)
    (home / "old" / "hf" / "models--org--backbone" / "snapshots" / ("c" * 40) / "config.json").unlink()
    assert model_files.location(model())[0] is None
    with pytest.raises(d.Failed, match="download it instead"):
        d.adopt(model())


@pytest.mark.parametrize('storage', ['published', 'old-cache'])
def test_a_companion_is_given_to_the_library_from_the_folder_with_no_network(home, monkeypatch, storage):
    pytest.importorskip("transformers")
    from transformers import AutoConfig

    if storage == 'published':
        d.download(model(), client=Server().client())
    else:
        _old_cache(home)  # pinned snapshot exists, but no mutable refs/main

    def refuse(*args, **kwargs):
        raise AssertionError("reached for the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    original = AutoConfig.from_pretrained
    try:
        model_files.companions("test-finder")
        assert AutoConfig.from_pretrained("org/backbone").model_type == "bert"
        assert AutoConfig.from_pretrained(str(model_files.model_dir("test-finder"))).model_type == "bert"
    finally:
        AutoConfig.from_pretrained = original
        model_files._redirected.clear()


@pytest.mark.timing  # it reads the first state while the download runs: on a loaded Mac the download finished first
def test_the_queue_downloads_in_the_background_and_says_each_state(home, monkeypatch):
    server = Server()
    real = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(server)))
    changes = []
    done = threading.Event()

    def changed():
        changes.append(1)
        if any(r["state"] in ("done", "failed") for r in queue.wire()):
            done.set()

    queue = d.Downloads(changed=changed)
    row = queue.start("test-finder")
    assert row["state"] in ("queued", "downloading") and row["total"] == len(WEIGHTS) + 2 * len(CONFIG)
    assert queue.start("test-finder")["state"] in ("queued", "downloading", "checking", "done"), "a second press joins it"
    assert done.wait(HANG_S)
    assert queue.wire()[0]["state"] == "done" and model_files.published(model()) is not None


def test_the_queue_refuses_what_it_cannot_download_with_the_step(home):
    queue = d.Downloads()
    with pytest.raises(d.Failed):
        queue.start("no-such-model")
    manifest(home / "repo" / "models", gated=True)
    with pytest.raises(d.Failed) as failed:
        queue.start("test-finder")
    assert failed.value.step == "key" and not queue.wire()


# --- mirrors where the rights allow (C7) --------------------------------------------------------------------------------
MIRROR = "https://mirror.example.org/test-finder/model.safetensors"
RIGHTS = "Apache-2.0 (its card): redistribution permitted with the licence and notice kept"


def with_mirror(root: Path, rights: str = RIGHTS) -> None:
    path = root / "test-finder" / "model.toml"
    text = path.read_text()
    old = f'{{ path = "model.safetensors", bytes = {len(WEIGHTS)}, sha256 = "{sha(WEIGHTS)}" }}'
    assert old in text
    path.write_text(text.replace(old, old[:-2] + f', mirror = {{ url = "{MIRROR}", rights = "{rights}" }} }}'))
    catalog.manifests.cache_clear()


class Mirrored(Server):
    """Its own place has lost the weights; the mirror has them (or something else in their place)."""

    def __init__(self, mirrored: bytes = WEIGHTS) -> None:
        super().__init__()
        del self.files["org/test-finder"]["model.safetensors"]
        self.mirrored = mirrored

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "mirror.example.org":
            self.asked.append((str(request.url), request.headers.get("range", ""), request.headers.get("authorization", "")))
            return httpx.Response(200, content=self.mirrored)
        return super().__call__(request)


def test_a_file_gone_from_its_place_comes_from_its_mirror_checked_by_its_hash(home, monkeypatch):
    with_mirror(home / "repo" / "models")
    monkeypatch.setenv("HF_TOKEN", "hf_canary")
    server = Mirrored()
    where = d.download(model(), client=server.client())
    assert (where / "model.safetensors").read_bytes() == WEIGHTS
    mirrored = [a for url, _, a in server.asked if url == MIRROR]
    assert mirrored == [""], "fetched once from its mirror, and never with the Hugging Face key"


def test_a_mirror_that_sends_another_file_is_never_published(home):
    with_mirror(home / "repo" / "models")
    with pytest.raises(d.Failed, match="didn't match"):
        d.download(model(), client=Mirrored(WEIGHTS[:-1] + b"X").client())
    assert model_files.published(model()) is None


def test_a_mirror_without_its_rights_stated_is_no_entry(home):
    with_mirror(home / "repo" / "models", rights="ok")
    assert catalog.get("test-finder") is None
    assert any("rights" in p for p in catalog.problems().get("test-finder", []))
