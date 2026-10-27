"""Real worker calls with a stand-in installer and synthetic reference pixels."""
import dataclasses
import json
import os

import cv2
import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import bundle_runtime, env_runner, envs, kinds
from tests.test_env_runner import home, standin_uv
from tests.test_bundle_manifest import HEAD

MEMBERS = '''
[[members]]
id = "invert"
name = "Invert image"
adapter = "adapter:invert"
inputs = [{ id = "image", type = "image.2d.rgb8" }, {id = "offset", type = "int", default = 0, min = 0, max = 5}]
outputs = [{ id = "result", type = "image.2d.rgb8" }]
[[members.reference_cases]]
input = "example.png"
reference = "reference-invert.png"
source = "Synthetic exact inversion, 255 minus every byte"
device = "cpu"
tolerance = {atol = 0, rtol = 0}
[[members]]
id = "mean"
name = "Mean intensity"
adapter = "adapter:mean"
inputs = [{ id = "image", type = "image.2d.rgb8" }]
outputs = [{ id = "table", type = "table.objects" }]
[[members.reference_cases]]
input = "example.png"
reference = "reference-mean.json"
source = "Synthetic constant 32 image with known mean 32"
device = "cpu"
tolerance = {atol = 0, rtol = 0, numeric_columns = ["mean"], identity_columns = ["region"]}
'''
ADAPTER = '''
import numpy as np

def invert(picture, params):
    return 255 - picture + params['offset']

def mean(picture, params):
    return {'columns': [{'name': 'region'}, {'name': 'mean'}], 'rows': [['whole', float(picture.mean())]]}
'''


def contribute(home, version='1', wrong=False):
    folder = home / 'repo/models/image-tools'
    folder.mkdir(exist_ok=True)
    (folder / 'model.toml').write_text(HEAD.replace('version = "1"', f'version = "{version}"') + MEMBERS)
    (folder / 'env.lock').write_text('numpy==2.0.0 --hash=sha256:' + 'a' * 64 + '\n')
    (folder / 'adapter.py').write_text(ADAPTER)
    cv2.imwrite(str(folder / 'example.png'), np.full((16, 16, 3), 32, np.uint8))
    cv2.imwrite(str(folder / 'reference-invert.png'), np.full((16, 16, 3), 223, np.uint8))
    (folder / 'reference-mean.json').write_text(json.dumps({'columns': [{'name': 'region'}, {'name': 'mean'}],
                                                         'rows': [['whole', 99 if wrong else 32]]}))
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    assert owner is not None, catalog.problems()
    return owner


@pytest.fixture(autouse=True)
def restore_registry():
    from orchestrator import operations
    before = dict(operations.OPERATIONS), set(kinds.registered())
    yield
    operations.OPERATIONS.clear()
    operations.OPERATIONS.update(before[0])
    kinds.registered().clear()
    kinds.registered().update(before[1])


def test_one_install_proves_and_exposes_two_operations_without_changing_owner(home, monkeypatch):
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    assert not env_runner.in_use(owner)
    folder, lines = envs.install(owner)
    active = env_runner.in_use(owner)
    assert active.folder == folder and active.m.key == 'image-tools'
    assert active.proof['ok'] and len(active.proof['cases']) == 2
    env_runner.register()
    available = [k for k in kinds.available() if k['key'].startswith('image-tools#')]
    assert {k['key'] for k in available} == {'image-tools#invert', 'image-tools#mean'}
    for k in available:
        assert kinds.manifest_of(k).owner.key == owner.key
    assert kinds.find('image-tools#in-vert') is None
    members = bundle_runtime.expand(active)
    for member in members:
        assert member.folder == folder and member.entry == active.entry
    worker = env_runner.client(members[0])
    output = worker.run(np.full((16, 16, 3), 32, np.uint8), {'offset': 0}, output='image.2d.rgb8')
    np.testing.assert_array_equal(output['picture'], np.full((16, 16, 3), 223, np.uint8))
    assert bundle_runtime.checked_parameters(members[0], {'offset': 0})
    assert not bundle_runtime.checked_parameters(members[0], {'offset': 1})
    assert len(envs.generations(owner)) == 1


def test_failed_member_reference_keeps_entire_candidate_unavailable(home, monkeypatch):
    owner = contribute(home, wrong=True)
    standin_uv(home, monkeypatch)
    with pytest.raises(envs.Failed, match='reference check failed'):
        envs.install(owner)
    assert env_runner.in_use(owner) is None
    assert not [k for k in kinds.available() if k['key'].startswith('image-tools#')]
    proof = env_runner.proof(owner)
    assert not proof['ok'] and proof['cases'][0]['ok'] and not proof['cases'][1]['ok']


