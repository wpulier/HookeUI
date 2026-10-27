"""Accepted synthetic masks through the shared retained-method interface; no inference."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator.scientific_projects import ProjectStore
from tests.test_scientific_arrays import accept, fill, project
from tests.test_method_evidence import selection
from tests.test_scientific_project_api import web  # noqa: F401


def inputs(tmp_path, store=None, session='s1'):
    store = store or ProjectStore(tmp_path/'projects')
    calibration = {'space': {'session':session,'id':'field','revision':1}, 'axes':['y','x'],
                   'units':['um','um'], 'affine':[[2,0,0],[0,3,0]]}
    saved = []
    for values, semantic in [(np.zeros((2,3),dtype='u1'),'intensity'),
                             (np.array([[1,1,0],[0,0,0]],dtype='u1'),'instance-labels'),
                             (np.array([[0,2,2],[0,0,0]],dtype='u1'),'instance-labels')]:
        owner = project(store,session)
        draft = store.array_draft(owner.id,session,component='array.labels',shape=values.shape,dtype=values.dtype.str,
            axes=['y','x'],chunks=[1,3],scientific={'semantic':semantic,'calibration':calibration},max_written_bytes=6)
        saved.append(accept(store,owner,fill(draft,values)))
    source,a,b=saved
    arms=[{'id':key,'factor':'boundary-definition','says':words,'status':'completed','mask':selection(mask)}
          for key,words,mask in [('a','Interior',a),('b','Including outer boundary',b)]]
    request={'session':session,'source':selection(source),'arms':arms,'request':'method-acceptance-0001'}
    hub=SimpleNamespace(resources=SimpleNamespace(projects=store));day=SimpleNamespace(sid=session,generation=1)
    return hub,day,request


def test_retained_summary_choice_reopen_and_export_keep_exact_native_alternatives(tmp_path):
    from orchestrator import method_workflow as flow, scientific_exchange
    hub,day,request=inputs(tmp_path)
    result=asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio'))
    saved=result['project']
    record=flow.read(hub.resources.projects,saved.id,'s1',saved.revision)
    assert record['summary']['arms'][0]['area']==12
    assert record['summary']['differences']==[{'arm':'b','absolute':0.,'unit':'µm²','relative':0.}]
    assert record['summary']['disagreement']==[{'arms':['a','b'],'onlyA':6.,'onlyB':6.,'unit':'µm²'}]
    assert not record['evidence']['evidenceComplete']  # actor declarations cannot invent producing histories
    chosen=asyncio.run(flow.run(hub,day,flow.OPERATION,'choose',{'session':'s1','project':saved.id,
        'revision':saved.revision,'arm':'b','rationale':'Question includes outer boundary'},caller='agent:chat'))['project']
    assert flow.read(hub.resources.projects,chosen.id,'s1',chosen.revision)['evidence']['chosen']=={
        'arm':'b','rationale':'Question includes outer boundary','by':'agent:chat'}
    assert flow.read(hub.resources.projects,saved.id,'s1',saved.revision)['evidence']['chosen'] is None
    bundle=tmp_path/'methods.zip';scientific_exchange.export(hub.resources.projects,chosen.id,'s1',chosen.revision,destination=bundle)
    reopened=ProjectStore(tmp_path/'reopened');scientific_exchange.reimport(reopened,bundle)
    assert flow.read(reopened,chosen.id,'s1',chosen.revision)==flow.read(hub.resources.projects,chosen.id,'s1',chosen.revision)


def test_failed_a_is_not_rebased_and_retry_never_substitutes_another_selection(tmp_path):
    from orchestrator import method_workflow as flow
    from orchestrator.scientific_projects import Conflict
    hub,day,request=inputs(tmp_path)
    request['arms'][0]={'id':'a','factor':'algorithm','says':'First method','status':'failed','outcome':{'says':'No accepted result'}}
    first=asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio'))
    again=asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='agent:chat'))
    assert again['project']==first['project'] and again['reused']
    record=flow.read(hub.resources.projects,first['project'].id,'s1',1)
    assert record['summary']['arms'][0]['area'] is None
    assert record['summary']['arms'][1]['area']==12
    assert record['summary']['differences'][0]['absolute'] is None
    assert record['summary']['disagreement'][0]['onlyA'] is None
    request['arms'][1]['says']='Changed method'
    with pytest.raises(Conflict,match='another comparison'):
        asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio'))


@pytest.mark.parametrize('change',['missing-alignment','bad-digest','cancelled','foreign-session','permission'])
def test_refused_requests_leave_every_accepted_revision_unchanged(tmp_path,change):
    import threading
    from orchestrator import method_workflow as flow
    hub,day,request=inputs(tmp_path);store=hub.resources.projects
    stop=threading.Event()
    if change=='bad-digest':request['source']['digest']='0'*64
    if change=='foreign-session':request['session']='other'
    if change=='cancelled':stop.set()
    if change=='missing-alignment':
        # Genuine stored metadata without an explicit coordinate space, not a fake reader.
        for ref in [request['source'],*(a['mask'] for a in request['arms'])]:
            original=store.read(ref['id'],'s1',revision=ref['revision'])
            source=store.array_source(original.id,'s1',original.revision,original.document['resources'][0])
            values=source.read((slice(0,2),slice(0,3)))
            owner=project(store,'s1')
            draft=store.array_draft(owner.id,'s1',component='array.labels',shape=[2,3],dtype='|u1',axes=['y','x'],chunks=[1,3],
                scientific={'semantic':source.descriptor['semantic']},max_written_bytes=6)
            replacement=accept(store,owner,fill(draft,values));ref.update(selection(replacement))
    before=[(s.id,s.digest) for s in store.list('s1')]
    def permission():
        if change=='permission':raise ValueError('permission withdrawn')
    with pytest.raises(ValueError):
        asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio',stopping=stop,check_caller=permission))
    assert [(s.id,s.digest) for s in store.list('s1')]==before


def test_http_and_authorized_agent_share_exact_comparison_and_caller_receipts(web,tmp_path):
    from orchestrator import method_workflow as flow
    from orchestrator.brain.registry import Registry,ToolContext
    from orchestrator.brain.toolbox.scientific import add_scientific_tools
    from tests.test_scientific_project_api import ORIGIN
    client,built=web
    _,_,request=inputs(tmp_path,built.hub.resources.projects,built.day.sid)
    body={'operation':flow.OPERATION,'action':'retain','selection':request}
    denied=client.post('/api/scientific-methods',headers={'Origin':'https://untrusted.invalid','X-MicroscopeMind':'compare-methods'},json=body)
    assert denied.status_code==403
    spoofed=client.post('/api/scientific-methods',headers={'Origin':ORIGIN,'X-MicroscopeMind':'compare-methods'},json={**body,'caller':'agent:system'})
    assert spoofed.status_code==400
    response=client.post('/api/scientific-methods',headers={'Origin':ORIGIN,'X-MicroscopeMind':'compare-methods'},
        json={'operation':flow.OPERATION,'action':'retain','selection':request})
    assert response.status_code==200,response.text
    result=response.json();saved=result['project']
    assert saved['document']['state']['methodAdmission']['invocation']['caller']=='studio'
    opened=client.get(f"/api/scientific-methods/{saved['id']}",params={'session':built.day.sid,'revision':1})
    assert opened.status_code==200 and opened.json()['summary']['arms'][0]['area']==12
    registry=Registry();add_scientific_tools(registry,SimpleNamespace(hub=built.hub,day=built.day))
    async def agent():
        call=registry.start('compare_retained_methods',{'operation':flow.OPERATION,'action':'retain','selection':request},
                            ToolContext('chat',1,None,asked=built.day.here()))
        await call.task
        assert call.ok,(call.error,call.summary)
        return call.values
    reused=asyncio.run(agent())
    assert reused['project']['id']==saved['id'] and reused['reused']
    assert reused['invocation']['caller']=='agent:chat'
    assert reused['project']['document']['state']['methodAdmission']==saved['document']['state']['methodAdmission']


def test_concurrent_retry_has_one_accepted_destination_and_original_producer(tmp_path):
    from orchestrator import method_workflow as flow
    hub,day,request=inputs(tmp_path)
    async def both():
        return await asyncio.gather(*(flow.run(hub,day,flow.OPERATION,'retain',request,caller=caller)
                                      for caller in ('studio','agent:chat')))
    first,second=asyncio.run(both())
    assert first['project']==second['project']
    assert sorted([first['reused'],second['reused']])==[False,True]
    accepted=[s for s in hub.resources.projects.list('s1') if s.document['kind']=='scientific-method-comparison']
    assert accepted==[first['project']]


@pytest.mark.parametrize("boundary", ["session", "cancel", "permission"])
def test_late_revocation_at_atomic_create_never_publishes(tmp_path,monkeypatch,boundary):
    import threading
    from orchestrator import method_workflow as flow
    hub,day,request=inputs(tmp_path);store=hub.resources.projects
    before=[(s.id,s.digest) for s in store.list('s1')]
    stop=threading.Event(); allowed=True
    def permission():
        if not allowed:raise ValueError('permission withdrawn')
    # Filesystem write seam: mutation after immutable revision bytes, before head publication.
    write=store._write
    def changed(path,data,**kwargs):
        nonlocal allowed
        result=write(path,data,**kwargs)
        if path.parent.name=='revisions':
            if boundary=='session':day.generation+=1
            elif boundary=='cancel':stop.set()
            else:allowed=False
        return result
    monkeypatch.setattr(store,'_write',changed)
    with pytest.raises(ValueError,match='Project changed|Comparison stopped|permission withdrawn'):
        asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio',stopping=stop,check_caller=permission))
    assert [(s.id,s.digest) for s in store.list('s1')]==before


def test_retained_summary_forgery_is_refused_and_zero_a_has_no_relative_difference(tmp_path):
    from orchestrator import method_workflow as flow,scientific_provenance
    from orchestrator.scientific_projects import InvalidProject
    hub,day,request=inputs(tmp_path);store=hub.resources.projects
    # Original input pixels are all zero; retain the same bytes with explicit label meaning.
    original=store.read(request['source']['id'],'s1',revision=request['source']['revision'])
    source=store.array_source(original.id,'s1',original.revision,original.document['resources'][0])
    owner=project(store,'s1');draft=store.array_draft(owner.id,'s1',component='array.labels',shape=[2,3],dtype='|u1',axes=['y','x'],chunks=[1,3],scientific={'semantic':'instance-labels','calibration':source.descriptor['calibration']},max_written_bytes=6)
    zero=accept(store,owner,fill(draft,np.zeros((2,3),dtype='u1')));request['arms'][0]['mask']=selection(zero)
    saved=asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio'))['project']
    assert flow.read(store,saved.id,'s1',1)['summary']['differences'][0]['relative'] is None
    forged=saved.document;forged['state']['methodSummary']['arms'][0]['area']=999
    with pytest.raises(InvalidProject,match='document save'):scientific_provenance.guard(saved,forged)
    # An imported document is still revalidated against native bytes, not trusted for its claims.
    imported=store.create('s1',forged)
    with pytest.raises(InvalidProject,match='does not match'):flow.read(store,imported.id,'s1',1)


def test_identical_choice_retry_returns_the_same_revision_without_replacing_actor(tmp_path):
    from orchestrator import method_workflow as flow
    hub,day,request=inputs(tmp_path)
    saved=asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio'))['project']
    chosen={'session':'s1','project':saved.id,'revision':1,'arm':'a','rationale':'The question concerns interior'}
    first=asyncio.run(flow.run(hub,day,flow.OPERATION,'choose',chosen,caller='studio'))
    second=asyncio.run(flow.run(hub,day,flow.OPERATION,'choose',chosen,caller='agent:chat'))
    assert second['reused'] and second['project']==first['project']
    record=flow.read(hub.resources.projects,saved.id,'s1',2)
    assert record['summary']['chosen']==record['evidence']['chosen']
    assert record['evidence']['chosen']['by']=='studio'
    chosen['rationale']='Different rationale'
    from orchestrator.scientific_projects import Conflict
    with pytest.raises(Conflict):asyncio.run(flow.run(hub,day,flow.OPERATION,'choose',chosen,caller='studio'))


def test_first_native_region_is_read_before_advancing_any_prefix_pool(tmp_path,monkeypatch):
    """An iterator budget detects eager traversal without allocating a huge skinny mask."""
    import builtins
    import threading
    from orchestrator import method_workflow as flow
    hub,day,request=inputs(tmp_path);store=hub.resources.projects
    stop=threading.Event();reads=[]
    from dataclasses import replace
    array_source=store.array_source
    def observed(*args,**kwargs):
        actual=array_source(*args,**kwargs)
        def first_region(region):
            value=actual.read(region);reads.append(region);stop.set();return value
        return replace(actual,read=first_region)
    def bounded_range(*args):
        for index,value in enumerate(builtins.range(*args)):
            if index:raise AssertionError('Prefix iterator consumed ahead of native region/cancellation check')
            yield value
    monkeypatch.setattr(store,'array_source',observed)
    monkeypatch.setattr(flow,'range',bounded_range,raising=False)
    before=[(s.id,s.digest) for s in store.list('s1')]
    with pytest.raises(ValueError,match='Comparison stopped'):
        asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio',stopping=stop))
    assert reads==[(slice(0,1),slice(0,3))]
    assert [(s.id,s.digest) for s in store.list('s1')]==before


def test_computed_summary_truthfully_scopes_capture_and_preserves_legacy_evidence_roundtrip(tmp_path):
    from orchestrator import method_workflow as flow,method_evidence,scientific_exchange
    from orchestrator.scientific_projects import InvalidProject
    hub,day,request=inputs(tmp_path);store=hub.resources.projects
    saved=asyncio.run(flow.run(hub,day,flow.OPERATION,'retain',request,caller='studio'))['project']
    record=flow.read(store,saved.id,'s1',1)
    assert record['summary']['arms'][0]['area']==12
    assert 'This evidence-capture step' in record['evidence']['meaning']
    assert 'comparison calculations are recorded separately' in record['evidence']['meaning']
    legacy_text=('Exact retained values and recorded producing context; declarations are attributed, '
                 'not independently verified. No inference, area calculation or scientific qualification ran.')
    legacy=method_evidence.create(store,'s1',request['source'],request['arms'],actor='historical')
    document=legacy.document;document['state']['methodEvidence']['meaning']=legacy_text
    legacy=store.create('s1',document);before=legacy.digest
    assert method_evidence.read(store,legacy.id,'s1',1)['meaning']==legacy_text
    bundle=tmp_path/'legacy.zip';scientific_exchange.export(store,legacy.id,'s1',1,destination=bundle)
    imported=ProjectStore(tmp_path/'legacy-reopen');scientific_exchange.reimport(imported,bundle)
    assert imported.read(legacy.id,'s1',revision=1).digest==before
    assert method_evidence.read(imported,legacy.id,'s1',1)['meaning']==legacy_text
    chosen=method_evidence.choose(imported,legacy.id,'s1',1,'a','Historical protocol',actor='studio')
    assert method_evidence.read(imported,chosen.id,'s1',2)['meaning']==legacy_text
    assert imported.read(legacy.id,'s1',revision=1).digest==before
    forged=legacy.document;forged['state']['methodEvidence']['meaning']='Scientifically validated accuracy'
    forged=store.create('s1',forged)
    with pytest.raises(InvalidProject,match='disagrees'):method_evidence.read(store,forged.id,'s1',1)
