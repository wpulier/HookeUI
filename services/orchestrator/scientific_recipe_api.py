"""Studio recipe actions retain the initiating Project and exact revisions."""
import asyncio
import threading
from contextlib import suppress
from fastapi import Request
from fastapi.responses import JSONResponse
from . import scientific_recipes as recipes
from .capture import captures, Refused as MemoryRefused
from .day.sid import is_sid
from .scientific_arrays import SAVE_WORKING_BYTES
from .scientific_file_api import watch_disconnect
from .scientific_project_api import _body, _error, _owned, _wire, Refused
from .scientific_projects import InvalidProject


def register(app,built,studio_origin,header):
    store=built.hub.resources.projects

    @app.get('/api/scientific-recipes/templates')
    async def templates(session:str):
        try:
            if not is_sid(session):raise InvalidProject('Choose the recipe’s Project')
            rows=await _owned(recipes.templates,store,session)
            return JSONResponse({'ok':True,'templates':rows},headers={'Cache-Control':'no-store'})
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/scientific-recipes/inputs')
    async def inputs(session:str):
        try:
            if not is_sid(session):raise InvalidProject('Choose the recipe’s Project')
            rows=await _owned(recipes.inputs,store,session)
            return JSONResponse({'ok':True,'inputs':rows},headers={'Cache-Control':'no-store'})
        except (ValueError,OSError) as error:return _error(error)

    @app.get('/api/scientific-projects/{project}/recipe-results')
    async def candidates(project:str,session:str,revision:int):
        try:
            if not is_sid(session):raise InvalidProject('Choose the recipe run’s Project')
            rows=await _owned(recipes.candidates,store,project,session,revision)
            return JSONResponse({'ok':True,'results':rows},headers={'Cache-Control':'no-store'})
        except (ValueError,OSError) as error:return _error(error)

    @app.post('/api/scientific-projects/{project}/recipe')
    async def change(project:str,request:Request):
        stopping=threading.Event();watcher=room=None
        def check():
            if stopping.is_set():raise InvalidProject('Recipe action stopped; accepted inputs and results remain')
        try:
            if not (request.headers.get(header)=='scientific-recipe' and studio_origin(request.headers.get('origin'),request.url.port)):
                raise Refused('Use the recipe controls in Studio.',403,next='return to the saved scientific window')
            body=await _body(request)
            if type(body.get('session')) is not str or not is_sid(body['session']) or type(body.get('revision')) is not int or body['revision']<1:
                raise InvalidProject('Choose an exact saved revision')
            session,revision=body['session'],body['revision']
            if session!=built.day.sid:raise Refused('The active Project changed.',409,next='return to this recipe’s Project')
            fields={'session','revision','action'};action=body.get('action')
            if action in ('create','begin') and set(body)==fields|{'request'}:
                function,args=(recipes.create if action=='create' else recipes.begin),(body['request'],)
            elif action=='begin' and set(body)==fields|{'request','input'} and type(body['input']) is dict:
                function,args=recipes.begin,(body['request'],body['input'])
            elif action=='fail' and set(body)==fields|{'note'} and type(body['note']) is str:
                function,args=recipes.fail,(body['note'],)
            elif action=='append' and set(body)==fields|{'template','templateRevision'} and type(body['template']) is str and type(body['templateRevision']) is int:
                function,args=recipes.append,(body['template'],body['templateRevision'])
            elif action=='prepare' and set(body)==fields:
                function,args=recipes.prepare,()
            elif action=='accept' and set(body)==fields|{'result','resultRevision'} and type(body['result']) is str and type(body['resultRevision']) is int:
                function,args=recipes.accept,(body['result'],body['resultRevision'])
            else:raise InvalidProject('Use a supported recipe action')
            room=captures(built.hub).memory.reserve('analysis',SAVE_WORKING_BYTES,'retaining recipe step')
            watcher=asyncio.create_task(watch_disconnect(request,stopping))
            saved=await _owned(function,store,project,session,revision,*args,check=check,on_cancel=stopping.set)
            return JSONResponse({'ok':True,'project':_wire(saved),'sessionChanged':session!=built.day.sid},headers={'Cache-Control':'no-store'})
        except (ValueError,MemoryRefused,OSError,RuntimeError) as error:
            return _error(error if isinstance(error,(InvalidProject,MemoryRefused,Refused)) else Refused(str(error),next='reopen the saved recipe; completed steps remain available'))
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher
            if room is not None:room.release()
