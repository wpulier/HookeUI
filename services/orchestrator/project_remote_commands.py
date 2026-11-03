"""The owner's Publish, Start from, Update, Back up and Restore commands (ADR 0028). Never exposed through the
agent door.

Publishing is two steps: prepare builds and seals the publication on this Mac and returns its review; publish sends
exactly that, only with the owner's sign-off on the review digest. Start from needs no sign-in for a public
repository or a folder. Update brings newer published revisions into the open Project through the prefix rule.
Back up sends the whole Project, conversations included, to a folder or a private GitHub repository; Restore brings
that same Project back on this Mac (project_backup).
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
                                         licence=msg.get('licence') or DEFAULT_LICENCE,
                                         clean_metadata=msg.get('cleanMetadata') is not False)
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
                                                                'bulkFiles', 'bulkObjects', 'uploadedBytes', 'bytes')},
            'location': receipt['remote']['records'].get('location') or receipt['remote']['records'].get('base')}


@command('project-start-from')
async def project_start_from(app, client, msg) -> dict:
    """Start an independent Project here from a publication: its own identity, the publication's documents held
    read-only, no grants. A public source needs no sign-in. The studio opens it."""
    from .day.journal import Journal
    from .project_repository import session_root
    from .project_roots import ProjectLocationError
    from .project_start_from import start_from
    from .scientific_exchange import BundleRefused
    day = app.day
    locations = getattr(day, 'locations', None)
    if locations is None:
        raise tools.ToolError('Project folders are not enabled here.', next='turn on Project folders, then retry')
    source = msg.get('source')
    destination = source if type(source) is dict else {'kind': 'github', 'repo': str(source or '')}
    name = ' '.join(str(msg.get('name') or 'Started project').split())[:80] or 'Started project'

    def create(avoid):
        return locations.create(name, date=day.date, preferences=day.user_prefs, actor='user', clock=day.clock,
                                local_only=False, avoid=avoid)

    def work():
        remote = _remote_for(destination, anonymous=True)
        root, ancestry, _ = start_from(remote, create, staging=_staging(app, 'start-from', 'fetch'), held=True)
        locations.writable(root.session)
        Journal(root.session, session_root(root)).append(ancestry, durable=True)
        return root, ancestry
    try:
        root, ancestry = await asyncio.to_thread(work)
    except (OSError, ProjectLocationError, BundleRefused) as error:
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
    from .scientific_exchange import BundleRefused
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
        return update_from(root, remote, origin=started[-1]['origin'], staging=_staging(app, root.origin, 'update'),
                           held=True)
    try:
        result = await asyncio.to_thread(work)
    except (OSError, ProjectLocationError, BundleRefused) as error:
        raise _refused(error) from error
    day._record({'type': 'project-updated-from', 'commit': result['commit'],
                 'snapshotSha256': result['snapshotSha256'], 'added': len(result['added']),
                 'extended': len(result['extended']), 'refused': len(result['refused'])}, strict=True)
    return {'ok': True, 'update': result}


def _private_repository(client_, repo, *, restoring=False):
    """A backup holds the Project's conversations: refuse a GitHub repository anyone can read."""
    _, data = client_.ok('GET', f'/repos/{repo}', what='reading the repository')
    if data.get('private') is not True:
        if restoring:
            raise tools.ToolError('This GitHub repository is public, so it holds a publication, not a backup.',
                                  next='use Start from a project for a published project')
        raise tools.ToolError('This GitHub repository is public, and a backup holds your conversations.',
                              next='choose a private repository, or let Hooke make one')


def _backup_remote(source, *, restoring=False):
    """A backup's remote: a folder, or a private GitHub repository read and written signed in."""
    from .project_remote_github import github_remote, repository
    if type(source) is dict and source.get('kind') == 'github':
        client_, repo = _github(), repository(source.get('repo', ''))
        _private_repository(client_, repo, restoring=restoring)
        return github_remote(client_, repo, private=True)
    return _remote_for(source)


