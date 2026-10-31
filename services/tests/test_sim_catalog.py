"""Synthetic resource stores; no real models, private entries or installations are used."""
import hashlib
import json

import pytest
from pathlib import Path

from gpu_worker import manifests, model_files
from orchestrator.paths import Paths


def test_simulation_clones_only_published_stock_resources(monkeypatch, tmp_path):
    from sim.catalog_fixture import prepare
    source = tmp_path / 'source'
    inherited = {'MICROMIND_CATALOG': str(source), 'MICROMIND_ENVS': str(tmp_path / 'installed-envs'),
                 'MICROMIND_TOOLS': str(tmp_path / 'installed-tools')}
    content = b'synthetic-weights'
    model = manifests.Manifest('fixture', 'Fixture', 'this-mac', '1', ('open',), folder=str(manifests.ROOT / 'fixture'),
                               source={'revision': 'a' * 40},
                               files=({'path': 'weights.bin', 'bytes': len(content),
                                       'sha256': hashlib.sha256(content).hexdigest()},))
    monkeypatch.setattr(manifests, 'manifests', lambda: {'fixture': model})
    revision = model_files.revision_dir(model, inherited)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(content)
    (revision / model_files.RECEIPT).write_text(json.dumps({'revision': manifests.revision_of(model)}))
    verification = model_files.verification_path(model, inherited)
    verification.write_text(json.dumps({'ok': True, 'at': 'synthetic'}))
    proof = revision.parent / '.device-check.json'
    proof.write_text('{"synthetic": true}')
    personal = source / 'mine' / 'models' / 'personal'
    personal.mkdir(parents=True)
    (personal / 'model.toml').write_text('private synthetic canary')
    (source / 'channels').mkdir()
    (source / 'channels' / 'channels.json').write_text('private synthetic channel')
    before = {str(f.relative_to(source)): f.read_bytes() for f in source.rglob('*') if f.is_file()}
    paths = Paths.under(tmp_path / 'run')
    env = {**inherited, **prepare(inherited, paths)}
    assert env['MICROMIND_CATALOG'] == str(paths.home / 'catalog')
    assert env['MICROMIND_ENVS'] == inherited['MICROMIND_ENVS']
    assert env['MICROMIND_TOOLS'] == inherited['MICROMIND_TOOLS']
    target = model_files.published(model, env)
    assert target is not None and not target.is_symlink()
    assert (target / 'weights.bin').read_bytes() == content
    assert (target.parent / '.device-check.json').read_bytes() == proof.read_bytes()
    assert not (Path(env['MICROMIND_CATALOG']) / 'mine').exists()
    assert not (Path(env['MICROMIND_CATALOG']) / 'channels').exists()
    model_files.verification_path(model, env).write_text('{"ok": false}')
    (target.parent / '.device-check.json').write_text('{"test-only": true}')
    (target / 'weights.bin').write_bytes(b'local test change')
    assert {str(f.relative_to(source)): f.read_bytes() for f in source.rglob('*') if f.is_file()} == before


def test_missing_stock_resources_are_not_fabricated(monkeypatch, tmp_path):
    from sim.catalog_fixture import prepare
    model = manifests.Manifest('absent', 'Absent', 'this-mac', '1', ('open',), folder=str(manifests.ROOT / 'absent'),
                               source={'revision': 'b' * 40}, files=({'path': 'absent', 'bytes': 1},))
    monkeypatch.setattr(manifests, 'manifests', lambda: {'absent': model})
    inherited = {'MICROMIND_CATALOG': str(tmp_path / 'missing')}
    env = {**inherited, **prepare(inherited, Paths.under(tmp_path / 'run'))}
    assert model_files.published(model, env) is None
    assert not (Path(env['MICROMIND_CATALOG']) / 'models' / 'absent').exists()


