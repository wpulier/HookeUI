"""Native bundle ownership through real journals, HTTP and restart; synthetic retained documents."""
import json
import io
import subprocess
import sys
import zipfile

import pytest
from fastapi.testclient import TestClient

from orchestrator.day.store import SessionStore
from orchestrator.paths import Paths
from orchestrator.scientific_projects import ProjectStore
from orchestrator.server import create_app

ORIGIN = 'http://127.0.0.1:8770'
HEADERS = {'Origin': ORIGIN, 'X-MicroscopeMind': 'import-bundle'}
SID = '2001-02-03'


def profile(root):
    paths = Paths.under(root)
    app = create_app('http://127.0.0.1:1', use_gpu=False, paths=paths)
    return paths, app.state.app, TestClient(app, base_url=ORIGIN)


def exported(root):
    paths = Paths.under(root)
    owner = SessionStore(paths.sessions, session=SID, clock=lambda: 981158400.0)
    owner.rename_session('Original specimen')
    store = ProjectStore(paths.results / 'projects')
    saved = store.create(SID, {'kind': 'scene', 'title': 'Retained native work', 'state': {},
                              'resources': [], 'runtimes': []})
    _, _, client = profile(root)
    response = client.get(f'/api/scientific-projects/{saved.id}/bundle', params={'session': SID})
    assert response.status_code == 200, response.text
    return response.content, saved


