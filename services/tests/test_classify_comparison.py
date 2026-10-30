"""Comparisons at scale (Part 2.5, W5): N classifying models name the same cells through the runner, and the comparison
(agreement, counts per class, time, cost) is built from the runs' records alone (orchestrator/classify.py,
model_comparison.py); the brain's compare_models and a researcher's hands both use it. Stand-in models only."""
import asyncio
import re
from types import SimpleNamespace

import pytest

from orchestrator import classify, model_comparison
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.choosing import add_choosing_tools
from orchestrator.operations import runner
from orchestrator.population import layer_of
from orchestrator.ticket import issue, using
from orchestrator.vision import VisionResult
from tests.test_apply import run
from tests.test_population import CellFinder, cells, world  # noqa: F401  (the fixture: six stand-in cells)

RED, WHITE = "red blood cell", "white blood cell"
CLASSES = [RED, WHITE]


class StandInVision:
    """A cloud vision model's stand-in: names each numbered tile by `decide(n)`, reporting the tokens it used."""

    def __init__(self, model, decide, usage=(1000, 100), error=""):
        self.model, self.decide, self.usage, self.error, self.prompts = model, decide, usage, error, []

    async def ask(self, images, prompt, max_output_tokens=1500):
        self.prompts.append(prompt)
        assert len(images) == 1 and images[0][:2] == b"\xff\xd8"  # one contact sheet the app drew
        if self.error:
            return VisionResult(None, 50.0, self.model, error=self.error)
        first, last = map(int, re.search(r"tiles (\d+) to (\d+)", prompt).groups())
        tiles = [{"n": n, "class": self.decide(n), "sure": True} for n in range(first, last + 1)]
        return VisionResult({"tiles": tiles}, 120.0, self.model, usage={"input": self.usage[0], "output": self.usage[1]})


class ZeroShot(CellFinder):
    """The analysis worker: the cell finder, and MedSigLIP's `zeroshot` ranking (the first three crops red, the rest white)."""

    def __init__(self):
        super().__init__()
        self.ranked = []

    async def call(self, op, timeout=0, **payload):
        if op != "zeroshot":
            return await super().call(op, timeout, **payload)
        self.ranked.append(payload)
        names = payload["names"]
        return {"rankings": [[[names[0], 0.7], [names[1], 0.3]] if i < 3 else [[names[0], 0.2], [names[1], 0.8]]
                             for i in range(len(payload["jpegs"]))], "model": "google/medsiglip-448"}


MODELS = {
    "gemini-3.8-flash": lambda n: RED if n <= 3 else WHITE,
    "gemini-3.5-flash": lambda n: "unsure" if n == 6 else RED if n <= 3 else WHITE,
    "gpt-6.1-sol": lambda n: RED if n <= 2 else "White Blood Cell",  # its own capitals: the same class, said its way
    "gemini-4-argon": lambda n: RED if n <= 3 else "platelet",  # not one of the classes: kept as said, never mapped
}


@pytest.fixture
def stand_ins(world, monkeypatch):
    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(key, "test")
    world.gpu_heavy = ZeroShot()
    clients = {m: StandInVision(m, decide) for m, decide in MODELS.items()}
    clients["claude-opus-5-5"] = StandInVision("claude-opus-5-5", None, error="RuntimeError: Anthropic 400: credit balance is too low")
    monkeypatch.setattr(classify, "vision_for", lambda option: clients[option])
    population, _ = cells(world)
    layer_of(world).show(population)
    return world, population, clients


def compare(world, args):
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None, magnifier=SimpleNamespace(
        show_picture=lambda jpeg, label, object_id=None: "/api/picture/x")))

    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["compare_models"].handler(ToolContext("chat", 1, None, "compare them"), args)
    return run(go())


