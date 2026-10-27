"""Actual motion producer -> original structural observation -> layer control.

Synthetic external camera/DOM transport only. Real App, CPU motion operation,
Registry, Runner, Ticket, accepted Screen and causal Change confirmation remain
production code. A layer broadcast is not a structural report or painted pixels.
No model, browser or provider is exercised; Root alone executes this cohort.
"""
import asyncio
import copy
import json
import time
import weakref

import pytest

from orchestrator.app import App
from orchestrator.brain.registry import ToolContext
from orchestrator.capture import RUN
from orchestrator.commands import COMMANDS
from orchestrator.motiontracks import tracks_of
from orchestrator.paths import Paths
from orchestrator.ticket import issue
from orchestrator.workspace_observation import ClientRef, ObservationError
from tests.test_current_camera_motion import CameraStudio, initial, receive
from tests.test_workspace_actions_app import CLIENT, STUDIOS, WORKSPACE, plan, succeeded
from tests.waiting import HANG_S


class DelayedStudio(CameraStudio):
    """External renderer waits for actual sample data and a separate report Send.

    Unlike CameraStudio's eager configuration-only fixture, receipt of the layer
    alone does not create a structural root. This matches main.screenItems:
    shownTracks and tracksLayer must both identify the same item.
    """
    def __init__(self, app, ref=CLIENT):
        super().__init__(app)
        self.ref = dict(ref)
        self.layer = None
        self.payload = None
        self.activated = asyncio.Event()
        self.changes = []
        self.disconnect_on_opacity = False

    def hear(self, message):
        if message.get('type') == 'motion-tracks':
            self.payload = copy.deepcopy(message)
            if self.layer and self.layer.get('tracks') == message['id']:
                super().hear(self.layer)
        elif message.get('type') == 'tracks-layer':
            self.layer = copy.deepcopy(message)
            if message.get('tracks'):
                self.activated.set()
            if self.payload and self.payload['id'] == message.get('tracks'):
                super().hear(message)
            if self.disconnect_on_opacity and message.get('style', {}).get('opacity') == .5:
                self.app.hub.screen.gone(self)  # actual socket retirement after external style delivery
        elif message.get('type') == 'present' and message.get('id', '').startswith('tracks:'):
            self.changes.append(copy.deepcopy(message))
            super().hear(message)  # independent observed style, real causal ACK/report
        else:
            super().hear(message)

    async def send_report(self, user=()):
        self.seq += 1
        rows = copy.deepcopy(list(self.track_rows.values()))
        for row in rows:
            row['workspaceId'] = self.ref['workspaceId']
        report = {'type': 'screen', 'schema': 'screen-observation-v2', 'client': dict(self.ref),
            'appRunId': RUN, 'seq': self.seq, 'layoutRevision': self.seq,
            'capturedMonoMs': time.monotonic() * 1000, 'visible': True,
            'complete': False, 'omitted': {'items': 1, 'sources': 0, 'targets': 0, 'edges': 0},
            'viewport': {'layoutSize': [1440, 1000], 'visualRect': [0, 0, 1440, 1000],
                'visualScale': 1, 'documentScroll': [0, 0], 'dpr': 1,
                'visible': True, 'documentFocused': True, 'activeSurface': 'workspace'},
            'items': rows, 'sources': [], 'targets': [], 'edges': [],
            'interaction': {'availability': 'partial', 'unknown': ['synthetic-structural-layer-only']},
            'commandResults': [], 'applied': list(self.applied), 'user': list(user)}
        reply = await COMMANDS['screen'](self.app, self, report)
        assert reply.get('ok') is True, f'SETUP: actual canonical structural report refused: {reply}'


