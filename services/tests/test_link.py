"""Trackastra as the LINK operation's implementation (M3.5): two populations of the same specimen, linked
member to member with its own score; nothing it did not link; only views the slide's measured move relates,
compared where they overlap; can't tell (with why) otherwise; "not installed here" when it is not; non-commercial
in this list (its weights' data terms are unknown, and it brings Gurobi)."""
import asyncio
import sys
import threading
from types import ModuleType, SimpleNamespace

import networkx as nx
import numpy as np
import pytest

from orchestrator import tools
from orchestrator.brain.registry import ToolFailed
from orchestrator.operations import OPERATIONS, availability
from orchestrator.ticket import issue, using
from tests.test_apply import run
from tests.test_corrections import keeping, saved
from tests.test_population import world  # noqa: F401 (the fixture)


def stand_in(monkeypatch, edges, seen):
    """Trackastra 0.5.6 as it is: from_folder(folder, device=) on the catalog's pinned revision (never from_pretrained,
    which fetches its own), and track() returning (a networkx DiGraph whose nodes carry time and label, edges weight;
    the tracked masks)."""
    from noncommercial import trackastra_link

    class Trackastra:
        @classmethod
        def from_folder(cls, folder, device="cpu"):
            seen["weights"] = folder
            return cls()

        def track(self, stack, labels, mode="greedy"):
            seen["mode"] = mode
            seen["copies"] = (stack.astype(np.float32) / 255.0, np.zeros_like(labels))  # Trackastra's normalise and zeros
            seen["shapes"] = (stack.shape, labels.shape, int(labels[0].max()), int(labels[1].max()))
            seen["thread"] = threading.current_thread() is threading.main_thread()
            seen["labels"], seen["stack"] = labels, stack
            graph = nx.DiGraph()
            for name, (t, label) in {"a": (0, 2), "b": (1, 3), "c": (1, 99), "d": (0, 1), "e": (1, 1)}.items():
                graph.add_node(name, time=t, label=label, coords=(0, 0))
            for u, v, w in edges:
                graph.add_edge(u, v, weight=w)
            return graph, labels

    package, model = ModuleType("trackastra"), ModuleType("trackastra.model")
    model.Trackastra = Trackastra
    monkeypatch.setitem(sys.modules, "trackastra", package)
    monkeypatch.setitem(sys.modules, "trackastra.model", model)
    monkeypatch.setattr(trackastra_link, "_model", None)
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    monkeypatch.setenv("MICROMIND_MODEL_FILES", "standin")  # its pinned files counted as downloaded


def two_views(world, tmp_path):
    keeping(world, tmp_path)
    with using(issue(world, "command")):
        first, _ = run(tools.outline_cells(world))
        second, _ = run(tools.outline_cells(world))
    return first, second


def link(world, a, b):
    from orchestrator import operations

    impl = OPERATIONS["link"].implementations[0]
    return asyncio.run(operations._trackastra(SimpleNamespace(hub=world), None, None, impl, {"a": a.id, "b": b.id}))


def test_linking_maps_trackastras_labels_to_each_populations_own_members(world, tmp_path, monkeypatch):
    from orchestrator.population import correct

    first, second = two_views(world, tmp_path)
    saved(first)
    correct(world, first, "remove", [first.live()[0].id], by="user")  # live() no longer follows the model's numbers
    saved(first)
    saved(second)  # saved: their pixels were released, so the maps are read from disk
    seen = {}
    # a->b links time 0 to time 1; a->c names no member; d->e... e->d runs backwards in time: neither is linked
    stand_in(monkeypatch, [("a", "b", 0.87), ("a", "c", 0.5), ("e", "d", 0.9)], seen)
    out = link(world, first, second)
    a_live, b_live = first.live(), second.live()
    assert out["links"] == [{"from": a_live[1].id, "to": b_live[2].id, "score": 0.87, "kind": "same-object", "basis": "trackastra"}]
    assert seen["mode"] == "greedy_nodiv"  # same object means no divisions
    from gpu_worker import manifests as catalog
    from gpu_worker.model_files import revision_dir

    assert seen["weights"] == revision_dir(catalog.get("trackastra")), "its pinned revision in the catalog"
    assert seen["shapes"][2:] == (len(a_live), len(b_live))
    assert seen["thread"] is False  # in a thread, never on the event loop
    assert all(m.mask is None for p in (first, second) for m in p.members.values())  # read for the maps, never kept
    assert first._labels is None and second._labels is None


