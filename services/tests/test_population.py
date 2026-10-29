"""Populations (contracts 3, 8 and 9): a cell run kept as evidence of the specialist's own masks,
measured by app code, shown member by member only where each member's own pixels are checked, and
queried, selected and highlighted by what was measured, in one request and the next."""
import asyncio
import threading
import time

import cv2
import numpy as np
import pytest

from gpu_worker.worker import encode_labels
from orchestrator import tools
from orchestrator.population import layer_of, resolve, review, select, summary
from orchestrator.ticket import issue, using
from tests.test_apply import World, run
from tests.waiting import HANG_S

# Six cells on the slide's texture: their sizes and shapes known (x, y, half-width, half-height).
CELLS = [(300, 200, 12, 12), (380, 210, 20, 20), (470, 220, 30, 10), (300, 330, 8, 8), (420, 340, 25, 25), (540, 330, 14, 7)]


def wait_saved(population, seconds: float = 60.0) -> None:
    """Waits for the background save itself (its thread), not a fixed time: under a sim's load a save can take
    longer than any short wait, and the wait ends as soon as it is done."""
    for thread in threading.enumerate():
        if thread.name == f"evidence-{population.id}":
            thread.join(seconds)
    t0 = time.time()
    while population.status == "saving" and time.time() - t0 < seconds:
        time.sleep(0.01)


def label_map(origin=(200, 120), size=(500, 320)):
    w, h = size
    labels = np.zeros((h, w), np.uint16)
    for n, (x, y, a, b) in enumerate(CELLS, start=1):
        cv2.ellipse(labels, (x - origin[0], y - origin[1]), (a, b), 0, 0, 360, n, -1)
    return labels, origin


class CellFinder:
    """The analysis worker's cell finder: Cellpose's label map of the six cells, with its scores."""

    def __init__(self):
        self.calls = 0

    async def ensure_started(self):
        return self

    async def call(self, op, timeout=0, **payload):
        self.calls += 1
        labels, origin = label_map()
        return {"labels": encode_labels(labels, origin), "scores": {str(n): 0.9 - 0.05 * n for n in range(1, 7)},
                "score": "cellprob", "polygons": [], "count": 6, "ms": 900}


@pytest.fixture
def world(monkeypatch):
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    w = World()
    w.settings.cells = "cellpose-sam"
    w.gpu_heavy = CellFinder()
    w.sent = []
    w.broadcast = w.sent.append
    return w


def cells(world):
    with using(issue(world, "command")):
        return run(tools.outline_cells(world))


def test_a_cell_run_is_a_population_of_the_specialists_own_masks_measured_by_the_app(world):
    population, shown = cells(world)
    assert shown and len(population.live()) == 6 and not [o for o in world.scene.objects.values() if o.kind == "cell"]
    areas = sorted(population.value(m.id, "areaPx") for m in population.live())
    assert areas[0] == pytest.approx(np.pi * 8 * 8, rel=0.15) and areas[-1] == pytest.approx(np.pi * 25 * 25, rel=0.1)
    long = max(population.live(), key=lambda m: population.value(m.id, "elongation"))
    assert population.value(long.id, "elongation") == pytest.approx(3.0, abs=0.25)  # the 30 x 10 one
    assert long.extras["scoreIs"] == "cellprob" and population.made_by["implementation"] == "cellpose-sam"
    assert "6 instances segmented by cellpose-sam inside the eyepiece disc" in population.count_words()
    assert population.status == "unsaved"  # this run has no evidence folder: said, never "saved"


def test_a_member_is_drawn_live_only_after_its_own_check(world):
    population, _ = cells(world)
    layer = layer_of(world)
    run(layer.check())
    assert layer.verified_count() == (6, 6)
    world.frame(37.0, -21.0)  # the slide moved: each member checked again where the move puts it
    run(layer.check())
    assert layer.verified_count() == (6, 6)
    world.frame(0.0, 0.0, epoch=1)  # continuity lost: unverified at once, never "moved"
    run(layer.check())
    assert layer.verified_count() == (0, 6)
    assert all(p["state"] == "unverified" for p in layer.placement.values())


