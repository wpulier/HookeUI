"""Real Project stores and connector transport with synthetic owner consent; no live-agent claim."""
from types import SimpleNamespace
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from orchestrator.day.store import SessionStore
from orchestrator.door import api
from orchestrator.door.projects import access, ProjectAccess
from orchestrator.origin import studio_origin
from orchestrator.scientific_projects import ProjectStore
from tests.test_agent_door import connect, bearer, BASE, HEADER, STUDIO, window_key
STUDIO = {**STUDIO, "X-Hooke-Studio": "synthetic-owner-window"}
from tests.waiting import until
from tests.test_scientific_project_api import web  # noqa: F401

@pytest.fixture
def project_door(tmp_path, monkeypatch):
    monkeypatch.setenv("HOOKE_STUDIO_TOKEN", "synthetic-owner-window")
    day = SessionStore(tmp_path/'sessions')
    store = ProjectStore(tmp_path/'documents')
    built = SimpleNamespace(paths=SimpleNamespace(home=tmp_path), day=day,
        hub=SimpleNamespace(clients=set(), resources=SimpleNamespace(projects=store)))
    app = FastAPI(); api.register(app, built, studio_origin, HEADER)
    with TestClient(app, base_url=BASE) as client:
        yield client, built

def run(client, headers, tool, args, key, project=None):
    response = client.post('/api/agent/v1/operations', json={'tool':tool, 'args':args,
        'requestKey':key, **({'project':project} if project else {})}, headers=headers)
    assert response.status_code in (200, 202), response.text
    op = response.json()['operation']
    def ended():
        result = client.get('/api/agent/v1/operations/'+op['id'], headers=headers).json()['operation']
        return result if result['state'] in ('succeeded','failed','interrupted','cancelled') else None
    result = until(ended)
    assert result['state']=='succeeded', result
    return result

def test_detached_project_creation_is_retained_without_switching(project_door):
    client,built=project_door
    c=connect(client); h=bearer(c['token'])
    original=built.day.sid; built.day.on_stage='existing-specimen'
    result=run(client,h,'project.create',{'name':'New research'},'new-project-0001')
    sid=result['outcome']['project']['session']
    assert sid != original and built.day.sid == original and built.day.on_stage == 'existing-specimen'
    assert built.day.elsewhere(sid).name == 'New research' and built.day.elsewhere(sid).local_only
    again=run(client,h,'project.create',{'name':'New research'},'new-project-0001')
    assert again['id']==result['id']
    assert SessionStore(built.day.root,session=sid).name=='New research'
    rows=run(client,h,'project.list',{},'list-projects-001')['outcome']['projects']
    assert [r['session'] for r in rows]==[sid]
    assert access(built).rows()[sid]['by']=='agent:'+c['connection']['id']


def test_workflow_discovery_uses_shared_contract_and_default_deny(project_door, monkeypatch):
    from orchestrator.door import listing, schema
    from orchestrator import agent_runnable
    client, _ = project_door
    monkeypatch.setattr(listing, 'gather', lambda built: [])
    h = bearer(connect(client)['token'])
    response = client.get('/api/agent/v1/tools?q=workflow', headers=h)
    assert response.status_code == 200
    rows = response.json()['tools']
    assert len(rows) == 1
    row = rows[0]
    assert row['id'] == 'workflow' and row['state'] == 'available'
    schema.check(row, 'capability')
    assert row['inputSchema'] == schema.load('workflow-admission')
    valid = {'study':'p'+'a'*32, 'session':'2026-10-28', 'workflow':'p'+'b'*32,
        'revision':1, 'inputs':[{'id':'p'+'c'*32,'session':'2026-10-28','revision':1,'digest':'d'*64}]}
    schema.validate(valid, row['inputSchema'])
    with pytest.raises(ValueError):
        schema.validate({**valid, 'inputs':[]}, row['inputSchema'])
    with pytest.raises(ValueError):
        schema.validate({**valid, 'revision':True}, row['inputSchema'])
    monkeypatch.delitem(agent_runnable.DOOR_RESOURCES, 'workflow.write')
    assert client.get('/api/agent/v1/tools?q=workflow', headers=h).json()['tools'] == []
    assert client.post('/api/agent/v1/operations', headers=h,
        json={'tool':'workflow','args':valid,'requestKey':'cannot-admit'}).status_code == 403

