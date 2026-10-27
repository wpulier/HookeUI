"""TEST-only proposed frameBasis on the actual Hub binary packet, authored at e99e80e.

Pixels/bridge metadata/optical inputs are SYNTHETIC; App, FrameSource, motion/FOV,
Captures, Client packet serialization and local Evidence saving/readback are real.
The frozen producer/cleanup are imported unchanged. No frame/header/scale/matcher
replacement, model, network lifespan, canonical identity or Canvas authority runs.
Root owns execution and the first observed RED; this file does not certify paint.
"""
import asyncio
import copy
import json
import struct

import cv2
import numpy as np
import pytest

from orchestrator.capture import RUN
from orchestrator.day import DayStore
from orchestrator.paths import Paths
from orchestrator.population import capture_of
from tests.test_live_member_producer_lineage import producer, synthetic_slide


def input_jpeg(pixels):
    """Independent external encoding, with the frozen intake helper's declared quality."""
    ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 96])
    assert ok
    return encoded.tobytes()


def parse_packet(raw):
    assert isinstance(raw, bytes) and raw[:4] == b'MMF1'
    assert len(raw) > 8
    size = struct.unpack('>I', raw[4:8])[0]
    assert 0 < size < len(raw) - 8
    header = json.loads(raw[8:8 + size])
    jpeg = raw[8 + size:]
    assert isinstance(header, dict) and header['type'] == 'frame'
    assert jpeg[:2] == b'\xff\xd8' and jpeg[-2:] == b'\xff\xd9'
    return header, jpeg


def actual_packet(rig, held, pixels):
    """Read actual latest-wins Client bytes after real intake; never offer a packet."""
    raw = None
    while not rig.client.frames.empty():
        raw = rig.client.frames.get_nowait()
    assert raw is not None, 'actual Hub._publish must deliver a binary frame to its real Client'
    header, jpeg = parse_packet(raw)
    source = rig.hub.source.get(held.id)
    assert source is not None and source.id == held.id
    original = input_jpeg(pixels)
    assert jpeg == original == source.jpeg == held.jpeg
    assert (source.seq, source.boot) == (held.seq, held.boot)
    assert (header['id'], header['run'], header['boot'], header['seq'], header['epoch']) == (
        held.id, RUN, held.boot, held.seq, held.epoch)
    assert [header['w'], header['h']] == [held.width, held.height] == list(pixels.shape[::-1])
    assert [header['dx'], header['dy']] == [round(held.dx, 2), round(held.dy, 2)]
    assert header['tracked'] is held.tracked and held.tracked
    assert header['fov'] == [round(held.fov.cx, 1), round(held.fov.cy, 1), round(held.fov.radius, 1)]
    decoded = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE)
    assert decoded is not None and decoded.shape == pixels.shape
    assert np.array_equal(decoded, cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_GRAYSCALE))
    assert decoded.min() < 10 and decoded.max() > 200, 'the actual JPEG has the controlled disc and texture'
    return raw, header, jpeg


def frame_basis(header, held):
    """Inspect only the proposed public arm; compare against the actual original Capture."""
    assert 'frameBasis' in header, 'MISSING_FEATURE: actual binary frame carries its immutable frozen frameBasis'
    basis = header['frameBasis']
    assert isinstance(basis, dict)
    assert set(basis) == {'schema', 'frameId', 'captureId', 'run', 'boot', 'seq', 'epoch', 'tracked',
                          'imageSize', 'disc', 'offset', 'objective', 'umPerPx', 'scaleId', 'scale'}
    assert basis['schema'] == 'frame-basis-v1'
    assert (basis['frameId'], basis['run'], basis['boot'], basis['seq'], basis['epoch']) == (
        held.id, RUN, held.boot, held.seq, held.epoch)
    assert basis['tracked'] is held.tracked
    assert basis['imageSize'] == [held.width, held.height]
    assert basis['disc'] == ([held.fov.cx, held.fov.cy, held.fov.radius] if held.fov is not None else None)
    assert basis['offset'] == [held.dx, held.dy]
    assert basis['captureId'] is None and basis['scaleId'] is None, 'canonical identities have no actual owner yet'
    scale = held.scale
    assert basis['scale'] == scale, 'all original reading/setter/calibration facts must survive the actual packet'
    assert basis['objective'] == (scale.get('objective') if scale is not None else None)
    assert basis['umPerPx'] == (scale.get('umPerPx') if scale is not None else None)
    json.dumps(basis, allow_nan=False)
    return basis


