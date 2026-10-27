"""Real Screen/storage with synthetic workers; one opt-in actual napari worker case."""
import asyncio
import uuid
from contextlib import contextmanager
import copy
import threading

import pytest

from orchestrator import owned_process
from orchestrator.scientific_reviews import ScientificReviews
from orchestrator.workspace_observation import ObservationError
from tests.test_scientific_workspace import Rig
from tests.test_scientific_host_worker import frame
from tests.waiting import HANG_S, until_async
from tests.test_scientific_host_worker import actual_host  # noqa: F401


class Worker:
    def __init__(self):
        self.requests = []
        self.block = False
        self.started = threading.Event()

    def request(self, request):
        self.requests.append(copy.deepcopy(request))
        self.started.set()
        while self.block:
            owned_process._bound.get().check()
            threading.Event().wait(.01)
        if request['op'] in ('python', 'python-resources'):
            return {'ok': True, 'console': {'format': 'scientific-console-v1', 'sequence': 1, 'stdout': '', 'stderr': '', 'result': '2', 'error': '', 'truncated': False, 'privatePreview': True}}, b''
        if request['op'] == 'python-assist':
            from env_worker.scientific_python_help import assist
            return {'ok':True, 'assistance':assist({'saved':2}, request['mode'], request['expression'])}, b''
        return frame(), b'0' * 24


class ReviewsRig(Rig):
    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.workers, self.closed, self.threads = [], [], []
        self.entered = threading.Event()
        self.block_start = False
        self.manager = ScientificReviews(self.screen, self.store, lambda _: self.layout, lambda: self.saved.session)
        self.manager._open_worker = self.open_worker

    @contextmanager
    def open_worker(self, origin):
        self.entered.set()
        while self.block_start:
            owned_process._bound.get().check()
            threading.Event().wait(.01)
        worker = Worker()
        self.workers.append(worker)
        self.threads.append(threading.get_ident())
        try:
            yield self.saved, worker
        finally:
            self.closed.append(worker)
            assert threading.get_ident() == self.threads[self.workers.index(worker)]

    def paint(self, identity):
        self.change()
        self.item['scientificPaint'].update(review=identity, width=2, height=3)
        return self.pin()


def test_python_assistance_keeps_execution_history_and_exact_resource_authority(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            rig.change(); rig.item['scientificPaint'] = None
            query = {'op':'python-assist', 'resource':rig.ref, 'mode':'complete', 'expression':'sav'}
            result = await manager.request(rig.client, identity, query, snapshot=rig.pin())
            assert result['assistance']['matches'] == ['saved']
            owner = manager._owners[identity]
            assert owner.completed is None and owner.sequence == 0 and owner.frame is None
            assert rig.workers[0].requests == [{'op':'python-assist','mode':'complete','expression':'sav'}]
            for bad in ({**query,'resource':{**rig.ref,'version':True}},
                        {**query,'resource':{**rig.ref,'version':rig.ref['version']+1}},
                        {**query,'expression':'viewer.clear()'}, {**query,'source':'evil()'}):
                with pytest.raises(ValueError):
                    await manager.request(rig.client, identity, bad, snapshot=rig.pin())
            with pytest.raises(ValueError):
                await manager.request(object(), identity, query, snapshot=rig.pin())
            assert len(rig.workers[0].requests) == 1
            assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
        finally:
            await manager.close()
    asyncio.run(go())


def test_resource_python_needs_exact_saved_authority_but_no_rendered_frame(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            rig.change(); rig.item['scientificPaint'] = None
            snapshot = rig.pin()
            command = {'op':'python-resources', 'cell':uuid.uuid4().hex,
                       'resource':rig.ref, 'source':'1+1'}
            result = await manager.request(rig.client, identity, command, snapshot=snapshot)
            assert result['console']['result'] == '2'
            assert rig.workers[0].requests == [{'op':'python-resources','source':'1+1'}]
            assert manager._owners[identity].frame is None
            history = rig.store.read(result['history']['id'], rig.saved.session)
            assert history.document['state']['input'] == {'session':rig.saved.session,
                'id':rig.saved.id,'revision':rig.saved.revision,'digest':rig.saved.digest}
            again = await manager.request(rig.client, identity, command, snapshot=rig.pin())
            assert again['console'] == result['console']
            assert len(rig.workers[0].requests) == 1
            assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
        finally:
            await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('change', ['revision','boolean-revision','document','session','extra','window','connection'])
def test_resource_python_cannot_retarget_or_borrow_another_connection(tmp_path, change):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            reference = dict(rig.ref)
            if change == 'revision': reference['version'] += 1
            elif change == 'boolean-revision': reference['version'] = True
            elif change == 'document': reference['id'] = 'p'+'a'*32
            elif change == 'session': reference['day'] = 'another-project'
            elif change == 'extra': reference['actor'] = 'studio'
            elif change == 'window':
                rig.change(); rig.item['view']['legacy']['document']['resource'] = {**rig.ref,'version':2}; rig.pin()
            client = object() if change == 'connection' else rig.client
            with pytest.raises(ObservationError):
                await manager.request(client, identity, {'op':'python-resources','cell':uuid.uuid4().hex,
                    'resource':reference,'source':'must not run'}, snapshot=rig.pin())
            assert not rig.workers[0].requests
        finally:
            await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('move_canvas', [False, True])
def test_focus_settling_during_layout_read_preserves_only_unchanged_frame(tmp_path, move_canvas):
    rig = ReviewsRig(tmp_path)
    entered, release = threading.Event(), threading.Event()

    def delayed_layout(_):
        entered.set()
        assert release.wait(HANG_S)
        return rig.layout

    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            snapshot = rig.paint(identity)
            manager.read_layout = delayed_layout
            pending = asyncio.create_task(manager.request(rig.client, identity,
                {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64, 'source': '1+1'},
                snapshot=snapshot, draw='draw-one'))
            await until_async(entered.is_set)
            rig.change()
            rig.item['view']['legacy']['document']['focused'] = False
            if move_canvas:
                rig.item['scientificPaint']['contentRect'][0] += 1
            rig.pin()
            release.set()
            if move_canvas:
                with pytest.raises(ObservationError): await pending
                assert not any(row['op'] == 'python' for row in rig.workers[0].requests)
            else:
                result = await pending
                assert result['console']['result'] == '2'
                assert len([row for row in rig.workers[0].requests if row['op'] == 'python']) == 1
            assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
        finally:
            release.set()
            await manager.close()
    asyncio.run(go())


def test_python_history_survives_stop_and_duplicate_requests_do_not_execute_twice(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            command = {'op': 'python', 'cell': 'cell-1234567890123456', 'frame': 'a' * 64, 'source': '1+1'}
            for _ in range(2):
                await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
                result = await manager.request(rig.client, identity, command, snapshot=rig.paint(identity), draw='draw-one')
                assert result['console']['result'] == '2'
            assert len([r for r in rig.workers[0].requests if r['op'] == 'python']) == 1
            ref = result['history']
            completed = manager.sessions(rig.client)[0]['completed']
            await manager.request(rig.client, identity, {'op': 'history-save'})
            assert manager.sessions(rig.client)[0]['completed'] == completed
            await manager.stop(rig.client, identity)
            saved = rig.store.read(ref['id'], rig.saved.session)
            assert len(saved.document['state']['cells']) == 1
            assert saved.document['state']['cells'][0]['state'] == 'completed'
            assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
        finally: await manager.close()
    asyncio.run(go())


def test_history_disk_failure_before_dispatch_runs_no_python_and_render_remains_usable(tmp_path, monkeypatch):
    from orchestrator.scientific_history import HistoryUnavailable
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            monkeypatch.setattr(rig.store, 'create', lambda *_, **__: (_ for _ in ()).throw(OSError('controlled disk refusal')))
            with pytest.raises(HistoryUnavailable):
                await manager.request(rig.client, identity, {'op': 'python', 'cell': 'cell-1234567890123456',
                    'frame': 'a' * 64, 'source': '1+1'}, snapshot=rig.paint(identity), draw='draw-one')
            assert not any(r['op'] == 'python' for r in rig.workers[0].requests)
            assert (await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}))['ok']
        finally: await manager.close()
    asyncio.run(go())


def test_failed_terminal_save_can_retry_storage_without_executing_again(tmp_path, monkeypatch):
    from orchestrator.scientific_history import HistoryUnavailable
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            original = rig.store.save
            monkeypatch.setattr(rig.store, 'save', lambda *_, **__: (_ for _ in ()).throw(OSError('controlled disk refusal')))
            with pytest.raises(HistoryUnavailable):
                await manager.request(rig.client, identity, {'op': 'python', 'cell': 'cell-1234567890123456',
                    'frame': 'a' * 64, 'source': '1+1'}, snapshot=rig.paint(identity), draw='draw-one')
            history = next(row for row in rig.store.list(rig.saved.session) if row.document['kind'] == 'scientific-python-history')
            assert history.document['state']['cells'][0]['state'] == 'submitted'
            monkeypatch.setattr(rig.store, 'save', original)
            result = await manager.request(rig.client, identity, {'op': 'history-save'})
            assert rig.store.read(result['history']['id'], rig.saved.session).document['state']['cells'][0]['state'] == 'completed'
            assert len([r for r in rig.workers[0].requests if r['op'] == 'python']) == 1
        finally: await manager.close()
    asyncio.run(go())


def test_worker_crash_keeps_submitted_source_and_uncertain_outcome(tmp_path, monkeypatch):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            monkeypatch.setattr(rig.workers[0], 'request', lambda *_: (_ for _ in ()).throw(RuntimeError('worker died')))
            with pytest.raises(RuntimeError, match='worker died'):
                await manager.request(rig.client, identity, {'op': 'python', 'cell': 'cell-1234567890123456',
                    'frame': 'a' * 64, 'source': 'viewer.layers.clear()'}, snapshot=rig.paint(identity), draw='draw-one')
            history = next(row for row in rig.store.list(rig.saved.session) if row.document['kind'] == 'scientific-python-history')
            cell = history.document['state']['cells'][0]
            assert cell['state'] == 'interrupted' and cell['source'] == 'viewer.layers.clear()'
            assert 'may have run' in cell['explanation']
        finally: await manager.close()
    asyncio.run(go())


def test_history_capacity_refusal_preserves_the_worker_and_its_private_state(tmp_path, monkeypatch):
    from orchestrator import scientific_history
    rig = ReviewsRig(tmp_path)
    monkeypatch.setattr(scientific_history, 'MAX_CELLS', 1)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            worker.private_value = 73
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            await manager.request(rig.client, identity, {'op': 'python', 'cell': 'cell-1234567890123456',
                'frame': 'a' * 64, 'source': '1+1'}, snapshot=rig.paint(identity), draw='draw-one')
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            with pytest.raises(ValueError, match='history is full'):
                await manager.request(rig.client, identity, {'op': 'python', 'cell': 'cell-2234567890123456',
                    'frame': 'a' * 64, 'source': '2+2'}, snapshot=rig.paint(identity), draw='draw-one')
            assert (await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}))['ok']
            assert worker.private_value == 73 and rig.closed == [] and len(rig.workers) == 1
            assert len([r for r in worker.requests if r['op'] == 'python']) == 1
        finally: await manager.close()
    asyncio.run(go())


