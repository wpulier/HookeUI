"""Managed runtime storage, grouped by its real owner; original work and proof inputs are never removal targets."""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import re
import shutil

from gpu_worker import manifests as catalog, model_files
from . import envs, fiji_generations, runtime_cleanup


def _pending(folder: Path) -> bool:
    return runtime_cleanup.pending(folder)


def _roots() -> list[dict]:
    entries = {**catalog.manifests(), **catalog.mine()}
    groups: dict[tuple, dict] = {}
    for m in entries.values():
        candidates = [('files', model_files.folder(m), 'Downloaded files')]
        if m.env:
            candidates.append(('environment', envs.root(m), 'Isolated Python'))
        if m.runner == 'fiji':
            candidates.append(('fiji', fiji_generations.root(), 'Shared Fiji installation'))
        for kind, folder, label in candidates:
            if folder.is_symlink() or not folder.is_dir() or not folder.resolve().is_relative_to(catalog.catalog_root().resolve()):
                continue
            def allowed(p):
                if not p.is_dir() or p.is_symlink():
                    return False
                if _pending(p):
                    # Reserved withdrawal folders are never executable. Their receipt may already have
                    # been deleted before cleanup was interrupted, so receipt presence cannot gate retry.
                    return bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,191}', p.name[len('.removing-'):]))
                return (p.name.isdigit() if kind == 'fiji' else
                        bool(re.fullmatch('[0-9a-f]{16}', p.name)) if kind == 'environment' else
                        not p.name.startswith('.') and (p / 'receipt.json').is_file())
            dirs = sorted((p for p in folder.iterdir() if allowed(p)), key=str)
            for pending in (False, True):
                paths = [p for p in dirs if _pending(p) == pending]
                if not paths:
                    continue
                identity = (kind, str(folder), pending)
                group = groups.setdefault(identity, {'kind': kind, 'name': label + ': ' + m.name,
                                                      'models': [], 'paths': paths, 'pending': pending})
                group['models'].append(m.key)
    return list(groups.values())


def _facts(item: dict) -> tuple[int, str]:
    records = []
    for folder in item['paths']:
        for p in sorted(folder.rglob('*')):
            stat = p.lstat()  # symlinks are counted as links; never follow them into another runtime or user's work
            # A file with another hard link keeps its space after removal, so it is not counted. An APFS clone (a download
            # adopted from a cache, a uv install) has one link but may share its blocks, so sizes are said as "up to".
            freed = p.is_file() and not p.is_symlink() and stat.st_nlink == 1
            records.append((str(p.relative_to(folder.parent)), stat.st_size if freed else 0, stat.st_mtime_ns, stat.st_ino))
    return sum(r[1] for r in records), hashlib.sha256(json.dumps(records).encode()).hexdigest()


def size_words(n: int) -> str:
    """As the studio says sizes (capabilityspace.ts storageWords)."""
    return f'{n / 1024 ** 3:.2f} GB' if n >= 1024 ** 3 else f'{n / 1024 ** 2:.1f} MB' if n >= 1024 ** 2 else f'{n:,} bytes'


def _names(models) -> str:
    names = [getattr(catalog.get(key), 'name', key) for key in models]
    return names[0] if len(names) == 1 else ', '.join(names[:-1]) + ' and ' + names[-1]


_SETUP = {'files': 'Download', 'environment': 'Install', 'fiji': 'Fiji setup'}


def _saved_work() -> str:
    """Where the user's own work is, said as a person reads it (~/MicroscopeMind)."""
    from . import paths
    home, mac = paths.Paths.from_env().home, Path.home()
    return '~/' + str(home.relative_to(mac)) if home.is_relative_to(mac) else str(home)


def _row_words(row: dict) -> str:
    """What removing this row does to storage and to the capabilities that use it, in plain words."""
    size, names = size_words(row['bytes']), _names(row['models'])
    if row.get('pendingRemoval'):
        return (f"{row['pendingRemoval']} withdrawn {'copy' if row['pendingRemoval'] == 1 else 'copies'} (up to {size}) "
                'still need cleanup. Finishing frees that space; installed versions and saved work stay.')
    if row.get('retention') and not row['retention']['checked']:
        return (f"Not removable now: whether saved work uses these files ({size}) could not be checked. "
                f"{row['retention']['reason']}")
    if row.get('retention'):
        return f"Kept: {size} stays on this Mac because saved work uses it. {row['retention']['reason']}"
    them = 'it runs' if len(row['models']) == 1 else 'they run'
    return (f"Remove frees up to {size} (less where its files share space with another copy, such as a download "
            f"cache). {names} then {'needs' if len(row['models']) == 1 else 'need'} "
            f"{_SETUP.get(row['kind'], 'setup')} again before {them}. Saved results, the entries and their original "
            'proof inputs stay.')


