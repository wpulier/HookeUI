"""Actual synthetic package proofs with a stand-in installer; retained review evidence."""
import dataclasses
import json
import os
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator import bundle_review, env_runner, envs, own_trials
from tests.test_bundle_runtime import contribute
from tests.test_env_runner import home, standin_uv


def installed_bundle(home, monkeypatch):
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    installed = env_runner.in_use(owner)
    # Also supplies evidence when exercising this module independently of proof publication.
    for case in installed.proof['cases']:
        image = case['member'].endswith('#invert')
        actual = ({'picture': np.full((16, 16, 3), 223, np.uint8)} if image else
                  {'columns': [{'name': 'region'}, {'name': 'mean'}], 'rows': [['whole', 32.0]]})
        reference = installed.entry / ('reference-invert.png' if image else 'reference-mean.json')
        case['artifacts'] = bundle_review.store(installed, case['case'], (installed.entry / 'example.png').read_bytes(),
                                               reference.read_bytes(), actual,
                                               'image.2d.rgb8' if image else 'table.objects')
    path = installed.folder.with_name(installed.folder.name + '.proof.json')
    path.write_text(json.dumps(installed.proof))
    return owner, installed


def test_retained_image_and_table_proofs_are_reviewable_and_timestamp_independent(home, monkeypatch):
    owner, installed = installed_bundle(home, monkeypatch)
    view = bundle_review.projection(installed)
    assert view['generation'] == installed.folder.name
    assert {case['member'] for case in view['cases']} == {'image-tools#invert', 'image-tools#mean'}
    assert view['cases'][0]['artifacts']['actual']['kind'] == 'image'
    table = bundle_review.read(installed, view['cases'][1]['artifacts']['actual'])
    assert json.loads(table)['rows'] == [['whole', 32.0]]
    view['cases'][0]['artifacts']['actual']['preview'] = 'UI-only'
    assert 'preview' not in installed.proof['cases'][0]['artifacts']['actual']
    modified = dataclasses.replace(installed, proof={**installed.proof, 'at': 'later', 'seconds': 1000})
    assert bundle_review.identity(modified) == view['digest']
    modified.proof['cases'] = [{**case, 'source': 'Different evidence'} for case in installed.proof['cases']]
    assert bundle_review.identity(modified) != view['digest']


def test_acceptance_is_explicit_generation_bound_and_preserves_trial(home, monkeypatch):
    owner, installed = installed_bundle(home, monkeypatch)
    own_trials.await_comparison(owner.key)
    path = own_trials.marker(owner.key)
    path.write_text(json.dumps({'model': owner.key, 'trial': 'retained-trial'}))
    view = bundle_review.projection(installed)
    before = path.read_bytes()
    for generation, digest in [('changed', view['digest']), (view['generation'], '0' * 64)]:
        with pytest.raises(ValueError, match='changed'):
            bundle_review.accept(owner.key, generation, digest)
        assert path.read_bytes() == before
    with pytest.raises(ValueError, match='session'):
        bundle_review.accept(owner.key, view['generation'], view['digest'], still_current=lambda: False)
    assert path.read_bytes() == before
    result = bundle_review.accept(owner.key, view['generation'], view['digest'])
    assert result['generation'] == installed.folder.name
    assert json.loads(path.read_text()) == {'model': owner.key, 'trial': 'retained-trial',
        'generation': view['generation'], 'adoptedDigest': view['digest'], 'reviewKind': 'bundle-reference'}
    bundle_review.accept(owner.key, view['generation'], view['digest'])
    assert json.loads(path.read_text())['adoptedDigest'] == view['digest']


