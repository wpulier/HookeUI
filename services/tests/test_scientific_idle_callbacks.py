"""Idle delivery belongs to the serialized worker owner, not a visible browser."""
from contextlib import contextmanager
import threading

import pytest

from orchestrator import owned_process
from orchestrator.scientific_host import ControlFailed
from orchestrator.scientific_reviews import _Owner
from tests.test_scientific_workspace import Rig
from tests.waiting import HANG_S, until


class Worker:
    def __init__(self):
        self.calls = []
        self.callback = threading.Event()
        self.fail = None
        self.scope = None

    def request(self, command):
        self.calls.append((threading.get_ident(), dict(command)))
        if command['op'] == 'layer-inventory':
            return {'ok': True, 'nativePanelAvailable': True}, b''
        if command['op'] == 'observe':
            self.scope = owned_process._bound.get()
            self.scope.check()
            self.callback.set()
            if self.fail:
                raise self.fail
            return {'ok': True, 'imageFrame': None}, b''
        return {'ok': True}, b''


@contextmanager
def owner(tmp_path, worker):
    rig = Rig(tmp_path)
    closed = threading.Event()
    @contextmanager
    def opening(origin):
        try:
            yield rig.saved, worker
        finally:
            closed.set()
    value = _Owner(rig.origin, opening, rig.store)
    value.thread.start()
    value.started.result(HANG_S)
    try:
        yield value, rig
    finally:
        value.stop.set()
        value.ended.result(HANG_S)
        value.thread.join(HANG_S)
        assert closed.is_set() and not value.thread.is_alive()


def test_idle_callbacks_progress_without_browser_requests_and_keep_same_owner(tmp_path):
    worker = Worker()
    with owner(tmp_path, worker) as (value, rig):
        value.submit({'op': 'layer-inventory', 'resource': rig.ref}).result(HANG_S)
        until(worker.callback.is_set, what='idle worker callback without browser polling')
        assert {ident for ident, _ in worker.calls} == {value.thread.ident}
        assert worker.scope is not None
        assert [call['op'] for _, call in worker.calls] == ['layer-inventory', 'observe']
        assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved


def test_idle_failure_is_reported_once_without_replaying_or_stopping_private_work(tmp_path):
    worker = Worker()
    worker.fail = ControlFailed('Background callback failed after a private change')
    with owner(tmp_path, worker) as (value, rig):
        value.submit({'op': 'layer-inventory', 'resource': rig.ref}).result(HANG_S)
        until(lambda: value.background_error is not None, what='retained idle callback error')
        count = len(worker.calls)
        value.submit({'op': 'python-assist', 'resource': rig.ref}).result(HANG_S)
        with pytest.raises(ControlFailed, match='Background callback failed'):
            value.submit({'op': 'observe', 'resource': rig.ref}).result(HANG_S)
        assert len(worker.calls) == count + 1, 'Reporting the error must not replay the callback'
        assert not value.stop.is_set()
        worker.fail = None
        assert value.submit({'op': 'observe', 'resource': rig.ref}).result(HANG_S)[0]['ok']
        assert value.background_error is None


def test_fatal_idle_failure_ends_only_its_owned_worker(tmp_path):
    worker = Worker()
    worker.fail = RuntimeError('Worker protocol failed')
    with owner(tmp_path, worker) as (value, rig):
        value.submit({'op': 'layer-inventory', 'resource': rig.ref}).result(HANG_S)
        until(value.stop.is_set, what='fatal idle callback cleanup')
        assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved


def test_stop_cancels_inflight_idle_delivery_and_reaps_owner(tmp_path):
    class WaitingWorker(Worker):
        def request(self, command):
            if command['op'] == 'observe':
                self.callback.set()
                while True:
                    owned_process._bound.get().check()
                    threading.Event().wait(.01)
            return super().request(command)
    worker = WaitingWorker()
    with owner(tmp_path, worker) as (value, rig):
        value.submit({'op': 'layer-inventory', 'resource': rig.ref}).result(HANG_S)
        until(worker.callback.is_set, what='inflight idle delivery')
        value.stop.set()
        assert value.ended.result(HANG_S) is None


def test_idle_delivery_requires_declared_native_panel_and_is_cadence_limited(tmp_path):
    worker = Worker()
    with owner(tmp_path, worker) as (value, rig):
        value._observe_idle(worker)
        assert worker.calls == []
        value.observation_available = True
        value.observation_due = float('inf')
        value._observe_idle(worker)
        assert worker.calls == []


def test_actual_qt_idle_callback_reads_brokered_data_without_browser_polling(tmp_path, monkeypatch):
    """Actual confined Qt worker, synthetic data; not package qualification."""
    import json
    import os
    from pathlib import Path
    import numpy as np
    from gpu_worker import manifests as catalog
    from orchestrator import scientific_host
    from tests.test_native_regions import source

    location = os.environ.get('NAPARI_NATIVE_HOST_PREPARATION')
    if not location:
        pytest.skip('requires independently prepared confined native runtime')
    prepared = json.loads(Path(location).read_text())
    for key, setting in prepared['environment'].items():
        monkeypatch.setenv(key, setting)
    monkeypatch.delenv('MICROMIND_SANDBOX_READ', raising=False)
    catalog.manifests.cache_clear()
    rig = Rig(tmp_path)
    values = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    reads, workers = [], []
    @contextmanager
    def opening(origin):
        resource = source(values, reads)
        resource.descriptor['semantic'] = 'intensity'
        with scientific_host.review(prepared['key'], Path(prepared['folder']).name,
                                    {'volume': resource}) as worker:
            workers.append(worker)
            yield rig.saved, worker
    value = _Owner(rig.origin, opening, rig.store)
    value.thread.start()
    try:
        value.started.result(HANG_S)
        value.submit({'op': 'layer-inventory', 'resource': rig.ref}).result(HANG_S)
        code = '''
from qtpy.QtCore import QTimer
import sys
idle_deliveries = []
def complete_idle_result():
    data = inputs['volume'].read([[0,1],[0,2],[0,3]])
    viewer.add_image(data[0], name='Idle brokered result')
    idle_deliveries.append(int(data.sum()))
    print('idle-result-ready', file=sys.__stderr__, flush=True)
QTimer.singleShot(200, complete_idle_result)
'''
        reply, _ = value.submit({'op': 'python-resources', 'resource': rig.ref,
                                 'source': code}).result(HANG_S)
        assert reply['console']['error'] == ''
        count = len(reads)
        # Only inspect the log: issuing a worker request here would hide the bug.
        until(lambda: 'idle-result-ready' in (workers[0].work / 'stderr.log').read_text(),
              what='actual Qt result delivered while browser sends nothing')
        assert len(reads) > count, 'Idle callback must service the scoped regional broker'
        reply, _ = value.submit({'op': 'python-resources', 'resource': rig.ref,
            'source': "(idle_deliveries, viewer.layers[-1].name, len(viewer.layers))"}).result(HANG_S)
        assert reply['console']['result'] == "([90], 'Idle brokered result', 2)"
        assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
    finally:
        value.stop.set()
        value.ended.result(HANG_S)
        value.thread.join(HANG_S)
        catalog.manifests.cache_clear()
    assert workers[0].process.poll() is not None and not workers[0].work.exists()
