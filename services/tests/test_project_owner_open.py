"""Explicit owner launch of real retained stores with synthetic evidence, no UI claim."""
import json
import pytest
from orchestrator.paths import Paths
from orchestrator.project_launch import configured_locations
from orchestrator.project_roots import ProjectLocationError, ProjectRoot, writer
from orchestrator.project_repository import document_root
from orchestrator.scientific_projects import ProjectStore
from orchestrator.day.store import SessionStore
from orchestrator.door.projects import ProjectAccess
from tests.test_project_launch import relocated


def source(tmp_path):
    _, _, root, moved, before = relocated(tmp_path)
    store = ProjectStore(document_root(ProjectRoot.open(moved)))
    saved = store.create(root.session, {'kind': 'scene', 'title': 'Accepted evidence',
                                      'state': {}, 'resources': [], 'runtimes': []})
    return root, moved, before, saved


def test_owner_opens_foreign_working_folder_without_inheriting_agents(tmp_path):
    root, moved, before, saved = source(tmp_path)
    host = Paths.under(tmp_path / 'new-host')
    other = SessionStore(host.sessions, session='2001-01-01')
    other._record({'type': 'note', 'text': 'Unrelated retained Project'})
    locations = configured_locations(host, str(tmp_path / 'new-projects'), opening=str(moved))
    try:
        resumed = SessionStore.resume(host.sessions, locations=locations)
        assert resumed.sid == root.session
        assert resumed.journal.path.read_bytes() == before
        assert ProjectStore(document_root(locations.project(root.session))).read(saved.id, root.session) == saved
        assert ProjectAccess(host.home).rows() == {}
        assert not ProjectAccess(host.home).path.exists()
        assert other.journal.path.is_file()
    finally:
        locations.close()


@pytest.mark.parametrize('collision', ['legacy', 'agent-access', 'portable'])
def test_owner_open_never_adopts_colliding_handles_or_permissions(tmp_path, collision):
    root, moved, before, _ = source(tmp_path)
    host = Paths.under(tmp_path / 'new-host')
    parent = tmp_path / 'new-projects'
    if collision == 'legacy':
        SessionStore(host.sessions, session=root.session)
    elif collision == 'agent-access':
        ProjectAccess(host.home).remember(root.session, 'agent:ac_' + 'a' * 24)
    else:
        from orchestrator.project_repository import create
        from orchestrator.project_roots import ProjectRegistry
        from orchestrator.day.prefs import Prefs
        unrelated = create(parent, 'Different origin', session=root.session, host_preferences=Prefs(host.sessions))
        ProjectRegistry(host.home).register(unrelated)
    registry = host.home / 'project-locations.json'
    old = registry.read_bytes() if registry.exists() else None
    with pytest.raises(ProjectLocationError, match='host handle'):
        configured_locations(host, str(parent), opening=str(moved))
    assert (registry.read_bytes() if registry.exists() else None) == old
    assert (moved / 'history/sessions' / root.session / 'journal.jsonl').read_bytes() == before


def test_owner_open_refuses_active_writer_or_damaged_history(tmp_path):
    root, moved, _, saved = source(tmp_path)
    host = Paths.under(tmp_path / 'new-host')
    selected = ProjectRoot.open(moved)
    with writer(selected), pytest.raises(ProjectLocationError, match='read-only'):
        configured_locations(host, str(tmp_path / 'new-projects'), opening=str(moved))
    store = ProjectStore(document_root(selected))
    # Corrupt an accepted revision; valid manifest/owner alone is insufficient.
    # Pin the actual accepted document rather than relying on filename order.
    files = [p for p in (store.root / saved.id).rglob('*.json') if p.name != 'head.json']
    assert files
    files[0].write_text('{}')
    with pytest.raises((ProjectLocationError, ValueError)):
        configured_locations(host, str(tmp_path / 'new-projects'), opening=str(moved))
    assert not (host.home / 'project-locations.json').exists()


def test_owner_open_snapshot_requires_explicit_working_copy(tmp_path):
    from orchestrator.project_folder_copy import copy_snapshot, restore_snapshot, verify_snapshot
    root, moved, before, _ = source(tmp_path)
    snapshot = copy_snapshot(ProjectRoot.open(moved), tmp_path / 'Snapshot')
    retained = verify_snapshot(snapshot.path)
    host = Paths.under(tmp_path / 'new-host')
    with pytest.raises(ProjectLocationError, match='retained snapshot'):
        configured_locations(host, str(tmp_path / 'new-projects'), opening=str(snapshot.path))
    restored = restore_snapshot(snapshot.path, tmp_path / 'Restored')
    locations = configured_locations(host, str(tmp_path / 'new-projects'), opening=str(restored.path))
    try:
        assert SessionStore.resume(host.sessions, locations=locations).sid == root.session
        assert verify_snapshot(snapshot.path) == retained
        assert ProjectAccess(host.home).rows() == {}
    finally:
        locations.close()


def test_owner_open_known_origin_elsewhere_requires_explicit_relocation(tmp_path):
    paths, parent, root, moved, before = relocated(tmp_path)
    with pytest.raises((ProjectLocationError, FileNotFoundError)):
        configured_locations(paths, str(parent), opening=str(moved))
    assert json.loads((paths.home / 'project-locations.json').read_text())['locations'][root.origin]['path'] == str(root.path)


def test_open_and_reopen_are_distinct_and_require_portable_configuration(tmp_path):
    host = Paths.under(tmp_path / 'host')
    with pytest.raises(ProjectLocationError, match='not both'):
        configured_locations(host, str(tmp_path), opening=str(tmp_path), reopen=str(tmp_path))
    with pytest.raises(ProjectLocationError, match='explicit'):
        configured_locations(host, None, opening=str(tmp_path))


@pytest.mark.parametrize('entrance', ['argument', 'environment'])
def test_ordinary_server_selects_owner_opened_project_without_grants(tmp_path, monkeypatch, entrance):
    import asyncio
    import sys
    from orchestrator import server
    root, moved, before, saved = source(tmp_path)
    host = Paths.under(tmp_path / 'new-host')
    monkeypatch.setattr(server.Paths, 'from_env', lambda: host)
    for name in ('HOOKE_OPEN_PROJECT', 'HOOKE_REOPEN_PROJECT', 'HOOKE_PROJECTS_DIR'):
        monkeypatch.delenv(name, raising=False)
    argv = ['orchestrator', '--no-gpu', '--project-folders', str(tmp_path / 'new-projects')]
    if entrance == 'argument':
        argv += ['--open-project', str(moved)]
    else:
        monkeypatch.setenv('HOOKE_OPEN_PROJECT', str(moved))
    monkeypatch.setattr(sys, 'argv', argv)
    def serve(app, **kwargs):
        built = app.state.app
        try:
            assert built.day.sid == root.session
            assert built.day.journal.path.read_bytes().startswith(before)
            assert built.hub.resources.projects.read(saved.id, root.session) == saved
            assert ProjectAccess(host.home).rows() == {}
        finally:
            built.day.locations.close()  # no background work starts in this transport fixture
            asyncio.run(built.stop())
    monkeypatch.setattr(server.uvicorn, 'run', serve)
    server.main()
