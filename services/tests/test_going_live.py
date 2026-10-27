"""What going live on 2026-10-07 found (the design doc's "Going live"): the words for outlines made from cells, the
zoom levels the model is offered (G1), and what colour the cells are drawn in, picking them by it, and what `present`
does with what it is given (G3)."""
from types import SimpleNamespace

import pytest

from orchestrator.brain.toolbox.common import describe
from orchestrator.brain.toolbox.view import outlined_words
from orchestrator.population import select
from tests.test_apply import run
from tests.test_population import cells, world  # noqa: F401  (the fixture)
from tests.waiting import HANG_S


def outline(label="", member=None, operation="cells"):
    return SimpleNamespace(label=label, made_by={"member": member, "operation": operation} if member else {}, shown=True)


def test_cells_outlined_are_said_by_their_ids_and_unnamed_outlines_are_counted_once():
    """Flow 5: "Highlight the five largest cells" said "outlined that outline" five times."""
    assert outlined_words([outline(member=f"c{n}") for n in (4, 12, 7)]) == "outlined cell c4, cell c12, cell c7"
    assert outlined_words([outline(), outline(), outline("stoma")]) == "outlined the stoma, 2 outlines not named yet"
    assert outlined_words([outline()]) == "outlined that outline"
    assert describe(outline("guard cell", member="c3")) == "the guard cell"  # a name the user or model gave comes first
    assert describe(outline(member="c9", operation="find-like")) == "match c9"  # a match is not called a cell


def test_the_zoom_tool_says_fit_is_the_whole_thing_and_max_only_when_asked():
    """Flow 4: "show that cell in HD" was given "max" (a few camera pixels across) and "zoom out so I see the whole
    cell" was given "less"."""
    from orchestrator.brain.registry import Registry
    from orchestrator.brain.toolbox.magnifier import add_magnifier_tools

    registry = Registry()
    add_magnifier_tools(registry, SimpleNamespace(hub=SimpleNamespace(), magnifier=SimpleNamespace(), log=None, agent=None, scale=None))
    words = registry.tools["zoom"].description
    assert '"in HD"' in words and '"the whole cell"' in words and "only when asked for the closest" in words
    assert "enlarged past the camera's detail, and the answer says so" in words  # "fit" never promises detail it lacks


# --- G3: what colour the cells are drawn in, picking them by it, and what present does with what it is given ---
HEAT_CASES = [(0, "rgb(59, 130, 246)"), (0.25, "rgb(30, 171, 186)"), (0.5, "rgb(135, 194, 94)"), (2.5 / 3, "rgb(245, 136, 45)"),
              (1, "rgb(239, 68, 68)"), (1.5, "rgb(239, 68, 68)"), (-1, "rgb(59, 130, 246)")]


def test_the_server_draws_a_member_as_the_studio_does():
    """The same cases as apps/studio/src/population.test.ts: the palette, the ramp and the grey are the contract's
    (one table, read by both), and a member's colour under each style is the studio's memberLook. 2.5 / 3 lands on a
    half (244.5): JavaScript rounds it up, as the server must."""
    from orchestrator.colours import PALETTE, drawn, heat
    from orchestrator.contract import OVERLAY
    from orchestrator.evidence import colour_of

    assert PALETTE == OVERLAY["memberColours"]["palette"]
    assert [heat(t) for t, _ in HEAT_CASES] == [rgb for _, rgb in HEAT_CASES]
    assert colour_of("c6") == 3  # the studio's case gives c6 the index the wire gives it
    assert drawn("c6", {"opacity": 1, "colour": "id"}) == "#e74c3c"
    assert drawn("c6", {"opacity": 1, "colour": "uniform", "value": "#ffdc64"}) == "#ffdc64"
    assert drawn("c6", {"opacity": 1, "colour": "column", "column": "areaPx", "values": {"c6": 0.25}}) == "rgb(30, 171, 186)"
    assert drawn("c6", {"opacity": 1, "colour": "column", "column": "areaPx", "values": {}}) == "#8a93a6"
    assert drawn("c6", {"opacity": 1, "colour": "category", "column": "group", "values": {"c6": 2}}) == "#38c3f7"
    assert drawn("c6", {"opacity": 1, "colour": "category", "column": "group", "values": {"c6": 14}}) == "#38c3f7"
    assert drawn("c6", {"opacity": 1, "colour": "category", "column": "group", "values": {}}) == "#8a93a6"
    assert drawn("c6", {"opacity": 1, "colour": "id", "emphasis": {"members": [], "rest": "fade"}}) == "#e74c3c"
    assert drawn("c6", {"opacity": 1, "colour": "id", "emphasis": {"members": [], "rest": "hide"}}) is None


def test_every_drawn_colour_has_a_plain_name_and_the_palette_keeps_its_own_names():
    """The names `present` colours by are the names the same colours are called by (one vocabulary both ways)."""
    from orchestrator.brain.toolbox.present import COLOURS
    from orchestrator.colours import PALETTE, heat, name_of

    assert {n: name_of(c) for n, c in COLOURS.items()} == {n: n for n in COLOURS}
    # Each palette colour its own name: none merged into another by code (the review of G3, SF1: three were "pink").
    names = [name_of(c) for c in PALETTE]
    assert names == ["pink", "green", "cyan", "red", "orange", "hot pink", "teal", "yellow", "blue", "coral", "magenta",
                     "lime"] and len(set(names)) == len(PALETTE)
    assert [name_of(heat(t)) for t in (0, 0.2, 0.3, 0.5, 0.7, 0.85, 1)] == ["blue", "cyan", "teal", "green", "yellow",
                                                                             "orange", "red"]
    assert name_of("#8a93a6") == "grey" and name_of("rgb(250, 250, 250)") == "white" and name_of("#101010") == "black"


def test_the_models_are_told_how_the_shown_cells_are_coloured_and_nothing_when_none_are_shown(world):
    """"What colour are the cells outlined in?" was answered "a bright color" (the brain) and "magenta or cyan"
    (the voice): neither was told. The Evidence line (scene table, fast pass and view note alike) says it."""
    from orchestrator.population import evidence_lines, layer_of

    cells(world)
    layer = layer_of(world)
    assert "; shown over the live view, each in its own colour, 0 verified" in evidence_lines(world)[0]
    layer.style = {"opacity": 1.0, "colour": "uniform", "value": "#ffdc64"}
    assert "; shown over the live view, all yellow, " in evidence_lines(world)[0]
    layer.style = {"opacity": 1.0, "colour": "column", "column": "areaPx", "values": {}}
    assert "; shown over the live view, coloured by size, blue (low) to red (high), " in evidence_lines(world)[0]
    layer.style = {"opacity": 1.0, "colour": "category", "column": "group", "values": {},
                   "legend": {"label": "look groups (DINOv2 small)", "categories": []}}
    assert "; shown over the live view, coloured by look groups (DINOv2 small), one colour each, " in evidence_lines(world)[0]
    layer.hide()
    assert evidence_lines(world) and not any("colour" in line for line in evidence_lines(world))


def area_style(population):
    """The layer coloured by size, as present's colour verb makes it (each member's area along the range, 0-1)."""
    areas = {m.id: population.value(m.id, "areaPx") for m in population.live()}
    low, high = min(areas.values()), max(areas.values())
    return {"opacity": 1.0, "colour": "column", "column": "areaPx",
            "values": {k: round((v - low) / (high - low), 3) for k, v in areas.items()}}


