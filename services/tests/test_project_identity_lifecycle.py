"""Portable identity through real journals; synthetic cells and worker barriers."""
import asyncio
import threading

import pytest

from orchestrator.identity import Identity
from orchestrator.project_roots import ProjectLocationError
from tests.test_project_evidence_routing import project
from tests.test_project_identity_routing import identities
from tests.waiting import HANG_S, until_async


def test_open_loads_off_loop_and_rejects_stale_completion(project, tmp_path, monkeypatch):
    day, _, _, _ = project
    first = day.sid
    identities(day, tmp_path).make(['p1/c1'])
    day.new('Second')
    second = day.sid
    identities(day, tmp_path).make(['p2/c2'])
    day.open(first)
    entered, release = threading.Event(), threading.Event()
    from orchestrator.project_events import ProjectEvents
    read = ProjectEvents.read
    def blocked(self, store, session, date):
        if session == first:
            entered.set()
            assert release.wait(HANG_S)
        return read(self, store, session, date)
    monkeypatch.setattr(ProjectEvents, 'read', blocked)
    async def run():
        identity = identities(day, tmp_path)
        try:
            await until_async(entered.is_set)
            assert not identity.io.ready
            assert identity.followed_outlines() == []
            with pytest.raises(ProjectLocationError, match='loading'):
                identity.individuals()
            # Opening another Project does not wait for the first history read.
            day.open(second)
            assert identity.session == second and not identity.io.ready
        finally:
            release.set()
        await identity.io.ensure_ready()
        assert set(identity.of) == {'p2/c2'}
        assert identity.session == second
    asyncio.run(run())


def test_admitted_write_finishes_in_original_project_after_ui_switch(project, tmp_path, monkeypatch):
    day, _, _, _ = project
    first = day.sid
    identities(day, tmp_path).make(['p1/c1'])
    other = day.create_retained('Second', actor='user').sid
    entered, release = threading.Event(), threading.Event()
    from orchestrator.project_events import ProjectEvents
    append = ProjectEvents.append
    def blocked(self, store, session, date, event):
        if event['kind'] == 'observed':
            entered.set()
            assert release.wait(HANG_S)
        return append(self, store, session, date, event)
    monkeypatch.setattr(ProjectEvents, 'append', blocked)
    async def run():
        identity = identities(day, tmp_path)
        await identity.io.ensure_ready()
        task = asyncio.create_task(identity.io.mutate('observe', 'p1/c1', {'at': 1}, owner=identity.io.owner()))
        try:
            await until_async(entered.is_set)
            day.open(other)
            assert identity.session == other
        finally:
            release.set()
        await task
        await identity.io.ensure_ready()
        assert identity.observations == {}
        day.open(first)
        await identity.io.ensure_ready()
        assert identity.observations == {'p1/c1': [{'at': 1}]}
    asyncio.run(run())


def test_cancelled_caller_does_not_release_actual_writer_serialization(project, tmp_path, monkeypatch):
    day, _, _, _ = project
    entered, release = threading.Event(), threading.Event()
    from orchestrator.project_events import ProjectEvents
    append = ProjectEvents.append
    observed = []
    def blocked(self, store, session, date, event):
        if event['kind'] == 'observed':
            observed.append(event['observation']['at'])
            if len(observed) == 1:
                entered.set()
                assert release.wait(HANG_S)
        return append(self, store, session, date, event)
    monkeypatch.setattr(ProjectEvents, 'append', blocked)
    async def run():
        identity = identities(day, tmp_path)
        await identity.io.ensure_ready()
        owner = identity.io.owner()
        first = asyncio.create_task(identity.io.mutate('observe', 'p1/c1', {'at': 1}, owner=owner))
        try:
            await until_async(entered.is_set)
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            second = asyncio.create_task(identity.io.mutate('observe', 'p1/c1', {'at': 2}, owner=owner))
            # Let the second admission actually reach its serial lock.
            await until_async(lambda: len(identity.io.tasks) >= 2)
            assert observed == [1]
        finally:
            release.set()
        await second
        assert observed == [1, 2]
        assert identity.observations['p1/c1'] == [{'at': 1}, {'at': 2}]
    asyncio.run(run())


