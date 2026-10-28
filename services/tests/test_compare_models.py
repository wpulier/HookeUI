"""Named comparisons (M2 of docs/design/2026-10-06-captures-operations-evidence.md): the models the user names
run on the same view with the same prompt (raw unless the full route is asked for), what each made is shown
side by side on that view's own pixels and measured, and a result reused from an earlier run is said to be,
its time not counted as a run."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator import tools
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.choosing import add_choosing_tools
from orchestrator.ticket import issue, using
from tests.test_apply import Gpu, World, run


class TwoModels(Gpu):
    """EdgeTAM outlines the box 2 px in; SAM 2.1 6 px in: the same thing, outlined a little differently."""

    async def call(self, op, timeout=30.0, **payload):
        out = await super().call(op, timeout, **payload)
        if payload.get("model") == "sam2.1-tiny":
            (poly,) = out["polygons"]
            out = {**out, "polygons": [[[poly[0][0] + 4, poly[0][1] + 4], [poly[1][0] - 4, poly[1][1] + 4],
                                        [poly[2][0] - 4, poly[2][1] - 4], [poly[3][0] + 4, poly[3][1] - 4]]]}
        return out


def setting_up():
    world = World()
    world.gpu = TwoModels()
    shown = []
    magnifier = SimpleNamespace(show_picture=lambda jpeg, label, object_id=None: shown.append((jpeg, label)) or "/api/picture/x")
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, magnifier=magnifier, publish_settings=None))
    obj = run(tools.outline_at(world, world.latest_info.id, 300, 200, box=(260, 160, 340, 240), source="click"))
    return world, registry, obj, shown


def compare(world, registry, args):
    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["compare_models"].handler(ToolContext("chat", 1, None, "compare them"), args)
    return run(go())


def test_the_named_models_run_on_the_same_view_and_are_shown_side_by_side_measured():
    world, registry, obj, shown = setting_up()
    said = compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny"], "object": obj.id})
    assert said.startswith("Compared on the view at ") and "(exactly those models)" in said
    # Each prompted by the outline's own box (264-336 on the view it was made on), on that view.
    assert "EdgeTAM 5,184 px², in " in said and "SAM 2.1 tiny 4,096 px², in " in said
    assert "EdgeTAM and SAM 2.1 tiny overlap 79% (IoU)" in said and "reused" not in said
    assert said.endswith("shown side by side in the magnifier; each result is kept for 5 minutes to keep one on the board")
    assert shown and shown[-1][1] == "Compared on the same view: EdgeTAM | SAM 2.1 tiny" and shown[-1][0][:2] == b"\xff\xd8"
    models = [p.get("model") for op, p in world.gpu.calls if op == "segment"]
    assert models.count("sam2.1-tiny") == 1  # each model once, on the same box


def test_asking_again_reuses_both_and_says_so():
    world, registry, obj, _ = setting_up()
    compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny"], "object": obj.id})
    calls = len(world.gpu.calls)
    again = compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny"], "object": obj.id})
    assert len(world.gpu.calls) == calls  # nothing ran again
    assert again.count("reused from its run at") == 2 and "(its time not counted)" in again


def test_a_model_that_cant_run_here_is_said_and_two_are_needed():
    world, registry, obj, _ = setting_up()
    with pytest.raises(ToolFailed) as refused:
        compare(world, registry, {"implementations": ["EdgeTAM"], "object": obj.id})
    assert "two different models" in str(refused.value) and refused.value.next
    with pytest.raises(ToolFailed) as refused:
        compare(world, registry, {"implementations": ["EdgeTAM", "SAM 3.1 (fal cloud)"], "object": obj.id})
    assert "can't run here" in str(refused.value)


def test_the_full_route_is_used_only_when_asked():
    world, registry, obj, _ = setting_up()
    said = compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny"], "object": obj.id, "policy": "route"})
    assert "(each with its declared fallbacks)" in said



def with_fal(monkeypatch, edgetam_finds=True):
    """SAM 3.1 (fal cloud) stands in, outlining the box 2 px in; EdgeTAM finds nothing when asked to."""
    monkeypatch.setenv("FAL_KEY", "test")
    calls = []

    async def fake_fal(hub_, capture, jpeg, x, y, box, label):
        calls.append(box)
        x0, y0, x1, y1 = box
        return {"polygons": [[[x0 + 2, y0 + 2], [x1 - 2, y0 + 2], [x1 - 2, y1 - 2], [x0 + 2, y1 - 2]]], "score": 0.95, "ms": 900.0}

    monkeypatch.setattr(tools, "_segment_fal", fake_fal)
    world, registry, obj, shown = setting_up()
    if not edgetam_finds:
        real = world.gpu.call

        async def empty_for_edgetam(op, timeout=30.0, **payload):
            out = await real(op, timeout, **payload)
            return {**out, "polygons": []} if payload.get("model") in (None, "edgetam") and op == "segment" else out

        world.gpu.call = empty_for_edgetam
    return world, registry, obj, shown, calls


def test_a_fallback_is_named_as_what_ran_and_the_same_models_output_is_never_an_overlap(monkeypatch):
    """Review of M2.2, B1 (ADR 0011): EdgeTAM found nothing, so its route fell back to SAM 3.1."""
    world, registry, obj, shown, fal = with_fal(monkeypatch, edgetam_finds=False)
    said = compare(world, registry, {"implementations": ["EdgeTAM", "SAM 3.1 (fal cloud)"], "object": obj.id, "policy": "route"})
    assert "EdgeTAM → SAM 3.1 (cloud), its fallback " in said
    assert "EdgeTAM and SAM 3.1 (cloud) are the same model's output (SAM 3.1 (cloud)), so they are not compared" in said
    assert "overlap" not in said and "sam3.1-fal" not in said
    assert shown[-1][1] == "Compared on the same view: EdgeTAM → SAM 3.1 (cloud), its fallback | SAM 3.1 (cloud)"


def test_three_models_are_compared_in_every_pair_and_a_fourth_is_said_to_be_left_out(monkeypatch):
    # This refusal oracle intentionally exercises the separate commercial edition.
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    world, registry, obj, _, _ = with_fal(monkeypatch)
    said = compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny", "EdgeTAM", "SAM 3.1 (fal cloud)",
                                                         "EdgeTAM, then SAM 3.1 (fal)", "no such model", "micro-sam"],
                                     "object": obj.id})
    assert said.count("overlap") == 3 and "SAM 2.1 tiny and SAM 3.1 (cloud) overlap" in said
    # The same model named twice counts once; an unknown name is said, never fatal; one that can't run here is said
    # with why; the cap is three of those that can run, and the rest are said to be left out (M2.2 re-check notes).
    assert "not known here: no such model" in said and "micro-sam (light microscopy) can't run here" in said
    assert said.endswith("left out (three at once at most): EdgeTAM then SAM 3.1 (cloud)")


def test_comparing_cells_reuses_the_cells_shown_records_it_and_keeps_its_populations_apart(monkeypatch):
    """Review of M2.2, B2 and B3: the kept population's use is recorded as a reuse; the comparison's own
    population is titled so and never stands in for "the cells"."""
    from orchestrator.operations import runner
    from orchestrator.population import layer_of, resolve
    from tests.test_population import CellFinder

    monkeypatch.setenv("MICROMIND_EDITION", "research")
    world = World()
    world.settings.cells = "cellpose-sam"
    world.gpu_heavy = CellFinder()
    world.sent = []
    world.broadcast = world.sent.append
    with using(issue(world, "command")):
        first, _ = run(tools.outline_cells(world))
    layer_of(world).show(first)
    logged = []
    runner(world).log = logged.append
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None, magnifier=SimpleNamespace(
        show_picture=lambda jpeg, label, object_id=None: "/api/picture/x")))
    said = compare(world, registry, {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"]})
    assert f"Cellpose-SAM: 6 instances segmented in the view ({first.id}), reused from its run at" in said
    assert "micro-sam (light microscopy): 6 instances segmented in the view (e" in said
    assert "6 found by both Cellpose-SAM and micro-sam (light microscopy)" in said
    # Each arm's cells are a population kept in Data, as the comparison panel says: never "kept for 5 minutes".
    assert said.endswith("; each result is kept in Data") and "5 minutes" not in said, said
    assert any(r.get("kind") == "execution-use" and r.get("reused") and r.get("from") == "evidence"
               and r.get("execution") == first.made_by["execution"] for r in logged)
    other = [p for p in world.evidence.items.values() if p is not first][-1]
    assert other.title.startswith("Cell outlines, for a comparison · micro-sam (light microscopy) · ")
    layer_of(world).hide()
    with using(issue(world, "turn")):
        assert resolve(world, None, ToolContext("chat", 2, None, "which is the biggest cell?")) is first
        assert resolve(world, other.id) is other  # named by id, it stands in



def test_a_pair_is_named_by_what_ran_and_said_once(monkeypatch):
    """M2.2 re-check, B1: EdgeTAM fell back to SAM 3.1; its overlap with SAM 2.1 is SAM 3.1's, said once."""
    world, registry, obj, _, _ = with_fal(monkeypatch, edgetam_finds=False)
    said = compare(world, registry, {"implementations": ["EdgeTAM", "SAM 3.1 (fal cloud)", "SAM 2.1 tiny"], "object": obj.id,
                                     "policy": "route"})
    assert "EdgeTAM and SAM 3.1 (cloud) are the same model's output (SAM 3.1 (cloud))" in said
    assert said.count("SAM 3.1 (cloud) and SAM 2.1 tiny overlap") == 1 and "EdgeTAM and SAM 2.1 tiny overlap" not in said


