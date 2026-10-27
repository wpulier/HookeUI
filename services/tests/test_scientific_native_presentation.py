"""A rendered scientific image never grants authority to an unseen plugin panel."""
import asyncio

import pytest

from orchestrator.workspace_observation import ObservationError
from tests.test_scientific_workspace import Rig
from tests.test_scientific_reviews import ReviewsRig, Worker
from tests.test_scientific_native_panel_wire import state


def test_native_paint_is_independent_and_bound_to_same_saved_window(tmp_path):
    rig = Rig(tmp_path)
    with pytest.raises(ObservationError):
        rig.origin.check(rig.screen, rig.snapshot, rig.layout, review='review-one',
            frame=rig.frame, draw='draw-one', presentation='native-panel')
    rig.change()
    rig.item['scientificPanelPaint'] = {**rig.item['scientificPaint'], 'frame': 'b'*64, 'draw': 'panel-draw'}
    pin = rig.pin()
    assert rig.origin.check(rig.screen, pin, rig.layout, review='review-one',
        frame={**rig.frame, 'frame': 'b'*64}, draw='panel-draw', presentation='native-panel') == (20, 40, 160, 120)
    with pytest.raises(ObservationError):
        rig.origin.check(rig.screen, pin, rig.layout, review='review-one',
            frame=rig.frame, draw='draw-one', presentation='native-panel')
    rig.change()
    rig.item['scientificPanelPaint'] = None
    with pytest.raises(ObservationError):
        rig.origin.check(rig.screen, rig.pin(), rig.layout, review='review-one',
            frame={**rig.frame, 'frame': 'b'*64}, draw='panel-draw', presentation='native-panel')


def test_panel_dispatch_requires_own_paint_records_history_and_consumes_both_frames(tmp_path, monkeypatch):
    rig = ReviewsRig(tmp_path)
    original = Worker.request
    calls = []
    def request(worker, fields):
        if fields['op'] == 'native-panel-render':
            return {'ok': True, 'nativePanel': state(), 'imageFrame': None}, bytes(32*24*4)
        if fields['op'] == 'native-panel-change':
            calls.append(fields)
            return {'ok': True, 'nativeChanged': True}, b''
        return original(worker, fields)
    monkeypatch.setattr(Worker, 'request', request)
    async def run():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op': 'render', 'width': 2, 'height': 3})
            await manager.request(rig.client, identity, {'op': 'native-panel-render', 'width': 32, 'height': 24})
            manager._owners[identity].view_preferences = {'pending': 'saved presentation'}
            command = {'op': 'native-panel-change', 'request': dict(panel='a'*32, frame='b'*64, op='click', value=[1,2])}
            with pytest.raises(ObservationError):
                await manager.request(rig.client, identity, command, snapshot=rig.paint(identity), draw='draw-one')
            assert not calls
            rig.change()
            rig.item['scientificPanelPaint'] = dict(review=identity, frame='b'*64, draw='panel-draw',
                width=32, height=24, contentRect=[20,40,160,120])
            result = await manager.request(rig.client, identity, command, snapshot=rig.pin(), draw='panel-draw')
            assert result['nativeChanged'] is True and len(calls) == 1
            owner = manager._owners[identity]
            assert owner.frame is None and owner.native_frame is None
            assert owner.view_preferences is None
            action = owner.history.document['state']['actions'][-1]
            assert action['request']['op'] == 'native-panel-change' and action['state'] == 'completed'
            assert action['context']['nativePanel']['frame'] == 'b'*64
            with pytest.raises(ObservationError):
                await manager.request(rig.client, identity, command, snapshot=rig.pin(), draw='panel-draw')
            assert len(calls) == 1
        finally:
            await manager.close()
    asyncio.run(run())


def test_background_observation_owns_revision_revokes_stale_image_and_never_records_an_action(tmp_path, monkeypatch):
    rig = ReviewsRig(tmp_path)
    original = Worker.request
    observed = []
    token = 'a'*64
    def request(worker, fields):
        if fields['op'] == 'observe':
            observed.append(fields)
            return {'ok':True, 'imageFrame':token}, b''
        return original(worker, fields)
    monkeypatch.setattr(Worker, 'request', request)
    async def run():
        nonlocal token
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            owner=manager._owners[identity]
            command={'op':'observe','resource':rig.ref}
            for invalid in ({**command,'resource':{**rig.ref,'version':True}},
                            {**command,'resource':{**rig.ref,'version':rig.ref['version']+1}},
                            {**command,'source':'viewer.layers.clear()'}):
                with pytest.raises(ValueError):
                    await manager.request(rig.client,identity,invalid)
            assert not observed
            await manager.request(rig.client,identity,command)
            assert owner.frame is not None
            token=None
            await manager.request(rig.client,identity,command)
            assert owner.frame is None
            assert owner.sequence==0 and owner.completed is None
            assert not owner.history.document['state']['actions']
            assert observed==[{'op':'observe'},{'op':'observe'}]
            assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
        finally:await manager.close()
    asyncio.run(run())
