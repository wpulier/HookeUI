"""A person accepting what an update candidate changed (ticket 59's MT2; ADR 0025's "Needs review").

compat.py lists what only a person can accept. `accept` records that a person accepted exactly that list for exactly
that candidate, as a decision in decisions.jsonl. Its fingerprint is the entry, the candidate's version and generation,
and the items. A different list, or another candidate, needs a new acceptance.

Acceptance is not setup: a key, a licence or an account the candidate newly needs is still got through its own step.
`missing_setup` names the first such step, so a candidate that couldn't run here is never promoted.

Items only of the kinds in PREPARE_FIRST (what it computes, and what it needs from this Mac) need no permission to prepare:
such a candidate is built and proven before the person looks, and their acceptance then only promotes it.
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Mapping

from gpu_worker import manifests as catalog

from . import store

PREPARE_FIRST = {"science", "hardware"}


def fingerprint(m: catalog.Manifest, generation: str, items: list[dict]) -> str:
    said = json.dumps({"key": m.key, "version": m.version, "generation": generation,
                       "items": sorted((r["what"], r["from"], r["to"]) for r in items)}, sort_keys=True)
    return hashlib.sha256(said.encode()).hexdigest()[:24]


def accepted(m: catalog.Manifest, print_: str, environ: Mapping[str, str] = os.environ) -> dict | None:
    from .promote import decisions

    return next((d for d in reversed(decisions(m.key, environ))
                 if d.get("action") == "accept" and d.get("fingerprint") == print_), None)


def accept(m: catalog.Manifest, print_: str, items: list[dict], *, by: str, how: str,
           environ: Mapping[str, str] = os.environ, generation: str = "") -> dict:
    from . import policy

    if how not in policy.HOW:
        raise policy.NotRecorded("what an update changes is accepted by a person: on the screen, or confirming a proposal")
    row = {"at": store.now(), "key": m.key, "action": "accept", "fingerprint": print_, "version": m.version,
           "review": items, "by": by, "how": how}
    if generation:
        row["generation"] = generation
    store.append(store.path("decisions.jsonl", environ), row)
    return row


def accept_current(m: catalog.Manifest, print_: str, *, by: str, how: str,
                   environ: Mapping[str, str] = os.environ) -> dict:
    """Accept the current reviewed target, never metadata from the older catalog pin."""
    from .. import envs
    from . import discover, prepare

    with prepare._entry_lock(m.key, environ) as acquired:
        if not acquired:
            raise ValueError(f"{m.name}'s update is changing; look at its card again")
        with store.locked(environ):
            att = prepare.last(m.key, environ)
            if att.get("state") != "needs-review" or not att.get("fingerprint"):
                raise ValueError(f"nothing of {m.name}'s update waits for your review")
            changed = f"what {m.name}'s update changes is different from what was shown"
            if print_ != att["fingerprint"]:
                raise ValueError(changed)
            target, items = att.get("target") or {}, att.get("review") or []
            candidate = (discover.candidate_manifest(m, target["snapshot"], environ) if target.get("snapshot")
                         else discover.fresh(m.key, environ))
            if candidate is None or candidate.key != m.key or candidate.version != target.get("version"):
                raise ValueError(changed)
            generation = envs.generation(candidate, environ)
            if ((target.get("generation") and generation != target["generation"])
                    or fingerprint(candidate, generation, items) != print_):
                raise ValueError(changed)
            return accept(candidate, print_, items, by=by, how=how, environ=environ, generation=generation)


def prepare_first(items: list[dict]) -> bool:
    return bool(items) and {r["kind"] for r in items} <= PREPARE_FIRST


def missing_setup(active: catalog.Manifest, candidate: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """The first setup step the candidate newly needs and this Mac lacks ("" when none), read as readiness reads it:
    a key not in the keys file, a licence or account access not granted (what Hugging Face said when asked:
    observed.py), data it reads not downloaded."""
    old = {*active.needs_to_get, *active.needs_to_run}
    new = [n for n in (*candidate.needs_to_get, *candidate.needs_to_run) if n not in old]
    if not new:
        return ""
    from .. import observed, readiness
    from ..keys import load_keys
    from ..paths import Paths

    try:
        have = load_keys(Paths.from_env(environ).keys_file)
    except Exception:  # noqa: BLE001 - an unreadable keys file has no keys; never a value in what is said
        have = {}
    for need in new:
        kind, _, what = need.partition(":")
        if kind == "key" and not (have.get(what) or environ.get(what)):
            return readiness.key_step(what, environ=environ).says
        if kind == "licence":
            from .discover import update_target
            # This candidate's own observation: Hugging Face asked about its source (observed.targets, Check again),
            # or your word for a page no probe reads (observe-confirm with update). Never the pinned entry's answer.
            obs = observed.latest("access", update_target(candidate.key, candidate.version), environ)
            if (obs or {}).get("state") == "yes":
                continue
            if obs is None or obs.get("state") != "no":  # never asked, can't be asked yet, or couldn't ask
                return "Its licence: not confirmed yet"
            return "Accept its licence on Hugging Face" if "huggingface.co" in what else f"Accept its licence ({what})"
        if kind == "data":
            data = catalog.get(what)
            if data is None or not readiness.obtained(data, environ)[0]:
                return f"Download the data it reads ({what})"
    return ""
