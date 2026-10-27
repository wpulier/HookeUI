"""Corrections with revisions (M2 of docs/design/2026-10-06-captures-operations-evidence.md): the user says
segmented cells are wrong; each correction is a new revision of the population, revision 0 (the model's own
output) never changes and stays saved, only the members touched are measured again, the count follows, and
the latest correction can be undone (as another revision; earlier ones are never edited)."""
import json
from types import SimpleNamespace
import threading
import time

import numpy as np

from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.population import add_correction_tool
from orchestrator.population import CorrectionRefused, correct, layer_of
from orchestrator.ticket import issue, using
from tests.test_apply import run
from tests.test_population import cells, world  # noqa: F401 (the fixture)

import pytest


def saved(population, seconds: float = 60.0) -> None:
    """Waits for the background save itself (its thread), not a fixed time: under a sim's load a save can take
    longer than any short wait, and the wait ends as soon as it is done."""
    for thread in threading.enumerate():
        if thread.name == f"evidence-{population.id}":
            thread.join(seconds)
    t0 = time.time()
    while population.status == "saving" and time.time() - t0 < seconds:
        time.sleep(0.01)
    assert population.status == "saved", population.status_why


def keeping(world, tmp_path):
    from orchestrator.day.store import DayStore
    from orchestrator.evidence import Evidence

    from tests.test_resources import OCT_7

    # The session used on the date it began (ADR 0017: a session's evidence is filed under the date it was made on).
    day = DayStore(root=tmp_path / "sessions", day="2026-10-07", clock=lambda: OCT_7)
    world.execution_day = day  # as app.py wires it: a population is filed in the session store's date folder
    world.evidence = Evidence(world, tmp_path / "evidence", day=day)


@pytest.mark.parametrize("away", [False, True])
def test_captured_correction_keeps_its_owner_and_refuses_a_replaced_population(world, tmp_path, away):
    from copy import copy

    keeping(world, tmp_path)
    if away:
        world.execution_day = SimpleNamespace(day="2026-10-06", sid="another-project", active_id=None)
    population, _ = cells(world)
    saved(population)
    owner = population.session
    first = population.live()[0].id
    correct(world, population, "remove", [first], by="user")
    saved(population)
    assert population.session == owner and population.revision == 1
    assert (world.evidence.get(population.id) is population) == (not away)
    manifest = (population.path / "manifest.json").read_bytes()
    replacement = copy(population)
    world.evidence.add(replacement)
    with pytest.raises(CorrectionRefused, match="population changed"):
        correct(world, population, "remove", [population.live()[0].id], by="user")
    assert replacement.revision == population.revision == 1
    assert (population.path / "manifest.json").read_bytes() == manifest


def test_removing_a_cell_is_a_revision_and_the_models_own_output_is_kept(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    first = [m.id for m in population.live()]
    gone = first[0]
    words, removed = correct(world, population, "remove", [gone], by="user", why="not a cell")
    assert removed == [gone] and population.revision == 1 and len(population.live()) == 5
    assert "c" in words and "6 instances segmented by cellpose-sam" in words and "5 after your corrections (revision 1)" in words
    assert gone not in population.columns["measure"]["values"]  # only what is live is measured
    rev = population.revisions[-1]
    assert rev["was"] == {gone: "model"} and rev["by"] == "user" and rev["execution"] == population.made_by["execution"]
    saved(population)
    manifest = json.loads((population.path / "manifest.json").read_text())
    assert manifest["revision"] == 1 and [m["id"] for m in manifest["members"]] == first  # every member, r0's included
    with np.load(population.path / "masks.npz") as masks:
        assert {f"{m}.bits" for m in first} <= set(masks.files)


def test_saved_is_said_once_its_session_journal_files_it(world, tmp_path, monkeypatch):
    """A restart reads evidence back from its session's journal, so "saved" (what the walkthrough and a restart wait
    for) is said only once the journal files it: never while that is still being written (on the macos-15 runner a
    read_back right after "saved" found nothing)."""
    from orchestrator.evidence import Evidence
    from tests.waiting import HANG_S

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    day = world.evidence.day
    record, filing, release = day.record_evidence, threading.Event(), threading.Event()

    def held(*args, **kwargs):
        filing.set()
        assert release.wait(HANG_S)
        return record(*args, **kwargs)

    monkeypatch.setattr(day, "record_evidence", held)
    correct(world, population, "remove", [population.live()[0].id], by="user")  # a revision: saved again
    try:
        assert filing.wait(HANG_S)
        assert population.status == "saving"  # its files are written, its filing is not: not said saved yet
    finally:
        release.set()
    saved(population)
    again = Evidence(world, tmp_path / "evidence", day=day)
    assert again.read_back() == [population.id] and again.get(population.id).revision == population.revision == 1


def test_a_journal_that_does_not_file_it_is_said_failed_never_saved(world, tmp_path, monkeypatch):
    """Its files written but the session's journal raising: a restart would not find it, so it is said failed (with
    why), never saved, and its pixels stay in memory."""
    from orchestrator.evidence import Evidence
    from tests.waiting import until

    keeping(world, tmp_path)
    day = world.evidence.day
    logged = []
    world.evidence.log = logged.append

    append = day.journal.append

    def broken(event):
        if event.get("type") == "evidence-saved":
            raise OSError("journal disk full")
        return append(event)

    # Exercise the real SessionStore recorder: an in-memory fallback is not a saved result.
    monkeypatch.setattr(day.journal, "append", broken)
    population, _ = cells(world)
    until(lambda: population.status != "saving")
    assert population.status == "failed" and "OSError: journal disk full" in population.status_why
    assert population.capture is not None and population.hold is not None and not population.hold.released
    assert all(m.mask is not None for m in population.live())  # its pixels kept: nothing a restart can read
    assert logged[-1]["kind"] == "evidence-save-failed" and "journal disk full" in logged[-1]["error"]
    assert Evidence(world, tmp_path / "evidence", day=day).read_back() == []

    monkeypatch.setattr(day.journal, "append", append)
    world.evidence.schedule_save(population)
    saved(population)
    assert Evidence(world, tmp_path / "evidence", day=day).read_back() == [population.id]


def test_merging_cells_measures_the_union_and_a_later_save_keeps_every_mask(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)  # saved: the members' pixels are released from memory
    assert all(m.mask is None for m in population.members.values())
    a, b = [m.id for m in population.live()][:2]
    words, _ = correct(world, population, "merge", [a, b], by="user", why="one cell")
    new = population.revisions[-1]["added"][0]
    assert population.members[new].status == "added" and population.members[a].status == "merged"
    assert f"{a} and {b} merged into one (one cell); they do not touch, so it is 2 pieces" in words
    area = population.value(new, "areaPx")
    assert area and area > 0 and population.value(new, "components") == 2
    saved(population)
    with np.load(population.path / "masks.npz") as masks:  # the six of revision 0 and the merged one
        assert {f"{m}.bits" for m in population.members} <= set(masks.files)


def test_undoing_brings_the_cells_back_as_another_revision(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    a, b = [m.id for m in population.live()][:2]
    correct(world, population, "merge", [a, b], by="user")
    new = population.revisions[-1]["added"][0]
    words, gone = correct(world, population, "undo", [], by="user")
    assert words.startswith(f"{population.title} ({population.id}): your correction 1 undone: {a} and {b} back")
    assert "6 instances segmented" in words and "6 after your corrections (revision 2)" in words
    assert gone == [new] and population.members[new].status == "deleted"
    assert {population.members[a].status, population.members[b].status} == {"model"}
    assert population.value(a, "areaPx")  # measured again from its own mask
    assert population.revisions[0].get("undoes") is None and population.revisions[1]["undoes"] == 1  # never edited
    with pytest.raises(CorrectionRefused):
        correct(world, population, "undo", [], by="user")  # nothing left to undo


def test_naming_cells_that_are_not_there_is_refused_with_the_reason(world):
    population, _ = cells(world)
    with pytest.raises(CorrectionRefused) as refused:
        correct(world, population, "remove", ["c999"], by="user")
    assert "not among" in str(refused.value) and refused.value.next


def test_that_s_not_a_cell_about_the_clicked_one_takes_its_outline_off_too(world):
    from orchestrator.brain.toolbox.view import add_highlight

    population, _ = cells(world)
    registry = Registry()
    add_correction_tool(registry, type("S", (), {"hub": world})())
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in ctx.objects]
    member = outline.made_by["member"]
    layer_of(world).selected = member  # the user clicked it
    ctx2 = ToolContext("chat", 2, None, "that's not a cell")
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(ctx2, {"change": "remove", "why": "not a cell"}))
    assert f"{member} removed (not a cell); 6 instances segmented" in said and "its outline taken off too" in said
    assert outline.id not in world.scene.objects


