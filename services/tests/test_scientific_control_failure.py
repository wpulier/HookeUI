"""Admitted callback failures retain private work without claiming rollback or replay."""
from pathlib import Path
import asyncio
import json
import pytest
from tests.test_scientific_change_recovery import host
from tests.test_scientific_host_worker import actual_host  # noqa: F401


def test_admitted_control_exception_is_distinct_from_refusal_and_keeps_private_state():
    from env_worker import scientific_host as local
    session=host()
    def fail():
        session.viewer.camera.zoom=7
        raise ValueError('A callback failed after changing private state')
    session.panel.prepare=lambda request:fail
    with pytest.raises((ValueError,RuntimeError)) as error:
        session.change({'frame':'current','operation':'controls','value':{}})
    assert session.viewer.camera.zoom==7 and session.dirty and session._frame is None
    assert isinstance(error.value,local.ControlFailed)


def test_control_failure_recovery_names_possible_changes_and_never_promises_rollback():
    from orchestrator.scientific_host import ControlFailed
    from orchestrator.scientific_review_api import error_response
    response=error_response(ControlFailed('Controlled callback failure'))
    value=json.loads(response.body)
    assert response.status_code==409 and value['code']=='SCIENTIFIC_CONTROL_FAILED'
    assert 'Check current view' in value['next'] and 'may have changed' in value['next']
    assert 'not run again' in value['next'] and 'private' in value['next']


def test_owned_control_failure_keeps_worker_and_records_uncertain_action(tmp_path,monkeypatch):
    from orchestrator.scientific_host import ControlFailed
    from tests.test_scientific_reviews import ReviewsRig,Worker
    rig=ReviewsRig(tmp_path);original=Worker.request;attempts=[]
    def request(worker,fields):
        if fields['op']=='change':
            attempts.append(fields)
            raise ControlFailed('Changed private state before failing')
        return original(worker,fields)
    monkeypatch.setattr(Worker,'request',request)
    async def run():
        manager=rig.manager
        try:
            identity=await manager.open(rig.origin)
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            with pytest.raises(ControlFailed):
                await manager.request(rig.client,identity,{'op':'change','width':2,'height':3,
                    'request':{'frame':'a'*64,'operation':'controls','value':{}}},snapshot=rig.paint(identity),draw='draw-one')
            owner=manager._owners[identity]
            assert rig.workers[0] not in rig.closed and owner.frame is None
            assert owner.history.document['state']['actions'][-1]['state']=='interrupted'
            assert owner.history.document['state']['actions'][-1]['outcome']=={'state':'unconfirmed'}
            assert rig.store.read(rig.saved.id,rig.saved.session)==rig.saved
            await manager.request(rig.client,identity,{'op':'render','width':2,'height':3})
            assert len(attempts)==1, 'Refreshing must never replay the failed callback'
            monkeypatch.setattr(Worker,'request',original)
            await manager.request(rig.client,identity,{'op':'change','width':2,'height':3,
                'request':{'frame':'a'*64,'operation':'controls','value':{}}},snapshot=rig.paint(identity),draw='draw-one')
            assert owner.history.document['state']['actions'][-1]['outcome']=={'state':'completed'}
        finally:await manager.close()
    asyncio.run(run())


