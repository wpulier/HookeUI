"""Exercise the suite's real fixture boundary in one fresh pytest process."""
import os
from pathlib import Path
import subprocess
import sys


def test_unrelated_tests_do_not_share_runtime_retention_registrations(tmp_path):
    tests = Path(__file__).parent
    (tmp_path / 'conftest.py').write_bytes((tests / 'conftest.py').read_bytes())
    (tmp_path / 'test_profiles.py').write_text('''
import pytest
from orchestrator import paths, scientific_retention as retention
from orchestrator.scientific_projects import ProjectStore

@pytest.mark.parametrize('profile', ['first', 'second'])
def test_independent_profile(tmp_path, monkeypatch, profile):
    monkeypatch.setattr(retention, 'MAX_STORES', 1)
    store = ProjectStore(tmp_path / profile, runtime_root=paths.envs())
    document = {'kind': 'scene', 'title': profile, 'state': {}, 'resources': [], 'runtimes': []}
    saved = store.create('s1', document)
    reopened = ProjectStore(store.root, runtime_root=paths.envs())
    assert reopened.read(saved.id, 's1') == saved
    assert len(list((paths.envs() / retention.METADATA).glob('*.json'))) == 1
''')
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', '-o', 'addopts=', str(tmp_path)],
        cwd=tests.parent,
        env={**os.environ, 'PYTHONPATH': str(tests.parent),
             'PYTEST_DISABLE_PLUGIN_AUTOLOAD': '1', 'PYTEST_ADDOPTS': '', 'PYTEST_PLUGINS': ''},
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert '2 passed' in result.stdout
