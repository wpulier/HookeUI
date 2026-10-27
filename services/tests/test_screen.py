from orchestrator.magnifier import Magnifier
"""What is on screen (orchestrator/screen.py; contract 10, Amendment 2 points 1-5, Amendment 4 items 4-6):
the studio reports what it shows; one set of verbs acts on any presentation and is done only once a report
shows it; a verb a kind does not take fails with the reason; who set each property is kept."""
import asyncio

import pytest

from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.present import add_present_tools
from orchestrator.screen import Screen
from orchestrator.ticket import issue, using
from tests.test_population import cells, world  # noqa: F401  (the fixture)
from types import SimpleNamespace


class Studio:
    """A stand-in studio: applies what it is sent and reports what it shows (or, `deaf`, never does)."""

    def __init__(self, screen: Screen, deaf: bool = False):
        self.screen, self.deaf, self.visible = screen, deaf, True
        self.items = {"live": {"id": "live", "kind": "live", "visible": True, "opacity": 1.0, "z": 0},
                      "magnifier": {"id": "magnifier", "kind": "magnifier", "open": True, "rect": [10, 10, 300, 200], "z": 5}}
        self.applied = []

    def hear(self, message: dict) -> None:
        if message.get("type") == "screen-ask" and not self.deaf:  # a wait asks for a report now (re-check N2)
            asyncio.get_running_loop().call_soon(self.report)
        if message.get("type") == "magnifier" and not self.deaf:
            source = message["sources"][message["index"]] if message["sources"] else {}
            peer = message.get("beside")
            self.items["magnifier"].update(
                open=message["open"], source=source.get("url") if source.get("kind") == "image" else source.get("kind"),
                shows=source.get("objectId") if source.get("kind") == "image" else message.get("objectId"),
                beside=(peer or {}).get("url"),
                level=1.0 if source.get("kind") == "image" else (message.get("zoom") or 1.0),
                pictures=[{"source": s["url"], "scale": s.get("displayScale", 1)}
                          for s in [source, peer] if s and s.get("kind") == "image"])
            asyncio.get_running_loop().call_soon(self.report)
        if message.get("type") == "layer" and message.get("population"):
            key = f"layer:{message['population']}"
            item = self.items.setdefault(key, {"id": key, "kind": "population", "visible": True, "z": 1})
            style = message.get("style") or {}
            item.update(opacity=style.get("opacity", 1.0), style={k: v for k, v in style.items() if k not in ("opacity", "values")})
        if message.get("type") == "present" and not self.deaf:
            item = self.items.setdefault(message["id"], {"id": message["id"], "kind": message["kind"]})
            values = message["set"]
            for k, v in values.items():
                if k in ("opacity", "visible", "open", "rect"):
                    item[k] = v
                elif k == "front":
                    item["z"] = 99
                else:
                    item.setdefault("style", {})[k] = v
            self.applied.append(message["change"])
            asyncio.get_running_loop().call_soon(self.report)

    def report(self, user=None, visible=None):
        self.visible = self.visible if visible is None else visible
        self.screen.report({"items": list(self.items.values()), "applied": list(self.applied), "user": user or [],
                            "visible": self.visible}, client=self)


def setup(world, deaf=False):
    screen = Screen()
    studio = Studio(screen, deaf)
    sent = world.broadcast

    def broadcast(message):
        sent(message)
        studio.hear(message)

    world.broadcast = broadcast
    screen.broadcast = broadcast
    world.screen = screen
    from orchestrator.population import layer_of

    layer_of(world).broadcast = broadcast
    registry = Registry()
    add_present_tools(registry, SimpleNamespace(hub=world, magnifier=None, presenter=None, slidemap=None, agent=None,
                                                publish_settings=None, log=None, history_log=None))
    studio.report()
    return registry, screen, studio


def present(registry, world, **args):
    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["present"].handler(ToolContext("chat", 1, None, "colour them"), args)
    return asyncio.run(go())


def test_colouring_the_cells_by_size_is_done_once_the_studio_shows_it(world):
    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.report()
    said = present(registry, world, what="the outlined cells", do="colour", value="size")
    assert said == f"{population.title} coloured by size, with a legend"  # its title, never its id (Amendment 4.1)
    item = studio.items[f"layer:{population.id}"]
    assert item["style"]["colour"] == "column" and item["style"]["column"] == "areaPx"
    assert screen.owner(f"layer:{population.id}", "colour").startswith("request:")
    # Said once, on the Evidence line (going live, G3): the screen's lines no longer repeat the colouring.
    from orchestrator.population import evidence_lines

    assert any(f"({population.id})" in line and "coloured by size, blue (low) to red (high)" in line
               for line in evidence_lines(world))
    assert not any(f"layer:{population.id}" in line for line in screen.lines())  # coloured, nothing else: no line of its own


