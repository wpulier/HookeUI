"""Synthetic discovery and comparison checks for isolated Fiji contributions."""
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog
from orchestrator import catalog_view, fiji_runner, fiji_sandbox, own_trials


def test_fiji_comparison_acceptance_is_bound_to_runtime_and_boundary(tmp_path, monkeypatch):
    m = replace(catalog.get('fiji-gaussian-blur'), namespace='mine')
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    proof = {'content': 'candidate-content', 'boundary': {'version': 'one'}, 'runtime': 'runtime-one'}
    installed = SimpleNamespace(m=m, proof=proof, n=1)
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *_: installed)
    own_trials.await_comparison(m.key)
    first = own_trials.comparison_digest(m)
    marker = own_trials.marker(m.key)
    import json
    marker.write_text(json.dumps({'model': m.key, 'adoptedDigest': first}))
    assert not own_trials.pending(m)
    installed.n = 2
    assert own_trials.pending(m)
    installed.n = 1
    proof['boundary'] = {'version': 'two'}
    assert own_trials.pending(m)
    proof['boundary'] = {'version': 'one'}
    proof['runtime'] = 'runtime-two'
    assert own_trials.pending(m)
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *_: None)
    assert own_trials.pending(m)


def test_fiji_share_control_requires_available_boundary_and_local_proof(monkeypatch):
    m = catalog.get('fiji-gaussian-blur')
    monkeypatch.setattr(fiji_sandbox, 'available', lambda: False)
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *_: SimpleNamespace(m=m, proof={}, n=4))
    assert not catalog_view.shareable(m)
    monkeypatch.setattr(fiji_sandbox, 'available', lambda: True)
    assert catalog_view.shareable(m)
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *_: None)
    assert not catalog_view.shareable(m)


def test_contribution_card_reports_its_pinned_generation(monkeypatch):
    m = replace(catalog.get('fiji-gaussian-blur'), namespace='community')
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *_: SimpleNamespace(m=m, proof={'at': 123}, n=4))
    assert catalog_view.entry_check(m) == {
        'state': 'works', 'at': 123, 'detail': 'Confined functional proof passed on Fiji generation 4'}


def test_shared_fiji_namespaced_install_requirement_is_valid(tmp_path):
    folder = tmp_path / 'fiji-gaussian-blur'
    import shutil
    shutil.copytree(catalog.ROOT / folder.name, folder)
    text = (catalog.ROOT / folder.name / catalog.MANIFEST).read_text()
    (folder / catalog.MANIFEST).write_text(text.replace('install:fiji-gaussian-blur', 'install:shared/fiji-gaussian-blur'))
    m, problems = catalog.read(folder / catalog.MANIFEST, 'shared', 'shared')
    assert m is not None and not problems, problems


@pytest.mark.parametrize('script', ['/tmp/run.groovy', 'nested/run.groovy', '../run.groovy'])
def test_contributed_script_must_be_beside_manifest(tmp_path, script):
    m = catalog.get('fiji-gaussian-blur')
    bad = replace(m, fiji={**m.fiji, 'script': script})
    assert any('beside the manifest' in p for p in catalog._fiji_problems(bad))


@pytest.mark.parametrize('switch_back', [False, True])
def test_comparison_never_approves_another_fiji_candidate(tmp_path, monkeypatch, switch_back):
    import asyncio
    from pathlib import Path
    from tests.capability_rig import Rig
    from tests.test_own_comparison import image

    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    rig = Rig(tmp_path / 'rig')
    m = replace(catalog.get('fiji-gaussian-blur'), namespace='mine')
    old = SimpleNamespace(m=m, proof={'content': 'old', 'runtime': 'runtime', 'boundary': 'one'},
                          n=1, folder=Path('old-generation'))
    new = SimpleNamespace(m=m, proof={'content': 'new', 'runtime': 'runtime', 'boundary': 'one'},
                          n=1, folder=Path('new-generation'))
    active = [old]
    get = catalog.get
    monkeypatch.setattr(catalog, 'get', lambda key: m if key == m.key else get(key))
    monkeypatch.setattr(fiji_runner, 'in_use', lambda *_: active[0])
    trial = own_trials.add_input(image(), 'clear', rig.hub)
    own_trials.prepare(m.key, trial['id'])

    async def run(*_):
        active[0] = old if switch_back else new
        return {'picture': image(), 'producer': {'generation': 'new-generation' if switch_back else 'old-generation'}}

    monkeypatch.setattr(fiji_runner, '_run', run)
    with pytest.raises(ValueError, match='changed during comparison'):
        asyncio.run(own_trials.compare(rig.app, trial['id'], model=m.key))
    assert not own_trials.state(trial['id']).get('comparison')
    assert own_trials.pending(m)
