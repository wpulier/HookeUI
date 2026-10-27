"""Explicit dependency preparation is neither operation activation nor qualification."""
import threading

import pytest

from gpu_worker import manifests as catalog
from orchestrator import catalog_view, channels, env_runner, envs, own_trials, readiness, share
from tests.test_channels import home  # noqa: F401
from tests.test_bundle_runtime import contribute
from tests.test_bundle_share import private_host
from tests.test_env_runner import standin_uv
from tests.waiting import until


def completed(queue):
    until(lambda: queue.wire() and queue.wire()[0]['state'] in ('done', 'failed', 'cancelled'))
    return queue.wire()[0]


def test_explicit_preparation_uses_the_install_queue_without_activating(home, monkeypatch):
    owner = private_host(contribute(home))
    standin_uv(home, monkeypatch)
    queue = envs.Installs()
    try:
        queue.start(owner.key, prepare_only=True)
        row = completed(queue)
        assert row['state'] == 'done', row
        assert row['prepareOnly'] and 'not qualified' in row['says']
        assert envs.state(owner)['state'] == 'ready'
        assert env_runner.in_use(owner) is None
        assert readiness.model(owner.key, {}).status == 'needs-setup'
        assert readiness.model(owner.key, {}).next.kind == 'workflow'
        assert own_trials.pending(owner)
        card = next(row for row in catalog_view.rows({}, {}, {}) if row['id'] == owner.key)
        assert card['status'] == 'needs-setup'
        assert any(fact.get('prepare') == owner.key and fact['ok'] is None for fact in card['facts'])
        assert not any(fact.get('install') for fact in card['facts'])
    finally:
        queue.stop()


def test_imported_candidate_needs_explicit_permission_before_preparation(home, tmp_path, monkeypatch):
    owner = private_host(contribute(home))
    path = tmp_path / 'candidate.observationui.zip'
    path.write_bytes(share.pack(owner))
    reviewed = tmp_path / 'recipient-reviewed'
    reviewed.mkdir()
    monkeypatch.setattr(catalog, 'ROOT', reviewed)
    catalog.manifests.cache_clear()
    imported = share.open_file(path)
    candidate = catalog.get(imported['model'])
    assert candidate is not None, catalog.problems()
    monkeypatch.setattr(envs, 'build', lambda *a, **k: pytest.fail('untrusted preparation cannot start'))
    with pytest.raises(envs.Failed, match='allow code'):
        envs.Installs().start(candidate.key, prepare_only=True)
    with pytest.raises(envs.Failed, match='Allow code'):
        envs.prepare_candidate(candidate)
    facts = catalog_view.facts(candidate, {}, lambda key: False, [], {})
    assert not any(fact.get('prepare') for fact in facts)
    assert not envs.root(candidate).exists()


def test_cancelled_preparation_is_retryable_and_ordinary_install_cannot_activate(home, monkeypatch):
    owner = private_host(contribute(home))
    standin_uv(home, monkeypatch)
    entered = threading.Event()
    original = envs.prepare_candidate
    def wait_for_stop(m, environ, say, stop):
        entered.set()
        until(stop)
        raise envs.Failed('run cancelled')
    monkeypatch.setattr(envs, 'prepare_candidate', wait_for_stop)
    queue = envs.Installs()
    try:
        queue.start(owner.key, prepare_only=True)
        until(entered.is_set)
        queue.cancel(owner.key)
        row = completed(queue)
        assert row['state'] == 'cancelled' and "wasn't prepared" in row['says']
        assert env_runner.in_use(owner) is None
        monkeypatch.setattr(envs, 'prepare_candidate', original)
        queue.start(owner.key, prepare_only=True)
        assert completed(queue)['state'] == 'done'
        queue.start(owner.key)
        assert completed(queue)['state'] == 'done'
        assert env_runner.in_use(owner) is None
        assert own_trials.pending(owner)
        assert readiness.model(owner.key, {}).next.kind == 'workflow'
    finally:
        queue.stop()


def test_preparation_does_not_bypass_ordinary_operation_proof(home):
    owner = contribute(home)
    with pytest.raises(envs.Failed, match='scientific integration candidate'):
        envs.Installs().start(owner.key, prepare_only=True)
    assert not envs.root(owner).exists()
