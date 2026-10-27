"""Synthetic transport plus real navigation ownership/chunk loops; no native/UI qualification."""
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator.scientific_navigation import Navigation
from orchestrator.scientific_host import ReviewWorker
from env_worker.lazy_arrays import LazyNative, ChunkCache
from env_worker.native_regions import ViewSuperseded
from tests.test_scientific_host_worker import frame


def anchor():
    result = frame()['frame']
    result['dimensions'].update(order=[0, 1, 2], displayed=[1, 2], ndisplay=2, steps=[0, 0, 0], counts=[10, 4, 4], labels=['z', 'y', 'x'], point=[0, 0, 0], ranges=[[0, 9, 1], [0, 3, 1], [0, 3, 1]])
    return result


def target(index):
    return {'order': [0, 1, 2], 'ndisplay': 2, 'steps': [index, 0, 0]}


def test_navigation_latest_slot_keeps_original_anchor_and_ordinary_limits():
    original = (anchor(), b'pixels')
    nav = Navigation('n' * 32, original, 'draw', target(1))
    nav.replace(3, target(3)); nav.replace(2, target(2))
    assert nav.target() == (3, target(3)) and nav.frame is original
    assert nav.superseded(1) and not nav.finish(1)
    for value in (target(10), {**target(2), 'order': [1, 0, 2]}, {**target(2), 'ndisplay': 3}):
        with pytest.raises(ValueError): nav.replace(4, value)
    assert nav.finish(3)
    with pytest.raises(ValueError): nav.replace(4, target(4))


def test_superseded_region_stops_old_chunk_loop_and_keeps_same_worker_state(monkeypatch):
    """The first region admits two newer targets; B is coalesced and only C is completed."""
    worker = object.__new__(ReviewWorker)
    nav = Navigation('n' * 32, (anchor(), b'pixels'), 'draw', target(1))
    reads = []; exchanged = []
    cache = ChunkCache(1 << 20)
    def exchange(request):
        index = request['request']['value']['steps'][0]; exchanged.append(index)
        def read(bounds, *, view):
            if worker._navigation_superseded():
                raise ViewSuperseded()
            reads.append(index)
            if index == 1:
                nav.replace(2, target(2)); nav.replace(3, target(3))
            return np.full((1, 4), index, dtype='|u1')
        source = SimpleNamespace(shape=(4, 4), dtype='|u1', read=read)
        lazy = LazyNative(source, cache, chunks=(1, 4), ident=str(index))
        try:
            pixels = lazy[:].tobytes()
        except ViewSuperseded:
            return {'ok': False, 'code': 'SCIENTIFIC_VIEW_SUPERSEDED'}, b''
        reply = {'ok': True, 'frame': anchor()}; reply['frame']['dimensions']['steps'][0] = index
        return reply, pixels
    worker._exchange = exchange
    monkeypatch.setattr('orchestrator.scientific_host.checked_reply', lambda *args: None)
    request = {'op':'change', 'width':2, 'height':3,
               'request': {'frame':anchor()['frame'], 'operation':'dimensions', 'value':target(1)}}
    reply, pixels = worker.navigate(request, nav)
    assert exchanged == [1, 3] and reads == [1, 3, 3, 3, 3]
    assert pixels == bytes([3]) * 16 and reply['navigation']['sequence'] == 3
    assert not worker._navigation_superseded()


