"""Production HTTP/Screen/storage with a synthetic worker; no browser-paint evidence."""
import asyncio
import uuid

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
import httpx

from orchestrator.capture import Memory, Captures
from orchestrator.scientific_review_api import FrameResponse, register
from orchestrator.workspace_observation import ObservationError
from tests.test_scientific_reviews import ReviewsRig

ORIGIN = 'http://127.0.0.1:8770'
HEADERS = {'origin': ORIGIN, 'X-MicroscopeMind': 'scientific-review', 'X-Micromind-Workspace-Grant': 'accepted'}


def test_conflict_recovery_never_recommends_repeating_an_impossible_save():
    import json
    from orchestrator.scientific_saves import SaveConflict
    from orchestrator.scientific_review_api import error_response
    response = error_response(SaveConflict('Newer revision accepted'))
    result = json.loads(response.body)
    assert response.status_code == 409 and result['code'] == 'SCIENTIFIC_SAVE_CONFLICT'
    assert 'Check array save' in result['next'] and 'private edits remain' in result['next']
    assert 'Retry saving' not in result['next']


def test_history_save_failure_never_tells_the_user_to_rerun_code():
    import json
    from orchestrator.scientific_history import HistoryUnavailable
    from orchestrator.scientific_review_api import error_response
    response = error_response(HistoryUnavailable('History save failed'))
    body = json.loads(response.body)
    assert response.status_code == 503
    assert 'Retry saving Python history' in body['next']
    assert 'Do not automatically rerun' in body['next']


def test_job_memory_refusal_has_actionable_recovery_and_preserves_other_reservations(monkeypatch):
    import json
    from orchestrator import jobs, machine
    from orchestrator.loads import MARGIN_MB
    from orchestrator.scientific_review_api import error_response
    memory = jobs.JobMemory()
    monkeypatch.setattr(machine, 'free_mb', lambda: MARGIN_MB + 150)
    with memory.reserve(100):
        try:
            with memory.reserve(100):
                pytest.fail('Concurrent admission must not exceed the unchanged limit')
        except jobs.Failed as error:
            response = error_response(error)
        assert memory.reserved_mb == 100
        body = json.loads(response.body)
        assert response.status_code == 503 and body['code'] == 'SCIENTIFIC_MEMORY'
        assert 'Review running sessions' in body['next']
        assert 'unsaved' in body['next'] and 'Stop' in body['next']
    assert memory.reserved_mb == 0
    with memory.reserve(100):
        assert memory.reserved_mb == 100
    assert memory.reserved_mb == 0


def test_preview_memory_recovery_never_invites_repeating_completed_science():
    import json
    from orchestrator.capture import Refused
    from orchestrator.scientific_review_api import error_response
    response = error_response(Refused('Preview delivery needs memory'))
    body = json.loads(response.body)
    assert response.status_code == 503 and body['code'] == 'SCIENTIFIC_MEMORY'
    assert 'Refresh preview' in body['next'] and 'Do not rerun code or a plugin action' in body['next']
    assert 'Open session again' not in body['next'] and 'retry this action' not in body['next']


@contextmanager
def api(tmp_path):
    rig = ReviewsRig(tmp_path)
    app = FastAPI()
    memory = Memory()
    def accepted(grant):
        if grant != 'accepted': raise ObservationError('CLIENT_MISMATCH', 'connection not accepted')
        return rig.client
    hub = SimpleNamespace(screen=rig.screen)
    hub.captures = Captures(hub)
    hub.captures.memory = memory
    built = SimpleNamespace(scientific_reviews=rig.manager, day=SimpleNamespace(sid=rig.saved.session),
        workspace_transport=SimpleNamespace(voice_client=accepted),
        hub=hub)
    register(app, built, lambda origin, port: origin == ORIGIN and port == 8770, 'X-MicroscopeMind')
    yield rig, httpx.ASGITransport(app=app), memory


def opening(rig):
    return {'session': rig.saved.session, 'document': rig.saved.id, 'revision': rig.saved.revision, 'item': rig.item['id']}


