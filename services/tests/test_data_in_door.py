"""Data into a Project through the one door (HOOKE-GAPS rows 1-7): dataset.acquire, a zip's members verified on
project.import, a PDF's text in artifact.read, and read-only matrix queries (target overlap, profile match)."""
import hashlib
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import h5py
import numpy as np
import pytest

from orchestrator import pdf_text
from tests.test_agent_door import connect, bearer, window_key  # noqa: F401  (signs the studio's connection answer)
from tests.test_agent_projects import project_door  # noqa: F401  (a real Project store behind the door)
from tests.test_dataset_acquire import server, nadig, H5AD  # noqa: F401  (the mock web)
from tests.waiting import until

MODELS = Path(__file__).resolve().parents[2] / 'catalog' / 'models'


def finish(client, headers, tool, args, key, project=None):
    response = client.post('/api/agent/v1/operations', json={'tool': tool, 'args': args, 'requestKey': key,
                                                               **({'project': project} if project else {})}, headers=headers)
    assert response.status_code in (200, 202), response.text
    op = response.json()['operation']
    def ended():
        value = client.get('/api/agent/v1/operations/' + op['id'], headers=headers).json()['operation']
        return value if value['state'] in ('succeeded', 'failed', 'interrupted', 'cancelled') else None
    return until(ended)


def project(client):
    agent = connect(client)
    headers = bearer(agent['token'])
    sid = finish(client, headers, 'project.create', {'name': 'Data in'}, 'data-in-project-01')['outcome']['project']['session']
    return headers, sid


def test_an_agent_acquires_a_dataset_and_a_closed_licence_is_refused(project_door, server, monkeypatch):
    from orchestrator.door import listing
    monkeypatch.setattr(listing, 'gather', lambda built: [])  # the door's own actions only
    client, built = project_door
    headers, sid = project(client)
    for name in ('dataset.acquire', 'matrix.query'):
        listed = client.get(f'/api/agent/v1/tools?q={name}', headers=headers).json()['tools']
        assert any(t['id'] == name for t in listed), listed
    closed = finish(client, headers, 'dataset.acquire', {'manifest': {**nadig(), 'licence': {'id': 'proprietary'}}},
                    'data-in-acquire-01', sid)
    assert closed['state'] == 'failed' and closed['outcome']['code'] == 'licence-not-admitted'
    assert server.seen == []
    done = finish(client, headers, 'dataset.acquire', {'manifest': nadig()}, 'data-in-acquire-02', sid)
    assert done['state'] == 'succeeded', done
    (file,) = done['outcome']['files']
    assert file['sha256'] == hashlib.sha256(H5AD).hexdigest() and file['from'] == 'primary'
    saved = built.hub.resources.projects.read(file['artifact']['id'], sid)
    assert saved.document['state']['sourceImport']['producer']['kind'] == 'dataset-acquire'
    assert not list(built.hub.resources.projects.root.rglob('*.part'))  # half a file never sits in a Project
    receipt = finish(client, headers, 'artifact.read', {'reference': done['outcome']['receipt']}, 'data-in-read-01', sid)
    assert json.loads(receipt['outcome']['content']['text'])['files'][0]['artifact'] == file['artifact']


def members_zip(path, members):
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, data in members.items():
            z.writestr(name, data)
    return path


def test_importing_a_zip_reads_every_member_and_finds_the_same_bytes_already_kept(project_door, tmp_path):
    client, built = project_door
    headers, sid = project(client)
    real = b'cell,target\n' + b'1,ABCC1\n' * 5000
    (tmp_path / 'real.csv').write_bytes(real)
    kept = finish(client, headers, 'project.import', {'file': str(tmp_path / 'real.csv')}, 'data-in-import-01', sid)
    archive = members_zip(tmp_path / 'organizer.zip', {'final/real.csv': real, 'final/README.md': b'read me'})
    result = finish(client, headers, 'project.import', {'file': str(archive)}, 'data-in-import-02', sid)
    assert result['state'] == 'succeeded', result
    record = result['outcome']['archive']
    assert record['verified'] and record['memberCount'] == 2
    row = next(m for m in record['members'] if m['name'] == 'final/real.csv')
    assert row['sha256'] == hashlib.sha256(real).hexdigest()
    assert [r['id'] for r in row['sameBytesAs']] == [kept['outcome']['artifact']['id']]
    saved = built.hub.resources.projects.read(result['outcome']['artifact']['id'], sid)
    assert saved.document['state']['sourceArchive']['members'][0]['crc32']
    renamed = tmp_path / 'organizer-bundle'  # a zip by its bytes, whatever its name
    renamed.write_bytes(archive.read_bytes())
    sniffed = finish(client, headers, 'project.import', {'file': str(renamed)}, 'data-in-import-07', sid)
    assert sniffed['outcome']['archive']['memberCount'] == 2
    expected = {'final/real.csv': hashlib.sha256(real).hexdigest()}
    again = finish(client, headers, 'project.import', {'file': str(archive), 'members': expected}, 'data-in-import-03', sid)
    assert again['state'] == 'succeeded' and again['outcome']['archive']['expectedMatched'] == 1
    wrong = finish(client, headers, 'project.import', {'file': str(archive), 'members': {'final/real.csv': '0' * 64}},
                   'data-in-import-04', sid)
    assert wrong['state'] == 'failed' and 'not the expected' in wrong['outcome']['says']


