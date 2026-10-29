"""Coordinate spaces (contract 10, invariant 14 of docs/design/2026-10-06-captures-operations-evidence.md;
orchestrator/spaces.py and the studio's spaces.ts): every overlay, close-up and screen item states which
space its coordinates are in, from one small vocabulary; the mappings between spaces round-trip."""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from orchestrator import screen, spaces
from orchestrator.coords import Crop
from orchestrator.scene import Scene

ORCHESTRATOR = Path(__file__).resolve().parents[1] / "orchestrator"
STUDIO = Path(__file__).resolve().parents[2] / "apps" / "studio" / "src"


# --- everything the server sends that carries geometry states its space ------------------------------------
GEOMETRY = {"polygons", "polygon", "box", "bbox", "box_2d", "points", "center", "rect", "fov"}
# ("origin" is left out: it is provenance in many records; the slide map's own is covered by spaces.WIRES.)
# Dicts that carry a geometry key and a "type"/"kind" but are not overlays sent to the studio, with why.
NOT_OVERLAYS = {
    ("workspace_views.py", "output"): "a saved output Tab descriptor with an absent specimen rectangle, not a geometric overlay",
    ("agent.py", "molmo-point"): "an interaction-log record, never sent to the studio",
    ("agent.py", "override"): "an interaction-log record, never sent to the studio",
    ("hub.py", "frame"): "the frame's own header: its pixels are the frame space itself",
    ("prompts.py", "object"): "a JSON schema for a model's reply",
    ("prompts.py", "array"): "a JSON schema for a model's reply",
    ("work.py", "object"): "a JSON schema for a model's reply",
    ("work.py", "array"): "a JSON schema for a model's reply",
    ("partner_tools.py", "object"): "the JSON schema of the voice's mark tool (its region and polygon are fractions of the camera picture)",
    ("partner_tools.py", "array"): "the JSON schema of the voice's mark tool (its region and polygon are fractions of the camera picture)",
}


def keys_of(node: ast.Dict) -> dict:
    return {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}


def carries_geometry(node: ast.Dict) -> set[str]:
    """The geometry keys anywhere in this dict literal, including dicts nested in lists and comprehensions
    (a candidate's x, y and r count as geometry)."""
    found = set()
    for inner in ast.walk(node):
        if isinstance(inner, ast.Dict):
            ks = set(keys_of(inner))
            found |= ks & GEOMETRY
            if {"x", "y"} <= ks:
                found |= {"x", "y"}
    return found


def is_call(value, attr: str, arg: str | None = None) -> bool:
    ok = isinstance(value, ast.Call) and isinstance(value.func, ast.Attribute) and value.func.attr == attr
    return ok and (arg is None or [a.value for a in value.args if isinstance(a, ast.Constant)] == [arg])


