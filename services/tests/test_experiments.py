"""Experiments (Part 2.5, W5; orchestrator/experiments.py): a plan drafted from facts, nothing run before a yes, the
run's records kept by WorkflowService, an audit researcher reading them, and the results in a Tab of their own.

Real app, Project store, WorkflowService, comparison and measurement steps and research manager; stand-in models (a
threshold on the frame stands in for each model's runner call, `population.segment`) and a scripted researcher (the
research engine "fake"); no camera, GPU worker, provider or studio."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from tests.test_scientific_project_api import web  # noqa: F401 (the app under a temporary home)
from tests.waiting import until_async

MODELS = ["cellpose-sam", "micro-sam-lm"]


def frame_jpeg(n: int = 0) -> bytes:
    """A synthetic leaf-like frame: bright cells on a dark ground, a little different each time."""
    image = np.full((96, 128), 30, np.uint8)
    for i, (y, x) in enumerate([(10, 10), (10, 50), (50, 20), (55, 70), (20, 95)]):
        image[y:y + 18 + (n % 2), x:x + 22] = 200 if i % 2 else 150
    ok, data = cv2.imencode(".jpg", cv2.cvtColor(image, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
    assert ok
    return data.tobytes()


class Member:
    def __init__(self, box, polygon):
        self.box, self.polygons = box, [polygon]


class Population:
    """A model's cells as population.segment returns them (the attributes the experiment reads)."""
    n = 0

    def __init__(self, labels, impl):
        Population.n += 1
        self.id, self._labels, self.implementation = f"e{Population.n}", labels, impl
        self.title, self.made_at, self.made_by = f"Cell outlines · {impl}", time.time(), {}
        self.members = []
        for value in range(1, int(labels.max()) + 1):
            ys, xs = np.nonzero(labels == value)
            if len(ys):
                x0, y0, x1, y1 = float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())
                self.members.append(Member((x0, y0, x1, y1), [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]))

    def labels(self):
        return self._labels

    def live(self):
        return self.members


@pytest.fixture
def rig(web, monkeypatch):  # noqa: F811
    """The app with two stand-in models that can run, a scripted researcher and frames from a stand-in camera."""
    from orchestrator import operations
    from orchestrator.experiments import experiments_of

    client, built = web
    monkeypatch.setenv("MICROMIND_RESEARCH_ENGINE", "fake")
    monkeypatch.setattr(operations, "availability", lambda hub, impl: "")
    ran = []

    async def segment(hub, *, capture, choice, policy, origin, priority, made_for, thread, operation):
        ran.append({"impl": choice, "priority": priority, "origin": origin, "for": made_for, "capture": capture.id})
        gray = capture.gray()
        threshold = 120 if choice == MODELS[0] else 170  # the second "model" misses the dimmer cells
        _, labels = cv2.connectedComponents((gray > threshold).astype(np.uint8))
        return Population(labels.astype(np.uint16), choice)

    from orchestrator import population

    monkeypatch.setattr(population, "segment", segment)
    xs = experiments_of(built.hub, built.day, built)
    count = iter(range(100))

    async def sample(hub):
        n = next(count)
        return frame_jpeg(n), {"epoch": None, "dx": 0.0, "dy": 0.0, "disc": None, "discPx": 0.0, "width": 128,
                               "height": 96, "at": time.time(), "seq": n, "boot": "test", "run": "a-test-run",
                               "tracked": True, "frame": 1000 + n, "scale": None}

    xs.sample = sample
    xs.fake_researcher = {"seconds": 0.2, "finding": "The counts in the table match each model's cells"}
    landed = []

    async def landing(x, entry):
        landed.append(entry)
        return f'shown in a Tab of its own, "{x.name}: {x.title}", in front, its pages drawn (document:reader-tab)'

    xs.landing = landing
    yield SimpleNamespace(built=built, xs=xs, ran=ran, landed=landed)
    built.research.stop()


