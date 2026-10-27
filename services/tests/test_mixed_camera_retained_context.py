"""TEST-ONLY mixed inputs through the actual App, request and acquisition paths.

Synthetic JPEGs enter the real camera ring/Hub and retained Index. Only external
provider replies, capacity inputs and allocator/I/O barriers are scripted. No
future helper import, fake registered camera tool, copied acquisition producer,
successful executor/observer replacement, server, GPU or private input is used.
Root owns execution: this file was written without importing or collecting it.

Proposed public contract: inspect_camera is an installed acquisition-only query
tool. It attaches an actual leased PictureInput from the original Ticket's held
camera Capture, through the same two slots as resources inspection. Its manifest
uses the existing source/hash/size fields, channel=specimen-camera, source facts
run/boot/seq/frame/epoch/at, crop=[x,y,width,height], transform and dated timing.
observation_basis is <=2 closed scope/input_refs entries, with <=2 unique actual
numbered refs. It cannot conflict with simultaneous legacy declarations.
"""
import asyncio
import copy
import gc
import hashlib
import json
import threading
import time
from contextlib import suppress
from pathlib import Path

import cv2
import numpy as np
import pytest

from orchestrator.brain.registry import CURRENT_RUN
from orchestrator.capture import RUN, captures
from orchestrator.commands import COMMANDS
from orchestrator.frames import Frame
from orchestrator.inspection import PictureInput, image_context
from orchestrator.marks import fov_crop
from tests.test_resource_inspection import (
    CURRENT_CAMERA_UNSUPPLIED,
    CURRENT_PARTIAL_ANSWER,
    actual_scheduler_fixture,
    assert_actual_scheduler_admission,
    assert_supplied_answer_facts,
)
from tests.test_screen_workspace import jpeg
from tests.test_workspace_request_observation import OTHER
from tests.waiting import HANG_S, until_async


MIXED_ANSWER = 'I compared the supplied dated camera crop with the exact retained picture.'
CAMERA_ANSWER = 'I inspected the supplied dated camera crop only.'
RETAINED_ANSWER = 'I inspected only the exact retained picture.'
REJECTED = 'SCRIPTED UNSUPPORTED MIXED OBSERVATION.'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def require_camera_capability(traced):
    """Named feature RED, before dispatch; do not import a not-yet-existing helper."""
    registry = traced.rig.app.brain.registry
    tool = registry.tools.get('inspect_camera')
    assert tool is not None, 'MISSING_FEATURE: actual installed Registry query inspect_camera must acquire original held camera pixels without an answer-model send'
    assert tool.kind == 'query' and callable(tool.handler)
    rows = traced.rig.app.capabilities_wire()['capabilities']
    offered = [row for row in rows if row.get('actor') == 'agent'
               and (row.get('how') or {}).get('tool') == 'inspect_camera']
    assert len(offered) == 1 and offered[0]['available'] is True, 'MISSING_FEATURE: installed camera acquisition must be supplied as an available model-selectable capability'
    assert 'inspect_camera' in registry.prompt_section()
    assert not traced.sends, 'BEHAVIOR: capability preflight occurs before any provider dispatch'


async def intake_camera(rig, *, frame_id=7, value=235, boot='synthetic-original-camera', seq=71):
    """External synthetic input at the unchanged camera fixture's real intake seam."""
    data = spatial_camera_jpeg(value=value)
    # Real FovTracker requires four agreeing detections. Supply actual frames,
    # never a fabricated FOV/FrameInfo or a replacement acquisition/encoder.
    frame_ids = range(frame_id - 3, frame_id + 1) if rig.app.hub.latest_info is None else [frame_id]
    for incoming_id in frame_ids:
        now = time.monotonic()
        frame = Frame(incoming_id, data, now, time.time(),
                      seq=seq - (frame_id - incoming_id), boot=boot)
        rig.app.hub.source.ring[frame.id] = frame
        rig.app.hub.source.last_frame_at = now
        await rig.app.hub._on_frame(frame)
    capture = captures(rig.app.hub).get(frame.id)
    assert capture is not None and capture.jpeg == frame.jpeg
    assert capture.fov is not None, 'FIXTURE: unchanged real intake must detect the synthetic eyepiece disc'
    return capture


