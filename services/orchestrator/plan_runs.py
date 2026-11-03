"""An experiment that runs a design's Study plan, step by step (experiments.py kind "steps"; Lane C of the "Hooke
drives everything" plan; experiment_design.py makes the designs and their plans).

- **Planned from the design, nothing run** (`plan_of_design`): the experiment's plan is the design's checked steps as
  saved in its Study plan (the design saved first when it is not: `Designs.save_plan`), said on the experiment's card:
  what the user does by hand, what Hooke captures (fresh clips on a schedule, or an operation on the live view on a
  schedule: a mission), what Hooke analyses (a Study researcher, a saved workflow), how long Hooke's own steps take and
  what its researchers may cost.
- **The user's yes is the owner review** (experiments.approve): the card's Run it or their own words quoted reviews the
  Study plan (`Coordination.review_plan(owner_yes=...)`, refused past the limits the owner already approved) and starts
  the run. Without it nothing runs.
- **The run** (`run_steps`, the experiment's job) takes the steps in order and waits for each, never replaying one that
  finished (its row in `x.ran`):
  - manual: waits for the user ("Waiting for you: ..."); their "done" (experiment do=continue, their words quoted) or the
    card goes on. Hooke never says a step of theirs happened.
  - researcher: a Study researcher (research/study_researchers.py: reserved against the Study's allowance), waited for
    until it finishes.
  - capture / watch: a mission (research/missions.py) of the plan's own researcher (a Study researcher started once,
    whose brief is the design): fresh clips (`schedule_acquisition`'s mission "capture") or one operation on the live
    view, every so often for a window; waited for until the mission ends and its finding is written.
  - workflow: the view now held as a retained frame, then the saved workflow admitted through the Study
    (`Studies.start_workflow`) under an assignment this run proposes and claims (the reviewed plan's experiment count
    holds), waited for until it settles, and the assignment completed with what it did.
  A step that fails stops the run with its reason; what ran stays in the Project.
"""
from __future__ import annotations

import asyncio
import contextlib
import time

from .experiment_design import duration_words

POLL_S = 1.0
LEASE_RENEW_S = 120.0  # a claimed assignment's lease is five minutes; it is renewed while its workflow runs
LABEL = "Hooke"
WHO = {"manual": "You", "capture": "Hooke captures", "watch": "Hooke watches", "researcher": "A researcher",
       "workflow": "Hooke runs"}


def step_words(step: dict) -> str:
    """A step as the card says it."""
    kind, says = step["kind"], step["what"]
    if kind == "capture":
        return (f"Hooke captures {step['count']} fresh {step['clipS']:g}-second clips, one every "
                f"{duration_words(step['everyS'])} (about {duration_words(step['seconds'])}): {says}")
    if kind == "watch":
        return (f"Hooke runs {step['operation']} on the live view every {duration_words(step['everyS'])} for "
                f"{duration_words(step['forS'])}: {says}")
    if kind == "researcher":
        return f"A researcher ({step['effort']}, at most ${step['maximumUsd']:.2f}): {says}"
    if kind == "workflow":
        return f"Hooke runs the workflow {step['name']} on the view: {says}"
    return f"You: {says}"


def plan_of_design(proposal: dict, d: dict, study: str) -> dict:
    from research_worker.budget import EFFORTS

    steps = [{**s, "what": s["says"], "says": step_words({**s, "what": s["says"]})} for s in d["steps"]]
    schedules = any(s["kind"] in ("capture", "watch") for s in d["steps"])
    research = round(d["researchUsd"] + (EFFORTS["quick"].cap_usd if schedules else 0.0), 2)
    seconds = round(d["appSeconds"])
    says = (f"Its researchers at most ${research:.2f} from this Study's allowance" if research else "Nothing spent")
    if schedules:
        says += "; each schedule's analysis is charged to today's research cap when it starts"
    return {"kind": "steps", "title": d["title"][:120], "question": d["question"], "duration": d.get("duration") or "",
            "design": {"id": d["id"], "n": d["n"], "version": d["version"], "proposal": proposal["key"]},
            "study": {"id": study}, "models": [], "measure": False, "audit": None,
            "input": {"kind": "plan", "says": f"Design {d['n']} ({d['id']}, version {d['version']}): {d['question']}"},
            "steps": steps, "replicates": d["replicates"],
            "cost": {"says": says, "researchUsd": research, "auditUsd": 0.0, "modelsUsd": 0.0},
            "estimate": {"seconds": seconds, "least": seconds, "manual": d["manualSteps"], "unknown": [],
                         "auditS": None, "basis": "the design's schedules and its researchers' usual time"}}