def test_a_swimmer_is_unverified_while_the_slide_pans_and_the_rest_stay_verified(world):
    population, _ = cells(world)
    layer = layer_of(world)
    run(layer.check())
    info = world.frame(12.0, 0.0)  # the slide pans...
    swimmer = next(m for m in population.live() if m.box[0] < 300 < m.box[2] and m.box[1] < 330 < m.box[3])
    image = cv2.imdecode(np.frombuffer(world.ring[info.id], np.uint8), cv2.IMREAD_GRAYSCALE)
    x0, y0, x1, y1 = (int(v) for v in swimmer.box)
    # ...and the thing that was there swam away: other texture where the slide's move puts it.
    other = np.random.default_rng(9).integers(0, 255, (y1 - y0 + 24, x1 - x0 + 24), dtype=np.uint8)
    image[y0 - 12:y1 + 12, x0 + 12 - 12:x1 + 12 + 12] = cv2.GaussianBlur(other, (0, 0), 2.0)
    world.ring[info.id] = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), 92])[1].tobytes()
    run(layer.check())  # its earlier check counts for nothing once this one fails (scaffolding review, M1.3 item 1)
    states = layer.states()
    assert states[swimmer.id] == "unverified" and sum(s == "verified" for s in states.values()) == 5


def test_members_are_chosen_by_what_was_measured(world):
    population, _ = cells(world)
    largest, how = select(population, by="area", top=2)
    assert [population.value(m.id, "areaPx") for m in largest] == sorted(
        (population.value(m.id, "areaPx") for m in population.live()), reverse=True)[:2]
    assert how == "largest areaPx first, first 2"
    long, _ = select(population, where={"elongation": [2.5, None]})
    assert len(long) == 1 and population.value(long[0].id, "elongation") > 2.5
    words = summary(population, ["areaPx", "elongation"])
    assert words.startswith("6 instances") and "median" in words and "no scale, so no µm" in words
    assert "units: lengths in px, areas in px²" in words  # area is not a length, including the overall unit line
    # The mean is a fact beside the median, so "the average" is answered with it (think-broad-2, just-average).
    areas = [population.value(m.id, "areaPx") for m in population.live()]
    assert f"areaPx: mean {sum(areas) / len(areas):.4g}, median " in words
    assert resolve(world, "", None) is population  # the one shown
    assert isinstance(review(population), list)


def test_a_cut_by_a_column_says_the_values_and_a_tie_at_the_cut():
    """Live 2026-10-11, "highlight the round ones" 3 of 3: the receipt said only "first 2: c2, c4", and the round
    after it called them the two roundest while c1 was as round. The values, the next ones and a tie are facts."""
    from types import SimpleNamespace

    from orchestrator.population import RANKING_SHOWN, ranking_words

    roundness = {"c2": 0.912, "c4": 0.903, "c1": 0.9031, "c6": 0.894, "c3": 0.71, "c5": 0.5}
    population = SimpleNamespace(value=lambda mid, column: roundness[mid])
    ranked = [SimpleNamespace(id=i, extras={}) for i in roundness]
    line = ranking_words(population, ranked, 2, "circularity")
    assert line == ("roundness: c2 0.912, c4 0.903; next: c1 0.903, c6 0.894, c3 0.71, c5 0.5; "
                    "c1 ties c4 at the cut")
    assert ranking_words(population, ranked, 3, "circularity").endswith("next: c6 0.894, c3 0.71, c5 0.5")  # no tie
    many = {f"c{n}": 100.0 - n for n in range(1, 21)}
    population = SimpleNamespace(value=lambda mid, column: many[mid])
    ranked = [SimpleNamespace(id=i, extras={}) for i in many]
    line = ranking_words(population, ranked, 7, "areaPx")
    assert line == "area (px²): c1 99, c2 98, 2 more, c5 95, c6 94, c7 93; next: c8 92, c9 91, c10 90, 10 more"
    import re

    assert len(re.findall(r"\bc\d+ \d", line)) == RANKING_SHOWN  # bounded: the rest are counted


def test_a_range_says_the_values_inside_it_and_the_nearest_outside():
    """Journeys round 2 ("the round ones" 0 of 3 live): "the round ones" are a range the model chooses
    ({"roundness": [0.85, null]}), and the values inside it and the nearest outside are facts to judge it by."""
    from types import SimpleNamespace

    from orchestrator.population import select

    roundness = {"c1": 1.05, "c2": 1.01, "c3": 0.71, "c4": 1.08, "c5": 0.69, "c6": 0.97}
    population = SimpleNamespace(live=lambda: [SimpleNamespace(id=i, extras={}) for i in roundness],
                                 value=lambda mid, column: roundness[mid], column_type=lambda column: "number")
    notes: list[str] = []
    chosen, how = select(population, where={"circularity": [0.85, None]}, notes=notes)
    assert sorted(m.id for m in chosen) == ["c1", "c2", "c4", "c6"] and how == "circularity ≥ 0.85"
    assert notes == ["roundness ≥ 0.85: c4 1.08, c1 1.05, c2 1.01, c6 0.97; nearest outside: c3 0.71, c5 0.69"]
    notes = []
    assert select(population, where={"circularity": [1.2, None]}, notes=notes)[0] == []
    assert notes == ["roundness ≥ 1.2: none; nearest outside: c4 1.08, c1 1.05, c2 1.01, 3 more"]


