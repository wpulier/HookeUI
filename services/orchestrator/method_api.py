"""Studio transport for the declared retained-method operation; no caller-supplied pixels."""
import asyncio
import threading
from contextlib import suppress

from fastapi import Request
from fastapi.responses import JSONResponse

from . import method_workflow as methods
from .capture import captures, Refused as MemoryRefused
from .scientific_file_api import HEADERS, watch_disconnect
from .scientific_project_api import _body, _owned, _wire, _error, Refused
from .scientific_projects import InvalidProject


def register(app,built,studio_origin,header):
    store=built.hub.resources.projects

    @app.post('/api/scientific-methods')
    async def comparison(request:Request):
        stop=threading.Event();watcher=None
        try:
            if request.headers.get(header)!='compare-methods' or not studio_origin(request.headers.get('origin'),request.url.port):
                raise Refused('Compare methods from saved scientific Data.',403,next='return to its comparison controls')
            body=await _body(request)
            if set(body)!={'operation','action','selection'}:
                raise InvalidProject('Choose the declared comparison action and exact saved selection')
            watcher=asyncio.create_task(watch_disconnect(request,stop))
            if body['action']=='describe':
                result=await methods.preview(built.hub,built.day,body['operation'],body['selection'],caller='studio',stopping=stop)
            else:
                result=await methods.run(built.hub,built.day,body['operation'],body['action'],body['selection'],caller='studio',stopping=stop)
                result={**result,'project':_wire(result['project'])}
            return JSONResponse({'ok':True,**result},headers=HEADERS)
        except (ValueError,OSError,MemoryRefused) as error:return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher

    @app.get('/api/scientific-methods/{project}')
    async def read(project:str,session:str,revision:int,request:Request):
        stop=threading.Event();watcher=asyncio.create_task(watch_disconnect(request,stop))
        def check():
            if stop.is_set():raise InvalidProject('Comparison read stopped; saved work is unchanged')
        try:
            with captures(built.hub).memory.reserve('analysis',methods.WORKING_BYTES,'reading retained method summary'):
                result=await _owned(methods.read,store,project,session,revision,check=check,on_cancel=stop.set)
            return JSONResponse({'ok':True,'source':{'id':project,'session':session,'revision':revision},**result},headers=HEADERS)
        except (ValueError,OSError,MemoryRefused) as error:return _error(error)
        finally:
            watcher.cancel()
            with suppress(asyncio.CancelledError):await watcher
