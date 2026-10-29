"""Storage foundation only: isolated folders, not complete Project portability."""
import json
import shutil

import pytest

from orchestrator.day.ownership import origin
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot, create_layout, writer


def made(tmp_path):
    owner = origin('2026-10-29', 1234)
    return create_layout(tmp_path / 'projects', 'Cell research', owner)


def test_folder_can_move_without_changing_identity(tmp_path):
    root = made(tmp_path)
    moved = root.path.with_name('A new folder label')
    root.path.rename(moved)
    reopened = ProjectRoot.open(moved)
    assert (reopened.origin, reopened.session, reopened.name) == (root.origin, root.session, root.name)
    assert (moved / 'project.json').is_file()
    assert {p.name for p in moved.iterdir()} == {'project.json', 'README.md', 'inputs', 'artifacts', 'history', 'workspace', 'exports'}


def test_existing_folder_is_never_overwritten(tmp_path):
    owner = origin('2026-10-29', 1234)
    root = create_layout(tmp_path, 'Cells', owner)
    (root.folder('inputs') / 'unique.txt').write_text('evidence')
    with pytest.raises(FileExistsError):
        create_layout(tmp_path, 'Cells', owner)
    assert (root.folder('inputs') / 'unique.txt').read_text() == 'evidence'


def test_second_writer_refuses_but_saved_manifest_remains_readable(tmp_path):
    root = made(tmp_path)
    with writer(root):
        assert ProjectRoot.open(root.path) == root
        with pytest.raises(ProjectLocationError, match='read-only'):
            with writer(root):
                pytest.fail('two writers entered')
    with writer(root):
        pass
    assert (root.path / '.writer.lock').is_file()


def test_registry_relocation_is_explicit_and_cannot_replace_active_original(tmp_path):
    root = made(tmp_path)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.register(root)
    copy_path = tmp_path / 'copy'
    shutil.copytree(root.path, copy_path)
    copied = ProjectRoot.open(copy_path)
    with pytest.raises(ProjectLocationError, match='known elsewhere'):
        registry.register(copied)
    with writer(root):
        with pytest.raises(ProjectLocationError, match='read-only'):
            registry.register(copied, replace_inactive=True)
    assert registry.locate(root.origin) == root
    registry.register(copied, replace_inactive=True)
    assert registry.locate(root.origin) == copied
    assert root.path.is_dir()
    assert set(json.loads(registry.path.read_text())) == {'format', 'locations'}


def test_registry_recovers_moved_original_only_on_explicit_choice(tmp_path):
    root = made(tmp_path)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.register(root)
    moved = tmp_path / 'moved'
    root.path.rename(moved)
    with pytest.raises(ProjectLocationError):
        registry.locate(root.origin)
    reopened = ProjectRoot.open(moved)
    registry.register(reopened, replace_inactive=True)
    assert registry.locate(root.origin) == reopened


def test_replaced_manifest_and_symlink_sections_refuse(tmp_path):
    root = made(tmp_path)
    outside = tmp_path / 'outside'
    outside.mkdir()
    root.folder('inputs').rmdir()
    (root.path / 'inputs').symlink_to(outside, target_is_directory=True)
    with pytest.raises(ProjectLocationError, match='missing or replaced'):
        ProjectRoot.open(root.path)
    (root.path / 'inputs').unlink()
    (root.path / 'inputs').mkdir()
    manifest = root.path / 'project.json'
    data = json.loads(manifest.read_text())
    data['origin'] = 'b' * 32
    manifest.write_text(json.dumps(data))
    with pytest.raises(ProjectLocationError, match='changed'):
        with writer(root):
            pytest.fail('identity replacement admitted')


def test_corrupt_registry_is_not_silently_replaced(tmp_path):
    root = made(tmp_path)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.home.mkdir()
    registry.path.write_text('{torn')
    with pytest.raises(ProjectLocationError):
        registry.register(root)
    assert registry.path.read_text() == '{torn'


def test_fifo_metadata_refuses_without_waiting_for_a_writer(tmp_path):
    import os
    root = made(tmp_path)
    manifest = root.path / 'project.json'
    manifest.unlink()
    os.mkfifo(manifest)
    with pytest.raises(ProjectLocationError, match='regular file'):
        ProjectRoot.open(root.path)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.home.mkdir()
    os.mkfifo(registry.path)
    with pytest.raises(ProjectLocationError, match='regular file'):
        registry.locate('a' * 32)


def test_dangling_registry_link_is_preserved_and_refused(tmp_path):
    root = made(tmp_path)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.home.mkdir()
    registry.path.symlink_to(tmp_path / 'unavailable-registry.json')
    with pytest.raises(OSError):
        registry.register(root)
    assert registry.path.is_symlink()