def problems_in(source: str, name: str) -> tuple[list[str], set[str]]:
    """Every dict literal with a constant "type" or "kind" that carries geometry (found by what it carries, not
    by a list of names) must state its space; the ones in WIRES must state exactly the vocabulary's entry."""
    problems, seen = [], set()
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Dict):
            continue
        ks = keys_of(node)
        if {"version", "objects", "selected"} <= set(ks):  # Scene.to_wire
            seen.add("scene")
            if not is_call(ks.get("space"), "of_wire", "scene"):
                problems.append(f"{name}:{node.lineno}: the scene wire does not state its space (spaces.of_wire('scene'))")
        is_message = isinstance(ks.get("type"), ast.Constant)  # a "type" is a wire message; a "kind" a record
        tag = ks.get("type") if is_message else ks.get("kind")
        if not (isinstance(tag, ast.Constant) and isinstance(tag.value, str)):
            continue
        kind = tag.value
        spread = [v for k, v in zip(node.keys, node.values) if k is None]
        if kind == "scene" and spread:
            seen.add(kind)
            # A message built from another dict (`**scene.to_wire()`): the receiver must be the Scene itself.
            for v in spread:
                receiver = v.func.value if isinstance(v, ast.Call) and isinstance(v.func, ast.Attribute) else None
                last = receiver.attr if isinstance(receiver, ast.Attribute) else getattr(receiver, "id", "")
                if not (is_call(v, "to_wire") and last == "scene"):
                    problems.append(f"{name}:{node.lineno}: the scene message spreads something other than scene.to_wire()")
            continue
        geometry = carries_geometry(node)
        if is_message and kind in spaces.WIRES:
            seen.add(kind)
        elif not geometry or (name, kind) in NOT_OVERLAYS:
            continue
        value = ks.get("space")
        if is_message and kind in spaces.WIRES:
            ok = is_call(value, "of_wire", kind)
        elif kind == "labels":  # a researcher's annotated picture: its boxes are fractions of the picture
            unit = ks.get("unit")
            ok = (isinstance(value, ast.Constant) and value.value == spaces.PICTURE
                  and isinstance(unit, ast.Constant) and unit.value == "fraction")
        else:
            ok = False
        if kind == "labels":
            seen.add("labels")
        if not ok:
            what = f"carries {sorted(geometry)} " if geometry else ""
            problems.append(f"{name}:{node.lineno}: the {kind!r} message {what}and does not state its space")
    return problems, seen


def test_every_wire_that_carries_geometry_states_its_space():
    problems, seen = [], set()
    for root in (ORCHESTRATOR, ORCHESTRATOR.parent / "research_worker"):
        for path in sorted(root.rglob("*.py")):
            found, kinds = problems_in(path.read_text(), path.name)
            problems += found
            seen |= kinds
    problems += [f"wire {k!r} is declared in spaces.WIRES but no message of that type is built" for k in sorted(set(spaces.WIRES) - seen)]
    assert "labels" in seen
    assert problems == []


def test_every_exemption_says_why_and_still_names_a_real_record():
    """A dict exempted as not an overlay says why, and still exists (the partner's re-check of #201, note 1)."""
    built = set()
    for root in (ORCHESTRATOR, ORCHESTRATOR.parent / "research_worker"):
        for path in sorted(root.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Dict):
                    ks = keys_of(node)
                    tag = ks.get("type") if isinstance(ks.get("type"), ast.Constant) else ks.get("kind")
                    if isinstance(tag, ast.Constant) and carries_geometry(node):
                        built.add((path.name, tag.value))
    assert all(why.strip() for why in NOT_OVERLAYS.values())
    assert sorted(set(NOT_OVERLAYS) - built) == []


def test_a_wire_with_geometry_and_no_space_is_caught_without_being_listed():
    # a type nobody declared, carrying x/y/r, polygons or a box: found by what it carries
    bad = [('{"type": "blobs", "items": [{"x": 1, "y": 2, "r": 3}]}', "blobs"),
           ('{"type": "marks", "polygons": []}', "marks"),
           ('{"kind": "pins", "rows": [{"box": [0, 0, 1, 1]}]}', "pins"),
           ('{"type": "spots", "bbox": [0, 0, 4, 4]}', "spots"),
           ('{"kind": "hits", "rows": [{"box_2d": [0, 0, 500, 500]}]}', "hits"),
           ('{"type": "trails", "trails": []}\n{"type": "slidemap", "space": "map"}', "'trails'")]
    for source, name in bad:
        found, _ = problems_in(source, "demo.py")
        assert found and name in found[0], source
    # states its space: fine; an undeclared type with no geometry: not an overlay
    assert problems_in('{"type": "blobs", "space": "frame", "items": [{"x": 1, "y": 2}]}', "d.py")[0] != []  # must be a declared wire
    assert problems_in('{"type": "hello", "health": {}}', "d.py")[0] == []
    assert problems_in('{"type": "trails", "space": spaces.of_wire("trails")}', "d.py")[0] == []


