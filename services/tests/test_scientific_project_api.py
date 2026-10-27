"""Synthetic scientific documents through production storage, HTTP and resource indexing."""
import json

import pytest
from fastapi.testclient import TestClient

from orchestrator.paths import Paths
from orchestrator.server import create_app
from tests.test_scientific_projects import document

ORIGIN = 'http://127.0.0.1:8770'
HEADERS = {'Origin': ORIGIN, 'X-MicroscopeMind': 'save-project'}


@pytest.fixture
def web(tmp_path):
    app = create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path))
    built = app.state.app
    built._project_test_baseline = project_files(built)
    return TestClient(app, base_url=ORIGIN), built


def project_files(built):
    root = built.hub.resources.projects.root
    return {str(path.relative_to(root)): path.read_bytes()
            for path in root.rglob('*') if path.is_file()}


def create(client, built, **changes):
    return client.post('/api/scientific-projects', headers=HEADERS,
                       json={'session': built.day.sid, 'document': document(), **changes})


def test_save_reopen_and_common_resource_read_preserve_exact_revisions(web):
    client, built = web
    first = create(client, built)
    assert first.status_code == 200, first.text
    item = first.json()['project']
    id_, sid = item['id'], item['session']
    changed = {**document(), 'title': 'Edited keyframes'}
    saved = client.put(f'/api/scientific-projects/{id_}', headers=HEADERS,
                       json={'session': sid, 'expectedRevision': 1, 'document': changed})
    assert saved.status_code == 200, saved.text
    assert saved.json()['project']['revision'] == 2
    stale = client.put(f'/api/scientific-projects/{id_}', headers=HEADERS,
                       json={'session': sid, 'expectedRevision': 1, 'document': document()})
    assert stale.status_code == 409
    assert stale.json()['next'] == 'reopen the latest saved revision and review your changes before saving again'
    original = client.get(f'/api/scientific-projects/{id_}', params={'session': sid, 'revision': 1})
    assert original.json()['project'] == item
    resource = client.get(f'/api/resources/{id_}', params={'day': sid}).json()
    assert resource['entry']['version'] == 2
    assert resource['detail']['values'] == changed
    assert resource['entry']['source']['sha256'] == saved.json()['project']['digest']
    listed = client.get('/api/resources', params={'kind': 'project', 'day': sid}).json()
    assert [e['id'] for e in listed['entries']] == [id_]
    assert client.get(f'/api/scientific-projects/{id_}', params={'session': '2020-01-01'}).status_code == 400
    # A new application instance reads the same store without any installed package.
    restarted = create_app('http://127.0.0.1:1', use_gpu=False, paths=built.paths)
    reopened = TestClient(restarted).get(f'/api/resources/{id_}', params={'day': sid})
    assert reopened.json() == resource


@pytest.mark.parametrize('headers', [{}, {'Origin': 'https://elsewhere.invalid', 'X-MicroscopeMind': 'save-project'},
                                     {'Origin': ORIGIN, 'X-MicroscopeMind': 'actor:user'}])
def test_writes_require_studio_provenance(web, headers):
    client, built = web
    result = client.post('/api/scientific-projects', headers=headers,
                        json={'session': built.day.sid, 'document': document()})
    assert result.status_code == 403
    assert result.json()['next'] == 'save the project from the Studio'
    assert project_files(built) == built._project_test_baseline


@pytest.mark.parametrize('changes', [{'session': '2020-01-01'}, {'actor': 'user'}, {'document': []}])
def test_invalid_or_stale_request_leaves_retained_data_alone(web, changes):
    client, built = web
    response = create(client, built, **changes)
    assert response.status_code in (400, 409)
    assert response.json()['next']
    assert project_files(built) == built._project_test_baseline


def test_corruption_is_reported_without_hiding_other_projects(web):
    client, built = web
    good = create(client, built).json()['project']
    bad = create(client, built).json()['project']
    record = built.hub.resources.projects.root / bad['id'] / 'revisions' / (bad['digest'] + '.json')
    record.write_text('{}')
    listed = client.get('/api/resources', params={'kind': 'project'}).json()
    assert [e['id'] for e in listed['entries']] == [good['id']]
    assert any(bad['id'] in error for error in listed['unreadable'])


