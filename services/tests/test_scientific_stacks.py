"""Open files as a stack through production HTTP, storage and source grants; no reader runs here."""
import pytest
from orchestrator import scientific_readers as readers
from orchestrator.scientific_projects import InvalidProject
from tests.test_scientific_directories import payload
from tests.test_scientific_reader_choice import installed, member, declaration, GEN, EXACT  # noqa: F401
from tests.test_scientific_project_api import web, ORIGIN, HEADERS as SAVE  # noqa: F401

HEADERS = {'Origin': ORIGIN, 'X-MicroscopeMind': 'import-scientific-stack'}
PLANES = {'z10.npy': b'plane ten', 'z2.npy': b'plane two!', 'z1.npy': b'plane one'}


def stack(client, built, files=PLANES, axis='z', request='stack-request-0123456789', **changes):
    _, body = payload(files)
    params = {'session': built.day.sid, 'name': 'z planes', 'size': str(sum(map(len, files.values()))),
              'request': request, 'axis': axis, **changes}
    return client.post('/api/scientific-stacks', headers=HEADERS, params={k: v for k, v in params.items() if v is not None},
                       content=body)


def nothing_kept(built):
    root = built.hub.resources.projects.root
    return built.hub.resources.projects.list(built.day.sid) == [] and not list(root.glob('.scientific-import-*'))


def test_a_stack_keeps_the_researchers_order_its_axis_and_each_exact_file(web):
    client, built = web
    response = stack(client, built)
    assert response.status_code == 200, response.text
    saved = response.json()['project']; state = saved['document']['state']
    assert saved['document']['kind'] == 'source-directory'
    assert state['sourceStack'] == {'format': 1, 'axis': 'z', 'count': 3}
    assert [row['path'] for row in state['sourceDirectory']['entries']] == ['z10.npy', 'z2.npy', 'z1.npy']
    for name, body in PLANES.items():
        read = client.get(f'/api/scientific-directories/{saved["id"]}/file',
                          params={'session': saved['session'], 'revision': 1, 'path': name})
        assert read.content == body
    assert stack(client, built).json()['project'] == saved


@pytest.mark.parametrize('files,axis,says', [
    ({'only.npy': b'x'}, 'z', 'at least two files'),
    ({'a.npy': b'x', 'b.tif': b'y'}, 'z', '“a.npy” and “b.tif” differ'),
    ({'a.npy': b'x', 'dir/b.npy': b'y'}, 'z', 'holds files, not folders'),
    ({'a.npy': b'x', 'README': b'y'}, 'z', '“README” has no file type'),
    (PLANES, 'Z', 'lowercase letters'),
    (PLANES, 'z axis', 'lowercase letters'),
    (PLANES, None, 'missing or repeated fields'),
])
def test_a_stack_that_one_reader_could_not_open_plane_by_plane_is_refused_and_keeps_nothing(web, files, axis, says):
    client, built = web
    response = stack(client, built, files=files, axis=axis)
    assert response.status_code == 400 and says in response.json()['says'], response.text
    assert nothing_kept(built)
    assert stack(client, built).status_code == 200


def test_stack_readers_are_chosen_by_declaration_and_receive_the_planes_in_order(web, installed):
    from env_worker.source_files import bind
    from orchestrator.scientific_project_review import _resolve, _files
    from orchestrator.source_files import SourceFileInputs
    client, built = web; store = built.hub.resources.projects
    saved = stack(client, built).json()['project']
    installed('mine/series', member('read', 'Plane stack', declaration(kind='stack', patterns=['*'], entries=['*.npy'])))
    installed('mine/folder', member('read', 'Folder reader', declaration(kind='directory', patterns=['*'])))
    installed('mine/tiff', member('read', 'TIFF stack', declaration(kind='stack', patterns=['*'], entries=['*.tif'])))
    found = readers.discover(store, saved['id'], saved['session'], 1)
    rows = {row['name']: row for row in found['readers']}
    assert found['choice']['capability'] == 'mine/series#read'
    assert rows['Folder reader']['reason'] == 'This reader opens folders; this source is a stack of 3 files'
    assert rows['TIFF stack']['reason'] == 'This reader needs every file to match *.tif; “z10.npy” and 2 more do not'
    bound = readers.bind(store, saved['id'], saved['session'], 1, 'mine/series#read', GEN)
    assert bound.document['state']['sourceReader']['stack'] == {'format': 1, 'axis': 'z', 'count': 3}
    reopened, _, _ = _resolve(store, bound.id, saved['session'], 1)
    spec = reopened.document['state']['scientificView']
    sources = _files(store, bound.id, saved['session'], 1, spec['files'], lambda: None, spec['directories'])
    grants = SourceFileInputs(sources); planes = bind(grants.specifications(), grants.answer)['source']
    assert list(planes) == ['z10.npy', 'z2.npy', 'z1.npy'] and planes['z2.npy'] == b'plane two!'


def test_how_files_were_stacked_cannot_be_rewritten_by_a_save(web):
    client, built = web; store = built.hub.resources.projects
    saved = stack(client, built).json()['project']; document = saved['document']; state = document['state']
    changed = {**document, 'state': {**state, 'sourceStack': {**state['sourceStack'], 'axis': 't'}}}
    refused = client.put(f'/api/scientific-projects/{saved["id"]}', headers=SAVE,
                         json={'session': built.day.sid, 'expectedRevision': 1, 'document': changed})
    assert refused.status_code == 400 and 'written only when Hooke imports them as a stack' in refused.json()['says']
    miscounted = {**document, 'state': {**state, 'sourceStack': {**state['sourceStack'], 'count': 2}}}
    with pytest.raises(InvalidProject, match='keep its axis and its exact files'):
        store.save(saved['id'], saved['session'], miscounted, expected_revision=1)
    assert store.read(saved['id'], saved['session']).revision == 1


def test_stack_reader_patterns_match_the_files_not_the_collection_label(web, installed):
    client, built = web; store = built.hub.resources.projects
    saved = stack(client, built).json()['project']
    installed('mine/npy', member('read', 'NumPy stack', declaration(kind='stack', patterns=['*.npy'])))
    installed('mine/tif', member('read', 'TIFF stack', declaration(kind='stack', patterns=['*.tif', '*.tiff'])))
    found = readers.discover(store, saved['id'], saved['session'], 1)
    rows = {row['name']: row for row in found['readers']}
    assert found['choice']['capability'] == 'mine/npy#read'
    assert rows['TIFF stack']['reason'] == 'This reader opens stacks of files matching *.tif, *.tiff; “z10.npy” and 2 more do not'
    assert not rows['TIFF stack']['matches']


def test_hidden_or_trailing_dot_names_have_no_file_type(web):
    client, built = web
    for files in ({'.hidden': b'a', '.other': b'b'}, {'a.npy': b'a', 'b.': b'b'}):
        response = stack(client, built, files=files)
        assert response.status_code == 400 and 'has no file type' in response.json()['says']
    assert nothing_kept(built)