def test_the_scene_wire_checks_its_producers_not_just_the_scene():
    spread = '{"type": "scene", **self.scene.to_wire()}'
    assert problems_in(spread, "d.py")[0] == []
    assert problems_in('{"type": "scene", **self.other.to_wire()}', "d.py")[0]
    assert problems_in('{"type": "scene", **self.scene.state()}', "d.py")[0]
    assert problems_in('{"version": 1, "objects": [], "selected": None}', "d.py")[0]  # Scene.to_wire without space


def test_a_researchers_annotated_boxes_state_a_picture_in_fractions_and_map_to_px():
    from research_worker.results import labels_content

    content = labels_content([{"name": "stoma", "where": "centre", "box": [0.25, 0.5, 0.75, 1.0]}])
    assert content["space"] == spaces.PICTURE and content["unit"] == "fraction" and content["unit"] in spaces.PICTURE_UNITS
    assert content["items"][0]["box"] == [0.25, 0.5, 0.75, 1.0]
    assert spaces.picture_to_px((0.25, 0.5), (800, 600)) == (200.0, 300.0)
    assert spaces.px_to_picture(spaces.picture_to_px((0.3, 0.7), (800, 600)), (800, 600)) == pytest.approx((0.3, 0.7))


def test_the_vocabulary_is_small_and_every_declaration_uses_it():
    assert set(spaces.SPACES) == {"frame", "capture", "workspace", "map", "picture"}
    for table in (spaces.WIRES, spaces.SOURCE_SPACES):
        assert all(spaces.valid(v) for v in table.values())
    assert all(v is None or spaces.valid(v) for v in spaces.PRESENTATION_SPACES.values())


def test_the_scene_trails_slide_map_and_magnifier_state_their_space():
    from orchestrator.slidemap import SlideMap
    from orchestrator.trail import Trails
    from tests.test_magnifier import FakeHub, stoma

    assert Scene().to_wire()["space"] == "frame"
    trails = Trails(lambda: 1000.0).wire()
    assert trails["space"] == "frame" and trails["offset"] == [0, 0]
    assert SlideMap(lambda fid: None, background=False).wire()["space"] == "map"
    hub = FakeHub([0])
    stoma(hub)
    from orchestrator.magnifier import Magnifier

    m = Magnifier(hub)
    m.zoom("o1")
    m.hold()
    m.show_image("/research/x.png", "Annotated", credit="Researcher 1")
    wire = m.wire()
    assert wire["space"] == "frame"  # its centre is in the object's anchor frame's pixels
    assert {s["kind"]: s["space"] for s in wire["sources"]} == {"live": "frame", "held": "capture", "image": "picture"}


# --- every presentation kind and screen item states a space -----------------------------------------------
def test_every_presentation_kind_declares_the_space_of_its_contents():
    kinds = set(screen.TAKES) | set(screen.NAMES)
    undeclared = sorted(k for k in kinds if k not in spaces.PRESENTATION_SPACES)
    assert undeclared == [], f"presentation kinds with no declared space: {undeclared}"
    for kind in kinds - {"magnifier", "entry"}:
        assert spaces.valid(spaces.item_space(kind)), kind
    assert spaces.item_space("magnifier", None) == "frame"
    assert spaces.item_space("magnifier", "live") == "frame" and spaces.item_space("magnifier", "held") == "capture"
    assert spaces.item_space("magnifier", "/research/x.png") == "picture"
    for kind, (presentation, _why) in screen.PRESENTED_AS.items():
        assert presentation is None or presentation in spaces.PRESENTATION_SPACES, kind


def test_a_report_item_always_has_a_space_and_the_studios_own_is_kept():
    s = screen.Screen()
    s.report({"items": [{"id": "live", "kind": "live"}, {"id": "outlines", "kind": "outlines"},
                        {"id": "magnifier", "kind": "magnifier", "source": "held", "open": True},
                        {"id": "slidemap", "kind": "slidemap", "open": True},
                        {"id": "x", "kind": "viewer", "space": "bogus"}, {"id": "e", "kind": "entry"}, {"id": "z", "kind": "weird"},
                        {"id": "layer:e1", "kind": "population", "space": "capture"}]})
    got = {i["id"]: i["space"] for i in s.latest["items"]}
    assert got == {"live": "frame", "outlines": "frame", "magnifier": "capture", "slidemap": "map", "x": "picture",
                   "layer:e1": "capture", "e": "workspace", "z": "workspace"}


