"""Owned measurement and export actions on captured scientific revisions."""
import asyncio
import re
import threading
from contextlib import suppress
from fastapi import Request
from fastapi.responses import JSONResponse, Response, StreamingResponse
from . import scientific_resource_actions as actions
from . import scientific_measurement as measurement
from . import population_native
from . import scientific_artifacts as artifacts
from . import scientific_families as families
from . import scientific_tables as tables
from .capture import captures, Refused as MemoryRefused
from .scientific_file_api import watch_disconnect, HEADERS
from .scientific_project_api import _body, _owned, _wire, _error, Refused
from .scientific_projects import InvalidProject


def _name(input):
    """Two arrays of one revision must not download under the same name."""
    return re.sub(r'[^A-Za-z0-9._-]+','-',input if type(input) is str else '').strip('.-')[:60]


class NativeExportResponse(StreamingResponse):
    def __init__(self,stream,room,revision,input=''):
        self.stream,self.room=stream,room
        name=_name(input)
        async def content():
            while True:
                chunk=await _owned(stream.read,1 << 20)
                if not chunk:break
                yield chunk
        super().__init__(content(),media_type='application/zip',headers={**HEADERS,
            'Content-Disposition':f'attachment; filename="scientific-array-revision-{revision}{"-"+name if name else ""}.zip"'})

    async def __call__(self,scope,receive,send):
        try:await super().__call__(scope,receive,send)
        finally:
            try:self.stream.close()
            finally:self.room.release()


async def _iterate(chunks):
    while True:
        chunk=await _owned(next,chunks,None)
        if chunk is None:break
        yield chunk