def moved(world, tmp_path, dx, dy):
    """Two views, the second after the slide moved (dx, dy): its picture moved, and the motion tracker measured it."""
    keeping(world, tmp_path)
    with using(issue(world, "command")):
        first, _ = run(tools.outline_cells(world))
    world.frame(dx, dy)
    with using(issue(world, "command")):
        second, _ = run(tools.outline_cells(world))
    return first, second


def test_the_second_view_is_placed_by_the_slides_measured_move_and_only_the_overlap_is_compared(world, tmp_path, monkeypatch):
    first, second = moved(world, tmp_path, 100.0, -40.0)
    assert (second.view["dx"] - first.view["dx"], second.view["dy"] - first.view["dy"]) == (100.0, -40.0)
    seen = {}
    stand_in(monkeypatch, [], seen)
    out = link(world, first, second)
    w, h = first.view["width"], first.view["height"]
    assert out["shift"] == [100, -40] and out["links"] == []
    assert max(abs(v) for v in out["lineUp"]["residual"]) <= 1.0  # the two cuts were measured to show the same place
    assert seen["shapes"][0] == (2, h - 40, w - 100)
    import numpy as np

    b = second.labels()
    assert b[0:h - 40, 100:w].any() and np.array_equal(seen["labels"][1], b[0:h - 40, 100:w])  # its own place, moved
    assert np.array_equal(seen["labels"][0], first.labels()[40:h, 0:w - 100])
    import cv2

    from orchestrator.evidence import evidence_of

    gray = cv2.imdecode(np.frombuffer(evidence_of(world).capture_jpeg(second), np.uint8), cv2.IMREAD_GRAYSCALE)
    assert np.array_equal(seen["stack"][1], gray[0:h - 40, 100:w].astype(np.float32))  # the picture moved with its labels


def test_views_the_slides_move_cannot_relate_are_said_and_never_linked(world, tmp_path, monkeypatch):
    first, second = two_views(world, tmp_path)
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.9)], seen)
    second.view["epoch"] = (first.view.get("epoch") or 0) + 1
    with pytest.raises(ToolFailed) as lost:
        link(world, first, second)
    assert "can't tell which cells" in str(lost.value) and "lost continuity" in str(lost.value)
    second.view["epoch"] = first.view.get("epoch")
    second.scale_id = {**(second.scale_id or {}), "objective": "100x"}
    first.scale_id = {**(first.scale_id or {}), "objective": "40x"}
    with pytest.raises(ToolFailed) as zoomed:
        link(world, first, second)
    assert "the objective changed" in str(zoomed.value)
    second.scale_id = dict(first.scale_id)
    second.view.update(dx=first.view["dx"] + 5000.0)
    with pytest.raises(ToolFailed) as apart:
        link(world, first, second)
    assert "the two views do not overlap" in str(apart.value)
    assert "shapes" not in seen  # Trackastra was never asked


def test_masks_that_cannot_be_read_back_are_cant_tell_never_no_links(world, tmp_path, monkeypatch):
    first, second = two_views(world, tmp_path)
    saved(first)
    saved(second)
    (second.path / "masks.npz").unlink()
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.9)], seen)
    with pytest.raises(ToolFailed) as refused:
        link(world, first, second)
    assert "masks could not be read back" in str(refused.value) and "shapes" not in seen


def test_a_run_that_made_links_is_not_empty_and_is_said_to_run_on_this_mac():
    from orchestrator.brain.toolbox.choosing import OPERATION_WORDS
    from orchestrator.operations import _has_output, alternatives_text

    link = OPERATIONS["link"]
    assert _has_output({"links": [{"from": "c1", "to": "c2"}]}, link) and not _has_output({"links": []}, link)
    assert set(OPERATION_WORDS) == set(OPERATIONS)  # use(operation="link") has its words
    text = alternatives_text(SimpleNamespace(settings=None), "link")
    assert "Trackastra [trackastra]: on this Mac (its processor)" in text and "app code" not in text


def test_trackastra_is_listed_non_commercial_with_gurobi(monkeypatch):
    from orchestrator import licences

    assert licences.COMPONENTS["trackastra"].tier == "noncommercial" and licences.COMPONENTS["gurobipy"].tier == "noncommercial"
    assert licences.COMPONENTS["trackastra"].adapter == "noncommercial.trackastra_link"
    impl = OPERATIONS["link"].implementations[0]
    monkeypatch.setenv("MICROMIND_EDITION", "commercial")
    assert "edition" in (availability(SimpleNamespace(settings=None), impl) or "")


