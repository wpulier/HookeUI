"""Research admission keeps its context across delayed real snapshot writes.

Synthetic camera pixels and worker transport; real manager, capture decoding,
snapshot files, session persistence and worker prompt. No provider calls.
"""
import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from orchestrator.capture import captures
from orchestrator.research import ResearchManager
from orchestrator.scene import SceneObject
from research_worker.researcher import Researcher
from tests.fakes import Hub
from tests.waiting import HANG_S, until_async


@pytest.mark.parametrize("evict_original", [False, True])
def test_start_keeps_original_context_and_picture_when_snapshot_io_is_delayed(tmp_path, monkeypatch, evict_original):
    dispatched = []

    class WorkerTransport:
        def __init__(self, _target, _name, event, request, exited):
            self.alive = False

        async def start(self):
            self.alive = True

        async def call(self, op, **payload):
            if op == "spawn":
                dispatched.append(payload)
            return {"ok": True}

        def stop(self):
            self.alive = False

    monkeypatch.setattr("orchestrator.research.manager.ProcessChannel", WorkerTransport)
    entered, release = threading.Event(), threading.Event()
    mkdir = Path.mkdir

    def delayed_mkdir(path, *args, **kwargs):
        if path.name == "inputs" and threading.current_thread() is not threading.main_thread():
            entered.set()
            assert release.wait(HANG_S), "test must release snapshot I/O"
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", delayed_mkdir)
    hub = Hub(specimen="original leaf", research="fake")
    hub.settings.level, hub.settings.hints_first = "simple", False
    old_info = hub.latest_info
    old_info.wall_at, old_info.steady, old_info.focus = 100, True, 1
    new_info = SimpleNamespace(**(vars(old_info) | {"id": 8}))
    hub.infos = {7: old_info, 8: new_info}
    images = {}
    for fid, color in ((7, (0, 0, 220)), (8, (220, 0, 0))):
        ok, jpeg = cv2.imencode(".jpg", np.full((48, 64, 3), color, dtype=np.uint8))
        assert ok
        images[fid] = SimpleNamespace(jpeg=jpeg.tobytes())
    hub.source.get = images.get
    obj = hub.scene.add(SceneObject(id="o1", kind="highlight", polygons=[[[10, 10], [30, 10], [30, 30], [10, 30]]],
        anchor_frame=7, anchor_epoch=0, anchor_offset=(0, 0), source="ai", label="original structure",
        confidence=.7, note="original outline", seen="original observation"))

    async def scenario():
        manager = ResearchManager(hub, root=tmp_path)
        task = asyncio.create_task(manager.start("explain this structure", object_id="o1", fake={"seconds": 0}))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S), "snapshot I/O must begin"
            hub.scene.context["specimen"] = "new aquatic mount"
            hub.settings.level, hub.settings.hints_first = "technical", True
            hub.latest_info = new_info
            obj.label, obj.confidence, obj.seen = "new structure", .9, "new observation"
            obj.anchor_frame, obj.anchor_offset = 8, (20, 0)
            obj.polygons[0][0][0] = 45
            if evict_original:
                del images[7]
                del hub.infos[7]
            release.set()
            session = await asyncio.wait_for(task, 5)
            await until_async(lambda: dispatched)
            assert len(dispatched) == 1
            return session
        finally:
            release.set()
            manager.stop()
            await asyncio.gather(task, return_exceptions=True)

    session = asyncio.run(scenario())
    snapshot = dispatched[0]["snapshot"]
    saved = json.loads((tmp_path / session.day / session.rid / "session.json").read_text())
    assert saved["specimen"] == snapshot["specimen"] == "original leaf"
    assert "simple, in everyday words" in snapshot["learner"]
    assert "Hints first" not in snapshot["learner"]
    assert snapshot["frame_id"] == 7
    assert saved["label"] == snapshot["object"]["label"] == "original structure"
    assert snapshot["object"]["confidence"] == .7
    assert snapshot["object"]["note"] == "original observation"
    photo = cv2.imread(str(tmp_path / session.day / session.rid / snapshot["frame"]))
    assert photo is not None and photo[:, :, 2].mean() > 200 and photo[:, :, 0].mean() < 10
    crop = cv2.imread(str(tmp_path / session.day / session.rid / snapshot["crop"]))
    assert crop is not None and crop[:, :, 2].mean() > 200 and crop[:, :, 0].mean() < 10
    assert crop.shape[:2] == (44, 52)  # original box plus existing 30% pad and 4:3 crop, clipped at the frame
    prompt = Researcher(dispatched[0], lambda _event: None, None).system_prompt()
    assert "original leaf" in prompt and "new aquatic mount" not in prompt
    assert "simple, in everyday words" in prompt and "Hints first" not in prompt
    assert not captures(hub).held()
    assert not any(captures(hub).memory.reserved.values())


@pytest.mark.parametrize("ending", ["cancelled", "write-failed"])
def test_interrupted_snapshot_releases_capture_only_after_io_exits(tmp_path, monkeypatch, ending):
    hub = Hub(specimen="original leaf", research="fake")
    ok, jpeg = cv2.imencode(".jpg", np.full((48, 64, 3), 100, dtype=np.uint8))
    assert ok
    store = captures(hub)
    original = store.adopt(hub.latest_info, jpeg.tobytes())
    entered, release = threading.Event(), threading.Event()
    mkdir = Path.mkdir

    def delayed_mkdir(path, *args, **kwargs):
        if path.name == "inputs" and threading.current_thread() is not threading.main_thread():
            entered.set()
            assert release.wait(HANG_S)
            if ending == "write-failed":
                raise OSError("synthetic snapshot disk failure")
        return mkdir(path, *args, **kwargs)

    monkeypatch.setattr(Path, "mkdir", delayed_mkdir)

    async def scenario():
        manager = ResearchManager(hub, root=tmp_path)
        task = asyncio.create_task(manager.start("explain this structure", fake={"seconds": 0}))
        try:
            assert await asyncio.to_thread(entered.wait, HANG_S)
            assert store.held() == [original]
            if ending == "cancelled":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert store.held() == [original], "executor still needs the counted camera bytes"
                release.set()
            else:
                release.set()
                with pytest.raises(OSError, match="snapshot disk failure"):
                    await task
            await until_async(lambda: not store.held())
            assert not store.held() and not any(store.memory.reserved.values())
            assert not manager.researchers() and manager.spending.committed() == 0
            assert not list(tmp_path.glob("*/r*/session.json")), "failed admission must not claim a saved researcher"
        finally:
            release.set()
            manager.stop()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
