"""Installing or proving a replacement must not select it for the researcher's work."""
import json

from orchestrator import env_runner, envs
from tests.test_env_runner import ADAPTER, contribute, home, install


def test_preparing_update_keeps_active_version_across_rescan(home, monkeypatch):
    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first).folder
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    assert install(home, monkeypatch)['state'] == 'done'
    env_runner.register()
    assert env_runner.in_use(candidate).folder == original
    assert 'review' in env_runner.update_waiting(candidate).lower()
    selected = env_runner.choose(candidate, None)
    assert selected.folder != original
    assert env_runner.chosen(candidate) == selected.folder.name


def test_direct_candidate_proof_preserves_legacy_active_generation(home, monkeypatch):
    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first).folder
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    envs.build(candidate)
    assert env_runner.prove(candidate)['ok']
    assert env_runner.in_use(candidate).folder == original


def test_explicit_missing_generation_never_falls_back(home, monkeypatch):
    entry = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    (envs.root(entry) / env_runner.CHOSEN).write_text(json.dumps({'generation': 'gone'}))
    assert env_runner.in_use(entry) is None
    assert 'selected version' in env_runner.ready(entry).lower()


def test_browsing_legacy_installation_does_not_write_activation(home, monkeypatch):
    entry = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    path = envs.root(entry) / env_runner.CHOSEN
    path.unlink(missing_ok=True)
    assert env_runner.in_use(entry) is not None
    assert not path.exists()


def test_remove_refuses_a_generation_with_an_owner(home, monkeypatch):
    import pytest
    from orchestrator import generation_leases

    entry = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(entry)
    with generation_leases.use(original.folder):
        with pytest.raises(envs.Failed, match='running work'):
            envs.remove(entry)
        assert original.python.exists()
    assert envs.remove(entry)


def test_promotion_finishes_owned_run_before_retiring_worker(home, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    import pytest
    from tests.test_env_runner import _picture

    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first)
    worker = env_runner.client(original)
    entered, release = threading.Event(), threading.Event()
    labels = worker._labels
    def held_labels(*args):
        entered.set()
        assert release.wait(10)
        return labels(*args)
    monkeypatch.setattr(worker, '_labels', held_labels)
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    assert install(home, monkeypatch)['state'] == 'done'
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(worker.run, _picture())
        try:
            assert entered.wait(10)
            process = worker._process
            env_runner.choose(candidate, None)
            env_runner.register()
            assert process.poll() is None, 'promotion killed an admitted generation'
            with pytest.raises(envs.Failed, match='running work'):
                envs.remove(candidate)
        finally:
            release.set()
        assert future.result(timeout=10)['count'] == 3
    assert process.poll() is not None, 'retired worker outlived its final owner'
    assert env_runner.client(env_runner.in_use(candidate)).run(_picture())['count'] == 3


def test_preparing_update_while_legacy_entry_is_off_preserves_its_version(home, monkeypatch):
    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first).folder
    env_runner.turn_off(first)
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    envs.install(candidate)
    env_runner.turn_on_again(candidate)
    assert env_runner.in_use(candidate).folder == original


def test_turn_on_uses_retained_requirements_not_an_incompatible_update(home, monkeypatch):
    from orchestrator import readiness

    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first).folder
    env_runner.turn_off(first)
    candidate = contribute(home, version='dark-spots 0.2', extra='needs_to_get = ["platform:unsupported-machine"]\n')
    state = readiness.model(candidate.key, {})
    assert state.status == 'needs-setup' and state.steps[0].on_again
    row = envs.Installs().start(candidate.key)
    assert row['onAgain'] and env_runner.in_use(candidate).folder == original
    assert envs.folder(candidate).exists() is False


def test_compatible_candidate_cannot_reenable_incompatible_retained_version(home, monkeypatch):
    import os
    import pytest
    from orchestrator import readiness

    monkeypatch.setattr(os, 'sysconf', lambda name: 16_000_000_000 if name == 'SC_PHYS_PAGES' else 1)
    first = contribute(home, extra='needs_to_get = ["memory:8"]\n')
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first).folder
    env_runner.turn_off(first)
    candidate = contribute(home, version='dark-spots 0.2')
    monkeypatch.setattr(os, 'sysconf', lambda name: 4_000_000_000 if name == 'SC_PHYS_PAGES' else 1)
    assert readiness.model(candidate.key, {}).status == 'unavailable'
    queue = envs.Installs()
    with pytest.raises(envs.Failed, match='8 GB of memory'):
        queue.start(candidate.key)
    assert queue.wire() == [] and env_runner.turned_off(candidate)
    assert original.exists() and env_runner.in_use(candidate) is None
    monkeypatch.setattr(os, 'sysconf', lambda name: 16_000_000_000 if name == 'SC_PHYS_PAGES' else 1)
    assert queue.start(candidate.key)['onAgain']
    assert env_runner.in_use(candidate).folder == original


