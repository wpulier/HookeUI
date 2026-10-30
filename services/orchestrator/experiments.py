"""Experiments: a plan the user approves, run by WorkflowService for as long as it takes, audited by a researcher, its
results in a Tab of its own (Part 2.5, W5; Will, 29 Oct: "experiments: a planner, workflows, long-running work,
researchers auditing", "I want to run an experiment comparing Cellpose and micro-sam on this footage and have a researcher
audit it"; docs/architecture/scientific-workflows.md "Experiments").

- **The plan is the brain's, its facts code's** (`Experiments.draft`): the brain names the models (by their catalog
  ids, as `use` lists them), the input (this footage: N frames of the live view, one every S seconds; or the view now)
  and whether a researcher audits it, in the user's words. Code checks each fact before anything is proposed: every model
  is one that can run here (model_comparison.select: two to six, the unknown and the unrunnable said apart), there is a
  picture from the camera, the researchers' model has its key when an audit is asked, and the steps fit a workflow. The
  plan says its steps, what runs where and what it may cost (the models on this Mac cost nothing; the audit at most its
  effort's cap), and is shown as a card (`wire`, the studio's `experiment` message) the user can approve or change.
- **Nothing runs before a yes** (`approve`): the card's Run it (a press of the user's own, from the studio) or the user's
  own words quoted by the brain (`research.policy.quoted`, from what they just said). A plan changed since the card was
  drawn is not the one approved (its version must be the one shown). A plan waits PROPOSAL_S for its yes. What runs is
  exactly what the plan says: its models, its frames, its audit, nothing added.
- **The run** is a job of its own (`_job`, never awaited by a request): it holds the frames from the live view (each kept
  as a retained frame: experiment_steps.keep_frame), then runs one frozen workflow in WorkflowService with a frame per
  item: each model's step (experiment_steps: the app's own runner at research priority, so the live view and clicks
  come first), each model's measurement and the workflow's own comparison step (method_workflow: where the masks differ),
  every attempt kept. It outlives the request that started it and is never waited on by the voice.
- **The comparison** is built from those records only (`_comparison`): per frame, each model's cells, the cells both
  found (comparing.matched: boxes at least half alike) and where their masks differ (the comparison step's retained
  summary), with the measurement step's row count as a check of each count; kept as one table in Data
  (model_comparison's table, a Frame column first), so the measurements table shows it with the placement vocabulary.
- **The audit** is a second frozen workflow whose one step is a researcher (workflow_research, provider
  "local-researcher": this app's researchers): its input is the run's record (`_record`, a JSON file of the run: the
  frames, each model's runs, the comparison and the ids of what they made in Data), its brief the question and criteria
  the user approved. Its report is the step's output, not reviewed by anyone.
- **The results land in a Tab of its own** (`_land`): one document in Data (Write-ups), made by the app from the run's
  records (never a model's words except the audit's, kept as the researcher's): what was compared, frame by frame, the
  comparison table, the outlines side by side on the first frame, and the audit; opened with `present` do=place
  placement new-tab through a request of its own on the studio that approved it (or the one whose report counts), done
  once the report shows it. The voice is told one outcome (`job-result`, voice/events.py), once.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from .brain.registry import ToolFailed

log = logging.getLogger(__name__)

KIND = "hooke-experiment"
FRAMES_DEFAULT, FRAMES_MAX = 5, 12
EVERY_DEFAULT_S, EVERY_MIN_S, EVERY_MAX_S = 4.0, 1.0, 60.0
PROPOSAL_S = 1800.0  # a plan waits half an hour for its yes
POLL_S = 1.0  # how often a running experiment reads its run's records
INPUTS = ("footage", "view")  # frames of the live view over time, or the view now
OPERATION = "cells"
EFFORTS = ("quick", "standard")
LIVE = ("sampling", "running", "auditing", "landing")
ENDED = ("done", "failed", "stopped", "expired")
STATES = ("proposed", *LIVE, *ENDED)
QUESTION = ("Audit this experiment's run: do its records support each comparison it reports? Say what holds and what "
            "does not, and why.")
CRITERIA = ("Every point cites the run's records; each model's count in the comparison table matches that model's own "
            "cells; the matched cells and where the masks differ are read from the comparison; agreement between models "
            "is not accuracy; say what the run cannot settle (frames, focus, field, models' settings).")


class Refused(ToolFailed):
    """A plan or an approval refused, with what to do."""


@dataclass(eq=False)
class Experiment:
    key: str  # its Project-store identity
    n: int  # its number in the Project ("Experiment 2")
    session: str
    plan: dict
    thread: str | None = None
    version: int = 1
    status: str = "proposed"
    asked: str = ""  # the user's words that asked for it
    request_id: str | None = None  # the request that drafted it: its card sits under it
    turn_id: int | None = None
    created: float = 0.0  # when it was planned (the service's clock; a changed plan waits again from then)
    approved: dict | None = None  # {by, actor, at, words}
    frames: list = field(default_factory=list)  # each frame's identity, as kept
    run: dict | None = None  # the models' workflow run {workflow, revision, id}
    audit: dict | None = None  # the audit's workflow run {workflow, revision, id, researcher, status}
    progress: str = ""
    outcome: dict | None = None  # {words, document, table, tab, shown}
    why: str = ""
    ended: float | None = None
    # Not kept: the job, and the studio that approved it (its results are placed there when they land).
    task: Any = field(default=None, repr=False)
    client: Any = field(default=None, repr=False)
    asked_in: Any = field(default=None, repr=False)  # day/model.Asked of the conversation it was asked in

    @property
    def id(self) -> str:
        return f"x{self.n}"

    @property
    def name(self) -> str:
        return f"Experiment {self.n}"

    @property
    def title(self) -> str:
        return " vs ".join(m["label"] for m in self.plan["models"])

    def record(self) -> dict:
        return {k: copy.deepcopy(getattr(self, k)) for k in (
            "n", "session", "plan", "thread", "version", "status", "asked", "request_id", "turn_id", "created",
            "approved", "frames", "run", "audit", "progress", "outcome", "why", "ended")}

    def wire(self, now: float | None = None) -> dict:
        """The studio's card: what the plan is, its state in words, and what the user can do."""
        now = time.time() if now is None else now
        expires = self.created + PROPOSAL_S
        return {"id": self.id, "key": self.key, "n": self.n, "name": self.name, "title": self.title,
                "session": self.session, "thread": self.thread, "version": self.version, "status": self.status,
                "requestId": self.request_id, "turnId": self.turn_id, "steps": [s["says"] for s in self.plan["steps"]],
                "cost": self.plan["cost"]["says"], "input": self.plan["input"]["says"],
                "progress": self.progress, "why": self.why,
                "outcome": {k: v for k, v in (self.outcome or {}).items() if k in ("words", "document", "tab", "shown")},
                "canRun": self.status == "proposed" and now < expires, "canStop": self.status in LIVE,
                "created": self.created, "ended": self.ended}


