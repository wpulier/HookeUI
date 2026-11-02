"""Retained research intent and assignments; never an execution or model runner."""
from __future__ import annotations

import time

from .efforts import KEY, MAX_TASKS, fingerprint, text
from ..door.schema import check
from ..scientific_comparisons import exact, identity
from ..scientific_projects import Conflict, InvalidProject


class Coordination:
    def __init__(self, studies, clock=time.time):
        self.studies = studies
        self.efforts = studies.efforts
        self.clock = clock

    def read(self, study, session):
        saved = self.efforts.read(study, session)
        state = saved.document['state'][KEY]
        assignments = []
        for key, task in state['tasks'].items():
            if task['specification'].get('provider') != 'collaboration':
                continue
            row = {'id': key, **task['specification']['assignment'], **task.get('collaboration', {})}
            row['observedState'] = task['status']
            if row.get('leaseUntil', 0) < self.clock() and task['status'] == 'running':
                row['observedState'] = 'needs-reconciliation'
            assignments.append(row)
        materials = []
        for ref in (state.get('researchPlan') or {}).get('materials', []):
            try:
                item = exact(self.studies.store, ref, session)
                from ..project_material import describe
                materials.append({'reference': ref, 'title': item.document['title'],
                                  'material': describe(item.document), 'available': True})
            except (ValueError, OSError):
                materials.append({'reference': ref, 'available': False,
                                  'reason': 'This exact retained revision is unavailable; restore or replace the draft selection.'})
        return {'basis': identity(saved), 'plan': state.get('researchPlan'), 'assignments': assignments,
                'capabilityProposals': list(state.get('capabilityProposals', {}).values()),
                'materials': materials,
                'readiness': {'materialAvailable': all(row['available'] for row in materials),
                              'scientificExecution': 'Checked against the selected frozen workflow at admission',
                              'modelAllowance': 'Requires a qualified external harness; connection is not budget enforcement'},
                'experiments': list(state.get('experiments', {}).values()),
                'claims': state.get('claims', []), 'challenges': state.get('claimChanges', [])}

    def propose_capability(self, study, session, *, request, proposal, actor, label):
        """Retain a package for owner review; no filesystem installation or code execution."""
        text(request, 'Proposal request', 128)
        check(proposal, 'capability-proposal')
        package = exact(self.studies.store, proposal['package'], session)
        if package.document['kind'] not in ('source-file', 'source-directory'):
            raise InvalidProject('Choose an exact retained package file or folder')
        key = fingerprint({'actor': actor, 'request': request})[:32]
        row = {'id': key, 'proposal': proposal, 'status': 'awaiting-owner-review',
               **self.studies.attribution(actor, label)}
        def change(state):
            proposals = state.setdefault('capabilityProposals', {})
            if key in proposals:
                if proposals[key] != row:
                    raise Conflict('This proposal request already names another package or scope')
                return False
            if state['status'] != 'active' or len(proposals) >= 32:
                raise InvalidProject('This Study cannot accept another capability proposal')
            proposals[key] = row
            return True
        self.efforts._change(study, session, change)
        return self.read(study, session)

    def save_plan(self, study, session, *, basis, plan, actor, label):
        check(plan, 'research-plan')
        check(basis, 'artifact-reference')
        if basis['id'] != study:
            raise InvalidProject('Use this Study as the plan basis')
        saved = exact(self.studies.store, basis, session)
        for ref in plan['materials']:
            exact(self.studies.store, ref, session)
        basis_plan = saved.document['state'][KEY].get('researchPlan')
        proposed = {**plan, 'status': 'draft', **self.studies.attribution(actor, label)}
        if 'instructionPackage' in plan:
            from .instructions import pin
            proposed['retainedInstructions'] = pin(plan['instructionPackage'])
        def change(state):
            if state['status'] != 'active':
                raise InvalidProject('Continue the Study before preparing a plan')
            old = state.get('researchPlan')
            if old != basis_plan:
                raise Conflict('The research plan changed. Compare the saved plan with your kept draft before saving again')
            ceiling = state.get('ownerPlanLimits')
            if actor != 'studio' and ceiling and any(plan['limits'][key] > ceiling[key] for key in ceiling):
                raise InvalidProject('Only the Project owner can increase approved research limits')
            if old and old.get('status') == 'reviewed':
                state.setdefault('researchPlanHistory', []).append(old)
            state['researchPlan'] = proposed
            return True
        self.efforts._change(study, session, change)
        return self.read(study, session)

    def review_plan(self, study, session, *, basis, actor, label):
        if actor != 'studio':
            raise InvalidProject('The Project owner must review this plan and its limits in Hooke')
        check(basis, 'artifact-reference')
        if basis['id'] != study:
            raise InvalidProject('Review this Study')
        saved = exact(self.studies.store, basis, session)
        basis_plan = saved.document['state'][KEY].get('researchPlan')
        if not basis_plan:
            raise InvalidProject('Save a plan before reviewing it')
        for ref in basis_plan['materials']:
            exact(self.studies.store, ref, session)
        def change(state):
            plan = state.get('researchPlan')
            if state['status'] != 'active' or plan != basis_plan:
                raise Conflict('The plan changed; review the current saved plan')
            if plan['status'] == 'reviewed':
                return False
            plan.update(status='reviewed', reviewedBy=self.studies.attribution(actor, label),
                        reviewedAt=self.clock(), priorExperiments=list(state.get('experiments', {})))
            state['ownerPlanLimits'] = dict(plan['limits'])
            return True
        self.efforts._change(study, session, change)
        return self.read(study, session)

    def propose(self, study, session, *, request, assignment, actor, label):
        text(request, 'Assignment request', 128)
        check(assignment, 'research-assignment')
        for ref in assignment['evidence']:
            exact(self.studies.store, ref, session)
        key = 'assignment:' + fingerprint({'actor': actor, 'request': request})[:32]
        specification = {'provider': 'collaboration', 'assignment': assignment}
        binding = fingerprint(specification)
        attribution = self.studies.attribution(actor, label)
        def change(state):
            old = state['tasks'].get(key)
            if old:
                if old['binding'] != binding:
                    raise Conflict('This request already names a different assignment')
                return False
            if state['status'] != 'active':
                raise InvalidProject('Continue the Study before assigning work')
            if len(state['tasks']) >= MAX_TASKS:
                raise InvalidProject('Study task capacity reached')
            parent = assignment.get('parent')
            if parent and (parent not in state['tasks'] or state['tasks'][parent]['specification'].get('provider') != 'collaboration'):
                raise InvalidProject('The parent must be an existing assignment in this Study')
            for dep in assignment['dependsOn']:
                if dep not in state['tasks'] or state['tasks'][dep]['specification'].get('provider') != 'collaboration':
                    raise InvalidProject('Dependencies must be existing assignments in this Study')
            state['tasks'][key] = {'specification': specification, 'binding': binding, 'status': 'planned',
                'reservedMicrousd': 0, 'chargedMicrousd': None, 'usageEstimated': False,
                'execution': None, 'result': None, 'problem': None,
                'collaboration': {'proposedBy': attribution, 'createdAt': self.clock(), 'operations': []}}
            return True
        self.efforts._change(study, session, change)
        return next(a for a in self.read(study, session)['assignments'] if a['id'] == key)

    def claim(self, study, session, *, assignment, actor, label, harness, model):
        text(harness, 'Harness', 160)
        text(model, 'Model', 160)
        attribution = self.studies.attribution(actor, label)
        def change(state):
            task = self.efforts._task(state, assignment)
            if task['specification'].get('provider') != 'collaboration':
                raise InvalidProject('Choose a research assignment')
            if state['status'] != 'active':
                raise InvalidProject('This Study is not accepting work')
            meta = task['collaboration']
            if task['status'] == 'running':
                if meta['claimedBy']['by'] != actor:
                    raise Conflict('Another contributor owns this assignment; reconcile it before reassignment')
                if (meta['harness'], meta['model']) != (harness, model):
                    raise Conflict('Resume using the recorded harness/model identity')
                meta['leaseUntil'] = self.clock() + 300
                return True
            if task['status'] != 'planned':
                raise Conflict('This assignment is no longer waiting')
            if any(state['tasks'][dep]['status'] != 'completed' for dep in task['specification']['assignment']['dependsOn']):
                raise Conflict('Read the completed dependency results before claiming this assignment')
            task['status'] = 'running'
            meta.update(claimedBy=attribution, claimedAt=self.clock(), leaseUntil=self.clock()+300,
                        harness=harness, model=model)
            return True
        self.efforts._change(study, session, change)
        return self.read(study, session)

    def recover(self, study, session, *, assignment, actor, label):
        """Owner release after checking original executions; never dispatch replacements."""
        if actor != 'studio':
            raise InvalidProject('Only the Project owner can release an assignment for reassignment')
        def reconcile(state):
            task = self.efforts._task(state, assignment)
            meta = task.get('collaboration')
            if meta is None:
                raise InvalidProject('Choose a collaborative assignment')
            if task['status'] == 'planned':
                return False
            if task['status'] not in ('running', 'cancelled'):
                raise Conflict('Only a claimed or stopped assignment can be reconciled')
            recovered = []
            for key, pending in meta.get('pendingAdmissions', {}).items():
                by = meta.get('claimedBy', {}).get('by')
                if not by or not pending.get('execution'):
                    raise Conflict('The original admission is unresolved and lacks its execution identity')
                receipt = self.studies.operations.request_receipt('study:' + key, by=by)
                if receipt is not None:
                    if (receipt.get('project') != session or receipt.get('execution') != pending['execution']
                            or receipt.get('params', {}).get('study') != study):
                        raise Conflict('The original admission receipt does not match this Study')
                    state.setdefault('experiments', {})[key] = {
                        'request': pending['request'], 'operation': receipt['id'], 'execution': receipt['execution'],
                        'specification': receipt['params'], 'by': receipt['by'], 'byLabel': receipt['byLabel']}
                    if receipt['id'] not in meta['operations']:
                        meta['operations'].append(receipt['id'])
                recovered.append((key, pending, receipt))
            for key, pending, receipt in recovered:
                meta.setdefault('reconciledAdmissions', {})[key] = {**pending, 'operation': receipt['id'] if receipt else None,
                    'reconciledBy': actor, 'at': self.clock()}
            meta.pop('pendingAdmissions', None)
            return bool(recovered)
        # Preserve the discovered original execution even if it is still running.
        # A subsequent Stop can now address it; a failed release never hides it again.
        self.efforts._change(study, session, reconcile)
        def change(state):
            task = self.efforts._task(state, assignment)
            meta = task['collaboration']
            if task['status'] in ('planned', 'cancelled'):
                return False
            if task['status'] != 'running' or state['status'] != 'active':
                raise Conflict('Continue the Study before releasing its assignment')
            for op_id in meta.get('operations', []):
                operation = self.studies.operations.get(op_id)
                execution = operation.get('execution') or {}
                if operation.get('project') != session or execution.get('session') != session:
                    raise InvalidProject('The original execution does not belong to this Project')
                observed = self.studies.workflows.observed_status(execution['id'], session)
                if (observed['status'] not in ('completed', 'failed', 'rejected', 'stopped')
                        or observed.get('executionObservation', {}).get('state') in ('interrupted', 'unstarted')):
                    raise Conflict('Original operations are now linked. Inspect or stop them before changing this assignment owner')
            meta.setdefault('priorClaims', []).append({key: meta[key] for key in
                ('claimedBy', 'claimedAt', 'leaseUntil', 'harness', 'model') if key in meta})
            for key in ('claimedBy', 'claimedAt', 'leaseUntil', 'harness', 'model'):
                meta.pop(key, None)
            meta['releasedBy'] = self.studies.attribution(actor, label)
            meta['releasedAt'] = self.clock()
            task['status'] = 'planned'
            return True
        self.efforts._change(study, session, change)
        return self.read(study, session)

    def complete(self, study, session, *, assignment, statement, evidence, limitations, actor, label):
        text(statement, 'Assignment report')
        text(limitations, 'Limitations')
        if type(evidence) is not list or len(evidence) > 32:
            raise InvalidProject('Use at most 32 exact evidence references')
        for ref in evidence:
            check(ref, 'artifact-reference')
            exact(self.studies.store, ref, session)
        result = {'statement': statement, 'evidence': evidence, 'limitations': limitations,
                  **self.studies.attribution(actor, label)}
        def change(state):
            task = self.efforts._task(state, assignment)
            meta = task.get('collaboration', {})
            if meta.get('claimedBy', {}).get('by') != actor:
                raise Conflict('Only the recorded contributor may return this assignment')
            if task['status'] == 'completed':
                if task['result'] != result:
                    raise Conflict('This assignment already has a different report')
                return False
            if task['status'] != 'running':
                raise Conflict('Reconcile the assignment before returning a report')
            if meta.get('pendingAdmissions'):
                raise Conflict('Wait for the original scientific admission to settle or be reconciled')
            for operation_id in meta.get('operations', []):
                operation = self.studies.operations.get(operation_id)
                execution = operation.get('execution', {})
                if operation.get('project') != session or execution.get('session') != session:
                    raise InvalidProject('The linked execution does not belong to this Project')
                observed = self.studies.workflows.observed_status(execution['id'], session)
                if (observed['status'] not in ('completed', 'failed', 'rejected', 'stopped')
                        or observed.get('executionObservation', {}).get('state') in ('interrupted', 'unstarted')):
                    raise Conflict('Wait for the original scientific operation to settle before completing this assignment')
            task.update(status='completed', result=result, chargedMicrousd=0)
            meta.update(report=result, completedAt=self.clock())
            return True
        self.efforts._change(study, session, change)
        return self.read(study, session)