def test_five_classifiers_name_the_same_cells_and_the_comparison_is_their_records(stand_ins):
    world, population, clients = stand_ins
    record = run(model_comparison.compare_classes(
        world, population, model_comparison.select(world, "classify", [*MODELS, "MedSigLIP"]).impls, classes=CLASSES))
    arms = {a["impl"]: a for a in record["arms"]}
    assert list(arms) == [*MODELS, "medsiglip-448"]
    # Each model's run is the runner's Execution of operation classify, on the same members and classes.
    for a in arms.values():
        execution = runner(world).get(a["execution"])
        assert execution.operation == "classify" and execution.executed == a["impl"] and execution.policy == "raw"
    assert record["on"] == {"population": population.id, "revision": 0, "title": population.title,
                            "capture": population.capture_id, "members": 6, "of": 6}
    summary = record["summary"]
    members = summary["members"]
    assert summary["labels"][members[0]] == {**{m: RED for m in MODELS}, "medsiglip-448": RED}
    assert summary["labels"][members[5]]["gemini-3.5-flash"] == "unsure"
    assert summary["labels"][members[5]]["gpt-6.1-sol"] == WHITE  # "White Blood Cell" is the class, case aside
    assert summary["labels"][members[5]]["gemini-4-argon"] == "platelet"
    assert summary["counts"]["gemini-3.8-flash"] == {RED: 3, WHITE: 3}
    assert summary["counts"]["gemini-3.5-flash"] == {RED: 3, WHITE: 2, "unsure": 1}
    # Agreement between every pair, over the cells both named with a class; kappa from the same pairs.
    pair = next(p for p in summary["pairs"] if {p["a"], p["b"]} == {"gemini-3.8-flash", "gpt-6.1-sol"})
    assert (pair["both"], pair["same"], pair["agreement"]) == (6, 5, 0.833)
    assert pair["kappa"] == model_comparison.kappa([(RED, RED)] * 2 + [(RED, WHITE)] + [(WHITE, WHITE)] * 3)
    assert next(p for p in summary["pairs"] if {p["a"], p["b"]} == {"gemini-3.8-flash", "medsiglip-448"})["agreement"] == 1.0
    assert len(summary["pairs"]) == 10
    # Cells 1-2 every model named red alike; cell 3 GPT said white; 4-6 Argon said platelet, Gemini 3.5 unsure on 6.
    assert (summary["everyNamed"], summary["allAgree"]) == (5, 2)
    assert summary["majority"][members[3]] == {"label": WHITE, "agree": 4, "of": 5}  # Argon said platelet
    # Costs from the reported tokens at the list price; MedSigLIP on this Mac costs nothing.
    assert arms["gemini-3.8-flash"]["costUsd"] == pytest.approx((1000 * 0.75 + 100 * 3.75) / 1e6)
    assert arms["medsiglip-448"]["costUsd"] == 0.0 and arms["medsiglip-448"]["runs"] == "this Mac"
    assert record["totals"]["models"] == 5 and record["totals"]["failed"] == 0
    # One table, a row per model: counts per class, its agreement with the others, time and cost.
    names = [c["name"] for c in record["columns"]]
    assert names[:4] == ["Model", "What ran", "Named", "Unsure"] and RED in names and "platelet" in names
    assert {"Agrees with the others", "Time", "Cost"} <= set(names)
    row = dict(zip(names, record["rows"][0]))
    assert row["Model"] == "Gemini 3.8 Flash" and row[RED] == 3 and row[WHITE] == 3 and row["Cost"] > 0
    words = model_comparison.words(record)
    assert "Gemini 3.8 Flash and GPT-6.1 Sol gave the same class to 5 of 6 cells both named (83%" in words
    assert "of 6 cells, 2 were named alike by every model that ran" in words and "not accuracy" in words
    # The vision models were told the classes and judged the tiles; MedSigLIP ranked them for each crop.
    assert RED in clients["gemini-3.8-flash"].prompts[0] and "unsure" in clients["gemini-3.8-flash"].prompts[0]
    assert world.gpu_heavy.ranked[-1]["names"] == CLASSES and len(world.gpu_heavy.ranked[-1]["jpegs"]) == 6


def test_the_same_runs_asked_again_are_one_job_each_and_a_failed_model_says_why(stand_ins):
    world, population, clients = stand_ins
    impls = model_comparison.select(world, "classify", ["gemini-3.8-flash", "claude-opus-5-5", "gpt-6.1-sol"]).impls
    record = run(model_comparison.compare_classes(world, population, impls, classes=CLASSES))
    failed = next(a for a in record["arms"] if a["impl"] == "claude-opus-5-5")
    assert failed["failed"].startswith("Claude Opus 5.5 did not classify them (Anthropic 400: credit balance is too low)")
    assert record["totals"]["failed"] == 1 and len(record["summary"]["pairs"]) == 1  # the others are still compared
    row = dict(zip([c["name"] for c in record["columns"]], record["rows"][1]))
    assert row["Failed"] == failed["failed"] and row["Named"] is None