def test_a_later_save_keeps_the_masks_already_saved_even_when_none_are_in_memory(world, tmp_path):
    """Finding 79: a save writes the saved masks and the resident ones, never only what is in memory."""
    from orchestrator.evidence import Member
    from orchestrator.measure import Mask

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    first = list(population.members)
    added = Member(world.evidence.member_id(), 0, Mask(np.ones((4, 4), bool), 10, 10), [], (10, 10, 14, 14), status="added")
    population.members[added.id] = added  # in memory alone: the rest were released when saved
    world.evidence.schedule_save(population)
    saved(population)
    with np.load(population.path / "masks.npz") as masks:
        assert {f"{m}.bits" for m in [*first, added.id]} <= set(masks.files)



# --- the partner's review of M2.3 ---------------------------------------------------------------------------
def test_revision_zero_is_counted_right_after_merges_undone_and_removed(world, tmp_path):
    """Review B1: "the N as first segmented" counted merged-in members after an undo."""
    from orchestrator.population import first_segmented

    population, _ = cells(world)
    a, b = [m.id for m in population.live()][:2]
    correct(world, population, "merge", [a, b], by="user")
    correct(world, population, "undo", [], by="user")
    assert first_segmented(population) == 6
    correct(world, population, "merge", [a, b], by="user")
    merged = population.revisions[-1]["added"][0]
    words, _ = correct(world, population, "remove", [merged], by="user")
    assert first_segmented(population) == 6 and "6 instances segmented" in words and "4 after your corrections" in words


