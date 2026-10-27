"""Real retained storage and explicit recipe transitions; synthetic plugin work."""
import pytest
from orchestrator import scientific_recipes as recipes
from orchestrator.scientific_comparisons import identity, reference
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from tests.test_scientific_edits import scene
from tests.test_scientific_tools import member, KEY, GEN
from tests.test_scientific_outputs import attempt

PARAMETERS={'format':'scientific-tool-document-v1','schema':'test.settings','version':1,'state':{'threshold':3}}

@pytest.fixture
def configured(scene, member):
    store, _, output, worker = attempt(scene)
    try:
        output.capture(worker); output.publish()
        source=store.read(output.target,output.saved.session)
    finally:output.close()
    doc=source.document
    runtime={'bundle':KEY.split('#')[0],'generation':GEN,'operation':'viewer'}
    doc['runtimes'].append(runtime)
    view=doc['state']['scientificView'];view['runtime']=runtime
    view['tool']={'document':PARAMETERS,'recipe':PARAMETERS,'bindings':{'original':'output'}}
    doc['state']['hookeOutput']['base']=identity(source)
    doc['resources'].append(reference(source))
    template=store.save(source.id,source.session,doc,expected_revision=source.revision)
    return store,source,template


def test_create_prepare_review_resume_and_reopen(configured):
    store,source,template=configured
    definition=recipes.create(store,template.id,template.session,template.revision,'a'*32)
    assert recipes.create(store,template.id,template.session,template.revision,'a'*32)==definition
    definition=recipes.append(store,definition.id,definition.session,1,template.id,template.revision)
    run=recipes.begin(store,definition.id,definition.session,definition.revision,'b'*32)
    assert recipes.begin(store,definition.id,definition.session,definition.revision,'b'*32)==run
    working=recipes.prepare(store,run.id,run.session,1)
    assert recipes.prepare(store,run.id,run.session,1)==working
    state=working.document['state']['recipeRun']
    branch=recipes.exact(store,state['working'],run.session)
    assert branch.document['state']['scientificView']['recipeTool']==PARAMETERS
    assert 'tool' not in branch.document['state']['scientificView']
    assert recipes.candidates(store,run.id,run.session,working.revision)==[]
    with pytest.raises(InvalidProject,match='not produced'):
        recipes.accept(store,run.id,run.session,working.revision,template.id,template.revision)
    # Stand-in for a separately accepted output, retaining its exact producing base.
    doc=branch.document;doc['state']['hookeOutput']={'base':identity(branch)}
    doc['resources'].append(reference(branch))
    result=store.save(branch.id,run.session,doc,expected_revision=branch.revision)
    assert recipes.candidates(store,run.id,run.session,working.revision)[0]['result']==identity(result)
    accepted=recipes.accept(store,run.id,run.session,working.revision,result.id,result.revision)
    assert recipes.accept(store,run.id,run.session,working.revision,result.id,result.revision)==accepted
    reopened=ProjectStore(store.root)
    saved,state,_=recipes.run(reopened,run.id,run.session,accepted.revision)
    assert state['completed']==[{'working':identity(branch),'result':identity(result)}]
    assert state['working'] is None
    next_step=recipes.prepare(reopened,run.id,run.session,saved.revision)
    next_branch=recipes.exact(reopened,next_step.document['state']['recipeRun']['working'],run.session)
    assert next_branch.document['state']['hookeCopy']['history'][-1]['source']==identity(result)
    assert store.read(source.id,source.session,revision=source.revision)==source


def test_failed_preparation_keeps_accepted_run_and_retry(configured,monkeypatch):
    from orchestrator import scientific_tools
    store,source,template=configured
    definition=recipes.create(store,template.id,template.session,template.revision,'c'*32)
    run=recipes.begin(store,definition.id,definition.session,1,'d'*32)
    original=scientific_tools.fork
    def fail(*a,**kw):raise ValueError('Runtime needs preparation')
    monkeypatch.setattr(scientific_tools,'fork',fail)
    with pytest.raises(ValueError,match='preparation'):recipes.prepare(store,run.id,run.session,1)
    assert store.read(run.id,run.session)==run
    monkeypatch.setattr(scientific_tools,'fork',original)
    assert recipes.prepare(store,run.id,run.session,1).revision==2
    assert store.read(source.id,source.session,revision=source.revision)==source


def test_cancel_does_not_create_work_or_execute(configured):
    store,_,template=configured
    definition=recipes.create(store,template.id,template.session,template.revision,'e'*32)
    run=recipes.begin(store,definition.id,definition.session,1,'f'*32)
    before=store.list(run.session)
    def cancel():raise RuntimeError('Stopped')
    with pytest.raises(RuntimeError,match='Stopped'):recipes.prepare(store,run.id,run.session,1,check=cancel)
    assert store.list(run.session)==before


