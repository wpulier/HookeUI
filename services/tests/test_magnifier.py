import asyncio
import json
from collections import OrderedDict
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from tests.test_population import world  # noqa: F401  (the fixture: cells over a textured slide)

from orchestrator.hub import focus_score
from orchestrator.magnifier import Magnifier, crop_jpeg, region_sharpness, sharpest_frame, side_by_side
from orchestrator.scene import Scene, SceneObject


def texture(seed=0):
    rng = np.random.default_rng(seed)
    img = (rng.random((360, 640)) * 255).astype(np.uint8)
    return cv2.GaussianBlur(img, (0, 0), 1.2)


class FakeHub:
    """Frames of one still view, each blurred by a different amount."""

    def __init__(self, blurs):
        self.scene = Scene()
        self.infos = OrderedDict()
        self.frames = {}
        self.sent = []
        base = texture()
        for i, blur in enumerate(blurs):
            gray = cv2.GaussianBlur(base, (0, 0), blur) if blur else base
            ok, jpeg = cv2.imencode(".jpg", cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR), [cv2.IMWRITE_JPEG_QUALITY, 95])
            self.frames[i] = SimpleNamespace(jpeg=jpeg.tobytes())
            self.infos[i] = SimpleNamespace(id=i, width=640, height=360, dx=0.0, dy=0.0, epoch=0, steady=True,
                                            wall_at=100.0 + i * 0.03, focus=focus_score(gray, None))
        self.latest_info = self.infos[len(blurs) - 1]
        self.source = SimpleNamespace(get=self.frames.get)

    def broadcast(self, payload):
        self.sent.append(payload)


def stoma(hub):
    obj = SceneObject(id="o1", kind="highlight", polygons=[[[300, 150], [360, 150], [360, 210], [300, 210]]],
                      anchor_frame=0, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label="stoma")
    return hub.scene.add(obj)


def test_blur_lowers_both_sharpness_scores():
    sharp, soft = texture(), cv2.GaussianBlur(texture(), (0, 0), 3)
    assert focus_score(sharp, None) > focus_score(soft, None) * 3
    assert region_sharpness(sharp) > region_sharpness(soft) * 3


def test_sharpest_frame_picks_the_least_blurred_frame():
    hub = FakeHub([3, 2, 0, 4, 1.5])
    capture, rect, _ = sharpest_frame(hub, stoma(hub))
    assert capture.id == 2  # the chosen view's bytes and facts, taken together (ADR 0010)
    x0, y0, x1, y1 = rect  # 15% padding around the 60 px box, at least 48 px
    assert x0 < 300 and x1 > 360 and y0 < 150 and y1 > 210


def test_crop_is_native_resolution_jpeg_of_the_object():
    hub = FakeHub([2, 0])
    jpeg, meta = crop_jpeg(hub, stoma(hub))
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
    assert meta["frame_id"] == 1 and img.shape[1] == meta["rect"][2] - meta["rect"][0]


def test_frames_from_another_epoch_are_never_used():
    hub = FakeHub([0, 2])
    hub.infos[0].epoch = 5  # the sharp frame is from before a tracking break
    capture, _, _ = sharpest_frame(hub, stoma(hub))
    assert capture.id == 1


def test_magnifier_state_follows_selection_and_closes_with_its_object():
    hub = FakeHub([0])
    stoma(hub)
    m = Magnifier(hub)
    m.follow("o1")
    assert not m.open  # following only moves an open magnifier
    m.zoom("o1")
    assert m.open and [s["kind"] for s in m.sources] == ["live", "held"] and m.index == 0
    m.hold()
    assert m.index == 1 and m.sources[1]["url"].startswith("/api/crop/o1")
    m.show_image("/research/x.png", "Annotated close-up", credit="Researcher 1")
    assert m.sources[m.index]["kind"] == "image" and m.sources[m.index]["credit"] == "Researcher 1"
    m.forget("o9")
    assert m.open
    m.forget("o1")
    assert not m.open and hub.sent[-1]["open"] is False


