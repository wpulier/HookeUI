"""Enrichment (session knowledge K1, contract 1): every model adds facts about the same members as column
groups beside the measurements. Groups by look come from one model's vectors only, with the number of
groups chosen by a stated criterion told with what the bench measured; a group is never a type; a correction
makes the groups stale, and every reader says so; one grouping is read everywhere, named with its model;
naming a group keeps its examples apart from the user's taught ones; vectors are kept within the memory bound
and saved, and read back."""
import asyncio
import json
import os
import threading

import numpy as np
import pytest

from orchestrator import enrichment
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.population import add_group_tools, add_population_tools
from orchestrator.operations import runner
from orchestrator.population import correct, layer_of
from orchestrator.ticket import issue, using
from tests.test_apply import run
from tests.test_corrections import keeping, saved
from tests.test_population import cells, world  # noqa: F401 (the fixture)
from tests.waiting import HANG_S, until


def unit(v):
    v = np.asarray(v, float)
    return v / np.linalg.norm(v)


def test_the_number_of_groups_is_chosen_by_silhouette_and_reported():
    rng = np.random.default_rng(0)
    centres = [unit([1, 0, 0, 0]), unit([0, 1, 0, 0]), unit([0, 0, 1, 0])]
    sizes = [20, 12, 7]
    x = np.vstack([unit(c + 0.05 * rng.standard_normal(4)) for c, n in zip(centres, sizes) for _ in range(n)])
    ids = [f"c{i}" for i in range(len(x))]
    out = enrichment.cluster(ids, x)
    assert out["k"] == 3 and out["silhouette"] > 0.7 and set(out["tried"]) == set(range(2, 9))
    assert out["sizes"] == {"g1": 20, "g2": 12, "g3": 7}  # numbered by size, largest first
    assert all(len(r) <= enrichment.REPRESENTATIVES for r in out["representatives"].values())
    assert enrichment.cluster(ids, x) == out  # deterministic
    with pytest.raises(ValueError):
        enrichment.cluster(ids[:3], x[:3])  # too few to group


def test_light_is_measured_inside_each_members_own_mask_not_its_box():
    """Review SF9: the mask is a disc in its box, and the box's corners are another colour."""
    from types import SimpleNamespace

    import cv2

    from orchestrator.measure import Mask

    image = np.zeros((60, 60, 3), np.uint8)
    image[10:30, 10:30] = (0, 0, 0)
    bits = np.zeros((20, 20), np.uint8)
    cv2.circle(bits, (10, 10), 8, 1, -1)
    image[10:30, 10:30][bits.astype(bool)] = (30, 60, 200)
    m = SimpleNamespace(id="c1", mask=Mask(bits.astype(bool), 10, 10, 1.0))
    (values,) = enrichment.light_values(image, [m]).values()
    assert values["meanRed"] == 200 and values["meanBlue"] == 30 and values["spreadRed"] == 0
    # A mask read back from disk (the member's own released) is the one used.
    released = SimpleNamespace(id="c1", mask=None)
    assert enrichment.light_values(image, [released], {"c1": m.mask})["c1"]["meanRed"] == 200
    assert enrichment.light_values(image, [released]) == {}


def stand_in_looks(monkeypatch, population):
    """Crops stand in by member id; the model's vectors say big cells look one way and small ones another."""
    area = {m.id: population.value(m.id, "areaPx") for m in population.live()}
    calls = []

    def crops(hub, pop, members):
        return {m.id: m.id.encode() for m in members}

    async def vectors(hub, jpegs, model):
        calls.append(model)
        return [list(unit([1.0, 0.02 * i, 0.0]) if area[j.decode()] > 900 else unit([0.0, 0.02 * i, 1.0]))
                for i, j in enumerate(jpegs)]

    monkeypatch.setattr(enrichment, "member_crops", crops)
    monkeypatch.setattr(enrichment, "gpu_vectors", vectors)
    return calls


def tools_for(world, taught=None):
    from types import SimpleNamespace

    registry = Registry()
    services = SimpleNamespace(hub=world, log=None, taught=taught)
    add_population_tools(registry, services)
    add_group_tools(registry, services)
    return registry


def ask(world, registry, name, args, ticket=None, ctx=None):
    with using(ticket or issue(world, "turn")):
        return run(registry.tools[name].handler(ctx or ToolContext("chat", 1, None, name), args))


def big_ids(population):
    return {m.id for m in population.live() if population.value(m.id, "areaPx") > 900}


def test_grouping_by_look_adds_column_groups_and_is_said_as_groups_not_types(world, monkeypatch):
    population, _ = cells(world)
    calls = stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    said = ask(world, registry, "group_cells", {"of": population.id})
    assert "6 cells in 2 groups by look (DINOv2 small)" in said and "silhouette" in said and "not a type" in said
    # The silhouette's score with what the bench measured, as data; never a verdict that the split is clear (SF1).
    assert enrichment.GROUPS_CALIBRATION["dinov2-small"] in said and "clear" not in said
    looks, groups = population.columns["looks:dinov2-small"], population.columns["groups:dinov2-small"]
    assert looks["columns"]["look"] == {"type": "vector", "model": "dinov2-small", "dim": 3} and looks["execution"]
    assert groups["columns"]["group"]["type"] == "category" and groups["k"] == 2 and groups["looks"] == "looks:dinov2-small"
    big = big_ids(population)
    assert {m for m, v in groups["values"].items() if v["group"] == groups["values"][next(iter(big))]["group"]} == big
    # The evidence line names each group with its picture model, in a few words (pinned: review note).
    assert "; looks (DINOv2 small); 2 look groups (DINOv2 small); light;" in population.line()
    # the same revision again: the jobs are reused, nothing embedded twice
    again = ask(world, registry, "group_cells", {"of": population.id})
    assert calls == ["dinov2-small"] and "reused" in again
    # the query tool selects by the category, named with its model
    g = groups["values"][next(iter(big))]["group"]
    listed = ask(world, registry, "query_population", {"of": population.id, "where": {"group": g}})
    assert f"{len(big)} members (look group (DINOv2 small) {g})" in listed


def test_at_n_after_group_cells_names_the_grouped_population(world, monkeypatch):
    """Review SF5: "@1" after group_cells is the population it grouped, not "made nothing (it failed)"."""
    from orchestrator.brain.registry import step_output

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ctx = ToolContext("chat", 1, None, "group these, then how do they differ")
    ctx.step = 1
    ctx.steps.tools.update({1: "group_cells", 2: "describe_groups"})
    ask(world, registry, "group_cells", {"of": population.id}, ctx=ctx)
    later = ToolContext("chat", 1, None, ctx.question, step=2, steps=ctx.steps)
    assert step_output(later, "@1") == population.id
    assert "2 groups by look" in ask(world, registry, "describe_groups", {"of": "@1"}, ctx=later)


def test_a_model_never_measured_on_the_groups_bench_is_said_so():
    assert "dinov3-small" not in enrichment.GROUPS_CALIBRATION  # nothing borrowed from another model's numbers


