"""S5a: a population found on the still the Stage shows is drawn on the specimen, in the right place.

The bridge plays the still letterboxed into its frames (orchestrator/still_fit.py); the population is in the original's
pixels. The layer maps its members through the still's fit (check, drawable, member_hit, the layer wire), only on frames
that show that still and match it; otherwise it is kept on the original image and nothing is drawn, as before. A
This Tab run drawn on the specimen places no window of its own once its outlines are checked there (Will's call,
2026-10-25); otherwise its masks window is placed as before. The app, library, journals,
captures, runner and evidence are real; the bridge and the model process are stand-ins.
"""
import asyncio
import json

import cv2
import numpy as np
import pytest

from orchestrator import population, still_fit, workspace_views
from orchestrator.commands import handle
from orchestrator.day import DayStore
from orchestrator.population import KEPT_ON_ORIGINAL
from tests.test_original_picture_analysis import prepare
from tests.test_sources import Client
from tests.waiting import until_async

FRAME = (320, 180)  # the stand-in bridge's frame size: the 96 x 96 fixture fits at 180 x 180, 70 px in from the left


def bridge_frame(image, size=FRAME, shift: int = 0) -> bytes:
    """A frame of the still as the bridge plays it (fitted and letterboxed: still_fit), JPEG; `shift` moves the picture."""
    pixels = cv2.imread(str(image), cv2.IMREAD_GRAYSCALE)
    fit = still_fit.fit(still_fit.read_layout(image), *size)
    x, y, w, h = fit.rect
    frame = np.zeros((size[1], size[0]), np.uint8)
    frame[y:y + h, x + shift:x + w] = cv2.resize(pixels, (w, h), interpolation=cv2.INTER_LINEAR)[:, :w - shift]
    return cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 92])[1].tobytes()


async def on_stage(built, aid, image, size=FRAME):
    await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
    await built.hub.source._accept(bridge_frame(image, size))
    return built.hub.latest_info


def polygons_contain(polygons, point) -> bool:
    return any(cv2.pointPolygonTest(np.asarray(p, np.float32).reshape(-1, 1, 2), point, False) >= 0 for p in polygons)