def time_words(plan: dict) -> str:
    """The whole run's time first (the design's own estimate, the user's steps in it), then Hooke's own part."""
    e = plan.get("estimate") or {}
    hooke = (f"Hooke's own steps take about {duration_words(e.get('seconds') or 0)}" if e.get("seconds")
             else "Hooke's own steps take little time")
    manual = e.get("manual") or 0
    yours = f"your {manual} step{'s' if manual != 1 else ''} by hand" if manual else ""
    if plan.get("duration"):
        return f"In all {plan['duration']}" + (f" with {yours}" if yours else "") + f"; {hooke}"
    return hooke + (f", plus {yours}" if yours else "")


def first_words(plan: dict) -> str:
    """What happens first when it starts: a step of the user's it waits for, or Hooke's own first step."""
    step = plan["steps"][0]
    if step["kind"] == "manual":
        return f"first it waits for you to {step['what'][:1].lower()}{step['what'][1:]}, and goes on when you say it is done"
    return f"first, {step['says'][:1].lower()}{step['says'][1:]}"


def plan_said(x) -> str:
    p = x.plan
    kinds = [s["kind"] for s in p["steps"]]
    parts = []
    if kinds.count("manual"):
        parts.append(f"{kinds.count('manual')} by your hand")
    captures = kinds.count("capture") + kinds.count("watch")
    if captures:
        parts.append(f"{captures} captured by Hooke on a schedule")
    analyses = kinds.count("researcher") + kinds.count("workflow")
    if analyses:
        parts.append(f"{analyses} analysed by Hooke")
    return (f"{x.name}, not run yet: {p['title']}, the Study plan of design {p['design']['id']} "
            f"({len(kinds)} steps: {', '.join(parts)}; {p.get('replicates')} replicates). {time_words(p)}; "
            f"{p['cost']['says'][:1].lower()}{p['cost']['says'][1:]}; nothing runs until you say yes, and your yes "
            "approves the Study plan.")


# --- the yes reviews the Study plan ----------------------------------------------------------------------------------
def review(xs, x, *, actor: str, words: str) -> None:
    """The user's yes as the owner review of the plan's Study plan (refused past the owner's approved limits)."""
    from .research.coordination import Coordination
    from .experiment_design import studies_of
    from .experiments import Refused
    from .scientific_projects import Conflict, InvalidProject

    studies = studies_of(xs.hub, xs.day, xs.app)
    coordination = Coordination(studies)
    study = x.plan["study"]["id"]
    try:
        current = coordination.read(study, x.session)
        plan = current.get("plan") or {}
        if plan.get("status") == "reviewed":
            return
        reviewer = actor if actor.startswith("agent:") else "agent:system"
        coordination.review_plan(study, x.session, basis=current["basis"], actor=reviewer,
                                 label="You, by your yes to its run" if actor == "user" else f"Hooke {reviewer[6:]}",
                                 owner_yes=" ".join(words.split())[:300] or "Run it (the plan's card)")
    except (InvalidProject, Conflict, ValueError, OSError) as exc:
        raise Refused(f"{x.name}'s Study plan was not approved: {exc}; nothing was started",
                      next="review its limits in the Study in Hooke, then say yes again") from None


