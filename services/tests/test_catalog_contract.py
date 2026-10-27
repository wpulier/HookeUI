"""Present means usable (phase 3's E1 of docs/design/2026-10-12-the-open-catalog.md): every requirement a manifest can
name has a fulfiller and a detector; nothing loads a model's weights from anywhere but its pinned files; and each gap the
catalog audit of 2026-10-12 found (G1-G8) stays closed. A catalog of our own in a temporary folder, stand-in servers:
never this Mac's models folder, caches or network."""
import hashlib
import importlib
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx
import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import downloads as d
from orchestrator import observed, readiness

SERVICES = Path(__file__).resolve().parent.parent


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --- every requirement: a fulfiller and a detector -----------------------------------------------------------------------
def test_every_kind_of_requirement_has_a_fulfiller_and_a_detector():
    assert set(readiness.FULFILLERS) == set(catalog.NEED_KINDS), "a requirement kind nothing fulfils"
    assert set(observed.SOURCES) >= set(catalog.NEED_KINDS), "a requirement kind nothing reads"
    for kind, how in readiness.FULFILLERS.items():
        if how.startswith(("user: ", "none: ")):
            continue
        module, _, name = how.rpartition(".")
        assert hasattr(importlib.import_module(module), name), (kind, how)


# --- nothing loads weights from anywhere but its pinned files --------------------------------------------------------------
# Every call into a model library that could fetch weights, reviewed: each reads the catalog's pinned files offline. A
# new loader fails here until it is written the same way and added (its line's words) with why.
LOADS = re.compile(r"from_pretrained\(|hf_hub_download|snapshot_download|torch\.hub|download_pretrained|"
                   r"create_model_and_transforms\(|CellposeModel\(|get_sam_model\(|build_sam3_predictor\(|from_folder\(")
REVIEWED = {
    ("gpu_worker/worker.py", "Sam2Processor.from_pretrained(path, local_files_only=True)"),
    ("gpu_worker/worker.py", "cls.from_pretrained(path, local_files_only=True)"),
    ("gpu_worker/worker.py", "build_sam3_predictor(checkpoint_path=ckpt"),  # model_files.model_file, pinned
    ("gpu_worker/embed.py", "AutoModel.from_pretrained(path, local_files_only=True)"),
    ("gpu_worker/embed.py", "open_clip.create_model_and_transforms(local)"),  # local-dir: of model_dir
    ("gpu_worker/molmo.py", "AutoProcessor.from_pretrained(path, trust_remote_code=True, local_files_only=True)"),
    ("gpu_worker/molmo.py", "AutoModelForImageTextToText.from_pretrained(path, trust_remote_code=True, local_files_only=True"),
    ("gpu_worker/zeroshot.py", 'cls.from_pretrained(model_dir("medsiglip"), local_files_only=True'),
    ("gpu_worker/objects.py", "EdgeTamVideoModel.from_pretrained(path, local_files_only=True)"),
    ("gpu_worker/objects.py", "Sam2VideoProcessor.from_pretrained(path, local_files_only=True)"),
    ("gpu_worker/model_files.py", "def from_pretrained(cls, name"),  # the companions' redirect to the revision folder
    ("gpu_worker/model_files.py", "`from_pretrained(path, local_files_only=True)`"),  # a docstring
    ("noncommercial/cellpose_cells.py", "pretrained_model=str(path or base)"),  # model_files.model_file, or a fine-tune made here
    ("noncommercial/cellpose_file_check.py", "models.CellposeModel(pretrained_model=...)"),  # a docstring
    ("noncommercial/microsam_server.py", "self.util.get_sam_model("),  # refused unless given its pinned files
    ("noncommercial/trackastra_link.py", "Trackastra.from_folder(folder"),  # model_files.model_dir
    ("noncommercial/trackastra_server.py", 'Trackastra.from_folder(Path(header["folder"])'),  # the folder trackastra_link sends
    ("orchestrator/publish.py", '"loads from torch.hub at run time'),  # what publishing refuses in an entry's code
}


def test_every_loader_reads_only_the_pinned_files():
    found = set()
    for top in ("gpu_worker", "noncommercial", "orchestrator", "research_worker"):
        for path in (SERVICES / top).rglob("*.py"):
            rel = str(path.relative_to(SERVICES))
            for line in path.read_text().splitlines():
                if LOADS.search(line) and not line.strip().startswith("#"):
                    match = next((words for where, words in REVIEWED if where == rel and words in line), None)
                    assert match is not None, f"{rel}: a load that could fetch weights, not reviewed: {line.strip()}"
                    found.add((rel, match))
    assert found == REVIEWED, f"reviewed loads no longer in the code: {sorted(REVIEWED - found)}"


