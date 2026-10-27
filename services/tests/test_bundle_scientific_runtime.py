"""Actual confined synthetic adapters; no upstream package or biological suitability claim."""
import asyncio

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import bundle_runtime, env_runner, envs, kinds, operations, retained_inputs
from tests.test_bundle_runtime import contribute
from tests.test_bundle_actors import command, results
from tests.test_env_runner import home, standin_uv  # noqa: F401


def contribute_scientific(home):
    contribute(home)
    path = home / 'repo/models/image-tools/model.toml'
    text = path.read_text().replace(
        'outputs = [{ id = "result", type = "image.2d.rgb8" }]',
        'outputs = [{ id = "result", type = "image.2d.rgb8" }]\n'
        'scientific = { inputs = { image = {} }, outputs = { result = { exact = { semantic = "intensity" } } } }')
    text = text.replace(
        'outputs = [{ id = "table", type = "table.objects" }]',
        'outputs = [{ id = "table", type = "table.objects" }]\n'
        'scientific = { inputs = { image = { exact = { semantic = "intensity" } } }, outputs = { table = {} } }')
    text = text.replace('device = "cpu"', 'device = "cpu"\nscientific = { input = { semantic = "intensity" }, reference = {} }')
    text = text.replace('reference = {}', 'reference = { semantic = "intensity" }', 1)
    path.write_text(text)
    (path.parent / 'adapter.py').write_text('''
def invert(picture, params, resources):
    descriptor = {'semantic': 'intensity'}
    if params.get('offset') == 1:
        descriptor['shape'] = [1, 1, 3]
    if params.get('offset') == 2:
        import numpy as np
        return {'value': np.ones((*picture.shape[:2], 4), np.uint8), 'scientific': descriptor}
    return {'value': 255 - picture, 'scientific': descriptor}

def mean(picture, params, resources):
    assert resources['image']['semantic'] == 'intensity'
    return {'value': {'columns': [{'name': 'region'}, {'name': 'mean'}],
                      'rows': [['whole', float(picture.mean())]]}, 'scientific': {}}
''')
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    assert owner is not None, catalog.problems()
    return owner


def contribute_regions(home):
    contribute_scientific(home)
    path = home / 'repo/models/image-tools/model.toml'
    path.write_text(path.read_text().replace(
        'scientific = { inputs = { image = { exact',
        'bridge = "regions-v1"\nscientific = { inputs = { image = { exact'))
    adapter = path.parent / 'adapter.py'
    adapter.write_text(adapter.read_text().split('def mean(')[0] + '''
def mean(resources, params):
    source = resources['image']
    assert source.descriptor['semantic'] == 'intensity'
    assert source.dtype == 'uint8'
    h, w, channels = source.shape
    if params.get('malformed'):
        import inspect, struct, time
        out = inspect.getclosurevars(source._request).nonlocals['out']
        raw = b'{"op":"read-region","region":' + b'[' * 10000 + b'0' + b']' * 10000 + b'}'
        out.write(struct.pack('>I', len(raw)) + raw + struct.pack('>I', 0))
        out.flush()
        time.sleep(120)
    if params.get('hang'):
        import time
        source.read(((0, 1), (0, w), (0, channels)))
        time.sleep(120)
    total = 0
    for y in range(h):
        row = source.read(((y, y + 1), (0, w), (0, channels)))
        assert not row.flags.writeable
        total += int(row.sum())
    return {'value': {'columns': [{'name': 'region'}, {'name': 'mean'}],
                      'rows': [['whole', total / (h * w * channels)]]}, 'scientific': {}}
''')
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    assert owner is not None, catalog.problems()
    return owner


@pytest.fixture(params=['inline', 'regions'])
def rig(home, monkeypatch, tmp_path, request):
    from tests.capability_rig import Rig
    before = dict(operations.OPERATIONS), set(kinds.registered())
    owner = (contribute_regions if request.param == 'regions' else contribute_scientific)(home)
    standin_uv(home, monkeypatch)
    folder, _ = envs.install(owner)
    assert env_runner.in_use(owner).proof['ok']
    made = Rig(tmp_path / 'rig')
    made.app.catalog_changed()
    made.bundle_generation = folder.name
    yield made
    operations.OPERATIONS.clear()
    operations.OPERATIONS.update(before[0])
    kinds.registered().clear()
    kinds.registered().update(before[1])


