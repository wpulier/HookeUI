"""The Board (board.py): what is on screen as one state, and what a request's "that" means on it."""
from types import SimpleNamespace

import pytest

from orchestrator.board import CLICK_RECENT_S, Board
from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.scene import SceneObject
from tests.fakes import Hub


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def outline(hub, label, x, source="ai:gemini", created=0.0, kind="highlight"):
    o = SceneObject(id=hub.scene.new_id(), kind=kind, polygons=[[[x, 100], [x + 40, 100], [x + 40, 140], [x, 140]]],
                    anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source=source, label=label,
                    created_at=created)
    return hub.scene.add(o)


def setup(magnifier=None):
    hub, clock = Hub(), Clock()
    return hub, Board(hub, magnifier=magnifier, clock=clock), clock


def ctx(objects=(), aliases=None):
    return ToolContext("chat", 1, 7, "q", objects=list(objects), aliases=dict(aliases or {}))


def test_the_state_has_boxes_shares_and_the_latest_changes():
    hub, board, clock = setup(SimpleNamespace(open=True, object_id=None, level=2.0))
    a = outline(hub, "stoma", 100, created=clock.t)
    hub.scene.select(a.id)
    state = board.now()
    item = state.item(a.id)
    assert item.box == (100, 100, 140, 140) and item.share == pytest.approx(1600 / (1280 * 720))
    assert item.in_view and item.source == "ai:gemini"
    assert state.selected == a.id and state.magnifier == {"open": True, "object": None, "level": 2.0}
    assert [(c.op, c.ids) for c in state.recent] == [("add", (a.id,)), ("select", (a.id,))]
    hub.scene.clear()
    assert board.now().recent[-1].op == "clear" and board.now().items == ()


def test_that_means_this_requests_outline_then_the_selection_then_a_recent_click_then_the_magnifier():
    mag = SimpleNamespace(open=False, object_id=None)
    hub, board, clock = setup(mag)
    ai = outline(hub, "xylem", 100, created=clock.t - 60)
    mine = outline(hub, "guard cell", 300, source="click", created=clock.t - 2)
    zoomed = outline(hub, "pore", 500, created=clock.t - 30)
    mag.open, mag.object_id = True, zoomed.id
    assert board.resolve("that", ctx([ai.id])) is ai  # made by this request
    hub.scene.select(zoomed.id)
    assert board.resolve("it", ctx()) is zoomed  # the selection
    hub.scene.select(None)
    assert board.resolve("this one", ctx()) is mine  # clicked a moment ago
    clock.t += CLICK_RECENT_S + 1
    assert board.resolve(None, ctx()) is zoomed  # the click is old: what the magnifier shows
    mag.open = False
    assert board.resolve("the highlight", ctx()) is zoomed  # the newest the app made


def test_the_one_i_clicked_and_ordinals():
    hub, board, clock = setup()
    first = outline(hub, "red cell", 100, created=1.0)
    second = outline(hub, "red cell", 300, created=2.0)
    third = outline(hub, "platelet", 500, created=3.0)
    mine = outline(hub, "nucleus", 700, source="click", created=0.5)
    assert board.resolve("the one I clicked", ctx()) is mine
    assert board.resolve("the second one", ctx()) is second
    assert board.resolve("the last one", ctx()) is third
    assert board.resolve("second", ctx([third.id, first.id])) is first  # of what this request outlined
    with pytest.raises(ToolFailed, match="only 3"):
        board.resolve("the fifth one", ctx())


def test_ids_targets_labels_and_aliases_as_before():
    hub, board, _ = setup()
    a = outline(hub, "stoma", 100, created=1.0)
    b = outline(hub, "guard cell", 300, created=2.0)
    assert board.resolve(a.id, ctx()) is a
    assert board.resolve("target 1", ctx([b.id])) is b
    assert board.resolve("stoma", ctx()) is a and board.resolve("guard", ctx()) is b
    assert board.resolve("o99", ctx(aliases={"o99": a.id})) is a
    with pytest.raises(ToolFailed, match="can't find 'nucleus'"):
        board.resolve("nucleus", ctx())


