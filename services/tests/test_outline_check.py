"""The app's outlines are checked after they are drawn, and outlined again when the model says
one does not fit (orchestrator/outline_check.py; option OUTLINE_CHECK)."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.outline_check import OutlineCheck, prompt, verdict
from orchestrator.scene import Scene, SceneObject
from orchestrator.options import Settings
from tests.waiting import HANG_S, until_async

JPEG = cv2.imencode(".jpg", np.full((720, 1280, 3), 140, np.uint8))[1].tobytes()
SQUARE = [[[600.0, 300.0], [700.0, 300.0], [700.0, 400.0], [600.0, 400.0]]]
BETTER = [[[620.0, 320.0], [680.0, 320.0], [680.0, 380.0], [620.0, 380.0]]]


def setup(mode, replies):
    replies = list(replies)
    asked = []

    async def ask(images, text, max_output_tokens=0):
        asked.append(text)
        return SimpleNamespace(data=replies.pop(0) if replies else {"fits": "yes"})

    settings = Settings()
    settings.outline_check, settings.fast = mode, "gemini-3.5-flash"
    view = SimpleNamespace(id=7, fov=None, epoch=0, dx=0.0, dy=0.0, width=1280, height=720)  # the frame's facts
    hub = SimpleNamespace(scene=Scene(), settings=settings, source=SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=JPEG)),
                          infos={7: view}, latest_info=view)
    agent = SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask))
    again = []

    async def outline_again(capture, box, label, made_by=None):
        again.append(box)
        return BETTER

    logged = []
    checker = OutlineCheck(hub, agent, outline_again, log=logged.append)
    return hub, checker, asked, again, logged


def add(hub, source="ai-look", label="stoma"):
    return hub.scene.add(SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=SQUARE, anchor_frame=7,
                                     anchor_epoch=0, anchor_offset=(0.0, 0.0), source=source, label=label))


async def settle(checker):
    await until_async(lambda: not checker.tasks)


def test_a_fitting_outline_is_kept_and_logged():
    async def go():
        hub, checker, asked, again, logged = setup("fix", [{"fits": "yes", "why": "follows the edge"}])
        obj = add(hub)
        await settle(checker)
        return hub, obj, asked, again, logged

    hub, obj, asked, again, logged = asyncio.run(go())
    assert len(asked) == 1 and '"stoma"' in asked[0] and not again
    assert logged[-1]["fits"] == "yes" and logged[-1]["done"] == "kept" and hub.scene.objects[obj.id].polygons == SQUARE


def test_with_fix_a_wrong_outline_is_outlined_again_and_kept_only_if_that_fits():
    async def go():
        hub, checker, asked, again, logged = setup("fix", [{"fits": "too_big", "box_2d": [400, 450, 550, 550]}, {"fits": "yes"}])
        obj = add(hub)
        await settle(checker)
        return hub, obj, asked, again, logged

    hub, obj, asked, again, logged = asyncio.run(go())
    assert len(again) == 1 and hub.scene.objects[obj.id].polygons == BETTER  # the same id, a better outline
    assert logged[-1]["done"] == "replaced" and hub.scene.objects[obj.id].template is not None
    assert hub.scene.objects[obj.id].naming["repaired"]["at"] > 0  # said with its name's account (known.py)
    x0, y0, x1, y1 = again[0]
    assert abs(x0 - 576) < 1 and abs(y0 - 288) < 1 and abs(x1 - 704) < 1 and abs(y1 - 396) < 1  # 0-1000 of the view, in px

    async def go2():
        hub, checker, asked, again, logged = setup("fix", [{"fits": "wrong", "box_2d": [400, 450, 550, 550]}, {"fits": "wrong"}])
        obj = add(hub)
        await settle(checker)
        return hub, obj, logged

    hub, obj, logged = asyncio.run(go2())
    assert hub.scene.objects[obj.id].polygons == SQUARE and "did not fit either" in logged[-1]["done"]


def test_an_outline_the_check_doubts_and_nothing_replaced_is_shown_as_doubted():
    """Live 2026-10-11: of 18 outlines the check called wrong, 9 stayed on screen as if they fit (the redraw did not fit
    either) and nothing said so. The verdict now stays on the outline while it stands: the studio is sent it (`fit`),
    draws the outline as uncertain and says why under its label; a fitting or redrawn outline is sent no `fit`."""
    async def go(mode, replies):
        hub, checker, asked, again, logged = setup(mode, replies)
        obj = add(hub)
        version = hub.scene.version
        await settle(checker)
        return hub, obj, version

    hub, obj, version = asyncio.run(go("fix", [{"fits": "too_small", "why": "covers only the cap", "box_2d": [400, 450, 550, 550]},
                                                {"fits": "too_big"}]))
    wire = hub.scene.objects[obj.id].to_wire()
    assert wire["fit"] == {"fits": "too_small", "why": "covers only the cap"} and wire["polygons"] == [[[600.0, 300.0], [700.0, 300.0], [700.0, 400.0], [600.0, 400.0]]]
    assert hub.scene.version > version  # sent to the studio, not only kept
    hub, obj, _ = asyncio.run(go("check", [{"fits": "wrong", "why": "a different stoma"}]))
    assert hub.scene.objects[obj.id].to_wire()["fit"]["fits"] == "wrong"  # check only: the verdict is shown all the same
    hub, obj, _ = asyncio.run(go("fix", [{"fits": "yes"}]))
    assert "fit" not in hub.scene.objects[obj.id].to_wire()
    hub, obj, _ = asyncio.run(go("fix", [{"fits": "too_big", "box_2d": [400, 450, 550, 550]}, {"fits": "yes"}]))
    assert "fit" not in hub.scene.objects[obj.id].to_wire()  # redrawn: the new outline fits


def test_check_only_logs_and_the_users_own_outlines_and_off_are_left_alone():
    async def go(mode, source):
        hub, checker, asked, again, logged = setup(mode, [{"fits": "wrong", "box_2d": [400, 450, 550, 550]}])
        obj = add(hub, source=source)
        await settle(checker)
        return hub, obj, asked, again, logged

    hub, obj, asked, again, logged = asyncio.run(go("check", "ai-look"))
    assert asked and not again and logged[-1]["fits"] == "wrong" and hub.scene.objects[obj.id].polygons == SQUARE
    _, _, asked, _, _ = asyncio.run(go("fix", "click"))
    assert not asked  # the user's own outline: never second-guessed
    _, _, asked, _, _ = asyncio.run(go("off", "ai-look"))
    assert not asked


def test_the_prompt_and_the_verdict():
    assert '"stoma"' in prompt("stoma") and "the user asked" in prompt("stoma", "what is that oval?")
    assert verdict({"fits": "Too Big", "box_2d": [1, 2, 3, 4]}) == ("too_big", "", [1, 2, 3, 4])
    assert verdict({"fits": "maybe"})[0] == "unclear" and verdict(None)[0] == "unclear"


# --- several things of one name (going live G4, 2026-10-07 16:11:45) -----------------------------------------
# Two stomata side by side, both outlined "stoma with guard cells" by one request. The check on o3 said "wrong: the
# magenta outline is on a different stoma" with a box on the other one, and the fix moved o3 onto the stoma o4 already
# outlined: two labels on one stoma, none on the other, and nothing said.
LEFT = [[[300.0, 300.0], [400.0, 300.0], [400.0, 400.0], [300.0, 400.0]]]
RIGHT = [[[800.0, 300.0], [900.0, 300.0], [900.0, 400.0], [800.0, 400.0]]]
RIGHT_BOX = [417, 625, 556, 703]  # RIGHT as [ymin, xmin, ymax, xmax] 0-1000 of the 1280 x 720 view
STOMA = "stoma with guard cells"


def outlined_side(images) -> str:
    """Where the magenta outline is in the first picture (what the model is shown): "left" or "right"."""
    view = cv2.imdecode(np.frombuffer(images[0], np.uint8), cv2.IMREAD_COLOR).astype(int)
    b, g, r = view[..., 0], view[..., 1], view[..., 2]
    xs = np.nonzero((r - g > 100) & (b - g > 40))[1]
    assert len(xs), "no magenta outline in the picture"
    return "left" if xs.mean() < view.shape[1] / 2 else "right"


def two_of_a_kind(replies: dict, told: list, during_second=None):
    """The checker over a hub; the model answers by where the outline it is shown is (`replies[side]`, a list
    used in turn); `during_second` runs at the second check (of one outline: the re-drawn one's)."""
    hub, checker, asked, again, logged = setup("fix", [])
    calls = []

    async def ask(images, text, max_output_tokens=0):
        side = outlined_side(images)
        calls.append((side, text))
        if len(calls) > 1 and during_second is not None:
            during_second(hub)
        reply = replies[side].pop(0) if replies[side] else {"fits": "yes"}
        return SimpleNamespace(data=reply)

    async def outline_again(capture, box, label, made_by=None):
        x0, y0, x1, y1 = box
        return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]

    checker.agent = SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask))
    checker.outline_again = outline_again
    checker.tell = lambda request, text: told.append((request, text))
    return hub, checker, calls, logged