def test_latest_navigation_rechecks_owner_and_does_not_admit_edits_while_busy(tmp_path):
    import asyncio
    import threading
    from tests.test_scientific_reviews import ReviewsRig
    from tests.waiting import HANG_S, until_async
    from orchestrator.workspace_observation import ObservationError
    rig = ReviewsRig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    async def go():
        manager = rig.manager
        try:
            identity = await manager.open(rig.origin)
            worker = rig.workers[0]
            worker.request = lambda request: ({'ok':True,'frame':anchor()}, b'0'*24)
            await manager.request(rig.client, identity, {'op':'render','width':2,'height':3})
            snapshot = rig.paint(identity)
            original_frame = manager._owners[identity].frame
            seen = []
            worker.unsaved_labels = np.array([4,7,9]); worker.undo_history = ['private correction']
            def navigate(request, navigation):
                entered.set()
                assert release.wait(HANG_S)
                sequence, value = navigation.target(); seen.append(value)
                assert navigation.finish(sequence)
                reply = {'ok':True,'frame':anchor(),'navigation':{'id':navigation.identity,'sequence':sequence,'state':'rendered'}}
                reply['frame']['dimensions']['steps'] = value['steps']
                reply['frame']['frame'] = 'c' * 64
                return reply, b'0'*24
            worker.navigate = navigate
            pending = asyncio.create_task(manager.request(rig.client, identity,
                {'op':'change','width':2,'height':3,'navigation':'n'*32,
                 'request':{'frame':anchor()['frame'],'operation':'dimensions','value':target(1)}},
                snapshot=snapshot,draw='draw-one'))
            await until_async(entered.is_set)
            replacement = {'op':'navigation-latest','navigation':'n'*32,'sequence':2,'value':target(2)}
            for blocked in ({'op':'render','width':2,'height':3}, {'op':'labels'}, {'op':'save-layers'}, {'op':'python'}):
                with pytest.raises(ObservationError, match='finish'):
                    await manager.request(rig.client,identity,blocked)
            with pytest.raises(ObservationError):
                await manager.request(object(),identity,replacement,snapshot=rig.pin(),draw='draw-one')
            with pytest.raises(ObservationError):
                await manager.request(rig.client,identity,replacement,snapshot=rig.pin(),draw='another-draw')
            manager.active_project = lambda:'another-project'
            with pytest.raises(ObservationError):
                await manager.request(rig.client,identity,replacement,snapshot=rig.pin(),draw='draw-one')
            manager.active_project = lambda:rig.saved.session
            rig.change(); document=rig.item['view']['legacy']['document']; previous=document['tab'];document['tab']='another-tab'
            with pytest.raises(ObservationError):
                await manager.request(rig.client,identity,replacement,snapshot=rig.pin(),draw='draw-one')
            document['tab']=previous;rig.change();snapshot=rig.pin()
            await manager.request(rig.client,identity,replacement,snapshot=snapshot,draw='draw-one')
            await manager.request(rig.client,identity,{**replacement,'sequence':3,'value':target(3)},snapshot=rig.pin(),draw='draw-one')
            assert manager._owners[identity].navigation.frame is original_frame
            release.set(); result=await pending
            assert seen==[target(3)] and result['navigation']['sequence']==3
            assert manager._owners[identity].frame[0]['dimensions']['steps']==[3,0,0]
            assert manager._owners[identity].navigation is None and not rig.closed
            assert worker.unsaved_labels.tolist()==[4,7,9] and worker.undo_history==['private correction']
            assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
        finally:
            release.set(); await manager.close()
    asyncio.run(go())


@pytest.mark.parametrize('supersede_after_read', [False, True])
def test_broker_cancels_display_regions_without_revoking_grants(monkeypatch, supersede_after_read):
    from tests.test_scientific_host_worker import startup_worker
    from env_worker.server import read_message
    cancelled = {'ok': False, 'code': 'SCIENTIFIC_VIEW_SUPERSEDED'}
    worker, _ = startup_worker(monkeypatch,
        {'op':'read-native-region', 'token':'n'*32, 'region':[[0,1],[0,1]], 'view':True}, cancelled)
    superseded = [not supersede_after_read]
    read = []
    def answer(header, size):
        read.append(header); superseded[0] = True
        return {'ok':True}, b'private pixels'
    worker.grants.answer = answer
    worker._navigation_superseded = lambda:superseded[0]
    assert worker._exchange({'op':'navigate','width':2,'height':3}) == (cancelled,b'')
    worker.process.stdin.seek(0)
    assert read_message(worker.process.stdin)[0]['op'] == 'navigate'
    assert read_message(worker.process.stdin) == (cancelled,b'')
    assert len(read) == int(supersede_after_read)
    assert not worker._stopped.is_set() and not worker._closed
    assert worker.grants.specifications()  # Original input grants remain usable.


def test_worker_continuation_refreshes_cancelled_same_slice_without_resetting_edits():
    import threading
    from env_worker.scientific_host import ReviewSession
    host = object.__new__(ReviewSession)
    host._closed,host._thread,host._frame = False,threading.get_ident(),'a'*64
    private_labels = np.array([4,7,9]); history=['unsaved correction']; refreshed=[]
    layer=SimpleNamespace(visible=True,data=private_labels,refresh=lambda:refreshed.append(True))
    host.viewer=SimpleNamespace(layers=[layer],dims=SimpleNamespace(ndim=3,order=[0,1,2],ndisplay=2,
        current_step=(1,0,0),point=(1,0,0),range=((0,9,1),(0,3,1),(0,3,1)),nsteps=(10,4,4)))
    host.panel=SimpleNamespace(invalidate=lambda:None)
    host.undo_history=history
    command={'frame':'a'*64,'operation':'dimensions','value':target(1)}
    host.navigate('n'*32,True,command)
    host._navigation_needs_refresh=True  # Dims applied, but its read was cancelled.
    host.navigate('n'*32,False,command)
    assert refreshed == [True] and host.dirty and host._frame is None
    assert layer.data is private_labels and host.undo_history is history and not host._closed
    with pytest.raises(ValueError): host.navigate('x'*32,False,command)
    with pytest.raises(ValueError): host.change(command)  # Ordinary edits still require a fresh frame.