def test_project_grants_read_contribute_and_revoke_without_rewriting_history(project_door,monkeypatch):
    client,built=project_door
    a=connect(client); b=connect(client,label='Reviewer',secret='another-real-fixture-secret')
    ha,hb=bearer(a['token']),bearer(b['token'])
    original=run(client,ha,'project.create',{'name':'Shared study'},'shared-project-001')
    sid=original['outcome']['project']['session']
    def attempt(headers):
        return client.post('/api/agent/v1/operations',json={'tool':'project.read','args':{},'project':sid,
            'requestKey':'read-shared-project'},headers=headers)
    assert attempt(hb).status_code==403
    change={'session':sid,'connection':b['connection']['id'],'role':'read'}
    assert client.post('/api/agent-project-access',json=change,headers=hb).status_code==403
    assert client.post('/api/agent-project-access',json=change,headers=STUDIO).status_code==403
    monkeypatch.delenv('HOOKE_CONFIRM_KEY'); monkeypatch.setenv('HOOKE_DEV_CONFIRM','1')
    assert client.post('/api/agent-project-access',json=change,headers=STUDIO).status_code==200
    assert run(client,hb,'project.read',{},'read-shared-project',sid)['outcome']['project']['role']=='read'
    fields={'session':sid,'requestKey':'create-study-001','title':'Compare','objective':'Measure',
            'criteria':'Retain evidence','budgetUsd':0,'inputs':[]}
    assert client.post('/api/agent/v1/studies',json=fields,headers=hb).status_code==403
    assert client.post('/api/agent-project-access',json={**change,'role':'contribute'},headers=STUDIO).status_code==200
    created=client.post('/api/agent/v1/studies',json=fields,headers=hb)
    assert created.status_code==200,created.text
    study=created.json()['study']
    assert study['createdBy']['by']=='agent:'+b['connection']['id']
    assert client.post('/api/agent-project-access',json={**change,'role':None},headers=STUDIO).status_code==200
    assert attempt(hb).status_code==403
    assert client.get(f"/api/agent/v1/studies/{study['id']}?session={sid}",headers=hb).status_code==403
    kept=client.get(f"/api/agent/v1/studies/{study['id']}?session={sid}",headers=ha)
    assert kept.status_code==200 and kept.json()['study']['createdBy']==study['createdBy']
    assert [r['role'] for r in access(built).rows()[sid]['history']]==['read','contribute',None]

def test_corrupt_registry_refuses_without_overwrite(tmp_path):
    registry=ProjectAccess(tmp_path); registry.path.parent.mkdir(parents=True)
    registry.path.write_text('{broken')
    with pytest.raises(Exception,match='permissions could not be read'):
        registry.remember('2026-10-28','agent:ac_'+'a'*24)
    assert registry.path.read_text()=='{broken'


