import copy

import pytest

from orchestrator import scientific_history as history
from orchestrator.scientific_projects import InvalidProject, ProjectStore


def setup(tmp_path):
    store = ProjectStore(tmp_path / 'projects')
    saved = store.create('s1', {'kind': 'scene', 'title': 'Original input', 'state': {}, 'resources': [],
        'runtimes': [{'bundle': 'mine/host', 'generation': 'abc123', 'operation': 'view'}]})
    journal = history.ScientificHistory(store, saved, 'kernel-1', tab='tab-1', placement='placement-1')
    return store, saved, journal


def transcript(**changes):
    return {'format': 'scientific-console-v1', 'sequence': 1, 'stdout': 'before error', 'stderr': '',
            'result': '2', 'error': '', 'truncated': False, 'privatePreview': True, **changes}


def test_source_and_confirmed_output_survive_reopen_without_changing_scientific_input(tmp_path):
    store, saved, journal = setup(tmp_path)
    assert journal.begin('cell-1234567890123456', '1+1')
    admitted = store.read(journal.record.id, 's1')
    assert admitted.document['state']['cells'][0]['state'] == 'submitted'
    journal.finish('cell-1234567890123456', transcript(error='ValueError: example'))
    reopened = ProjectStore(store.root).read(journal.record.id, 's1')
    cell = reopened.document['state']['cells'][0]
    assert cell['source'] == '1+1' and cell['state'] == 'completed'
    assert cell['transcript']['stdout'] == 'before error'
    assert cell['transcript']['error'] == 'ValueError: example'
    assert reopened.document['runtimes'] == saved.document['runtimes']
    assert reopened.document['resources'] == []
    assert reopened.document['state']['input']['digest'] == saved.digest
    assert store.read(saved.id, 's1') == saved
    assert not journal.begin('cell-1234567890123456', '1+1')
    with pytest.raises(InvalidProject, match='different code'):
        journal.begin('cell-1234567890123456', '2+2')


def test_unresolved_admission_survives_crash_and_never_authorizes_reexecution(tmp_path):
    store, saved, journal = setup(tmp_path)
    assert journal.begin('cell-1234567890123456', 'viewer.layers.clear()')
    assert not journal.begin('cell-1234567890123456', 'viewer.layers.clear()')
    with pytest.raises(history.HistoryUnavailable, match='unconfirmed'):
        journal.begin('cell-2234567890123456', '1+1')
    reopened = ProjectStore(store.root).read(journal.record.id, 's1')
    assert reopened.document['state']['cells'][0]['state'] == 'submitted'
    journal.finish('cell-1234567890123456', interrupted=True)
    assert journal.lookup('cell-1234567890123456', 'viewer.layers.clear()')['state'] == 'interrupted'
    assert store.read(saved.id, 's1') == saved


