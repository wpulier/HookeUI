"""Synthetic labels through real accepted storage and shared built-in admission."""
import asyncio
import threading
from types import SimpleNamespace

import pytest
from orchestrator import scientific_resource_actions as actions
from orchestrator.scientific_projects import Conflict, InvalidProject
from tests.test_scientific_resource_actions import labels
from tests.test_scientific_project_api import web  # noqa: F401


def environment(tmp_path):
    store, saved, data = labels(tmp_path)
    return SimpleNamespace(resources=SimpleNamespace(projects=store)), SimpleNamespace(sid='s1'), saved


def test_shared_operation_retains_caller_exact_input_and_reuses_completed_publication(tmp_path):
    from orchestrator import scientific_measurement as capability
    hub, day, source = environment(tmp_path)
    selection = {'project': source.id, 'session': 's1', 'revision': source.revision, 'input': 'labels'}
    async def run():
        first = await capability.run(hub, day, capability.OPERATION, selection, caller='studio')
        again = await capability.run(hub, day, capability.OPERATION, selection, caller='agent:chat')
        return first, again
    first, again = asyncio.run(run())
    assert first['project'] == again['project'] and again['reused'] and not first['reused']
    assert first['producer'] == again['producer']
    assert first['producer']['admission']['caller'] == 'studio'
    assert again['invocation']['caller'] == 'agent:chat'
    assert first['invocation']['id'] != again['invocation']['id']
    assert first['producer']['source']['digest'] == source.digest
    assert first['producer']['operation'] == capability.OPERATION
    table = actions.table(hub.resources.projects, source.id, 's1', first['project'].revision)
    assert table['rows'] == [['7', 2, 1., 1.], [str(2**63+7), 2, 0., 1.5]]
    assert table['source']['revision'] == source.revision


@pytest.mark.parametrize('change', ['operation', 'session', 'revision', 'input', 'cancelled'])
def test_refused_admission_never_reads_pixels_or_changes_accepted_revision(tmp_path, monkeypatch, change):
    from orchestrator import scientific_measurement as capability
    hub, day, source = environment(tmp_path)
    selection = {'project': source.id, 'session': 's1', 'revision': source.revision, 'input': 'labels'}
    operation = capability.OPERATION
    stop = threading.Event()
    if change == 'operation': operation = 'other'
    if change == 'session': selection['session'] = 'other'
    if change == 'revision': selection['revision'] = True
    if change == 'input': selection['input'] = 'missing'
    if change == 'cancelled': stop.set()
    monkeypatch.setattr(actions, 'blocks', lambda *a: pytest.fail('pixels read before admission'))
    with pytest.raises(ValueError):
        asyncio.run(capability.run(hub, day, operation, selection, caller='studio', stopping=stop))
    assert hub.resources.projects.read(source.id, 's1') == source


def test_session_change_during_measurement_refuses_publication(tmp_path, monkeypatch):
    from orchestrator import scientific_measurement as capability
    hub, day, source = environment(tmp_path)
    real_blocks = actions.blocks
    def changed(*args):
        for block in real_blocks(*args):
            day.sid = 'another'
            yield block
    monkeypatch.setattr(actions, 'blocks', changed)
    with pytest.raises(ValueError, match='Project changed'):
        asyncio.run(capability.run(hub, day, capability.OPERATION,
            {'project': source.id, 'session': 's1', 'revision': source.revision, 'input': 'labels'}, caller='studio'))
    assert hub.resources.projects.read(source.id, 's1') == source


def test_intervening_revision_refuses_without_reusing_an_older_table(tmp_path):
    from orchestrator import scientific_measurement as capability
    hub, day, source = environment(tmp_path)
    selection = {'project': source.id, 'session': 's1', 'revision': source.revision, 'input': 'labels'}
    measured = asyncio.run(capability.run(hub, day, capability.OPERATION, selection, caller='studio'))['project']
    document = measured.document
    document['state']['note'] = 'other accepted work'
    newest = hub.resources.projects.save(source.id, 's1', document, expected_revision=measured.revision)
    with pytest.raises(Conflict):
        asyncio.run(capability.run(hub, day, capability.OPERATION, selection, caller='agent:chat'))
    assert hub.resources.projects.read(source.id, 's1') == newest