def test_a_saved_populations_coverage_and_label_map_survive_a_correction(world, tmp_path):
    """Review B2: once saved, the pixels were released and a removal measured coverage 0."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    before = population.columns["measure"]["coverage"]
    correct(world, population, "remove", [population.live()[0].id], by="user")
    after = population.columns["measure"]["coverage"]
    assert 0 < after < before
    saved(population)
    population._labels = None
    assert int(population.labels().max()) == len(population.live())  # the map from the saved masks, not empty


def test_a_corrected_count_is_never_credited_to_the_model(world):
    """Review B3."""
    population, _ = cells(world)
    correct(world, population, "remove", [population.live()[0].id], by="user")
    assert population.count_words().startswith("6 instances segmented by cellpose-sam")
    assert population.count_words().endswith("; 5 after your corrections (revision 1)")


def test_that_s_not_a_cell_about_a_clicked_outline_finds_its_cell(world):
    """Review B4: clicking a highlighted cell selects its outline (not the layer's cell), and the scene table names
    outlines: both reach the cell."""
    from orchestrator.brain.toolbox.view import add_highlight

    population, _ = cells(world)
    registry = Registry()
    add_correction_tool(registry, type("S", (), {"hub": world})())
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in ctx.objects]
    member = outline.made_by["member"]
    world.scene.select(outline.id)  # the studio's click on the outline (commands.select)
    layer_of(world).selected = None
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(ToolContext("chat", 2, None, "that's not a cell"),
                                                            {"change": "remove", "why": "not a cell"}))
    assert f"{member} removed (not a cell)" in said and population.members[member].status == "deleted"
    population2, _ = cells(world)
    ctx3 = ToolContext("chat", 3, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx3, {"of": population2.id, "by": "area", "top": 1}))
        (o2,) = [world.scene.objects[i] for i in ctx3.objects]
        said = run(registry.tools["correct_cells"].handler(ToolContext("chat", 4, None, "remove o"), {"change": "remove", "members": [o2.id]}))
    assert f"{o2.made_by['member']} removed" in said  # an outline id names its cell


def test_a_removed_cell_is_no_longer_selected_and_at_n_names_the_corrected_population(world):
    """Review S1 and S2."""
    from orchestrator.brain.registry import step_output

    population, _ = cells(world)
    registry = Registry()
    add_correction_tool(registry, type("S", (), {"hub": world})())
    layer = layer_of(world)
    layer.show(population)
    layer.selected = population.live()[0].id
    ctx = ToolContext("chat", 1, None, "that's not a cell")
    ctx.step = 1
    ctx.steps.tools.update({1: "correct_cells", 2: "highlight"})
    with using(issue(world, "turn")):
        run(registry.tools["correct_cells"].handler(ctx, {"change": "remove"}))
    assert layer.selected is None
    later = type("C", (), {**ctx.__dict__, "step": 2})()
    assert step_output(later, "@1") == population.id


def test_a_cell_named_from_a_population_not_shown_is_found_and_undo_takes_this_conversations_latest(world):
    """Review S3."""
    first, _ = cells(world)
    second, _ = cells(world)
    registry = Registry()
    add_correction_tool(registry, type("S", (), {"hub": world})())
    target = next(m for m in first.live() if m.id not in second.members)
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(ToolContext("chat", 1, None, f"{target.id} is not a cell"),
                                                            {"change": "remove", "members": [target.id]}))
        assert said.startswith(f"{first.title} ({first.id}): {target.id} removed")
        third, _ = run(__import__("orchestrator.tools", fromlist=["x"]).outline_cells(world))
        undone = run(registry.tools["correct_cells"].handler(ToolContext("chat", 2, None, "undo that"), {"change": "undo"}))
    assert undone.startswith(f"{first.title} ({first.id}): your correction 1 undone")


def test_an_outline_the_user_named_stays_when_its_cell_is_merged(world):
    """Review S4."""
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.known import renamed

    population, _ = cells(world)
    registry = Registry()
    add_correction_tool(registry, type("S", (), {"hub": world})())
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in ctx.objects]
    world.scene.update(outline.id, label="red blood cell", status="confirmed", naming=renamed(outline, "you", "red blood cell"))
    member = outline.made_by["member"]
    other = next(m.id for m in population.live() if m.id != member)
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(ToolContext("chat", 2, None, "these are one"),
                                                            {"change": "merge", "members": [member, other]}))
    assert outline.id in world.scene.objects and 'your "red blood cell" outline is kept (you named it)' in said


def test_the_users_own_words_are_kept_with_the_revision(world):
    """Review S7."""
    population, _ = cells(world)
    registry = Registry()
    add_correction_tool(registry, type("S", (), {"hub": world})())
    with using(issue(world, "turn")):
        run(registry.tools["correct_cells"].handler(ToolContext("chat", 1, None, "c1 is just dust, take it off"),
                                                    {"change": "remove", "members": [population.live()[0].id]}))
    assert population.revisions[-1]["asked"] == "c1 is just dust, take it off"


def test_the_review_mutations_are_caught(world, tmp_path):
    """Review S8: each of these was a mutation every test passed with."""
    from orchestrator.measure import Mask
    from orchestrator.scene import SceneObject

    population, _ = cells(world)
    registry = Registry()
    add_correction_tool(registry, type("S", (), {"hub": world})())
    live = [m.id for m in population.live()]
    # a one-cell merge is refused
    with pytest.raises(CorrectionRefused):
        correct(world, population, "merge", [live[0]], by="user")
    # undo puts back what each member was, not "model": a merged cell removed and the removal undone is "added" again
    correct(world, population, "merge", live[:2], by="user")
    merged = population.revisions[-1]["added"][0]
    correct(world, population, "remove", [merged], by="user")
    correct(world, population, "undo", [], by="user")
    assert population.members[merged].status == "added"
    # the user's own outline linked to a removed cell stays (only the app's are taken off)
    info = world.latest_info
    mine = SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                       (info.dx, info.dy), "click", label="mine")
    world.scene.add(mine)
    population.links.append({"kind": "same-object", "from": live[2], "to": mine.id, "basis": "user", "confidence": 1.0})
    with using(issue(world, "turn")):
        run(registry.tools["correct_cells"].handler(ToolContext("chat", 1, None, "x"), {"change": "remove", "members": [live[2]]}))
    assert mine.id in world.scene.objects
    # the layer forgets removed members' places
    layer = layer_of(world)
    layer.show(population)
    layer.placement[live[3]] = {"state": "verified", "frame": 1}
    with using(issue(world, "turn")):
        run(registry.tools["correct_cells"].handler(ToolContext("chat", 2, None, "x"), {"change": "remove", "members": [live[3]]}))
    assert live[3] not in layer.placement
    # a selection on a layer that is not shown is not "that"
    layer.hide()
    layer.selected = live[4]
    with pytest.raises(ToolFailed):
        with using(issue(world, "turn")):
            run(registry.tools["correct_cells"].handler(ToolContext("chat", 3, None, "that's not a cell"), {"change": "remove"}))


def test_a_merge_is_made_at_full_resolution_from_scaled_masks(world):
    from orchestrator.measure import Mask

    population, _ = cells(world)
    a, b = population.live()[:2]
    a.mask = Mask(np.ones((10, 10), bool), 100, 100, 2.0)  # 20 x 20 capture px each, kept at half size
    b.mask = Mask(np.ones((10, 10), bool), 140, 100, 2.0)
    correct(world, population, "merge", [a.id, b.id], by="user")
    merged = population.revisions[-1]["added"][0]
    assert population.value(merged, "areaPx") == 800


def test_undo_on_a_saved_population_measures_the_cells_back_from_their_saved_masks(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    a = population.live()[0].id
    correct(world, population, "remove", [a], by="user")
    saved(population)
    correct(world, population, "undo", [], by="user")
    assert population.value(a, "areaPx")


def tools_logging(world):
    from types import SimpleNamespace

    logged: list[dict] = []
    registry = Registry()
    add_correction_tool(registry, SimpleNamespace(hub=world, log=logged.append))
    return registry, logged


def say(world, registry, words, args, thread=None, turn=1):
    from types import SimpleNamespace

    ctx = ToolContext("chat", turn, None, words)
    ctx.asked = SimpleNamespace(thread=thread) if thread else None
    with using(issue(world, "turn")):
        return run(registry.tools["correct_cells"].handler(ctx, args))


def test_a_save_finishing_while_the_masks_are_read_never_leaves_a_correction_without_them(world, tmp_path, monkeypatch):
    """The M2.3 re-check's race: the previous correction's save finished (releasing every mask) while the next
    step's masks were read in a thread; the merge was refused and an undo stored coverage 0."""
    import orchestrator.population as pop

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    store = world.evidence
    real = pop.prepare_correction

    def save_finishes_meanwhile(hub, p, change):
        gray = real(hub, p, change)
        store._release_pixels(p)  # what `_saved` does on the loop while the thread reads
        return gray

    monkeypatch.setattr(pop, "prepare_correction", save_finishes_meanwhile)
    import threading

    reads = []
    real_reload = store.reload_masks
    monkeypatch.setattr(store, "reload_masks", lambda p: reads.append(threading.current_thread() is threading.main_thread()) or real_reload(p))
    registry, _ = tools_logging(world)
    c1, c2, c3 = [m.id for m in population.live()][:3]
    say(world, registry, "remove c1", {"change": "remove", "members": [c1]})
    said = say(world, registry, "merge c2 and c3", {"change": "merge", "members": [c2, c3]})
    assert f"{c2} and {c3} merged into one" in said
    assert population.columns["measure"]["coverage"] > 0
    said = say(world, registry, "undo that", {"change": "undo"})
    assert population.columns["measure"]["coverage"] > 0 and population.value(c2, "areaPx")
    assert reads and not any(reads)  # read back in the thread only, never again on the loop
    saved(population)
    assert all(m.mask is None for m in population.members.values())  # released once the corrections were done


def test_masks_that_cannot_be_read_back_refuse_the_correction_and_change_nothing(world, tmp_path):
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    (population.path / "masks.npz").unlink()
    with pytest.raises(CorrectionRefused) as refused:
        correct(world, population, "remove", [population.live()[0].id], by="user")
    assert "could not be read back from disk" in str(refused.value) and population.revision == 0
    with pytest.raises(CorrectionRefused):  # prepared beforehand, then released: the last guard
        correct(world, population, "remove", [population.live()[0].id], by="user", gray=None)
    assert population.revision == 0 and len(population.live()) == 6


def test_a_corrected_count_is_recorded_with_its_revision(world):
    """Re-check B3: count's measurement record (it goes into datasets)."""
    from types import SimpleNamespace

    from orchestrator.brain.toolbox.magnifier import add_magnifier_tools

    population, _ = cells(world)
    logged: list[dict] = []
    registry = Registry()
    add_magnifier_tools(registry, SimpleNamespace(hub=world, log=logged.append, magnifier=SimpleNamespace(), scale=None, agent=None))
    correct(world, population, "remove", [population.live()[0].id], by="user")
    with using(issue(world, "turn")):
        run(registry.tools["count"].handler(ToolContext("chat", 1, None, "how many cells?"), {"label": "cells", "of": population.id}))
    record = [r for r in logged if r.get("what") == "count"][-1]
    assert record["n"] == 5 and record["revision"] == 1 and record["segmented"] == 6
    assert "as the user corrected it (revision 1)" in record["how"] and "the specialist's instances)" not in record["how"]


def test_undo_takes_the_latest_correction_made_in_this_conversation(world):
    """Re-check S3: segmented in one conversation, corrected in another, undone there."""
    population, _ = cells(world)
    population.thread = "t1"
    registry, _ = tools_logging(world)
    target = population.live()[0].id
    say(world, registry, "that's dust", {"change": "remove", "members": [target]}, thread="t2")
    with pytest.raises(ToolFailed) as refused:  # nothing corrected in t3: t2's correction is not undone from there
        say(world, registry, "undo that", {"change": "undo"}, thread="t3", turn=2)
    assert "nothing has been corrected in this conversation" in str(refused.value) and population.revision == 1
    said = say(world, registry, "undo that", {"change": "undo"}, thread="t2", turn=3)
    assert f"your correction 1 undone: {target} back" in said


def test_a_newer_click_beats_an_older_selection_and_an_outline_of_no_cell_is_said_so(world):
    """The re-check: highlight c5, select its outline, then click elsewhere (the user's own outline): "that's not a
    cell either" is about the click, never c5."""
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.scene import SceneObject

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in ctx.objects]
    world.scene.select(outline.id)
    time.sleep(0.01)
    info = world.latest_info
    mine = SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                       (info.dx, info.dy), "click", label="mine")
    world.scene.add(mine)
    with pytest.raises(ToolFailed) as refused:
        say(world, registry, "that's not a cell either", {"change": "remove"}, turn=2)
    assert f"{mine.id} (the outline you made) is not one of the segmented cells" in str(refused.value)
    assert population.members[outline.made_by["member"]].status == "model"


