"""Contract 7 (docs/design/2026-10-06-captures-operations-evidence.md; ADR 0011): a one-request choice
covers the whole request and nothing else; inspecting what can do something runs nothing; a default
changed while work runs changes no dispatched job."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import tools
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.choosing import add_choosing_tools
from orchestrator.ticket import issue, using
from tests.test_apply import World, run


def registry_for(world, published=None):
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=(lambda: published.append(1)) if published is not None else None))
    return registry


def ctx():
    return ToolContext("chat", 1, None, "use SAM 2.1 for this")


def outlined_with(world):
    return [payload.get("model") for op, payload in world.gpu.calls if op == "segment"]


def test_a_choice_covers_its_request_and_nothing_else_running_beside_it():
    """Concurrent A (with a choice) and B (the default): each request's outlines are made its own way."""
    world = World()
    registry = registry_for(world)

    async def go():
        a, b = issue(world, "turn"), issue(world, "turn")
        with using(a):
            said = await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "SAM 2.1 tiny"})

        async def in_a():
            with using(a):
                return await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")

        async def in_b():
            with using(b):
                return await tools.outline_at(world, 1, 500, 300, box=(480, 280, 520, 320), source="ai:test")

        oa, ob = await asyncio.gather(in_a(), in_b())
        return said, oa, ob, a

    said, oa, ob, a = run(go())
    assert said == "for this request, outlining uses SAM 2.1 tiny, exactly that, with no fallback, from this step on"
    assert (oa.made_by["implementation"], oa.made_by["policy"], oa.made_by["origin"]) == ("sam2.1-tiny", "raw", "user-chosen")
    assert (ob.made_by["implementation"], ob.made_by["policy"]) == ("edgetam", "route")
    assert a.choices["outline"]["used"] is True


def test_who_chose_the_model_is_said_never_assumed():
    """2026-10-11: `by` defaulted to "user", so a choice the brain made was recorded as the user's. It is required:
    without it the choice is refused for the model to say who; with "brain" the run's origin is brain-chosen."""
    world = World()
    registry = registry_for(world)

    async def go(args):
        a = issue(world, "turn")
        with using(a):
            try:
                await registry.tools["use"].handler(ctx(), args)
            except ToolFailed as refused:
                return refused, None
            return None, await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")

    refused, _ = run(go({"operation": "outline", "implementation": "sam2.1-tiny"}))
    assert "say who chose the model: by is user or brain" in str(refused) and refused.next == "pick one of these and try again"
    _, obj = run(go({"operation": "outline", "implementation": "sam2.1-tiny", "by": "brain"}))
    assert (obj.made_by["implementation"], obj.made_by["origin"]) == ("sam2.1-tiny", "brain-chosen")
    assert registry.tools["use"].params["required"] == ["operation", "implementation", "by"]


def test_deep_paths_and_late_steps_of_the_request_keep_it_and_settings_reads_see_it():
    world = World()
    registry = registry_for(world)

    async def go():
        a = issue(world, "turn")
        with using(a):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})
            seen_in_request = world.settings.outline

            async def nested():  # a tool inside a tool, in a task of its own
                await asyncio.sleep(0.01)  # a step that runs later
                return await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")

            obj = await asyncio.ensure_future(nested())
        return seen_in_request, obj

    seen_in_request, obj = run(go())
    assert seen_in_request == "sam2.1-tiny" and world.settings.outline == "edgetam"  # outside the request: the default
    assert obj.made_by["implementation"] == "sam2.1-tiny"
    assert world.settings.to_wire()["outline"] == "edgetam"  # the studio never sees a request's choice as the setting


def test_work_not_for_the_request_never_sees_its_choice():
    from orchestrator.options import unrelated

    world = World()
    registry = registry_for(world)

    async def go():
        a = issue(world, "turn")
        with using(a):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})

            async def embedding():  # seen-before keeping an outline, started while the request ran
                return world.settings.outline

            return await unrelated(embedding())

    assert run(go()) == "edgetam"


