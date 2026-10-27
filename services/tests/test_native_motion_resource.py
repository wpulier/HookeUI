"""TEST-only actual motion producer -> native retained-image presentation.

Synthetic CPU camera clip and external AppStudio rendering; actual App/Hub frame
publication, motion operation/store, Registry/Runner/Ticket and delivery owners.
No model, server or browser. No replacement handlers, invented SourceRefs,
forced-unverified live placement or newly generated bitmap when reopening.
"""
import asyncio
import copy
import json
import time

import cv2
import pytest

from orchestrator.capture import RUN
from orchestrator.frames import Frame
from orchestrator.hub import FrameInfo
from orchestrator.motiontracks import keep_measured, tracks_of
from orchestrator.ticket import issue, using
from tests.test_comparison_retention import until
from tests.test_motion_tracks import Studio as MotionStudio
from tests.test_movement import MOVERS, STILL, clip
from tests.test_workspace_actions_app import (
    WORKSPACE, address, built, digest, output_window, plan, succeeded,
)


class MotionTransport:
    """External legacy renderer alongside the existing accepted native renderer.

    It receives the real Hub's JSON broadcast. Only actual tracks-layer messages
    reach the existing motion renderer; binary camera packets are not UI proof.
    """
    def __init__(self, studio):
        self.control = asyncio.Queue(maxsize=16)  # actual Hub inspection backpressure seam
        self.renderer = MotionStudio(studio.app.hub.screen)
        self.renderer.base = studio.legacy

    def offer(self, message):
        if isinstance(message, str):
            message = json.loads(message)
            if message.get('type') == 'tracks-layer':
                self.renderer.hear(message)


async def measured(app, studio):
    """Held synthetic frame/registration pairs enter the real Hub/CPU producer.

    Registration facts are external fixture inputs, not motion-analysis quality
    evidence. The original moving dots are actually followed/measured/rendered.
    """
    source = clip(MOVERS, STILL, seconds=3, fps=15, noise=8, slide=(0, 0))
    origin = time.time() - 3
    frames = []
    for n, sample in enumerate(source, 1):
        ok, encoded = cv2.imencode('.jpg', sample.image)
        assert ok
        when = origin + sample.t
        frame = Frame(n, encoded.tobytes(), time.monotonic(), when, 7000 + n, 'synthetic-motion-bridge')
        info = FrameInfo(n, 1280, 720, sample.dx, sample.dy, 7, True, True, None,
                         when, seq=frame.seq, boot=frame.boot)
        app.hub.source.ring[n] = frame  # external retained camera intake, no source adapter replacement
        app.hub._publish(frame, info)
        frames.append((frame, info))
    transport = MotionTransport(studio)
    app.hub.clients.add(transport)
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    try:
        with using(ticket):
            item, reused, done = await keep_measured(app.hub, frames, None, '', 3)
        assert reused is False and done.execution.operation == 'motion'
        assert done.execution.outputs == [item.id] and len(item.rows) > 0
        await until(lambda: item.status == 'saved', 'actual motion producer did not finish saving its bitmap')
        raw = tracks_of(app.hub).picture(item)
        assert raw and raw != frames[-1][0].jpeg  # actual annotated source, not untouched camera bytes
        assert item.capture_id == 45 and item.view['epoch'] == 7
        assert item.view['run'] == RUN and item.span['frames'] == 45
        layer = app.hub.track_layer
        layer.show(item)
        await layer.check()
        assert layer.verified(), layer.placement
        # Resources are addressed by owning session, not their calendar storage
        # folder: a measurement can finish after midnight in yesterday's session.
        entry = app.hub.resources.get(item.id, item.session)
        assert entry.kind == 'tracks' and entry.version is None
        assert app.hub.resources.picture(entry.id, 0, entry.day) == (raw, 'image/jpeg')
        return item, entry, raw
    finally:
        ticket.close()


def assert_captured_source(source, item, entry, raw):
    assert source['identity']['resource'] == address(entry)
    assert source['kind'] == 'saved-image' and source['media'] == 'labelled'
    assert source['digest'] == digest(raw)
    facts = source['provenance']
    # Public producer basis is deliberately explicit; neither latest camera nor
    # current live verification is allowed to replace the captured source.
    assert facts['basis'] == 'captured-still'
    assert facts['view'] == item.view
    assert facts['span'] == item.span
    assert facts['scale'] == item.scale
    assert facts['producer'] == item.producer
    assert facts['transformations'] == item.transformations
    assert facts['frame'] == item.capture_id