def test_a_population_found_on_the_stages_still_is_drawn_on_it_through_the_fit(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        info = await on_stage(built, aid, image)
        found = await population.segment(built.hub, choice="cellpose-sam")
        assert population.drawn_on_stage(built.hub, found)
        layer = population.layer_of(built.hub)
        layer.show(found)
        await layer.check()
        wire = layer.wire()
        assert sorted(wire["verified"]) == sorted(m.id for m in found.live()) and wire["why"] == ""
        assert wire["fit"] == {"frame": [320, 180], "rect": [70, 0, 180, 180],
                               "matrix": [1.875, 0.0, 70.4375, 0.0, 1.875, 0.4375], "orientation": 1}
        assert wire["view"] == {"epoch": info.epoch, "offset": [round(info.dx, 2), round(info.dy, 2)]}
        assert wire["correction"] == [0.0, 0.0]
        proof = wire["memberChecks"]
        assert len(proof["samples"]) == 1 and proof["samples"][0]["checkedBasis"]["fit"] == wire["fit"]  # one check, its fit
        assert {row["checkRef"] for row in proof["members"]} == {proof["samples"][0]["ref"]}
        assert layer.drawn_frame_reason() is None and layer.verified_count() == (3, 3)
        fit = layer.fit
        # Drawn where the bridge put each member: every polygon point through the fit (as the studio reports drawing
        # the layer: no studio here, so its own style stands for the report).
        monkeypatch.setattr(layer, "drawn_style", lambda: layer.style)
        drawn = layer.drawable(info)
        assert len(drawn) == 3
        for (_, polygons), member in zip(drawn, found.live()):
            expected = [[list(fit.to_frame(x, y)) for x, y in poly] for poly in member.polygons]
            assert polygons == expected
        # A click inside a member, in frame px, selects it; one in the letterbox or between them selects none.
        big = max(found.live(), key=lambda m: found.value(m.id, "areaPx"))
        cx, cy = fit.to_frame(25, 25)  # the fixture's large circle
        assert layer.member_hit(info, cx, cy) is big
        assert polygons_contain(big.polygons, fit.to_source(cx, cy))
        assert layer.member_hit(info, 20.0, 90.0) is None  # the letterbox
        assert layer.member_hit(info, *fit.to_frame(48, 48)) is None  # the light field between them
        # The brain is told where it is drawn.
        assert "drawn on the picture on the Stage" in population.evidence_lines(built.hub)[0]

    asyncio.run(go())


@pytest.mark.parametrize("size", [(320, 180), (1280, 720), (640, 480), (200, 360)])
def test_its_place_follows_the_bridges_frame_size_and_letterbox(tmp_path, monkeypatch, size):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        info = await on_stage(built, aid, image, size)
        found = await population.segment(built.hub, choice="cellpose-sam")
        layer = population.layer_of(built.hub)
        layer.show(found)
        await layer.check()
        fit = still_fit.fit(still_fit.read_layout(image), *size)
        assert layer.wire()["fit"] == fit.wire() and len(layer.wire()["verified"]) == 3
        big = max(found.live(), key=lambda m: found.value(m.id, "areaPx"))
        assert layer.member_hit(info, *fit.to_frame(25, 25)) is big
        x, y, w, h = fit.rect
        # the frame's corner is letterbox in every one of these sizes (pillarboxed or letterboxed)
        corner = (x / 2, y + h / 2) if x else (x + w / 2, y / 2)
        assert layer.member_hit(info, *corner) is None

    asyncio.run(go())


def test_on_another_input_it_is_kept_on_the_original_and_nothing_is_drawn(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        info = await on_stage(built, aid, image)
        found = await population.segment(built.hub, choice="cellpose-sam")
        layer = population.layer_of(built.hub)
        layer.show(found)
        await layer.check()
        assert layer.wire()["fit"] is not None
        # The live camera on the Stage: the layer is hidden as the source changes, and shown again it is kept.
        await handle(built, Client(), {"type": "source", "requestId": "live", "open": "live"})
        assert layer.shown is None
        await built.hub.source._accept(bridge_frame(image))  # a frame that is not the still's (the camera's), untagged
        assert not population.drawn_on_stage(built.hub, found)
        layer.show(found)
        await layer.check()
        wire = layer.wire()
        assert wire["verified"] == [] and wire["fit"] is None and wire["view"] is None
        assert wire["why"] == KEPT_ON_ORIGINAL
        live = built.hub.latest_info
        assert layer.drawable(live) == [] and layer.member_hit(live, *still_fit.fit(still_fit.read_layout(image), *FRAME).to_frame(25, 25)) is None
        assert "original image" in layer.drawn_frame_reason()
        assert "not drawn on the Stage now" in population.evidence_lines(built.hub)[0]
        # The still again: drawn again.
        await on_stage(built, aid, image)
        layer.show(found)
        await layer.check()
        assert len(layer.wire()["verified"]) == 3

    asyncio.run(go())


@pytest.mark.parametrize("change", ["another picture", "shifted", "another size", "another epoch"])
def test_a_frame_that_is_not_the_still_where_the_fit_says_draws_nothing(tmp_path, monkeypatch, change):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        info = await on_stage(built, aid, image)
        found = await population.segment(built.hub, choice="cellpose-sam")
        layer = population.layer_of(built.hub)
        layer.show(found)
        await layer.check()
        assert len(layer.wire()["verified"]) == 3
        if change == "another epoch":
            # The renderer's own gate, before the next check: a frame of another motion epoch (a cut) draws nothing.
            moved = type(info)(**{**info.__dict__, "epoch": (info.epoch or 0) + 7})
            assert "changed" in layer.drawn_frame_reason(moved) and layer.drawable(moved) == []
            assert layer.member_hit(moved, *layer.fit.to_frame(25, 25)) is None
            return
        if change == "another size":
            moved = type(info)(**{**info.__dict__, "width": 640, "height": 360})
            assert "size" in layer.drawn_frame_reason(moved) and layer.drawable(moved) == []
            return
        # Tagged as the still at intake (the source switching), but its picture is not the still where the fit puts it.
        frame = (cv2.imencode(".jpg", np.full((FRAME[1], FRAME[0]), 200, np.uint8))[1].tobytes() if change == "another picture"
                 else bridge_frame(image, shift=12))
        await built.hub.source._accept(frame)
        await layer.check()
        wire = layer.wire()
        assert wire["verified"] == [] and wire["fit"] is None
        # A cut to another picture first goes unregistered by the motion tracker; registered, it does not match.
        assert wire["why"] in ("the picture on the Stage does not match the original image they were found on",
                               "the picture on the Stage is changing")
        if change == "shifted":
            assert wire["why"] == "the picture on the Stage does not match the original image they were found on"
        monkeypatch.setattr(layer, "drawn_style", lambda: layer.style)
        assert layer.drawable(built.hub.latest_info) == []
        assert layer.member_hit(built.hub.latest_info, *still_fit.fit(still_fit.read_layout(image), *FRAME).to_frame(25, 25)) is None

    asyncio.run(go())


def test_the_still_is_checked_once_per_distinct_frame(tmp_path, monkeypatch):
    """The bridge plays one picture: frames with the same bytes are matched once (no decode per check)."""
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    decodes = []

    async def go():
        await on_stage(built, aid, image)
        found = await population.segment(built.hub, choice="cellpose-sam")
        layer = population.layer_of(built.hub)
        real = still_fit.score
        monkeypatch.setattr(still_fit, "score", lambda ref, gray: decodes.append(1) or real(ref, gray))
        layer.show(found)
        for _ in range(3):
            await built.hub.source._accept(bridge_frame(image))
            await layer.check()
        assert len(layer.wire()["verified"]) == 3

    asyncio.run(go())
    assert len(decodes) == 1


def _journal(day) -> list[dict]:
    return [json.loads(line) for line in day.journal.path.read_text().splitlines() if line.strip()]


def _specimen_states(day, run) -> list[str]:
    return [e["state"] for e in _journal(day) if e.get("type") == "workspace-specimen" and e.get("run") == run]


async def _workspace_run(built, aid, run, *, destination=None, sent=None):
    """A This Tab (or New Tab) run of Cellpose-SAM on the still the Stage shows, as the studio sends it; its reply and
    the population it showed, once saved."""
    day = built.day
    workspace_views.save(day, workspace_views.read(day, schema=2)["layout"], 0)
    origin = {"session": day.sid, "tab": "main", "placementId": f"pending-{run}", "run": run, "input": {"source": aid},
              **({"destination": destination} if destination else {})}
    layer = population.layer_of(built.hub)
    if sent is not None:
        broadcast = layer.broadcast
        layer.broadcast = lambda message: sent.append(message) or broadcast(message)
    client = Client()
    await handle(built, client, {"type": "action", "capability": "cells", "args": {}, "use": {"cells": "cellpose-sam"},
                                 "run": run, "workspaceOrigin": origin})
    reply = client.replies[-1]
    assert reply["ok"], reply
    shown = population.evidence_of(built.hub).get(layer.shown)
    await until_async(lambda: shown.status == "saved")
    return reply, shown, layer


def test_a_this_tab_run_drawn_on_the_specimen_places_no_window_and_is_kept_in_data(tmp_path, monkeypatch):
    """Will's call (2026-10-25): a run whose population is drawn on the specimen places no masks window; it stays in
    Data, listed under completed work with why. Its window waits on the check from before its save is filed (so
    reconcile never places it first), and the first check that verifies it on the Stage's still decides "drawn"."""
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    day, sent = built.day, []
    monkeypatch.setattr(population.Layer, "start", lambda self: None)  # its checks run here, one at a time

    async def go():
        await on_stage(built, aid, image)
        reply, shown, layer = await _workspace_run(built, aid, "still-run", sent=sent)
        assert reply["placed"] == "specimen"  # its result is on the specimen once checked there, not on a Tab
        assert _specimen_states(day, "still-run") == ["checking"]
        assert workspace_views.read(day, schema=2)["pending"][0]["run"] == "still-run"  # waiting, truthfully
        await layer.check()
        assert len(layer.wire()["verified"]) == 3
        return shown

    shown = asyncio.run(go())
    kinds = [(e.get("type"), e.get("state"), e.get("id") or e.get("population")) for e in _journal(day)]
    assert kinds.index(("workspace-specimen", "checking", shown.id)) < kinds.index(("evidence-saved", None, shown.id))
    assert _specimen_states(day, "still-run") == ["checking", "drawn"]
    assert {"type": "action-placed", "run": "still-run", "placed": "specimen"} in sent
    reopened = DayStore(root=day.root, day=day.sid)
    workspace_views.reconcile(reopened)
    state = workspace_views.read(reopened, schema=2)
    assert state["layout"]["views"][0]["items"] == []
    assert [(c["origin"]["run"], c["item"]["resource"]["id"], c["item"].get("presentation"), c["state"], c["reason"])
            for c in state["completions"]] == [("still-run", shown.id, "masks", "available", workspace_views.SPECIMEN_REASON)]
    assert state["pending"] == []  # settled: no "Dismiss pending presentation" for it


@pytest.mark.parametrize("why", ["never verified", "hidden first", "replaced first"])
def test_a_run_whose_outlines_are_not_checked_on_the_specimen_places_its_masks(tmp_path, monkeypatch, why):
    """The window waits on the check, never on the asset's name alone: when the Stage's picture never matches (here its
    score), or the layer is hidden (the Stage changed) or replaced (Run again) before its first check verifies it, its
    masks window is placed on its Tab, as before, and every studio is told so (`action-placed` tab)."""
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    day, sent = built.day, []
    monkeypatch.setattr(population.Layer, "start", lambda self: None)
    if why == "never verified":
        monkeypatch.setattr(still_fit, "score", lambda ref, gray: 0.0)
        monkeypatch.setattr(population, "SPECIMEN_WAIT_S", 0.0)

    async def go():
        await on_stage(built, aid, image)
        reply, shown, layer = await _workspace_run(built, aid, "x-run", sent=sent)
        assert reply["placed"] == "specimen" and _specimen_states(day, "x-run") == ["checking"]
        if why == "never verified":
            await layer.check()
            assert layer.wire()["verified"] == []
        elif why == "hidden first":
            await handle(built, Client(), {"type": "source", "requestId": "live", "open": "live"})
            assert layer.shown is None
        else:
            other = await population.segment(built.hub, choice="cellpose-sam")
            layer.show(other)
        return shown

    shown = asyncio.run(go())
    assert _specimen_states(day, "x-run") == ["checking", "placed"]
    assert {"type": "action-placed", "run": "x-run", "placed": "tab"} in sent
    items = workspace_views.read(day, schema=2)["layout"]["views"][0]["items"]  # placed at once, no read needed
    assert [(item["resource"]["id"], item.get("presentation")) for item in items] == [(shown.id, "masks")]
    assert workspace_views.read(day, schema=2)["pending"] == []


def test_a_new_tab_run_on_the_stages_still_keeps_its_masks_window(tmp_path, monkeypatch):
    """A New Tab run opened its Tab for its result: its masks are placed there, drawn on the specimen or not."""
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    day = built.day

    async def go():
        await on_stage(built, aid, image)
        reply, shown, layer = await _workspace_run(built, aid, "new-run", destination="new")
        assert "placed" not in reply
        return shown

    shown = asyncio.run(go())
    assert _specimen_states(day, "new-run") == []
    reopened = DayStore(root=day.root, day=day.sid)
    workspace_views.reconcile(reopened)
    items = workspace_views.read(reopened, schema=2)["layout"]["views"][0]["items"]
    assert [(item["resource"]["id"], item.get("presentation")) for item in items] == [(shown.id, "masks")]


def test_a_workspace_run_kept_on_the_original_still_places_its_masks(tmp_path, monkeypatch):
    """The Stage showing something else by the time it is shown: kept on the original image, placed as its masks (S5b),
    as before."""
    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    day = built.day
    monkeypatch.setattr(population, "on_stage", lambda spec, source: False)

    async def go():
        await on_stage(built, aid, image)
        reply, shown, layer = await _workspace_run(built, aid, "kept-run")
        assert "placed" not in reply
        return shown

    shown = asyncio.run(go())
    assert not any(e.get("type") == "workspace-specimen" for e in _journal(day))
    reopened = DayStore(root=day.root, day=day.sid)
    workspace_views.reconcile(reopened)
    items = workspace_views.read(reopened, schema=2)["layout"]["views"][0]["items"]
    assert [(item["resource"]["id"], item.get("presentation")) for item in items] == [(shown.id, "masks")]


def test_only_a_this_tab_run_with_outlines_shown_on_the_stages_still_waits_on_the_specimen(tmp_path, monkeypatch):
    """placed_on_stage's gate: shown, found on the Stage's still, with members to draw, and a This Tab run's."""
    from types import SimpleNamespace

    from orchestrator import ticket

    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await on_stage(built, aid, image)
        found = await population.segment(built.hub, choice="cellpose-sam")
        origin = {"session": built.day.sid, "tab": "main", "placementId": "p", "run": "r", "input": {"source": aid}}
        empty = SimpleNamespace(view=found.view, live=lambda: [], made_by=found.made_by, id=found.id)
        for case, pop, shown, destination in [("not shown", found, False, None), ("no members", empty, True, None),
                                              ("a New Tab run", found, True, "new")]:
            request = SimpleNamespace(workspace_origin={**origin, **({"destination": destination} if destination else {})},
                                      workspace_store=lambda: built.day, on_specimen=None)
            monkeypatch.setattr(ticket, "current", lambda request=request: request)
            assert population.placed_on_stage(built.hub, pop, shown) is False, case
            assert request.on_specimen is None, case
        monkeypatch.setattr(ticket, "current", lambda: None)
        assert population.placed_on_stage(built.hub, found, True) is False  # not a workspace run

    asyncio.run(go())
    assert not any(e.get("type") == "workspace-specimen" for e in _journal(built.day))


def test_reconcile_follows_each_runs_specimen_decision(tmp_path, monkeypatch):
    """The journal's facts alone (no app): a run still checking in this process places nothing yet; drawn, it is kept in
    Data with why and no window; placed, or left checking by an earlier start, its masks are placed; a run that never
    waited places them as before. A decision is made once, and only for a run that was checking."""
    from tests.test_workspace_recovery import migrated

    day = DayStore(root=tmp_path / "sessions")
    workspace_views.save(day, migrated(day), 0)
    runs, this_start = ("checking", "drawn", "placed", "stale", "other"), workspace_views._BOOT
    for run in runs:
        origin = {"session": day.sid, "tab": "main", "placementId": f"pending-{run}", "run": run}
        workspace_views.admit(day, origin)
        workspace_views.link_execution(day, origin, f"x-{run}")
    for run in runs[:4]:
        # "stale": left checking by an earlier start of the app (its layer is gone), never decided, so it is placed
        monkeypatch.setattr(workspace_views, "_BOOT", "an earlier start" if run == "stale" else this_start)
        assert workspace_views.specimen(day, day.workspace_origins[run], f"x-{run}", "e1", "checking") == "checking"
    monkeypatch.setattr(workspace_views, "_BOOT", this_start)
    assert workspace_views.specimen(day, day.workspace_origins["other"], "x-other", "e1", "drawn") is None  # never waited
    assert workspace_views.specimen(day, day.workspace_origins["drawn"], "x-drawn", "e1", "drawn") == "drawn"
    assert workspace_views.specimen(day, day.workspace_origins["drawn"], "x-drawn", "e1", "placed") == "drawn"  # once
    assert workspace_views.specimen(day, day.workspace_origins["placed"], "x-placed", "e1", "placed") == "placed"
    with pytest.raises(ValueError):
        workspace_views.specimen(day, day.workspace_origins["checking"], "x-checking", "e1", "shown")
    folder = tmp_path / "evidence-e1"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"executions": [f"x-{run}" for run in runs], "title": "Masks",
                                                      "version": 1}))
    day._record({"type": "evidence-saved", "id": "e1", "path": str(folder)})
    day._record({"type": "result", "id": "r1", "title": "Answer", "executions": [f"x-{run}" for run in runs],
                 "pictures": [], "state": "done"})
    reopened = DayStore(root=day.root, day=day.sid)
    workspace_views.reconcile(reopened)
    state = workspace_views.read(reopened, schema=2)
    rows = {c["origin"]["run"]: (c["state"], c.get("reason"), c["item"].get("presentation")) for c in state["completions"]}
    assert rows == {"drawn": ("available", workspace_views.SPECIMEN_REASON, "masks"), "placed": ("placed", None, "masks"),
                    "stale": ("placed", None, "masks"), "other": ("placed", None, "masks")}  # the Answer: no window
    assert [p["run"] for p in state["pending"]] == ["checking"]
    assert sum(1 for e in _journal(day) if e.get("type") == "workspace-specimen" and e.get("run") == "drawn") == 2
    # Decided later, the run still checking is placed at once.
    assert workspace_views.specimen(reopened, reopened.workspace_origins["checking"], "x-checking", "e1", "placed") == "placed"
    assert workspace_views.read(reopened, schema=2)["pending"] == []