def test_cancelling_one_request_leaves_the_others_work_and_choice_alone():
    world = World()
    world.gpu.delay = 0.05
    registry = registry_for(world)

    async def go():
        a, b = issue(world, "turn"), issue(world, "turn")
        with using(a):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})

        async def outline(request, x):
            with using(request):
                return await tools.outline_at(world, 1, x, 200, box=(x - 20, 180, x + 20, 220), source="ai:test")

        ta = asyncio.ensure_future(outline(a, 300))
        tb = asyncio.ensure_future(outline(b, 600))
        await asyncio.sleep(0.01)
        ta.cancel()
        ob = await tb
        with pytest.raises(asyncio.CancelledError):
            await ta
        return ob

    ob = run(go())
    assert ob.made_by["implementation"] == "edgetam"


def test_a_default_changed_mid_flight_changes_no_dispatched_job():
    world = World()
    world.gpu.delay = 0.05

    async def go():
        b = issue(world, "turn")

        async def outline():
            with using(b):
                return await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")

        task = asyncio.ensure_future(outline())
        await asyncio.sleep(0.01)  # dispatched with the default
        world.settings.outline = "sam2.1-tiny"  # the user changes the default meanwhile
        first = await task
        with using(issue(world, "turn")):
            second = await tools.outline_at(world, 1, 600, 300, box=(580, 280, 620, 320), source="ai:test")
        return first, second

    first, second = run(go())
    assert first.made_by["implementation"] == "edgetam" and second.made_by["implementation"] == "sam2.1-tiny"


def test_a_choice_nothing_used_is_said_when_the_request_ends():
    from orchestrator import ticket as tickets

    world = World()
    registry = registry_for(world)
    told = []
    tickets.UNUSED_CHOICE[:] = [lambda request, unused: told.append(unused)]
    try:
        async def go():
            a = issue(world, "turn")
            with using(a):
                await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "cells", "implementation": "edgetam-automask"})
            a.seal()

        run(go())
    finally:
        tickets.UNUSED_CHOICE[:] = []
    assert told and told[0][0]["operation"] == "cells" and told[0][0]["implementation"] == "edgetam-automask"


def test_a_choice_that_cannot_run_is_refused_before_anything_runs(monkeypatch):
    monkeypatch.delenv("FAL_KEY", raising=False)
    world = World()
    registry = registry_for(world)

    async def go():
        with using(issue(world, "turn")):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "SAM 3.1 (fal cloud)"})

    with pytest.raises(ToolFailed) as refused:
        run(go())
    assert "needs FAL_KEY" in str(refused.value) and refused.value.next and not world.gpu.calls


def test_what_can_do_it_is_listed_without_running_anything(monkeypatch):
    from orchestrator.operations import alternatives_text

    monkeypatch.delenv("FAL_KEY", raising=False)
    world = World()
    text = alternatives_text(world, "outline", "box")
    assert "EdgeTAM [edgetam]: on this Mac; takes point, box; the default; can run now" in text
    assert "SAM 3.1 (cloud) [sam3.1-fal]: in the cloud" in text and "can't run here (needs FAL_KEY)" in text
    assert "A ring around" not in text  # a declared fallback only, listed with the routes
    assert "empty-box -> sam3.1-fal" in text and "Nothing was run" in text
    assert not world.gpu.calls


def test_what_can_do_it_says_which_are_loaded_and_what_a_load_costs_here():
    # A cold model is a wait (micro-sam 41 s to 2 min on 2026-10-11): the models choosing one are told it, measured here.
    from orchestrator.loads import Loads
    from orchestrator.operations import alternatives_text

    world = World()
    world.gpu.resident, world.gpu.loading = {"edgetam": {"usedAt": 0.0}}, {}
    world.loads = Loads(None)
    world.loads.measured = {"sam2.1-tiny": [{"seconds": 0.6, "memoryMb": 279, "how": "footprint"}]}
    text = alternatives_text(world, "outline", "box")
    assert "EdgeTAM [edgetam]: on this Mac; takes point, box; the default; can run now; loaded" in text
    assert "SAM 2.1 tiny [sam2.1-tiny]: on this Mac; takes point, box; can run now; not loaded: about 1 s to load" in text
    world.gpu.loading = {"sam2.1-tiny": {"since": 0.0}}
    assert "[sam2.1-tiny]: on this Mac; takes point, box; can run now; loading now" in alternatives_text(world, "outline", "box")


