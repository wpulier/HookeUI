"""Portable dependency artifacts: exact bytes, no owner paths or automatic trust."""
import hashlib
import io
import json
from pathlib import Path
import zipfile

import pytest

from gpu_worker import manifests as catalog
from orchestrator import envs, share
from tests.test_channels import home  # noqa: F401
from tests.test_bundle_runtime import contribute

WHEEL = 'tiny_science-1.0-py3-none-any.whl'


def wheel_owner(home, data=None):
    owner = contribute(home)
    folder = catalog.entry_folder(owner)
    if data is None:
        data = b'synthetic wheel bytes; installer execution is tested separately'
    sha = hashlib.sha256(data).hexdigest()
    text = (folder / 'model.toml').read_text().replace('catalog = 3', 'catalog = 4')
    text = text.replace('imports = ["numpy"]', f'imports = ["numpy"], wheels = [{{file = "{WHEEL}", sha256 = "{sha}", bytes = {len(data)}}}]')
    (folder / 'model.toml').write_text(text)
    (folder / WHEEL).write_bytes(data)
    (folder / 'env.lock').write_text(f'tiny-science @ hooke-wheel:{WHEEL} --hash=sha256:{sha}\n')
    catalog.manifests.cache_clear()
    owner = catalog.get(owner.key)
    assert owner is not None, catalog.problems()
    return owner, data


def test_portable_wheel_roundtrip_retains_bytes_and_requires_recipient_permission(home, tmp_path, monkeypatch):
    owner, data = wheel_owner(home)
    package = share.pack(owner)
    with zipfile.ZipFile(io.BytesIO(package)) as archive:
        assert json.loads(archive.read('SHARED.json'))['format'] == 4
        assert archive.read(f'catalog/models/{owner.id}/{WHEEL}') == data
        assert b'hooke-wheel:' in archive.read(f'catalog/models/{owner.id}/env.lock')
    path = tmp_path / 'candidate.observationui.zip'; path.write_bytes(package)
    recipient = tmp_path / 'recipient'
    opened = share.open_file(path, {'MICROMIND_CATALOG': str(recipient)})
    assert opened['kind'] == 'shared'
    assert not (recipient / 'envs').exists(), 'Import must not install or activate code'


def test_older_reader_refuses_portable_recipe_before_interpreting_it(home, monkeypatch):
    owner, _ = wheel_owner(home)
    monkeypatch.setattr(catalog, 'CATALOG_FORMAT', 3)
    parsed, errors = catalog.read(catalog.entry_folder(owner) / 'model.toml', 'mine')
    assert parsed is None and isinstance(errors[0], catalog.Incompatible)


def test_prepared_lock_uses_only_verified_copied_artifacts(home, tmp_path):
    from orchestrator.environment_wheels import prepare
    owner, data = wheel_owner(home)
    lock = prepare(owner, tmp_path / 'runtime')
    assert lock is not None and lock.parent == tmp_path / 'runtime' / '.wheel-inputs'
    resolved = lock.read_text()
    assert 'hooke-wheel:' not in resolved
    assert (lock.parent / WHEEL).as_uri() + '#sha256=' + hashlib.sha256(data).hexdigest() in resolved
    assert (lock.parent / WHEEL).read_bytes() == data
    assert 'hooke-wheel:' in envs.lock_file(owner).read_text(), 'Portable source recipe remains unchanged'


@pytest.mark.parametrize('mutation', ['hash', 'size', 'missing', 'link', 'undeclared', 'local-url', 'path', 'include'])
def test_invalid_portable_recipe_refuses_before_preparation(home, tmp_path, mutation):
    from orchestrator.environment_wheels import prepare
    owner, _ = wheel_owner(home)
    folder = catalog.entry_folder(owner)
    if mutation == 'hash': (folder / WHEEL).write_bytes(b'x' * (folder / WHEEL).stat().st_size)
    elif mutation == 'size': (folder / WHEEL).write_bytes(b'x')
    elif mutation == 'missing': (folder / WHEEL).unlink()
    elif mutation == 'link':
        target = tmp_path / 'outside.whl'; (folder / WHEEL).rename(target); (folder / WHEEL).symlink_to(target)
    else:
        replacements = {'undeclared':'other-1-py3-none-any.whl', 'local-url':'file:///owner/private.whl',
                        'path':'../outside.whl', 'include':'-r /owner/private.lock'}
        text = envs.lock_file(owner).read_text()
        if mutation == 'undeclared': text = text.replace(WHEEL, replacements[mutation])
        elif mutation == 'path': text = text.replace(WHEEL, replacements[mutation])
        else: text += replacements[mutation] + '\n'
        envs.lock_file(owner).write_text(text)
    with pytest.raises((ValueError, envs.Failed)):
        prepare(owner, tmp_path / 'runtime')
    assert not (tmp_path / 'runtime' / 'receipt.json').exists()


