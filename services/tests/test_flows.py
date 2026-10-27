"""M1's acceptance flows that need their own pieces (docs/design/2026-10-06-captures-operations-evidence.md,
"M1 acceptance"): flow 4, "show that cell in HD", a close-up of a cell of a population, linked to it."""
import asyncio
from types import SimpleNamespace

from orchestrator.brain.registry import Registry, ToolContext
from orchestrator.brain.toolbox.magnifier import add_magnifier_tools
from orchestrator.population import layer_of
from orchestrator.ticket import issue, using
from tests.test_population import cells, world  # noqa: F401  (the fixture)


from orchestrator.magnifier import Magnifier


def zoom_tool(world, magnifier):
    registry = Registry()
    add_magnifier_tools(registry, SimpleNamespace(hub=world, magnifier=magnifier, agent=None, research=None, desk=None,
                                                  presenter=None, log=None, board=None))
    return registry.tools["zoom"]


def ask(world, tool, args):
    async def go():
        with using(issue(world, "turn")):
            return await tool.handler(ToolContext("chat", 1, None, "show that cell in HD"), args)
    return asyncio.run(go())


def test_show_that_cell_in_hd_magnifies_the_selected_cell_from_its_own_mask(world):
    population, _ = cells(world)
    layer = layer_of(world)
    asyncio.run(layer.check())
    member = next(m for m in population.live() if layer.placement.get(m.id, {}).get("state") == "verified")
    layer.selected = member.id
    magnifier = Magnifier(world)
    said = ask(world, zoom_tool(world, magnifier), {"object": "that cell"})
    obj = world.scene.objects[magnifier.object_id]
    assert said.startswith("opened the magnifier on") and (obj.made_by or {}).get("member") == member.id
    assert (obj.made_by or {}).get("how") == "measured"  # its own mask, not a new segmentation


def test_a_cell_not_verified_in_place_is_shown_on_the_view_it_was_made_on(world):
    population, _ = cells(world)
    member = population.live()[0]
    world.frame(0.0, 0.0, epoch=1)  # continuity lost: nothing is verified in place
    magnifier = Magnifier(world)
    said = ask(world, zoom_tool(world, magnifier), {"object": member.id})
    assert "on the view it was made on" in said and "not verified in place" in said
    assert magnifier.pictures and "real pixels, not the live view" in magnifier.picture_now()["label"]


def test_a_cell_not_in_place_counts_as_the_requests_zoom_and_one_after_another_zoom_is_refused(world):
    """The partner's review of G4, NOTE 3: zoom's cell branch skipped the one-magnifier rule. A cell not in place on
    the live view can only be shown alone (on the view it was made on), so after another zoom in the same request it
    is refused, with what to do; shown alone, it is the request's zoom."""
    from orchestrator.brain.registry import ToolFailed

    population, _ = cells(world)
    first, second = population.live()[0], population.live()[1]
    world.frame(0.0, 0.0, epoch=1)  # continuity lost: nothing is verified in place
    magnifier = Magnifier(world)
    tool = zoom_tool(world, magnifier)
    request = ToolContext("chat", 1, None, "show me these two cells up close")

    async def go(args):
        with using(issue(world, "turn")):
            return await tool.handler(request, args)

    assert "on the view it was made on" in asyncio.run(go({"object": first.id}))
    assert len(request.magnified) == 1 and len(magnifier.pictures) == 1
    try:
        asyncio.run(go({"object": second.id}))
        refused = None
    except ToolFailed as exc:
        refused = exc
    assert refused is not None and "the magnifier shows one thing at a time and already shows" in str(refused)
    assert refused.next.endswith("on its own next") and len(magnifier.pictures) == 1  # the first stays shown


