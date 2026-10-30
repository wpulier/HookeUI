"""Owner command admission over real stores and synthetic shared Record bytes."""
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator.commands import COMMANDS
from orchestrator.door.operations import Operations
from orchestrator.door.schema import check
from orchestrator.project_record_inclusion import prepare, include
from orchestrator.project_roots import ProjectLocationError
from orchestrator.tools import ToolError
from test_project_media import setup
from test_project_record_collection import dataset, retained
from tests.waiting import until_async


def capture(tmp_path):
    original = dataset(tmp_path)
    parent = tmp_path / 'datasets'; parent.mkdir()
    folder = parent / 'ds-20261030-120000'; original.rename(folder)
    summary = json.loads((folder / 'dataset.json').read_text())
    summary['id'] = folder.name
    (folder / 'dataset.json').write_text(json.dumps(summary))
    return parent, folder


def test_review_whole_scope_then_copy_keeps_shared_bytes_and_warnings(tmp_path):
    locations, day, session, stores, _ = setup(tmp_path)
    parent, folder = capture(tmp_path)
    try:
        _, plan = prepare(day, stores, parent, session, folder.name)
        check(plan, 'project-record-review')
        assert plan['scope'] == 'whole-record-folder'
        assert plan['captureCheck']['ok'] is False
        assert plan['requiredFreeBytes'] > plan['bytes']
        receipt = include(day, stores, parent, session, folder.name, plan['reviewDigest'], 'op_' + 'a' * 24)
        check(receipt, 'project-record-inclusion')
        from orchestrator.project_media import MediaReference
        ref = receipt['reference']
        saved, files = retained(stores, MediaReference(receipt['origin'], session, ref['id'], ref['revision'], ref['digest']))
        assert files['events.jsonl'] == (folder / 'events.jsonl').read_bytes()
        assert b'"session":"B"' in files['events.jsonl']
        assert saved.document['state']['sourceImport']['producer']['actor'] == 'user'
        assert folder.exists() and receipt['externalReferences'] == 'not-collected'
    finally:
        locations.close()


@pytest.mark.parametrize('change', ['bytes', 'identity', 'path', 'symlink'])
def test_invalid_selection_or_changed_review_refuses_without_publication(tmp_path, change):
    locations, day, session, stores, _ = setup(tmp_path)
    parent, folder = capture(tmp_path)
    try:
        _, plan = prepare(day, stores, parent, session, folder.name)
        identity = folder.name
        if change == 'bytes': (folder / 'events.jsonl').write_text('changed\n')
        if change == 'identity':
            value = json.loads((folder / 'dataset.json').read_text()); value['id'] = 'different'
            (folder / 'dataset.json').write_text(json.dumps(value))
        if change == 'path': identity = '../' + identity
        if change == 'symlink':
            moved = tmp_path / 'elsewhere'; folder.rename(moved); folder.symlink_to(moved)
        with pytest.raises((ValueError, OSError)):
            include(day, stores, parent, session, identity, plan['reviewDigest'], 'op_' + 'b' * 24)
        assert stores.list(session) == []
    finally:
        locations.close()


def test_owner_admission_is_idempotent_keeps_destination_and_refuses_unrelated_status(tmp_path):
    locations, day, session, stores, _ = setup(tmp_path)
    parent, folder = capture(tmp_path)
    async def run():
        operations = Operations(tmp_path / 'host', locations=locations)
        app = SimpleNamespace(day=day, hub=SimpleNamespace(resources=SimpleNamespace(projects=stores)),
            paths=SimpleNamespace(datasets=parent), agent_operations=operations)
        review = (await COMMANDS['project-record-review'](app, None, {'session': session, 'dataset': folder.name}))['review']
        params = {'session': session, 'dataset': folder.name, 'reviewDigest': review['reviewDigest'],
            'requestKey': 'owner-record-inclusion-01', 'includeWholeDataset': True}
        with pytest.raises(ToolError):
            await COMMANDS['project-record-include'](app, None, {**params, 'includeWholeDataset': False})
        first = await COMMANDS['project-record-include'](app, None, params)
        retry = await COMMANDS['project-record-include'](app, None, params)
        assert first['operation']['id'] == retry['operation']['id']
        day.new('Another Project')
        await until_async(lambda: operations.get(first['operation']['id'])['state'] in ('succeeded', 'failed', 'cancelled'))
        row = operations.get(first['operation']['id'])
        assert row['state'] == 'succeeded', row
        assert row['by'] == 'user' and row['outcome']['inclusion']['session'] == session
        assert day.sid != session
        with pytest.raises(ToolError, match='Project changed'):
            await COMMANDS['project-record-status'](app, None, {'session': session, 'operation': row['id']})
        from orchestrator.agent_runnable import OWNER_STORAGE_ACTIONS, door_resource
        assert 'project-record-include' in OWNER_STORAGE_ACTIONS
        assert not door_resource('project.record.include')
    try:
        asyncio.run(run())
    finally:
        locations.close()