def test_import_read_and_download_retain_full_source_and_scope(project_door,tmp_path):
    import hashlib
    client,built=project_door
    a=connect(client); h=bearer(a['token'])
    b=connect(client,label='Unrelated',secret='unrelated-fixture-secret-0001'); hb=bearer(b['token'])
    sid=run(client,h,'project.create',{'name':'Corpus'},'corpus-project-01')['outcome']['project']['session']
    content=b'Full retained research context, including failures.\n'*2000
    source=tmp_path/'protocol.md'; source.write_bytes(content)
    result=run(client,h,'project.import',{'file':str(source)},'corpus-import-001',sid)
    ref=result['outcome']['artifact']
    assert result['outcome']['source']['sha256']==hashlib.sha256(content).hexdigest()
    assert built.day.sid != sid  # import never navigated the workspace
    first=run(client,h,'artifact.read',{'reference':ref},'corpus-read-001',sid)['outcome']
    assert first['document']['state']['sourceImport']['producer']=={
        'kind':'agent-import','actor':'agent:'+a['connection']['id'],
        'label':result['byLabel'],'operation':result['id']}
    from orchestrator.resources import Index, maker_of
    saved = built.hub.resources.projects.read(ref['id'], sid, revision=ref['revision'])
    indexed = Index.project_entry(saved)
    assert indexed.made_by == {'actor':'agent:'+a['connection']['id'], 'operation':result['id']}
    assert maker_of(indexed.kind, indexed.id, indexed.made_by) == {
        'kind':'agent', 'id':a['connection']['id'], 'name':result['byLabel']}
    assert first['content']['text']==content[:65536].decode()
    assert first['content']['nextOffset']==65536
    rest=run(client,h,'artifact.read',{'reference':ref,'offset':65536},'corpus-read-002',sid)['outcome']
    assert first['content']['text']+rest['content']['text']==content.decode()
    route='/api/agent/v1/'+first['download']
    assert client.get(route,headers=h).content==content
    assert client.get(route,headers=hb).status_code==403
    source.write_text('Changed after intake')
    retried=run(client,h,'project.import',{'file':str(source)},'corpus-import-001',sid)
    assert retried['id']==result['id']
    assert client.get(route,headers=h).content==content
    assert client.get('/api/agent/v1/operations/'+result['id'],headers=hb).status_code==403


def test_workflow_request_keys_are_scoped_to_actor_and_project(project_door):
    from tests.test_workflows import graph
    client,built=project_door
    a=connect(client); b=connect(client,label='Other',secret='other-workflow-client-secret')
    ha,hb=bearer(a['token']),bearer(b['token'])
    pa=run(client,ha,'project.create',{'name':'A'},'project-a')['outcome']['project']['session']
    pb=run(client,hb,'project.create',{'name':'B'},'project-b')['outcome']['project']['session']
    command={'action':'create','name':'Inspect','graph':graph(),'request':'c'*32}
    def create(headers,project):
        response=client.post('/api/agent/v1/workflows/command',json={**command,'project':project},headers=headers)
        assert response.status_code==200,response.text
        return response.json()['workflow']
    wa,wb=create(ha,pa),create(hb,pb)
    assert wa['id']!=wb['id']
    assert create(ha,pa)==wa
    assert client.get(f"/api/agent/v1/workflows/{wa['id']}?session={pb}",headers=hb).status_code==403
    assert client.get(f'/api/agent/v1/workflows?session={pb}',headers=hb).json()['workflows'][0]['id']==wb['id']


def test_registry_size_refusal_preserves_previous_permissions(tmp_path,monkeypatch):
    from orchestrator.door import projects
    registry=ProjectAccess(tmp_path)
    registry.remember('2026-10-28','agent:ac_'+'a'*24)
    original=registry.path.read_bytes()
    monkeypatch.setattr(projects,'_LIMIT',len(original)+10)
    with pytest.raises(Exception,match='storage is full'):
        registry.grant('2026-10-28','agent:ac_'+'b'*24,'read',authority='development')
    assert registry.path.read_bytes()==original


def test_unattributed_documents_remain_readable_in_resource_context(web):
    client, built = web
    index = built.hub.resources
    saved = index.projects.create(built.day.sid, {
        'kind':'text-file', 'title':'Existing protocol', 'state':{'text':'Retained before agent intake'},
        'resources':[], 'runtimes':[]})
    entry = index.get(saved.id, built.day.sid)
    assert entry.made_by == {} and entry.maker is None
    assert 'Existing protocol' in index.read_full(saved.id, built.day.sid)
    assert index.chronology(entry)['producer'] is None
    assert any(row['kind'] == 'project' for row in index.summary(built.day.sid)['kinds'])
