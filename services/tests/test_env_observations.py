"""Synthetic disk receipts through the real display/resolver; no models, keys, installs or running servers."""
import asyncio
import json
import os
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import env_observations as observations, env_runner, envs, readiness
from tests.test_env_runner import home, contribute  # noqa: F401


def installed(m):
    d = envs.folder(m)
    (d / 'bin').mkdir(parents=True)
    (d / 'bin' / 'python').touch()
    (d / envs.RECEIPT).write_text(json.dumps({'at': '2026-10-16', 'python': '3.13', 'packages': 1}))
    shutil.copytree(m.folder, d / envs.SNAPSHOT / m.id)
    env_runner.proof_path(m).write_text(json.dumps({'ok': True, 'found': 3}))
    return d


def test_update_display_uses_observed_maintenance_records(home, monkeypatch):
    from orchestrator.maintenance import store
    m = contribute(home)
    active = installed(m)
    env_runner.choose(m, active.name)
    candidate = contribute(home, version='dark-spots 0.2')
    snapshot = observations.collect()
    root = store.root()
    stat = Path.stat
    def no_maintenance_stat(path, *args, **kwargs):
        if path.is_relative_to(root):
            pytest.fail(f'synchronous maintenance display read: {path.name}')
        return stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'stat', no_maintenance_stat)
    with observations.display(snapshot):
        assert 'Update available' in env_runner.update_waiting(candidate)


@pytest.mark.parametrize('name', ['policies.json', 'checks.json', 'pins.json', 'attempts/mine--spots.json'])
def test_maintenance_observation_keeps_snapshot_but_live_reads_and_recollection_change(home, name):
    from orchestrator.maintenance import store
    path = store.path(name)
    store.write(path, {'version': 1})
    snapshot = store.display_records(['mine/spots'])
    store.write(path, {'version': 2})
    with observations.display(snapshot), store.display_reads():
        assert store.read(path, {}) == {'version': 1}
        with observations.live():
            assert store.read(path, {}) == {'version': 2}
    assert store.read(path, {}) == {'version': 2}
    path.unlink()
    with observations.display(store.display_records(['mine/spots'])), store.display_reads():
        assert store.read(path, {}) == {}


def test_maintenance_decisions_snapshot_and_profiles_are_independent(home):
    from orchestrator.maintenance import store
    path = store.path('decisions.jsonl')
    store.append(path, {'action': 'before'})
    snapshot = store.display_records([])
    store.append(path, {'action': 'after'})
    other = {'MICROMIND_CATALOG': str(home / 'other-catalog')}
    store.write(store.path('policies.json', other), {'separate': True})
    with observations.display(snapshot), store.display_reads():
        assert store.lines(path) == [{'action': 'before'}]
        assert store.read(store.path('policies.json', other), {}) == {'separate': True}
        assert store.read(store.path('policies.json'), {}) == {}
    assert store.lines(path) == [{'action': 'before'}, {'action': 'after'}]


def test_maintenance_mutations_inside_display_read_live_state(home):
    from orchestrator.maintenance import policy, store
    m = contribute(home)
    installed(m)
    policy.record(m, 'review', by='you', how='screen')
    snapshot = observations.collect()
    path = store.path('policies.json')
    live = store.read(path, {})
    live['other-entry'] = {'policy': 'pin', 'generation': 'preserve-me'}
    store.write(path, live)
    with observations.display(snapshot), store.display_reads():
        assert policy.get(m.key)['policy'] == 'review'
        policy.record(m, 'manual', by='you', how='screen')
        with store.locked(), store.locked():
            assert policy.get(m.key)['policy'] == 'manual'
            assert policy.get('other-entry')['generation'] == 'preserve-me'
        assert policy.get(m.key)['policy'] == 'review', 'display remains the prior observation until refreshed'
    assert policy.get(m.key)['policy'] == 'manual'
    assert policy.get('other-entry')['generation'] == 'preserve-me'


def test_status_projection_is_observed_without_changing_ordinary_policy_reads(home):
    from orchestrator.maintenance import policy, status
    m = contribute(home)
    installed(m)
    policy.record(m, 'review', by='you', how='screen')
    snapshot = observations.collect()
    policy.record(m, 'manual', by='you', how='screen')
    with observations.display(snapshot):
        assert status.of(m)['policy']['policy'] == 'review'
        assert policy.get(m.key)['policy'] == 'manual'
    assert status.of(m)['policy']['policy'] == 'manual'