def test_proof_records_actual_scientific_metadata_and_direct_calls_cannot_bypass_it(rig):
    installed = env_runner.in_use(catalog.get('image-tools'))
    assert installed.proof['cases'][0]['scientific']['actual']['semantic'] == 'intensity'
    assert installed.proof['cases'][0]['scientific']['evidence']['producerFields'] == ['semantic']
    members = bundle_runtime.expand(installed)
    worker = env_runner.Client(members[1])
    try:
        picture = np.ones((16, 16, 3), np.uint8)
        for descriptor in ({}, {'semantic': 'class-labels'}, {'semantic': 'instance-labels'}):
            with pytest.raises(env_runner.Failed, match='Incompatible scientific input'):
                worker.run(picture, {}, output='table.objects', scientific=descriptor)
            assert worker._process is None
        for wrong in (picture.astype('float32'), picture[:, :, 0], np.ones((2, 2, 4), np.uint8)):
            with pytest.raises(env_runner.Failed, match='RGB8'):
                worker.run(wrong, {}, output='table.objects', scientific={'semantic': 'intensity'})
            assert worker._process is None
        with pytest.raises(env_runner.Failed, match='declared port'):
            worker.run(picture, {}, output='text', scientific={'semantic': 'intensity'})
        got = worker.run(picture, {}, output='table.objects', scientific={'semantic': 'intensity'})
        assert got['rows'] == [['whole', 1.0]]
    finally:
        worker.stop()


def test_contradictory_output_is_rejected_and_good_worker_can_retry(rig):
    installed = bundle_runtime.expand(env_runner.in_use(catalog.get('image-tools')))[0]
    worker = env_runner.Client(installed)
    try:
        picture = np.ones((16, 16, 3), np.uint8)
        with pytest.raises(env_runner.Failed, match='Invalid scientific output.*shape'):
            worker.run(picture, {'offset': 1}, output='image.2d.rgb8')
        with pytest.raises(env_runner.Failed, match='exactly RGB8'):
            worker.run(picture, {'offset': 2}, output='image.2d.rgb8')
        got = worker.run(picture, {'offset': 0}, output='image.2d.rgb8')
        assert got['scientific']['semantic'] == 'intensity'
        assert got['scientific'].get('calibration') is None
        assert np.all(got['picture'] == 254)
    finally:
        worker.stop()


