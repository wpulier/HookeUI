"""H02 slice 4: every publication path is explained by the same resolver, through retries, conflicts and undo.

Actual retained storage with synthetic workers; no napari kernel or browser qualification.
"""
import pytest

from orchestrator.scientific_group_save import LayersSave
from orchestrator.scientific_outputs import OutputSave
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_provenance import resolve
from orchestrator.scientific_saves import LayerSave, SaveConflict, SaveUnavailable
from tests.test_scientific_edits import scene  # noqa: F401 (fixture)
from tests.test_scientific_group_save import LAYERS, GroupWorker, multiple  # noqa: F401 (fixture)
from tests.test_scientific_outputs import Worker as OutputWorker
from tests.test_scientific_provenance_resolve import CONTEXT, PAINT, editor, kernel
from tests.test_scientific_saves import Worker, setup


def exact(record):
    return {'id': record.id, 'session': record.session, 'revision': record.revision, 'digest': record.digest}


def lost_acknowledgement(store, monkeypatch):
    """The store accepts the revision, then the acknowledgement is lost once."""
    original = store.save
    calls = []

    def save(*args, **kwargs):
        result = original(*args, **kwargs)
        if not calls:
            calls.append(result)
            raise OSError('lost acknowledgement')
        return result
    monkeypatch.setattr(store, 'save', save)
    return calls


def test_a_multi_layer_save_resolves_every_layer_to_one_producer_after_a_lost_acknowledgement(multiple, monkeypatch):
    store, saved, data = multiple
    history = kernel(store, saved, 'group-kernel', 'labels.data[:] += 50; second.data[:] += 150')
    save = LayersSave(store, saved, 'save-group-0000000000', LAYERS, producer=history.producer())
    save.capture(GroupWorker(data))
    with monkeypatch.context() as patch:
        lost = lost_acknowledgement(store, patch)
        with pytest.raises(Exception):
            save.publish()
    save.publish()
    assert lost and store.read(saved.id, saved.session).revision == saved.revision + 1
    result = resolve(ProjectStore(store.root), saved.id, saved.session)
    assert result['problems'] == [] and result['acceptedAt'] == saved.revision + 1
    assert [e['output']['component'] for e in result['edits']] == ['array.labels', 'array.second']
    for edit in result['edits']:
        assert edit['base'] == exact(saved) and edit['changed']['chunks'] == 4 and edit['changeRecorded']
        assert edit['producer']['verified'] and edit['producer']['history']['id'] == history.record.id
        assert [c['source'] for c in edit['producer']['cells']] == ['labels.data[:] += 50; second.data[:] += 150']


def test_two_multi_layer_saves_from_one_base_conflict_explicitly_and_the_copy_keeps_its_producer(multiple):
    store, saved, data = multiple
    first, second = (kernel(store, saved, name, code) for name, code in
                     (('group-first', 'first()'), ('group-second', 'second()')))
    saves = [LayersSave(store, saved, f'save-{n}'.ljust(20, '0'), LAYERS, producer=h.producer())
             for n, h in (('one', first), ('two', second))]
    for save in saves:
        save.capture(GroupWorker(data))
    saves[0].publish()
    with pytest.raises(SaveConflict):
        saves[1].publish()
    copied = saves[1].copy(GroupWorker(data), None)['resource']
    result = resolve(store, copied['id'], saved.session)
    assert result['problems'] == [] and result['copy']['conflictCopy'] and result['acceptedAt'] == 1
    assert {e['producer']['history']['id'] for e in result['edits']} == {second.record.id}
    assert resolve(store, saved.id, saved.session)['edits'][0]['producer']['history']['id'] == first.record.id


