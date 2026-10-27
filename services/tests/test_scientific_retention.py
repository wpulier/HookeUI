"""Actual filesystem/process lifetime boundaries; no package or clean-machine qualification."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from orchestrator import envs, scientific_retention as retention
from orchestrator.scientific_projects import ProjectStore
from tests.test_envs import home, entry, fake_uv


def document(bundle='boxed', generation='a' * 16):
    return {'kind': 'scene', 'title': 'Retained cells', 'state': {}, 'resources': [],
            'runtimes': [{'bundle': bundle, 'generation': generation, 'operation': 'view'}]}


def test_old_accepted_revisions_and_shared_profiles_retain_generations_after_restart(tmp_path):
    runtime = tmp_path / 'envs'
    first = ProjectStore(tmp_path / 'one', runtime_root=runtime)
    saved = first.create('s1', document())
    first.save(saved.id, 's1', document(generation='b' * 16), expected_revision=1)
    second = ProjectStore(tmp_path / 'two', runtime_root=runtime)
    second.create('s2', document('other'))
    with retention.locked(runtime):
        rows = list(retention.references(runtime))
        assert {r['generation'] for r in rows} == {'a' * 16, 'b' * 16}
        assert {r['session'] for r in rows} == {'s1', 's2'}
        for model in ('boxed', 'other'):
            with pytest.raises(retention.Retained, match='saved scientific'):
                retention.require_removable(runtime, models={model})
        retention.require_removable(runtime, models={'unrelated'})
        retention.require_removable(runtime, models={'boxed'}, generations={'c' * 16})
    code = 'from orchestrator import scientific_retention as r; from pathlib import Path; import sys\nwith r.locked(Path(sys.argv[1])): r.require_removable(Path(sys.argv[1]), models={"boxed"})'
    result = subprocess.run([sys.executable, '-c', code, str(runtime)], capture_output=True, text=True)
    assert result.returncode and 'saved scientific' in result.stderr


def test_uninstall_and_receiptless_repair_preserve_saved_runtime(home):
    model = entry(home)
    folder = envs.folder(model)
    folder.mkdir(parents=True)
    sentinel = folder / 'scientific-runtime'
    sentinel.write_bytes(b'keep this exact version')
    store = ProjectStore(home / 'work', runtime_root=folder.parent.parent)
    saved = store.create('s1', document(model.key, folder.name))
    with pytest.raises(retention.Retained): envs.remove(model)
    with pytest.raises(retention.Retained): envs.build(model)
    assert sentinel.read_bytes() == b'keep this exact version'
    assert store.read(saved.id, 's1') == saved


@pytest.mark.parametrize('damage', ['revision', 'head', 'head-missing', 'store', 'binding', 'association'])
def test_unreadable_retention_evidence_refuses_unrelated_removal(tmp_path, damage):
    runtime = tmp_path / 'envs'
    store = ProjectStore(tmp_path / 'work', runtime_root=runtime)
    saved = store.create('s1', document())
    folder = store.root / saved.id
    target = {'revision': folder / 'revisions' / (saved.digest + '.json'), 'head': folder / 'head.json',
              'binding': store.root / retention.BINDINGS,
              'association': retention._association(runtime, store.root)}
    if damage in target: target[damage].write_text('corrupt')
    elif damage == 'head-missing': (folder / 'head.json').unlink()
    else: store.root.rename(tmp_path / 'moved-work')
    with retention.locked(runtime), pytest.raises(retention.Uncertain):
        retention.require_removable(runtime, models={'unrelated'})


def test_orphan_revision_does_not_pin_another_generation(tmp_path):
    runtime = tmp_path / 'envs'
    store = ProjectStore(tmp_path / 'work', runtime_root=runtime)
    saved = store.create('s1', document())
    (store.root / saved.id / 'revisions' / ('f' * 64 + '.json')).write_text('unpublished garbage')
    with retention.locked(runtime):
        retention.require_removable(runtime, models={'boxed'}, generations={'f' * 16})
        assert len(list(retention.references(runtime))) == 1


def test_association_interruption_fails_closed_and_bind_repairs(tmp_path, monkeypatch):
    runtime = tmp_path / 'envs'
    store = ProjectStore(tmp_path / 'work')
    store.create('s1', document())
    original = retention._write
    def fail(path, value):
        if path.name == retention.BINDINGS: raise OSError('controlled disk failure')
        return original(path, value)
    with monkeypatch.context() as patch:
        patch.setattr(retention, '_write', fail)
        with pytest.raises(OSError): retention.bind(store, runtime)
    with retention.locked(runtime), pytest.raises(retention.Uncertain):
        retention.require_removable(runtime, models={'boxed'})
    retention.bind(store, runtime)
    with retention.locked(runtime), pytest.raises(retention.Retained):
        retention.require_removable(runtime, models={'boxed'})


def test_save_reopened_store_uses_all_associated_runtime_roots(tmp_path):
    one, two = tmp_path / 'env-one', tmp_path / 'env-two'
    store = ProjectStore(tmp_path / 'work', runtime_root=one)
    retention.bind(store, two)
    reopened = ProjectStore(store.root)
    saved = reopened.create('s1', document())
    for runtime in (one, two):
        with retention.locked(runtime):
            assert list(retention.references(runtime))[0]['document'] == saved.id


def test_identical_environment_directory_alias_cannot_bypass_retention(tmp_path):
    runtime = tmp_path / 'envs'
    store = ProjectStore(tmp_path / 'work', runtime_root=runtime)
    store.create('s1', document('mine/host'))
    with retention.locked(runtime), pytest.raises(retention.Retained):
        retention.require_removable(runtime, models={'mine--host'})


def test_space_explains_saved_work_and_rechecks_before_withdrawal(home):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from orchestrator import capability_space
    m = entry(home)
    folder = envs.folder(m)
    folder.mkdir(parents=True)
    (folder / 'original').write_bytes(b'kept')
    store = ProjectStore(home / 'work', runtime_root=folder.parent.parent)
    row = next(r for r in capability_space.inventory()['items'] if r['kind'] == 'environment' and m.key in r['models'])
    assert 'retention' not in row
    store.create('s1', document(m.key, folder.name))
    current = next(r for r in capability_space.inventory()['items'] if r['id'] == row['id'])
    assert 'saved scientific' in current['retention']['reason']
    assert 'Turn off' in current['retention']['next']
    unload = AsyncMock(return_value={'unloaded': True})
    worker = SimpleNamespace(loading={}, resident={m.key: {}})
    app = SimpleNamespace(hub=SimpleNamespace(loads=SimpleNamespace(clients=[worker], let_go=unload)), downloads=SimpleNamespace(wire=lambda: []),
                          installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
    with pytest.raises(retention.Retained):
        asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert (folder / 'original').read_bytes() == b'kept'
    unload.assert_not_called()


def test_space_rechecks_work_saved_while_a_worker_is_retiring(home):
    import asyncio
    from types import SimpleNamespace
    from orchestrator import capability_space

    m = entry(home)
    folder = envs.folder(m)
    folder.mkdir(parents=True)
    (folder / 'original').write_bytes(b'kept')
    store = ProjectStore(home / 'work', runtime_root=folder.parent.parent)
    row = next(r for r in capability_space.inventory()['items'] if r['kind'] == 'environment' and m.key in r['models'])
    published = []
    async def unload(worker, model):
        assert model == m.key
        published.append(store.create('s1', document(m.key, folder.name)))
        return {'unloaded': True}
    worker = SimpleNamespace(loading={}, resident={m.key: {}})
    app = SimpleNamespace(hub=SimpleNamespace(loads=SimpleNamespace(clients=[worker], let_go=unload)),
                          downloads=SimpleNamespace(wire=lambda: []), installs=SimpleNamespace(wire=lambda: []),
                          catalog_changed=lambda: pytest.fail('A refused removal must not announce a catalog change'))
    with pytest.raises(retention.Retained):
        asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert len(published) == 1 and store.read(published[0].id, 's1') == published[0]
    assert (folder / 'original').read_bytes() == b'kept'


def test_separate_process_publication_wins_before_removal(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    runtime = tmp_path / 'envs'
    store = ProjectStore(tmp_path / 'work', runtime_root=runtime)
    code = '''import json, sys
from pathlib import Path
from orchestrator.scientific_projects import ProjectStore
store = ProjectStore(Path(sys.argv[1]))
original = store._write
def held(path, body, **kwargs):
    if path.name == 'head.json':
        print('before-head', flush=True)
        sys.stdin.readline()
    return original(path, body, **kwargs)
store._write = held
store.create('s1', json.loads(sys.argv[2]))
'''
    child = subprocess.Popen([sys.executable, '-c', code, str(store.root), json.dumps(document())],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'before-head'
        def remove():
            with retention.locked(runtime):
                retention.require_removable(runtime, models={'boxed'})
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(remove)
            child.stdin.write('publish\n'); child.stdin.flush()
            with pytest.raises(retention.Retained): pending.result(timeout=10)
        assert child.wait(timeout=10) == 0, child.stderr.read()
    finally:
        if child.poll() is None: child.kill()
        child.wait(timeout=10)


def test_broken_runtime_registration_does_not_prevent_reading_saved_work(tmp_path):
    runtime = tmp_path / 'envs'
    store = ProjectStore(tmp_path / 'work', runtime_root=runtime)
    saved = store.create('s1', document())
    (store.root / retention.BINDINGS).write_text('damaged')
    reopened = ProjectStore(store.root, runtime_root=runtime)
    assert reopened.runtime_problem
    assert reopened.read(saved.id, 's1') == saved
    from orchestrator.scientific_projects import InvalidProject
    with pytest.raises(InvalidProject):
        reopened.save(saved.id, 's1', document(), expected_revision=1)
    with retention.locked(runtime), pytest.raises(retention.Uncertain):
        retention.require_removable(runtime, models={'boxed'})
    (store.root / retention.BINDINGS).write_text(json.dumps([str(runtime)]))
    assert reopened.save(saved.id, 's1', document(), expected_revision=1).revision == 2


def test_registration_limits_never_publish_unreadable_bindings(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'work')
    for index in range(32): retention.bind(store, tmp_path / f'env-{index}')
    before = (store.root / retention.BINDINGS).read_bytes()
    with pytest.raises(retention.Uncertain, match='maximum'):
        retention.bind(store, tmp_path / 'env-overflow')
    assert (store.root / retention.BINDINGS).read_bytes() == before
    assert len(retention._roots(store.root)) == 32
    assert not retention._association(tmp_path / 'env-overflow', store.root).exists()
    root = tmp_path / 'shared-env'
    monkeypatch.setattr(retention, 'MAX_STORES', 1)
    one, two = ProjectStore(tmp_path / 'one'), ProjectStore(tmp_path / 'two')
    retention.bind(one, root)
    with pytest.raises(retention.Uncertain, match='limit'):
        retention.bind(two, root)
    assert not (two.root / retention.BINDINGS).exists()
    assert len(list((root / retention.METADATA).glob('*.json'))) == 1


def test_registration_syncs_parent_directories_before_binding_ack(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / 'work')
    calls = []
    original = ProjectStore._sync_directory
    def sync(path):
        calls.append(Path(path))
        original(path)
    monkeypatch.setattr(ProjectStore, '_sync_directory', staticmethod(sync))
    runtime = tmp_path / 'new' / 'runtime'
    retention.bind(store, runtime)
    assert runtime.parent in calls and runtime in calls
    assert (runtime / retention.METADATA).is_dir()


def test_corrected_registration_retries_from_storage_refresh(tmp_path, monkeypatch):
    from orchestrator import capability_space
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    runtime = tmp_path / 'catalog' / 'envs'
    store = ProjectStore(tmp_path / 'work', runtime_root=runtime)
    saved = store.create('s1', document())
    (store.root / retention.BINDINGS).unlink()
    assert list((runtime / retention.METADATA).glob('*.json'))
    capability_space.inventory(store)
    assert retention._roots(store.root) == [runtime]
    assert store.read(saved.id, 's1') == saved


def test_deeply_nested_registration_keeps_saved_data_readable(tmp_path):
    runtime = tmp_path / 'envs'
    store = ProjectStore(tmp_path / 'work', runtime_root=runtime)
    saved = store.create('s1', document())
    (store.root / retention.BINDINGS).write_text('[' * 15000 + ']' * 15000)
    reopened = ProjectStore(store.root, runtime_root=runtime)
    assert reopened.runtime_problem
    assert reopened.read(saved.id, 's1') == saved
    with retention.locked(runtime), pytest.raises(retention.Uncertain):
        retention.require_removable(runtime, models={'unrelated'})


@pytest.mark.parametrize('command', ['model', 'space'])
def test_manual_removal_recovery_does_not_tell_user_to_stop_nonexistent_work(home, command):
    import asyncio
    from types import SimpleNamespace
    from orchestrator import capability_space, commands
    from orchestrator.brain.registry import ToolFailed
    m = entry(home)
    folder = envs.folder(m)
    folder.mkdir(parents=True)
    (folder / 'original').write_bytes(b'kept')
    store = ProjectStore(home / 'work', runtime_root=folder.parent.parent)
    store.create('s1', document(m.key, folder.name))
    app = SimpleNamespace(hub=SimpleNamespace(loads=None, resources=SimpleNamespace(projects=store)),
                          downloads=SimpleNamespace(wire=lambda: []), installs=SimpleNamespace(wire=lambda: []),
                          catalog_changed=lambda: None)
    row = next(r for r in capability_space.inventory()['items'] if r['kind'] == 'environment' and m.key in r['models'])
    action = commands.model_uninstall(app, None, {'model': m.key}) if command == 'model' else commands.capability_remove(app, None, row)
    with pytest.raises(ToolFailed) as error: asyncio.run(action)
    assert 'Turn off' in error.value.next
    assert 'saved scientific' in str(error.value)
    assert folder.exists()



# A profile carried to another folder or Mac (the 0.1.0-beta layout: assets/results/projects beside catalog/envs). Its two
# retention records name their folders by absolute path; H12 C3b 16:58Z had to repair a copied profile's by hand.
def beta_profile(base, monkeypatch):
    profile = base / 'MicroscopeMind'
    monkeypatch.setenv('MICROMIND_CATALOG', str(profile / 'catalog'))
    runtime = profile / 'catalog' / 'envs'
    store = ProjectStore(profile / 'assets' / 'results' / 'projects', runtime_root=runtime)
    kept = runtime / 'kept' / ('c' * 16)
    kept.mkdir(parents=True)
    (kept / 'receipt.json').write_text('{"generation": "cccccccccccccccc"}')
    saved = store.create('s1', document('kept', 'c' * 16))
    store.save(saved.id, 's1', document('kept', 'd' * 16), expected_revision=1)
    return profile, saved


def tree(root):
    import hashlib
    return {str(p.relative_to(root)): (p.is_symlink(), hashlib.sha256(p.read_bytes()).hexdigest() if p.is_file() else None)
            for p in sorted(root.rglob('*')) if '.save-' not in p.name}


def carry(profile, to, monkeypatch, *, move=False, runtime_root=True):
    import shutil
    (shutil.move if move else lambda a, b: shutil.copytree(a, b, symlinks=True))(profile, to)
    monkeypatch.setenv('MICROMIND_CATALOG', str(to / 'catalog'))
    return ProjectStore(to / 'assets' / 'results' / 'projects', runtime_root=(to / 'catalog' / 'envs') if runtime_root else None)


def receipts(runtime):
    return sorted((runtime / retention.METADATA / retention.CARRIED).glob('*/receipt.json'))


def blocked_by_carried_record(runtime):
    with retention.locked(runtime), pytest.raises(retention.Uncertain, match='association is invalid'):
        retention.require_removable(runtime, models={'unrelated'})


@pytest.mark.parametrize('move', [False, True])
def test_carried_over_profile_opens_with_its_runtimes_and_prepares_again(tmp_path, monkeypatch, home, move):
    profile, saved = beta_profile(tmp_path / 'vm', monkeypatch)
    model = entry(home)
    fake_uv(home, monkeypatch, fail_at='pip sync')
    with pytest.raises(envs.Failed):
        envs.build(model)  # a preparation that failed on the older version leaves its generation folder
    original = None if move else tree(profile)
    store = carry(profile, tmp_path / 'HookeBetaProfile', monkeypatch, move=move)
    runtime = store.runtime_root.resolve()
    assert store.runtime_problem is None
    assert store.read(saved.id, 's1', revision=1).document == saved.document
    assert retention._roots(store.root) == [runtime]
    with retention.locked(runtime):
        assert [r['generation'] for r in retention.references(runtime)] == ['c' * 16, 'd' * 16]
        with pytest.raises(retention.Retained):
            retention.require_removable(runtime, models={'kept'})
    fake_uv(home, monkeypatch)
    assert (envs.build(model) / envs.RECEIPT).is_file(), 'the retry the copied VM profile refused'
    [kept] = receipts(runtime)
    receipt = json.loads(kept.read_text())
    assert receipt['state'] == 'complete' and len(receipt['records']) == 1
    old = (tmp_path / 'vm' / 'MicroscopeMind').resolve()
    assert receipt['records'][0]['value'] == {'store': str(old / 'assets/results/projects'), 'runtimeRoot': str(old / 'catalog/envs')}
    assert receipt['bindings']['before'] == [str(old / 'catalog/envs')]
    assert (kept.parent / receipt['records'][0]['name']).is_file() and (kept.parent / 'bindings.json').is_file()
    if original is not None:
        assert tree(profile) == original, 'the profile it was copied from is never written'
    else:
        assert not profile.exists(), 'nothing recreated where it used to be'
    ProjectStore(store.root, runtime_root=runtime).create('s1', document('kept', 'e' * 16))
    assert receipts(runtime) == [kept], 'once: a later open or save finds nothing carried'


def test_profile_opened_by_the_older_version_first_is_repaired_as_the_vm_needed(tmp_path, monkeypatch):
    """The VM's exact state: 0.1.0-beta opened the copy, registering it beside the original, then refused the retry."""
    profile, saved = beta_profile(tmp_path / 'vm', monkeypatch)
    with monkeypatch.context() as older:
        older.setattr(retention, '_rehome', lambda store, root, roots: roots)
        store = carry(profile, tmp_path / 'HookeBetaProfile', monkeypatch)
        runtime = store.runtime_root.resolve()
        blocked_by_carried_record(runtime)
    original = tree(profile)
    reopened = ProjectStore(store.root, runtime_root=runtime)
    assert reopened.runtime_problem is None and reopened.read(saved.id, 's1') == store.read(saved.id, 's1')
    with retention.locked(runtime):
        retention.require_removable(runtime, models={'unrelated'})
        with pytest.raises(retention.Retained):
            retention.require_removable(runtime, models={'kept'})
    old = profile.resolve() / 'catalog' / 'envs'
    assert retention._roots(store.root) == sorted([old, runtime], key=str), 'its registration there is real; kept'
    assert tree(profile) == original


