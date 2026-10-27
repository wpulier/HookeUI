"""The numbers the studio, the server and the sim harness share (contract/overlay.json): each
reader uses the file, and the magnifier's rules give the same numbers as the studio's
(apps/studio/src/overlay.test.ts has the same cases)."""
import asyncio
import inspect
import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from orchestrator import contract, magnifier, versions
from orchestrator.brain.toolbox import in_view
from orchestrator.frames import FrameSource
from orchestrator.research import ResearchManager
from sim import harness
from tests.test_research import FakeHub

OVERLAY = json.loads((Path(__file__).resolve().parents[2] / "contract" / "overlay.json").read_text())


def test_the_contract_is_read_from_the_file():
    assert contract.OVERLAY == OVERLAY
    assert contract.FRAME_DIM_MS == OVERLAY["frameDimMs"] == 1500
    assert contract.FEED_STALE_S == OVERLAY["feedStaleS"] == 3.0
    assert contract.MAGNIFIER_ASPECT == 4 / 3


def test_the_feed_goes_stale_after_feed_stale_s():
    default = inspect.signature(FrameSource.__init__).parameters["stale_seconds"].default
    assert default == OVERLAY["feedStaleS"]


@pytest.mark.parametrize("box, fit, level", [
    ((60, 60), (104, 78), 19.5),  # 78 x 78 padded, widened to 4:3; 78 / 4 px
    ((100, 50), (130, 97.5), 24.375),  # wide: made taller
    ((10, 10), (128 / 3, 32), 8),  # small: at least 32 px
    ((1000, 800), (4160 / 3, 1040), 64),  # large: at most 64x
])
def test_the_magnifier_fits_and_goes_as_close_as_the_studio(box, fit, level):
    w, h = magnifier.fit_window(*box)
    assert w == pytest.approx(fit[0]) and h == pytest.approx(fit[1])
    assert magnifier.level_limit((w, h)) == pytest.approx(level)
    assert magnifier.level_limit(None) == OVERLAY["magnifier"]["maxLevel"]


def test_the_magnifier_sends_its_max_level_and_when_it_is_pixel_enlarged():
    from tests.test_magnifier import FakeHub as FrameHub, stoma

    hub = FrameHub([0])
    stoma(hub)  # 60 px: fitted 104 x 78
    m = magnifier.Magnifier(hub)
    m.zoom("o1")
    assert hub.sent[-1]["maxLevel"] == 19.5 and hub.sent[-1]["pixelated"] is False
    # Pixel-enlarged past 6 screen px per camera px on the usual 352 px panel: level > 6 x 104 / 352.
    m.set_level(1.7)
    assert hub.sent[-1]["pixelated"] is False
    m.set_level(1.8)
    assert hub.sent[-1]["pixelated"] is True


def test_in_view_uses_the_in_view_radius():
    fov = SimpleNamespace(cx=500.0, cy=400.0, radius=100.0)
    info = SimpleNamespace(epoch=0, dx=0.0, dy=0.0, fov=fov)

    def at(x):
        return SimpleNamespace(track="tracking", anchor_epoch=0, center=(x, 400.0), anchor_offset=(0.0, 0.0))

    r = OVERLAY["inViewRadius"] * 100
    assert in_view(at(500 + r - 0.5), info)
    assert not in_view(at(500 + r + 0.5), info)


def test_the_sim_harness_judges_by_the_same_numbers():
    assert (harness.FADE_START_PX_PER_FRAME, harness.FADE_END_PX_PER_FRAME, harness.SPEED_WINDOW) == (
        OVERLAY["fadeStartPx"], OVERLAY["fadeEndPx"], OVERLAY["speedWindow"])
    assert harness.overlay_opacity([8.0]) == pytest.approx(0.5)
    header = {"fov": [500, 400, 100], "dx": 0, "dy": 0}
    r = OVERLAY["inViewRadius"] * 100
    square = lambda x: {"polygons": [[[x - 1, 399], [x + 1, 399], [x + 1, 401], [x - 1, 401]]], "anchorOffset": [0, 0]}
    assert harness._inside_view(square(500 + r - 0.5), header)
    assert not harness._inside_view(square(500 + r + 0.5), header)