def test_a_name_the_user_confirmed_is_never_relabelled_by_a_highlight():
    """Invariant 14 (found reviewing wpulier/HookeUI#157): highlight(label=) wrote over a confirmed name."""
    import asyncio
    from types import SimpleNamespace

    from orchestrator import tools
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.ticket import issue, using
    from tests.test_apply import World

    world = World()
    obj = asyncio.run(tools.outline_at(world, world.latest_info.id, 300, 200, box=(280, 180, 320, 220), source="click"))
    world.scene.update(obj.id, label="guard cell", status="confirmed")
    registry = Registry()
    add_highlight(registry, world, sight=SimpleNamespace(view=object()), outline_seen=None)

    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["highlight"].handler(ToolContext("chat", 1, None, "highlight that as a stoma"),
                                                             {"things": [obj.id], "label": "stoma"})
    asyncio.run(go())
    assert world.scene.objects[obj.id].label == "guard cell"


def test_zooming_on_one_cell_again_makes_no_second_outline_and_a_later_click_takes_over(world):
    """M1.5c review item 1: three zooms on one cell made three outlines, and a cell selected earlier took over a
    later "zoom in more" on another outline."""
    population, _ = cells(world)
    layer = layer_of(world)
    asyncio.run(layer.check())
    member = next(m for m in population.live() if layer.placement.get(m.id, {}).get("state") == "verified")
    magnifier = Magnifier(world)
    tool = zoom_tool(world, magnifier)
    for _ in range(3):
        ask(world, tool, {"object": member.id})
    measured = [o for o in world.scene.objects.values() if (o.made_by or {}).get("member") == member.id]
    assert len(measured) == 1
    layer.selected = member.id  # the cell clicked earlier...
    world.scene.select(measured[0].id)  # ...then an outline selected: the cell is no longer what "that" means
    assert layer.selected is None
    magnifier.open, magnifier.object_id = True, measured[0].id
    ask(world, tool, {"level": "more"})  # no object, the magnifier open: what it shows
    assert magnifier.object_id == measured[0].id and len(world.scene.objects) == 1


def test_highlighting_a_whole_population_shows_its_layer_not_fifteen_outlines(world):
    """Acceptance flow 5 on footage: "pull up the cells from earlier" became highlight(of=e1), which outlined 15
    of 140 cells with "Outlined" labels and said "125 more fit"."""
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.screen import Screen
    from tests.test_screen import Studio

    population, _ = cells(world)
    layer_of(world).hide()
    screen = Screen()
    world.screen = screen
    studio = Studio(screen)
    sent = world.broadcast

    def broadcast(message):
        sent(message)
        studio.hear(message)

    world.broadcast = screen.broadcast = layer_of(world).broadcast = broadcast
    studio.report()
    registry = Registry()
    add_highlight(registry, world, sight=SimpleNamespace(view=None), outline_seen=None)

    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["highlight"].handler(ToolContext("chat", 1, None, "pull up the cells from earlier"),
                                                             {"of": population.id})
    said = asyncio.run(go())
    assert said.startswith(f"{population.title} shown") and layer_of(world).shown == population.id
    assert not [o for o in world.scene.objects.values() if (o.made_by or {}).get("how") == "measured"]


def test_a_cell_is_placed_live_only_when_its_pixels_were_found_on_that_very_frame(world):
    """M1.5c re-check N2: promotion trusted the layer's last verdict, made on an earlier frame."""
    from orchestrator.population import promote

    population, _ = cells(world)
    layer = layer_of(world)
    asyncio.run(layer.check())
    member = next(m for m in population.live() if layer.placement.get(m.id, {}).get("state") == "verified")
    world.frame(6.0, 3.0)  # a newer frame: the last check was on another one
    assert layer.placement[member.id]["frame"] != world.latest_info.id
    (obj,) = asyncio.run(promote(world, population, [member.id]))
    assert layer.placement[member.id]["frame"] == world.latest_info.id  # checked again on the frame it is placed on
    assert obj.anchor_frame == world.latest_info.id and obj.track == "tracking"