def test_describe_groups_gives_facts_per_group_and_never_a_type(world, monkeypatch):
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    said = ask(world, registry, "describe_groups", {"of": population.id})
    assert "never types" in said and said.count("cells; medians: area") == 2 and "(DINOv2 small;" in said
    assert "most different between groups" in said and "area" in said.split("most different")[1]
    for word in ("neutrophil", "lymphocyte", "healthy", "dead", "viable"):
        assert word not in said.lower()


def test_a_correction_makes_the_groups_stale_and_they_are_made_again_on_demand(world, monkeypatch):
    population, _ = cells(world)
    calls = stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    correct(world, population, "remove", [population.live()[0].id], by="user")
    group = population.columns["groups:dinov2-small"]
    assert population.stale(group) and enrichment.current(population, "groups:dinov2-small") is None
    assert population.value(population.live()[0].id, "group") is None  # a stale group is never read
    assert population.value(population.live()[0].id, "group@dinov2-small") is None  # by its model's name either
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "describe_groups", {"of": population.id})
    assert "made before your correction" in str(refused.value) and refused.value.next == "group them again"
    ask(world, registry, "group_cells", {"of": population.id})
    assert len(calls) == 2 and enrichment.current(population, "groups:dinov2-small")["revision"] == 1


def test_stale_groups_are_refused_by_every_reader_and_their_colours_go(world, monkeypatch):
    """Review B3: one stale check (a group made at revision r is stale at a later revision), used by the query,
    the layer's category colour and legend, naming a group, describe_groups and the evidence line."""
    from tests.test_screen import present, setup

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    view, screen, studio = setup(world)
    present(view, world, what=population.id, do="show")
    present(view, world, what=population.id, do="colour", value="look groups")
    layer = layer_of(world)
    assert layer.style["colour"] == "category" and layer.confirmed_style["colour"] == "category"
    correct(world, population, "remove", [population.live()[0].id], by="user")
    # The layer goes back to each cell's own colour, as sent to the studio: no stale legend, none to put back.
    assert layer.style["colour"] == "id" and "legend" not in layer.style and "values" not in layer.style
    assert layer.confirmed_style["colour"] == "id"
    sent = [m for m in world.sent if m.get("type") == "layer" and m.get("population") == population.id][-1]["style"]
    assert sent["colour"] == "id" and "legend" not in sent
    # The evidence line names no stale group.
    assert "look groups" not in population.line() and "light" not in population.line().split("measured")[1]
    for name, args in (("query_population", {"of": population.id, "where": {"group": "g1"}}),
                       ("query_population", {"of": population.id, "by": "meanRed"}),
                       ("describe_groups", {"of": population.id}),
                       ("group_cells", {"of": population.id, "group": "g1", "name": "platelets"})):
        with pytest.raises(ToolFailed) as refused:
            ask(world, registry, name, args)
        assert "made before your correction" in str(refused.value) and refused.value.next == "group them again", name
    with pytest.raises(ToolFailed) as refused:
        present(view, world, what=population.id, do="colour", value="look groups")
    assert "made before your correction" in str(refused.value)


def test_vectors_of_two_models_are_never_compared(world, monkeypatch):
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    world.settings.embed = "bioclip-2"
    # No BioCLIP 2 groups yet: the groups that exist (DINOv2 small's) are described, named with their model,
    # never as BioCLIP's (review SF2: not "not grouped by look yet" while groups exist).
    said = ask(world, registry, "describe_groups", {"of": population.id})
    assert "(DINOv2 small;" in said
    ask(world, registry, "group_cells", {"of": population.id})
    groups = population.columns["groups:bioclip-2"]
    assert groups["looks"] == "looks:bioclip-2" and "looks:dinov2-small" in population.columns
    assert "(BioCLIP 2;" in ask(world, registry, "describe_groups", {"of": population.id})


def test_one_grouping_is_read_everywhere_named_with_its_model(world, monkeypatch):
    """Review SF2: the request's own choice (`use`), else option EMBED's, else the newest by when it was made
    (never dict order), the same for describing, colouring, querying and naming; said with its model."""
    from tests.test_screen import present, setup

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})  # DINOv2 small, 2 groups
    world.settings.embed = "bioclip-2"
    ask(world, registry, "group_cells", {"of": population.id, "groups": 3})  # BioCLIP 2, 3 groups, made later
    world.settings.embed = "dinov2-small"
    ask(world, registry, "group_cells", {"of": population.id})  # reused: DINOv2's stay the older ones
    assert population.columns["groups:dinov2-small"]["madeAt"] < population.columns["groups:bioclip-2"]["madeAt"]
    view, _screen, _studio = setup(world)
    present(view, world, what=population.id, do="show")

    def readers():
        said = ask(world, registry, "describe_groups", {"of": population.id})
        coloured = present(view, world, what=population.id, do="colour", value="look groups")
        listed = ask(world, registry, "query_population", {"of": population.id, "where": {"group": ["g1", "g2", "g3"]}})
        return said, coloured, listed

    said, coloured, listed = readers()  # EMBED's own (DINOv2 small), though BioCLIP's are newer
    assert "2 groups by look (DINOv2 small;" in said and "(DINOv2 small, 2 groups)" in coloured
    assert "look group (DINOv2 small) g1 or g2 or g3" in listed
    world.settings.embed = "dinov3-small"  # none made with it: the newest, BioCLIP 2's, everywhere
    said, coloured, listed = readers()
    assert "3 groups by look (BioCLIP 2;" in said and "(BioCLIP 2, 3 groups)" in coloured
    assert "6 members (look group (BioCLIP 2) g1 or g2 or g3)" in listed
    # The request's own choice comes first, and is marked used.
    world.settings.embed = "bioclip-2"
    ticket = issue(world, "turn")
    ticket.choices["looks"] = {"implementation": "dinov2-small", "policy": "raw", "origin": "user-chosen", "used": False,
                               "label": "DINOv2 small"}
    assert "2 groups by look (DINOv2 small;" in ask(world, registry, "describe_groups", {"of": population.id}, ticket)
    assert ticket.choices["looks"]["used"]
    # A choice with no groups of its own is refused, never another model's in its place (ADR 0011).
    ticket = issue(world, "turn")
    ticket.choices["looks"] = {"implementation": "dinov3-small", "policy": "raw", "origin": "user-chosen", "used": False,
                               "label": "DINOv3 small"}
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "describe_groups", {"of": population.id}, ticket)
    assert "DINOv3 small, the picture model this request chose" in str(refused.value)
    # The evidence line says both, each with its model.
    assert "; 2 look groups (DINOv2 small);" in population.line() and "; 3 look groups (BioCLIP 2);" in population.line()


