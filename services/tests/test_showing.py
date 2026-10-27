from orchestrator.magnifier import Magnifier
"""Show me (orchestrator/showing.py; the partner §2.3, ADR 0015; UX §2 "The Show me experience"): a
showing owns the changes its beats make (done once the studio shows them); stopping it puts back only
what it still owns, leaves what the user changed meanwhile, says what it could not put back, and
touches nothing else. show_me gives the brain the finding's records, each with how it is shown."""
import asyncio
import time
from types import SimpleNamespace

from orchestrator.brain.registry import Registry, ToolContext
from orchestrator.brain.toolbox import Services
from orchestrator.brain.toolbox.present import act
from orchestrator.brain.toolbox.showing import add_showing_tools, evidence_lines
from orchestrator.capture import RUN
from orchestrator.presentations import PresentationError, Presentations
from orchestrator.showing import showings_of, stop
from orchestrator.ticket import issue, using
from tests.test_population import cells, world  # noqa: F401  (the fixture)
from tests.test_screen import Studio, setup


class AskedStudio(Studio):
    """Also answers the server's request for a report (screen.Screen.until)."""

    def hear(self, message: dict) -> None:
        super().hear(message)
        if message.get("type") == "screen-ask":
            asyncio.get_running_loop().call_soon(self.report)


def as_the_studio_does(studio, message: dict) -> dict:
    """A place or size as the real studio applies it (main.ts, present): any rect moves the magnifier to its x
    and y; only a resize changes its width, and its height follows; a move keeps its size."""
    rect = (message.get("set") or {}).get("rect") if message.get("type") == "present" else None
    now = (studio.items.get(message.get("id")) or {}).get("rect")
    if not (isinstance(rect, list) and len(rect) == 4 and isinstance(now, list) and len(now) == 4):
        return message
    x, y, w = rect[:3]
    if message.get("verb") == "resize":
        rect = [x, y, w, round(now[3] * w / now[2]) if now[2] else now[3]]
    else:
        rect = [x, y, now[2], now[3]]
    return dict(message, set=dict(message["set"], rect=rect))


def studio_world(world):
    registry, screen, studio = setup(world)
    hear = studio.hear

    def answering(message):  # the same studio (its deafness included), now also answering the server's asks
        hear(as_the_studio_does(studio, message))
        if message.get("type") == "screen-ask":
            asyncio.get_running_loop().call_soon(studio.report)

    studio.hear = answering
    services = SimpleNamespace(hub=world, magnifier=None, presenter=None, slidemap=None, agent=None, publish_settings=None,
                               log=None, history_log=None)
    return services, screen, studio


def day_reset_services(world, app):
    """Real reset owner/plumbing for the synthetic App-method fixtures."""
    def refuse_source(_source):
        raise PresentationError("source_unavailable", "the day-reset fixture has no native source authority")

    services = Services(hub=world, magnifier=app.magnifier, presenter=app.presenter,
                        subjects=app.subjects,
                        presentations=Presentations(resolve_source=refuse_source, app_run=RUN))
    world.presentations = services.presentations
    return services


def test_a_showing_owns_its_beats_and_stopping_puts_back_only_what_it_still_owns(world):
    services, screen, studio = studio_world(world)
    population, _ = cells(world)
    pid = f"layer:{population.id}"

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": "live view", "do": "opacity", "value": "0.4"})
            await act(services, {"what": population.id, "do": "emphasise", "by": "area", "top": 5, "rest": "fade"})
        # Meanwhile the user brightens the live view again: that property is now theirs.
        studio.items["live"]["opacity"] = 0.8
        studio.report(user=[{"id": "live", "prop": "opacity"}])
        assert screen.owner("live", "opacity") == "user" and screen.owner(pid, "emphasis") == showing.owner
        return await stop(services, why="asked")

    said = asyncio.run(go())
    assert studio.items["live"]["opacity"] == 0.8  # the user's change stays
    assert "emphasis" not in studio.items[pid].get("style", {})  # the showing's emphasis is put back
    assert "Put back: the cell outlines as they were" in said
    assert "Left as changed since: the live view's brightness (changed by you since)" in said
    assert showings_of(world).current is None


def test_a_change_outside_the_showings_request_is_not_the_showings(world):
    services, screen, studio = studio_world(world)

    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
        with using(issue(world, "turn")):  # a later request
            await act(services, {"what": "live view", "do": "opacity", "value": "0.5"})
        return await stop(services)

    said = asyncio.run(go())
    assert studio.items["live"]["opacity"] == 0.5  # not the showing's: never put back
    assert "Nothing of it needed putting back" in said


