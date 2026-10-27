""""Close that" is one rule (Slice 5, 2026-10-11): the window in front, from the studio's report (screen.frontmost),
whichever closing tool the model picked: present close or hide with no window named, close_result when its own window
is not open. A tool whose own window is open closes it, as asked. close_magnifier closes its own kind only: the
magnifier panel, else a live magnification window (T2, live 2026-10-11 16:36:38: its fallback to the window in front
closed the reference photo, and the answer said the live magnification was closed)."""
import asyncio
from types import SimpleNamespace

from orchestrator.brain.registry import Registry, ToolContext
from orchestrator.brain.toolbox.view import add_close_magnifier
from orchestrator.screen import frontmost
from orchestrator.ticket import issue, using
from tests.test_population import world  # noqa: F401  (the fixture)
from tests.test_screen import present, setup


def windows(studio):
    studio.items["viewer"] = {"id": "viewer", "kind": "viewer", "open": True, "z": 10}
    studio.items["slidemap"] = {"id": "slidemap", "kind": "slidemap", "open": True, "z": 3}
    studio.report()  # magnifier z 5 (test_screen's studio), the viewer in front, the slide map behind


def test_the_window_in_front_is_the_open_one_stacked_highest():
    items = [{"id": "live", "kind": "live", "z": 0}, {"id": "magnifier", "kind": "magnifier", "open": True, "z": 5},
             {"id": "viewer", "kind": "viewer", "open": False, "z": 10}, {"id": "card", "kind": "card", "open": True, "z": 7},
             {"id": "layer:e1", "kind": "population", "visible": True, "z": 20}]
    assert frontmost(items)["id"] == "card"  # the closed viewer and the cells' layer are no windows to close
    assert frontmost([{"id": "live", "kind": "live", "z": 0}]) is None and frontmost(None) is None


def test_close_that_or_hide_that_closes_the_window_in_front(world):
    registry, screen, studio = setup(world)
    windows(studio)
    said = present(registry, world, what="that", do="close")
    assert studio.items["viewer"]["open"] is False and studio.items["magnifier"]["open"] is True, said
    said = present(registry, world, what="", do="hide")  # "hide that": a window is closed, never hidden
    assert studio.items["magnifier"]["open"] is False and studio.items["slidemap"]["open"] is True, said


LIVE = {"kind": "live", "media": "capture"}  # a live magnification's source (live_sources.object_source)
PHOTO = {"kind": "saved-image", "media": "reference"}


def close_magnifier_with(world, monkeypatch, items):
    """close_magnifier, the magnifier panel not open, over a studio reporting `items` besides its own panels."""
    from orchestrator.brain.toolbox import present as present_module

    registry, screen, studio = setup(world)
    windows(studio)
    studio.items.update({item["id"]: item for item in items})
    studio.report()
    closed = []

    async def native(services, ctx, args):
        closed.append(args)
        return "closed the window"

    monkeypatch.setattr(present_module, "_native_present", native)
    services = SimpleNamespace(hub=world, magnifier=None, presenter=None, slidemap=None, agent=None, publish_settings=None,
                               log=lambda record: None, history_log=None, presentations=object(),
                               prepared_sources=object(), workspace_context=lambda: None)
    tools = Registry()
    add_close_magnifier(tools, SimpleNamespace(open=False, hub=world), services)

    async def go():
        with using(issue(world, "turn")):
            return await tools.tools["close_magnifier"].handler(ToolContext("chat", 1, None, "close the magnification"), {})

    return asyncio.run(go()), closed, studio


def test_the_magnifier_is_its_own_kind_a_live_magnification_window_never_the_window_in_front(world, monkeypatch):
    """Live 2026-10-11 16:36:38 ("close the magnification and then re-highlight"): no magnifier panel was open, so
    close_magnifier closed the window in front, the reference photo, and the answer said the live magnification was
    closed. The magnifier is its own kind: a live magnification window (native, a live capture of an outline), the one
    in front first, closed by its instance; a photo in front of it is left open."""
    said, closed, studio = close_magnifier_with(world, monkeypatch, [
        {"id": "w-live-old", "kind": "magnifier", "open": True, "z": 12, "sourceRef": LIVE},
        {"id": "w-live", "kind": "magnifier", "open": True, "z": 20, "sourceRef": LIVE},
        {"id": "w-photo", "kind": "image", "open": True, "z": 30, "sourceRef": PHOTO}])
    assert closed == [{"do": "close", "instance": {"instanceId": "w-live"}}]
    assert said == "the live magnification window: closed the window"
    assert studio.items["viewer"]["open"] is True  # the legacy panel behind it is left alone


def test_with_no_magnifier_open_close_magnifier_says_so_and_closes_nothing(world, monkeypatch):
    said, closed, studio = close_magnifier_with(world, monkeypatch, [
        {"id": "w-photo", "kind": "image", "open": True, "z": 30, "sourceRef": PHOTO}])
    assert closed == [] and studio.items["viewer"]["open"] is True
    assert said == "the magnifier was not open, and no live magnification window is open: nothing was closed"


