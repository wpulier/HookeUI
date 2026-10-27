"""Data's sections, makers and runs on the session's index (ticket 66: S1 role, S2 maker, S3 run).

Every kind has a role (`produces.ROLES`); who made an entry is said only from its record (`resources.maker_of`), never
guessed; entries from one execution share its `run` and are one Data row. Real registry and result store, no camera.
"""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.produces import KINDS, ROLES, RESULT_ROLES, role_of
from orchestrator.resources import Entry, Researchers, maker_of, single_run
from tests.test_resources import app_at, jpeg

ALLOWED = {"acquired", "analysis", "writeup", "source", "other", None}


# --- S1: one role for every kind -------------------------------------------------------------------------------------
def test_every_kind_in_the_index_has_a_role_and_no_role_names_a_kind_that_is_not():
    missing = sorted(set(KINDS) - set(ROLES))
    assert missing == [], f"kinds with no role in produces.ROLES: {missing}"
    assert sorted(set(ROLES) - set(KINDS)) == []
    assert set(ROLES.values()) <= ALLOWED and set(RESULT_ROLES.values()) <= ALLOWED


def test_roles_follow_the_lab_notebook_and_a_result_is_decided_by_its_media():
    assert [role_of(k) for k in ("asset", "volume", "recording", "snapshot", "clip", "dataset")] == ["acquired"] * 6
    assert [role_of(k) for k in ("population", "tracks", "dataset-table", "finding-table", "finding-chart")] == ["analysis"] * 5
    assert [role_of(k) for k in ("research", "lesson", "video", "figure", "annotation")] == ["writeup"] * 5
    assert role_of("source") == "source"
    assert role_of("result") == "analysis" and role_of("result", "reference") == "source"  # a reference photo
    assert [role_of(k) for k in ("bookmark", "calibration", "taught-label", "project")] == ["other"] * 4
    assert [role_of(k) for k in ("member", "outcome", "fact", "job")] == [None] * 4


def test_the_wire_carries_role_maker_and_run_and_the_reading_a_researcher_pages_through_does_not():
    entry = Entry("d1", "result", "Count · 10:00", "2026-10-07", 1.0, media="reference", run="x1",
                  maker={"kind": "you", "name": "You"}, shown="")
    wire = entry.wire()
    assert (wire["role"], wire["maker"], wire["run"]) == ("source", {"kind": "you", "name": "You"}, "x1")
    assert not {"role", "maker", "run"} & set(entry.wire(listed=False))


# --- S2: one maker, from the record only ------------------------------------------------------------------------------
def test_an_older_record_that_cannot_tell_who_made_it_gets_no_maker():
    """ADR 0006: Data says nothing rather than guess "you" (the ticket's requestedBy:'user' is ambiguous)."""
    assert maker_of("result", "d1", {"tool": "count", "requestedBy": "user", "execution": "x1"}) is None
    assert maker_of("population", "e1", {"operation": "cells", "origin": "user-chosen", "execution": "x1"}) is None
    assert maker_of("population", "e1", {"operation": "cells", "origin": "brain-default"}) is None
    assert maker_of("result", "d2", {"tool": "run_capability", "execution": "x2", "by": "agent:system"}) is None
    assert maker_of("named-outline", "named-s1", {"by": "app", "operation": "outline"}) is None
    assert maker_of("bookmark", "b1", {"tool": "bookmark"}) is None
    assert maker_of("asset", "a1", {"from": "example"}) is None
    assert maker_of("result", "d3", None) is None


def test_who_started_the_run_names_the_maker_and_a_record_s_own_words_do_too():
    assert maker_of("result", "d1", {"tool": "count", "actor": "you"}) == {"kind": "you", "name": "You"}
    assert maker_of("population", "e1", {"actor": "assistant"}) == {"kind": "assistant", "name": "The Assistant"}
    assert maker_of("annotation", "a1", {"by": "user"})["kind"] == "you"
    assert maker_of("dataset", "x", {"by": "Record"})["kind"] == "you"
    assert maker_of("dataset-table", "tb1", {"by": "agent:chat"})["kind"] == "assistant"
    assert maker_of("named-outline", "named-s2", {"by": "app", "origin": "click"})["kind"] == "you"
    assert maker_of("result", "d1", {"actor": "someone"}) is None  # only the two words the run records


def test_a_researchers_work_and_what_its_hands_made_name_the_researcher_its_id_only_when_its_record_says():
    who = Researchers.of([SimpleNamespace(rid="r1", name="Researcher 1", ran=[{"id": "e4"}]),
                          SimpleNamespace(rid="r2", name="Researcher 2", ran=[])])
    assert maker_of("research", "r1", {"tool": "start_researcher"}, who) == {"kind": "researcher", "id": "r1", "name": "Researcher 1"}
    assert maker_of("source", "r2:source-0123456789ab", {"researcher": "r2", "name": "Researcher 2"}, who)["id"] == "r2"
    # Its hands' run, recorded in its `ran`: the id is the researcher's own record.
    assert maker_of("population", "e4", {"origin": "background", "for": "Researcher 1"}, who) == {
        "kind": "researcher", "id": "r1", "name": "Researcher 1"}
    # Made for a researcher by name with no record of which one: its name, never a guessed id.
    assert maker_of("population", "e9", {"origin": "background", "for": "Researcher 2"}, who) == {
        "kind": "researcher", "name": "Researcher 2"}
    # A camera clip captured for a researcher's mission names it by its id.
    assert maker_of("clip", "acq-1", {"operation": "capture", "for": "r2"}, who)["id"] == "r2"
    # "for" wins over a contextual actor: a researcher's hands are never the user's.
    assert maker_of("result", "d5", {"for": "Researcher 1", "actor": None}, who)["kind"] == "researcher"


