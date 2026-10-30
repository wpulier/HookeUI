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
    monkeypatch.delenv('HOOKE_REOPEN_PROJECT', raising=False)
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


def relocated(tmp_path):
    from orchestrator.day.store import SessionStore
    paths = Paths.under(tmp_path / 'host')
    parent = tmp_path / 'Projects'
    locations = configured_locations(paths, str(parent))
    day = SessionStore(paths.sessions, session='2026-10-29', locations=locations)
    sid = day.new('Research to move')
    day._record({'type': 'note', 'text': 'Kept original evidence'})
    root = locations.project(sid)
    before = day.journal.path.read_bytes()
    locations.close()
    moved = tmp_path / 'Relocated research'
    root.path.rename(moved)
    return paths, parent, root, moved, before


def test_explicit_reopen_recovers_known_origin_without_rewriting_history_or_access(tmp_path):
    from orchestrator.day.store import SessionStore
    paths, parent, root, moved, before = relocated(tmp_path)
    connections = paths.home / 'connections-placeholder'
    connections.write_text('unchanged owner-approved access')
    locations = configured_locations(paths, str(parent), reopen=str(moved))
    try:
        restored = SessionStore.resume(paths.sessions, locations=locations)
        assert restored.sid == root.session
        assert restored.journal.path.read_bytes() == before
        assert locations.project(root.session).path == moved
        assert connections.read_text() == 'unchanged owner-approved access'
        assert not (paths.sessions / root.session).exists()
    finally:
        locations.close()


def test_reopen_unknown_origin_cannot_import_history_or_access(tmp_path):
    paths, parent, root, moved, before = relocated(tmp_path)
    unrelated = Paths.under(tmp_path / 'unrelated-host')
    with pytest.raises(ProjectLocationError, match='not registered here'):
        configured_locations(unrelated, str(parent), reopen=str(moved))
    assert not (unrelated.home / 'project-locations.json').exists()
    assert (moved / 'history/sessions' / root.session / 'journal.jsonl').read_bytes() == before


@pytest.mark.parametrize('held', ['original', 'selected'])
def test_reopen_refuses_active_original_or_selected_copy(tmp_path, held):
    import shutil
    from orchestrator.project_roots import writer
    paths, parent, root, moved, _ = relocated(tmp_path)
    shutil.copytree(moved, root.path)
    registry = paths.home / 'project-locations.json'
    before = registry.read_bytes()
    with writer(root if held == 'original' else ProjectRoot.open(moved)):
        with pytest.raises(ProjectLocationError, match='read-only'):
            configured_locations(paths, str(parent), reopen=str(moved))
    assert registry.read_bytes() == before


def test_reopen_conflicting_legacy_owner_keeps_both_and_locator(tmp_path):
    from orchestrator.day.store import SessionStore
    paths, parent, root, moved, _ = relocated(tmp_path)
    conflicting = SessionStore(paths.sessions, session=root.session)
    legacy = conflicting.journal.path.read_bytes()
    registry = paths.home / 'project-locations.json'
    before = registry.read_bytes()
    with pytest.raises(ProjectLocationError, match='legacy Project'):
        configured_locations(paths, str(parent), reopen=str(moved))
    assert registry.read_bytes() == before and conflicting.journal.path.read_bytes() == legacy


def test_reopen_mismatched_journal_does_not_publish_locator(tmp_path):
    import json
    paths, parent, root, moved, _ = relocated(tmp_path)
    journal = moved / 'history/sessions' / root.session / 'journal.jsonl'
    events = [json.loads(line) for line in journal.read_text().splitlines()]
    events[0]['origin'] = 'f' * 32
    journal.write_text(''.join(json.dumps(event) + '\n' for event in events))
    registry = paths.home / 'project-locations.json'
    before = registry.read_bytes()
    with pytest.raises(ProjectLocationError, match='identity do not match'):
        configured_locations(paths, str(parent), reopen=str(moved))
    assert registry.read_bytes() == before


def test_reopen_ambiguous_registry_refuses_before_changing_location(tmp_path):
    import json
    paths, parent, root, moved, _ = relocated(tmp_path)
    registry = paths.home / 'project-locations.json'
    value = json.loads(registry.read_text())
    value['locations']['f' * 32] = dict(value['locations'][root.origin])
    registry.write_text(json.dumps(value))
    before = registry.read_bytes()
    with pytest.raises(ProjectLocationError, match='same host handle'):
        configured_locations(paths, str(parent), reopen=str(moved))
    assert registry.read_bytes() == before


@pytest.mark.parametrize('entrance', ['argument', 'environment'])
def test_server_startup_can_reopen_previously_selected_moved_project(tmp_path, monkeypatch, entrance):
    from orchestrator import server
    paths, parent, root, moved, before = relocated(tmp_path)
    monkeypatch.setattr(server.Paths, 'from_env', lambda: paths)
    monkeypatch.delenv('HOOKE_REOPEN_PROJECT', raising=False)
    argv = ['orchestrator', '--no-gpu', '--project-folders', str(parent)]
    if entrance == 'argument': argv += ['--reopen-project', str(moved)]
    else: monkeypatch.setenv('HOOKE_REOPEN_PROJECT', str(moved))
    monkeypatch.setattr(sys, 'argv', argv)
    def serve(app, **kwargs):
        built = app.state.app
        try:
            assert built.day.sid == root.session
            assert built.day.locations.project(root.session).path == moved
            assert built.day.journal.path.read_bytes().startswith(before)
        finally:
            built.day.locations.close()
            asyncio.run(built.stop())
    monkeypatch.setattr(server.uvicorn, 'run', serve)
    server.main()


def test_all_locations_offline_startup_preserves_original_and_allocates_once(tmp_path):
    from orchestrator.day.store import SessionStore
    from orchestrator.day.prefs import Prefs
    from orchestrator.project_repository import session_root
    paths = Paths.under(tmp_path / 'host')
    parent = tmp_path / 'Projects'
    clock = lambda: 1793354400.0
    locations = configured_locations(paths, str(parent))
    prefs = Prefs(paths.sessions)
    original = locations.create('Research', date='2026-10-30', preferences=prefs,
        actor='user', clock=clock)
    prefs.point(original.session)
    journal = session_root(original) / original.session / 'journal.jsonl'
    before = journal.read_bytes()
    original_locator = locations.registry.locations()[original.origin]
    locations.close()
    moved = tmp_path / 'Disconnected Research'
    original.path.rename(moved)
    locations = configured_locations(paths, str(parent))
    try:
        resumed = SessionStore.resume(paths.sessions, clock=clock, locations=locations)
        assert resumed.sid != original.session and resumed.name == 'Project recovery'
        assert locations.registry.locations()[original.origin] == original_locator
        assert resumed.catalogue.get(original.session).unavailable
        assert not original.path.exists()
        assert not (paths.sessions / original.session).exists()
        assert (moved / 'history/sessions' / original.session / 'journal.jsonl').read_bytes() == before
        count = len(locations.handles())
        again = SessionStore.resume(paths.sessions, clock=clock, locations=locations)
        assert again.sid == resumed.sid and len(locations.handles()) == count
        assert not (paths.home / 'agent-projects.json').exists()
    finally:
        locations.close()
