"""A coder's commands stay in the handoff and return a fresh, untrusted package."""
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path
from types import SimpleNamespace

import pytest

from bench import capability_test
from gpu_worker import manifests as catalog
from orchestrator import capability_workspace as workspaces, contribute, share
from research_worker import prompts


@pytest.fixture
def item(tmp_path, monkeypatch):
    live = tmp_path / "live"
    live.mkdir()
    (live / "retained.txt").write_text("existing session and choices")
    monkeypatch.setenv("MICROMIND_CATALOG", str(live))
    return workspaces.create("Measure spots", environ={"MICROMIND_CATALOG": str(tmp_path / "private")})


def source():
    return catalog.ROOT / "spot-measure"


def derived(item):
    return contribute.derive(str(source()), item.candidate_id, workspace=item.folder)


def test_cli_scaffold_and_preflight_are_private_and_do_not_claim_completion(item):
    command = [sys.executable, "-m", "orchestrator.contribute"]
    created = subprocess.run([*command, "new", item.candidate_id, "--workspace", str(item.folder)], capture_output=True, text=True)
    assert created.returncode == 0, created.stderr
    checked = subprocess.run([*command, "preflight", item.candidate_id, "--workspace", str(item.folder)], capture_output=True, text=True)
    assert checked.returncode == 1 and "TODO" in checked.stdout
    live = Path(os.environ["MICROMIND_CATALOG"])
    assert list(live.iterdir()) == [live / "retained.txt"]
    assert not catalog.mine()


def test_cli_derives_packages_and_returns_new_identity_without_activation(item):
    assert contribute.main(["derive", str(source()), item.candidate_id, "--workspace", str(item.folder)]) == 0
    assert contribute.main(["preflight", item.candidate_id, "--workspace", str(item.folder)]) == 0
    assert contribute.main(["package", "--workspace", str(item.folder), "--by", "Test researcher"]) == 0
    path, = item.folder.glob("*.observationui.zip")
    shared, id_, files = share._read(path)
    assert id_ == item.candidate_id and shared["proof"] == {}
    assert "derived_from" in files["model.toml"].decode()
    assert not catalog.mine()


@pytest.mark.parametrize("command", ["new", "derive", "lock"])
def test_commands_cannot_target_an_existing_active_identity(item, command):
    argv = [command, "spot-measure"] if command != "derive" else [command, str(source()), "spot-measure"]
    assert contribute.main([*argv, "--workspace", str(item.folder)]) == 2
    assert not item.candidate.exists()


def test_workspace_scaffold_never_overwrites_a_candidate(item):
    folder = contribute.scaffold(item.candidate_id, workspace=item.folder)
    (folder / "adapter.py").write_text("retained edits")
    assert contribute.main(["new", item.candidate_id, "--workspace", str(item.folder)]) == 2
    assert (folder / "adapter.py").read_text() == "retained edits"


def test_derive_hash_covers_nested_owned_files_and_excludes_runtime_state(item, tmp_path):
    original = tmp_path / "source" / "spot-measure"
    shutil.copytree(source(), original)
    (original / "helpers").mkdir()
    (original / "helpers/measure.py").write_text("value = 3\n")
    for name in ("activation.json", "proof.json", ".secret"):
        (original / name).write_text("private runtime state")
    (original / "downloaded-revision").mkdir()
    (original / "downloaded-revision/weights.bin").write_bytes(b"weights")
    folder = contribute.derive(str(original), item.candidate_id, workspace=item.folder)
    manifest, problems = catalog.read(folder / "model.toml")
    assert manifest is not None and not problems
    assert (folder / "helpers/measure.py").read_text() == "value = 3\n"
    assert not any((folder / name).exists() for name in ("activation.json", "proof.json", ".secret", "downloaded-revision"))
    with workspaces.snapshot(original) as copied:
        digest = hashlib.sha256()
        for path in sorted(p for p in copied.rglob("*") if p.is_file()):
            digest.update(path.relative_to(copied).as_posix().encode() + b"\0" + path.read_bytes())
    assert manifest.derived_from["sha256"] == digest.hexdigest()