def test_a_plan_is_drafted_from_facts_and_nothing_runs_before_a_yes(rig):
    from orchestrator.experiments import Refused

    xs, session = rig.xs, rig.built.day.sid
    x = xs.draft(session=session, thread=None, models=MODELS, frames=3, every=1.5, asked="compare them and audit it")
    assert x.status == "proposed" and x.n == 1 and x.id == "x1"
    assert [m["id"] for m in x.plan["models"]] == MODELS
    assert x.plan["input"] == {"kind": "footage", "frames": 3, "everyS": 1.5,
                               "says": "3 frames of this footage, one every 1.5 s (over about 3 s)"}
    kinds = [s["kind"] for s in x.plan["steps"]]
    assert kinds == ["frames", "model", "model", "measure", "compare", "audit", "results"]
    assert x.plan["cost"]["says"] == "Nothing spent: the models run on this Mac; the audit at most $0.25"
    card = x.wire()
    assert card["canRun"] and card["status"] == "proposed" and len(card["steps"]) == 7
    # Planned only: no frame, no run, no researcher.
    assert x.frames == [] and x.run is None and rig.ran == [] and not rig.built.research.sessions
    # The facts are checked: a name that means nothing is refused with the choices, one model is not a comparison.
    with pytest.raises(Refused, match="choices"):
        xs.draft(session=session, thread=None, models=["cellpose-sam", "no-such-model"])
    with pytest.raises(Refused):
        xs.draft(session=session, thread=None, models=["cellpose-sam"])
    with pytest.raises(Refused, match="frames"):
        xs.draft(session=session, thread=None, models=MODELS, frames=40)
    # A changed plan is a new version; the card's old version is not the one approved.
    x = xs.change(x, frames=2)
    assert x.version == 2 and x.plan["input"]["frames"] == 2

    async def run_old_version():
        return xs.approve(x, version=1, by="card", actor="user")

    with pytest.raises(Refused, match="changed since it was shown"):
        asyncio.run(run_old_version())
    assert x.status == "proposed" and rig.ran == []
    # Kept in the Project store: a restart reads it back as it was.
    from orchestrator.experiments import Experiments

    again = Experiments(rig.built.hub, day=rig.built.day)
    back = again.get("x1", session)
    assert back.version == 2 and back.status == "proposed" and back.plan == x.plan


def test_a_plan_expires_without_a_yes(rig):
    from orchestrator.experiments import PROPOSAL_S, Refused

    xs, session = rig.xs, rig.built.day.sid
    x = xs.draft(session=session, thread=None, models=MODELS, on="view")
    x.created -= PROPOSAL_S + 1

    async def go():
        xs.approve(x, version=None, by="card", actor="user")

    with pytest.raises(Refused, match="waited over"):
        asyncio.run(go())
    assert x.status == "expired" and rig.ran == []


def test_the_approved_plan_runs_as_a_workflow_with_its_records_then_the_audit_then_the_results(rig):
    from orchestrator import experiment_steps
    from orchestrator.scientific_comparisons import exact
    from orchestrator.workflow_service import service

    built, xs = rig.built, rig.xs
    session = built.day.sid

    async def go():
        x = xs.draft(session=session, thread=None, models=MODELS, frames=2, every=1.0,
                     asked="compare Cellpose and micro-sam on this footage and have a researcher audit it")
        xs.approve(x, version=x.version, by="card", actor="user")
        await until_async(lambda: x.status in ("done", "failed", "stopped"), seconds=120)
        return x

    x = asyncio.run(go())
    assert x.status == "done", x.why
    # The frames: kept as retained scenes with their pictures.
    assert len(x.frames) == 2
    store = built.hub.resources.projects
    first = exact(store, x.frames[0], session)
    assert experiment_steps.frame_facts(first)["width"] == 128
    assert experiment_steps.frame_jpeg(store, first)[:2] == b"\xff\xd8"
    # Each model ran on each frame at research priority, made for the experiment.
    assert sorted(r["impl"] for r in rig.ran) == sorted(MODELS * 2)
    assert {r["priority"] for r in rig.ran} == {"research"} and {r["for"] for r in rig.ran} == {"Experiment 1"}
    # The run's records: every attempt completed, the models' outputs on the frame's grid, measured and compared.
    runner = service(built.hub, built.day)
    status = runner.observed_status(x.run["id"], session)
    assert status["status"] == "completed"
    nodes = sorted({a["node"] for a in status["attempts"]})
    assert nodes == ["compare", "m0", "m1", "measure0", "measure1"]
    model = next(a for a in status["attempts"] if a["node"] == "m0" and a["item"] == 0)
    saved = exact(store, model["output"], session)
    run = saved.document["state"][experiment_steps.RAN]
    assert run["impl"] == "cellpose-sam" and run["count"] == 5
    other = next(a for a in status["attempts"] if a["node"] == "m1" and a["item"] == 0)
    assert exact(store, other["output"], session).document["state"][experiment_steps.RAN]["count"] == 2
    compared = next(a for a in status["attempts"] if a["node"] == "compare" and a["item"] == 0)
    assert exact(store, compared["output"], session).document["kind"] == "scientific-method-comparison"
    # The comparison table in Data: a row per frame and model, then one per model over all frames.
    table = built.hub.resources.get(x.outcome["table"], session)
    assert table is not None
    # The audit: a second workflow whose researcher step read the run's record and wrote its report.
    audit = runner.observed_status(x.audit["id"], session)
    assert audit["status"] == "completed"
    step = audit["attempts"][0]
    assert step["research"]["provider"] == "local-researcher"
    findings = exact(store, step["output"], session).document
    assert findings["kind"] == "research-findings" and findings["state"]["researchFindings"]["accepted"] is False
    researcher = built.research.get(step["research"]["id"])
    assert researcher.status == "done" and researcher.origin["experiment"]["run"] == x.audit["id"]
    assert x.outcome["table"] in researcher.brief and "Check the numbers" in researcher.brief
    # The results: one document in Data, made by the app from the records, landed in a Tab of its own.
    assert len(rig.landed) == 1
    doc = built.hub.resources.get(x.outcome["document"], session)
    assert doc.kind == "result" and doc.media == "document"
    assert "shown in a Tab of its own" in x.outcome["words"]
    assert "Cellpose-SAM: 5, 5 cells" in x.outcome["words"]
    # One outcome, once: owed to the voice (said at a pause) and noted in the conversation it was asked in.
    from orchestrator.voice.events import bus_of

    told = [e for e in bus_of(built.brain).events.values() if e.ref == f"experiment:{x.key}"]
    assert [e.kind for e in told] == ["job-result"] and told[0].text.startswith("Experiment 1 finished: ")
    assert any("Experiment 1 finished" in t.text for t in built.conversation.turns if t.role != "user")


