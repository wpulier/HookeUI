"""TEST-only original Sight ROI contract, authored against eb7344b.

Actual App, camera intake, Capture, Sight.look, accepted Send pin, Brain, Registry,
PreparedSources, Presentations and COMMANDS.screen own production state/proof.
Only camera JPEG ingress, external provider replies and rendering are synthetic.
No successful ToolRun, owner, prepared lease, Scene object or completion is made
by a fixture. The renderer reports the received command and decoded token bytes;
this is backend contract evidence, never browser paint or model-quality evidence.

Proposed public selector: present(do="open", source={"sight": "s2"}). The
positive cases deliberately require the missing producer join without importing
any proposed API. Voice has no accepted workspace origin and is negative only.
"""
import asyncio
import copy
import math
from types import SimpleNamespace
from uuid import UUID

import cv2
import numpy as np
import pytest

from orchestrator.brain.speech import SpokenRequests
from orchestrator.capture import RUN, captures
from orchestrator.commands import COMMANDS
from orchestrator.ticket import current
from tests.test_native_requested_goal_binding import close_app, inline_report
from tests.test_workspace_actions_app import CONTROL_PROPERTY, WORKSPACE, built, digest
from tests.waiting import HANG_S


def camera_jpeg(replacement=False):
    """Distinct camera-space regions, surrounded by a textured eyepiece disc."""
    gray = np.full((640, 640), 5, np.uint8)
    disc = np.zeros_like(gray)
    cv2.circle(disc, (320, 320), 280, 1, -1)
    texture = np.random.default_rng(641).integers(95, 210, gray.shape, dtype=np.uint8)
    gray[disc.astype(bool)] = texture[disc.astype(bool)]
    pixels = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
    pixels[220:420, 110:275] = (20, 210, 35)
    pixels[220:420, 365:530] = (210, 35, 20) if replacement else (20, 35, 210)
    ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 96])
    assert ok
    return encoded.tobytes()