@pytest.mark.parametrize("bad", ["link", "fifo", "oversized", "directory-link"])
def test_derive_refuses_unbounded_or_redirected_owned_source(item, tmp_path, bad):
    original = tmp_path / "source" / "spot-measure"
    shutil.copytree(source(), original)
    path = original / "helper.py"
    if bad == "link":
        path.symlink_to(source() / "adapter.py")
    elif bad == "fifo":
        os.mkfifo(path)
    elif bad == "directory-link":
        path.symlink_to(source(), target_is_directory=True)
    else:
        path.write_bytes(b"x" * (share.MAX_FILE + 1))
    assert contribute.main(["derive", str(original), item.candidate_id, "--workspace", str(item.folder)]) == 2
    assert not item.candidate.exists()


def test_lock_only_replaces_the_private_lock_after_bounded_compilation(item, monkeypatch):
    folder = derived(item)
    (folder / "requirements.in").write_text("numpy==2.3.4\n")
    calls = []
    def compile_(args, **kwargs):
        calls.append(args)
        assert str(folder) not in args
        Path(args[-1]).write_text("numpy==2.3.4 --hash=sha256:" + "a" * 64 + "\n")
    monkeypatch.setattr(contribute.subprocess, "run", compile_)
    result = contribute.lock(item.candidate_id, workspace=item.folder)
    assert result == folder / "env.lock" and "sha256:" in result.read_text()
    assert "--only-binary" in calls[0] and "--generate-hashes" in calls[0]
    assert not catalog.mine()


def test_requirements_changed_during_lock_preserve_the_previous_lock(item, monkeypatch):
    folder = derived(item)
    (folder / "requirements.in").write_text("numpy==2.3.4\n")
    previous = (folder / "env.lock").read_bytes()
    def compile_(args, **kwargs):
        (folder / "requirements.in").write_text("numpy==2.3.5\n")
        Path(args[-1]).write_text("obsolete lock")
    monkeypatch.setattr(contribute.subprocess, "run", compile_)
    with pytest.raises(ValueError, match="requirements changed"):
        contribute.lock(item.candidate_id, workspace=item.folder)
    assert (folder / "env.lock").read_bytes() == previous


def test_capability_test_copies_only_the_candidate_to_a_private_catalog(item):
    folder = derived(item)
    (folder / "activation.json").write_text('{"active":true}')
    (folder / "proof.json").write_text('{"ok":true}')
    live = Path(os.environ["MICROMIND_CATALOG"])
    destination = item.folder / "checks" / "example" / "catalog"
    key = capability_test.setup(destination, item.candidate_id, workspace=item.folder)
    assert key == "mine/" + item.candidate_id
    copied = destination / "mine/models" / item.candidate_id
    assert (copied / "adapter.py").read_bytes() == (folder / "adapter.py").read_bytes()
    assert not (copied / "activation.json").exists() and not (copied / "proof.json").exists()
    assert os.environ["MICROMIND_CATALOG"] == str(destination)
    assert (live / "retained.txt").read_text() == "existing session and choices"


@pytest.mark.parametrize("destination", ["live", "source", "parent", "elsewhere"])
def test_test_catalog_cannot_replace_source_or_live_state(item, destination):
    derived(item)
    paths = {"live": Path(os.environ["MICROMIND_CATALOG"]), "source": item.candidate,
             "parent": item.folder, "elsewhere": item.folder.parent.parent / "elsewhere"}
    with pytest.raises(ValueError):
        capability_test.setup(paths[destination], item.candidate_id, workspace=item.folder)
    assert (item.candidate / "adapter.py").is_file()


def test_legacy_test_setup_refuses_source_destination_overlap(item):
    derived(item)
    with pytest.raises(ValueError, match="separate"):
        capability_test.setup(item.candidate, str(item.candidate))
    with pytest.raises(ValueError, match="separate"):
        capability_test.setup(Path(os.environ["MICROMIND_CATALOG"]), str(item.candidate))


