"""Retained researcher input through production actors; synthetic pixels and real CPU workers.

Installer is a stand-in. No provider calls, live model quality or scientific accuracy claims.
"""
import asyncio
import time

import cv2
import numpy as np
import pytest

from orchestrator import retained_inputs
from orchestrator.research.hands import Hands
from tests.test_bundle_actors import rig, command, results, assert_retained  # noqa: F401
from tests.test_env_runner import home  # noqa: F401
from tests.test_research_hands import session


def seed(rig):
    reply = command(rig, {'type': 'capability-run', 'model': 'image-tools#invert',
                         'session': rig.app.day.sid, 'input': 'image.2d.rgb8'})
    assert reply.get('ok') is not False, reply
    row, _ = assert_retained(rig, 'invert')
    store = rig.hub.resources.results
    deadline = time.monotonic() + 5
    while store._pending and time.monotonic() < deadline:
        time.sleep(.01)
    [choice] = retained_inputs.choices(rig.hub.resources, rig.app.day.sid)
    return choice['reference'], store.picture(row['id'])


def test_researcher_runs_on_exact_retained_pixels_without_camera_or_hidden_live_values(rig, monkeypatch):
    from orchestrator import kinds
    reference, original = seed(rig)
    rig.hub.source.is_fresh = lambda: False
    monkeypatch.setattr(kinds, 'inputs_for', lambda *a, **k: pytest.fail('retained input borrowed live hidden values'))
    current = rig.hub.latest_info
    researcher = session(sitting=rig.app.day.sid)
    reply = asyncio.run(Hands(rig.hub).run(researcher, 'capability', 'measure retained result',
                        capability='image-tools#mean', resource=reference))
    assert reply['ok'], reply
    row, values = assert_retained(rig, 'mean')
    assert values['rows'][0][1] == float(cv2.imdecode(np.frombuffer(original, np.uint8), cv2.IMREAD_UNCHANGED).mean())
    assert values['inputResources'] == [reference]
    assert row['source'] == {'frame': None, 'seq': None, 'boot': None, 'resources': [reference]}
    assert row['session'] == researcher.sitting and row['for'] == researcher.name
    assert researcher.ran[-1]['inputResources'] == [reference]
    assert 'retained result' in reply['words'] and 'live view' not in reply['words']
    assert rig.hub.latest_info is current
    assert rig.hub.resources.results.picture(reference['id']) == original


def test_researcher_image_output_is_retained_and_eligible_for_another_operation(rig):
    reference, _original = seed(rig)
    reply = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability',
                        capability='image-tools#invert', resource=reference))
    assert reply['ok'], reply
    row, values = assert_retained(rig, 'invert')
    assert values['output'] == 'image.2d.rgb8'
    assert values['scientific']['dtype'] == 'uint8'
    assert values['scientific']['axes'] == ['y', 'x', 'c']
    assert 'semantic' not in values['scientific'] and 'calibration' not in values['scientific']
    assert any(choice['reference']['id'] == row['id'] for choice in retained_inputs.choices(rig.hub.resources, rig.app.day.sid))


@pytest.mark.parametrize('change', [{'session': 'elsewhere'}, {'revision': '0' * 64}, {'component': 'picture-1'}])
def test_researcher_rejects_wrong_resource_without_running_or_losing_prior_results(rig, change):
    reference, _ = seed(rig)
    before = list(results(rig))
    researcher = session(sitting=rig.app.day.sid)
    hands = Hands(rig.hub)
    reply = asyncio.run(hands.run(researcher, 'capability', capability='image-tools#mean',
                                 resource={**reference, **change}))
    assert not reply['ok'], reply
    assert results(rig) == before and not researcher.ran and hands.busy is None
    retry = asyncio.run(hands.run(researcher, 'capability', capability='image-tools#mean', resource=reference))
    assert retry['ok'], retry


@pytest.mark.parametrize('rig', ['human-only'], indirect=True)
def test_retained_input_does_not_grant_researcher_human_only_parameter_authority(rig):
    reference, _ = seed(rig)
    before = list(results(rig))
    reply = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability',
                        capability='image-tools#invert', params={'offset': 0}, resource=reference))
    assert not reply['ok'] and results(rig) == before


