"""Actual isolated stores/copies with synthetic material; no complete-collection claim."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.commands import COMMANDS
from orchestrator.door import schema
from orchestrator.door.operations import Operations
from orchestrator.project_export import export
from orchestrator.project_working_copy import prepare, restore, reveal
from orchestrator.project_roots import ProjectLocationError, ProjectRoot, SNAPSHOT, COPY_INTENT
from orchestrator.tools import ToolError
from tests.test_project_locations import setup
from tests.test_agent_operations import settle


def source(day, locations):
    session = day.new('Research')
    receipt = export(day, session, 'op_' + 'a' * 24, acknowledge_incomplete=True)
    return session, receipt


def test_working_copy_preserves_identity_history_and_partial_scope_without_registration(tmp_path):
    day, locations = setup(tmp_path)
    try:
        session, exported = source(day, locations)
        registry = locations.registry.path.read_bytes()
        original = day.journal.path.read_bytes()
        plan = prepare(day, session, exported)
        assert plan['requiredFreeBytes'] > plan['bytes']
        receipt = restore(day, session, exported, 'op_' + 'b' * 24)
        schema.check(receipt, 'project-working-copy')
        destination = reveal(day, session, receipt)
        copied = ProjectRoot.open(destination)
        assert copied.origin == locations.project(session).origin
        assert not (destination / SNAPSHOT).exists()
        assert (destination / 'history/restorations' / exported['manifestSha256'] / SNAPSHOT).is_file()
        assert (destination / 'history/sessions' / session / 'journal.jsonl').read_bytes() == original
        assert Path(exported['location'], SNAPSHOT).is_file()
        assert locations.registry.path.read_bytes() == registry
        assert receipt['collection']['state'] == 'unverified'
        assert not list(destination.rglob('connections.json'))
        with pytest.raises(FileExistsError):
            restore(day, session, exported, 'op_' + 'b' * 24)
    finally:
        locations.close()


def test_restoration_refuses_changed_receipt_and_insufficient_space(tmp_path, monkeypatch):
    from orchestrator import project_folder_copy
    day, locations = setup(tmp_path)
    try:
        session, exported = source(day, locations)
        with pytest.raises(ProjectLocationError, match='manifest changed'):
            restore(day, session, {**exported, 'manifestSha256': '0' * 64}, 'op_' + 'c' * 24)
        monkeypatch.setattr(project_folder_copy.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
        with pytest.raises(ProjectLocationError, match='no copy was started'):
            restore(day, session, exported, 'op_' + 'c' * 24)
        assert not list((locations.parent / 'Restored').iterdir())
    finally:
        locations.close()


def test_cancellation_keeps_incomplete_destination_and_original_snapshot(tmp_path):
    day, locations = setup(tmp_path)
    try:
        session, exported = source(day, locations)
        def stop():
            if list((locations.parent / 'Restored').glob('*/' + COPY_INTENT)):
                raise RuntimeError('stopped')
        with pytest.raises(RuntimeError, match='stopped'):
            restore(day, session, exported, 'op_' + 'd' * 24, check=stop)
        assert len(list((locations.parent / 'Restored').glob('*/' + COPY_INTENT))) == 1
        assert Path(exported['location'], SNAPSHOT).is_file()
    finally:
        locations.close()


def test_restore_snapshot_checks_reviewed_manifest_before_creating_destination(tmp_path):
    from orchestrator.project_folder_copy import restore_snapshot
    day, locations = setup(tmp_path)
    try:
        _, exported = source(day, locations)
        destination = tmp_path / 'never-created'
        with pytest.raises(ProjectLocationError, match='reviewed export'):
            restore_snapshot(Path(exported['location']), destination, expected_manifest_sha256='0' * 64)
        assert not destination.exists()
    finally:
        locations.close()


def test_owner_operation_is_durable_idempotent_and_does_not_switch_or_grant(tmp_path):
    from orchestrator import agent_runnable
    day, locations = setup(tmp_path)
    async def scenario():
        session = day.new('Research')
        app = SimpleNamespace(day=day, agent_operations=Operations(tmp_path / 'host', locations=locations))
        other = day.create_retained('Other', actor='user').sid
        reply = await COMMANDS['project-export'](app, None, {'session': session,
            'requestKey': 'restoration-source', 'acknowledgeIncomplete': True})
        await settle(app.agent_operations)
        source_id = reply['operation']['id']
        message = {'session': session, 'operation': source_id, 'requestKey': 'working-copy-01', 'acknowledgeIncomplete': True}
        plan = await COMMANDS['project-copy-prepare'](app, None, message)
        assert plan['copy']['requiredFreeBytes'] > plan['copy']['bytes']
        result = await COMMANDS['project-copy-restore'](app, None, message)
        again = await COMMANDS['project-copy-restore'](app, None, message)
        assert again['operation']['id'] == result['operation']['id']
        day.switch_session(other)
        await settle(app.agent_operations)
        kept = app.agent_operations.get(result['operation']['id'])
        assert kept['state'] == 'succeeded' and kept['by'] == 'user'
        assert kept['outcome']['copy']['session'] == session
        assert day.sid == other
        with pytest.raises(ToolError, match='Project changed'):
            await COMMANDS['project-copy-status'](app, None, {'session': session, 'operation': kept['id']})
        assert 'project-copy-restore' in agent_runnable.OWNER_STORAGE_ACTIONS
        assert not agent_runnable.door_resource('project.restore')
    try:
        asyncio.run(scenario())
    finally:
        locations.close()
