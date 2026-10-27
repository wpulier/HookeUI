"""Real retained-result storage, bounded original reads and semantic refusals; synthetic pixels."""
import asyncio
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator import retained_inputs as inputs
from orchestrator.brain.registry import ToolFailed
from tests.test_resources import app_at


@pytest.fixture
def saved(tmp_path):
    built = app_at(tmp_path).state.app
    store = built.hub.resources.results
    png = cv2.imencode('.png', np.full((7, 9, 3), 71, np.uint8))[1].tobytes()
    row = store.add('run_capability', {}, 'synthetic image', values={
        'output': 'image.2d.rgb8', 'producer': {'implementation': 'fixture', 'generation': 'g1'},
        'scientific': {'dtype': 'uint8', 'axes': ['y', 'x', 'c'], 'shape': [7, 9, 3]}}, picture_bytes=[png])
    # Wait only for the result store's asynchronous file retention, never use its preview substitute.
    deadline = time.monotonic() + 5
    while store._pending and time.monotonic() < deadline:
        time.sleep(.01)
    assert not store._pending
    [choice] = inputs.choices(built.hub.resources, built.day.sid)
    return built, row, choice['reference'], png


def test_exact_lossless_pixels_and_unknown_scientific_meaning_survive_reopen(saved, tmp_path):
    built, row, ref, png = saved
    from orchestrator.capture import captures
    before = captures(built.hub).memory.wire()
    async def run(app):
        async with inputs.admitted(app.hub, ref, app.day.sid) as capture:
            assert capture.analysis_bytes == png
            descriptor = capture.source_image['scientific']
            assert descriptor['reference'] == ref and descriptor['shape'] == [7, 9, 3]
            assert 'semantic' not in descriptor and 'calibration' not in descriptor
            assert capture.scale.get('umPerPx') is None
    asyncio.run(run(built))
    assert captures(built.hub).memory.wire()['classes']['analysis']['reservedMb'] == before['classes']['analysis']['reservedMb']
    again = app_at(tmp_path).state.app
    assert inputs.choices(again.hub.resources, again.day.sid)[0]['reference'] == ref
    asyncio.run(run(again))


def test_project_api_keeps_a_real_retained_content_reference_after_restart(saved, tmp_path):
    from fastapi.testclient import TestClient
    from tests.test_scientific_projects import document
    built, row, reference, png = saved
    client = TestClient(app_at(tmp_path), base_url='http://127.0.0.1:8770')
    work = {**document(), 'resources': [reference]}
    response = client.post('/api/scientific-projects',
                           headers={'Origin': 'http://127.0.0.1:8770', 'X-MicroscopeMind': 'save-project'},
                           json={'session': built.day.sid, 'document': work})
    assert response.status_code == 200, response.text
    project = response.json()['project']
    restarted = app_at(tmp_path)
    reopened = TestClient(restarted).get('/api/scientific-projects/' + project['id'],
                                        params={'session': built.day.sid, 'revision': 1})
    assert reopened.json()['project']['document'] == work
    exact = reopened.json()['project']['document']['resources'][0]
    capture, hold = inputs.resolve(restarted.state.app.hub, exact, built.day.sid)
    try:
        assert capture.analysis_bytes == png
        assert capture.source_image['scientific']['reference'] == reference
    finally:
        hold.release()
    assert restarted.state.app.hub.resources.results.get(row['id'])['digests'] == row['digests']


@pytest.mark.parametrize('change', [
    {'session': 'another'}, {'id': '../d1'}, {'revision': '0' * 64}, {'component': 'picture-1'},
    {'revision': 0}, {'extra': 'ignored'},
])
def test_forged_stale_and_cross_session_references_refused(saved, change):
    built, _, ref, _ = saved
    with pytest.raises((ValueError, OSError)):
        inputs.resolve(built.hub, {**ref, **change}, built.day.sid)


