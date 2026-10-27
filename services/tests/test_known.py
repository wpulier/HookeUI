"""How do you know? (orchestrator/known.py; the partner §1a Understand, §1f essential provenance):
naming a click keeps how it was named on the outline (the model, the view, what it noted and
weighed, what it was told besides the pictures), renames keep who changed it, and
resources(id="o1") reads it all with what outlined it, how sure, where it is and its size. None of
it rests on the interaction log, and an unvoted blind-test arm never says what made it."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np

from orchestrator.agent import Agent
from orchestrator.resources import Index
from orchestrator.scene import SceneObject
from tests.fakes import Hub


def named(tmp_path, data=None, log_off=True):
    hub = Hub()
    image = np.full((720, 1280, 3), 200, np.uint8)
    _, jpeg = cv2.imencode(".jpg", image)
    hub.infos = {7: hub.latest_info}
    hub.source = SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=jpeg.tobytes()) if fid == 7 else None, is_fresh=lambda: True)
    obj = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[600, 300], [660, 300], [660, 360], [600, 360]]],
                                    anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="click",
                                    made_by={"operation": "outline", "implementation": "edgetam", "execution": "x1"}))
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    reply = data or {"observations": ["multi-lobed nucleus", "pale granular cytoplasm"], "label": "neutrophil",
                     "alternatives": [{"name": "eosinophil", "why": "granules"}], "answer": "A white blood cell.",
                     "confidence": 0.82}

    async def ask(images, prompt, max_output_tokens=0):
        return SimpleNamespace(data=reply, model="stand-in-model", latency_ms=5.0, error=None)

    agent.vision = lambda _o: SimpleNamespace(ask=ask)
    if log_off:
        agent.log = lambda record: None  # the interaction log written nowhere: nothing below may rest on it
    agent.keep_image = lambda *a, **k: None
    hub.scene.context["specimen"] = "blood smear"
    asyncio.run(agent.identify(obj.id))
    return hub, hub.scene.objects["o1"]


def test_naming_a_click_keeps_how_it_was_named_on_the_outline(tmp_path):
    hub, obj = named(tmp_path)
    n = obj.naming
    assert n["by"] == "model" and n["model"] == "stand-in-model" and n["frame"] == 7
    assert n["observations"] == ["multi-lobed nucleus", "pale granular cytoplasm"] and n["alternatives"] == ["eosinophil"]
    assert "blood smear" in n["told"]  # exactly what it was told besides the pictures
    assert "naming" not in obj.to_wire()  # off the wire


def test_how_do_you_know_reads_the_outline_one_step_behind_it(tmp_path):
    hub, obj = named(tmp_path)
    text = Index(hub).read("o1")
    assert text.startswith('"neutrophil" (o1)')
    assert "Outlined from your click" in text
    assert 'Named "neutrophil" by' in text
    assert "shown the whole view with the outline drawn and a close-up of it" in text and "(frame 7)" in text
    assert "What it noted: multi-lobed nucleus; pale granular cytoplasm." in text
    assert "What else it weighed: eosinophil." in text
    assert "Its confidence: 0.82" in text and "45%" in text
    assert "What it was told besides the pictures:" in text and "blood smear" in text
    assert "no scale yet" in text  # no size is made up


def test_a_name_the_model_was_unsure_of_says_so_and_never_lists_its_own_pick_as_another(tmp_path):
    hub, obj = named(tmp_path, {"observations": ["small"], "group": "white blood cell", "label": "monocyte",
                                "alternatives": [{"name": "lymphocyte"}], "answer": "Hard to tell.", "confidence": 0.4,
                                "unsure": True})
    text = Index(hub).read("o1")
    assert 'Named "white blood cell" by' in text and 'Its own first guess was "monocyte"' in text
    assert "What else it weighed: lymphocyte." in text  # not its own first pick again
    assert "It said it was not sure: maybe monocyte or lymphocyte (confidence 0.40)." in text


def test_renames_through_the_real_paths_keep_who_changed_what_and_the_models_account_stays_its_own(tmp_path):
    """Review of #157, H1 and should-fix 3: name_outline and accepting a correction, each through its
    own code; a relabel nobody's rename explains (the app's) is said; the model's account is for its
    own name only, and its calibration is not printed under another."""
    from orchestrator.brain.registry import ToolContext
    from orchestrator.brain.toolbox import Services, build
    from tests.fakes import announcer

    hub, obj = named(tmp_path)
    registry = build(Services(hub=hub))
    asyncio.run(registry.tools["name_outline"].handler(ToolContext("chat", 1, None), {"object": "o1", "name": "band cell"}))
    correction = {"id": "c1", "object_id": "o1", "label": "band cell", "to_label": "young neutrophil", "reason": "nucleus shape"}
    research = SimpleNamespace(decide=lambda cid, accept: (SimpleNamespace(name="Researcher 2", rid="r2"), correction))
    s = announcer(tmp_path / "a", research, scene=hub.scene)

    async def accept():
        out = s.announcer.decided("c1", True)
        await asyncio.sleep(0.05)
        return out

    asyncio.run(accept())
    text = Index(hub).read("o1")
    assert text.startswith('"young neutrophil" (o1)')
    assert 'Named "neutrophil" by' in text and "What it noted: multi-lobed nucleus" in text
    assert 'Renamed "neutrophil" to "band cell" by you at' in text
    assert 'Renamed "band cell" to "young neutrophil" by you, from Researcher 2\'s suggestion at' in text
    assert 'The account above is for "neutrophil", not for "young neutrophil".' in text
    assert "45%" not in text  # the naming calibration belongs to the model's own name only
    hub.scene.update("o1", label="eosinophil")  # a highlight's label= relabels without a rename
    assert 'Relabelled by the app to "eosinophil" since.' in Index(hub).read("o1")


def test_a_relabel_by_the_app_is_said_and_the_account_is_for_the_models_name(tmp_path):
    hub, obj = named(tmp_path)
    hub.scene.update("o1", label="band cell")  # what highlight(label=) does
    text = Index(hub).read("o1")
    assert 'Named "neutrophil" by' in text and 'Relabelled by the app to "band cell" since.' in text
    assert 'The account above is for "neutrophil", not for "band cell".' in text and "45%" not in text


def test_each_path_says_only_what_it_knows(tmp_path):
    """Review of #157, H2: a SAM 3.1 match, a promoted cell, and a path that keeps no record."""
    from orchestrator.known import match_record, promoted_record

    hub = Hub()

    def add(oid, source, **kw):
        hub.scene.add(SceneObject(id=oid, kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1,
                                  anchor_epoch=0, anchor_offset=(0.0, 0.0), source=source, status="likely", **kw))

    add("o4", "ai:sam3.1-all", label="red cell", confidence=0.55, naming=match_record(label="red cell", example="o2", score=0.55))
    add("o5", "ai:measured:e3", label="red blood cell", naming=promoted_record(label="red blood cell", population="e3", member="c12"))
    add("o6", "voice", label="stoma", confidence=0.7)
    index = Index(hub)
    match = index.read("o4")
    assert "Outlined from a match for o2" in match and "mask score 0.55, how well the outline fits, not how sure a name is" in match
    assert 'its name "red cell" is the example\'s, not recognised on its own' in match and "45%" not in match
    promoted = index.read("o5")
    assert "as cell c12 of e3, then promoted" in promoted and 'Its name "red blood cell" came with the request that chose it.' in promoted
    assert "the app's look" not in promoted and "not from looking" not in promoted
    unknown = index.read("o6")
    assert "This path does not keep how it was named." in unknown and "45%" not in unknown
    assert "made before" not in unknown


