"""Real isolated journals/locations and door transport; synthetic connection consent."""
from types import SimpleNamespace

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.door import schema
from orchestrator.project_roots import ProjectLocationError, writer
from orchestrator.project_storage_status import describe, unavailable
from tests.test_project_locations import setup
from tests.test_agent_projects import project_door, run
from tests.test_agent_door import connect, bearer, window_key


def test_legacy_status_never_advertises_shared_home_as_portable(tmp_path):
    day = SessionStore(tmp_path / 'sessions', session='2026-10-29')
    before = day.journal.path.read_bytes()
    status = describe(day, day.sid)
    schema.check(status, 'project-storage')
    assert status['layout'] == 'legacy' and status['availability'] == 'available'
    assert status['origin'] and status['location'] is None
    assert status['collection']['state'] == 'not-assessed'
    assert day.journal.path.read_bytes() == before
    assert not (tmp_path / 'project-locations.json').exists()


def test_missing_legacy_origin_is_unavailable_and_never_minted(tmp_path):
    root = tmp_path / 'sessions'
    folder = root / '2020-01-01'
    folder.mkdir(parents=True)
    journal = folder / 'journal.jsonl'
    original = b'{"type":"session-started","session":"2020-01-01","name":"Old"}\n'
    journal.write_bytes(original)
    result = describe(SimpleNamespace(root=root), '2020-01-01')
    schema.check(result, 'project-storage')
    assert result['origin'] is None and result['availability'] == 'unavailable'
    assert journal.read_bytes() == original


def test_portable_status_checks_owner_without_taking_writer_and_preserves_missing_location(tmp_path):
    day, locations = setup(tmp_path)
    try:
        project = day.create_retained('Research', actor='user')
        root = locations.project(project.sid)
        # A second read-only host can inspect a held Project without obtaining a lease.
        from orchestrator.project_locations import ProjectLocations
        other = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            status = describe(SimpleNamespace(root=day.root, locations=other), project.sid)
            schema.check(status, 'project-storage')
            assert status['origin'] == root.origin and status['location'] == str(root.path)
            assert status['availability'] == 'available' and not other._leases
            assert status['collection']['state'] == 'not-assessed'
            with pytest.raises(ProjectLocationError, match='read-only'):
                with writer(root): pass
        finally:
            other.close()
        root.path.rename(tmp_path / 'Moved elsewhere')
        missing = describe(day, project.sid)
        schema.check(missing, 'project-storage')
        assert missing['availability'] == 'unavailable'
        assert missing['origin'] == root.origin and missing['location'] == str(root.path)
        assert not root.path.exists() and not (locations.legacy / project.sid).exists()
    finally:
        locations.close()


def test_host_unavailability_uses_shared_meaning():
    status = unavailable('2026-10-29')
    schema.check(status, 'project-storage')
    assert status['layout'] == status['availability'] == 'unavailable'
    assert status['location'] is None and status['origin'] is None
    assert status['collection']['state'] == 'not-assessed'
    with pytest.raises(ValueError):
        schema.check({**status, 'collection': {'state':'complete','reason':'has a folder'}}, 'project-storage')


def test_storage_discovery_and_read_require_project_permission(project_door, monkeypatch):
    from orchestrator.door import listing
    from orchestrator import agent_runnable
    client, built = project_door
    monkeypatch.setattr(listing, 'gather', lambda built: [])
    a = bearer(connect(client)['token'])
    b = bearer(connect(client, label='Unrelated', secret='unrelated-storage-fixture-secret')['token'])
    project = run(client, a, 'project.create', {'name':'Retained'}, 'storage-create')['outcome']['project']['session']
    tools = client.get('/api/agent/v1/tools?q=project.storage', headers=a).json()['tools']
    assert [row['id'] for row in tools] == ['project.storage']
    status = run(client, a, 'project.storage', {}, 'storage-read', project)['outcome']['storage']
    assert status == describe(built.day, project)
    schema.check(status, 'project-storage')
    refused = client.post('/api/agent/v1/operations', headers=b,
        json={'tool':'project.storage','args':{},'project':project,'requestKey':'unrelated-read'})
    assert refused.status_code == 403
    monkeypatch.delitem(agent_runnable.DOOR_RESOURCES, 'project.storage')
    assert client.get('/api/agent/v1/tools?q=project.storage', headers=a).json()['tools'] == []
    assert client.post('/api/agent/v1/operations', headers=a,
        json={'tool':'project.storage','args':{},'project':project,'requestKey':'disabled-read'}).status_code == 403


def test_leased_project_with_changed_owner_never_reports_available(tmp_path):
    import json
    day, locations = setup(tmp_path)
    try:
        project = day.create_retained('Research', actor='user')
        root = locations.project(project.sid)
        journal = project.journal.path
        lines = journal.read_text().splitlines()
        origin = json.loads(lines[0]); origin['origin'] = 'f' * 32
        lines[0] = json.dumps(origin)
        journal.write_text('\n'.join(lines) + '\n')
        result = describe(day, project.sid)
        assert result['availability'] == 'unavailable'
        assert result['origin'] == root.origin and 'do not match' in result['reason']
    finally:
        locations.close()


def test_successful_status_reports_verified_location_when_registry_moves(tmp_path, monkeypatch):
    from orchestrator.project_locations import ProjectLocations
    from orchestrator.project_roots import ProjectRoot
    day, locations = setup(tmp_path)
    project = day.create_retained('Research', actor='user')
    root = locations.project(project.sid)
    locations.close()
    reader = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    original = reader.project
    def moved_before_resolution(session):
        moved = tmp_path / 'Moved during status read'
        root.path.rename(moved)
        reader.registry.register(ProjectRoot.open(moved), replace_inactive=True)
        return original(session)
    monkeypatch.setattr(reader, 'project', moved_before_resolution)
    try:
        result = describe(SimpleNamespace(root=day.root, locations=reader), project.sid)
        assert result['availability'] == 'available'
        assert result['location'] == str(tmp_path / 'Moved during status read')
        assert result['origin'] == root.origin
    finally:
        reader.close()
