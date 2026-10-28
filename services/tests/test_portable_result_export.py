"""Native evidence versus an explicit portable download; synthetic pixels/catalog, production HTTP and store seams."""
import asyncio
import hashlib
import json
import shutil
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from gpu_worker import manifests as catalog
from orchestrator import commands, made_with, readiness, result_entries, share
from orchestrator.capture import Memory, captures
from orchestrator.day import DayStore
from orchestrator.resources import Results
from tests.test_channels import home  # noqa: F401
from tests.test_share import yours
from tests.test_resources import app_at
from tests.waiting import HANG_S


def png():
    return cv2.imencode('.png', np.arange(192, dtype=np.uint8).reshape(8, 8, 3))[1].tobytes()


def test_portable_export_refuses_before_reading_when_shared_memory_cannot_admit_it():
    memory = Memory({'analysis': 0})
    with pytest.raises(RuntimeError):
        result_entries.export_image(lambda _: pytest.fail('source read before admission'), 100, [], memory)
    assert memory.wire()['classes']['analysis']['reservedMb'] == 0


@pytest.mark.parametrize('failure', ['aggregate', 'cache', 'admission'])
def test_sharing_failures_do_not_change_original_evidence_or_completion(tmp_path, failure):
    day = DayStore(root=tmp_path / 'sessions', day='2026-10-07')
    results = Results(tmp_path / 'results', day=day)
    stamp = {'key': 'mine/example', 'name': 'Example', 'version': '1', 'from': 'yours',
             'portable': {'sha256': '0' * 64, 'bytes': result_entries.MAX_BYTES}}
    made = [stamp, {**stamp, 'key': 'mine/second', 'portable': {'sha256': '1' * 64, 'bytes': result_entries.MAX_BYTES}}] \
        if failure == 'aggregate' else [stamp]
    original = png()
    entry = results.add('enhance', {}, 'Synthetic method output', picture_bytes=[original], made_with=made)
    assert results.picture(entry['id']) == original
    assert entry['digests'] == [hashlib.sha256(original).hexdigest()]
    memory = Memory({'analysis': 0}) if failure == 'admission' else Memory()
    with pytest.raises((ValueError, OSError, RuntimeError)):
        results.export_picture(entry['id'], 0, day.sid, memory)
    assert results.picture(entry['id']) == original
    reopened = Results(tmp_path / 'results', day=DayStore(root=tmp_path / 'sessions', day=day.sid))
    # Wait only for the existing background file writer in the runtime test; this check is not an app launch.
    for thread in __import__('threading').enumerate():
        if thread.name.endswith('(write_all)'):
            thread.join(HANG_S)
    assert reopened.picture(entry['id']) == original
    assert memory.wire()['classes']['analysis']['reservedMb'] == 0


def test_real_download_route_exports_a_separate_copy_and_preserves_native_hash(home, tmp_path):
    app = app_at(tmp_path / 'app')
    built = app.state.app
    memory = captures(built.hub).memory
    stamp = made_with.capture(catalog.get(yours(home)), memory=memory)
    original = png()
    results = built.hub.resources.results
    entry = results.add('enhance', {}, 'A synthetic native PNG', picture_bytes=[original], made_with=[stamp])
    client = TestClient(app)
    query = f"?day={built.day.sid}&n=0"
    response = client.get(f"/api/resources/{entry['id']}/share{query}")
    assert response.status_code == 200, response.text
    assert response.headers['x-original-sha256'] == hashlib.sha256(original).hexdigest()
    assert response.headers['x-share-sha256'] == hashlib.sha256(response.content).hexdigest()
    assert response.content != original
    copy = tmp_path / 'download.png'
    copy.write_bytes(response.content)
    assert made_with.read(copy) == [stamp]
    assert client.get(f"/api/resources/{entry['id']}/picture{query}").content == original
    assert memory.wire()['classes']['analysis']['reservedMb'] == 0


