"""Journal routing with real isolated stores; live App/storage integration is separate."""
import json

import pytest

from orchestrator.day.store import SessionStore
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_repository import ProjectRepository, create
from orchestrator.project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot


def setup(tmp_path):
    legacy = tmp_path / 'host/sessions'
    day = SessionStore(legacy, session='2026-10-29')
    locations = ProjectLocations(legacy, ProjectRegistry(tmp_path / 'host'), tmp_path / 'Projects')
    routed = SessionStore(legacy, session=day.sid, locations=locations)
    return routed, locations


def test_new_and_background_projects_share_catalogue_but_not_journals(tmp_path):
    day, locations = setup(tmp_path)
    try:
        original = day.sid
        background = day.create_retained('Analysis', actor='agent:connection-a')
        root = locations.project(background.sid)
        assert day.sid == original
        assert day.user_prefs.current is None
        assert root.path.parent == tmp_path / 'Projects'
        assert not (locations.legacy / background.sid).exists()
        background.rename_session('Analysis, continued')
        first = day.new('Microscopy')
        assert first not in (original, background.sid)
        day.elsewhere(background.sid).rename_session('Filed by original owner')
        assert day.name == 'Microscopy'
        assert {i.id for i in day.catalogue.list()} == {original, background.sid, first}
        day.switch_session(background.sid)
        assert day.name == 'Filed by original owner'
        assert day.journal.root == root.path / 'history/sessions'
        assert not list(root.path.rglob('preferences.jsonl'))
        assert ProjectRepository(root).owner()['metadata']['name'] == 'Filed by original owner'
    finally:
        locations.close()


def test_writer_survives_project_switch_and_releases_at_host_close(tmp_path):
    day, locations = setup(tmp_path)
    background = day.create_retained('Retained', actor='agent:a')
    root = locations.project(background.sid)
    second = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        day.new('Another Project')
        assert ProjectRepository(root).events()
        with pytest.raises(ProjectLocationError, match='read-only'):
            SessionStore(second.legacy, session=background.sid, locations=second)
        locations.close()
        with pytest.raises(ProjectLocationError, match='closed'):
            background.rename_session('late disconnected write')
        continued = SessionStore(second.legacy, session=background.sid, locations=second)
        continued.rename_session('Continued in second host')
        assert ProjectRepository(root).owner()['metadata']['name'] == 'Continued in second host'
    finally:
        locations.close(); second.close()


def test_move_resume_retains_conversation_and_workspace_without_replay(tmp_path):
    day, locations = setup(tmp_path)
    sid = day.new('Mixed research')
    day._record({'type': 'line', 'thread': day.active_id,
                 'line': {'lineId': 'question', 'role': 'user', 'text': 'Compare the retained evidence'}})
    day._record({'type': 'workflow-observed', 'operation': 'op-original', 'state': 'running'})
    root = locations.project(sid)
    events = ProjectRepository(root).events()
    locations.close()
    moved = tmp_path / 'Moved Project'
    root.path.rename(moved)
    fresh = ProjectLocations(locations.legacy, locations.registry, locations.parent)
    try:
        fallback = SessionStore.resume(fresh.legacy, locations=fresh)
        assert fallback.sid != sid
        assert fallback.catalogue.get(sid).unavailable
        assert not (fresh.legacy / sid).exists()
        fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
        fallback.open(sid)
        restored = SessionStore.resume(fresh.legacy, locations=fresh)
        assert restored.sid == sid
        assert restored.journal.load()[:len(events)] == events
        assert any(e.get('operation') == 'op-original' for e in restored.journal.load())
    finally:
        fresh.close()


def test_offline_locator_reserves_handle_without_hiding_other_projects(tmp_path):
    day, locations = setup(tmp_path)
    try:
        first = day.create_retained('Offline', actor='agent:a')
        root = locations.project(first.sid)
        root.path.rename(tmp_path / 'unmounted')
        second = day.create_retained('Available', actor='agent:a')
        assert second.sid != first.sid
        assert second.name == 'Available'
        listed = {i.id: i for i in day.catalogue.list()}
        assert listed[first.sid].unavailable
        assert listed[first.sid].wire()['storageUnavailable']
        assert listed[second.sid].unavailable is None
        assert day.catalogue.find('Available')[0].id == second.sid
        assert day.catalogue.last_used().unavailable is None
        with pytest.raises(ProjectLocationError, match='shared legacy result IDs'):
            day.catalogue.used(day.date, 'e')
        with pytest.raises(ProjectLocationError):
            locations.root_for(first.sid)
        assert not (locations.legacy / first.sid).exists()
    finally:
        locations.close()


def test_failed_post_lock_verification_never_caches_or_retains_lease(tmp_path, monkeypatch):
    import orchestrator.project_locations as module
    from orchestrator.project_roots import writer
    day, locations = setup(tmp_path)
    root = create(locations.parent, 'Verification failure', session='2026-10-29.8',
                  host_preferences=day.user_prefs)
    locations.registry.register(root)
    verify, calls = module.verify_owner, []
    def change_after_acquisition(candidate):
        calls.append(candidate)
        if len(calls) == 2:
            path = root.path / 'history/sessions' / root.session / 'journal.jsonl'
            lines = path.read_text().splitlines()
            first = json.loads(lines[0]); first['origin'] = 'f' * 32
            lines[0] = json.dumps(first)
            path.write_text('\n'.join(lines) + '\n')
        return verify(candidate)
    monkeypatch.setattr(module, 'verify_owner', change_after_acquisition)
    try:
        with pytest.raises(ProjectLocationError, match='do not match'):
            locations.writable(root.session)
        assert not locations._leases
        with writer(root):
            pass  # failure relinquished the filesystem lock as well as its cache
        with pytest.raises(ProjectLocationError, match='do not match'):
            locations.writable(root.session)
    finally:
        locations.close()