def test_a_panel_it_closed_is_said_not_reopened_and_stopping_twice_is_harmless(world):
    services, screen, studio = studio_world(world)

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            change = screen.send("magnifier", "magnifier", "close", {"open": False}, showing.owner)
            assert await screen.confirm(change, lambda r: True)
            assert showings_of(world).noted(change, screen) == "owned"
        first = await stop(services)
        return first, await stop(services)

    first, second = asyncio.run(go())
    assert "Not put back: the magnifier (it was open before; reopen it from where it was)" in first
    assert second == "nothing is being shown"


def test_show_me_gives_the_findings_records_with_how_each_is_shown(world):
    result = {"title": "Odd heads", "one_liner": "Two cells have unusual heads.",
              "evidence": [{"id": "e3", "found": True, "title": "Cell outlines · 18:04", "kind": "population"},
                           {"id": "d4", "found": False},
                           {"id": "named-s2", "found": True, "title": "sperm cell · 18:06", "kind": "named-outline"},
                           {"id": "x9", "found": None, "why": "the index did not answer"},
                           {"id": "d7", "found": True, "title": "Count · 18:08", "kind": "result"}],
              "session_claims": [{"point": "c4 is odd", "evidence": ["d4"], "found": False}]}
    lines = evidence_lines(result)
    # Cell outlines are drawn on the picture (show); a saved result opens in the Data reader (open; ticket 66, D6).
    assert lines[0] == '- Cell outlines · 18:04 (e3): show it with present(what="e3", do="show")'
    assert "d4: not found in the session's index; say so" in lines[1]
    assert "not shown on screen (a thing named earlier is shown by outlining it again" in lines[2]
    assert "could not be checked (the index did not answer)" in lines[3]
    assert lines[4] == '- Count · 18:08 (d7): show it with present(what="d7", do="open")'

    session = SimpleNamespace(rid="r2", name="Researcher 2", result=result)
    services = SimpleNamespace(hub=world, research=object(), presenter=object(), log=None,
                               desk=SimpleNamespace(find=lambda ref: session))
    registry = Registry()
    add_showing_tools(registry, services)

    async def go():
        with using(issue(world, "turn")):
            return await registry.tools["show_me"].handler(ToolContext("chat", 1, None, "show me"), {"researcher": "2"})

    said = asyncio.run(go())
    assert said.startswith("Showing Researcher 2's finding (sh") and 'untrusted="true"' in said
    assert "1 of its points rest on records not in the session" in said
    assert showings_of(world).current.rid == "r2"


def test_stop_is_the_same_tool_and_a_new_showing_stops_the_last(world):
    session = SimpleNamespace(rid="r2", name="Researcher 2", result={"title": "T", "summary": "S"})
    services = SimpleNamespace(hub=world, research=object(), presenter=object(), log=None,
                               desk=SimpleNamespace(find=lambda ref: session))
    registry = Registry()
    add_showing_tools(registry, services)

    async def call(args):
        with using(issue(world, "turn")):
            return await registry.tools["show_me"].handler(ToolContext("chat", 1, None, "show me"), args)

    first = asyncio.run(call({"researcher": "2"}))
    second = asyncio.run(call({"researcher": "2"}))
    assert first.startswith("Showing Researcher 2's finding (sh1)")
    assert second.startswith("stopped showing Researcher 2's finding") and "(sh2)" in second
    assert asyncio.run(call({"stop": True})).startswith("stopped showing")
    assert asyncio.run(call({"stop": True})) == "nothing is being shown"


# --- the review of #162 ----------------------------------------------------------------------------------
def beats(world, services, *calls, stop_after=False):
    """Run beats in one request that started a showing; then stop. Returns (said, request)."""
    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            for args in calls:
                await act(services, args)
        return await stop(services) if stop_after else None
    return asyncio.run(go())


def test_two_beats_on_one_property_put_back_what_was_there_before_the_first(world):
    """B1: live 1.0 -> 0.4 -> 0.3 ends at 1.0; top 5 then top 2 ends with no emphasis."""
    services, screen, studio = studio_world(world)
    population, _ = cells(world)
    pid = f"layer:{population.id}"
    said = beats(world, services, {"what": "live view", "do": "opacity", "value": "0.4"},
                 {"what": "live view", "do": "opacity", "value": "0.3"},
                 {"what": population.id, "do": "emphasise", "by": "area", "top": 5},
                 {"what": population.id, "do": "emphasise", "by": "area", "top": 2}, stop_after=True)
    assert studio.items["live"]["opacity"] == 1.0 and "the live view at full brightness" in said
    assert "emphasis" not in studio.items[pid].get("style", {})