def spatial_camera_jpeg(*, value):
    """Synthetic external pixels; a large off-centre eyepiece with unequal landmarks."""
    width, height = 1600, 1200
    pixels = np.empty((height, width, 3), np.uint8)
    pixels[:, :, 0] = 80 + np.arange(width, dtype=np.int32)[None, :] * 90 // width
    pixels[:, :, 1] = 90 + np.arange(height, dtype=np.int32)[:, None] * 90 // height
    pixels[:, :, 2] = 80 + value * 2 // 5 + np.arange(width, dtype=np.int32)[None, :] * 40 // width
    ys, xs = np.ogrid[:height, :width]
    outside = (xs - 900) ** 2 + (ys - 610) ** 2 > 550 ** 2
    pixels[outside] = [8, 12, 16]
    cv2.rectangle(pixels, (530, 300), (700, 470), (80, 220, 110), -1)
    cv2.rectangle(pixels, (1060, 440), (1270, 530), (220, 110, 190), -1)
    cv2.rectangle(pixels, (800, 820), (900, 1030), (140, 150, 245), -1)
    ok, encoded = cv2.imencode('.jpg', pixels, [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok, 'FIXTURE: synthetic external camera JPEG must encode'
    return encoded.tobytes()


async def send(rig, *, text='Compare these selected inputs.', frame_id=7, screen=None):
    return await COMMANDS['ask'](rig.app, rig.client,
        {'type': 'ask', 'text': text, 'frameId': frame_id, 'screen': screen or rig.message()})


def retained_action(rig, key='source-a', *, inspect=True):
    ref = rig.sources[key]['identity']['resource']
    return {'tool': 'resources', 'args': {'id': ref['id'], 'inspect': inspect}}


def camera_action():
    return {'tool': 'inspect_camera', 'args': {}}


def declaration(*entries):
    return {'observation_basis': [{'scope': scope, 'input_refs': list(refs)} for scope, refs in entries]}


def fast_reply(actions, *, needs_camera=False):
    return {'intent': 'request', 'needs_look': needs_camera, 'answer': 'I am acquiring the selected inputs.',
            'actions': list(actions), 'observation_scope': 'none', 'input_refs': []}


def acquired(traced):
    return [picture for run, _ctx, _ticket in traced.started if run.ok is True for picture in run.images]


def basis_for(pictures):
    return declaration(*[(picture.facts['channel'], [number])
                         for number, picture in enumerate(pictures, 1)])


def assert_original_request(traced, result, expected):
    ticket, budget = assert_actual_scheduler_admission(traced, result)
    assert not traced.looks, 'BEHAVIOR: selected camera acquisition is local, never an Agent.look/provider pass'
    socket, request_id, pin = traced.rig.pins[0]
    assert socket is traced.rig.client and request_id == budget.request_id
    assert pin.observation.wire() == expected
    for run, ctx, owner in traced.started:
        assert owner is ticket and ctx.client is socket and ctx.request_snapshot is pin
        assert ctx.request_id == request_id and ctx.admission is budget
        assert ctx.turn_id == result['turnId'] and ctx.channel == 'chat'
        assert run.occurrence.request_id == request_id
    return ticket, budget, pin


def assert_completed_interpretations(traced, budget, *, phases=('fast', 'query', 'final')):
    """A semantic outcome is not a fallback caused by a caught provider assertion."""
    assert [sent.phase for sent in traced.sends] == list(phases)
    assert [attempt.phase for attempt in budget.attempts] == list(phases)
    assert budget.used == len(phases)
    assert [attempt.status for attempt in budget.attempts] == ['done'] * len(phases), (
        'BEHAVIOR: semantic refusal/selection requires successful entered query and final providers; '
        'a scripted-provider assertion failure is not observed claim validation')


def assert_physical_inputs(sent, pictures):
    assert len(sent.images) == len(pictures) <= 2
    assert sent.images == tuple(picture.jpeg for picture in pictures)
    assert image_context(pictures) in sent.prompt
    for number, picture in enumerate(pictures, 1):
        assert isinstance(picture, PictureInput)
        facts = picture.facts
        assert json.dumps({'image': number, **facts}, default=str) in sent.prompt
        assert digest(sent.images[number - 1]) == facts['inputSha256']
        pixels = cv2.imdecode(np.frombuffer(sent.images[number - 1], np.uint8), cv2.IMREAD_COLOR)
        assert pixels is not None and list(pixels.shape[1::-1]) == facts['inputSize']


def assert_camera_picture(picture, original):
    facts = picture.facts
    assert facts['channel'] == 'specimen-camera'
    assert facts['sha256'] == digest(original.jpeg)
    assert facts['inputSha256'] == digest(picture.jpeg)
    assert facts['originalSize'] == [original.width, original.height]
    assert max(facts['inputSize']) <= 1024
    crop = fov_crop(original.fov, original.width, original.height)
    assert facts['crop'] == [crop.x, crop.y, crop.width, crop.height]
    expected_size = [round(crop.width * min(1, 1024 / max(crop.width, crop.height))),
                     round(crop.height * min(1, 1024 / max(crop.width, crop.height)))]
    assert facts['inputSize'] == expected_size
    shown = cv2.imdecode(np.frombuffer(picture.jpeg, np.uint8), cv2.IMREAD_COLOR)
    raw = cv2.imdecode(np.frombuffer(original.jpeg, np.uint8), cv2.IMREAD_COLOR)
    region = raw[crop.y:crop.y + crop.height, crop.x:crop.x + crop.width]
    assert abs(float(shown.mean()) - float(region.mean())) < 2, 'BEHAVIOR: the actual supplied crop must contain the camera pixels, not a relabelled retained image'
    # The oracle starts from actual held bytes and measured FOV, independently
    # slicing/resampling without the acquisition producer or its encode_crop.
    assert original.fov is not None and crop.x > 0 and crop.y > 0
    assert crop.width < original.width and crop.height < original.height
    assert max(crop.width, crop.height) > 1024 and max(expected_size) == 1024
    expected = cv2.resize(region, tuple(expected_size), interpolation=cv2.INTER_AREA)
    assert shown.shape == expected.shape
    error = np.abs(shown.astype(np.int16) - expected.astype(np.int16))
    assert float(error.mean()) < 3, 'BEHAVIOR: actual supplied pixels must match the original measured FOV crop/resize within JPEG tolerance'
    assert float(np.percentile(error, 95)) < 8, 'BEHAVIOR: spatial agreement must hold across the image, not just its global mean'
    # Prove this fixture distinguishes full-frame and wrong-region resizing,
    # even if a faulty producer copies otherwise correct crop/hash metadata.
    full_frame = cv2.resize(raw, tuple(expected_size), interpolation=cv2.INTER_AREA)
    shifted = raw[crop.y:crop.y + crop.height, crop.x - 160:crop.x - 160 + crop.width]
    assert crop.x >= 160 and shifted.shape == region.shape
    wrong_region = cv2.resize(shifted, tuple(expected_size), interpolation=cv2.INTER_AREA)
    for alternative in (full_frame, wrong_region):
        assert float(np.abs(expected.astype(np.int16) - alternative.astype(np.int16)).mean()) > 12, (
            'FIXTURE: spatial camera evidence must distinguish the eyepiece crop from incorrect regions')
    assert isinstance(facts['transform'], str) and facts['transform']
    source = facts['source']
    assert {key: source[key] for key in ('run', 'boot', 'seq', 'frame', 'epoch', 'at')} == {
        'run': RUN, 'boot': original.boot, 'seq': original.seq, 'frame': original.id,
        'epoch': original.epoch, 'at': original.wall_at}
    assert facts['timing'] == 'request-held-frame', 'BEHAVIOR: a dated held frame is not exact client Send time or continuous live viewing'


def assert_retained_picture(picture, rig, key='source-a'):
    ref = rig.sources[key]['identity']['resource']
    facts = picture.facts
    assert facts['channel'] == 'retained-source', 'MISSING_FEATURE: retained pixels require positive retained-channel identity'
    assert {key: facts[key] for key in ('id', 'day', 'version', 'picture')} == ref
    index = rig.app.hub.resources
    entry = index.get(ref['id'], ref['day'])
    assert facts['source'] == entry.picture_source(ref['picture'])
    assert facts['sha256'] == rig.sources[key]['digest']
    assert max(facts['inputSize']) <= 768


@pytest.mark.parametrize('camera_first', [False, True], ids=['retained-then-camera', 'camera-then-retained'])
def test_actual_selected_camera_and_retained_share_query_final_calls(tmp_path, monkeypatch, camera_first):
    actions, traced_box = [], {}

    def response(sent):
        if sent.phase == 'fast':
            assert not sent.images and 'inspect_camera' in sent.prompt
            return fast_reply(actions, needs_camera=True)
        pictures = acquired(traced_box['traced'])
        assert len(pictures) == 2 and sent.phase in ('query', 'final')
        assert_physical_inputs(sent, pictures)
        assert CURRENT_CAMERA_UNSUPPLIED not in sent.prompt, 'MISSING_FEATURE: successful actual camera attachment must replace the stale unsupplied-input fact'
        return {'answer': MIXED_ANSWER, 'actions': [], **basis_for(pictures)}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    actions.extend([camera_action(), retained_action(rig)] if camera_first else [retained_action(rig), camera_action()])
    try:
        require_camera_capability(traced)

        async def scenario():
            original = await intake_camera(rig)
            await rig.ingest()
            expected = rig.observed().wire()
            result = await send(rig)
            ticket, budget, _pin = assert_original_request(traced, result, expected)
            assert_completed_interpretations(traced, budget, phases=('fast', 'query'))
            assert [run.tool for run, _ctx, _owner in traced.started] == [action['tool'] for action in actions]
            assert all(run.ok is True for run, _ctx, _owner in traced.started)
            pictures = acquired(traced)
            assert_supplied_answer_facts(traced.sends[1], [run.wire() for run, _ctx, _owner in traced.started], pictures)
            assert traced.sends[1].reply_data == {'answer': MIXED_ANSWER, 'actions': [], **basis_for(pictures)}
            assert_physical_inputs(traced.sends[1], pictures)
            camera = next(p for p in pictures if p.facts['channel'] == 'specimen-camera')
            retained = next(p for p in pictures if p.facts['channel'] == 'retained-source')
            assert camera.jpeg != retained.jpeg and camera.facts['sha256'] != retained.facts['sha256']
            assert_camera_picture(camera, original)
            assert_retained_picture(retained, rig)
            slots = traced.started[0][1].inspection_slots
            assert len(slots) == 2 and all(ctx.inspection_slots is slots for _run, ctx, _owner in traced.started)
            assert len(result['actions']) == 2 and all(row['ok'] is True for row in result['actions'])
            assert result['answer'] == MIXED_ANSWER and CURRENT_CAMERA_UNSUPPLIED not in result['answer']
            assert ticket.closed
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('selected', ['retained', 'camera', 'metadata'])
def test_actual_model_selection_does_not_force_other_channels(tmp_path, monkeypatch, selected):
    actions, traced_box = [], {}

    def response(sent):
        if sent.phase == 'fast':
            return fast_reply(actions, needs_camera=selected == 'retained')
        pictures = acquired(traced_box['traced'])
        if selected == 'metadata':
            assert not sent.images and not pictures
            return {'answer': 'I read the saved metadata only.', 'actions': [],
                    'observation_scope': 'structural-workspace', 'input_refs': []}
        if selected == 'retained':
            assert len(sent.images) == 1 and CURRENT_CAMERA_UNSUPPLIED in sent.prompt
            return {'answer': CURRENT_PARTIAL_ANSWER, 'actions': [],
                    'observation_scope': 'retained-source', 'input_refs': [1]}
        assert_physical_inputs(sent, pictures)
        return {'answer': CAMERA_ANSWER, 'actions': [], **basis_for(pictures)}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    actions.append(camera_action() if selected == 'camera' else retained_action(rig, inspect=selected == 'retained'))
    try:
        if selected == 'camera':
            require_camera_capability(traced)

        async def scenario():
            original = await intake_camera(rig)
            await rig.ingest()
            expected = rig.observed().wire()
            # Identical text across choices: a provider-selected action, not words, selects inputs.
            result = await send(rig, text='Read the selected evidence and explain it.')
            _ticket, _budget, _pin = assert_original_request(traced, result, expected)
            assert_completed_interpretations(traced, _budget, phases=('fast', 'query'))
            assert len(traced.started) == 1 and traced.started[0][0].ok is True
            pictures = acquired(traced)
            assert_supplied_answer_facts(traced.sends[1], [traced.started[0][0].wire()], pictures,
                                        camera_required=selected == 'retained')
            assert traced.sends[1].reply_data['answer'] == result['answer']
            if selected == 'metadata':
                assert not pictures and not traced.started[0][1].inspection_slots
                assert result['answer'] == 'I read the saved metadata only.'
                assert traced.sends[1].reply_data == {'answer': result['answer'], 'actions': [],
                    'observation_scope': 'structural-workspace', 'input_refs': []}
            elif selected == 'retained':
                assert len(pictures) == 1 and pictures[0].facts.get('channel') != 'specimen-camera'
                assert result['answer'] == CURRENT_PARTIAL_ANSWER
                assert traced.sends[1].reply_data == {'answer': CURRENT_PARTIAL_ANSWER, 'actions': [],
                    'observation_scope': 'retained-source', 'input_refs': [1]}
            else:
                assert len(pictures) == 1 and result['answer'] == CAMERA_ANSWER
                assert_camera_picture(pictures[0], original)
                assert not any(run.tool == 'resources' for run, _ctx, _owner in traced.started)
                assert traced.sends[1].reply_data == {'answer': CAMERA_ANSWER, 'actions': [], **basis_for(pictures)}
            assert not any(sent.phase == 'look' for sent in traced.sends)
        asyncio.run(scenario())
    finally:
        rig.close()


def test_actual_query_selects_camera_for_reserved_final_without_fourth_send(tmp_path, monkeypatch):
    actions, traced_box = [], {}

    def response(sent):
        if sent.phase == 'fast':
            return fast_reply(actions, needs_camera=True)
        pictures = acquired(traced_box['traced'])
        if sent.phase == 'query':
            assert len(pictures) == len(sent.images) == 1
            assert CURRENT_CAMERA_UNSUPPLIED in sent.prompt
            return {'answer': 'I am acquiring the missing dated camera input.', 'actions': [camera_action()],
                    'observation_scope': 'retained-source', 'input_refs': [1]}
        assert sent.phase == 'final' and len(pictures) == 2
        assert_physical_inputs(sent, pictures)
        assert CURRENT_CAMERA_UNSUPPLIED not in sent.prompt, 'MISSING_FEATURE: final receives acquired camera pixels, not the earlier retained-only limitation'
        return {'answer': MIXED_ANSWER, 'actions': [], **basis_for(pictures)}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    actions.append(retained_action(rig))
    try:
        require_camera_capability(traced)

        async def scenario():
            original = await intake_camera(rig)
            await rig.ingest()
            expected = rig.observed().wire()
            result = await send(rig)
            _ticket, budget, _pin = assert_original_request(traced, result, expected)
            assert [sent.phase for sent in traced.sends] == ['fast', 'query', 'final']
            assert budget.used == 3 and result['answer'] == MIXED_ANSWER
            assert [run.tool for run, _ctx, _owner in traced.started] == ['resources', 'inspect_camera']
            assert all(run.ok is True for run, _ctx, _owner in traced.started)
            assert_camera_picture(acquired(traced)[1], original)
            assert_retained_picture(acquired(traced)[0], rig)
            assert len(traced.started[0][1].inspection_slots) == 2
        asyncio.run(scenario())
    finally:
        rig.close()


@pytest.mark.parametrize('bad_basis', [
    'swapped', 'boolean', 'out-of-range', 'camera-as-retained', 'conflicting-legacy',
    'too-many-entries', 'unknown-entry-field', 'unknown-scope',
])
def test_actual_final_refuses_invalid_mixed_basis(tmp_path, monkeypatch, bad_basis):
    actions, traced_box = [], {}

    def response(sent):
        if sent.phase == 'fast':
            return fast_reply(actions, needs_camera=True)
        pictures = acquired(traced_box['traced'])
        assert_physical_inputs(sent, pictures)
        valid = declaration(('retained-source', [1]), ('specimen-camera', [2]))
        if sent.phase == 'query':
            # The second actual input is still missing; acquire the original
            # held camera before interpreting the unchanged malformed final.
            return {'answer': 'I am acquiring the missing dated camera input.', 'actions': [camera_action()],
                    **declaration(('retained-source', [1]))}
        assert sent.phase == 'final'
        if bad_basis == 'swapped':
            bad = declaration(('specimen-camera', [1]), ('retained-source', [2]))
        elif bad_basis == 'boolean':
            bad = declaration(('retained-source', [True]), ('specimen-camera', [2]))
        elif bad_basis == 'out-of-range':
            bad = declaration(('retained-source', [1]), ('specimen-camera', [3]))
        elif bad_basis == 'camera-as-retained':
            bad = declaration(('retained-source', [1, 2]))
        elif bad_basis == 'conflicting-legacy':
            bad = {**valid, 'observation_scope': 'workspace-pixels', 'input_refs': [1, 2]}
        elif bad_basis == 'too-many-entries':
            bad = declaration(('retained-source', [1]), ('specimen-camera', [2]), ('none', []))
        elif bad_basis == 'unknown-entry-field':
            bad = copy.deepcopy(valid)
            bad['observation_basis'][0]['verified'] = True
        else:
            bad = declaration(('continuous-workspace', [1, 2]))
        return {'answer': REJECTED, 'actions': [], **bad}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    actions.append(retained_action(rig))
    try:
        require_camera_capability(traced)

        async def scenario():
            original = await intake_camera(rig)
            await rig.ingest()
            expected = rig.observed().wire()
            result = await send(rig)
            _ticket, budget, _pin = assert_original_request(traced, result, expected)
            assert [sent.phase for sent in traced.sends] == ['fast', 'query', 'final']
            assert budget.used == 3 and all(run.ok is True for run, _ctx, _owner in traced.started)
            assert_completed_interpretations(traced, budget)
            # Verify the real inputs outside the callback as well: callback
            # assertions are provider errors caught by the production loop.
            pictures = acquired(traced)
            assert len(pictures) == 2
            assert_retained_picture(pictures[0], rig)
            assert pictures[1].facts.get('channel') == 'specimen-camera'
            assert_camera_picture(pictures[1], original)
            query, final = traced.sends[1:]
            assert_physical_inputs(query, pictures[:1])
            assert_physical_inputs(final, pictures)
            assert query.reply_data == {'answer': 'I am acquiring the missing dated camera input.',
                'actions': [camera_action()], **declaration(('retained-source', [1]))}
            valid = declaration(('retained-source', [1]), ('specimen-camera', [2]))
            expected_bad = {
                'swapped': declaration(('specimen-camera', [1]), ('retained-source', [2])),
                'boolean': declaration(('retained-source', [True]), ('specimen-camera', [2])),
                'out-of-range': declaration(('retained-source', [1]), ('specimen-camera', [3])),
                'camera-as-retained': declaration(('retained-source', [1, 2])),
                'conflicting-legacy': {**valid, 'observation_scope': 'workspace-pixels', 'input_refs': [1, 2]},
                'too-many-entries': declaration(('retained-source', [1]), ('specimen-camera', [2]), ('none', [])),
                'unknown-entry-field': {'observation_basis': [
                    {'scope': 'retained-source', 'input_refs': [1], 'verified': True},
                    {'scope': 'specimen-camera', 'input_refs': [2]}]},
                'unknown-scope': declaration(('continuous-workspace', [1, 2])),
            }[bad_basis]
            # JSON distinguishes true from 1; Python dict equality alone does not.
            assert json.dumps(final.reply_data, sort_keys=True) == json.dumps(
                {'answer': REJECTED, 'actions': [], **expected_bad}, sort_keys=True)
            assert CURRENT_CAMERA_UNSUPPLIED in query.prompt
            assert CURRENT_CAMERA_UNSUPPLIED not in final.prompt
            assert REJECTED not in result['answer'], 'MISSING_FEATURE: invalid per-call typed basis must refuse unchecked final prose'
            assert result['answer'] == '' and result['answerStatus'] == 'not-published'
            assert CURRENT_CAMERA_UNSUPPLIED not in result['answer']  # camera did actually arrive
            receipts = [run.wire() for run, _ctx, _owner in traced.started]
            assert len(receipts) == 2 and result['actions'] == receipts
            assert [row['tool'] for row in receipts] == ['resources', 'inspect_camera']
            assert len({row['actionId'] for row in receipts}) == 2
            assert all(row['requestId'] == budget.request_id for row in receipts)
            assert_supplied_answer_facts(query, receipts[:1], pictures[:1], camera_required=True)
            assert_supplied_answer_facts(final, receipts, pictures)
            history = rig.app.dialogue.history()['lines']
            assert not any(row.get('role') == 'assistant' and row.get('turnId') == result['turnId'] for row in history)
            activity = [row for row in history if row.get('type') == 'chat-action'
                        and row.get('requestId') == budget.request_id]
            published = []
            while not rig.client.control.empty():
                row = json.loads(rig.client.control.get_nowait())
                if row.get('type') == 'chat-action' and row.get('requestId') == budget.request_id:
                    published.append(row)
            assert len(activity) == len(published) == len(receipts)
            for rows in (activity, published):
                for row, receipt, (_run, ctx, _owner) in zip(rows, receipts, traced.started):
                    assert {key: row.get(key) for key in receipt} == receipt
                    assert row['turnId'] == result['turnId'] and row['thread'] == ctx.asked.thread
                    assert row.get('standalone') is True and not row.get('answer')
        asyncio.run(scenario())
    finally:
        rig.close()


def test_actual_historical_camera_picture_is_still_retained_not_current(tmp_path, monkeypatch):
    actions, traced_box = [], {}

    def response(sent):
        if sent.phase == 'fast':
            return fast_reply(actions)
        pictures = acquired(traced_box['traced'])
        assert_physical_inputs(sent, pictures)
        assert len(pictures) == 1 and pictures[0].facts.get('channel') == 'retained-source', 'MISSING_FEATURE: actual retained inspection must positively declare its channel even for an earlier camera origin'
        if sent.phase == 'query':
            return {'answer': '', 'actions': [], **declaration(('retained-source', [1]))}
        return {'answer': REJECTED, 'actions': [], **declaration(('specimen-camera', [1]))}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    historical = rig.app.hub.resources.results.add('compare', {}, 'Synthetic earlier camera source',
        picture_bytes=[jpeg(value=110)], source={'run': 'synthetic-earlier-run', 'frame': 7,
            'boot': 'synthetic-earlier-camera', 'seq': 1, 'epoch': 0, 'at': 1.0})
    actions.append({'tool': 'resources', 'args': {'id': historical['id'], 'inspect': True}})
    try:
        async def scenario():
            await intake_camera(rig)
            await rig.ingest()
            expected = rig.observed().wire()
            result = await send(rig)
            _ticket, budget, _pin = assert_original_request(traced, result, expected)
            assert budget.used == 3 and len(traced.started) == 1 and traced.started[0][0].ok is True
            pictures = acquired(traced)
            assert len(pictures) == 1 and pictures[0].facts.get('channel') == 'retained-source', 'MISSING_FEATURE: actual retained inspection must positively declare its channel even for an earlier camera origin'
            assert_completed_interpretations(traced, budget)
            assert traced.sends[1].reply_data == {'answer': '', 'actions': [], **declaration(('retained-source', [1]))}
            assert traced.sends[2].reply_data == {'answer': REJECTED, 'actions': [], **declaration(('specimen-camera', [1]))}
            for sent in traced.sends[1:]:
                assert_physical_inputs(sent, pictures)
                assert_supplied_answer_facts(sent, [traced.started[0][0].wire()], pictures)
            assert acquired(traced)[0].facts['source']['boot'] == 'synthetic-earlier-camera'
            assert acquired(traced)[0].facts['source']['run'] == 'synthetic-earlier-run'
            assert traced.started[0][0].tool == 'resources'
            assert REJECTED not in result['answer'], 'MISSING_FEATURE: historical camera origin cannot authorize current-camera scope'
            assert result['answer'] == '' and result['answerStatus'] == 'not-published'
            assert not any(row.get('role') == 'assistant' and row.get('turnId') == result['turnId']
                           for row in rig.app.dialogue.history()['lines'])
        asyncio.run(scenario())
    finally:
        rig.close()


def test_actual_two_retained_inputs_refuse_selected_camera_without_substitution(tmp_path, monkeypatch):
    actions, traced_box = [], {}

    def response(sent):
        if sent.phase == 'fast':
            return fast_reply(actions, needs_camera=True)
        pictures = acquired(traced_box['traced'])
        assert len(pictures) == 2
        assert_physical_inputs(sent, pictures)
        assert CURRENT_CAMERA_UNSUPPLIED in sent.prompt
        return {'answer': CURRENT_PARTIAL_ANSWER, 'actions': [],
                'observation_scope': 'retained-source', 'input_refs': [1, 2]}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    actions.extend([retained_action(rig), retained_action(rig, 'source-b'), camera_action()])
    try:
        require_camera_capability(traced)

        async def scenario():
            await intake_camera(rig)
            await rig.ingest()
            expected = rig.observed().wire()
            result = await send(rig)
            _ticket, budget, _pin = assert_original_request(traced, result, expected)
            assert [sent.phase for sent in traced.sends] == ['fast', 'query'] and budget.used == 2
            assert_completed_interpretations(traced, budget, phases=('fast', 'query'))
            assert [run.ok for run, _ctx, _owner in traced.started] == [True, True, False]
            camera_run = traced.started[-1][0]
            assert camera_run.tool == 'inspect_camera' and not camera_run.images and 'two' in camera_run.summary
            pictures = acquired(traced)
            assert_retained_picture(pictures[0], rig)
            assert_retained_picture(pictures[1], rig, 'source-b')
            slots = traced.started[0][1].inspection_slots
            assert len(slots) == 2 and all(ctx.inspection_slots is slots for _run, ctx, _owner in traced.started)
            assert result['answer'] == CURRENT_PARTIAL_ANSWER
        asyncio.run(scenario())
    finally:
        rig.close()


def test_actual_held_camera_survives_feed_layout_changes_and_workspace_occlusion(tmp_path, monkeypatch):
    actions, traced_box = [], {}
    opened, release = threading.Event(), threading.Event()
    actual_open = Path.open
    held_path = None

    def held_read(path, *args, **kwargs):
        mode = kwargs.get('mode', args[0] if args else 'r')
        if path == held_path and mode in ('r', 'rb'):
            opened.set()
            if not release.wait(HANG_S):
                raise TimeoutError('external retained read barrier was not released')
        return actual_open(path, *args, **kwargs)

    def response(sent):
        if sent.phase == 'fast':
            return fast_reply(actions, needs_camera=True)
        pictures = acquired(traced_box['traced'])
        assert_physical_inputs(sent, pictures)
        return {'answer': MIXED_ANSWER, 'actions': [], **basis_for(pictures)}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    ref = rig.sources['source-a']['identity']['resource']
    index = rig.app.hub.resources
    found = index.picture(ref['id'], ref['picture'], ref['day'])
    assert found and found[0]
    data, _media = found
    assert digest(data) == rig.sources['source-a']['digest']
    values = {'heldRead': 'synthetic-original-source-metadata'}
    record = index.results.add('compare', {}, 'Synthetic retained source with full metadata',
                               values=values, picture_bytes=[data])
    entry = index.get(record['id'], record['day'])
    assert entry is not None and index.results.get(entry.id, entry.day)['full'] is True
    rig.sources['source-a']['identity']['resource'] = {
        'id': entry.id, 'day': entry.day, 'version': entry.version, 'picture': 0}
    ref = rig.sources['source-a']['identity']['resource']
    # Nonempty actual values make Index.read take Results.values' real metadata
    # read branch. A picture-only result legitimately reads its journal preview;
    # its JPEG may also remain in the real pending-write cache.
    held_path = rig.app.hub.resources.results.root / ref['day'] / f"{ref['id']}.json"
    original_metadata = held_path.read_bytes()
    assert json.loads(original_metadata)['values'] == values
    actions.extend([retained_action(rig), camera_action()])
    try:
        require_camera_capability(traced)
        monkeypatch.setattr(Path, 'open', held_read)

        async def scenario():
            original = await intake_camera(rig)
            raw = rig.message()
            # The renderer reports the workspace hidden/occluded while the raw
            # camera is fresh. Presentation visibility grants no raw-pixel veto.
            raw['viewport']['visible'] = False
            raw['items'][0]['visible'] = False
            raw['items'][0]['visibility'] = 'occluded'
            await rig.ingest(raw)
            expected = rig.observed().wire()
            pending = asyncio.create_task(send(rig, screen=copy.deepcopy(raw)))
            try:
                assert await asyncio.to_thread(opened.wait, HANG_S)
                ticket = traced.sends[0].ticket
                assert ticket.held(original.id) is original
                await intake_camera(rig, frame_id=8, value=25, boot='synthetic-later-camera', seq=1)
                peer = rig.message(seq=80, ref=OTHER)
                peer['items'][0]['rect'][0] = 700
                assert (await rig.ingest(peer, rig.other))['ok'] is True
                newer = rig.message(seq=11)
                newer['items'][0]['rect'][0] = 800
                newer['layoutRevision'] += 1
                assert (await rig.ingest(newer))['ok'] is True
                release.set()  # no inline_ms alteration or fabricated completion
                result = await asyncio.wait_for(pending, HANG_S)
                _ticket, budget, pin = assert_original_request(traced, result, expected)
                assert pin.observation.wire() == expected
                assert_completed_interpretations(traced, budget, phases=('fast', 'query'))
                pictures = acquired(traced)
                assert_supplied_answer_facts(traced.sends[1], [run.wire() for run, _ctx, _owner in traced.started], pictures)
                assert_physical_inputs(traced.sends[1], pictures)
                assert traced.sends[1].reply_data == {'answer': MIXED_ANSWER, 'actions': [], **basis_for(pictures)}
                camera = next(p for p in acquired(traced) if p.facts['channel'] == 'specimen-camera')
                assert_camera_picture(camera, original)
                assert camera.facts['source']['boot'] != rig.app.hub.latest_info.boot
                assert_retained_picture(acquired(traced)[0], rig)
                assert result['answer'] == MIXED_ANSWER
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
        asyncio.run(scenario())
    finally:
        release.set()
        rig.close()


@pytest.mark.parametrize('blocked', ['missing-exact-frame', 'memory', 'failed-final'])
def test_actual_acquisition_or_final_failure_preserves_receipts_and_limitations(tmp_path, monkeypatch, blocked):
    actions, traced_box, allocations = [], {}, []
    intended_final_failures = []
    actual_resize, actual_encode = cv2.resize, cv2.imencode

    def traced_allocator(name, actual, *args, **kwargs):
        run = CURRENT_RUN.get()
        if run is not None and run.tool == 'inspect_camera':
            allocations.append(name)
        return actual(*args, **kwargs)

    def response(sent):
        if sent.phase == 'fast':
            if blocked == 'memory':
                memory = captures(traced_box['traced'].rig.app.hub).memory
                memory.allowances['analysis'] = memory.reserved['analysis']  # actual external capacity constraint
            return fast_reply(actions, needs_camera=True)
        pictures = acquired(traced_box['traced'])
        if blocked == 'failed-final':
            assert_physical_inputs(sent, pictures)
            if sent.phase == 'final':
                assert len(pictures) == 2
                intended_final_failures.append(sent)  # reached after actual input checks
                raise RuntimeError('Explicitly canned entered final-provider failure.')
            return {'answer': 'I am acquiring the missing dated camera input.', 'actions': [camera_action()],
                    **declaration(('retained-source', [1]))}
        assert not any(p.facts.get('channel') == 'specimen-camera' for p in pictures)
        failures = [run.summary for run, _ctx, _owner in traced_box['traced'].started if run.ok is False]
        assert failures and all(reason in sent.prompt for reason in failures), 'BEHAVIOR: the actual interpretation prompt must retain the concrete failed acquisition receipt'
        assert CURRENT_CAMERA_UNSUPPLIED in sent.prompt, 'MISSING_FEATURE: each interpretation must retain the concrete missing camera obligation after actual acquisition failure'
        supported = CURRENT_PARTIAL_ANSWER if pictures else CURRENT_CAMERA_UNSUPPLIED
        return {'answer': ' '.join([supported, *failures]), 'actions': [],
                'observation_scope': 'retained-source' if pictures else 'none',
                'input_refs': [1] if pictures else []}

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    traced_box['traced'] = traced
    rig = traced.rig
    actions.extend([camera_action()] if blocked == 'memory' else
                   [retained_action(rig)] if blocked == 'failed-final' else [retained_action(rig), camera_action()])
    try:
        require_camera_capability(traced)
        monkeypatch.setattr(cv2, 'resize', lambda *a, **k: traced_allocator('resize', actual_resize, *a, **k))
        monkeypatch.setattr(cv2, 'imencode', lambda *a, **k: traced_allocator('encode', actual_encode, *a, **k))

        async def scenario():
            original = await intake_camera(rig)
            await rig.ingest()
            expected = rig.observed().wire()
            # A real explicit frame request that does not exist must not silently
            # adopt frame7 through Agent.snapshot's current best-frame fallback.
            result = await send(rig, frame_id=999 if blocked == 'missing-exact-frame' else original.id)
            _ticket, budget, _pin = assert_original_request(traced, result, expected)
            camera_run = next(run for run, _ctx, _owner in traced.started if run.tool == 'inspect_camera')
            if blocked == 'failed-final':
                assert all(run.ok is True for run, _ctx, _owner in traced.started)
                assert [sent.phase for sent in traced.sends] == ['fast', 'query', 'final']
                assert [attempt.status for attempt in budget.attempts] == ['done', 'done', 'failed']
                assert len(intended_final_failures) == 1 and intended_final_failures[0] is traced.sends[-1], (
                    'BEHAVIOR: failed final must reach the intended external failure; '
                    'a caught input assertion is not the requested provider-failure control')
                assert result['answer'] == '' and result['answerStatus'] == 'not-published'
                pictures = acquired(traced)
                assert len(pictures) == 2
                assert_retained_picture(pictures[0], rig)
                assert_camera_picture(pictures[1], original)
                receipts = [run.wire() for run, _ctx, _owner in traced.started]
                assert len(receipts) == 2 and result['actions'] == receipts
                assert [row['tool'] for row in receipts] == ['resources', 'inspect_camera']
                assert len({row['actionId'] for row in receipts}) == 2
                assert all(row['requestId'] == budget.request_id and row['summary'] for row in receipts)
                query, final = traced.sends[1:]
                assert_physical_inputs(query, pictures[:1])
                assert_physical_inputs(final, pictures)
                assert_supplied_answer_facts(query, receipts[:1], pictures[:1], camera_required=True)
                assert_supplied_answer_facts(final, receipts, pictures)
                assert query.reply_data == {'answer': 'I am acquiring the missing dated camera input.',
                    'actions': [camera_action()], **declaration(('retained-source', [1]))}
                assert type(final.provider_error) is RuntimeError
                assert str(final.provider_error) == 'Explicitly canned entered final-provider failure.'
                assert not hasattr(final, 'reply_data')
                assert CURRENT_CAMERA_UNSUPPLIED in query.prompt
                assert CURRENT_CAMERA_UNSUPPLIED not in final.prompt
                history = rig.app.dialogue.history()['lines']
                assert not any(row.get('role') == 'assistant' and row.get('turnId') == result['turnId'] for row in history)
                activity = [row for row in history if row.get('type') == 'chat-action'
                            and row.get('requestId') == budget.request_id]
                published = []
                while not rig.client.control.empty():
                    row = json.loads(rig.client.control.get_nowait())
                    if row.get('type') == 'chat-action' and row.get('requestId') == budget.request_id:
                        published.append(row)
                assert len(activity) == len(published) == len(receipts)
                for rows in (activity, published):
                    for row, receipt, (_run, ctx, _owner) in zip(rows, receipts, traced.started):
                        assert {key: row.get(key) for key in receipt} == receipt
                        assert row['turnId'] == result['turnId'] and row['thread'] == ctx.asked.thread
                        assert row.get('standalone') is True and not row.get('answer')
                assert CURRENT_CAMERA_UNSUPPLIED not in result['answer']
            else:
                assert_completed_interpretations(traced, budget, phases=('fast', 'query'))
                assert camera_run.ok is False and not camera_run.images
                assert not allocations, 'MISSING_FEATURE: unavailable/over-memory camera input must refuse before camera encode allocations'
                assert camera_run.summary and camera_run.summary in result['answer'], 'MISSING_FEATURE: the public answer must retain the concrete acquisition failure receipt'
                supported = CURRENT_PARTIAL_ANSWER if acquired(traced) else CURRENT_CAMERA_UNSUPPLIED
                failures = [run.summary for run, _ctx, _owner in traced.started if run.ok is False]
                assert result['answer'] == ' '.join([supported, *failures]), (
                    'BEHAVIOR: preserve the exact truthful scripted partial/unavailable answer '
                    'and actual failure receipts without duplicating the missing-camera wording')
                if blocked == 'memory':
                    memory = captures(rig.app.hub).memory
                    assert memory.refused['analysis'] > 0
                    assert 'held' in camera_run.summary.lower() or 'memory' in camera_run.summary.lower()
                else:
                    assert_retained_picture(acquired(traced)[0], rig)
                    assert 'frame' in camera_run.summary.lower() or 'view' in camera_run.summary.lower()
            assert budget.used <= 3 and not any(sent.phase == 'look' for sent in traced.sends)
        asyncio.run(scenario())
    finally:
        rig.close()


def test_actual_camera_worker_cancellation_keeps_memory_until_allocator_returns(tmp_path, monkeypatch):
    entered, release, returned = threading.Event(), threading.Event(), threading.Event()
    original_encode = cv2.imencode

    def held_encode(*args, **kwargs):
        run = CURRENT_RUN.get()
        if run is None or run.tool != 'inspect_camera':
            return original_encode(*args, **kwargs)
        entered.set()
        try:
            if not release.wait(HANG_S):
                raise TimeoutError('external encode barrier was not released')
            return original_encode(*args, **kwargs)
        finally:
            returned.set()

    def response(sent):
        assert sent.phase == 'fast', 'BEHAVIOR: cancelled acquisition must not enter a later interpretation provider'
        return fast_reply([camera_action()])

    traced = actual_scheduler_fixture(tmp_path, monkeypatch, response)
    rig = traced.rig
    try:
        require_camera_capability(traced)
        monkeypatch.setattr(cv2, 'imencode', held_encode)

        async def scenario():
            await intake_camera(rig)
            await rig.ingest()
            pending = asyncio.create_task(send(rig))
            try:
                assert await asyncio.to_thread(entered.wait, HANG_S), 'BEHAVIOR: real camera allocator must run on the acquired input'
                run, ctx, ticket = next(row for row in traced.started if row[0].tool == 'inspect_camera')
                memory = captures(rig.app.hub).memory
                assert memory.reserved['analysis'] > 0 and ticket.held(7) is not None
                pending.cancel()
                run.task.cancel()
                with suppress(asyncio.CancelledError):
                    await pending
                with suppress(asyncio.CancelledError):
                    await run.task
                await asyncio.sleep(0)
                gc.collect()
                assert not returned.is_set()
                assert memory.reserved['analysis'] > 0, 'MISSING_FEATURE: cancelled camera worker still owns its pixel buffers and reservation until the real allocator returns'
                assert not run.images and not run.resource
                assert ctx.admission is ticket.admission and ticket.admission.used == 1
                assert ticket.admission.attempts[0].status == 'done'
                release.set()
                assert await asyncio.to_thread(returned.wait, HANG_S)
                # Completion callbacks must release ownership without requiring a
                # new request, server restart, slot reset or interpretation send.

                def released():
                    gc.collect()
                    return not memory.reserved['analysis']

                await until_async(released)
                assert memory.reserved['analysis'] == 0
                assert len(traced.sends) == 1 and not traced.looks and ticket.closed
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
        asyncio.run(scenario())
    finally:
        release.set()
        rig.close()
