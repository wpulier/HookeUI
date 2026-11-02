"""Operator-installed file executors; workflows own identity, allowance and recovery.

No provider implementation lives here. Saved settings select an installed profile,
never Python code. All external execution receipts live on the original attempt.
"""
from __future__ import annotations

import copy
import importlib
import os
from pathlib import Path
import shutil
from uuid import uuid4

from .file_locks import fcntl
from .scientific_projects import InvalidProject, Conflict, _json
from .scientific_comparisons import identity
from .workflows import token


class ExecutionUncertain(InvalidProject):
    """A provider may still own execution. A new attempt must not be admitted."""


class ExecutionFailed(InvalidProject):
    """The original provider job has a retained terminal failure receipt."""


def selection(value):
    if (type(value) is not dict or set(value) != {'profile', 'generation'}
            or any(type(v) is not str or not 1 <= len(v) <= 200 for v in value.values())):
        raise InvalidProject('Choose an installed execution profile and its exact generation')
    return copy.deepcopy(value)


def install(hub):
    module = os.environ.get('HOOKEUI_MATRIX_EXECUTION_EXTENSION', '').strip()
    if not module:
        return
    backend = importlib.import_module(module).create_backend()
    if any(not callable(getattr(backend, method, None)) for method in
           ('describe', 'prepare', 'execute', 'reconcile')):
        raise RuntimeError('A matrix execution extension needs describe, prepare, execute and reconcile')
    hub.matrix_executor = backend
    profiles(hub)


def profiles(hub):
    backend = getattr(hub, 'matrix_executor', None)
    if backend is None:
        return []
    value = backend.describe()
    if (type(value) is not dict or len(_json(value)) > 16 << 10
            or not {'id', 'generation', 'capabilities', 'limits', 'sessions'} <= set(value)
            or type(value['sessions']) is not list or not value['sessions']
            or any(type(s) is not str for s in value['sessions'])
            or type(value['capabilities']) is not list
            or type(value['limits']) is not dict
            or any(type(s) is not str for s in value['capabilities'])):
        raise InvalidProject('The installed execution profile is malformed')
    selection({'profile': value['id'], 'generation': value['generation']})
    for key in ('maximumMicros', 'jobMaximumMicros', 'maximumOutputBytes'):
        if type(value['limits'].get(key)) is not int or value['limits'][key] <= 0:
            raise InvalidProject('The installed execution profile needs explicit resource ceilings')
    return [copy.deepcopy(value)]


def validate(hub, chosen, capability, session):
    chosen = selection(chosen)
    found = profiles(hub)
    if not found:
        raise InvalidProject('This execution profile is not installed; nothing ran')
    profile = found[0]
    if chosen != {'profile': profile['id'], 'generation': profile['generation']}:
        raise InvalidProject('The installed execution generation changed; explicitly revise the workflow')
    if session not in profile['sessions'] or capability not in profile['capabilities']:
        raise InvalidProject('This Project or capability is outside the operator-approved execution profile')
    return profile


def discovery(hub):
    """Describe available execution without disclosing operator Project allowlists."""
    return [{k: v for k, v in profile.items() if k != 'sessions'} for profile in profiles(hub)]


def _receipt(value):
    if (type(value) is not dict or len(_json(value)) > 64 << 10
            or type(value.get('job')) is not str or not 1 <= len(value['job']) <= 512
            or type(value.get('maximumMicros')) is not int or value['maximumMicros'] <= 0
            or type(value.get('state')) is not str):
        raise InvalidProject('The executor must return a bounded job identity, state and cost reservation')
    return copy.deepcopy(value)


def checkpoint(workflows, claim, receipt):
    receipt = _receipt(receipt)
    with workflows.locked(claim['run'], claim['session']):
        saved = workflows.store.read(claim['attempt']['id'], claim['session'])
        state = saved.document['state']['workflowAttempt']
        execution = state.get('execution')
        if not execution or state['status'] not in ('running', 'uncertain'):
            raise ExecutionUncertain('The original external attempt no longer owns this observation')
        prior = execution['receipt']
        if any(receipt[k] != prior[k] for k in ('job', 'maximumMicros')):
            raise Conflict('An external observation cannot replace its job identity or allowance')
        execution['receipt'] = receipt
        state['note'] = 'External execution: ' + receipt['state'][:100]
        workflows._save_attempt(saved, state)


