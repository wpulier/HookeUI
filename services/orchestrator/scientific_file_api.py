"""Studio-selected source intake and exact-byte download; neither executes a reader."""
import asyncio
import threading
import time
from contextlib import suppress
from urllib.parse import quote

from fastapi import Request
from fastapi.responses import JSONResponse,StreamingResponse
from .capture import Refused as MemoryRefused,captures
from .day.sid import is_sid
from .scientific_file_intake import FileIntake,WORKING_BYTES
from .scientific_files import CHUNK_BYTES,VERIFY_WORKING_BYTES
from .scientific_projects import InvalidProject
from .scientific_project_api import Refused,_body,_owned,_wire,_error

MAX_UPLOAD=64 << 20
UPLOAD_SECONDS=300
async def watch_disconnect(request,stopping):
    while not stopping.is_set():
        if await request.is_disconnected():
            stopping.set();return
        await asyncio.sleep(.1)


HEADERS={'Cache-Control':'no-store','X-Content-Type-Options':'nosniff',
         'Cross-Origin-Resource-Policy':'same-origin'}


class SourceResponse(StreamingResponse):
    def __init__(self,source,room):
        self.room=room
        async def pieces():
            offset=0
            while offset<source.size:
                length=min(CHUNK_BYTES,source.size-offset)
                yield await _owned(source.read,offset,length)
                offset+=length
        super().__init__(pieces(),media_type='application/octet-stream',headers={**HEADERS,
            'Content-Length':str(source.size),
            'Content-Disposition':"attachment; filename*=UTF-8''"+quote(source.name,safe='')})
    async def __call__(self,scope,receive,send):
        try:await super().__call__(scope,receive,send)
        finally:self.room.release()


def _inputs_changed(built):
    """A scientific input was retained: the projects list counts it (F-L8-6), so it is sent again."""
    changed=getattr(built,'sessions_changed',None)
    if changed is not None:changed()


