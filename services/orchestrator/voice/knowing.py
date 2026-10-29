"""Knowing: what the live partner knows of what happened in Hooke, from Activity (spec step 1, section 4.3).

Activity records every request and its steps, the user's clicks, drags and commands, setting changes, model loads and
researchers (orchestrator/activity.py). Two ways in, both from facts the app recorded, never a guess, and both only of
the Project open (each kept line knows its Project: `Activity.project_of`):
- **the `activity` tool** (`read`): recent lines, newest first, optionally since some seconds ago and of some kinds,
  when the partner needs them ("what did I just do?", "what ran on this?", "close that" after the user's own click);
  each line with its receipt when it is one of the partner's own actions (verified, failed, uncertain, running);
- **the user's own actions as silent context** (`UserActions`): a line the user made by hand (a click, a studio
  command, a researcher started from the panel) is told to the voice as context, coalesced (one note for a burst),
  never spoken, with the ids of what it touched (`outline:o3`, `window:w-2`, `researcher:4`), which also go into the
  pointing history Hands resolves "that one" from. On by default; `MICROMIND_PARTNER_USER_EVENTS=0` turns it off.
"""
from __future__ import annotations

import asyncio
import contextlib
import os
import re
import time
from typing import Any, Awaitable, Callable

READ_MAX = 20  # lines one read returns
COALESCE_S = 1.2  # the user's actions within this of each other are one note
KINDS = ("request", "click", "command", "setting", "model", "research")
ASSISTANT = "Assistant:"  # the partner's own actions (hands.py) are not the user's
PARTNER_CHANNEL = "voice"  # a researcher the partner started; any other was started by the user (the panel, a Study)

SPEC = {"type": "object", "properties": {
    "since": {"type": "number", "description": "only lines from the last this many seconds"},
    "kinds": {"type": "array", "items": {"type": "string", "enum": list(KINDS)},
              "description": "request (asked by voice or chat, with its steps), click (the user's clicks and drags), "
                             "command (studio actions, the user's and yours), setting, model (loads), research"}}}
DESCRIPTION = ("What happened in this Project lately, newest first, from Hooke's Activity: requests and their steps, "
               "the user's clicks and studio actions, setting changes, model loads, researchers, and your own actions "
               "with their receipts. For \"what did I just do?\", \"what ran on this?\", or which thing the user just "
               "opened, moved or closed.")


def line_text(line: dict, now: float) -> dict:
    out = {"kind": line.get("kind"), "said": line.get("text"), "state": line.get("state"),
           "secondsAgo": round(max(0.0, now - float(line.get("at") or now)), 1)}
    if line.get("steps"):
        out["steps"] = [{k: s.get(k) for k in ("tool", "ok", "summary") if s.get(k) is not None}
                        for s in line["steps"][:6]]
    if line.get("ref"):
        out["ref"] = line["ref"]
    receipt = ((line.get("details") or {}).get("ids") or {}).get("receipt")
    if receipt:  # one of the partner's own actions: its receipt, as Hands decided it
        out["receipt"] = receipt
    return out


def read(hub, args: dict, *, project: str | None = None, clock: Callable[[], float] = time.time) -> dict:
    """The `activity` tool: recent Activity lines of the Project open (`project`), newest first."""
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
        if project is not None and found.project_of(line.get("id")) not in (None, project):
            continue  # another Project's line
        lines.append(line_text(line, now))
        if len(lines) >= READ_MAX:
            break
    return {"ok": True, "lines": lines, "note": "Facts recorded by Hooke; the user's own actions are theirs, lines "
                                                "starting \"Assistant:\" are yours."}


def by_user(line: dict) -> bool:
    """A click or a studio action the user made by hand (not the partner's own)."""
    return line.get("kind") in ("click", "command") and not str(line.get("text") or "").startswith(ASSISTANT)


def _as_target(value: str) -> str | None:
    """A thing a line touched, as a mark target, from its id's own form (o3 an outline, w-… a window, p-… a document,
    e3 a layer)."""
    if re.fullmatch(r"o\d+", value):
        return f"outline:{value}"
    if value.startswith("w-"):
        return f"window:{value}"
    if value.startswith("p-"):
        return f"document:{value}"
    if re.fullmatch(r"e\d+", value):
        return f"population:{value}"
    return None


def targets_of(line: dict, researcher: Any = None) -> list[str]:
    """The ids a line says it touched, as mark targets (what "that one" can mean)."""
    ids = ((line.get("details") or {}).get("ids") or {})
    out: list[str] = []
    if researcher is not None:
        out.append(f"researcher:{getattr(researcher, 'n', None) or getattr(researcher, 'rid', '')}")
    for key in ("objectId", "id", "what"):
        value = ids.get(key)
        if isinstance(value, str):
            target = _as_target(value)
            if target:
                out.append(target)
    ref = line.get("ref") or {}
    if isinstance(ref.get("objectId"), str):
        target = _as_target(ref["objectId"])
        if target:
            out.append(target)
    return list(dict.fromkeys(out))


class UserActions:
    """The user's own actions, coalesced, told to the voice as silent context (never spoken), with their ids; each one's
    ids also go to `point` (Hands' pointing history), newest first."""

    def __init__(self, tell: Callable[[str], Awaitable[None]], *, sink=None, point: Callable[[list[str]], None] | None = None,
                 researcher: Callable[[str], Any] | None = None, project: Callable[[], str | None] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.tell, self.clock = tell, clock
        self.sink, self.point, self.researcher, self.project = sink, point, researcher, project
        self.pending: list[str] = []
        self.seen: set[str] = set()
        self._flush: asyncio.Task | None = None

    @staticmethod
    def enabled() -> bool:
        return os.environ.get("MICROMIND_PARTNER_USER_EVENTS", "1") != "0"

    def _researcher(self, line: dict):
        """The researcher a research line is about, when the user started it by hand (not the partner)."""
        if line.get("kind") != "research" or self.sink is None or self.researcher is None:
            return None
        rid = self.sink.researcher_of(line.get("id"))
        session = self.researcher(rid) if rid else None
        origin = getattr(session, "origin", None) or {}
        if session is None or origin.get("channel") == PARTNER_CHANNEL:
            return None
        return session

    def line(self, line: dict) -> None:
        session = self._researcher(line)
        if session is not None:
            if line.get("state") != "running":  # its start is the user's act; its finish is the announcer's event
                return
        elif not by_user(line) or line.get("state") == "running":
            return
        if self.project is not None and self.sink is not None:
            with contextlib.suppress(Exception):
                open_now = self.project()
                if open_now is not None and self.sink.project_of(line.get("id")) not in (None, open_now):
                    return  # another Project's
        key = f'{line.get("id")}:{line.get("state")}'
        if key in self.seen:
            return
        self.seen.add(key)
        targets = targets_of(line, session)
        if targets and self.point is not None:
            with contextlib.suppress(Exception):
                self.point(targets)
        text = str(line.get("text") or "")[:200]
        if session is not None:
            text = f"{text} (they started it)"
        self.pending.append(text + (f" [{', '.join(targets)}]" if targets else ""))
        if self._flush is None or self._flush.done():
            with contextlib.suppress(RuntimeError):
                self._flush = asyncio.get_running_loop().create_task(self._later())

    async def _later(self) -> None:
        await asyncio.sleep(COALESCE_S)
        lines, self.pending = self.pending[-6:], []
        if lines:
            await self.tell("The user, by hand, just now (context only, not their words; say nothing about it unless "
                            "asked; the ids in brackets are what each touched): " + "; ".join(lines))