def test_an_outline_linked_to_a_cell_without_a_member_record_is_that_cell(world):
    """The re-check: find-all-like matches and masks pointing link their outline to a cell by a same-object link."""
    from orchestrator.scene import SceneObject

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    target = population.live()[1].id
    info = world.latest_info
    found = SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                        (info.dx, info.dy), "ai", label="match")
    world.scene.add(found)
    from orchestrator.evidence import evidence_of

    evidence_of(world).link(population, "same-object", target, found.id, "matches")
    world.scene.select(found.id)
    said = say(world, registry, "that's not one", {"change": "remove"})
    assert f"{target} removed" in said
    assert found.id not in world.scene.objects  # the app's outline of it is taken off
    with pytest.raises(ToolFailed) as again:  # named again: done already, never "click the cell you mean"
        say(world, registry, "that's not a cell", {"change": "remove", "members": [target]}, turn=2)
    assert "was already removed" in str(again.value) and again.value.next is None


def test_the_rest_of_the_rechecks_mutations_are_caught(world, tmp_path):
    """Re-check S6-S8: the revision said once, revision 0 kept once saved, the user's words in the log line, the
    layer's templates renewed, and cells back from an undo said as not outlined again."""
    from orchestrator.brain.toolbox.population import add_population_tools
    from orchestrator.brain.toolbox.view import add_highlight
    from types import SimpleNamespace

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    registry, logged = tools_logging(world)
    add_population_tools(registry, SimpleNamespace(hub=world, log=None))
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    layer = layer_of(world)
    layer.show(population)
    ctx = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in ctx.objects]
    member = outline.made_by["member"]
    layer.templates_of, layer.templates = population.id, {"stale": (np.zeros((4, 4), np.uint8), (0, 0))}
    said = say(world, registry, "the big one is two cells stuck together, drop it", {"change": "remove", "members": [member]}, turn=2)
    assert "(what the model segmented is kept as revision 0)" in said
    assert layer.templates_of is None and layer.templates == {}
    line = [r for r in logged if r.get("what") == "population"][-1]
    assert line["asked"] == "the big one is two cells stuck together, drop it" and line["revision"] == 1
    with using(issue(world, "turn")):
        facts = run(registry.tools["query_population"].handler(ToolContext("chat", 3, None, "how many?"), {"of": population.id}))
    assert facts.count("(revision 1)") == 1
    said = say(world, registry, "undo that", {"change": "undo"}, turn=4)
    assert f"{member} is not outlined again (ask to highlight it)" in said