def stoma(hub, polygons, source="ai:sight", label=STOMA, epoch=0, offset=(0.0, 0.0), origin_box=None):
    return hub.scene.add(SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=polygons, anchor_frame=7,
                                     anchor_epoch=epoch, anchor_offset=offset, source=source, label=label,
                                     origin_box=origin_box))


def test_a_fix_never_moves_an_outline_onto_a_thing_another_outline_already_covers():
    """The live case: o3 (left stoma) is called "wrong" with a box on the right stoma, which o4 outlines."""
    told = []

    async def go():
        hub, checker, calls, logged = two_of_a_kind(
            {"left": [{"fits": "wrong", "why": "on a different stoma", "box_2d": RIGHT_BOX}], "right": [{"fits": "yes"}]}, told)
        o3, o4 = stoma(hub, LEFT), stoma(hub, RIGHT)
        await settle(checker)
        return hub, o3, o4, calls, logged

    hub, o3, o4, calls, logged = asyncio.run(go())
    assert hub.scene.objects[o3.id].polygons == LEFT and hub.scene.objects[o4.id].polygons == RIGHT  # each on its own stoma
    record = next(r for r in logged if r.get("kind") == "outline-check" and r["objectId"] == o3.id)
    assert record["fits"] == "wrong" and record["done"] == f"kept: the fix would cover {o4.id}" and record["covers"] == o4.id
    assert any(r.get("kind") == "override" and r["rule"] == "check-fix-would-cover" and r["covers"] == o4.id for r in logged)
    assert sorted(side for side, _ in calls) == ["left", "right"]  # no second check: nothing was re-drawn
    assert not told  # nothing visible changed, so nothing is said