def test_compare_models_takes_up_to_six_classifiers_and_says_the_rest(stand_ins, monkeypatch):
    world, population, clients = stand_ins
    for m in ("gemini-3.5-flash-lite", "gpt-6-luna", "gemini-3.8-flash-hires"):
        clients[m] = StandInVision(m, lambda n: RED)
    said = compare(world, {"operation": "classify", "classes": CLASSES,
                           "implementations": ["gemini-3.8-flash", "gemini-3.5-flash", "gpt-6.1-sol", "gemini-4-argon",
                                               "medsiglip-448", "gpt-6-luna", "gemini-3.5-flash-lite"]})
    assert said.startswith(f"Classified 6 of {population.title}'s 6 cells ({population.id}) with each model")
    assert "Gemini 3.8 Flash: 3 red blood cell, 3 white blood cell" in said
    assert said.endswith("left out (6 at once at most): Gemini 3.5 Flash-Lite")
    with pytest.raises(ToolFailed, match="two different models"):
        compare(world, {"operation": "classify", "implementations": ["gemini-3.8-flash"]})


def test_medsiglip_needs_classes_and_open_naming_is_said(stand_ins, monkeypatch):
    world, population, clients = stand_ins
    impls = model_comparison.select(world, "classify", ["gemini-3.8-flash", "medsiglip-448", "gpt-6.1-sol"]).impls
    record = run(model_comparison.compare_classes(world, population, impls))  # no classes, no subject's list
    assert record["summary"]["vocabulary"] == "open" and "own words" in clients["gemini-3.8-flash"].prompts[-1]
    assert next(a for a in record["arms"] if a["impl"] == "medsiglip-448")["failed"].startswith("MedSigLIP 448 ranks a list")
    assert "compared word for word" in model_comparison.words(record)
    world.scene.context = {**(world.scene.context or {}), "specimen": "a blood smear, Wright stain"}
    record = run(model_comparison.compare_classes(world, population, impls))  # the subject's list, MedSigLIP's sentence
    assert record["summary"]["vocabulary"] == "subject" and "neutrophil" in record["summary"]["classes"]
    assert world.gpu_heavy.ranked[-1]["template"] == "a {} in a blood smear"


def test_kappa_and_agreement_are_the_arithmetic():
    assert model_comparison.kappa([("a", "a"), ("b", "b")]) == 1.0
    assert model_comparison.kappa([("a", "b"), ("b", "a")]) == -1.0
    assert model_comparison.kappa([("a", "a"), ("a", "a")]) is None  # chance agreement is total
    arms = [{"impl": "x", "label": "X", "labels": {"c1": {"label": "a"}, "c2": {"label": "unsure"}}},
            {"impl": "y", "label": "Y", "labels": {"c1": {"label": "a"}, "c2": {"label": "b"}}},
            {"impl": "z", "label": "Z", "failed": "no key"}]
    summary = model_comparison.agreement(arms, ["c1", "c2"])
    assert summary["pairs"] == [{"a": "x", "b": "y", "both": 1, "same": 1, "agreement": 1.0, "kappa": None}]
    assert (summary["everyNamed"], summary["allAgree"]) == (1, 1) and summary["majority"]["c2"] == {"label": "b", "agree": 1, "of": 2}


def test_the_comparison_table_is_kept_bound_to_its_digest_and_read_as_the_apps_own(stand_ins):
    from orchestrator.tables import result_table

    world, population, _ = stand_ins
    impls = model_comparison.select(world, "classify", ["gemini-3.8-flash", "gpt-6.1-sol"]).impls
    record = run(model_comparison.compare_classes(world, population, impls, classes=CLASSES))
    kept = model_comparison.values(record)
    table = result_table("d7", {"title": "Model comparison · 18:04"}, kept)
    assert table["kind"] == "result-table" and table["count"] == 2 and "comparison of 2 models' runs" in table["note"]
    assert table["rows"][0]["model"] == "Gemini 3.8 Flash" and table["rows"][0]["red_blood_cell"] == 3
    tampered = {**kept, "rows": [[*kept["rows"][0][:2], 99, *kept["rows"][0][3:]], kept["rows"][1]]}
    assert result_table("d7", {"title": "x"}, tampered) is None  # what is kept is no longer what was compared