def test_actual_callback_failure_preserves_namespace_and_corrected_retry(actual_host):
    import numpy as np
    from orchestrator import scientific_host
    from tests.test_native_regions import source
    values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28);before=values.tobytes()
    with scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(values,[])}) as worker:
        code='''kept_after_callback_failure=83
callback_failures=[]
def fail_opacity(event):
    callback_failures.append(float(viewer.layers[0].opacity))
    raise ValueError('Controlled actual plugin callback failure')
viewer.layers[0].events.opacity.ignore_callback_errors=False
viewer.layers[0].events.opacity.connect(fail_opacity)
'''
        result,_=worker.request({'op':'python-resources','source':code});assert not result['console']['error'],result
        shown,_=worker.request({'op':'render','width':256,'height':192});frame=shown['frame']
        button=next(row for row in frame['controls']['controls'] if row['kind']=='button')
        request={'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'controls','value':{
            'panel':frame['controls']['panel'],'revision':frame['controls']['revision'],'control':button['id'],'op':'invoke'}}}
        with pytest.raises((ValueError,RuntimeError,EOFError,OSError)) as failure:worker.request(request)
        assert worker.process.poll() is None, 'An ordinary callback failure discarded private work'
        assert isinstance(failure.value,scientific_host.ControlFailed)
        with pytest.raises(scientific_host.ChangeRefused):worker.request(request)
        result,_=worker.request({'op':'python-resources','source':"assert callback_failures==[.4]\nassert kept_after_callback_failure==83\nviewer.layers[0].events.opacity.disconnect(fail_opacity)\nviewer.layers[0].opacity=.8"})
        assert not result['console']['error'],result
        fresh,_=worker.request({'op':'render','width':256,'height':192});frame=fresh['frame']
        request['request']['frame']=frame['frame'];request['request']['value']['revision']=frame['controls']['revision']
        result,_=worker.request(request);assert result['frame']['layers'][0]['opacity']==.4
        result,_=worker.request({'op':'python-resources','source':'assert callback_failures==[.4]\nkept_after_callback_failure+1'})
        assert result['console']['result']=='84' and not result['console']['error']
    assert values.tobytes()==before and worker.process.poll() is not None and not worker.work.exists()


def test_wrapped_memory_failure_is_fatal_even_with_an_ordinary_outer_exception():
    from env_worker import scientific_host as local
    session=host()
    def fail():
        try:raise MemoryError('Controlled allocation refusal; no memory exhaustion')
        except MemoryError as error:raise RuntimeError('Signal wrapper') from error
    session.panel.prepare=lambda request:fail
    with pytest.raises(Exception) as error:
        session.change({'frame':'current','operation':'controls','value':{}})
    assert not isinstance(error.value,(local.ControlFailed,local.ChangeRefused))
    assert session._frame is None and session.dirty


def test_actual_wrapped_memory_failure_stops_only_its_owned_worker(actual_host):
    import numpy as np
    from contextlib import ExitStack
    from orchestrator import scientific_host
    from tests.test_native_regions import source
    values=np.arange(6*24*28,dtype='<u2').reshape(6,24,28);before=values.tobytes()
    with ExitStack() as stack:
        good=stack.enter_context(scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(values,[])}))
        broken=stack.enter_context(scientific_host.review(actual_host['key'],Path(actual_host['folder']).name,{'volume':source(values,[])}))
        result,_=broken.request({'op':'python-resources','source':'''def fatal_opacity(event):
    raise MemoryError('Controlled memory refusal')
viewer.layers[0].events.opacity.ignore_callback_errors=False
viewer.layers[0].events.opacity.connect(fatal_opacity)
'''})
        assert not result['console']['error'],result
        shown,_=broken.request({'op':'render','width':256,'height':192});frame=shown['frame']
        button=next(row for row in frame['controls']['controls'] if row['kind']=='button')
        with pytest.raises((RuntimeError,ValueError,OSError,EOFError)) as error:
            broken.request({'op':'change','width':256,'height':192,'request':{'frame':frame['frame'],'operation':'controls','value':{
                'panel':frame['controls']['panel'],'revision':frame['controls']['revision'],'control':button['id'],'op':'invoke'}}})
        assert not isinstance(error.value,(scientific_host.ControlFailed,scientific_host.ChangeRefused))
        assert broken.process.poll() is not None
        result,_=good.request({'op':'python-resources','source':'assert int(viewer.layers[0].data[0,0,1])==1\n73'})
        assert result['console']['result']=='73' and good.process.poll() is None
    assert values.tobytes()==before and good.process.poll() is not None and not broken.work.exists()


