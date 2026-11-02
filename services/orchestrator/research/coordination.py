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
        document = saved.document
        state = document['state'][KEY]
        if state['status'] != 'active':
            raise InvalidProject('Continue the Study before preparing a plan')
        old = state.get('researchPlan')
        if old and old.get('status') == 'reviewed':
            raise Conflict('The reviewed plan is pinned; retain it and propose a follow-up assignment')
        state['researchPlan'] = {**plan, 'status': 'draft', **self.studies.attribution(actor, label)}
        if 'instructionPackage' in plan:
            from .instructions import pin
            state['researchPlan']['retainedInstructions'] = pin(plan['instructionPackage'])
        self.studies.store.save(study, session, document, expected_revision=saved.revision)
        return self.read(study, session)

    def review_plan(self, study, session, *, basis, actor, label):
        check(basis, 'artifact-reference')
        if basis['id'] != study:
            raise InvalidProject('Review this Study')
        saved = exact(self.studies.store, basis, session)
        document = saved.document
        state = document['state'][KEY]
        plan = state.get('researchPlan')
        if not plan or state['status'] != 'active':
            raise InvalidProject('Save a plan in an active Study before reviewing it')
        if plan['status'] == 'reviewed':
            return self.read(study, session)
        for ref in plan['materials']:
            exact(self.studies.store, ref, session)
        plan.update(status='reviewed', reviewedBy=self.studies.attribution(actor, label), reviewedAt=self.clock())
        self.studies.store.save(study, session, document, expected_revision=saved.revision)
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
            for operation_id in meta.get('operations', []):
                operation = self.studies.operations.get(operation_id)
                execution = operation.get('execution', {})
                if operation.get('project') != session or execution.get('session') != session:
                    raise InvalidProject('The linked execution does not belong to this Project')
                observed = self.studies.workflows.observed_status(execution['id'], session)
                if observed['status'] not in ('completed', 'failed', 'rejected', 'stopped'):
                    raise Conflict('Wait for the original scientific operation to settle before completing this assignment')
            task.update(status='completed', result=result, chargedMicrousd=0)
            meta.update(report=result, completedAt=self.clock())
            return True
        self.efforts._change(study, session, change)
        return self.read(study, session)