def test_the_check_is_told_which_of_several_of_one_name_it_is():
    async def go():
        hub, checker, calls, logged = two_of_a_kind({"left": [{"fits": "yes"}], "right": [{"fits": "yes"}]}, [])
        stoma(hub, LEFT), stoma(hub, RIGHT)
        await settle(checker)
        return calls, logged

    calls, logged = asyncio.run(go())
    texts = dict(calls)
    assert f'2 outlines in this view are named "{STOMA}"; this one is meant to be the one at [417, 234, 556, 312]' in texts["left"]
    assert f"this one is meant to be the one at {RIGHT_BOX} in the first picture, not another of them." in texts["right"]
    assert all(r.get("sameName") for r in logged if r.get("kind") == "outline-check")
    # One of a kind: the prompt is as it was, byte for byte.
    assert prompt(STOMA) == prompt(STOMA, several=None) and "outlines in this view are named" not in prompt(STOMA)


def test_the_check_waits_for_the_outlines_made_beside_it():
    """Live, o4 was made 0.4 s after o3, by the same highlight: o3's check had already begun and was told nothing of it."""
    async def go():
        hub, checker, calls, logged = two_of_a_kind({"left": [{"fits": "yes"}], "right": [{"fits": "yes"}]}, [])

        from orchestrator.brain.registry import Registry, Tool, ToolContext, schema

        async def highlight(ctx, args):  # one tool run outlining both, the second a moment after the first
            stoma(hub, LEFT)
            await asyncio.sleep(0.2)
            stoma(hub, RIGHT)
            return "outlined both"

        registry = Registry()
        registry.add(Tool("highlight", "", schema({}), highlight))
        await registry.start("highlight", {}, ToolContext("voice", 1, 7)).task
        await settle(checker)
        return calls

    texts = dict(asyncio.run(go()))
    assert '2 outlines in this view are named' in texts["left"] and '2 outlines in this view are named' in texts["right"]


def test_an_outline_made_while_the_fix_was_being_checked_is_not_covered_either():
    """The fix's place was free when it was outlined, and taken (a click) while its second check ran: checked again
    where nothing awaits before the write."""
    told = []

    def click_there(hub):
        if len(hub.scene.objects) == 1:
            stoma(hub, RIGHT, source="click")

    async def go():
        hub, checker, calls, logged = two_of_a_kind(
            {"left": [{"fits": "wrong", "why": "not a stoma", "box_2d": RIGHT_BOX}], "right": [{"fits": "yes"}]}, told,
            during_second=click_there)
        o3 = stoma(hub, LEFT)
        await settle(checker)
        return hub, o3, calls, logged

    hub, o3, calls, logged = asyncio.run(go())
    clicked = next(o for o in hub.scene.objects.values() if o.source == "click")
    assert [side for side, _ in calls] == ["left", "right"]  # the re-drawn outline was checked, and fitted
    checked = [r for r in logged if r.get("kind") == "outline-check"]
    assert hub.scene.objects[o3.id].polygons == LEFT and checked[-1]["done"] == f"kept: the fix would cover {clicked.id}"
    assert checked[-1]["again"] == "yes" and logged[-1]["kind"] == "override"
    assert not told


def test_a_fix_that_moves_an_outline_to_a_free_place_is_said_where_the_request_was_made():
    from orchestrator import ticket as tickets

    told = []

    async def go():
        hub, checker, calls, logged = two_of_a_kind(
            {"left": [{"fits": "wrong", "why": "not a stoma", "box_2d": RIGHT_BOX}], "right": [{"fits": "yes"}]}, told)
        request = tickets.issue(hub, "turn", channel="voice")
        with tickets.using(request):
            o3 = stoma(hub, LEFT)
        await settle(checker)
        return hub, o3, request, logged

    hub, o3, request, logged = asyncio.run(go())
    xs, ys = [x for x, _ in hub.scene.objects[o3.id].polygons[0]], [y for _, y in hub.scene.objects[o3.id].polygons[0]]
    assert abs(min(xs) - 800) < 1 and abs(max(xs) - 900) < 1 and abs(min(ys) - 300) < 1 and abs(max(ys) - 400) < 1  # moved
    assert logged[-1]["done"] == "replaced"
    assert told == [(request, f"Redrew the {STOMA} outline: the old one was on something else.")]


