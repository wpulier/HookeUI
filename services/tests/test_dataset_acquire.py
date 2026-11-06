"""dataset.acquire through real Project storage: a mock transport and resolver stand in for GEO, figshare and depmap.org.

The night's sources are the shapes tested: a GEO file with no checksum (size only), figshare+ files whose article
states size, md5 and licence, and a DepMap file whose publisher answers automated clients with a verification page,
so its figshare mirror is used and the receipt says why."""
import hashlib
import json
import socket

import httpx
import pytest

from orchestrator import dataset_acquire as acquire
from orchestrator import scientific_url_intake as web
from orchestrator.scientific_projects import ProjectStore, InvalidProject

GEO = 'https://ftp.ncbi.nlm.nih.gov/geo/series/GSE264nnn/GSE264667/suppl/GSE264667_hepg2_raw_singlecell_01.h5ad'
DEPMAP = 'https://depmap.org/portal/data_page/?tab=allData&filename=Model.csv'
FIGSHARE_FILE = 'https://ndownloader.figshare.com/files/51065297'
S3 = 'https://s3-eu-west-1.amazonaws.com/pstorage-plus/51065297/Model.csv?X-Amz-Expires=10'
ARTICLE = 'https://api.figshare.com/v2/articles/27993248'
HOSTS = {'ftp.ncbi.nlm.nih.gov': '130.14.250.10', 'depmap.org': '104.18.1.1', 'ndownloader.figshare.com': '52.48.1.1',
         's3-eu-west-1.amazonaws.com': '52.218.1.1', 'api.figshare.com': '52.48.1.2'}
H5AD = bytes(range(256)) * 400 + b'end'
MODEL = b'ModelID,CellLineName\nACH-000001,NIH:OVCAR-3\n' * 300
SESSION = '2026-11-06'


def md5(data):
    return hashlib.md5(data).hexdigest()


def sha(data):
    return hashlib.sha256(data).hexdigest()


class Server:
    """Serves files with HTTP ranges (If-Range on an ETag); records every request; can drop a transfer midway."""

    def __init__(self):
        self.files, self.pages, self.redirects, self.json = {}, {}, {}, {}
        self.seen, self.drop_after = [], {}

    def __call__(self, request):
        url = str(request.url)
        self.seen.append(request)
        if url in self.redirects:
            return httpx.Response(302, headers={'location': self.redirects[url]})
        if url in self.pages:
            return httpx.Response(200, content=iter([self.pages[url]]), headers={'content-type': 'text/html; charset=utf-8'})
        if url in self.json:
            return httpx.Response(200, content=iter([json.dumps(self.json[url]).encode()]), headers={'content-type': 'application/json'})
        if url not in self.files:
            return httpx.Response(404)
        body, tag = self.files[url], '"' + sha(self.files[url])[:16] + '"'
        start, status, headers = 0, 200, {'etag': tag, 'content-type': 'application/octet-stream'}
        wanted = request.headers.get('range')
        if wanted and request.headers.get('if-range') == tag:
            start = int(wanted.split('=')[1].split('-')[0])
            if start >= len(body):
                return httpx.Response(416, headers={'content-range': f'bytes */{len(body)}'})
            status = 206
            headers['content-range'] = f'bytes {start}-{len(body) - 1}/{len(body)}'
        part = body[start:]
        headers['content-length'] = str(len(part))
        drop = self.drop_after.get(url)

        def stream():
            sent = 0
            for i in range(0, len(part), 4096):
                if drop is not None and start + sent >= drop:
                    raise httpx.ReadError('connection reset')
                yield part[i:i + 4096]
                sent += len(part[i:i + 4096])
        return httpx.Response(status, content=stream(), headers=headers)


@pytest.fixture
def server(monkeypatch):
    served = Server()
    def resolve(host, port, type=0):
        if host not in HOSTS:
            raise socket.gaierror('unknown')
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (HOSTS[host], port))]
    monkeypatch.setattr(web, 'RESOLVE', resolve)
    monkeypatch.setattr(acquire, 'TRANSPORT', httpx.MockTransport(served))
    monkeypatch.setattr(acquire, 'RETRY_SECONDS', (0, 0, 0))
    served.files[GEO] = H5AD
    served.redirects[FIGSHARE_FILE] = S3
    served.files[S3] = MODEL
    served.pages[DEPMAP] = b'<!DOCTYPE html><title>DepMap \xe2\x80\x94 Verification</title>'
    served.json[ARTICLE] = {'version': 1, 'doi': '10.25452/figshare.plus.27993248.v1',
                            'license': {'value': 1, 'name': 'CC BY 4.0', 'url': 'https://creativecommons.org/licenses/by/4.0/'},
                            'files': [{'id': 51065297, 'name': 'Model.csv', 'size': len(MODEL), 'computed_md5': md5(MODEL),
                                       'supplied_md5': md5(MODEL), 'download_url': FIGSHARE_FILE}]}
    return served


