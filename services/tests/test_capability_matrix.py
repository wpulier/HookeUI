"""The capability matrix (the user's Q13 and Q20 of 2026-10-11; Slice 8 of that day's plan): every capability in
contract/capabilities.json, by every actor that can ask for it (chat, the voice's hand-off, a researcher), with every
model option of its roles, runs its real tool once in the real app (App.build) over stand-in workers and scripted
models that answer at once (tests/capability_rig.py). Each row checks that:

- the request reached the implementation it named (the worker op and model asked, the cloud call made, the model
  asked by the tool, what the research process was asked to start);
- the name it used resolves through names.py (the `use` step names the model by its one name with where it runs);
- a choice that can't run is refused saying why: needs a key, not installed here, not in this edition, done one way.

A capability × actor with no planned row fails (`test_every_capability_and_actor_has_a_planned_row`). What only the
user does in the studio, or the app does on its own, is asked by none of the three (`askers`), and is checked by its
own tests. The real models are bench/capability_check.py's (make capability-check).

Rows run per capability and actor, every model option inside one test (one failure message lists every row that
failed), within the 90 s a half of the matrix is given (`test_the_matrix_fits_in_its_time`: a wall-clock bound, so make
test-timing checks it, running the whole matrix in one process; the module is marked `wall_clock` for that)."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

import numpy as np
import pytest

from orchestrator import capabilities, model_checks, names
from orchestrator.model_checks import STOMA_BOX

ACTORS = ("chat", "voice", "researcher")
# A researcher's own operations (research_worker.budget.RUN_OPERATIONS), by the capability that is each.
RUNS = {"research-run": "segment", "research-enhance": "enhance", "research-motion": "motion"}
# Roles a request names a model for with `use` (names.roles), by the operation or setting `use` takes.
USE_AS = {"outline": "outline", "cells": "cells", "embed": "looks"}
# Methods whose choice runs a model of its own: set as the Behaviour menu sets them, from now on.
BEHAVIOUR = ("dissect", "track", "claim_check", "lesson_check", "reference_search")
BUDGET_S = 90.0
_spent: dict[str, float] = {}
pytestmark = pytest.mark.wall_clock  # make test-timing runs the whole matrix: its time bound sums every row's


@dataclass
class Plan:
    """How a capability is asked for: the user's words, the request's steps (tool, args), what must be on the board
    first (`setup`: rig setups, then steps run as earlier chat requests), and what it must end in."""
    words: str
    steps: list
    setup: tuple = ()
    before: list = field(default_factory=list)  # earlier requests' steps (chat), whose results the row's request uses
    expect: str = "ok"  # "ok", or "refused: <words>" when the stand-in world can't do it and it must say why
    # Roles of the capability a path does not reach, with why: "role" for every actor, "actor:role" for one.
    not_on_path: dict = field(default_factory=dict)
    # The steps name the row's model themselves ("{name}": its one name, "{use_as}": what `use` calls its role,
    # "{other}": another of the role's models), instead of a `use` step before them; and how the model is reached:
    # "asked" (a worker, model or process was asked for it), "listed" (the answer names it and whether it can run),
    # "set" (the setting is it from now on).
    names_model: bool = False
    reach: str = "asked"


O1 = "o1"
ASK_LESSON = "make me a lesson on this"
NO_STUDIO = "refused: no studio is showing the view now"
ENGINE_KEPT = ("a researcher's model is the one it started with (Slice 5: chosen when it starts); a follow-up or a new "
               "version runs on it")
RUN_CHOOSES = ("the cell finder is the researcher's own choice in its `run` (research/hands.py `implementation`), not the "
               "request's that started it; the researcher row reaches it")
RUN_ENGINE = ("a researcher's `run` asks the app for an operation; its own model was chosen when it started (chat and the "
              "voice reach it by starting one)")
PLANS: dict[str, Plan] = {
    # The real registry reads catalog descriptors and retains a frozen definition. No model or scientific run
    # is stood in for here; fitting and workflow execution have their separate exact-input/receipt checks.
    "find-workflows": Plan("which scientific capabilities can I use in a workflow?", [
        ("find_workflows", {"capabilities": True})]),
    "workflow": Plan("save a workflow that exports its exact input", [("workflow", {"command": {
        "action": "create", "name": "Export the retained input", "request": "f" * 32,
        "graph": {"format": 1, "input": {"kind": "scientific-scene"}, "nodes": [
            {"id": "export", "name": "Export input", "kind": "export", "needs": ["input"], "settings": {}}]},
    }})]),
    "saved-make-table": Plan("keep these measurements in an editable table", [
        ("make_table", {"sources": [{"id": "e1", "revision": 0}], "mode": "frozen"})], ("cells",)),
    "saved-edit-table": Plan("name the saved table", [
        ("edit_table", {"id": "tb1", "version": 1, "edits": [{"op": "title", "title": "Cell measurements"}]})],
        ("cells",), before=[("make_table", {"sources": [{"id": "e1", "revision": 0}], "mode": "frozen"})]),
    "saved-analyze": Plan("measure the correlation of the saved cell positions", [
        ("analyze", {"table": "tb1", "version": 1, "spec": {"method": "pearson", "x": "x", "y": "y"}, "mode": "frozen"})],
        ("cells",), before=[("make_table", {"sources": [{"id": "e1", "revision": 0}], "mode": "frozen"})]),
    "saved-make-graph": Plan("plot the saved cell areas", [
        ("make_graph", {"table": "tb1", "version": 1, "spec": {"kind": "histogram", "value": "areaPx", "bins": 3}, "mode": "frozen"})],
        ("cells",), before=[("make_table", {"sources": [{"id": "e1", "revision": 0}], "mode": "frozen"})]),
    "saved-read-analysis": Plan("read the saved graph numbers without recomputing", [
        ("read_analysis", {"id": "gr1", "version": 1})], ("cells",), before=[
        ("make_table", {"sources": [{"id": "e1", "revision": 0}], "mode": "frozen"}),
        ("make_graph", {"table": "tb1", "version": 1, "spec": {"kind": "histogram", "value": "areaPx", "bins": 3}, "mode": "frozen"})]),
    # This rig deliberately has no originating Studio. Never route a Tab action to another client.
    "saved-tabs": Plan("list this Project's Tabs", [("tabs", {"action": "list"})],
        expect="refused: the originating Studio is not open for this Project"),
    "compare-retained-methods": Plan("compare retained methods", [("compare_retained_methods", {})],
        expect="refused: Choose the exact method selection from this Project"),
    "retain-population-labels": Plan("retain this population as native labels", [("retain_population_labels", {})],
        expect="refused: Choose the declared operation and exact saved population revision"),
    "measure-saved-objects": Plan("measure the saved labels", [("measure_saved_objects", {})],
        expect="refused: Choose describe or run"),
    "rename": Plan("call that a guard cell", [("name_outline", {"object": O1, "name": "guard cell"})], ("outline",)),
    "suggest-name": Plan("I think that's a guard cell", [("propose_correction", {"object": O1, "to_label": "guard cell",
                                                                                 "reason": "two bean-shaped cells"})], ("outline",)),
    "highlight": Plan("highlight the stoma", [("highlight", {"things": ["s1"]})], ("look",)),
    "select": Plan("select the stoma", [("select", {"object": O1})], ("outline",)),
    "clear": Plan("clear the highlights", [("clear", {})], ("outline",)),
    "cells": Plan("outline all the cells", [("outline_cells", {})]),
    "correct-cells": Plan("that's not a cell", [("correct_cells", {"change": "remove", "members": ["c1"]})], ("cells",)),
    "cells-by-colour": Plan("highlight the biggest cell", [("highlight", {"of": "e1", "by": "areaPx", "top": 1})], ("cells",)),
    "find": Plan("find the stoma", [("find", {"what": "stoma"})]),
    "find-inside": Plan("point out the guard cell in that stoma", [("find", {"what": "guard cell", "inside": O1})], ("outline",)),
    "find-like": Plan("find all like this one", [("find", {"what": "stoma", "like": O1})], ("outline",)),
    "find-concept": Plan("find every stoma by its name with SAM 3.1", [("find", {"what": "stoma", "by": "concept"})]),
    "find-cells": Plan("outline every cell", [("find", {"what": "cell", "by": "cells"})]),
    "dissect": Plan("what is it made of?", [("dissect", {"object": O1})], ("outline",)),
    "zoom": Plan("magnify that", [("zoom", {"object": O1})], ("outline",)),
    "hold": Plan("hold it there", [("hold", {"object": O1})], ("outline", "magnifier")),
    "compare": Plan("compare these two", [("compare", {"a": "o1", "b": "o2"})], ("outline", "outline2")),
    "compare-models": Plan("compare these two models on that", [("compare_models", {
        "operation": "{use_as}", "object": O1, "implementations": ["{name}", "{other}"]})], ("outline",), names_model=True),
    "contrast": Plan("how are these two different?", [("contrast", {"a": "o1", "b": "o2"})], ("outline", "outline2")),
    "focus-check": Plan("is it in focus?", [("focus_check", {"object": O1})], ("outline",)),
    "show-picture": Plan("show me the labelled picture", [("show_in_magnifier", {"researcher": "r1"})], ("lesson",)),
    "magnifier-source": Plan("show just the live close-up", [("magnifier_source", {"source": "live"})], ("outline", "magnifier")),
    "magnifier-picture": Plan("make just the reference picture bigger", [("magnifier_picture", {"source": "@picture", "scale": "2"})],
                              ("outline", "magnifier", "reference")),
    "reference-photo": Plan("show me a real picture of a stoma", [("reference_photo", {"subject": "stoma"})]),
    "close-magnifier": Plan("close that", [("close_magnifier", {})], ("outline", "magnifier")),
    "count": Plan("how many stomata are there?", [("count", {"label": "stoma"})]),
    "measure": Plan("how big is that?", [("measure", {"object": O1})], ("outline", "objective")),
    "query-population": Plan("which cell is the most elongated?", [("query_population", {"by": "elongation", "top": 1})], ("cells",)),
    "group-cells": Plan("group these cells by how they look", [("group_cells", {"groups": 2})], ("cells",)),
    # Grouped and asked in one request: a grouping made with another picture model is refused ("group them again").
    "describe-groups": Plan("group these blood cells by look and say how the groups differ", [
        ("group_cells", {"groups": 2}), ("describe_groups", {"names": True})], ("blood", "cells")),
    "cells-by-look-group": Plan("group them by look: which cells are in group 2?", [
        ("group_cells", {"groups": 2}), ("query_population", {"where": {"group": 2}})], ("cells",)),
    "follow-cells": Plan("keep an eye on the odd ones", [("follow_cells", {"members": ["c1"]})], ("cells",)),
    "individuals": Plan("where are the odd ones now?", [("individuals", {})], ("cells",),
                        before=[("follow_cells", {"members": ["c1"]})]),
    "same-one": Plan("that's the same cell as before", [("same_one", {"a": "o1", "b": "o2"})], ("outline", "outline2")),
    "compare-captures": Plan("how do these cells compare with the earlier ones?", [("query_population", {"of": "e2", "against": "e1"})],
                             ("cells", "cells")),
    "link-captures": Plan("compare them with the earlier cells, linking each cell",
                          [("query_population", {"of": "e2", "against": "e1", "link": True})], ("cells", "cells")),
    "outcomes": Plan("which model did better on this leaf today?", [("outcomes", {})]),
    "objective": Plan("I'm on 40x", [("set_objective", {"objective": 40})]),
    "calibrate": Plan("that stoma is 30 µm long", [("calibrate_scale", {"object": O1, "micrometres": 30})], ("outline", "objective")),
    "track-motion": Plan("are they swimming?", [("track_motion", {"seconds": 2})], ("still",)),
    "keep-trail": Plan("show me where it went", [("track_motion", {"of": O1})], ("outline", "still"), expect="refused: has no trail",
                       not_on_path={"track": "the follower follows on each new frame (objtrack.py), never inside a request, "
                                             "and a trail is kept only after seconds of following; the follow-clicks sim and "
                                             "make capability-check's track check reach EdgeTAM video"}),
    "show-tracks": Plan("show me the tracks from earlier", [("present", {"what": "m1", "do": "show"})], ("still",),
                        before=[("track_motion", {"seconds": 2})], expect=NO_STUDIO),
    "what-happened": Plan("what just happened?", [("what_happened", {"seconds": 3})], ("still",)),
    "watch": Plan("tell me when something appears", [("watch", {"what": "a cell swims in", "by": "look",
                                                                 "question": "Has a cell swum into view?"})]),
    "stop-watching": Plan("stop watching", [("stop_watching", {})], before=[("watch", {"what": "movement", "by": "motion"})]),
    "watching": Plan("what are you watching for?", [("watching", {})], before=[("watch", {"what": "movement", "by": "motion"})]),
    "map-show": Plan("show me the map", [("show_map", {})]),
    "where-is": Plan("where was the stoma?", [("where_is", {"object": O1})], ("outline",)),
    "bookmark": Plan("bookmark this, the nice stomata", [("bookmark", {"note": "the nice stomata"})]),
    "way-to-bookmark": Plan("which way is the stomata I marked?", [("way_to_bookmark", {})],
                            before=[("bookmark", {"note": "the nice stomata"})]),
    "enhance": Plan("clean up the picture", [("enhance", {})], ("still",)),
    "snapshot": Plan("save this view", [("snapshot", {})]),
    "seen-before": Plan("have we seen this before?", [("seen_before", {})], ("outline", "seen")),
    "what-data": Plan("what data do we have?", [("resources", {})]),
    "inspect-camera": Plan("compare this view with the saved picture", [("inspect_camera", {})]),
    "how-known": Plan("how do you know it's a stoma?", [("resources", {"id": O1})], ("outline",)),
    "user-annotation": Plan("keep this as my tentative observation", [("annotate_resource", {"text": "two guard cells"})],
                            expect="refused: retained source"),
    "recall": Plan("what did we say about the stoma?", [("recall", {"query": "stoma"})]),
    "name-specimen": Plan("this is a leaf epidermis", [("name_specimen", {"name": "leaf epidermis"})]),
    "new-thread": Plan("new topic", [("new_thread", {"title": "leaf"})]),
    "switch-thread": Plan("back to the previous one", [("switch_thread", {"thread": "t1"})], before=[("new_thread", {"title": "leaf"})]),
    "new-session": Plan("start a new session", [("new_session", {"name": "kidney section"})]),
    # The library (brain/toolbox/library.py): looking touches no session; putting one on stage is the `source` command,
    # here refused for an asset the stand-in world has none of, saying why.
    "find-assets": Plan("which recordings of rotifers do I have?", [("assets", {"words": "rotifer"})]),
    # No volume in the stand-in world: the live view is a 2D picture, never registered (test_registrations runs one).
    "register-volume-agent": Plan("register this brain to the Allen atlas", [("register_volume", {})],
                                  expect="refused: there is no volume in this session to register"),
    "add-asset": Plan("show me that rotifer recording", [("add_asset", {"asset": "a999999"})],
                      expect="refused: there is no asset a999999"),
    "switch-session": Plan("go back to the previous session", [("switch_session", {"session": "previous"})],
                           before=[("new_session", {"name": "kidney section"})]),
    "you-lead": Plan("let me find it myself", [("set_lead", {"user_leads": True})]),
    "level": Plan("explain it more simply", [("set_level", {"level": "simple"})]),
    "suggest-next": Plan("what should I look at next?", [("suggest_next", {"text": "look at the guard cells next"})]),
    "start-researcher": Plan("look into why stomata close", [("start_researcher", {
        "brief": "why stomata close", "effort": "quick", "asked_with": "look into why stomata close"})]),
    "schedule-acquisition": Plan("capture ten five-second clips every thirty seconds", [("schedule_acquisition", {
        "researcher": "r2", "count": 10, "every_seconds": 30, "clip_seconds": 5,
        "asked_with": "capture ten five-second clips every thirty seconds"})], ("working",)),
    "research-status": Plan("how is the researcher doing?", [("research_status", {})], ("lesson",)),
    "summarize-research": Plan("what have they found?", [("summarize_research", {"label": "guard cells"})], ("lesson",)),
    "cancel-research": Plan("stop the researcher", [("cancel_researcher", {"researcher": "r2"})], ("working",)),
    "followup": Plan("ask the researcher why", [("researcher_followup", {"researcher": "r1", "question": "why do they close?"})],
                     ("lesson",), not_on_path={"research": ENGINE_KEPT}),
    "revise": Plan("make question 2 easier", [("revise_work", {"researcher": "r1", "change": "make question 2 easier",
                                                               "asked_with": "make question 2 easier"})], ("lesson",),
                   not_on_path={"research": ENGINE_KEPT}),
    "note": Plan("tell it to focus on the guard cells", [("researcher_note", {"researcher": "r2", "text": "focus on the guard cells"})],
                 ("working",)),
    "hold-footage": Plan("yes, keep 15 seconds for it", [("researcher_note", {"researcher": "r2", "text": "yes", "answers": "q1",
                                                                             "hold_seconds": 15, "asked_with": "yes"})],
                         ("working", "asked")),
    "research-cap": Plan("cap research at two dollars", [("set_research_cap", {"dollars": "2", "asked_with": "cap research at two dollars"})]),
    "show-result": Plan("show me the lesson", [("show_result", {"researcher": "r1"})], ("lesson",)),
    "read-result": Plan("what does the lesson say?", [("read_result", {"researcher": "r1"})], ("lesson",)),
    "step": Plan("next question", [("result_step", {"researcher": "r1", "to": "next"})], ("lesson",)),
    "mark-step": Plan("that's right", [("mark_step", {"researcher": "r1", "step": "q1", "outcome": "right"})], ("lesson",)),
    "close-result": Plan("close the lesson", [("close_result", {})], ("lesson",), before=[("show_result", {"researcher": "r1"})]),
    "show-me": Plan("show me what it found", [("show_me", {"researcher": "r1"})], ("lesson",)),
    "stop-showing": Plan("stop", [("show_me", {"stop": True})], ("lesson",)),
    "style": Plan("make the outlines yellow", [("set_style", {"color": "yellow"})]),
    # What is on screen changes only once a studio reports it (screen.py); none reports here, which is said.
    "change-on-screen": Plan("colour the cells by size", [("present", {"what": "e1", "do": "colour", "column": "areaPx"})], ("cells",),
                             expect=NO_STUDIO),
    "colour-by-look-group": Plan("group them by look and colour the cells by group", [
        ("group_cells", {"groups": 2}), ("present", {"what": "e1", "do": "colour", "column": "group"})], ("cells",), expect=NO_STUDIO),
    "look-at-screen": Plan("is the video still showing?", [("look_at_screen", {"question": "is the video showing?"})],
                           expect="refused: no studio is showing the view",
                           not_on_path={"vision": "it looks at the studio's own picture of the screen (screen-image), which "
                                                  "only a studio sends; test_look_at_screen reaches the model"}),
    "read-capabilities": Plan("what can you do?", [("capabilities", {})]),
    # The Models page in words (brain/toolbox/catalog.py): in this rig every model on this Mac counts as downloaded, so
    # nothing is offered and the yes is refused saying so; no studio answers open-page, so the card is refused too.
    "ask-about-models": Plan("what do I need to set up?", [("models", {})]),
    "models-in-use": Plan("what models are powering you right now?", [("models_in_use", {})]),
    "get-model": Plan("yes, download it", [("get_model", {"model": "all", "asked_with": "yes, download it"})],
                      before=[("models", {})], expect="refused: not offered just now"),
    "open-model-card": Plan("add my OpenAI key", [("open_models", {"model": "OpenAI key"})],
                            expect="refused: did not say it shows"),
    # One press of a job's Turn on, by the agent on the user's quoted yes (capability_shelf.py): in this rig the job is
    # on already (every model counts as downloaded), and it says so.
    # A new kind of job the catalog added (kinds.py): a stand-in one on, run on the view (capability_rig.turn_on_kind).
    "run-new-kind": Plan("describe the view with the describer I added", [("run_capability", {"capability": "stand-in-describer",
                                                                                           "by": "user"})], setup=("kind",)),
    "describe-capability": Plan("what can the describer I added be set to?",
                                [("describe_capability", {"capability": "stand-in-describer"})], setup=("kind",)),
    "turn-on-capability-agent": Plan("turn on Find every cell", [("turn_on", {"job": "Find every cell",
                                                                               "asked_with": "turn on Find every cell"})]),
    "show-measurements": Plan("show me the cell measurements as a table", [("show_measurements", {})], setup=("cells",),
                              expect="refused: did not say it shows"),
    "which-models": Plan("which models can do this?", [("capabilities", {"operation": "{use_as}"})], names_model=True, reach="listed"),
    "use-for-request": Plan("use SAM 2.1 for this one", [("use", {"operation": "outline", "implementation": "SAM 2.1 tiny",
                                                                  "by": "user"})]),
    "set-default-model": Plan("always use this one from now on", [("set_default", {"operation": "{use_as}", "implementation": "{name}",
                                                                                     "by": "user"})], names_model=True, reach="set"),
    "retrieve-page": Plan("pull up a page about stomata", [("retrieve_page", {"query": "stomata"})]),
    # Researchers' work: asked for by chat and voice (start_researcher), and a researcher's own operations by its `run`.
    "research": Plan("look into why stomata close", [("start_researcher", {"brief": "why stomata close", "effort": "quick",
                                                                           "asked_with": "look into why stomata close"})]),
    "report": Plan("write me a report on this", [("start_researcher", {"brief": "a report on this leaf", "effort": "standard",
                                                                       "asked_with": "write me a report on this"})]),
    "annotated": Plan("label the parts on a picture", [("start_researcher", {"brief": "label the parts", "effort": "standard",
                                                                             "deliverable": "annotated_image", "object": O1,
                                                                             "asked_with": "label the parts on a picture"})], ("outline",)),
    "illustration": Plan("draw how it works", [("start_researcher", {"brief": "how a stoma opens", "effort": "standard",
                                                                     "deliverable": "image", "asked_with": "draw how it works"})]),
    "video": Plan("make a video of a stoma opening", [("start_researcher", {"brief": "a stoma opening", "effort": "standard",
                                                                            "deliverable": "video",
                                                                            "asked_with": "make a video of a stoma opening"})]),
    "lesson": Plan(ASK_LESSON, [("start_researcher", {"brief": "a lesson on stomata", "effort": "standard",
                                                      "deliverable": "lesson", "asked_with": ASK_LESSON})]),
    "research-run": Plan("have a researcher measure how much these cells vary", [("start_researcher", {
        "brief": "measure how much these cells vary", "effort": "quick",
        "asked_with": "have a researcher measure how much these cells vary"})],
        not_on_path={"chat:cells": RUN_CHOOSES, "voice:cells": RUN_CHOOSES, "researcher:research": RUN_ENGINE}),
    "research-enhance": Plan("have a researcher take the clearest picture", [("start_researcher", {
        "brief": "take the clearest picture", "effort": "quick", "asked_with": "have a researcher take the clearest picture"})],
        not_on_path={"researcher:research": RUN_ENGINE}),
    "research-motion": Plan("have a researcher measure how fast these swim", [("start_researcher", {
        "brief": "measure how fast these swim", "effort": "quick",
        "asked_with": "have a researcher measure how fast these swim"})], not_on_path={"researcher:research": RUN_ENGINE}),
    # A chart or an animation of a kept table: a research researcher's own tools (measurement_charts.py), never a video.
    "research-chart": Plan("have a researcher chart the nucleus areas", [("start_researcher", {
        "brief": "chart the nucleus areas", "effort": "quick", "asked_with": "have a researcher chart the nucleus areas"})]),
    "research-animation": Plan("have a researcher animate the measurements table", [("start_researcher", {
        "brief": "animate the measurements table", "effort": "quick",
        "asked_with": "have a researcher animate the measurements table"})]),
}


def askers(cap: dict) -> tuple[str, ...]:
    """Who of chat, the voice and a researcher can ask for a capability: a tool's by chat and the voice; a researcher's
    deliverable by chat and the voice (start_researcher), and a researcher's own operation by a researcher too (`run`).
    What only the user does in the studio, or the app does on its own, is asked by none of them."""
    if cap["actor"] == "agent":
        return ("chat", "voice")
    if cap["actor"] == "researcher":
        return ("chat", "voice", "researcher") if cap["id"] in RUNS else ("chat", "voice")
    return ()


def model_roles(cap: dict) -> list[str]:
    """A capability's roles that choose a model: model roles, fixed models, and methods whose choice runs one."""
    have = {e.role for e in model_checks.entries()}
    return [r for r in cap.get("roles", []) if r in have]


