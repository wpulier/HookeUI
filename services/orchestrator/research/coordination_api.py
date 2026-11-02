"""Shared coordination operations exposed by owner UI and authorized agent door."""
import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse
from .coordination import Coordination
from .studies import service
from ..scientific_project_api import _body, _error, Refused
from ..scientific_projects import InvalidProject


def execute(built, study, session, action, fields, actor, label):
    coordination = Coordination(service(built))
    methods = {'plan': ('save_plan', {'basis', 'plan'}),
               'review': ('review_plan', {'basis'}),
               'propose': ('propose', {'request', 'assignment'}),
               'propose-capability': ('propose_capability', {'request', 'proposal'}),
               'recover': ('recover', {'assignment'}),
               'claim': ('claim', {'assignment', 'harness', 'model'}),
               'complete': ('complete', {'assignment', 'statement', 'evidence', 'limitations'})}
    if action not in methods or set(fields) != methods[action][1]:
        raise InvalidProject('Use the declared research coordination action and fields')
    return getattr(coordination, methods[action][0])(study, session, actor=actor, label=label, **fields)


async def execute_command(built, study, session, action, fields, actor, label):
    # Recovery and scientific admission use the same owner lock, so an absent
    # receipt cannot be declared absent while its validation/admission is in flight.
    if action in ('recover', 'plan', 'review', 'claim', 'complete'):
        studies = service(built)
        lock = studies._locks.setdefault((session, study), asyncio.Lock())
        async with lock:
            return await asyncio.to_thread(execute, built, study, session, action, fields, actor, label)
    return await asyncio.to_thread(execute, built, study, session, action, fields, actor, label)


def register_studio(app, built, studio_origin, header):
    base = '/api/research-efforts/{study}/coordination'

    @app.get('/api/research-instructions')
    async def instructions():
        from .instructions import packages
        return JSONResponse({'ok': True, 'packages': await asyncio.to_thread(packages)})

    @app.get(base)
    async def read(study: str, session: str):
        try:
            built.day.elsewhere(session)
            result = await asyncio.to_thread(Coordination(service(built)).read, study, session)
            return JSONResponse({'ok': True, **result}, headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError) as error:
            return _error(error)

    @app.post(base + '/{action}')
    async def write(study: str, action: str, request: Request):
        try:
            if (request.headers.get(header) != 'research-effort'
                    or not studio_origin(request.headers.get('origin'), request.url.port)):
                raise Refused('Manage research from its Project.', 403, next='open the Study in its Project to review or update its plan')
            body = await _body(request)
            session = body.pop('session', None)
            if session != built.day.sid:
                raise Refused('The active Project changed.', 409, next='return to the original Project and reopen its Study before saving')
            result = await execute_command(built, study, session, action, body, 'studio', 'You')
            return JSONResponse({'ok': True, 'coordination': result}, headers={'Cache-Control': 'no-store'})
        except (ValueError, OSError) as error:
            return _error(error)
