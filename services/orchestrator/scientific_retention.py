"""Saved scientific histories veto destructive runtime operations across registered profiles."""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from .file_locks import fcntl  # the standard module, or Windows' refusing stand-in
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import stat
import sys
import time

BINDINGS = '.runtime-roots.json'
METADATA = '.scientific-retention'
CARRIED = 'carried-over'  # records a copied or moved profile brought along, set aside with a receipt (never deleted)
MAX_STORES = 1024


class Retained(ValueError):
    next = 'Keep this version for saved scientific work. Turn off the capability instead of removing its runtime.'


class Uncertain(Retained):
    next = 'Restore access to the saved Project data and its runtime associations, then check storage again. No software was removed.'


def _directory(path, root, *, create=True):
    """Make `path` and its missing folders (or, without `create`, refuse a missing one). `root` is resolved; no folder
    from it down to `path` may be a link, so nothing is read or written through one."""
    from .scientific_projects import ProjectStore
    missing, parent = [], root
    while not parent.exists() and not parent.is_symlink():
        missing.append(parent)
        parent = parent.parent
    if missing and not create:
        raise Uncertain('There is no such carried-over receipt')
    for folder in reversed(missing):
        folder.mkdir(exist_ok=True, mode=0o700)
        ProjectStore._sync_directory(folder.parent)
    folder = root
    for part in path.relative_to(root).parts:
        folder = folder / part
        if not folder.is_symlink() and not folder.exists():
            if not create:
                raise Uncertain('There is no such carried-over receipt')
            folder.mkdir(exist_ok=True, mode=0o700)
            ProjectStore._sync_directory(folder.parent)
        if folder.is_symlink() or not folder.is_dir():
            raise Uncertain('Runtime retention metadata cannot be redirected')


@contextmanager
def _lock(path):
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise Uncertain('Runtime retention lock is not a regular file')
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _read(path):
    from .scientific_projects import ProjectStore, InvalidProject
    try:
        return json.loads(ProjectStore._read(path, 256 << 10))
    except (OSError, ValueError, RecursionError, InvalidProject) as error:
        raise Uncertain('Saved runtime associations are unreadable; removal is blocked') from error


def _write(path, value):
    from .scientific_projects import ProjectStore, _json
    ProjectStore._write(path, _json(value))


def _roots(store):
    file = store / BINDINGS
    if not file.exists() and not file.is_symlink():
        return []
    roots = _read(file)
    if (type(roots) is not list or len(roots) > 32 or any(type(p) is not str or not Path(p).is_absolute() for p in roots)
            or len(set(roots)) != len(roots)):
        raise Uncertain('Saved runtime roots are invalid; removal is blocked')
    return [Path(p) for p in roots]


def _association(root, store):
    return root / METADATA / (hashlib.sha256(str(store).encode()).hexdigest() + '.json')


def _present(path):
    return path.exists() or path.is_symlink()


def _carried(root):
    """Records in this runtime folder naming another folder as theirs: they came along when a profile was copied or
    moved (both records keep absolute paths). Unreadable or malformed ones are left alone, so removal stays blocked."""
    found = []
    for path in sorted((root / METADATA).glob('*.json')):
        try:
            value = _read(path)
        except Uncertain:
            continue
        if (type(value) is dict and set(value) == {'store', 'runtimeRoot'}
                and all(type(value[k]) is str and Path(value[k]).is_absolute() for k in value)
                and value['runtimeRoot'] != str(root) and _association(root, Path(value['store'])) == path):
            found.append((path, value))
    return found


def _superseded(root, store, value):
    """A carried record is set aside only when that is known to keep every veto: the profile it was copied from is
    still whole and does not claim this folder (it keeps its own records), or its store moved here together with this
    folder (same place beside it) and this store registers in its stead. Anything else keeps removal blocked."""
    old_store, old_root = Path(value['store']), Path(value['runtimeRoot'])
    if old_store.is_dir() and not old_store.is_symlink() and old_root.is_dir():
        try:
            return root not in _roots(old_store)
        except Uncertain:
            return False
    return not _present(old_store) and os.path.relpath(old_root, old_store) == os.path.relpath(root, store)