@pytest.mark.parametrize('calibrated', [False, True])
def test_actual_estimated_or_calibrated_packet_carries_exact_capture_basis(tmp_path, calibrated):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, _ = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            if calibrated:
                rig.hub.scale.set_objective(10, field_number=20, via='synthetic packet eyepiece input')
                rig.hub.scale.calibrate(100, 50, first.disc or first.fov.radius)
                held = await rig.receive(pixels)
            else:
                held = first
            _, header, _ = actual_packet(rig, held, pixels)
            scale = held.scale
            assert scale['objective'] == 10 and scale['measured'] is calibrated
            assert scale['optics']['objective']['source'] == 'user'
            assert scale['optics']['objective']['via'] is not None
            if calibrated:
                assert scale['fieldNumberSource'] == 'user' and scale['fieldNumber'] == 20
                facts = scale['optics']['calibrated']['10']
                assert facts['lengthPx'] == 100 and facts['lengthUm'] == 50
                assert facts['discRadiusPx'] == round(first.disc or first.fov.radius, 2)
                assert facts['at'] == scale['calibratedAt'] and facts['at'] > 0
            else:
                assert scale['fieldNumberSource'] == 'default' and scale['fieldNumber'] == 18
                assert scale['optics']['calibrated'] == {} and scale['calibratedAt'] is None
                assert scale['umPerPx'] == pytest.approx(1800 / (2 * (held.disc or held.fov.radius)))
            frame_basis(header, held)
    asyncio.run(exercise())


def test_held_10x_packet_survives_later_knob_and_genuine_40x_bitmap(tmp_path):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, _ = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            raw, original_header, jpeg = actual_packet(rig, first, pixels)
            original_scale = copy.deepcopy(first.scale)
            rig.hub.scale.set_objective(40, via='synthetic later packet knob')
            assert rig.hub.latest_info.id == first.id and rig.client.frames.empty()
            assert rig.hub.scale.objective == 40 and rig.store.take(first.id) is first
            held_header, held_jpeg = parse_packet(raw)
            assert held_header == original_header and held_jpeg == jpeg == first.jpeg
            assert first.scale == original_scale and first.scale['objective'] == 10
            later = await rig.receive(pixels)
            _, later_header, later_jpeg = actual_packet(rig, later, pixels)
            assert later.id != first.id and later.seq != first.seq
            assert later.epoch == first.epoch and (later.dx, later.dy) == (first.dx, first.dy)
            assert later_jpeg == jpeg, 'identical actual pixels may have genuinely different intake optics'
            assert later.scale['objective'] == 40 and later.scale['optics']['objective']['via'] == 'synthetic later packet knob'
            assert first.scale == original_scale and rig.hub.source.get(first.id).jpeg == jpeg
            assert parse_packet(raw) == (original_header, jpeg)
            held_basis = frame_basis(held_header, first)
            new_basis = frame_basis(later_header, later)
            assert held_basis['objective'] == 10 and new_basis['objective'] == 40
            assert held_basis['umPerPx'] != new_basis['umPerPx']
    asyncio.run(exercise())


