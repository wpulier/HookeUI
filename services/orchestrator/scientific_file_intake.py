"""Owned streaming file intake; publication retains bytes, never promises a reader.

The caller admits the destination Project, byte/disk budget and upload lifetime.
No parser runs here. A complete exact stream becomes one independently retained
source document; interrupted transfers remain private and are cleaned by the owner.
"""
from pathlib import Path
from contextlib import nullcontext
import hashlib
import os
import re
import shutil
import tempfile
import threading

from . import scientific_files as files
from .downloads import SPARE
from .scientific_projects import ProjectStore,InvalidProject,Conflict,_document,_identity
from .scientific_retention import publication

REQUEST=re.compile(r'[a-zA-Z0-9_-]{16,128}')
WORKING_BYTES=4 << 20
_pending=set()
_pending_lock=threading.Lock()


def request_identity(session, request):
    """One exact owned intake request has one durable destination, including lost acknowledgements."""
    _identity(session, 'session')
    if type(request) is not str or not REQUEST.fullmatch(request):
        raise InvalidProject('Choose a valid scientific retention request identity')
    transaction = hashlib.sha256((session+'\0'+request).encode()).hexdigest()
    return 'p'+transaction[:32], transaction

def retry_cleanup():
    with _pending_lock:attempts=list(_pending)
    return [OSError('Scientific source staging cleanup remains pending') for owner in attempts if not owner.close()]


class FileIntake:
    def __init__(self,store,session,request,*,name,size,max_bytes,retained_source=None):
        _identity(session,'session')
        files._name(name)
        if (type(request) is not str or not REQUEST.fullmatch(request)
                or type(size) is not int or type(max_bytes) is not int or not 0<=size<=max_bytes
                or size>files.CHUNK_BYTES*files.MAX_CHUNKS):
            raise InvalidProject('Choose a file within this import’s declared allowance')
        self.store,self.session,self.request=store,session,request
        self.name,self.size=name,size
        if retained_source is not None and (not isinstance(retained_source,files.FileSource)
                or retained_source.store is not store or retained_source.session!=session
                or retained_source.name!=name or retained_source.size!=size):
            raise InvalidProject('Retain an accepted source owned by this Project store and session')
        self.retained_source=retained_source
        self.verified_files=()
        # One public retry key binds the same destination, independently of a lost
        # HTTP acknowledgement. Existing bytes must match before a retry succeeds.
        self.target,self.transaction=request_identity(session,request)
        self.stage=self.private=self.writer=self.body=self.receipt=None
        self.closed=False;self.staged=False
        store.root.mkdir(parents=True,exist_ok=True)
        # Retention creates independent directory entries, not another payload.
        required=size if retained_source is None else files.MAX_MANIFEST+4096*len(retained_source.chunks)
        if shutil.disk_usage(store.root).free<required+WORKING_BYTES+SPARE:
            raise InvalidProject('Free disk space before importing this scientific file')
        try:
            self.stage=Path(tempfile.mkdtemp(prefix='.scientific-import-',dir=store.root))
            self.private=ProjectStore(self.stage)
            seed=self.private.create(session,{'kind':'source-staging','title':name,'state':{},'resources':[], 'runtimes':[]},identity=self.target)
            if retained_source is None:
                self.writer=self.private.file_draft(seed.id,session,component='file.source',name=name,size=size,
                    sha256=None,max_written_bytes=max_bytes)
        except BaseException:
            self.close();raise

    def write(self,body):
        if self.closed or self.body is not None or self.writer is None:raise InvalidProject('This scientific import is closed to writes')
        self.writer.write(body)

    def document(self,ref):
        """Server-owned description; derived reader intake retains the same file owner."""
        return {'kind':'source-file','title':self.name,'state':{'sourceFile':{
            'format':1,'name':self.name,'size':self.size,'sha256':self.retained_source.sha256 if self.retained_source is not None else self.writer.digest.hexdigest(),
            'reference':ref}},'resources':[ref],'runtimes':[]}

    def acceptance(self):
        """An optional source owner boundary, only at final visible publication."""
        return nullcontext()

    def publish(self,stopping=lambda:False):
        if self.receipt is not None:return self.receipt
        if self.closed:raise InvalidProject('This scientific import is closed')
        def check():
            if stopping():raise InvalidProject('Import stopped; the source was not added')
        check()
        if self.body is None:
            if self.retained_source is None:
                ref=self.writer.finish()
            else:
                ref=files.retain_into(self.retained_source,self.private,self.target,check=check)
                self.verified_files=(ref,)
            self.body=_document(self.document(ref))
        if not self.staged:
            self.private._publish(self.private._folder(self.target),self.session,self.body,[],transaction=self.transaction,
                                  check=check,verified_files=self.verified_files)
            self.staged=True
        target=self.store._folder(self.target)
        if target.exists():
            saved=self.store.read(self.target,self.session,revision=1)
            if saved.transaction!=self.transaction or saved._document!=self.body:
                raise Conflict('This import request already belongs to different content; choose the file again')
            # Verification runs outside the shared publication lock and remains
            # interruptible. Exact first revisions and chunks are immutable.
            retained=self.store.file_source(saved.id,saved.session,saved.revision,saved.document['resources'][0],
                                           check=check,verify_content=self.retained_source is None)
            if self.retained_source is not None:files.retained_presence(retained,check=check)
            with self.store._locked(self.store.root):
                check()
                if self.store.read(self.target,self.session,revision=1)!=saved:
                    raise Conflict('The retained source changed; keep the original before retrying')
                self.store._sync_directory(self.store.root)
            self.receipt=saved;return saved
        with self.store._locked(self.store.root):
            if target.exists():
                raise Conflict('This import was accepted concurrently; retry this same file to recover its receipt')
            with publication(self.store.root,self.store.runtime_root), self.acceptance():
                if stopping():raise InvalidProject('Import stopped; the source was not added')
                os.rename(self.private._folder(self.target),target)
                self.store._sync_directory(self.store.root)
                self.receipt=self.store.read(self.target,self.session,revision=1)
                return self.receipt

    def close(self):
        self.closed=True
        if self.writer is not None:self.writer.close()
        try:
            if self.stage is not None:
                shutil.rmtree(self.stage)
                self.stage=None
        except OSError:
            with _pending_lock:_pending.add(self)
            return False
        with _pending_lock:_pending.discard(self)
        return True