def checked(world):
    """Cells outlined and checked in place, as the layer does every 0.3 s while shown: all six drawn."""
    from orchestrator.population import layer_of

    population, _ = cells(world)
    layer = layer_of(world)
    run(layer.check())
    assert layer.verified_count() == (6, 6)
    return population, layer


def unverify(layer, *ids):
    for mid in ids:
        layer.placement[mid] = {**layer.placement[mid], "state": "unverified", "why": "its pixels did not match"}


def test_the_red_ones_are_exactly_the_members_drawn_red_in_the_style_shown(world):
    """"Can you highlight all the red ones?" went to the vision model, which sees the camera's picture and never the
    app's outlines ("no red elements present"). The colour each member is drawn in is a selector now, with the other
    colours drawn as facts beside it."""
    from orchestrator.brain.registry import ToolFailed

    population, layer = checked(world)
    # Each in its own colour (by id): c6 is the one drawn red.
    notes = []
    chosen, how = select(population, where={"colour": "red"}, hub=world, notes=notes)
    assert [m.id for m in chosen] == ["c6"] and how == "drawn red"
    assert notes == ["also drawn: lime 2, magenta 2, pink 1"]  # the model judges what the user meant (SF1)
    assert [m.id for m in select(population, where={"shown color": "Red"}, hub=world)[0]] == ["c6"]
    assert sorted(m.id for m in select(population, where={"colour": ["lime", "magenta"]}, hub=world)[0]) == ["c1", "c2", "c4", "c5"]
    with pytest.raises(ToolFailed) as blue:  # none drawn blue: what is drawn is said, the model decides what was meant
        select(population, where={"colour": "blue"}, hub=world)
    assert str(blue.value) == f"none of {population.title} is drawn blue now; they are drawn lime 2, magenta 2, pink 1, red 1"
    # Coloured by size: red is the largest, blue the smallest; nothing is drawn by its own colour then.
    layer.style = area_style(population)
    largest = max(population.live(), key=lambda m: population.value(m.id, "areaPx"))
    smallest = min(population.live(), key=lambda m: population.value(m.id, "areaPx"))
    assert [m.id for m in select(population, where={"colour": "red"}, hub=world)[0]] == [largest.id]
    assert smallest.id in [m.id for m in select(population, where={"colour": "blue"}, hub=world)[0]]
    with pytest.raises(ToolFailed) as none:
        select(population, where={"colour": "magenta"}, hub=world)
    assert "is drawn magenta now; they are drawn " in str(none.value) and "red 1" in str(none.value)
    # The top of the ramp: "red" is the top cell, and the orange ones beside it are said, not decided (SF1).
    layer.style = {"opacity": 1.0, "colour": "column", "column": "areaPx",
                   "values": {"c5": 1.0, "c2": 0.85, "c3": 0.8, "c1": 0.1, "c4": 0.0, "c6": 0.05}}
    notes = []
    assert [m.id for m in select(population, where={"colour": "red"}, hub=world, notes=notes)[0]] == ["c5"]
    assert notes == ["also drawn: blue 3, orange 2"]
    # By a category (the look groups, K1): each group's palette colour in the legend's order, grey without one.
    layer.style = {"opacity": 1.0, "colour": "category", "column": "group", "values": {"c1": 3, "c2": 0}}
    assert [m.id for m in select(population, where={"colour": "red"}, hub=world)[0]] == ["c1"]
    assert sorted(m.id for m in select(population, where={"colour": "grey"}, hub=world)[0]) == ["c3", "c4", "c5", "c6"]
    assert sorted(m.id for m in select(population, where={"colour": "gray"}, hub=world)[0]) == ["c3", "c4", "c5", "c6"]
    # One colour for all: a colour nothing is drawn in says which colour they are.
    layer.style = {"opacity": 1.0, "colour": "uniform", "value": "#ffdc64"}
    with pytest.raises(ToolFailed) as yellow:
        select(population, where={"colour": "red"}, hub=world)
    assert str(yellow.value).endswith("is drawn red now; they are drawn yellow 6") and yellow.value.next == "say one of those colours"
    # A selection kept with the rest hidden: the hidden ones are drawn in no colour.
    layer.style = {"opacity": 1.0, "colour": "id", "emphasis": {"members": ["c1"], "rest": "hide"}}
    with pytest.raises(ToolFailed) as hidden:
        select(population, where={"colour": "red"}, hub=world)
    assert str(hidden.value).endswith("they are drawn magenta 1")
    # The layer hidden: no cell is drawn in any colour.
    layer.hide()
    with pytest.raises(ToolFailed) as off:
        select(population, where={"colour": "red"}, hub=world)
    assert str(off.value) == "no cells are shown now, so none is drawn in any colour"


def test_a_cell_not_drawn_now_is_never_called_drawn_and_is_said_apart(world):
    """The review of G3, B1: with c6 not verified in place the Evidence line says "1 unverified (not drawn)", while
    the pick said "c6 drawn red". Drawn means drawn now: verified in place, on a layer not faded out."""
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.population import evidence_lines

    population, layer = checked(world)
    unverify(layer, "c6")
    assert "5 verified in place, 1 unverified (not drawn" in evidence_lines(world)[0]
    with pytest.raises(ToolFailed) as red:
        select(population, where={"colour": "red"}, hub=world)
    assert str(red.value) == (f"none of {population.title} is drawn red now (1 would be, but it is not in place on the "
                              "live view now); they are drawn lime 2, magenta 2, pink 1")
    unverify(layer, "c4")  # one of the two magenta ones
    notes = []
    chosen, how = select(population, where={"colour": "magenta"}, hub=world, notes=notes)
    assert [m.id for m in chosen] == ["c1"] and how == "drawn magenta"
    assert notes == ["also drawn: lime 2, pink 1", "1 more would be magenta but it is not in place on the live view now"]
    # Faded out: nothing is drawn, whatever its colour.
    layer.style = {**layer.style, "opacity": 0.0}
    with pytest.raises(ToolFailed) as faded:
        select(population, where={"colour": "magenta"}, hub=world)
    assert str(faded.value) == (f"none of {population.title} is drawn on the live view now (2 would be, but the layer is "
                                "faded out)") and faded.value.next == "fade the cells in again, then ask again"
    assert layer.drawable(world.latest_info) == []  # a snapshot draws none of them either


def test_a_chosen_cell_is_outlined_in_the_colour_it_was_chosen_by_on_its_captured_view(world, monkeypatch):
    """The review of G3, B1: coloured by size, c5 chosen as "drawn red" was shown on its captured view in its own
    palette colour (green). Its captured view and a snapshot outline it as the layer draws it."""
    import cv2

    from orchestrator.colours import bgr, drawn
    from orchestrator.population import captured_picture

    population, layer = checked(world)
    layer.style = area_style(population)
    largest = max(population.live(), key=lambda m: population.value(m.id, "areaPx"))
    drawn_in = []
    real = cv2.polylines
    monkeypatch.setattr(cv2, "polylines", lambda img, pts, closed, colour, *a, **k: (drawn_in.append(colour), real(img, pts, closed, colour, *a, **k))[1])
    assert captured_picture(world, population, [largest.id]) is not None
    from orchestrator.drawing import CASING_BGR

    lines = [c for c in drawn_in if tuple(c) != CASING_BGR]  # every line has a dark casing under it (drawing.py)
    assert lines == [bgr("rgb(239, 68, 68)")] == [bgr(drawn(largest.id, layer.style))]  # the red it was chosen by
    assert len(drawn_in) == 2
    colours = {tuple(c) for c, _ in layer.drawable(world.latest_info)}
    assert bgr("rgb(239, 68, 68)") in colours and bgr("#9acd32") not in colours  # the snapshot as the studio draws it
    layer.style = {**layer.style, "emphasis": {"members": [largest.id], "rest": "hide"}}
    assert len(layer.drawable(world.latest_info)) == 1  # the ones the style hides are not drawn


