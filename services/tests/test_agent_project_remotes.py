"""Agents and a Project's remotes (hooke-projects ticket 09): read where it is published and backed up, back it up
only where its owner already does, and ask the owner to publish. Real Project folders, a folder destination and the
door's own transport; synthetic connections. No network or Git."""
import asyncio

from orchestrator import agent_runnable
from orchestrator.commands import COMMANDS
from orchestrator.project_repository import ProjectRepository
from tests.test_agent_door import bearer, connect, window_key  # noqa: F401  (signs the test window's answers)
from tests.test_agent_projects import STUDIO, run
from tests.test_project_export import export_door  # noqa: F401  (the fixture)
from tests.waiting import until

SIGNOFF = {'rights': True, 'noIdentifiableHumanData': True, 'permanent': True}


def attempt(client, headers, tool, args, key, project):
    """Run an operation to its end, whatever that is."""
    response = client.post('/api/agent/v1/operations', json={'tool': tool, 'args': args, 'requestKey': key,
                                                               'project': project}, headers=headers)
    if response.status_code not in (200, 202):
        return response
    op = response.json()['operation']
    return until(lambda: (lambda r: r if r['state'] in ('succeeded', 'failed', 'interrupted', 'cancelled') else None)(
        client.get('/api/agent/v1/operations/' + op['id'], headers=headers).json()['operation']))


def owner(built, kind, **msg):
    return asyncio.run(COMMANDS[kind](built, None, {'type': kind, **msg}))


def setup_project(client, built, monkeypatch):
    from orchestrator.door import listing
    monkeypatch.setattr(listing, 'gather', lambda built: [])
    maker = connect(client)
    helper = connect(client, label='Helper', secret='remote-helper-secret-01')
    sid = run(client, bearer(maker['token']), 'project.create', {'name': 'Research'}, 'remote-create-01')['outcome']['project']['session']
    monkeypatch.delenv('HOOKE_CONFIRM_KEY'); monkeypatch.setenv('HOOKE_DEV_CONFIRM', '1')
    grant = {'session': sid, 'connection': helper['connection']['id'], 'role': 'read'}
    assert client.post('/api/agent-project-access', json=grant, headers=STUDIO).status_code == 200
    return sid, bearer(helper['token']), grant


def test_an_agent_backs_up_only_where_the_owner_already_does(export_door, monkeypatch, tmp_path):
    client, built = export_door
    sid, helper, grant = setup_project(client, built, monkeypatch)
    status = run(client, helper, 'project.remote.status', {}, 'remote-status-01', sid)['outcome']['remote']
    assert status['backups'] == [] and status['published'] == [] and status['publishRequests'] == []
    # A read grant may look, not back up.
    assert client.post('/api/agent/v1/operations', json={'tool': 'project.remote.backup', 'args': {}, 'project': sid,
                                                         'requestKey': 'remote-backup-01'}, headers=helper).status_code == 403
    assert client.post('/api/agent-project-access', json={**grant, 'role': 'contribute'}, headers=STUDIO).status_code == 200
    # With no backup set up by the owner there is nowhere to go, and the agent cannot name a new place.
    first = attempt(client, helper, 'project.remote.backup', {}, 'remote-backup-02', sid)
    assert first['state'] == 'failed' and 'no backup its owner set up' in str(first)
    built.day.open(sid, 'test')
    drive = str(tmp_path / 'Owner drive')
    assert owner(built, 'project-backup', session=sid, destination={'kind': 'folder', 'path': drive, 'create': True})['ok']
    elsewhere = attempt(client, helper, 'project.remote.backup', {'to': str(tmp_path / 'Agent drive')}, 'remote-backup-03', sid)
    assert elsewhere['state'] == 'failed' and not (tmp_path / 'Agent drive').exists()
    done = run(client, helper, 'project.remote.backup', {}, 'remote-backup-04', sid)['outcome']
    assert done['location'] == drive and done['backup']['commit']
    named = run(client, helper, 'project.remote.backup', {'to': drive}, 'remote-backup-05', sid)['outcome']
    assert named['backup']['commit'] != done['backup']['commit'] or named['backup']['uploadedBytes'] == 0
    events = ProjectRepository(built.day.locations.project(sid)).events()
    mine = [e for e in events if e.get('type') == 'project-backed-up']
    assert len(mine) == 3 and mine[0].get('by') is None and mine[1]['by'] and mine[1]['by'] == mine[2]['by']
    listed = run(client, helper, 'project.remote.status', {}, 'remote-status-02', sid)['outcome']['remote']['backups']
    assert [b['state'] for b in listed] == ['active']