def test_a_colouring_by_a_measurement_comes_back_with_its_legend_and_values(world):
    """B1: cells coloured by size before; pink during the showing; by size again after, legend and all."""
    from orchestrator.population import layer_of

    services, screen, studio = studio_world(world)
    population, _ = cells(world)

    async def colour():
        with using(issue(world, "turn")):
            await act(services, {"what": population.id, "do": "colour", "value": "size"})
    asyncio.run(colour())
    before = dict(layer_of(world).style)
    said = beats(world, services, {"what": population.id, "do": "colour", "value": "pink"}, stop_after=True)
    after = layer_of(world).style
    assert after.get("colour") == "column" and after.get("legend") == before["legend"] and after.get("values") == before["values"]
    assert "the cell outlines as they were" in said


def test_stop_never_puts_back_what_the_screen_no_longer_shows_as_the_showing_set_it(world):
    """B2: the live view changed meanwhile without a reported user change (as opening work from the shelf
    once was): it is left, and said so."""
    services, screen, studio = studio_world(world)

    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": "live view", "do": "opacity", "value": "0.4"})
        studio.items["live"]["opacity"] = 0.7  # changed, unreported
        studio.report()
        return await stop(services)

    said = asyncio.run(go())
    assert studio.items["live"]["opacity"] == 0.7 and "the live view's brightness (changed since)" in said


def test_after_a_stop_its_request_makes_no_more_beats_and_a_late_one_is_put_back(world, monkeypatch):
    """B3: interrupted means interrupted."""
    import pytest

    from orchestrator.brain.registry import ToolFailed

    services, screen, studio = studio_world(world)

    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": "live view", "do": "opacity", "value": "0.4"})
            hear, held = studio.hear, []
            studio.hear = lambda message: held.append(message) if message.get("type") == "present" and not held else hear(message)
            late = asyncio.create_task(act(services, {"what": "live view", "do": "opacity", "value": "0.3"}))
            await asyncio.sleep(0.05)  # the next beat waits for the studio...
            said = await stop(services)  # ...the user stops meanwhile (and that is shown)...
            studio.hear = hear
            hear(held[0])  # ...then the studio shows the late beat
            await asyncio.sleep(0.05)
            with pytest.raises(ToolFailed, match="the showing was stopped, so this was put back"):
                await late
            with pytest.raises(ToolFailed, match="was stopped: nothing more of it is shown"):
                await act(services, {"what": "live view", "do": "opacity", "value": "0.2"})
            return said

    said = asyncio.run(go())
    assert studio.items["live"]["opacity"] == 1.0 and "the live view at full brightness" in said


def test_a_beat_that_changed_nothing_is_not_the_showings(world):
    """B4: the user had dimmed the live view to 0.4; a beat to 0.4 changes nothing and owns nothing."""
    services, screen, studio = studio_world(world)
    studio.items["live"]["opacity"] = 0.4
    studio.report(user=[{"id": "live", "prop": "opacity"}])
    said = beats(world, services, {"what": "live view", "do": "opacity", "value": "0.4"}, stop_after=True)
    assert "changed by you since" not in said and "Nothing of it needed putting back" in said


def test_an_entry_opened_as_a_beat_is_the_showings_and_a_restore_the_studio_never_shows_is_said(world, monkeypatch):
    """Closes two surviving mutations: open_entry's noted, and the layer restore's check."""
    import orchestrator.screen as screen_module

    services, screen, studio = studio_world(world)
    population, _ = cells(world)
    pid = f"layer:{population.id}"
    from orchestrator.population import layer_of

    layer_of(world).hide()
    studio.items.pop(pid, None)

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": population.id, "do": "show"})
            await act(services, {"what": population.id, "do": "fill"})
            assert (pid, "visible") in showing.touched or (pid, "open") in showing.touched
        monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)
        original = studio.hear
        studio.hear = lambda message: None if message.get("type") == "layer" else original(message)  # it never shows the restore
        return await stop(services)

    said = asyncio.run(go())
    assert "Not put back" in said


