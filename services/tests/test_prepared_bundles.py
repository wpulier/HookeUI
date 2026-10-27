"""Preparation identity and preservation with a synthetic bundle/stand-in installer."""
import json
import os

import pytest

from gpu_worker import manifests as catalog
from orchestrator import env_runner, envs, generation_leases, prepared_bundles
from tests.test_bundle_runtime import contribute
from tests.test_env_runner import home, standin_uv


def prepare(home, monkeypatch):
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    return owner, envs.build(owner)


def test_prepared_member_is_not_proven_or_enabled_and_retains_runtime(home, monkeypatch):
    owner, folder = prepare(home, monkeypatch)
    with prepared_bundles.prepared_member('image-tools#mean', folder.name) as candidate:
        assert candidate.m.owner.key == owner.key and candidate.m.member.id == 'mean'
        assert candidate.folder == folder and candidate.entry == folder / 'entry/image-tools'
        assert candidate.python == folder / 'bin/python'
        assert not isinstance(candidate, env_runner.Installed)
        assert not hasattr(candidate, 'proof')
        assert env_runner.in_use(owner) is None
        assert not env_runner.proof_path(owner).exists()
        with pytest.raises(generation_leases.Busy):
            with generation_leases.withdraw(folder):
                pass
    with generation_leases.withdraw(folder):
        pass
    assert env_runner.in_use(owner) is None


def test_review_uses_exact_snapshot_and_preserves_active_generation(home, monkeypatch):
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    active, _ = envs.install(owner)
    env_runner.choose(owner, active.name)
    proof = env_runner.proof_path(owner).read_bytes()
    newer = contribute(home, version='2')
    candidate = envs.build(newer)
    chosen = (envs.root(owner) / env_runner.CHOSEN).read_bytes()
    (home / 'repo/models/image-tools/adapter.py').write_text('raise AssertionError("unprepared edit")')
    with prepared_bundles.prepared_member('image-tools#mean', candidate.name) as prepared:
        assert prepared.m.owner.version == '2'
        assert 'unprepared edit' not in (prepared.entry / 'adapter.py').read_text()
        assert env_runner.in_use(newer).folder == active
    assert (envs.root(owner) / env_runner.CHOSEN).read_bytes() == chosen
    assert active.with_name(active.name + '.proof.json').read_bytes() == proof


@pytest.mark.parametrize('field,value', [('model', 'other'), ('generation', '0' * 16),
                                      ('lockSha256', 'f' * 64), ('dependencyCheck', None),
                                      ('dependencyCheck', {'boundary': 'installed-requirements-v1',
                                                           'scope': 'installed distribution requirements', 'ok': 1})])
def test_receipt_cannot_substitute_other_candidate_or_skip_requirements(home, monkeypatch, field, value):
    _, folder = prepare(home, monkeypatch)
    path = folder / envs.RECEIPT
    receipt = json.loads(path.read_text())
    receipt[field] = value
    path.write_text(json.dumps(receipt))
    with pytest.raises(prepared_bundles.NotPrepared, match='preparation receipt'):
        with prepared_bundles.prepared_member('image-tools#mean', folder.name):
            pytest.fail('invalid receipt admitted')


@pytest.mark.parametrize('mutation', ['adapter', 'lock', 'manifest', 'symlink', 'fifo', 'receipt-link', 'receipt-fifo'])
def test_changed_or_redirected_prepared_files_are_refused(home, monkeypatch, mutation):
    _, folder = prepare(home, monkeypatch)
    entry = folder / 'entry/image-tools'
    if mutation in ('adapter', 'lock'):
        (entry / ('adapter.py' if mutation == 'adapter' else 'env.lock')).write_text('changed')
    elif mutation == 'manifest':
        path = entry / 'model.toml'
        path.write_text(path.read_text().replace('version = "1"', 'version = "changed"'))
    elif mutation in ('receipt-link', 'receipt-fifo'):
        receipt = folder / envs.RECEIPT
        original = folder / 'receipt-copy.json'
        receipt.rename(original)
        if mutation == 'receipt-link':
            receipt.symlink_to(original)
        else:
            os.mkfifo(receipt)
    elif mutation == 'symlink':
        (entry / 'unexpected').symlink_to(home)
    else:
        os.mkfifo(entry / 'unexpected')
    with pytest.raises(prepared_bundles.NotPrepared):
        with prepared_bundles.prepared_member('image-tools#mean', folder.name):
            pytest.fail('changed candidate admitted')


