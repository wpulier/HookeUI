"""Historical motion paths, current registration and captured fallback style.

JPEGs/arrival cadence and the legacy external renderer are synthetic. Hub,
MotionTracker, Captures, Registry, motion operation, TracksStore, TrackLayer,
Screen and Magnifier remain actual production owners. No model or browser runs.
The parent runs these TEST-first tracers; fixture preconditions are explicit.
"""
import asyncio
import hashlib
import math
import time

import cv2
import numpy as np
import pytest

from orchestrator.brain.registry import ToolContext
from orchestrator.brain.toolbox import Services, build
from orchestrator.hub import Hub
from orchestrator.magnifier import Magnifier
from orchestrator.motiontracks import TrackLayer, TracksStore, tracks_of
from tests.test_motion_tracks import (
    MotionHub, moving_slide, present, run, studio_for, tool, view_of,
)
from tests.test_movement import MOVERS, STILL, clip


def stationary_field(t, *, moving=True, different=False):
    """A fixed eyepiece/stage with independent moving spots and fixed landmarks.

    The landmarks cover the top/bottom and left/right of the admitted central
    registration window, spanning more than four of its sixteen grid cells.
    They establish registration independently of the moving population.
    No displacement/epoch/registration result is supplied by the fixture.
    """
    pixels = np.zeros((720, 1280), np.uint8)
    cv2.circle(pixels, (640, 360), 330, 210, -1)
    rng = np.random.default_rng(190 if different else 19)
    if different:
        inside = pixels > 0
        pixels[inside] = rng.integers(30, 230, int(inside.sum()), dtype=np.uint8)
        return pixels
    # The ~330px circle gives an actual quantized registration window of
    # approximately x440..840/y160..560. Peripheral features were excluded in
    # both retained setup failures; these patches are wholly inside that window.
    for x, y in [(470, 185), (620, 185), (810, 185),
                 (470, 535), (620, 535), (810, 535), (470, 350), (810, 350)]:
        # Use spatially resolved stationary corners at the actual 512px
        # registration scale. The first noise-patch fixture failed its real
        # stationary-registration prerequisite; success is still asserted below.
        patch = np.full((48, 48), 210, np.uint8)
        for _ in range(12):
            px, py = rng.integers(3, 39, 2)
            cv2.rectangle(patch, (int(px), int(py)), (int(px)+6, int(py)+6),
                          int(rng.choice([25, 80, 250])), -1)
        pixels[y-24:y+24, x-24:x+24] = patch
    for iy, y in enumerate(range(230, 501, 45)):
        for ix, x in enumerate(range(465, 826, 45)):
            vx = (30 if (ix + iy) % 2 else -30) if moving else 0
            vy = (15 if ix % 2 else -15) if moving else 0
            cv2.circle(pixels, (round(x + vx * t), round(y + vy * t)), 9, 35, -1)
    return pixels


async def receive(hub, pixels, seq):
    ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 92])
    assert ok
    await hub.source._accept(encoded.tobytes(), seq=seq, boot='external-moving-specimen')
    assert hub.latest_info.seq == seq


async def measured_field(moving):
    hub = Hub('http://127.0.0.1:1', use_gpu=False)
    start = time.monotonic()
    try:
        # Actual intake timestamps, actual CPU registration, no fabricated FrameInfo.
        for seq in range(1, 49):
            await receive(hub, stationary_field(time.monotonic() - start, moving=moving), seq)
            await asyncio.sleep(1 / 30)
        original = hub.latest_info
        assert original.fov is not None and original.tracked, 'SETUP: actual stage registration required'
        registry = build(Services(hub=hub))
        work = registry.start('track_motion', {'seconds': 1.5, 'show': False},
                              ToolContext('chat', 1, original.id, 'measure these paths'))
        await work.task
        assert work.ok, work.summary
        item = tracks_of(hub).latest()
        assert item is not None and item.capture_id == original.id
        assert item.rows and item.span['frames'] >= 10
        if moving:
            assert item.summary['moving'] >= 20, 'SETUP: actual measured moving population required'
        return hub, item, start
    except BaseException:
        hub._analysis.shutdown(wait=True)
        hub._background.shutdown(wait=True)
        raise


@pytest.mark.parametrize('change', ['unchanged', 'different-specimen'])
def test_actual_registration_and_historical_path_placement_for_static_field_and_changed_specimen(change):
    hub, item, start = run(measured_field(False))
    layer = TrackLayer(hub)
    try:
        # Useful original self-match proves the actual item has a checkable capture.
        layer.show(item)
        run(layer.check())
        assert layer.verified(), layer.placement
        before = hub.latest_info
        before_votes = hub.motion.consensus['checked'] - hub.motion.consensus['no_vote']
        # Continue actual arrivals at their real cadence; a single artificial
        # +0.8s jump previously invalidated the registration prerequisite.
        async def continue_arrivals():
            for seq in range(49, 73):
                await receive(hub, stationary_field(time.monotonic() - start,
                                                   moving=False, different=change == 'different-specimen'), seq)
                await asyncio.sleep(1 / 30)
        run(continue_arrivals())
        after = hub.latest_info
        if change == 'unchanged':
            votes = hub.motion.consensus['checked'] - hub.motion.consensus['no_vote']
            assert votes > before_votes, 'SETUP: actual distributed feature consensus must vote on the continuation'
            assert after.epoch == before.epoch and after.tracked, 'SETUP: real continuity must remain valid'
            assert math.hypot(after.dx - before.dx, after.dy - before.dy) < 4, 'SETUP: real stationary stage'
        run(layer.check())
        if change != 'unchanged':
            assert not layer.verified(), 'different source must not certify historical path placement'
        else:
            assert layer.verified(), f'unchanged field must remain useful: registration={after}, placement={layer.placement}'
        assert item.capture_id == before.id and item.view['epoch'] == before.epoch
    finally:
        layer.hide()
        hub._analysis.shutdown(wait=True)
        hub._background.shutdown(wait=True)


