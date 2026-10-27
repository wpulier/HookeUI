"""A studio in the background is waiting to be seen, never a failure (live 2026-10-11).

The user's own app switch (another app or tab in front of the studio, a hidden pane) stopped the studio painting: a
reference photo was in the studio's window, and the answer said "I could not pull up the reference photo ... because
the workspace window was hidden". The browser paints what it was given once the studio is in view again, so a
window change sent while it is in the background is still being done: its confirmation deadline counts only the
time the studio is in view, it is confirmed when the studio paints it, and after BACKGROUND_WAIT_S it is said to be
sent and not yet confirmed, never that it could not be done.

Real App.build registry, runner, Screen and completion core; only the renderer is the synthetic studio of
tests/test_workspace_actions_app.py."""
import asyncio
import copy

from orchestrator.brain.toolbox import present as present_module
from tests.test_workspace_actions_app import address, built, plan, saved_picture


def background(studio):
    """The studio goes to the background: it applies what it is sent, paints nothing, and says it is not visible."""
    studio.visible, studio.draw = False, False


async def shown_again(studio):
    """The studio is in view again: the browser paints what it was given (workspace-windows.ts paint on the next
    animation frame), and the studio reports it."""
    studio.visible, studio.draw = True, True
    for iid, item in studio.items.items():
        if item.get("open") and item.get("phase") == "loaded":
            paint = f"synthetic-paint:{studio.seq + 1}:{iid}"
            item.update(phase="drawn", drawRef=paint, viewDrawRef="view-" + paint, visible=True)
            item["load"].update(state="drawn", drawToken=paint)
            for result in studio.command_results:
                if result["presentationId"] == iid:
                    result.update(phase="drawn", drawRef=paint, viewDrawRef="view-" + paint, reportSeq=studio.seq + 1)
    await studio.send_report()


def test_a_window_opened_while_the_studio_is_in_the_background_is_confirmed_once_it_is_seen(tmp_path, monkeypatch):
    import orchestrator.screen as screen_module

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.3)  # the draw deadline, so the wait outlasts it many times
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    background(studio)

    async def go():
        async def return_later():
            await asyncio.sleep(1.5)  # five draw deadlines in the background
            await shown_again(studio)

        returning = asyncio.ensure_future(return_later())
        runs, _ = await plan(app, [("present", {"do": "open", "source": address(entry)})])
        await returning
        run = runs[0]
        assert run.ok is True, run.summary
        assert "could not" not in run.summary.lower()
        outcome = run.wire()["outcome"]
        assert outcome["status"] == "confirmed"
        assert run.progress is None  # nothing left to wait for

    asyncio.run(go())


def test_a_studio_left_in_the_background_says_the_window_was_sent_not_that_it_failed(tmp_path, monkeypatch):
    import orchestrator.screen as screen_module

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.3)
    monkeypatch.setattr(present_module, "BACKGROUND_WAIT_S", 1.0)
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    background(studio)

    async def go():
        runs, _ = await plan(app, [("present", {"do": "open", "source": address(entry)})])
        run = runs[0]
        assert run.ok is True, run.summary  # sent and applied: not a failure, no "!"
        assert "stayed in the background" in run.summary and "not confirmed" in run.summary
        assert "could not" not in run.summary.lower()
        assert run.progress is None

    asyncio.run(go())


def test_while_in_the_background_the_step_says_what_it_waits_for(tmp_path, monkeypatch):
    import orchestrator.screen as screen_module

    monkeypatch.setattr(screen_module, "CONFIRM_S", 0.3)
    app, studio = built(tmp_path)
    entry, _ = saved_picture(app)
    background(studio)

    async def go():
        seen = []

        async def watch_then_return():
            await asyncio.sleep(0.8)  # past two draw deadlines, still in the background
            seen.append(copy.copy(watched[0].progress) if watched else None)
            await shown_again(studio)

        watched = []
        original_start = app.brain.registry.start

        def start(*args, **kwargs):
            run = original_start(*args, **kwargs)
            watched.append(run)
            return run

        monkeypatch.setattr(app.brain.registry, "start", start)
        returning = asyncio.ensure_future(watch_then_return())
        runs, _ = await plan(app, [("present", {"do": "open", "source": address(entry)})])
        await returning
        assert seen and seen[0] and "studio" in seen[0] and "in view" in seen[0]
        assert runs[0].ok is True

    asyncio.run(go())


def background_report(rig, *, seq=10):
    """An accepted studio's structural report from the background: the page is hidden, its surfaces laid out."""
    from tests.test_legacy_workspace_projection import structural_report

    raw = structural_report(rig, seq=seq)
    raw["viewport"]["visible"] = False
    raw["visible"] = False
    return raw


