"""Synthetic owned-worker observations with real bounded sealed receipt/artifact storage.

No plugin, scientific accuracy, movie export or ordinary UI qualification is inferred.
"""
import copy
from dataclasses import replace
from types import SimpleNamespace

import pytest

from gpu_worker import bundle_contract, manifests as catalog
from orchestrator import bundle_admission, bundle_review, env_runner, envs, local_workflow, own_trials
from tests.test_bundle_manifest import HEAD

MEMBER = '''
[[members]]
id = "viewer"
name = "Interactive tool"
adapter = "adapter:open_host"
bridge = "scientific-host-v1"
inputs = [{id="project", type="project.scientific"}]
outputs = [{id="result", type="project.scientific"}]
'''


@pytest.fixture
def trial(tmp_path, monkeypatch):
    path = tmp_path/'image-tools/model.toml'; path.parent.mkdir(); path.write_text(HEAD+MEMBER)
    owner, errors = catalog.read(path); assert owner, errors
    folder = tmp_path/('a'*16); folder.mkdir()
    identity = {'generation':folder.name,'workflowHostSha256':'b'*64,'preparationSha256':'c'*64,'declarationSha256':'d'*64}
    monkeypatch.setattr(local_workflow, '_identity', lambda *a:dict(identity))
    monkeypatch.setattr(env_runner, 'preserve_active', lambda *a:None)
    member = bundle_contract.parse_members(list(owner.members), owner)[0]
    worker = SimpleNamespace(prepared=SimpleNamespace(m=bundle_contract.view(owner, member),folder=folder),
                             environ={},retained_tool=None,_allowed=lambda:None)
    source = SimpleNamespace(id='p-input',session='project',revision=1,digest='e'*64,document={'resources':[]})
    history_record = SimpleNamespace(id='p-history',session='project',revision=2,digest='f'*64)
    history = SimpleNamespace(pending=None,record=history_record,document={'state':{'cells':[], 'actions':[
        {'id':'control-one','state':'completed','request':{'op':'native-panel-change'}}]}})
    tool = {'document':{'state':{'threshold':.25}},'bindings':{'image':'input'}}
    result = SimpleNamespace(id='p-result',session='project',revision=1,digest='1'*64,document={
        'resources':[], 'state':{'scientificView':{'runtime':{'bundle':owner.key,'generation':folder.name,'operation':'viewer'},
                'tool':tool,'layers':[{'input':'input'}]},
            'hookeOutput':{'base':local_workflow._reference(source),'producer':{'history':local_workflow._reference(history_record)}}}})
    # Isolate marker files even when the worker has its own explicit environment mapping.
    worker.environ={'MICROMIND_CATALOG':str(tmp_path/'catalog')}
    return SimpleNamespace(owner=owner,folder=folder,identity=identity,worker=worker,source=source,history=history,result=result)


def reopen(t):
    t.worker.retained_tool = t.result.document['state']['scientificView']['tool']
    observation = local_workflow.Observation(t.worker,t.result)
    observation.rendered(SimpleNamespace(document={'state':{'actions':[],'cells':[]}}))
    return local_workflow._read(t.folder,'.proof.json')


def test_local_workflow_requires_real_parent_save_then_fresh_reopen_and_explicit_acceptance(trial, monkeypatch):
    t=trial
    assert bundle_admission.local_workflow(t.owner)
    assert bundle_admission.local_problem(t.owner) is None
    assert bundle_admission.integration_problem(t.owner)  # no public distribution waiver
    assert not reopen(t)  # a render by itself establishes nothing
    observer=local_workflow.Observation(t.worker,t.source)
    observer.saved_tool(t.result,t.history)
    assert own_trials.marker(t.owner.key,t.worker.environ).is_file()
    assert not local_workflow._read(t.folder,'.proof.json')
    proof=reopen(t)
    assert local_workflow.complete(t.owner,t.folder,proof)
    assert proof['fullQualification'] is False and proof['cases'][0]['saved']==proof['cases'][0]['reopened']
    installed=env_runner.Installed(t.owner,t.folder,proof,True)
    monkeypatch.setattr(env_runner,'in_use',lambda *a:installed)
    monkeypatch.setattr(catalog,'get',lambda *a:t.owner)
    assert own_trials.pending(t.owner,t.worker.environ)
    review=bundle_review.projection(installed)
    assert review['cases'][0]['kind']=='workflow' and 'device' not in review['cases'][0]
    bundle_review.accept(t.owner.key,t.folder.name,review['digest'],t.worker.environ)
    assert not own_trials.pending(t.owner,t.worker.environ)
    t.identity['workflowHostSha256']='2'*64
    assert not local_workflow.complete(t.owner,t.folder,proof)
    with pytest.raises(ValueError,match='complete reference checks'):
        bundle_review.accept(t.owner.key,t.folder.name,review['digest'],t.worker.environ)