def test_tampered_shared_wheel_is_refused_before_channel_import(home, tmp_path):
    owner, _ = wheel_owner(home)
    from tests.test_bundle_share import archive
    path = archive(tmp_path, share.pack(owner), lambda files: files.__setitem__(
        f'catalog/models/{owner.id}/{WHEEL}', b'tampered'))
    recipient = tmp_path / 'recipient'
    with pytest.raises(share.CantShare): share.open_file(path, {'MICROMIND_CATALOG': str(recipient)})
    assert not recipient.exists()


@pytest.mark.parametrize('option', ['--find-links=file:///private', '-r /private/lock',
                                  '--index-url=https://other.invalid', '--no-binary=:all:'])
@pytest.mark.parametrize('position', ['before-hash', 'after-hash'])
def test_installer_options_cannot_hide_inside_a_pinned_line(home, tmp_path, option, position):
    from orchestrator.environment_wheels import prepare
    owner, _ = wheel_owner(home)
    lock = envs.lock_file(owner)
    text = lock.read_text().strip()
    text = text.replace(' --hash=', f' {option} --hash=') if position == 'before-hash' else text + ' ' + option
    lock.write_text(text + '\n')
    with pytest.raises(ValueError):
        prepare(owner, tmp_path / 'runtime')
    with pytest.raises(share.CantShare):
        share.pack(owner)


def test_marker_and_hash_continuations_remain_portable(home, tmp_path):
    from orchestrator.environment_wheels import prepare
    owner, _ = wheel_owner(home)
    lock = envs.lock_file(owner)
    lock.write_text(lock.read_text() + 'other==1.0 ; sys_platform == "darwin" \\\n'
                    '  --hash=sha256:' + 'a' * 64 + '\n')
    assert 'sys_platform == "darwin"' in prepare(owner, tmp_path / 'runtime').read_text()


def test_preparation_rejects_linked_parent(home, tmp_path):
    from dataclasses import replace
    from orchestrator.environment_wheels import prepare
    owner, _ = wheel_owner(home)
    link = tmp_path / 'linked-owner'
    link.symlink_to(catalog.entry_folder(owner), target_is_directory=True)
    with pytest.raises(ValueError, match='linked'):
        prepare(replace(owner, folder=str(link)), tmp_path / 'runtime')


def test_large_declared_wheel_survives_public_workspace_packaging(home, tmp_path):
    import shutil
    from orchestrator import capability_workspace as workspaces, contribute as authoring
    owner, data = wheel_owner(home, b'x' * (share.MAX_FILE + 1))
    environ = {'MICROMIND_CATALOG': str(tmp_path / 'workspace-profile')}
    item = workspaces.create('Synthetic wheel packaging regression', environ=environ)
    shutil.copytree(catalog.entry_folder(owner), item.candidate)
    path = item.candidate / 'model.toml'
    path.write_text(path.read_text().replace(owner.id, item.candidate_id))
    findings = authoring.preflight(str(item.candidate), environ, workspace=item.folder)
    assert not [finding.says for finding in findings if finding.blocks]
    path = workspaces.package(item.id, environ=environ)
    _, _, files = share._read(path)
    assert files[WHEEL] == data


def test_result_carry_refuses_oversize_wheel_before_reading_it(home, monkeypatch):
    from orchestrator import result_entries
    owner, _ = wheel_owner(home, b'x' * (share.MAX_TOTAL + 1))
    original = share._read_relative
    def bounded(directory, name, maximum=share.MAX_FILE):
        if name == WHEEL:
            assert maximum <= share.MAX_TOTAL, 'Result carry must bound the read before allocating wheel bytes'
        return original(directory, name, maximum)
    monkeypatch.setattr(share, '_read_relative', bounded)
    with pytest.raises(share.CantShare, match='too big'):
        result_entries._retain(owner)