def test_http_memory_refusal_retries_only_after_owned_reservation_is_released(tmp_path, monkeypatch):
    from orchestrator import jobs, machine
    from orchestrator.loads import MARGIN_MB
    room = jobs.JobMemory()
    monkeypatch.setattr(machine, 'free_mb', lambda: MARGIN_MB + 150)
    with api(tmp_path) as (rig, transport, _):
        @contextmanager
        def open_worker(origin):
            with room.reserve(100), rig.open_worker(origin) as worker:
                yield worker
        rig.manager._open_worker = open_worker
        async def go():
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=HEADERS) as http:
                try:
                    with room.reserve(100):
                        failed = await http.post('/api/scientific-reviews', json=opening(rig))
                        assert failed.status_code == 503 and failed.json()['code'] == 'SCIENTIFIC_MEMORY'
                        assert not rig.workers and room.reserved_mb == 100
                        assert (await http.get('/api/scientific-reviews')).json()['reviews'] == []
                    resumed = await http.post('/api/scientific-reviews', json=opening(rig))
                    assert resumed.status_code == 200 and len(rig.workers) == 1
                    assert room.reserved_mb == 100
                    stopped = await http.delete('/api/scientific-reviews/' + resumed.json()['review'])
                    assert stopped.status_code == 200 and room.reserved_mb == 0
                    assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
                finally:
                    await rig.manager.close()
        asyncio.run(go())


def test_http_review_render_interact_stop_preserves_saved_document(tmp_path):
    with api(tmp_path) as (rig, transport, memory):
        async def go():
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=HEADERS) as http:
                try:
                    response = await http.post('/api/scientific-reviews', json=opening(rig))
                    assert response.status_code == 200, response.text
                    identity = response.json()['review']
                    base = '/api/scientific-reviews/' + identity
                    response = await http.post(base + '/request', json={'request': {'op': 'render', 'width': 2, 'height': 3}, 'draw': None})
                    assert response.json()['frame']['frame'] == 'a' * 64, response.text
                    response = await http.get(base + '/frames/' + 'a' * 64)
                    assert response.status_code == 200, response.text
                    assert response.content == b'0' * 24
                    assert response.headers['cache-control'] == 'no-store'
                    assert memory.reserved['analysis'] == 0
                    rig.paint(identity)
                    response = await http.post(base + '/request', json={'request': {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64, 'source': '1+1'}, 'draw': 'draw-one'})
                    assert response.json()['console']['result'] == '2', response.text
                    assert (await http.get(base + '/frames/' + 'a' * 64)).status_code == 409
                    assert (await http.delete(base)).json() == {'ok': True}
                    assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
                    assert (await http.post(base + '/request', json={'request': {'op': 'render'}, 'draw': None})).status_code == 409
                finally: await rig.manager.close()
        asyncio.run(go())


@pytest.mark.parametrize('change', ['header', 'origin', 'grant', 'actor', 'revision', 'session', 'duplicate', 'oversized'])
def test_invalid_public_admission_starts_no_worker(tmp_path, change):
    with api(tmp_path) as (rig, transport, _):
        async def go():
            headers, fields = HEADERS.copy(), opening(rig)
            if change == 'header': headers['X-MicroscopeMind'] = 'actor:user'
            if change == 'origin': headers['origin'] = 'https://elsewhere.invalid'
            if change == 'grant': headers['X-Micromind-Workspace-Grant'] = 'forged'
            if change == 'actor': fields['actor'] = 'user'
            if change == 'revision': fields['revision'] = True
            if change == 'session': fields['session'] = '2020-01-01'
            kwargs = {'json': fields}
            if change == 'duplicate': kwargs = {'content': '{"item":"one","item":"two"}'}
            if change == 'oversized': kwargs = {'content': ' ' * (128 * 1024 + 1)}
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as http:
                try:
                    response = await http.post('/api/scientific-reviews', headers=headers, **kwargs)
                    assert response.status_code in (403, 409), response.text
                    assert response.json()['next']
                    assert not rig.workers
                    assert not rig.manager._owners
                finally: await rig.manager.close()
        asyncio.run(go())


