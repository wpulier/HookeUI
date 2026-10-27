"""Explicit continuing camera-coordinate motion, through actual product owners.

Synthetic external JPEG/registration inputs; actual App/Registry/Runner/Hub,
Captures, CPU spot/link operation and original display lifecycle. No models or
server/browser are started. Known registration inputs are not tracking-quality
evidence. Finite saved measurements remain separate from continuing display.
"""
import asyncio
import copy
import json
import struct
import threading
import time
import weakref

import cv2

from orchestrator.capture import RUN
from orchestrator.apply import apply
from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.commands import COMMANDS
from orchestrator.frames import Frame
from orchestrator.hub import FrameInfo
from orchestrator.motiontracks import colour_style, select as select_tracks, tracks_of
from orchestrator.paths import Paths
from orchestrator.ticket import issue
from orchestrator.workspace_observation import ClientRef
from tests.test_movement import clip
from tests.test_native_motion_resource import MotionTransport
from tests.test_comparison_retention import until
from tests.test_legacy_workspace_projection import structural_item
from tests.test_workspace_actions_app import AppStudio, CLIENT, STUDIOS, built, plan, succeeded
from tests.waiting import HANG_S, until_async


class CameraStudio(AppStudio):
    """External registered structural layer, with no native source/paint authority.

    Configuration visibility follows received actual tracks-layer state. The
    producer/JPEG tests independently observe pixels; this DOM fixture never
    certifies a current-camera draw from the server's verified flag.
    """
    def __init__(self, app):
        super().__init__(app)
        self.track_rows = {}

    def offer(self, message):
        if isinstance(message, str):
            self.hear(json.loads(message))

    def hear(self, message):
        if message.get('type') == 'tracks-layer':
            shown = message.get('tracks')
            for row in self.track_rows.values():
                if row['visible'] != (row['id'] == f'tracks:{shown}'):
                    row.update(visible=False, open=False, revision=row['revision'] + 1)
            if shown:
                key = f'tracks:{shown}'
                row = self.track_rows.setdefault(key, structural_item(key, 'tracks', {}, generation=1, revision=1))
                row['workspaceId'] = CLIENT['workspaceId']
                before = copy.deepcopy(row)
                row.update(visible=True, open=True)
                style = message.get('style') or {}
                row['view']['legacy'].update(shows=shown, opacity=style.get('opacity', 1),
                    style={k: v for k, v in style.items() if k not in ('opacity', 'values', 'legend')},
                    placement='current-camera drawing not observed by this structural fixture')
                if row != before:
                    row['revision'] += 1
        elif message.get('type') == 'present' and message.get('id', '').startswith('tracks:'):
            assert not message.get('commandId'), 'structural fixture cannot acknowledge native commands'
            row = self.track_rows[message['id']]
            assert message['verb'] in ('show', 'hide', 'opacity', 'colour', 'emphasise')
            # ACK only after real layer broadcasts independently produced these
            # external observations; never apply requested values from a Change.
            legacy = row['view']['legacy']
            observed = {'visible': row['visible'], 'opacity': legacy.get('opacity'), **legacy.get('style', {})}
            assert all(observed.get(key) == value for key, value in message['set'].items())
            if message['verb'] == 'hide':
                assert self.app.hub.track_layer.shown is None
            self.applied.append(message['change'])
            task = asyncio.get_running_loop().create_task(self.send_report())
            self.observation_tasks.append(task)
            return
        super().hear(message)

    async def send_report(self, user=()):
        self.seq += 1
        report = {'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(CLIENT),
            'appRunId': RUN, 'seq': self.seq, 'layoutRevision': self.seq,
            'capturedMonoMs': time.monotonic() * 1000, 'visible': True,
            'complete': False, 'omitted': {'items': 1, 'sources': 0, 'targets': 0, 'edges': 0},
            'viewport': {'layoutSize': [1440, 1000], 'visualRect': [0, 0, 1440, 1000],
                'visualScale': 1, 'documentScroll': [0, 0], 'dpr': 1,
                'visible': True, 'documentFocused': True, 'activeSurface': 'workspace'},
            'items': copy.deepcopy(list(self.track_rows.values())), 'sources': [], 'targets': [], 'edges': [],
            'interaction': {'availability': 'partial', 'unknown': ['synthetic-structural-layer-only']},
            'commandResults': [], 'applied': list(self.applied), 'user': list(user)}
        reply = await COMMANDS['screen'](self.app, self, report)
        assert reply.get('ok') is True, f'SETUP: actual accepted structural report refused: {reply}'


