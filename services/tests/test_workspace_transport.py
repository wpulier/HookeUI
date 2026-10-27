"""Synthetic real ASGI/WebSocket/native-source transport; no lifespan or model.

The local test driver is authorized. This is not browser paint, camera or
provider-input evidence. Production App, origin binding and source stores run.
"""
import asyncio
import copy

import pytest
from fastapi.testclient import TestClient

from orchestrator.capture import RUN
from orchestrator.paths import Paths
from orchestrator.presentations import PresentationError, ResourceRef
from orchestrator.server import create_app
from orchestrator.ticket import issue, using
from tests.test_workspace_actions_app import saved_picture


ORIGIN = 'http://127.0.0.1:8770'
GRANT_HEADER = 'X-Micromind-Workspace-Grant'


def api(tmp_path):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    return web, TestClient(web)  # deliberately no startup/lifespan


def hello(workspace='workspace-a', client='client-a', page='page-a'):
    return {'type': 'workspace-hello', 'schema': 'workspace-origin-v2', 'requestId': 'hello-request',
            'workspaceId': workspace, 'clientId': client, 'pageEpoch': page}


def reply_for(ws, request='hello-request'):
    for _ in range(64):
        reply = ws.receive_json()
        if reply.get('requestId') == request:
            return reply
    pytest.fail('bounded initial snapshot never reached the actual handshake reply')


def ready(ws, message=None):
    ws.send_json(message or hello())
    reply = reply_for(ws)
    assert reply.get('type') == 'workspace-ready', f'MISSING_FEATURE: accepted server handshake: {reply}'
    assert reply['schema'] == 'workspace-origin-v2' and reply['appRunId'] == RUN
    assert isinstance(reply['connectionGrant'], str) and 32 <= len(reply['connectionGrant']) <= 128
    return reply


def test_server_handshake_assigns_socket_epoch_and_replay_retains_original_binding(tmp_path):
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as ws:
        first = ready(ws)
        assert first['client'] == {**{k: hello()[k] for k in ('workspaceId', 'clientId', 'pageEpoch')},
                                  'connectionEpoch': first['client']['connectionEpoch']}
        assert first['client']['connectionEpoch'] and first['client']['connectionEpoch'] != 'page-a'
        second = ready(ws, copy.deepcopy(hello()))
        assert second == first
        socket, = web.state.hub.clients
        context = web.state.app.workspace_context(socket, request_id='original-ask', owner='original-action')
        assert (context.workspace_id, context.client_id, context.client_epoch, context.connection_epoch) == (
            'workspace-a', 'client-a', 'page-a', first['client']['connectionEpoch'])
        changed = hello(workspace='different-workspace')
        ws.send_json(changed)
        refused = reply_for(ws)
        assert refused['ok'] is False and refused['code'] == 'CLIENT_MISMATCH'
        assert web.state.app.workspace_context(socket, request_id='still-original', owner='action').client == context.client


@pytest.mark.parametrize('field', ['connectionEpoch', 'verified', 'client'])
def test_browser_cannot_supply_connection_or_capture_authority_in_handshake(tmp_path, field):
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as ws:
        message = hello()
        message[field] = True if field == 'verified' else {'verified': True} if field == 'client' else 'forged-socket'
        ws.send_json(message)
        refused = reply_for(ws)
        assert refused['ok'] is False and refused['code'] == 'INVALID_REPORT'
        assert not getattr(web.state.app, 'presentations', None) or web.state.app.presentations.list('workspace-a') == ()


@pytest.mark.parametrize('header', [None, 'unknown-opaque-grant'])
def test_native_source_url_without_accepted_connection_grant_is_refused(tmp_path, header):
    _, http = api(tmp_path)
    headers = {} if header is None else {GRANT_HEADER: header}
    response = http.get('/api/presentation-sources/' + 'a' * 32, headers=headers)
    assert response.status_code == 403, 'a raw token URL must not authorize a native source read'
    assert response.json()['code'] == 'CLIENT_MISMATCH'


