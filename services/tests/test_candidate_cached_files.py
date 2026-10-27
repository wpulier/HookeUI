"""Tiny real cache/publication evidence; no upstream runtime or qualification."""
import hashlib

import pytest

from gpu_worker import manifests as catalog, model_files
from orchestrator import catalog_view, downloads, env_runner, envs, prepared_artifacts, readiness
from tests.test_channels import home  # noqa: F401
from tests.test_bundle_runtime import contribute
from tests.test_bundle_share import private_host
from tests.waiting import until


def cached_candidate(home, monkeypatch, *, scientific=True, corrupt=False):
    owner = contribute(home)
    if scientific:
        owner = private_host(owner)
    payload = b'pinned synthetic weights'
    revision = 'a' * 40
    manifest = catalog.entry_folder(owner) / 'model.toml'
    manifest.write_text(f'source = {{ hf = "test/weights", revision = "{revision}" }}\n'
                        f'files = [{{path = "weights.bin", bytes = {len(payload)}, '
                        f'sha256 = "{hashlib.sha256(payload).hexdigest()}"}}]\n' + manifest.read_text())
    catalog.manifests.cache_clear()
    owner = catalog.get(owner.key)
    assert owner is not None, catalog.problems()
    monkeypatch.setenv('MICROMIND_MODEL_FILES', str(home / 'old'))
    cached = home / 'old' / 'hf' / 'models--test--weights' / 'snapshots' / revision / 'weights.bin'
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b'x' * len(payload) if corrupt else payload)
    return owner, cached, payload


def file_fact(owner):
    return next(f for f in catalog_view.facts(owner, {}, lambda key: False, []) if f['kind'] == 'files')


@pytest.mark.parametrize('corrupt', [False, True])
def test_cached_candidate_requires_real_publication_before_scientific_admission(home, monkeypatch, corrupt):
    owner, cached, payload = cached_candidate(home, monkeypatch, corrupt=corrupt)
    before = cached.read_bytes()
    fact = file_fact(owner)
    assert fact['ok'] is False and fact['model'] == owner.key and fact['cached'] is True
    assert 'Prepare cached files' in fact['says']
    with pytest.raises(prepared_artifacts.NotPrepared):
        with prepared_artifacts.open_files(owner):
            pytest.fail('Cache presence is not prepared artifact authority')
    monkeypatch.setattr(downloads, 'download', lambda *a, **k: pytest.fail('Local adoption must not download'))
    queue = downloads.Downloads()
    queue.start(fact['model'])
    until(lambda: queue.wire()[0]['state'] in ('done', 'failed'))
    queue._thread.join(timeout=5)
    assert not queue._thread.is_alive()
    if corrupt:
        assert queue.wire()[0]['state'] == 'failed'
        assert model_files.published(owner) is None
        with pytest.raises(prepared_artifacts.NotPrepared):
            with prepared_artifacts.open_files(owner):
                pytest.fail('Corrupt bytes cannot become prepared')
    else:
        assert queue.wire()[0]['state'] == 'done', queue.wire()
        with prepared_artifacts.open_files(owner) as prepared:
            assert prepared.read_paths[0].read_bytes() == payload
        assert file_fact(owner)['ok'] is True
        assert 'cached' not in file_fact(owner) and 'model' not in file_fact(owner)
    assert cached.read_bytes() == before
    assert not envs.root(owner).exists(), 'File publication does not rebuild dependencies'
    assert env_runner.in_use(owner) is None
    assert readiness.model(owner.key, {}).status == 'needs-setup'
    assert readiness.model(owner.key, {}).next.kind == 'workflow'


def test_ordinary_model_cache_readiness_is_unchanged(home, monkeypatch):
    owner, _, _ = cached_candidate(home, monkeypatch, scientific=False)
    fact = file_fact(owner)
    assert fact['ok'] is True and 'cached' not in fact and 'model' not in fact
