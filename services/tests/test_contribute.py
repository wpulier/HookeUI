"""Adding a capability, for people and AI coders (orchestrator/contribute.py; the skill `add-a-capability`): the preflight
names what stands in the way before anything is installed; a scaffold is a folder the preflight refuses until filled in;
a derived version names the one it came from, under an id of its own; and a derived version shared back as a file opens
on another Mac, is allowed, installed and run there, beside its parent, each giving its own result. A stand-in uv in a
temporary catalog; the real ones are make capability-test."""
import shutil
import time

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, contribute, env_runner, envs, kinds, mine, operations, share
from tests.test_env_runner import home, standin_uv  # noqa: F401 - a scratch catalog (the repo's entries copied in)


def blocks(source) -> list[str]:
    return [f.says for f in contribute.preflight(str(source)) if f.blocks]


def test_the_examples_in_the_catalog_have_nothing_in_their_way(home):
    for key in ("spot-measure", "spot-shape", "swin2sr-enlarge", "fiji-gaussian-blur"):
        assert blocks(key) == [], key


def test_a_scaffold_is_refused_until_its_todos_are_filled_in(home):
    folder = contribute.scaffold("my-measure", output="table.objects")
    assert folder == catalog.mine_root() / "my-measure"
    found = blocks(folder)
    assert any("still says TODO" in b for b in found), found
    fiji = contribute.scaffold("my-filter", runner="fiji", output="image.2d.rgb8")
    assert any("still says TODO" in b for b in blocks(fiji))
    with pytest.raises(ValueError, match="already exists"):
        contribute.scaffold("my-measure")
    for made in (folder, fiji):  # every TODO the scaffold writes is a placeholder the preflight knows
        for f in made.iterdir():
            text = f.read_text()
            for placeholder in contribute.PLACEHOLDERS:
                text = text.replace(placeholder, "")
            assert "TODO" not in text, (f.name, text)


@pytest.mark.parametrize("lock, code, said", [
    ("nvidia-cudnn-cu12==9.1.0.70 \\\n    --hash=sha256:" + "a" * 64, "", "runs only with NVIDIA's CUDA"),
    ("numpy==2.3.4", "", "needs its hash"),
    ("", "x = model.cuda()\n", "puts work on CUDA"),
    ("", "import requests\nrequests.get('https://example.org/w.pt')\n", "fetches from the network"),
])
def test_the_preflight_names_what_a_mac_cant_run_before_anything_installs(home, lock, code, said):
    folder = catalog.ROOT / "spot-measure"
    if lock:
        (folder / "env.lock").write_text(lock + "\n")
    if code:
        (folder / "adapter.py").write_text((folder / "adapter.py").read_text() + "\n" + code)
    assert any(said in b for b in blocks(folder)), blocks(folder)


def test_a_link_says_its_route(home, monkeypatch):
    from orchestrator import link_recognise

    monkeypatch.setattr(link_recognise, "recognise", lambda url: link_recognise.Found("needs-code", link=url))
    (note,) = contribute.preflight("https://github.com/someone/model")
    assert not note.blocks and "make capability-new" in note.says


def test_a_derived_version_names_its_parent_under_an_id_of_its_own(home):
    folder = contribute.derive("spot-measure", "my-spots")
    m, problems = catalog.read(folder / catalog.MANIFEST)
    assert problems == [] and m.id == "my-spots" and m.name == "Spot measure (your version)"
    assert m.derived_from["id"] == "spot-measure" and m.derived_from["version"] == "1"
    assert len(m.derived_from["sha256"]) == 64 and m.licence == ("my-spots",) and m.component
    assert (catalog.ROOT / "spot-measure" / "model.toml").read_text().count("derived_from") == 0, "its parent unchanged"
    with pytest.raises(ValueError, match="an id of its own"):
        contribute.derive("spot-measure", "spot-measure")


def test_a_derived_version_copies_only_the_entrys_own_files(home):
    """Never a weights revision folder kept beside its manifest, this Mac's state there (activation.json) or caches:
    envs.entry_files' rule, the same files its digest and its share read."""
    parent = catalog.ROOT / "spot-measure"
    m, _ = catalog.read(parent / catalog.MANIFEST)
    own = sorted(str(f) for f in envs.entry_files(m))
    revision = parent / catalog.revision_of(m)
    revision.mkdir()
    (revision / "weights.pt").write_bytes(b"w" * 64)
    (parent / "activation.json").write_text('{"on": true}')
    (parent / "__pycache__").mkdir()
    (parent / "__pycache__" / "adapter.cpython-313.pyc").write_bytes(b"pyc")
    (parent / ".DS_Store").write_bytes(b"ds")
    folder = contribute.derive("spot-measure", "my-spots")
    assert sorted(str(f.relative_to(folder)) for f in folder.rglob("*") if f.is_file()) == own
    assert not (folder / revision.name).exists() and not (folder / "activation.json").exists()


