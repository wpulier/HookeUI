"""The studio's maintenance commands, registered in the one command table (commands.COMMANDS) when the app imports
this module. Each answers with what happened and the entry's update state as status.of says it, and every studio is
told (catalog_changed). A policy is a person's: its command comes from the screen, or confirms an agent's proposal.
"""
from __future__ import annotations

import asyncio

from gpu_worker import manifests as catalog

from ..brain.registry import ToolFailed
from ..commands import command
from . import discover, policy, promote, status


def _entry(msg: dict) -> catalog.Manifest:
    m = catalog.get(str(msg.get("model") or ""))
    if m is None:
        raise ToolFailed("there is no such entry", next="reload the Models page")
    return m


def _maintainer(app):
    found = getattr(app, "maintainer", None)
    if found is None:
        raise ToolFailed("maintenance isn't running in this app", next=None)  # no next: nothing the user can start
    return found


def _reply(app, m: catalog.Manifest, says: str) -> dict:
    from .. import activity

    app.catalog_changed()
    activity.activity(app.hub, "model", says, state="done", ref={"model": m.key})
    return {"ok": True, "says": says, "update": status.of(m)}


@command("maintenance-policy")
async def maintenance_policy(app, client, msg) -> dict:
    """Record the entry's policy: manual, review, auto or pin (`generation` for pin; the active one when left out)."""
    m = _entry(msg)
    how = "agent-proposal-confirmed" if msg.get("confirming") else "screen"
    try:
        rec = await asyncio.to_thread(policy.record, m, str(msg.get("policy") or ""), by="you", how=how,
                                      generation=str(msg.get("generation") or ""))
    except policy.NotRecorded as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the refusal says what to do
    maintainer = getattr(app, "maintainer", None)
    if maintainer is not None:
        if rec["policy"] in ("review", "auto"):
            maintainer.now(m.key)
        else:
            maintainer.cancel(m.key)  # revoked: an attempt in progress ends before it promotes
    return _reply(app, m, f"{m.name}: {policy.WORDS[rec['policy']].lower()}" +
                  (f" ({rec['version']})" if rec.get("version") else ""))


@command("maintenance-check")
async def maintenance_check(app, client, msg) -> dict:
    """Check the entry's source now. Under review or auto the attempt that follows may obtain and prepare it; under
    manual only the newer commit is said, nothing is fetched."""
    m = _entry(msg)
    from . import prepare
    if m.key in await asyncio.to_thread(prepare.unfinished):  # its first check after applying, under any policy
        _maintainer(app).now(m.key)
        return _reply(app, m, f"Checking {m.name}'s update again: its first check after applying didn't finish")
    if await asyncio.to_thread(status.shared_file, m):  # no online revisions: never "up to date"
        return _reply(app, m, f"{m.name} came from a shared file, so it doesn't update itself. A newer shared file is "
                              "added as its own entry")
    if app.day.local_only:
        raise ToolFailed("this session is local only, so nothing is checked online", next=None)  # no next: the session's local-only choice is the user's
    allowed, _less = policy.effective(m)
    if allowed in ("review", "auto"):
        _maintainer(app).now(m.key)
        return _reply(app, m, f"Checking {m.name} for an update")
    rec = await asyncio.to_thread(discover.check, m)
    says = (f"Couldn't check {m.name} for an update: {rec.get('why')}" if not rec["ok"] else
            f"{m.name}: an update is available" if rec.get("found") else f"{m.name} is up to date")
    return _reply(app, m, says)


@command("maintenance-apply")
async def maintenance_apply(app, client, msg) -> dict:
    """Your Apply: the proven update candidate (status.of's, from its own snapshot or the catalog) becomes active for
    new work, with its first check. Anything not here, ready and proven is refused, with where to get it."""
    from . import prepare

    m = _entry(msg)
    now = await asyncio.to_thread(status.of, m)
    candidate = now.get("candidate") or {}
    if not candidate.get("proven"):
        raise ToolFailed(f"{m.name}'s update isn't prepared and proven here yet", next="install it from its card first")
    if candidate.get('workflow') and (msg.get('generation') != candidate['generation'] or msg.get('digest') != candidate.get('digest')):
        raise ToolFailed('The update card changed', next='review its exact candidate again')
    try:
        decision = await asyncio.to_thread(prepare.apply, m, candidate["generation"], digest=msg.get('digest'))
    except promote.Failed as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the reason says why the version can't be made active
    if decision["action"] == "rollback":
        return _reply(app, m, f"{m.name}'s update failed its first check: {decision['why']}. "
                              f"{decision['version']} stays in use")
    return _reply(app, m, f"{m.name} runs {decision['version']} now (you applied it)")


@command("maintenance-undo")
async def maintenance_undo(app, client, msg) -> dict:
    """Your Undo of the last promotion: the version before is active again, and automatic maintenance waits for you."""
    m = _entry(msg)
    try:
        decision = await asyncio.to_thread(promote.undo, m)
    except promote.Failed as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: nothing was promoted, so there is nothing to undo
    return _reply(app, m, f"{m.name} runs {decision['version']} again (you undid the update)")


@command("maintenance-stop")
async def maintenance_stop(app, client, msg) -> dict:
    """Stop the entry's update attempt; the active version is untouched."""
    from . import prepare

    m = _entry(msg)
    stopped = await asyncio.to_thread(_maintainer(app).cancel, m.key)
    if stopped and (await asyncio.to_thread(prepare.last, m.key)).get("state") == "applying":
        return _reply(app, m, f"{m.name}'s update was being applied: its first check still runs, and rolls it back if it "
                              "fails")
    return _reply(app, m, f"Stopped {m.name}'s update" if stopped else f"{m.name} has no update being prepared")


@command("maintenance-accept")
async def maintenance_accept(app, client, msg) -> dict:
    """Your acceptance of exactly what an update changed (its Needs review list, by its fingerprint); the attempt then
    continues: auto promotes it, review waits for Apply. A key or licence it newly needs is still its own setup step."""
    from . import review

    m = _entry(msg)
    how = "agent-proposal-confirmed" if msg.get("confirming") else "screen"
    try:
        accepted = await asyncio.to_thread(review.accept_current, m, msg.get("fingerprint"), by="you", how=how)
    except (ValueError, OSError) as exc:
        raise ToolFailed(str(exc), next="look at its card again") from exc
    maintainer = getattr(app, "maintainer", None)
    if maintainer is not None:
        maintainer.now(m.key)
    return _reply(app, m, f"You accepted what {m.name}'s update changes: " +
                  "; ".join(r["says"] for r in accepted["review"]))


@command("maintenance-merge")
async def maintenance_merge(app, client, msg) -> dict:
    """Your version, brought onto the newest of what it was made from: the merged candidate written into your folder,
    your files as they were kept beside it. Refused while a conflict marker remains."""
    from . import adapt

    m = _entry(msg)
    try:
        row = await asyncio.to_thread(adapt.apply, m)
    except ValueError as exc:
        raise ToolFailed(str(exc), next=None) from exc  # no next: the refusal says which files to resolve
    return _reply(app, m, f"{m.name} now builds on {row['base']['id']} {row['base']['to']}; your previous files are kept "
                          f"in {row['kept']}. Install it to use it")
