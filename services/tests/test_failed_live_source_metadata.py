"""Actual failed producer, Screen admission and manual close; synthetic DOM only."""
import asyncio
import copy
import json

import httpx
import pytest

from orchestrator.commands import handle
from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_closed_presentation_source_metadata import initial, report, send
from tests.test_native_live_motion_delivery import GRANT


def failed_report(origin, seq, state, lease):
    message = report(origin, seq, [(state, lease)])
    message['items'][0]['load'].update(state='error', errorCode='DELIVERY_REVOKED')
    return message


def test_failed_motion_report_can_be_pinned_closed_and_followed_by_another_request(tmp_path, monkeypatch):
    async def go():
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        try:
            socket, origin, opened, lease = await initial(web, 'live')
            app, screen = web.state.app, web.state.hub.screen
            app.hub.track_layer.retire_live()
            assert await send(web, socket, failed_report(origin, 2, opened, lease)) == {'ok': True}
            pinned = screen.pin(socket, request_id='ask-after-producer-failure')
            assert pinned.observation.wire()['items'][0]['load']['state'] == 'error'
            assert screen.completion_report(socket)['items'][0]['drawRef'] is None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                assert (await http.get('/api/presentation-sources/' + lease.token,
                                      headers={GRANT: origin['connectionGrant']})).status_code == 404
            replies = []
            def receive(message, *args, **kwargs):
                if isinstance(message, str):
                    message = json.loads(message)
                if isinstance(message, dict):
                    replies.append(copy.deepcopy(message))
                if not isinstance(message, dict) or message.get('schema') != 'presentation-command-v2':
                    return True
                assert message['op'] == 'close'
                state = app.services.presentations.get(opened.workspace_id, opened.id)
                outgoing = report(origin, 4, [(state, lease)])
                row = outgoing['items'][0]
                fence = message['fence']; target = fence['targets'][opened.id]
                outgoing['commandResults'] = [{**{k: fence[k] for k in ('requestId', 'actionId', 'commandId', 'appRunId')},
                    'presentationId': opened.id, 'phase': 'closed', 'reportSeq': 4,
                    'lifetimeGeneration': row['lifetimeGeneration'], 'sourceGeneration': row['sourceGeneration'],
                    'committedRevision': target['committedRevision'],
                    'committedPropertyVersions': target['committedPropertyVersions'],
                    'committedPropertyOwners': {k: target['owner'] for k in target['committedPropertyVersions']},
                    'propertyVersions': row['propertyVersions'], 'propertyOwners': row['propertyOwners'],
                    'sourceKey': lease.source.key, 'sourceToken': lease.token, 'drawRef': None}]
                screen.report(outgoing, socket)
                return True
            monkeypatch.setattr(socket, 'offer', receive)
            await handle(app, socket, {'type': 'present', 'requestId': 'close-dead-source',
                'actionId': 'close-dead-action', 'client': origin['client'], 'do': 'close',
                'what': {'instanceId': opened.id}, 'screen': failed_report(origin, 3, opened, lease)})
            result = next(r for r in replies if r.get('type') == 'result' and r.get('requestId') == 'close-dead-source')
            assert result.get('ok') is True, result
            closed = app.services.presentations.get(opened.workspace_id, opened.id)
            assert closed.open is False
            assert lease.token not in app.services.prepared_sources._pins
            assert screen.pin(socket, request_id='next-ask', inline=report(origin, 5, [(closed, lease)]))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                assert (await http.get('/api/presentation-sources/' + lease.token,
                                      headers={GRANT: origin['connectionGrant']})).status_code == 404
        finally:
            await web.state.app.stop()
    asyncio.run(go())


