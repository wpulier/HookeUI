"""Immutable user-selected files retained independently of software installation.

A completed file is a private candidate until a Project revision accepts its
reference. Names are labels, never host paths. Reads are bounded byte ranges;
readers and their permissions are admitted separately by the execution service.
"""
from __future__ import annotations
import copy
import hashlib
import json
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass

from .bounded_files import read_at
from .scientific_arrays import _put, DIRECTORY
from .scientific_projects import DIGEST, InvalidProject, _json
from .semantic_resources import parse_reference

FORMAT='project-source-file-v1'
CHUNK_BYTES=1 << 20
MAX_CHUNKS=65536
MAX_MANIFEST=8 << 20
VERIFY_WORKING_BYTES=MAX_MANIFEST*8+CHUNK_BYTES*4
RETAINED_METADATA_BYTES=16 << 20
COMPONENT=re.compile(r'file\.[A-Za-z0-9][A-Za-z0-9._-]{0,95}')


def _read(folder,name,limit):
    try:return read_at(folder,name,limit)
    except (OSError,ValueError) as error:
        raise InvalidProject('A retained source file is missing, changed or unsafe to read') from error


def _name(value):
    if (type(value) is not str or not 0<len(value)<=240 or value in ('.','..')
            or any(c in value for c in ('/','\\','\x00'))):
        raise InvalidProject('A scientific source needs a file name, not a path')
    return value


@contextmanager
def _directory(store,project,*,create=False):
    store._folder(project)
    root=os.open(store.root,DIRECTORY);owner=files=None
    try:
        owner=os.open(project,DIRECTORY,dir_fd=root)
        if create:
            try:
                os.mkdir('files',mode=0o700,dir_fd=owner);os.fsync(owner)
            except FileExistsError:pass
        files=os.open('files',DIRECTORY,dir_fd=owner)
        yield files
    finally:
        for fd in (files,owner,root):
            if fd is not None:os.close(fd)


def _reference(project,session,value):
    try:ref=parse_reference(value)
    except ValueError as error:raise InvalidProject(str(error)) from error
    if (ref.get('id')!=project or ref.get('session')!=session
            or type(ref.get('component')) is not str or not COMPONENT.fullmatch(ref['component'])
            or type(ref.get('revision')) is not str or not DIGEST.fullmatch(ref['revision'])):
        raise InvalidProject('A source file must belong to this Project and exact component revision')
    return ref