def test_legacy_weight_device_proof_is_preserved_without_inventing_published_weights(monkeypatch, tmp_path):
    from sim.catalog_fixture import prepare
    model = manifests.Manifest('legacy', 'Legacy', 'this-mac', '1', ('open',), folder=str(manifests.ROOT / 'legacy'),
                               files=({'path': 'weights', 'bytes': 1},))
    monkeypatch.setattr(manifests, 'manifests', lambda: {'legacy': model})
    inherited = {'MICROMIND_CATALOG': str(tmp_path / 'source')}
    folder = model_files.folder(model, inherited)
    folder.mkdir(parents=True)
    (folder / '.device-check.json').write_text('{"synthetic-proof": true}')
    env = {**inherited, **prepare(inherited, Paths.under(tmp_path / 'run'))}
    assert (model_files.folder(model, env) / '.device-check.json').read_text() == '{"synthetic-proof": true}'
    assert model_files.published(model, env) is None


def test_reviewed_channel_is_not_bundled_stock(monkeypatch, tmp_path):
    from sim.catalog_fixture import prepare
    from tests.test_env_runner import ENTRY
    source = tmp_path / 'source'
    monkeypatch.setenv('MICROMIND_CATALOG', str(source))
    snapshot = source / 'channels' / 'reviewed' / ('c' * 40)
    entry = snapshot / 'dark-spots'
    entry.mkdir(parents=True)
    (entry / 'model.toml').write_text(ENTRY.format() + '\n[[files]]\npath = "weights"\nurl = "https://example.org/weights"\nbytes = 1\nsha256 = "' + 'a' * 64 + '"\n')
    (entry / 'env.lock').write_text('synthetic-lock')
    (entry / 'adapter.py').write_text('# synthetic')
    (source / 'channels' / 'channels.json').write_text(json.dumps([
        {'name': 'reviewed', 'commit': 'c' * 40, 'reviewed': True}]))
    manifests.manifests.cache_clear()
    try:
        assert 'dark-spots' in manifests.manifests(), manifests.problems()
        model = manifests.manifests()['dark-spots']
        assert model.namespace == '' and model.channel == 'reviewed'
        inherited = {'MICROMIND_CATALOG': str(source)}
        revision = model_files.revision_dir(model, inherited)
        revision.mkdir(parents=True)
        (revision / 'weights').write_bytes(b'x')
        (revision / model_files.RECEIPT).write_text(json.dumps({'revision': manifests.revision_of(model)}))
        assert model_files.published(model, inherited) is not None
        env = {**inherited, **prepare(inherited, Paths.under(tmp_path / 'run'))}
        assert model_files.published(model, env) is None
        assert not (Path(env['MICROMIND_CATALOG']) / 'channels').exists()
    finally:
        manifests.manifests.cache_clear()


@pytest.mark.parametrize('alias', ['revision', 'file'])
def test_source_aliases_are_refused_before_copy(monkeypatch, tmp_path, alias):
    from sim.catalog_fixture import prepare
    model = manifests.Manifest('fixture', 'Fixture', 'this-mac', '1', ('open',),
                               folder=str(manifests.ROOT / 'fixture'),
                               files=({'path': 'weights', 'bytes': 1},))
    monkeypatch.setattr(manifests, 'manifests', lambda: {'fixture': model})
    inherited = {'MICROMIND_CATALOG': str(tmp_path / 'source')}
    revision = model_files.revision_dir(model, inherited)
    revision.mkdir(parents=True)
    (revision / 'weights').write_bytes(b'x')
    (revision / model_files.RECEIPT).write_text(json.dumps({'revision': manifests.revision_of(model)}))
    external = tmp_path / 'owner-resource'
    target = revision if alias == 'revision' else revision / 'weights'
    target.rename(external)
    target.symlink_to(external, target_is_directory=alias == 'revision')
    assert model_files.published(model, inherited) is not None
    with pytest.raises(ValueError, match='symlink'):
        prepare(inherited, Paths.under(tmp_path / 'run'))
