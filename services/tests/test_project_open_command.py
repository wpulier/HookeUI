"""Owner folder admission uses real synthetic stores; no native chooser qualification."""
import asyncio
from types import SimpleNamespace
import pytest
from orchestrator.commands import COMMANDS
from orchestrator.day.store import SessionStore
from orchestrator.door.projects import ProjectAccess
from orchestrator.project_launch import configured_locations, register_owner_folder
from orchestrator.paths import Paths
from orchestrator.tools import ToolError
from orchestrator.agent_runnable import door_resource, runnable, OWNER_STORAGE_ACTIONS
from tests.test_project_owner_open import source


def test_owner_command_registers_without_navigation_or_inherited_access(tmp_path):
    root, moved, original, saved = source(tmp_path)
    paths = Paths.under(tmp_path / 'receiving-host')
    locations = configured_locations(paths, str(tmp_path / 'Projects'))
    day = SessionStore(paths.sessions, session='2001-01-01', locations=locations)
    before = day.journal.path.read_bytes()
    app = SimpleNamespace(day=day, paths=paths)
    try:
        reply = asyncio.run(COMMANDS['project-open-folder'](app, None, {'session': day.sid, 'path': str(moved)}))
        assert reply['ok'] and reply['current']
        assert reply['project']['origin'] == root.origin
        assert reply['project']['session'] == root.session
        assert locations.project(root.session).path == moved
        assert day.sid == '2001-01-01' and day.journal.path.read_bytes() == before
        assert (moved / 'history/sessions' / root.session / 'journal.jsonl').read_bytes() == original
        assert ProjectAccess(paths.home).rows() == {}
        assert register_owner_folder(paths, locations, str(moved)).origin == root.origin
        assert not door_resource('project-open-folder') and not runnable('project-open-folder')
        assert 'project-open-folder' in OWNER_STORAGE_ACTIONS
    finally:
        locations.close()


def test_stale_owner_request_and_disabled_host_preserve_current_project(tmp_path):
    root, moved, original, _ = source(tmp_path)
    paths = Paths.under(tmp_path / 'receiving-host')
    day = SessionStore(paths.sessions, session='2001-01-01')
    app = SimpleNamespace(day=day, paths=paths)
    for session, message in [('2001-01-02', 'Project changed'), (day.sid, 'not enabled')]:
        with pytest.raises(ToolError, match=message):
            asyncio.run(COMMANDS['project-open-folder'](app, None, {'session': session, 'path': str(moved)}))
    assert not (paths.home / 'project-locations.json').exists()
    assert (moved / 'history/sessions' / root.session / 'journal.jsonl').read_bytes() == original
