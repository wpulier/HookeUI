"""Share a capability as a file, with no account (orchestrator/share.py; C5): an entry with its own adapter travels as one
small file (its manifest, adapter, lockfile and picture, never weights); opened on another Mac it is a community catalog
of its own, pinned at a commit, its code allowed once by the name of who shared it, then installed and proven as any
entry. Anything a catalog entry doesn't hold refuses the whole file."""
import io
import json
import tomllib
from pathlib import Path
import zipfile

import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, mine, readiness, share
from tests.test_catalog_news import PNG
from tests.test_channels import home  # noqa: F401 - a scratch catalog
from tests.test_env_runner import ADAPTER, ENTRY


def yours(home, files_line: str = "") -> str:
    folder = catalog.mine_root() / "dark-spots"
    folder.mkdir(parents=True)
    text = ENTRY.format().replace("install:dark-spots", "install:mine/dark-spots")
    (folder / "model.toml").write_text(text.replace("[[implements]]", f'example = "example.png"\n{files_line}\n[[implements]]', 1))
    (folder / "env.lock").write_text("numpy==2.0.0 --hash=sha256:" + "a" * 64 + "\n")
    (folder / "adapter.py").write_text(ADAPTER)
    (folder / "example.png").write_bytes(PNG)
    (folder / "activation.json").write_text("{}")  # this Mac's own record: never shared
    catalog.manifests.cache_clear()
    assert catalog.get("mine/dark-spots") is not None, catalog.problems()
    return "mine/dark-spots"


def test_a_capability_travels_as_one_small_file_and_opens_as_a_community_catalog_of_its_own(home, monkeypatch, tmp_path):
    path = share.export(yours(home), by="Maya Okafor")
    with zipfile.ZipFile(path) as z:
        names = sorted(z.namelist())
        manifest = z.read("catalog/models/dark-spots/model.toml").decode()
        shared = json.loads(z.read("SHARED.json"))
    assert names == ["SHARED.json", "catalog/models/dark-spots/adapter.py", "catalog/models/dark-spots/env.lock",
                     "catalog/models/dark-spots/example.png", "catalog/models/dark-spots/model.toml"]
    assert 'contributed_by = { name = "Maya Okafor" }' in manifest and shared["by"] == "Maya Okafor"

    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "another-mac"))  # opened on another Mac
    catalog.manifests.cache_clear()
    opened = mine.add(str(path))
    assert opened["kind"] == "shared" and opened["model"].endswith("/dark-spots") and "Maya Okafor" in opened["says"]
    m = catalog.get(opened["model"])
    assert m is not None and m.channel == opened["channel"] and m.contributed_by == {"name": "Maya Okafor"}
    step = readiness.model(m.key, {}).steps[0]
    assert (step.kind, step.says) == ("trust", "Allow code from Maya Okafor's shared file"), "nothing runs before Allow"
    channels.trust(opened["channel"])
    assert [s.kind for s in readiness.model(m.key, {}).steps] == ["install"], "then its own Install and proof"
    assert mine.add(str(path))["says"].endswith("from a file shared earlier")


def test_a_shared_file_is_named_for_hookeui_and_one_named_before_the_rename_still_opens(home, monkeypatch, tmp_path):
    path = share.export(yours(home), by="Maya Okafor")
    assert path.name == "dark-spots.hookeui.zip"
    older = tmp_path / "dark-spots.observationui.zip"  # sent by an app from before the rename
    older.write_bytes(path.read_bytes())
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "another-mac"))
    catalog.manifests.cache_clear()
    opened = mine.add(str(older))
    assert opened["kind"] == "shared" and opened["model"].endswith("/dark-spots") and "Maya Okafor" in opened["says"]