@pytest.mark.parametrize('problem', ['changed', 'missing', 'pending', 'descriptor', 'symlink', 'directory'])
def test_failed_original_or_metadata_does_not_publish_input_or_lose_result(saved, problem):
    import json
    built, row, ref, _ = saved
    store = built.hub.resources.results
    path = store.root / row['day'] / row['pictures'][0]
    if problem == 'symlink':
        original = path.with_suffix('.original'); path.rename(original); path.symlink_to(original)
    elif problem == 'directory':
        path.unlink(); path.mkdir()
    elif problem == 'changed':
        path.write_bytes(b'changed')
    elif problem == 'missing':
        path.unlink()
    elif problem == 'pending':
        store._pending[(row['day'], row['pictures'][0])] = b'not yet retained'
    else:
        metadata = store.root / row['day'] / (row['id'] + '.json')
        data = json.loads(metadata.read_text())
        data['values']['scientific']['shape'] = [70, 90, 3]
        metadata.write_text(json.dumps(data))
        ref = inputs.choices(built.hub.resources, built.day.sid)[0]['reference']
    with pytest.raises((ValueError, OSError)):
        inputs.resolve(built.hub, ref, built.day.sid)
    assert store.get(row['id'])['id'] == row['id']


def test_cancellation_releases_an_admitted_input_even_during_disk_read(monkeypatch):
    import threading
    entered, finish = threading.Event(), threading.Event()
    released = []
    def resolve(*args, **kwargs):
        entered.set(); finish.wait(2)
        return object(), SimpleNamespace(release=lambda: released.append(True))
    monkeypatch.setattr(inputs, 'resolve', resolve)
    async def run():
        async def work():
            async with inputs.admitted(None, {}, 's'):
                pytest.fail('cancelled input reached dispatch')
        task = asyncio.create_task(work())
        await asyncio.to_thread(entered.wait, 2)
        task.cancel(); finish.set()
        with pytest.raises(asyncio.CancelledError):
            await task
    asyncio.run(run())
    assert released == [True]


@pytest.mark.parametrize('broken', [[], None, 5, 'text'])
def test_one_malformed_metadata_record_does_not_break_other_inputs(saved, broken):
    import json
    built, row, ref, png = saved
    store = built.hub.resources.results
    good = store.add('run_capability', {}, 'good', values={'output': 'image.2d.rgb8'}, picture_bytes=[png])
    (store.root / row['day'] / (row['id'] + '.json')).write_text(json.dumps(broken))
    assert [item['reference']['id'] for item in inputs.choices(built.hub.resources, built.day.sid)] == [good['id']]
    with pytest.raises(ValueError, match='metadata'):
        inputs.resolve(built.hub, ref, built.day.sid)


def test_existing_public_read_exposes_the_exact_component_reference(saved):
    import json
    built, row, ref, _ = saved
    reading = built.hub.resources.read_full(row['id'], built.day.sid)
    line = next(line for line in reading.splitlines() if line.startswith('Exact original input for run_capability.resource'))
    assert json.loads(line.split(': ', 1)[1]) == ref


def test_research_admission_cannot_borrow_foreground_memory(saved):
    from orchestrator.capture import captures, Refused
    built, _, ref, _ = saved
    memory = captures(built.hub).memory
    before = memory.wire()['classes']['analysis']['reservedMb']
    budget = memory.wire()['classes']['research']['allowanceMb']
    with memory.reserve('research', int(budget * 1024 * 1024), 'occupied research budget'):
        with pytest.raises(Refused):
            inputs.resolve(built.hub, ref, built.day.sid, allowance='research')
        capture, hold = inputs.resolve(built.hub, ref, built.day.sid, allowance='analysis')
        hold.release()
    assert memory.wire()['classes']['analysis']['reservedMb'] == before
    capture, hold = inputs.resolve(built.hub, ref, built.day.sid, allowance='research')
    assert memory.wire()['classes']['analysis']['reservedMb'] == before
    hold.release()
    assert memory.wire()['classes']['research']['reservedMb'] == 0


def test_chained_input_waits_for_its_original_without_blocking_other_results(saved, monkeypatch):
    """A completed producer can hand its exact reference straight to the next tool."""
    import concurrent.futures
    import threading
    from tests.waiting import HANG_S
    built, _, good_ref, png = saved
    store = built.hub.resources.results
    writing, release = threading.Event(), threading.Event()
    write = store._write
    def blocked(day, name, data, **kwargs):
        if name.endswith('.png'):
            writing.set()
            assert release.wait(HANG_S)
        return write(day, name, data, **kwargs)
    monkeypatch.setattr(store, '_write', blocked)
    row = store.add('run_capability', {}, 'next input', values={
        'output': 'image.2d.rgb8'}, picture_bytes=[png])
    assert writing.wait(HANG_S)
    ref = inputs.reference_for(built.hub.resources, row['id'], built.day.sid)
    with concurrent.futures.ThreadPoolExecutor() as pool:
        future = pool.submit(inputs.resolve, built.hub, ref, built.day.sid)
        try:
            # The exact original is still being written. A different retained result remains usable.
            with pytest.raises(concurrent.futures.TimeoutError):
                future.result(timeout=.1)
            capture, hold = inputs.resolve(built.hub, good_ref, built.day.sid)
            assert capture.analysis_bytes == png
            hold.release()
        finally:
            release.set()
        capture, hold = future.result(timeout=HANG_S)
        try:
            assert capture.analysis_bytes == png
            assert (store.root / row['day'] / row['pictures'][0]).read_bytes() == png
        finally:
            hold.release()


