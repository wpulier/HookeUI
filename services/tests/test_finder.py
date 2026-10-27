"""The Finder (orchestrator/finder): what a request means becomes outlines, inside an outline when
asked, never at the cost of the outline it searched in."""
import asyncio
from types import SimpleNamespace

import numpy as np
import pytest

from orchestrator import tools
from orchestrator.agent import Agent, _same_thing
from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.coords import Crop
from orchestrator.finder import Finder, FindRef, describe_found
from orchestrator.scene import SceneObject
from tests.fakes import Hub


def outline(hub, label, box, source="ai:gemini"):
    x0, y0, x1, y1 = box
    return hub.scene.add(SceneObject(id=hub.scene.new_id(), kind="highlight", polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                                     anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source=source, label=label))


class Agent_:
    """The finder's eyes: boxes from a script, the view a blank picture."""

    def __init__(self, hub, boxes, note=""):
        self.hub, self.boxes, self.note, self.calls = hub, boxes, note, []

    def snapshot(self, frame_id=None):
        return self.hub.latest_info, np.zeros((720, 1280, 3), np.uint8), Crop.full(1280, 720)

    async def look_in(self, prompt, view, region=None, option=None):
        self.calls.append((prompt, region))
        return {"targets": [{"box": b, "label": l, "confidence": c} for l, b, c in self.boxes], "note": self.note}, \
            {"option": "gemini-3.8-flash"}


@pytest.fixture
def outlines(monkeypatch):
    async def fake_outline(hub, frame_id, x, y, box=None, **kw):
        made = outline(hub, kw["label"], box, source=kw["source"])
        made.naming = kw.get("naming")  # as outline_at passes it to add_outline
        return made

    monkeypatch.setattr(tools, "outline_at", fake_outline)


def ctx(question="find it"):
    return ToolContext("chat", 1, 7, question)


def test_inside_an_outline_it_looks_at_a_close_up_and_keeps_only_what_is_in_it(outlines):
    hub = Hub()
    hub.latest_info.width, hub.latest_info.height = 1280, 720
    cell = outline(hub, "guard cell", (400, 250, 560, 400))
    agent = Agent_(hub, [("nucleus", (460, 300, 500, 340), 0.8), ("nucleus", (800, 300, 840, 340), 0.9)])
    found = asyncio.run(Finder(hub, agent).find(FindRef("nucleus", inside=cell.id, quantity="all"), ctx()))
    assert [o.label for o in found.objects] == ["nucleus"] and found.container is cell
    # The look's own name, kept as the look's (known.py); a request's word it did not give is never credited to it.
    assert found.objects[0].naming["by"] == "look" and found.objects[0].naming["label"] == "nucleus"
    agent2 = Agent_(hub, [("", (460, 300, 500, 340), 0.8)])
    hub.scene.remove(found.objects[0].id)
    asked = asyncio.run(Finder(hub, agent2).find(FindRef("nucleus", inside=cell.id, quantity="all"), ctx()))
    assert asked.objects[0].naming["by"] == "asked" and asked.objects[0].label == "nucleus"
    prompt, region = agent.calls[0]
    assert "inside the outlined guard cell" in prompt and "Image 2 is" in prompt
    assert region[0] < 400 and region[2] > 560  # the close-up shows a little around the cell
    assert describe_found(found, FindRef("nucleus", inside=cell.id)) == "outlined the nucleus inside the guard cell"


def test_what_is_already_outlined_is_reused_and_nothing_found_says_why(outlines):
    hub = Hub()
    stoma = outline(hub, "stoma", (100, 100, 140, 140))
    agent = Agent_(hub, [], note="no paramecium in this leaf")
    found = asyncio.run(Finder(hub, agent).find(FindRef("stoma"), ctx()))
    assert found.objects == [stoma] and found.how == "board" and not agent.calls
    none = asyncio.run(Finder(hub, agent).find(FindRef("paramecium"), ctx()))
    with pytest.raises(ToolFailed, match="couldn't find paramecium: no paramecium in this leaf"):
        describe_found(none, FindRef("paramecium"))