def test_render_then_exact_paint_allows_one_mutation_and_keeps_saved_work(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            assert await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}) == frame()
            snapshot = rig.paint(identity)
            reply = await manager.request(rig.client, identity,
                {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64, 'source': '1+1'}, snapshot=snapshot, draw='draw-one')
            assert reply['console']['result'] == '2'
            with pytest.raises(ObservationError, match='fresh scientific preview'):
                await manager.request(rig.client, identity,
                    {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64, 'source': 'must not run'}, snapshot=snapshot, draw='draw-one')
            assert len(rig.workers[0].requests) == 2
            assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
            assert await manager.stop(rig.client, identity) is None
            assert rig.closed == rig.workers
        finally:
            assert await manager.close() == []
    asyncio.run(go())


@pytest.mark.parametrize('change', ['connection', 'window', 'project', 'client', 'frame'])
def test_invalid_interaction_never_reaches_owned_worker(tmp_path, change):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            snapshot = rig.paint(identity)
            client = rig.client
            if change == 'connection': rig.accept()
            elif change == 'window':
                rig.change()
                rig.item['view']['legacy']['document']['placement'] = 'another-window'
                snapshot = rig.pin()
            elif change == 'project': manager.active_project = lambda: 'another-project'
            elif change == 'client': client = object()
            with pytest.raises(ObservationError):
                await manager.request(client, identity, {'op': 'python', 'cell': uuid.uuid4().hex,
                    'frame': ('b' if change == 'frame' else 'a') * 64, 'source': 'must not run'},
                    snapshot=snapshot, draw='draw-one')
            assert len(rig.workers[0].requests) == 1
        finally:
            await manager.close()
        assert rig.closed == rig.workers
    asyncio.run(go())


def test_cancelled_start_is_owned_until_its_context_exits(tmp_path):
    rig = ReviewsRig(tmp_path)
    rig.block_start = True
    async def go():
        opening = asyncio.create_task(rig.manager.open(rig.origin))
        await until_async(rig.entered.is_set)
        opening.cancel()
        with pytest.raises(asyncio.CancelledError): await opening
        assert not rig.manager._owners
        assert not rig.workers
        assert await rig.manager.close() == []
    asyncio.run(go())


def test_overlap_refused_and_cancelled_request_reaps_only_its_worker(tmp_path):
    rig, other = ReviewsRig(tmp_path / 'one'), ReviewsRig(tmp_path / 'two')
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            other_id = await other.manager.open(other.origin)
            rig.workers[0].block = True
            running = asyncio.create_task(rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}))
            await until_async(rig.workers[0].started.is_set)
            with pytest.raises(ObservationError, match='finish'):
                await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            running.cancel()
            with pytest.raises(asyncio.CancelledError): await running
            assert rig.closed == rig.workers
            assert not other.closed
            assert await other.manager.request(other.client, other_id, {'op': 'render', 'width': 2, 'height': 3}) == frame()
        finally:
            await rig.manager.close()
            await other.manager.close()
    asyncio.run(go())


def test_disconnect_retires_idle_worker_without_cancelling_unrelated_work(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        identity = await rig.manager.open(rig.origin)
        rig.manager.gone(object())
        assert not rig.manager._owners[identity].stop.is_set()
        rig.manager.gone(rig.client)
        await until_async(lambda: not rig.manager._owners)
        assert rig.closed == rig.workers
        assert await rig.manager.close() == []
    asyncio.run(go())


def test_hidden_window_does_not_cancel_worker_but_cannot_interact(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            rig.change()
            rig.item['visible'] = False
            rig.item['scientificPaint'] = None
            rig.pin()
            with pytest.raises(ObservationError):
                await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            assert not rig.closed
            assert await rig.manager.stop(rig.client, identity) is None
            assert rig.closed == rig.workers
        finally:
            await rig.manager.close()
    asyncio.run(go())


def test_failed_start_releases_slot_and_corrected_retry_opens(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        original = rig.manager._open_worker
        def failed(origin):
            raise RuntimeError('controlled dependency failure')
        rig.manager._open_worker = failed
        with pytest.raises(RuntimeError, match='dependency failure'):
            await rig.manager.open(rig.origin)
        assert not rig.manager._owners
        rig.manager._open_worker = original
        identity = await rig.manager.open(rig.origin)
        assert await rig.manager.stop(rig.client, identity) is None
        assert await rig.manager.close() == ['RuntimeError: controlled dependency failure']
    asyncio.run(go())


@pytest.mark.parametrize('phase', [1, 2])
def test_cancellation_during_admission_or_reply_check_releases_worker(tmp_path, phase):
    rig = ReviewsRig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = 0
    def layout(_):
        nonlocal calls
        calls += 1
        if calls == phase:
            entered.set()
            assert release.wait(HANG_S)
        return rig.layout
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            rig.manager.read_layout = layout
            task = asyncio.create_task(rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}))
            await until_async(entered.is_set)
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not rig.manager._owners
            assert rig.closed == rig.workers
        finally:
            release.set()
            await rig.manager.close()
    asyncio.run(go())


def test_failed_requests_do_not_exhaust_capacity_and_retry_keeps_saved_data(tmp_path):
    rig = ReviewsRig(tmp_path)
    def failed(_):
        raise RuntimeError('controlled worker failure')
    async def go():
        try:
            for _ in range(5):
                identity = await rig.manager.open(rig.origin)
                rig.workers[-1].request = failed
                with pytest.raises(RuntimeError, match='worker failure'):
                    await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
                await until_async(lambda: not rig.manager._owners)
            identity = await rig.manager.open(rig.origin)
            assert await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}) == frame()
            assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
        finally:
            failures = await rig.manager.close()
            assert len(failures) == 5
            assert rig.closed == rig.workers
    asyncio.run(go())


def test_app_disconnect_and_shutdown_use_scientific_owner(tmp_path, monkeypatch):
    from tests.test_workspace_transport import api, ORIGIN, ready
    web, http = api(tmp_path)
    app = web.state.app
    disconnected = []
    monkeypatch.setattr(app.scientific_reviews, 'gone', disconnected.append)
    with http.websocket_connect('/ws', headers={'origin': ORIGIN}) as socket:
        ready(socket)
        client, = app.hub.clients
    assert client in disconnected
    closed = []
    async def close():
        closed.append(True)
        return []
    monkeypatch.setattr(app.scientific_reviews, 'close', close)
    asyncio.run(app.stop())
    assert closed == [True]


