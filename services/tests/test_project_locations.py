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
        with pytest.raises(ProjectLocationError):
            SessionStore.resume(fresh.legacy, locations=fresh)
        assert not (fresh.legacy / sid).exists()
        fresh.registry.register(ProjectRoot.open(moved), replace_inactive=True)
        restored = SessionStore.resume(fresh.legacy, locations=fresh)
        assert restored.sid == sid
        assert restored.journal.load() == events
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
        with pytest.raises(ProjectLocationError):
            locations.root_for(first.sid)
        assert not (locations.legacy / first.sid).exists()
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
