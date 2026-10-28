"""Project the existing local harness's pinned attempt, including delayed usage receipts."""
import copy

from .efforts import KEY, FINAL, fingerprint
from ..scientific_comparisons import identity


def original_executions(manager, study, session, request):
    found = []
    for researcher in manager.sessions.values():
        if researcher.sitting != session:
            continue
        for run_id, usage in researcher.usage_runs.items():
            origin = usage.get('job', {}).get('origin') or {}
            if origin.get('study') == study and origin.get('studyRequest') == request:
                found.append((researcher, run_id))
    return found


def execution(researcher, run_id):
    job = researcher.usage_runs.get(run_id, {}).get('job', {})
    return {'provider':'local-researcher', 'id':researcher.rid, 'day':researcher.day, 'runId':run_id,
            'configuredEngine':job.get('configuredEngine')}


def collect(studies, manager, study, session):
    saved = studies.efforts.read(study, session)
    for request, task in saved.document['state'][KEY]['tasks'].items():
        ref = task.get('execution') or {}
        if ref.get('provider') != 'local-researcher':
            continue
        researcher = manager.sessions.get(ref.get('id'))
        if researcher is None or researcher.sitting != session:
            studies.efforts.uncertain(study, session, request, 'The original researcher receipt is not loaded; nothing was replayed.')
            continue
        run_id = ref.get('runId')
        usage = copy.deepcopy(researcher.usage_runs.get(run_id, {}))
        job = usage.get('job') or {}
        origin = job.get('origin') or {}
        if origin.get('study') != study or origin.get('studyRequest') != request:
            studies.efforts.uncertain(study, session, request, 'The exact original researcher attempt is unavailable; inspect its retained receipts.')
            continue
        terminal = {'done':'completed', 'partial':'completed', 'failed':'failed', 'timeout':'failed',
                    'over_budget':'failed', 'cancelled':'cancelled', 'interrupted':'failed'}.get(job.get('status'))
        if terminal is None:
            continue
        if job['status'] == 'interrupted' and usage.get('outcomeUnknown') and task['status'] not in FINAL:
            studies.efforts.uncertain(study, session, request, 'Research was interrupted; provider outcome and usage remain uncertain. Its allowance stays held.')
            continue
        sequence = usage.get('sequence', -1)
        estimated = float(usage.get('estimated') or 0)
        known = sequence >= 0 or not job.get('dispatchedAt')
        charge = round(float(usage.get('spent') or 0) + estimated, 6) if known else task['reservedMicrousd']/1e6
        is_estimate = estimated > 0 or not known or bool(usage.get('outcomeUnknown'))
        if task['status'] not in FINAL:
            result = {'researcher':researcher.rid, 'day':researcher.day, 'runId':run_id,
                      'status':job['status'], 'accepted':False}
            revisions = [copy.deepcopy(r) for r in researcher.finding_revisions if (r.get('run') or {}).get('runId') == run_id]
            finding = revisions[-1]['result'] if revisions else None
            if finding is None and (researcher.result_run or {}).get('runId') == run_id:
                finding = copy.deepcopy(researcher.result)
            if finding is None:
                finding = next((copy.deepcopy(r.get('result')) for r in researcher.history if r.get('runId') == run_id), None)
            if finding is not None:
                content = {'result':finding, 'findingRevisions':revisions, 'researcher':researcher.rid,
                           'runId':run_id, 'configuredEngine':ref.get('configuredEngine'), 'accepted':False}
                document = studies.store.create(session, {'kind':'research-findings', 'title':'Research findings',
                    'state':{'researchFindings':content}, 'resources':[], 'runtimes':[]},
                    identity='p'+fingerprint({'study':study, 'runId':run_id, 'content':content})[:32])
                result['artifact'] = identity(document)
            if task['status'] == 'cancel_requested' and terminal == 'completed':
                terminal = 'cancelled'
                result['note'] = 'Work finished after Stop; the original output is retained without acceptance.'
            studies.efforts.finish(study, session, request, status=terminal, result=result,
                                   actual_usd=charge, usage_estimated=is_estimate)
        studies.efforts.reconcile_usage(study, session, request, execution=ref,
                                        sequence=sequence, actual_usd=charge, estimated=is_estimate)