def test_researcher_cancel_releases_retained_input_and_operation_slot(rig, monkeypatch):
    from orchestrator.capture import captures
    reference, _ = seed(rig)
    hands = Hands(rig.hub)
    before = dict(captures(rig.hub).memory.reserved)

    async def scenario():
        entered = asyncio.Event()
        async def pending(*args, selected=None):
            assert selected.source_image['resource'] == reference
            entered.set()
            await asyncio.Event().wait()
        monkeypatch.setattr(hands, '_capability_on', pending)
        task = asyncio.create_task(hands.run(session(sitting=rig.app.day.sid), 'capability',
                                            capability='image-tools#mean', resource=reference))
        await asyncio.wait_for(entered.wait(), 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(scenario())
    assert hands.busy is None and captures(rig.hub).memory.reserved == before


def worker():
    from research_worker.researcher import Researcher
    from research_worker.budget import EFFORTS
    r = Researcher.__new__(Researcher)
    r.rid, r.runs, r.started_at = 'r2', 0, time.monotonic()
    r.effort, r._looked_at = EFFORTS['standard'], set()
    r.spec = {'snapshot': {'capabilities': [{'key': 'image-tools#mean', 'name': 'Mean intensity'}]}}
    r.step = lambda *a, **k: None
    r.emit = lambda *a, **k: None
    return r


def test_worker_schema_and_transport_offer_and_forward_exact_reference():
    r = worker()
    shape = {'description': 'Run at most {operation_limit}', 'parameters': {'properties': {
        'operation': {'enum': ['segment']}, 'implementation': {}}}}
    schema = r.run_schema(shape)
    offered = schema['parameters']['properties']['resource']
    assert offered['anyOf'] == retained_inputs.SCHEMA['anyOf']
    assert offered['anyOf'][0]['required'] == ['session', 'id', 'revision', 'component']
    assert offered['anyOf'][0]['additionalProperties'] is False
    reference = {'session': 's1', 'id': 'd1', 'revision': 'a' * 64, 'component': 'picture-0'}
    asked = []
    async def request(op, **payload):
        asked.append((op, payload))
        return {'ok': False, 'error': 'test refusal'}
    r.request = request
    asyncio.run(r.tool_run({'operation': 'capability', 'capability': 'image-tools#mean', 'resource': reference}))
    assert asked[0][1]['resource'] == reference and asked[0][0] == 'run'
    assert r.runs == 0


@pytest.mark.parametrize('extra', [{'input': 'e1'}, {'frame': 0}, {'operation': 'segment'}, {'resource': 'd1'}])
def test_worker_rejects_ambiguous_retained_selection_before_transport(extra):
    r = worker()
    async def request(*a, **k):
        pytest.fail('invalid retained selection reached transport')
    r.request = request
    reply, _ = asyncio.run(r.tool_run({'operation': 'capability', 'capability': 'image-tools#mean',
                                      'resource': {'id': 'd1'}, **extra}))
    assert 'error' in reply


def test_manager_routes_exact_retained_reference_to_researcher_hands(rig, tmp_path):
    from orchestrator.research import ResearchManager
    from tests.test_research import FakeHub
    reference, _ = seed(rig)
    rig.hub.source.is_fresh = lambda: False
    manager = ResearchManager(FakeHub(), root=tmp_path / 'research')
    manager.hands = Hands(rig.hub)
    researcher = session(sitting=rig.app.day.sid)
    researcher.root = tmp_path / 'research'
    manager.sessions[researcher.rid] = researcher
    reply = asyncio.run(manager._request({'op': 'run', 'rid': researcher.rid, 'operation': 'capability',
                                         'capability': 'image-tools#mean', 'resource': reference}))
    assert reply['ok'], reply
    assert researcher.ran[-1]['inputResources'] == [reference]
    _, values = assert_retained(rig, 'mean')
    assert values['inputResources'] == [reference]


@pytest.mark.parametrize('output', ['labels.instance.2d', 'tracks.graph', 'volume.3d'])
def test_researcher_refuses_unsupported_output_without_opening_retained_resource(rig, monkeypatch, output):
    hands = Hands(rig.hub)
    monkeypatch.setattr(hands, 'capability', lambda *a: ({'input': 'image.2d.rgb8', 'output': output}, {}, None, None))
    reply = asyncio.run(hands.run(session(sitting=rig.app.day.sid), 'capability',
                                 capability='any', resource={'id': 'd1'}))
    assert not reply['ok'] and 'cannot consume' in reply['error']
    assert hands.busy is None
