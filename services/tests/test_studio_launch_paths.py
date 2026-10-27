"""Real launcher path selection only: no server, model, camera or save mutation."""
import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / 'scripts' / 'studio.sh'


def launch_paths(values):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('MICROMIND_') and key not in ('ORCH_PORT', 'CAMERA_PORT', 'FOOTAGE_DIR')}
    env.update(values)
    returned = subprocess.run(['bash', '-c',
        'source "$1"; printf "%s\\n" "$LOG_DIR" "$RECORDINGS_DIR" "$SAVES_ROOT" "${MICROMIND_DATASETS_DIR:-}"',
        'path-check', str(SCRIPT)], env=env, text=True, capture_output=True, check=True)
    return returned.stdout.splitlines()


def test_explicit_home_and_write_overrides_survive_global_saves_marker(tmp_path):
    assets, home = tmp_path / 'assets', tmp_path / 'private-home'
    assets.mkdir()
    (assets / '.saves.json').write_text('{}')
    logs, recordings = home / 'custom-logs', home / 'custom-recordings'
    assert launch_paths({'MICROMIND_ASSETS': str(assets), 'MICROMIND_HOME': str(home),
        'MICROMIND_INTERACTION_LOG': str(logs / 'interactions.jsonl'),
        'MICROMIND_RECORDINGS_DIR': str(recordings), 'ORCH_PORT': '61770'}) == [
            str(logs), str(recordings), '', '',
        ]


def test_moved_live_saves_keep_their_existing_defaults(tmp_path):
    assets = tmp_path / 'assets'
    assets.mkdir()
    (assets / '.saves.json').write_text('{}')
    assert launch_paths({'MICROMIND_ASSETS': str(assets), 'ORCH_PORT': '8770'}) == [
        str(assets / 'logs'), str(assets / 'assets' / 'recordings'), str(assets), str(assets / 'datasets'),
    ]


@pytest.mark.parametrize('individual', [False, True])
def test_relative_write_paths_have_the_same_base_in_all_consumers(tmp_path, individual):
    values = {'MICROMIND_ASSETS': str(tmp_path), 'MICROMIND_HOME': 'relative-private-home',
              'ORCH_PORT': '61770'}
    services = SCRIPT.parent.parent / 'services'
    expected_logs = services / 'relative-private-home' / 'logs'
    expected_recordings = SCRIPT.parent.parent / 'recordings'
    if individual:
        values.update(MICROMIND_INTERACTION_LOG='relative-logs/interactions.jsonl',
                      MICROMIND_RECORDINGS_DIR='relative-recordings')
        expected_logs = services / 'relative-logs'
        expected_recordings = services / 'relative-recordings'
    assert launch_paths(values) == [str(expected_logs), str(expected_recordings), '', '']


def test_the_bridge_is_given_the_apps_snapshots_and_results_as_media_roots(tmp_path):
    """The bridge may play any picture or recording in them (POST /feed?source=asset): the app's own, as it keeps them."""
    env = {key: value for key, value in os.environ.items()
           if not key.startswith('MICROMIND_') and key not in ('ORCH_PORT', 'CAMERA_PORT', 'FOOTAGE_DIR')}
    env.update({'MICROMIND_ASSETS': str(tmp_path), 'MICROMIND_HOME': str(tmp_path / 'home'), 'ORCH_PORT': '61770'})
    returned = subprocess.run(['bash', '-c', 'source "$1"; printf "%s\\n" "$SNAPSHOTS_DIR" "$RESULTS_DIR"',
                               'path-check', str(SCRIPT)], env=env, text=True, capture_output=True, check=True)
    assert returned.stdout.splitlines() == [str(tmp_path / 'home' / 'snapshots'), str(tmp_path / 'home' / 'results')]
    assert '--media-root "$SNAPSHOTS_DIR" --media-root "$RESULTS_DIR"' in SCRIPT.read_text()


def test_status_does_not_describe_a_running_worker_as_installed_models(tmp_path):
    """A fresh worker can answer health while no model weights exist."""
    curl = tmp_path / 'curl'
    curl.write_text('#!/bin/sh\nprintf \'%s\\n\' \'{"service":"microscope-mind-orchestrator","gpu":{"ready":true}}\'\n')
    curl.chmod(0o755)
    pgrep = tmp_path / 'pgrep'
    pgrep.write_text('#!/bin/sh\nexit 1\n')
    pgrep.chmod(0o755)
    env = {key: value for key, value in os.environ.items() if not key.startswith('MICROMIND_')}
    env.update(PATH=f"{tmp_path}:{os.environ['PATH']}", MICROMIND_HOME=str(tmp_path / 'profile'),
               MICROMIND_ASSETS=str(tmp_path / 'assets'), MICROMIND_KEYS_FILE=str(tmp_path / 'absent'))
    result = subprocess.run(['bash', str(SCRIPT), 'status'], env=env, text=True, capture_output=True, check=True)
    assert 'outline models ready' not in result.stdout
    assert 'worker ready' in result.stdout and 'Models' in result.stdout