# --- the studio's table is this one ------------------------------------------------------------------------
def studio_table(name: str) -> dict[str, str]:
    text = (STUDIO / "spaces.ts").read_text()
    body = re.search(rf"export const {name}[^=]*= \{{(.*?)\n?\}};", text, re.S).group(1)
    return {k.strip("'\" "): v for k, v in re.findall(r"""('?[\w-]+'?):\s*'(\w+)'""", body)}


def test_the_studios_spaces_are_the_servers():
    assert studio_table("WIRES") == spaces.WIRES
    assert studio_table("SOURCE_SPACES") == spaces.SOURCE_SPACES
    studio_items = studio_table("ITEM_SPACES")
    # the studio lists what is not the workspace's default; a kind in neither (entry, anything new) is the workspace's on both
    assert studio_items == {k: v for k, v in spaces.PRESENTATION_SPACES.items() if v and k != "entry"}
    for kind in list(spaces.PRESENTATION_SPACES) + ["weird"]:
        if kind != "magnifier":
            assert spaces.item_space(kind) == studio_items.get(kind, "workspace"), kind
    text = (STUDIO / "spaces.ts").read_text()
    assert tuple(re.findall(r"'(\w+)'", re.search(r"SPACES: readonly Space\[\] = \[(.*?)\]", text).group(1))) == spaces.SPACES


# --- the mappings round-trip --------------------------------------------------------------------------------
def close(a, b):
    return a == pytest.approx(b, abs=1e-9)


def test_frame_and_capture_map_by_the_offsets_and_back():
    p, anchor, now = (120.0, 80.0), (10.0, -4.0), (35.5, 9.25)
    there = spaces.frame_to_frame(p, anchor, now)
    assert there == (145.5, 93.25)
    assert close(spaces.frame_to_frame(there, now, anchor), p)
    # the same slide place seen from either frame: slide px do not depend on the frame
    assert close(spaces.frame_to_slide(p, anchor), spaces.frame_to_slide(there, now))
    assert close(spaces.slide_to_frame(spaces.frame_to_slide(p, anchor), anchor), p)
    assert spaces.capture_to_frame(p, anchor, now, correction=(2.0, -1.0)) == (147.5, 92.25)
    assert spaces.capture_to_frame(p, anchor, anchor) == p  # a capture of the frame now is that frame


def test_the_slide_map_maps_slide_px_by_its_scale_and_origin_and_back():
    p, offset, scale, origin = (640.0, 360.0), (-120.0, 40.0), 0.25, (-50.0, -20.0)
    on_map = spaces.frame_to_map(p, offset, scale, origin)
    assert on_map == ((640 + 120) * 0.25 + 50, (360 - 40) * 0.25 + 20)
    assert close(spaces.slide_to_frame(spaces.map_to_slide(on_map, scale, origin), offset), p)


def test_an_enlarged_view_is_a_crop_and_scale_of_its_capture_and_round_trips():
    crop, scale = Crop(200, 100, 160, 120), 4.0
    assert spaces.view_to_capture((0.0, 0.0), crop, scale) == (200.0, 100.0)
    assert spaces.view_to_capture((640.0, 480.0), crop, scale) == (360.0, 220.0)  # the crop's far corner
    for p in [(0.0, 0.0), (321.5, 77.25), (640.0, 480.0)]:
        back = spaces.capture_to_view(spaces.view_to_capture(p, crop, scale), crop, scale)
        assert close(back, p)


