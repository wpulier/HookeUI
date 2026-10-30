"""Actual isolated accepted records/door transport; synthetic connection consent."""
import threading

import pytest

from orchestrator.door import schema
from orchestrator.project_dependency_access import inspect, unavailable
from orchestrator.project_repository import document_root
from orchestrator.project_roots import ProjectLocationError
from orchestrator.scientific_projects import ProjectStore
from tests.test_project_folder_copy import setup
from tests.test_project_record_dependencies import study
from tests.test_agent_projects import project_door, run, STUDIO
from tests.test_agent_door import connect, bearer, window_key  # noqa: F401
from tests.waiting import until


def test_available_review_keeps_exact_heads_and_never_changes_history(tmp_path):
    root, _, first, second = setup(tmp_path)
    store = ProjectStore(document_root(root))
    effort = study(store, first)
    before = [(s.id, s.revision, s.digest) for s in store.list(root.session)]
    result = inspect(store, root.session)
    schema.check(result, 'project-dependencies')
    assert result['availability'] == 'available' and result['completeProject'] is False
    assert result['documents'] == 2 and result['revisions'] == 3
    assert result['referencesChecked'] == 1 and not result['issues']
    assert {r['id'] for r in result['basis']} == {first.id, effort.id}
    assert before == [(s.id, s.revision, s.digest) for s in store.list(root.session)]


def test_new_accepted_revision_refuses_a_stale_review(tmp_path, monkeypatch):
    root, _, first, _ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    original = store.scan
    calls = 0
    def changed(session):
        nonlocal calls
        calls += 1
        if calls == 2:
            store.create(session, {'kind': 'note', 'title': 'New work', 'resources': [],
                                  'runtimes': [], 'state': {'text': 'Arrived during review'}})
        return original(session)
    monkeypatch.setattr(store, 'scan', changed)
    with pytest.raises(ProjectLocationError, match='changed during inspection'):
        inspect(store, root.session)
    assert store.read(first.id, root.session, revision=first.revision) == first


def test_unavailable_contract_cannot_claim_complete():
    result = unavailable('2026-10-30')
    schema.check(result, 'project-dependencies')
    assert result['availability'] == 'unavailable' and result['basis'] == []
    with pytest.raises(ValueError):
        schema.check({**result, 'completeProject': True}, 'project-dependencies')


def test_dependency_action_discovery_scope_and_idempotent_receipt(project_door, monkeypatch):
    from orchestrator.door import listing
    from orchestrator import agent_runnable
    client, built = project_door
    monkeypatch.setattr(listing, 'gather', lambda built: [])
    owner = bearer(connect(client)['token'])
    outsider = bearer(connect(client, label='Other', secret='unrelated-dependencies-0001')['token'])
    session = run(client, owner, 'project.create', {'name': 'Evidence'}, 'dependency-create')['outcome']['project']['session']
    assert [t['id'] for t in client.get('/api/agent/v1/tools?q=project.dependencies', headers=owner).json()['tools']] == ['project.dependencies']
    result = run(client, owner, 'project.dependencies', {}, 'dependency-review', session)
    schema.check(result['outcome']['dependencies'], 'project-dependencies')
    assert result['outcome']['dependencies']['basis'] == []
    assert run(client, owner, 'project.dependencies', {}, 'dependency-review', session)['id'] == result['id']
    request = {'tool': 'project.dependencies', 'args': {}, 'project': session, 'requestKey': 'unrelated-dependency'}
    assert client.post('/api/agent/v1/operations', headers=outsider, json=request).status_code == 403
    monkeypatch.delitem(agent_runnable.DOOR_RESOURCES, 'project.dependencies')
    assert client.get('/api/agent/v1/tools?q=project.dependencies', headers=owner).json()['tools'] == []
    assert client.post('/api/agent/v1/operations', headers=owner, json=request).status_code == 403


def test_reader_revoked_during_review_does_not_receive_results(project_door, monkeypatch):
    from orchestrator import project_dependency_access
    client, built = project_door
    owner = bearer(connect(client)['token'])
    reader = connect(client, label='Reader', secret='reader-dependencies-0001')
    headers = bearer(reader['token'])
    session = run(client, owner, 'project.create', {'name': 'Evidence'}, 'reader-project')['outcome']['project']['session']
    monkeypatch.delenv('HOOKE_CONFIRM_KEY'); monkeypatch.setenv('HOOKE_DEV_CONFIRM', '1')
    grant = {'session': session, 'connection': reader['connection']['id'], 'role': 'read'}
    assert client.post('/api/agent-project-access', headers=STUDIO, json=grant).status_code == 200
    entered, resume = threading.Event(), threading.Event()
    original = project_dependency_access.inspect
    def held(*args, **kwargs):
        entered.set()
        assert resume.wait(5), 'test release did not arrive'
        return original(*args, **kwargs)
    monkeypatch.setattr(project_dependency_access, 'inspect', held)
    request = {'tool': 'project.dependencies', 'args': {}, 'project': session, 'requestKey': 'reader-inspection'}
    response = client.post('/api/agent/v1/operations', headers=headers, json=request)
    assert response.status_code == 202
    op = response.json()['operation']['id']
    try:
        assert entered.wait(5)
        assert client.post('/api/agent-project-access', headers=STUDIO, json={**grant, 'role': None}).status_code == 200
    finally:
        resume.set()
    def stopped():
        result = client.get('/api/agent/v1/operations/' + op, headers=owner).json()['operation']
        return result if result['state'] == 'failed' else None
    result = until(stopped)
    assert result['outcome']['code'] == 'project-access-refused'
    assert 'dependencies' not in result['outcome']
    assert client.get('/api/agent/v1/operations/' + op, headers=headers).status_code == 403


def test_cancellation_waits_for_read_worker_to_stop(project_door, monkeypatch):
    from orchestrator import project_dependency_access
    client, built = project_door
    headers = bearer(connect(client)['token'])
    session = run(client, headers, 'project.create', {'name': 'Evidence'}, 'cancel-project')['outcome']['project']['session']
    entered, resume, exited = threading.Event(), threading.Event(), threading.Event()
    original = project_dependency_access.inspect
    def held(*args, **kwargs):
        try:
            entered.set()
            assert resume.wait(5)
            return original(*args, **kwargs)
        finally:
            exited.set()
    monkeypatch.setattr(project_dependency_access, 'inspect', held)
    response = client.post('/api/agent/v1/operations', headers=headers, json={
        'tool': 'project.dependencies', 'args': {}, 'project': session, 'requestKey': 'cancel-inspection'})
    assert response.status_code == 202
    op = response.json()['operation']['id']
    try:
        assert entered.wait(5)
        stop = client.post('/api/agent/v1/operations/' + op + '/cancel', headers=headers)
        assert stop.status_code == 200 and not exited.is_set()
    finally:
        resume.set()
    def ended():
        result = client.get('/api/agent/v1/operations/' + op, headers=headers).json()['operation']
        return result if result['state'] == 'cancelled' else None
    result = until(ended)
    assert exited.is_set() and 'dependencies' not in result['outcome']