def test_optional_placement_log_failure_cannot_leave_the_studio_verified():
    hub = MotionHub(clip(MOVERS, STILL, seconds=4.0), disc=False, base=(500.0, -300.0))
    rejected_writes = []

    def external_log(record):
        if record.get('kind') == 'tracks-placement' and record.get('state') == 'unverified':
            rejected_writes.append(record)
            raise OSError('external diagnostic filesystem unavailable')

    hub.tracks_store = TracksStore(hub, None, log=external_log)
    work = tool(hub, {'show': False})
    assert work.ok, work.summary
    item = tracks_of(hub).latest()
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(item)
    run(layer.check())
    assert layer.verified() and hub.sent[-1]['verified'] is True
    sent_before = len(hub.sent)
    rng = np.random.default_rng(103)
    hub.add(rng.integers(0, 256, (720, 1280), dtype=np.uint8),
            item.view['dx'], item.view['dy'], epoch=item.view['epoch'])
    error = None
    try:
        run(layer.check())
    except OSError as exc:
        error = exc
    assert rejected_writes, 'SETUP: genuine rejected match must reach the external diagnostic write'
    assert not layer.verified() and layer.correction == (0.0, 0.0)
    updates = [message for message in hub.sent[sent_before:] if message.get('type') == 'tracks-layer']
    assert updates and updates[-1]['verified'] is False, 'optional log failure must not preserve unsafe Studio state'
    assert updates[-1]['correction'] == [0.0, 0.0] and updates[-1]['why']
    assert error is None, 'optional diagnostic failure must not abort the placement check'


def test_failed_motion_placement_retains_original_capture_check_frame_score_and_residual():
    # Keep paths inside a stationary full frame and exercise nonzero stage
    # coordinates. The original panning fixture failed its self-match before
    # reaching the diagnostic assertion; this new prerequisite remains explicit.
    hub = MotionHub(clip(MOVERS, STILL, seconds=4.0), disc=False, base=(500.0, -300.0))
    records = []
    hub.tracks_store = TracksStore(hub, None, log=records.append)
    work = tool(hub, {'show': False})
    assert work.ok, work.summary
    item = tracks_of(hub).latest()
    layer = TrackLayer(hub, hub.broadcast)
    layer.show(item)
    run(layer.check())
    assert layer.verified(), layer.placement
    rng = np.random.default_rng(102)
    now = hub.add(rng.integers(0, 256, (720, 1280), dtype=np.uint8),
                  item.view['dx'], item.view['dy'], epoch=item.view['epoch'])
    run(layer.check())
    assert not layer.verified() and 'did not match' in layer.placement['why']
    checks = [r for r in records if r.get('kind') == 'tracks-placement' and r.get('frame') == now.id]
    assert checks, 'placement failure must retain actual check evidence for diagnosis'
    check = checks[-1]
    assert check['id'] == item.id and check['captureFrame'] == item.capture_id
    assert check['state'] == 'unverified' and check['score'] == layer.placement['score']
    assert math.isfinite(check['score']) and -1 <= check['score'] <= 1
    assert len(check['residual']) == 2 and all(math.isfinite(v) for v in check['residual'])


def test_opacity_receipt_distinguishes_live_layer_style_from_unchanged_captured_tracks():
    hub = MotionHub(moving_slide(), disc=False)
    work = tool(hub, {'show': False})
    assert work.ok, work.summary
    item = tracks_of(hub).latest()
    original_picture = tracks_of(hub).picture(item)
    hub.add(view_of(0, 0), 0, 0, epoch=4)
    magnifier = Magnifier(hub)
    registry, screen, studio = studio_for(hub, magnifier)
    said = present(registry, hub, what=item.id, do='show')
    assert 'in the magnifier, not the live view' in said and 'lost continuity' in said
    layer = hub.track_layer
    assert not layer.verified()
    shown_before = magnifier.picture_now()
    picture_key = shown_before['url'].rsplit('/', 1)[-1]
    picture_before = magnifier.pictures[picture_key]
    assert hashlib.sha256(picture_before).digest() == hashlib.sha256(original_picture).digest()
    said = present(registry, hub, what=item.id, do='opacity', value='50%')
    observed = screen.item(f'tracks:{item.id}')
    assert observed['opacity'] == layer.style['opacity'] == layer.confirmed_style['opacity'] == 0.5
    assert observed['placement'].startswith('not drawn live:')
    assert magnifier.picture_now() == shown_before and magnifier.pictures[picture_key] == picture_before
    assert 'live-layer style' in said and 'not drawn' in said and 'captured image unchanged' in said, (
        'configured style cannot imply the unchanged fallback image was faded: ' + said)
