"""Portable bundle bytes and recipient permission gates. No upstream packages or API calls."""
import io
import json
import os
import zipfile

import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, env_runner, envs, readiness, share
from tests.test_channels import home  # noqa: F401
from tests.test_bundle_runtime import contribute


def package(home):
    owner = contribute(home)
    folder = catalog.entry_folder(owner)
    adapters = folder / 'adapters'
    adapters.mkdir()
    (adapters / '__init__.py').write_text('')
    (folder / 'adapter.py').rename(adapters / 'operations.py')
    manifest = folder / 'model.toml'
    manifest.write_text(manifest.read_text().replace('adapter:', 'adapters.operations:'))
    (folder / 'LICENSE').write_text('Synthetic test integration, MIT\n')
    (folder / 'activation.json').write_text('{"ok":true}')
    (folder / 'private.key').write_text('not for sharing')
    (adapters / 'unrelated.py').write_text('not declared')
    catalog.manifests.cache_clear()
    return catalog.get(owner.key)


def archive(tmp_path, data, change):
    path = tmp_path / 'changed.observationui.zip'
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        files = {name: source.read(name) for name in source.namelist()}
    change(files)
    with zipfile.ZipFile(path, 'w') as target:
        for name, content in files.items():
            target.writestr(name, content)
    return path


def private_host(owner):
    from tests.test_bundle_manifest import HEAD
    folder = catalog.entry_folder(owner)
    (folder / 'model.toml').write_text(HEAD + '''
[[members]]
id = "viewer"
name = "Private scientific view"
adapter = "adapter:open_host"
bridge = "scientific-host-v1"
inputs = [{id = "project", type = "project.scientific"}]
outputs = [{id = "result", type = "project.scientific"}]
''')
    catalog.manifests.cache_clear()
    owner = catalog.get(owner.key)
    assert owner is not None, catalog.problems()
    return owner


def test_private_scientific_host_cannot_be_shared_as_a_usable_tool(home, monkeypatch):
    from orchestrator import catalog_view, publish, publish_api

    owner = private_host(contribute(home))
    # Private development packaging remains possible; it confers no readiness.
    assert share.pack(owner)
    assert not catalog_view.shareable(owner)
    with pytest.raises(share.CantShare, match='private scientific.*qualification'):
        share.export(owner.key)
    assert not (share.root() / 'outbox').exists()
    for proven in (True, False):
        assert any('private scientific' in reason for reason in publish.problems(owner, proven=proven))
    monkeypatch.setattr(catalog, 'mine', lambda *args: {'mine/' + owner.id: owner})
    monkeypatch.setattr(publish_api, '_api', lambda: pytest.fail('Private candidate must refuse before GitHub access'))
    for destination in ('review', 'community'):
        with pytest.raises(publish.CantPublish, match='private scientific'):
            publish_api.prepare(owner.id, destination=destination)


def test_supported_local_host_prepares_without_activation_or_public_qualification(home, monkeypatch):
    from orchestrator import bundle_review, catalog_view, own_trials
    from tests.test_env_runner import standin_uv

    owner = private_host(contribute(home))
    standin_uv(home, monkeypatch)
    r = readiness.model(owner.key, {})
    assert r.status == 'needs-setup' and r.next.kind == 'workflow'
    assert not envs.root(owner).exists(), 'Reading readiness never prepares code'
    folder, lines = envs.install(owner)
    assert folder.is_dir() and any('local workflow' in line for line in lines)
    assert env_runner.in_use(owner) is None
    assert own_trials.pending(owner)
    assert readiness.model(owner.key, {}).next.kind == 'workflow'
    card = next(row for row in catalog_view.rows({}, {}, {}) if row['id'] == owner.key)
    assert card['status'] == 'needs-setup'
    assert not any(fact.get('install') for fact in card['facts']), 'No repeated automatic proof loop'
    assert not catalog_view.shareable(owner)
    with pytest.raises(ValueError, match='Complete the package checks'):
        bundle_review.accept(owner.key, folder.name, 'a' * 64)
    with pytest.raises(share.CantShare, match='private scientific.*qualification'):
        share.export(owner.key)


