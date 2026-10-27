"""Synthetic acting slides/external provider scripts over real Brain/Agent/tool paths.

No model quality or actual voice/browser acceptance is claimed. These controls
keep acting scripts behind actual answer admission, original pixels and receipts.
"""
import asyncio
import copy
import hashlib

import cv2
import numpy as np

from bench.acting.run import holds, load_cases
from bench.acting.world import World
from orchestrator.brain.registry import ACTIVITY_KEYS
from orchestrator.day import DayStore
from orchestrator.resources import Results
from tests.waiting import HANG_S


def told(wires):
    """Receipts as the model is told them: what only Activity says (the models that worked on a step) left out."""
    return [{k: v for k, v in wire.items() if k not in ACTIVITY_KEYS} for wire in wires]


def count_world():
    original = next(case for case in load_cases() if case['id'] == 'chat-how-many-finds-then-counts')
    world = World(copy.deepcopy(original))
    world.case['vision'][0]['reply']['then']['answer'] = 'There are {n} red cells in view.'
    # The original 57 cases are unchanged. This additional tracer makes room for
    # the actual camera query and final interpretation within the same MAX3.
    world.hub.settings.fast = 'off'
    return world


def test_actual_count_outcome_uses_supplied_receipt_without_replaying_the_plan():
    world = count_world()
    completed = []
    world.brain.registry.finished.append(lambda run, ctx: completed.append((run, ctx)))

    async def scenario():
        return await asyncio.wait_for(world.brain.turn('how many red cells are there?', frame_id=1), HANG_S)

    found = asyncio.run(scenario())
    sends = world.fast.sends
    assert [sent.attempt.phase for sent in sends] == ['look', 'query', 'final']
    request = sends[0].ticket
    assert all(sent.ticket is request for sent in sends)
    assert request.admission.used == 3
    assert len({attempt.attempt_id for attempt in request.admission.attempts}) == 3
    assert all(attempt.status == 'done' for attempt in request.admission.attempts)
    assert len(completed) == 1 and completed[0][0].tool == 'count' and completed[0][0].ok is True
    run, ctx = completed[0]
    assert ctx.admission is request.admission and ctx.frame_id == 1
    assert run.values['count']['n'] == 5
    assert len(run.values['count']['objectIds']) == 5
    assert len(world.hub.scene.objects) == 5
    assert found['actions'] == [run.wire()]
    assert found['answer'] == 'There are 5 red cells in view.'
    assert sends[0].facts is None
    assert sends[1].facts['operations'] == sends[2].facts['operations'] == told(found['actions'])
    assert all(sent.facts['omittedOperations'] == sent.facts['omittedFacts'] == 0 for sent in sends[1:])
    assert sends[1].facts['inputs'] == {'legacyCameraCropSupplied': True, 'images': []}
    assert sends[2].facts['inputs'] == {'legacyCameraCropSupplied': False, 'images': []}
    assert len(sends[0].images) == len(sends[1].images) == 1 and sends[0].images == sends[1].images
    assert not sends[2].images
    decoded = cv2.imdecode(np.frombuffer(sends[0].images[0], np.uint8), cv2.IMREAD_COLOR)
    assert decoded is not None and decoded.shape[:2] == (576, 1024)
    assert not any('tool-results' in sent.prompt for sent in sends)
    assert all(sent.reply['actions'] == [] for sent in sends[1:])


def test_deliberately_repeating_external_count_plan_stops_at_actual_max3():
    world = count_world()
    world.case['vision'][0]['reply']['then'] = {
        'answer': 'Counting again deliberately.', 'targets': [],
        'actions': [{'tool': 'count', 'args': {'label': 'red cell'}}],
    }
    completed = []
    world.brain.registry.finished.append(lambda run, ctx: completed.append((run, ctx)))

    async def scenario():
        return await asyncio.wait_for(world.brain.turn('how many red cells are there?', frame_id=1), HANG_S)

    found = asyncio.run(scenario())
    sends = world.fast.sends
    assert [sent.attempt.phase for sent in sends] == ['look', 'query', 'final']
    request = sends[0].ticket
    assert all(sent.ticket is request for sent in sends)
    assert request.admission.used == 3 and request.admission.remaining == 0
    assert len(request.admission.attempts) == 3
    assert all(attempt.status == 'done' for attempt in request.admission.attempts)
    assert len(completed) == 2
    assert all(run.tool == 'count' and run.ok is True and ctx.admission is request.admission for run, ctx in completed)
    assert len({run.occurrence.action_id for run, _ctx in completed}) == 2
    assert len(world.hub.scene.objects) == 5  # the deliberate second count reads, not outlines again
    assert sends[1].facts['operations'] == told([completed[0][0].wire()])
    assert sends[2].facts['operations'] == told(found['actions'])
    assert found['actions'] == [run.wire() for run, _ctx in completed]
    # No actions execute from the final interpretation, even when its canned
    # external reply deliberately repeats the same plan for a third time.
    assert all(sent.reply['actions'] == [{'tool': 'count', 'args': {'label': 'red cell'}}] for sent in sends)