def test_snapshot_rights_not_active_generation_rights(home, monkeypatch):
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    newer = contribute(home, version='2')
    path = home / 'repo/models/image-tools/model.toml'
    path.write_text(path.read_text().replace('tier = "open"', 'tier = "noncommercial"'))
    catalog.manifests.cache_clear()
    newer = catalog.get('image-tools')
    candidate = envs.build(newer)
    monkeypatch.setenv('MICROMIND_EDITION', 'commercial')
    with pytest.raises(prepared_bundles.NotPrepared, match='not permitted'):
        with prepared_bundles.prepared_member('image-tools#mean', candidate.name):
            pytest.fail('candidate borrowed active licence')
    assert env_runner.in_use(newer).m.version == '1'


def test_live_trust_and_preparation_ignore_display_observations(home, monkeypatch):
    from orchestrator import channels, env_observations
    owner, folder = prepare(home, monkeypatch)
    with env_observations.display({}):
        with prepared_bundles.prepared_member('image-tools#mean', folder.name):
            pass
    monkeypatch.setattr(channels, 'trusted', lambda *args: False)
    with pytest.raises(prepared_bundles.NotPrepared, match='Allow code'):
        with prepared_bundles.prepared_member('image-tools#mean', folder.name):
            pytest.fail('trust bypassed')


def test_generation_redirect_and_unknown_member_are_refused(home, monkeypatch):
    _, folder = prepare(home, monkeypatch)
    for generation in ('../' + folder.name, str(folder), 'z' * 16, '', None):
        with pytest.raises(prepared_bundles.NotPrepared):
            with prepared_bundles.prepared_member('image-tools#mean', generation):
                pytest.fail('invalid generation admitted')
    with pytest.raises(prepared_bundles.NotPrepared, match='not in that prepared generation'):
        with prepared_bundles.prepared_member('image-tools#missing', folder.name):
            pytest.fail('unknown member admitted')
    other = folder.with_name('0' * 16)
    other.symlink_to(folder, target_is_directory=True)
    with pytest.raises(prepared_bundles.NotPrepared, match='managed runtime'):
        with prepared_bundles.prepared_member('image-tools#mean', other.name):
            pytest.fail('redirected generation admitted')


def test_review_failure_is_preserved_and_releases_lease(home, monkeypatch):
    _, folder = prepare(home, monkeypatch)
    failure = OSError('review failed after preparation')
    with pytest.raises(OSError) as caught:
        with prepared_bundles.prepared_member('image-tools#mean', folder.name):
            raise failure
    assert caught.value is failure
    with generation_leases.withdraw(folder):
        pass


def test_prepared_lease_can_close_in_a_different_off_loop_context(home, monkeypatch):
    from contextvars import Context
    _, folder = prepare(home, monkeypatch)
    lease = prepared_bundles.prepared_member('image-tools#mean', folder.name)
    candidate = Context().run(lease.__enter__)
    assert candidate.folder == folder
    Context().run(lease.__exit__, None, None, None)
    with generation_leases.withdraw(folder):
        pass


@pytest.mark.parametrize('name', ['../env.lock', '/private/env.lock', './env.lock', 'nested//env.lock', 'nested/../env.lock'])
def test_snapshot_lock_cannot_read_outside_its_canonical_entry(home, monkeypatch, name):
    _, folder = prepare(home, monkeypatch)
    path = folder / 'entry/image-tools/model.toml'
    path.write_text(path.read_text().replace('lock = "env.lock"', f'lock = "{name}"'))
    original = prepared_bundles._regular
    reads = []
    def read(path, limit):
        reads.append(path)
        return original(path, limit)
    monkeypatch.setattr(prepared_bundles, '_regular', read)
    with pytest.raises(prepared_bundles.NotPrepared, match='canonical file'):
        with prepared_bundles.prepared_member('image-tools#mean', folder.name):
            pytest.fail('redirected lock admitted')
    assert all(path.name in ('model.toml', envs.RECEIPT) for path in reads)


def test_older_receipt_rechecks_without_reinstalling_or_losing_active_work(home, monkeypatch):
    from orchestrator import environment_check
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    folder, _ = envs.install(owner)
    path = folder / envs.RECEIPT
    record = json.loads(path.read_text())
    del record['dependencyCheck']
    original = json.dumps(record).encode()
    path.write_bytes(original)
    proof = env_runner.proof_path(owner).read_bytes()
    marker = folder / 'retained-marker'
    marker.write_text('unchanged')
    actual_check = environment_check.check
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise envs.Failed('controlled dependency conflict')
        patch.setattr(environment_check, 'check', fail)
        with pytest.raises(envs.Failed, match='dependency conflict'):
            envs.build(owner)
    assert path.read_bytes() == original
    assert env_runner.in_use(owner).folder == folder
    calls = []
    def record_check(*args, **kwargs):
        calls.append(args[1])
        return actual_check(*args, **kwargs)
    monkeypatch.setattr(environment_check, 'check', record_check)
    assert envs.build(owner) == folder
    assert envs.build(owner) == folder and calls == [folder]
    assert marker.read_text() == 'unchanged'
    assert env_runner.proof_path(owner).read_bytes() == proof
    assert not list(folder.glob('.dependency-check-*'))
    with prepared_bundles.prepared_member('image-tools#mean', folder.name):
        pass