def test_oversized_request_and_duplicate_json_keys_are_refused(web):
    client, built = web
    for data in ('x' * ((2 << 20) + 8193),
                 '{"session": "bad", "session": ' + json.dumps(built.day.sid) + ', "document": {}}'):
        result = client.post('/api/scientific-projects', headers=HEADERS, content=data)
        assert result.status_code in (400, 413)
        assert result.json()['next']
    assert project_files(built) == built._project_test_baseline


@pytest.mark.parametrize(('data', 'says', 'next_step'), [
    ('{"document": {}, "document": {}}', 'A project field was repeated.', 'send one value for each project field'),
    ('{', 'The project request is not valid JSON.', 'send a valid project document from the Studio'),
    ('[]', 'The request must contain one project document.', 'send one project document from the Studio'),
])
def test_body_refusals_keep_the_specific_reason_and_next_step(web, data, says, next_step):
    client, built = web
    result = client.post('/api/scientific-projects', headers=HEADERS, content=data)
    assert result.status_code == 400
    assert result.json() == {'ok': False, 'says': says, 'next': next_step}
    assert project_files(built) == built._project_test_baseline


def test_storage_failure_offers_recovery_without_claiming_a_save(web, monkeypatch):
    client, built = web
    def unavailable(*args):
        raise OSError('private storage detail')
    monkeypatch.setattr(built.hub.resources.projects, 'create', unavailable)
    result = create(client, built)
    assert result.status_code == 503 and result.json()['ok'] is False
    assert result.json()['next'] == 'reopen the last saved revision before retrying'
    assert 'private storage detail' not in result.text


def test_session_change_after_admission_keeps_work_in_original_session(web, monkeypatch):
    client, built = web
    store = built.hub.resources.projects
    original, sid = store.create, built.day.sid
    def save_then_switch(*args):
        item = original(*args)
        built.day.sid = '2026-10-16.999'
        return item
    monkeypatch.setattr(store, 'create', save_then_switch)
    response = create(client, built)
    assert response.status_code == 200
    item = response.json()['project']
    assert response.json()['sessionChanged'] is True and item['session'] == sid
    assert store.read(item['id'], sid).document == document()
    assert store.list(built.day.sid) == []


def test_resource_identity_lookup_never_scans_all_projects(web, monkeypatch):
    client, built = web
    item = create(client, built).json()['project']
    store = built.hub.resources.projects
    def no_scan(*args, **kwargs):
        pytest.fail('An identity lookup must not scan scientific projects')
    monkeypatch.setattr(store, 'scan', no_scan)
    index = built.hub.resources
    assert index.get('d999') is None
    assert index.get('p53') is None  # a scientific term must not cause project file reads
    assert index.get(item['id']).version == 1


def test_native_save_uses_shared_memory_admission_and_retries_without_losing_work(web, monkeypatch):
    import numpy as np
    from orchestrator.capture import captures
    from orchestrator.scientific_arrays import SAVE_WORKING_BYTES
    client, built = web
    store, memory = built.hub.resources.projects, captures(built.hub).memory
    item = create(client, built).json()['project']
    draft = store.array_draft(item['id'], item['session'], component='array.labels', shape=(2, 3),
                              dtype='<u2', axes=('y', 'x'), chunks=(2, 3), scientific={'semantic': 'instance-labels'},
                              max_written_bytes=12)
    draft.write((0, 0), np.full((2, 3), 65000, dtype='<u2'))
    reference = draft.finish()
    doc = document() | {'resources': [reference]}
    request = {'session': item['session'], 'expectedRevision': 1, 'document': doc}
    monkeypatch.setitem(memory.allowances, 'analysis', SAVE_WORKING_BYTES - 1)
    refused = client.put(f"/api/scientific-projects/{item['id']}", headers=HEADERS, json=request)
    assert refused.status_code == 503 and refused.json()['next']
    assert store.read(item['id'], item['session']).revision == 1
    assert memory.reserved['analysis'] == 0
    monkeypatch.setitem(memory.allowances, 'analysis', SAVE_WORKING_BYTES)
    saved = client.put(f"/api/scientific-projects/{item['id']}", headers=HEADERS, json=request)
    assert saved.status_code == 200, saved.text
    assert saved.json()['project']['revision'] == 2
    assert memory.peak['analysis'] == SAVE_WORKING_BYTES and memory.reserved['analysis'] == 0
    reopened = store.array_source(item['id'], item['session'], 2, reference)
    assert reopened.read((slice(0, 2), slice(0, 3))).tolist() == [[65000] * 3] * 2