def test_what_counts_as_covering_another_outline():
    from orchestrator.outline_check import covered_by

    def square(x, y, side):
        return [[[x, y], [x + side, y], [x + side, y + side], [x, y + side]]]

    other = [("o4", "stoma", square(800, 300, 100))]
    assert covered_by(square(805, 305, 95), other) == "o4"  # the same stoma outlined again
    assert covered_by(square(845, 300, 100), other) == "o4"  # off-centre (IoU 0.38), its centre inside the other
    assert covered_by(square(300, 300, 100), other) is None  # the stoma beside it
    assert covered_by(square(840, 340, 20), other) is None  # a part inside it (a pore in its stoma) is not the same thing
    assert covered_by(square(700, 200, 300), other) is None  # ...nor a whole around it, nine times its size
    wide = [[[820, 300], [1070, 300], [1070, 400], [820, 400]]]  # 2.5 times o4: its centre outside o4, o4's inside it
    assert covered_by(wide, other) == "o4"
    assert covered_by([], other) is None
    # Two C shapes, one 5 px from the other: neither's centre is inside the other (it is in the notch), so only the
    # overlap (IoU about 0.8) says they are one thing.
    c = [[[800, 300], [900, 300], [900, 320], [820, 320], [820, 380], [900, 380], [900, 400], [800, 400]]]
    shifted = [[[x + 5, y] for x, y in poly] for poly in c]
    assert covered_by(shifted, [("o4", "stoma", c)]) == "o4"
    # The outline as drawn already covered it as much as the fix would: not counted; less than the fix would: counted.
    assert covered_by(square(805, 305, 95), other, drawn=square(800, 300, 100)) is None
    assert covered_by(square(800, 300, 100), other, drawn=square(845, 300, 100)) == "o4"


# --- the partner's review of G4 (at 3143f9b): fixes that end a doubling, namesakes in the picture, the meant box ---
GUARD = [[[800.0, 300.0], [850.0, 300.0], [850.0, 400.0], [800.0, 400.0]]]  # the right stoma's left guard cell
GUARD_BOX = [417, 625, 556, 664]
FAR = [[[2000.0, 300.0], [2100.0, 300.0], [2100.0, 400.0], [2000.0, 400.0]]]  # off the 1280 x 720 picture


def checked(build, replies):
    told = []

    async def go():
        hub, checker, calls, logged = two_of_a_kind(replies, told)
        made = build(hub)
        await settle(checker)
        return hub, made, calls, logged

    hub, made, calls, logged = asyncio.run(go())
    return hub, made, calls, [r for r in logged if r.get("kind") == "outline-check"], told


def test_a_fix_that_ends_a_doubling_is_kept():
    """Review probes a and b: an app outline drawn as a copy of the user's outline of another thing. Refusing its fix
    for covering the user's outline would keep the doubling G4 is about; the live case (o3 never covered o4) holds."""
    # a: a "guard cell" outline over the whole stoma the user outlined; "too big", boxed on one guard cell.
    hub, o5, calls, records, told = checked(lambda hub: (stoma(hub, RIGHT, source="click"), stoma(hub, RIGHT, label="guard cell"))[1],
                                            {"right": [{"fits": "too_big", "box_2d": GUARD_BOX}, {"fits": "yes"}], "left": []})
    assert records[-1]["done"] == "replaced" and hub.scene.objects[o5.id].polygons != RIGHT
    # b: a "stoma" outline drawn around only the guard cell the user outlined; "too small", boxed on the stoma.
    hub, o5, calls, records, told = checked(lambda hub: (stoma(hub, GUARD, source="click", label="guard cell"), stoma(hub, GUARD))[1],
                                            {"right": [{"fits": "too_small", "box_2d": RIGHT_BOX}, {"fits": "yes"}], "left": []})
    assert records[-1]["done"] == "replaced" and hub.scene.objects[o5.id].polygons != GUARD


def test_a_fix_that_would_double_another_outline_more_than_it_was_is_refused():
    """Drawn half over o4 (its centre inside o4: one thing by the centre rule), the fix lands exactly on o4: worse."""
    half = [[[845.0, 300.0], [945.0, 300.0], [945.0, 400.0], [845.0, 400.0]]]
    hub, o3, calls, records, told = checked(lambda hub: (stoma(hub, RIGHT, source="click"), stoma(hub, half))[1],
                                            {"right": [{"fits": "wrong", "box_2d": RIGHT_BOX}, {"fits": "yes"}], "left": []})
    assert hub.scene.objects[o3.id].polygons == half and records[-1]["done"].startswith("kept: the fix would cover")