def test_after_a_restart_an_old_link_never_names_the_new_outline_with_the_same_id(world):
    """The second re-check of M2.3 (BLOCKING): outline ids start again at o1 in every run; a population read back
    keeps its links to the old ids, and a correction must not take a cell the user never pointed at."""
    from orchestrator.scene import SceneObject

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    c5 = population.live()[4].id
    info = world.latest_info
    mine = SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                       (info.dx, info.dy), "click", label="mine")
    world.scene.add(mine)
    # the earlier run's link (its highlight of c5 had this id then), and one from before runs were recorded
    population.links += [{"kind": "same-object", "from": c5, "to": mine.id, "basis": "promoted", "confidence": 1.0,
                          "at": mine.created_at - 3600, "run": "123-earlier"},
                         {"kind": "same-object", "from": c5, "to": mine.id, "basis": "promoted", "confidence": 1.0,
                          "at": mine.created_at - 3600}]
    world.scene.select(mine.id)
    with pytest.raises(ToolFailed) as refused:
        say(world, registry, "that's not a cell", {"change": "remove"})
    assert f"{mine.id} (the outline you made) is not one of the segmented cells" in str(refused.value)
    with pytest.raises(ToolFailed):
        say(world, registry, "remove it", {"change": "remove", "members": [mine.id]}, turn=2)
    assert population.members[c5].status == "model" and mine.id in world.scene.objects


def test_undo_in_one_conversation_never_undoes_anothers_correction_of_the_same_cells(world):
    """The second re-check (S3): t1 removes c1, t2 removes c2; "undo that" in t1 brings c1 back, not c2."""
    population, _ = cells(world)
    registry, _ = tools_logging(world)
    c1, c2 = [m.id for m in population.live()][:2]
    say(world, registry, "c1 is dust", {"change": "remove", "members": [c1]}, thread="t1")
    say(world, registry, "c2 is dust", {"change": "remove", "members": [c2]}, thread="t2", turn=2)
    said = say(world, registry, "undo that", {"change": "undo"}, thread="t1", turn=3)
    assert f"{c1} back" in said and population.members[c1].status == "model" and population.members[c2].status == "deleted"


def test_a_selection_newer_than_a_click_is_what_that_means(world):
    """The second re-check: the newest pointing wins both ways (a selection after a click, too)."""
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.scene import SceneObject

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    info = world.latest_info
    mine = SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                       (info.dx, info.dy), "click", label="mine")
    world.scene.add(mine)
    time.sleep(0.01)
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in ctx.objects]
    world.scene.select(outline.id)  # after the click
    said = say(world, registry, "that's not a cell", {"change": "remove"}, turn=2)
    assert f"{outline.made_by['member']} removed" in said


