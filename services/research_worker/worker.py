"""The researcher process: runs researchers on its own asyncio loop, so their model calls,
web fetches and image work never touch the orchestrator's live loop.

Messages over the pipe (orchestrator/research/ipc.py is the other end):
  in   {"id", "op": "spawn", ...spec}   start a researcher (reply when started)
       {"id", "op": "cancel", "rid"}    cancel one
       {"id", "op": "ping"}
       {"id", "op": "keys", "set", "unset"}  a key added, replaced or removed in the app (key_admin.py)
       {"reply_to", ...}                answer to one of our requests
       None                             stop
  out  {"reply_to", "ok", ...}          answers
       {"event", "rid", ...}            status, step, cost, output, finding, correction, done
       {"request", "op", ...}           ask the orchestrator (snapshot; run: one of the app's operations)
"""
from __future__ import annotations

import asyncio
import itertools
import threading

from orchestrator.keys import apply_values, load_keys

from .engines import make_engine
from .researcher import Researcher


def serve(conn) -> None:
    load_keys()
    asyncio.run(_main(conn))


async def _main(conn) -> None:
    loop = asyncio.get_running_loop()
    inbox: asyncio.Queue = asyncio.Queue()
    send_lock = threading.Lock()
    pending: dict[int, asyncio.Future] = {}
    ids = itertools.count(1)
    tasks: dict[str, asyncio.Task] = {}

    def send(message: dict) -> None:
        with send_lock:
            try:
                conn.send(message)
            except (BrokenPipeError, OSError):
                pass

    def reader() -> None:
        while True:
            try:
                message = conn.recv()
            except (EOFError, OSError):
                message = None
            loop.call_soon_threadsafe(inbox.put_nowait, message)
            if message is None:
                return

    threading.Thread(target=reader, daemon=True, name="research-reader").start()

    async def request(op: str, timeout: float = 15, **payload) -> dict:
        request_id = next(ids)
        future = loop.create_future()
        pending[request_id] = future
        send({"request": request_id, "op": op, **payload})
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            return {"ok": False, "error": "the app did not answer"}
        finally:
            pending.pop(request_id, None)

    async def run_one(spec: dict) -> None:
        researcher = Researcher(spec, send, request)
        try:
            await researcher.run(make_engine("fake" if spec.get("fake") is not None else spec.get("engine")))
        finally:
            tasks.pop(spec["rid"], None)

    while True:
        message = await inbox.get()
        if message is None:
            break
        if "reply_to" in message:
            future = pending.get(message["reply_to"])
            if future is not None and not future.done():
                future.set_result(message)
            continue
        op, reply = message.get("op"), {"reply_to": message.get("id"), "ok": True}
        if op == "spawn":
            if message['rid'] in tasks:
                reply.update(ok=False, error='this researcher already has an active dispatch')
            else:
                tasks[message["rid"]] = asyncio.create_task(run_one(message))
        elif op == "cancel":
            task = tasks.get(str(message.get("rid")))
            if task is not None:
                task.cancel()
                if message.get("wait"):
                    await asyncio.gather(task, return_exceptions=True)
            reply["ok"] = task is not None or bool(message.get("wait"))
            if message.get("wait"):
                reply["terminated"] = True
        elif op == "keys":
            apply_values(message.get("set") or {}, message.get("unset") or [])  # researchers' clients read the environment
        elif op != "ping":
            reply.update(ok=False, error=f"unknown op {op}")
        send(reply)
    for task in tasks.values():
        task.cancel()
