"""Existing local harness admission using synthetic manager receipts; no provider calls."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.door.schema import check
from orchestrator.research import study_researchers
from orchestrator.research.studies import service
from orchestrator.scientific_projects import ProjectStore, InvalidProject
from orchestrator.scientific_provenance import guard


def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(study_researchers, 'engine_key_refusal', lambda _: None)
    manager = SimpleNamespace(sessions={}, calls=0, fail_reply=False)
    async def launch(prompt, **kwargs):
        manager.calls += 1
        researcher = SimpleNamespace(rid='r123456', day='2026-10-28', sitting='s1', origin=kwargs['origin'],
            status='running', result=None, finding_revisions=[], engine='synthetic', spent=0, spent_estimated=0,
            _attempt=SimpleNamespace(id='original-run'), history=[], result_run={'runId':'original-run'},
            usage_runs={'original-run':{'spent':0, 'estimated':0, 'sequence':-1,
                'job':{'status':'dispatched', 'dispatchedAt':1, 'origin':kwargs['origin'], 'configuredEngine':'synthetic'}}})
        manager.sessions[researcher.rid] = researcher
        if manager.fail_reply:
            raise OSError('Synthetic lost launch response')
        return researcher
    async def cancel(rid, **kwargs):
        manager.sessions[rid].status = 'cancelled'
        manager.sessions[rid].usage_runs['original-run']['job']['status'] = 'cancelled'
    manager.start, manager.cancel = launch, cancel
    built = SimpleNamespace(paths=SimpleNamespace(home=tmp_path), research=manager,
        hub=SimpleNamespace(resources=SimpleNamespace(projects=ProjectStore(tmp_path/'projects')),
                            settings=SimpleNamespace(research='synthetic')),
        day=SimpleNamespace(sid='s1',local_only=False,elsewhere=lambda _:SimpleNamespace(local_only=False)))
    studies = service(built)
    study = studies.create('s1', actor='studio', label='You', request='study-local-001', title='Retained Study',
        objective='Compare measured results', criteria='Keep original evidence', budget_usd=10)
    return built, studies, study


def test_local_launch_retry_and_collected_evidence_survive_stop(tmp_path, monkeypatch):
    async def go():
        built, studies, study = setup(tmp_path, monkeypatch)
        check(study_researchers.readiness(built, study.id, 's1'), 'harness-readiness')
        fields = dict(request='research-001', brief='Read prior evidence', effort='quick')
        a, b = await asyncio.gather(*(study_researchers.start(built, study.id, 's1', **fields) for _ in range(2)))
        assert a['researcher'] == b['researcher'] and built.research.calls == 1
        r = built.research.sessions[a['researcher']]
        r.status, r.result = 'done', {'report':'Synthetic finding; no biological conclusion.'}
        r.spent, r.spent_estimated = .02, .01
        usage = r.usage_runs['original-run']; usage.update(spent=.02, estimated=.01, sequence=1)
        usage['job']['status'] = 'done'
        retained = studies.read(study.id, 's1')
        task = retained['tasks']['research-001']
        assert task['status'] == 'completed' and task['chargedMicrousd'] == 30000 and task['usageEstimated']
        artifact = task['result']['artifact']
        assert studies.store.read(artifact['id'], 's1').document['state']['researchFindings']['result'] == r.result
        assert studies.read(study.id, 's1') == retained
        await studies.control(study.id, 's1', 'stop', actor='studio')
        built.day.local_only = True
        assert (await study_researchers.start(built, study.id, 's1', **fields))['researcher'] == r.rid
        assert built.research.calls == 1
    asyncio.run(go())


def test_collection_uses_original_attempt_and_reconciles_late_usage(tmp_path, monkeypatch):
    async def go():
        built, studies, study = setup(tmp_path, monkeypatch)
        reply = await study_researchers.start(built, study.id, 's1', request='research-001', brief='Do research', effort='quick')
        r = built.research.sessions[reply['researcher']]
        original = r.usage_runs['original-run']
        original.update(spent=.04, estimated=.10, sequence=1)
        original['job']['status'] = 'done'
        r.history = [{'runId':'original-run', 'result':{'report':'Original retained result'}}]
        r.result_run, r.result, r._attempt = {'runId':'follow-up'}, {'report':'Unrelated result'}, SimpleNamespace(id='follow-up')
        r.origin = {'asked':'Later user follow-up'}
        r.engine = 'different-follow-up-engine'
        r.spent = 99  # lifetime cost must not enter this Study
        first = studies.read(study.id, 's1')
        task = first['tasks']['research-001']
        assert task['chargedMicrousd'] == 140000 and task['usageEstimated']
        artifact = task['result']['artifact']
        assert studies.store.read(artifact['id'], 's1').document['state']['researchFindings']['result']['report'] == 'Original retained result'
        assert studies.store.read(artifact['id'], 's1').document['state']['researchFindings']['configuredEngine'] == 'synthetic'
        original.update(spent=.06, estimated=0, sequence=2)
        second = studies.read(study.id, 's1')['tasks']['research-001']
        assert second['chargedMicrousd'] == 60000 and not second['usageEstimated']
        assert second['result'] == task['result']  # scientific result is immutable through billing updates
        await studies.control(study.id, 's1', 'stop', actor='studio')
        assert r.status == 'running'  # Stop did not cancel the unrelated follow-up
    asyncio.run(go())


@pytest.mark.parametrize('status', ['timeout', 'over_budget', 'interrupted'])
def test_unsuccessful_original_attempt_does_not_remain_running(tmp_path, monkeypatch, status):
    async def go():
        built, studies, study = setup(tmp_path, monkeypatch)
        reply = await study_researchers.start(built, study.id, 's1', request='research-001', brief='Do research', effort='quick')
        r = built.research.sessions[reply['researcher']]
        usage = r.usage_runs['original-run']; usage['job']['status'] = status
        if status == 'interrupted':
            usage['outcomeUnknown'] = True
            assert studies.read(study.id, 's1')['tasks']['research-001']['status'] == 'unknown'
            assert studies.read(study.id, 's1')['heldMicrousd'] > 0
        else:
            usage.update(spent=.01, sequence=1)
            assert studies.read(study.id, 's1')['tasks']['research-001']['status'] == 'failed'
    asyncio.run(go())


def test_lost_local_reply_reconciles_original_without_a_second_provider_call(tmp_path, monkeypatch):
    async def go():
        built, studies, study = setup(tmp_path, monkeypatch)
        built.research.fail_reply = True
        fields = dict(request='research-001', brief='Read prior evidence', effort='quick')
        with pytest.raises(OSError):
            await study_researchers.start(built, study.id, 's1', **fields)
        assert studies.read(study.id, 's1')['tasks']['research-001']['status'] == 'unknown'
        reply = await study_researchers.start(built, study.id, 's1', **fields)
        assert reply['researcher'] == 'r123456' and built.research.calls == 1
        with pytest.raises(InvalidProject):
            await study_researchers.start(built, study.id, 's1', **{**fields, 'brief':'Different work'})
    asyncio.run(go())


def test_local_only_refuses_before_reservation_or_provider_dispatch(tmp_path, monkeypatch):
    async def go():
        built, studies, study = setup(tmp_path, monkeypatch)
        built.day.local_only = True
        with pytest.raises(InvalidProject, match='local only'):
            await study_researchers.start(built, study.id, 's1', request='research-001', brief='Do research', effort='quick')
        assert not studies.read(study.id, 's1')['tasks'] and built.research.calls == 0
    asyncio.run(go())


def test_project_switch_during_admission_releases_without_dispatch(tmp_path, monkeypatch):
    async def go():
        built, studies, study = setup(tmp_path, monkeypatch)
        original = studies.efforts.claim
        def switch(*args, **kwargs):
            value = original(*args, **kwargs)
            built.day.sid = 'other-project'
            return value
        monkeypatch.setattr(studies.efforts, 'claim', switch)
        with pytest.raises(InvalidProject, match='original Project changed'):
            await study_researchers.start(built, study.id, 's1', request='research-001', brief='Do research', effort='quick')
        kept = studies.read(study.id, 's1')
        assert kept['tasks']['research-001']['status'] == 'cancelled'
        assert kept['heldMicrousd'] == kept['spentMicrousd'] == built.research.calls == 0
    asyncio.run(go())


def test_uncertain_child_stop_is_reported_without_claiming_termination(tmp_path, monkeypatch):
    async def go():
        from orchestrator.research.ipc import WorkerGone
        built, studies, study = setup(tmp_path, monkeypatch)
        await study_researchers.start(built, study.id, 's1', request='research-001', brief='Do research', effort='quick')
        async def unavailable(*args, **kwargs):
            raise WorkerGone('Synthetic worker termination unconfirmed')
        built.research.cancel = unavailable
        result = await studies.control(study.id, 's1', 'stop', actor='studio')
        assert result['study']['stopPending']
        assert result['controlProblems'] == ['Synthetic worker termination unconfirmed']
        assert studies.read(study.id, 's1')['controlProblems'] == result['controlProblems']
        assert result['study']['heldMicrousd'] > 0
    asyncio.run(go())


def test_study_completion_does_not_start_unreserved_automatic_presentation(tmp_path, monkeypatch):
    from tests.test_research_versions import lesson_session
    from unittest.mock import Mock
    async def go():
        manager, researcher = lesson_session(tmp_path)
        researcher.origin = {'study':'p'+'a'*32, 'studyRequest':'research-001'}
        manager._new_attempt(researcher)
        manager.hub.settings.presentation = 'auto'
        begin = Mock(); monkeypatch.setattr(manager.presentations, 'begin', begin)
        manager._finish(researcher, 'done', {'title':'Saved result', 'summary':'Synthetic result',
            'report':{'methods':'Fixture','observations':'Fixture','measurements':'Fixture','limitations':'Synthetic'}})
        begin.assert_not_called()
        assert researcher.finding_revisions and researcher.result
    asyncio.run(go())


@pytest.mark.parametrize('key', ['researchEffort', 'researchFindings', 'matrixOperation'])
def test_ordinary_document_save_cannot_forge_research_receipts(key):
    with pytest.raises(InvalidProject, match='provenance'):
        guard(None, {'kind':'note','state':{key:{'fake':True}}})
