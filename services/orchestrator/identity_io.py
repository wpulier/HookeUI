"""Keep portable identity I/O off the live loop and pinned to its admitted Project.

The existing Identity event model remains authoritative. A worker rebuilds a
detached view, persists through the ordinary store and returns accepted state.
Only the matching UI generation may receive that state. Cancellation of a caller
does not release the serial queue while its filesystem worker is still running.
"""
import asyncio
import copy
from types import SimpleNamespace

from .project_roots import ProjectLocationError

FIELDS = ('day', 'session', 'of', 'made', 'links', 'joins', 'merges', 'carries', 'observations', 'lost')
MUTATIONS = frozenset(('make', 'same_one', 'not_same', 'follow', 'observe', 'lose', 'found', '_write'))


class IdentityIO:
    def __init__(self, identity):
        self.identity = identity
        self.ready, self.error, self.pending = False, None, None
        self.closed = False
        self.lock = asyncio.Lock()
        self.tasks = set()

    def owner(self):
        return self.identity._sid(), getattr(self.identity.store, 'generation', 0)

    def background(self):
        # Configuration is an in-memory fact. Resolving a locator here would
        # still block the camera loop on slow/offline Project storage.
        return getattr(self.identity.store, 'locations', None) is not None

    def opened(self):
        self.error, self.ready, self.pending = None, False, None
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.identity._load()
            self.ready = True
            return
        if not self.background():
            self.identity._load()
            self.ready = True
            return
        self.identity._reset()
        self.pending = self._schedule(self._run(self.owner(), self._capture(self.owner())))

    def _schedule(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.add(task)
        def finished(done):
            self.tasks.discard(done)
            if not done.cancelled() and done.exception() is not None:
                self.identity.log({'kind': 'identity-storage', 'error': str(done.exception())[:200]})
        task.add_done_callback(finished)
        return task

    def require_ready(self):
        if self.error is not None:
            raise ProjectLocationError(f'Project identity history could not be read: {self.error}')
        if not self.ready:
            raise ProjectLocationError('Project identity history is still loading; retry when it is ready')

    async def ensure_ready(self):
        if self.identity.session != self.identity._sid():
            self.opened()
        owner = self.owner()
        if not self.ready and self.pending is not None:
            await asyncio.shield(self.pending)
        if owner != self.owner():
            raise ProjectLocationError('The Project changed while its identity history was loading')
        self.require_ready()

    async def mutate(self, method, *args, owner, **kwargs):
        if method not in MUTATIONS:
            raise ValueError('Unsupported identity mutation')
        if self.closed or owner != self.owner():
            raise ProjectLocationError('The Project changed or closed before identity work was admitted')
        if not self.background():
            return getattr(self.identity, method)(*args, **kwargs)
        # The task, not its awaiting caller, owns serialization until the actual
        # worker returns. A lost reply cannot overlap another write/replay.
        task = self._schedule(self._run(owner, self._capture(owner), method, copy.deepcopy(args), copy.deepcopy(kwargs)))
        return await asyncio.shield(task)

    def carry(self, event):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            self.identity._write(event)
            return
        if not self.background():
            self.identity._write(event)
            return
        owner = self.owner()
        self._schedule(self._run(owner, self._capture(owner), '_write', (copy.deepcopy(event),), {}))

    def retire(self):
        self.closed = True
        if self.identity._task is not None:
            self.identity._task.cancel()

    def _capture(self, owner):
        if self.closed or owner != self.owner():
            raise ProjectLocationError('The Project changed or closed before identity work was admitted')
        store = self.identity.store
        pinned = SimpleNamespace(sid=owner[0], days=tuple(store.days), locations=store.locations, listeners=[])
        original = SimpleNamespace(root=store.root, clock=store.clock,
            user_prefs=store.user_prefs, catalogue=store.catalogue)
        return pinned, original, store.journal.root, self.identity.today()

    async def _run(self, owner, captured, method=None, args=(), kwargs=None):
        pinned, store, journal_root, date = captured
        live = self.identity
        async with self.lock:
            try:
                if self.closed:
                    raise ProjectLocationError('This identity host has closed')
                state, given, result = await asyncio.to_thread(
                    self._worker, pinned, store, journal_root, date, method, args, kwargs or {})
            except Exception as error:
                if owner == self.owner():
                    self.ready, self.error = False, str(error)
                raise
            if not self.closed and owner == self.owner():
                for field in FIELDS:
                    setattr(live, field, state[field])
                live._given = max(live._given, given)
                self.ready, self.error = True, None
                if live._following():
                    live.start()
            return result

    def _worker(self, pinned, original, journal_root, date, method, args, kwargs):
        from .identity import Identity
        from .day.store import SessionStore
        from .project_repository import verify_owner
        root = pinned.locations.project(pinned.sid)
        if pinned.locations.root_for(pinned.sid) != journal_root:
            raise ProjectLocationError('The captured Project location changed')
        if method is not None:
            pinned.locations.writable(pinned.sid)
            if root is not None:
                pinned.locations.check_writer(root)
                verify_owner(root)
            # Detached SessionStore reconstructs existing accepted journal data;
            # it cannot follow the original store's later UI selection.
            pinned = SessionStore(original.root, original.clock, session=pinned.sid,
                prefs=original.user_prefs, catalogue=original.catalogue, locations=pinned.locations)
        view = Identity(self.identity.root, today=lambda: date, store=pinned,
                        _events=self.identity._project_events)
        if pinned.locations.project(pinned.sid) != root or pinned.locations.root_for(pinned.sid) != journal_root:
            raise ProjectLocationError('The captured Project location changed')
        result = getattr(view, method)(*args, **kwargs) if method is not None else None
        return {field: getattr(view, field) for field in FIELDS}, view._given, result
