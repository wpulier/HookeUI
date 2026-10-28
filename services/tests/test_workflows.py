"""Workflow retention/recovery contracts. Synthetic documents; no scientific accuracy claim."""
import copy
import pytest
from orchestrator.scientific_projects import ProjectStore, InvalidProject, Conflict
from orchestrator.scientific_comparisons import identity


def service(tmp_path):
    from orchestrator.workflows import Workflows
    return Workflows(ProjectStore(tmp_path/'projects'))


def source(w, session='s1', title='Image'):
    return w.store.create(session, {'kind':'scene','title':title,'state':{},'resources':[],'runtimes':[]})


def graph():
    return {'format':1,'input':{'kind':'scientific-scene'},'nodes':[
        {'id':'check','name':'Inspect','kind':'review','needs':['input'],'settings':{}}]}


def test_definition_mutations_retain_creator_and_each_callers_revision(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.workflow_service import WorkflowService
    w=service(tmp_path)
    runner=WorkflowService(SimpleNamespace(resources=SimpleNamespace(projects=w.store)),SimpleNamespace())
    a='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa';b='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb'
    async def exercise():
        command={'action':'create','name':'Review','graph':graph(),'request':'a'*32}
        first=(await runner.command(command,actor=a))['workflow']
        assert first['createdBy']==a and first['lastChange']=={'by':a,'action':'create','revision':1}
        assert (await runner.command(command,actor=b))['workflow']==first
        with pytest.raises(Conflict):await runner.command({**command,'name':'Different'},actor=b)
        edit={'action':'edit','workflow':first['id'],'revision':1,'name':'Review better','graph':graph(),'removed':False}
        changed=(await runner.command(edit,actor=b))['workflow']
        assert changed['createdBy']==a and changed['lastChange']=={'by':b,'action':'edit','revision':2}
        duplicate=(await runner.command({'action':'duplicate','workflow':first['id'],'revision':2,
            'name':'Another review','request':'b'*32},actor=b))['workflow']
        assert duplicate['createdBy']==b and duplicate['lastChange']=={'by':b,'action':'duplicate','revision':1}
        removed=(await runner.command({**edit,'revision':2,'removed':True},actor=b))['workflow']
        assert removed['createdBy']==a and removed['lastChange']=={'by':b,'action':'remove','revision':3}
        restored=(await runner.command({**edit,'revision':3},actor=a))['workflow']
        assert restored['createdBy']==a and restored['lastChange']=={'by':a,'action':'restore','revision':4}
        with pytest.raises(Conflict):await runner.command(edit,actor=b)
        from orchestrator.workflows import wire
        assert wire(runner.workflows.definition(first['id'],1))==first
        assert (await runner.command(command,actor=b))['workflow']==first
    asyncio.run(exercise())


def test_legacy_definition_edit_keeps_original_creator_unknown(tmp_path):
    w=service(tmp_path)
    legacy=w.library.create('personal',{'kind':'scientific-workflow','title':'Legacy review',
        'state':{'workflow':{'graph':graph(),'removed':False}},'resources':[],'runtimes':[]})
    actor='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb'
    changed=w.edit(legacy.id,1,'Review today',graph(),False,actor=actor)
    state=changed.document['state']['workflow']
    assert 'createdBy' not in state
    assert state['lastChange']=={'by':actor,'action':'edit','revision':2}
    assert w.definition(legacy.id,1).document==legacy.document


def test_library_versions_cross_project_and_removal_preserve_runs(tmp_path):
    w=service(tmp_path); a=source(w); b=source(w,'s2')
    definition=w.create('Count',graph(),'a'*32)
    run=w.start(definition.id,1,'s2',[identity(b)],'b'*32,actor='studio')
    assert w.start(definition.id,1,'s2',[identity(b)],'b'*32,actor='studio').id==run.id
    edited=w.edit(definition.id,1,'Count better',graph(),False)
    assert edited.revision==2 and w.definition(definition.id,1).document['title']=='Count'
    w.edit(definition.id,2,'Count better',graph(),True)
    assert w.list()==[] and len(w.list(removed=True))==1
    assert w.status(run.id,'s2')['definition']['name']=='Count'
    with pytest.raises(InvalidProject,match='removed'):w.start(definition.id,1,'s2',[identity(b)],'c'*32,actor='studio')
    assert str(a.id) not in str(w.status(run.id,'s2'))
    with pytest.raises((ValueError,Conflict)):
        w.start(definition.id,1,'s2',[identity(a)],'d'*32,actor='studio')


def test_graph_validation_rejects_cycles_unknowns_and_unbounded_work(tmp_path):
    w=service(tmp_path)
    for change in ('cycle','unknown','too-many'):
        g=graph()
        if change=='cycle':g['nodes'][0]['needs']=['check']
        if change=='unknown':g['nodes'][0]['kind']='shell'
        if change=='too-many':g['nodes']*=13
        with pytest.raises(InvalidProject):w.create('Bad',g,'a'*32)


def test_review_uses_exact_revision_and_run_is_bound_to_its_project(tmp_path):
    w=service(tmp_path); saved=source(w); d=w.create('Review',graph(),'a'*32)
    r=w.start(d.id,1,saved.session,[identity(saved)],'b'*32,actor='studio')
    claim=w.claim(r.id,saved.session)
    assert claim['node']['kind']=='review'
    w.finish(claim,{'status':'awaiting-review','output':identity(saved)})
    other=source(w)
    with pytest.raises(InvalidProject,match='same'):
        w.review(r.id,saved.session,claim['attempt']['id'],identity(other),True,'c'*32)
    result=w.review(r.id,saved.session,claim['attempt']['id'],identity(saved),True,'c'*32)
    assert result['status']=='completed'
    assert service(tmp_path).status(r.id,saved.session)==result
    assert w.review(r.id,saved.session,claim['attempt']['id'],identity(saved),True,'c'*32)==result


def test_claim_crash_recovery_refuses_duplicate_and_retry_until_resolved(tmp_path):
    w=service(tmp_path); s=source(w); d=w.create('Review',graph(),'a'*32)
    r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    claim=w.claim(r.id,s.session)
    assert w.claim(r.id,s.session) is None
    restarted=service(tmp_path)
    restarted.recover(r.id,s.session)
    assert restarted.status(r.id,s.session)['status']=='uncertain'
    with pytest.raises(InvalidProject,match='uncertain'):restarted.control(r.id,s.session,'retry')
    restarted.resolve(r.id,s.session,claim['attempt']['id'],'Confirmed no execution survived')
    restarted.control(r.id,s.session,'retry')
    newer=restarted.claim(r.id,s.session)
    assert newer['attempt']['id']!=claim['attempt']['id']
    w.finish(claim,{'status':'completed','output':identity(s)})
    state=restarted.status(r.id,s.session)
    assert state['status']!='completed' and len(state['attempts'])==2


def test_pause_stop_and_late_results_are_retained_without_acceptance(tmp_path):
    w=service(tmp_path); s=source(w); d=w.create('Review',graph(),'a'*32)
    r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    w.control(r.id,s.session,'pause');assert w.claim(r.id,s.session) is None
    w.control(r.id,s.session,'resume');claim=w.claim(r.id,s.session)
    w.control(r.id,s.session,'stop')
    assert w.status(r.id,s.session)['status']=='stopping'
    w.finish(claim,{'status':'completed','output':identity(s)})
    status=w.status(r.id,s.session)
    assert status['status']=='stopped'
    assert status['attempts'][0]['status']=='stopped'
    assert status['attempts'][0]['output']==identity(s)


def test_batch_freezes_inputs_and_runs_sequentially(tmp_path):
    w=service(tmp_path); a=source(w); b=source(w)
    d=w.create('Batch',graph(),'a'*32)
    r=w.start(d.id,1,a.session,[identity(a),identity(b)],'b'*32,actor='studio')
    first=w.claim(r.id,a.session);assert first['item']==0
    w.finish(first,{'status':'awaiting-review','output':identity(a)})
    assert w.claim(r.id,a.session) is None
    w.review(r.id,a.session,first['attempt']['id'],identity(a),True,'c'*32)
    second=w.claim(r.id,a.session);assert second['item']==1
    w.finish(second,{'status':'awaiting-review','output':identity(b)})
    w.review(r.id,a.session,second['attempt']['id'],identity(b),True,'d'*32)
    assert w.status(r.id,a.session)['status']=='completed'


def test_request_reuse_does_not_start_different_work(tmp_path):
    w=service(tmp_path);s=source(w);d=w.create('A',graph(),'a'*32)
    with pytest.raises(Conflict):w.create('B',graph(),'a'*32)
    w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    w.edit(d.id,1,'B',graph(),False)
    with pytest.raises(Conflict):w.start(d.id,2,s.session,[identity(s)],'b'*32,actor='studio')


def native_input(w,session='s1',data=None):
    import numpy as np
    from orchestrator.scientific_families import write_array
    s=w.store.create(session,{'kind':'scene','title':'Synthetic image','state':{},'resources':[],
        'runtimes':[{'bundle':'mine/test-viewer','generation':'g1','operation':'viewer'}]})
    if data is None:
        data=np.zeros((32,32),dtype='uint8');data[4:12,4:12]=220;data[20:28,20:28]=220
    ref=write_array(w.store,s,'array.image',data,['y','x'],{'semantic':'intensity','calibration':{'space':{'session':session,'id':s.id,'revision':1},'axes':['y','x'],'units':['pixel','pixel'],'affine':[[1,0,0],[0,1,0]]}})
    doc=s.document;doc['resources']=[ref];doc['state']['scientificView']={'runtime':doc['runtimes'][0],'inputs':{'image':ref}}
    return w.store.save(s.id,session,doc,expected_revision=s.revision)


def test_bright_regions_review_measure_export_use_native_values(tmp_path):
    from orchestrator.workflow_execution import ScientificSteps, starter
    from orchestrator import scientific_resource_actions as actions
    w=service(tmp_path);s=native_input(w);driver=ScientificSteps(w)
    definition=w.create('Measure bright regions',starter(),'a'*32)
    run=w.start(definition.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    first=w.claim(run.id,s.session);out=driver.execute(first);w.finish(first,out)
    labels=w.store.read(out['output']['id'],s.session,revision=out['output']['revision'])
    assert labels.document['state']['hookeOutput']['base']==identity(s)
    second=w.claim(run.id,s.session);w.finish(second,driver.execute(second))
    assert w.status(run.id,s.session)['status']=='awaiting-review'
    w.review(run.id,s.session,second['attempt']['id'],identity(labels),True,'c'*32)
    measure=w.claim(run.id,s.session);measured=driver.execute(measure);w.finish(measure,measured)
    table=actions.table(w.store,measured['output']['id'],s.session,measured['output']['revision'])
    assert len(table['rows'])==2
    export=w.claim(run.id,s.session);exported=driver.execute(export);w.finish(export,exported)
    file=w.store.read(exported['output']['id'],s.session)
    src=w.store.file_source(file.id,s.session,file.revision,file.document['resources'][0])
    assert src.read(0,src.size)==actions.table_csv(table)
    assert service(tmp_path).status(run.id,s.session)['status']=='completed'
    assert w.store.read(s.id,s.session)==s


def test_changed_generation_and_unsupported_native_input_refuse_before_execution(tmp_path):
    from orchestrator.workflow_execution import ScientificSteps, starter
    w=service(tmp_path);s=native_input(w);g=starter();g['nodes'][0]['settings']['generation']='old'
    d=w.create('Pinned',g,'a'*32);r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    with pytest.raises(InvalidProject,match='generation'):ScientificSteps(w).execute(w.claim(r.id,s.session))


def test_research_settings_require_bounded_report_and_enforced_tool_allowlist(tmp_path):
    from orchestrator.workflow_research import validate
    good={'question':'Explain measurements','criteria':'Cite exact inputs','provider':'gemini-google','maximumUsd':.1,
          'seconds':60,'tools':[],'output':'report'}
    validate(good)
    for update in ({'tools':['shell']},{'maximumUsd':0},{'seconds':0},{'output':'parameters'}):
        with pytest.raises(InvalidProject):validate({**good,**update})

from tests.test_scientific_project_api import web, ORIGIN  # noqa: E402,F401

def test_workflow_http_uses_explicit_owner_and_shared_commands(web):
    client,built=web
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'workflow'}
    payload={'action':'create','name':'Inspect','graph':graph(),'request':'a'*32}
    assert client.post('/api/workflows/command',json=payload).status_code==403
    made=client.post('/api/workflows/command',json=payload,headers=headers)
    assert made.status_code==200,made.text
    definition=made.json()['workflow']
    s=built.hub.resources.projects.create(built.day.sid,{'kind':'scene','title':'Image','state':{},'resources':[],'runtimes':[]})
    started=client.post('/api/workflows/command',json={'action':'start','workflow':definition['id'],'revision':1,
        'session':s.session,'inputs':[identity(s)],'request':'b'*32},headers=headers)
    assert started.status_code==200,started.text
    run=started.json()['run']
    assert run['inputs']==[identity(s)]
    assert client.get('/api/workflows').json()['workflows'][0]['name']=='Inspect'
    assert client.get(f'/api/workflow-runs/{run["id"]}',params={'session':s.session}).json()['run']['definition']['name']=='Inspect'


def test_agent_and_studio_share_the_workflow_library(web):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.brain.registry import Registry,ToolContext
    from orchestrator.brain.toolbox.workflows import add_workflow_tools
    client,built=web;registry=Registry();add_workflow_tools(registry,SimpleNamespace(hub=built.hub,day=built.day))
    async def invoke():
        call=registry.start('workflow',{'command':{'action':'create','name':'From researcher','graph':graph(),'request':'9'*32}},
                            ToolContext('chat',1,None,asked=built.day.here()))
        await call.task
        assert call.ok,(call.summary,call.error)
        return call.values
    value=asyncio.run(invoke())
    assert client.get('/api/workflows').json()['workflows'][0]['id']==value['workflow']['id']


def test_generic_save_cannot_forge_or_reclassify_workflow_receipts(web):
    client,built=web;w=built.hub.workflow_service.workflows
    s=source(w,built.day.sid);d=w.create('Review',graph(),'a'*32);r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'save-project'}
    for change in ('state','kind'):
        doc=r.document
        if change=='state':doc['state']['workflowRun']['control']='stop'
        else:doc['kind']='scene'
        response=client.put(f'/api/scientific-projects/{r.id}',json={'session':s.session,'expectedRevision':r.revision,'document':doc},headers=headers)
        assert response.status_code==400,response.text
    assert w.run(r.id,s.session)==r