def test_vectors_are_reserved_saved_and_read_back(world, monkeypatch, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    # Hold the filesystem's commit point. The background writer may otherwise
    # legitimately finish and release its reservation before group_cells returns.
    manifest = population.path / "manifest.json"
    before = manifest.read_bytes()
    committing, release = threading.Event(), threading.Event()
    replace = os.replace

    def held_replace(source, destination):
        if destination == manifest:
            committing.set()
            if not release.wait(60):
                raise TimeoutError("test did not release evidence manifest replacement")
        return replace(source, destination)

    with monkeypatch.context() as saving:
        saving.setattr(os, "replace", held_replace)
        try:
            ask(world, registry, "group_cells", {"of": population.id})
            assert committing.wait(60), "the evidence save did not reach its atomic commit"
            assert population.status == "saving"
            assert population.vector_hold is not None and population.vector_hold.nbytes > 0
            assert manifest.read_bytes() == before  # not published until the atomic replacement
        finally:
            release.set()
            saved(population)
    assert population.vectors == {} and population.vector_hold is None  # released once saved
    kept = world.evidence.vectors_of(population, "looks:dinov2-small")
    assert len(kept["ids"]) == 6 and kept["array"].shape == (6, 3)
    # A second model's looks, saved later, keep the first's (review SF9: vectors.npz never drops a saved group).
    world.settings.embed = "bioclip-2"
    ask(world, registry, "group_cells", {"of": population.id})
    saved(population)
    from orchestrator.evidence import Evidence

    again = Evidence(world, tmp_path / "evidence", day=world.evidence.day)
    (pid,) = again.read_back()
    back = again.get(pid)
    assert back.columns["groups:dinov2-small"]["k"] == 2 and back.columns["groups:bioclip-2"]["k"] == 2
    for model in ("dinov2-small", "bioclip-2"):
        assert again.vectors_of(back, f"looks:{model}")["array"].shape == (6, 3)


def test_a_population_whose_vectors_file_is_gone_is_read_back_without_its_looks(world, monkeypatch, tmp_path):
    """Review note: a missing vectors.npz no longer loses the masks, the measurements and the revisions."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    ask(world, tools_for(world), "group_cells", {"of": population.id})
    saved(population)
    correct(world, population, "remove", [population.live()[0].id], by="user")
    saved(population)
    (population.path / "vectors.npz").unlink()
    from orchestrator.evidence import Evidence

    again = Evidence(world, tmp_path / "evidence", day=world.evidence.day)
    (pid,) = again.read_back()
    back = again.get(pid)
    assert back.revision == 1 and len(back.live()) == 5 and back.value(back.live()[0].id, "areaPx")
    assert "looks:dinov2-small" not in back.columns and again.reload_masks(back)
    assert any("looks were not read back" in p for p in again.problems)


def test_naming_a_group_keeps_its_examples_apart_from_your_taught_ones(world, monkeypatch, tmp_path):
    """Review B1 (and SF7, SF9): the group's representatives are the app's picks, so they never become taught
    examples (no "the user's own" line, no 72% calibration, `ready()` stays false); every cell gets the group
    name's nearest match, a label only from the model's measured floor, written by an execution of its own."""
    from orchestrator.seen import THRESHOLDS
    from orchestrator.taught import TaughtLabels, context_line

    monkeypatch.setattr(enrichment, "REPRESENTATIVES", 2)  # fewer than the group, so examples are not the whole group
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)

    async def embed(jpegs, model="dinov2-small"):
        return [list(unit([1.0, 0.0, 0.0])) for _ in jpegs]

    taught = TaughtLabels(tmp_path / "taught", today=lambda: "2026-10-07", embed=embed, hub=world)
    registry = tools_for(world, taught)
    ask(world, registry, "group_cells", {"of": population.id})
    groups = population.columns["groups:dinov2-small"]
    big = big_ids(population)
    g = groups["values"][next(iter(big))]["group"]
    reps = groups["representatives"][g]
    assert len(reps) == 2 and set(reps) < big
    ctx = ToolContext("chat", 1, None, "those are big round cells")
    ctx.step = 1
    ctx.steps.tools.update({1: "group_cells", 2: "highlight"})
    said = ask(world, registry, "group_cells", {"of": population.id, "group": g, "name": "big round cells"}, ctx=ctx)
    # Nothing reaches taught.py: no example, no file, no cached crop, no taught line for a click.
    assert taught.counts() == {} and not taught.ready() and not taught.recent
    assert not (tmp_path / "taught" / "2026-10-07" / "examples.jsonl").exists()
    assert context_line(taught.match_vector(list(unit([1.0, 0.0, 0.0])))) == ""  # no taught line for a click
    # The examples are exactly the representatives, kept with the population.
    column = population.columns["named:dinov2-small"]
    (entry,) = column["named"]
    assert entry["members"] == reps and entry["name"] == "big round cells" and entry["group"] == g
    # Said as the group name's nearest match, never as cells the user confirmed.
    assert said.startswith('"big round cells" is kept for the 2 cells nearest the centre of')
    assert "not cells you confirmed" in said and "closest to the group you named" in said
    assert "borrowed from the seen-before bench" in said and "never their names" in said
    # A label only from DINOv2's measured floor: the third big cell is near; the small ones (similarity ~0.0x) not.
    floor = THRESHOLDS["dinov2-small"]
    assert column["columns"]["yourLabel"]["floor"] == floor
    for mid in population.members:
        row = column["values"][mid]
        assert ("yourLabel" in row) == (row["similarity"] >= floor)
        assert (row.get("yourLabel") == "big round cells") == (mid in big)
    assert "1 of the 4 other cells are closest to the group you named at or above 0.93" in said
    # Never written into a member, an outline or a label.
    assert not any("big round cells" in json.dumps(m.extras) for m in population.members.values())
    assert not any(getattr(o, "label", "") == "big round cells" for o in world.scene.objects.values())
    # Written with its own execution through the runner, resting on the looks and the groups it used.
    made = runner(world).get(column["execution"])
    assert made.operation == "your-label" and made.executed == "nearest-named" and not made.attempts[-1].empty
    assert column["restsOn"] == {"looks": population.columns["looks:dinov2-small"]["execution"],
                                 "groups": groups["execution"]}
    assert "named groups (DINOv2 small)" in population.line()
    from orchestrator.brain.registry import step_output

    later = type("C", (), {**ctx.__dict__, "step": 2})()
    assert step_output(later, "@1") == population.id  # "@n" names the named population (review SF5)


def test_with_no_measured_floor_no_cell_is_given_the_label(world, monkeypatch):
    """Review B1: DINOv3 small was never measured on the seen-before bench, so no floor is invented."""
    # DINOv3's weights are gated: it runs only with HF_TOKEN (operations.LOOKS needs=, the capability check of
    # 2026-10-11). The looks here are stand-ins; what this checks is the floor, so a stand-in token is enough.
    monkeypatch.setenv("HF_TOKEN", "stand-in")
    world.settings.embed = "dinov3-small"
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    said = ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "big round cells"})
    column = population.columns["named:dinov3-small"]
    assert column["floor"] is None and not any("yourLabel" in v for v in column["values"].values())
    assert all("similarity" in v for v in column["values"].values())
    assert "unmeasured with DINOv3 small, so no cell is given the label" in said


def test_the_nearest_named_group_is_measured_by_its_nearest_example():
    ids = ["a", "b", "c", "d", "e"]
    x = np.array([unit([1, 0, 0]), unit([0.999, 0.04, 0]), unit([0.6, 0.8, 0]), unit([0, 0, 1]), unit([0, 1, 0])])
    out = enrichment.nearest_named(ids, x, [{"name": "round", "members": ["a"]}, {"name": "flat", "members": ["d"]}], 0.93)
    assert out["b"]["yourLabel"] == "round" and out["b"]["similarity"] > 0.99
    assert "yourLabel" not in out["c"] and out["c"]["similarity"] == pytest.approx(0.6, abs=1e-3)
    assert out["d"]["yourLabel"] == "flat"
    assert all("yourLabel" not in v for v in enrichment.nearest_named(ids, x, [{"name": "round", "members": ["a"]}], None).values())
    # Its nearest example, never the mean of a name's examples (the re-check's survivor): "round" has a near one
    # and a far one; b is 0.999 alike to the near one (the mean of the two would be about 0.5).
    two = enrichment.nearest_named(ids, x, [{"name": "round", "members": ["a", "e"]}], 0.93)
    assert two["b"]["similarity"] > 0.99 and two["b"]["yourLabel"] == "round"


def test_an_equal_similarity_goes_to_the_newer_name_not_the_later_spelling():
    """The K1 re-check, SF3: ties are broken by recency."""
    ids = ["a", "b"]
    x = np.array([unit([1, 0, 0]), unit([1, 0, 0])])
    older_big = [{"name": "big", "members": ["a"]}, {"name": "aardvark", "members": ["b"]}]
    assert enrichment.nearest_named(ids, x, older_big, 0.93)["a"]["yourLabel"] == "aardvark"
    older_aardvark = [{"name": "aardvark", "members": ["a"]}, {"name": "big", "members": ["b"]}]
    assert enrichment.nearest_named(ids, x, older_aardvark, 0.93)["a"]["yourLabel"] == "big"


def test_every_enrichment_execution_records_what_it_made(world, monkeypatch):
    """Review B2: looks, groups, light, names and your labels are recorded as made (executed, not empty)."""
    from orchestrator import zeroshot
    from orchestrator.operations import GROUPS, LIGHT, LOOKS, NAMES, YOUR_LABEL, _has_output

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    world.scene.context["specimen"] = "a blood smear"

    async def rank(hub, jpegs, names, template):
        return [[[n, 1.0 / len(names)] for n in names] for _ in jpegs]

    monkeypatch.setattr(zeroshot, "gpu_rank", rank)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    ask(world, registry, "describe_groups", {"of": population.id, "names": True})
    ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "big"})
    impls = {"looks": "dinov2-small", "groups": "kmeans-silhouette", "light": "channel-stats", "names": "medsiglip-448",
             "your-label": "nearest-named"}
    made = {e.operation: e for e in runner(world).executions.values()}
    for operation, impl in impls.items():
        record = made[operation]
        assert record.executed == impl and not record.attempts[-1].empty and record.made_by()["implementation"] == impl
    for gid in ("looks:dinov2-small", "groups:dinov2-small", "light", "names:groups:dinov2-small", "named:dinov2-small"):
        assert runner(world).get(population.columns[gid]["execution"]).executed is not None
    # An output with nothing in it is empty, by each operation's own output keys.
    for op, key in ((LOOKS, "vectors"), (GROUPS, "groups"), (LIGHT, "values"), (NAMES, "byGroup"), (YOUR_LABEL, "values")):
        assert not _has_output({key: {}}, op) and _has_output({key: {"c1": 1}}, op) and not _has_output({"polygons": [[1]]}, op)


def test_a_kept_group_is_reused_as_evidence_and_its_use_recorded(world, monkeypatch):
    """Finding K1-3 (review SF9): the second ask reuses the kept groups, logged as a use of their executions."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    records = []
    runner(world).log = records.append
    ask(world, registry, "group_cells", {"of": population.id})
    uses = {r["operation"]: r["execution"] for r in records if r["kind"] == "execution-use" and r.get("from") == "evidence"}
    for operation, gid in (("looks", "looks:dinov2-small"), ("groups", "groups:dinov2-small"), ("light", "light")):
        assert uses[operation] == population.columns[gid]["execution"]
    assert not [r for r in records if r["kind"] == "execution"]  # nothing ran again


def test_masks_are_read_back_only_for_light_off_the_loop_and_never_kept(world, monkeypatch, tmp_path):
    """Review SF6: once saved, the masks are released; light reads them back in its thread, in reserved room,
    and they stay released (looks, groups and names never read them)."""
    from orchestrator.capture import captures

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    assert all(m.mask is None for m in population.members.values())
    stand_in_looks(monkeypatch, population)
    memory = captures(world).memory
    reserved = []
    reserve = memory.reserve

    def recording(cls, n, why):
        made = reserve(cls, n, why)
        reserved.append((cls, n, why, made))
        return made

    monkeypatch.setattr(memory, "reserve", recording)
    ask(world, tools_for(world), "group_cells", {"of": population.id})
    assert all(m.mask is None for m in population.members.values())  # never resident again
    assert population.value(population.live()[0].id, "meanRed") is not None  # measured from the saved masks
    masks = [r for r in reserved if "masks, read back" in r[2]]
    assert masks and all(cls == "analysis" and n > 0 and made.released for cls, n, _why, made in masks)
    grouping = [r for r in reserved if r[2].startswith("grouping")]
    assert grouping and all(n >= enrichment.cluster_bytes(6, 3) and made.released for _cls, n, _why, made in grouping)


def test_columns_keep_their_case_categories_are_lists_and_a_lone_number_is_a_lower_bound(world, monkeypatch):
    """Review SF3."""
    from orchestrator.population import column_of
    from tests.test_screen import present, setup

    assert column_of("meanRed") == "meanRed" and column_of("Size") == "areaPx"  # its case kept, without a population too

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    assert "score, group (once grouped)" in registry.tools["query_population"].description
    # Before any grouping: a lone number is a lower bound, as before K1 ("area": "500" is ≥ 500).
    listed = ask(world, registry, "query_population", {"of": population.id, "where": {"area": "500"}})
    assert "3 members (areaPx ≥ 500)" in listed
    # A measurement's own name, as the population has it (the re-check's survivor: known_columns keeps its case).
    assert "6 members (feretMaxPx ≥ 0)" in ask(world, registry, "query_population",
                                               {"of": population.id, "where": {"feretMaxPx": [0, None]}})
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "query_population", {"of": population.id, "where": {"area": "big"}})
    assert "is not a number" in str(refused.value)
    ask(world, registry, "group_cells", {"of": population.id})
    listed = ask(world, registry, "query_population", {"of": population.id, "where": {"meanRed": [0, None]}, "by": "meanRed"})
    assert "6 members (meanRed ≥ 0, largest meanRed first)" in listed
    assert "6 members" in ask(world, registry, "query_population", {"of": population.id, "where": {"MEANRED": [0, None]}})
    listed = ask(world, registry, "query_population", {"of": population.id, "where": {"group": ["g1", "g2"]}})
    assert "6 members (look group (DINOv2 small) g1 or g2)" in listed
    assert "meanRed: mean" in ask(world, registry, "query_population", {"of": population.id, "columns": ["meanred"]})  # the mean first, then the median (population.summary)
    view, _screen, studio = setup(world)
    present(view, world, what=population.id, do="show")
    said = present(view, world, what=population.id, do="colour", value="meanRed")
    assert said.endswith("coloured by meanRed, with a legend") and studio.items[f"layer:{population.id}"]["style"]["column"] == "meanRed"
    ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "big"})
    said = present(view, world, what=population.id, do="colour", value="your label")
    style = layer_of(world).style
    assert said.endswith("coloured by nearest group you named (DINOv2 small) (1 name), with a legend")
    assert style["column"] == "yourLabel"
    assert style["legend"]["categories"] == [{"id": "big", "n": 3, "label": "big"}]
    assert "3 members" in ask(world, registry, "query_population", {"of": population.id, "where": {"yourLabel": "big"}})


