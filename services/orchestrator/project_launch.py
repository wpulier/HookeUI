"""Explicit operator configuration for staged portable Project qualification.

The existing factory dependency remains the implementation. No saved document or
request can choose this setting, and existing legacy Projects are never migrated.
"""
from pathlib import Path

from .project_locations import ProjectLocations
from .project_roots import ProjectLocationError, ProjectRegistry


def configured_locations(paths, folder):
    registry = ProjectRegistry(paths.home)
    if folder is None:
        if registry.path.exists() or registry.path.is_symlink():
            raise ProjectLocationError('This profile has portable Project locations. Start it with --project-folders (or HOOKE_PROJECTS_DIR); do not fall back to legacy storage.')
        return None
    if not isinstance(folder, str) or not folder.strip():
        raise ProjectLocationError('Choose an absolute parent folder for new Projects')
    parent = Path(folder).expanduser()
    if not parent.is_absolute() or parent.is_symlink() or (parent.exists() and not parent.is_dir()):
        raise ProjectLocationError('Choose an absolute Project parent folder, not a file or link')
    locations = ProjectLocations(paths.sessions, registry, parent)
    locations.handles()  # fail early on an unreadable registry, without replacing it
    return locations
