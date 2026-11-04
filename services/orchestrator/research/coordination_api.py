"""Shared coordination operations exposed by owner UI and authorized agent door."""
import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse
from .coordination import Coordination
from .studies import service
from ..scientific_project_api import _body, _error, _owned, Refused
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
    expected = methods.get(action, (None, set()))[1]
    if action == 'complete' and 'comparison' in fields:
        expected = expected | {'comparison'}
    if action not in methods or set(fields) != expected:
        raise InvalidProject('Use the declared research coordination action and fields')
    return getattr(coordination, methods[action][0])(study, session, actor=actor, label=label, **fields)


async def execute_command(built, study, session, action, fields, actor, label):
    if actor.startswith('agent:') and action == 'plan':
        from ..door.projects import access
        plan = fields.get('plan')
        pair = plan.get('comparison') if isinstance(plan, dict) else None
        if isinstance(pair, dict):
            for role in ('baseline', 'challenger'):
                ref = pair.get(role)
                if isinstance(ref, dict) and isinstance(ref.get('id'), str):
                    await _owned(access(built).require_workflow, session, ref['id'])
    if action == 'read-pair-scores':
        from .method_pair import verify_report_files
        from ..door.schema import check
        if set(fields) != {'baseline','challenger'}:
            raise InvalidProject('Select both predictions and their scores')
        for arm in fields.values():
            if type(arm) is not dict or set(arm) != {'prediction','score'}:
                raise InvalidProject('Select one prediction and one scoring report per method')
            for ref in arm.values():
                check(ref,'artifact-reference')
        return {'metrics': await _owned(verify_report_files, service(built).store, session, fields)}
    if action in ('check-comparison', 'queue-comparison'):
        if set(fields) != {'basis'}:
            raise InvalidProject('Choose the reviewed comparison')
        from .method_pair import preflight
        from .efforts import fingerprint
        studies = service(built)
        async with studies._locks.setdefault((session, study), asyncio.Lock()):
            plan = await preflight(studies, study, session, fields['basis'], actor)
            if plan['actorMode'] == 'connected-agent':
                from ..door.projects import access
                for role in ('baseline', 'challenger'):
                    from ..door.connections import Refused
                    try:
                        await _owned(access(built).require_workflow, session, plan['comparison'][role]['id'])
                    except Refused as error:
                        raise InvalidProject('The connected agent needs a Project-associated ' + role +
                                             ' workflow. Select one created through this Project’s connector.') from error
            if action == 'check-comparison':
                from .method_pair import describe_methods
                return await _owned(describe_methods, studies, plan, study, session)
            if plan['actorMode'] != 'connected-agent':
                raise InvalidProject('This plan does not designate a connected agent')
            coordination = Coordination(studies)
            key = 'comparison:' + fingerprint(plan)[:32]
            assignment = {'objective': plan['methodNotes'], 'deliverable': plan['desiredOutput'],
                          'uncertainty': plan['evaluation'], 'whyNewWork': 'Establish the reviewed baseline.',
                          'decisionImpact': 'Inspect the baseline diagnostics before justifying one change.',
                          'evidence': plan['materials'], 'dependsOn': []}
            await _owned(coordination.propose, study, session, request=key, assignment=assignment,
                         actor=actor, label=label, shared_request=True)
            return await _owned(coordination.read, study, session)
    # Recovery and scientific admission use the same owner lock, so an absent
    # receipt cannot be declared absent while its validation/admission is in flight.
    if action in ('recover', 'plan', 'review', 'claim', 'complete'):
        studies = service(built)
        lock = studies._locks.setdefault((session, study), asyncio.Lock())
        async with lock:
            return await _owned(execute, built, study, session, action, fields, actor, label)
    return await _owned(execute, built, study, session, action, fields, actor, label)


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
