"""Synthetic empty-shadow recovery; no user journal or automatic migration."""
import json
import pytest
from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_recovery import inspect, preserve_shadow
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry


def setup(tmp_path):
    home = tmp_path / 'host'
    locations = ProjectLocations(home / 'sessions', ProjectRegistry(home), tmp_path / 'Projects')
    day = SessionStore(home / 'sessions', session='2026-10-29', locations=locations)
    sid = day.new('Portable research')
    root = locations.project(sid)
    events = day.journal.path.read_bytes()
    locations.close()
    # The earlier detached constructor omitted locations and created this shadow.
    shadow = SessionStore(home / 'sessions', session=sid)
    return home, sid, root, events, shadow


def test_explicit_recovery_preserves_bytes_and_reopens_original_identity(tmp_path):
    home, sid, root, events, shadow = setup(tmp_path)
    original = shadow.journal.path.read_bytes()
    _, review = inspect(home, sid)
    result = preserve_shadow(home, sid, review['shadowSha256'])
    from pathlib import Path
    assert (Path(result['recoveryLocation']) / 'original/journal.jsonl').read_bytes() == original
    assert not shadow.journal.path.exists()
    locations = ProjectLocations(home / 'sessions', ProjectRegistry(home), tmp_path / 'Projects')
    try:
        reopened = SessionStore(home / 'sessions', session=sid, locations=locations)
        assert locations.project(sid) == root
        assert reopened.journal.path.read_bytes() == events
    finally:
        locations.close()


@pytest.mark.parametrize('extra', ['event', 'file', 'torn', 'digest'])
def test_recovery_refuses_nonempty_or_changed_shadow(tmp_path, extra):
    home, sid, root, events, shadow = setup(tmp_path)
    _, review = inspect(home, sid)
    if extra == 'event': shadow.rename_session('Real user work')
    if extra == 'file': (shadow.journal.path.parent / 'notes.txt').write_text('retain me')
    if extra == 'torn':
        with shadow.journal.path.open('ab') as f: f.write(b'{')
    digest = '0' * 64 if extra == 'digest' else review['shadowSha256']
    with pytest.raises(ProjectLocationError): preserve_shadow(home, sid, digest)
    assert shadow.journal.path.exists()
    assert (root.path / 'history/sessions' / sid / 'journal.jsonl').read_bytes() == events


def test_recovery_refuses_active_portable_writer(tmp_path):
    from orchestrator.project_roots import writer
    home, sid, root, events, shadow = setup(tmp_path)
    _, review = inspect(home, sid)
    with writer(root), pytest.raises(ProjectLocationError, match='read-only'):
        preserve_shadow(home, sid, review['shadowSha256'])
    assert shadow.journal.path.exists()
