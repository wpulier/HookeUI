"""Actual App/Core/PreparedSources lifecycle with synthetic external DOM reports.

No fabricated action success, ACK, source or source grant. Core commits execute
real ownership changes; external rows describe only their structural lifecycle.
Real authenticated ASGI image access is checked independently. This is metadata
intake evidence, not browser painting or an end-to-end close confirmation.
"""
import asyncio
import copy
import json
import time

import httpx
import pytest

from orchestrator.capture import RUN
from orchestrator.commands import COMMANDS
from orchestrator.paths import Paths
from orchestrator.presentations import PresentationAction, ResourceRef
from orchestrator.server import create_app
from orchestrator.ticket import issue, using
from tests.test_native_live_motion_delivery import GRANT, accepted, camera_samples, prepared
from tests.test_workspace_actions_app import saved_picture


async def saved(web, socket, value=70):
    app = web.state.app
    entry, data = saved_picture(app, value)
    ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
    ctx = app.workspace_context(socket, request_id=ticket.admission.request_id, owner='saved-source-action')
    try:
        with using(ticket):
            lease = await app.services.prepared_sources.prepare(
                ResourceRef(entry.day, entry.id, entry.version, 0), ctx)
        return lease, data
    finally:
        ticket.seal()


def commit(web, socket, action, *, borrow_token=None):
    """Actual original admission and Core commit; never manufacture a receipt."""
    app = web.state.app
    ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
    ctx = app.workspace_context(socket, request_id=ticket.admission.request_id, owner='original-native-action')
    borrow = None
    try:
        with using(ticket):
            if borrow_token is not None:
                # Mirrors _native_present's actual readonly hold across close ACK.
                borrow = app.services.prepared_sources.borrow(borrow_token, ctx)
            effect = app.services.presentations.commit(app.services.presentations.admit(action, ctx))
        assert effect.phase == 'committed' and effect.context.request_id == ctx.request_id
        return app.services.presentations.get(ctx.workspace_id, effect.targets[0]), borrow
    finally:
        ticket.seal()


def row(state, lease, *, closed=None):
    """External structural snapshot; media sampling/paint remains unknown here."""
    opened = state.open if closed is None else not closed
    source = lease.source.to_wire()
    return {
        'id': state.id, 'rootId': 'external-root:' + state.id,
        'workspaceId': state.workspace_id, 'kind': 'magnifier' if source['kind'] == 'live' else 'image',
        'sourceKey': source['key'], 'sourceToken': lease.token,
        'sourceGeneration': state.source_generation, 'lifetimeGeneration': state.lifetime_generation,
        'revision': state.revision, 'propertyVersions': dict(state.property_versions),
        'propertyOwners': dict(state.owners), 'open': opened, 'visible': opened, 'connected': opened,
        'staged': state.staged, 'rect': [30, 40, 360, 280], 'contentRect': [30, 70, 360, 250],
        'clipRect': [30, 70, 360, 250], 'mode': 'floating', 'controls': [], 'liveSample': None,
        'load': {'state': 'loaded' if opened else 'empty', 'sourceKey': source['key'],
                 'sourceGeneration': state.source_generation, 'token': lease.token, 'drawToken': None},
    }


def report(origin, seq, states, *, sources=None):
    items = [row(state, lease) for state, lease in states]
    refs = {lease.source.key: lease.source.to_wire() for _, lease in states}
    active = [item for item in items if item['open'] and item['connected']]
    return {
        'type': 'screen', 'schema': 'screen-observation-v2', 'client': origin['client'], 'appRunId': RUN,
        'seq': seq, 'layoutRevision': seq, 'capturedMonoMs': time.monotonic() * 1000,
        'visible': True, 'complete': True, 'omitted': {},
        'viewport': {'layoutSize': [1440, 1000], 'visualRect': [0, 0, 1440, 1000],
                     'presentationArea': [0, 0, 1440, 1000], 'visualScale': 1, 'documentScroll': [0, 0],
                     'dpr': 1, 'visible': True, 'documentFocused': True, 'activeSurface': 'workspace'},
        'items': items, 'sources': list(refs.values()) if sources is None else sources,
        'targets': [{'id': 'target:' + item['id'], 'type': 'presentation', 'presentationId': item['id'],
                     'sourceKey': item['sourceKey'], 'basis': 'registered-window'} for item in active],
        'edges': [{'type': 'shows', 'from': item['id'], 'to': item['sourceKey']} for item in active],
        'interaction': {'availability': 'observed', 'unknown': []},
        'commandResults': [], 'applied': [], 'user': [],
    }