def test_fresh_profile_import_restores_original_owner_and_reexport_keeps_origin(tmp_path):
    body, saved = exported(tmp_path / 'source')
    paths, app, client = profile(tmp_path / 'destination')
    active = app.day.sid
    response = client.post('/api/scientific-bundles', content=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer['owner']['state'] == 'restored'
    assert answer['owner']['session'] == SID
    assert app.day.sid == active  # importing never switches or remaps ownership
    assert app.day.catalogue.get(SID).name == 'Original specimen'
    assert app.day.switch_session(SID)
    assert app.hub.resources.projects.read(saved.id, SID) == saved
    restarted = SessionStore.resume(paths.sessions)
    assert restarted.sid == SID and restarted.name == 'Original specimen'
    restarted.rename_session('Locally renamed')
    before_retry = restarted.journal.path.read_bytes()
    again = client.post('/api/scientific-bundles', content=body, headers=HEADERS).json()
    assert again['owner']['state'] == 'matched'
    assert again['owner']['originDigest'] == answer['owner']['originDigest']
    assert restarted.journal.path.read_bytes() == before_retry
    reexport = client.get(f'/api/scientific-projects/{saved.id}/bundle', params={'session': SID})
    assert reexport.status_code == 200, reexport.text
    _, _, third = profile(tmp_path / 'third')
    imported = third.post('/api/scientific-bundles', content=reexport.content, headers=HEADERS).json()
    assert imported['owner']['originDigest'] == answer['owner']['originDigest']


def test_equal_clock_and_session_ids_do_not_join_unrelated_projects(tmp_path):
    body, saved = exported(tmp_path / 'source')
    paths = Paths.under(tmp_path / 'destination')
    unrelated = SessionStore(paths.sessions, session=SID, clock=lambda: 981158400.0)
    unrelated.rename_session('Unrelated specimen')
    before = unrelated.journal.path.read_bytes()
    _, app, client = profile(tmp_path / 'destination')
    response = client.post('/api/scientific-bundles', content=body, headers=HEADERS)
    assert response.status_code == 400
    assert 'different or unverified Project' in response.json()['says']
    assert unrelated.journal.path.read_bytes() == before
    assert app.hub.resources.projects.list(SID) == []


def test_legacy_export_remains_data_only_without_mutating_or_adopting_owner(tmp_path):
    body, saved = exported(tmp_path / 'source')
    paths = Paths.under(tmp_path / 'source')
    journal = paths.sessions / SID / 'journal.jsonl'
    legacy = b''.join(line for line in journal.read_bytes().splitlines(keepends=True)
                      if json.loads(line)['type'] != 'session-origin')
    journal.write_bytes(legacy)
    _, _, client = profile(tmp_path / 'source')
    response = client.get(f'/api/scientific-projects/{saved.id}/bundle', params={'session': SID})
    assert response.status_code == 200
    assert journal.read_bytes() == legacy  # no migration on read or export
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        manifest = json.loads(archive.read('bundle.json'))
    assert manifest['format'] == 'hooke-provenance-bundle-v1' and 'owners' not in manifest
    paths, app, destination = profile(tmp_path / 'destination')
    local = SessionStore(paths.sessions, session=SID, clock=lambda: 981158400.0)
    before = local.journal.path.read_bytes()
    refused = destination.post('/api/scientific-bundles', content=response.content, headers=HEADERS)
    assert refused.status_code == 400 and 'owner' in refused.json()['says']
    assert local.journal.path.read_bytes() == before
    assert app.hub.resources.projects.list(SID) == []
    # Explicit offline legacy recovery remains supported outside the public SessionStore namespace.
    from orchestrator.scientific_exchange import reimport
    archive = tmp_path / 'legacy.zip'
    archive.write_bytes(response.content)
    offline = ProjectStore(tmp_path / 'offline-projects')
    target = reimport(offline, archive)
    assert target['id'] == saved.id and offline.read(saved.id, SID) == saved


def dependency_bundle(root):
    paths = Paths.under(root)
    first = SessionStore(paths.sessions, session=SID, clock=lambda: 981158400.0)
    second = SessionStore(paths.sessions, session='2001-02-04', clock=lambda: 981244800.0)
    first.rename_session('Input Project')
    second.rename_session('Output Project')
    store = ProjectStore(paths.results / 'projects')
    source = store.create(first.sid, {'kind': 'scene', 'title': 'Frozen input', 'state': {}, 'resources': [], 'runtimes': []})
    target = store.create(second.sid, {'kind': 'scientific-python-history', 'title': 'Producing history',
                                      'state': {'input': {'id': source.id, 'session': source.session,
                                                          'revision': source.revision, 'digest': source.digest}},
                                      'resources': [], 'runtimes': []})
    _, _, client = profile(root)
    response = client.get(f'/api/scientific-projects/{target.id}/bundle', params={'session': target.session})
    assert response.status_code == 200, response.text
    return response.content, source, target


def test_import_restores_all_dependency_owners_without_rewriting_records(tmp_path):
    body, source, target = dependency_bundle(tmp_path / 'source')
    _, app, client = profile(tmp_path / 'destination')
    response = client.post('/api/scientific-bundles', content=body, headers=HEADERS)
    assert response.status_code == 200, response.text
    assert app.day.catalogue.get(source.session).name == 'Input Project'
    assert app.day.catalogue.get(target.session).name == 'Output Project'
    for saved in (source, target):
        assert app.hub.resources.projects.read(saved.id, saved.session) == saved


def test_owner_aware_import_keeps_exact_native_dtype_axes_and_values(tmp_path):
    import numpy as np
    from orchestrator.scientific_exchange import export

    paths = Paths.under(tmp_path / 'source')
    SessionStore(paths.sessions, session=SID, clock=lambda: 981158400.0)
    store = ProjectStore(paths.results / 'projects')
    saved = store.create(SID, {'kind': 'scene', 'title': 'Native image', 'state': {}, 'resources': [], 'runtimes': []})
    data = np.array([[0, 256, 65535], [40001, 17, 2]], dtype='>u2')
    draft = store.array_draft(saved.id, SID, component='array.image', shape=data.shape, dtype=data.dtype.str,
                              axes=['y', 'x'], chunks=[2, 3], scientific={'semantic': 'intensity'}, max_written_bytes=data.nbytes)
    draft.write([0, 0], data)
    ref = draft.finish()
    saved = store.save(saved.id, SID, {**saved.document, 'resources': [ref]}, expected_revision=1)
    archive = tmp_path / 'native.zip'
    export(store, saved.id, SID, destination=archive, sessions=paths.sessions)
    _, app, client = profile(tmp_path / 'destination')
    result = client.post('/api/scientific-bundles', content=archive.read_bytes(), headers=HEADERS)
    assert result.status_code == 200, result.text
    imported = app.hub.resources.projects
    assert imported.read(saved.id, SID) == saved
    source = imported.array_source(saved.id, SID, saved.revision, ref)
    actual = source.read((slice(0, 2), slice(0, 3)))
    assert actual.dtype.str == '>u2'
    assert actual.tolist() == [[0, 256, 65535], [40001, 17, 2]]
    assert tuple(source.axes) == ('y', 'x')


@pytest.mark.parametrize('damage', ['missing-owner', 'extra-owner', 'changed-origin', 'owner-session', 'invalid-metadata'])
def test_incomplete_or_changed_owner_envelope_publishes_nothing(tmp_path, damage):
    body, source, target = dependency_bundle(tmp_path / 'source')
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(entries['bundle.json'])
    owner = manifest['owners'][source.session]
    if damage == 'missing-owner':
        del manifest['owners'][source.session]
    elif damage == 'extra-owner':
        manifest['owners']['2001-02-05'] = owner
    elif damage == 'changed-origin':
        owner['origin']['origin'] = '0' * 32
    elif damage == 'owner-session':
        owner['session'] = target.session
    else:
        owner['metadata']['localOnly'] = 'false'
    entries['bundle.json'] = json.dumps(manifest).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        for name, value in entries.items():
            archive.writestr(name, value)
    paths, app, client = profile(tmp_path / 'destination')
    response = client.post('/api/scientific-bundles', content=output.getvalue(), headers=HEADERS)
    assert response.status_code == 400, response.text
    for sid in (source.session, target.session):
        assert app.day.catalogue.get(sid) is None
        assert app.hub.resources.projects.list(sid) == []


def test_commit_cleanup_failure_never_rolls_back_committed_owner_or_documents(tmp_path, monkeypatch):
    body, saved = exported(tmp_path / 'source')
    paths, _, client = profile(tmp_path / 'destination')
    write = ProjectStore._write
    failed = False

    def fail_after_commit(path, content, **kwargs):
        nonlocal failed
        write(path, content, **kwargs)
        if (not failed and path == paths.results / 'projects' / '.bundle-import.json'
                and json.loads(content)['state'] == 'committed'):
            failed = True
            raise OSError('directory fsync failed after replace')

    monkeypatch.setattr(ProjectStore, '_write', staticmethod(fail_after_commit))
    response = client.post('/api/scientific-bundles', content=body, headers=HEADERS)
    assert response.status_code == 503
    _, restarted, retry = profile(tmp_path / 'destination')
    assert restarted.hub.resources.projects.read(saved.id, SID) == saved
    assert restarted.day.catalogue.get(SID).name == 'Original specimen'
    response = retry.post('/api/scientific-bundles', content=body, headers=HEADERS)
    assert response.json()['owner']['state'] == 'matched'


def test_import_publication_refuses_concurrent_reads_without_empty_catalogue_or_retention_deadlock(tmp_path, monkeypatch):
    import os
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from orchestrator.scientific_import_transaction import ImportPending
    from orchestrator.scientific_retention import locked, references, Uncertain
    from tests.waiting import HANG_S

    body, saved = exported(tmp_path / 'source')
    paths, app, client = profile(tmp_path / 'destination')
    reached, release = Event(), Event()
    rename = os.rename

    def held(source, target, **kwargs):
        rename(source, target, **kwargs)
        if target == paths.sessions / SID:
            reached.set()
            assert release.wait(HANG_S)

    monkeypatch.setattr(os, 'rename', held)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(client.post, '/api/scientific-bundles', content=body, headers=HEADERS)
        try:
            assert reached.wait(HANG_S)
            with pytest.raises(ImportPending):
                app.day.catalogue.list()
            with pytest.raises(ImportPending):
                app.hub.resources.projects.list(SID)
            assert client.get('/api/sessions').status_code == 503
            with locked(app.hub.resources.projects.runtime_root):
                with pytest.raises(Uncertain):
                    list(references(app.hub.resources.projects.runtime_root))
            assert app.day.sid != SID
        finally:
            release.set()
        assert pending.result(timeout=HANG_S).status_code == 200
    assert app.day.catalogue.get(SID).name == 'Original specimen'
    assert app.hub.resources.projects.read(saved.id, SID) == saved


def test_failed_extension_preserves_existing_heads_journals_and_retry(tmp_path, monkeypatch):
    body, saved = exported(tmp_path / 'source')
    paths, app, client = profile(tmp_path / 'destination')
    assert client.post('/api/scientific-bundles', content=body, headers=HEADERS).status_code == 200
    original_journal = (paths.sessions / SID / 'journal.jsonl').read_bytes()
    source = ProjectStore(Paths.under(tmp_path / 'source').results / 'projects')
    latest = source.save(saved.id, SID, {**saved.document, 'title': 'Later revision'}, expected_revision=1)
    _, _, source_client = profile(tmp_path / 'source')
    later = source_client.get(f'/api/scientific-projects/{saved.id}/bundle', params={'session': SID}).content
    write = ProjectStore._write
    failed = False

    def interrupt(path, content, **kwargs):
        nonlocal failed
        write(path, content, **kwargs)
        if not failed and path == paths.results / 'projects' / saved.id / 'head.json':
            failed = True
            raise OSError('accepted-head replacement interrupted')

    monkeypatch.setattr(ProjectStore, '_write', staticmethod(interrupt))
    assert client.post('/api/scientific-bundles', content=later, headers=HEADERS).status_code == 503
    assert app.hub.resources.projects.read(saved.id, SID) == saved
    assert (paths.sessions / SID / 'journal.jsonl').read_bytes() == original_journal
    assert client.post('/api/scientific-bundles', content=later, headers=HEADERS).status_code == 200
    assert app.hub.resources.projects.read(saved.id, SID) == latest


def test_failed_rollback_retains_its_staging_until_restart_can_finish(tmp_path, monkeypatch):
    import os

    body, saved = exported(tmp_path / 'source')
    paths, _, client = profile(tmp_path / 'destination')
    rename = os.rename

    def fail(source, target, **kwargs):
        if target.name.endswith('.withdrawn'):
            raise OSError('withdrawal temporarily unavailable')
        rename(source, target, **kwargs)
        if target == paths.sessions / SID:
            raise OSError('owner confirmation failed')

    monkeypatch.setattr(os, 'rename', fail)
    assert client.post('/api/scientific-bundles', content=body, headers=HEADERS).status_code == 503
    monkeypatch.setattr(os, 'rename', rename)
    _, restarted, retry = profile(tmp_path / 'destination')
    assert restarted.day.catalogue.get(SID) is None
    assert restarted.hub.resources.projects.list(SID) == []
    assert retry.post('/api/scientific-bundles', content=body, headers=HEADERS).status_code == 200
    assert restarted.hub.resources.projects.read(saved.id, SID) == saved


def test_user_new_project_refuses_busy_namespace_then_retries_with_next_identity(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from tests.waiting import HANG_S
    from orchestrator.scientific_import_transaction import ImportPending

    body, _ = exported(tmp_path / 'source')
    paths, app, client = profile(tmp_path / 'destination')
    import time
    app.day.clock = lambda: time.mktime((2001, 2, 3, 12, 0, 0, 0, 0, -1))
    reached, release, starting = Event(), Event(), Event()
    write = ProjectStore._write

    def held(path, content, **kwargs):
        write(path, content, **kwargs)
        if path == paths.results / 'projects' / '.bundle-import.json' and json.loads(content)['state'] == 'pending':
            reached.set()
            assert release.wait(HANG_S)

    def new_project():
        starting.set()
        return app.day.new('A separate Project')

    monkeypatch.setattr(ProjectStore, '_write', staticmethod(held))
    with ThreadPoolExecutor(max_workers=2) as pool:
        importing = pool.submit(client.post, '/api/scientific-bundles', content=body, headers=HEADERS)
        assert reached.wait(HANG_S)
        creating = pool.submit(new_project)
        try:
            assert starting.wait(HANG_S)
            with pytest.raises(ImportPending):
                creating.result(timeout=HANG_S)
        finally:
            release.set()
        assert importing.result(timeout=HANG_S).status_code == 200
        assert app.day.new('A separate Project') == '2001-02-03.2'
    assert app.day.catalogue.get(SID).name == 'Original specimen'


@pytest.mark.parametrize('point,committed', [('intent', False), ('document', False), ('owner', False),
                                           ('commit', True), ('owner-visible', True), ('response-lost', True), ('rollback', False)])
def test_interrupted_owner_import_is_invisible_and_recovers_on_real_app_restart(tmp_path, point, committed):
    body, saved = exported(tmp_path / 'source')
    archive = tmp_path / 'bundle.zip'
    archive.write_bytes(body)
    paths, app, client = profile(tmp_path / 'destination')
    # Process exit is the filesystem-failure seam: skip Python finally/rollback after the owner journal lands.
    script = '''
import os, sys
from pathlib import Path
from orchestrator.paths import Paths
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_exchange import reimport
p = Paths.under(Path(sys.argv[1]))
point = sys.argv[3]
write = ProjectStore._write
def interrupted_write(path, body, **kw):
    write(path, body, **kw)
    if path == p.results / 'projects' / '.bundle-import.json':
        import json
        if (point == 'intent' and json.loads(body)['state'] == 'pending') or (point == 'commit' and json.loads(body)['state'] == 'committed'): os._exit(73)
ProjectStore._write = staticmethod(interrupted_write)
rename = os.rename
def interrupted(source, target, **kw):
    rename(source, target, **kw)
    if point == 'owner' and Path(target).parent == p.sessions: os._exit(73)
    if point == 'rollback' and Path(target).parent == p.sessions: raise OSError('fail after owner publication')
    if point == 'rollback' and Path(target).name.endswith('.withdrawn'): os._exit(73)
    if point == 'document' and Path(target).parent == p.results / 'projects': os._exit(73)
os.rename = interrupted
unlink = Path.unlink
def interrupted_unlink(path, **kw):
    unlink(path, **kw)
    if point == 'owner-visible' and path == p.sessions / '.bundle-import.json': os._exit(73)
    if point == 'response-lost' and path == p.results / 'projects' / '.bundle-import.json': os._exit(73)
Path.unlink = interrupted_unlink
reimport(ProjectStore(p.results / 'projects'), sys.argv[2], sessions=p.sessions)
'''
    child = subprocess.run([sys.executable, '-c', script, str(tmp_path / 'destination'), str(archive), point], timeout=15)
    assert child.returncode == 73
    if point not in ('intent', 'owner-visible', 'response-lost'):
        with pytest.raises(ValueError, match='import'):
            app.day.catalogue.get(SID)
        unavailable = client.get('/api/sessions')
        assert unavailable.status_code == 503 and unavailable.json()['ok'] is False
        assert app.day.sid != SID
    if point != 'response-lost':
        with pytest.raises((ValueError, OSError), match='import'):
            app.hub.resources.projects.read(saved.id, SID)
    _, restarted, client = profile(tmp_path / 'destination')
    assert (restarted.day.catalogue.get(SID) is not None) == committed
    assert restarted.hub.resources.projects.list(SID) == ([saved] if committed else [])
    retry = client.post('/api/scientific-bundles', content=body, headers=HEADERS)
    assert retry.status_code == 200, retry.text
    assert retry.json()['owner']['state'] == ('matched' if committed else 'restored')
    assert restarted.hub.resources.projects.read(saved.id, SID) == saved


@pytest.mark.parametrize('cached', [False, True])
def test_session_input_counts_hold_visibility_through_cached_and_uncached_scan(tmp_path, monkeypatch, cached):
    from concurrent.futures import ThreadPoolExecutor
    from pathlib import Path
    from orchestrator.file_locks import fcntl
    from orchestrator.scientific_import_transaction import ImportPending, _visibility_lock
    from tests.waiting import HANG_S

    _, app, client = profile(tmp_path / 'profile')
    store = app.hub.resources.projects
    from tests.test_scientific_file_api import upload
    assert upload(client, app).status_code == 200
    store.__dict__.pop('_input_heads', None)
    if cached:
        assert store.inputs_by_session() == {app.day.sid: 1}
    iterdir = Path.iterdir
    admitted = []

    def publication_attempt():
        # A deterministic competing writer at the filesystem admission seam: it may not enter mid-count.
        try:
            with _visibility_lock(store.root, fcntl.LOCK_EX | fcntl.LOCK_NB):
                admitted.append(True)
        except ImportPending:
            admitted.append(False)

    def competing(path):
        if path == store.root:
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(publication_attempt).result(timeout=HANG_S)
        yield from iterdir(path)

    monkeypatch.setattr(Path, 'iterdir', competing)
    response = client.get('/api/sessions')
    assert response.status_code == 200, response.text
    row = next(row for row in response.json()['sessions'] if row['id'] == app.day.sid)
    assert row['counts']['sources'] == 1
    assert admitted == [False]


def test_session_input_counts_qualify_retryable_read_unavailability(tmp_path, monkeypatch):
    from orchestrator.scientific_import_transaction import ImportPending

    _, app, client = profile(tmp_path / 'profile')
    store = app.hub.resources.projects
    from tests.test_scientific_file_api import upload
    assert upload(client, app).status_code == 200
    store.__dict__.pop('_input_heads', None)

    def unavailable(*args, **kwargs):
        raise ImportPending('Bundle publication needs recovery')

    with monkeypatch.context() as pending:
        pending.setattr(store, 'read', unavailable)
        response = client.get('/api/sessions')
        assert response.status_code == 200, response.text
        row = next(row for row in response.json()['sessions'] if row['id'] == app.day.sid)
        assert row['counts']['sourcesIncomplete'] is True
        assert row['counts']['sources'] == 0  # unavailable, not a certified empty inventory
    recovered = client.get('/api/sessions')
    assert recovered.status_code == 200, recovered.text
    counts = next(row for row in recovered.json()['sessions'] if row['id'] == app.day.sid)['counts']
    assert counts['sources'] == 1
    assert 'sourcesIncomplete' not in counts


@pytest.mark.parametrize('operation', ['append', 'new', 'load', 'uncommitted'])
def test_import_does_not_block_loop_or_lose_admitted_journal_events(tmp_path, monkeypatch, operation):
    import asyncio
    asyncio.run(_import_with_live_journal(tmp_path, monkeypatch, operation))


async def _import_with_live_journal(tmp_path, monkeypatch, operation):
    import asyncio
    import os
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from queue import Queue
    from threading import Event
    from orchestrator.day.journal import Journal
    from orchestrator.file_locks import fcntl
    from orchestrator.scientific_import_transaction import ImportPending
    from tests.waiting import HANG_S, until_async

    body, _ = exported(tmp_path / 'source')
    paths, app, client = profile(tmp_path / 'destination')
    active, generation = app.day.sid, app.day.generation
    existing = app.day.journal.path
    pointer = app.day.user_prefs.path.read_bytes()
    reached, held, progress = Event(), Event(), Queue()
    rename, flock = os.rename, fcntl.flock
    loop_thread = threading.get_ident()
    dispositions = []

    def pause(source, target, **kwargs):
        rename(source, target, **kwargs)
        if target == paths.sessions / SID:
            held.set()
            reached.set()
            try:
                dispositions.append(progress.get(timeout=HANG_S))
            finally:
                held.clear()

    def observed_lock(fd, mode):
        # Wake the held importer on the old blocking syscall, so a red cannot hang the event loop/test.
        # The real syscall still runs; success requires the ordinary loop marker to release it instead.
        if threading.get_ident() == loop_thread and held.is_set() and mode == fcntl.LOCK_EX:
            progress.put('blocking-lock-request')
        return flock(fd, mode)

    monkeypatch.setattr(os, 'rename', pause)
    monkeypatch.setattr(fcntl, 'flock', observed_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        importing = pool.submit(client.post, '/api/scientific-bundles', content=body, headers=HEADERS)
        await until_async(reached.is_set)
        try:
            if operation == 'append':
                app.day.add_turn('user', 'chat', 'Durable while another Project imports')
                assert b'Durable while another Project imports' in existing.read_bytes()
            else:
                imported_before = (paths.sessions / SID / 'journal.jsonl').read_bytes()
                with pytest.raises(ImportPending):
                    if operation == 'new':
                        app.day.new('Must retry')
                    elif operation == 'load':
                        app.day.switch_session(SID)
                    else:
                        Journal(SID, paths.sessions).append({'type': 'must-not-escape-rollback'})
                assert (paths.sessions / SID / 'journal.jsonl').read_bytes() == imported_before
                assert (app.day.sid, app.day.generation) == (active, generation)
                assert app.day.user_prefs.path.read_bytes() == pointer
            await asyncio.sleep(0)  # an actual loop turn must occur while the importer still owns publication
            assert held.is_set()
            progress.put('loop-progress')
        finally:
            progress.put('test-cleanup')
        response = await asyncio.to_thread(importing.result, HANG_S)
    assert response.status_code == 200, response.text
    assert dispositions == ['loop-progress']
    assert not getattr(app.day, '_write_failed', False)


def test_transaction_contention_is_not_memory_only_journal_success(tmp_path, monkeypatch):
    from orchestrator.scientific_import_transaction import ImportPending
    _, app, _ = profile(tmp_path / 'profile')
    before = app.day.journal.path.read_bytes()
    turns = list(app.day.active.turns)

    def pending(event):
        raise ImportPending('Retry after owner publication')

    monkeypatch.setattr(app.day.journal, 'append', pending)
    with pytest.raises(ImportPending):
        app.day.add_turn('user', 'chat', 'Must not exist only in memory')
    assert app.day.journal.path.read_bytes() == before
    assert app.day.active.turns == turns


def test_replaced_admitted_journal_is_not_written_or_retained_only_in_memory(tmp_path):
    from orchestrator.scientific_import_transaction import ImportPending
    _, app, _ = profile(tmp_path / 'profile')
    path = app.day.journal.path
    before = path.read_bytes()
    replacement = path.with_suffix('.replacement')
    replacement.write_bytes(before)
    replacement.replace(path)
    turns = list(app.day.active.turns)
    with pytest.raises(ImportPending, match='journal changed'):
        app.day.add_turn('user', 'chat', 'Refuse a replaced owner journal')
    assert path.read_bytes() == before
    assert app.day.active.turns == turns


def test_deleted_admitted_journal_is_not_recreated_or_memory_only(tmp_path):
    from orchestrator.scientific_import_transaction import ImportPending
    _, app, _ = profile(tmp_path / 'profile')
    path = app.day.journal.path
    path.unlink()
    turns = list(app.day.active.turns)
    with pytest.raises(ImportPending):
        app.day.add_turn('user', 'chat', 'Refuse a missing owner journal')
    assert not path.exists()
    assert app.day.active.turns == turns


def test_post_open_journal_disappearance_refuses_without_memory_event_and_closes_fd(tmp_path, monkeypatch):
    import os
    from pathlib import Path
    from orchestrator.scientific_import_transaction import ImportPending

    _, app, _ = profile(tmp_path / 'profile')
    path = app.day.journal.path
    turns = list(app.day.active.turns)
    opened = []
    open_fd, stat = os.open, Path.stat

    def observed_open(name, flags, *args, **kwargs):
        fd = open_fd(name, flags, *args, **kwargs)
        if name == path and flags & os.O_APPEND:
            opened.append(fd)
        return fd

    def vanished(name, **kwargs):
        if name == path and opened:
            name.unlink()
        return stat(name, **kwargs)

    monkeypatch.setattr(os, 'open', observed_open)
    monkeypatch.setattr(Path, 'stat', vanished)
    with pytest.raises(ImportPending):
        app.day.add_turn('user', 'chat', 'Cannot retain this event only in memory')
    assert app.day.active.turns == turns
    assert len(opened) == 1
    with pytest.raises(OSError):
        os.fstat(opened[0])


@pytest.mark.parametrize('owner_exists', [False, True])
def test_public_legacy_import_cannot_adopt_an_unrelated_current_or_future_project(tmp_path, owner_exists):
    import time
    from orchestrator.scientific_exchange import export

    _, saved = exported(tmp_path / 'source')
    source = ProjectStore(Paths.under(tmp_path / 'source').results / 'projects')
    archive = tmp_path / 'data-only.zip'
    export(source, saved.id, SID, destination=archive)  # supported offline v1 export; no owner assertion
    paths, app, client = profile(tmp_path / 'destination')
    app.day.clock = lambda: time.mktime((2001, 2, 3, 12, 0, 0, 0, 0, -1))
    if owner_exists:
        local = SessionStore(paths.sessions, session=SID, clock=app.day.clock)
        before = local.journal.path.read_bytes()
    response = client.post('/api/scientific-bundles', content=archive.read_bytes(), headers=HEADERS)
    if not owner_exists:
        created = client.post('/api/session/new', headers={'Origin': ORIGIN, 'X-MicroscopeMind': 'new-session'}, json={'name': 'Unrelated later Project'})
        assert created.status_code == 200, created.text
        assert app.day.sid == SID
    else:
        assert local.journal.path.read_bytes() == before
    data = client.get('/api/resources', params={'day': SID})
    assert data.status_code == 200, data.text
    ids = [entry['id'] for entry in data.json()['entries']]
    assert saved.id not in ids, {'import': response.json(), 'data': data.json()}
    read = client.get(f'/api/scientific-projects/{saved.id}', params={'session': SID, 'revision': 1})
    assert read.status_code != 200, read.text
    assert response.status_code == 400, response.text
    assert 'owner' in response.json()['says'].lower()


@pytest.mark.parametrize('filing', ['turn', 'tracks'])
@pytest.mark.parametrize('tail', ['', '{\"type\":\"turn\"', '{\"type\":\"turn\"}'])
def test_existing_owner_reconstruction_can_file_durable_work_during_unrelated_creation(tmp_path, tail, filing):
    from concurrent.futures import ThreadPoolExecutor
    from orchestrator.day.ownership import namespace
    from tests.waiting import HANG_S

    store = SessionStore(tmp_path / 'sessions', session=SID)
    asked = store.here()
    with store.journal.path.open('a') as stream:
        stream.write(tail)
    store.new('Other specimen')
    # Creation owns the namespace on this thread; another worker must only take shared visibility
    # when reconstructing an already existing owner. No pending import/publication is being bypassed.
    with ThreadPoolExecutor(max_workers=1) as pool:
        with namespace(store.journal.root):
            if filing == 'turn':
                future = pool.submit(store.add_turn, 'user', 'chat', 'Filed to its original owner', asked=asked)
            else:
                future = pool.submit(store.record_tracks, 'm1', str(tmp_path / 'evidence'), asked.thread, session=SID)
            result = future.result(timeout=HANG_S)
    reopened = SessionStore(store.journal.root, session=SID)
    if filing == 'turn':
        assert reopened.active.turns[-1].text == result.text == 'Filed to its original owner'
    else:
        assert reopened.tracks['m1']['thread'] == asked.thread
        assert not store.tracks
    assert store.active.turns == []


@pytest.mark.parametrize('partial', ['', '{"type":"session-origin"}\n', '{"type":"thread-opened","thread":"t1","n":1,"at":1}'])
def test_incomplete_owner_creation_cannot_be_reconstructed_under_namespace(tmp_path, partial):
    from concurrent.futures import ThreadPoolExecutor
    from orchestrator.day.ownership import namespace
    from orchestrator.scientific_import_transaction import ImportPending
    from tests.waiting import HANG_S

    store = SessionStore(tmp_path / 'sessions', session=SID)
    pending = store.journal.root / '2001-02-03.2' / 'journal.jsonl'
    pending.parent.mkdir()
    pending.write_text(partial)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with namespace(store.journal.root):
            future = pool.submit(store.elsewhere, '2001-02-03.2')
            with pytest.raises(ImportPending):
                future.result(timeout=HANG_S)
    assert pending.read_text() == partial
    assert store.sid == SID and store.active.turns == []
