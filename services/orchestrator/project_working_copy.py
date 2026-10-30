"""Explicit working restoration of an owned, verified export; never imports authority."""
from pathlib import Path
import re
import stat

from .project_export import REASON, reveal_path
from .project_folder_copy import HEADROOM, MAX_MANIFEST, restore_snapshot, verify_snapshot
from .project_roots import ProjectLocationError, ProjectRoot
from .scientific_projects import ProjectStore


def prepare(day, session, export_receipt, *, check=lambda: None):
    check()
    source = reveal_path(day, session, export_receipt)
    verified = verify_snapshot(source, check=check)
    # Verify the retained receipt again after reading the full source.
    if reveal_path(day, session, export_receipt) != source:
        raise ProjectLocationError('The exported copy changed during preparation')
    return {'files': verified['files'], 'bytes': verified['bytes'],
            'requiredFreeBytes': verified['bytes'] + MAX_MANIFEST + HEADROOM,
            'collection': {'state': 'unverified', 'reason': REASON}}


def restore(day, session, export_receipt, operation, *, check=lambda: None):
    if not isinstance(operation, str) or not re.fullmatch('op_[a-f0-9]{16,32}', operation):
        raise ProjectLocationError('Restoration needs its admitted operation identity')
    plan = prepare(day, session, export_receipt, check=check)
    source = reveal_path(day, session, export_receipt)
    locations = day.locations
    owner = locations.project(session)
    parent = Path(locations.parent) / 'Restored'
    if parent.is_symlink():
        raise ProjectLocationError('The restoration folder is a link; use a regular Projects parent')
    parent.mkdir(exist_ok=True)
    if not stat.S_ISDIR(parent.stat(follow_symlinks=False).st_mode):
        raise ProjectLocationError('The restoration destination is not a regular folder')
    ProjectStore._sync_directory(parent.parent)
    destination = parent / f'{owner.origin}—{operation}'
    def current():
        check()
        if locations.project(session) != owner:
            raise ProjectLocationError('The owning Project changed during restoration')
    # restore_snapshot reserves a fresh destination and leaves an incomplete
    # marker on failure. No existing destination is reused or overwritten.
    restore_snapshot(source, destination, check=current,
        expected_manifest_sha256=export_receipt['manifestSha256'])
    return {'session': session, 'origin': owner.origin, 'operation': operation,
            'location': str(destination), 'files': plan['files'], 'bytes': plan['bytes'], 'sourceManifestSha256': export_receipt['manifestSha256'],
            'collection': {'state': 'unverified', 'reason': REASON}}


def reveal(day, session, receipt):
    locations = getattr(day, 'locations', None)
    owner = locations.project(session) if locations is not None else None
    operation = receipt.get('operation')
    if (owner is None or receipt.get('session') != session or receipt.get('origin') != owner.origin
            or not isinstance(operation, str) or not re.fullmatch('op_[a-f0-9]{16,32}', operation)):
        raise ProjectLocationError('This working copy does not belong to the selected Project')
    parent = Path(locations.parent) / 'Restored'
    expected = parent / f'{owner.origin}—{operation}'
    if parent.is_symlink() or str(expected) != receipt.get('location'):
        raise ProjectLocationError('The working-copy location changed')
    copied = ProjectRoot.open(expected)
    if copied.origin != owner.origin or copied.session != session:
        raise ProjectLocationError('The working-copy identity changed')
    return expected