@pytest.mark.parametrize('first_caller',['agent','studio'])
def test_registry_and_http_use_identical_declaration_native_contract_and_readiness(web,first_caller):
    import numpy as np
    from orchestrator import scientific_measurement as capability
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.scientific import add_scientific_tools
    from tests.test_scientific_arrays import fill
    from tests.test_scientific_project_api import ORIGIN
    client, built = web
    store = built.hub.resources.projects
    saved = store.create(built.day.sid, {'kind':'scene','title':'Accepted labels','state':{},'resources':[],'runtimes':[]})
    data = np.array([[0,7],[7,7]], dtype='u1')
    draft = store.array_draft(saved.id, saved.session, component='array.labels', shape=data.shape, dtype='|u1',
        axes=['y','x'], chunks=[2,2], scientific={'semantic':'instance-labels'}, max_written_bytes=4)
    ref = fill(draft,data)
    document = saved.document
    document['resources'] = [ref]
    document['state']['scientificView'] = {'inputs':{'labels':ref}}
    saved = store.save(saved.id,saved.session,document,expected_revision=1)
    selection = {'project':saved.id,'session':saved.session,'revision':saved.revision,'input':'labels'}
    registry = Registry()
    add_scientific_tools(registry, SimpleNamespace(hub=built.hub,day=built.day))
    async def agent(action):
        ctx = ToolContext('chat',1,None,asked=built.day.here())
        call = registry.start('measure_saved_objects', {'action':action,'operation':capability.OPERATION,**{k:v for k,v in selection.items() if k!='session'}},ctx)
        await call.task
        assert call.ok, (call.summary,call.error)
        return call
    preview = asyncio.run(agent('describe')).values
    listing = client.get(f'/api/scientific-projects/{saved.id}/arrays',params={'session':saved.session,'revision':saved.revision}).json()
    assert listing['operation'] == preview['operation'] == capability.descriptor()
    assert listing['arrays'][0]['measurementPlan'] == preview['readiness']
    def studio():
        response = client.post(f'/api/scientific-projects/{saved.id}/measure',
            headers={'Origin':ORIGIN,'X-MicroscopeMind':'measure-scientific-objects'},
            json={k:v for k,v in selection.items() if k!='project'} | {'operation':capability.OPERATION})
        assert response.status_code == 200, response.text
        return response.json()
    if first_caller=='agent':
        measured=asyncio.run(agent('run'))
        result=studio()
    else:
        result=studio()
        measured=asyncio.run(agent('run'))
    assert result['reused'] == (first_caller=='agent')
    assert measured.values['reused'] == (first_caller=='studio')
    assert result['producer'] == measured.values['producer']
    assert measured.resource['id']==saved.id and measured.resource['version']==saved.revision+1
    assert result['producer']['admission']['caller'] == ('agent:chat' if first_caller=='agent' else 'studio')
    assert result['project']['revision'] == measured.values['output']['revision'] == saved.revision+1
    assert actions.table(store,saved.id,saved.session,saved.revision+1)['rows'] == [['7',3,2/3,2/3]]


@pytest.mark.parametrize('reason', ['cancel', 'session-ticket', 'memory'])
def test_shared_admission_enforces_ticket_and_memory_before_work(tmp_path, monkeypatch, reason):
    from orchestrator import scientific_measurement as capability
    from orchestrator.capture import captures, Refused
    from orchestrator.ticket import Generations, Ticket, using
    hub, day, source = environment(tmp_path)
    ticket = Ticket(Generations(), 'command')
    if reason == 'cancel': ticket.cancel('user stopped')
    if reason == 'session-ticket': ticket.generations.bump('session')
    if reason == 'memory':
        def no_room(*a): raise Refused('no room')
        monkeypatch.setattr(captures(hub).memory,'reserve',no_room)
    monkeypatch.setattr(actions,'measure',lambda *a,**k:pytest.fail('unadmitted work'))
    with using(ticket), pytest.raises((ValueError,Refused)):
        asyncio.run(capability.run(hub,day,capability.OPERATION,
            {'project':source.id,'session':'s1','revision':source.revision,'input':'labels'},caller='agent:chat'))
    assert hub.resources.projects.read(source.id,'s1') == source


def test_task_cancellation_waits_for_owned_work_and_preserves_original(tmp_path, monkeypatch):
    from orchestrator import scientific_measurement as capability
    from tests.waiting import until_async, until
    hub, day, source = environment(tmp_path)
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    stopping = threading.Event()
    real_blocks = actions.blocks
    def paused(*args):
        try:
            entered.set()
            until(release.is_set)
            yield from real_blocks(*args)
        finally: finished.set()
    monkeypatch.setattr(actions,'blocks',paused)
    async def scenario():
        task=asyncio.create_task(capability.run(hub,day,capability.OPERATION,
            {'project':source.id,'session':'s1','revision':source.revision,'input':'labels'},caller='studio',stopping=stopping))
        try:
            await until_async(entered.is_set)
            task.cancel()
            await until_async(stopping.is_set)
        finally: release.set()
        with pytest.raises(asyncio.CancelledError): await task
        assert finished.is_set()
    asyncio.run(scenario())
    assert hub.resources.projects.read(source.id,'s1') == source