def test_set_default_changes_it_from_now_on_and_says_so():
    world = World()
    published = []
    registry = registry_for(world, published)
    said = run(registry.tools["set_default"].handler(ctx(), {"operation": "outline", "implementation": "sam2.1-tiny", "by": "user"}))
    assert world.settings.outline == "sam2.1-tiny" and published and said.startswith("from now on, outlining uses SAM 2.1 tiny")


# --- the scaffolding review's M1.4 items ------------------------------------------------------------------
def test_sam_3_1_means_its_default_and_the_user_is_asked_only_when_that_one_cannot_run(monkeypatch):
    """Item 3: "SAM 3.1" matched four labels and was refused as not existing. Since 2026-10-11 (the user's Q18) a
    family named alone is its default variant, said in the receipt; asked about only when the default can't run here
    and another of the family can."""
    from orchestrator import operations
    from orchestrator.brain.toolbox.choosing import candidates, pick, pick_said
    from orchestrator.operations import OPERATIONS

    op = OPERATIONS["outline"]
    assert [i.id for i in candidates("outline", "SAM 3.1")] == ["sam3.1-fal"]
    monkeypatch.setattr(operations, "availability", lambda hub, impl, **kw: "")
    impl, said = pick_said(None, op, "SAM 3.1")
    assert impl.id == "sam3.1-fal" and said == "SAM 3.1 named alone is its default, SAM 3.1 (cloud); the other: SAM 3.1 (this Mac)"
    assert pick(None, op, "SAM 3.1 (local)").id == "sam3.1-local"  # the live failure of 2026-10-11
    monkeypatch.setattr(operations, "availability", lambda hub, impl, **kw: "" if impl.id == "sam3.1-local" else "needs FAL_KEY")
    with pytest.raises(ToolFailed) as asked:
        pick(None, op, "SAM 3.1")
    assert "SAM 3.1 named alone is SAM 3.1 (cloud), which can't run here (needs FAL_KEY); SAM 3.1 (this Mac) can" in str(asked.value)
    assert asked.value.next == "ask the user whether to use SAM 3.1 (this Mac)"
    monkeypatch.setattr(operations, "availability", lambda hub, impl, **kw: "needs FAL_KEY")
    assert pick(None, op, "SAM 3.1").id == "sam3.1-fal"  # nothing else of it can run: `use` then says why it can't


def test_only_what_can_run_a_request_can_be_chosen():
    """Item 4: the spot ring and the vision model's boxes (declared fallbacks, no adapter of their own)
    were accepted, then failed."""
    world = World()
    registry = registry_for(world)

    async def go(args):
        with using(issue(world, "turn")):
            return await registry.tools["use"].handler(ctx(), args)

    for args in ({"operation": "outline", "implementation": "spot-ring"}, {"operation": "find-like", "implementation": "vision boxes"}):
        with pytest.raises(ToolFailed) as refused:
            run(go(args))
        # A name that means nothing goes back to the model with the choices, to pick again (live 2026-10-11).
        assert "there is no" in str(refused.value) and refused.value.next == "pick one of these and try again"
        assert "(id = name): " in str(refused.value)
    assert not world.gpu.calls


def test_inside_a_request_the_listing_says_which_is_really_the_default():
    """Item 5: after use(sam2.1-tiny) the listing called SAM 2.1 tiny "the default"."""
    from orchestrator.operations import alternatives_text

    world = World()
    registry = registry_for(world)

    async def go():
        with using(issue(world, "turn")):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})
            return alternatives_text(world, "outline", "box")

    text = run(go())
    assert "EdgeTAM [edgetam]: on this Mac; takes point, box; the default" in text
    assert "SAM 2.1 tiny [sam2.1-tiny]: on this Mac; takes point, box; can run" in text
    with pytest.raises(ToolFailed) as refused:
        alternatives_text(world, "teleport")
    assert refused.value.next


