from orchestrator.project_material import describe
from orchestrator.resources import Index
from orchestrator.project_state import _item
from orchestrator.door.schema import check
from orchestrator.scientific_projects import ProjectStore


def document(kind, state=None, title='same-name.zip'):
    return {'kind': kind, 'title': title, 'state': state or {}, 'resources': [], 'runtimes': []}


def test_names_do_not_define_scientific_meaning():
    for name in ('training.h5ad', 'model.npz', 'protocol.pdf'):
        item = describe(document('source-file', {'sourceFile': {'size': 18}}, name))
        assert item['kind'] == 'file' and item['input'] and item['origin'] == 'imported'
        assert 'format' not in item
        check(item, 'project-material')


def test_saved_model_format_and_origin_survive_reuse():
    value = document('matrix-result', {'matrixOperation': {'output': {'summary': {
        'format': 'response-model-v1', 'shape': [10, 40]}}}})
    model = describe(value)
    assert model['kind'] == 'model' and model['origin'] == 'generated'
    assert not model['input'] and model['shape'] == [10, 40]
    assert describe(document('matrix-result'))['kind'] == 'analysis'


def test_workflow_attempt_groups_under_original_run():
    row = describe(document('scientific-workflow-attempt', {'workflowAttempt': {'run': 'p123'}}))
    assert row['record'] and row['run'] == 'p123' and not row['input']


def test_resource_and_agent_share_material_metadata(tmp_path):
    store = ProjectStore(tmp_path)
    from orchestrator.scientific_file_intake import FileIntake
    intake = FileIntake(store, '2026-10-29', 'a' * 32, name='controls.h5ad', size=3, max_bytes=10)
    intake.write(b'abc')
    saved = intake.publish()
    intake.close()
    entry = Index.project_entry(saved)
    assert entry.wire()['role'] == 'files'
    assert entry.wire()['material'] == _item(entry)['material']
    assert _item(entry)['category'] == 'import'
    assert entry.wire()['version'] == saved.revision
    assert entry.wire()['source']['sha256'] == saved.digest


def test_assignment_is_input_and_opens_authoritative_study(tmp_path):
    store = ProjectStore(tmp_path)
    saved = store.create('2026-10-29', document('research-effort'))
    entry = Index.project_entry(saved)
    assert entry.wire()['material']['kind'] == 'assignment'
    assert entry.wire()['material']['input']
    assert _item(entry) is None  # project.state lists Studies separately


def test_exact_study_use_does_not_relabel_generated_model(tmp_path):
    from orchestrator.project_material import attach_usage
    store = ProjectStore(tmp_path)
    saved = store.create('2026-10-29', document('matrix-result', {'matrixOperation': {'output': {'summary': {'format': 'response-model-v1'}}}}))
    study = store.create(saved.session, document('research-effort', {'researchEffort': {'inputs': [
        {'id': saved.id, 'revision': saved.revision, 'digest': saved.digest}]}}))
    model, assignment = Index.project_entry(saved), Index.project_entry(study)
    attach_usage([model, assignment])
    assert model.material['input'] and model.material['origin'] == 'generated'
    assert model.material['uses'][0]['study'] == study.id
    check(model.material, 'project-material')
    model.version += 1
    attach_usage([model, assignment])
    assert not model.material['input']
    assert model.material['uses'][0]['revision'] == 1


def test_malformed_memberships_degrade_without_hiding_inventory(tmp_path):
    from orchestrator.project_material import attach_usage
    store = ProjectStore(tmp_path)
    for inputs in (None, [{'id': []}], [{'id': 'model'}], 'bad'):
        saved = store.create('2026-10-29', document('research-effort', {'researchEffort': {'inputs': inputs}}))
        entry = Index.project_entry(saved)
        assert attach_usage([entry], store)
        assert entry.material['kind'] == 'assignment'
        check(entry.material, 'project-material')


def test_historical_input_is_retained_with_its_original_title(tmp_path):
    from orchestrator.project_material import attach_usage
    store = ProjectStore(tmp_path)
    original = store.create('2026-10-29', document('matrix-result', title='Original model'))
    study = store.create(original.session, document('research-effort', {'researchEffort': {'inputs': [
        {'id': original.id, 'revision': 1, 'digest': original.digest}]}}))
    newer = store.save(original.id, original.session, document('matrix-result', title='New model'), expected_revision=1)
    entry = Index.project_entry(newer)
    assert not attach_usage([entry, Index.project_entry(study)], store)
    [use] = entry.material['uses']
    assert use['title'] == 'Original model' and use['available']
    assert use['digest'] == original.digest and use['revision'] == 1