# --- the run ---------------------------------------------------------------------------------------------------------
def _row(x, i: int) -> dict:
    while len(x.ran) <= i:
        x.ran.append({"step": len(x.ran) + 1, "kind": x.plan["steps"][len(x.ran)]["kind"], "status": "waiting"})
    return x.ran[i]


async def run_steps(xs, x) -> str:
    """Every step in order, each waited for; returns the outcome in words."""
    from .experiments import Refused

    manager = _manager(xs)
    for i, step in enumerate(x.plan["steps"]):
        row = _row(x, i)
        if row["status"] == "done":  # never replayed
            continue
        if x.why == "you stopped it":
            raise asyncio.CancelledError
        row.update(status="running", at=xs.clock())
        xs._progress(x, f"Step {i + 1} of {len(x.plan['steps'])}: {step['what']}")
        xs._changed(x)
        kind = step["kind"]
        if kind == "manual":
            words = await _wait_for_user(xs, x, i, step)
            row.update(status="done", said=f"you said it was done: {words}" if words else "you said it was done")
        elif kind == "researcher":
            rid = await start_researcher(xs, x, request=f"{x.key}:s{i + 1}", brief=_brief(x, step), effort=step["effort"])
            row["researcher"] = rid
            xs._save(x)
            row.update(**await _wait_researcher(xs, x, manager, rid, i))
        elif kind in ("capture", "watch"):
            rid = await _plan_researcher(xs, x)
            row["researcher"] = rid
            mission = await _start_mission(xs, x, manager, rid, step, i)
            row["mission"] = mission
            xs._save(x)
            row.update(**await _wait_mission(xs, x, manager, rid, mission, i))
        elif kind == "workflow":
            row.update(**await _run_workflow(xs, x, step, i, row))
        else:  # a kind the design never checked: refused, never guessed
            raise Refused(f"step {i + 1} is a {kind} step, which this app cannot run", next=None)  # no next: said
        row["ended"] = xs.clock()
        xs._changed(x)
        if row["status"] != "done":
            raise RuntimeError(f"step {i + 1} ({kind}) did not finish: {row.get('said') or row['status']}")
    return outcome_words(x)


def outcome_words(x) -> str:
    made = [r for r in x.ran if r.get("researcher")]
    names = sorted({r.get("name") or r["researcher"] for r in made})
    return (f"all {len(x.ran)} steps of its plan done" + (f"; its findings are with {', '.join(names)} in Researchers"
                                                           if names else "")
            + "; what each step made is in this Project")


def _manager(xs):
    from . import experiment_steps

    manager = experiment_steps.manager_of(xs.hub)
    if manager is None:
        from .experiments import Refused

        raise Refused("this app has no researchers to run the plan's steps", next=None)  # no next: said
    return manager


def _brief(x, step: dict) -> str:
    return (f"{step['brief']}\n\nThis is a step of {x.name} ({x.plan['title']}), the experiment the user approved: "
            f"{x.plan['question']}")


async def start_researcher(xs, x, *, request: str, brief: str, effort: str) -> str:
    """A Study researcher for this plan (research/study_researchers.py): reserved against the Study's allowance; the
    same request is the same researcher (never a second)."""
    if xs.start_study_researcher is not None:  # tests stand one in
        return await xs.start_study_researcher(x, request=request, brief=brief, effort=effort)
    from .research import study_researchers

    if xs.app is None:
        raise RuntimeError("no app to start a researcher in")
    started = await study_researchers.start(xs.app, x.plan["study"]["id"], x.session, request=request, brief=brief,
                                            effort=effort)
    return started["researcher"]