def install(key: str) -> dict:
    queue = envs.Installs()
    queue.start(key)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline and queue.wire()[0]["state"] not in ("done", "failed"):
        time.sleep(0.05)
    return queue.wire()[0]


@pytest.fixture
def clean_kinds():
    yield
    for op in list(kinds.registered()):
        operations.OPERATIONS.pop(op, None)
    kinds.registered().clear()


def test_a_derived_version_shared_back_runs_beside_its_parent_on_another_mac(home, monkeypatch, tmp_path, clean_kinds):
    standin_uv(home, monkeypatch)
    folder = contribute.derive("spot-measure", "big-spots")
    adapter = folder / "adapter.py"  # B's substantive change: a column of its own, and only big spots
    adapter.write_text(adapter.read_text().replace(
        'columns = [{"name": "Spot"}', 'columns = [{"name": "Big", "definition": "area over 100 px"}, {"name": "Spot"}', 1)
        .replace("row = [i, area,", "row = [area > 100, i, area,", 1))
    catalog.manifests.cache_clear()
    path = share.export("mine/big-spots", by="Researcher B")

    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "researcher-a"))  # A opens it on their own Mac
    catalog.manifests.cache_clear()
    opened = mine.add(str(path))
    shared = catalog.get(opened["model"])
    assert shared is not None, (opened, catalog.problems())
    assert shared.derived_from["id"] == "spot-measure" and shared.contributed_by == {"name": "Researcher B"}
    channels.trust(opened["channel"])
    row = install(shared.key)
    assert row["state"] == "done", {k: v for k, v in row.items() if k != "by"}
    row = install("spot-measure")
    assert row["state"] == "done", row
    env_runner.register()
    picture = np.full((96, 96, 3), 225, np.uint8)
    picture[10:30, 10:30] = 40
    picture[50:54, 50:54] = 40
    values = {"threshold": 100, "min_area": 5}
    theirs = env_runner.client(env_runner.in_use(shared)).run(picture, values, output="table.objects")
    parent = env_runner.client(env_runner.in_use(catalog.get("spot-measure"))).run(picture, values, output="table.objects")
    assert [c["name"] for c in theirs["columns"]][:2] == ["Big", "Spot"] and [r[0] for r in theirs["rows"]] == [True, False]
    assert [c["name"] for c in parent["columns"]][0] == "Spot" and len(parent["rows"]) == 2, "its parent unchanged"
    on = {k["key"] for k in kinds.available()}
    assert {shared.key, "spot-measure"} <= on, "both are in the one registry, side by side"
    env_runner.stop()


def test_one_skill_for_every_ai_coder_and_the_in_app_builder():
    """The skill is where each agent looks for it (Codex: .agents/skills, Claude Code: .claude/skills), the same file,
    and the in-app builder is held to its rules, read from it."""
    from pathlib import Path

    from research_worker.prompts import deliverable_text, skill_rules

    repo = Path(__file__).resolve().parents[2]
    codex, claude = (repo / d / "skills" / "add-a-capability" / "SKILL.md" for d in (".agents", ".claude"))
    assert codex.read_text() == claude.read_text(), "copy .agents/skills/add-a-capability/SKILL.md to .claude/skills/"
    text = codex.read_text()
    assert text.startswith("---\nname: add-a-capability\ndescription: ")
    rules = skill_rules()
    assert rules and all(line in deliverable_text("adapter") for line in rules.splitlines())
    makefile = (repo / "Makefile").read_text()
    for target in ("capability-preflight", "capability-new", "capability-lock", "capability-derive", "capability-test",
                   "catalog-pin", "catalog-pr"):
        assert f"{target}:" in makefile and f"make {target}" in text, target
    for example in ("spot-measure", "swin2sr-enlarge", "fiji-gaussian-blur", "spot-shape"):
        assert f"catalog/models/{example}" in text and (repo / "catalog" / "models" / example / "model.toml").is_file()