def test_a_background_studios_last_report_stands_for_the_board_and_the_request_pin(tmp_path, monkeypatch):
    """It reports only when it changes or is shown again: its last report is what it shows, never stale, and the
    board says the studio is in the background instead of calling its surfaces hidden."""
    from tests.test_legacy_workspace_projection import structural_rig

    rig, _ = structural_rig(tmp_path)
    rig.screen.report(background_report(rig), rig.client)
    later = rig.screen._observation_clock() + 120.0  # two minutes in the background, no report since
    monkeypatch.setattr(rig.screen, "_observation_clock", lambda: later)
    monkeypatch.setattr(rig.screen, "clock", lambda: later)
    pin = rig.pin(request_id="asked-from-the-background")
    assert pin.availability != "stale"
    assert rig.screen._current_observation(rig.screen.observation(rig.client))
    lines = rig.screen.lines()
    assert lines and "in the background" in lines[0]
    assert not any("outlines are hidden" in line for line in lines)


def test_close_that_in_a_background_studio_closes_the_window_in_front_of_its_layout():
    from orchestrator.screen import frontmost

    items = [{"id": "live", "kind": "live", "z": 0, "visible": False, "visibility": "tab-hidden"},
             {"id": "w-photo", "kind": "image", "open": True, "visible": False, "visibility": "tab-hidden", "z": 30},
             {"id": "w-tucked", "kind": "image", "open": True, "visible": False, "visibility": "css-hidden", "z": 40},
             {"id": "magnifier", "kind": "magnifier", "open": True, "visible": False, "visibility": "tab-hidden", "z": 5}]
    assert frontmost(items)["id"] == "w-photo"  # the tucked one is not shown in the layout either


def test_close_that_closes_a_photo_window_in_front_not_the_window_behind_it():
    """A native window of any kind takes close: TAKES listed only the legacy kinds, so a reference photo (kind
    "image") in front was passed over and the magnifier behind it was closed."""
    from orchestrator.screen import frontmost

    items = [{"id": "magnifier", "kind": "magnifier", "open": True, "visible": True, "z": 5},
             {"id": "w-photo", "kind": "image", "open": True, "visible": True, "z": 30}]
    assert frontmost(items)["id"] == "w-photo"


def test_a_closed_window_is_listed_apart_never_among_what_is_on_screen(tmp_path):
    """Live 2026-10-11: after the researcher's diagram was closed, "what do you see" said it was on screen: the closed
    window was among the screen's items, its title and all. Closed windows are listed apart, id and title only."""
    import json

    from orchestrator.brain.context import _screen
    from tests.test_screen_workspace import Rig

    rig = Rig(tmp_path)
    rig.accept()
    raw = rig.message()
    closed = next(i for i in raw["items"] if i["id"] == "window-b")
    closed.update(open=False, visible=False)
    assert asyncio.run(rig.ingest(raw))["ok"] is True
    text = _screen(None, False, request_snapshot=rig.pin())
    facts = json.loads(text.split("Originating workspace structural facts (no attached pixels): ", 1)[1].split("\n", 1)[0])
    assert [i["id"] for i in facts["items"]] == ["window-a"]
    assert [c["id"] for c in facts["closedNotOnScreen"]] == ["window-b"]
    assert "closed, not on screen: ['window-b']" in text


def test_the_dissected_view_is_its_own_window_from_its_own_kept_picture(tmp_path):
    """Live 2026-10-11: "keep open the dissected view but close live magnification" closed both: the dissected view
    lived in the magnifier, so closing the magnifier closed it, and its "Parts" entry had no picture to open again.
    In a native workspace it is its own window, from its own kept picture, independent of any live magnification."""
    from orchestrator.scene import SceneObject
    from tests.test_workspace_actions_app import jpeg, succeeded

    app, studio = built(tmp_path)
    obj = SceneObject("o47", "highlight", [[[10, 10], [30, 10], [30, 30], [10, 30]]], 7, 0, (0, 0), "user",
                      label="stoma")
    app.hub.scene.add(obj)
    picture = jpeg(90)

    async def dissect(target, method=None):
        assert target.id == obj.id
        return {"parts": ["stomatal pore", "guard cell"], "summary": "", "ms": 5, "method": "grid", "picture": picture}

    app.services.dissector.dissect = dissect

    async def go():
        runs, ctx = await plan(app, [("dissect", {"object": obj.id})])
        succeeded(runs[0])
        assert "stomatal pore, guard cell" in runs[0].summary
        outputs = [o.wire() for o in ctx.steps.outputs[1]]
        assert [o["kind"] for o in outputs] == ["source", "presentation"]  # its kept picture, then its own window
        state = studio.store().get("synthetic-workspace", outputs[1]["instanceId"])
        assert state.source.kind == "saved-image"  # its own kept picture, never the object's live source
        assert "dissected parts" in (state.title or "")
        kept = app.hub.resources.get(outputs[0]["source"]["id"], outputs[0]["source"]["day"])
        assert kept is not None and app.hub.resources.picture(kept.id, 0, kept.day)[0] == picture  # it opens again
        assert not app.services.magnifier.open  # the magnifier was never its home

    asyncio.run(go())