def test_only_namesakes_in_the_picture_are_counted_and_the_box_is_where_it_was_meant_to_be():
    # A namesake off the picture (the slide moved since it was made) is not "in this view".
    hub, o3, calls, records, told = checked(lambda hub: (stoma(hub, LEFT), stoma(hub, FAR, source="click"))[0],
                                            {"left": [{"fits": "yes"}], "right": []})
    assert "outlines in this view are named" not in calls[0][1] and "sameName" not in records[-1]
    # The outline model drew on the left stoma what the look boxed on the right one: the line gives the look's box.
    hub, o3, calls, records, told = checked(
        lambda hub: (stoma(hub, LEFT, origin_box=(800.0, 300.0, 900.0, 400.0)), stoma(hub, RIGHT, source="click"))[0],
        {"left": [{"fits": "yes"}], "right": []})
    assert f"this one is meant to be the one at {RIGHT_BOX} in the first picture" in calls[0][1]


def test_a_fix_with_namesakes_is_checked_again_told_its_new_place_and_said_by_where_it_is():
    """The second check gets the line too, with the box the first check gave; the words say which one moved."""
    elsewhere = [[[50.0, 50.0], [150.0, 50.0], [150.0, 150.0], [50.0, 150.0]]]
    hub, o3, calls, records, told = checked(lambda hub: (stoma(hub, LEFT), stoma(hub, elsewhere, source="click"))[0],
                                            {"left": [{"fits": "wrong", "box_2d": RIGHT_BOX}], "right": [{"fits": "yes"}]})
    assert records[-1]["done"] == "replaced"
    again = [text for side, text in calls if side == "right"][0]
    assert f'2 outlines in this view are named "{STOMA}"; this one is meant to be the one at {RIGHT_BOX}' in again
    assert [t for _, t in told] == [f"Redrew one of the 2 {STOMA} outlines, the one in the middle on the checked view: the old one was on "
                                    "something else."]


def test_where_other_outlines_are_on_the_checked_view():
    """Review probes (survivors of the PR's tests): another outline is placed on the check's view by the slide's
    offsets, only in its epoch; only the same name is a namesake."""
    # Anchored 150 px of slide later: in its own pixels it is at RIGHT + 150, on the check's view at RIGHT.
    moved = [[[x + 150.0, y] for x, y in poly] for poly in RIGHT]
    hub, o3, calls, records, told = checked(lambda hub: (stoma(hub, LEFT), stoma(hub, moved, source="click", offset=(150.0, 0.0)))[0],
                                            {"left": [{"fits": "wrong", "box_2d": RIGHT_BOX}], "right": [{"fits": "yes"}]})
    assert hub.scene.objects[o3.id].polygons == LEFT and records[-1]["done"].startswith("kept: the fix would cover")
    # Another epoch's outline at those pixels: where it is on this view is not known, so it neither blocks nor counts.
    hub, o3, calls, records, told = checked(lambda hub: (stoma(hub, LEFT), stoma(hub, RIGHT, source="click", epoch=1))[0],
                                            {"left": [{"fits": "wrong", "box_2d": RIGHT_BOX}], "right": [{"fits": "yes"}]})
    assert records[-1]["done"] == "replaced" and "outlines in this view are named" not in calls[0][1]
    # Another name beside it is not one of several.
    hub, o3, calls, records, told = checked(lambda hub: (stoma(hub, LEFT), stoma(hub, RIGHT, source="click", label="guard cell"))[0],
                                            {"left": [{"fits": "yes"}], "right": []})
    assert "outlines in this view are named" not in calls[0][1]


def test_a_redraw_not_yet_in_place_says_it_shows_once_checked_and_an_unsure_name_is_not_said():
    from orchestrator.outline_check import redrawn_words

    assert redrawn_words("too_big", "stoma", verified=False) == ("Redrew the stoma outline: the old one took in too much "
                                                                 "around it; it shows again once its place is checked.")
    assert redrawn_words("wrong", "Not sure") == "Redrew an outline: the old one was on something else."


def test_an_outline_keeps_the_box_it_was_outlined_from():
    """`origin_box` (tools.outline_at): where the look meant it, which the check is told when others share its name."""
    from orchestrator import tools
    from tests.test_apply import World

    world = World()
    boxed = asyncio.run(tools.outline_at(world, world.latest_info.id, 300, 200, box=(280, 180, 320, 220), source="ai:sight"))
    clicked = asyncio.run(tools.outline_at(world, world.latest_info.id, 500, 200, source="click"))
    assert boxed.origin_box == (280.0, 180.0, 320.0, 220.0) and clicked.origin_box is None