def test_colour_is_never_a_silent_measurement(world):
    """The review of G3, N3: "colour" as by or outliers gave 0 members, or every one; in query_population's columns,
    nothing. It is refused as a measurement, and listed as the colours drawn now."""
    import asyncio

    from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
    from orchestrator.brain.toolbox.population import add_population_tools

    population, layer = checked(world)
    for args in ({"by": "colour"}, {"outliers": "colour"}):
        with pytest.raises(ToolFailed) as refused:
            select(population, hub=world, **args)
        assert str(refused.value) == "the colour a cell is drawn in is not a measurement to sort or compare by"
    registry = Registry()
    add_population_tools(registry, SimpleNamespace(hub=world, log=None, execution=None))
    said = asyncio.run(registry.tools["query_population"].handler(ToolContext("chat", 1, None, "which colours?"),
                                                                   {"columns": ["colour"]}))
    assert "\ndrawn now: lime 2, magenta 2, pink 1, red 1" in said
    unverify(layer, "c6")
    said = asyncio.run(registry.tools["query_population"].handler(ToolContext("chat", 1, None, "which colours?"),
                                                                   {"columns": ["colour"], "where": {"colour": "pink"}}))
    assert "drawn now: lime 2, magenta 2, pink 1; 1 not drawn (not in place on the live view now)" in said
    assert "also drawn: lime 2, magenta 2" in said


def test_another_population_shown_is_not_the_one_drawn(world):
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.population import layer_of

    first, _ = cells(world)
    second, _ = cells(world)  # shown in its place
    assert layer_of(world).shown == second.id
    with pytest.raises(ToolFailed) as other:
        select(first, where={"colour": "red"}, hub=world)
    assert str(other.value) == f"{first.title} is not shown now, so it is drawn in no colour"


def test_the_find_tool_says_it_never_sees_the_apps_outlines():
    """Hands sent "the red ones" to find, whose vision model sees the camera's picture only."""
    from orchestrator.brain.registry import Registry
    from orchestrator.brain.toolbox.finding import add_finding_tools
    from orchestrator.brain.toolbox.view import add_highlight

    registry = Registry()
    add_finding_tools(registry, SimpleNamespace(hub=SimpleNamespace(), agent=SimpleNamespace(look_in=None), board=None,
                                                segment_all=None))
    add_highlight(registry, SimpleNamespace(), SimpleNamespace(), lambda *a: None)
    assert registry.tools["find"].description.endswith(
        "It sees the camera's picture only, never the outlines the app draws (by their colour: highlight where).")
    assert '{"colour": "red"}: as drawn now' in registry.tools["highlight"].params["properties"]["where"]["description"]


def test_present_never_drops_what_it_was_given_without_a_word(world):
    """Live, 16:10:50: present(what="outline", do="opacity", value="50%", by="area", top=1, order="desc") faded every
    app outline and said "the app's outlines at 50% opacity", by, top and order unused. Refused now, with what was not
    used, and nothing changed (the conservative choice: fading all of them was not asked)."""
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.population import layer_of
    from tests.test_screen import present, setup

    registry, screen, studio = setup(world)
    population, layer = checked(world)
    studio.items["outlines"] = {"id": "outlines", "kind": "outlines", "visible": True, "opacity": 1.0, "z": 2}
    studio.report()
    with pytest.raises(ToolFailed) as part:
        present(registry, world, what="outlines", do="opacity", value="50%", by="area", top=1, order="desc")
    assert str(part.value) == ("the app's outlines can't be faded or dimmed for only some (by area; top 1; order desc): "
                               "keeping some and fading the rest is for the cells' layer, so nothing was changed")
    assert studio.items["outlines"]["opacity"] == 1.0  # nothing faded
    with pytest.raises(ToolFailed) as unknown:
        present(registry, world, what=population.id, do="colour", value="size", colour="red")
    assert str(unknown.value) == ("colour red is not something a change on screen takes: give it as value (red), so "
                                  "nothing was changed")
    assert layer_of(world).style.get("colour") == "id"  # not coloured by size either
    with pytest.raises(ToolFailed) as over:
        present(registry, world, what=population.id, do="opacity", value="50%", over=True)
    assert str(over.value) == ("over is for putting something over the view, which is moving or resizing it, not faded "
                               "or dimmed, so nothing was changed")
    with pytest.raises(ToolFailed) as column:
        present(registry, world, what=population.id, do="opacity", value="50%", column="size")
    assert str(column.value) == "column size is for colouring them, not faded or dimmed, so nothing was changed"
    # The verb that takes a selection uses all of it, and says why these, with the other colours drawn as facts.
    said = present(registry, world, what=population.id, do="emphasise", where={"colour": "red"}, rest="fade",
                   over=False)  # a flag left off is nothing asked
    assert said == f"{population.title} 1 kept (drawn red), the rest faded; also drawn: lime 2, magenta 2, pink 1"
    assert layer_of(world).style["emphasis"] == {"members": ["c6"], "rest": "fade"}


def test_present_refuses_a_value_or_a_rect_its_verb_does_not_take_and_says_which_argument(world):
    """The review of G3: SF2 (show with value red was "shown" in its own colours; opacity with a rect was "done") and
    SF3 (colour by size refused as "for only some"; "show the five largest" told it is for the cells' layer when it
    is; opacity=0.5 as a key with no usable step; top 0 or a lone order taken as a selection)."""
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.population import layer_of
    from tests.test_screen import present, setup

    registry, screen, studio = setup(world)
    population, layer = checked(world)
    studio.report()
    with pytest.raises(ToolFailed) as value:
        present(registry, world, what=population.id, do="show", value="red")
    assert str(value.value) == "value red is for fading or colouring, not shown, so nothing was changed"
    assert layer.style.get("colour") == "id"
    with pytest.raises(ToolFailed) as rect:
        present(registry, world, what=population.id, do="opacity", value="50%", rect=[1, 2, 3, 4])
    assert str(rect.value) == "rect 1, 2, 3, 4 is for moving or resizing, not faded or dimmed, so nothing was changed"
    assert layer.style.get("opacity") == 1.0
    with pytest.raises(ToolFailed) as some:
        present(registry, world, what=population.id, do="show", by="area", top=5)
    assert str(some.value) == ("the cell outlines can't be shown for only some (by area; top 5): keeping some and fading "
                               "the rest is the verb emphasise, so nothing was changed")
    assert some.value.next == "say which cells to keep and fade the rest, or say it for all of them"
    with pytest.raises(ToolFailed) as key:
        present(registry, world, what=population.id, do="opacity", opacity=0.5)
    assert str(key.value) == ("opacity 0.5 is not something a change on screen takes: give it as value (0.5), so nothing "
                              "was changed") and key.value.next == "say it again with the amount or colour as the value"
    # Defaults are nothing asked: top 0, an order with nothing to sort by.
    assert present(registry, world, what=population.id, do="opacity", value="50%", top=0) == f"{population.title} at 50% opacity"
    assert present(registry, world, what=population.id, do="opacity", value="60%", order="desc") == f"{population.title} at 60% opacity"
    # The colour verb's by is what it colours by, as value is.
    assert present(registry, world, what=population.id, do="colour", by="size", order="desc") == (
        f"{population.title} coloured by size, with a legend")
    assert layer_of(world).style["column"] == "areaPx"