def test_new_pins_are_live_for_removal_authority_and_mutation(home):
    from orchestrator.maintenance import pins
    m = contribute(home)
    active = installed(m)
    snapshot = observations.collect()
    pins.add(m.key, active.name, 'recipe', 'saved-workflow')
    with observations.display(snapshot):
        assert pins.of(m.key)[0]['owner'] == 'saved-workflow', 'ordinary reads remain live even inside display'
        with pytest.raises(pins.Held, match='saved-workflow'):
            pins.require_removable(m.key, {active.name})
        assert pins.remove(m.key, 'recipe', 'saved-workflow') == 1
    assert pins.of(m.key) == []


def test_runtime_removal_sees_new_active_attempt_inside_old_display(home, monkeypatch):
    from orchestrator.maintenance import prepare, store
    m = contribute(home)
    active = installed(m)
    snapshot = observations.collect()
    store.write(prepare.path(m.key), {'state': 'applying', 'pid': 123})
    monkeypatch.setattr(prepare, 'alive', lambda attempt: attempt.get('pid') == 123)
    with observations.display(snapshot):
        with pytest.raises(envs.Failed, match='update is being prepared'):
            envs.removal(m)
    assert active.exists()


def test_apply_precheck_sees_new_active_attempt_inside_old_display(home):
    from orchestrator.maintenance import prepare, promote, store
    m = contribute(home)
    active = installed(m)
    snapshot = observations.collect()
    store.write(prepare.path(m.key), {'state': 'applying'})
    with observations.display(snapshot):
        with pytest.raises(promote.Failed, match='previous update'):
            prepare.apply(m, active.name)
    assert prepare.last(m.key)['state'] == 'applying'


def test_explicit_selection_releases_pin_recorded_after_display_snapshot(home):
    from orchestrator.maintenance import policy, pins
    m = contribute(home)
    original = installed(m)
    newer = contribute(home, version='dark-spots 0.2')
    replacement = installed(newer)
    env_runner.choose(newer, original.name)
    snapshot = observations.collect()
    policy.record(newer, 'pin', generation=original.name, by='you', how='screen')
    with observations.display(snapshot):
        env_runner.choose(newer, replacement.name)
    assert env_runner.selected(newer).folder == replacement
    assert policy.get(newer.key)['policy'] == 'manual'
    assert not [p for p in pins.of(newer.key) if p['kind'] == 'user']


def test_update_check_persistence_does_not_borrow_display_backoff(home):
    from orchestrator.maintenance import discover, store
    m = contribute(home)
    snapshot = observations.collect()
    store.write(store.path('checks.json'), {m.key: {'failures': 4, 'failingSince': 'original'}})
    with observations.display(snapshot):
        result = discover._save(m.key, {'ok': False, 'at': 'later'}, dict(os.environ))
    assert result['failures'] == 5
    assert result['failingSince'] == 'original'


def test_versions_enumerates_once_and_display_reuses_facts(home, monkeypatch):
    m = contribute(home)
    d = installed(m)
    real = envs.generations
    calls = []
    def count(*args, **kwargs):
        calls.append(args[0].key)
        return real(*args, **kwargs)
    monkeypatch.setattr(envs, 'generations', count)
    assert env_runner.versions(m)[0]['generation'] == d.name
    assert calls == [m.key]
    snapshot = observations.collect()
    calls.clear()
    with observations.display(snapshot), readiness.batch():
        for _ in range(3):
            assert env_runner.in_use(m).folder == d
            assert env_runner.versions(m)[0]['inUse']
            assert env_runner.ready(m) == ''
            assert envs.state(m)['state'] == 'ready'
    assert calls == []


