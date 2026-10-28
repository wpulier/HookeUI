"""Synthetic native signatures over real Project grants and HTTP; not a packaged UI claim."""
import base64

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from orchestrator.door.project_grants import GrantConfirmations, message
from orchestrator.door.projects import access
from orchestrator.door.connections import Refused
from tests.test_agent_door import connect, bearer, STUDIO, WINDOW, _window_key, window_key
from tests.test_agent_projects import project_door, run


def sign(choice, key=WINDOW, *, cancel=False):
    return {'id': choice['id'], 'signature': base64.b64encode(key.sign(message(choice, cancel=cancel), ec.ECDSA(hashes.SHA256()))).decode(),
            **({"answer":"cancel"} if cancel else {})}


@pytest.fixture
def shared(project_door):
    client, built = project_door
    owner = connect(client)
    peer = connect(client, label='Peer', secret='peer-grant-secret-for-test')
    sid = run(client, bearer(owner['token']), 'project.create', {'name':'Shared corpus'}, 'grant-project')['outcome']['project']['session']
    return client, built, peer, {'session':sid, 'connection':peer['connection']['id'], 'role':'read'}


def test_native_grant_is_one_use_and_bound_to_exact_choice(shared):
    client, built, peer, value = shared
    prepared = client.post('/api/agent-project-access/prepare', json=value)
    assert prepared.status_code == 200, prepared.text
    choice = prepared.json()['choice']
    assert choice['projectName'] == 'Shared corpus' and choice['label'] == 'Peer'
    actor = peer['connection']['actor'] if 'actor' in peer['connection'] else 'agent:'+peer['connection']['id']
    assert actor not in access(built).rows()[value['session']]['grants']
    assert client.post('/api/agent-project-access', json=value, headers=STUDIO).status_code == 403
    for field, changed in [('role','contribute'), ('session','2026-01-01'), ('connection','ac_'+'f'*24)]:
        assert client.post('/api/agent-project-access/confirm', json=sign({**choice, field:changed})).status_code == 403
    assert client.post('/api/agent-project-access/confirm', json=sign(choice, _window_key()[0])).status_code == 403
    answer = sign(choice)
    assert client.post('/api/agent-project-access/confirm', json=answer).status_code == 200
    assert access(built).rows()[value['session']]['grants'][actor] == 'read'
    revoke = client.post('/api/agent-project-access/prepare', json={**value,'role':None}).json()['choice']
    assert client.post('/api/agent-project-access/confirm', json=sign(revoke)).status_code == 200
    assert client.post('/api/agent-project-access/confirm', json=answer).status_code == 410
    assert actor not in access(built).rows()[value['session']]['grants']


def test_expiry_restart_and_changed_authority_refuse(shared):
    _, built, peer, value = shared
    clock = [0.0]
    confirms = GrantConfirmations(built, built.agent_connections, clock=lambda:clock[0])
    choice = confirms.prepare(value)
    with pytest.raises(Refused):
        GrantConfirmations(built, built.agent_connections).confirm(sign(choice))
    clock[0] = 301
    with pytest.raises(Refused):
        confirms.confirm(sign(choice))
    choice = confirms.prepare(value)
    access(built).grant(value['session'], 'agent:'+peer['connection']['id'], 'contribute', authority='native')
    with pytest.raises(ValueError, match='changed while'):
        confirms.confirm(sign(choice))
    choice = confirms.prepare(value)
    built.agent_connections.disconnect(peer['connection']['id'])
    with pytest.raises(ValueError, match='revoked'):
        confirms.confirm(sign(choice))


def test_development_cannot_impersonate_native_owner(shared,monkeypatch):
    client, built, _, value = shared
    monkeypatch.delenv('HOOKE_CONFIRM_KEY')
    monkeypatch.setenv('HOOKE_DEV_CONFIRM','1')
    assert client.post('/api/agent-project-access/prepare',json=value,headers=STUDIO).status_code == 403


