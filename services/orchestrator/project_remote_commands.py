"""The owner's Publish, Start from and Update commands (ADR 0028). Never exposed through the agent door.

Publishing is two steps: prepare builds and seals the publication on this Mac and returns its review; publish sends
exactly that, only with the owner's sign-off on the review digest. Start from needs no sign-in for a public
repository or a folder. Update brings newer published revisions into the open Project through the prefix rule.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
import re
import uuid

from . import tools
from .commands import command

MAX_LISTED = 300


def _project(app, session):
    day = app.day
    if session != day.sid:
        raise tools.ToolError('The Project changed.', next='open the Project again and retry')
    locations = getattr(day, 'locations', None)
    root = locations.project(session) if locations is not None else None
    if root is None:
        raise tools.ToolError('Only a Project folder can be published or updated.',
                              next='move this Project into a Project folder first')
    return day, locations, root


def _staging(app, origin, kind):
    folder = Path(app.paths.home) / 'project-remotes' / origin
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f'{kind}-{uuid.uuid4().hex[:12]}'


def _github(anonymous=False):
    from .github_rest import Client, signed_in_client
    return Client(anonymous=True) if anonymous else signed_in_client()


def _remote_for(destination, *, anonymous=False):
    from .project_remote_folder import folder_remote
    from .project_remote_github import github_remote, repository
    if type(destination) is not dict:
        raise tools.ToolError('Choose where to publish or start from.', next='pick GitHub or a folder')
    kind = destination.get('kind')
    if kind == 'folder':
        path = destination.get('path')
        if not isinstance(path, str) or not Path(path).expanduser().is_absolute():
            raise tools.ToolError('Choose a folder by its full location.', next='pick the folder again')
        return folder_remote(Path(path).expanduser(), create=bool(destination.get('create')))
    if kind == 'github':
        return github_remote(_github(anonymous), repository(destination.get('repo', '')))
    raise tools.ToolError('Choose GitHub or a folder.', next='pick where again')


def _refused(error):
    return tools.ToolError(str(error), next=None)  # no next: the refusal's own words say what to do


@command('project-publish-prepare')
async def project_publish_prepare(app, client, msg) -> dict:
    """Seal and review a publication of the open Project; nothing leaves the Mac."""
    from .project_folder_copy import copy_owned_snapshot
    from .project_publish import DEFAULT_LICENCE, prepare
    from .project_roots import ProjectLocationError
    day, locations, root = _project(app, msg.get('session'))
    staging = _staging(app, root.origin, 'publication')
    def seal(destination):
        return copy_owned_snapshot(locations, day.sid, destination)
    try:
        review = await asyncio.to_thread(prepare, seal, staging,
                                         include_transcripts=msg.get('includeTranscripts') is True,
                                         licence=msg.get('licence') or DEFAULT_LICENCE)
    except (OSError, ProjectLocationError) as error:
        raise _refused(error) from error
    members = review.pop('members')
    return {'ok': True, 'review': {**review, 'members': members[:MAX_LISTED], 'memberCount': len(members),
                                   'staging': Path(review['staging']).name}}


@command('project-publish')
async def project_publish(app, client, msg) -> dict:
    """Send the reviewed publication, with the owner's sign-off on its review digest."""
    from .project_publish import last_publication, publish
    from .project_remote_github import create_repository, github_remote
    from .project_repository import ProjectRepository
    from .project_roots import ProjectLocationError
    day, locations, root = _project(app, msg.get('session'))
    name = msg.get('staging')
    if not isinstance(name, str) or not re.fullmatch(r'publication-[0-9a-f]{12}', name):
        raise tools.ToolError('Review the publication first.', next='prepare it again')
    staging = Path(app.paths.home) / 'project-remotes' / root.origin / name
    destination = msg.get('destination') or {}
    def send():
        replaces = None
        if destination.get('kind') == 'github' and destination.get('create'):
            client_ = _github()
            repo, replaces = create_repository(client_, str(destination.get('name') or ''), private=False,
                                               description=f'{root.name}: a Hooke research project')
            remote = github_remote(client_, repo, placeholder=replaces)
        else:
            remote = _remote_for(destination)
        parent = None if replaces else last_publication(ProjectRepository(root).events(), remote.describe())
        return publish(staging, review_digest=str(msg.get('reviewDigest') or ''), signoff=msg.get('signoff'),
                       remote=remote, parent=parent, replaces=replaces)
    try:
        receipt = await asyncio.to_thread(send)
    except (OSError, ProjectLocationError) as error:
        raise _refused(error) from error
    day._record(receipt['event'], strict=True)
    return {'ok': True, 'published': {k: receipt[k] for k in ('commit', 'snapshotSha256', 'recordFiles',
                                                                'bulkObjects', 'uploadedBytes', 'bytes')},
            'location': receipt['remote']['records'].get('location') or receipt['remote']['records'].get('base')}