def test_another_session_stops_the_showing_as_stop_does(world, monkeypatch):
    """S3: another session opening runs the same stop (what it dimmed is put back), never only drops the bar."""
    from orchestrator import app as app_module, commands

    ran = []

    async def run_tool(app, name, args):
        ran.append((name, args))
        return {"ok": True}

    monkeypatch.setattr(commands, "run_tool", run_tool)
    services, screen, studio = studio_world(world)
    showings_of(world).start("r2", "Researcher 2's finding", "request:9")
    class Fake(SimpleNamespace):
        def __getattr__(self, name):  # the rest of the app: nothing there in this test
            return None

    fake = Fake(hub=world, magnifier=SimpleNamespace(close=lambda: None), presenter=SimpleNamespace(reset_all=lambda: None),
                slidemap=None, enrich=SimpleNamespace(entries={}, wire_full=lambda: {"type": "notes"}),
                subjects=SimpleNamespace(reset=lambda: None), brain=SimpleNamespace(voice_notes=[]),
                research_list=lambda: {"type": "research-list"})
    fake.services = day_reset_services(world, fake)
    fake.manual_commands = commands.NativeManualCommands(fake)
    world.layer = SimpleNamespace(hide=lambda: None)

    async def go():
        app_module.App._clear_view(fake)
        await asyncio.sleep(0)

    asyncio.run(go())
    assert ran == [("show_me", {"stop": True, "why": "another session was opened"})]


def test_an_index_entry_opened_as_a_beat_is_the_showings(world, tmp_path):
    """Closes the surviving mutation of open_entry's noted: a result opened in the magnifier as a beat."""
    import cv2
    import numpy as np

    from orchestrator.resources import Index, Results

    services, screen, studio = studio_world(world)
    studio.items["magnifier"].update(open=True, source="live", shows="o1")

    magnifier = Magnifier(world)
    pictures = {"k": cv2.imencode(".jpg", np.full((20, 20, 3), 90, np.uint8))[1].tobytes()}
    world.resources = Index(world, results=Results(tmp_path / "results", pictures=pictures.get))
    entry = world.resources.results.add("track_motion", {}, "14 of 40 moving", pictures=["k"])
    services.magnifier = magnifier

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": entry["id"], "do": "open"})
            return showing

    showing = asyncio.run(go())
    assert ("magnifier", "shows") in showing.touched



# --- the re-check of #162 (its probes, as tests) -----------------------------------------------------------
class Presenter:
    """A stand-in result viewer: what it shows is what the studio reports; `open` is the server's own state."""

    def __init__(self, studio, open_=False, rid=None):
        self.studio, self.calls, self.open = studio, [], open_
        studio.items["viewer"] = {"id": "viewer", "kind": "viewer", "open": open_, "shows": rid, "z": 6}
        studio.report()

    def show(self, rid):
        self.calls.append(("show", rid))
        self.open = True
        self.studio.items["viewer"].update(open=True, shows=rid)
        asyncio.get_running_loop().call_soon(self.studio.report)

    def close(self):
        self.calls.append(("close",))
        self.open = False
        self.studio.items["viewer"]["open"] = False  # the studio keeps the rid (viewer.state.rid)
        asyncio.get_running_loop().call_soon(self.studio.report)


async def beat_on_viewer(world, screen, studio, showing, rid):
    change = screen.send("viewer", "entry", "open", {"open": True, "shows": rid}, showing.owner)
    studio.items["viewer"].update(open=True, shows=rid)
    studio.applied.append(change.id)
    studio.report()
    assert await screen.confirm(change, lambda r: True)
    assert showings_of(world).noted(change, screen) == "owned"


def test_work_the_user_opened_from_the_shelf_is_never_closed_by_stop(world):
    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=False)

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
            await beat_on_viewer(world, screen, studio, showing, "r2")
        studio.items["viewer"].update(open=True, shows="r5")  # the user opens r5 from the shelf
        studio.report(user=[{"id": "viewer", "prop": "shows"}])
        return await stop(services)

    said = asyncio.run(go())
    assert studio.items["viewer"]["open"] and studio.items["viewer"]["shows"] == "r5"
    assert "the result viewer (changed by you since)" in said and ("close",) not in services.presenter.calls


def test_a_viewer_the_user_closed_is_never_reopened_by_stop(world):
    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=True, rid="r1")

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
            await beat_on_viewer(world, screen, studio, showing, "r3")
        studio.items["viewer"]["open"] = False  # the user's x
        services.presenter.open = False
        studio.report(user=[{"id": "viewer", "prop": "open"}])
        return await stop(services)

    said = asyncio.run(go())
    assert not studio.items["viewer"]["open"] and not services.presenter.calls
    assert "the result viewer (changed by you since)" in said