def test_actual_saved_napari_worker_uses_owner_and_reaps_on_stop(tmp_path, actual_host):
    from pathlib import Path
    from orchestrator.scientific_workspace import ReviewOrigin
    from tests.test_scientific_project_review import saved_view
    rig = Rig(tmp_path)
    bundle, operation = actual_host['key'].split('#')
    rig.saved, values, _ = saved_view(rig.store, {'bundle': bundle,
        'operation': operation, 'generation': Path(actual_host['folder']).name})
    rig.ref = {'day': rig.saved.session, 'id': rig.saved.id, 'version': rig.saved.revision, 'picture': None}
    rig.item['view']['legacy']['document'].update(project=rig.saved.session, resource=rig.ref)
    rig.layout['views'][0]['items'][0]['resource'] = rig.ref
    rig.change()
    rig.origin = ReviewOrigin.admit(rig.screen, rig.client, rig.pin(), rig.item['id'], rig.saved, rig.layout)
    manager = ScientificReviews(rig.screen, rig.store, lambda _: rig.layout, lambda: rig.saved.session)
    original, workers = manager._open_worker, []
    @contextmanager
    def capture(origin):
        with original(origin) as (saved, worker):
            workers.append(worker)
            yield saved, worker
    manager._open_worker = capture
    async def go():
        try:
            identity = await manager.open(rig.origin)
            reply = await manager.request(rig.client, identity, {'op': 'render', 'width': 256, 'height': 192})
            rig.change()
            rig.item['scientificPaint'].update(review=identity, frame=reply['frame']['frame'], width=256, height=192)
            result = await manager.request(rig.client, identity,
                {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': reply['frame']['frame'], 'source': 'int(viewer.layers[0].data.sum())'},
                snapshot=rig.pin(), draw='draw-one')
            assert result['console']['result'] == str(int(values.sum()))
            history_ref = result['history']
            assert rig.store.read(history_ref['id'], rig.saved.session).document['state']['cells'][0]['transcript']['result'] == str(int(values.sum()))
            reply = await manager.request(rig.client, identity, {'op': 'render', 'width': 256, 'height': 192})
            rig.change()
            rig.item['scientificPaint']['frame'] = reply['frame']['frame']
            running = asyncio.create_task(manager.request(rig.client, identity,
                {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': reply['frame']['frame'],
                 'source': 'from pathlib import Path\nPath("owner-cell-started").touch()\nwhile True: pass'},
                snapshot=rig.pin(), draw='draw-one'))
            await until_async(lambda: (workers[0].work / 'owner-cell-started').exists())
            running.cancel()
            with pytest.raises(asyncio.CancelledError): await running
            assert workers[0].process.poll() is not None
            assert not workers[0].work.exists()
            assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
            history_cells = rig.store.read(history_ref['id'], rig.saved.session).document['state']['cells']
            assert len(history_cells) == 2 and history_cells[-1]['state'] == 'interrupted'
            identity = await manager.open(rig.origin)
            reply = await manager.request(rig.client, identity, {'op': 'render', 'width': 256, 'height': 192})
            assert reply['frame']['dirty'] is False
            assert await manager.stop(rig.client, identity) is None
            assert workers[1].process.poll() is not None and not workers[1].work.exists()
        finally:
            assert await manager.close() == []
    asyncio.run(go())


def test_frame_delivery_retains_bounded_bytes_until_response_finishes(tmp_path):
    from orchestrator.capture import Memory, Refused
    rig = ReviewsRig(tmp_path)
    memory = Memory({'analysis': 48 / (1 << 20)})
    async def go():
        manager = rig.manager
        leases = []
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            for _ in range(2):
                leases.append(await manager.frame(rig.client, identity, 'a' * 64, memory))
            assert memory.reserved['analysis'] == 48
            assert leases[0].pixels == b'0' * 24
            with pytest.raises(Refused):
                await manager.frame(rig.client, identity, 'a' * 64, memory)
            await leases[0].check()
            await manager.stop(rig.client, identity)
            assert memory.reserved['analysis'] == 48
            with pytest.raises(ObservationError):
                await leases[0].check()
            leases[0].release()
            leases[0].release()
            assert leases[0].pixels == b''
            assert memory.reserved['analysis'] == 24
            with pytest.raises(ObservationError):
                await leases[0].check()
        finally:
            for lease in leases: lease.release()
            await manager.close()
        assert memory.reserved['analysis'] == 0
        assert manager._deliveries == 0
    asyncio.run(go())


def test_frame_delivery_is_revoked_by_interaction_and_rejects_other_clients(tmp_path):
    from orchestrator.capture import Memory
    rig = ReviewsRig(tmp_path)
    async def go():
        manager, lease = rig.manager, None
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            with pytest.raises(ObservationError):
                await manager.frame(object(), identity, 'a' * 64, Memory())
            with pytest.raises(ObservationError):
                await manager.frame(rig.client, identity, 'b' * 64, Memory())
            lease = await manager.frame(rig.client, identity, 'a' * 64, Memory())
            snapshot = rig.paint(identity)
            await manager.request(rig.client, identity, {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64,
                'source': '1+1'}, snapshot=snapshot, draw='draw-one')
            with pytest.raises(ObservationError): await lease.check()
        finally:
            if lease: lease.release()
            await manager.close()
    asyncio.run(go())


def test_dynamic_change_uses_declared_nested_worker_frame(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            snapshot = rig.paint(identity)
            request = {'op': 'change', 'width': 2, 'height': 3, 'request': {
                'frame': 'a' * 64, 'operation': 'dimensions', 'value': {'order': [1, 0], 'ndisplay': 2, 'steps': [0, 0]}}}
            result = await manager.request(rig.client, identity, request, snapshot=snapshot, draw='draw-one')
            assert result == frame()
            assert rig.workers[0].requests[-1] == request
        finally: await manager.close()
    asyncio.run(go())


def test_repeated_open_recovers_existing_identity_without_repeating_preparation(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            assert await rig.manager.open(rig.origin) == identity
            assert len(rig.workers) == 1
            await rig.manager.stop(rig.client, identity)
            assert await rig.manager.open(rig.origin) != identity
            assert len(rig.workers) == 2
        finally: await rig.manager.close()
    asyncio.run(go())


def test_released_frame_drops_callback_closure_and_published_buffer(tmp_path):
    import gc
    import weakref
    from orchestrator.capture import Memory
    rig = ReviewsRig(tmp_path)
    class Pixels(bytearray): pass
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            owner = rig.manager._owners[identity]
            pixels = Pixels(b'0' * 24)
            retained = weakref.ref(pixels)
            owner.frame = (owner.frame[0], pixels)
            lease = await rig.manager.frame(rig.client, identity, 'a' * 64, Memory())
            del pixels
            await rig.manager.stop(rig.client, identity)
            gc.collect()
            assert retained() is not None
            lease.release()
            gc.collect()
            assert retained() is None
        finally: await rig.manager.close()
    asyncio.run(go())


def test_cancelled_prestart_open_settles_concurrent_retry(tmp_path):
    rig = ReviewsRig(tmp_path)
    blocked, release = threading.Event(), threading.Event()
    counter = 0
    def layout(_):
        nonlocal counter
        counter += 1
        if counter == 1:
            blocked.set()
            assert release.wait(HANG_S)
        return rig.layout
    rig.manager.read_layout = layout
    async def go():
        first = asyncio.create_task(rig.manager.open(rig.origin))
        second = None
        try:
            assert await asyncio.to_thread(blocked.wait, HANG_S)
            second = asyncio.create_task(rig.manager.open(rig.origin))
            await until_async(lambda: counter == 2)
            first.cancel()
            with pytest.raises(asyncio.CancelledError): await first
            with pytest.raises((ObservationError, RuntimeError)):
                await asyncio.wait_for(second, HANG_S)
            assert not rig.workers
            assert not rig.manager._owners
            release.set()
            assert await rig.manager.open(rig.origin)
        finally:
            release.set()
            if second and not second.done(): second.cancel()
            await rig.manager.close()
    asyncio.run(go())


def test_closing_window_during_startup_keeps_admitted_work_stoppable(tmp_path):
    rig = ReviewsRig(tmp_path)
    rig.block_start = True
    async def go():
        pending = asyncio.create_task(rig.manager.open(rig.origin))
        try:
            assert await asyncio.to_thread(rig.entered.wait, HANG_S)
            rig.change()
            rig.item.update(open=False, visible=False, scientificPaint=None)
            rig.pin()
            rig.block_start = False
            identity = await asyncio.wait_for(pending, HANG_S)
            sessions = rig.manager.sessions(rig.client)
            assert len(sessions) == 1 and sessions[0]['review'] == identity
            assert sessions[0]['state'] == 'idle'
            assert not rig.manager.sessions(object())
            assert len(rig.workers) == 1 and not rig.closed
            await rig.manager.stop(rig.client, identity)
            assert rig.closed == rig.workers
        finally:
            rig.block_start = False
            await rig.manager.close()
    asyncio.run(go())


def test_hidden_python_completion_is_retained_before_presentation_refusal(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            snapshot = rig.paint(identity)
            worker = rig.workers[0]
            worker.started.clear()
            worker.block = True
            pending = asyncio.create_task(rig.manager.request(rig.client, identity,
                {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64, 'source': '1+1'}, snapshot=snapshot, draw='draw-one'))
            assert await asyncio.to_thread(worker.started.wait, HANG_S)
            rig.change()
            rig.item.update(open=False, visible=False, scientificPaint=None)
            rig.pin()
            worker.block = False
            with pytest.raises(ObservationError): await pending
            completed = rig.manager.sessions(rig.client)[0]['completed']
            assert completed['reply']['console']['result'] == '2'
            assert completed['document'] == rig.origin.reference
            assert completed['tab'] == rig.origin.tab
            assert rig.manager.sessions(object()) == []
            assert not rig.closed
        finally: await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('move', [False, True])
def test_reattach_recreated_window_keeps_worker_but_revokes_old_frame(tmp_path, move):
    from orchestrator.scientific_workspace import ReviewOrigin
    from orchestrator.capture import Memory
    rig = ReviewsRig(tmp_path)
    async def go():
        lease = None
        try:
            identity = await rig.manager.open(rig.origin)
            await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            lease = await rig.manager.frame(rig.client, identity, 'a' * 64, Memory())
            original = rig.paint(identity)
            rig.change()
            rig.item['rootId'] = 'reconstructed-root'
            rig.item['lifetimeGeneration'] = 'reconstructed-lifetime'
            rig.item['sourceGeneration'] += 1
            rig.item['scientificPaint'] = None
            if move:
                rig.layout['views'][0]['id'] = 'new-tab'
                rig.item['view']['legacy']['document']['tab'] = 'new-tab'
            snapshot = rig.pin()
            origin = ReviewOrigin.admit(rig.screen, rig.client, snapshot, rig.item['id'], rig.saved, rig.layout)
            assert await rig.manager.attach(rig.client, identity, origin) == identity
            assert len(rig.workers) == 1
            with pytest.raises(ObservationError): await lease.check()
            with pytest.raises(ObservationError):
                await rig.manager.request(rig.client, identity,
                    {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64, 'source': 'must not run'}, snapshot=original, draw='draw-one')
            assert len(rig.workers[0].requests) == 1
            await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            assert len(rig.workers[0].requests) == 2
            assert rig.manager._owners[identity].execution is rig.origin
        finally:
            if lease: lease.release()
            await rig.manager.close()
    asyncio.run(go())


def test_reattach_does_not_steal_inflight_interaction(tmp_path):
    rig = ReviewsRig(tmp_path)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            snapshot = rig.paint(identity)
            worker = rig.workers[0]
            worker.block = True
            worker.started.clear()
            pending = asyncio.create_task(rig.manager.request(rig.client, identity,
                {'op': 'python', 'cell': uuid.uuid4().hex, 'frame': 'a' * 64, 'source': '1+1'}, snapshot=snapshot, draw='draw-one'))
            assert await asyncio.to_thread(worker.started.wait, HANG_S)
            with pytest.raises(ObservationError, match='finish'):
                await rig.manager.attach(rig.client, identity, rig.origin)
            worker.block = False
            assert (await pending)['console']['result'] == '2'
            assert rig.manager.sessions(rig.client)[0]['completed']['sequence'] == 1
        finally: await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('changed', ['revision', 'profile', 'placement'])
def test_failed_attach_keeps_original_binding_and_worker_usable(tmp_path, changed):
    from dataclasses import replace
    rig = ReviewsRig(tmp_path)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            changes = {'revision': rig.origin.revision + 1} if changed == 'revision' else {changed: object() if changed == 'profile' else 'other-placement'}
            with pytest.raises(ObservationError):
                await rig.manager.attach(rig.client, identity, replace(rig.origin, **changes))
            assert rig.manager._owners[identity].origin is rig.origin
            assert await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}) == frame()
        finally: await rig.manager.close()
    asyncio.run(go())


def test_stop_during_attachment_cannot_restore_retired_session(tmp_path):
    rig = ReviewsRig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    async def go():
        identity = await rig.manager.open(rig.origin)
        def layout(_):
            entered.set()
            assert release.wait(HANG_S)
            return rig.layout
        rig.manager.read_layout = layout
        attaching = asyncio.create_task(rig.manager.attach(rig.client, identity, rig.origin))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            await rig.manager.stop(rig.client, identity)
            release.set()
            with pytest.raises(ObservationError): await attaching
            assert not rig.manager.sessions(rig.client)
            assert rig.closed == rig.workers
        finally:
            release.set()
            await rig.manager.close()
    asyncio.run(go())


def save_rig(tmp_path):
    import numpy as np
    from tests.test_scientific_arrays import fill
    from orchestrator.scientific_workspace import ReviewOrigin
    rig = ReviewsRig(tmp_path)
    values = np.arange(6, dtype='<u2').reshape(2, 3)
    draft = rig.store.array_draft(rig.saved.id, rig.saved.session, component='array.labels', shape=values.shape,
        dtype=values.dtype.str, axes=['y','x'], chunks=[2,3], scientific={'semantic':'instance-labels'}, max_written_bytes=values.nbytes)
    reference = fill(draft, values)
    doc = rig.saved.document
    doc['resources'] = [reference]
    doc['state']['scientificView'] = {'runtime': {}, 'inputs': {'labels': reference}}
    rig.saved = rig.store.save(rig.saved.id, rig.saved.session, doc, expected_revision=1)
    rig.ref['version'] = rig.saved.revision
    rig.item['view']['legacy']['document']['resource'] = rig.ref
    rig.layout['views'][0]['items'][0]['resource'] = rig.ref
    rig.change()
    rig.origin = ReviewOrigin.admit(rig.screen, rig.client, rig.pin(), rig.item['id'], rig.saved, rig.layout)
    return rig, values


def labels_winner(rig, values, title):
    """Another save that changes the labels input itself: a true conflict for a labels save."""
    from tests.test_scientific_arrays import fill
    edit = rig.store.array_edit(rig.saved.id, rig.saved.session, rig.saved.revision,
                                rig.saved.document['resources'][0], max_written_bytes=values.nbytes)
    reference = fill(edit, values + 99)
    document = rig.saved.document
    document['resources'] = [reference]
    document['state']['scientificView']['inputs']['labels'] = reference
    document['title'] = title
    return rig.store.save(rig.saved.id, rig.saved.session, document, expected_revision=rig.saved.revision)


def saving_worker(worker, values):
    original = worker.request
    def request(fields):
        reply, body = original(fields)
        if 'frame' in reply:
            reply['frame']['layers'] = [{'id': 'd'*32, 'editableInput':'labels'}]
        return reply, body
    worker.request = request
    calls = []
    def stage(frame, layer, input, edit):
        calls.append((frame,layer,input))
        edit.write([0,0], values)
    worker.stage_layer = stage
    return calls


def test_pre_capture_conflict_survives_refresh_without_reexport_or_retargeting(tmp_path):
    from orchestrator.scientific_saves import SaveUnavailable
    rig, values = save_rig(tmp_path)

    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            calls = saving_worker(rig.workers[0], values+10)
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            winner = labels_winner(rig, values, 'Newer accepted work')
            with pytest.raises(SaveUnavailable, match='newer revision'):
                await manager.request(rig.client, identity,
                    {'op':'save-layer','request':'save-1234567890123456','frame':'a'*64,'layer':'d'*32},
                    snapshot=rig.paint(identity), draw='draw-one')
            status = manager.sessions(rig.client)[0]['arraySave']
            assert status['state'] == 'conflict' and status['latest']['version'] == winner.revision
            refreshed = await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            assert refreshed['arraySave'] == status
            with pytest.raises(ObservationError, match='newer revision'):
                await manager.request(rig.client, identity,
                    {'op':'save-layer','request':'other-1234567890123456','frame':'a'*64,'layer':'d'*32})
            assert calls == [] and not rig.closed
            assert rig.store.read(rig.saved.id, rig.saved.session) == winner
            assert manager.sessions(rig.client)[0]['document']['version'] == rig.saved.revision
        finally: await manager.close()
    asyncio.run(go())


def test_save_retry_keeps_worker_source_and_does_not_run_export_again(tmp_path, monkeypatch):
    from orchestrator.scientific_saves import SaveUnavailable
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            calls = saving_worker(rig.workers[0], values+10)
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            command = {'op':'save-layer','request':'save-1234567890123456','frame':'a'*64,'layer':'d'*32}
            with monkeypatch.context() as patch:
                patch.setattr(rig.store,'save',lambda *a,**k: (_ for _ in ()).throw(OSError('controlled disk refusal')))
                with pytest.raises(SaveUnavailable):
                    await manager.request(rig.client,identity,command,snapshot=rig.paint(identity),draw='draw-one')
            assert not rig.closed and len(calls)==1
            assert manager.sessions(rig.client)[0]['arraySave']['state']=='staged'
            with pytest.raises(ObservationError,match='Finish saving'):
                await manager.request(rig.client,identity,{'op':'python','cell':'cell-1234567890123456','frame':'a'*64,'source':'1+1'})
            result = await manager.request(rig.client,identity,{'op':'save-retry','request':command['request']})
            assert result['arraySave']['state']=='saved' and len(calls)==1
            accepted = rig.store.read(rig.saved.id,rig.saved.session)
            assert accepted.revision==3
            assert manager.sessions(rig.client)[0]['document']['version']==2
            assert await manager.request(rig.client,identity,{'op':'save-retry','request':command['request']}) == result
        finally: await manager.close()
    asyncio.run(go())


def test_admitted_save_survives_window_hiding_and_stale_admission_saves_nothing(tmp_path):
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            calls = saving_worker(rig.workers[0],values+10)
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            command={'op':'save-layer','request':'save-1234567890123456','frame':'a'*64,'layer':'d'*32}
            with pytest.raises(ObservationError):
                await manager.request(rig.client,identity,command,snapshot=rig.paint(identity),draw='wrong')
            assert calls==[] and rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
            original=rig.workers[0].stage_layer
            def hide(*args):
                original(*args)
                rig.item['visible']=False
                rig.change()
            rig.workers[0].stage_layer=hide
            result = await manager.request(rig.client,identity,command,snapshot=rig.paint(identity),draw='draw-one')
            saved=manager.sessions(rig.client)[0]['arraySave']
            assert result['arraySave'] == saved
            assert saved['state']=='saved' and rig.store.read(rig.saved.id,rig.saved.session).revision==3
        finally: await manager.close()
    asyncio.run(go())


def test_partial_export_failure_stops_owned_worker_without_accepting_data(tmp_path):
    from orchestrator.scientific_projects import InvalidProject
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            saving_worker(rig.workers[0], values+10)
            def broken(frame, layer, input, edit):
                edit.write([0,0], values+10)
                raise InvalidProject('controlled export failure')
            rig.workers[0].stage_layer = broken
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            with pytest.raises(RuntimeError, match='export failed'):
                await manager.request(rig.client,identity,{'op':'save-layer','request':'save-1234567890123456',
                    'frame':'a'*64,'layer':'d'*32},snapshot=rig.paint(identity),draw='draw-one')
            await until_async(lambda: rig.closed == rig.workers)
            assert not manager.sessions(rig.client)
            assert rig.store.read(rig.saved.id,rig.saved.session) == rig.saved
        finally: await manager.close()
    asyncio.run(go())


def remember(rig):
    from tests.test_scientific_view_preferences import CASES
    settings = copy.deepcopy(CASES['valid'])
    rig.layout['schema'] = 3
    rig.layout['views'][0]['items'][0]['display'] = {'scientific': settings}
    return settings


def test_remembered_view_restores_only_a_new_worker_from_the_accepted_arrangement(tmp_path):
    rig = ReviewsRig(tmp_path)
    settings = remember(rig)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            worker = rig.workers[0]
            assert worker.requests == []
            await rig.manager.request(rig.client,identity,{'op':'render','width':2,'height':3},snapshot=rig.pin())
            assert [r['op'] for r in worker.requests] == ['render', 'change']
            assert worker.requests[1]['request']['operation'] == 'restore-view'
            assert worker.requests[1]['request']['value'] == settings
            settings['layers'][0]['visible'] = False
            assert worker.requests[1]['request']['value']['layers'][0]['visible'] is True
            assert await rig.manager.open(rig.origin) == identity
            await rig.manager.attach(rig.client, identity, rig.origin)
            assert len(worker.requests) == 2
            with pytest.raises(ObservationError, match='Stop'):
                await rig.manager.open(rig.origin, default_view=True)
            await rig.manager.stop(rig.client, identity)
            identity = await rig.manager.open(rig.origin, default_view=True)
            assert rig.workers[1].requests == []
            assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
        finally: await rig.manager.close()
    asyncio.run(go())


def test_restore_failure_reaps_only_new_worker_and_can_retry_default(tmp_path, monkeypatch):
    rig = ReviewsRig(tmp_path)
    remember(rig)
    original = Worker.request
    def failing(worker, request):
        if request['op'] == 'change':
            raise ValueError('The remembered layers no longer match. Start with its default view.')
        return original(worker, request)
    monkeypatch.setattr(Worker, 'request', failing)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            with pytest.raises(ValueError, match='no longer match'):
                await rig.manager.request(rig.client,identity,{'op':'render','width':2,'height':3},snapshot=rig.pin())
            await until_async(lambda: len(rig.closed) == len(rig.workers))
            assert rig.manager.sessions(rig.client) == []
            assert rig.closed == rig.workers
            assert rig.store.read(rig.saved.id, rig.saved.session).digest == rig.saved.digest
            identity = await rig.manager.open(rig.origin, default_view=True)
            assert rig.workers[-1] not in rig.closed
            assert (await rig.manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3}))['ok']
        finally: await rig.manager.close()
    asyncio.run(go())


