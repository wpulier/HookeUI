"""Bundle members through production actor doors.

Synthetic reference images and a stand-in installer; member adapters execute in real
worker processes. Chat planning is scripted, not a paid model or usability study.
"""
import asyncio

import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, kinds, operations
from tests.test_bundle_runtime import contribute
from tests.test_env_runner import home, standin_uv  # noqa: F401


@pytest.fixture
def rig(home, monkeypatch, tmp_path, request):
    from tests.capability_rig import Rig

    registry_before = dict(operations.OPERATIONS), set(kinds.registered())
    owner = contribute(home)
    if getattr(request, 'param', '') == 'human-only':
        manifest = home / 'repo/models/image-tools/model.toml'
        manifest.write_text(manifest.read_text().replace('id = "offset", type = "int"',
                                                        'id = "offset", agent = false, type = "int"'))
        catalog.manifests.cache_clear()
        owner = catalog.get('image-tools')
    standin_uv(home, monkeypatch)
    folder, _ = envs.install(owner)
    made = Rig(tmp_path / 'rig')
    made.app.catalog_changed()
    made.bundle_generation = folder.name
    yield made
    operations.OPERATIONS.clear()
    operations.OPERATIONS.update(registry_before[0])
    kinds.registered().clear()
    kinds.registered().update(registry_before[1])


def results(rig):
    return [row for row in rig.hub.resources.results.entries() if row.get('tool') == 'run_capability']


def command(rig, message):
    from orchestrator.commands import handle
    from tests.test_sources import Client

    client = Client()
    asyncio.run(handle(rig.app, client, {**message, 'requestId': 'bundle-command'}))
    return client.replies[-1]


def assert_retained(rig, member):
    row = results(rig)[-1]
    values = rig.hub.resources.results.values(row['id'])
    assert values['capability'] == f'image-tools#{member}'
    assert values['producer']['implementation'] == f'image-tools#{member}'
    assert values['producer']['generation'] == rig.bundle_generation
    assert values['producer']['catalog']['key'] == 'image-tools'
    assert row['madeBy'][0]['implementation'] == f'image-tools#{member}'
    assert row['madeBy'][0]['producer']['generation'] == rig.bundle_generation
    from orchestrator.day.store import SessionStore
    from orchestrator.resources import Results

    reopened = Results(rig.hub.resources.results.root,
                       SessionStore(rig.app.day.root, session=rig.app.day.sid))
    assert reopened.values(row['id']) == values
    assert reopened.get(row['id'])['madeBy'] == row['madeBy']
    return row, values


@pytest.mark.parametrize('actor', ['action', 'try', 'agent', 'researcher'])
def test_proven_bundle_member_reaches_existing_actor_door_and_retains_identity(rig, actor):
    if actor == 'action':
        reply = command(rig, {'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}})
        assert reply.get('ok') is not False, reply
    elif actor == 'try':
        reply = command(rig, {'type': 'capability-run', 'model': 'image-tools#mean',
                              'session': rig.app.day.sid, 'input': 'image.2d.rgb8'})
        assert reply.get('ok') is not False, reply
    elif actor == 'agent':
        runs = asyncio.run(rig.turn([('run_capability', {'capability': 'image-tools#mean'})],
                                     'Measure the mean intensity'))
        run = next(run for run in runs if run.tool == 'run_capability')
        assert run.ok, run.summary
    else:
        from orchestrator.research.hands import Hands
        from tests.test_research_hands import session

        reply = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability',
                            'mean intensity', capability='image-tools#mean'))
        assert reply['ok'], reply
    row, values = assert_retained(rig, 'mean')
    assert values['rows'][0][0] == 'whole' and 0 <= values['rows'][0][1] <= 255
    assert len(values['rows']) == 1
    if actor in {'action', 'try'}:
        assert reply['table'] == row['id']
        assert reply['tableIdentity'] == {'session': rig.app.day.sid, 'revision': 0}
        assert row['madeBy'][0]['origin'] == 'user-chosen'