def test_correction_starts_derived_run_and_keeps_old_results(tmp_path):
    w=service(tmp_path);s=source(w);g=graph();g['nodes'].append({'id':'next','name':'Next review','kind':'review','needs':['check'],'settings':{}})
    d=w.create('Two reviews',g,'a'*32);r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    c=w.claim(r.id,s.session);w.finish(c,{'status':'awaiting-review','output':identity(s)})
    w.review(r.id,s.session,c['attempt']['id'],identity(s),True,'c'*32)
    second=w.claim(r.id,s.session);w.finish(second,{'status':'awaiting-review','output':identity(s)})
    w.review(r.id,s.session,second['attempt']['id'],identity(s),True,'d'*32)
    old=w.status(r.id,s.session)
    doc=s.document;doc['title']='Corrected';corrected=w.store.save(s.id,s.session,doc,expected_revision=s.revision)
    derived=w.revise(r.id,s.session,c['attempt']['id'],identity(corrected),'e'*32)
    assert w.status(r.id,s.session)==old
    assert w.status(derived.id,s.session)['attempts'][0]['output']==identity(corrected)
    next_claim=w.claim(derived.id,s.session)
    assert next_claim['node']['id']=='next' and next_claim['inputs']==[identity(corrected)]


def test_bright_outputs_preserve_declared_input_coordinate_identity(tmp_path):
    from orchestrator.workflow_execution import ScientificSteps,starter
    from orchestrator import scientific_resource_actions as actions
    w=service(tmp_path);s=native_input(w);d=w.create('Regions',starter(),'a'*32)
    r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio');claim=w.claim(r.id,s.session)
    result=ScientificSteps(w).execute(claim)
    saved=w.store.read(result['output']['id'],s.session)
    assert saved.document['state']['hookeOutput'].get('coordinatesFrom')
    before=actions.selected(w.store,s.id,s.session,s.revision,'image')[2]
    after=actions.selected(w.store,saved.id,s.session,saved.revision,'output')[2]
    assert before.descriptor['calibration']==after.descriptor['calibration']


