"""H02 slice 5: export an accepted revision with what explains it; reimport it elsewhere with the association intact.

Actual retained storage, zip bundles and HTTP with synthetic workers; no napari kernel or browser qualification.
"""
import zipfile

import pytest
from fastapi.testclient import TestClient

from orchestrator import scientific_exchange as exchange
from orchestrator.paths import Paths
from orchestrator.scientific_projects import ProjectStore
from orchestrator.scientific_provenance import resolve
from orchestrator.server import create_app
from tests.test_scientific_edits import scene  # noqa: F401 (fixture)
from tests.test_scientific_provenance_resolve import three_producers
from tests.test_scientific_saves import setup


def produced(scene, tmp_path):
    store, saved, shown, data = setup(scene)
    histories, (second_copy, manual_copy) = three_producers(store, saved, shown, data)
    results = [(saved.id, store.read(saved.id, saved.session).revision), (second_copy['id'], 1), (manual_copy['id'], 1)]
    return store, saved, histories, results


def bundle(store, tmp_path, project, session, revision=None, name='bundle.zip'):
    path = tmp_path / name
    exchange.export(store, project, session, revision, destination=path)
    return path


def test_every_result_reimported_into_another_profile_resolves_exactly_as_it_did(scene, tmp_path):
    store, saved, histories, results = produced(scene, tmp_path)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    for project, revision in results:
        target = exchange.reimport(elsewhere, bundle(store, tmp_path, project, saved.session, revision, f'{project}.zip'))
        assert (target['id'], target['revision']) == (project, revision)
        assert resolve(elsewhere, project, saved.session, revision) == resolve(store, project, saved.session, revision)
        assert resolve(elsewhere, project, saved.session, revision)['problems'] == []
    producers = {resolve(elsewhere, p, saved.session, r)['edits'][0]['producer']['history']['id'] for p, r in results}
    assert producers == {h.record.id for h in histories}
    restarted = ProjectStore(elsewhere.root)
    assert all(resolve(restarted, p, saved.session, r)['problems'] == [] for p, r in results)


def test_a_bundle_carries_exact_bytes_under_portable_names(scene, tmp_path):
    store, saved, histories, results = produced(scene, tmp_path)
    path = bundle(store, tmp_path, results[2][0], saved.session, 1)
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    assert 'bundle.json' in names
    assert all(n == 'bundle.json' or exchange.NAME.fullmatch(n) for n in names)
    assert all('\\' not in n and '..' not in n and not n.startswith('/') for n in names)
    # The copy, its base document and the manual editor's history all travel; the other kernels' do not.
    carried = {n.split('/')[1] for n in names if n != 'bundle.json'}
    assert carried == {results[2][0], saved.id, histories[2].record.id}


def test_reimport_is_idempotent_and_extends_a_shorter_history_it_already_holds(scene, tmp_path):
    store, saved, _, _ = produced(scene, tmp_path)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    earlier = bundle(store, tmp_path, saved.id, saved.session, saved.revision, 'earlier.zip')
    exchange.reimport(elsewhere, earlier)
    exchange.reimport(elsewhere, earlier)
    assert elsewhere.read(saved.id, saved.session).revision == saved.revision
    later = bundle(store, tmp_path, saved.id, saved.session, None, 'later.zip')
    exchange.reimport(elsewhere, later)
    exchange.reimport(elsewhere, earlier)  # a shorter history than the one held changes nothing
    latest = store.read(saved.id, saved.session)
    assert elsewhere.read(saved.id, saved.session).digest == latest.digest
    assert resolve(elsewhere, saved.id, saved.session)['problems'] == []


def test_a_different_document_with_the_same_identity_is_never_overwritten(scene, tmp_path):
    store, saved, _, _ = produced(scene, tmp_path)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    exchange.reimport(elsewhere, bundle(store, tmp_path, saved.id, saved.session, saved.revision, 'base.zip'))
    held = elsewhere.read(saved.id, saved.session)
    elsewhere.save(saved.id, saved.session, {**held.document, 'title': 'Changed elsewhere'}, expected_revision=held.revision)
    diverged = elsewhere.read(saved.id, saved.session)
    with pytest.raises(exchange.BundleRefused, match='different document with the same identity'):
        exchange.reimport(elsewhere, bundle(store, tmp_path, saved.id, saved.session, None, 'later.zip'))
    assert elsewhere.read(saved.id, saved.session) == diverged
    assert not any(p.name.startswith('.scientific-import-') for p in elsewhere.root.iterdir())