def test_showing_another_population_drops_a_category_colour(world, monkeypatch):
    """Review SF4 (finding 74): colours by a category were the old members'; the next population is drawn in
    each member's own colour, not all grey under "By look group"."""
    population, _ = cells(world)
    layer = layer_of(world)
    layer.show(population)
    layer.style = {"opacity": 0.8, "colour": "category", "column": "group", "columnGroup": "groups:dinov2-small",
                   "legend": {"label": "look groups", "categories": [{"id": "g1", "n": 3}]}, "values": {"c1": 0}}
    world.frame(5.0, 0.0)
    other, _ = cells(world)
    assert other.id != population.id
    layer.show(other)
    assert layer.style == {"opacity": 0.8, "colour": "id"} and layer.confirmed_style == layer.style


def test_too_few_to_group_says_what_helps_not_ask_again(world, monkeypatch):
    """Review note: the runner keeps an adapter's own next step."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    correct(world, population, "remove", [m.id for m in population.live()[:3]], by="user")
    with pytest.raises(ToolFailed) as refused:
        ask(world, tools_for(world), "group_cells", {"of": population.id})
    assert "too few to group" in str(refused.value) and refused.value.next == "outline more cells, or a view with more of them"


def test_the_view_says_a_look_group_legend_in_words():
    from orchestrator.brain.toolbox.present import category_style
    from orchestrator.evidence import Population

    population = Population("e1", "cells", "cellpose-sam", {}, 1, {"at": 0}, None, None, {})
    population.columns["groups:bioclip-2"] = {"revision": 0, "columns": {"group": {"type": "category"}},
                                              "values": {}, "sizes": {"g1": 2, "g2": 1}, "model": "bioclip-2"}
    style, words = category_style(population, "group@bioclip-2", "groups:bioclip-2")
    assert words == "coloured by look group (BioCLIP 2, 2 groups), with a legend"
    assert style["legend"] == {"label": "look groups (BioCLIP 2)", "categories": [{"id": "g1", "n": 2}, {"id": "g2", "n": 1}]}


def test_the_cells_are_coloured_by_look_group_with_a_legend_through_the_verbs(world, monkeypatch):
    from tests.test_screen import present, setup

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    ask(world, tools_for(world), "group_cells", {"of": population.id})
    registry, screen, studio = setup(world)
    present(registry, world, what=population.id, do="show")
    said = present(registry, world, what=population.id, do="colour", value="look groups")
    assert said.endswith("coloured by look group (DINOv2 small, 2 groups), with a legend")
    style = studio.items[f"layer:{population.id}"]["style"]
    assert style["colour"] == "category" and style["column"] == "group"
    sent = [m for m in world.sent if m.get("type") == "layer" and m.get("population") == population.id][-1]["style"]
    assert sent["legend"]["categories"][0]["id"] == "g1" and set(sent["values"].values()) == {0, 1}
    assert sent["legend"]["label"] == "look groups (DINOv2 small)" and sent["columnGroup"] == "groups:dinov2-small"


def test_a_number_of_groups_asked_for_is_made_and_said_so(world, monkeypatch):
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    said = ask(world, registry, "group_cells", {"of": population.id, "groups": 3})
    assert "in 3 groups by look" in said and "3 groups as asked" in said and "reused" not in said
    assert population.columns["groups:dinov2-small"]["asked"] == 3
    with pytest.raises(ToolFailed):
        ask(world, registry, "group_cells", {"of": population.id, "groups": 12})


def test_medsiglips_names_per_group_are_data_with_their_calibration(world, monkeypatch):
    from orchestrator import zeroshot

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    world.scene.context["specimen"] = "a blood smear"
    asked = []

    async def rank(hub, jpegs, names, template):
        asked.append((len(jpegs), template))
        return [[["platelet", 0.5], ["neutrophil", 0.3]] + [[n, 0.2 / (len(names) - 2)] for n in names
                                                            if n not in ("platelet", "neutrophil")] for _ in jpegs]

    monkeypatch.setattr(zeroshot, "gpu_rank", rank)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    said = ask(world, registry, "describe_groups", {"of": population.id, "names": True})
    assert said.count("MedSigLIP's names (data): platelet 0.50, neutrophil 0.30") == 2
    assert "MedSigLIP's names are data, not names: on 120 pictures of 8 kinds of blood cell" in said
    # Its calibration was measured one picture at a time, never on a group's average (review SF8).
    assert "measured one picture at a time" in said and "averaged, which is not measured" in said
    assert asked and all(t == "a {} in a blood smear" for _, t in asked)
    for m in population.live():
        assert population.value(m.id, "names") is None  # never written onto a member as its name
    assert "MedSigLIP names (DINOv2 small groups)" in population.line()
    ask(world, registry, "group_cells", {"of": population.id, "groups": 3})  # regrouped: names of the old groups unsaid
    assert "MedSigLIP names" not in population.line()
    world.scene.context["specimen"] = "onion epidermis"
    population.columns.pop("names:groups:dinov2-small", None)
    said = ask(world, registry, "describe_groups", {"of": population.id, "names": True})
    assert "no MedSigLIP names: MedSigLIP has candidate names only for blood smears and Pap smears" in said


def test_medsiglip_powers_describing_groups_on_the_capabilities_page():
    """Review SF8: MedSigLIP (terms tier) is shown as powering "how the groups differ", fixed in the code and not
    the Medical names setting; describing groups declares the column group it writes."""
    from orchestrator import capabilities, options
    from orchestrator.produces import TOOLS

    w = capabilities.wire(options.Settings(), {})
    roles = {r["id"]: r for r in w["roles"]}
    caps = {c["id"]: c for c in w["capabilities"]}
    assert caps["describe-groups"]["roles"] == ["embed", "fixed:group-names"]
    names = roles["fixed:group-names"]
    assert names["label"] == "MedSigLIP 448" and names["tier"] == "terms" and "not the Medical names setting" in names["purpose"]
    assert names["operation"]["id"] == "names" and "describe-groups" in names["powers"]
    assert TOOLS["describe_groups"] == "population"
    assert caps["colour-by-look-group"]["roles"] == ["embed"] and caps["cells-by-look-group"]["roles"] == ["embed"]


def test_the_evidence_line_is_few_words(world, monkeypatch):
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    ask(world, tools_for(world), "group_cells", {"of": population.id})
    assert enrichment.kinds(population) == ["looks (DINOv2 small)", "2 look groups (DINOv2 small)", "light"]


def test_named_groups_survive_a_restart_with_the_population(world, monkeypatch, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "big"})
    saved(population)
    from orchestrator.evidence import Evidence

    again = Evidence(world, tmp_path / "evidence", day=world.evidence.day)
    (pid,) = again.read_back()
    back = again.get(pid)
    assert back.columns["named:dinov2-small"]["named"][0]["name"] == "big"
    assert asyncio.iscoroutinefunction(enrichment.run_your_label)


# --- the K1 re-check at d73f9de (by the partner) ----------------------------------------------------------
def correcting_during(monkeypatch, world, population, target: str, member=None):
    """A correction that lands while `target` (a function the job calls) runs: the cell is removed then."""
    original = getattr(enrichment, target)
    done = []

    def wrapped(*args, **kwargs):
        out = original(*args, **kwargs)
        if not done:
            done.append(True)
            correct(world, population, "remove", [member or population.live()[0].id], by="user")
        return out

    async def awrapped(*args, **kwargs):
        out = await original(*args, **kwargs)
        if not done:
            done.append(True)
            correct(world, population, "remove", [member or population.live()[0].id], by="user")
        return out

    monkeypatch.setattr(enrichment, target, awrapped if asyncio.iscoroutinefunction(original) else wrapped)
    return done


def test_a_correction_during_the_embedding_leaves_the_looks_stale_and_is_said(world, monkeypatch):
    """Re-check B1, probe N7: the groups are stamped with the revision and members they were made from."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    gone = population.live()[0].id
    correcting_during(monkeypatch, world, population, "gpu_vectors", gone)
    registry = tools_for(world)
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "group_cells", {"of": population.id})
    assert "was corrected while its looks were being made" in str(refused.value) and refused.value.next == "group them again"
    looks = population.columns["looks:dinov2-small"]
    assert looks["revision"] == 0 and gone in looks["members"] and population.stale(looks)
    assert "groups:dinov2-small" not in population.columns
    said = ask(world, registry, "group_cells", {"of": population.id})  # again: made from the cells as they are
    assert "5 cells in 2 groups" in said and gone not in population.columns["groups:dinov2-small"]["members"]
    assert gone not in json.dumps(population.columns["groups:dinov2-small"]["representatives"])