def test_keep_the_five_largest_and_fade_the_rest(world):
    registry, screen, studio = setup(world)
    population, _ = cells(world)
    said = present(registry, world, what=population.id, do="emphasise", by="area", top=5, rest="fade")
    assert "5 kept (largest by size)" in said and "the rest faded" in said
    assert len(studio.items[f"layer:{population.id}"]["style"]["emphasis"]["members"]) == 5


def test_a_verb_a_kind_does_not_take_fails_with_the_reason_and_what_it_takes(world):
    registry, _, _ = setup(world)
    with pytest.raises(ToolFailed) as refused:
        present(registry, world, what="the live view", do="hide")
    assert "never hidden" in str(refused.value) and "dim it" in refused.value.next
    assert present(registry, world, what="the live view", do="opacity", value="dim") == "the live view at 35% opacity"
    with pytest.raises(ToolFailed) as hidden:
        present(registry, world, what="the live view", do="opacity", value=0)
    assert "dims to 15% at most" in str(hidden.value)  # M1.5b review item 3: never hidden
    with pytest.raises(ToolFailed) as too_much:
        present(registry, world, what="the live view", do="opacity", value="1.5")
    assert "more than full opacity" in str(too_much.value)
    assert present(registry, world, what="the live view", do="opacity", value="40%") == "the live view at 40% opacity"


def test_nothing_is_called_done_that_the_studio_did_not_show(world, monkeypatch):
    from orchestrator import screen as screen_module

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)
    registry, _, _ = setup(world, deaf=True)
    with pytest.raises(ToolFailed) as refused:
        present(registry, world, what="the live view", do="opacity", value=0.5)
    assert "not at 50% opacity on screen" in str(refused.value)


def test_without_a_studio_nothing_on_screen_can_change(world):
    registry = Registry()
    world.screen = Screen()
    add_present_tools(registry, SimpleNamespace(hub=world, magnifier=None, presenter=None, slidemap=None, agent=None,
                                                publish_settings=None, log=None, history_log=None))
    with pytest.raises(ToolFailed) as refused:
        present(registry, world, what="the live view", do="opacity", value=0.5)
    assert "no studio is showing the view" in str(refused.value)


def test_the_users_own_change_is_theirs(world):
    _, screen, studio = setup(world)
    studio.items["magnifier"]["rect"] = [400, 40, 320, 240]
    studio.report(user=[{"id": "magnifier", "prop": "rect"}])
    assert screen.owner("magnifier", "rect") == "user" and screen.owners["magnifier"]["rect"]["was"] == [10, 10, 300, 200]


def test_every_kind_of_entry_in_the_index_is_shown_somehow_or_says_why_not():
    """Amendment 2, point 6: horizontal by construction."""
    from orchestrator.produces import KINDS
    from orchestrator.screen import PRESENTED_AS, TAKES

    for kind in KINDS:
        assert kind in PRESENTED_AS, f"{kind} declares no presentation"
        shown_as, why = PRESENTED_AS[kind]
        assert (shown_as in TAKES) if shown_as else bool(why), kind


def test_project_without_a_viewer_offers_reading_its_retained_state(world, tmp_path):
    from orchestrator.resources import resources_of
    from orchestrator.scientific_projects import ProjectStore
    from tests.test_scientific_projects import document

    index = resources_of(world)
    world.resources = index
    index.projects = ProjectStore(tmp_path / 'projects')
    item = index.projects.create(index.session(), document())
    registry, screen, studio = setup(world)
    with pytest.raises(ToolFailed, match='no screen viewer here') as caught:
        present(registry, world, what=item.id, do='open')
    assert caught.value.next == 'ask to read it instead'
    assert not studio.applied, 'an unsupported project viewer must not send a presentation change'
    assert index.document(item.id)['detail']['values'] == document()