@dataclass(frozen=True)
class Row:
    capability: str
    actor: str
    role: str | None = None
    option: str | None = None

    def __str__(self) -> str:
        return f"{self.capability} × {self.actor}" + (f" × {self.role}:{self.option}" if self.role else "")


def table() -> list[Row]:
    """Every capability × actor that can ask for it × each model option of its roles (one row when it has none)."""
    rows = []
    for cap in capabilities.load()["capabilities"]:
        for actor in askers(cap):
            roles = model_roles(cap)
            options = [(e.role, e.option) for r in roles for e in model_checks.entries() if e.role == r]
            rows += [Row(cap["id"], actor, role, option) for role, option in options] or [Row(cap["id"], actor)]
    return rows


def groups() -> list[tuple[str, str]]:
    """Each capability × actor once, in the table's order: the same on every run and every pytest-xdist worker (a set's
    order changes with the hash seed, and xdist refuses workers that collect in different orders)."""
    return list(dict.fromkeys((r.capability, r.actor) for r in table()))


# --- the coverage, before anything runs -------------------------------------------------------------------------------
def test_every_capability_and_actor_has_a_planned_row():
    """Q13: a capability that chat, the voice or a researcher can ask for, with no plan here, fails; so does a plan for
    a capability the list no longer has. Every row's model name resolves to its option through names.py."""
    cat = {c["id"]: c for c in capabilities.load()["capabilities"]}
    asked = {c for c, actor in groups()}
    assert sorted(asked - set(PLANS)) == [], "no planned row (add a Plan)"
    assert sorted(set(PLANS) - asked) == [], "a plan for no capability chat, the voice or a researcher can ask for"
    for cid in asked:
        tools = {tool for tool, _ in PLANS[cid].steps}
        if "tool" in cat[cid]["how"]:
            assert cat[cid]["how"]["tool"] in tools, f"{cid}: its plan does not run its own tool"
    for row in table():
        if row.role is None or row.role.startswith("fixed:") or row.role in BEHAVIOUR:
            continue
        entry = model_checks.find(row.role, row.option)
        space = USE_AS.get(row.role, f"option:{row.role}")
        meant = names.resolve(space, entry.name, choosable_only=False) if not space.startswith("option:") else names.option(row.role, entry.name)
        assert meant is not None and meant.id in (row.option, entry.probe[1]), (str(row), entry.name)


