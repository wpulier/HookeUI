"""Declared sample data through production HTTP and storage; a mock transport stands in for the internet."""
import hashlib
from types import SimpleNamespace
import pytest
from gpu_worker import bundle_contract
from orchestrator import scientific_readers as readers
from tests.test_scientific_reader_choice import installed, member, declaration  # noqa: F401
from tests.test_scientific_url_intake import server, answer, nothing_kept  # noqa: F401
from tests.test_scientific_project_api import web, ORIGIN  # noqa: F401

HEADERS = {'Origin': ORIGIN, 'X-MicroscopeMind': 'open-scientific-sample'}
CELLS = b'\x93NUMPY sample planes' * 20
SAMPLE = {'id': 'cells', 'name': 'Nuclei, two planes', 'url': 'https://data.example.org/samples/cells.npy',
          'sha256': hashlib.sha256(CELLS).hexdigest(), 'size': len(CELLS), 'licence': 'CC0-1.0'}


def opened(client, built, capability='mine/exact#read', sample='cells', request='sample-request-0123456789'):
    return client.post('/api/scientific-samples', headers=HEADERS, json={'session': built.day.sid, 'capability': capability,
                                                                        'sample': sample, 'request': request})


@pytest.fixture
def declared(installed, server):
    installed('mine/exact', member('read', 'Exact NumPy', declaration(samples=[SAMPLE])))
    installed('mine/rgb', member('read', 'Picture reader', declaration(keeps=['values'], samples=[{**SAMPLE, 'id': 'photo'}])))
    installed('mine/plain', member('read', 'No samples', declaration(patterns=['*.tif'])))
    server.routes[SAMPLE['url']] = answer(200, CELLS, {'content-length': str(len(CELLS))})
    return server


def test_samples_are_listed_from_declarations_with_their_readers_and_nothing_runs(web, declared):
    client, built = web
    rows = client.get('/api/scientific-samples').json()['samples']
    assert [(row['capability'], row['id']) for row in rows] == [('mine/exact#read', 'cells'), ('mine/rgb#read', 'photo')]
    assert rows[0]['sha256'] == SAMPLE['sha256'] and rows[0]['licence'] == 'CC0-1.0' and rows[0]['readerReason'] == ''
    assert 'will not open scientific data' in rows[1]['readerReason'] and 'qualifies nothing' in rows[0]['scope']
    assert declared.seen == []


def test_a_sample_opens_at_its_pinned_digest_and_its_reader_is_then_chosen(web, declared):
    client, built = web
    response = opened(client, built)
    assert response.status_code == 200, response.text
    saved = response.json()['project']; receipt = saved['document']['state']['sourceRemote']
    assert saved['document']['title'] == 'cells.npy' and receipt['sha256'] == SAMPLE['sha256']
    assert receipt['sample'] == {'capability': 'mine/exact#read', 'id': 'cells', 'name': 'Nuclei, two planes', 'licence': 'CC0-1.0'}
    assert opened(client, built).json()['project'] == saved and len(declared.seen) == 1
    found = readers.discover(built.hub.resources.projects, saved['id'], saved['session'], 1)
    assert found['choice']['capability'] == 'mine/exact#read'


def test_bytes_that_differ_from_the_declaration_are_refused_and_keep_nothing(web, declared):
    client, built = web
    declared.routes[SAMPLE['url']] = answer(200, CELLS[:-1] + b'!')
    response = opened(client, built)
    assert response.status_code == 400 and f'not the expected {SAMPLE["sha256"]}' in response.json()['says']
    assert nothing_kept(built)


@pytest.mark.parametrize('capability,sample', [('mine/exact#read', 'missing'), ('mine/other#read', 'cells')])
def test_an_undeclared_sample_is_refused_before_any_download(web, declared, capability, sample):
    client, built = web
    response = opened(client, built, capability, sample)
    assert response.status_code == 404 and 'not declared by an installed reader' in response.json()['says']
    assert declared.seen == [] and nothing_kept(built)


def test_a_sample_open_needs_the_studio(web, declared):
    client, built = web
    response = client.post('/api/scientific-samples', json={'session': built.day.sid, 'capability': 'mine/exact#read',
                                                            'sample': 'cells', 'request': 'sample-request-0123456789'})
    assert response.status_code == 403 and declared.seen == []


@pytest.mark.parametrize('change', [{'url': 'http://data.example.org/a.npy'}, {'sha256': 'A' * 64}, {'size': 2048},
                                    {'size': True}, {'id': 'Bad id'}, {'command': 'make_sample'}, {'licence': ''}, {'name': ' '}])
def test_sample_declarations_are_pinned_https_files(change):
    owner = SimpleNamespace(key='mine/reader', runner='python-env', requires={'catalog': 3}, members=())
    good = member('read', 'Reader', declaration(samples=[SAMPLE]))
    assert bundle_contract.parse_members([good], owner)[0].reader['samples'][0] == SAMPLE
    for bad in (declaration(samples=[{**SAMPLE, **change}]), declaration(samples=[SAMPLE, SAMPLE]),
                declaration(kind='directory', samples=[SAMPLE])):
        with pytest.raises(ValueError, match='format-3 reader'):
            bundle_contract.parse_members([member('read', 'Reader', bad)], owner)


def test_a_sample_must_be_exactly_its_declared_size(web, declared):
    client, built = web
    declared.routes[SAMPLE['url']] = answer(200, CELLS + b'more', {'content-length': str(len(CELLS) + 4)})
    response = opened(client, built)
    assert response.status_code == 400 and f'not the declared {len(CELLS):,}' in response.json()['says']
    declared.routes[SAMPLE['url']] = answer(200, CELLS + b'more')
    response = opened(client, built)
    assert response.status_code == 400 and 'grew past the declared' in response.json()['says']
    assert nothing_kept(built)


def test_samples_of_a_catalog_whose_code_is_not_allowed_are_listed_but_not_opened(web, declared, monkeypatch):
    client, built = web
    monkeypatch.setattr(readers.channels, 'trusted', lambda owner, environ: owner.key != 'mine/exact')
    rows = {row['id']: row for row in client.get('/api/scientific-samples').json()['samples']}
    assert not rows['cells']['canOpen'] and 'Allow this catalog' in rows['cells']['reason'] and rows['photo']['canOpen']
    response = opened(client, built)
    assert response.status_code == 403 and declared.seen == [] and nothing_kept(built)
