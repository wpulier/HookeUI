"""Declared execution budgets; synthetic candidates, no model footprint claim."""
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from gpu_worker import bundle_contract as contract
from orchestrator import catalog_view, envs, scientific_host
from tests.test_bundle_contract import declaration
from tests.test_bundle_manifest import HEAD, write
from tests.test_native_regions import source
from tests.test_channels import home  # noqa: F401


def member(resources=None):
    raw = declaration(bridge='scientific-host-v1',
        inputs=[{'id': 'project', 'type': 'project.scientific'}],
        outputs=[{'id': 'result', 'type': 'project.scientific'}])
    if resources is not None:
        raw['resources'] = resources
    return raw


def parse(resources=None):
    owner = SimpleNamespace(key='model-tool', runner='python-env', requires={'catalog': 3})
    parsed, = contract.parse_members([member(resources)], owner)
    return parsed


@pytest.mark.parametrize('resources', [
    {}, {'format': 2, 'memory_mb': 4096}, {'format': True, 'memory_mb': 4096},
    {'format': 1, 'memory_mb': True}, {'format': 1, 'memory_mb': 1023},
    {'format': 1, 'memory_mb': 8193}, {'format': 1, 'memory_mb': 4096.0},
    {'format': 1, 'memory_mb': 4096, 'processes': 2},
])
def test_unsupported_resource_declarations_refuse(resources):
    with pytest.raises(ValueError):
        parse(resources)


def test_declared_budget_is_detached_and_default_unchanged():
    parsed = parse({'format': 1, 'memory_mb': 4096})
    parsed.resources['memory_mb'] = 8192
    assert parsed.resources == {'format': 1, 'memory_mb': 4096}
    assert parse().resources == {}


def test_old_client_rejects_new_field(monkeypatch):
    monkeypatch.setattr(contract, 'FIELDS', contract.FIELDS - {'resources'})
    with pytest.raises(ValueError):
        parse({'format': 1, 'memory_mb': 4096})


def test_budget_changes_runtime_identity(tmp_path):
    text = HEAD + '''
[[members]]
id = "viewer"
name = "Scientific viewer"
adapter = "adapter:open_host"
bridge = "scientific-host-v1"
inputs = [{id = "project", type = "project.scientific"}]
outputs = [{id = "result", type = "project.scientific"}]
resources = {format = 1, memory_mb = 4096}
'''
    owner, errors = write(tmp_path, text)
    assert owner is not None, errors
    before = envs.entry_digest(owner)
    changed = replace(owner, members=({**owner.members[0], 'resources': {'format': 1, 'memory_mb': 2048}},))
    assert envs.entry_digest(changed) != before


def test_package_setup_shows_declared_budget_without_readiness_claim(home):
    from gpu_worker import manifests as catalog
    from tests.test_bundle_runtime import contribute
    from tests.test_bundle_share import private_host
    owner = private_host(contribute(home))
    path = catalog.entry_folder(owner) / 'model.toml'
    path.write_text(path.read_text() + '\nresources = {format = 1, memory_mb = 4096}\n')
    catalog.manifests.cache_clear()
    owner = catalog.get(owner.key)
    assert owner is not None, catalog.problems()
    facts = catalog_view.facts(owner, {}, lambda key: False, [])
    assert any(fact['kind'] == 'resources' and '4 GiB' in fact['says'] and fact['ok'] is None for fact in facts)
    assert not any('memory' in item.get('title', '').lower() for item in catalog_view.abilities(owner, []))


def test_ordinary_operation_cannot_request_scientific_memory():
    owner = SimpleNamespace(key='model-tool', runner='python-env', requires={'catalog': 3})
    with pytest.raises(ValueError, match='scientific-host-v1'):
        contract.parse_members([declaration(resources={'format': 1, 'memory_mb': 4096})], owner)


@pytest.mark.parametrize('memory_mb', [None, 4096])
@pytest.mark.parametrize('denied_reservation', [None, 1, 2])
def test_review_reserves_declared_memory_before_launch_and_releases(tmp_path, monkeypatch, memory_mb, denied_reservation):
    from tests.test_prepared_artifacts import artifact
    from orchestrator.scientific_arrays import SAVE_WORKING_BYTES
    manifest, environ, _ = artifact(tmp_path)
    parsed = parse(None if memory_mb is None else {'format': 1, 'memory_mb': memory_mb})
    manifest = replace(manifest, files=())
    prepared = SimpleNamespace(m=contract.view(manifest, parsed))
    @contextmanager
    def prepared_member(*args):
        yield prepared
    reserved, launched, released = [], [], []
    @contextmanager
    def reserve(amount):
        reserved.append(amount)
        if len(reserved) == denied_reservation:
            raise scientific_host.jobs.Failed('not enough free memory')
        try:
            yield
        finally:
            released.append(amount)
    def start(worker):
        launched.append(worker.limits)
        assert reserved == [(memory_mb or 1024) + 32, SAVE_WORKING_BYTES / (1 << 20)]
        assert not released
        with worker.save_workspace.claim() as active:
            assert active
        assert worker.limits.memory_bytes == (memory_mb or 1024) << 20
        assert worker.limits.processes == 1
        assert worker.limits.work_bytes == 64 << 20
        raise OSError('synthetic startup failure')
    monkeypatch.setattr(scientific_host.sandbox, 'available', lambda: True)
    monkeypatch.setattr(scientific_host.prepared_bundles, 'prepared_member', prepared_member)
    monkeypatch.setattr(scientific_host, '_bootstrap', lambda root: None)
    monkeypatch.setattr(scientific_host.jobs.MEMORY, 'reserve', reserve)
    monkeypatch.setattr(scientific_host.ReviewWorker, 'start', start)
    with pytest.raises(scientific_host.jobs.Failed if denied_reservation else OSError):
        with scientific_host.review('model-tool#measure', 'generation', {'volume': source(np.ones((2,3),dtype='<u2'),[])}, environ):
            pytest.fail('failed startup admitted')
    expected = [(memory_mb or 1024) + 32, SAVE_WORKING_BYTES / (1 << 20)]
    assert reserved == expected[:denied_reservation]
    assert bool(launched) is (denied_reservation is None)
    acquired = expected if denied_reservation is None else expected[:denied_reservation - 1]
    assert released == list(reversed(acquired))