# --- G1: Trackastra's weights are the catalog's, fetched from their pinned archive -------------------------------------------
def test_trackastra_pins_its_files_from_its_archive():
    m = catalog.get("trackastra")
    assert {f["path"] for f in m.files} == {"model.pt", "config.yaml", "train_config.yaml"}
    assert all(f["archive"]["member"] == f"general_2d/{f['path']}" and f["url"].endswith("/general_2d.zip")
               for f in m.files)
    assert [s.kind for s in readiness.model("trackastra", {}, environ={"MICROMIND_MODEL_FILES": "/nonexistent",
                                                                       "MICROMIND_CATALOG": "/nonexistent"}).steps
            if s.kind == "download"], "its card offers the download"


ZIP_FILES = {"bundle/model.pt": bytes(range(256)) * 20, "bundle/config.yaml": b"a: 1\n"}


def _zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in ZIP_FILES.items():
            z.writestr(name, data)
    return buf.getvalue()


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(catalog, "ROOT", tmp_path / "repo" / "models")
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "old"))
    monkeypatch.setattr(d, "SPARE", 0)
    catalog.manifests.cache_clear()
    yield tmp_path
    catalog.manifests.cache_clear()


def archived_model(root: Path, archive: bytes) -> None:
    folder = root / "zipped"
    folder.mkdir(parents=True, exist_ok=True)
    url = "https://example.org/bundle.zip"
    files = ",\n".join(
        f'  {{ path = "{name.split("/")[1]}", bytes = {len(data)}, sha256 = "{sha(data)}", url = "{url}", '
        f'archive = {{ sha256 = "{sha(archive)}", bytes = {len(archive)}, member = "{name}" }} }}'
        for name, data in ZIP_FILES.items())
    (folder / "model.toml").write_text(f'''id = "zipped"
name = "Zipped"
runs = "cpu"
version = "v1"
licence = ["edgetam"]
kind = "model"
what = "Links cells"
runner = ""
source = {{ url = "{url}" }}
files = [
{files}
]
needs_to_get = ["download"]
''')
    catalog.manifests.cache_clear()


def test_reviewed_entries_and_diagnostics_share_reads_and_follow_file_changes(home, monkeypatch):
    """A Models response does not parse the same unchanged catalog twice.

    Count real file opens, then mutate/add/remove actual files. Diagnostics and
    available entries must observe the same refreshed reviewed snapshot.
    """
    import os

    archived_model(catalog.ROOT, _zip())
    monkeypatch.setattr(catalog, "SIGNATURE_S", 0)
    source = catalog.ROOT / "zipped" / "model.toml"
    original = source.read_text()
    opens = []
    open_file = Path.open

    def observed_open(path, *args, **kwargs):
        if path.is_relative_to(catalog.ROOT) and path.name == "model.toml":
            opens.append(path)
        return open_file(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", observed_open)
    for _ in range(3):
        assert catalog.manifests()["zipped"].name == "Zipped"
        assert catalog.problems() == {}
    assert opens == [source], 'unchanged entries and diagnostics should share one real read'

    # An atomic replacement can preserve mtime and byte count.
    previous = source.stat()
    replacement = source.with_suffix('.new')
    replacement.write_text(original.replace('name = "Zipped"', 'name = "Edited"'))
    os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    replacement.replace(source)
    assert catalog.manifests()["zipped"].name == "Edited"
    source.write_text('invalid = [')
    assert "zipped" in catalog.problems() and "zipped" not in catalog.manifests()
    source.write_text(original)
    assert catalog.problems() == {} and "zipped" in catalog.manifests()
    added = catalog.ROOT / "broken" / "model.toml"
    added.parent.mkdir()
    added.write_text('invalid = [')
    assert "broken" in catalog.problems()
    added.unlink()
    assert catalog.problems() == {}


def test_a_file_inside_a_pinned_archive_is_fetched_once_checked_and_published(home):
    archive = _zip()
    archived_model(home / "repo" / "models", archive)
    asked = []

    def serve(request):
        asked.append(str(request.url))
        return httpx.Response(200, content=archive)

    path = d.download(catalog.get("zipped"), client=httpx.Client(transport=httpx.MockTransport(serve)))
    assert {p.name: p.read_bytes() for p in path.iterdir() if p.name != "receipt.json"} == \
        {k.split("/")[1]: v for k, v in ZIP_FILES.items()}
    assert asked == ["https://example.org/bundle.zip"], "the archive fetched once for both of its files"
    assert not (path.parent / ".archives").exists(), "the archive is gone once its files are published"
    assert model_files.model_dir("zipped") == path


def test_an_archive_that_is_not_the_one_pinned_publishes_nothing(home):
    archived_model(home / "repo" / "models", _zip())
    other = _zip() + b"tampered"
    with pytest.raises(d.Failed, match="archive didn't match"):
        d.download(catalog.get("zipped"),
                   client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=other))))
    assert model_files.published(catalog.get("zipped")) is None