def test_research_worker_can_capture_and_find_workflows_without_camera(tmp_path):
    import asyncio
    from tests.test_research_scientific_measurement import setup
    from orchestrator.research.hands import Hands
    hub,researcher,_,_=setup(tmp_path)
    hub.execution_day.elsewhere=lambda sid: hub.execution_day
    hub.execution_day.local_only=False
    async def run():
        hands=Hands(hub)
        made=await hands.run(researcher,'workflow',params={'action':'create','name':'Review saved data','graph':graph(),'request':'a'*32},run_id=researcher._attempt.id)
        assert made['ok'],made
        choices=await hands.run(researcher,'workflow',params={'action':'list'},run_id=researcher._attempt.id)
        assert choices['ok'] and choices['values']['workflows'][0]['name']=='Review saved data'
        denied=await hands.run(researcher,'workflow',params={'action':'review'},run_id=researcher._attempt.id)
        assert not denied['ok']
    asyncio.run(run())


def test_same_grid_branches_compare_through_existing_method_service(web):
    from orchestrator.workflow_execution import starter
    from orchestrator.workflow_service import service as shared
    client,built=web;runner=shared(built.hub,built.day);w=runner.workflows;s=native_input(w,built.day.sid)
    g=starter();a=g['nodes'][0];b=copy.deepcopy(a);b.update(id='alternative',name='Alternative settings');b['settings']['parameters']['min_area']=25
    g['nodes']=[a,b,{'id':'compare','name':'Compare regions','kind':'compare','needs':['regions','alternative'],'settings':{}}]
    d=w.create('Compare',g,'a'*32);r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    for _ in range(3):
        claim=w.claim(r.id,s.session);assert claim
        w.finish(claim,runner.steps.execute(claim))
    state=w.status(r.id,s.session)
    assert state['status']=='completed'
    output=next(a['output'] for a in state['attempts'] if a['node']=='compare')
    saved=w.store.read(output['id'],s.session)
    assert saved.document['kind']=='scientific-method-comparison'


