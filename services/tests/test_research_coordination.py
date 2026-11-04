"""Synthetic scientific records through real immutable storage; no agent/model calls."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest
from orchestrator.research.coordination import Coordination
from orchestrator.research.efforts import Efforts, KEY
from orchestrator.scientific_projects import ProjectStore, Conflict, InvalidProject
from orchestrator.scientific_comparisons import identity

@pytest.fixture
def context(tmp_path):
    store = ProjectStore(tmp_path/'projects')
    efforts = Efforts(store)
    saved = efforts.create('2026-11-01', request='study', title='Research', objective='Compare', criteria='Error', budget_usd=0)
    studies = SimpleNamespace(store=store, efforts=efforts, attribution=lambda a,l: {'by':a,'byLabel':l})
    return Coordination(studies, clock=lambda:1000), saved

def proposal():
    return {'objective':'Inspect inputs','deliverable':'Evidence-linked report','uncertainty':'Coverage',
            'decisionImpact':'Choose a suitable baseline','whyNewWork':'No inspection retained',
            'evidence':[], 'dependsOn':[]}

def propose(c,s,request='first',assignment=None):
    return c.propose(s.id,s.session,request=request,assignment=assignment or proposal(),actor='agent:a',label='Lead')

def claim(c,s,key,actor='agent:a'):
    return c.claim(s.id,s.session,assignment=key,actor=actor,label=actor,harness='fixture',model='fixture')

def test_plan_is_retained_and_stale_edit_refuses(context):
    c,s=context
    plan={'desiredOutput':'Prediction','evaluation':'Error','materials':[],'methodNotes':'Baseline',
          'actorMode':'connected-agent','limits':{'experiments':2,'modelMinutes':30,'tokens':60000}}
    result=c.save_plan(s.id,s.session,basis=identity(s),plan=plan,actor='studio',label='You')
    assert result['plan']['status']=='draft' and not result['experiments']
    with pytest.raises(Conflict):
        c.save_plan(s.id,s.session,basis=identity(s),plan=plan,actor='studio',label='You')
    reviewed=c.review_plan(s.id,s.session,basis=result['basis'],actor='studio',label='You')
    assert reviewed['plan']['status']=='reviewed'
    amended=c.save_plan(s.id,s.session,basis=reviewed['basis'],plan=plan,actor='studio',label='You')
    assert amended['plan']['status']=='draft'
    assert c.efforts.read(s.id,s.session).document['state'][KEY]['researchPlanHistory'][0]['status']=='reviewed'

def test_assignments_retry_without_duplicates_and_require_distinct_intent(context):
    c,s=context
    a=propose(c,s)
    assert propose(c,s)==a
    with pytest.raises(Conflict):
        propose(c,s,assignment={**proposal(),'objective':'Different'})
    assert len(c.read(s.id,s.session)['assignments'])==1

def test_concurrent_claim_has_one_winner(context):
    c,s=context
    a=propose(c,s)
    def attempt(actor):
        try:
            claim(c,s,a['id'],actor)
            return True
        except Conflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(attempt,['agent:a','agent:b']))==[False,True]

def test_expiration_requires_original_owner_not_duplicate_dispatch(context):
    c,s=context
    a=propose(c,s)
    claim(c,s,a['id'])
    c.clock=lambda:1400
    assert c.read(s.id,s.session)['assignments'][0]['observedState']=='needs-reconciliation'
    with pytest.raises(Conflict): claim(c,s,a['id'],'agent:b')
    claim(c,s,a['id'])
    assert c.read(s.id,s.session)['assignments'][0]['observedState']=='running'

def test_dependencies_and_completion_preserve_author(context):
    c,s=context
    a=propose(c,s)
    b=propose(c,s,'review',{**proposal(),'dependsOn':[a['id']]})
    with pytest.raises(Conflict): claim(c,s,b['id'])
    claim(c,s,a['id'])
    fields=dict(assignment=a['id'],statement='No measurements yet',evidence=[],limitations='Setup only',label='Lead')
    with pytest.raises(Conflict): c.complete(s.id,s.session,actor='agent:b',**fields)
    result=c.complete(s.id,s.session,actor='agent:a',**fields)
    assert c.complete(s.id,s.session,actor='agent:a',**fields)==result
    assert result['assignments'][0]['report']['by']=='agent:a'
    claim(c,s,b['id'],'agent:b')
    state=c.efforts.read(s.id,s.session).document['state'][KEY]
    assert state['findings']==[] # an assignment report never becomes human acceptance

def test_paused_study_refuses_new_claim(context):
    c,s=context
    a=propose(c,s)
    c.efforts.control(s.id,s.session,'pause')
    with pytest.raises(InvalidProject): claim(c,s,a['id'])


def test_capability_proposal_pins_package_and_never_accepts_activation(context):
    from orchestrator.scientific_file_intake import FileIntake
    c,s=context
    intake=FileIntake(c.studies.store,s.session,'diagnostic-package',name='diagnostic.zip',size=3,max_bytes=10)
    intake.write(b'zip');package=intake.publish();intake.close()
    proposal={'title':'Intensity summary','gap':'A bounded diagnostic', 'package':identity(package),
              'licence':'MIT','dependencies':'Existing image runtime','permissions':'Selected image only',
              'resources':'CPU, 1 MiB','proofPlan':'Compare exact reference intensities'}
    fields=dict(request='diagnostic',proposal=proposal,actor='agent:a',label='Lead')
    first=c.propose_capability(s.id,s.session,**fields)
    assert c.propose_capability(s.id,s.session,**fields)==first
    assert first['capabilityProposals'][0]['status']=='awaiting-owner-review'
    with pytest.raises(Conflict):
        c.propose_capability(s.id,s.session,**{**fields,'proposal':{**proposal,'permissions':'Network'}})
    with pytest.raises(ValueError):
        c.propose_capability(s.id,s.session,**{**fields,'proposal':{**proposal,'activate':True}})
    assert first['experiments']==[]

def test_reviewed_plan_bounds_and_deduplicates_scientific_admissions(tmp_path):
    import asyncio
    from tests.test_study_workflows import setup, A, B
    from tests.waiting import until_async
    async def run():
        studies,runner,study,image,definition=setup(tmp_path)
        runner.steps.execute=lambda _: {'status':'completed','output':identity(image)}
        c=Coordination(studies)
        plan={'desiredOutput':'Measured output','evaluation':'Compare','materials':[identity(image)],'methodNotes':'Inspect',
              'actorMode':'connected-agent','limits':{'experiments':1,'modelMinutes':30,'tokens':60000}}
        saved=c.save_plan(study.id,study.session,basis=identity(study),plan=plan,actor='studio',label='You')
        args=dict(study=study.id,session=study.session,request='first-work',workflow=definition.id,revision=1,inputs=[identity(image)],actor=A,label='Lead')
        with pytest.raises(InvalidProject): await studies.start_workflow(**args)
        c.review_plan(study.id,study.session,basis=saved['basis'],actor='studio',label='You')
        with pytest.raises(InvalidProject): await studies.start_workflow(**args)
        task=c.propose(study.id,study.session,request='first-task',assignment=proposal(),actor=A,label='Lead')
        c.claim(study.id,study.session,assignment=task['id'],actor=A,label='Lead',harness='fixture',model='fixture')
        first,made=await studies.start_workflow(**args,assignment=task['id'])
        assert made
        second,made=await studies.start_workflow(**{**args,'actor':B,'request':'another-request'})
        assert not made and first['id']==second['id'] and second['by']==A
        assert c.read(study.id,study.session)['assignments'][0]['operations']==[first['id']]
        await until_async(lambda: not runner.tasks)
    asyncio.run(run())


def test_admission_reservation_reuse_and_explicit_repeat(tmp_path):
    import asyncio
    import threading
    from tests.test_study_workflows import setup, A, B
    from tests.waiting import until_async
    async def run():
        studies, runner, study, image, definition = setup(tmp_path)
        gate = threading.Event()
        c = Coordination(studies)
        plan = {'desiredOutput':'Measured output','evaluation':'Compare','materials':[identity(image)],
                'methodNotes':'Inspect','actorMode':'connected-agent',
                'limits':{'experiments':2,'modelMinutes':30,'tokens':60000}}
        saved = c.save_plan(study.id,study.session,basis=identity(study),plan=plan,actor='studio',label='You')
        c.review_plan(study.id,study.session,basis=saved['basis'],actor='studio',label='You')
        def task(actor, request):
            row = c.propose(study.id,study.session,request=request,assignment=proposal(),actor=actor,label=actor)
            c.claim(study.id,study.session,assignment=row['id'],actor=actor,label=actor,harness='fixture',model='fixture')
            return row['id']
        first_task, second_task = task(A,'a'), task(B,'b')
        report = dict(statement='Observed output',evidence=[identity(image)],limitations='Controlled worker',label='Lead')
        original_command = runner.command
        async def command(body, **kwargs):
            if body['action'] == 'validate':
                # complete() shares the immutable revision boundary with admission.
                with pytest.raises(Conflict, match='admission'):
                    c.complete(study.id,study.session,assignment=first_task,actor=A,**report)
            return await original_command(body, **kwargs)
        runner.command = command
        def execute(_):
            assert gate.wait(10)
            return {'status':'completed','output':identity(image)}
        runner.steps.execute = execute
        args = dict(study=study.id,session=study.session,request='first',workflow=definition.id,
                    revision=1,inputs=[identity(image)],actor=A,label='Lead',assignment=first_task)
        try:
            first, made = await studies.start_workflow(**args)
            assert made
            reused, made = await studies.start_workflow(**{**args,'actor':B,'assignment':second_task,'request':'reuse'})
            assert not made and reused['id'] == first['id'] and reused['by'] == A
            second = next(a for a in c.read(study.id,study.session)['assignments'] if a['id']==second_task)
            assert second['operations'] == [first['id']]
            with pytest.raises(Conflict, match='settle'):
                c.complete(study.id,study.session,assignment=second_task,actor=B,**report)
            gate.set()
            await until_async(lambda: not runner.tasks)
            repeat = {**args,'request':'repeat','repeat':{'id':'replicate-1','reason':'Check unchanged-method repeatability'}}
            repeated, made = await studies.start_workflow(**repeat)
            assert made and repeated['id'] != first['id']
            retry, made = await studies.start_workflow(**repeat)
            assert not made and retry['id'] == repeated['id']
            await until_async(lambda: not runner.tasks)
            c.complete(study.id,study.session,assignment=first_task,actor=A,**report)
            retry, made = await studies.start_workflow(**repeat)
            assert not made and retry['id'] == repeated['id']
            with pytest.raises(InvalidProject, match='allowance'):
                await studies.start_workflow(**{**repeat,'request':'third','repeat':{'id':'replicate-2','reason':'Another repeat'}})
        finally:
            gate.set()
            await runner.close()
    asyncio.run(run())


def test_unrelated_study_write_does_not_block_plan_edit(context):
    c,s=context
    plan={'desiredOutput':'Prediction','evaluation':'Error','materials':[],'methodNotes':'Baseline',
          'actorMode':'connected-agent','limits':{'experiments':2,'modelMinutes':30,'tokens':60000}}
    saved=c.save_plan(s.id,s.session,basis=identity(s),plan=plan,actor='studio',label='You')
    propose(c,s)
    edited=c.save_plan(s.id,s.session,basis=saved['basis'],plan={**plan,'methodNotes':'Compare'},actor='studio',label='You')
    assert edited['plan']['methodNotes']=='Compare'
    assert len(edited['assignments'])==1
    with pytest.raises(InvalidProject,match='owner'):
        c.review_plan(s.id,s.session,basis=edited['basis'],actor='agent:a',label='Agent')
    reviewed=c.review_plan(s.id,s.session,basis=edited['basis'],actor='studio',label='You')
    with pytest.raises(InvalidProject,match='increase'):
        c.save_plan(s.id,s.session,basis=reviewed['basis'],plan={**plan,'limits':{**plan['limits'],'experiments':3}},actor='agent:a',label='Agent')


def test_stop_assignment_without_external_process_settles_coordination(context):
    c,s=context
    task=propose(c,s)
    claim(c,s,task['id'])
    saved=c.efforts.control(s.id,s.session,'stop',actor='studio')
    task=saved.document['state'][KEY]['tasks'][task['id']]
    assert task['status']=='cancelled' and task['chargedMicrousd']==0
    assert task['collaboration']['claimedBy']['by']=='agent:a'


def test_owner_releases_expired_claim_preserving_authorship(context):
    c,s=context
    task=propose(c,s)
    claim(c,s,task['id'])
    c.clock=lambda:1400
    with pytest.raises(InvalidProject,match='owner'):
        c.recover(s.id,s.session,assignment=task['id'],actor='agent:b',label='B')
    restored=c.recover(s.id,s.session,assignment=task['id'],actor='studio',label='You')
    assert restored['assignments'][0]['priorClaims'][0]['claimedBy']['by']=='agent:a'
    claim(c,s,task['id'],'agent:b')
    assert c.read(s.id,s.session)['assignments'][0]['claimedBy']['by']=='agent:b'


def test_recovery_preserves_unresolved_admission(context):
    c,s=context
    task=propose(c,s)
    claim(c,s,task['id'])
    def pending(state):
        state['tasks'][task['id']]['collaboration']['pendingAdmissions']={'original':{'request':'original'}}
        return True
    c.efforts._change(s.id,s.session,pending)
    with pytest.raises(Conflict,match='unresolved'):
        c.recover(s.id,s.session,assignment=task['id'],actor='studio',label='You')
    assert c.read(s.id,s.session)['assignments'][0]['pendingAdmissions']


def test_recovery_retains_original_receipt_before_refusing_live_execution(context):
    c,s=context
    a=propose(c,s);claim(c,s,a['id'])
    execution={'kind':'workflow','id':'original','session':s.session}
    def pending(state):
        state['tasks'][a['id']]['collaboration']['pendingAdmissions']={'lost':{'request':'lost-reply','execution':execution}}
        return True
    c.efforts._change(s.id,s.session,pending)
    receipt={'id':'op-original','project':s.session,'execution':execution,'params':{'study':s.id},'by':'agent:a','byLabel':'Lead'}
    c.studies.operations=SimpleNamespace(request_receipt=lambda key,by:receipt,get=lambda key:receipt)
    c.studies.workflows=SimpleNamespace(observed_status=lambda run,session:{'status':'running'})
    with pytest.raises(Conflict,match='Original operations are now linked'):
        c.recover(s.id,s.session,assignment=a['id'],actor='studio',label='You')
    state=c.efforts.read(s.id,s.session).document['state'][KEY]
    assert state['experiments']['lost']['operation']=='op-original'
    assert state['tasks'][a['id']]['collaboration']['operations']==['op-original']
    assert not state['tasks'][a['id']]['collaboration'].get('pendingAdmissions')
    c.studies.workflows.observed_status=lambda run,session:{'status':'completed'}
    released=c.recover(s.id,s.session,assignment=a['id'],actor='studio',label='You')
    assert released['assignments'][0]['observedState']=='planned'
    assert released['experiments'][0]['by']=='agent:a'


def test_assignment_parent_must_exist_and_coordination_is_not_execution(context):
    from orchestrator.research.efforts import summary
    c,s=context
    with pytest.raises(InvalidProject,match='parent'):
        propose(c,s,assignment={**proposal(),'parent':'absent'})
    a=propose(c,s)
    propose(c,s,'child',{**proposal(),'parent':a['id'],'approach':'Inspect morphology'})
    observed=summary(c.efforts.read(s.id,s.session))
    assert observed['coordination']==dict(waiting=2,claimed=0,reports=0,stopped=0,unresolvedAdmissions=0)
    assert observed['heldMicrousd']==0


def test_recovery_cannot_declare_missing_history_absent(context, tmp_path):
    from orchestrator.door.operations import Operations
    from orchestrator.door.connections import Refused
    c,s=context
    a=propose(c,s);claim(c,s,a['id'])
    def pending(state):
        state['tasks'][a['id']]['collaboration']['pendingAdmissions']={'lost':{'request':'lost-reply','execution':{'kind':'workflow','id':'original','session':s.session}}}
        return True
    c.efforts._change(s.id,s.session,pending)
    c.studies.operations=Operations(tmp_path)
    c.studies.operations._history_complete=False
    with pytest.raises(Refused,match='history is unavailable'):
        c.recover(s.id,s.session,assignment=a['id'],actor='studio',label='You')
    kept=c.read(s.id,s.session)['assignments'][0]
    assert kept['pendingAdmissions'] and kept['claimedBy']['by']=='agent:a'


def test_comparison_plan_requires_distinct_exact_methods_and_two_runs(context):
    c, s = context
    ref = identity(s)
    plan = {'desiredOutput': 'Measured comparison', 'evaluation': 'Mean error, lower is better',
            'materials': [ref], 'methodNotes': 'Change one method', 'actorMode': 'manual',
            'limits': {'experiments': 2, 'modelMinutes': 5, 'tokens': 1000},
            'comparison': {'baseline': ref, 'challenger': ref, 'input': ref, 'bindings': {}}}
    with pytest.raises(ValueError, match='distinct'):
        c.save_plan(s.id, s.session, basis=ref, plan=plan, actor='studio', label='You')
    plan['limits']['experiments'] = 3
    with pytest.raises(ValueError, match='two'):
        c.save_plan(s.id, s.session, basis=ref, plan=plan, actor='studio', label='You')


def test_two_method_admission_rejects_input_substitution_and_unreviewed_repeat(context):
    from orchestrator.research.method_pair import candidate_role
    _, s = context
    ref = identity(s)
    spec = {'baseline': ref, 'challenger': {**ref, 'revision': 2}, 'input': ref, 'bindings': {}}
    params = {'workflow': ref['id'], 'revision': 1, 'inputs': [ref], 'bindings': {}}
    assert candidate_role(spec, params) == 'baseline'
    assert candidate_role(spec, {**params, 'revision': 2}) == 'challenger'
    for changed in ({'inputs': []}, {'bindings': {'replacement': ref}}, {'revision': 3}, {'repeat': {'id': 'new'}}):
        with pytest.raises(InvalidProject):
            candidate_role(spec, {**params, **changed})


def test_report_numbers_must_match_both_frozen_score_files():
    from orchestrator.research.method_pair import verify_score_pair
    with pytest.raises(InvalidProject):
        verify_score_pair({'metrics': []}, {}, {})


def test_same_method_with_renamed_or_reordered_nodes_is_not_a_challenger():
    from orchestrator.research.method_pair import method_signature
    def graph(key, setting):
        prepare = {'id': key, 'name': key, 'kind': 'tool', 'needs': ['input'],
                   'settings': {'parameters': {'value': setting}}}
        fit = {'id': 'fit', 'name': 'Fit', 'kind': 'tool', 'needs': [key], 'settings': {'bindings': {'matrix': key}}}
        return {'input': {'kind': 'source-file'}, 'nodes': [prepare, fit]}
    original, renamed = graph('prepare', 1), graph('renamed', 1)
    renamed['nodes'].reverse()
    assert method_signature(original) == method_signature(renamed)
    assert method_signature(original) != method_signature(graph('prepare', 2))
