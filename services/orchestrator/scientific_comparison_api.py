"""Scoped comparison writes reuse Studio admission and owned scientific work."""
import asyncio
import threading
from contextlib import suppress
from fastapi import Request
from fastapi.responses import JSONResponse
from . import scientific_comparisons as comparisons
from .capture import captures, Refused as MemoryRefused
from .day.sid import is_sid
from .scientific_arrays import SAVE_WORKING_BYTES
from .scientific_file_api import watch_disconnect
from .scientific_project_api import _body, _error, _owned, _wire, Refused
from .scientific_projects import InvalidProject


def register(app, built, studio_origin, header):
    store = built.hub.resources.projects

    @app.get('/api/scientific-projects/{project}/comparison-results')
    async def results(project: str, session: str, revision: int):
        try:
            if not is_sid(session):
                raise InvalidProject('Choose the comparison’s Project')
            rows = await _owned(comparisons.outcomes, store, project, session, revision)
            return JSONResponse({'ok': True, 'results': rows}, headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError) as error:
            return _error(error if isinstance(error, InvalidProject) else Refused(str(error), next='reopen the saved comparison'))

    @app.post('/api/scientific-projects/{project}/comparison')
    async def change(project: str, request: Request):
        stopping = threading.Event(); watcher = room = None
        def check():
            if stopping.is_set():
                raise InvalidProject('Comparison stopped; saved inputs and results remain available')
        try:
            if not (request.headers.get(header) == 'scientific-comparison' and studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('Use the comparison controls in Studio.', 403, next='return to the saved scientific window')
            body = await _body(request)
            if (type(body.get('session')) is not str or not is_sid(body['session'])
                    or type(body.get('revision')) is not int or body['revision'] < 1):
                raise InvalidProject('Choose an exact saved comparison revision')
            session, revision = body['session'], body['revision']
            if session != built.day.sid:
                raise Refused('The active Project changed.', 409, next='return to the comparison’s Project')
            action = body.get('action'); fields = {'session', 'revision', 'action'}
            if action == 'create' and set(body) == fields | {'choices', 'request'}:
                function, args = comparisons.create, (body['choices'], body['request'])
            elif action == 'prepare' and set(body) == fields | {'arm'} and body['arm'] in ('a','b','c'):
                function, args = comparisons.prepare_arm, (body['arm'],)
            elif (action == 'retain' and set(body) == fields | {'arm','result','resultRevision'}
                  and type(body['result']) is str and type(body['resultRevision']) is int and body['resultRevision'] > 0):
                function, args = comparisons.retain, (body['arm'], body['result'], body['resultRevision'])
            elif (action == 'settle' and set(body) == fields | {'arm','outcome','says','request'} and body['arm'] in ('a','b','c')
                  and body['outcome'] in ('failed','cancelled') and type(body['says']) is str and type(body['request']) is str):
                function, args = comparisons.settle, (body['arm'], body['outcome'], body['says'], body['request'])
            elif (action == 'choose' and set(body) == fields | {'arm','result','resultRevision'}
                  and (body['arm'] is None and body['result'] is None and body['resultRevision'] is None
                       or body['arm'] in ('a','b','c') and type(body['result']) is str
                       and type(body['resultRevision']) is int and body['resultRevision'] > 0)):
                function, args = comparisons.choose, (body['arm'], body['result'], body['resultRevision'])
            else:
                raise InvalidProject('Use a supported comparison action')
            room = captures(built.hub).memory.reserve('analysis', SAVE_WORKING_BYTES, 'retaining comparison input')
            watcher = asyncio.create_task(watch_disconnect(request, stopping))
            saved = await _owned(function, store, project, session, revision, *args, check=check, on_cancel=stopping.set)
            return JSONResponse({'ok': True, 'project': _wire(saved), 'sessionChanged': session != built.day.sid}, headers={'Cache-Control': 'no-store'})
        except (ValueError, MemoryRefused, OSError, RuntimeError) as error:
            return _error(error if isinstance(error, (InvalidProject, MemoryRefused, Refused)) else Refused(str(error), next='check integration setup, then reopen the saved comparison'))
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError): await watcher
            if room is not None: room.release()