async def render_token(studio, command):
    """External renderer boundary; do not consult a newer PresentationState.

    Owners/versions/causal IDs are read exclusively from the received command.
    Media is read through the actual accepted prepared-token API and decoded.
    The synthetic surface then measures a floating rectangle and reports through
    the existing AppStudio/COMMANDS.screen ingress. No comparator is replaced.
    """
    assert command['schema'] == 'presentation-command-v2'
    assert command['op'] == 'open'
    fence = command['fence']
    assert fence['client'] == studio.client_ref
    assert fence['appRunId'] == RUN
    assert command['targets'] == [state['id'] for state in command['instances']]
    results = []
    for state in command['instances']:
        iid = state['id']
        bound = fence['targets'][iid]
        token = state['delivery']['sourceToken']
        context = studio.app.workspace_context(studio, request_id=command['requestId'],
                                               owner=command['actionId'])
        lease = studio.app.services.prepared_sources.borrow(token, context)
        try:
            data = studio.app.services.prepared_sources.get(token, context)
            assert lease.source.to_wire() == state['sourceRef'] == bound['sourceRef']
            assert digest(data) == lease.payload_binding['servedDigest']
            decoded = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
            assert decoded is not None, 'SETUP: actual delivered bytes must decode'
            delivery = lease.to_wire()
        finally:
            lease.release()
        studio.decoded[iid] = decoded
        h, w = decoded.shape[:2]
        ref, view = state['sourceRef'], state['view']
        versions, owners = state['propertyVersions'], state['propertyOwners']
        rect = [*state['geometryIntent']['position'], *(state['geometryIntent']['size'] or [360, 280])]
        content = [rect[0], rect[1] + 30, rect[2], rect[3] - 30]
        scale = min(content[2] / w, content[3] / h) * view['zoom']
        tx, ty = content[2] / 2 - view['pan'][0] * w * scale, content[3] / 2 - view['pan'][1] * h * scale
        paint = f'synthetic-paint:{studio.seq + 1}:{iid}'
        controls = {name: {'id': f'{name}:{iid}', 'role': ('drag-handle' if name == 'move' else 'resize-handle' if name == 'resize' else 'button'), 'editable': False,
            'enabled': True, 'present': True, 'targetId': iid, 'stateRevision': versions[prop]}
            for name, prop in CONTROL_PROPERTY.items()}
        controls['zoom']['observedValue'] = view['zoom']
        studio.items[iid] = {'id': iid, 'rootId': 'synthetic-root:' + iid, 'workspaceId': WORKSPACE,
            'kind': 'image', 'sourceRef': copy.deepcopy(ref), 'sourceKey': ref['key'], 'sourceToken': token,
            'sourceGeneration': state['sourceGeneration'], 'lifetimeGeneration': state['lifetimeGeneration'],
            'revision': state['revision'], 'propertyVersions': dict(versions), 'propertyOwners': dict(owners),
            'open': True, 'visible': True, 'connected': True, 'staged': False,
            'rect': rect, 'contentRect': content, 'clipRect': content, 'mode': 'floating',
            'phase': 'drawn', 'drawRef': paint,
            'load': {'state': 'drawn', 'sourceKey': ref['key'], 'sourceGeneration': state['sourceGeneration'],
                     'naturalSize': [w, h], 'drawToken': paint, 'token': token},
            'payloadBinding': {**delivery['payloadBinding'], 'decodedToken': token}, 'controls': controls,
            'zoom': view['zoom'], 'pan': list(view['pan']), 'view': copy.deepcopy(view),
            'viewTransform': {'sourceToContent': [scale, 0, 0, scale, tx, ty], 'contentOrigin': content[:2],
                'sourceToWorkspace': [scale, 0, 0, scale, tx + content[0], ty + content[1]],
                'originalSize': [w, h], 'transformRevision': state['revision']},
            'z': len(studio.items) + 1}
        results.append({**{key: command[key] for key in ('commandId', 'requestId', 'actionId', 'appRunId')},
            'presentationId': iid, 'reportSeq': studio.seq + 1, 'phase': 'drawn',
            'lifetimeGeneration': state['lifetimeGeneration'], 'sourceGeneration': state['sourceGeneration'],
            'committedRevision': state['revision'], 'committedPropertyVersions': dict(bound['committedPropertyVersions']),
            'propertyVersions': dict(versions), 'propertyOwners': dict(owners),
            'sourceToken': token, 'sourceKey': ref['key'], 'drawRef': paint, 'absent': False})
    studio.rendered_commands[command['commandId']] = copy.deepcopy(command)
    studio.keep_results(results)
    await studio.send_report()