def test_undo_takes_the_newest_correction_by_time_and_a_named_population_never_overrides_the_clicked_one(world):
    """The second re-check: by time, not by list order; and `of` does not move a clicked outline to another population."""
    from orchestrator.brain.toolbox.view import add_highlight

    first, _ = cells(world)
    second, _ = cells(world)
    registry, _ = tools_logging(world)
    a, b = second.live()[0].id, first.live()[1].id
    say(world, registry, "x", {"change": "remove", "members": [a], "of": second.id})
    time.sleep(0.01)
    say(world, registry, "y", {"change": "remove", "members": [b], "of": first.id}, turn=2)  # newer, in the earlier population
    said = say(world, registry, "undo that", {"change": "undo"}, turn=3)
    assert said.startswith(f"{first.title} ({first.id}): your correction 1 undone: {b} back")
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 4, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(ctx, {"of": first.id, "by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in ctx.objects]
    world.scene.select(outline.id)
    said = say(world, registry, "that's not a cell", {"change": "remove", "of": second.id}, turn=5)
    assert said.startswith(f"{first.title} ({first.id}): {outline.made_by['member']} removed")
    with pytest.raises(ToolFailed) as missing:
        say(world, registry, "c999 is not a cell", {"change": "remove", "members": ["c999"], "of": first.id}, turn=6)
    assert f"({first.id})'s cells now" in str(missing.value)


def test_a_refused_correction_leaves_no_masks_in_memory(world, tmp_path):
    """The second re-check's note: masks read back for a correction that changed nothing go once it is done."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    registry, _ = tools_logging(world)
    c1 = population.live()[0].id
    say(world, registry, "c1 is dust", {"change": "remove", "members": [c1]})
    saved(population)
    with pytest.raises(ToolFailed) as again:
        say(world, registry, "c1 is dust", {"change": "remove", "members": [c1]}, turn=2)
    assert "already removed" in str(again.value)
    assert all(m.mask is None for m in population.members.values())



def test_a_cell_selected_on_the_layer_after_a_click_is_what_that_means(world):
    """The second re-check: the layer's selection carries its time too."""
    from orchestrator.scene import SceneObject

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    info = world.latest_info
    world.scene.add(SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                                (info.dx, info.dy), "click", label="mine"))
    time.sleep(0.01)
    layer = layer_of(world)
    layer.show(population)
    target = population.live()[2]
    layer.select(target)
    said = say(world, registry, "that's not a cell", {"change": "remove"})
    assert f"{target.id} removed" in said



def earlier_run_links(population, member, outline_id, created_at):
    """A population read back after a restart: its links name outline ids of an earlier run of the app. One is stamped
    later than the new outline (a clock set back), one comes from before links recorded their run."""
    population.links += [{"kind": "same-object", "from": member, "to": outline_id, "basis": "promoted", "confidence": 1.0,
                          "at": created_at + 60, "run": "123-earlier"},
                         {"kind": "same-object", "from": member, "to": outline_id, "basis": "promoted", "confidence": 1.0,
                          "at": created_at - 3600}]


def new_look(world, label="stoma", source="ai:look"):
    from orchestrator.scene import SceneObject

    info = world.latest_info
    obj = SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                      (info.dx, info.dy), source, label=label)
    world.scene.add(obj)
    return obj


def test_after_a_restart_every_reader_leaves_the_new_outline_with_an_old_id_alone(world):
    """The third re-check (the partner's probes P3, P4, P5 and P7): removing the cell by its number never takes the new
    outline off; the undo says the cell is not outlined again; a close-up of the cell never opens on the new outline;
    and a link from an earlier run never names it, even stamped later than it."""
    from tests.test_flows import Magnifier, ask as zoom, zoom_tool

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    c5, c2 = population.live()[4].id, population.live()[1].id
    mine = new_look(world)
    earlier_run_links(population, c5, mine.id, mine.created_at)
    said = say(world, registry, "c5 is not a cell", {"change": "remove", "members": [c5]})
    assert mine.id in world.scene.objects and "taken off" not in said  # P3
    said = say(world, registry, "undo that", {"change": "undo"}, turn=2)
    assert f"{c5} is not outlined again" in said  # P5: its outline then was of the earlier run
    world.scene.select(mine.id)
    with pytest.raises(ToolFailed):  # P7: the later-stamped link of an earlier run never makes it c5's
        say(world, registry, "that's not a cell", {"change": "remove"}, turn=3)
    assert population.members[c5].status == "model"
    other = new_look(world, "stoma 2")
    earlier_run_links(population, c2, other.id, other.created_at)
    magnifier = Magnifier(world)
    zoom(world, zoom_tool(world, magnifier), {"object": c2})
    assert magnifier.object_id not in (other.id, mine.id)  # P4: a close-up of c2 makes its own outline


def test_undo_takes_the_newest_correction_wherever_it_is_in_the_list(world):
    """The third re-check: the newest by time, also when it is in the later population (open_[0] survived)."""
    first, _ = cells(world)
    second, _ = cells(world)
    registry, _ = tools_logging(world)
    a, b = first.live()[0].id, second.live()[1].id
    say(world, registry, "x", {"change": "remove", "members": [a], "of": first.id})
    time.sleep(0.01)
    say(world, registry, "y", {"change": "remove", "members": [b], "of": second.id}, turn=2)
    said = say(world, registry, "undo that", {"change": "undo"}, turn=3)
    assert said.startswith(f"{second.title} ({second.id}): your correction 1 undone: {b} back")


def test_the_notes_words_are_pinned(world):
    """The third re-check: query_population's record carries the revision; a merged cell is "merged into another";
    the user's own box with the cells shown is told to click the cell itself."""
    from orchestrator.brain.toolbox.population import add_population_tools

    population, _ = cells(world)
    registry, logged = tools_logging(world)
    add_population_tools(registry, SimpleNamespace(hub=world, log=logged.append))
    a, b = [m.id for m in population.live()][:2]
    say(world, registry, "one cell", {"change": "merge", "members": [a, b]})
    with pytest.raises(ToolFailed) as again:
        say(world, registry, "merge again", {"change": "remove", "members": [a]}, turn=2)
    assert f"{a} was already merged into another" in str(again.value)
    with using(issue(world, "turn")):
        run(registry.tools["query_population"].handler(ToolContext("chat", 3, None, "how many?"), {"of": population.id}))
    record = [r for r in logged if r.get("what") == "population"][-1]
    assert record["revision"] == 1
    layer_of(world).show(population)
    box = new_look(world, "mine", source="click")
    world.scene.select(box.id)
    with pytest.raises(ToolFailed) as refused:
        say(world, registry, "that's not a cell", {"change": "remove"}, turn=4)
    assert refused.value.next == "click the cell itself on the cells' layer, or say its number"



