"""H02 slice 1: an accepted revision explains itself only from retained, digest-checked records.

Actual retained storage and HTTP with synthetic workers; no napari kernel, browser or export qualification.
"""
import shutil

import pytest
from fastapi.testclient import TestClient

from orchestrator.paths import Paths
from orchestrator.scientific_copies import PreparedArray
from orchestrator.scientific_history import ScientificHistory
from orchestrator.scientific_projects import InvalidProject, ProjectStore
from orchestrator.scientific_provenance import guard, resolve
from orchestrator.scientific_publication import ArrayPublication
from orchestrator.scientific_saves import LayerSave, SaveConflict
from orchestrator.server import create_app
from tests.test_scientific_edits import scene  # noqa: F401 (fixture)
from tests.test_scientific_saves import Worker, setup

TRANSCRIPT = {'format': 'scientific-console-v1', 'sequence': 1, 'stdout': '', 'stderr': '', 'result': '',
              'error': '', 'truncated': False, 'privatePreview': True}
PAINT = {'op': 'labels', 'request': 'gesture-123456789012', 'tool': 'paint', 'label': '9',
         'brush': 1, 'points': [[1, 4]]}
CONTEXT = {'dimensions': {'order': [0, 1]}, 'camera': {'zoom': 2}}


def kernel(store, saved, name, code):
    history = ScientificHistory(store, saved, name, tab='tab', placement='place')
    history.begin('cell-' + name.ljust(16, '0'), code)
    history.finish('cell-' + name.ljust(16, '0'), TRANSCRIPT)
    return history


def editor(store, saved):
    history = ScientificHistory(store, saved, 'manual-editor', tab='tab', placement='place')
    history.begin_action(PAINT['request'], PAINT, CONTEXT)
    history.finish_action(PAINT['request'], {'edit': {'state': 'applied', 'changed': True, 'label': '9'}})
    return history


def three_producers(store, saved, shown, data):
    """Two kernels and a manual editor start from one base; the first publishes, the others conflict."""
    first, second, manual = (kernel(store, saved, 'first-kernel', 'labels.data[:] = 11'),
                             kernel(store, saved, 'second-kernel', 'labels.data[:] = 22'), editor(store, saved))
    eleven, twenty_two, painted = data.copy(), data.copy(), data.copy()
    eleven[:], twenty_two[:], painted[1, 4] = 11, 22, 9
    saves = [LayerSave(store, saved, f'save-{n}'.ljust(20, '0'), shown, 'd' * 32, producer=h.producer())
             for n, h in (('first', first), ('second', second), ('manual', manual))]
    for save, values in zip(saves, (eleven, twenty_two, painted)):
        save.capture(Worker(values))
    saves[0].publish()
    copies = []
    for save in saves[1:]:
        with pytest.raises(SaveConflict):
            save.publish()
        copies.append(save.copy(None, None)['resource'])
    return (first, second, manual), copies


def test_two_kernels_and_a_manual_edit_each_resolve_only_their_own_producer_after_restart(scene):
    store, saved, shown, data = setup(scene)
    (first, second, manual), (second_copy, manual_copy) = three_producers(store, saved, shown, data)
    restarted = ProjectStore(store.root)
    base = {'id': saved.id, 'session': saved.session, 'revision': saved.revision, 'digest': saved.digest}
    found = {
        'first': resolve(restarted, saved.id, saved.session),
        'second': resolve(restarted, second_copy['id'], saved.session),
        'manual': resolve(restarted, manual_copy['id'], saved.session),
    }
    for name, history in (('first', first), ('second', second), ('manual', manual)):
        result = found[name]
        assert result['recorded'] and result['problems'] == [] and result['replayable'] is False
        [edit] = result['edits']
        assert edit['base'] == base and edit['input'] == saved.document['resources'][0]
        producer = edit['producer']
        assert producer['recorded'] and producer['kernel'] == history.document['state']['kernel']
        assert producer['history'] == {'id': history.record.id, 'session': history.record.session,
                                       'revision': history.record.revision, 'digest': history.record.digest}
    assert [c['source'] for c in found['first']['edits'][0]['producer']['cells']] == ['labels.data[:] = 11']
    assert [c['source'] for c in found['second']['edits'][0]['producer']['cells']] == ['labels.data[:] = 22']
    assert found['first']['edits'][0]['producer']['actions'] == []
    manual_producer = found['manual']['edits'][0]['producer']
    assert manual_producer['cells'] == []
    [action] = manual_producer['actions']
    assert action['request'] == PAINT and action['context'] == CONTEXT and action['state'] == 'completed'
    # The in-place result was accepted directly on its base; the others are copies of that same base.
    assert found['first']['acceptedAt'] == saved.revision + 1 and found['first']['copy'] is None
    for name in ('second', 'manual'):
        assert found[name]['copy']['conflictCopy'] and found[name]['acceptedAt'] == 1
        assert found[name]['copy']['steps'][-1]['source'] == base
    histories = {found[n]['edits'][0]['producer']['history']['id'] for n in found}
    assert len(histories) == 3