@pytest.fixture
def store(tmp_path):
    return ProjectStore(tmp_path / 'documents')


@pytest.fixture
def spool(tmp_path):
    return tmp_path / 'home' / 'downloads' / acquire.SPOOL


def acquiring(store, spool, request, manifest):
    return acquire.Acquisition(store, SESSION, request, manifest, spool=spool)


def nadig():
    return {'format': 'dataset-manifest-v1', 'name': 'Nadig 2025 HepG2 (GSE264667)',
            'licence': {'id': 'NCBI-public', 'url': 'https://www.ncbi.nlm.nih.gov/home/about/policies/'},
            'citation': 'Nadig et al. 2025, Nat Genet', 'files': [{'name': 'GSE264667_hepg2_raw_singlecell_01.h5ad',
                                                                   'url': GEO, 'size': len(H5AD)}]}


def depmap():
    return {'format': 'dataset-manifest-v1', 'name': 'DepMap 24Q4 Public',
            'licence': {'id': 'CC-BY-4.0', 'url': 'https://creativecommons.org/licenses/by/4.0/'},
            'figshare': {'article': 27993248},
            'files': [{'name': 'Model.csv', 'url': DEPMAP, 'figshareFile': 51065297,
                       'mirror': {'url': FIGSHARE_FILE, 'why': 'depmap.org answers automated clients with a verification '
                                                               'page; figshare+ holds DepMap\'s own 24Q4 release'}}]}


def test_a_dataset_without_open_terms_is_refused_before_anything_is_fetched(server, store, spool):
    for licence, code in ((None, 'licence-missing'), ({'id': 'proprietary'}, 'licence-not-admitted'),
                          ({'id': 'CC-BY-ND-4.0'}, 'licence-not-admitted')):
        manifest = {**nadig(), 'licence': licence} if licence else {k: v for k, v in nadig().items() if k != 'licence'}
        with pytest.raises(acquire.Refused) as refused:
            acquiring(store, spool, 'acquire-request-0001', manifest).run()
        assert refused.value.code == code
    assert server.seen == []


def test_a_geo_file_is_fetched_verified_and_retained_with_where_it_came_from(server, store, spool):
    receipt, saved = acquiring(store, spool, 'acquire-request-0001', nadig()).run()
    (file,) = saved
    source = file.document['state']['sourceFile']
    record = acquire.record_of(file.document['state'])
    assert source['sha256'] == sha(H5AD) and source['size'] == len(H5AD)
    assert record['from'] == 'primary' and record['source'] == GEO and record['licence']['id'] == 'NCBI-public'
    assert record['md5'] == md5(H5AD) and record['publisher'] == {'size': len(H5AD)}
    assert store.file_source(file.id, SESSION, 1, source['reference']).read(0, 8) == H5AD[:8]
    written = store.read(receipt['artifact']['id'], SESSION)
    body = json.loads(store.file_source(written.id, SESSION, 1, written.document['state']['sourceFile']['reference'])
                      .read(0, written.document['state']['sourceFile']['size']))
    assert body['format'] == 'dataset-receipt-v1' and body['files'][0]['sha256'] == sha(H5AD)
    assert body['files'][0]['artifact']['id'] == file.id and body['manifestSha256'] == acquire.digest(nadig())
    assert not (spool).exists() or not any((spool).iterdir())
    fetched = len(server.seen)
    again, _ = acquiring(store, spool, 'acquire-request-0001', nadig()).run()
    assert again['artifact'] == receipt['artifact'] and len(server.seen) == fetched  # retained: never fetched again


def test_a_dropped_connection_keeps_what_arrived_and_the_retry_asks_for_the_rest(server, store, spool):
    server.drop_after[GEO] = 40960
    with pytest.raises(acquire.Refused) as refused:
        acquiring(store, spool, 'acquire-request-0002', nadig()).run()
    assert refused.value.code == 'connection-dropped'
    parts = list((spool).rglob('*.part'))
    assert len(parts) == 1 and parts[0].stat().st_size == 40960
    assert store.list(SESSION) == []
    del server.drop_after[GEO]
    server.seen.clear()
    _, (file,) = acquiring(store, spool, 'acquire-request-0002', nadig()).run()
    assert server.seen[0].headers['range'] == 'bytes=40960-' and server.seen[0].headers['if-range']
    assert file.document['state']['sourceFile']['sha256'] == sha(H5AD)


