"""Which defaults really ran (live 2026-10-11: "am I even on the right model?", so that a slower run is never taken
for a regression of the model that did not run).

Every place where a configured model did not run and another answered in its place notes it here once, where that
decision is made: the operation runner (an implementation's fallback: operations.Runner._record) and the fast pass
(the full look answered instead: brain.loop.quick). The meter's panel says the defaults ran, or names each exception
with how often and why (`recent`). Held in memory for the last WINDOW_S; the interaction log keeps the records.
"""
from __future__ import annotations

import time
from collections import deque

WINDOW_S = 600.0  # the last ten minutes


class Fallbacks:
    def __init__(self, clock=time.monotonic) -> None:
        self.clock = clock
        self.notes: deque = deque(maxlen=200)

    def note(self, role: str, default: str, used: str, why: str) -> None:
        """`role` ran `used` instead of its `default`, because `why` (plain words)."""
        self.notes.append((self.clock(), str(role), str(default), str(used), str(why)[:160]))

    def recent(self, window_s: float = WINDOW_S) -> list[dict]:
        """Each exception of the last `window_s`, grouped: its role, default, what ran, how often, the latest why."""
        now = self.clock()
        grouped: dict[tuple, dict] = {}
        for at, role, default, used, why in self.notes:
            if now - at > window_s:
                continue
            row = grouped.setdefault((role, default, used), {"role": role, "default": default, "used": used, "times": 0})
            row["times"] += 1
            row["why"], row["agoS"] = why, round(now - at)
        return list(grouped.values())


def of(hub) -> Fallbacks:
    """The hub's record (made on first use, so a stand-in hub has one too)."""
    found = getattr(hub, "fallbacks", None)
    if not isinstance(found, Fallbacks):
        found = Fallbacks()
        try:
            hub.fallbacks = found
        except AttributeError:
            pass
    return found
