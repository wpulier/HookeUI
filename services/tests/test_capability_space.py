"""The Space API removes only managed runtime copies; original work and proof inputs remain byte-identical."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from gpu_worker import manifests as catalog, model_files
from tests.test_downloads import home, model, REV_A


def test_remove_published_read_only_download_and_prepare_again(home):
    import httpx
    from orchestrator import capability_space, downloads
    from tests.test_downloads import Server

    m = model()
    with httpx.Client(transport=httpx.MockTransport(Server())) as client:
        revision = downloads.download(m, client=client)
        assert not revision.stat().st_mode & 0o200
        app = SimpleNamespace(hub=SimpleNamespace(loads=None), downloads=SimpleNamespace(wire=lambda: []),
                              installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
        row = capability_space.inventory()['items'][0]
        assert asyncio.run(capability_space.remove(app, row['id'], row['revision']))['ok']
        assert not revision.exists() and not capability_space.inventory()['items']
        assert downloads.download(m, client=client) == revision
        assert model_files.published(m) == revision


def test_failed_cleanup_stays_visible_and_retry_finishes_without_touching_original_work(home, monkeypatch):
    from unittest.mock import AsyncMock
    from orchestrator import capability_space

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'original')
    (revision / 'receipt.json').write_text('{}')
    original = revision.parent / '.proof-input.npy'
    original.write_bytes(b'keep original scientific input')
    app = SimpleNamespace(hub=SimpleNamespace(loads=None), downloads=SimpleNamespace(wire=lambda: []),
                          installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
    row = capability_space.inventory()['items'][0]
    def fail(folder):
        (folder / 'receipt.json').unlink()
        raise PermissionError('controlled cleanup failure')
    with monkeypatch.context() as patch:
        patch.setattr(capability_space.runtime_cleanup.shutil, 'rmtree', fail)
        with pytest.raises(ValueError, match='Refresh storage.*finish'):
            asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert not revision.exists()  # A partially deleted revision cannot become executable again.
    pending = capability_space.inventory()['items'][0]
    assert pending['pendingRemoval'] == 1 and pending['bytes'] == len(b'original')
    # A later preparation may reuse the original generation's pathname. Cleanup only owns its withdrawn copy.
    revision.mkdir()
    (revision / 'receipt.json').write_text('{}')
    (revision / 'weights.bin').write_bytes(b'reprepared working version')
    original.chmod(0o400)
    mode = original.stat().st_mode
    pending_folder = next(revision.parent.glob('.removing-*'))
    (pending_folder / 'outside-link').symlink_to(original)
    (pending_folder / 'shared-hardlink').hardlink_to(original)
    pending = next(row for row in capability_space.inventory()['items'] if row.get('pendingRemoval'))
    unload = AsyncMock(return_value={'unloaded': True})
    app.hub.loads = SimpleNamespace(clients=[SimpleNamespace(loading={}, resident={m.key: {}})], let_go=unload)
    assert asyncio.run(capability_space.remove(app, pending['id'], pending['revision']))['ok']
    assert all(not row.get('pendingRemoval') for row in capability_space.inventory()['items'])
    assert (revision / 'weights.bin').read_bytes() == b'reprepared working version'
    unload.assert_not_called()
    assert original.read_bytes() == b'keep original scientific input'
    assert original.stat().st_mode == mode


def test_inventory_counts_shared_files_once_and_remove_preserves_original_work(home):
    from orchestrator import capability_space

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'1234567890')
    (revision / 'receipt.json').write_text('{}')
    # The model entry, source data, evidence and proof input are all outside the removable runtime revision.
    originals = [home / 'results' / 'result.png', home / 'datasets' / 'source.tif',
                 home / 'catalog' / 'envs' / 'test-finder' / 'abc.proof-input.npy']
    for p in originals:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'irreplaceable original ' + p.name.encode())
    kept = {p: p.read_bytes() for p in originals}
    app = SimpleNamespace(hub=SimpleNamespace(loads=None),
                          downloads=SimpleNamespace(wire=lambda: []), installs=SimpleNamespace(wire=lambda: []),
                          catalog_changed=lambda: None)
    rows = capability_space.inventory()
    row = next(r for r in rows['items'] if m.key in r['models'])
    assert row['bytes'] == 12 and row['kind'] == 'files'
    asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert not revision.exists()
    assert all(p.read_bytes() == data for p, data in kept.items())
    assert catalog.entry_folder(m).is_dir()
    assert not any(r for r in capability_space.inventory()['items'] if r['id'] == row['id'])


def test_changed_inventory_and_in_progress_download_cannot_be_removed(home):
    from orchestrator import capability_space

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'original')
    (revision / 'receipt.json').write_text('{}')
    row = capability_space.inventory()['items'][0]
    app = SimpleNamespace(hub=SimpleNamespace(loads=None),
                          downloads=SimpleNamespace(wire=lambda: [{'model': m.key, 'state': 'downloading'}]),
                          installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
    with pytest.raises(ValueError, match='finish|running|progress'):
        asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert (revision / 'weights.bin').read_bytes() == b'original'
    app.downloads = SimpleNamespace(wire=lambda: [])
    (revision / 'weights.bin').write_bytes(b'changed since review')
    with pytest.raises(ValueError, match='changed|refresh'):
        asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert revision.exists()


def test_remove_refuses_while_the_app_has_an_owned_request_using_its_runtime(home):
    from orchestrator import capability_space, ticket

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'original')
    (revision / 'receipt.json').write_text('{}')
    row = capability_space.inventory()['items'][0]
    app = SimpleNamespace(hub=SimpleNamespace(loads=None, scene=SimpleNamespace()),
                          downloads=SimpleNamespace(wire=lambda: []), installs=SimpleNamespace(wire=lambda: []),
                          catalog_changed=lambda: None)
    request = ticket.issue(app.hub, 'command')
    try:
        with pytest.raises(ValueError, match='finish|running|work'):
            asyncio.run(capability_space.remove(app, row['id'], row['revision']))
        assert revision.exists()
    finally:
        request.seal()


def test_storage_inventory_never_follows_a_catalog_parent_symlink(home):
    from orchestrator import capability_space

    outside = home / 'original-work'
    revision = outside / 'test-finder' / REV_A
    revision.mkdir(parents=True)
    (revision / 'receipt.json').write_text('{}')
    (revision / 'important.bin').write_bytes(b'keep me')
    (home / 'catalog').mkdir(exist_ok=True)
    (home / 'catalog' / 'models').symlink_to(outside, target_is_directory=True)
    assert capability_space.inventory()['items'] == []
    assert (revision / 'important.bin').read_bytes() == b'keep me'


def test_space_names_every_alias_of_one_download_and_counts_its_bytes_once(home, monkeypatch):
    from dataclasses import replace
    from orchestrator import capability_space

    owner = model()
    alias = replace(owner, id='another-use', name='Another use', files=(), files_from=owner.key)
    with monkeypatch.context() as patch:
        patch.setattr(catalog, 'manifests', lambda: {owner.key: owner, alias.key: alias})
        revision = model_files.revision_dir(owner)
        revision.mkdir(parents=True)
        (revision / 'receipt.json').write_text('{}')
        (revision / 'weights.bin').write_bytes(b'1234567890')
        result = capability_space.inventory()
        assert result['bytes'] == 12 and len(result['items']) == 1
        assert set(result['items'][0]['models']) == {owner.key, alias.key}


def test_space_retires_separate_fiji_jvm_before_withdrawing_generations(tmp_path, monkeypatch):
    from orchestrator import capability_space, fiji_bindings, fiji_generations

    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    folder = fiji_generations.root() / '1'
    folder.mkdir(parents=True)
    (folder / 'receipt.json').write_text('{}')
    (folder / 'Fiji').mkdir()
    stopped = []
    def stop():
        assert folder.exists(), 'JVM must retire while its generation still exists'
        stopped.append(True)
    monkeypatch.setattr(fiji_bindings.WORKER, 'stop', stop)
    app = SimpleNamespace(hub=SimpleNamespace(loads=None), downloads=SimpleNamespace(wire=lambda: []),
                          installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
    row = next(r for r in capability_space.inventory()['items'] if r['kind'] == 'fiji')
    asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert stopped and not folder.exists()


def test_space_cannot_remove_a_generation_owned_outside_a_studio_ticket(home):
    from unittest.mock import AsyncMock
    from orchestrator import capability_space, generation_leases

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'original')
    (revision / 'receipt.json').write_text('{}')
    row = capability_space.inventory()['items'][0]
    unload = AsyncMock(return_value={'unloaded': True})
    worker = SimpleNamespace(loading={}, resident={m.key: {}})
    app = SimpleNamespace(hub=SimpleNamespace(loads=SimpleNamespace(clients=[worker], let_go=unload), scene=SimpleNamespace()),
                          downloads=SimpleNamespace(wire=lambda: []), installs=SimpleNamespace(wire=lambda: []),
                          catalog_changed=lambda: None)
    with generation_leases.use(revision):
        with pytest.raises(ValueError, match='running work'):
            asyncio.run(capability_space.remove(app, row['id'], row['revision']))
        assert (revision / 'weights.bin').read_bytes() == b'original'
        unload.assert_not_called()
    # Once the owned work finishes, the same reviewed removal can be retried.
    result = asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert result['ok'] and not revision.exists()
    unload.assert_awaited_once_with(worker, m.key)


def test_space_says_what_can_be_freed_what_is_kept_and_where_saved_work_is(home, monkeypatch):
    """L7d: storage effects are said plainly; a runtime kept for saved work is not counted as space Remove can free."""
    from orchestrator import capability_space, scientific_retention

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'1234567890')
    (revision / 'receipt.json').write_text('{}')
    listed = capability_space.inventory()
    row = listed['items'][0]
    assert listed['reclaimableBytes'] == 12 and listed['keptBytes'] == 0
    assert row['says'].startswith(f'Remove frees up to 12 bytes (less where its files share space with another copy')
    assert f'{m.name} then needs Download again before it runs.' in row['says']
    assert listed['says'] == '12 bytes in managed copies: up to 12 bytes can be freed here.'
    assert listed['savedWork'] in listed['preserved'] and 'never removed by Space' in listed['preserved']

    def kept(references, *, models):
        raise scientific_retention.Retained('This runtime is kept for 1 saved scientific document.')
    monkeypatch.setattr(scientific_retention, 'require_unreferenced', kept)
    listed = capability_space.inventory()
    row = listed['items'][0]
    assert listed['reclaimableBytes'] == 0 and listed['keptBytes'] == 12
    assert row['says'].startswith('Kept: 12 bytes stays on this Mac because saved work uses it.')
    assert listed['says'] == ('12 bytes in managed copies: nothing can be freed here and 12 bytes is kept '
                              'because saved work uses it.')


def test_space_says_when_the_saved_work_check_could_not_run_instead_of_calling_it_kept(home, monkeypatch):
    from orchestrator import capability_space, scientific_retention

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'1234567890')
    (revision / 'receipt.json').write_text('{}')
    def unreadable(references, *, models):
        raise scientific_retention.Uncertain('Saved runtime associations are unreadable; removal is blocked')
    monkeypatch.setattr(scientific_retention, 'require_unreferenced', unreadable)
    listed = capability_space.inventory()
    row = listed['items'][0]
    assert row['retention']['checked'] is False
    assert row['says'].startswith('Not removable now: whether saved work uses these files (12 bytes) could not be checked.')
    assert listed['keptBytes'] == 0 and listed['uncheckedBytes'] == 12 and listed['reclaimableBytes'] == 0
    assert 'kept because saved work uses it' not in listed['says']
    assert "12 bytes can't be removed now because the check of saved work could not run" in listed['says']


def test_space_removal_says_what_was_freed_and_files_it_in_activity(home, monkeypatch):
    from orchestrator import activity, capability_space

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'weights.bin').write_bytes(b'1234567890')
    (revision / 'receipt.json').write_text('{}')
    lines = []
    sink = SimpleNamespace(add=lambda kind, text, **kw: lines.append((kind, text, kw.get('state'))))
    app = SimpleNamespace(hub=SimpleNamespace(loads=None, activity_sink=sink), downloads=SimpleNamespace(wire=lambda: []),
                          installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
    monkeypatch.setattr(activity, 'sink', lambda hub: getattr(hub, 'activity_sink', None))
    free = iter([1000, 1012])
    monkeypatch.setattr(capability_space.shutil, 'disk_usage', lambda path: SimpleNamespace(free=next(free)))
    row = capability_space.inventory()['items'][0]
    done = asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert done['filesBytes'] == 12 and done['freedBytes'] == 12 and done['models'] == [m.key]
    assert done['says'].startswith(f"Removed Downloaded files: {m.name}. Its files took up to 12 bytes; this disk's free "
                                   f'space rose by 12 bytes while it was removed. {m.name} needs Download again')
    assert 'are kept' in done['says']
    assert lines == [('model', done['says'], 'done')]


def test_space_does_not_count_a_hard_linked_file_as_space_remove_frees(home):
    from orchestrator import capability_space

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'receipt.json').write_text('{}')
    (revision / 'own.bin').write_bytes(b'1234567890')
    shared = home / 'elsewhere.bin'
    shared.write_bytes(b'kept by its other link')
    (revision / 'shared.bin').hardlink_to(shared)
    assert capability_space.inventory()['items'][0]['bytes'] == 12


def test_a_cloned_file_is_said_as_up_to_and_the_reply_says_the_measured_disk_change(home, monkeypatch):
    """L7d review: an APFS clone (a download adopted from a cache) has one link but frees nothing of shared blocks."""
    import subprocess
    from orchestrator import capability_space

    m = model()
    revision = model_files.revision_dir(m)
    revision.mkdir(parents=True)
    (revision / 'receipt.json').write_text('{}')
    cache = home / 'cache.bin'
    cache.write_bytes(b'x' * 4096)
    subprocess.run(['cp', '-c', str(cache), str(revision / 'weights.bin')], check=True)  # a clone, as downloads adopt
    row = capability_space.inventory()['items'][0]
    assert row['says'].startswith('Remove frees up to 4,098 bytes (less where its files share space')
    app = SimpleNamespace(hub=SimpleNamespace(loads=None), downloads=SimpleNamespace(wire=lambda: []),
                          installs=SimpleNamespace(wire=lambda: []), catalog_changed=lambda: None)
    free = iter([5000, 5000])  # the clone's blocks stay with the cache copy: nothing was freed
    monkeypatch.setattr(capability_space.shutil, 'disk_usage', lambda path: SimpleNamespace(free=next(free)))
    done = asyncio.run(capability_space.remove(app, row['id'], row['revision']))
    assert done['freedBytes'] == 0
    assert "free space rose by 0 bytes while it was removed" in done['says']
    assert cache.read_bytes() == b'x' * 4096
