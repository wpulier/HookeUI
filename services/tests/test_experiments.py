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