def test_real_async_links_replay_and_refuse_changed_owner(project, tmp_path):
    day, _, _, _ = project
    async def run():
        identity = identities(day, tmp_path)
        await identity.io.ensure_ready()
        owner = identity.io.owner()
        first = await identity.io.mutate('make', ['p1/c1'], owner=owner)
        linked = await identity.io.mutate('same_one', 'p1/c1', 'p2/c2', basis='user', owner=owner)
        assert linked == first
        assert identity.nodes_of(first) == {'p1/c1', 'p2/c2'}
        day.new('Another')
        with pytest.raises(ProjectLocationError, match='changed'):
            await identity.io.mutate('observe', 'p1/c1', {'at': 2}, owner=owner)
        await identity.io.ensure_ready()
        assert identity.individuals() == []
        day.open(owner[0])
        await identity.io.ensure_ready()
        assert identity.nodes_of(first) == {'p1/c1', 'p2/c2'}
    asyncio.run(run())


def test_uncertain_write_cannot_become_empty_ready_history(project, tmp_path, monkeypatch):
    day, _, _, _ = project
    from orchestrator import project_events
    sync = project_events.os.fsync
    async def run():
        identity = identities(day, tmp_path)
        await identity.io.ensure_ready()
        def failed(fd):
            if list(tmp_path.rglob('pending.json')):
                raise OSError('simulated disk full')
            return sync(fd)
        monkeypatch.setattr(project_events.os, 'fsync', failed)
        with pytest.raises(OSError, match='disk full'):
            await identity.io.mutate('observe', 'p1/c1', {'at': 1}, owner=identity.io.owner())
        monkeypatch.setattr(project_events.os, 'fsync', sync)
        assert not identity.io.ready
        with pytest.raises(ProjectLocationError, match='could not be read'):
            identity.individuals()
        identity.io.opened()
        with pytest.raises(ProjectLocationError, match='uncertain'):
            await identity.io.ensure_ready()
        assert not identity.io.ready
    asyncio.run(run())


def test_retirement_refuses_new_admission(project, tmp_path):
    day, _, _, _ = project
    async def run():
        identity = identities(day, tmp_path)
        await identity.io.ensure_ready()
        owner = identity.io.owner()
        identity.io.retire()
        with pytest.raises(ProjectLocationError, match='closed'):
            await identity.io.mutate('observe', 'p1/c1', {'at': 1}, owner=owner)
        assert identity.observations == {}
    asyncio.run(run())


def test_locator_itself_never_runs_on_live_loop(project, tmp_path, monkeypatch):
    day, locations, _, _ = project
    locate = locations.project
    entered, release = threading.Event(), threading.Event()
    loop_thread = threading.get_ident()
    def blocked(session):
        assert threading.get_ident() != loop_thread, 'location I/O ran on live loop'
        entered.set()
        assert release.wait(HANG_S)
        return locate(session)
    monkeypatch.setattr(locations, 'project', blocked)
    async def run():
        identity = identities(day, tmp_path)
        try:
            await until_async(entered.is_set)
            # This coroutine still executes while the location worker is blocked.
            assert not identity.io.ready
            assert identity.followed_outlines() == []
        finally:
            release.set()
        await identity.io.ensure_ready()
        await identity.io.mutate('observe', 'p1/c1', {'at': 1}, owner=identity.io.owner())
        assert identity.observations['p1/c1'] == [{'at': 1}]
    asyncio.run(run())


def test_missing_locator_replaces_old_pending_with_actionable_error(project, tmp_path, monkeypatch):
    day, locations, _, _ = project
    async def run():
        identity = identities(day, tmp_path)
        await identity.io.ensure_ready()
        prior = identity.io.pending
        def missing(_):
            raise ProjectLocationError('Project folder is missing')
        monkeypatch.setattr(locations, 'project', missing)
        identity.io.opened()
        assert identity.io.pending is not prior
        with pytest.raises(ProjectLocationError, match='folder is missing'):
            await identity.io.ensure_ready()
        assert not identity.io.ready and identity.io.error
        with pytest.raises(ProjectLocationError, match='folder is missing'):
            identity.individuals()
    asyncio.run(run())


def test_background_calls_preserve_cross_call_inode_checks(project, tmp_path):
    day, locations, _, _ = project
    async def run():
        identity = identities(day, tmp_path)
        await identity.io.ensure_ready()
        await identity.io.mutate('observe', 'p1/c1', {'at': 1}, owner=identity.io.owner())
        path = await asyncio.to_thread(identity._path)
        original = path.read_bytes()
        path.rename(path.with_name('original.jsonl'))
        path.write_bytes(original)
        with pytest.raises(ProjectLocationError, match='changed'):
            await identity.io.mutate('observe', 'p1/c1', {'at': 2}, owner=identity.io.owner())
        assert path.read_bytes() == original
    asyncio.run(run())
