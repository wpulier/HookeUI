"""Placement check provenance through actual intake and a held external codec.

Only incoming JPEG/bridge metadata and codec scheduling are synthetic. Real
Hub/FrameSource/MotionTracker, Registry motion production, Capture ownership,
TrackLayer and existing TracksStore logging remain intact. No runtime/model is
called by authoring this TEST checkpoint; parent owns serial execution.
"""
import asyncio
import threading

import cv2
import pytest

from orchestrator.motiontracks import TrackLayer, tracks_of
from tests.test_motion_track_placement_facts import measured_field, receive, stationary_field
from tests.waiting import HANG_S


@pytest.mark.parametrize('bridge_metadata', ['known', 'unknown'])
def test_check_provenance_names_the_held_frame_even_when_a_new_frame_arrives(monkeypatch, bridge_metadata):
    async def go():
        hub, item, _start = await measured_field(False)
        records = []
        tracks_of(hub).log = records.append  # external diagnostic sink only
        return hub, item, records

    hub, item, records = asyncio.run(go())
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(item)  # no loop running here, so no background checker is started
    blocked, release = threading.Event(), threading.Event()
    decode = cv2.imdecode

    async def check_pair():
        task = None
        try:
            # Establish the useful actual producer and its original self-match.
            await layer.check()
            assert layer.verified(), layer.placement
            pixels = stationary_field(0, moving=False)
            pixels[300:303, 550:553] = 205  # distinct JPEG with unchanged field geometry
            ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 92])
            assert ok
            jpeg = encoded.tobytes()
            seq, boot = ((491, 'external-check-boot') if bridge_metadata == 'known' else (None, ''))
            await hub.source._accept(jpeg, seq=seq, boot=boot)
            checked = hub.latest_info
            before = {
                'checkSeq': seq, 'checkBoot': boot, 'checkAt': checked.wall_at,
                'checkTracked': checked.tracked,
                'checkGeometry': {'width': checked.width, 'height': checked.height,
                                  'discPx': checked.disc},
            }
            assert checked.tracked and checked.fov is not None and checked.disc > 0
            assert (checked.width, checked.height) == (1280, 720)
            assert checked.seq == seq and checked.boot == boot
            assert checked.id != item.capture_id and item.view['seq'] != seq

            def held_decode(data, flags):
                if flags == cv2.IMREAD_GRAYSCALE and data.tobytes() == jpeg:
                    blocked.set()
                    assert release.wait(HANG_S), 'test did not release the actual check decode'
                return decode(data, flags)

            monkeypatch.setattr(cv2, 'imdecode', held_decode)
            task = asyncio.create_task(layer.check())
            assert await asyncio.to_thread(blocked.wait, HANG_S), 'SETUP: actual check must hold its codec await'
            newer = stationary_field(0, moving=False)
            newer[310:313, 550:553] = 205
            await receive(hub, newer, 492)
            assert hub.latest_info.id != checked.id and hub.latest_info.seq == 492
            release.set()
            await task
            pair = [r for r in records if r.get('kind') == 'tracks-placement' and r.get('frame') == checked.id]
            assert pair, 'SETUP: actual held frame must produce its existing placement diagnostic'
            report = pair[-1]
            assert report['id'] == item.id and report['captureFrame'] == item.capture_id
            assert report['state'] == 'verified' and layer.verified()
            # Actual checked-frame metadata, not newest-frame or original-capture
            # metadata. Missing legacy sequence/boot remains None/empty explicitly.
            assert {key: report.get(key) for key in before} == before, (
                'placement record must preserve the exact held source pair and geometry')
            assert report['checkAt'] != hub.latest_info.wall_at
            assert item.view['seq'] != hub.latest_info.seq and item.capture_id != checked.id
        finally:
            release.set()
            if task is not None and not task.done():
                await task
            layer.hide()
            hub._analysis.shutdown(wait=True)
            hub._background.shutdown(wait=True)

    asyncio.run(check_pair())
