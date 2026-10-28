"""The update state of one entry, in the H13 status vocabulary (ticket 59), for every surface: the card, the setup
sheet, the agent and the maintenance overview.

One state at a time (STATES), with its words and one next action, beside facts kept apart from it: the policy, the last
check, the active generation with its own dates, the update candidate, and the generations held by work. Readiness's
availability statuses (ready, needs-setup, unavailable, broken) are not changed by any of this.

Reading is cheap: the policy, checks, attempts, decisions and pins files, and the environment facts the display already
observes. Nothing here starts a check, a download, a build or package code.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Mapping

from gpu_worker import manifests as catalog

from . import discover, pins, policy, prepare, promote

STATES = ("none", "available", "preparing", "checking", "ready-to-apply", "applied", "needs-review", "fix-pending",
          "failed", "unavailable", "pinned")
STALE_S = 2 * discover.EVERY_S


def _check(key: str, environ: Mapping[str, str]) -> dict:
    from .. import catalog_news

    rec = discover.last(key, environ)
    if not rec:
        return {"state": "never" if catalog_news.automatic(environ) else "off"}
    state = "ok" if rec.get("ok") else "failed"
    try:
        age = time.time() - time.mktime(time.strptime(rec.get("at", ""), "%Y-%m-%dT%H:%M:%S"))
    except ValueError:
        age = 0.0
    if state == "ok" and age > STALE_S:
        state = "stale"
    return {"state": state, "at": rec.get("at", ""), **({"why": rec["why"]} if rec.get("why") else {})}


def _when(stamp: str) -> str:
    """A kept time ("2026-10-27T23:42:00", this Mac's local time) as people read it ("Oct 27, 11:42 pm"); as given when it
    isn't one."""
    from ..library import when_words

    try:
        return when_words(time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S"))) or stamp
    except ValueError:
        return stamp


def _current(m: catalog.Manifest, environ: Mapping[str, str]) -> str:
    from .. import envs

    try:
        return envs.generation(m, environ)
    except (OSError, KeyError):
        return ""


def _receipt(folder) -> dict:
    from .. import envs
    from . import store

    return store.read(folder / envs.RECEIPT, {})


def of(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """The entry's update state (see _of). Inside a display scope (one wire build) it is computed once, whichever
    surfaces ask: the wire's setup.maintenance, each readiness, the card's update line. Read it, never change it."""
    from .. import env_observations

    kept = env_observations.memo("maintenance-status")
    if kept is None:
        return _of(m, environ)
    key = (m.key, id(environ))
    if key not in kept:
        kept[key] = _of(m, environ)
    return kept[key]


def _of(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> dict:
    """{"state", "says", "next", "policy", "check", "active", "candidate", "held", ...}; {} for an entry maintenance
    doesn't cover (no environment of its own)."""
    from .. import env_runner

    if m.runner != "python-env":
        return {}
    rec = policy.get(m.key, environ)
    allowed, less = policy.effective(m, environ)
    active = env_runner.selected(m, environ)
    current = _current(m, environ)
    att = prepare.last(m.key, environ)
    rows = promote.decisions(m.key, environ, moves=True)
    last = rows[-1] if rows else {}
    held = pins.of(m.key, environ)
    picked = env_runner.chosen_by(m, environ)
    out = {
        "policy": {"policy": rec["policy"], "words": policy.WORDS[rec["policy"]], "effective": allowed,
                   **{k: rec[k] for k in ("by", "at", "how", "held", "generation", "version") if rec.get(k)},
                   **({"less": less} if less else {})},
        "check": _check(m.key, environ),
        "active": None if active is None else {
            "generation": active.folder.name, "version": active.m.version,
            "installedAt": _receipt(active.folder).get("at", ""), "chosenBy": picked.get("by", ""), "chosenAt": picked.get("at", ""),
            **({"promotedAt": last["at"]} if last.get("action") == "promote" and last.get("to") == active.folder.name else {}),
            "proof": {k: active.proof.get(k) for k in ("ok", "at", "scope") if k in active.proof}},
        "candidate": None,
        "held": held,
        "next": None,
    }
    out["defect"], out["maintained"] = _notices(m, active, environ)
    out["affected"] = _affected(m, active, environ)
    # The candidate: the entry's own fetched snapshot when a check found one, else the pinned catalog's generation,
    # unless a promotion already moved away from that one (the catalog is then behind the entry: discover.py).
    found = discover.last(m.key, environ).get("found") or {}
    from_snapshot = bool(found.get("fetched") and found.get("snapshot") and found.get("generation"))
    current, version = (found["generation"], found.get("version", m.version)) if from_snapshot else (current, m.version)
    if not from_snapshot and current in discover.superseded(m, environ):
        current = active.folder.name if active is not None else current
    newer = active is not None and bool(current) and current != active.folder.name
    from .. import env_observations
    observed = env_observations.get(m, environ)  # the display's observed facts: no disk read on the event loop
    proven_candidate = ((observed.installed(current) if observed is not None
                         else env_runner.generation(m, current, environ)) if newer else None)
    proven = proven_candidate is not None
    if newer:
        out["candidate"] = {"generation": current, "version": version, "proven": proven}
    target = att.get("target") or {}
    about_now = bool(target) and (not target.get("generation") or target.get("generation") == current)
    in_use = active.m.version if active is not None else "the version in use"

    def said(state: str, says: str, nxt: str | None = None, **more) -> dict:
        # One set of words for the card and the agent: whether anyone maintains it, and a known defect, said in `says`.
        kept = out.get("maintained")
        if kept and kept.get("why") and kept["why"] not in says:
            label = "Needs attention" if kept["state"] == "attention" else "Not maintained"
            says = "; ".join(x for x in (says, f"{label}: {kept['why']}") if x)
        if out.get("affected"):  # exports a defective version made: offered again, never rewritten (MT7)
            n = len(out["affected"])
            says = "; ".join(x for x in (says, f"{n} export{'s' if n > 1 else ''} made by a version with a known defect "
                                               "can be made again; whether each shows it depends on what was in view") if x)
        return {**out, "state": state, "says": says, "next": nxt, **more}

    if rec["policy"] == "pin":
        if active is not None and active.folder.name == rec.get("generation"):
            return said("pinned", f"Pinned to {rec.get('version') or in_use}")
        out["policy"]["broken"] = f"pinned to {rec.get('version')}, which isn't the version running"
    if att.get("state") in prepare.RUNNING:
        if prepare.alive(att):  # no Stop once applying: the pointer may move, and its first check must follow
            return said(att["state"], att.get("says", "") + ("; its first check follows" if att["state"] == "applying"
                                                             else ""), None if att["state"] == "applying" else "stop")
        if att["state"] == "applying":  # promoted, or about to be, when it stopped: its first check runs again
            return said("checking", f"Checking update: the first check after applying {target.get('version', '')} "
                                    "didn't finish; it runs again", "retry")
        return said("failed", f"Update failed: it stopped before it finished. {in_use} stays in use", "retry",
                    why="it stopped before it finished")
    from . import adapt
    if adapt.derived(m):
        base, report = adapt.latest(m, environ)
        if base is not None and report:
            left = adapt.unresolved(Path(report["folder"]), report)
            ready = not left  # merge once nothing is left to resolve, whether or not its base was kept here
            return said("ready-to-apply" if ready else "needs-review", adapt.words(report, left),
                        "merge" if ready else None, derived=report["folder"], conflicts=left)
        if base is not None:
            return said("available", f"Update available: {base.name} {base.version} changed what yours was made from",
                        "retry")
    if newer:
        from . import revocations
        if (notice := revocations.revoked(m, version, environ)) is not None:  # never offered, never applied, never accepted
            return said("unavailable", f"Update unavailable here: {version} was revoked by its maintainers: "
                                       f"{notice['why']}", None, why=notice["why"])
    if about_now and att.get("state") == "failed":  # what stays in use is said as it is now, not as it was
        return said("failed", f"Update failed: {att.get('why') or 'its attempt failed'}. {in_use} stays in use", "retry",
                    why=att.get("why", ""))
    # Local interactive updates have an explicit trial -> receipt review -> Apply boundary.
    # Inspect only metadata/receipts here; this never opens the scientific host.
    if proven_candidate is not None:
        from ..bundle_admission import local_workflow
        if local_workflow(proven_candidate.m):
            from .. import bundle_review
            from . import workflow, review
            digest = bundle_review.identity(proven_candidate)
            out['candidate'].update(workflow=True, digest=digest)
            changed_waits = bool(att.get('fingerprint') and review.accepted(proven_candidate.m, att['fingerprint'], environ) is None)
            if not changed_waits and not att.get('setup'):
                if not workflow.accepted(m, current, digest, environ):
                    return said('needs-review', 'The update’s local workflow is retained. Review update results in its setup before Apply.', workflowReview=current)
                return said('ready-to-apply', f'Ready to apply: {version}. Its saved state will be reopened and rendered; the scientific operation is not rerun.', 'apply')
    if about_now and att.get("state") in ("needs-review", "unavailable"):
        accept = att["state"] == "needs-review" and att.get("fingerprint") and not att.get("setup")
        return said(att["state"], att.get("says", ""), "accept" if accept else None,
                    **{k: att[k] for k in ("why", "review", "setup", "prepared", "fingerprint") if att.get(k)})
    if last.get("action") == "rollback" and last.get("by", "").endswith(":first-check") and newer:
        return said("failed", f"Update failed: {last.get('why')}. {in_use} stays in use", "retry", why=last.get("why"))
    fixes = (out.get("defect") or {}).get("fixedIn") == version and (out.get("defect") or {}).get("issue")
    fixing = f" (fixes {out['defect']['issue']})" if fixes else ""
    if proven:  # Apply only what is here, ready and proven (maintenance-apply refuses anything else)
        return said("ready-to-apply", f"Ready to apply: {version}{fixing}" + (f" ({less})" if less else ""), "apply")
    if newer and not from_snapshot and (failed := env_runner.update_failed(m, environ)):
        return said("failed", failed, "retry")
    if newer:
        how = "" if allowed in ("review", "auto") else ". Install checks it beside the one in use"
        return said("available", f"Update available: {version}{fixing}{how}")
    if found.get("commit") and not found.get("fetched"):
        return said("available", f"Update available: a newer {m.name} in its catalog ({found['commit'][:7]})")
    known = out.get("defect") or {}
    if known.get("issue") and not known.get("fixedIn"):  # the fix exists upstream, no distributable build yet (MT7)
        return said("fix-pending", f"Fix identified — compatible build pending ({known['issue']})")
    if last.get("action") == "promote" and active is not None and last.get("to") == active.folder.name:
        undo = any(p.get("kind") == "rollback" and p.get("owner") == last["to"] for p in held)
        return said("applied", f"Updated to {last.get('version') or active.m.version} ({_when(last['at'])})",
                    "undo" if undo else None)
    if out["check"]["state"] == "ok":  # its last check found nothing newer (ticket 67: only Activity said so)
        return said("none", f"Up to date · checked {_when(out['check']['at'])}")
    return said("none", "")


def _affected(m: catalog.Manifest, active, environ: Mapping[str, str]) -> list[dict]:
    """Retained exports made by a version a fix covers, once a version without the defect runs (MT7)."""
    if active is None:
        return []
    from . import revocations
    from .. import view_export

    out: dict[str, dict] = {}
    for row in revocations.fixes(m, environ):
        if row["fixedIn"] and active.m.version not in row["versions"]:
            for one in view_export.affected(m.key, row["versions"], environ):
                out.setdefault(one["file"], one)
    return list(out.values())


def _notices(m: catalog.Manifest, active, environ: Mapping[str, str]) -> tuple[dict | None, dict | None]:
    """(a known result defect of the active version, whether anyone maintains the entry), from the catalog's own
    notices (revocations.py) and the checks: a recent check never means someone maintains it (MT3)."""
    from . import revocations

    defect = revocations.revoked(m, active.m.version, environ) if active is not None else None
    known = revocations.fix(m, active.m.version, environ) if active is not None and defect is None else None
    if known:  # a known defect with its fix declared (MT7): attention, linked to its issue
        return known, {"state": "attention", "why": f"a known defect in {active.m.version}: {known['why']}"
                       + (f" ({known['issue']})" if known["issue"] else "")}
    check = discover.last(m.key, environ)
    declared = revocations.unmaintained(m, environ)
    removed = check.get("removed") or {}
    if defect:
        return defect, {"state": "attention", "why": f"{active.m.version} was revoked by its maintainers: {defect['why']}"
                        + (f" ({defect['link']})" if defect["link"] else "")}
    if declared:
        return None, {"state": "unmaintained", "why": declared["why"], "since": declared["since"]}
    if removed:
        return None, {"state": "unmaintained", "since": check.get("at", ""),
                      "why": f"removed from its catalog at {removed['commit'][:7]}; it keeps working here"}
    since = check.get("failingSince", "")
    if int(check.get("failures", 0)) >= discover.UNREACHABLE and _older_than(since, discover.UNREACHABLE_S):
        # A Mac offline for a while is no abandoned package: said as what happened, not as unmaintained.
        return None, {"state": "attention", "since": since,
                      "why": f"couldn't reach its source since {since} ({check.get('why', 'no answer')})"}
    return None, None


def _older_than(stamp: str, seconds: float) -> bool:
    try:
        return time.time() - time.mktime(time.strptime(stamp, "%Y-%m-%dT%H:%M:%S")) >= seconds
    except ValueError:
        return False


def built_in(m: catalog.Manifest) -> dict:
    """An entry that comes with the app and is updated with it, not maintained here (no policy, no check of its own):
    one line for its Details (ticket 67: built-in Cellpose-SAM said nothing about its updates). {} for any other."""
    if m.namespace or m.channel or m.runner == "python-env" or m.runs == "cloud":
        return {}  # yours, a channel's, maintained here (of), or a provider's model
    from .. import build_info

    hooke = build_info.version()
    return {"state": "none", "says": f"Updated with Hooke {hooke}" if hooke else "Updated with Hooke: it comes with this "
            "copy of the app", "next": None, "builtIn": True, "version": m.version}


def words(m: catalog.Manifest, environ: Mapping[str, str] = os.environ) -> str:
    """The one line a card or the agent says for the entry's update state ("" when there is nothing to say)."""
    return of(m, environ).get("says", "")


def wire(environ: Mapping[str, str] = os.environ) -> dict[str, dict]:
    """The update state of every entry maintenance covers and that is installed here, by key (the capabilities wire's
    setup.maintenance): the card's update line, its policy choice and its next action; and each built-in entry's one
    line, that it is updated with Hooke (`built_in`)."""
    from .. import env_runner

    out = {}
    for m in [*catalog.manifests().values(), *catalog.mine(environ).values()]:
        if m.runner == "python-env" and env_runner.installed_any(m, environ):
            out[m.key] = of(m, environ)
        elif line := built_in(m):
            out[m.key] = line
    return out
