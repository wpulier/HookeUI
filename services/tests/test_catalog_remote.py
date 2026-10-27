"""Public GitHub catalogs must work without a developer's Git installation."""
import hashlib

import httpx
import pytest

from gpu_worker import manifests as catalog
from orchestrator import channels, readiness
from tests.test_channels import ENTRY, home  # noqa: F401


@pytest.fixture
def remote(monkeypatch):
    commit = 'a' * 40
    files = {'dark-spots/model.toml': ENTRY.format().encode(),
             'dark-spots/adapter.py': b'raise RuntimeError("must never execute during import")\n',
             'dark-spots/env.lock': b'numpy==2.0.0 --hash=sha256:' + b'b' * 64 + b'\n',
             'dark-spots/modules/helper.py': b'# nested declared support\n'}
    state = {'commit': commit, 'files': files, 'requests': [], 'corrupt': False, 'rows': None,
             'truncated': False, 'status': 200}

    def respond(request):
        state['requests'].append(str(request.url))
        if state['status'] != 200:
            return httpx.Response(state['status'], headers={'Location': 'https://example.test/private'})
        if request.url.host == 'api.github.com':
            if '/commits/' in request.url.path:
                return httpx.Response(200, json={'sha': state['commit']})
            rows = [{'path': 'catalog/models/' + name, 'mode': '100644', 'type': 'blob',
                     'size': len(data), 'sha': hashlib.sha1(f'blob {len(data)}\0'.encode() + data).hexdigest()}
                    for name, data in files.items()]
            return httpx.Response(200, json={'tree': state['rows'] if state['rows'] is not None else rows,
                                            'truncated': state['truncated']})
        assert request.url.host == 'raw.githubusercontent.com'
        assert request.url.path.startswith(f'/someone/catalog/{state["commit"]}/catalog/models/')
        name = request.url.path.split('/catalog/models/', 1)[1]
        return httpx.Response(200, content=b'corrupt' if state['corrupt'] else files[name])

    client = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kw: client(transport=httpx.MockTransport(respond), **kw))
    def no_git(*args, **kwargs):
        raise FileNotFoundError('No developer tools on this computer')
    monkeypatch.setattr(channels, '_git', no_git)
    return state


def test_github_add_and_update_need_no_git_and_preserve_permission_gate(home, remote):
    added = channels.add('https://github.com/someone/catalog.git', name='lab')
    assert added['commit'] == remote['commit'] and added['entries'] == 1
    model = catalog.get('lab/dark-spots')
    assert model is not None
    assert readiness.model(model.key, {}).steps[0].kind == 'trust'
    folder = catalog.entry_folder(model)
    assert (folder / 'modules/helper.py').read_bytes() == remote['files']['dark-spots/modules/helper.py']
    before = {p.relative_to(folder).as_posix(): p.read_bytes() for p in folder.rglob('*') if p.is_file()}
    remote['commit'] = 'b' * 40
    channels.update('lab')
    assert folder.exists() and before == {p.relative_to(folder).as_posix(): p.read_bytes()
                                         for p in folder.rglob('*') if p.is_file()}
    assert not channels.trusted(catalog.get(model.key))


def test_bad_pinned_blob_leaves_channel_and_previous_snapshot_unchanged(home, remote):
    channels.add('https://github.com/someone/catalog', name='lab')
    before = channels.config().read_bytes()
    remote['commit'] = 'c' * 40
    remote['corrupt'] = True
    with pytest.raises(channels.CantDo, match='identity'):
        channels.update('lab')
    assert channels.config().read_bytes() == before
    assert not channels.snapshot_dir('lab', remote['commit']).exists()
    remote['corrupt'] = False
    assert channels.update('lab')['commit'] == remote['commit']


@pytest.mark.parametrize('bad', [
    {'path': 'catalog/models/../outside'},
    {'path': 'catalog/models/dark-spots//file'},
    {'path': 'catalog/models/dark-spots/./file'},
    {'path': 'catalog/models/dark-spots/back\\slash'},
    {'mode': '120000'},
    {'mode': '160000', 'type': 'commit'},
    {'size': -1}, {'size': True}, {'size': 8 * 1024 * 1024 + 1}, {'sha': 'not-an-identity'},
])
def test_unsafe_listing_is_refused_before_any_file_arrives(home, remote, bad):
    remote['rows'] = [{'path': 'catalog/models/dark-spots/model.toml', 'mode': '100644', 'type': 'blob',
                       'size': 10, 'sha': 'b' * 40, **bad}]
    with pytest.raises(channels.CantDo):
        channels.add('https://github.com/someone/catalog', name='lab', commit=remote['commit'])
    assert len(remote['requests']) == 1 and 'git/trees/' in remote['requests'][0]
    assert catalog.channels() == []
    assert not channels.snapshot_dir('lab', remote['commit']).exists()


@pytest.mark.parametrize('kind', ['duplicate', 'case', 'unicode', 'parent', 'count', 'total', 'truncated', 'space'])
def test_catalog_bounds_are_checked_before_content_requests(home, remote, monkeypatch, kind):
    from orchestrator import catalog_remote
    row = {'path': 'catalog/models/dark-spots/model.toml', 'mode': '100644', 'type': 'blob',
           'size': 1, 'sha': 'b' * 40}
    remote['rows'] = [row]
    if kind == 'duplicate': remote['rows'].append(dict(row))
    if kind == 'case': remote['rows'].append({**row, 'path': 'catalog/models/DARK-SPOTS/model.toml'})
    if kind == 'unicode': remote['rows'] += [{**row, 'path': 'catalog/models/dark-spots/caf\u00e9.txt'},
                                           {**row, 'path': 'catalog/models/dark-spots/cafe\u0301.txt'}]
    if kind == 'parent': remote['rows'].append({**row, 'path': 'catalog/models/dark-spots/model.toml/nested'})
    if kind == 'count': monkeypatch.setattr(catalog_remote, 'MAX_FILES', 0)
    if kind == 'total': monkeypatch.setattr(catalog_remote, 'TOTAL_BYTES', 0)
    if kind == 'truncated': remote['truncated'] = True
    if kind == 'space': monkeypatch.setattr(catalog_remote.shutil, 'disk_usage', lambda _: type('Disk', (), {'free': 0})())
    with pytest.raises(channels.CantDo):
        channels.add('https://github.com/someone/catalog', name='lab', commit=remote['commit'])
    assert len(remote['requests']) == 1
    assert catalog.channels() == []


@pytest.mark.parametrize('status', [302, 403, 404, 429, 500])
def test_provider_failure_is_actionable_and_does_not_fall_back_to_git(home, remote, status):
    remote['status'] = status
    with pytest.raises(channels.CantDo, match='retry'):
        channels.add('https://github.com/someone/catalog', name='lab')
    assert len(remote['requests']) == 1
    assert catalog.channels() == []
    remote['status'] = 200
    assert channels.add('https://github.com/someone/catalog', name='lab')['entries'] == 1


def test_pinned_get_never_resolves_head_and_automatic_peek_never_fetches_code(home, remote, monkeypatch):
    channels.add('https://github.com/someone/catalog', name='lab', commit=remote['commit'])
    assert not any('/commits/HEAD' in url for url in remote['requests'])
    remote['requests'].clear()
    monkeypatch.setenv('MICROMIND_REVIEWED_CHANNEL', 'https://github.com/someone/catalog')
    result = channels.peek()
    assert result['entries'] == 1
    assert not any('adapter.py' in url or 'env.lock' in url or 'helper.py' in url for url in remote['requests'])