def test_old_selection_failed_candidate_and_malformed_selection(home):
    m = contribute(home)
    d = installed(m)
    env_runner.choose(m, d.name)
    newer = contribute(home, version='dark-spots 0.2')
    candidate = installed(newer)
    env_runner.proof_path(newer).write_text(json.dumps({'ok': False, 'why': 'synthetic failure'}))
    with observations.display(observations.collect()):
        assert env_runner.in_use(newer).folder == d
        assert not env_runner.in_use(newer).current
        assert env_runner.ready(newer) == ''
        assert 'synthetic failure' in env_runner.update_failed(newer)
        assert not env_runner.newest_installed(newer)
        assert [r['generation'] for r in env_runner.versions(newer)] == [d.name]
    (envs.root(newer) / env_runner.CHOSEN).write_text('malformed')
    with observations.display(observations.collect()):
        assert env_runner.in_use(newer) is None
        assert 'selected version is unavailable' in env_runner.ready(newer)
    assert candidate != d


def test_damaged_candidate_marker_does_not_hide_retained_selection(home):
    m = contribute(home)
    active = installed(m)
    env_runner.choose(m, active.name)
    candidate = contribute(home, version='dark-spots 0.2')
    envs._marker(envs.folder(candidate), envs.FAILED).write_text('broken json')
    with observations.display(observations.collect()):
        assert env_runner.in_use(candidate).folder == active
        assert env_runner.ready(candidate) == ''
        assert envs.state(candidate)['state'] == 'failed'


def test_reactivation_uses_live_selection_and_live_enablement_readback(home):
    first = contribute(home)
    original = installed(first)
    env_runner.choose(first, original.name)
    candidate = contribute(home, version='dark-spots 0.2', extra='needs_to_get = ["memory:1000000000"]\n')
    replacement = installed(candidate)  # synthetic receipt: no claim this runtime works on this machine
    env_runner.turn_off(candidate)
    stale = observations.collect()
    env_runner.choose(candidate, replacement.name)
    queue = envs.Installs()
    with observations.display(stale):
        assert env_runner.selected(candidate).folder == original
        with pytest.raises(envs.Failed, match='GB of memory'):
            queue.start(candidate.key)
    assert env_runner.turned_off(candidate) and queue.wire() == []
    env_runner.choose(candidate, original.name)
    with observations.display(stale):
        assert queue.start(candidate.key)['onAgain']
        assert queue.wire()[0]['state'] == 'done', 'cached off=True must not trigger candidate preparation'
    assert env_runner.in_use(candidate).folder == original


def test_one_unreadable_entry_does_not_freeze_other_display_observations(home, monkeypatch):
    m = contribute(home)
    active = installed(m)
    observe = observations.observe
    def damaged(entry, environ):
        if entry.key != m.key:
            raise OSError('controlled unreadable entry')
        return observe(entry, environ)
    monkeypatch.setattr(observations, 'observe', damaged)
    with observations.display(observations.collect()):
        assert env_runner.in_use(m).folder == active


@pytest.mark.parametrize('removed', ['python', 'proof', 'off', 'lock'])
def test_refresh_observes_changes_outside_receipt_signatures(home, removed):
    m = contribute(home)
    d = installed(m)
    snapshot = observations.collect()
    if removed == 'python':
        (d / 'bin' / 'python').unlink()
    elif removed == 'proof':
        env_runner.proof_path(m).unlink()
    elif removed == 'off':
        env_runner.turn_off(m)
    else:
        envs.lock_file(m).unlink()
    with observations.display(snapshot):
        assert env_runner.in_use(m) is not None, 'the retained observation is intentionally stale'
    with observations.display(observations.collect()):
        found = env_runner.in_use(m)
        if removed == 'lock':
            assert found is not None and not found.current, 'the retained proven snapshot remains usable'
        else:
            assert found is None


def test_exact_execution_validation_bypasses_display_and_expired_context(home):
    m = contribute(home)
    d = installed(m)
    snapshot = observations.collect()
    async def scenario():
        async def inherited():
            assert env_runner.in_use(m) is None
            assert readiness._once(('probe',), lambda: 'fresh') == 'fresh'
        with observations.display(snapshot), readiness.batch():
            assert env_runner.in_use(m) is not None
            assert readiness._once(('probe',), lambda: 'stale') == 'stale'
            env_runner.proof_path(m).unlink()
            assert env_runner.generation(m, d.name) is None
            # Even an execution explicitly started inside display cannot use its stale approval.
            from orchestrator.operations import Unavailable
            with pytest.raises(Unavailable):
                await env_runner._run(None, None, None, SimpleNamespace(id=m.id, label=m.name), {})
            child = asyncio.create_task(inherited())
        await child
    asyncio.run(scenario())


