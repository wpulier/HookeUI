"""Start from a published Project, and update from it later (ADR 0028, format v2 section 9).

Starting from makes an independent Project here: its own origin and handle, so it sits beside the original or another
copy of the same publication. The publication's accepted documents come in byte for byte through the owner-aware
import, under their own (inherited) owner: the new Project lists and reads them and never saves over them. Its plain
result files are copied, and the lines that list them (its tables, evidence, tracks and notes) join their inherited
owner's journal, so they are listed too. It inherits no grants or registry entries; the caller records its ancestry. Update brings
newer published revisions in the same way: a document is added when absent or extended when the local history is a
prefix of the published one, and anything else is refused with that document unchanged.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import scientific_exchange as exchange
from .project_remote import Remote, fetch
from .project_repository import document_root, session_root
from .project_roots import ProjectRoot, writer
from .scientific_projects import ProjectStore

PROJECT = re.compile(r'p[0-9a-f]{32}')
# Plain files that an update may add to a working copy when they are absent; never overwritten.
COPIED = ('artifacts/results', 'artifacts/tables', 'artifacts/table-derivatives', 'artifacts/evidence',
          'artifacts/snapshots', 'artifacts/bookmarks', 'artifacts/volumes', 'artifacts/identity', 'artifacts/research')


def start_from(remote: Remote, create, *, staging: Path, commit=None, source=None, held=False, check=lambda: None):
    """Fetch a publication and start an independent Project from it.

    `create(avoid)` makes the new, empty Project on this host (its own origin, and a handle that is none of `avoid`,
    the publication's owners) and returns its ProjectRoot;
    `held` says this host already holds that Project's writer lease. Returns (the new ProjectRoot, the ancestry event
    to record, what came in). A publication whose documents do not all come in is refused, and what came in is said.
    """
    staging = Path(staging)
    staging.mkdir(mode=0o700)
    try:
        fetched, details = fetch(remote, staging / 'fetched', commit=commit, check=check)
        sessions = session_root(fetched)
        theirs = frozenset(f.name for f in sessions.iterdir()) if sessions.is_dir() else frozenset()
        root = create(frozenset(owner for _, owner, _ in _documents(fetched)) | {fetched.session} | theirs)
        result = _bring_in(fetched, details, root, staging, held=held, check=check)
        if result['refused']:
            raise exchange.BundleRefused('Not everything in that publication could come in: '
                                         + '; '.join(r['reason'] for r in result['refused'][:3]))
        event = {'type': 'project-started-from', 'origin': details['origin'], 'snapshotSha256': details['snapshotSha256'],
                 'source': {**(source or remote.describe()['records']), 'commit': details['commit']},
                 'documents': len(result['added']), 'results': result['resultsAdded']}
        return root, event, result
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _documents(root: ProjectRoot):
    found = []
    records = document_root(root)
    for folder in sorted(records.iterdir()) if records.is_dir() else ():
        if PROJECT.fullmatch(folder.name):
            head = json.loads((folder / 'head.json').read_text())
            found.append((folder.name, head['session'], tuple(head['revisions'])))
    return found


def _sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1 << 20), b''):
            value.update(block)
    return value.hexdigest()


def update_from(root: ProjectRoot, remote: Remote, *, origin: str, staging: Path, commit=None, held=False,
                check=lambda: None):
    """Bring newer published work into `root`, which was started from the publication of Project `origin` (its
    ancestry event says so). Returns what changed and what was refused.

    `held` says this host already holds the Project's writer lease (the open Project); otherwise it is taken here.
    """
    staging = Path(staging)
    staging.mkdir(mode=0o700)
    try:
        fetched, details = fetch(remote, staging / 'fetched', commit=commit, check=check)
        if fetched.origin != origin:
            raise exchange.BundleRefused('That publication is a different Project; start from it instead')
        return _bring_in(fetched, details, root, staging, held=held, check=check)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _bring_in(fetched: ProjectRoot, details: dict, root: ProjectRoot, staging: Path, *, held, check):
    """The publication's documents into `root` through the owner-aware import, and its plain files where absent."""
    source = ProjectStore(document_root(fetched))
    target = ProjectStore(document_root(root))
    result = {'commit': details['commit'], 'snapshotSha256': details['snapshotSha256'],
              'added': [], 'extended': [], 'unchanged': [], 'refused': [], 'filesAdded': 0, 'fileConflicts': []}
    with (contextlib.nullcontext() if held else writer(root)):
        local = {identity: (owner, revisions) for identity, owner, revisions in _documents(root)}
        for identity, owner, revisions in _documents(fetched):
            check()
            had = local.get(identity)
            if had is not None and had == (owner, revisions):
                result['unchanged'].append(identity)
                continue
            bundle = Path(tempfile.mkstemp(prefix='update-', suffix='.zip', dir=staging)[1])
            bundle.unlink()
            try:
                exchange.export(source, identity, owner, None, destination=bundle,
                                sessions=session_root(fetched))
                exchange.reimport(target, bundle, sessions=session_root(root))
            except exchange.BundleRefused as error:
                result['refused'].append({'id': identity, 'reason': str(error)})
                continue
            result['added' if had is None else 'extended'].append(identity)
        for section in COPIED:
            folder = fetched.path / section
            for path in sorted(folder.rglob('*')) if folder.is_dir() else ():
                if not path.is_file() or path.is_symlink():
                    continue
                relative = path.relative_to(fetched.path)
                out = root.path / relative
                if out.exists() or out.is_symlink():
                    if out.is_symlink() or _sha(out) != _sha(path):
                        result['fileConflicts'].append(relative.as_posix())
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, out)
                os.chmod(out, 0o600)
                result['filesAdded'] += 1
        result['resultsAdded'] = _results(fetched, root, result, check=check)
    return result


def _results(fetched: ProjectRoot, root: ProjectRoot, result: dict, *, check) -> int:
    """The publication's result-listing lines, each under its inherited owner here (project_inherited.bring_in)."""
    from .day.sid import is_sid
    from .project_inherited import bring_in, read_journal, split
    added, theirs, ours = 0, session_root(fetched), session_root(root)
    for folder in sorted(theirs.iterdir()) if theirs.is_dir() else ():
        check()
        if not is_sid(folder.name) or folder.is_symlink() or not folder.is_dir() or folder.name == root.session:
            continue
        try:
            shape = split(read_journal(folder / 'journal.jsonl'), folder.name)
        except (OSError, ValueError):
            shape = None
        if shape is None:
            continue  # a session the publication holds without listed results
        try:
            added += bring_in(ours, folder.name, *shape)
        except (OSError, ValueError) as error:
            result['refused'].append({'id': folder.name, 'reason': f'its results could not come in: {error}'})
    return added
