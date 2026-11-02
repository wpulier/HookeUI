"""Start from a published Project, and update from it later (ADR 0028, format v2 section 9).

Starting from fetches a publication as a verified sealed snapshot and restores a working copy of it. It inherits no
grants or registry entries; the caller registers the copy and records its ancestry. Update brings newer published
revisions in through the owner-aware import: a document is added when absent or extended when the local history is
a prefix of the published one, and anything else is refused with that document unchanged.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from . import scientific_exchange as exchange
from .project_folder_copy import restore_snapshot
from .project_remote import Remote, fetch
from .project_repository import document_root, session_root
from .project_roots import ProjectRoot, writer
from .scientific_projects import ProjectStore

PROJECT = re.compile(r'p[0-9a-f]{32}')
# Plain files that an update may add to a working copy when they are absent; never overwritten.
COPIED = ('artifacts/results', 'artifacts/tables', 'artifacts/table-derivatives', 'artifacts/evidence',
          'artifacts/snapshots', 'artifacts/bookmarks', 'artifacts/volumes', 'artifacts/identity', 'artifacts/research')


def start_from(remote: Remote, destination: Path, *, staging: Path, commit=None, source=None, check=lambda: None):
    """Fetch and restore a working copy. Returns (working ProjectRoot, the ancestry event to record)."""
    staging = Path(staging)
    staging.mkdir(mode=0o700)
    Path(destination).expanduser().parent.mkdir(parents=True, exist_ok=True)
    fetched, details = fetch(remote, staging / 'fetched', commit=commit, check=check)
    working = restore_snapshot(fetched.path, destination, check=check)
    event = {'type': 'project-started-from', 'origin': details['origin'], 'snapshotSha256': details['snapshotSha256'],
             'source': {**(source or remote.describe()['records']), 'commit': details['commit']}}
    shutil.rmtree(staging, ignore_errors=True)
    return working, event


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


def update_from(root: ProjectRoot, remote: Remote, *, staging: Path, commit=None, check=lambda: None):
    """Bring newer published work into the working copy `root`. Returns what changed and what was refused."""
    staging = Path(staging)
    staging.mkdir(mode=0o700)
    try:
        fetched, details = fetch(remote, staging / 'fetched', commit=commit, check=check)
        if fetched.origin != root.origin:
            raise exchange.BundleRefused('That publication is a different Project; start from it instead')
        source = ProjectStore(document_root(fetched))
        target = ProjectStore(document_root(root))
        result = {'commit': details['commit'], 'snapshotSha256': details['snapshotSha256'],
                  'added': [], 'extended': [], 'unchanged': [], 'refused': [], 'filesAdded': 0, 'fileConflicts': []}
        with writer(root):
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
        return result
    finally:
        shutil.rmtree(staging, ignore_errors=True)
