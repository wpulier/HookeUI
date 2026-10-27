"""Production result writer and public index; synthetic pixels, controlled disk failure."""
import asyncio
import copy
import threading
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ToolContext, attach
from tests.test_resources import app_at, jpeg, run_tool
from tests.waiting import HANG_S, until, until_async


def test_finished_receipt_is_stable_even_when_observation_takes_time(tmp_path, monkeypatch):
    from orchestrator.brain import registry

    app = app_at(tmp_path).state.app
    clock = [0.0]
    monkeypatch.setattr(registry, 'time', SimpleNamespace(perf_counter=lambda: clock[0]))
    observed = []
    async def produce(ctx, args):
        attach({'output': 'image.2d.rgb8'}, picture_bytes=jpeg())
        clock[0] += 0.1
        return 'Produced a test image'
    def observe(run, ctx):
        observed.append(copy.deepcopy(run.wire(full=True)))
        clock[0] += 10  # Controlled observer duration, not a wall-clock assertion or a sleeping worker.
    app.brain.registry.finished.append(observe)
    run = run_tool(app, 'run_capability', produce)
    assert run.ok and app.hub.resources.picture(run.resource['id'])[0] == jpeg()
    assert observed == [run.wire(full=True)]


@pytest.mark.parametrize('failure', ['original', 'metadata', 'journal'])
def test_failed_original_save_cannot_be_reported_as_retained_success(tmp_path, monkeypatch, failure):
    app = app_at(tmp_path).state.app
    store = app.hub.resources.results
    original = store._write
    async def produce(ctx, args):
        attach({'output': 'image.2d.rgb8'}, picture_bytes=jpeg())
        return 'Produced a test image'

    good = run_tool(app, 'run_capability', produce)
    assert good.ok
    before = app.hub.resources.picture(good.resource['id'])[0]
    with monkeypatch.context() as failed:
        if failure == 'journal':
            append = app.day.journal.append
            def refusing(event):
                if event.get('type') == 'result':
                    raise OSError('controlled result-journal failure')
                return append(event)
            failed.setattr(app.day.journal, 'append', refusing)
        else:
            suffix = '.jpg' if failure == 'original' else '.json'
            failed.setattr(store, '_write', lambda day, name, data, **kw:
                           False if name.endswith(suffix) else original(day, name, data, **kw))
        run = run_tool(app, 'run_capability', produce)
    until(lambda: not store._picture_writes)
    assert run.ok is False, 'a failed original write was reported as a successful retained result'
    assert run.resource['kind'] == 'Result not saved'
    assert 'storage' in str(run.next).lower()
    entry = app.hub.resources.get(run.resource['id'])
    assert entry.state == 'failed'
    assert 'sav' in entry.summary.lower()
    assert app.hub.resources.picture(good.resource['id'])[0] == before
    retry = run_tool(app, 'run_capability', produce)
    assert retry.ok and retry.resource['id'] != run.resource['id']
    reopened = app_at(tmp_path).state.app
    assert reopened.hub.resources.picture(retry.resource['id'])[0] == before
    assert reopened.hub.resources.picture(good.resource['id'])[0] == before
    if failure != 'journal':
        assert reopened.hub.resources.get(entry.id).state == 'failed'


@pytest.fixture
def blocked_save(tmp_path, monkeypatch):
    app = app_at(tmp_path).state.app
    store = app.hub.resources.results
    entered, release = threading.Event(), threading.Event()
    original = store._write
    def blocked(day, name, data, **kwargs):
        if name.endswith('.jpg'):
            entered.set()
            assert release.wait(HANG_S)
        return original(day, name, data, **kwargs)
    monkeypatch.setattr(store, '_write', blocked)
    async def produce(ctx, args):
        attach({'output': 'image.2d.rgb8'}, picture_bytes=jpeg())
        return 'Produced a test image'
    monkeypatch.setattr(app.brain.registry.tools['run_capability'], 'handler', produce)
    yield app, store, entered, release
    release.set()
    until(lambda: not store._picture_writes)


