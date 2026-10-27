"""A two-way pipe to a worker process (the GPU worker's pipe, generalised): our requests
get replies, the worker sends events, and the worker can ask us things (a fresh snapshot)."""
from __future__ import annotations

import asyncio
import itertools
import multiprocessing as mp
import threading
from typing import Awaitable, Callable


class WorkerGone(Exception):
    pass


class ProcessChannel:
    def __init__(self, target: Callable, name: str, on_event: Callable[[dict], None],
                 on_request: Callable[[dict], Awaitable[dict]], on_exit: Callable[[], None] | None = None) -> None:
        self.target = target
        self.name = name
        self.on_event = on_event
        self.on_request = on_request
        self.on_exit = on_exit
        self._conn = None
        self._process: mp.Process | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._ids = itertools.count(1)
        self._loop: asyncio.AbstractEventLoop | None = None
        self._send_lock = threading.Lock()
        self.alive = False

    async def start(self) -> None:
        ctx = mp.get_context("spawn")
        parent, child = ctx.Pipe()
        self._conn = parent
        self._process = ctx.Process(target=self.target, args=(child,), daemon=True, name=self.name)
        self._process.start()
        child.close()  # only the worker keeps this end, so its exit wakes the reader with EOF
        self._loop = asyncio.get_running_loop()
        self.alive = True
        threading.Thread(target=self._reader, daemon=True, name=self.name + "-reader").start()
        await self.call("ping", timeout=60)

    def stop(self) -> None:
        self.alive = False
        if self._conn is not None:
            try:
                self._conn.send(None)
            except (BrokenPipeError, OSError):
                pass
        if self._process is not None:
            self._process.join(timeout=3)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=3)
            if self._process.is_alive():
                self._process.kill()
                self._process.join(timeout=3)
            if self._process.is_alive():
                raise RuntimeError("research process termination not confirmed")

    def _send(self, message: dict) -> None:
        with self._send_lock:
            try:
                self._conn.send(message)
            except (BrokenPipeError, OSError) as exc:
                raise WorkerGone(str(exc)) from exc

    async def call(self, op: str, timeout: float = 10.0, **payload) -> dict:
        if not self.alive:
            raise WorkerGone("worker not running")
        request_id = next(self._ids)
        future = self._loop.create_future()
        self._pending[request_id] = future
        self._send({"id": request_id, "op": op, **payload})
        try:
            reply = await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(request_id, None)
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", f"{self.name} error"))
        return reply

    async def _answer(self, message: dict) -> None:
        try:
            reply = await self.on_request(message)
        except Exception as exc:
            reply = {"ok": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        try:
            self._send({"reply_to": message["request"], **reply})
        except WorkerGone:
            pass

    def _gone(self) -> None:
        self.alive = False
        for future in self._pending.values():
            if not future.done():
                future.set_exception(WorkerGone(f"{self.name} exited"))
        if self.on_exit is not None:
            self.on_exit()

    def _post(self, callback, *args) -> bool:
        try:
            self._loop.call_soon_threadsafe(callback, *args)
            return True
        except RuntimeError:
            if not self._loop.is_closed():
                raise
            self.alive = False
            return False  # the owner is gone; no live request can receive this late message

    def _reader(self) -> None:
        while True:
            try:
                message = self._conn.recv()
            except (EOFError, OSError):
                self.alive = False
                self._post(self._gone)
                return
            if "reply_to" in message:
                future = self._pending.get(message["reply_to"])
                if future is not None:
                    if not self._post(lambda f=future, m=message: f.done() or f.set_result(m)):
                        return
            elif "request" in message:
                if not self._post(lambda m=message: asyncio.ensure_future(self._answer(m))):
                    return
            elif "event" in message:
                if not self._post(self.on_event, message):
                    return
