"""Synthetic native saves through actual journals and folder snapshot verification."""
import hashlib
import json

import pytest

from orchestrator.project_folder_copy import copy_owned_snapshot, verify_snapshot
from orchestrator.project_native_integrity import verify_native_records
from orchestrator.project_roots import ProjectLocationError, SNAPSHOT
from tests.test_project_evidence_routing import project, make
from tests.test_evidence import saved


def test_every_accepted_save_survives_snapshot_and_relocation(project, tmp_path):
    day, locations, evidence, _ = project
    p = make(evidence, day)
    evidence.link(p, 'same-object', 'c1', 'o9', 'review')
    assert saved(p).status == 'saved'
    root = locations.project(day.sid)
    assert verify_native_records(root) == {'savedEvents': 2, 'verifiedSaves': 2}
    snapshot = copy_owned_snapshot(locations, day.sid, tmp_path / 'Snapshot')
    assert verify_snapshot(snapshot.path)['files'] > 0
    snapshot.path.rename(tmp_path / 'Moved snapshot')
    assert verify_snapshot(tmp_path / 'Moved snapshot')['origin'] == root.origin


@pytest.mark.parametrize('damage', ['capture', 'masks', 'manifest', 'missing'])
def test_historical_damage_refuses_new_snapshot_even_if_latest_save_is_good(project, tmp_path, damage):
    day, locations, evidence, _ = project
    p = make(evidence, day)
    old = p.path
    evidence.link(p, 'same-object', 'c1', 'o9', 'review')
    assert saved(p).status == 'saved' and p.path != old
    target = old / {'capture': 'capture.jpg', 'masks': 'masks.npz', 'manifest': 'manifest.json', 'missing': 'capture.jpg'}[damage]
    if damage == 'missing': target.unlink()
    else: target.write_bytes(b'changed')
    with pytest.raises(ProjectLocationError, match='native evidence'):
        copy_owned_snapshot(locations, day.sid, tmp_path / 'Snapshot')
    assert not (tmp_path / 'Snapshot').exists()
    assert p.path.exists()


def test_changed_capture_cannot_be_blessed_by_recomputed_outer_seal(project, tmp_path):
    day, locations, evidence, _ = project
    p = make(evidence, day)
    root = locations.project(day.sid)
    snapshot = copy_owned_snapshot(locations, day.sid, tmp_path / 'Snapshot')
    target = snapshot.path / (p.path / 'capture.jpg').relative_to(root.path)
    target.write_bytes(b'changed')
    seal = snapshot.path / SNAPSHOT
    value = json.loads(seal.read_bytes())
    entry = next(item for item in value['entries'] if item['path'] == target.relative_to(snapshot.path).as_posix())
    entry.update(bytes=7, sha256=hashlib.sha256(b'changed').hexdigest())
    seal.chmod(0o600); seal.write_text(json.dumps(value))
    with pytest.raises(ProjectLocationError, match='native evidence'):
        verify_snapshot(snapshot.path)


def test_declared_optional_enrichment_is_required_for_snapshot(project, tmp_path):
    import numpy as np
    day, locations, evidence, _ = project
    p = make(evidence, day)
    p.vectors = {'g1': {'ids': ['c1'], 'array': np.array([[1.0, 2.0]], dtype=np.float32)}}
    evidence.schedule_save(p)
    assert saved(p).status == 'saved'
    assert (p.path / 'vectors.npz').exists()
    (p.path / 'vectors.npz').unlink()
    with pytest.raises(ProjectLocationError, match='native evidence'):
        copy_owned_snapshot(locations, day.sid, tmp_path / 'Snapshot')
    assert not (tmp_path / 'Snapshot').exists()


def test_native_verification_cancellation_preserves_original(project):
    day, locations, evidence, _ = project
    p = make(evidence, day)
    before = (p.path / 'manifest.json').read_bytes()
    def cancelled(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        verify_native_records(locations.project(day.sid), check=cancelled)
    assert (p.path / 'manifest.json').read_bytes() == before


def test_manifest_allowance_applies_before_manifest_hashing(project, monkeypatch):
    day, locations, evidence, _ = project
    make(evidence, day)
    monkeypatch.setattr('orchestrator.project_native_integrity.MAX_EVIDENCE_FILE', 1)
    with pytest.raises(ProjectLocationError, match='verification allowance'):
        verify_native_records(locations.project(day.sid))


def test_manifest_hash_observes_verification_cancellation(project, monkeypatch):
    from orchestrator import project_folder_copy
    day, locations, evidence, _ = project
    p = make(evidence, day)
    stream = project_folder_copy._stream
    hashing = False
    checks = []
    def wrapped(path, *args, **kwargs):
        nonlocal hashing
        if path == p.path / 'manifest.json': hashing = True
        return stream(path, *args, **kwargs)
    monkeypatch.setattr(project_folder_copy, '_stream', wrapped)
    def cancel_during_hash():
        checks.append(hashing)
        if hashing: raise RuntimeError('cancelled during manifest hash')
    with pytest.raises(RuntimeError, match='cancelled during manifest hash'):
        verify_native_records(locations.project(day.sid), check=cancel_during_hash)
    assert True in checks
