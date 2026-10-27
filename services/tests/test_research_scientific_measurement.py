"""Synthetic tiny labels, real shared storage/worker transport; no model or native viewer."""
import asyncio
import copy
import threading
import time
from types import SimpleNamespace

import pytest

from orchestrator import scientific_measurement as measurement, scientific_resource_actions as actions
from orchestrator.research import ResearchManager
from orchestrator.research.manager import WorkerAttempt
from orchestrator.research.hands import Hands
from tests.test_research import FakeHub
from tests.test_research_hands import session
from tests.test_scientific_resource_actions import labels


def setup(tmp_path):
    store, saved, _ = labels(tmp_path)
    day = SimpleNamespace(sid='s1', generation=1)
    hub = SimpleNamespace(resources=SimpleNamespace(projects=store), execution_day=day)
    researcher = session(sitting='s1')
    researcher._attempt = WorkerAttempt(0, {}, completion=SimpleNamespace(done=lambda: False),
                                        record={'dispatchedAt': time.time()})
    selected = {'project': saved.id, 'session': 's1', 'revision': saved.revision,
                'input': 'labels', 'digest': saved.digest}
    return hub, researcher, saved, selected


def test_worker_manager_hands_publish_shared_table_and_studio_reuses_it(tmp_path):
    from research_worker.researcher import Researcher
    from research_worker.budget import EFFORTS
    from research_worker.work import SCHEMAS
    from orchestrator.resources import Index

    hub, s, source, selected = setup(tmp_path)
    manager = ResearchManager(FakeHub(), root=tmp_path/'research')
    manager.hands = Hands(hub)
    s.root = tmp_path/'research'
    manager.sessions[s.rid] = s
    worker = Researcher.__new__(Researcher)
    worker.rid, worker.runs = s.rid, 0
    worker.spec = {'run_id': s._attempt.id, 'day': 's1'}
    worker.effort, worker.started_at = EFFORTS['quick'], time.monotonic()
    worker.step = lambda *args, **kwargs: None
    worker.emit = lambda *args, **kwargs: None
    async def request(op, **payload):
        payload.pop('timeout', None)
        return await manager._request({'op': op, **payload})
    worker.request = request
    # Names/selection are discoverable through the existing session index; no caller needs a private store.
    entry = Index.project_entry(source)
    assert entry.title == 'Corrected labels'
    assert entry.version == selected['revision'] and entry.source['sha256'] == selected['digest']
    assert entry.values['state']['scientificView']['inputs']['labels'] == source.document['resources'][0]
    assert 'measure_saved_objects' in SCHEMAS['run']['parameters']['properties']['operation']['enum']
    async def run():
        reply, _ = await worker.tool_run({'operation': 'measure_saved_objects', 'selection': selected, 'why': 'measure saved work'})
        assert reply['ok'], reply
        s.status = 'done'
        again = await measurement.run(hub, hub.execution_day, measurement.OPERATION,
            {k: v for k, v in selected.items() if k != 'digest'}, caller='studio')
        return again
    again = asyncio.run(run())
    assert again['reused']
    assert again['producer']['admission']['caller'] == 'agent:researcher'
    assert again['producer']['admission']['researcher'] == {'id': s.rid, 'run': 0, 'runId': s._attempt.id}
    assert again['invocation']['caller'] == 'studio'
    assert s.ran[0]['source']['digest'] == source.digest
    assert s.ran[0]['output']['digest'] == again['project'].digest
    table = actions.table(hub.resources.projects, source.id, 's1', again['project'].revision)
    assert table['rows'] == [['7', 2, 1., 1.], [str(2**63+7), 2, 0., 1.5]]
    assert hub.resources.projects.read(source.id, 's1', revision=source.revision) == source


@pytest.mark.parametrize('change', ['digest', 'foreign', 'stale', 'mixed'])
def test_bad_selection_never_retargets_or_reads_labels(tmp_path, monkeypatch, change):
    hub, s, source, selected = setup(tmp_path)
    extra = {}
    if change == 'digest': selected['digest'] = '0' * 64
    if change == 'foreign': selected['session'] = 'another'
    if change == 'mixed': extra['input'] = 'e1'
    if change == 'stale':
        hub.resources.projects.save(source.id, 's1', {**source.document, 'title': 'Later draft'}, expected_revision=source.revision)
    before = hub.resources.projects.read(source.id, 's1')
    monkeypatch.setattr(actions, 'blocks', lambda *args: pytest.fail('refused input was read'))
    reply = asyncio.run(Hands(hub).run(s, 'measure_saved_objects', selection=selected, run_id=s._attempt.id, **extra))
    assert not reply['ok'] and not s.ran
    assert hub.resources.projects.read(source.id, 's1') == before