def test_without_trackastra_installed_linking_says_so_and_how_to_set_it_up(monkeypatch):
    import importlib.util

    impl = OPERATIONS["link"].implementations[0]
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a: None if name == "trackastra" else real(name, *a))
    monkeypatch.setenv("MICROMIND_EDITION", "research")
    assert availability(SimpleNamespace(settings=None), impl) == "not installed here (trackastra: its Install button on the Models page)"



def test_a_cell_linked_to_two_is_linked_to_neither_and_said(world, tmp_path, monkeypatch):
    """M3.5's re-check: a division (or two candidates) is not the same object as either."""
    first, second = two_views(world, tmp_path)
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.8), ("a", "e", 0.7)], seen)  # a -> b and a -> e: a divides into two members
    out = link(world, first, second)
    a_live, b_live = first.live(), second.live()
    assert out["links"] == []  # neither of the two is the same object as a
    assert out["ambiguous"] == sorted([a_live[1].id, b_live[2].id, b_live[0].id])  # a and both its daughters
    assert out["ambiguousEdges"] == [{"from": a_live[1].id, "to": b_live[2].id, "score": 0.8},
                                     {"from": a_live[1].id, "to": b_live[0].id, "score": 0.7}]
    assert out["compared"] == {"a": len(a_live), "b": len(b_live)}  # the views overlap whole: every cell compared


def test_two_cells_linked_to_one_are_linked_to_neither_and_both_named(world, tmp_path, monkeypatch):
    """The second re-check: a merge (two cells to one) is as unsure as a division."""
    first, second = two_views(world, tmp_path)
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.8), ("d", "b", 0.6)], seen)  # a -> b and d -> b: two cells, one target
    out = link(world, first, second)
    a_live, b_live = first.live(), second.live()
    assert out["links"] == []
    assert out["ambiguous"] == sorted([a_live[1].id, a_live[0].id, b_live[2].id])


def test_views_of_two_runs_or_an_unregistered_view_are_never_linked(world, tmp_path, monkeypatch):
    """M3.5's re-check (BLOCKING): epoch numbers start again with every run of the app."""
    first, second = two_views(world, tmp_path)
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.9)], seen)
    first.view["run"] = "123-earlier"
    with pytest.raises(ToolFailed) as restarted:
        link(world, first, second)
    assert "the app restarted between the two views" in str(restarted.value)
    for unknown in ((None, second.view["run"]), (second.view["run"], None), (None, None)):  # either way round, or both
        first.view["run"], second.view["run"] = unknown[0], unknown[1] if unknown[1] is not None else None
        with pytest.raises(ToolFailed) as unrecorded:
            link(world, first, second)
        assert "it is not known which run of the app made one of the views" in str(unrecorded.value)
    from orchestrator.capture import RUN

    second.view["run"] = RUN
    first.view["run"] = second.view["run"]
    second.view["tracked"] = False
    with pytest.raises(ToolFailed) as moving:
        link(world, first, second)
    assert "not registered" in str(moving.value) and "shapes" not in seen


def test_a_view_a_verified_rejoin_carried_is_linked_by_the_rejoins_offset(world, tmp_path, monkeypatch):
    """Read as the cells' layer reads a view (population._ViewOf): another epoch rejoined by a verified match is the
    same epoch, its offset moved by the rejoin's delta."""
    keeping(world, tmp_path)
    world.frame(30.0, 0.0)  # the first view, 30 px along: its epoch (5) started its own offsets at 0 there
    with using(issue(world, "command")):
        first, _ = run(tools.outline_cells(world))
    world.frame(0.0, 0.0)
    with using(issue(world, "command")):
        second, _ = run(tools.outline_cells(world))
    seen = {}
    stand_in(monkeypatch, [], seen)
    old = second.view.get("epoch") or 0
    first.view.update(epoch=old + 5, dx=0.0, dy=0.0)
    world.epoch_links = {old + 5: (second.view.get("epoch") or 0, (30.0, 0.0))}  # the rejoin moved its offsets 30 px
    out = link(world, first, second)
    assert out["shift"] == [-30, 0] and "shapes" in seen  # placed by the rejoin's offset, not the old epoch's numbers