def test_a_picture_in_the_magnifier_and_a_resize_are_not_said_changed_when_nobody_changed_them(world, tmp_path):
    import cv2
    import numpy as np

    from orchestrator.resources import Index, Results

    services, screen, studio = studio_world(world)
    studio.items["magnifier"].update(open=True, source="live", shows="o1")
    studio.report()  # as the studio always reports its magnifier: live, on o1



    services.magnifier = Magnifier(world)
    from orchestrator.scene import SceneObject

    world.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1,
                                anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click"))
    pictures = {"k": cv2.imencode(".jpg", np.full((20, 20, 3), 90, np.uint8))[1].tobytes()}
    world.resources = Index(world, results=Results(tmp_path / "results", pictures=pictures.get))
    entry = world.resources.results.add("track_motion", {}, "14 of 40 moving", pictures=["k"])
    hear = studio.hear

    def aspect(message):  # the studio keeps the picture's aspect: its height follows the width
        if message.get("type") == "present" and message.get("id") == "magnifier" and "rect" in message.get("set", {}):
            x, y, w, h = message["set"]["rect"]
            message = dict(message, set={"rect": [x, y, w, round(w * 2 / 3)]})
        hear(message)

    studio.hear = aspect

    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": entry["id"], "do": "open"})
            await act(services, {"what": "magnifier", "do": "resize", "rect": [10, 10, 420, 200]})
        return await stop(services)

    said = asyncio.run(go())
    assert "changed since" not in said and "the magnifier back on the live view" in said
    assert studio.items["magnifier"]["source"] == "live"  # killed if open_entry stops sending its picture's source
    assert "the magnifier as big as it was" in said and studio.items["magnifier"]["rect"][2] == 300


def test_a_late_close_that_could_not_be_put_back_says_so(world):
    import pytest

    from orchestrator.brain.registry import ToolFailed

    services, screen, studio = studio_world(world)

    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": "live view", "do": "opacity", "value": "0.4"})
            hear, held = studio.hear, []
            studio.hear = lambda message: held.append(message) if message.get("type") == "present" and not held else hear(message)
            late = asyncio.create_task(act(services, {"what": "magnifier", "do": "close"}))
            await asyncio.sleep(0.05)
            await stop(services)
            studio.hear = hear
            hear(held[0])
            await asyncio.sleep(0.05)
            with pytest.raises(ToolFailed) as failed:
                await late
            return str(failed.value)

    failed = asyncio.run(go())
    assert "so this was put back" not in failed and "could not be put back (the magnifier (it was open before" in failed


def test_a_cells_style_restore_the_studio_never_shows_is_not_put_back_and_layers_are_named_in_words(world, monkeypatch):
    """Kills two surviving mutations: the style restore's own check, and the layer's name."""
    import orchestrator.screen as screen_module

    services, screen, studio = studio_world(world)
    population, _ = cells(world)
    pid = f"layer:{population.id}"

    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": population.id, "do": "emphasise", "by": "area", "top": 5})
            await act(services, {"what": population.id, "do": "fill"})
        studio.items[pid]["style"]["fill"] = False  # the user's own change to the fill
        studio.report(user=[{"id": pid, "prop": "fill"}])
        monkeypatch.setattr(screen_module, "CONFIRM_S", 0.2)
        original = studio.hear
        studio.hear = lambda message: None if message.get("type") == "layer" else original(message)  # it never shows the restore
        return await stop(services)

    said = asyncio.run(go())
    assert "Not put back: the cell outlines' style: the studio has not shown it put back" in said
    assert "the cell outlines' fill (changed by you since)" in said and "layer:" not in said



# --- the third round of #162 ---------------------------------------------------------------------------
def test_a_new_day_never_reopens_yesterdays_work_whatever_the_studio_last_said(world):
    """BLOCKING: the new day closed the viewer; the studio's last report still says open (stale)."""
    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=True, rid="r1")

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
            await beat_on_viewer(world, screen, studio, showing, "r3")
        services.presenter.open = False  # the new day closed it; the studio has not reported yet
        return await stop(services, why="another session was opened")

    said = asyncio.run(go())
    assert services.presenter.calls == [] and "on what it showed before" not in said