async def send(web, socket, message):
    return await COMMANDS['screen'](web.state.app, socket, message)


async def initial(web, kind):
    if kind == 'live':
        socket, origin, _, _, _, _, lease, _ = await prepared(web)
    else:
        socket, origin = accepted(web)
        lease, _ = await saved(web, socket)
    state, _ = commit(web, socket, PresentationAction('open', source=lease.source))
    # The Core now owns visibility. This original producer handle retains only
    # immutable metadata after its own release, never an extra hidden owner.
    lease.release()
    if kind == 'live':
        await camera_samples(web, socket)
    response = await send(web, socket, report(origin, 1, [(state, lease)]))
    assert response == {'ok': True}, ('SETUP: active actual source report', response)
    return socket, origin, state, lease


@pytest.mark.parametrize('kind', ['live', 'saved'])
def test_closed_metadata_survives_revocation_and_next_other_active_source_report(tmp_path, kind):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        borrow = other = None
        try:
            socket, origin, opened, lease = await initial(web, kind)
            original = copy.deepcopy(lease.source.to_wire())
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                headers = {GRANT: origin['connectionGrant']}
                url = '/api/presentation-sources/' + lease.token
                assert (await http.get(url, headers=headers)).status_code == 200
                closed, borrow = commit(web, socket, PresentationAction('close', instance=opened.id),
                                        borrow_token=lease.token if kind == 'live' else None)
                assert not closed.open and closed.lifetime_generation != opened.lifetime_generation
                if kind == 'live':
                    assert web.state.hub.track_layer.analysis_work() is None
                    assert web.state.hub.track_layer.acquire_snapshot(lease.source, 'closed-read') is None
                assert (await http.get(url, headers=headers)).status_code == 404
                # First ACK interval: the command's readonly borrow still exists.
                result = await send(web, socket, report(origin, 2, [(closed, lease)]))
                assert result == {'ok': True}, ('BEHAVIOR: exact closed metadata must remain reportable', result)
                if borrow:
                    borrow.release()
                    borrow = None
                # Later reports have no source pin at all, only real Core tombstone.
                assert (await http.get(url, headers=headers)).status_code == 404
                assert await send(web, socket, report(origin, 3, [(closed, lease)])) == {'ok': True}
                other, data = await saved(web, socket, value=110)
                active, _ = commit(web, socket, PresentationAction('open', source=other.source))
                other.release()
                next_report = report(origin, 4, [(closed, lease), (active, other)])
                assert await send(web, socket, next_report) == {'ok': True}
                observed = web.state.hub.screen.observation(socket).wire()
                retained = next(s for s in observed['sources'] if s['key'] == original['key'])
                assert retained == original and closed.source.to_wire() == original
                closed_row = next(i for i in observed['items'] if i['id'] == closed.id)
                assert not any(closed_row[k] for k in ('open', 'visible', 'connected'))
                assert closed_row['load']['state'] == 'empty' and closed_row['liveSample'] is None
                assert (await http.get(url, headers=headers)).status_code == 404
                live_other = await http.get('/api/presentation-sources/' + other.token, headers=headers)
                assert live_other.status_code == 200 and live_other.content == data
        finally:
            if borrow:
                borrow.release()
            if other:
                other.release()
            await web.state.app.stop()
    asyncio.run(go())


