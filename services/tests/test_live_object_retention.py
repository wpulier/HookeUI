"""Real source/Registry/HTTP flow over synthetic camera bytes; no camera/model claim."""
import asyncio
import copy
import json
import time

import httpx
import pytest

from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.capture import captures
from orchestrator.paths import Paths
from orchestrator.presentations import PresentationAction, PresentationError
from orchestrator.scene import SceneObject
from orchestrator.server import create_app
from orchestrator.ticket import issue, using
from tests.test_native_live_motion_delivery import accepted, camera_samples, GRANT
from tests.test_workspace_observation import report
from tests.waiting import until_async


async def object_window(web):
    app = web.state.app
    app.hub.settings.outline_check = 'off'
    socket, origin = accepted(web)
    await camera_samples(web, socket)
    info = app.hub.latest_info
    obj = app.hub.scene.add(SceneObject(
        'o-held', 'highlight', [[[640, 300], [710, 300], [710, 360], [640, 360]]],
        info.id, info.epoch, (info.dx, info.dy), 'ai', label='synthetic object', origin=info.id))
    store = app.services.prepared_sources
    source = store.live.object_source(obj)
    ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
    context = app.workspace_context(socket, request_id=ticket.admission.request_id, owner='held-object')
    try:
        with using(ticket):
            lease = await store.prepare(source, context)
    finally:
        ticket.seal()
    return socket, origin, obj, source, lease, context


def test_registry_clear_keeps_exact_authenticated_sample_until_last_close(tmp_path):
    async def go():
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        lease = None
        try:
            socket, origin, obj, source, lease, context = await object_window(web)
            store = web.state.app.services.prepared_sources
            token = lease.token
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                async def read():
                    return await http.get('/api/presentation-sources/' + token,
                                          headers={GRANT: origin['connectionGrant']})
                first = await read()
                assert first.status_code == 200
                sample = json.loads(first.headers['X-Micromind-Live-Sample'])
                with using(issue(web.state.hub, 'turn', asked=web.state.app.day.here(), channel='chat')):
                    run = web.state.app.brain.registry.start('clear', {'what': 'outlines'},
                        ToolContext('chat', 1, None, 'remove only the highlight'))
                    await run.task
                assert run.ok and obj.id not in web.state.hub.scene.objects
                held = await read()
                assert held.status_code == 200, held.text
                assert held.headers['X-Micromind-Live-State'] == 'held'
                assert json.loads(held.headers['X-Micromind-Live-Sample']) == sample
                assert held.content == first.content
                # A newer frame and even a recycled ID cannot renew this stopped source.
                await camera_samples(web, socket, start=100)
                web.state.hub.scene.add(SceneObject(obj.id, 'highlight', obj.polygons,
                    100, obj.anchor_epoch, obj.anchor_offset, 'ai', origin=100))
                again = await read()
                assert again.content == held.content
                assert again.headers['X-Micromind-Live-State'] == 'held'
                claim = store._pins[token].live
                assert claim.current()
                lease.release(); lease = None
                assert claim.released and claim.snapshot is None
                assert (await read()).status_code != 200
                with pytest.raises((PresentationError, ToolFailed)):
                    await store.prepare(source, context)
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