def test_failed_and_corrupted_proofs_never_overwrite_acceptance(home, monkeypatch):
    owner, installed = installed_bundle(home, monkeypatch)
    view = bundle_review.projection(installed)
    bundle_review.accept(owner.key, view['generation'], view['digest'])
    marker = own_trials.marker(owner.key).read_bytes()
    actual = view['cases'][0]['artifacts']['actual']
    (bundle_review.folder(installed) / actual['sha256']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='changed'):
        bundle_review.accept(owner.key, view['generation'], view['digest'])
    assert own_trials.marker(owner.key).read_bytes() == marker
    with pytest.raises(ValueError, match='complete reference'):
        bundle_review.projection(dataclasses.replace(installed, proof={**installed.proof, 'ok': False}))


def test_review_store_rejects_symlinks_and_preserves_originals(tmp_path):
    installed = SimpleNamespace(folder=tmp_path / 'generation')
    installed.folder.mkdir()
    case = 'a' * 64
    descriptors = bundle_review.store(installed, case, b'input', b'{}', {'text': 'hello'}, 'text')
    assert bundle_review.read(installed, descriptors['actual']) == b'{"text":"hello"}'
    original = (bundle_review.folder(installed) / descriptors['actual']['sha256']).read_bytes()
    bundle_review.store(installed, case, b'input', b'{}', {'text': 'changed'}, 'text')
    assert bundle_review.read(installed, descriptors['actual']) == original
    target = bundle_review.folder(installed) / descriptors['actual']['sha256']
    target.unlink()
    protected = tmp_path / 'protected'
    protected.write_bytes(original)
    target.symlink_to(protected)
    with pytest.raises(ValueError, match='unavailable'):
        bundle_review.read(installed, descriptors['actual'])
    with pytest.raises(ValueError, match='unsafe'):
        bundle_review.store(installed, case, b'input', b'{}', {'text': 'hello'}, 'text')
    assert protected.read_bytes() == original


def test_review_directory_symlink_and_bad_descriptors_are_refused(tmp_path):
    installed = SimpleNamespace(folder=tmp_path / 'generation')
    installed.folder.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    bundle_review.folder(installed).symlink_to(outside, target_is_directory=True)
    with pytest.raises(OSError):
        bundle_review.store(installed, 'a' * 64, b'i', b'{}', {'text': 'x'}, 'text')
    assert not list(outside.iterdir())
    for descriptor in ({'kind': 'image', 'sha256': '../secret', 'bytes': 2},
                       {'kind': 'json', 'sha256': 'a' * 64, 'bytes': -1},
                       {'kind': 'json', 'sha256': 'a' * 64, 'bytes': True}):
        assert not bundle_review.descriptors_valid(dict.fromkeys(('input', 'reference', 'actual'), descriptor))


def test_storage_bounds_fail_without_deleting_previous_evidence(tmp_path, monkeypatch):
    installed = SimpleNamespace(folder=tmp_path / 'generation')
    installed.folder.mkdir()
    descriptors = bundle_review.store(installed, 'a' * 64, b'i', b'{}', {'text': 'first'}, 'text')
    before = {p.name: p.read_bytes() for p in bundle_review.folder(installed).iterdir()}
    monkeypatch.setattr(bundle_review, 'MAX_GENERATION', sum(map(len, before.values())))
    with pytest.raises(ValueError, match='generation limit'):
        bundle_review.store(installed, 'b' * 64, b'i', b'{}', {'text': 'second'}, 'text')
    assert {p.name: p.read_bytes() for p in bundle_review.folder(installed).iterdir()} == before
    assert bundle_review.read(installed, descriptors['actual']) == b'{"text":"first"}'
    with pytest.raises(ValueError):
        bundle_review.store(installed, 'a' * 64, b'i', b'{}', {'value': float('nan')}, 'table.objects')


def test_accept_rechecks_session_at_commit(home, monkeypatch):
    owner, installed = installed_bundle(home, monkeypatch)
    view = bundle_review.projection(installed)
    own_trials.await_comparison(owner.key)
    path = own_trials.marker(owner.key)
    before = path.read_bytes()
    checks = iter([True, False])
    with pytest.raises(ValueError, match='session changed'):
        bundle_review.accept(owner.key, view['generation'], view['digest'], still_current=lambda: next(checks))
    assert path.read_bytes() == before
    assert not list(path.parent.glob('*.tmp'))