def test_one_means_the_most_confident(outlines):
    hub = Hub()
    agent = Agent_(hub, [("red cell", (10, 10, 40, 40), 0.4), ("red cell", (100, 10, 130, 40), 0.9)])
    found = asyncio.run(Finder(hub, agent).find(FindRef("red cell"), ctx()))
    assert len(found.objects) == 1 and found.objects[0].bbox[0] == 100


def test_look_in_maps_the_close_ups_boxes_back_to_the_frame():
    hub = Hub()
    seen = []

    async def ask(images, prompt, max_output_tokens=0):
        seen.append(len(images))
        return SimpleNamespace(data={"targets": [{"box_2d": [0, 0, 500, 500], "label": "x"}]}, model="m", latency_ms=1.0)

    agent = Agent(hub)
    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    view = (hub.latest_info, np.zeros((720, 1280, 3), np.uint8), Crop.full(1280, 720))
    data, _ = asyncio.run(agent.look_in("find", view, (200, 100, 400, 300)))
    assert seen == [2] and data["targets"][0]["box"] == pytest.approx((200, 100, 300, 200))


def test_a_small_box_inside_a_big_one_is_a_part_not_the_same_thing():
    cell, nucleus = (400, 250, 560, 400), (460, 300, 500, 340)
    assert not _same_thing(cell, nucleus)
    assert _same_thing((400, 250, 560, 400), (410, 255, 555, 395))


def test_every_thing_like_an_outlined_one_comes_from_sam_matches(outlines):
    hub = Hub()
    hub.infos = {7: hub.latest_info}
    example = outline(hub, "red cell", (100, 100, 140, 140), source="click")
    calls = []

    async def matches(hub_, frame_id, box, text=""):
        calls.append(box)
        square = lambda x: [[[x, 100], [x + 40, 100], [x + 40, 140], [x, 140]]]
        return [{"polygons": square(100), "score": 0.95}, {"polygons": square(300), "score": 0.9},
                {"polygons": square(500), "score": 0.8}]

    namings = []

    async def add(hub_, frame_id, polygons, **kw):
        namings.append(kw.get("naming"))
        xs = [p[0] for p in polygons[0]]
        return outline(hub_, kw["label"], (min(xs), 100, max(xs), 140), source=kw["source"])

    import orchestrator.tools as tools_mod
    tools_mod_add, tools_mod.add_outline = tools_mod.add_outline, add
    try:
        found = asyncio.run(Finder(hub, Agent_(hub, []), segment_all=matches).find(
            FindRef("red cell", quantity="all", like=example.id), ctx()))
    finally:
        tools_mod.add_outline = tools_mod_add
    assert calls == [(100, 100, 140, 140)] and found.how == "sam3.1-all"
    assert found.objects[0] is example and len(found.objects) == 3  # the example kept, two more
    assert {o.label for o in found.objects} == {"red cell"}
    # How each was named, for "how do you know?" (known.py): a match for the example, its name the example's.
    assert [(n["by"], n["example"], n["label"]) for n in namings] == [("match", example.id, "red cell")] * 2


def test_without_sam_matches_the_vision_model_finds_them(outlines):
    hub = Hub()
    example = outline(hub, "red cell", (100, 100, 140, 140), source="click")

    async def unavailable(*a, **k):
        from orchestrator.operations import Unavailable

        raise Unavailable("SAM 3.1, every match can't run here (needs FAL_KEY)", fallback="vision-boxes")

    agent = Agent_(hub, [("red cell", (300, 100, 340, 140), 0.9)])
    found = asyncio.run(Finder(hub, agent, segment_all=unavailable).find(
        FindRef("red cell", quantity="all", like=example.id), ctx()))
    assert found.how == "vlm-box>edgetam" and found.errors and len(found.objects) == 1


