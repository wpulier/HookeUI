"""Local Studio workflow transport. The service owns lifetime and captured Project identity."""
import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse
from .scientific_project_api import _body, _error, Refused
from .scientific_projects import InvalidProject
from .workflows import wire
from .scientific_comparisons import identity
from .workflow_service import service
from .workflow_execution import starter


def input_title(saved):
    """Disambiguate computed files without changing their immutable saved titles."""
    title = saved.document['title']
    operation = saved.document['state'].get('matrixOperation')
    if isinstance(operation, dict) and isinstance(operation.get('capability'), str):
        return f"{title} · {operation['capability']} · {saved.id[-8:]}"
    return title


def register(app,built,studio_origin,header):
    runner=service(built.hub,built.day);w=runner.workflows
    app.state.workflow_service=runner
    def reply(**values):return JSONResponse({'ok':True,**values},headers={'Cache-Control':'no-store'})

    @app.get('/api/workflows')
    async def listing(query:str='',removed:bool=False):
        try:return reply(workflows=await asyncio.to_thread(w.list,query,removed))
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/workflows/starter')
    async def example():return reply(name='Measure bright regions',graph=starter())

    @app.get('/api/workflows/inputs')
    async def inputs(session:str):
        try:
            runner.owner(session)
            def listed():
                from .scientific_recipes import inputs as scenes
                rows=scenes(w.store,session)
                rows.extend({'input':identity(s),'title':input_title(s)} for s in w.store.list(session) if s.document['kind']=='source-file' or 'matrixOperation' in s.document['state'])
                return rows
            return reply(inputs=await asyncio.to_thread(listed))
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/workflows/capabilities')
    async def capabilities():
        try:
            from .matrix_capability_runs import descriptors
            return reply(capabilities=await asyncio.to_thread(descriptors))
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/workflows/{workflow}')
    async def definition(workflow:str,revision:int|None=None):
        try:return reply(workflow=wire(await asyncio.to_thread(w.definition,workflow,revision)))
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/workflow-runs')
    async def runs(session:str):
        try:
            runner.owner(session)
            def listed():return [runner.observed_status(s.id,session) for s in w.store.list(session) if s.document['kind']=='scientific-workflow-run']
            return reply(runs=await asyncio.to_thread(listed))
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/workflow-runs/{run}')
    async def run_status(run:str,session:str):
        try:
            runner.owner(session)
            result=await asyncio.to_thread(runner.observed_status,run,session)
            task=runner.tasks.get((session,run))
            return reply(run=result,processOwnsExecution=task is not None and not task.done())
        except (ValueError,OSError) as error:return _error(error)

    @app.post('/api/workflows/command')
    async def command(request:Request):
        try:
            if not (request.headers.get(header)=='workflow' and studio_origin(request.headers.get('origin'),request.url.port)):
                raise Refused('Use Workflow controls in Studio.',403,next='return to Workflows')
            body=await _body(request)
            return reply(**await runner.command(body,actor='studio'))
        except (ValueError,OSError,KeyError,TypeError) as error:
            return _error(error if isinstance(error,(ValueError,OSError)) else InvalidProject('Choose complete supported workflow fields'))