def test_work_not_for_the_request_never_runs_with_its_choice():
    """Note: the runner read the ticket's choices even in work started as not for the request."""
    from orchestrator.options import unrelated

    world = World()
    registry = registry_for(world)

    async def go():
        a = issue(world, "turn")
        with using(a):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})

            async def background():
                return await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")

            return await unrelated(background()), a

    obj, a = run(go())
    assert obj.made_by["implementation"] == "edgetam" and a.choices["outline"]["used"] is False


def test_a_spoken_request_s_hand_off_shares_hands_choices_and_an_unused_one_is_said_once():
    """Item 1: hands made the choice on its own ticket; the hand-off's look outlined on another."""
    from orchestrator import ticket as tickets

    world = World()
    registry = registry_for(world)
    told = []
    tickets.UNUSED_CHOICE[:] = [lambda request, unused: told.append((request.id, [u["implementation"] for u in unused]))]
    try:
        async def go():
            hands = issue(world, "hands")
            with using(hands):
                await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})
            hands.keep_choices(0.05)
            hands.seal()  # hands is done; the hand-off has not come yet
            turn = issue(world, "turn")
            turn.adopt_choices(hands)
            with using(turn):
                obj = await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")
            turn.seal()
            await asyncio.sleep(0.1)
            unused_hands = issue(world, "hands")
            with using(unused_hands):
                await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})
            unused_hands.keep_choices(0.02)
            unused_hands.seal()
            await asyncio.sleep(0.05)
            return obj

        obj = run(go())
    finally:
        tickets.UNUSED_CHOICE[:] = []
    assert obj.made_by["implementation"] == "sam2.1-tiny"
    assert len(told) == 1 and told[0][1] == ["sam2.1-tiny"]  # only the one nothing used, said once


def test_a_job_that_finishes_after_its_request_returned_keeps_the_choice():
    """Note: the delayed-job test awaited inside the request; here the job ends after it is sealed."""
    world = World()
    world.gpu.delay = 0.05
    registry = registry_for(world)

    async def go():
        a = issue(world, "turn")
        with using(a):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})
            task = asyncio.ensure_future(tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test"))
            a.track(task)
        a.seal()  # the request returned; its job is still running
        return await task

    assert run(go()).made_by["implementation"] == "sam2.1-tiny"


def test_a_refused_choice_runs_nothing_in_its_place_for_the_rest_of_the_request():
    """Item 8 (round 2): a refused `use` left the request's outlining to the default."""
    from orchestrator import ticket as tickets

    world = World()
    registry = registry_for(world)
    told = []
    tickets.UNUSED_CHOICE[:] = [lambda request, unused: told.append(unused)]
    try:
        async def go():
            a = issue(world, "turn")
            with using(a):
                with pytest.raises(ToolFailed):
                    await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "spot ring"})
                with pytest.raises(tools.ToolError) as refused:
                    await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")
            a.seal()
            with using(issue(world, "turn")):  # the next request is back to the default
                after = await tools.outline_at(world, 1, 500, 300, box=(480, 280, 520, 320), source="ai:test")
            return str(refused.value), after

        said, after = run(go())
    finally:
        tickets.UNUSED_CHOICE[:] = []
    assert "spot ring was asked for and refused" in said and "nothing else was used in its place" in said
    assert after.made_by["implementation"] == "edgetam" and told == []  # refused is said, not "unused"