def test_run_continues_after_origin_project_switch_and_duplicate_dispatch(web):
    import asyncio
    from orchestrator.workflow_execution import starter
    client,built=web;runner=built.hub.workflow_service;w=runner.workflows;s=native_input(w,built.day.sid)
    definition=w.create('Durable',starter(),'a'*32)
    async def exercise():
        reply=await runner.command({'action':'start','workflow':definition.id,'revision':1,'session':s.session,'inputs':[identity(s)],'request':'b'*32},actor='studio')
        run=reply['run'];built.day.sid='2026-01-01'
        runner.launch(run['id'],s.session)
        await runner.tasks[(s.session,run['id'])]
        state=w.status(run['id'],s.session)
        assert state['status']=='awaiting-review'
        assert len(state['attempts'])==2
        assert all(a['output']['session']==s.session for a in state['attempts'])
    asyncio.run(exercise())


def test_derived_run_initialization_resumes_after_partial_write(tmp_path,monkeypatch):
    w=service(tmp_path);s=source(w);d=w.create('Review',graph(),'a'*32)
    r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio');c=w.claim(r.id,s.session)
    w.finish(c,{'status':'completed','output':identity(s)})
    original=w.store.create
    def interrupted(session,doc,**kwargs):
        if doc['kind']=='scientific-workflow-attempt':raise OSError('interrupted seed')
        return original(session,doc,**kwargs)
    monkeypatch.setattr(w.store,'create',interrupted)
    with pytest.raises(OSError):w.revise(r.id,s.session,c['attempt']['id'],identity(s),'c'*32)
    pending=next(x for x in w.store.list(s.session) if x.document['state'].get('workflowRun',{}).get('derivedFrom'))
    with pytest.raises(InvalidProject,match='initializ'):w.control(pending.id,s.session,'resume')
    monkeypatch.setattr(w.store,'create',original)
    derived=w.revise(r.id,s.session,c['attempt']['id'],identity(s),'c'*32)
    assert w.status(derived.id,s.session)['status']=='completed'


def test_unavailable_managed_research_refuses_before_dispatch_or_allowance(tmp_path):
    from orchestrator import workflow_research as research
    from orchestrator.workflow_execution import ScientificSteps
    w=service(tmp_path);s=source(w)
    settings={'question':'Explain','criteria':'Cite evidence','provider':'gemini-google','maximumUsd':.1,'seconds':1,'tools':[],'output':'report'}
    g=graph();g['nodes'][0].update(kind='researcher',settings=settings)
    assert not ScientificSteps(w).validate(g,[identity(s)],s.session)['available']
    before=list(w.store.list(s.session))
    with pytest.raises(InvalidProject,match='managed research service'):
        research.start(w,{'node':g['nodes'][0]})
    assert list(w.store.list(s.session))==before


def test_declared_input_requirements_refuse_incompatible_data_before_start(tmp_path):
    from orchestrator.workflow_execution import ScientificSteps
    w=service(tmp_path);s=native_input(w);g=graph()
    g['input']={'kind':'scientific-scene','semantic':'intensity','axes':['z','y','x'],'dtype':'|u1','calibration':'required'}
    d=w.create('Stack only',g,'a'*32)
    ready=ScientificSteps(w).validate(d.document['state']['workflow']['graph'],[identity(s)],s.session)
    assert not ready['available'] and 'axes' in str(ready)


def test_research_manager_forwards_original_attempt_and_workflow_command(tmp_path):
    import asyncio
    from tests.test_research_scientific_measurement import setup,FakeHub,ResearchManager
    from orchestrator.research.hands import Hands
    hub,s,_,_=setup(tmp_path);hub.execution_day.elsewhere=lambda sid:hub.execution_day;hub.execution_day.local_only=False
    manager=ResearchManager(FakeHub(),root=tmp_path/'research');manager.hands=Hands(hub);manager.sessions[s.rid]=s
    message={'op':'run','rid':s.rid,'operation':'workflow','runId':s._attempt.id,
        'params':{'action':'create','name':'Reusable review','graph':graph(),'request':'a'*32}}
    reply=asyncio.run(manager._request(message))
    assert reply['ok'],reply
    assert reply['values']['workflow']['name']=='Reusable review'
    assert reply['values']['workflow']['createdBy']=='researcher:'+s.rid
    message['runId']='stale'
    assert not asyncio.run(manager._request(message))['ok']


from tests.test_scientific_recipes import configured, scene, member