def test_unsupported_host_still_refuses_ordinary_install_without_side_effects(home, monkeypatch):
    from orchestrator import bundle_admission
    from tests.test_env_runner import standin_uv

    owner = contribute(home)
    manifest = catalog.entry_folder(owner) / 'model.toml'
    operation = manifest.read_text().split('[[members]]')[1]
    owner = private_host(owner)
    manifest.write_text(manifest.read_text() + '\n[[members]]' + operation)
    catalog.manifests.cache_clear()
    owner = catalog.get(owner.key)
    assert owner is not None, catalog.problems()
    assert bundle_admission.local_problem(owner)
    standin_uv(home, monkeypatch)
    assert readiness.model(owner.key, {}).status == 'unavailable'
    queue = envs.Installs()
    with pytest.raises(envs.Failed, match='private scientific-host'):
        queue.start(owner.key)
    with pytest.raises(envs.Failed, match='private scientific-host'):
        envs.install(owner)
    assert not envs.root(owner).exists() and not queue.wire()


def test_private_replacement_does_not_hide_or_export_over_the_accepted_bundle(home, monkeypatch):
    from orchestrator import catalog_view, publish
    from tests.test_env_runner import standin_uv

    owner = contribute(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    active = env_runner.in_use(owner)
    before = share.export(owner.key).read_bytes()
    candidate = private_host(owner)
    assert env_runner.in_use(candidate).folder == active.folder
    assert readiness.model(candidate.key, {}).status == 'ready'
    assert catalog_view.shareable(candidate)
    exported = share.export(candidate.key).read_bytes()
    with zipfile.ZipFile(io.BytesIO(before)) as old, zipfile.ZipFile(io.BytesIO(exported)) as new:
        assert new.namelist() == old.namelist()
        for name in new.namelist():
            if name != 'SHARED.json':
                assert new.read(name) == old.read(name)
        assert b'scientific-host-v1' not in new.read('catalog/models/image-tools/model.toml')
    # Publication proposes the new contribution, so its own gate still refuses it.
    assert any('private scientific' in reason for reason in publish.problems(candidate))


def test_bundle_modules_references_and_attribution_reach_an_isolated_profile_without_proof(home, monkeypatch, tmp_path):
    owner = package(home)
    data = share.pack(owner, by='Maya', proof={'ok': True, 'cases': [{'ok': True}]})
    path = tmp_path / 'tools.observationui.zip'
    path.write_bytes(data)
    with zipfile.ZipFile(path) as z:
        shared = json.loads(z.read('SHARED.json'))
        assert shared['format'] == 3 and shared['proof'] == {}
        names = {n.removeprefix('catalog/models/image-tools/') for n in z.namelist()}
        assert names == {'SHARED.json', 'model.toml', 'env.lock', 'LICENSE', 'adapters/__init__.py',
                         'adapters/operations.py', 'example.png', 'reference-invert.png', 'reference-mean.json'}
    reviewed = tmp_path / 'recipient-reviewed'
    reviewed.mkdir()
    monkeypatch.setattr(catalog, 'ROOT', reviewed)
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'recipient'))
    catalog.manifests.cache_clear()
    opened = share.open_file(path)
    received = catalog.get(opened['model'])
    assert received is not None, catalog.problems()
    assert received.members and received.contributed_by == {'name': 'Maya'}
    folder = catalog.entry_folder(received)
    assert (folder / 'adapters/operations.py').read_bytes() == (catalog.entry_folder(owner) / 'adapters/operations.py').read_bytes()
    assert readiness.model(received.key, {}).steps[0].kind == 'trust'
    assert env_runner.in_use(received) is None
    channels.trust(opened['channel'])
    assert readiness.model(received.key, {}).steps[0].kind == 'install'
    assert not envs.python(received)
    assert share.open_file(path)['model'] == received.key
    from tests.test_env_runner import standin_uv
    from orchestrator import bundle_runtime
    import numpy as np
    standin_uv(home, monkeypatch)
    envs.install(received)
    active = env_runner.in_use(received)
    assert active.proof['ok'] and len(active.proof['cases']) == 2
    member = bundle_runtime.expand(active)[0]
    result = env_runner.client(member).run(np.full((16, 16, 3), 32, np.uint8), {'offset': 0}, output='image.2d.rgb8')
    np.testing.assert_array_equal(result['picture'], np.full((16, 16, 3), 223, np.uint8))


