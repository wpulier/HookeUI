"""Remote mirrors of sealed Project snapshots (ADR 0028), on a folder remote; no network, account or Git."""
import json
import os

import pytest

from orchestrator.day.prefs import Prefs
from orchestrator.project_folder_copy import copy_snapshot, restore_snapshot, verify_snapshot
from orchestrator.project_remote import Diverged, SecretsFound, fetch, push_sealed
from orchestrator.project_remote_folder import FolderRemote, folder_remote
from orchestrator.project_remote_layers import ChangedOutsideHooke, RemoteRefused, classify, snapshot_manifest
from orchestrator.project_repository import ProjectRepository, create, document_root
from orchestrator.project_roots import COPY_INTENT, ProjectLocationError, ProjectRegistry, ProjectRoot
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore

PIXELS = bytes(range(256)) * 6144  # 1.5 MiB: two content-addressed chunks, which travel in one pack


def project(tmp_path, text='First question'):
    prefs = Prefs(tmp_path / 'host')
    root = create(tmp_path / 'Projects', 'Research', session='2026-10-29', host_preferences=prefs)
    registry = ProjectRegistry(tmp_path / 'host')
    registry.register(root)
    repo = ProjectRepository(root)
    doc = {'kind': 'note', 'title': 'Question', 'state': {'text': text}, 'resources': [], 'runtimes': []}
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'runtimes') as edit:
        note = edit.create_document(doc)
        edit.append({'type': 'chat', 'role': 'user', 'text': 'A private conversation'})
    intake = FileIntake(ProjectStore(document_root(root)), root.session, 'synthetic-input-012345',
                        name='cells.tif', size=len(PIXELS), max_bytes=len(PIXELS))
    try:
        for start in range(0, len(PIXELS), 1 << 20):
            intake.write(PIXELS[start:start + (1 << 20)])
        source = intake.publish()
    finally:
        intake.close()
    return root, repo, prefs, registry, note, source


def sealed(root, tmp_path, name='Sealed'):
    return copy_snapshot(root, tmp_path / name).path


def test_round_trip_through_a_folder_is_byte_identical_and_reads_the_same(tmp_path):
    root, repo, _, _, note, source = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    receipt = push_sealed(sealed(root, tmp_path), remote, parent=None, summary='First backup')
    assert receipt['bulkFiles'] == 2 and receipt['bulkObjects'] == 1 and receipt['uploadedBytes'] == len(PIXELS)
    assert receipt['parent'] is None and remote.records.head() == receipt['commit']
    fetched, details = fetch(remote, tmp_path / 'Fetched')
    assert details['snapshotSha256'] == receipt['snapshotSha256'] and fetched.origin == root.origin
    assert verify_snapshot(fetched.path)['files'] == details['files']
    copy = ProjectRepository(fetched)
    assert copy.read(note.id, 1) == note and copy.read(source.id, 1) == source
    assert copy.events() == repo.events()
    working = restore_snapshot(fetched.path, tmp_path / 'Working')
    assert ProjectRepository(working).read(source.id, 1) == source


def test_the_bulk_layer_never_enters_the_record_tree(tmp_path):
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    receipt = push_sealed(sealed(root, tmp_path), remote, parent=None, summary='Backup')
    commit = remote.records.read_commit(receipt['commit'])
    paths = [e['path'] for e in commit['entries']]
    assert not any(p.endswith('.bin') for p in paths)
    assert {'README.md', '.hooke/snapshot.json', '.hooke/bulk.json', 'project/project.json'} <= set(paths)
    assert all(p.startswith(('project/', '.hooke/')) or p == 'README.md' for p in paths)
    assert 'Hooke-Snapshot: ' + receipt['snapshotSha256'] in commit['message']


def test_a_second_push_sends_only_what_is_new_and_chains_onto_the_first(tmp_path):
    root, repo, prefs, registry, note, _ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    first = push_sealed(sealed(root, tmp_path, 'One'), remote, parent=None, summary='One')
    with repo.edit(prefs, registry=registry, host_runtime_root=tmp_path / 'runtimes') as edit:
        edit.save_document(note.id, {**note.document, 'state': {'text': 'Revised'}}, expected_revision=1)
    second = push_sealed(sealed(root, tmp_path, 'Two'), remote, parent=first['commit'], summary='Two')
    assert second['uploadedBytes'] == 0 and second['reusedObjects'] == 1 and second['parent'] == first['commit']
    assert remote.records.read_commit(second['commit'])['parent'] == first['commit']
    fetched, _ = fetch(remote, tmp_path / 'Fetched')
    assert ProjectRepository(fetched).read(note.id).revision == 2


def test_a_version_sent_from_elsewhere_is_refused_without_overwriting(tmp_path):
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    first = push_sealed(sealed(root, tmp_path, 'One'), remote, parent=None, summary='One')
    with pytest.raises(Diverged, match='two places'):
        push_sealed(sealed(root, tmp_path, 'Two'), remote, parent=None, summary='Stale')
    assert remote.records.head() == first['commit']


def test_two_writers_racing_from_one_parent_cannot_both_win(tmp_path):
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    snapshot = sealed(root, tmp_path)
    a = remote.records.write_commit([], parent=None, message='a')
    b = remote.records.write_commit([], parent=None, message='b')
    remote.records.advance(None, a)
    with pytest.raises(Diverged):
        remote.records.advance(None, b)
    assert remote.records.head() == a and snapshot.exists()