@pytest.mark.parametrize('failure',['no-control','failed-control','wrong-result','different-history','changed-host','cancelled'])
def test_missing_failed_or_stale_observation_never_writes_success(trial,failure):
    t=trial; observer=local_workflow.Observation(t.worker,t.source)
    if failure=='no-control':t.history.document['state']['actions']=[]
    if failure=='failed-control':t.history.document['state']['actions'][0]['state']='unconfirmed'
    if failure=='wrong-result':t.result.document['state']['scientificView']['runtime']['generation']='3'*16
    if failure=='different-history':t.result.document['state']['hookeOutput']['producer']['history']['digest']='4'*64
    if failure=='changed-host':t.identity['workflowHostSha256']='5'*64
    if failure=='cancelled':t.worker._allowed=lambda:(_ for _ in ()).throw(ValueError('Stopped'))
    if failure in ('no-control','failed-control'):observer.saved_tool(t.result,t.history)
    else:
        with pytest.raises(ValueError):observer.saved_tool(t.result,t.history)
    assert not local_workflow._read(t.folder,'.proof.json')
    assert not local_workflow._read(t.folder,'.proof-attempt.json')


def test_one_member_cannot_cover_siblings_and_tampered_artifacts_refuse(trial):
    t=trial; second=copy.deepcopy(t.owner.members[0]);second['id']='second'
    owner=replace(t.owner,members=(*t.owner.members,second))
    first=bundle_contract.parse_members(list(owner.members),owner)[0]
    t.worker.prepared.m=bundle_contract.view(owner,first)
    local_workflow.Observation(t.worker,t.source).saved_tool(t.result,t.history)
    assert not reopen(t)
    attempt=local_workflow._read(t.folder,'.proof-attempt.json')
    assert attempt['cases'][0]['ok'] and not attempt['ok']
    assert not local_workflow.complete(owner,t.folder,attempt)
    # Neither changing the success bit nor changing retained bytes is a parent observation.
    attempt['ok']=True
    assert not local_workflow.complete(owner,t.folder,attempt)
    artifact=attempt['cases'][0]['artifacts']['actual']
    (bundle_review.folder(SimpleNamespace(folder=t.folder))/artifact['sha256']).write_bytes(b'changed')
    assert not local_workflow.complete(owner,t.folder,attempt)


def test_reopen_after_interaction_does_not_certify_the_saved_state(trial):
    t=trial
    local_workflow.Observation(t.worker,t.source).saved_tool(t.result,t.history)
    t.worker.retained_tool=t.result.document['state']['scientificView']['tool']
    local_workflow.Observation(t.worker,t.result).rendered(t.history)
    assert not local_workflow._read(t.folder,'.proof.json')


def test_maintenance_refuses_interactive_promotion_before_pointer_mutation(trial,monkeypatch):
    from orchestrator.maintenance import promote
    t=trial; installed=env_runner.Installed(t.owner,t.folder,{},False)
    monkeypatch.setattr(env_runner,'generation',lambda *a:installed)
    monkeypatch.setattr(env_runner,'promote',lambda *a,**k:pytest.fail('changed selection before supported first check'))
    with pytest.raises(promote.Failed,match='current version stays selected'):
        promote.promote(t.owner,t.folder.name,by='maintenance:auto',environ=t.worker.environ)


def test_workflow_preparation_cannot_become_an_automatic_reinstall_or_rgb_check(trial,monkeypatch):
    from orchestrator import readiness, channels, capability_shelf, catalog_view
    t=trial
    monkeypatch.setattr(readiness,'_operational',lambda m,e:m)
    monkeypatch.setattr(readiness,'obtained',lambda *a:(None,0))
    monkeypatch.setattr(channels,'trusted',lambda *a:True)
    monkeypatch.setattr(env_runner,'in_use',lambda *a:None)
    monkeypatch.setattr(env_runner,'turned_off',lambda *a:False)
    monkeypatch.setattr(env_runner,'ready',lambda *a:'Try, Save and reopen')
    steps,_,_=readiness.model_steps(t.owner,lambda key:False,t.worker.environ)
    assert [step.kind for step in steps]==['workflow']
    assert not capability_shelf._doable({'readiness':{'status':'needs-setup','steps':[{'kind':'workflow'}]}})
    assert catalog_view.proof_step(t.owner)['kind']=='workflow'


def test_stop_during_receipt_publication_preserves_pending_without_success(trial,monkeypatch):
    t=trial
    local_workflow.Observation(t.worker,t.source).saved_tool(t.result,t.history)
    t.worker.retained_tool=t.result.document['state']['scientificView']['tool']
    stopped=[False]
    observer=local_workflow.Observation(t.worker,t.result,stopping=lambda:stopped[0])
    original=bundle_review.store
    def store(*args,**kwargs):
        result=original(*args,**kwargs);stopped[0]=True;return result
    monkeypatch.setattr(bundle_review,'store',store)
    with pytest.raises(ValueError,match='stopped'):
        observer.rendered(SimpleNamespace(document={'state':{'actions':[],'cells':[]}}))
    assert not local_workflow._read(t.folder,'.proof.json')
    assert not local_workflow._read(t.folder,'.proof-attempt.json')['cases'][0]['ok']
