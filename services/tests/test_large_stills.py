"""A large still is outlined at its working size, never refused for its size (live, 30 Oct: "Outline every cell"
refused a 12 MP photo and asked the user to wait, which never helps).

The bridge and model process are stand-ins; the app, library, capture store, memory allowances, runner, measurements
and evidence saving are real. No camera or account is needed.
"""
import asyncio
import hashlib
import time

import cv2
import numpy as np
import pytest

from orchestrator import commands, population, source_images
from orchestrator.capture import MB, Memory, Refused, captures
from orchestrator.commands import handle
from orchestrator.library import Library
from tests.standin_workers import StandInWorker, build_app
from tests.test_sources import Client
from tests.waiting import until_async


class DarkCells(StandInWorker):
    """A cell finder stand-in: every dark blob of the picture it is sent is one label, on that picture's own grid."""

    def __init__(self):
        super().__init__()
        self.inputs = []

    async def call(self, op, **payload):
        from orchestrator.evidence import encode_labels

        image = cv2.imdecode(np.frombuffer(payload["jpeg"], np.uint8), cv2.IMREAD_UNCHANGED)
        self.inputs.append(image.shape)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        _, labels = cv2.connectedComponents((gray < 128).astype(np.uint8))
        return {"labels": encode_labels(labels), "count": int(labels.max())}


def still(path, width, height, cells, radius, colour=False, params=()):
    """A pale picture with a dark disc of `radius` at each of `cells` (x, y: pixel centres of the encoded grid)."""
    picture = np.full((height, width, 3) if colour else (height, width), 220, np.uint8)
    for x, y in cells:
        cv2.circle(picture, (x, y), radius, (40, 40, 40) if colour else 40, -1)
    assert cv2.imwrite(str(path), picture, list(params))
    return path


def opened(tmp_path, monkeypatch, path):
    built = build_app(tmp_path / "home")
    worker = DarkCells()
    built.hub.gpu_heavy = worker
    library = Library(tmp_path / "library.jsonl")
    built.hub.library = library
    aid = library.register("imported:example", path, title="A large still", at=time.time(), origin=None)

    async def bridge(app, method, path):
        return {"ok": True}

    monkeypatch.setattr(commands, "_bridge", bridge)
    return built, worker, aid


def test_the_working_scale_is_the_smallest_whole_number_that_fits_a_camera_frames_pixels():
    assert source_images.WORKING_PIXELS == 1920 * 1080
    assert source_images.working_scale(1920, 1080) == 1  # a camera frame's size is analysed as it is
    assert source_images.working_scale(96, 96) == 1
    assert source_images.working_scale(1921, 1080) == 2  # 961 x 540
    assert source_images.working_scale(2048, 1536) == 2  # 3.1 MP: 1024 x 768
    assert source_images.working_scale(4032, 3024) == 3  # a 12 MP photo: 1344 x 1008 (2016 x 1512 is 3.0 MP)
    assert source_images.working_scale(4000, 6000) == 4  # 24 MP: 1000 x 1500


