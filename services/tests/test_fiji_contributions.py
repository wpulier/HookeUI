"""Private Fiji activation preserves reviewed and previously accepted capabilities."""
import json
from pathlib import Path

import numpy as np
import pytest

from gpu_worker import manifests as catalog
from orchestrator import envs, fiji_contributions as contributions, fiji_generations as generations
from orchestrator import fiji_runner, fiji_sandbox, readiness

PROOF = fiji_runner.proof


@pytest.fixture
def contribution(tmp_path, monkeypatch):
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    folder = catalog.mine_root() / 'contributed-blur'
    folder.mkdir(parents=True)
    for name, data in contributions.files(catalog.get('fiji-gaussian-blur')).items():
        if name == catalog.MANIFEST:
            data = data.replace(b'fiji-gaussian-blur', b'contributed-blur')
        (folder / name).write_bytes(data)
    m, problems = catalog.read(folder / catalog.MANIFEST, 'mine')
    assert m and not problems, problems
    runtime = generations.fiji_dir(1)
    runtime.mkdir(parents=True)
    (runtime.parent / generations.RECEIPT).write_text(json.dumps({'proofs': {'reviewed': {'passed': True}}}))
    (generations.root() / 'current').write_text('1')
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: True)
    monkeypatch.setattr(fiji_sandbox, 'available', lambda: True)
    monkeypatch.setattr(fiji_sandbox, 'identity', lambda: {'boundary': 'test', 'workerSha256': 'test'})
    monkeypatch.setattr(contributions.channels, 'trusted', lambda *args: True)
    monkeypatch.setattr(fiji_runner, 'proof', lambda entry, *args: {
        **fiji_runner.identity(entry), 'passed': True, 'summary': 'synthetic parity'})
    return m


def test_separate_receipts_and_idempotent_immutable_candidate(contribution):
    m = contribution
    reviewed = (generations.root() / '1' / generations.RECEIPT).read_bytes()
    before = envs.signature()
    folder, _ = envs.install(m)
    held = contributions.in_use(m)
    assert held and held.folder == folder and held.current
    assert held.m.folder != m.folder and held.n == 1
    assert (generations.root() / '1' / generations.RECEIPT).read_bytes() == reviewed
    assert generations.current() == 1 and envs.signature() != before
    assert envs.install(m)[0] == folder
    assert contributions.retained_generations() == {1}


def test_failed_update_preserves_active_and_reviewed_generation(contribution, monkeypatch):
    m = contribution
    contributions.install(m)
    before = contributions.in_use(m)
    (Path(m.folder) / 'README.md').write_text('new candidate')
    def fail(*args):
        raise generations.Failed('parity differs')
    monkeypatch.setattr(fiji_runner, 'proof', fail)
    with pytest.raises(generations.Failed, match='parity differs'):
        contributions.install(m)
    after = contributions.in_use(m)
    assert after and after.folder == before.folder and not after.current
    assert generations.current() == 1
    assert contributions._json(contributions.root(m) / 'last-failure.json')['why'] == 'parity differs'


def test_invalid_edit_does_not_hide_working_snapshot(contribution):
    m = contribution
    contributions.install(m)
    before = contributions.in_use(m)
    (Path(m.folder) / catalog.MANIFEST).write_text('this is not toml')
    after = contributions.in_use(m)
    assert after and after.folder == before.folder and not after.current


def test_new_generation_requires_new_proof_before_switching(contribution):
    m = contribution
    contributions.install(m)
    old = contributions.in_use(m)
    runtime = generations.fiji_dir(2)
    runtime.mkdir(parents=True)
    (runtime.parent / generations.RECEIPT).write_text('{"proofs": {}}')
    (generations.root() / 'current').write_text('2')
    held = contributions.in_use(m)
    assert held.n == 1 and not held.current and contributions.retained_generations() == {1}
    contributions.install(m)
    new = contributions.in_use(m)
    assert new.n == 2 and new.current and new.folder != old.folder
    assert contributions.retained_generations() == {2}


@pytest.mark.parametrize('change', ['boundary', 'receipt', 'snapshot'])
def test_changed_proof_identity_refuses_execution(contribution, monkeypatch, change):
    m = contribution
    contributions.install(m)
    held = contributions.in_use(m)
    if change == 'boundary':
        monkeypatch.setattr(fiji_sandbox, 'identity', lambda: {'boundary': 'changed'})
    elif change == 'receipt':
        (generations.root() / '1' / generations.RECEIPT).write_text('{"changed": true}')
    else:
        path = Path(held.m.folder) / catalog.MANIFEST
        path.chmod(0o644)
        path.write_text(path.read_text() + '\n# changed\n')
    assert contributions.in_use(m) is None