async def _plan_researcher(xs, x) -> str:
    """The plan's own researcher, whose schedules (missions) these are: started once, at quick effort."""
    held = (x.outcome or {}).get("analyst")
    if held:
        return held
    p = x.plan
    brief = (f"Analyse what the schedules of {x.name} capture, for the question: {p['question']} The plan: "
             + "; ".join(s["what"] for s in p["steps"]) + ". Compare the conditions against the controls over "
             f"{p.get('replicates')} replicates, cite the frames and measurements you read, and say what the run "
             "cannot settle.")
    rid = await start_researcher(xs, x, request=f"{x.key}:analyst", brief=brief, effort="quick")
    x.outcome = {**(x.outcome or {}), "analyst": rid}
    xs._save(x)
    return rid


async def _start_mission(xs, x, manager, rid: str, step: dict, i: int) -> str:
    from .research.policy import ResearchRefused

    for _ in range(int(60 / POLL_S)):  # its first run must be going (the mission's admission is its researcher's)
        session = manager.get(rid)
        if session is not None and session.status not in ("queued", "starting"):
            break
        await asyncio.sleep(POLL_S)
    origin = {"experiment": x.key, "step": i + 1, "channel": "system", "thread": x.thread}
    try:
        if step["kind"] == "capture":
            mission = manager.start_mission(rid, "capture", step["everyS"], (step["count"] - 1) * step["everyS"],
                                            acquisition={"seconds": step["clipS"]}, origin=origin)
        else:
            mission = manager.start_mission(rid, step["operation"], step["everyS"], step["forS"], origin=origin)
    except ResearchRefused as exc:
        raise RuntimeError(f"its schedule was refused: {exc}") from None
    return mission.id


async def _wait_for_user(xs, x, i: int, step: dict) -> str:
    x.waiting = {"step": i + 1, "says": step["what"]}
    x.resume = asyncio.Event()
    xs._progress(x, f"Waiting for you (step {i + 1}): {step['what']}; say when it is done")
    xs._changed(x)
    await x.resume.wait()
    words = (x.waiting or {}).get("done", "")
    x.waiting = None
    return words


async def _wait_researcher(xs, x, manager, rid: str, i: int) -> dict:
    while True:
        session = manager.get(rid)
        if session is None:
            return {"status": "failed", "said": "its researcher is not in this app's records"}
        if session.status in ("done", "partial"):
            return {"status": "done", "name": session.name,
                    "said": f"{session.name} finished: {str((session.result or {}).get('summary') or '')[:300]}"}
        if session.status not in ("queued", "starting", "running"):
            return {"status": "failed", "name": session.name, "said": f"{session.name} {session.status}"}
        xs._progress(x, f"Step {i + 1} of {len(x.plan['steps'])}: {session.name} is {session.status}")
        await asyncio.sleep(POLL_S)


async def _wait_mission(xs, x, manager, rid: str, mission_id: str, i: int) -> dict:
    while True:
        session = manager.get(rid)
        mission = getattr(session, "mission", None) if session is not None else None
        if mission is None or mission.id != mission_id:
            past = [m for m in getattr(session, "past_missions", []) or [] if m.id == mission_id]
            mission = past[0] if past else None
        if mission is None:
            return {"status": "failed", "said": "its schedule is not in its researcher's records"}
        counts = mission.counts()
        if mission.status not in ("watching", "checkpoint", "finishing") and not manager.working(session):
            if mission.status == "done":
                return {"status": "done", "name": session.name,
                        "said": f"{session.name}'s schedule ended: " + ", ".join(f"{n} {k}" for k, n in counts.items() if n)}
            return {"status": "failed", "name": session.name,
                    "said": f"{session.name}'s schedule {mission.status}" + (f" ({mission.why_ended})" if mission.why_ended else "")}
        done = len(mission.ledger)
        xs._progress(x, f"Step {i + 1} of {len(x.plan['steps'])}: {done} of {mission.slots} slots")
        await asyncio.sleep(POLL_S)