def test_show_me_the_motion_one_opens_its_picture(world, tmp_path):
    """Flow 10: every item the summary names can be opened as a presentation."""
    import cv2
    import numpy as np
    from orchestrator.resources import Index, Results

    registry, screen, studio = setup(world)

    magnifier = Magnifier(world)  # the studio reports the picture it shows (re-check N5)
    pictures = {"k": cv2.imencode(".jpg", np.full((20, 20, 3), 90, np.uint8))[1].tobytes()}
    world.resources = Index(world, results=Results(tmp_path / "results", pictures=pictures.get))
    entry = world.resources.results.add("track_motion", {}, "14 of 40 moving", pictures=["k"])
    registry2 = Registry()
    add_present_tools(registry2, SimpleNamespace(hub=world, magnifier=magnifier, presenter=None, slidemap=None, agent=None,
                                                 publish_settings=None, log=None, history_log=None))
    said = present(registry2, world, what=entry["id"], do="open")
    assert said.startswith("Opened existing Motion · ") and "not the live view" in magnifier.picture_now()["label"]



# --- the scaffolding review's M1.5b items ----------------------------------------------------------------
def test_words_that_fit_several_things_are_asked_about_and_whole_words_are_matched(world):
    """Item 4: "fade the cells on the live view" dimmed the live view; "the heatmap" meant the slide map."""
    registry, screen, studio = setup(world)
    population, _ = cells(world)
    with pytest.raises(ToolFailed) as asked:
        present(registry, world, what="the cells on the live view", do="opacity", value=0.5)
    assert "which one:" in str(asked.value) and "the live view" in str(asked.value) and asked.value.next == "say which one"
    assert studio.items["live"]["opacity"] == 1.0  # nothing was changed on a guess
    said = present(registry, world, what="the heatmap", do="colour", value="size")
    assert said.startswith(population.title)


def test_a_step_reference_reaches_the_verbs(world):
    """Item S8: what="@1" failed."""
    from orchestrator.brain.registry import Steps

    registry, screen, studio = setup(world)
    population, _ = cells(world)

    async def go():
        ctx = ToolContext("chat", 1, None, "outline the cells and fade them")
        ctx.steps = Steps(tools={1: "outline_cells", 2: "present"}, produced={1: [population.id]})
        ctx.step = 2
        with using(issue(world, "turn")):
            return await registry.tools["present"].handler(ctx, {"what": "@1", "do": "opacity", "value": 0.5})

    assert asyncio.run(go()) == f"{population.title} at 50% opacity"


def test_the_visible_studio_confirms_and_who_set_what_is_kept_only_once_shown(world):
    """Items S1 and S2: any tab's report could settle a change; a refused change still took ownership."""
    from orchestrator import screen as screen_module

    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.report()  # the layer drawn (the studio reports it as it draws it)
    hidden_tab = Studio(screen)
    hidden_tab.report(visible=False)  # a second studio, in a tab the user is not looking at
    present(registry, world, what=population.id, do="opacity", value=0.5)
    assert screen.owner(f"layer:{population.id}", "opacity").startswith("request:")
    assert screen.owner(f"layer:{population.id}", "colour") is None  # only what changed is recorded
    studio.report(visible=False)  # the user switched tabs: this studio is no longer the one they see
    deaf = Studio(screen, deaf=True)
    deaf.report()  # the visible studio now never applies anything
    screen_module.CONFIRM_S, saved = 0.2, screen_module.CONFIRM_S
    try:
        with pytest.raises(ToolFailed):
            present(registry, world, what=population.id, do="opacity", value=0.3)
    finally:
        screen_module.CONFIRM_S = saved
    owner = screen.owners[f"layer:{population.id}"]["opacity"]
    assert owner["was"] == 1.0 and owner["by"].startswith("request:")  # a change never shown owns nothing
    from orchestrator.population import layer_of

    assert layer_of(world).style["opacity"] == 0.5  # and the server's style is put back as the screen still is


def test_closing_is_done_only_once_the_studio_shows_it_closed(world, monkeypatch):
    """Item 1 (flow 7): close_magnifier said "closed" at once, without the report."""
    from orchestrator import screen as screen_module
    from orchestrator.brain.toolbox.view import add_close_magnifier

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)
    screen = Screen()
    world.screen = screen
    magnifier = SimpleNamespace(open=True, hub=world, close=lambda: setattr(magnifier, "open", False))
    registry = Registry()
    add_close_magnifier(registry, magnifier)
    deaf = Studio(screen, deaf=True)
    deaf.items["magnifier"]["open"] = True
    deaf.report()

    async def go():
        return await registry.tools["close_magnifier"].handler(ToolContext("chat", 1, None, "close the magnifier"), {})

    with pytest.raises(ToolFailed) as refused:
        asyncio.run(go())  # the studio never reports it closed
    assert "the magnifier is not closed on screen" in str(refused.value)
    magnifier.open = True
    reporting = Studio(screen)
    reporting.items["magnifier"]["open"] = True
    reporting.report()

    async def closes():
        task = asyncio.ensure_future(registry.tools["close_magnifier"].handler(ToolContext("chat", 1, None, "close it"), {}))
        await asyncio.sleep(0.01)
        reporting.items["magnifier"]["open"] = False
        reporting.report()  # the studio draws it closed
        return await task

    assert asyncio.run(closes()) == "closed the magnifier"