def test_a_comparisons_cells_are_reached_by_at_n_when_shown_and_said_when_only_they_exist(monkeypatch):
    """M2.2 re-check, SF1-SF4."""
    from orchestrator.brain.registry import step_output
    from orchestrator.population import layer_of, nothing_here, resolve
    from tests.test_population import CellFinder

    monkeypatch.setenv("MICROMIND_EDITION", "research")
    world = World()
    world.gpu_heavy = CellFinder()
    world.sent = []
    world.broadcast = world.sent.append
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None, magnifier=SimpleNamespace(
        show_picture=lambda jpeg, label, object_id=None: "/api/picture/x")))
    ctx = ToolContext("chat", 1, None, "compare them")
    ctx.step = 1
    ctx.steps.tools.update({1: "compare_models", 2: "highlight"})

    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["compare_models"].handler(ctx, {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"]})
    run(go())
    made = [p for p in world.evidence.items.values()]
    assert len(made) == 2 and all(p.made_by.get("origin") == "compare" for p in made)
    later = SimpleNamespace(**{**ctx.__dict__, "step": 2})
    with pytest.raises(ToolFailed) as two:  # SF2: "@1" reaches them, and two are named, never "made nothing"
        step_output(later, "@1")
    assert f"step 1 made {made[0].id}, {made[1].id}; name one by its id" in str(two.value)
    assert nothing_here(world).startswith(f"only cells segmented for a comparison are here ({made[0].id}, {made[1].id})")  # SF3
    assert made[1].title.startswith("Cell outlines, for a comparison · micro-sam (light microscopy) · ")
    layer_of(world).show(made[1])
    with using(issue(world, "turn")):
        assert resolve(world, None, ToolContext("chat", 2, None, "biggest?")) is made[1]  # SF1: what is shown stands
    with pytest.raises(ToolFailed) as gone:  # SF4: a named view that is gone is said, never replaced
        made[0].capture, made[0].path = None, None
        compare(world, registry, {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"], "of": made[0].id})
    assert "is not here any more" in str(gone.value)



def cells_world(monkeypatch):
    from tests.test_population import CellFinder

    monkeypatch.setenv("MICROMIND_EDITION", "research")
    world = World()
    world.gpu_heavy = CellFinder()
    world.sent = []
    world.broadcast = world.sent.append
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None, magnifier=SimpleNamespace(
        show_picture=lambda jpeg, label, object_id=None: "/api/picture/x")))
    return world, registry


def test_a_named_population_that_does_not_exist_is_said_to_not_exist(monkeypatch):
    """M2.2 re-check SF1 (ADR 0006): "e99" was refused as "a view that is gone"; "every cell" is no name (SF4)."""
    world, registry = cells_world(monkeypatch)
    with pytest.raises(ToolFailed) as none:
        compare(world, registry, {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"], "of": "e99"})
    assert str(none.value).startswith("there are no cells called e99 today") and "not here any more" not in str(none.value)
    said = compare(world, registry, {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"], "object": "every cell"})
    assert said.startswith("Compared on the view at ")  # "every cell" names no population: the view now


def test_a_caption_is_never_cut_off():
    import numpy as np

    from orchestrator.comparing import caption_strip

    text = "EdgeTAM → SAM 3.1 (cloud), its fallback · 5,184 px²".replace("→", "->")
    strip = caption_strip(text, 160, (255, 255, 255))
    assert strip.shape[0] == 44  # too long for one line at any size: two lines, the area on the second
    assert caption_strip("EdgeTAM · 5,184 px²", 160, (255, 255, 255)).shape[0] == 28


def test_nothing_here_is_what_every_tool_says_when_only_comparison_cells_exist(monkeypatch):
    """M2.2 re-check SF3, pinned on each caller."""
    from orchestrator.brain.toolbox.population import add_population_tools
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.screen import Screen, resolve as screen_item

    world, registry = cells_world(monkeypatch)
    compare(world, registry, {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"]})
    add_population_tools(registry, SimpleNamespace(hub=world, log=None))
    add_highlight(registry, world, sight=SimpleNamespace(view=None), outline_seen=None)

    def refused(name, args):
        with pytest.raises(ToolFailed) as no:
            with using(issue(world, "turn")):
                run(registry.tools[name].handler(ToolContext("chat", 1, None, name), args))
        return str(no.value)

    assert refused("query_population", {}).startswith("only cells segmented for a comparison are here")
    assert refused("highlight", {"by": "area", "top": 1}).startswith("only cells segmented for a comparison are here")
    world.screen = Screen()
    with pytest.raises(ValueError) as no:
        screen_item(world, "the cells", None)
    assert str(no.value).startswith("only cells segmented for a comparison are here")


# --- Slice 5 (2026-10-11): what was compared is kept to choose from (the user's Q16) ------------------------------
def test_a_compared_outline_is_kept_on_the_board_by_the_model_that_made_it():
    """"Keep the SAM 2.1 one" was refused live: the compared view had left the 4 s ring. The comparison holds its view
    (the unsaved allowance) for 5 minutes, and keep= puts that arm's outline on the board, made by its execution; the
    app's earlier outline of that thing is replaced, the user's own never."""
    from orchestrator.capture import captures

    world, registry, user_obj, shown = setting_up()
    app_obj = run(tools.outline_at(world, world.latest_info.id, 600, 300, box=(560, 260, 640, 340), source="ai:test",
                                   label="stoma", status="likely"))
    compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny"], "object": app_obj.id})
    held = [c.id for c in captures(world).held()]
    world.ring.clear()  # the frame ring let the view go; the comparison still holds it
    said = compare(world, registry, {"keep": "SAM 2.1 tiny"})
    kept = [o for o in world.scene.objects.values() if (o.made_by or {}).get("implementation") == "sam2.1-tiny"]
    assert said.startswith("kept SAM 2.1 tiny's outline of the stoma on the board") and len(kept) == 1
    assert kept[0].made_by["origin"] == "compare" and kept[0].label == "stoma" and held
    assert app_obj.id not in world.scene.objects and user_obj.id in world.scene.objects  # the app's replaced, the user's kept
    assert not captures(world).held()  # a keep lets the view go
    with pytest.raises(ToolFailed) as nothing:
        compare(world, registry, {"keep": "EdgeTAM"})
    assert "there is no comparison from the last 5 minutes" in str(nothing.value)


def test_a_keep_names_one_of_the_compared_models_or_is_told_which_there_are():
    world, registry, obj, shown = setting_up()
    compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny"], "object": obj.id})
    with pytest.raises(ToolFailed) as which:
        compare(world, registry, {"keep": "micro-sam"})
    assert "its results (id = name): edgetam = EdgeTAM; sam2.1-tiny = SAM 2.1 tiny" in str(which.value)
    assert which.value.next == "pick one of these and try again"
    said = compare(world, registry, {"keep": "edgetam"})
    assert said.startswith("kept EdgeTAM's outline") and obj.id in world.scene.objects  # the user's click stays beside it


def test_a_cells_comparison_with_no_eyepiece_circle_is_framed_on_the_whole_view_and_an_arm_kept_is_shown(monkeypatch):
    """choosing.py's cells comparison said "not shown: this view has no eyepiece circle" and showed nothing: it is
    framed on the whole view now; keeping a cells arm shows its population."""
    world, _ = cells_world(monkeypatch)
    shown = []
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None, magnifier=SimpleNamespace(
        show_picture=lambda jpeg, label, object_id=None: shown.append(label) or "/api/picture/x")))
    said = compare(world, registry, {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"]})
    assert "no eyepiece circle" not in said and shown and shown[-1].startswith("Compared on the same view: Cellpose-SAM")
    acted = []

    async def act(services, args, by=None, ctx=None):
        acted.append(args)
        return f"showed {args['what']}"

    import orchestrator.brain.toolbox.present as present

    monkeypatch.setattr(present, "act", act)
    kept = compare(world, registry, {"keep": "micro-sam"})
    population = acted[-1]["what"]
    assert acted[-1]["do"] == "show" and kept.startswith(f"kept micro-sam (light microscopy)'s cells ({population})")


def test_a_cells_comparison_run_from_the_workspaces_tools_is_drawn_there_once(monkeypatch):
    """Will, 2026-10-20: each compared model's marks are drawn on the Stage in its own colour, by the workspace. A cells
    comparison the user ran by hand there gets no second, side-by-side picture; the agent's comparisons keep theirs."""
    import orchestrator.workspace_views as workspace_views
    from orchestrator.activity import BY_HAND

    monkeypatch.setattr(workspace_views, "link_execution", lambda store, origin, execution: None)
    world, _ = cells_world(monkeypatch)
    shown = []
    registry = Registry()
    add_choosing_tools(registry, SimpleNamespace(hub=world, publish_settings=None, magnifier=SimpleNamespace(
        show_picture=lambda jpeg, label, object_id=None: shown.append(label) or "/api/picture/x")))

    async def by_hand(workspace: bool):
        ticket = issue(world, "command")
        if workspace:  # admitted as the workspace's own run (commands.admit_workspace_command); its store is not this test's
            ticket.workspace_origin, ticket.workspace_store = {"session": "p1", "input": {"source": "live"}}, lambda: None
        token = BY_HAND.set(True)
        try:
            with using(ticket):
                return await registry.tools["compare_models"].handler(
                    ToolContext("chat", 1, None, ""), {"operation": "cells", "implementations": ["Cellpose-SAM", "micro-sam"]})
        finally:
            BY_HAND.reset(token)

    said = run(by_hand(workspace=True))
    assert not shown and "magnifier" not in said and "found by both" in said  # the facts are said; nothing claimed shown
    run(by_hand(workspace=False))  # the older side bar draws no Stage: its picture stays
    assert shown and shown[-1].startswith("Compared on the same view: ")


def test_exact_comparison_refuses_unavailable_arm_instead_of_silently_dropping_it():
    world, registry, obj, shown = setting_up()
    before = len(world.gpu.calls)
    with pytest.raises(ToolFailed, match="available, distinct models"):
        compare(world, registry, {"implementations": ["EdgeTAM", "SAM 2.1 tiny", "unknown-model"],
                                  "object": obj.id, "exact": True})
    assert len(world.gpu.calls) == before and not shown


def test_keep_requires_exact_comparison_token_and_origin_project():
    from orchestrator.brain.toolbox.choosing import Compared
    from orchestrator.capture import captures

    world, registry, obj, _ = setting_up()
    compared = Compared(world)
    payload = {"operation": "outline", "capture": captures(world).get(obj.origin),
               "arms": [{"impl": "edgetam", "label": "EdgeTAM", "polygons": obj.polygons}]}
    compared.hold(payload)
    old_token = compared.token
    compared.hold(payload)
    compared.session = "project-one"
    ctx = ToolContext("system", 0, None, "keep", asked=SimpleNamespace(session="project-one"))
    with pytest.raises(ToolFailed, match="changed or expired"):
        run(compared.keep(None, ctx, "edgetam", expected=old_token))
    ctx.asked = SimpleNamespace(session="project-two")
    with pytest.raises(ToolFailed, match="changed or expired"):
        run(compared.keep(None, ctx, "edgetam", expected=compared.token))
    compared.release()
    assert not captures(world).held()