def test_an_undo_naming_a_population_corrected_only_elsewhere_says_so(world):
    """The third re-check's note: "has no correction to undo" was untrue of a population corrected in another
    conversation."""
    population, _ = cells(world)
    registry, _ = tools_logging(world)
    say(world, registry, "c1 is dust", {"change": "remove", "members": [population.live()[0].id]}, thread="t1")
    with pytest.raises(ToolFailed) as refused:
        say(world, registry, "undo that", {"change": "undo", "of": population.id}, thread="t2", turn=2)
    assert "no correction made in this conversation to undo (its corrections were made in another one)" in str(refused.value)


def test_a_click_made_after_the_request_is_not_what_that_meant(world):
    """The partner's Amendment 1, C2, with M2.3's newest pointing: "that's not a cell" said with c5's outline
    selected; the user then clicks elsewhere while it is answered. c5 is what was meant."""
    from orchestrator.board import Board
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.scene import SceneObject

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    first = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(first, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in first.objects]
    board = Board(world, clock=time.time)
    world.scene.select(outline.id)
    request = ToolContext("chat", 2, None, "that's not a cell", when_asked=board.when_asked(), board=board)
    time.sleep(0.01)
    info = world.latest_info
    mine = world.scene.add(SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id,
                                       info.epoch, (info.dx, info.dy), "click", label="mine"))  # clicked after the request
    world.scene.select(mine.id)  # and selected
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(request, {"change": "remove"}))
    member = outline.made_by["member"]
    assert population.members[member].status == "deleted" and member in said


def asked_with_a_cell(world, which=2):
    """The cells shown and one selected on the layer when the user says "that's not a cell"."""
    from orchestrator.board import Board

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    layer = layer_of(world)
    layer.show(population)
    meant = population.live()[which]
    layer.select(meant)
    board = Board(world, clock=time.time)
    request = ToolContext("chat", 2, None, "that's not a cell", when_asked=board.when_asked(), board=board)
    time.sleep(0.01)
    return population, registry, layer, meant, request


def test_the_cell_selected_when_asked_is_what_that_meant_after_a_click_elsewhere(world):
    """The executor's P7: the user clicks elsewhere while it is answered (which clears the layer's cell, N1): the
    cell selected when they spoke is still what was meant, not an older click."""
    from orchestrator.scene import SceneObject

    population, registry, layer, meant, request = asked_with_a_cell(world)
    info = world.latest_info
    layer.select(None)
    world.scene.add(SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                                (info.dx, info.dy), "click", label="mine"))
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(request, {"change": "remove"}))
    assert f"{meant.id} removed" in said and population.members[meant.id].status == "deleted"


def test_the_cell_selected_when_asked_is_what_that_meant_after_another_cell_is_clicked(world):
    """The executor's P7b: the user clicks another cell while it is answered; the one selected when they spoke is
    removed, and the one clicked after stays."""
    population, registry, layer, meant, request = asked_with_a_cell(world)
    other = population.live()[4]
    layer.select(other)
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(request, {"change": "remove"}))
    assert f"{meant.id} removed" in said and population.members[other.id].status != "deleted"


def test_an_outline_selected_when_asked_and_gone_since_is_said(world):
    """The executor's P5, for corrections: never another cell in its place."""
    from orchestrator.board import Board
    from orchestrator.brain.toolbox.view import add_highlight

    population, _ = cells(world)
    registry, _ = tools_logging(world)
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    first = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(first, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in first.objects]
    world.scene.select(outline.id)
    board = Board(world, clock=time.time)
    request = ToolContext("chat", 2, None, "that's not a cell", when_asked=board.when_asked(), board=board)
    world.scene.remove(outline.id)
    before = len(population.live())
    with using(issue(world, "turn")):
        with pytest.raises(ToolFailed) as refused:
            run(registry.tools["correct_cells"].handler(request, {"change": "remove"}))
    assert "no longer on screen" in str(refused.value) and refused.value.next == "click it again"
    assert len(population.live()) == before  # nothing removed in its place


def test_the_requests_own_step_comes_before_what_was_selected_when_asked(world):
    """The executor's P3 for corrections: "select the largest and say it's not a cell": the cell the request's own
    step pointed at is removed, not the one selected on the layer when the user asked."""
    from orchestrator.brain.toolbox.view import add_highlight

    population, registry, layer, meant, request = asked_with_a_cell(world)
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    first = ToolContext("chat", 1, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(first, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in first.objects]
    largest = outline.made_by["member"]
    assert largest != meant.id
    request.pointed = outline.id  # the request's own select step
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(request, {"change": "remove"}))
    assert f"{largest} removed" in said and population.members[meant.id].status != "deleted"


