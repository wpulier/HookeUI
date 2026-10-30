"""Operator launch selection; synthetic isolated profile, no actual browser proof."""
import asyncio
import sys

import pytest

from orchestrator.paths import Paths
from orchestrator.project_launch import configured_locations
from orchestrator.project_roots import ProjectLocationError, ProjectRoot


def test_no_implicit_activation_or_forgetting_registered_projects(tmp_path):
    paths = Paths.under(tmp_path / 'host')
    assert configured_locations(paths, None) is None
    assert not paths.home.exists()
    paths.home.mkdir()
    registry = paths.home / 'project-locations.json'
    registry.write_text('{damaged')
    with pytest.raises(ProjectLocationError, match='portable Project locations'):
        configured_locations(paths, None)
    with pytest.raises(ProjectLocationError, match='unreadable'):
        configured_locations(paths, str(tmp_path / 'Projects'))
    assert registry.read_text() == '{damaged'


def test_parent_validation_does_not_create_or_replace_folders(tmp_path):
    paths = Paths.under(tmp_path / 'host')
    file = tmp_path / 'file'
    file.write_text('kept')
    link = tmp_path / 'link'
    link.symlink_to(tmp_path, target_is_directory=True)
    for value in ('', 'relative', str(file), str(link)):
        with pytest.raises(ProjectLocationError):
            configured_locations(paths, value)
    selected = configured_locations(paths, str(tmp_path / 'Projects'))
    assert not selected.parent.exists()
    selected.close()
    assert file.read_text() == 'kept'


@pytest.mark.parametrize('entrance', ['argument', 'environment'])
def test_ordinary_server_main_uses_operator_location(tmp_path, monkeypatch, entrance):
    from orchestrator import server
    paths = Paths.under(tmp_path / 'host')
    parent = tmp_path / 'Projects'
    monkeypatch.setattr(server.Paths, 'from_env', lambda: paths)
    argv = ['orchestrator', '--no-gpu', '--bridge-url', 'http://127.0.0.1:1']
    monkeypatch.delenv('HOOKE_PROJECTS_DIR', raising=False)
    if entrance == 'argument':
        argv += ['--project-folders', str(parent)]
    else:
        monkeypatch.setenv('HOOKE_PROJECTS_DIR', str(parent))
    monkeypatch.setattr(sys, 'argv', argv)
    def serve(app, **kwargs):
        built = app.state.app
        try:
            saved = built.day.create_retained('Portable research', actor='user')
            root = built.day.locations.project(saved.sid)
            assert root.path.parent == parent
            assert ProjectRoot.open(root.path) == root
            assert not (paths.sessions / saved.sid).exists()
        finally:
            built.day.locations.close()  # no background work is admitted by this transport stand-in
            asyncio.run(built.stop())
    monkeypatch.setattr(server.uvicorn, 'run', serve)
    server.main()
