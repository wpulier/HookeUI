"""Real isolated operation stores and folder relocation; synthetic work, no agent credentials."""
import asyncio
import json

import pytest

from orchestrator.day.prefs import Prefs
from orchestrator.door.connections import Refused
from orchestrator.door.operations import Operations, _write
from orchestrator.project_locations import ProjectLocations
from orchestrator.project_repository import create
from orchestrator.project_roots import ProjectRegistry, ProjectRoot
from tests.test_agent_operations import AGENT, settle
from tests.waiting import HANG_S, until_async


def setup(tmp_path):
    home = tmp_path / 'host'
    locations = ProjectLocations(home / 'sessions', ProjectRegistry(home), tmp_path / 'Projects')
    root = create(locations.parent, 'Research', session='2026-10-30', host_preferences=Prefs(home))
    locations.registry.register(root)
    return home, locations, root


async def admit(ops, project, work, key='portable-request-0001'):
    return await ops.admit(by=AGENT, label='Connected scientist', tool='local-analysis', project=project,
        input_revision='exact-input', generation='pinned-generation', params={'x': 1}, request_key=key, work=work)


def test_operation_moves_with_project_and_keeps_attribution_without_host_authority(tmp_path):
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        async def work(record, progress):
            saved = json.loads((root.path / 'history/operations' / (record['id'] + '.json')).read_text())
            assert saved['storageOrigin'] == root.origin
            await progress(1, 1, 'Measured')
            return {'measurement': 17}
        first, made = await admit(ops, root.session, work)
        await settle(ops)
        assert made and not (home / 'agents/operations').exists()
        assert ops.get(first['id'])['by'] == AGENT
        assert 'storageOrigin' not in ops.get(first['id'])
        locations.close()
        moved = tmp_path / 'Moved'
        root.path.rename(moved)
        new_home = tmp_path / 'second-host'
        new_locations = ProjectLocations(new_home / 'sessions', ProjectRegistry(new_home), tmp_path / 'Projects')
        new_locations.registry.register(ProjectRoot.open(moved))
        try:
            continued = Operations(new_home, locations=new_locations)
            assert continued.get(first['id']) == ops.get(first['id'])
            again, made = await admit(continued, root.session, work)
            assert not made and again['id'] == first['id']
            assert not continued._tasks
            assert not (new_home / 'agents/connections.json').exists()
            assert not (new_home / 'agents/projects.json').exists()
        finally:
            new_locations.close()
    try: asyncio.run(go())
    finally: locations.close()


def test_second_host_reads_saved_state_without_reconciling_or_cancelling_writer(tmp_path):
    home, locations, root = setup(tmp_path)
    async def go():
        started, finish = asyncio.Event(), asyncio.Event()
        async def work(record, progress):
            started.set(); await asyncio.wait_for(finish.wait(), HANG_S); return {'measurement': 1}
        ops = Operations(home, locations=locations)
        record, _ = await admit(ops, root.session, work)
        await asyncio.wait_for(started.wait(), HANG_S)
        file = root.path / 'history/operations' / (record['id'] + '.json')
        before = file.read_bytes()
        second_locations = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            reader = Operations(tmp_path / 'second', locations=second_locations)
            assert reader.get(record['id'])['storageReadOnly'] is True
            assert reader.get(record['id'])['state'] == 'running'
            assert file.read_bytes() == before
            with pytest.raises(Refused, match='read-only'):
                await reader.cancel(record['id'])
            finish.set(); await settle(ops)
            assert ops.get(record['id'])['state'] == 'succeeded'
        finally: second_locations.close()
    try: asyncio.run(go())
    finally: locations.close()


