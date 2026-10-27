"""Refusal before mutation keeps private work; callback failures are not success."""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from env_worker import scientific_host as local
from tests.test_scientific_host_worker import actual_host  # noqa: F401


def host():
    value = object.__new__(local.ReviewSession)
    value._closed, value._thread, value._frame = False, threading.get_ident(), 'current'
    value._layers = []
    value.dirty = False
    value.viewer = SimpleNamespace(camera=SimpleNamespace(center=(1, 2, 3), angles=(0, 0, 0), zoom=2, orientation=('towards','down','right'), perspective=0))
    value.panel = SimpleNamespace(invalidate=lambda: None)
    return value


@pytest.mark.parametrize('change', [
    {'frame': 'old', 'operation': 'camera', 'value': {'center': [1, 2, 3], 'angles': [0, 0, 0], 'zoom': 3}},
    {'frame': 'current', 'operation': 'camera', 'value': {'center': [1, 2, 3], 'angles': [0, 0, 0], 'zoom': -1}},
    {'frame': 'current', 'operation': 'layer-presentation', 'value': {'layer': 'gone', 'visible': True, 'opacity': .5}},
    {'frame': 'current', 'operation': 'unknown', 'value': {}},
])
def test_change_refusal_does_not_consume_frame_or_private_work(change):
    value = host()
    with pytest.raises(local.ChangeRefused):
        value.change(change)
    assert value._frame == 'current' and value.dirty is False
    assert value.viewer.camera.zoom == 2
    value.change({'frame': 'current', 'operation': 'camera',
                  'value': {'center': [1, 2, 3], 'angles': [0, 0, 0], 'zoom': 3}})
    assert value.viewer.camera.zoom == 3 and value._frame is None and value.dirty


@pytest.mark.parametrize('claimed_refusal', [False, True])
def test_callback_failure_is_not_reported_as_a_not_started_change(claimed_refusal):
    value = host()
    def callback(request):
        value.viewer.camera.zoom = 7
        raise (local.ChangeRefused if claimed_refusal else ValueError)('callback failed after mutation')
    value.panel.prepare = lambda request: lambda: callback(request)
    with pytest.raises((ValueError, RuntimeError)) as error:
        value.change({'frame': 'current', 'operation': 'controls', 'value': {}})
    assert not isinstance(error.value, local.ChangeRefused)
    assert value._frame is None and value.dirty and value.viewer.camera.zoom == 7


@pytest.mark.parametrize('reason', [local.Stale, local.Unsupported])
def test_control_preparation_refusal_preserves_private_frame(reason):
    value = host()
    def prepare(request):
        raise reason('Control settings changed before application')
    value.panel.prepare = prepare
    with pytest.raises(local.ChangeRefused):
        value.change({'frame': 'current', 'operation': 'controls', 'value': {}})
    assert value._frame == 'current' and not value.dirty
    assert value.viewer.camera.zoom == 2


def test_actual_refused_change_preserves_namespace_native_values_and_retry(actual_host):
    import numpy as np
    from orchestrator import scientific_host
    from tests.test_native_regions import source
    prepared = actual_host
    values = np.arange(6 * 24 * 28, dtype='<u2').reshape(6, 24, 28)
    before = values.tobytes()
    with scientific_host.review(prepared['key'], Path(prepared['folder']).name,
                                {'volume': source(values, [])}) as worker:
        worker.request({'op': 'python-resources', 'source': 'kept_after_refusal = 83'})
        shown, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        frame = shown['frame']['frame']
        camera = shown['frame']['camera']
        value = {key: camera[key] for key in ('center', 'angles', 'zoom')}
        for token, change in [('f' * 64, value), (frame, {**value, 'zoom': -1})]:
            with pytest.raises(scientific_host.ChangeRefused):
                worker.request({'op': 'change', 'width': 256, 'height': 192,
                                'request': {'frame': token, 'operation': 'camera', 'value': change}})
            assert worker.process.poll() is None
        changed, _ = worker.request({'op': 'change', 'width': 256, 'height': 192,
            'request': {'frame': frame, 'operation': 'camera', 'value': {**value, 'zoom': value['zoom'] * 1.1}}})
        assert changed['frame']['camera']['zoom'] == pytest.approx(value['zoom'] * 1.1)
        shown = changed['frame']
        button = next(c for c in shown['controls']['controls'] if c['kind'] == 'button')
        command = {'panel': shown['controls']['panel'], 'revision': shown['controls']['revision'],
                   'control': button['id'], 'op': 'invoke'}
        for token, settings in [('f' * 64, command),
                                (shown['frame'], {**command, 'revision': command['revision'] - 1}),
                                (shown['frame'], {**command, 'control': 'f' * 32})]:
            with pytest.raises(scientific_host.ChangeRefused):
                worker.request({'op': 'change', 'width': 256, 'height': 192,
                    'request': {'frame': token, 'operation': 'controls', 'value': settings}})
            assert worker.process.poll() is None
        refreshed, _ = worker.request({'op': 'render', 'width': 256, 'height': 192})
        current = refreshed['frame']
        corrected, _ = worker.request({'op': 'change', 'width': 256, 'height': 192,
            'request': {'frame': current['frame'], 'operation': 'controls',
                        'value': {**command, 'revision': current['controls']['revision']}}})
        assert corrected['frame']['layers'][0]['opacity'] == .4
        result, _ = worker.request({'op': 'python-resources', 'source': 'kept_after_refusal + 1'})
        assert result['console']['result'] == '84'
    assert values.tobytes() == before
    assert worker.process.poll() is not None and not worker.work.exists()


