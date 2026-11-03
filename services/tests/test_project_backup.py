"""Back up a whole Project and restore the same Project on another Mac, or on this one after a wipe (ticket 08).

Real Project folders on two simulated Macs and a folder destination; no network or Git.
"""
import shutil

import pytest

from orchestrator.project_backup import BehindBackup, back_up, last_backup
from orchestrator.project_folder_copy import copy_snapshot
from orchestrator.project_remote_folder import folder_remote
from orchestrator.project_repository import ProjectRepository
from orchestrator.tools import ToolError
from tests.test_project_publish import seal
from tests.test_project_remote import project
from tests.test_project_remote_commands import NOTE, host, run, stores


def chats(root):
    return [e.get('text') for e in ProjectRepository(root).events() if e.get('type') == 'chat']


def test_a_backup_restores_the_same_project_with_its_conversations_on_another_mac(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    other, other_day, other_locations = host(tmp_path, 'Mac B')
    try:
        sid = day.new('Research')
        root = locations.project(sid)
        note = stores(app, locations).create(sid, NOTE)
        day._record({'type': 'chat', 'role': 'user', 'text': 'only for my own backup'}, strict=True)
        drive = {'kind': 'folder', 'path': str(tmp_path / 'Backup drive'), 'create': True}
        first = run(app, 'project-backup', session=sid, destination=drive)
        assert first['ok'] and first['backup']['commit'] and first['location'] == str(tmp_path / 'Backup drive')
        status = run(app, 'project-remote-status', session=sid)
        assert [b['commit'] for b in status['backups']] == [first['backup']['commit']]
        # A second backup chains onto the first.
        stores(app, locations).save(note.id, sid, {**NOTE, 'title': 'Revised question'}, expected_revision=1)
        second = run(app, 'project-backup', session=sid, destination={'kind': 'folder', 'path': drive['path']})
        assert second['backup']['commit'] != first['backup']['commit']
        assert run(app, 'project-remote-status', session=sid)['backups'][0]['commit'] == second['backup']['commit']
        # Mac B restores it: the same Project, history and conversations included.
        restored = run(other, 'project-restore', source={'kind': 'folder', 'path': drive['path']})['project']
        assert (restored['origin'], restored['session']) == (root.origin, sid)
        copy = other_locations.project(sid)
        assert copy.path.parent == other_locations.parent and copy.path != root.path
        assert 'only for my own backup' in chats(copy)
        assert stores(other, other_locations).read(note.id, sid).document['title'] == 'Revised question'
        assert ProjectRepository(copy).events()[-1]['type'] == 'project-restored-from-backup'
        # Mac B carries on and backs up; Mac A, now behind, is told to restore first and nothing is sent.
        other_day.open(sid, 'test')
        run(other, 'project-backup', session=sid, destination={'kind': 'folder', 'path': drive['path']})
        with pytest.raises(ToolError, match='newer version, sent from another Mac'):
            run(app, 'project-backup', session=sid, destination={'kind': 'folder', 'path': drive['path']})
    finally:
        locations.close()
        other_locations.close()


def test_after_a_wipe_the_backup_takes_the_place_of_the_lost_folder(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        root = locations.project(sid)
        note = stores(app, locations).create(sid, NOTE)
        drive = {'kind': 'folder', 'path': str(tmp_path / 'Backup drive'), 'create': True}
        run(app, 'project-backup', session=sid, destination=drive)
        # This Mac still has it: restoring would make a second copy of one Project, so it is refused.
        with pytest.raises(ToolError, match='already has this Project'):
            run(app, 'project-restore', source={'kind': 'folder', 'path': drive['path']})
        assert sorted(p.name for p in locations.parent.iterdir()) == [root.path.name]
        locations.close()
        shutil.rmtree(root.path)
        app, day, locations = host_again(app, tmp_path, 'Mac A')
        # A folder that is not there may be on an unplugged drive: the owner says to restore in its place.
        asked = run(app, 'project-restore', source={'kind': 'folder', 'path': drive['path']})
        assert asked['missing'] and 'not there now' in asked['error'] and str(root.path) in asked['error']
        assert not any(locations.parent.iterdir()) if locations.parent.exists() else True
        restored = run(app, 'project-restore', source={'kind': 'folder', 'path': drive['path']},
                       replaceMissing=True)['project']
        assert restored['origin'] == root.origin and restored['session'] == sid
        assert locations.registry.locate(root.origin).path == locations.project(sid).path
        assert stores(app, locations).read(note.id, sid).digest == note.digest
    finally:
        locations.close()


def host_again(app, tmp_path, name):
    """The same Mac after its Project folder was lost: its registry still names the old location."""
    from tests.test_project_locations import setup
    day, locations = setup(tmp_path / name)
    app.day = day
    return app, day, locations


def test_a_restore_nobody_can_register_leaves_no_folder_behind(tmp_path):
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        drive = {'kind': 'folder', 'path': str(tmp_path / 'Backup drive'), 'create': True}
        run(app, 'project-backup', session=sid, destination=drive)
        before = sorted(p.name for p in locations.parent.iterdir())
        with pytest.raises(ToolError):
            run(app, 'project-restore', source={'kind': 'folder', 'path': drive['path']})
        assert sorted(p.name for p in locations.parent.iterdir()) == before
    finally:
        locations.close()


def test_a_folder_backup_keeps_what_a_publication_would_never_send(tmp_path):
    root, *_ = project(tmp_path, text='token: sk-abcdefghijklmnopqrstu')
    remote = folder_remote(tmp_path / 'Drive', create=True)
    receipt = back_up(seal(root), remote, staging=tmp_path / 'Staging', scan_secrets=False)
    assert receipt['event']['type'] == 'project-backed-up' and not (tmp_path / 'Staging').exists()
    paths = {e['path'] for e in remote.records.read_commit(receipt['commit'])['entries']}
    assert any(p.startswith('project/history/sessions/') for p in paths)  # conversations travel in a backup
    # A destination outside the owner's own storage still refuses a key.
    with pytest.raises(Exception, match='secret|key|token|password'):
        back_up(seal(root), folder_remote(tmp_path / 'Elsewhere', create=True), staging=tmp_path / 'Staging 2')


def test_the_next_backup_chains_onto_the_last_one_or_the_restore(tmp_path):
    there = {'records': {'kind': 'folder', 'base': '/b'}, 'bulk': {'kind': 'folder', 'base': '/b'}}
    other = {'records': {'kind': 'folder', 'base': '/c'}, 'bulk': {'kind': 'folder', 'base': '/c'}}
    events = [{'type': 'project-backed-up', 'destination': there, 'commit': '1'},
              {'type': 'project-backed-up', 'destination': other, 'commit': '2'},
              {'type': 'project-restored-from-backup', 'destination': there, 'commit': '3'}]
    assert last_backup(events, there) == '3' and last_backup(events, other) == '2'
    assert last_backup(events, {'records': {}, 'bulk': {}}) is None
    # GitHub names repositories without regard to case.
    typed = {'records': {'kind': 'github', 'location': 'https://github.com/ada/research-backup', 'branch': 'main'}}
    made = {'records': {'kind': 'github', 'location': 'https://github.com/Ada/Research-Backup', 'branch': 'main'}}
    assert last_backup([{'type': 'project-backed-up', 'destination': made, 'commit': '4'}], typed) == '4'


def test_a_backup_behind_another_mac_sends_nothing(tmp_path):
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    first = back_up(seal(root), remote, staging=tmp_path / 'S1', scan_secrets=False)
    back_up(seal(root), remote, staging=tmp_path / 'S2', parent=first['commit'], scan_secrets=False)
    head = remote.records.head()
    with pytest.raises(BehindBackup, match='back up this Mac’s version to a new place'):
        back_up(seal(root), remote, staging=tmp_path / 'S3', parent=first['commit'], scan_secrets=False)
    assert remote.records.head() == head
    assert copy_snapshot  # the sealed snapshot is the unit (format v2 section 1)


def test_a_backup_never_goes_to_a_public_github_repository(tmp_path, monkeypatch):
    from orchestrator import project_remote_commands
    asked = []

    class Public:
        def ok(self, method, path, body=None, *, what='', allow=()):
            asked.append((method, path))
            return 200, {'private': False}
    monkeypatch.setattr(project_remote_commands, '_github', lambda anonymous=False: Public())
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        with pytest.raises(ToolError, match='public, and a backup holds your conversations'):
            run(app, 'project-backup', session=sid, destination={'kind': 'github', 'repo': 'ada/research'})
        assert asked == [('GET', '/repos/ada/research')]
        assert run(app, 'project-remote-status', session=sid)['backups'] == []
    finally:
        locations.close()


class PrivateGitHub:
    """The in-memory GitHub of test_project_remote_github, with private repositories: release files come only through
    the signed-in asset API, never the public download address."""

    def __new__(cls):
        from tests.test_project_remote_github import GitHub

        class Private(GitHub):
            def __init__(self):
                super().__init__()
                self.private = {}

            def call(self, method, path, payload=None):
                if method == 'POST' and path == '/user/repos':
                    self.private[f'ada/{payload["name"]}'] = payload['private']
                full = path.split('/', 2)[2] if path.count('/') == 3 else None
                if full in self.repos:
                    archived = self.__dict__.setdefault('archived', {})
                    if method == 'GET':
                        return 200, {'full_name': full, 'private': self.private.get(full, False),
                                     'archived': archived.get(full, False)}
                    if method == 'PATCH':
                        archived[full] = payload['archived']
                        return 200, {}
                    if method == 'DELETE':
                        del self.repos[full]
                        return 204, {}
                if method in ('POST', 'PATCH') and full is None and self.__dict__.get('archived', {}).get(
                        '/'.join(path.split('/')[2:4])):
                    return 403, {'message': 'Repository was archived so is read-only.'}
                return super().call(method, path, payload)

            def upload(self, url, body):
                status, data = super().upload(url, body)
                repo = self._repo(*url.split('/repos/')[1].split('/')[:2])
                asset = repo['releases']['hooke-data']['assets'][-1]
                asset['id'] = len(repo['releases']['hooke-data']['assets'])
                return status, {**data, 'id': asset['id']}

            def asset(self, repo, asset_id, limit):
                found = self._repo(*repo.split('/'))['releases']['hooke-data']['assets']
                return next(a for a in found if a['id'] == asset_id)['body'][:limit + 1]

            def download(self, url, limit):
                full = '/'.join(url.split('/')[3:5])
                if 'releases/download' in url and self.private.get(full):
                    raise AssertionError('a private release file was asked for at its public address')
                return super().download(url, limit)
        return Private()


def test_a_private_github_backup_restores_on_another_mac(tmp_path, monkeypatch):
    from orchestrator import project_remote_commands
    from orchestrator.github_rest import Client
    from tests.test_project_remote import PIXELS
    github = PrivateGitHub()
    monkeypatch.setattr(project_remote_commands, '_github', lambda anonymous=False: Client(http=github))
    app, day, locations = host(tmp_path, 'Mac A')
    other, _, other_locations = host(tmp_path, 'Mac B')
    try:
        sid = day.new('Research')
        root = locations.project(sid)
        (root.path / 'artifacts' / 'results').mkdir(parents=True, exist_ok=True)
        (root.path / 'artifacts' / 'results' / 'large.bin').write_bytes(PIXELS)  # over 1 MiB: a release file
        day._record({'type': 'chat', 'role': 'user', 'text': 'private notes'}, strict=True)
        sent = run(app, 'project-backup', session=sid, destination={'kind': 'github', 'create': True, 'name': 'research-backup'})
        assert github.private['ada/research-backup'] is True and sent['backup']['bulkObjects'] >= 1
        again = run(app, 'project-backup', session=sid, destination={'kind': 'github', 'repo': 'ada/research-backup'})
        assert again['backup']['uploadedBytes'] == 0  # its packs are reused
        restored = run(other, 'project-restore', source={'kind': 'github', 'repo': 'ada/research-backup'})['project']
        copy = other_locations.project(restored['session'])
        assert restored['origin'] == root.origin and (copy.path / 'artifacts/results/large.bin').read_bytes() == PIXELS
        assert 'private notes' in chats(copy)
    finally:
        locations.close()
        other_locations.close()


DAY = 24 * 3600


def age(path, days):
    """Make a stored file look as if it arrived `days` ago."""
    import os
    import time
    when = time.time() - days * DAY
    os.utime(path, (when, when))


def test_data_no_recent_version_needs_is_removed_after_30_days_and_nothing_sooner(tmp_path):
    import time
    from orchestrator.project_backup import prune
    from tests.test_project_remote import PIXELS
    root, *_ = project(tmp_path)
    remote = folder_remote(tmp_path / 'Drive', create=True)
    first = back_up(seal(root), remote, staging=tmp_path / 'S1', scan_secrets=False)
    old = {name for name, _, _ in remote.bulk.listing()}
    # The large file is replaced: the next version's pack holds only the new bytes.
    (root.path / 'artifacts' / 'results').mkdir(parents=True, exist_ok=True)
    (root.path / 'artifacts' / 'results' / 'new.bin').write_bytes(PIXELS[::-1])
    second = back_up(seal(root), remote, staging=tmp_path / 'S2', parent=first['commit'], scan_secrets=False)
    assert second['pruned'] == {'removed': 0, 'bytes': 0}  # everything is recent
    # Old objects are kept while any version of the last 30 days names them, or while they are recent themselves.
    for path in (tmp_path / 'Drive' / 'objects').glob('*/*'):
        age(path, 40)
    assert prune(remote, now=time.time())['removed'] == 0  # the head and its 30-day window name them all
    stray = tmp_path / 'Drive' / 'objects' / 'ab' / ('ab' + '0' * 62)
    stray.parent.mkdir(exist_ok=True)
    stray.write_bytes(b'left by an interrupted push')
    assert prune(remote, now=time.time())['removed'] == 0  # unnamed, but it arrived today
    age(stray, 31)
    assert prune(remote, now=time.time()) == {'removed': 1, 'bytes': len(b'left by an interrupted push')}
    assert not stray.exists() and old <= {name for name, _, _ in remote.bulk.listing()} | old
    from orchestrator.project_remote import fetch
    fetched, _ = fetch(remote, tmp_path / 'Check')  # the current version is whole
    assert (fetched.path / 'artifacts' / 'results' / 'new.bin').read_bytes() == PIXELS[::-1]


def test_a_deleted_backup_stays_restorable_for_30_days_can_be_kept_and_is_then_removed(tmp_path, monkeypatch):
    import time
    from orchestrator import project_backup
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        drive = {'kind': 'folder', 'path': str(tmp_path / 'Backup drive'), 'create': True}
        run(app, 'project-backup', session=sid, destination=drive)
        place = {'kind': 'folder', 'path': drive['path']}
        (tmp_path / 'Backup drive' / 'mine.txt').write_text('the owner keeps this here')
        marked = run(app, 'project-backup-delete', session=sid, destination=place)
        assert marked['purgeAfter'] > time.time() + 29 * DAY
        [entry] = run(app, 'project-remote-status', session=sid)['backups']
        assert entry['state'] == 'deleting'
        with pytest.raises(ToolError, match='being deleted'):
            run(app, 'project-backup', session=sid, destination=place)
        run(app, 'project-backup-keep', session=sid, destination=place)
        assert run(app, 'project-remote-status', session=sid)['backups'][0]['state'] == 'active'
        run(app, 'project-backup', session=sid, destination=place)
        run(app, 'project-backup-delete', session=sid, destination=place)
        # Within 30 days nothing is removed; after them, the next look removes Hooke's files and nothing else.
        assert (tmp_path / 'Backup drive' / 'HEAD').exists()
        later = time.time() + 31 * DAY
        monkeypatch.setattr(time, 'time', lambda: later)
        [entry] = run(app, 'project-remote-status', session=sid)['backups']
        assert entry['state'] == 'deleted'
        assert sorted(p.name for p in (tmp_path / 'Backup drive').iterdir()) == ['mine.txt']
        # A new backup there starts afresh (a new backup goes in an empty folder).
        monkeypatch.undo()
        (tmp_path / 'Backup drive' / 'mine.txt').rename(tmp_path / 'mine.txt')
        again = run(app, 'project-backup', session=sid, destination=drive)
        assert again['ok'] and run(app, 'project-remote-status', session=sid)['backups'][0]['state'] == 'active'
        folder_remote(tmp_path / 'Elsewhere', create=True)  # another Hooke remote, not this Project's backup
        with pytest.raises(ToolError, match='never backed up there'):
            run(app, 'project-backup-delete', session=sid,
                destination={'kind': 'folder', 'path': str(tmp_path / 'Elsewhere')})
        assert project_backup.GRACE == 30 * DAY
    finally:
        locations.close()


def test_a_deleted_github_backup_is_archived_then_its_repository_removed(tmp_path, monkeypatch):
    import time
    from orchestrator import project_remote_commands
    from orchestrator.github_rest import Client
    github = PrivateGitHub()
    monkeypatch.setattr(project_remote_commands, '_github', lambda anonymous=False: Client(http=github))
    app, day, locations = host(tmp_path, 'Mac A')
    try:
        sid = day.new('Research')
        run(app, 'project-backup', session=sid, destination={'kind': 'github', 'create': True, 'name': 'research-backup'})
        place = {'kind': 'github', 'repo': 'ada/research-backup'}
        run(app, 'project-backup-delete', session=sid, destination=place)
        assert github.archived['ada/research-backup'] is True
        with pytest.raises(ToolError, match='being deleted'):
            run(app, 'project-backup', session=sid, destination={'kind': 'github', 'repo': 'ada/research-backup'})
        later = time.time() + 31 * DAY
        monkeypatch.setattr(time, 'time', lambda: later)
        assert run(app, 'project-remote-status', session=sid)['backups'][0]['state'] == 'deleted'
        assert 'ada/research-backup' not in github.repos
    finally:
        locations.close()