async def _run_workflow(xs, x, step: dict, i: int, row: dict) -> dict:
    """The view now held, the workflow admitted through the Study under an assignment this run claims, waited for."""
    from . import experiment_steps
    from .research.coordination import Coordination
    from .experiment_design import studies_of
    from .scientific_comparisons import identity
    from .workflow_service import service

    studies = studies_of(xs.hub, xs.day, xs.app)
    coordination = Coordination(studies)
    study, actor = x.plan["study"]["id"], xs._actor(x)
    if actor in ("user", "studio"):
        actor = "agent:system"
    request = f"{x.key}:s{i + 1}"
    if not row.get("assignment"):
        assignment = await asyncio.to_thread(coordination.propose, study, x.session, request=request, assignment={
            "objective": step["what"], "deliverable": f"The workflow {step['name']}'s run on the view",
            "uncertainty": x.plan["question"][:8000], "decisionImpact": f"Step {i + 1} of {x.name}'s approved plan",
            "whyNewWork": "The approved plan runs it", "evidence": [], "dependsOn": []},
            actor=actor, label=LABEL)
        row["assignment"] = assignment["id"]
        xs._save(x)
    claim = lambda: coordination.claim(study, x.session, assignment=row["assignment"], actor=actor,  # noqa: E731
                                       label=LABEL, harness="hooke-experiment", model="app")
    await asyncio.to_thread(claim)
    if not row.get("frame"):
        jpeg, facts = await xs._view()
        saved = await asyncio.to_thread(experiment_steps.keep_frame, xs.store, x.session, jpeg=jpeg,
                                        facts={**facts, "experiment": {"name": x.name, "key": x.key, "step": i + 1}},
                                        title=f"{x.name} · step {i + 1}", request=request)
        row["frame"] = identity(saved)
        xs._save(x)
    operation, _made = await studies.start_workflow(study, x.session, request=request, workflow=step["workflow"],
                                                    revision=step["revision"], inputs=[row["frame"]], actor=actor,
                                                    label=LABEL, assignment=row["assignment"])
    run = (operation.get("execution") or {}).get("id")
    row["run"] = run
    xs._save(x)
    runner = service(xs.hub, xs.day)
    renewed = time.monotonic()
    while True:
        status = await asyncio.to_thread(runner.observed_status, run, x.session)
        if status["status"] in ("completed", "failed", "rejected", "stopped", "uncertain", "awaiting-review"):
            break
        if time.monotonic() - renewed > LEASE_RENEW_S:
            await asyncio.to_thread(claim)
            renewed = time.monotonic()
        xs._progress(x, f"Step {i + 1} of {len(x.plan['steps'])}: the workflow is {status['status']}")
        await asyncio.sleep(POLL_S)
    words = f"the workflow {step['name']} {status['status']}"
    with contextlib.suppress(Exception):  # the assignment's report: what ran, never a finding of the user's
        await asyncio.to_thread(coordination.complete, study, x.session, assignment=row["assignment"],
                                statement=f"Step {i + 1} of {x.name}: {words}.", evidence=[],
                                limitations="The app's run of an approved plan step; its outputs are in the run.",
                                actor=actor, label=LABEL)
    return {"status": "done" if status["status"] == "completed" else "failed", "said": words}


async def stop(xs, x) -> None:
    """Stops what the plan started: its researchers (and so their schedules) and a manual wait."""
    manager = None
    with contextlib.suppress(Exception):
        manager = _manager(xs)
    for rid in {r.get("researcher") for r in x.ran if r.get("researcher")} | {(x.outcome or {}).get("analyst")} - {None}:
        if manager is not None:
            with contextlib.suppress(Exception):
                await manager.cancel(rid, confirmed=True)
    if x.task is not None and not x.task.done():
        x.task.cancel()


def live_words(xs, x, elapsed: float) -> str:
    total = len(x.plan["steps"])
    done = sum(1 for r in x.ran if r.get("status") == "done")
    lead = f"{x.name} is running, {duration_words(elapsed)} in: {done} of {total} steps done"
    if x.waiting:
        return f"{lead}; waiting for you (step {x.waiting['step']}): {x.waiting['says']}; say when it is done."
    return lead + (f"; now {x.progress}" if x.progress else "") + "."