def test_a_name_the_model_got_wrong_is_picked_again_within_the_request_and_the_work_is_done(monkeypatch):
    """Live 2026-10-11: the brain sent "SAM 3.1 (local)", `use` refused it, and the request ended on a raw failure. A
    name that means nothing goes back to the model with the choices ("id = name"); its next round names one, the later
    `use` replaces the refusal, and the find after it outlines with that model. The answer is the model's own."""
    from orchestrator import operations
    from orchestrator.brain.loop import Brain
    from orchestrator.brain.toolbox import Services, build
    from tests.fakes import conversation
    from tests.test_brain import FakeAgent, FakeVision
    from tests.test_finder import Agent_

    monkeypatch.setitem(operations.INSTALLED, "sam3", lambda: "")
    world = World()
    world.gpu_heavy = world.gpu  # SAM 3.1 on this Mac runs on the analysis worker
    world.clients, world.sent, world.focused = set(), [], None
    world.broadcast = lambda payload: world.sent.append(payload)
    day = conversation()
    finder = Agent_(world, [("stoma", (280, 180, 320, 220), 0.9)])
    # Typed chat as live: the fast pass plans first (with the name it shortened), the recovery round is the vision
    # model's, and the answer is written again from the receipts.
    agent = FakeAgent(world, day, [
        {"answer": "Outlining the stoma with SAM 3.1 on this Mac.", "targets": [],
         "actions": [{"tool": "use", "args": {"by": "user", "operation": "outline", "implementation": "sam3.1-local"}},
                     {"tool": "find", "args": {"what": "stoma"}}]},
    ])
    agent.fast_model = FakeVision(fast={"intent": "request", "needs_look": False, "answer": "Outlining the stoma.",
                                        "actions": [{"tool": "use", "args": {"by": "user", "operation": "outline", "implementation": "SAM 3.1 (lokal)"}},
                                                    {"tool": "find", "args": {"what": "stoma"}}]})
    agent.look_in, agent.snapshot = finder.look_in, finder.snapshot
    final = {"answer": "I outlined the stoma with SAM 3.1 on this Mac.", "actions": [], "observation_scope": "none",
             "input_refs": []}
    agent.fast_model.rewrite = final
    services = Services(hub=world, agent=agent)
    brain = Brain(world, agent, build(services), day)
    brain.services = services
    result = run(brain.turn("outline the stoma with SAM 3.1 on this Mac"))
    steps = [(a["tool"], a["ok"]) for a in result["actions"]]
    assert steps == [("use", False), ("find", False), ("use", True), ("find", True)], steps
    refused = result["actions"][0]["summary"]
    assert "the choices (id = name): " in refused and "sam3.1-local = SAM 3.1 (this Mac)" in refused
    assert "pick one of these and try again" in agent.calls[0]["question"]  # the recovery round was told the choices
    assert not any("refused" in a["summary"] for a in result["actions"][2:])  # the later `use` replaced the refusal
    outlined = [world.scene.objects[i] for i in result["objectIds"]]
    assert [o.made_by["implementation"] for o in outlined] == ["sam3.1-local"]
    assert [p.get("model") for op, p in world.gpu.calls if op == "segment"] == ["sam3.1-local"]
    assert result["answer"] == final["answer"] and result["answerStatus"] == "model"
    # The turn log says what this request chose and what ran, beside the settings (the defaults), and counts the
    # outline its steps made, not only its look's (Slice 2).
    turn = next(r for r in agent.logged if r.get("kind") == "turn")
    assert turn["outline"] == "edgetam" and turn["outlined"] == 1 and turn["lookOutlined"] == 0
    assert turn["used"]["choices"]["outline"]["implementation"] == "sam3.1-local"
    assert turn["used"]["choices"]["outline"]["origin"] == "user-chosen"
    assert {"operation": "outline", "implementation": "sam3.1-local", "policy": "raw", "origin": "user-chosen"} \
        in turn["used"]["ran"]