def test_a_damaged_archive_member_is_never_imported(project_door, tmp_path):
    client, built = project_door
    headers, sid = project(client)
    archive = members_zip(tmp_path / 'damaged.zip', {'data.bin': b'\x00' * 1000})
    body = bytearray(archive.read_bytes())
    at = body.index(b'PK\x01\x02')  # the central directory's CRC of the one member
    body[at + 16:at + 20] = b'\x01\x02\x03\x04'
    at = 0  # and its local header's, so the member reads as stored but checks as damaged
    body[at + 14:at + 18] = b'\x01\x02\x03\x04'
    archive.write_bytes(bytes(body))
    before = len(built.hub.resources.projects.list(sid))
    failed = finish(client, headers, 'project.import', {'file': str(archive)}, 'data-in-import-05', sid)
    assert failed['state'] == 'failed' and 'fails its check' in failed['outcome']['says']
    assert len(built.hub.resources.projects.list(sid)) == before


def tiny_pdf(text):
    """A one-page PDF with `text` in Helvetica, its cross-reference table exact."""
    stream = f'BT /F1 18 Tf 72 720 Td ({text}) Tj ET'.encode()
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>', b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
               b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>']
    out, offsets = io.BytesIO(), []
    out.write(b'%PDF-1.4\n')
    for n, body in enumerate(objects, 1):
        offsets.append(out.tell())
        out.write(b'%d 0 obj\n' % n + body + b'\nendobj\n')
    xref = out.tell()
    out.write(b'xref\n0 %d\n0000000000 65535 f \n' % (len(objects) + 1))
    for at in offsets:
        out.write(b'%010d 00000 n \n' % at)
    out.write(b'trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n' % (len(objects) + 1, xref))
    return out.getvalue()


def test_a_pdf_reads_as_its_extracted_text_a_page_at_a_time(project_door, tmp_path, monkeypatch):
    client, built = project_door
    headers, sid = project(client)
    (tmp_path / 'paper.pdf').write_bytes(tiny_pdf('placeholder'))
    ref = finish(client, headers, 'project.import', {'file': str(tmp_path / 'paper.pdf')}, 'data-in-import-06', sid)['outcome']['artifact']
    text = 'Methods' + ' word' * 20000 + '\fSecond page'
    runs = []

    def fake(pdf, out, script):
        runs.append(pdf)
        return [sys.executable, '-c', f'import sys; open(sys.argv[1], "w").write({text!r})', out]
    monkeypatch.setattr(pdf_text, 'extractors', lambda: [('stand-in extractor', fake)])
    first = finish(client, headers, 'artifact.read', {'reference': ref}, 'data-in-read-02', sid)['outcome']
    page = first['content']
    assert page['text'] == text[:65536] and page['nextOffset'] == 65536 and page['totalCharacters'] == len(text)
    assert page['pages'] == {'first': 1, 'last': 1, 'total': 2} and page['extractedBy'] == 'stand-in extractor'
    assert first['untrusted'] is True and 'download' in first
    rest = finish(client, headers, 'artifact.read', {'reference': ref, 'offset': 65536}, 'data-in-read-03', sid)['outcome']
    assert page['text'] + rest['content']['text'] == text and rest['content']['pages']['last'] == 2
    assert len(runs) == 1  # extracted once, then read from the cache

    def broken(pdf, out, script):
        return [sys.executable, '-c', 'import sys; sys.exit("not a readable PDF")']
    monkeypatch.setattr(pdf_text, 'extractors', lambda: [('stand-in extractor', broken)])
    shutil.rmtree(Path(built.paths.home) / 'cache' / 'pdf-text')
    failed = finish(client, headers, 'artifact.read', {'reference': ref}, 'data-in-read-04', sid)['outcome']
    assert 'not a readable PDF' in failed['contentUnavailable'] and 'content' not in failed