def test_failed_publication_and_low_disk_leave_existing_evidence_usable(tmp_path, monkeypatch):
    installed = SimpleNamespace(folder=tmp_path / 'generation')
    installed.folder.mkdir()
    original = bundle_review.store(installed, 'a' * 64, b'i', b'{}', {'text': 'first'}, 'text')
    before = {p.name: p.read_bytes() for p in bundle_review.folder(installed).iterdir()}
    real_link = bundle_review.os.link
    def failed_link(*args, **kwargs):
        raise OSError('interrupted publication')
    monkeypatch.setattr(bundle_review.os, 'link', failed_link)
    with pytest.raises(OSError, match='interrupted'):
        bundle_review.store(installed, 'b' * 64, b'i', b'{}', {'text': 'second'}, 'text')
    assert {p.name: p.read_bytes() for p in bundle_review.folder(installed).iterdir()} == before
    monkeypatch.setattr(bundle_review.os, 'link', real_link)
    monkeypatch.setattr(bundle_review.shutil, 'disk_usage', lambda _: SimpleNamespace(free=1))
    with pytest.raises(ValueError, match='free space'):
        bundle_review.store(installed, 'b' * 64, b'i', b'{}', {'text': 'second'}, 'text')
    assert bundle_review.read(installed, original['actual']) == b'{"text":"first"}'


def test_update_and_rollback_cannot_reuse_another_generations_acceptance(home, monkeypatch):
    owner, first = installed_bundle(home, monkeypatch)
    first_view = bundle_review.projection(first)
    bundle_review.accept(owner.key, first_view['generation'], first_view['digest'])
    before = own_trials.marker(owner.key).read_bytes()
    updated = contribute(home, version='2')
    second_folder, _ = envs.install(updated)
    env_runner.choose(updated, second_folder.name)
    with pytest.raises(ValueError, match='no supported local interactive workflow'):
        bundle_review.accept(updated.key, first_view['generation'], first_view['digest'])
    assert own_trials.marker(owner.key).read_bytes() == before
    env_runner.choose(updated, first.folder.name)
    bundle_review.accept(updated.key, first_view['generation'], first_view['digest'])
    assert own_trials.marker(owner.key).read_bytes() == before


def test_review_identity_reads_no_entry_files_during_display(home, monkeypatch):
    from tests.test_bundle_runtime import contribute
    from tests.test_env_runner import standin_uv
    from orchestrator import bundle_review, env_runner, envs, env_observations, own_trials
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    own_trials.await_comparison(owner.key, review_kind='bundle-reference')
    observations = env_observations.collect()
    def forbidden(*args, **kwargs):
        raise AssertionError('entry content was hashed on the display path')
    monkeypatch.setattr(envs, 'entry_digest', forbidden)
    with env_observations.display(observations):
        assert own_trials.pending(owner)
        assert bundle_review.identity(env_runner.in_use(owner))


def test_corrupt_evidence_is_repaired_by_recheck_without_changing_acceptance(home, monkeypatch):
    from tests.test_bundle_runtime import contribute
    from tests.test_env_runner import standin_uv
    from orchestrator import bundle_review, bundle_runtime, env_runner, envs, own_trials
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    installed = env_runner.in_use(owner)
    checked = bundle_review.projection(installed)
    bundle_review.accept(owner.key, checked['generation'], checked['digest'])
    descriptor = checked['cases'][0]['artifacts']['actual']
    path = bundle_review.folder(installed) / descriptor['sha256']
    original = path.read_bytes()
    path.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='changed'):
        bundle_review.projection(installed)
    assert bundle_runtime.prove(owner, os.environ, lambda: False)['ok']
    assert path.read_bytes() == original
    assert not own_trials.pending(owner)
    assert bundle_review.projection(env_runner.in_use(owner))['digest'] == checked['digest']
