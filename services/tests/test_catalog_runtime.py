"""Dependency readiness is not adapter or scientific proof."""
from dataclasses import replace
from pathlib import Path

import pytest

from bench import catalog_ci
from gpu_worker import manifests as catalog
from orchestrator import envs, publish


@pytest.mark.parametrize('name', ['matrix-cpu-runtime', 'cell-evaluation-runtime'])
def test_bundled_runtime_is_publishable_metadata(name):
    m = catalog.get(name)
    assert m.kind == 'runtime'
    assert publish.problems(m, proven=False) == []


@pytest.mark.parametrize('changes', [
    {'env': {}}, {'env': {'python': '3.13.5', 'lock': 'env.lock', 'imports': []}},
    {'runner': 'builtin-cpu'}, {'adapter': 'adapter:run'}, {'operation': 'count'},
    {'workers': ('analysis',)}, {'host': 'shared'}, {'runs': 'cloud'},
])
def test_runtime_cannot_claim_execution_or_omit_its_checks(changes):
    m = replace(catalog.get('matrix-cpu-runtime'), **changes)
    assert any('runtime' in why for why in catalog._problems(m, m.id))


def test_runtime_lock_and_preparation_are_required(tmp_path, monkeypatch):
    m = replace(catalog.get('matrix-cpu-runtime'), folder=str(tmp_path))
    monkeypatch.setattr(catalog, 'entry_folder', lambda *args: tmp_path)
    assert any('lockfile' in why for why in publish.problems(m, proven=False))
    (tmp_path / 'env.lock').write_text('numpy==2.0.0\n')
    assert any('without a hash' in why for why in publish.problems(m, proven=False))
    (tmp_path / 'env.lock').write_text('numpy==2.0.0 --hash=sha256:' + 'a' * 64 + '\n')
    monkeypatch.setattr(envs, 'python', lambda *args: None)
    assert publish.problems(m, proven=False) == []
    assert any('not prepared' in why for why in publish.problems(m))


def test_ci_proves_runtime_preparation_without_an_operation(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(envs, 'build', lambda m, environ, say: calls.append(m.id))
    monkeypatch.setattr(envs, 'python', lambda *args: Path('/prepared/bin/python'))
    monkeypatch.setattr(catalog_ci.subprocess, 'run', lambda *a, **k: pytest.fail('no operation proof for a runtime'))
    assert catalog_ci.check('matrix-cpu-runtime', static=False, environ={}) == []
    assert calls == ['matrix-cpu-runtime']
    assert 'not operation or scientific qualification' in capsys.readouterr().out
    monkeypatch.setattr(envs, 'python', lambda *args: None)
    assert 'no usable receipt' in catalog_ci.check('matrix-cpu-runtime', static=False, environ={})[0]


def test_changed_imports_cannot_reuse_prepared_runtime(tmp_path, monkeypatch):
    import json
    m = catalog.get('matrix-cpu-runtime')
    environ = {'MICROMIND_CATALOG':str(tmp_path)}
    old = envs.folder(m, environ)
    (old / 'bin').mkdir(parents=True)
    (old / 'bin/python').write_text('synthetic interpreter locator')
    (old / envs.RECEIPT).write_text(json.dumps({'imports':m.env['imports']}))
    assert envs.python(m, environ) is not None
    changed = replace(m, env={**m.env, 'imports':[*m.env['imports'], 'nonexistent_new_import']})
    assert envs.folder(changed, environ) != old
    assert envs.python(changed, environ) is None
    assert any('not prepared' in why for why in publish.problems(changed, environ))
    assert (old / envs.RECEIPT).is_file()  # existing scientific evidence/generation stays intact


def test_prepared_runtime_card_does_not_request_an_operation_proof(tmp_path):
    from orchestrator import catalog_view, readiness
    m = catalog.get('matrix-cpu-runtime')
    r = readiness.Readiness(status='ready', word='Ready')
    face = catalog_view.face(m, r, [], {'MICROMIND_CATALOG':str(tmp_path)})
    assert face['state'] == 'obtained' and face['action'] is None
    assert 'Dependencies prepared' in face['says'] and 'no operation proof' in face['says']