def test_a_changed_file_is_never_spliced_onto_what_arrived_before(server, store, spool):
    server.drop_after[GEO] = 8192
    with pytest.raises(acquire.Refused):
        acquiring(store, spool, 'acquire-request-0003', nadig()).run()
    changed = bytes(reversed(H5AD))
    server.files[GEO] = changed  # a new ETag: If-Range fails, so the server sends the whole new file
    del server.drop_after[GEO]
    with pytest.raises(acquire.Refused) as refused:  # the manifest pins no checksum, but the size and bytes are whole
        manifest = nadig()
        manifest['files'][0]['sha256'] = sha(H5AD)
        acquiring(store, spool, 'acquire-request-0003', manifest).run()
    assert 'sha256' in refused.value.says and refused.value.code == 'source-unavailable'
    assert store.list(SESSION) == [] and not list((spool).rglob('*.part'))


def test_a_verification_page_moves_to_the_mirror_and_the_receipt_says_so(server, store, spool):
    receipt, (file,) = acquiring(store, spool, 'acquire-request-0004', depmap()).run()
    record = acquire.record_of(file.document['state'])
    assert record['from'] == 'mirror' and record['source'] == FIGSHARE_FILE
    assert record['final'] == S3.split('?')[0]  # a pre-signed query expires in seconds and is never kept
    assert 'web page' in record['primaryRefused'] and 'verification page' in record['mirrorWhy']
    assert record['publisher']['figshare']['article'] == 27993248 and record['md5'] == md5(MODEL)
    assert receipt['files'][0]['from'] == 'mirror' and 'primaryRefused' in receipt['files'][0]
    assert all('cookie' not in r.headers and 'authorization' not in r.headers for r in server.seen)


def test_the_publisher_states_size_md5_and_licence_and_a_disagreeing_manifest_is_refused(server, store, spool):
    wrong = depmap()
    wrong['files'][0]['md5'] = '0' * 32
    with pytest.raises(acquire.Refused) as refused:
        acquiring(store, spool, 'acquire-request-0005', wrong).run()
    assert refused.value.code == 'checksum-mismatch'
    relicensed = {**depmap(), 'licence': {'id': 'CC0-1.0'}}
    with pytest.raises(acquire.Refused) as refused:
        acquiring(store, spool, 'acquire-request-0005', relicensed).run()
    assert refused.value.code == 'licence-mismatch'
    server.files[S3] = MODEL[:-1] + b'X'  # same size, other bytes: the publisher's md5 catches it
    with pytest.raises(acquire.Refused) as refused:
        acquiring(store, spool, 'acquire-request-0006', depmap()).run()
    assert 'md5' in refused.value.says and store.list(SESSION) == []


def test_too_little_space_refuses_before_anything_is_fetched(server, store, spool, monkeypatch):
    from collections import namedtuple
    usage = namedtuple('usage', 'total used free')
    monkeypatch.setattr(acquire.shutil, 'disk_usage', lambda path: usage(1 << 40, 1 << 40, 10 << 20))
    with pytest.raises(acquire.Refused) as refused:
        acquiring(store, spool, 'acquire-request-0007', nadig()).run()
    assert refused.value.code == 'disk' and server.seen == []


def test_a_stop_keeps_what_arrived(server, store, spool):
    calls = []
    def stopping():
        calls.append(1)
        return len(calls) > 4
    with pytest.raises(acquire.Stopped):
        acquiring(store, spool, 'acquire-request-0008', nadig()).run(stopping)
    assert store.list(SESSION) == [] and list((spool).rglob('*.part'))


def test_only_acquisition_writes_an_acquisition_receipt(server, store, spool):
    _, (file,) = acquiring(store, spool, 'acquire-request-0009', nadig()).run()
    forged = json.loads(json.dumps(file.document))
    forged['state']['sourceDataset']['source'] = 'https://elsewhere.example.org/x'
    with pytest.raises(InvalidProject):
        web.guard(file, forged)
    plain = {**forged, 'state': {k: v for k, v in forged['state'].items() if k != 'sourceDataset'}}
    with pytest.raises(InvalidProject):
        web.guard(file, plain)
    with pytest.raises(InvalidProject):
        acquire.record_of({**forged['state'], 'sourceDataset': {**forged['state']['sourceDataset'], 'from': 'cache'}})


def test_the_nights_manifests_fit_the_contract():
    from pathlib import Path
    folder = Path(__file__).resolve().parents[2] / 'examples' / 'datasets' / 'vcc-2026'
    names = sorted(p.name for p in folder.glob('*.json'))
    assert names == ['depmap-24q4.json', 'nadig-2025-gse264667.json', 'replogle-2022-k562-essential.json']
    for path in folder.glob('*.json'):
        manifest = acquire.check_manifest(json.loads(path.read_text()))
        assert all(f.get('sha256') for f in manifest['files'])  # pinned from the night's own copies