def test_installer_uses_private_wheel_lock_and_retains_portable_source(home, monkeypatch):
    from tests.test_env_runner import standin_uv
    owner, data = wheel_owner(home)
    log = standin_uv(home, monkeypatch)
    runtime = envs.build(owner)
    assert str(runtime / '.wheel-inputs/resolved.lock') in log.read_text()
    assert (runtime / envs.SNAPSHOT / owner.id / WHEEL).read_bytes() == data
    assert 'hooke-wheel:' in (runtime / envs.SNAPSHOT / owner.id / 'env.lock').read_text()


def test_candidate_change_during_install_cannot_publish_a_receipt(home, monkeypatch):
    from tests.test_env_runner import standin_uv
    owner, _ = wheel_owner(home)
    standin_uv(home, monkeypatch)
    runtime = envs.folder(owner)
    original = envs.copy
    def changed(manifest, destination, environ):
        envs.lock_file(manifest, environ).write_text('different==1 --hash=sha256:' + 'a' * 64 + '\n')
        return original(manifest, destination, environ)
    monkeypatch.setattr(envs, 'copy', changed)
    with pytest.raises(envs.Failed, match='changed during preparation'):
        envs.build(owner)
    assert not (runtime / envs.RECEIPT).exists()


def test_change_after_generation_selection_cannot_install_under_old_identity(home, monkeypatch):
    from tests.test_env_runner import standin_uv
    owner, _ = wheel_owner(home)
    log = standin_uv(home, monkeypatch)
    runtime = envs.folder(owner)
    original = envs.folder
    def changed(manifest, environ):
        selected = original(manifest, environ)
        adapter = catalog.entry_folder(manifest, environ) / 'adapter.py'
        adapter.write_text(adapter.read_text() + '\n# concurrent edit\n')
        return selected
    monkeypatch.setattr(envs, 'folder', changed)
    with pytest.raises(envs.Failed, match='changed during preparation'):
        envs.build(owner)
    assert 'pip sync' not in log.read_text()
    assert not (runtime / envs.RECEIPT).exists()


def test_portable_origin_repair_versions_generation_without_rewriting_old_receipts(home):
    owner, _ = wheel_owner(home)
    legacy = hashlib.sha256(envs.lock_file(owner).read_bytes())
    legacy.update(str(owner.env['python']).encode())
    legacy.update(envs.entry_digest(owner).encode())
    old = envs.root(owner) / legacy.hexdigest()[:16]
    old.mkdir(parents=True)
    receipt = old / envs.RECEIPT
    receipt.write_text('retained original evidence')
    assert envs.generation(owner) != old.name
    assert receipt.read_text() == 'retained original evidence'
    ordinary = contribute(home)
    expected = hashlib.sha256(envs.lock_file(ordinary).read_bytes())
    expected.update(str(ordinary.env['python']).encode())
    expected.update(envs.entry_digest(ordinary).encode())
    assert envs.generation(ordinary) == expected.hexdigest()[:16]


@pytest.mark.parametrize('historical', [False, True])
def test_exact_portable_generations_remain_admissible_across_preparation_versions(home, monkeypatch, historical):
    from tests.test_env_runner import standin_uv
    from orchestrator import prepared_bundles
    owner, _ = wheel_owner(home)
    standin_uv(home, monkeypatch)
    original = envs._generation_from_files
    with monkeypatch.context() as before:
        if historical:
            before.setattr(envs, '_generation_from_files', lambda m, environ: original(m, environ, wheel_preparation=0))
        runtime = envs.build(owner)
    path = runtime / envs.RECEIPT
    receipt = json.loads(path.read_text())
    assert receipt['wheelPreparation'] == 1
    if historical:
        receipt.pop('wheelPreparation')
        path.write_text(json.dumps(receipt))
    retained = path.read_bytes()
    with prepared_bundles.prepared_member(owner.key + '#mean', runtime.name) as prepared:
        assert prepared.folder == runtime
    assert path.read_bytes() == retained
    assert (envs.generation(owner) != runtime.name) == historical
    receipt['wheelPreparation'] = True
    path.write_text(json.dumps(receipt))
    with pytest.raises(prepared_bundles.NotPrepared):
        with prepared_bundles.prepared_member(owner.key + '#mean', runtime.name):
            pytest.fail('invalid preparation version admitted')