def register(app,built,studio_origin,header):
    store=built.hub.resources.projects
    from . import method_api
    method_api.register(app,built,studio_origin,header)

    @app.get('/api/populations/{population}/native-labels')
    async def labels_readiness(population: str, session: str, revision: int):
        try:
            return JSONResponse(population_native.describe(built.hub, built.day, population_native.OPERATION,
                {'id': population, 'session': session, 'revision': revision}), headers=HEADERS)
        except (ValueError, OSError) as error:
            return _error(error)

    @app.post('/api/populations/retain-labels')
    async def retain_labels(request: Request):
        stopping = threading.Event()
        watcher = None
        try:
            if request.headers.get(header) != 'retain-population-labels' or not studio_origin(request.headers.get('origin'), request.url.port):
                raise Refused('Retain labels from the population controls.', 403, next='return to its saved correction controls')
            body = await _body(request)
            if set(body) != {'operation', 'session', 'id', 'revision', 'request'}:
                raise InvalidProject('Choose the declared operation and exact saved population revision')
            watcher = asyncio.create_task(watch_disconnect(request, stopping))
            result = await population_native.run(built.hub, built.day, body['operation'],
                {k: body[k] for k in ('session', 'id', 'revision', 'request')}, caller='studio', stopping=stopping)
            saved = result.pop('project')
            return JSONResponse({'ok': True, **result, 'project': _wire(saved)}, headers=HEADERS)
        except (ValueError, OSError, MemoryRefused) as error:
            return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):
                    await watcher

    @app.get('/api/scientific-projects/{project}/arrays')
    async def arrays(project:str,session:str,revision:int):
        try:return JSONResponse({'ok':True,'operation':measurement.descriptor(),**await _owned(actions.inventory,store,project,session,revision)},headers=HEADERS)
        except (ValueError,OSError) as error:return _error(error)

    @app.post('/api/scientific-projects/{project}/measure')
    async def measure(project:str,request:Request):
        stopping=threading.Event();watcher=None
        try:
            if request.headers.get(header)!='measure-scientific-objects' or not studio_origin(request.headers.get('origin'),request.url.port):
                raise Refused('Measure from the saved scientific window.',403,next='return to its saved data controls')
            body=await _body(request)
            if (set(body)!={'operation','session','revision','input'} or type(body['session']) is not str
                    or type(body['revision']) is not int or body['revision']<1 or type(body['input']) is not str):
                raise InvalidProject('Choose an exact saved revision and labels input')
            if body['session']!=built.day.sid:
                raise Refused('The active Project changed.',409,next='return to the original Project before measuring')
            watcher=asyncio.create_task(watch_disconnect(request,stopping))
            result=await measurement.run(built.hub,built.day,body['operation'],
                {'project':project,**{k:body[k] for k in ('session','revision','input')}},caller='studio',stopping=stopping)
            saved=result.pop('project')
            return JSONResponse({'ok':True,**result,'project':_wire(saved),'sessionChanged':saved.session!=built.day.sid},headers=HEADERS)
        except (ValueError,OSError,MemoryRefused) as error:return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher

    def public(table):
        return {'name':table.name,**{k:v for k,v in table.schema.items() if k!='columns'},
                'columns':[{k:v for k,v in column.items() if k!='component'} for column in table.schema['columns']]}

    @app.get('/api/scientific-projects/{project}/tables')
    async def table_list(project:str,session:str,revision:int):
        def read():
            saved=store.read(project,session,revision=revision)
            return [public(tables.open_table(store,project,session,revision,name)) for name in tables.tables(saved.document)]
        try:return JSONResponse({'ok':True,'tables':await _owned(read)},headers=HEADERS)
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/scientific-projects/{project}/tables/{name}')
    async def table_page(project:str,name:str,session:str,revision:int,offset:int=0,limit:int=100):
        def read():
            table=tables.open_table(store,project,session,revision,name)
            return {'table':public(table),'offset':offset,'rows':table.page(offset,limit)}
        try:return JSONResponse({'ok':True,**await _owned(read)},headers=HEADERS)
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/scientific-projects/{project}/resources')
    async def resource_inventory(project:str,session:str,revision:int):
        try:return JSONResponse({'ok':True,**await _owned(families.inventory,store,project,session,revision)},headers=HEADERS)
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/scientific-projects/{project}/artifacts/{name}')
    async def artifact(project:str,name:str,session:str,revision:int):
        try:
            opened=await _owned(artifacts.open_artifact,store,project,session,revision,name)
            # Served as bytes to save, never as the declared type for the browser to render or run.
            return StreamingResponse(_iterate(opened.chunks()),media_type='application/octet-stream',headers={**HEADERS,
                'Content-Disposition':f'attachment; filename="{_name(opened.schema["file_name"]) or name}"',
                'X-Content-SHA256':opened.schema['sha256']})
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/scientific-projects/{project}/measurements.csv')
    async def csv(project:str,session:str,revision:int):
        try:
            value=await _owned(actions.table,store,project,session,revision)
            name=_name(value['source'].get('input'))
            return Response(actions.table_csv(value),media_type='text/csv',headers={**HEADERS,
                'Content-Disposition':f'attachment; filename="objects-revision-{revision}{"-"+name if name else ""}.csv"'})
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/scientific-projects/{project}/native-export')
    async def export(project:str,session:str,revision:int,input:str,request:Request):
        stopping=threading.Event();watcher=None;room=None;stream=None
        def check():
            if stopping.is_set():raise InvalidProject('Export stopped; the saved array remains intact')
        try:
            room=captures(built.hub).memory.reserve('analysis',actions.WORKING_BYTES,'exporting native data')
            watcher=asyncio.create_task(watch_disconnect(request,stopping))
            stream=await _owned(actions.export_array,store,project,session,revision,input,check=check,on_cancel=stopping.set)
            watcher.cancel()
            with suppress(asyncio.CancelledError):await watcher
            watcher=None
            response=NativeExportResponse(stream,room,revision,input)
            stream=room=None
            return response
        except (ValueError,OSError,MemoryRefused) as error:return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher
            if stream is not None:stream.close()
            if room is not None:room.release()
