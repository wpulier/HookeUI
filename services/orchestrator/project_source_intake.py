"""Explicit Library-to-Project intake; reopening never follows the original.

The existing owner UI and file-open door call this before choosing a new source.
It is not an outside-agent permission to browse the owner's Library. Byte work
runs on the existing intake implementation with the captured Project and actor.
"""
import asyncio
from dataclasses import asdict
import hashlib
import threading

from .door import actor
from .project_media import MediaReference, ProjectMedia
from .project_roots import ProjectLocationError
from .scientific_file_intake import request_identity
from .scientific_files import CHUNK_BYTES, MAX_CHUNKS


def _copy(app, sid, identity, request, by, stopping):
    locations = getattr(app.day, 'locations', None)
    root = locations.project(sid) if locations is not None else None
    if root is None:
        return {'ok': True, 'storage': 'legacy', 'asset': identity, 'session': sid}
    def check():
        if stopping.is_set() or app._stopping:
            raise ProjectLocationError('Adding this material was stopped')
        if locations.project(sid) != root:
            raise ProjectLocationError('The destination Project changed; reopen it before adding material')
    check()
    media = ProjectMedia(app.hub.resources.projects)
    owned = media.stores.for_session(sid)
    key = hashlib.sha256((by + '\0' + request).encode()).hexdigest()
    document, _ = request_identity(sid, key)
    if owned._folder(document).exists():
        # A lost reply must be recoverable even after the original is deleted.
        saved = owned.read(document, sid, revision=1)
        selected = MediaReference(root.origin, sid, saved.id, saved.revision, saved.digest)
        metadata, _ = media.read(selected)
        if metadata['asset'].get('id') != identity:
            raise ProjectLocationError('This intake request already retained a different source')
    else:
        selected = media.retain_library(sid, app.hub.library, identity, request=key,
            max_bytes=CHUNK_BYTES * MAX_CHUNKS, producer={'actor': by},
            stopping=lambda: stopping.is_set() or app._stopping)
        metadata, _ = media.read(selected)
    check()
    app.day.retain_source(identity, asdict(selected), session=sid, by=by)
    return {'ok': True, 'storage': 'project', 'asset': identity, 'session': sid,
            'retained': asdict(selected), 'title': metadata['asset']['title']}


async def retain(app, sid, identity, request):
    from .day.sid import is_sid
    from .library import is_asset_id
    if not is_sid(sid) or not is_asset_id(identity) or not isinstance(request, str) or not 1 <= len(request) <= 200:
        raise ProjectLocationError('Choose a Project, Library source and intake request')
    stopping = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_copy, app, sid, identity, request,
                                                 actor.current() or 'user', stopping))
    # Consume a late failure after repeated caller cancellation; the worker still
    # owns its intake and its finally cleanup. Stop checks fence final binding.
    task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stopping.set()
        try:
            await asyncio.shield(task)
        except (OSError, ValueError):
            pass
        raise