def test_a_failure_of_sam_matches_is_said_never_replaced_by_the_vision_model(outlines):
    """Scaffolding review, M1.2 item 1: a fal error (or any failure once it ran) is a failure, said as
    it is; only Unavailable with the declared fallback hands over to the vision model's boxes."""
    hub = Hub()
    example = outline(hub, "red cell", (100, 100, 140, 140), source="click")

    async def connection_error(*a, **k):
        raise tools.ToolError("ConnectionError: fal unreachable")

    agent = Agent_(hub, [("red cell", (300, 100, 340, 140), 0.9)])
    found = asyncio.run(Finder(hub, agent, segment_all=connection_error).find(
        FindRef("red cell", quantity="all", like=example.id), ctx()))
    assert not agent.calls and found.objects == [] and "fal unreachable" in found.errors[0]


def test_the_vision_model_takes_over_only_when_sam_cannot_run(outlines):
    """What the capability list says of "Find all like this" (capabilities.json find-like fallbacks):
    the vision model and the outline model take over only when SAM 3.1 can't run here (a ToolError:
    no FAL_KEY, not in this edition). SAM finding nothing is said; an error from fal is a failure."""
    hub = Hub()
    example = outline(hub, "red cell", (100, 100, 140, 140), source="click")
    agent = Agent_(hub, [("red cell", (300, 100, 340, 140), 0.9)])

    async def none(*a, **k):
        return []

    found = asyncio.run(Finder(hub, agent, segment_all=none).find(FindRef("red cell", quantity="all", like=example.id), ctx()))
    assert found.how == "sam3.1-all" and not found.objects and "nothing else like" in found.note

    async def broken(*a, **k):
        raise RuntimeError("fal: 502")

    with pytest.raises(RuntimeError):
        asyncio.run(Finder(hub, agent, segment_all=broken).find(FindRef("red cell", quantity="all", like=example.id), ctx()))


@pytest.mark.parametrize(('quantity', 'expected_lefts'), [
    ('all', [160, 280]), ('one', [160]), ('requested', [160, 280]),
])
def test_by_cells_outlines_what_the_model_picks_from_the_cell_finders_cells(monkeypatch, quantity, expected_lefts):
    """find by="cells": the cell finder outlines every cell, the model lists which are the ones asked
    for; only those are outlined (blood smear bench: every red cell 0.90 found against 0.60 by looking)."""
    import cv2

    from orchestrator.finder import picked

    hub = Hub()
    jpeg = cv2.imencode(".jpg", np.full((720, 1280, 3), 200, np.uint8))[1].tobytes()
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg), is_fresh=lambda: True)
    cells = [[[[100 + 60 * i, 100], [140 + 60 * i, 100], [140 + 60 * i, 140], [100 + 60 * i, 140]]][0] for i in range(5)]

    async def segment(hub_, **kw):
        # The cell finder's population (evidence): its members' own masks, from these polygons.
        from orchestrator.capture import Capture
        from orchestrator.evidence import Population, evidence_of
        from orchestrator.population import members_from, view_facts

        store = evidence_of(hub_)
        capture = Capture(7, jpeg, SimpleNamespace(id=7, fov=None, width=1280, height=720, epoch=0, dx=0.0, dy=0.0))
        made = Population(store.new_id(), "cells", "cellpose-sam", {"operation": "cells", "implementation": "cellpose-sam"},
                          7, view_facts(capture), None, None, members_from({"polygons": cells}, store), capture=capture)
        return store.add(made)

    added, namings = [], []

    async def add_outline(hub_, frame_id, polygons, **kw):
        added.append((polygons, kw["label"], kw["source"]))
        namings.append(kw.get("naming"))
        xs = [p[0] for poly in polygons for p in poly]
        ys = [p[1] for poly in polygons for p in poly]
        return outline(hub_, kw["label"], (min(xs), min(ys), max(xs), max(ys)), source=kw["source"])

    import orchestrator.population as population_module

    monkeypatch.setattr(population_module, "segment", segment)
    monkeypatch.setattr(tools, "add_outline", add_outline)
    asked = {}

    async def ask(images, prompt, max_output_tokens=0):
        asked.update(n=len(images), prompt=prompt)
        return SimpleNamespace(data={"numbers": [2, 4, 9]})

    agent = SimpleNamespace(vision=lambda option: SimpleNamespace(ask=ask))
    found = asyncio.run(Finder(hub, agent).find(FindRef("red blood cell", quantity=quantity, by="cells"),
                                                 ctx("highlight the red blood cells")))
    # Members 2 and 4 promoted from their own masks; 9: no such member, dropped.
    lefts = [min(p[0] for poly in polys for p in poly) for polys, _, _ in added]
    assert lefts == expected_lefts
    assert all(label == "red blood cell" and source.startswith("ai:measured:e") for _, label, source in added)
    # Promoted: its name came from the request, kept for "how do you know?" (known.py).
    assert [(n["by"], n["label"]) for n in namings] == [("request", "red blood cell")] * len(expected_lefts) and namings[0]["member"]
    assert asked["n"] == 2 and "red blood cell" in asked["prompt"] and f"5 found, {len(expected_lefts)} picked" in found.how
    if quantity == "requested":
        assert "original words above" in asked["prompt"]
    assert picked({"numbers": ["1", -3, "x"]}, cells) == [cells[0]]


