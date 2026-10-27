"""Your own models (orchestrator/mine.py; P5 of the open catalog, ADR 0021): added by a path or a link, shown at once,
run only once turned on, and turned on only when their check passes in a process of its own. Each case in a temporary
catalog; the choices it registers are put back after it."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from gpu_worker import manifests as catalog
from gpu_worker import model_files
from orchestrator import capabilities, catalog_view, mine, names, operations, options, readiness

SERVICES = Path(__file__).resolve().parent.parent


@pytest.fixture
def home(tmp_path, monkeypatch):
    from orchestrator import link_recognise
    from tests.test_link_github import EmptyGitHub

    monkeypatch.setattr(link_recognise, "_http", EmptyGitHub)  # a GitHub link is read (from_github); never the real one
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "old"))  # this Mac's caches never count here
    before_options = dict(options.GROUPS["cells"])
    before_op = operations.OPERATIONS["cells"]
    yield tmp_path
    options.GROUPS["cells"].clear()
    options.GROUPS["cells"].update(before_options)
    operations.OPERATIONS["cells"] = before_op
    for key in [k for k in operations.ADAPTERS if k[1].startswith(mine.OPTION_PREFIX)]:
        operations.ADAPTERS.pop(key)
    names.index.cache_clear()


def a_file(tmp_path: Path, name: str = "my_cells.pt", size: int = 2_000_000) -> Path:
    path = tmp_path / "from" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(bytes(range(256)) * (size // 256))
    return path


def passing(*args, **kwargs):
    """The check's process, as a pass (its real runs are in the tests below and the live acceptance)."""
    return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": True, "steps": [{"says": "ok", "ok": True}],
                                                                   "cells": 64}), stderr="")


def row_of(key: str) -> dict:
    wire = capabilities.wire(options.Settings(), {})
    return next(r for r in catalog_view.rows(wire["roles"], {}, {}) if r["id"] == key)


def test_a_file_added_is_shown_at_once_and_runs_nothing_until_turned_on(home):
    added = mine.add(str(a_file(home)), "My cells")
    assert added["kind"] == "cellpose-file" and added["model"] == "mine/my-cells"
    m = catalog.mine()["mine/my-cells"]
    assert m.host == "own" and m.runner == "cellpose-file", "a worker of its own (ADR 0021)"
    folder = model_files.published(m)
    assert folder is not None and folder.parent == catalog.mine_root() / "my-cells", "its revision beside its entry"
    row = row_of("mine/my-cells")
    assert row["group"] == "available" and row["next"]["kind"] == "activate" and row["word"] == "Turn it on"
    assert any(f["kind"] == "on" and f["ok"] is False for f in row["facts"])
    assert not [k for k in options.GROUPS["cells"] if k.startswith(mine.OPTION_PREFIX)], "no choice before Turn on"
    assert mine.register() == {"added": [], "removed": [], "reset": False}


def test_turning_on_makes_it_a_choice_and_off_takes_it_away(home):
    key = mine.add(str(a_file(home)), "My cells")["model"]
    check = mine.activate(key, home / "frame.jpg", run=passing)
    assert check["ok"] and mine.state(catalog.mine()[key])["on"]
    settings = options.Settings()
    assert mine.register(settings=settings)["added"] == ["mine-my-cells"]
    assert options.GROUPS["cells"]["mine-my-cells"].label == "My cells"
    impl = operations.OPERATIONS["cells"].impl("mine-my-cells")
    assert impl is not None and impl.worker == "analysis" and impl.uses == (mine.COMPONENT,)
    assert readiness.option("cells", "mine-my-cells", keys={}).status == "ready"
    assert row_of(key)["group"] == "ready" and [u["title"] for u in row_of(key)["usedFor"]] == ["Cells"]
    settings.cells = "mine-my-cells"
    mine.deactivate(key)
    changed = mine.register(settings=settings)
    assert changed == {"added": [], "removed": ["mine-my-cells"], "reset": True}
    assert settings.cells == options.Settings().cells, "a setting that chose it goes back to the default"
    assert "mine-my-cells" not in options.GROUPS["cells"] and operations.OPERATIONS["cells"].impl("mine-my-cells") is None