def test_recipe_refuses_unknown_version_and_changed_settings(configured):
    store,_,template=configured
    definition=recipes.create(store,template.id,template.session,template.revision,'1'*32)
    doc=definition.document;doc['state']['recipe']['steps'][0]['parameters']['state']['threshold']=True
    bad=store.save(definition.id,definition.session,doc,expected_revision=1)
    with pytest.raises(InvalidProject,match='differ'):recipes.begin(store,bad.id,bad.session,bad.revision,'2'*32)
    assert store.read(template.id,template.session)==template

def other_input(store,source,template):
    """A second saved output of the same labels: another exact input in the same Project."""
    from orchestrator.scientific_outputs import OutputSave
    from tests.test_scientific_outputs import Worker
    labels=next(row for row in store.list(source.session) if row.document['title']=='Editable labels')
    output=OutputSave(store,labels,'request-2234567890123','c'*32,{'semantic':'instance-labels','axes':['z','x']},producer={'kind':'test'})
    try:
        output.capture(Worker());output.publish();other=store.read(output.target,labels.session)
    finally:output.close()
    rows=[row['input'] for row in recipes.inputs(store,source.session)]
    assert identity(template) in rows and identity(other) in rows
    return identity(other)


def produced(store,branch):
    """Stand-in for a separately accepted output, retaining its exact producing base."""
    doc=branch.document;doc['state']['hookeOutput']={'base':identity(branch)}
    doc['resources'].append(reference(branch))
    return store.save(branch.id,branch.session,doc,expected_revision=branch.revision)


def test_run_on_another_input_keeps_exact_identities(configured):
    store,source,template=configured
    definition=recipes.create(store,template.id,template.session,template.revision,'3'*32)
    other=other_input(store,source,template)
    before=store.read(other['id'],other['session'],revision=other['revision'])
    run=recipes.begin(store,definition.id,definition.session,1,'4'*32,other)
    assert recipes.begin(store,definition.id,definition.session,1,'4'*32,other)==run
    original=recipes.begin(store,definition.id,definition.session,1,'4'*32)
    assert original.id!=run.id and original.document['state']['recipeRun']['input']==identity(source)
    state=run.document['state']['recipeRun']
    assert state=={'format':recipes.RUN_FORMAT,'recipe':identity(definition),'input':other,'completed':[],'working':None,'attempts':[]}
    assert reference(before) in run.document['resources']
    assert run.document['title'].startswith('Run on '+before.document['title']+' · Recipe')
    row=next(row for row in recipes.inputs(store,run.session) if row['input']==other)
    assert row['layers']==len(before.document['state']['scientificView']['layers']) and type(row['tool']) is str and row['tool']
    assert all(type(t['tool']) is str and t['tool'] for t in recipes.templates(store,run.session))
    working=recipes.prepare(store,run.id,run.session,1)
    branch=recipes.exact(store,working.document['state']['recipeRun']['working'],run.session)
    assert branch.document['state']['hookeCopy']['source']==other
    assert branch.document['state']['scientificView']['recipeTool']==PARAMETERS
    assert store.read(other['id'],other['session'],revision=other['revision'])==before
    with pytest.raises(InvalidProject,match='changed'):
        recipes.begin(store,definition.id,definition.session,1,'5'*32,{**other,'digest':'0'*64})
    assert recipes.inputs(store,run.session)==[row for row in recipes.inputs(store,run.session) if row['input']['id']!=branch.id]