def test_malformed_receipt_is_a_recoverable_refusal(home, monkeypatch):
    _, folder = prepare(home, monkeypatch)
    path = folder / envs.RECEIPT
    for body in ('[', '[' * 2000 + ']' * 2000, '{"model":1,"model":2}'):
        path.write_text(body)
        with pytest.raises(prepared_bundles.NotPrepared):
            with prepared_bundles.prepared_member('image-tools#mean', folder.name):
                pytest.fail('malformed receipt admitted')


def test_redirected_interpreter_is_refused_before_launch_or_scope_grant(home, monkeypatch):
    owner, folder = prepare(home, monkeypatch)
    executable = folder / 'bin/python'
    executable.unlink()
    outside = home / 'private/bin/python'
    outside.parent.mkdir(parents=True)
    outside.write_text('must never execute')
    executable.symlink_to(outside)
    def launched(*args, **kwargs):
        pytest.fail('a redirected interpreter must never launch')
    monkeypatch.setattr(envs, '_run', launched)
    with pytest.raises(prepared_bundles.NotPrepared):
        with prepared_bundles.prepared_member('image-tools#mean', folder.name):
            pytest.fail('redirected interpreter admitted')
    with pytest.raises(envs.Failed, match='changed after preparation'):
        envs.build(owner)


def test_failed_legacy_interpreter_recovery_preserves_receipt_and_proof(home, monkeypatch):
    from orchestrator import interpreter_identity
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    folder, _ = envs.install(owner)
    path = folder / envs.RECEIPT
    record = json.loads(path.read_text())
    identity = record.pop('interpreter')
    original = json.dumps(record).encode()
    path.write_bytes(original)
    proof = env_runner.proof_path(owner).read_bytes()
    with monkeypatch.context() as patch:
        def fail(*args, **kwargs):
            raise envs.Failed('managed lookup unavailable')
        patch.setattr(interpreter_identity, 'recover', fail)
        with pytest.raises(envs.Failed, match='managed lookup unavailable'):
            envs.build(owner)
    assert path.read_bytes() == original and env_runner.proof_path(owner).read_bytes() == proof
    assert env_runner.in_use(owner).folder == folder
    monkeypatch.setattr(interpreter_identity, 'recover', lambda *args: identity)
    assert envs.build(owner) == folder
    assert json.loads(path.read_text())['interpreter'] == identity
    assert env_runner.proof_path(owner).read_bytes() == proof


def test_interactive_candidate_cannot_borrow_operation_proof_or_replace_active_bundle(home, monkeypatch):
    from orchestrator import bundle_review, bundle_runtime, own_trials
    from tests.test_bundle_manifest import HEAD
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    active, _ = envs.install(owner)
    proof = env_runner.proof_path(owner).read_bytes()
    installed = env_runner.in_use(owner)
    reviewed = bundle_review.projection(installed)
    bundle_review.accept(owner.key, reviewed['generation'], reviewed['digest'])
    accepted = json.loads(own_trials.marker(owner.key).read_text())['adoptedDigest']
    path = home / 'repo/models/image-tools/model.toml'
    path.write_text(HEAD.replace('version = "1"', 'version = "2"') + '''
[[members]]
id = "review"
name = "Scientific workspace review"
adapter = "adapter:review"
bridge = "scientific-host-v1"
inputs = [{id = "work", type = "project.scientific"}]
outputs = [{id = "result", type = "project.scientific"}]
''')
    catalog.manifests.cache_clear()
    candidate = catalog.get('image-tools')
    assert candidate is not None, catalog.problems()
    folder = envs.build(candidate)
    with prepared_bundles.prepared_member('image-tools#review', folder.name) as prepared:
        assert prepared.m.bridge == 'scientific-host-v1'
    assert not bundle_runtime.proof_complete(candidate, folder, json.loads(proof))
    prepared_folder, lines = envs.install(candidate)
    assert prepared_folder == folder and any('local workflow' in line for line in lines)
    assert not bundle_runtime.proof_complete(candidate, folder, json.loads(proof))
    assert env_runner.in_use(candidate).folder == active
    assert active.with_name(active.name + '.proof.json').read_bytes() == proof

    assert not own_trials.pending(candidate), 'Preparing another contract preserves the selected accepted generation'
    assert json.loads(own_trials.marker(owner.key).read_text())['adoptedDigest'] == accepted