@pytest.mark.parametrize('failure', ['send', 'retired', 'cancel'])
def test_asgi_delivery_failure_always_releases_held_pixels(tmp_path, failure):
    rig = ReviewsRig(tmp_path)
    memory = Memory()
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            delivery = await rig.manager.frame(rig.client, identity, 'a' * 64, memory)
            response = FrameResponse(delivery)
            if failure == 'retired': rig.manager.gone(rig.client)
            async def send(message):
                if failure == 'cancel': raise asyncio.CancelledError()
                raise RuntimeError('disconnected while transmitting')
            with pytest.raises((ObservationError, RuntimeError, asyncio.CancelledError)):
                await response({'type': 'http'}, None, send)
            assert response.body == b''
            assert delivery.pixels == b''
            assert memory.reserved['analysis'] == 0
            assert rig.manager._deliveries == 0
        finally: await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('site,grant,expected', [('same-origin', 'accepted', 200), ('cross-site', 'accepted', 403),
                                               (None, 'accepted', 403), ('same-origin', 'forged', 403)])
def test_browser_get_without_origin_needs_same_origin_and_accepted_grant(tmp_path, site, grant, expected):
    with api(tmp_path) as (rig, transport, memory):
        async def go():
            try:
                identity = await rig.manager.open(rig.origin)
                await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
                headers = {'X-MicroscopeMind': 'scientific-review', 'X-Micromind-Workspace-Grant': grant}
                if site: headers['sec-fetch-site'] = site
                async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as http:
                    response = await http.get('/api/scientific-reviews/' + identity + '/frames/' + 'a' * 64, headers=headers)
                    assert response.status_code == expected, response.text
                assert memory.reserved['analysis'] == 0
            finally: await rig.manager.close()
        asyncio.run(go())


def test_failed_open_response_can_recover_worker_through_same_public_request(tmp_path, monkeypatch):
    from fastapi.responses import JSONResponse
    with api(tmp_path) as (rig, transport, _):
        async def go():
            original = JSONResponse.__call__
            async def lost(response, scope, receive, send):
                raise OSError('connection dropped before open response')
            try:
                async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=HEADERS) as http:
                    monkeypatch.setattr(JSONResponse, '__call__', lost)
                    with pytest.raises(OSError): await http.post('/api/scientific-reviews', json=opening(rig))
                    assert len(rig.manager._owners) == 1
                    identity = next(iter(rig.manager._owners))
                    monkeypatch.setattr(JSONResponse, '__call__', original)
                    response = await http.post('/api/scientific-reviews', json=opening(rig))
                    assert response.json() == {'ok': True, 'review': identity}
                    assert len(rig.workers) == 1
                    assert (await http.delete('/api/scientific-reviews/' + identity)).json() == {'ok': True}
            finally: await rig.manager.close()
        asyncio.run(go())


def test_public_inventory_and_reattach_restore_exact_window_without_restarting(tmp_path):
    with api(tmp_path) as (rig, transport, _):
        async def go():
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=HEADERS) as http:
                try:
                    response = await http.post('/api/scientific-reviews', json=opening(rig))
                    identity = response.json()['review']
                    rig.change()
                    rig.item['rootId'] = 'replacement-root'
                    rig.item['lifetimeGeneration'] = 'replacement-lifetime'
                    rig.item['scientificPaint'] = None
                    rig.pin()
                    response = await http.post('/api/scientific-reviews', json=opening(rig))
                    assert response.status_code == 409 and 'Resume' in response.json()['error']
                    inventory = (await http.get('/api/scientific-reviews')).json()['reviews']
                    assert len(inventory) == 1 and inventory[0]['review'] == identity
                    assert inventory[0]['document'] == rig.origin.reference
                    response = await http.post('/api/scientific-reviews/' + identity + '/attach', json=opening(rig))
                    assert response.json() == {'ok': True, 'review': identity}, response.text
                    assert len(rig.workers) == 1
                    rig.change()
                    rig.item['view']['legacy']['document']['placement'] = 'another-placement'
                    rig.layout['views'][0]['items'][0]['placementId'] = 'another-placement'
                    rig.pin()
                    response = await http.post('/api/scientific-reviews/' + identity + '/attach', json=opening(rig))
                    assert response.status_code == 409
                    assert len(rig.workers) == 1
                    assert (await http.delete('/api/scientific-reviews/' + identity)).json() == {'ok': True}
                finally: await rig.manager.close()
        asyncio.run(go())