@pytest.mark.parametrize('case', ['missing-owner','stale-owner','foreign-session','undeclared-operation'])
def test_agent_refuses_unowned_or_implicit_selection(web,monkeypatch,case):
    from orchestrator import scientific_measurement as capability
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.scientific import add_scientific_tools
    client,built=web
    registry=Registry();add_scientific_tools(registry,SimpleNamespace(hub=built.hub,day=built.day))
    asked=built.day.here()
    if case=='stale-owner':
        from dataclasses import replace
        asked=replace(asked,stale=True)
    if case=='missing-owner': asked=None
    args={'action':'run','operation':capability.OPERATION,'project':'p'+'a'*32,'session':built.day.sid,'revision':2,'input':'labels'}
    if case=='foreign-session':args['session']='elsewhere'
    if case=='undeclared-operation':args['operation']='other'
    monkeypatch.setattr(actions,'measure',lambda *a,**k:pytest.fail('unadmitted native work'))
    async def call():
        run=registry.start('measure_saved_objects',args,ToolContext('chat',1,None,asked=asked))
        await run.task
        assert run.ok is False and not run.values and run.error is None
    asyncio.run(call())


def test_concurrent_callers_have_one_atomic_winner_and_no_false_receipt(tmp_path,monkeypatch):
    from orchestrator import scientific_measurement as capability
    from tests.waiting import until_async, until
    hub,day,source=environment(tmp_path)
    arrived=[];release=threading.Event();lock=threading.Lock()
    real_measure=actions.measure
    def simultaneous(*args,**kwargs):
        with lock: arrived.append(kwargs['admission']['caller'])
        until(release.is_set)
        return real_measure(*args,**kwargs)
    monkeypatch.setattr(actions,'measure',simultaneous)
    selection={'project':source.id,'session':'s1','revision':source.revision,'input':'labels'}
    async def run():
        tasks=[asyncio.create_task(capability.run(hub,day,capability.OPERATION,selection,caller=caller))
               for caller in ('studio','agent:chat')]
        try: await until_async(lambda:len(arrived)==2)
        finally: release.set()
        return await asyncio.gather(*tasks,return_exceptions=True)
    results=asyncio.run(run())
    good=[r for r in results if isinstance(r,dict)]
    assert len(good)==1 and sum(isinstance(r,Conflict) for r in results)==1
    accepted=hub.resources.projects.read(source.id,'s1')
    assert accepted.revision==source.revision+1 and good[0]['project']==accepted
    assert good[0]['producer']['admission']['caller'] in arrived
    monkeypatch.setattr(actions,'measure',lambda *a,**k:pytest.fail('completed retry executed again'))
    retry=asyncio.run(capability.run(hub,day,capability.OPERATION,selection,caller='studio'))
    assert retry['reused'] and retry['producer']==good[0]['producer']


def test_builtin_is_discoverable_through_the_same_catalogue_and_tools_schema():
    from orchestrator import capabilities, scientific_measurement as capability
    from orchestrator.brain.toolbox import catalogue
    tool=catalogue().tools['measure_saved_objects']
    declared=next(row for row in capabilities.load()['capabilities'] if row['id']=='measure-saved-objects')
    assert declared['how']['tool']==tool.name
    assert tool.params['properties']['operation']['enum']==[capability.descriptor()['id']]
    assert set(tool.params['required'])=={'action','operation','project','revision','input'}
    assert 'session' not in tool.params['properties']


def test_copied_producer_metadata_alone_is_not_a_completed_operation_receipt(tmp_path,monkeypatch):
    from orchestrator import scientific_measurement as capability, scientific_tables as tables
    hub,day,source=environment(tmp_path)
    original=tables.publish
    def ordinary_publication(*args,**kwargs):
        kwargs.pop('transaction',None)
        return original(*args,**kwargs)
    monkeypatch.setattr(tables,'publish',ordinary_publication)
    copied=actions.measure(hub.resources.projects,source.id,'s1',source.revision,'labels',
                           admission={'id':'a'*32,'caller':'studio'})
    assert copied.transaction is None
    with pytest.raises(Conflict):
        asyncio.run(capability.run(hub,day,capability.OPERATION,
            {'project':source.id,'session':'s1','revision':source.revision,'input':'labels'},caller='studio'))