def prepare(web, accepted, value=70):
    app = web.state.app
    store = getattr(app.services, 'prepared_sources', None)
    assert store is not None, 'MISSING_FEATURE: actual App native prepared-source owner'
    socket = next(socket for socket in web.state.hub.clients
                  if app.workspace_context(socket, request_id='inspect-origin', owner='inspection').client_id
                  == accepted['client']['clientId'])
    entry, data = saved_picture(app, value)
    ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
    context = app.workspace_context(socket, request_id=ticket.admission.request_id, owner='prepared-http-action')

    async def work():
        with using(ticket):
            return await store.prepare(ResourceRef(entry.day, entry.id, entry.version, 0), context)
    try:
        lease = asyncio.run(work())
    finally:
        ticket.seal()
    return store, lease, context, data


def test_http_native_bytes_require_the_exact_active_origin_and_retire_on_disconnect(tmp_path):
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as first_ws:
        first = ready(first_ws)
        store, lease, context, data = prepare(web, first)
        try:
            url = '/api/presentation-sources/' + lease.token
            response = http.get(url, headers={GRANT_HEADER: first['connectionGrant']})
            assert response.status_code == 200 and response.content == data
            assert response.headers['content-type'] == lease.to_wire()['mediaType']
            assert response.headers['cache-control'] == 'no-store'
            assert store.get(lease.token, context) == data
            with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as second_ws:
                peer = ready(second_ws, hello('workspace-b', 'client-b', 'page-b'))
                refused = http.get(url, headers={GRANT_HEADER: peer['connectionGrant']})
                assert refused.status_code == 403 and refused.json()['code'] == 'context_mismatch'
                assert store.get(lease.token, context) == data
            refused = http.get(url, headers={GRANT_HEADER: first['connectionGrant'], 'origin': 'https://example.com'})
            assert refused.status_code == 403 and store.get(lease.token, context) == data
        finally:
            lease.release()
    retired = http.get('/api/presentation-sources/' + 'a' * 32, headers={GRANT_HEADER: first['connectionGrant']})
    assert retired.status_code == 403 and retired.json()['code'] == 'CLIENT_MISMATCH'


def test_reconnect_retires_old_socket_grant_without_changing_the_page_epoch(tmp_path):
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as old_ws:
        old = ready(old_ws)
        with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as new_ws:
            new = ready(new_ws)
            assert new['client']['pageEpoch'] == old['client']['pageEpoch']
            assert new['client']['connectionEpoch'] != old['client']['connectionEpoch']
            assert new['connectionGrant'] != old['connectionGrant']
            response = http.get('/api/presentation-sources/' + 'a' * 32, headers={GRANT_HEADER: old['connectionGrant']})
            assert response.status_code == 403 and response.json()['code'] == 'CLIENT_MISMATCH'


def test_delayed_hello_from_disconnected_socket_cannot_retire_its_active_replacement(tmp_path):
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as old_ws:
        ready(old_ws)
        old_socket, = web.state.hub.clients
    # A server command task may run after its originating WebSocket closes.
    # Use the actual retired Client rather than an identity dictionary.
    assert old_socket not in web.state.hub.clients
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as replacement:
        accepted = ready(replacement)
        current_socket, = web.state.hub.clients
        original = web.state.app.workspace_context(current_socket, request_id='before', owner='active')
        refused = web.state.workspace_transport.hello(old_socket, hello())
        assert refused.get('ok') is False and refused.get('code') == 'CLIENT_MISMATCH'
        after = web.state.app.workspace_context(current_socket, request_id='after', owner='active')
        assert after.client == original.client
        assert after.connection_epoch == accepted['client']['connectionEpoch']


