"""Explicit operator configuration for staged portable Project qualification.

The existing factory dependency remains the implementation. No saved document or
request can choose this setting, and existing legacy Projects are never migrated.
"""
from pathlib import Path

from .project_locations import ProjectLocations
from .project_roots import ProjectLocationError, ProjectRegistry, ProjectRoot


def configured_locations(paths, folder, *, reopen=None, opening=None):
    if reopen is not None and opening is not None:
        raise ProjectLocationError('Choose either Open Project or relocation recovery, not both')
    registry = ProjectRegistry(paths.home)
    if folder is None:
        if reopen is not None or opening is not None:
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
    if opening is not None:
        _open_owner_folder(paths, locations, opening)
    locations.handles()  # fail early on an unreadable registry, without replacing it
    return locations


def _open_owner_folder(paths, locations, opening):
    from .day.prefs import Prefs
    root = register_owner_folder(paths, locations, opening)
    Prefs(paths.sessions).point(root.session, strict=True)


def register_owner_folder(paths, locations, opening):
    """Register an owner-selected working folder without navigation or agent access."""
    from .day.ownership import namespace, retained_owner
    from .door.projects import ProjectAccess
    from .project_repository import verify_owner
    if locations is None:
        raise ProjectLocationError("Project folders are not enabled for this host")
    if not isinstance(opening, str) or not opening.strip() or not Path(opening).expanduser().is_absolute():
        raise ProjectLocationError('Choose the absolute location of the Project folder')
    root = ProjectRoot.open(Path(opening))
    with namespace(paths.sessions):
        locations._check()
        verify_owner(root)
        entries = locations.registry.locations()
        prior = entries.get(root.origin)
        if prior is not None:
            # No implicit relocation, re-registration, or access reset for a known origin.
            if Path(prior['path']) != root.path or locations.registry.locate(root.origin) != root:
                raise ProjectLocationError('This Project is known at another location. Choose explicit inactive-location recovery or open read-only.')
            return root
        if root.session in locations.handles():
            raise ProjectLocationError('Another Project uses this host handle. Neither Project was changed; open in a separate profile.')
        if ((paths.sessions / root.session / 'journal.jsonl').exists()
                or (paths.sessions / root.session / 'journal.jsonl').is_symlink()
                or retained_owner(paths.sessions, root.session) is not None):
            raise ProjectLocationError('A legacy Project already uses this host handle. Neither Project was changed.')
        if root.session in ProjectAccess(paths.home).rows():
            raise ProjectLocationError('Local agent permissions already use this host handle. Import cannot inherit them; use a separate profile.')
        locations.registry.register(root, verify_history=True, publication_guard=locations.registration_publication)
        return root
