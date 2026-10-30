"""The voice's hand-offs to Hooke's brain, as the chat shows them (`do_in_hooke`; the studio's `handoffs.ts`).

Will, 29 Oct: "if its a parallel process and you keep talking, we have to have some hyper clear way to communicate thats
whats happening." Each hand-off is one row in the Assistant's chat, under the voice line that started it, and an entry
in the "in progress" strip above the composer while it runs:
  - **working** from the call until the brain's turn returns; `long` once it has run past the voice's wait
    (TOOL_WAIT_S): the row says it is still working until its result lands;
  - **done** only from the brain's answer with no step that failed;
  - **started** when what it did was start a researcher: its part is done, the researcher's work is not (its card says);
  - **uncertain** when a change was sent but Hooke could not confirm it on screen (the step's own receipt says it was
    not observed: `unconfirmed`): it may not have happened, and is never said to have failed;
  - **failed** with its reason in one line (the brain's own answer when it gave one, else what did not work);
  - **settling**: the brain answered while one of its steps still runs (a late step); the row stays working and the
    studio settles it from that step's own receipt (its `chat-action`, by occurrence);
  - **unknown**: it stopped before Hooke said how it went, or the app restarted while it worked; one the user stopped
    (the voice's `stop_work`) carries `stopped` and says so.
A settling row's end comes from the steps that were still running, once they have (`settled`, from its job's
`Jobs.run_finished`), so its line in the journal ends too. Each hand-off is also a job (voice/jobs.py `HandoffJob`):
`changed` tells it every new state.
A hand-off may carry a step for after the work it starts (`then`: voice/followups.py), shown on its row with that step's
own state; the step, when it runs, is a hand-off of its own, `after` the work it waited on.
Truthful states (ADR 0006): the row never says more than the brain's turn did.

Each state is one `handoff` line filed with the conversation it was asked in (`Dialogue.show`, the same `lineId` each
time, so the journal keeps one line per hand-off and a replay shows its last state). A line the journal says was working
that this run no longer runs is replayed as `unknown` (`Dialogue.history` reads `Dialogue.flying`), never as working.
Without a dialogue (a bare test) the lines go to the studio over the voice socket instead.
"""
from __future__ import annotations

import contextlib
import itertools
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

LABEL_CHARS = 80
TEXT_CHARS = 1800
REASON_CHARS = 400
STEPS = 8
# What a step's row needs (its state, its words, its models and time, its identity for a late receipt, what it made).
STEP_KEYS = ("tool", "ok", "summary", "ms", "requestId", "actionId", "next", "progress", "models", "resource", "outputs",
             "at", "stopped")
_ids = itertools.count(1)
# A step's receipt that says its change was sent but not seen on screen (present.py: no report since it was sent, the
# studio in the background, its check timed out): not confirmed, which is not a failure.
UNSEEN_STATUS = frozenset({"unobserved", "timed_out", "pending"})
UNSEEN_CODES = frozenset({"STUDIO_UNREPORTED", "STUDIO_IN_BACKGROUND", "REPORT_STALE"})


def unconfirmed(action: dict) -> bool:
    """Whether a step's own receipt says its change could not be confirmed on screen."""
    outcome = action.get("outcome") if isinstance(action, dict) else None
    if not isinstance(outcome, dict):
        return False
    code = (outcome.get("failure") or {}).get("code") if isinstance(outcome.get("failure"), dict) else None
    return outcome.get("status") in UNSEEN_STATUS or code in UNSEEN_CODES


def corrected(actions) -> list[dict]:
    """The steps of a brain turn that still stand: a refusal of the brain's own call (`argument`: its arguments did not
    fit, ToolFailed.argument) that a later call of the same tool replaced is the brain correcting itself, left out
    (will-research, 29 Oct: an annotated picture refused for want of an outline, then the illustration started; the
    row said "did not work" and the voice told the user nothing was outlined). Any other failure stays."""
    rows = [a for a in actions or () if isinstance(a, dict)]
    return [a for i, a in enumerate(rows)
            if not (a.get("ok") is False and a.get("argument") is True
                    and any(b.get("tool") == a.get("tool") and b.get("ok") is not False for b in rows[i + 1:]))]


def started_researcher(result: dict) -> str | None:
    """The researcher a brain turn started, from its steps' outputs (a research-run reference)."""
    for action in result.get("actions") or []:
        for output in (action.get("outputs") or []) if isinstance(action, dict) else []:
            ref = output.get("resource") if isinstance(output, dict) else None
            if isinstance(ref, dict) and ref.get("kind") == "research-run" and isinstance(ref.get("researcherId"), str):
                return ref["researcherId"]
    return None