def test_a_restart_brings_back_what_was_on(home):
    key = mine.add(str(a_file(home)), "My cells")["model"]
    mine.activate(key, home / "frame.jpg", run=passing)
    mine.register()
    options.GROUPS["cells"].pop("mine-my-cells")  # as a fresh process starts: nothing registered
    operations.OPERATIONS["cells"] = __import__("dataclasses").replace(
        operations.OPERATIONS["cells"], implementations=tuple(i for i in operations.OPERATIONS["cells"].implementations
                                                              if not i.id.startswith(mine.OPTION_PREFIX)))
    assert mine.register()["added"] == ["mine-my-cells"]


def test_a_check_that_fails_leaves_it_off(home):
    key = mine.add(str(a_file(home)), "My cells")["model"]

    def failing(*args, **kwargs):
        return subprocess.CompletedProcess(args, 0, stdout=json.dumps({"ok": False, "steps": [
            {"says": "It holds only weights (nothing that runs code to load)", "ok": True},
            {"says": "Not a Cellpose model this version can load: wrong", "ok": False}]}), stderr="")

    assert not mine.activate(key, home / "frame.jpg", run=failing)["ok"]
    assert not mine.state(catalog.mine()[key])["on"] and mine.register()["added"] == []
    facts = {f["kind"]: f for f in row_of(key)["facts"]}
    assert facts["check"]["ok"] is False and "Not a Cellpose model" in facts["check"]["says"]


def test_turning_on_runs_the_check_in_a_process_of_its_own_with_no_credentials(home, monkeypatch):
    key = mine.add(str(a_file(home)), "My cells")["model"]
    monkeypatch.setenv("GEMINI_API_KEY", "test-value")
    monkeypatch.setenv("HF_TOKEN", "test-token")
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, env=kwargs["env"])
        return passing(cmd)

    mine.activate(key, home / "frame.jpg", run=run)
    assert seen["cmd"][:3] == [sys.executable, "-m", "noncommercial.cellpose_file_check"]
    assert "GEMINI_API_KEY" not in seen["env"] and "HF_TOKEN" not in seen["env"]


def test_the_real_check_refuses_a_file_that_needs_code_without_running_it(home):
    torch = pytest.importorskip("torch")

    class Payload:
        def __reduce__(self):
            return (print, ("THE PAYLOAD RAN",))

    path = home / "needs_code.pt"
    torch.save({"model": Payload()}, path)
    done = subprocess.run([sys.executable, "-m", "noncommercial.cellpose_file_check", str(path), str(home / "none.jpg")],
                          capture_output=True, text=True, cwd=SERVICES, env={"PYTHONPATH": str(SERVICES), "PATH": "/usr/bin:/bin"})
    assert "THE PAYLOAD RAN" not in done.stdout + done.stderr
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["ok"] is False and out["steps"][0]["says"].startswith("It needs code to load")


def test_the_real_check_refuses_a_model_of_another_architecture(home):
    torch = pytest.importorskip("torch")
    pytest.importorskip("cellpose")
    path = home / "wrong.pt"
    torch.save({"net.weight": torch.zeros(4, 4)}, path)
    done = subprocess.run([sys.executable, "-m", "noncommercial.cellpose_file_check", str(path), str(home / "none.jpg")],
                          capture_output=True, text=True, cwd=SERVICES, env={"PYTHONPATH": str(SERVICES), "PATH": "/usr/bin:/bin"})
    out = json.loads(done.stdout.strip().splitlines()[-1])
    assert out["ok"] is False and [s["ok"] for s in out["steps"]] == [True, False]
    assert out["steps"][1]["says"].startswith("Not a Cellpose model this version can load")


def test_any_other_link_is_a_skeleton_that_can_never_run(home):
    added = mine.add("https://github.com/someone/their-segmenter", "Their segmenter")
    assert added["kind"] == "skeleton" and "needs an adapter" in added["says"]
    row = row_of(added["model"])
    assert row["status"] == "needs-setup" and row["next"]["says"] == "Write an adapter for it"
    assert any(f["kind"] == "runs" and f["ok"] is False for f in row["facts"])
    with pytest.raises(mine.CantDo, match="needs an adapter"):
        mine.activate(added["model"], home / "frame.jpg", run=passing)


