"""Revocable range grants for already accepted user-owned source files."""
import copy
import hashlib
import json
import secrets
import threading
from env_worker import source_files as wire
from .scientific_files import FileSource


class SourceFileInputs:
    def __init__(self,sources,*,regional=()):
        if type(sources) is not dict or len(sources)>64:raise ValueError('Source-file grants need a bounded named map')
        regional=frozenset(regional)
        if not regional <= sources.keys():raise ValueError('Regional grants must name selected file inputs')
        self._regional=set()
        self._specs={};self._sources={};self._indexes={};self._index_reads=set();self._closed=False;self._budget=wire.Budget();self._read_lock=threading.Lock()
        for name,source in sources.items():
            if not isinstance(source,FileSource):raise ValueError('A file grant requires an accepted retained source')
            token=secrets.token_hex(24)
            spec={'format':wire.FORMAT,'token':token,'name':source.name,'size':source.size,'sha256':source.sha256,'reference':source.reference}
            if name in regional:
                spec['regional']=True;self._regional.add(token)
            if source.directory is not None:
                from env_worker.source_directory import index
                payload=json.dumps(index(source.directory,source.size),ensure_ascii=False,separators=(',',':')).encode()
                self._indexes[token]=payload
                spec['directory']={'format':'source-directory-index-v1','bytes':len(payload),'sha256':hashlib.sha256(payload).hexdigest()}
            wire.specification(spec)
            from env_worker.native_regions import IDENTITY
            if type(name) is not str or not IDENTITY.fullmatch(name):raise ValueError('Invalid source-file input name')
            self._sources[token]=source;self._specs[name]=spec
    def specifications(self):
        if self._closed:raise ValueError('Source-file grants are closed')
        return copy.deepcopy(self._specs)
    def answer(self,request,payload_size=0):
        if self._closed:raise ValueError('Source-file grants are closed')
        if type(payload_size) is not int or payload_size!=0:raise ValueError('Invalid source-file read request')
        if type(request) is dict and request.get('op')=='read-source-directory':
            if set(request)!={'op','token'} or type(request['token']) is not str:raise ValueError('Invalid folder index request')
            with self._read_lock:
                if self._closed:raise ValueError('Source-file grants are closed')
                body=self._indexes.get(request['token'])
                if body is None:raise ValueError('No selected folder matches this grant')
                if request['token'] in self._index_reads:raise ValueError('This folder index has already been delivered')
                self._index_reads.add(request['token'])
                if self._closed:raise ValueError('Source-file grant closed before delivery')
                return {'ok':True,'kind':'source-directory-index-v1','bytes':len(body)},body
        if (type(request) is not dict or set(request) not in ({'op','token','offset','length'},{'op','token','offset','length','view'})
                or ('view' in request and request['view'] is not True)
                or request['op']!='read-source-file' or type(request['token']) is not str
                or type(payload_size) is not int or payload_size!=0):
            raise ValueError('Invalid source-file read request')
        source=self._sources.get(request['token'])
        if source is None:raise ValueError('No selected file matches this read grant')
        view='view' in request
        if view and request['token'] not in self._regional:
            raise ValueError('This selected file has no declared regional read grant')
        wire.bounds(request['offset'],request['length'],source.size)
        with self._read_lock:
            if self._closed:raise ValueError('Source-file grants are closed')
            self._budget.admit(request['length'],view=view)
            body=source.read(request['offset'],request['length'])
            if self._closed:raise ValueError('Source-file grant closed before delivery')
        return {'ok':True,'kind':wire.FORMAT,'offset':request['offset'],'bytes':request['length']},body
    def close(self):
        # Revoke before any in-flight read completes; the read lock never delays Stop.
        self._closed=True