def test_a_redraw_not_yet_in_place_on_the_live_view_says_it_shows_once_checked():
    """The repair is written re-finding (its place on the live view not verified: continuity was lost since the
    view it was checked on), so it is not drawn yet; the words say so."""
    told = []

    async def go():
        hub, checker, calls, logged = two_of_a_kind({"left": [{"fits": "wrong", "box_2d": RIGHT_BOX}], "right": [{"fits": "yes"}]}, told)
        o3 = stoma(hub, LEFT)
        later = SimpleNamespace(id=8, fov=None, epoch=1, dx=0.0, dy=0.0, width=1280, height=720)
        hub.infos[8], hub.latest_info = later, later  # the slide's motion lost continuity before the repair is placed
        await settle(checker)
        return hub, o3

    hub, o3 = asyncio.run(go())
    assert hub.scene.objects[o3.id].track == "refinding"
    assert [t for _, t in told] == [f"Redrew the {STOMA} outline: the old one was on something else; it shows again once "
                                    "its place is checked."]


def test_a_late_repair_after_pan_does_not_duplicate_an_outline_on_the_same_specimen():
    """The later click is anchored at a new slide offset; duplicate prevention compares capture pixels."""
    told = []

    def pan_and_click(hub):
        view = SimpleNamespace(**vars(hub.latest_info))
        view.id, view.dx = 8, 150.0
        hub.infos[8] = view
        hub.latest_info = view
        shifted = [[[x + 150, y] for x, y in poly] for poly in RIGHT]
        stoma(hub, shifted, source='click', offset=(150.0, 0.0))

    async def go():
        hub, checker, calls, logged = two_of_a_kind(
            {'left': [{'fits': 'wrong', 'box_2d': RIGHT_BOX}], 'right': [{'fits': 'yes'}]},
            told, during_second=pan_and_click)
        obj = stoma(hub, LEFT)
        await settle(checker)
        return hub, obj, logged

    hub, obj, logged = asyncio.run(go())
    assert hub.scene.objects[obj.id].polygons == LEFT
    assert any(r.get('covers') for r in logged)
    assert told == []


def test_an_accepted_repair_renews_the_box_of_the_outline_it_actually_made():
    """Keeping the old origin box misdescribes a repaired outline to later provenance readers."""
    async def go():
        hub, checker, _, _, logged = setup('fix', [{'fits': 'too_big', 'box_2d': [400, 450, 550, 550]}, {'fits': 'yes'}])
        obj = add(hub)
        obj.origin_box = (600, 300, 700, 400)
        await settle(checker)
        return hub.scene.objects[obj.id]

    obj = asyncio.run(go())
    assert obj.origin_box == (620.0, 320.0, 680.0, 380.0)


def test_late_repair_location_words_explicitly_name_the_checked_view():
    """A position on an old capture must not be called the outline's location now after the slide pans."""
    from orchestrator.outline_check import redrawn_words

    said = redrawn_words('wrong', STOMA, 1, 'at the right')
    assert 'on the checked view' in said
    assert 'now at the right' not in said


@pytest.mark.parametrize('held_at', ['first-check', 'second-check'])
@pytest.mark.parametrize('case', ['known-rejoin', 'unrelated-epoch', 'less-overlap'])
def test_a_held_repair_checks_occupancy_through_the_actual_rejoin_carry(held_at, case):
    """Both early and final guards must recognize B after I1 carries it, without inventing unknown geometry."""
    from orchestrator.hub import Hub

    async def go():
        hub, checker, _, _, logged = setup('fix', [])
        hub.latest_info.epoch = 2
        ready, release = asyncio.Event(), asyncio.Event()
        told, calls = [], []
        checker.tell = lambda request, words: told.append(words)
        box = GUARD_BOX if case == 'less-overlap' else RIGHT_BOX

        async def ask(images, text, max_output_tokens=0):
            calls.append(text)
            if len(calls) == (1 if held_at == 'first-check' else 2):
                ready.set()
                await release.wait()
            return SimpleNamespace(data={'fits': 'too_big' if case == 'less-overlap' else 'wrong',
                                         'box_2d': box} if len(calls) == 1 else {'fits': 'yes'})

        async def outline_again(capture, rect, label, made_by=None):
            x0, y0, x1, y1 = rect
            return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]

        checker.agent = SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask))
        checker.outline_again = outline_again
        original = RIGHT if case == 'less-overlap' else LEFT
        obj = stoma(hub, original, epoch=2)
        other = stoma(hub, RIGHT, source='click', epoch=1 if case == 'unrelated-epoch' else 2) \
            if held_at == 'first-check' else None
        await asyncio.wait_for(ready.wait(), HANG_S)
        if other is None:
            # The second check was already entered: B lands while that model call is held.
            other = stoma(hub, RIGHT, source='click', epoch=1 if case == 'unrelated-epoch' else 2)
        delta = (120.0, -60.0)
        now = SimpleNamespace(**{**vars(hub.latest_info), 'id': 8, 'epoch': 1, 'dx': delta[0], 'dy': delta[1]})
        hub.infos[now.id], hub.latest_info = now, now
        hub.motion = SimpleNamespace(rejoins=[] if case == 'unrelated-epoch' else [
            {'from': 2, 'to': 1, 'delta': delta}])
        hub._rejoins_seen, hub._rejoined = 0, {}
        # A past link out of the epoch now must not pull either object past the live view.
        hub.epoch_links = {1: (0, (900.0, 900.0))}
        Hub._carry_over(hub)  # actual I1 application path, not a hand-edited transform
        assert other.anchor_epoch == 1
        assert obj.anchor_epoch == (2 if case == 'unrelated-epoch' else 1)
        assert obj.revision == 0 and other.revision == 0  # carries are not semantic revisions
        release.set()
        await asyncio.wait_for(asyncio.gather(*list(checker.tasks)), 10)
        Hub._carry_over(hub)  # also exercise the late-write carry on the next publication
        records = [r for r in logged if r.get('kind') == 'outline-check' and r.get('objectId') == obj.id]
        assert len(hub.scene.objects) == 2 and other.polygons == RIGHT
        if case == 'known-rejoin':
            assert obj.polygons == original
            assert any(r.get('covers') == other.id for r in records)
            assert not any(r.get('done') == 'replaced' for r in records)
            assert told == []
        else:
            assert obj.polygons != original
            assert any(r.get('done') == 'replaced' for r in records)
            assert not any(r.get('covers') for r in records)
            assert told  # a genuine repair remains allowed and its late outcome is said

    asyncio.run(go())


