"""Jobs: the work the live partner hands behind the voice, beside its researchers, as one kind of thing (the part 2.5
spec, section 4.4; the lifecycle is Qwen-Audio-Agent's).

A think job (`Job`) is one `think.ThinkTask` with an id and a lifecycle: queued, running, cancelling, then done, failed
or cancelled. Queued is the state a job is in until its task starts; no cap is put on how many run at once.
- **Interrupting speech never cancels a job.** Only the voice's `cancel_job` does, and "cancelled" is said only from the
  job's own confirmation: its task has ended cancelled, told as a `job-cancelled` event (voice/events.py).
- **Follow-ups go to the job** (`update_job`): words for a running job go into its task's updates, given to the
  backend once at its next step (`ThinkTask.new_updates`). A job waiting on its question, or one that has ended, runs
  again with what it did so far (`ThinkTask.previous`) and the words, under the same id; each run is said apart (`ref`).
- **Questions and permission requests are the job's:** its report's question, and a permission Hands asked during it
  (with its approval id), are published as a `job-question` with the job's id; the voice relays only those, and the
  user's answer comes back through `update_job`, so the backend can call the tool again with the approval.
- **Done comes from receipts** (`outcome`): a job is done only when every acting call it made ended verified (or it
  made none and answered), whatever its own report says; the report's `checked` is advisory. What is not verified goes
  with its result (`unverified`, each step's own words), and is said as such.
- **Recorded in the Project:** each job files one Activity line (kind command, "Assistant: thinking it through: ...")
  replaced as its state changes, and project_state lists think jobs beside the researchers, numbered together
  (`project_state.jobs`), so "the second one" resolves the same way for the voice, think and a reader of the brief.

The jobs live on the hub (`jobs_of`), not on a Talk session: a new Talk session, the brief and `jobs` see the same list.

**A voice hand-off is a job too** (`HandoffJob`, kind "hooke"; the spec, 4.8 horizontal 1): each `do_in_hooke` is
Hooke's own work in progress, beside think jobs and researchers, under its hand-off's id (its chat row,
voice/handoffs.py). Running from the call; then done, failed or uncertain (a change sent but not confirmed on screen,
never "done") from the brain's own turn, or from the steps that turn left running (`Jobs.watch`: they end through
`Registry.finished`); cancelled only once its work has stopped (the voice's `stop_work`). It is listed and numbered
with the others, files one Activity line with its label (running, then its end), and while the brain works on it the
studio is sent the chat's `thinking` message with its id (partner_tools `_thinking`).
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, ClassVar

STATES = ("queued", "running", "cancelling", "done", "failed", "uncertain", "cancelled")
WORKING = frozenset({"queued", "running", "cancelling"})
ENDED = frozenset({"done", "failed", "uncertain", "cancelled"})
KEPT = 40  # jobs kept per app; the oldest that have ended go first
ENDED_KEPT = 128  # receipts of steps that ended while hand-offs ran, kept for a turn that answers after them
WORDS = {"queued": "queued", "running": "working", "cancelling": "being cancelled", "done": "done",
         "failed": "did not work", "uncertain": "sent, but not confirmed on screen", "cancelled": "cancelled"}
# A hand-off's row state (voice/handoffs.py) as its job's: started (it started a researcher) is its own part done; the
# researcher is a job of its own. Unknown (it stopped before the brain answered) is not done.
FROM_HANDOFF = {"working": "running", "done": "done", "started": "done", "uncertain": "uncertain", "failed": "failed",
                "unknown": "failed"}


@dataclass(eq=False)
class Job:
    kind: ClassVar[str] = "think"
    task: "object"  # think.ThinkTask
    project: str | None = None  # the Project it was started in (a session id)
    started: float = field(default_factory=time.time)
    state: str = "queued"
    runs: int = 0  # backend runs: an answer to its question, or words after it ended, are its next run
    runner: asyncio.Task | None = None
    doing: str = ""  # the tool it is running now
    asking: str = ""  # its question for the user, waited on (its answer comes with update_job)
    approval: dict | None = None  # a permission Hands asked during it: {tool, approval, ask}
    result: dict | None = None  # its last run's facts
    verified: bool | None = None  # its last run: every acting call verified
    ended: float | None = None
    added: list[str] = field(default_factory=list)  # the user's words given to it, for its Activity line

    @property
    def id(self) -> str:
        return self.task.id

    @property
    def request(self) -> str:
        return self.task.request

    def ref(self) -> str:
        """This run's reference on the bus: the job's id for its first run, then id.2, id.3...: each run's result is
        said once, and a later run's is not taken for the earlier one's."""
        return self.id if self.runs <= 1 else f"{self.id}.{self.runs}"

    def group(self) -> str:
        """running, finished or cancelled, as project_state groups researchers."""
        return "running" if self.state in WORKING else "cancelled" if self.state == "cancelled" else "finished"

    def row(self) -> dict:
        """The job as project_state.jobs lists it beside the researchers."""
        from ..untrusted import inline

        return {"kind": "think", "id": self.id, "name": "Think job", "state": self.state, "group": self.group(),
                "started": self.started, "about": inline(self.request, 160), "doing": self.doing,
                "asking": inline(self.asking, 160), "verified": self.verified}

    def so_far(self) -> str:
        """What it did and said, for its next run (`ThinkTask.previous`): its last words, its steps' receipts, its
        question and any permission it was given an approval for."""
        facts = self.result or {}
        parts = []
        if facts.get("say"):
            parts.append(f"you said: {facts['say']}")
        steps = [f"{s.get('name')}: {s.get('result') or ('ok' if s.get('ok') else 'not ok')}"
                 for s in facts.get("steps") or [] if isinstance(s, dict)]
        if steps:
            parts.append("your steps: " + "; ".join(steps))
        if self.asking:
            parts.append(f"you asked the user: {self.asking}")
        if self.approval:
            parts.append(f"Hooke asked the user's permission for {self.approval.get('tool')} ({self.approval.get('ask')}): "
                         f"approval {self.approval.get('approval')}; if their words agree, call it again with approved "
                         f"set to {self.approval.get('approval')}")
        return "; ".join(parts) or "it had not reported yet"


@dataclass(eq=False)
class HandoffJob:
    """A voice hand-off to Hooke's brain (`do_in_hooke`) as a job, kind "hooke": its id, label and words are its
    hand-off's (handoffs.Handoff), so its chat row, its job, its Activity line and its `thinking` message name it alike."""
    kind: ClassVar[str] = "hooke"
    handoff: "object"  # handoffs.Handoff
    project: str | None = None
    started: float = field(default_factory=time.time)
    state: str = "running"  # running from the call (never queued), cancelling, then done, failed, uncertain or cancelled
    runs: int = 1
    runner: asyncio.Task | None = None  # its brain turn: stop_work cancels it
    ticket: "object" = None  # its request (ticket.Ticket): the steps it tracks, its later effects called off on a stop
    stop: "object" = None  # its user_stop.Stop holder: a cancellation inside it is the user's stop, never a failure
    doing: str = ""  # the steps the brain's answer left running, by tool
    pending: set = field(default_factory=set)  # those steps' action ids: it ends once each has (Jobs.watch)
    late: list = field(default_factory=list)  # their receipts as each ended (ToolRun.wire)
    asking: str = ""
    verified: bool | None = None
    ended: float | None = None
    reason: str = ""  # why it did not work, or what a stop left
    told: bool = False  # its stop was said already (stop_work saw it end): not said again when it lands
    thinking: bool = False  # the chat's `thinking` message is on for it (partner_tools `_thinking`)
    settled: Callable | None = None  # (state, receipts, reason): its row takes the end its late steps gave it

    @property
    def id(self) -> str:
        return self.handoff.id

    @property
    def request(self) -> str:
        return self.handoff.request

    @property
    def label(self) -> str:
        return self.handoff.label

    def ref(self) -> str:
        return self.id

    def group(self) -> str:
        return "running" if self.state in WORKING else "cancelled" if self.state == "cancelled" else "finished"

    def row(self) -> dict:
        """The job as project_state.jobs lists it beside the researchers and think jobs."""
        from ..untrusted import inline

        return {"kind": "hooke", "id": self.id, "name": "Hooke's work", "label": inline(self.label, 80),
                "state": self.state, "group": self.group(), "started": self.started,
                "about": inline(self.label or self.request, 160), "doing": inline(self.doing, 160), "asking": "",
                "verified": self.verified}


