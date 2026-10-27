"""A provisional answer backed by the measurement (M2 of docs/design/2026-10-06-captures-operations-evidence.md):
"which is the biggest cell?" is answered by eye at once, the guess outlined, and the measurement on the
capture then says whether it holds, where the guess ranks if not, or that the two can't be compared and why.
Never silent, never assumed right; the guess's name goes only to the cell it is, and only a guess this
request's look made is ever taken off (scaffolding review of #164)."""
import time

from orchestrator import tools
from orchestrator.brain.registry import Registry, ToolContext, ToolFailed
from orchestrator.brain.toolbox.view import add_highlight
from orchestrator.scene import SceneObject
from orchestrator.ticket import issue, using
from tests.test_apply import run
from tests.test_population import CELLS, world  # noqa: F401 (the fixture)

import pytest


def outline_of(world, box, label="biggest cell?", source="ai:look", status="likely", **kw) -> SceneObject:
    from orchestrator.known import look_record

    x0, y0, x1, y1 = box
    info = world.latest_info
    kw.setdefault("naming", look_record(label=label, model="gemini-3.5-flash"))  # as the look keeps it (#157)
    obj = SceneObject(world.scene.new_id(), "highlight", [[[x0, y0], [x1, y0], [x1, y1], [x0, y1]]],
                      info.id, info.epoch, (info.dx, info.dy), source, label=label, status=status, **kw)
    world.scene.add(obj)
    return obj


def cell_box(n: int):
    x, y, a, b = CELLS[n]
    return (x - a, y - b, x + a, y + b)


def asked(world, guess=None, args=None, *, before=None, log=None, between=None):
    """One request: the look outlines its guess (`guess`: a box, made now, as target 1), every cell is
    measured, and highlight checks the guess. `before`: an outline made before the request, used as target 1."""
    registry = Registry()
    add_highlight(registry, world, sight=type("S", (), {"view": None})(), outline_seen=None, log=log)
    ctx = ToolContext("chat", 1, None, "which is the biggest cell?")
    with using(issue(world, "turn")):
        made = outline_of(world, guess) if guess is not None else None
        target = made or before
        if target is not None:
            ctx.objects.insert(0, target.id)
        population, _ = run(tools.outline_cells(world))
        if between is not None:
            between(target)
        said = run(registry.tools["highlight"].handler(ctx, {"of": population.id, "by": "area", "top": 1,
                                                              "checks": "target 1", **(args or {})}))
    return said, target, population


def measured(world):
    return [o for o in world.scene.objects.values() if (o.made_by or {}).get("how") == "measured"]


def test_a_first_guess_that_holds_takes_its_name_and_how_sure_to_the_measured_outline(world):
    from orchestrator.known import look_record

    logged = []
    said, guess, population = asked(world, cell_box(4), log=logged.append)  # (420, 340, 25, 25): the largest
    assert said.startswith("Measured: my first guess holds (it is the largest by area)")
    assert guess.id not in world.scene.objects
    (m,) = measured(world)
    assert m.label == "biggest cell" and m.status == "likely"
    (line,) = [r for r in logged if r.get("rule") == "first-guess"]
    assert line["verdict"] == "holds" and line["iou"] >= 0.3 and line["removed"] == guess.id
    assert any(k["kind"] == "same-object" and k["to"] == guess.id and "IoU" in k["basis"] for k in population.links)

    # its name, how sure and how it was named go with it (review B1)
    world2_guess = {"label": "neutrophil", "status": "unsure", "confidence": 0.4,
                    "naming": look_record(label="neutrophil", model="gemini-3.5-flash", confidence=0.4)}
    for o in measured(world):
        world.scene.remove(o.id)

    def named(target):
        world.scene.update(target.id, **world2_guess)  # the look named it, unsure

    said, guess, _ = asked(world, cell_box(4), between=named)
    (m,) = measured(world)
    assert m.label == "neutrophil" and m.status == "unsure" and m.confidence == 0.4 and (m.naming or {}).get("by") == "look"