def test_a_researcher_classifies_with_five_models_and_keeps_the_comparison_made_for_it(stand_ins, tmp_path):
    from orchestrator.research.hands import Hands
    from orchestrator.resources import Index, Results
    from tests.test_research_hands import session

    from orchestrator.day import DayStore

    world, population, _ = stand_ins
    made_in = getattr(population, "session", "") or population.day
    day = DayStore(root=tmp_path / "sessions", day=made_in)
    world.resources = Index(world, day=day, results=Results(tmp_path / "results", day=day))
    s = session()
    s.sitting = day.sid  # the researcher works in the cells' own Project
    logged = []
    reply = asyncio.run(Hands(world, log=logged.append).run(s, "classify", "compare five classifiers on these cells", input=population.id,
                                         models=[*MODELS, "medsiglip-448"], classes=CLASSES))
    assert reply["ok"] and reply["id"].startswith("d"), (reply, logged[-1].get("error"))
    assert reply["words"].startswith(f"classified 6 of {population.title}'s cells ({population.id}) with 5 models "
                                     f"and kept the comparison ({reply['id']}): 5 of 5 models ran; of 6 cells, 2 were "
                                     "named alike by every model that ran; pairs agreed on ")
    assert reply["words"].endswith("Its numbers are in the entry")
    entry = world.resources.results.get(reply["id"])
    assert entry["for"] == "Researcher 2" and entry["title"].startswith("Model comparison, for Researcher 2 · ")
    kept = world.resources.results.values(reply["id"])
    assert kept["comparison"]["summary"]["allAgree"] == 2 and kept["producer"]["implementation"] == "model-comparison"
    # Every run it compared is an execution made for the researcher, at the researchers' priority, in the record.
    for execution in entry["executions"]:
        record = runner(world).get(execution)
        assert record.made_for == "Researcher 2" and record.origin == "background"
    assert world.gpu_heavy.ranked[-1]["priority"] == "research"
    assert s.ran[-1]["operation"] == "classify" and s.ran[-1]["of"] == population.id and len(s.ran[-1]["executions"]) == 5
    assert reply["values"]["comparison"]["allAgree"] == 2 and len(reply["values"]["comparison"]["pairs"]) == 10
    # What it can't do is said with a next step a researcher can take.
    one = asyncio.run(Hands(world).run(s, "classify", "x", models=["gemini-3.8-flash"]))
    assert not one["ok"] and "two classifying models" in one["error"] and "medsiglip-448 = MedSigLIP 448" in one["next"]
    bad = asyncio.run(Hands(world).run(s, "segment", "x", models=["gemini-3.8-flash"]))
    assert not bad["ok"] and "only to classify" in bad["error"]


def test_the_research_process_lists_the_apps_classifiers():
    from orchestrator import names
    from research_worker.budget import CLASSIFIERS, MAX_CLASSIFIERS

    space = names.index()["classify"]
    assert CLASSIFIERS == {e.id: e.name for e in space.entries.values()}  # every one: they are named only to compare
    assert MAX_CLASSIFIERS == model_comparison.MAX_MODELS == 6  # "two to six", as compare_models' description says it


def test_the_researchers_run_tool_sends_classify_with_its_models_and_classes():
    import time

    from research_worker.budget import EFFORTS
    from research_worker.researcher import Researcher

    asked = []

    async def request(op, **payload):
        asked.append((op, payload))
        return {"ok": True, "id": "d4", "title": "Model comparison, for Researcher 2 · 18:04",
                "words": "classified 6 of Cell outlines' cells (e3) with 2 models and kept the comparison (d4): ..."}

    researcher = Researcher.__new__(Researcher)
    researcher.rid, researcher.runs = "r2", 0
    researcher.effort, researcher.started_at = EFFORTS["standard"], time.monotonic()
    researcher._looked_at = set()
    researcher.emit = lambda event, **fields: None
    researcher.request = request
    researcher.step = lambda text, kind="step", detail=None: None
    result, _ = asyncio.run(researcher.tool_run({"operation": "classify", "why": "compare", "input": "e3",
                                                 "models": ["gemini-3.8-flash", "medsiglip-448"], "classes": CLASSES}))
    assert result["ok"] and asked[-1][1]["models"] == ["gemini-3.8-flash", "medsiglip-448"]
    assert asked[-1][1]["classes"] == CLASSES and asked[-1][1]["input"] == "e3"
    refused, _ = asyncio.run(researcher.tool_run({"operation": "classify", "why": "x", "models": ["gemini-3.8-flash"]}))
    assert refused["error"].startswith("models are two to 6 of:")