def built_camera(tmp_path):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = CameraStudio(app)
    app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'],
                                          CLIENT['pageEpoch'], CLIENT['connectionEpoch']))
    original = app.hub.broadcast

    def broadcast(message):
        original(message)
        studio.hear(message)

    app.hub.broadcast = app.hub.screen.broadcast = broadcast
    asyncio.run(studio.send_report())
    STUDIOS[id(app)] = weakref.ref(studio)
    return app, studio


class CameraTransport(MotionTransport):
    """External transport observes real JSON broadcasts; legacy renderer is reused."""
    def __init__(self, studio):
        super().__init__(studio)
        self.structural = studio if isinstance(studio, CameraStudio) else None
        self.messages = []
        self.packets = []

    def offer(self, message):
        if isinstance(message, str):
            observed = json.loads(message)
            self.messages.append(observed)
            if self.structural is not None and observed.get('type') == 'tracks-layer':
                self.structural.hear(observed)  # actual Hub transport reaches its accepted external renderer
        elif isinstance(message, bytes) and message.startswith(b'MMF1'):
            self.packets = (self.packets + [message])[-16:]
        super().offer(message)


def publish(app, pixels, fid, wall, *, dx=500.0, dy=-300.0, tracked=True):
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    frame = Frame(fid, encoded.tobytes(), time.monotonic(), wall, 8000 + fid, 'external-motion-camera')
    info = FrameInfo(fid, 1280, 720, dx, dy, epoch=7, tracked=tracked, steady=True, fov=None,
                     wall_at=wall, seq=frame.seq, boot=frame.boot)
    # Existing synthetic external intake seam: the real Hub publishes/adopts the
    # exact JPEG with its producing facts and notifies its actual subscribers.
    app.hub.source.ring[fid] = frame
    app.hub.source.last_frame_at = frame.received_at
    app.hub._publish(frame, info)
    return frame, info


async def receive(app, pixels, fid, wall):
    """External JPEG goes through the actual existing analysis/publication path."""
    ok, encoded = cv2.imencode('.jpg', pixels)
    assert ok
    frame = Frame(fid, encoded.tobytes(), time.monotonic(), wall, 8000 + fid, 'external-motion-camera')
    app.hub.source.ring[fid] = frame
    app.hub.source.last_frame_at = frame.received_at
    await app.hub._on_frame(frame)
    return frame


def packet(transport, fid):
    for raw in transport.packets:
        length = struct.unpack('>I', raw[4:8])[0]
        header = json.loads(raw[8:8 + length])
        if header['id'] == fid:
            return header, raw[8 + length:]
    assert False, f'SETUP: actual intake did not publish frame {fid}'


async def initial(app, studio):
    transport = CameraTransport(studio)
    app.hub.clients.add(transport)
    samples = clip([(620, 330, 30, 0)], [(500, 250), (760, 440)], seconds=2, fps=30, noise=4)
    origin = time.time() - 2
    for fid, sample in enumerate(samples, 1):
        publish(app, sample.image, fid, origin + sample.t)
    return transport, samples, origin


async def observation(transport, frame):
    def found():
        seen = [m for m in transport.messages if m.get('type') == 'motion-tracks'
                and m.get('basis') == 'camera-pixels' and (m.get('sample') or {}).get('frameId') == frame]
        return seen[-1] if seen else None

    return await until_async(found, what=f'MISSING_FEATURE: an actual current-camera motion observation for original frame {frame}')