@pytest.mark.parametrize('outcome', ['exact', 'wrapped', 'swallowed', 'unrelated', 'cleanup-context', 'cleanup-cause'])
def test_worker_protocol_only_recovers_superseded_display_reads(monkeypatch, tmp_path, outcome):
    import io
    import os
    import sys
    from env_worker import scientific_host_server as server
    from env_worker.server import read_message, write_message
    incoming, output = io.BytesIO(), io.BytesIO()
    callbacks, instances = [], []
    class EmitLoopError(Exception):
        def __init__(self, cause):
            super().__init__('psygnal callback wrapper'); self.__cause__ = cause
    monkeypatch.setitem(sys.modules, 'psygnal', SimpleNamespace(EmitLoopError=EmitLoopError))
    failure = outcome in ('unrelated', 'cleanup-context', 'cleanup-cause')
    def bind(inputs, callback, **kwargs):
        callbacks.append(callback)
        return {}
    class Session:
        native_panel = tool_document = None
        def __init__(self, *args, **kwargs):
            self.calls=[];self.closed=False;self.private_labels=[4,7,9];instances.append(self)
        def navigate(self, identity, first, request):
            self.calls.append(request['value'])
            if first:
                if outcome == 'unrelated':
                    raise RuntimeError('real integration failure')
                try: callbacks[0]({'op':'read-native-region','token':'n'*32,'region':[[0,1],[0,1]],'view':True})
                except ViewSuperseded as error:
                    if outcome == 'exact': raise
                    if outcome == 'wrapped': raise EmitLoopError(error) from error
                    if outcome == 'cleanup-context': raise RuntimeError('real integration failure')
                    if outcome == 'cleanup-cause': raise RuntimeError('real integration failure') from error
            else:
                assert self._navigation_needs_refresh and self._frame is None
        def render(self, width, height):
            return anchor(),b'0'*24
        def close(self): self.closed=True
    monkeypatch.setattr(server,'ReviewSession',Session)
    monkeypatch.setattr(server,'load_factory',lambda *args:lambda context:None)
    monkeypatch.setattr(server.native_regions,'bind',bind)
    write_message(incoming,{'op':'open','inputs':{}})
    request={'op':'navigate','navigation':'n'*32,'first':True,'width':2,'height':3,
             'request':{'frame':'a'*64,'operation':'dimensions','value':target(1)}}
    write_message(incoming,request)
    write_message(incoming,{'ok':False,'code':'SCIENTIFIC_VIEW_SUPERSEDED'})
    write_message(incoming,{**request,'first':False,'request':{**request['request'],'value':target(3)}})
    write_message(incoming,{'op':'close'});incoming.seek(0)
    read_fd,write_fd=os.pipe()
    monkeypatch.setattr(sys,'stdin',SimpleNamespace(buffer=incoming))
    monkeypatch.setattr(sys,'stdout',SimpleNamespace(buffer=output))
    try:
        if failure:
            with pytest.raises(RuntimeError,match='real integration failure'):
                server.main(tmp_path,'fixture:open',read_fd)
        else:
            server.main(tmp_path,'fixture:open',read_fd)
    finally: os.close(write_fd)
    output.seek(0)
    messages=[]
    while output.tell()<len(output.getvalue()): messages.append(read_message(output)[0])
    if failure:
        assert messages[-1] == {'ok':False,'error':'real integration failure'}
        assert not any(message.get('code') == 'SCIENTIFIC_VIEW_SUPERSEDED' for message in messages)
    else:
        assert messages[-3] == {'ok':False,'code':'SCIENTIFIC_VIEW_SUPERSEDED'}
        assert messages[-2]['ok'] and messages[-2]['frame'] == anchor()
        assert messages[-1] == {'ok':True,'closed':True}
        assert instances[0].calls == [target(1),target(3)]
    assert len(instances)==1 and instances[0].private_labels==[4,7,9] and instances[0].closed