def test_update_proof_never_switches_owner_and_both_members_switch_together(home, monkeypatch):
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    first, _ = envs.install(owner)
    newer = contribute(home, version='2')
    second, _ = envs.install(newer)
    assert first != second
    assert env_runner.in_use(newer).folder == first
    assert {m.m.version for m in bundle_runtime.expand(env_runner.in_use(newer))} == {'1'}
    env_runner.choose(newer, second.name)
    assert {m.m.version for m in bundle_runtime.expand(env_runner.in_use(newer))} == {'2'}
    env_runner.choose(newer, first.name)
    assert {m.m.version for m in bundle_runtime.expand(env_runner.in_use(newer))} == {'1'}


def test_legacy_smoke_receipt_cannot_enable_a_bundle(home, monkeypatch):
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    folder = envs.build(owner)
    env_runner.proof_path(owner).write_text('{"ok":true}')
    assert env_runner.in_use(owner) is None
    from orchestrator import env_observations
    assert not env_observations.observe(owner).candidate_proven
    assert not env_runner.newest_installed(owner)


def test_removed_member_finishes_admitted_work_when_owner_switches_generation(home, monkeypatch):
    from orchestrator import generation_leases
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    first, _ = envs.install(owner)
    env_runner.register()
    member = bundle_runtime.expand(env_runner.in_use(owner))[1]
    worker = env_runner.client(member)
    worker.run(np.full((16, 16, 3), 32, np.uint8), {}, output='table.objects')
    newer = contribute(home, version='2')
    manifest = home / 'repo/models/image-tools/model.toml'
    text = manifest.read_text()
    manifest.write_text(text[:text.index('[[members]]', text.index('[[members]]') + 1)])
    catalog.manifests.cache_clear()
    newer = catalog.get('image-tools')
    second, _ = envs.install(newer)
    stopped = []
    def retire(old):
        old.stop()
        stopped.append(old)
    with generation_leases.use(first):
        env_runner.choose(newer, second.name)
        env_runner.register(retire=retire)
        assert stopped == [] and worker._process.poll() is None
        assert 'image-tools#mean' not in {k['key'] for k in kinds.available()}
    assert stopped == [worker] and worker._process is None


def test_volume_input_cannot_be_proved_with_a_plane(home, monkeypatch):
    owner = contribute(home)
    file = home / 'repo/models/image-tools/model.toml'
    file.write_text(file.read_text().replace('id = "image", type = "image.2d.rgb8"', 'id = "image", type = "volume.3d"'))
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    standin_uv(home, monkeypatch)
    with pytest.raises(envs.Failed, match='one 2D RGB8 image'):
        envs.install(owner)
    assert env_runner.in_use(owner) is None


def test_observed_readiness_does_not_repeat_worker_file_reads(home, monkeypatch):
    from orchestrator import env_observations
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    observed = env_observations.observe(owner)
    def unexpected():
        raise AssertionError('display attempted a worker filesystem read')
    monkeypatch.setattr(bundle_runtime, 'worker_identity', unexpected)
    assert observed.candidate_proven
    assert observed.resolved().proof['scope'] == 'bundle-reference'
    with env_observations.display({env_observations.key(owner, {}): observed}):
        assert observed.installed(observed.candidate.name) is not None


def test_bundle_cannot_run_without_cpu_confinement(home, monkeypatch):
    from orchestrator import sandbox
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    monkeypatch.setattr(sandbox, 'available', lambda: False)
    with pytest.raises(envs.Failed, match='sandbox.*not run without it'):
        envs.install(owner)
    assert env_runner.in_use(owner) is None


def test_package_card_exposes_each_operation_from_active_generation(home, monkeypatch):
    from orchestrator import catalog_view
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    before = catalog_view.bundle_kinds(owner, os.environ)
    assert len(before) == 2 and all(not member['proved'] for member in before)
    envs.install(owner)
    env_runner.register()
    members = catalog_view.bundle_kinds(owner, os.environ)
    assert {member['key'] for member in members} == {'image-tools#invert', 'image-tools#mean'}
    assert all(member['proved'] and 'CPU' in member['proof'] for member in members)
    assert {item['try']['model'] for item in catalog_view.tries(owner, [])} == {member['key'] for member in members}
    assert {item['title'] for item in catalog_view.abilities(owner, [])} == {'Invert image', 'Mean intensity'}
    contribute(home, version='2')
    path = home / 'repo/models/image-tools/model.toml'
    path.write_text(path.read_text().replace('Invert image', 'New inversion'))
    catalog.manifests.cache_clear()
    newer = catalog.get('image-tools')
    assert catalog_view.bundle_kinds(newer, os.environ)[0]['name'] == 'Invert image'
    assert kinds.find('Mean intensity')['key'] == 'image-tools#mean'


