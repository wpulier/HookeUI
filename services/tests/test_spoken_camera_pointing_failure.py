"""TEST-only SCM-S1: scene pointing cannot replace original camera acquisition.

Reuse frozen actual App/SpokenRig/Hands. Synthetic ingress, canned external
vision replies and a synthetic segmentation-worker response are the only inputs.
The worker response uses real mask_to_polygons; Agent.point, Runner, Capture
leases, CPU placement/template creation, apply, Scene and receipts remain real.
No fake successful outline, manually inserted object/run, core patch or extra
Hold is used. Root alone collects/runs; static authorship is not observed RED.
"""
import asyncio
import hashlib
import json

import cv2
import numpy as np
import pytest

from gpu_worker.worker import mask_to_polygons
from orchestrator.coords import frame_box_to_gemini
from orchestrator.inspection import image_context
from orchestrator.marks import fov_crop
from orchestrator.operations import runner
from orchestrator.ticket import current
from tests.test_spoken_camera_lifecycle import ResponseGate
from tests.test_spoken_camera_request_selection import (
    MISSING_CAMERA, SpokenRig, actual_pictures, assert_spoken_camera,
)
from tests.waiting import HANG_S


WORDS = 'Acquire my original specimen view and point to the green landmark in the view now.'
LABEL = 'Synthetic green landmark'
MISSING_ANSWER = 'The original selected camera input is unavailable; the later view has been outlined.'
HELD_ANSWER = 'I inspected the original dated camera crop and outlined the later view.'


class ExternalMaskWorker:
    """Segmentation IO boundary, explicitly synthetic; no model is loaded.

    Its green-pixel mask comes from the actual JPEG delivered to the worker,
    inside the real box prompt. Production contour conversion and subsequent
    placement/template work run unchanged; this is not model-quality evidence.
    Records are passive and assertions occur outside the callback.
    """
    ready = True
    device = 'explicitly-synthetic-segmentation-boundary'

    def __init__(self):
        self.calls = []

    async def call(self, op, **payload):
        if op != 'segment':
            raise RuntimeError('SETUP: unexpected segmentation boundary operation ' + op)
        owner = current()
        jpeg = payload['jpeg']
        pixels = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if pixels is None:
            raise RuntimeError('SETUP: external segmentation JPEG could not decode')
        blue, green, red = (pixels[:, :, n].astype(np.int16) for n in range(3))
        mask = ((green > 180) & (green > red + 50) & (green > blue + 30)).astype(np.uint8)
        x0, y0, x1, y1 = (int(round(v)) for v in payload['box'])
        bounded = np.zeros_like(mask)
        bounded[max(0, y0):min(mask.shape[0], y1), max(0, x0):min(mask.shape[1], x1)] = 1
        mask *= bounded
        polygons = mask_to_polygons(mask)
        self.calls.append({'owner': owner, 'frame': payload['frame_id'],
                           'sha256': hashlib.sha256(jpeg).hexdigest(),
                           'model': payload['model'], 'box': tuple(payload['box']),
                           'maskPixels': int(mask.sum()), 'polygons': polygons,
                           'heldAtEntry': owner is not None and owner.held(payload['frame_id']) is not None})
        return {'polygons': polygons, 'area': int(mask.sum()), 'score': 0.9, 'ms': 0.0,
                'note': 'Explicitly synthetic external green-pixel segmentation response'}


def assert_raw_look_input(sent, capture):
    """Independent source pixels, not the acquisition encoder's own output."""
    assert len(sent.images) == 1, 'SETUP: actual raw look must have one supplied camera picture'
    crop = fov_crop(capture.fov, capture.width, capture.height)
    factor = min(1.0, 1024 / max(crop.width, crop.height))
    raw = cv2.imdecode(np.frombuffer(capture.jpeg, np.uint8), cv2.IMREAD_COLOR)
    shown = cv2.imdecode(np.frombuffer(sent.images[0], np.uint8), cv2.IMREAD_COLOR)
    assert raw is not None and shown is not None, 'SETUP: actual raw look input must decode'
    expected = raw[crop.y:crop.y + crop.height, crop.x:crop.x + crop.width]
    if factor < 1:
        expected = cv2.resize(expected, None, fx=factor, fy=factor, interpolation=cv2.INTER_AREA)
    assert shown.shape == expected.shape, 'SETUP: actual look input must show the later eyepiece crop'
    error = np.abs(shown.astype(np.int16) - expected.astype(np.int16))
    assert float(error.mean()) < 3 and float(np.percentile(error, 95)) < 8, (
        'SETUP: raw look must use the actual distinct later frame, not the original acquisition')