def _rehome(store, root, roots):
    """Called holding the store's binding lock. A profile carried to another folder (H12 C3b, 16:58Z: a copied
    profile's preparation retry was refused until a developer edited both records) is bound to the runtime folder
    beside it: the carried records are copied into a receipt folder, the store registers here, it stops naming the
    folder it was copied from (it never registered there, so no veto is lost and the original is never written), and
    the saved work must read exactly as before or everything is put back. Returns the store's other runtime roots."""
    from .scientific_projects import ProjectStore
    with locked(root):
        carried = [(path, value) for path, value in _carried(root) if _superseded(root, store, value)]
        if not carried:
            return roots
        layout = os.path.relpath(root, store)
        moved = {Path(v['runtimeRoot']) for _, v in carried if os.path.relpath(v['runtimeRoot'], v['store']) == layout}
        kept = [r for r in roots if r == root or r not in moved or _present(_association(r, store))]
        before = list(_saved(store, [0]))  # every accepted revision's runtimes; the same must be retained afterwards
        bindings = store / BINDINGS
        original = ProjectStore._read(bindings, 256 << 10) if _present(bindings) else None
        association = _association(root, store)
        expected = {'store': str(store), 'runtimeRoot': str(root)}
        receipt = {'format': 1, 'store': str(store), 'runtimeRoot': str(root),
                   'at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'state': 'started',
                   'createdAssociation': not _present(association) or any(p == association for p, _ in carried),
                   'records': [], 'bindings': {'before': [str(r) for r in roots], 'saved': original is not None,
                   'after': [str(r) for r in sorted({*kept, root}, key=str)],
                   'sha256': hashlib.sha256(original).hexdigest() if original is not None else None}}
        folder = root / METADATA / CARRIED / (time.strftime('%Y%m%dT%H%M%SZ', time.gmtime()) + '-' + secrets.token_hex(4))
        _directory(folder, root)
        for path, value in carried:
            body = ProjectStore._read(path, 256 << 10)
            ProjectStore._write(folder / path.name, body, immutable=True)
            receipt['records'].append({'name': path.name, 'sha256': hashlib.sha256(body).hexdigest(), 'value': value})
        if original is not None:
            ProjectStore._write(folder / 'bindings.json', original, immutable=True)
        _write(folder / 'receipt.json', receipt)
        try:
            _verify_archive(folder, receipt)
            # Every moment either the carried record (removal blocked) or this store's registration is in place.
            if not any(path == association for path, _ in carried):
                _register(root, store)
            _write(bindings, receipt['bindings']['after'])
            for path, _ in carried:
                if path == association:
                    _write(path, expected)
                else:
                    path.unlink()
            ProjectStore._sync_directory(root / METADATA)
            if (_read(association) != expected or _roots(store) != [Path(r) for r in receipt['bindings']['after']]
                    or any(_present(p) for p, _ in carried if p != association)
                    or list(_documents(root, store, association, [0])) != before):
                raise Uncertain('The carried-over runtime records did not verify')
        except BaseException:
            _undo(root, folder, receipt)
            raise
        receipt['state'] = 'complete'
        _write(folder / 'receipt.json', receipt)
        return [r for r in kept if r != root]


def _verify_archive(folder, receipt):
    from .scientific_projects import ProjectStore
    for record in receipt['records']:
        if hashlib.sha256(ProjectStore._read(folder / record['name'], 256 << 10)).hexdigest() != record['sha256']:
            raise Uncertain('A carried-over runtime record was not kept exactly')
    if receipt['bindings']['saved'] and hashlib.sha256(
            ProjectStore._read(folder / 'bindings.json', 256 << 10)).hexdigest() != receipt['bindings']['sha256']:
        raise Uncertain('The saved runtime roots were not kept exactly')


