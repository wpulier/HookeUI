"""A recorded outside-agent stream played back as the program (tests/fixtures/agents/README.md).

Each message the adapter sends must be the recording's next "out" of the same kind (its method, or for Claude Code its
type and subtype); the "in" lines up to the following "out" are then delivered. Request ids are carried over: a
recorded answer answers the adapter's own request.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures" / "agents"


def load(name: str) -> list[dict]:
    rows = [json.loads(line) for line in (FIXTURES / name).read_text().splitlines() if line.strip()]
    return [r for r in rows if r["dir"] in ("in", "out")]


def kind(message: dict) -> tuple:
    if "type" in message:  # Claude Code's stream-json
        request = message.get("request") or message.get("response") or {}
        return message["type"], request.get("subtype") if message["type"] == "control_request" else None, \
            "priority" in message
    if "method" in message:
        return ("request" if "id" in message else "notification"), message["method"]
    return ("response",)


class Replay:
    def __init__(self, records: list[dict]):
        self.records = list(records)
        self.i = 0
        self.sent: list[dict] = []
        self.returncode: int | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._ids: dict = {}  # recorded request id -> the adapter's own
        self._release()

    def _map(self, message: dict) -> dict:
        message = json.loads(json.dumps(message))
        if "id" in message and "method" not in message and message["id"] in self._ids:
            message["id"] = self._ids[message["id"]]
        response = message.get("response") if message.get("type") == "control_response" else None
        if response is not None and response.get("request_id") in self._ids:
            response["request_id"] = self._ids[response["request_id"]]
        return message

    def _release(self) -> None:
        while self.i < len(self.records) and self.records[self.i]["dir"] == "in":
            self._queue.put_nowait(self._map(self.records[self.i]["msg"]))
            self.i += 1

    @property
    def waiting(self) -> dict | None:
        """The next message the recording expects Hooke to send, once everything before it was delivered."""
        if self.i < len(self.records) and self._queue.empty():
            return self.records[self.i]["msg"]
        return None

    async def send(self, message: dict) -> None:
        self.sent.append(message)
        assert self.i < len(self.records), f"sent after the recording ended: {message}"
        expected = self.records[self.i]["msg"]
        assert kind(message) == kind(expected), f"sent {kind(message)}, the recording has {kind(expected)} next"
        if "method" in expected and "id" in expected:
            self._ids[expected["id"]] = message["id"]
        if expected.get("type") == "control_request":
            self._ids[expected["request_id"]] = message["request_id"]
        self.i += 1
        self._release()

    async def incoming(self):
        while True:
            message = await self._queue.get()
            if message is None:
                return
            yield message

    def end(self, code: int = 0) -> None:
        """The program exits: whatever the recording still expected is dropped."""
        self.returncode = code
        self._queue.put_nowait(None)

    async def close(self) -> None:
        if self.returncode is None:
            self.end(0)

    def stderr_tail(self) -> list[str]:
        return []


def factory(replay: Replay):
    calls = []

    async def make(argv, cwd, env):
        calls.append((argv, cwd, env))
        return replay

    make.calls = calls
    return make


class Events:
    """Everything an adapter says, collected as it comes."""

    def __init__(self, adapter):
        self.items: list[dict] = []
        self.task = asyncio.get_running_loop().create_task(self._collect(adapter))

    async def _collect(self, adapter):
        async for event in adapter.events():
            self.items.append(event)

    def of(self, type_: str, **fields) -> list[dict]:
        return [e for e in self.items if e["type"] == type_ and all(e.get(k) == v for k, v in fields.items())]

    async def until(self, test, seconds: float = 5.0):
        for _ in range(int(seconds / 0.005)):
            found = test()
            if found:
                return found
            await asyncio.sleep(0.005)
        raise AssertionError(f"never happened; events so far: {[e['type'] for e in self.items]}")


async def until_waiting(replay: Replay, seconds: float = 5.0) -> dict:
    """The point in the recording where Hooke spoke next (after everything before it was read)."""
    for _ in range(int(seconds / 0.005)):
        if replay.waiting is not None:
            await asyncio.sleep(0.02)  # the adapter reads what was delivered
            if replay.waiting is not None and replay._queue.empty():
                return replay.waiting
        await asyncio.sleep(0.005)
    raise AssertionError(f"the recording never reached Hooke's next message (at line {replay.i})")