def title(text: str, limit: int = LABEL_CHARS) -> str:
    """One line of at most `limit` characters, cut at a word with an ellipsis."""
    words = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(words) <= limit:
        return words
    cut = words[: limit - 1].rsplit(" ", 1)[0] or words[: limit - 1]
    return cut.rstrip(" ,;:.") + "…"


@dataclass
class Handoff:
    id: str
    label: str
    request: str
    started: float
    asked: Any = None
    state: str = "working"  # working | done | started | uncertain | failed | unknown
    long: bool = False
    answer: str = ""
    reason: str = ""
    steps: list = field(default_factory=list)
    turn_id: int | None = None
    ended: float | None = None
    settling: bool = False
    then: dict | None = None  # its step for after the work it starts: {words, state, after?, rid?, reason?}
    after: str = ""  # a step done after other work: that work's name ("Researcher 2")
    stopped: bool = False  # the user stopped it (stop_work): its row says so

    def line(self) -> dict:
        out: dict[str, Any] = {"type": "handoff", "id": self.id, "lineId": f"handoff:{self.id}", "state": self.state,
                               "label": self.label, "request": self.request, "startedAt": round(self.started, 3)}
        if self.long:
            out["long"] = True
        if self.settling:
            out["settling"] = True
        if self.answer:
            out["answer"] = self.answer
        if self.reason:
            out["reason"] = self.reason
        if self.steps:
            out["steps"] = self.steps
        if self.turn_id is not None:
            out["turnId"] = self.turn_id
        if self.ended is not None:
            out["endedAt"] = round(self.ended, 3)
        if self.then:
            out["then"] = dict(self.then)
        if self.after:
            out["after"] = self.after
        if self.stopped:
            out["stopped"] = True
        return out


