"""What every request knows about the session beyond the active thread: a short digest.

The live view, the active thread's last exchanges and its summary are given by the brain
(brain/loop.py, brain/state.py). This adds the rest of the session in at most DIGEST_CHARS: which
session it is (its name, and since when when it began on an earlier day: ADR 0017), the other
threads (title, specimens, a line of their summary), specimens seen, the user's preferences that
have words for the models, researchers' work and the facts settled in it (day/memory.py). Anything
more specific comes from the recall tool, only when asked for.
"""
from __future__ import annotations

import time

from ..learner import PREFS as LEARNER_PREFS
from ..quiz import MISSED
from ..research.desk import made
from ..untrusted import inline
from .catalogue import days_before

DIGEST_CHARS = 900
# The preferences the digest words for the models; no other is repeated here. The studio's own layout (the shelf), the
# models chosen from now on (defaults.py), the look of outlines, the research cap and the scale are settings, told
# where the models need them; how to explain has its own line in every prompt (learner.py).
PREF_WORDS = {"userLeads": "the user wants to find things themselves (point only when asked)"}


def session_line(store) -> str:
    """Which session this is, when that says something: its name, and since when if it began on an earlier day
    ('Session "Leaf epidermis" (since yesterday)'); "" for an unnamed session of today."""
    began = time.strftime("%Y-%m-%d", time.localtime(store.created)) if getattr(store, "created", 0) else store.date
    since = ""
    if began != store.date:
        yesterday = days_before(store.clock(), 1)
        since = "since yesterday" if began == yesterday else "since " + time.strftime("%b %-d", time.localtime(store.created))
    name = getattr(store, "name", "")
    if not name and not since:
        return ""
    return (f'Session "{inline(name, 60)}"' if name else "This session") + (f" ({since})" if since else "")


def day_digest(store, research=None, facts: list[str] | None = None, limit: int = DIGEST_CHARS) -> str:
    parts = []
    which = session_line(store)
    others = [t for t in sorted(store.threads.values(), key=lambda t: t.n) if t.id != store.active_id and t.turns]
    if others:
        rows = []
        for t in others[-5:]:
            subjects = ", ".join(s["name"] for s in t.subjects[-3:])
            gist = (inline(t.summary.split(". ")[0], 120) + ".") if t.summary else ""  # model-written, from researchers' notes too
            rows.append(f'{t.id} "{t.title}"' + (f" ({subjects})" if subjects else "") + (f": {gist}" if gist else ""))
        parts.append("Other conversations: " + "; ".join(rows))
    named = [s for s in store.subjects.values() if s.get("named_by") == "user"]
    if named:
        parts.append("Specimens the user named: " + ", ".join(s["name"] for s in named[-4:]))
    seen = [s["name"] for s in store.subjects.values()]
    if len(seen) > 1:
        parts.append("Specimens seen: " + ", ".join(seen[-6:]))
    # How to explain is not repeated here: every prompt has its own line for it (learner.py).
    prefs = [PREF_WORDS[k] for k, v in store.prefs.items() if v and k in PREF_WORDS and k not in LEARNER_PREFS]
    if prefs:
        parts.append("Preferences: " + "; ".join(prefs))
    facts = facts if facts is not None else [f for f in store.digest.split("\n") if f.strip()]
    if facts:
        # First: what the user settled is what the digest is for, and the end is what is cut.
        parts.insert(0, "Settled: " + " | ".join(inline(f, 140) for f in facts[:6]))
    if research is not None:
        rows = []
        for s in research.researchers(session=store.sid)[-8:]:  # the session's own, any date (ids name its threads)
            title = inline((s.result or {}).get("title") or s.label or s.brief, 50)  # a researcher's words
            where = (s.origin or {}).get("thread")
            quiz = store.outcomes_of(s.rid) if hasattr(store, "outcomes_of") else {}
            how = ""
            if quiz:  # how the user did on its steps (quiz.py): what "go over what I missed" goes over
                right, missed = [k for k, v in quiz.items() if v == "right"], [k for k, v in quiz.items() if v in MISSED]
                how = f", quiz: {len(right)} right" + (f", to go over: {', '.join(sorted(missed))}" if missed else "")
            rows.append(f"{s.name} {made(s)} \"{title}\"" + (f" ({where})" if where else "") + how)
        if rows:
            parts.append("Researchers' work: " + "; ".join(rows))
    if which and parts:
        parts.insert(0, which)  # first: which session the rest is of
    text = " ".join(p.rstrip(".") + "." for p in parts)
    return text if len(text) <= limit else text[: limit - 1] + "…"
