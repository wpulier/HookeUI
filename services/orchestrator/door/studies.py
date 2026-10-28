"""Authenticated Study transport; all behavior lives in the shared Study service."""
import asyncio

from fastapi import Request

from .connections import Refused
from .projects import access
from .. import agent_runnable
from ..research.studies import service
from ..research.efforts import summary
from ..scientific_projects import Conflict


def register(app, built, connected, body, reply, refusal):
    prefix = '/api/agent/v1'

    def authorized(request, session, write=False, resource='study'):
        caller = connected(request)
        action = resource + ('.write' if write else '.read')
        if not agent_runnable.door_resource(action):
            raise Refused(403, 'action-not-admitted', 'Study access is unavailable.', next='discover available tools')
        access(built).require(session, caller.actor, write=write)
        return caller

    def failure(error):
        if isinstance(error, Refused):
            return refusal(error)
        return refusal(Refused(409 if isinstance(error, Conflict) else 400, 'research-refused', str(error),
                              next='read the Study and exact input revisions before retrying'))

    @app.get(prefix + '/artifacts/{artifact}/bytes')
    async def download(artifact: str, request: Request, session: str, revision: int):
        from fastapi.responses import StreamingResponse
        from ..scientific_files import CHUNK_BYTES
        try:
            caller = authorized(request, session, resource='artifact')
            store = built.hub.resources.projects
            saved = await asyncio.to_thread(store.read, artifact, session, revision=revision)
            file = saved.document.get('state', {}).get('sourceFile')
            if not file:
                raise ValueError('Select a retained file output')
            source = await asyncio.to_thread(store.file_source, artifact, session, revision, file['reference'])
            async def chunks():
                for offset in range(0, source.size, CHUNK_BYTES):
                    connected(request)
                    access(built).require(session, caller.actor)
                    yield await asyncio.to_thread(source.read, offset, min(CHUNK_BYTES, source.size-offset))
            return StreamingResponse(chunks(), media_type='application/octet-stream', headers={
                'Content-Length': str(source.size), 'ETag': '"' + source.sha256 + '"', 'Cache-Control':'no-store'})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/studies')
    async def listing(request: Request, session: str):
        try:
            authorized(request, session)
            rows = await asyncio.to_thread(service(built).list, session)
            return reply({'ok': True, 'studies': rows})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies')
    async def create(request: Request):
        try:
            caller = connected(request)
            b = await body(request)
            if set(b) != {'session', 'requestKey', 'title', 'objective', 'criteria', 'budgetUsd', 'inputs'}:
                raise ValueError('Use the declared Study creation fields')
            authorized(request, b['session'], True)
            saved = await asyncio.to_thread(service(built).create, b['session'], actor=caller.actor,
                label=caller.label, request=b['requestKey'], title=b['title'], objective=b['objective'],
                criteria=b['criteria'], budget_usd=b['budgetUsd'], inputs=b['inputs'])
            return reply({'ok': True, 'study': summary(saved)})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/studies/{study}')
    async def read(study: str, request: Request, session: str):
        try:
            authorized(request, session)
            return reply({'ok': True, **await service(built).evidence(study, session)})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies/{study}/evidence')
    async def evidence(study: str, request: Request):
        try:
            caller = connected(request)
            b = await body(request)
            if set(b) != {'session', 'reference'}:
                raise ValueError('Select one exact retained evidence reference')
            authorized(request, b['session'])
            result = await asyncio.to_thread(service(built).evidence_read, study, b['session'], b['reference'],
                                            actor=caller.actor, label=caller.label)
            return reply({'ok': True, **result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies/{study}/claims')
    async def claim(study: str, request: Request):
        try:
            caller = connected(request)
            b = await body(request)
            if set(b) != {'session', 'requestKey', 'statement', 'evidence', 'dependsOn', 'limitations'}:
                raise ValueError('A claim needs its statement, evidence, dependencies and limitations')
            authorized(request, b['session'], True)
            result = await asyncio.to_thread(service(built).publish, study, b['session'], request=b['requestKey'],
                statement=b['statement'], evidence=b['evidence'], depends_on=b['dependsOn'], limitations=b['limitations'],
                actor=caller.actor, label=caller.label)
            return reply({'ok': True, 'study': result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies/{study}/challenges')
    async def challenge(study: str, request: Request):
        try:
            caller = connected(request)
            b = await body(request)
            if set(b) != {'session', 'requestKey', 'claim', 'reason', 'evidence'}:
                raise ValueError('A challenge needs its claim, reason and exact evidence')
            authorized(request, b['session'], True)
            result = await asyncio.to_thread(service(built).challenge, study, b['session'], request=b['requestKey'],
                claim=b['claim'], reason=b['reason'], evidence=b['evidence'], actor=caller.actor, label=caller.label)
            return reply({'ok': True, 'study': result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies/{study}/control')
    async def control(study: str, request: Request):
        try:
            caller = connected(request)
            b = await body(request)
            if set(b) != {'session', 'action'}:
                raise ValueError('Choose a Study control')
            authorized(request, b['session'], True)
            result = await service(built).control(study, b['session'], b['action'], actor=caller.actor)
            return reply({'ok': True, **result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/workflows')
    async def workflows(request: Request, session: str):
        try:
            authorized(request, session, resource='workflow')
            rows = await asyncio.to_thread(service(built).workflows.workflows.list)
            allowed = access(built).rows()[session].get('workflows', [])
            return reply({'ok': True, 'workflows': [r for r in rows if r['id'] in allowed]})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/workflows/capabilities')
    async def workflow_capabilities(request: Request):
        try:
            connected(request)
            if not agent_runnable.door_resource('workflow.read'):
                raise ValueError('Workflow discovery is not admitted')
            from ..matrix_capability_runs import descriptors
            from .schema import check
            rows = await asyncio.to_thread(descriptors)
            for row in rows:
                check(row, 'workflow-capability')
            return reply({'ok': True, 'capabilities': rows})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/workflows/{workflow}')
    async def workflow_definition(workflow: str, request: Request, session: str, revision: int | None = None):
        try:
            authorized(request, session, resource='workflow')
            access(built).require_workflow(session, workflow)
            from ..workflows import wire
            saved = await asyncio.to_thread(service(built).workflows.workflows.definition, workflow, revision)
            return reply({'ok': True, 'workflow': wire(saved)})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/workflows/command')
    async def command(request: Request):
        try:
            caller = connected(request)
            b = await body(request)
            session = b.pop('project', None)
            action = b.get('action')
            authorized(request, session, action not in ('validate', 'check'), resource='workflow')
            if action not in ('create', 'duplicate', 'validate', 'pause', 'stop', 'check'):
                raise ValueError('Use a new frozen workflow; execution is admitted through a Study operation. Human review remains in Hooke.')
            if 'session' in b and b['session'] != session:
                raise ValueError('The workflow must belong to the selected Project')
            if 'workflow' in b:
                access(built).require_workflow(session, b['workflow'])
            if action in ('create', 'duplicate'):
                from ..workflows import request as validate_request
                from ..workflows import token as fingerprint
                validate_request(b.get('request'))
                b['request'] = fingerprint({'project': session, 'actor': caller.actor, 'request': b['request']})[:32]
            result = await service(built).workflows.command(b, actor=caller.actor)
            if action in ('create', 'duplicate'):
                access(built).remember_workflow(session, caller.actor, result['workflow']['id'])
            return reply({'ok': True, **result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)