def test_held_window_report_manual_close_and_next_send_pin(tmp_path, monkeypatch):
    """Actual authenticated pixels plus synthetic DOM receipts, never browser evidence."""
    async def go():
        from orchestrator.commands import handle
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        lease = None
        try:
            socket, origin, obj, source, lease, _ = await object_window(web)
            app, store = web.state.app, web.state.app.services.prepared_sources
            core, screen = app.services.presentations, app.hub.screen
            ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
            context = app.workspace_context(socket, request_id=ticket.admission.request_id, owner='setup-open')
            with using(ticket):
                opened = core.commit(core.admit(PresentationAction('open', source=source), context))
            ticket.seal()
            iid = opened.targets[0]
            token = lease.token
            lease.release(); lease = None  # Core is now the only visible owner.
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                url = '/api/presentation-sources/' + token
                first = await http.get(url, headers={GRANT: origin['connectionGrant']})
                assert first.status_code == 200
                run = app.brain.registry.start('clear', {'what': 'outlines'}, ToolContext('chat', 1, None, 'remove only the highlight'))
                await run.task
                assert run.ok
                held = await http.get(url, headers={GRANT: origin['connectionGrant']})
                assert held.status_code == 200 and held.headers['X-Micromind-Live-State'] == 'held'
                sample = json.loads(held.headers['X-Micromind-Live-Sample'])
                sequence = 0

                def observed():
                    nonlocal sequence
                    sequence += 1
                    state = core.get(context.workspace_id, iid)
                    message = report(seq=sequence)
                    message.update(client=origin['client'], appRunId=origin['appRunId'], capturedMonoMs=time.monotonic() * 1000,
                                   targets=[], edges=[], interaction={'availability': 'observed', 'unknown': []}, sources=[source.to_wire()])
                    row = message['items'][0]
                    row.update(id=iid, rootId='synthetic:' + iid, workspaceId=context.workspace_id, kind='magnifier',
                               sourceKey=source.key, sourceToken=token, sourceGeneration=state.source_generation,
                               lifetimeGeneration=state.lifetime_generation, revision=state.revision,
                               propertyVersions=dict(state.property_versions), propertyOwners=dict(state.owners),
                               open=state.open, visible=state.open, connected=state.open, controls=[],
                               view={'zoom': 1, 'pan': [.5, .5]}, heldSample=sample if state.open else None)
                    row['load'].update(state='drawn' if state.open else 'empty', sourceKey=source.key,
                                       token=token, sourceGeneration=state.source_generation,
                                       drawToken='synthetic-held-paint' if state.open else None)
                    return message

                first_report = observed()
                screen.report(first_report, socket)
                pin = screen.pin(socket, request_id='after-clear')
                assert pin.observation.wire()['items'][0]['heldSample'] == sample
                assert screen.completion_report(socket)['items'][0]['drawRef'] == {'held': sample}
                replies = []

                def receive(message, *args, **kwargs):
                    if isinstance(message, str):
                        message = json.loads(message)
                    if isinstance(message, dict):
                        replies.append(copy.deepcopy(message))
                    if not isinstance(message, dict) or message.get('schema') != 'presentation-command-v2':
                        return True
                    assert message['op'] == 'close'
                    outgoing = observed()
                    fence = message['fence']; target = fence['targets'][iid]
                    row = outgoing['items'][0]
                    outgoing['commandResults'] = [{**{k: fence[k] for k in ('requestId', 'actionId', 'commandId', 'appRunId')},
                        'presentationId': iid, 'phase': 'closed', 'reportSeq': sequence,
                        'lifetimeGeneration': row['lifetimeGeneration'], 'sourceGeneration': row['sourceGeneration'],
                        'committedRevision': target['committedRevision'],
                        'committedPropertyVersions': target['committedPropertyVersions'],
                        'committedPropertyOwners': {k: target['owner'] for k in target['committedPropertyVersions']},
                        'propertyVersions': row['propertyVersions'], 'propertyOwners': row['propertyOwners'],
                        'sourceKey': source.key, 'sourceToken': token, 'drawRef': None}]
                    screen.report(outgoing, socket)
                    return True

                monkeypatch.setattr(socket, 'offer', receive)
                await handle(app, socket, {'type': 'present', 'requestId': 'close-held-original',
                    'actionId': 'close-held-action', 'client': origin['client'], 'do': 'close',
                    'what': {'instanceId': iid}, 'screen': observed()})
                await until_async(lambda: any(r.get('requestId') == 'close-held-original' and r.get('type') == 'result' for r in replies))
                result = next(r for r in replies if r.get('requestId') == 'close-held-original' and r.get('type') == 'result')
                assert result.get('ok') is True, result
                assert not core.get(context.workspace_id, iid).open
                assert token not in store._pins
                next_pin = screen.pin(socket, request_id='next-ask', inline=observed())
                assert next_pin.observation.wire()['items'][0]['open'] is False
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


@pytest.mark.parametrize('boundary', ['disconnect', 'reconnect', 'day'])
def test_retained_original_follows_core_window_ownership_boundaries(tmp_path, boundary):
    async def go():
        from tests.test_closed_presentation_source_metadata import commit
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        lease = None
        try:
            socket, origin, obj, source, lease, context = await object_window(web)
            app, store = web.state.app, web.state.app.services.prepared_sources
            commit(web, socket, PresentationAction('open', source=source))
            token = lease.token
            lease.release(); lease = None
            delivered = await store.snapshot(token, context)
            delivered.release()
            claim = store._pins[token].live
            assert claim.snapshot is not None
            app.hub.scene.remove(obj.id)
            if boundary == 'day':
                app._clear_view()
            elif boundary == 'reconnect':
                accepted(web)  # same workspace/client/page, new connection epoch
            else:
                app.hub.screen.gone(socket)
            if boundary != 'day':
                # Core deliberately retains window ownership across a transient
                # disconnect. No new image/hold or delivery grant is manufactured.
                assert not claim.released and claim.snapshot is not None
                assert list(store._pins) == [token]
                app.services.presentations.reset_all()  # actual workspace retirement
            assert claim.released and claim.snapshot is None
            assert token not in store._pins
            with pytest.raises(PresentationError):
                store.borrow(token, context)
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


def test_failed_new_frame_cannot_replace_last_successful_original(tmp_path, monkeypatch):
    async def go():
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        lease = None
        try:
            socket, _, obj, _, lease, context = await object_window(web)
            store = web.state.app.services.prepared_sources
            first = await store.snapshot(lease.token, context)
            raw, sample = first.raw, dict(first.data.sample)
            first.release()
            claim = store._pins[lease.token].live
            original = claim.snapshot.capture
            await camera_samples(web, socket, start=100)
            newer = captures(web.state.hub).take()
            decode = type(newer).bgr
            monkeypatch.setattr(type(newer), 'bgr', lambda capture: None if capture is newer else decode(capture))
            with pytest.raises(PresentationError):
                await store.snapshot(lease.token, context)
            assert claim.snapshot.capture is original
            web.state.hub.scene.remove(obj.id)
            held = await store.snapshot(lease.token, context)
            assert held.raw == raw and held.data.sample == sample
            assert held.headers['X-Micromind-Live-State'] == 'held'
            held.release()
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())
