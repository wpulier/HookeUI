"""Real directory transactions with controlled filesystem failures; no installed package qualification."""
from pathlib import Path
import pytest
from orchestrator import runtime_cleanup


def test_failed_multi_generation_withdrawal_restores_every_original(tmp_path, monkeypatch):
    folders = [tmp_path / ('a' * 16), tmp_path / ('b' * 16)]
    for folder in folders:
        folder.mkdir()
        (folder / 'receipt.json').write_text(folder.name)
    rename = Path.rename
    def fail_second(self, destination):
        if self == folders[1]:
            raise PermissionError('controlled second-generation refusal')
        return rename(self, destination)
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'rename', fail_second)
        with pytest.raises(PermissionError):
            runtime_cleanup.withdraw(folders)
    assert sorted(tmp_path.iterdir()) == folders
    assert [(folder / 'receipt.json').read_text() for folder in folders] == [p.name for p in folders]
    trash = runtime_cleanup.withdraw(folders)
    assert not any(folder.exists() for folder in folders)
    for folder in trash:
        runtime_cleanup.discard(folder)
    assert not list(tmp_path.iterdir())


def test_cleanup_cannot_accept_active_or_linked_directories(tmp_path):
    active = tmp_path / ('a' * 16)
    active.mkdir()
    original = active / 'data'
    original.write_bytes(b'keep')
    with pytest.raises(OSError):
        runtime_cleanup.discard(active)
    link = tmp_path / '.removing-linked'
    link.symlink_to(active, target_is_directory=True)
    with pytest.raises(OSError):
        runtime_cleanup.discard(link)
    with pytest.raises(OSError):
        runtime_cleanup.withdraw([link])
    assert original.read_bytes() == b'keep'
