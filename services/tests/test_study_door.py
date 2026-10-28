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
