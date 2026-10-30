from orchestrator.project_material import describe
from tests.test_scientific_project_api import web  # noqa: F401
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
        {'id': report.id, 'session': report.session, 'revision': report.revision, 'digest': report.digest}], 'facts': [
        {'shape': [18400,18533], 'report': {'id': report.id, 'session': report.session, 'revision': report.revision, 'digest': report.digest}}]}
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


def test_project_menu_counts_selected_material_and_historical_dependencies(web):
    from tests.test_scientific_project_api import create
    from tests.test_scientific_file_api import upload
    from orchestrator.project_material_edit import organize
    client, app = web
    sid = app.day.sid
    store = app.hub.resources.projects
    source = upload(client, app).json()['project']
    ref = {key: source[key] for key in ('session', 'id', 'revision', 'digest')}
    organize(store, sid, ref, role='Verification receipt', selected=False,
             expected_revision=0, actor='user', request='receipt-not-input')
    first = store.create(sid, document('matrix-result', title='Fitted model'))
    old = {'id': first.id, 'revision': first.revision, 'digest': first.digest}
    store.create(sid, document('research-effort', {'researchEffort': {'inputs': [old]}}))
    store.save(first.id, sid, document('matrix-result', title='Later model'), expected_revision=1)
    create(client, app)  # unrelated scene is not an input
    rows = client.get('/api/sessions').json()['sessions']
    assert next(row for row in rows if row['id'] == sid)['counts']['sources'] == 2
    entries, problems = app.hub.resources.collect(sid, kinds=('project',))
    from orchestrator.project_material import input_references
    assert not problems
    selected = input_references(entries)
    assert (sid, first.id, 1, first.digest) in selected and len(selected) == 2


def test_project_menu_deduplicates_only_exact_retained_source_aliases(tmp_path):
    from types import SimpleNamespace
    from orchestrator.project_material import additional_inputs, attach_usage
    store, saved, _ = inspected_source(tmp_path)
    entry = Index.project_entry(saved)
    attach_usage([entry], store)
    ref = {'session': saved.session, 'document': saved.id,
           'revision': saved.revision, 'digest': saved.digest}
    record = SimpleNamespace(sources={'a1': 1}, removed_sources=set(), source_references={'a1': ref})
    assert additional_inputs([entry], record) == 0  # the journal already counts it once
    record.source_references['a1'] = {**ref, 'revision': 2}
    assert additional_inputs([entry], record) == 1  # a different version is a separate input
    record.source_references['a1'] = ref
    record.removed_sources.add('a1')
    assert additional_inputs([entry], record) == 1  # retained Project input survives Stage removal


def test_project_menu_keeps_healthy_inputs_and_marks_unreadable_membership(web):
    from tests.test_scientific_file_api import upload
    client, app = web
    sid = app.day.sid
    upload(client, app)
    app.hub.resources.projects.create(sid, document('project-material-membership'))
    counts = next(row for row in client.get('/api/sessions').json()['sessions'] if row['id'] == sid)['counts']
    assert counts['sources'] == 1 and counts['sourcesIncomplete']


def test_grouped_material_scan_reads_each_shared_head_only_once(tmp_path, monkeypatch):
    from pathlib import Path
    store = ProjectStore(tmp_path)
    sessions = ['2026-10-01', '2026-10-02', '2026-10-03']
    saved = [store.create(sid, document('matrix-result')) for sid in sessions]
    reads = []
    original = store.read
    def counted(identity, session, **kwargs):
        reads.append((identity, session))
        return original(identity, session, **kwargs)
    monkeypatch.setattr(store, 'read', counted)
    scans = []
    original_iterdir = Path.iterdir
    def iterated(path):
        if path == store.root:
            scans.append(path)
        return original_iterdir(path)
    monkeypatch.setattr(Path, 'iterdir', iterated)
    grouped = store.scan_sessions(sessions)
    assert len(scans) == 1 and len(reads) == len(saved)
    assert all(len(grouped[sid][0]) == 1 and not grouped[sid][1] for sid in sessions)
    # Unattributable damage cannot silently report a complete count for any owner.
    (store.root / saved[0].id / 'head.json').write_text('broken')
    grouped = store.scan_sessions(sessions)
    assert all(grouped[sid][1] for sid in sessions)


def test_pending_legacy_publication_keeps_project_list_with_qualified_count(web):
    from tests.test_scientific_file_api import upload
    client, app = web
    upload(client, app)
    store = app.hub.resources.projects
    (store.root / '.bundle-import.json').write_text('{}')
    response = client.get('/api/sessions')
    assert response.status_code == 200
    row = next(row for row in response.json()['sessions'] if row['id'] == app.day.sid)
    assert row['counts']['sourcesIncomplete']


def test_inspection_preview_projection_is_bounded_and_preserves_older_facts():
    from orchestrator.project_material import inspection_facts
    import copy
    preview={'selection':'first-observations-and-variables','observations':['one'],'variables':['gene'],
             'values':[[2]],'observationAnnotations':[{'name':'batch','values':['A']}],'variableAnnotations':[]}
    assert inspection_facts({'preview':preview})['preview']==preview
    for change in ({'values':[[float('nan')]]},{'values':[[2,3]]},{'observations':['x']*11},
                   {'observationAnnotations':[{'name':'bad','values':['x'*257]}]}):
        bad=copy.deepcopy(preview);bad.update(change)
        facts=inspection_facts({'dtype':'float32','preview':bad})
        assert facts=={'dtype':'float32'}
    assert inspection_facts({'dtype':'float32','shape':[2,3]})=={'dtype':'float32','shape':[2,3]}