@pytest.mark.parametrize('broken_send', [False, True])
def test_native_response_owns_its_borrow_until_actual_asgi_send_finishes(tmp_path, broken_send):
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as ws:
        accepted = ready(ws)
        store, lease, context, data = prepare(web, accepted)
        transport = getattr(web.state, 'workspace_transport', None)
        assert transport is not None, 'MISSING_FEATURE: accepted transport response owner'
        response = transport.image(lease.token, accepted['connectionGrant'])
        lease.release()  # only the actual HTTP borrow now owns the native bytes
        assert store.get(lease.token, context) == data
        sent = []

        async def send(message):
            assert store.get(lease.token, context) == data
            if broken_send:
                raise RuntimeError('synthetic HTTP transport failed')
            sent.append(message)

        async def consume():
            await response({'type': 'http', 'method': 'GET', 'asgi': {'spec_version': '2.4'}}, None, send)
        if broken_send:
            with pytest.raises(RuntimeError, match='synthetic HTTP transport failed'):
                asyncio.run(consume())
        else:
            asyncio.run(consume())
            assert b''.join(message.get('body', b'') for message in sent) == data
        with pytest.raises(PresentationError) as revoked:
            store.get(lease.token, context)
        assert revoked.value.code == 'delivery_revoked'
        assert response.body == b''


def test_canonical_screen_report_can_be_acknowledged_before_http_interaction(tmp_path):
    from tests.test_scientific_presentation import scientific_report
    web, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as ws:
        accepted = ready(ws)
        report = scientific_report()
        report.update(type='screen', client=accepted['client'], appRunId=accepted['appRunId'], requestId='before-scientific-http')
        for item in report['items']: item['workspaceId'] = accepted['client']['workspaceId']
        ws.send_json(report)
        reply = reply_for(ws, 'before-scientific-http')
        assert reply['ok'] is True, reply
        socket, = web.state.hub.clients
        assert web.state.hub.screen.pin(socket, request_id='http-after-ack').observation.wire()['seq'] == report['seq']


def test_project_arrangements_are_the_only_reconnect_owner(tmp_path, monkeypatch):
    """Actual handshake/core; snapshot I/O must not race Project layout restoration."""
    from orchestrator import presentations
    from tests.test_presentations import opened
    web, http = api(tmp_path)
    app = web.state.app
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as old_ws:
        old = ready(old_ws)
        store, lease, context, data = prepare(web, old)
        core = app.services.presentations
        old_socket, = web.state.hub.clients
        ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
        try:
            with using(ticket):
                live_context = app.workspace_context(old_socket, request_id=ticket.admission.request_id, owner='open-native')
                opened(presentations, core, live_context, lease.source)
        finally:
            ticket.close()
        lease.release()
        transport = app.workspace_transport
        old_socket, = web.state.hub.clients
        transport.save(old_socket, force=True)
        path = transport._path('workspace-a')
        snapshot = path.read_bytes()
        with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as new_ws:
            proposal = {**hello(page='new-page'), 'restoration': 'project-layout'}
            accepted = ready(new_ws, proposal)
            assert accepted['restoration'] == 'project-layout'
            assert not any(item.open for item in core.list('workspace-a'))
            assert path.read_bytes() == snapshot
            socket = transport.voice_client(accepted['connectionGrant'])
            # Neither explicit restore entry nor disconnect/save may replay or erase
            # the old native snapshot. Retained scientific bytes remain available.
            monkeypatch.setattr(transport, '_path', lambda _: pytest.fail('legacy snapshot accessed'))
            transport.begin_restore(socket)
            assert socket not in transport._restoring
            transport.save(socket, force=True)
            assert ready(new_ws, proposal) == accepted
            # A new connection can still prepare the retained scientific source.
            check = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
            context2 = app.workspace_context(socket, request_id=check.admission.request_id, owner='retained-check')
            async def reopen():
                with using(check):
                    return await store.prepare(lease.source, context2)
            try:
                lease2 = asyncio.run(reopen())
                try:
                    assert store.get(lease2.token, context2) == data
                finally:
                    lease2.release()
            finally:
                check.close()
            new_ws.send_json(hello(page='new-page'))
            assert reply_for(new_ws)['code'] == 'CLIENT_MISMATCH'
        monkeypatch.undo()
        assert path.read_bytes() == snapshot


@pytest.mark.parametrize('mode', [None, False, 'native', {}, 'project-layout-v99'])
def test_unknown_restoration_owner_is_refused_before_acceptance(tmp_path, mode):
    _, http = api(tmp_path)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as ws:
        ws.send_json({**hello(), 'restoration': mode})
        assert reply_for(ws)['code'] == 'INVALID_REPORT'
