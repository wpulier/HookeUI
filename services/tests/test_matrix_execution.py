"""Synthetic external transport over real workflow attempts and file intake."""
import copy
from types import SimpleNamespace
import pytest

from orchestrator import matrix_execution as external
from orchestrator.scientific_comparisons import identity
from orchestrator.scientific_projects import InvalidProject


class Backend:
    def __init__(self):
        self.calls = []
        self.failure = None
        self.limits = {'maximumMicros': 20, 'jobMaximumMicros': 10, 'maximumOutputBytes': 1024}

    def describe(self):
        return {'id': 'synthetic', 'generation': 'frozen-1', 'sessions': ['s1'],
                'capabilities': ['response-fit'], 'limits': self.limits}

    def prepare(self, intent):
        return {'job': intent['attempt'], 'state': 'prepared', 'maximumMicros': 10}

    def execute(self, intent, sources, workdir, checkpoint, stopped):
        self.calls.append(('submit', intent['attempt']))
        checkpoint({**intent['receipt'], 'state': 'running'})
        if self.failure:
            raise self.failure
        return self.reconcile(intent, sources, workdir, checkpoint, stopped)

    def reconcile(self, intent, sources, workdir, checkpoint, stopped):
        self.calls.append(('observe', intent['attempt']))
        if stopped():
            checkpoint({**intent['receipt'], 'state': 'cancelled'})
            raise external.ExecutionFailed('Observed cancellation')
        checkpoint({**intent['receipt'], 'state': 'succeeded'})
        workdir.mkdir()
        output = workdir / 'result.json'
        output.write_text('{"synthetic":true}')
        return output, {'evidenceClass': 'synthetic-provider'}


def test_startup_recovery_transports_are_not_admission_profiles(monkeypatch):
    old, current = Backend(), Backend()
    describe = current.describe
    current.describe = lambda: {**describe(), 'generation': 'frozen-2', 'sessions': []}
    module = SimpleNamespace(create_backend=lambda: current, create_recovery_backends=lambda: [old])
    monkeypatch.setenv('HOOKEUI_MATRIX_EXECUTION_EXTENSION', 'trusted_operator_extension')
    monkeypatch.setattr(external.importlib, 'import_module', lambda _: module)
    hub = SimpleNamespace()
    external.install(hub)
    assert external.profiles(hub) == [current.describe()]
    chosen = {'profile': 'synthetic', 'generation': 'frozen-1'}
    assert external.recovery_transport(hub, chosen)[0] is old
    with pytest.raises(InvalidProject, match='generation changed'):
        external.validate(hub, chosen, 'response-fit', 's1')
    assert not old.calls and not current.calls


def test_duplicate_recovery_generation_refuses_startup(monkeypatch):
    backend = Backend()
    module = SimpleNamespace(create_backend=lambda: backend,
                             create_recovery_backends=lambda: [backend, backend])
    monkeypatch.setenv('HOOKEUI_MATRIX_EXECUTION_EXTENSION', 'trusted_operator_extension')
    monkeypatch.setattr(external.importlib, 'import_module', lambda _: module)
    with pytest.raises(RuntimeError, match='Duplicate recovery'):
        external.install(SimpleNamespace())


def fixture(tmp_path, monkeypatch):
    from orchestrator.scientific_projects import ProjectStore
    from orchestrator.workflows import Workflows
    from orchestrator.workflow_execution import ScientificSteps, keep_file
    from orchestrator import matrix_capability_runs as local
    monkeypatch.setattr(local, 'descriptor', lambda _: {'generation': 'science-1', 'inputs': [{'id': 'effects'}]})
    monkeypatch.setattr(local, 'parameters', lambda _, p: dict(p))
    w = Workflows(ProjectStore(tmp_path / 'projects'))
    source = keep_file(w.store, 's1', 'a'*32, 'effects.npz', b'synthetic input')
    b = Backend()
    day = SimpleNamespace(local_only=False)
    day.elsewhere = lambda _: day
    steps = ScientificSteps(w, SimpleNamespace(matrix_executor=b, resources=SimpleNamespace(projects=w.store)))
    steps.day = day
    graph = {'format': 1, 'input': {'kind': 'source-file'}, 'nodes': [{
        'id': 'fit', 'name': 'Fit', 'kind': 'tool', 'needs': ['input'], 'settings': {
            'capability': 'response-fit', 'generation': 'science-1', 'parameters': {}, 'mode': 'automatic',
            'execution': {'profile': 'synthetic', 'generation': 'frozen-1'}}}]}
    definition = w.create('External fit', graph, 'b'*32)
    def claim(key='c'):
        saved = w.start(definition.id, 1, 's1', [identity(source)], key*32, actor='studio')
        return w.claim(saved.id, 's1')
    return w, steps, b, source, claim


