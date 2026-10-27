"""Async client for the GPU worker process (services/gpu_worker/worker.py).

Scheduling with bounds it can keep (docs/design/2026-10-06-captures-operations-evidence.md,
contract 5):
- every call says its priority class: "click" (an explicit click: the newest pending click runs
  next), "interactive" (other short work), "background" (long or optional work), "research" (a
  researcher's own operations, research/hands.py: served only when nothing of the app's own waits,
  bounded apart, and never superseding a call made for the user);
- every call carries a deadline: a call still queued in the worker past it is answered "expired"
  without running (a running call is never claimed to be cancelled);
- pending calls are bounded per op and priority class: explicit clicks are not bounded here (each
  holds its view in the interactive memory allowance, which refuses a click visibly when too many are
  pending), the app's own short work and background work have their own bounds, over which a call is
  refused with the reason (`Busy`); a background kind (automatic masks, tracking) keeps only its
  newest call (older queued ones are dropped by the worker and answered "superseded"). The app
  outlining 15 things never refuses a click (scaffolding review, M1.2 item 2);
- the worker says when it starts each call; a call running past its hard limit (its timeout plus a
  grace) from then marks the worker hung (a call still queued behind a long one never does): new
  calls fail fast with the reason, the capabilities that need it report it, and the brain is told
  once, and again when it recovers;
- a call given up (cancelled, or timed out) that the worker has not started is dropped from its queue.

Nothing here holds the event loop (live 2026-10-11 02:38-02:39: one frame's publish took 46.8 s and the
follow worker was reported hung after 42 s instead of 15, under load 142 while micro-sam loaded):
- requests are pickled and written by a writer thread fed by a queue, so a worker slow to drain its pipe
  (starved, or busy loading a model) never blocks the loop; a request given up or superseded before it
  was written is never sent;
- replies are read on a reader thread, which also notes at once when a call started, finished or is
  loading a model, so the hang check never waits on a busy loop;
- the hang check is a thread too; what it tells reaches the loop through call_soon_threadsafe.

A worker that dies (killed, crashed: L7e 2026-10-20, `kill -9` of the click worker left every click on "Loading the
outline model" for good) fails its calls at once, saying it stopped, and one that started with the app (the click
worker) is started again by itself (`_respawn`), with backoff (RESPAWN_S) and at most RESPAWN_MAX times in
RESPAWN_WINDOW_S: while it restarts, calls fail fast saying so (`problem`, WorkerRestarting), the conversation and
the log are told as for a hang and again when it is back (`on_problem`), and Activity has a line, running then done or
failed. The workers started on first use (analysis, following, Molmo) start again on their next use, as before.

A model loading is said, never mistaken for a hang (gpu_worker/loading.py): the worker tells when a load
starts and ends; while it loads, the call's hang limit is the load's own, and its timeout is moved by the
load's length, so a call's timeout is for its run (micro-sam's cold start of 120 s once counted against the
180 s outline timeout). Loads are told to `loads` (orchestrator/loads.py), which says them, keeps their
measured times and sizes, and frees memory before one that would not fit.
"""
from __future__ import annotations

import asyncio
import contextvars
import itertools
import multiprocessing as mp
import os
import queue
import signal
import threading
import time
from . import execution
from gpu_worker.loading import limit_s, model_of
from .loads import model_name

PRIORITIES = ("click", "interactive", "background", "research")
URGENT_OPS = {"ping", "segment"}  # the priority of a call that does not say (as before the classes)
# Pending calls per op (of one priority class) beyond which one is refused (with the reason) or, for a
# kind that keeps only its newest, the older queued one is dropped. Clicks ("click") are not bounded
# here: the interactive memory allowance bounds them. The app's own outlines (a look's 15 targets, an
# arena ask's arms) are "interactive" segments, bounded at 24.
LIMITS = {"segment": 24, "regions": 4, "cells": 2, "automask": 1, "embed": 8, "zeroshot": 4, "track": 1,
          "molmo_read": 2, "molmo_point": 2}
