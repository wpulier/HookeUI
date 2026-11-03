"""A version's large files in packs of about 16 MiB (format v2 section 4, hooke-projects ticket 07), on a folder remote
and against an in-memory Hooke Cloud. Synthetic bytes made here; no network or account."""
import hashlib
import json

import pytest

from orchestrator import project_remote
from orchestrator.project_folder_copy import copy_snapshot
from orchestrator.project_remote import Remote, fetch, push_sealed
from orchestrator.project_remote_cloud import CloudBulk
from orchestrator.project_remote_folder import FolderRemote, folder_remote
from orchestrator.project_remote_layers import ChangedOutsideHooke, RemoteRefused, canonical
from orchestrator.project_remote_packs import PACK_BYTES, parse, plan
from orchestrator.project_repository import ProjectRepository, document_root
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore
from tests.test_project_remote import project
from tests.test_project_remote_cloud import BASE, Cloud

MiB = 1 << 20


def add_file(root, name, body, store_id='synthetic-input-0123456789'):
    intake = FileIntake(ProjectStore(document_root(root)), root.session, store_id[:30], name=name, size=len(body),
                        max_bytes=len(body))
    try:
        for start in range(0, len(body), MiB):
            intake.write(body[start:start + MiB])
        return intake.publish()
    finally:
        intake.close()


def stack(n, seed=b''):
    return b''.join(hashlib.sha256(seed + i.to_bytes(4, 'big')).digest() for i in range(n // 32))


def objects(base):
    return [p for p in (base / 'objects').rglob('*') if p.is_file()]


def test_a_large_recording_travels_in_16_mib_packs_not_1_mib_chunks_and_comes_back_byte_for_byte(tmp_path):
    root, *_ = project(tmp_path)
    recording = add_file(root, 'recording.tif', stack(33 * MiB))
    remote = folder_remote(tmp_path / 'Drive', create=True)
    receipt = push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, remote, parent=None, summary='Backup')
    assert receipt['bulkFiles'] == 35  # 33 chunks of the recording, 2 of the image
    assert receipt['bulkObjects'] == len(objects(tmp_path / 'Drive')) == 3  # 16 MiB, 16 MiB and what is left
    assert all(p.stat().st_size <= PACK_BYTES for p in objects(tmp_path / 'Drive'))
    fetched, _ = fetch(remote, tmp_path / 'Fetched')
    assert ProjectRepository(fetched).read(recording.id, 1) == recording


def test_a_file_split_across_two_packs_is_rebuilt_from_both(tmp_path, monkeypatch):
    monkeypatch.setattr('orchestrator.project_remote_packs.PACK_BYTES', 700 << 10)  # every chunk spans a boundary
    root, *_ = project(tmp_path)
    recording = add_file(root, 'recording.tif', stack(5 * MiB))
    remote = folder_remote(tmp_path / 'Drive', create=True)
    receipt = push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, remote, parent=None, summary='Backup')
    bulk = json.loads(_bulk_raw(remote, receipt['commit']))
    assert bulk['format'] == 'hooke-bulk-v2' and len(bulk['packs']) == receipt['bulkObjects'] == 10
    assert any(len(p['contents']) > 1 for p in bulk['packs'])
    fetched, _ = fetch(remote, tmp_path / 'Fetched')
    assert ProjectRepository(fetched).read(recording.id, 1) == recording


def test_the_next_version_reuses_every_pack_and_sends_only_the_new_file(tmp_path):
    root, *_ = project(tmp_path)
    add_file(root, 'first.tif', stack(3 * MiB, b'first'))
    remote = folder_remote(tmp_path / 'Drive', create=True)
    first = push_sealed(copy_snapshot(root, tmp_path / 'One').path, remote, parent=None, summary='One')
    second_file = add_file(root, 'second.tif', stack(2 * MiB, b'second'), 'synthetic-input-9876543210')
    second = push_sealed(copy_snapshot(root, tmp_path / 'Two').path, remote, parent=first['commit'], summary='Two')
    assert second['reusedObjects'] == first['bulkObjects'] and second['uploadedBytes'] == 2 * MiB
    fetched, _ = fetch(remote, tmp_path / 'Fetched')
    assert ProjectRepository(fetched).read(second_file.id, 1) == second_file