def test_more_in_a_one_sided_range_than_can_be_outlined_puts_those_past_its_bound_farthest_first():
    """Live 2026-10-11: "the round ones" (roundness >= 0.85) fit 161 cells; the first 15 by id were outlined and
    called the roundest. A one-sided range is ordered by how far past its bound each is, said; others are untouched."""
    from types import SimpleNamespace

    from orchestrator.population import past_bound_first

    roundness = {"c1": 0.86, "c2": 0.99, "c3": 0.91, "c4": 0.88}
    population = SimpleNamespace(live=lambda: [SimpleNamespace(id=i, extras={}) for i in roundness],
                                 value=lambda mid, column: roundness[mid], column_type=lambda column: "number")
    members = population.live()
    ordered, words = past_bound_first(population, members, {"circularity": [0.85, None]})
    assert [m.id for m in ordered] == ["c2", "c3", "c4", "c1"] and words == "largest circularity first"
    ordered, words = past_bound_first(population, members, {"circularity": [None, 0.95]})
    assert [m.id for m in ordered] == ["c1", "c4", "c3", "c2"] and words == "smallest circularity first"
    for where in (None, {"circularity": [0.8, 0.95]}):  # nothing to order by: as it was
        assert past_bound_first(population, members, where) == (members, "")


def test_a_highlight_and_a_query_by_a_column_say_the_values_behind_their_cut(world):
    from types import SimpleNamespace

    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.population import add_population_tools
    from orchestrator.brain.toolbox.view import add_highlight

    population, _ = cells(world)
    registry = Registry()
    add_highlight(registry, world, sight=SimpleNamespace(view=None), outline_seen=None)
    add_population_tools(registry, SimpleNamespace(hub=world, log=None))
    ranked, _ = select(population, by="roundness")
    values = [population.value(m.id, "circularity") for m in ranked]
    with using(issue(world, "turn")):
        said = run(registry.tools["highlight"].handler(ToolContext("chat", 1, None, "highlight the round ones"),
                                                       {"by": "roundness", "top": 2}))
    shown = lambda k: f"{ranked[k].id} {values[k]:.3g}"
    assert f"; roundness: {shown(0)}, {shown(1)}; next: {shown(2)}, {shown(3)}, {shown(4)}, {shown(5)}" in said
    with using(issue(world, "turn")):
        asked = run(registry.tools["query_population"].handler(ToolContext("chat", 2, None, "the two roundest?"),
                                                               {"by": "roundness", "top": 2}))
    assert f"roundness: {shown(0)}, {shown(1)}; next: {shown(2)}" in asked


def test_the_five_largest_cells_are_one_call_and_unverified_ones_show_on_their_capture(world):
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.view import add_highlight

    population, _ = cells(world)
    shown_pictures = []
    magnifier = type("M", (), {"show_picture": lambda self, jpeg, label, object_id=None: shown_pictures.append(label)})()
    registry = Registry()
    add_highlight(registry, world, sight=type("S", (), {"view": None})(), outline_seen=None, magnifier=magnifier)
    ctx = ToolContext("chat", 1, None, "highlight the five largest cells")
    with using(issue(world, "turn")):
        said = run(registry.tools["highlight"].handler(ctx, {"by": "area", "top": 5}))
    made = [world.scene.objects[i] for i in ctx.objects]
    assert len(made) == 5 and all((o.made_by or {}).get("how") == "measured" for o in made)
    assert said.startswith(f"{population.title} ({population.id}), largest areaPx first, first 5: outlined")
    assert population.title.startswith("Cell outlines · ")  # a plain title first (Amendment 4, item 1)
    world.frame(0.0, 0.0, epoch=1)  # the view changed: members can only be shown on their capture
    ctx2 = ToolContext("chat", 2, None, "and the two smallest")
    with using(issue(world, "turn")):
        said2 = run(registry.tools["highlight"].handler(ctx2, {"of": population.id, "by": "area", "order": "asc", "top": 2}))
    assert "shown on the view they were made on" in said2 and shown_pictures and "not the live view" in shown_pictures[-1]


