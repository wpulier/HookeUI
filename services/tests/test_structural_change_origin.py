"""Two real accepted clients and original request-owned structural Changes.

CPU motion/captures are real; camera pixels and renderer reports are synthetic
external inputs. No handler/Screen/Ticket/placement or provider is replaced.
Shared tracks-layer state is broadcast, but a causal Change belongs to one
original request/socket/root. These tests do not certify native window paint.
"""
import asyncio
import copy
import json
import time

import cv2
import pytest

from orchestrator.brain.registry import ToolContext
from orchestrator.capture import RUN
from orchestrator.frames import Frame
from orchestrator.hub import FrameInfo
from orchestrator.motiontracks import keep_measured, tracks_of
from orchestrator.ticket import issue, using
from orchestrator.workspace_observation import ClientRef
from tests.test_comparison_retention import until
from tests.test_legacy_workspace_projection import structural_item, structural_report
from tests.test_movement import MOVERS, STILL, clip
from tests.test_screen_workspace import REF, Rig


PEER = ClientRef('synthetic-other-workspace', 'synthetic-other-client',
                 'synthetic-other-page', 'synthetic-other-socket')


def packets(client):
    """Read only the actual external Client control transport, preserving records."""
    out = []
    while not client.control.empty():
        out.append(json.loads(client.control.get_nowait()))
    return out


async def measured(rig):
    source = clip(MOVERS, STILL, seconds=3, fps=15, noise=8, slide=(0, 0))
    origin = time.time() - 3
    frames = []
    for number, sample in enumerate(source, 1):
        ok, encoded = cv2.imencode('.jpg', sample.image)
        assert ok
        frame = Frame(number, encoded.tobytes(), time.monotonic(), origin + sample.t,
                      7000 + number, 'synthetic-two-studio-motion')
        info = FrameInfo(number, 1280, 720, sample.dx, sample.dy, 7, True, True,
                         None, frame.wall_at, seq=frame.seq, boot=frame.boot)
        rig.app.hub.source.ring[number] = frame
        rig.app.hub._publish(frame, info)
        frames.append((frame, info))
    ticket = issue(rig.app.hub, 'command', asked=rig.app.day.here())
    try:
        with using(ticket):
            item, reused, done = await keep_measured(rig.app.hub, frames, None, '', 3)
        assert not reused and done.execution.outputs == [item.id]
        await until(lambda: item.status == 'saved', 'actual motion bitmap was not saved')
        assert item.view['run'] == RUN and item.view['epoch'] == 7
        assert item.capture_id == 45 and item.span['frames'] == 45
        rig.app.hub.track_layer.show(item)
        return item
    finally:
        ticket.close()


def report(rig, item, *, ref=REF, seq=10, opacity=1.0, applied=()):
    raw = structural_report(rig, seq=seq)
    raw['client'] = ref.wire()
    raw['items'].append(structural_item(f'tracks:{item.id}', 'tracks', {
        'title': item.title, 'shows': item.id, 'opacity': opacity,
        'style': {'colour': 'uniform'}, 'placement': 'not drawn live: not checked yet',
    }))
    for surface in raw['items']:
        surface['workspaceId'] = ref.workspace_id
        surface['rootId'] = f'{ref.workspace_id}:{surface["id"]}'
        surface['lifetimeGeneration'] = f'{ref.page_epoch}:{surface["id"]}'
    raw['applied'] = list(applied)
    return raw


async def world(tmp_path):
    rig = Rig(tmp_path)
    rig.app.accept_workspace(rig.client, REF)
    rig.app.accept_workspace(rig.other, PEER)
    item = await measured(rig)
    original = report(rig, item)
    assert (await rig.ingest(original))['ok'] is True
    ticket = issue(rig.app.hub, 'turn', asked=rig.app.day.here(), channel='chat')
    pin = rig.screen.pin(rig.client, request_id=ticket.admission.request_id)
    ctx = ToolContext('chat', 1, item.capture_id, 'Set this motion layer to half opacity',
        asked=ticket.asked, admission=ticket.admission, client=rig.client,
        workspace_id=REF.workspace_id, request_snapshot=pin)
    rig.now += 0.1
    assert (await rig.ingest(report(rig, item, ref=PEER, seq=1200), rig.other))['ok'] is True
    assert rig.screen.latest['clientId'] == PEER.client_id  # genuine newer peer premise
    initial = {rig.client: packets(rig.client), rig.other: packets(rig.other)}
    return rig, item, ticket, ctx, initial