def test_manual_gesture_is_never_replayed_and_outcome_survives_render(tmp_path, monkeypatch):
    from tests.test_scientific_label_wire import request
    rig = ReviewsRig(tmp_path)
    async def go():
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            worker=rig.workers[0]; original=worker.request
            def labels(fields):
                result,pixels=original(fields)
                if fields['op']=='labels':
                    result['edit']={'request':fields['request'],'state':'applied','changed':True,'label':'7','message':''}
                return result,pixels
            monkeypatch.setattr(worker,'request',labels)
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            fields={**request(),'width':2,'height':3,'points':[[1,1]]}
            result=await manager.request(rig.client,identity,fields,snapshot=rig.paint(identity),draw='draw-one')
            for replay in (fields,{**fields,'label':'8'}):
                with pytest.raises(ObservationError,match='already submitted'):
                    await manager.request(rig.client,identity,replay,snapshot=rig.paint(identity),draw='draw-one')
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            status=await manager.request(rig.client,identity,{'op':'edit-status','request':fields['request']})
            assert status['edit']==result['edit']
            status['edit']['label']='changed locally'
            assert (await manager.request(rig.client,identity,{'op':'edit-status','request':fields['request']}))['edit']['label']=='7'
            assert len([r for r in worker.requests if r['op']=='labels'])==1
            assert rig.store.read(rig.saved.id,rig.saved.session).digest==rig.saved.digest
        finally: await manager.close()
    asyncio.run(go())