def test_a_correction_during_the_clustering_is_said_and_never_coloured(world, monkeypatch):
    """Re-check B1, probe N7b."""
    from tests.test_screen import present, setup

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    correcting_during(monkeypatch, world, population, "cluster")
    registry = tools_for(world)
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "group_cells", {"of": population.id})
    assert "was corrected while its look groups were being made" in str(refused.value)
    assert population.stale(population.columns["groups:dinov2-small"])
    view, _screen, _studio = setup(world)
    present(view, world, what=population.id, do="show")
    with pytest.raises(ToolFailed) as refused:
        present(view, world, what=population.id, do="colour", value="look groups")
    assert "made before your correction" in str(refused.value)
    with pytest.raises(ToolFailed):
        ask(world, registry, "describe_groups", {"of": population.id})


def test_a_correction_while_the_colours_are_measured_is_said_in_words(world, monkeypatch):
    """Re-check B1, probe N7c: never a raw AttributeError."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    correcting_during(monkeypatch, world, population, "light_values")
    with pytest.raises(ToolFailed) as refused:
        ask(world, tools_for(world), "group_cells", {"of": population.id})
    assert "made before your correction" in str(refused.value) and refused.value.next == "group them again"


def test_a_correction_while_a_group_is_named_is_said_and_the_names_are_stale(world, monkeypatch):
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    correcting_during(monkeypatch, world, population, "nearest_named")
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "big"})
    assert "nearest named groups were being made" in str(refused.value)
    assert population.stale(population.columns["named:dinov2-small"])


def test_a_group_made_from_an_older_revision_never_replaces_a_newer_one(world):
    population, _ = cells(world)
    population.revision = 1
    newer = {"execution": "x-new", "revision": 1, "values": {}, "columns": {}}
    population.columns["light"] = newer
    kept = enrichment.add_group(world, population, "light", execution="x-old", implementation="channel-stats", version="1",
                                basis="", columns={}, values={}, made_from=(0, []))
    assert kept is newer and population.columns["light"]["execution"] == "x-new"


def test_the_layer_never_sends_colours_by_a_stale_or_replaced_group(world, monkeypatch):
    """Re-check SF2, probes R3b, N1 and N2: checked wherever the style is sent, shown or hidden."""
    from tests.test_screen import present, setup

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    view, _screen, _studio = setup(world)
    layer = layer_of(world)

    def sent():
        return [m for m in world.sent if m.get("type") == "layer" and m.get("population") == population.id][-1]["style"]

    # Regrouped while shown (N1): the 2-group legend goes at once.
    present(view, world, what=population.id, do="show")
    present(view, world, what=population.id, do="colour", value="look groups")
    assert sent()["colour"] == "category" and sent()["columnExecution"]
    ask(world, registry, "group_cells", {"of": population.id, "groups": 3})
    assert sent()["colour"] == "id" and "legend" not in sent() and layer.style["colour"] == "id"
    # Your nearest named groups replaced by naming another group (N2): the old legend goes.
    ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "big"})
    present(view, world, what=population.id, do="colour", value="your label")
    assert sent()["colour"] == "category"
    ask(world, registry, "group_cells", {"of": population.id, "group": "g2", "name": "small"})
    assert sent()["colour"] == "id" and layer.confirmed_style["colour"] == "id"
    # Coloured, hidden, corrected, shown again (R3b): never the stale legend.
    present(view, world, what=population.id, do="colour", value="look groups")
    present(view, world, what=population.id, do="hide")
    correct(world, population, "remove", [population.live()[0].id], by="user")
    present(view, world, what=population.id, do="show")
    assert sent()["colour"] == "id" and "legend" not in sent()


def test_your_earlier_names_are_carried_over_and_said(world, monkeypatch):
    """Re-check SF3, probes N3 and N3b: a name is never dropped unsaid; until a group is named again after a
    correction, the stale names are refused (the re-check's survivor), never answered "0 members"."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    big = big_ids(population)
    groups = population.columns["groups:dinov2-small"]
    g_big = groups["values"][next(iter(big))]["group"]
    ask(world, registry, "group_cells", {"of": population.id, "group": g_big, "name": "big"})
    removed = sorted(big)[0]
    correct(world, population, "remove", [removed], by="user")
    ask(world, registry, "group_cells", {"of": population.id})  # regrouped: current groups, stale names
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "query_population", {"of": population.id, "where": {"yourLabel": "big"}})
    assert "nearest named groups were made before your correction" in str(refused.value)
    assert refused.value.next == "name a group again (your earlier names are carried over)"
    groups = population.columns["groups:dinov2-small"]
    g_small = next(g for g, reps in groups["representatives"].items() if not set(reps) & big)
    said = ask(world, registry, "group_cells", {"of": population.id, "group": g_small, "name": "small"})
    assert 'your earlier names kept: "big" (2 of its 3 example cells left)' in said
    (kept, new) = population.columns["named:dinov2-small"]["named"]
    assert kept["name"] == "big" and removed not in kept["members"] and new["name"] == "small"
    listed = ask(world, registry, "query_population", {"of": population.id, "where": {"yourLabel": "big"}})
    assert "2 members (nearest group you named (DINOv2 small) big)" in listed
    assert "c" in listed and "nearest group you named (DINOv2 small) big" in listed.split("):", 1)[1]


