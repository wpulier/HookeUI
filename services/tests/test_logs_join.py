"""Log lines can be joined back to the pictures the models saw (W0-2): naming keeps its view
and close-up, a suggested correction keeps its crop, and export_corrections turns them into
labelled pictures."""
import json

from bench.export_corrections import rows
from orchestrator.agent import Agent
from tests.fakes import Hub


def test_keep_image_writes_beside_the_logs(tmp_path):
    agent = Agent(Hub(), interaction_log=tmp_path / "logs" / "interactions.jsonl")
    path = agent.keep_image("sight", b"jpeg-bytes", 42, "o3")
    assert path.startswith("crops/") and path.endswith("-sight-f42-o3.jpg")
    assert (tmp_path / "logs" / path).read_bytes() == b"jpeg-bytes"


def test_keep_image_never_fails_a_request(tmp_path):
    blocker = tmp_path / "logs"
    blocker.write_text("a file where the folder should be")
    agent = Agent(Hub(), interaction_log=blocker / "interactions.jsonl")
    assert agent.keep_image("sight", b"x", 1) is None


def test_export_finds_accepted_corrections_with_their_crop(tmp_path):
    research = tmp_path / "research"
    folder = research / "2026-10-04" / "r1"
    (folder / "corrections").mkdir(parents=True)
    (folder / "corrections" / "r1-c1.jpg").write_bytes(b"jpg")
    (folder / "session.json").write_text(json.dumps({"rid": "r1", "corrections": [
        {"id": "r1-c1", "label": "stoma", "to_label": "trichome base", "status": "accepted", "from": "Researcher 1",
         "crop": "2026-10-04/r1/corrections/r1-c1.jpg"},
        {"id": "r1-c2", "label": "cell", "to_label": "guard cell", "status": "proposed", "from": "Researcher 1"},
    ]}))
    found = rows(research)
    assert [r["id"] for r in found] == ["r1-c1"]
    assert found[0]["day"] == "2026-10-04" and found[0]["to"] == "trichome base" and found[0]["crop"].endswith("r1-c1.jpg")
    assert len(rows(research, include_open=True)) == 2


def test_the_studios_frame_timing_is_logged_clean():
    import asyncio

    from orchestrator.commands import COMMANDS
    from types import SimpleNamespace

    logged = []
    app = SimpleNamespace(agent=SimpleNamespace(log=logged.append))
    reply = asyncio.run(COMMANDS["timing"](app, None, {"frames": 30, "stages": {
        "total": {"p50": 41, "p90": 66, "junk": "x"}, "deliver": {"p50": "fast"}}}))
    assert reply == {"ok": True}
    assert logged == [{"kind": "frame-timing", "frames": 30, "stages": {"total": {"p50": 41, "p90": 66}, "deliver": {}}}]


def test_a_flagged_moment_is_logged_with_what_was_on_screen():
    import asyncio
    from types import SimpleNamespace

    from orchestrator.commands import COMMANDS
    from tests.fakes import Hub
    from tests.test_brain import obj

    hub = Hub()
    obj(hub, "stoma")
    logged = []
    app = SimpleNamespace(agent=SimpleNamespace(log=logged.append), hub=hub, magnifier=SimpleNamespace(open=True),
                          presenter=SimpleNamespace(open=False))
    assert asyncio.run(COMMANDS["flag"](app, None, {"note": "named it wrong", "frameId": 5})) == {"ok": True}
    assert logged == [{"kind": "flag", "note": "named it wrong", "frameId": 5, "latestFrame": 7, "outlines": ["stoma"],
                       "magnifier": True, "presenter": False}]


def _click_app(hub, logged, outline):
    from types import SimpleNamespace

    from orchestrator import tools

    calls = []

    async def outline_at(hub_, frame_id, x, y, *, box=None, **_):
        calls.append({"frameId": frame_id, "x": x, "y": y, "box": box})
        return outline()

    app = SimpleNamespace(agent=SimpleNamespace(log=logged.append, available=lambda: False), hub=hub,
                          arena=SimpleNamespace(enabled=False))
    return app, calls, outline_at, tools


def test_a_click_is_logged_with_the_outline_it_made(monkeypatch):
    import asyncio

    from orchestrator.commands import COMMANDS
    from tests.fakes import Hub
    from tests.test_brain import obj

    hub, logged = Hub(), []
    app, calls, outline_at, tools = _click_app(hub, logged, lambda: obj(hub, ""))
    monkeypatch.setattr(tools, "outline_at", outline_at)
    reply = asyncio.run(COMMANDS["click"](app, None, {"frameId": 5, "x": 110.04, "y": 104.96}))
    assert reply == {"ok": True, "objectId": "o1"}
    assert calls == [{"frameId": 5, "x": 110.04, "y": 104.96, "box": None}]
    [record] = logged
    assert record.pop("ms") >= 0
    assert record == {"kind": "click", "frameId": 5, "latestFrame": 7, "x": 110.0, "y": 105.0, "box": None,
                      "outline": "edgetam", "ok": True, "objectId": "o1", "areaPx": 200,
                      "bbox": [100.0, 100.0, 120.0, 120.0]}


def test_a_dragged_box_outlines_inside_it_and_a_failed_click_is_logged_too(monkeypatch):
    import asyncio

    import pytest

    from orchestrator.commands import COMMANDS
    from tests.fakes import Hub

    def nothing():
        raise tools.ToolError("nothing distinct to outline there")

    hub, logged = Hub(), []
    app, calls, outline_at, tools = _click_app(hub, logged, nothing)
    monkeypatch.setattr(tools, "outline_at", outline_at)
    with pytest.raises(tools.ToolError):  # the studio is told why (commands.handle)
        asyncio.run(COMMANDS["click"](app, None, {"frameId": 5, "box": [140, 160, 100, 120]}))
    # Dragged up and to the left: the same box, outlined around its centre on the frame the user saw.
    assert calls == [{"frameId": 5, "x": 120.0, "y": 140.0, "box": (100.0, 120.0, 140.0, 160.0)}]
    [record] = logged
    assert record.pop("ms") >= 0
    assert record == {"kind": "click", "frameId": 5, "latestFrame": 7, "x": 120.0, "y": 140.0,
                      "box": [100.0, 120.0, 140.0, 160.0], "outline": "edgetam", "ok": False,
                      "error": "nothing distinct to outline there"}
    with pytest.raises(tools.ToolError, match="too small"):
        asyncio.run(COMMANDS["click"](app, None, {"frameId": 5, "box": [100, 100, 101, 140]}))
    assert logged[-1]["ok"] is False and "too small" in logged[-1]["error"] and len(calls) == 1