class Jobs(dict):
    """Every job of this app by id: think jobs and the voice's hand-offs (one per hub, made once, so a new Talk session
    knows what is running). Each change of state files the job's Activity line again."""

    def __init__(self, hub=None) -> None:
        super().__init__()
        self.hub = hub
        self._watching = None  # the registry whose finished runs end hand-off jobs (watch)
        # Steps that ended while a hand-off was running, by action id (their receipts): one that ended before its
        # brain turn answered is not waited for afterwards.
        self._ended: OrderedDict[str, dict] = OrderedDict()

    def add(self, job: Job) -> Job:
        self[job.id] = job
        ended = [j for j in self.values() if j.state in ENDED]
        while len(self) > KEPT and ended:
            self.pop(ended.pop(0).id, None)
        self.file(job)
        return job

    def set(self, job: Job, state: str) -> None:
        if state not in STATES:
            raise ValueError(f"a job's state is one of {', '.join(STATES)}")
        job.state = state
        job.ended = time.time() if state in ENDED else None
        self.file(job)

    def of(self, project: str | None) -> list[Job]:
        """The jobs of a Project (and those started where no Project was known), oldest first."""
        return sorted((j for j in self.values() if j.project in (None, project)), key=lambda j: j.started)

    def file(self, job: Job) -> None:
        """Its one Activity line: running while it works or waits on the user, then done (verified) or not."""
        if self.hub is None:
            return
        if isinstance(job, HandoffJob):
            self._file_handoff(job)
            return
        from .. import activity
        from ..untrusted import inline

        words = WORDS[job.state]
        if job.asking and job.state in WORKING:
            words = "waiting for your answer"
        elif job.state == "done" and job.verified is False:
            words = "ended, not all of it checked"
        state = ("running" if job.state in WORKING else "done" if job.state == "done" and job.verified is not False
                 else "failed")
        lines = [f"Asked: {inline(job.request, 280)}", *(f"You added: {inline(w, 200)}" for w in job.added[-3:])]
        if job.asking:
            lines.append(f"Its question: {inline(job.asking, 200)}")
        with contextlib.suppress(Exception):
            activity.activity(self.hub, "command", f"Assistant: thinking it through: {inline(job.request, 120)}: {words}",
                              state=state, id=f"command:job-{job.id}",
                              details={"ids": {"job": job.id, "state": job.state}, "lines": lines})


    def _file_handoff(self, job: HandoffJob) -> None:
        """A hand-off's line, by its label: "Assistant: <label>: working", then done, did not work, not confirmed on
        screen, or stopped; an uncertain one is never done (as Hands files its own)."""
        from .. import activity
        from ..untrusted import inline

        words = {"cancelling": "being stopped", "cancelled": "stopped"}.get(job.state, WORDS[job.state])
        if job.state == "running" and job.doing:
            words = f"working, still running {job.doing}"
        state = "running" if job.state in WORKING else "done" if job.state == "done" else "failed"
        lines = [f"Asked: {inline(job.request, 280)}"]
        if job.reason:
            lines.append(inline(job.reason, 280))
        with contextlib.suppress(Exception):
            activity.activity(self.hub, "command", f"Assistant: {inline(job.label or job.request, 120)}: {words}",
                              state=state, id=f"command:job-{job.id}",
                              details={"ids": {"job": job.id, "kind": "hooke", "state": job.state}, "lines": lines})

    # --- hand-offs: their state from their row, their end from the steps they left running ---------------------------
    def handoff_changed(self, handoff) -> HandoffJob | None:
        """Its row changed (handoffs.Handoffs): the job follows it. A row the brain answered with steps still running
        (`settling`) keeps the job running until those steps end (`run_finished`); one cut off by a stop is cancelled."""
        job = self.get(getattr(handoff, "id", None))
        if not isinstance(job, HandoffJob) or job.state in ENDED:
            return job if isinstance(job, HandoffJob) else None
        if job.state == "cancelling":  # being stopped: its stop ends it, once all its work has (stop_work)
            return job
        if handoff.state == "working":
            running = [s for s in handoff.steps or [] if isinstance(s, dict) and s.get("ok") is None]
            if handoff.settling and running:
                job.pending = {s["actionId"] for s in running if s.get("actionId")}
                job.doing = ", ".join(dict.fromkeys(str(s.get("tool") or "a step") for s in running))
                if not job.pending:  # no identity to wait on: its steps' own rows settle it, the job cannot
                    self.set(job, "uncertain")
                    return job
                for action in list(job.pending):  # ended already, before the brain's answer came back
                    if action in self._ended:
                        job.pending.discard(action)
                        job.late.append(self._ended[action])
                if not job.pending:
                    self._end_late(job)
                    return job
                self.file(job)
            return job
        job.verified = handoff.state in ("done", "started")
        job.reason = getattr(handoff, "reason", "") or ""
        self.set(job, FROM_HANDOFF.get(handoff.state, "failed"))
        return job

    def watch(self, registry) -> None:
        """Hand-off jobs end when the steps their brain turn left running end (`Registry.finished`): one listener per
        app's registry."""
        finished = getattr(registry, "finished", None)
        if self._watching is registry or not isinstance(finished, list):
            return
        finished.append(self.run_finished)
        self._watching = registry

    def run_finished(self, run, ctx=None) -> None:
        """A step ended: a hand-off job waiting on it takes its receipt, and ends once the last of them has (done only
        when every step verified; a change sent but not confirmed is uncertain; a step that failed, failed)."""
        action = getattr(getattr(run, "occurrence", None), "action_id", None)
        running = [job for job in self.values() if isinstance(job, HandoffJob) and job.state in WORKING]
        if not action or not running:
            return
        try:
            receipt = run.wire()
        except Exception:
            receipt = {"tool": getattr(run, "tool", ""), "ok": getattr(run, "ok", None), "actionId": action}
        self._ended[action] = receipt
        while len(self._ended) > ENDED_KEPT:
            self._ended.popitem(last=False)
        for job in running:
            if action not in job.pending:
                continue
            job.pending.discard(action)
            job.late.append(receipt)
            if not job.pending:
                self._end_late(job)

    def _end_late(self, job: HandoffJob) -> None:
        """The last step a hand-off's answer left running has ended: the job (and its row) end from their receipts."""
        from .handoffs import unconfirmed

        job.doing = ""
        receipts = [r for r in job.late if isinstance(r, dict)]
        if any(r.get("ok") is False and not unconfirmed(r) for r in receipts):
            state = "failed"
            job.reason = next((str(r.get("summary")) for r in receipts if r.get("ok") is False and r.get("summary")),
                              "a step did not work")
        elif any(unconfirmed(r) for r in receipts):
            state = "uncertain"
        else:
            state = "done"
        job.verified = state == "done"
        self.set(job, state)
        if job.settled is not None:
            with contextlib.suppress(Exception):
                job.settled(state, receipts, job.reason)