def begin(app):
    ctx = ToolContext('system', 1, None, 'Produce a test image', asked=app.day.here())
    return app.brain.registry.start('run_capability', {}, ctx)


def test_completion_and_saved_hooks_wait_for_exact_original(blocked_save):
    app, store, entered, release = blocked_save
    hooks = []
    store.on_saved.append(lambda entry: hooks.append(entry['id']))
    async def exercise():
        run = begin(app)
        try:
            await until_async(entered.is_set)
            await until_async(lambda: bool(run.resource))
            assert not run.task.done() and run.ok is None and not hooks
            assert app.hub.resources.get(run.resource['id']).state == 'saving'
            # Another result and the event loop remain available during the write.
            unrelated = store.add('count', {}, 'A separate measurement', values={'count': 3})
            assert app.hub.resources.get(unrelated['id']).state == 'done'
            assert hooks == [unrelated['id']]
        finally:
            release.set()
        await run.task
        assert run.ok and hooks == [unrelated['id'], run.resource['id']]
        assert app.hub.resources.get(run.resource['id']).state == 'done'
        assert app.hub.resources.picture(run.resource['id'])[0] == jpeg()
    asyncio.run(exercise())


def test_cancellation_preserves_owned_save_without_reporting_success(blocked_save):
    app, store, entered, release = blocked_save
    async def exercise():
        run = begin(app)
        await until_async(entered.is_set)
        await until_async(lambda: bool(run.resource))
        run.task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run.task
        assert run.ok is False and not run.picture_bytes
        assert store._picture_writes and not release.is_set()
        assert app.hub.resources.get(run.resource['id']).state == 'failed'
        assert app.hub.resources.picture(run.resource['id'])[0] == jpeg()
        release.set()
        await until_async(lambda: not store._picture_writes)
        assert app.hub.resources.picture(run.resource['id'])[0] == jpeg()
    asyncio.run(exercise())


def test_save_deadline_reports_failure_but_does_not_discard_producing_bytes(blocked_save, monkeypatch):
    from orchestrator import resources
    app, store, entered, release = blocked_save
    monkeypatch.setattr(resources, 'RESULT_SAVE_S', 0)
    async def exercise():
        run = begin(app)
        await run.task
        assert run.ok is False and 'storage' in run.next
        assert app.hub.resources.get(run.resource['id']).state == 'failed'
        assert app.hub.resources.picture(run.resource['id'])[0] == jpeg()
        assert not release.is_set()
        release.set()
        await until_async(lambda: not store._picture_writes)
    asyncio.run(exercise())


def test_save_completion_stays_in_its_initiating_project(blocked_save):
    app, store, entered, release = blocked_save
    original_session = app.day.sid
    async def exercise():
        run = begin(app)
        try:
            await until_async(entered.is_set)
            await until_async(lambda: bool(run.resource))
            app.new_session('Another isolated Project')
            assert app.day.sid != original_session
        finally:
            release.set()
        await run.task
        assert run.ok and run.resource['day'] == original_session
        assert not app.hub.resources.list(kind='result')
        assert app.hub.resources.get(run.resource['id'], original_session).state == 'done'
        assert app.hub.resources.picture(run.resource['id'], day=original_session)[0] == jpeg()
    asyncio.run(exercise())


def test_interrupted_save_is_reconstructed_from_files_without_rerunning(tmp_path, monkeypatch):
    app = app_at(tmp_path).state.app
    store = app.hub.resources.results
    row = store.add('run_capability', {}, 'Produced a test image',
                    values={'output': 'image.2d.rgb8'}, picture_bytes=[jpeg()], retaining=True)
    until(lambda: not store._picture_writes)
    assert row['state'] == 'saving'  # no completion listener ran before the simulated restart
    reopened = app_at(tmp_path).state.app
    assert reopened.hub.resources.get(row['id']).state == 'done'
    assert reopened.hub.resources.picture(row['id'])[0] == jpeg()
    (store.root / row['day'] / row['pictures'][0]).unlink()
    missing = app_at(tmp_path).state.app.hub.resources.get(row['id'])
    assert missing.state == 'failed' and 'storage' in missing.summary