def assert_real_point(rig, worker, later, origin, final):
    """Positive producer/source/lifetime preconditions precede SCM-S1 oracles."""
    assert len(worker.calls) == 1, 'SETUP: actual Runner must reach one external segmentation response'
    call = worker.calls[0]
    assert call['frame'] == later.id and call['sha256'] == hashlib.sha256(later.jpeg).hexdigest()
    assert call['model'] == 'edgetam' and call['heldAtEntry'], 'SETUP: actual segmentation must own its source'
    assert call['maskPixels'] > 1000 and call['polygons'], 'SETUP: real mask conversion must produce useful polygons'
    assert call['owner'] is final.ticket and call['owner'] is not origin
    assert final.ticket.admission is origin.admission and final.ticket.held(later.id) is later
    objects = [obj for obj in rig.app.hub.scene.objects.values() if obj.label == LABEL]
    assert len(objects) == 1, 'SETUP: actual Agent.point/apply must create the useful outline before SCM-S1 checks'
    obj = objects[0]
    assert obj.origin == later.id and obj.anchor_frame == later.id and obj.polygons
    assert obj.shown and obj.track == 'tracking', 'SETUP: CPU placement must verify the actual useful outline'
    assert obj.template is not None and obj.template.size > 0, 'SETUP: actual CPU template creation must run'
    assert obj.made_by and obj.made_by['implementation'] == 'edgetam'
    execution = runner(rig.app.hub).get(obj.made_by['execution'])
    assert execution is not None and execution.capture == later.id and execution.executed == 'edgetam'
    assert len(execution.attempts) == 1 and execution.attempts[0].ok is True
    assert obj.id in execution.outputs, 'SETUP: actual Runner must record the produced scene object'
    return obj


