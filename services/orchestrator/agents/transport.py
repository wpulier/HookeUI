"""One outside agent's program, spoken to line by line (ADR 0029).

Every adapter talks to its program through a Transport: JSON messages, one per line, out on stdin and in on stdout.
`ProcessTransport` runs the user's own installed program as a child of its own process group (owned_process.py), so
Stop ends it and everything it started. Tests use a recorded stream instead (tests/agent_streams.py).
"""
from __future__ import annotations

import asyncio
import collections
import json
import os
import subprocess
import threading
from typing import AsyncIterator, Protocol

from ..owned_process import terminate

LINE_BYTES = 16 * 1024 * 1024  # one message; a longer line is reported, never parsed in pieces
STDERR_LINES = 40


class Transport(Protocol):
    returncode: int | None

    async def send(self, message: dict) -> None: ...

    def incoming(self) -> AsyncIterator[dict]: ...

    async def close(self) -> None: ...

    def stderr_tail(self) -> list[str]: ...


class Gone(RuntimeError):
    """The program has exited: nothing more can be sent to it."""


class ProcessTransport:
    """The program as a child process. Reading and writing happen on threads, never on the event loop."""

    def __init__(self, argv: list[str], *, cwd: str, env: dict[str, str]):
        self.argv, self.cwd, self.env = list(argv), cwd, dict(env)
        self.process: subprocess.Popen | None = None
        self.returncode: int | None = None
        self._queue: asyncio.Queue = asyncio.Queue()
        self._stderr: collections.deque[str] = collections.deque(maxlen=STDERR_LINES)
        self._write_lock = asyncio.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    async def open(self) -> None:
        self._loop = asyncio.get_running_loop()

        def spawn():
            return subprocess.Popen(self.argv, cwd=self.cwd, env=self.env, stdin=subprocess.PIPE,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True,
                                    bufsize=0)

        self.process = await asyncio.to_thread(spawn)
        threading.Thread(target=self._read_out, name=f"agent-out-{self.process.pid}", daemon=True).start()
        threading.Thread(target=self._read_err, name=f"agent-err-{self.process.pid}", daemon=True).start()

    def _put(self, item) -> None:
        if self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._queue.put_nowait, item)

    def _read_out(self) -> None:
        stream = self.process.stdout
        try:
            while True:
                line = stream.readline(LINE_BYTES + 1)
                if not line:
                    break
                if len(line) > LINE_BYTES:
                    self._stderr.append("hooke: a message longer than 16 MB was skipped")
                    while line and not line.endswith(b"\n"):
                        line = stream.readline(LINE_BYTES)
                    continue
                text = line.decode("utf-8", errors="replace").strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except ValueError:
                    self._stderr.append(text[:400])  # a program's stray print is shown with its errors, never parsed
                    continue
                if isinstance(message, dict):
                    self._put(message)
        except (OSError, ValueError):
            pass
        finally:
            code = self.process.wait()
            self.returncode = code
            self._put(None)

    def _read_err(self) -> None:
        try:
            for line in self.process.stderr:
                text = line.decode("utf-8", errors="replace").rstrip()
                if text:
                    self._stderr.append(text[:400])
        except (OSError, ValueError):
            pass

    async def send(self, message: dict) -> None:
        if self.process is None or self.process.poll() is not None:
            raise Gone("the program has exited")
        data = (json.dumps(message, separators=(",", ":")) + "\n").encode()
        async with self._write_lock:
            def write():
                self.process.stdin.write(data)
                self.process.stdin.flush()
            try:
                await asyncio.to_thread(write)
            except (BrokenPipeError, OSError, ValueError) as error:
                raise Gone(f"the program has exited ({error})") from error

    async def incoming(self) -> AsyncIterator[dict]:
        while True:
            item = await self._queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        process = self.process
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        if process.poll() is None:
            await asyncio.to_thread(terminate, process)
        self.returncode = process.returncode

    def stderr_tail(self) -> list[str]:
        return list(self._stderr)


def child_env(extra: dict[str, str]) -> dict[str, str]:
    """The program's environment: the user's own (so its sign-in and PATH work), without Hooke's own secrets and
    without the markers of a Claude Code session that started Hooke (a nested `claude` would refuse to run)."""
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("MICROMIND_") or k.startswith("HOOKE_") or k in _NEVER_PASSED)}
    env.update(extra)
    return env


_NEVER_PASSED = {"CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SSE_PORT", "HOOKEUI_APP",
                 "OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "XAI_API_KEY",
                 "FAL_KEY", "HF_TOKEN"}
