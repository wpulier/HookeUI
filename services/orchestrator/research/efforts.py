"""Project-owned research work, with durable admission before external execution.

The existing scientific ProjectStore owns every revision. A task key names one
request for the lifetime of this effort. An uncertain launch keeps its reservation
and must be reconciled; reading or restarting never launches it again.
"""
from __future__ import annotations

import hashlib
from decimal import Decimal, InvalidOperation

from ..scientific_projects import Conflict, InvalidProject, ProjectStore, _json

KIND = 'research-effort'
KEY = 'researchEffort'
FINAL = frozenset(('completed', 'failed', 'cancelled'))
MAX_TASKS = 256


def money(value) -> int:
    """USD to integer microdollars; invalid/rounded-away allowances refuse."""
    if type(value) not in (str, int, float):
        raise InvalidProject('A research allowance must be a finite USD amount')
    try:
        amount = Decimal(str(value)) * 1_000_000
        if not amount.is_finite() or amount < 0 or amount > 1_000_000_000_000:
            raise InvalidOperation
        if amount != amount.to_integral_value():
            raise InvalidOperation
        return int(amount)
    except (InvalidOperation, ValueError):
        raise InvalidProject('Use a nonnegative USD amount with at most six decimal places') from None


def text(value, label, limit=8000):
    if type(value) is not str or not 0 < len(value.strip()) <= limit:
        raise InvalidProject(f'{label} needs 1 to {limit} characters')
    return value


def fingerprint(value):
    return hashlib.sha256(_json(value)).hexdigest()


def summary(saved):
    state = saved.document['state'][KEY]
    held = sum(t['reservedMicrousd'] for t in state['tasks'].values() if t['chargedMicrousd'] is None)
    spent = sum(t['chargedMicrousd'] or 0 for t in state['tasks'].values())
    return {'id': saved.id, 'session': saved.session, 'revision': saved.revision, 'digest': saved.digest,
            **state, 'title': saved.document['title'], 'spentMicrousd': spent, 'heldMicrousd': held,
            'availableMicrousd': max(0, state['capMicrousd'] - spent - held),
            'stopPending': state['status'] == 'stopped' and any(t['status'] not in FINAL for t in state['tasks'].values())}