def test_explicit_legacy_upgrade_keeps_original_definition_and_run(configured):
    from orchestrator import scientific_recipes as recipes
    from orchestrator.workflows import Workflows
    store,source,template=configured;w=Workflows(store)
    legacy=recipes.create(store,template.id,template.session,template.revision,'a'*32)
    run=recipes.begin(store,legacy.id,legacy.session,legacy.revision,'b'*32)
    upgraded=w.upgrade(legacy.id,legacy.session,legacy.revision,'Reusable tool','c'*32)
    assert store.read(legacy.id,legacy.session)==legacy and store.read(run.id,run.session)==run
    assert upgraded.document['resources']==[]
    assert upgraded.document['state']['workflow']['graph']['nodes'][0]['settings']['mode']=='interactive'


def test_capture_and_upgrade_definition_authors_are_preserved(configured):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.workflow_service import WorkflowService
    from orchestrator import scientific_recipes as recipes
    store,source_,template=configured
    runner=WorkflowService(SimpleNamespace(resources=SimpleNamespace(projects=store)),
        SimpleNamespace(elsewhere=lambda sid:None))
    runner.workflows.library=ProjectStore(store.root/'isolated-workflow-library')
    a='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa';b='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb'
    legacy=recipes.create(store,template.id,template.session,template.revision,'a'*32)
    async def exercise():
        for action,source,req in [('upgrade',legacy,'c'*32),('capture',template,'d'*32)]:
            command={'action':action,'source':source.id,'session':source.session,'revision':source.revision,
                'name':'Reusable '+action,'request':req}
            first=(await runner.command(command,actor=a))['workflow']
            assert first['createdBy']==a and first['lastChange']=={'by':a,'action':action,'revision':1}
            assert (await runner.command(command,actor=b))['workflow']==first
    asyncio.run(exercise())