def _encode(meta):
    body=json.dumps(meta,ensure_ascii=False,allow_nan=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    if len(body)>MAX_MANIFEST:raise InvalidProject('The source file manifest exceeds its allowance')
    return body


def _manifest(store,project,session,reference,*,verify=True,check=lambda:None):
    check()
    ref=_reference(project,session,reference)
    with _directory(store,project) as folder:
        try:
            body=_read(folder,ref['revision']+'.json',MAX_MANIFEST)
            if hashlib.sha256(body).hexdigest()!=ref['revision']:raise InvalidProject('Source file manifest hash changed')
            meta=json.loads(body)
        except (ValueError,RecursionError) as error:raise InvalidProject('Source file manifest is unreadable') from error
        if (type(meta) is not dict or set(meta)!={'format','name','size','sha256','chunkBytes','chunks'}
                or meta['format']!=FORMAT or type(meta['size']) is not int or meta['size']<0
                or type(meta['chunkBytes']) is not int or not 1<=meta['chunkBytes']<=CHUNK_BYTES
                or type(meta['sha256']) is not str or not DIGEST.fullmatch(meta['sha256'])
                or type(meta['chunks']) is not list or len(meta['chunks'])>MAX_CHUNKS
                or len(meta['chunks'])!=(meta['size']+meta['chunkBytes']-1)//meta['chunkBytes']
                or any(type(x) is not str or not DIGEST.fullmatch(x) for x in meta['chunks'])):
            raise InvalidProject('Source file manifest has invalid sizes or content identities')
        _name(meta['name'])
        if verify:
            digest=hashlib.sha256()
            for index,key in enumerate(meta['chunks']):
                check()
                size=min(meta['chunkBytes'],meta['size']-index*meta['chunkBytes'])
                part=_read(folder,key+'.bin',size)
                if len(part)!=size or hashlib.sha256(part).hexdigest()!=key:
                    raise InvalidProject('Source file chunk hash changed')
                digest.update(part)
            if digest.hexdigest()!=meta['sha256']:raise InvalidProject('Source file hash changed')
    return meta


class FileDraft:
    """Sequential owned ingestion with fixed-size immutable chunks and a bounded buffer."""
    def __init__(self,store,project,session,*,component,name,size,sha256,max_written_bytes):
        store.read(project,session)
        if (type(component) is not str or not COMPONENT.fullmatch(component)
                or type(size) is not int or not 0<=size<=CHUNK_BYTES*MAX_CHUNKS
                or type(max_written_bytes) is not int or max_written_bytes<size
                or (sha256 is not None and (type(sha256) is not str or not DIGEST.fullmatch(sha256)))):
            raise InvalidProject('A source file requires a bounded size, hash and component identity')
        self.name=_name(name);self.size=size;self.sha256=sha256
        self.store,self.project,self.session,self.component=store,project,session,component
        self.closed=False;self.reference=None;self.received=0;self.buffer=bytearray();self.chunks=[]
        self.digest=hashlib.sha256()
        with _directory(store,project,create=True):pass

    def _flush(self):
        if not self.buffer:return
        with _directory(self.store,self.project) as folder:
            self.chunks.append(_put(folder,bytes(self.buffer),'.bin'))
        self.buffer.clear()

    def write(self,data):
        if self.closed or self.reference is not None:raise InvalidProject('This source file writer is closed')
        if type(data) is not bytes or len(data)>CHUNK_BYTES or self.received+len(data)>self.size:
            raise InvalidProject('Source file transfer exceeds its declared allowance')
        self.received+=len(data);self.digest.update(data)
        pending=memoryview(data)
        try:
            while pending:
                count=min(CHUNK_BYTES-len(self.buffer),len(pending))
                self.buffer.extend(pending[:count]);pending=pending[count:]
                if len(self.buffer)==CHUNK_BYTES:self._flush()
        except BaseException:
            self.close();raise

    def finish(self):
        if self.closed:raise InvalidProject('This source file writer is closed')
        if self.reference is not None:return copy.deepcopy(self.reference)
        if self.received!=self.size:raise InvalidProject('The source file transfer is incomplete')
        digest=self.digest.hexdigest()
        if self.sha256 is not None and digest!=self.sha256:raise InvalidProject('The source file hash does not match the selected content')
        self._flush()
        body=_encode({'format':FORMAT,'name':self.name,'size':self.size,'sha256':digest,
                    'chunkBytes':CHUNK_BYTES,'chunks':self.chunks})
        if len(body)>MAX_MANIFEST:raise InvalidProject('The source file manifest exceeds its allowance')
        with _directory(self.store,self.project) as folder:revision=_put(folder,body,'.json')
        self.reference={'session':self.session,'id':self.project,'component':self.component,'revision':revision}
        return copy.deepcopy(self.reference)

    def close(self):
        self.closed=True;self.buffer.clear()


@dataclass(frozen=True)
class FileSource:
    store:object
    project:str
    session:str
    name:str
    size:int
    sha256:str
    chunk_bytes:int
    chunks:tuple
    _reference:dict
    accepted_revision:int
    directory:dict | None = None

    @property
    def reference(self):return copy.deepcopy(self._reference)

    def read(self,offset,length):
        if (type(offset) is not int or type(length) is not int or offset<0 or length<0
                or offset+length>self.size or length>CHUNK_BYTES):
            raise InvalidProject('Source file range must stay inside its bounded byte allowance')
        if not length:return b''
        parts=[];end=offset+length
        with _directory(self.store,self.project) as folder:
            for index in range(offset//self.chunk_bytes,(end-1)//self.chunk_bytes+1):
                key=self.chunks[index];start=index*self.chunk_bytes
                size=min(self.chunk_bytes,self.size-start)
                body=_read(folder,key+'.bin',size)
                if len(body)!=size or hashlib.sha256(body).hexdigest()!=key:
                    raise InvalidProject('Source file chunk hash changed')
                parts.append(body[max(0,offset-start):min(size,end-start)])
        return b''.join(parts)


def source(store,project,session,revision,reference,*,check=lambda:None,verify_content=True):
    """Resolve an accepted manifest; optional regional opening defers content checks to each read.

    The default verifies the whole file. False establishes manifest/ownership validity,
    not fresh integrity of unread content. Every FileSource.read verifies its chunks.
    """
    if type(verify_content) is not bool:raise InvalidProject('Choose full or per-read content verification')
    saved=store.read(project,session,revision=revision)
    if reference not in saved.document['resources']:raise InvalidProject('Source file reference is not accepted in this revision')
    meta=_manifest(store,project,session,reference,check=check,verify=verify_content)
    return FileSource(store,project,session,meta['name'],meta['size'],meta['sha256'],meta['chunkBytes'],tuple(meta['chunks']),copy.deepcopy(reference),saved.revision)


@contextmanager
def _immutable_chunk(folder,name,size):
    """Pin one regular immutable object, without claiming fresh content integrity."""
    descriptor=None
    try:
        descriptor=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=folder)
        info=os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size!=size or info.st_mode&0o222:
            raise InvalidProject('A retained source chunk is missing, writable or unsafe; restore its original bytes')
        yield info
    except OSError as error:
        raise InvalidProject('A retained source chunk is unavailable; restore its original bytes') from error
    finally:
        if descriptor is not None:os.close(descriptor)


def retained_presence(source,*,check=lambda:None):
    """Check accepted immutable objects, deferring unread content hashes to read()."""
    with _directory(source.store,source.project) as folder:
        for index,key in enumerate(source.chunks):
            check()
            size=min(source.chunk_bytes,source.size-index*source.chunk_bytes)
            with _immutable_chunk(folder,key+'.bin',size):pass


def retain_into(accepted,target,target_project,*,check=lambda:None):
    """Give a private document independent links to an exact accepted file.

    Hooke never changes accepted chunks in place. Hard links preserve deletion
    lifetime, not physical isolation from an external chmod/write; each later
    read still verifies its content hash. No payload is read or copied here.
    The caller owns staging cleanup and atomic publication of the returned ref.
    """
    captured=source(accepted.store,accepted.project,accepted.session,accepted.accepted_revision,
                    accepted.reference,check=check,verify_content=False)
    if captured!=accepted:
        raise InvalidProject('The retained source identity changed; reopen its saved original')
    target.read(target_project,accepted.session)
    meta={'format':FORMAT,'name':accepted.name,'size':accepted.size,'sha256':accepted.sha256,
          'chunkBytes':accepted.chunk_bytes,'chunks':list(accepted.chunks)}
    with _directory(accepted.store,accepted.project) as origin, _directory(target,target_project,create=True) as dest:
        for index,key in enumerate(accepted.chunks):
            check()
            name=key+'.bin';size=min(accepted.chunk_bytes,accepted.size-index*accepted.chunk_bytes)
            with _immutable_chunk(origin,name,size) as before:
                try:
                    os.link(name,name,src_dir_fd=origin,dst_dir_fd=dest,follow_symlinks=False)
                except FileExistsError:pass  # repeated chunks must resolve to the same immutable object
                except OSError as error:
                    raise InvalidProject('This storage cannot retain immutable source links; no reader was added') from error
                with _immutable_chunk(dest,name,size) as after:
                    if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):
                        raise InvalidProject('A retained source chunk changed while linking; no reader was added')
        check()
        digest=_put(dest,_encode(meta),'.json')
    return {'session':accepted.session,'id':target_project,'component':'file.source','revision':digest}