def test_every_thing_the_words_name_comes_from_sam_matches_for_the_words(outlines):
    """M3: SAM 3.1 text concepts. by="concept" asks SAM 3.1 for every match of the words themselves (no
    example), labels them with the words, and says when it found nothing (on these slides, often)."""
    hub = Hub()
    hub.infos = {7: hub.latest_info}
    calls = []

    async def matches(hub_, frame_id, box, text=""):
        calls.append((box, text))
        square = lambda x: [[[x, 100], [x + 40, 100], [x + 40, 140], [x, 140]]]
        return [{"polygons": square(300), "score": 0.9}, {"polygons": square(500), "score": 0.8}]

    async def add(hub_, frame_id, polygons, **kw):
        xs = [p[0] for p in polygons[0]]
        namings.append(kw["naming"])
        return outline(hub_, kw["label"], (min(xs), 100, max(xs), 140), source=kw["source"])

    namings = []
    import orchestrator.tools as tools_mod
    tools_mod_add, tools_mod.add_outline = tools_mod.add_outline, add
    try:
        found = asyncio.run(Finder(hub, Agent_(hub, []), segment_all=matches).find(
            FindRef("guard cell", quantity="all", by="concept"), ctx()))
    finally:
        tools_mod.add_outline = tools_mod_add
    assert calls == [(None, "guard cell")] and found.how == "sam3.1-concept"
    assert len(found.objects) == 2 and {o.label for o in found.objects} == {"guard cell"}
    assert {o.source for o in found.objects} == {"ai:sam3.1-concept"}
    assert [(n["by"], n["words"], n["score"]) for n in namings] == [("concept", "guard cell", 0.9), ("concept", "guard cell", 0.8)]

    async def none(*a, **k):
        return []

    nothing = asyncio.run(Finder(hub, Agent_(hub, []), segment_all=none).find(FindRef("stoma", quantity="all", by="concept"), ctx()))
    assert not nothing.objects and "an example works better" in nothing.note


def test_words_sam_cannot_take_are_refused_never_replaced_by_the_vision_models_boxes(outlines):
    """Review B2: by="concept" is an explicit choice (ADR 0011): raw, its reason and what else can be done said."""
    from orchestrator.operations import Unavailable

    hub = Hub()

    async def unavailable(*a, **k):
        raise Unavailable("SAM 3.1, every match can't run here (needs FAL_KEY)", fallback="vision-boxes")

    agent = Agent_(hub, [("guard cell", (300, 100, 340, 140), 0.9)])
    with pytest.raises(tools.ToolError) as refused:
        asyncio.run(Finder(hub, agent, segment_all=unavailable).find(FindRef("guard cell", quantity="all", by="concept"), ctx()))
    assert "needs FAL_KEY" in str(refused.value) and "outline one and ask for every one like it" in refused.value.next
    assert not agent.calls  # the vision model was never asked in its place