@pytest.mark.parametrize('damage', ['changed-byte', 'foreign-name', 'missing-revision', 'wrong-target'])
def test_a_damaged_or_foreign_bundle_imports_nothing(scene, tmp_path, damage):
    import json
    store, saved, _, results = produced(scene, tmp_path)
    good = bundle(store, tmp_path, results[1][0], saved.session, 1)
    bad = tmp_path / 'bad.zip'
    with zipfile.ZipFile(good) as source, zipfile.ZipFile(bad, 'w') as target:
        names = source.namelist()
        revision = next(n for n in names if '/revisions/' in n)
        for name in names:
            body = source.read(name)
            if damage == 'changed-byte' and name == revision:
                body = body.replace(b'"format":1', b'"format":2', 1)
            if damage == 'missing-revision' and name == revision:
                continue
            if damage == 'wrong-target' and name == 'bundle.json':
                manifest = json.loads(body)
                manifest['target']['digest'] = '0' * 64
                body = json.dumps(manifest).encode()
            target.writestr(name, body)
        if damage == 'foreign-name':
            target.writestr('../outside.txt', b'nope')
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    elsewhere.root.mkdir()
    with pytest.raises(exchange.BundleRefused, match='nothing was imported'):
        exchange.reimport(elsewhere, bad)
    assert list(elsewhere.root.iterdir()) == []
    assert not (tmp_path / 'outside.txt').exists()


def test_work_beyond_the_bundle_allowance_is_refused_and_says_so(scene, tmp_path, monkeypatch):
    store, saved, _, results = produced(scene, tmp_path)
    monkeypatch.setattr(exchange, 'MAX_BUNDLE', 64)
    with pytest.raises(exchange.BundleRefused, match='bundle allowance; nothing usable was exported'):
        bundle(store, tmp_path, results[0][0], saved.session)


@pytest.fixture
def dated(tmp_path):
    """The edits fixture under a real session id, which the HTTP routes require."""
    import itertools
    import numpy as np
    store = ProjectStore(tmp_path / 'dated')
    original = store.create('2026-10-21', {'kind': 'scene', 'title': 'Editable labels', 'state': {}, 'resources': [], 'runtimes': []})
    data = np.arange(4 * 6, dtype='>u2').reshape(4, 6)
    writer = store.array_draft(original.id, '2026-10-21', component='array.labels', shape=data.shape, dtype=data.dtype.str,
                               axes=['y', 'x'], chunks=[2, 3], scientific={'semantic': 'instance-labels'}, max_written_bytes=data.nbytes)
    for row, col in itertools.product(range(2), repeat=2):
        writer.write([row, col], data[row * 2:row * 2 + 2, col * 3:col * 3 + 3])
    reference = writer.finish()
    accepted = store.save(original.id, '2026-10-21', {**original.document, 'resources': [reference]}, expected_revision=1)
    return store, accepted, reference, data


def test_export_and_import_through_http_between_two_profiles(dated, tmp_path):
    origin = 'http://127.0.0.1:8770'
    store, saved, _, results = produced(dated, tmp_path)
    from orchestrator.day.store import SessionStore
    owners = tmp_path / 'source-sessions'
    SessionStore(owners, session=saved.session)
    component = tmp_path / 'owned-bundle.zip'
    exchange.export(store, results[2][0], saved.session, 1, destination=component, sessions=owners)
    first = TestClient(create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path / 'one')), base_url=origin)
    headers = {'Origin': origin, 'X-MicroscopeMind': 'import-bundle'}
    assert first.post('/api/scientific-bundles', content=component.read_bytes(), headers={'Origin': origin}).status_code == 403
    imported = first.post('/api/scientific-bundles', content=component.read_bytes(), headers=headers)
    assert imported.status_code == 200, imported.text
    assert imported.json()['target'] == {'id': results[2][0], 'session': saved.session, 'revision': 1,
                                         'digest': store.read(results[2][0], saved.session).digest}
    exported = first.get(f'/api/scientific-projects/{results[2][0]}/bundle', params={'session': saved.session})
    assert exported.status_code == 200 and exported.headers['content-type'] == 'application/zip'
    second = TestClient(create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path / 'two')), base_url=origin)
    again = second.post('/api/scientific-bundles', content=exported.content, headers=headers)
    assert again.status_code == 200, again.text
    answer = second.get(f'/api/scientific-projects/{results[2][0]}/provenance', params={'session': saved.session}).json()
    assert answer['provenance']['problems'] == [] and answer['provenance']['copy']['conflictCopy']
    assert answer['provenance']['edits'][0]['producer']['actions'][0]['request']['tool'] == 'paint'
    junk = second.post('/api/scientific-bundles', content=b'not a zip', headers=headers)
    assert junk.status_code == 400 and 'not a Hooke provenance bundle' in junk.json()['says']
    assert second.get(f'/api/scientific-projects/{results[2][0]}/bundle', params={'session': '2020-01-01'}).status_code == 400


