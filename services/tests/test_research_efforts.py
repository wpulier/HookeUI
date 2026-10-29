"""Real retained Project storage; execution/provider outcomes are explicit stand-ins."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from orchestrator.research.efforts import Efforts, KEY, money, summary
from orchestrator.scientific_projects import Conflict, InvalidProject, ProjectStore


@pytest.fixture
def effort(tmp_path):
    service = Efforts(ProjectStore(tmp_path / 'projects'))
    saved = service.create('2026-10-23', request='research-request-1', title='Compare methods',
                           objective='Compare predictors', criteria='Held-out error', budget_usd=1)
    return service, saved


def reserve(service, saved, request='experiment-1', maximum=.6):
    return service.reserve(saved.id, saved.session, request=request,
                           specification={'method': 'test-only', 'seed': 7}, maximum_usd=maximum)


def launch(service, saved, request='experiment-1'):
    reserve(service, saved, request)
    service.claim(saved.id, saved.session, request)
    return service.started(saved.id, saved.session, request, {'provider': 'stand-in', 'id': request})


def test_retry_and_restart_preserve_one_reservation_and_original_request(effort):
    service, saved = effort
    accepted = reserve(service, saved)
    reopened = Efforts(ProjectStore(service.store.root))
    assert reserve(reopened, saved) == accepted
    assert summary(accepted)['heldMicrousd'] == 600_000
    with pytest.raises(Conflict):
        reserve(reopened, saved, maximum=.5)
    assert reopened.read(saved.id, saved.session) == accepted


def test_concurrent_admission_cannot_spend_same_budget_twice(effort):
    service, saved = effort
    def submit(key):
        try:
            reserve(Efforts(ProjectStore(service.store.root)), saved, request=key)
            return 'accepted'
        except InvalidProject:
            return 'refused'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(submit, ['a', 'b'])) == ['accepted', 'refused']
    state = summary(service.read(saved.id, saved.session))
    assert len(state['tasks']) == 1 and state['availableMicrousd'] == 400_000


def test_only_one_dispatcher_claims_and_uncertain_launch_stays_held(effort):
    service, saved = effort
    reserve(service, saved)
    def claim(_):
        try:
            service.claim(saved.id, saved.session, 'experiment-1')
            return True
        except Conflict:
            return False
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]
    service.uncertain(saved.id, saved.session, 'experiment-1', 'Response lost after dispatch')
    reopened = Efforts(ProjectStore(service.store.root))
    state = summary(reopened.read(saved.id, saved.session))
    assert state['heldMicrousd'] == 600_000 and state['tasks']['experiment-1']['status'] == 'unknown'
    with pytest.raises(Conflict):
        reopened.claim(saved.id, saved.session, 'experiment-1')
    reopened.started(saved.id, saved.session, 'experiment-1', {'provider': 'stand-in', 'id': 'found-original'})
    assert summary(reopened.read(saved.id, saved.session))['tasks']['experiment-1']['status'] == 'running'


def test_stop_requires_execution_confirmation_and_keeps_pending_cost(effort):
    service, saved = effort
    launch(service, saved)
    reserve(service, saved, request='not-dispatched', maximum=.2)
    stopped = service.control(saved.id, saved.session, 'stop')
    state = summary(stopped)
    assert state['tasks']['not-dispatched']['status'] == 'cancelled'
    assert state['tasks']['experiment-1']['status'] == 'cancel_requested'
    assert state['heldMicrousd'] == 600_000
    with pytest.raises(Conflict):
        service.finish(saved.id, saved.session, 'experiment-1', status='completed', result={'metric': 1})
    final = service.finish(saved.id, saved.session, 'experiment-1', status='cancelled',
                           result={'confirmedStopped': True}, actual_usd=.05)
    assert summary(final)['spentMicrousd'] == 50_000 and summary(final)['heldMicrousd'] == 0
    assert service.finish(saved.id, saved.session, 'experiment-1', status='cancelled',
                          result={'confirmedStopped': True}, actual_usd=.05) == final
    with pytest.raises(InvalidProject):
        service.control(saved.id, saved.session, 'continue')


def test_missing_usage_is_conservative_and_real_overrun_is_not_hidden(effort):
    service, saved = effort
    launch(service, saved)
    completed = service.finish(saved.id, saved.session, 'experiment-1', status='completed', result={'metric': 1})
    assert summary(completed)['spentMicrousd'] == 600_000
    assert summary(completed)['tasks']['experiment-1']['usageEstimated']
    reserve(service, saved, request='second', maximum=.3)
    service.claim(saved.id, saved.session, 'second')
    service.started(saved.id, saved.session, 'second', {'id': 'second'})
    completed = service.finish(saved.id, saved.session, 'second', status='failed', result={'provider': 'overrun'}, actual_usd=.5)
    assert summary(completed)['spentMicrousd'] == 1_100_000
    with pytest.raises(InvalidProject, match='budget'):
        reserve(service, saved, request='third', maximum=.01)


def test_repeated_receipt_cannot_silently_change_charge_or_estimate_class(effort):
    service, saved = effort
    launch(service, saved)
    kept = service.finish(saved.id, saved.session, 'experiment-1', status='completed',
                          result={'metric': 1}, actual_usd=.6)
    for amount in (.1, None):
        with pytest.raises(Conflict):
            service.finish(saved.id, saved.session, 'experiment-1', status='completed',
                           result={'metric': 1}, actual_usd=amount)
    assert service.read(saved.id, saved.session) == kept


def test_pause_blocks_new_dispatch_but_retains_running_outcome(effort):
    service, saved = effort
    launch(service, saved)
    service.control(saved.id, saved.session, 'pause')
    with pytest.raises(InvalidProject):
        reserve(service, saved, request='new', maximum=.1)
    service.finish(saved.id, saved.session, 'experiment-1', status='completed', result={'metric': 1}, actual_usd=.1)
    service.control(saved.id, saved.session, 'continue')
    assert reserve(service, saved, request='new', maximum=.1)


def test_exact_inputs_and_owner_survive_reopen(effort):
    service, saved = effort
    source = service.store.create(saved.session, {'kind': 'table', 'title': 'Training data', 'state': {}, 'resources': [], 'runtimes': []})
    arguments = dict(request='source-research', title='Study', objective='Measure', criteria='Error', budget_usd=1,
                     inputs=[{'id': source.id, 'revision': source.revision, 'digest': source.digest}])
    study = service.create(saved.session, **arguments)
    service.store.save(source.id, source.session, {**source.document, 'title': 'Later data'}, expected_revision=1)
    assert service.create(saved.session, **arguments) == study
    assert study.document['state'][KEY]['inputs'][0]['digest'] == source.digest
    with pytest.raises(InvalidProject):
        service.read(study.id, '2026-10-24')
    with pytest.raises(InvalidProject):
        service.create('2026-10-24', **arguments)
    assert service.list('2026-10-24') == []


def test_failed_save_cannot_authorize_work(effort, monkeypatch):
    service, saved = effort
    def fail(*args, **kwargs):
        raise OSError('disk unavailable')
    monkeypatch.setattr(service.store, 'save', fail)
    with pytest.raises(OSError):
        reserve(service, saved)
    assert service.read(saved.id, saved.session) == saved


def test_findings_require_retained_outcomes_and_document_save_cannot_forge_spend(effort):
    from orchestrator.scientific_provenance import guard
    service, saved = effort
    launch(service, saved)
    with pytest.raises(InvalidProject):
        service.finding(saved.id, saved.session, request='f1', statement='Improved', tasks=['experiment-1'])
    service.finish(saved.id, saved.session, 'experiment-1', status='failed', result={'error': 'No data'}, actual_usd=.1)
    found = service.finding(saved.id, saved.session, request='f1', statement='Data was unavailable', tasks=['experiment-1'])
    assert service.finding(saved.id, saved.session, request='f1', statement='Data was unavailable', tasks=['experiment-1']) == found
    doc = found.document
    doc['state'][KEY]['capMicrousd'] = 10_000_000
    with pytest.raises(InvalidProject):
        guard(found, doc)
    with pytest.raises(InvalidProject):
        guard(None, saved.document)


@pytest.mark.parametrize('value', [True, None, 'NaN', 'Infinity', -1, .0000001, {}, '1e30'])
def test_invalid_money_refuses(value):
    with pytest.raises(InvalidProject):
        money(value)


def test_list_dates_creation_from_first_revision_and_keeps_original_initiator(effort):
    service, saved = effort
    original = saved.at
    reserve(service, saved)
    listing = service.list(saved.session)[0]
    assert listing['createdAt'] == original
    assert listing['updatedAt'] == service.read(saved.id, saved.session).at
    assert 'createdBy' not in listing  # Legacy author is unknown, never guessed.
    attributed = service.create(saved.session, request='attributed', title='New effort',
        objective='Compare', criteria='Measured', budget_usd=0,
        attribution={'by': 'agent:ac_0123456789abcdef', 'byLabel': 'Lead'})
    reopened = Efforts(ProjectStore(service.store.root))
    row = next(r for r in reopened.list(saved.session) if r['id'] == attributed.id)
    assert row['createdAt'] == attributed.at
    assert row['createdBy']['by'] == 'agent:ac_0123456789abcdef'
