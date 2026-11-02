"""Authenticated Study transport; all behavior lives in the shared Study service."""
import asyncio

from fastapi import Request

from .connections import Refused
from .projects import access
from .. import agent_runnable
from ..research.studies import service
from ..research.efforts import summary
from ..scientific_projects import Conflict


def descriptors(build):
    """Describe the existing Study execution entrance, without granting Project access."""
    from .listing import app_generation
    from .schema import load
    if not agent_runnable.door_resource('workflow.write'):
        return []
    return [{'id': 'workflow', 'name': 'Run a Study workflow',
        'task': agent_runnable.DOOR_RESOURCES['workflow.write'],
        'description': 'Run an existing frozen workflow in an authorized Study. Discover definitions with '
            'GET workflows?session=<project> and scientific steps with GET workflows/capabilities. '
            'Project contribute access, exact inputs and step readiness are checked at admission.',
        'inputs': [{'type':'project-evidence','label':'Exact retained Study inputs'}],
        'outputs': [{'type':'workflow-run','label':'Retained workflow run and attempts'}],
        'inputSchema': load('workflow-admission'), 'generation': app_generation(build),
        'state': 'available', 'runsOn':'this Mac', 'cost':'local computation only'}]


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

    @app.get(prefix + '/research-instructions')
    async def research_instructions(request: Request):
        try:
            connected(request)
            if not agent_runnable.door_resource('research.instructions'):
                raise Refused(403, 'action-not-admitted', 'Research instructions are unavailable.', next='discover available tools')
            from ..research.instructions import packages
            return reply({'ok': True, 'packages': await asyncio.to_thread(packages)})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/studies/{study}/coordination')
    async def coordination_read(study: str, request: Request, session: str):
        try:
            authorized(request, session, resource='study-coordination')
            from ..research.coordination import Coordination
            result = await asyncio.to_thread(Coordination(service(built)).read, study, session)
            return reply({'ok': True, **result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies/{study}/coordination/{action}')
    async def coordination_write(study: str, action: str, request: Request):
        try:
            b = await body(request)
            session = b.pop('session', None)
            caller = authorized(request, session, True, resource='study-coordination')
            from ..research.coordination_api import execute
            result = await asyncio.to_thread(execute, built, study, session, action, b, caller.actor, caller.label)
            return reply({'ok': True, 'coordination': result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

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
                raise Refused(400, 'research-refused',
                    'A claim requires exactly session, requestKey, statement, evidence, dependsOn and limitations. '
                    'The saved claim uses request; input uses requestKey. '
                    'Evidence is a list of {artifact: exact reference, relation: supports|challenges|input|result|failure}; '
                    'dependsOn is a list and limitations is text. Do not send basis, by, byLabel or at; '
                    'Hooke records authorship.',
                    next='send requestKey, not request; correct the body and retry the same requestKey')
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

    @app.get(prefix + '/studies/{study}/questions')
    async def questions(study: str, request: Request, session: str):
        try:
            authorized(request, session, resource='study-question')
            from ..research.study_questions import for_app
            rows = await asyncio.to_thread(for_app(built).rows, study, session)
            return reply({'ok': True, 'questions': rows})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.get(prefix + '/studies/{study}/questions/{question}')
    async def question_context(study: str, question: str, request: Request, session: str):
        try:
            authorized(request, session, resource='study-question')
            from ..research.study_questions import for_app
            return reply({'ok': True, **await asyncio.to_thread(for_app(built).context, study, session, question)})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies/{study}/questions')
    async def ask_question(study: str, request: Request):
        try:
            b = await body(request)
            if set(b) - {'materials'} != {'session', 'request', 'question', 'basis', 'recipient'}:
                raise ValueError('Use the declared Study question fields')
            session = b.pop('session')
            caller = authorized(request, session, True, resource='study-question')
            from ..research.study_questions import for_app, require_recipient
            b['recipient'] = require_recipient(built, session, b['recipient'])
            result = await asyncio.to_thread(for_app(built).ask, study, session, actor=caller.actor, label=caller.label, **b)
            return reply({'ok': True, 'question': result})
        except (Refused, ValueError, OSError) as error:
            return failure(error)

    @app.post(prefix + '/studies/{study}/questions/{question}/answer')
    async def answer_question(study: str, question: str, request: Request):
        try:
            b = await body(request)
            if set(b) != {'session', 'statement', 'evidence', 'limitations'}:
                raise ValueError('An answer needs its statement, exact evidence and limitations')
            session = b.pop('session')
            caller = authorized(request, session, True, resource='study-question')
            from ..research.study_questions import for_app
            result = await asyncio.to_thread(for_app(built).answer, study, session, question, actor=caller.actor, label=caller.label, **b)
            return reply({'ok': True, 'question': result})
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