def gain_words(before: int, after: int) -> str:
    """The measured change in the disk's free space while something was removed (other writes change it too)."""
    gain = after - before
    return (f"this disk's free space rose by {size_words(gain)} while it was removed" if gain >= 0 else
            f"this disk's free space fell by {size_words(-gain)} while it was removed, because other work wrote to it")


def _id(item: dict) -> str:
    suffix = ':pending-removal' if item['pending'] else ''
    return hashlib.sha256((item['kind'] + str(item['paths'][0].parent) + suffix).encode()).hexdigest()[:24]


def inventory(projects=None) -> dict:
    from . import scientific_retention, paths
    blocked = None
    try:
        if projects is not None:
            projects.register_runtime()
        with scientific_retention.locked(paths.envs()) as runtime_root:
            references = list(scientific_retention.references(runtime_root))
    except scientific_retention.Retained as error:
        references, blocked = [], error
    rows = []
    for item in _roots():
        size, revision = _facts(item)
        row = {'id': _id(item), 'name': item['name'], 'kind': item['kind'], 'models': item['models'],
               'bytes': size, 'revision': revision, 'generations': len(item['paths'])}
        if item['pending']:
            row['pendingRemoval'] = len(item['paths'])
            rows.append(row)
            continue
        try:
            if blocked is not None:
                raise blocked
            scientific_retention.require_unreferenced(references, models=set(item['models']))
        except scientific_retention.Retained as error:
            # Uncertain: the saved-work check itself could not run, which is not the same as saved work using it.
            row['retention'] = {'reason': str(error), 'next': error.next,
                                'checked': not isinstance(error, scientific_retention.Uncertain)}
        rows.append(row)
    for row in rows:
        row['says'] = _row_words(row)
    total = sum(r['bytes'] for r in rows)
    kept = sum(r['bytes'] for r in rows if r.get('retention') and r['retention']['checked'])
    unchecked = sum(r['bytes'] for r in rows if r.get('retention') and not r['retention']['checked'])
    where = _saved_work()
    free = total - kept - unchecked
    said = [f'up to {size_words(free)} can be freed here' if free else 'nothing can be freed here']
    if kept:
        said.append(f'{size_words(kept)} is kept because saved work uses it')
    if unchecked:
        said.append(f"{size_words(unchecked)} can't be removed now because the check of saved work could not run")
    says = (f'{size_words(total)} in managed copies: ' + (', '.join(said[:-1]) + ' and ' + said[-1] if len(said) > 1
                                                          else said[0]) + '.')
    return {'items': rows, 'bytes': total, 'reclaimableBytes': total - kept - unchecked, 'keptBytes': kept,
            'uncheckedBytes': unchecked, 'says': says,
            'savedWork': where,
            'preserved': f'Your Projects, pictures, datasets and recordings in {where} are not counted here and are never '
                         'removed by Space. Model entries and original proof inputs stay too.'}


def _busy(app, models: set[str]) -> bool:
    from . import ticket

    if ticket.working(app.hub):
        return True
    registrations = getattr(app, 'registrations', None)
    if registrations is not None and registrations.running():
        return True
    running = {'queued', 'downloading', 'checking', 'installing'}
    if any(r.get('model') in models and r.get('state') in running
           for queue in (app.downloads, app.installs) for r in queue.wire()):
        return True
    checks = getattr(app.hub, 'checks', None)
    return bool(checks and checks.running)


@contextmanager
def _removal_boundary(item: dict):
    """Check before worker retirement, then acquire again through actual withdrawal.

    The first check avoids disrupting work for a removal already known to be refused. Only the second,
    held through renaming, authorizes removal: references and owners can change while workers retire.
    """
    from . import fiji_contributions, generation_leases, scientific_retention, paths

    with scientific_retention.locked(paths.envs()) as runtime_root, fiji_contributions.LOCK, ExitStack() as owners:
        if not item['pending']:
            scientific_retention.require_removable(runtime_root, models=set(item['models']))
        try:
            for folder in item['paths']:
                owners.enter_context(generation_leases.withdraw(folder))
        except generation_leases.Busy as exc:
            raise ValueError(str(exc)) from exc
        if item['kind'] == 'fiji' and not item['pending']:
            retained = fiji_contributions.retained_generations()
            if any(int(folder.name) in retained for folder in item['paths']):
                raise ValueError('Fiji generations used by contributed capabilities are retained; their working versions stay available')
        yield


