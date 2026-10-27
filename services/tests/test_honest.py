"""The app never claims what did not happen, and pointing at things works (live session
2026-10-04: "Here it is highlighted" over an empty view; a refused researcher reported as
started; large tissue impossible to outline)."""
import asyncio
import json
from functools import partial
from types import SimpleNamespace

import pytest

from gpu_worker.worker import area_limit
from orchestrator import tools
from orchestrator.brain.actions import outline_seen
from orchestrator.brain.loop import Brain
from orchestrator.brain.registry import Tool, ToolContext, ToolFailed, schema
from orchestrator.brain.state import Conversation
from tests.fakes import conversation as day_conversation
from orchestrator.brain.toolbox import Services, build
from orchestrator.scene import SceneObject
from orchestrator.sight import Seen, SightView
from tests.test_brain import FakeAgent, FakeHub, make, obj
from tests.test_population import world, cells  # noqa: F401 (real population fixture)
from tests.waiting import HANG_S


def rewriter(answer):
    """A fast model that rewrites the answer (Brain._reconcile)."""
    prompts = []

    async def ask(images, prompt, max_output_tokens=0):
        prompts.append(prompt)
        return SimpleNamespace(data={"answer": answer}, latency_ms=5.0)

    return (lambda _m: SimpleNamespace(ask=ask)), prompts


def supplied_facts(prompt):
    """Inspect the actual bounded facts packet sent to the synthetic provider."""
    header = '<answer-facts source="app" untrusted="true">\n'
    assert header in prompt
    return json.loads(prompt.split(header, 1)[1].split('\n</answer-facts>', 1)[0])


@pytest.mark.parametrize("removed, expected", [(0, None), (2, "4 after your corrections (revision 1)"),
                                               (6, "0 after your corrections (revision 1)")])
def test_status_reconciliation_keeps_the_retained_measurement_context(world, tmp_path, removed, expected):
    """A cancellation rewrite once changed a correct count after losing the population context."""
    from orchestrator.research import ResearchManager, Session

    population, _ = cells(world)
    conversation = day_conversation()
    agent = FakeAgent(world, conversation, [{"answer": "Stopping the researcher. There are 6 retained instances.",
                                           "actions": [{"tool": "cancel_researcher", "args": {}}]}])
    research = ResearchManager(world, root=tmp_path, today=lambda: "2026-10-08")
    sibling = Session(rid="r2", n=2, brief="Explain the measurements", deliverable="research", effort="quick",
                      engine="gpt-6.1-sol", purpose="research", label="beginner", object_id=None, origin={},
                      day="2026-10-08", sitting="2026-10-08", status="running")
    research.sessions[sibling.rid] = sibling
    services = Services(hub=world, agent=agent, research=research)
    registry = build(services)

    async def stopped(ctx, args):
        sibling.status = "cancelled"
        if removed:
            from orchestrator.population import correct

            correct(world, population, "remove", list(population.members)[:removed], by="user")
        world.frame(0, 0, epoch=4)  # a new field does not change the saved population
        return "stopped the researcher"

    registry.add(Tool("cancel_researcher", "", schema({}), stopped))
    brain = Brain(world, agent, registry, conversation)
    brain.services = services
    agent.vision, prompts = rewriter("Stopped the researcher; the retained set has 6 instances.")
    asyncio.run(brain.turn("Stop the researcher and give the retained count after moving the view"))
    current_facts = prompts[-1].split("Current app and workspace facts (existing state is included):", 1)[1]
    assert population.id in current_facts
    assert "6 instances segmented by cellpose-sam" in current_facts
    assert "view at" in current_facts and "cancelled" in current_facts
    if expected:
        assert expected in current_facts  # a changed revision must not be frozen at the earlier count


