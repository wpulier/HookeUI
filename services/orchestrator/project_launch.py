"""Explicit operator configuration for staged portable Project qualification.

The existing factory dependency remains the implementation. No saved document or
request can choose this setting, and existing legacy Projects are never migrated.
"""
from pathlib import Path

from .project_locations import ProjectLocations
from .project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot


def configured_locations(paths, folder, *, reopen=None):
    registry = ProjectRegistry(paths.home)
    if folder is None:
        if reopen is not None:
            raise ProjectLocationError('Reopening a Project folder requires explicit --project-folders configuration')
        if registry.path.exists() or registry.path.is_symlink():
            raise ProjectLocationError('This profile has portable Project locations. Start it with --project-folders (or HOOKE_PROJECTS_DIR); do not fall back to legacy storage.')
        return None
    if not isinstance(folder, str) or not folder.strip():
        raise ProjectLocationError('Choose an absolute parent folder for new Projects')
    parent = Path(folder).expanduser()
    if not parent.is_absolute() or parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ProjectLocationError('Choose an absolute Project parent folder, not a file or link')
    locations = ProjectLocations(paths.sessions, registry, parent)
    if reopen is not None:
        from .day.ownership import namespace, retained_owner
        from .project_repository import verify_owner
        if not isinstance(reopen, str) or not reopen.strip() or not Path(reopen).expanduser().is_absolute():
            raise ProjectLocationError('Choose the absolute location of the registered Project folder')
        root = ProjectRoot.open(Path(reopen))
        with namespace(paths.sessions):
            verify_owner(root)
            old = retained_owner(paths.sessions, root.session)
            if ((old is not None and old['origin']['origin'] != root.origin)
                    or (old is None and (paths.sessions / root.session / 'journal.jsonl').exists())):
                raise ProjectLocationError('A different or unverified legacy Project uses this handle; neither was changed')
            registry.register(root, replace_inactive=True, require_known=True)
    locations.handles()  # fail early on an unreadable registry, without replacing it
    return locations
