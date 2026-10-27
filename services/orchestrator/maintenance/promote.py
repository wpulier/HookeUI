"""Promotion, the first check after it, rollback and Undo (ticket 59's lifecycle; ADR 0025).

Promotion replaces one pointer (env_runner.promote) and appends one decision to decisions.jsonl: who or which policy
decided, the generation it replaced and the one it made active, its version and its proof. The replaced generation
gets a rollback pin, so it stays until nothing needs it. Work that already holds a generation keeps it; nothing reruns.

The first check runs right after an unattended promotion: the new active generation still resolves (ready and proven)
and runs its proof input once in its own worker. If it fails, the pointer goes back to the replaced generation by
itself, the decision says why, and automatic maintenance of the entry is held at review until a person looks.
"""
from __future__ import annotations

import os
from typing import Mapping

from gpu_worker import manifests as catalog

from . import pins, policy, store


class Failed(RuntimeError):
    pass


MOVES = ("promote", "rollback")  # the decisions that change which generation is active


def decisions(key: str | None = None, environ: Mapping[str, str] = os.environ, *, moves: bool = False) -> list[dict]:
    rows = store.lines(store.path("decisions.jsonl", environ))
    return [r for r in rows if (key is None or r.get("key") == key) and (not moves or r.get("action") in MOVES)]


def checked(key: str, promotion: dict, environ: Mapping[str, str] = os.environ) -> bool:
    """Whether the first check after `promotion` was recorded."""
    return any(d.get("action") == "checked" and d.get("of") == promotion.get("at") and d.get("to") == promotion.get("to")
               for d in decisions(key, environ))


def _decide(row: dict, environ: Mapping[str, str]) -> dict:
    row = {"at": store.now(), **row}
    store.append(store.path("decisions.jsonl", environ), row)
    return row


def first_check(m: catalog.Manifest, generation: str, environ: Mapping[str, str] = os.environ) -> str:
    """"" when the generation resolves and runs its proof input once here; else why not."""
    from .. import env_runner, generation_leases, owned_process

    found = env_runner.generation(m, generation, environ)
    if found is None:
        return "it isn't ready and proven here any more"
    try:
        with generation_leases.use(found.folder), owned_process.scope(seconds=env_runner.RUN_S) as bound:
            from ..bundle_admission import local_workflow
            if local_workflow(found.m):
                from . import workflow
                workflow.first_check(found, environ, check_bound=bound.check)
            else:
                env_runner.run_once(found, environ)
    except Exception as exc:  # noqa: BLE001 - any failure of the first run is the check's answer, said in its words
        return str(exc).splitlines()[0][:300] if str(exc) else type(exc).__name__
    return ""