def start_opacity(rig, item, ticket, ctx):
    with using(ticket):
        return rig.app.brain.registry.start('present', {
            'do': 'opacity', 'what': f'tracks:{item.id}', 'value': '50%',
        }, ctx)


async def settle(run, ticket):
    if run.task is not None and not run.task.done():
        run.task.cancel()
    if run.task is not None:
        await asyncio.gather(run.task, return_exceptions=True)
    ticket.close()


@pytest.mark.parametrize('preceding_message', [False, True])
def test_original_pinned_request_receives_sole_causal_change_despite_newer_peer_report(tmp_path, preceding_message):
    async def go():
        rig, item, ticket, ctx, initial = await world(tmp_path)
        original_view = copy.deepcopy(item.view)
        original_bitmap = tracks_of(rig.app.hub).picture(item)
        if preceding_message:
            # Status traffic is not evidence that this request's presentation arrived.
            rig.client.control.put_nowait(json.dumps({'type': 'health', 'fixture': 'preceding-status'}))
        run = start_opacity(rig, item, ticket, ctx)
        try:
            delivered = {rig.client: [], rig.other: []}
            def presentation_arrived():
                for client in delivered:
                    delivered[client].extend(packets(client))
                return any(packet.get('type') == 'present' for packet in delivered[rig.client]) or run.task.done()
            await until(presentation_arrived, 'origin received no causal presentation')
            commands = {client: [packet for packet in data if packet.get('type') == 'present']
                        for client, data in delivered.items()}
            for client in (rig.client, rig.other):
                shared = [packet for packet in delivered[client] if packet.get('type') == 'tracks-layer']
                assert shared and shared[-1]['style']['opacity'] == 0.5
            assert len(commands[rig.client]) == 1 and commands[rig.other] == [], (
                'shared layer state may broadcast; original causal Change must not reach the peer')
            change = commands[rig.client][0]
            assert change['id'] == f'tracks:{item.id}' and change['set'] == {'opacity': 0.5}
            ack = report(rig, item, seq=11, opacity=0.5, applied=[change['change']])
            next(surface for surface in ack['items'] if surface['id'] == f'tracks:{item.id}')['revision'] = 8
            reply = await rig.ingest(ack)
            assert reply['ok'] is True, reply
            await run.task
            assert run.ok is True and run.occurrence.request_id == ticket.admission.request_id
            assert rig.screen.owner(f'tracks:{item.id}', 'opacity') == f'request:{ticket.id}'
            assert item.view == original_view and tracks_of(rig.app.hub).picture(item) == original_bitmap
            assert ctx.request_snapshot.observation.wire()['seq'] == 10
            assert ticket.admission.used == 0 and run.outputs == []
            assert initial[rig.client] and initial[rig.other]  # initialization history was retained
        finally:
            await settle(run, ticket)
    asyncio.run(go())


@pytest.mark.parametrize('retirement', ['disconnect', 'same-socket-reacceptance'])
def test_retired_original_pin_cannot_redirect_structural_effect_to_live_peer(tmp_path, retirement):
    async def go():
        rig, item, ticket, ctx, initial = await world(tmp_path)
        if retirement == 'disconnect':
            rig.screen.gone(rig.client)
        else:
            rig.app.accept_workspace(rig.client, REF)
        before = copy.deepcopy(rig.app.hub.track_layer.style)
        run = start_opacity(rig, item, ticket, ctx)
        try:
            await until(lambda: run.task.done() or not rig.other.control.empty(),
                        'original lifetime was neither refused nor dispatched')
            delivered = {rig.client: packets(rig.client), rig.other: packets(rig.other)}
            assert not any(packet.get('type') == 'present' for data in delivered.values() for packet in data), (
                'retired original request cannot emit a Change for another accepted workspace')
            await run.task
            assert run.ok is False
            assert rig.app.hub.track_layer.style == before and run.outputs == []
            assert ticket.admission.used == 0 and tracks_of(rig.app.hub).get(item.id) is item
            assert initial[rig.client] and initial[rig.other]
        finally:
            await settle(run, ticket)
    asyncio.run(go())