def regrouped(population, representatives: dict) -> dict:
    """The look groups as another grouping (a new execution) with these representatives: a regrouping whose
    split is known, so which of a name's cells a new group takes is known."""
    import copy

    groups = copy.deepcopy(population.columns["groups:dinov2-small"])
    groups["execution"] = "x-regrouped"
    groups["representatives"] = representatives
    groups["values"] = {m: {"group": g} for g, ms in representatives.items() for m in ms}
    groups["sizes"] = {g: len(ms) for g, ms in representatives.items()}
    population.columns["groups:dinov2-small"] = groups
    return groups


def test_a_newer_name_for_the_same_cells_replaces_the_older_one_and_says_so(world, monkeypatch):
    """Re-check SF3, probe N4b: "aardvark" for every one of the cells named "big" replaces it, whatever the
    spelling; asked for, "big" says what replaced it (the second re-check's note), never a bare "0 members"."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    big = sorted(big_ids(population))
    g_big = population.columns["groups:dinov2-small"]["values"][big[0]]["group"]
    ask(world, registry, "group_cells", {"of": population.id, "group": g_big, "name": "big"})
    small = sorted(m.id for m in population.live() if m.id not in big)
    regrouped(population, {"g1": big, "g2": small})
    said = ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "aardvark"})
    assert 'it replaces your earlier "big" (every one of its example cells is in this group)' in said
    column = population.columns["named:dinov2-small"]
    assert [e["name"] for e in column["named"]] == ["aardvark"] and column["replaced"] == ["big"]
    assert {column["values"][m].get("yourLabel") for m in big} == {"aardvark"}
    listed = ask(world, registry, "query_population", {"of": population.id, "where": {"yourLabel": "big"}})
    assert '0 members (nearest group you named (DINOv2 small) big; "big" was replaced by "aardvark" for its example cells)' in listed
    # The same group named again replaces its name too, said as such.
    said = ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "huge"})
    assert 'it replaces your earlier "aardvark" (this group, named again)' in said
    assert '"big" was replaced by "aardvark"' in ask(world, registry, "query_population",
                                                      {"of": population.id, "where": {"yourLabel": "big"}})


def test_a_newer_name_for_some_of_a_names_cells_takes_only_those(world, monkeypatch):
    """The second re-check's note (N9): "huge" for 2 of "big"'s 3 cells leaves "big" on the third, and says what
    moved, never "(the same cells)"."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    big = sorted(big_ids(population))
    g_big = population.columns["groups:dinov2-small"]["values"][big[0]]["group"]
    ask(world, registry, "group_cells", {"of": population.id, "group": g_big, "name": "big"})
    small = sorted(m.id for m in population.live() if m.id not in big)
    regrouped(population, {"g1": big[:2], "g2": [big[2], *small]})
    said = ask(world, registry, "group_cells", {"of": population.id, "group": "g1", "name": "huge"})
    assert 'your earlier "big" keeps 1 of its 3 example cells, and 2 are now "huge"' in said and "same cells" not in said
    named = {e["name"]: e["members"] for e in population.columns["named:dinov2-small"]["named"]}
    assert named == {"big": [big[2]], "huge": big[:2]}
    values = population.columns["named:dinov2-small"]["values"]
    assert values[big[2]]["yourLabel"] == "big" and {values[m]["yourLabel"] for m in big[:2]} == {"huge"}


