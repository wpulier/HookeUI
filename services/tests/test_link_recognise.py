"""Add a capability from a link (orchestrator/link_recognise.py, mine.py; C3): a BioImage Model Zoo link becomes an entry
run by the reviewed template (catalog/templates/bioimageio: its adapter and lockfile copied byte for byte) on the model's
own files pinned by their hashes; what the template can't run is said with why and never runs. A stand-in zoo, and the
template's own picture-to-labels logic run here (numpy and scipy)."""
import hashlib
import importlib.util
import json

import numpy as np
import pytest
import yaml

from gpu_worker import manifests as catalog
from orchestrator import env_runner, link_recognise, mine, readiness

ZOO = link_recognise.ZOO
spec = importlib.util.spec_from_file_location("bioimageio_template", link_recognise.TEMPLATES / "bioimageio" / "adapter.py")
template = importlib.util.module_from_spec(spec)
spec.loader.exec_module(template)


def rdf(**over) -> dict:
    base = {"type": "model", "format_version": "0.5.3", "name": "Diatom counter", "description":
            "Outlines every diatom in a brightfield view of plankton, one by one.", "license": "MIT",
            "authors": [{"name": "Maya Okafor"}], "cite": [{"text": "paper", "doi": "10.1/x"}],
            "tags": ["diatoms", "plankton", "2d", "Not A Tag!"], "covers": ["cover.png"],
            "inputs": [{"id": "input0", "axes": [{"type": "batch"}, {"type": "channel", "channel_names": ["c0"]},
                                                 {"type": "space", "id": "y", "size": {"min": 64, "step": 16}},
                                                 {"type": "space", "id": "x", "size": {"min": 64, "step": 16}}],
                        "preprocessing": [{"id": "zero_mean_unit_variance", "kwargs": {"axes": ["channel", "y", "x"]}}],
                        "test_tensor": {"source": "test_input_0.npy", "sha256": "a" * 64}}],
            "outputs": [{"id": "output0", "axes": [{"type": "batch"}, {"type": "channel", "channel_names": ["fg", "bd"]},
                                                   {"type": "space", "id": "y"}, {"type": "space", "id": "x"}],
                         "test_tensor": {"source": "test_output_0.npy", "sha256": "b" * 64}}],
            "weights": {"torchscript": {"source": "weights.pt", "sha256": "c" * 64}}}
    base.update(over)
    return base