def test_lost_reply_recovers_original_without_submission(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); b.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain):
        external.run(steps, c, {'effects': identity(source)})
    w.finish(c, {'status': 'uncertain', 'output': None, 'note': 'Lost reply'})
    with pytest.raises(InvalidProject, match='termination'):
        w.resolve(c['run'], 's1', c['attempt']['id'], 'probably gone', actor='studio')
    external.recover(steps, c['run'], 's1')
    result = w.status(c['run'], 's1')
    assert result['status'] == 'completed'
    assert [x[0] for x in b.calls] == ['submit', 'observe']
    assert result['attempts'][0]['execution']['receipt']['state'] == 'succeeded'
    external.recover(steps, c['run'], 's1')
    assert len(b.calls) == 2


def test_reservations_block_unsettled_and_exhausted_runs(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); b.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain): external.run(steps, c, {'effects': identity(source)})
    second = claim('d')
    with pytest.raises(InvalidProject, match='Reconcile'): external.run(steps, second, {'effects': identity(source)})
    assert len(b.calls) == 1
    external.recover(steps, c['run'], 's1')
    b.failure = None
    external.run(steps, second, {'effects': identity(source)})
    with pytest.raises(InvalidProject, match='allowance'): external.run(steps, claim('e'), {'effects': identity(source)})
    assert [x[0] for x in b.calls].count('submit') == 2


@pytest.mark.parametrize('settled', [False, True])
def test_removed_project_keeps_outstanding_and_spent_reservations(tmp_path, monkeypatch, settled):
    from orchestrator.workflow_execution import keep_file
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    original = claim()
    if not settled:
        b.failure = OSError('Lost reply')
        with pytest.raises(external.ExecutionUncertain):
            external.run(steps, original, {'effects': identity(source)})
    else:
        external.run(steps, original, {'effects': identity(source)})
    describe = b.describe
    b.describe = lambda: {**describe(), 'sessions': ['s2']}
    b.limits['maximumMicros'] = 10
    new_source = keep_file(w.store, 's2', 'd'*32, 'effects.npz', b'synthetic input')
    saved_run = w.store.read(original['run'], 's1').document['state']['workflowRun']
    definition = saved_run['definition']
    run = w.start(definition['id'], definition['revision'], 's2', [identity(new_source)], 'e'*32, actor='studio')
    new_claim = w.claim(run.id, 's2')
    with pytest.raises(InvalidProject, match='allowance' if settled else 'Reconcile'):
        external.run(steps, new_claim, {'effects': identity(new_source)})
    assert [x[0] for x in b.calls].count('submit') == 1


def test_unreadable_reservation_scope_refuses_admission(tmp_path, monkeypatch):
    from orchestrator.workflow_execution import keep_file
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim()
    unrelated = keep_file(w.store, 's2', 'f'*32, 'effects.npz', b'synthetic input')
    (w.store.root / unrelated.id / 'head.json').write_text('{}')
    with pytest.raises(InvalidProject, match='ownership'):
        external.run(steps, c, {'effects': identity(source)})
    assert not b.calls


def test_stopped_restart_observes_cancellation_and_never_resubmits(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); b.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain): external.run(steps, c, {'effects': identity(source)})
    w.control(c['run'], 's1', 'stop', actor='studio')
    external.recover(steps, c['run'], 's1')
    result = w.status(c['run'], 's1')
    assert result['status'] == 'stopped'
    assert result['attempts'][0]['execution']['receipt']['state'] == 'cancelled'
    assert [x[0] for x in b.calls] == ['submit', 'observe']


