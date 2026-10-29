"""Export an accepted revision with everything that explains it, and reimport it into another profile.

A bundle carries exact bytes: each needed document's revision records from the first to the one needed, every
array and file component those revisions accept, and, recursively, the bases, producing histories and copy
sources their provenance names. No record is rewritten, so every digest the provenance names still matches after
reimport, on any operating system (names are lower-case hex with forward slashes). Reimport verifies every byte
before anything becomes visible, adds a document only when it is absent or its saved history is a prefix of the
bundle's, and never overwrites. Nothing in a bundle is executed.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import zipfile
import zlib

from . import scientific_arrays as arrays, scientific_files as files
from .bounded_files import read_at
from .scientific_projects import DIGEST, MAX_DOCUMENT, MAX_REVISIONS, PROJECT_ID, InvalidProject, ProjectStore, _json

FORMAT = 'hooke-provenance-bundle-v1'
OWNER_FORMAT = 'hooke-provenance-bundle-v2'
MAX_BUNDLE = 256 << 20
MAX_PROJECTS = 256
MAX_ENTRIES = 200_000
MAX_RECORD = MAX_DOCUMENT + 4096
NAME = re.compile(r'projects/(p[0-9a-f]{32})/(revisions|arrays|files)/([0-9a-f]{64})\.(json|bin)')


class BundleRefused(InvalidProject):
    pass


def _references(document):
    """Every other saved revision this document's provenance names: (id, session, revision)."""
    state, found = document.get('state') or {}, []
    def add(ref):
        if type(ref) is dict and type(ref.get('id')) is str and type(ref.get('session')) is str \
                and type(ref.get('revision')) is int:
            found.append((ref['id'], ref['session'], ref['revision']))
    membership = state.get('materialMembership')
    if document.get('kind') == 'project-material-membership' and type(membership) is dict:
        add(membership.get('reference'))
    edit = state.get('hookeEdit')
    for change in (edit.get('changes', []) if type(edit) is dict and edit.get('format') == 2 else [edit]):
        if type(change) is dict:
            add(change.get('base'))
            add((change.get('producer') or {}).get('history'))
    output = state.get('hookeOutput')
    if type(output) is dict:
        add(output.get('base'))
        add((output.get('producer') or {}).get('history'))
    population = state.get('populationSource')
    if type(population) is dict and type(population.get('nativeSource')) is dict:
        add(population['nativeSource'].get('nativeSource'))
    lineage = state.get('hookeCopy')
    if type(lineage) is dict:
        for step in (lineage.get('history', []) if lineage.get('format') == 2 and 'history' in lineage else [lineage]):
            add(step.get('source') if type(step) is dict else None)
    if document.get('kind') == 'scientific-python-history':
        add(state.get('input'))
    evidence = state.get('methodEvidence')
    if type(evidence) is dict:
        add(evidence.get('source'))
        for arm in evidence.get('arms', []):
            if type(arm) is dict:
                add(arm.get('mask'))
                for ref in [*arm.get('lineage', []), *arm.get('histories', [])]:
                    add(ref)
    return found