def test_permission_and_confinement_required_for_install_and_use(contribution, monkeypatch):
    m = contribution
    contributions.install(m)
    monkeypatch.setattr(contributions.channels, 'trusted', lambda *args: False)
    assert contributions.in_use(m) is None
    with pytest.raises(generations.Failed, match='Allow this contributor'):
        contributions.install(m)
    monkeypatch.setattr(contributions.channels, 'trusted', lambda *args: True)
    monkeypatch.setattr(fiji_sandbox, 'available', lambda: False)
    monkeypatch.setattr(fiji_sandbox, 'supported', lambda: False)
    assert contributions.in_use(m) is None
    with pytest.raises(generations.Failed, match='cannot establish'):
        contributions.install(m)


def test_turn_off_preserves_snapshot_and_runtime_for_reenable(contribution):
    m = contribution
    contributions.install(m)
    before = contributions.in_use(m)
    signature = envs.signature()
    contributions.turn_off(m)
    assert contributions.turned_off(m) and contributions.in_use(m) is None
    assert before.folder.is_dir() and contributions.retained_generations() == {1}
    assert envs.signature() != signature
    contributions.install(m)
    assert contributions.in_use(m).folder == before.folder
    assert not contributions.turned_off(m)


def test_plugin_payload_and_missing_runtime_fail_closed(contribution):
    m = contribution
    contributions.install(m)
    before = contributions.in_use(m)
    (Path(m.folder) / 'plugin.jar').write_bytes(b'jar')
    with pytest.raises(generations.Failed, match='cannot install plugins'):
        contributions.install(m)
    assert contributions.in_use(m).folder == before.folder
    (generations.root() / '1' / generations.RECEIPT).unlink()
    assert contributions.in_use(m) is None


def test_contribution_cannot_install_update_site(contribution):
    with pytest.raises(envs.Failed, match='cannot modify Fiji'):
        envs.install(contribution, site={'site': 'unsafe', 'url': 'https://example.org'})


def test_edit_during_proof_never_activates(contribution, monkeypatch):
    m = contribution
    def proof(*args):
        (Path(m.folder) / 'README.md').write_text('edited during proof')
        return {'passed': True, 'summary': 'synthetic'}
    monkeypatch.setattr(fiji_runner, 'proof', proof)
    with pytest.raises(generations.Failed, match='changed during'):
        contributions.install(m)
    assert contributions.in_use(m) is None


def test_corrupt_existing_snapshot_is_not_activated_on_retry(contribution):
    m = contribution
    contributions.install(m)
    path = Path(contributions.in_use(m).m.folder) / catalog.MANIFEST
    path.chmod(0o644)
    path.write_text('bad toml')
    with pytest.raises(generations.Failed, match='incompatible candidate'):
        contributions.install(m)
    assert contributions.in_use(m) is None


def test_reviewed_generation_never_proves_unreviewed_contributions(contribution, monkeypatch):
    monkeypatch.setattr(fiji_runner, 'entries', lambda *args: [contribution])
    monkeypatch.setattr(generations, 'smoke', lambda *args: 'standin')
    monkeypatch.setattr(generations, 'PROOFS', {})
    monkeypatch.setattr(fiji_runner, 'proof', lambda *args: pytest.fail('unreviewed proof reached reviewed generation'))
    assert generations._prove(1, {}, lambda _: None) == {'smoke': 'standin'}


def test_invocation_cannot_borrow_reviewed_worker(contribution, monkeypatch):
    import cv2
    calls = []
    def call(runtime, request, **kwargs):
        calls.append((runtime, request, kwargs))
        ok, png = cv2.imencode('.png', cv2.imread(request['image']))
        assert ok
        return fiji_sandbox.Result({'ok': True, 'kind': 'picture'}, png.tobytes(), '')
    monkeypatch.setattr(fiji_sandbox, 'call', call)
    monkeypatch.setattr(fiji_runner, 'worker', lambda: pytest.fail('reviewed worker borrowed'))
    got = fiji_runner.run_function(contribution, np.zeros((4, 4, 3), np.uint8), {'sigma': 2}, n=1, using=object())
    assert got['shape'] == [4, 4, 3] and calls[0][0] == generations.fiji_dir(1)
    assert calls[0][2]['entry'] == Path(contribution.folder)