def test_a_pack_the_store_lost_is_packed_again(tmp_path):
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    first = push_sealed(copy_snapshot(root, tmp_path / 'One').path, remote, parent=None, summary='One')
    for path in objects(tmp_path / 'Drive'):
        path.unlink()
    second = push_sealed(copy_snapshot(root, tmp_path / 'Two').path, remote, parent=first['commit'], summary='Two')
    assert second['reusedObjects'] == 0 and second['uploadedBytes'] == first['uploadedBytes']
    assert fetch(remote, tmp_path / 'Fetched')[0].origin == root.origin


def _bulk_raw(remote, commit):
    entry = next(e for e in remote.records.read_commit(commit)['entries'] if e['path'] == '.hooke/bulk.json')
    return remote.records.read_blob(entry['ref'], 8 << 20)


A, B, C, D = 'a' * 64, 'b' * 64, 'c' * 64, 'd' * 64


def test_a_parent_pack_is_reused_only_with_every_content_it_holds_and_every_piece_of_them(monkeypatch):
    monkeypatch.setattr('orchestrator.project_remote_packs.PACK_BYTES', 10)
    previous = [{'sha256': '1' * 64, 'bytes': 10, 'contents': [[A, 0, 6], [B, 0, 4]]},
                {'sha256': '2' * 64, 'bytes': 10, 'contents': [[B, 4, 2], [C, 0, 8]]},
                {'sha256': '3' * 64, 'bytes': 3, 'contents': [[D, 0, 3]]}]
    names = {p['sha256'] for p in previous}
    # C is gone: pack 2 can't be reused, so neither can pack 1, which holds B's start; D's pack stays.
    reused, fresh = plan({A: 6, B: 6, D: 3}, previous, names)
    assert [p['sha256'] for p in reused] == ['3' * 64]
    assert fresh == [[[A, 0, 6], [B, 0, 4]], [[B, 4, 2]]]
    # Everything still held, but the store lost pack 3: only D is packed again.
    reused, fresh = plan({A: 6, B: 6, C: 8, D: 3}, previous, names - {'3' * 64})
    assert [p['sha256'] for p in reused] == ['1' * 64, '2' * 64] and fresh == [[[D, 0, 3]]]


@pytest.mark.parametrize('packs', [
    [{'sha256': '1' * 64, 'bytes': 6, 'contents': [[A, 0, 6]]}],                       # B is in no pack
    [{'sha256': '1' * 64, 'bytes': 12, 'contents': [[A, 0, 6], [B, 0, 6]]},
     {'sha256': '2' * 64, 'bytes': 6, 'contents': [[B, 0, 6]]}],                       # B twice
    [{'sha256': '1' * 64, 'bytes': 11, 'contents': [[A, 0, 6], [B, 1, 5]]}],           # a gap at B's start
    [{'sha256': '1' * 64, 'bytes': 13, 'contents': [[A, 0, 6], [B, 0, 6], [C, 0, 1]]}],  # a content not listed
    [{'sha256': '1' * 64, 'bytes': 13, 'contents': [[A, 0, 6], [B, 0, 6]]}],           # bytes the pieces don't fill
    [{'sha256': '1' * 64, 'bytes': 12, 'contents': [[A, 0, 6], [B, 0, 6]], 'url': 'x'}],
    [{'sha256': '1' * 64, 'bytes': 12, 'contents': [[A, 0, 6], [B, 0, 6.0]]}],
    [{'sha256': '1' * 64, 'bytes': 65 << 20, 'contents': [[A, 0, 6], [B, 0, (65 << 20) - 6]]}],
])
def test_a_pack_listing_that_does_not_hold_exactly_the_large_files_is_refused(packs):
    with pytest.raises(ChangedOutsideHooke):
        parse(packs, {A: 6, B: 6})