def test_a_failed_model_is_said_with_its_reason(rig, monkeypatch):
    from orchestrator import population
    from orchestrator.brain.registry import ToolFailed

    async def broken(hub, **kw):
        raise ToolFailed("can't segment every cell here (the worker is gone)", next=None)

    monkeypatch.setattr(population, "segment", broken)
    xs, session = rig.xs, rig.built.day.sid

    async def go():
        x = xs.draft(session=session, thread=None, models=MODELS, on="view", audit=False)
        xs.approve(x, version=None, by="card", actor="user")
        await until_async(lambda: x.status in ("done", "failed", "stopped"), seconds=60)
        return x

    x = asyncio.run(go())
    assert x.status == "failed" and "the worker is gone" in x.why and "frame 1" in x.why
    assert "did not finish" in xs.status_words(x)



def test_the_results_document_holds_the_table_the_outlines_side_by_side_and_the_audit(rig):
    """What lands in the Tab: one document made by the app from the run's records (never the brain's model's words),
    with the comparison table, the outlines side by side on frame 1 and the researcher's audit in its own words."""
    from orchestrator.documents import text_of

    built, xs = rig.built, rig.xs
    session = built.day.sid

    async def go():
        x = xs.draft(session=session, thread=None, models=MODELS, frames=2, every=1.0, asked="compare them")
        xs.approve(x, version=x.version, by="card", actor="user")
        await until_async(lambda: x.status in ("done", "failed", "stopped"), seconds=120)
        return x

    x = asyncio.run(go())
    assert x.status == "done", x.why
    values = built.hub.resources.results.values(x.outcome["document"], session)
    assert values["media"] == "document" and values["writer"] == "app"
    assert values["byline"] == "Made by the app from Experiment 1's run records, with the researcher's audit"
    sections = {s["heading"]: s for s in values["document"]["sections"]}
    assert list(sections) == ["What was compared", "Frame by frame", "The comparison", "The audit"]
    frames = sections["Frame by frame"]["paragraphs"]
    assert frames[0]["text"].startswith("Frame 1: Cellpose-SAM found 5 cells; micro-sam (light microscopy) found 2 cells; "
                                        "2 were found by both")
    assert {c["kind"] for c in frames[0]["cites"]} == {"population"}
    table = sections["The comparison"]["table"]
    assert table["columns"][:6] == ["Frame", "Model", "What ran", "Cells", "Measured", "Mask area (px²)"]
    # A row per frame and model, then one per model over all frames; the measurement step's rows match each count.
    assert [r[:2] for r in table["rows"]] == [[1, "Cellpose-SAM"], [1, "micro-sam (light microscopy)"],
                                             [2, "Cellpose-SAM"], [2, "micro-sam (light microscopy)"],
                                             ["all", "Cellpose-SAM"], ["all", "micro-sam (light microscopy)"]]
    assert [r[3] for r in table["rows"][:4]] == [r[4] for r in table["rows"][:4]] == [5, 2, 5, 2]
    assert sections["The comparison"]["figure"] == {"picture": 0, "caption": "The outlines side by side on frame 1: each "
                                                                             "model's cells on the frame's own pixels"}
    audit = sections["The audit"]["paragraphs"][0]
    assert audit["text"].startswith("Researcher 1's audit, in its own words (not reviewed by you): Simulated research")
    assert audit["cites"][0]["kind"] == "research"
    entry = built.hub.resources.get(x.outcome["document"], session)
    assert entry.pictures  # the outlines side by side, kept with it
    assert "Made by the app" in text_of(values) and "Frame | Model" in text_of(values)


