"""Contract 1 (docs/design/2026-10-06-captures-operations-evidence.md; ADR 0010): captures, the
views work is about, held by their owners inside a memory bound enforced before allocation."""
import asyncio
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.capture import MB, Capture, Captures, CaptureGone, Memory, Refused, footprint
from orchestrator.scene import Scene
from orchestrator.ticket import issue


def frames_hub(n=3, size=(720, 1280)):
    """A hub with a frame ring the tests can empty, as the 4 s ring does."""
    h, w = size
    rng = np.random.default_rng(1)
    jpegs = {i: cv2.imencode(".jpg", rng.integers(0, 255, (h, w), dtype=np.uint8))[1].tobytes() for i in range(1, n + 1)}
    infos = {i: SimpleNamespace(id=i, width=w, height=h, epoch=0, dx=float(i), dy=0.0, fov=None, wall_at=100.0 + i, disc=0.0)
             for i in range(1, n + 1)}
    ring = dict(jpegs)
    hub = SimpleNamespace(scene=Scene(), infos=infos, latest_info=infos[n],
                          source=SimpleNamespace(get=lambda fid: SimpleNamespace(jpeg=ring[fid]) if fid in ring else None,
                                                 is_fresh=lambda: True))
    hub.ring = ring
    return hub


def test_a_capture_is_one_per_frame_and_stands_in_for_its_facts():
    hub = frames_hub()
    store = Captures(hub)
    a, b = store.take(2), store.get(2)
    assert a is b  # one capture per frame id
    assert a.id == 2 and a.width == 1280 and a.dx == 2.0 and a.epoch == 0  # every FrameInfo field
    hub.infos[2].dx = 99.0  # later changes to the hub's facts never reach a capture
    assert a.dx == 2.0
    with pytest.raises(AttributeError):
        a.jpeg = b""  # the view as it was


def test_a_decode_is_kept_only_while_a_hold_reserves_room_for_it():
    """Invariant 3: resident decodes are counted. A decode no hold covers is made for its caller and
    never kept (and counted as uncounted); a held one is kept until the last hold covering it goes
    (scaffolding review, M1.1 item 3)."""
    from orchestrator.capture import UNCOUNTED

    hub = frames_hub()
    store = Captures(hub)
    a = store.take(2)
    before = sum(UNCOUNTED.values())
    assert a.gray().shape == (720, 1280) and a.resident == len(a.jpeg)  # not kept
    assert sum(UNCOUNTED.values()) == before + 1  # and counted where it was asked for
    hold = store.hold(a, "analysis", "hold", "test")  # room for the gray decode, not the colour one
    a.gray(), a.bgr()
    assert a._gray is not None and a._bgr is None
    look = store.hold(a, "analysis", "look", "test")
    a.bgr()
    assert a._bgr is not None and a.resident <= store.memory.reserved["analysis"]
    look.release()
    assert a._bgr is None and a._gray is not None  # the colour decode went with the hold that covered it
    hold.release()
    assert a._gray is None and a.resident == len(a.jpeg)


def test_a_second_capture_of_one_frame_is_refused():
    hub = frames_hub()
    store = Captures(hub)
    first = store.take(2)
    with pytest.raises(ValueError):
        store.hold(Capture(2, hub.ring[2], hub.infos[2]), "analysis", "hold", "test")
    assert store.hold(first, "analysis", "hold", "test").capture is first


def test_a_held_capture_outlives_the_ring_and_goes_when_let_go():
    hub = frames_hub()
    store = Captures(hub, Memory({"interactive": 64, "analysis": 64, "unsaved": 8}))
    hold = store.hold(store.take(1), "analysis", "outline", "test")
    hub.ring.clear()  # the 4 s ring has moved on
    hub.infos.clear()
    assert store.get(1) is hold.capture and store.take(1).jpeg  # still found, bytes and facts
    assert store.memory.reserved["analysis"] == hold.capture.footprint("outline")
    hold.release()
    del hold  # the holder let go, and nothing else refers to the view
    import gc

    gc.collect()
    assert store.get(1) is None and store.memory.reserved["analysis"] == 0
    with pytest.raises(CaptureGone):
        store.take(1)


def test_the_held_list_is_read_whole_while_other_threads_let_go():
    """Live 2026-10-10 00:26: /health raised "dictionary changed size during iteration" reading the held views while a
    worker thread let one go. The store's own lock covers reading them too."""
    import threading

    hub = frames_hub(n=40, size=(72, 128))
    store = Captures(hub, Memory({"interactive": 64, "analysis": 64, "unsaved": 8}))
    stop, errors = threading.Event(), []

    def churn():
        while not stop.is_set():
            holds = [store.hold(store.take(i), "analysis", "outline", "test") for i in range(1, 41)]
            for hold in holds:
                hold.release()

    def read():
        try:
            for _ in range(3000):
                store.wire()
                store.held()
        except RuntimeError as exc:
            errors.append(exc)

    workers = [threading.Thread(target=churn) for _ in range(2)]
    for w in workers:
        w.start()
    try:
        read()
    finally:
        stop.set()
        for w in workers:
            w.join()
    assert not errors