def register(app,built,studio_origin,header):
    store=built.hub.resources.projects

    from .scientific_chunk_intake import Transfers
    transfers = built.scientific_uploads = Transfers(store, captures(built.hub).memory)

    def transfer_origin(request):
        kind = 'file' if request.url.path.startswith('/api/scientific-file-') else 'directory'
        if not (request.headers.get(header)==f'import-scientific-{kind}-chunk'
                and studio_origin(request.headers.get('origin'),request.url.port)):
            raise Refused('Import the selected source from this workspace.',403,next='return to Import in the workspace')
        return kind

    @app.post('/api/scientific-file-transfers')
    @app.post('/api/scientific-directory-transfers')
    async def begin_transfer(request:Request):
        try:
            kind=transfer_origin(request)
            p=request.query_params
            if set(p)!={'session','request','name','size','bytes'} or len(p.multi_items())!=5:
                raise InvalidProject('The source transfer needs its exact selection metadata')
            if not is_sid(p['session']) or p['session']!=built.day.sid:
                raise Refused('The destination Project changed.',409,next='choose the source in its intended Project')
            try:size,total=int(p['size']),int(p['bytes'])
            except ValueError:raise InvalidProject('The source byte counts are invalid')
            if str(size)!=p['size'] or str(total)!=p['bytes']:raise InvalidProject('The source byte counts are invalid')
            item=await _owned(transfers.begin,p['session'],p['request'],p['name'],size,total,kind)
            return JSONResponse({'ok':True,'token':item.token,'offset':item.offset,'chunkBytes':CHUNK_BYTES},headers=HEADERS)
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:return _error(error)

    @app.post('/api/scientific-file-transfer-recovery')
    @app.post('/api/scientific-directory-transfer-recovery')
    async def recover_transfer(request:Request):
        try:
            kind=transfer_origin(request)
            p=request.query_params
            if set(p)!={'session','request'} or len(p.multi_items())!=2 or not is_sid(p['session']):
                raise InvalidProject('Recover the original captured Project and import request')
            with captures(built.hub).memory.reserve('analysis',4<<20,'recovering source acceptance'):
                saved=await _owned(transfers.recover,p['session'],p['request'],kind)
            return JSONResponse({'ok':True,'project':_wire(saved) if saved else None},headers=HEADERS)
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:return _error(error)

    @app.put('/api/scientific-file-transfers/{token}')
    @app.put('/api/scientific-directory-transfers/{token}')
    async def transfer_chunk(request:Request,token:str):
        try:
            kind=transfer_origin(request)
            p=request.query_params
            if set(p)!={'offset'} or len(p.multi_items())!=1:raise InvalidProject('Supply the exact chunk offset')
            try:offset=int(p['offset']); length=int(request.headers.get('content-length',''))
            except ValueError:raise InvalidProject('Supply bounded chunk length and offset')
            if str(offset)!=p['offset'] or not 0<length<=CHUNK_BYTES:
                raise InvalidProject('Supply one chunk of at most 1 MiB')
            item=transfers.get(token,kind)
            # A request is bounded independently from the admitted logical source.
            with captures(built.hub).memory.reserve('analysis',3*CHUNK_BYTES,'receiving one source chunk'):
                async with asyncio.timeout(UPLOAD_SECONDS):
                    body=bytearray()
                    async for part in request.stream():
                        if len(body)+len(part)>length:raise InvalidProject('The source chunk exceeds its declared length')
                        body.extend(part)
                    if len(body)!=length:raise InvalidProject('The source chunk was interrupted')
                    accepted=await _owned(item.write,offset,bytes(body))
            return JSONResponse({'ok':True,'offset':accepted},headers=HEADERS)
        except TimeoutError:return _error(Refused('This source chunk timed out.',408,next='retry this same chunk or stop the import'))
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:return _error(error)

    @app.post('/api/scientific-file-transfers/{token}/finish')
    @app.post('/api/scientific-directory-transfers/{token}/finish')
    async def finish_transfer(request:Request,token:str):
        stopping=threading.Event();watcher=None
        try:
            kind=transfer_origin(request);item=transfers.get(token,kind)
            with captures(built.hub).memory.reserve('analysis',VERIFY_WORKING_BYTES,'verifying retained source'):
                async with asyncio.timeout(UPLOAD_SECONDS):
                    watcher=asyncio.create_task(watch_disconnect(request,stopping))
                    saved=await _owned(item.finish,stopping.is_set,on_cancel=stopping.set)
            _inputs_changed(built)
            return JSONResponse({'ok':True,'project':_wire(saved),'sessionChanged':saved.session!=built.day.sid},headers=HEADERS)
        except TimeoutError:return _error(Refused('Folder verification timed out.',408,next='retry the same selection to recover any accepted source'))
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher
            # Never use an unauthorized token to stop somebody else's transfer.
            if watcher is not None:await _owned(transfers.discard,token,kind)

    @app.delete('/api/scientific-file-transfers/{token}')
    @app.delete('/api/scientific-directory-transfers/{token}')
    async def stop_transfer(request:Request,token:str):
        try:
            kind=transfer_origin(request)
            await _owned(transfers.discard,token,kind)
            return JSONResponse({'ok':True},headers=HEADERS)
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:return _error(error)

    @app.post('/api/scientific-files')
    async def import_file(request:Request):
        return await retain(request,'file')

    @app.post('/api/scientific-directories')
    async def import_directory(request:Request):
        return await retain(request,'directory')

    @app.post('/api/scientific-stacks')
    async def import_stack(request:Request):
        return await retain(request,'stack')

    async def retain(request:Request,kind:str):
        owner=None;stopping=threading.Event();directory=kind!='file'
        try:
            if not (request.headers.get(header)==f'import-scientific-{kind}'
                    and studio_origin(request.headers.get('origin'),request.url.port)):
                raise Refused('Choose the scientific file from the Studio.',403,next='return to Open in the workspace')
            params=request.query_params
            fields={'session','name','size','request',*(('axis',) if kind=='stack' else ())}
            if set(params)!=fields or len(params.multi_items())!=len(fields):
                raise Refused('The scientific import has missing or repeated fields.',next='choose the file again')
            session=params['session']
            if not is_sid(session) or session!=built.day.sid:
                raise Refused('The destination Project changed.',409,next='choose the file in its intended Project')
            try:size=int(params['size'])
            except ValueError:raise Refused('The selected file size is invalid.',next='choose the file again')
            if str(size)!=params['size'] or not 0<=size<=MAX_UPLOAD:
                raise Refused('This file import supports files up to 64 MiB.',413,next='use the chunked File import in the workspace for a larger local file')
            # The ASGI transport owns its incoming chunk. Reserve the full admitted
            # request bound as well as our chunk buffers; never decode the source.
            with captures(built.hub).memory.reserve('analysis',MAX_UPLOAD+WORKING_BYTES+(1 << 20),'retaining selected scientific source'):
                async with asyncio.timeout(UPLOAD_SECONDS):
                    def begin():
                        nonlocal owner
                        from .scientific_directory_intake import DirectoryTransfer,StackTransfer
                        factory={'file':FileIntake,'directory':DirectoryTransfer,'stack':StackTransfer}[kind]
                        extra={'axis':params['axis']} if kind=='stack' else {}
                        owner=factory(store,session,params['request'],name=params['name'],size=size,max_bytes=MAX_UPLOAD,**extra)
                    await _owned(begin,on_cancel=stopping.set)
                    async for part in request.stream():
                        for offset in range(0,len(part),CHUNK_BYTES):
                            await _owned(owner.write,part[offset:offset+CHUNK_BYTES])
                    watcher=asyncio.create_task(watch_disconnect(request,stopping))
                    try:saved=await _owned(owner.publish,stopping.is_set,on_cancel=stopping.set)
                    finally:
                        watcher.cancel()
                        with suppress(asyncio.CancelledError):await watcher
            _inputs_changed(built)
            return JSONResponse({'ok':True,'project':_wire(saved),'sessionChanged':session!=built.day.sid},headers=HEADERS)
        except TimeoutError:
            return _error(Refused('The file transfer timed out.',408,next='retry the same file; an accepted import is retained without duplication'))
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:
            return _error(error)
        finally:
            if owner is not None:await _owned(owner.close)

    @app.post('/api/scientific-urls')
    async def import_url(request:Request):
        """Download a source from a web address into this Project; the bytes are untrusted and never parsed."""
        from .scientific_url_intake import Download
        def owner(session,body):
            if (set(body)!={'session','url','request','sha256','name'}
                    or any(body[k] is not None and type(body[k]) is not str for k in ('sha256','name'))):
                raise Refused('The download request has missing or unsupported fields.',next='paste the address again')
            return Download(store,session,body['request'],body['url'],sha256=body['sha256'],name=body['name'])
        return await downloaded(request,'import-scientific-url',owner)

    @app.get('/api/scientific-samples')
    async def samples():
        from .scientific_samples import listed
        try:return JSONResponse({'ok':True,'samples':await _owned(listed)},headers={'Cache-Control':'no-store'})
        except (ValueError,OSError) as error:
            return _error(Refused(str(error),next='check the reader integrations in Models'))

    @app.post('/api/scientific-samples')
    async def open_sample(request:Request):
        """Download a declared sample at its pinned digest; the same untrusted-web rules apply."""
        from .scientific_samples import download
        def owner(session,body):
            if set(body)!={'session','capability','sample','request'} or any(type(body[k]) is not str for k in ('capability','sample')):
                raise Refused('The sample request has missing or unsupported fields.',next='choose the sample again')
            return download(store,session,body['request'],body['capability'],body['sample'])
        return await downloaded(request,'open-scientific-sample',owner)

    async def downloaded(request,purpose,make):
        from .scientific_url_intake import RemoteRefused,TOTAL_SECONDS
        owner=None;stopping=threading.Event();watcher=None
        try:
            if not (request.headers.get(header)==purpose and studio_origin(request.headers.get('origin'),request.url.port)):
                raise Refused('Download the scientific source from the Studio.',403,next='return to Import in the workspace')
            body=await _body(request)
            if type(body.get('session')) is not str:
                raise Refused('The download request has missing or unsupported fields.',next='start the download again')
            session=body['session']
            if not is_sid(session) or session!=built.day.sid:
                raise Refused('The destination Project changed.',409,next='start the download in its intended Project')
            with captures(built.hub).memory.reserve('analysis',WORKING_BYTES+2*CHUNK_BYTES,'downloading scientific source'):
                async with asyncio.timeout(TOTAL_SECONDS+30):
                    def begin():
                        nonlocal owner
                        owner=make(session,body)
                    await _owned(begin)
                    watcher=asyncio.create_task(watch_disconnect(request,stopping))
                    saved=await _owned(owner.run,stopping.is_set,on_cancel=stopping.set)
            _inputs_changed(built)
            return JSONResponse({'ok':True,'project':_wire(saved),'sessionChanged':session!=built.day.sid},headers=HEADERS)
        except TimeoutError:
            return _error(Refused('The download timed out; nothing was kept.',408,next='retry the download'))
        except RemoteRefused as error:
            return _error(Refused(str(error),error.status,next=error.next))
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:
            return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher
            if owner is not None:await _owned(owner.close)

    @app.get('/api/scientific-files/{project}/text')
    async def preview_text(project:str,session:str,revision:int,offset:int=0,length:int=65536):
        from .scientific_text import page, PAGE_BYTES
        try:
            if not is_sid(session) or revision < 1 or offset < 0 or not 1 <= length <= PAGE_BYTES:
                raise InvalidProject('Select an exact source revision and a bounded byte range')
            def read():
                saved=store.read(project,session,revision=revision)
                state=saved.document.get('state',{}).get('sourceFile')
                if saved.document['kind'] not in ('source-file','matrix-result') or type(state) is not dict or 'reference' not in state:
                    raise InvalidProject('This document has no retained file to preview as text')
                source=store.file_source(project,session,revision,state['reference'],verify_content=False)
                return {'ok':True,'id':saved.id,'session':saved.session,'revision':saved.revision,
                        'digest':saved.digest,'sha256':source.sha256,'content':page(source,offset,length),
                        'untrusted':True,'verification':'displayed-chunks-only'}
            # Each displayed chunk is verified. Unread chunks are not scanned or certified.
            with captures(built.hub).memory.reserve('analysis',VERIFY_WORKING_BYTES,'previewing retained text'):
                result=await _owned(read)
            return JSONResponse(result,headers=HEADERS)
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:return _error(error)

    @app.get('/api/scientific-files/{project}')
    async def download(request:Request,project:str,session:str,revision:int):
        room=None;watcher=None;stopping=threading.Event();deadline=time.monotonic()+60
        def check():
            if stopping.is_set() or time.monotonic()>deadline:
                raise InvalidProject('Source verification stopped; retry downloading its saved revision')
        try:
            if not is_sid(session) or revision<1:
                raise Refused('Select an exact source revision.',next='reopen the source in Project Data')
            room=captures(built.hub).memory.reserve('analysis',VERIFY_WORKING_BYTES,'reading retained scientific source')
            saved=await _owned(store.read,project,session,revision=revision)
            document=saved.document
            state=document['state'].get('sourceFile')
            if document['kind'] not in ('source-file','matrix-result') or type(state) is not dict or 'reference' not in state:
                raise Refused('This document has no downloadable retained file.',next='choose a retained file or scientific result in Project Data')
            watcher=asyncio.create_task(watch_disconnect(request,stopping))
            source=await _owned(store.file_source,project,session,revision,state['reference'],check=check,on_cancel=stopping.set)
            response=SourceResponse(source,room);room=None
            return response
        except (Refused,MemoryRefused,InvalidProject,OSError) as error:return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher
            if room is not None:room.release()


    @app.get('/api/scientific-directories/{project}/file')
    async def download_entry(request:Request,project:str,session:str,revision:int,path:str):
        room=None;watcher=None;stopping=threading.Event();deadline=time.monotonic()+60
        def check():
            if stopping.is_set() or time.monotonic()>deadline:raise InvalidProject('Folder verification stopped; retry its saved revision')
        try:
            if not is_sid(session) or revision<1:raise Refused('Choose an exact saved folder revision.',next='reopen the folder in Project Data')
            room=captures(built.hub).memory.reserve('analysis',VERIFY_WORKING_BYTES+(4 << 20),'reading retained folder entry')
            saved=await _owned(store.read,project,session,revision=revision)
            if saved.document['kind']!='source-directory':raise Refused('Choose a retained folder in Project Data.',next='open a folder rather than a single file')
            state=saved.document['state']
            from env_worker.source_directory import index
            description=index(state.get('sourceDirectory'),state['sourceFile']['size'])
            row=next((row for row in description['entries'] if row['path']==path),None)
            if row is None:raise Refused('This file is not in the selected saved folder.',404,next='choose an entry from the saved folder')
            watcher=asyncio.create_task(watch_disconnect(request,stopping))
            source=await _owned(store.file_source,project,session,revision,state['sourceFile']['reference'],check=check,on_cancel=stopping.set)
            class Entry:
                name=path.rsplit('/',1)[-1]
                size=row['size']
                def read(self,offset,length):return source.read(row['offset']+offset,length)
            response=SourceResponse(Entry(),room);room=None;return response
        except (Refused,MemoryRefused,InvalidProject,ValueError,OSError) as error:return _error(error)
        finally:
            if watcher is not None:
                watcher.cancel()
                with suppress(asyncio.CancelledError):await watcher
            if room is not None:room.release()