def rewritten(good, bad, change):
    """A copy of a bundle with `change(entries)` applied to its {name: bytes} entries."""
    import json as _json
    with zipfile.ZipFile(good) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    manifest = _json.loads(entries['bundle.json'])
    change(entries, manifest)
    entries['bundle.json'] = _json.dumps(manifest).encode()
    with zipfile.ZipFile(bad, 'w') as target:
        for name, body in entries.items():
            target.writestr(name, body)
    return bad


def unchanged(store):
    """No document and no staging folder; lock files the store creates while locking are not content."""
    return not store.root.exists() or [p.name for p in store.root.iterdir() if not p.name.endswith('.lock')] == []


def entry(entries, kind, suffix):
    return next(n for n in entries if f'/{kind}/' in n and n.endswith(suffix))


@pytest.mark.parametrize('damage', ['missing-chunk', 'missing-manifest', 'non-json-revision', 'invalid-extra-document',
                                    'unreferenced-extra-document'])
def test_an_incomplete_or_invalid_bundle_imports_nothing_and_leaves_no_files(scene, tmp_path, damage):
    import hashlib, json as _json
    store, saved, _, results = produced(scene, tmp_path)
    good = bundle(store, tmp_path, results[2][0], saved.session, 1)
    other = bundle(store, tmp_path, results[1][0], saved.session, 1, 'other.zip')

    def change(entries, manifest):
        if damage == 'missing-chunk':
            del entries[entry(entries, 'arrays', '.bin')]
        elif damage == 'missing-manifest':
            del entries[entry(entries, 'arrays', '.json')]
        elif damage == 'non-json-revision':
            name = entry(entries, 'revisions', '.json')
            identity = name.split('/')[1]
            body = b'not json at all'
            digest = hashlib.sha256(body).hexdigest()
            del entries[name]
            entries[f'projects/{identity}/revisions/{digest}.json'] = body
            manifest['projects'][identity]['revisions'] = [digest if d == name.split('/')[-1][:-5] else d
                                                          for d in manifest['projects'][identity]['revisions']]
        else:
            with zipfile.ZipFile(other) as extra:
                copied = _json.loads(extra.read('bundle.json'))['projects']
                identity = results[1][0]
                for name in extra.namelist():
                    if name.startswith(f'projects/{identity}/'):
                        entries[name] = extra.read(name)
            manifest['projects'][identity] = copied[identity]
            if damage == 'invalid-extra-document':
                name = entry({n: b for n, b in entries.items() if n.startswith(f'projects/{identity}/')}, 'revisions', '.json')
                record = _json.loads(entries[name])
                record['document']['unknownKey'] = 1
                body = _json.dumps(record).encode()
                digest = hashlib.sha256(body).hexdigest()
                del entries[name]
                entries[f'projects/{identity}/revisions/{digest}.json'] = body
                manifest['projects'][identity]['revisions'] = [digest]
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    with pytest.raises(exchange.BundleRefused, match='nothing was imported'):
        exchange.reimport(elsewhere, rewritten(good, tmp_path / 'bad.zip', change))
    assert unchanged(elsewhere)


def test_a_damaged_copy_this_profile_holds_is_repaired_by_a_good_bundle(scene, tmp_path):
    store, saved, _, results = produced(scene, tmp_path)
    good = bundle(store, tmp_path, results[2][0], saved.session, 1)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    exchange.reimport(elsewhere, good)
    chunk = next((elsewhere.root / results[2][0] / 'arrays').glob('*.bin'))
    chunk.chmod(0o600)
    chunk.unlink()
    from orchestrator import scientific_arrays
    copied = elsewhere.read(results[2][0], saved.session, revision=1)
    with pytest.raises(Exception):
        scientific_arrays.validate_references(elsewhere, copied.id, copied.session, copied.document)
    exchange.reimport(elsewhere, good)
    assert chunk.exists()
    scientific_arrays.validate_references(elsewhere, copied.id, copied.session, copied.document)
    assert resolve(elsewhere, results[2][0], saved.session, 1) == resolve(store, results[2][0], saved.session, 1)