def test_a_shared_file_is_named_as_one_wherever_it_is_named(home, monkeypatch, tmp_path):
    # Thesis check, ticket 67: its card said "community: shared-1a2b3c…". The row now carries the channel's source and
    # label, and every server sentence names it by one helper (channels.said).
    from orchestrator import catalog_view, env_runner, envs

    path = share.export(yours(home), by="Maya Okafor")
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "another-mac"))
    catalog.manifests.cache_clear()
    opened = mine.add(str(path))
    m = catalog.get(opened["model"])
    assert channels.source(m.channel) == "shared-file" and channels.said(m.channel) == "Maya Okafor's shared file"
    row = next(r for r in catalog_view.rows({}, {}, {}) if r["id"] == m.key)
    assert (row["channel"], row["channelSource"], row["channelLabel"]) == (m.channel, "shared-file", "Maya Okafor's shared file")
    entry = next(f for f in row["facts"] if f["kind"] == "entry")
    assert entry["says"] == "From Maya Okafor's shared file: not reviewed here", "no content hash for a person to read"
    assert env_runner.ready(m) == "code from Maya Okafor's shared file isn't allowed here (it isn't reviewed here)"
    with pytest.raises(envs.Failed) as refused:
        envs.install(m)
    assert str(refused.value).startswith("Dark spots comes from Maya Okafor's shared file, not reviewed here")
    assert m.channel not in str(refused.value)
    how = readiness.model(m.key, {}).steps[0].how
    assert "Maya Okafor's shared file" in how and m.channel not in how
    from orchestrator import report

    assert report.facts(m.key)["from"] == "Maya Okafor's shared file" and report.where(m) == ""


def test_a_community_repository_is_still_named_as_a_channel(home):
    from orchestrator import catalog_view

    found = catalog.channels()
    found.append({"name": "lab-models", "url": "https://github.com/lab/models", "commit": "9" * 40, "reviewed": False,
                  "trusted": False})
    channels._save(found, __import__("os").environ)
    assert channels.source("lab-models") == "git" and channels.said("lab-models") == "the community channel lab-models"
    found[-1]["label"] = "Lab models"
    channels._save(found, __import__("os").environ)
    assert channels.said("lab-models") == "Lab models"
    assert channels.source("reviewed") in ("reviewed", "") and channels.said("nowhere") == "the community channel nowhere"
    assert all("channelSource" not in r for r in catalog_view.rows({}, {}, {}) if not r.get("channel"))


def test_weights_only_on_this_mac_never_travel(home):
    files = 'files = [{ path = "w.pt", bytes = 10, sha256 = "' + "a" * 64 + '", url = "file:///Users/me/w.pt" }]'
    key = yours(home, files)
    with pytest.raises(share.CantShare, match="only on this Mac"):
        share.export(key)


def test_opening_a_shared_package_needs_no_git_or_other_external_program(home, monkeypatch, tmp_path):
    import subprocess
    path = share.export(yours(home), by='Maya')
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'without-developer-tools'))
    catalog.manifests.cache_clear()
    def unavailable(*args, **kwargs):
        raise FileNotFoundError('No developer tools on this computer')
    monkeypatch.setattr(subprocess, 'run', unavailable)
    opened = mine.add(str(path))
    model = catalog.get(opened['model'])
    assert model is not None and not channels.trusted(model)
    assert readiness.model(model.key, {}).steps[0].kind == 'trust'
    assert share.open_file(path)['says'].endswith('from a file shared earlier')
    update = channels.update(opened['channel'])
    # Never "import the newer file" as if it updated this one: a newer shared file is an entry of its own.
    assert "doesn't update itself" in update['says'] and 'A newer shared file is added as its own entry' in update['says']
    assert not (share.root() / 'repos').exists()


def test_failed_registration_stays_unpublished_and_allows_corrected_retry(home, monkeypatch):
    path = share.export(yours(home), by='Maya')
    monkeypatch.setenv('MICROMIND_CATALOG', str(home / 'recipient'))
    catalog.manifests.cache_clear()
    original = channels._save
    def disk_failure(*args, **kwargs):
        raise OSError('controlled storage failure')
    monkeypatch.setattr(channels, '_save', disk_failure)
    with pytest.raises(share.CantShare, match='storage failure'):
        share.open_file(path)
    assert not catalog.channels()
    assert not list(catalog.channels_root().glob('*/' + '?' * 64))
    monkeypatch.setattr(channels, '_save', original)
    opened = share.open_file(path)
    assert catalog.get(opened['model']) is not None, catalog.problems()


