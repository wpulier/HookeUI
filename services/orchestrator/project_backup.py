"""Back up a whole Project privately, and restore it on another Mac (ADR 0028, format v2 sections 6 and 7; ticket 08).

A backup is the full sealed snapshot, journals and conversations included, pushed to a destination only its owner
reads: a folder (an external drive, a shared folder) or a private GitHub repository. Each backup chains onto the
last one this Project sent there, and the remote moves by compare-and-swap, so two Macs never overwrite each other:
the one that falls behind is told to restore the newer backup first.

Restoring brings the same Project back (its origin, handle and history), not a copy: on another Mac it is registered
as that Mac's Project, with no agent permissions, and after a wipe it takes the place of the location that is gone.
The restored journal records where it came from, so its next backup chains onto the version it was restored from.
"""
from __future__ import annotations

from pathlib import Path
import re
import shutil

from .project_folder_copy import restore_snapshot
from .project_remote import Diverged, Remote, fetch, push_sealed
from .project_remote_layers import RemoteRefused
from .project_roots import ProjectRoot

BACKED_UP = 'project-backed-up'
RESTORED = 'project-restored-from-backup'


class BehindBackup(RemoteRefused):
    """The backup has a newer version than this Mac sent: another Mac backed up the same Project since."""


def _place(destination) -> str:
    """Where a backup is, as one comparable value: GitHub names repositories without regard to case."""
    records = (destination or {}).get('records') if type(destination) is dict else None
    if type(records) is not dict:
        return ''
    if records.get('kind') == 'github':
        return 'github:' + str(records.get('location', '')).lower()
    return f"{records.get('kind')}:{records.get('base') or records.get('location') or ''}"


def last_backup(events, destination: dict):
    """The commit this Project's next backup to `destination` chains onto: its last backup there, or the backup it
    was restored from, whichever came later."""
    last, place = None, _place(destination)
    for event in events:
        if event.get('type') in (BACKED_UP, RESTORED) and _place(event.get('destination')) == place:
            last = event.get('commit')
    return last


def backups(events):
    """Each destination this Project was backed up to or restored from, with its latest event."""
    latest = {}
    for event in events:
        if event.get('type') in (BACKED_UP, RESTORED) and type(event.get('destination')) is dict:
            latest[_place(event['destination'])] = event
    return list(latest.values())


def back_up(seal, remote: Remote, *, staging: Path, parent=None, expected_head=None, scan_secrets=True,
            check=lambda: None):
    """Seal the Project (`seal(destination)` returns the sealed folder or ProjectRoot) and push all of it. Returns the
    push receipt and the journal event to record.

    `parent` is the last backup this Project sent there (last_backup). `expected_head` is a new GitHub repository's
    placeholder commit, which the first backup replaces. A destination outside the owner's own storage scans for
    secrets (`scan_secrets`); a drive of their own does not.
    """
    staging = Path(staging)
    staging.mkdir(mode=0o700)
    try:
        sealed = seal(staging / 'snapshot')
        sealed = sealed.path if isinstance(sealed, ProjectRoot) else Path(sealed)
        name = ProjectRoot.open(sealed).name
        try:
            receipt = push_sealed(sealed, remote, parent=parent, summary=f'Back up {name}',
                                  scan_findings=None if scan_secrets else [],
                                  expected_head=expected_head if expected_head is not None else parent,
                                  check=check)
        except Diverged as error:
            raise BehindBackup('This backup has a newer version, sent from another Mac. Nothing was sent. Keep '
                               'working on that Mac, or back up this Mac’s version to a new place.') from error
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    event = {'type': BACKED_UP, 'destination': receipt['remote'], 'commit': receipt['commit'],
             'parent': parent, 'snapshotSha256': receipt['snapshotSha256'], 'bytes': receipt['bytes'],
             'uploadedBytes': receipt['uploadedBytes']}
    return {**receipt, 'event': event}


def _free(parent: Path, name: str) -> Path:
    base = re.sub(r'[/:\\\x00-\x1f]+', ' ', name).strip().strip('.') or 'Restored project'
    candidate, number = parent / base[:80], 2
    while candidate.exists() or candidate.is_symlink():
        candidate = parent / f'{base[:72]} ({number})'
        number += 1
    return candidate


def restore(remote: Remote, parent: Path, register, *, staging: Path, commit=None, check=lambda: None):
    """Fetch a backup and restore it as a working Project folder in `parent`, then `register(root)` it on this Mac.
    Returns (the registered ProjectRoot, the journal event to record in it).

    `register` decides whether this Mac may hold it (project_launch.register_owner_folder); a refusal there removes
    the folder made here, and nothing else changes.
    """
    staging = Path(staging)
    staging.mkdir(mode=0o700)
    try:
        fetched, details = fetch(remote, staging / 'fetched', commit=commit, check=check)
        Path(parent).mkdir(parents=True, exist_ok=True)  # a new Mac may not have made its Projects folder yet
        destination = _free(Path(parent), fetched.name)
        try:  # the folder is new and only this restore's: a refusal or failure removes it, and nothing else
            root = restore_snapshot(fetched.path, destination, check=check)
            root = register(root if isinstance(root, ProjectRoot) else ProjectRoot.open(destination))
        except BaseException:
            shutil.rmtree(destination, ignore_errors=True)
            raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    event = {'type': RESTORED, 'destination': remote.describe(), 'commit': details['commit'],
             'snapshotSha256': details['snapshotSha256']}
    return root, event