def test_unavailable_final_model_keeps_actual_activity_and_the_answer_nothing_contradicts():
    """The final model unavailable (no reply in time): the activity is the step's own, and since its receipts leave
    nothing to tell beyond the look's answer (the select worked; no problem, choice or limitation), that answer is
    kept, logged unchecked with the reason (T5, live 2026-10-11 16:49:38: a request whose steps ran never ends with
    no words). With something to tell, no prose stays the rule (tests/test_brain.py)."""
    case = {
        'id': 'provider-unavailable-final', 'slide': [{'name': 'stoma', 'box': [400, 300, 460, 350]}],
        'board': {'outlines': ['stoma']}, 'fast': [],
        'vision': [{'when': 'select', 'reply': {
            'answer': 'Selecting the stoma.', 'targets': [],
            'actions': [{'tool': 'select', 'args': {'object': '#stoma'}}],
        }}],
    }
    world = World(case)
    world.hub.settings.fast = 'off'
    completed = []
    world.brain.registry.finished.append(lambda run, ctx: completed.append((run, ctx)))

    async def scenario():
        return await asyncio.wait_for(world.brain.turn('select the stoma', frame_id=1), HANG_S)

    found = asyncio.run(scenario())
    sends = world.fast.sends
    assert [sent.attempt.phase for sent in sends] == ['look', 'final']
    request = sends[0].ticket
    assert sends[1].ticket is request
    assert [attempt.status for attempt in request.admission.attempts] == ['done', 'failed']
    assert request.admission.used == 2 and sends[1].reply is None
    assert len(completed) == 1 and completed[0][0].tool == 'select' and completed[0][0].ok is True
    assert world.hub.scene.selected == next(iter(world.hub.scene.objects))
    assert sends[1].facts['operations'] == told(found['actions'])
    assert found['actions'] == [completed[0][0].wire()]
    assert not sends[1].images and sends[1].facts['inputs'] == {'legacyCameraCropSupplied': False, 'images': []}
    assert found['answer'] == 'Selecting the stoma.' and found['answerStatus'] == 'model'
    [kept] = [r for r in world.agent.logged if r.get('rule') == 'answer-recheck']
    assert kept['kept'] is True and kept['unchecked'] == [] and kept['reason'] == 'timeout'