@command('project-start-from')
async def project_start_from(app, client, msg) -> dict:
    """Make a working copy of a published Project here; it inherits no grants. A public source needs no sign-in."""
    from .day.journal import Journal
    from .project_launch import register_owner_folder
    from .project_repository import session_root
    from .project_roots import ProjectLocationError, writer
    from .project_start_from import start_from
    locations = getattr(app.day, 'locations', None)
    if locations is None:
        raise tools.ToolError('Project folders are not enabled here.', next='turn on Project folders, then retry')
    source = msg.get('source')
    destination = source if type(source) is dict else {'kind': 'github', 'repo': str(source or '')}
    def work():
        remote = _remote_for(destination, anonymous=True)
        parent = Path(locations.parent)
        parent.mkdir(parents=True, exist_ok=True)
        label = re.sub(r'[\\/:\x00-\x1f\x7f]', '-', str(msg.get('name') or 'Started project')).strip(' .')[:80]
        target = parent / (label or 'Started project')
        number = 2
        while target.exists():
            target = parent / f'{label} {number}'
            number += 1
        staging = _staging(app, 'start-from', 'fetch')
        working, ancestry = start_from(remote, target, staging=staging)
        with writer(working):
            Journal(working.session, session_root(working)).append(ancestry, durable=True)
        root = register_owner_folder(app.paths, locations, str(working.path))
        return root, ancestry
    try:
        root, ancestry = await asyncio.to_thread(work)
    except (OSError, ProjectLocationError) as error:
        raise _refused(error) from error
    return {'ok': True, 'project': {'session': root.session, 'origin': root.origin, 'name': root.name,
                                    'location': str(root.path)}, 'startedFrom': ancestry}


@command('project-update-from')
async def project_update_from(app, client, msg) -> dict:
    """Bring newer published revisions into the open working copy; diverged documents are refused unchanged."""
    from .project_remote_folder import folder_remote
    from .project_repository import ProjectRepository
    from .project_roots import ProjectLocationError
    from .project_start_from import update_from
    day, locations, root = _project(app, msg.get('session'))
    events = ProjectRepository(root).events()
    started = [e for e in events if e.get('type') == 'project-started-from']
    if not started:
        raise tools.ToolError('This Project was not started from a publication.', next=None)  # no next: there is nothing to update from
    source = started[-1]['source']
    def work():
        locations.writable(day.sid)
        if source.get('kind') == 'github':
            remote = _remote_for({'kind': 'github', 'repo': source.get('location', '')}, anonymous=True)
        else:
            remote = folder_remote(Path(source.get('base') or source.get('location') or ''))
        return update_from(root, remote, staging=_staging(app, root.origin, 'update'), held=True)
    try:
        result = await asyncio.to_thread(work)
    except (OSError, ProjectLocationError) as error:
        raise _refused(error) from error
    day._record({'type': 'project-updated-from', 'commit': result['commit'],
                 'snapshotSha256': result['snapshotSha256'], 'added': len(result['added']),
                 'extended': len(result['extended']), 'refused': len(result['refused'])}, strict=True)
    return {'ok': True, 'update': result}


@command('project-remote-status')
async def project_remote_status(app, client, msg) -> dict:
    """Where the open Project was published, and what it was started from, from its own journal."""
    from .project_repository import ProjectRepository
    day, locations, root = _project(app, msg.get('session'))
    events = await asyncio.to_thread(lambda: ProjectRepository(root).events())
    return {'ok': True,
            'published': [{k: e.get(k) for k in ('destination', 'commit', 'snapshotSha256', 'licence', 'at')}
                          for e in events if e.get('type') == 'project-published'],
            'startedFrom': next((e for e in reversed(events) if e.get('type') == 'project-started-from'), None)}