def test_compare_models_keeps_one_comparison_table_in_data_that_show_measurements_reads(stand_ins, tmp_path):
    from orchestrator.day import DayStore
    from orchestrator.resources import Index, Results
    from orchestrator.tables import table_of

    world, population, _ = stand_ins
    day = DayStore(root=tmp_path / "sessions", day=getattr(population, "session", "") or population.day)
    world.resources = Index(world, day=day, results=Results(tmp_path / "results", day=day))
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None, magnifier=SimpleNamespace(
        show_picture=lambda jpeg, label, object_id=None: "/api/picture/x")))

    async def go():
        with using(issue(world, "turn")):
            done = registry.start("compare_models", {"operation": "classify", "classes": CLASSES, "of": population.id,
                                                     "implementations": ["gemini-3.8-flash", "gpt-6.1-sol", "medsiglip-448"]},
                                  ToolContext("chat", 1, None, "compare three classifiers on these cells"))
            await done.task
            return done
    done = run(go())
    assert done.ok, done.summary
    entries = world.resources.results.entries()
    assert len(entries) == 1  # one entry: the run's own, its table kept with it (never a second for the same run)
    entry = entries[0]
    assert entry["title"].startswith("Model comparison · ") and entry["id"] in done.summary
    assert f"the comparison's table is kept in Data as {entry['id']} (3 classifiers compared)" in done.summary
    assert len(entry["executions"]) == 3 and {m["operation"] for m in entry["madeBy"]} == {"classify"}
    table = table_of(world, entry["id"])  # what show_measurements opens, with the placement it is given
    assert table["count"] == 3 and [r["model"] for r in table["rows"]] == ["Gemini 3.8 Flash", "GPT-6.1 Sol", "MedSigLIP 448"]
    assert table["rows"][2]["cost"] == 0.0 and table["rows"][0]["agrees_with_the_others"] is not None


def test_the_manager_passes_a_researchers_classify_request_to_its_hands(stand_ins, tmp_path):
    import json

    from orchestrator.day import DayStore
    from orchestrator.research import ResearchManager
    from orchestrator.research.hands import Hands
    from orchestrator.resources import Index, Results
    from tests.test_research import FakeHub
    from tests.test_research_hands import session

    world, population, _ = stand_ins
    day = DayStore(root=tmp_path / "sessions", day=getattr(population, "session", "") or population.day)
    world.resources = Index(world, day=day, results=Results(tmp_path / "results", day=day))
    manager = ResearchManager(FakeHub(), root=tmp_path)
    manager.hands = Hands(world)
    s = session()
    s.root, s.sitting = tmp_path, day.sid
    manager.sessions["r2"] = s
    reply = asyncio.run(manager._request({"op": "run", "rid": "r2", "operation": "classify", "why": "compare them",
                                          "input": population.id, "models": ["gemini-3.8-flash", "gpt-6.1-sol"],
                                          "classes": CLASSES}))
    assert reply["ok"] and reply["id"].startswith("d"), reply
    saved = json.loads((tmp_path / "2026-10-07" / "r2" / "session.json").read_text())
    assert saved["ran"][0]["operation"] == "classify" and saved["ran"][0]["models"] == ["gemini-3.8-flash", "gpt-6.1-sol"]


def test_the_side_by_side_picture_tiles_six_models_three_to_a_row_whatever_their_captions():
    import cv2
    import numpy as np

    from orchestrator import comparing

    view = np.full((400, 600, 3), 128, np.uint8)
    arms = [{"polygons": [[[100, 100], [200, 100], [200, 200], [100, 200]]],
             "caption": "EdgeTAM · 5,184 px²" if i % 2 else "EdgeTAM → SAM 3.1 (cloud), its fallback · 5,184 px²"}
            for i in range(6)]
    picture = cv2.imdecode(np.frombuffer(comparing.picture(view, arms, (100, 100, 200, 200)), np.uint8), cv2.IMREAD_COLOR)
    assert 2 * 300 < picture.shape[0] < 3 * 300  # two rows of three, a two-line caption never breaks the row
    assert len(set(comparing.COLOURS_BGR)) == 6