def test_a_match_for_words_is_not_sure_and_carries_no_confidence(outlines):
    """Review B3: the studio said "Probably" and the card "91% likely" from SAM's match score."""
    hub = Hub()
    seen = []

    async def two(*a, **k):
        return [{"polygons": [[[300, 100], [340, 100], [340, 140], [300, 140]]], "score": 0.91}]

    async def add(hub_, frame_id, polygons, **kw):
        seen.append(kw)
        return outline(hub_, kw["label"], (300, 100, 340, 140), source=kw["source"])

    import orchestrator.tools as tools_mod
    real, tools_mod.add_outline = tools_mod.add_outline, add
    try:
        asyncio.run(Finder(hub, Agent_(hub, []), segment_all=two).find(FindRef("all the guard cells", quantity="all", by="concept"), ctx()))
    finally:
        tools_mod.add_outline = real
    (kw,) = seen
    assert kw["status"] == "unsure" and kw["confidence"] is None and kw["naming"]["score"] == 0.91
    assert kw["label"] == "guard cells"  # the thing's name, not the whole request (review S4)


def test_the_find_tool_takes_words_and_its_matches_are_reached_by_at_n(outlines, monkeypatch):
    """Review S1 and S2: through the tool (by="concept" kept, every one), and "@1" names its matches."""
    from types import SimpleNamespace

    from orchestrator.brain.registry import Registry, step_output
    from orchestrator.brain.toolbox.finding import add_finding_tools

    hub = Hub()
    asked = {}

    async def two(hub_, frame_id, box, text=""):
        asked["text"] = text
        square = lambda x: [[[x, 100], [x + 40, 100], [x + 40, 140], [x, 140]]]
        return [{"polygons": square(300), "score": 0.9, "population": "e7", "member": "c1"}]

    async def add(hub_, frame_id, polygons, **kw):
        return outline(hub_, kw["label"], (300, 100, 340, 140), source=kw["source"])

    import orchestrator.tools as tools_mod
    monkeypatch.setattr(tools_mod, "add_outline", add)
    registry = Registry()
    add_finding_tools(registry, SimpleNamespace(hub=hub, agent=Agent_(hub, []), board=None, segment_all=two, log=None))
    context = ctx()
    context.step = 1
    context.steps.tools.update({1: "find", 2: "count"})
    run_find = registry.tools["find"].handler(context, {"what": "guard cell", "by": "concept"})
    said = asyncio.run(run_find)
    assert asked["text"] == "guard cell" and "guard cell" in said
    later = SimpleNamespace(**{**context.__dict__, "step": 2})
    assert step_output(later, "@1") == "e7"


def test_matches_for_words_are_titled_by_them_never_the_cells_and_never_kept_as_names(monkeypatch):
    """Review S3 and S5."""
    from orchestrator.known import concept_record
    from orchestrator.population import layer_of, resolve
    from orchestrator.scene import SceneObject
    from orchestrator.seen import SeenToday
    from tests.test_population import CellFinder, cells
    from tests.test_apply import World

    monkeypatch.setenv("MICROMIND_EDITION", "research")
    world = World()
    world.settings.cells = "cellpose-sam"
    world.gpu_heavy = CellFinder()
    world.sent = []
    world.broadcast = world.sent.append
    real, _ = cells(world)
    words, _ = cells(world)
    words.operation = "find-like"
    words.producer = {**(words.producer or {}), "configuration": {"text": "dust speck", "box": None}}
    layer_of(world).hide()
    assert words.title.startswith('Matches for "dust speck" · ')
    assert resolve(world, None) is real and resolve(world, words.id) is words
    obj = SceneObject("o9", "highlight", [[[1, 1], [9, 1], [9, 9]]], 1, 0, (0.0, 0.0), "ai:sam3.1-concept", label="dust speck",
                      naming=concept_record(label="dust speck", words="dust speck", score=0.7))
    assert not SeenToday.eligible(obj)