async def remove(app, identity: str, revision: str) -> dict:
    item = next((r for r in await asyncio.to_thread(_roots) if _id(r) == identity), None)
    if item is None:
        raise ValueError('That runtime copy is gone; refresh Space')
    models = set(item['models'])
    projects = getattr(getattr(app.hub, 'resources', None), 'projects', None)
    if projects is not None:
        await asyncio.to_thread(projects.register_runtime)
    if _busy(app, models):
        raise ValueError('Let its running download, installation or functional check finish, then remove it')
    size, current = await asyncio.to_thread(_facts, item)
    if current != revision:
        raise ValueError('Its storage changed since you reviewed it; refresh Space before removing it')
    def preflight():
        with _removal_boundary(item):
            pass
    await asyncio.to_thread(preflight)
    loads = getattr(app.hub, 'loads', None)
    if loads is not None and not item['pending']:
        for client in loads.clients:
            if set(getattr(client, 'loading', {})) & models:
                raise ValueError('A model is loading; let that work finish before removing it')
            for model in set(getattr(client, 'resident', {})) & models:
                reply = await loads.let_go(client, model)
                if not reply.get('unloaded'):
                    raise ValueError('Its worker is still using this runtime; let the current work finish and retry')
    if item['kind'] == 'fiji' and not item['pending']:
        from . import fiji_bindings, fiji_runner
        await asyncio.to_thread(fiji_bindings.WORKER.stop)
        if fiji_runner.WORKER is not None:
            await asyncio.to_thread(fiji_runner.WORKER.stop)
    # Atomic withdrawal keeps new lookups from opening a half-deleted environment. A storage action never traverses
    # the parent folder, which also holds the entry and original .proof-input.npy files.
    final_facts = await asyncio.to_thread(_facts, item)
    if final_facts != (size, current) or _busy(app, models):
        raise ValueError('Its storage changed or work started; refresh Space and retry')
    def withdraw():
        with _removal_boundary(item):
            return runtime_cleanup.withdraw(item['paths'])

    disk = item['paths'][0].parent
    free_before = await asyncio.to_thread(lambda: shutil.disk_usage(disk).free)
    trash = await asyncio.to_thread(withdraw)
    if not item['pending']:
        app.catalog_changed()
    try:
        for folder in trash:
            await asyncio.to_thread(runtime_cleanup.discard, folder)
    except OSError as exc:
        _said(app, f"Withdrew {item['name']}, but some of its files could not be removed yet; they are listed in "
                   'Space to finish. Your saved work is kept.', item, state='failed')
        raise ValueError('Software was withdrawn, but some files could not be removed. Refresh storage and finish the pending removal. Your saved work is kept.') from exc
    free_after = await asyncio.to_thread(lambda: shutil.disk_usage(disk).free)
    names, where = _names(item['models']), await asyncio.to_thread(_saved_work)
    measured = f'Its files took up to {size_words(size)}; {gain_words(free_before, free_after)}.'
    if item['pending']:
        says = (f"Finished removing withdrawn copies of {item['name']}. {measured} Installed versions and your saved "
                f'work in {where} are kept.')
    else:
        one = len(item['models']) == 1
        says = (f"Removed {item['name']}. {measured} {names} {'needs' if one else 'need'} "
                f"{_SETUP.get(item['kind'], 'setup')} again before {'it runs' if one else 'they run'}. Your Projects "
                f'and saved results in {where}, the entries and their original proof inputs are kept.')
    _said(app, says, item)
    return {'ok': True, 'filesBytes': size, 'freedBytes': max(0, free_after - free_before), 'models': item['models'],
            'says': says}


def _said(app, text: str, item: dict, *, state: str = 'done') -> None:
    """Space's removals are kept in Activity, as the card's Remove is (commands.py model-uninstall)."""
    from . import activity
    activity.activity(app.hub, 'model', text, state=state, ref={'model': item['models'][0]} if item['models'] else None)