def test_this_runs_rejoins_never_join_two_views_of_an_earlier_run(world, tmp_path, monkeypatch):
    """The second re-check (BLOCKING): epoch numbers start again with every run, so a rejoin this run verified between
    epochs 5 and 0 says nothing about an earlier run's epochs 5 and 0 (it once linked 48 pairs of different cells)."""
    first, second = two_views(world, tmp_path)
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.9)], seen)
    old = first.view.get("epoch") or 0
    first.view["epoch"] = old + 5
    first.view["run"] = second.view["run"] = "12345-earlier"  # one earlier run made both views
    world.epoch_links = {old + 5: (second.view.get("epoch") or 0, (30.0, 0.0))}  # this run's rejoin, same numbers
    with pytest.raises(ToolFailed) as lost:
        link(world, first, second)
    assert "lost continuity" in str(lost.value) and "shapes" not in seen


def test_only_the_cells_inside_the_overlap_are_counted_as_compared(world, tmp_path, monkeypatch):
    """The second re-check (BLOCKING): a cell outside the part both views show was not compared, so it is can't tell,
    never "not the same" (ADR 0012)."""
    from orchestrator import operations

    first, second = two_views(world, tmp_path)
    second.view.update(dx=first.view["dx"] + 800.0)  # the overlap: x < 480 of the first, x >= 800 of the second
    # The stand-in slide pans at most 400 px, so its pictures can't show this move: what is compared is this test's
    # matter, not whether the views line up (tested below).
    monkeypatch.setattr(operations, "line_up", lambda *a, **k: {"residual": [0.0, 0.0], "response": 1.0})
    seen = {}
    stand_in(monkeypatch, [], seen)
    out = link(world, first, second)
    a_in = {int(n) for n in np.unique(seen["labels"][0]) if n}
    b_in = {int(n) for n in np.unique(seen["labels"][1]) if n}
    assert 0 < len(a_in) < len(first.live()) and not b_in  # one of the first's cells and all the second's are outside
    assert out["compared"] == {"a": len(a_in), "b": 0}


def test_an_unregistered_capture_is_never_linked_through_the_tool(world, tmp_path, monkeypatch):
    """The view's `tracked` comes from the capture itself (population.view_facts), not only from a test's edit."""
    keeping(world, tmp_path)
    world.latest_info.tracked = False  # the motion tracker could not register this frame
    with using(issue(world, "command")):
        first, _ = run(tools.outline_cells(world))
    world.frame(0.0, 0.0)  # the next frame, registered
    with using(issue(world, "command")):
        second, _ = run(tools.outline_cells(world))
    assert first.view["tracked"] is False and second.view["tracked"] is True
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.9)], seen)
    with pytest.raises(ToolFailed) as moving:
        link(world, first, second)
    assert "not registered" in str(moving.value) and "shapes" not in seen


def test_an_eyepiece_circle_of_another_size_is_never_linked(world, tmp_path, monkeypatch):
    """contract 3's geometry: the disc radius within 0.5%."""
    first, second = two_views(world, tmp_path)
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.9)], seen)
    first.view["discPx"], second.view["discPx"] = 300.0, 303.0  # the eyepiece circle's measured radius, 1% apart
    with pytest.raises(ToolFailed) as scaled:
        link(world, first, second)
    assert "can't tell which cells" in str(scaled.value) and "shapes" not in seen


def test_linking_reserves_its_own_footprint(world, tmp_path, monkeypatch):
    """The reservation is the measured `link` footprint, not another kind's (the cells' is under half of it)."""
    from orchestrator import capture

    first, second = two_views(world, tmp_path)
    stand_in(monkeypatch, [], {})
    kinds, real = [], capture.reserving

    def spy(hub, w, h, kind, **kw):
        kinds.append(kind)
        return real(hub, w, h, kind, **kw)

    monkeypatch.setattr(capture, "reserving", spy)
    link(world, first, second)
    assert kinds == ["link"]


def test_what_linking_holds_stays_inside_its_reservation(world, tmp_path, monkeypatch):
    """M3.5's re-check: the first version held about 34 bytes a pixel against 30 reserved. Measured with tracemalloc,
    the model's own copies included (the stand-in makes them as Trackastra does)."""
    import tracemalloc

    from orchestrator.capture import footprint

    first, second = two_views(world, tmp_path)
    seen = {}
    stand_in(monkeypatch, [], seen)
    tracemalloc.start()
    tracemalloc.reset_peak()
    link(world, first, second)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak <= footprint(first.view["width"], first.view["height"], 0, "link")