def export(store, project, session, revision=None, *, destination, check=lambda: None, sessions=None):
    """Write the bundle for one exact revision to `destination` (a new file). Returns the target reference."""
    target = store.read(project, session, revision=revision)
    needed, pending = {}, [(target.id, target.session, target.revision)]
    while pending:
        check()
        identity, owner, wanted = pending.pop()
        if identity in needed and needed[identity][1] >= wanted:
            continue
        if identity in needed and needed[identity][0] != owner:
            raise BundleRefused('Two records name one document under different sessions; nothing was exported')
        if len(needed) >= MAX_PROJECTS and identity not in needed:
            raise BundleRefused(f'This work depends on more than {MAX_PROJECTS} saved documents; nothing was exported')
        needed[identity] = (owner, wanted)
        for number in range(1, wanted + 1):
            pending.extend(_references(store.read(identity, owner, revision=number).document))
    owners = None
    if sessions is not None:
        from .day.ownership import snapshot
        try:
            owners = {owner: snapshot(sessions, owner) for owner in sorted({v[0] for v in needed.values()})}
        except (ValueError, OSError) as error:
            raise BundleRefused(str(error)) from error
        if any(value is None for value in owners.values()):
            owners = None  # legacy data export remains available; no owner is invented
    used, entries, written = 0, [], set()
    with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as bundle:
        def put(name, body):
            nonlocal used
            if name in written:  # content-addressed: revisions share unchanged chunks
                return
            written.add(name)
            used += len(body)
            if used > MAX_BUNDLE:
                raise BundleRefused(f'This work and what explains it exceed the {MAX_BUNDLE >> 20} MiB bundle allowance; '
                                    'nothing usable was exported')
            entries.append(name)
            bundle.writestr(name, body)
        projects = {}
        for identity, (owner, wanted) in sorted(needed.items()):
            folder = store._folder(identity)
            digests = store._head(folder, owner)[:wanted]
            projects[identity] = {'session': owner, 'revisions': digests}
            held = set()
            for number, digest in enumerate(digests, 1):
                check()
                body = store._read(folder / 'revisions' / f'{digest}.json', MAX_RECORD)
                if hashlib.sha256(body).hexdigest() != digest:
                    raise BundleRefused('A saved revision changed on disk; nothing usable was exported')
                put(f'projects/{identity}/revisions/{digest}.json', body)
                for ref in json.loads(body)['document']['resources']:
                    component = ref.get('component', '') if type(ref) is dict else ''
                    if ref.get('id') != identity or json.dumps(ref, sort_keys=True) in held:
                        continue
                    held.add(json.dumps(ref, sort_keys=True))
                    if component.startswith('array.'):
                        _put_array(store, identity, ref, put, check)
                    elif component.startswith('file.'):
                        _put_file(store, identity, owner, ref, put, check)
        manifest = {'format': FORMAT, 'target': {'id': target.id, 'session': target.session,
                    'revision': target.revision, 'digest': target.digest}, 'projects': projects}
        if owners is not None:
            manifest.update(format=OWNER_FORMAT, owners=owners)
        bundle.writestr('bundle.json', _json(manifest))
    return manifest['target']


def _put_array(store, project, ref, put, check):
    record = arrays._manifest(store, project, ref)
    with arrays._directory(store, project) as folder:
        put(f'projects/{project}/arrays/{ref["revision"]}.json', read_at(folder, ref['revision'] + '.json', arrays.MAX_MANIFEST))
        shape, dtype, _, chunks, grid = arrays._layout(record['shape'], record['dtype'], record['axes'], record['chunks'])
        for key, digest in sorted(record['parts'].items()):
            check()
            index = tuple(map(int, key.split(',')))
            size = math.prod(arrays._chunk_shape(index, shape, chunks)) * arrays.wire.DTYPES[dtype]
            body = read_at(folder, digest + '.bin', size)
            if len(body) != size or hashlib.sha256(body).hexdigest() != digest:
                raise BundleRefused('A saved array chunk changed on disk; nothing usable was exported')
            put(f'projects/{project}/arrays/{digest}.bin', body)


def _put_file(store, project, session, ref, put, check):
    meta = files._manifest(store, project, session, ref, check=check)
    with files._directory(store, project) as folder:
        put(f'projects/{project}/files/{ref["revision"]}.json', files._encode(meta))
        for index, key in enumerate(meta['chunks']):
            check()
            size = min(meta['chunkBytes'], meta['size'] - index * meta['chunkBytes'])
            put(f'projects/{project}/files/{key}.bin', files._read(folder, key + '.bin', size))


def reimport(store, source, *, check=lambda: None, sessions=None):
    """Verify a bundle file completely, then add what this profile lacks. Returns the target reference.

    Every revision is read through the store's own checks and every array and file reference is validated on a
    complete staged copy before anything is visible. A refusal leaves the profile exactly as it was.
    """
    projects, target, objects, owners = _unpacked(source, check)
    if sessions is not None and owners is None:
        raise BundleRefused('This legacy bundle has no verified Project owner; keep the archive for offline recovery')
    store.root.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.scientific-import-', dir=store.root))
    try:
        stage = ProjectStore(staging)
        for identity, (owner, digests) in sorted(projects.items()):
            check()
            _build(stage, identity, owner, digests, objects.get(identity, {}), check)
        _validated(stage, projects, target, check)
        plan = []
        for identity, (owner, digests) in sorted(projects.items()):
            existing = _existing(store, identity, owner)
            if existing is not None and existing[:len(digests)] != digests and digests[:len(existing)] != existing:
                raise BundleRefused('This profile holds a different document with the same identity; nothing was imported')
            plan.append((identity, owner, digests, existing))
        owner = _place_all(store, stage, plan, target, sessions=sessions, owners=owners)
    finally:
        from .scientific_import_transaction import INTENT
        pending = store.root / INTENT
        try:
            retained = pending.exists() and json.loads(store._read(pending, MAX_DOCUMENT)).get('stage') == staging.name
        except (InvalidProject, ValueError, AttributeError):
            retained = True  # unreadable recovery state must never cause its staged undo data to be discarded
        if not retained:
            shutil.rmtree(staging, ignore_errors=True)
    return target if sessions is None else {'target': target, 'owner': owner}


