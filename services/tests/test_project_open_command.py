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
from tests.test_project_launch import relocated


def test_owner_relocation_keeps_identity_history_and_existing_grants(tmp_path):
    paths, parent, root, moved, before = relocated(tmp_path)
    access = ProjectAccess(paths.home)
    access.remember(root.session, 'agent:ac_' + 'a' * 24)
    grants = access.path.read_bytes()
    locations = configured_locations(paths, str(parent))
    day = SessionStore(paths.sessions, session='2001-01-01', locations=locations)
    app = SimpleNamespace(day=day, paths=paths)
    message = {'session': day.sid, 'path': str(moved)}
    try:
        registry = locations.registry.path.read_bytes()
        with pytest.raises(ToolError, match='Use this location'):
            asyncio.run(COMMANDS['project-open-folder'](app, None, message))
        assert locations.registry.path.read_bytes() == registry
        result = asyncio.run(COMMANDS['project-open-folder'](app, None, {**message, 'useLocation': True}))
        assert result['project']['origin'] == root.origin
        assert locations.project(root.session).path == moved
        assert day.sid == '2001-01-01'
        assert access.path.read_bytes() == grants
        assert (moved / 'history/sessions' / root.session / 'journal.jsonl').read_bytes() == before
    finally:
        locations.close()


@pytest.mark.parametrize('locked', ['original', 'selected'])
def test_relocation_refuses_either_active_copy_and_keeps_locator(tmp_path, locked):
    import shutil
    from orchestrator.project_roots import ProjectRoot, writer
    paths, parent, root, moved, _ = relocated(tmp_path)
    shutil.copytree(moved, root.path)
    locations = configured_locations(paths, str(parent))
    before = locations.registry.path.read_bytes()
    try:
        selected = root if locked == 'original' else ProjectRoot.open(moved)
        with writer(selected), pytest.raises(ValueError, match='Another Hooke host'):
            register_owner_folder(paths, locations, str(moved), use_location=True)
        assert locations.registry.path.read_bytes() == before
    finally:
        locations.close()


def test_relocation_never_reinterprets_unknown_origin_as_authorized(tmp_path):
    root, moved, _, _ = source(tmp_path)
    paths = Paths.under(tmp_path / 'receiving-host')
    locations = configured_locations(paths, str(tmp_path / 'Projects'))
    try:
        with pytest.raises(ValueError, match='not registered here'):
            register_owner_folder(paths, locations, str(moved), use_location=True)
        for value in ('true', 1, None):
            with pytest.raises(ValueError, match='explicitly'):
                register_owner_folder(paths, locations, str(moved), use_location=value)
        assert not locations.registry.path.exists()
        assert ProjectAccess(paths.home).rows() == {}
    finally:
        locations.close()


def test_missing_old_path_does_not_erase_a_host_held_writer(tmp_path):
    import shutil
    paths, parent, root, moved, _ = relocated(tmp_path)
    moved.rename(root.path)
    locations = configured_locations(paths, str(parent))
    try:
        locations.writable(root.session)
        # A cross-volume move can leave an independent copy while the old lease
        # inode survives at a different location. No accepted files are deleted.
        shutil.copytree(root.path, moved)
        root.path.rename(tmp_path / 'Original still held by host')
        before = locations.registry.path.read_bytes()
        with pytest.raises(ValueError, match='still owns the original'):
            register_owner_folder(paths, locations, str(moved), use_location=True)
        assert locations.registry.path.read_bytes() == before
    finally:
        locations.close()


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


def test_slow_open_verification_leaves_current_project_loop_responsive(tmp_path, monkeypatch):
    import threading
    from orchestrator import project_snapshot_integrity
    from tests.waiting import HANG_S, until_async
    root, moved, _, _ = source(tmp_path)
    paths = Paths.under(tmp_path / 'receiving-host')
    locations = configured_locations(paths, str(tmp_path / 'Projects'))
    day = SessionStore(paths.sessions, session='2001-01-01', locations=locations)
    entered, release = threading.Event(), threading.Event()
    original = project_snapshot_integrity.verify_records
    def held(*args, **kwargs):
        entered.set()
        assert release.wait(HANG_S)
        return original(*args, **kwargs)
    monkeypatch.setattr(project_snapshot_integrity, 'verify_records', held)
    async def scenario():
        pending = asyncio.create_task(COMMANDS['project-open-folder'](
            SimpleNamespace(day=day, paths=paths), None, {'session': day.sid, 'path': str(moved)}))
        await until_async(entered.is_set)
        # A separate watchdog prevents a bad shared lock from hanging this test.
        watchdog = threading.Timer(HANG_S / 2, release.set); watchdog.start()
        try:
            day._record({'type': 'note', 'text': 'Current Project stays responsive'})
            await asyncio.sleep(0)
            assert not release.is_set(), 'Current Project activity waited for unrelated folder verification'
        finally:
            release.set(); watchdog.cancel()
        assert (await pending)['ok']
    try:
        asyncio.run(scenario())
    finally:
        release.set(); locations.close()


def test_host_retired_during_verification_never_publishes_locator(tmp_path, monkeypatch):
    from orchestrator import project_snapshot_integrity
    root, moved, _, _ = source(tmp_path)
    paths = Paths.under(tmp_path / 'receiving-host')
    locations = configured_locations(paths, str(tmp_path / 'Projects'))
    original = project_snapshot_integrity.verify_records
    def retiring(*args, **kwargs):
        result = original(*args, **kwargs)
        locations.retire()
        return result
    monkeypatch.setattr(project_snapshot_integrity, 'verify_records', retiring)
    with pytest.raises(ValueError, match='closed'):
        register_owner_folder(paths, locations, str(moved))
    assert not locations.registry.path.exists()
