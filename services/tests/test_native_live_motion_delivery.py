"""Synthetic actual producer -> prepared source -> authenticated ASGI delivery.

Real App, TrackLayer, Captures, CPU motion producer, source leases and HTTP route;
synthetic JPEG intake and a trusted local Client driver. No installed WebSocket,
model, camera hardware, browser paint or biological tracking quality is certified.
The producer source prerequisite must be composed before this backend gate.
"""
import asyncio
import copy
import hashlib
import json
import struct
import threading
import time

import cv2
import httpx
import numpy as np
import pytest

from orchestrator.capture import RUN, captures
from orchestrator.frames import Frame
from orchestrator.hub import Client
from orchestrator.paths import Paths
from orchestrator.presentations import PresentationError, SourceRef
from orchestrator.server import create_app
from orchestrator.ticket import issue, using
from tests.test_comparison_retention import until
from tests.test_movement import clip
from tests.test_native_motion_resource import measured
from tests.test_workspace_actions_app import AppStudio
from tests.waiting import HANG_S


GRANT = 'X-Micromind-Workspace-Grant'


def test_failed_live_producer_can_release_its_window_without_renewing_pixels(tmp_path):
    async def go():
        web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
        lease = closing = None
        try:
            socket, origin, _, _, _, _, lease, context = await prepared(web)
            store = web.state.app.services.prepared_sources
            web.state.hub.track_layer.retire_live()
            with pytest.raises(PresentationError):
                store.borrow(lease.token, context)
            closing = store.borrow(lease.token, context, closing=True)
            assert closing.source.same_identity(lease.source)
            assert closing.token == lease.token
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                refused = await http.get('/api/presentation-sources/' + lease.token, headers={GRANT: origin['connectionGrant']})
                assert refused.status_code != 200  # close metadata cannot revive a frame lease
        finally:
            if closing: closing.release()
            if lease: lease.release()
            await web.state.app.stop()
    asyncio.run(go())


class CameraClient(Client):
    """Passively retains the actual bounded camera packet alongside real queues."""
    def __init__(self):
        super().__init__(None)
        self.latest_packet = None

    def offer(self, message, lossy=None):
        super().offer(message, lossy)
        if isinstance(message, bytes) and message.startswith(b'MMF1'):
            self.latest_packet = message


def accepted(web, *, name='original'):
    socket = CameraClient()
    web.state.hub.clients.add(socket)
    result = web.state.workspace_transport.hello(socket, {
        'type': 'workspace-hello', 'schema': 'workspace-origin-v2',
        'requestId': f'hello-{name}', 'workspaceId': f'workspace-{name}',
        'clientId': f'client-{name}', 'pageEpoch': f'page-{name}',
    })
    assert result['type'] == 'workspace-ready' and result['appRunId'] == RUN
    return socket, result


async def prepared(web):
    app = web.state.app
    socket, origin = accepted(web)
    item, entry, retained = await measured(app, AppStudio(app))
    layer = app.hub.track_layer
    layer.hide()
    # Only the real same-run producer can issue this source. No test constructs
    # an arbitrary live descriptor or substitutes the finite saved bitmap.
    source = SourceRef.from_wire(layer.live_source(item))
    assert source.kind == 'live' and source.to_wire()['digest'] is None
    assert source.identity['run'] == RUN
    assert dict(source.identity['resource']) == {
        'day': item.day, 'id': item.id, 'version': None, 'picture': 0,
    }
    assert source.identity['producerGeneration']
    assert source.to_wire()['provenance']['basis'] == 'current-camera'
    ticket = issue(app.hub, 'command', asked=app.day.here(), channel='studio')
    context = app.workspace_context(socket, request_id=ticket.admission.request_id,
                                    owner='native-live-original-action')
    try:
        with using(ticket):
            lease = await app.services.prepared_sources.prepare(source, context)
    finally:
        ticket.seal()
    return socket, origin, item, entry, retained, source, lease, context


