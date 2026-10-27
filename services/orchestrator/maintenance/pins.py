"""Durable holds on generations, kept across restarts (ticket 59's "Hold on a generation").

generation_leases.py holds a generation for work running in this process, in memory. A pin holds one for an owner that
outlives the process: a saved recipe, a session, a job record, the user's Pin this version, or the rollback hold on the
generation a promotion replaced. A pinned owner resolves its generation by name (env_runner.generation); promotion never
changes what it resolves, and removal refuses a held generation.
"""
from __future__ import annotations

import os
from typing import Mapping

from . import store

KINDS = ("user", "recipe", "session", "job", "rollback")


def _all(environ: Mapping[str, str]) -> dict:
    return store.read(store.path("pins.json", environ), {})


def add(key: str, generation: str, kind: str, owner: str, environ: Mapping[str, str] = os.environ) -> dict:
    if kind not in KINDS:
        raise ValueError(f"{kind!r} is no kind of pin ({', '.join(KINDS)})")
    row = {"generation": generation, "kind": kind, "owner": owner, "at": store.now()}
    with store.locked(environ):
        found = _all(environ)
        rows = [r for r in found.get(key, []) if not (r.get("kind") == kind and r.get("owner") == owner)]
        found[key] = [*rows, row]
        store.write(store.path("pins.json", environ), found)
    return row


def remove(key: str, kind: str, owner: str | None = None, environ: Mapping[str, str] = os.environ) -> int:
    """Take an owner's pin away (every pin of that kind with `owner` None). Returns how many went."""
    if not any(r.get("kind") == kind and (owner is None or r.get("owner") == owner) for r in of(key, environ)):
        return 0  # nothing to take away: nothing written
    with store.locked(environ):
        found = _all(environ)
        rows = found.get(key, [])
        kept = [r for r in rows if not (r.get("kind") == kind and (owner is None or r.get("owner") == owner))]
        if kept:
            found[key] = kept
        else:
            found.pop(key, None)
        store.write(store.path("pins.json", environ), found)
    return len(rows) - len(kept)


def of(key: str, environ: Mapping[str, str] = os.environ) -> list[dict]:
    return [dict(r) for r in _all(environ).get(key, []) if isinstance(r, dict)]


def holds(m, environ: Mapping[str, str] = os.environ) -> dict[str, list[str]]:
    """Every generation of the entry something holds, with what holds it: pins, and work running in this process."""
    from .. import envs, generation_leases

    out: dict[str, list[str]] = {}
    for r in of(m.key, environ):
        out.setdefault(r["generation"], []).append(f"{r['kind']}:{r['owner']}")
    for d, _receipt in envs.generations(m, environ):
        if n := generation_leases.holding(d):
            out.setdefault(d.name, []).append(f"running:{n}")
    return out


def require_removable(key: str, generations: set[str], environ: Mapping[str, str] = os.environ) -> None:
    """Raise Held when an owner other than a rollback pins one of `generations`: removing the entry's software would
    take away what that owner runs on. A rollback hold goes with the software it holds (envs.removal releases it)."""
    held = [r for r in of(key, environ) if r["generation"] in generations and r.get("kind") != "rollback"]
    if held:
        owners = ", ".join(sorted({f"{r['kind']} {r['owner']}" for r in held}))
        raise Held(f"This runtime is kept for what is pinned to it ({owners}). Unpin it first, or use Turn off instead.")


class Held(ValueError):
    pass