def test_a_failed_tool_is_never_claimed_and_is_shown():
    async def broken(ctx, args):
        raise ToolFailed("a lesson researcher only starts when the user asks for one explicitly")

    async def fine(ctx, args):
        return "comparing the fiber cap and the xylem side by side"

    brain, _, agent = make([{"answer": "I've set a researcher to prepare a presentation.", "targets": [], "actions": [
        {"tool": "compare", "args": {}}, {"tool": "start_researcher", "args": {}}]}],
        [Tool("compare", "", schema({}), fine), Tool("start_researcher", "", schema({}), broken)])
    agent.vision, prompts = rewriter("They're side by side now; the researcher didn't start, since it needs you to ask for a lesson.")
    result = asyncio.run(brain.turn("make a little presentation"))
    assert "didn't start" in result["answer"]
    assert "I've set a researcher" in prompts[0] and "only starts when" in prompts[0]
    # The refusal is shown even though compare worked (it used to be filtered out).
    assert [(a["tool"], a["ok"]) for a in result["actions"]] == [("compare", True), ("start_researcher", False)]
    logged = agent.logged[-1]
    assert logged["actions"][1]["args"] == {} and logged["fastUsed"] is False


@pytest.mark.parametrize("tool_fails", [True, False])
@pytest.mark.parametrize("rewrite_fails", [True, False])
def test_reconciliation_reads_current_research_status_after_actions(tmp_path, tool_fails, rewrite_fails):
    from orchestrator.research import ResearchManager, Session

    async def stopped(ctx, args):
        sibling.status = "timeout"  # its independent work ends while this action runs
        if tool_fails:
            raise ToolFailed("the comparison researcher is no longer running")
        return "stopped the comparison researcher"

    async def slow(ctx, args):
        await asyncio.Event().wait()  # held beyond the reply; the test loop cancels it on exit
        return "finished the picture"

    brain, hub, agent = make([{"answer": "The beginner researcher is still running.", "targets": [],
                              "actions": [{"tool": "cancel_researcher", "args": {}}, {"tool": "enhance", "args": {}}]}],
                            [Tool("cancel_researcher", "", schema({}), stopped),
                             Tool("enhance", "", schema({}), slow, inline_ms=20)])
    # This tests immediate correction with one spare answer round. The synthetic
    # rewriter is not a fast planner; an extra fast attempt would leave only the
    # reserved late-outcome slot (covered by test_pending_effect_final_answer).
    hub.settings.fast = "off"
    research = ResearchManager(hub, root=tmp_path, today=lambda: "2026-10-08")
    sibling = Session(rid="r2", n=2, brief="Explain the measurements", deliverable="research", effort="quick",
                      engine="gpt-6.1-sol", purpose="research", label="beginner", object_id=None, origin={},
                      day="2026-10-08", sitting="2026-10-08", status="running")
    research.sessions[sibling.rid] = sibling
    brain.services = Services(hub=hub, agent=agent, research=research)
    agent.vision, prompts = rewriter("" if rewrite_fails else "Both researchers have stopped.")
    result = asyncio.run(brain.turn("Stop the comparison one and keep the other going"))
    if rewrite_fails:
        assert result["answer"] == "" and result["answerStatus"] == "not-published"
    else:
        assert result["answer"] == "Both researchers have stopped."
    assert [(r["tool"], r["ok"]) for r in result["actions"]] == [
        ("cancel_researcher", not tool_fails), ("enhance", None)]
    facts = supplied_facts(prompts[-1])
    assert [(r["tool"], r["ok"]) for r in facts["operations"]] == [
        ("cancel_researcher", not tool_fails), ("enhance", None)]
    assert ("no longer running" if tool_fails else "stopped the comparison") in facts["operations"][0]["summary"]
    assert "Researcher 2" in prompts[-1] and "timeout" in prompts[-1]
    assert '<researchers source="web" untrusted="true">' in prompts[-1]


