"""Shared explicit folder snapshots; no caller-selected paths or completeness claims."""
import asyncio
from pathlib import Path
import re
import stat

from .project_folder_copy import copy_owned_snapshot, estimate, verify_snapshot, _stream, MAX_MANIFEST
from .project_roots import ProjectLocationError, SNAPSHOT

SCOPE = 'project-folder'
REASON = 'External and shared dependencies have not been fully collected. Keep the original Project.'


def prepare(day, session, *, check=lambda: None):
    check()
    locations = getattr(day, 'locations', None)
    root = locations.project(session) if locations is not None else None
    if root is None:
        raise ProjectLocationError('This Project needs explicit collection from its existing storage before folder export')
    value = estimate(root, check=check)
    check()
    if locations.project(session) != root:
        raise ProjectLocationError('The Project location changed during export preparation')
    return {'session': session, 'origin': root.origin, 'scope': SCOPE,
            'files': value['files'], 'bytes': value['bytes'], 'requiredFreeBytes': value['requiredFreeBytes'],
            'collection': {'state': 'unverified', 'reason': REASON},
            'dependencyReview': value['dependencyReview'], 'sourceReview': value['sourceReview']}


def export(day, session, operation, *, acknowledge_incomplete, check=lambda: None):
    if acknowledge_incomplete is not True:
        raise ProjectLocationError('Review and acknowledge the unverified dependency scope before exporting')
    if not isinstance(operation, str) or not re.fullmatch('op_[a-f0-9]{16,32}', operation):
        raise ProjectLocationError('Export needs its admitted operation identity')
    check()
    locations = getattr(day, 'locations', None)
    root = locations.project(session) if locations is not None else None
    if root is None:
        raise ProjectLocationError('Collect this Project into a folder before exporting a folder snapshot')
    parent = Path(locations.parent) / 'Exports'
    if parent.is_symlink():
        raise ProjectLocationError('The export folder is a link; choose a regular Projects parent')
    parent.mkdir(exist_ok=True)
    if not stat.S_ISDIR(parent.stat(follow_symlinks=False).st_mode):
        raise ProjectLocationError('The export destination is not a regular folder')
    destination = parent / f'{root.origin}—{operation}'
    check()
    if not destination.exists():
        copy_owned_snapshot(locations, session, destination, check=check)
    # A lost reply may leave the same operation's completed destination. Reuse its
    # seal rather than copying newer source bytes over the earlier snapshot.
    receipt = verify_snapshot(destination)
    if receipt['origin'] != root.origin:
        raise ProjectLocationError('The export destination belongs to another Project; preserve both')
    return {'session': session, 'origin': root.origin, 'operation': operation,
            'location': str(destination), 'files': receipt['files'], 'bytes': receipt['bytes'],
            'manifestSha256': _stream(destination / SNAPSHOT, max_bytes=MAX_MANIFEST)[1], 'scope': SCOPE,
            'collection': {'state': 'unverified', 'reason': REASON}}


async def owned_export(function, *, stopped):
    """Keep a cancelled caller's copy owned until its publication outcome is observed."""
    task = asyncio.create_task(asyncio.to_thread(function))
    cancelled = False
    while True:
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            stopped()
            if task.done():
                if task.cancelled(): raise
                return task.result()
        except Exception:
            if cancelled: raise asyncio.CancelledError from None
            raise


def reveal_path(day, session, receipt):
    from .project_roots import ProjectRoot
    locations = getattr(day, 'locations', None)
    root = locations.project(session) if locations is not None else None
    if root is None or receipt.get('origin') != root.origin or receipt.get('session') != session:
        raise ProjectLocationError('The export does not match this Project')
    operation = receipt.get('operation')
    if not isinstance(operation, str) or not re.fullmatch('op_[a-f0-9]{16,32}', operation):
        raise ProjectLocationError('The export identity is unavailable')
    parent = Path(locations.parent) / 'Exports'
    if parent.is_symlink():
        raise ProjectLocationError('The export folder was replaced by a link')
    expected = parent / f'{root.origin}—{operation}'
    if str(expected) != receipt.get('location') or ProjectRoot.open(expected).origin != root.origin:
        raise ProjectLocationError('The export folder changed; preserve the original copy')
    from .project_folder_copy import MAX_MANIFEST
    if _stream(expected / SNAPSHOT, max_bytes=MAX_MANIFEST)[1] != receipt.get('manifestSha256'):
        raise ProjectLocationError('The export manifest changed; preserve the original copy')
    return expected