def test_restart_reconciles_original_record_without_replay(tmp_path):
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        async def work(record, progress): return {'kept': True}
        record, _ = await admit(ops, root.session, work)
        await settle(ops)
        path = root.path / 'history/operations' / (record['id'] + '.json')
        saved = json.loads(path.read_text()); saved['state'] = 'running'
        _write(path, saved)  # isolated crash fixture
        locations.close()
        fresh_locations = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            fresh = Operations(home, locations=fresh_locations)
            assert fresh.get(record['id'])['state'] == 'interrupted'
            assert json.loads(path.read_text())['state'] == 'interrupted'
            assert not fresh._tasks
        finally: fresh_locations.close()
    try: asyncio.run(go())
    finally: locations.close()


def test_failed_final_sync_reuses_same_admission_before_starting_work(tmp_path, monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        calls = []
        async def work(record, progress): calls.append(record['id']); return {'kept': True}
        original = ProjectStore._sync_directory
        def fail(path):
            if path.name == 'operations': raise OSError('injected final sync failure')
            return original(path)
        monkeypatch.setattr(ProjectStore, '_sync_directory', fail)
        with pytest.raises(Refused, match='could not record'):
            await admit(ops, root.session, work)
        files = list((root.path / 'history/operations').glob('op_*.json'))
        assert len(files) == 1 and calls == []
        monkeypatch.setattr(ProjectStore, '_sync_directory', original)
        again, made = await admit(ops, root.session, work)
        await settle(ops)
        assert made and again['id'] == files[0].stem and calls == [again['id']]
        assert len(list(files[0].parent.glob('op_*.json'))) == 1
    try: asyncio.run(go())
    finally: locations.close()


def test_conflicting_locations_refuse_before_recovery_writes(tmp_path):
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        async def work(record, progress): return {'kept': True}
        record, _ = await admit(ops, root.session, work)
        await settle(ops)
        portable = root.path / 'history/operations' / (record['id'] + '.json')
        saved = json.loads(portable.read_text()); saved['state'] = 'running'
        _write(home / 'agents/operations' / portable.name, saved)
        before = portable.read_bytes()
        with pytest.raises(ValueError, match='Conflicting operation histories'):
            Operations(home, locations=locations)
        assert portable.read_bytes() == before
    try: asyncio.run(go())
    finally: locations.close()


def test_replaced_folder_never_falls_back_or_recreates_missing_history(tmp_path):
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        async def work(record, progress): return {'kept': True}
        record, _ = await admit(ops, root.session, work)
        await settle(ops)
        folder = root.path / 'history/operations'
        folder.rename(root.path / 'history/preserved-operations')
        folder.mkdir()
        with pytest.raises(ValueError, match='storage changed'):
            await ops._save(ops._records[record['id']])
        assert not list(folder.iterdir()) and not (home / 'agents/operations').exists()
    try: asyncio.run(go())
    finally: locations.close()


def test_workflow_receipt_on_second_host_is_observation_not_execution(tmp_path):
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        record, _ = await ops.admit(by=AGENT, label='Scientist', tool='workflow', project=root.session,
            input_revision='exact', generation='pinned', params={}, request_key='workflow-request-001',
            work=None, execution={'kind': 'workflow', 'id': 'p' + 'a' * 32, 'session': root.session})
        file = root.path / 'history/operations' / (record['id'] + '.json')
        before = file.read_bytes()
        other = ProjectLocations(locations.legacy, locations.registry, locations.parent)
        try:
            reader = Operations(tmp_path / 'other', locations=other)
            assert reader.get(record['id'])['storageReadOnly'] is True
            assert reader.get(record['id'])['execution'] == record['execution']
            with pytest.raises(Refused, match='read-only'):
                await reader.cancel(record['id'])
            assert file.read_bytes() == before and not reader._tasks
        finally: other.close()
    try: asyncio.run(go())
    finally: locations.close()


def test_missing_project_keeps_healthy_history_readable_but_refuses_unknown_keys(tmp_path):
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        async def work(record, progress): return {'kept': True}
        record, _ = await admit(ops, root.session, work)
        await settle(ops)
        missing = create(locations.parent, 'Offline', session='2026-10-30.2', host_preferences=Prefs(home))
        locations.registry.register(missing)
        missing.path.rename(tmp_path / 'Detached')
        fresh = Operations(home, locations=locations)
        assert fresh.get(record['id'])['state'] == 'succeeded'
        assert any('2026-10-30.2' in item for item in fresh.unreadable)
        with pytest.raises(Refused, match='history is unavailable'):
            await admit(fresh, root.session, work, key='another-request-0001')
    try: asyncio.run(go())
    finally: locations.close()


def test_cancelled_admission_drains_writer_before_same_key_retry(tmp_path, monkeypatch):
    import threading
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        entered, release = threading.Event(), threading.Event()
        original = ops._persist
        calls = []
        def delayed(record):
            if not entered.is_set():
                entered.set()
                assert release.wait(HANG_S)
            original(record)
        monkeypatch.setattr(ops, '_persist', delayed)
        async def work(record, progress): calls.append(record['id']); return {'kept': True}
        first = asyncio.create_task(admit(ops, root.session, work))
        retry = None
        try:
            await until_async(entered.is_set)
            first.cancel()
            retry = asyncio.create_task(admit(ops, root.session, work))
            # Repeated cancellation must not release the writer's lock either.
            first.cancel()
            assert not first.done() and not retry.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(first, HANG_S)
            record, made = await asyncio.wait_for(retry, HANG_S)
            await settle(ops)
            files = list((root.path / 'history/operations').glob('op_*.json'))
            assert made and calls == [record['id']] and len(files) == 1
            assert json.loads(files[0].read_text())['state'] == 'succeeded'
        finally:
            release.set()
            await asyncio.gather(first, *([retry] if retry is not None else []), return_exceptions=True)
    try: asyncio.run(go())
    finally: locations.close()


@pytest.mark.parametrize('budget', ['MAX_RECORDS', 'MAX_HISTORY_BYTES'])
def test_invalid_candidates_count_toward_history_loading_budget(tmp_path, monkeypatch, budget):
    from orchestrator.door import operations
    folder = tmp_path / 'agents/operations'
    folder.mkdir(parents=True)
    for number in range(3):
        (folder / f'op_{number:016x}.json').write_text('invalid')
    monkeypatch.setattr(operations, budget, 2)
    with pytest.raises(ValueError, match='allowance'):
        Operations(tmp_path)


def test_unreadable_directory_cannot_be_treated_as_new_request_history(tmp_path, monkeypatch):
    from orchestrator.door import operations
    home, locations, root = setup(tmp_path)
    async def go():
        ops = Operations(home, locations=locations)
        calls = []
        async def work(record, progress): calls.append(record['id']); return {'kept': True}
        record, _ = await admit(ops, root.session, work)
        await settle(ops)
        folder = root.path / 'history/operations'
        scan = operations.os.scandir
        def denied(path):
            if path == folder: raise PermissionError('injected unreadable directory')
            return scan(path)
        monkeypatch.setattr(operations.os, 'scandir', denied)
        fresh = Operations(home, locations=locations)
        monkeypatch.setattr(operations.os, 'scandir', scan)
        with pytest.raises(Refused, match='history is unavailable'):
            await admit(fresh, root.session, work)
        assert calls == [record['id']]
        reloaded = Operations(home, locations=locations)
        same, made = await admit(reloaded, root.session, work)
        assert not made and same['id'] == record['id']
    try: asyncio.run(go())
    finally: locations.close()


def test_directory_entry_budget_includes_unmatched_files(tmp_path, monkeypatch):
    from orchestrator.door import operations
    folder = tmp_path / 'agents/operations'
    folder.mkdir(parents=True)
    for number in range(3): (folder / f'unmatched-{number}').touch()
    monkeypatch.setattr(operations, 'MAX_RECORDS', 2)
    with pytest.raises(ValueError, match='entry allowance'): Operations(tmp_path)