def test_what_a_panel_shows_is_put_back_only_while_the_server_has_it_open(world):
    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=True, rid="r1")

    async def go(closed_by_server):
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
            await beat_on_viewer(world, screen, studio, showing, "r3")
        if closed_by_server:
            services.presenter.open = False  # closed on the server (a new day, a restart); the report is stale
        return await stop(services)

    said = asyncio.run(go(False))
    assert ("show", "r1") in services.presenter.calls and "on what it showed before" in said
    services.presenter = Presenter(studio, open_=True, rid="r1")
    showings_of(world).stopped.clear()
    said = asyncio.run(go(True))
    assert services.presenter.calls == [] and "on what it showed before" not in said


def test_a_resize_then_a_move_are_both_put_back_and_a_move_within_6_px_is_still_the_showings(world):
    services, screen, studio = studio_world(world)

    async def go(slack):
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": "magnifier", "do": "resize", "rect": [10, 10, 420, 280]})
            await act(services, {"what": "magnifier", "do": "move", "rect": [200, 50, 420, 280]})
        studio.items["magnifier"]["rect"] = [200 + slack, 50, 420, 280]
        studio.report()
        return await stop(services)

    said = asyncio.run(go(4))
    assert "changed since" not in said and studio.items["magnifier"]["rect"][:3] == [10, 10, 300]
    showings_of(world).stopped.clear()
    said = asyncio.run(go(20))
    assert "the magnifier (changed since)" in said


def test_a_beat_confirmed_while_stop_is_still_putting_back_is_late():
    """The order in noted: a stopped showing first, even while it is still the current one (stop is running)."""
    from orchestrator.screen import Change, Screen
    from orchestrator.showing import Showings

    showings = Showings()
    showing = showings.start("r2", "Researcher 2's finding", "request:7")
    showing.active = False
    showings.stopped["request:7"] = showing  # what stop does first, before any await
    screen = Screen()
    change = Change("c1", "live", "live", "opacity", {"opacity": 0.3}, showing.owner)
    screen._own("live", "opacity", showing.owner, 0.4)
    assert showings.noted(change, screen) == "late"


def test_another_session_says_what_its_stop_did_and_the_tool_keeps_the_reason(world, monkeypatch):
    from orchestrator import app as app_module, commands

    sent = []

    async def run_tool(app, name, args):
        return {"ok": True, "summary": "stopped showing Researcher 2's finding. Put back: the live view at full brightness."}

    monkeypatch.setattr(commands, "run_tool", run_tool)
    services, screen, studio = studio_world(world)
    world.broadcast = sent.append
    showings_of(world).start("r2", "Researcher 2's finding", "request:9")

    class Fake(SimpleNamespace):
        def __getattr__(self, name):
            return None

    fake = Fake(hub=world, magnifier=SimpleNamespace(close=lambda: None), presenter=SimpleNamespace(reset_all=lambda: None),
                enrich=SimpleNamespace(entries={}, wire_full=lambda: {"type": "notes"}), subjects=SimpleNamespace(reset=lambda: None),
                brain=SimpleNamespace(voice_notes=[]), research_list=lambda: {"type": "research-list"})
    fake.services = day_reset_services(world, fake)
    fake.manual_commands = commands.NativeManualCommands(fake)
    world.layer = SimpleNamespace(hide=lambda: None)

    async def go():
        app_module.App._clear_view(fake)
        await asyncio.sleep(0.01)

    asyncio.run(go())
    assert {"type": "hint", "text": "Another session: stopped showing Researcher 2's finding. Put back: the live view at full brightness."} in sent

    logged = []
    session = SimpleNamespace(rid="r2", name="Researcher 2", result={"title": "T", "summary": "S"})
    tools_services = SimpleNamespace(hub=world, research=object(), presenter=object(), log=logged.append,
                                     desk=SimpleNamespace(find=lambda ref: session))
    registry = Registry()
    add_showing_tools(registry, tools_services)

    async def call(args):
        with using(issue(world, "turn")):
            return await registry.tools["show_me"].handler(ToolContext("chat", 1, None, "x"), args)

    asyncio.run(call({"researcher": "2"}))
    asyncio.run(call({"stop": True, "why": "another session was opened"}))
    asyncio.run(call({"researcher": "2"}))
    asyncio.run(call({"stop": True, "why": "anything else"}))
    assert [r["why"] for r in logged if r.get("kind") == "showing-stop"][-2:] == ["another session was opened", "asked"]


# --- the fourth round of #162 ----------------------------------------------------------------------------
class SlideMap:
    """A stand-in slide map: `open` is the server's own state (slidemap.SlideMaps.set_open)."""

    def __init__(self):
        self.open = False

    def set_open(self, value: bool) -> None:
        self.open = bool(value)

    def wire(self) -> dict:
        return {"type": "slidemap-state", "open": self.open}