def test_nothing_on_screen_says_so():
    hub, board, _ = setup()
    with pytest.raises(ToolFailed, match="ask me to find it first"):
        board.resolve("that", ctx())


def test_a_target_this_answer_did_not_outline_says_what_to_do():
    """Live 2026-10-10 20:33: the look outlined nothing and its zoom named "target 1"; the step says so as a fact the
    request's recovery can act on (find it first), not "click it"."""
    hub, board, _ = setup()
    outline(hub, "cabinet door", 100)  # something else on screen is never taken for it
    with pytest.raises(ToolFailed, match="outlined nothing, so there is no 'target 1'") as refused:
        board.resolve("target 1", ctx())
    assert "find it first" in refused.value.next
    b = outline(hub, "left eye", 300)
    with pytest.raises(ToolFailed, match="outlined 1, so there is no 'target 2'"):
        board.resolve("target 2", ctx([b.id]))


def test_cells_and_arena_arms_are_not_what_that_means():
    hub, board, clock = setup()
    outline(hub, "cell", 100, source="cells", kind="cell", created=5.0)
    arm = outline(hub, "stoma", 300, created=6.0)
    arm.trial = "t1"
    picked = outline(hub, "stoma", 500, created=1.0)
    assert board.resolve("that", ctx()) is picked
    assert all(i.id != arm.id for i in board.now().items)


def asked(board, words="zoom on that"):
    """A request made now, as Brain.turn saves it (C2)."""
    return ToolContext("voice", 1, 7, words, when_asked=board.when_asked())


def test_that_means_what_was_pointed_at_when_the_request_was_made():
    """The partner's Amendment 1, C2: a selection or click made while the request was being answered is not what
    "that" meant; without a saved pointing, it is the screen now. The override log says what now would have been."""
    hub, logged, clock = Hub(), [], Clock()
    mag = SimpleNamespace(open=False, object_id=None)
    board = Board(hub, magnifier=mag, clock=clock, log=logged.append)
    a = outline(hub, "stoma", 100, created=clock.t - 5)
    b = outline(hub, "pore", 300, created=clock.t - 4)
    hub.scene.select(a.id)
    clicked_before = outline(hub, "guard cell", 500, source="click", created=clock.t - 1)
    request = asked(board)
    clock.t += 2  # while it is being answered:
    hub.scene.select(b.id)
    clicked_after = outline(hub, "dot", 700, source="click", created=clock.t)
    assert board.resolve("that", request) is a
    assert logged[-1]["asOf"] == "the request" and logged[-1]["nowWouldBe"] == b.id
    assert board.resolve("that", ctx()) is b  # no saved pointing: now
    assert board.resolve("the one I clicked", request) is clicked_before
    assert board.resolve("the one I clicked", ctx()) is clicked_after
    hub.scene.select(None)  # cleared since: the selection then still stands while it is on screen
    assert board.resolve("that", request) is a
    # Nothing selected then: the click before it, then what the magnifier was open on then, never one opened since.
    hub.scene.select(None)
    mag.open, mag.object_id = True, a.id  # not the newest outline: only the saved magnifier names it
    clock.t += CLICK_RECENT_S + 1
    request = asked(board)
    mag.object_id = clicked_after.id
    assert board.resolve("that", request) is a


def test_what_was_selected_when_asked_is_kept_however_much_is_outlined_meanwhile():
    """The executor's P2: the selection is saved on the request, not rebuilt from the changes kept (30), which two
    finds' outlines once pushed it out of, and "that" became the newest red cell nobody pointed at."""
    hub, board, clock = setup()
    stoma = outline(hub, "stoma", 100, created=clock.t - 60)
    hub.scene.select(stoma.id)
    for i in range(29):
        outline(hub, f"red cell {i}", 200 + i, created=clock.t - 30 + i * 0.1)
    request = asked(board)
    clock.t += 3
    later = [outline(hub, f"red cell {29 + i}", 300 + i, created=clock.t) for i in range(40)]
    hub.scene.select(later[0].id)  # the user's newer pointing, while it is answered
    assert len(board.changes) == board.changes.maxlen
    assert board.resolve("that", request) is stoma


