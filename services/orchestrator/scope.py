"""The orchestrator's side of the scope bridge (services/scope_bridge/AGENTS.md): asking the bridge, never the hardware.

The scope bridge is its own process (`make start SCOPE_BACKEND=...`, scripts/studio.sh), on this Mac only; the
orchestrator knows it by `MICROMIND_SCOPE_URL`. Every action goes to the bridge as a typed action and comes back as the
bridge's receipt; the brain's tools (`brain/toolbox/scope.py`) say what it did and file the receipt in the session's
journal as an app notice (producer "scope"), so the conversation keeps every move with what was asked.
"""
from __future__ import annotations

import os

TIMEOUT_S = 120.0  # an acquisition or an autofocus sweep can take a while; the bridge answers when it ends


class ScopeClient:
    def __init__(self, url: str, *, timeout: float = TIMEOUT_S, transport=None) -> None:
        self.url = url.rstrip("/")
        self.timeout = timeout
        self._transport = transport

    def _client(self):
        import httpx

        return httpx.AsyncClient(base_url=self.url, timeout=self.timeout, transport=self._transport)

    async def state(self) -> dict:
        async with self._client() as c:
            r = await c.get("/api/state")
            r.raise_for_status()
            return r.json()

    async def do(self, action: dict, approval: str | None = None) -> dict:
        async with self._client() as c:
            r = await c.post("/api/do", json={"action": action, **({"approval": approval} if approval else {})})
            r.raise_for_status()
            return r.json()

    async def stop(self) -> dict:
        async with self._client() as c:
            r = await c.post("/api/stop")
            r.raise_for_status()
            return r.json()

    async def set_limits(self, limits: dict, words: str) -> dict:
        async with self._client() as c:
            r = await c.put("/api/limits", json={"limits": limits, "words": words})
            if r.status_code == 400:
                raise ValueError(r.json().get("error") or "the limits were not changed")
            r.raise_for_status()
            return r.json()

    async def file(self, path: str) -> bytes:
        async with self._client() as c:
            r = await c.get(f"/api/files/{path}")
            r.raise_for_status()
            return r.content


def from_environment(environ=os.environ) -> ScopeClient | None:
    """The scope bridge this orchestrator was started with, or None (no microscope to drive)."""
    url = (environ.get("MICROMIND_SCOPE_URL") or "").strip()
    return ScopeClient(url) if url.startswith(("http://127.0.0.1:", "http://localhost:")) else None


def said(receipt: dict) -> str:
    """A receipt in words, as the user hears it: what was done, or why not."""
    kind, status = receipt.get("kind") or "action", receipt.get("status")
    why = receipt.get("why")
    if status == "refused":
        return f"not done: {why}"
    if status == "needs-yes":
        return f"waiting for the user's yes: {receipt.get('ask') or why}"
    if status == "failed":
        return f"the microscope could not: {why}"
    after = receipt.get("after") or {}
    where = (f" the stage is at x {after['x']:.1f}, y {after['y']:.1f}, z {after['z']:.2f} µm"
             if all(isinstance(after.get(a), (int, float)) for a in "xyz") else "")
    if status == "stopped":
        return f"stopped part way{(': ' + why) if why else ''};{where}".rstrip(";")
    checked = "" if receipt.get("verified", True) else " (not confirmed: " + (why or "read back differently") + ")"
    made = receipt.get("made") or []
    pictures = f", {len(made)} picture{'s' if len(made) != 1 else ''} made" if made else ""
    return f"{kind} done{checked}{pictures};{where}".rstrip(";")