def test_unadmitted_manual_gesture_recovers_without_discarding_private_worker(tmp_path):
    from tests.test_scientific_label_wire import request
    rig=ReviewsRig(tmp_path)
    async def go():
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            worker=rig.workers[0]; worker.private_value=73
            fields={**request(),'width':2,'height':3,'points':[[1,1]]}
            with pytest.raises(ObservationError):
                await manager.request(rig.client,identity,fields,snapshot=rig.paint(identity),draw='draw-one')
            result=await manager.request(rig.client,identity,{'op':'edit-status','request':fields['request']})
            assert result['edit']['state']=='not-admitted' and not result['edit']['changed']
            assert worker.private_value==73 and rig.closed==[]
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            with pytest.raises(ObservationError,match='already submitted'):
                await manager.request(rig.client,identity,fields,snapshot=rig.paint(identity),draw='draw-one')
            assert not any(row['op']=='labels' for row in worker.requests)
            owner=manager._owners[identity]; owner.busy=True
            pending=await manager.request(rig.client,identity,{'op':'edit-status','request':'delayed-1234567890123456'})
            assert pending['edit']['state']=='submitted'
            owner.busy=False
            owner.edits={f'previous-{n:016d}':{'state':'applied'} for n in range(128)}
            with pytest.raises(ObservationError,match='allowance'):
                await manager.request(rig.client,identity,fields,snapshot=rig.paint(identity),draw='draw-one')
            status=await manager.request(rig.client,identity,{'op':'edit-status','request':fields['request']})
            assert status['edit']['state']=='not-admitted' and worker.private_value==73 and rig.closed==[]
            assert (await manager.request(rig.client,identity,{'op':'render','width':2,'height':3}))['ok']
        finally: await manager.close()
    asyncio.run(go())


def test_conflict_copy_requires_fresh_frame_and_keeps_original_owner(tmp_path):
    import numpy as np
    from orchestrator.scientific_saves import SaveConflict
    from tests.test_scientific_edits import values as native_values
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            exports = saving_worker(rig.workers[0], values+10)
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            winner = labels_winner(rig, values, 'Winner')
            with pytest.raises(SaveConflict):
                await manager.request(rig.client,identity,{'op':'save-layer','request':'save-1234567890123456','frame':'a'*64,'layer':'d'*32},snapshot=rig.paint(identity),draw='draw-one')
            command = {'op':'save-copy','request':'save-1234567890123456','frame':'a'*64}
            with pytest.raises(ObservationError): await manager.request(rig.client,identity,command)
            assert not exports
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            result = await manager.request(rig.client,identity,command,snapshot=rig.paint(identity),draw='draw-one')
            assert result['arraySave']['state'] == 'copied' and len(exports) == 1
            assert await manager.request(rig.client,identity,{**command,'frame':None}) == result
            copied = rig.store.read(result['arraySave']['resource']['id'],rig.saved.session)
            assert np.array_equal(native_values(rig.store,copied,copied.document['resources'][0]),values+10)
            assert rig.store.read(rig.saved.id,rig.saved.session) == winner
            assert manager.sessions(rig.client)[0]['document']['version'] == rig.saved.revision
            await manager.stop(rig.client,identity)
            assert rig.store.read(copied.id,copied.session) == copied and len(exports) == 1
        finally: await manager.close()
    asyncio.run(go())


