"""A synthetic provider credential from one test must not authorize the next."""
import os
from pathlib import Path
import subprocess
import sys


def test_provider_credentials_and_origin_are_isolated_between_tests(tmp_path):
    tests = Path(__file__).parent
    (tmp_path / 'conftest.py').write_bytes((tests / 'conftest.py').read_bytes())
    (tmp_path / 'test_credentials.py').write_text('''
import os
from orchestrator import keys

def test_first_case_loads_its_own_synthetic_key(tmp_path):
    source = tmp_path / 'synthetic-keys.txt'
    source.write_text('OPENAI_API_KEY=synthetic-isolation-sentinel\\n')
    assert keys.load_keys(source)['OPENAI_API_KEY']
    assert 'OPENAI_API_KEY' in os.environ[keys.APPLIED]

def test_next_case_starts_without_provider_authority(tmp_path):
    assert not any(os.environ.get(name) for name in keys.KNOWN_KEYS)
    assert not os.environ.get(keys.APPLIED)
    assert not any(keys.load_keys(tmp_path / 'absent.txt').values())
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