def test_reference_is_confined(contribution, monkeypatch, tmp_path):
    import cv2
    image = tmp_path / 'input.png'
    cv2.imwrite(str(image), np.zeros((4, 4, 3), np.uint8))
    calls = []
    def macro(runtime, picture, command, options, **kwargs):
        calls.append((runtime, command, options))
        return fiji_sandbox.Result({}, image.read_bytes(), '')
    monkeypatch.setattr(fiji_sandbox, 'run_macro', macro)
    monkeypatch.setattr(generations, 'run_fiji', lambda *args, **kwargs: pytest.fail('unrestricted Fiji invoked'))
    result = fiji_runner.by_macro(contribution, image, {'sigma': 2}, 1)
    assert result['shape'] == [4, 4, 3] and calls[0][2] == 'sigma=2'


def test_readiness_uses_private_receipt_and_refuses_missing_sandbox(contribution, monkeypatch):
    m = contribution
    monkeypatch.setattr(fiji_runner, 'entries', lambda *args: [m])
    steps, *_ = readiness.model_steps(m, lambda _: False)
    assert any(s.model == m.key and s.kind == 'install' for s in steps)
    contributions.install(m)
    assert generations.proven(m.key) == ''
    assert readiness.model_steps(m, lambda _: False)[0] == []
    monkeypatch.setattr(fiji_sandbox, 'available', lambda: False)
    assert readiness.model_steps(m, lambda _: False)[0][0].kind == 'unsupported'


def test_running_attempt_retains_generation_after_active_pointer_moves(contribution):
    m = contribution
    contributions.install(m)
    runtime = generations.fiji_dir(2)
    runtime.mkdir(parents=True)
    (runtime.parent / generations.RECEIPT).write_text('{"proofs": {}}')
    (generations.root() / 'current').write_text('2')
    with contributions.retain(1):
        contributions.install(m)
        assert contributions.retained_generations() == {1, 2}
    assert contributions.retained_generations() == {2}