def test_memory_is_reserved_before_allocation_and_refused_with_the_reason():
    memory = Memory({"interactive": 10, "analysis": 20, "unsaved": 5})
    first = memory.reserve("analysis", 15 * MB, "a cell run")
    with pytest.raises(Refused) as no:
        memory.reserve("analysis", 6 * MB, "a second cell run")
    assert "held views are full" in str(no.value) and memory.refused["analysis"] == 1
    with pytest.raises(Refused):
        first.grow(6 * MB, "a label map")  # more is asked for before it is allocated
    first.grow(4 * MB, "a label map")
    assert memory.reserved["analysis"] == 19 * MB and memory.peak["analysis"] == 19 * MB
    first.settle(3 * MB)  # what it really keeps
    assert memory.reserved["analysis"] == 3 * MB
    first.release()
    assert memory.reserved["analysis"] == 0
    with pytest.raises(Refused) as clicks:
        memory.reserve("interactive", 11 * MB, "a click")
    assert "too many outlines" in str(clicks.value)
    with pytest.raises(Refused) as unsaved:
        memory.reserve("unsaved", 6 * MB, "evidence")
    assert "not yet saved" in str(unsaved.value)


def test_the_worst_case_footprint_counts_decodes_label_maps_and_copies():
    jpeg = 300_000
    w, h = 1920, 1080
    assert footprint(w, h, jpeg, "hold") == int((jpeg + w * h) * 1.25)
    assert footprint(w, h, jpeg, "cells") == int((jpeg + w * h + 3 * w * h + 8 * w * h) * 1.25)
    assert footprint(w, h, jpeg, "cells") > footprint(w, h, jpeg, "outline") > footprint(w, h, jpeg, "hold")


def test_a_tickets_views_are_held_until_its_last_work_is_done():
    async def go():
        hub = frames_hub()
        hub.captures = Captures(hub, Memory({"interactive": 64, "analysis": 64, "unsaved": 8}))
        request = issue(hub, "turn")
        request.hold(hub.captures.take(2), "analysis")
        done = asyncio.Event()
        request.track(asyncio.ensure_future(done.wait()))
        request.seal()  # no more work, but one piece is still running
        hub.ring.clear()
        held_while_running = hub.captures.get(2) is not None
        done.set()
        await asyncio.sleep(0.01)
        return hub, request, held_while_running

    hub, request, held_while_running = asyncio.run(go())
    assert held_while_running and request.closed
    assert hub.captures.memory.reserved["analysis"] == 0


def test_five_analyses_at_once_stay_inside_the_allowance_and_are_refused_honestly():
    """Five cell runs started together on a 1280x720 view: each reserves its worst case; those that
    do not fit are refused with the reason, nothing is held past the run, and the bound holds."""
    async def go():
        hub = frames_hub(n=5)
        need = Capture(1, hub.ring[1], hub.infos[1]).footprint("cells")
        hub.captures = Captures(hub, Memory({"interactive": 8, "analysis": need * 2.5 / MB, "unsaved": 8}))
        peak = []

        async def run(i):
            hold = hub.captures.hold(hub.captures.take(i), "analysis", "cells", f"run {i}")
            peak.append(hub.captures.memory.reserved["analysis"])
            await asyncio.sleep(0.02)
            hold.release()
            return "ran"

        results = await asyncio.gather(*(run(i) for i in range(1, 6)), return_exceptions=True)
        return hub, results, peak

    hub, results, peak = asyncio.run(go())
    memory = hub.captures.memory
    ran = [r for r in results if r == "ran"]
    refused = [r for r in results if isinstance(r, Refused)]
    assert len(ran) == 2 and len(refused) == 3 and all("held views are full" in str(r) for r in refused)
    assert max(peak) <= memory.allowances["analysis"] and memory.reserved["analysis"] == 0


def test_a_frames_scale_is_decided_once_and_the_oldest_frames_are_forgotten_first(monkeypatch):
    """#186 re-check note 4: a frame's scale is the one in force when it was first asked about (a held clip keeping
    it, or its first capture), for every later reader; only frames long gone from the ring are forgotten, oldest
    first, and a frame asked about only after a change gets the new scale."""
    from types import SimpleNamespace

    from orchestrator import capture
    from orchestrator.frames import FrameSource

    objective = {"value": 10}
    hub = SimpleNamespace(scale=SimpleNamespace(facts=lambda: {"objective": {"value": objective["value"], "source": "user"}},
                                                calibrations={}, reading=lambda disc: None))
    capture.captures(hub)
    infos = [SimpleNamespace(id=i, width=8, height=8, disc=0.0, fov=None) for i in range(1, 7)]
    monkeypatch.setattr(capture, "SCALED_FRAMES", 3)
    assert [capture.scale_at(hub, info)["objective"] for info in infos[:5]] == [10] * 5  # 3, 4 and 5 remembered
    objective["value"] = 40
    assert [capture.scale_at(hub, infos[k])["objective"] for k in (4, 3, 2, 1, 0, 5)] == [10, 10, 10, 40, 40, 40]
    # Remembered far longer than the camera ring keeps a frame (4 s), at 60 frames a second.
    import inspect

    ring_s = inspect.signature(FrameSource.__init__).parameters["ring_seconds"].default
    monkeypatch.undo()
    assert capture.SCALED_FRAMES >= 4 * ring_s * 60