def test_another_population_shown_does_not_carry_the_last_ones_selection(world):
    """Re-check N1: "keep the five largest and hide the rest", then a new cell run: every cell was hidden."""
    from orchestrator.population import layer_of

    registry, screen, studio = setup(world)
    first, _ = cells(world)
    present(registry, world, what=first.id, do="emphasise", by="area", top=5, rest="hide")
    present(registry, world, what=first.id, do="colour", value="size")
    present(registry, world, what=first.id, do="opacity", value=0.6)
    second, _ = cells(world)
    style = layer_of(world).style
    assert layer_of(world).shown == second.id != first.id
    assert "emphasis" not in style and "values" not in style and style["colour"] == "id"  # about the old members: gone
    assert style["opacity"] == 0.6  # the layer's own look stays


def test_a_wait_asks_the_studio_for_a_report_and_is_not_waited_out(world, wall_clock):
    """Re-check N2: a change the studio had no reason to report (the map already open) waited 4 s or failed."""
    import itertools
    import time as _time

    from orchestrator.screen import open_state

    ticks = itertools.count(1)
    screen = Screen(clock=lambda: float(next(ticks)))
    quiet = Studio(screen)  # reports only when asked
    screen.broadcast = lambda m: asyncio.get_running_loop().call_soon(quiet.report) if m.get("type") == "screen-ask" else None
    quiet.report()

    async def go():
        t0 = _time.monotonic()
        ok = await screen.until(open_state("magnifier", True), screen.reported_at, timeout=2.0)
        return ok, _time.monotonic() - t0

    ok, took = asyncio.run(go())
    assert ok
    wall_clock.within(took, 0.5, "the wait")


def test_the_cells_own_title_and_outlines_with_none_drawn_mean_the_cells(world):
    """Re-check N3: "the cell outlines" asked which; "fade the outlines" faded the app's (none) outlines."""
    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.items[f"layer:{population.id}"]["title"] = population.title  # as the studio reports it
    studio.report()
    assert present(registry, world, what="the cell outlines", do="opacity", value=0.5) == f"{population.title} at 50% opacity"
    assert present(registry, world, what="the outlines", do="opacity", value=0.4) == f"{population.title} at 40% opacity"


def test_a_change_that_never_showed_never_undoes_a_later_one(world, monkeypatch):
    """Re-check N4: the undo after a timeout put the old style back over a change made since."""
    from orchestrator import screen as screen_module
    from orchestrator.population import layer_of

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)
    registry, screen, studio = setup(world, deaf=True)
    population, _ = cells(world)
    layer = layer_of(world)

    async def go():
        with using(issue(world, "turn")):
            task = asyncio.ensure_future(registry.tools["present"].handler(
                ToolContext("chat", 1, None, "fade them"), {"what": population.id, "do": "opacity", "value": 0.5}))
            await asyncio.sleep(0.05)
            later = dict(layer.style, opacity=0.8)
            layer.style = later  # another change, made while the first waited
            with pytest.raises(ToolFailed):
                await task
            return later

    later = asyncio.run(go())
    assert layer.style is later


def test_a_users_style_change_keeps_what_it_replaced(world):
    """Re-check N6: `was` was read from the item's top level, None for a colour or fill."""
    _, screen, studio = setup(world)
    studio.items["outlines"] = {"id": "outlines", "kind": "outlines", "visible": True, "opacity": 1.0,
                                "style": {"colour": "pink", "fill": False}, "z": 2}
    studio.report()
    studio.items["outlines"]["style"]["colour"] = "yellow"
    studio.report(user=[{"id": "outlines", "prop": "colour"}])
    assert screen.owners["outlines"]["colour"]["by"] == "user" and screen.owners["outlines"]["colour"]["was"] == "pink"


