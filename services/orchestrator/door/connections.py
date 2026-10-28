"""Outside agents' connections to this copy of Hooke (ADR 0026).

A connection is asked for by the `hooke` command, confirmed by the researcher in the Hooke window, and claimed once by
the command that asked, with the secret whose SHA-256 it gave. Only token hashes are kept, in the data home (so Stable
and Beta never share one). Disconnecting is final for that connection: nothing here ever reactivates a revoked one.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .actor import remember

PENDING_SECONDS = 600
MOST_PENDING = 5
KINDS = ("claude-code", "codex", "cursor", "other")
_CODE_LETTERS = "ACDEFHJKMNPRTVWXY3479"  # no look-alikes (0/O, 1/I/L, 2/Z, 5/S, 6/G, 8/B)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class Refused(Exception):
    """A refusal said as the contract says (contract/agent/refusal.schema.json), with its HTTP status."""

    def __init__(self, status: int, code: str, says: str, *, next: str):
        super().__init__(says)
        self.status, self.code, self.says, self.next = status, code, says, next

    def wire(self) -> dict:
        return {"ok": False, "code": self.code, "says": self.says, "next": self.next}


@dataclass
class Connection:
    id: str
    label: str
    kind: str
    at: float
    token_sha256: str = field(repr=False)
    revoked_at: float | None = None

    @property
    def actor(self) -> str:
        return f"agent:{self.id}"

    def wire(self) -> dict:
        return {"id": self.id, "label": self.label, "kind": self.kind, "at": self.at, "revokedAt": self.revoked_at}


@dataclass
class Pending:
    id: str
    label: str
    kind: str
    code: str
    secret_sha256: str = field(repr=False)
    at: float = 0.0
    state: str = "pending"  # pending | confirmed | declined | claimed
    connection: str | None = None

    def expires(self) -> float:
        return self.at + PENDING_SECONDS

    def wire(self) -> dict:
        return {"id": self.id, "label": self.label, "kind": self.kind, "code": self.code, "at": self.at,
                "expiresAt": self.expires(), "state": self.state}


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def clean_label(label) -> str:
    text = " ".join(str(label or "").split())
    text = "".join(ch for ch in text if ch.isprintable())[:40].strip()
    return text or "An agent"


class Connections:
    def __init__(self, home: Path, clock=time.time):
        self.folder = Path(home) / "agents"
        self.path = self.folder / "connections.json"
        self.clock = clock
        self._lock = threading.Lock()
        self._pending: dict[str, Pending] = {}
        self._by_id: dict[str, Connection] = {}
        self._by_token: dict[str, Connection] = {}
        self.broken = ""  # why the kept list couldn't be read: the door refuses everything until it is restored
        try:
            self._read()
        except (RuntimeError, KeyError, TypeError) as error:
            self.broken = str(error)
            self._by_id.clear()
            self._by_token.clear()

    # --- the kept list --------------------------------------------------------------------------------------------
    def _read(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (OSError, ValueError) as error:
            # Never start with no connections when the file is there but unreadable: every agent would look new, and a
            # revoked one could be confirmed again unnoticed. Refuse until the file is restored.
            raise RuntimeError(f"agent connections unreadable at {self.path}: {error}") from error
        if not isinstance(raw, dict) or raw.get("format") != 1 or not isinstance(raw.get("connections"), list):
            raise RuntimeError(f"agent connections at {self.path} are not format 1")
        for row in raw["connections"]:
            c = Connection(id=row["id"], label=row["label"], kind=row["kind"], at=row["at"],
                           token_sha256=row["tokenSha256"], revoked_at=row.get("revokedAt"))
            self._by_id[c.id] = c
            remember(c.id, c.label)
            if c.revoked_at is None:
                self._by_token[c.token_sha256] = c

    def _write(self) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        os.chmod(self.folder, 0o700)
        rows = [{"id": c.id, "label": c.label, "kind": c.kind, "at": c.at, "tokenSha256": c.token_sha256,
                 "revokedAt": c.revoked_at} for c in self._by_id.values()]
        temporary = self.path.with_suffix(".tmp")
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as stream:
            json.dump({"format": 1, "connections": rows}, stream, indent=1)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def _usable(self) -> None:
        if self.broken:
            raise Refused(503, "connections-unreadable", "Hooke's list of connected agents could not be read.",
                          next=f"restore {self.path} from a backup, or move it aside to start the list again")

    # --- asking, confirming, claiming -----------------------------------------------------------------------------
    def _expire(self) -> None:
        now = self.clock()
        for pid in [p.id for p in self._pending.values() if now > p.expires()]:
            del self._pending[pid]

    def request(self, label, kind, secret_sha256) -> Pending:
        """A pending connection: nothing is allowed until the researcher confirms it in Hooke."""
        if not isinstance(secret_sha256, str) or not _HEX64.match(secret_sha256):
            raise Refused(400, "bad-request", "The connection request needs the SHA-256 of a secret the command made.",
                          next="update the hooke command, then run hooke connect again")
        self._usable()
        with self._lock:
            self._expire()
            waiting = [p for p in self._pending.values() if p.state == "pending"]
            if len(waiting) >= MOST_PENDING:
                raise Refused(429, "too-many-pending", "Several agents are already waiting to connect.",
                              next="answer them in Hooke, or wait ten minutes, then run hooke connect again")
            code = "".join(secrets.choice(_CODE_LETTERS) for _ in range(3)) + "-" + "".join(
                secrets.choice(_CODE_LETTERS) for _ in range(3))
            p = Pending(id="pc_" + secrets.token_hex(12), label=clean_label(label),
                        kind=kind if kind in KINDS else "other", code=code, secret_sha256=secret_sha256,
                        at=self.clock())
            self._pending[p.id] = p
            return p

    def pending(self) -> list[Pending]:
        with self._lock:
            self._expire()
            return [p for p in self._pending.values() if p.state == "pending"]

    def _waiting(self, pid: str) -> Pending:
        self._expire()
        p = self._pending.get(pid)
        if p is None:
            raise Refused(410, "connection-expired", "That connection request has expired or is unknown.",
                          next="run hooke connect again")
        return p

    def waiting(self, pid: str) -> Pending:
        """The request still waiting for an answer (its code is part of what the window signs)."""
        with self._lock:
            p = self._waiting(pid)
            if p.state != "pending":
                raise Refused(409, "already-answered", "That connection request was already answered.",
                              next="run hooke connect again if you meant to connect")
            return p

    def answer(self, pid: str, connect: bool) -> Pending:
        """The researcher's answer in the Hooke window."""
        with self._lock:
            p = self._waiting(pid)
            if p.state != "pending":
                raise Refused(409, "already-answered", "That connection request was already answered.",
                              next="run hooke connect again if you meant to connect")
            p.state = "confirmed" if connect else "declined"
            return p

    def claim(self, pid: str, secret) -> tuple[Pending, Connection | None, str | None]:
        """The asking command collects its token, once, with the secret it hashed. While unanswered: (pending, None,
        None)."""
        self._usable()
        with self._lock:
            p = self._waiting(pid)
            if not isinstance(secret, str) or not secrets.compare_digest(sha256(secret), p.secret_sha256):
                raise Refused(403, "wrong-secret", "This command did not ask for that connection.",
                              next="run hooke connect again from the same command")
            if p.state == "pending":
                return p, None, None
            if p.state == "declined":
                del self._pending[pid]
                raise Refused(403, "declined", "The connection was declined in Hooke.",
                              next="run hooke connect again if you want to connect")
            if p.state == "claimed":
                raise Refused(409, "already-claimed", "That connection's token was already collected.",
                              next="run hooke connect again")
            token = "hk_local_" + secrets.token_urlsafe(32)
            c = Connection(id="ac_" + secrets.token_hex(12), label=p.label, kind=p.kind, at=self.clock(),
                           token_sha256=sha256(token))
            self._by_id[c.id] = c
            self._by_token[c.token_sha256] = c
            remember(c.id, c.label)
            try:
                self._write()
            except OSError:
                del self._by_id[c.id], self._by_token[c.token_sha256]
                raise Refused(503, "not-saved", "Hooke could not save the connection.",
                              next="check free disk space, then run hooke connect again") from None
            p.state, p.connection = "claimed", c.id
            del self._pending[pid]
            return p, c, token

    # --- using and ending -----------------------------------------------------------------------------------------
    def authenticate(self, authorization: str | None) -> Connection:
        self._usable()
        token = (authorization or "")[7:].strip() if (authorization or "").lower().startswith("bearer ") else ""
        if not token:
            raise Refused(401, "not-connected", "This agent is not connected to Hooke.", next="run hooke connect")
        digest = sha256(token)
        with self._lock:
            c = self._by_token.get(digest)
            if c is not None:
                return c
            revoked = next((x for x in self._by_id.values() if x.token_sha256 == digest), None)
        if revoked is not None:
            raise Refused(401, "disconnected", "This agent was disconnected in Hooke.",
                          next="run hooke connect and confirm it in Hooke to connect again")
        raise Refused(401, "not-connected", "This agent is not connected to Hooke.", next="run hooke connect")

    def disconnect(self, cid: str) -> Connection:
        self._usable()
        with self._lock:
            c = self._by_id.get(cid)
            if c is None:
                raise Refused(404, "unknown-connection", "There is no such connection.", next="reload the list")
            if c.revoked_at is None:
                c.revoked_at = self.clock()
                self._by_token.pop(c.token_sha256, None)
                try:
                    self._write()
                except OSError:
                    # The token stays refused in memory for this run; say the record could not be kept.
                    raise Refused(503, "not-saved", "Disconnected for now, but Hooke could not save that.",
                                  next="free some disk space, then disconnect it again") from None
            return c

    def connections(self) -> list[Connection]:
        with self._lock:
            return sorted(self._by_id.values(), key=lambda c: c.at)

    def get(self, cid: str) -> Connection | None:
        with self._lock:
            return self._by_id.get(cid)