def test_a_selection_gone_since_the_request_is_said_never_replaced():
    """The executor's P5: what was selected when the user asked was removed before the tool ran: said, with what to
    do, instead of acting on an older outline. Re-outlined by this answer, it is the new outline."""
    hub, board, clock = setup()
    outline(hub, "xylem", 100, created=clock.t - 60)
    stoma = outline(hub, "stoma", 300, created=clock.t - 30)
    hub.scene.select(stoma.id)
    request = asked(board)
    hub.scene.remove(stoma.id)
    with pytest.raises(ToolFailed) as refused:
        board.resolve("that", request)
    assert "no longer on screen" in str(refused.value) and refused.value.next == "click it again"
    again = outline(hub, "stoma", 300, created=clock.t)
    request.aliases[stoma.id] = again.id
    assert board.resolve("that", request) is again


def test_nothing_outlined_when_asked_is_said_not_something_made_after():
    """The executor's P4: with nothing outlined when the user asked, "that" is not the click made after it."""
    hub, board, clock = setup()
    request = asked(board)
    clock.t += 1
    after = outline(hub, "dot", 100, source="click", created=clock.t)
    with pytest.raises(ToolFailed) as refused:
        board.resolve("that", request)
    assert "nothing was outlined when you asked" in str(refused.value) and refused.value.next
    assert board.resolve("that", ctx()) is after


def test_this_requests_own_selection_comes_before_what_was_selected_when_asked():
    """The executor's P3: "select the stoma and measure it": the request's own step pointed at the stoma, so "it"
    in the steps after is the stoma, not the pore selected when the user asked."""
    hub, board, clock = setup()
    stoma = outline(hub, "stoma", 100, created=clock.t - 10)
    pore = outline(hub, "pore", 300, created=clock.t - 5)
    hub.scene.select(pore.id)
    request = asked(board, "select the stoma and measure it")
    request.pointed = stoma.id  # the select step (toolbox/scene.py)
    assert board.resolve("it", request) is stoma
    assert board.resolve(None, request) is stoma


def test_the_comparison_with_now_never_fails_a_resolve():
    """The executor's P8: a blind arm selected when the user asked, nothing pointed at now: the log's comparison
    finds nothing, and the resolve still answers."""
    hub, logged, clock = Hub(), [], Clock()
    board = Board(hub, clock=clock, log=logged.append)
    arm = outline(hub, "stoma", 100, created=clock.t - 5)
    arm.trial = "t1"
    hub.scene.select(arm.id)
    request = asked(board)
    hub.scene.select(None)
    assert board.resolve("that", request) is arm
    assert logged[-1]["asOf"] == "the request" and logged[-1]["nowWouldBe"] is None


def test_an_unknown_explicit_id_after_a_select_step_is_refused():
    """An ID that never existed cannot mean a different outline selected by this request.
    Deictic 'it' still names the request's own selection, ahead of the older selection."""
    hub, board, clock = setup()
    stoma = outline(hub, "stoma", 100, created=clock.t - 10)
    pore = outline(hub, "pore", 300, created=clock.t - 5)
    hub.scene.select(pore.id)
    request = asked(board, "select the stoma and measure o99")
    request.pointed = stoma.id
    with pytest.raises(ToolFailed):
        board.resolve("o99", request)
    assert board.resolve("it", request) is stoma
    assert hub.scene.selected == pore.id


