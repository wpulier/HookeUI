"""Explicit Studio export of a captured saved revision; disconnect cancels owned work."""
import asyncio
import threading
from contextlib import suppress
from fastapi import Request
from fastapi.responses import JSONResponse
from . import scientific_movie
from .capture import captures, Refused as MemoryRefused
from .day.sid import is_sid
from .scientific_file_api import watch_disconnect, HEADERS
from .scientific_project_api import _body, _owned, _wire, _error, Refused
from .scientific_projects import InvalidProject


def register(app, built, studio_origin, header):
    store = built.hub.resources.projects

    @app.post('/api/scientific-projects/{project}/movie')
    async def export(project: str, request: Request):
        stopping = threading.Event()
        watcher = None
        try:
            if not (request.headers.get(header) == 'export-scientific-movie'
                    and studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('Export the saved movie from the Studio.', 403, next='return to its Export section')
            body = await _body(request)
            if (set(body) != {'session', 'revision', 'request', 'settings'}
                    or type(body['session']) is not str or not is_sid(body['session'])
                    or type(body['revision']) is not int or body['revision'] < 1):
                raise InvalidProject('Choose an exact saved tool revision before exporting')
            session = body['session']
            if session != built.day.sid:
                raise Refused('The active Project changed.', 409, next='return to the tool’s Project before exporting')
            with captures(built.hub).memory.reserve('analysis', scientific_movie.WORKING_BYTES, 'exporting saved scientific movie'):
                watcher = asyncio.create_task(watch_disconnect(request, stopping))
                saved = await _owned(scientific_movie.export, store, project, session, body['revision'],
                                     body['request'], body['settings'], stopping=stopping.is_set, on_cancel=stopping.set)
            return JSONResponse({'ok': True, 'project': _wire(saved), 'sessionChanged': session != built.day.sid}, headers=HEADERS)
        except (ValueError, RuntimeError, OSError, MemoryRefused) as error:
            return _error(error if isinstance(error, (InvalidProject, Refused, MemoryRefused)) else
                          Refused(str(error), next='keep the saved tool; retry this export or check Project Data for an accepted result'))
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError): await watcher