def test_real_specimen_cross_project_correction_export_and_reopen(tmp_path):
    # Real specimen, deterministic local algorithm, native correction with synthetic display authority.
    # No model invocation or biological validation; ordinary UI is a separate gate.
    import cv2,numpy as np
    from pathlib import Path
    from tests.bright_objects_reference import reference,canonical
    from orchestrator.workflow_execution import ScientificSteps,starter
    from orchestrator.scientific_edits import ViewEdits
    from orchestrator import scientific_resource_actions as actions
    pixels=cv2.imread(str(Path(__file__).resolve().parents[2]/'assets/examples/human_mitosis.png'),cv2.IMREAD_GRAYSCALE)
    assert pixels is not None
    w=service(tmp_path);d=w.create('Measure bright regions',starter(),'a'*32);retained=[]
    for number,data in enumerate((pixels,np.roll(pixels,1,axis=0))):
        s=native_input(w,'specimen'+str(number),data);driver=ScientificSteps(w)
        r=w.start(d.id,1,s.session,[identity(s)],str(number+1)*32,actor='studio' if number==0 else 'agent:researcher')
        c=w.claim(r.id,s.session);out=driver.execute(c);w.finish(c,out)
        result=w.store.read(out['output']['id'],s.session)
        row,native=driver.array(result,'instance-labels');values=native.read(tuple(slice(0,n) for n in native.shape))
        if number==0:
            expected=reference(data)
            oracle=expected['labels'] if isinstance(expected,dict) else expected[1]
            assert np.array_equal(canonical(values),canonical(oracle))
        review=w.claim(r.id,s.session);w.finish(review,driver.execute(review))
        if number==1:
            edits=ViewEdits(w.store,result.id,s.session,result.revision,row['reference'])
            frame=edits.present(view='test-correction',layer='labels',context={});edits.rendered(frame)
            edit=edits.begin(frame,max_written_bytes=values.nbytes)
            corrected=values.copy();corrected[corrected==1]=0
            import itertools
            for idx in itertools.product(*(range((n+c-1)//c) for n,c in zip(native.shape,native.chunks))):
                region=tuple(slice(i*c,min((i+1)*c,n)) for i,c,n in zip(idx,native.chunks,native.shape))
                edit.write(list(idx),corrected[region])
            result=edit.commit()
        w.review(r.id,s.session,review['attempt']['id'],identity(result),True,'b'*32)
        while (c:=w.claim(r.id,s.session)) is not None:w.finish(c,driver.execute(c))
        status=w.status(r.id,s.session);assert status['status']=='completed'
        retained.append(status)
    reopened=service(tmp_path)
    for run in retained:assert reopened.status(run['id'],run['session'])==run


@pytest.mark.parametrize('action',['review','resume'])
def test_command_wakes_driver_already_leaving_stale_snapshot(web,monkeypatch,action):
    import asyncio,threading
    from orchestrator.workflow_service import service as shared
    client,built=web;runner=shared(built.hub,built.day);w=runner.workflows;s=source(w,built.day.sid)
    g=graph();g['nodes'].append({'id':'next','name':'Next','kind':'review','needs':['check'],'settings':{}})
    d=w.create('Wakeup',g,'a'*32);r=w.start(d.id,1,s.session,[identity(s)],'b'*32,actor='studio')
    if action=='review':
        claim=w.claim(r.id,s.session);w.finish(claim,{'status':'awaiting-review','output':identity(s)})
    else:w.control(r.id,s.session,'pause')
    captured,released=threading.Event(),threading.Event();original=w.status;reads=0
    def stale_status(*args):
        nonlocal reads
        reads+=1;value=original(*args)
        if reads==2:
            captured.set();assert released.wait(60)
        return value
    monkeypatch.setattr(w,'status',stale_status)
    async def exercise():
        runner.launch(r.id,s.session);old=runner.tasks[(s.session,r.id)]
        assert await asyncio.to_thread(captured.wait,60)
        try:
            extra={'attempt':claim['attempt']['id'],'result':identity(s),'accept':True,'request':'c'*32} if action=='review' else {}
            await runner.command({'action':action,'run':r.id,'session':s.session,**extra},actor='studio')
        finally:released.set()
        await old;await asyncio.sleep(0)
        task=runner.tasks.get((s.session,r.id))
        assert task is not None and task is not old,'The accepted wakeup was lost as the old driver exited'
        await task
        assert len(w.attempts(r.id,s.session))==(2 if action=='review' else 1)
    asyncio.run(exercise())


def test_returned_research_evidence_does_not_expire_during_human_review(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from orchestrator import workflow_research as research
    w=service(tmp_path);efforts=research.Efforts(w.store)
    saved=efforts.create('s1',request='a'*32,title='Report',objective='Explain',criteria='Cite evidence',budget_usd=1,inputs=[])
    efforts.reserve(saved.id,'s1',request='req',specification={'provider':'gemini-google'},maximum_usd=.1)
    efforts.claim(saved.id,'s1','req');execution={'provider':'gemini-google','request':'known'}
    efforts.started(saved.id,'s1','req',execution)
    observation={'sealedEvidence':True,'providerStatus':'returned','evidence':{'retained':'test'}}
    efforts.checkpoint(saved.id,'s1','req',execution=execution,previous=None,observation=observation)
    binding={'id':saved.id,'request':'req','started':0,'seconds':1}
    with pytest.raises(InvalidProject,match='original research service'):
        research.collect(w,{'session':'s1'},binding)
    assert efforts.read(saved.id,'s1').document['state'][research.KEY]['tasks']['req']['status'] not in research.FINAL
    efforts.finish(saved.id,'s1','req',status='completed',result={'review':'Accepted exact retained evidence'},expected_observation=observation)
    assert research.collect(w,{'session':'s1'},binding)['status']=='awaiting-review'


def test_researcher_and_studio_invocations_retain_equivalent_definition_and_inputs(tmp_path):
    import asyncio
    from tests.test_research_scientific_measurement import setup,FakeHub,ResearchManager
    from orchestrator.research.hands import Hands
    from orchestrator.workflow_service import service as shared
    hub,s,input_,_=setup(tmp_path);hub.execution_day.elsewhere=lambda sid:hub.execution_day;hub.execution_day.local_only=False
    manager=ResearchManager(FakeHub(),root=tmp_path/'research');manager.hands=Hands(hub);manager.sessions[s.rid]=s
    async def exercise():
        runner=shared(hub,hub.execution_day);w=runner.workflows;definition=w.create('Inspect',graph(),'a'*32)
        body={'action':'start','workflow':definition.id,'revision':1,'inputs':[identity(input_)],'request':'b'*32}
        worker=await manager._request({'op':'run','rid':s.rid,'operation':'workflow','runId':s._attempt.id,'params':body})
        assert worker['ok'],worker
        first=worker['values']['run'];await runner.tasks[(input_.session,first['id'])]
        ui=await runner.command({**body,'session':input_.session,'request':'c'*32},actor='studio')
        second=ui['run'];await runner.tasks[(input_.session,second['id'])]
        a,b=(w.status(r['id'],input_.session) for r in (first,second))
        assert a['actor']=='researcher:'+s.rid and b['actor']=='studio'
        assert a['definition']==b['definition'] and a['inputs']==b['inputs']==[identity(input_)]
        assert a['attempts'][0]['output']==b['attempts'][0]['output']==identity(input_)
        assert a['status']==b['status']=='awaiting-review'
        await runner.close()
    asyncio.run(exercise())


def test_researcher_study_bridge_reads_evidence_and_admits_attributed_work_without_camera(tmp_path):
    import asyncio
    from tests.test_research_scientific_measurement import setup, FakeHub, ResearchManager
    from orchestrator.research.hands import Hands
    from orchestrator.research.studies import for_workspace
    from orchestrator.door.operations import Operations
    hub, researcher, input_, _ = setup(tmp_path)
    hub.execution_day.elsewhere = lambda sid: hub.execution_day
    hub.execution_day.local_only = False
    hub.agent_operations = Operations(tmp_path/'operations')
    manager = ResearchManager(FakeHub(), root=tmp_path/'research')
    manager.hands = Hands(hub)
    manager.sessions[researcher.rid] = researcher

    async def exercise():
        studies = for_workspace(hub, hub.execution_day)
        study = studies.create(input_.session, actor='studio', label='You', request='study-bridge-001',
            title='Inspect retained data', objective='Inspect exact evidence', criteria='Keep the original revision',
            budget_usd=0)
        researcher._attempt.origin['study'] = study.id
        definition = studies.workflows.workflows.create('Inspect', graph(), 'a'*32)
        async def call(action, **fields):
            return await manager._request({'op':'run', 'rid':researcher.rid, 'operation':'workflow',
                'runId':researcher._attempt.id, 'params':{'action':action, **fields}})
        listed = await call('study-list')
        assert listed['ok'] and listed['values']['studies'][0]['id'] == study.id
        evidence = await call('study-evidence', study=study.id, reference=identity(input_))
        assert evidence['ok'] and evidence['values']['document'] == input_.document
        reply = await call('study-run', study=study.id, request='experiment-bridge-001',
            workflow=definition.id, revision=1, inputs=[identity(input_)])
        assert reply['ok'], reply
        operation = reply['values']['operation']
        run_id = operation['execution']['id']
        await studies.workflows.tasks[(input_.session, run_id)]
        retained = await call('study-read', study=study.id)
        assert retained['ok'], retained
        row = retained['values']
        assert row['study']['evidenceReads'][0]['by'] == 'researcher:'+researcher.rid
        assert row['operations'][0]['by'] == 'researcher:'+researcher.rid
        assert row['operations'][0]['workflow']['actor'] == 'researcher:'+researcher.rid
        assert row['operations'][0]['workflow']['status'] == 'awaiting-review'
        assert row['operations'][0]['workflow']['attempts'][0]['output'] == identity(input_)
        denied = await call('study-read', study=study.id, session='foreign-project')
        assert not denied['ok']
        for action, fields in [('start', {}), ('capability', {}), ('study-run', {'study':'another-study'}),
                               ('study-claim', {'study':'another-study'})]:
            assert not (await call(action, **fields))['ok']
        await studies.workflows.close()
    asyncio.run(exercise())


def test_restarted_service_discloses_orphaned_attempts_without_replaying_them(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.workflow_service import WorkflowService
    w=service(tmp_path);saved=source(w);definition=w.create('Review',graph(),'a'*32)
    request={'action':'start','workflow':definition.id,'revision':1,'session':saved.session,
             'inputs':[identity(saved)],'request':'b'*32}
    run=w.start(definition.id,1,saved.session,[identity(saved)],'b'*32,actor='studio')
    claim=w.claim(run.id,saved.session)
    restarted=WorkflowService(SimpleNamespace(resources=SimpleNamespace(projects=w.store)),
        SimpleNamespace(elsewhere=lambda _:SimpleNamespace(local_only=False)))
    before=w.store.read(claim['attempt']['id'],saved.session)
    async def exercise():
        first=restarted.observed_status(run.id,saved.session)
        assert first['status']=='running' and first['executionObservation']['state']=='interrupted'
        for action in ('check','start'):
            body=request if action=='start' else {'action':action,'run':run.id,'session':saved.session}
            reply=await restarted.command(body,actor='studio')
            assert reply['run']['executionObservation']['state']=='interrupted'
            assert not restarted.tasks
        for action in ('resume','retry'):
            with pytest.raises(InvalidProject,match='Reconcile'):
                await restarted.command({'action':action,'run':run.id,'session':saved.session},actor='studio')
        assert w.store.read(claim['attempt']['id'],saved.session)==before
        stopped=await restarted.command({'action':'stop','run':run.id,'session':saved.session},actor='studio')
        assert stopped['run']['status']=='stopping'
        assert stopped['run']['executionObservation']['state']=='interrupted' and not restarted.tasks
        # A retained review requires no execution owner and must remain ordinary review.
        w.finish(claim,{'status':'awaiting-review','output':identity(saved)})
        assert restarted.observed_status(run.id,saved.session)['executionObservation']['state']=='idle'
        await restarted.close()
    asyncio.run(exercise())


def test_admitted_run_without_dispatch_needs_explicit_resume(tmp_path):
    import asyncio
    from types import SimpleNamespace
    from orchestrator.workflow_service import WorkflowService
    w=service(tmp_path);saved=source(w);definition=w.create('Review',graph(),'a'*32)
    run=w.start(definition.id,1,saved.session,[identity(saved)],'b'*32,actor='studio')
    restarted=WorkflowService(SimpleNamespace(resources=SimpleNamespace(projects=w.store)),
        SimpleNamespace(elsewhere=lambda _:SimpleNamespace(local_only=False)))
    async def exercise():
        first=await restarted.command({'action':'check','run':run.id,'session':saved.session},actor='studio')
        assert first['run']['executionObservation']['state']=='unstarted'
        assert first['run']['attempts']==[] and not restarted.tasks
        await restarted.command({'action':'resume','run':run.id,'session':saved.session},actor='studio')
        await asyncio.gather(*list(restarted.tasks.values()))
        after=restarted.observed_status(run.id,saved.session)
        assert after['status']=='awaiting-review' and len(after['attempts'])==1
        assert after['executionObservation']['state']=='idle'
        await restarted.close()
    asyncio.run(exercise())


def test_named_file_inputs_are_frozen_scoped_and_retry_bound(tmp_path):
    from orchestrator.workflows import run_identity
    w=service(tmp_path);control=source(w);training=source(w,title='Training');foreign=source(w,'other')
    g={'format':1,'input':{'kind':'source-file'},'nodes':[{'id':'fit','name':'Fit','kind':'tool','needs':['input','input:training'],
       'settings':{'capability':'response-fit','generation':'pinned','parameters':{},'mode':'automatic','bindings':{'control':'input','source':'input:training'}}}]}
    d=w.create('Two files',g,'a'*32)
    with pytest.raises(InvalidProject,match='bindings'):
        w.start(d.id,1,'s1',[identity(control)],'b'*32,actor='studio')
    with pytest.raises(InvalidProject):
        w.start(d.id,1,'s1',[identity(control)],'b'*32,actor='studio',bindings={'training':identity(foreign)})
    r=w.start(d.id,1,'s1',[identity(control)],'b'*32,actor='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa',bindings={'training':identity(training)})
    assert r.id==run_identity('s1','b'*32)
    assert w.status(r.id,'s1')['bindings']=={'training':identity(training)}
    claim=w.claim(r.id,'s1')
    assert claim['inputs']==[identity(control),identity(training)]
    assert w.status(r.id,'s1')['attempts'][0]['actor']=='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa'
    with pytest.raises(Conflict):
        w.start(d.id,1,'s1',[identity(control)],'b'*32,actor='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb',bindings={'training':identity(control)})


def test_continuation_attributes_each_actor_without_rewriting_original(tmp_path):
    w=service(tmp_path);s=source(w);g=graph();g['nodes'].append({'id':'next','name':'Next','kind':'review','needs':['check'],'settings':{}})
    d=w.create('Two reviewers',g,'a'*32);r=w.start(d.id,1,'s1',[identity(s)],'b'*32,actor='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa')
    first=w.claim(r.id,'s1');w.finish(first,{'status':'awaiting-review','output':identity(s)})
    w.review(r.id,'s1',first['attempt']['id'],identity(s),True,'c'*32,actor='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb')
    second=w.claim(r.id,'s1');status=w.status(r.id,'s1')
    assert status['actor']=='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa'
    assert status['attempts'][0]['actor']=='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa'
    assert status['attempts'][0]['reviewActor']=='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb'
    assert status['attempts'][1]['actor']=='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb'
    w.finish(second,{'status':'completed','output':identity(s)})
    derived=w.revise(r.id,'s1',first['attempt']['id'],identity(s),'d'*32,actor='studio')
    assert w.status(derived.id,'s1')['actor']=='studio'
    assert w.status(derived.id,'s1')['derivedFrom']['run']==r.id
    assert w.status(r.id,'s1')['actor']=='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa'
    followup=w.claim(derived.id,'s1')
    assert followup is not None
    assert w.status(derived.id,'s1')['attempts'][-1]['actor']=='studio'


def test_review_retry_repairs_actor_publication_gap_without_overwriting_later_control(tmp_path,monkeypatch):
    w=service(tmp_path);s=source(w);g=graph();g['nodes'].append({'id':'next','name':'Next','kind':'review','needs':['check'],'settings':{}})
    d=w.create('Review retry',g,'a'*32);r=w.start(d.id,1,'s1',[identity(s)],'b'*32,actor='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa')
    first=w.claim(r.id,'s1');w.finish(first,{'status':'awaiting-review','output':identity(s)})
    original=w.store.save
    def fail_run(id_,*args,**kw):
        if id_==r.id:raise OSError('lost run publication')
        return original(id_,*args,**kw)
    monkeypatch.setattr(w.store,'save',fail_run)
    with pytest.raises(OSError,match='publication'):
        w.review(r.id,'s1',first['attempt']['id'],identity(s),True,'c'*32,actor='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb')
    monkeypatch.setattr(w.store,'save',original)
    w.review(r.id,'s1',first['attempt']['id'],identity(s),True,'c'*32,actor='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa')
    assert w.status(r.id,'s1')['lastAction']['actor']=='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb'
    w.control(r.id,'s1','pause',actor='studio')
    w.review(r.id,'s1',first['attempt']['id'],identity(s),True,'c'*32,actor='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa')
    assert w.status(r.id,'s1')['lastAction']['actor']=='studio'
    assert w.status(r.id,'s1')['control']=='paused'


def test_manual_fit_and_workflow_share_catalog_execution_and_retained_output(web,tmp_path,monkeypatch):
    # Real tiny fitted computation/retention; runtime admission and process transport are stand-ins.
    import io,sys,numpy as np
    from pathlib import Path
    from orchestrator import matrix_capability_runs as matrix
    from orchestrator.workflow_execution import keep_file,ScientificSteps
    from scientific_compute.engine import OPERATIONS
    from scientific_compute import catalog as matrix_catalog
    client,built=web;runner=built.hub.workflow_service;w=runner.workflows
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT',str(tmp_path/'isolated-slot'))
    monkeypatch.setattr(matrix_catalog,'runtime',lambda *a,**k:{'prepared':True,'reason':'','backend':'cpu','owner':'matrix-cpu-runtime','evidenceClass':'synthetic-runtime-admission'})
    monkeypatch.setattr(matrix,'execution_runtime',lambda _:(Path(sys.executable),Path(sys.prefix)))
    dispatched=[]
    def compute(capability,sources,params,work,check,*runtime):
        output=work/'outputs';output.mkdir()
        resolved={}
        for port,source in sources.items():
            path=work/(port+'.npz');path.write_bytes(source.read(0,source.size));resolved[port]=path
        dispatched.append(capability)
        return OPERATIONS[capability](resolved,params,output,check)
    monkeypatch.setattr(matrix,'_compute',compute)
    monkeypatch.setattr(runner,'launch',lambda *args:None)
    data=io.BytesIO();np.savez(data,format=np.array('response-effects-v1'),effects=np.array([[2.,4.],[6.,8.]]),
        genes=np.array(['g1','g2']),contexts=np.array(['first','second']),targets=np.array(['target','target']))
    source_file=keep_file(w.store,built.day.sid,'e'*32,'effects.npz',data.getvalue())
    declared=matrix.descriptor('response-fit')
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'workflow'}
    body={'action':'capability','session':built.day.sid,'capability':'response-fit','generation':declared['generation'],
          'inputs':{'effects':identity(source_file)},'parameters':{'regularization':1.,'rank':0},'request':'f'*32}
    response=client.post('/api/workflows/command',json=body,headers=headers)
    assert response.status_code==200,response.text
    run=response.json()['run'];claim=w.claim(run['id'],built.day.sid)
    manual_definition=w.definition(run['definition']['id'],run['definition']['revision']).document['state']['workflow']
    assert manual_definition['createdBy']=='studio'
    assert manual_definition['lastChange']=={'by':'studio','action':'capability','revision':1}
    outcome=ScientificSteps(w,built.hub).execute(claim);w.finish(claim,outcome)
    status=w.status(run['id'],built.day.sid)
    assert status['status']=='completed' and len(status['attempts'])==1
    saved=w.store.read(outcome['output']['id'],built.day.sid)
    assert saved.document['state']['matrixOperation']['capability']=='response-fit'
    ref=saved.document['state']['sourceFile']['reference']
    output=w.store.file_source(saved.id,saved.session,saved.revision,ref)
    with np.load(io.BytesIO(output.read(0,output.size)),allow_pickle=False) as fitted:
        np.testing.assert_allclose(fitted['effects'],[[2.,3.]])
    retry=client.post('/api/workflows/command',json=body,headers=headers)
    assert retry.json()['run']['id']==run['id'] and dispatched==['response-fit']
    assert saved.document['state']['workflowOutput']['attempt']['id']==claim['attempt']['id']


def test_stop_retry_keeps_original_control_actor_and_observed_execution(tmp_path):
    w=service(tmp_path);s=source(w);d=w.create('Stop',graph(),'a'*32)
    r=w.start(d.id,1,'s1',[identity(s)],'b'*32,actor='agent:ac_aaaaaaaaaaaaaaaaaaaaaaaa')
    claim=w.claim(r.id,'s1')
    stopped=w.control(r.id,'s1','stop',actor='studio')
    assert stopped['status']=='stopping'
    assert w.control(r.id,'s1','stop',actor='agent:ac_bbbbbbbbbbbbbbbbbbbbbbbb')==stopped
    w.finish(claim,{'status':'completed','output':identity(s)})
    assert w.status(r.id,'s1')['status']=='stopped'