def test_saved_session_dataset_carries_entries_when_its_folder_moves_to_another_mac(home, monkeypatch, tmp_path):
    from orchestrator.datasets import snapshot, manifest, store
    from tests.test_datasets import _session_on_disk

    key = yours(home)
    stamp = made_with.capture(catalog.get(key), memory=Memory())
    paths, start, stop = _session_on_disk(tmp_path / 'session')
    with paths.interaction_log.open('a') as stream:
        stream.write(json.dumps({'at': '2026-10-07T18:02:10', 'kind': 'execution', 'id': 'x-portable',
                                 'executed': key, 'version': stamp['version'], 'producer': {'catalog': stamp}}) + '\n')
    folder = snapshot.save(paths, start, stop, scope='conversation')
    summary = manifest.read(folder)
    assert summary['madeWith'] == [stamp]
    assert list((folder / 'capabilities').glob('*.observationui.zip'))
    received_root = tmp_path / 'received-datasets'
    received = received_root / folder.name
    shutil.copytree(folder, received)  # the complete supported dataset, not the sender's capability cache
    assert store.find(received_root, folder.name) == received
    video = received / summary['video']['parts'][0]['video']
    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'recipient-catalog'))
    catalog.manifests.cache_clear()
    assert made_with.offers(made_with.of_asset(video))[0]['state'] == 'acquire'

    async def sync():
        pass

    app = SimpleNamespace(hub=SimpleNamespace(library=SimpleNamespace(get=lambda aid: SimpleNamespace(path=video))), sync_mine=sync)
    got = asyncio.run(commands.made_with_acquire(app, None, {'asset': 'owned-video', 'key': key}))
    assert readiness.model(got['model'], {}).steps[0].kind == 'trust'


def test_record_stop_retains_capability_sidecars_inside_the_finished_dataset(home, tmp_path):
    from orchestrator.datasets import manifest
    from tests.test_datasets import Clock, collector, recording_now, finish

    key = yours(home)
    stamp = made_with.capture(catalog.get(key), memory=Memory())
    clock = Clock()
    collected, bridge, _ = collector(tmp_path / 'record', clock)
    folder = collected.root / collected.start()['id']
    asyncio.run(collected.tick())
    recording_now(collected, bridge, clock, seq=1)
    collected.interaction({'kind': 'execution', 'id': 'x-portable', 'executed': key,
                           'version': stamp['version'], 'producer': {'catalog': stamp}})
    collected.stop()
    finish(collected)
    bridge.health['recording'] = {'running': False, 'owners': [], 'finishing': []}
    clock.now += 1
    asyncio.run(collected.tick())
    summary = manifest.read(folder)
    assert summary['madeWith'] == [stamp]
    payload = folder / 'capabilities' / (stamp['portable']['sha256'] + '.observationui.zip')
    assert hashlib.sha256(payload.read_bytes()).hexdigest() == stamp['portable']['sha256']


def test_native_image_sidecar_is_local_identity_not_a_claim_of_embedded_capability(home, tmp_path):
    stamp = made_with.capture(catalog.get(yours(home)), memory=Memory())
    native = tmp_path / 'd1-0.png'
    native.write_bytes(png())
    native.with_name('d1.json').write_text(json.dumps({'madeWith': [stamp]}))
    assert made_with.of_asset(native) == [{k: v for k, v in stamp.items() if k != 'portable'}]
    assert made_with.of_asset(native, include_local=True) == [stamp]


def test_library_download_retains_a_clean_snapshots_exact_bytes_and_its_own_capability(home, tmp_path):
    from orchestrator.library import Library

    stamp = made_with.capture(catalog.get(yours(home)), memory=Memory())
    snapshots = tmp_path / 'snapshots'
    folder = snapshots / '2026-10-07'
    (folder / 'clean').mkdir(parents=True)
    name = 'snapshot-120000-test-f1'
    original = png()
    native = folder / 'clean' / (name + '.png')
    native.write_bytes(original)
    # The drawn snapshot (its outlines on it) differs from the clean copy: its Made with names those outlines, so
    # Download with capability exports the drawn picture (Spec review of 64f49d1b, P2-2); Download original stays clean.
    drawn = cv2.imencode('.png', 255 - np.arange(192, dtype=np.uint8).reshape(8, 8, 3))[1].tobytes()
    (folder / (name + '.png')).write_bytes(drawn)
    (folder / (name + '.json')).write_text(json.dumps({'overlays': True, 'madeWith': [stamp]}))
    web = app_at(tmp_path / 'app')
    web.state.hub.library = Library(tmp_path / 'library.jsonl', snapshots=snapshots,
                                    footage=tmp_path / 'footage', manifest=tmp_path / 'manifest.json')
    asset = next(a for a in web.state.hub.library.assets().values() if a.path == native)
    client = TestClient(web)
    response = client.get(f'/api/library/{asset.id}/share')
    assert response.status_code == 200, response.text
    assert made_with.read(response.content) == [stamp]
    assert response.headers['x-original-sha256'] == hashlib.sha256(drawn).hexdigest(), 'the picture that shows the outlines'
    assert native.read_bytes() == original and (folder / (name + '.png')).read_bytes() == drawn
    assert client.get(f'/api/library/{asset.id}/file').content == original


