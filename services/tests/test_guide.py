"""Guiding the user's hands (orchestrator/guide.py): the facts the judge is given, the slide-to-picture relation learned
from the user's own moves, the code's checks on "done", and whole guidances in a closed loop (bench/guide_bench.py: a
synthetic eyepiece, a simulated person following each line, every frame through the hub's own analysis)."""
from __future__ import annotations

import asyncio
import math
from types import SimpleNamespace

import pytest

from bench import guide_bench as B
from orchestrator import guide as G


# --- the relation between the hands and the picture ---------------------------------------------------------------
def test_the_relation_is_assumed_opposite_until_two_moves_agree():
    rel = G.Relation()
    assert rel.matrix() == ((-1.0, 0.0, 0.0, -1.0), False)
    assert "assumed" in rel.words()
    # The hands moved the slide right; the picture moved left (a compound microscope).
    rel.add((1.0, 0.0), (-40.0, 1.0))
    assert rel.matrix()[1] is False  # one move is not a measurement
    rel.add((0.0, -1.0), (2.0, 35.0))  # away: the picture moved down
    (a, b, c, d), measured = rel.matrix()
    assert measured and a < -0.95 and d < -0.95
    assert rel.words().startswith("measured") and "opposite" in rel.words()
    assert rel.hand_for((30.0, 0.0)) == pytest.approx((-1.0, 0.0), abs=0.05)  # to move the picture right: slide left


def test_a_phone_turned_a_quarter_on_the_eyepiece_is_learned():
    rel = G.Relation()
    # turned: moving the slide right moves the picture up, away moves it right
    rel.add((1.0, 0.0), (0.0, -30.0))
    rel.add((0.0, -1.0), (28.0, 0.0))
    (a, b, c, d), measured = rel.matrix()
    assert measured
    hx, hy = rel.hand_for((0.0, -20.0))  # the picture must go up: the slide goes right
    assert hx == pytest.approx(1.0, abs=0.05) and abs(hy) < 0.05
    assert "turned" in rel.words()


def test_a_mirrored_picture_is_told_apart_once_two_directions_were_moved():
    rel = G.Relation()
    # mirrored left-right (x same as the slide, y opposite)
    rel.add((1.0, 0.0), (30.0, 0.0))
    rel.add((0.0, 1.0), (0.0, -30.0))
    rel.add((1.0, 0.0), (25.0, 2.0))
    (a, b, c, d), measured = rel.matrix()
    assert measured and a > 0.9 and d < -0.9
    assert rel.hand_for((0.0, 20.0)) == pytest.approx((0.0, -1.0), abs=0.05)


def test_tiny_movements_say_nothing_about_the_relation():
    rel = G.Relation()
    rel.add((1.0, 0.0), (1.0, 0.0))
    assert rel.pairs == []


# --- the model's answer, checked --------------------------------------------------------------------------------------
def test_an_answer_out_of_shape_is_not_used():
    assert G.shaped({"say": "Move the slide left a little.", "slide": {"x": "left", "y": None, "amount": "little"},
                     "focus": None, "state": "guiding"})["slide"] == {"x": "left", "amount": "little"}
    assert G.shaped({"say": "", "state": "guiding"}) is None
    assert G.shaped({"say": "go", "slide": {"x": "north"}}) is None
    assert G.shaped({"say": "go", "focus": "sideways"}) is None
    assert G.shaped({"say": "go", "state": "finished"}) is None
    assert G.shaped("left") is None
    assert G.shaped({"say": "Focus up.", "slide": {"x": None, "y": None}})["slide"] is None


def guidance(until: str = "sharp") -> G.Guidance:
    return G.Guidance("g1", "o1", "stoma", "centre it", until)


def facts_at(r: float, sharpness: float | None = None, probes: int = 1) -> dict:
    return {"where": {"known": True, "fromCentre": r}, "focus": {"sharpness": sharpness} if sharpness else {}}