def test_nothing_outlined_is_not_claimed_and_the_reason_is_kept():
    brain, _, agent = make([{"answer": "Here is the pink-stained tissue outlined.",
                             "targets": [{"box_2d": [100, 100, 900, 900], "label": "xylem"}], "actions": []}])

    async def none_outlined(targets, frame_id, crop, marks=None, model_name="", errors=None):
        errors.append("xylem: nothing distinct to outline there")
        return []

    agent.point = none_outlined
    result = asyncio.run(brain.turn("outline the pink"))  # unavailable model cannot become app-written prose
    assert result["answer"] == "" and result["answerStatus"] == "not-published"
    assert result["objectIds"] == []
    rewrites = [p for p in agent.fast_model.prompts if "only your notes" not in p]
    assert len(rewrites) == 1 and "nothing distinct to outline there" in rewrites[0]
    assert supplied_facts(rewrites[0])["targets"] == {"requested": 1, "outlined": 0}
    assert agent.logged[-1]["outlineErrors"] == ["xylem: nothing distinct to outline there"]


def test_an_answer_that_claims_nothing_keeps_its_content():
    async def broken(ctx, args):
        raise ToolFailed("the magnifier is closed")

    brain, _, agent = make([{"answer": "That is a stoma.", "targets": [], "actions": [{"tool": "hold", "args": {}}]}],
                       [Tool("hold", "", schema({}), broken)])
    answer = "That is a stoma. The magnifier is closed, so I could not hold it."
    agent.vision, prompts = rewriter(answer)
    result = asyncio.run(brain.turn("what is it, and hold it"))
    assert result["answer"] == answer and result["answerStatus"] == "model"
    assert [(r["tool"], r["ok"]) for r in result["actions"]] == [("hold", False)]
    assert supplied_facts(prompts[-1])["operations"][0]["summary"] == "the magnifier is closed"


def run_tool(brain, name, args, question, *, seen_view=None):
    async def go():
        run = brain.registry.start(name, args, ToolContext("chat", 1, 7, question, seen_view=seen_view))
        await asyncio.wait_for(run.task, HANG_S)
        return run

    return asyncio.run(go())


def seen_brain(fast_reply, vision_reply):
    hub = FakeHub()
    hub.settings.fast = "gemini-3.8-flash"
    hub.infos = {7: hub.latest_info}
    conversation = day_conversation()
    agent = FakeAgent(hub, conversation, [vision_reply])

    async def ask(images, prompt, max_output_tokens=0):
        return SimpleNamespace(data=fast_reply, latency_ms=5.0)

    agent.vision = lambda _m: SimpleNamespace(ask=ask)
    sight = SimpleNamespace(view=SightView(7, 0, 0.0, 0.0, 300.0, "Stem section.", "stem",
                                           [Seen("s1", "xylem", "centre", (600, 340, 680, 400), 0.8)], "sharp"))
    services = Services(hub=hub, agent=agent, magnifier=SimpleNamespace(open=False, follow=lambda _id: None), sight=sight,
                        outline_seen=partial(outline_seen, hub, lambda: sight.view))
    brain = Brain(hub, agent, build(services), conversation)
    brain.services, brain.sight = services, sight
    return brain, hub, agent


def test_a_highlight_that_misses_looks_again_without_repeating_what_worked():
    brain, hub, agent = seen_brain(
        {"needs_look": False, "answer": "Clearing those and outlining the parenchyma.",
         "actions": [{"tool": "clear", "args": {}}, {"tool": "highlight", "args": {"things": ["s9"]}}]},
        {"answer": "Those big cells at the top are pith parenchyma.", "targets": [{"box_2d": [1, 2, 3, 4], "label": "pith"}],
         "actions": [{"tool": "clear", "args": {}}]})
    obj(hub, "phloem")
    result = asyncio.run(brain.turn("un-highlight that and highlight the stuff at the top"))
    assert not result["fast"] and len(agent.calls) == 1  # the look answered
    assert [(a["tool"], a["ok"]) for a in result["actions"]] == [("highlight", False), ("clear", True)]
    assert result["actions"][0]["summary"] == "s9 is not something I saw in my last look"
    assert len({(a["requestId"], a["actionId"]) for a in result["actions"]}) == 2
    assert [hub.scene.objects[i].label for i in result["objectIds"]] == ["pith"]
    assert agent.logged[-1]["retried"][0]["summary"] == "s9 is not something I saw in my last look"