def test_a_removed_outline_is_refused_truthfully_and_a_long_context_is_marked_cut(tmp_path):
    from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
    from orchestrator.brain.toolbox.resources import add_resource_tools
    from orchestrator.known import TOLD_MAX, naming_record

    import pytest

    registry = Registry()
    hub = Hub()
    add_resource_tools(registry, SimpleNamespace(hub=hub, day=None))
    with pytest.raises(ToolFailed) as refused:
        asyncio.run(registry.tools["resources"].handler(ToolContext("chat", 1, None), {"id": "o3"}))
    assert "not outlined now" in str(refused.value) and "click the thing again" in (refused.value.next or "")
    record = naming_record(model="m", option="m", frame_id=1, info=SimpleNamespace(), reading={"label": "x"},
                           told="word " * TOLD_MAX)
    assert record["told"].endswith("… (cut here)") and len(record["told"]) < TOLD_MAX + 20


def test_outlines_without_a_naming_record_and_blind_arms_say_only_what_is_known(tmp_path):
    hub = Hub()
    hub.scene.add(SceneObject(id="o2", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1,
                              anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:gemini-3.5-flash", label="stoma",
                              status="likely"))
    hub.scene.add(SceneObject(id="o3", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1,
                              anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:gpt", label="stoma", trial="t1", arm="A"))
    index = Index(hub)
    looked = index.read("o2")
    assert "Named by the app's look at the view" in looked and "was not kept" in looked
    assert index.read("o3") == "o3 is a blind test's answer: how it was made is shown after the user's vote."
    assert "gpt" not in index.read("o3")
    assert index.read("o9") == ""
    assert all(e.id not in ("o2", "o3") for e in index.list())  # a lookup, not listed


def test_the_resources_tool_says_it_reads_outlines_for_how_do_you_know():
    from orchestrator.brain.registry import Registry
    from orchestrator.brain.toolbox.resources import add_resource_tools

    registry = Registry()
    add_resource_tools(registry, SimpleNamespace(hub=Hub(), day=None))
    assert 'how do you know?' in registry.tools["resources"].description


def test_the_looks_own_outlines_keep_a_record_and_a_relabel_is_said_against_it(tmp_path):
    """Review of #157, re-check H1: the look's outlines (the most common) say who named them, and a
    highlight's label= afterwards is the app's relabel, never the look's account under a new name."""
    from orchestrator.coords import Crop
    from tests.test_taught import SQUARE, make_hub_with_frame

    hub = make_hub_with_frame()
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=SQUARE, anchor_frame=7, anchor_epoch=0,
                              anchor_offset=(0.0, 0.0), source="ai:gemini-3.5-flash", label="", status="outline"))
    box = [300 / 720 * 1000, 600 / 1280 * 1000, 360 / 720 * 1000, 660 / 1280 * 1000]  # the same square, 0-1000
    asyncio.run(agent.point([{"box_2d": box, "label": "stoma", "confidence": 0.7}], 7, Crop(0, 0, 1280, 720)))
    obj = hub.scene.objects["o1"]
    assert obj.label == "stoma" and obj.naming["by"] == "look" and obj.naming["label"] == "stoma"
    text = Index(hub).read("o1")
    assert 'Named "stoma" by the app\'s look at the view' in text and "Outlined from the app's look" in text
    assert "The look's own confidence: 0.70 (not calibrated on the naming bench)." in text and "45%" not in text
    hub.scene.update("o1", label="band cell")  # highlight(label=) on it
    text = Index(hub).read("o1")
    assert 'Relabelled by the app to "band cell" since.' in text and 'The account above is for "stoma", not for "band cell".' in text
    assert "The look's own confidence" not in text
    # A look that renames an outline named before keeps the earlier account and adds its rename.
    hub.scene.update("o1", label="stoma")
    asyncio.run(agent.point([{"box_2d": box, "label": "guard cell", "confidence": 0.8}], 7, Crop(0, 0, 1280, 720)))
    assert 'Renamed "stoma" to "guard cell" by the app\'s look' in Index(hub).read("o1")