def test_carried_record_without_proof_of_where_it_belongs_keeps_removal_blocked(tmp_path, monkeypatch):
    profile, _ = beta_profile(tmp_path / 'vm', monkeypatch)
    import shutil
    runtime = tmp_path / 'elsewhere' / 'envs'
    shutil.copytree(profile / 'catalog' / 'envs', runtime, symlinks=True)
    shutil.move(profile / 'assets', tmp_path / 'unmounted')  # its store is not where it was, nor moved with this folder
    ProjectStore(tmp_path / 'other' / 'projects', runtime_root=runtime)
    blocked_by_carried_record(runtime.resolve())
    assert not (runtime / retention.METADATA / retention.CARRIED).exists()


def test_carried_record_its_original_store_claims_keeps_removal_blocked(tmp_path, monkeypatch):
    profile, _ = beta_profile(tmp_path / 'vm', monkeypatch)
    store = carry(profile, tmp_path / 'copy', monkeypatch, runtime_root=False)
    runtime = (tmp_path / 'copy' / 'catalog' / 'envs').resolve()
    original_store = (profile / 'assets' / 'results' / 'projects').resolve()
    (original_store / retention.BINDINGS).write_text(json.dumps(sorted([str(profile.resolve() / 'catalog/envs'), str(runtime)])))
    ProjectStore(store.root, runtime_root=runtime)
    blocked_by_carried_record(runtime)