def test_a_name_whose_cells_were_all_removed_is_dropped_and_said(world, monkeypatch):
    monkeypatch.setattr(enrichment, "REPRESENTATIVES", 2)  # big's examples are 2 of its 3 cells: 4 cells stay to group
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    big = big_ids(population)
    g_big = population.columns["groups:dinov2-small"]["values"][next(iter(big))]["group"]
    ask(world, registry, "group_cells", {"of": population.id, "group": g_big, "name": "big"})
    examples = population.columns["named:dinov2-small"]["named"][0]["members"]
    correct(world, population, "remove", examples, by="user")
    ask(world, registry, "group_cells", {"of": population.id})
    groups = population.columns["groups:dinov2-small"]
    g_small = next(g for g, reps in groups["representatives"].items() if not set(reps) & big)
    said = ask(world, registry, "group_cells", {"of": population.id, "group": g_small, "name": "small"})
    assert 'your earlier "big" dropped: none of its example cells is left after your corrections' in said
    assert population.columns["named:dinov2-small"]["dropped"] == ["big"]
    listed = ask(world, registry, "query_population", {"of": population.id, "where": {"yourLabel": "big"}})
    assert '0 members (nearest group you named (DINOv2 small) big; "big" was dropped: none of its example cells' in listed


def test_names_are_reused_for_the_same_groups_and_subject_and_recorded_as_a_use(world, monkeypatch):
    """Re-check note 4 (finding K1-3)."""
    from orchestrator import zeroshot

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    world.scene.context["specimen"] = "a blood smear"
    calls = []

    async def rank(hub, jpegs, names, template):
        calls.append(len(jpegs))
        return [[[n, 1.0 / len(names)] for n in names] for _ in jpegs]

    monkeypatch.setattr(zeroshot, "gpu_rank", rank)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    ask(world, registry, "describe_groups", {"of": population.id, "names": True})
    made = population.columns["names:groups:dinov2-small"]["execution"]
    ranked = len(calls)
    records = []
    runner(world).log = records.append
    said = ask(world, registry, "describe_groups", {"of": population.id, "names": True})
    assert len(calls) == ranked and "MedSigLIP's names (data)" in said
    assert population.columns["names:groups:dinov2-small"]["execution"] == made
    assert any(r["kind"] == "execution-use" and r.get("from") == "evidence" and r["execution"] == made for r in records)
    world.scene.context["specimen"] = "a Pap smear"  # another subject's names: ranked again
    ask(world, registry, "describe_groups", {"of": population.id, "names": True})
    assert len(calls) > ranked