class SampleTransport:
    """Actual Hub bytes/JSON -> external renderer, no private producer shortcut."""
    def __init__(self, studio):
        self.studio = studio
        self.control = asyncio.Queue(maxsize=16)
        self.packets = []

    def offer(self, message):
        if isinstance(message, str):
            parsed = json.loads(message)
            if parsed.get('type') in ('tracks-layer', 'motion-tracks'):
                self.studio.hear(parsed)
        elif isinstance(message, bytes) and message.startswith(b'MMF1'):
            self.packets = (self.packets + [message])[-4:]


def built(tmp_path):
    app = App.build('http://127.0.0.1:1', Paths.under(tmp_path), use_gpu=False)
    studio = DelayedStudio(app)
    app.accept_workspace(studio, ClientRef(CLIENT['workspaceId'], CLIENT['clientId'],
                                          CLIENT['pageEpoch'], CLIENT['connectionEpoch']))
    STUDIOS[id(app)] = weakref.ref(studio)
    return app, studio


async def setup(app, studio):
    await studio.send_report()
    previous, samples, origin = await initial(app, studio)
    app.hub.clients.remove(previous)  # remove eager legacy transport; keep original real camera frames
    transport = SampleTransport(studio)
    app.hub.clients.add(transport)
    ticket = issue(app.hub, 'turn', asked=app.day.here(), channel='chat')
    pin = app.hub.screen.pin(studio, request_id=ticket.admission.request_id, timing='server-receive-legacy')
    ctx = ToolContext('chat', 1, 60, 'Make continuing motion and set its opacity to 50%',
        asked=ticket.asked, admission=ticket.admission, request_id=ticket.admission.request_id,
        client=studio, workspace_id=WORKSPACE, request_snapshot=pin)
    assert pin.observation.wire()['items'] == []
    return transport, samples, origin, ticket, ctx


async def sample(app, studio, transport, samples, origin):
    incoming = await receive(app, samples[-1].image, 61, origin + 2.1)
    assert studio.payload is not None and studio.payload['basis'] == 'camera-pixels'
    assert studio.payload['sample']['frameId'] == incoming.id
    assert studio.payload['id'] == app.hub.track_layer.shown
    assert transport.packets, 'SETUP: actual CPU publication must include an original camera packet'
    assert list(studio.track_rows) == [f'tracks:{studio.payload["id"]}']
    return studio.payload['id']