def test_the_receipt_says_what_trackastra_is_installed(world, tmp_path, monkeypatch):
    from noncommercial import trackastra_link

    first, second = two_views(world, tmp_path)
    stand_in(monkeypatch, [], {})
    monkeypatch.setattr(trackastra_link, "installed_version", lambda: "0.5.7")
    out = link(world, first, second)
    assert out["producer"] == {"implementation": "trackastra", "weights": "general_2d", "mode": "greedy_nodiv", "version": "0.5.7"}
    assert OPERATIONS["link"].implementations[0].uses == ("trackastra", "gurobipy")  # Gurobi named with it


def test_views_whose_pictures_do_not_line_up_where_the_slides_move_puts_them_are_never_linked(world, tmp_path, monkeypatch):
    """The K2 re-check, note 1: nothing checked that the offsets were right (80 px wrong once kept two wrong links)."""
    keeping(world, tmp_path)
    with using(issue(world, "command")):
        first, _ = run(tools.outline_cells(world))
    world.frame(40.0, 0.0)  # the slide moved 40 px...
    world.latest_info.dx = 120.0  # ...and the motion tracker measured 120 (both views registered)
    with using(issue(world, "command")):
        second, _ = run(tools.outline_cells(world))
    seen = {}
    stand_in(monkeypatch, [("a", "b", 0.9)], seen)
    with pytest.raises(ToolFailed) as off:
        link(world, first, second)
    assert ("the two views do not line up where the slide's measured move puts them (off by 80 px)" in str(off.value)
            and "shapes" not in seen)


def test_lining_up_is_measured_inside_both_eyepiece_circles_and_says_when_it_cannot_tell():
    import cv2

    from orchestrator.operations import line_up

    rng = np.random.default_rng(7)
    texture = cv2.GaussianBlur(rng.integers(0, 255, (720, 1280), dtype=np.uint8), (0, 0), 2.0).astype(np.float32)
    assert line_up(texture, texture, [None, None], 1280)["residual"] == [0.0, 0.0]
    odd = texture[:605, :609]  # measured 244 x 242 px: 242 comes out half a pixel off unless cut to an even optimal size
    assert line_up(odd, odd, [None, None], 1280)["residual"] == [0.0, 0.0]
    moved = np.roll(texture, 8, axis=1)
    assert line_up(texture, moved, [None, None], 1280)["residual"][0] == pytest.approx(8.0, abs=1.0)  # within 10 px
    said = line_up(texture, np.roll(texture, 14, axis=0), [None, None], 1280)  # measured at 512 px wide: about 1 px
    assert "do not line up" in said and 13 <= float(said.split("off by ")[1].split(" px")[0]) <= 15.5
    # The eyepiece circle's edge stays put in the frame while the slide moves: only what is inside both is compared.
    disc = np.zeros_like(texture)
    cv2.circle(disc, (640, 360), 300, 1.0, -1)
    a, b = texture * disc + 255.0 * (1 - disc), np.roll(texture, 30, axis=1) * disc + 255.0 * (1 - disc)
    circles = [(640.0, 360.0, 300.0), (640.0, 360.0, 300.0)]
    said = line_up(a, b, circles, 1280)
    assert "do not line up" in said and int(said.split("off by ")[1].split(" px")[0]) in (29, 30, 31)
    shifted_back = np.roll(b, -30, axis=1)  # the slide's content lined up, the circle's edge now 30 px apart
    assert line_up(a, shifted_back, [(640.0, 360.0, 300.0), (610.0, 360.0, 300.0)], 1280)["residual"] == [0.0, 0.0]
    flat = np.full_like(texture, 128.0)
    assert "match too weakly" in line_up(texture, flat + rng.normal(0, 1, texture.shape).astype(np.float32), [None, None], 1280)
    assert "too small to check" in line_up(texture, texture, [(640.0, 360.0, 40.0), None], 1280)


def test_each_eyepiece_circle_is_given_in_its_own_cuts_px(world, tmp_path, monkeypatch):
    """The circle stays put in the frame while the slide moves, so each view's is placed in its own cut."""
    from orchestrator import operations

    first, second = moved(world, tmp_path, 100.0, -40.0)  # the first's cut starts at (0, 40), the second's at (100, 0)
    first.view["disc"] = second.view["disc"] = [640.0, 360.0, 300.0]
    seen, real = {}, operations.line_up

    def spy(a, b, discs, width):
        seen["discs"] = discs
        return real(a, b, discs, width)

    monkeypatch.setattr(operations, "line_up", spy)
    stand_in(monkeypatch, [], {})
    out = link(world, first, second)
    assert seen["discs"] == [(640.0, 320.0, 300.0), (540.0, 360.0, 300.0)] and out["lineUp"]["response"] > 0.5