@pytest.mark.skipif(not pdf_text.extractors(), reason='no PDF text extractor on this computer')
def test_a_real_extractor_reads_a_real_pdf(tmp_path):
    (tmp_path / 'a.pdf').write_bytes(tiny_pdf('Hooke reads this sentence'))
    value = pdf_text.extract(tmp_path / 'a.pdf')
    assert 'Hooke reads this sentence' in value['text'] and value['pages'] == [0]


def write_h5ad(path, *, targets, counts, genes, legacy=False):
    """A minimal AnnData file as h5py writes it: obs with a target column, var names, CSR X."""
    from scipy import sparse
    with h5py.File(path, 'w') as f:
        obs = f.create_group('obs')
        names = np.array([f'cell{i}' for i in range(len(targets))], dtype=object)
        text = h5py.string_dtype()
        obs.attrs.update({'_index': '_index', 'encoding-type': 'dataframe', 'encoding-version': '0.2.0'})
        obs.create_dataset('_index', data=names, dtype=text)
        categories = sorted(set(targets))
        codes = np.array([categories.index(t) for t in targets], dtype=np.int8)
        if legacy:
            obs.attrs['column-order'] = ['gene']
            obs.create_dataset('gene', data=codes)
            obs.create_group('__categories').create_dataset('gene', data=np.array(categories, dtype=object), dtype=text)
        else:
            obs.attrs['column-order'] = ['target_gene', 'strength']
            column = obs.create_group('target_gene')
            column.attrs.update({'encoding-type': 'categorical', 'encoding-version': '0.2.0', 'ordered': False})
            column.create_dataset('codes', data=codes)
            column.create_dataset('categories', data=np.array(categories, dtype=object), dtype=text)
            obs.create_dataset('strength', data=np.arange(len(targets), dtype=np.float64))
        var = f.create_group('var')
        var.attrs.update({'_index': '_index', 'encoding-type': 'dataframe', 'encoding-version': '0.2.0',
                          'column-order': np.array([], dtype=np.float64)})
        index = var.create_group('_index')  # AnnData 0.11+: a nullable string array
        index.attrs.update({'encoding-type': 'nullable-string-array', 'encoding-version': '0.1.0'})
        index.create_dataset('values', data=np.array(genes, dtype=object), dtype=text)
        index.create_dataset('mask', data=np.zeros(len(genes), dtype=bool))
        matrix = sparse.csr_matrix(np.asarray(counts, dtype=np.float32))
        x = f.create_group('X')
        x.attrs.update({'encoding-type': 'csr_matrix', 'encoding-version': '0.1.0', 'shape': np.array(matrix.shape)})
        for key in ('data', 'indices', 'indptr'):
            x.create_dataset(key, data=getattr(matrix, key))
    return path


def load(name):
    import importlib
    sys.path.insert(0, str(MODELS / name))
    try:
        for cached in ('adapter', 'h5ad_read'):
            sys.modules.pop(cached, None)
        return importlib.import_module('adapter')
    finally:
        sys.path.pop(0)
        for cached in ('adapter', 'h5ad_read'):
            sys.modules.pop(cached, None)


def test_both_folders_read_h5ad_files_with_the_same_reader():
    assert (MODELS / 'target-overlap' / 'h5ad_read.py').read_bytes() == (MODELS / 'profile-match' / 'h5ad_read.py').read_bytes()


@pytest.mark.parametrize('legacy', [False, True])
def test_target_overlap_reads_only_annotations(tmp_path, legacy):
    targets = ['non-targeting', 'ABCC1', 'ABCC1', 'TP53', 'non-targeting', 'MYC']
    path = write_h5ad(tmp_path / 'screen.h5ad', targets=targets, counts=np.ones((6, 3)), genes=['g0', 'g1', 'g2'],
                      legacy=legacy)
    (tmp_path / 'list.csv').write_text('target_gene\nABCC1\nACLY\nMYC\n')
    adapter = load('target-overlap')
    params = {'column': 'gene' if legacy else 'target_gene', 'targets_column': 'target_gene', 'control_label': 'non-targeting',
              'value_column': '' if legacy else 'strength', 'name_token': 0}
    out, summary = adapter.run({'matrix': path, 'targets': tmp_path / 'list.csv'}, params, tmp_path, lambda: None)
    result = json.loads(out.read_text())
    assert (result['found'], result['notFound'], result['distinctTargets'], result['controlObservations']) == (2, 1, 3, 2)
    assert result['foundTargets'] == [{'target': 'ABCC1', 'observations': 2}, {'target': 'MYC', 'observations': 1}]
    assert result['notFoundTargets'] == ['ACLY'] and summary['found'] == 2
    if not legacy:
        assert result['valuesByFoundTarget']['ABCC1'] == {'n': 2, 'mean': 1.5, 'median': 1.5, 'min': 1.0, 'max': 2.0}


