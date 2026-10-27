"""How big things are (orchestrator/scale.py, roadmap W4-1): microns per pixel from the objective,
a measured calibration, outline sizes, and the tools that set and use them."""
import asyncio
from types import SimpleNamespace

import pytest

from orchestrator.brain.registry import ToolContext, ToolFailed
from orchestrator.brain.toolbox import Services, build
from orchestrator.day import DayStore
from orchestrator.scale import Reading, Scale, round_um, size_words, sizes
from orchestrator.scene import SceneObject
from tests.fakes import Hub


def test_the_objective_gives_an_estimated_scale_and_a_day_keeps_it(tmp_path):
    day = DayStore(root=tmp_path)
    scale = Scale(day)
    assert scale.reading(325.0) is None  # no objective: no sizes at all
    scale.set_objective(40)
    r = scale.reading(325.0)  # an 18 mm field at 40x is 450 µm across, on a 650 px disc
    assert r.field_um == pytest.approx(450.0) and r.um_per_px == pytest.approx(450 / 650) and not r.measured
    assert "450 µm across at 40x (estimated" in r.words
    assert Scale(DayStore(root=tmp_path)).objective == 40  # kept until changed (day/prefs.py)
    with pytest.raises(ValueError):
        scale.set_objective(40, field_number=60)


def test_a_known_length_calibrates_this_objective(tmp_path):
    scale = Scale(DayStore(root=tmp_path))
    with pytest.raises(ValueError):
        scale.calibrate(100, 100, 325)  # no objective yet
    scale.set_objective(10)
    r = scale.calibrate(length_px=144.0, length_um=100.0, disc_radius_px=325.0)
    assert r.measured and r.field_um == pytest.approx(100 / 144 * 650)
    scale.set_objective(40)
    assert not scale.reading(325.0).measured  # another objective: an estimate again
    scale.set_objective(10)
    assert scale.reading(325.0).measured


def test_outline_sizes():
    square = [[[0, 0], [20, 0], [20, 10], [0, 10]]]
    size = sizes(square, Reading(0.5, 325.0, 40, False))
    assert size["lengthUm"] == pytest.approx(11.18, abs=0.1)  # the diagonal: the longest width
    assert size["widthUm"] == pytest.approx(5.0, abs=0.1)
    assert size["areaUm2"] == pytest.approx(50.0)
    assert size_words(size) == "about 11 × 5 µm (estimated from the objective)"
    assert round_um(452.0) == "450" and round_um(7.25) == "7.2" and round_um(12.6) == "13"


def test_the_tools(tmp_path):
    hub = Hub()
    hub.latest_info.fov = SimpleNamespace(cx=640, cy=360, radius=325.0)
    scale = Scale(DayStore(root=tmp_path))
    hub.scale = scale
    hub.scale_reading = lambda: scale.reading(325.0)
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[600, 340], [640, 340], [640, 380], [600, 380]]],
                              anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label="red blood cell"))
    registry = build(Services(hub=hub, scale=scale))
    ctx = ToolContext("chat", 1, 7, "how big is it?", None, [])
    run = lambda name, args: asyncio.run(registry.tools[name].handler(ctx, args))  # noqa: E731
    with pytest.raises(ToolFailed) as refused:
        run("measure", {"object": "o1"})
    assert "which objective" in str(refused.value)
    assert "40x" in run("set_objective", {"objective": 40})
    assert "about 39 × 28 µm (estimated" in run("measure", {"object": "o1"})  # a 40 px square at 0.69 µm/px
    assert "calibrated at 40x" in run("calibrate_scale", {"object": "o1", "micrometres": 8})
    assert "µm across" in run("measure", {"object": "o1"}) and "estimated" not in run("measure", {"object": "o1"})
    assert "forgot" in run("set_objective", {"objective": 0})


def test_a_measurement_is_logged_as_numbers_with_its_scale_and_frame(tmp_path):
    # Sizes, counts, motion and places are written as values with units, not only as the sentence the
    # tool says (datasets: docs/datasets.md); the scale says where it came from.
    hub = Hub()
    hub.latest_info.fov = SimpleNamespace(cx=640, cy=360, radius=325.0)
    hub.latest_info.seq, hub.latest_info.boot, hub.latest_info.wall_at = 1201, "boot1", 1_791_264_563.0
    scale = Scale(DayStore(root=tmp_path))
    hub.scale = scale
    hub.scale_reading = lambda: scale.reading(325.0)
    hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[600, 340], [640, 340], [640, 380], [600, 380]]],
                              anchor_frame=7, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai", label="red blood cell"))
    logged: list[dict] = []
    registry = build(Services(hub=hub, scale=scale, log=logged.append, magnifier=SimpleNamespace()))  # count is a magnifier tool
    ctx = ToolContext("voice", 3, 7, "how big is it?", None, [])
    asyncio.run(registry.tools["set_objective"].handler(ctx, {"objective": 40}))
    asyncio.run(registry.tools["measure"].handler(ctx, {"object": "o1"}))
    size = [r for r in logged if r.get("kind") == "measurement"][-1]
    assert size["what"] == "size" and size["objectId"] == "o1" and size["lengthUm"] == pytest.approx(39.2, abs=0.5)
    assert size["scale"]["source"] == "estimate" and size["scale"]["fieldNumberSource"] == "default"
    assert size["measuredOn"]["seq"] == 1201 and size["measuredOn"]["boot"] == "boot1" and size["channel"] == "voice"
    assert scale.facts()["objective"]["via"] == "voice"  # who set the objective is kept too
    asyncio.run(registry.tools["count"].handler(ctx, {"label": "red blood cell"}))
    count = [r for r in logged if r.get("kind") == "measurement"][-1]
    assert count["what"] == "count" and count["n"] == 1 and count["objectIds"] == ["o1"] and not count["justOutlined"]
    asyncio.run(registry.tools["where_is"].handler(ctx, {"object": "o1"}))
    where = [r for r in logged if r.get("kind") == "measurement"][-1]
    assert where["what"] == "place" and where["places"][0]["objectId"] == "o1" and where["places"][0]["known"]
    assert where["places"][0]["inView"] and where["places"][0]["um"] is not None