def test_an_answer_filed_before_its_masks_is_not_placed_while_they_wait_on_the_specimen(tmp_path, monkeypatch):
    """The live order on the 0.4.0 candidate: a This Tab run's answer (its `result`) is filed after its masks' specimen
    mark and before their save. Reconciled at that moment, the answer was placed as the run's window although its
    outlines were then drawn on the still; one window per execution (S5b) counts masks waiting on the specimen too."""
    from tests.test_workspace_recovery import migrated

    day = DayStore(root=tmp_path / "sessions")
    workspace_views.save(day, migrated(day), 0)
    origin = {"session": day.sid, "tab": "main", "placementId": "pending-r", "run": "r"}
    workspace_views.admit(day, origin)
    workspace_views.link_execution(day, origin, "x-r")
    assert workspace_views.specimen(day, day.workspace_origins["r"], "x-r", "e1", "checking") == "checking"
    day._record({"type": "result", "id": "d1", "title": "Answer", "executions": ["x-r"], "pictures": [], "state": "done"})
    workspace_views.reconcile(day)
    state = workspace_views.read(day, schema=2)
    assert state["completions"] == [] and state["layout"]["views"][0]["items"] == []  # the answer: no window
    assert workspace_views.specimen(day, day.workspace_origins["r"], "x-r", "e1", "drawn") == "drawn"
    folder = tmp_path / "evidence-e1"
    folder.mkdir()
    (folder / "manifest.json").write_text(json.dumps({"executions": ["x-r"], "title": "Masks", "version": 1}))
    day._record({"type": "evidence-saved", "id": "e1", "path": str(folder)})
    workspace_views.reconcile(day)
    state = workspace_views.read(day, schema=2)
    assert [(c["item"]["resource"]["id"], c["state"], c.get("reason")) for c in state["completions"]] == [
        ("e1", "available", workspace_views.SPECIMEN_REASON)]
    assert state["layout"]["views"][0]["items"] == [] and state["pending"] == []


def test_shown_again_on_its_still_it_is_drawn_there_not_in_the_magnifier(tmp_path, monkeypatch):
    """`present` show (Data, the outline card's earlier layers): on the still the Stage shows, the population is drawn on
    it, so its original is not opened in the magnifier as a population kept on the original image is."""
    from types import SimpleNamespace
    from orchestrator.brain.toolbox.present import _on_their_view

    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await on_stage(built, aid, image)
        found = await population.segment(built.hub, choice="cellpose-sam")
        layer = population.layer_of(built.hub)
        layer.show(found)
        shown = []
        magnifier = SimpleNamespace(show_picture=lambda *args, **kwargs: shown.append(kwargs) or "/picture")
        assert await _on_their_view(SimpleNamespace(hub=built.hub, magnifier=magnifier), layer, found) == ("", None)
        assert shown == [] and len(layer.wire()["verified"]) == 3

    asyncio.run(go())