@command('project-backup')
async def project_backup(app, client, msg) -> dict:
    """Back up the whole open Project, journals included, to a folder or a private GitHub repository."""
    from .project_backup import back_up, last_backup
    from .project_folder_copy import copy_owned_snapshot
    from .project_remote_github import create_repository, github_remote
    from .project_repository import ProjectRepository
    from .project_roots import ProjectLocationError
    day, locations, root = _project(app, msg.get('session'))
    destination = msg.get('destination')
    if type(destination) is not dict:
        raise tools.ToolError('Choose where to back up.', next='pick a folder or GitHub')
    def send():
        expected = parent = None
        if destination.get('kind') == 'github' and destination.get('create'):
            client_ = _github()
            repo, expected = create_repository(client_, str(destination.get('name') or ''), private=True,
                                               description=f'{root.name}: a private Hooke backup')
            remote = github_remote(client_, repo, placeholder=expected, private=True)
        else:
            remote = _backup_remote(destination)
            parent = last_backup(ProjectRepository(root).events(), remote.describe())
        return back_up(lambda target: copy_owned_snapshot(locations, day.sid, target), remote,
                       staging=_staging(app, root.origin, 'backup'), parent=parent, expected_head=expected,
                       scan_secrets=destination.get('kind') != 'folder')
    try:
        receipt = await asyncio.to_thread(send)
    except (OSError, ProjectLocationError) as error:
        raise _refused(error) from error
    day._record(receipt['event'], strict=True)
    return {'ok': True, 'backup': {k: receipt[k] for k in ('commit', 'snapshotSha256', 'recordFiles', 'bulkFiles',
                                                             'bulkObjects', 'uploadedBytes', 'bytes')},
            'location': receipt['remote']['records'].get('location') or receipt['remote']['records'].get('base')}


@command('project-restore')
async def project_restore(app, client, msg) -> dict:
    """Restore a backed-up Project on this Mac: the same Project, its history and conversations, registered here with
    no agent permissions. A Project this Mac still holds is opened where it is, never restored over."""
    from .day.journal import Journal
    from .project_backup import restore
    from .project_launch import register_owner_folder
    from .project_repository import session_root
    from .project_remote_layers import trailers
    from .project_roots import ProjectLocationError
    from .scientific_exchange import BundleRefused
    locations = getattr(app.day, 'locations', None)
    if locations is None:
        raise tools.ToolError('Project folders are not enabled here.', next='turn on Project folders, then retry')
    source = msg.get('source')

    class Missing(Exception):
        """This Mac knew the Project at a folder that is not there now: lost, or on a drive that is unplugged."""

    def check_place(origin):
        prior = locations.registry.locations().get(origin)
        if prior is None:
            return False
        if Path(prior['path']).exists() or Path(prior['path']).is_symlink():
            raise ProjectLocationError(f'This Mac already has this Project, at {prior["path"]}. Open it there: a '
                                       'backup is restored only where its Project is gone.')
        if msg.get('replaceMissing') is not True:
            raise Missing(prior['path'])  # the owner says which: an unplugged drive looks the same as a lost folder
        return True

    def register(root):
        return register_owner_folder(app.paths, locations, str(root.path), use_location=check_place(root.origin))

    def work():
        remote = _backup_remote(source, restoring=True)
        head = remote.records.head()
        if head is not None:  # refuse before fetching anything when this Mac still has the Project, or may
            check_place(trailers(remote.records.read_commit(head)['message'])[1])
        root, event = restore(remote, locations.parent, register, staging=_staging(app, 'restore', 'fetch'))
        locations.writable(root.session)
        Journal(root.session, session_root(root)).append(event, durable=True)
        return root, event
    try:
        root, event = await asyncio.to_thread(work)
    except Missing as missing:
        return {'ok': False, 'missing': True,
                'error': f'This Mac knew this Project at {missing}, which is not there now. If it is on a drive that '
                         'is unplugged, plug the drive in and open it there.'}
    except (OSError, ProjectLocationError, BundleRefused) as error:
        raise _refused(error) from error
    return {'ok': True, 'project': {'session': root.session, 'origin': root.origin, 'name': root.name,
                                    'location': str(root.path)}, 'restored': event}


@command('project-remote-status')
async def project_remote_status(app, client, msg) -> dict:
    """Where the open Project was published and backed up, and what it was started from, from its own journal."""
    from .project_backup import backups
    from .project_repository import ProjectRepository
    day, locations, root = _project(app, msg.get('session'))
    events = await asyncio.to_thread(lambda: ProjectRepository(root).events())
    return {'ok': True,
            'published': [{k: e.get(k) for k in ('destination', 'commit', 'snapshotSha256', 'licence', 'at')}
                          for e in events if e.get('type') == 'project-published'],
            'startedFrom': next((e for e in reversed(events) if e.get('type') == 'project-started-from'), None),
            'backups': [{k: e.get(k) for k in ('type', 'destination', 'commit', 'snapshotSha256', 'bytes', 'at')}
                        for e in backups(events)]}