def test_done_is_held_to_the_facts():
    guide = G.Guide(SimpleNamespace(latest_info=None, scene=None))
    g = guidance()
    done = {"say": "Done.", "state": "done"}
    assert "not centred" in guide._check(g, done, facts_at(0.3, 1.0))
    assert "no focus line" in guide._check(g, done, facts_at(0.05, 1.0))
    g.probes_here = 1
    assert "not sharp" in guide._check(g, done, facts_at(0.05, 0.7))
    g.lines.append(G.Said(0, "Focus up.", None, "up", "guiding", (0, 0), 0, 0.05, 1.0, then="sharpness rose 20%"))
    assert "not found yet" in guide._check(g, done, facts_at(0.05, 1.0))  # it only got sharper: the peak may be ahead
    g.lines.append(G.Said(1, "Keep going.", None, "up", "guiding", (0, 0), 0, 0.05, 1.0, then="sharpness fell 20%"))
    assert "turn back" in guide._check(g, done, facts_at(0.05, 0.9))  # past it: back first
    g.lines.append(G.Said(2, "Back a little.", None, "down", "guiding", (0, 0), 0, 0.05, 1.0, then="sharpness rose 15%"))
    assert guide._check(g, done, facts_at(0.05, 0.95)) == ""
    assert guide._check(guidance("centred"), done, facts_at(0.12)) == ""
    assert "not known" in guide._check(g, done, {"where": {"known": False}})
    assert guide._check(g, {"say": "Left.", "state": "guiding"}, facts_at(0.9)) == ""


def test_the_judge_is_told_what_centred_and_sharp_mean_and_never_asked_for_numbers():
    text = " ".join(G.prompt({"until": "centred and sharp", "where": {"known": True}}).split())
    assert "within about 0.1 of the radius" in text and "never work out a direction from where" in text
    assert "no ids, no numbers" in text
    assert "Focus is not asked" in G.prompt({"until": "centred"})


# --- whole guidances in a closed loop ---------------------------------------------------------------------------------
@pytest.mark.parametrize("case", ["right-near", "phone-turned", "centred-only"])
def test_the_rule_brings_the_target_to_the_centre_and_into_focus(case):
    c = next(x for x in B.CASES if x.name == case)
    r = asyncio.run(B.run(c, judge="rule", seed=1, seconds=60))
    assert r["event"] == "done", r["said"]
    assert r["centred"] and (c.until != "sharp" or r["sharp"]), r
    assert r["lines"] <= 14, r["said"]


def test_a_model_answer_that_says_done_too_early_is_not_said_and_the_model_is_told_why():
    import json

    asked: list[dict] = []

    async def model(prompt: str):
        """A stand-in model: "done" at once (too early), then toward the centre by the facts it is given."""
        facts = json.loads(prompt.split("Facts:\n", 1)[1].split("\n\nReply with JSON", 1)[0])
        asked.append(facts)
        where = facts["where"]
        if len(asked) == 1 or where.get("fromCentre", 1) <= G.CENTRED_SAID:
            return {"say": "Done.", "state": "done"}
        to = facts["moveSlide"]
        return {"say": "Move it.", "slide": {"x": to.get("x"), "y": to.get("y"), "amount": G.amount_for(to["radii"])},
                "state": "guiding"}

    r = asyncio.run(B.run(next(c for c in B.CASES if c.name == "centred-only"), judge="model", ask_model=model,
                          seed=1, seconds=60, realtime=False))
    assert r["said"][0] == "Move it."  # the first answer, a refused "done", was never said
    assert asked[1]["doneRefused"].startswith("it is 0.") and asked[1]["why_now"] == G.REASONS["refused"]
    assert r["event"] == "done" and r["centred"]
    assert r["overrides"] >= 1


def test_a_model_that_cannot_answer_leaves_the_rule_speaking_logged_as_an_override():
    async def broken(prompt: str):
        raise RuntimeError("no key")

    r = asyncio.run(B.run(next(c for c in B.CASES if c.name == "centred-only"), judge="model", ask_model=broken,
                          seed=1, seconds=60, realtime=False))
    assert r["event"] == "done" and r["centred"]
    reflexes = sum(1 for said in r["said"] if said == "Stop.")  # the app's own stop is no model's line
    assert r["overrides"] == r["lines"] - reflexes  # every other line was the rule's, each logged


