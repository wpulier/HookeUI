"""Identity (session knowledge K2, contract 2; ADR 0012): two kinds of link, never mixed; an individual is
one physical thing joined by same-object links, its confidence its weakest link (carries included); its
history compared over time only on a shared basis; where it is told only from the slide's measured movement
where its own pixels last matched, else "can't tell"; anything from another run of the app is history only;
kept per day and read back."""
import asyncio
import time
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.identity import add_identity_tools
from orchestrator.identity import (Identity, change_words, comparable, hhmmss, how_words, member_node, outline_node,
                                   where_words)
from orchestrator.ticket import issue, using
from tests.test_apply import World, run
from tests.test_population import CellFinder, cells, world  # noqa: F401 (the fixture)


def keeping(world, tmp_path):
    world.identity = Identity(tmp_path / "identity", today=lambda: "2026-10-07", hub=world)
    return world.identity


def tools(world):
    registry = Registry()
    add_identity_tools(registry, SimpleNamespace(hub=world, log=None))
    return registry


def ask(world, registry, name, args):
    with using(issue(world, "turn")):
        return run(registry.tools[name].handler(ToolContext("chat", 1, None, name), args))


def followed_outline(identity, i):
    return next(n for n in identity.nodes_of(i) if "@" in n)


def fov(world, cx=400.0, cy=250.0, r=300.0):
    from orchestrator.fov import FieldOfView

    world.latest_info.fov = FieldOfView(cx, cy, r, 1.0)