def test_stale_client_retirement_and_final_release_stay_off_the_event_loop(home, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    import cv2
    from orchestrator import source_images
    from tests.test_env_runner import _picture

    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    worker = env_runner.client(env_runner.in_use(first))
    worker.run(_picture())
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    assert install(home, monkeypatch)['state'] == 'done'
    loop_thread = threading.get_ident()
    stopped_on = []
    stop = env_runner.Client.stop
    def observed_stop(client):
        stopped_on.append(threading.get_ident())
        return stop(client)
    monkeypatch.setattr(env_runner.Client, 'stop', observed_stop)
    encoded = cv2.imencode('.jpg', _picture())[1].tobytes()
    async def input_and_switch(capture):
        # The old generation has been admitted, but preparation yields before worker execution.
        env_runner.choose(candidate, None)
        env_runner.register()
        return encoded
    monkeypatch.setattr(source_images, 'worker_input', input_and_switch)
    impl = SimpleNamespace(id=first.id, label=first.name)
    result = asyncio.run(env_runner._run(None, SimpleNamespace(jpeg=encoded), None, impl, {}))
    assert result['version'] == first.version
    assert stopped_on and loop_thread not in stopped_on
    # A different stale idle client can also be encountered before register has run.
    env_runner.client(env_runner.generation(candidate, worker.installed.folder.name)).run(_picture())
    async def input_only(capture):
        return encoded
    monkeypatch.setattr(source_images, 'worker_input', input_only)
    stopped_on.clear()
    result = asyncio.run(env_runner._run(None, SimpleNamespace(jpeg=encoded), None, impl, {}))
    assert result['version'] == candidate.version
    assert stopped_on and loop_thread not in stopped_on


def test_deferred_retirement_uses_the_real_app_cleanup_owner(home, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from orchestrator import generation_leases
    from orchestrator.app import App
    from tests.test_env_runner import _picture

    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first)
    worker = env_runner.client(original)
    worker.run(_picture())
    process = worker._process
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    assert install(home, monkeypatch)['state'] == 'done'
    app = SimpleNamespace(_catalog_cleanup=set(), agent=SimpleNamespace(log=lambda row: None))
    async def go():
        owner = generation_leases.use(original.folder)
        owner.__enter__()
        env_runner.choose(candidate, None)
        env_runner.register(retire=lambda client: App._retire_catalog_client(app, client))
        await asyncio.to_thread(owner.__exit__, None, None, None)
        await asyncio.sleep(0)  # scheduled handoff to the app's cleanup owner
        if app._catalog_cleanup:
            await asyncio.gather(*list(app._catalog_cleanup))
    asyncio.run(go())
    assert process.poll() is not None
    assert worker not in env_runner._retiring


def test_stopped_operation_still_releases_its_generation(home, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import pytest
    from orchestrator import generation_leases, owned_process, source_images

    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first)
    stopped = False
    async def input_stops(capture):
        nonlocal stopped
        stopped = True
        raise RuntimeError('input stopped')
    monkeypatch.setattr(source_images, 'worker_input', input_stops)
    async def go():
        with owned_process.scope(stop=lambda: stopped):
            with pytest.raises(RuntimeError, match='input stopped'):
                await env_runner._run(None, None, None, SimpleNamespace(id=first.id, label=first.name), {})
    try:
        with pytest.raises(owned_process.Stopped, match='cancelled'):
            asyncio.run(go())
        with generation_leases.withdraw(original.folder):
            pass
    finally:
        # Test cleanup only: a broken implementation must not contaminate later test lifecycles.
        generation_leases._owners.pop(original.folder.resolve(), None)


def test_promoted_generation_is_explicit_even_when_receipt_times_tie(home, monkeypatch):
    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    assert install(home, monkeypatch)['state'] == 'done'
    old = env_runner.in_use(candidate).folder
    new = envs.folder(candidate)
    rows = envs.generations(candidate)
    monkeypatch.setattr(envs, 'generations', lambda *args: sorted(rows, key=lambda row: row[0] != old))
    versions = env_runner.versions(candidate)
    assert versions[0]['generation'] == new.name
    assert not versions[0]['inUse']


def test_cancelled_promoted_run_still_reaps_through_real_app_owner(home, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    import pytest
    from orchestrator import generation_leases, owned_process
    from orchestrator.app import App
    from tests.test_env_runner import _picture

    first = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(first)
    worker = env_runner.client(original)
    worker.run(_picture())
    process = worker._process
    candidate = contribute(home, ADAPTER.replace('< 128', '< 129'), version='dark-spots 0.2')
    assert install(home, monkeypatch)['state'] == 'done'
    errors = []
    app = SimpleNamespace(_catalog_cleanup=set(), agent=SimpleNamespace(log=errors.append))
    async def go():
        stopped = False
        with pytest.raises(owned_process.Stopped):
            with owned_process.scope(stop=lambda: stopped):
                owner = generation_leases.use(original.folder)
                owner.__enter__()
                env_runner.choose(candidate, None)
                env_runner.register(retire=lambda client: App._retire_catalog_client(app, client))
                stopped = True
                await generation_leases.release(owner)
        await asyncio.sleep(0)
        if app._catalog_cleanup:
            await asyncio.gather(*list(app._catalog_cleanup))
    try:
        asyncio.run(go())
        assert not errors, errors
        assert process.poll() is not None
    finally:
        worker.stop()


def test_catalog_wire_keeps_single_proven_recovery_candidate(home, monkeypatch):
    from orchestrator import catalog_view

    entry = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    (envs.root(entry) / env_runner.CHOSEN).write_text(json.dumps({'generation': 'gone'}))
    row = next(row for row in catalog_view.rows([], {}, {}) if row['id'] == entry.key)
    assert len(row['versions']) == 1
    assert row['versions'][0]['generation'] == envs.folder(entry).name
    assert not row['versions'][0]['inUse']


def test_missing_source_lock_does_not_hide_proven_versions(home, monkeypatch):
    from pathlib import Path
    entry = contribute(home)
    assert install(home, monkeypatch)['state'] == 'done'
    original = env_runner.in_use(entry).folder
    (Path(entry.folder) / 'env.lock').unlink()
    assert env_runner.versions(entry)[0]['generation'] == original.name