def test_failed_attempt_is_kept_and_retry_has_its_own_copy(configured):
    store,_,template=configured
    definition=recipes.create(store,template.id,template.session,template.revision,'6'*32)
    run=recipes.begin(store,definition.id,definition.session,1,'7'*32)
    with pytest.raises(InvalidProject,match='Prepare this step'):recipes.fail(store,run.id,run.session,1,'')
    prepared=recipes.prepare(store,run.id,run.session,1)
    first=prepared.document['state']['recipeRun']['working']
    with pytest.raises(InvalidProject,match='500'):recipes.fail(store,run.id,run.session,prepared.revision,'x'*501)
    failed=recipes.fail(store,run.id,run.session,prepared.revision,' Dimensions did not match ')
    assert recipes.fail(store,run.id,run.session,prepared.revision,' Dimensions did not match ')==failed
    state=failed.document['state']['recipeRun']
    assert state['working'] is None and state['completed']==[]
    assert state['attempts']==[{'step':0,'working':first,'outcome':'failed','note':'Dimensions did not match'}]
    kept=recipes.exact(store,first,run.session)
    again=recipes.prepare(store,run.id,run.session,failed.revision)
    second=again.document['state']['recipeRun']['working']
    assert second['id']!=first['id']
    assert recipes.prepare(store,run.id,run.session,failed.revision)==again
    late=produced(store,kept)
    with pytest.raises(InvalidProject,match='not produced'):
        recipes.accept(store,run.id,run.session,again.revision,late.id,late.revision)
    result=produced(store,recipes.exact(store,second,run.session))
    accepted=recipes.accept(store,run.id,run.session,again.revision,result.id,result.revision)
    _,reopened,_=recipes.run(ProjectStore(store.root),run.id,run.session,accepted.revision)
    assert reopened['attempts']==state['attempts'] and reopened['completed']==[{'working':second,'result':identity(result)}]
    assert store.read(run.id,run.session,revision=failed.revision)==failed


def test_earlier_run_format_reads_and_upgrades_only_on_its_next_save(configured):
    store,source,template=configured
    definition=recipes.create(store,template.id,template.session,template.revision,'8'*32)
    doc={'kind':'scientific-recipe-run','title':'Run · earlier','resources':[reference(definition),reference(source)],
         'runtimes':definition.document['runtimes'],
         'state':{'recipeRun':{'format':'scientific-recipe-run-v1','recipe':identity(definition),'completed':[],'working':None}}}
    earlier=store.create(template.session,doc)
    _,state,_=recipes.run(store,earlier.id,earlier.session,1)
    assert state['input']==identity(source) and state['attempts']==[]
    prepared=recipes.prepare(store,earlier.id,earlier.session,1)
    assert prepared.document['state']['recipeRun']['format']==recipes.RUN_FORMAT
    assert store.read(earlier.id,earlier.session,revision=1)==earlier
    unknown=dict(doc,state={'recipeRun':{**doc['state']['recipeRun'],'format':'scientific-recipe-run-v9'}})
    future=store.create(template.session,unknown)
    with pytest.raises(InvalidProject,match='unsupported'):recipes.run(store,future.id,future.session,1)


from tests.test_scientific_project_api import web, ORIGIN  # noqa: E402,F401

def test_http_recipe_actions_require_origin_and_initiating_project(web):
    from tests.test_scientific_project_review import saved_view
    from tests.test_scientific_outputs import Worker
    from orchestrator.scientific_outputs import OutputSave
    client,built=web;store=built.hub.resources.projects
    source,_,_=saved_view(store,session='2026-10-19')
    output=OutputSave(store,source,'request-1234567890123','a'*32,{'semantic':'instance-labels','axes':['z','x']})
    try:
        output.capture(Worker());output.publish();saved=store.read(output.target,source.session)
    finally:output.close()
    doc=saved.document;doc['state']['scientificView']['tool']={'document':PARAMETERS,'recipe':PARAMETERS,'bindings':{'original':'output'}}
    saved=store.save(saved.id,saved.session,doc,expected_revision=1)
    path=f'/api/scientific-projects/{saved.id}/recipe'
    payload={'session':saved.session,'revision':saved.revision,'action':'create','request':'7'*32}
    headers={'Origin':ORIGIN,'X-MicroscopeMind':'scientific-recipe'}
    assert client.post(path,json=payload).status_code==403
    built.day.sid='2026-10-18'
    assert client.post(path,json=payload,headers=headers).status_code==409
    built.day.sid=saved.session
    first=client.post(path,json=payload,headers=headers)
    assert first.status_code==200,first.text
    assert client.post(path,json=payload,headers=headers).json()==first.json()
    assert store.read(saved.id,saved.session)==saved
    rows=client.get('/api/scientific-recipes/inputs',params={'session':saved.session}).json()
    assert rows['ok'] and identity(saved) in [row['input'] for row in rows['inputs']]
    definition=first.json()['project']
    run=client.post(f'/api/scientific-projects/{definition["id"]}/recipe',headers=headers,json={'session':saved.session,'revision':1,'action':'begin','request':'8'*32,'input':identity(saved)})
    assert run.status_code==200,run.text
    assert run.json()['project']['document']['state']['recipeRun']['input']==identity(saved)
    nothing=client.post(f'/api/scientific-projects/{run.json()["project"]["id"]}/recipe',headers=headers,json={'session':saved.session,'revision':1,'action':'fail','note':'x'})
    assert nothing.status_code>=400 and 'Prepare this step' in nothing.text