def test_change_record_names_exactly_the_chunks_whose_bytes_differ(scene):
    store, saved, shown, data = setup(scene)
    _, (second_copy, manual_copy) = three_producers(store, saved, shown, data)
    whole = resolve(store, saved.id, saved.session)['edits'][0]['changed']
    assert (whole['chunks'], whole['of'], whole['bounds']) == (4, 4, [[0, 4], [0, 6]])
    # No event announced the painted pixel: the record comes from retained bytes, not from what was said.
    one = resolve(store, manual_copy['id'], saved.session)['edits'][0]['changed']
    assert one['granularity'] == 'chunk' and one['axes'] == ['y', 'x']
    assert (one['chunks'], one['of'], one['keys'], one['bounds']) == (1, 4, ['0,1'], [[0, 2], [3, 6]])
    assert not one['truncated']


def test_kept_provenance_says_when_it_was_accepted_and_reports_values_changed_later(scene):
    store, saved, shown, data = setup(scene)
    three_producers(store, saved, shown, data)
    accepted = store.read(saved.id, saved.session)
    kept = store.save(saved.id, saved.session, {**accepted.document, 'title': 'Renamed'},
                      expected_revision=accepted.revision)
    result = resolve(store, saved.id, saved.session)
    assert result['record']['revision'] == kept.revision and result['acceptedAt'] == accepted.revision
    assert result['problems'] == []
    # A writer that replaced the values but kept the old record cannot pass that record off as theirs.
    document = kept.document
    document['resources'] = [dict(document['resources'][0], revision=saved.document['resources'][0]['revision'])]
    store.save(saved.id, saved.session, document, expected_revision=kept.revision)
    assert any('no longer holds' in p for p in resolve(store, saved.id, saved.session)['problems'])


def test_an_unreadable_history_is_reported_and_never_replaced_by_another_sharing_the_input(scene):
    store, saved, shown, data = setup(scene)
    (first, second, _), _ = three_producers(store, saved, shown, data)
    shutil.rmtree(store.root / first.record.id)
    result = resolve(ProjectStore(store.root), saved.id, saved.session)
    producer = result['edits'][0]['producer']
    assert producer['cells'] is None and producer['history']['id'] == first.record.id
    assert result['problems'] == ['The producing history could not be read; nothing else was substituted for it']
    assert second.record.id != first.record.id


def test_values_accepted_without_a_producer_say_so_and_a_later_unrecorded_publication_drops_the_old_record(scene):
    store, saved, shown, data = setup(scene)
    changed = data.copy()
    changed[0, 0] = 7
    save = LayerSave(store, saved, 'save-unrecorded-00000', shown, 'd' * 32)
    save.capture(Worker(changed))
    save.publish()
    producer = resolve(store, saved.id, saved.session)['edits'][0]['producer']
    assert producer == {'recorded': False, 'details': 'Producing actions were not recorded by this caller'}
    current = store.read(saved.id, saved.session)
    ref = current.document['resources'][0]
    # Publishing the same values without a record keeps the record that describes them.
    same = PreparedArray(store, current.id, current.session, current.revision, ref, ref)
    ArrayPublication(store, current, [same], 'a' * 64).publish()
    current = store.read(saved.id, saved.session)
    assert resolve(store, saved.id, saved.session)['recorded']
    # New values without a record drop it instead of passing it on to them.
    draft = store.array_edit(current.id, current.session, current.revision, ref, max_written_bytes=data.nbytes)
    Worker(data).stage_layer(None, None, None, type('Edit', (), {
        'specification': lambda self: {'shape': list(draft.shape), 'chunks': list(draft.chunks)},
        'write': lambda self, index, values: draft.write(index, values)})())
    replaced = PreparedArray(store, current.id, current.session, current.revision, ref, draft.finish())
    ArrayPublication(store, current, [replaced], 'b' * 64).publish()
    result = resolve(store, saved.id, saved.session)
    assert not result['recorded'] and result['meaning'] == 'No producing provenance was recorded for this revision.'