@pytest.mark.parametrize("edition, runs", [(None, True), ("research", True), ("RESEARCH", True),
                                        ("commercial", False), ("reasearch", False)])
def test_the_cells_clicks_scenario_uses_the_same_edition_as_the_app(monkeypatch, edition, runs):
    if edition is None:
        monkeypatch.delenv("MICROMIND_EDITION", raising=False)
    else:
        monkeypatch.setenv("MICROMIND_EDITION", edition)

    class CameraStack:
        async def wait(self, predicate, *args):
            health = {"state": "live", "motion": {"tracked": True},
                      "workers": [{"ready": True, "resident": ["cellpose-sam"]}]}
            return bool(predicate(health)), health

    # No cameras, processes or GPU calls: synthetic selected/resident model state lets
    # an admitted edition report missing click spots rather than silently passing a skip.
    viewer = SimpleNamespace(settings={"cells": "cellpose-sam"}, candidates=[],
                             latest=lambda: (None, {"fov": None}, None))
    outcome = asyncio.run(harness.scenario_cells_clicks(CameraStack(), viewer))
    assert bool(outcome.checks) is runs
    if runs:
        assert [(check.what, check.ok) for check in outcome.checks] == [
            ("selected cells model is Cellpose-SAM", True),
            ("selected Cellpose is resident before admission", True),
            ("candidate spots inside the view", False),
        ]
        assert not outcome.passed


def test_work_is_listed_lesson_video_picture_then_older_versions_then_the_result(tmp_path):
    assert versions.KIND_ORDER == OVERLAY["kindOrder"]
    day = time.strftime("%Y-%m-%d")
    directory = tmp_path / day / "r1"
    directory.mkdir(parents=True)
    outputs = [
        {"id": "o1", "kind": "image", "path": "a.png", "caption": "", "version": 1},
        {"id": "o2", "kind": "html", "path": "l1.html", "caption": "", "version": 1},
        {"id": "o3", "kind": "html", "path": "l2.html", "caption": "", "version": 2, "replaces": "o2", "change": "easier"},
        {"id": "o4", "kind": "video", "path": "v.mp4", "caption": "", "version": 1},
    ]
    (directory / "session.json").write_text(json.dumps({
        "rid": "r1", "n": 1, "brief": "b", "deliverable": "research", "effort": "quick", "engine": "gpt-6.1-sol",
        "purpose": "research", "label": "l", "status": "done", "created": 0.0, "spent": 0.0, "outputs": outputs,
        "result": {"title": "x", "summary": "s"}}))
    session = ResearchManager(FakeHub(), root=tmp_path).sessions["r1"]
    assert [o["id"] for o, _ in versions.ordered(session.outputs)] == ["o3", "o4", "o1", "o2"]
    # The studio's result viewer shows these as they are (labels made once, here).
    expected_items = [
        {"kind": "html", "label": "Lesson v2", "id": "o3", "media": "lesson", "line": "A lesson written by a researcher"},
        {"kind": "video", "label": "AI video", "id": "o4", "media": "generated", "line": "AI video, not microscope footage"},
        {"kind": "image", "label": "Picture", "id": "o1", "media": "unknown", "line": "A picture; how it was made is not recorded"},
        {"kind": "html", "label": "Lesson v1", "id": "o2", "media": "lesson", "line": "A lesson written by a researcher"},
        {"kind": "summary", "label": "What it found", "id": None, "media": "written", "line": "What the researcher found, in its words"}]
    for item, (oid, version) in zip(expected_items, [("o3",2),("o4",1),("o1",1),("o2",1),("summary",None)]):
        item["work"] = {"researcherId":"r1", "resource":{"day":day,"id":oid,"version":version}}
    assert session.wire()["items"] == expected_items