def test_the_refusal_reads_as_a_sentence(world, monkeypatch):
    """Re-check note 5: never "…'s the look groups were …"."""
    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    correct(world, population, "remove", [population.live()[0].id], by="user")
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "describe_groups", {"of": population.id})
    assert f"{population.title}'s look groups were made before your correction" in str(refused.value)
    assert "'s the" not in str(refused.value)


# --- the second K1 re-check at 4a5bfbd (by the partner) ---------------------------------------------------
def at_once(world, registry, first: tuple, second: tuple, delay: float = 0.08):
    """Two requests in one event loop, the second asked `delay` s after the first, each with its own ticket."""
    async def one(name, args, wait):
        await asyncio.sleep(wait)
        with using(issue(world, "turn")):
            return await registry.tools[name].handler(ToolContext("chat", 1, None, name), args)

    async def go():
        return await asyncio.gather(one(*first, 0.0), one(*second, delay), return_exceptions=True)

    return asyncio.run(go())


def test_an_earlier_asked_grouping_landing_last_never_replaces_a_later_one(world, monkeypatch):
    """The second re-check, SF1 (probe S2): "group these by look" (slow), then "into 3 groups" (fast): the 3
    groups stay, and the first says it was overtaken, never that its groups are the ones there."""
    import time as _time

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    original = enrichment.cluster

    def slow_silhouette(ids, x, k_min=enrichment.K_MIN, k_max=enrichment.K_MAX):
        if k_min != k_max:
            _time.sleep(0.4)
        return original(ids, x, k_min, k_max)

    monkeypatch.setattr(enrichment, "cluster", slow_silhouette)
    registry = tools_for(world)
    first, second = at_once(world, registry, ("group_cells", {"of": population.id}),
                            ("group_cells", {"of": population.id, "groups": 3}))
    assert isinstance(second, str) and "6 cells in 3 groups" in second and "3 groups as asked" in second
    assert isinstance(first, ToolFailed), first
    assert "a later request regrouped" in str(first) and "its 2 groups were not kept" in str(first)
    assert "the groups there now are that request's (3 groups by look (DINOv2 small))" in str(first)
    groups = population.columns["groups:dinov2-small"]
    assert groups["k"] == 3 and groups["asked"] == 3
    assert "3 groups by look" in ask(world, registry, "describe_groups", {"of": population.id})


def test_two_namings_at_once_both_keep_their_names(world, monkeypatch):
    """The second re-check, SF1 (probe S1): "g1 are big" and "g2 are small" 50 ms apart: both names are kept
    (namings run one at a time, each reading the names before it), and the second says the first was kept."""
    import time as _time

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    original = enrichment.nearest_named
    calls = []

    def slow_first(*args):
        calls.append(1)
        if len(calls) == 1:
            _time.sleep(0.3)
        return original(*args)

    monkeypatch.setattr(enrichment, "nearest_named", slow_first)
    first, second = at_once(world, registry, ("group_cells", {"of": population.id, "group": "g1", "name": "big"}),
                            ("group_cells", {"of": population.id, "group": "g2", "name": "small"}), delay=0.05)
    assert isinstance(first, str) and isinstance(second, str), (first, second)
    assert first.startswith('"big" is kept') and 'your earlier names kept: "big"' in second
    assert [e["name"] for e in population.columns["named:dinov2-small"]["named"]] == ["big", "small"]


def test_a_correction_while_medsiglip_ranks_refuses_the_description(world, monkeypatch):
    """The second re-check's survivor: describe_groups' check after the names, when a correction lands while
    MedSigLIP ranks: refused in words, never described from the stale groups."""
    from orchestrator import zeroshot

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    world.scene.context["specimen"] = "a blood smear"
    done = []

    async def rank(hub, jpegs, names, template):
        if not done:
            done.append(True)
            correct(world, population, "remove", [population.live()[0].id], by="user")
        return [[[n, 1.0 / len(names)] for n in names] for _ in jpegs]

    monkeypatch.setattr(zeroshot, "gpu_rank", rank)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    with pytest.raises(ToolFailed) as refused:
        ask(world, registry, "describe_groups", {"of": population.id, "names": True})
    assert "look groups were made before your correction" in str(refused.value) and refused.value.next == "group them again"


def test_a_naming_that_waited_through_a_correction_is_refused_in_words(world, monkeypatch):
    """Namings run one at a time: one waiting while a correction lands never names stale groups."""
    import time as _time

    population, _ = cells(world)
    stand_in_looks(monkeypatch, population)
    registry = tools_for(world)
    ask(world, registry, "group_cells", {"of": population.id})
    original = enrichment.nearest_named
    calls = []

    def slow_first_then_corrected(*args):
        calls.append(1)
        if len(calls) == 1:
            _time.sleep(0.3)
            correct(world, population, "remove", [population.live()[0].id], by="user")
        return original(*args)

    monkeypatch.setattr(enrichment, "nearest_named", slow_first_then_corrected)
    first, second = at_once(world, registry, ("group_cells", {"of": population.id, "group": "g1", "name": "big"}),
                            ("group_cells", {"of": population.id, "group": "g2", "name": "small"}), delay=0.05)
    assert isinstance(first, ToolFailed) and "nearest named groups were being made" in str(first)
    assert isinstance(second, ToolFailed) and "look groups were made before your correction" in str(second)
    assert second.next == "group them again, then name the group" and len(calls) == 1


def test_vectors_installed_as_previous_save_finishes_remain_reserved_until_written(world, tmp_path, monkeypatch):
    """Off-loop vector installation and a finishing mask save share one release boundary."""
    import threading
    import time

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    store = world.evidence
    real_done, real_vectors = store._done_writing, store._vectors_bytes
    gate, writers, failures = threading.Event(), [], []
    ids = [m.id for m in population.live()]
    expected = np.ones((len(ids), 3), np.float32)

    def install():
        try:
            store.keep_vectors(population, "looks:race", ids, expected)
        except Exception as exc:
            failures.append(exc)

    def slot_freed(p):
        again = real_done(p)
        if not writers:
            writers.append(threading.Thread(target=install))
            writers[0].start()
            writers[0].join(0.2)
        return again

    def delayed_vectors(p, folder):
        assert gate.wait(HANG_S)
        return real_vectors(p, folder)

    monkeypatch.setattr(store, "_done_writing", slot_freed)
    monkeypatch.setattr(store, "_vectors_bytes", delayed_vectors)
    store.schedule_save(population)
    until(lambda: writers, every=0.01)
    writers[0].join(HANG_S)
    try:
        assert not failures and not writers[0].is_alive()
        assert population.status == "saving"
        assert population.vector_hold is not None
        assert np.array_equal(population.vectors["looks:race"]["array"], expected)
    finally:
        gate.set()
    saved(population)
    assert np.array_equal(store.vectors_of(population, "looks:race")["array"], expected)
    assert population.vector_hold is None and population.vectors == {}