def test_missing_skill_fails_only_adapter_build_and_preserves_other_research(tmp_path, monkeypatch):
    monkeypatch.setattr(prompts, "SKILL", tmp_path / "missing.md")
    assert prompts.deliverable_text("research")
    with pytest.raises(ValueError, match="capability skill"):
        prompts.deliverable_text("adapter")


@pytest.mark.parametrize("text", ["missing", "<!-- rules -->unfinished", "<!-- rules --><!-- /rules -->",
                                  "<!-- rules -->first<!-- /rules --><!-- rules -->second<!-- /rules -->"])
def test_incomplete_or_ambiguous_skill_rules_are_explicit_errors(tmp_path, text):
    path = tmp_path / "SKILL.md"
    path.write_text(text)
    with pytest.raises(ValueError, match="rules block"):
        prompts.skill_rules(path)


def test_contributed_fiji_test_uses_contribution_install(monkeypatch):
    from orchestrator import envs, fiji_generations, fiji_runner
    candidate = SimpleNamespace(runner="fiji", namespace="mine", key="mine/test", fiji={"command": "Blur"})
    installed = SimpleNamespace(n=7, proof={"summary": "confined parity passed", "cases": []})
    calls = []
    monkeypatch.setattr(fiji_generations, "current", lambda: 7)
    monkeypatch.setattr(envs, "install", lambda m: calls.append(m))
    monkeypatch.setattr(fiji_runner, "in_use", lambda m: installed)
    monkeypatch.setattr(fiji_generations, "proven", lambda key: "")
    monkeypatch.setattr(fiji_generations, "renew", lambda **kwargs: pytest.fail("must not modify reviewed generation"))
    report = []
    assert capability_test.install(candidate, report)
    assert calls == [candidate] and report[0]["ok"]


def test_packaging_cannot_silently_drop_nested_owned_code(item):
    folder = derived(item)
    (folder / "helpers").mkdir()
    (folder / "helpers/measure.py").write_text("value = 3\n")
    with pytest.raises(workspaces.WorkspaceError, match="would not travel"):
        workspaces.package(item.id, environ={"MICROMIND_CATALOG": str(item.folder.parent.parent)})
    assert not list(item.folder.glob("*.observationui.zip"))


def test_explicit_mine_source_uses_given_profile_even_when_reviewed_id_matches(item, tmp_path):
    profile = {"MICROMIND_CATALOG": str(tmp_path / "another-profile")}
    own = catalog.mine_root(profile) / "spot-measure"
    shutil.copytree(source(), own)
    assert contribute._folder_of("mine/spot-measure", profile) == own
    assert contribute._folder_of("spot-measure", profile) == source()


def test_fiji_examples_run_the_immutable_producer_snapshot(monkeypatch):
    from orchestrator import fiji_runner
    candidate = SimpleNamespace(runner="fiji", tests=(), outcomes={}, outputs=(SimpleNamespace(type="image.2d.rgb8"),))
    kept = object()
    monkeypatch.setattr(catalog, "params_given", lambda m, values: ({}, []))
    monkeypatch.setattr(fiji_runner, "in_use", lambda m: SimpleNamespace(m=kept, n=7))
    calls = []
    def run(m, pixels, values, *, n):
        calls.append((m, n))
        return {"image": pixels, "shape": list(pixels.shape)}
    monkeypatch.setattr(fiji_runner, "run_function", run)
    report = []
    capability_test.examples(candidate, report)
    assert calls == [(kept, 7)]
    assert report[0]["ok"] is True


