"""A finding's evidence in the session (ADR 0015; docs/design/2026-10-07-the-partner.md §2.2).

A researcher may say which of the session's index entries its finding uses (`finish.evidence`: ids such as
e3 or r2) and which points rest on which entries (`finish.session_claims`: {point, evidence: [ids]}).
Before the result is announced, each id is resolved in the session's index (session_index.py), as points
from the web are looked for on their pages (claims.py). A point whose evidence does not resolve is marked
"evidence unavailable", kept word for word, never left standing as if it were supported. When the index
cannot be read, the point is "not checked", with why. An entry the researcher read at one version (a
population's revision) that has been corrected since is said so: the point rests on what it read then
(the partner's Amendment 1, C1).
"""
from __future__ import annotations

import re
import time

from .session_index import IndexUnavailable, NoEntry

MAX_RESOLVED = 16  # ids looked up for one result: more are "not checked", said so
CHECK_SECONDS = 8.0  # the whole check, so a slow index never pushes the finding past its watchdog
ID = re.compile(r"[\w.:-]{1,80}")


def ids_of(raw) -> list[str]:
    """Entry ids as the researcher gave them: well-formed, each once, every one (resolve looks up the first
    MAX_RESOLVED and says the rest were not checked)."""
    out = []
    for item in raw or []:
        text = str(item or "").strip()
        if ID.fullmatch(text) and text not in out:
            out.append(text)
    return out


def session_claims_of(raw) -> list[dict]:
    """The points that rest on the session's records, as {"point", "evidence": [ids]}, every one and whole."""
    out = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        point = " ".join(str(item.get("point") or "").split())
        ids = ids_of(item.get("evidence"))
        if point and ids:
            out.append({"point": point, "evidence": ids})
    return out


def resolve(ids: list[str], index, clock=time.monotonic, seen: dict[str, list[int]] | None = None) -> dict[str, dict]:
    """Each id in the session's index: {"found": True, "title", "kind", "time"}, {"found": False} (not found in
    the session's index), or {"found": None, "why"} (not checked). `index` is a SessionIndex, or None when the
    app gave no address. The first time the index does not answer, the rest are not checked either (each
    lookup could take seconds); at most MAX_RESOLVED ids and CHECK_SECONDS in all. Blocking: run it in a thread."""
    out: dict[str, dict] = {}
    why = None if index is not None else "the session's index was not available"
    started = clock()
    for n, entry_id in enumerate(ids):
        if why is None and n >= MAX_RESOLVED:
            why = f"only the first {MAX_RESOLVED} cited entries are checked"
        if why is None and clock() - started > CHECK_SECONDS:
            why = "the check ran out of time"
        if why is not None:
            out[entry_id] = {"found": None, "why": why}
            continue
        try:
            entry = (index.read(entry_id) or {}).get("entry") or {}
        except NoEntry:
            out[entry_id] = {"found": False}
            continue
        except IndexUnavailable as exc:
            why = f"the session's index did not answer ({exc})"[:160]
            out[entry_id] = {"found": None, "why": why}
            continue
        out[entry_id] = {"found": True, "title": entry.get("title"), "kind": entry.get("kind"),
                         "time": entry.get("time") or entry.get("at")}
        now, read = entry.get("version"), [v for v in (seen or {}).get(entry_id) or [] if isinstance(v, int)]
        if isinstance(now, int) and read and any(v != now for v in read):
            # It read an earlier version (perhaps the current one too): a point it formed may rest on either.
            out[entry_id]["corrected"] = {"read": sorted(read), "now": now}
    return out


def apply(result: dict, resolved: dict[str, dict]) -> None:
    """Writes what was found into the result: `evidence` as [{id, found, ...}], and each session claim's
    `found`: True when all its evidence is there, False when any of it is not (evidence unavailable),
    None when it could not be checked."""
    result["evidence"] = [{"id": i, **resolved.get(i, {"found": None, "why": "not checked"})} for i in result.get("evidence") or []]
    for claim in result.get("session_claims") or []:
        states = [resolved.get(i, {}).get("found") for i in claim["evidence"]]
        claim["found"] = False if False in states else (None if None in states else True)
        missing = [i for i in claim["evidence"] if resolved.get(i, {}).get("found") is False]
        if missing:
            claim["missing"] = missing
        corrected = [{"id": i, **resolved[i]["corrected"]} for i in claim["evidence"] if (resolved.get(i) or {}).get("corrected")]
        if corrected:
            claim["corrected"] = corrected


def unavailable(result: dict | None) -> list[dict]:
    """The points whose evidence is not in today's session."""
    return [c for c in (result or {}).get("session_claims") or [] if isinstance(c, dict) and c.get("found") is False]


def corrected(result: dict | None) -> list[dict]:
    """The points resting on an entry corrected since the researcher read it."""
    return [c for c in (result or {}).get("session_claims") or [] if isinstance(c, dict) and c.get("corrected")]


def _revisions(claim: dict) -> str:
    def read(versions) -> str:
        versions = versions if isinstance(versions, list) else [versions]
        return ("revision " if len(versions) == 1 else "revisions ") + " and ".join(str(v) for v in versions)
    return ", ".join(f"{c['id']}: read at {read(c['read'])}, now {c['now']}" for c in claim.get("corrected") or [])


def unchecked(result: dict | None) -> list[dict]:
    """The cited entries that could not be looked up, with why."""
    return [e for e in (result or {}).get("evidence") or [] if isinstance(e, dict) and e.get("found") is None]


def summary_line(result: dict | None) -> str:
    """One line on the evidence check, saying only what was checked, and what could not be ("" when nothing
    was cited)."""
    evidence = [e for e in (result or {}).get("evidence") or [] if isinstance(e, dict)]
    points = [c for c in (result or {}).get("session_claims") or [] if isinstance(c, dict)]
    if not evidence and not points:
        return ""
    checked = [e for e in evidence if e.get("found") is not None]
    parts = []
    if checked:
        found = [e for e in checked if e.get("found") is True]
        gone = [e["id"] for e in checked if e.get("found") is False]
        parts.append(f"{len(found)} of {len(checked)} checked entries were found in the session's index"
                     + (f"; not found: {', '.join(gone)}" if gone else ""))
    skipped = unchecked(result)
    if skipped:
        parts.append(f"{len(skipped)} cited {'entry' if len(skipped) == 1 else 'entries'} could not be checked "
                     f"({skipped[0].get('why') or 'not checked'})")
    missing = unavailable(result)
    if missing:
        parts.append(f"{len(missing)} of {len(points)} points rest on evidence not found in the session's index")
    changed = corrected(result)
    if changed:
        parts.append(f"{len(changed)} of {len(points)} points rest on records corrected since it read them "
                     f"({_revisions(changed[0])})")
    return "; ".join(parts)


def note(result: dict | None) -> str:
    """For the line that announces a result: what the session check could not stand behind ("" when all of
    it stands). The user always hears it, never only what was found (ADR 0015)."""
    missing = unavailable(result)
    if missing:
        first = " ".join(str(missing[0].get("point") or "").split())[:90]
        return (f" Note: {len(missing)} of its points rest on evidence not found in the session's index, "
                f"for example: {first}" + ("" if first.endswith((".", "!", "?")) else "."))
    changed = corrected(result)
    if changed:
        return (f" Note: {len(changed)} of its points rest on records corrected since it read them "
                f"({_revisions(changed[0])}): based on what they were then.")
    if unchecked(result):
        return " Note: some of the session's records it cites could not be checked."
    return ""