@pytest.mark.parametrize('damage', ['none', 'bytes', 'symlink'])
def test_interrupted_import_reuses_only_the_exact_validated_snapshot(home, monkeypatch, damage):
    path = share.export(yours(home), by='Maya')
    opened = share.open_file(path)
    record = catalog.channels()[0]
    snapshot = catalog.snapshot(record)
    channels.config().unlink()  # simulate interruption between snapshot publication and registry commit
    catalog.manifests.cache_clear()
    target = snapshot / 'dark-spots' / 'adapter.py'
    original = target.read_bytes()
    if damage == 'bytes':
        target.chmod(0o644)
        target.write_bytes(b'different bytes')
    elif damage == 'symlink':
        private = home / 'private'
        private.write_bytes(original)
        target.unlink()
        target.symlink_to(private)
    if damage == 'none':
        assert share.open_file(path)['model'] == opened['model']
    else:
        with pytest.raises(share.CantShare, match='kept unchanged'):
            share.open_file(path)
        assert not catalog.channels()
        assert target.is_symlink() if damage == 'symlink' else target.read_bytes() == b'different bytes'
        if damage == 'symlink': assert private.read_bytes() == original


@pytest.mark.parametrize("extra, why", [
    ({"catalog/models/dark-spots/run.sh": b"rm -rf ~"}, "no catalog entry holds"),
    ({"catalog/models/other/model.toml": b"x"}, "no catalog entry holds"),
    ({"../../escape.py": b"x"}, "no catalog entry holds"),
])
def test_a_file_with_anything_an_entry_doesnt_hold_is_refused_whole(home, tmp_path, extra, why):
    good = share.export(yours(home), by="Maya")
    bad = tmp_path / "bad.observationui.zip"
    with zipfile.ZipFile(good) as z, zipfile.ZipFile(bad, "w") as out:
        for n in z.namelist():
            out.writestr(n, z.read(n))
        for n, data in extra.items():
            out.writestr(n, data)
    with pytest.raises(share.CantShare, match=why):
        share.open_file(bad)
    linked = tmp_path / "link.observationui.zip"
    with zipfile.ZipFile(good) as z, zipfile.ZipFile(linked, "w") as out:
        for n in z.namelist():
            out.writestr(n, z.read(n))
        info = zipfile.ZipInfo("catalog/models/dark-spots/README.md")
        info.external_attr = (0o120777 << 16)
        out.writestr(info, "/etc/passwd")
    with pytest.raises(share.CantShare, match="holds a link"):
        share.open_file(linked)
    plain = tmp_path / "plain.observationui.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as out:
        out.writestr("catalog/models/dark-spots/model.toml", "x")
    plain.write_bytes(buf.getvalue())
    with pytest.raises(share.CantShare, match="no SHARED.json"):
        share.open_file(plain)


def test_applicable_attribution_files_travel_with_exact_bytes(home, monkeypatch, tmp_path):
    key = yours(home)
    folder = catalog.entry_folder(catalog.get(key))
    credits = {"LICENSE": b"Permission notice\r\n", "NOTICE.txt": b"Upstream credit\n", "COPYING.md": b"Terms\n"}
    for name, data in credits.items():
        (folder / name).write_bytes(data)
    path = share.export(key)
    with zipfile.ZipFile(path) as archive:
        assert {name: archive.read("catalog/models/dark-spots/" + name) for name in credits} == credits
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "receiver"))
    catalog.manifests.cache_clear()
    opened = share.open_file(path)
    received = catalog.entry_folder(catalog.get(opened["model"]))
    assert {name: (received / name).read_bytes() for name in credits} == credits


@pytest.mark.parametrize("kind", ["link", "oversize"])
def test_attribution_files_keep_the_portable_file_bounds_and_containment(home, tmp_path, kind):
    key = yours(home)
    folder = catalog.entry_folder(catalog.get(key))
    if kind == "link":
        outside = tmp_path / "private-note"
        outside.write_text("not licensed entry content")
        (folder / "LICENSE").symlink_to(outside)
    else:
        (folder / "LICENSE").write_bytes(b"x" * (share.MAX_FILE + 1))
    with pytest.raises(share.CantShare, match="link|big|regular"):
        share.export(key)


