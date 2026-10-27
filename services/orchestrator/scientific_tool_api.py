"""Studio selection of an exact prepared tool for an accepted scene revision."""
import asyncio
import threading
from contextlib import suppress
from fastapi import Request
from fastapi.responses import JSONResponse
from . import scientific_tools
from .capture import captures, Refused as MemoryRefused
from .day.sid import is_sid
from .scientific_arrays import SAVE_WORKING_BYTES
from .scientific_file_api import watch_disconnect
from .scientific_project_api import _body, _error, _owned, _wire, Refused
from .scientific_projects import InvalidProject


def register(app, built, studio_origin, header):
    store = built.hub.resources.projects

    @app.get('/api/scientific-projects/{project}/tools')
    async def choices(project: str, session: str, revision: int):
        try:
            if not is_sid(session):
                raise InvalidProject('Choose the saved scene’s Project')
            result = await _owned(scientific_tools.discover, store, project, session, revision)
            return JSONResponse({'ok': True, **result}, headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError) as error:
            return _error(error if isinstance(error, InvalidProject) else Refused(str(error), next='reopen the scene and check its integrations in Models'))

    @app.post('/api/scientific-projects/{project}/tool')
    async def choose(project: str, request: Request):
        stopping = threading.Event()
        watcher = room = None
        def check():
            if stopping.is_set():
                raise InvalidProject('Tool selection stopped; reopen the saved revision before retrying')
        try:
            if not (request.headers.get(header) == 'choose-scientific-tool' and studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('Choose this tool from the Studio.', 403, next='return to the saved scientific window')
            body = await _body(request)
            if (set(body) not in ({'session', 'revision', 'capability', 'generation'}, {'session', 'revision', 'capability', 'generation', 'updateTrial'}) or type(body.get('updateTrial',False)) is not bool or type(body['session']) is not str
                    or not is_sid(body['session']) or type(body['revision']) is not int or body['revision'] < 1):
                raise InvalidProject('Choose an exact saved scene revision and declared tool')
            session = body['session']
            if session != built.day.sid:
                raise Refused('The active Project changed.', 409, next='return to the scene’s Project before choosing its tool')
            room = captures(built.hub).memory.reserve('analysis', SAVE_WORKING_BYTES, 'binding selected scientific tool')
            watcher = asyncio.create_task(watch_disconnect(request, stopping))
            saved = await _owned(scientific_tools.bind, store, project, session, body['revision'], body['capability'], body['generation'], update_trial=body.get('updateTrial',False), check=check, on_cancel=stopping.set)
            return JSONResponse({'ok': True, 'project': _wire(saved), 'sessionChanged': session != built.day.sid}, headers={'Cache-Control': 'no-store'})
        except (ValueError, MemoryRefused, OSError, RuntimeError) as error:
            return _error(error if isinstance(error, (InvalidProject, MemoryRefused, Refused)) else Refused(str(error), next='check this integration’s setup in Models, then retry the saved revision'))
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher
            if room is not None:
                room.release()