def _now() -> float:
    return time.time()


def _token(value) -> str:
    from .workflows import token

    return token(value)


def cells(n) -> str:
    return f"{n} cell" if n == 1 else f"{n} cells"


def said_list(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1] if items else ""


# --- the plan -------------------------------------------------------------------------------------------------------
def plan_of(hub, *, models, on: str, frames, every, audit: bool, question: str | None, effort: str, measure: bool,
            research_ready: Callable[[], str | None]) -> dict:
    """The plan from the brain's choices, each checked against what this app has (never the request's words)."""
    from . import model_comparison
    from .brain.toolbox.choosing import PICK_AGAIN, pick
    from .workflows import MAX_NODES

    if on not in INPUTS:
        raise Refused("the experiment runs on this footage (frames of the live view) or on the view now",
                      next='say on="footage" or on="view"')
    chosen = model_comparison.select(hub, OPERATION, models, pick=pick)
    if chosen.unknown or len(chosen.impls) + len(chosen.cannot) < 2:
        raise Refused(f"an experiment compares two to {model_comparison.MAX_MODELS} different models"
                      + (f" (not known: {'; '.join(chosen.unknown)})" if chosen.unknown else "")
                      + f"; the choices (id = name): {model_comparison.choices_line(OPERATION)}", next=PICK_AGAIN)
    if chosen.cannot or chosen.left_out or len(chosen.impls) < 2:
        raise Refused("; ".join(chosen.cannot + ([f"left out: {', '.join(chosen.left_out)}"] if chosen.left_out else []))
                      or "an experiment needs two models that can run here",
                      next="choose models that can run here, or set the missing one up first")
    if on == "view":
        frames, every = 1, 0.0
    else:
        frames = FRAMES_DEFAULT if frames is None else frames
        every = EVERY_DEFAULT_S if every is None else every
        if type(frames) is not int or not 1 <= frames <= FRAMES_MAX:
            raise Refused(f"an experiment takes 1 to {FRAMES_MAX} frames", next=f"say how many frames, at most {FRAMES_MAX}")
        if type(every) not in (int, float) or not EVERY_MIN_S <= float(every) <= EVERY_MAX_S:
            raise Refused(f"frames are held {EVERY_MIN_S:.0f} to {EVERY_MAX_S:.0f} s apart",
                          next="say how far apart the frames are, in seconds")
        every = float(every)
    if effort not in EFFORTS:
        raise Refused("an audit is a quick or standard researcher", next='say effort "quick" or "standard"')
    if audit:
        why = research_ready()
        if why:
            raise Refused(f"no researcher can audit it here: {why}", next="plan it without the audit, or set the "
                                                                          "researchers' model up first")
    impls = chosen.impls
    measure = bool(measure) and 2 * len(impls) + 1 <= MAX_NODES
    span = (frames - 1) * every
    from research_worker.budget import EFFORTS as BUDGETS

    audit_cap = BUDGETS[effort].cap_usd if audit else 0.0
    local = [i for i in impls if i.worker != "cloud"]
    cloud = [i for i in impls if i.worker == "cloud"]
    held = (f"{frames} frames of this footage, {every:g} s apart (about {span:.0f} s)" if on == "footage"
            else "the view now (one frame)")
    steps = [{"kind": "frames", "says": f"Hold {held}"}]
    for impl in impls:
        where = "this Mac" if impl.worker != "cloud" else "cloud, cost not reported"
        steps.append({"kind": "model", "model": impl.id, "says": f"{impl.label} finds every cell on each frame ({where})"})
    if measure:
        steps.append({"kind": "measure", "says": "Measure each model's cells"})
    steps.append({"kind": "compare", "says": "Compare them frame by frame: cells both found, counts, where outlines differ"})
    if audit:
        steps.append({"kind": "audit", "effort": effort,
                      "says": f"A researcher audits the run's records: what holds and what doesn't ({effort})"})
    steps.append({"kind": "results", "says": "Results in a Tab of their own: the table, the outlines side by side"
                                             + (" and the audit" if audit else "")})
    says = (("Nothing spent: the models run on this Mac" if not cloud else
             f"{said_list([i.label for i in cloud])}' cost is not reported") +
            (f"; the audit at most ${audit_cap:.2f}" if audit else ""))
    return {"operation": OPERATION, "policy": "raw",
            "models": [{"id": i.id, "label": i.label, "runs": "cloud" if i.worker == "cloud" else "this Mac"} for i in impls],
            "input": {"kind": on, "frames": frames, "everyS": every, "says": held},
            "measure": measure,
            "audit": ({"question": " ".join(str(question or QUESTION).split())[:2000], "criteria": CRITERIA,
                       "effort": effort, "maximumUsd": audit_cap} if audit else None),
            "steps": steps, "cost": {"modelsUsd": 0.0 if not cloud else None, "auditUsd": audit_cap, "says": says,
                                     "local": len(local), "cloud": len(cloud)}}


def plan_words(x: Experiment) -> str:
    """The plan in plain words, for the brain to say (the card shows the same)."""
    steps = "; ".join(f"{i + 1}) {s['says']}" for i, s in enumerate(x.plan["steps"]))
    return (f"{x.name}, {x.title}, planned (version {x.version}; not run yet): {steps}. {x.plan['cost']['says']}. Its "
            "card shows the plan with Run it; it runs only on the user's yes")