@pytest.mark.parametrize('carried', [False, True], ids=['same-epoch', 'carried-epoch'])
@pytest.mark.parametrize('case', ['duplicate', 'smaller-overlap', 'unrelated-epoch', 'separate-neighbor', 'unrelated-geometry'])
def test_a_repair_keeps_older_outline_occupancy_after_its_anchor_stores_expire(carried, case):
    """Short-lived frame bytes/facts expiring do not erase an outline made through the real scene writer."""
    import gc
    import weakref
    from orchestrator import tools
    from orchestrator.hub import Hub
    from tests.test_apply import World

    async def go():
        hub = World()
        hub.broadcast = lambda message: None
        hub.settings.outline_check = 'fix'
        if case == 'unrelated-epoch':
            hub.frame(0, 0, epoch=2)
        hub.latest_info.disc = 100.0
        capture = hub.captures.take(hub.latest_info.id)
        old_capture = weakref.ref(capture)
        neighbor = [[[1000.0, 300.0], [1100.0, 300.0], [1100.0, 400.0], [1000.0, 400.0]]]
        occupied = neighbor if case == 'separate-neighbor' else RIGHT
        other = await tools.add_outline(hub, capture, occupied, source='click', label=STOMA)
        old_frame = other.anchor_frame
        assert other.track == 'tracking' and other.template is not None
        del capture  # Scene geometry must not depend on retaining this JPEG/Capture indefinitely.
        now = hub.frame(0, 0, epoch=0)
        now.disc = 102.0 if case == 'unrelated-geometry' else 100.0
        ready, release = asyncio.Event(), asyncio.Event()
        logged, told, calls = [], [], []

        async def ask(images, text, max_output_tokens=0):
            calls.append(text)
            if len(calls) == 1:
                ready.set()
                await release.wait()
                return SimpleNamespace(data={'fits': 'too_big' if case == 'smaller-overlap' else 'wrong',
                                             'box_2d': GUARD_BOX if case == 'smaller-overlap' else RIGHT_BOX})
            return SimpleNamespace(data={'fits': 'yes'})

        async def outline_again(capture, rect, label, made_by=None):
            x0, y0, x1, y1 = rect
            return [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]]

        checker = OutlineCheck(hub, SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask)),
                               outline_again, log=logged.append)
        checker.tell = lambda request, words: told.append(words)
        original = RIGHT if case == 'smaller-overlap' else LEFT
        obj = await tools.add_outline(hub, hub.captures.take(now.id), original, source='ai:sight', label=STOMA)
        try:
            await asyncio.wait_for(ready.wait(), HANG_S)
            # The old accurately tracking anchor falls out of both real short-lived stores while A's check waits.
            hub.ring.pop(old_frame)
            hub.infos.pop(old_frame)
            gc.collect()
            assert old_capture() is None and hub.captures.get(old_frame) is None
            assert other.track == 'tracking' and other.polygons == occupied
            if carried:
                live = hub.frame(0, 0, epoch=0)
                live.epoch, live.dx, live.dy, live.disc = 1, 120.0, -60.0, now.disc
                hub.motion = SimpleNamespace(rejoins=[{'from': 0, 'to': 1, 'delta': (120.0, -60.0)}])
                hub.epoch_links, hub._rejoins_seen, hub._rejoined = {}, 0, {}
                Hub._carry_over(hub)
                assert other.anchor_epoch == (2 if case == 'unrelated-epoch' else 1)
                assert other.anchor_frame == old_frame  # actual carry does not refresh the expired frame anchor
        finally:
            release.set()
            await asyncio.wait_for(asyncio.gather(*list(checker.tasks)), 10)
        records = [r for r in logged if r.get('kind') == 'outline-check' and r.get('objectId') == obj.id]
        assert len(hub.scene.objects) == 2 and other.polygons == occupied
        if case == 'duplicate':
            assert obj.polygons == original
            assert any(r.get('covers') == other.id for r in records)
            assert not any(r.get('done') == 'replaced' for r in records) and told == []
        else:
            assert obj.polygons != original
            assert any(r.get('done') == 'replaced' for r in records)
            assert not any(r.get('covers') for r in records) and told

    asyncio.run(go())


