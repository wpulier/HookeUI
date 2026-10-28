"""Authenticated Study transport; all behavior lives in the shared Study service."""
import asyncio

from fastapi import Request

from .connections import Refused
from ..research.studies import service
from ..research.efforts import summary
from ..scientific_projects import Conflict


def register(app, built, connected, body, reply, refusal):
    prefix = '/api/agent/v1'

    def failure(error):
        if isinstance(error, Refused):
            return refusal(error)
        return refusal(Refused(409 if isinstance(error, Conflict) else 400, 'research-refused', str(error),
                              next='read the Study and exact input revisions before retrying'))

    @app.get(prefix + '/studies')
    async def listing(request: Request, session: str):
        try:
            connected(request)
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
            saved = await asyncio.to_thread(service(built).create, b['session'], actor=caller.actor,
                label=caller.label, request=b['requestKey'], title=b['title'], objective=b['objective'],
                criteria=b['criteria'], budget_usd=b['budgetUsd'], inputs=b['inputs'])
            return reply({'ok': True, 'study': summary(saved)})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/studies/{study}')
    async def read(study: str, request: Request, session: str):
        try:
            connected(request)
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
            result = await service(built).control(study, b['session'], b['action'], actor=caller.actor)
            return reply({'ok': True, **result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/workflows')
    async def workflows(request: Request):
        try:
            connected(request)
            rows = await asyncio.to_thread(service(built).workflows.workflows.list)
            return reply({'ok': True, 'workflows': rows})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/workflows/capabilities')
    async def workflow_capabilities(request: Request):
        try:
            connected(request)
            from ..matrix_capability_runs import descriptors
            from .schema import check
            rows = await asyncio.to_thread(descriptors)
            for row in rows:
                check(row, 'workflow-capability')
            return reply({'ok': True, 'capabilities': rows})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/workflows/{workflow}')
    async def workflow_definition(workflow: str, request: Request, revision: int | None = None):
        try:
            connected(request)
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
            if b.get('action') in ('start', 'capability'):
                raise ValueError('Admit execution through operations with tool workflow and its Study')
            return reply({'ok': True, **await service(built).workflows.command(b, actor=caller.actor)})
        except (Refused, ValueError, OSError) as error:
            return failure(error)