def _unpacked(source, check):
    """The bundle's description and its objects, each matching its name, with revisions parsed safely.

    A damaged archive (a bad central directory, corrupt deflate data, an encrypted or unsupported entry, a
    truncated file) is a refusal, never an unexpected failure.
    """
    if not zipfile.is_zipfile(source):
        raise BundleRefused('This is not a Hooke provenance bundle; nothing was imported')
    try:
        return _read_bundle(source, check)
    except (zipfile.BadZipFile, zlib.error, RuntimeError, NotImplementedError, EOFError) as error:
        raise BundleRefused('This bundle file is damaged or unreadable; nothing was imported') from error


def _read_bundle(source, check):
    with zipfile.ZipFile(source) as bundle:
        infos = bundle.infolist()
        if len(infos) > MAX_ENTRIES or sum(i.file_size for i in infos) > MAX_BUNDLE + MAX_RECORD:
            raise BundleRefused('This bundle exceeds its allowance; nothing was imported')
        names = {i.filename for i in infos}
        if len(names) != len(infos) or 'bundle.json' not in names \
                or any(n != 'bundle.json' and NAME.fullmatch(n) is None for n in names):
            raise BundleRefused('This is not a Hooke provenance bundle; nothing was imported')
        if bundle.getinfo('bundle.json').file_size > MAX_RECORD:
            raise BundleRefused('This bundle description exceeds its allowance; nothing was imported')
        try:
            manifest = json.loads(bundle.read('bundle.json'))
        except (ValueError, RecursionError) as error:
            raise BundleRefused('This bundle has no readable description; nothing was imported') from error
        projects, target = _described(manifest)
        owners = _owners(manifest, projects)
        objects = {}
        for name in sorted(names - {'bundle.json'}):
            check()
            identity, kind, digest, suffix = NAME.fullmatch(name).groups()
            if identity not in projects:
                raise BundleRefused('This bundle holds a document it does not describe; nothing was imported')
            body = bundle.read(name)
            # Revision records and manifests are named by their own hash; chunks by their content's hash.
            if hashlib.sha256(body).hexdigest() != digest:
                raise BundleRefused('A record in this bundle does not match its name; nothing was imported')
            objects.setdefault(identity, {})[(kind, f'{digest}.{suffix}')] = body
    for identity, (owner, digests) in projects.items():
        held = objects.get(identity, {})
        parent = None
        for number, digest in enumerate(digests, 1):
            body = held.get(('revisions', f'{digest}.json'))
            if body is None:
                raise BundleRefused('This bundle is missing a revision it names; nothing was imported')
            try:
                record = json.loads(body)
            except (ValueError, RecursionError) as error:
                raise BundleRefused('A revision in this bundle is unreadable; nothing was imported') from error
            if (type(record) is not dict or record.get('id') != identity or record.get('session') != owner
                    or record.get('revision') != number or record.get('parent') != parent):
                raise BundleRefused('A revision in this bundle is out of order; nothing was imported')
            parent = digest
    return projects, target, objects, owners


def _validated(stage, projects, target, check):
    """Every staged revision passes the store's read checks and has every array and file it accepts."""
    try:
        for identity, (owner, digests) in sorted(projects.items()):
            previous = None
            for number in range(1, len(digests) + 1):
                check()
                record = stage.read(identity, owner, revision=number)
                document = record.document
                arrays.validate_references(stage, identity, owner, document,
                                           accepted=arrays.accepted_parts(stage, identity, owner, previous))
                files.validate_references(stage, identity, owner, document, check=check)
                previous = document
        reached, pending = set(), [(target['id'], target['session'], target['revision'])]
        while pending:
            identity, owner, wanted = pending.pop()
            if (identity, wanted) in reached or identity not in projects:
                continue
            reached.add((identity, wanted))
            for number in range(1, wanted + 1):
                pending.extend(_references(stage.read(identity, owner, revision=number).document))
    except (InvalidProject, OSError, KeyError, TypeError, ValueError) as error:
        if isinstance(error, BundleRefused):
            raise
        raise BundleRefused(f'This bundle is incomplete or invalid ({error}); nothing was imported') from error
    unreached = set(projects) - {identity for identity, _ in reached}
    if unreached:
        raise BundleRefused('This bundle holds documents nothing in its revision refers to; nothing was imported')