@pytest.mark.parametrize('cancelled', [True, False])
def test_failed_recheck_preserves_accepted_proof_and_running_members(home, monkeypatch, cancelled):
    from orchestrator import generation_leases, owned_process
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    folder, _ = envs.install(owner)
    env_runner.register()
    accepted = env_runner.proof_path(owner).read_bytes()
    member = bundle_runtime.expand(env_runner.in_use(owner))[1]
    worker = env_runner.client(member)
    picture = np.full((16, 16, 3), 32, np.uint8)
    worker.run(picture, {}, output='table.objects')
    original = env_runner.Client.run
    stopped = False
    def fail_second(self, *args, **kwargs):
        nonlocal stopped
        if self is not worker and self.declaration.key.endswith('#mean'):
            stopped = cancelled
            raise owned_process.Stopped('cancelled') if cancelled else env_runner.Failed('controlled recheck failure')
        return original(self, *args, **kwargs)
    monkeypatch.setattr(env_runner.Client, 'run', fail_second)
    with generation_leases.use(folder):
        with pytest.raises(envs.Failed, match='cancelled|controlled recheck failure'):
            envs.install(owner, stop=lambda: stopped)
        env_runner.register()
        assert env_runner.proof_path(owner).read_bytes() == accepted
        assert {k['key'] for k in kinds.available() if k['key'].startswith('image-tools#')} == {'image-tools#invert', 'image-tools#mean'}
        assert worker._process.poll() is None
        assert worker.run(picture, {}, output='table.objects')['rows'][0][1] == 32
    attempt = json.loads(folder.with_name(folder.name + '.proof-attempt.json').read_text())
    assert not attempt['ok'] and attempt['cases'][0]['ok']
    assert env_runner.in_use(owner).folder == folder


def test_public_bench_runs_member_examples_from_installed_snapshot(home, monkeypatch):
    from bench import capability_test
    owner = contribute(home)
    path = home / 'repo/models/image-tools/model.toml'
    path.write_text(path.read_text().replace('adapter = "adapter:invert"',
                                            'adapter = "adapter:invert"\ntests = [{input = "example.png"}]'))
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    standin_uv(home, monkeypatch)
    envs.install(owner)
    member = bundle_runtime.expand(env_runner.in_use(owner))[0]
    (home / 'repo/models/image-tools/example.png').unlink()
    report = []
    capability_test.examples(member.m, report, member)
    assert len(report) == 2 and all(row['ok'] for row in report), report


def test_resource_failure_is_readable_and_retry_keeps_other_worker(home, monkeypatch):
    from orchestrator import worker_resources
    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    first, second = bundle_runtime.expand(env_runner.in_use(owner))
    healthy = env_runner.Client(first)
    picture = np.full((16, 16, 3), 32, np.uint8)
    healthy.run(picture, {'offset': 0}, output='image.2d.rgb8')
    guard = worker_resources.Guard
    with monkeypatch.context() as patch:
        patch.setattr(worker_resources, 'Guard', lambda process, work: guard(process, work, worker_resources.Limits(memory_bytes=1)))
        broken = env_runner.Client(second)
        with pytest.raises(env_runner.Failed, match='memory limit'):
            broken.run(picture, {}, output='table.objects')
        assert broken._process is None and broken._private_work is None
    try:
        assert healthy._process.poll() is None
        assert healthy.run(picture, {'offset': 0}, output='image.2d.rgb8')['picture'].shape == picture.shape
        assert broken.run(picture, {}, output='table.objects')['rows'][0][1] == 32
    finally:
        healthy.stop()
        broken.stop()


@pytest.mark.parametrize('test', ['{input = "example.png", outcome = "empty"}', '{input = "example.png", count = 9}'])
def test_declared_member_example_failure_blocks_bundle_readiness(home, monkeypatch, test):
    owner = contribute(home)
    path = home / 'repo/models/image-tools/model.toml'
    path.write_text(path.read_text().replace('adapter = "adapter:mean"', f'adapter = "adapter:mean"\ntests = [{test}]'))
    catalog.manifests.cache_clear()
    owner = catalog.get('image-tools')
    standin_uv(home, monkeypatch)
    with pytest.raises(envs.Failed, match='test 1.*declares'):
        envs.install(owner)
    assert env_runner.in_use(owner) is None
    assert not env_runner.proof(owner)['ok']