class KeepsClear:
    """The studio's magnifier as placeAway places it: a place that would cover what it magnifies (OBJECT, workspace
    px) is not taken unless it was asked for over the view (`over`); the report says whether it covers it."""

    OBJECT = (500, 300, 700, 500)

    def __init__(self, studio):
        self.studio, self.hear = studio, studio.hear
        studio.hear = self.placed

    def placed(self, message):
        if message.get("type") == "present" and message.get("id") == "magnifier" and "rect" in message["set"]:
            x, y, w, h = message["set"]["rect"]
            ox0, oy0, ox1, oy1 = self.OBJECT
            covers = x < ox1 and ox0 < x + w and y < oy1 and oy0 < y + h
            if covers and not message["set"].get("over"):
                message = {**message, "set": {k: v for k, v in message["set"].items() if k != "rect"}}  # stays clear
            self.studio.items["magnifier"]["covers"] = covers and bool(message["set"].get("rect"))
        self.hear(message)


def test_a_place_asked_for_over_the_view_is_taken_and_one_nobody_asked_for_keeps_clear(world):
    """Live: "can you overlay it on our view?", "yes", then "It won't cover what it's magnifying. Say another place".
    Keeping clear is the rule for places nobody asked for, never a refusal of the user's own request."""
    from orchestrator import tools
    from orchestrator.brain.registry import ToolFailed
    from tests.test_apply import run as run_
    from tests.test_screen import present, setup

    registry, screen, studio = setup(world)
    obj = run_(tools.outline_at(world, world.latest_info.id, 300, 200, box=(280, 180, 320, 220), source="click"))
    world.scene.update(obj.id, label="stoma")
    KeepsClear(studio)
    studio.items["live"]["rect"] = [100, 50, 1000, 700]
    studio.items["magnifier"].update(open=True, rect=[10, 10, 300, 200], shows=obj.id)
    studio.report()
    # A place nobody asked to have over it: kept clear, and said so with how to ask.
    with pytest.raises(ToolFailed) as clear:
        present(registry, world, what="magnifier", do="move", rect=[450, 250, 300, 200])
    assert str(clear.value) == "the magnifier did not go there: unless asked, it keeps clear of what it magnifies"
    assert clear.value.next == "say if you want it over it anyway, or another place or size"
    assert studio.items["magnifier"]["rect"] == [10, 10, 300, 200]
    # Asked for over the view: there it goes, confirmed by the report, and what it covers is said.
    said = present(registry, world, what="magnifier", do="move", over=True)
    assert studio.items["magnifier"]["rect"] == [450, 300, 300, 200]  # centred on the live view, as wide as it was
    assert said == "put the magnifier over the view, as you asked; it covers the stoma"
    assert screen.owner("magnifier", "rect").startswith("request:")


def test_an_asked_for_place_the_studio_did_not_show_is_never_told_to_ask_for_it(world):
    """The review of G3 (R10): with "over" asked and the studio not showing it, the refusal must not say "unless asked":
    it was asked."""
    from orchestrator.brain.registry import ToolFailed
    from tests.test_screen import present, setup

    registry, screen, studio = setup(world)
    hear = studio.hear

    def never_moves(message):  # applies the change, but the magnifier stays where it is
        if message.get("type") == "present" and message.get("id") == "magnifier":
            message = {**message, "set": {k: v for k, v in message["set"].items() if k != "rect"}}
        hear(message)

    studio.hear = never_moves
    studio.items["live"]["rect"] = [100, 50, 1000, 700]
    studio.items["magnifier"].update(open=True, rect=[10, 10, 300, 200])
    studio.report()
    with pytest.raises(ToolFailed) as refused:
        present(registry, world, what="magnifier", do="move", rect=[450, 250, 300, 200], over=True)
    assert str(refused.value) == "the magnifier is not moved on screen: the studio has not shown it"


def test_the_models_are_told_when_the_magnifier_covers_what_it_shows():
    """The review of G3, NOTE 2: `covers` was in the report and in no line a model reads."""
    from orchestrator.brain import context
    from orchestrator.screen import Screen

    screen = Screen()
    stoma = SimpleNamespace(id="o1", label="stoma")
    hub = SimpleNamespace(scene=SimpleNamespace(objects={"o1": stoma}, selected=None), screen=screen, layer=None)
    magnifier = SimpleNamespace(open=True, object_id="o1", sources=[{"kind": "live"}], index=0, level=1)
    brain = SimpleNamespace(hub=hub, services=SimpleNamespace(magnifier=magnifier), presenter=None)
    screen.report({"items": [{"id": "magnifier", "kind": "magnifier", "open": True, "shows": "o1", "covers": False}]})
    before = context._screen(brain, for_voice=True)
    subject = "the magnifier is open on o1 'stoma' (live zoom fitted)"
    assert before.startswith(subject) and 'covering it' not in before
    assert 'magnifier sources' in before  # P5's source context remains beside the covering-window fact.
    screen.report({"items": [{"id": "magnifier", "kind": "magnifier", "open": True, "shows": "o1", "covers": True}]})
    assert context._screen(brain, for_voice=True) == before.replace(subject, subject + ', covering it', 1)


def test_a_showings_put_back_of_the_magnifier_is_never_refused_for_covering_its_object(monkeypatch):
    """The review of G3, NOTE 5: the put-back was a plain move, so a user's own place over its object was refused on
    the way back, and became the agent's."""
    import asyncio

    from orchestrator import showing
    from orchestrator.brain.toolbox import present

    sent = []

    async def act(services, args, by=None, ctx=None):
        sent.append(args)
        return ""

    monkeypatch.setattr(present, "act", act)
    said = asyncio.run(showing._put_back(SimpleNamespace(), "magnifier", "rect", [500, 300, 300, 200], "move"))
    assert sent == [{"what": "the magnifier", "do": "move", "rect": [500, 300, 300, 200], "over": True}]
    assert said == "the magnifier where it was"


# --- G4: the magnifier is one window (2026-10-07 16:11:51 and 16:12:30) -------------------------------------------
def magnifier_world():
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.magnifier import Magnifier
    from orchestrator.scene import SceneObject
    from tests.test_magnifier import FakeHub, stoma

    hub = FakeHub([0])
    stoma(hub)  # o1
    hub.scene.add(SceneObject(id="o2", kind="highlight", polygons=[[[450, 150], [510, 150], [510, 210], [450, 210]]],
                              anchor_frame=0, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label="guard cell"))
    hub.scene.add(SceneObject(id="o3", kind="highlight", polygons=[[[100, 150], [160, 150], [160, 210], [100, 210]]],
                              anchor_frame=0, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label="pore"))
    magnifier, logged = Magnifier(hub), []
    registry = build(Services(hub=hub, agent=SimpleNamespace(log=logged.append), magnifier=magnifier))
    return hub, magnifier, registry, logged


def run_tool(registry, name, args, ctx):
    import asyncio

    async def go():
        r = registry.start(name, args, ctx)
        if r.task is not None:
            await r.task
        return r

    return asyncio.run(go())