def test_portable_attribution_with_ambiguous_case_is_refused_whole(home, tmp_path):
    good = share.export(yours(home))
    path = tmp_path / 'ambiguous.observationui.zip'
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(path, 'w') as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr('catalog/models/dark-spots/LICENSE', b'first terms')
        target.writestr('catalog/models/dark-spots/license', b'different terms')
    with pytest.raises(share.CantShare, match='ambiguous'):
        share.open_file(path)


@pytest.mark.parametrize("alias", [
    "catalog/models/dark-spots/./LICENSE",
    "catalog//models/dark-spots/LICENSE",
    "./catalog/models/dark-spots/LICENSE",
    "catalog/models/dark-spots/LICENSE/.",
])
def test_noncanonical_attribution_paths_cannot_replace_terms_or_create_a_channel(home, tmp_path, alias):
    good = share.export(yours(home))
    path = tmp_path / "aliased.observationui.zip"
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(path, "w") as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        target.writestr("catalog/models/dark-spots/LICENSE", b"original terms")
        target.writestr(alias, b"replacement terms")
    with pytest.raises(share.CantShare, match="noncanonical|ambiguous"):
        share.open_file(path)
    assert not (share.root() / "repos").exists(), "refusal precedes every destination write"


def test_declared_template_support_data_travels_with_its_adapter(home, monkeypatch, tmp_path):
    """A recipient gets the complete entry before trust; no model or installer runs."""
    from orchestrator import link_recognise
    monkeypatch.setattr(link_recognise, 'TEMPLATES', Path(__file__).resolve().parents[2] / 'catalog' / 'templates')
    key = yours(home)
    folder = catalog.entry_folder(catalog.get(key))
    support = (link_recognise.TEMPLATES / 'bioimageio' / 'output-semantics.json').read_bytes()
    (folder / 'output-semantics.json').write_bytes(support)
    path = share.export(key, by='Maya')
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'recipient'))
    catalog.manifests.cache_clear()
    opened = share.open_file(path)
    received = catalog.get(opened['model'])
    assert (catalog.entry_folder(received) / 'output-semantics.json').read_bytes() == support
    assert readiness.model(received.key, {}).steps[0].kind == 'trust'


@pytest.mark.parametrize("existing", [False, True])
def test_sharing_quoted_manifest_keys_preserves_semantics_and_card_attribution(home, tmp_path, existing):
    from orchestrator import capability_import
    key = yours(home)
    m = catalog.get(key)
    folder = catalog.entry_folder(m)
    manifest_path = folder / catalog.MANIFEST
    fields = tomllib.loads(manifest_path.read_text())
    fields["what"] = "This description mentions contributed_by but is not attribution"
    if existing:
        fields["contributed_by"] = {"name": "Original author"}
    manifest_path.write_text("# contributed_by is metadata, not this comment\n" +
                             "\n".join(capability_import._toml(k) + " = " + capability_import._toml(v)
                                       for k, v in fields.items()))
    before = manifest_path.read_bytes()
    m, problems = catalog.read(manifest_path, namespace="mine")
    assert m is not None, problems
    packaged = tmp_path / "attributed.observationui.zip"
    packaged.write_bytes(share.pack(m, by="Sharing researcher"))
    _, _, files = share._read(packaged)
    shared_fields = tomllib.loads(files[catalog.MANIFEST].decode())
    assert shared_fields == {**fields, "contributed_by": fields.get("contributed_by", {"name": "Sharing researcher"})}
    assert manifest_path.read_bytes() == before
    recipient = {"MICROMIND_CATALOG": str(tmp_path / "recipient")}
    opened = share.open_file(packaged, recipient)
    received = catalog.mine(recipient)[opened["model"]]
    assert received.contributed_by == shared_fields["contributed_by"]
    assert not channels.trusted(received, recipient)