RECORD = re.compile(r'[0-9a-f]{64}\.json')


def _check_receipt(receipt, root):
    """A receipt is read back from disk before an undo: every name it gives must be a bare association file name and
    every field its expected type, or nothing is read or written."""
    if type(receipt) is not dict:
        raise Uncertain('The carried-over receipt is invalid; nothing was restored')
    records, bindings = receipt.get('records'), receipt.get('bindings')
    if (receipt.get('format') != 1 or receipt.get('runtimeRoot') != str(root)
            or type(receipt.get('store')) is not str or not Path(receipt['store']).is_absolute()
            or type(receipt.get('createdAssociation')) is not bool or type(records) is not list or len(records) > MAX_STORES
            or any(type(r) is not dict or type(r.get('name')) is not str or RECORD.fullmatch(r['name']) is None
                   or Path(r['name']).name != r['name'] or type(r.get('sha256')) is not str for r in records)
            or type(bindings) is not dict or type(bindings.get('saved')) is not bool
            or (bindings['saved'] and type(bindings.get('sha256')) is not str)):
        raise Uncertain('The carried-over receipt is invalid; nothing was restored')


def _undo(root, folder, receipt):
    """Put every record a rehoming changed back exactly as its receipt kept it. Called holding both locks."""
    from .scientific_projects import ProjectStore
    _check_receipt(receipt, root)
    _verify_archive(folder, receipt)
    store = Path(receipt['store'])
    names = {record['name'] for record in receipt['records']}
    for name in sorted(names):
        ProjectStore._write(root / METADATA / name, ProjectStore._read(folder / name, 256 << 10))
    association = _association(root, store)
    if receipt['createdAssociation'] and association.name not in names:
        association.unlink(missing_ok=True)
    if receipt['bindings']['saved']:
        ProjectStore._write(store / BINDINGS, ProjectStore._read(folder / 'bindings.json', 256 << 10))
    else:
        (store / BINDINGS).unlink(missing_ok=True)
    receipt['state'] = 'restored'
    _write(folder / 'receipt.json', receipt)


def restore(root, name):
    """Undo one rehoming from its receipt (`python -m orchestrator.scientific_retention restore <runtime root> <name>`).
    The next start sets the same records aside again unless the copy is moved back where they say it was."""
    root = Path(root).resolve()
    folder = root / METADATA / CARRIED / name
    if type(name) is not str or Path(name).name != name or name in ('', '.', '..'):
        raise Uncertain('There is no such carried-over receipt')
    _directory(folder, root, create=False)  # refuses a missing or linked folder on the way down
    if (folder / 'receipt.json').is_symlink() or not (folder / 'receipt.json').is_file():
        raise Uncertain('There is no such carried-over receipt')
    receipt = _read(folder / 'receipt.json')
    _check_receipt(receipt, root)
    with _lock(Path(receipt['store']) / '.runtime-bindings.lock'), locked(root):
        _undo(root, folder, receipt)


@contextmanager
def locked(root):
    root = Path(root).resolve()
    metadata = root / METADATA
    _directory(metadata, root)
    with _lock(metadata / '.lock'):
        yield root


def _register(root, store):
    expected = {'store': str(store), 'runtimeRoot': str(root)}
    association = _association(root, store)
    if association.exists() or association.is_symlink():
        if _read(association) != expected:
            raise Uncertain('A saved runtime association changed; removal is blocked')
    else:
        if sum(1 for _ in (root / METADATA).glob('*.json')) >= MAX_STORES:
            raise Uncertain('The saved Project store registration limit was reached')
        _write(association, expected)


