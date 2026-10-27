"""TEST-ONLY frame-production lineage tracers authored on frozen d746b265.

Only incoming JPEGs/bridge metadata are synthetic. FrameSource._accept invokes
actual Hub._on_frame, CPU analysis and publication; Scale/Captures and Client
queues are real. No network lifespan, GPU, model, browser, private asset, live
registration or requested-goal fulfillment is exercised. Parent owns execution.
"""
import asyncio
import json
import struct

import cv2
import numpy as np
import pytest

from orchestrator.capture import RUN, captures
from orchestrator.frames import Frame
from orchestrator.hub import Client, FRAME_MAGIC, Hub
from orchestrator.scale import Scale


@pytest.mark.parametrize('first_reader', ['late-scale-of', 'late-take', 'unchanged-first-reader'])
def test_published_frame_keeps_producing_optics_when_first_read_after_objective_change(first_reader):
    pixels = np.full((240, 320), 8, np.uint8)
    cv2.circle(pixels, (160, 120), 90, 180, -1)
    cv2.rectangle(pixels, (125, 95), (175, 135), 70, -1)
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    jpeg = encoded.tobytes()

    async def receive():
        hub = Hub('http://127.0.0.1:1', use_gpu=False)
        hub.scale = Scale()
        hub.scale.set_objective(10)
        try:
            # Four real analyses establish the eyepiece; no test asks for any
            # capture or scale_of during arrival/analysis/publication.
            for seq in range(101, 105):
                await hub.source._accept(jpeg, seq=seq, boot='synthetic-bridge-10x')
            original = hub.latest_info
            assert original.id == 4 and original.fov is not None
            assert original.width == 320 and original.height == 240
            assert original.seq == 104 and original.boot == 'synthetic-bridge-10x'
            radius = original.disc or original.fov.radius
            assert radius > 0
            store = captures(hub)
            control = store.scale_of(original) if first_reader == 'unchanged-first-reader' else None
            if control is not None:
                assert control['objective'] == 10
            hub.scale.set_objective(40)
            # The late first reader must not fix this old frame from the new knob.
            basis = (store.take(original.id).scale if first_reader == 'late-take'
                     else store.scale_of(original))
            assert basis['objective'] == 10, 'MISSING_FEATURE: optics are fixed at frame production'
            if control is not None:
                assert basis == control
            assert basis['objectiveSource'] == 'user'
            assert basis['fieldNumber'] == 18 and basis['fieldNumberSource'] == 'default'
            assert basis['frame'] == [original.width, original.height]
            assert basis['discPx'] == round(radius, 2)
            assert basis['umPerPx'] == pytest.approx(18_000 / 10 / (2 * radius))
            assert basis['measured'] is False
            old_capture = store.take(original.id)
            assert old_capture.scale == basis and old_capture.jpeg == jpeg
            assert (old_capture.id, old_capture.seq, old_capture.boot) == (
                original.id, 104, 'synthetic-bridge-10x')
            assert (old_capture.width, old_capture.height, old_capture.disc, old_capture.fov) == (
                original.width, original.height, original.disc, original.fov)
            # A genuinely later frame uses 40x; the preserved old scale is not a
            # global first-reading latch that prevents later optics from working.
            await hub.source._accept(jpeg, seq=105, boot='synthetic-bridge-40x')
            later = hub.latest_info
            later_basis = store.scale_of(later)
            assert later.id != original.id and later_basis['objective'] == 40
            assert later_basis['frame'] == [later.width, later.height]
            assert later_basis['umPerPx'] == pytest.approx(18_000 / 40 / (2 * (later.disc or later.fov.radius)))
            assert store.take(later.id).scale == later_basis
            assert store.scale_of(original) == basis
            assert old_capture.scale == basis
        finally:
            hub._analysis.shutdown(wait=True)
            hub._background.shutdown(wait=True)

    asyncio.run(receive())