def test_a_known_replaced_id_resolves_only_through_its_exact_request_alias():
    hub, board, clock = setup()
    original = outline(hub, "stoma", 100, created=clock.t - 10)
    pore = outline(hub, "pore", 300, created=clock.t - 5)
    hub.scene.select(pore.id)
    request = asked(board, f"outline the stoma again and measure {original.id}")
    hub.scene.remove(original.id)
    replacement = outline(hub, "stoma", 100, created=clock.t)
    request.pointed = replacement.id
    with pytest.raises(ToolFailed):
        board.resolve(original.id, request)
    request.aliases[original.id] = replacement.id
    assert board.resolve(original.id, request) is replacement
    assert board.resolve("it", request) is replacement
    with pytest.raises(ToolFailed):
        board.resolve("o99", request)  # an unrelated alias never licenses global retargeting


def test_a_click_newer_than_the_selection_is_what_that_meant():
    """The #179 re-check's SF2 (PN4): a click on empty space outlines without selecting, so a stoma selected half a
    minute ago is not what "that" means right after a click on the pore: the newer pointing wins, each with its time
    (C2). A selection made after the click, or a click no longer just before the request, leaves the selection."""
    hub, board, clock = setup()
    stoma = outline(hub, "stoma", 100, created=clock.t - 60)
    hub.scene.select(stoma.id)
    hub.scene.selected_at = clock.t - 30
    pore = outline(hub, "pore", 300, source="click", created=clock.t - 2)
    assert board.resolve("that", asked(board)) is pore
    hub.scene.selected_at = clock.t - 1  # selected after the click
    assert board.resolve("that", asked(board)) is stoma
    hub.scene.selected_at = clock.t - 30
    clock.t += CLICK_RECENT_S + 5  # the click is no longer just before the request
    assert board.resolve("that", asked(board)) is stoma


def test_step_reference_uses_its_actual_outline_and_never_an_unrelated_selection():
    hub, board, _ = setup()
    original = outline(hub, 'debris', 100)
    other = outline(hub, 'cell', 300)
    context = ctx()
    context.step = 2
    context.steps.tools = {1: 'find', 2: 'zoom'}
    context.steps.produced = {1: [original.id]}
    hub.scene.select(other.id)
    assert board.resolve('@1', context) is original
    hub.scene.remove(original.id)
    with pytest.raises(ToolFailed, match="can't find"):
        board.resolve('@1', context)


def test_how_it_was_made_names_the_whole_model_the_cloud_and_the_mac_one_apart():
    """2026-10-11 review: cutting a name at " (" made "SAM 3.1 (fal cloud)" and "SAM 3.1 (on this Mac)" the same
    "SAM 3.1". The line is read from the outline's own record: the model that found it and the one that drew it."""
    from orchestrator.board import how_made

    cloud = SimpleNamespace(source="ai:gemini-3.8-flash", created_at=0.0, made_by={"implementation": "sam3.1-fal"})
    assert "found by Gemini 3.8 Flash" in how_made(cloud) and "drawn by SAM 3.1 (cloud)" in how_made(cloud)
    mac = SimpleNamespace(source="ai:gemini-3.8-flash", created_at=0.0, made_by={"implementation": "sam3.1-local"})
    assert "SAM 3.1 (this Mac)" in how_made(mac)
    click = SimpleNamespace(source="click", created_at=0.0, made_by={"implementation": "edgetam"})
    assert how_made(click).startswith("found by the user's click at ") and "EdgeTAM (on this Mac)" in how_made(click)


AT = 1791707460.0  # 2026-10-11, early morning; said in the machine's own time zone below


def made(hub, *, source, made_by=None, naming=None, checked=None, label="stoma", status="likely", box=None):
    o = outline(hub, label, 300, source=source, created=AT)
    o.made_by, o.naming, o.checked, o.status, o.origin_box = made_by, naming, checked, status, box
    return o