def test_runtime_folder_copied_alone_binds_both_without_losing_either(tmp_path, monkeypatch):
    profile, saved = beta_profile(tmp_path / 'vm', monkeypatch)
    import shutil
    first = (profile / 'catalog' / 'envs').resolve()
    second = tmp_path / 'second-envs'
    shutil.copytree(first, second, symlinks=True)
    store = ProjectStore(profile / 'assets' / 'results' / 'projects', runtime_root=second)
    assert store.runtime_problem is None
    assert retention._roots(store.root) == sorted([first, second.resolve()], key=str)
    for runtime in (first, second.resolve()):
        with retention.locked(runtime), pytest.raises(retention.Retained):
            retention.require_removable(runtime, models={'kept'})
    [receipt] = receipts(second.resolve())
    assert json.loads(receipt.read_text())['createdAssociation'] is True


def test_rehoming_that_does_not_verify_is_put_back_exactly(tmp_path, monkeypatch):
    profile, saved = beta_profile(tmp_path / 'vm', monkeypatch)
    import shutil
    copy = tmp_path / 'HookeBetaProfile'
    shutil.copytree(profile, copy, symlinks=True)
    before = tree(copy)
    original = retention._documents
    def mismatch(*args):
        yield from original(*args)
        yield {'bundle': 'invented', 'generation': 'f' * 16, 'operation': 'view'}
    monkeypatch.setattr(retention, '_documents', mismatch)
    store = ProjectStore(copy / 'assets' / 'results' / 'projects', runtime_root=copy / 'catalog' / 'envs')
    assert 'did not verify' in store.runtime_problem
    assert store.read(saved.id, 's1') == ProjectStore(profile / 'assets' / 'results' / 'projects').read(saved.id, 's1')
    [receipt] = receipts((copy / 'catalog' / 'envs').resolve())
    assert json.loads(receipt.read_text())['state'] == 'restored'
    assert {k: v for k, v in tree(copy).items() if retention.CARRIED not in k} == before


