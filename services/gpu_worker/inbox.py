"""The GPU worker's queue (contract 5 of docs/design/2026-10-06-captures-operations-evidence.md):
requests filed by priority class, and refused without running when they no longer should run.

- "click" (an explicit click): first, the newest click first;
- "interactive" (other short work): next, in order;
- "background" (long or optional work): next, in order;
- "research" (a researcher's own operations, orchestrator/research/hands.py): only when nothing else waits.

A request a newer one of the same kind replaced (the client's "drop") is answered "superseded", one
still queued past its deadline "expired", and a tracking step older than one already taken
"superseded". Kept free of torch so it can be tested on its own.
"""
from __future__ import annotations

import itertools
import queue
import time

CLASSES = {"click": 0, "interactive": 1, "background": 2, "research": 3}


class Inbox:
    def __init__(self, urgent_ops: set[str]) -> None:
        self.urgent_ops = urgent_ops
        self.queue: queue.PriorityQueue = queue.PriorityQueue()
        self.order = itertools.count(1)
        self.dropped: set = set()
        self.track = (-1, -1)  # the newest (session, seq) of a tracking step taken

    def put(self, req: dict | None) -> None:
        if req is None:
            self.queue.put((-1, 0, 0, None))
            return
        if req.get("op") == "drop":
            self.dropped.update(req.get("ids") or [])
            return
        n = next(self.order)
        cls = self.klass(req)
        self.queue.put((cls, -n if cls == 0 else n, n, req))

    def klass(self, req: dict) -> int:
        """Its priority class: 0 a click, 1 other short work, 2 background, 3 a researcher's."""
        return CLASSES.get(req.get("_priority"), 0 if req.get("op") in self.urgent_ops else 2)

    def get(self):
        return self.queue.get()[3]

    def get_nowait(self):
        """(class, request) or raises queue.Empty."""
        item = self.queue.get_nowait()
        return item[0], item[3]

    def put_back(self, cls: int, req: dict) -> None:
        n = next(self.order)
        self.queue.put((cls, -n if cls == 0 else n, n, req))

    def refusal(self, req: dict, now: float | None = None) -> dict | None:
        """The reply for a request that must not run now, or None."""
        rid = req.get("id")
        if rid in self.dropped:
            self.dropped.discard(rid)
            return {"id": rid, "ok": False, "superseded": True, "error": "replaced by a newer one before it ran"}
        deadline = req.get("_deadline")
        if deadline is not None and (now if now is not None else time.time()) > float(deadline):
            return {"id": rid, "ok": False, "expired": True, "error": f"{req.get('op')} waited past its deadline in the queue"}
        if req.get("op") == "track" and req.get("session") is not None:
            step = (int(req.get("session")), int(req.get("seq") or 0))
            if step <= self.track:
                return {"id": rid, "ok": False, "superseded": True, "error": "a tracking step from before a newer one"}
            self.track = step
        return None