def test_copy_cleanup_failure_still_releases_worker_and_settles_stop(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from orchestrator.scientific_saves import LayerSave
    rig, values = save_rig(tmp_path)
    async def go():
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            saving_worker(rig.workers[0],values)
            result=await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            save=LayerSave(rig.store,rig.saved,'save-1234567890123456',result['frame'],'d'*32)
            save.capture(rig.workers[0])
            def fail(): raise OSError('controlled staging cleanup failure')
            save.copy_attempt=SimpleNamespace(close=fail)
            owner=manager._owners[identity]; owner.array_save=save
            failure = await manager.stop(rig.client,identity)
            assert isinstance(failure,OSError) and 'staging cleanup' in str(failure)
            assert rig.closed == rig.workers and owner.ended.done()
            assert save.edit._closed and save.owner._closed
        finally: await manager.close()
    asyncio.run(go())


def test_manual_history_retry_recovers_receipt_without_replaying_then_binds_saved_values(tmp_path, monkeypatch):
    from tests.test_scientific_label_wire import request
    from orchestrator.scientific_history import HistoryUnavailable
    from orchestrator.scientific_projects import ProjectStore
    rig, values = save_rig(tmp_path)

    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            saving_worker(worker, values + 1)
            original_request = worker.request
            def labels(fields):
                result, pixels = original_request(fields)
                if fields['op'] == 'labels':
                    result['edit'] = {'request': fields['request'], 'state': 'applied',
                        'changed': True, 'label': '7', 'message': ''}
                return result, pixels
            monkeypatch.setattr(worker, 'request', labels)
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            fields = {**request(), 'width':2, 'height':3, 'points':[[1,1]]}
            original_save = rig.store.save
            with monkeypatch.context() as patch:
                def lost_ack(*args, **kwargs):
                    original_save(*args, **kwargs)
                    raise OSError('controlled lost history acknowledgement')
                patch.setattr(rig.store, 'save', lost_ack)
                with pytest.raises(HistoryUnavailable):
                    await manager.request(rig.client, identity, fields, snapshot=rig.paint(identity), draw='draw-one')
            pending = await manager.request(rig.client, identity, {'op':'edit-status','request':fields['request']})
            assert pending['edit']['state'] == 'submitted'
            recovered = await manager.request(rig.client, identity, {'op':'history-save'})
            assert '_recovered_edits' not in recovered
            status = await manager.request(rig.client, identity, {'op':'edit-status','request':fields['request']})
            assert status['edit']['state'] == 'applied'
            assert len([r for r in worker.requests if r['op']=='labels']) == 1
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            await manager.request(rig.client, identity,
                {'op':'save-layer','request':'save-1234567890123456','frame':'a'*64,'layer':'d'*32},
                snapshot=rig.paint(identity),draw='draw-one')
            reopened = ProjectStore(rig.store.root)
            saved = reopened.read(rig.saved.id, rig.saved.session)
            provenance = saved.document['state']['hookeEdit']
            history = provenance['producer']['history']
            actual = reopened.read(history['id'], history['session'], revision=history['revision'])
            assert actual.digest == history['digest']
            assert history['revision'] == 2
            action = actual.document['state']['actions'][0]
            assert action['request'] == fields and action['context']['frame'] == 'a'*64
            assert action['outcome']['edit'] == status['edit']
            assert provenance['producer']['kernel'] == identity
            assert provenance['output'] == saved.document['resources'][0]
            assert reopened.read(rig.saved.id, rig.saved.session, revision=rig.saved.revision) == rig.saved
        finally:
            await manager.close()
    asyncio.run(go())


def test_panel_callbacks_record_exact_controls_but_navigation_uses_no_edit_allowance(tmp_path, monkeypatch):
    from orchestrator import scientific_history
    rig = ReviewsRig(tmp_path)
    monkeypatch.setattr(scientific_history, 'MAX_CELLS', 1)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            original = worker.request
            declared = {'format':'scientific-controls-v1','panel':'b'*32,'revision':3,'controls':[
                {'id':'c'*32,'kind':'choice','label':'Solver','choices':[{'id':'d'*32,'label':'Exact CPU'}],
                 'value':'d'*32,'help':'Test choice','enabled':True,'visible':True}]}
            def controls(fields):
                result, pixels = original(fields)
                result['frame']['controls'] = copy.deepcopy(declared)
                return result, pixels
            monkeypatch.setattr(worker, 'request', controls)
            for operation in ('dimensions', 'camera', 'controls', 'dimensions'):
                await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
                command = {'op':'change','width':2,'height':3,'request':{
                    'frame':'a'*64,'operation':operation,'value':{'synthetic':True}}}
                result = await manager.request(rig.client, identity, command,
                    snapshot=rig.paint(identity),draw='draw-one')
                if operation == 'controls':
                    reference = result['history']
                    history = rig.store.read(reference['id'], rig.saved.session, revision=reference['version'])
                    action = history.document['state']['actions'][0]
                    assert action['request'] == command
                    assert action['context']['controls'] == declared
                    assert 'opaque Python values' in action['context']['choiceMeaning']
                else:
                    assert 'history' not in result
            assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
        finally:
            await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('operation', ['python', 'labels'])
@pytest.mark.parametrize('written', [False, True])
def test_failed_admission_retry_retains_earlier_private_work_without_dispatch(tmp_path, monkeypatch, operation, written):
    from tests.test_scientific_label_wire import request
    from orchestrator.scientific_history import HistoryUnavailable
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            saving_worker(worker, values + 1)
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            await manager.request(rig.client, identity, {'op':'python','cell':'earlier-1234567890123456',
                'source':'earlier private changes','frame':'a'*64}, snapshot=rig.paint(identity),draw='draw-one')
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            fields = ({'op':'python','cell':'refused-1234567890123456','source':'must not run','frame':'a'*64}
                if operation == 'python' else {**request(),'width':2,'height':3,'points':[[1,1]]})
            original = rig.store.save
            def refuse(*args, **kwargs):
                if written: original(*args, **kwargs)
                raise OSError('controlled admission acknowledgement failure')
            with monkeypatch.context() as patch:
                patch.setattr(rig.store, 'save', refuse)
                with pytest.raises(HistoryUnavailable):
                    await manager.request(rig.client, identity, fields, snapshot=rig.paint(identity),draw='draw-one')
            recovered = await manager.request(rig.client, identity, {'op':'history-save'})
            saved_history = rig.store.read(recovered['history']['id'], rig.saved.session)
            collection = 'cells' if operation == 'python' else 'actions'
            assert saved_history.document['state'][collection][-1]['state'] == 'not-dispatched'
            assert not any(row == fields for row in worker.requests)
            if operation == 'labels':
                status = await manager.request(rig.client, identity, {'op':'edit-status','request':fields['request']})
                assert status['edit']['state'] == 'refused' and status['edit']['changed'] is False
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            result = await manager.request(rig.client, identity,
                {'op':'save-layer','request':'save-1234567890123456','frame':'a'*64,'layer':'d'*32},
                snapshot=rig.paint(identity),draw='draw-one')
            assert result['arraySave']['state'] == 'saved'
            saved = rig.store.read(rig.saved.id, rig.saved.session)
            assert saved.document['state']['hookeEdit']['producer']['history']['digest'] == saved_history.digest
            assert rig.store.read(rig.saved.id, rig.saved.session, revision=rig.saved.revision) == rig.saved
        finally: await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('nested', [None, [], 'bad', 3])
def test_malformed_control_request_is_scoped_refusal_with_no_history(tmp_path, nested):
    rig = ReviewsRig(tmp_path)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            await rig.manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            with pytest.raises(ObservationError, match='frame changed'):
                await rig.manager.request(rig.client, identity, {'op':'change','width':2,'height':3,'request':nested},
                    snapshot=rig.paint(identity),draw='draw-one')
            assert not any(row['op'] == 'change' for row in rig.workers[0].requests)
            assert len(rig.store.list(rig.saved.session)) == 1
            assert (await rig.manager.request(rig.client, identity, {'op':'render','width':2,'height':3}))['ok']
        finally: await rig.manager.close()
    asyncio.run(go())


def test_resource_save_after_python_needs_no_render_receipt(tmp_path):
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            writes = []
            def stage(layer, input, edit):
                writes.append((layer,input))
                edit.write([0,0], values+20)
            rig.workers[0].stage_resource = stage
            rig.change(); rig.item['scientificPaint'] = None
            await manager.request(rig.client,identity,{'op':'python-resources','resource':rig.ref,
                'cell':uuid.uuid4().hex,'source':'1+1'},snapshot=rig.pin())
            result = await manager.request(rig.client,identity,{'op':'save-resource','resource':rig.ref,
                'request':uuid.uuid4().hex,'layer':'d'*32,'input':'labels'},snapshot=rig.pin())
            assert result['arraySave']['state'] == 'saved'
            assert writes == [('d'*32,'labels')]
            assert manager._owners[identity].frame is None
            accepted = rig.store.read(rig.saved.id,rig.saved.session)
            assert accepted.document['state']['hookeEdit']['frame'] is None
            assert accepted.document['state']['hookeEdit']['producer']['history']
            assert rig.store.read(rig.saved.id,rig.saved.session,revision=rig.saved.revision)==rig.saved
        finally:
            await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('mismatch',['revision','boolean-revision','document','input','layer','extra'])
def test_resource_save_rejects_changed_authority_before_worker_export(tmp_path,mismatch):
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            writes=[]
            rig.workers[0].stage_resource=lambda *args:writes.append(args)
            command={'op':'save-resource','resource':dict(rig.ref),'request':uuid.uuid4().hex,'layer':'d'*32,'input':'labels'}
            if mismatch=='revision': command['resource']['version']+=1
            if mismatch=='boolean-revision': command['resource']['version']=True
            if mismatch=='document': command['resource']['id']='p'+'b'*32
            if mismatch=='input': command['input']='not-declared'
            if mismatch=='layer': command['layer']='not-a-layer'
            if mismatch=='extra': command['frame']='a'*64
            with pytest.raises((ObservationError,ValueError)):
                await manager.request(rig.client,identity,command,snapshot=rig.pin())
            assert writes==[]
            assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
        finally:
            await manager.close()
    asyncio.run(go())


def test_resource_pre_capture_conflict_can_copy_without_restoring_preview(tmp_path):
    rig, values = save_rig(tmp_path)
    async def go():
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            calls=[]
            def stage(layer,input,edit):
                calls.append((layer,input));edit.write([0,0],values+30)
            rig.workers[0].stage_resource=stage
            winner=labels_winner(rig, values, 'winner')
            request=uuid.uuid4().hex
            from orchestrator.scientific_saves import SaveConflict
            with pytest.raises(SaveConflict):
                await manager.request(rig.client,identity,{'op':'save-resource','resource':rig.ref,'request':request,
                    'layer':'d'*32,'input':'labels'},snapshot=rig.pin())
            status=manager.sessions(rig.client)[0]['arraySave']
            assert status['copyNeedsPreview'] is False and status['copyCaptured'] is False
            result=await manager.request(rig.client,identity,{'op':'save-copy','request':request,'frame':None},snapshot=rig.pin())
            assert result['arraySave']['state']=='copied' and calls==[('d'*32,'labels')]
            assert rig.store.read(rig.saved.id,rig.saved.session)==winner
        finally:
            await manager.close()
    asyncio.run(go())


def test_refused_uncaptured_copy_releases_only_attempt_and_records_correction(tmp_path):
    from orchestrator.scientific_host import CaptureRefused
    from orchestrator.scientific_saves import SaveConflict, SaveNotStarted
    rig, values = save_rig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            def refused(*args):
                raise CaptureRefused('Restore declared calibration')
            worker.stage_resource = refused
            winner = labels_winner(rig, values, 'concurrent winner')
            command = {'op':'save-resource','resource':rig.ref,'request':uuid.uuid4().hex,
                       'layer':'d'*32,'input':'labels'}
            with pytest.raises(SaveConflict):
                await manager.request(rig.client,identity,command,snapshot=rig.pin())
            before = manager._owners[identity].array_save.producer
            with pytest.raises(SaveNotStarted):
                await manager.request(rig.client,identity,{'op':'save-copy','request':command['request'],'frame':None},snapshot=rig.pin())
            result = await manager.request(rig.client,identity,{'op':'save-status'},snapshot=rig.pin())
            assert result['arraySave'] is None
            assert not manager._owners[identity].stop.is_set()
            await manager.request(rig.client,identity,{'op':'python-resources','resource':rig.ref,
                'cell':uuid.uuid4().hex,'source':'restore_calibration()'},snapshot=rig.pin())
            worker.stage_resource = lambda layer, input, edit: edit.write([0,0],values+31)
            command['request'] = uuid.uuid4().hex
            with pytest.raises(SaveConflict):
                await manager.request(rig.client,identity,command,snapshot=rig.pin())
            assert manager._owners[identity].array_save.producer != before
            result = await manager.request(rig.client,identity,{'op':'save-copy','request':command['request'],'frame':None},snapshot=rig.pin())
            assert result['arraySave']['state']=='copied'
            assert rig.store.read(rig.saved.id,rig.saved.session)==winner
            assert rig.store.read(rig.saved.id,rig.saved.session,revision=rig.saved.revision)==rig.saved
        finally:
            await manager.close()
    asyncio.run(go())


def test_layer_inventory_is_resource_authorized_and_never_presents_or_completes_work(tmp_path):
    rig=ReviewsRig(tmp_path)
    async def go():
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            def inventory(fields):
                assert fields=={'op':'layer-inventory'}
                return {'ok':True,'inventory':{'format':'scientific-layers-v1','layers':[]}}, b''
            rig.workers[0].request=inventory
            for revision in [True,rig.ref['version']+1]:
                with pytest.raises(ObservationError):
                    await manager.request(rig.client,identity,{'op':'layer-inventory','resource':{**rig.ref,'version':revision}},snapshot=rig.pin())
            result=await manager.request(rig.client,identity,{'op':'layer-inventory','resource':rig.ref},snapshot=rig.pin())
            assert result['inventory']['layers']==[] and result['arraySave'] is None
            assert manager._owners[identity].frame is None
            assert manager._owners[identity].completed is None
        finally:
            await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('reason',['Layer geometry changed','The image color choices exceed their supported metadata allowance'])
def test_incompatible_deferred_view_keeps_worker_and_saved_settings(tmp_path,monkeypatch,reason):
    from orchestrator.scientific_host import RestoreRefused
    rig=ReviewsRig(tmp_path);settings=copy.deepcopy(remember(rig));original=Worker.request
    def request(worker,fields):
        if fields['op']=='change': raise RestoreRefused(reason)
        if fields['op']=='layer-inventory': return {'ok':True,'inventory':{'format':'scientific-layers-v1','layers':[]}},b''
        return original(worker,fields)
    monkeypatch.setattr(Worker,'request',request)
    async def go():
        try:
            identity=await rig.manager.open(rig.origin)
            assert rig.workers[0].requests==[]
            await rig.manager.request(rig.client,identity,{'op':'layer-inventory','resource':rig.ref},snapshot=rig.pin())
            assert all(r['op']!='render' for r in rig.workers[0].requests)
            shown=await rig.manager.request(rig.client,identity,{'op':'render','width':2,'height':3},snapshot=rig.pin())
            assert 'not applied' in shown['viewNotice']
            assert rig.workers[0] not in rig.closed
            assert rig.layout['views'][0]['items'][0]['display']['scientific']==settings
            shown=await rig.manager.request(rig.client,identity,{'op':'render','width':2,'height':3},snapshot=rig.pin())
            assert 'viewNotice' not in shown
        finally: await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('cell_error', [False, True])
def test_deferred_view_never_overwrites_python_changes(tmp_path, monkeypatch, cell_error):
    rig = ReviewsRig(tmp_path)
    settings = copy.deepcopy(remember(rig))
    original = Worker.request
    def request(worker, fields):
        reply, payload = original(worker, fields)
        if fields['op'] == 'python-resources' and cell_error:
            reply['console']['error'] = 'ValueError: error after a private mutation'
        return reply, payload
    monkeypatch.setattr(Worker, 'request', request)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            await rig.manager.request(rig.client, identity, {
                'op': 'python-resources', 'resource': rig.ref, 'cell': uuid.uuid4().hex,
                'source': 'viewer.layers[0].visible = False'}, snapshot=rig.pin())
            shown = await rig.manager.request(rig.client, identity,
                {'op': 'render', 'width': 2, 'height': 3}, snapshot=rig.pin())
            assert 'because Python has run' in shown['viewNotice']
            assert [r['op'] for r in rig.workers[0].requests] == ['python-resources', 'render']
            assert rig.layout['views'][0]['items'][0]['display']['scientific'] == settings
            assert rig.workers[0] not in rig.closed
            shown = await rig.manager.request(rig.client, identity,
                {'op': 'render', 'width': 2, 'height': 3}, snapshot=rig.pin())
            assert 'viewNotice' not in shown
        finally:
            await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('manifest_failure', [False, True])
def test_multi_resource_save_uses_one_origin_and_one_publication_without_preview(tmp_path, monkeypatch, manifest_failure):
    from tests.test_scientific_arrays import fill
    from orchestrator.scientific_workspace import ReviewOrigin
    rig, data = save_rig(tmp_path)
    draft = rig.store.array_draft(rig.saved.id, rig.saved.session, component='array.second', shape=data.shape,
        dtype=data.dtype.str, axes=['y','x'], chunks=[2,3], scientific={'semantic':'instance-labels'}, max_written_bytes=data.nbytes)
    second = fill(draft, data+10)
    document = rig.saved.document
    document['resources'].append(second); document['state']['scientificView']['inputs']['second'] = second
    rig.saved = rig.store.save(rig.saved.id, rig.saved.session, document, expected_revision=rig.saved.revision)
    rig.ref['version'] = rig.saved.revision
    rig.item['view']['legacy']['document']['resource'] = rig.ref
    rig.layout['views'][0]['items'][0]['resource'] = rig.ref
    rig.change()
    rig.origin = ReviewOrigin.admit(rig.screen, rig.client, rig.pin(), rig.item['id'], rig.saved, rig.layout)
    async def go():
        try:
            identity = await rig.manager.open(rig.origin)
            captures=[]
            def stage(rows):
                captures.append([name for _,name,_ in rows])
                for _,name,edit in rows: edit.write([0,0],data+(100 if name=='labels' else 200))
            rig.workers[0].stage_resources=stage
            rig.change();rig.item['scientificPaint']=None
            command={'op':'save-resources','resource':dict(rig.ref),'request':uuid.uuid4().hex,
                     'layers':[{'layer':'a'*32,'input':'labels'},{'layer':'b'*32,'input':'second'}]}
            wrong={**command,'resource':{**rig.ref,'version':rig.saved.revision+1}}
            with pytest.raises(ObservationError,match='originating'):
                await rig.manager.request(rig.client,identity,wrong,snapshot=rig.pin())
            assert not captures
            if manifest_failure:
                from orchestrator.scientific_edits import Edit
                from orchestrator.scientific_saves import SaveUnavailable
                with monkeypatch.context() as patch:
                    patch.setattr(Edit,'prepared',lambda self: (_ for _ in ()).throw(OSError('controlled manifest error')))
                    with pytest.raises(SaveUnavailable,match='could not be confirmed'):
                        await rig.manager.request(rig.client,identity,command,snapshot=rig.pin())
                assert not rig.closed and rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
                status=await rig.manager.request(rig.client,identity,{'op':'save-status'})
                assert status['arraySave']['state']=='staged'
                result=await rig.manager.request(rig.client,identity,{'op':'save-retry','request':command['request']},snapshot=rig.pin())
            else:
                result=await rig.manager.request(rig.client,identity,command,snapshot=rig.pin())
            assert result['arraySave']['state']=='saved' and captures==[['labels','second']]
            assert rig.manager._owners[identity].frame is None
            accepted=rig.store.read(rig.saved.id,rig.saved.session)
            assert accepted.revision==rig.saved.revision+1
            assert len(accepted.document['state']['hookeEdit']['changes'])==2
            status=await rig.manager.request(rig.client,identity,{'op':'save-status'})
            assert status['arraySave']==result['arraySave']
            assert rig.store.read(rig.saved.id,rig.saved.session,revision=rig.saved.revision)==rig.saved
        finally: await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('multiple,tool',[(False,False),(True,False),(False,True)])
def test_new_output_authority_retry_release_and_other_private_work(tmp_path,monkeypatch,multiple,tool):
    from orchestrator.scientific_projects import InvalidProject
    from tests.test_scientific_outputs import Worker
    from tests.test_scientific_output_sets import SetWorker
    from tests.test_scientific_tool_save import ToolWorker
    OutputWorker=ToolWorker if tool else SetWorker if multiple else Worker
    from orchestrator.scientific_workspace import ReviewOrigin
    from orchestrator.scientific_saves import SaveUnavailable
    rig,_=save_rig(tmp_path)
    document=rig.saved.document
    runtime={'bundle':'mine/runtime','generation':'g1','operation':'viewer'}
    document['runtimes']=[runtime];document['state']['scientificView']['runtime']=runtime
    rig.saved=rig.store.save(rig.saved.id,rig.saved.session,document,expected_revision=rig.saved.revision)
    rig.ref['version']=rig.saved.revision
    rig.item['view']['legacy']['document']['resource']=rig.ref
    rig.layout['views'][0]['items'][0]['resource']=rig.ref
    rig.change();rig.origin=ReviewOrigin.admit(rig.screen,rig.client,rig.pin(),rig.item['id'],rig.saved,rig.layout)
    async def go():
        try:
            identity=await rig.manager.open(rig.origin)
            output=OutputWorker();worker=rig.workers[0]
            if tool:
                previous = worker.request
                worker.request = lambda fields: output.request(fields) if fields['op'] == 'layer-inventory' else previous(fields)
            worker.describe_output=output.describe_output
            if multiple or tool:worker.stage_outputs=output.stage_outputs
            else:worker.stage_output=output.stage_output
            command={'op':'save-output','resource':dict(rig.ref),'request':uuid.uuid4().hex,'layer':'a'*32,
                     'meaning':{'semantic':'instance-labels','axes':['y','x']}}
            if multiple:
                command={'op':'save-outputs','resource':dict(rig.ref),'request':command['request'],
                         'outputs':[{'layer':'a'*32,'meaning':command['meaning']},
                                    {'layer':'b'*32,'meaning':{'semantic':'probabilities','axes':['y','x']}}]}
            if tool:
                command={'op':'save-tool','resource':dict(rig.ref),'request':command['request'],
                         'outputs':[{'layer':'a'*32,'meaning':command['meaning']}]}
            for bad in [{**rig.ref,'version':True},{**rig.ref,'version':rig.saved.revision+1}]:
                with pytest.raises(ObservationError):
                    await rig.manager.request(rig.client,identity,{**command,'resource':bad},snapshot=rig.pin())
            assert output.calls==0
            from orchestrator.scientific_arrays import ArrayDraft
            with monkeypatch.context() as patch:
                patch.setattr(ArrayDraft,'finish',lambda self:(_ for _ in ()).throw(OSError('controlled manifest failure')))
                with pytest.raises(SaveUnavailable):
                    await rig.manager.request(rig.client,identity,command,snapshot=rig.pin())
            assert not rig.closed and output.calls==1
            reply=await rig.manager.request(rig.client,identity,{'op':'save-retry','request':command['request']},snapshot=rig.pin())
            assert reply['arraySave']['kind']=='new-output' and reply['arraySave']['state']=='saved'
            ref=reply['arraySave']['resource']
            release={'op':'save-release','request':command['request']}
            import orchestrator.scientific_outputs as outputs
            with monkeypatch.context() as patch:
                patch.setattr(outputs.shutil,'rmtree',lambda path:(_ for _ in ()).throw(OSError('controlled cleanup failure')))
                released=await rig.manager.request(rig.client,identity,release,snapshot=rig.pin())
                assert released['arraySave'] is None and 'cleanup' in released['cleanupNotice']
            assert not rig.closed and outputs.retry_cleanup()==[]
            assert (await rig.manager.request(rig.client,identity,release,snapshot=rig.pin()))['arraySave'] is None
            await rig.manager.request(rig.client,identity,{'op':'python-resources','resource':dict(rig.ref),
                'cell':uuid.uuid4().hex,'source':'print(private_work)'},snapshot=rig.pin())
            with pytest.raises(InvalidProject,match='already saved'):
                await rig.manager.request(rig.client,identity,command,snapshot=rig.pin())
            assert output.calls==1 and not rig.closed
            assert rig.store.read(ref['id'],ref['day']).revision==1
            assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
        finally:await rig.manager.close()
    asyncio.run(go())




def test_interrupt_is_bound_to_running_cell_and_original_connection(tmp_path):
    rig=ReviewsRig(tmp_path)
    async def go():
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            rig.change();rig.item['scientificPaint']=None
            worker=rig.workers[0];worker.block=True
            calls=[]
            def interrupt():
                calls.append('interrupt');worker.block=False;return True
            worker.interrupt_python=interrupt
            cell=uuid.uuid4().hex
            running=asyncio.create_task(manager.request(rig.client,identity,{'op':'python-resources','cell':cell,'resource':rig.ref,'source':'1+1'},snapshot=rig.pin()))
            await until_async(worker.started.is_set)
            wrong=await manager.request(rig.client,identity,{'op':'python-interrupt','cell':uuid.uuid4().hex})
            assert wrong['interrupt']['requested'] is False and not calls
            with pytest.raises(ObservationError):
                await manager.request(object(),identity,{'op':'python-interrupt','cell':cell})
            outcome=await manager.request(rig.client,identity,{'op':'python-interrupt','cell':cell})
            assert outcome['interrupt']=={'cell':cell,'requested':True}
            assert (await running)['console']['result']=='2'
            assert not rig.closed
            assert (await manager.request(rig.client,identity,{'op':'python-interrupt','cell':cell}))['interrupt']['requested'] is False
            assert calls==['interrupt']
            assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
        finally:
            await manager.close()
    asyncio.run(go())

@pytest.mark.parametrize('changed', ['unchanged', 'hidden', 'replacement', 'disconnected'])
def test_slow_completion_refreshes_original_observation_without_replaying_code(tmp_path, changed):
    rig = ReviewsRig(tmp_path)
    asked = []
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            original = worker.request
            def slow(request):
                result = original(request)
                rig.now += 1.25  # Longer than the freshness bound, shorter than the normal heartbeat.
                return result
            worker.request = slow
            def observe(message):
                asked.append(message)
                rig.change()
                if changed == 'hidden': rig.raw['viewport']['visible'] = False
                if changed == 'replacement': rig.item['lifetimeGeneration'] = 'replacement'
                if changed == 'disconnected':
                    rig.screen._accepted.pop(rig.client)
                    return
                rig.pin()
            rig.screen.broadcast = observe
            command = {'op':'python-resources','resource':rig.ref,'cell':uuid.uuid4().hex,'source':'1+1'}
            if changed == 'unchanged':
                reply = await manager.request(rig.client,identity,command,snapshot=rig.pin())
                assert reply['console']['result'] == '2'
            else:
                with pytest.raises(ObservationError):
                    await manager.request(rig.client,identity,command,snapshot=rig.pin())
            assert asked == [{'type':'screen-ask'}]
            assert len(worker.requests) == 1
            assert manager._owners[identity].completed['reply']['console']['result'] == '2'
            assert rig.store.read(rig.saved.id,rig.saved.session) == rig.saved
        finally: await manager.close()
    asyncio.run(go())

@pytest.mark.parametrize('response', ['none', 'replay', 'other-client'])
def test_completion_refresh_cannot_borrow_freshness_and_keeps_outcome(tmp_path, monkeypatch, response):
    from orchestrator import screen
    monkeypatch.setattr(screen, 'CONFIRM_S', .03)
    rig = ReviewsRig(tmp_path)
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            original = worker.request
            def slow(request):
                result = original(request); rig.now += 1.25; return result
            worker.request = slow
            asked = []
            def observe(message):
                asked.append(message)
                if response == 'replay': rig.pin()  # Same sequence cannot refresh its age.
                if response == 'other-client':
                    other = object()
                    from dataclasses import replace
                    ref = replace(CLIENT, client_id='other-client')
                    rig.screen.accept_client(other, ref, app_run_id='run-a', source_resolver=lambda *_: None,
                                             capture_authority=lambda *_: None)
                    report = copy.deepcopy(rig.raw); report['client'] = ref.wire()
                    rig.screen.pin(other, request_id='other', inline=report)
            rig.screen.broadcast = observe
            command = {'op':'python-resources','resource':rig.ref,'cell':uuid.uuid4().hex,'source':'1+1'}
            with pytest.raises(ObservationError, match='did not answer'):
                await manager.request(rig.client,identity,command,snapshot=rig.pin())
            assert asked == [{'type':'screen-ask'}]
            assert len(worker.requests) == 1
            assert manager.sessions(rig.client)[0]['completed']['reply']['console']['result'] == '2'
            assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
        finally: await manager.close()
    from tests.test_scientific_presentation import CLIENT
    asyncio.run(go())


def test_recovery_capability_rebinds_only_detached_exact_origin(tmp_path):
    from dataclasses import replace
    from orchestrator.scientific_workspace import ReviewOrigin
    rig = ReviewsRig(tmp_path)
    key = 'recovery-' + 'a' * 40
    async def go():
        try:
            identity = await rig.manager.open(rig.origin, recovery_key=key)
            owner = rig.manager._owners[identity]
            original = rig.client
            rig.manager.gone(original)
            rig.screen.gone(original)
            assert not owner.stop.is_set()
            assert rig.manager.sessions(original) == []
            rig.client = object(); rig.accept(); rig.change()
            renewed = ReviewOrigin.admit(rig.screen, rig.client, rig.pin(), rig.item['id'], rig.saved, rig.layout)
            for wrong in (None, 'recovery-' + 'b' * 40):
                with pytest.raises(ObservationError):
                    await rig.manager.open(renewed, recovery_key=wrong)
            for field, value in [('revision', 2), ('digest', 'different'), ('placement', 'other'), ('project', 'other'), ('tab', 'other')]:
                with pytest.raises(ObservationError):
                    await rig.manager.open(replace(renewed, **{field:value}), recovery_key=key)
            assert await rig.manager.open(renewed, recovery_key=key) == identity
            assert len(rig.workers) == 1 and not rig.closed
            assert owner.frame is None and owner.native_frame is None
            assert await rig.manager.open(renewed, recovery_key=key) == identity
            assert key not in str(rig.manager.sessions(rig.client))
            with pytest.raises(ObservationError): await rig.manager.request(original, identity, {'op':'render','width':2,'height':3})
            await rig.manager.stop(rig.client, identity)
            assert rig.closed == rig.workers
        finally:
            await rig.manager.close()
    asyncio.run(go())


def test_recovery_cannot_take_live_connection_or_abandon_inflight_work(tmp_path):
    from dataclasses import replace
    rig = ReviewsRig(tmp_path)
    key = 'recovery-' + 'c' * 40
    async def go():
        try:
            identity = await rig.manager.open(rig.origin, recovery_key=key)
            with pytest.raises(ObservationError):
                await rig.manager.open(replace(rig.origin, client=object()), recovery_key=key)
            worker = rig.workers[0]; worker.block = True
            task = asyncio.create_task(rig.manager.request(rig.client, identity, {'op':'python-resources','cell':'cell-'+uuid.uuid4().hex,'resource':rig.ref,'source':'2'}))
            await until_async(worker.started.is_set)
            rig.manager.gone(rig.client); task.cancel()
            worker.block = False
            with pytest.raises(asyncio.CancelledError): await task
            owner = rig.manager._owners[identity]
            await until_async(lambda: not owner.busy)
            assert not owner.stop.is_set() and not rig.closed
            assert owner.completed['reply']['console']['result'] == '2'
            assert len(worker.requests) == 1
        finally:
            await rig.manager.close()
        assert rig.closed == rig.workers
    asyncio.run(go())


def test_recovery_retains_startup_after_disconnect_and_shutdown_reaps_it(tmp_path):
    rig=ReviewsRig(tmp_path);rig.block_start=True
    async def go():
        task=asyncio.create_task(rig.manager.open(rig.origin,recovery_key='startup-'+ 'd'*40))
        await until_async(rig.entered.is_set)
        rig.manager.gone(rig.client);rig.screen.gone(rig.client)
        rig.block_start=False
        with pytest.raises(ObservationError):await task
        assert len(rig.manager._owners)==1 and not rig.closed
        assert await rig.manager.close()==[]
        assert rig.closed==rig.workers
    asyncio.run(go())


def test_cancelled_recovery_reply_is_recorded_once(tmp_path,monkeypatch):
    rig=ReviewsRig(tmp_path)
    async def go():
        try:
            identity=await rig.manager.open(rig.origin,recovery_key='reply-'+ 'e'*40)
            reached=asyncio.Event()
            async def pause(_):
                reached.set();await asyncio.Event().wait()
            monkeypatch.setattr(rig.manager,'_refresh_completion',pause)
            task=asyncio.create_task(rig.manager.request(rig.client,identity,{'op':'python-resources','cell':'cell-'+uuid.uuid4().hex,'resource':rig.ref,'source':'2'}))
            await reached.wait();rig.manager.gone(rig.client);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            owner=rig.manager._owners[identity]
            assert owner.sequence==1 and not owner.busy and not owner.stop.is_set()
        finally:await rig.manager.close()
    asyncio.run(go())


def test_recoverable_start_cancellation_before_disconnect_does_not_reap(tmp_path):
    rig=ReviewsRig(tmp_path);rig.block_start=True
    async def go():
        try:
            task=asyncio.create_task(rig.manager.open(rig.origin,recovery_key='ordering-'+ 'f'*40))
            await until_async(rig.entered.is_set);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert len(rig.manager._owners)==1
            owner=next(iter(rig.manager._owners.values()))
            assert not owner.stop.is_set()
            rig.manager.gone(rig.client);rig.screen.gone(rig.client);rig.block_start=False
            await until_async(owner.started.done)
            assert not rig.closed
        finally:
            rig.block_start=False;await rig.manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('cancelled',[False,True])
def test_disconnected_callback_failure_stays_visible_after_successful_recovery(tmp_path,monkeypatch,cancelled):
    from orchestrator.scientific_host import ControlFailed
    from orchestrator.scientific_workspace import ReviewOrigin
    rig=ReviewsRig(tmp_path)
    async def go():
        try:
            key='failure-'+ 'g'*40;identity=await rig.manager.open(rig.origin,recovery_key=key)
            worker=rig.workers[0];original=worker.request;entered=threading.Event();release=threading.Event()
            def fail(request):
                if request['op']=='observe':
                    entered.set();assert release.wait(HANG_S)
                    raise ControlFailed('Background callback: fixture rejected a layer')
                return original(request)
            monkeypatch.setattr(worker,'request',fail)
            task=asyncio.create_task(rig.manager.request(rig.client,identity,{'op':'observe','resource':rig.ref}))
            await until_async(entered.is_set);rig.manager.gone(rig.client);rig.screen.gone(rig.client)
            if cancelled:task.cancel()
            release.set()
            with pytest.raises(asyncio.CancelledError if cancelled else ControlFailed):await task
            owner=rig.manager._owners[identity];await until_async(lambda:not owner.busy)
            rig.client=object();rig.accept();rig.change()
            origin=ReviewOrigin.admit(rig.screen,rig.client,rig.pin(),rig.item['id'],rig.saved,rig.layout)
            assert await rig.manager.open(origin,recovery_key=key)==identity
            await rig.manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            notice=rig.manager.recovery_notice(rig.client,identity)
            assert 'observe' in notice and 'fixture rejected a layer' in notice
            assert rig.manager.sessions(rig.client)[0]['sessionNotice']==notice
        finally:await rig.manager.close()
    asyncio.run(go())


class SlowStarts(ReviewsRig):
    """Workers that report startup steps (scientific_host.reporting) and wait past the open bound when told to."""

    def __init__(self, tmp_path, slow):
        super().__init__(tmp_path)
        self.slow = list(slow)  # per attempt: the step it reaches before it waits past the bound, or None to open
        self.attempts = 0
        self.release = threading.Event()

    @contextmanager
    def open_worker(self, origin):
        from orchestrator import scientific_host
        self.attempts += 1
        starting = scientific_host._STARTUP.get()
        stuck = self.slow[self.attempts - 1] if self.attempts <= len(self.slow) else None
        for step in ('code', 'viewer', 'tool'):
            starting(step)
            if step == stuck:
                while True:  # past the bound, as a cold runtime's import is
                    owned_process._bound.get().check()
                    threading.Event().wait(.01)
        while not self.release.wait(.01):  # opened up to its panel: held until the test has read it
            owned_process._bound.get().check()
        with super().open_worker(origin) as opened:
            yield opened


def test_first_open_past_its_bound_is_tried_once_more_with_its_progress_said(tmp_path, monkeypatch):
    from orchestrator import scientific_reviews
    monkeypatch.setattr(scientific_reviews, 'OPEN_SECONDS', .5)
    rig = SlowStarts(tmp_path, ['viewer'])
    async def go():
        opening = asyncio.create_task(rig.manager.open(rig.origin))
        await until_async(lambda: rig.attempts == 2 and any(
            row.get('opening', {}).get('step') == 'tool' for row in rig.manager.sessions(rig.client)))
        row, = rig.manager.sessions(rig.client)
        assert row['state'] == 'opening'
        said = row['opening']
        assert (said['says'], said['attempt'], said['attempts'], said['limitSeconds']) == (
            'Opening the tool’s panel', 2, 2, .5)
        assert said['previous']['step'] == 'viewer' and said['previous']['says'] == 'Opening the viewer'
        assert said['previous']['seconds'] >= .5 and said['seconds'] < .5 + HANG_S
        rig.release.set()
        identity = await opening
        assert 'opening' not in rig.manager.sessions(rig.client)[0]
        assert len(rig.workers) == 1  # the timed-out attempt made no worker; the second one is the only one
        assert await rig.manager.stop(rig.client, identity) is None
        assert await rig.manager.close() == []
    asyncio.run(go())


def test_two_opens_past_their_bound_are_said_plainly_with_each_step(tmp_path, monkeypatch):
    from orchestrator import scientific_reviews
    monkeypatch.setattr(scientific_reviews, 'OPEN_SECONDS', .3)
    rig = SlowStarts(tmp_path, ['code', 'tool'])
    async def go():
        with pytest.raises(ObservationError) as raised:
            await rig.manager.open(rig.origin)
        words = str(raised.value)
        assert 'did not open in 0.3 s, twice' in words
        assert '“Loading the tool’s code”' in words and '“Opening the tool’s panel”' in words
        assert 'saved work is unchanged' in words
        assert rig.attempts == 2 and not rig.workers and not rig.manager._owners
        rig.release.set()
        identity = await rig.manager.open(rig.origin)  # a third open, asked for again, is a new start
        assert await rig.manager.stop(rig.client, identity) is None
        assert len(await rig.manager.close()) == 1
    asyncio.run(go())


def test_an_open_that_fails_for_another_reason_is_never_retried(tmp_path):
    rig = ReviewsRig(tmp_path)
    calls = []
    def failed(origin):
        calls.append(origin)
        raise RuntimeError('controlled dependency failure')
    rig.manager._open_worker = failed
    async def go():
        with pytest.raises(RuntimeError, match='dependency failure'):
            await rig.manager.open(rig.origin)
    asyncio.run(go())
    assert len(calls) == 1
