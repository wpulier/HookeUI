"""Studies own scientific intent and evidence; WorkflowService owns execution.

An experiment is a reference, never a second attempt or worker ledger. Reading a
Study or operation projects the authoritative workflow receipts without dispatch.
"""
from __future__ import annotations

import asyncio
import time

from .efforts import Efforts, KEY, summary, fingerprint, text
from ..door.schema import check
from ..scientific_projects import InvalidProject, Conflict
from ..scientific_comparisons import identity, exact


class Studies:
    def __init__(self, store, workflows, operations):
        self.efforts = Efforts(store)
        self.store, self.workflows, self.operations = store, workflows, operations
        self._locks = {}

    def attribution(self, actor, label):
        from ..workflows import valid_actor
        if not valid_actor(actor):
            raise InvalidProject('Use the authenticated caller identity')
        text(label, 'Caller label', 240)
        return {'by': actor, 'byLabel': label}

    def create(self, session, *, actor, label, **fields):
        self.workflows.owner(session)
        return self.efforts.create(session, attribution=self.attribution(actor, label), **fields)

    def _read_intent(self, study, session):
        self.workflows.owner(session)
        self.collect_local(study, session)
        return summary(self.efforts.read(study, session))

    @staticmethod
    def _observe_stop(row, runs):
        # Stop is intent; original receipts, including absent ones, determine settlement.
        if row['status'] == 'stopped' and any(
                run is None or run['status'] not in ('completed', 'failed', 'rejected', 'stopped')
                for run in runs):
            row['stopPending'] = True
        return row

    def read(self, study, session):
        row = self._read_intent(study, session)
        if row['status'] == 'stopped':
            ids = [item['execution']['id'] for item in row.get('experiments', {}).values()]
            try:
                runs = self.workflows.workflows.statuses(ids, session)
                self._observe_stop(row, (runs.get(id_) for id_ in ids))
            except (ValueError, OSError):
                row['stopPending'] = True
        return row

    def collect_local(self, study, session):
        """Project existing manager receipts; never launch, retry or invent settlement."""
        manager = getattr(self, 'local_manager', None)
        if manager is None:
            return
        from .study_local_results import collect
        collect(self, manager, study, session)

    def list(self, session):
        self.workflows.owner(session)
        return self.efforts.list(session)

    async def start_workflow(self, study, session, *, request, workflow, revision, inputs,
                             actor, label, bindings=None, assignment=None, repeat=None):
        from ..workflows import run_identity
        self.workflows.owner(session)
        self.attribution(actor, label)
        text(request, 'Experiment request identity', 128)
        params = {'study': study, 'workflow': workflow, 'revision': revision, 'inputs': inputs,
                  'bindings': bindings or {}}
        if assignment is not None:
            text(assignment, 'Assignment identity', 128)
            params['assignment'] = assignment
        if repeat is not None:
            if type(repeat) is not dict or set(repeat) != {'id', 'reason'}:
                raise InvalidProject('An explicit repeat needs an identity and scientific reason')
            text(repeat['id'], 'Repeat identity', 128)
            text(repeat['reason'], 'Repeat reason', 2000)
            params['repeat'] = repeat
        workflow_request = fingerprint({'study': study, 'session': session, 'request': request, 'actor': actor})[:32]
        run = run_identity(session, workflow_request)
        execution = {'kind': 'workflow', 'id': run, 'session': session}
        # One admission boundary also serializes explicit Stop with new work.
        lock = self._locks.setdefault((session, study), asyncio.Lock())
        async with lock:
            saved = await asyncio.to_thread(self.efforts.read, study, session)
            state = saved.document['state'][KEY]
            # Older Studies keyed experiments by the caller's raw request. Keep
            # that exact admission on retry; upgrading must never run it again.
            plan = state.get('researchPlan')
            assigned = None
            if assignment is not None:
                assigned = self.efforts._task(state, assignment)
                if (assigned['specification'].get('provider') != 'collaboration'
                        or assigned.get('collaboration', {}).get('claimedBy', {}).get('by') != actor):
                    raise InvalidProject('Use an assignment claimed by this contributor')
            if plan:
                if plan['status'] != 'reviewed':
                    raise InvalidProject('Review the retained research plan before admitting experiments')
                # Scientific identity excludes actor and assignment. Explicit repeat
                # identity/reason distinguishes a justified repeat, not request retries.
                intent = {k: v for k, v in params.items() if k != 'assignment'}
                for previous in state.get('experiments', {}).values():
                    if {k: v for k, v in previous['specification'].items() if k != 'assignment'} == intent:
                        original = self.operations.get(previous['operation'])
                        if assignment is not None:
                            def reuse(current):
                                task = self.efforts._task(current, assignment)
                                operations = task['collaboration']['operations']
                                if original['id'] in operations:
                                    return False
                                self._claim_for_admission(current, assignment, actor)
                                operations.append(original['id'])
                                return True
                            await asyncio.to_thread(self.efforts._change, study, session, reuse)
                        return await self.operation(original), False
                if len(state.get('experiments', {})) >= plan['limits']['experiments']:
                    raise InvalidProject('The reviewed plan experiment allowance is exhausted')
                if actor != 'studio' and assigned is None:
                    raise InvalidProject('Claim an assignment before admitting this plan’s experiment')
                if assigned and (assigned['status'] != 'running' or assigned['collaboration']['leaseUntil'] <= time.time()):
                    raise InvalidProject('Renew the original assignment claim before admitting work')
            legacy = state.get('experiments', {}).get(request)
            if legacy and legacy.get('by') == actor:
                if legacy.get('specification') != params:
                    raise Conflict('This experiment request already belongs to different work')
                original = self.operations.get(legacy['operation'], by=actor)
                if original.get('execution') != legacy.get('execution') or original.get('params') != params:
                    raise InvalidProject('The original experiment receipt does not match; nothing was replayed')
                return await self.operation(original), False
            admission = dict(tool='workflow', project=session, input_revision=str(revision),
                             generation=None, params=params, request_key='study:' + workflow_request)
            record = self.operations.lookup(by=actor, **admission)
            if record is not None and workflow_request in state.get('experiments', {}):
                # An acknowledged retry is a receipt read even after Stop or completion.
                return await self.operation(record), False
            made = False
            command = {'action': 'validate', 'workflow': workflow, 'revision': revision,
                       'session': session, 'inputs': inputs, **({'bindings': bindings} if bindings else {})}
            reserved = False
            if assignment is not None:
                def reserve(current):
                    task = self._claim_for_admission(current, assignment, actor)
                    pending = task['collaboration'].setdefault('pendingAdmissions', {})
                    if workflow_request in pending:
                        raise Conflict('Reconcile the retained pending admission before retrying')
                    pending[workflow_request] = {'request': request, 'execution': execution}
                    return True
                await asyncio.to_thread(self.efforts._change, study, session, reserve)
                reserved = True
            try:
                if record is None:
                    if state['status'] != 'active':
                        raise InvalidProject('Continue this Study before admitting more work')
                    if len(state.get('experiments', {})) >= 256:
                        raise InvalidProject('Retain this Study and start another before adding experiments')
                    definition = await asyncio.to_thread(self.workflows.workflows.definition, workflow, revision)
                    if any(n['kind'] == 'researcher' for n in definition.document['state']['workflow']['graph']['nodes']):
                        raise InvalidProject('Study workflows cannot launch researcher nodes until their allowance is qualified')
                    readiness = await self.workflows.command(command, actor=actor)
                    if not readiness['readiness']['available']:
                        raise InvalidProject('; '.join(s['reason'] for s in readiness['readiness']['steps'] if s['reason']))
                    record, made = await self.operations.admit(by=actor, label=label, **admission,
                                                              work=None, execution=execution)
                def link(state):
                    if state['status'] != 'active':
                        raise Conflict('The Study stopped accepting work during admission')
                    if assignment is not None:
                        self._claim_for_admission(state, assignment, actor)
                    rows = state.setdefault('experiments', {})
                    old = rows.get(workflow_request)
                    row = {'request': request, 'operation': record['id'], 'execution': execution,
                           'specification': params, 'by': record['by'], 'byLabel': record['byLabel']}
                    if old:
                        if old['specification'] != params:
                            raise Conflict('This experiment request already belongs to different work')
                        return False
                    if len(rows) >= 256:
                        raise InvalidProject('Retain this Study and start another before adding experiments')
                    rows[workflow_request] = row
                    if assignment is not None:
                        meta = self.efforts._task(state, assignment)['collaboration']
                        if record['id'] not in meta['operations']:
                            meta['operations'].append(record['id'])
                        meta.get('pendingAdmissions', {}).pop(workflow_request, None)
                    return True
                await asyncio.to_thread(self.efforts._change, study, session, link)
                # A lost response/reconnect only reads an existing run; no replay on read.
                if made:
                    command.update(action='start', request=workflow_request)
                    await self.workflows.command(command, actor=record['by'])
                return await self.operation(record), made
            except BaseException:
                # Failed readiness may release the reservation. Once admitted, keep
                # uncertain ownership until reconciliation; never silently replay it.
                if reserved and record is None:
                    def release(current):
                        pending = self.efforts._task(current, assignment)['collaboration'].get('pendingAdmissions', {})
                        return pending.pop(workflow_request, None) is not None
                    await asyncio.to_thread(self.efforts._change, study, session, release)
                raise

    def _claim_for_admission(self, state, assignment, actor):
        task = self.efforts._task(state, assignment)
        meta = task.get('collaboration', {})
        if (state['status'] != 'active' or task['status'] != 'running'
                or meta.get('claimedBy', {}).get('by') != actor
                or meta.get('leaseUntil', 0) <= time.time()):
            raise Conflict('Renew or reconcile the original assignment claim before admitting work')
        return task

    async def operation(self, record, *, observed=None):
        execution = record.get('execution')
        if not execution:
            return record
        check(execution, 'operation-execution')
        await asyncio.to_thread(self.workflows.owner, execution['session'])
        try:
            run = observed if observed is not None else await asyncio.to_thread(self.workflows.observed_status, execution['id'], execution['session'])
            if run['id'] != execution['id'] or run['session'] != execution['session']:
                raise InvalidProject('Workflow observation belongs to another admission')
        except (FileNotFoundError, InvalidProject) as error:
            return {**record, 'state': 'interrupted', 'outcome': {
                'says': 'The original workflow admission has no readable run. Nothing was replayed.',
                'detail': str(error), 'next': 'Inspect the original Study admission before continuing.'}}
        status = run['status']
        state = {'completed': 'succeeded', 'failed': 'failed', 'rejected': 'failed',
                 'stopped': 'cancelled', 'stopping': 'cancelRequested', 'uncertain': 'interrupted'}.get(status, 'running')
        if run.get('executionObservation', {}).get('state') == 'interrupted':
            state = 'interrupted'
        elif run.get('executionObservation', {}).get('state') == 'unstarted':
            state = 'admitted'
        return {**record, 'state': state, 'workflow': run,
                'progress': {'done': sum(a['status'] == 'completed' for a in run['attempts']),
                             'of': None, 'says': 'Workflow ' + status},
                'outcome': {'workflowRun': {k: run[k] for k in ('id', 'session', 'revision', 'digest')},
                            'says': 'Read the retained workflow attempts for results, failures and review.'}}

    async def evidence(self, study, session):
        row = await asyncio.to_thread(self._read_intent, study, session)
        records = [self.operations.get(item['operation']) for item in row.get('experiments', {}).values()]
        if any(record['project'] != session for record in records):
            raise InvalidProject('Study operation belongs to another Project')
        executions = [record['execution'] for record in records if record.get('execution')]
        for execution in executions:
            check(execution, 'operation-execution')
            if execution['session'] != session:
                raise InvalidProject('Study execution belongs to another Project')
        observed = await asyncio.to_thread(self.workflows.observed_statuses, [e['id'] for e in executions], session)
        runs = [await self.operation(record, observed=observed.get(record.get('execution', {}).get('id'))) for record in records]
        self._observe_stop(row, (operation.get('workflow') for operation in runs))
        return {'study': row, 'operations': runs}

    def evidence_read(self, study, session, reference, *, actor, label):
        self.efforts.read(study, session)
        check(reference, 'artifact-reference')
        saved = exact(self.store, reference, session)
        attribution = self.attribution(actor, label)
        def note(state):
            rows = state.setdefault('evidenceReads', [])
            row = {'artifact': identity(saved), **attribution}
            if row in rows:
                return False
            if len(rows) >= 512:
                raise InvalidProject('Evidence read capacity reached; retain this Study')
            rows.append(row)
            return True
        self.efforts._change(study, session, note)
        return {'artifact': identity(saved), 'document': saved.document,
                'contentClass': 'retained-project-document', 'untrusted': True}

    def publish(self, study, session, *, request, statement, evidence, depends_on, actor, label,
                limitations=''):
        row = {'request': request, 'statement': statement, 'evidence': evidence, 'dependsOn': depends_on,
               'limitations': limitations, **self.attribution(actor, label), 'at': time.time()}
        check(row, 'claim')
        for item in evidence:
            exact(self.store, item['artifact'], session)
        def change(state):
            rows = state.setdefault('claims', [])
            prior = next((c for c in rows if c['request'] == request), None)
            if prior:
                if {k: v for k, v in prior.items() if k != 'at'} != {k: v for k, v in row.items() if k != 'at'}:
                    raise Conflict('This claim request already belongs to another statement or author')
                return False
            if len(rows) >= 128 or any(d not in {c['request'] for c in rows} for d in depends_on):
                raise InvalidProject('Claims must depend on existing claims within this Study')
            rows.append(row)
            return True
        return summary(self.efforts._change(study, session, change))

    def challenge(self, study, session, *, request, claim, reason, evidence, actor, label):
        row = {'request': request, 'claim': claim, 'reason': reason, 'evidence': evidence,
               **self.attribution(actor, label), 'at': time.time()}
        check(row, 'claim-change')
        for item in evidence:
            exact(self.store, item['artifact'], session)
        def change(state):
            if claim not in {c['request'] for c in state.get('claims', [])}:
                raise InvalidProject('Challenge an existing Study claim')
            rows = state.setdefault('claimChanges', [])
            old = next((c for c in rows if c['request'] == request), None)
            if old:
                if {k:v for k,v in old.items() if k != 'at'} != {k:v for k,v in row.items() if k != 'at'}:
                    raise Conflict('This change request belongs to another challenge')
                return False
            if len(rows) >= 128:
                raise InvalidProject('Claim change capacity reached')
            rows.append(row)
            return True
        return summary(self.efforts._change(study, session, change))

    async def control(self, study, session, action, *, actor):
        from .ipc import WorkerGone
        self.workflows.owner(session)
        self.attribution(actor, actor)
        lock = self._locks.setdefault((session, study), asyncio.Lock())
        async with lock:
            await asyncio.to_thread(self.collect_local, study, session)
            saved = await asyncio.to_thread(self.efforts.control, study, session, action, actor=actor)
            problems = []
            if action == 'stop':
                manager = getattr(self, 'local_manager', None)
                if manager is not None:
                    for task in saved.document['state'][KEY]['tasks'].values():
                        execution = task.get('execution') or {}
                        if execution.get('provider') == 'local-researcher' and task['status'] == 'cancel_requested':
                            researcher = manager.sessions.get(execution['id'])
                            attempt = getattr(researcher, '_attempt', None)
                            if attempt is None or attempt.id != execution.get('runId'):
                                # A later follow-up on the same researcher is a different owned action.
                                continue
                            try:
                                await manager.cancel(execution['id'], confirmed=True)
                            except (ValueError, OSError, RuntimeError, WorkerGone) as error:
                                problems.append(str(error))
                for item in saved.document['state'][KEY].get('experiments', {}).values():
                    try:
                        await self.workflows.command({'action': 'stop', 'run': item['execution']['id'],
                                                      'session': session}, actor=actor)
                    except (ValueError, OSError) as error:
                        problems.append(str(error))
            def retain_problems(state):
                values = [p[:2000] for p in problems]
                if state.get('controlProblems', []) == values:
                    return False
                state['controlProblems'] = values
                return True
            await asyncio.to_thread(self.efforts._change, study, session, retain_problems)
            return {**await self.evidence(study, session), 'controlProblems': problems}

    async def command(self, body, session, *, actor, label):
        """Shared scientific actions, for a local researcher or another admitted caller."""
        if type(body) is not dict:
            raise InvalidProject('Supply one Study command')
        fields = dict(body)
        action = fields.pop('action', None)
        study = fields.pop('study', None)
        if action == 'study-list' and not fields and study is None:
            return {'studies': await asyncio.to_thread(self.list, session)}
        if action == 'study-create' and study is None and set(fields) == {
                'request', 'title', 'objective', 'criteria', 'budgetUsd', 'inputs'}:
            fields['budget_usd'] = fields.pop('budgetUsd')
            return {'study': summary(await asyncio.to_thread(self.create, session, actor=actor, label=label, **fields))}
        await asyncio.to_thread(self.read, study, session)
        if action == 'study-read' and not fields:
            return await self.evidence(study, session)
        if action == 'study-evidence' and set(fields) == {'reference'}:
            return await asyncio.to_thread(self.evidence_read, study, session, fields['reference'], actor=actor, label=label)
        if action == 'study-claim' and set(fields) == {'request', 'statement', 'evidence', 'dependsOn', 'limitations'}:
            fields['depends_on'] = fields.pop('dependsOn')
            return {'study': await asyncio.to_thread(self.publish, study, session, actor=actor, label=label, **fields)}
        if action == 'study-run' and {'request', 'workflow', 'revision', 'inputs'} <= set(fields) and not set(fields)-{
                'request', 'workflow', 'revision', 'inputs', 'bindings'}:
            operation, made = await self.start_workflow(study, session, actor=actor, label=label, **fields)
            return {'operation': operation, 'made': made}
        raise InvalidProject('Choose a supported Study command with its declared fields')


def service(built):
    if getattr(built.hub, 'agent_operations', None) is None:
        from ..door.operations import Operations
        built.hub.agent_operations = Operations(built.paths.home)
    built.hub.study_research_manager = getattr(built, 'research', None)
    return for_workspace(built.hub, built.day)


def for_workspace(hub, day):
    current = getattr(hub, 'studies', None)
    if current is None:
        from ..workflow_service import service as workflows
        current = hub.studies = Studies(hub.resources.projects, workflows(hub, day), hub.agent_operations)
    current.local_manager = getattr(hub, 'study_research_manager', None)
    return current
