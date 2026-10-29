"""Person-facing Study conversation; agents use the authenticated door."""
from fastapi import Request
from fastapi.responses import JSONResponse

from ..scientific_project_api import _body, _owned, _error, Refused
from .study_questions import for_app, recipients, require_recipient


def register(app, built, studio_origin, header):
    def owner(request, session, write=False):
        if (request.headers.get(header) != 'research-effort'
                or (write and not studio_origin(request.headers.get('origin'), request.url.port))):
            raise Refused('Use Study conversation from Hooke.', 403, next='return to the Study')
        built.day.elsewhere(session)
        if write and session != built.day.sid:
            raise Refused('The active Project changed. Return to the original Study.', 409, next='return to the originating Project')

    @app.get('/api/research-efforts/{study}/questions')
    async def listing(study: str, session: str, request: Request):
        try:
            owner(request, session)
            snapshot = await _owned(for_app(built).snapshot, study, session)
            available = await _owned(recipients, built, session)
            return JSONResponse({'ok': True, **snapshot, 'recipients': available}, headers={'Cache-Control':'no-store'})
        except (ValueError, OSError) as error:
            return _error(error)

    @app.post('/api/research-efforts/{study}/questions')
    async def ask(study: str, request: Request):
        try:
            body = await _body(request)
            if set(body) != {'session', 'request', 'question', 'basis', 'recipient'}:
                raise ValueError('Use the declared Study question fields')
            session = body.pop('session')
            owner(request, session, True)
            body['recipient'] = require_recipient(built, session, body['recipient'])
            question = await _owned(for_app(built).ask, study, session, actor='studio', label='You', **body)
            return JSONResponse({'ok': True, 'question': question})
        except (ValueError, OSError) as error:
            return _error(error)