def test_a_reply_that_used_to_be_cut_off_is_read_now():
    """Live 2026-10-11: 3 of 7 checks read "unclear" because the reply was cut off (MAX_TOKENS): a thinking model
    counts its thinking in the same cap, and the cap was 200 in all. The check now leaves its thinking a budget of its
    own; a reply still cut off is said to have been, never taken for the model's judgment."""
    from orchestrator.outline_check import CHECK_REPLY_TOKENS, CHECK_THINKING_TOKENS, CUT_OFF, check

    thought, reply = 640, '{"fits": "too_big", "why": "takes in the guard cells too", "box_2d": [400, 450, 550, 550]}'

    class Thinking:  # as Gemini 3 at thinking level "low": thinking first, then the reply, in one cap
        async def ask(self, images, prompt, max_output_tokens=0):
            if max_output_tokens < thought + 60:
                return SimpleNamespace(data=None, raw_text=reply[:max(0, max_output_tokens - thought) * 4] or '{"fits": "too_')
            return SimpleNamespace(data=__import__("json").loads(reply), raw_text=reply)

    fits, why, box, _ = asyncio.run(check(Thinking(), JPEG, JPEG, "stoma"))
    assert (fits, box) == ("too_big", [400, 450, 550, 550]) and CHECK_THINKING_TOKENS >= thought
    assert CHECK_REPLY_TOKENS >= 60

    class Cut:  # the cap of before: what it said is not a verdict
        async def ask(self, images, prompt, max_output_tokens=0):
            return await Thinking().ask(images, prompt, 200)

    assert asyncio.run(check(Cut(), JPEG, JPEG, "stoma"))[:2] == ("unclear", CUT_OFF)


def test_the_check_and_its_redraw_are_part_of_how_the_outline_was_made():
    """The check's model and verdict are kept on the outline (known.chain: "checked by"), and a redraw changes what
    made it to what drew it again, with what drew it first kept; the log names both (Slice 2, 2026-10-11)."""
    from orchestrator.board import how_made

    async def go():
        hub, checker, asked, again, logged = setup("fix", [{"fits": "too_big", "box_2d": [400, 450, 550, 550]}, {"fits": "yes"}])

        async def outline_again(capture, box, label, made_by=None):
            again.append(made_by)
            return {"polygons": BETTER, "madeBy": {"operation": "outline", "implementation": "sam3.1-fal", "policy": "route",
                                                    "origin": "background", "execution": "x2"}}

        checker.outline_again = outline_again
        obj = add(hub)
        obj.made_by = {"operation": "outline", "implementation": "edgetam", "policy": "route", "origin": "brain-default",
                       "execution": "x1"}
        # One outline at a time: the scripted replies are taken in order, so two outlines checked at once would take
        # them in either order (the gate of 2026-10-11 caught it: 1 run in 3 failed).
        await settle(checker)
        fitting = add(hub, label="pore")
        await settle(checker)
        return hub, obj, fitting, again, logged

    hub, obj, fitting, again, logged = asyncio.run(go())
    repaired = hub.scene.objects[obj.id]
    assert again == [{"operation": "outline", "implementation": "edgetam", "policy": "route", "origin": "brain-default",
                      "execution": "x1"}]
    assert repaired.made_by["implementation"] == "sam3.1-fal" and repaired.made_by["repairOf"] == "x1"
    assert repaired.checked["redrawn"] and repaired.checked["before"] == "edgetam" and repaired.checked["fits"] == "too_big"
    assert "drawn by EdgeTAM (on this Mac), checked by Gemini 3.5 Flash (it took in too much) and redrawn by SAM 3.1 (cloud)" \
        in how_made(repaired)
    assert hub.scene.objects[fitting.id].checked["fits"] == "yes"  # kept as drawn: the check is still part of it
    done = next(r for r in logged if r.get("done") == "replaced")
    assert done["model"] == "gemini-3.5-flash" and done["redrawnWith"] == "sam3.1-fal" and done["redrawnExecution"] == "x2"
