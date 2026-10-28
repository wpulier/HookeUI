"""Local Study documents and an explicit, operator-selected execution extension.

Extensions are trusted installed Python code. No request, saved document, provider
response or account can select one. The standalone core imports no managed backend.
"""
import importlib
import inspect
import os

from fastapi import Request
from fastapi.responses import JSONResponse
from ..scientific_project_api import _body, _owned, _error, Refused
from ..scientific_projects import InvalidProject
from .efforts import Efforts, summary, KIND

HEADERS = {'Cache-Control': 'no-store'}
# Said in the studio too (study-view.ts STUDY_UNAVAILABLE).
UNAVAILABLE = 'Running a Study needs a research service this app does not have. You can still write, save and reopen a Study.'


class LocalLifecycle:
    def start(self):
        pass

    async def stop(self):
        pass


def register(app, built, studio_origin, header):
    extension = os.environ.get('HOOKEUI_RESEARCH_EXTENSION', '').strip()
    if extension:
        # Invalid configuration fails at startup, never silently falls back.
        importlib.import_module(extension).register(app, built, studio_origin, header)
        lifecycle = getattr(app.state, 'research_lifecycle', None)
        start = getattr(lifecycle, 'start', None)
        stop = getattr(lifecycle, 'stop', None)
        if (not callable(start) or inspect.iscoroutinefunction(start)
                or not inspect.iscoroutinefunction(stop)):
            raise RuntimeError('Research extension lifecycle requires synchronous start() and asynchronous stop()')
        return
    app.state.research_lifecycle = LocalLifecycle()
    service = Efforts(built.hub.resources.projects)

    async def admitted(request, fields):
        if (request.headers.get(header) != 'research-effort'
                or not studio_origin(request.headers.get('origin'), request.url.port)):
            raise Refused('Manage research from its Project.', 403, next='return to the Project research controls')
        body = await _body(request)
        if set(body) != fields:
            raise InvalidProject('Use the declared research fields')
        if body['session'] != built.day.sid:
            raise Refused('The active Project changed.', 409, next='return to the original Project')
        return body

    @app.get('/api/research-readiness')
    async def readiness(session: str, effort: str | None = None):
        try:
            built.day.elsewhere(session)
            if effort:
                await _owned(service.read, effort, session)
            return JSONResponse({'ok': True, 'providers': [], 'executionAvailable': False,
                                 'reason': UNAVAILABLE}, headers=HEADERS)
        except (ValueError, OSError, KeyError) as error:
            return _error(error)

    @app.get('/api/research-inputs')
    async def inputs(session: str):
        try:
            built.day.elsewhere(session)
            def listed():
                return [{'id': s.id, 'revision': s.revision, 'digest': s.digest,
                         'title': s.document['title'], 'kind': s.document['kind'],
                         'managedFileEligible': False, 'scientificInputEligible': False,
                         'limitation': UNAVAILABLE, 'scientificLimitation': UNAVAILABLE}
                        for s in service.store.list(session) if s.document['kind'] != KIND]
            return JSONResponse({'ok': True, 'inputs': await _owned(listed)}, headers=HEADERS)
        except (ValueError, OSError, KeyError) as error:
            return _error(error)

    @app.get('/api/research-efforts')
    async def list_efforts(session: str):
        try:
            built.day.elsewhere(session)
            return JSONResponse({'ok': True, 'efforts': await _owned(service.list, session)}, headers=HEADERS)
        except (ValueError, OSError, KeyError) as error:
            return _error(error)

    @app.get('/api/research-efforts/{effort}')
    async def read_effort(effort: str, session: str):
        try:
            return JSONResponse({'ok': True, 'effort': summary(await _owned(service.read, effort, session))}, headers=HEADERS)
        except (ValueError, OSError) as error:
            return _error(error)

    @app.post('/api/research-efforts')
    async def create_effort(request: Request):
        try:
            body = await admitted(request, {'session', 'request', 'title', 'objective', 'criteria', 'budgetUsd', 'inputs'})
            saved = await _owned(service.create, body['session'], request=body['request'], title=body['title'],
                                 objective=body['objective'], criteria=body['criteria'],
                                 budget_usd=body['budgetUsd'], inputs=body['inputs'])
            return JSONResponse({'ok': True, 'effort': summary(saved),
                                 'sessionChanged': saved.session != built.day.sid}, headers=HEADERS)
        except (ValueError, OSError) as error:
            return _error(error)

    @app.post('/api/research-efforts/{effort}/control')
    async def control_effort(effort: str, request: Request):
        try:
            body = await admitted(request, {'session', 'action'})
            # Existing external work remains cancel_requested, never falsely settled.
            saved = await _owned(service.control, effort, body['session'], body['action'])
            return JSONResponse({'ok': True, 'effort': summary(saved)}, headers=HEADERS)
        except (ValueError, OSError) as error:
            return _error(error)

    @app.post('/api/research-efforts/{effort}/findings')
    async def retain_finding(effort: str, request: Request):
        try:
            body = await admitted(request, {'session', 'request', 'statement', 'tasks'})
            saved = await _owned(service.finding, effort, body['session'], request=body['request'],
                                 statement=body['statement'], tasks=body['tasks'])
            return JSONResponse({'ok': True, 'effort': summary(saved)}, headers=HEADERS)
        except (ValueError, OSError) as error:
            return _error(error)

    @app.api_route('/api/research-efforts/{effort}/{action:path}', methods=['GET', 'POST'])
    async def unavailable(effort: str, action: str):
        return JSONResponse({'ok': False, 'says': UNAVAILABLE,
                             'next': 'Use local tools or configure a trusted execution extension.'},
                            status_code=503, headers=HEADERS)