def test_a_dissected_view_whose_window_is_not_confirmed_keeps_its_parts(tmp_path, monkeypatch):
    """The parts are found, outlined and kept whether or not their window could be confirmed: the step says where
    they show, never that the dissection failed (review of 2026-10-11: a window error became the step's failure while
    the parts were on the board)."""
    from orchestrator.brain.registry import ToolFailed
    from orchestrator.brain.toolbox import present
    from orchestrator.scene import SceneObject
    from tests.test_workspace_actions_app import jpeg, succeeded

    app, studio = built(tmp_path)
    obj = SceneObject("o48", "highlight", [[[10, 10], [30, 10], [30, 30], [10, 30]]], 7, 0, (0, 0), "user",
                      label="stoma")
    app.hub.scene.add(obj)

    async def dissect(target, method=None):
        return {"parts": ["stomatal pore", "guard cell"], "summary": "", "ms": 5, "method": "grid", "picture": jpeg(90)}

    async def unconfirmed(*args, **kwargs):
        raise ToolFailed("the window was not confirmed on screen", next="show it again")

    app.services.dissector.dissect = dissect
    monkeypatch.setattr(present, "_native_present", unconfirmed)

    async def go():
        runs, ctx = await plan(app, [("dissect", {"object": obj.id})])
        succeeded(runs[0])
        assert "stomatal pore, guard cell" in runs[0].summary
        assert "the window was not confirmed on screen" in runs[0].summary

    asyncio.run(go())


def test_closing_the_magnifier_says_what_it_showed():
    from types import SimpleNamespace

    from orchestrator.brain.toolbox.view import shown_words

    stoma = SimpleNamespace(id="o1", label="stoma", parent=None)
    pore = SimpleNamespace(id="p2", label="stomatal pore", parent="o1")
    scene = SimpleNamespace(objects={"o1": stoma, "p2": pore})
    magnifier = SimpleNamespace(sources=[{"kind": "live"}], index=0, object_id="o1")
    assert shown_words(magnifier, scene) == "a live close-up of the stoma, with its dissected parts (stomatal pore)"


def test_a_part_is_drawn_as_a_part_never_as_a_second_outline_of_its_object():
    """Live 2026-10-11 (the doubled stoma): a stoma's dissected parts were drawn exactly like the stoma's own outline."""
    import numpy as np
    from types import SimpleNamespace

    from orchestrator.snapshot import OUTLINE_BGR, PAPER, draw

    info = SimpleNamespace(id=7, epoch=0, dx=0.0, dy=0.0, fov=None)

    def thing(kind, square, label):
        x0, y0, x1, y1 = square
        return SimpleNamespace(id=label, kind=kind, label=label, status="likely", arm=None, track="tracking",
                               anchor_epoch=0, anchor_offset=(0.0, 0.0),
                               polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]])

    image = np.zeros((200, 200, 3), np.uint8)
    drawn = draw(image, info, [thing("highlight", (20, 20, 180, 180), "stoma"), thing("part", (80, 80, 120, 120), "pore")],
                 labels=False)
    assert tuple(drawn.image[20, 100]) == OUTLINE_BGR["pink"]  # the object's own outline
    part = drawn.image[79:82, 100].max(axis=0)  # its part's thin line (antialiased), pale, never the object's pink
    assert all(int(c) > 180 for c in part) and abs(int(part[0]) - int(PAPER[0])) < 40


def test_a_removed_outline_can_be_brought_back_as_it_was_for_a_while():
    """A click on a selected outline removes it (the user's notes of 2026-10-11), so it can be taken back: the same
    outline, its name, how it was made and its parts, while it is held (Scene.restore, UNDO_S)."""
    from orchestrator import scene as scene_module
    from orchestrator.scene import Scene, SceneObject

    scene = Scene()
    stoma = scene.add(SceneObject("o1", "highlight", [[[10, 10], [30, 10], [30, 30], [10, 30]]], 7, 0, (0, 0), "user",
                                  label="stoma"))
    stoma.made_by = {"implementation": "edgetam"}
    scene.add(SceneObject("p2", "part", [[[12, 12], [20, 12], [20, 20], [12, 20]]], 7, 0, (0, 0), "dissect:grid",
                          label="stomatal pore", parent="o1"))
    assert scene.remove("o1") and not scene.objects
    back = scene.restore("o1")
    assert back is stoma and back.label == "stoma" and back.made_by == {"implementation": "edgetam"}
    assert set(scene.objects) == {"o1", "p2"}
    assert scene.restore("o1") is None  # once
    scene.remove("o1")
    scene._recently_removed["o1"] = (scene._recently_removed["o1"][0] - scene_module.UNDO_S - 1,
                                     scene._recently_removed["o1"][1])
    assert scene.restore("o1") is None and not scene.objects  # no longer held
