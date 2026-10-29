"""Isolated filesystem restoration; not live Project/UI or dependency-closure proof."""
import json
from types import SimpleNamespace

import pytest

from orchestrator.day.prefs import Prefs
from orchestrator.project_repository import ProjectRepository, create, document_root
from orchestrator.project_roots import COPY_INTENT, SNAPSHOT, ProjectLocationError, ProjectRegistry, ProjectRoot, writer
from orchestrator.project_folder_copy import copy_snapshot, estimate, verify_snapshot


def setup(tmp_path):
    prefs = Prefs(tmp_path / 'host')
    root = create(tmp_path / 'Projects', 'Research', session='2026-10-29', host_preferences=prefs)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.register(root)
    repo = ProjectRepository(root)
    doc = {'kind': 'note', 'title': 'Question', 'state': {'text': 'First question'}, 'resources': [], 'runtimes': []}
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'runtimes') as edit:
        first = edit.create_document(doc)
        second = edit.save_document(first.id, {**doc, 'state': {'text': 'Revised question'}}, expected_revision=1)
        edit.append({'type': 'chat', 'role': 'user', 'text': 'Retain this conversation'})
    (root.path / 'inputs/material.bin').write_bytes(b'original imported bytes')
    (root.path / 'inputs/.runtime-roots.json').write_text('user material with a reserved-looking name')
    (root.path / 'workspace/layout.json').write_text('{"tabs": ["main", "study"]}')
    (root.path / 'credentials.json').write_text('host data must not be swept into the snapshot')
    return root, repo, first, second


def test_copy_move_read_keeps_all_revisions_history_and_material_without_authority(tmp_path):
    root, repo, first, second = setup(tmp_path)
    before = estimate(root)
    copied = copy_snapshot(root, tmp_path / 'Collected')
    moved = tmp_path / 'Moved'
    copied.path.rename(moved)
    check = verify_snapshot(moved)
    assert check['bytes'] == before['bytes'] and check['externalDependencies'] == 'not-verified'
    restored = ProjectRepository(ProjectRoot.open(moved))
    assert restored.read(first.id, 1) == first
    assert restored.read(second.id, 2) == second
    assert restored.events() == repo.events()
    assert (moved / 'workspace/layout.json').read_bytes() == (root.path / 'workspace/layout.json').read_bytes()
    assert not (moved / 'credentials.json').exists()
    assert not (document_root(restored.root) / '.runtime-roots.json').exists()
    assert (moved / 'inputs/.runtime-roots.json').read_text().startswith('user material')
    source = root.path / 'inputs/material.bin'
    assert source.stat().st_ino != (moved / 'inputs/material.bin').stat().st_ino
    source.unlink()
    assert (moved / 'inputs/material.bin').read_bytes() == b'original imported bytes'
    with pytest.raises(ProjectLocationError, match='snapshot'):
        with writer(restored.root): pass
    # Reads can initialize ordinary excluded read-lock files without changing evidence.
    assert verify_snapshot(moved) == check


def test_active_writer_refuses_copy_without_creating_destination(tmp_path):
    root, *_ = setup(tmp_path)
    with writer(root), pytest.raises(ProjectLocationError, match='read-only'):
        copy_snapshot(root, tmp_path / 'Refused')
    assert not (tmp_path / 'Refused').exists()


def test_fresh_project_empty_storage_and_read_only_media(tmp_path):
    prefs = Prefs(tmp_path / 'host')
    root = create(tmp_path / 'Projects', 'Empty', session='2026-10-29', host_preferences=prefs)
    (root.path / 'inputs/empty').mkdir()
    copied = copy_snapshot(root, tmp_path / 'Copy')
    assert document_root(copied).is_dir()
    assert (copied.path / 'inputs/empty').is_dir()
    paths = [copied.path, *copied.path.rglob('*')]
    modes = {p: p.stat().st_mode & 0o777 for p in paths}
    try:
        for path in paths: path.chmod(0o500 if path.is_dir() else 0o400)
        assert verify_snapshot(copied.path)['files'] > 0
        assert ProjectRepository(copied).documents() == ([], [])
        assert ProjectRepository(copied).events()
    finally:
        for path, mode in modes.items(): path.chmod(mode)


def test_insufficient_disk_refuses_before_destination(tmp_path, monkeypatch):
    root, *_ = setup(tmp_path)
    monkeypatch.setattr('orchestrator.project_folder_copy.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(ProjectLocationError, match='free bytes'):
        copy_snapshot(root, tmp_path / 'Refused')
    assert not (tmp_path / 'Refused').exists()


def test_interruption_is_explicit_and_original_unchanged(tmp_path):
    root, repo, *_ = setup(tmp_path)
    original = repo.events()
    target = tmp_path / 'Interrupted'
    def check():
        if (target / 'inputs/material.bin').exists(): raise RuntimeError('injected stop')
    with pytest.raises(RuntimeError, match='injected stop'):
        copy_snapshot(root, target, check=check)
    assert (target / COPY_INTENT).exists()
    with pytest.raises(ProjectLocationError, match='incomplete'): ProjectRoot.open(target)
    assert repo.events() == original
    with pytest.raises(FileExistsError): copy_snapshot(root, target)
    assert verify_snapshot(copy_snapshot(root, tmp_path / 'Retry').path)['files'] > 0


def test_source_change_during_copy_never_publishes_success(tmp_path):
    root, *_ = setup(tmp_path)
    target = tmp_path / 'Changed'
    changed = False
    def check():
        nonlocal changed
        if (target / 'inputs/material.bin').exists() and not changed:
            changed = True
            (root.path / 'inputs/material.bin').write_bytes(b'changed externally')
    with pytest.raises(ProjectLocationError, match='changed'):
        copy_snapshot(root, target, check=check)
    assert (target / COPY_INTENT).exists()


@pytest.mark.parametrize('damage', ['bytes', 'missing', 'extra', 'traversal', 'duplicate'])
def test_changed_snapshot_refuses_verification(tmp_path, damage):
    root, *_ = setup(tmp_path)
    copy = copy_snapshot(root, tmp_path / 'Copy')
    material = copy.path / 'inputs/material.bin'
    if damage == 'bytes': material.write_bytes(b'not the original')
    elif damage == 'missing': material.unlink()
    elif damage == 'extra': (copy.path / 'inputs/extra.bin').write_bytes(b'extra')
    else:
        path = copy.path / SNAPSHOT
        manifest = json.loads(path.read_text())
        if damage == 'traversal': manifest['entries'][0]['path'] = '../outside'
        else: manifest['entries'][0] = manifest['entries'][1]
        path.write_text(json.dumps(manifest))
    with pytest.raises(ProjectLocationError): verify_snapshot(copy.path)


def test_symlink_and_nested_destination_refuse(tmp_path):
    root, *_ = setup(tmp_path)
    with pytest.raises(ProjectLocationError, match='outside'): copy_snapshot(root, root.path / 'nested')
    (root.path / 'inputs/linked').symlink_to(tmp_path / 'outside')
    with pytest.raises(ProjectLocationError, match='linked'): copy_snapshot(root, tmp_path / 'Refused')
    assert not (tmp_path / 'Refused').exists()
