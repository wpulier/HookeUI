"""Owned bounded transport into existing immutable file/folder intake.

The lease owns private staging, not a second source store. Each acknowledged chunk
has one offset; a lost reply may repeat only those exact bytes. Publication is the
existing FileIntake transaction and remains the sole visible acceptance boundary.
"""
import hashlib
import secrets
import shutil
import threading
import time

from env_worker.source_directory import MAX_INDEX
from .downloads import SPARE
from .scientific_directory_intake import DirectoryTransfer
from .scientific_file_intake import FileIntake, WORKING_BYTES, request_identity
from .scientific_files import CHUNK_BYTES, MAX_CHUNKS, RETAINED_METADATA_BYTES, _name
from .scientific_projects import InvalidProject

MAX_BYTES = CHUNK_BYTES * MAX_CHUNKS
IDLE_SECONDS = 60
MAX_TRANSFERS = 4


class Transfer:
    def __init__(self, owner, token, session, request, name, size, total, room, kind='directory'):
        self.owner, self.token, self.session = owner, token, session
        self.kind = kind
        self.identity = (session, request, name, size, total, kind)
        self.size, self.total, self.offset = size, total, 0
        self.last = None
        self.room = room
        self.lock = threading.RLock()
        self.stopping = threading.Event()
        self.closed = False
        self.finishing = False
        self.touched = time.monotonic()
        self.timer = None

    def check(self):
        if self.closed or self.stopping.is_set():
            raise InvalidProject('The source transfer stopped or expired; choose the source again')

    def write(self, offset, body):
        with self.lock:
            self.check()
            if type(offset) is not int or type(body) is not bytes or not 0 < len(body) <= CHUNK_BYTES:
                raise InvalidProject('Send one bounded source chunk at its exact offset')
            digest = hashlib.sha256(body).hexdigest()
            if self.last == (offset, len(body), digest):
                self.touched = time.monotonic()
                return self.offset
            if offset != self.offset or offset + len(body) > self.total:
                raise InvalidProject('The source chunk does not match the next accepted offset')
            try:
                self.owner.write(body)
            except BaseException:
                self.close()
                raise
            self.last = (offset, len(body), digest)
            self.offset += len(body)
            self.touched = time.monotonic()
            return self.offset

    def finish(self, disconnected=lambda: False):
        with self.lock:
            self.check()
            if self.offset != self.total:
                raise InvalidProject('The source transfer is incomplete; continue its remaining chunks')
            self.touched = time.monotonic()
            self.finishing = True
            try:
                return self.owner.publish(lambda: self.stopping.is_set() or disconnected())
            finally:
                self.close()

    def close(self):
        self.stopping.set()
        with self.lock:
            if not self.closed:
                self.closed = True
                if self.timer: self.timer.cancel()
                try: self.owner.close()
                finally:
                    self.owner = None
                    self.last = None
                    self.room.release()


class Transfers:
    def __init__(self, store, memory):
        self.store, self.memory = store, memory
        self.lock = threading.RLock()
        self.items = {}
        self.closed = False

    def begin(self, session, request, name, size, total, kind='directory'):
        request_identity(session, request)
        _name(name)
        if (kind not in ('file', 'directory') or type(size) is not int or not 0 <= size <= MAX_BYTES or type(total) is not int
                or (total != size if kind == 'file' else not size + 4 < total <= size + MAX_INDEX + 4)):
            raise InvalidProject('Choose a source within retained storage and declared transfer allowances')
        identity = (session, request, name, size, total, kind)
        with self.lock:
            if self.closed: raise InvalidProject('The app is stopping; import after reopening')
            self.items = {key: item for key, item in self.items.items() if not item.closed}
            for item in self.items.values():
                if item.identity == identity:
                    item.check()
                    item.touched = time.monotonic()
                    return item
                if item.identity[:2] == identity[:2]:
                    raise InvalidProject('This request already owns a different source transfer')
            if len(self.items) >= MAX_TRANSFERS:
                raise InvalidProject('Finish or stop another source import before starting one')
            store = self.store.for_session(session, write=True)
            store.root.mkdir(parents=True, exist_ok=True)
            outstanding = sum(max(0, item.total-item.offset) for item in self.items.values())
            if shutil.disk_usage(store.root).free < size + outstanding + WORKING_BYTES + SPARE:
                raise InvalidProject('Free disk space before importing this source')
            room = self.memory.reserve('analysis', WORKING_BYTES + RETAINED_METADATA_BYTES + (4*MAX_INDEX if kind == 'directory' else 0) + 3*CHUNK_BYTES,
                                       'retaining bounded source chunks')
            try:
                factory = FileIntake if kind == 'file' else DirectoryTransfer
                owner = factory(store, session, request, name=name, size=size, max_bytes=MAX_BYTES)
                token = secrets.token_hex(32)
                item = Transfer(owner, token, session, request, name, size, total, room, kind)
                self.items[token] = item
                self._watch(item)
                return item
            except BaseException:
                room.release()
                raise

    def _watch(self, item):
        def expire():
            with self.lock:
                if self.items.get(item.token) is not item or item.closed: return
                if item.finishing or time.monotonic()-item.touched < IDLE_SECONDS:
                    self._watch(item)
                    return
                item.stopping.set()
            item.close()
            with self.lock:
                if self.items.get(item.token) is item: self.items.pop(item.token)
        item.timer = threading.Timer(IDLE_SECONDS, expire)
        item.timer.daemon = True
        item.timer.start()

    def get(self, token, kind=None):
        with self.lock:
            item = self.items.get(token)
            if item is None: raise InvalidProject('The source transfer expired; choose the source again')
            if kind is not None and kind != item.kind: raise InvalidProject('The transfer belongs to another source kind')
            item.check()
            return item

    def discard(self, token, kind=None):
        with self.lock:
            item = self.items.get(token)
            if item is not None and kind is not None and kind != item.kind:
                raise InvalidProject('The transfer belongs to another source kind')
            if item: item.stopping.set()
        if item:
            item.close()
            with self.lock:
                if self.items.get(token) is item: self.items.pop(token)

    def recover(self, session, request, kind='directory'):
        """Join a stopped attempt before resolving its exact original acceptance."""
        target, transaction = request_identity(session, request)
        with self.lock:
            matching = [item for item in self.items.values() if item.identity[:2] == (session, request)]
            if any(item.kind != kind for item in matching):
                raise InvalidProject('The request belongs to another source kind')
            for item in matching:
                item.stopping.set()
        for item in matching: item.close()
        # Keep stopping attempts discoverable until every observer can join the
        # final publication boundary; a timed-out recovery may itself be retried.
        # A concurrent retry with the same request must not start while its final
        # receipt is being resolved. Begin and this read share the registry lock.
        with self.lock:
            for item in matching:
                if self.items.get(item.token) is item: self.items.pop(item.token)
            if any(item.identity[:2] == (session, request) for item in self.items.values()):
                raise InvalidProject('This import restarted during recovery; check its outcome again')
            store = self.store.for_session(session)
            if not store._folder(target).exists(): return None
            saved = store.read(target, session, revision=1)
            if saved.transaction != transaction or saved.document['kind'] != 'source-' + kind:
                raise InvalidProject('The original request belongs to different retained work')
            return saved

    def close(self):
        with self.lock:
            self.closed = True
            items = tuple(self.items.values())
            for item in items: item.stopping.set()
        for item in items: item.close()
        with self.lock: self.items.clear()