def _run(built, name, handler, ctx):
    registry = built.brain.registry
    tool = registry.tools[name]
    original = tool.handler
    tool.handler = handler
    try:
        async def go():
            run = registry.start(name, {}, ctx)
            await run.task
            return run
        return asyncio.run(go())
    finally:
        tool.handler = original


@pytest.mark.parametrize("channel,actor,expected", [("chat", None, "assistant"), ("voice", None, "assistant"),
                                                    ("system", "you", "you"), ("system", None, None)])
def test_new_runs_record_who_started_them_where_they_begin(tmp_path, channel, actor, expected):
    from orchestrator.brain.registry import ToolContext
    from orchestrator.operations import _actor_now

    built = app_at(tmp_path).state.app
    seen = {}

    async def counting(ctx, args):
        seen["execution"] = _actor_now(None)  # what a job started inside this run records
        return "6 counted"

    run = _run(built, "count", counting, ToolContext(channel, 1, None, "count them", asked=built.day.here(), actor=actor))
    assert run.actor == expected and seen["execution"] == expected
    [entry] = built.hub.resources.list(kind="result")
    assert entry.made_by.get("actor") == expected
    assert (entry.maker or {}).get("kind") == expected


def test_the_studios_own_run_is_the_users(tmp_path):
    from orchestrator.commands import run_tool

    built = app_at(tmp_path).state.app
    tool = built.brain.registry.tools["count"]
    original = tool.handler

    async def counting(ctx, args):
        return "6 counted"

    tool.handler = counting
    try:
        asyncio.run(run_tool(built, "count", {}))
    finally:
        tool.handler = original
    [entry] = built.hub.resources.list(kind="result")
    assert entry.maker == {"kind": "you", "name": "You"}


def test_an_execution_says_who_started_it_unless_it_was_made_for_a_researcher():
    from orchestrator.operations import Execution

    assert Execution("cells", "cellpose-sam", "raw", "user-chosen", 1, "explicit", actor="you").made_by()["actor"] == "you"
    assert "actor" not in Execution("cells", "cellpose-sam", "raw", "background", 1, "explicit", made_for="Researcher 1").made_by()
    # A request from the studio itself (a click, Outline cells) is the user's; the brain's own turn the Assistant's.
    from orchestrator.operations import _actor_now
    assert _actor_now(SimpleNamespace(channel="studio")) == "you"
    assert _actor_now(SimpleNamespace(channel="voice")) == "assistant"
    assert _actor_now(SimpleNamespace(channel="system")) is None


def test_a_result_kept_for_a_researcher_never_carries_a_users_actor(tmp_path):
    built = app_at(tmp_path).state.app
    kept = built.hub.resources.results.add("enhance", {}, "clearer", picture_bytes=(jpeg(),), made_for="Researcher 1",
                                           actor="assistant")
    assert "actor" not in kept and kept["for"] == "Researcher 1"


# --- S3: one run, one row -----------------------------------------------------------------------------------------------
def test_entries_from_one_execution_share_its_run_and_a_result_over_several_is_its_own_row(tmp_path):
    assert single_run(["x1"]) == "x1" and single_run(["x1", "x2"]) is None and single_run([]) is None
    built = app_at(tmp_path).state.app
    index = built.hub.resources
    one = index.results.add("run_capability", {}, "271 regions", executions=["x9"], values={"name": "Bright objects"})
    many = index.results.add("compare_models", {}, "two models", executions=["x9", "x10"])
    runs = {e.id: e.run for e in index.list(kind="result")}
    assert runs == {one["id"]: "x9", many["id"]: None}
    wire = index.get(one["id"]).wire()
    assert wire["run"] == "x9" and wire["madeBy"]["execution"] == "x9"


def test_a_population_and_its_answer_from_one_run_share_it(tmp_path):
    from orchestrator.capture import Capture
    from orchestrator.evidence import evidence_of
    from tests.test_evidence import population

    built = app_at(tmp_path).state.app
    hub, index = built.hub, built.hub.resources
    info = SimpleNamespace(id=7, width=160, height=120, epoch=0, dx=0.0, dy=0.0, fov=None, disc=0.0, wall_at=1000.0)
    p = population(evidence_of(hub), Capture(7, jpeg(), info), thread=built.day.active_id)
    p.made_by = {**p.made_by, "execution": "x1-7", "actor": "you"}
    p.day = built.day.day
    evidence_of(hub).add(p)
    index.results.add("run_capability", {}, "3 cells", executions=["x1-7"], actor="you")
    rows = {}
    for entry in index.collect()[0]:
        rows.setdefault(entry.run, []).append(entry.kind)
    assert sorted(rows["x1-7"]) == ["population", "result"]
    assert all(e.maker == {"kind": "you", "name": "You"} for e in index.collect()[0] if e.run == "x1-7")


def test_a_snapshot_or_bookmark_says_who_took_it_when_its_file_records_it(tmp_path):
    import json

    built = app_at(tmp_path).state.app
    index = built.hub.resources
    folder = built.paths.snapshots / built.day.day
    folder.mkdir(parents=True)
    for name, actor in (("snapshot-101500-f9", "assistant"), ("snapshot-101600-f10", None)):
        (folder / f"{name}.json").write_text(json.dumps({"frameId": 9, "wallAt": 1.7e9, "outlines": [],
                                                         **({"actor": actor} if actor else {})}))
        (folder / f"{name}.png").write_bytes(b"\x89PNG fake")
    makers = {e.id: e.maker for e in index.list(kind="snapshot")}
    assert makers == {"snapshot-101500-f9": {"kind": "assistant", "name": "The Assistant"}, "snapshot-101600-f10": None}