def test_a_version_written_before_packs_still_fetches(tmp_path, monkeypatch):
    root, _, _, _, _, source = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    snapshot = copy_snapshot(root, tmp_path / 'Sealed').path
    _, _, manifest = project_remote.sealed_manifest(snapshot)
    _, bulk = project_remote.classify(manifest)
    for entry in bulk:  # hooke-bulk-v1: each content its own object
        remote.bulk.put(entry['sha256'], snapshot / entry['path'], entry['bytes'])
    monkeypatch.setattr(project_remote.packs, 'plan', lambda *a: ([], []))
    monkeypatch.setattr(project_remote, 'bulk_document', lambda origin, sha, entries, stores, packs: canonical(
        {'format': 'hooke-bulk-v1', 'origin': origin, 'snapshotSha256': sha, 'entries': [dict(e) for e in entries],
         'stores': list(stores)}))
    push_sealed(snapshot, remote, parent=None, summary='Before packs')
    monkeypatch.undo()
    fetched, _ = fetch(remote, tmp_path / 'Fetched')
    assert ProjectRepository(fetched).read(source.id, 1) == source


class Links(Cloud):
    """Hooke Cloud that hands out links elsewhere, and says 412 when an object arrived meanwhile."""

    def __init__(self, link, put_status=200):
        super().__init__()
        self.link, self.put_status, self.sent = link, put_status, []

    def request(self, method, url, *, json=None, content=None, headers=None, auth=True, limit=None):
        if url.startswith(self.link):
            self.sent.append((method, auth))
            digest = url.rsplit('/', 1)[-1]
            if method == 'PUT':
                self.objects[digest] = content
                return self.put_status, {}
            return 200, self.objects[digest][:limit + 1]
        status, data = super().request(method, url, json=json, content=content, headers=headers, auth=auth, limit=limit)
        for entry in (data.get('objects') or []) if isinstance(data, dict) else []:
            if 'url' in entry:
                entry['url'] = f'{self.link}{entry["sha256"]}'
        return status, data


def cloud_remote(tmp_path, cloud, origin, token='secret-token'):
    return Remote(FolderRemote(tmp_path / 'Records', create=True), CloudBulk(BASE, origin, token=token, http=cloud))


def test_the_sign_in_token_never_goes_to_an_object_link_even_one_that_looks_like_the_service(tmp_path):
    root, _, _, _, _, source = project(tmp_path)
    cloud = Links('https://app.hookeui.org.storage.example/')
    push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, cloud_remote(tmp_path, cloud, root.origin),
                parent=None, summary='Backup')
    fetched, _ = fetch(cloud_remote(tmp_path, cloud, root.origin), tmp_path / 'Fetched')
    assert ProjectRepository(fetched).read(source.id, 1) == source
    assert cloud.sent == [('PUT', False), ('GET', False)]


@pytest.mark.parametrize('link', ['http://app.hookeui.org/objects/', 'http://app.hookeui.org.example/', 'file:///tmp/',
                                  'https://bad link/'])
def test_an_upload_link_that_is_not_https_or_the_service_itself_is_refused(tmp_path, link):
    root, *_ = project(tmp_path)
    target = cloud_remote(tmp_path, Links(link), root.origin)
    with pytest.raises(ChangedOutsideHooke, match='unexpected upload link'):
        push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, target, parent=None, summary='Backup')
    assert target.records.head() is None


def test_an_object_that_arrived_meanwhile_is_kept_not_an_error(tmp_path):
    root, *_ = project(tmp_path)
    cloud = Links('https://bucket.r2.example/', put_status=412)
    receipt = push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, cloud_remote(tmp_path, cloud, root.origin),
                          parent=None, summary='Backup')
    assert receipt['uploadedBytes'] == 0 and receipt['commit']


def test_a_cloud_store_says_private_unless_it_is_public():
    assert CloudBulk(BASE, 'a' * 32, http=Cloud()).describe()['visibility'] == 'private'
    assert CloudBulk(BASE, 'a' * 32, http=Cloud(), visibility='public').describe()['visibility'] == 'public'
    with pytest.raises(RemoteRefused):
        CloudBulk(BASE, 'a' * 32, http=Cloud(), visibility='shared')