def test_displayed_motion_defaults_to_current_camera_without_rewriting_measurement(tmp_path):
    app, studio = built_camera(tmp_path)

    async def go():
        transport, samples, origin = await initial(app, studio)
        try:
            runs, ctx = await plan(app, [('track_motion', {'seconds': 1.5})])
            succeeded(runs[0])
            item = tracks_of(app.hub).latest()
            assert item is not None and item.made_by['implementation'] == 'spots'
            assert item.span['frames'] >= 40 and item.capture_id == 60
            assert len(item.rows) == 3
            original = copy.deepcopy((item.view, item.span, item.summary, item.rows, item.producer))
            original_pixels = tracks_of(app.hub).picture(item)
            assert original_pixels and original_pixels.startswith(b'\xff\xd8')
            assert runs[0].outputs[0]['source'] == {'day': item.day, 'id': item.id, 'version': None, 'picture': 0}
            # Continue through real intake and analysis. The independently known
            # pixel position must not be displaced by measured field offsets.
            forward = clip([(680, 330, 30, 0)], [(500, 250), (760, 440)], seconds=.5, fps=30, noise=4)
            for fid, sample in enumerate(forward, 61):
                incoming = await receive(app, sample.image, fid, origin + 2 + sample.t)
            header, raw = packet(transport, 75)
            assert raw == incoming.jpeg, 'SETUP: original JPEG packet identity must survive analysis'
            assert isinstance(header.get('motionTracks'), dict), 'MISSING_FEATURE: motion and source image must publish in the same packet'
            wire = await observation(transport, 75)
            assert header['motionTracks'] == wire
            assert wire['id'] == item.id
            sample = wire['sample']
            assert (sample['run'], sample['boot'], sample['seq'], sample['frameId']) == (RUN, 'external-motion-camera', 8075, 75)
            assert sample == header['frameBasis']
            assert sample['imageSize'] == [1280, 720]
            points = [seg['points'][-1] for track in wire['tracks'] for seg in track['segments'] if seg['points']]
            assert any(abs(p[0] - 694) <= 3 and abs(p[1] - 330) <= 3 for p in points), points
            assert (item.view, item.span, item.summary, item.rows, item.producer) == original
            from orchestrator.motiontracks import lines
            context = "\n".join(text for _at, text in lines(app.hub))
            assert "saved measurement unchanged" in context
            assert "current trails do not update these counts or speeds" in context
            assert "spot tracks do not establish organism identity" in context
            assert tracks_of(app.hub).picture(item) == original_pixels
            assert ctx.admission.used == 0 and ctx.inspection_slots == []
            assert len(runs[0].outputs) == 2, 'MISSING_FEATURE: live source must be a real independently consumable output'
            source = runs[0].outputs[1]['source']
            assert source['kind'] == 'live' and source['identity']['run'] == RUN
            assert source['identity']['resource'] == runs[0].outputs[0]['source']
            assert source['provenance']['basis'] == 'current-camera'
            # Stop via the actual public presentation tool, not a private task.
            await studio.send_report()
            request = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
            try:
                pin = app.hub.screen.pin(studio, request_id=request.admission.request_id,
                                         timing='server-receive-legacy')
                pinned = pin.wire()
                hide_ctx = ToolContext('chat', 2, 75, 'Hide the current camera trails', asked=request.asked,
                    admission=request.admission, request_id=request.admission.request_id,
                    client=studio, workspace_id=CLIENT['workspaceId'], request_snapshot=pin)
                hidden, actual_ctx = await plan(app, [('present', {'what': item.id, 'do': 'hide'})],
                                               ctx=hide_ctx, ticket=request)
                assert actual_ctx is hide_ctx and actual_ctx.request_snapshot is pin
                assert pin.wire() == pinned and pin.observation.client.wire() == CLIENT
                assert hidden[0].occurrence.request_id == request.admission.request_id
                assert actual_ctx.admission is request.admission and request.admission.used == 0
            finally:
                request.seal()
            succeeded(hidden[0])
            boundary = len(transport.messages)
            await receive(app, forward[-1].image, 76, origin + 2.6)
            hidden_header, hidden_raw = packet(transport, 76)
            assert hidden_raw and hidden_header.get('motionTracks') is None
            await asyncio.sleep(.4)
            assert not [m for m in transport.messages[boundary:] if m.get('type') == 'motion-tracks' and m.get('basis') == 'camera-pixels']
        finally:
            await app.stop()

    asyncio.run(go())