def test_a_picture_is_shown_in_the_magnifier_only_once_the_studio_reports_it(world, monkeypatch):
    """Re-check N5: enhance, snapshot, compare and the rest said "shown in the magnifier" with no report."""
    from orchestrator import screen as screen_module
    from orchestrator.screen import picture_shown

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)
    screen = Screen()
    world.screen = screen
    studio = Studio(screen)
    screen.broadcast = lambda m: asyncio.get_running_loop().call_soon(studio.report) if m.get("type") == "screen-ask" else None
    studio.report()
    from orchestrator.magnifier import Magnifier
    magnifier = Magnifier(world)
    with pytest.raises(ToolFailed) as refused:
        asyncio.run(picture_shown(magnifier, b"jpeg", "a picture"))  # the studio never shows it
    assert "not shown in the magnifier" in str(refused.value)
    world.broadcast = studio.hear
    url = asyncio.run(picture_shown(magnifier, b"jpeg", "a picture"))
    assert studio.items["magnifier"]["source"] == url
    assert studio.items["magnifier"]["pictures"] == [{"source": url, "scale": 1}]


# --- the re-check of M1.5b at 5d0ced0 (fixed in M1.5c) ----------------------------------------------------
def test_hiding_and_showing_the_same_cells_keeps_their_selection(world):
    """Re-check N1's side effect: a hide set `shown` to None, so showing them again dropped the selection."""
    from orchestrator.population import layer_of

    registry, screen, studio = setup(world)
    population, _ = cells(world)
    present(registry, world, what=population.id, do="emphasise", by="area", top=3, rest="fade")
    present(registry, world, what=population.id, do="hide")
    present(registry, world, what=population.id, do="show")
    assert len(layer_of(world).style["emphasis"]["members"]) == 3


def test_the_outlines_with_none_of_the_apps_mean_the_cells_even_hidden_and_a_title_beside_other_words_asks(world):
    """Re-check N3: "show the outlines" after hiding the cells said the app's outlines were shown; "dim the live
    view under the cell outlines" picked the cells by their title."""
    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.items[f"layer:{population.id}"]["title"] = population.title
    studio.report()
    present(registry, world, what="the outlines", do="hide")
    assert present(registry, world, what="the outlines", do="show") == f"{population.title} shown"
    with pytest.raises(ToolFailed) as asked:
        present(registry, world, what="dim the live view under the cell outlines", do="opacity", value=0.5)
    assert "which one:" in str(asked.value)


def test_two_changes_that_never_showed_leave_the_style_the_studio_last_showed(world, monkeypatch):
    """Re-check N4: A (50%) and B (80%) both timed out, and B's undo put back A's 50%."""
    from orchestrator import screen as screen_module
    from orchestrator.population import layer_of

    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.report()
    present(registry, world, what=population.id, do="opacity", value=0.9)  # shown: the last confirmed style
    studio.deaf = True
    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)

    async def go():
        async def one(value):
            with using(issue(world, "turn")):
                with pytest.raises(ToolFailed):
                    await registry.tools["present"].handler(ToolContext("chat", 1, None, "fade"),
                                                            {"what": population.id, "do": "opacity", "value": value})
        await asyncio.gather(one(0.5), one(0.8))

    asyncio.run(go())
    assert layer_of(world).style["opacity"] == 0.9


def test_a_studio_in_a_hidden_tab_still_counts(world):
    """Re-check, new item 1: after 30 s hidden, `showing()` turned false and the waits disagreed."""
    clock = [1_000.0]
    screen = Screen(clock=lambda: clock[0])
    studio = Studio(screen)
    studio.report(visible=False)
    clock[0] += 120.0  # hidden two minutes: it draws nothing, so it has not reported
    assert screen.showing()


# --- M1.5c's review, S8: the studio's own controls and Open on every kind -------------------------------------
def test_the_studios_own_controls_are_the_users_change(world):
    """The `present` command (the cells' controls, Made today's Open) runs the same verbs as the user's."""
    from orchestrator.commands import COMMANDS

    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.report()
    app = SimpleNamespace(brain=SimpleNamespace(services=SimpleNamespace(hub=world, magnifier=None, presenter=None, slidemap=None)))
    reply = asyncio.run(COMMANDS["present"](app, None, {"type": "present", "what": population.id, "do": "opacity", "value": 0.5}))
    assert reply["ok"] and screen.owner(f"layer:{population.id}", "opacity") == "user"