def test_the_brain_runs_it_only_on_the_users_own_yes_and_the_card_runs_the_version_it_shows(rig, monkeypatch):
    """`experiment` do=run: the user's yes quoted from what they said (never the model's paraphrase), or the card's
    press through the studio's command; `experiment_status` reads how it goes from its records."""
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import research as research_tools

    built, xs = rig.built, rig.xs
    registry = built.brain.registry
    said = {"words": "I want to run an experiment comparing Cellpose and micro-sam on this footage"}
    monkeypatch.setattr(research_tools, "recent_request", lambda services, ctx: said["words"])
    asked = built.conversation.here()

    async def call(tool, args, channel="chat", actor="assistant"):
        ctx = ToolContext(channel, 1, None, question=said["words"], asked=asked, actor=actor)
        run = registry.start(tool, args, ctx)
        if run.task is not None:
            await run.task
        return run

    async def go():
        planned = await call("experiment", {"do": "plan", "models": MODELS, "frames": 2, "every": 1.0})
        assert planned.ok, planned.summary
        assert "planned (version 1; not run yet)" in planned.summary and "nothing has run" in planned.summary
        x = xs.get(None, built.day.sid)
        # A yes the user did not say is refused, and nothing runs.
        refused = await call("experiment", {"do": "run", "asked_with": "yes, run it"})
        assert refused.ok is False and "own yes" in refused.summary and x.status == "proposed"
        said["words"] = "Yes, run it."
        started = await call("experiment", {"do": "run", "asked_with": "yes, run it"})
        assert started.ok, started.summary
        assert x.status in ("sampling", "running") and x.approved["by"] == "words" and x.approved["actor"] == "agent:chat"
        going = await call("experiment_status", {})
        assert going.ok and "Experiment 1" in going.summary and ("sampling" in going.summary or "running" in going.summary)
        await until_async(lambda: x.status in ("done", "failed", "stopped"), seconds=120)
        ended = await call("experiment_status", {"experiment": "x1"})
        assert "finished after" in ended.summary and "Cellpose-SAM: 5, 5 cells" in ended.summary
        # The card: another plan, run by its version through the studio's command (the user's own press).
        second = await call("experiment", {"do": "plan", "models": MODELS, "on": "view", "audit": False})
        assert second.ok
        y = xs.get("x2", built.day.sid)
        xs.change(y, every=None, frames=None, on="view", measure=False)
        from orchestrator.commands import COMMANDS

        stale = await COMMANDS["experiment"](built, None, {"action": "run", "id": "x2", "version": 1})
        assert stale["ok"] is False and "changed since it was shown" in stale["error"]
        ran = await COMMANDS["experiment"](built, None, {"action": "run", "id": "x2", "version": 2})
        assert ran["ok"], ran
        assert y.approved["by"] == "card" and y.approved["actor"] == "user"
        await until_async(lambda: y.status in ("done", "failed", "stopped"), seconds=120)
        assert y.status == "done" and len(y.frames) == 1 and y.audit is None
        return x

    asyncio.run(go())


def test_the_results_land_through_present_place_new_tab_on_the_studio_that_approved_it(rig, monkeypatch):
    """The landing is a request of its own on the approving studio, pinned to what it shows now: `present` do=place,
    placement new-tab, with the document's id (documents.show then waits for the studio's report). With no studio
    showing the Project, it is said not opened, never claimed."""
    from orchestrator import screen as screen_module
    from orchestrator.experiments import Experiment

    built, xs = rig.built, rig.xs
    session = built.day.sid
    x = Experiment(key="p" + "0" * 32, n=9, session=session, plan={"models": [{"label": "A"}, {"label": "B"}]},
                   status="landing", outcome={"document": "d9"})
    assert asyncio.run(xs._show(x, {"id": "d9"})).startswith("not opened yet: no studio shows this Project now")
    client = object()
    built.hub.clients.add(client)
    started = []

    class Run:
        ok, summary, task = True, 'Shown in a Tab of its own, "Experiment 9: A vs B", in front, its pages drawn', None

    monkeypatch.setattr(built.hub.screen, "pin", lambda c, **kw: SimpleNamespace(client=c, **kw))

    async def fresh(hub, c, now=False):
        return None

    monkeypatch.setattr(screen_module, "fresh_report", fresh)
    monkeypatch.setattr(built.brain.registry, "start", lambda tool, args, ctx: started.append((tool, args, ctx)) or Run())
    x.client = client
    said = asyncio.run(xs._show(x, {"id": "d9"}))
    assert said.startswith('shown in a Tab of its own, "Experiment 9: A vs B"')
    (tool, args, ctx), = started
    assert (tool, args) == ("present", {"what": "d9", "do": "place", "placement": "new-tab"})
    assert ctx.client is client and ctx.request_snapshot.timing == "server-handoff-arrival" and ctx.channel == "system"
    built.hub.clients.discard(client)