def test_publication_checks_declared_bundle_modules_and_excludes_private_neighbors(home):
    from orchestrator import publish, publish_api

    owner = package(home)
    assert publish.problems(owner, proven=False) == []
    with zipfile.ZipFile(io.BytesIO(share.pack(owner))) as portable:
        expected = {name: portable.read(name) for name in portable.namelist() if name != 'SHARED.json'}
    assert publish_api._files(owner, {}) == expected
    # A second undeclared module must not be distributed just because it is nearby.
    assert all('private.key' not in name and 'unrelated.py' not in name for name in expected)


@pytest.mark.parametrize('name', ['adapters/operations.py', 'reference-mean.json'])
def test_publication_reports_missing_bundle_content_before_external_access(home, monkeypatch, name):
    from orchestrator import publish, publish_api

    owner = package(home)
    (catalog.entry_folder(owner) / name).unlink()
    monkeypatch.setattr(catalog, 'mine', lambda *args: {'mine/' + owner.id: owner})
    monkeypatch.setattr(publish_api, '_api', lambda: pytest.fail('Missing package must refuse before account access'))
    reasons = publish.problems(owner, proven=False)
    assert any('missing declared' in reason and name in reason for reason in reasons)
    with pytest.raises(publish.CantPublish, match='missing declared'):
        publish_api.prepare(owner.id)


def test_bundle_local_proposal_keeps_all_reference_evidence_outside_catalog(home, monkeypatch):
    import hashlib
    from orchestrator import bundle_review, publish
    from tests.test_env_runner import standin_uv
    from tests.test_publish import contribution_repositories

    owner = package(home)
    standin_uv(home, monkeypatch)
    envs.install(owner)
    active = env_runner.in_use(owner)
    assert publish.problems(owner) == []
    repo, _, _ = contribution_repositories(home, monkeypatch)
    destination = publish._destination(repo, 'origin', 'upstream', 'release', 'catalog/bundle-review')
    prepared = publish._prepare(owner, repo, destination, home / 'bundle-proposal', os.environ)
    proposal, payload = publish._read_package(prepared, owner.id)
    proof = json.loads(payload['proof.json'])
    assert len(proof['cases']) == 2
    for case in proof['cases']:
        for descriptor in case['artifacts'].values():
            retained = payload['proof-artifacts/' + descriptor['sha256']]
            assert hashlib.sha256(retained).hexdigest() == descriptor['sha256']
            assert retained == bundle_review.read(active, descriptor)
    assert 'Bundle reference checks' in payload['body.md'].decode()
    assert 'not independent qualification' in payload['body.md'].decode()
    assert 'Adapter contract smoke' not in payload['body.md'].decode()
    portable = share.portable_files(owner)
    assert {name: data for name, data in payload.items() if name.startswith('catalog/')} == {
        f'catalog/models/{owner.id}/{name}': data for name, data in portable.items()}
    patch = payload['proposal.patch'].decode()
    assert 'private.key' not in patch and 'proof-artifacts' not in patch and 'unrelated.py' not in patch
    # Corrupt retained actual pixels: preparation must fail before creating a proposal.
    descriptor = proof['cases'][0]['artifacts']['actual']
    (bundle_review.folder(active) / descriptor['sha256']).write_bytes(b'damaged')
    with pytest.raises(publish.CantPublish, match='reference evidence.*changed'):
        publish._prepare(owner, repo, destination, home / 'bad-proposal', os.environ)
    assert not (home / 'bad-proposal').exists()


