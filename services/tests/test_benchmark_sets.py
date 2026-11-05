"""Benchmark sets (docs/bench/sets/, contract/benchmarks/benchmark-set.schema.json): every set Hooke keeps fits the one
shape and holds together, is listed in docs/bench/README.md, and the shape takes a user's set as it takes ours."""
from __future__ import annotations

import copy
import json

import pytest

from bench import benchmark_sets
from orchestrator.paths import REPO

SETS = sorted(benchmark_sets.SETS.glob("*.json"))


def test_there_are_sets():
    assert len(SETS) >= 7


@pytest.mark.parametrize("path", SETS, ids=lambda p: p.stem)
def test_every_set_fits_the_shape_and_holds_together(path):
    value = json.loads(path.read_text())
    assert benchmark_sets.problems(value) == []
    assert value["id"] == path.stem


def test_every_set_is_listed_in_the_readme():
    readme = (REPO / "docs" / "bench" / "README.md").read_text()
    assert [p.stem for p in SETS if f"`{p.stem}`" not in readme] == []


def _minimal(**over):
    value = {
        "schema": "benchmark-set/1", "id": "2026-12-01-my-cells", "title": "My cells, two segmenters",
        "measures": "Which segmenter finds more of my cells on my own pictures.",
        "ran": {"started": "2026-12-01T10:00:00+01:00"}, "runBy": {"kind": "user", "name": "A researcher"},
        "benchmark": {"name": "Cell count", "method": "Ten pictures counted by hand, then by each segmenter."},
        "arms": [{"id": "cellpose-sam", "label": "Cellpose-SAM"}, {"id": "micro-sam-lm", "label": "micro-sam"}],
        "metrics": [{"id": "found", "label": "Cells found", "unit": "fraction", "better": "higher"}],
        "results": [{"arm": "cellpose-sam", "values": [{"metric": "found", "value": 0.9, "outOf": 1}]},
                    {"arm": "micro-sam-lm", "values": [{"metric": "found", "value": 0.8, "outOf": 1}]}],
        "licence": "CC-BY-4.0", "privacy": {"images": False, "personalData": False},
    }
    value.update(over)
    return value


def test_a_users_own_set_fits_the_same_shape():
    assert benchmark_sets.problems(_minimal()) == []


@pytest.mark.parametrize("change, said", [
    (lambda v: v["privacy"].update(images=True), "privacy.images"),
    (lambda v: v["results"][0].update(arm="sam3"), "unknown arm 'sam3'"),
    (lambda v: v["results"][0]["values"][0].update(metric="speed"), "unknown metric 'speed'"),
    (lambda v: v["results"][0]["values"][0].update(value=1.5), "more than its 1"),
    (lambda v: v.update(cases=[{"id": "c1", "results": [{"arm": "cellpose-sam", "right": 3, "tries": 2}]}]), "right 3 of 2"),
    (lambda v: v["results"].pop(), "arm 'micro-sam-lm' has no results"),
    (lambda v: v["arms"].append({"id": "cellpose-sam", "label": "again"}), "listed twice"),
    (lambda v: v["runBy"].update(kind="anonymous"), "runBy.kind"),
    (lambda v: v.update(links=[{"kind": "write-up", "title": "Missing", "href": "docs/bench/no-such.md"}]), "not in the repository"),
])
def test_what_the_check_refuses(change, said):
    value = copy.deepcopy(_minimal())
    change(value)
    assert any(said in p for p in benchmark_sets.problems(value)), benchmark_sets.problems(value)


def test_the_cli_checks_every_set(capsys):
    assert benchmark_sets.main(["check"]) == 0
    assert capsys.readouterr().out.count("ok ") == len(SETS)