async def opened(app, entry):
    runs, ctx = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
    succeeded(runs[0])
    iid = output_window(ctx)
    assert runs[0].outcome['status'] == 'confirmed'
    return iid, runs[0], ctx


@pytest.mark.parametrize('after_midnight', [False, True])
def test_actual_measured_motion_opens_as_independent_captured_source_without_changing_live_layer(tmp_path, after_midnight):
    app, studio = built(tmp_path)
    if after_midnight:
        app.day.clock = lambda: time.time() + 86400

    async def go():
        item, entry, raw = await measured(app, studio)
        if after_midnight:
            assert item.day != item.session and entry.day == item.session
        layer = app.hub.track_layer
        original = copy.deepcopy(layer.style)
        execution = item.made_by['execution']
        iid, run, ctx = await opened(app, entry)
        state = studio.store().get(WORKSPACE, iid)
        assert_captured_source(state.source.to_wire(), item, entry, raw)
        assert state.open and studio.items[iid]['load']['state'] == 'drawn'
        assert studio.items[iid]['sourceKey'] == state.source.key
        assert studio.items[iid]['payloadBinding']['servedDigest'] == digest(raw)
        assert layer.shown == item.id and layer.verified() and layer.style == original
        assert item.made_by['execution'] == execution and tracks_of(app.hub).items[item.id] is item
        assert app.services.magnifier.picture_now() is None  # no forced captured-view fallback
        assert run.images == [] and ctx.inspection_slots == []
        assert ctx.admission.used == 0  # source delivery does not invent a model call

    asyncio.run(go())