def test_an_interrupted_push_leaves_the_remote_unchanged_and_a_retry_completes(tmp_path, monkeypatch):
    root, *_ = project(tmp_path)
    monkeypatch.setattr('orchestrator.project_remote_packs.PACK_BYTES', 1 << 20)  # two packs
    remote = folder_remote(tmp_path / 'Drive', create=True)
    snapshot = sealed(root, tmp_path)
    original = remote.bulk.put
    calls = []
    def fail_second(digest, path, size, **kw):
        calls.append(digest)
        if len(calls) == 2:
            raise OSError('injected: drive unplugged')
        return original(digest, path, size, **kw)
    monkeypatch.setattr(remote.bulk, 'put', fail_second)
    with pytest.raises(OSError):
        push_sealed(snapshot, remote, parent=None, summary='Interrupted')
    assert remote.records.head() is None
    monkeypatch.setattr(remote.bulk, 'put', original)
    receipt = push_sealed(snapshot, remote, parent=None, summary='Retried')
    assert receipt['uploadedBytes'] < len(PIXELS)  # the pack sent before the failure is reused
    assert fetch(remote, tmp_path / 'Fetched')[0].origin == root.origin


def test_a_file_changed_on_the_remote_is_refused_and_nothing_is_trusted(tmp_path):
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    receipt = push_sealed(sealed(root, tmp_path), remote, parent=None, summary='Backup')
    commit = remote.records.read_commit(receipt['commit'])
    journal = next(e for e in commit['entries'] if e['path'].endswith('journal.jsonl'))
    blob = tmp_path / 'Drive' / 'blobs' / journal['ref'][:2] / journal['ref']
    os.chmod(blob, 0o600)
    blob.write_bytes(blob.read_bytes().replace(b'private', b'altered'))
    with pytest.raises(ChangedOutsideHooke):
        fetch(remote, tmp_path / 'Fetched')
    with pytest.raises(ProjectLocationError):
        ProjectRoot.open(tmp_path / 'Fetched')


def test_a_damaged_large_object_is_refused(tmp_path):
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    push_sealed(sealed(root, tmp_path), remote, parent=None, summary='Backup')
    obj = next((tmp_path / 'Drive' / 'objects').rglob('*'))
    while obj.is_dir():
        obj = next(obj.iterdir())
    obj.write_bytes(b'x' * obj.stat().st_size)
    with pytest.raises(ChangedOutsideHooke):
        fetch(remote, tmp_path / 'Fetched')
    assert (tmp_path / 'Fetched' / COPY_INTENT).exists()


def test_a_version_not_made_by_hooke_is_refused(tmp_path):
    remote = folder_remote(tmp_path / 'Drive', create=True)
    commit = remote.records.write_commit([], parent=None, message='Edited on the web')
    remote.records.advance(None, commit)
    with pytest.raises(ChangedOutsideHooke, match='not made by Hooke'):
        fetch(remote, tmp_path / 'Fetched')
    assert not (tmp_path / 'Fetched').exists()


@pytest.mark.parametrize('path', ['project/../escape', '/etc/passwd', 'project/inputs/\x01x', 'elsewhere/file',
                                  'project/credentials.json', 'project/inputs/a/./b'])
def test_hostile_paths_are_refused_before_anything_is_written(tmp_path, path):
    from orchestrator.project_remote import RemoteFile
    remote = folder_remote(tmp_path / 'Drive', create=True)
    files = [RemoteFile(path, 1, lambda: b'x')]
    commit = remote.records.write_commit(files, parent=None,
                                         message=f'x\n\nHooke-Snapshot: {"a" * 64}\nHooke-Origin: {"b" * 32}\nHooke-Format: 2\n')
    remote.records.advance(None, commit)
    with pytest.raises(ChangedOutsideHooke):
        fetch(remote, tmp_path / 'Fetched')
    assert not (tmp_path / 'Fetched').exists()


def test_a_secret_in_the_record_layer_stops_the_push_before_anything_is_sent(tmp_path):
    root, *_ = project(tmp_path, text='my key is sk-abcdefghijklmnop1234')
    remote = folder_remote(tmp_path / 'Drive', create=True)
    with pytest.raises(SecretsFound) as refused:
        push_sealed(sealed(root, tmp_path), remote, parent=None, summary='Backup')
    assert 'sk-abc' not in str(refused.value) and 'line' in str(refused.value)
    assert remote.records.head() is None and not (tmp_path / 'Drive' / 'objects').exists()


def test_layers_split_by_path_and_size_only(tmp_path):
    root, *_ = project(tmp_path)
    raw = (sealed(root, tmp_path) / 'snapshot.json').read_bytes()
    record, bulk = classify(snapshot_manifest(raw))
    assert {e['path'].rsplit('/', 1)[-1].split('.')[0] for e in bulk} == {e['sha256'] for e in bulk}
    assert all(e['bytes'] <= 1 << 20 for e in record)


def test_a_folder_that_is_not_a_remote_is_refused(tmp_path):
    (tmp_path / 'Photos').mkdir()
    (tmp_path / 'Photos' / 'holiday.jpg').write_bytes(b'not a remote')
    with pytest.raises(RemoteRefused):
        FolderRemote(tmp_path / 'Photos', create=True)
    with pytest.raises(RemoteRefused):
        FolderRemote(tmp_path / 'Photos')
    assert json.loads(json.dumps({'ok': True}))
