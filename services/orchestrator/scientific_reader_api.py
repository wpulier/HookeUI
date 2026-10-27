"""Studio reader choice binds an exact saved source; never silently starts code."""
import asyncio
import threading
from contextlib import suppress
from fastapi import Request
from fastapi.responses import JSONResponse
from . import scientific_readers
from .capture import captures, Refused as MemoryRefused
from .day.sid import is_sid
from .scientific_arrays import SAVE_WORKING_BYTES
from .scientific_file_api import watch_disconnect
from .scientific_project_api import _body, _error, _owned, _wire, Refused
from .scientific_projects import InvalidProject


def register(app,built,studio_origin,header):
    store=built.hub.resources.projects

    @app.get('/api/scientific-files/{project}/readers')
    async def readers(project:str,session:str,revision:int):
        try:
            if not is_sid(session):raise InvalidProject('Choose the saved source’s Project')
            result=await _owned(scientific_readers.discover,store,project,session,revision)
            return JSONResponse({'ok':True,**result},headers={'Cache-Control':'no-store'})
        except (ValueError,OSError) as error:
            return _error(error if isinstance(error,InvalidProject) else Refused(str(error),next='reopen the source and check its integrations in Models'))

    @app.post('/api/scientific-files/{project}/reader')
    async def choose(project:str,request:Request):
        stopping=threading.Event();watcher=None;room=None
        def check():
            if stopping.is_set():raise InvalidProject('Reader selection stopped; reopen the saved source before retrying')
        try:
            if not(request.headers.get(header)=='choose-scientific-reader' and studio_origin(request.headers.get('origin'),request.url.port)):
                raise Refused('Choose this reader from the Studio.',403,next='return to the saved source window')
            body=await _body(request)
            if (set(body)!={'session','revision','capability','generation'} or type(body['session']) is not str
                    or not is_sid(body['session']) or type(body['revision']) is not int or body['revision']<1):
                raise InvalidProject('Choose an exact source revision and declared reader')
            session=body['session']
            if session!=built.day.sid:raise Refused('The active Project changed.',409,next='return to the source’s Project before choosing its reader')
            room=captures(built.hub).memory.reserve('analysis',SAVE_WORKING_BYTES,'binding selected scientific reader')
            watcher=asyncio.create_task(watch_disconnect(request,stopping))
            try:saved=await _owned(scientific_readers.bind,store,project,session,body['revision'],body['capability'],body['generation'],check=check,on_cancel=stopping.set)
            except InvalidProject as error:
                raise Refused(str(error),next='your saved source is unchanged; check readers again and choose one that opens it exactly') from error
            return JSONResponse({'ok':True,'project':_wire(saved),'sessionChanged':session!=built.day.sid},headers={'Cache-Control':'no-store'})
        except (ValueError,MemoryRefused,OSError,RuntimeError) as error:
            return _error(error if isinstance(error,(InvalidProject,MemoryRefused,Refused)) else Refused(str(error),next='check this integration’s setup in Models, then retry the saved source'))
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher
            if room is not None:room.release()
