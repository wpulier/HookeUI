"""Synthetic Fiji receipts through real display consumers; never actual runtime qualification."""
import asyncio
import json
import threading
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_observations, fiji_bindings, fiji_generations, fiji_runner
from tests.test_fiji_contributions import contribution  # noqa: F401


def prepare(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    m = catalog.get('fiji-gaussian-blur')
    base = fiji_generations.root()
    (base / '1').mkdir(parents=True)
    (base / 'current').write_text('1')
    (base / '1' / fiji_generations.RECEIPT).write_text(json.dumps({
        'proofs': {'smoke': 'synthetic', m.key: {**fiji_runner.identity(m), 'passed': True},
                   fiji_bindings.BINDING: {**fiji_bindings.proof_identity(), 'passed': True}},
        'sites': [], 'at': 'synthetic'}))
    return m, base


def test_settings_publication_never_reads_reviewed_fiji_files_on_loop(tmp_path, monkeypatch):
    from tests.test_app import app
    m, base = prepare(tmp_path, monkeypatch)
    built = app(tmp_path / 'app')
    async def scenario():
        loop_thread = threading.get_ident()
        original = Path.open
        def checked(path, *args, **kwargs):
            if (path.is_relative_to(base) or path.parent == Path(m.folder)
                    or path in (fiji_runner.FUNCTIONS, fiji_bindings.SERVER, fiji_bindings.REFERENCE)):
                assert threading.get_ident() != loop_thread, f'Fiji disk read on event loop: {path}'
            return original(path, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', checked)
        await built.start_environment_observations()
        try:
            row = next(row for row in built.capabilities_wire(counts=False)['models'] if row['id'] == m.key)
            assert row['newKind']['proved']
            built.hub.settings.quieter = True
            built.publish_settings()
            assert built.settings_wire()['settings']['quieter']
        finally:
            await built._environment_observer.close()
    asyncio.run(scenario())


def test_stale_display_does_not_authorize_execution_or_survive_refresh(tmp_path, monkeypatch):
    from orchestrator import fiji_display
    from orchestrator import operations, share
    from orchestrator.operations import Unavailable
    m, base = prepare(tmp_path, monkeypatch)
    snapshot = env_observations.collect()
    (base / '1' / fiji_generations.RECEIPT).unlink()
    async def scenario():
        async def inherited():
            assert fiji_display.in_use(m) is None
        with env_observations.display(snapshot):
            assert fiji_display.in_use(m) is not None
            assert fiji_display.proven(m.key) == ''
            assert fiji_display.proven(fiji_bindings.BINDING) == ''
            assert fiji_runner.in_use(m) is None
            with pytest.raises(Unavailable):
                await fiji_runner._run(None, None, None, SimpleNamespace(id=m.id, label=m.name), {})
            with pytest.raises(Unavailable):
                await operations._fiji_particles(None, None, None,
                    SimpleNamespace(id=fiji_bindings.BINDING, label='Particles'), {})
            with pytest.raises(share.CantShare):
                share.export(m.key)
            child = asyncio.create_task(inherited())
        await child
    asyncio.run(scenario())
    with env_observations.display(env_observations.collect()):
        assert fiji_display.current() is None
        assert fiji_display.in_use(m) is None
        assert fiji_display.proven(m.key)


def test_changed_manifest_cannot_inherit_previous_display_proof(tmp_path, monkeypatch):
    from orchestrator import fiji_display
    m, _ = prepare(tmp_path, monkeypatch)
    snapshot = env_observations.collect()
    edited = replace(m, version=m.version + '-edited')
    original = catalog.get
    monkeypatch.setattr(catalog, 'get', lambda key, *args, **kw: edited if key == m.key else original(key, *args, **kw))
    with env_observations.display(snapshot):
        assert fiji_display.in_use(edited) is None
        assert fiji_display.proven(edited.key)


@pytest.mark.parametrize('damaged', [[], [1], {'proofs': []}, {'sites': [None]}, {'files': 7}])
def test_damaged_fiji_records_do_not_prevent_other_environment_collection(tmp_path, monkeypatch, damaged):
    from orchestrator import fiji_display
    from tests.test_app import app
    m, base = prepare(tmp_path, monkeypatch)
    built = app(tmp_path / 'app')
    first = env_observations.collect()
    original = (base / '1' / fiji_generations.RECEIPT).read_bytes()
    (base / '1' / fiji_generations.RECEIPT).write_text(json.dumps(damaged))
    with env_observations.display(first):
        assert fiji_display.in_use(m) is not None
    fresh = env_observations.collect()
    with env_observations.display(fresh):
        assert fiji_display.current() is None
        assert fiji_display.in_use(m) is None
        assert fiji_display.proven(m.key)
        assert fiji_display.generations()[0]['state'] == 'failed'
    built._environment_observer = SimpleNamespace(snapshot=fresh)
    rows = built.capabilities_wire(counts=False)['models']
    assert not next(row for row in rows if row['id'] == m.key)['newKind']['proved']
    assert any(row['id'] == 'dinov2-small' for row in rows)
    (base / '1' / fiji_generations.RECEIPT).write_bytes(original)
    built._environment_observer.snapshot = env_observations.collect()
    recovered = built.capabilities_wire(counts=False)['models']
    assert next(row for row in recovered if row['id'] == m.key)['newKind']['proved']


def test_empty_display_scope_refuses_without_disk_fallback(tmp_path, monkeypatch):
    from orchestrator import fiji_display
    m, _ = prepare(tmp_path, monkeypatch)
    def unexpected(*args, **kwargs):
        raise AssertionError('missing observation triggered a disk read')
    monkeypatch.setattr(fiji_generations, 'current', unexpected)
    monkeypatch.setattr(fiji_runner, 'in_use', unexpected)
    with env_observations.display({}):
        assert fiji_display.current() is None
        assert fiji_display.in_use(m) is None
        assert fiji_display.proven(m.key)
        assert fiji_display.receipt(1) is None
        assert fiji_display.generations() == []


def test_generation_switch_during_collection_never_publishes_mixed_facts(tmp_path, monkeypatch):
    from orchestrator import fiji_display
    m, base = prepare(tmp_path, monkeypatch)
    (base / '2').mkdir()
    (base / '2' / fiji_generations.RECEIPT).write_bytes((base / '1' / fiji_generations.RECEIPT).read_bytes())
    original = fiji_runner.in_use
    def switch(entry, environ):
        (base / 'current').write_text('2')
        return original(entry, environ)
    monkeypatch.setattr(fiji_runner, 'in_use', switch)
    with env_observations.display(env_observations.collect()):
        assert fiji_display.current() is None
        assert fiji_display.in_use(m) is None
        assert fiji_display.proven(m.key)
    with env_observations.display(env_observations.collect()):
        assert fiji_display.current() == 2
        assert fiji_display.in_use(m).n == 2


def test_contribution_permissions_remain_live_inside_display(contribution, monkeypatch):
    from orchestrator import fiji_display, fiji_contributions
    m = contribution
    fiji_contributions.install(m)
    snapshot = env_observations.collect()
    with env_observations.display(snapshot):
        assert fiji_display.in_use(m) is not None
        monkeypatch.setattr(fiji_contributions.channels, 'trusted', lambda *args: False)
        assert fiji_display.in_use(m) is None
        assert fiji_display.proven(m.key)
