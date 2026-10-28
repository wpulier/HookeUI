"""A Project opened again draws its cells' layer again on the picture its cells were found on (ticket 67).

The thesis check (2026-10-27): going back to a Project left its Stage without the outline layer, so no result in Data
said "On this Tab" any more. Each session journals which population its layer shows (day/store.py `record_layer`);
hiding it because another session opened changes nothing in the session left. Gone back to, its first landing shows
that population again, only when the Stage's still is the picture the cells were found on (`sourceImage`): on the live
camera or another source it starts fresh (ADR 0010). The app, library, journals, captures, runner and evidence are
real; the bridge and the model process are stand-ins.
"""
import asyncio
import time

from orchestrator import population
from orchestrator.commands import handle
from orchestrator.fiji_bindings import fixture
from orchestrator.library import Library
from tests.standin_workers import build_app
from tests.test_original_picture_analysis import prepare
from tests.test_sources import Client
from tests.test_stage_still import bridge_frame, on_stage
from tests.waiting import until_async


async def _shown_and_saved(built, aid, image):
    await on_stage(built, aid, image)
    found = await population.segment(built.hub, choice="cellpose-sam")
    layer = population.layer_of(built.hub)
    layer.show(found)
    await layer.check()
    assert len(layer.wire()["verified"]) == 3
    await until_async(lambda: found.status == "saved")
    return found, layer


async def _landed(built, open_: str) -> None:
    """The studio's landing of the session opened: one `source` command, then the showing it may start."""
    client = Client()
    await handle(built, client, {"type": "source", "requestId": "land", "open": open_, "session": built.day.sid})
    assert client.replies[-1]["ok"], client.replies[-1]
    showing = getattr(built, "_reopening", None)
    if showing is not None:
        await showing


def test_a_project_gone_back_to_draws_its_cells_again_on_their_picture(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        first = built.day.sid
        found, layer = await _shown_and_saved(built, aid, image)
        built.new_session("Kidney section")  # another Project: its view starts fresh
        assert layer.shown is None
        built.switch_session(first)
        assert layer.shown is None  # nothing is drawn before the Stage shows their picture
        await _landed(built, aid)
        assert layer.shown == found.id
        await built.hub.source._accept(bridge_frame(image))
        await layer.check()
        wire = layer.wire()
        assert sorted(wire["verified"]) == sorted(m.id for m in found.live()) and wire["fit"] is not None
        assert layer.drawn_frame_reason() is None and layer.verified_count() == (3, 3)
        # Journaled in the session that shows it; hiding it because another session opened is not that session's change.
        assert built.day.layer_shown == found.id
        built.new_session()
        assert built.day.layer_shown is None and built.day.elsewhere(first).layer_shown == found.id

    asyncio.run(go())


def test_on_the_live_camera_or_another_picture_a_project_gone_back_to_starts_fresh(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    (tmp_path / "other").mkdir()
    other = built.hub.library.register("imported:other", fixture(tmp_path / "other"), title="Another picture",
                                       at=time.time(), origin=None)

    async def go():
        first = built.day.sid
        found, layer = await _shown_and_saved(built, aid, image)
        for landing in ("live", other):
            built.new_session()
            built.switch_session(first)
            await _landed(built, landing)
            assert layer.shown is None, landing
            # Shown once, by the first landing only: the picture put on stage after that is the user's own choice.
            await _landed(built, aid)
            assert layer.shown is None, landing
        # Hidden by the user, it stays hidden when the Project is opened again.
        layer.show(found)
        await handle(built, Client(), {"type": "clear", "requestId": "c", "kind": "cell"})
        assert layer.shown is None and built.day.layer_shown is None
        built.new_session()
        built.switch_session(first)
        await _landed(built, aid)
        assert layer.shown is None

    asyncio.run(go())


def test_a_restart_draws_the_cells_again_once_the_studio_lands_their_picture(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        found, layer = await _shown_and_saved(built, aid, image)
        return found

    found = asyncio.run(go())
    again = build_app(tmp_path / "home")  # the app started again over the same home and library
    again.hub.library = Library(tmp_path / "library.jsonl")
    assert again.hub.layer.shown is None  # nothing drawn before the landing

    async def back():
        await _landed(again, aid)
        assert again.hub.layer.shown == found.id

    asyncio.run(back())
