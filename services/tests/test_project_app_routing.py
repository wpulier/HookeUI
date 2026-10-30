"""Real application/door storage with synthetic connections; no live-client qualification."""
import asyncio

import pytest

from tests.waiting import HANG_S
from orchestrator.app import App
from orchestrator.paths import Paths
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_roots import ProjectRegistry, ProjectRoot, ProjectLocationError, writer


def build(paths, parent):
    locations = ProjectLocations(paths.sessions, ProjectRegistry(paths.home), parent)
    return App.build('http://127.0.0.1:1', paths, use_gpu=False, project_locations=locations)


def close(built):
    asyncio.run(built.stop())


def test_app_connector_creation_import_study_and_relocation_keep_exact_history(tmp_path):
    import json
    import subprocess
    import sys
    import selectors
    from pathlib import Path

    def probe(mode):
        process = subprocess.Popen([sys.executable, '-m', 'tests.project_app_host_probe', mode, str(tmp_path)],
            cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)
        try:
            with selectors.DefaultSelector() as ready:
                ready.register(process.stdout, selectors.EVENT_READ)
                assert ready.select(HANG_S), 'Application probe did not reach stopped state'
            line = process.stdout.readline()
            if not line:
                _, err = process.communicate(timeout=HANG_S)
                pytest.fail(err)
            assert line.strip() == 'stopped-with-lease', line
            receipt = json.loads((tmp_path / 'fixture-receipt.json').read_text())
            root = ProjectRoot.open(tmp_path / 'Moved Project' if mode == 'reopen' else receipt['root'])
            with pytest.raises(ProjectLocationError, match='read-only'):
                with writer(root): pass
            _, err = process.communicate('exit\n', timeout=HANG_S)
            assert process.returncode == 0, err
            with writer(root): pass
            return root
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=HANG_S)

    root = probe('create')
    moved = tmp_path / 'Moved Project'
    root.path.rename(moved)
    ProjectRegistry(tmp_path / 'host').register(ProjectRoot.open(moved), replace_inactive=True)
    probe('reopen')
    assert not list(moved.rglob('projects.json'))  # grants are host-local
    assert not list(moved.rglob('preferences.jsonl'))


def test_failed_app_build_releases_admitted_project_writer(tmp_path, monkeypatch):
    paths = Paths.under(tmp_path / 'host')
    built = build(paths, tmp_path / 'Projects')
    owner = built.day.create_retained('Preserved', actor='user')
    sid = owner.sid
    root = built.day.locations.project(sid)
    built.day.locations.close()
    close(built)
    locations = ProjectLocations(paths.sessions, ProjectRegistry(paths.home), tmp_path / 'Projects')
    locations.writable(sid)
    def fail(*args, **kwargs):
        raise RuntimeError('injected startup failure')
    monkeypatch.setattr(App, '_build', fail)
    with pytest.raises(RuntimeError, match='startup failure'):
        App.build('http://127.0.0.1:1', paths, use_gpu=False, project_locations=locations)
    assert locations.closed
    with writer(root): pass


def test_app_rejects_other_host_resolver_without_closing_it(tmp_path):
    paths = Paths.under(tmp_path / 'host')
    locations = ProjectLocations(tmp_path / 'unrelated/sessions', ProjectRegistry(tmp_path / 'other'), tmp_path / 'Projects')
    try:
        with pytest.raises(ProjectLocationError, match='this host'):
            App.build('http://127.0.0.1:1', paths, use_gpu=False, project_locations=locations)
        assert not locations.closed
    finally:
        locations.close()