def test_local_only_and_disk_refuse_before_prepare(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); steps.day.local_only = True
    with pytest.raises(InvalidProject, match='local only'): external.run(steps, c, {'effects': identity(source)})
    steps.day.local_only = False
    monkeypatch.setattr(external.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    with pytest.raises(InvalidProject, match='local space'): external.run(steps, c, {'effects': identity(source)})
    assert not b.calls
    assert 'execution' not in w.status(c['run'], 's1')['attempts'][0]


def test_resource_profile_is_explicit_and_does_not_change_default():
    from scientific_compute import resource_profile as r
    profile = {**r.DEFAULT, 'memory_bytes': 112<<30, 'materialization_bytes': 96<<30, 'cpu_seconds': 7200}
    token = r.configure(profile)
    try: assert r.current() == profile
    finally: r.reset(token)
    assert r.current() == r.DEFAULT
    with pytest.raises(ValueError): r.validate({**profile, 'memory_bytes': 24<<30})
    with pytest.raises(ValueError): r.validate({**profile, 'cpu_seconds': 0})


def test_stop_recovery_needs_no_current_input_code_or_disk(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); b.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain): external.run(steps, c, {'effects': identity(source)})
    w.control(c['run'], 's1', 'stop', actor='studio')
    from orchestrator import matrix_capability_runs as local
    def unavailable(*a, **kw): raise AssertionError('Recovery must not need current scientific inputs')
    monkeypatch.setattr(local, 'descriptor', unavailable)
    monkeypatch.setattr(local, '_input', unavailable)
    monkeypatch.setattr(external.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    external.recover(steps, c['run'], 's1')
    assert w.status(c['run'], 's1')['status'] == 'stopped'


def test_concurrent_recovery_has_one_original_observer(tmp_path, monkeypatch):
    import threading
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); b.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain): external.run(steps, c, {'effects': identity(source)})
    entered, release = threading.Event(), threading.Event()
    original = b.reconcile
    def held(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    b.reconcile = held
    thread = threading.Thread(target=external.recover, args=(steps, c['run'], 's1'))
    thread.start()
    try:
        assert entered.wait(10)
        external.recover(steps, c['run'], 's1')
        assert [x[0] for x in b.calls] == ['submit']
    finally:
        release.set(); thread.join(10)
    assert w.status(c['run'], 's1')['status'] == 'completed'


def test_explicit_operator_allowance_is_exact_retained_and_scoped(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    steps.day.local_only = True
    from orchestrator.workflow_execution import keep_file
    evidence = keep_file(w.store, 's1', 'f'*32, 'owner-allowance.txt', b'Explicit scoped operator authorization')
    authorization = {'reason': 'Owner explicitly authorized this bounded profile', 'evidence': identity(evidence)}
    original = b.describe
    monkeypatch.setattr(b, 'describe', lambda: {**original(), 'localOnlyAuthorizations': {'s1': authorization}})
    assert 'localOnlyAuthorizations' not in external.discovery(steps.hub)[0]
    c = claim()
    authorization['evidence']['digest'] = '0'*64
    with pytest.raises(InvalidProject): external.run(steps, c, {'effects': identity(source)})
    assert not b.calls
    authorization['evidence'] = identity(evidence)
    b.failure = OSError('lost reply')
    with pytest.raises(external.ExecutionUncertain): external.run(steps, c, {'effects': identity(source)})
    saved = w.status(c['run'], 's1')['attempts'][0]['execution']['intent']
    assert saved['localOnlyAuthorization'] == authorization
    assert steps.day.local_only is True
    # Cancellation does not require the authorization document to remain readable.
    import orchestrator.scientific_comparisons as comparisons
    monkeypatch.setattr(comparisons, 'exact', lambda *a: (_ for _ in ()).throw(AssertionError('read current evidence')))
    w.control(c['run'], 's1', 'stop', actor='studio')
    external.recover(steps, c['run'], 's1')
    assert w.status(c['run'], 's1')['status'] == 'stopped'


@pytest.mark.parametrize('stop', [False, True])
def test_original_transport_survives_profile_change(tmp_path, monkeypatch, stop):
    w, steps, original, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); original.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain):
        external.run(steps, c, {'effects': identity(source)})
    replacement = Backend()
    describe = replacement.describe
    replacement.describe = lambda: {**describe(), 'generation': 'frozen-2', 'sessions': []}
    steps.hub.matrix_executor = replacement
    steps.hub.matrix_recovery_executors = {('synthetic', 'frozen-1'): original}
    if stop: w.control(c['run'], 's1', 'stop', actor='studio')
    external.recover(steps, c['run'], 's1')
    assert w.status(c['run'], 's1')['status'] == ('stopped' if stop else 'completed')
    assert [call[0] for call in original.calls] == ['submit', 'observe']
    assert replacement.calls == []
    with pytest.raises(InvalidProject):
        external.run(steps, claim('d'), {'effects': identity(source)})


def test_missing_original_transport_keeps_uncertainty(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); b.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain):
        external.run(steps, c, {'effects': identity(source)})
    steps.hub.matrix_executor = None
    external.recover(steps, c['run'], 's1')
    result = w.status(c['run'], 's1')['attempts'][0]
    assert result['status'] == 'uncertain'
    assert 'original execution transport' in result['note']
    assert len(b.calls) == 1


def test_revoked_admission_does_not_revoke_existing_stop(tmp_path, monkeypatch):
    w, steps, b, source, claim = fixture(tmp_path, monkeypatch)
    c = claim(); b.failure = OSError('Lost reply')
    with pytest.raises(external.ExecutionUncertain):
        external.run(steps, c, {'effects': identity(source)})
    describe = b.describe
    monkeypatch.setattr(b, 'describe', lambda: {**describe(), 'sessions': [], 'capabilities': []})
    w.control(c['run'], 's1', 'stop', actor='studio')
    external.recover(steps, c['run'], 's1')
    assert w.status(c['run'], 's1')['status'] == 'stopped'
    with pytest.raises(InvalidProject, match='outside'):
        external.validate(steps.hub, {'profile': 'synthetic', 'generation': 'frozen-1'}, 'response-fit', 's1')