def test_space_cannot_remove_a_pinned_fiji_generation(contribution, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from orchestrator import capability_space, fiji_bindings

    contributions.install(contribution)
    monkeypatch.setattr(fiji_bindings.WORKER, 'stop', lambda: pytest.fail('A refused removal must not stop Fiji'))
    app = SimpleNamespace(hub=SimpleNamespace(loads=None), downloads=SimpleNamespace(wire=lambda: []),
                          installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
    row = next(r for r in capability_space.inventory()['items'] if r['kind'] == 'fiji')
    with pytest.raises(ValueError, match='retained'):
        asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert contributions.in_use(contribution) is not None and generations.fiji_dir(1).is_dir()


def test_repeated_readiness_reuses_verified_content_bytes(contribution, monkeypatch):
    contributions.install(contribution)
    assert contributions.in_use(contribution)
    monkeypatch.setattr(contributions, 'files', lambda *args: pytest.fail('unchanged content rehashed'))
    assert contributions.in_use(contribution)
    assert contributions.in_use(contribution)


def test_available_does_not_probe_during_cold_catalog_read(monkeypatch):
    fiji_sandbox.supported.cache_clear()
    monkeypatch.setattr(fiji_sandbox.subprocess, 'run', lambda *args, **kwargs: pytest.fail('probe ran on catalog read'))
    assert fiji_sandbox.available() is False


def test_full_contributed_proof_confines_both_sides_at_every_parameter(contribution, monkeypatch):
    import cv2
    from orchestrator import fiji_bindings

    invocations, references = [], []
    def encoded(image, options):
        sigma = float(options.split('=')[1])
        picture = (cv2.imread(str(image)) * (1 - sigma / 40)).astype('uint8')
        ok, png = cv2.imencode('.png', picture)
        assert ok
        return png.tobytes()
    def call(runtime, request, **kwargs):
        invocations.append(request)
        if request['op'] == 'has_command':
            return fiji_sandbox.Result({'found': True}, None, '')
        return fiji_sandbox.Result({'kind': 'picture'}, encoded(request['image'], request['options']), '')
    def reference(runtime, image, command, options, **kwargs):
        references.append(options)
        return fiji_sandbox.Result({}, encoded(image, options), '')
    monkeypatch.setattr(fiji_sandbox, 'call', call)
    monkeypatch.setattr(fiji_sandbox, 'run_macro', reference)
    monkeypatch.setattr(fiji_bindings, 'Worker', lambda *args: pytest.fail('reviewed worker opened'))
    monkeypatch.setattr(generations, 'run_fiji', lambda *args, **kwargs: pytest.fail('unrestricted reference opened'))
    result = PROOF(contribution, generations.fiji_dir(1))
    assert result['passed'] and len(result['cases']) == 6
    assert len(invocations) == 7 and len(references) == 6
    assert all(case['largestDifference'] == 0 for case in result['cases'])


def test_oversized_picture_is_refused_before_decoding(tmp_path, monkeypatch):
    import cv2
    from PIL import Image
    from types import SimpleNamespace
    from contextlib import nullcontext

    image = tmp_path / 'small.png'
    image.write_bytes(b'bounded fixture')
    monkeypatch.setattr(Image, 'open', lambda *args: nullcontext(SimpleNamespace(size=(16_000_001, 1))))
    monkeypatch.setattr(cv2, 'imread', lambda *args: pytest.fail('oversized picture reached decoder'))
    with pytest.raises(generations.Failed, match='16 million pixels'):
        fiji_runner._picture(image)


def test_multiplane_picture_is_refused_before_decoding(tmp_path, monkeypatch):
    import cv2
    from PIL import Image

    image = tmp_path / 'stack.tiff'
    frame = Image.new('RGB', (8, 8))
    frame.save(image, save_all=True, append_images=[frame])
    monkeypatch.setattr(cv2, 'imread', lambda *args: pytest.fail('stack reached decoder'))
    with pytest.raises(generations.Failed, match='one image plane'):
        fiji_runner._picture(image)


def test_decode_must_agree_with_the_checked_header(tmp_path, monkeypatch):
    import cv2
    from PIL import Image

    image = tmp_path / 'image.png'
    Image.new('RGB', (8, 8)).save(image)
    monkeypatch.setattr(cv2, 'imread', lambda *args: np.zeros((9, 8, 3), np.uint8))
    with pytest.raises(generations.Failed, match='match its image header'):
        fiji_runner._picture(image)


def test_distinct_namespace_ids_never_share_activation_or_off_state(contribution):
    from dataclasses import replace

    left = replace(contribution, namespace='a', id='b--c')
    right = replace(contribution, namespace='a--b', id='c')
    assert contributions.root(left) != contributions.root(right)
    contributions.turn_off(left)
    assert contributions.turned_off(left) and not contributions.turned_off(right)


def test_separate_preview_is_included_in_immutable_snapshot(contribution):
    from dataclasses import replace

    image = next(t['input'] for t in contribution.tests if t.get('input'))
    preview = Path(contribution.folder) / 'preview.png'
    preview.write_bytes((Path(contribution.folder) / image).read_bytes())
    changed = replace(contribution, example='preview.png')
    assert contributions.files(changed)['preview.png'] == preview.read_bytes()
    preview.unlink()
    with pytest.raises(generations.Failed, match='incomplete'):
        contributions.files(changed)


def test_memory_refusal_happens_before_input_read_or_jvm_launch(contribution, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from orchestrator import capture, source_images
    from orchestrator.operations import Unavailable

    contributions.install(contribution)
    memory = capture.Memory({'analysis': 1})
    monkeypatch.setattr(capture, 'captures', lambda hub: SimpleNamespace(memory=memory))
    monkeypatch.setattr(source_images, 'worker_input', lambda *args: pytest.fail('input read before admission'))
    monkeypatch.setattr(fiji_runner, 'run_function', lambda *args, **kwargs: pytest.fail('JVM launched before admission'))
    image = SimpleNamespace(width=8, height=8, analysis_bytes=b'')
    impl = SimpleNamespace(id=contribution.id, label=contribution.name)
    with pytest.raises(Unavailable, match="wasn't given its picture"):
        asyncio.run(fiji_runner._run(SimpleNamespace(hub=object()), image, None, impl, {}))
    assert memory.reserved['analysis'] == 0


def test_jvm_queue_cancellation_never_launches_waiting_attempt(monkeypatch, tmp_path):
    import threading
    from orchestrator import owned_process

    entered, release, cancel = threading.Event(), threading.Event(), threading.Event()
    calls, errors = [], []
    def admitted(*args):
        calls.append(True)
        entered.set()
        assert release.wait(5)
        return {}, ''
    monkeypatch.setattr(fiji_sandbox, '_execute_admitted', admitted)
    def first():
        fiji_sandbox._execute(tmp_path, tmp_path, (), [], None, fiji_sandbox.Limits())
    def second():
        try:
            with owned_process.scope(stop=cancel.is_set):
                fiji_sandbox._execute(tmp_path, tmp_path, (), [], None, fiji_sandbox.Limits())
        except owned_process.Stopped as exc:
            errors.append(exc)
    owner = threading.Thread(target=first)
    waiter = threading.Thread(target=second)
    owner.start()
    try:
        assert entered.wait(2)
        waiter.start()
        cancel.set()
        waiter.join(2)
        assert not waiter.is_alive() and errors and len(calls) == 1
    finally:
        release.set()
        owner.join(2)
        if waiter.ident is not None:
            waiter.join(2)


def test_output_memory_admission_precedes_large_file_read(tmp_path, monkeypatch):
    from orchestrator.capture import Refused
    from tests.test_fiji_sandbox import PNG

    output = tmp_path / 'output.png'
    output.write_bytes(PNG)
    seen = []
    def reject(header, size):
        seen.append((len(header), size))
        raise Refused('controlled output limit', next='retry with smaller output')
    with pytest.raises(Refused, match='controlled output limit'):
        fiji_sandbox._output(output, fiji_sandbox.Limits(), reject)
    assert seen == [(33, len(PNG))]


def test_cancelled_setup_keeps_prior_active_and_is_a_queue_failure_type(contribution):
    contributions.install(contribution)
    held = contributions.in_use(contribution)
    with pytest.raises(envs.Failed, match='cancelled'):
        envs.install(contribution, stop=lambda: True)
    assert contributions.in_use(contribution).folder == held.folder


def test_failed_invocation_releases_shared_memory(contribution, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from orchestrator import capture, source_images
    from orchestrator.operations import Unavailable
    from tests.test_fiji_sandbox import PNG

    contributions.install(contribution)
    memory = capture.Memory({'analysis': 160})
    monkeypatch.setattr(capture, 'captures', lambda hub: SimpleNamespace(memory=memory))
    async def input_bytes(*args):
        assert memory.reserved['analysis'] > 0
        return PNG
    def fail(*args, **kwargs):
        assert memory.reserved['analysis'] > 0
        raise generations.Failed('controlled failure')
    monkeypatch.setattr(source_images, 'worker_input', input_bytes)
    monkeypatch.setattr(fiji_runner, 'run_function', fail)
    image = SimpleNamespace(width=1, height=1, analysis_bytes=PNG)
    impl = SimpleNamespace(id=contribution.id, label=contribution.name)
    with pytest.raises(Unavailable, match='controlled failure'):
        asyncio.run(fiji_runner._run(SimpleNamespace(hub=object()), image, None, impl, {}))
    assert memory.reserved['analysis'] == 0


def test_turn_off_during_update_is_not_undone_by_completion(contribution, monkeypatch):
    m = contribution
    contributions.install(m)
    active = (contributions.root(m) / 'active.json').read_bytes()
    proof = fiji_runner.proof
    def turn_off_then_complete(*args):
        contributions.turn_off(m)
        return proof(*args)
    monkeypatch.setattr(fiji_runner, 'proof', turn_off_then_complete)
    with pytest.raises(generations.Failed, match='turned off during setup'):
        contributions.install(m)
    assert contributions.turned_off(m)
    assert contributions.in_use(m) is None
    assert (contributions.root(m) / 'active.json').read_bytes() == active


def test_turn_off_with_queued_update_preserves_active_and_other_work(contribution, monkeypatch):
    import asyncio
    import threading
    from types import SimpleNamespace
    from orchestrator import commands

    m = contribution
    contributions.install(m)
    active = (contributions.root(m) / 'active.json').read_bytes()
    original_get = catalog.get
    monkeypatch.setattr(catalog, 'get', lambda key: m if key == m.key else original_get(key))
    queue = envs.Installs()
    # A held worker makes the admission genuinely queued until deactivation returns.
    release = threading.Event()
    queue._thread = threading.Thread(target=release.wait)
    queue._thread.start()
    queue.start(m.key)
    other = original_get('fiji-gaussian-blur')
    queue.start(other.key)
    cancelled = []
    turn_on = SimpleNamespace(wire=lambda: {'model:' + m.key: {'model': m.key, 'attempt': 'old'},
                                            'unrelated': {'model': other.key, 'attempt': 'keep'}},
                              cancel=lambda job, **kw: cancelled.append((job, kw)))
    app = SimpleNamespace(turn_on=turn_on, installs=queue, catalog_changed=lambda: None, hub=None)
    monkeypatch.setattr(commands.activity, 'activity', lambda *args, **kw: None)
    try:
        asyncio.run(commands.model_deactivate(app, None, {'model': m.key}))
        assert queue.rows[m.key]['state'] == 'cancelled'
        assert m.key not in queue._queue
        assert queue.rows[other.key]['state'] == 'queued'
        assert other.key in queue._queue
        assert cancelled == [('model:' + m.key, {'attempt': 'old'})]
        assert contributions.turned_off(m)
        assert contributions.in_use(m) is None
        assert (contributions.root(m) / 'active.json').read_bytes() == active
    finally:
        release.set()
        queue._thread.join(5)