def _described(manifest):
    if type(manifest) is not dict or manifest.get('format') not in (FORMAT, OWNER_FORMAT) or type(manifest.get('projects')) is not dict \
            or not 0 < len(manifest['projects']) <= MAX_PROJECTS or type(manifest.get('target')) is not dict:
        raise BundleRefused('This bundle has an unsupported description; nothing was imported')
    projects = {}
    for identity, entry in manifest['projects'].items():
        if (PROJECT_ID.fullmatch(identity) is None or type(entry) is not dict or type(entry.get('session')) is not str
                or type(entry.get('revisions')) is not list or not 0 < len(entry['revisions']) <= MAX_REVISIONS
                or any(type(d) is not str or DIGEST.fullmatch(d) is None for d in entry['revisions'])):
            raise BundleRefused('This bundle describes a document it cannot hold; nothing was imported')
        projects[identity] = (entry['session'], entry['revisions'])
    target = manifest['target']
    if (target.get('id') not in projects or projects[target['id']][0] != target.get('session')
            or type(target.get('revision')) is not int
            or not 0 < target['revision'] <= len(projects[target['id']][1])
            or projects[target['id']][1][target['revision'] - 1] != target.get('digest')):
        raise BundleRefused('This bundle names a revision it does not carry; nothing was imported')
    return projects, target


def _owners(manifest, projects):
    from .day.ownership import validate
    if manifest['format'] == FORMAT:
        if 'owners' in manifest:
            raise BundleRefused('Legacy bundles cannot claim restored Project ownership')
        return None
    owners = manifest.get('owners')
    if type(owners) is not dict or set(owners) != {owner for owner, _ in projects.values()}:
        raise BundleRefused('This bundle does not carry every original Project owner')
    try:
        return {sid: validate(value, sid) for sid, value in owners.items()}
    except ValueError as error:
        raise BundleRefused(str(error)) from error


def _existing(store, identity, owner):
    folder = store._folder(identity)
    if not folder.exists():
        return None
    try:
        return store._head(folder, owner)
    except InvalidProject as error:
        raise BundleRefused('This profile holds an unreadable document with the same identity; nothing was imported') from error


def _build(stage, identity, owner, digests, held, check):
    folder = stage.root / identity
    (folder / 'revisions').mkdir(parents=True, mode=0o700)
    for digest in digests:
        stage._write(folder / 'revisions' / f'{digest}.json', held[('revisions', f'{digest}.json')], immutable=True)
    stage._write(folder / 'head.json', _json({'format': 1, 'session': owner, 'revisions': digests}))
    for kind, opener in (('arrays', arrays._directory), ('files', files._directory)):
        bodies = [(name, body) for (where, name), body in held.items() if where == kind]
        if not bodies:
            continue
        with opener(stage, identity, create=True) as directory:
            for name, body in bodies:
                check()
                arrays._put(directory, body, '.' + name.rsplit('.', 1)[1])
    stage._sync_directory(folder)


def _missing(stage, store, identity):
    """Immutable staged objects the profile's copy of this document lacks or holds damaged (a damaged or partial copy).

    Every object is named by the hash of its bytes, so a present object whose bytes no longer match its name is
    damaged and is replaced like a missing one.
    """
    source, target = stage.root / identity, store._folder(identity)
    for kind in ('revisions', 'arrays', 'files'):
        if (source / kind).is_dir():
            for item in sorted((source / kind).iterdir()):
                if item.name.endswith(('.json', '.bin')) and not _intact(target / kind / item.name):
                    yield kind, item


def _intact(path):
    if path.is_symlink() or not path.is_file():
        return False
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest() == path.name.split('.', 1)[0]


def _replace(item, destination):
    """Put a verified staged object in place in one step, whether the profile lacks it or holds it damaged."""
    temporary = destination.with_name(f'.import-{os.getpid()}-{destination.name}')
    temporary.unlink(missing_ok=True)
    os.link(item, temporary)
    os.replace(temporary, destination)
    ProjectStore._sync_directory(destination.parent)