def test_open_works_for_every_kind_whose_entry_says_it_can_be_opened(world, tmp_path):
    """Review item 2: Open on cell outlines said "can't be opened"; every kind listing "open" opens."""
    import cv2
    import numpy as np
    from orchestrator.resources import ACTIONS, Index, Results

    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.report()
    pictures = {"k": cv2.imencode(".jpg", np.full((20, 20, 3), 90, np.uint8))[1].tobytes()}
    world.resources = Index(world, results=Results(tmp_path / "results", pictures=pictures.get))
    result = world.resources.results.add("track_motion", {}, "14 of 40 moving", pictures=["k"])


    shown_rid = []

    def show(rid, index=None):
        shown_rid.append(rid)
        studio.items["viewer"] = {"id": "viewer", "kind": "viewer", "open": True, "shows": rid, "z": 11}
        return "its work"

    services = SimpleNamespace(hub=world, magnifier=Magnifier(world), slidemap=None, agent=None,
                               presenter=SimpleNamespace(show=show), publish_settings=None, log=None, history_log=None)
    registry2 = Registry()
    add_present_tools(registry2, services)
    opened = {}
    for kind, entry_id in (("population", population.id), ("result", result["id"])):
        assert "open" in ACTIONS[kind]
        opened[kind] = present(registry2, world, what=entry_id, do="open")
    assert opened["population"] == f"{population.title} shown"
    assert opened["result"].startswith("Opened existing Motion · ")


def test_cells_shown_while_none_is_in_place_are_shown_on_their_own_view(world):
    """Acceptance flow 5 on footage: "pull up the cells from earlier" said "shown" with nothing drawn (the view had
    changed since). They are shown on the view they were made on, in the magnifier, and that is said."""
    registry, screen, studio = setup(world)
    population, _ = cells(world)
    present(registry, world, what=population.id, do="hide")
    world.frame(0.0, 0.0, epoch=1)  # the view changed: none of them can be checked in place now

    from orchestrator.magnifier import Magnifier
    services = SimpleNamespace(hub=world, magnifier=Magnifier(world), presenter=None,
                               slidemap=None, agent=None, publish_settings=None, log=None, history_log=None)
    registry2 = Registry()
    add_present_tools(registry2, services)
    said = present(registry2, world, what=population.id, do="show")
    assert said.startswith(f"{population.title} shown; none of the 6 is in place on the live view now")
    assert said.endswith("in the magnifier")


@pytest.mark.parametrize('loaded', [[], ['/saved'], ['/outside']])
def test_picture_arrangement_receipt_rejects_unloaded_primary_or_peer(loaded):
    from orchestrator.screen import magnifier_arrangement

    report = {'items': [{'id': 'magnifier', 'open': True, 'shows': 'o4', 'source': '/saved',
                        'beside': '/outside', 'pictures': [{'source': url, 'scale': 1} for url in loaded]}]}
    assert not magnifier_arrangement('/saved', 'o4', ['/saved', '/outside'], '/outside')(report)
    report['items'][0]['pictures'] = [{'source': url, 'scale': 1} for url in ['/saved', '/outside']]
    assert magnifier_arrangement('/saved', 'o4', ['/saved', '/outside'], '/outside')(report)
    report['items'][0]['shows'] = 'o5'
    assert not magnifier_arrangement('/saved', 'o4', ['/saved', '/outside'], '/outside')(report)


def test_layer_change_origin_is_recorded_and_unknown_history_stays_unknown():
    screen = Screen(clock=lambda: 100)
    screen._own('layer:e1', 'opacity', 'request:manual', 1, actor='user')
    assert screen.last_change('layer:e1') == {'by': 'user', 'at': 100}
    screen._own('layer:e1', 'opacity', 'request:model', 1, actor='assistant')
    assert screen.last_change('layer:e1') == {'by': 'assistant', 'at': 100}
    screen.owners['layer:e1']['opacity'] = {'by': 'request:old', 'at': 101, 'was': 1}
    assert screen.last_change('layer:e1') is None
    assert screen.last_change('layer:missing') is None


@pytest.mark.parametrize('kind,actor', [('command', 'user'), ('turn', 'assistant')])
def test_layer_origin_survives_request_ownership(world, kind, actor):
    registry, screen, studio = setup(world)
    population, _ = cells(world)
    studio.report()
    async def go():
        with using(issue(world, kind)):
            return await registry.tools['present'].handler(ToolContext('studio' if kind == 'command' else 'chat', 1, None, 'fade'),
                {'what': population.id, 'do': 'opacity', 'value': 0.5})
    asyncio.run(go())
    assert screen.owner(f'layer:{population.id}', 'opacity').startswith('request:')
    assert screen.last_change(f'layer:{population.id}')['by'] == actor