def test_profile_match_ranks_reference_profiles_and_says_it_is_a_hypothesis(tmp_path):
    rng = np.random.default_rng(7)
    genes = [f'G{i}' for i in range(600)]
    line_a = rng.gamma(2.0, 50.0, 600)
    line_b = rng.gamma(2.0, 50.0, 600)
    controls = np.vstack([rng.poisson(line_a / 10) for _ in range(4)])
    perturbed = rng.poisson(line_b / 10, (2, 600))
    path = write_h5ad(tmp_path / 'context.h5ad', targets=['non-targeting'] * 4 + ['MYC'] * 2,
                      counts=np.vstack([controls, perturbed]), genes=genes)
    header = ',' + ','.join(f'{g} ({i})' for i, g in enumerate(genes))
    rows = [f'ACH-1,' + ','.join(f'{v:.4f}' for v in np.log2(line_b + 1)),
            f'ACH-2,' + ','.join(f'{v:.4f}' for v in np.log2(line_a + 1))]
    (tmp_path / 'expression.csv').write_text(header + '\n' + '\n'.join(rows) + '\n')
    (tmp_path / 'Model.csv').write_text('ModelID,CellLineName\nACH-1,LINE-B\nACH-2,LINE-A\n')
    params = {'cell_column': 'target_gene', 'cell_label': 'non-targeting', 'gene_column': '', 'strip_parenthesised_ids': True,
              'names_id_column': 'ModelID', 'names_label_column': 'CellLineName', 'min_counts': 0.0, 'min_genes': 500, 'top': 5}
    adapter = load('profile-match')
    out, summary = adapter.run({'sample': path, 'reference': tmp_path / 'expression.csv', 'names': tmp_path / 'Model.csv'},
                               params, tmp_path, lambda: None)
    result = json.loads(out.read_text())
    assert result['hypothesis'] is True and 'does not establish identity' in result['meaning']
    assert [r['name'] for r in result['top']] == ['LINE-A', 'LINE-B'] and result['selectedRows'] == 4
    assert result['genesCompared'] == 600 and result['top'][0]['spearman'] > 0.8 > result['top'][1]['spearman']
    assert summary['best']['name'] == 'LINE-A'
    with pytest.raises(ValueError, match='No row'):
        adapter.run({'sample': path, 'reference': tmp_path / 'expression.csv', 'names': tmp_path / 'Model.csv'},
                    {**params, 'cell_label': 'absent'}, tmp_path, lambda: None)


def test_an_agent_queries_a_retained_matrix_through_the_door(project_door, tmp_path, monkeypatch):
    from orchestrator import matrix_capability_runs as runs
    from scientific_compute import catalog
    monkeypatch.setenv('MICROMIND_HEAVY_SLOT', str(tmp_path / 'slot'))
    monkeypatch.setattr(catalog, 'runtime', lambda capability, environ=None: {
        'owner': catalog._runtime_owner(capability), 'prepared': True, 'reason': '', 'backend': 'cpu',
        'evidenceClass': 'synthetic-runtime-admission'})
    monkeypatch.setattr(runs, 'execution_runtime', lambda _: (Path(sys.executable), Path(sys.prefix)))
    client, built = project_door
    headers, sid = project(client)
    write_h5ad(tmp_path / 'screen.h5ad', targets=['non-targeting', 'ABCC1', 'MYC'], counts=np.ones((3, 2)), genes=['g0', 'g1'])
    (tmp_path / 'list.csv').write_text('target_gene\nABCC1\nACLY\n')
    refs = {port: finish(client, headers, 'project.import', {'file': str(tmp_path / name)}, f'data-in-import-q{port}', sid)
            ['outcome']['artifact'] for port, name in (('matrix', 'screen.h5ad'), ('targets', 'list.csv'))}
    refused = client.post('/api/agent/v1/operations', headers=headers, json={  # only read-only queries run here
        'tool': 'matrix.query', 'args': {'capability': 'transfer-factors', 'inputs': refs}, 'requestKey': 'data-in-query-01',
        'project': sid})
    assert refused.status_code == 400 and 'should be one of' in refused.json()['says']
    done = finish(client, headers, 'matrix.query', {'capability': 'target-overlap', 'inputs': refs}, 'data-in-query-02', sid)
    assert done['state'] == 'succeeded', done
    assert done['outcome']['summary']['found'] == 1
    read = finish(client, headers, 'artifact.read', {'reference': done['outcome']['artifact']}, 'data-in-read-05', sid)['outcome']
    assert read['document']['state']['matrixOperation']['capability'] == 'target-overlap'
