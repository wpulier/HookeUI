"""Real ordinary server routes/lifespan; synthetic bytes and consent, no browser or real client."""
import json
from pathlib import Path
import subprocess
import sys

from orchestrator.project_roots import ProjectRegistry, ProjectRoot, writer
from tests.waiting import HANG_S


def test_studio_create_connector_contribute_shutdown_move_and_restart(tmp_path):
    def probe(mode):
        result = subprocess.run([sys.executable, '-m', 'tests.project_server_host_probe', mode, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=HANG_S)
        assert result.returncode == 0, result.stdout + result.stderr

    probe('create')
    receipt = json.loads((tmp_path / 'server-receipt.json').read_text())
    root = ProjectRoot.open(receipt['root'])
    with writer(root):
        pass
    moved = tmp_path / 'Moved Studio Project'
    root.path.rename(moved)
    ProjectRegistry(tmp_path / 'host').register(ProjectRoot.open(moved), replace_inactive=True)
    probe('reopen')
    assert not list(moved.rglob('preferences.jsonl'))
    assert not list(moved.rglob('projects.json'))