@contextmanager
def publication(store, runtime_root=None):
    """Lock order is store binding, then sorted runtime roots; deletion never locks a store."""
    store = Path(store).resolve()
    with _lock(store / '.runtime-bindings.lock'):
        try:
            roots = _roots(store)
        except Uncertain:
            if runtime_root is not None:
                with locked(runtime_root) as current:
                    _register(current, store)
            raise
        if runtime_root is not None:
            roots = _rehome(store, Path(runtime_root).resolve(), roots)
            roots = sorted(set([*roots, Path(runtime_root).resolve()]), key=str)
        if len(roots) > 32:
            raise Uncertain('This Project store already has the maximum number of runtime roots')
        if runtime_root is not None:
            with locked(runtime_root) as current:
                _register(current, store)
        with ExitStack() as ownership:
            for root in roots:
                ownership.enter_context(locked(root))
                _register(root, store)
            if runtime_root is not None:
                _write(store / BINDINGS, [str(root) for root in roots])
            yield


def bind(store, runtime_root):
    store._ensure_root()
    with publication(store.root, runtime_root):
        pass


def references(root):
    """Call while holding this runtime root's lock. Read every accepted revision, not orphan files."""
    root = Path(root).resolve()
    metadata = root / METADATA
    paths = list(metadata.glob('*.json'))
    if len(paths) > MAX_STORES:
        raise Uncertain('Too many saved Project stores to verify removal safely')
    records = [0]
    for path in paths:
        value = _read(path)
        if (type(value) is not dict or set(value) != {'store', 'runtimeRoot'} or value['runtimeRoot'] != str(root)
                or type(value['store']) is not str or not Path(value['store']).is_absolute()):
            raise Uncertain('A saved Project association is invalid; removal is blocked')
        yield from _documents(root, Path(value['store']), path, records)


def _documents(root, store_root, path, records):
    if (store_root.is_symlink() or not store_root.is_dir() or _association(root, store_root) != path
            or root not in _roots(store_root)):
        raise Uncertain('A saved Project store is unavailable or its registration was interrupted; removal is blocked')
    yield from _saved(store_root, records)


def _saved(store_root, records):
    from .scientific_projects import ProjectStore, PROJECT_ID, InvalidProject
    store = ProjectStore(store_root)
    for folder in store_root.iterdir():
        if PROJECT_ID.fullmatch(folder.name) is None:
            continue
        try:
            store._folder(folder.name)
            if not folder.is_dir():
                raise Uncertain('Scientific document is not a directory')
            head_file = folder / 'head.json'
            if not head_file.exists() and not head_file.is_symlink():
                if (folder / 'revisions').exists():
                    raise Uncertain('Scientific history has no accepted head')
                continue
            head = _read(head_file)
            session = head.get('session') if type(head) is dict else None
            history = store._head(store._folder(folder.name), session)
            records[0] += len(history)
            if records[0] > 100_000:
                raise Uncertain('Too much scientific history to verify removal safely in one operation')
            for revision in range(1, len(history) + 1):
                saved = store.read(folder.name, session, revision=revision)
                for ref in saved.document['runtimes']:
                    yield {**ref, 'document': saved.id, 'session': saved.session, 'revision': saved.revision}
        except (OSError, ValueError, RecursionError, InvalidProject) as error:
            raise Uncertain(f'Saved scientific document {folder.name} cannot be verified; removal is blocked') from error


def require_unreferenced(refs, *, models, generations=None):
    owners = {model.replace('/', '--') for model in models}
    retained = []
    for ref in refs:
        if ref['bundle'].replace('/', '--') in owners and (generations is None or ref['generation'] in generations):
            retained.append(ref)
    if retained:
        documents = len({(r['session'], r['document']) for r in retained})
        noun = "document" if documents == 1 else "documents"
        raise Retained(f'This runtime is kept for {documents} saved scientific {noun}.')


def require_removable(root, *, models, generations=None):
    require_unreferenced(references(root), models=models, generations=generations)


if __name__ == '__main__':
    if len(sys.argv) != 4 or sys.argv[1] != 'restore':
        sys.exit('usage: python -m orchestrator.scientific_retention restore <runtime root> <receipt folder name>')
    restore(sys.argv[2], sys.argv[3])