def test_words_found_but_not_shown_are_never_said_found_nothing(outlines):
    """Re-check B1: SAM 3.1 found two, but neither could be shown (a newer request replaced the app's outlines):
    said as that, never "found nothing" and never "couldn't find"."""
    from orchestrator.finder import describe_found

    hub = Hub()

    async def two(*a, **k):
        square = lambda x: [[[x, 100], [x + 40, 100], [x + 40, 140], [x, 140]]]
        return [{"polygons": square(300), "score": 0.9}, {"polygons": square(500), "score": 0.8}]

    async def refused(*a, **k):
        raise tools.ToolError("not shown: superseded by a newer request", next=None)

    import orchestrator.tools as tools_mod
    real, tools_mod.add_outline = tools_mod.add_outline, refused
    try:
        ref = FindRef("guard cell", quantity="all", by="concept")
        found = asyncio.run(Finder(hub, Agent_(hub, []), segment_all=two).find(ref, ctx()))
    finally:
        tools_mod.add_outline = real
    assert found.note == "SAM 3.1 found 2, but they could not be shown: superseded by a newer request"
    with pytest.raises(ToolFailed) as said:
        describe_found(found, ref)
    words = str(said.value)
    assert words.startswith("SAM 3.1 found 2, but they could not be shown") and "found nothing" not in words
    assert "couldn't find" not in words and said.value.next is None