def test_an_agent_asks_to_publish_and_only_the_owner_sends_it(export_door, monkeypatch, tmp_path):
    client, built = export_door
    sid, helper, grant = setup_project(client, built, monkeypatch)
    assert client.post('/api/agent/v1/operations', json={'tool': 'project.publish', 'args': {}, 'project': sid,
                                                         'requestKey': 'remote-publish-01'}, headers=helper).status_code == 403
    assert client.post('/api/agent-project-access', json={**grant, 'role': 'contribute'}, headers=STUDIO).status_code == 200
    asked = run(client, helper, 'project.publish', {'licence': 'CC0-1.0'}, 'remote-publish-02', sid)['outcome']
    publication = asked['publication']
    assert publication['state'] == 'awaiting-owner' and publication['licence'] == 'CC0-1.0' and not publication['blocked']
    assert 'Nothing is published until they review it' in asked['says']
    # Nothing left the Mac, and the owner sees the request.
    built.day.open(sid, 'test')
    [request] = owner(built, 'project-remote-status', session=sid)['publishRequests']
    assert request['reviewDigest'] == publication['reviewDigest'] and request['byLabel'] == 'Helper'
    review = owner(built, 'project-publish-review', session=sid, staging=request['staging'])['review']
    assert review['reviewDigest'] == publication['reviewDigest'] and review['licence'] == 'CC0-1.0'
    # The owner reviews and signs off: exactly what the agent prepared is published, and who asked is recorded.
    sent = owner(built, 'project-publish', session=sid, staging=request['staging'], reviewDigest=review['reviewDigest'],
                 signoff=SIGNOFF, destination={'kind': 'folder', 'path': str(tmp_path / 'Public'), 'create': True})
    assert sent['ok']
    published = [e for e in ProjectRepository(built.day.locations.project(sid)).events()
                 if e.get('type') == 'project-published']
    assert published[-1]['reviewDigest'] == publication['reviewDigest'] and published[-1]['requestedBy']
    assert owner(built, 'project-remote-status', session=sid)['publishRequests'] == []
    # Requests waiting for the owner are few: each holds a sealed copy of the Project on this Mac.
    for number in range(3):
        got = attempt(client, helper, 'project.publish', {}, f'remote-publish-1{number}', sid)
        assert got['state'] == 'succeeded', {k: got.get(k) for k in ('error', 'outcome', 'failure', 'refusal', 'says')}
    full = attempt(client, helper, 'project.publish', {}, 'remote-publish-20', sid)
    assert full['state'] == 'failed' and 'already wait for its owner' in str(full)
    # Sending, signing off and choosing where are the owner's commands, never the agent's.
    for command in ('project-publish', 'project-publish-review', 'project-backup', 'project-backup-delete',
                    'project-restore'):
        assert not agent_runnable.door_resource(command)


def test_a_sealed_copy_never_takes_an_operation_record_being_written(tmp_path):
    """An agent operation rewrites its record through a temporary file beside it; a copy made meanwhile skips that
    file, which is renamed away a moment later, and keeps every record itself."""
    from orchestrator.project_folder_copy import copy_snapshot
    from tests.test_project_remote import project
    root, *_ = project(tmp_path)
    operations = root.path / 'history' / 'operations'
    operations.mkdir(parents=True, exist_ok=True)
    (operations / 'op_0123456789abcdef01234567.json').write_text('{"state": "running"}')
    (operations / '.op_0123456789abcdef01234567.json.0a1b2c3d4e5f.tmp').write_text('{"state": "succ')
    copied = copy_snapshot(root, tmp_path / 'Copy')
    kept = sorted(p.name for p in (copied.path / 'history' / 'operations').iterdir())
    assert kept == ['op_0123456789abcdef01234567.json']
