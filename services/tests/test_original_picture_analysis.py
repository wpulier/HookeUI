"""Ordinary source command → production population/worker boundary on original still pixels.

The bridge and model process are stand-ins; the app, library, journals, capture,
runner, measurements and evidence persistence are real. No camera/account needed.
"""
import asyncio
import hashlib
import json
import time

import cv2
import numpy as np
import pytest

from orchestrator import commands, population
from orchestrator.commands import handle
from orchestrator.fiji_bindings import fixture
from orchestrator.library import Library
from orchestrator.capture import captures
from tests.standin_workers import StandInWorker, build_app
from tests.test_sources import Client
from tests.waiting import HANG_S, until_async


class PixelWorker(StandInWorker):
    def __init__(self):
        super().__init__()
        self.inputs = []

    async def call(self, op, **payload):
        from orchestrator.evidence import encode_labels

        image = cv2.imdecode(np.frombuffer(payload["jpeg"], np.uint8), cv2.IMREAD_UNCHANGED)
        self.inputs.append(image)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
        _, labels = cv2.connectedComponents((gray == 40).astype(np.uint8))
        return {"labels": encode_labels(labels), "count": 3}


def prepare(tmp_path, monkeypatch):
    built = build_app(tmp_path / "home")
    worker = PixelWorker()
    built.hub.gpu_heavy = worker
    library = Library(tmp_path / "library.jsonl")
    built.hub.library = library
    image = fixture(tmp_path)
    aid = library.register("imported:example", image, title="Three particles", at=time.time(), origin=None)

    async def bridge(app, method, path):
        return {"ok": True}

    monkeypatch.setattr(commands, "_bridge", bridge)
    return built, worker, aid, image