# --- the service ----------------------------------------------------------------------------------------------------
class Experiments:
    """The Project's experiments: plans, their runs and their results, kept in the Project store and shown as cards."""

    def __init__(self, hub, *, day=None, app=None, sample=None, clock: Callable[[], float] | None = None) -> None:
        self.hub, self.day, self.app = hub, day, app
        self.sample = sample  # async (hub) -> (jpeg, facts) of the view now (a test's stand-in); None: the camera
        self.clock = clock if clock is not None else _now
        self.items: dict[tuple[str, str], Experiment] = {}  # (session, key)
        self.loaded: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self.fake_researcher: dict | None = None  # a scripted audit researcher (tests)
        self.landing: Callable | None = None  # async (x, entry) -> words: where the results were placed (tests)
        self.closing = False

    # --- the store ------------------------------------------------------------------------------------------------
    @property
    def store(self):
        return self.hub.resources.projects

    def _load(self, session: str) -> None:
        if session in self.loaded:
            return
        self.loaded.add(session)
        try:
            saved = [s for s in self.store.list(session) if s.document.get("kind") == KIND]
        except (ValueError, OSError):
            return
        for s in saved:
            state = s.document["state"].get("experiment") or {}
            try:
                x = Experiment(key=s.id, **{k: v for k, v in state.items() if k in Experiment.__dataclass_fields__})
            except TypeError:
                continue
            if x.status in LIVE:  # it was working when the app stopped: its records are kept, nothing is replayed
                x.status, x.why = "failed", "the app stopped while it ran; its frames and the runs so far are kept"
                x.progress = ""
            self.items[(session, s.id)] = x

    def of_session(self, session: str) -> list[Experiment]:
        self._load(session)
        return sorted((x for (sid, _), x in self.items.items() if sid == session), key=lambda x: x.n)

    def get(self, ref, session: str) -> Experiment:
        found = self.of_session(session)
        if not found:
            raise Refused("this Project has no experiments yet", next="plan one first")
        if ref in (None, ""):
            return found[-1]
        text = str(ref).strip().lower().removeprefix("experiment").strip()
        text = text.removeprefix("x")
        x = next((x for x in found if str(x.n) == text or x.key == str(ref)), None)
        if x is None:
            raise Refused(f"there is no {ref} in this Project; its experiments: "
                          + ", ".join(f"{x.id} = {x.name} ({x.title}, {x.status})" for x in found[-6:]),
                          next="name one of those")
        return x

    def _save(self, x: Experiment) -> None:
        doc = {"kind": KIND, "title": f"{x.name} · {x.title}"[:240], "state": {"experiment": x.record()},
               "resources": [], "runtimes": []}
        try:
            current = self.store.read(x.key, x.session)
        except (ValueError, OSError, KeyError):
            current = None
        try:
            if current is None:
                self.store.create(x.session, doc, identity=x.key)
            elif current.document != doc:
                self.store.save(x.key, x.session, doc, expected_revision=current.revision)
        except (ValueError, OSError) as exc:  # the experiment goes on; its card still says where it is
            log.warning("experiment %s not saved: %s", x.key, exc)

    def _changed(self, x: Experiment, *, save: bool = True) -> None:
        if save:
            self._save(x)
        with contextlib.suppress(Exception):
            self.hub.broadcast({"type": "experiment", "experiment": x.wire(self.clock())})

    def wire_list(self, session: str) -> dict:
        return {"type": "experiments", "session": session,
                "experiments": [x.wire(self.clock()) for x in self.of_session(session)]}

    # --- planning -------------------------------------------------------------------------------------------------
    def research_ready(self) -> str | None:
        from . import experiment_steps

        manager = experiment_steps.manager_of(self.hub)
        if manager is None:
            return "this app has no researchers"
        if self.fake_researcher is not None:
            return None
        from .research.manager import engine_key_refusal

        missing = engine_key_refusal(getattr(getattr(self.hub, "settings", None), "research", None)) \
            if getattr(getattr(manager, "_worker", None), "runs_a_model", True) else None
        return str(missing) if missing is not None else None

    def _has_picture(self) -> None:
        if self.sample is not None:
            return
        if getattr(self.hub, "latest_info", None) is None:
            from .frames import no_picture

            why, next_step = no_picture(self.hub, "there is no picture from the camera to take frames from",
                                        "check the camera or the footage is playing, then ask again")
            raise Refused(why, next=next_step)

    def draft(self, *, session: str, thread: str | None, models, on: str = "footage", frames=None, every=None,
              audit: bool = True, question: str | None = None, effort: str = "quick", measure: bool = True,
              asked: str = "", request_id: str | None = None, turn_id: int | None = None, asked_in=None) -> Experiment:
        self._has_picture()
        plan = plan_of(self.hub, models=models, on=on, frames=frames, every=every, audit=audit, question=question,
                       effort=effort, measure=measure, research_ready=self.research_ready)
        n = max((x.n for x in self.of_session(session)), default=0) + 1
        key = "p" + _token({"experiment": session, "n": n, "at": self.clock()})[:32]
        x = Experiment(key=key, n=n, session=session, plan=plan, thread=thread, asked=" ".join(asked.split())[:600],
                       request_id=request_id, turn_id=turn_id, created=self.clock())
        x.asked_in = asked_in
        self.items[(session, key)] = x
        self._changed(x)
        return x

    def change(self, x: Experiment, **choices) -> Experiment:
        if x.status != "proposed":
            raise Refused(f"{x.name} is {x.status}; only a plan not run yet can change",
                          next="plan a new experiment with the change")
        current = {"models": [m["id"] for m in x.plan["models"]], "on": x.plan["input"]["kind"],
                   "frames": x.plan["input"]["frames"], "every": x.plan["input"]["everyS"] or None,
                   "audit": x.plan["audit"] is not None,
                   "question": (x.plan["audit"] or {}).get("question"),
                   "effort": (x.plan["audit"] or {}).get("effort", "quick"), "measure": x.plan["measure"]}
        merged = {**current, **{k: v for k, v in choices.items() if v is not None}}
        if merged["on"] == "view":
            merged["frames"], merged["every"] = None, None
        self._has_picture()
        x.plan = plan_of(self.hub, models=merged["models"], on=merged["on"], frames=merged["frames"],
                         every=merged["every"], audit=bool(merged["audit"]), question=merged["question"],
                         effort=merged["effort"], measure=bool(merged["measure"]), research_ready=self.research_ready)
        x.version += 1
        x.created = self.clock()  # a changed plan waits again for its yes
        self._changed(x)
        return x

    # --- the yes --------------------------------------------------------------------------------------------------
    def approve(self, x: Experiment, *, version: int | None, by: str, actor: str, words: str = "", client=None) -> Experiment:
        """Starts exactly the plan the user said yes to. `by`: "card" (the user's press) or "words" (their own yes,
        already checked by the caller); `version`: the plan shown, when the card says it."""
        if x.status in LIVE or x.status == "done":
            raise Refused(f"{x.name} is already {x.status}; nothing new was started", next=None)  # no next: said
        if x.status != "proposed":
            raise Refused(f"{x.name} is {x.status}; it cannot run now", next="plan it again")
        if self.clock() > x.created + PROPOSAL_S:
            x.status = "expired"
            self._changed(x)
            raise Refused(f"{x.name}'s plan waited over {PROPOSAL_S / 60:.0f} minutes for a yes, so it was not run",
                          next="plan it again")
        if version is not None and version != x.version:
            raise Refused(f"{x.name}'s plan changed since it was shown (now version {x.version}); nothing was run",
                          next="look at the plan as it is now, then say yes to it")
        if self.day is not None and getattr(self.day, "sid", x.session) != x.session:
            raise Refused(f"{x.name} belongs to another Project", next="open its Project first")
        x.approved = {"by": by, "actor": actor, "at": self.clock(), "words": " ".join(words.split())[:300]}
        x.status, x.progress = "sampling", "Holding the frames"
        x.client = client
        self._changed(x)
        try:
            task = asyncio.get_running_loop().create_task(self._job(x))
        except RuntimeError:  # no loop (a bare construction): nothing runs, and that is said
            x.status, x.why = "failed", "it could not be started here"
            self._changed(x)
            raise Refused(x.why, next=None) from None  # no next: a test's construction only
        x.task = task
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return x

    async def stop(self, x: Experiment) -> str:
        if x.status not in LIVE:
            return f"{x.name} is {x.status}; nothing to stop"
        x.why = "you stopped it"
        from .workflow_service import service

        runner = service(self.hub, self.day)
        for part in (x.run, x.audit):
            if part and part.get("id"):
                with contextlib.suppress(ValueError, OSError):
                    await runner.command({"action": "stop", "run": part["id"], "session": x.session}, actor="user")
        if x.audit and x.audit.get("researcher"):
            from . import experiment_steps

            manager = experiment_steps.manager_of(self.hub)
            with contextlib.suppress(Exception):
                await manager.cancel(x.audit["researcher"], confirmed=True)
        if x.task is not None and not x.task.done() and x.status == "sampling":
            x.task.cancel()
        return f"stopping {x.name}: what it made so far stays in this Project"

    async def close(self) -> None:
        self.closing = True
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    # --- the job --------------------------------------------------------------------------------------------------
    def _progress(self, x: Experiment, words: str) -> None:
        if words != x.progress:
            x.progress = words
            self._changed(x, save=False)

    async def _job(self, x: Experiment) -> None:
        try:
            await self._hold_frames(x)
            x.status = "running"
            self._changed(x)
            status = await self._run_models(x)
            comparison = await asyncio.to_thread(self._comparison, x, status)
            await self._keep_table(x, comparison)
            audit = None
            if x.plan["audit"] is not None:
                x.status = "auditing"
                self._progress(x, "A researcher is auditing the run")
                self._changed(x)
                audit = await self._audit(x, comparison)
            x.status = "landing"
            self._progress(x, "Putting the results in a Tab")
            words = await self._land(x, comparison, audit)
            x.status = "done"
            x.outcome = {**(x.outcome or {}), "words": words}
        except asyncio.CancelledError:
            if self.closing:  # the app is quitting: said so when it comes back (its records are kept), never "stopped"
                x.status, x.why = "failed", "the app stopped while it ran; its frames and the runs so far are kept"
                x.ended, x.progress = self.clock(), ""
                self._save(x)
                raise
            x.status = "stopped"
            x.why = x.why or "it was stopped"
        except Exception as exc:  # noqa: BLE001 - every failure is said with its reason, never a raw error
            if x.why == "you stopped it" or x.status == "stopped":
                x.status = "stopped"
            else:
                x.status = "failed"
                x.why = str(exc)[:400] or "something went wrong while it ran"
                log.warning("experiment %s failed: %s", x.key, exc, exc_info=True)
        finally:
            if not self.closing:
                x.ended = self.clock()
                x.progress = ""
                self._changed(x)
                self._tell(x)

    async def _view(self) -> tuple[bytes, dict]:
        """The view now as the frame to keep: the best view of the last second (Captures.best), its JPEG as the camera
        sent it and its facts as the view recorded them."""
        if self.sample is not None:
            return await self.sample(self.hub)
        from .capture import CaptureGone, captures
        from .population import view_facts

        try:
            capture = captures(self.hub).best(1.0)
        except CaptureGone as exc:
            raise Refused(str(exc), next=getattr(exc, "next", None)) from None  # next: passed on
        return bytes(capture.jpeg or b""), {**view_facts(capture), "frame": capture.id,
                                            "scale": copy.deepcopy(getattr(capture, "scale", None))}

    async def _hold_frames(self, x: Experiment) -> None:
        from . import experiment_steps

        n, every = x.plan["input"]["frames"], float(x.plan["input"]["everyS"] or 0.0)
        for i in range(len(x.frames), n):
            if i:
                await asyncio.sleep(every)
            if x.why == "you stopped it":
                raise asyncio.CancelledError
            self._progress(x, f"Holding frame {i + 1} of {n}")
            jpeg, facts = await self._view()
            facts = {**facts, "experiment": {"name": x.name, "thread": x.thread, "key": x.key, "frame": i + 1}}
            saved = await asyncio.to_thread(experiment_steps.keep_frame, self.store, x.session, jpeg=jpeg, facts=facts,
                                            title=f"{x.name} · frame {i + 1}", request=f"{x.key}:{i}")
            from .scientific_comparisons import identity

            x.frames.append(identity(saved))
        self._save(x)

    def graph(self, x: Experiment) -> dict:
        from . import experiment_steps
        from .operations import OPERATIONS

        nodes = []
        op = OPERATIONS[OPERATION]
        for i, m in enumerate(x.plan["models"]):
            impl = op.impl(m["id"])
            nodes.append({"id": f"m{i}", "name": f"{m['label']} cells"[:240], "kind": "tool", "needs": ["input"],
                          "settings": experiment_steps.settings_for(OPERATION, impl, x.plan["policy"])})
        if x.plan["measure"]:
            for i, m in enumerate(x.plan["models"]):
                nodes.append({"id": f"measure{i}", "name": f"Measure {m['label']} cells"[:240], "kind": "measure",
                              "needs": [f"m{i}"], "settings": {}})
        nodes.append({"id": "compare", "name": "Compare the models", "kind": "compare",
                      "needs": [f"m{i}" for i in range(len(x.plan["models"]))], "settings": {}})
        return {"format": 1, "input": {"kind": "scientific-scene", "semantic": "intensity", "axes": ["y", "x"],
                                       "dtype": "|u1"}, "nodes": nodes}

    def audit_graph(self, x: Experiment) -> dict:
        from . import experiment_steps

        a = x.plan["audit"]
        return {"format": 1, "input": {"kind": "source-file"}, "nodes": [{
            "id": "audit", "name": "Audit the run", "kind": "researcher", "needs": ["input"],
            "settings": {"question": a["question"], "criteria": a["criteria"], "provider": experiment_steps.LOCAL,
                         "maximumUsd": a["maximumUsd"], "seconds": 1800, "tools": [], "output": "report",
                         "effort": a["effort"]}}]}

    def _actor(self, x: Experiment) -> str:
        actor = (x.approved or {}).get("actor") or "agent:system"
        from .workflows import allowed_actor

        return actor if allowed_actor(actor) else "agent:system"

    async def _start(self, x: Experiment, name: str, graph: dict, inputs: list, part: str) -> dict:
        from .workflow_service import service

        runner = service(self.hub, self.day)
        if self.fake_researcher is not None:
            runner.steps.fake_researcher = self.fake_researcher
        actor = self._actor(x)
        created = await runner.command({"action": "create", "name": name[:240], "graph": graph,
                                        "request": _token({"experiment": x.key, "part": part, "v": x.version})[:32]},
                                       actor=actor)
        workflow = created["workflow"]
        started = await runner.command({"action": "start", "workflow": workflow["id"], "revision": workflow["revision"],
                                        "session": x.session, "inputs": inputs,
                                        "request": _token({"experiment": x.key, "run": part, "v": x.version})[:32]},
                                       actor=actor)
        return {"workflow": workflow["id"], "revision": workflow["revision"], "id": started["run"]["id"]}

    async def _wait(self, x: Experiment, run: dict, words: Callable[[dict], str]) -> dict:
        from .workflow_service import service

        runner = service(self.hub, self.day)
        while True:
            status = await asyncio.to_thread(runner.observed_status, run["id"], x.session)
            if status["status"] in ("completed", "failed", "rejected", "stopped", "uncertain"):
                return status
            if status["status"] == "awaiting-review":
                return status
            if status["executionObservation"]["state"] in ("interrupted", "unstarted") and run["id"] not in (
                    k[1] for k in runner.tasks):
                return status
            self._progress(x, words(status))
            await asyncio.sleep(POLL_S)

    def _model_progress(self, x: Experiment, status: dict) -> str:
        n, models = len(x.frames), x.plan["models"]
        done = sum(1 for a in status["attempts"] if a["node"].startswith("m") and a["node"][1:].isdigit()
                   and a["status"] == "completed")
        running = next((a for a in status["attempts"] if a["status"] == "running"), None)
        total = n * len(models)
        if running is None:
            return f"{done} of {total} model runs done"
        node = next((nd for nd in status["definition"]["graph"]["nodes"] if nd["id"] == running["node"]), None)
        what = (node or {}).get("name", "a step")
        note = running.get("note") or ""
        return f"Frame {running['item'] + 1} of {n}: {what}" + (f" ({note.lower()})" if note.startswith("Waiting") else "") \
            + f"; {done} of {total} model runs done"

    async def _run_models(self, x: Experiment) -> dict:
        if x.run is None:
            x.run = await self._start(x, f"{x.name} · {x.title}", self.graph(x), list(x.frames), "models")
            self._changed(x)
        status = await self._wait(x, x.run, lambda s: self._model_progress(x, s))
        if status["status"] != "completed":
            failed = [a for a in status["attempts"] if a["status"] in ("failed", "rejected", "uncertain", "stopped")]
            if x.why == "you stopped it" or status["status"] == "stopped":
                raise asyncio.CancelledError
            names = {nd["id"]: nd["name"] for nd in status["definition"]["graph"]["nodes"]}
            first = failed[0] if failed else None
            reason = (f"{names.get(first['node'], 'a step')} on frame {first['item'] + 1}: {first.get('note') or first['status']}"
                      if first else f"its run is {status['status']}")
            raise RuntimeError(f"it did not finish: {reason}")
        return status

    # --- the comparison -------------------------------------------------------------------------------------------
    def _comparison(self, x: Experiment, status: dict) -> dict:
        """Per frame, from the run's own attempts: each model's run (experiment_steps' record of it), the cells both
        found, the comparison step's areas and where the masks differ, and the measurement step's rows."""
        from . import comparing, experiment_steps, model_comparison
        from . import scientific_resource_actions as actions
        from .scientific_comparisons import exact

        latest = {(a["item"], a["node"]): a for a in status["attempts"]}
        models = x.plan["models"]
        frames = []
        for item, frame in enumerate(x.frames):
            arms, reads = [], []
            for i, m in enumerate(models):
                a = latest.get((item, f"m{i}"))
                if a is None or a["status"] != "completed" or not a.get("output"):
                    arms.append(model_comparison.failed_arm(SimpleImpl(m), (a or {}).get("note") or "it did not run"))
                    continue
                saved = exact(self.store, a["output"], x.session)
                run = dict(saved.document["state"].get(experiment_steps.RAN) or {})
                measured = latest.get((item, f"measure{i}"))
                rows = None
                if measured is not None and measured.get("output"):
                    try:
                        table = actions.table(self.store, measured["output"]["id"], x.session, measured["output"]["revision"])
                        rows = len(table["rows"])
                    except (ValueError, OSError, KeyError):
                        rows = None
                arms.append({**{k: run.get(k) for k in ("impl", "label", "executed", "execution", "at", "ms", "reused",
                                                        "runs", "costUsd", "costBasis", "population",
                                                        "populationTitle", "count")},
                             "boxes": run.get("boxes") or [], "measured": rows, "output": a["output"]})
            pairs = []
            ran = [a for a in arms if not a.get("failed")]
            for p in range(len(ran)):
                for q in range(p + 1, len(ran)):
                    pairs.append({"a": ran[p]["impl"], "b": ran[q]["impl"],
                                  "matched": comparing.matched(ran[p]["boxes"], ran[q]["boxes"])})
            compared = latest.get((item, "compare"))
            method = None
            if compared is not None and compared.get("output"):
                saved = exact(self.store, compared["output"], x.session)
                method = saved.document["state"].get("methodSummary")
                reads.append({"id": compared["output"]["id"], "what": "the comparison step's record"})
            record = model_comparison.build(OPERATION, [{k: v for k, v in a.items() if k != "boxes"} for a in arms],
                                            policy=x.plan["policy"], summary={"pairs": pairs},
                                            on={"frame": item + 1, "scene": frame["id"]})
            frames.append({"frame": item + 1, "scene": frame, "record": record, "method": method,
                           "boxes": {a["impl"]: a["boxes"] for a in ran}})
        return {"experiment": x.name, "title": x.title, "frames": frames, "models": models}

    def _table(self, x: Experiment, comparison: dict) -> dict:
        """One table: a row per frame and model (the comparison's own columns, a Frame column first, the measured rows
        and the masks' area beside), then a row per model over all frames."""
        from . import model_comparison
        from .tables import kept_table_digest

        frames = comparison["frames"]
        base = frames[0]["record"]["columns"]
        columns = ([{"name": "Frame"}] + base[:3]
                   + [{"name": "Measured", "definition": "rows of the measurement step's table for its cells; the same "
                                                         "as Cells when every cell was measured"},
                      {"name": "Mask area", "unit": "px²", "definition": "its cells' pixels on the frame (the comparison "
                                                                         "step's count)"}]
                   + base[3:])
        rows = []
        totals: dict[str, dict] = {}
        for f in frames:
            areas = {}
            for i, arm in enumerate((f["method"] or {}).get("arms") or []):
                if i < len(x.plan["models"]):
                    areas[x.plan["models"][i]["id"]] = arm.get("area")
            for arm, row in zip(f["record"]["arms"], f["record"]["rows"]):
                measured = arm.get("measured")
                rows.append([f["frame"], *row[:3], measured, areas.get(arm["impl"]), *row[3:]])
                t = totals.setdefault(arm["impl"], {"label": arm["label"], "cells": 0, "frames": 0, "ms": 0.0,
                                                    "failed": 0})
                if arm.get("failed"):
                    t["failed"] += 1
                else:
                    t["cells"] += int(arm.get("count") or 0)
                    t["frames"] += 1
                    t["ms"] += float(arm.get("ms") or 0.0)
        for impl, t in totals.items():
            mean = round(t["cells"] / t["frames"], 1) if t["frames"] else None
            row = ["all", t["label"], None, mean] + [None] * (len(columns) - 4)
            rows.append(row)
        # A frame where a model failed has fewer "found by" columns: every row is the table's width.
        rows = [(row + [None] * len(columns))[:len(columns)] for row in rows]
        body = {"columns": columns, "rows": rows}
        return {"comparison": {"format": model_comparison.FORMAT, "operation": OPERATION, "experiment": x.name,
                               "frames": len(frames), "says": model_comparison.NOT_ACCURACY,
                               "totals": {impl: {k: v for k, v in t.items() if k != "label"} for impl, t in totals.items()}},
                **body, "producer": {"implementation": "experiment", "version": model_comparison.FORMAT,
                                     "sha256": kept_table_digest(body)}}

    async def _keep_table(self, x: Experiment, comparison: dict) -> None:
        results = getattr(getattr(self.hub, "resources", None), "results", None)
        if results is None:
            return
        values = self._table(x, comparison)
        summary = self._overall_words(x, comparison)
        entry = await asyncio.to_thread(results.add, "experiment_table", {"experiment": x.id}, summary, values=values,
                                        thread=x.thread, request=x.asked, made_for=x.name, session=x.session,
                                        actor="assistant")
        x.outcome = {**(x.outcome or {}), "table": entry["id"]}
        self._save(x)

    def _overall_words(self, x: Experiment, comparison: dict) -> str:
        """The comparison in plain facts: each model's cells per frame, then the cells both found."""
        parts = []
        labels = {m["id"]: m["label"] for m in x.plan["models"]}
        for impl, label in labels.items():
            counts = []
            for f in comparison["frames"]:
                arm = next((a for a in f["record"]["arms"] if a["impl"] == impl), None)
                counts.append("failed" if arm is None or arm.get("failed") else str(arm.get("count")))
            parts.append(f"{label}: {', '.join(counts)} cells")
        pairs = []
        for f in comparison["frames"]:
            for p in f["record"]["summary"].get("pairs") or []:
                pairs.append((p["a"], p["b"], p["matched"]))
        joined = {}
        for a, b, m in pairs:
            joined.setdefault((a, b), []).append(m)
        found = [f"found by both {labels[a]} and {labels[b]}: {', '.join(str(v) for v in ms)}"
                 for (a, b), ms in joined.items()]
        n = len(comparison["frames"])
        return (f"{x.name} on {n} frame{'s' if n != 1 else ''} (each frame in order): " + "; ".join(parts)
                + ("; " + "; ".join(found) if found else "") + ". Agreement between models is not accuracy")

    # --- the audit ------------------------------------------------------------------------------------------------
    def _record(self, x: Experiment, comparison: dict) -> dict:
        """The run's record the audit reads: the frames, each model's runs and what they made in Data, the comparison."""
        read = []
        if (x.outcome or {}).get("table"):
            read.append({"id": x.outcome["table"], "what": "the comparison table (a row per frame and model)"})
        for f in comparison["frames"]:
            for a in f["record"]["arms"]:
                if a.get("population"):
                    read.append({"id": a["population"],
                                 "what": f"{a['label']}'s cells on frame {f['frame']} ({cells(a.get('count'))})"})
        return {"format": "hooke-experiment-record-v1", "name": x.name, "title": x.title, "thread": x.thread,
                "plan": {k: x.plan[k] for k in ("operation", "policy", "models", "input", "measure")},
                "frames": [{"frame": f["frame"], "scene": f["scene"],
                            "arms": [{k: a.get(k) for k in ("impl", "label", "executed", "execution", "ms", "reused",
                                                            "population", "count", "measured", "failed")}
                                     for a in f["record"]["arms"]],
                            "pairs": f["record"]["summary"].get("pairs"),
                            "method": {k: (f["method"] or {}).get(k) for k in ("arms", "disagreement", "says")}}
                           for f in comparison["frames"]],
                "read": read, "run": x.run}

    async def _audit(self, x: Experiment, comparison: dict) -> dict:
        from . import experiment_steps
        from .workflow_execution import keep_file

        record = self._record(x, comparison)
        body = json.dumps(record, ensure_ascii=False, default=str).encode()
        kept = await asyncio.to_thread(keep_file, self.store, x.session, _token({"experimentRecord": x.key}),
                                       "experiment-record.json", body)
        from .scientific_comparisons import identity

        x.audit = await self._start(x, f"{x.name} · audit", self.audit_graph(x), [identity(kept)], "audit")
        self._changed(x)

        def words(status) -> str:
            a = next((a for a in status["attempts"] if a["node"] == "audit"), None)
            if a is not None and isinstance(a.get("research"), dict):
                x.audit["researcher"] = a["research"].get("id")
                return f"{a['research'].get('name') or 'A researcher'} is auditing the run"
            return "Starting the researcher who audits the run"

        status = await self._wait(x, x.audit, words)
        attempt = next((a for a in status["attempts"] if a["node"] == "audit"), None)
        out = {"status": status["status"], "note": (attempt or {}).get("note") or "",
               "researcher": (attempt or {}).get("research", {}).get("id") if attempt else None,
               "name": ((attempt or {}).get("research") or {}).get("name")}
        if status["status"] == "completed" and attempt and attempt.get("output"):
            from .scientific_comparisons import exact

            saved = exact(self.store, attempt["output"], x.session)
            findings = saved.document["state"].get("researchFindings") or {}
            out.update(result=findings.get("result"), output=attempt["output"])
        elif x.why == "you stopped it" or status["status"] == "stopped":
            raise asyncio.CancelledError
        x.audit["status"] = out["status"]
        self._save(x)
        _ = experiment_steps  # the step's own module keeps the researcher's outcome rules
        return out

    # --- the results ----------------------------------------------------------------------------------------------
    def _picture(self, x: Experiment, comparison: dict) -> bytes | None:
        """The outlines side by side on the first frame: each model's cells drawn on the frame's own pixels."""
        from . import comparing
        from .evidence import evidence_of
        from . import experiment_steps
        from .scientific_comparisons import exact
        import cv2
        import numpy as np

        first = comparison["frames"][0]
        try:
            scene = exact(self.store, first["scene"], x.session)
            jpeg = experiment_steps.frame_jpeg(self.store, scene)
        except (ValueError, OSError):
            return None
        image = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return None
        items = getattr(evidence_of(self.hub), "items", {}) or {}
        arms = []
        for a in first["record"]["arms"]:
            if a.get("failed"):
                continue
            population = items.get(a.get("population"))
            polygons = [p for m in population.live() for p in m.polygons] if population is not None else []
            arms.append({"polygons": polygons, "caption": f"{a['label']} · {cells(a.get('count'))}"})
        if len(arms) < 2:
            return None
        facts = scene.document["state"].get(experiment_steps.FRAME_KIND) or {}
        disc = facts.get("disc")
        h, w = image.shape[:2]
        box = ((disc[0] - disc[2], disc[1] - disc[2], disc[0] + disc[2], disc[1] + disc[2]) if disc
               else (0.0, 0.0, float(w), float(h)))
        return comparing.picture(image, arms, box, 420)

    @staticmethod
    def _page_table(table: dict) -> dict:
        """The comparison table as the page shows it: the columns that say something here (what ran only when a model
        fell back, a failure only when one failed; time kept, cost and reuse left to the table in Data)."""
        names = [c["name"] + (f" ({c['unit']})" if c.get("unit") else "") for c in table["columns"]]
        rows = table["rows"]

        def says(i: int) -> bool:
            name = table["columns"][i]["name"]
            if name == "What ran":
                return any(r[i] not in (None, r[1]) for r in rows)
            if name in ("Failed", "Reused"):
                return any(r[i] for r in rows)
            return name != "Cost"

        keep = [i for i in range(len(names)) if says(i)]
        return {"columns": [names[i] for i in keep], "rows": [[r[i] for i in keep] for r in rows]}

    def _document(self, x: Experiment, comparison: dict, audit: dict | None, table: dict | None) -> dict:
        """The results, made by the app from the run's records (the audit's words are the researcher's)."""
        from .untrusted import inline

        labels = {m["id"]: m["label"] for m in x.plan["models"]}
        cites_table = [{"id": x.outcome["table"], "kind": "result", "day": x.session, "title": "The comparison table"}] \
            if (x.outcome or {}).get("table") else []
        sections = [{"heading": "What was compared", "paragraphs": [{
            "text": (f"{said_list(list(labels.values()))} each segmented every cell on {x.plan['input']['says']}, "
                     f"through this app's own runner, exactly those models"
                     + (", and each model's cells were measured" if x.plan["measure"] else "") + "."
                     + (f' You asked: "{inline(x.asked, 300)}".' if x.asked else "")),
            "cites": []}]}]
        paragraphs = []
        for f in comparison["frames"]:
            cites = [{"id": a["population"], "kind": "population", "day": x.session,
                      "title": a.get("populationTitle") or f"{a['label']}'s cells"}
                     for a in f["record"]["arms"] if a.get("population")]
            words = []
            for a in f["record"]["arms"]:
                if a.get("failed"):
                    words.append(f"{a['label']} failed ({inline(a['failed'], 160)})")
                    continue
                check = ("" if a.get("measured") in (None, a.get("count"))
                         else f", but its measurement has {a['measured']} rows")
                words.append(f"{a['label']} found {cells(a.get('count'))}" + check)
            for p in f["record"]["summary"].get("pairs") or []:
                words.append(f"{p['matched']} were found by both {labels.get(p['a'], p['a'])} and "
                             f"{labels.get(p['b'], p['b'])}")
            for d in (f["method"] or {}).get("disagreement") or []:
                other = d["arms"][1]
                only_a, only_b = d.get("onlyA"), d.get(f"only{other.upper()}")
                ia, ib = "abcdef".index(d["arms"][0]), "abcdef".index(other)
                if ia < len(x.plan["models"]) and ib < len(x.plan["models"]) and only_a is not None:
                    unit = "px²" if d.get("unit") in (None, "pixel") else d["unit"]
                    words.append(f"{only_a:,.0f} {unit} were in {x.plan['models'][ia]['label']}'s cells only and "
                                 f"{only_b:,.0f} {unit} in {x.plan['models'][ib]['label']}'s only")
            paragraphs.append({"text": f"Frame {f['frame']}: " + "; ".join(words) + ".", "cites": cites})
        sections.append({"heading": "Frame by frame", "paragraphs": paragraphs})
        sections.append({"heading": "The comparison", "paragraphs": [{
            "text": self._overall_words(x, comparison) + ".", "cites": cites_table}],
            **({"table": self._page_table(table)} if table else {}),
            "figure": {"picture": 0, "caption": "The outlines side by side on frame 1: each model's cells on the "
                                                "frame's own pixels"}})
        if audit is not None:
            result = audit.get("result") or {}
            said = inline(result.get("summary") or result.get("headline") or result.get("text") or "", 1200) \
                if isinstance(result, dict) else ""
            name = audit.get("name") or "The researcher"
            if audit.get("status") == "completed" and said:
                text = f"{name}'s audit, in its own words (not reviewed by you): {said}"
            elif audit.get("status") == "completed":
                text = f"{name} finished its audit; its report is in Researchers."
            else:
                text = f"The audit did not finish: {inline(audit.get('note') or audit.get('status') or '', 300)}."
            cites = [{"id": audit["researcher"], "kind": "research", "day": x.session, "title": f"{name}'s audit"}] \
                if audit.get("researcher") else []
            sections.append({"heading": "The audit", "paragraphs": [{"text": text, "cites": cites}]})
        cited = sorted({c["id"] for s in sections for p in s["paragraphs"] for c in p["cites"]})
        title = f"{x.name}: {x.title}"
        return {"media": "document", "title": title, "asked": inline(x.asked, 600),
                "byline": f"Made by the app from {x.name}'s run records" + (", with the researcher's audit"
                                                                             if audit is not None else ""),
                "writer": "app", "records": {"given": len(cited), "inProject": len(cited), "cited": len(cited)},
                "leftOut": {}, "cites": cited, "document": {"title": title, "sections": sections},
                "experiment": {"id": x.id, "key": x.key, "run": x.run, "audit": x.audit}}

    async def _land(self, x: Experiment, comparison: dict, audit: dict | None) -> str:
        results = getattr(getattr(self.hub, "resources", None), "results", None)
        if results is None:
            return self._overall_words(x, comparison)
        table = await asyncio.to_thread(self._table, x, comparison)
        picture = await asyncio.to_thread(self._picture, x, comparison)
        values = self._document(x, comparison, audit, table)
        if picture is None:
            for s in values["document"]["sections"]:
                s.pop("figure", None)
        from .documents import summary_of

        entry = await asyncio.to_thread(results.add, "experiment", {"experiment": x.id}, summary_of(values),
                                        values=values, picture_bytes=[picture] if picture else (), thread=x.thread,
                                        request=x.asked, made_for=None, session=x.session, actor="assistant",
                                        media="document")
        x.outcome = {**(x.outcome or {}), "document": entry["id"]}
        self._save(x)
        where = await (self.landing or self._show)(x, entry)
        x.outcome["shown"] = where
        audit_words = ""
        if audit is not None:
            audit_words = ("; the researcher's audit is in it" if audit.get("status") == "completed"
                           else f"; the audit did not finish ({audit.get('note') or audit.get('status')})")
        return (f"{self._overall_words(x, comparison)}{audit_words}. Its results are saved in Data as {entry['id']} "
                f"(Write-ups), {where}")

    async def open(self, x: Experiment, client=None) -> str:
        """Its results, opened again in a Tab of their own on the studio that asked (its card's Open results)."""
        document = (x.outcome or {}).get("document")
        if not document:
            raise Refused(f"{x.name} has no results yet ({x.status})", next="wait for it to finish, or ask how it is going")
        if client is not None:
            x.client = client
        return await (self.landing or self._show)(x, {"id": document})

    async def _show(self, x: Experiment, entry: dict) -> str:
        """Opens the results in a Tab of its own through `present` (do=place, placement new-tab), as a request of its
        own on the studio that approved it (else the one whose report counts); done once the studio's report shows it."""
        app = self.app
        if app is None:
            return "not opened (no studio here)"
        from .ticket import issue, using
        from .brain.registry import ToolContext
        from .screen import fresh_report, screen_of

        screen = screen_of(self.hub)
        client = x.client if x.client is not None and x.client in getattr(self.hub, "clients", ()) else None
        if client is None:
            client = next(iter(getattr(screen, "_accepted", {}) or {}), None)
        if client is None or getattr(self.day, "sid", None) != x.session:
            return "not opened yet: no studio shows this Project now; ask to open it and it opens in a Tab of its own"
        with contextlib.suppress(Exception):
            await fresh_report(self.hub, client, now=True)
        asked = x.asked_in if x.asked_in is not None else app.conversation.here()
        request = issue(self.hub, "command", asked=asked, channel="system")
        try:
            # Pinned to what the studio shows now, as a hand-off's later step is (partner_tools.follow_up).
            pin = self.hub.screen.pin(client, request_id=request.admission.request_id, timing="server-handoff-arrival")
            info = self.hub.latest_info
            ctx = ToolContext("system", 0, info.id if info else None, f"Open {x.name}'s results",
                              asked=request.asked, admission=request.admission, actor="assistant",
                              request_id=request.admission.request_id, client=client, request_snapshot=pin,
                              board=app.brain.board)
            with using(request):
                run = app.brain.registry.start("present", {"what": entry["id"], "do": "place", "placement": "new-tab"},
                                               ctx)
                if run.task is not None:
                    request.track(run.task)
                    await run.task
            if run.ok:
                x.outcome = {**(x.outcome or {}), "tab": f"{x.name}: {x.title}"}
                return run.summary[:1:].lower() + run.summary[1:] if run.summary else "opened in a Tab of its own"
            return f"not opened in a Tab: {run.summary}"
        except Exception as exc:  # noqa: BLE001 - said in plain words; the results stay in Data, the reason in the log
            log.warning("experiment %s results not opened: %s", x.key, exc)
            return ("not opened in a Tab yet: the studio did not show what it has open just then; ask to open the "
                    "results and they open in a Tab of their own")
        finally:
            request.seal()

    # --- telling ---------------------------------------------------------------------------------------------------
    def status_words(self, x: Experiment) -> str:
        """How it is going, from its records (never a guess): its state, its frames, its run's attempts, its audit."""
        head = f"{x.name} ({x.title})"
        if x.status == "proposed":
            return f"{head} is planned and waiting for your yes (version {x.version}): {x.plan['input']['says']}"
        if x.status == "expired":
            return f"{head} was planned but never run: its plan waited over {PROPOSAL_S / 60:.0f} minutes"
        elapsed = (x.ended or self.clock()) - ((x.approved or {}).get("at") or x.created)
        clock = f"{elapsed / 60:.0f} min" if elapsed >= 90 else f"{elapsed:.0f} s"
        if x.status in LIVE:
            words = f"{head} is {x.status} ({clock} so far): {x.progress or 'working'}"
            if x.run and x.status in ("running", "auditing"):
                with contextlib.suppress(Exception):
                    from .workflow_service import service

                    status = service(self.hub, self.day).observed_status(x.run["id"], x.session)
                    counts = self._counts(x, status)
                    if counts:
                        words += "; so far " + counts
            return words
        if x.status == "done":
            return f"{head} finished after {clock}: {(x.outcome or {}).get('words') or 'its results are in Data'}"
        return f"{head} {x.status} after {clock}: {x.why}"

    def _counts(self, x: Experiment, status: dict) -> str:
        from . import experiment_steps
        from .scientific_comparisons import exact

        parts = []
        for i, m in enumerate(x.plan["models"]):
            counts = []
            for a in status["attempts"]:
                if a["node"] == f"m{i}" and a["status"] == "completed" and a.get("output"):
                    with contextlib.suppress(Exception):
                        saved = exact(self.store, a["output"], x.session)
                        counts.append(str((saved.document["state"].get(experiment_steps.RAN) or {}).get("count")))
            if counts:
                parts.append(f"{m['label']} {', '.join(counts)} cells")
        return "; ".join(parts)

    def _tell(self, x: Experiment) -> None:
        """One outcome, once: on the voice's bus (said at a pause, owed until heard) and in the conversation."""
        if x.status == "done":
            text = f"{x.name} finished: {(x.outcome or {}).get('words')}"
        elif x.status == "stopped":
            text = f"{x.name} stopped ({x.why}); what it made so far stays in this Project"
        else:
            text = f"{x.name} did not finish: {x.why}; its frames and the runs so far stay in this Project"
        app = self.app
        if app is None:
            return
        with contextlib.suppress(Exception):
            from .voice.events import bus_of

            bus_of(app.brain).publish("job-result" if x.status == "done" else "job-failed", text, ref=f"experiment:{x.key}")
        with contextlib.suppress(Exception):
            app.conversation.add("note", "system", text, asked=x.asked_in)


@dataclass(frozen=True)
class SimpleImpl:
    """A planned model as model_comparison's arms name it, when its run left no record."""
    m: dict

    @property
    def id(self) -> str:
        return self.m["id"]

    @property
    def label(self) -> str:
        return self.m["label"]


def experiments_of(hub, day=None, app=None) -> Experiments:
    found = getattr(hub, "experiments", None)
    if not isinstance(found, Experiments):
        found = Experiments(hub, day=day, app=app)
        with contextlib.suppress(AttributeError):
            hub.experiments = found
    if day is not None and found.day is None:
        found.day = day
    if app is not None and found.app is None:
        found.app = app
    return found