class Zoo:
    """httpx as the zoo answers it: GET by address, HEAD for sizes."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files

    class R:
        def __init__(self, status, content=b"", length=0):
            self.status_code, self.content, self.headers = status, content, {"content-length": str(length)}

    def get(self, url, **kw):
        return self.R(200, self.files[url]) if url in self.files else self.R(404)

    def head(self, url, **kw):
        return self.R(200, length=1234) if url.endswith((".pt", ".npy")) else self.R(404)


def zoo(description: dict, nick: str = "diatom-counter") -> Zoo:
    base = f"{ZOO}/{nick}/1.0/files"
    return Zoo({f"{ZOO}/{nick}/versions.json": json.dumps({"versions": [{"v": "1.0"}]}).encode(),
                f"{base}/rdf.yaml": yaml.safe_dump(description).encode(), f"{base}/cover.png": b"\x89PNG cover"})


@pytest.fixture
def mine_home(tmp_path, monkeypatch):
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    catalog.manifests.cache_clear()
    yield tmp_path
    catalog.manifests.cache_clear()


def test_a_zoo_link_is_an_entry_run_by_the_reviewed_template_on_its_own_pinned_files(mine_home, monkeypatch):
    monkeypatch.setattr(link_recognise, "_http", lambda: zoo(rdf()))
    assert link_recognise.zoo_id("https://bioimage.io/#/?id=diatom-counter") == "diatom-counter"
    assert link_recognise.zoo_id(f"{ZOO}/diatom-counter/1.0/files/rdf.yaml") == "diatom-counter"
    added = mine.add("https://bioimage.io/#/?id=diatom-counter")
    m = catalog.mine()[added["model"]]
    folder = catalog.entry_folder(m)
    for f in ("adapter.py", "env.lock"):
        assert (folder / f).read_bytes() == (link_recognise.TEMPLATES / "bioimageio" / f).read_bytes(), f
    assert {f["path"]: f.get("sha256") for f in m.files} == {
        "rdf.yaml": hashlib.sha256(yaml.safe_dump(rdf()).encode()).hexdigest(), "weights.pt": "c" * 64,
        "test_input_0.npy": "a" * 64, "test_output_0.npy": "b" * 64}
    assert (m.example, (folder / "example.png").read_bytes()) == ("example.png", b"\x89PNG cover")
    assert m.tags == ("diatoms", "plankton", "2d") and m.component["tier"] == "open" and m.licence == (m.id,)
    assert m.credit == {"authors": "Maya Okafor", "link": "https://bioimage.io/#/?id=diatom-counter",
                        "paper": "https://doi.org/10.1/x"}
    assert [s.kind for s in readiness.model(m.key, {}).steps] == ["install"], "(tests count files as downloaded)"
    fact = env_runner.adapter_fact(m)
    assert "reviewed bioimageio runner" in fact["says"] and "inside macOS's sandbox" in fact["says"]
    from orchestrator import sandbox

    assert sandbox.required(m), "the runner is reviewed, the model is its makers' code (TorchScript is code)"
    (folder / "adapter.py").write_text("print('mine now')\n")  # changed by its owner: no longer the reviewed runner
    assert link_recognise.reviewed_template(m) == ""
    assert "runs code you added" in env_runner.adapter_fact(m)["says"]


@pytest.mark.parametrize("over, kind, why", [
    ({"weights": {"tensorflow_saved_model_bundle": {"source": "tf.zip"}}}, "unsupported", "need TensorFlow"),
    ({"weights": {"pytorch_state_dict": {"source": "w.pt"}}}, "needs-code", "architecture code"),
    ({"type": "dataset"}, "unsupported", "not a model"),
])
def test_what_the_template_cant_run_is_said_and_never_runs(mine_home, monkeypatch, over, kind, why):
    monkeypatch.setattr(link_recognise, "_http", lambda: zoo(rdf(**over)))
    found = link_recognise.recognise("diatom-counter")
    assert found.kind == kind and why in found.why
    added = mine.add("diatom-counter")
    assert added["kind"] == "skeleton" and why in added["says"]


def test_a_3d_model_or_unknown_preprocessing_is_said(mine_home, monkeypatch):
    three_d = rdf()
    three_d["inputs"][0]["axes"].insert(2, {"type": "space", "id": "z"})
    monkeypatch.setattr(link_recognise, "_http", lambda: zoo(three_d))
    assert "3D model" in link_recognise.recognise("diatom-counter").why
    odd = rdf()
    odd["inputs"][0]["preprocessing"] = [{"id": "scale_mean_variance", "kwargs": {}}]
    monkeypatch.setattr(link_recognise, "_http", lambda: zoo(odd))
    assert "scale_mean_variance" in link_recognise.recognise("diatom-counter").why
    assert link_recognise.tier("CC-BY-NC-4.0") == "noncommercial" and link_recognise.tier("") == "noncommercial"


def test_the_template_turns_a_picture_into_the_models_input_and_its_output_into_labels():
    t = rdf()["inputs"][0]
    x, size = template.to_input(np.full((100, 70, 3), 7, np.uint8), t)
    assert x.shape == (1, 1, 112, 80) and size == (100, 70), "padded to min + k * step, grey for one channel"
    assert template.fit(64, 64, 16) == 64 and template.fit(65, 64, 16) == 80
    with pytest.raises(ValueError, match="at most"):
        template.fit(300, 256, 0)
    z = template.preprocess(np.arange(16, dtype=np.float32).reshape(1, 1, 4, 4), t)
    assert abs(float(z.mean())) < 1e-5 and abs(float(z.std()) - 1) < 1e-3
    fg = np.zeros((1, 2, 40, 40), np.float32)
    fg[0, 0, 5:15, 5:15] = fg[0, 0, 20:35, 20:35] = 1  # two things, the second with a boundary ring
    fg[0, 1, 20, 20:35] = 1
    labels = template.to_labels(fg, ["b", "c", "y", "x"], (40, 40), rdf()["outputs"][0])
    assert labels.shape == (40, 40) and labels.max() == 2 and labels[20, 25] != 0, "the boundary given back to its thing"
    one = template.to_labels(fg[:, :1], ["b", "c", "y", "x"], (30, 30), {"name": "foreground"})
    assert one.shape == (30, 30) and one.max() == 2
    ints = np.zeros((1, 1, 8, 8), np.int64)
    ints[0, 0, 2:4, 2:4] = 5
    assert template.to_labels(ints, ["b", "c", "y", "x"], (8, 8), {"name": "instance_labels"}).max() == 5
    with pytest.raises(ValueError, match="channels"):
        template.to_labels(np.zeros((1, 5, 8, 8), np.float32), ["b", "c", "y", "x"], (8, 8))


# --- Hugging Face transformers segmentation models (catalog/templates/hf-segmentation) ---------------------------------
HF = link_recognise.HF
SHA = "489d5cd81a0b59fab9b7ea758d3548ebe99677da"
CONFIG = {"architectures": ["SegformerForSemanticSegmentation"], "model_type": "segformer",
          "id2label": {"0": "background", "1": "diatom"}}


class Hub:
    """httpx as Hugging Face answers it: the model's API record, then its small files at the commit."""

    def __init__(self, info: dict, files: dict[str, bytes]):
        self.info, self.files, self.asked = info, files, []

    class R:
        def __init__(self, status, content=b"", record=None):
            content = json.dumps(record).encode() if record is not None else content  # as a server sends it
            self.status_code, self.content, self._record = status, content, record

        def json(self):
            return self._record

    def get(self, url, **kw):
        self.asked.append((url, kw.get("headers") or {}))
        if "/api/models/" in url:
            return self.R(200, record=self.info)
        return self.R(200, self.files[url]) if url in self.files else self.R(404)