def test_a_new_outline_keeps_the_record_it_is_given_and_earlier_renames_past_five_are_said_not_kept():
    from orchestrator import tools
    from orchestrator.known import RENAMES_KEPT, look_record, renamed
    from tests.test_apply import World, run

    world = World()
    obj = run(tools.add_outline(world, world.captures.take(1), [[[300, 200], [340, 200], [340, 240], [300, 240]]],
                                source="ai:test", label="stoma", naming=look_record(label="stoma", model="test")))
    assert obj.naming["by"] == "look"  # through add_outline itself, not a stand-in
    names = ["stoma"] + [f"name {k}" for k in range(RENAMES_KEPT + 1)]
    for old, new in zip(names, names[1:]):
        world.scene.update(obj.id, label=new, status="confirmed", naming=renamed(world.scene.objects[obj.id], "you", new))
    text = Index(world).read(obj.id)
    assert "1 earlier rename not kept." in text and "Relabelled by the app" not in text


def test_a_new_look_outline_from_an_empty_board_keeps_the_looks_record_and_outline_at_passes_it_on(tmp_path, monkeypatch):
    """Review of #157, second re-check SF1: the main path, from nothing on the board."""
    from orchestrator import tools
    from orchestrator.coords import Crop
    from orchestrator.known import look_record
    from tests.test_apply import World, run
    from tests.test_taught import make_hub_with_frame

    hub = make_hub_with_frame()
    agent = Agent(hub, interaction_log=tmp_path / "interactions.jsonl")

    async def outline_at(hub_, view, x, y, box=None, **kw):  # the outline model, stood in (no GPU here)
        x0, y0, x1, y1 = box
        return hub_.scene.add(SceneObject(id=hub_.scene.new_id(), kind="highlight", anchor_frame=7, anchor_epoch=0,
                                          anchor_offset=(0.0, 0.0), polygons=[[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                                          source=kw["source"], label=kw.get("label", "")))

    monkeypatch.setattr(tools, "outline_at", outline_at)
    box = [300 / 720 * 1000, 600 / 1280 * 1000, 360 / 720 * 1000, 660 / 1280 * 1000]
    made = asyncio.run(agent.point([{"box_2d": box, "label": "stoma", "confidence": 0.7}], 7, Crop(0, 0, 1280, 720)))
    assert made and made[0].naming["by"] == "look" and made[0].naming["label"] == "stoma"
    monkeypatch.undo()
    world = World()
    obj = run(tools.outline_at(world, world.latest_info.id, 300, 200, box=(280, 180, 320, 220), source="ai:test",
                               label="stoma", naming=look_record(label="stoma", model="test")))
    assert obj.naming["by"] == "look"  # outline_at hands it to add_outline


def test_a_request_word_the_look_did_not_give_is_said_as_the_requests():
    from orchestrator.known import asked_record

    hub = Hub()
    hub.scene.add(SceneObject(id="o7", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1,
                              anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:gemini-3.5-flash", label="nucleus",
                              status="likely", naming=asked_record(label="nucleus", model="gemini-3.5-flash")))
    text = Index(hub).read("o7")
    assert 'its name "nucleus" is the request\'s word, not the look\'s' in text and "Named \"nucleus\" by" not in text


def test_a_thing_sam_found_for_the_words_says_its_name_is_the_words():
    """M3.1: find by="concept" keeps a concept record; the name is what SAM 3.1 was asked to find."""
    from orchestrator.known import concept_record

    hub = Hub()
    hub.scene.add(SceneObject(id="o7", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1,
                              anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai:sam3.1-concept", status="likely",
                              label="guard cell", confidence=0.62,
                              naming=concept_record(label="guard cell", words="guard cell", score=0.62)))
    said = Index(hub).read("o7")
    assert 'Outlined from a match for the words "guard cell"' in said
    assert 'Found by SAM 3.1 for the words "guard cell"' in said and "its match score 0.62, not how sure a name is" in said
    assert 'its name "guard cell" is the words it was asked to find' in said and "45%" not in said


def test_a_renamed_concept_outline_says_whose_account_it_is():
    """M3.1 review S1: "concept" in the lines after a rename and a redraw."""
    from orchestrator.known import concept_record, renamed

    hub = Hub()
    obj = SceneObject(id="o8", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30]]], anchor_frame=1, anchor_epoch=0,
                      anchor_offset=(0.0, 0.0), source="ai:sam3.1-concept", status="unsure", label="guard cell",
                      naming=concept_record(label="guard cell", words="guard cell", score=0.6))
    hub.scene.add(obj)
    record = renamed(obj, "you", "stoma")
    record["repaired"] = {"at": 1.0}
    hub.scene.update("o8", label="stoma", naming=record)
    said = Index(hub).read("o8")
    assert 'The account above is for "guard cell", not for "stoma".' in said
    assert "the score above was for the first outline" in said