@pytest.mark.parametrize('calibrated', [False, True])
def test_original_packet_and_real_saved_source_readback_keep_original_optics(tmp_path, calibrated):
    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, boxes = synthetic_slide()
            first = await rig.receive(pixels, warm=True)
            if calibrated:
                rig.hub.scale.set_objective(10, field_number=20, via='synthetic saved packet eyepiece')
                rig.hub.scale.calibrate(100, 50, first.disc or first.fov.radius)
                first = await rig.receive(pixels)
            raw, original_header, jpeg = actual_packet(rig, first, pixels)
            original_scale = copy.deepcopy(first.scale)
            pop = await rig.population(first, boxes)
            assert pop.capture is None and pop.path is not None
            assert (pop.path / 'capture.jpg').read_bytes() == jpeg
            manifest = json.loads((pop.path / 'manifest.json').read_text())
            assert manifest['view'] == pop.view and manifest['scaleId'] == original_scale
            rig.hub.scale.set_objective(40, field_number=25, via='synthetic after saved packet')
            current = await rig.receive(pixels)
            _, current_header, _ = actual_packet(rig, current, pixels)
            assert current.scale['objective'] == 40 and current.scale['fieldNumber'] == 25
            assert rig.hub.evidence.read_back() == [pop.id] and rig.hub.evidence.problems == []
            back = rig.hub.evidence.get(pop.id)
            assert back is not pop and back.capture is None
            assert back.view == pop.view and back.scale_id == original_scale
            assert rig.hub.evidence.capture_jpeg(back) == jpeg, 'actual saved-source byte reader preserves the original'
            own = capture_of(rig.hub, back)
            assert own is not None and own.jpeg == jpeg and own.scale == original_scale
            assert (own.id, own.seq, own.boot, own.epoch) == (first.id, first.seq, first.boot, first.epoch)
            assert {m.id for m in back.live()} == set(pop.members)
            assert len(back.live()) == len(boxes) == 6 and back.revision == pop.revision == 0
            header, retained_jpeg = parse_packet(raw)
            assert header == original_header and retained_jpeg == jpeg and first.scale == original_scale
            original_basis = frame_basis(header, first)
            frame_basis(current_header, current)
            assert original_basis['scale'] == back.scale_id and original_basis['objective'] == 10
    asyncio.run(exercise())


@pytest.mark.parametrize('missing', ['objective', 'legacy_field_number', 'bridge_identity'])
def test_actual_missing_input_provenance_stays_unknown_or_absent_in_packet(tmp_path, missing):
    # A real legacy journal entry is external local input, not a replacement Scale/FrameInfo.
    if missing == 'legacy_field_number':
        DayStore(root=Paths.under(tmp_path).sessions).set_pref('scale', {'fieldNumber': 18.0})

    async def exercise():
        async with producer(tmp_path) as rig:
            pixels, _ = synthetic_slide()
            if missing == 'objective':
                rig.hub.scale.set_objective(None)
            held = await rig.receive(pixels, warm=True)
            if missing == 'bridge_identity':
                # Actual bridge input defaults: no sequence/boot supplied, no frame facts injected.
                await rig.hub.source._accept(input_jpeg(pixels))
                held = rig.store.take(rig.hub.latest_info.id)
            _, header, _ = actual_packet(rig, held, pixels)
            scale = held.scale
            if missing == 'objective':
                assert scale['objective'] is None and scale['objectiveSource'] == 'unknown'
                assert 'umPerPx' not in scale and 'measured' not in scale
                assert scale['optics']['objective']['source'] == 'unknown'
                assert scale['optics']['objective']['via'] is None
            elif missing == 'legacy_field_number':
                assert scale['fieldNumber'] == 18 and scale['fieldNumberSource'] == 'unknown'
                facts = scale['optics']['fieldNumber']
                assert facts['source'] == 'unknown' and facts['via'] is None and facts['at'] is None
                assert 'note' not in facts, 'legacy 18 mm is unknown, not an invented default'
            else:
                assert held.seq is None and held.boot == ''
                assert header['seq'] is None and header['boot'] == ''
            basis = frame_basis(header, held)
            if missing == 'objective':
                assert basis['objective'] is None and basis['umPerPx'] is None
            elif missing == 'legacy_field_number':
                assert basis['scale']['optics']['fieldNumber']['source'] == 'unknown'
            else:
                assert basis['seq'] is None and basis['boot'] == ''
    asyncio.run(exercise())