def test_a_new_output_resolves_its_producer_and_base_after_a_failed_publication_is_retried(scene, monkeypatch):
    import orchestrator.scientific_outputs as module
    store, saved, _, _ = scene
    runtime = {'bundle': 'mine/runtime', 'generation': 'g1', 'operation': 'viewer'}
    document = saved.document
    document['runtimes'] = [runtime]
    document['state']['scientificView'] = {'runtime': runtime, 'inputs': {'labels': document['resources'][0]}}
    saved = store.save(saved.id, saved.session, document, expected_revision=saved.revision)
    history = kernel(store, saved, 'output-kernel', 'viewer.add_labels(objects)')
    save = OutputSave(store, saved, 'request-1234567890123', 'a' * 32, {'semantic': 'instance-labels', 'axes': ['z', 'x']},
                      producer=history.producer())
    save.capture(OutputWorker())
    rename = module.os.rename

    def acknowledged_then_lost(source, target):
        rename(source, target)
        raise OSError('lost rename acknowledgement')
    with monkeypatch.context() as patch:
        patch.setattr(module.os, 'rename', acknowledged_then_lost)
        with pytest.raises(SaveUnavailable):
            save.publish()
    save.publish()
    result = resolve(ProjectStore(store.root), save.target, saved.session)
    assert result['problems'] == [] and result['edits'] == []
    outputs = result['outputs']
    assert outputs['base'] == exact(saved) and outputs['runtimes'] == [runtime] and 'not recorded' in outputs['unrecorded']
    assert outputs['producer']['verified'] and [c['source'] for c in outputs['producer']['cells']] == ['viewer.add_labels(objects)']
    [made] = outputs['made']
    assert made['output'] in store.read(save.target, saved.session).document['resources']


def test_a_producer_record_hooke_cannot_check_is_kept_as_given_not_as_verified(multiple):
    store, saved, data = multiple
    save = LayersSave(store, saved, 'save-1234567890123456', LAYERS, producer={'kind': 'test', 'seed': 42})
    save.capture(GroupWorker(data))
    save.publish()
    producer = resolve(store, saved.id, saved.session)['edits'][0]['producer']
    assert producer['recorded'] and producer['verified'] is False
    assert producer['record'] == {'kind': 'test', 'seed': 42} and 'names no saved history' in producer['details']


def test_an_edit_undone_before_saving_records_its_actions_and_no_changed_values(scene):
    store, saved, shown, data = setup(scene)
    history = editor(store, saved)
    undo = {'op': 'labels', 'request': 'gesture-undo-00000000', 'tool': 'undo'}
    history.begin_action(undo['request'], undo, CONTEXT)
    history.finish_action(undo['request'], {'edit': {'state': 'applied', 'changed': True}})
    save = LayerSave(store, saved, 'save-undone-000000000', shown, 'd' * 32, producer=history.producer())
    save.capture(Worker(data.copy()))
    save.publish()
    [edit] = resolve(store, saved.id, saved.session)['edits']
    assert edit['changed']['chunks'] == 0 and edit['changed']['bounds'] is None
    assert [a['request']['tool'] for a in edit['producer']['actions']] == ['paint', 'undo']


def test_a_change_no_action_announced_is_in_the_change_record_and_said_to_be_unattributed(scene):
    store, saved, shown, data = setup(scene)
    history = editor(store, saved)  # one recorded paint at (1, 4)
    values = data.copy()
    values[1, 4] = 9
    values[3, 0] = 77  # written in place by a callback after its action returned; no event, no action
    save = LayerSave(store, saved, 'save-silent-000000000', shown, 'd' * 32, producer=history.producer())
    save.capture(Worker(values))
    save.publish()
    [edit] = resolve(store, saved.id, saved.session)['edits']
    assert edit['changed']['keys'] == ['0,1', '1,0'] and edit['changed']['bounds'] == [[0, 4], [0, 6]]
    assert [a['request'] for a in edit['producer']['actions']] == [PAINT]
    assert 'not attributed to an action' in edit['unrecorded']


def test_an_edit_undone_and_redone_before_saving_records_all_three_and_its_one_changed_block(scene):
    store, saved, shown, data = setup(scene)
    history = editor(store, saved)  # the paint at (1, 4)
    for tool in ('undo', 'redo'):
        step = {'op': 'labels', 'request': f'gesture-{tool}'.ljust(20, '0'), 'tool': tool}
        history.begin_action(step['request'], step, CONTEXT)
        history.finish_action(step['request'], {'edit': {'state': 'applied', 'changed': True}})
    redone = data.copy()
    redone[1, 4] = 9  # the redo restores the painted value
    save = LayerSave(store, saved, 'save-redone-000000000', shown, 'd' * 32, producer=history.producer())
    save.capture(Worker(redone))
    save.publish()
    [edit] = resolve(store, saved.id, saved.session)['edits']
    assert [a['request']['tool'] for a in edit['producer']['actions']] == ['paint', 'undo', 'redo']
    assert edit['changed']['keys'] == ['0,1'] and edit['changed']['bounds'] == [[0, 2], [3, 6]]
    assert values_of(store, saved)[1, 4] == 9


def values_of(store, saved):
    current = store.read(saved.id, saved.session)
    source = store.array_source(current.id, current.session, current.revision, current.document['resources'][0])
    return source.read(tuple(slice(0, n) for n in source.shape))
