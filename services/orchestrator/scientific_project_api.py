"""Retained scientific work: bounded Studio writes and exact-revision reads.

Saving a document establishes neither runnable software nor scientific compatibility. Runtime
and resource references are retained unchanged for later validation by the owning integration.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import zipfile

from fastapi import Request
from fastapi.responses import JSONResponse

from .day.sid import is_sid
from .scientific_projects import Conflict, InvalidProject, MAX_DOCUMENT
from .capture import Refused as MemoryRefused

MAX_REQUEST = MAX_DOCUMENT + 8192


class Refused(ValueError):
    def __init__(self, says, status=400, *, next):
        super().__init__(says)
        self.status = status
        self.next = next


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise Refused('A project field was repeated.', next='send one value for each project field')
        result[key] = value
    return result


async def _body(request):
    data = bytearray()
    async for chunk in request.stream():
        if len(data) + len(chunk) > MAX_REQUEST:
            raise Refused('Project state is too large.', 413, next='keep image data in referenced resources, then save again')
        data.extend(chunk)
    try:
        value = json.loads(data, object_pairs_hook=_pairs)
    except Refused:
        raise
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise Refused('The project request is not valid JSON.', next='send a valid project document from the Studio') from exc
    if not isinstance(value, dict):
        raise Refused('The request must contain one project document.', next='send one project document from the Studio')
    return value


async def _owned(function, *args, on_cancel=None, **kwargs):
    # A disconnected client cannot leave an unowned atomic save running after request cleanup.
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    cancelled = False
    while True:
        try:
            value = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if on_cancel is not None:on_cancel()
            if task.done():
                raise
            cancelled = True
        except BaseException:
            if cancelled:raise asyncio.CancelledError()
            raise
    if cancelled:
        raise asyncio.CancelledError()
    return value


def _wire(item):
    return {'id': item.id, 'session': item.session, 'revision': item.revision,
            'digest': item.digest, 'parent': item.parent, 'at': item.at, 'document': item.document}


def _error(exc):
    from .scientific_import_transaction import ImportPending
    if isinstance(exc, ImportPending):
        status, says, next_step = 503, str(exc), 'retry after import completes; restart Hooke if an import was interrupted'
    elif isinstance(exc, Refused):
        status, says, next_step = exc.status, str(exc), exc.next
    elif isinstance(exc, MemoryRefused):
        status, says, next_step = 503, 'Scientific work could not be saved within its memory allowance.', 'wait for other analyses to finish, then retry this save'
    elif isinstance(exc, Conflict):
        status, says = 409, str(exc)
        next_step = 'reopen the latest saved revision and review your changes before saving again'
    elif isinstance(exc, InvalidProject):
        status, says = 400, str(exc)
        next_step = 'review the project document and its session, then retry'
    else:
        status, says = 503, 'The project could not be saved or read. Reopen its last saved revision before retrying.'
        next_step = 'reopen the last saved revision before retrying'
    return JSONResponse({'ok': False, 'says': says, 'next': next_step}, status_code=status, headers={'Cache-Control': 'no-store'})


def register(app, built, studio_origin, header):
    from .research.effort_api import register as register_efforts
    register_efforts(app, built, studio_origin, header)
    from .scientific_resource_api import register as register_resources
    register_resources(app,built,studio_origin,header)
    from .scientific_file_api import register as register_files
    register_files(app,built,studio_origin,header)
    from .scientific_reader_api import register as register_readers
    register_readers(app,built,studio_origin,header)
    from .scientific_tool_api import register as register_tools
    register_tools(app,built,studio_origin,header)
    from .scientific_comparison_api import register as register_comparisons
    register_comparisons(app,built,studio_origin,header)
    from .scientific_recipe_api import register as register_recipes
    register_recipes(app,built,studio_origin,header)
    from .scientific_movie_api import register as register_movies
    register_movies(app,built,studio_origin,header)
    from .workflow_api import register as register_workflows
    register_workflows(app,built,studio_origin,header)
    store = built.hub.resources.projects

    def save_admitted(project, session, document, expected_revision=None, check=lambda:None):
        from contextlib import nullcontext
        from .capture import captures
        from .scientific_arrays import SAVE_WORKING_BYTES
        refs = document.get('resources') if type(document) is dict else None
        native = type(refs) is list and any(type(ref) is dict and type(ref.get('component')) is str
                                          and ref['component'].startswith(('array.','file.')) for ref in refs)
        room = captures(built.hub).memory.reserve('analysis', SAVE_WORKING_BYTES, 'validating native scientific save') if native else nullcontext()
        from .scientific_provenance import guard
        from .scientific_url_intake import guard as downloaded
        with room:
            if project:
                previous = (store.read(project, session, revision=expected_revision)
                            if type(expected_revision) is int and expected_revision >= 1 else None)
                guard(previous, document)
                downloaded(previous, document)
                return store.save(project, session, document, expected_revision=expected_revision,check=check)
            guard(None, document)
            downloaded(None, document)
            return store.create(session, document)

    async def write(request, project=None):
        stopping=threading.Event()
        def check():
            if stopping.is_set():raise InvalidProject('Scientific save stopped before acceptance; reopen its saved revision')
        try:
            if not (request.headers.get(header) == 'save-project'
                    and studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('This project save did not come from the Studio.', 403, next='save the project from the Studio')
            body = await _body(request)
            fields = {'session', 'document', 'expectedRevision'} if project else {'session', 'document'}
            if set(body) != fields:
                raise Refused('The project request has missing or unsupported fields.', next='send the supported project fields from the Studio')
            session = body['session']
            if not isinstance(session, str) or not is_sid(session) or session != built.day.sid:
                raise Refused('The session changed.', 409, next='reopen the project in its original session before saving')
            # Admission captures the session. A subsequent switch cannot redirect this save.
            if project:
                item = await _owned(save_admitted, project, session, body['document'], body['expectedRevision'],check=check,on_cancel=stopping.set)
            else:
                item = await _owned(save_admitted, None, session, body['document'])
            return JSONResponse({'ok': True, 'project': _wire(item), 'sessionChanged': session != built.day.sid},
                                headers={'Cache-Control': 'no-store'})
        except (Refused, MemoryRefused, InvalidProject, OSError) as exc:
            return _error(exc)

    @app.post('/api/scientific-projects/{project}/organization')
    async def organization(project: str, request: Request):
        from .project_material_edit import organize
        stopping = threading.Event()
        try:
            if not (request.headers.get(header) == 'save-project'
                    and studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('Organize material from the Studio.', 403, next='return to the Project’s Inputs')
            body = await _body(request)
            if set(body) != {'reference', 'role', 'input', 'request', 'expectedRevision'} or not isinstance(body['reference'], dict):
                raise InvalidProject('Choose exact material and its group')
            ref = body['reference']
            session = ref.get('session')
            if ref.get('id') != project or session != built.day.sid:
                raise Refused('The Project changed.', 409, next='reopen this material in its Project')
            def check():
                if stopping.is_set():
                    raise InvalidProject('Organization stopped before publication')
            saved = await _owned(organize, store, session, ref, role=body['role'], selected=body['input'],
                expected_revision=body['expectedRevision'], actor='user', request=body['request'], check=check, on_cancel=stopping.set)
            return JSONResponse({'ok': True, 'project': _wire(saved)}, headers={'Cache-Control':'no-store'})
        except (Refused, InvalidProject, OSError) as exc:
            return _error(exc)

    @app.post('/api/scientific-projects')
    async def create(request: Request):
        return await write(request)

    @app.put('/api/scientific-projects/{project}')
    async def save(project: str, request: Request):
        return await write(request, project)

    @app.get('/api/scientific-projects/{project}/provenance')
    async def provenance(project: str, session: str, revision: int | None = None):
        from .scientific_provenance import resolve
        try:
            if not is_sid(session):
                raise Refused('The project session is not valid.', next='choose the project’s original session')
            found = await _owned(resolve, store, project, session, revision)
            return JSONResponse({'ok': True, 'provenance': found}, headers={'Cache-Control': 'no-store'})
        except (Refused, InvalidProject, OSError) as exc:
            return _error(exc)

    @app.get('/api/scientific-projects/{project}/bundle')
    async def bundle(project: str, session: str, revision: int | None = None):
        """This exact revision with everything that explains it, as exact bytes (scientific_exchange)."""
        import tempfile
        from fastapi.responses import FileResponse
        from starlette.background import BackgroundTask
        from .scientific_exchange import export
        folder = tempfile.mkdtemp(prefix='hooke-bundle-')
        handed_over = False
        try:
            if not is_sid(session):
                raise Refused('The project session is not valid.', next='choose the project’s original session')
            path = os.path.join(folder, 'bundle.zip')
            target = await _owned(export, store, project, session, revision, destination=path, sessions=built.day.journal.root)
            response = FileResponse(path, media_type='application/zip', headers={'Cache-Control': 'no-store'},
                                    filename=f'hooke-{target["id"]}-r{target["revision"]}.zip',
                                    background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True))
            handed_over = True  # the response removes the folder once it has been sent
            return response
        except (Refused, InvalidProject, OSError) as exc:
            return _error(exc)
        finally:
            if not handed_over:
                shutil.rmtree(folder, ignore_errors=True)

    @app.post('/api/scientific-bundles')
    async def import_bundle(request: Request):
        """Add a verified bundle's documents to this profile without overwriting anything."""
        import tempfile
        from .scientific_exchange import MAX_BUNDLE, reimport
        folder = tempfile.mkdtemp(prefix='hooke-import-')
        try:
            if not (request.headers.get(header) == 'import-bundle'
                    and studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('This import did not come from the Studio.', 403, next='import the bundle from the Studio')
            path, used = os.path.join(folder, 'bundle.zip'), 0
            stream = await asyncio.to_thread(open, path, 'wb')
            try:
                async for chunk in request.stream():
                    used += len(chunk)
                    if used > MAX_BUNDLE + (1 << 20):
                        raise Refused('This bundle is too large.', 413, next='export less work together')
                    await asyncio.to_thread(stream.write, chunk)
            finally:
                await asyncio.to_thread(stream.close)
            result = await _owned(reimport, store, path, sessions=built.day.journal.root)
            return JSONResponse({'ok': True, **result}, headers={'Cache-Control': 'no-store'})
        except (Refused, InvalidProject, OSError) as exc:
            return _error(exc)
        except zipfile.BadZipFile:
            return _error(Refused('This is not a Hooke provenance bundle.', next='choose a bundle Hooke exported'))
        finally:
            await asyncio.to_thread(shutil.rmtree, folder, True)

    @app.get('/api/scientific-projects/{project}')
    async def read(project: str, session: str, revision: int | None = None):
        try:
            if not is_sid(session):
                raise Refused('The project session is not valid.', next='choose the project’s original session')
            item = await _owned(store.read, project, session, revision=revision)
            return JSONResponse({'ok': True, 'project': _wire(item)}, headers={'Cache-Control': 'no-store'})
        except (Refused, InvalidProject, OSError) as exc:
            return _error(exc)
