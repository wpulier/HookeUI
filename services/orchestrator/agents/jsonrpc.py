"""JSON-RPC over one program's stdio, for Codex's app-server and ACP agents (ADR 0029).

Requests Hooke makes wait for their answer; the program's own requests (asking permission) and notifications go to the
adapter's handlers. Codex leaves out the "jsonrpc" field; ACP requires it.
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable

from .transport import Gone, Transport

ANSWER_S = 120.0


class RpcError(RuntimeError):
    def __init__(self, error: dict):
        self.error = error if isinstance(error, dict) else {"message": str(error)}
        super().__init__(self.error.get("message") or "the program refused")


class Peer:
    def __init__(self, transport: Transport, *, on_request: Callable[[dict], Awaitable[None]],
                 on_notification: Callable[[dict], Awaitable[None] | None], version: str | None = None):
        self.transport = transport
        self.on_request, self.on_notification = on_request, on_notification
        self.version = version
        self._next = 0
        self._waiting: dict[int, asyncio.Future] = {}

    def _frame(self, message: dict) -> dict:
        return {"jsonrpc": self.version, **message} if self.version else message

    async def request(self, method: str, params: dict | None = None, *, timeout: float = ANSWER_S):
        self._next += 1
        rid = self._next
        future = asyncio.get_running_loop().create_future()
        self._waiting[rid] = future
        message = {"id": rid, "method": method}
        if params is not None:
            message["params"] = params
        try:
            await self.transport.send(self._frame(message))
            reply = await asyncio.wait_for(future, timeout)
        finally:
            self._waiting.pop(rid, None)
        if "error" in reply:
            raise RpcError(reply["error"])
        return reply.get("result")

    async def notify(self, method: str, params: dict | None = None) -> None:
        message = {"method": method}
        if params is not None:
            message["params"] = params
        await self.transport.send(self._frame(message))

    async def reply(self, rid, result=None, *, error: dict | None = None) -> None:
        message = {"id": rid, "error": error} if error is not None else {"id": rid, "result": result}
        await self.transport.send(self._frame(message))

    async def run(self) -> None:
        """Read until the program ends; then every waiting request fails as Gone."""
        try:
            async for message in self.transport.incoming():
                if "method" in message and "id" in message:
                    await self.on_request(message)
                elif "method" in message:
                    outcome = self.on_notification(message)
                    if asyncio.iscoroutine(outcome):
                        await outcome
                elif "id" in message:
                    future = self._waiting.get(message["id"])
                    if future is not None and not future.done():
                        future.set_result(message)
        finally:
            for future in self._waiting.values():
                if not future.done():
                    future.set_exception(Gone("the program has exited"))