def test_the_newer_of_a_click_and_a_cell_selected_before_the_request_is_meant_by_their_saved_times(world):
    """The executor's survivors N26 and N27: an older click and a newer pointing (a cell on the layer, or a
    selected outline of a cell) were both before the request; the saved times decide, so the newer is meant, never
    the older click (which is no cell: it would be refused)."""
    from orchestrator.board import Board
    from orchestrator.brain.toolbox.view import add_highlight
    from orchestrator.scene import SceneObject

    info = world.latest_info
    world.scene.add(SceneObject(world.scene.new_id(), "highlight", [[[1, 1], [9, 1], [9, 9], [1, 9]]], info.id, info.epoch,
                                (info.dx, info.dy), "click", label="mine"))  # an older click on no cell
    time.sleep(0.01)
    population, registry, layer, meant, request = asked_with_a_cell(world)  # then a cell selected: N26
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(request, {"change": "remove"}))
    assert f"{meant.id} removed" in said
    add_highlight(registry, world, sight=type("V", (), {"view": None})(), outline_seen=None)
    first = ToolContext("chat", 4, None, "highlight the largest")
    with using(issue(world, "turn")):
        run(registry.tools["highlight"].handler(first, {"by": "area", "top": 1}))
    (outline,) = [world.scene.objects[i] for i in first.objects]
    time.sleep(0.01)
    world.scene.select(outline.id)  # then an outline of a cell selected: N27
    request = ToolContext("chat", 5, None, "that's not a cell", when_asked=Board(world, clock=time.time).when_asked(),
                          board=Board(world, clock=time.time))
    with using(issue(world, "turn")):
        said = run(registry.tools["correct_cells"].handler(request, {"change": "remove"}))
    assert f"{outline.made_by['member']} removed" in said


# --- off the event loop: a save's release never drops what a later save still needs (finding 84) ------------
def test_a_merge_right_after_a_removal_off_the_loop_keeps_the_merged_mask_saved(world, tmp_path):
    """With no running loop (scripts, tests, a worker thread) a save finishes on its own writer thread: the removal's
    save releasing its pixels while the merge is made and saved never drops the merged member's mask."""
    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    live = [m.id for m in population.live()]
    correct(world, population, "remove", [live[0]], by="user")
    correct(world, population, "merge", [live[1], live[2]], by="user")  # no wait: the removal is still being saved
    merged = population.revisions[-1]["added"][0]
    saved(population)
    with np.load(population.path / "masks.npz") as masks:
        assert f"{merged}.bits" in masks.files
        assert {f"{m}.bits" for m in population.members} <= set(masks.files)
    assert population.value(merged, "areaPx")  # measured from its own mask, never one released under it


def test_a_member_made_while_a_save_writes_the_masks_keeps_its_mask_until_a_save_writes_it(world, tmp_path, monkeypatch):
    """The window itself, made certain: a member is made (on another thread) while a save is writing the masks it
    read, and its own save is asked for only after that one is done. Only what a saved file holds leaves memory, so
    the member's mask is still there for its save."""
    from orchestrator.evidence import Member
    from orchestrator.measure import Mask

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    added = Member(world.evidence.member_id(), 0, Mask(np.ones((4, 4), bool), 10, 10), [], (10, 10, 14, 14), status="added")
    real = np.savez_compressed

    def a_member_is_made_meanwhile(*args, **arrays):
        population.members.setdefault(added.id, added)  # this save read the masks before it was made
        return real(*args, **arrays)

    monkeypatch.setattr(np, "savez_compressed", a_member_is_made_meanwhile)
    world.evidence.schedule_save(population)  # a link, say
    saved(population)
    monkeypatch.setattr(np, "savez_compressed", real)
    assert added.mask is not None  # not written yet, so not released
    world.evidence.schedule_save(population)  # the member's own save
    saved(population)
    with np.load(population.path / "masks.npz") as masks:
        assert {f"{m}.bits" for m in population.members} <= set(masks.files)
    assert all(m.mask is None for m in population.members.values())  # written now, so released


def test_a_correction_called_directly_holds_its_masks_while_a_save_finishes(world, tmp_path, monkeypatch):
    """Off the loop `correct` is called without the tool's hold (a script, a test): a save finishing between reading
    the masks back and merging them leaves them in memory until the correction is done."""
    import orchestrator.population as pop

    keeping(world, tmp_path)
    population, _ = cells(world)
    saved(population)
    store = world.evidence
    real = pop.prepare_correction

    def a_save_finishes_meanwhile(hub, p, change):
        gray = real(hub, p, change)
        store._release_pixels(p)  # what the writer thread's `_saved` does when it lands here
        return gray

    monkeypatch.setattr(pop, "prepare_correction", a_save_finishes_meanwhile)
    a, b = [m.id for m in population.live()][:2]
    words, _ = correct(world, population, "merge", [a, b], by="user")
    assert f"{a} and {b} merged into one" in words and population.columns["measure"]["coverage"] > 0
    saved(population)
    assert all(m.mask is None for m in population.members.values())  # released once it was done and saved


@pytest.mark.parametrize("known_scale", [True, False])
def test_retained_population_measurement_keeps_original_capture_and_scale(world, known_scale):
    """A later camera/objective must not become the provenance of saved measurements."""
    from orchestrator.brain.toolbox.population import add_population_tools

    population, _ = cells(world)
    population.view.update(seq=17, boot="original-camera", at=123.0, run="original-run")
    population.scale_id = ({"umPerPx": 0.5, "objective": 40, "measured": True,
                            "fieldNumber": 18, "fieldNumberSource": "user"} if known_scale else None)
    world.latest_info.seq, world.latest_info.boot = 900, "replacement-camera"
    world.scale_reading = lambda: SimpleNamespace(um_per_px=7.0, objective=4, measured=False)
    registry, logged = tools_logging(world)
    add_population_tools(registry, SimpleNamespace(hub=world, log=logged.append))
    with using(issue(world, "turn")):
        run(registry.tools["query_population"].handler(
            ToolContext("chat", 3, None, "read the earlier cells"), {"of": population.id}))
    record = [r for r in logged if r.get("what") == "population"][-1]
    assert record["measuredOn"] == {"frameId": population.capture_id, "seq": 17,
                                    "boot": "original-camera", "wallAt": 123.0, "run": "original-run"}
    assert record["revision"] == population.revision
    if known_scale:
        assert record["scale"]["umPerPx"] == 0.5 and record["scale"]["objective"] == 40
        assert record["scale"]["source"] == "measured"
        population.scale_id["umPerPx"] = 99
        assert record["scale"]["umPerPx"] == 0.5
    else:
        assert record["scale"] is None