NEWEST_ONLY = {"automask", "track"}
GRACE_S = 5.0  # past its timeout plus this, a running call means the worker is hung
RESPAWN_S = (1.0, 2.0, 5.0, 10.0, 30.0)  # waits before each try at starting a dead worker again
RESPAWN_MAX = 5  # restarts at most this many times in RESPAWN_WINDOW_S (a worker that dies at once is not restarted forever)
RESPAWN_WINDOW_S = 600.0
WATCH_S = 1.0  # how often the hang check looks
WAKE_S = 1.0  # how often a waiting call looks at its deadline (a load moves it later, and back when it ends)
# Told when the worker takes a call from its queue (the runner's job then counts as begun: contract 4,
# a queued job is dropped when nobody needs it, a begun one completes).
STARTED: contextvars.ContextVar = contextvars.ContextVar("gpu_started", default=None)
# The role each of the app's workers starts with (gpu_worker/boot.py: its thread cap and priority); a
# client with another name (a bench's own worker) starts as before.
ROLES = {"micromind-gpu": "main", "micromind-gpu-follow": "follow", "micromind-gpu-heavy": "heavy",
         "micromind-gpu-molmo": "molmo", "micromind-gpu-replay": "heavy"}


class Busy(RuntimeError):
    """Too many calls of this kind are already waiting; the message says so."""


class Superseded(RuntimeError):
    """A newer call of the same kind replaced this one before it ran."""


class WorkerHungError(RuntimeError):
    """The worker was already marked hung: the call fails fast and is never sent (never run)."""


class WorkerRestarting(WorkerHungError):
    """The worker stopped and is being started again: the call fails fast and is never sent."""


class ExpiredError(TimeoutError):
    """It waited in the worker's queue past its deadline and was never run (a TimeoutError, as before)."""