def hub(over: dict | None = None, config: dict | None = None, siblings=None) -> Hub:
    info = {"id": "maya/diatom-segformer", "sha": SHA, "pipeline_tag": "image-segmentation",
            "library_name": "transformers", "config": {"model_type": (config or CONFIG)["model_type"]},
            "cardData": {"license": "apache-2.0"}, "tags": ["segformer", "diatoms", "license:apache-2.0"],
            "siblings": siblings if siblings is not None else [
                {"rfilename": "config.json"}, {"rfilename": "preprocessor_config.json"},
                {"rfilename": "model.safetensors", "lfs": {"sha256": "d" * 64, "size": 15_036_944}},
                {"rfilename": "pytorch_model.bin", "lfs": {"sha256": "e" * 64, "size": 15_092_257}}]}
    info.update(over or {})
    base = f"{HF}/maya/diatom-segformer/resolve/{SHA}"
    return Hub(info, {f"{base}/config.json": json.dumps(config or CONFIG).encode(),
                      f"{base}/preprocessor_config.json": b'{"image_processor_type": "SegformerImageProcessor"}'})


def test_a_hugging_face_segmentation_model_is_run_by_the_reviewed_template_at_its_commit(mine_home, monkeypatch):
    stand_in = hub()
    monkeypatch.setattr(link_recognise, "_http", lambda: stand_in)
    added = mine.add("https://huggingface.co/maya/diatom-segformer")
    m = catalog.mine()[added["model"]]
    folder = catalog.entry_folder(m)
    for f in ("adapter.py", "env.lock"):
        assert (folder / f).read_bytes() == (link_recognise.TEMPLATES / "hf-segmentation" / f).read_bytes(), f
    assert link_recognise.reviewed_template(m) == "hf-segmentation"
    assert m.source == {"hf": "maya/diatom-segformer", "revision": SHA}
    pinned = {f["path"]: f for f in m.files}
    assert set(pinned) == {"config.json", "preprocessor_config.json", "model.safetensors"}, "never the pickle weights"
    assert pinned["model.safetensors"]["sha256"] == "d" * 64
    assert pinned["config.json"]["sha256"] == hashlib.sha256(json.dumps(CONFIG).encode()).hexdigest()
    assert all(f"/resolve/{SHA}/" in f["url"] for f in m.files), "at its commit, never a branch"
    assert m.component["tier"] == "open" and "Hugging Face" in added["says"]
    from orchestrator import sandbox

    assert sandbox.required(m), "the runner is reviewed; the model is its makers'"


