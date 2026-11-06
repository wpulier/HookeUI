"""The Project front page record (project_front.py): about, the Project's page, and planned next steps."""
import pytest

from tests.test_scientific_project_api import web, ORIGIN  # noqa: F401
from tests.test_scientific_file_api import upload
from orchestrator.project_front import KIND, PAGE_CSP, ProjectFront, front_id
from orchestrator.project_material import describe
from orchestrator.scientific_projects import Conflict, InvalidProject

OWNER = {'by': 'studio', 'byLabel': 'You'}
AGENT = {'by': 'agent:ac_0123456789abcdef01234567', 'byLabel': 'Claude Code (research)'}
FRONT = {'Origin': ORIGIN, 'X-MicroscopeMind': 'project-front'}
PAGE = b'<!doctype html><title>Our entry</title><h1>C3 is our final entry</h1>'


def retained(client, built, name, body=PAGE, request='page-upload-0123456789'):
    saved = upload(client, built, body=body, name=name, request=request).json()['project']
    return {key: saved[key] for key in ('id', 'session', 'revision', 'digest')}


@pytest.fixture
def front(web):
    client, built = web
    return client, built, ProjectFront(built.hub.resources.projects, clock=lambda: 1793900000.0)


def test_an_untouched_project_reads_empty_and_writes_nothing(front):
    client, built, record = front
    before = len(built.hub.resources.projects.list(built.day.sid))
    assert record.read(built.day.sid) == {'format': 1, 'about': None, 'page': None, 'visual': None, 'next': [], 'changes': [], 'revision': 0}
    assert client.get('/api/project-front', params={'session': built.day.sid}).json()['front']['revision'] == 0
    assert len(built.hub.resources.projects.list(built.day.sid)) == before


def test_about_and_page_are_attributed_revisions(front):
    client, built, record = front
    sid = built.day.sid
    page = retained(client, built, 'entry.html')
    record.set_about(sid, ' Predict how cells respond. ', attribution=OWNER)
    state = record.set_page(sid, page, attribution=AGENT)
    assert state['about'] == 'Predict how cells respond.'
    assert state['page']['id'] == page['id'] and state['page']['name'] == 'entry.html'
    assert state['page']['heading'] == 'Our entry'  # the page's own <title>
    assert [c['did'] for c in state['changes']] == ['about', 'page']
    assert state['changes'][1]['by'] == AGENT['by'] and state['revision'] == 2
    assert record.set_page(sid, page, attribution=AGENT)['revision'] == 2  # the same choice is no new revision
    assert record.set_page(sid, None, attribution=OWNER)['page'] is None


def test_the_page_must_be_an_exact_retained_html_file(front):
    client, built, record = front
    sid = built.day.sid
    with pytest.raises(InvalidProject, match='.html'):
        record.set_page(sid, retained(client, built, 'notes.md', request='notes-upload-0123456789'), attribution=OWNER)
    page = retained(client, built, 'entry.HTML')
    with pytest.raises(InvalidProject):
        record.set_page(sid, {**page, 'digest': 'f' * 64}, attribution=OWNER)


def test_planned_steps_are_idempotent_per_request_and_finish_once(front):
    _, built, record = front
    sid = built.day.sid
    first = record.add_next(sid, request='score', text='Record the score', when='late November', attribution=AGENT)
    item = first['next'][0]
    assert item['text'] == 'Record the score' and item['when'] == 'late November' and item['by'] == AGENT['by']
    again = record.add_next(sid, request='score', text='Record the score', when='late November', attribution=AGENT)
    assert again['revision'] == first['revision'] and len(again['next']) == 1
    with pytest.raises(Conflict):
        record.add_next(sid, request='score', text='Something else', attribution=AGENT)
    done = record.done_next(sid, item['id'], attribution=OWNER)
    assert done['next'][0]['done']['by'] == 'studio'
    assert record.done_next(sid, item['id'], attribution=OWNER)['revision'] == done['revision']
    with pytest.raises(InvalidProject):
        record.done_next(sid, 'nmissing', attribution=OWNER)


def test_the_record_is_project_history_not_an_input(front):
    _, built, record = front
    record.set_about(built.day.sid, 'A Project.', attribution=OWNER)
    saved = built.hub.resources.projects.read(front_id(built.day.sid), built.day.sid)
    assert saved.document['kind'] == KIND
    material = describe(saved.document)
    assert not material['input'] and material['section'] == 'history' and material['record']