def test_closing_an_entry_closes_what_shows_it(monkeypatch):
    """Live 2026-10-11: "pull up the snapshot from earlier", then "close that": present(close, d8) was refused "entry
    can't be closed" and the snapshot stayed. Closing an entry closes the window or panel showing it, or says none does."""
    import pytest

    from orchestrator.brain.registry import ToolFailed
    from orchestrator.brain.toolbox import present as present_module

    done = []

    async def native(services, ctx, args):
        done.append(("native", args))
        return "closed the window"

    async def legacy(services, args, by=None, ctx=None):
        done.append(("panel", args))
        return "closed the magnifier"

    monkeypatch.setattr(present_module, "_native_present", native)
    monkeypatch.setattr(present_module, "act", legacy)
    entry = SimpleNamespace(id="d8", kind="snapshot", title="Snapshot · 11:23")
    shown_d8 = SimpleNamespace(picture_now=lambda: {"kind": "image", "resource": {"id": "d8"}})
    services = SimpleNamespace(presentations=object(), prepared_sources=object(), workspace_context=lambda: None,
                               magnifier=shown_d8)
    window = {"id": "w-9", "kind": "image", "open": True, "z": 20,
              "sourceRef": {"kind": "saved-image", "identity": {"resource": {"id": "d8", "day": "2026-10-11"}}}}
    magnifier = {"id": "magnifier", "kind": "magnifier", "open": True, "z": 5}

    async def close(items):
        return await present_module.close_entry(services, None, "d8", entry, {"items": items})

    assert asyncio.run(close([magnifier, window])) == "closed the window"
    assert done[-1] == ("native", {"do": "close", "instance": {"instanceId": "w-9"}})
    assert asyncio.run(close([magnifier])) == "closed the magnifier"
    assert done[-1] == ("panel", {"what": "magnifier", "do": "close"})
    services.magnifier = SimpleNamespace(picture_now=lambda: {"kind": "image", "resource": {"id": "d3"}})
    with pytest.raises(ToolFailed, match="not open on screen"):  # the magnifier shows something else: left open
        asyncio.run(close([magnifier]))


def test_a_closing_tool_whose_window_is_not_open_closes_the_window_in_front(world):
    from orchestrator.brain.toolbox.view import add_result_tools

    registry, screen, studio = setup(world)
    windows(studio)
    logged = []
    services = SimpleNamespace(hub=world, magnifier=None, presenter=None, slidemap=None, agent=None, publish_settings=None,
                               log=logged.append, history_log=None)
    tools = Registry()
    add_result_tools(tools, None, None, SimpleNamespace(open=False), hub=world, services=services)

    async def go():
        with using(issue(world, "turn")):
            return await tools.tools["close_result"].handler(ToolContext("chat", 1, None, "close that"), {})

    said = asyncio.run(go())
    assert said.startswith("the result viewer was not open; ") and said.endswith("(the window in front)")
    assert studio.items["viewer"]["open"] is False  # the viewer was in front
    assert logged and logged[-1]["kind"] == "override" and logged[-1]["rule"] == "close-frontmost"
    for item in ("magnifier", "slidemap"):
        studio.items[item]["open"] = False
    studio.report()
    # Nothing open to close: nothing done, and said, so no answer says it closed something (live 2026-10-11).
    assert asyncio.run(go()) == "the result viewer was not open, and nothing else is open to close: nothing was closed"


def test_closing_what_this_request_already_closed_is_done_not_failed(tmp_path):
    """Live 2026-10-11 16:17:34 ("close the magnification"): close_magnifier closed the live magnification window, the
    completion round then closed the same window again with present, which failed ("the prepared token is unavailable
    or no longer owned"), and the answer said the window could not be closed. A close of a window an earlier step of
    the same request closed is done, said as such; a window closed by anything else is not claimed as this request's."""
    from orchestrator.ticket import issue as issue_ticket
    from tests.test_workspace_actions_app import WORKSPACE, built, open_window, plan, saved_picture

    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)

    async def go():
        window, _, _ = await open_window(app, entry)
        close = ("present", {"do": "close", "instance": {"instanceId": window}})
        runs, _ = await plan(app, [close, close])  # one plan, two steps
        assert [r.ok for r in runs] == [True, True], [r.summary for r in runs]
        assert "already closed by an earlier step of this request" in runs[1].summary
        assert not studio.store().get(WORKSPACE, window).open

        window, _, _ = await open_window(app, entry)
        close = ("present", {"do": "close", "instance": {"instanceId": window}})
        ticket = issue_ticket(app.hub, "turn", asked=app.day.here(), channel="chat")
        first, ctx = await plan(app, [close], ticket=ticket)
        again, _ = await plan(app, [close], ctx=ctx, ticket=ticket)  # the same request's next round
        assert first[0].ok is True and again[0].ok is True, again[0].summary
        assert "already closed by an earlier step of this request" in again[0].summary

        later, _ = await plan(app, [close])  # another request: not this request's close
        assert "this request" not in later[0].summary

    asyncio.run(go())