def test_repeated_cancel_releases_capacity_without_granting(shared):
    client, built, peer, value = shared
    for _ in range(6):
        prepared = client.post('/api/agent-project-access/prepare',json=value)
        assert prepared.status_code == 200
        choice = prepared.json()['choice']
        cancelled = sign(choice, cancel=True)
        # A cancel signature cannot be reinterpreted as a grant.
        assert client.post('/api/agent-project-access/confirm',json={k:v for k,v in cancelled.items() if k!='answer'}).status_code == 403
        assert client.post('/api/agent-project-access/confirm',json=cancelled).status_code == 200
    assert 'agent:'+peer['connection']['id'] not in access(built).rows()[value['session']]['grants']
    choice = client.post('/api/agent-project-access/prepare',json=value).json()['choice']
    assert client.post('/api/agent-project-access/confirm',json=sign(choice)).status_code == 200


def test_user_project_requires_owner_confirmation_and_keeps_creator(project_door):
    client, built = project_door
    peer = connect(client, label='Outside researcher')
    sid = built.day.sid
    actor = 'agent:' + peer['connection']['id']
    headers = bearer(peer['token'])
    assert sid not in access(built).rows()
    listing = client.get('/api/agent-project-access', headers=STUDIO).json()
    assert any(p['session'] == sid and p['by'] == 'user' for p in listing['projects'])
    assert sid not in access(built).rows()  # viewing Settings creates no grant
    assert run(client, headers, 'project.list', {}, 'before-grant-list')['outcome']['projects'] == []
    value = {'session': sid, 'connection': peer['connection']['id'], 'role': 'read'}
    choice = client.post('/api/agent-project-access/prepare', json=value).json()['choice']
    assert sid not in access(built).rows()  # preparing consent grants nothing
    assert client.post('/api/agent-project-access/confirm', json=sign(choice)).status_code == 200
    assert access(built).rows()[sid]['by'] == 'user'
    assert access(built).role(sid, actor) == 'read'
    assert run(client, headers, 'project.read', {}, 'after-grant-read', sid)['outcome']['project']['session'] == sid
    assert built.day.sid == sid
    revoke = client.post('/api/agent-project-access/prepare', json={**value, 'role': None}).json()['choice']
    assert client.post('/api/agent-project-access/confirm', json=sign(revoke)).status_code == 200
    assert access(built).role(sid, actor) is None
    assert access(built).rows()[sid]['by'] == 'user'
    assert built.day.catalogue.get(sid) is not None
    assert run(client, headers, 'project.list', {}, 'after-revoke-list')['outcome']['projects'] == []


@pytest.mark.parametrize('sid', [None, '../outside', '1999-01-01'])
def test_missing_or_invalid_user_project_does_not_gain_registry_record(project_door, sid):
    client, built = project_door
    peer = connect(client)
    value = {'session': sid, 'connection': peer['connection']['id'], 'role': 'contribute'}
    assert client.post('/api/agent-project-access/prepare', json=value).status_code in (400, 404)
    assert access(built).rows() == {}


def test_source_owner_can_share_existing_user_project_without_navigation(project_door, monkeypatch):
    client, built = project_door
    peer = connect(client)
    sid = built.day.sid
    monkeypatch.delenv('HOOKE_CONFIRM_KEY'); monkeypatch.setenv('HOOKE_DEV_CONFIRM', '1')
    value = {'session': sid, 'connection': peer['connection']['id'], 'role': 'contribute'}
    assert client.post('/api/agent-project-access', json=value, headers=STUDIO).status_code == 200
    actor = 'agent:' + peer['connection']['id']
    assert access(built).role(sid, actor) == 'contribute'
    assert access(built).rows()[sid]['by'] == 'user'
    assert built.day.sid == sid
    # A token alone cannot use the owner route, even when it already contributes.
    assert client.post('/api/agent-project-access', json={**value, 'role':'read'}, headers=bearer(peer['token'])).status_code == 403
