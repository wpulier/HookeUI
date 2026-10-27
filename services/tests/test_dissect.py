import multiprocessing as mp
import threading
import time
from types import SimpleNamespace

import numpy as np

from gpu_worker import regions as R
from orchestrator.dissect import _apply_merge_drop
from orchestrator.hub import parts_follow
from orchestrator.scene import Scene, SceneObject
from tests.waiting import HANG_S


def disk(shape, cx, cy, r):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return (xx - cx) ** 2 + (yy - cy) ** 2 <= r * r


def test_refine_keeps_real_parts_and_drops_noise():
    obj = disk((100, 100), 50, 50, 40)
    pore = disk((100, 100), 50, 50, 10)
    rim = obj & ~disk((100, 100), 50, 50, 30)
    whole = obj.copy()  # the whole object is not a part (over 60%)
    speck = disk((100, 100), 80, 50, 1)  # under 1%
    outside = disk((100, 100), 5, 5, 4)  # clipped away
    candidates = [(pore, 0.5), (pore.copy(), 0.4), (rim, 0.3), (whole, 0.9), (speck, 0.8), (outside, 0.9), (pore, 0.1)]
    kept = R.refine(candidates, obj)
    assert [round(k["score"], 1) for k in kept] == [0.5, 0.3]  # duplicate pore and low score gone


def test_grid_points_stay_inside_the_object():
    obj = disk((60, 60), 30, 30, 20)
    pts = R.grid_points(obj, 8)
    assert 30 < len(pts) <= 64 and all(obj[int(y), int(x)] for x, y in pts)


def test_merge_and_drop_keep_numbering_stable():
    regions = [{"polygons": [[[i, 0]]], "score": 1} for i in range(4)]
    out = _apply_merge_drop(regions, merge=[[2, 3]], drop=[4])
    assert out[0]["polygons"] == [[[0, 0]]]
    assert out[1]["polygons"] == [[[1, 0]], [[2, 0]]] and out[2] is None and out[3] is None


def test_parts_follow_their_object():
    scene = Scene()
    parent = scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[100, 100], [120, 100], [120, 120]]],
                                   anchor_frame=1, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai"))
    scene.add(SceneObject(id="p1", kind="part", polygons=[[[105, 105], [110, 105], [110, 110]]],
                          anchor_frame=1, anchor_epoch=0, anchor_offset=(0.0, 0.0), source="dissect", parent="o1"))
    info = SimpleNamespace(id=9, dx=10.0, dy=0.0, epoch=1)
    # the parent re-found 3 px right of where motion tracking put it
    change = {"polygons": [[[x + 13, y] for x, y in parent.polygons[0]]], "anchor_frame": 9, "anchor_epoch": 1,
              "anchor_offset": (10.0, 0.0), "track": "tracking", "lost_since": None}
    (pid, part_change), = parts_follow(scene, [("o1", change)], info)
    assert pid == "p1" and part_change["polygons"][0][0] == [118, 105] and part_change["anchor_epoch"] == 1
    lost = parts_follow(scene, [("o1", {"track": "refinding", "lost_since": 5.0})], info)
    assert lost == [("p1", {"track": "refinding", "lost_since": 5.0})]


def test_removing_an_object_removes_its_parts():
    scene = Scene()
    scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[0, 0], [1, 0], [1, 1]]], anchor_frame=1,
                          anchor_epoch=0, anchor_offset=(0.0, 0.0), source="ai"))
    scene.add(SceneObject(id="p1", kind="part", polygons=[[[0, 0], [1, 0], [1, 1]]], anchor_frame=1,
                          anchor_epoch=0, anchor_offset=(0.0, 0.0), source="dissect", parent="o1"))
    gone = []
    scene.on_remove.append(gone.append)
    scene.remove("o1")
    assert not scene.objects and gone == ["o1", "p1"]


class FakeModels:
    """Stands in for the GPU models: a long regions op that yields between chunks."""

    log: list = []

    def __init__(self):
        self.device = "fake"

    def segment(self, req):
        FakeModels.log.append(("segment", round(time.monotonic(), 2)))
        return {"polygons": [], "score": 0.0, "area": 0, "ms": 1}

    def regions(self, req, between_chunks=lambda: None):
        for _ in range(6):
            time.sleep(0.05)  # a chunk of prompts
            FakeModels.log.append(("chunk", round(time.monotonic(), 2)))
            between_chunks()
        return {"regions": [], "crop": None, "ms": 300}


def test_clicks_overtake_a_running_dissect(monkeypatch):
    from gpu_worker import worker

    FakeModels.log = []
    monkeypatch.setattr(worker, "Models", FakeModels)
    parent, child = mp.Pipe()
    thread = threading.Thread(target=worker.serve, args=(child,), daemon=True)
    thread.start()
    parent.send({"id": 1, "op": "regions", "jpeg": b"", "polygons": []})
    time.sleep(0.08)  # the dissect is running
    parent.send({"id": 2, "op": "segment"})
    replies = []
    while len(replies) < 2:
        reply = parent.recv()
        if not reply.get("started"):  # the worker also says when it starts each call (the client's hang check)
            replies.append(reply)
    parent.send(None)
    thread.join(HANG_S)
    assert [r["id"] for r in replies] == [2, 1]  # the click answered before the dissect finished
    kinds = [k for k, _ in FakeModels.log]
    assert kinds.index("segment") < len(kinds) - 1  # served between chunks, not after