class _OwnedChildren:
    """Model processes stay owned after EOF and while their worker is retiring."""
    def __init__(self):
        self.pids = {}
        self.stopping = False
        self.lock = threading.RLock()

    @staticmethod
    def terminate(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def event(self, reply):
        model = reply.get('model')
        with self.lock:
            if reply.get('load') == 'unloaded':
                self.pids.pop(model, None)
            elif reply.get('load') == 'loaded' and reply.get('pid'):
                pid = int(reply['pid'])
                self.pids[model] = pid
                if self.stopping:
                    self.terminate(pid)
                    self.pids.pop(model, None)

    def stop(self):
        with self.lock:
            self.stopping = True
            for pid in self.pids.values():
                self.terminate(pid)
            self.pids.clear()  # Do not signal an exited/reused PID on another stop.


class GpuClient:
    """One GPU worker process. The main one serves clicks and warms up the outline model at
    start; a "heavy" one (warm=False) is started on first use for long single-call jobs
    (Cellpose) so they never hold up a click."""

    LABELS = {"micromind-gpu": "outline", "micromind-gpu-heavy": "analysis", "micromind-gpu-follow": "following",
              "micromind-gpu-molmo": "Molmo"}

    def __init__(self, name: str = "micromind-gpu", warm: bool = True, role: str | None = None) -> None:
        self.name = name
        self.warm = warm
        self.role = role or ROLES.get(name)
        self.hung = ""  # what has been running past its limit, while it is
        self.warm_error = ""  # why the warm-up at start could not load the outline model ahead, if it could not
        self.on_problem: list = []  # (text) -> None, told once when the worker is found hung, and ("") when it recovers
        # id -> [op, priority class, hard limit, when the worker started it (None while queued), started callback],
        # until replied
        self._outstanding: dict[int, list] = {}
        self._loading_for: dict[int, dict] = {}  # id -> the load it is waiting on: {"model", "since", "limitS"}
        self._deadlines: dict[int, float] = {}  # id -> when its caller gives up (monotonic), moved by a load
        self._contexts: dict[int, contextvars.Context] = {}  # id -> its caller's context (a load is told in it)
        self._starting: asyncio.Task | None = None
        self._conn = None
        self._process: mp.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send_lock = threading.Lock()
        self._outbox: queue.SimpleQueue | None = None  # the writer thread's queue (None: written at once, as tests do)
        self._unsent: dict[int, dict] = {}  # requests queued for the writer, not yet written
        self._watching: threading.Event | None = None  # set to stop the hang check's thread
        self.loads = None  # loads.Loads: said, measured and kept within memory (App.build); None: not told
        self.own: str | None = None  # the model this worker is for alone (hub.host_for): letting it go stops the worker
        self.loading: dict[str, dict] = {}  # model -> {"since", "limitS"} while the worker loads it
        self.resident: dict[str, dict] = {}  # model -> {"sizeMb", "seconds", "usedAt"}: what the worker holds
        self.children: dict[str, int] = {}  # model -> pid of a process the worker started for it (micro-sam)
        self._owned_children = _OwnedChildren()
        self.ready = False
        self.device = ""
        self.restarting = ""  # why calls fail fast while a dead worker is started again (or could not be)
        self.restarts: list[float] = []  # when it was started again (monotonic), within RESPAWN_WINDOW_S
        self._stopping = False  # stop() was asked: an exit is expected, never restarted
        self._respawning: asyncio.Task | None = None
        self._exits = 0  # the worker's exits seen (`_exited`): a restart is good only if none happened while it started

    async def start(self) -> None:
        """Start the worker process, once: a start under way is waited for, never repeated; a start
        that failed (or a worker that exited) may be made again."""
        if self.ready:
            return
        if self._starting is None or self._starting.done():
            self._starting = asyncio.ensure_future(self._start())
        await asyncio.shield(self._starting)

    async def _start(self) -> None:
        from gpu_worker.boot import serve, spawn_env

        self._stopping = False
        _STARTING.set(True)  # this start's own ping and warm-up go through while a restart is said
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self._conn = parent
        self._owned_children = _OwnedChildren()
        self._process = ctx.Process(target=serve, args=(child, self.role), daemon=True, name=self.name)
        with spawn_env(self.role):
            self._process.start()
        child.close()  # the worker has its own end now: when it exits, the reader sees it (EOF) and its calls fail
        self._loop = asyncio.get_running_loop()
        self._outbox = queue.SimpleQueue()
        threading.Thread(target=self._writer, args=(self._outbox, parent), daemon=True, name=f"{self.name}-writer").start()
        threading.Thread(target=self._reader, args=(parent, self._owned_children), daemon=True, name=f"{self.name}-reader").start()
        self.watch()
        info = await self.call("ping", timeout=120)
        self.device = info.get("device", "")
        if self.warm:
            await self.warm_up()
        self.ready = True

    async def warm_up(self) -> None:
        """Load the outline model ahead, at start, before anyone clicks (measured, not said). A head start, never a gate:
        when it can't (not downloaded yet: gpu_worker/model_files.py; or broken), the worker still serves, and the first
        click loads it then or says why it can't (2026-10-12: a warm-up that raised left `ready` False for good, and every
        click waited on "Loading the outline model")."""
        try:
            await self.call("warmup", timeout=300, _quiet=True)
            self.warm_error = ""
        except Exception as exc:  # noqa: BLE001
            self.warm_error = f"{type(exc).__name__}: {exc}"[:300]

    def processes(self) -> list[dict]:
        """This worker's process, and those it started for a model (micro-sam's Python), with their roles and
        what is loading there, for the performance meter (machine.py). Read from the meter's thread."""
        process = self._process
        if process is None or not process.is_alive():
            return []
        label = f"{model_name(self.own)} worker" if self.own else f"{self.LABELS.get(self.name, self.name)} worker"
        loading = ", ".join(model_name(m) for m in list(self.loading))
        out = [{"pid": process.pid, "role": label, "worker": self.name, **({"loading": loading} if loading else {})}]
        for model, pid in list(self.children.items()):
            out.append({"pid": pid, "role": f"{model_name(model)} ({label})", "worker": self.name, "model": model,
                        **({"loading": model_name(model)} if model in self.loading else {})})
        return out

    def wire(self) -> dict:
        """For /api/health: started, hung, what is loading and what is held."""
        now = time.monotonic()
        return {"name": self.name, "role": self.role, "ready": self.ready, "hung": self.hung,
                "loading": {m: round(now - s.get("since", now)) for m, s in list(self.loading.items())},
                "resident": sorted(self.resident)}

    def clicks_waiting(self) -> bool:
        """Whether a click is being answered here now (asked from any thread: a download waits while one is)."""
        try:
            return any(entry[1] == "click" for entry in list(self._outstanding.values()))
        except RuntimeError:  # changed while read: ask again next time
            return False

    def problem(self) -> str:
        """Why calls to this worker fail fast now ("" when they do not)."""
        label = self.LABELS.get(self.name, self.name)
        if self.restarting:
            return self.restarting
        return f"the {label} worker is not responding ({self.hung})" if self.hung else ""

    # --- the hang check ----------------------------------------------------------------
    def watch(self) -> None:
        """Start the hang check's thread (once)."""
        if self._watching is not None and not self._watching.is_set():
            return
        self._watching = threading.Event()
        threading.Thread(target=self._watch, args=(self._watching,), daemon=True, name=f"{self.name}-watch").start()

    def unwatch(self) -> None:
        if self._watching is not None:
            self._watching.set()

    def _watch(self, stop: threading.Event) -> None:
        while not stop.wait(WATCH_S):
            self.check_hung()

    def check_hung(self, now: float | None = None) -> None:
        """Mark the worker hung, or recovered, from what it is running now. Only calls the worker has started
        count (one queued behind a 70 s first model load is not hung); a call waiting on a model's load has
        that load's own limit, counted from when the load began."""
        now = time.monotonic() if now is None else now
        overdue = []
        for rid, entry in list(self._outstanding.items()):
            load = self._loading_for.get(rid)
            if load is not None:
                if now - load["since"] > load["limitS"] + GRACE_S:
                    overdue.append((f"loading {model_name(load['model'])}", now - load["since"]))
            elif entry[3] is not None and now - entry[3] > entry[2]:
                overdue.append((entry[0], now - entry[3]))
        if overdue and not self.hung:
            op, age = max(overdue, key=lambda o: o[1])
            self.hung = f"{op} running for {age:.0f} s"
            self._tell_soon(self.problem())
        elif not overdue and self.hung:
            self.hung = ""
            self._tell_soon("")  # recovered: what needs it is offered again

    def _tell_soon(self, text: str) -> None:
        loop = self._loop
        if loop is None:
            self._tell(text)
            return
        try:
            loop.call_soon_threadsafe(self._tell, text)
        except RuntimeError:
            pass  # the loop is closed

    def _tell(self, text: str) -> None:
        for tell in list(self.on_problem):
            try:
                tell(text)
            except Exception:
                pass

    async def ensure_started(self) -> "GpuClient":
        """Start on first use (the heavy worker); a worker already started or starting is never
        started a second time (scaffolding review, M1.2 item 3)."""
        if not self.ready:
            await self.start()
        return self

    def stop(self) -> None:
        self._stopping = True
        try:
            current = asyncio.current_task()
        except RuntimeError:  # no loop running here (shutdown from a thread, a test)
            current = None
        if self._respawning is not None and self._respawning is not current and not self._respawning.done():
            self._respawning.cancel()  # the app stops: a restart under way ends with it
        self._reap()

    def _reap(self) -> None:
        """What is left of the worker ended: its stop message sent, its process joined (terminated, killed if need be),
        its model children stopped. Blocks up to about 5 s: a restart calls it in a thread."""
        process, conn, outbox = self._process, self._conn, self._outbox
        children, ownership = dict(self.children), self._owned_children
        for model, pid in children.items():
            ownership.event({'load': 'loaded', 'model': model, 'pid': pid})
        ownership.stop()
        self.unwatch()
        # A busy worker may not drain its pipe. Never wait for the writer's lock
        # on the shutdown thread: that would prevent the bounded terminate/reap.
        self._unsent.clear()
        if outbox is not None:
            outbox.put(None)
        elif conn is not None:
            # Standalone pipe users have no writer; give the stop message one.
            outbox = queue.SimpleQueue()
            outbox.put(None)
            threading.Thread(target=self._writer, args=(outbox, conn), daemon=True,
                             name=f"{self.name}-stop-writer").start()
        try:
            if process is not None:
                process.join(timeout=3)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=1)
                if process.is_alive():
                    process.kill()
                    process.join(timeout=1)
                if process.is_alive():
                    raise RuntimeError("GPU worker termination not confirmed")
            if self._process is process:
                self.ready = False
        finally:
            ownership.stop()
            for model, pid in children.items():
                if self.children.get(model) == pid:
                    self.children.pop(model, None)

    # --- sending -----------------------------------------------------------------------
    def _send(self, message: dict) -> None:
        """To the worker: through the writer thread once it runs (pickling a picture and writing it into a pipe
        the worker may be slow to drain never holds the loop), else at once (a stand-in connection in tests)."""
        if self._outbox is None:
            with self._send_lock:
                self._conn.send(message)
            return
        rid = message.get("id")
        if rid:
            self._unsent[rid] = message
        self._outbox.put(message)

    def _writer(self, outbox: queue.SimpleQueue, conn) -> None:
        while True:
            message = outbox.get()
            if message is None:
                try:
                    with self._send_lock:
                        conn.send(None)
                except (BrokenPipeError, OSError, ValueError):
                    pass
                return
            rid = message.get("id")
            if rid and self._unsent.pop(rid, None) is None:
                continue  # given up or superseded before it was written: never sent
            try:
                with self._send_lock:
                    conn.send(message)
            except (BrokenPipeError, OSError, ValueError) as exc:
                if rid and self._loop is not None:
                    try:
                        self._loop.call_soon_threadsafe(self._send_failed, rid, exc)
                    except RuntimeError:
                        return

    def _send_failed(self, rid: int, exc: Exception) -> None:
        self._forget(rid)
        future = self._pending.get(rid)
        if future is not None and not future.done():
            future.set_exception(RuntimeError(f"gpu worker exited ({type(exc).__name__})"))

    def _withdraw(self, rid: int) -> bool:
        """Take a request back before the writer wrote it (True), so it is never sent."""
        return self._unsent.pop(rid, None) is not None

    def _forget(self, rid: int) -> None:
        self._outstanding.pop(rid, None)
        self._loading_for.pop(rid, None)
        self._deadlines.pop(rid, None)
        self._contexts.pop(rid, None)

    @execution.traced("gpu")
    async def call(self, op: str, timeout: float = 30.0, priority: str | None = None, **payload) -> dict:
        if self.restarting and not _STARTING.get():
            raise WorkerRestarting(self.problem())
        if self.hung:
            raise WorkerHungError(self.problem())
        cls = priority if priority in PRIORITIES else ("click" if op in URGENT_OPS else "background")
        waiting = [rid for rid, entry in self._outstanding.items() if entry[0] == op and entry[1] == cls and rid in self._pending]
        limit = LIMITS.get(op)
        if waiting and op in NEWEST_ONLY:
            sent = []
            for rid in waiting:
                if self._withdraw(rid):  # never written: answered superseded here, never sent
                    self._forget(rid)
                    future = self._pending.get(rid)
                    if future is not None and not future.done():
                        future.set_result({"id": rid, "superseded": True, "error": "replaced by a newer one before it was sent"})
                else:
                    sent.append(rid)
            if sent:
                self._send({"id": 0, "op": "drop", "ids": sent})  # the worker skips them if still queued
        elif limit is not None and cls != "click" and len(waiting) >= limit and op != "ping":
            raise Busy(f"{len(waiting)} {op} requests of the app's own are already waiting; try again in a moment")
        model = model_of(op, payload)
        held = self.resident.get(model) if model is not None else None
        if held is not None:
            held["usedAt"] = time.monotonic()  # the memory guard lets go of the least recently used first
        elif model is not None and self.loads is not None and op not in ("unload", "ping"):
            await self.loads.before_load(self, model)  # memory for it, made before it loads (loads.py)
        quiet = bool(payload.pop("_quiet", False))  # a preload: measured, never said
        request_id = next(self._ids)
        if execution.recording():
            execution.note(selected=payload.get("model"), scope=op, worker=self.name, rpcId=request_id,
                           frameId=payload.get("frame_id"))
            payload["_execution"] = True
        payload["_priority"] = cls
        payload["_deadline"] = time.time() + timeout
        future = self._loop.create_future()
        self._pending[request_id] = future
        self._outstanding[request_id] = [op, cls, timeout + GRACE_S, None, STARTED.get()]
        deadline = time.monotonic() + timeout
        self._deadlines[request_id] = deadline
        context = contextvars.copy_context()
        if quiet:
            context.run(_QUIET.set, True)
        self._contexts[request_id] = context
        try:
            self._send({"id": request_id, "op": op, **payload})
        except (BrokenPipeError, ConnectionError, OSError, ValueError, AttributeError) as exc:
            # The worker went away (its pipe closed, no writer since): the call was never sent, and is forgotten.
            self._forget(request_id)
            self._pending.pop(request_id, None)
            self._unsent.pop(request_id, None)
            label = self.LABELS.get(self.name, self.name)
            raise RuntimeError(f"gpu worker exited: {self.problem() or f'the {label} worker is not running'} "
                               f"(never sent: {type(exc).__name__})") from None
        try:
            while not future.done():
                left = self._deadlines.get(request_id, deadline) - time.monotonic()
                if left <= 0:
                    raise asyncio.TimeoutError
                try:
                    await asyncio.wait_for(asyncio.shield(future), min(left, WAKE_S))
                except asyncio.TimeoutError:
                    pass  # look again: a model's load may have moved the deadline (either way)
            reply = future.result()
        except (asyncio.CancelledError, asyncio.TimeoutError) as exc:
            # Given up: if the worker has not started it, it is dropped from the queue (it never runs).
            entry = self._outstanding.get(request_id)
            if entry is not None and entry[3] is None:
                self._forget(request_id)
                if not self._withdraw(request_id):
                    try:
                        self._send({"id": 0, "op": "drop", "ids": [request_id]})
                    except (BrokenPipeError, OSError, AttributeError):
                        pass
                if isinstance(exc, asyncio.TimeoutError):  # it never ran: said so (outcomes never count it as run)
                    raise ExpiredError(f"{op} waited {timeout:.0f} s in the worker's queue and was never run") from None
            raise
        finally:
            self._pending.pop(request_id, None)
            self._deadlines.pop(request_id, None)
            self._contexts.pop(request_id, None)
        if reply.get("superseded"):
            raise Superseded(reply.get("error") or "replaced by a newer one before it ran")
        if reply.get("expired"):
            raise ExpiredError(reply.get("error") or f"{op} waited past its deadline in the worker's queue")
        try:
            facts = reply.pop("_execution", None)
            if isinstance(facts, dict):
                execution.note(**facts, adapterMs=reply.get("ms"))
        except Exception:
            pass
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "gpu worker error"))
        return reply

    # --- replies -----------------------------------------------------------------------
    def _fail_pending(self, why: str = "gpu worker exited") -> None:
        if self._outbox is not None:
            self._outbox.put(None)  # its writer ends with it (a restart makes its own)
            self._outbox, self._unsent = None, {}
        self._outstanding.clear()
        self._loading_for.clear()
        self.loading.clear()
        self.resident.clear()
        self.children.clear()
        self.hung = ""
        for future in self._pending.values():
            if not future.done():
                future.set_exception(RuntimeError(why))

    def _mark(self, reply: dict, ownership=None) -> list | None:
        ownership = ownership or self._owned_children
        with ownership.lock:
            ownership.event(reply)
            if ownership is not self._owned_children or ownership.stopping:
                return None
            return self._mark_active(reply)

    def _mark_active(self, reply: dict) -> list | None:
        """What a reply says about the call it is for, noted at once (on the reader thread, so the hang check
        never waits for a busy loop): when the worker started it, a model's load beginning or ending (its
        deadline moved by the load), or that it is answered. Returns the call's entry as it was."""
        rid = reply.get("id")
        entry = self._outstanding.get(rid)
        now = time.monotonic()
        if reply.get("started"):
            if entry is not None and entry[3] is None:
                entry[3] = now  # the worker began it: its hard limit counts from now
        elif reply.get("load"):
            model, state = reply.get("model"), reply.get("load")
            if state == "loading":
                load = {"model": model, "since": now, "limitS": float(reply.get("limitS") or limit_s(model))}
                self.loading[model] = load
                if entry is not None:
                    self._loading_for[rid] = {**load, "left": max(0.0, self._deadlines.get(rid, now) - now)}
                    self._deadlines[rid] = now + load["limitS"] + self._loading_for[rid]["left"]
            elif state in ("loaded", "failed"):
                self.loading.pop(model, None)
                load = self._loading_for.pop(rid, None)
                if state == "loaded" and model != "test-load":  # the harness's test load holds nothing
                    self.resident[model] = {"sizeMb": reply.get("memoryMb"), "seconds": reply.get("seconds"), "usedAt": now}
                    if reply.get("pid"):
                        self.children[model] = int(reply["pid"])
                if entry is not None and load is not None:
                    entry[3] = now  # the run's own limit counts from the end of the load...
                    self._deadlines[rid] = now + load["left"]  # ...and it has the time it had left
            elif state == "unloaded":
                self.resident.pop(model, None)
                self.children.pop(model, None)
                self.loading.pop(model, None)
        else:
            self._outstanding.pop(rid, None)
            self._loading_for.pop(rid, None)
        return entry

    def _replied(self, reply: dict, entry: object = None, marked: bool = False) -> None:
        if not marked:
            entry = self._mark(reply)
        # Observer timestamps share the harness wall-clock domain. Queue submission is
        # not execution: record only the worker's existing started/final replies.
        if not reply.get("load"):
            context = self._contexts.get(reply.get("id"))
            if context is not None:
                field = "workerStartedAt" if reply.get("started") else "workerFinishedAt"
                try:
                    context.run(execution.note, **{field: time.time()})
                except Exception:
                    pass  # observation never changes a worker result
        if reply.get("started"):
            if entry is not None and entry[4] is not None:
                try:
                    entry[4]()
                except Exception:
                    pass
            return
        if reply.get("load"):
            if self.loads is not None:
                context = self._contexts.get(reply.get("id"))
                try:
                    if context is not None:
                        context.run(self.loads.told, self, reply)
                    else:
                        self.loads.told(self, reply)
                except Exception:
                    pass  # saying a load never fails the call it is for
            return
        future = self._pending.get(reply.get("id"))
        if future is not None and not future.done():
            future.set_result(reply)

    def _exited(self, conn):
        if conn is not self._conn:
            return
        self.ready = False
        self._exits += 1
        expected = self._stopping
        how = self._exit_words(wait=False)  # on the loop: the exit code if it is already known, never a join
        label = self.LABELS.get(self.name, self.name)
        self._fail_pending("gpu worker exited" if expected else f"gpu worker exited: the {label} worker stopped ({how})")
        if not expected and self.warm and self._loop is not None:
            self._owned_children.stop()  # what the dead worker started for a model (micro-sam's Python) goes with it
            self.restarting = f"the {label} worker stopped ({how}); restarting the {label} model"  # calls fail fast now
            if self._respawning is None or self._respawning.done():
                self._respawning = self._loop.create_task(self._respawn(how))

    def _exit_words(self, wait: bool = True) -> str:
        """How the worker's process ended, in words ("killed, signal 9", "exit code 1"). `wait`: join it briefly first
        (in a thread: a restart's)."""
        process = self._process
        try:
            if process is not None and wait:
                process.join(timeout=0.2)
            code = process.exitcode if process is not None else None
        except Exception:
            code = None
        if code is None:
            return "its pipe closed"
        return f"killed, signal {-code}" if code < 0 else f"exit code {code}"

    async def _respawn(self, how: str) -> bool:
        """A worker that started with the app and died is started again, with backoff, while calls fail fast saying
        so; said as a hang is (`on_problem`) and in Activity. False when it could not be (or restarted too often)."""
        from . import activity

        label = self.LABELS.get(self.name, self.name)
        if how == "its pipe closed":  # its exit code was not known yet on the loop: asked again in a thread
            how = await asyncio.to_thread(self._exit_words)
        now = time.monotonic()
        self.restarts = [t for t in self.restarts if now - t < RESPAWN_WINDOW_S]
        hub = getattr(self.loads, "hub", None)
        key = f"worker:{self.name}:{len(self.restarts)}:{int(now)}"
        if len(self.restarts) >= RESPAWN_MAX:
            self.restarting = (f"the {label} worker stopped ({how}) and was restarted {len(self.restarts)} times in "
                               f"{RESPAWN_WINDOW_S / 60:.0f} min, so it is not restarted again; restart the app")
            self._tell(self.restarting)
            activity.activity(hub, "model", f"The {label} worker stopped and was not restarted", state="failed",
                              id=key, details={"lines": [self.restarting]})
            return False
        self.restarting = f"the {label} worker stopped ({how}); restarting the {label} model"
        self._tell(self.restarting)
        activity.activity(hub, "model", f"Restarting the {label} worker", state="running", id=key,
                          details={"lines": [f"It stopped ({how})"]})
        error = ""
        for wait in RESPAWN_S:
            await asyncio.sleep(wait)
            try:
                await asyncio.to_thread(self._reap)  # whatever is left of the dead one, off the loop (it joins)
                self.restarting = f"the {label} worker stopped ({how}); restarting the {label} model"
                exits = self._exits
                await self.start()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # tried again after the next wait
                error = f"{type(exc).__name__}: {exc}"[:200]
                continue
            process = self._process
            if self._exits != exits or process is None or not process.is_alive() or not self.ready:
                # It stopped again while it started (its warm-up says nothing of that): not running again, tried again.
                self.ready = False
                self.restarting = f"the {label} worker stopped ({how}); restarting the {label} model"
                error = "it stopped again while it started"
                continue
            self.restarts.append(time.monotonic())
            self.restarting = ""
            self._tell("")
            activity.activity(hub, "model", f"The {label} worker is running again", state="done", id=key,
                              details={"lines": [f"It stopped ({how}) and was started again"]})
            return True
        self.restarting = f"the {label} worker stopped ({how}) and could not be started again ({error}); restart the app"
        self._tell(self.restarting)
        activity.activity(hub, "model", f"The {label} worker could not be started again", state="failed", id=key,
                          details={"lines": [self.restarting]})
        return False

    def _reader(self, conn=None, ownership=None) -> None:
        conn = conn or self._conn
        ownership = ownership or self._owned_children
        while True:
            try:
                reply = conn.recv()
            except (EOFError, OSError):
                try:
                    self._loop.call_soon_threadsafe(self._exited, conn)
                except RuntimeError:
                    if not self._loop.is_closed():
                        raise
                    # The owner is gone; a late EOF has no live futures to notify.
                return
            if conn is not self._conn:
                ownership.event(reply)
                continue
            entry = self._mark(reply, ownership)
            try:
                self._loop.call_soon_threadsafe(self._replied, reply, entry, True)
            except RuntimeError:
                return  # the loop is closed


# Inside a worker's start (its task's own context): its ping and warm-up are sent while others fail fast (`restarting`).
_STARTING: contextvars.ContextVar = contextvars.ContextVar("gpu_starting", default=False)

# A preload's call (`_quiet`): its load is measured and logged, never said (loads.py reads it).
_QUIET: contextvars.ContextVar = contextvars.ContextVar("gpu_quiet_load", default=False)


def quiet() -> bool:
    return _QUIET.get()