def jobs_of(hub) -> Jobs:
    """The hub's think jobs, made on first use."""
    found = getattr(hub, "think_jobs", None)
    if not isinstance(found, Jobs):
        found = Jobs(hub)
        with contextlib.suppress(AttributeError):
            hub.think_jobs = found
    return found


def outcome(result) -> object:
    """A run's outcome from the receipts of its acting calls (think.ThinkResult.tools entries with a `result`), never
    from its own report alone: ok True when it says it is done and every acting call ended verified (or it made none);
    False when it was refused, or nothing it did happened and it is not done; otherwise None (part done, or not all
    checked), with what is not verified in `unverified`."""
    receipts = [t for t in result.tools if isinstance(t, dict) and t.get("result")]
    result.unverified = [{"tool": t.get("name"), "result": t["result"], **({"said": t["said"]} if t.get("said") else {})}
                         for t in receipts if t["result"] != "verified"]
    happened = {t["result"] for t in receipts} - {"failed", "ask", "permission"}
    if result.refused:
        result.ok = False
    elif result.question:
        result.ok = None
    elif result.done and not result.error and not result.unverified:
        result.ok = True
    elif happened:
        result.ok = None
    else:
        result.ok = False
    return result


def receipt_of(result) -> str:
    """The run as one receipt word for its wording (openai_realtime.RECEIPT_RULES): verified only when `outcome` found
    it done; running when what is not verified is all still under way (a researcher started); else failed when it was
    not done at all, uncertain when part of it was."""
    if result.ok is True:
        return "verified"
    if result.ok is False:
        return "failed"
    pending = [u for u in getattr(result, "unverified", []) if u.get("result") != "failed"]
    return "running" if pending and all(u.get("result") == "running" for u in pending) else "uncertain"
