"""Synthetic researcher history through real publication and folder-copy checks."""
import hashlib
import json

import pytest

from orchestrator.project_folder_copy import copy_owned_snapshot, verify_snapshot
from orchestrator.project_research_integrity import verify_research_records
from orchestrator.project_roots import ProjectLocationError, SNAPSHOT
from orchestrator.research.findings import retain
from tests.test_project_research_storage import setup, session


def saved_research(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    item = session(sid)
    retain(item.finding_revisions, {'report': {'observations': 'First observation'}}, [], None)
    manager._save(item)
    item.result = {'summary': 'Later interpretation'}
    retain(item.finding_revisions, {'report': {'observations': 'Second observation'}}, [], None)
    manager._save(item)
    return locations, sid, manager.directory(item)


def test_prior_research_and_findings_keep_their_identities_in_a_copy(tmp_path):
    locations, sid, folder = saved_research(tmp_path)
    try:
        root = locations.project(sid)
        before = {path.name: path.read_bytes() for path in (folder / 'history').iterdir()}
        snapshot = copy_owned_snapshot(locations, sid, tmp_path / 'Copy')
        assert verify_snapshot(snapshot.path)['externalDependencies'] == 'not-verified'
        copied = snapshot.path / folder.relative_to(root.path)
        assert (copied / 'session.json').read_bytes() == (folder / 'session.json').read_bytes()
        assert {p.name: p.read_bytes() for p in (copied / 'history').iterdir()} == before
    finally:
        locations.close()


@pytest.mark.parametrize('damage', ['archive', 'finding', 'owner', 'pending', 'missing', 'finding-list', 'finding-tail'])
def test_damaged_research_refuses_before_creating_a_copy(tmp_path, damage):
    locations, sid, folder = saved_research(tmp_path)
    try:
        head = folder / 'session.json'
        if damage == 'archive':
            old = next((folder / 'history').iterdir())
            old.chmod(0o600)
            old.write_bytes(old.read_bytes().replace(b'First observation', b'Wrong observation'))
        elif damage in ('finding', 'owner', 'finding-list', 'finding-tail'):
            value = json.loads(head.read_bytes())
            if damage == 'finding': value['findingRecords'][0]['result']['report']['observations'] = 'Changed'
            elif damage == 'finding-list': del value['findingRecords']
            elif damage == 'finding-tail': value['findingRecords'].pop()
            else: value['session'] = '2026-10-01'
            head.write_text(json.dumps(value))
        elif damage == 'pending':
            (folder / '.publication-pending').write_text('incomplete')
        else:
            head.unlink()
        with pytest.raises(ProjectLocationError, match='researcher history'):
            copy_owned_snapshot(locations, sid, tmp_path / 'Refused')
        assert not (tmp_path / 'Refused').exists()
        assert folder.is_dir()
    finally:
        locations.close()


def test_older_research_with_no_declared_findings_remains_readable(tmp_path):
    locations, day, sid, manager = setup(tmp_path)
    try:
        item = session(sid); manager._save(item)
        head = manager.directory(item) / 'session.json'
        value = json.loads(head.read_bytes())
        del value['findingRecords'], value['findingRevisions']
        head.write_text(json.dumps(value))
        verify_research_records(locations.project(sid))
    finally:
        locations.close()


def test_new_outer_hash_cannot_bless_changed_finding_in_an_old_metadata_revision(tmp_path):
    locations, sid, folder = saved_research(tmp_path)
    try:
        root = locations.project(sid)
        snapshot = copy_owned_snapshot(locations, sid, tmp_path / 'Copy')
        old = next((snapshot.path / folder.relative_to(root.path) / 'history').iterdir())
        old.chmod(0o600)
        old.write_bytes(old.read_bytes().replace(b'First observation', b'Wrong observation'))
        seal = snapshot.path / SNAPSHOT
        value = json.loads(seal.read_bytes())
        row = next(row for row in value['entries'] if row['path'] == old.relative_to(snapshot.path).as_posix())
        row['bytes'], row['sha256'] = old.stat().st_size, hashlib.sha256(old.read_bytes()).hexdigest()
        seal.chmod(0o600); seal.write_text(json.dumps(value))
        with pytest.raises(ProjectLocationError, match='Earlier researcher metadata changed'):
            verify_snapshot(snapshot.path)
    finally:
        locations.close()


def test_research_verification_is_bounded_cancellable_and_read_only(tmp_path, monkeypatch):
    locations, sid, folder = saved_research(tmp_path)
    try:
        root = locations.project(sid)
        before = (folder / 'session.json').read_bytes()
        monkeypatch.setattr('orchestrator.project_research_integrity.MAX_RECORDS', 1)
        with pytest.raises(ProjectLocationError, match='allowance'):
            verify_research_records(root)
        def stop(): raise RuntimeError('cancelled')
        with pytest.raises(RuntimeError, match='cancelled'):
            verify_research_records(root, check=stop)
        assert (folder / 'session.json').read_bytes() == before
    finally:
        locations.close()


def test_directory_review_stops_streaming_before_reading_the_rest(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    from orchestrator import project_research_integrity as module
    seen = []
    @contextmanager
    def entries(_):
        def stream():
            for number in range(10):
                seen.append(number)
                yield SimpleNamespace(name=f'entry-{number}')
        yield stream()
    monkeypatch.setattr(module.os, 'scandir', entries)
    monkeypatch.setattr(module, 'MAX_RECORDS', 2)
    with pytest.raises(ProjectLocationError, match='directory allowance'):
        list(module._children(tmp_path, lambda: None))
    assert seen == [0, 1, 2]
    seen.clear()
    def stop(): raise RuntimeError('cancelled')
    with pytest.raises(RuntimeError, match='cancelled'):
        list(module._children(tmp_path, stop))
    assert seen == [0]