# --- pen marks (invariant 14): not built; nothing may burn drawing into a capture --------------------------
# Pen marks are the UX's later work. When they are built they reach models only as prompts on the clean capture,
# so what holds now is that nothing draws on a capture's own bytes or its cached decode. This is a partial check
# (a drawing call whose target is written as `x.bgr()` / `x.gray()` / `x.jpeg`, and an assignment to `.jpeg`); a
# drawing on a variable that came from them is not seen. The runtime half: a Capture refuses to change.
DRAW = {"line", "polylines", "rectangle", "circle", "ellipse", "putText", "fillPoly", "fillConvexPoly", "drawContours",
        "arrowedLine", "drawMarker"}


def burns_into_a_capture(source: str, name: str) -> list[str]:
    problems = []
    for node in ast.walk(ast.parse(source)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in DRAW
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "cv2" and node.args):
            target = node.args[0]
            if (isinstance(target, ast.Call) and isinstance(target.func, ast.Attribute) and target.func.attr in ("bgr", "gray")) \
                    or (isinstance(target, ast.Attribute) and target.attr == "jpeg"):
                problems.append(f"{name}:{node.lineno}: cv2.{node.func.attr} draws on a capture's own pixels")
        if isinstance(node, (ast.Assign, ast.AugAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                while isinstance(t, ast.Subscript):
                    t = t.value
                if isinstance(t, ast.Attribute) and t.attr == "jpeg" and not (isinstance(t.value, ast.Name) and t.value.id == "self"):
                    problems.append(f"{name}:{node.lineno}: assigns to a capture's .jpeg")
    return problems


def test_nothing_draws_into_a_captures_pixels():
    problems = [p for path in sorted(ORCHESTRATOR.rglob("*.py")) for p in burns_into_a_capture(path.read_text(), path.name)]
    assert problems == []
    assert burns_into_a_capture("cv2.polylines(capture.bgr(), pts, True, 1)", "x.py")  # the check can fail
    assert burns_into_a_capture("capture.jpeg = b''", "x.py")


def test_a_capture_cannot_be_changed_after_it_is_taken():
    from types import SimpleNamespace

    from orchestrator.capture import Capture

    capture = Capture(1, b"jpeg", SimpleNamespace(width=4, height=4, wall_at=1.0))
    with pytest.raises(AttributeError):
        capture.jpeg = b"drawn on"


def test_the_frame_reaches_the_workspace_through_the_layout_and_back():
    # view.ts placeFrame gives scale and origin for each layout; the studio's spaces.test.ts feeds these numbers from it.
    for scale, ox, oy, canvas in [(0.8, 120.0, 40.0, (0.0, 0.0)), (1.1, -30.0, 25.5, (0.0, 56.0)), (0.35, 5.0, 60.0, (0.0, 0.0))]:
        p = (640.0, 360.0)
        w = spaces.frame_to_workspace(p, scale, ox, oy, canvas)
        assert w == (canvas[0] + ox + p[0] * scale, canvas[1] + oy + p[1] * scale)
        assert close(spaces.workspace_to_frame(w, scale, ox, oy, canvas), p)


def test_a_zoomed_magnifier_maps_its_frame_window_to_the_panel_and_back():
    x0, y0, k, panel = 600.0, 300.0, 8.0, (900.0, 120.0)
    assert spaces.frame_to_magnifier((600.0, 300.0), x0, y0, k, panel) == panel  # the window's top-left is the panel's
    p = (612.5, 307.25)
    assert close(spaces.magnifier_to_frame(spaces.frame_to_magnifier(p, x0, y0, k, panel), x0, y0, k, panel), p)


def test_a_stills_original_maps_to_the_frames_by_its_fit_and_back():
    """S5a: the Stage's still in the bridge's frames, turned, scaled and letterboxed (still_fit.py); spaces.ts the same."""
    matrix = (0.0, -2.4, 759.3, 2.4, 0.0, 0.7)  # a quarter turn, x 2.4, 520 px in from the left
    there = spaces.still_to_frame((10.0, 20.0), matrix)
    assert close(there, (711.3, 24.7))
    assert close(spaces.frame_to_still(there, matrix), (10.0, 20.0))