@pytest.mark.parametrize("over, config, siblings, kind, why", [
    ({}, None, [{"rfilename": "config.json"}, {"rfilename": "preprocessor_config.json"},
                {"rfilename": "pytorch_model.bin", "lfs": {"sha256": "e" * 64, "size": 1}}], "unsupported", "pickle"),
    ({}, {**CONFIG, "auto_map": {"AutoModel": "modeling.Mine"}}, None, "needs-code", "its makers' own code"),
    ({"config": {"model_type": "sam"}}, {**CONFIG, "model_type": "sam"}, None, "needs-code", "doesn't know yet"),
])
def test_what_the_transformers_template_cant_run_is_said_and_never_runs(mine_home, monkeypatch, over, config, siblings,
                                                                         kind, why):
    stand_in = hub(over, config, siblings)
    monkeypatch.setattr(link_recognise, "_http", lambda: stand_in)
    found = link_recognise.recognise("https://huggingface.co/maya/diatom-segformer")
    assert found.kind == kind and why in found.why, found.why
    added = mine.add("https://huggingface.co/maya/diatom-segformer")
    assert catalog.mine()[added["model"]].runner != "python-env" or not catalog.mine()[added["model"]].adapter, added


def test_any_other_hugging_face_link_stays_a_cellpose_model_file(monkeypatch):
    stand_in = hub({"pipeline_tag": None, "library_name": "cellpose"})
    found = link_recognise.recognise("https://huggingface.co/maya/diatom-segformer", http=stand_in)
    assert found.kind == "elsewhere"


def test_a_gated_model_is_read_with_the_users_hugging_face_key(monkeypatch):
    stand_in = hub()
    link_recognise.recognise("https://huggingface.co/maya/diatom-segformer", http=stand_in, token="hf_canary")
    assert all(h.get("Authorization") == "Bearer hf_canary" for _url, h in stand_in.asked)


COMMIT = "f0a392a0" + "1" * 32


@pytest.mark.parametrize("link, name", [
    (f"https://github.com/haesleinhuepf/napari-segment-blobs-and-things-with-membranes/tree/{COMMIT}",
     "napari-segment-blobs-and-things-with-membranes"),
    (f"https://raw.githubusercontent.com/maya/diatom-tools/{COMMIT}/src/threshold_methods.py", "diatom-tools"),
    ("https://raw.githubusercontent.com/maya/diatom-tools/refs/heads/main/threshold_methods.py", "diatom-tools"),
    (f"https://github.com/maya/diatom-tools/blob/{COMMIT}/threshold_methods.py", "diatom-tools"),
    ("https://github.com/maya/diatom-tools.git", "diatom-tools"),
    ("https://github.com/maya/diatom-tools", "diatom-tools"),
    (f"https://huggingface.co/maya/diatom-segformer/tree/{COMMIT}", "diatom-segformer"),
    (f"https://example.org/models/cell-finder/{COMMIT}", "cell-finder"),
])
def test_a_link_is_named_after_its_repository_never_a_commit_or_a_file(link, name):
    """Acceptance 2026-10-13: a skeleton of a repository at a commit was named by the commit, one of a raw file by
    the file."""
    assert link_recognise.link_name(link) == name