def test_zoom_into_both_of_them_shows_them_side_by_side_and_says_so():
    """"Can you zoom into both of them": zoom(o3) then zoom(o4) in one request, the second replacing the first while the
    answer said both. The second is shown beside the first instead, in the one magnifier, and that is what is said."""
    from orchestrator.brain.registry import ToolContext

    hub, magnifier, registry, logged = magnifier_world()
    request = ToolContext("voice", 1, 0, "can you zoom into both of them")
    first = run_tool(registry, "zoom", {"object": "o1", "level": "more"}, request)
    second = run_tool(registry, "zoom", {"object": "o2", "level": "more"}, request)
    assert first.ok and first.summary.startswith("magnified the stoma ×2")
    assert second.ok and second.summary == ("the magnifier shows one thing at a time, so it shows a still picture of each, "
                                            "the stoma and the guard cell, side by side instead")
    shown = magnifier.sources[magnifier.index]
    assert magnifier.open and shown["kind"] == "image" and shown["label"] == "stoma and guard cell"  # both, on screen
    assert [(r["rule"], r["showing"], r["ran"]) for r in logged if r["kind"] == "override"] == [
        ("one-magnifier", "o1", {"compare": ["o1", "o2"]})]  # code decided it, not the model: logged
    # A third in the same request would replace the two: refused, with words and a next step.
    third = run_tool(registry, "zoom", {"object": "o3"}, request)
    assert third.ok is False and "it already shows the stoma and the guard cell" in third.summary
    assert "ask for the pore on its own next" in third.summary and magnifier.sources[magnifier.index]["label"] == "stoma and guard cell"
    # The same one again in the request, and another in a new request: zoom as ever.
    again = ToolContext("voice", 2, 0, "zoom in on the pore")
    assert run_tool(registry, "zoom", {"object": "o3"}, again).summary.startswith("opened the magnifier on the pore")
    assert run_tool(registry, "zoom", {"object": "o3", "level": "more"}, again).summary.startswith("magnified the pore")
    assert magnifier.object_id == "o3" and magnifier.sources[magnifier.index]["kind"] == "live"


def test_after_a_comparison_in_the_request_a_zoom_on_a_third_does_not_replace_the_two():
    import copy
    from orchestrator.brain.registry import ToolContext

    hub, magnifier, registry, _ = magnifier_world()
    request = ToolContext("chat", 1, 0, "compare these two and zoom into the pore")
    comparison = run_tool(registry, "compare", {"a": "o1", "b": "o2"}, request)
    assert comparison.ok
    assert [source['outline'] for source in comparison.values['captureSources']] == ['o1', 'o2']
    before = copy.deepcopy(magnifier.wire())
    third = run_tool(registry, "zoom", {"object": "o3"}, request)
    assert third.ok is False and "already shows the stoma and the guard cell" in third.summary
    assert magnifier.wire() == before  # P5's identified source comparison stays shown, including its provenance.


def test_a_second_zoom_after_the_request_closed_the_magnifier_is_a_plain_zoom():
    from orchestrator.brain.registry import ToolContext

    hub, magnifier, registry, _ = magnifier_world()
    request = ToolContext("chat", 1, 0, "zoom on the stoma, close it, then zoom on the guard cell")
    run_tool(registry, "zoom", {"object": "o1"}, request)
    run_tool(registry, "close_magnifier", {}, request)
    second = run_tool(registry, "zoom", {"object": "o2"}, request)
    assert second.ok and second.summary.startswith("opened the magnifier on the guard cell") and magnifier.object_id == "o2"


def test_zoom_exposes_independent_windows_and_compare_preserves_its_composite():
    """16:12:30, "make them two separate windows": compare ran and the answer, then the voice, said they were split into
    two separate windows. There is one magnifier, and it can be moved and resized (by its bar and grip, or `present`):
    the partner's review of G4 caught "no movable windows" as the prompt's own false words."""
    _, _, registry, _ = magnifier_world()
    assert "Each object gets its own window" in registry.tools["zoom"].description
    words = registry.tools["compare"].description
    assert "side by side in the one magnifier window" in words
    assert "original captures" in words and "present(open), not compare" in words


def test_a_pair_the_studio_does_not_show_is_not_said_to_be_shown(monkeypatch):
    """"Both" is done only once the studio's report shows the pair (screen.picture_shown): a studio that never shows it
    fails the second zoom, with what to do next, and the request still counts only the first as shown."""
    import asyncio

    from orchestrator import screen as screens
    from orchestrator.brain.registry import ToolContext
    from orchestrator.screen import Screen

    hub, magnifier, registry, logged = magnifier_world()
    monkeypatch.setattr(screens, "CONFIRM_S", 0.2)

    def report():  # the magnifier as the studio draws it: its live view, never a picture
        hub.screen.report({"items": [{"id": "magnifier", "kind": "magnifier", "open": magnifier.open,
                                      "shows": magnifier.object_id, "source": "live", "level": magnifier.factor}]})

    hub.screen = Screen(lambda m: asyncio.get_running_loop().call_soon(report) if m.get("type") == "screen-ask" else None)
    report()
    request = ToolContext("voice", 1, 0, "can you zoom into both of them")
    assert run_tool(registry, "zoom", {"object": "o1", "level": "more"}, request).ok
    second = run_tool(registry, "zoom", {"object": "o2", "level": "more"}, request)
    assert second.ok is False and "side by side did not work" in second.summary
    assert second.next == "look at the magnifier, or ask again"  # its next step, passed on
    assert request.magnified == ["o1"] and not [r for r in logged if r.get("rule") == "one-magnifier"]


def test_only_the_looks_steps_that_repeat_what_hands_did_are_left_out():
    """The partner's review of G4, NOTE 5: every look step of a tool hands had run was dropped, so hands' zoom on one
    stoma dropped the look's zoom on the other. A look step is left out only when it is the same tool on the same
    things (a seen id as the outline hands made of it), one for each of hands' steps."""
    from orchestrator.brain.loop import repeated_steps
    from orchestrator.brain.registry import ToolRun

    look = [("zoom", {"object": "o1", "level": "more"}), ("zoom", {"object": "o2", "level": "more"})]
    assert repeated_steps(look, [ToolRun("zoom", {"object": "o2"})], {}) == set()  # "more" changes the existing zoom
    assert repeated_steps(look, [ToolRun("zoom", {"object": "o2", "level": "more"})], {}) == {2}
    assert repeated_steps(look, [ToolRun("zoom", {"object": "o5"})], {}) == set()
    assert repeated_steps([("zoom", {"object": "s1"})], [ToolRun("zoom", {"object": "o5"})], {"s1": "o5"}) == {1}
    assert repeated_steps([("clear", {"what": "all"}), ("clear", {"what": "all"})], [ToolRun("clear", {})], {}) == {1}
    assert repeated_steps([("compare", {"a": "o2", "b": "o1"})], [ToolRun("compare", {"a": "o1", "b": "o2"})], {}) == {1}
    # Named by label (the partner's re-check of G4, SHOULD-FIX 1): two labels are two things, one label is one.
    done = [ToolRun("zoom", {"object": "stoma 1"})]
    assert repeated_steps([("zoom", {"object": "stoma 2"})], done, {}) == set()
    assert repeated_steps([("zoom", {"object": "stoma 2"}), ("zoom", {"object": "Stoma  1"})], done, {}) == {2}
    # `present` names its thing in words under `what`: another thing, or another change, is another step.
    shown = [ToolRun("present", {"what": "the magnifier", "do": "move", "to": "top left"})]
    assert repeated_steps([("present", {"what": "the cells", "do": "colour", "value": "size"})], shown, {}) == set()
    assert repeated_steps([("present", {"what": "the magnifier", "do": "move", "to": "top left"})], shown, {}) == {1}


