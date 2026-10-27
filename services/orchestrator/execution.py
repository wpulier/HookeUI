"""Bounded execution facts for UI inspection. Never chat memory or model input.

No I/O, awaits, images or model text on the recording path. ContextVars keep concurrent
and late tasks with their request. Inspection alone builds wire dictionaries. Collection
is on for new interactions; MICROMIND_EXECUTION_INSPECT=0 disables it.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
import os
import time
import uuid
import weakref

FIELDS = {"selected", "selection", "adapter", "model", "provider", "endpoint", "device",
          "identity", "producer", "fallback", "policy", "worker", "rpcId", "frameId",
          "errorType", "cache", "trialId", "arm", "scope", "reportedModel", "objectId", "sessionId", "adapterMs", "role", "optionId",
          # The runner's facts (operations.py; the scaffolding's interface, signed off 2026-10-07): the one
          # execution identity, the operation, the capture it ran on, the evidence it made, who asked.
          "executionId", "operation", "captureId", "evidenceId", "origin",
          # Optional model diagnostics are primitives only, never prompts or replies.
          "workerStartedAt", "workerFinishedAt",
          "probeEmbedWallMs", "probeEmbedThreadMs", "probeSubmitWallMs", "probeSubmitThreadMs",
          "probeReadbackWallMs", "probeReadbackThreadMs", "probeWallMs", "probeProcessMs",
          "probeMinorFaults", "probeMajorFaults", "probeEmbeddingHit",
          "requestId", "attemptId", "retryOf", "phase", "outputCap", "inputTokens", "outputTokens", "thinkingTokens",
          "finishReason", "responseStatus", "parseOk", "promptDigest", "promptBytes",
          "rawActionCount", "actionCount", "targetCount", "planDigest", "planOmitted",
          "candidateCount", "groundingCount", "outputChars", "sourceHost", "sourceDigest", "rejection",
          "webSearchQueryCount", "returnedMatchCount", "acceptedMatchCount", "rejectedMatchCount", "matchFields"}
_current: ContextVar[Scope | None] = ContextVar("execution_scope", default=None)


def _facts(values: dict) -> dict:
    return {k: v[:128] if isinstance(v, str) else v for k, v in values.items()
            if k in FIELDS and isinstance(v, (str, int, float, bool, type(None)))}


@dataclass(slots=True, weakref_slot=True)
class Request:
    id: str
    kind: str
    at: float
    day: str = ""
    thread: str = ""
    generation: int = 0
    turn_id: int | None = None
    object_ids: list[str] = field(default_factory=list)
    trial_id: str | None = None
    outcome: str = "running"
    incomplete: bool = False
    revision: int = 0
    steps: list[dict] = field(default_factory=list)
    closed: bool = False
    revealed: bool = False
    context: dict = field(default_factory=dict)
    tracking_stopped: bool = False
    cached: bool = True
    session: str = ""  # the session it was made in (ADR 0017); "day" carries the same id for the studio

    def reference(self) -> dict:
        # No adapter names here: this small event is safe even during a blind trial.
        return {"id": self.id, "kind": self.kind, "at": self.at, "day": self.day, "session": self.session or self.day,
                "thread": self.thread,
                "generation": self.generation, "turnId": self.turn_id, "objectIds": list(self.object_ids),
                "trialId": self.trial_id, "outcome": self.outcome, "revision": self.revision,
                "incomplete": self.incomplete}


class Scope:
    def __init__(self, log=None, root=None, row=None, parent=None, suppress=False):
        self.log, self.root, self.row, self.parent = log, root, row, parent
        self.id = row["id"] if row is not None else root.id if root else None
        self.last_producer = None
        self._token = None
        self._start = 0.0
        self._suppress = suppress

    def __enter__(self):
        if self.root is not None:
            if not self.active:
                self._token = _current.set(self if self.retaining else None)
                return self
            try:
                self._start = time.perf_counter()
                self._token = _current.set(self)
            except Exception:
                self.root.incomplete = True
                self.root = None
                self._token = _current.set(None)
        elif self._suppress:
            self._token = _current.set(None)
        return self

    @property
    def retaining(self):
        return self.root is not None and self.log.archive is not None

    @property
    def active(self):
        return (self.root is not None and self.log.enabled
                and not self.root.tracking_stopped
                and (self.root.cached or self.log.archive is not None))

    def finish(self, outcome="completed", **facts):
        if not self.active:
            if self.root is not None and self.log.archive is not None and self.root.tracking_stopped:
                if self.row is not None:
                    self.row['outcome'] = outcome
                else:
                    self.root.outcome = outcome
            return
        if self.row is not None:
            try:
                self.row.update(_facts(facts), outcome=outcome)
            except Exception:
                self.root.incomplete = True
        elif self.root is not None:
            self.root.outcome = outcome

    def __exit__(self, typ, value, tb):
        if self.root is None:
            if self._token is not None:
                try:
                    _current.reset(self._token)
                except Exception:
                    pass
            return False
        try:
            if not self.active:
                # Turning details off stops new spans/timing, not the essential
                # terminal receipt for work already in flight.
                if self.log.archive is not None and self.root.tracking_stopped:
                    outcome = ('cancelled' if typ and issubclass(typ, asyncio.CancelledError)
                               else 'failed' if typ else 'completed')
                    if self.row is not None:
                        if typ or self.row['outcome'] == 'running':
                            self.row['outcome'] = outcome
                    else:
                        if typ or self.root.outcome == 'running':
                            self.root.outcome = outcome
                        self.root.closed = True
                    self.root.revision += 1
                    self.log._persist(self.root)
                return False
            outcome = "cancelled" if typ and issubclass(typ, asyncio.CancelledError) else "failed" if typ else "completed"
            if self.row is not None:
                if typ:
                    self.finish(outcome, errorType=typ.__name__)
                elif self.row["outcome"] == "running":
                    self.finish()
                self.row["ms"] = round((time.perf_counter() - self._start) * 1000, 3)
                if self.parent is not None:
                    self.parent.last_producer = self.row.get("producer") or self.id
            else:
                if typ or self.root.outcome == "running":
                    self.root.outcome = outcome
                self.root.closed = True
            self.root.revision += 1
            self.log._persist(self.root)
            if self.row is None or self.root.closed:
                self.log._notify(self.root)
        except Exception:
            self.root.incomplete = True
        finally:
            if self._token is not None:
                try:
                    _current.reset(self._token)
                except Exception:
                    pass
        return False  # a primary exception/cancellation always propagates unchanged


_OFF = Scope()


class ExecutionLog:
    def __init__(self, *, enabled=False, max_steps=2000, max_roots=256, max_per_root=64, publish=None, archive=None):
        self.enabled = enabled
        self.max_steps, self.max_roots, self.max_per_root = max_steps, max_roots, max_per_root
        self.publish = publish
        self.archive = archive
        self.context_provider = None  # memory-only, allowlisted build/model identity at request admission
        self._boot = uuid.uuid4().hex[:12]
        self._sequence = 0
        self._roots: OrderedDict[str, Request] = OrderedDict()
        # Outstanding scopes own their bounded trees after UI-cache eviction.
        # Weak references let the switch stop those collectors without keeping
        # every historical request resident.
        self._live = weakref.WeakValueDictionary()
        self._objects: dict[tuple[int, str], str] = {}
        self._steps = 0
        self._pending: dict[str, None] = {}
        self._drain_scheduled = False
        self._drain_handle = None
        self.dropped = 0

    @classmethod
    def from_env(cls, **kwargs):
        return cls(enabled=os.environ.get("MICROMIND_EXECUTION_INSPECT", "1") != "0", **kwargs)

    def set_enabled(self, enabled: bool):
        """Runtime diagnostic switch, never a model setting or a conversation event.

        Clearing invalidates scopes already held by unfinished tasks, even if collection
        is enabled again before they return. Their primary work is never cancelled.
        """
        self.enabled = enabled
        if enabled:
            return
        if self._drain_handle is not None:
            self._drain_handle.cancel()
            self._drain_handle = None
        self._drain_scheduled = False
        self._pending.clear()
        for root in list(self._live.values()):
            root.tracking_stopped = True
            self._persist(root)
        while self._roots:
            self._evict()
        self._objects.clear()
        self._steps = 0

    def request(self, kind, *, day="", thread="", generation=0, client_id=None, resume=None, session=""):
        if not self.enabled and self.archive is None:
            return Scope(suppress=True) if _current.get() is not None else _OFF
        try:
            root = self._roots.get(resume) if resume else None
            if root is None:
                while len(self._roots) >= self.max_roots:
                    self._evict()
                self._sequence += 1
                root = Request(f"e{self._boot}-{self._sequence}", str(kind)[:32], time.time(),
                               str(day)[:32], str(thread)[:32], generation, session=str(session or day)[:32])
                root.tracking_stopped = not self.enabled
                if self.context_provider is not None:
                    try:
                        root.context.update(self.context_provider())
                    except Exception:
                        root.incomplete = True
                self._roots[root.id] = root
                self._live[root.id] = root
                self._persist(root)
            return Scope(self, root)
        except Exception:
            self.dropped += 1
            return _OFF

    def _evict(self):
        rid, root = self._roots.popitem(last=False)
        self._persist(root)
        self._steps -= len(root.steps)
        root.cached = False
        for oid in root.object_ids:
            if self._objects.get((root.generation, oid)) == rid:
                self._objects.pop((root.generation, oid), None)
        self._pending.pop(rid, None)
        if self.archive is None:
            root.steps.clear()

    def _span(self, operation, parent, facts):
        root = parent.root
        if not parent.active:
            return Scope(suppress=True)
        if len(root.steps) >= self.max_per_root:
            root.incomplete = True
            self.dropped += 1
            return Scope(suppress=True)
        while root.cached and self._steps >= self.max_steps:
            if next(iter(self._roots)) == root.id:
                root.incomplete = True
                self.dropped += 1
                return Scope(suppress=True)
            self._evict()
        row = {"id": f"{root.id}.{len(root.steps) + 1}", "parent": parent.id,
               "operation": str(operation)[:48], "outcome": "running", **_facts(facts)}
        root.steps.append(row)
        if root.cached:
            self._steps += 1
        self._persist(root)
        return Scope(self, root, row, parent)

    def _snapshot(self, root):
        return {"status": "blind" if root.trial_id and not root.revealed else "available",
                "root": root.reference(), "steps": [dict(s) for s in root.steps],
                "closed": root.closed and not any(s['outcome'] == 'running' for s in root.steps),
                "trackingStopped": root.tracking_stopped, "context": dict(root.context)}

    def _persist(self, root):
        if self.archive is not None:
            try:
                if not self.archive.offer(self._snapshot(root)):
                    root.incomplete = True
            except Exception:
                root.incomplete = True
                self.dropped += 1

    def _notify(self, root):
        if not self.enabled or self.publish is None or self._roots.get(root.id) is not root:
            return
        # Coalesce late child completions. Serialization is not part of an action's await.
        if root.id in self._pending:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no UI loop: retain the receipt without calling a sink on this thread
        else:
            self._pending[root.id] = None
            if not self._drain_scheduled:
                self._drain_scheduled = True
                self._drain_handle = loop.call_later(0.05, self._drain)

    def _drain(self):
        # One scheduled callback, bounded by the roots, four optional hints per 50 ms.
        # A burst never allocates one event-loop callback per request or monopolises a tick.
        self._drain_scheduled = False
        self._drain_handle = None
        if not self.enabled:
            return
        for rid in tuple(self._pending)[:4]:
            self._send(rid)
        if self._pending:
            self._drain_scheduled = True
            self._drain_handle = asyncio.get_running_loop().call_later(0.05, self._drain)

    def _send(self, rid):
        self._pending.pop(rid, None)
        root = self._roots.get(rid)
        if self.enabled and root is not None:
            try:
                self.publish({"type": "execution", "entry": root.reference()})
            except Exception:
                self.dropped += 1

    def references(self, thread=None, generation=None):
        """Live connection/broadcast snapshots are memory-only, never disk I/O."""
        return [r.reference() for r in self._roots.values() if (thread is None or r.thread == thread)
                and (generation is None or r.generation == generation)]

    def for_object(self, object_id, generation=0):
        return self._objects.get((generation, object_id))

    def reveal(self, trial_id):
        for root in list(self._live.values()):
            if root.trial_id == trial_id:
                root.revealed = True
                self._persist(root)
        if self.archive is not None:
            self.archive.reveal(trial_id)

    def inspect(self, rid):
        root = self._roots.get(rid)
        snapshot = self._snapshot(root) if root is not None else self.archive.inspect(rid) if self.archive is not None else None
        if snapshot is None:
            return {"status": "expired", "steps": []}
        if snapshot['status'] == 'blind':
            return {"status": "blind", "steps": [], "root": snapshot['root']}
        if self.archive is not None:
            try:
                snapshot['markers'] = self.archive.markers(rid)
            except (OSError, ValueError):
                snapshot['markerError'] = 'Saved problem markers could not be read.'
        steps = snapshot['steps']
        # Catalogue labels are resolved only when inspected, never on an action's path.
        from .execution_powers import powers
        return {**snapshot, "powers": powers(steps)}


def span(operation, **facts):
    current = _current.get()
    if current is None or not current.active:
        return Scope(suppress=True) if current is not None else _OFF
    try:
        return current.log._span(operation, current, facts)
    except Exception:
        current.root.incomplete = True
        return Scope(suppress=True)


def note(**facts):
    current = _current.get()
    if current and current.active and current.row is not None:
        try:
            current.row.update(_facts(facts))
        except Exception:
            current.root.incomplete = True


def finish(outcome="completed", **facts):
    current = _current.get()
    if current:
        current.finish(outcome, **facts)


def producer():
    current = _current.get()
    return current.last_producer if current and current.active else None


def bind(*, turn_id=None, object_id=None, trial_id=None, day=None, thread=None, generation=None, words=None, owner=None):
    current = _current.get()
    if current is None or not (current.active or current.retaining):
        return
    try:
        root, log = current.root, current.log
        if turn_id is not None:
            root.turn_id = turn_id
        if day is not None:
            root.day = root.session = str(day)[:32]
        if thread is not None:
            root.thread = str(thread)[:32]
        if generation is not None:
            root.generation = generation
        if trial_id is not None:
            root.trial_id = str(trial_id)[:32]
        if object_id is not None and object_id not in root.object_ids and len(root.object_ids) < 64:
            oid = str(object_id)[:32]
            root.object_ids.append(oid)
            if root.cached:
                log._objects[(root.generation, oid)] = root.id
        if object_id is not None and current.row is not None:
            current.row["objectId"] = str(object_id)[:32]
        if object_id is not None and object_id not in root.object_ids:
            root.incomplete = True
        if isinstance(words, str):
            root.context['words'] = words[:8000]
            if len(words) > 8000:
                root.incomplete = True
        if isinstance(owner, dict):
            root.context['owner'] = {key: value[:256] for key, value in owner.items()
                if key in {'workspaceId', 'clientId', 'pageEpoch', 'connectionEpoch', 'voiceSessionId', 'requestId'}
                and isinstance(value, str)}
        log._persist(root)
    except Exception:
        current.root.incomplete = True


def root_id():
    current = _current.get()
    return current.root.id if current and (current.active or current.retaining) else None


def recording():
    current = _current.get()
    return current is not None and current.active


def request(hub, kind):
    """Failure-isolated optional root for a UI command or a background transition."""
    if _current.get() is not None:
        return _OFF
    try:
        log = getattr(hub, "execution", None)
        if log is not None and (log.enabled or log.archive is not None):
            day = getattr(hub, "execution_day", None)
            return log.request(kind, day=getattr(day, "sid", ""), thread=getattr(day, "active_id", ""),
                               generation=getattr(day, "generation", 0), session=getattr(day, "sid", ""))
    except Exception:
        pass
    return _OFF


def traced(operation, *, request=None):
    """Add a scope without changing a result, exception, prompt or caller's arguments."""
    def decorate(function):
        @wraps(function)
        async def call(*args, **kwargs):
            root_scope = _OFF
            if _current.get() is None and request is not None:
                try:
                    owner = args[0]
                    brain = getattr(owner, "brain", None)
                    hub = getattr(owner, "hub", None) or getattr(brain, "hub", None)
                    log = getattr(hub, "execution", None)
                    if log is not None and (log.enabled or log.archive is not None):
                        conversation = getattr(owner, "conversation", None) or getattr(brain, "conversation", None)
                        day = getattr(conversation, "store", None) or getattr(hub, "execution_day", None)
                        handled = kwargs.get("handled") or {}
                        kind = "voice" if request == "ask" and (kwargs.get("channel") == "voice" or len(args) > 2 and args[2] == "voice") else request
                        root_scope = log.request(kind, day=getattr(day, "sid", ""), thread=getattr(day, "active_id", ""),
                                                 generation=getattr(day, "generation", 0), resume=handled.get("execution_id"),
                                                 session=getattr(day, "sid", ""))
                except Exception:
                    pass
            with root_scope:
                if not recording():
                    return await function(*args, **kwargs)
                with span(operation):
                    if request == "voice" or request == "ask" and (kwargs.get("channel") == "voice" or len(args) > 2 and args[2] == "voice"):
                        try:
                            owner = args[0]
                            voice = getattr(owner, "voice", None)
                            config = getattr(voice, "config", None)
                            if config is not None:
                                note(model=config.model, provider="openai", adapter="voice.openai_live.OpenAILiveSession",
                                     cache="existing-live-session", sessionId=getattr(voice, "session_id", None),
                                     identity="configured live session; hosted revision unconfirmed")
                        except Exception:
                            pass
                    result = await function(*args, **kwargs)
                    if request == "voice" and isinstance(result, dict):
                        # Internal hands hand-off only. Never a model context or public result.
                        result["execution_id"] = root_id()
                    return result
        return call
    return decorate