def validate_references(store,project,session,document,*,accepted=(),check=lambda:None):
    original=None
    state=document['state'].get('sourceFile')
    if document['kind']=='source-directory':
        from env_worker.source_directory import index
        try:index(document['state'].get('sourceDirectory'),state.get('size') if type(state) is dict else None)
        except ValueError as error:raise InvalidProject(str(error)) from error
    for ref in document['resources']:
        if type(ref.get('component')) is str and ref['component'].startswith('file.') and ref not in accepted:
            meta=_manifest(store,project,session,ref,check=check)
            if document['kind'] in ('source-file','source-directory') and type(state) is dict and state.get('reference')==ref:
                original=meta
    if document['kind'] in ('source-file','source-directory'):
        if (type(state) is not dict or set(state)!={'format','name','size','sha256','reference'}
                or type(state['format']) is not int or state['format']!=1
                or type(state['size']) is not int or state['reference'] not in document['resources']):
            raise InvalidProject('A source file document must identify its exact retained original')
        original=original or _manifest(store,project,session,state['reference'],verify=False,check=check)
        if any(state[key]!=original[key] for key in ('name','size','sha256')):
            raise InvalidProject('Source file identity must match its retained original bytes')
        from .scientific_url_intake import record_of
        record_of(document['state'])
        if document['kind']=='source-directory':
            from .scientific_directory_intake import stack_of
            stack_of(document['state'])
        elif 'sourceStack' in document['state']:raise InvalidProject('Only a retained collection of files can be a stack')


def copy_records(store,project,session,document,*,check=lambda:None):
    """Validated descriptors for ownership-preserving document copies."""
    records=[];used=0
    for ref in document['resources']:
        if not ref.get('component','').startswith('file.'):continue
        meta=_manifest(store,project,session,ref,check=check)
        used+=len(meta['chunks'])*160+4096
        if used>RETAINED_METADATA_BYTES:
            raise InvalidProject('These sources exceed the copy metadata allowance; copy fewer sources together')
        records.append((ref,meta))
    return records


def copy_into(store,project,target,target_project,records,check):
    """Copy immutable bytes under the caller's deadline and private publication."""
    mapping={}
    if not records:return mapping
    with _directory(store,project) as source, _directory(target,target_project,create=True) as dest:
        for ref,meta in records:
            check()
            for index,key in enumerate(meta['chunks']):
                check()
                size=min(meta['chunkBytes'],meta['size']-index*meta['chunkBytes'])
                body=_read(source,key+'.bin',size)
                if len(body)!=size or hashlib.sha256(body).hexdigest()!=key:
                    raise InvalidProject('A source file changed; no copy was published')
                _put(dest,body,'.bin')
            digest=_put(dest,_encode(meta),'.json')
            mapping[_json(ref)]={**ref,'id':target_project,'revision':digest}
    return mapping