def test_ordinary_picture_source_reaches_analysis_without_camera_or_resampling(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        client = Client()
        await handle(built, client, {"type": "source", "requestId": "open", "open": aid})
        assert client.replies[-1]["ok"]
        result = await population.segment(built.hub, choice="cellpose-sam")
        return result

    result = asyncio.run(go())
    assert built.day.on_stage == aid and aid in built.day.sources
    assert (result.view["width"], result.view["height"]) == (96, 96)
    assert worker.inputs[0].shape == (96, 96) and worker.inputs[0].dtype == np.uint8
    assert hashlib.sha256(worker.inputs[0].tobytes()).hexdigest() == "1343019a13d406a783dd80da0933f798c78b86d34886331e693ff92407767506"
    assert result.view["sourceImage"]["asset"] == aid
    assert result.view["sourceImage"]["sha256"] == hashlib.sha256(image.read_bytes()).hexdigest()
    assert "original image" in result.count_words() and "eyepiece" not in result.count_words()
    assert result.scale_id["units"] == "pixels" and not result.scale_id.get("umPerPx")
    assert result.view["epoch"] is None and result.view["tracked"] is False


def test_saved_evidence_reopens_original_pixels_instead_of_its_jpeg_preview(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        result = await population.segment(built.hub, choice="cellpose-sam")
        await until_async(lambda: result.status == "saved")
        assert result.status == "saved" and result.capture is None
        return result

    result = asyncio.run(go())
    original = image.read_bytes()
    image.unlink()  # evidence is independent of the library file after saving
    reopened = population.capture_of(built.hub, result)
    assert reopened.analysis_bytes == original
    assert reopened.source_image["asset"] == aid
    with captures(built.hub).hold(reopened, "analysis", "hold", "reading retained input"):
        assert hashlib.sha256(reopened.gray().tobytes()).hexdigest() == "1343019a13d406a783dd80da0933f798c78b86d34886331e693ff92407767506"
    assert len(worker.inputs) == 1  # reopening did not compute again


def test_held_preview_keeps_its_original_source_when_stage_changes(tmp_path, monkeypatch):
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        preview = cv2.imencode(".jpg", np.full((180, 320), 200, np.uint8))[1].tobytes()
        await built.hub.source._accept(preview)
        capture = captures(built.hub).take(built.hub.latest_info.id)
        await handle(built, Client(), {"type": "source", "requestId": "live", "open": "live"})
        result = await population.segment(built.hub, capture=capture, choice="cellpose-sam")
        layer = population.layer_of(built.hub)
        layer.show(result)
        assert layer.drawn_frame_reason() is not None
        assert layer.verified_count() == (0, 3)
        return result

    result = asyncio.run(go())
    assert result.view["sourceImage"]["asset"] == aid
    assert worker.inputs[0].shape == (96, 96)
    assert result.view["epoch"] is None


def test_preview_coordinates_cannot_be_used_as_original_image_coordinates(tmp_path, monkeypatch):
    import pytest
    from orchestrator.brain.registry import ToolFailed

    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        with pytest.raises(ToolFailed, match="original image") as failure:
            await population.segment(built.hub, choice="cellpose-sam", region=(20, 10, 80, 60))
        assert "whole" in failure.value.next
        assert worker.inputs == []

    asyncio.run(go())


def test_imported_jpeg_keeps_encoded_grid_and_its_known_physical_scale(tmp_path, monkeypatch):
    built, worker, _, image = prepare(tmp_path, monkeypatch)
    photo = tmp_path / "photo.jpg"
    cv2.imwrite(str(photo), cv2.imread(str(image), cv2.IMREAD_GRAYSCALE))
    aid = built.hub.library.register("imported:photo", photo, title="Photo", at=time.time(), origin=None,
                                     optics={"umPerPx": 0.5})

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        return await population.segment(built.hub, choice="cellpose-sam")

    result = asyncio.run(go())
    assert result.view["sourceImage"]["mediaType"] == "image/jpeg"
    assert result.view["sourceImage"]["sha256"] == hashlib.sha256(photo.read_bytes()).hexdigest()
    assert worker.inputs[0].shape == (96, 96)
    assert result.scale_id["umPerPx"] == 0.5
    assert result.scale_id["units"] == result.view["sourceImage"]["units"] == "µm"
    assert result.view["sourceImage"]["umPerPx"] == 0.5


@pytest.mark.parametrize("switch", ["source", "session"])
def test_result_arriving_after_switch_keeps_evidence_but_does_not_show(tmp_path, monkeypatch, switch):
    from types import SimpleNamespace
    from orchestrator import ticket, tools

    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        started, release = asyncio.Event(), asyncio.Event()
        run = worker.call

        async def delayed(op, **payload):
            started.set()
            await release.wait()
            return await run(op, **payload)

        worker.call = delayed  # process boundary holds the actual run until the user's source change
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        sid = built.day.sid
        request = ticket.issue(built.hub, "command", asked=SimpleNamespace(session=sid),
                               choices={"cells": {"implementation": "cellpose-sam"}})
        with ticket.using(request):
            pending = asyncio.create_task(tools.outline_cells(built.hub))
        await asyncio.wait_for(started.wait(), HANG_S)
        await handle(built, Client(), {"type": "source", "requestId": "live", "open": "live"})
        if switch == "session":
            await handle(built, Client(), {"type": "session", "requestId": "new", "action": "new", "name": "Another session"})
        release.set()
        result, shown = await pending
        request.close()
        assert result.session == sid and result.view["sourceImage"]["session"] == sid
        assert result.view["sourceImage"]["asset"] == aid
        assert not shown
        assert population.layer_of(built.hub).shown is None

    asyncio.run(go())


@pytest.mark.parametrize("format", ["uint16", "alpha", "tiff", "corrupt", "jpeg-markers", "missing"])
def test_unsupported_original_refuses_before_worker_even_with_a_valid_preview(tmp_path, monkeypatch, format):
    from orchestrator.brain.registry import ToolFailed

    built, worker, aid, image = prepare(tmp_path, monkeypatch)
    if format == "uint16":
        cv2.imwrite(str(image), np.full((96, 96), 1000, np.uint16))
    elif format == "alpha":
        cv2.imwrite(str(image), np.zeros((96, 96, 4), np.uint8))
    elif format == "tiff":
        image.write_bytes(cv2.imencode(".tiff", np.full((96, 96), 40, np.uint8))[1].tobytes())
    elif format == "jpeg-markers":
        image.write_bytes(b"\xff\xd8" + b"\xff" * 6)
    elif format == "corrupt":
        image.write_bytes(image.read_bytes()[:35])

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        await built.hub.source._accept(cv2.imencode(".jpg", np.full((180, 320), 200, np.uint8))[1].tobytes())
        if format == "missing":
            image.unlink()
        with pytest.raises(ToolFailed, match="original picture") as failure:
            await population.segment(built.hub, choice="cellpose-sam")
        assert "keeping the original" in failure.value.next
        assert worker.inputs == []

    asyncio.run(go())


def test_corrupt_retained_original_never_falls_back_to_saved_preview(tmp_path, monkeypatch):
    from orchestrator.capture import CaptureGone
    built, worker, aid, image = prepare(tmp_path, monkeypatch)

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        result = await population.segment(built.hub, choice="cellpose-sam")
        await until_async(lambda: result.status == "saved")
        assert result.status == "saved"
        return result

    result = asyncio.run(go())
    (result.path / "source.image").write_bytes(b"corrupt")
    with pytest.raises(CaptureGone, match="original"):
        population.capture_of(built.hub, result)


def test_jpeg_orientation_metadata_cannot_rotate_original_analysis_grid(tmp_path, monkeypatch):
    import struct
    built, worker, _, _ = prepare(tmp_path, monkeypatch)
    pixels = np.full((64, 96, 3), 220, np.uint8)
    pixels[5:25, 15:35] = 40
    encoded = cv2.imencode(".jpg", pixels)[1].tobytes()
    exif = b"Exif\0\0II" + struct.pack("<HIH", 42, 8, 1) + struct.pack("<HHII", 274, 3, 1, 6) + b"\0" * 4
    encoded = encoded[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + encoded[2:]
    photo = tmp_path / "rotated.jpg"
    photo.write_bytes(encoded)
    aid = built.hub.library.register("imported:oriented-photo", photo, title="Photo", at=time.time(), origin=None)
    seen = []
    run = worker.call

    async def normal_decoder(op, **payload):
        # Existing model decoders use IMREAD_COLOR, which applies JPEG EXIF rotation.
        seen.append(cv2.imdecode(np.frombuffer(payload["jpeg"], np.uint8), cv2.IMREAD_COLOR).shape)
        return await run(op, **payload)

    worker.call = normal_decoder

    async def go():
        await handle(built, Client(), {"type": "source", "requestId": "open", "open": aid})
        result = await population.segment(built.hub, choice="cellpose-sam")
        return result

    result = asyncio.run(go())
    assert seen == [(64, 96, 3)]
    assert (result.view["height"], result.view["width"]) == (64, 96)
