"""Real isolated stores, synthetic evidence; not public folder-opening qualification."""
from types import SimpleNamespace
import pytest
from orchestrator.project_folder_copy import copy_snapshot, restore_snapshot, verify_snapshot
from orchestrator.project_repository import ProjectRepository
from orchestrator.project_roots import COPY_INTENT, SNAPSHOT, ProjectLocationError, ProjectRegistry, ProjectRoot, writer
from orchestrator.day.prefs import Prefs
from tests.test_project_folder_copy import setup


def test_restoration_preserves_history_and_requires_separate_location_choice(tmp_path):
    root, repo, first, second = setup(tmp_path)
    snap = copy_snapshot(root, tmp_path / 'Snapshot')
    seal = (snap.path / SNAPSHOT).read_bytes()
    restored = restore_snapshot(snap.path, tmp_path / 'Working')
    assert restored.origin == root.origin and restored.session == root.session
    assert not (restored.path / SNAPSHOT).exists()
    assert (snap.path / SNAPSHOT).read_bytes() == seal
    assert verify_snapshot(snap.path)['externalDependencies'] == 'not-verified'
    receipts = list((restored.path / 'history/restorations').glob('*/snapshot.json'))
    assert len(receipts) == 1 and receipts[0].read_bytes() == seal
    read = ProjectRepository(restored)
    assert read.events() == repo.events()
    assert read.read(first.id, 1) == first and read.read(second.id, 2) == second
    assert (root.path / 'inputs/material.bin').stat().st_ino != (restored.path / 'inputs/material.bin').stat().st_ino
    assert not (restored.path / 'credentials.json').exists()
    registry = ProjectRegistry(tmp_path / 'host')
    assert registry.locate(root.origin) == root
    with pytest.raises(ProjectLocationError, match='already known'):
        registry.register(restored)
    with writer(root), pytest.raises(ProjectLocationError, match='read-only'):
        registry.register(restored, replace_inactive=True)
    registry.register(restored, replace_inactive=True)
    with read.edit(Prefs(tmp_path / 'host'), registry=registry, host_runtime_root=tmp_path / 'runtime') as edit:
        third = edit.save_document(second.id, {**second.document, 'state': {'text': 'Continued here'}}, expected_revision=2)
    assert third.revision == 3 and repo.read(second.id).revision == 2
    assert verify_snapshot(snap.path)['externalDependencies'] == 'not-verified'


def test_corrupt_snapshot_refuses_before_destination(tmp_path):
    root, *_ = setup(tmp_path)
    snap = copy_snapshot(root, tmp_path / 'Snapshot')
    (snap.path / 'inputs/material.bin').write_bytes(b'changed')
    with pytest.raises(ProjectLocationError, match='content'):
        restore_snapshot(snap.path, tmp_path / 'Working')
    assert not (tmp_path / 'Working').exists()


def test_disk_and_destination_collision_preserve_snapshot(tmp_path, monkeypatch):
    root, *_ = setup(tmp_path)
    snap = copy_snapshot(root, tmp_path / 'Snapshot')
    before = verify_snapshot(snap.path)
    monkeypatch.setattr('orchestrator.project_folder_copy.shutil.disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(ProjectLocationError, match='free bytes'):
        restore_snapshot(snap.path, tmp_path / 'Working')
    assert not (tmp_path / 'Working').exists() and verify_snapshot(snap.path) == before
    monkeypatch.undo()
    with pytest.raises(ProjectLocationError, match='outside'):
        restore_snapshot(snap.path, snap.path / 'Nested')
    with pytest.raises(FileExistsError):
        restore_snapshot(snap.path, root.path)


@pytest.mark.parametrize('change', ['stop', 'manifest', 'source'])
def test_interrupted_or_changed_restoration_retains_marker(tmp_path, change):
    root, *_ = setup(tmp_path)
    snap = copy_snapshot(root, tmp_path / 'Snapshot')
    target = tmp_path / 'Working'
    changed = False
    def check():
        nonlocal changed
        if not changed and (target / 'inputs/material.bin').exists():
            changed = True
            if change == 'stop': raise RuntimeError('stop requested')
            if change == 'manifest':
                (snap.path / SNAPSHOT).write_bytes((snap.path / SNAPSHOT).read_bytes() + b' ')
            else:
                (snap.path / 'inputs/material.bin').write_bytes(b'changed while copying')
    with pytest.raises((RuntimeError, ProjectLocationError)):
        restore_snapshot(snap.path, target, check=check)
    assert (target / COPY_INTENT).exists()
    with pytest.raises(ProjectLocationError, match='incomplete'): ProjectRoot.open(target)


def test_running_records_are_retained_and_copy_can_be_snapshotted_again(tmp_path):
    root, repo, *_ = setup(tmp_path)
    registry = ProjectRegistry(tmp_path / 'host')
    with repo.edit(Prefs(tmp_path / 'host'), registry=registry, host_runtime_root=tmp_path / 'runtime') as edit:
        run = edit.create_document({'kind': 'project', 'title': 'Running', 'state': {'run': {'status': 'running'}}, 'resources': [], 'runtimes': []})
    snap = copy_snapshot(root, tmp_path / 'Snapshot')
    restored = restore_snapshot(snap.path, tmp_path / 'Working')
    assert ProjectRepository(restored).read(run.id) == run
    again = copy_snapshot(restored, tmp_path / 'Another snapshot')
    newest = restore_snapshot(again.path, tmp_path / 'Newest working copy')
    assert ProjectRepository(newest).read(run.id) == run
    assert len(list((newest.path / 'history/restorations').glob('*/snapshot.json'))) == 2