def test_a_save_racing_an_import_cannot_overwrite_the_imported_revision(scene, tmp_path, monkeypatch):
    from orchestrator.scientific_projects import Conflict
    store, saved, _, _ = produced(scene, tmp_path)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    exchange.reimport(elsewhere, bundle(store, tmp_path, saved.id, saved.session, saved.revision, 'base.zip'))
    held = elsewhere.read(saved.id, saved.session)
    racing, outcome = [], {}
    original = exchange._missing

    def missing(stage, profile, identity):
        if identity == saved.id and not racing:
            import threading, time
            def save():
                try:
                    outcome['saved'] = elsewhere.save(saved.id, saved.session, {**held.document, 'title': 'Racing'},
                                                      expected_revision=held.revision)
                except Conflict as error:
                    outcome['refused'] = error
            racing.append(threading.Thread(target=save))
            racing[0].start()
            time.sleep(0.3)  # the save would land now if the import did not hold the document's lock
        return original(stage, profile, identity)
    monkeypatch.setattr(exchange, '_missing', missing)
    exchange.reimport(elsewhere, bundle(store, tmp_path, saved.id, saved.session, None, 'later.zip'))
    racing[0].join(10)
    assert 'refused' in outcome and 'saved' not in outcome
    assert elsewhere.read(saved.id, saved.session).digest == store.read(saved.id, saved.session).digest


def test_a_failure_while_placing_takes_back_everything_this_import_placed(scene, tmp_path, monkeypatch):
    store, saved, _, results = produced(scene, tmp_path)
    good = bundle(store, tmp_path, results[2][0], saved.session, 1)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    calls = []
    original = exchange._confirm

    def confirm(*args):
        calls.append(args[1])
        if len(calls) == 2:
            raise exchange.BundleRefused('simulated failure; nothing was imported')
        return original(*args)
    monkeypatch.setattr(exchange, '_confirm', confirm)
    with pytest.raises(exchange.BundleRefused, match='simulated'):
        exchange.reimport(elsewhere, good)
    assert len(calls) == 2
    assert unchanged(elsewhere)