def test_a_new_day_closes_again_the_slide_map_the_showing_opened_and_skips_only_what_it_closed(world):
    """NEW 1: the new day closes the magnifier and the viewer, not the slide map; the slide map the showing
    opened is closed again and said. Without a slide map of its own, the studio's report says it is open."""
    for holder in (True, False):
        services, screen, studio = studio_world(world)
        studio.items["slidemap"] = {"id": "slidemap", "kind": "slidemap", "open": False, "z": 4}
        services.presenter = Presenter(studio, open_=False)
        services.slidemap = SlideMap() if holder else None
        showings_of(world).stopped.clear()

        async def go():
            with using(issue(world, "turn")) as request:
                showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
                change = screen.send("slidemap", "slidemap", "open", {"open": True}, showing.owner)
                studio.items["slidemap"]["open"] = True
                studio.applied.append(change.id)
                studio.report()
                assert await screen.confirm(change, lambda r: True) and showings_of(world).noted(change, screen) == "owned"
                if services.slidemap is not None:
                    services.slidemap.open = True
                await beat_on_viewer(world, screen, studio, showing, "r3")  # the viewer the showing opened
            services.presenter.close()  # what the new day closes (app._clear_view): the viewer, not the slide map
            await asyncio.sleep(0)
            return await stop(services, why="another session was opened")

        said = asyncio.run(go())
        assert not studio.items["slidemap"]["open"] and "Put back: the slide map closed again" in said, (holder, said)
        assert "the result viewer" not in said and services.presenter.calls == [("close",)]  # closed by the day, never claimed


def test_a_move_after_a_resize_keeps_the_resized_width_and_is_still_the_showings(world):
    """NEW 2: the move's rect carries another width; the studio keeps the resized one, and that is what the
    showing set. An unflagged change of that width since is not the showing's, so it is left."""
    services, screen, studio = studio_world(world)

    async def go(width_since):
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            await act(services, {"what": "magnifier", "do": "resize", "rect": [10, 10, 420, 280]})
            await act(services, {"what": "magnifier", "do": "move", "rect": [50, 50, 300, 200]})
        assert studio.items["magnifier"]["rect"][:3] == [50, 50, 420]
        if width_since:
            studio.items["magnifier"]["rect"] = [50, 50, width_since, 300]
            studio.report()  # the studio's own (its aspect, a drag of its corner): nobody's reported change
        return await stop(services)

    said = asyncio.run(go(None))
    assert "changed since" not in said and studio.items["magnifier"]["rect"][:3] == [10, 10, 300]
    assert "the magnifier as big as it was" in said and "the magnifier where it was" in said
    showings_of(world).stopped.clear()
    said = asyncio.run(go(500))
    assert "the magnifier (changed since)" in said and studio.items["magnifier"]["rect"][2] == 500


def test_a_details_card_or_the_made_today_list_the_showing_closed_is_said_not_claimed(world):
    """NEW 3: the verbs only close them; one the showing closed is said, as for the other panels."""
    services, screen, studio = studio_world(world)
    studio.items["card"] = {"id": "card", "kind": "card", "open": True, "z": 7}
    studio.items["made-today"] = {"id": "made-today", "kind": "made-today", "open": True, "z": 8}
    studio.report()

    said = beats(world, services, {"what": "details card", "do": "close"}, {"what": "made today", "do": "close"},
                 stop_after=True)
    assert "Not put back: the details card (it was open before; open it again from where it was)" in said
    assert "the list of what was made today (it was open before" in said and "Nothing of it" not in said


def test_a_magnifier_the_server_closed_is_not_reopened_to_put_its_picture_back(world, tmp_path):
    """Round 3's survivor: the magnifier's picture is put back only while the server has it open (closed by
    close_magnifier or a new day, not by the user's own x, nothing reports it)."""
    services, screen, studio = studio_world(world)
    studio.items["magnifier"].update(open=True, source="live", shows="o1")
    studio.report()
    zoomed = []
    services.magnifier = SimpleNamespace(zoom=zoomed.append, open=True)
    from orchestrator.scene import SceneObject

    world.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1,
                                anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click"))

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r2", "Researcher 2's finding", f"request:{request.id}")
            # As open_entry sends a picture: `shows` is the entry; the studio keeps the outline it follows.
            change = screen.send("magnifier", "entry", "open", {"open": True, "shows": "x1", "source": "/api/p"}, showing.owner)
            studio.items["magnifier"].update(source="/api/p")
            studio.applied.append(change.id)
            studio.report()
            assert await screen.confirm(change, lambda r: True) and showings_of(world).noted(change, screen) == "owned"
        services.magnifier.open = False  # closed on the server; the studio has not reported it
        return await stop(services)

    said = asyncio.run(go())
    assert zoomed == [] and "back on the live view" not in said  # left open, it is (the test above)