# --- running the rows ---------------------------------------------------------------------------------------------------
@pytest.fixture
def world(tmp_path, monkeypatch):
    """Keys present, every model's code installed, the cloud stood in for: so a row's run is the wiring, and a refusal
    a row expects is made on purpose."""
    from orchestrator import fal_segment, operations

    for key in ("GEMINI_API_KEY", "OPENAI_API_KEY", "FAL_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.setenv(key, "stand-in")
    monkeypatch.delenv("MICROMIND_EDITION", raising=False)
    for name in list(operations.INSTALLED):
        monkeypatch.setitem(operations.INSTALLED, name, lambda: "")
    fal: list[str] = []

    def mask(box):
        out = np.zeros((720, 1280), bool)
        x0, y0, x1, y1 = (int(v) for v in box)
        out[y0:y1, x0:x1] = True
        return out

    async def segment_box(jpeg, box, width, height, label=""):
        fal.append("segment_box")
        return {"mask": mask(box), "score": 0.9, "ms": 1.0}

    async def segment_all(jpeg, width, height, box=None, text=""):
        fal.append("segment_all")
        return [{"mask": mask(STOMA_BOX), "score": 0.9}, {"mask": mask((600, 300, 660, 350)), "score": 0.8}]

    monkeypatch.setattr(fal_segment, "segment_box", segment_box)
    monkeypatch.setattr(fal_segment, "segment_all", segment_all)
    from research_worker import retrieval, safe_fetch

    looked: list[str] = []

    async def lookup(query, *, images=False, observe=None):
        looked.append(query)
        return [{"page": "https://en.wikipedia.org/wiki/Stoma", "title": "Stoma", "image": "https://upload.wikimedia.org/x.jpg"}]

    async def fetch_text(url, *a, **k):
        return "Stomata are pores in the epidermis of leaves."

    async def fetch_image(url, client=None):
        from tests.capability_rig import leaf

        return safe_fetch.FetchedImage(leaf(), 1280, 720, url, "https://en.wikipedia.org/wiki/Stoma", "Stoma", "CC BY-SA 4.0",
                                       "A. Botanist")

    async def matches_photo(query, jpeg):
        return True

    async def page_images(url, client=None):
        return []

    catalogued: list[str] = []

    async def commons_search(query, limit=3, client=None):  # option REFERENCE_SEARCH "web+commons": the catalogue
        catalogued.append(query)
        return []  # nothing there: the web's photo is the one shown, the same for both choices

    monkeypatch.setattr(retrieval, "lookup", lookup)
    monkeypatch.setattr(retrieval, "matches_photo", matches_photo)
    monkeypatch.setattr(safe_fetch, "fetch_text", fetch_text)
    monkeypatch.setattr(safe_fetch, "fetch_image", fetch_image)
    monkeypatch.setattr(safe_fetch, "page_images", page_images)
    monkeypatch.setattr(safe_fetch, "commons_search", commons_search)
    monkeypatch.setattr(safe_fetch, "check_url", lambda url: None)
    # Trackastra (services/noncommercial, reached through licences.adapter): its link of two views, stood in for.
    from noncommercial import trackastra_link

    linked: list[int] = []

    def link(views, labels):
        linked.append(len(views))
        return [(0, 1, 1, 1, 0.9), (0, 2, 1, 2, 0.8)]

    monkeypatch.setattr(trackastra_link, "link", link)
    monkeypatch.setattr(trackastra_link, "installed_version", lambda: "0.5.6")
    return {"root": tmp_path, "fal": fal, "looked": looked, "linked": linked, "catalogued": catalogued}


def run_row_group(world, capability: str, actor: str) -> list[str]:
    """Every row of one capability × actor: [] when each reached what it named, else a line per row that did not."""
    from tests.capability_rig import Rig

    rows = [r for r in table() if (r.capability, r.actor) == (capability, actor)]
    problems = []
    for n, row in enumerate(rows):
        rig = Rig(world["root"] / f"{n}")
        try:
            problem = asyncio.run(run_row(rig, world, row))
        except Exception as exc:  # noqa: BLE001 - a crash is the row's finding
            problem = f"crashed: {type(exc).__name__}: {exc}"
        if problem:
            problems.append(f"{row}: {problem}")
    return problems


def why_unavailable(row: Row) -> str:
    """Why the row's model can't run in this world (keys present, code installed): what its refusal must say, or ""
    (Cellpose-SAM fine-tuned has no weights until make finetune-cells makes them)."""
    import os

    if row.role is None:
        return ""
    return model_checks.before(model_checks.find(row.role, row.option), environ=os.environ, download=True, video=True)[1]


def other(row: Row) -> str:
    """Another model of the row's role that runs here, for a comparison of two."""
    for e in model_checks.entries():
        if e.role == row.role and e.option != row.option and not why_unavailable(Row(row.capability, row.actor, e.role, e.option)):
            return e.name
    return ""


def declared(plan: Plan, row: Row) -> str:
    """Why this path does not reach the row's role ("" when it must)."""
    return plan.not_on_path.get(f"{row.actor}:{row.role}") or plan.not_on_path.get(row.role or "") or ""


async def run_row(rig, world, row: Row) -> str:
    from tests import capability_rig as setups

    plan = PLANS[row.capability]
    try:
        return await _run_row(rig, world, row, plan, setups)
    finally:
        if "kind" in plan.setup:
            setups.turn_off_kind(rig)  # its operation and tool are the process's: never left for later rows


async def _run_row(rig, world, row: Row, plan, setups) -> str:
    await setups.prepare(rig, plan.setup + (("still",) if row.actor == "researcher" else ()))
    for step in plan.before:
        await rig.turn([(step[0], rig.fill(step[1]))], plan.words)  # an earlier request of the user's (chat)
    choice: list = []
    entry = model_checks.find(row.role, row.option) if row.role is not None else None
    places = {}
    if entry is not None:
        places = {"{name}": entry.name, "{use_as}": USE_AS.get(row.role, row.role), "{other}": other(row)}
        if plan.names_model or row.actor == "researcher" or declared(plan, row):
            pass  # the steps (or the researcher's `run`) name it; or this path does not reach it (declared)
        elif row.role in USE_AS or names.SETTINGS.get(row.role):
            if row.role == "fast":  # the fast pass plans before any step of its request: chosen from now on
                await rig.turn([("set_default", {"operation": "fast", "implementation": entry.name, "by": "user"})],
                               "use it from now on")
            else:
                choice = [("use", {"operation": USE_AS.get(row.role, row.role), "implementation": entry.name, "by": "user"})]
        elif row.role in BEHAVIOUR:
            problems = rig.hub.settings.update({row.role: row.option})  # as the Behaviour menu changes it
            if problems:
                return f"the setting refused: {problems}"
    since = rig.mark_world(rig.mark(), world)
    steps = choice + [(tool, rig.fill(args, places)) for tool, args in plan.steps]
    if row.actor == "chat":
        runs = await rig.turn(steps, plan.words)
    elif row.actor == "voice":
        runs, _found = await rig.voice(steps, plan.words)
    else:
        reply = await rig.researcher(RUNS[row.capability], implementation=entry.name if row.role == "cells" else None)
        return rig.judge_run(reply, row, since, world, why_unavailable(row) if not declared(plan, row) else "")
    return rig.judge(runs, row, plan, since, world, why_unavailable(row), declared(plan, row))


# --- what can't run is refused, saying why ----------------------------------------------------------------------------
# A step of each role's operation after its choice: it must not run with another model in the choice's place.
AFTER = {"outline": ("find", {"what": "stoma"}), "cells": ("outline_cells", {}), "embed": ("group_cells", {"groups": 2})}


def refusals(actor: str) -> list[tuple]:
    """(role, option, reason, words) for every model a request can name that this world can make unable to run: no key,
    its code not installed, not in this edition (and for a researcher, the cell finders); then each operation done one
    way. `words`: what the refusal must say."""
    from orchestrator import licences
    from orchestrator.operations import OPERATIONS

    out = []
    for e in model_checks.entries():
        if actor == "researcher" and e.role != "cells":
            continue
        if actor != "researcher" and not (e.role in USE_AS or names.SETTINGS.get(e.role)):
            continue
        if e.needs:
            out.append((e.role, e.option, "key", f"needs {e.needs}"))
        if e.installed:
            out.append((e.role, e.option, "installed", "not installed here"))
        if licences.tier(e.uses) == "noncommercial":
            out.append((e.role, e.option, "edition", "not in the commercial edition"))
    for op in OPERATIONS.values():
        if names.one_way(op) and (actor != "researcher" or op.id == "motion"):
            out.append((op.id, op.implementations[0].id, "one-way", "done one way"))
    return out


async def refuse(rig, actor: str, role: str, option: str, reason: str, words: str) -> str:
    """"" when the choice was refused saying `words`, and nothing else ran in its place."""
    if reason == "one-way":
        if actor == "researcher":
            reply = await rig.researcher(role, implementation=option)
            return "" if not reply.get("ok") and words in reply.get("error", "") else f"the run said {reply}"
        step = ("use", {"operation": role, "implementation": option, "by": "user"})
        after = None
    else:
        entry = model_checks.find(role, option)
        if actor == "researcher":
            reply = await rig.researcher("segment", implementation=entry.name)
            return "" if not reply.get("ok") and words in reply.get("error", "") else f"the run said {reply}"
        step = ("use", {"operation": USE_AS.get(role, role), "implementation": entry.name, "by": "user"})
        after = AFTER.get(role)
    since = rig.mark()
    plan = [step] + ([after] if after else [])
    runs = await (rig.turn(plan, "use that one") if actor == "chat" else rig.voice(plan, "use that one"))
    runs = runs if actor == "chat" else runs[0]
    chose = next((r for r in runs if r.tool == "use"), None)
    if chose is None or chose.ok is not False or words not in str(chose.summary):
        return f"the choice was not refused saying {words!r}: {[(r.tool, r.ok, str(r.summary)[:150]) for r in runs]}"
    ran = [(o, p.get("model")) for _w, o, p in rig.calls_since(since) if o in ("segment", "cells", "automask", "embed")]
    if after is not None and ran:
        return f"something else ran in its place: {ran}"
    return ""


@pytest.mark.parametrize("actor", ACTORS)
def test_a_choice_that_cannot_run_is_refused_saying_why(world, monkeypatch, actor):
    """A model a request names that can't run here is refused saying why (needs a key, not installed here, not in this
    edition, done one way), and the request does not do its work with another model instead (ADR 0011)."""
    from orchestrator import operations
    from tests.capability_rig import Rig, prepare

    t0 = time.perf_counter()
    problems = []
    for n, (role, option, reason, words) in enumerate(refusals(actor)):
        entry = model_checks.find(role, option) if reason != "one-way" else None
        with monkeypatch.context() as m:
            if reason == "key":
                m.delenv(entry.needs)
            elif reason == "installed":
                m.setitem(operations.INSTALLED, entry.installed, lambda: "not installed here (as the check says)")
            elif reason == "edition":
                m.setenv("MICROMIND_EDITION", "commercial")
            rig = Rig(world["root"] / f"refuse-{actor}-{n}")

            async def go():
                await prepare(rig, ("cells",) if role == "embed" else ("still",) if actor == "researcher" else ())
                return await refuse(rig, actor, role, option, reason, words)

            try:
                problem = asyncio.run(go())
            except Exception as exc:  # noqa: BLE001 - a crash is the row's finding
                problem = f"crashed: {type(exc).__name__}: {exc}"
        if problem:
            problems.append(f"{actor} × {role}:{option} ({reason}): {problem}")
    _check_time(f"refusals:{actor}", t0)
    assert refusals(actor) and not problems, "\n".join(problems)


def _check_time(name: str, t0: float) -> None:
    _spent[name] = time.perf_counter() - t0


@pytest.mark.parametrize("capability,actor", groups(), ids=lambda v: v)
def test_every_capability_reaches_what_it_names(world, capability, actor):
    t0 = time.perf_counter()
    problems = run_row_group(world, capability, actor)
    _check_time(f"{capability}:{actor}", t0)
    assert not problems, "\n".join(problems)


def half_of(name: str) -> int:
    """Which half of the matrix a timed part is in, by its capability's family: the plan's budget is 90 s, split by family
    past that, never trimmed (ported from the parallel one-door branch, #316, where its describe-capability rows took the
    matrix past 90 s). The families, in the capability list's order, are cut in two; the refusals are timed with the
    second half."""
    caps = capabilities.load()["capabilities"]
    families = list(dict.fromkeys(c.get("family", "") for c in caps))
    first = set(families[: (len(families) + 1) // 2])
    capability = name.split(":", 1)[0]
    family = next((c.get("family", "") for c in caps if c["id"] == capability), None)
    return 0 if family in first else 1


@pytest.mark.parametrize("half", (0, 1))
def test_the_matrix_fits_in_its_time(half, wall_clock):
    """Each half of it, split by family, takes at most 90 s (the plan's budget; split it by family past that, never trim
    rows). A wall-clock bound: make test-timing checks it."""
    if len(_spent) < len(groups()) + len(ACTORS):
        pytest.skip("runs only after the whole matrix")
    mine = {k: v for k, v in _spent.items() if half_of(k) == half}
    assert mine, f"half {half} timed nothing"
    wall_clock.within(sum(mine.values()), BUDGET_S, f"half {half} (the slowest: " + ", ".join(
        f"{k} {v:.1f}s" for k, v in sorted(mine.items(), key=lambda kv: -kv[1])[:5]) + ")")
