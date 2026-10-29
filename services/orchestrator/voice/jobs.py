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
"""
from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, field

STATES = ("queued", "running", "cancelling", "done", "failed", "cancelled")
WORKING = frozenset({"queued", "running", "cancelling"})
ENDED = frozenset({"done", "failed", "cancelled"})
KEPT = 40  # think jobs kept per app; the oldest that have ended go first
WORDS = {"queued": "queued", "running": "working", "cancelling": "being cancelled", "done": "done",
         "failed": "did not work", "cancelled": "cancelled"}


@dataclass(eq=False)
class Job:
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


class Jobs(dict):
    """Every think job of this app by id (one per hub, made once, so a new Talk session knows what is running). Each
    change of state files the job's Activity line again."""

    def __init__(self, hub=None) -> None:
        super().__init__()
        self.hub = hub

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