@pytest.mark.parametrize('tool,first,second', [
    ('zoom', {'object': 'o1', 'level': 2}, {'object': 'o1', 'level': 4}),
    ('zoom', {'object': 'o1'}, {'object': 'o1', 'hold': True}),
    ('clear', {'what': 'ai'}, {'what': 'all'}),
    ('highlight', {'of': 'e1', 'top': 1}, {'of': 'e1', 'top': 2}),
    ('compare', {'a': 'o1', 'b': 'o2'}, {'a': 'o1', 'b': 'o3'}),
])
def test_carried_calls_with_changed_effect_arguments_are_distinct(tool, first, second):
    """Dropping a same-target call with a new zoom/control/selection loses an explicit requested effect."""
    from orchestrator.brain.loop import repeated_steps
    from orchestrator.brain.registry import ToolRun

    assert repeated_steps([(tool, second)], [ToolRun(tool, first)], {}) == set()


def test_a_carried_step_keeps_only_its_own_occurrence_output():
    """Two earlier highlights must not make @1 mean both populations when only the second is repeated."""
    from orchestrator.brain.registry import Steps, ToolContext, step_output

    earlier = Steps(tools={1: 'highlight', 2: 'highlight'}, produced={1: ['o1'], 2: ['o2']})
    carried = Steps.carrying([('highlight', {'of': 'e2'}), ('zoom', {'object': '@1'})], {1: 2}, earlier)
    ctx = ToolContext('chat', 't1', 7, step=2, steps=carried)
    assert step_output(ctx, '@1') == 'o2'
    earlier.produced[1].append('o3')
    assert step_output(ctx, '@1') == 'o2'
    assert carried.produced[1] is earlier.produced[2]


def test_a_legacy_tool_name_cannot_carry_two_distinct_occurrences():
    """The compatibility path may carry one known occurrence; a name alone cannot identify two."""
    from orchestrator.brain.registry import Steps

    earlier = Steps(tools={1: 'highlight', 2: 'highlight'}, produced={1: ['o1'], 2: ['o2']})
    carried = Steps.carrying([('highlight', {'of': 'e2'})], {'highlight'}, earlier)
    assert carried.tools == {}  # no occurrence was identified, so no step may be marked carried
    assert carried.produced == {}


def test_a_queued_carried_occurrence_shares_its_exact_output_when_it_finishes(world):
    """Use the real ordered runner: a second highlight has identity before the first releases it."""
    import asyncio
    from orchestrator.brain.actions import ActionRunner
    from orchestrator.brain.loop import repeated_occurrences
    from orchestrator.brain.registry import Registry, Steps, Tool, ToolContext, record_made, step_output

    async def go():
        release = asyncio.Event()
        finished = asyncio.Event()

        async def highlight(ctx, args):
            if args['of'] == 'e1':
                await release.wait()
            record_made(ctx, 'o1' if args['of'] == 'e1' else 'o2')
            if args['of'] == 'e2':
                finished.set()
            return 'outlined'

        async def late(*args):
            pass

        registry = Registry()
        registry.add(Tool('highlight', '', {}, highlight, inline_ms=1))
        runner = ActionRunner(registry, world, lambda: None, late)
        earlier = ToolContext('chat', 1, world.latest_info.id)
        runs = await runner.run([('highlight', {'of': 'e1'}), ('highlight', {'of': 'e2'})], earlier)
        assert runs[1].task is None  # queued behind actual pending first work
        actions = [('highlight', {'of': 'e2'}), ('zoom', {'object': '@1'})]
        done = repeated_occurrences(actions, runs, {})
        later = ToolContext('chat', 1, world.latest_info.id, step=2,
                            steps=Steps.carrying(actions, done, earlier.steps))
        release.set()
        await runs[0].task
        await asyncio.wait_for(finished.wait(), HANG_S)
        await runs[1].task
        assert step_output(later, '@1') == 'o2'
        assert later.steps.produced[1] is earlier.steps.produced[2]

    asyncio.run(go())


@pytest.mark.parametrize('phase', ['decode', 'match'])
@pytest.mark.parametrize('probe', ['pending', 'recovered'])
def test_a_rejoining_layer_publishes_its_checked_basis_and_members_together(world, monkeypatch, phase, probe):
    """A real member check cannot make its unpublished rejoin look like the renderer's current layer."""
    import asyncio
    import copy
    import threading
    from orchestrator import population as populations
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.hub import Hub
    from tests.test_screen import setup

    # Use the same unclipped specimen texture, assigning the tracker epochs independently of its pixels.
    world.frame(0, 0, epoch=0, wall=1001.0)
    world.latest_info.epoch = 2
    population, layer = checked(world)
    _, _, studio = setup(world)
    published = []
    delivered = layer.broadcast

    def publish(message):
        if message.get('type') == 'layer':
            published.append(copy.deepcopy(message))
        delivered(message)

    layer.broadcast = publish
    publish(layer.wire())
    studio.report()
    old_wire = copy.deepcopy(published[-1])
    assert old_wire['view']['epoch'] == 2 and len(old_wire['verified']) == 6

    # The picture pans four pixels on the same specimen; the measured rejoin adds its epoch translation.
    now = world.frame(4, 0, epoch=0, wall=1001.1)
    now.epoch, now.dx, now.dy = 1, 124.0, -60.0
    world.motion = SimpleNamespace(rejoins=[{'from': 2, 'to': 1, 'delta': (120.0, -60.0)}])
    world.epoch_links, world._rejoins_seen, world._rejoined = {}, 0, {}
    Hub._carry_over(world)
    assert world.epoch_links[2] == (1, (120.0, -60.0))
    release = threading.Event()

    async def go():
        loop, ready = asyncio.get_running_loop(), asyncio.Event()
        original = populations._half if phase == 'decode' else layer._match

        def held(*args):
            result = original(*args)  # real decode/matching and its calculated registration correction
            loop.call_soon_threadsafe(ready.set)
            if not release.wait(10):
                raise RuntimeError('test did not release the member check')
            return result

        monkeypatch.setattr(populations if phase == 'decode' else layer,
                            '_half' if phase == 'decode' else '_match', held)
        task = asyncio.create_task(layer.check())
        try:
            await asyncio.wait_for(ready.wait(), HANG_S)
            assert published[-1] == old_wire  # Studio still has epoch 2, so its epoch-1 frame draws no members.
            if probe == 'pending':
                assert layer.wire() == old_wire  # view/offset/correction/verified set publish as one checked basis
                drawn, later, why = populations.drawn_colours(population, layer)
                assert drawn == {} and len(later) == 6 and why
                assert layer.drawable(now) == [] and layer.member_hit(now, 304, 200) is None
                assert layer.verified_count() == (0, 6)
                with pytest.raises(ToolFailed):
                    select(population, where={'colour': 'red'}, hub=world)
                facts = next(line for line in populations.evidence_lines(world) if f'({population.id})' in line)
                assert 'not drawn over the live view now' in facts and '0 verified in place' in facts
        finally:
            release.set()
            await asyncio.wait_for(task, 10)
        studio.report()
        assert published[-1]['view'] == {'epoch': 1, 'offset': [120.0, -60.0]}
        assert len(published[-1]['verified']) == 6 and layer.wire() == published[-1]
        assert len(populations.drawn_colours(population, layer)[0]) == 6
        assert [m.id for m in select(population, where={'colour': 'red'}, hub=world)[0]] == ['c6']
        drawn = layer.drawable(now)
        assert len(drawn) == 6
        assert drawn[0][1][0][0][0] == pytest.approx(population.live()[0].polygons[0][0][0] + 4, abs=0.02)
        assert layer.member_hit(now, 304, 200).id == population.live()[0].id

    asyncio.run(go())


