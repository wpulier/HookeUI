"""Real Project/workflow receipts with a controlled execution seam, not client qualification."""
import asyncio
import threading
from types import SimpleNamespace

import pytest

from orchestrator.door.operations import Operations
from orchestrator.door import schema
from orchestrator.research.studies import Studies
from orchestrator.research.efforts import summary
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from orchestrator.scientific_comparisons import identity
from orchestrator.workflow_service import WorkflowService
from tests.test_workflows import graph, source
from tests.waiting import until_async

A = 'agent:ac_' + 'a' * 24
B = 'agent:ac_' + 'b' * 24


def setup(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    day = SimpleNamespace(elsewhere=lambda sid: SimpleNamespace(local_only=True))
    hub = SimpleNamespace(resources=SimpleNamespace(projects=store))
    runner = WorkflowService(hub, day)
    # Exercise the canonical admission and driver with a controlled, tiny worker.
    runner.steps.validate = lambda *args: {'available': True, 'steps': []}
    studies = Studies(store, runner, Operations(tmp_path))
    saved = studies.create('s1', actor=A, label='Client A', request='study-0001', title='Compare',
                           objective='Find a useful result', criteria='Review evidence', budget_usd=0)
    image = source(runner.workflows)
    definition = runner.workflows.create('Inspect', graph(), 'a' * 32)
    return studies, runner, saved, image, definition


def test_detached_work_and_continuation_preserve_original_receipts(tmp_path):
    async def go():
        s, runner, study, image, definition = setup(tmp_path)
        gate = threading.Event()
        calls = []
        def execute(claim):
            calls.append(claim['attempt']['id'])
            assert gate.wait(10)
            return {'status': 'completed', 'output': identity(image)}
        runner.steps.execute = execute
        fields = dict(request='experiment-0001', workflow=definition.id, revision=1, inputs=[identity(image)])
        operation, made = await s.start_workflow(study.id, 's1', actor=A, label='Client A', **fields)
        assert made and operation['by'] == A
        await until_async(lambda: bool(calls))
        # A no longer participates; B reads the same locator while Hooke still owns it.
        assert len((await s.evidence(study.id, 's1'))['operations']) == 1
        again, made = await s.start_workflow(study.id, 's1', actor=B, label='Client B', **fields)
        assert not made and again['id'] == operation['id'] and again['by'] == A
        gate.set()
        await until_async(lambda: not runner.tasks)
        result = await s.operation(s.operations.get(operation['id']))
        assert result['state'] == 'succeeded' and len(calls) == 1
        schema.check(result, 'operation')
        ref = identity(image)
        actual = s.evidence_read(study.id, 's1', ref, actor=B, label='Client B')
        assert actual['document'] == image.document and actual['untrusted']
        result = s.publish(study.id, 's1', request='claim-0001', statement='The controlled output was retained.',
            evidence=[{'artifact': ref, 'relation': 'supports'}], depends_on=[], actor=B, label='Client B')
        assert result['claims'][0]['by'] == B and result['experiments']['experiment-0001']['by'] == A
        # Reopening operations must not overwrite the workflow's completed state.
        s.operations = Operations(tmp_path)
        assert (await s.operation(s.operations.get(operation['id'])))['state'] == 'succeeded'
        await runner.close()
    asyncio.run(go())


def test_exact_evidence_scope_and_claim_dependencies(tmp_path):
    s, runner, study, image, _ = setup(tmp_path)
    wrong = {**identity(image), 'session': 'other'}
    with pytest.raises(ValueError):
        s.evidence_read(study.id, 's1', wrong, actor=B, label='B')
    with pytest.raises(ValueError):
        s.evidence_read(study.id, 's1', {**identity(image), 'digest': '0'*64}, actor=B, label='B')
    fields = dict(request='claim-0001', statement='Observed synthetic result',
                  evidence=[{'artifact': identity(image), 'relation': 'supports'}], actor=A, label='A')
    with pytest.raises(InvalidProject):
        s.publish(study.id, 's1', depends_on=['future'], **fields)
    first = s.publish(study.id, 's1', depends_on=[], **fields)
    assert s.publish(study.id, 's1', depends_on=[], **fields) == first
    revised = s.challenge(study.id, 's1', request='challenge-0001', claim='claim-0001',
        reason='The output is synthetic, not biological validation.',
        evidence=[{'artifact': identity(image), 'relation': 'challenges'}], actor=B, label='B')
    assert revised['claims'] == first['claims'] and revised['claimChanges'][0]['by'] == B


def test_matrix_schema_rejects_unbounded_or_ambiguous_identity():
    ref = {'id': 'p'+'a'*32, 'session':'s1', 'revision':1, 'digest':'b'*64}
    value = {'artifact':ref,'component':'matrix','format':'h5ad','shape':[2,3],
             'dtype':'float32','values':'counts','rows':'cells','columns':'genes','sparse':'csr'}
    schema.check(value, 'annotated-matrix')
    for changed in ({'shape':[2,3,4]}, {'shape':[-1,3]}, {'artifact':{**ref,'revision':0}}, {'path':'/private/file'}):
        assert schema.problems({**value, **changed}, 'annotated-matrix')


def test_every_study_entrance_refuses_researcher_nodes_before_operation_admission(tmp_path):
    async def go():
        s,runner,study,image,_=setup(tmp_path)
        g=graph();g['nodes'][0].update(kind='researcher',settings={
            'question':'Explain','criteria':'Cite evidence','provider':'gemini-google',
            'maximumUsd':.1,'seconds':1,'tools':[],'output':'report'})
        definition=runner.workflows.create('Unqualified recursive allowance',g,'e'*32)
        # Even a future backend that reports ready must not bypass the Study allowance boundary.
        fields=dict(request='unqualified-nested-research',workflow=definition.id,revision=1,inputs=[identity(image)])
        with pytest.raises(InvalidProject,match='allowance is qualified'):
            await s.start_workflow(study.id,'s1',actor=A,label='Client A',**fields)
        assert s.operations.operations()['operations']==[]
        assert not s.read(study.id,'s1').get('experiments')
        assert not runner.tasks
    asyncio.run(go())


def test_lost_admission_response_never_dispatches_on_retry(tmp_path, monkeypatch):
    async def go():
        s, runner, study, image, definition = setup(tmp_path)
        original = runner.command
        async def lost(body, **kwargs):
            if body['action'] == 'start':
                raise OSError('Synthetic loss before the workflow admission receipt')
            return await original(body, **kwargs)
        monkeypatch.setattr(runner, 'command', lost)
        fields = dict(request='lost-admission-1', workflow=definition.id, revision=1, inputs=[identity(image)])
        with pytest.raises(OSError):
            await s.start_workflow(study.id, 's1', actor=A, label='A', **fields)
        monkeypatch.setattr(runner, 'command', original)
        operation, made = await s.start_workflow(study.id, 's1', actor=B, label='B', **fields)
        assert not made and operation['state'] == 'interrupted'
        assert not runner.tasks and not runner.workflows.store._folder(operation['execution']['id']).exists()
        with pytest.raises(Exception, match='different work'):
            await s.start_workflow(study.id, 's1', actor=B, label='B', **{**fields, 'revision': 2})
    asyncio.run(go())


def test_stopped_study_retry_reads_original_and_stop_waits_for_actual_work(tmp_path):
    async def go():
        s, runner, study, image, definition = setup(tmp_path)
        gate = threading.Event()
        calls = []
        def execute(claim):
            calls.append(claim)
            assert gate.wait(10)
            return {'status':'completed', 'output':identity(image)}
        runner.steps.execute = execute
        fields = dict(request='stop-experiment-1', workflow=definition.id, revision=1, inputs=[identity(image)])
        operation, _ = await s.start_workflow(study.id, 's1', actor='studio', label='You', **fields)
        schema.check(operation, 'operation')
        await until_async(lambda: bool(calls))
        stopped = await s.control(study.id, 's1', 'stop', actor=B)
        assert stopped['study']['stopPending'] and stopped['study']['lastControl']['by'] == B
        # Retired/unavailable capabilities or a stopped Study do not block receipt retrieval.
        runner.steps.validate = lambda *args: {'available': False, 'steps':[{'reason':'Synthetic unavailable runtime'}]}
        again, made = await s.start_workflow(study.id, 's1', actor=B, label='B', **fields)
        assert not made and again['id'] == operation['id'] and len(calls) == 1
        gate.set()
        await until_async(lambda: not runner.tasks)
        assert not s.read(study.id, 's1')['stopPending']
        await runner.close()
    asyncio.run(go())