def test_explicit_live_source_with_show_false_is_dormant_until_an_owned_view_opens(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        transport, _, origin = await initial(app, studio)
        try:
            runs, ctx = await plan(app, [('track_motion', {'seconds': 1.5, 'live': True, 'show': False})])
            succeeded(runs[0])
            item = tracks_of(app.hub).latest()
            assert item is not None and len(item.rows) == 3
            assert len(runs[0].outputs) == 2, 'MISSING_FEATURE: explicit live mode must expose its actual dormant source'
            assert runs[0].outputs[1]['source']['kind'] == 'live'
            boundary = len(transport.messages)
            extra = clip([(680, 330, 30, 0)], [], seconds=.1, fps=30)[0]
            publish(app, extra.image, 61, origin + 2.1)
            await asyncio.sleep(.4)
            assert not [m for m in transport.messages[boundary:] if m.get('type') == 'motion-tracks' and m.get('basis') == 'camera-pixels']
            assert app.hub.track_layer.shown is None
            assert ctx.admission.used == 0 and ctx.inspection_slots == []
        finally:
            await app.stop()

    asyncio.run(go())


def test_default_finite_motion_keeps_only_the_original_captured_source(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        transport, _, _ = await initial(app, studio)
        try:
            runs, ctx = await plan(app, [('track_motion', {'seconds': 1.5, 'show': False})])
            succeeded(runs[0])
            item = tracks_of(app.hub).latest()
            assert item is not None and item.capture_id == 60 and len(item.rows) == 3
            assert len(runs[0].outputs) == 1
            assert runs[0].outputs[0]['source'] == {'day': item.day, 'id': item.id, 'version': None, 'picture': 0}
            assert not [m for m in transport.messages if m.get('basis') == 'camera-pixels']
            await receive(app, clip([], [], seconds=.1, fps=30)[0].image, 61, time.time())
            finite_header, finite_raw = packet(transport, 61)
            assert finite_raw and finite_header.get('motionTracks') is None
            assert ctx.admission.used == 0 and ctx.inspection_slots == []
        finally:
            await app.stop()

    asyncio.run(go())


def test_published_motion_frames_kept_in_the_real_ring_release_transient_work_and_rows(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        transport, _, origin = await initial(app, studio)
        try:
            runs, _ = await plan(app, [('track_motion', {'seconds': 1.5, 'live': True})])
            succeeded(runs[0])
            producer = app.hub.track_layer._live  # passive original ownership observation
            assert producer is not None and producer.owners
            forward = clip([(680, 330, 30, 0)], [(500, 250)], seconds=.1, fps=30, noise=4)
            retained = []
            for fid, sample in enumerate(forward, 61):
                frame = await receive(app, sample.image, fid, origin + 2 + sample.t)
                header, raw = packet(transport, fid)
                assert raw == frame.jpeg and header['motionTracks']['tracks'], 'SETUP: actual paired sample absent'
                assert app.hub.source.get(fid) is frame, 'SETUP: original Frame is not retained by the real ring'
                retained.append((frame, frame.jpeg, frame.seq, frame.boot))
        finally:
            await app.stop()  # actual shutdown retires the producer and its owned reservation
        assert not producer.owners and producer.latest is None and producer.reservation is None
        for frame, jpeg, seq, boot in retained:
            assert (frame.jpeg, frame.seq, frame.boot) == (jpeg, seq, boot)
            assert getattr(frame, '_motion_work', None) is None, 'original ring must not retain retired producer claims'
            assert getattr(frame, '_motion_result', None) is None, 'original ring must not retain published motion rows'

    asyncio.run(go())


def test_cancelled_actual_intake_releases_transient_claim_after_its_original_decoder_settles(tmp_path, monkeypatch):
    app, studio = built(tmp_path)
    entered, release = threading.Event(), threading.Event()
    decode = cv2.imdecode

    async def go():
        transport, _, origin = await initial(app, studio)
        intake = None
        try:
            runs, _ = await plan(app, [('track_motion', {'seconds': 1.5, 'live': True})])
            succeeded(runs[0])
            producer = app.hub.track_layer._live
            assert producer is not None and producer.owners
            image = clip([(680, 330, 30, 0)], [], seconds=.1, fps=30, noise=4)[0].image
            ok, encoded = cv2.imencode('.jpg', image)
            assert ok
            original_jpeg = encoded.tobytes()

            def held_external_decode(data, flags):
                if data.tobytes() == original_jpeg and flags == cv2.IMREAD_GRAYSCALE:
                    entered.set()
                    assert release.wait(HANG_S), 'SETUP: original external decoder was never released'
                return decode(data, flags)  # original decoder/analysis/producer, no fabricated result

            monkeypatch.setattr(cv2, 'imdecode', held_external_decode)
            intake = asyncio.create_task(receive(app, image, 61, origin + 2.1))
            await until(entered.is_set, 'SETUP: actual existing analysis worker never entered external decode')
            retained = app.hub.source.get(61)
            assert retained is not None and retained.jpeg == original_jpeg
            assert not [raw for raw in transport.packets if packet_id(raw) == 61]
            intake.cancel()  # actual external camera callback cancellation, not a delayed Ticket/producer
            await asyncio.gather(intake, return_exceptions=True)
            await app.stop()
            release.set()
            # Queue a passive fence on the existing executor, after its original
            # decoder/analysis job. This neither changes its schedule nor owns work.
            await asyncio.wrap_future(app.hub._analysis.submit(lambda: None))
            assert not producer.owners and producer.latest is None and producer.reservation is None
            assert (retained.jpeg, retained.seq, retained.boot) == (original_jpeg, 8061, 'external-motion-camera')
            assert getattr(retained, '_motion_work', None) is None, 'cancelled ring entry retained its original producer'
            assert getattr(retained, '_motion_result', None) is None, 'cancelled work must not attach unowned rows after settlement'
        finally:
            release.set()
            if intake is not None and not intake.done():
                intake.cancel()
                await asyncio.gather(intake, return_exceptions=True)
            await app.stop()

    asyncio.run(go())


def packet_id(raw):
    length = struct.unpack('>I', raw[4:8])[0]
    return json.loads(raw[8:8 + length])['id']


def test_native_only_current_sample_style_preserves_opacity_without_borrowing_finite_ranks(tmp_path):
    """Producer configuration/snapshot seam, not Screen or native UI proof."""
    app, studio = built(tmp_path)

    async def go():
        _, _, origin = await initial(app, studio)
        lease = snapshot = None
        try:
            finite, _ = await plan(app, [('track_motion', {'seconds': 1.5, 'live': True, 'show': False})])
            succeeded(finite[0])
            item = tracks_of(app.hub).latest()
            assert finite[0].outputs[0]['source'] == {'day': item.day, 'id': item.id, 'version': None, 'picture': 0}
            source = finite[0].outputs[1]['source']
            assert source['kind'] == 'live' and source['identity']['resource'] == finite[0].outputs[0]['source']
            layer = app.hub.track_layer
            chosen, _ = select_tracks(item, by='net', top=1)
            assert len(chosen) == 1 and item.rows, 'SETUP: original measured ranking unavailable'
            # The same public motion writer/configuration seam used by present;
            # no renderer ACK or visibility is fabricated for this producer test.
            style = {**layer.style, **colour_style(item, 'net'), 'opacity': .5,
                     'emphasis': {'members': [chosen[0]['id']], 'rest': 'fade'}}
            configured = await apply(app.hub, lambda _: setattr(layer, 'style', style), scope='motion')
            assert configured.ok and layer.shown is None
            measured_style = copy.deepcopy(layer.style)
            assert measured_style['colour'] == 'column' and measured_style['values']
            assert measured_style['emphasis']['members'] and measured_style['opacity'] == .5
            # Lower producer API boundary: this external server visibility token
            # is not a native client grant. Whole broker admission is tested separately.
            lease = layer.acquire_live(source, 'synthetic-native-visibility-owner')
            image = clip([(680, 330, 30, 0)], [], seconds=.1, fps=30, noise=4)[0].image
            incoming = await receive(app, image, 61, origin + 2.1)
            snapshot = layer.acquire_snapshot(source, 'synthetic-native-original-reader')
            assert snapshot is not None and snapshot.capture.jpeg == incoming.jpeg
            assert snapshot.wire['tracks'] and snapshot.wire['sample']['frameId'] == 61
            assert layer.style == measured_style, 'qualifying live style must not mutate finite measured presentation'
            current = snapshot.wire['style']
            assert current['opacity'] == .5 and current['colour'] == 'id'
            assert not {'column', 'values', 'legend', 'emphasis'} & current.keys(), 'finite ranks cannot identify current camera tracks'
            snapshot.release()
            snapshot = None
            for colour, expected in [('red', 'uniform'), ('id', 'id')]:
                uniform = {k: v for k, v in layer.style.items() if k not in ('column', 'values', 'legend', 'value')}
                uniform['colour'] = expected
                if colour == 'red':
                    uniform['value'] = '#e74c3c'
                changed = await apply(app.hub, lambda _: setattr(layer, 'style', uniform), scope='motion')
                assert changed.ok and layer.shown is None
                snapshot = layer.acquire_snapshot(source, 'synthetic-native-style-reader')
                assert snapshot.capture.jpeg == incoming.jpeg and snapshot.wire['sample']['frameId'] == 61
                assert snapshot.wire['style']['opacity'] == .5 and snapshot.wire['style']['colour'] == expected
                if colour == 'red':
                    assert snapshot.wire['style']['value'] == layer.style['value'] == '#e74c3c'
                snapshot.release()
                snapshot = None
        finally:
            if snapshot is not None:
                snapshot.release()
            if lease is not None:
                lease.release()
            await app.stop()

    asyncio.run(go())