def test_following_cells_makes_individuals_joined_by_promotion_and_same_kind_never_joins(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a, b = [m.id for m in population.live()][:2]
    said = ask(world, tools(world), "follow_cells", {"members": [a, b], "of": population.id})
    # Plain words first (finding 78; review SF7): the cells by their numbers, the individuals' ids last.
    assert said.startswith(f"keeping an eye on {a} and {b}, each outlined") and "measured again every 15 s" in said
    assert said.endswith("(to ask about them later: i1, i2)") and "edgetam" not in said.lower()
    (i1,) = identity.find(member_node(population.id, a))
    outline = followed_outline(identity, i1)
    assert identity.nodes_of(i1) == {member_node(population.id, a), outline} and outline.endswith(f"@{identity_run()}")
    assert identity.confidence(i1) == (1.0, "")  # promotion: a link of full confidence
    population.links.append({"kind": "same-kind", "from": a, "to": b, "basis": "looks", "confidence": 0.95})
    assert member_node(population.id, b) not in identity.nodes_of(i1)  # alike is never the same thing


def identity_run():
    from orchestrator import identity

    return identity.RUN


def test_the_users_link_joins_two_and_the_weakest_link_is_the_confidence(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a, b = [m.id for m in population.live()][:2]
    i = identity.make([member_node(population.id, a)])
    identity.same_one(member_node(population.id, a), member_node(population.id, b), basis="linking", confidence=0.62)
    assert identity.nodes_of(i) == {member_node(population.id, a), member_node(population.id, b)}
    assert identity.confidence(i) == (0.62, "linking")


def test_the_day_is_read_back_and_ids_are_never_reused(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a, b = [m.id for m in population.live()][:2]
    i1 = identity.make([member_node(population.id, a)], why="followed")
    identity.same_one(member_node(population.id, a), outline_node("o99"))
    identity.observe(outline_node("o99"), {"at": 1.0, "epoch": 0, "slide": [1, 2], "producer": "edgetam outline",
                                           "measure": {"areaPx": 10}})
    again = Identity(tmp_path / "identity", today=lambda: "2026-10-07", hub=world)
    assert again.individuals() == [i1] and outline_node("o99") in again.nodes_of(i1)
    assert again.observations[outline_node("o99")][0]["measure"] == {"areaPx": 10}
    assert again.make([member_node(population.id, b)]) == "i2"  # the next number, never one used


def obs(at, area, objective="40x", disc=300.0, producer="edgetam outline"):
    return {"at": at, "epoch": 0, "slide": [100.0, 100.0], "producer": producer, "measure": {"areaPx": area, "elongation": 1.2},
            "basis": {"objective": objective, "frame": [1280, 720], "discPx": disc, "umPerPx": None, "scale": None}}


def test_a_change_is_measured_only_on_a_shared_basis(world, tmp_path):
    identity = keeping(world, tmp_path)
    i = identity.make([outline_node("o7")])
    assert change_words(identity, i) == "not measured twice yet, so nothing can be said of a change"
    identity.observe(outline_node("o7"), obs(time.time() - 60, 400.0))
    identity.observe(outline_node("o7"), obs(time.time(), 460.0))
    said = change_words(identity, i)
    assert "area 400 to 460 px² (+15%)" in said and "both measured the same way (its outline drawn again" in said
    assert "edgetam" not in said  # the producer stays in the record, not in the words (review SF7)
    identity.observe(outline_node("o7"), obs(time.time() + 1, 900.0, objective="100x"))
    assert change_words(identity, i) == "not comparable: the objective changed (40x to 100x)"
    assert comparable(obs(0, 1), obs(0, 1, disc=310.0)).startswith("the eyepiece circle's size changed by 3.2%")
    assert comparable(obs(0, 1), obs(0, 1, producer="cellpose-sam mask")) == (
        "they were measured by different means (its outline drawn again and the cell finder's mask)")


def test_where_it_is_is_measured_from_the_slides_movement_or_cant_be_told(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a = population.live()[0].id
    ask(world, tools(world), "follow_cells", {"members": [a], "of": population.id})
    (i,) = identity.individuals()
    fov(world)
    assert where_words(world, identity, i) == ("in view now, toward the upper left of the view (its outline: its own "
                                               "pixels matched there, carried by the slide's measured movement)")
    run(identity.measure_followed())  # measured on the view now: one observation of its outline
    outline = followed_outline(identity, i)
    assert identity.observations[outline][0]["producer"].endswith(" outline")
    info = world.frame(0.0, 0.0, epoch=1)  # continuity lost
    fov(world)
    run(identity.measure_followed())
    assert outline in identity.lost
    assert where_words(world, identity, i).startswith("can't tell: continuity was lost at ")
    assert info.epoch == 1


# --- review B1: another run's outlines, links and observations are history only ------------------------------
def new_run(monkeypatch, name="run-later"):
    """A restart: frame ids, outline ids and epochs start again; RUN is the new process's."""
    from orchestrator import capture, identity, population

    for module in (capture, identity, population):
        monkeypatch.setattr(module, "RUN", name)
    w = World()
    w.settings.cells = "cellpose-sam"
    w.gpu_heavy = CellFinder()
    w.sent = []
    w.broadcast = w.sent.append
    return w


def test_after_a_restart_a_new_outline_with_the_same_id_is_never_the_followed_one(world, tmp_path, monkeypatch):
    from orchestrator import tools as outlines

    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a = population.live()[0].id
    ask(world, tools(world), "follow_cells", {"members": [a], "of": population.id})
    (i,) = identity.individuals()
    old = followed_outline(identity, i)
    oid = old.split("@")[0]
    fov(world)
    assert run(identity.measure_followed())  # measured once in the first run
    before = len(identity.observations[old])
    # An earlier run's observation stamped later than anything of the new run (a clock set back): history only.
    identity.observe(old, {**identity.observations[old][0], "at": time.time() + 3600})

    later = new_run(monkeypatch)
    for x in (300, 380, 470):  # clicks until the new run has an outline with the old one's id
        run(outlines.outline_at(later, later.latest_info.id, x, 200, box=(x - 15, 185, x + 15, 215), source="click"))
    assert oid in later.scene.objects
    again = Identity(tmp_path / "identity", today=lambda: "2026-10-07", hub=later)
    later.identity = again
    assert again.find(oid) == [] and outline_node(oid) not in again.nodes_of(i)
    assert again.outline(old) is None and again.followed() == []  # following ended with its run
    again.follow(i)
    fov(later)
    assert run(again.measure_followed()) == [] and len(again.observations[old]) == before + 1  # never measured as it
    assert outline_node(oid) not in again.observations
    said = where_words(later, again, i)
    assert said.startswith("can't tell: it was last seen at ") and "in an earlier run of the app" in said
    said = ask(later, tools(later), "follow_cells", {"objects": [oid]})
    assert said.endswith("(to ask about it later: i2)")  # the new outline is a new thing, never i1


def test_an_earlier_runs_link_never_names_this_runs_outline_whatever_its_time(world, tmp_path, monkeypatch):
    from orchestrator import tools as outlines

    later = new_run(monkeypatch)
    identity = keeping(later, tmp_path)
    obj = run(outlines.outline_at(later, later.latest_info.id, 300, 200, box=(285, 185, 315, 215), source="click"))
    population, _ = cells(later)
    c1, c2, c3 = [m.id for m in population.live()][:3]
    now = time.time()
    population.links += [
        # Recorded in another run and stamped an hour later than the new outline (a clock set back).
        {"kind": "same-object", "from": c1, "to": obj.id, "basis": "promotion", "confidence": 1.0, "at": now + 3600,
         "run": "run-earlier"},
        # Kept before runs were recorded and older than the outline: not this outline either.
        {"kind": "same-object", "from": c2, "to": obj.id, "basis": "promotion", "confidence": 1.0, "at": obj.created_at - 60},
        # Kept before runs were recorded and no older than it: this run's outline (the M2.3 rule).
        {"kind": "same-object", "from": c3, "to": obj.id, "basis": "promotion", "confidence": 1.0, "at": obj.created_at + 1},
    ]
    i1, i2, i3 = (identity.make([member_node(population.id, c)]) for c in (c1, c2, c3))
    assert outline_node(obj.id) not in identity.nodes_of(i1) and outline_node(obj.id, "run-earlier") in identity.nodes_of(i1)
    assert identity.nodes_of(i2) == {member_node(population.id, c2)}
    assert outline_node(obj.id) in identity.nodes_of(i3)


# --- review B2: an outline whose own check failed is never "in view now" -------------------------------------
def test_an_outline_whose_own_pixels_failed_says_since_when_never_in_view(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    ask(world, tools(world), "follow_cells", {"members": [population.live()[0].id], "of": population.id})
    (i,) = identity.individuals()
    fov(world)
    obj = identity.outline(followed_outline(identity, i))
    assert where_words(world, identity, i).startswith("in view now")
    since = time.time() - 30
    for track in ("refinding", "lost"):
        world.scene.update(obj.id, track=track, lost_since=since)
        assert where_words(world, identity, i) == (f"I can't confirm it's still there: its own pixels have not matched "
                                                   f"since {hhmmss(since)}, so its outline is not drawn until they do")
        assert run(identity.measure_followed()) == []  # not verified in place: never measured (the review's mutation)
    world.scene.update(obj.id, track="out_of_view", lost_since=None)
    assert where_words(world, identity, i).endswith(
        "from the slide's measured movement since its own pixels last matched (out of view, they can't be checked)")


# --- review B3: a carry (re-find, rejoin, a gap, the follower) is a link with its basis and score -------------
def test_reanchoring_says_how_each_outline_came_back():
    import cv2
    import numpy as np

    from orchestrator.hub import Hub
    from orchestrator.scene import SceneObject, capture_template

    rng = np.random.default_rng(3)
    gray = cv2.GaussianBlur(rng.integers(0, 255, (720, 1280), dtype=np.uint8), (0, 0), 2.0)
    before = np.roll(gray, -5, axis=1)

    def outline(oid, epoch, track):
        obj = SceneObject(id=oid, kind="highlight", polygons=[[[100, 100], [160, 100], [160, 160], [100, 160]]],
                          anchor_frame=1, anchor_epoch=epoch, anchor_offset=(0.0, 0.0), source="ai", track=track,
                          lost_since=1.0 if track != "tracking" else None)
        obj.template, obj.template_origin = capture_template(before, obj.bbox)
        return obj

    info = SimpleNamespace(id=50, dx=5.0, dy=0.0, epoch=0, fov=None)
    changes = dict(Hub._reanchor(None, [outline("o1", 0, "refinding"), outline("o2", 1, "lost"),
                                        outline("o3", 0, "tracking")], gray, info))
    basis, score = changes["o1"]["carried"]
    assert basis == "gap" and score >= 0.6  # back after its own pixels had not matched
    basis, score = changes["o2"]["carried"]
    assert basis == "re-find" and score >= 0.8  # re-found by its template in another epoch
    assert "carried" not in changes.get("o3", {})  # followed all along: continuity, no link
    # A repeating texture: no unique correction, but its own pixels are where the slide's move puts it (a gap).
    tile = rng.integers(0, 255, (16, 16), dtype=np.uint8)
    tiled = np.tile(tile, (45, 80))
    obj = outline("o4", 0, "lost")
    obj.template, obj.template_origin = capture_template(tiled, obj.bbox)
    changes = dict(Hub._reanchor(None, [obj], tiled, SimpleNamespace(id=51, dx=0.0, dy=0.0, epoch=0, fov=None)))
    assert changes["o4"]["carried"][0] == "gap" and "polygons" not in changes["o4"]  # matched in place, not moved


def test_a_rejoin_is_said_as_a_rejoin_once_its_own_pixels_match_and_the_scene_gets_no_stray_key():
    from orchestrator import tools as outlines
    from orchestrator.hub import Hub

    world = World()
    obj = run(outlines.outline_at(world, 1, 300, 200, box=(280, 180, 320, 220), source="click"))
    world.scene.update(obj.id, anchor_epoch=3)
    heard = []
    hub = Hub.__new__(Hub)  # only what the carry needs
    hub.scene, hub.on_carry, hub._rejoined, hub.epoch_links, hub._rejoins_seen = world.scene, [], {}, {}, 0
    hub.on_carry.append(lambda oid, basis, c: heard.append((oid, basis, c)))
    hub.motion = SimpleNamespace(rejoins=[{"from": 3, "to": 0, "delta": (0.0, 0.0)}])
    hub._carry_over()
    assert world.scene.objects[obj.id].track == "refinding" and heard == []  # not said until its pixels match
    hub._apply_reanchor([(obj.id, {"track": "tracking", "lost_since": None, "carried": ("gap", 0.83)})], world.latest_info)
    assert heard == [(obj.id, "rejoin", 0.83)] and world.scene.objects[obj.id].track == "tracking"
    assert not hasattr(world.scene.objects[obj.id], "carried")


def test_a_carry_is_recorded_for_a_followed_thing_and_said_with_its_basis_and_weakest_link(world, tmp_path):
    world.on_carry = []
    identity = keeping(world, tmp_path)
    assert world.on_carry == [identity._carried]
    population, _ = cells(world)
    ask(world, tools(world), "follow_cells", {"members": [population.live()[0].id], "of": population.id})
    (i,) = identity.individuals()
    oid = followed_outline(identity, i).split("@")[0]
    for listener in world.on_carry:
        listener(oid, "re-find", 0.86)
        listener("o999", "gap", 0.61)  # an outline nobody follows: nothing written
    assert identity.confidence(i) == (0.86, "re-find")
    assert "o999" not in str(identity.carries)
    said = how_words(identity, i)
    assert "re-found by its own pixels after the view jumped (0.86)" in said and "as sure as its weakest link, 0.86" in said
    again = Identity(tmp_path / "identity", today=lambda: "2026-10-07", hub=world)
    assert again.confidence(i) == (0.86, "re-find")  # kept and read back
    said = ask(world, tools(world), "individuals", {})
    assert "seen continuously (its outline followed)" not in said and "re-found by its own pixels" in said


def test_the_follower_says_when_it_brings_an_outline_back_across_a_jump():
    import numpy as np

    from tests.test_objtrack import make, pixels, square

    rng = np.random.default_rng(4)
    gray = (rng.random((400, 500)) * 255).astype(np.uint8)
    follower, hub = make()
    heard = []
    hub.carried = lambda oid, basis, c: heard.append((oid, basis, round(c, 2)))
    hub.latest_info = SimpleNamespace(id=50, dx=0.0, dy=0.0, epoch=1)  # continuity was lost since
    hub.infos = {50: hub.latest_info}
    pixels(hub, gray)
    obj = hub.scene.objects["o1"]
    follower.followed["o1"] = {"misses": 0, "area": 400.0}
    hub.gpu_heavy.following = {"o1"}
    obj.template, obj.template_origin = gray[100:140, 190:230].copy(), (80, 80)
    hub.gpu_heavy.where["o1"] = (210, 120)
    asyncio.run(follower.step())
    assert hub.scene.objects["o1"].polygons == square(210, 120) and heard == [("o1", "follower", 1.0)]


# --- review B4: linking checks the views first, keeps one-to-one links once, and counts cells ----------------
class Linker:
    """A stand-in for the linking model's run (operations' `link`), in the shape M3.5's adapter returns: the model's
    candidates kept one to one (`operations.one_to_one`: both ends of the rest named, with the edges) and the cells
    it compared (every live cell of both, unless given). `raw`: the candidates as links, as no adapter returns them
    (only for `link_populations`' own defensive check)."""

    def __init__(self, world, candidates, compared=None, raw=False):
        self.world, self.candidates, self.compared, self.raw, self.calls = world, candidates, compared, raw, 0

    async def run(self, op, capture, prompt, params=None, **kw):
        from orchestrator.evidence import evidence_of
        from orchestrator.operations import one_to_one

        self.calls += 1
        if self.raw:
            output = {"links": list(self.candidates)}
        else:
            store = evidence_of(self.world)
            a, b = store.get(params["a"]), store.get(params["b"])
            output = {**one_to_one([(c["from"], c["to"], c["score"]) for c in self.candidates]),
                      "compared": self.compared or {"a": len(a.live()), "b": len(b.live())}}
        return SimpleNamespace(output=output, execution=SimpleNamespace(executed="trackastra"))


def linking(world, monkeypatch, candidates, compared=None, raw=False):
    from orchestrator import operations
    from orchestrator.brain.toolbox.population import add_population_tools

    linker = Linker(world, candidates, compared, raw)
    monkeypatch.setattr(operations, "runner", lambda hub: linker)
    registry = Registry()
    add_population_tools(registry, SimpleNamespace(hub=world, log=None))
    return linker, registry


def test_linking_is_refused_with_why_when_the_views_cant_be_related(world, tmp_path, monkeypatch):
    keeping(world, tmp_path)
    first, _ = cells(world)
    world.frame(40.0, 0.0)
    moved, _ = cells(world)
    world.frame(0.0, 0.0, epoch=1)
    jumped, _ = cells(world)
    world.frame(0.0, 0.0)
    other, _ = cells(world)
    linker, registry = linking(world, monkeypatch, [])
    moved.view = {**moved.view, "tracked": False}
    said = ask(world, registry, "query_population", {"of": first.id, "against": moved.id, "link": True})
    assert "no links made: can't tell which cells are the same: the slide's motion was not registered" in said
    said = ask(world, registry, "query_population", {"of": first.id, "against": jumped.id, "link": True})
    assert "the app lost track of the slide's movement between the two views" in said
    other.scale_id = {**(other.scale_id or {}), "objective": "100x"}
    said = ask(world, registry, "query_population", {"of": first.id, "against": other.id, "link": True})
    assert "can't tell which cells are the same: the objective changed" in said
    other.scale_id, other.view = first.scale_id, {**other.view, "run": "run-earlier"}
    said = ask(world, registry, "query_population", {"of": first.id, "against": other.id, "link": True})
    assert f"{other.id} was made in an earlier run of the app" in said
    other.view = {**first.view, "disc": [640.0, 360.0, 300.0], "dx": 700.0}
    first.view = {**first.view, "disc": [640.0, 360.0, 300.0]}
    said = ask(world, registry, "query_population", {"of": first.id, "against": other.id, "link": True})
    assert "the two views show different parts of the slide (their fields do not overlap)" in said
    assert linker.calls == 0 and world.identity.links == []
    # The slide moving between two views it tracked is no reason: the model is given the overlap, aligned (M3.5).
    moved.view = {**moved.view, "tracked": True}
    ask(world, registry, "query_population", {"of": first.id, "against": moved.id, "link": True})
    assert linker.calls == 1


def test_links_are_kept_one_to_one_once_and_cells_are_counted_not_links(world, tmp_path, monkeypatch):
    identity = keeping(world, tmp_path)
    a, _ = cells(world)
    b, _ = cells(world)
    x, y = [m.id for m in a.live()], [m.id for m in b.live()]
    linker, registry = linking(world, monkeypatch, [
        {"from": x[0], "to": y[0], "score": 0.91}, {"from": x[1], "to": y[1], "score": 0.82},
        {"from": x[2], "to": y[2], "score": 0.6}, {"from": x[2], "to": y[3], "score": 0.5},  # a division
    ])
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "2 cells linked one to one by the linking model (its scores 0.82 to 0.91)" in said
    # Both ends of the division, as the adapter names them (`ambiguous`), and what it compared.
    assert (f"{x[2]}, {y[2]} and {y[3]} were in links that were not one to one (a division, a merge, or the model could "
            "not tell which), so not linked") in said
    assert f"it compared 6 of {a.id}'s cells and 6 of {b.id}'s" in said
    assert "2 cells are linked as the same cell in both (linking)" in said
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "0 cells linked one to one" in said and "2 already linked earlier, not linked twice" in said
    assert "2 cells are linked as the same cell in both (linking)" in said and len(identity.links) == 2
    # A user's link to a second cell makes it ambiguous: counted once, never as two.
    identity.same_one(member_node(a.id, x[0]), member_node(b.id, y[4]))
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id})
    assert "1 cell is linked as the same cell in both" in said and f"{x[0]} and {y[0]} and {y[4]}" not in said
    assert f"{x[0]} and {y[4]} are linked to two or more cells" in said or f"{x[0]} is linked to two" in said


def test_what_the_linking_model_could_not_make_one_to_one_and_what_it_compared_are_said(world, tmp_path, monkeypatch):
    """M3.5's adapter keeps only one-to-one links, names both ends of the rest (`ambiguous`) and counts the cells it
    compared (inside the overlap): neither is read as "not the same" (ADR 0012)."""
    keeping(world, tmp_path)
    a, _ = cells(world)
    b, _ = cells(world)
    x, y = [m.id for m in a.live()], [m.id for m in b.live()]
    _, registry = linking(world, monkeypatch, [{"from": x[0], "to": y[0], "score": 0.9}, {"from": x[1], "to": y[1], "score": 0.7},
                                               {"from": x[1], "to": y[2], "score": 0.6}], compared={"a": 4, "b": 3})
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "1 cell linked one to one" in said
    assert f"{x[1]}, {y[1]} and {y[2]}" in said and "were in links that were not one to one" in said
    assert f"it compared 4 of {a.id}'s cells and 3 of {b.id}'s" in said and "for the others it can't tell" in said


def test_links_an_implementation_gave_that_are_not_one_to_one_are_never_kept(world, tmp_path, monkeypatch):
    """The adapter never returns them (its `one_to_one`); `link_populations` keeps its own check for any implementation
    that would, and names both ends as the adapter does."""
    identity = keeping(world, tmp_path)
    a, _ = cells(world)
    b, _ = cells(world)
    x, y = [m.id for m in a.live()], [m.id for m in b.live()]
    _, registry = linking(world, monkeypatch, [{"from": x[0], "to": y[0], "score": 0.9}, {"from": x[2], "to": y[2], "score": 0.6},
                                               {"from": x[2], "to": y[3], "score": 0.5}], raw=True)
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "1 cell linked one to one" in said and f"{x[2]}, {y[2]} and {y[3]} were in links that were not one to one" in said
    assert len(identity.links) == 1


def test_a_link_outside_the_part_both_views_show_is_left_out(world, tmp_path, monkeypatch):
    keeping(world, tmp_path)
    a, _ = cells(world)
    b, _ = cells(world)
    for p in (a, b):
        p.view = {**p.view, "disc": [300.0, 200.0, 40.0]}  # only the cell at (300, 200) is inside both
    x, y = [m.id for m in a.live()], [m.id for m in b.live()]
    _, registry = linking(world, monkeypatch, [{"from": x[0], "to": y[0], "score": 0.9}, {"from": x[5], "to": y[5], "score": 0.9}])
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "1 cell linked one to one" in said and "1 left out: outside the part of the slide both views show" in said


# --- review SF1 and SF8: measured again from a grown box, only when it is the same thing, off the loop -------
def outline_returning(world, polygon):
    sent = []

    async def call(op, timeout=30.0, **payload):
        sent.append(payload.get("box"))
        return {"polygons": [polygon], "score": 0.9, "ms": 5.0}

    world.gpu.call = call
    return sent


def test_measured_again_from_a_grown_box_and_only_when_the_new_outline_is_the_same_thing(world, tmp_path, monkeypatch):
    from orchestrator import identity as module

    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    ask(world, tools(world), "follow_cells", {"members": [population.live()[0].id], "of": population.id})  # (300, 200), r 12
    obj = identity.outline(followed_outline(identity, identity.individuals()[0]))
    x0, y0, x1, y1 = obj.bbox
    threads = []
    real = asyncio.to_thread
    monkeypatch.setattr(module.asyncio, "to_thread", lambda f, *a: threads.append(f) or real(f, *a))
    cases = [  # its old box is (288, 188, 312, 212); the prompt box (282, 182, 318, 218)
        ([[287, 187], [313, 187], [313, 213], [287, 213]], True),  # grown past its old box: measured
        ([[284, 184], [316, 184], [316, 216], [284, 216]], False),  # it and the neighbour in its box, drawn as one
        ([[308, 184], [315, 184], [315, 194], [308, 194]], False),  # a neighbour drawn instead
        ([[300, 200], [340, 200], [340, 240], [300, 240]], False),  # spilled past its box (into a neighbour)
    ]
    for polygon, same in cases:
        world.frame(0.0, 0.0)  # a new view each round (the runner reuses a result on the same view and box)
        sent = outline_returning(world, polygon)
        (observation,) = run(identity.measure_followed())
        bx0, by0, bx1, by1 = sent[-1]
        assert bx0 <= x0 - 6 and by0 <= y0 - 6 and bx1 >= x1 + 6 and by1 >= y1 + 6  # the prompt box grown
        assert bool(observation["measure"]) is same and ("notMeasured" in observation) is not same, polygon
        assert observation["run"] == identity_run() and "overlap" in observation
    assert threads  # filling, comparing, decoding and measuring ran off the event loop
    box = sent[-1]
    world.frame(0.0, 0.0)
    # The same thing, reaching the right edge of its prompt box: it may be cut off there.
    outline_returning(world, [[x0 + 1, y0 + 1], [box[2] - 1, y0 + 1], [box[2] - 1, y1 - 1], [x0 + 1, y1 - 1]])
    (observation,) = run(identity.measure_followed())
    assert not observation["measure"] and "may be cut off" in observation["notMeasured"]


# --- review SF2: following is what was asked, and says what it can't do -------------------------------------
def test_the_longest_followed_stops_past_four_and_is_said(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    ids = [m.id for m in population.live()]
    registry = tools(world)
    ask(world, registry, "follow_cells", {"members": ids[:1], "of": population.id})
    ask(world, registry, "follow_cells", {"members": ids[1:4], "of": population.id})
    first = identity.find(member_node(population.id, ids[0]))[0]
    assert first in identity.followed()
    said = ask(world, registry, "follow_cells", {"members": ids[4:5], "of": population.id})
    assert f"no longer followed (at most 4 at once): {ids[0]}" in said and first not in identity.followed()
    assert len(identity.followed()) == 4
    # Asked again, the first is the newest followed: the longest followed now (ids[1]) stops instead; its outline
    # is still on the view, so it is not outlined twice.
    outlines = len(world.scene.objects)
    ask(world, registry, "follow_cells", {"members": ids[:1], "of": population.id})
    assert len(world.scene.objects) == outlines
    second = identity.find(member_node(population.id, ids[1]))[0]
    assert first in identity.followed() and second not in identity.followed()


def test_a_cell_removed_by_a_correction_is_said_never_followed_silently(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    a, b = [m.id for m in population.live()][:2]
    population.members[a].status = "deleted"
    said = ask(world, tools(world), "follow_cells", {"members": [a, b]})  # a bare c12 names its own population
    assert said.startswith(f"keeping an eye on {b}, each outlined") and f"{a} was removed by your correction" in said
    with pytest.raises(ToolFailed) as none:
        ask(world, tools(world), "follow_cells", {"members": [a]})
    assert "nothing could be followed" in str(none.value) and ":" in str(none.value)


def test_the_follower_follows_what_the_user_keeps_an_eye_on_before_later_clicks():
    from orchestrator.objtrack import ObjectFollower
    from orchestrator.scene import Scene, SceneObject
    from tests.test_objtrack import square

    scene = Scene()
    for n in range(1, 6):
        scene.add(SceneObject(id=f"o{n}", kind="highlight", polygons=square(50 * n, 100), anchor_frame=1, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="click", created_at=1000.0 + n))
    hub = SimpleNamespace(scene=scene, identity=SimpleNamespace(followed_outlines=lambda: ["o1"]))
    wanted = [o.id for o in ObjectFollower(hub).wanted(None)]
    assert wanted == ["o1", "o5", "o4", "o3"]  # o1, oldest, is followed; then the newest highlights


def test_following_after_a_restart_ends_and_is_never_measured(world, tmp_path, monkeypatch):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    ask(world, tools(world), "follow_cells", {"members": [population.live()[0].id], "of": population.id})
    assert identity.followed() and identity.followed_outlines()
    later = new_run(monkeypatch)
    again = Identity(tmp_path / "identity", today=lambda: "2026-10-07", hub=later)
    assert again.followed() == [] and again.followed_outlines() == []


# --- review SF3: corrections ---------------------------------------------------------------------------------
def test_a_member_a_correction_removed_is_no_longer_the_thing_and_is_said(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a = population.live()[0].id
    ask(world, tools(world), "follow_cells", {"members": [a], "of": population.id})
    (i,) = identity.individuals()
    assert member_node(population.id, a) in identity.nodes_of(i)
    population.members[a].status = "deleted"
    population.revision = 1
    population.revisions.append({"r": 1, "change": f"removed {a}", "by": "user", "members": [a], "was": {a: "model"},
                                 "added": [], "undoes": None})
    nodes = identity.nodes_of(i)
    assert member_node(population.id, a) not in nodes and len(nodes) == 1  # its outline stays the thing followed
    assert all(o["node"] != member_node(population.id, a) for o in identity.history(i))
    said = ask(world, tools(world), "individuals", {})
    assert f"{a} was removed by your correction (revision 1), so it no longer counts as this thing" in said
    population.members[a].status = "model"  # an undo puts it back
    assert member_node(population.id, a) in identity.nodes_of(i)


def test_a_member_a_merge_added_is_stamped_with_the_revision_that_made_it(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a = population.live()[0].id
    population.revision = 2
    population.revisions += [{"r": 1, "change": "merged", "members": [], "added": [a]}, {"r": 2, "change": "x", "members": []}]
    i = identity.make([member_node(population.id, a)])
    (seen,) = identity.history(i)
    assert seen["revision"] == 1  # made in revision 1, not stamped with the current one


# --- review SF4: when continuity was lost, and found again ---------------------------------------------------
@pytest.mark.parametrize("departure_known", [True, False])
def test_reanchored_outline_does_not_substitute_last_seen_for_loss_time(world, tmp_path, departure_known):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    ask(world, tools(world), "follow_cells", {"members": [population.live()[0].id], "of": population.id})
    (individual,) = identity.individuals()
    node = followed_outline(identity, individual)
    run(identity.measure_followed())
    last_seen = identity.history(individual)[-1]["at"]
    departure = last_seen + 5
    world.epoch_left = {0: departure} if departure_known else {}
    # A cross-epoch match can replace the outline's anchor before another
    # measurement. The current frame cannot be placed, but the old observation
    # and its epoch's actual departure remain distinct evidence.
    world.frame(0.0, 0.0, epoch=1, wall=departure + 2)
    world.latest_info.tracked = False
    world.scene.update(node.split("@")[0], anchor_epoch=1)
    identity._carried(node.split("@")[0], "re-find", 0.9)
    assert node not in identity.lost

    words = where_words(world, identity, individual)
    assert f"it was last seen at {hhmmss(last_seen)}" in words
    assert f"continuity was lost at {hhmmss(last_seen)}" not in words
    if departure_known:
        assert f"continuity was lost at {hhmmss(departure)}" in words
    else:
        assert "the loss time is unavailable" in words


def test_continuity_lost_says_when_the_view_left_its_epoch_and_a_second_loss_its_own_time(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    ask(world, tools(world), "follow_cells", {"members": [population.live()[0].id], "of": population.id})
    (i,) = identity.individuals()
    node = followed_outline(identity, i)
    run(identity.measure_followed())  # seen once, now
    left = time.time() - 600
    world.epoch_left = {0: left}
    world.frame(0.0, 0.0, epoch=1)
    # Said at when the view left its epoch, before any measuring round wrote the loss down.
    assert f"can't tell: continuity was lost at {hhmmss(left)}" in where_words(world, identity, i)
    run(identity.measure_followed())
    assert identity.lost[node] == left
    world.epoch_left = {}
    assert f"can't tell: continuity was lost at {hhmmss(left)}" in where_words(world, identity, i)
    identity._carried(node.split("@")[0], "re-find", 0.9)  # found again: the loss is cleared
    assert node not in identity.lost
    world.scene.update(node.split("@")[0], anchor_epoch=1)
    world.epoch_left = {0: left, 1: left + 300}
    world.frame(0.0, 0.0, epoch=2)
    run(identity.measure_followed())
    assert identity.lost[node] == left + 300  # the second loss its own time, never the first one's
    assert f"continuity was lost at {hhmmss(left + 300)}" in where_words(world, identity, i)


def test_the_hub_keeps_when_the_view_left_each_epoch():
    from collections import OrderedDict

    from orchestrator.hub import Hub

    hub = Hub.__new__(Hub)
    hub.motion, hub._rejoins_seen, hub.epoch_links, hub._rejoined = SimpleNamespace(rejoins=[]), 0, {}, {}
    hub.scene, hub.infos, hub.on_frame, hub.on_still_change, hub.clients = SimpleNamespace(objects={}), OrderedDict(), [], [], set()
    hub.latest_info, hub._steady_frames, hub.epoch_left = None, 0, {}

    def info(n, epoch, at):
        return SimpleNamespace(id=n, width=10, height=10, dx=0.0, dy=0.0, epoch=epoch, tracked=True, steady=True, fov=None,
                               wall_at=at, quality=(), changed_at=0.0, change=None, analysed_at=0.0,
                               boot="synthetic-epoch-clock", seq=n)

    frame = SimpleNamespace(jpeg=b"")
    hub._publish(frame, info(1, 0, 100.0))
    hub._publish(frame, info(2, 0, 101.0))
    assert hub.epoch_left == {}
    hub._publish(frame, info(3, 1, 102.5))
    assert hub.epoch_left == {0: 102.5}


# --- review SF5 and SF9: declarations; "those aren't the same"; a bare c12 ---------------------------------------
def test_an_individual_is_listed_by_what_made_it(world, tmp_path):
    from orchestrator.produces import TOOLS, kinds_of
    from orchestrator.resources import Index

    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a, b = [m.id for m in population.live()][:2]
    ask(world, tools(world), "same_one", {"a": a, "b": b})
    index = Index(world, identity=identity, day=SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-07"))
    index.today = lambda: "2026-10-07"
    (entry,) = index.list("individual")
    assert entry.made_by == {"tool": "same_one"} and entry.title.startswith("Said to be one") and "where:" in index.read("i1")
    assert "individual" in kinds_of(TOOLS["query_population"])  # linking writes links and individuals


def test_those_arent_the_same_undoes_the_link_as_a_new_event_and_history_stays(world, tmp_path):
    identity = keeping(world, tmp_path)
    first, _ = cells(world)
    second, _ = cells(world)
    a, b = first.live()[0].id, second.live()[0].id
    i1 = identity.make([member_node(first.id, a)])
    i2 = identity.make([member_node(second.id, b)])
    registry = tools(world)
    said = ask(world, registry, "same_one", {"a": a, "b": b})  # bare ids: each its own population
    assert said == f"{a} and {b} are now one thing, because you said so (to ask about it later: {i1})"
    assert identity.individuals() == [i1] and identity.find(i2) == [i1]  # one thing, listed once
    said = ask(world, registry, "same_one", {"a": a, "b": b, "undo": True})
    assert said.startswith(f"{a} and {b} are no longer one thing")
    assert identity.individuals() == [i1, i2] and member_node(second.id, b) not in identity.nodes_of(i1)
    assert len(identity.links) == 1 and identity.links[0]["undone"]["by"] == "user"  # kept, marked undone
    again = Identity(tmp_path / "identity", today=lambda: "2026-10-07", hub=world)
    assert again.individuals() == [i1, i2] and again.links[0]["undone"]
    with pytest.raises(ToolFailed) as none:
        ask(world, registry, "same_one", {"a": a, "b": b, "undo": True})
    assert "I have no link of yours or the linking model's between" in str(none.value)


def test_it_means_the_outline_selected_and_a_followed_one_is_found_by_it(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    ask(world, tools(world), "follow_cells", {"members": [population.live()[0].id], "of": population.id})
    world.scene.select(followed_outline(identity, "i1").split("@")[0])
    said = ask(world, tools(world), "individuals", {"which": "it", "ask": "changed"})
    assert "(i1)" in said and "changed:" in said


def test_the_individuals_tool_says_how_it_knows_and_the_index_lists_them(world, tmp_path):
    from orchestrator.resources import Index

    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a = population.live()[0].id
    registry = tools(world)
    with pytest.raises(ToolFailed) as none:
        ask(world, registry, "individuals", {})
    assert "nothing is being followed today" in str(none.value)
    ask(world, registry, "follow_cells", {"members": [a], "of": population.id})
    said = ask(world, registry, "individuals", {})
    assert said.startswith(f"{a}, outlined (i1): one thing: outlined from its own mask") and "where:" in said
    assert "changed:" in said and "edgetam" not in said.lower()
    index = Index(world, identity=identity, day=SimpleNamespace(day="2026-10-07", sid="2026-10-07", date="2026-10-07"))
    index.today = lambda: "2026-10-07"
    (entry,) = index.list("individual")
    assert entry.id == "i1" and entry.title.startswith("Followed") and entry.title.endswith(f"· {a}, outlined")
    assert "where:" in index.read("i1") and entry.made_by == {"tool": "follow_cells"}


def test_two_captures_are_compared_as_populations_and_cell_by_cell_only_through_links(world, tmp_path):
    from orchestrator.brain.toolbox.population import add_population_tools

    identity = keeping(world, tmp_path)
    first, _ = cells(world)
    second, _ = cells(world)
    registry = Registry()
    add_population_tools(registry, SimpleNamespace(hub=world, log=None))
    said = ask(world, registry, "query_population", {"of": first.id, "against": second.id})
    assert f"{first.id} at " in said and f"{second.id} at " in said and "median area:" in said
    assert "no cell is linked as the same in both, so they can't be compared cell by cell" in said
    a, b = first.live()[0].id, second.live()[0].id
    identity.same_one(member_node(first.id, a), member_node(second.id, b), basis="linking", confidence=0.8)
    said = ask(world, registry, "query_population", {"of": first.id, "against": second.id})
    assert "1 cell is linked as the same cell in both (linking), so it can be compared one by one" in said
    second.scale_id = {**(second.scale_id or {}), "objective": "100x"}
    said = ask(world, registry, "query_population", {"of": first.id, "against": second.id})
    assert "their measurements are not compared: the objective changed" in said and "median area" not in said


def test_linking_two_captures_is_said_and_never_run_unasked(world, tmp_path, monkeypatch):
    from orchestrator.brain.toolbox.population import add_population_tools

    keeping(world, tmp_path)
    first, _ = cells(world)
    second, _ = cells(world)
    registry = Registry()
    add_population_tools(registry, SimpleNamespace(hub=world, log=None))
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "trackastra" else real(name, *a))
    said = ask(world, registry, "query_population", {"of": first.id, "against": second.id, "link": True})
    assert "no links made: Trackastra can't run here (not installed here (trackastra: its Install button on the Models page))" in said


def test_a_box_overlap_is_not_a_basis_that_joins_individuals(world, tmp_path):
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    a = population.live()[0].id
    population.links.append({"kind": "same-object", "from": a, "to": "o42", "basis": "overlap with the first guess (box IoU 0.31)",
                             "confidence": 0.31, "at": time.time(), "run": identity_run()})
    i = identity.make([member_node(population.id, a)])
    assert identity.nodes_of(i) == {member_node(population.id, a)}


# --- the partner's re-check at 77dc0d3 -------------------------------------------------------------------------
# SF2 (BLOCKING here): a cell of an earlier run is never followed, re-found or carried across the restart.
def restarted(world, population, tmp_path, monkeypatch):
    """A restart after `population` was made: its view is read back from disk in the new run (another run's: no
    epoch, an id no live frame has), and the day's identity is read back."""
    from orchestrator.evidence import evidence_of

    later = new_run(monkeypatch)
    store = evidence_of(world)
    jpeg = population.capture.jpeg
    population.capture = None  # its pixels left memory: read back from its saved JPEG
    later.evidence = store
    population.path = tmp_path / population.id
    population.path.mkdir(exist_ok=True)
    (population.path / "capture.jpg").write_bytes(jpeg)
    again = Identity(tmp_path / "identity", today=lambda: "2026-10-07", hub=later)
    later.identity = again
    return later, again


def test_a_cell_segmented_before_a_restart_is_not_followed(world, tmp_path, monkeypatch):
    """Probe P7: following it would outline it on its read-back view, which the hub then searched for near the
    earlier run's offsets."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    mid = population.live()[0].id
    later, again = restarted(world, population, tmp_path, monkeypatch)
    with pytest.raises(ToolFailed) as refused:
        ask(later, tools(later), "follow_cells", {"members": [mid], "of": population.id})
    assert (f"{mid} was segmented before the app restarted, so where it is now is not known: outline the cells again on "
            "the view now") in str(refused.value)
    assert refused.value.next == "outline the cells again on the view now"
    assert again.individuals() == [] and not later.scene.objects  # nothing outlined, nothing followed


def test_an_earlier_runs_cell_outlined_after_a_restart_is_never_re_found_or_carried(world, tmp_path, monkeypatch):
    """Probe P8: c1 followed, the app restarts, c1 is outlined again from its read-back view (a highlight). Its
    outline joins the thing by promotion, but where it is now is not known: the hub never searches for it near the
    earlier run's offsets, a carry of it is not recorded, and nothing says "since" a time it never matched."""
    import cv2
    import numpy as np

    from orchestrator.hub import Hub
    from orchestrator.identity import EARLIER_VIEW
    from orchestrator.population import promote

    keeping(world, tmp_path)
    population, _ = cells(world)
    mid = population.live()[0].id
    ask(world, tools(world), "follow_cells", {"members": [mid], "of": population.id})
    later, again = restarted(world, population, tmp_path, monkeypatch)
    (i,) = again.individuals()
    (obj,) = run(promote(later, population, [mid], why="highlighted"))
    assert obj.anchor_epoch is None and obj.origin < 0 and obj.track == "refinding"
    assert outline_node(obj.id) in again.nodes_of(i)  # its promotion link joins it to the thing
    info = later.frame(0.0, 0.0)  # the same place again: its template matches where the earlier offsets put it
    gray = cv2.imdecode(np.frombuffer(later.ring[info.id], np.uint8), cv2.IMREAD_GRAYSCALE)
    changes = dict(Hub._reanchor(None, [obj], gray, info))
    assert "carried" not in changes.get(obj.id, {}) and "polygons" not in changes.get(obj.id, {})
    assert changes.get(obj.id, {}).get("track", obj.track) in ("refinding", "lost")  # kept unverified
    again._carried(obj.id, "re-find", 0.95)  # told it was carried all the same: not recorded
    assert outline_node(obj.id) not in again.carries and again.confidence(i) == (1.0, "")
    # Even had something moved it onto this run's view (as the re-find did, before the carry was said), it was made on
    # the earlier run's view (its origin): no carry, no place.
    later.scene.update(obj.id, anchor_epoch=info.epoch, anchor_frame=info.id, track="tracking", lost_since=None)
    again._carried(obj.id, "re-find", 0.95)
    assert outline_node(obj.id) not in again.carries
    fov(later)
    assert where_words(later, again, i) == f"can't tell: {EARLIER_VIEW}"
    again.follow(i)
    assert run(again.measure_followed()) == [] and outline_node(obj.id) not in again.lost  # no loss "since" it was outlined
    with pytest.raises(ToolFailed) as refused:
        ask(later, tools(later), "follow_cells", {"objects": [obj.id]})
    assert "before the app restarted" in str(refused.value)


# Note 1, the conservative fix: linking checks that the two views line up where the slide's measured move puts them.
class ShiftedFinder(CellFinder):
    """The cell finder seeing the same six cells moved by `shift` px in the frame (the slide moved)."""

    def __init__(self, shift=(0, 0)):
        super().__init__()
        self.shift = shift

    async def call(self, op, timeout=0, **payload):
        from gpu_worker.worker import encode_labels
        from tests.test_population import label_map

        self.calls += 1
        labels, origin = label_map()
        origin = (origin[0] + self.shift[0], origin[1] + self.shift[1])
        return {"labels": encode_labels(labels, origin), "scores": {str(n): 0.9 - 0.05 * n for n in range(1, 7)},
                "score": "cellprob", "polygons": [], "count": 6, "ms": 900}


def overlap_trackastra(monkeypatch, seen):
    """A position-based stand-in for Trackastra 0.5.6 behind the real adapter: links each label of the first frame
    to the label of the second it overlaps most (IoU at least 0.2), its weight the IoU."""
    import importlib.machinery
    import sys
    from types import ModuleType

    import networkx as nx
    import numpy as np
    from noncommercial import trackastra_link

    class Trackastra:
        @classmethod
        def from_folder(cls, folder, device="cpu"):
            seen["folder"] = folder  # the catalog's pinned revision (catalog/models/trackastra), never its own download
            return cls()

        def track(self, stack, labels, mode="greedy"):
            seen["shape"] = stack.shape
            graph = nx.DiGraph()
            firsts = [int(v) for v in np.unique(labels[0]) if v]
            seconds = [int(v) for v in np.unique(labels[1]) if v]
            for n in firsts:
                graph.add_node(("a", n), time=0, label=n)
            for n in seconds:
                graph.add_node(("b", n), time=1, label=n)
            for n in firsts:
                mine = labels[0] == n
                ious = [(float((mine & (labels[1] == m)).sum()) / float((mine | (labels[1] == m)).sum()), m) for m in seconds]
                iou, best = max(ious, default=(0.0, None))
                if best is not None and iou >= 0.2:
                    graph.add_edge(("a", n), ("b", best), weight=round(iou, 3))
            return graph, labels

    package, model = ModuleType("trackastra"), ModuleType("trackastra.model")
    package.__spec__ = importlib.machinery.ModuleSpec("trackastra", None)
    model.Trackastra = Trackastra
    monkeypatch.setitem(sys.modules, "trackastra", package)
    monkeypatch.setitem(sys.modules, "trackastra.model", model)
    monkeypatch.setattr(trackastra_link, "_model", None)
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")  # its pinned files counted as downloaded


def moved_views(world, moved, offset, epoch=0):
    """Two views of the six cells: the second after the slide moved `moved` px (its picture and its cells), with
    `offset` the slide's measured move as the second view's offset, in `epoch`."""
    world.gpu_heavy = ShiftedFinder((0, 0))
    world.frame(0.0, 0.0)
    a, _ = cells(world)
    world.gpu_heavy = ShiftedFinder((moved, 0))
    info = world.frame(float(moved), 0.0)  # the picture moved by `moved`
    info.dx, info.epoch = float(offset), epoch  # what the motion tracker measured
    b, _ = cells(world)
    return a, b


def same_cell(a, b, x, y, moved):
    """Whether a's cell x and b's cell y are one physical cell (b's frame moved by `moved` px)."""
    ax0, ay0, ax1, ay1 = a.members[x].box
    bx0, by0, bx1, by1 = b.members[y].box
    return abs((ax0 + ax1) / 2 + moved - (bx0 + bx1) / 2) < 3 and abs((ay0 + ay1) / 2 - (by0 + by1) / 2) < 3


def population_registry(world):
    from orchestrator.brain.toolbox.population import add_population_tools

    registry = Registry()
    add_population_tools(registry, SimpleNamespace(hub=world, log=None))
    return registry


def test_two_views_that_do_not_line_up_where_the_slides_move_puts_them_are_never_linked(world, tmp_path, monkeypatch):
    """Probe P3b: the slide moved 40 px and the offsets say 120 (80 px wrong, on views both registered): once two wrong
    links were kept. Probe P2: with the right offsets every cell is linked to itself."""
    identity = keeping(world, tmp_path)
    seen = {}
    overlap_trackastra(monkeypatch, seen)
    a, b = moved_views(world, 40, 120)
    said = ask(world, population_registry(world), "query_population", {"of": a.id, "against": b.id, "link": True})
    assert (f"no links made: can't tell which cells of {a.id} and {b.id} are the same: the two views do not line up where "
            "the slide's measured move puts them (off by 80 px)") in said
    assert identity.links == [] and not seen  # the model was never asked
    assert f"{a.id} at " in said and "no cell is linked as the same in both" in said  # still compared as populations
    a, b = moved_views(world, 40, 40)
    said = ask(world, population_registry(world), "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "6 cells linked one to one" in said and len(identity.links) == 6
    assert all(same_cell(a, b, k["from"].split("/")[1], k["to"].split("/")[1], 40) for k in identity.links)


def test_two_views_a_verified_rejoin_relates_are_linked_by_its_delta(world, tmp_path, monkeypatch):
    """Probe P9 (the re-check's M7 and M31): continuity was lost and the motion tracker rejoined the place 40 px along.
    Before the rejoin it can't tell; after it, every cell is linked to itself, and each lies inside both eyepiece
    circles only by the rejoin's delta (a check that ignored it would leave cells out)."""
    identity = keeping(world, tmp_path)
    overlap_trackastra(monkeypatch, {})
    a, b = moved_views(world, 40, 0.0, epoch=3)  # epoch 3's offsets start again at 0
    a.view = {**a.view, "disc": [420.0, 270.0, 145.0]}  # each view's eyepiece circle holds the six cells, just
    b.view = {**b.view, "disc": [460.0, 270.0, 145.0]}
    registry = population_registry(world)
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "the app lost track of the slide's movement between the two views" in said and identity.links == []
    world.epoch_links = {3: (0, (40.0, 0.0))}  # motion's verified rejoin: epoch 3 is epoch 0 moved by 40 px
    world.latest_info.epoch = 0  # ...and the view is in epoch 0 now (carries stop at the epoch the view is in)
    said = ask(world, registry, "query_population", {"of": a.id, "against": b.id, "link": True})
    assert "6 cells linked one to one" in said and "left out" not in said, said
    assert all(same_cell(a, b, k["from"].split("/")[1], k["to"].split("/")[1], 40) for k in identity.links)


# The coordinator's item: cells segmented for a researcher are never "the cells" the user means unless named or shown.
def test_a_researchers_cells_stand_in_only_when_named_or_shown(world, tmp_path):
    from orchestrator.population import layer_of, segment

    identity = keeping(world, tmp_path)
    first, _ = cells(world)
    mine, _ = cells(world)
    with using(issue(world, "command")):
        theirs = run(segment(world, origin="background", made_for="Researcher 1"))  # the newest
    assert theirs.made_for == "Researcher 1" and layer_of(world).shown == mine.id
    layer_of(world).hide()
    c = theirs.live()[0].id
    with pytest.raises(ToolFailed) as refused:
        ask(world, tools(world), "follow_cells", {"members": [c]})
    assert f"{c} is one of the cells segmented for Researcher 1 ({theirs.id}), which stand in only when named" in str(refused.value)
    assert refused.value.next.startswith(f"name {theirs.id}") and identity.individuals() == []
    with pytest.raises(ToolFailed):
        ask(world, tools(world), "individuals", {"which": c})
    # Compared by default with the user's newest, never the researcher's.
    said = ask(world, population_registry(world), "query_population", {"against": first.id})
    assert said.startswith(mine.title) and f"{mine.id} at " in said and theirs.id not in said
    # Named, they stand in; once followed, its cell is found by its number.
    said = ask(world, tools(world), "follow_cells", {"members": [c], "of": theirs.id})
    assert said.startswith(f"keeping an eye on {c}")
    assert "(i1)" in ask(world, tools(world), "individuals", {"which": c})
    layer_of(world).show(theirs)  # shown, its cells stand in too
    other = theirs.live()[1].id
    assert ask(world, tools(world), "follow_cells", {"members": [other]}).startswith(f"keeping an eye on {other}")


# Note 4: what an individual's presentation says is true of every individual.
def test_an_individual_is_said_to_be_drawn_only_while_its_own_pixels_match():
    from orchestrator.brain.toolbox.showing import evidence_lines

    (line,) = evidence_lines({"evidence": [{"id": "i1", "found": True, "kind": "individual", "title": "Followed · c1"}]})
    assert "its outline is drawn on the live view only while its own pixels match where it is" in line
    assert "its outline is on the view" not in line


# --- the second re-check's SF: "where is it?" looks at every outline of it, never the first one found ---------
def test_a_live_outline_said_the_same_as_an_earlier_runs_cell_is_where_it_is(world, tmp_path, monkeypatch):
    """P8b: after a restart, c1 highlighted from its read-back view (an outline on an earlier run's view) and the user's
    live click on the same cell said to be the same one: the live outline's verified place is the answer."""
    from orchestrator import tools as otools
    from orchestrator.identity import EARLIER_VIEW
    from orchestrator.population import promote

    keeping(world, tmp_path)
    population, _ = cells(world)
    mid = population.live()[0].id
    ask(world, tools(world), "follow_cells", {"members": [mid], "of": population.id})
    later, again = restarted(world, population, tmp_path, monkeypatch)
    (i,) = again.individuals()
    run(promote(later, population, [mid], why="highlighted"))  # an outline on the earlier run's view
    info = later.latest_info
    live = run(otools.outline_at(later, info.id, 300, 200, box=(286, 186, 314, 214), source="click"))
    ask(later, tools(later), "same_one", {"a": live.id, "b": mid})
    ask(later, tools(later), "follow_cells", {"objects": [live.id]})
    fov(later)
    words = where_words(later, again, i)
    assert words != f"can't tell: {EARLIER_VIEW}" and "its own pixels matched there" in words


def test_a_refinding_outline_never_hides_another_outline_of_the_same_thing_that_is_in_place(world, tmp_path, monkeypatch):
    """P13: c1 followed (o1), the user clicks the same cell (o2) and says it is the same one; o1's own check fails,
    o2's holds: where it is comes from o2, never "can't confirm" from o1."""
    from orchestrator import tools as otools

    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    mid = population.live()[0].id
    ask(world, tools(world), "follow_cells", {"members": [mid], "of": population.id})
    (i,) = identity.individuals()
    info = world.latest_info
    live = run(otools.outline_at(world, info.id, 300, 200, box=(286, 186, 314, 214), source="click"))
    ask(world, tools(world), "same_one", {"a": live.id, "b": mid})
    first = next(n for n in sorted(identity.nodes_of(i)) if "@" in n and n.split("@")[0] != live.id)
    world.scene.update(first.split("@")[0], track="refinding", lost_since=time.time() - 2)
    fov(world)
    words = where_words(world, identity, i)
    assert "its own pixels matched there" in words and "can't confirm" not in words
    # With only the refinding one left, it says it can't confirm (the order is kept, not lost).
    world.scene.remove(live.id) if hasattr(world.scene, "remove") else world.scene.objects.pop(live.id, None)
    assert "can't confirm it's still there" in where_words(world, identity, i)


def test_an_outline_being_refound_after_the_view_left_its_epoch_is_said_as_the_loss_of_continuity(world, tmp_path):
    """The identity scenario (sim): while the view is in another epoch, a followed cell's outline is being re-found
    across epochs, not failing its own check here, so where it is can't be told and the loss is said as continuity
    lost (review SF4), never as its own pixels not matching."""
    identity = keeping(world, tmp_path)
    population, _ = cells(world)
    mid = population.live()[0].id
    ask(world, tools(world), "follow_cells", {"members": [mid], "of": population.id})
    (i,) = identity.individuals()
    outline = next(identity.outline(n) for n in sorted(identity.nodes_of(i)) if identity.outline(n) is not None)
    world.frame(0.0, 0.0, epoch=1)  # continuity lost: the view is in another epoch now
    world.scene.update(outline.id, track="refinding", lost_since=time.time())
    fov(world)
    words = where_words(world, identity, i)
    assert words.startswith("can't tell: continuity was lost at") and "can't confirm" not in words
