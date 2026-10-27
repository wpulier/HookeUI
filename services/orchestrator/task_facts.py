"""What stands between a scientific task and doing it here, as typed facts for the agent (ticket 59's MT6).

The agent must tell apart why something can't be done now: no input yet, no scale for a physical measurement, this Mac
can't run it, a capability that is here but not prepared, no integration for it at all, a permission only the person
grants, or a scientific judgment that is the researcher's. `blockers` gives those as facts with their reason, drawn from
what the app already knows (readiness rows, the input's calibration, whether a camera is in use); the model words the
answer. Nothing here decides which method is right, and nothing claims a tool, guide or video it wasn't given.
"""
from __future__ import annotations

REASONS = ("missing-input", "missing-calibration", "unavailable-hardware", "unprepared", "missing-integration",
           "missing-permission", "scientific-judgment", "edition-restriction", "missing-execution-route",
           "broken-installation", "unavailable")


def blockers(*, have_input: bool, calibrated: bool | None, needs_calibration: bool, camera: bool,
             candidates: list[dict], permission: str = "", judgment: str = "") -> dict:
    """`candidates`: readiness rows of the capabilities that do the task ({key, name, status, word, next, why}).
    Returns {"ready": [keys], "blockers": [{reason, ...}]}; no blocker and a ready key means it can be done now."""
    out: list[dict] = []
    if not have_input:
        out.append({"reason": "missing-input", "upload": True, "capture": camera})  # capture only when a camera is in use
    elif needs_calibration and not calibrated:
        out.append({"reason": "missing-calibration", "pixelsOnly": True})
    ready = [c["key"] for c in candidates if c.get("status") == "ready"]
    if not ready:
        grouped = {}
        causes = {'edition': 'edition-restriction', 'platform': 'unavailable-hardware',
                  'route': 'missing-execution-route'}
        for candidate in candidates:
            state = candidate.get('status')
            if state == 'needs-setup':
                reason = 'unprepared'
            elif state == 'broken':
                reason = 'broken-installation'
            else:
                step = next((row for row in candidate.get('steps', []) if row.get('kind') in causes),
                            candidate.get('next') if isinstance(candidate.get('next'), dict) else {})
                reason = causes.get(step.get('kind'), 'unavailable')
            grouped.setdefault(reason, []).append({key: candidate.get(key) for key in
                ('key', 'name', 'status', 'word', 'why', 'next', 'steps')})
        out.extend({'reason': reason, 'capabilities': rows} for reason, rows in grouped.items())
        if not candidates:
            out.append({'reason': 'missing-integration', 'contribute': True})
    if permission:
        out.append({"reason": "missing-permission", "what": permission})
    if judgment:
        out.append({"reason": "scientific-judgment", "what": judgment})
    return {"ready": ready, "blockers": out}