def test_a_hugging_face_link_pins_its_one_file_at_its_revision(home, monkeypatch):
    import httpx

    listing = {"sha": "a" * 40, "cardData": {"license": "cc-by-nc-4.0"}, "siblings": [
        {"rfilename": "README.md", "size": 2000},
        {"rfilename": "weights_lc", "blobId": "b" * 40, "lfs": {"size": 1_218_648_575, "sha256": "c" * 64}}]}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=listing, request=httpx.Request("GET", url)))
    added = mine.add("https://huggingface.co/someone/lc-cells")
    m = catalog.mine()[added["model"]]
    assert m.source == {"hf": "someone/lc-cells", "revision": "a" * 40} and m.licence_note == "cc-by-nc-4.0"
    assert m.files == ({"path": "weights_lc", "bytes": 1_218_648_575, "sha256": "c" * 64},)
    assert row_of(m.key)["next"]["kind"] == "download", "a link is downloaded with Download, as any model is"
    listing["siblings"].append({"rfilename": "other_weights", "lfs": {"size": 900_000_000, "sha256": "d" * 64}})
    assert mine.add("https://huggingface.co/someone/two-models")["kind"] == "skeleton", "which file is the model? said"


def test_a_model_that_is_off_never_runs(home):
    import asyncio

    key = mine.add(str(a_file(home)), "My cells")["model"]
    mine.activate(key, home / "frame.jpg", run=passing)
    mine.register()
    mine.deactivate(key)
    impl = operations.Impl("mine-my-cells", "My cells", "model", ("field",), "analysis", "mine-my-cells", (mine.COMPONENT,))
    with pytest.raises(operations.Unavailable, match="not turned on"):
        asyncio.run(operations.ADAPTERS[("cells", "cellpose-file")](None, None, None, impl, {}))


def test_files_dropped_in_the_folder_are_offered(home):
    mine.root().mkdir(parents=True, exist_ok=True)
    (mine.root() / "lc_model").write_bytes(b"x" * 1_500_000)
    assert [f["name"] for f in mine.loose()] == ["lc_model"]
    assert mine.add(str(mine.root() / "lc_model"))["model"] == "mine/lc-model"


def test_a_broken_entry_says_what_is_wrong_and_is_never_offered(home):
    folder = mine.root() / "broken"
    folder.mkdir(parents=True)
    (folder / "model.toml").write_text('id = "broken"\nname = "Broken"\nruns = "somewhere"\n')
    assert "mine/broken" in {p["entry"] for p in catalog_view.problems()}
    assert "mine/broken" not in catalog.mine() and mine.register()["added"] == []


