"""Synthetic maintenance targets/workers; real sealed workflow artifacts and decision ledger.

These checks establish exact admission/restore routing, not actual plugin qualification.
"""
from contextlib import contextmanager
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from orchestrator import bundle_review, env_runner, local_workflow, own_trials, scientific_project_review, scientific_projects
from orchestrator.maintenance import workflow, promote, prepare, policy
from tests.test_local_workflow import trial, reopen  # noqa: F401


@pytest.fixture
def candidate(trial, monkeypatch):
    t = trial
    local_workflow.Observation(t.worker,t.source).saved_tool(t.result,t.history)
    proof = reopen(t)
    t.installed = env_runner.Installed(t.owner,t.folder,proof,True)
    t.old = env_runner.Installed(replace(t.owner,version='0.1'),t.folder.with_name('c'*16),{},False)
    t.selected = [t.old]
    monkeypatch.setattr(env_runner,'selected',lambda *a:t.selected[0])
    monkeypatch.setattr(env_runner,'in_use',lambda *a:t.selected[0])
    monkeypatch.setattr(env_runner,'generation',lambda m,g,*a:t.installed if g==t.folder.name else t.old if g==t.old.folder.name else None)
    monkeypatch.setattr(workflow.discover,'last',lambda *a:{})
    monkeypatch.setattr(workflow.discover,'fresh',lambda *a:t.owner)
    monkeypatch.setattr(workflow.envs,'generation',lambda *a:t.folder.name)
    monkeypatch.setattr(workflow.revocations,'revoked',lambda *a:None)
    monkeypatch.setattr(workflow.compat,'compare',lambda *a:{'compatible':True,'review':[]})
    t.digest = bundle_review.identity(t.installed)
    return t


def test_candidate_acceptance_keeps_active_digest_and_is_exact(candidate, monkeypatch):
    t=candidate; env=t.worker.environ
    marker=own_trials.marker(t.owner.key,env)
    marker.write_text(json.dumps({'model':t.owner.key,'adoptedDigest':'old accepted digest'}))
    before=marker.read_bytes()
    workflow.accept(t.owner,t.folder.name,t.digest,env)
    assert marker.read_bytes()==before and t.selected[0] is t.old
    assert workflow.accepted(t.owner,t.folder.name,t.digest,env)
    assert not workflow.accepted(t.owner,'e'*16,t.digest,env)
    assert not workflow.accepted(t.owner,t.folder.name,'e'*64,env)
    t.selected[0]=t.installed
    assert not own_trials.pending(t.owner,env)
    t.selected[0]=t.old
    monkeypatch.setattr(own_trials,'comparison_digest',lambda *a,**kw:'old accepted digest')
    assert not own_trials.pending(t.owner,env)


def test_stale_target_or_project_never_accepts_or_applies(candidate,monkeypatch):
    t=candidate;env=t.worker.environ
    with pytest.raises(ValueError,match='Project or update'):
        workflow.accept(t.owner,t.folder.name,t.digest,env,still_current=lambda:False)
    assert not promote.decisions(t.owner.key,env)
    with pytest.raises(ValueError,match='candidate changed'):
        workflow.accept(t.owner,'e'*16,t.digest,env)
    workflow.accept(t.owner,t.folder.name,t.digest,env)
    with pytest.raises(promote.Failed,match='exact update receipt'):
        prepare.apply(t.owner,t.folder.name,env,digest='e'*64)
    assert t.selected[0] is t.old and prepare.last(t.owner.key,env)=={}
    monkeypatch.setattr(workflow.envs,'generation',lambda *a:'f'*16)
    with pytest.raises(ValueError,match='candidate changed'):
        workflow.require_accepted(t.owner,t.folder.name,t.digest,env)


def test_candidate_changed_requirements_and_revocation_still_gate_trial(candidate,monkeypatch):
    t=candidate;env=t.worker.environ
    monkeypatch.setattr(workflow.compat,'compare',lambda *a:{'compatible':False,'review':[{'what':'licence','from':'old','to':'new','kind':'rights'}]})
    with pytest.raises(ValueError,match='changed requirements'):
        workflow.target(t.owner,t.folder.name,env)
    monkeypatch.setattr(workflow.revocations,'revoked',lambda *a:{'why':'unsafe'})
    with pytest.raises(ValueError,match='revoked'):
        workflow.target(t.owner,t.folder.name,env)


def test_apply_requires_explicit_person_even_after_workflow_acceptance(candidate):
    t=candidate;env=t.worker.environ
    workflow.accept(t.owner,t.folder.name,t.digest,env)
    with pytest.raises(promote.Failed,match='explicit Review and Apply'):
        promote.promote(t.owner,t.folder.name,by='maintenance:auto',environ=env)
    assert t.selected[0] is t.old


@pytest.mark.parametrize('fault',[None,'missing','changed','render-failed'])
def test_first_check_restores_only_exact_saved_state_or_rolls_back(candidate,monkeypatch,fault):
    t=candidate;env=t.worker.environ
    workflow.accept(t.owner,t.folder.name,t.digest,env)
    policy.record(t.owner,'auto',by='user',how='screen',environ=env)
    requests=[]
    def read(*a,**kw):
        if fault=='missing':raise ValueError('Saved trial is missing')
        return SimpleNamespace(**{**vars(t.result),'digest':'0'*64}) if fault=='changed' else t.result
    monkeypatch.setattr(scientific_projects,'ProjectStore',lambda *a,**kw:SimpleNamespace(read=read))
    def request(value):
        requests.append(value)
        if fault=='render-failed':raise ValueError('Saved state cannot render')
        return {'frame':{}},b'\0'*(256*192*4)
    @contextmanager
    def restored(*a,**kw):
        yield t.result,SimpleNamespace(request=request)
    monkeypatch.setattr(scientific_project_review,'review',restored)
    def select(m,g,**kw):
        t.selected[0]=t.installed if g==t.folder.name else t.old
        return t.selected[0]
    monkeypatch.setattr(env_runner,'promote',select)
    monkeypatch.setattr(env_runner,'run_once',lambda *a:pytest.fail('No RGB or scientific operation replay'))
    decision=prepare.apply(t.owner,t.folder.name,env,digest=t.digest)
    assert decision['action']==('rollback' if fault else 'promote')
    assert t.selected[0] is (t.old if fault else t.installed)
    checks=[d for d in promote.decisions(t.owner.key,env) if d['action']=='checked']
    assert len(checks)==1 and checks[0]['ok']==(fault is None)
    assert requests==([] if fault in ('missing','changed') else [{'op':'render','width':256,'height':192}])
    if fault:assert policy.get(t.owner.key,env).get('held')


def test_status_offers_exact_receipt_review_then_apply_without_starting_work(candidate,monkeypatch):
    from orchestrator.maintenance import status
    t=candidate;env=t.worker.environ
    monkeypatch.setattr(env_runner,'run_once',lambda *a:pytest.fail('Reading status cannot execute'))
    shown=status.of(t.owner,env)
    assert shown['state']=='needs-review' and shown['workflowReview']==t.folder.name and shown['next'] is None
    workflow.accept(t.owner,t.folder.name,t.digest,env)
    shown=status.of(t.owner,env)
    assert shown['state']=='ready-to-apply' and shown['next']=='apply'
    assert shown['candidate']['generation']==t.folder.name and shown['candidate']['digest']==t.digest
    assert t.selected[0] is t.old