def test_original_motion_window_resize_and_move_use_its_actual_instance_and_source(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        item, entry, raw = await measured(app, studio)
        iid, _, _ = await opened(app, entry)
        source = studio.store().get(WORKSPACE, iid).source
        runs, ctx = await plan(app, [
            ('present', {'do': 'resize', 'instance': {'instanceId': iid}, 'size': {'width': 240, 'height': 135}}),
            ('present', {'do': 'move', 'instance': {'instanceId': iid}, 'position': {'x': 1180, 'y': 20}}),
        ])
        assert len(runs) == 2
        for run in runs:
            succeeded(run)
            assert run.outcome['status'] == 'confirmed'
        # Original viewport is 1440x1000: a 240px component ends 20px from its right edge.
        assert studio.items[iid]['rect'] == [1180, 20, 240, 135]
        assert studio.store().get(WORKSPACE, iid).source == source
        assert_captured_source(source.to_wire(), item, entry, raw)
        assert app.hub.track_layer.shown == item.id and app.hub.track_layer.verified()
        assert ctx.inspection_slots == [] and ctx.admission.used == 0
        schema = next(t['parameters'] for t in app.brain.registry.responses_tools() if t['name'] == 'present')
        assert {'open', 'resize', 'move'} <= set(schema['properties']['do']['enum'])
        assert 'motion' in schema['properties']['source']['description'].lower()
        assert 'captured' in schema['properties']['source']['description'].lower()

    asyncio.run(go())


def test_saved_motion_reloads_its_original_captured_bitmap_without_a_current_camera(tmp_path):
    app, studio = built(tmp_path)
    item, entry, raw = asyncio.run(measured(app, studio))
    fresh, renderer = built(tmp_path)
    retained = tracks_of(fresh.hub).get(entry.id)
    assert retained is not None and retained is not item
    assert retained.picture is None and retained.capture is None
    assert fresh.hub.latest_info is None and fresh.hub.source.ring == {}
    loaded = fresh.hub.resources.get(entry.id, entry.day)
    assert address(loaded) == address(entry)

    async def go():
        iid, _, ctx = await opened(fresh, loaded)
        state = renderer.store().get(WORKSPACE, iid)
        assert_captured_source(state.source.to_wire(), item, entry, raw)
        assert renderer.items[iid]['payloadBinding']['servedDigest'] == digest(raw)
        assert fresh.hub.track_layer.shown is None
        assert fresh.hub.latest_info is None and ctx.inspection_slots == []

    asyncio.run(go())


@pytest.mark.parametrize('change', [{'picture': 1}, {'version': 0}, {'id': 'm999'}, {'day': '2000-01-01'}])
def test_motion_source_wrong_original_address_refuses_without_window_or_live_retarget(tmp_path, change):
    app, studio = built(tmp_path)

    async def go():
        item, entry, raw = await measured(app, studio)
        ref = {**address(entry), **change}
        before = tuple(studio.store().list(WORKSPACE))
        runs, _ = await plan(app, [('present', {'do': 'open', 'source': ref})])
        assert len(runs) == 1 and runs[0].ok is False
        failure = runs[0].outcome['failure']
        expected = 'SOURCE_IDENTITY_MISMATCH' if 'picture' in change or 'version' in change else 'SOURCE_UNAVAILABLE'
        assert runs[0].outcome['status'] == 'failed' and failure['code'] == expected
        assert failure['reason'] and runs[0].summary and runs[0].outputs == []
        assert tuple(studio.store().list(WORKSPACE)) == before
        assert app.hub.track_layer.shown == item.id and app.hub.track_layer.verified()
        assert tracks_of(app.hub).picture(item) == raw

    asyncio.run(go())


def test_missing_original_motion_bitmap_refuses_without_current_camera_substitution(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        item, entry, raw = await measured(app, studio)
        assert item.picture is None and item.path is not None
        (item.path / 'tracks.jpg').unlink()  # external retained-file loss, not a substituted source reader
        assert tracks_of(app.hub).picture(item) is None
        runs, _ = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
        assert len(runs) == 1 and runs[0].ok is False
        failure = runs[0].outcome['failure']
        assert runs[0].outcome['status'] == 'failed' and failure['code'] == 'SOURCE_UNAVAILABLE'
        assert failure['reason'] and runs[0].summary and runs[0].outputs == []
        assert studio.store().list(WORKSPACE) == ()
        assert app.hub.track_layer.shown == item.id and app.hub.track_layer.verified()
        assert app.hub.source.get(item.capture_id).jpeg != raw

    asyncio.run(go())


def test_owning_home_symlink_keeps_original_motion_source_address_and_pixels(tmp_path):
    actual = tmp_path / 'actual-home'
    actual.mkdir()
    home = tmp_path / 'configured-home'
    home.symlink_to(actual, target_is_directory=True)  # same semantics as macOS /tmp -> /private/tmp
    app, studio = built(home)
    assert app.paths.home == home and app.hub.tracks_store.root == home / 'evidence'

    async def go():
        item, entry, raw = await measured(app, studio)
        assert item.path == home / 'evidence' / entry.day / entry.id
        assert item.path.absolute() != item.path.resolve()
        assert item.path.resolve() == actual / 'evidence' / entry.day / entry.id
        iid, _, _ = await opened(app, entry)
        state = studio.store().get(WORKSPACE, iid)
        assert_captured_source(state.source.to_wire(), item, entry, raw)
        assert studio.items[iid]['payloadBinding']['servedDigest'] == digest(raw)
        assert app.hub.track_layer.shown == item.id and app.hub.track_layer.verified()

    asyncio.run(go())


def test_nested_motion_folder_alias_refuses_without_native_or_live_retarget(tmp_path):
    app, studio = built(tmp_path / 'home')

    async def go():
        item, entry, raw = await measured(app, studio)
        original = item.path
        elsewhere = tmp_path / 'outside-original-root'
        original.rename(elsewhere)
        original.symlink_to(elsewhere, target_is_directory=True)
        assert item.path == original and item.path.resolve() != original
        assert tracks_of(app.hub).picture(item) == raw  # available bytes are not authority to escape the store
        runs, _ = await plan(app, [('present', {'do': 'open', 'source': address(entry)})])
        assert len(runs) == 1 and runs[0].ok is False
        assert runs[0].outcome['status'] == 'failed'
        assert runs[0].outcome['failure']['code'] == 'UNSAFE_SOURCE'
        assert runs[0].outcome['failure']['reason'] and runs[0].outputs == []
        assert studio.store().list(WORKSPACE) == ()
        assert app.hub.track_layer.shown == item.id and app.hub.track_layer.verified()

    asyncio.run(go())


def test_same_plan_motion_window_binds_to_actual_producer(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        original, _, _ = await measured(app, studio)
        runs, ctx = await plan(app, [
            ('track_motion', {'seconds': 2, 'show': False}),
            ('present', {'do': 'open', 'source': {'step': 1, 'output': 0}}),
        ])
        for run in runs:
            succeeded(run)
        output = runs[0].outputs[0].wire()
        assert output['kind'] == 'source'
        item = tracks_of(app.hub).items[output['source']['id']]
        assert item.id != original.id
        assert output['source'] == {'day': item.session, 'id': item.id, 'version': None, 'picture': 0}
        assert app.hub.track_layer.shown == original.id
        iid = runs[1].outputs[0]['instanceId']
        assert studio.store().get(WORKSPACE, iid).source.to_wire()['identity']['resource'] == output['source']
        assert len(tracks_of(app.hub).items) == 2
    asyncio.run(go())
