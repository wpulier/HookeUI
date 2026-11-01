"""Each entry's recorded maintenance policy (ADR 0025): manual (the default), review, auto or pin.

No record means manual: nothing happens unattended. A record is made by a person (on the screen, or confirming the
agent's proposal that quotes their words), kept across restarts, and every change is appended to policies.jsonl. A
policy covers only an entry already turned on here, from the source it came from. `effective` is what the policy
allows now: auto acts as review for code nobody reviewed, and while a rollback or the user's own going back holds it.
"""
from __future__ import annotations

import os
from typing import Mapping

from gpu_worker import manifests as catalog

from . import store

POLICIES = ("manual", "review", "auto", "pin")
HOW = ("screen", "agent-proposal-confirmed", "command-line", "automatic-rollback")
WORDS = {"manual": "Update when I choose", "review": "Review updates",
         "auto": "Automatically maintain compatible updates", "pin": "Pin this version"}


class NotRecorded(ValueError):
    pass


def _all(environ: Mapping[str, str]) -> dict:
    return store.read(store.path("policies.json", environ), {})


def get(key: str, environ: Mapping[str, str] = os.environ) -> dict:
    """The record, or the default {"policy": "manual", "default": True}."""
    rec = _all(environ).get(key)
    return dict(rec) if isinstance(rec, dict) and rec.get("policy") in POLICIES else {"policy": "manual", "default": True}


def keys(environ: Mapping[str, str] = os.environ, *, wanting: tuple[str, ...] = ("review", "auto")) -> list[str]:
    return sorted(k for k, rec in _all(environ).items() if isinstance(rec, dict) and rec.get("policy") in wanting)


def source(m: catalog.Manifest) -> dict:
    return {"channel": m.channel or ("mine" if m.namespace == "mine" else "app"), "namespace": m.namespace}


def reviewed(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> bool:
    """ADR 0021's code-trust fact: the app's own entries and the reviewed channel's are reviewed; yours and a community
    channel's are not."""
    if m.namespace == "mine":
        return False
    if not m.channel:
        return True
    c = next((c for c in catalog.channels(environ) if c["name"] == m.channel), None)
    return bool(c and c.get("reviewed"))


def record(m: catalog.Manifest, policy: str, *, by: str, how: str, generation: str = "", why: str = "",
           environ: Mapping[str, str] = os.environ) -> dict:
    """Record a person's policy for one entry. Raises NotRecorded with the reason in plain words."""
    with store.locked(environ):
        return _record(m, policy, by=by, how=how, generation=generation, why=why, environ=environ)


def _record(m: catalog.Manifest, policy: str, *, by: str, how: str, generation: str, why: str,
            environ: Mapping[str, str]) -> dict:
    """Publish the policy and its durable pin together while holding the maintenance transition lock."""
    from .. import env_runner

    if policy not in POLICIES:
        raise NotRecorded(f"{policy!r} is no maintenance policy (one of {', '.join(POLICIES)})")
    if how not in HOW:
        raise NotRecorded("a maintenance policy is recorded by a person: on the screen, or confirming a proposal")
    if m.runner != "python-env":
        raise NotRecorded(f"automatic maintenance covers entries with their own environment for now; {m.name} is updated "
                      "by its own setup")
    active = env_runner.selected(m, environ)
    if policy != "manual" and active is None:
        raise NotRecorded(f"turn {m.name} on first: a policy maintains an entry you already use (ADR 0021)")
    if policy == "pin":
        generation = generation or active.folder.name
        if env_runner.generation(m, generation, environ) is None:
            raise NotRecorded("that version is no longer here, ready and proven: pin a version that is")
    rec = {"policy": policy, "by": by, "how": how, "at": store.now(), "source": source(m), **({"why": why} if why else {}),
           **({"generation": generation, "version": env_runner.generation(m, generation, environ).m.version}
              if policy == "pin" else {})}
    with store.locked(environ):
        found = _all(environ)
        found[m.key] = rec
        store.write(store.path("policies.json", environ), found)
        store.append(store.path("policies.jsonl", environ), {"key": m.key, **rec})
    from . import pins
    pins.remove(m.key, "user", "pin", environ)
    if policy == "pin":  # the pinned version is held: removal keeps it (pins.require_removable)
        pins.add(m.key, generation, "user", "pin", environ)
    return rec


def pinned(key: str, environ: Mapping[str, str] = os.environ) -> str:
    """The generation the entry is pinned to, or ""."""
    rec = get(key, environ)
    return rec.get("generation", "") if rec["policy"] == "pin" else ""


def leave_pin(m: catalog.Manifest, generation: str, environ: Mapping[str, str] = os.environ, *,
              why: str = "you chose another version, so it is no longer pinned", by: str = "you",
              how: str = "screen") -> None:
    """The version running is no longer the pinned one: the pin ends, and that is recorded with why."""
    from .. import env_observations
    with env_observations.live():
        previous = pinned(m.key, environ)
        if previous and previous != generation:
            record(m, "manual", by=by, how=how, environ=environ, why=why)


def hold(key: str, why: str, environ: Mapping[str, str] = os.environ) -> None:
    """Keep auto acting as review until a person records a policy again (an automatic rollback; your Undo)."""
    with store.locked(environ):
        found = _all(environ)
        rec = found.get(key)
        if not isinstance(rec, dict) or rec.get("policy") != "auto":
            return
        rec.update(held=why, heldAt=store.now())
        store.write(store.path("policies.json", environ), found)
        store.append(store.path("policies.jsonl", environ), {"key": key, "held": why, "at": rec["heldAt"]})


def effective(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> tuple[str, str]:
    """(what the policy allows now, why it is less than recorded or "")."""
    rec = get(m.key, environ)
    policy = rec["policy"]
    if policy != "auto":
        return policy, ""
    if rec.get("source") and rec["source"] != source(m):
        return "review", "its source changed since the policy was recorded"
    if not reviewed(m, environ):
        return "review", "its code isn't reviewed here, so you apply each update"
    if rec.get("held"):
        return "review", rec["held"]
    return "auto", ""