def test_document_saves_cannot_add_change_or_remove_provenance(scene):
    store, saved, shown, data = setup(scene)
    three_producers(store, saved, shown, data)
    accepted = store.read(saved.id, saved.session)
    guard(accepted, accepted.document)
    forged = accepted.document
    forged['state']['hookeEdit']['producer']['kernel'] = 'someone-else'
    for previous, document in ((accepted, forged), (None, accepted.document),
                               (accepted, {**accepted.document, 'state': {}})):
        with pytest.raises(InvalidProject, match='only when Hooke accepts the work'):
            guard(previous, document)


def test_provenance_endpoint_and_the_studio_save_refusing_forged_provenance(tmp_path):
    app = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    built = app.state.app
    client = TestClient(app, base_url='http://127.0.0.1:8770')
    headers = {'Origin': 'http://127.0.0.1:8770', 'X-MicroscopeMind': 'save-project'}
    sid = built.day.sid
    plain = {'kind': 'scene', 'title': 'Plain', 'state': {}, 'resources': [], 'runtimes': []}
    created = client.post('/api/scientific-projects', headers=headers, json={'session': sid, 'document': plain})
    assert created.status_code == 200, created.text
    project = created.json()['project']
    answer = client.get(f'/api/scientific-projects/{project["id"]}/provenance', params={'session': sid})
    assert answer.status_code == 200 and answer.json()['provenance']['recorded'] is False
    claim = {'format': 1, 'producer': {'kind': 'scientific-session', 'kernel': 'invented', 'history': None}}
    forged = {**plain, 'state': {'hookeOutput': claim}}
    refused = client.post('/api/scientific-projects', headers=headers, json={'session': sid, 'document': forged})
    assert refused.status_code == 400 and 'only when Hooke accepts the work' in refused.json()['says']
    refused = client.put(f'/api/scientific-projects/{project["id"]}', headers=headers,
                         json={'session': sid, 'expectedRevision': 1, 'document': forged})
    assert refused.status_code == 400
    assert client.get(f'/api/scientific-projects/{project["id"]}', params={'session': sid}).json()['project']['revision'] == 1
    assert client.get(f'/api/scientific-projects/{project["id"]}/provenance',
                      params={'session': '2020-01-01'}).status_code == 400


def test_a_record_written_outside_publication_is_reported_against_its_base_kernel_and_input(scene):
    store, saved, shown, data = setup(scene)
    (first, second, _), _ = three_producers(store, saved, shown, data)
    accepted = store.read(saved.id, saved.session)
    other = store.create(saved.session, {'kind': 'scene', 'title': 'Other input', 'state': {}, 'resources': [],
                                         'runtimes': []})
    unrelated = kernel(store, other, 'first-kernel', 'labels.data[:] = 11')
    for history, claimed, expected in (
            (second, 'first-kernel', 'belongs to a different kernel'),
            (unrelated, 'first-kernel', 'belongs to a different input')):
        current = store.read(saved.id, saved.session)
        document = current.document
        producer = document['state']['hookeEdit']['producer']
        producer['kernel'], producer['history'] = claimed, {
            'id': history.record.id, 'session': history.record.session,
            'revision': history.record.revision, 'digest': history.record.digest}
        store.save(saved.id, saved.session, document, expected_revision=current.revision)
        problems = resolve(store, saved.id, saved.session)['problems']
        assert problems == ['These values were not accepted directly on their recorded original revision',
                            f'The recorded history {expected}; nothing else was substituted for it']
    assert resolve(store, saved.id, saved.session, accepted.revision)['problems'] == []