async def camera_samples(web, socket, *, start=46):
    samples = clip([(680, 330, 30, 0)], [(500, 250), (760, 440)],
                   seconds=.3, fps=30, noise=4)
    last = None
    for fid, sample in enumerate(samples, start):
        ok, encoded = cv2.imencode('.jpg', sample.image)
        assert ok
        last = Frame(fid, encoded.tobytes(), time.monotonic(), time.time() + sample.t,
                     9000 + fid, 'native-live-external-camera')
        web.state.hub.source.ring[fid] = last
        web.state.hub.source.last_frame_at = last.received_at
        await web.state.hub._on_frame(last)
    raw = socket.latest_packet
    assert raw and raw.startswith(b'MMF1')
    size = struct.unpack('>I', raw[4:8])[0]
    header = json.loads(raw[8:8 + size])
    assert header['id'] == last.id and raw[8 + size:] == last.jpeg
    # A native-only owner does not replace the independent direct overlay.
    # Its HTTP sample must still equal this actual original camera packet basis.
    return last, header


def assert_image(response, lease, header, retained):
    assert response.status_code == 200, response.text[:200] if response.status_code != 200 else ''
    assert response.headers['content-type'] == 'image/jpeg'
    assert response.headers['cache-control'] == 'no-store'
    assert response.headers['x-micromind-source-token'] == lease.token
    basis = header['frameBasis']
    assert json.loads(response.headers['x-micromind-live-sample']) == {
        'run': basis['run'], 'boot': basis['boot'], 'seq': basis['seq'],
        'frame': basis['frameId'], 'epoch': basis['epoch'],
    }
    assert json.loads(response.headers['x-micromind-image-size']) == basis['imageSize']
    assert response.headers['x-micromind-content-sha256'] == hashlib.sha256(response.content).hexdigest()
    image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
    assert image is not None and [image.shape[1], image.shape[0]] == basis['imageSize']
    assert response.content != retained  # Never relabel the old saved mN picture as a live sample.
    # The annotated response actually contains coloured trajectory pixels; an
    # unannotated grey camera JPEG plus invented sample headers is insufficient.
    assert np.count_nonzero(image.max(axis=2).astype(int) - image.min(axis=2) > 40) > 10


def test_actual_prepared_live_descriptor_and_http_snapshot_are_distinct_from_retained_motion(tmp_path):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        lease = None
        try:
            socket, origin, item, entry, retained, source, lease, context = await prepared(web)
            wire = lease.to_wire()
            assert wire['sourceRef'] == source.to_wire()
            assert wire['sourceToken'] == lease.token
            assert wire['payloadBinding'] == {'servedDigest': None, 'originalDigest': None,
                                               'contentType': 'image/jpeg'}
            assert 'originalSize' not in wire  # No actual current sample exists yet.
            assert wire['mediaType'] == 'image/jpeg'
            original = copy.deepcopy((item.view, item.span, item.summary, item.producer))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                pending = await http.get('/api/presentation-sources/' + lease.token,
                                         headers={GRANT: origin['connectionGrant']})
                assert pending.status_code == 425
                assert not any(key in pending.headers for key in ('x-micromind-live-sample', 'x-micromind-content-sha256'))
                _, header = await camera_samples(web, socket)
                response = await http.get('/api/presentation-sources/' + lease.token,
                                          headers={GRANT: origin['connectionGrant']})
                assert_image(response, lease, header, retained)
            assert lease.to_wire() == wire and (item.view, item.span, item.summary, item.producer) == original
            assert web.state.app.hub.resources.picture(entry.id, 0, entry.day) == (retained, 'image/jpeg')
            resolved = web.state.app.services.prepared_sources.resolve(source, context=context)
            try:
                assert resolved.source.same_identity(source)
            finally:
                resolved.release()
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


def test_live_http_rejects_peer_and_revoked_token_without_borrowing_a_current_frame(tmp_path, monkeypatch):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        lease = None
        try:
            socket, origin, _, _, _, _, lease, _ = await prepared(web)
            await camera_samples(web, socket)
            _, peer = accepted(web, name='peer')
            borrows = []
            actual = web.state.hub.track_layer.acquire_snapshot

            def observe(*args, **kwargs):
                borrows.append(args)
                return actual(*args, **kwargs)

            monkeypatch.setattr(web.state.hub.track_layer, 'acquire_snapshot', observe)
            url = '/api/presentation-sources/' + lease.token
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                refused = await http.get(url, headers={GRANT: peer['connectionGrant']})
                assert refused.status_code == 403 and borrows == []
                lease.release()
                revoked = await http.get(url, headers={GRANT: origin['connectionGrant']})
                assert revoked.status_code == 404 and borrows == []
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