@pytest.mark.parametrize('actor', ['try', 'action', 'agent', 'researcher'])
def test_actors_reject_unknown_meaning_then_chain_exact_semantic_output(rig, actor, monkeypatch):
    async def run(resource=None):
        extra = {'resource': resource} if resource else {}
        if actor == 'researcher':
            from orchestrator.research.hands import Hands
            from tests.test_research_hands import session
            return await Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability',
                         'measure', capability='image-tools#mean', **extra)
        if actor == 'agent':
            calls = await rig.turn([('run_capability', {'capability': 'image-tools#mean', **extra})], 'Measure intensity')
            called = next(call for call in calls if call.tool == 'run_capability')
            return {'ok': called.ok, 'says': called.summary}
        from orchestrator.commands import handle
        from tests.test_sources import Client
        client = Client()
        message = ({'type': 'capability-run', 'model': 'image-tools#mean', 'session': rig.app.day.sid,
                    'input': 'image.2d.rgb8'} if actor == 'try' else
                   {'type': 'action', 'capability': 'catalog:image-tools#mean', 'args': {}})
        await handle(rig.app, client, {**message, **extra, 'requestId': 'scientific'})
        return client.replies[-1]

    before = list(results(rig))
    rejected = asyncio.run(run())
    assert rejected.get('ok') is False, rejected
    assert 'scientific meaning is unknown' in str(rejected), rejected
    assert results(rig) == before
    import threading
    from tests.waiting import HANG_S
    store = rig.hub.resources.results
    release, waiting = threading.Event(), threading.Event()
    write, wait = store._write, store.wait_for_picture
    def blocked(day, name, data, **kwargs):
        if name.endswith('.png'):
            assert release.wait(HANG_S)
        return write(day, name, data, **kwargs)
    def observed(*args, **kwargs):
        waiting.set()
        return wait(*args, **kwargs)
    monkeypatch.setattr(store, '_write', blocked)
    monkeypatch.setattr(store, 'wait_for_picture', observed)
    try:
        async def chain():
            from orchestrator.commands import handle
            from tests.test_sources import Client
            from tests.waiting import until_async
            client = Client()
            producer = asyncio.create_task(handle(rig.app, client, {
                'type': 'action', 'capability': 'catalog:image-tools#invert', 'args': {},
                'requestId': 'producing-scientific'}))
            try:
                await until_async(lambda: len(results(rig)) > len(before))
                saved = results(rig)[-1]
                values = store.values(saved['id'])
                assert values['scientific']['semantic'] == 'intensity'
                assert values['scientificEvidence']['producerFields'] == ['semantic']
                reference = retained_inputs.reference_for(rig.hub.resources, saved['id'], rig.app.day.sid)
                assert await asyncio.to_thread(waiting.wait, HANG_S)
                assert not producer.done(), 'producer completed before its original was retained'
            finally:
                release.set()
            await producer
            assert client.replies[-1].get('ok') is not False, client.replies[-1]
            return await run(reference), saved, values, reference
        accepted, saved, values, reference = asyncio.run(chain())
        assert accepted.get('ok') is not False, accepted
        next_values = rig.hub.resources.results.values(results(rig)[-1]['id'])
        assert next_values['inputResources'] == [reference]
        assert next_values['rows'][0][0] == 'whole'
        assert rig.hub.resources.results.values(saved['id']) == values
    finally:
        release.set()


@pytest.mark.parametrize('actor', ['try', 'action', 'agent', 'researcher'])
@pytest.mark.parametrize('failure', ['original', 'metadata'])
def test_image_producer_save_failure_is_truthful_for_every_actor_and_retry_works(rig, actor, failure, monkeypatch):
    """Actual isolated adapter and actor paths; only the result writer's disk refusal is controlled."""
    async def produce():
        if actor == 'researcher':
            from orchestrator.research.hands import Hands
            from tests.test_research_hands import session
            return await Hands(rig.hub).run(session(sitting=rig.app.day.sid), 'capability',
                                          'invert', capability='image-tools#invert')
        if actor == 'agent':
            calls = await rig.turn([('run_capability', {'capability': 'image-tools#invert'})], 'Invert this image')
            called = next(call for call in calls if call.tool == 'run_capability')
            return {'ok': called.ok, 'says': called.summary}
        from orchestrator.commands import handle
        from tests.test_sources import Client
        client = Client()
        message = ({'type': 'capability-run', 'model': 'image-tools#invert',
                    'session': rig.app.day.sid, 'input': 'image.2d.rgb8'} if actor == 'try' else
                   {'type': 'action', 'capability': 'catalog:image-tools#invert', 'args': {}})
        await handle(rig.app, client, {**message, 'requestId': 'save-proof'})
        return client.replies[-1]

    store = rig.hub.resources.results
    assert asyncio.run(produce()).get('ok') is not False
    good = results(rig)[-1]
    original_pixels = store.picture(good['id'])
    original_write = store._write
    with monkeypatch.context() as fail:
        suffix = '.png' if failure == 'original' else '.json'
        fail.setattr(store, '_write', lambda day, name, data, **kw:
                     False if name.endswith(suffix) else original_write(day, name, data, **kw))
        refused = asyncio.run(produce())
    assert refused.get('ok') is False, refused
    assert 'storage' in str(refused), refused
    failed = results(rig)[-1]
    assert failed['id'] != good['id'] and failed['state'] == 'failed'
    assert store.picture(good['id']) == original_pixels
    assert env_runner.in_use(catalog.get('image-tools')).folder.name == rig.bundle_generation
    corrected = asyncio.run(produce())
    assert corrected.get('ok') is not False, corrected
    retry = results(rig)[-1]
    assert retry['id'] not in (good['id'], failed['id']) and retry['state'] == 'done'
    assert store.picture(retry['id']) == original_pixels