@pytest.mark.parametrize('copy', ['new', 'extended'])
def test_a_save_landing_between_two_placements_is_never_lost(scene, tmp_path, monkeypatch, copy):
    """Review round 2: a save onto a document this import already placed (a new copy, or an extended history the
    profile held) waits for the import; if the import then fails and takes that document back, the save is
    refused, never accepted and then lost."""
    import threading, time
    store, saved, _, results = produced(scene, tmp_path)
    good = bundle(store, tmp_path, results[2][0], saved.session, 1)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    if copy == 'extended':  # the profile already holds the document's first revision; this import extends it
        exchange.reimport(elsewhere, bundle(store, tmp_path, saved.id, saved.session, 1, 'older.zip'))
        assert elsewhere.read(saved.id, saved.session).revision == 1 < store.read(saved.id, saved.session).revision
    before = {p.relative_to(elsewhere.root): p.read_bytes() for p in elsewhere.root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
    racing, outcome, main = [], {}, threading.get_ident()
    confirm, read = exchange._confirm, elsewhere.read

    def placed(*args):
        confirm(*args)
        if args[1] == saved.id and not racing:  # the document is visible now; a save on it starts
            current = read(saved.id, saved.session)

            def save():
                try:
                    outcome['saved'] = elsewhere.save(saved.id, saved.session, {**current.document, 'title': 'Racing'},
                                                      expected_revision=current.revision)
                except Exception as error:  # noqa: BLE001 - any refusal; the point is that nothing is accepted and lost
                    outcome['refused'] = error
            racing.append(threading.Thread(target=save))
            racing[0].start()

    def failing(*args, **kwargs):  # the import's last read-back (of the target revision), after every placement and outside any publication
        if racing and threading.get_ident() == main and 'revision' in kwargs and 'failed' not in outcome:
            outcome['failed'] = True
            time.sleep(0.5)  # without the held folder locks the save lands now
            assert 'saved' not in outcome and 'refused' not in outcome  # it is still waiting for the import
            raise exchange.BundleRefused('simulated failure after the placements; nothing was imported')
        return read(*args, **kwargs)
    monkeypatch.setattr(exchange, '_confirm', placed)
    monkeypatch.setattr(elsewhere, 'read', failing)
    with pytest.raises(exchange.BundleRefused, match='simulated'):
        exchange.reimport(elsewhere, good)
    racing[0].join(10)
    assert outcome['failed'] and not racing[0].is_alive() and 'refused' in outcome and 'saved' not in outcome
    after = {p.relative_to(elsewhere.root): p.read_bytes() for p in elsewhere.root.rglob('*') if p.is_file() and not p.name.endswith('.lock')}
    assert read(saved.id, saved.session).revision == 1 if copy == 'extended' else not (elsewhere.root / saved.id).exists()
    assert {k: v for k, v in after.items() if k.name == 'head.json'} == {k: v for k, v in before.items() if k.name == 'head.json'}


@pytest.mark.parametrize('damage', ['zeroed-chunk', 'corrupt-revision'])
def test_a_present_but_damaged_object_is_replaced_by_a_good_bundle(scene, tmp_path, damage):
    """Review round 2: an object whose bytes no longer match its name is damaged, and is replaced atomically."""
    store, saved, _, results = produced(scene, tmp_path)
    good = bundle(store, tmp_path, results[2][0], saved.session, 1)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    exchange.reimport(elsewhere, good)
    folder = elsewhere.root / results[2][0]
    damaged = next((folder / 'arrays').glob('*.bin')) if damage == 'zeroed-chunk' else next((folder / 'revisions').glob('*.json'))
    intact = damaged.read_bytes()
    damaged.chmod(0o600)
    damaged.write_bytes(bytes(len(intact)) if damage == 'zeroed-chunk' else intact[:-2] + b'}}')
    assert damaged.read_bytes() != intact
    exchange.reimport(elsewhere, good)
    assert damaged.read_bytes() == intact and not any(p.name.startswith('.import-') for p in damaged.parent.iterdir())
    from orchestrator import scientific_arrays
    copied = elsewhere.read(results[2][0], saved.session, revision=1)
    scientific_arrays.validate_references(elsewhere, copied.id, copied.session, copied.document)
    assert resolve(elsewhere, results[2][0], saved.session, 1) == resolve(store, results[2][0], saved.session, 1)


def damaged_archive(good, path, damage):
    """The same bundle written deflated, then broken the way a real file breaks."""
    with zipfile.ZipFile(good) as source:
        entries = {name: source.read(name) for name in source.namelist()}
    with zipfile.ZipFile(path, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        for name, body in entries.items():
            target.writestr(name, body)
    raw = bytearray(path.read_bytes())
    with zipfile.ZipFile(path) as archive:
        first = max(archive.infolist(), key=lambda info: info.compress_size)
    start = first.header_offset + 30 + len(first.filename.encode()) + len(first.extra)
    if damage == 'corrupt-deflate':  # an invalid deflate block type: zlib.error while reading
        raw[start:start + 8] = b'\xff' * 8
    elif damage == 'encrypted':  # the central directory says the entry is encrypted: RuntimeError while reading
        central = raw.index(b'PK\x01\x02')
        raw[central + 8] |= 1
    else:  # truncated in the middle of the entries
        raw = raw[:start + 16]
    path.write_bytes(bytes(raw))
    return path


@pytest.mark.parametrize('damage', ['corrupt-deflate', 'encrypted', 'truncated'])
def test_a_damaged_archive_is_refused_and_imports_nothing(scene, tmp_path, damage):
    store, saved, _, results = produced(scene, tmp_path)
    broken = damaged_archive(bundle(store, tmp_path, results[2][0], saved.session, 1), tmp_path / 'broken.zip', damage)
    elsewhere = ProjectStore(tmp_path / 'elsewhere')
    with pytest.raises(exchange.BundleRefused, match='nothing was imported'):
        exchange.reimport(elsewhere, broken)
    assert unchanged(elsewhere)


def test_a_corrupt_archive_posted_over_http_is_a_refusal_not_a_server_error(dated, tmp_path):
    origin = 'http://127.0.0.1:8770'
    store, saved, _, results = produced(dated, tmp_path)
    broken = damaged_archive(bundle(store, tmp_path, results[2][0], saved.session, 1), tmp_path / 'broken.zip', 'corrupt-deflate')
    client = TestClient(create_app('http://127.0.0.1:1', use_gpu=False, paths=Paths.under(tmp_path / 'one')), base_url=origin)
    answer = client.post('/api/scientific-bundles', content=broken.read_bytes(),
                         headers={'Origin': origin, 'X-MicroscopeMind': 'import-bundle'})
    assert answer.status_code == 400 and 'damaged or unreadable' in answer.json()['says'], answer.text