@pytest.mark.parametrize('field', ['producerGeneration', 'run'])
def test_native_live_admission_refuses_forged_or_earlier_producer_identity(tmp_path, field):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        lease = None
        try:
            socket, _, _, _, _, source, lease, _ = await prepared(web)
            changed = source.to_wire()
            changed['identity'][field] = 'different-original-owner'
            ticket = issue(web.state.hub, 'command', asked=web.state.app.day.here(), channel='studio')
            context = web.state.app.workspace_context(socket, request_id=ticket.admission.request_id, owner='forged-action')
            try:
                with using(ticket), pytest.raises(PresentationError):
                    await web.state.app.services.prepared_sources.prepare(SourceRef.from_wire(changed), context)
            finally:
                ticket.seal()
            assert lease.source.same_identity(source)
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


def test_canceled_live_encoding_keeps_original_capture_hold_until_actual_cpu_settlement(tmp_path, monkeypatch):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        lease = None
        release, entered, settled = threading.Event(), threading.Event(), threading.Event()
        snapshots = []
        try:
            socket, origin, _, _, _, _, lease, _ = await prepared(web)
            last, _ = await camera_samples(web, socket)
            layer = web.state.hub.track_layer
            actual_snapshot, actual_encode = layer.acquire_snapshot, cv2.imencode

            def snapshot(*args, **kwargs):
                result = actual_snapshot(*args, **kwargs)
                if result is not None:
                    snapshots.append(result)
                return result

            def encode(*args, **kwargs):
                entered.set()
                try:
                    assert release.wait(HANG_S), 'SETUP: external CPU barrier must be released'
                    return actual_encode(*args, **kwargs)
                finally:
                    settled.set()

            monkeypatch.setattr(layer, 'acquire_snapshot', snapshot)
            monkeypatch.setattr(cv2, 'imencode', encode)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                task = asyncio.create_task(http.get('/api/presentation-sources/' + lease.token,
                                                   headers={GRANT: origin['connectionGrant']}))
                await until(entered.is_set, 'actual native encoding never reached the external CPU boundary')
                assert len(snapshots) == 1 and snapshots[0].capture.id == last.id
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                lease.release()  # Retire the producer's own latest sample too.
                web.state.hub.source.ring.clear()
                assert not snapshots[0].hold.released
                assert snapshots[0].capture in captures(web.state.hub).held()
                release.set()
                await until(lambda: settled.is_set() and snapshots[0].hold.released,
                            'actual CPU settlement did not release its original independent hold')
        finally:
            release.set()
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


def test_reopening_same_live_source_is_independent_of_an_old_asgi_response_borrow(tmp_path):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        original = reopened = response = sending = None
        entered, release = asyncio.Event(), asyncio.Event()
        sent = []
        try:
            socket, origin, _, _, _, source, original, _ = await prepared(web)
            await camera_samples(web, socket)
            store = web.state.app.services.prepared_sources
            response = await web.state.workspace_transport.delivery_image(original.token, origin['connectionGrant'])
            assert response.status_code == 200
            old_bytes = bytes(response.body)
            assert response.headers['x-micromind-content-sha256'] == hashlib.sha256(old_bytes).hexdigest()

            async def send(message):
                # External ASGI body I/O is held after the real source/send check.
                # The actual response, not a test-created pin, owns the borrow.
                if message['type'] == 'http.response.body':
                    entered.set()
                    await release.wait()
                sent.append(message)

            sending = asyncio.create_task(response(
                {'type': 'http', 'method': 'GET', 'asgi': {'spec_version': '2.4'}}, None, send))
            await until(entered.is_set, 'actual live response never entered external ASGI body I/O')
            original.release()  # No visible window remains; only this old response is owned.

            ticket = issue(web.state.hub, 'command', asked=web.state.app.day.here(), channel='studio')
            context = web.state.app.workspace_context(socket, request_id=ticket.admission.request_id,
                                                      owner='reopened-original-live-source')
            try:
                with using(ticket):
                    reopened = await store.prepare(source, context)
            finally:
                ticket.seal()
            assert reopened.token != original.token
            assert reopened.source.same_identity(source)
            assert response.body == old_bytes  # Fresh visibility did not replace pending response bytes.

            release.set()
            await sending
            assert b''.join(message.get('body', b'') for message in sent) == old_bytes
            assert response.body == b''
            with pytest.raises(PresentationError) as old:
                store.borrow(original.token, context)
            assert old.value.code == 'delivery_revoked'
            borrowed = store.borrow(reopened.token, context)
            try:
                assert borrowed.source.same_identity(source)
            finally:
                borrowed.release()
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                pending = await http.get('/api/presentation-sources/' + reopened.token,
                                         headers={GRANT: origin['connectionGrant']})
                assert pending.status_code == 425  # A new owner cannot borrow the stopped sample.
                _, header = await camera_samples(web, socket, start=61)
                current = await http.get('/api/presentation-sources/' + reopened.token,
                                         headers={GRANT: origin['connectionGrant']})
                assert current.status_code == 200
                assert current.headers['x-micromind-source-token'] == reopened.token
                assert json.loads(current.headers['x-micromind-live-sample'])['frame'] == header['id']
                assert current.headers['x-micromind-content-sha256'] == hashlib.sha256(current.content).hexdigest()
        finally:
            release.set()
            if sending is not None:
                await sending
            if reopened:
                reopened.release()
            if original:
                original.release()
            await web.state.app.stop()
    asyncio.run(go())


