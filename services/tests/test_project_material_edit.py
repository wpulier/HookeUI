"""Real revision and intake stores; synthetic transport credentials, no agent qualification."""
import pytest
from orchestrator.project_material_edit import organize
from orchestrator.project_material import describe
from orchestrator.scientific_comparisons import identity
from orchestrator.scientific_projects import ProjectStore, InvalidProject, Conflict
from tests.test_project_material import document
from tests.test_agent_projects import project_door, run  # noqa: F401
from tests.test_agent_door import connect, bearer, window_key  # noqa: F401
from tests.test_scientific_project_api import web, HEADERS  # noqa: F401


def test_organize_generated_model_preserves_origin_bytes_and_history(tmp_path):
    store = ProjectStore(tmp_path)
    original = store.create('2026-10-29', document('matrix-result', {'matrixOperation': {
        'output': {'summary': {'format': 'response-model-v1'}}}}))
    annotated = organize(store, original.session, identity(original), role='Candidate models', selected=True,
                         expected_revision=0, actor='agent:test', request='organize1')
    assert annotated.document['state']['materialMembership']['role'] == 'Candidate models'
    assert store.read(original.id, original.session).digest == original.digest
    assert store.read(original.id, original.session, revision=1).digest == original.digest
    assert store.read(original.id, original.session).document == original.document
    again = organize(store, original.session, identity(original), role='Candidate models', selected=True,
                     expected_revision=0, actor='agent:test', request='organize1')
    assert again.digest == annotated.digest
    with pytest.raises(Conflict):
        organize(store, original.session, identity(original), role='Changed', selected=True,
                 expected_revision=0, actor='agent:test', request='organize1')


def test_stale_and_foreign_and_revoked_organization_refuse(tmp_path):
    store = ProjectStore(tmp_path)
    original = store.create('2026-10-29', document('research-note'))
    ref = identity(original)
    with pytest.raises(InvalidProject):
        organize(store, '2026-10-30', ref, role='Reference', selected=True, expected_revision=0, actor='user', request='r')
    def revoked():
        raise ValueError('revoked')
    with pytest.raises(ValueError, match='revoked'):
        organize(store, original.session, ref, role='Reference', selected=True, expected_revision=0, actor='user', request='r', check=revoked)
    assert store.read(original.id, original.session).revision == 1
    organize(store, original.session, ref, role='Reference', selected=True, expected_revision=0, actor='user', request='r')
    with pytest.raises(Conflict):
        organize(store, original.session, ref, role='Training', selected=True, expected_revision=0, actor='user', request='different')


def test_ui_and_door_use_same_organization_and_grants(project_door):
    from orchestrator.door.projects import access
    client, built = project_door
    first = connect(client); other = connect(client, label='Reader', secret='another-test-reader-secret')
    headers, reader = bearer(first['token']), bearer(other['token'])
    sid = run(client, headers, 'project.create', {'name':'Materials'}, 'make-materials')['outcome']['project']['session']
    original = built.hub.resources.projects.create(sid, document('matrix-result'))
    args = {'reference':identity(original), 'role':'Development diagnostics', 'input':True, 'expectedRevision':0}
    access(built).grant(sid, 'agent:'+other['connection']['id'], 'read', authority='development')
    rejected = client.post('/api/agent/v1/operations', headers=reader, json={'tool':'material.organize', 'args':args, 'project':sid, 'requestKey':'cannot-organize'})
    assert rejected.status_code == 403
    result = run(client, headers, 'material.organize', args, 'organize-material', sid)
    saved = built.hub.resources.projects.read(result['outcome']['artifact']['id'], sid)
    assert result['outcome']['artifact'] == identity(saved)
    assert saved.document['state']['materialMembership']['by'] == 'agent:'+first['connection']['id']
    assert built.day.sid != sid


def test_ui_organization_and_generic_save_cannot_forge_attribution(web):
    client, built = web
    original = built.hub.resources.projects.create(built.day.sid, document('research-note'))
    body = {'reference':identity(original), 'role':'Protocols', 'input':True, 'request':'ui-request', 'expectedRevision':0}
    result = client.post(f'/api/scientific-projects/{original.id}/organization', headers=HEADERS, json=body)
    assert result.status_code == 200, result.text
    saved = result.json()['project']
    assert saved['document']['state']['materialMembership']['by'] == 'user'
    changed = saved['document']; changed['state']['materialMembership']['by'] = 'agent:pretend'
    refused = client.put(f'/api/scientific-projects/{saved["id"]}', headers=HEADERS,
        json={'session':built.day.sid,'expectedRevision':1,'document':changed})
    assert refused.status_code == 400
    assert client.post(f'/api/scientific-projects/{original.id}/organization', json=body).status_code == 403


def test_historical_input_organization_does_not_advance_scientific_head(tmp_path):
    from orchestrator.resources import Index
    from orchestrator.project_material import attach_usage
    store = ProjectStore(tmp_path)
    original = store.create('2026-10-29', document('matrix-result'))
    newer = store.save(original.id, original.session, document('matrix-result', {'value':'new'}), expected_revision=1)
    membership = organize(store, original.session, identity(original), role='Prior baseline', selected=True,
                          expected_revision=0, actor='user', request='old-model')
    model = Index.project_entry(newer)
    assert not attach_usage([model, Index.project_entry(membership)], store)
    [use] = model.material['uses']
    assert use['revision'] == 1 and use['material']['organization']['role'] == 'Prior baseline'
    assert store.read(original.id, original.session).digest == newer.digest
    from orchestrator.door.schema import check
    check(model.material, 'project-material')
    changed = organize(store, original.session, identity(original), role='Earlier baseline', selected=True,
                       expected_revision=1, actor='user', request='rename-group')
    assert changed.revision == 2
    assert store.read(original.id, original.session).revision == 2


@pytest.mark.parametrize('kind', ['workflow-invocation','scientific-workflow-run','scientific-workflow-attempt','research-effort'])
def test_owned_records_refuse_organization(tmp_path,kind):
    store=ProjectStore(tmp_path)
    original=store.create('2026-10-29',document(kind))
    with pytest.raises(InvalidProject,match='own controls'):
        organize(store,original.session,identity(original),role='Group',selected=True,expected_revision=0,actor='user',request='refuse')
    assert store.read(original.id,original.session).revision==1


def test_membership_does_not_establish_producing_provenance_and_exports_its_basis(tmp_path):
    from orchestrator.scientific_provenance import resolve
    from orchestrator.scientific_exchange import _references
    store=ProjectStore(tmp_path)
    original=store.create('2026-10-29',document('research-note'))
    saved=organize(store,original.session,identity(original),role='Protocol',selected=True,expected_revision=0,actor='user',request='note')
    assert resolve(store, original.id, original.session)['recorded'] is False
    assert resolve(store, saved.id, saved.session)['recorded'] is False
    assert _references(saved.document)==[(original.id,original.session,1)]
