"""Real ordinary server routes/lifespan; synthetic bytes and consent, no browser or real client."""
import json
from pathlib import Path
import subprocess
import sys
import pytest

from orchestrator.project_roots import ProjectRegistry, ProjectRoot, writer
from tests.waiting import HANG_S


@pytest.mark.parametrize('material', ['protocol', 'mixed'])
@pytest.mark.parametrize('restoring', [False, True], ids=['move', 'restore-snapshot'])
def test_studio_create_connector_contribute_shutdown_move_and_restart(tmp_path, material, restoring):
    def probe(mode):
        result = subprocess.run([sys.executable, '-m', 'tests.project_server_host_probe', mode, str(tmp_path), material],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=HANG_S)
        assert result.returncode == 0, result.stdout + result.stderr

    probe('create')
    receipt = json.loads((tmp_path / 'server-receipt.json').read_text())
    root = ProjectRoot.open(receipt['root'])
    with writer(root):
        pass
    from orchestrator.project_folder_copy import copy_snapshot, restore_snapshot, verify_snapshot
    snapshot = copy_snapshot(root, tmp_path / 'Snapshot')
    snapshot_receipt = verify_snapshot(snapshot.path)
    assert snapshot_receipt['externalDependencies'] == 'not-verified'
    moved = tmp_path / 'Moved Studio Project'
    if restoring:
        restore_snapshot(snapshot.path, moved)
    else:
        root.path.rename(moved)
    ProjectRegistry(tmp_path / 'host').register(ProjectRoot.open(moved), replace_inactive=True)
    probe('reopen')
    assert verify_snapshot(snapshot.path) == snapshot_receipt
    assert not list(moved.rglob('preferences.jsonl'))
    assert not list(moved.rglob('projects.json'))