@pytest.mark.parametrize('operation', ['camera', 'controls'])
def test_owned_review_keeps_worker_and_prior_completion_after_refusal(tmp_path, monkeypatch, operation):
    import asyncio
    from orchestrator.scientific_host import ChangeRefused
    from tests.test_scientific_reviews import ReviewsRig, Worker
    rig = ReviewsRig(tmp_path)
    original = Worker.request
    def request(worker, fields):
        if fields['op'] == 'change':
            raise ChangeRefused('View changed before admission')
        return original(worker, fields)
    monkeypatch.setattr(Worker, 'request', request)
    async def run():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            with pytest.raises(ChangeRefused):
                await manager.request(rig.client, identity, {'op': 'change', 'width': 2, 'height': 3,
                    'request': {'frame': 'a' * 64, 'operation': operation, 'value': {}}},
                    snapshot=rig.paint(identity), draw='draw-one')
            assert rig.workers[0] not in rig.closed
            assert manager.sessions(rig.client)[0]['completed'] is None
            assert manager._owners[identity].frame is None  # Must obtain a fresh preview.
            if operation == 'controls':
                owner = manager._owners[identity]
                action = owner.history.document['state']['actions'][-1]
                assert action['state'] == 'completed'
                assert action['outcome'] == {'state': 'not-applied'}
                saved_history = rig.store.read(owner.history.record.id, rig.saved.session)
                assert saved_history.document['state']['actions'][-1] == action
            shown = await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            assert shown['frame']['frame'] == 'a' * 64
            monkeypatch.setattr(Worker, 'request', original)
            await manager.request(rig.client, identity, {'op': 'change', 'width': 2, 'height': 3,
                'request': {'frame': 'a' * 64, 'operation': operation, 'value': {}}},
                snapshot=rig.paint(identity), draw='draw-one')
            assert rig.workers[0] not in rig.closed
            if operation == 'controls':
                assert owner.history.document['state']['actions'][-1]['outcome'] == {'state': 'completed'}
            assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
        finally:
            await manager.close()
    asyncio.run(run())


def test_refusal_response_preserves_private_work_and_names_refresh():
    import json
    from orchestrator.scientific_host import ChangeRefused
    from orchestrator.scientific_review_api import error_response
    response = error_response(ChangeRefused('The current layer changed'))
    value = json.loads(response.body)
    assert response.status_code == 409 and value['code'] == 'SCIENTIFIC_CHANGE_NOT_APPLIED'
    assert 'Check current view' in value['next'] and 'private' in value['next']


def test_changed_input_at_worker_start_has_actionable_recovery_and_reaps(tmp_path):
    import asyncio
    from contextlib import contextmanager
    from dataclasses import replace
    from tests.test_scientific_reviews import ReviewsRig
    rig = ReviewsRig(tmp_path)
    @contextmanager
    def changed(origin):
        with rig.open_worker(origin) as (saved, worker):
            yield replace(saved, digest='f' * 64), worker
    rig.manager._open_worker = changed
    async def run():
        try:
            with pytest.raises(ValueError, match='saved scientific input changed; reopen its exact revision'):
                await rig.manager.open(rig.origin)
            assert rig.workers == rig.closed
            assert rig.store.read(rig.saved.id, rig.saved.session) == rig.saved
        finally:
            await rig.manager.close()
    asyncio.run(run())