def test_app_wire_uses_off_loop_observations_and_live_settings(home, monkeypatch):
    from tests.test_app import app
    m = contribute(home)
    d = installed(m)
    built = app(home / 'app')
    async def scenario():
        loop_thread = threading.get_ident()
        from pathlib import Path
        real = Path.iterdir
        env_root = envs.root(m).parent
        def checked(path):
            if path.parent == env_root:
                assert threading.get_ident() != loop_thread, 'generation directory scanned on event loop'
            return real(path)
        monkeypatch.setattr(Path, 'iterdir', checked)
        digest = envs.entry_digest
        def checked_digest(*args, **kwargs):
            assert threading.get_ident() != loop_thread, 'candidate entry hashed on event loop'
            return digest(*args, **kwargs)
        monkeypatch.setattr(envs, 'entry_digest', checked_digest)
        await built.start_environment_observations()
        try:
            wire = built.capabilities_wire(counts=False)
            row = next(r for r in wire['models'] if r['id'] == m.key)
            assert row['env']['on'] and row['versions'][0]['generation'] == d.name
            built.hub.settings.quieter = True
            built.publish_settings()
            assert built.settings_wire()['settings']['quieter'] is True
        finally:
            await built._environment_observer.close()
    asyncio.run(scenario())


def test_refresh_coalesces_superseded_reads_and_uses_current_app_settings(home, monkeypatch):
    from tests.test_app import app
    built = app(home / 'app')
    started, release = threading.Event(), threading.Event()
    calls, published = [], []
    def collect(environ):
        calls.append(len(calls) + 1)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        return {'revision': len(calls)}
    monkeypatch.setattr(observations, 'collect', collect)
    async def scenario():
        observer = observations.Observer(lambda: published.append((observer.snapshot, built.hub.settings.quieter)),
                                         lambda exc: pytest.fail(str(exc)))
        first = observer.request()
        assert await asyncio.to_thread(started.wait, 5)
        built.hub.settings.quieter = True
        assert observer.request() is first
        assert observer.request() is first
        release.set()
        await first
        assert calls == [1, 2]
        assert published == [({'revision': 2}, True)]
        assert observer.published == observer.revision == 3
        await observer.close()
        assert observer.request() is None
    try:
        asyncio.run(scenario())
    finally:
        release.set()


def test_display_does_not_freeze_trust_or_choose_the_observed_candidate(home, monkeypatch):
    from orchestrator import channels
    m = contribute(home)
    d = installed(m)
    snapshot = observations.collect()
    allowed = True
    monkeypatch.setattr(channels, 'trusted', lambda *args: allowed)
    with observations.display(snapshot):
        assert env_runner.in_use(m) is not None
        allowed = False
        assert env_runner.in_use(m) is None
        assert env_runner.versions(m) == []
        allowed = True
        # Same manifest, changed adapter: Use newest must hash current code, not the display's old candidate.
        (envs.lock_file(m).parent / 'adapter.py').write_text('changed code')
        with pytest.raises(env_runner.Failed):
            env_runner.choose(m, None)
        assert env_runner.generation(m, d.name) is not None, 'retained exact generation remains valid'


def test_shutdown_joins_pending_read_and_never_publishes_it(monkeypatch):
    started, release, finished = threading.Event(), threading.Event(), threading.Event()
    published = []
    def collect(environ):
        started.set()
        assert release.wait(5)
        finished.set()
        return {'done': True}
    monkeypatch.setattr(observations, 'collect', collect)
    async def scenario():
        observer = observations.Observer(lambda: published.append(True), lambda exc: pytest.fail(str(exc)))
        observer.request()
        assert await asyncio.to_thread(started.wait, 5)
        closing = asyncio.create_task(observer.close())
        await asyncio.sleep(0)
        assert observer.closed and not closing.done()
        release.set()
        await closing
        assert finished.is_set() and not published
    try:
        asyncio.run(scenario())
    finally:
        release.set()