def test_live_snapshot_memory_refusal_happens_before_detached_producer_acquisition(tmp_path, monkeypatch):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        lease = None
        memory = captures(web.state.hub).memory
        allowance = memory.allowances['analysis']
        calls = []
        try:
            socket, origin, _, _, _, _, lease, _ = await prepared(web)
            await camera_samples(web, socket)
            baseline = memory.reserved['analysis']
            actual = web.state.hub.track_layer.acquire_snapshot

            def acquire(*args, **kwargs):
                calls.append(memory.reserved['analysis'])
                return actual(*args, **kwargs)

            monkeypatch.setattr(web.state.hub.track_layer, 'acquire_snapshot', acquire)
            # Real admission is constrained, not its return value or a fake hold.
            # The existing bounded producer metadata needs twelve MiB before copying.
            memory.allowances['analysis'] = baseline + 12 * 1024 * 1024 - 1
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                response = await http.get('/api/presentation-sources/' + lease.token,
                                          headers={GRANT: origin['connectionGrant']})
            assert response.status_code == 425
            assert response.json()['code'] == 'source_pending'
            assert calls == [], 'metadata copy must not enter before its actual allowance is admitted'
            assert memory.reserved['analysis'] == baseline
        finally:
            memory.allowances['analysis'] = allowance
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())


@pytest.mark.parametrize('has_sample', [False, True], ids=['no-sample-cleanup', 'encoded-response-cleanup'])
def test_real_metadata_reservation_precedes_snapshot_copy_and_releases_after_delivery(
        tmp_path, monkeypatch, has_sample):
    web = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))

    async def go():
        lease = None
        seen, snapshots = [], []
        try:
            socket, origin, _, _, _, _, lease, _ = await prepared(web)
            if has_sample:
                await camera_samples(web, socket)
            memory = captures(web.state.hub).memory
            baseline = memory.reserved['analysis']
            actual = web.state.hub.track_layer.acquire_snapshot

            def acquire(*args, **kwargs):
                seen.append(memory.reserved['analysis'] - baseline)
                result = actual(*args, **kwargs)
                if result is not None:
                    snapshots.append(result)
                return result

            monkeypatch.setattr(web.state.hub.track_layer, 'acquire_snapshot', acquire)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(web), base_url='http://127.0.0.1:8770') as http:
                response = await http.get('/api/presentation-sources/' + lease.token,
                                          headers={GRANT: origin['connectionGrant']})
            assert response.status_code == (200 if has_sample else 425)
            assert len(seen) == 1 and seen[0] >= 12 * 1024 * 1024
            if has_sample:
                assert len(snapshots) == 1 and snapshots[0].hold.released
                assert response.headers['x-micromind-content-sha256'] == hashlib.sha256(response.content).hexdigest()
            else:
                assert snapshots == [] and response.json()['code'] == 'source_pending'
            assert memory.reserved['analysis'] == baseline
        finally:
            if lease:
                lease.release()
            await web.state.app.stop()
    asyncio.run(go())