def test_bundle_publication_never_reads_a_linked_lockfile(home):
    from orchestrator import publish

    owner = package(home)
    lock = catalog.entry_folder(owner) / 'env.lock'
    lock.unlink()
    outside = home / 'private-not-a-lock'
    outside.write_bytes(b'PRIVATE_SENTINEL\xff')
    lock.symlink_to(outside)
    reasons = publish.problems(owner, proven=False)
    assert any('links are refused' in reason for reason in reasons)
    assert all('PRIVATE_SENTINEL' not in reason for reason in reasons)


def test_bundle_api_review_retains_evidence_but_never_in_the_outgoing_files(home, monkeypatch):
    from pathlib import Path
    import shutil
    from orchestrator import bundle_review, publish, publish_api
    from tests.test_env_runner import standin_uv
    from tests.test_publish_api import GitHub

    owner = package(home)
    local = home / 'catalog/mine/models' / owner.id
    local.parent.mkdir(parents=True)
    shutil.move(catalog.entry_folder(owner), local)
    catalog.manifests.cache_clear()
    owner = catalog.mine()['mine/image-tools']
    standin_uv(home, monkeypatch)
    envs.install(owner)
    active = env_runner.in_use(owner)
    api = GitHub()
    prepared = publish_api.prepare(owner.id, http=api)
    folder = Path(prepared['package'])
    proposal, outgoing, body = publish_api._read(folder)
    assert proposal['evidence'] and 'proof.json' in proposal['evidence']
    assert all(path.startswith('catalog/') for path in outgoing)
    assert all(method == 'GET' for method, _, _ in api.calls)
    assert 'not independent qualification' in body
    local_proof = json.loads((folder / 'proof.json').read_bytes())
    descriptor = local_proof['cases'][0]['artifacts']['actual']
    artifact = folder / 'proof-artifacts' / descriptor['sha256']
    assert artifact.read_bytes() == bundle_review.read(active, descriptor)
    artifact.write_bytes(b'changed after review')
    with pytest.raises(publish.CantPublish, match='changed since you reviewed'):
        publish_api._read(folder)
    # Missing original evidence refuses before even reading the account.
    (bundle_review.folder(active) / descriptor['sha256']).unlink()
    monkeypatch.setattr(publish_api, '_api', lambda: pytest.fail('Evidence must be checked before GitHub access'))
    with pytest.raises(publish.CantPublish, match='reference evidence'):
        publish_api.prepare(owner.id)


@pytest.mark.parametrize('name', ['adapters/operations.py', 'env.lock', 'example.png', 'reference-mean.json'])
def test_missing_declared_content_is_refused_before_import(home, tmp_path, name):
    owner = package(home)
    data = share.pack(owner)
    path = archive(tmp_path, data, lambda files: files.pop('catalog/models/image-tools/' + name))
    with pytest.raises(share.CantShare, match='missing declared'):
        share.open_file(path)
    assert not (share.root() / 'repos').exists()
    (catalog.entry_folder(owner) / name).unlink()
    with pytest.raises(share.CantShare, match='missing declared'):
        share.pack(owner)


@pytest.mark.parametrize('name', ['adapters/secret.py', '../secret.py', 'adapters/../secret.py', 'activation.json', 'weights.pt'])
def test_archive_rejects_undeclared_modules_state_and_paths(home, tmp_path, name):
    data = share.pack(package(home))
    path = archive(tmp_path, data, lambda files: files.update({'catalog/models/image-tools/' + name: b'secret'}))
    with pytest.raises(share.CantShare, match='no catalog entry|noncanonical'):
        share.open_file(path)
    assert not (share.root() / 'repos').exists()


