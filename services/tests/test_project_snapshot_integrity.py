"""Synthetic accepted records through real snapshot admission; no full collection proof."""
import hashlib
import json

import pytest

from orchestrator.project_folder_copy import copy_snapshot, verify_snapshot
from orchestrator.project_repository import document_root
from orchestrator.project_roots import ProjectLocationError, SNAPSHOT
from orchestrator.project_snapshot_integrity import verify_records
from orchestrator.scientific_file_intake import FileIntake
from orchestrator.scientific_projects import ProjectStore
from tests.test_project_folder_copy import setup


def source(root):
    store = ProjectStore(document_root(root))
    intake = FileIntake(store, root.session, 'integrity-source-001', name='source.bin', size=7, max_bytes=7)
    try:
        intake.write(b'payload')
        saved = intake.publish()
    finally:
        intake.close()
    return saved, next((document_root(root) / saved.id / 'files').glob('*.bin'))


def test_old_revision_corruption_cannot_be_blessed_by_fresh_snapshot_hashes(tmp_path):
    root, _, first, _ = setup(tmp_path)
    prior = document_root(root) / first.id / 'revisions' / f'{first.digest}.json'
    prior.chmod(0o600)
    prior.write_bytes(prior.read_bytes().replace(b'First question', b'Wrong question'))
    with pytest.raises(ProjectLocationError, match='accepted records'):
        copy_snapshot(root, tmp_path / 'Snapshot')
    assert not (tmp_path / 'Snapshot').exists() and prior.exists()


@pytest.mark.parametrize('damage', ['changed', 'missing'])
def test_retained_file_damage_refuses_before_destination(tmp_path, damage):
    root, *_ = setup(tmp_path)
    saved, chunk = source(root)
    if damage == 'missing':
        chunk.unlink()
    else:
        chunk.chmod(0o600); chunk.write_bytes(b'changed')
    with pytest.raises(ProjectLocationError, match='accepted records'):
        copy_snapshot(root, tmp_path / 'Snapshot')
    assert not (tmp_path / 'Snapshot').exists()
    assert ProjectStore(document_root(root)).read(saved.id, root.session) == saved


def test_snapshot_verification_checks_accepted_content_not_only_outer_seal(tmp_path):
    root, *_ = setup(tmp_path)
    _, chunk = source(root)
    snapshot = copy_snapshot(root, tmp_path / 'Snapshot')
    copied = snapshot.path / chunk.relative_to(root.path)
    copied.chmod(0o600); copied.write_bytes(b'changed')
    seal = snapshot.path / SNAPSHOT
    manifest = json.loads(seal.read_bytes())
    entry = next(e for e in manifest['entries'] if e['path'] == copied.relative_to(snapshot.path).as_posix())
    entry['sha256'] = hashlib.sha256(b'changed').hexdigest()
    seal.chmod(0o600); seal.write_text(json.dumps(manifest))
    with pytest.raises(ProjectLocationError, match='accepted records'):
        verify_snapshot(snapshot.path)


def test_superseded_array_bytes_remain_required(tmp_path):
    import numpy as np
    from tests.test_scientific_arrays import draft, fill, accept, project
    root, *_ = setup(tmp_path)
    store = ProjectStore(document_root(root))
    owner = project(store, root.session)
    data = np.arange(8, dtype='<u2').reshape(2, 2, 2)
    first = accept(store, owner, fill(draft(store, owner, data, chunks=(2, 2, 2)), data))
    old_chunks = set((document_root(root) / owner.id / 'arrays').glob('*.bin'))
    accept(store, first, fill(draft(store, first, data + 10, chunks=(2, 2, 2)), data + 10))
    assert verify_records(root)['revisions'] == 5
    next(iter(old_chunks)).unlink()
    with pytest.raises(ProjectLocationError, match='accepted records'):
        copy_snapshot(root, tmp_path / 'Snapshot')
    assert not (tmp_path / 'Snapshot').exists()


def test_verification_is_bounded_and_cancellable_without_mutation(tmp_path, monkeypatch):
    root, _, first, second = setup(tmp_path)
    assert verify_records(root) == {'documents': 1, 'revisions': 2}
    monkeypatch.setattr('orchestrator.project_snapshot_integrity.MAX_HISTORY', 1)
    with pytest.raises(ProjectLocationError, match='history allowance'):
        copy_snapshot(root, tmp_path / 'Limited')
    assert not (tmp_path / 'Limited').exists()
    def stop():
        raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        verify_records(root, check=stop)
    store = ProjectStore(document_root(root))
    assert store.read(first.id, root.session, revision=1) == first
    assert store.read(second.id, root.session) == second
