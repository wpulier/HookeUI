"""Authenticated HTTP and real Study storage. Synthetic clients; no family qualification claim."""
from types import SimpleNamespace
import json
import pytest
from tests.test_agent_door import window_key
from orchestrator.door.projects import access

from fastapi import FastAPI
from fastapi.testclient import TestClient

from orchestrator.door import api, schema
from orchestrator.origin import studio_origin
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_comparisons import identity
from tests.test_agent_door import connect, bearer, BASE, HEADER, STUDIO


def test_agents_share_study_evidence_but_cannot_forge_authorship(tmp_path):
    store=ProjectStore(tmp_path/'projects')
    built=SimpleNamespace(paths=SimpleNamespace(home=tmp_path),
        hub=SimpleNamespace(clients=set(),resources=SimpleNamespace(projects=store)),
        day=SimpleNamespace(sid='2026-10-28',elsewhere=lambda _:SimpleNamespace(local_only=False)))
    app=FastAPI();api.register(app,built,studio_origin,HEADER)
    with TestClient(app,base_url=BASE) as client:
        a=connect(client,label='Synthetic A');b=connect(client,label='Synthetic B',secret='second-client-secret-0001')
        h1,h2=bearer(a['token']),bearer(b['token'])
        access(built).remember('2026-10-28', 'agent:'+a['connection']['id'])
        access(built).grant('2026-10-28', 'agent:'+b['connection']['id'], 'contribute', authority='development')
        fields={'session':'2026-10-28','requestKey':'create-study-001','title':'Cross-client continuation',
                'objective':'Inspect evidence','criteria':'Exact evidence retained','budgetUsd':0,'inputs':[]}
        created=client.post('/api/agent/v1/studies',json=fields,headers=h1)
        assert created.status_code==200,created.text
        study=created.json()['study'];sid=study['id']
        assert study['createdBy']['by']=='agent:'+a['connection']['id']
        result=store.create('2026-10-28',{'kind':'text-file','title':'Evidence','state':{'text':'Synthetic measured output'},'resources':[],'runtimes':[]})
        read=client.post(f'/api/agent/v1/studies/{sid}/evidence',json={'session':'2026-10-28','reference':identity(result)},headers=h2)
        assert read.status_code==200 and read.json()['document']==result.document
        claim={'session':'2026-10-28','requestKey':'claim-001','statement':'Synthetic output retained',
               'evidence':[{'artifact':identity(result),'relation':'supports'}],'dependsOn':[],'limitations':'Synthetic fixture'}
        # A connected reviewer confused input requestKey with the saved request
        # field. A refusal must explain correction without publishing a claim.
        for wrong in ({**claim, 'request': claim['requestKey']},
                      {**{k:v for k,v in claim.items() if k not in ('session','requestKey')}, 'request':'claim-001'}):
            refused=client.post(f'/api/agent/v1/studies/{sid}/claims',json=wrong,headers=h2)
            assert refused.status_code==400
            schema.check(refused.json(),'refusal')
            assert 'exactly session, requestKey' in refused.json()['says']
            assert 'send requestKey, not request' in refused.json()['next']
            assert 'limitations is text' in refused.json()['says']
            assert client.get(f'/api/agent/v1/studies/{sid}?session=2026-10-28',headers=h2).json()['study'].get('claims',[])==[]
        kept=client.post(f'/api/agent/v1/studies/{sid}/claims',json=claim,headers=h2)
        assert kept.status_code==200,kept.text
        row=kept.json()['study']['claims'][0];schema.check(row,'claim')
        assert row['by']=='agent:'+b['connection']['id']
        forged=client.post(f'/api/agent/v1/studies/{sid}/claims',json={**claim,'by':'agent:'+a['connection']['id']},headers=h2)
        assert forged.status_code==400
        client.post(f"/api/agent-connections/{a['connection']['id']}/disconnect",json={},headers=STUDIO)
        assert client.get(f'/api/agent/v1/studies/{sid}?session=2026-10-28',headers=h1).status_code==401
        continued=client.get(f'/api/agent/v1/studies/{sid}?session=2026-10-28',headers=h2)
        assert continued.status_code==200 and continued.json()['study']['claims']==[row]
        assert client.get(f'/api/agent/v1/studies/{sid}?session=foreign',headers=h2).status_code==400