@pytest.mark.parametrize('name', ['adapters', 'adapters/operations.py', 'reference-mean.json'])
def test_export_refuses_links_in_every_declared_path_component(home, tmp_path, name):
    owner = package(home)
    path = catalog.entry_folder(owner) / name
    outside = tmp_path / 'outside'
    path.rename(outside)
    path.symlink_to(outside, target_is_directory=outside.is_dir())
    with pytest.raises(share.CantShare, match='links|regular'):
        share.pack(owner)


def test_bundle_cannot_be_downgraded_to_legacy_share(home, tmp_path):
    data = share.pack(package(home))
    def downgrade(files):
        shared = json.loads(files['SHARED.json'])
        shared['format'] = 1
        files['SHARED.json'] = json.dumps(shared).encode()
    path = archive(tmp_path, data, downgrade)
    with pytest.raises(share.CantShare, match='require share format 3'):
        share.open_file(path)


def test_old_share_reader_rejects_format_three_explicitly(home, tmp_path, monkeypatch):
    data = share.pack(package(home))
    path = tmp_path / 'new.observationui.zip'
    path.write_bytes(data)
    monkeypatch.setattr(share, 'RUNNERS', {1: 'python-env', 2: 'fiji'})
    with pytest.raises(share.CantShare, match="kind of share this app doesn't read"):
        share.open_file(path)


def test_export_uses_the_active_immutable_bundle_snapshot(home, monkeypatch, tmp_path):
    owner = package(home)
    snapshot = tmp_path / 'snapshot' / 'image-tools'
    import shutil
    shutil.copytree(catalog.entry_folder(owner), snapshot)
    saved, errors = catalog.read(snapshot / 'model.toml')
    assert saved is not None, errors
    monkeypatch.setattr(env_runner, 'in_use', lambda *args: type('Installed', (), {'m': saved, 'proof': {}})())
    (catalog.entry_folder(owner) / 'adapters/operations.py').write_text('unaccepted edits')
    path = share.export(owner.key)
    with zipfile.ZipFile(path) as z:
        assert z.read('catalog/models/image-tools/adapters/operations.py') == (snapshot / 'adapters/operations.py').read_bytes()


@pytest.mark.parametrize('damage', ['duplicate', 'symlink', 'oversize'])
def test_bundle_archive_keeps_duplicate_link_and_size_bounds(home, tmp_path, damage):
    data = share.pack(package(home))
    path = tmp_path / 'damaged.observationui.zip'
    module = 'catalog/models/image-tools/adapters/operations.py'
    with zipfile.ZipFile(io.BytesIO(data)) as source, zipfile.ZipFile(path, 'w') as target:
        for name in source.namelist():
            info = zipfile.ZipInfo(name)
            content = source.read(name)
            if name == module and damage == 'symlink':
                info.external_attr = 0o120777 << 16
                content = b'/etc/passwd'
            if name == module and damage == 'oversize':
                content = b'x' * (share.MAX_FILE + 1)
            target.writestr(info, content)
        if damage == 'duplicate':
            target.writestr(module.upper(), b'different code')
    with pytest.raises(share.CantShare, match='duplicate|link|too big'):
        share.open_file(path)
    assert not (share.root() / 'repos').exists()


def test_bundle_export_keeps_file_count_limit(home, monkeypatch):
    owner = package(home)
    monkeypatch.setattr(share, 'MAX_ENTRIES', 3)
    with pytest.raises(share.CantShare, match='too many files'):
        share.pack(owner)


def test_archive_source_link_is_refused_before_channel_creation(home, tmp_path):
    actual = tmp_path / 'actual.observationui.zip'
    actual.write_bytes(share.pack(package(home)))
    link = tmp_path / 'linked.observationui.zip'
    link.symlink_to(actual)
    with pytest.raises(share.CantShare):
        share.open_file(link)
    assert not (share.root() / 'repos').exists()