@pytest.mark.parametrize('unknown', [False, 0, '', None])
def test_present_refuses_unknown_argument_keys_even_when_falsey(world, unknown):
    """An unknown key must not silently disappear merely because its requested value is false or zero."""
    from orchestrator.brain.registry import ToolFailed
    from tests.test_screen import present, setup

    registry, screen, studio = setup(world)
    studio.report()
    before = dict(studio.items['magnifier'])
    with pytest.raises(ToolFailed) as refused:
        present(registry, world, what='magnifier', do='close', unknown_control=unknown)
    assert 'unknown_control' in str(refused.value)
    assert studio.items['magnifier'] == before


@pytest.mark.parametrize('paired', [False, True], ids=['historical-second', 'third-after-pair'])
def test_a_refused_cell_zoom_does_not_leave_an_unasked_outline(world, paired):
    """The one-window guard must refuse before cell promotion leaves a new, unshown outline behind."""
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.magnifier import Magnifier
    from tests.test_magnifier import _outline

    population, _ = checked(world)
    first = _outline(world, 100, 'stoma')
    second = _outline(world, 150, 'guard cell') if paired else None
    magnifier = Magnifier(world)
    magnifier.zoom(first.id)
    registry = build(Services(hub=world, agent=None, magnifier=magnifier))
    ctx = ToolContext('chat', 1, world.latest_info.id, magnified=[first.id] + ([second.id] if paired else []))
    if not paired:
        world.frame(0, 0, epoch=1)
        run(world.layer.check())
    before = set(world.scene.objects)
    result = run_tool(registry, 'zoom', {'object': population.live()[0].id}, ctx)
    assert result.ok is False
    assert set(world.scene.objects) == before
    assert magnifier.object_id == first.id


def test_drawn_colour_selection_uses_the_last_style_the_studio_confirmed(world):
    """A concurrent query must not name a requested style before Studio reports its drawn pixels."""
    import asyncio
    from orchestrator.brain.registry import ToolContext
    from orchestrator.ticket import issue, using
    from tests.test_screen import setup

    population, layer = checked(world)
    registry, screen, studio = setup(world)
    layer.broadcast(layer.wire())
    studio.report()

    async def go():
        queued = asyncio.Event()
        delayed = []
        delivered = layer.broadcast

        def delay(message):
            if message.get('type') in ('layer', 'present'):
                delayed.append(message)
                if message.get('type') == 'present':
                    queued.set()
            else:
                delivered(message)

        layer.broadcast = screen.broadcast = delay
        with using(issue(world, 'turn')):
            change = asyncio.create_task(registry.tools['present'].handler(
                ToolContext('chat', 1, world.latest_info.id),
                {'what': population.id, 'do': 'colour', 'value': 'yellow'}))
            await asyncio.wait_for(queued.wait(), HANG_S)
            try:
                assert layer.style['colour'] == 'uniform'  # actual request queued, not a fabricated state
                assert not change.done()  # awaiting real Screen.confirm
                selected, how = select(population, where={'colour': 'red'}, hub=world)
                assert [m.id for m in selected] == ['c6']
                assert how == 'drawn red'
                from orchestrator.colours import bgr, own
                from orchestrator.population import evidence_lines, member_bgr

                assert any('each in its own colour' in line for line in evidence_lines(world))
                assert not any('all yellow' in line for line in evidence_lines(world))
                assert member_bgr(world, population, 'c6') == bgr(own('c6'))
                assert layer.drawable(world.latest_info)[5][0] == bgr(own('c6'))
            finally:
                for message in delayed:
                    delivered(message)
                await change
        assert [m.id for m in select(population, where={'colour': 'yellow'}, hub=world)[0]] == [
            'c1', 'c2', 'c3', 'c4', 'c5', 'c6']
        assert any('all yellow' in line for line in evidence_lines(world))

    asyncio.run(go())


def test_a_cell_zoom_losing_placement_during_promotion_does_not_leave_an_outline(world, monkeypatch):
    """Preflight is insufficient: the slide can lose continuity while promotion reads its own template."""
    from orchestrator import population as populations
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.magnifier import Magnifier
    from tests.test_magnifier import _outline

    population, _ = checked(world)
    first = _outline(world, 100, 'stoma')
    magnifier = Magnifier(world)
    magnifier.zoom(first.id)
    registry = build(Services(hub=world, agent=None, magnifier=magnifier))
    ctx = ToolContext('chat', 1, world.latest_info.id, magnified=[first.id])
    before = set(world.scene.objects)
    original = populations._own_templates

    def move_while_reading(*args):
        world.frame(0, 0, epoch=1)
        return original(*args)

    monkeypatch.setattr(populations, '_own_templates', move_while_reading)
    result = run_tool(registry, 'zoom', {'object': population.live()[0].id}, ctx)
    assert result.ok is False
    assert set(world.scene.objects) == before
    assert not population.links
    assert magnifier.object_id == first.id


def test_a_queued_colour_ramp_refresh_keeps_the_acknowledged_member_values(world):
    """Same column controls can conceal different colours: a correction changes the measured range."""
    import asyncio
    from orchestrator.brain.registry import ToolContext
    from orchestrator.population import correct, drawn_colours
    from orchestrator.ticket import issue, using
    from tests.test_screen import setup

    population, layer = checked(world)
    registry, screen, studio = setup(world)
    layer.broadcast(layer.wire())
    studio.report()

    async def go():
        with using(issue(world, 'turn')):
            await registry.tools['present'].handler(ToolContext('chat', 1, world.latest_info.id),
                                                   {'what': population.id, 'do': 'colour', 'value': 'size'})
        largest = max(population.live(), key=lambda m: population.value(m.id, 'areaPx'))
        correct(world, population, 'remove', [largest.id], by='user')
        # Revision changes invalidate the old pixel checks. Verify the actual
        # remaining members before comparing their acknowledged drawn colours.
        await layer.check()
        assert layer.verified_count() == (5, 5)
        studio.report()
        before = drawn_colours(population, layer)[0]
        assert len(before) == 5
        assert 'red' not in before.values()  # the red member was removed; the original range still draws
        queued = asyncio.Event()
        delayed = []
        delivered = layer.broadcast

        def delay(message):
            if message.get('type') in ('layer', 'present'):
                delayed.append(message)
                if message.get('type') == 'present':
                    queued.set()
            else:
                delivered(message)

        layer.broadcast = screen.broadcast = delay
        with using(issue(world, 'turn')):
            change = asyncio.create_task(registry.tools['present'].handler(
                ToolContext('chat', 1, world.latest_info.id),
                {'what': population.id, 'do': 'colour', 'value': 'size'}))
            await asyncio.wait_for(queued.wait(), HANG_S)
            try:
                assert not change.done()
                assert layer.style['values'] != layer.confirmed_style['values']
                assert drawn_colours(population, layer)[0] == before
            finally:
                for message in delayed:
                    delivered(message)
                await change
        now = drawn_colours(population, layer)[0]
        assert now != before
        assert [mid for mid, colour in now.items() if colour == 'red'] == [
            max(population.live(), key=lambda m: population.value(m.id, 'areaPx')).id]

    asyncio.run(go())


