"""Read-only seekable access to explicitly granted source bytes, never host paths."""
from __future__ import annotations
import copy
import hashlib
import json
import io
import threading
from gpu_worker.semantic_resources import parse_reference

FORMAT='source-file-ranges-v1'
MAX_RANGE=1 << 20
MAX_READ_BYTES=64 << 20
MAX_REQUESTS=4096


def specification(value):
    required={'format','token','name','size','sha256','reference'}
    if (type(value) is not dict or not required <= set(value) <= required | {'directory','regional'}
            or ('regional' in value and value['regional'] is not True)
            or value['format']!=FORMAT or type(value['token']) is not str or not 1<=len(value['token'])<=256
            or type(value['name']) is not str or not 0<len(value['name'])<=240
            or any(c in value['name'] for c in ('/','\\','\x00')) or value['name'] in ('.','..')
            or type(value['size']) is not int or not 0<=value['size']<2**63
            or type(value['sha256']) is not str or len(value['sha256'])!=64
            or any(c not in '0123456789abcdef' for c in value['sha256'])):
        raise ValueError('Source files require bounded names, sizes and exact content identities')
    if 'directory' in value:
        from .source_directory import MAX_INDEX
        meta=value['directory']
        if (type(meta) is not dict or set(meta)!={'format','bytes','sha256'}
                or meta['format']!='source-directory-index-v1' or type(meta['bytes']) is not int or not 1<=meta['bytes']<=MAX_INDEX
                or type(meta['sha256']) is not str or len(meta['sha256'])!=64 or any(c not in '0123456789abcdef' for c in meta['sha256'])):
            raise ValueError('A folder grant needs an exact bounded index identity')
    ref=parse_reference(value['reference'])
    if not ref.get('component','').startswith('file.'):
        raise ValueError('A source file must name its retained file component')
    return copy.deepcopy(value)


def bounds(offset,length,size):
    if (type(offset) is not int or type(length) is not int or offset<0 or not 0<=length<=MAX_RANGE
            or offset+length>size):
        raise ValueError('Read a bounded byte range inside this selected source')


class Budget:
    def __init__(self):
        from .native_regions import Budget as RegionalBudget
        self.requests=self.bytes=0;self.lock=threading.RLock()
        self.navigation=RegionalBudget()
    def admit(self,size,*,view=False):
        if view:
            self.navigation.admit(size,view=True)
            return
        if self.requests>=MAX_REQUESTS or self.bytes+size>MAX_READ_BYTES:
            raise ValueError('This reader exhausted its source-file allowance; use a declared regional reader')
        self.requests+=1;self.bytes+=size


class SourceFile(io.RawIOBase):
    def __init__(self,spec,request,budget):
        super().__init__();self._spec=specification(spec);self._request=request;self._budget=budget;self._position=0
    @property
    def name(self):return self._spec['name']
    @property
    def size(self):return self._spec['size']
    @property
    def sha256(self):return self._spec['sha256']
    @property
    def regional(self):return self._spec.get('regional',False)
    @property
    def reference(self):return copy.deepcopy(self._spec['reference'])
    def readable(self):return not self.closed
    def seekable(self):return not self.closed
    def writable(self):return False
    def tell(self):self._checkClosed();return self._position
    def seek(self,offset,whence=io.SEEK_SET):
        self._checkClosed()
        if type(offset) is not int or type(whence) is not int or whence not in (0,1,2):raise ValueError('Invalid source-file seek')
        position=offset+({0:0,1:self._position,2:self.size}[whence])
        if not 0<=position<=self.size:raise ValueError('Source-file seek is outside the selected content')
        self._position=position;return position
    def read(self,size=-1,*,view=False):
        self._checkClosed()
        if type(view) is not bool or view and not self._spec.get('regional',False):
            raise ValueError('This selected file has no declared regional read grant')
        if type(size) is not int or size < -1:raise ValueError('Source-file read needs an integer size')
        length=self.size-self._position if size==-1 else min(size,self.size-self._position)
        bounds(self._position,length,self.size)
        if length==0:return b''
        with self._budget.lock:
            self._budget.admit(length,view=view)
            reply=self._request({'op':'read-source-file','token':self._spec['token'],'offset':self._position,'length':length,
                                 **({'view':True} if view else {})})
            if type(reply) is not tuple or len(reply)!=2:raise ValueError('Invalid source-file reply')
            header,body=reply
            if (type(header) is not dict or set(header)!={'ok','kind','offset','bytes'} or header.get('ok') is not True
                    or header['kind']!=FORMAT or type(header['offset']) is not int or header['offset']!=self._position
                    or type(header['bytes']) is not int or header['bytes']!=length or type(body) is not bytes or len(body)!=length):
                raise ValueError('Source-file reply does not match the granted range')
            if view:
                self._budget.navigation.finished_view()
            self._position+=length;return body
    def readinto(self,buffer):
        view=memoryview(buffer).cast('B')
        if view.readonly:raise TypeError('Read destination must be writable')
        # BufferedReader can request a large destination. Transfer bounded pieces
        # without allocating or sending that entire destination through the broker.
        data=self.read(min(len(view),MAX_RANGE));view[:len(data)]=data;return len(data)


def bind(specs,request):
    if type(specs) is not dict or len(specs)>64 or not callable(request):raise ValueError('Source files require a bounded named grant map')
    from .native_regions import IDENTITY
    budget=Budget();out={}
    for name,spec in specs.items():
        if type(name) is not str or not IDENTITY.fullmatch(name):raise ValueError('Invalid source-file input name')
        source=SourceFile(spec,request,budget)
        if 'directory' in spec:
            from .source_directory import SourceDirectory
            meta=spec['directory']
            with budget.lock:
                header,body=request({'op':'read-source-directory','token':spec['token']})
            if (header!={'ok':True,'kind':'source-directory-index-v1','bytes':meta['bytes']}
                    or type(body) is not bytes or len(body)!=meta['bytes'] or hashlib.sha256(body).hexdigest()!=meta['sha256']):
                raise ValueError('Folder index reply does not match the granted identity')
            try:description=json.loads(body)
            except (ValueError,RecursionError) as error:raise ValueError('Folder index is invalid') from error
            source=SourceDirectory(source,description)
        out[name]=source
    return out