class Rig:
    def __init__(self, tmp_path, monkeypatch):
        self.app, self.studio = built(tmp_path)
        self.studio.decoded = {}
        monkeypatch.setattr(self.studio, 'observe_command', lambda command: render_token(self.studio, command))
        settings = self.app.hub.settings
        settings.fast = settings.vision = settings.sight = 'gemini-3.5-flash'
        settings.compare, settings.pointing, settings.closeup = '', 'box', 'single'
        settings.outcome_lines = settings.speculate = settings.outline_check = 'off'
        settings.quality, settings.dust = 'tell', 'tell'
        self.app.hub.scale.set_objective(40, via='synthetic ROI test operator')
        self.seq = 0
        self.actions, self.sends, self.sight_sends, self.runs = [], [], [], []
        self.sight_things = [
            {'name': 'synthetic green region', 'where': 'left', 'box_2d': [350, 200, 650, 400], 'confidence': 0.8},
            {'name': 'synthetic red region', 'where': 'right', 'box_2d': [350, 600, 650, 800], 'confidence': 0.8}]
        self.planned = False
        self.entered, self.release, self.finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.block = False
        # The actual Registry's supported finished observer, not a replacement handler.
        self.app.brain.registry.finished.append(self.record)
        monkeypatch.setattr(self.app.agent, 'vision', lambda _option: SimpleNamespace(ask=self.provider))

    def record(self, run, ctx):
        self.runs.append((run, ctx))
        if len(self.runs) >= len(self.actions):
            self.finished.set()

    async def provider(self, images, prompt, **kwargs):
        ticket = current()
        admission = getattr(ticket, 'admission', None)
        if admission is None:
            assert len(images) == 1, 'SETUP: Sight uses its real producing image'
            self.sight_sends.append(tuple(images))
            data = {'summary': 'Two synthetic colored regions.', 'specimen': 'synthetic test slide',
                    'focus': 'sharp', 'things': copy.deepcopy(self.sight_things)}
        else:
            phase = admission.attempts[-1].phase
            self.sends.append(SimpleNamespace(ticket=ticket, admission=admission, phase=phase, images=tuple(images)))
            first = not self.planned
            self.planned = True
            if first and self.block:
                self.entered.set()
                await self.release.wait()
            data = {'answer': 'I am opening the selected captured regions.' if first else 'The operation results are available.',
                'targets': [], 'actions': copy.deepcopy(self.actions) if first else [],
                'intent': 'request', 'needs_look': False, 'observation_scope': 'structural-workspace', 'input_refs': []}
        return SimpleNamespace(data=data, latency_ms=0, error=None, model='explicitly-canned-external-provider')

    async def feed(self, replacement=False):
        self.seq += 1
        await self.app.hub.source._accept(camera_jpeg(replacement), seq=self.seq, boot='synthetic-roi-camera')
        frame = self.app.hub.source.latest()
        captured = captures(self.app.hub).get(frame.id)
        assert captured is not None and captured.jpeg == frame.jpeg
        return captured

    async def observe(self):
        view = await self.app.sight.look()
        assert view is not None
        captured = captures(self.app.hub).get(view.frame_id)
        assert captured is not None
        return view, captured

    def select(self, *ids):
        self.actions = [{'tool': 'present', 'args': {'do': 'open', 'source': {'sight': sid}}} for sid in ids]

    async def ask(self):
        self.sent = inline_report(self.studio)
        result = await COMMANDS['ask'](self.app, self.studio,
            {'type': 'ask', 'text': 'Open the selected regions in separate captured-view windows.', 'screen': self.sent})
        await asyncio.wait_for(self.finished.wait(), HANG_S)
        return result

    def no_inspection(self, *, sight_calls=1):
        assert len(self.sight_sends) == sight_calls, 'opening must not reacquire Sight'
        assert self.sends and all(not send.images for send in self.sends)
        admission = self.sends[0].admission
        assert admission.used == len(self.sends) <= 3
        assert all(send.admission is admission and send.ticket is self.sends[0].ticket for send in self.sends)
        assert all(not ctx.inspection_slots for _, ctx in self.runs), 'delivery does not consume either inspection slot'


@pytest.fixture
def rig(tmp_path, monkeypatch):
    instance = Rig(tmp_path, monkeypatch)
    yield instance
    instance.app.sight.stop()
    instance.app.services.presentations.reset_all()
    close_app(instance.app)