def test_a_github_links_source_keeps_the_commit_it_pins_and_never_a_branch():
    assert link_recognise.link_source(f"https://github.com/maya/diatom-tools/tree/{COMMIT}") == {
        "url": f"https://github.com/maya/diatom-tools/tree/{COMMIT}", "git": "https://github.com/maya/diatom-tools",
        "rev": COMMIT}
    raw = f"https://raw.githubusercontent.com/maya/diatom-tools/{COMMIT}/threshold_methods.py"
    assert link_recognise.link_source(raw)["rev"] == COMMIT
    assert "rev" not in link_recognise.link_source("https://github.com/maya/diatom-tools/tree/main"), "a branch moves"
    assert link_recognise.link_source("https://example.org/x") == {"url": "https://example.org/x"}
    found = link_recognise.recognise(raw, http=object())
    assert (found.kind, found.name) == ("needs-code", "diatom-tools")


def test_the_same_zoo_model_added_again_is_its_entry_not_a_second(mine_home, monkeypatch):
    monkeypatch.setattr(link_recognise, "_http", lambda: zoo(rdf()))
    first = mine.add("https://bioimage.io/#/?id=diatom-counter")
    again = mine.add("https://bioimage.io/#/?id=diatom-counter")
    assert again["model"] == first["model"] and again["already"] and again["kind"] == first["kind"]
    assert again["says"].startswith("Already added:") and "Nothing was added or downloaded again" in again["says"]
    assert again["says"].endswith("Next, install Diatom counter."), "the resolver's step, as its card says it"
    assert sorted(catalog.mine()) == [first["model"]], "no second entry (…-2)"


def test_added_again_with_another_template_version_is_a_new_entry_whose_files_are_reused(mine_home, monkeypatch):
    """Acceptance 2026-10-13: the same Hugging Face model added again downloaded its 339 MB again."""
    from orchestrator import downloads, own_trials
    from gpu_worker import model_files

    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(mine_home / "old"))  # published means published, here
    content = {"weights.pt": b"w" * 1234, "test_input_0.npy": b"a" * 1234, "test_output_0.npy": b"b" * 1234}
    description = rdf()
    description["weights"]["torchscript"]["sha256"] = hashlib.sha256(content["weights.pt"]).hexdigest()
    description["inputs"][0]["test_tensor"]["sha256"] = hashlib.sha256(content["test_input_0.npy"]).hexdigest()
    description["outputs"][0]["test_tensor"]["sha256"] = hashlib.sha256(content["test_output_0.npy"]).hexdigest()
    content["rdf.yaml"] = yaml.safe_dump(description).encode()
    monkeypatch.setattr(link_recognise, "_http", lambda: zoo(description))
    key = mine.add("diatom-counter")["model"]
    first = catalog.mine()[key]
    stage = downloads.staging(first)
    stage.mkdir(parents=True)
    for name, data in content.items():
        (stage / name).write_bytes(data)
    downloads.publish(first, "downloaded")
    # As if it had been made with an earlier version of the reviewed template.
    toml = catalog.entry_folder(first) / catalog.MANIFEST
    toml.write_text(toml.read_text().replace("bioimageio v", "bioimageio v0."))
    first = catalog.mine()[first.key]

    added = mine.add("diatom-counter")
    second = catalog.mine()[added["model"]]
    assert second.key != first.key and not added.get("already") and second.name == first.name
    assert "reused, not downloaded again" in added["says"]
    assert model_files.published(second) is not None, "its files are here without a download"
    assert model_files.revision_dir(second) != model_files.revision_dir(first)
    assert model_files.published(first) is not None, "the first entry's revision is only read"
    receipt = json.loads((model_files.revision_dir(second) / "receipt.json").read_text())
    assert receipt["how"].startswith(f"reused from {first.key}")
    assert own_trials.detail(first) != own_trials.detail(second), "the comparison list can tell them apart"