def test_side_by_side_joins_crops_at_one_height():
    a = cv2.imencode(".jpg", np.zeros((100, 50, 3), np.uint8))[1].tobytes()
    b = cv2.imencode(".jpg", np.zeros((50, 100, 3), np.uint8))[1].tobytes()
    img = cv2.imdecode(np.frombuffer(side_by_side([a, b], height=120, gap=8), np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[0] == 120 and img.shape[1] == 60 + 8 + 240


# --- the zoom level -------------------------------------------------------------------------

def test_level_more_less_fit_max_and_numbers():
    hub = FakeHub([0])
    stoma(hub)  # 60 px: fitted window 104 x 78 px, so the closest is 78 / 4 = 19.5x
    m = Magnifier(hub)
    m.zoom("o1")
    assert m.level is None and hub.sent[-1]["zoom"] is None  # fitted
    assert m.set_level("more") == 2 and hub.sent[-1]["zoom"] == 2
    assert m.set_level("more") == 4
    assert m.set_level("less") == 2
    assert m.set_level(6) == 6 and m.set_level("x3") == 3 and m.set_level("8x") == 8
    assert m.set_level("max") == m.max_level() == 19.5
    assert m.set_level(500) == 19.5  # never refuses: the closest it goes
    assert m.pixel_enlarged()
    assert m.set_level("fit") == 1 and m.level is None
    assert m.set_level("less") == 1  # never below fitting the object
    with pytest.raises(ValueError):
        m.set_level("sideways")


def test_zoom_on_the_open_object_changes_level_and_keeps_sources():
    hub = FakeHub([0])
    stoma(hub)
    m = Magnifier(hub)
    m.zoom("o1")
    m.show_image("/research/x.png", "Annotated close-up", credit="Researcher 1")
    sources = list(m.sources)
    assert [s["kind"] for s in sources] == ["live", "held", "image"]  # live stays beside the picture
    assert m.zoom("o1", level=4) == 4
    assert m.sources == sources and m.index == 0 and hub.sent[-1]["zoom"] == 4  # explicit object zoom selects its camera source
    assert m.zoom("o1") == 4  # no level given: it stays where it is
    hub.scene.add(SceneObject(id="o2", kind="highlight", polygons=[[[100, 100], [140, 100], [140, 140], [100, 140]]],
                              anchor_frame=0, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label="cell"))
    assert m.zoom("o2") == 1 and m.level is None  # a new object starts fitted
    assert m.zoom("o1", level="3") == 3  # unless a level is given


def test_show_image_keeps_a_live_close_up_of_the_selection():
    hub = FakeHub([0])
    stoma(hub)
    hub.scene.select("o1")
    m = Magnifier(hub)
    m.show_image("/research/x.png", "Reference")
    assert m.object_id == "o1" and [s["kind"] for s in m.sources] == ["live", "held", "image"]
    assert m.sources[m.index]["kind"] == "image"


def test_selected_saved_picture_keeps_its_own_subject_not_the_live_target():
    hub = FakeHub([0])
    stoma(hub)
    m = Magnifier(hub)
    m.show_image("/saved/first.jpg", "first saved candidate", object_id="o1")
    m.show_image("/saved/second.jpg", "second saved candidate", object_id="o2")
    m.set_index(next(i for i, s in enumerate(m.sources) if s.get("url") == "/saved/first.jpg"))
    assert m.object_id == "o2"  # live source stays available independently
    assert m.displayed_object == "o1"
    m.show_image("/reference.jpg", "outside example", kind_of="reference")
    assert m.displayed_object is None  # an outside picture is not the retained live target


def test_model_screen_notes_name_the_selected_picture_not_its_hidden_live_target():
    from orchestrator.brain.context import capture
    from tests.test_brain import make

    brain, hub, _ = make([])
    stoma(hub)
    m = Magnifier(hub)
    m.zoom("o1")
    m.show_image("/reference.jpg", "outside example", kind_of="reference")
    from orchestrator.brain.toolbox import Services
    brain.services = Services(hub=hub, magnifier=m)
    text = capture(brain).screen
    assert "the magnifier is open on outside example" in text
    assert "the magnifier is open on o1" not in text


def test_reference_can_stay_beside_a_saved_picture_with_independent_enlargement():
    hub = FakeHub([0])
    m = Magnifier(hub)
    specimen = cv2.imencode(".jpg", np.full((40, 50, 3), 60, np.uint8))[1].tobytes()
    reference = cv2.imencode(".jpg", np.full((70, 80, 3), 180, np.uint8))[1].tobytes()
    original = m.show_picture(specimen, "saved candidate c4", object_id="o4")
    before = len(hub.sent)
    outside = m.show_picture(reference, "outside example", kind_of="reference", beside=True)
    assert len(hub.sent) == before + 1  # no intermediate replacement flash
    assert m.picture_now()["url"] == original
    assert m.shown_beside()["url"] == outside
    assert m.displayed_object == "o4"
    m.scale_picture(outside, "more")
    assert m.shown_beside()["displayScale"] == 1.25
    assert m.picture_now().get("displayScale", 1) == 1
    assert m.pictures[original.rsplit("/", 1)[-1]] == specimen
    m.set_index(next(i for i,s in enumerate(m.sources) if s.get("url")==outside))
    assert m.shown_beside() is None  # never pair an image with itself
    m.pair_sources(original, "")
    assert m.picture_now()["url"] == original and m.shown_beside() is None
    assert any(s.get("url")==outside for s in m.sources)  # hidden, still retrievable


def test_zoom_tool_levels_and_acts_on_the_open_magnifier():
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox import Services, build

    hub = FakeHub([0])
    stoma(hub)
    m = Magnifier(hub)
    registry = Registry()
    registry = build(Services(hub=hub, agent=None, magnifier=m))

    async def go(args):
        r = registry.start("zoom", args, ToolContext("chat", 1, 0))
        if r.task is not None:
            await r.task
        return r

    r = asyncio.run(go({"object": "o1"}))
    assert r.ok and "×1" in r.summary and m.open and m.level is None
    r = asyncio.run(go({"level": "more"}))  # no object named: the open one
    assert r.ok and m.level == 2 and "×2" in r.summary
    asyncio.run(go({}))  # "magnify it" again on what is open: closer
    assert m.level == 4
    r = asyncio.run(go({"level": 100}))
    assert r.ok and m.level == 19.5 and "pixel-enlarged" in r.summary and "closest" in r.summary
    r = asyncio.run(go({"level": "fit"}))
    assert r.ok and m.level is None
    r = asyncio.run(go({"level": "sideways"}))
    assert not r.ok


def test_studio_level_command_sets_the_level():
    from orchestrator.commands import handle

    hub = FakeHub([0])
    stoma(hub)
    m = Magnifier(hub)
    m.zoom("o1")
    sent = []
    client = SimpleNamespace(offer=lambda message, lossy=None: sent.append(json.loads(message)))
    app = SimpleNamespace(hub=hub, magnifier=m, day=SimpleNamespace(local_only=False))

    def level(value):
        asyncio.run(handle(app, client, {"type": "magnifier", "action": "level", "level": value}))
        return sent[-1]

    reply = level(3.5)
    assert reply["ok"] and reply["level"] == 3.5 and hub.sent[-1]["zoom"] == 3.5
    level("more")
    assert m.level == 7
    assert level("nope")["ok"] is False and m.level == 7


def test_magnifying_or_dissecting_an_outline_is_what_it_means_to_the_requests_later_steps():
    """The partner's Amendment 1, C2 (the executor's P3): a step that points at an outline (magnify, dissect, like
    select) records it on the request, so "it" in the steps after is that outline."""
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build

    hub = FakeHub([0])
    stoma(hub)

    async def parts(obj):
        return {"parts": ["pore"], "summary": ""}

    registry = build(Services(hub=hub, agent=None, magnifier=Magnifier(hub), dissector=SimpleNamespace(dissect=parts)))
    for tool in ("zoom", "dissect"):
        ctx = ToolContext("chat", 1, 0)

        async def go():
            r = registry.start(tool, {"object": "stoma"}, ctx)
            if r.task is not None:
                await r.task
            return r

        assert asyncio.run(go()).ok and ctx.pointed == "o1", tool


def test_zoom_on_that_cell_means_the_cell_selected_when_the_user_asked(world):
    """The executor's re-check of #179 (PZ1-PZ3): "show that cell in HD" means the cell selected when the user
    asked, not one clicked while it is answered; a click elsewhere since leaves it meant; and with an outline
    selected when the user asked, "zoom on that" is the outline, not a cell clicked since."""
    from orchestrator.board import Board
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.population import layer_of
    from orchestrator.scene import SceneObject
    from orchestrator.ticket import issue, using
    from tests.test_population import cells

    population, _ = cells(world)
    layer = layer_of(world)
    m = Magnifier(world)
    registry = build(Services(hub=world, agent=None, magnifier=m))
    board = Board(world)
    c1, c2 = population.live()[0], population.live()[1]

    def zoom(words, asked, args):
        ctx = ToolContext("chat", 1, world.latest_info.id, words, when_asked=asked, board=board)

        async def go():
            with using(issue(world, "turn")):
                r = registry.start("zoom", args, ctx)
                if r.task is not None:
                    await r.task
                return r
        return asyncio.run(go())

    layer.select(c1)
    asked = board.when_asked()
    layer.select(c2)  # PZ3: another cell clicked while it is answered
    r = zoom("show that cell in HD", asked, {"object": "that cell"})
    assert r.ok and world.scene.objects[m.object_id].made_by.get("member") == c1.id, r.summary
    m.close()
    layer.select(c1)
    asked = board.when_asked()
    layer.select(None)  # PZ2: a click elsewhere cleared the cell
    r = zoom("show that cell in HD", asked, {"object": "that cell"})
    assert r.ok and world.scene.objects[m.object_id].made_by.get("member") == c1.id, r.summary
    m.close()
    info = world.latest_info
    stoma = world.scene.add(SceneObject(world.scene.new_id(), "highlight", [[[10, 10], [60, 10], [60, 60], [10, 60]]], info.id,
                                        info.epoch, (info.dx, info.dy), "click", label="stoma"))
    world.scene.select(stoma.id)
    asked = board.when_asked()
    world.scene.select(None)
    layer.select(c2)  # PZ1: a cell clicked while "zoom on that" is answered
    r = zoom("zoom on that", asked, {})
    assert r.ok and m.object_id == stoma.id, r.summary


def test_zoom_on_it_after_the_requests_own_select_and_on_a_cell_of_a_population_no_longer_shown(world):
    """The C2 re-check's survivors Z3 and Z5: "select the stoma and zoom on it" zooms on the stoma the step selected,
    not the cell selected when the user asked; and "that cell" is the cell of the population shown when the user
    asked, even after another population is shown."""
    from orchestrator.board import Board
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.population import layer_of
    from orchestrator.scene import SceneObject
    from orchestrator.ticket import issue, using
    from tests.test_population import cells

    first, _ = cells(world)
    layer = layer_of(world)
    m = Magnifier(world)
    registry = build(Services(hub=world, agent=None, magnifier=m))
    board = Board(world)

    def zoom(words, asked, args, pointed=None):
        ctx = ToolContext("chat", 1, world.latest_info.id, words, when_asked=asked, board=board)
        ctx.pointed = pointed

        async def go():
            with using(issue(world, "turn")):
                r = registry.start("zoom", args, ctx)
                if r.task is not None:
                    await r.task
                return r
        return asyncio.run(go())

    c1 = first.live()[0]
    layer.select(c1)
    asked = board.when_asked()
    info = world.latest_info
    stoma = world.scene.add(SceneObject(world.scene.new_id(), "highlight", [[[10, 10], [60, 10], [60, 60], [10, 60]]], info.id,
                                        info.epoch, (info.dx, info.dy), "ai:test", label="stoma"))
    r = zoom("select the stoma and zoom on it", asked, {"object": "it"}, pointed=stoma.id)  # Z3
    assert r.ok and m.object_id == stoma.id, r.summary
    m.close()
    second, _ = cells(world)  # another population, now the one shown
    layer.show(second)
    r = zoom("show that cell in HD", asked, {"object": "that cell"})  # Z5
    assert r.ok and world.scene.objects[m.object_id].made_by.get("member") == c1.id
    assert world.scene.objects[m.object_id].made_by.get("evidence") == first.id, r.summary


def _zoom_as_asked(world):
    """The real zoom tool, a board, and a way to run a request saved as asked (C2)."""
    from orchestrator.board import Board
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from orchestrator.ticket import issue, using

    m = Magnifier(world)
    registry = build(Services(hub=world, agent=None, magnifier=m))
    board = Board(world)

    def zoom(words, asked, args):
        ctx = ToolContext("chat", 1, world.latest_info.id, words, when_asked=asked, board=board)

        async def go():
            with using(issue(world, "turn")):
                r = registry.start("zoom", args, ctx)
                if r.task is not None:
                    await r.task
                return r
        return asyncio.run(go())
    return m, board, zoom


def _outline(world, x, label, source="ai:test"):
    info = world.latest_info
    return world.scene.add(SceneObject(world.scene.new_id(), "highlight", [[[x, 40], [x + 20, 40], [x + 20, 60], [x, 60]]],
                                       info.id, info.epoch, (info.dx, info.dy), source, label=label))


def test_with_nothing_named_an_open_magnifier_means_what_it_showed_when_the_user_asked(world):
    """The #179 re-check's SF1 (PZ4): the magnifier open on the stoma when the user asked "zoom in more"; the user then
    selects the pore and the open magnifier follows it. With nothing named, the request is about the stoma, never the
    pore it shows now; with nothing changed, it is the magnifier as it is."""
    from tests.test_population import cells

    cells(world)
    m, board, zoom = _zoom_as_asked(world)
    stoma, pore = _outline(world, 40, "stoma"), _outline(world, 120, "pore")
    world.scene.select(stoma.id)
    m.zoom(stoma.id)
    asked = board.when_asked()
    world.scene.select(pore.id)
    m.follow(pore.id)  # the studio's select: an open magnifier follows it (commands.select)
    r = zoom("zoom in more", asked, {})
    assert r.ok and m.object_id == stoma.id, r.summary
    asked = board.when_asked()  # open on the stoma when asked, and still
    r = zoom("zoom in more", asked, {})
    assert r.ok and m.object_id == stoma.id and r.summary.startswith("magnified"), r.summary


def test_with_nothing_named_a_cell_selected_when_asked_is_shown_and_one_removed_since_is_said(world):
    """The #179 re-check's Z6 and Z8 ("show it in HD" with the model leaving the object out, a cell selected when the
    user asked and the magnifier closed: that cell, from its own mask), and note PZ6 (removed by a correction since:
    said so, never "outline the cells again")."""
    from orchestrator.population import correct, layer_of
    from tests.test_population import cells

    population, _ = cells(world)
    layer = layer_of(world)
    m, board, zoom = _zoom_as_asked(world)
    c1, c2 = population.live()[0], population.live()[1]
    layer.select(c1)
    asked = board.when_asked()
    r = zoom("show it in HD", asked, {})
    assert r.ok and world.scene.objects[m.object_id].made_by.get("member") == c1.id, r.summary
    m.close()
    layer.select(c2)
    asked = board.when_asked()
    correct(world, population, "remove", [c2.id], by="user")
    r = zoom("show that cell in HD", asked, {"object": "that cell"})
    assert not r.ok and r.summary.startswith(f"{c2.id} was removed by a correction since you asked"), r.summary


def test_comparison_preserves_equal_features_pixel_size_across_unequal_crops():
    from orchestrator.magnifier import side_by_side

    images = []
    for height in (60, 120):
        image = np.zeros((height, 80, 3), np.uint8)
        image[10:30, 10:30] = 255  # same original feature in two differently padded captures
        images.append(cv2.imencode('.jpg', image)[1].tobytes())
    out = cv2.imdecode(np.frombuffer(side_by_side(images, height=120, same_scale=True), np.uint8), cv2.IMREAD_GRAYSCALE)
    _, _, boxes, _ = cv2.connectedComponentsWithStats((out > 230).astype(np.uint8))
    assert sorted((b[2], b[3]) for b in boxes[1:]) == [(20, 20), (20, 20)]


def test_pairing_keeps_selected_oldest_picture_at_cache_limit():
    from orchestrator.magnifier import PICTURES_KEPT

    m = Magnifier(FakeHub([0]))
    first = m.show_picture(b'first', 'first')
    for n in range(PICTURES_KEPT - 1):
        m.show_picture(str(n).encode(), str(n))
    m.set_index(0)
    newest = m.show_picture(b'newest', 'newest', beside=True)
    assert m.picture_now()['url'] == first and m.shown_beside()['url'] == newest
    assert m.picture_kept(first) and len(m.pictures) == PICTURES_KEPT


def test_explicit_zoom_selects_hidden_live_target_after_switching_to_older_picture(world):
    from orchestrator.board import Board
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox import Services
    from orchestrator.brain.toolbox.magnifier import add_magnifier_tools
    from tests.test_brain import obj
    from tests.test_screen import setup

    first, second = obj(world, 'first'), obj(world, 'second', x=160)
    m = Magnifier(world)
    m.show_image('/saved/first', 'first saved', object_id=first.id)
    m.show_image('/saved/second', 'second saved', object_id=second.id)
    m.set_index(next(i for i, s in enumerate(m.sources) if s.get('url') == '/saved/first'))
    board = Board(world, magnifier=m)
    registry = Registry()
    add_magnifier_tools(registry, Services(hub=world, magnifier=m, board=board))
    _, _, studio = setup(world)

    async def go():
        context = ToolContext('chat', 1, 0)
        context.when_asked = board.when_asked()
        result = registry.start('zoom', {'object': second.id}, context)
        if result.task:
            await result.task
        return result

    result = asyncio.run(go())
    assert result.ok, result.summary
    assert studio.items['magnifier']['source'] == 'live'
    assert studio.items['magnifier']['shows'] == second.id == m.displayed_object


@pytest.mark.parametrize('entry', ['show_picture', 'show_image'])
def test_invalid_pairing_preserves_cache_and_display_state(entry):
    import copy
    from orchestrator.magnifier import PICTURES_KEPT
    from orchestrator.tools import ToolError

    m = Magnifier(FakeHub([0]))
    old = m.show_picture(b'old', 'old')
    for n in range(PICTURES_KEPT + 1):
        m.show_picture(str(n).encode(), str(n))
    m.set_index(next(i for i, s in enumerate(m.sources) if s.get('url') == old))
    before = copy.deepcopy((m.wire(), m.pictures))
    with pytest.raises(ToolError, match='no longer retained'):
        if entry == 'show_picture':
            m.show_picture(b'new', 'new', beside=True)
        else:
            m.show_image('/outside', 'new', beside=True)
    assert (m.wire(), m.pictures) == before