def test_a_change_to_a_panel_before_the_showing_began_is_not_a_change_since(world):
    """Round 3's survivor: only what changed after the showing began is someone else's."""
    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=False)

    async def go():
        studio.items["viewer"]["rect"] = [400, 20, 360, 500]
        studio.report(user=[{"id": "viewer", "prop": "rect"}])  # the user moved it, before
        screen.owners["viewer"]["rect"]["at"] = round(time.time() - 5, 3)
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
            await beat_on_viewer(world, screen, studio, showing, "r3")
        return await stop(services)

    said = asyncio.run(go())
    assert "changed by you since" not in said and "the result viewer closed again" in said


def test_the_showings_own_put_back_is_never_someone_elses_change(world):
    """Round 3's survivor: a put-back (by showing:restore) inside the showing's time is the showing's own doing."""
    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=False)

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
            await beat_on_viewer(world, screen, studio, showing, "r3")
        screen._own("viewer", "rect", "showing:restore", [400, 20, 360, 500])  # an earlier showing's put-back, just now
        return await stop(services)

    said = asyncio.run(go())
    assert "changed by" not in said and "the result viewer closed again" in said


def test_a_late_beat_on_a_panel_is_put_back_by_the_panels_own_rules(world):
    """Round 3's survivor: what a late beat opened in the viewer goes back to what it showed before."""
    from orchestrator.showing import put_back_late

    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=True, rid="r1")

    async def go():
        with using(issue(world, "turn")) as request:
            showing = showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
        await stop(services)
        change = screen.send("viewer", "entry", "open", {"open": True, "shows": "r3"}, showing.owner)
        studio.items["viewer"].update(open=True, shows="r3")
        studio.applied.append(change.id)
        studio.report()
        assert await screen.confirm(change, lambda r: True) and showings_of(world).noted(change, screen) == "late"
        return await put_back_late(services, change)

    put, left = asyncio.run(go())
    assert ("show", "r1") in services.presenter.calls and put == ["the result viewer on what it showed before"] and left == []


def test_the_studios_stop_is_the_users_stop(monkeypatch):
    """Round 3's survivor: Stop on the bar or Escape is logged as the user's Stop."""
    from orchestrator import commands

    called = []

    async def run_tool(app, name, args):
        called.append((name, args))
        return {"ok": True}

    monkeypatch.setattr(commands, "run_tool", run_tool)
    asyncio.run(commands.showing(None, None, {"type": "showing"}))
    assert called == [("show_me", {"stop": True, "why": "the user's Stop"})]


# --- the review of #169 ----------------------------------------------------------------------------------
def test_at_midnight_a_slide_map_the_showing_closed_is_said_and_a_cards_outline_is_gone(world):
    """The new day closes only the magnifier and the viewer: a slide map the showing closed is said (the day did
    not close it); a details card's outline went with the old day, so it is not offered to reopen."""
    services, screen, studio = studio_world(world)
    studio.items["slidemap"] = {"id": "slidemap", "kind": "slidemap", "open": True, "z": 4}
    studio.items["card"] = {"id": "card", "kind": "card", "open": True, "z": 7}
    studio.report()
    services.slidemap = SlideMap()
    services.slidemap.open = True

    async def go():
        with using(issue(world, "turn")) as request:
            showings_of(world).start("r9", "Researcher 9's finding", f"request:{request.id}")
            await act(services, {"what": "slide map", "do": "close"})
            await act(services, {"what": "details card", "do": "close"})
        return await stop(services, why="another session was opened")

    said = asyncio.run(go())
    assert "Not put back: the slide map (it was open before; reopen it from where it was)" in said
    assert "details card" not in said


def test_whether_a_panel_is_open_is_the_servers_own_state_and_else_the_studios_report(world):
    from orchestrator.showing import _open_now

    services, screen, studio = studio_world(world)
    services.presenter = Presenter(studio, open_=True, rid="r1")
    services.presenter.open = False  # the server closed it; the report still says open
    assert _open_now(services, "viewer") is False
    services.presenter = None  # nothing of the server's own: the studio's report
    assert _open_now(services, "viewer") is True