# --- Slice 5 (2026-10-11): choose any model, any way ----------------------------------------------------------------
def test_every_model_role_can_be_chosen_for_one_request_and_the_rest_from_now_on(monkeypatch):
    """The user's Q14: the vision model and every other model role can be switched by asking. A setting's choice is
    seen by every settings read made for the request (options.overriding), never outside it; the running look and
    medical names are no request's own (set_default only); a name that means nothing is refused with the choices."""
    from orchestrator.options import VISION

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    world = World()
    registry = registry_for(world)

    async def go(args, read):
        a = issue(world, "turn")
        with using(a):
            try:
                said = await registry.tools["use"].handler(ctx(), args)
            except ToolFailed as refused:
                return refused, None, a
            return said, getattr(world.settings, read), a

    said, vision, a = run(go({"operation": "vision", "implementation": "GPT-6.1 Sol", "by": "user"}, "vision"))
    assert said == "for this request, the vision model (looking and answering) uses GPT-6.1 Sol, from this step on"
    assert vision == "gpt-6.1-sol" and world.settings.vision == "claude-opus-5-5-fast"  # outside the request: the default
    assert a.choices["vision"]["implementation"] == "gpt-6.1-sol" and a.choice_log[-1]["operation"] == "vision"
    said, fast, _ = run(go({"operation": "fast", "implementation": "off", "by": "brain"}, "fast"))
    assert fast == "off" and said.startswith("for this request, the quick-decision model uses Always look first")
    refused, _, _ = run(go({"operation": "sight", "implementation": "off", "by": "user"}, "sight"))
    assert "not part of one request" in str(refused) and refused.next == "say whether to change it from now on"
    refused, _, a = run(go({"operation": "vision", "implementation": "Gemini 9", "by": "user"}, "vision"))
    assert "the choices (id = name): gemini-3.8-flash = Gemini 3.8 Flash" in str(refused)
    assert a.choices["vision"]["refused"] and "vision" not in a.overrides
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    refused, _, _ = run(go({"operation": "vision", "implementation": "gemini-3.5-flash", "by": "user"}, "vision"))
    assert "Gemini 3.5 Flash can't run here (needs GEMINI_API_KEY)" in str(refused) and "GPT-6.1 Sol" in str(refused)
    assert set(VISION) <= set(registry.tools["use"].params["properties"]["implementation"]["enum"])


def test_a_vision_choice_made_after_the_look_says_that_look_used_the_default(monkeypatch):
    """Slice 5 timing: a `use` of the vision model in the look's own plan comes after that look; what already ran is
    said, never claimed to have used the choice."""
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    world = World()
    registry = registry_for(world)

    async def go():
        a = issue(world, "turn")
        a.admission = SimpleNamespace(attempts=(SimpleNamespace(phase="look", model="gemini-3.8-flash"),))
        with using(a):
            return await registry.tools["use"].handler(ctx(), {"operation": "vision", "implementation": "gemini-3.5-flash",
                                                               "by": "user"})

    said = run(go())
    assert said.endswith("; this request's look already ran with Gemini 3.8 Flash, so it stays as it is")


def test_a_choice_holds_for_the_steps_after_it_until_the_next_one():
    """"Outline it with SAM 2.1, then with micro-sam": a request holds a choice per step. Only the first `use` of an
    operation moves before the steps (actions.first_choices); a later one keeps its place, so the steps between run
    with the earlier one, and each outline is made by the model chosen before it."""
    from orchestrator.brain.actions import first_choices

    plan = [("highlight", {"things": ["o1"]}), ("use", {"operation": "outline", "implementation": "sam2.1-tiny"}),
            ("highlight", {"things": ["o1"]}), ("use", {"operation": "outline", "implementation": "micro-sam-lm"}),
            ("highlight", {"things": ["o1"]}), ("use", {"operation": "vision", "implementation": "gpt-6.1-sol"})]
    assert first_choices(plan) == [1, 5]  # the first of each operation; micro-sam's stays after its step
    world = World()
    registry = registry_for(world)

    async def go():
        a = issue(world, "turn")
        with using(a):
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})
            first = await tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test")
            await registry.tools["use"].handler(ctx(), {"by": "user", "operation": "outline", "implementation": "edgetam"})
            second = await tools.outline_at(world, 1, 500, 300, box=(480, 280, 520, 320), source="ai:test")
        return first, second, a

    first, second, a = run(go())
    assert (first.made_by["implementation"], second.made_by["implementation"]) == ("sam2.1-tiny", "edgetam")
    assert [c["implementation"] for c in a.choice_log] == ["sam2.1-tiny", "edgetam"]