# --- the tool's first line, said once -----------------------------------------------------------------------------------
def test_the_first_line_is_held_for_the_tools_answer_and_not_said_again():
    async def go():
        said = []
        obj = SimpleNamespace(id="o1", center=(700.0, 300.0), anchor_offset=(0.0, 0.0), anchor_epoch=1, label="stoma")
        hub = SimpleNamespace(scene=SimpleNamespace(objects={"o1": obj}), latest_info=None)
        info = SimpleNamespace(id=1, width=1280, height=720, dx=0.0, dy=0.0, epoch=1, tracked=True, steady=True,
                               fov=SimpleNamespace(cx=640.0, cy=360.0, radius=300.0), wall_at=0.0, stats=(50.0, 100.0, 10.0, 0.0))
        hub.latest_info = info
        guide = G.Guide(hub, judge=lambda: "rule", say=lambda g, line: said.append(line))
        g = guide.start("o1", name="stoma", asked="centre it", hold_first=True)
        first = await guide.first_line(g, 1.0)
        guide.stop()
        return first, said, g

    first, said, g = asyncio.run(go())
    assert first is not None and first["say"].startswith("Move the slide") and first["n"] == 1
    # the first line was the tool's to say: filed as told, not said again
    assert [(line["event"], bool(line.get("told"))) for line in said] == [("line", True), ("stopped", False)]
    assert g.event == "stopped"


def test_a_new_guidance_ends_the_last_and_stopping_says_so():
    async def go():
        said = []
        hub = SimpleNamespace(scene=SimpleNamespace(objects={}), latest_info=None)
        guide = G.Guide(hub, judge=lambda: "rule", say=lambda g, line: said.append(line))
        a = guide.start("o1", name="stoma", asked="centre it")
        b = guide.start("o2", name="cell", asked="centre that cell")
        await asyncio.sleep(0)
        stopped = guide.stop()
        nothing = guide.stop()
        return a, b, stopped, nothing, said

    a, b, stopped, nothing, said = asyncio.run(go())
    assert a.event == "replaced" and b.event == "stopped" and stopped is b and nothing is None
    assert [line["event"] for line in said if line["event"] != "line"] == ["replaced", "stopped"]