@pytest.mark.parametrize('changed', ['provenance', 'lifetime', 'owner'])
def test_closed_metadata_cannot_borrow_forged_source_or_replaced_core_fence(tmp_path, changed):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        try:
            socket, origin, opened, lease = await initial(web, 'saved')
            closed, _ = commit(web, socket, PresentationAction('close', instance=opened.id))
            message = report(origin, 2, [(closed, lease)])
            if changed == 'provenance':
                message['sources'][0]['provenance']['description'] = 'Different unproduced source facts'
            elif changed == 'lifetime':
                message['items'][0]['lifetimeGeneration'] = opened.lifetime_generation
            else:
                message['items'][0]['propertyOwners']['open'] = 'foreign-action-owner'
            before = web.state.hub.screen.observation(socket)
            result = await send(web, socket, message)
            assert result['ok'] is False and result['code'] == 'SOURCE_UNRESOLVED', result
            assert web.state.hub.screen.observation(socket) is before
            assert closed.source.to_wire() == lease.source.to_wire()
        finally:
            await web.state.app.stop()
    asyncio.run(go())


def test_closed_wire_cannot_authorize_metadata_of_still_open_core_after_live_producer_retirement(tmp_path):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        try:
            socket, origin, opened, lease = await initial(web, 'live')
            web.state.hub.track_layer.retire_live()  # actual source end; never a forged validity callback
            assert web.state.app.services.presentations.get(opened.workspace_id, opened.id).open
            message = report(origin, 2, [(opened, lease)])
            message['items'][0] = row(opened, lease, closed=True)
            message['targets'], message['edges'] = [], []
            before = web.state.hub.screen.observation(socket)
            result = await send(web, socket, message)
            assert result['ok'] is False and result['code'] == 'SOURCE_UNRESOLVED', result
            assert web.state.hub.screen.observation(socket) is before
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                response = await http.get('/api/presentation-sources/' + lease.token,
                                          headers={GRANT: origin['connectionGrant']})
                assert response.status_code == 404
        finally:
            await web.state.app.stop()
    asyncio.run(go())


@pytest.mark.parametrize('kind', ['live', 'saved'])
def test_preclose_source_report_refusal_preserves_original_report_correlation(tmp_path, kind):
    """Actual App/Core revocation and COMMANDS.handle reply; external DOM report only."""
    from orchestrator.commands import handle

    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        try:
            socket, origin, opened, lease = await initial(web, kind)
            old = json.loads(json.dumps(report(origin, 2, [(opened, lease)])))
            before = web.state.hub.screen.observation(socket)
            closed, _ = commit(web, socket, PresentationAction('close', instance=opened.id))
            assert not closed.open
            if kind == 'live':
                assert web.state.hub.track_layer.analysis_work() is None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                assert (await http.get('/api/presentation-sources/' + lease.token,
                                      headers={GRANT: origin['connectionGrant']})).status_code == 404
            await handle(web.state.app, socket, old)
            replies = []
            while not socket.control.empty():
                replies.append(json.loads(socket.control.get_nowait()))
            refusal = next(reply for reply in replies if reply.get('type') == 'result' and reply.get('for') == 'screen')
            assert refusal['ok'] is False and refusal['code'] == 'SOURCE_UNRESOLVED'
            assert refusal['requestId'] is None
            assert web.state.hub.screen.observation(socket) is before  # refused pixels/media are never admitted
            assert await send(web, socket, report(origin, 3, [(closed, lease)])) == {'ok': True}
            assert refusal.get('screenReport') == {
                'client': origin['client'], 'appRunId': RUN, 'seq': old['seq'], 'sourceKey': lease.source.key,
            }, 'MISSING_FEATURE: original refused report/source correlation must survive its WS result'
        finally:
            await web.state.app.stop()
    asyncio.run(go())