def test_restore_undoes_a_rehoming_from_its_receipt(tmp_path, monkeypatch):
    profile, _ = beta_profile(tmp_path / 'vm', monkeypatch)
    import shutil
    copy = tmp_path / 'HookeBetaProfile'
    shutil.copytree(profile, copy, symlinks=True)
    runtime = (copy / 'catalog' / 'envs').resolve()
    records = {p.name: p.read_bytes() for p in (runtime / retention.METADATA).glob('*.json')}
    bindings = (copy / 'assets/results/projects' / retention.BINDINGS).read_bytes()
    ProjectStore(copy / 'assets' / 'results' / 'projects', runtime_root=runtime)
    [receipt] = receipts(runtime)
    result = subprocess.run([sys.executable, '-m', 'orchestrator.scientific_retention', 'restore', str(runtime),
                             receipt.parent.name], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert {p.name: p.read_bytes() for p in (runtime / retention.METADATA).glob('*.json')} == records
    assert (copy / 'assets/results/projects' / retention.BINDINGS).read_bytes() == bindings
    assert json.loads(receipt.read_text())['state'] == 'restored'
    blocked_by_carried_record(runtime)


@pytest.mark.parametrize('linked', [retention.CARRIED, retention.METADATA])
def test_a_linked_retention_folder_is_refused_and_nothing_is_written_through_it(tmp_path, monkeypatch, linked):
    profile, saved = beta_profile(tmp_path / 'vm', monkeypatch)
    import shutil
    copy = tmp_path / 'HookeBetaProfile'
    shutil.copytree(profile, copy, symlinks=True)
    runtime = (copy / 'catalog' / 'envs').resolve()
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    if linked == retention.METADATA:  # its records moved out and the folder replaced by a link to them
        shutil.move(runtime / retention.METADATA, elsewhere / 'moved')
        (runtime / retention.METADATA).symlink_to(elsewhere / 'moved')
        outside = elsewhere / 'moved'
    else:
        (runtime / retention.METADATA / retention.CARRIED).symlink_to(elsewhere)
        outside = elsewhere
    before = tree(outside)
    store = ProjectStore(copy / 'assets' / 'results' / 'projects', runtime_root=runtime)
    assert 'redirected' in store.runtime_problem
    assert store.read(saved.id, 's1') == ProjectStore(profile / 'assets' / 'results' / 'projects').read(saved.id, 's1')
    assert tree(outside) == before, 'no receipt or record written through the link'
    if linked == retention.CARRIED:
        blocked_by_carried_record(runtime)  # the carried record stays in place: removal stays blocked
    else:
        with pytest.raises(retention.Uncertain, match='redirected'):
            with retention.locked(runtime): pass


@pytest.mark.parametrize('name', ['../../escape.json', 'absolute', 'receipt.json', 'f' * 64 + '.txt'])
def test_a_receipt_naming_anything_but_a_record_file_is_refused_with_nothing_written(tmp_path, monkeypatch, name):
    profile, _ = beta_profile(tmp_path / 'vm', monkeypatch)
    import shutil
    copy = tmp_path / 'HookeBetaProfile'
    shutil.copytree(profile, copy, symlinks=True)
    runtime = (copy / 'catalog' / 'envs').resolve()
    ProjectStore(copy / 'assets' / 'results' / 'projects', runtime_root=runtime)
    [receipt] = receipts(runtime)
    value = json.loads(receipt.read_text())
    (receipt.parent / value['records'][0]['name']).rename(receipt.parent / 'escape.json')
    value['records'][0]['name'] = str(tmp_path / 'escape.json') if name == 'absolute' else name
    receipt.write_text(json.dumps(value))
    before = tree(tmp_path)
    with pytest.raises(retention.Uncertain, match='receipt is invalid'):
        retention.restore(runtime, receipt.parent.name)
    assert tree(tmp_path) == before
    assert not (tmp_path / 'escape.json').exists() and not (runtime / 'escape.json').exists()


@pytest.mark.parametrize('name', ['missing', '..', 'a/b'])
def test_restore_of_no_such_receipt_creates_nothing(tmp_path, name):
    runtime = tmp_path / 'envs'
    ProjectStore(tmp_path / 'work', runtime_root=runtime)
    before = tree(tmp_path)
    with pytest.raises(retention.Uncertain, match='no such'):
        retention.restore(runtime, name)
    assert tree(tmp_path) == before