def test_words_for_sam_are_the_things_name_and_how_it_knows_says_those_words(outlines):
    """Re-check S4 and the new item: "all of the stomata" is "stomata"; the record names the words SAM was given."""
    from orchestrator.finder import concept_words

    assert concept_words("all of the stomata") == "stomata"
    assert concept_words("every one of the guard cells") == "guard cells"
    assert concept_words("both of those nuclei") == "nuclei"
    hub = Hub()
    seen = []

    async def one(hub_, frame_id, box, text=""):
        seen.append(text)
        x = 300 + 200 * (len(seen) // 2)  # each find a thing of its own (the same one would be reused)
        return [{"polygons": [[[x, 100], [x + 40, 100], [x + 40, 140], [x, 140]]], "score": 0.9}]

    async def add(hub_, frame_id, polygons, **kw):
        seen.append(kw)
        xs = [p[0] for p in polygons[0]]
        return outline(hub_, kw["label"], (min(xs), 100, max(xs), 140), source=kw["source"])

    import orchestrator.tools as tools_mod
    real, tools_mod.add_outline = tools_mod.add_outline, add
    try:
        long = "all the very small pale granular inclusion bodies near the wall"
        asyncio.run(Finder(hub, Agent_(hub, []), segment_all=one).find(FindRef("all the guard cells please", quantity="all", by="concept"), ctx()))
        asyncio.run(Finder(hub, Agent_(hub, []), segment_all=one).find(FindRef(long, quantity="all", by="concept"), ctx()))
    finally:
        tools_mod.add_outline = real
    text, kw, text2, kw2 = seen
    assert text == "guard cells please" and kw["naming"]["words"] == text  # what SAM got is what is said
    assert len(kw2["label"]) <= 40 and kw2["naming"]["words"] == text2 and len(text2.split()) <= 6


def test_words_always_ask_sam_for_every_match_even_with_one_outlined_by_that_name(outlines, monkeypatch):
    """Re-check S1: by="concept" is every one (the tool forces it), so an outline already named so is not reused."""
    from types import SimpleNamespace

    from orchestrator.brain.registry import Registry
    from orchestrator.brain.toolbox.finding import add_finding_tools

    hub = Hub()
    outline(hub, "guard cell", (600, 300, 640, 340))
    asked = []

    async def one(hub_, frame_id, box, text=""):
        asked.append(text)
        return [{"polygons": [[[300, 100], [340, 100], [340, 140], [300, 140]]], "score": 0.9}]

    async def add(hub_, frame_id, polygons, **kw):
        return outline(hub_, kw["label"], (300, 100, 340, 140), source=kw["source"])

    import orchestrator.tools as tools_mod
    monkeypatch.setattr(tools_mod, "add_outline", add)
    registry = Registry()
    add_finding_tools(registry, SimpleNamespace(hub=hub, agent=Agent_(hub, []), board=None, segment_all=one, log=None))
    asyncio.run(registry.tools["find"].handler(ctx(), {"what": "guard cell", "by": "concept"}))
    assert asked == ["guard cell"]


def test_words_to_sam_have_no_fallback_in_the_capability_list():
    """Re-check: the list says fallbacks only as the code does them; by="concept" is raw (review B2)."""
    import json

    from orchestrator.paths import REPO

    caps = {c["id"]: c for c in json.loads((REPO / "contract" / "capabilities.json").read_text())["capabilities"]}
    assert not caps["find-concept"].get("fallbacks") and caps["find-like"].get("fallbacks")


def test_found_but_not_shown_carries_what_to_do_and_says_why_once(outlines):
    """M3.1's third re-check: the failure's own next step is said; "not shown" is said once; matches with no outline
    are said as that."""
    hub = Hub()

    async def two(*a, **k):
        square = lambda x: [[[x, 100], [x + 40, 100], [x + 40, 140], [x, 140]]]
        return [{"polygons": square(300), "score": 0.9}, {"polygons": square(500), "score": 0.8}]

    async def moved(*a, **k):
        raise tools.ToolError("not shown: the slide moved", next="hold the slide still, then ask again")

    import orchestrator.tools as tools_mod
    real, tools_mod.add_outline = tools_mod.add_outline, moved
    try:
        ref = FindRef("guard cell", quantity="all", by="concept")
        found = asyncio.run(Finder(hub, Agent_(hub, []), segment_all=two).find(ref, ctx()))
    finally:
        tools_mod.add_outline = real
    with pytest.raises(ToolFailed) as said:
        describe_found(found, ref)
    assert str(said.value) == "SAM 3.1 found 2, but they could not be shown: the slide moved (guard cell)"
    assert said.value.next == "hold the slide still, then ask again"

    async def bare(*a, **k):
        return [{"polygons": [], "score": 0.9}]

    found = asyncio.run(Finder(hub, Agent_(hub, []), segment_all=bare).find(FindRef("bacteria", quantity="all", by="concept"), ctx()))
    assert found.note == "SAM 3.1 found 1, but none came with an outline to show"


def test_words_that_change_what_is_meant_are_kept():
    from orchestrator.finder import concept_words

    assert concept_words("the most mature cells") == "the most mature cells"
    assert concept_words("every other cell") == "every other cell"
    assert concept_words("A cells") == "A cells"
    assert concept_words("both of those") == "both of those"
    assert concept_words("both of those cells") == "cells" and concept_words("a guard cell") == "guard cell"


def test_matches_already_outlined_still_name_their_population_for_at_n(outlines):
    """M3.1's third re-check: "@n" is recorded on the path where every match was already outlined."""
    from orchestrator.brain.registry import step_output

    hub = Hub()
    outline(hub, "guard cell", (300, 100, 340, 140))

    async def same(hub_, frame_id, box, text=""):
        return [{"polygons": [[[300, 100], [340, 100], [340, 140], [300, 140]]], "score": 0.9, "population": "e4", "member": "c1"}]

    context = ctx()
    context.step = 1
    context.steps.tools.update({1: "find", 2: "count"})
    found = asyncio.run(Finder(hub, Agent_(hub, []), segment_all=same).find(FindRef("guard cell", quantity="all", by="concept"), context))
    assert found.objects and found.objects[0].label == "guard cell"
    later = SimpleNamespace(**{**context.__dict__, "step": 2})
    assert step_output(later, "@1") == "e4"