def test_connected_agent_discovers_exact_workflow_inputs_and_frozen_definition(tmp_path):
    from orchestrator.matrix_capability_runs import descriptors
    from tests.test_workflows import graph
    store=ProjectStore(tmp_path/'projects')
    built=SimpleNamespace(paths=SimpleNamespace(home=tmp_path),
        hub=SimpleNamespace(clients=set(),resources=SimpleNamespace(projects=store)),
        day=SimpleNamespace(sid='2026-10-28',elsewhere=lambda _:SimpleNamespace(local_only=True)))
    app=FastAPI();api.register(app,built,studio_origin,HEADER)
    with TestClient(app,base_url=BASE) as client:
        route='/api/agent/v1/workflows/capabilities'
        assert client.get(route).status_code==401
        connection=connect(client);headers=bearer(connection['token'])
        access(built).remember('2026-10-28', 'agent:'+connection['connection']['id'])
        found=client.get(route,headers=headers)
        assert found.status_code==200,found.text
        assert found.json()['capabilities']==json.loads(json.dumps(descriptors()))
        for row in found.json()['capabilities']:
            schema.check(row,'workflow-capability')
        fitted=next(c for c in found.json()['capabilities'] if c['id']=='response-fit')
        assert fitted['inputs']==[{'id':'effects','type':'artifact.response-effects'}]
        assert fitted['generation'].startswith('matrix-v1.')
        assert 'regularization' in fitted['parameterSchema']['properties']
        command={'project':'2026-10-28','action':'create','name':'Exact discovery','graph':graph(),'request':'d'*32}
        created=client.post('/api/agent/v1/workflows/command',json=command,headers=headers).json()['workflow']
        path=f"/api/agent/v1/workflows/{created['id']}?session=2026-10-28&revision={created['revision']}"
        opened=client.get(path,headers=headers)
        assert opened.status_code==200 and opened.json()['workflow']==created
        assert client.get(path.replace('revision=1','revision=99'),headers=headers).status_code==400
        client.post(f"/api/agent-connections/{connection['connection']['id']}/disconnect",json={},headers=STUDIO)
        assert client.get(route,headers=headers).status_code==401
        assert client.get(path,headers=headers).status_code==401


def test_study_question_ui_to_connected_agent_roundtrip_enforces_grants(tmp_path):
    from orchestrator.research.effort_api import register
    store=ProjectStore(tmp_path/'projects')
    session='2026-10-28'
    built=SimpleNamespace(paths=SimpleNamespace(home=tmp_path),
        hub=SimpleNamespace(clients=set(),resources=SimpleNamespace(projects=store)),
        day=SimpleNamespace(sid=session,elsewhere=lambda _:SimpleNamespace(local_only=True)))
    app=FastAPI();api.register(app,built,studio_origin,HEADER);register(app,built,studio_origin,HEADER)
    with TestClient(app,base_url=BASE) as client:
        a=connect(client,label='Lead');b=connect(client,label='Reviewer',secret='second-client-secret-0001')
        h1,h2=bearer(a['token']),bearer(b['token']);aa='agent:'+a['connection']['id'];bb='agent:'+b['connection']['id']
        access(built).remember(session,aa);access(built).grant(session,bb,'contribute',authority='development')
        fields={'session':session,'requestKey':'new-study','title':'Study','objective':'Read actual evidence','criteria':'Cite exact revisions','budgetUsd':0,'inputs':[]}
        study=client.post('/api/agent/v1/studies',json=fields,headers=h1).json()['study']
        sid=study['id'];local=f'/api/research-efforts/{sid}/questions';door=f'/api/agent/v1/studies/{sid}/questions'
        studio={**STUDIO,HEADER:'research-effort'}
        options=client.get(local,params={'session':session},headers=studio)
        assert options.status_code==200,options.text
        assert len(options.json()['recipients'])==2
        basis={key:study[key] for key in ('id','session','revision','digest')}
        body={'session':session,'request':'question-ui','question':'what does it mean?','basis':basis,'recipient':bb}
        assert client.post(local,json=body).status_code==403
        asked=client.post(local,json=body,headers=studio)
        assert asked.status_code==200,asked.text
        question=asked.json()['question'];qid=question['id'];schema.check(question,'study-question')
        assert client.post(local,json=body,headers=studio).json()['question']==question
        context=client.get(door+'/'+qid,params={'session':session},headers=h2).json()
        assert context['study']['objective']=='Read actual evidence'
        assert context['study']['revision']==basis['revision']
        answer={'session':session,'statement':'Explanation based on the saved protocol.', 'evidence':[basis],'limitations':'No experiments yet.'}
        assert client.post(door+'/'+qid+'/answer',json=answer,headers=h1).status_code==400
        assert client.post(door+'/'+qid+'/answer',json={**answer,'by':aa},headers=h2).status_code==400
        access(built).grant(session,bb,'read',authority='development')
        assert client.post(door+'/'+qid+'/answer',json=answer,headers=h2).status_code==403
        access(built).grant(session,bb,'contribute',authority='development')
        got=client.post(door+'/'+qid+'/answer',json=answer,headers=h2)
        assert got.status_code==200,got.text
        assert got.json()['question']['answer']['by']==bb
        schema.check(got.json()['question'],'study-question')
        assert client.post(door+'/'+qid+'/answer',json=answer,headers=h2).json()==got.json()
        assert client.get(local,params={'session':session},headers=studio).json()['questions']==[got.json()['question']]
        assert client.get(door,params={'session':'2026-10-27'},headers=h2).status_code==403
        client.post(f"/api/agent-connections/{b['connection']['id']}/disconnect",json={},headers=STUDIO)
        assert client.get(door,params={'session':session},headers=h2).status_code==401
        assert len(client.get(local,params={'session':session},headers=studio).json()['recipients'])==1
        built.day.sid='2026-10-27'
        assert client.post(local,json={**body,'request':'changed-project'},headers=studio).status_code==409