def test_unknown_producing_objective_stays_unknown_after_later_selection():
    pixels = np.full((240, 320), 8, np.uint8)
    cv2.circle(pixels, (160, 120), 90, 180, -1)
    cv2.rectangle(pixels, (125, 95), (175, 135), 70, -1)
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    jpeg = encoded.tobytes()

    async def receive():
        hub = Hub('http://127.0.0.1:1', use_gpu=False)
        hub.scale = Scale()
        assert hub.scale.objective is None
        try:
            for seq in range(201, 205):
                await hub.source._accept(jpeg, seq=seq, boot='synthetic-unknown-optics')
            original = hub.latest_info
            assert original.fov is not None
            # Neither a capture nor scale_of has read any published frame yet.
            hub.scale.set_objective(40)
            store = captures(hub)
            old_basis = store.scale_of(original)
            assert old_basis['objective'] is None, 'MISSING_FEATURE: original unknown optics cannot inherit a later setting'
            assert old_basis['objectiveSource'] == 'unknown'
            assert old_basis.get('umPerPx') is None and old_basis.get('measured') is None
            assert old_basis['frame'] == [320, 240]
            assert old_basis['discPx'] == round(original.disc or original.fov.radius, 2)
            old_capture = store.take(original.id)
            assert old_capture.scale == old_basis and old_capture.jpeg == jpeg
            assert (old_capture.seq, old_capture.boot) == (204, 'synthetic-unknown-optics')
            await hub.source._accept(jpeg, seq=205, boot='synthetic-known-optics')
            current = store.take(hub.latest_info.id)
            assert current.scale['objective'] == 40 and current.scale['umPerPx'] > 0
            assert store.scale_of(original) == old_basis and old_capture.scale == old_basis
        finally:
            hub._analysis.shutdown(wait=True)
            hub._background.shutdown(wait=True)

    asyncio.run(receive())


def test_published_bitmap_header_keeps_own_run_boot_seq_across_backlog_and_invalid_jpeg():
    first = np.full((240, 320), 8, np.uint8)
    cv2.circle(first, (160, 120), 90, 180, -1)
    second = np.full((360, 480), 12, np.uint8)
    cv2.circle(second, (240, 180), 130, 160, -1)
    ok_a, encoded_a = cv2.imencode('.jpg', first)
    ok_b, encoded_b = cv2.imencode('.jpg', second)
    assert ok_a and ok_b
    jpeg_a, jpeg_b = encoded_a.tobytes(), encoded_b.tobytes()
    invalid = b'\xff\xd8not-a-jpeg\xff\xd9'

    async def receive():
        hub = Hub('http://127.0.0.1:1', use_gpu=False)
        hub.scale = Scale()
        client = Client(None)
        hub.clients.add(client)
        try:
            # Concurrent arrivals queue behind real CPU analysis. No busy flag,
            # analysis result, FrameInfo or publication function is replaced.
            await asyncio.gather(hub.source._accept(jpeg_a, seq=301, boot='synthetic-bridge-a'),
                                 hub.source._accept(invalid, seq=302, boot='synthetic-invalid-bridge'),
                                 hub.source._accept(jpeg_b, seq=303, boot='synthetic-bridge-b'))
            originals = {1: (jpeg_a, 320, 240, 301, 'synthetic-bridge-a'),
                         3: (jpeg_b, 480, 360, 303, 'synthetic-bridge-b')}
            assert tuple(hub.infos) == (1, 3) and hub.latest_info.id == 3
            assert hub.source.get(2).jpeg == invalid and 2 not in hub.infos
            delivered = []
            while not client.frames.empty():
                packet = client.frames.get_nowait()
                assert packet[:4] == FRAME_MAGIC
                length = struct.unpack('>I', packet[4:8])[0]
                header = json.loads(packet[8:8 + length])
                bitmap = packet[8 + length:]
                jpeg, width, height, seq, boot = originals[header['id']]
                frame = hub.source.get(header['id'])
                assert isinstance(frame, Frame) and bitmap == jpeg == frame.jpeg
                decoded = cv2.imdecode(np.frombuffer(bitmap, np.uint8), cv2.IMREAD_GRAYSCALE)
                assert decoded.shape == (height, width)
                assert (header['w'], header['h'], header['t']) == (width, height, frame.wall_at)
                assert (header['run'], header['boot'], header['seq']) == (RUN, boot, seq), (
                    'MISSING_FEATURE: published bitmap carries its own run/bridge identity')
                info = hub.infos[header['id']]
                assert (info.id, info.boot, info.seq) == (frame.id, boot, seq)
                delivered.append(header['id'])
            assert delivered == [1, 3]
            assert not hub._analysis_busy and not hub._pending_frames
        finally:
            hub._analysis.shutdown(wait=True)
            hub._background.shutdown(wait=True)

    asyncio.run(receive())