@pytest.fixture
def writing_result(saved, monkeypatch):
    import threading
    from tests.waiting import HANG_S, until
    built, _, _, png = saved
    store = built.hub.resources.results
    started, release = threading.Event(), threading.Event()
    write = store._write
    def blocked(day, name, data, **kwargs):
        if name.endswith('.png'):
            started.set()
            assert release.wait(HANG_S)
        return write(day, name, data, **kwargs)
    monkeypatch.setattr(store, '_write', blocked)
    row = store.add('run_capability', {}, 'pending original',
                    values={'output': 'image.2d.rgb8'}, picture_bytes=[png])
    assert started.wait(HANG_S)
    ref = inputs.reference_for(built.hub.resources, row['id'], built.day.sid)
    try:
        yield built, row, ref, release
    finally:
        release.set()
        until(lambda: not store._picture_writes)


def test_retention_wait_is_bounded_and_does_not_discard_the_result(writing_result):
    built, row, _, _ = writing_result
    store = built.hub.resources.results
    with pytest.raises(ValueError, match='still being saved'):
        store.wait_for_picture(row['day'], row['pictures'][0], timeout=0)
    assert store.get(row['id']) == row
    assert (row['day'], row['pictures'][0]) in store._pending


def test_cancelling_next_tool_does_not_cancel_or_wait_for_producer_save(writing_result, monkeypatch):
    import threading
    from tests.waiting import HANG_S
    built, row, ref, release = writing_result
    store = built.hub.resources.results
    entered = threading.Event()
    wait = store.wait_for_picture
    def observed(*args, **kwargs):
        entered.set()
        return wait(*args, **kwargs)
    monkeypatch.setattr(store, 'wait_for_picture', observed)
    async def run():
        async def consume():
            async with inputs.admitted(built.hub, ref, built.day.sid):
                pytest.fail('cancelled input dispatched')
        task = asyncio.create_task(consume())
        assert await asyncio.to_thread(entered.wait, HANG_S)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, HANG_S)
        assert not release.is_set()
        assert store.get(row['id']) == row
    asyncio.run(run())


def test_failed_save_is_not_a_scientific_input_and_corrected_retry_works(saved, monkeypatch):
    from tests.waiting import until
    built, _, good_ref, png = saved
    store = built.hub.resources.results
    write = store._write
    monkeypatch.setattr(store, '_write', lambda day, name, data, **kw:
                        False if name.endswith('.png') else write(day, name, data, **kw))
    row = store.add('run_capability', {}, 'failed original',
                    values={'output': 'image.2d.rgb8'}, picture_bytes=[png])
    until(lambda: not store._picture_writes)
    ref = inputs.reference_for(built.hub.resources, row['id'], built.day.sid)
    with pytest.raises(ValueError, match='could not be saved'):
        inputs.resolve(built.hub, ref, built.day.sid)
    # Display recovery bytes remain available but cannot be mistaken for a retained input.
    assert store._pending[(row['day'], row['pictures'][0])] == png
    assert store.get(row['id']) == row
    capture, hold = inputs.resolve(built.hub, good_ref, built.day.sid)
    hold.release()
    monkeypatch.setattr(store, '_write', write)
    retry = store.add('run_capability', {}, 'retry', values={'output': 'image.2d.rgb8'}, picture_bytes=[png])
    again = inputs.reference_for(built.hub.resources, retry['id'], built.day.sid)
    capture, hold = inputs.resolve(built.hub, again, built.day.sid)
    assert capture.analysis_bytes == png
    hold.release()
    assert again['id'] != ref['id']