def test_picker_uses_the_same_contract_and_keeps_unsupported_resources_visible(rig):
    made = command(rig, {'type': 'action', 'capability': 'catalog:image-tools#invert', 'args': {}})
    assert made.get('ok') is not False, made
    installed = env_runner.in_use(catalog.get('image-tools'))
    mean = bundle_runtime.expand(installed)[1].m
    listed = retained_inputs.choices(rig.hub.resources, rig.app.day.sid, mean)
    assert listed[-1]['compatibility']['compatible'] is True
    from orchestrator.scientific_ports import compatibility
    assert compatibility(mean, {})['compatible'] is False
    assert compatibility(mean, {'semantic': 'class-labels'})['compatible'] is False


def test_reference_must_independently_satisfy_the_declared_scientific_output(home, monkeypatch):
    owner = contribute_scientific(home)
    path = home / 'repo/models/image-tools/model.toml'
    path.write_text(path.read_text().replace('reference = { semantic = "intensity" }', 'reference = {}'))
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    standin_uv(home, monkeypatch)
    with pytest.raises(envs.Failed, match='scientific meaning is unknown'):
        envs.install(owner)


@pytest.mark.parametrize('failure', ['budget', 'cancel', 'malformed'])
def test_regional_failure_reaps_owned_worker_preserves_other_tool_and_allows_retry(home, monkeypatch, failure):
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from orchestrator import owned_process, region_inputs

    owner = contribute_regions(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    invert, mean = bundle_runtime.expand(env_runner.in_use(owner))
    good, reader = env_runner.Client(invert), env_runner.Client(mean)
    picture = np.ones((16, 16, 3), np.uint8)
    seen, stop = threading.Event(), threading.Event()
    pids = []
    answer = region_inputs.RegionInputs.answer

    def observe(broker, header):
        if not pids:
            pids.append(reader._process.pid)
        result = answer(broker, header)
        seen.set()
        return result

    def run():
        with owned_process.scope(stop=stop.is_set):
            return reader.run(picture, {'hang': failure == 'cancel', 'malformed': failure == 'malformed'}, output='table.objects',
                              scientific={'semantic': 'intensity'})

    try:
        first = good.run(picture, {}, output='image.2d.rgb8')
        good_pid = good._process.pid
        monkeypatch.setattr(region_inputs.RegionInputs, 'answer', observe)
        if failure == 'malformed':
            reader.run(picture, {}, output='table.objects', scientific={'semantic': 'intensity'})
            with pytest.raises(env_runner.Failed, match='header'):
                run()
        elif failure == 'budget':
            monkeypatch.setattr(region_inputs, 'MAX_READ_REQUESTS', 2)
            with pytest.raises(env_runner.Failed, match='regional read allowance'):
                run()
            monkeypatch.setattr(region_inputs, 'MAX_READ_REQUESTS', 1024)
        else:
            with ThreadPoolExecutor(max_workers=1) as executor:
                task = executor.submit(run)
                try:
                    assert seen.wait(30), 'The actual worker did not request its first region'
                finally:
                    stop.set()
                with pytest.raises(env_runner.Failed):
                    task.result(timeout=10)
            stop.clear()
        assert pids
        with pytest.raises(ProcessLookupError):
            os.killpg(pids[0], 0)
        assert good._process.pid == good_pid
        again = good.run(picture, {}, output='image.2d.rgb8')
        np.testing.assert_array_equal(again['picture'], first['picture'])
        retried = reader.run(picture, {}, output='table.objects', scientific={'semantic': 'intensity'})
        assert retried['rows'] == [['whole', 1.0]]
    finally:
        stop.set()
        reader.stop()
        good.stop()