class Handoffs:
    """The hand-offs of one Talk session: each state filed as it changes. `dialogue` (Dialogue) files and publishes the
    line; `send` (async, the voice socket) is used only without one."""

    def __init__(self, dialogue=None, send: Callable[[dict], Awaitable[None]] | None = None, *,
                 spawn: Callable[[Awaitable], Any] | None = None, log: Callable[[dict], None] | None = None,
                 clock: Callable[[], float] = time.time, changed: Callable[[Handoff], Any] | None = None) -> None:
        self.dialogue, self.send, self.spawn, self.log, self.clock = dialogue, send, spawn, log, clock
        self.changed = changed  # told each new state (its job follows it: voice/jobs.py)
        self.open: dict[str, Handoff] = {}
        # Told after each state is filed: the voice's state on screen counts the hand-offs in flight (presence.py).
        self.counted: Callable[[], None] = lambda: None

    def _flying(self) -> set | None:
        flying = getattr(self.dialogue, "flying", None)
        return flying if isinstance(flying, set) else None

    def start(self, request: str, label: str | None = None, asked=None, *, then: str = "", after: str = "") -> Handoff:
        """A hand-off begins: its row shows at once, working, titled by the voice's short label (else the request);
        `then`: a step the user asked for after the work it starts; `after`: it is such a step, after that work."""
        hid = f"h{next(_ids)}-{uuid.uuid4().hex[:6]}"
        named = title(label or "") or title(request)
        handoff = Handoff(hid, named, title(request, TEXT_CHARS), self.clock(), asked=asked,
                          then={"words": title(then, REASON_CHARS), "state": "planned"} if then.strip() else None,
                          after=title(after))
        self.open[hid] = handoff
        flying = self._flying()
        if flying is not None:
            flying.add(hid)
        self._file(handoff)
        return handoff

    def still(self, handoff: Handoff) -> None:
        """It has run past the voice's wait: still working, said so, until its result lands."""
        if handoff.state != "working" or handoff.long or handoff.id not in self.open:
            return
        handoff.long = True
        self._file(handoff)

    def answered(self, handoff: Handoff, result: dict, out: dict) -> None:
        """The brain's turn returned (`result`, as Brain.turn gives it; `out`, what the voice is told): done, did not
        work, or settling while a late step still runs."""
        if handoff.id not in self.open:
            return
        steps = []
        for action in (result.get("actions") or [])[:STEPS]:
            if isinstance(action, dict):
                steps.append({k: action[k] for k in STEP_KEYS if k in action})
        handoff.steps = steps
        handoff.answer = str(result.get("answer") or "").strip()[:TEXT_CHARS]
        if isinstance(result.get("turnId"), int):
            handoff.turn_id = result["turnId"]
        ok = out.get("ok")
        actions = corrected(result.get("actions"))
        unseen = [a for a in actions if unconfirmed(a)]
        failed_for_real = [a for a in actions if a.get("ok") is False and not unconfirmed(a)]
        if (unseen or out.get("result") == "uncertain") and not failed_for_real and not out.get("error"):
            # Sent, not confirmed on screen: it may not have happened. Never "did not work", never "done".
            handoff.state = "uncertain"
        elif ok is True:
            handoff.state = "started" if started_researcher(result) else "done"
        elif ok is None and not out.get("error"):
            # The brain answered with a step still running (a late step): working until that step's own receipt.
            handoff.state, handoff.settling = "working", True
        else:
            handoff.state = "failed"
            failed = [str(s.get("summary") or "").strip() for s in actions if s.get("ok") is False]
            failed = [f for f in failed if f]
            error = str(out.get("error") or result.get("error") or "").strip()
            handoff.reason = title(handoff.answer or "; ".join(failed) or error or "Hooke did not say what it did",
                                   REASON_CHARS)
        self._end(handoff)

    def broke(self, handoff: Handoff, exc: BaseException) -> None:
        """Its work raised: did not work, said plainly (the error itself is in the log)."""
        if handoff.id not in self.open:
            return
        handoff.state, handoff.reason = "failed", "Something went wrong in Hooke while it worked"
        if self.log is not None:
            with contextlib.suppress(Exception):
                self.log({"kind": "partner-handoff-failed", "id": handoff.id,
                          "error": f"{type(exc).__name__}: {exc}"[:300]})
        self._end(handoff)

    def lost(self, handoff: Handoff, *, stopped: bool = False) -> None:
        """Its work was cancelled before the brain answered: what happened is not known. `stopped`: the user stopped it
        (stop_work), said so."""
        if handoff.id not in self.open:
            return
        handoff.state, handoff.stopped = "unknown", stopped
        handoff.reason = ("Stopped when you asked; anything it had finished stays" if stopped
                          else "It stopped before Hooke said how it went")
        self._end(handoff)

    def settled(self, handoff: Handoff, state: str, receipts: list, reason: str = "") -> None:
        """A row the brain answered while steps still ran (`settling`) ends from those steps' own receipts, once each has
        (its job's: done, failed or uncertain), never from a guess."""
        if handoff.state != "working" or not handoff.settling:
            return
        late = {r.get("actionId"): r for r in receipts if isinstance(r, dict) and r.get("actionId")}
        handoff.steps = [{k: late[s["actionId"]][k] for k in STEP_KEYS if k in late[s["actionId"]]}
                         if isinstance(s, dict) and s.get("actionId") in late else s for s in handoff.steps]
        handoff.settling = False
        if state == "done":
            handoff.state = "started" if started_researcher({"actions": handoff.steps}) else "done"
        elif state == "uncertain":
            handoff.state = "uncertain"
        elif state == "cancelled":
            handoff.state, handoff.stopped = "unknown", True
            handoff.reason = title(reason or "Stopped when you asked; anything it had finished stays", REASON_CHARS)
        else:
            handoff.state = "failed"
            handoff.reason = title(reason or "a step did not work", REASON_CHARS)
        handoff.ended = self.clock()
        self._file(handoff)

    def _end(self, handoff: Handoff) -> None:
        handoff.ended = self.clock()
        self.open.pop(handoff.id, None)
        flying = self._flying()
        if flying is not None:
            flying.discard(handoff.id)
        self._file(handoff)

    def refile(self, handoff: Handoff) -> None:
        """Its row again as it stands (its later step's state changed after it ended)."""
        self._file(handoff)

    def _file(self, handoff: Handoff) -> None:
        line = handoff.line()
        if self.log is not None:
            with contextlib.suppress(Exception):
                self.log({"kind": "partner-handoff", "id": handoff.id, "state": handoff.state, "long": handoff.long,
                          "settling": handoff.settling, "label": handoff.label, "t": self.clock()})
        if self.changed is not None:
            with contextlib.suppress(Exception):
                self.changed(handoff)
        if self.dialogue is not None:
            with contextlib.suppress(Exception):
                self.dialogue.show(line, asked=handoff.asked)
        elif self.send is not None and self.spawn is not None:
            self.spawn(self.send(line))
        with contextlib.suppress(Exception):
            self.counted()


def replayed(line: dict, flying: set) -> dict:
    """A hand-off line as a replay shows it: one the journal says was working that this run no longer runs (the app
    restarted while it worked) had no result recorded, and says so."""
    if (line.get("type") == "handoff" and line.get("state") == "working" and not line.get("settling")
            and line.get("id") not in flying):
        return {**line, "state": "unknown", "reason": "No result was recorded before Hooke restarted"}
    return line