def test_failed_metadata_does_not_authorize_pixels_foreign_tokens_or_stale_core(tmp_path):
    async def go():
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        try:
            socket, origin, opened, lease = await initial(web, 'live')
            app, screen = web.state.app, web.state.hub.screen
            app.hub.track_layer.retire_live()
            baseline = screen.observation(socket)
            for changed in ('loaded', 'drawn', 'draw-token', 'lifetime', 'revision', 'owner', 'token', 'provenance', 'mixed-consumer'):
                message = failed_report(origin, 2, opened, lease)
                row = message['items'][0]
                if changed in ('loaded', 'drawn'):
                    row['load']['state'] = changed
                elif changed == 'draw-token':
                    row['load']['drawToken'] = 'false-draw'
                elif changed == 'lifetime':
                    row['lifetimeGeneration'] += '-stale'
                elif changed == 'revision':
                    row['revision'] += 1
                elif changed == 'owner':
                    row['propertyOwners']['source'] = 'other-owner'
                elif changed == 'token':
                    row['sourceToken'] = row['load']['token'] = 'f' * 32
                elif changed == 'provenance':
                    message['sources'][0]['provenance']['producer'] = 'invented-producer'
                else:
                    other = copy.deepcopy(row)
                    other.update(id='w-other', rootId='other-root')
                    other['load']['state'] = 'loaded'
                    message['items'].append(other)
                result = await send(web, socket, message)
                assert result['ok'] is False, (changed, result)
                assert screen.observation(socket) is baseline, changed
            from tests.test_native_live_motion_delivery import accepted
            peer, peer_origin = accepted(web, name='peer')
            other = failed_report(peer_origin, 1, opened, lease)
            other['items'][0]['workspaceId'] = peer_origin['client']['workspaceId']
            assert (await send(web, peer, other))['ok'] is False
            assert screen.observation(peer) is None
        finally:
            await web.state.app.stop()
    asyncio.run(go())


def test_error_metadata_coexists_with_exact_closed_sibling_of_same_source(tmp_path):
    from orchestrator.presentations import PresentationAction
    from tests.test_closed_presentation_source_metadata import commit

    async def go():
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        try:
            socket, origin, opened, lease = await initial(web, 'live')
            duplicate, _ = commit(web, socket, PresentationAction('duplicate', instance=opened.id))
            closed, _ = commit(web, socket, PresentationAction('close', instance=duplicate.id))
            web.state.hub.track_layer.retire_live()
            message = report(origin, 2, [(opened, lease), (closed, lease)])
            message['items'][0]['load'].update(state='error', errorCode='DELIVERY_REVOKED')
            message['interaction']['pointer'] = {'modality': 'mouse', 'state': 'present', 'css': [50, 60],
                'presentationId': opened.id, 'sourceKey': lease.source.key,
                'hitPath': [opened.id], 'targetCandidates': []}
            assert await send(web, socket, message) == {'ok': True}
            screen = web.state.hub.screen
            assert screen.pin(socket, request_id='mixed-state-ask')
            for changed in ('closed-draw', 'closed-active-control', 'closed-owner', 'closed-visible', 'closed-selected', 'closed-root-edge'):
                invalid = copy.deepcopy(message)
                invalid['seq'] = 3
                row = invalid['items'][1]
                if changed == 'closed-draw':
                    row['load']['state'] = 'drawn'
                elif changed == 'closed-active-control':
                    row['controls'] = [{'id': 'close:' + closed.id, 'role': 'button', 'present': True,
                                        'enabled': True, 'visible': True, 'label': 'Close', 'rect': [30, 40, 28, 28]}]
                elif changed == 'closed-owner':
                    row['propertyOwners']['source'] = 'other-owner'
                elif changed == 'closed-selected':
                    invalid['interaction']['selected'] = [closed.id]
                elif changed == 'closed-root-edge':
                    invalid['edges'].append({'type': 'shows', 'from': row['rootId'], 'to': lease.source.key})
                else:
                    row['visible'] = True
                assert (await send(web, socket, invalid))['ok'] is False, changed
        finally:
            await web.state.app.stop()
    asyncio.run(go())
