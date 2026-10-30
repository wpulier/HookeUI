"""A step the user asked for after some work finishes, kept with that work and done by Hooke when it lands.

Will's session, 29 Oct: the voice said "Researcher 2 is working on a small educational graphic. When it's ready, we'll
place the picture into the Stoma Reference tab." Nothing scheduled the placement: when the researcher finished the voice
only said so, and he had to ask. The voice cannot act on a landing (what it says unasked is said with no tools:
RealtimeSession's proactive responses run with tool_choice "none"), so the promise is kept here, on the server:

  - the voice hands the request over whole with its later step as `then` (do_in_hooke); Hooke's brain does the first
    part only, and the step is kept with the researcher that part started (`Followups.keep`), or, when it started none,
    with the hand-off itself, which then runs it as soon as it is done;
  - when that researcher lands (the research manager's on_done, whether or not Talk is on), the step is handed to Hooke's
    brain as a hand-off of its own, in the user's words, with a row of its own in the chat ("After Researcher 2");
  - the researcher's result and the step's outcome are said together, once (one `researcher-done` event); a researcher
    that did not finish leaves the step undone, and that is said;
  - the hand-off that kept it shows the step on its row ("Then, after Researcher 2: put it in Stoma Reference") and the
    strip over the composer lists it while it waits.
A step with nothing to wait on is never promised: the voice is told so (`not_kept`). Kept in memory: a restart of the app
forgets a waiting step (the hand-off's row then shows it waiting, with no result recorded).
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import dataclass, field
from typing import Any

FOLLOW_WAIT_S = 45.0  # a researcher's result waits this long for its step's outcome, then is said with the step under way


@dataclass(eq=False)
class Followup:
    words: str  # the step, in the user's words
    request: str  # the whole request it was part of, as the user said it
    rid: str  # the researcher it waits on
    after: str  # that researcher's name ("Researcher 2")
    runner: Any  # the PartnerTools that kept it (its brain, studio and chat rows)
    parent: Any  # the hand-off that kept it (its row shows the step)
    asked: Any = None  # the conversation it was asked in
    state: str = "waiting"  # waiting | running | done | failed | dropped
    reason: str = ""
    kept: float = field(default_factory=time.time)


class Followups:
    """The steps waiting on researchers, for the whole app (on the hub): one listener on the research manager."""

    def __init__(self, research=None) -> None:
        self.waiting: dict[str, list[Followup]] = {}
        self.tasks: set[asyncio.Task] = set()
        self.research = research
        if research is not None and isinstance(getattr(research, "on_done", None), list):
            research.on_done.append(self.landed)

    def keep(self, followup: Followup) -> Followup:
        self.waiting.setdefault(followup.rid, []).append(followup)
        return followup

    def has(self, rid: str) -> bool:
        """A step waits on this researcher, or is being done for it now (its result is said with the step's outcome)."""
        return any(f.state in ("waiting", "running") for f in self.waiting.get(rid, []))

    def landed(self, session) -> None:
        """A researcher finished, failed or stopped: its steps are done now (or dropped when it did not finish)."""
        rid = str(getattr(session, "rid", "") or "")
        steps = [f for f in self.waiting.get(rid, []) if f.state == "waiting"]
        if not steps:
            return
        for followup in steps:
            followup.state = "running"  # at once: the partner's own listener sees it is this one's to say
        with contextlib.suppress(RuntimeError):  # no running loop (a bare construction): nothing runs
            task = asyncio.get_running_loop().create_task(self._run(session, steps))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _run(self, session, steps: list[Followup]) -> None:
        rid = str(getattr(session, "rid", "") or "")
        status = str(getattr(session, "status", "") or "")
        finished = status in ("done", "partial")
        outcomes = []
        for followup in steps:
            if not finished:
                followup.state = "dropped"
                followup.reason = f"{followup.after} did not finish ({status or 'stopped'}), so this was not done"
                followup.runner.then_changed(followup)
                outcomes.append({"asked": followup.words, "status": "not done", "why": followup.reason})
                continue
            followup.runner.then_changed(followup)
            try:
                out = await asyncio.wait_for(asyncio.shield(followup.runner.follow_up(followup, session)), FOLLOW_WAIT_S)
            except asyncio.TimeoutError:
                outcomes.append({"asked": followup.words, "status": "still working",
                                 "note": "its outcome is told to you when it lands"})
                continue
            except Exception as exc:  # said as not done, with its reason; the error is in the log
                followup.state, followup.reason = "failed", "something went wrong in Hooke while it worked"
                followup.runner.then_changed(followup)
                with contextlib.suppress(Exception):
                    followup.runner.partner.log({"kind": "partner-followup-failed", "rid": rid,
                                                 "error": f"{type(exc).__name__}: {exc}"[:300]})
                outcomes.append({"asked": followup.words, "status": "did not work", "why": followup.reason})
                continue
            outcomes.append({"asked": followup.words, "status": "done" if out.get("ok") else
                             ("still working" if out.get("ok") is None else "did not work"),
                             **({"answer": out["answer"]} if out.get("answer") else {}),
                             **({"failed": out["failed"]} if out.get("failed") else {}),
                             **({"_point": out["_point"]} if out.get("_point") else {})})
        self.waiting[rid] = [f for f in self.waiting.get(rid, []) if f.state in ("waiting",)]
        if not self.waiting[rid]:
            self.waiting.pop(rid, None)
        await steps[0].runner.say_landed(session, outcomes)


def followups_of(hub, research=None) -> Followups:
    """The one registry kept on the hub, made on first use with the research manager's listener."""
    found = getattr(hub, "partner_followups", None)
    if not isinstance(found, Followups):
        found = Followups(research)
        with contextlib.suppress(AttributeError):
            hub.partner_followups = found
    elif found.research is None and research is not None:
        found.research = research
        if isinstance(getattr(research, "on_done", None), list):
            research.on_done.append(found.landed)
    return found


def facts(outcomes: list[dict]) -> str:
    """The steps' outcomes, as the facts said with the researcher's result (what each points at is the app's, not said)."""
    told = [{k: v for k, v in o.items() if not str(k).startswith("_")} if isinstance(o, dict) else o for o in outcomes]
    return ("\nThe step the user asked for after it, done by Hooke now that it finished (say its outcome with the "
            "result, once):\n" + json.dumps(told, ensure_ascii=False))
