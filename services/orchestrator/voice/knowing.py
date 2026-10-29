"""Knowing: what the live partner knows of what happened in Hooke, from Activity (spec step 1, section 4.3).

Activity records every request and its steps, the user's clicks, drags and commands, setting changes, model loads and
researchers (orchestrator/activity.py). Two ways in, both from facts the app recorded, never a guess:
- **the `activity` tool** (`read`): recent lines, newest first, optionally since some seconds ago and of some kinds,
  when the partner needs them ("what did I just do?", "what ran on this?", "close that" after the user's own click);
- **the user's own actions as silent context** (`UserActions`): a line the user made by hand (a Tab opened, a table
  moved, a researcher started) is told to the voice as context, coalesced (one note for a burst), never spoken. Behind
  `MICROMIND_PARTNER_USER_EVENTS=1` until the journey shows it helps at no speed cost ("context earns its place").
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Awaitable, Callable

READ_MAX = 20  # lines one read returns
COALESCE_S = 1.2  # the user's actions within this of each other are one note
KINDS = ("request", "click", "command", "setting", "model", "research")
ASSISTANT = "Assistant:"  # the partner's own actions (hands.py) are not the user's

SPEC = {"type": "object", "properties": {
    "since": {"type": "number", "description": "only lines from the last this many seconds"},
    "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)},
              "description": "request (asked by voice or chat, with its steps), click (the user's clicks and drags), "
                             "command (studio actions, the user's and yours), setting, model (loads), research"}}}
DESCRIPTION = ("What happened in Hooke lately, newest first, from its Activity: requests and their steps, the user's "
               "clicks and studio actions, setting changes, model loads, researchers. For \"what did I just do?\", "
               "\"what ran on this?\", or which thing the user just opened, moved or closed.")


def line_text(line: dict, now: float) -> dict:
    out = {"kind": line.get("kind"), "said": line.get("text"), "state": line.get("state"),
           "secondsAgo": round(max(0.0, now - float(line.get("at") or now)), 1)}
    if line.get("steps"):
        out["steps"] = [{k: s.get(k) for k in ("tool", "ok", "summary") if s.get(k) is not None}
                        for s in line["steps"][:6]]
    if line.get("ref"):
        out["ref"] = line["ref"]
    return out


def read(hub, args: dict, *, clock: Callable[[], float] = time.time) -> dict:
    """The `activity` tool: recent Activity lines, newest first."""
    from .. import activity

    found = activity.sink(hub)
    if found is None:
        return {"ok": False, "error": "Activity is not kept here"}
    now = clock()
    since = args.get("since")
    kinds = [k for k in (args.get("kinds") or []) if k in KINDS]
    lines = []
    for line in reversed(list(found.recent.values())):
        if kinds and line.get("kind") not in kinds:
            continue
        if isinstance(since, (int, float)) and now - float(line.get("at") or 0) > since:
            break
        lines.append(line_text(line, now))
        if len(lines) >= READ_MAX:
            break
    return {"ok": True, "lines": lines, "note": "Facts recorded by Hooke; the user's own actions are theirs, lines "
                                                "starting \"Assistant:\" are yours."}


def by_user(line: dict) -> bool:
    """A line the user made by hand: a click or a studio action that is not the partner's own."""
    return line.get("kind") in ("click", "command") and not str(line.get("text") or "").startswith(ASSISTANT)


class UserActions:
    """The user's own actions, coalesced, told to the voice as silent context (never spoken)."""

    def __init__(self, tell: Callable[[str], Awaitable[None]], *, clock: Callable[[], float] = time.monotonic) -> None:
        self.tell, self.clock = tell, clock
        self.pending: list[str] = []
        self.seen: set[str] = set()
        self._flush: asyncio.Task | None = None

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("MICROMIND_PARTNER_USER_EVENTS") == "1"

    def line(self, line: dict) -> None:
        if not by_user(line) or line.get("state") == "running":
            return
        key = f'{line.get("id")}:{line.get("state")}'
        if key in self.seen:
            return
        self.seen.add(key)
        self.pending.append(str(line.get("text") or "")[:200])
        if self._flush is None or self._flush.done():
            with contextlib.suppress(RuntimeError):
                self._flush = asyncio.get_running_loop().create_task(self._later())

    async def _later(self) -> None:
        await asyncio.sleep(COALESCE_S)
        lines, self.pending = self.pending[-6:], []
        if lines:
            await self.tell("The user, by hand, just now (context only, not their words; say nothing about it unless "
                            "asked): " + "; ".join(lines))