def test_admission_disk_failure_does_not_admit_and_save_retry_does_not_execute(tmp_path, monkeypatch):
    store, _, journal = setup(tmp_path)
    original = store.create
    monkeypatch.setattr(store, 'create', lambda *_, **__: (_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises(history.HistoryUnavailable, match='do not rerun'):
        journal.begin('cell-1234567890123456', '1+1')
    assert journal.record is None
    monkeypatch.setattr(store, 'create', original)
    journal.retry_save()
    assert not journal.begin('cell-1234567890123456', '1+1')


def test_completion_failure_keeps_bounded_output_and_retries_exact_save(tmp_path, monkeypatch):
    store, _, journal = setup(tmp_path)
    journal.begin('cell-1234567890123456', '1+1')
    original = store.save
    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('acknowledgement lost after durable save')
    monkeypatch.setattr(store, 'save', uncertain)
    with pytest.raises(history.HistoryUnavailable):
        journal.finish('cell-1234567890123456', transcript())
    with pytest.raises(history.HistoryUnavailable):
        journal.begin('cell-2234567890123456', '2+2')
    monkeypatch.setattr(store, 'save', original)
    journal.retry_save()
    assert journal.record.revision == 2
    assert journal.lookup('cell-1234567890123456', '1+1')['transcript']['result'] == '2'


def test_initial_save_acknowledgement_loss_recovers_one_history_identity(tmp_path, monkeypatch):
    store, _, journal = setup(tmp_path)
    original = store.create
    def uncertain(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError('creation acknowledgement lost')
    monkeypatch.setattr(store, 'create', uncertain)
    with pytest.raises(history.HistoryUnavailable): journal.begin('cell-1234567890123456', '1+1')
    assert journal.record is None
    monkeypatch.setattr(store, 'create', original)
    journal.retry_save()
    assert len(store.list('s1')) == 2
    assert journal.record.id == journal.identity and journal.record.revision == 2
    assert journal.document['state']['cells'][0]['state'] == 'not-dispatched'
    assert store.read(journal.identity, 's1', revision=1).document['state']['cells'][0]['state'] == 'submitted'
    assert not journal.begin('cell-1234567890123456', '1+1')


def test_history_reserves_terminal_output_and_preserves_older_cells(tmp_path, monkeypatch):
    store, _, journal = setup(tmp_path)
    journal.begin('cell-1234567890123456', '1+1')
    journal.finish('cell-1234567890123456', transcript())
    prior = copy.deepcopy(journal.document)
    monkeypatch.setattr(history, 'MAX_HISTORY', len(history._json(prior)) + history.TERMINAL_ALLOWANCE)
    with pytest.raises(InvalidProject, match='full'):
        journal.begin('cell-2234567890123456', '2+2')
    assert store.read(journal.record.id, 's1').document == prior


def test_large_sources_keep_total_history_bound_and_preserve_prior_admissions(tmp_path):
    from env_worker.scientific_console import SOURCE_BYTES
    store, saved, journal = setup(tmp_path)
    assert history.MAX_HISTORY == 1 << 20
    code = '#' + 'x' * (SOURCE_BYTES - 1)
    for index in range(2):
        identity = f'large-script-cell-{index:016d}'
        assert journal.begin(identity, code)
        journal.finish(identity, transcript())
    before = journal.record
    with pytest.raises(InvalidProject, match='full'):
        journal.begin('large-script-cell-0000000000000002', code)
    assert journal.pending is None and journal.record == before
    reopened = ProjectStore(store.root).read(before.id, 's1')
    assert [cell['source'] for cell in reopened.document['state']['cells']] == [code, code]
    assert all(cell['state'] == 'completed' for cell in reopened.document['state']['cells'])
    assert store.read(saved.id, 's1') == saved


@pytest.mark.parametrize('bad', ['', '../invalid', 1])
def test_invalid_identity_never_creates_history(tmp_path, bad):
    store, _, journal = setup(tmp_path)
    with pytest.raises(InvalidProject): journal.begin(bad, '1+1')
    assert len(store.list('s1')) == 1


def test_invalid_or_oversized_output_cannot_be_saved_as_completed(tmp_path):
    store, _, journal = setup(tmp_path)
    journal.begin('cell-1234567890123456', '1+1')
    with pytest.raises(ValueError): journal.finish('cell-1234567890123456', transcript(stdout='x' * (17 << 10)))
    assert store.read(journal.record.id, 's1').document['state']['cells'][0]['state'] == 'submitted'


def test_unreadable_runtime_registration_keeps_save_recovery_available(tmp_path):
    import json
    from orchestrator.scientific_retention import BINDINGS
    store, saved, journal = setup(tmp_path)
    store.runtime_root = tmp_path / 'envs'
    store.register_runtime()
    journal.begin('cell-1234567890123456', '1+1')
    (store.root / BINDINGS).write_text('damaged')
    with pytest.raises(history.HistoryUnavailable, match='could not be confirmed saved'):
        journal.finish('cell-1234567890123456', transcript())
    assert store.read(saved.id, 's1') == saved
    (store.root / BINDINGS).write_text(json.dumps([str(store.runtime_root)]))
    journal.retry_save()
    assert journal.lookup('cell-1234567890123456', '1+1')['state'] == 'completed'


def test_failed_recovery_terminal_write_can_retry_without_losing_undispatched_outcome(tmp_path, monkeypatch):
    store, _, journal = setup(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(store, 'create', lambda *a, **kw: (_ for _ in ()).throw(OSError('initial write refused')))
        with pytest.raises(history.HistoryUnavailable): journal.begin('cell-1234567890123456', 'never_run()')
    with monkeypatch.context() as patch:
        patch.setattr(store, 'save', lambda *a, **kw: (_ for _ in ()).throw(OSError('terminal write refused')))
        with pytest.raises(history.HistoryUnavailable): journal.retry_save()
    assert journal.record.revision == 1
    with pytest.raises(history.HistoryUnavailable): journal.producer()
    journal.retry_save()
    assert journal.record.revision == 2 and journal.document['state']['cells'][0]['state'] == 'not-dispatched'
    assert journal.producer()['history']['digest'] == journal.record.digest
    journal.retry_save()
    assert journal.record.revision == 2