def test_studio_changes_need_the_studio_header_and_the_open_project(front):
    client, built, _ = front
    sid = built.day.sid
    refused = client.post('/api/project-front/about', json={'session': sid, 'about': 'x'})
    assert refused.status_code == 403
    moved = client.post('/api/project-front/about', headers=FRONT, json={'session': '2026-01-01.1', 'about': 'x'})
    assert moved.status_code == 409
    wrong = client.post('/api/project-front/about', headers=FRONT, json={'session': sid, 'about': 'x', 'extra': 1})
    assert wrong.status_code == 400
    saved = client.post('/api/project-front/about', headers=FRONT, json={'session': sid, 'about': 'One sentence.'})
    assert saved.status_code == 200 and saved.json()['front']['about'] == 'One sentence.'
    assert saved.json()['front']['changes'][-1]['by'] == 'studio'
    page = retained(client, built, 'entry.html')
    chosen = client.post('/api/project-front/page', headers=FRONT,
                         json={'session': sid, 'page': {'id': page['id'], 'revision': page['revision']}})
    assert chosen.json()['front']['page']['digest'] == page['digest']
    step = client.post('/api/project-front/next', headers=FRONT,
                       json={'session': sid, 'request': 'r-1', 'text': 'Record the score'}).json()['front']['next'][0]
    done = client.post('/api/project-front/done', headers=FRONT, json={'session': sid, 'item': step['id']})
    assert done.json()['front']['next'][0]['done']['by'] == 'studio'


def test_the_page_is_served_sandboxed_and_only_for_html(front):
    client, built, _ = front
    page = retained(client, built, 'entry.html')
    served = client.get(f"/api/scientific-files/{page['id']}/page",
                        params={'session': page['session'], 'revision': page['revision']})
    assert served.status_code == 200 and served.content == PAGE
    assert served.headers['content-type'].startswith('text/html')
    assert served.headers['content-security-policy'] == PAGE_CSP and 'sandbox' in PAGE_CSP
    assert "default-src 'none'" in PAGE_CSP and 'connect-src' not in PAGE_CSP
    other = retained(client, built, 'notes.md', request='notes-upload-0123456789')
    refused = client.get(f"/api/scientific-files/{other['id']}/page",
                         params={'session': other['session'], 'revision': other['revision']})
    assert refused.status_code == 400


def test_the_main_visual_is_an_exact_picture_or_page(front):
    client, built, record = front
    sid = built.day.sid
    picture = retained(client, built, 'cells.png', body=b'\x89PNG fixture bytes', request='picture-upload-0123456789')
    state = record.set_visual(sid, {'id': picture['id'], 'revision': picture['revision']}, attribution=OWNER)
    assert state['visual']['digest'] == picture['digest'] and state['visual']['kind'] == 'image'
    assert state['visual']['name'] == 'cells.png' and state['visual']['sha256']
    page = retained(client, built, 'entry.html')
    assert record.set_visual(sid, page, attribution=AGENT)['visual']['kind'] == 'page'
    with pytest.raises(InvalidProject, match='main visual'):
        record.set_visual(sid, retained(client, built, 'notes.md', request='notes-upload-0123456789'), attribution=OWNER)
    assert record.set_visual(sid, None, attribution=OWNER)['visual'] is None
    chosen = client.post('/api/project-front/visual', headers=FRONT,
                         json={'session': sid, 'visual': {'id': picture['id'], 'revision': picture['revision']}})
    assert chosen.status_code == 200 and chosen.json()['front']['visual']['kind'] == 'image'


def test_the_projects_library_lists_each_projects_front_and_studies(front):
    client, built, record = front
    sid = built.day.sid
    record.set_about(sid, 'A Project.', attribution=OWNER)
    listed = client.get('/api/project-library')
    assert listed.status_code == 200
    row = next(r for r in listed.json()['projects'] if r['session'] == sid)
    assert row['about'] == 'A Project.' and row['studies'] == 0 and row['savedIn'] in ('folder', 'mac')
    assert row['visual'] is None and row['page'] is None


def test_library_rows_are_reused_until_the_project_or_its_record_changes(front, monkeypatch):
    from orchestrator import project_front
    _, built, record = front
    sid = built.day.sid
    project_front._LIBRARY_CACHE.clear()
    reads = []
    original = record.read
    monkeypatch.setattr(record, 'read', lambda session: reads.append(session) or original(session))
    project_front.library_rows(record, built, [sid])
    project_front.library_rows(record, built, [sid])
    assert reads == [sid]  # unchanged: the second read reuses the row
    record.set_about(sid, 'Changed.', attribution=OWNER)
    reads.clear()
    rows = project_front.library_rows(record, built, [sid])
    assert reads == [sid] and rows[0]['about'] == 'Changed.'


def test_warming_fills_the_library_cache_and_stops_when_asked(front):
    import threading
    from orchestrator import project_front
    _, built, record = front
    project_front._LIBRARY_CACHE.clear()
    assert project_front.warm_library(record, built, threading.Event(), pause=0) >= 1
    assert built.day.sid in project_front._LIBRARY_CACHE
    stopped = threading.Event(); stopped.set()
    project_front._LIBRARY_CACHE.clear()
    assert project_front.warm_library(record, built, stopped, pause=0) == 0
    assert not project_front._LIBRARY_CACHE