# --- G2 is test_catalog's (a download that names no files) ---------------------------------------------------------------

# --- G5: an install made while the app runs is seen -------------------------------------------------------------------------
def test_a_package_installed_while_the_app_runs_is_seen(tmp_path, monkeypatch):
    from orchestrator import operations

    site_dir = tmp_path / "site"
    site_dir.mkdir()
    monkeypatch.setattr("site.getsitepackages", lambda: [str(site_dir)])
    monkeypatch.setattr("site.getusersitepackages", lambda: str(tmp_path / "none"))
    monkeypatch.setattr(operations, "_pth_seen", None)
    monkeypatch.setattr(sys, "path", list(sys.path))
    assert operations._package("micromind_late_pkg", "make it").startswith("not installed here")
    code = tmp_path / "elsewhere"
    (code / "micromind_late_pkg").mkdir(parents=True)
    (code / "micromind_late_pkg" / "__init__.py").write_text("")
    (site_dir / "late.pth").write_text(str(code) + "\n")  # as an editable install leaves it
    assert operations._package("micromind_late_pkg", "make it") == "", "seen without a restart"


# --- G6: a model not downloaded is never held as a failure ---------------------------------------------------------------------
def test_a_picture_model_that_was_not_downloaded_loads_once_it_is(monkeypatch):
    from gpu_worker import embed as embed_module
    from gpu_worker import worker

    m = worker.Models.__new__(worker.Models)
    m._embedders, m._embed_failed, m.device = {}, {}, "cpu"
    m.loading = lambda name: __import__("contextlib").nullcontext()
    there = [False]

    def embedder(name, device):
        if not there[0]:
            raise model_files.NotDownloaded(catalog.get("dinov3-small"), 86_000_000)
        return "loaded"

    monkeypatch.setattr(embed_module, "embedder", embedder)
    with pytest.raises(model_files.NotDownloaded):
        m.embedder("dinov3-small")
    there[0] = True  # Download published it
    assert m.embedder("dinov3-small") == "loaded", "the next call loads it: not held for 10 minutes"


# --- G7: "downloaded" is what the loader finds ----------------------------------------------------------------------------------
def test_files_split_across_two_old_caches_are_not_downloaded(home, monkeypatch):
    rev = "a" * 40
    weights, config = b"w" * 300, b'{"a": 1}'
    folder = home / "repo" / "models" / "split"
    folder.mkdir(parents=True)
    (folder / "model.toml").write_text(f'''id = "split"
name = "Split"
runs = "this-mac"
version = "org/split"
licence = ["edgetam"]
kind = "model"
what = "Outlines what you click"
runner = "gpu-worker"
source = {{ hf = "org/split", revision = "{rev}" }}
files = [
  {{ path = "config.json", bytes = {len(config)}, sha256 = "{sha(config)}" }},
  {{ path = "model.safetensors", bytes = {len(weights)}, sha256 = "{sha(weights)}" }},
]
needs_to_get = ["download"]
workers = ["analysis"]
documented_mb = 1
load_limit_s = 1
''')
    catalog.manifests.cache_clear()
    monkeypatch.delenv("MICROMIND_MODEL_FILES")
    one, two = home / "hf-one", home / "hf-two"
    monkeypatch.setenv("HF_HUB_CACHE", str(one))
    monkeypatch.setenv("HF_HOME", str(two.parent / "hf-home"))
    (two.parent / "hf-home" / "hub").mkdir(parents=True)
    two = two.parent / "hf-home" / "hub"
    for base, name, data in ((one, "config.json", config), (two, "model.safetensors", weights)):
        snap = base / "models--org--split" / "snapshots" / rev
        snap.mkdir(parents=True, exist_ok=True)
        (snap / name).write_bytes(data)
    m = catalog.get("split")
    assert model_files.location(m)[0] is None, "the loader reads one folder: split across two is not downloaded"
    with pytest.raises(model_files.NotDownloaded):
        model_files.model_dir("split")
    (one / "models--org--split" / "snapshots" / rev / "model.safetensors").write_bytes(weights)
    assert model_files.location(m) == ("old cache", 0) and model_files.model_dir("split").is_relative_to(one)