def test_actual_comparison_retains_original_clean_crops_and_shows_that_composite():
    original = next(case for case in load_cases() if case['id'] == 'live-161230-two-separate-windows')
    world = World(copy.deepcopy(original))
    completed = []
    world.brain.registry.finished.append(lambda run, ctx: completed.append((run, ctx)))

    async def scenario():
        return await asyncio.wait_for(world.brain.turn('you make them two separate windows', frame_id=1), HANG_S)

    found = asyncio.run(scenario())
    assert len(completed) == 1
    run, ctx = completed[0]
    assert run.tool == 'compare' and run.ok is True
    assert run.args == {'a': 'o1', 'b': 'o2'}
    assert found['actions'] == [run.wire()]
    assert len(world.hub.scene.objects) == 2  # the comparison does not manufacture another outline
    sends = world.fast.sends
    assert [send.attempt.phase for send in sends] == ['fast', 'query']
    request = sends[0].ticket
    assert all(send.ticket is request for send in sends)
    assert ctx.admission is request.admission and ctx.frame_id == 1
    assert run.occurrence.request_id == request.admission.request_id
    assert request.admission.used == 2 and request.admission.remaining == 1
    assert len(request.admission.attempts) == 2 and all(a.status == 'done' for a in request.admission.attempts)
    assert sends[1].facts['operations'] == told(found['actions'])
    assert sends[1].reply['actions'] == [] and found['answer'] == sends[-1].reply['answer']
    assert not sends[0].images and not sends[1].images  # no phantom workspace/camera interpretation

    results = world.hub.resources.results
    entry = results.get(run.resource['id'], run.resource['day'])
    assert entry['state'] == 'saved' and entry['tool'] == 'compare'
    source = entry['source']
    assert source['kind'] == 'composite' and source['provenance']['componentsInOrder'] is True
    components = source['components']
    assert len(components) == 2
    assert [c['provenance']['outline'] for c in components] == ['o1', 'o2']
    assert [c['provenance']['frame'] for c in components] == [1, 1]
    assert source['provenance']['producerRun']
    assert all(c['provenance']['run'] == source['provenance']['producerRun'] for c in components)
    assert all(c['provenance']['seq'] is None and c['provenance']['boot'] is None for c in components)
    assert [c['provenance']['originalSize'] for c in components] == [[1280, 720], [1280, 720]]
    assert [c['provenance']['naturalSize'] for c in components] == [[104, 78], [104, 78]]
    assert [c['provenance']['outlines'] for c in components] == [False, False]
    assert [c['identity']['resource']['picture'] for c in components] == [1, 2]
    assert source['provenance']['requestId'] == run.occurrence.request_id
    assert source['provenance']['step'] == 1

    # Known synthetic frame rectangles, not product crop/Source helpers. Encoding
    # these clean pixels must equal the originals actually saved by the producer.
    camera = cv2.imdecode(np.frombuffer(world.hub.source.get(1).jpeg, np.uint8), cv2.IMREAD_COLOR)
    for n, rect in enumerate(((288, 291, 392, 369), (688, 291, 792, 369)), 1):
        x0, y0, x1, y1 = rect
        assert components[n - 1]['provenance']['rect'] == list(rect)
        ok, encoded = cv2.imencode('.jpg', camera[y0:y1, x0:x1], [cv2.IMWRITE_JPEG_QUALITY, 92])
        assert ok
        pixels = results.picture(entry['id'], n, entry['day'])
        assert pixels == encoded.tobytes()
        assert hashlib.sha256(pixels).hexdigest() == components[n - 1]['digest']
    composite = results.picture(entry['id'], 0, entry['day'])
    assert hashlib.sha256(composite).hexdigest() == source['digest']
    assert cv2.imdecode(np.frombuffer(composite, np.uint8), cv2.IMREAD_COLOR).shape[:2] == (360, 968)
    shown = world.magnifier.picture_now()
    assert world.magnifier.open and shown['url'] == '/api/picture/' + hashlib.sha1(composite).hexdigest()[:12]
    assert world.magnifier.pictures[shown['url'].rsplit('/', 1)[-1]] == composite
    reported = next(item for item in world.hub.screen.items() if item['id'] == 'magnifier')
    assert reported['open'] and reported['source'] == shown['url']
    assert any(picture['source'] == shown['url'] for picture in reported['pictures'])
    assert world.board()['magnifier']['comparison'] == {
        'url': shown['url'], 'resource': entry['id'], 'outlineIds': ['o1', 'o2'],
        'labels': ['stoma 1', 'stoma 2'],
    }
    assert 'sharpest recent camera frame' in shown['label']  # retain source qualifiers

    # Public reload proves the original bytes and source identity were journaled,
    # not a synthetic success from the renderer or a transient Results listener.
    reloaded_day = DayStore(root=world.conversation.store.root, day=entry['day'])
    reloaded = Results(results.root, day=reloaded_day)
    assert reloaded.get(entry['id'])['source'] == source
    assert [reloaded.picture(entry['id'], n) for n in range(3)] == [
        results.picture(entry['id'], n) for n in range(3)
    ]
    assert holds(original['expect']['board'][0], world.board())
    # A different actual image with a convenient title must not impersonate the
    # original composite. This is a real Magnifier change/report, not a fake Source.
    world.magnifier.show_picture(world.hub.source.get(1).jpeg, 'stoma 1 and stoma 2')
    assert world.board()['magnifier']['comparison'] is None
    assert not holds(original['expect']['board'][0], world.board())