def test_direct_handoff_command_runs_from_outside_the_repository(item, tmp_path):
    text = workspaces.instructions(item.id, {"MICROMIND_CATALOG": str(item.folder.parent.parent)})
    command = re.findall(r"```sh\n(.*?)\n```", text, re.DOTALL)[0]
    result = subprocess.run(["/bin/sh", "-c", command], cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (item.candidate / "model.toml").is_file()
    assert not catalog.mine()


def test_repeated_cli_checks_keep_separate_reports_and_source_edits(item, monkeypatch):
    folder = derived(item)
    before = (folder / "adapter.py").read_bytes()
    def installed(m, report):
        report.append({"layer": "1 contract", "ok": True, "says": "scripted install fixture"})
        return True
    monkeypatch.setattr(capability_test, "install", installed)
    for _ in range(2):
        assert capability_test.main([item.candidate_id, "--workspace", str(item.folder), "--layers", "1"]) == 0
    reports = list((item.folder / "checks").glob("*/*.json"))
    assert len(reports) == 2
    assert (folder / "adapter.py").read_bytes() == before


@pytest.mark.parametrize("field", ["env.lock", "example", "check", "fiji.script", "tests.input", "files.path"])
@pytest.mark.parametrize("reference", ["absolute", "parent"])
@pytest.mark.parametrize("action", ["preflight", "package", "test"])
def test_workspace_consumers_refuse_outside_manifest_references(item, tmp_path, monkeypatch, field, reference, action):
    folder = derived(item)
    outside = tmp_path / "outside.lock"
    outside.write_text("numpy==2.3.4 --hash=sha256:" + "a" * 64 + "\n")
    value = str(outside) if reference == "absolute" else "../outside.lock"
    manifest = folder / "model.toml"
    fields = tomllib.loads(manifest.read_text())
    if field == "env.lock":
        fields["env"]["lock"] = value
    elif field in ("example", "check"):
        fields[field] = value
    elif field == "fiji.script":
        fields["fiji"] = {"script": value}
    elif field == "tests.input":
        fields["tests"][0]["input"] = value
    else:
        fields["files"] = [{"path": value}]
    manifest.write_text(contribute._manifest_text(fields))
    original_read = Path.read_text
    def read(path, *args, **kwargs):
        if path == outside:
            pytest.fail("outside reference must never be read")
        return original_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    with pytest.raises(workspaces.WorkspaceError, match="capability-owned relative file"):
        if action == "preflight":
            contribute.preflight(item.candidate_id, workspace=item.folder)
        elif action == "package":
            workspaces.package(item.id, environ={"MICROMIND_CATALOG": str(item.folder.parent.parent)})
        else:
            capability_test.setup(item.folder / "checks/test/catalog", item.candidate_id, workspace=item.folder)
    assert not list(item.folder.glob("*.observationui.zip"))
    assert not list((item.folder / "checks").glob("*/catalog/mine/models/*"))


def test_derive_keeps_namespaced_source_identity_and_digest(item, tmp_path):
    profile = {"MICROMIND_CATALOG": str(tmp_path / "source-profile")}
    original = catalog.mine_root(profile) / "spot-measure"
    shutil.copytree(source(), original)
    folder = contribute.derive("mine/spot-measure", item.candidate_id, environ=profile, workspace=item.folder)
    manifest, problems = catalog.read(folder / "model.toml")
    assert manifest is not None and not problems
    assert manifest.derived_from["id"] == "mine/spot-measure"
    with workspaces.snapshot(original) as copied:
        digest = hashlib.sha256()
        for path in sorted(p for p in copied.rglob("*") if p.is_file()):
            digest.update(path.relative_to(copied).as_posix().encode() + b"\0" + path.read_bytes())
    assert manifest.derived_from["sha256"] == digest.hexdigest()


def test_snapshot_bounds_empty_directories_across_the_entire_tree(item, tmp_path):
    original = tmp_path / "source" / "spot-measure"
    shutil.copytree(source(), original)
    for i in range(16):
        for j in range(16):
            (original / f"group-{i}" / f"empty-{j}").mkdir(parents=True)
    with pytest.raises(workspaces.WorkspaceError, match="too many files or folders"):
        contribute.derive(str(original), item.candidate_id, workspace=item.folder)
    assert not item.candidate.exists()


def test_snapshot_enumeration_stops_at_the_entry_limit(item, monkeypatch):
    original = derived(item)
    actual = os.scandir
    visited = []
    class Entries:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass
        def __iter__(self):
            for i in range(workspaces.MAX_ENTRIES + 1):
                visited.append(i)
                yield SimpleNamespace(name=f"empty-{i}")
            pytest.fail("snapshot must stop enumerating at the entry limit")
    source_inode = original.stat().st_ino
    # Restore the process-wide scanner before pytest's optional immediate temp-directory cleanup.
    with monkeypatch.context() as patch:
        patch.setattr(workspaces.os, "scandir", lambda fd: Entries()
                      if isinstance(fd, int) and os.fstat(fd).st_ino == source_inode else actual(fd))
        with pytest.raises(workspaces.WorkspaceError, match="too many files or folders"):
            with workspaces.snapshot(original):
                pytest.fail("an over-limit tree cannot be accepted")
    assert len(visited) == workspaces.MAX_ENTRIES + 1


def test_snapshot_uses_the_manifest_whose_references_were_validated(item, monkeypatch):
    original = derived(item)
    manifest = original / "model.toml"
    before = manifest.read_bytes()
    read = workspaces.bounded_files.read_at
    def read_and_edit(fd, name, maximum):
        data = read(fd, name, maximum)
        if name == "model.toml":
            fields = tomllib.loads(before.decode())
            fields["env"]["lock"] = "/outside.lock"
            manifest.write_text(contribute._manifest_text(fields))
        return data
    monkeypatch.setattr(workspaces.bounded_files, "read_at", read_and_edit)
    with workspaces.snapshot(original) as copied:
        assert (copied / "model.toml").read_bytes() == before
    assert b'/outside.lock' in manifest.read_bytes()


@pytest.mark.parametrize("workspace", [True, False])
def test_derive_rewrites_imported_quoted_toml_semantically_and_keeps_owned_source(item, tmp_path, workspace):
    from orchestrator import capability_import
    original = tmp_path / "source" / "spot-measure"
    shutil.copytree(source(), original)
    path = original / "model.toml"
    fields = tomllib.loads(path.read_text())
    fields["derived_from"] = {"id": "author/earlier", "version": "7", "sha256": "a" * 64}
    fields["needs_to_get"] = ["install:maker/spot-measure", "key:huggingface"]
    fields["what"] = 'Leave the text install:spot-measure and id = "spot-measure" unchanged'
    fields["inputs"][1]["agent"] = False
    path.write_text("\n".join(capability_import._toml(k) + " = " + capability_import._toml(v)
                              for k, v in fields.items()))
    before = {p.name: p.read_bytes() for p in original.iterdir() if p.is_file()}
    profile = {"MICROMIND_CATALOG": str(tmp_path / "destination")}
    folder = contribute.derive(str(original), item.candidate_id, environ=profile,
                               **({"workspace": item.folder} if workspace else {}))
    result = tomllib.loads((folder / "model.toml").read_text())
    manifest, problems = catalog.read(folder / "model.toml")
    assert manifest is not None and not problems, problems
    assert result["id"] == item.candidate_id and result["version"] == "1"
    assert result["name"] == fields["name"] + " (your version)"
    assert result["derived_from"]["id"] == "spot-measure"
    assert result["derived_from"]["version"] == fields["version"]
    assert len(result["derived_from"]["sha256"]) == 64
    assert result["needs_to_get"] == ["install:mine/" + item.candidate_id, "key:huggingface"]
    assert result["needs_to_run"] == ["install:mine/" + item.candidate_id]
    assert result["licence"] == [item.candidate_id]
    for key in fields.keys() - {"id", "name", "version", "derived_from", "needs_to_get", "needs_to_run", "licence"}:
        assert result[key] == fields[key], key
    assert before == {p.name: p.read_bytes() for p in original.iterdir() if p.is_file()}
    for name, data in before.items():
        if name != "model.toml":
            assert (folder / name).read_bytes() == data