def test_reading_and_adding_load_no_model_library(tmp_path):
    code = (f"import sys, os; os.environ['MICROMIND_CATALOG'] = {str(tmp_path / 'c')!r}; "
            f"from pathlib import Path; p = Path({str(tmp_path / 'm.pt')!r}); p.write_bytes(b'x' * 2_000_000); "
            "from orchestrator import mine; mine.add(str(p), 'M'); mine.register(); "
            "print(sorted(m for m in ('torch', 'cellpose', 'transformers') if m in sys.modules))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=SERVICES, check=True)
    assert out.stdout.strip().splitlines()[-1] == "[]", out.stdout + out.stderr


COMMIT = "f0a392a0" + "1" * 32


def test_a_github_link_at_a_commit_is_named_after_its_repository_and_keeps_the_commit(home):
    """Acceptance 2026-10-13: a skeleton made from .../tree/<commit> was named by the commit's hash."""
    link = f"https://github.com/haesleinhuepf/napari-segment-blobs-and-things-with-membranes/tree/{COMMIT}"
    added = mine.add(link)
    m = catalog.mine()[added["model"]]
    assert m.name == "napari-segment-blobs-and-things-with-membranes" and "f0a392a0" not in m.id
    assert m.id == mine.slug("napari-segment-blobs-and-things-with-membranes")
    assert m.source == {"url": link, "git": "https://github.com/haesleinhuepf/napari-segment-blobs-and-things-with-membranes",
                        "rev": COMMIT}
    assert added["says"].startswith("Added napari-segment-blobs-and-things-with-membranes as a skeleton")


def test_a_raw_file_link_is_named_after_its_repository_not_the_file(home):
    """Acceptance 2026-10-13: a raw.githubusercontent.com link was named "threshold methods.py"."""
    added = mine.add(f"https://raw.githubusercontent.com/maya/diatom-tools/{COMMIT}/threshold_methods.py")
    m = catalog.mine()[added["model"]]
    assert (m.name, m.id, m.source["rev"]) == ("diatom-tools", "diatom-tools", COMMIT)


def test_the_same_link_added_again_is_said_and_never_a_second_entry(home):
    link = f"https://github.com/maya/diatom-tools/tree/{COMMIT}"
    first = mine.add(link)
    again = mine.add(link + "/")
    assert again["model"] == first["model"] and again["kind"] == "skeleton" and again["already"]
    assert again["says"] == ("Already added: diatom-tools is one of yours, from the same source at f0a392a. Nothing was "
                             "added or downloaded again. Next, write an adapter for it.")
    assert sorted(catalog.mine()) == [first["model"]]
    assert mine.add(f"https://github.com/maya/diatom-tools/tree/{'2' * 40}")["model"] != first["model"], \
        "another commit is another entry"


def test_the_same_file_or_hugging_face_file_added_again_is_its_entry(home, monkeypatch):
    import httpx

    path = a_file(home)
    first = mine.add(str(path), "My cells")
    again = mine.add(str(path))
    assert again["model"] == first["model"] and again["already"] and "Next, turn it on." in again["says"], "its card's step"
    listing = {"sha": "a" * 40, "cardData": {}, "siblings": [
        {"rfilename": "weights_lc", "blobId": "b" * 40, "lfs": {"size": 1_218_648_575, "sha256": "c" * 64}}]}
    monkeypatch.setattr(httpx, "get", lambda url, **kw: httpx.Response(200, json=listing, request=httpx.Request("GET", url)))
    hf = mine.add("https://huggingface.co/someone/lc-cells")
    hf_again = mine.add("https://huggingface.co/someone/lc-cells")
    assert hf_again["model"] == hf["model"] and hf_again["already"]
    assert len(catalog.mine()) == 2, "no …-2 entries"


def test_a_download_of_files_another_entry_already_has_reuses_them(home):
    """Two of your entries pinning exactly the same files (the same link made with two template versions): the second's
    download clones the first's published revision and checks it, never fetching anything."""
    from orchestrator import downloads

    path = a_file(home)
    sha = mine._sha256(path)
    files = [{"path": "weights.pt", "bytes": path.stat().st_size, "sha256": sha, "url": "https://example.org/w.pt"}]
    for id_ in ("cells-a", "cells-b"):
        mine.write_entry(mine.root() / id_, mine._cellpose_fields(id_, "Cells", files, {"url": "https://example.org/w.pt"},
                                                                   {}, "test"), mine.CELLS_IMPLEMENTS)
    a, b = catalog.mine()["mine/cells-a"], catalog.mine()["mine/cells-b"]
    assert downloads.twin(b) is None, "nothing is published yet"
    downloads.adopt_file(a, path)
    assert downloads.twin(b).key == a.key

    class NoNetwork:
        def __getattr__(self, name):
            raise AssertionError("nothing is fetched")

    where = downloads.download(b, client=NoNetwork())
    assert where == model_files.revision_dir(b) and model_files.published(b) is not None
    assert (where / "weights.pt").read_bytes() == path.read_bytes()
    assert json.loads((where / "receipt.json").read_text())["how"].startswith("reused from mine/cells-a")
    files[0]["sha256"] = "0" * 64
    mine.write_entry(mine.root() / "cells-c", mine._cellpose_fields("cells-c", "Cells", files, {"url": "https://example.org/w.pt"},
                                                                     {}, "test"), mine.CELLS_IMPLEMENTS)
    assert downloads.twin(catalog.mine()["mine/cells-c"]) is None, "other content is never a twin"


def _numbered(home, monkeypatch, installed_classes=None, edited_classes=None, installed=True, labels=None):
    """A Hugging Face entry whose config names its 40 classes only by number, with the generation that runs (its snapshot
    and pinned files) and the editable folder apart, as env_runner keeps them."""
    import dataclasses

    from orchestrator import env_runner

    mine.add(str(a_file(home)), "Reef")
    m = dataclasses.replace(catalog.mine()["mine/reef"], runner="python-env",
                            pins={"adapter": "catalog/templates/hf-segmentation v4 (reviewed)"})
    # Its pinned files: the installed generation's, or (nothing installed) its downloaded revision, read-only once published.
    files = home / "running-files" if installed else model_files.revision_dir(m)
    files.mkdir(parents=True, exist_ok=True)
    files.chmod(0o755)
    config = files / "config.json"
    if config.exists():
        config.chmod(0o644)
    config.write_text(json.dumps({"id2label": labels or {str(i): i for i in range(40)}}))
    generation = home / "generation"
    entry = generation / "snapshot" / m.id
    entry.mkdir(parents=True, exist_ok=True)
    if installed_classes is not None:
        (entry / "classes.json").write_text(installed_classes)
    if edited_classes is not None:
        (Path(m.folder) / "classes.json").write_text(edited_classes)
    running = env_runner.Installed(m, generation, {"ok": True}, True) if installed else None
    monkeypatch.setattr(env_runner, "in_use", lambda model, environ=None: running)
    monkeypatch.setattr(env_runner, "files_dir", lambda inst, environ=None: files)
    monkeypatch.setattr(env_runner.Installed, "entry", property(lambda self: entry))
    return m


def says(facts):
    return [(f["ok"], f["says"]) for f in facts]


def test_numbered_classes_are_said_of_the_generation_that_runs(home, monkeypatch):
    """Coralscapes names its 40 classes by number ("13": 13): with no classes.json in the generation that runs, every
    class counts as something found, and the card says so."""
    m = _numbered(home, monkeypatch)
    (fact,) = catalog_view.classes_fact(m)
    assert fact["ok"] is None and "40 classes are numbers only" in fact["says"] and "classes.json" in fact["says"]


def test_the_installed_classes_json_is_what_the_card_says_applies(home, monkeypatch):
    said = json.dumps({"notThings": [0, 13, 14], "source": "its dataset card"})
    m = _numbered(home, monkeypatch, installed_classes=said, edited_classes=said)
    assert says(catalog_view.classes_fact(m)) == [(True, "Classes it doesn't count as things: [0, 13, 14] (from its dataset card)")]


def test_an_edit_not_installed_is_said_apart_never_as_applied(home, monkeypatch):
    """Execution keeps the installed snapshot until the new version is installed and proven (or after Go back): an edited
    classes.json in the folder is said as not in use yet, and the running one is what applies."""
    m = _numbered(home, monkeypatch, edited_classes=json.dumps({"notThings": [13]}))
    first, edit = catalog_view.classes_fact(m)
    assert first["ok"] is None and "numbers only" in first["says"], "the generation that runs has none"
    assert edit["ok"] is None and "isn't in use yet: it would leave out [13]" in edit["says"] and "Install" in edit["says"]


def test_after_going_back_the_older_generations_mapping_is_said(home, monkeypatch):
    m = _numbered(home, monkeypatch, installed_classes=json.dumps({"notThings": [13]}),
                  edited_classes=json.dumps({"notThings": [0, 13, 14]}))
    running, edit = catalog_view.classes_fact(m)
    assert running == {**running, "ok": True, "says": "Classes it doesn't count as things: [13]"}
    assert "isn't in use yet: it would leave out [0, 13, 14]" in edit["says"]


@pytest.mark.parametrize("bad, why", [
    ('{"notThings": "13"}', "notThings must be a list of class numbers"),
    ('{"notThings": [true]}', "notThings must be a list of class numbers"),
    ('{"notThings": [52]}', "classes the model doesn't have: [52]"),
    ("not json", "can't be read"),
])
def test_a_classes_json_the_adapter_would_refuse_is_never_endorsed(home, monkeypatch, bad, why):
    m = _numbered(home, monkeypatch, installed_classes=bad)
    (fact,) = catalog_view.classes_fact(m)
    assert fact["ok"] is False and why in fact["says"]
    m = _numbered(home, monkeypatch, edited_classes=bad, installed=False)
    assert [f["ok"] for f in catalog_view.classes_fact(m)] == [None, False], "an uninstalled edit is said, with why"


def test_a_model_that_names_its_classes_or_isnt_from_the_template_says_nothing(home, monkeypatch):
    import dataclasses

    m = _numbered(home, monkeypatch, labels={"0": "background", "1": "coral"})
    assert catalog_view.classes_fact(m) == []
    assert catalog_view.classes_fact(dataclasses.replace(m, pins={})) == []



def test_a_mapping_file_that_cant_be_read_is_contained_and_the_other_facts_stay(home, monkeypatch):
    """A classes.json removed while the page is built, or unreadable, never breaks the catalog's answer: the running
    generation is said without it, and the facts that don't depend on it stay (Codex's follow-up, P2)."""
    said = json.dumps({"notThings": [13], "source": "its dataset card"})
    m = _numbered(home, monkeypatch, installed_classes=said, edited_classes=json.dumps({"notThings": [0, 13, 14]}))
    real = Path.read_bytes

    def flaky(path):
        if path.name == "classes.json":
            raise PermissionError(13, "Permission denied", str(path))
        return real(path)

    monkeypatch.setattr(Path, "read_bytes", flaky)
    running, edit = catalog_view.classes_fact(m)
    assert running["ok"] is False and "installed classes.json can't be read (Permission denied)" in running["says"]
    assert edit["ok"] is False and "in your folder can't be read (Permission denied)" in edit["says"]
    kinds = [f["kind"] for f in catalog_view.facts(m, {}, lambda key: True, [])]
    assert "entry" in kinds and kinds.count("classes") == 2, "its card's other facts stay beside them"
    monkeypatch.setattr(Path, "read_bytes", real)
    (Path(m.folder) / "classes.json").unlink()  # removed between listing and reading: the edit is simply not there
    assert says(catalog_view.classes_fact(m)) == [(True, "Classes it doesn't count as things: [13] (from its dataset card)")]


# --- GitHub links (link_recognise.from_github; ticket 62 row 7) -------------------------------------------------------
def test_a_github_branch_link_is_a_skeleton_pinned_at_the_commit_it_was_read_at_with_why(home):
    from tests.test_link_github import SHA

    added = mine.add("https://github.com/maya/diatom-tools")
    m = catalog.mine()[added["model"]]
    assert m.source == {"url": "https://github.com/maya/diatom-tools", "git": "https://github.com/maya/diatom-tools",
                        "rev": SHA} and m.version == f"commit {SHA[:12]}", "a branch moves: the commit read is kept"
    assert added["kind"] == "skeleton" and "no bioimage.io model description" in added["why"]
    assert added["why"] in added["says"] and not m.runner, "listed, never runnable (ADR 0021)"
    again = mine.add("https://github.com/maya/diatom-tools")
    assert again["already"] and again["model"] == added["model"] and again["why"] == added["why"]


def test_an_issue_or_pull_request_link_adds_nothing_and_says_what_to_paste(home):
    for link, what in (("https://github.com/maya/diatom-tools/issues/12", "an issue"),
                       ("https://github.com/maya/diatom-tools/pull/3", "a pull request")):
        with pytest.raises(mine.CantDo, match=f"this link is {what} of maya/diatom-tools.*https://github.com/maya/diatom-tools itself"):
            mine.add(link)
    assert catalog.mine() == {}


def test_a_link_already_added_and_any_other_link_are_as_before(home, monkeypatch):
    from orchestrator import link_recognise

    class Unread:
        def get(self, *a, **kw):
            raise AssertionError("a link that is not GitHub's is not read")

    monkeypatch.setattr(link_recognise, "_http", Unread)
    added = mine.add("https://example.org/models/cell-finder")
    assert added == {"model": "mine/cell-finder", "kind": "skeleton", "says": (
        "Added cell-finder as a skeleton: the app can not run it as it is. It needs an adapter written for it before it can "
        "run (catalog/README.md, “Add your own”).")}
    assert catalog.mine()["mine/cell-finder"].source == {"url": "https://example.org/models/cell-finder"}
    assert mine.add("https://example.org/models/cell-finder")["already"]


@pytest.mark.parametrize("turning", ["on", "off"])
def test_the_catalogue_read_on_the_loop_survives_a_thread_changing_cells(home, monkeypatch, turning):
    """Turning one of yours on or off registers it on a worker thread (App.sync_mine) while the loop builds the
    catalogue: the group being read is swapped, never changed in place (CI 2026-10-21: "dictionary changed size during
    iteration"). The writer runs, whole, in the middle of the catalogue's iteration of Cells: no sleep, no chance."""
    import threading

    key = mine.add(str(a_file(home)), "My cells")["model"]
    mine.activate(key, home / "frame.jpg", run=passing)
    if turning == "off":
        mine.register()
        mine.deactivate(key)
    actual, ran = readiness.option, []

    def option(group, option_id, *args, **kwargs):
        if group == "cells" and not ran:
            ran.append(threading.Thread(target=mine.register))  # another thread, as App.sync_mine's to_thread
            ran[0].start()
            ran[0].join()
        return actual(group, option_id, *args, **kwargs)

    monkeypatch.setattr(readiness, "option", option)
    during = options.catalogue({})
    assert ran, "the writer ran inside the iteration"
    after = options.catalogue({})
    ours = lambda c: [o["id"] for o in c["cells"] if o["id"].startswith(mine.OPTION_PREFIX)]  # noqa: E731
    assert ours(during) == ([] if turning == "on" else ["mine-my-cells"]), "the group as it was when read"
    assert ours(after) == (["mine-my-cells"] if turning == "on" else []), "the next read sees the change"
    assert options.CELLS is options.GROUPS["cells"], "the name Cells was defined under follows the swap"


def test_two_writers_at_once_lose_neither_change():
    """kinds.register and mine.register may change one group at the same time: each change is applied to the other's."""
    import threading

    before = options.GROUPS["cells"]
    barrier = threading.Barrier(2)

    def put(oid):
        barrier.wait()
        options.change_choices("cells", put={oid: options.Option(oid, oid, "test")})

    try:
        threads = [threading.Thread(target=put, args=(oid,)) for oid in ("test-a", "test-b")]
        [t.start() for t in threads]
        [t.join() for t in threads]
        assert {"test-a", "test-b"} <= set(options.GROUPS["cells"])
        assert set(before) == set(options.GROUPS["cells"]) - {"test-a", "test-b"}, "the group read before is unchanged"
    finally:
        options.change_choices("cells", drop=("test-a", "test-b"))


def test_registering_entries_on_the_loop_survives_a_thread_turning_yours_on(home, monkeypatch):
    """kinds.register (on the loop: App.catalog_changed) reads every adapter while App.sync_mine's thread registers one
    of yours, which adds its adapter: the adapters being read are swapped, never changed in place (review of #413,
    2026-10-21: "dictionary changed size during iteration"). The writer runs, whole, in the middle of the iteration."""
    import threading

    from orchestrator import kinds

    key = mine.add(str(a_file(home)), "My cells")["model"]
    mine.activate(key, home / "frame.jpg", run=passing)
    ran = []

    class Hook:
        """An adapter whose hash kinds.register takes while it iterates the adapters (`run in adapters`)."""

        def __hash__(self):
            if not ran and threading.current_thread() is threading.main_thread():
                ran.append(threading.Thread(target=mine.register))  # another thread, as App.sync_mine's to_thread
                ran[0].start()
                ran[0].join()
            return id(self)

        def __eq__(self, other):
            return self is other

    monkeypatch.setitem(operations.ADAPTERS, ("outline", "test-hook"), Hook())
    kinds.register()
    assert ran, "the writer ran inside the iteration"
    assert ("cells", "mine-my-cells") in operations.ADAPTERS and operations.OPERATIONS["cells"].impl("mine-my-cells")