def opened(rig, position, view, captured, sid):
    run, ctx = rig.runs[position]
    assert run.ok is True, 'MISSING_FEATURE: real present cannot yet prepare the original Sight ROI'
    assert len(run.outputs) == 1 and run.outputs[0]['kind'] == 'presentation'
    output = run.outputs[0].wire()
    iid = output['instanceId']
    assert iid.startswith('w-')
    UUID(iid[2:])
    assert ctx.client is rig.studio and ctx.request_snapshot.timing == 'client-send'
    assert ctx.request_snapshot.observation.wire()['seq'] == rig.sent['seq']
    assert ctx.request_id == run.occurrence.request_id
    source = output['source']
    assert (source['kind'], source['media'], source['availability']) == ('saved-image', 'capture', 'temporary')
    assert 'resource' not in source['identity'], 'a temporary camera crop is not an invented saved record'
    facts = source['provenance']
    assert {key: facts[key] for key in ('run', 'frame', 'seq', 'boot', 'at')} == {
        'run': RUN, 'frame': captured.id, 'seq': captured.seq, 'boot': captured.boot, 'at': captured.wall_at}
    assert facts['scale'] == captured.scale
    assert facts['parentDigest'] == digest(captured.jpeg)
    assert facts['sight'] == {'id': sid, 'at': view.at}
    assert facts['epoch'] == view.epoch and facts['offset'] == [view.dx, view.dy]
    assert 'outline' not in facts
    box = next(thing.box for thing in view.things if thing.id == sid)
    expected_rect = [math.floor(box[0]), math.floor(box[1]), math.ceil(box[2]), math.ceil(box[3])]
    assert facts['rect'] == expected_rect
    x0, y0, x1, y1 = expected_rect
    assert facts['originalSize'] == [captured.width, captured.height]
    assert facts['naturalSize'] == [x1 - x0, y1 - y0]
    assert facts['cropAffine'] == [1, 0, 0, 1, -x0, -y0]
    context = rig.app.workspace_context(rig.studio, request_id=ctx.request_id, owner=run.occurrence.action_id)
    raw = rig.app.services.prepared_sources.get(output['sourceToken'], context)
    lease = rig.app.services.prepared_sources.borrow(output['sourceToken'], context)
    try:
        assert lease.source.to_wire() == source
        assert lease.payload_binding['servedDigest'] == digest(raw)
        assert lease.payload_binding['originalDigest'] == digest(raw)
        assert source['digest'] == digest(raw)
        assert lease.to_wire()['originalSize'] == facts['naturalSize']
        assert lease.to_wire()['transform'] == [1, 0, 0, 1, 0, 0]
    finally:
        lease.release()
    # Independent lossy-pixel oracle: cut the original decoded JPEG, without
    # calling the producer's crop/encoding helpers or reproducing its encoder.
    original = cv2.imdecode(np.frombuffer(captured.jpeg, np.uint8), cv2.IMREAD_COLOR)[y0:y1, x0:x1]
    actual = rig.studio.decoded[iid]
    assert actual.shape == original.shape
    assert np.abs(actual.astype(float) - original.astype(float)).mean() < 8
    receipts = run.wire()['outcome']['receipts']
    assert receipts and receipts[-1]['status'] == 'confirmed'
    assert rig.studio.items[iid]['sourceRef'] == source
    assert rig.studio.items[iid]['sourceToken'] == output['sourceToken']
    assert rig.studio.items[iid]['controls']['close']['targetId'] == iid
    assert not rig.app.hub.scene.objects, 'ROI delivery must not manufacture outlines'
    return output


def test_selected_s2_uses_sights_producing_capture_not_send_camera(rig):
    async def go():
        await rig.feed()
        view, original = await rig.observe()
        newer = await rig.feed()
        assert newer.id != original.id
        rig.select('s2')
        await rig.ask()
        opened(rig, 0, view, original, 's2')
        rig.no_inspection()
    asyncio.run(go())


@pytest.mark.parametrize('second', ['s2', 's1'], ids=['different-rois', 'same-roi-twice'])
def test_two_original_rois_open_independent_instances_even_for_equal_pixels(rig, second):
    async def go():
        await rig.feed()
        view, original = await rig.observe()
        rig.select('s1', second)
        await rig.ask()
        outputs = [opened(rig, n, view, original, sid) for n, sid in enumerate(('s1', second))]
        assert len({output['instanceId'] for output in outputs}) == 2
        assert len({rig.studio.items[output['instanceId']]['rootId'] for output in outputs}) == 2
        assert len({run.occurrence.action_id for run, _ in rig.runs}) == 2
        if second == 's1':
            assert outputs[0]['source']['identity'] == outputs[1]['source']['identity']
            assert outputs[0]['source']['provenance'] == outputs[1]['source']['provenance']
            assert np.array_equal(rig.studio.decoded[outputs[0]['instanceId']], rig.studio.decoded[outputs[1]['instanceId']])
        else:
            assert outputs[0]['source']['identity'] != outputs[1]['source']['identity']
        for output in outputs:
            item = rig.studio.items[output['instanceId']]
            assert set(CONTROL_PROPERTY) <= set(item['controls'])
            assert all(control['targetId'] == output['instanceId'] for control in item['controls'].values())
        rig.no_inspection()
    asyncio.run(go())