def test_a_later_step_takes_what_an_earlier_one_made(world):
    """[outline_cells, highlight(of="@1", by="area", top=5)] in one request (contract 9)."""
    from orchestrator.brain.actions import ActionRunner
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox.scene import add_scene_tools
    from orchestrator.brain.toolbox.view import add_highlight
    from types import SimpleNamespace

    registry = Registry()
    add_scene_tools(registry, SimpleNamespace(hub=world, magnifier=None, subjects=None, taught=None, board=None,
                                              agent=None, presenter=None))
    add_highlight(registry, world, sight=SimpleNamespace(view=None), outline_seen=None)

    async def late(run_, ctx_):
        return None

    actions = ActionRunner(registry, world, sight=lambda: None, on_late=late)
    ctx = ToolContext("chat", 1, None, "outline the cells and show me the five largest")
    with using(issue(world, "turn")):
        runs = run(actions.run([("outline_cells", {}), ("highlight", {"of": "@1", "by": "area", "top": 5})], ctx))
    assert [r.ok for r in runs] == [True, True], [r.summary for r in runs]
    made = ctx.steps.produced[1]
    assert made and f"({made[0]}), largest areaPx first, first 5" in runs[1].summary


def composing(world):
    from orchestrator.brain.actions import ActionRunner
    from orchestrator.brain.registry import Registry
    from orchestrator.brain.toolbox.scene import add_scene_tools
    from orchestrator.brain.toolbox.view import add_highlight
    from types import SimpleNamespace

    registry = Registry()
    add_scene_tools(registry, SimpleNamespace(hub=world, magnifier=None, subjects=None, taught=None, board=None,
                                              agent=None, presenter=None))
    add_highlight(registry, world, sight=SimpleNamespace(view=None), outline_seen=None)

    async def late(run_, ctx_):
        return None

    return ActionRunner(registry, world, sight=lambda: None, on_late=late)


def test_a_step_keeps_the_number_the_model_gave_it_when_its_answer_runs_in_two_parts(world):
    """Scaffolding review, M1.3 item 8: the brain runs a look's clearing steps, then the rest, as two
    runs; "@2" was renumbered to the highlight itself and failed with a false "no cells segmented"."""
    from orchestrator.brain.registry import Steps, ToolContext

    actions = composing(world)
    ctx = ToolContext("chat", 1, None, "clear, outline the cells and show me the five largest")
    ctx.steps = Steps()
    with using(issue(world, "turn")):
        first = run(actions.run([("clear", {"what": "all"})], ctx, [1]))
        rest = run(actions.run([("outline_cells", {}), ("highlight", {"of": "@2", "by": "area", "top": 5})], ctx, [2, 3]))
    assert [r.ok for r in first + rest] == [True, True, True], [r.summary for r in first + rest]
    assert f"({ctx.steps.produced[2][0]})" in rest[1].summary


def test_steps_the_fast_pass_already_ran_keep_what_they_made_under_the_answers_numbers():
    """The reviewer's re-check of M1.3, note 2: the look's [outline_cells, highlight(of="@1")] after the
    fast pass ran outline_cells was refused ("this answer has no step 1") though the cells existed."""
    from orchestrator.brain.registry import Steps

    fast = Steps(tools={1: "outline_cells"}, produced={1: ["e4"]})
    look = Steps.carrying([("outline_cells", {}), ("highlight", {"of": "@1"})], {"outline_cells"}, fast)
    assert look.tools == {1: "outline_cells"} and look.produced[1] == ["e4"]
    fast.produced[1].append("e5")  # made after the look began: still found (one shared list)
    assert look.produced[1] == ["e4", "e5"]
    assert Steps.carrying([("highlight", {"of": "@1"})], set(), fast).tools == {}


def test_a_reference_to_a_step_that_made_nothing_is_refused_with_the_reason(world):
    from orchestrator.brain.registry import ToolContext

    actions = composing(world)
    ctx = ToolContext("chat", 1, None, "clear and show me the five largest")
    with using(issue(world, "turn")):
        runs = run(actions.run([("clear", {"what": "all"}), ("highlight", {"of": "@1", "top": 5}),
                                ("highlight", {"of": "@3", "top": 5})], ctx))
    assert runs[1].ok is False and "step 1 (clear) made nothing" in runs[1].summary
    assert runs[2].ok is False and "this step or a later one" in runs[2].summary
    with using(issue(world, "turn")):  # a later round is a new answer: its "@1" is its own first step
        again = run(actions.run([("highlight", {"of": "@1", "top": 5})], ctx))
    assert again[0].ok is False and "this step or a later one (highlight)" in again[0].summary