def test_immediate_produced_layer_control_waits_for_original_structural_report(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        transport, samples, origin, ticket, ctx = await setup(app, studio)
        original_pin = ctx.request_snapshot.wire()
        original_style = copy.deepcopy(app.hub.track_layer.style)
        pending = asyncio.create_task(plan(app, [
            ('track_motion', {'seconds': 1.5, 'live': True, 'show': True}),
            ('present', {'do': 'opacity', 'what': '@1', 'value': '50%'}),
        ], ctx=ctx, ticket=ticket))
        try:
            await asyncio.wait_for(studio.activated.wait(), HANG_S)
            tid = await sample(app, studio, transport, samples, origin)
            # Actual producer/sample reached, but original external report remains held.
            await asyncio.sleep(.05)
            assert app.hub.screen.request_report(ctx.request_snapshot, studio,
                request_id=ctx.request_id)['items'] == []
            # Live activation legitimately drops finite measurement colouring;
            # this requested opacity must remain untouched until represented.
            assert app.hub.track_layer.style['opacity'] == original_style['opacity'], 'BEHAVIOR: no opacity mutation before original structural readiness'
            assert not studio.changes and not pending.done()
            await studio.send_report()  # real originating canonical root arrives separately
            runs, actual_ctx = await pending
            for run in runs:
                succeeded(run)
            assert actual_ctx is ctx and ctx.request_snapshot.wire() == original_pin
            assert runs[0].outputs[0]['source']['id'] == tid
            assert all(run.occurrence.request_id == ctx.admission.request_id for run in runs)
            assert app.hub.track_layer.style['opacity'] == .5
            current = app.hub.screen.request_report(ctx.request_snapshot, studio, request_id=ctx.request_id)
            assert next(row for row in current['items'] if row['id'] == f'tracks:{tid}')['opacity'] == .5
            assert len(studio.changes) == 1 and studio.changes[0]['id'] == f'tracks:{tid}'
            assert ctx.admission.used == 0 and ctx.inspection_slots == []
            assert tracks_of(app.hub).get(tid) is not None
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            ticket.close()
            await app.stop()

    asyncio.run(go())


@pytest.mark.parametrize('unavailable', ['missing', 'disconnected', 'wrong-peer'])
def test_unavailable_original_structural_readiness_cannot_mutate_layer_style(tmp_path, unavailable):
    app, studio = built(tmp_path)

    async def go():
        transport, samples, origin, ticket, ctx = await setup(app, studio)
        original_style = copy.deepcopy(app.hub.track_layer.style)
        pending = asyncio.create_task(plan(app, [
            ('track_motion', {'seconds': 1.5, 'live': True, 'show': True}),
            ('present', {'do': 'opacity', 'what': '@1', 'value': '50%'}),
        ], ctx=ctx, ticket=ticket))
        try:
            await asyncio.wait_for(studio.activated.wait(), HANG_S)
            tid = await sample(app, studio, transport, samples, origin)
            if unavailable == 'disconnected':
                app.hub.screen.gone(studio)
            elif unavailable == 'wrong-peer':
                ref = {'workspaceId': 'peer-workspace', 'clientId': 'peer-client',
                       'pageEpoch': 'peer-page', 'connectionEpoch': 'peer-socket'}
                peer = DelayedStudio(app, ref)
                app.accept_workspace(peer, ClientRef(ref['workspaceId'], ref['clientId'], ref['pageEpoch'], ref['connectionEpoch']))
                peer.hear(studio.payload)
                peer.hear(studio.layer)
                await peer.send_report()
                assert app.hub.screen.observation(peer).wire()['items'][0]['id'] == f'tracks:{tid}'
            runs, _ = await pending
            succeeded(runs[0])
            assert runs[1].ok is False and runs[1].error is None, 'BEHAVIOR: missing original readiness is a typed refusal, not an app exception'
            assert app.hub.track_layer.style['opacity'] == original_style['opacity'], 'BEHAVIOR: neither absent original facts nor a peer may authorize opacity mutation'
            assert not studio.changes
            assert ctx.admission.used == 0 and ctx.inspection_slots == []
        finally:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
            ticket.close()
            await app.stop()

    asyncio.run(go())


def test_original_connection_lost_after_style_delivery_restores_only_owned_style(tmp_path):
    app, studio = built(tmp_path)

    async def go():
        transport, samples, origin, ticket, ctx = await setup(app, studio)
        try:
            produced, _ = await plan(app, [('track_motion', {'seconds': 1.5, 'live': True})], ctx=ctx, ticket=ticket)
            succeeded(produced[0])
            tid = await sample(app, studio, transport, samples, origin)
            await studio.send_report()
            original_style = copy.deepcopy(app.hub.track_layer.style)
            studio.disconnect_on_opacity = True
            # External transport retires the actual original binding synchronously
            # after receiving the changed style, before causal Change dispatch.
            runs, _ = await plan(app, [('present', {'do': 'opacity', 'what': f'tracks:{tid}', 'value': '50%'})],
                                 ctx=ctx, ticket=ticket)
            with pytest.raises(ObservationError) as retired:
                app.hub.screen.observation(studio)
            assert retired.value.code == 'CLIENT_MISMATCH', 'SETUP: original connection actually retired'
            assert runs[0].ok is False and runs[0].error is None
            assert app.hub.track_layer.shown == tid
            assert app.hub.track_layer.style == original_style, 'BEHAVIOR: failed dispatch must not retain its already-applied style'
            assert not studio.changes
            assert ctx.admission.used == 0 and ctx.inspection_slots == []
        finally:
            ticket.close()
            await app.stop()

    asyncio.run(go())