def test_that_cell_means_the_cell_in_the_magnifier_and_a_cell_of_another_population_is_not_selected(world):
    """Re-check N3 ("zoom in on that cell" after its close-up: "can't find 'that cell'") and S3."""
    from orchestrator.brain.context import selected_cell

    population, _ = cells(world)
    layer = layer_of(world)
    asyncio.run(layer.check())
    member = next(m for m in population.live() if layer.placement.get(m.id, {}).get("state") == "verified")
    layer.selected = member.id
    magnifier = Magnifier(world)
    tool = zoom_tool(world, magnifier)
    ask(world, tool, {"object": "that cell"})
    first = magnifier.object_id
    ask(world, tool, {"object": "that cell", "level": "more"})
    assert magnifier.object_id == first and len(world.scene.objects) == 1
    layer.selected = member.id
    second, _ = cells(world)  # another run shown: the cell selected before is not one of its cells
    assert layer.selected is None and selected_cell(world) == ""


def test_a_cell_that_swam_off_is_outlined_but_not_drawn_and_is_re_found_by_its_own_pixels(world):
    """M1.5c re-check N2: a cell whose same-frame check failed was drawn anyway (the generic re-check matched its
    surroundings, a 72 px crop around a 25 px cell); its template is now the cell's own pixels."""
    import cv2
    import numpy as np
    from orchestrator.population import promote

    population, _ = cells(world)
    layer = layer_of(world)
    asyncio.run(layer.check())
    member = next(m for m in population.live() if layer.placement.get(m.id, {}).get("state") == "verified")
    info = world.frame(0.0, 0.0)  # the slide still; the cell swims off: its place now holds other pixels
    image = cv2.imdecode(np.frombuffer(world.ring[info.id], np.uint8), cv2.IMREAD_GRAYSCALE)
    x0, y0, x1, y1 = [int(v) for v in member.box]
    image[y0 - 6:y1 + 6, x0 - 6:x1 + 6] = np.random.default_rng(1).integers(0, 255, (y1 - y0 + 12, x1 - x0 + 12))
    world.ring[info.id] = cv2.imencode(".jpg", image)[1].tobytes()
    (obj,) = asyncio.run(promote(world, population, [member.id]))
    assert obj.track == "refinding" and "did not match" in obj.unplaced  # kept, not drawn, and why
    th, tw = obj.template.shape[:2]
    assert th <= (y1 - y0) + 2 * 4 + 2 and tw <= (x1 - x0) + 2 * 4 + 2  # the cell's own pixels, cut tight


def test_that_cell_after_another_cell_is_clicked_is_the_cell_clicked(world):
    """M1.5c re-check R1: after "show that cell in HD" on one cell, clicking another and asking again acted on the
    first (its outline in the magnifier)."""
    population, _ = cells(world)
    layer = layer_of(world)
    asyncio.run(layer.check())
    first, second = [m for m in population.live() if layer.placement.get(m.id, {}).get("state") == "verified"][:2]
    magnifier = Magnifier(world)
    tool = zoom_tool(world, magnifier)
    layer.selected = first.id
    ask(world, tool, {"object": "that cell"})
    assert (world.scene.objects[magnifier.object_id].made_by or {}).get("member") == first.id
    layer.selected = second.id  # the user clicks another cell
    ask(world, tool, {"object": "that cell"})
    assert (world.scene.objects[magnifier.object_id].made_by or {}).get("member") == second.id


def test_highlighting_outlined_things_with_rest_highlights_them_and_says_where_fading_goes():
    """M1.5c re-check SF3: things plus rest failed ("which cells to keep?")."""
    from orchestrator import tools
    from orchestrator.brain.toolbox.view import add_highlight
    from tests.test_apply import World

    world = World()
    obj = asyncio.run(tools.outline_at(world, world.latest_info.id, 300, 200, box=(280, 180, 320, 220), source="ai:look"))
    registry = Registry()
    add_highlight(registry, world, sight=SimpleNamespace(view=object()), outline_seen=None)

    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["highlight"].handler(ToolContext("chat", 1, None, "highlight that and fade the rest"),
                                                             {"things": [obj.id], "rest": "fade"})
    said = asyncio.run(go())
    assert said.startswith("outlined") and "nothing else was faded" in said