@pytest.mark.parametrize('change', ['project', 'opening', 'run', 'stop', 'attempt'])
def test_changed_original_owner_during_read_cannot_publish(tmp_path, monkeypatch, change):
    hub, s, source, selected = setup(tmp_path)
    real_blocks = actions.blocks
    def blocks(*args):
        for block in real_blocks(*args):
            if change == 'project': hub.execution_day.sid = 'other'
            if change == 'opening': hub.execution_day.generation += 1
            if change == 'run': s.history.append({'q': 'new run'})
            if change == 'stop': s.status = 'cancelled'
            if change == 'attempt': s._attempt = copy.copy(s._attempt)
            yield block
    monkeypatch.setattr(actions, 'blocks', blocks)
    reply = asyncio.run(Hands(hub).run(s, 'measure_saved_objects', selection=selected, run_id=s._attempt.id))
    assert not reply['ok'] and not s.ran
    assert hub.resources.projects.read(source.id, 's1') == source


def test_dispatch_detaches_selection_and_cancellation_owns_io_until_it_stops(tmp_path, monkeypatch):
    hub, s, source, selected = setup(tmp_path)
    original = copy.deepcopy(selected)
    entered, release = threading.Event(), threading.Event()
    real_blocks = actions.blocks
    def blocks(*args):
        for block in real_blocks(*args):
            entered.set()
            assert release.wait(5), 'test did not release the bounded native read'
            yield block
    monkeypatch.setattr(actions, 'blocks', blocks)
    hands = Hands(hub)
    async def run():
        task = asyncio.create_task(hands.run(s, 'measure_saved_objects', selection=selected, run_id=s._attempt.id))
        try:
            assert await asyncio.to_thread(entered.wait, 5)
            selected['input'] = 'other'
            task.cancel()
            await asyncio.sleep(0)  # deliver cancellation while the owned read is still held
            assert hands.busy == s.rid and not task.done()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert hands.busy is None and not s.ran
        assert hub.resources.projects.read(source.id, 's1') == source
        # Explicit retry uses the original accepted selection and real native values.
        reply = await hands.run(s, 'measure_saved_objects', selection=original, run_id=s._attempt.id)
        assert reply['ok']
        assert s.ran[0]['source']['input'] == 'labels'
    asyncio.run(run())


@pytest.mark.parametrize('change', ['missing', 'older', 'undispatched', 'finished'])
def test_late_worker_cannot_adopt_current_research_attempt(tmp_path, monkeypatch, change):
    hub, s, source, selected = setup(tmp_path)
    manager = ResearchManager(FakeHub(), root=tmp_path/'research')
    manager.hands = Hands(hub)
    manager.sessions[s.rid] = s
    message = {'op': 'run', 'rid': s.rid, 'operation': 'measure_saved_objects',
               'selection': selected, 'runId': s._attempt.id}
    if change == 'missing': del message['runId']
    if change == 'older': message['runId'] = 'previous-attempt'
    if change == 'undispatched': s._attempt.record.clear()
    if change == 'finished': s._attempt.completion.done = lambda: True
    monkeypatch.setattr(actions, 'blocks', lambda *args: pytest.fail('late worker read pixels'))
    reply = asyncio.run(manager._request(message))
    assert not reply['ok'] and 'original research run' in reply['error']
    assert hub.resources.projects.read(source.id, 's1') == source


def test_late_timeout_does_not_claim_to_undo_an_accepted_measurement(tmp_path, monkeypatch):
    hub, s, source, selected = setup(tmp_path)
    async def timeout_after_publication(work, timeout):
        await work  # actual shared atomic publication won the race
        raise asyncio.TimeoutError()
    monkeypatch.setattr(asyncio, 'wait_for', timeout_after_publication)
    reply = asyncio.run(Hands(hub).run(s, 'measure_saved_objects', selection=selected,
                                      run_id=s._attempt.id, wait=10))
    assert not reply['ok'] and 'may already have been accepted' in reply['error']
    assert 'nothing was made' not in reply['error']
    assert hub.resources.projects.read(source.id, 's1').revision == source.revision + 1
    assert hub.resources.projects.read(source.id, 's1', revision=source.revision) == source