@pytest.mark.parametrize('grant', [None, 'forged'])
def test_inventory_rejects_missing_or_wrong_connection_grant(tmp_path, grant):
    with api(tmp_path) as (rig, transport, _):
        async def go():
            headers = HEADERS.copy()
            headers.pop('X-Micromind-Workspace-Grant')
            if grant: headers['X-Micromind-Workspace-Grant'] = grant
            try:
                await rig.manager.open(rig.origin)
                async with httpx.AsyncClient(transport=transport, base_url=ORIGIN) as http:
                    reply = await http.get('/api/scientific-reviews', headers=headers)
                    assert reply.status_code == 403
                    assert 'reviews' not in reply.json()
            finally: await rig.manager.close()
        asyncio.run(go())


def test_array_save_failure_offers_save_only_recovery():
    import json
    from orchestrator.scientific_saves import SaveUnavailable
    from orchestrator.scientific_review_api import error_response
    response = error_response(SaveUnavailable('Storage interrupted'))
    body = json.loads(response.body)
    assert response.status_code == 503
    assert 'same staged data' in body['next'] and 'Do not rerun Python or the tool' in body['next']


@pytest.mark.parametrize('value', [None, 'yes', 1, {}, []])
def test_default_view_requires_explicit_boolean_before_starting(tmp_path, value):
    with api(tmp_path) as (rig, transport, memory):
        async def go():
            async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=HEADERS) as http:
                response = await http.post('/api/scientific-reviews', json={**opening(rig), 'defaultView': value})
                assert response.status_code == 409
                assert rig.workers == []
        asyncio.run(go())


def test_pre_capture_refusal_has_explicit_safe_correction_instructions():
    import json
    from orchestrator.scientific_saves import SaveNotStarted
    from orchestrator.scientific_review_api import error_response
    response=error_response(SaveNotStarted('Binding changed'))
    result=json.loads(response.body)
    assert response.status_code==409 and result['code']=='SCIENTIFIC_SAVE_NOT_STARTED'
    assert 'Other private changes' in result['next'] and 'then save again' in result['next']
    assert 'reopen' not in result['next'].lower()


def test_public_reconnect_recovers_exact_private_worker_and_hides_capability(tmp_path):
    with api(tmp_path) as (rig, transport, _):
        async def go():
            try:
                async with httpx.AsyncClient(transport=transport, base_url=ORIGIN, headers=HEADERS) as http:
                    fields = {**opening(rig), 'recoveryKey':'browser-private-' + 'a'*40}
                    response = await http.post('/api/scientific-reviews', json=fields)
                    assert response.status_code == 200
                    identity = response.json()['review']; original = rig.client
                    rig.manager.gone(original); rig.screen.gone(original)
                    rig.client = object(); rig.accept(); rig.change(); rig.pin()
                    assert (await http.get('/api/scientific-reviews')).json()['reviews'] == []
                    denied = await http.post('/api/scientific-reviews', json=opening(rig))
                    assert denied.status_code == 409 and len(rig.workers) == 1
                    recovered = await http.post('/api/scientific-reviews', json=fields)
                    assert recovered.json() == {'ok':True,'review':identity}
                    inventory = await http.get('/api/scientific-reviews')
                    assert fields['recoveryKey'] not in inventory.text
                    assert len(rig.workers) == 1 and not rig.closed
                    assert (await http.delete('/api/scientific-reviews/'+identity)).status_code == 200
                    assert rig.closed == rig.workers
            finally: await rig.manager.close()
        asyncio.run(go())


@pytest.mark.parametrize('key',[None,123,'short','x'*129])
def test_invalid_recovery_capability_never_opens_worker(tmp_path,key):
    with api(tmp_path) as (rig,transport,_):
        async def go():
            async with httpx.AsyncClient(transport=transport,base_url=ORIGIN,headers=HEADERS) as http:
                response=await http.post('/api/scientific-reviews',json={**opening(rig),'recoveryKey':key})
                assert response.status_code==409
                assert not rig.workers
            await rig.manager.close()
        asyncio.run(go())