@pytest.mark.parametrize('actor', ['action', 'try'])
def test_unchecked_configuration_preserves_active_bundle_and_known_good_retained_result(rig, actor):
    good = command(rig, {'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}})
    assert good.get('ok') is not False, good
    row, values = assert_retained(rig, 'mean')
    before = list(results(rig))
    message = ({'type': 'action', 'capability': 'catalog:image-tools#invert', 'args': {'offset': 1}}
               if actor == 'action' else
               {'type': 'capability-run', 'model': 'image-tools#invert', 'session': rig.app.day.sid,
                'input': 'image.2d.rgb8', 'params': {'offset': 1}})
    reply = command(rig, message)
    assert reply.get('ok') is False, reply
    assert 'check' in str(reply).lower(), reply
    assert rig.hub.resources.results.values(row['id']) == values
    assert results(rig) == before
    active = env_runner.in_use(catalog.get('image-tools'))
    assert active.folder.name == rig.bundle_generation and active.proof['ok']
    reply = command(rig, {'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}})
    assert reply.get('ok') is not False, reply
    next_row, next_values = assert_retained(rig, 'mean')
    assert next_row['id'] != row['id'] and next_values['rows'] == values['rows']


@pytest.mark.parametrize('rig', ['human-only'], indirect=True)
def test_bundle_human_only_parameter_rejects_actor_spoofing_but_allows_studio(rig):
    runs = asyncio.run(rig.turn([('run_capability', {'capability': 'image-tools#invert',
                               'params': {'offset': 0}, 'by': 'user'})], 'Invert with offset zero'))
    run = next(run for run in runs if run.tool == 'run_capability')
    assert not run.ok
    from orchestrator.research.hands import Hands
    from tests.test_research_hands import session

    reply = asyncio.run(Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability',
                        'invert image', capability='image-tools#invert', params={'offset': 0}))
    assert not reply['ok'], reply
    for message in [
        {'type': 'action', 'capability': 'catalog:image-tools#invert', 'args': {'offset': 0}},
        {'type': 'capability-run', 'model': 'image-tools#invert', 'session': rig.app.day.sid,
         'input': 'image.2d.rgb8', 'params': {'offset': 0}},
    ]:
        reply = command(rig, message)
        assert reply.get('ok') is not False, reply
        assert_retained(rig, 'invert')


@pytest.mark.parametrize('same_display_names', [False, True])
def test_agent_uses_exact_bundle_owner_when_normalized_names_collide(rig, home, same_display_names):
    import json
    import shutil

    # These owner IDs become the same text under the legacy name normalizer. Both
    # adapters run for real; the second deliberately returns a distinct value.
    source = home / 'repo/models/image-tools'
    target = home / 'repo/models/imagetools'
    shutil.copytree(source, target)
    manifest = target / 'model.toml'
    declaration = manifest.read_text().replace('image-tools', 'imagetools')
    if not same_display_names:
        declaration = declaration.replace('Invert image', 'Second inversion').replace('Mean intensity', 'Offset intensity')
    manifest.write_text(declaration)
    adapter = target / 'adapter.py'
    adapter.write_text(adapter.read_text().replace('float(picture.mean())', 'float(picture.mean()) + 1'))
    reference = target / 'reference-mean.json'
    expected = json.loads(reference.read_text())
    expected['rows'][0][1] += 1
    reference.write_text(json.dumps(expected))
    catalog.manifests.cache_clear()
    owner = catalog.get('imagetools')
    assert owner is not None, catalog.problems()
    folder, _ = envs.install(owner)
    rig.app.catalog_changed()

    seen = {}
    for key, generation in [('image-tools#mean', rig.bundle_generation), ('imagetools#mean', folder.name)]:
        runs = asyncio.run(rig.turn([('run_capability', {'capability': key})], f'Run {key}'))
        run = next(run for run in runs if run.tool == 'run_capability')
        assert run.ok, run.summary
        row = results(rig)[-1]
        values = rig.hub.resources.results.values(row['id'])
        assert values['capability'] == key
        assert row['madeBy'][0]['implementation'] == key
        assert values['producer']['generation'] == generation
        assert values['producer']['catalog']['key'] == key.partition('#')[0]
        seen[key] = values['rows'][0][1]
    assert seen['imagetools#mean'] == pytest.approx(seen['image-tools#mean'] + 1)

    before = list(results(rig))
    for malformed in ['image-tools#me-an', 'imagetools#me_an', 'image-tools##mean']:
        runs = asyncio.run(rig.turn([('run_capability', {'capability': malformed})], f'Run {malformed}'))
        run = next(run for run in runs if run.tool == 'run_capability')
        assert not run.ok, (malformed, run.summary)
        assert results(rig) == before


def test_retained_output_feeds_next_worker_without_changing_stage_or_inheriting_scale(rig):
    import cv2
    import numpy as np
    import time
    from orchestrator import retained_inputs

    reply = command(rig, {'type': 'capability-run', 'model': 'image-tools#invert',
                          'session': rig.app.day.sid, 'input': 'image.2d.rgb8'})
    assert reply.get('ok') is not False, reply
    row, _values = assert_retained(rig, 'invert')
    store = rig.hub.resources.results
    deadline = time.monotonic() + 5
    while store._pending and time.monotonic() < deadline:
        time.sleep(.01)
    [choice] = retained_inputs.choices(rig.hub.resources, rig.app.day.sid)
    raw = store.picture(row['id'])
    expected = float(cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_UNCHANGED).mean())
    current = rig.hub.latest_info
    reply = command(rig, {'type': 'capability-run', 'model': 'image-tools#mean',
                          'session': rig.app.day.sid, 'input': 'image.2d.rgb8', 'resource': choice['reference']})
    assert reply.get('ok') is not False, reply
    kept, values = assert_retained(rig, 'mean')
    assert kept['source'] == {'frame': None, 'seq': None, 'boot': None, 'resources': [choice['reference']]}
    assert values['rows'][0][1] == expected
    assert values['inputResources'] == [choice['reference']]
    assert rig.hub.latest_info is current
    assert store.picture(row['id']) == raw
    # Same server path rejects forged cross-session references, retaining the known-good result.
    before = len(results(rig))
    reply = command(rig, {'type': 'capability-run', 'model': 'image-tools#mean',
                          'session': rig.app.day.sid, 'input': 'image.2d.rgb8',
                          'resource': {**choice['reference'], 'session': 'another'}})
    assert reply.get('ok') is False and len(results(rig)) == before


def test_agent_discovers_input_through_resources_and_action_uses_same_exact_reference(rig):
    import json
    import time
    reply = command(rig, {'type': 'capability-run', 'model': 'image-tools#invert',
                          'session': rig.app.day.sid, 'input': 'image.2d.rgb8'})
    assert reply.get('ok') is not False, reply
    row, _ = assert_retained(rig, 'invert')
    deadline = time.monotonic() + 5
    while rig.hub.resources.results._pending and time.monotonic() < deadline:
        time.sleep(.01)
    runs = asyncio.run(rig.turn([('resources', {'id': row['id']})], 'Read that retained image'))
    read = next(run for run in runs if run.tool == 'resources')
    assert read.ok, read.summary
    line = next(line for line in read.summary.splitlines() if line.startswith('Exact original input for run_capability.resource'))
    reference = json.loads(line.split(': ', 1)[1])
    runs = asyncio.run(rig.turn([('run_capability', {'capability': 'image-tools#mean', 'resource': reference})],
                               'Measure that saved original'))
    run = next(run for run in runs if run.tool == 'run_capability')
    assert run.ok, run.summary
    _, agent = assert_retained(rig, 'mean')
    reply = command(rig, {'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}, 'resource': reference})
    assert reply.get('ok') is not False, reply
    _, manual = assert_retained(rig, 'mean')
    assert manual['inputResources'] == agent['inputResources'] == [reference]
    assert manual['rows'] == agent['rows']