def test_highlighting_an_outline_under_a_choice_draws_it_again_and_replaces_the_apps(monkeypatch):
    """The user's Q17: "outline it with SAM 2.1" on an existing outline drew nothing new (view.py skipped
    ticket.reusable). It is drawn again with the choice where it is now and replaces the app's earlier outline;
    keep=true draws beside it; the user's own outline is never removed; one this request drew stays (per step)."""
    from orchestrator.brain.toolbox.view import add_highlight

    world = World()
    registry = registry_for(world)
    add_highlight(registry, world, SimpleNamespace(view=None), outline_seen=None)
    app_obj = run(tools.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="ai:test", label="stoma",
                                   status="likely"))
    user_obj = run(tools.outline_at(world, 1, 500, 300, box=(480, 280, 520, 320), source="click", label="pore"))

    async def go(*steps):
        a = issue(world, "turn")
        c = ctx()
        out = []
        with using(a):
            for name, args in steps:
                out.append(await registry.tools[name].handler(c, args))
        return out, c

    (chose, said), c = run(go(("use", {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"}),
                              ("highlight", {"things": [app_obj.id]})))
    new = world.scene.objects[c.aliases[app_obj.id]]
    assert new.made_by["implementation"] == "sam2.1-tiny" and new.label == "stoma" and app_obj.id not in world.scene.objects
    assert "the stoma drawn again with SAM 2.1 tiny (on this Mac), the model chosen for this request" in said
    (_, said), c = run(go(("use", {"by": "user", "operation": "outline", "implementation": "edgetam"}),
                          ("highlight", {"things": [user_obj.id], "keep": True})))
    assert user_obj.id in world.scene.objects and new.id in world.scene.objects  # the user's own, and kept beside
    # Per step in one request ("SAM 2.1, then EdgeTAM"): SAM 2.1's stands (it drew it), EdgeTAM draws it again, and
    # both are kept, each said with the model that drew it.
    (_, _, _, said), c = run(go(("use", {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"}),
                                ("highlight", {"things": [new.id]}),
                                ("use", {"by": "user", "operation": "outline", "implementation": "edgetam"}),
                                ("highlight", {"things": [new.id]})))
    drawn = [world.scene.objects[i].made_by["implementation"] for i in c.objects]
    assert drawn == ["sam2.1-tiny", "edgetam"] and new.id in world.scene.objects
    assert "drawn again with EdgeTAM (on this Mac)" in said


def test_from_now_on_is_kept_until_changed_restored_after_a_restart_and_said(tmp_path, monkeypatch):
    """The user's Q15: set_default (and the Models menu: one helper) keeps the model choices until changed (the
    preference `defaults`, day/prefs.py); a restart applies each, checked as runnable, and says it; one that can't
    run is said and the code's default stays. Not repeated as a preference in the day's digest."""
    from orchestrator import defaults
    from orchestrator.day import DayStore
    from orchestrator.day.context import day_digest
    from orchestrator.options import Settings

    monkeypatch.setenv("OPENAI_API_KEY", "x")
    day = DayStore(root=tmp_path)
    world = World()
    published = []
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(
        hub=world, publish_settings=lambda: published.append(1),
        change_settings=lambda changes, by: defaults.change(world, day, changes, by, publish=lambda: published.append(1))))
    said = run(registry.tools["set_default"].handler(ctx(), {"operation": "outline", "implementation": "SAM 2.1",
                                                              "by": "user"}))
    assert said.startswith("from now on, outlining uses SAM 2.1 tiny (the Models menu shows it; kept for today)")
    run(registry.tools["set_default"].handler(ctx(), {"operation": "vision", "implementation": "GPT-6.1 Sol", "by": "brain"}))
    assert world.settings.outline == "sam2.1-tiny" and world.settings.vision == "gpt-6.1-sol" and published
    assert set(day.prefs["defaults"]) == {"outline", "vision"} and day.prefs["defaults"]["vision"]["by"] == "brain"
    assert "defaults" not in day_digest(day) and "sam2.1" not in day_digest(day)

    again, fresh = DayStore(root=tmp_path), Settings()  # a restart
    restored = defaults.restore(fresh, again)
    assert fresh.outline == "sam2.1-tiny" and fresh.vision == "gpt-6.1-sol" and not any(r["problem"] for r in restored)
    words = defaults.words(restored, fresh)
    assert "Outlining still uses SAM 2.1 tiny (on this Mac), as you asked at " in words
    assert "Seeing still uses GPT-6.1 Sol (cloud), as chosen at " in words
    monkeypatch.delenv("OPENAI_API_KEY")
    later = Settings()
    restored = defaults.restore(later, DayStore(root=tmp_path))
    assert later.vision == "claude-opus-5-5-fast" and next(r for r in restored if r["group"] == "vision")["problem"] == "needs OPENAI_API_KEY"
    assert "Seeing was set to GPT-6.1 Sol (cloud) at " in defaults.words(restored, later)
    assert "but it can't run now (needs OPENAI_API_KEY): Claude Opus 5.5 (fast mode) is used." in defaults.words(restored, later)
    # The key is added while the app runs: the choice is the user's again at once, not at the next restart (live
    # 2026-10-12: DINOv3 downloaded again and look-alikes stayed on DINOv2 until a restart).
    assert defaults.resume(later, DayStore(root=tmp_path)) == []
    monkeypatch.setenv("OPENAI_API_KEY", "x")
    back = defaults.resume(later, DayStore(root=tmp_path))
    assert [(r["group"], r["option"]) for r in back] == [("vision", "gpt-6.1-sol")]
    assert defaults.resumed_words(back).startswith("Seeing uses GPT-6.1 Sol (cloud) again: it can run now, as chosen at ")


def test_the_models_menu_and_set_default_change_settings_one_way(tmp_path):
    """commands.change_settings is the one way: the Models menu's command and set_default (Services.change_settings)
    both keep the day's model choice and publish."""
    from orchestrator import commands
    from orchestrator.day import DayStore

    published = []
    app = SimpleNamespace(hub=World(), day=DayStore(root=tmp_path), publish_settings=lambda: published.append(1),
                          agent=SimpleNamespace(zeroshot=None), taught=None, seen=None)
    app.hub.settings.taught = "on"
    problems = commands.change_settings(app, {"outline": "sam2.1-tiny", "level": "technical"}, by="menu")
    assert not problems and app.day.prefs["defaults"]["outline"]["option"] == "sam2.1-tiny" and published
    assert app.day.prefs["level"] == "technical" and "level" not in app.day.prefs["defaults"]  # not a model


def test_a_choice_with_no_step_after_it_says_nothing_has_been_done_with_it():
    """Journeys round 2 (live 2026-10-11, "use SAM 2.1 for this" 0 of 3): the plan was `use` alone, and the round after
    it saw only "uses SAM 2.1 tiny, from this step on" and added no step. A choice that is its plan's last step says,
    as a fact, that nothing has been done with it; one with a step after it does not."""
    from orchestrator.brain.actions import ActionRunner
    from orchestrator.brain.registry import Tool

    world = World()
    registry = registry_for(world)

    async def zoom(ctx, args):
        return "zoomed"

    registry.add(Tool("zoom", "", {}, zoom, inline_ms=1))

    async def late(*args):
        pass

    async def plan(*steps):
        with using(issue(world, "turn")):
            return await ActionRunner(registry, world, lambda: None, late).run(list(steps), ctx())

    alone = run(plan(("use", {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"})))
    assert alone[0].ok and alone[0].summary.endswith(
        "from this step on; no step comes after it in this plan, so nothing has been done with it yet")
    followed = run(plan(("use", {"by": "user", "operation": "outline", "implementation": "sam2.1-tiny"}),
                        ("zoom", {"object": "o1"})))
    assert followed[0].ok and "nothing has been done with it" not in followed[0].summary


def test_a_model_not_downloaded_is_refused_with_its_download_offered(tmp_path, monkeypatch):
    """Live 2026-10-12: "I got approved for DINOv3, can you use it now?" was answered "not downloaded" and nothing more.
    What stops it is a download, so the refusal carries the offer (get_model on the user's yes)."""
    monkeypatch.setenv("MICROMIND_MODEL_FILES", str(tmp_path / "files"))
    monkeypatch.setenv("MICROMIND_CATALOG", str(tmp_path / "catalog"))
    world = World()
    offered = []
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None,
                                                 offer_download=lambda model: offered.append(model) or " offered (p1)"))
    with pytest.raises(ToolFailed) as refused:
        run(registry.tools["set_default"].handler(ctx(), {"operation": "outline", "implementation": "sam2.1-tiny", "by": "user"}))
    assert offered == ["sam2.1-tiny"] and "offered (p1)" in str(refused.value), str(refused.value)
    assert refused.value.next == "ask the user whether to download it"
    assert world.settings.outline != "sam2.1-tiny", "nothing changed"
