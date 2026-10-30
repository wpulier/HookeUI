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
    assert _item(entry) is None  # project.state joins the authoritative Study once below


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


def test_empty_study_is_the_same_material_to_owner_and_connected_reader(tmp_path):
    import asyncio
    from orchestrator.project_state import state_of, OWNER
    from orchestrator.research.efforts import Efforts
    from orchestrator.door.projects import access
    from tests.test_resources import app_at
    built = app_at(tmp_path).state.app
    study = Efforts(built.hub.resources.projects).create(built.day.sid, request="assignment-parity",
        title="Compare measurements", objective="Read the evidence", criteria="Retain revisions", budget_usd=0)
    # Synthetic grant here; actual connected-client qualification is separate.
    actor = "agent:ac_" + "a" * 24
    access(built).grant(built.day.sid, actor, "read", authority="development", user_project=True)
    owner = asyncio.run(state_of(built.hub).read(built.day.sid, OWNER))
    reader = asyncio.run(state_of(built.hub).read(built.day.sid, actor, access=access(built)))
    own = next(i for i in owner['data']['items'] if i['ref']['id'] == study.id)
    other = next(i for i in reader['data']['items'] if i['ref']['id'] == study.id)
    entry = next(e for e in built.hub.resources.collect(built.day.sid)[0] if e.id == study.id)
    assert own == other
    assert own['material'] == entry.wire()['material']
    assert own['material']['input'] and own['material']['kind'] == 'assignment'
    assert owner['studies']['items'][0]['ref'] == own['ref']
    check(owner, 'project-state'); check(reader, 'project-state')


def inspected_source(tmp_path):
    from orchestrator.scientific_file_intake import FileIntake
    store = ProjectStore(tmp_path)
    intake = FileIntake(store, '2026-10-29', 'synthetic-inspection-source', name='unknown.bin', size=3, max_bytes=10)
    intake.write(b'abc')
    source = intake.publish(); intake.close()
    file = source.document['state']['sourceFile']
    ref = {'id': source.id, 'session': source.session, 'revision': source.revision,
           'digest': source.digest, 'sha256': file['sha256'], 'bytes': file['size'],
           'component': file['reference']['component']}
    return store, source, ref


def inspection_report(store, source, ref, shape=(18400, 18533)):
    # Synthetic recorded result, not evidence of actual model execution.
    return store.create(source.session, document('matrix-result', {'matrixOperation': {
        'format': 1, 'capability': 'matrix-inspect', 'inputs': {'matrix': ref},
        'output': {'summary': {'format': 'annotated-matrix-summary-v1', 'shape': list(shape)}}}}))


def test_inspection_attaches_to_exact_source_with_report_provenance(tmp_path):
    from orchestrator.project_material import attach_usage
    store, source, ref = inspected_source(tmp_path)
    report = inspection_report(store, source, ref)
    entry = Index.project_entry(source)
    before = store.read(source.id, source.session)
    assert not attach_usage([entry, Index.project_entry(report)], store)
    assert entry.material['kind'] == 'matrix'
    assert entry.material['format'] == 'AnnData' and entry.material['shape'] == [18400, 18533]
    assert entry.material['origin'] == 'imported'
    assert entry.material['inspection'] == {'status': 'reported', 'total': 1, 'reports': [
        {'id': report.id, 'session': report.session, 'revision': report.revision, 'digest': report.digest}]}
    assert store.read(source.id, source.session) == before
    check(entry.material, 'project-material')
    assert _item(entry)['material'] == entry.wire()['material']


def test_inspection_never_attaches_by_filename_or_to_foreign_or_changed_reference(tmp_path):
    from orchestrator.project_material import attach_usage
    store, source, ref = inspected_source(tmp_path)
    for field, value in [('id', 'p' + 'b' * 32), ('session', '2026-10-28'),
                         ('revision', 2), ('digest', 'b' * 64), ('sha256', 'c' * 64),
                         ('bytes', 4), ('component', 'file.different')]:
        report = inspection_report(store, source, {**ref, field: value})
        entry = Index.project_entry(source)
        attach_usage([entry, Index.project_entry(report)], store)
        assert entry.material['kind'] == 'file', field
        assert 'inspection' not in entry.material, field


def test_conflicting_inspections_do_not_choose_latest_shape(tmp_path):
    from orchestrator.project_material import attach_usage
    store, source, ref = inspected_source(tmp_path)
    a = inspection_report(store, source, ref)
    b = inspection_report(store, source, ref, shape=(1, 2))
    entry = Index.project_entry(source)
    attach_usage([entry, Index.project_entry(b), Index.project_entry(a)], store)
    assert entry.material['kind'] == 'file' and 'shape' not in entry.material
    assert entry.material['inspection']['status'] == 'conflicting'
    assert entry.material['inspection']['total'] == 2
    check(entry.material, 'project-material')


def test_inspection_of_historical_study_input_does_not_describe_new_head(tmp_path):
    from orchestrator.project_material import attach_usage
    store, source, ref = inspected_source(tmp_path)
    report = inspection_report(store, source, ref)
    study = store.create(source.session, document('research-effort', {'researchEffort': {'inputs': [
        {'id': source.id, 'revision': source.revision, 'digest': source.digest}]}}))
    newer = store.save(source.id, source.session, {**source.document, 'title': 'New version'}, expected_revision=1)
    entry = Index.project_entry(newer)
    attach_usage([entry, Index.project_entry(report), Index.project_entry(study)], store)
    assert entry.material['kind'] == 'file'
    assert entry.material['uses'][0]['material']['kind'] == 'matrix'
    assert entry.material['uses'][0]['material']['inspection']['reports'][0]['id'] == report.id
    check(entry.material, 'project-material')


def test_nonproject_and_malformed_reports_cannot_poison_inventory(tmp_path):
    from dataclasses import replace
    from orchestrator.project_material import attach_usage
    store, source, ref = inspected_source(tmp_path)
    report = Index.project_entry(inspection_report(store, source, ref))
    for malformed in (replace(report, kind='result', source={}), replace(report, id='r1'),
                      replace(report, source={}), replace(report, version=None)):
        entry = Index.project_entry(source)
        attach_usage([entry, malformed], store)
        assert 'inspection' not in entry.material
        assert entry.material['kind'] == 'file'