def test_a_still_above_the_working_size_is_outlined_on_an_exact_block_average_and_kept_on_its_original_grid(
        tmp_path, monkeypatch):
    # A small working size keeps this fast; the 12 MP case below runs at the app's own size and allowances.
    monkeypatch.setattr(source_images, "WORKING_PIXELS", 128 * 96)
    cells, radius = [(60, 50), (200, 140), (320, 230), (95, 230)], 24
    path = still(tmp_path / "large.png", 384, 288, cells, radius)
    built, worker, aid = opened(tmp_path, monkeypatch, path)

    async def go():
        client = Client()
        await handle(built, client, {"type": "source", "requestId": "open", "open": aid})
        assert client.replies[-1]["ok"]
        result = await population.segment(built.hub, choice="cellpose-sam")
        masks = {m.id: (m.mask.scale, m.mask.x0, m.mask.y0) for m in result.live()}  # before saving lets them go
        await until_async(lambda: result.status == "saved")
        return result, masks

    result, masks = asyncio.run(go())
    # The model was shown the working copy: every 3 x 3 block of the original averaged, as one pixel.
    assert worker.inputs == [(96, 128)]
    # The population is the original's: its view, its source and its measurements are in the original's pixels.
    assert (result.view["width"], result.view["height"]) == (384, 288)
    source = result.view["sourceImage"]
    assert (source["width"], source["height"]) == (384, 288) and source["asset"] == aid
    assert source["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert population.drawn_on_stage(built.hub, result)  # the Stage's still: its fit maps these pixels as before
    members = sorted(result.live(), key=lambda m: result.columns["measure"]["values"][m.id]["centroid"][0])
    assert len(members) == len(cells)
    for member, (x, y) in zip(members, sorted(cells)):
        measured = result.columns["measure"]["values"][member.id]
        cx, cy = measured["centroid"]  # a pixel's centre is its index + 0.5 here (measure.py)
        assert abs(cx - (x + 0.5)) < 1.0 and abs(cy - (y + 0.5)) < 1.0
        assert measured["areaPx"] == pytest.approx(np.pi * radius ** 2, rel=0.08)
        assert measured["intensity"] < 60  # measured on the original's own grey pixels under the mask
        # Each mask pixel is exactly a 3 x 3 block of the original, so the outline maps back with no rounding.
        scale, x0, y0 = masks[member.id]
        assert scale == 3 and x0 % 3 == 0 and y0 % 3 == 0
        xs, ys = zip(*member.polygons[0])
        assert x - radius - 3 <= min(xs) and max(xs) <= x + radius + 3
        assert y - radius - 3 <= min(ys) and max(ys) <= y + radius + 3
    # What was done to the picture is said with the result, never left as if the original was segmented.
    working = result.producer["workingInput"]
    assert working["scale"] == 3 and working["size"] == [128, 96] and working["of"] == [384, 288]
    assert working["method"].startswith("area average") and len(working["pixelSha256"]) == 64
    assert "working size" in result.transformations["input"] and "128 x 96" in result.transformations["input"]
    assert "3 x 3" in result.transformations["masks"]


def test_a_still_at_or_under_the_working_size_is_still_analysed_without_resampling(tmp_path, monkeypatch):
    path = still(tmp_path / "small.png", 160, 120, [(50, 60), (110, 60)], 12)
    built, worker, aid = opened(tmp_path, monkeypatch, path)

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        return await population.segment(built.hub, choice="cellpose-sam")

    result = asyncio.run(go())
    assert worker.inputs == [(120, 160)]
    assert "workingInput" not in result.producer
    assert result.transformations["input"] == "the original imported image, decoded without resizing"
    assert all(m.mask.scale == 1 for m in result.live())


def test_a_12_megapixel_photo_is_outlined_within_the_apps_own_allowances_and_lets_them_go_once_saved(
        tmp_path, monkeypatch):
    cells = [(600 + 700 * i, 500 + 650 * j) for i in range(5) for j in range(4)]
    path = still(tmp_path / "photo.jpg", 4032, 3024, cells, 90, colour=True,
                 params=(cv2.IMWRITE_JPEG_QUALITY, 92))
    built, worker, aid = opened(tmp_path, monkeypatch, path)
    memory = captures(built.hub).memory
    assert memory.allowances == Memory.from_env().allowances  # the app's own bound, not one made for this test

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        result = await population.segment(built.hub, choice="cellpose-sam")
        await until_async(lambda: result.status == "saved")
        return result

    result = asyncio.run(go())
    assert worker.inputs == [(1008, 1344, 3)]
    assert (result.view["width"], result.view["height"]) == (4032, 3024) and len(result.live()) == len(cells)
    assert not any(memory.refused.values()), memory.last_refusal
    assert all(memory.peak[c] <= memory.allowances[c] for c in memory.allowances)
    # Saved: its pixels left memory and their room with them; only its table stays resident (evidence._keep_summary).
    assert memory.reserved["unsaved"] == result.summary_hold.nbytes < 1 * MB


def test_work_larger_than_its_whole_allowance_says_so_and_never_asks_to_wait():
    memory = Memory({"interactive": 10, "analysis": 20, "unsaved": 5})
    with pytest.raises(Refused) as alone:
        memory.reserve("unsaved", 6 * MB, "evidence being made")
    said = str(alone.value)
    assert "6 MB" in said and "5 MB" in said and "fill" not in said
    assert alone.value.next is None or "wait" not in alone.value.next
    # Room others hold does come back, so a full allowance still says waiting helps.
    held = memory.reserve("unsaved", 3 * MB, "an analysis being saved")
    with pytest.raises(Refused) as full:
        memory.reserve("unsaved", 3 * MB, "evidence being made")
    assert "fill their allowance" in str(full.value) and "wait" in full.value.next
    held.release()
    memory.reserve("unsaved", 3 * MB, "evidence being made").release()


def test_runs_on_the_same_large_still_at_once_share_its_working_copy_and_let_it_go_together(tmp_path, monkeypatch):
    monkeypatch.setattr(source_images, "WORKING_PIXELS", 64 * 48)
    path = still(tmp_path / "large.png", 192, 144, [(60, 50), (130, 90)], 14)
    built, worker, aid = opened(tmp_path, monkeypatch, path)
    store = captures(built.hub)

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        original = await source_images.for_analysis(built.hub)
        first = await asyncio.to_thread(source_images.working_copy, store, original)
        second = await asyncio.to_thread(source_images.working_copy, store, original)
        assert first is second and first.capture.id != original.id and first.size == (64, 48)
        made = first.capture.id
        del first, second
        await asyncio.sleep(0)  # the loop lets go of the thread's finished future once this coroutine yields
        import gc

        gc.collect()
        assert store.working.get(original.id) is None and store.get(made) is None  # nothing kept past its last run
        return await population.segment(built.hub, capture=original, choice="cellpose-sam")

    result = asyncio.run(go())
    assert worker.inputs == [(48, 64)] and len(result.live()) == 2