@pytest.mark.parametrize('suffix', ['.png', '.jpg'])
def test_download_with_capability_travels_through_two_recipients_without_acquiring_or_running(home, monkeypatch, tmp_path, suffix):
    from orchestrator.library import Library

    key = yours(home)
    producer = catalog.get(key)
    adapter = (catalog.entry_folder(producer) / 'adapter.py').read_bytes()
    stamp = made_with.capture(producer, memory=Memory())
    encoded = cv2.imencode(suffix, np.arange(192, dtype=np.uint8).reshape(8, 8, 3))[1].tobytes()
    web = app_at(tmp_path / 'sender')
    built = web.state.app
    result = built.hub.resources.results.add('enhance', {}, 'Synthetic original', picture_bytes=[encoded], made_with=[stamp])
    client = TestClient(web)
    outgoing = client.get(f"/api/resources/{result['id']}/share?day={built.day.sid}&n=0")
    assert outgoing.status_code == 200, outgoing.text
    portable = outgoing.content

    for recipient in ('second', 'third'):
        local = tmp_path / recipient
        monkeypatch.setenv('MICROMIND_CATALOG', str(local / 'catalog'))
        catalog.manifests.cache_clear()
        added = local / 'footage' / 'added'
        added.mkdir(parents=True)
        image = added / ('received' + suffix)
        image.write_bytes(portable)
        built.hub.library = Library(local / 'library.jsonl', footage=local / 'footage', manifest=local / 'manifest.json')
        asset = next(a for a in built.hub.library.assets().values() if a.path == image)
        assert made_with.offers(made_with.of_asset(image))[0]['state'] == 'acquire'
        assert not catalog.channels(), 're-sharing must not acquire, trust, install or run the carried entry'
        shared = client.get(f'/api/library/{asset.id}/share')
        assert shared.status_code == 200, shared.text
        assert shared.content == portable, 'already portable bytes must not grow another payload on each transfer'
        assert shared.headers['x-original-sha256'] == hashlib.sha256(portable).hexdigest()
        assert client.get(f'/api/library/{asset.id}/file').content == portable
        assert not catalog.channels()
        portable = shared.content

    async def sync():
        pass

    receiver = SimpleNamespace(hub=built.hub, sync_mine=sync)
    acquired = asyncio.run(commands.made_with_acquire(receiver, None, {'asset': asset.id, 'key': key}))
    entry = catalog.get(acquired['model'])
    assert (catalog.entry_folder(entry) / 'adapter.py').read_bytes() == adapter
    assert readiness.model(entry.key, {}).steps[0].kind == 'trust'
    assert captures(built.hub).memory.wire()['classes']['analysis']['reservedMb'] == 0


def test_older_dataset_completion_cannot_erase_a_newer_stopped_recording(tmp_path, monkeypatch):
    import threading
    from orchestrator.datasets import manifest
    from tests.test_datasets import Clock, collector, finish

    clock = Clock()
    collected, _bridge, _ = collector(tmp_path / 'record', clock)
    first = collected.root / collected.start()['id']
    collected.stop()
    finish(collected)
    entered, release = threading.Event(), threading.Event()
    write = manifest.write

    def held_write(folder, summary):
        if folder == first and summary.get('endCheck'):
            entered.set()
            assert release.wait(HANG_S), 'test barrier must release the older filesystem write'
        return write(folder, summary)

    monkeypatch.setattr(manifest, 'write', held_write)

    async def scenario():
        clock.now += 1
        old = asyncio.create_task(collected.tick())
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            clock.now += 1
            second = collected.root / collected.start()['id']
            collected.stop()
            finish(collected)
            release.set()
            await old
            assert 'endCheck' in manifest.read(first)
            check = collected.wire()['check']
            assert check['state'] == 'saving' and check['id'] == second.name
            await collected.tick()
            assert 'endCheck' in manifest.read(second)
            assert collected.wire()['check']['last']['id'] == second.name
        finally:
            release.set()
            await asyncio.gather(old, return_exceptions=True)

    asyncio.run(scenario())