def _place_all(store, stage, plan, target, *, sessions=None, owners=None):
    """Lock the complete affected set before publishing; owner-aware imports also survive process exit."""
    from .scientific_retention import publication
    from .scientific_import_transaction import Import, internal, require_visible, publishing
    from .day import ownership
    placed, new_owners = [], []
    result = {'session': target['session'], 'state': 'unavailable',
              'reason': 'This data bundle has no verified original Project owner; export from an origin-aware Project'}
    restoring = sessions is not None and owners is not None
    owner_lock = ownership.namespace(sessions) if restoring else contextlib.nullcontext()
    with owner_lock, store._locked(store.root), contextlib.ExitStack() as held:
        require_visible(store.root)
        if restoring:
            require_visible(sessions)
        # Recheck under every affected lock BEFORE writing an undo intent. A concurrent accepted save is never undone.
        for identity, owner, digests, existing in plan:
            folder = store._folder(identity)
            held.enter_context(store._locked(stage.root / identity if existing is None else folder))
            if _existing(store, identity, owner) != existing or (existing is None and folder.exists()):
                raise BundleRefused('This profile changed during import; nothing was imported')
        if restoring:
            held.enter_context(publishing(store.root, sessions))
            for sid, envelope in sorted(owners.items()):
                folder = Path(sessions) / sid
                existing_owner = ownership.snapshot(sessions, sid)
                if folder.exists() and (existing_owner is None or existing_owner['originDigest'] != envelope['originDigest']):
                    raise BundleRefused('A different or unverified Project already uses this session ID; import into another profile')
                if existing_owner is None:
                    new_owners.append((folder, envelope))
            result = {'session': target['session'], 'state': 'restored' if any(f.name == target['session'] for f, _ in new_owners) else 'matched',
                      'originDigest': owners[target['session']]['originDigest']}
        for folder, envelope in new_owners:
            staged_owner = stage.root / 'owners' / folder.name
            staged_owner.mkdir(parents=True, mode=0o700)
            store._write(staged_owner / 'journal.jsonl', ownership.journal_bytes(envelope))
        if new_owners:
            store._sync_directory(stage.root / 'owners')
        transaction = Import(store, sessions, plan, new_owners, stage.root) if restoring else None
        with internal(store.root, sessions) if restoring else contextlib.nullcontext():
            try:
                if transaction:
                    transaction.begin()
                for identity, owner, digests, existing in plan:
                    folder = store._folder(identity)
                    head = _json({'format': 1, 'session': owner, 'revisions': digests})
                    with publication(store.root, store.runtime_root):
                        if existing is None:
                            os.rename(stage.root / identity, folder)
                            placed.append(('new', identity, owner, folder, None))
                            _confirm(store, identity, owner, digests)
                        else:
                            for kind, item in list(_missing(stage, store, identity)):
                                (folder / kind).mkdir(mode=0o700, exist_ok=True)
                                _replace(item, folder / kind / item.name)
                            if len(digests) > len(existing):
                                store._write(folder / 'head.json', head)
                                placed.append(('extended', identity, owner, folder, existing))
                            _confirm(store, identity, owner, digests if len(digests) > len(existing) else existing)
                accepted = store.read(target['id'], target['session'], revision=target['revision'])
                if accepted.digest != target['digest']:
                    raise BundleRefused('The imported revision does not match the bundle; nothing was imported')
                for folder, envelope in new_owners:
                    os.rename(stage.root / 'owners' / folder.name, folder)
                    if ownership.snapshot(sessions, folder.name) != envelope:
                        raise BundleRefused('Imported Project owner did not read back unchanged')
                store._sync_directory(store.root)
                if transaction:
                    store._sync_directory(Path(sessions))
                    transaction.commit()
            except BaseException:
                if transaction:
                    if transaction.value['state'] == 'pending':
                        transaction.rollback()
                    # A durable commit is never undone because removing a visibility barrier failed.
                else:
                    for kind, identity, owner, folder, existing in reversed(placed):
                        if kind == 'new':
                            os.rename(folder, stage.root / (identity + '.withdrawn'))
                        else:
                            store._write(folder / 'head.json', _json({'format': 1, 'session': owner, 'revisions': existing}))
                raise
    return result


def _confirm(store, identity, owner, digests):
    try:
        last = store.read(identity, owner)
    except InvalidProject as error:
        raise BundleRefused(f'An imported document could not be read back ({error}); nothing was imported') from error
    if last.revision != len(digests) or last.digest != digests[-1]:
        raise BundleRefused('An imported document did not read back as the bundle carries it; nothing was imported')