async def pointing_scenario(tmp_path, monkeypatch, original_state):
    record = {}

    def response(sent):
        if sent.phase == 'hands':
            return {'intent': 'request', 'needs_look': True,
                    'answer': 'I am acquiring the original camera input and looking for the landmark.',
                    'actions': [{'tool': 'inspect_camera', 'args': {}}],
                    'observation_scope': 'none', 'input_refs': []}
        if sent.phase == 'look':
            later = record['later']
            crop = fov_crop(later.fov, later.width, later.height)
            return {'answer': 'I am outlining the green landmark on this supplied view.',
                    'targets': [{'box_2d': frame_box_to_gemini((520, 290, 710, 480), crop),
                                 'label': LABEL, 'confidence': 0.9}], 'actions': [],
                    'observation_scope': 'specimen-camera', 'input_refs': ['camera']}
        if sent.phase not in ('query', 'final', 'late'):
            raise RuntimeError('SETUP: unexpected answer phase ' + str(sent.phase))
        pictures = actual_pictures(rig)
        return {'answer': HELD_ANSWER if original_state == 'held' else MISSING_ANSWER, 'actions': [],
                'observation_basis': [{'scope': image.facts['channel'], 'input_refs': [number]}
                                      for number, image in enumerate(pictures, 1)]}

    rig = SpokenRig(tmp_path, monkeypatch, response)
    rig.app.hub.settings.speculate = 'needs-look'
    rig.app.hub.settings.outline = 'edgetam'
    rig.app.hub.settings.outline_check = 'off'  # declared option; keep this gate on original answer calls
    worker = ExternalMaskWorker()
    rig.app.hub.gpu = worker  # authorized external segmentation IO boundary only
    look_gate = ResponseGate(monkeypatch, rig, 'look')
    final_gate = ResponseGate(monkeypatch, rig, 'final')
    try:
        source = rig.app.hub.source
        freshness = source.stale_seconds
        original = None
        if original_state in ('stale', 'held'):
            original = await rig.camera()
        if original_state == 'stale':
            source.stale_seconds = 0  # external feed freshness input before original selection
            assert not source.is_fresh(), 'SETUP: the original frame must actually be stale'
        elif original_state == 'absent':
            assert source.latest() is None and rig.app.hub.latest_info is None
        hands = rig.hands(WORDS)
        await asyncio.wait_for(rig.hands_entered.wait(), HANG_S)
        first = rig.sends[0]
        origin = first.ticket
        assert origin is rig.spoken._hands_requests[hands] and first.phase == 'hands'
        assert first.admission is origin.admission and origin.admission.used == 1
        assert not first.images and not rig.started and not rig.looks and not rig.snapshots
        if original_state == 'held':
            assert origin.held(original.id) is original, 'SETUP: original positive acquisition must own real bytes before await'
        else:
            assert not origin.holds, 'SETUP: absent/stale original selection must not own camera bytes'

        source.stale_seconds = freshness
        source.ring_seconds = 0  # later raw look's best-frame candidates must come from the new ingress
        later = await rig.camera(value=25, boot='synthetic-pointing-later', seq=901)
        record['later'] = later
        assert source.is_fresh() and later is not original
        if original is not None:
            assert later.id != original.id and later.boot != original.boot and later.jpeg != original.jpeg
            assert original.id not in source.ring
        rig.hands_release.set()
        await look_gate.wait()
        handled = await asyncio.wait_for(hands, 4)
        look = handled['look']
        assert look is not None and look.task in origin.tasks and not look.task.done()
        assert look.view[0] is later and look.request_id == origin.admission.request_id
        assert look_gate.sent.ticket is origin and look_gate.sent.phase == 'look'
        assert look_gate.sent.admission is origin.admission
        assert_raw_look_input(look_gate.sent, later)
        assert len(rig.looks) == len(rig.snapshots) == 1 and len(rig.started) == 1
        run, ctx, owner = rig.started[0]
        assert run is handled['runs'][0] and run.tool == 'inspect_camera' and owner is origin
        assert run.occurrence is not None and run.occurrence.request_id == origin.admission.request_id
        assert ctx is handled['ctx'] and ctx.admission is origin.admission
        selection, slots = ctx.camera_selection, ctx.inspection_slots
        assert selection.requested_frame is None
        if original_state == 'held':
            assert selection.capture is original and run.ok is True and len(run.images) == len(slots) == 1
            assert origin.held(original.id) is original
            assert_spoken_camera(run.images[0], original)
            assert look_gate.sent.images[0] != run.images[0].jpeg
        else:
            assert selection.capture is None and run.ok is False and run.summary and not run.images
            assert not slots and not actual_pictures(rig)
        assert [attempt.phase for attempt in origin.admission.attempts] == ['hands', 'look']
        assert [attempt.status for attempt in origin.admission.attempts] == ['done', 'running']
        assert not worker.calls and not rig.app.hub.scene.objects

        handoff = rig.handoff('synthetic-pointing-' + original_state, WORDS)
        look_gate.release.set()
        await final_gate.wait()
        obj = assert_real_point(rig, worker, later, origin, final_gate.sent)
        assert ctx.camera_selection is selection and ctx.inspection_slots is slots
        if original_state == 'held':
            assert final_gate.sent.ticket.held(original.id) is original
            assert final_gate.sent.images == (run.images[0].jpeg,)
            assert image_context(run.images) in final_gate.sent.prompt
            assert MISSING_CAMERA not in final_gate.sent.prompt
        else:
            assert not final_gate.sent.images and not actual_pictures(rig)
        final_gate.release.set()
        result = await asyncio.wait_for(handoff, 4)

        # All positive setup/accounting evidence is outside provider callbacks,
        # and established before the intended failure-retention assertions.
        assert rig.turns[-1]['objectIds'] == [obj.id] and result['outlined'] == [LABEL]
        assert result['actions'] == rig.turns[-1]['actions']
        assert [sent.phase for sent in rig.sends] == ['hands', 'look', 'final']
        budget = origin.admission
        assert all(sent.admission is budget for sent in rig.sends)
        assert budget.used == rig.turns[-1]['calls'] == 3
        assert [attempt.phase for attempt in budget.attempts] == ['hands', 'look', 'final']
        assert [attempt.status for attempt in budget.attempts] == ['done'] * 3
        assert len({attempt.attempt_id for attempt in budget.attempts}) == 3
        assert all(attempt.request_id == budget.request_id for attempt in budget.attempts)
        assert len(rig.started) == 1 and len(worker.calls) == 1 and len(rig.looks) == 1

        public = [receipt for receipt in result['actions'] if receipt.get('actionId') == run.occurrence.action_id]
        answers = [sent for sent in rig.sends if sent.phase in ('query', 'final', 'late')]
        assert answers and all(sent.admission is budget for sent in answers)
        if original_state == 'held':
            assert public == [run.wire()] and public[0]['ok'] is True
            assert result['answer'] == HELD_ANSWER
            assert len(actual_pictures(rig)) == 1 and selection.capture is original
            assert_spoken_camera(run.images[0], original)
            for sent in answers:
                assert sent.images == (run.images[0].jpeg,) and image_context(run.images) in sent.prompt
                assert MISSING_CAMERA not in sent.prompt
        else:
            violations = []
            if public != [run.wire()]:
                violations.append('original failed acquisition occurrence/receipt disappeared')
            for sent in answers:
                if MISSING_CAMERA not in sent.prompt:
                    violations.append(sent.phase + ' lost the original unsupplied camera obligation')
                if run.summary not in sent.prompt or json.dumps(run.occurrence.action_id) not in sent.prompt:
                    violations.append(sent.phase + ' lost the concrete original failed receipt/source identity')
            assert not violations, ('MISSING_FEATURE SCM-S1: useful later pointing cannot replace original acquired input: '
                                    + '; '.join(violations))
            assert public[0]['ok'] is False and public[0]['requestId'] == budget.request_id
            assert result['answer'] == MISSING_ANSWER and selection.capture is None
    finally:
        await rig.close()


@pytest.mark.parametrize('original_state', ['absent', 'stale'])
def test_spoken_useful_later_point_retains_original_camera_failure(tmp_path, monkeypatch, original_state):
    asyncio.run(pointing_scenario(tmp_path, monkeypatch, original_state))


def test_spoken_useful_later_point_keeps_successful_original_camera_input(tmp_path, monkeypatch):
    asyncio.run(pointing_scenario(tmp_path, monkeypatch, 'held'))