@pytest.mark.parametrize('cause',[MemoryError,RecursionError,SystemExit,KeyboardInterrupt])
def test_fatal_callback_chains_are_not_recoverable_control_errors(cause):
    from env_worker import scientific_host as local
    session=host()
    def fail():
        try:raise cause('Controlled fatal callback')
        except BaseException as error:raise RuntimeError('Wrapped signal') from error
    session.panel.prepare=lambda request:fail
    with pytest.raises(RuntimeError) as error:
        session.change({'frame':'current','operation':'controls','value':{}})
    assert not isinstance(error.value,local.ControlFailed)


def test_memory_failure_in_exception_group_remains_fatal():
    from env_worker import scientific_host as local
    session=host()
    def fail():raise ExceptionGroup('Callback failures',[ValueError('ordinary'),MemoryError('fatal')])
    session.panel.prepare=lambda request:fail
    with pytest.raises(ExceptionGroup):
        session.change({'frame':'current','operation':'controls','value':{}})


@pytest.mark.parametrize('outer',[ 'ControlFailed', 'ChangeRefused', 'RestoreRefused'])
def test_callback_cannot_forge_recovery_for_a_fatal_cause(outer):
    from env_worker import scientific_host as local
    session=host()
    def fail():
        try:raise MemoryError('Controlled fatal allocation refusal')
        except MemoryError as error:raise getattr(local,outer)('Claimed recovery') from error
    session.panel.prepare=lambda request:fail
    with pytest.raises(Exception) as error:
        session.change({'frame':'current','operation':'controls','value':{}})
    assert not isinstance(error.value,(local.ControlFailed,local.ChangeRefused))
    assert session._frame is None and session.dirty


def test_cyclic_or_oversized_callback_exception_graph_fails_closed():
    from env_worker import scientific_host as local
    cycle=ValueError('cycle');cycle.__cause__=cycle
    assert not local.recoverable_callback(cycle)
    assert not local.recoverable_callback(ExceptionGroup('large',[ValueError(str(i)) for i in range(65)]))
    shared=ValueError('shared');outer=RuntimeError('wrapper')
    outer.__cause__=outer.__context__=shared
    assert local.recoverable_callback(outer)


def test_exception_group_cannot_hide_its_fatal_members():
    from env_worker import scientific_host as local
    class MaskedGroup(ExceptionGroup):
        @property
        def exceptions(self):return (ValueError('claimed ordinary error'),)
    assert not local.recoverable_callback(MaskedGroup('masked',[MemoryError('actual fatal error')]))


def test_private_callback_summary_bounds_groups_cycles_and_broken_messages():
    from env_worker.scientific_callback_errors import callback_summary
    class BrokenMessage(ValueError):
        def __str__(self): raise RuntimeError('cannot format')
    cause = PermissionError('Model file unavailable')
    outer = RuntimeError('Queued work failed')
    outer.__cause__ = cause
    cause.__cause__ = outer
    report = callback_summary(ExceptionGroup('callbacks', [outer, BrokenMessage()]))
    assert report.count('Model file unavailable') == 1
    assert 'exception text unavailable' in report
    assert len(callback_summary(ExceptionGroup('large', [ValueError('x'*5000) for _ in range(100)]))) <= 1800


@pytest.mark.parametrize('failure', [MemoryError(), RecursionError(), SystemExit(), KeyboardInterrupt()])
@pytest.mark.parametrize('wrapped', [False, True])
def test_callback_summary_never_recovers_fatal_message_formatting(failure, wrapped):
    from env_worker.scientific_callback_errors import callback_summary
    class FatalMessage(ValueError):
        def __str__(self):
            if not wrapped:
                raise failure
            try:
                raise failure
            except BaseException as cause:
                raise RuntimeError('wrapped fatal formatting') from cause
    with pytest.raises(RuntimeError if wrapped else type(failure)):
        callback_summary(FatalMessage())