def promote(m: catalog.Manifest, generation: str, *, by: str, environ: Mapping[str, str] = os.environ,
            check: bool = True) -> dict:
    """Make `generation` active for new work. `by`: "user" (Apply) or "maintenance:auto". Returns the decision; on a
    failed first check, the rollback's decision (its "action" says which)."""
    from .. import env_runner
    from . import compat, review, revocations

    with store.locked(environ):
        target = env_runner.generation(m, generation, environ)
        from ..bundle_admission import local_workflow
        if target is not None and local_workflow(target.m):
            from . import workflow
            from .. import bundle_review
            if by != 'user':
                raise Failed('Interactive updates require explicit Review and Apply; the current version stays selected')
            try:
                workflow.require_accepted(m, generation, bundle_review.identity(target), environ)
            except ValueError as exc:
                raise Failed(str(exc)) from exc
        if target is not None and (notice := revocations.revoked(target.m, target.m.version, environ)) is not None:
            raise Failed(f"{target.m.name} {target.m.version} was revoked by its maintainers: {notice['why']}; it isn't "
                         "applied")  # whoever asks: a person's Apply, or automatic maintenance (#402 review, item 3)
        active = env_runner.selected(m, environ)
        if target is not None and active is not None:
            changes = compat.compare(active.m, target.m)
            if not changes["compatible"]:
                mark = review.fingerprint(target.m, generation, changes["review"])
                if review.accepted(target.m, mark, environ) is None:
                    raise Failed(f"Needs review: {compat.words(changes['review'])}")
                if step := review.missing_setup(active.m, target.m, environ):
                    raise Failed(f"Needs review: First: {step}")
        if (pin := policy.pinned(m.key, environ)) and pin != generation:
            if not by.startswith("user"):
                raise Failed(f"{m.name} is pinned to the version it runs; nothing moves it unattended")
            if env_runner.generation(m, generation, environ) is None:  # the pin stays unless the move can happen
                raise Failed(f"that version of {m.name} is no longer here, ready and proven")
            policy.leave_pin(m, generation, environ)  # nested writes share this transition lock
        before = active
        try:
            found = env_runner.promote(m, generation, by=by, environ=environ)
        except env_runner.Failed as exc:
            raise Failed(str(exc)) from exc
        decision = _decide({"key": m.key, "action": "promote", "by": by, "from": before.folder.name if before else "",
                            "fromVersion": before.m.version if before else "", "to": generation,
                            "version": found.m.version, "proof": {k: found.proof.get(k) for k in ("ok", "at", "scope")},
                            "policy": policy.get(m.key, environ)["policy"]}, environ)
        if before is not None and before.folder.name != generation:
            pins.add(m.key, before.folder.name, "rollback", generation, environ)
    if not check:
        return decision
    return check_after(m, decision, by=by, environ=environ)


def check_after(m: catalog.Manifest, promotion: dict, *, by: str, environ: Mapping[str, str] = os.environ) -> dict:
    """The first check after a promotion, recorded (a `checked` decision); on failure, the rollback's decision."""
    why = first_check(m, promotion["to"], environ)
    _decide({"key": m.key, "action": "checked", "of": promotion["at"], "to": promotion["to"], "ok": not why,
             **({"why": why} if why else {})}, environ)
    if not why:
        return promotion
    back = rollback(m, by=f"{by}:first-check", why=why, environ=environ)
    policy.hold(m.key, f"the update to {promotion.get('version', 'the newer version')} failed its first check: {why}",
                environ)
    return back


def rollback(m: catalog.Manifest, *, by: str, why: str = "", environ: Mapping[str, str] = os.environ) -> dict:
    """Make the generation the last promotion replaced active again. Raises Failed when there is none to go back to."""
    from .. import env_runner

    with store.locked(environ):
        rows = decisions(m.key, environ, moves=True)
        last = rows[-1] if rows else None
        if last is None or last.get("action") != "promote" or not last.get("from"):
            raise Failed(f"{m.name} has no earlier version to go back to")
        try:
            found = env_runner.promote(m, last["from"], by=by, environ=environ)
        except env_runner.Failed as exc:
            raise Failed(str(exc)) from exc
        decision = _decide({"key": m.key, "action": "rollback", "by": by, "from": last["to"],
                            "fromVersion": last.get("version", ""), "to": last["from"], "version": found.m.version,
                            "why": why}, environ)
        pins.remove(m.key, "rollback", last["to"], environ)
        if by != "user":  # every rollback but a person's own Undo (which ends the pin itself): the pin no longer holds what runs
            policy.leave_pin(m, last["from"], environ, by="maintenance", how="automatic-rollback",
                             why=f"it went back to {found.m.version} by itself after its first check failed, "
                                 "so it is no longer pinned")
    return decision


def undo(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """The user's Undo of the last promotion: back to the version before, held at review until they choose again."""
    with store.locked(environ):
        decision = rollback(m, by="user", why="you pressed Undo", environ=environ)
        policy.leave_pin(m, decision["to"], environ)  # a pin to the version undone no longer holds what runs
        policy.hold(m.key, f"you undid the update to {decision['fromVersion'] or 'the newer version'}", environ)
    return decision