# --- G8: a published file whose content changed is not downloaded any more ---------------------------------------------------
def test_a_published_file_that_changed_is_found_said_and_downloaded_again(home):
    from orchestrator import catalog_view

    archive = _zip()
    archived_model(home / "repo" / "models", archive)
    serve = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=archive)))
    path = d.download(catalog.get("zipped"), client=serve)
    assert d.verify_published(catalog.get("zipped")) == ""
    target = path / "model.pt"
    target.chmod(0o644)
    data = bytearray(target.read_bytes())
    data[10] ^= 0xFF  # the same size: only its content says
    target.write_bytes(bytes(data))
    assert model_files.published(catalog.get("zipped")) is not None, "the size alone can't tell"
    assert "content differs" in d.verify_published(catalog.get("zipped"))
    assert model_files.published(catalog.get("zipped")) is None, "a changed revision is not downloaded"
    fact = next(f for f in catalog_view.facts(catalog.get("zipped"), {}, lambda k: False, []) if f["kind"] == "files")
    assert fact["ok"] is False and fact["says"].startswith("A file changed since it was downloaded")
    again = d.download(catalog.get("zipped"), client=serve)
    assert again == path and (again / "model.pt").read_bytes() == ZIP_FILES["bundle/model.pt"]
    assert d.verify_published(catalog.get("zipped")) == "" and model_files.published(catalog.get("zipped")) == path
    assert any(p.name.startswith(".incomplete-") for p in path.parent.iterdir()), "the changed one kept aside"


def test_checking_again_never_touches_the_live_view(home):
    """verify_all runs at background priority and reads only what was published."""
    archive = _zip()
    archived_model(home / "repo" / "models", archive)
    d.download(catalog.get("zipped"),
               client=httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=archive))))
    assert d.verify_all() == {"zipped": ""}
    assert json.loads(model_files.verification_path(catalog.get("zipped")).read_text())["ok"] is True


# --- G3: fine-tuned weights are kept in their catalog entry's folder ----------------------------------------------------------
def test_fine_tuned_versions_are_kept_in_the_catalog_and_old_ones_adopted(tmp_path, monkeypatch):
    from orchestrator import finetuned

    monkeypatch.delenv("MICROMIND_MODELS_DIR", raising=False)
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    assert finetuned.root() == tmp_path / "catalog" / "models", "beside the downloaded models' revisions"
    old = tmp_path / "assets" / "models"
    weights = tmp_path / "w.pt"
    weights.write_bytes(b"tuned" * 100)
    made = finetuned.write("cellpose-sam-tuned", weights, {"base": "cellpose-sam"}, old)
    assert finetuned.adopt_old(old, "cellpose-sam-tuned") == ["v1"]
    here = finetuned.current("cellpose-sam-tuned")
    assert here["folder"] == str(tmp_path / "catalog" / "models" / "cellpose-sam-tuned" / "v1")
    assert here["sha256"] == made["sha256"] and finetuned.verify(here)
    assert finetuned.adopt_old(old, "cellpose-sam-tuned") == [], "never written over"
    assert Path(made["folder"]).exists(), "the old one kept"


# --- G12: the proof is decisive -----------------------------------------------------------------------------------------------
def test_the_proofs_processes_reach_nothing_beyond_this_mac():
    import subprocess

    code = ("import os, socket; os.environ['MICROMIND_REFUSE_NETWORK'] = '1'; from gpu_worker.offline import refuse, "
            "NetworkRefused; refuse()\n"
            "try:\n    socket.create_connection(('93.184.216.34', 80), timeout=1); print('reached')\n"
            "except NetworkRefused: print('refused')\n"
            "try:\n    socket.getaddrinfo('huggingface.co', 443); print('resolved')\n"
            "except NetworkRefused: print('refused')\n"
            "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); c = socket.create_connection(s.getsockname()); "
            "print('local ok')")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=SERVICES).stdout.split()
    assert out == ["refused", "refused", "local", "ok"]


def test_a_model_the_proof_cannot_find_is_missing_and_the_proof_is_red(tmp_path, monkeypatch, capsys):
    from bench import catalog_proof

    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "nothing"))
    monkeypatch.setenv("BESIDE_LIVE", "1")
    monkeypatch.setattr(sys, "argv", ["catalog_proof", "--only", "dinov2-small"])
    assert catalog_proof.main() == 1, "a supported model that isn't here is red, never skipped"
    assert "missing   dinov2-small     not downloaded" in capsys.readouterr().out


def test_what_this_mac_itself_is_read_and_said_as_unavailable_here(monkeypatch):
    """`platform` and `memory` are read at every look (readiness.misfit), never only written down."""
    import dataclasses
    import platform as plat

    m = dataclasses.replace(catalog.get("fiji"), needs_to_run=("platform:macos-arm64",))
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(plat, "machine", lambda: "arm64")
    assert readiness.misfit(m) == ""
    monkeypatch.setattr(plat, "machine", lambda: "x86_64")
    assert readiness.misfit(m) == "it runs on a Mac with Apple Silicon (this one is macos-x86_64)"
    big = dataclasses.replace(m, needs_to_run=("memory:100000",))
    assert readiness.misfit(big).startswith("it needs 100000 GB of memory (this Mac has ")
    monkeypatch.setattr(catalog, "get", lambda key, *a, **k: big if key == "fiji" else None)
    r = readiness.model("fiji", {})
    assert r.status == "unavailable" and r.word == "Not on this Mac"