class Efforts:
    def __init__(self, store: ProjectStore):
        self.store = store

    def create(self, session, *, request, title, objective, criteria, budget_usd, inputs=(), attribution=None):
        text(request, 'Request identity', 128)
        text(title, 'Research title', 240)
        text(objective, 'Research objective')
        text(criteria, 'Success criteria')
        cap = money(budget_usd)
        if not isinstance(inputs, (tuple, list)) or len(inputs) > 64:
            raise InvalidProject('Select at most 64 exact saved inputs')
        selected = []
        for ref in inputs:
            if type(ref) is not dict or set(ref) != {'id', 'revision', 'digest'}:
                raise InvalidProject('An input needs its document id, accepted revision and digest')
            if type(ref['revision']) is not int or ref['revision'] < 1:
                raise InvalidProject('Select an exact accepted input revision')
            saved = self.store.read(ref['id'], session, revision=ref['revision'])
            if saved.digest != ref['digest']:
                raise InvalidProject('The selected input digest does not match its saved revision')
            selected.append(dict(ref))
        state = {'format': 1, 'objective': objective, 'criteria': criteria, 'status': 'active',
                 'capMicrousd': cap, 'inputs': selected, 'tasks': {}, 'findings': []}
        if attribution is not None:
            state['createdBy'] = dict(attribution)
        document = {'kind': KIND, 'title': title, 'state': {KEY: state}, 'resources': [], 'runtimes': []}
        identity = 'p' + fingerprint({'session': session, 'researchRequest': request})[:32]
        self.store.create(session, document, identity=identity)
        return self.read(identity, session)

    def read(self, effort, session):
        saved = self.store.read(effort, session)
        if saved.document['kind'] != KIND or saved.document['state'].get(KEY, {}).get('format') != 1:
            raise InvalidProject('Choose a saved research effort')
        return saved

    def list(self, session):
        rows = []
        for saved in self.store.list(session):
            if saved.document['kind'] != KIND:
                continue
            # Creation is the first retained revision, not the current revision's
            # modification time or a guess based on its Project name.
            first = self.store.read(saved.id, session, revision=1)
            rows.append({**summary(saved), 'createdAt': first.at, 'updatedAt': saved.at})
        return rows

    def _change(self, effort, session, change):
        # Compare-and-save admits concurrent tools against the same durable cap.
        for _ in range(16):
            saved = self.read(effort, session)
            document = saved.document
            if not change(document['state'][KEY]):
                return saved
            try:
                return self.store.save(effort, session, document, expected_revision=saved.revision)
            except Conflict:
                continue
        raise Conflict('Research changed concurrently; retry using the same request identity')

    def reserve(self, effort, session, *, request, specification, maximum_usd):
        text(request, 'Task request identity', 128)
        if type(specification) is not dict or not specification:
            raise InvalidProject('A research task needs an explicit specification')
        if len(_json(specification)) > 32 << 10:
            raise InvalidProject('Keep large data in retained input files')
        maximum = money(maximum_usd)
        binding = fingerprint({'specification': specification, 'maximumMicrousd': maximum})
        def change(state):
            existing = state['tasks'].get(request)
            if existing:
                if existing['binding'] != binding:
                    raise Conflict('This task request already belongs to a different experiment')
                return False
            if state['status'] != 'active':
                raise InvalidProject('Continue this research effort before scheduling more work')
            if len(state['tasks']) >= MAX_TASKS:
                raise InvalidProject('Research task capacity reached; retain this effort and start another')
            committed = sum(t['reservedMicrousd'] if t['chargedMicrousd'] is None else t['chargedMicrousd']
                            for t in state['tasks'].values())
            if committed + maximum > state['capMicrousd']:
                raise InvalidProject('This task exceeds the remaining research budget')
            state['tasks'][request] = {'binding': binding, 'specification': specification,
                'status': 'planned', 'reservedMicrousd': maximum, 'chargedMicrousd': None,
                'usageEstimated': False, 'execution': None, 'result': None, 'problem': None}
            return True
        return self._change(effort, session, change)

    def claim(self, effort, session, request):
        """Only the caller that durably claims a planned task may launch it."""
        def change(state):
            task = self._task(state, request)
            if state['status'] != 'active' or task['status'] != 'planned':
                raise Conflict('This task is already claimed or its effort is not active; reconcile it')
            task['status'] = 'launching'
            return True
        return self._change(effort, session, change)

    @staticmethod
    def _task(state, request):
        if type(request) is not str or request not in state['tasks']:
            raise InvalidProject('Unknown research task')
        return state['tasks'][request]

    def started(self, effort, session, request, execution):
        if type(execution) is not dict or not execution or len(_json(execution)) > 8192:
            raise InvalidProject('A task needs a bounded provider execution identity')
        def change(state):
            task = self._task(state, request)
            if task['execution'] is not None:
                if task['execution'] != execution:
                    raise Conflict('This task already has a different execution identity')
                return False
            if task['status'] not in ('launching', 'unknown', 'cancel_requested'):
                raise Conflict('This task was not admitted for execution')
            task['execution'] = execution
            if task['status'] != 'cancel_requested':
                task['status'] = 'running'
            task['problem'] = None
            return True
        return self._change(effort, session, change)

    def uncertain(self, effort, session, request, reason):
        text(reason, 'Reconciliation reason', 2000)
        def change(state):
            task = self._task(state, request)
            if task['status'] in FINAL:
                return False
            if task['status'] == 'planned':
                raise Conflict('An unclaimed task cannot have an uncertain execution')
            if task['problem'] == reason and task['status'] in ('unknown', 'cancel_requested'):
                return False
            task['problem'] = reason
            if task['status'] != 'cancel_requested':
                task['status'] = 'unknown'
            return True
        return self._change(effort, session, change)

    def refused_before_dispatch(self, effort, session, request, reason):
        """Trusted caller has observed refusal before invoking any execution provider."""
        text(reason, 'Admission refusal', 2000)
        def change(state):
            task = self._task(state, request)
            if task['status'] != 'launching' or task['execution'] is not None:
                raise Conflict('Only a known undispatched admission can be released')
            task.update(status='cancelled', chargedMicrousd=0, usageEstimated=False,
                        result={'reason': reason, 'dispatched': False}, problem=None)
            return True
        return self._change(effort, session, change)

    def reconcile_usage(self, effort, session, request, *, execution, sequence, actual_usd, estimated):
        """Trusted producer receipts may settle late usage without rewriting scientific results."""
        amount = money(actual_usd)
        if type(sequence) is not int or sequence < -1 or type(estimated) is not bool:
            raise InvalidProject('Usage reconciliation needs a producer receipt sequence')
        def change(state):
            task = self._task(state, request)
            if task['execution'] != execution or task['status'] not in FINAL:
                raise Conflict('Reconcile only the original completed execution')
            previous = task.get('usageSequence', -2)
            if sequence <= previous:
                return False
            task.update(usageSequence=sequence, chargedMicrousd=amount, usageEstimated=estimated)
            return True
        return self._change(effort, session, change)

    def checkpoint(self, effort, session, request, *, execution, previous, observation):
        """Trusted provider collector only; an old poll cannot replace newer evidence."""
        if type(observation) is not dict or len(_json(observation)) > 32 << 10:
            raise InvalidProject('Keep provider evidence in retained files with a bounded checkpoint')
        def change(state):
            task = self._task(state, request)
            if task['execution'] != execution or task['status'] in FINAL:
                raise Conflict('The provider execution is different or already finished')
            if task.get('observation') == observation:
                return False
            if task.get('observation') != previous:
                raise Conflict('Newer provider evidence is already retained; read before polling again')
            task['observation'] = observation
            return True
        return self._change(effort, session, change)

    def claim_action(self, effort, session, request, action):
        """Retain a remote stop/close intent once, including across a lost reply."""
        if action not in ('interrupt', 'archive'):
            raise InvalidProject('Unsupported managed researcher control')
        def change(state):
            task = self._task(state, request)
            if task['status'] in FINAL or task['execution'] is None:
                raise Conflict('There is no active provider execution to control')
            actions = task.setdefault('managedActions', {})
            if action in actions:
                raise Conflict('This control was already sent or is uncertain; observe before acting again')
            actions[action] = 'pending'
            return True
        return self._change(effort, session, change)

    def staged_input(self, effort, session, request, receipt):
        """Trusted uploader records exact acknowledged inputs before launching the harness."""
        if type(receipt) is not dict or len(_json(receipt)) > 4096:
            raise InvalidProject('Keep a bounded input-upload receipt')
        def change(state):
            task = self._task(state, request)
            if task['execution'] is not None or task['status'] not in ('launching', 'cancel_requested'):
                raise Conflict('This task no longer owns input staging')
            uploads = task.setdefault('stagedInputs', [])
            if receipt in uploads:
                return False
            if len(uploads) >= 32:
                raise InvalidProject('Research input staging capacity reached')
            uploads.append(receipt)
            return True
        return self._change(effort, session, change)

    def action_outcome(self, effort, session, request, action, outcome):
        if outcome not in ('acknowledged', 'unknown'):
            raise InvalidProject('Unsupported provider acknowledgement')
        def change(state):
            task = self._task(state, request)
            actions = task.get('managedActions', {})
            if actions.get(action) != 'pending':
                raise Conflict('This remote control no longer owns the pending attempt')
            actions[action] = outcome
            return True
        return self._change(effort, session, change)

    def finish(self, effort, session, request, *, status, result, actual_usd=None, expected_observation=None,
               usage_estimated=False):
        """Trusted execution service only; missing billing retains the whole reservation as estimated cost."""
        if status not in FINAL or type(result) is not dict or len(_json(result)) > 32 << 10:
            raise InvalidProject('A task needs a terminal outcome and a bounded result receipt')
        actual = money(actual_usd) if actual_usd is not None else None
        def change(state):
            task = self._task(state, request)
            if expected_observation is not None and task.get('observation') != expected_observation:
                raise Conflict('Provider evidence changed after review; inspect the latest retained snapshot')
            if task['status'] in FINAL:
                expected_charge = task['reservedMicrousd'] if actual is None else actual
                if (task['status'] != status or task['result'] != result
                        or task['chargedMicrousd'] != expected_charge or task['usageEstimated'] != (actual is None or usage_estimated)):
                    raise Conflict('A finished task cannot acquire a different result')
                return False
            if task['status'] == 'planned' or task['execution'] is None:
                raise Conflict('Confirm this task execution before recording its outcome')
            if task['status'] == 'cancel_requested' and status == 'completed':
                raise Conflict('A stopped effort cannot accept late success; reconcile its execution')
            task.update(status=status, result=result, problem=None,
                        chargedMicrousd=task['reservedMicrousd'] if actual is None else actual,
                        usageEstimated=actual is None or usage_estimated)
            return True
        return self._change(effort, session, change)

    def control(self, effort, session, action, *, actor=None):
        if action not in ('pause', 'continue', 'stop'):
            raise InvalidProject('Choose pause, continue or stop')
        target = {'pause': 'paused', 'continue': 'active', 'stop': 'stopped'}[action]
        def change(state):
            if state['status'] == target:
                return False
            if state['status'] == 'stopped':
                raise InvalidProject('This effort was stopped; preserve it and start a new effort')
            state['status'] = target
            if actor is not None:
                from ..workflows import allowed_actor
                if not allowed_actor(actor):
                    raise InvalidProject('Use the authenticated Study control identity')
                state['lastControl'] = {'action': action, 'by': actor}
            if action == 'stop':
                for task in state['tasks'].values():
                    if task['status'] == 'planned':
                        task.update(status='cancelled', chargedMicrousd=0,
                                    result={'reason': 'Stopped before dispatch'})
                    elif task['status'] not in FINAL:
                        task['status'] = 'cancel_requested'
            return True
        return self._change(effort, session, change)

    def finding(self, effort, session, *, request, statement, tasks):
        text(request, 'Finding identity', 128)
        text(statement, 'Finding', 8000)
        if type(tasks) is not list or not tasks or len(tasks) > 32 or any(type(t) is not str for t in tasks):
            raise InvalidProject('A finding must cite its retained task receipts')
        row = {'request': request, 'statement': statement, 'tasks': tasks}
        def change(state):
            for old in state['findings']:
                if old['request'] == request:
                    if old != row:
                        raise Conflict('This finding request already belongs to a different statement')
                    return False
            if any(self._task(state, t)['status'] not in FINAL for t in tasks):
                raise InvalidProject('Cite observed task outcomes; running work is not a finding')
            if len(state['findings']) >= 128:
                raise InvalidProject('Finding capacity reached; preserve this effort')
            state['findings'].append(row)
            return True
        return self._change(effort, session, change)
