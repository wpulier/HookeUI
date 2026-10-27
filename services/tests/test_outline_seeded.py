import asyncio

import pytest

from orchestrator import tools

SQUARE = [[100.0, 200.0], [140.0, 200.0], [140.0, 230.0], [100.0, 230.0]]


@pytest.fixture(autouse=True)
def scripted_sam_install(monkeypatch):
    from orchestrator.operations import INSTALLED

    monkeypatch.setitem(INSTALLED, "sam3", lambda: "")  # FakeGpu supplies the SAM implementation in these cases.


class FakeGpu:
    ready = True

    def __init__(self, polygons):
        self.polygons = polygons
        self.calls = []

    async def call(self, op, timeout=30.0, **payload):
        self.calls.append(payload)
        return {"polygons": self.polygons, "score": 0.9, "ms": 20.0}


class FakeHub:
    def __init__(self, polygons):
        self.gpu = FakeGpu(polygons)


def run(coro):
    return asyncio.run(coro)


def test_seeded_sam31_prompts_with_edgetams_padded_box(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")
    seen = {}

    async def fake_fal(hub, frame_id, jpeg, x, y, box, label):
        seen["box"] = box
        return {"polygons": [SQUARE], "score": 0.96, "ms": 1500.0}

    monkeypatch.setattr(tools, "_segment_fal", fake_fal)
    hub = FakeHub([SQUARE])
    result = run(tools.segment(hub, "edgetam>sam3.1-fal", 1, b"", 120, 215))
    assert hub.gpu.calls[0]["model"] == "edgetam" and "points" in hub.gpu.calls[0]
    # 40 x 30 object, padded by 10% (min 4 px) on each side
    assert seen["box"] == pytest.approx((96.0, 196.0, 144.0, 234.0))
    assert result["ms"] == pytest.approx(1520.0)
    assert "SAM 3.1 (fal)" in result["note"]


def test_seeded_sam31_falls_back_to_edgetam_when_it_finds_nothing(monkeypatch):
    monkeypatch.setenv("FAL_KEY", "test")

    async def empty_fal(*args):
        return {"polygons": [], "score": 0.0, "ms": 1400.0}

    monkeypatch.setattr(tools, "_segment_fal", empty_fal)
    result = run(tools.segment(FakeHub([SQUARE]), "edgetam>sam3.1-fal", 1, b"", 120, 215))
    assert result["polygons"] == [SQUARE]
    assert "found nothing" in result["note"]


def test_seeded_sam31_with_a_box_skips_the_seed():
    hub = FakeHub([SQUARE])
    run(tools.segment(hub, "edgetam>sam3.1-local", 1, b"", 0, 0, box=(10, 10, 50, 50)))
    assert len(hub.gpu.calls) == 1
    assert hub.gpu.calls[0]["model"] == "sam3.1-local" and hub.gpu.calls[0]["box"] == [10, 10, 50, 50]


def test_seeded_sam31_returns_empty_when_edgetam_finds_nothing():
    result = run(tools.segment(FakeHub([]), "edgetam>sam3.1-local", 1, b"", 120, 215))
    assert result["polygons"] == []