def test_highlight_replaces_the_apps_earlier_highlights_unless_also(monkeypatch):
    brain, hub, _ = seen_brain({}, {})

    async def fake_outline(hub_, frame_id, x, y, **kw):
        return hub_.scene.add(SceneObject(id=hub_.scene.new_id(), kind="highlight", polygons=[[[x, y], [x + 5, y], [x, y + 5]]],
                                          anchor_frame=frame_id, anchor_epoch=0, anchor_offset=(0.0, 0.0),
                                          source=kw["source"], label=kw["label"]))

    monkeypatch.setattr(tools, "outline_at", fake_outline)
    earlier = obj(hub, "phloem", x=10.0)
    clicked = hub.scene.add(SceneObject(id="c1", kind="highlight", polygons=[[[900, 600], [910, 600], [900, 610]]],
                                        anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click", label="mine"))
    original_view = brain.sight.view

    def highlight(question, keep=False):
        return run_tool(brain, "highlight", {"things": ["s1"], "keep": keep}, question, seen_view=original_view)

    run = highlight("also highlight the xylem", keep=True)  # the model says keep: no keyword decides it
    assert run.ok and earlier.id in hub.scene.objects
    run = highlight("highlight the xylem")
    assert run.ok and earlier.id not in hub.scene.objects and clicked.id in hub.scene.objects
    assert run.summary == "outlined the xylem"


def test_a_stale_look_says_why_it_cannot_point():
    brain, hub, _ = seen_brain({}, {})
    original_view = brain.sight.view
    hub.latest_info.epoch = 3  # continuity lost since the look
    run = run_tool(brain, "highlight", {"things": ["s1"]}, "highlight the xylem", seen_view=original_view)
    assert run.ok is False and run.summary == "the slide moved since I saw the xylem"


def test_a_box_may_outline_large_tissue_but_a_point_may_not():
    h, w = 720, 1280
    assert area_limit({"points": [[1, 1]]}, h, w) == pytest.approx(0.12 * h * w)
    big_box = {"box": [100, 50, 1100, 650]}  # a ring of xylem filling most of the view
    assert area_limit(big_box, h, w) == pytest.approx(0.9 * h * w)
    small_box = {"box": [0, 0, 100, 100]}
    assert area_limit(small_box, h, w) == pytest.approx(0.12 * h * w)  # never below the point limit


def test_old_outlines_stay_when_the_new_ones_fail(monkeypatch):
    from orchestrator.agent import Agent
    from orchestrator.coords import Crop

    hub = FakeHub()
    hub.infos = {7: hub.latest_info}
    old = obj(hub, "stoma", x=600.0)

    async def failing(*a, **kw):
        raise tools.ToolError("nothing distinct to outline there")

    monkeypatch.setattr(tools, "outline_at", failing)
    errors = []
    objects = asyncio.run(Agent(hub).point([{"box_2d": [100, 100, 200, 200], "label": "new"}], 7,
                                                      Crop(0, 0, 1000, 1000), errors=errors))
    assert objects == [] and old.id in hub.scene.objects
    assert errors == ["new: nothing distinct to outline there"]


def test_a_step_still_running_is_never_claimed_as_done():
    async def slow(ctx, args):
        await asyncio.sleep(0.3)
        return "found 3 parts"

    brain, _, agent = make([{"answer": "A stoma. I've broken it into its parts.", "targets": [],
                         "actions": [{"tool": "dissect", "args": {}}]}], [Tool("dissect", "", schema({}), slow, inline_ms=20)])
    result = asyncio.run(brain.turn("what is it made of?"))
    assert result["answer"] == "" and result["answerStatus"] == "not-published"
    assert [(r["tool"], r["ok"]) for r in result["actions"]] == [("dissect", None)]
    prompts = [p for p in agent.fast_model.prompts if "only your notes" not in p]
    assert len(prompts) == 1
    assert supplied_facts(prompts[0])["operations"][0]["ok"] is None


def test_a_word_used_as_an_adjective_is_no_claim_of_done():
    """Acceptance flow 9 on footage: "Colouring the outlined cells by their roundness now." was cut as a
    claim (it has "outlined"), and the user was told only "Still working on it (present)"."""
    async def slow(ctx, args):
        await asyncio.sleep(0.3)
        return "coloured"

    brain, _, agent = make([{"answer": "Colouring the outlined cells by their roundness now.", "targets": [],
                         "actions": [{"tool": "present", "args": {}}]}], [Tool("present", "", schema({}), slow, inline_ms=20)])
    answer = "Colouring the outlined cells by their roundness now."
    agent.vision, prompts = rewriter(answer)
    result = asyncio.run(brain.turn("can you colour them by roundness?"))
    assert result["answer"] == answer and result["answerStatus"] == "model"
    assert [(r["tool"], r["ok"]) for r in result["actions"]] == [("present", None)]
    assert supplied_facts(prompts[-1])["operations"][0]["ok"] is None


def test_the_voice_is_told_what_is_done_what_is_still_being_done_and_what_failed():
    from orchestrator.brain.claims import hands_note
    from orchestrator.brain.registry import ToolRun

    done = ToolRun("close_magnifier", {}, ok=True, summary="closed the magnifier")
    assert hands_note([done], []) == ("Context only, do not read aloud: the app has already done this for the user's "
                                      "last request: closed the magnifier.")
    view = SimpleNamespace(things=[Seen("s1", "cortex", "", (0, 0, 1, 1), 0.8)])
    note = hands_note([done], [("highlight", {"things": ["s1"]})], view=view)
    assert "already done" not in note and "done: closed the magnifier" in note
    assert "still working on" in note and "outlining cortex (it needs a new look first)" in note
    missed = ToolRun("highlight", {}, ok=False, summary="could not outline the parenchyma")
    note = hands_note([ToolRun("clear", {}, ok=True, summary="cleared 1 outline"), missed], [])
    assert "already done" not in note and "could not: could not outline the parenchyma" in note


def test_a_finished_query_step_is_not_the_request_done():
    """Acting bench, live cases (2026-10-11): "magnify the eye" with nothing outlined; hands found the eye (a query
    step, whose results go back to the model for the zoom) and the voice was told the app had "already done" the
    request, so it said "Done." before anything was magnified."""
    from orchestrator.brain.claims import hands_note
    from orchestrator.brain.registry import ToolRun

    found = ToolRun("find", {"what": "eye", "inside": "s1"}, ok=True, summary="outlined 2 (eye) inside the person")
    note = hands_note([found], [], kind=lambda tool: "query" if tool == "find" else "effect")
    assert "already done" not in note
    assert "done: find done" in note and "still working on" in note and "what find found" in note


def test_a_sentence_that_says_it_is_being_done_is_no_claim_but_a_clause_that_says_done_is():
    """Acceptance flow 9 on footage: "Keeping the five largest cells highlighted and fading the rest" was cut."""
    from orchestrator.brain.loop import claims_done

    assert not claims_done("Keeping the five largest cells highlighted and fading the rest of the outlines.")
    assert not claims_done("Okay, zooming in on it.")
    assert claims_done("Outlining the stoma; it is outlined.") and claims_done("Showing it now.")
    assert claims_done("I've closed the magnifier.")


def test_descriptions_with_common_participles_are_no_claims_but_speakers_saying_them_are():
    """M1.5c re-check S2, measured on 1,112 real sentences: "filled with tannins", "fluid-filled", "kept warm",
    "the slide moved", "Every highlighted cell" were cut; "Their tracks are now shown" is a claim."""
    from orchestrator.brain.loop import claims_done

    for said in ("The cells are filled with tannins.", "A fluid-filled vacuole.", "A lighter-colored region.",
                 "It was kept warm.", "The slide moved before I could outline them.", "Every highlighted cell is a neutrophil."):
        assert not claims_done(said), said
    for said in ("Their tracks are now shown in the magnifier.", "I kept the largest five.", "They are all outlined now.",
                 "Faded the rest."):
        assert claims_done(said), said