def test_original_s2_and_optics_survive_new_sight_during_provider_await(rig):
    async def go():
        await rig.feed()
        original_view, original = await rig.observe()
        rig.select('s2')
        rig.block = True
        pending = asyncio.create_task(rig.ask())
        await asyncio.wait_for(rig.entered.wait(), HANG_S)
        try:
            # Age the original beyond Sight's actual best-frame settle window.
            await asyncio.sleep(0.7)
            rig.app.hub.scale.set_objective(10, via='synthetic replacement operator')
            await rig.feed(replacement=True)
            rig.sight_things[1]['name'] = 'synthetic replacement blue region'
            latest_view, latest = await rig.observe()
            assert latest_view.frame_id != original_view.frame_id and latest.scale != original.scale
        finally:
            rig.release.set()
        await pending
        opened(rig, 0, original_view, original, 's2')
        rig.no_inspection(sight_calls=2)
    asyncio.run(go())


@pytest.mark.parametrize('observed', [False, True], ids=['no-original-look', 'missing-original-id'])
def test_missing_original_basis_fails_without_latest_or_outline_fallback(rig, observed):
    async def go():
        await rig.feed()
        if observed:
            await rig.observe()
        rig.select('s9' if observed else 's2')
        await rig.ask()
        run, _ = rig.runs[0]
        assert run.ok is False and run.summary and not run.outputs
        assert run.error is None, 'absence must be a concrete production refusal'
        assert run.wire()['outcome']['status'] == 'failed'
        assert not any(message.get('commandId') for message in rig.studio.messages)
        assert not rig.studio.decoded and not rig.app.hub.scene.objects
    asyncio.run(go())


def test_voice_hands_handoff_has_no_native_origin_and_cannot_borrow_typed_client(rig):
    async def go():
        await rig.feed()
        await rig.observe()
        rig.select('s2')
        spoken = SpokenRequests(rig.app.brain, rig.app.dialogue)
        words = 'Open that selected region in its own captured-view window.'
        await spoken.hands(words)
        await spoken.handoff(words, 'synthetic-roi-delegation')
        assert len(rig.runs) == 1, 'the same words must not repeat the native operation'
        run, ctx = rig.runs[0]
        assert ctx.channel == 'voice' and ctx.client is None and ctx.request_snapshot is None
        assert run.ok is False and not run.outputs
        assert run.wire()['outcome']['failure']['code'] == 'CLIENT_MISMATCH'
        assert not any(message.get('commandId') for message in rig.studio.messages)
        assert not rig.app.hub.scene.objects
    asyncio.run(go())


def test_bound_voice_opens_original_region_and_handoff_does_not_repeat_it(rig):
    """Real spoken execution + source delivery; synthetic model and renderer."""
    async def go():
        await rig.feed()
        await rig.observe()
        await rig.studio.send_report()
        rig.select('s2')
        spoken = SpokenRequests(rig.app.brain, rig.app.dialogue, client=rig.studio)
        words = 'Show that region close up in its own window.'
        await spoken.hands(words)
        await spoken.handoff(words, 'bound-voice-region')
        assert len(rig.runs) == 1
        run, ctx = rig.runs[0]
        assert run.ok is True, run.summary
        assert ctx.client is rig.studio and ctx.request_snapshot is not None
        assert len(rig.studio.decoded) == 1
        command, = [m for m in rig.studio.messages if m.get('commandId')]
        assert command['fence']['client'] == rig.studio.client_ref
        assert run.outputs and run.wire()['outcome']['status'] == 'confirmed'
    asyncio.run(go())