def test_coordination_shares_intent_but_enforces_each_project_grant(tmp_path):
    store=ProjectStore(tmp_path/'projects')
    session='2026-11-01'
    built=SimpleNamespace(paths=SimpleNamespace(home=tmp_path),hub=SimpleNamespace(clients=set(),resources=SimpleNamespace(projects=store)),day=SimpleNamespace(sid=session,elsewhere=lambda _:SimpleNamespace(local_only=True)))
    app=FastAPI();api.register(app,built,studio_origin,HEADER)
    with TestClient(app,base_url=BASE) as client:
        a=connect(client,label='Lead');b=connect(client,label='Reviewer',secret='second-client-secret-0001')
        h1,h2=bearer(a['token']),bearer(b['token']);aa='agent:'+a['connection']['id'];bb='agent:'+b['connection']['id']
        access(built).remember(session,aa);access(built).grant(session,bb,'read',authority='development')
        study=client.post('/api/agent/v1/studies',json={'session':session,'requestKey':'coordination-study','title':'Compare','objective':'Read evidence','criteria':'Measured result','budgetUsd':0,'inputs':[]},headers=h1).json()['study']
        base=f"/api/agent/v1/studies/{study['id']}/coordination"
        initial=client.get(base,params={'session':session},headers=h2)
        assert initial.status_code==200
        body={'session':session,'basis':initial.json()['basis'],'plan':{'desiredOutput':'Comparison','evaluation':'Error','methodNotes':'Baseline then follow-up','materials':[],'actorMode':'connected-agent','limits':{'experiments':2,'modelMinutes':30,'tokens':60000}}}
        assert client.post(base+'/plan',json=body,headers=h2).status_code==403
        saved=client.post(base+'/plan',json=body,headers=h1)
        assert saved.status_code==200,saved.text
        assert saved.json()['coordination']['plan']['by']==aa
        body['by']=bb
        assert client.post(base+'/plan',json=body,headers=h1).status_code==400
        access(built).grant(session,bb,'contribute',authority='development')
        from tests.test_research_coordination import proposal
        assigned=client.post(base+'/propose',json={'session':session,'request':'review','assignment':proposal()},headers=h1)
        assignment=assigned.json()['coordination']['id']
        claim=client.post(base+'/claim',json={'session':session,'assignment':assignment,'harness':'fixture','model':'fixture'},headers=h2)
        assert claim.status_code==200,claim.text
        assert claim.json()['coordination']['assignments'][0]['claimedBy']['by']==bb
        client.post(f"/api/agent-connections/{b['connection']['id']}/disconnect",json={},headers=STUDIO)
        assert client.get(base,params={'session':session},headers=h2).status_code==401
        assert client.get(base,params={'session':'unrelated'},headers=h1).status_code in (400,403)
        assert client.get('/api/agent/v1/research-instructions',headers=h1).status_code==200
        assert client.get('/api/agent/v1/research-instructions',headers=h2).status_code==401