def test_a_wrong_first_guess_is_corrected_in_plain_words_and_its_name_stays_off_the_measured_cell(world):
    said, guess, _ = asked(world, cell_box(1))  # (380, 210, 20, 20): the second largest
    assert said.startswith("Measured: my first guess was not it (it is the 2nd of 6 by area); the measured one is outlined now")
    assert "c" + "1" not in said.split(";")[0]  # no member ids in the verdict (review S5)
    assert guess.id not in world.scene.objects
    (m,) = measured(world)
    x0, y0, x1, y1 = m.bbox
    assert x0 < 420 < x1 and y0 < 340 < y1  # the largest is outlined in its place
    assert m.label == "largest by area" and (m.naming or {}).get("by") == "request"  # not the guess's name (review B1)
    from orchestrator.seen import SeenToday

    assert m.naming.get("basisLabel") and not SeenToday.eligible(m)  # how it was chosen is never kept as a name


def test_a_guess_that_cant_be_compared_is_kept_beside_and_says_why(world):
    def lost(target):
        world.frame(0.0, 0.0, epoch=1)  # continuity lost between the guess and the measured view

    registry = Registry()
    add_highlight(registry, world, sight=type("S", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "which is the biggest cell?")
    with using(issue(world, "turn")):
        guess = outline_of(world, cell_box(1))
        ctx.objects.insert(0, guess.id)
        world.frame(0.0, 0.0, epoch=1)
        population, _ = run(tools.outline_cells(world))
        said = run(registry.tools["highlight"].handler(ctx, {"of": population.id, "by": "area", "top": 1, "checks": "target 1"}))
    assert said.startswith("Measured: my first guess is kept beside the measured one (the slide lost track")
    assert guess.id in world.scene.objects  # nothing assumed: both stay


def test_a_guess_on_no_measured_cell_is_said_and_kept(world):
    said, guess, _ = asked(world, (620, 120, 660, 160))
    assert "no measured cell lies where the guess is now" in said and guess.id in world.scene.objects


def test_only_a_guess_this_requests_look_made_is_taken_off(world):
    """Review B2: an outline reused from an earlier answer, or one the user named, is never removed or renamed."""
    earlier = outline_of(world, cell_box(1), label="megakaryocyte", status="confirmed", source="ai:look")
    time.sleep(0.01)
    said, guess, _ = asked(world, before=earlier)
    assert guess is earlier and earlier.id in world.scene.objects and earlier.label == "megakaryocyte"
    assert "my first guess's outline is kept as it was (you named it)" in said
    for o in measured(world):
        world.scene.remove(o.id)
    reused = outline_of(world, cell_box(1), label="cell")  # the look's, from an earlier answer
    time.sleep(0.01)
    said, guess, _ = asked(world, before=reused)
    assert reused.id in world.scene.objects and "it was outlined before this request" in said


def test_a_guess_named_while_the_cells_are_measured_is_the_users_and_stays(world):
    from orchestrator.known import renamed

    def named(target):  # the user names it during the 1-8 s (name_outline keeps the rename as theirs)
        world.scene.update(target.id, label="eosinophil", naming=renamed(target, "you", "eosinophil"))

    said, guess, _ = asked(world, cell_box(1), between=named)
    assert guess.id in world.scene.objects and world.scene.objects[guess.id].label == "eosinophil"
    assert "kept as it was (you named it)" in said

    def relabelled(target):  # a label its record does not explain is not the look's any more
        world.scene.update(target.id, label="lymphocyte")

    said, guess, _ = asked(world, cell_box(1), between=relabelled)
    assert guess.id in world.scene.objects and "kept as it was (you named it)" in said


def test_a_guess_that_holds_stays_while_the_measured_outline_is_not_shown(world, monkeypatch):
    """Review S4: on "holds" the guess is taken off only when the measured outline is drawn."""
    import orchestrator.population as population_mod

    real = population_mod.promote

    async def unplaced(*a, **k):
        made = await real(*a, **k)
        for o in made:  # placed only where verified (contract 3): this one could not be, yet
            o.track, o.unplaced = "unverified", "its pixels are not where the slide's move puts it now"
        return made

    monkeypatch.setattr(population_mod, "promote", unplaced)
    said, guess, _ = asked(world, cell_box(4))
    assert not any(o.shown for o in measured(world))
    assert guess.id in world.scene.objects and "kept as it was (the measured outline is not shown yet)" in said


def test_cells_measured_before_the_guess_are_not_used_to_correct_it(world):
    """Review S2: an older population may be another view; only this request's own or a later one checks it."""
    population, _ = run_cells(world)
    registry = Registry()
    add_highlight(registry, world, sight=type("S", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "which is the biggest cell?")
    with using(issue(world, "turn")):
        time.sleep(0.01)
        guess = outline_of(world, cell_box(1))
        ctx.objects.insert(0, guess.id)
        said = run(registry.tools["highlight"].handler(ctx, {"of": population.id, "by": "area", "top": 1, "checks": "target 1"}))
    assert said.startswith("I couldn't check my first guess: these cells were measured at") and guess.id in world.scene.objects


def test_only_this_requests_own_target_is_checked_and_failures_say_the_guess_was_not_checked(world):
    """Review S3 and S1: "that" is not a guess; a step that made nothing says the guess went unchecked."""
    from orchestrator.brain.registry import ToolFailed

    clicked = outline_of(world, cell_box(1), label="cell", source="click")
    said, _, _ = asked(world, None, {"checks": "that"}, before=None)
    assert said.startswith("I couldn't check my first guess: it isn't outlined") and clicked.label == "cell"
    registry = Registry()
    add_highlight(registry, world, sight=type("S", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "which is the biggest cell?")
    with using(issue(world, "turn")):
        with pytest.raises(ToolFailed) as failed:
            run(registry.tools["highlight"].handler(ctx, {"of": "@1", "by": "area", "top": 1, "checks": "target 1"}))
    assert str(failed.value).startswith("I couldn't check my first guess: ") and failed.value.next


def run_cells(world):
    with using(issue(world, "command")):
        return run(tools.outline_cells(world))


def test_an_answer_that_never_says_guess_is_made_to_and_the_verdict_is_remembered():
    """Review B3 and S7, through the real brain (the acting bench's stand-in slide and models): the model's
    answer says "This one is the biggest cell." with a plain label; a step checking target 1 makes it a first
    guess in the outline and actual answer facts, and the verdict is a note in the conversation. The synthetic
    external final provider answers from checked operations; app receipts are not assistant prose."""
    import asyncio

    from bench.acting import run as acting
    from bench.acting.world import World as ActingWorld

    (case,) = [c for c in acting.load_cases() if c["id"] == "chat-biggest-cell-first-guess-unsaid"]

    async def go():
        world = ActingWorld(case, 0.1)
        marked = []
        update = world.hub.scene.update

        def watch(oid, **changes):
            if str(changes.get("label", "")).endswith("?"):
                marked.append((oid, changes["label"], changes.get("note")))
            return update(oid, **changes)

        world.hub.scene.update = watch
        await acting.play(world, case["events"])
        await world.clock.sleep(case["expect"]["settle"])
        return world, marked

    world, marked = asyncio.run(go())
    answer = next(c.text for c in world.claims if c.by == "chat")
    expected = case["vision"][0]["reply"]["final"]["answer"]
    assert answer == expected  # scripted external model reply, validated against actual operations
    assert marked and marked[0][1] == "cell?" and "a first guess by eye, being measured" in marked[0][2]
    notes = [t.text for t in world.conversation.turns if t.role == "note"]
    assert any(n.startswith("highlight finished: Measured: my first guess was not it") for n in notes)
    prompts = world.agent.fast.prompts
    assert any('Targets [1] are first guesses by eye; their measurement checks have not completed at planning time.' in prompt
               for prompt in prompts)
    assert any('outline_cells' in prompt and 'highlight' in prompt and 'answer-facts' in prompt for prompt in prompts)
    assert [t.text for t in world.conversation.turns if t.role == 'assistant'] == [expected]
    turn = next(row for row in reversed(world.agent.logged) if row.get('kind') == 'turn')
    assert 1 <= turn['calls'] <= 3 and turn['answer'] == expected
    assert any(row.get('kind') == 'answer-rewritten' or row.get('rule') == 'answer-rewritten'
               for row in world.agent.logged)


# --- the partner's re-check of the review fixes ---------------------------------------------------------
def test_a_users_confirmed_guess_that_holds_is_never_outlined_again_or_its_confirmed_copied(world, monkeypatch):
    """Re-check B-new-1: with the measurement in doubt (a review flag), nothing was removed, a second outline was
    promoted and the user's "Confirmed" copied onto it."""
    import orchestrator.population as population_mod

    mine = outline_of(world, cell_box(4), label="megakaryocyte", status="confirmed")
    real = population_mod.review
    monkeypatch.setattr(population_mod, "review", lambda p: [(m.id, "solidity 0.70 (maybe two merged)") for m in p.live()])
    time.sleep(0.01)
    said, guess, _ = asked(world, before=mine)
    monkeypatch.setattr(population_mod, "review", real)
    confirmed = [o for o in world.scene.objects.values() if o.status == "confirmed"]
    assert confirmed == [mine] and not measured(world)  # the cell is not outlined twice, and nothing copied
    assert said.startswith("Measured: my first guess holds") and "the measurement may be off" in said


def test_a_name_given_while_the_cells_are_outlined_keeps_the_guess(world, monkeypatch):
    """Re-check SF1: the user confirms the guess while promote runs; it is no longer removed."""
    import orchestrator.population as population_mod
    from orchestrator.known import renamed

    real = population_mod.promote
    seen = {}

    async def confirming(hub, population, ids, **kw):
        g = seen["guess"]
        hub.scene.update(g.id, label="eosinophil", status="confirmed", naming=renamed(g, "you", "eosinophil"))
        return await real(hub, population, ids, **kw)

    def remember(target):
        seen["guess"] = target

    monkeypatch.setattr(population_mod, "promote", confirming)
    said, guess, _ = asked(world, cell_box(1), between=remember)
    assert guess.id in world.scene.objects and world.scene.objects[guess.id].status == "confirmed"
    assert "kept as it was (you named it)" in said


def test_a_target_that_failed_never_shifts_the_ones_after_it_and_a_measured_outline_is_no_guess(world):
    """Re-check SF2: "target 2" is the look's second target, whatever became of the first; a measured outline
    (a check's own result) is never taken for a guess."""
    from orchestrator.brain.toolbox.view import first_guess_of, made_by_this_request

    ctx = ToolContext("chat", 1, None, "which is the biggest cell?")
    with using(issue(world, "turn")):
        second = outline_of(world, cell_box(5), label="smallest cell")
        ctx.objects[:0] = [second.id]
        ctx.targets = [None, second.id]  # the first target could not be outlined
        assert first_guess_of(world, "target 1", ctx) is None and first_guess_of(world, "target 2", ctx) is second
        said, _, _ = asked(world, (620, 120, 660, 160))
        (m,) = measured(world)
        assert not made_by_this_request(m)


def test_a_guess_not_marked_by_the_look_is_marked_and_its_note_says_what_the_measurement_said(world):
    """Re-check SF3 and SF4: a guess the fast pass made (no "?" yet) is marked when it is checked, and once
    measuring ends its note never says "being measured"."""
    registry = Registry()
    add_highlight(registry, world, sight=type("S", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "which is the biggest cell?")
    with using(issue(world, "turn")):
        guess = outline_of(world, cell_box(1), label="cell")
        ctx.objects.insert(0, guess.id)
        world.frame(0.0, 0.0, epoch=1)  # they can't be compared: the guess is kept
        population, _ = run(tools.outline_cells(world))
        run(registry.tools["highlight"].handler(ctx, {"of": population.id, "by": "area", "top": 1, "checks": "target 1"}))
    kept = world.scene.objects[guess.id]
    assert kept.label == "cell?" and kept.note.startswith("a first guess by eye; not checked: the slide lost track")
    assert "being measured" not in kept.note


def test_a_label_naming_the_guess_never_goes_onto_another_cell(world):
    """Re-check SF5: the model passes label "biggest cell" (the guess's name) and the guess was not it."""
    said, guess, _ = asked(world, cell_box(1), {"label": "biggest cell"})
    (m,) = measured(world)
    assert "was not it" in said and m.label == "largest by area"


def test_how_do_you_know_says_a_measured_cell_was_outlined_by_the_cell_finder(world):
    from orchestrator.resources import Index

    said, guess, population = asked(world, cell_box(4))
    (m,) = measured(world)
    read = Index(world).read(m.id)
    assert f"as cell {m.made_by['member']} of {population.id}, then promoted" in read and "the app's look by" not in read


# --- the partner's second re-check --------------------------------------------------------------------------
def test_a_guess_confirmed_while_the_cells_are_outlined_never_makes_the_app_write_confirmed(world, monkeypatch):
    """Pins the guard: the user confirms the guess during promote and the guess holds; the measured outline
    never gets "Confirmed" from the app."""
    import orchestrator.population as population_mod
    from orchestrator.known import renamed

    real = population_mod.promote
    seen = {}

    async def confirming(hub, population, ids, **kw):
        g = seen["guess"]
        hub.scene.update(g.id, status="confirmed", naming=renamed(g, "you", g.label.rstrip("?")))
        return await real(hub, population, ids, **kw)

    monkeypatch.setattr(population_mod, "promote", confirming)
    said, guess, _ = asked(world, cell_box(4), between=lambda t: seen.update(guess=t))
    assert [o for o in world.scene.objects.values() if o.status == "confirmed"] == [guess]


def test_with_top_three_the_guesss_name_goes_only_to_its_own_cell(world):
    """Second re-check SF1: with top 3 the 1st and 3rd largest became "neutrophil" too."""
    from orchestrator.known import look_record

    def named(target):
        world.scene.update(target.id, label="neutrophil", naming=look_record(label="neutrophil", model="gemini-3.5-flash"))

    said, guess, _ = asked(world, cell_box(1), {"top": 3}, between=named)  # the 2nd largest: among the top 3
    labels = sorted(o.label for o in measured(world))
    assert labels.count("neutrophil") == 1 and said.startswith("Measured: my first guess holds")


def test_a_check_that_fails_says_so_in_the_guess_and_how_do_you_know(world):
    """Second re-check: the failure path's note, and known.py, never "being measured" after it."""
    from orchestrator.resources import Index

    registry = Registry()
    add_highlight(registry, world, sight=type("S", (), {"view": None})(), outline_seen=None)
    ctx = ToolContext("chat", 1, None, "which is the biggest cell?")
    ctx.step = 2
    ctx.steps.tools.update({1: "outline_cells", 2: "highlight"})
    with using(issue(world, "turn")):
        guess = outline_of(world, cell_box(1), label="cell")
        ctx.objects.insert(0, guess.id)
        with pytest.raises(ToolFailed):
            run(registry.tools["highlight"].handler(ctx, {"of": "@1", "by": "area", "top": 1, "checks": "target 1"}))
    kept = world.scene.objects[guess.id]
    assert kept.note.startswith("a first guess by eye; not checked: ")
    read = Index(world).read(guess.id)
    assert "a first guess by eye (not checked:" in read and "being measured" not in read


def test_the_board_takes_target_n_by_the_looks_own_number(world):
    from orchestrator.board import Board

    with using(issue(world, "turn")):
        o = outline_of(world, cell_box(2), label="cell")
        ctx = ToolContext("chat", 1, None, "zoom target 2")
        ctx.objects = [o.id]
        ctx.targets = [None, o.id]  # target 1 could not be outlined
        assert Board(world, listen=False).resolve("target 2", ctx) is o