def test_portable_copies_left_from_before_are_removed_at_start_and_a_current_one_is_kept(tmp_path, monkeypatch):
    """Standards review of 64f49d1b, P2-4: a download the browser never started left its copy in exports/ forever."""
    import os
    import time

    from orchestrator import result_entries, share

    monkeypatch.setenv('MICROMIND_CATALOG', str(tmp_path / 'catalog'))
    folder = share.root() / 'exports'
    folder.mkdir(parents=True)
    old, new = folder / 'left.png', folder / 'serving.png'
    old.write_bytes(b'portable copy'), new.write_bytes(b'portable copy')
    hour_ago = time.time() - 7200
    os.utime(old, (hour_ago, hour_ago))
    assert result_entries.clear_exports() == 1
    assert not old.exists() and new.exists(), 'a copy being served now is never removed'
    assert result_entries.clear_exports() == 0


def test_a_picture_a_capability_made_is_in_the_library_with_what_made_it_and_a_plain_result_is_not(tmp_path):
    """Spec review of 64f49d1b, P2-1: a contributed capability's picture was neither a product nor a library asset, so
    Download with capability could not be reached for it. It joins the library as a clearer picture does."""
    from tests.capability_rig import Rig

    rig = Rig(tmp_path / 'rig')
    results, library = rig.hub.resources.results, rig.hub.library
    date = '2026-10-13'
    (results.root / date).mkdir(parents=True, exist_ok=True)
    made = [{'key': 'mine/denoiser', 'name': 'Denoiser', 'version': 'v1', 'from': 'yours'}]
    for rid, tool, with_made in (('d7', 'run_capability', made), ('d8', 'count', None)):
        (results.root / date / f'{rid}-0.jpg').write_bytes(b'\xff\xd8\xff' + rid.encode())
        entry = {'id': rid, 'tool': tool, 'title': rid, 'pictures': [f'{rid}-0.jpg'], 'day': date, 'at': 1.0,
                 'session': date, **({'madeWith': with_made} if with_made else {})}
        for hook in list(results.on_saved):
            hook(entry)
    assert library.by_key(f'result:{date}/d7/0') is not None, 'a capability picture is a library still'
    assert library.by_key(f'result:{date}/d8/0') is None, 'a count is an answer, never an asset'


def test_a_carried_entry_keeps_its_proof_and_is_packed_once_until_its_files_change(home, monkeypatch):
    """Spec review of 64f49d1b, P3-1 and P3-4: Share a file carries the generation's proof, so a result's carried entry
    does too; and a run no longer re-zips and hashes its entry every time."""
    import zipfile

    m = catalog.get(yours(home))
    packs = []
    real = share.pack
    monkeypatch.setattr(share, 'pack', lambda *a, **k: packs.append(1) or real(*a, **k))
    monkeypatch.setattr(result_entries, '_RETAINED', {})
    proof = {'says': 'found 3 on its own example', 'found': 3, 'at': '2026-10-13T21:00:00'}
    first = result_entries.retain(m, Memory(), proof=proof)
    again = result_entries.retain(m, Memory(), proof=proof)
    assert first == again and len(packs) == 1, 'packed once for the same entry as it stands'
    packed = share.root() / 'results' / (first['sha256'] + share.STORED_SUFFIX)
    with zipfile.ZipFile(packed) as z:
        shared = json.loads(z.read('SHARED.json'))
    assert shared['proof'] == proof
    adapter = catalog.entry_folder(m) / 'adapter.py'
    adapter.write_text(adapter.read_text() + '\n# changed\n')
    changed = result_entries.retain(m, Memory(), proof=proof)
    assert len(packs) == 2 and changed['sha256'] != first['sha256'], 'a changed entry is packed again'