def test_a_copy_of_unchanged_values_keeps_their_producer_remapped_to_the_copy(scene):
    store, saved, shown, data = setup(scene)
    (first, _, _), _ = three_producers(store, saved, shown, data)
    edited = store.read(saved.id, saved.session)
    ref = edited.document['resources'][0]
    copied = store.copy_candidate(edited, PreparedArray(store, edited.id, edited.session, edited.revision,
                                                        ref, ref)).publish(lambda: False)
    result = resolve(store, copied.id, copied.session)
    assert result['problems'] == [] and result['acceptedAt'] == 1
    [edit] = result['edits']
    assert edit['output'] == copied.document['resources'][0] and edit['output']['id'] == copied.id
    assert edit['producer']['history']['id'] == first.record.id
    assert result['copy']['steps'][-1]['source'] == {'id': edited.id, 'session': edited.session,
                                                     'revision': edited.revision, 'digest': edited.digest}


def test_a_copy_saved_before_records_were_remapped_says_so_not_that_values_changed(scene, monkeypatch):
    from orchestrator import scientific_copies
    store, saved, shown, data = setup(scene)
    three_producers(store, saved, shown, data)
    edited = store.read(saved.id, saved.session)
    ref = edited.document['resources'][0]
    original = scientific_copies._document

    def unremapped(document):  # what copies wrote before this change: the source's output, as it was
        if 'hookeEdit' in document['state']:
            document['state']['hookeEdit']['output'] = edited.document['state']['hookeEdit']['output']
        return original(document)
    monkeypatch.setattr(scientific_copies, '_document', unremapped)
    copied = store.copy_candidate(edited, PreparedArray(store, edited.id, edited.session, edited.revision,
                                                        ref, ref)).publish(lambda: False)
    problems = resolve(store, copied.id, copied.session)['problems']
    assert problems == ['This copy was made before copies kept their record up to date: its record still names '
                        "the values in the document it was copied from, not this copy's own"]


def test_a_damaged_earlier_revision_is_a_problem_not_a_failed_lookup(scene):
    store, saved, shown, data = setup(scene)
    (first, _, _), _ = three_producers(store, saved, shown, data)
    accepted = store.read(saved.id, saved.session)
    store.save(saved.id, saved.session, {**accepted.document, 'title': 'Renamed'}, expected_revision=accepted.revision)
    (store.root / saved.id / 'revisions' / f'{accepted.digest}.json').write_bytes(b'damaged')
    result = resolve(store, saved.id, saved.session)
    assert result['acceptedAt'] is None
    assert result['problems'] == [f'Revision {accepted.revision} of this document could not be read, so the '
                                  'revision that accepted this record is unknown']
    assert result['edits'][0]['producer']['history']['id'] == first.record.id
    assert result['edits'][0]['producer']['cells'][0]['source'] == 'labels.data[:] = 11'


def test_the_accepted_record_keeps_runtimes_coordinates_its_change_and_what_was_not_recorded(scene):
    store, saved, shown, data = setup(scene)
    document = saved.document
    document['runtimes'] = [{'bundle': 'napari-0.6', 'generation': 'a' * 16, 'operation': 'labels'}]
    document['state']['coordinateSpace'] = {'axes': ['y', 'x'], 'units': ['um', 'um'], 'scale': [0.5, 0.5]}
    saved = store.save(saved.id, saved.session, document, expected_revision=saved.revision)
    (first, _, manual), (_, manual_copy) = three_producers(store, saved, shown, data)
    record = store.read(saved.id, saved.session).document['state']['hookeEdit']
    assert record['runtimes'] == document['runtimes'] and record['coordinateSpace'] == document['state']['coordinateSpace']
    assert record['changed']['chunks'] == 4 and 'not recorded' in record['unrecorded']
    copied = store.read(manual_copy['id'], saved.session).document['state']['hookeEdit']
    assert copied['changed']['keys'] == ['0,1']
    [edit] = resolve(store, saved.id, saved.session)['edits']
    assert edit['changeRecorded'] and edit['runtimes'] == document['runtimes'] and edit['unrecorded'] == record['unrecorded']


def test_a_saved_change_record_that_disagrees_with_the_bytes_is_reported(scene):
    store, saved, shown, data = setup(scene)
    three_producers(store, saved, shown, data)
    current = store.read(saved.id, saved.session)
    document = current.document
    document['state']['hookeEdit']['changed']['chunks'] = 1
    store.save(saved.id, saved.session, document, expected_revision=current.revision)
    result = resolve(store, saved.id, saved.session)
    assert 'The saved change record disagrees with the saved values; the values were measured again' in result['problems']
    assert result['edits'][0]['changed']['chunks'] == 4