def test_showing_three_hundred_members_keeps_the_scene_push_small(world):
    """Cells are no longer scene objects: the scene's wire stays the few outlines; the population is
    sent once when shown, and only the verified set changes after."""
    from orchestrator.evidence import Member, Population, evidence_of, polygons_of
    from orchestrator.measure import Mask
    from collections import OrderedDict

    store = evidence_of(world)
    members = OrderedDict()
    for i in range(300):
        bits = np.ones((10, 10), bool)
        mask = Mask(bits, 200 + 30 * (i % 30), 100 + 30 * (i // 30))
        mid = store.member_id()
        members[mid] = Member(mid, i + 1, mask, polygons_of(mask), mask.box, {})
    p = Population(store.new_id(), "cells", "cellpose-sam", {}, 1, {"epoch": 0, "dx": 0.0, "dy": 0.0, "width": 1280,
                   "height": 720, "disc": None, "at": 1000.0}, None, None, members)
    store.add(p)
    layer_of(world).show(p)
    times = []
    for _ in range(50):
        t0 = time.perf_counter()
        world.scene.to_wire()
        layer_of(world).wire()
        times.append((time.perf_counter() - t0) * 1000)
    assert sorted(times)[int(0.95 * len(times))] < 2.0


def test_a_click_inside_a_shown_member_selects_it_and_only_while_it_is_drawn(world):
    population, _ = cells(world)
    layer = layer_of(world)
    run(layer.check())
    member = layer.member_hit(world.latest_info, 380, 210)  # inside the second cell
    assert member is not None and layer.member_hit(world.latest_info, 900, 600) is None
    layer.select(member)
    said = [m for m in world.sent if m.get("type") == "member-selected"][-1]
    assert said["member"] == member.id and said["words"].startswith(f"{member.id}: area ")  # plain words (flow 4 on footage)
    world.frame(0.0, 0.0, epoch=1)
    run(layer.check())
    assert layer.member_hit(world.latest_info, 380, 210) is None  # not drawn now, so a click outlines instead


def test_the_studio_and_the_app_colour_a_member_alike():
    """contract 10: a member's colour comes from its id, the same in the studio and the app's pictures."""
    import re
    from pathlib import Path

    from orchestrator.contract import OVERLAY
    from orchestrator.population import PALETTE_BGR

    # One table, contract/overlay.json's memberColours, read by both (going live, G3).
    source = (Path(__file__).resolve().parents[2] / "apps/studio/src/population.ts").read_text()
    assert re.search(r"export const PALETTE: string\[\] = contract\.memberColours\.palette;", source)
    css = [c[1:] for c in OVERLAY["memberColours"]["palette"]]
    assert len(css) == 12 and [tuple(int(c[i:i + 2], 16) for i in (4, 2, 0)) for c in css] == PALETTE_BGR


def test_a_background_mask_set_becomes_evidence_once_a_request_uses_it(world):
    """Contract 8: automatic masks made in the background stay temporary; the first request that
    outlines one of them keeps the set as a population (its view, each mask as made), once."""
    from types import SimpleNamespace

    from orchestrator.population import from_maskset

    bits = np.zeros((20, 30), bool)
    bits[2:18, 3:27] = True
    region = {"polygons": [[[100, 100], [130, 100], [130, 120], [100, 120]]], "label": [115, 110], "score": 0.9,
              "mask": {"bits": np.packbits(bits).tobytes(), "shape": [20, 30], "x0": 100.0, "y0": 100.0, "scale": 1.0}}
    marks = SimpleNamespace(regions={1: region, 2: {**region, "mask": None}}, made_on=1)
    population, by_number = from_maskset(world, marks)
    assert population.operation == "automask" and population.made_by["origin"] == "background"
    assert list(by_number) == [1] and population.value(by_number[1], "areaPx") == bits.sum()
    # Its execution, as every population has (Amendment 4, item 3), with what it became.
    from orchestrator.operations import runner

    record = runner(world).get(population.made_by["execution"])
    assert record.executed == "edgetam-automask" and record.origin == "background" and population.id in record.outputs
    again, _ = from_maskset(world, marks)
    assert again is population  # one population per mask set, however many uses


def test_a_slide_offset_a_few_px_off_is_corrected_by_the_members_own_pixels(world):
    """After a pan the slide's offset can be a few px off for every member at once; their own peaks
    say by how much, and they are drawn there (as the hub nudges outlines)."""
    population, _ = cells(world)
    layer = layer_of(world)
    info = world.frame(20.0, 0.0)
    info.dx = 16.0  # the tracker says 16 px; the picture moved 20
    run(layer.check())
    assert layer.verified_count() == (6, 6)
    assert layer.correction[0] == pytest.approx(4.0, abs=1.0) and abs(layer.correction[1]) < 1.0
    drawn = layer.drawable(world.latest_info)
    first = population.live()[0]
    colour, polygons = drawn[0]
    assert polygons[0][0][0] == pytest.approx(first.polygons[0][0][0] + 20.0, abs=1.0)  # where its pixels are
    from orchestrator.evidence import colour_of
    from orchestrator.population import PALETTE_BGR

    assert colour == PALETTE_BGR[colour_of(first.id)]  # a snapshot draws it in its own colour, as the studio does


def test_saved_evidence_keeps_what_interpreting_it_needs_with_every_log_off(world, tmp_path, monkeypatch):
    """Amendment 4, item 3 (the partner, §1f): with MICROMIND_EXECUTION_INSPECT=0 and the chat's
    execution-logging switch off, a saved population's manifest still holds its source (capture,
    boot, seq), time, the execution, the producer with its version and configuration, policy and
    origin, transformations, and measurements with units and the scale; and so does its read-back."""
    import json

    from orchestrator import execution
    from orchestrator.day.store import DayStore
    from orchestrator.evidence import Evidence

    monkeypatch.setenv("MICROMIND_EXECUTION_INSPECT", "0")
    world.execution = execution.ExecutionLog.from_env()
    world.execution.set_enabled(False)
    assert not world.execution.enabled
    world.latest_info.seq, world.latest_info.boot = 4242, "b7"
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    world.evidence = Evidence(world, tmp_path / "evidence", day=day)
    population, _ = cells(world)
    wait_saved(population)
    assert population.status == "saved"
    manifest = json.loads((population.path / "manifest.json").read_text())
    assert manifest["source"] == {"capture": population.capture_id, "boot": "b7", "seq": 4242}
    assert manifest["capturedAt"] == population.view["at"] and manifest["title"].startswith("Cell outlines · ")
    assert manifest["execution"] and manifest["execution"] == population.made_by["execution"]
    assert (manifest["policy"], manifest["origin"]) == ("route", "brain-default")
    assert manifest["producer"]["implementation"] == "cellpose-sam" and manifest["producer"]["version"] == "cellpose cpsam"
    assert manifest["producer"]["configuration"]["crop"] and manifest["transformations"]["crop"]
    measured = manifest["columns"]["measure"]
    assert measured["basis"]["unit"] == "px" and measured["values"] and "scaleId" in manifest
    back = Evidence(world, tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07")).get(population.id)
    assert back.producer == manifest["producer"] and back.made_by["execution"] == manifest["execution"]


def test_after_a_rejoin_the_studio_is_told_the_layers_new_view(world):
    """Scaffolding review, M1.3 item 3: the verified set alone did not change, so the studio kept the old
    epoch and drew nothing while the board said they were in place."""
    population, _ = cells(world)
    layer = layer_of(world)
    run(layer.check())
    sent = len([m for m in world.sent if m.get("type") == "layer"])
    world.epoch_links = {0: (2, (500.0, 0.0))}  # a verified rejoin carried epoch 0 to 2
    info = world.frame(0.0, 0.0, epoch=2)
    info.dx = 500.0
    run(layer.check())
    layers = [m for m in world.sent if m.get("type") == "layer"]
    assert len(layers) > sent and layers[-1]["view"]["epoch"] == 2


def test_a_newer_population_shown_while_templates_were_made_gets_its_own(world):
    """Scaffolding review, M1.3 item 5: e2 shown while e1's templates were being made never ran on e1's."""
    first, _ = cells(world)
    layer = layer_of(world)
    second, _ = cells(world)  # e2 is shown now
    layer.show(first)  # e1 shown again; while its templates are made, e2 is shown
    made = layer._templates

    def slow(population):
        out = made(population)
        layer.show(second)
        return out

    layer._templates = slow
    run(layer.check())  # e1's check stops: e2 is shown now
    assert layer.templates_of is None or layer.templates_of == second.id
    layer._templates = made
    run(layer.check())
    assert layer.templates_of == second.id and layer.verified_count() == (6, 6)


def test_a_view_read_back_from_another_run_is_never_the_live_frame_with_its_id(world, tmp_path):
    """Scaffolding review, M1.3 item 9: frame ids start at 1 on every start, so promoting a population
    read back after a restart could take today's live frame with the same id, and make its template
    from the wrong picture. In the run it was made in, it is still that frame."""
    from orchestrator.capture import captures
    from orchestrator.day.store import DayStore
    from orchestrator.evidence import Evidence
    from orchestrator.population import capture_of

    day = DayStore(root=tmp_path / "sessions", day="2026-10-07")
    world.evidence = Evidence(world, tmp_path / "evidence", day=day)
    population, _ = cells(world)
    wait_saved(population)
    assert population.status == "saved" and population.capture is None
    same_run = capture_of(world, population)
    assert same_run.id == population.capture_id and same_run.epoch == population.view["epoch"]

    back = Evidence(world, tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07")).get(population.id)
    back.view["run"] = "an-earlier-run"  # as read back after a restart
    live = captures(world).take(population.capture_id)  # today's frame that has its id now
    earlier = capture_of(world, back)
    assert earlier is not live and earlier.id < 0 and earlier.epoch is None
    assert earlier.jpeg == (back.path / "capture.jpg").read_bytes()
    assert capture_of(world, back) is earlier  # one capture per saved view


def test_a_population_read_back_from_another_run_is_never_verified_on_the_live_view(world, tmp_path):
    """Epochs and offsets start again with every start (finding 49): a population read back after a
    restart, shown while the live view's epoch has the same number, is not placed by that number. Its
    members stay unverified with the reason, and its captured view stays usable."""
    from orchestrator.day.store import DayStore
    from orchestrator.evidence import Evidence
    from orchestrator.population import capture_of

    world.evidence = Evidence(world, tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    population, _ = cells(world)
    wait_saved(population)
    assert population.status == "saved"
    layer = layer_of(world)
    world.frame(0.0, 0.0)  # the same picture, in the same epoch
    run(layer.check())
    assert layer.verified_count() == (6, 6)  # in its own run its members' pixels are where the slide puts them

    world.evidence = Evidence(world, tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions", day="2026-10-07"))
    back = world.evidence.get(population.id)
    back.view["run"] = "an-earlier-run"  # as read back after a restart
    layer.show(back)
    world.frame(0.0, 0.0)  # today's epoch 0: the same number, and the same picture
    assert world.latest_info.epoch == back.view["epoch"]
    run(layer.check())
    assert layer.verified_count() == (0, 6)
    why = "they were measured before the app restarted, so where they are on the slide now is not known"
    assert {p["why"] for p in layer.placement.values()} == {why} and layer.wire()["why"] == why
    assert layer.drawable(world.latest_info) == [] and layer.member_hit(world.latest_info, 380, 210) is None
    assert capture_of(world, back).jpeg == (back.path / "capture.jpg").read_bytes()  # its own view, still there
    del back.view["run"]  # no run recorded: not taken for this run's (as capture_of reads it back)
    layer.show(back)
    run(layer.check())
    assert layer.verified_count() == (0, 6)


def test_a_populations_view_follows_rejoins_only_up_to_the_epoch_the_view_is_in():
    """Review of #203, SF3: the cells follow the same links as the outlines and stop where they stop: a link out
    of the epoch the view is in now (it was rejoined there, so the link is a past one) is not followed, and the
    cells and the outlines agree. A chain is followed whole, and a cycle ends."""
    from types import SimpleNamespace

    from orchestrator.epochs import follow_links
    from orchestrator.population import _ViewOf

    pop = SimpleNamespace(view={"epoch": 3, "dx": 1.0, "dy": 2.0}, scale_id=None)
    links = {3: (2, (10.0, 0.0)), 2: (1, (0.0, 5.0))}
    v = _ViewOf(pop, links)
    assert (v.epoch, v.dx, v.dy) == (1, 11.0, 7.0)  # a chain, no bound
    v = _ViewOf(pop, links, now=2)
    assert (v.epoch, v.dx, v.dy) == (2, 11.0, 2.0)  # the view is in 2: not carried out of it
    v = _ViewOf(pop, links, now=3)
    assert (v.epoch, v.dx, v.dy) == (3, 1.0, 2.0)  # still in its own epoch: carried nowhere
    assert follow_links({1: (2, (1.0, 0.0)), 2: (1, (1.0, 0.0))}, 1, 0.0, 0.0, now=9)[0] in (1, 2)  # a cycle ends
    assert follow_links({1: (2, None)}, 1, 0.0, 0.0) == (1, 0.0, 0.0)  # no delta, no link


@pytest.mark.parametrize('change,reason', [('objective', 'objective changed'), ('epoch', 'continuity'), ('resize', 'picture size')])
def test_a_changed_live_geometry_keeps_the_population_on_its_own_capture(world, tmp_path, change, reason):
    """History must retain original pixels/scale and stop borrowing live placement after geometry changes."""
    from orchestrator.day.store import DayStore
    from orchestrator.population import capture_of, captured_picture
    from orchestrator.scale import Scale

    world.scale = Scale(DayStore(root=tmp_path))
    world.scale.set_objective(10)
    population, _ = cells(world)
    layer = layer_of(world)
    run(layer.check())
    assert layer.verified_count() == (6, 6)
    own = capture_of(world, population)
    pixels = own.jpeg
    if change == 'objective':
        world.scale.set_objective(40)
        world.frame(0, 0)
    elif change == 'epoch':
        world.frame(0, 0, epoch=1)
    else:
        world.frame(0, 0)
        world.latest_info.width = 640
    run(layer.check())
    assert layer.verified_count() == (0, 6)
    assert all(reason in p.get('why', '') for p in layer.placement.values())
    assert capture_of(world, population).jpeg == pixels
    assert own.scale['objective'] == 10
    assert captured_picture(world, population, [population.live()[0].id]) is not None
    assert list(layer.drawable(world.latest_info)) == []


def test_summary_distinguishes_estimated_scale_from_measured_calibration(world):
    population, _ = cells(world)
    # Two explicit capture metadata fixtures; an objective estimate must not be renamed a calibration.
    population.scale_id = {"umPerPx": 0.5, "objective": 40, "measured": False}
    words = summary(population, ["areaPx", "feretMaxPx"])
    assert "lengths in µm, areas in µm² by the estimated scale" in words
    assert "calibrated" not in words
    population.scale_id = {"umPerPx": 0.5, "objective": 40, "measured": True}
    assert "by the measured scale" in summary(population, ["areaPx"])


def test_compare_saved_members_uses_original_captures_after_live_epoch_changes(world):
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox import Services
    from orchestrator.brain.toolbox.magnifier import add_magnifier_tools
    from orchestrator.magnifier import Magnifier

    population, _ = cells(world)
    members = population.live()[:2]
    world.frame(0, 0, epoch=23)
    magnifier = Magnifier(world)
    registry = Registry()
    add_magnifier_tools(registry, Services(hub=world, magnifier=magnifier))

    async def go():
        result = registry.start("compare", {"a": members[0].id, "b": members[1].id}, ToolContext("chat", 1, 0))
        if result.task:
            await result.task
        return result

    result = asyncio.run(go())
    assert result.ok, result.error
    assert population.id in result.summary and "captured" in result.summary
    assert "same camera-pixel scale" in result.summary
    assert members[0].id in magnifier.picture_now()["label"] and members[1].id in magnifier.picture_now()["label"]
    from orchestrator.resources import Results
    entry = Results(None).from_run(result, ToolContext("chat", 1, world.latest_info.id), world)
    assert entry["source"]["frame"] is None  # a two-source artifact never inherits the request's live frame
    assert [v["member"] for v in entry["source"]["captures"]] == [m.id for m in members]
    assert all(v["frame"] == population.capture_id and v["population"] == population.id
               and v["revision"] == population.revision for v in entry["source"]["captures"])


def test_comparison_refuses_revision_changed_while_its_crop_was_read(world, monkeypatch, tmp_path):
    from orchestrator import population as populations
    from orchestrator.brain.registry import Registry, ToolContext
    from orchestrator.brain.toolbox import Services
    from orchestrator.brain.toolbox.magnifier import add_magnifier_tools
    from orchestrator.magnifier import Magnifier

    from orchestrator.day import DayStore
    from orchestrator.evidence import Evidence
    world.evidence = Evidence(world, tmp_path / "evidence", day=DayStore(root=tmp_path / "sessions"))
    population, _ = cells(world)
    members = population.live()[:2]
    entered, release = threading.Event(), threading.Event()
    from pathlib import Path
    wait_saved(population)
    assert population.path and (population.path / "capture.jpg").is_file()
    population.capture = None  # exercise the retained file, as after eviction from memory
    original = Path.read_bytes
    def slow_read(path):
        data = original(path)
        # Hold the comparison's first read only. Correction independently reads
        # the same retained bytes to validate integrity before it revises them.
        if path == population.path / "capture.jpg" and not entered.is_set():
            entered.set()
            assert release.wait(HANG_S)
        return data
    monkeypatch.setattr(Path, 'read_bytes', slow_read)
    magnifier = Magnifier(world)
    registry = Registry()
    add_magnifier_tools(registry, Services(hub=world, magnifier=magnifier))
    async def go():
        result = registry.start('compare', {'a': members[0].id, 'b': members[1].id}, ToolContext('chat', 1, 0))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            populations.correct(world, population, 'remove', [members[0].id], by='user')
        finally:
            release.set()
        await result.task
        return result
    result = asyncio.run(go())
    assert not result.ok and 'changed' in result.summary
    assert not magnifier.open and not magnifier.pictures