def _frame_before_the_next_layer_check(world, change):
    """Real frame publication can invalidate Studio rendering before the throttled member check runs."""
    population, layer = checked(world)
    from tests.test_screen import setup

    _, _, studio = setup(world)
    layer.broadcast(layer.wire())
    studio.report()
    now = world.frame(4, 0, epoch=1 if change == 'epoch' else 0, wall=1001.1)
    now.tracked = change != 'unregistered'
    assert all(p['state'] == 'verified' for p in layer.placement.values())  # deliberately no Layer.check
    return population, layer


@pytest.mark.parametrize('change', ['epoch', 'unregistered', 'neighbor'])
def test_drawn_colour_facts_follow_the_current_renderer_frame_gate(world, change):
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.population import drawn_colours, evidence_lines

    population, layer = _frame_before_the_next_layer_check(world, change)
    now, later, why = drawn_colours(population, layer)
    facts = next(line for line in evidence_lines(world) if f'({population.id})' in line)
    if change == 'neighbor':
        assert len(now) == 6 and not later
        assert [m.id for m in select(population, where={'colour': 'red'}, hub=world)[0]] == ['c6']
        assert 'shown over the live view' in facts and '6 verified in place' in facts
    else:
        assert not now and len(later) == 6 and later['c6'] == 'red'
        with pytest.raises(ToolFailed) as refused:
            select(population, where={'colour': 'red'}, hub=world)
        assert 'none' in str(refused.value) and 'now' in str(refused.value)
        assert 'not drawn over the live view now' in facts and '0 verified in place' in facts
        assert why  # the would-be colours keep their reason, rather than being discarded


@pytest.mark.parametrize('change', ['epoch', 'unregistered', 'neighbor'])
def test_drawn_snapshot_follows_the_current_renderer_frame_gate(world, change):
    population, layer = _frame_before_the_next_layer_check(world, change)
    drawn = layer.drawable(world.latest_info)
    if change == 'neighbor':
        assert len(drawn) == 6
        # The real member check retains a subpixel registration correction (under 0.02 px on this texture).
        # A neighboring frame must still carry the outline by the slide's measured four-pixel move.
        assert drawn[0][1][0][0][0] == pytest.approx(population.live()[0].polygons[0][0][0] + 4, abs=0.02)
        assert drawn[0][1][0][0][1] == pytest.approx(population.live()[0].polygons[0][0][1], abs=0.02)
    else:
        assert drawn == []


@pytest.mark.parametrize('change', ['epoch', 'objective', 'picture-size', 'disc', 'unregistered',
                                   'expired-time', 'excess-travel', 'new-content', 'neighbor'])
def test_paired_cell_promotion_revalidates_after_the_final_placement_worker(world, tmp_path, monkeypatch, change):
    """Hold the actual final worker result, then publish a new view before its synchronous scene write."""
    import asyncio
    import threading
    import cv2
    import numpy as np
    from orchestrator import apply as placement_gate
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.day import DayStore
    from orchestrator.hub import focus_score
    from orchestrator.magnifier import Magnifier
    from orchestrator.scale import Scale
    from orchestrator.ticket import issue, using
    from tests.test_magnifier import _outline
    from tests.test_screen import setup

    def frame_facts(info):
        # World supplies the real textured JPEG and measured offset. A paired magnifier also consumes
        # the steady/focus facts that the live hub publishes, so keep those fixture facts complete.
        gray = cv2.imdecode(np.frombuffer(world.ring[info.id], np.uint8), cv2.IMREAD_GRAYSCALE)
        info.steady, info.focus = True, focus_score(gray, info.fov)

    world.scale = Scale(DayStore(tmp_path))
    world.scale.set_objective(10)
    world.latest_info.disc = 100.0
    frame_facts(world.latest_info)
    population, layer = checked(world)
    _, _, studio = setup(world)
    delivered = world.broadcast

    def report_magnifier(message):
        delivered(message)
        if message.get('type') == 'magnifier':
            sources = message.get('sources') or []
            source = sources[message['index']] if sources else {}
            studio.items['magnifier'].update(open=message['open'], shows=message.get('objectId'),
                                             source=source.get('url') or source.get('kind'))
            asyncio.get_running_loop().call_soon(studio.report)

    world.broadcast = report_magnifier
    first = _outline(world, 100, 'stoma')
    magnifier = Magnifier(world)
    registry = build(Services(hub=world, agent=None, magnifier=magnifier))
    original = placement_gate._place_all
    release = threading.Event()

    async def go():
        loop = asyncio.get_running_loop()
        ready = asyncio.Event()

        def held_worker(*args):
            result = original(*args)
            if release.is_set():
                return result  # a subsequent recheck may legitimately find the new view unverified
            assert result[0].verified  # actual pixel/window calculation succeeded on the previous view
            loop.call_soon_threadsafe(ready.set)
            if not release.wait(10):
                raise RuntimeError('test did not release the final placement worker')
            return result

        monkeypatch.setattr(placement_gate, '_place_all', held_worker)
        magnifier.zoom(first.id)
        before_scene = set(world.scene.objects)
        before_links = list(population.links)
        before_magnifier = magnifier.wire()
        ctx = ToolContext('chat', 1, world.latest_info.id, magnified=[first.id])
        with using(issue(world, 'turn')):
            result = registry.start('zoom', {'object': population.live()[0].id}, ctx)
            try:
                await asyncio.wait_for(ready.wait(), HANG_S)
                now = world.frame(20 if change == 'excess-travel' else 4, 0,
                                  epoch=1 if change == 'epoch' else 0,
                                  wall=1003.0 if change == 'expired-time' else 1001.1)
                now.disc = 102.0 if change == 'disc' else 100.0
                now.tracked = change != 'unregistered'
                if change == 'objective':
                    world.scale.set_objective(40)
                if change == 'picture-size':
                    now.width //= 2
                    pixels = cv2.imdecode(np.frombuffer(world.ring[now.id], np.uint8), cv2.IMREAD_COLOR)
                    world.ring[now.id] = cv2.imencode('.jpg', cv2.resize(pixels, (now.width, now.height)))[1].tobytes()
                if change in ('expired-time', 'excess-travel', 'new-content'):
                    world.ring[now.id] = cv2.imencode('.jpg', np.full((now.height, now.width, 3), 140, np.uint8))[1].tobytes()
                if change == 'new-content':
                    world.last_change = ('content', now.wall_at)
                frame_facts(now)
            finally:
                release.set()
            await asyncio.wait_for(result.task, 10)
        if change == 'neighbor':
            assert result.ok is True, result.summary
            assert len(world.scene.objects) == len(before_scene) + 1
            assert len(population.links) == len(before_links) + 1
            assert ctx.magnified == [first.id, population.links[-1]['to']]
        else:
            assert result.ok is False, result.summary
            assert set(world.scene.objects) == before_scene
            assert population.links == before_links
            assert magnifier.wire() == before_magnifier

    asyncio.run(go())