def test_how_it_was_made_is_each_model_in_order_from_the_outlines_own_record():
    """Slice 2 (2026-10-11): "which model did you use?" was answered "our built-in vision model"; Gemini 3.8 Flash had
    found it and EdgeTAM drawn it. The chain is read from the outline's own record (known.chain): found by, drawn by,
    checked by, named by, followed by; the same account `resources(id=...)` reads."""
    import time

    from orchestrator import known
    from orchestrator.board import how_made

    hub = Hub()
    clock = time.strftime("%H:%M:%S", time.localtime(AT))
    # A click: the user's click, the outline model, the model that named it.
    click = made(hub, source="click", made_by={"operation": "outline", "implementation": "edgetam", "policy": "route",
                                               "origin": "click"},
                 naming={"by": "model", "option": "gemini-3.5-flash", "label": "stoma"})
    assert how_made(click) == (f"found by the user's click at {clock}, drawn by EdgeTAM (on this Mac), "
                               "named by Gemini 3.5 Flash")
    drag = made(hub, source="click", made_by={"implementation": "edgetam"}, box=(300, 100, 340, 140), status="outline")
    assert how_made(drag) == f"found by the user's drag at {clock}, drawn by EdgeTAM (on this Mac), not named yet"
    # A find: the look's model found and named it; the default outline model drew it.
    find = made(hub, source="ai:gemini-3.8-flash", made_by={"implementation": "edgetam", "policy": "route"},
                naming=known.look_record(label="stoma", model="gemini-3.8-flash"))
    assert how_made(find) == "found by Gemini 3.8 Flash, drawn by EdgeTAM (on this Mac), named by Gemini 3.8 Flash"
    # A `use` redraw: the model the request chose.
    chosen = made(hub, source="ai:gemini-3.8-flash", made_by={"implementation": "sam2.1-tiny", "policy": "raw",
                                                               "origin": "user-chosen"},
                  naming=known.look_record(label="stoma", model="gemini-3.8-flash"))
    assert "drawn by SAM 2.1 tiny (on this Mac) as chosen for that request" in how_made(chosen)
    # A fallback is said as one.
    fell = made(hub, source="ai:gemini-3.8-flash", made_by={"implementation": "sam3.1-fal", "fallbackFrom": "edgetam"})
    assert "drawn by SAM 3.1 (cloud) after EdgeTAM (on this Mac) could not" in how_made(fell)
    # Repaired after the check: what drew it first, what the check said, what drew it again.
    repaired = made(hub, source="ai:gemini-3.8-flash", made_by={"implementation": "sam3.1-fal", "origin": "background"},
                    checked={"option": "gemini-3.5-flash", "fits": "too_big", "redrawn": True, "before": "edgetam"},
                    naming=known.look_record(label="stoma", model="gemini-3.8-flash"))
    assert how_made(repaired) == ("found by Gemini 3.8 Flash, drawn by EdgeTAM (on this Mac), checked by Gemini 3.5 Flash "
                                  "(it took in too much) and redrawn by SAM 3.1 (cloud), named by Gemini 3.8 Flash")
    kept = made(hub, source="ai:gemini-3.8-flash", made_by={"implementation": "edgetam"},
                checked={"option": "gemini-3.5-flash", "fits": "yes"})
    assert "checked by Gemini 3.5 Flash (it fits)" in how_made(kept)
    # Followed: the follower's stamp (objtrack.py), its shape kept.
    find.followed_by = {"model": "edgetam-video", "shape": "kept"}
    assert how_made(find).endswith(", followed by EdgeTAM video (on this Mac) with its shape kept")
    # Renamed by the user: the name is theirs.
    click.naming = known.renamed(click, "you", "guard cell")
    assert how_made(click).endswith("named by the user")
    # An unvoted blind arm says nothing of its model.
    click.trial = "t1"
    assert "EdgeTAM" not in how_made(click)
    # The same account is what "how do you know?" reads, first.
    facts = known.outline_facts(hub, find)
    assert facts.splitlines()[1] == f"How it was made, each model in order: {how_made(find)}."
    # And each part names a model only as names.py says it (one name each, no commas inside a part).
    assert all("," not in part for part in known.chain(repaired))