def test_same_handle_different_origin_refuses_without_overwriting(tmp_path):
    day, locations = setup(tmp_path)
    try:
        root = create(locations.parent, 'Conflicting import', session=day.sid,
                      host_preferences=day.user_prefs)
        locations.registry.register(root)
        before = day.journal.path.read_bytes()
        with pytest.raises(ProjectLocationError, match='different legacy'):
            day.rename_session('must not be recorded')
        assert day.journal.path.read_bytes() == before
        assert ProjectRepository(root).owner()['metadata']['name'] == 'Conflicting import'
    finally:
        locations.close()


def test_old_registry_upgrade_retains_origin_and_new_missing_locator_is_readable(tmp_path):
    day, locations = setup(tmp_path)
    try:
        root = create(locations.parent, 'Old locator', session='2026-10-29.8',
                      host_preferences=day.user_prefs)
        locations.registry.path.write_text(json.dumps({'format': 1, 'locations': {root.origin: str(root.path)}}))
        assert locations.registry.locate(root.origin) == root
        locations.registry.register(root)
        assert json.loads(locations.registry.path.read_text())['format'] == 2
        root.path.rename(tmp_path / 'offline')
        assert locations.handles()[root.session] == root.origin
    finally:
        locations.close()


def test_moved_format_one_location_requires_explicit_matching_recovery(tmp_path):
    day, locations = setup(tmp_path)
    try:
        root = create(locations.parent, 'Old location', session='2026-10-29.9',
                      host_preferences=day.user_prefs)
        locations.registry.path.write_text(json.dumps({'format': 1, 'locations': {root.origin: str(root.path)}}))
        moved = tmp_path / 'moved-old-project'
        root.path.rename(moved)
        reopened = ProjectRoot.open(moved)
        with pytest.raises(ProjectLocationError):
            locations.registry.register(reopened)
        locations.registry.register(reopened, replace_inactive=True)
        assert locations.root_for(root.session) == moved / 'history/sessions'
    finally:
        locations.close()


def test_malformed_owner_is_unavailable_without_hiding_healthy_projects(tmp_path):
    day, locations = setup(tmp_path)
    root = create(locations.parent, 'Damaged owner', session='2026-10-29.8',
                  host_preferences=day.user_prefs)
    locations.registry.register(root)
    path = root.path / 'history/sessions' / root.session / 'journal.jsonl'
    first = json.loads(path.read_text().splitlines()[0])
    with path.open('a') as stream:
        stream.write(json.dumps(first) + '\n')
    try:
        listed = {i.id: i for i in day.catalogue.list()}
        assert 'multiple origins' in listed[root.session].unavailable
        assert listed[day.sid].unavailable is None
        with pytest.raises(ProjectLocationError, match='multiple origins'):
            locations.writable(root.session)
    finally:
        locations.close()


@pytest.mark.parametrize('failure', ['missing', 'unreadable'])
def test_leased_journal_failure_does_not_allow_partial_id_allocation(tmp_path, monkeypatch, failure):
    from pathlib import Path
    day, locations = setup(tmp_path)
    saved = day.create_retained('Leased', actor='agent:a')
    saved._record({'type': 'made', 'id': 'e999', 'kind': 'population'})
    root = locations.project(saved.sid)
    path = root.path / 'history/sessions' / root.session / 'journal.jsonl'
    day.catalogue.list()  # cache a healthy row before failure
    if failure == 'missing':
        path.unlink()
    else:
        with path.open('a') as stream:
            stream.write('\n')  # require the next catalogue read
        original = Path.open
        def refuse(self, *args, **kwargs):
            if self == path:
                raise PermissionError('test journal unreadable')
            return original(self, *args, **kwargs)
        monkeypatch.setattr(Path, 'open', refuse)
    try:
        listed = {i.id: i for i in day.catalogue.list()}
        assert listed[saved.sid].unavailable
        assert listed[day.sid].unavailable is None
        with pytest.raises(ProjectLocationError, match='shared legacy result IDs'):
            day.catalogue.used(day.date, 'e')
    finally:
        locations.close()


def test_delayed_quiz_reply_uses_original_portable_project(tmp_path):
    from orchestrator.quiz import Quiz
    from orchestrator.day.store import Asked
    day, locations = setup(tmp_path)
    try:
        sid = day.new('Original quiz')
        original = Asked(day.active_id, day.generation, day=sid, session=sid)
        root = locations.project(sid)
        day.new('Later selection')
        owner, reply = Quiz.reply_store(day, original)
        assert owner.sid == sid and owner.journal.root == root.path / 'history/sessions'
        assert reply.thread == original.thread
        assert not (locations.legacy / sid).exists()
    finally:
        locations.close()
