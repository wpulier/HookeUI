"""Operations: outside agents' work, admitted to disk before it starts (ADR 0026; contract/agent/operation.schema.json).

- **A request key means one request.** Its record keeps the SHA-256 of what was asked (tool, project, input revision,
  generation, parameters). The same key and request returns the same operation; the same key with anything else is a
  conflict.
- **Admission is written first.** The record is on disk (fsynced) before any work starts, so a crash never runs the
  same request twice.
- **A restart reports what survived.** A record that was admitted, running or asked to stop when Hooke stopped is
  marked `interrupted` with what it says it left. Nothing here resumes work by assumption.
- **A cancel is reported, not assumed.** A cancel is `cancelRequested` until the work's own end says how it stopped.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Awaitable, Callable

from .connections import Refused

TERMINAL = ("succeeded", "failed", "cancelled", "interrupted")
_KEY = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
_ID = re.compile(r"^op_[0-9a-f]{16,32}$")


def request_digest(tool: str, project: str, input_revision, generation, params: dict) -> str:
    """What a request key is bound to. Shared meaning with the hosted service: the same fields, sorted JSON, SHA-256."""
    asked = {"tool": tool, "project": project, "inputRevision": input_revision, "generation": generation,
             "params": params}
    return hashlib.sha256(json.dumps(asked, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # A temporary file of its own for every write: two writes of one record never share one (a shared name let a
    # cancel's write and a progress write interleave into a damaged record).
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(value, stream, indent=1)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def wire(record: dict) -> dict:
    """The operation as the contract says it (internal fields left out)."""
    return {k: v for k, v in record.items() if k not in ("requestSha256",)}


Work = Callable[[dict, Callable[..., Awaitable[None]]], Awaitable[dict]]


class Operations:
    def __init__(self, home: Path, clock=time.time):
        self.folder = Path(home) / "agents" / "operations"
        self.clock = clock
        self._records: dict[str, dict] = {}
        self._keys: dict[tuple[str, str], str] = {}  # (who, request key) -> operation: keys are each connection's own
        self._tasks: dict[str, asyncio.Task] = {}
        self._saving: dict[str, asyncio.Lock] = {}  # one record's writes in order, one at a time
        self._lock = asyncio.Lock()
        self.unreadable: list[str] = []
        self._load()

    def _path(self, op_id: str) -> Path:
        return self.folder / f"{op_id}.json"

    def _load(self) -> None:
        if not self.folder.is_dir():
            return
        for path in sorted(self.folder.glob("op_*.json")):
            try:
                record = json.loads(path.read_text())
                if not _ID.match(record["id"]) or not _KEY.match(record["requestKey"]):
                    raise ValueError("not an operation")
            except (OSError, ValueError, KeyError, TypeError):
                self.unreadable.append(path.name)  # kept as it is; said by `operations()`, never overwritten
                continue
            if record.get("state") not in TERMINAL:
                record["state"] = "interrupted"
                record["outcome"] = {**(record.get("outcome") or {}),
                                     "says": "Hooke stopped while this ran. Anything it finished is kept in its Project.",
                                     "next": "check its Project, or run it again with a new request key"}
                record["endedAt"] = self.clock()
                try:
                    _write(path, record)
                except OSError:
                    pass  # said as interrupted in memory; the file says so on the next start
            self._records[record["id"]] = record
            self._keys[(record.get("by", ""), record["requestKey"])] = record["id"]

    async def _save(self, record: dict) -> None:
        """Writes the record as it is when its turn comes: writes of one record never overlap, and the last one wins."""
        lock = self._saving.setdefault(record["id"], asyncio.Lock())
        async with lock:
            await asyncio.to_thread(_write, self._path(record["id"]), dict(record))

    async def admit(self, *, by: str, label: str, tool: str, project: str, input_revision, generation, params: dict,
                    request_key, work: Work) -> tuple[dict, bool]:
        """The operation for this request, and whether it was made now. Work starts only after its record is on disk."""
        if not isinstance(request_key, str) or not _KEY.match(request_key):
            raise Refused(400, "bad-request-key", "A request key is 8 to 128 letters, digits or . _ : -.",
                          next="choose a new request key for this request")
        digest = request_digest(tool, project, input_revision, generation, params)
        async with self._lock:
            known = self._keys.get((by, request_key))
            if known is not None:
                record = self._records[known]
                if record["requestSha256"] != digest:
                    raise Refused(409, "request-key-conflict",
                                  "That request key was already used for a different request.",
                                  next="use a new request key for a new request")
                return wire(record), False
            record = {"id": "op_" + secrets.token_hex(12), "requestKey": request_key, "requestSha256": digest,
                      "state": "admitted", "tool": tool, "project": project, "inputRevision": input_revision,
                      "generation": generation, "params": params, "by": by, "byLabel": label, "at": self.clock(),
                      "progress": None, "outcome": None, "resumable": False}
            try:
                await self._save(record)
            except OSError:
                raise Refused(503, "not-saved", "Hooke could not record the request, so nothing was started.",
                              next="check free disk space, then try again with the same request key") from None
            self._records[record["id"]] = record
            self._keys[(by, request_key)] = record["id"]
            task = asyncio.create_task(self._run(record, work))
            self._tasks[record["id"]] = task
            task.add_done_callback(lambda done, record=record: self._ended(record, done))
            return wire(record), True

    def _ended(self, record: dict, task: asyncio.Task) -> None:
        """A task cancelled before its work began never runs `_run`'s ending: end its record here, said truly."""
        self._tasks.pop(record["id"], None)
        if task.cancelled() and record["state"] not in TERMINAL:
            asked = bool(record.get("cancelAskedAt"))
            record["state"], record["endedAt"] = ("cancelled" if asked else "interrupted"), self.clock()
            record["outcome"] = {**(record.get("outcome") or {}),
                                 "says": "Stopped before it started." if asked else "Hooke stopped before this started."}
            asyncio.get_running_loop().create_task(self._save(record))

    async def _run(self, record: dict, work: Work) -> None:
        async def progress(done: float, of: float | None, says: str) -> None:
            record["progress"] = {"done": done, "of": of, "says": says}
            await self._save(record)

        try:
            if record["state"] == "admitted":  # a cancel asked before this point stays asked
                record["state"] = "running"
            await self._save(record)
            outcome = await work(record, progress)
        except asyncio.CancelledError:
            if record.get("cancelAskedAt"):
                record["state"] = "cancelled"
                record["outcome"] = {**(record.get("outcome") or {}), "says": "Stopped before it finished."}
            else:  # nobody asked it to stop: Hooke is stopping (quit, restart, update), so it was interrupted
                record["state"] = "interrupted"
                record["outcome"] = {**(record.get("outcome") or {}),
                                     "says": "Hooke stopped while this ran. Anything it finished is kept in its Project.",
                                     "next": "check its Project, or run it again with a new request key"}
        except Refused as error:  # cut off by part of Hooke stopping is interrupted, never the work's failure
            record["state"] = "interrupted" if error.code == "stopped-while-running" else "failed"
            record["outcome"] = {"code": error.code, "says": error.says, "next": error.next}
        except Exception as error:  # the work's own failure, said as it is (ADR 0006)
            says = getattr(error, "summary", None) or str(error) or type(error).__name__
            record["state"] = "failed"
            record["outcome"] = {"code": "failed", "says": says,
                                 "next": getattr(error, "next", None) or "check Hooke's Activity, then try again"}
        else:
            record["state"], record["outcome"] = "succeeded", outcome
            if record.get("cancelAskedAt"):
                record["outcome"] = {**outcome, "note": "It finished before it could stop."}
        record["endedAt"] = self.clock()
        try:
            await asyncio.shield(self._save(record))  # the ending is written even if a cancel arrives now
        finally:
            self._tasks.pop(record["id"], None)

    def known(self, request_key, tool: str, params: dict, by: str = "") -> dict | None:
        """An operation already admitted under this key, for a retry: the same tool and parameters return it before
        anything else is checked (a retry while Hooke is busy finds its work, never a refusal); anything else under the
        key is a conflict. None for a key not seen."""
        record = self._records.get(self._keys.get((by, request_key))) if isinstance(request_key, str) else None
        if record is None:
            return None
        if record["tool"] != tool or record.get("params") != params:
            raise Refused(409, "request-key-conflict", "That request key was already used for a different request.",
                          next="use a new request key for a new request")
        return wire(record)

    def _own(self, op_id: str, by: str | None) -> dict | None:
        record = self._records.get(op_id)
        return record if record is not None and (by is None or record.get("by") == by) else None

    def get(self, op_id: str, by: str | None = None) -> dict:
        """One operation, only to the connection that started it (None: any, for the app itself)."""
        record = self._own(op_id, by)
        if record is None:
            raise Refused(404, "unknown-operation", "There is no such operation.", next="list operations to find it")
        return wire(record)

    def operations(self, limit: int = 20, by: str | None = None) -> dict:
        mine = [r for r in self._records.values() if by is None or r.get("by") == by]
        rows = sorted(mine, key=lambda r: r["at"], reverse=True)[: max(1, min(limit, 100))]
        # Unreadable records can't say whose they were, so only the app itself (by None) is told their names.
        return {"operations": [wire(r) for r in rows], "unreadable": list(self.unreadable) if by is None else []}

    async def cancel(self, op_id: str, by: str | None = None) -> dict:
        record = self._own(op_id, by)
        if record is None:
            raise Refused(404, "unknown-operation", "There is no such operation.", next="list operations to find it")
        if record["state"] in TERMINAL:
            return wire(record)
        record["state"], record["cancelAskedAt"] = "cancelRequested", self.clock()
        await self._save(record)
        task = self._tasks.get(op_id)
        if task is not None:
            task.cancel()
        return wire(record)