def _admit(workflows, claim, intent, receipt, profile):
    """One cross-run lock, but no second ledger: sum original attempt reservations."""
    folder = workflows.library.root
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / '.external-execution.lock').open('a+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with workflows.locked(claim['run'], claim['session']):
                saved = workflows.store.read(claim['attempt']['id'], claim['session'])
                state = saved.document['state']['workflowAttempt']
                previous = state.get('execution')
                if previous:
                    if previous['intent'] != intent:
                        raise Conflict('The original attempt belongs to different external work')
                    return previous['receipt'], False
                if state['status'] != 'running':
                    raise InvalidProject('Only the original running attempt can admit external work')
                spent = 0
                for session in profile['sessions']:
                    for item in workflows.store.list(session):
                        a = item.document['state'].get('workflowAttempt', {})
                        e = a.get('execution')
                        if not e or e['intent']['execution']['profile'] != profile['id']:
                            continue
                        spent += e['receipt']['maximumMicros']
                        if e['receipt']['state'] not in ('succeeded', 'failed', 'cancelled', 'not-submitted'):
                            raise InvalidProject('Reconcile the original external job before admitting another')
                ceiling = profile['limits']
                if receipt['maximumMicros'] > ceiling['jobMaximumMicros'] or spent + receipt['maximumMicros'] > ceiling['maximumMicros']:
                    raise InvalidProject('External execution exceeds the remaining reserved allowance')
                state['execution'] = {'format': 1, 'intent': copy.deepcopy(intent), 'receipt': receipt}
                workflows._save_attempt(saved, state)
                return receipt, True
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


class ExecutionBusy(InvalidProject):
    """Another caller currently owns the original attempt's provider observation."""


def run(steps, claim, inputs, *, reconcile=False):
    folder = steps.workflows.library.root
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / ('.external-attempt-' + token(claim['attempt']['id']) + '.lock')).open('a+b') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ExecutionBusy('The original external attempt is already being observed') from error
        try:
            return _run(steps, claim, inputs, reconcile=reconcile)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _run(steps, claim, inputs, *, reconcile):
    from . import matrix_capability_runs as local
    from .scientific_file_intake import SPARE, WORKING_BYTES
    state = steps.workflows.store.read(claim['attempt']['id'], claim['session']).document['state']['workflowAttempt']
    prior = state.get('execution')
    if reconcile and not prior:
        raise InvalidProject('There is no original external execution to reconcile')
    if state['status'] not in ('running', 'uncertain'):
        raise ExecutionBusy('This external attempt has already settled')
    sources = {}
    if prior:
        # Cancellation/observation requires no current source, scientific code or
        # local output space: those may have disappeared after submission.
        intent = copy.deepcopy(prior['intent'])
        profile = validate(steps.hub, intent['execution'], intent['capability'], claim['session'])
        receipt, fresh = prior['receipt'], False
    else:
        settings = claim['node']['settings']
        if steps.day.elsewhere(claim['session']).local_only:
            raise InvalidProject('This Project is local only; external execution is unavailable')
        profile = validate(steps.hub, settings['execution'], settings['capability'], claim['session'])
        store = steps.store.for_session(claim['session'], write=True)
        declared = local.descriptor(settings['capability'])
        if declared['generation'] != settings['generation']:
            raise InvalidProject('The pinned scientific capability generation changed')
        params = local.parameters(settings['capability'], settings['parameters'])
        if set(inputs) != {p['id'] for p in declared['inputs']}:
            raise InvalidProject('Bind every declared scientific file input')
        exact = {}
        for port, ref in inputs.items():
            sources[port], exact[port] = local._input(store, claim['session'], ref, lambda: None)
        intent = {'session': claim['session'], 'run': claim['run'], 'attempt': claim['attempt']['id'],
                  'capability': settings['capability'], 'generation': settings['generation'],
                  'params': params, 'inputs': exact, 'execution': selection(settings['execution'])}
        maximum = min(profile['limits']['maximumOutputBytes'], local.MAX_INPUT_BYTES)
        if shutil.disk_usage(store.root).free < 2 * maximum + WORKING_BYTES + SPARE:
            raise InvalidProject('Insufficient local space to retain the admitted external output')
        receipt, fresh = _admit(steps.workflows, claim, intent,
                               _receipt(steps.hub.matrix_executor.prepare(intent)), profile)
    def stopped():
        return bool(getattr(steps, 'closing', False) or steps.workflows.run(claim['run'], claim['session']).document['state']['workflowRun']['control'] == 'stop')
    scratch = None
    try:
        store = steps.store.for_session(claim['session'], write=True)
        maximum = min(profile['limits']['maximumOutputBytes'], local.MAX_INPUT_BYTES)
        intent['receipt'] = receipt
        observe = lambda value: checkpoint(steps.workflows, claim, value)
        request = token(claim['attempt']['id'])[:32]
        invocation = {'format': 1, 'capability': intent['capability'], 'generation': intent['generation'],
                      'runtime': {'executionProfile': profile['id'], 'generation': profile['generation']},
                      'inputs': intent['inputs'], 'params': intent['params']}
        target, _ = local.request_identity(claim['session'], request)
        if store._folder(target).exists():
            saved = store.read(target, claim['session'], revision=1)
            old = saved.document['state'].get('matrixOperation', {})
            if {k: v for k, v in old.items() if k != 'output'} != invocation:
                raise Conflict('External result identity belongs to different scientific work')
            local._input(store, claim['session'], identity(saved), lambda: None)
            return saved
        # The backend creates this only when retaining a result. Even mkdir can
        # fail on a full disk, so provider cancellation must precede it.
        scratch = Path(store.root) / ('.external-matrix-' + uuid4().hex)
        backend = steps.hub.matrix_executor
        execute = backend.execute if fresh and not reconcile else backend.reconcile
        path, summary = execute(intent, sources, scratch, observe, stopped)
        path = Path(path)
        if path.is_symlink() or path.parent.resolve() != scratch.resolve() or not path.is_file() or path.stat().st_size > maximum:
            raise ExecutionUncertain('The external result is outside its declared retention allowance')
        if len(_json(summary)) > local.MAX_RECEIPT_BYTES:
            raise ExecutionUncertain('The external result summary exceeds its metadata allowance')
        intake = local._Output(store, claim['session'], request, name=path.name, size=path.stat().st_size,
                               max_bytes=maximum, invocation=invocation, summary=summary)
        try:
            with path.open('rb') as stream:
                while part := stream.read(local.CHUNK_BYTES):
                    intake.write(part)
            return intake.publish()
        finally:
            intake.close()
    except ExecutionUncertain:
        raise
    except ExecutionFailed:
        current = steps.workflows.store.read(claim['attempt']['id'], claim['session']).document['state']['workflowAttempt']['execution']['receipt']
        if current['state'] not in ('failed', 'cancelled', 'not-submitted') and not (current['state'] == 'succeeded' and stopped()):
            raise ExecutionUncertain('Failure lacks a retained termination observation')
        raise
    except Exception as error:
        raise ExecutionUncertain(str(error)[:500] or 'Reconcile the original external execution') from error
    finally:
        if scratch is not None and scratch.exists():
            try:
                shutil.rmtree(scratch)
            except OSError as error:
                raise ExecutionUncertain('External scratch cleanup interrupted; reconcile the retained original result') from error


def recover(steps, run_id, session):
    status = steps.workflows.status(run_id, session)
    for attempt in status['attempts']:
        if attempt['status'] not in ('running', 'uncertain') or not attempt.get('execution'):
            continue
        node = next(n for n in status['definition']['graph']['nodes'] if n['id'] == attempt['node'])
        claim = {'run': run_id, 'session': session, 'node': node, 'inputs': attempt['inputs'],
                 'attempt': identity(steps.store.read(attempt['id'], session, revision=1))}
        try:
            saved = run(steps, claim, {}, reconcile=True)
            outcome = steps.completed(claim, saved)
        except ExecutionBusy:
            continue
        except ExecutionFailed as error:
            outcome = {'status': 'failed', 'output': None, 'note': str(error)[:500]}
        except (ValueError, OSError) as error:
            outcome = {'status': 'uncertain', 'output': None, 'note': str(error)[:500]}
        steps.workflows.finish(claim, outcome)