def test_the_frame_path_reads_numbers_only_and_is_cheap():
    import time

    obj = SimpleNamespace(id="o1", center=(700.0, 300.0), anchor_offset=(0.0, 0.0), anchor_epoch=1, label="stoma")
    hub = SimpleNamespace(scene=SimpleNamespace(objects={"o1": obj}), latest_info=None)

    async def go():
        guide = G.Guide(hub, judge=lambda: "rule")
        g = guide.start("o1", name="stoma", asked="centre it")
        spent = []
        for i in range(600):
            info = SimpleNamespace(id=i, width=1280, height=720, dx=float(i % 40), dy=0.0, epoch=1, tracked=True,
                                   steady=i % 40 == 0, fov=SimpleNamespace(cx=640.0, cy=360.0, radius=300.0),
                                   wall_at=0.0, stats=(50.0 + i % 7, 100.0, 10.0, 0.0))
            t0 = time.perf_counter()
            guide.frame(info)
            spent.append(time.perf_counter() - t0)
        guide.stop()
        return sorted(spent)

    spent = asyncio.run(go())
    assert spent[len(spent) // 2] < 2e-4  # a frame is 33 ms; reading it costs well under a millisecond


def test_the_place_is_said_unknown_across_a_lost_epoch():
    obj = SimpleNamespace(id="o1", center=(700.0, 300.0), anchor_offset=(0.0, 0.0), anchor_epoch=1, label="stoma")
    hub = SimpleNamespace(scene=SimpleNamespace(objects={"o1": obj}), latest_info=None)
    guide = G.Guide(hub)
    g = guidance()
    info = SimpleNamespace(id=1, width=1280, height=720, dx=0.0, dy=0.0, epoch=2, tracked=True, steady=True,
                           fov=SimpleNamespace(cx=640.0, cy=360.0, radius=300.0), wall_at=0.0, stats=())
    reading = guide._read(g, info)
    assert not reading.known
    info.epoch = 1
    reading = guide._read(g, info)
    assert reading.known and math.isclose(reading.r, math.hypot(60, 60) / 300)


def test_a_replaced_guidance_says_nothing_more():
    obj = SimpleNamespace(id="o1", center=(800.0, 300.0), anchor_offset=(0.0, 0.0), anchor_epoch=1, label="stoma")
    obj2 = SimpleNamespace(id="o2", center=(500.0, 400.0), anchor_offset=(0.0, 0.0), anchor_epoch=1, label="cell")
    hub = SimpleNamespace(scene=SimpleNamespace(objects={"o1": obj, "o2": obj2}), latest_info=None)

    async def go():
        said = []
        guide = G.Guide(hub, judge=lambda: "rule", say=lambda g, line: said.append((g.id, line["event"])))
        guide.start("o1", name="stoma", asked="centre it")
        await asyncio.sleep(0.05)
        g2 = guide.start("o2", name="cell", asked="centre that cell")
        for i in range(400):  # frames with nothing changing: quiet moments come for the live one only
            info = SimpleNamespace(id=i, width=1280, height=720, dx=0.0, dy=0.0, epoch=1, tracked=True, steady=True,
                                   fov=SimpleNamespace(cx=640.0, cy=360.0, radius=300.0), wall_at=0.0,
                                   stats=(50.0, 100.0, 10.0, 0.0))
            guide.clock = lambda i=i: 1000.0 + i * 0.05
            guide.wall = guide.clock
            guide.frame(info)
            await asyncio.sleep(0)
        guide.stop()
        await asyncio.sleep(0.05)
        return said, g2

    said, g2 = asyncio.run(go())
    after = said.index(("g1", "replaced"))
    assert all(gid == "g2" for gid, _ in said[after + 1:]), said


def test_a_lost_target_is_said_once_per_loss():
    obj = SimpleNamespace(id="o1", center=(800.0, 300.0), anchor_offset=(0.0, 0.0), anchor_epoch=1, label="stoma")
    hub = SimpleNamespace(scene=SimpleNamespace(objects={"o1": obj}), latest_info=None)

    async def go():
        said = []
        guide = G.Guide(hub, judge=lambda: "rule", say=lambda g, line: said.append(line["say"]))
        t = [1000.0]
        guide.clock = guide.wall = lambda: t[0]
        g = guide.start("o1", name="stoma", asked="centre it")
        for i in range(200):
            t[0] += 0.05
            epoch = 1 if i < 20 else 2  # the view lost track of the slide after a second
            info = SimpleNamespace(id=i, width=1280, height=720, dx=0.0, dy=0.0, epoch=epoch, tracked=True, steady=True,
                                   fov=SimpleNamespace(cx=640.0, cy=360.0, radius=300.0), wall_at=t[0],
                                   stats=(50.0, 100.0, 10.0, 0.0))
            guide.frame(info)
            for _ in range(5):
                await asyncio.sleep(0)
        guide.stop()
        return said

    said = asyncio.run(go())
    assert sum(1 for s in said if "lost" in s.lower()) == 1, said


def test_a_stalled_model_never_holds_the_guidance(monkeypatch):
    monkeypatch.setattr(G, "JUDGE_S", 0.05)

    async def stalled(prompt: str):
        await asyncio.sleep(30)

    r = asyncio.run(B.run(next(c for c in B.CASES if c.name == "centred-only"), judge="model", ask_model=stalled,
                          seed=1, seconds=60, realtime=False))
    assert r["event"] == "done" and r["centred"] and r["overrides"] >= 1

