"""Hooke Cloud as a Project's bulk store (ADR 0028), against an in-memory stand-in for its object routes."""
import hashlib
import re

import pytest

from orchestrator.project_folder_copy import copy_snapshot
from orchestrator.project_remote import Remote, fetch, push_sealed
from orchestrator.project_remote_cloud import CloudBulk
from orchestrator.project_remote_folder import FolderRemote
from orchestrator.project_remote_layers import ChangedOutsideHooke, RemoteRefused
from orchestrator.project_repository import ProjectRepository
from tests.test_project_remote import PIXELS, project

BASE = 'https://app.hookeui.org'


class Cloud:
    """The routes of hooke-web's project_routes.py, kept in memory, with a byte cap and public reads."""

    def __init__(self, cap=10 << 20):
        self.objects, self.public, self.cap, self.calls = {}, False, cap, []

    def request(self, method, url, *, json=None, content=None, headers=None, auth=True, limit=None):
        self.calls.append((method, url.split('?')[0], auth))
        m = re.fullmatch(re.escape(BASE) + r'/api/projects/([0-9a-f]{32})/(objects:[a-z-]+)', url)
        if m:
            route = m.group(2)
            if route != 'objects:download-urls' and not auth:
                return 401, {'ok': False, 'error': 'Sign in'}
            if route == 'objects:missing':
                return 200, {'ok': True, 'missing': [d for d in json['digests'] if d not in self.objects]}
            if route == 'objects:upload-urls':
                out = []
                for o in json['objects']:
                    if o['sha256'] in self.objects:
                        out.append({**o, 'present': True})
                        continue
                    if sum(len(b) for b in self.objects.values()) + o['size'] > self.cap:
                        return 403, {'ok': False, 'error': 'This would go over your 10 GiB of <b>Hooke Cloud</b> storage.'}
                    out.append({**o, 'present': False, 'method': 'PUT', 'url': f'{BASE}/api/projects/x/objects/{o["sha256"]}?t=1',
                                'headers': {'Content-Length': str(o['size'])}})
                return 200, {'ok': True, 'objects': out}
            if route == 'objects:download-urls':
                if not auth and not self.public:
                    return 404, {'ok': False, 'error': 'Not found'}
                found = [{'sha256': d, 'size': len(self.objects[d]), 'method': 'GET',
                          'url': f'{BASE}/api/projects/x/objects/{d}?t=1'} for d in json['digests'] if d in self.objects]
                return 200, {'ok': True, 'objects': found, 'missing': [d for d in json['digests'] if d not in self.objects]}
        m = re.fullmatch(re.escape(BASE) + r'/api/projects/x/objects/([0-9a-f]{64})\?t=1', url)
        if m and method == 'PUT':
            if hashlib.sha256(content).hexdigest() != m.group(1):
                return 400, {'ok': False}
            self.objects[m.group(1)] = content
            return 200, {}
        if m and method == 'GET':
            return 200, self.objects[m.group(1)][:limit + 1]
        return 404, {}


def remote(tmp_path, cloud, origin, token='t'):
    return Remote(FolderRemote(tmp_path / 'Records', create=True), CloudBulk(BASE, origin, token=token, http=cloud))


def test_large_files_go_to_hooke_cloud_and_a_public_project_downloads_with_no_account(tmp_path):
    root, _, _, _, _, source = project(tmp_path)
    cloud = Cloud()
    receipt = push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, remote(tmp_path, cloud, root.origin),
                          parent=None, summary='Publish')
    assert len(cloud.objects) == 1 and receipt['uploadedBytes'] == len(PIXELS)  # both chunks in one pack
    cloud.public = True
    reader = Remote(FolderRemote(tmp_path / 'Records'), CloudBulk(BASE, root.origin, token=None, http=cloud))
    pushed = len(cloud.calls)
    fetched, _ = fetch(reader, tmp_path / 'Fetched')
    assert ProjectRepository(fetched).read(source.id, 1) == source
    assert [m for m, _, auth in cloud.calls[pushed:] if auth is not False] == []
    assert [m for m, _, _ in cloud.calls[pushed:]] == ['POST', 'GET']  # one pack: one link, one download


def test_a_second_push_uploads_nothing_already_kept(tmp_path):
    root, *_ = project(tmp_path)
    cloud = Cloud()
    first = push_sealed(copy_snapshot(root, tmp_path / 'One').path, remote(tmp_path, cloud, root.origin),
                        parent=None, summary='One')
    puts = [c for c in cloud.calls if c[0] == 'PUT']
    push_sealed(copy_snapshot(root, tmp_path / 'Two').path,
                Remote(FolderRemote(tmp_path / 'Records'), CloudBulk(BASE, root.origin, token='t', http=cloud)),
                parent=first['commit'], summary='Two')
    assert [c for c in cloud.calls if c[0] == 'PUT'] == puts


def test_the_cap_refusal_reaches_people_as_plain_words_and_nothing_is_published(tmp_path):
    root, *_ = project(tmp_path)
    cloud = Cloud(cap=100)
    target = remote(tmp_path, cloud, root.origin)
    with pytest.raises(RemoteRefused, match='go over your 10 GiB') as refused:
        push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, target, parent=None, summary='Publish')
    assert '<b>' not in str(refused.value) and target.records.head() is None


def test_a_private_project_is_not_readable_without_signing_in(tmp_path):
    root, *_ = project(tmp_path)
    cloud = Cloud()
    push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, remote(tmp_path, cloud, root.origin),
                parent=None, summary='Backup')
    reader = Remote(FolderRemote(tmp_path / 'Records'), CloudBulk(BASE, root.origin, token=None, http=cloud))
    with pytest.raises(RemoteRefused):
        fetch(reader, tmp_path / 'Fetched')


def test_bytes_that_do_not_match_their_name_are_refused(tmp_path):
    root, *_ = project(tmp_path)
    cloud = Cloud()
    push_sealed(copy_snapshot(root, tmp_path / 'Sealed').path, remote(tmp_path, cloud, root.origin),
                parent=None, summary='Backup')
    for digest in cloud.objects:
        cloud.objects[digest] = b'\0' * len(cloud.objects[digest])
    with pytest.raises(ChangedOutsideHooke):
        fetch(Remote(FolderRemote(tmp_path / 'Records'), CloudBulk(BASE, root.origin, token='t', http=cloud)),
              tmp_path / 'Fetched')


@pytest.mark.parametrize('base', ['http://app.hookeui.org', 'ftp://x', 'https://app.hookeui.org/api', 'nonsense'])
def test_only_an_https_service_address_is_accepted(base):
    with pytest.raises(RemoteRefused):
        CloudBulk(base, 'a' * 32, http=Cloud())
