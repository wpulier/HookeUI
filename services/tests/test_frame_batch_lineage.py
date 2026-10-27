"""A rejected JPEG must not shift another frame's measurements onto its bytes."""
import asyncio
import json
import struct

import cv2
import numpy as np
import pytest

from orchestrator.frames import Frame
from orchestrator.hub import Client, FRAME_MAGIC, Hub


@pytest.mark.parametrize("order,expected", [
    ((11, 13), (11, 13)),
    ((12, 11, 13), (11, 13)),
    ((11, 12, 13), (11, 13)),
    ((11, 13, 12), (11, 13)),
    ((12,), ()),
], ids=["valid-control", "invalid-first", "invalid-middle", "invalid-last", "all-invalid"])
def test_each_published_frame_keeps_its_own_jpeg_measurements_and_bridge_identity(order, expected):
    # Returning a compact list of successful analyses and zipping it with the
    # original batch sends the invalid bytes as the following good frame.
    first = np.full((240, 320), 30, np.uint8)
    cv2.circle(first, (160, 120), 90, 180, -1)
    cv2.rectangle(first, (125, 95), (175, 135), 70, -1)
    second = np.roll(first, 4, axis=1)
    jpeg_a = cv2.imencode(".jpg", first)[1].tobytes()
    jpeg_b = cv2.imencode(".jpg", second)[1].tobytes()
    frames = {
        11: Frame(11, jpeg_a, 1.1, 1011.0, seq=111, boot="bridge-a"),
        12: Frame(12, b"\xff\xd8not-a-jpeg\xff\xd9", 1.2, 1012.0, seq=112, boot="bridge-a"),
        13: Frame(13, jpeg_b, 1.3, 1013.0, seq=113, boot="bridge-b"),
    }

    async def receive():
        hub = Hub("http://127.0.0.1:1", use_gpu=False)
        client = Client(None)
        hub.clients.add(client)
        try:
            batch = [frames[i] for i in order]
            hub.source.ring.update((frame.id, frame) for frame in batch)
            # A backlog already awaiting the one analysis worker, then one more
            # callback: exercise the real analysis and publication boundaries.
            hub._pending_frames.extend(batch[:-1])
            await hub._on_frame(batch[-1])
            delivered = []
            while not client.frames.empty():
                packet = client.frames.get_nowait()
                assert packet[:4] == FRAME_MAGIC
                length = struct.unpack(">I", packet[4:8])[0]
                header = json.loads(packet[8:8 + length])
                jpeg = packet[8 + length:]
                delivered.append(header["id"])
                original = frames[header["id"]]
                assert jpeg == original.jpeg
                assert cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_GRAYSCALE) is not None
                assert (header["w"], header["h"], header["t"]) == (320, 240, original.wall_at)
                info = hub.infos[header["id"]]
                assert (info.id, info.seq, info.boot, info.wall_at) == (
                    original.id, original.seq, original.boot, original.wall_at)
            assert tuple(delivered) == expected
            assert tuple(hub.infos) == expected
            assert not hub._analysis_busy and not hub._pending_frames
        finally:
            hub._analysis.shutdown(wait=True)
            hub._background.shutdown(wait=True)

    asyncio.run(receive())


@pytest.mark.parametrize("consumer", ["slide", "object-slide"])
def test_tracking_benches_consume_the_analysed_frame_pair(tmp_path, monkeypatch, consumer):
    """Exercise both real bench consumers on two synthetic frames, without GPU or scoring claims."""
    from bench import object_tracking, tracking

    image = np.full((1600, 1600, 3), 180, np.uint8)
    cv2.rectangle(image, (700, 700), (850, 850), (60, 60, 60), -1)
    path = tmp_path / "synthetic-slide.png"
    assert cv2.imwrite(str(path), image)
    monkeypatch.setenv("MICROMIND_HOME", str(tmp_path / "home"))
    monkeypatch.setitem(tracking.PICTURES, "textured", path)
    # At 30fps this processes two frames, below the follower's one-second
    # start. The object benchmark uses TRACK slide and never calls EdgeTAM.
    if consumer == "slide":
        result = tracking.run("textured", seconds=2 / 30, seed=7)
        assert result["condition"] == "textured" and result["frames"] == 2
    else:
        result = asyncio.run(object_tracking.run("fixed", "slide", seconds=2 / 30, seed=7))
        assert result["condition"] == "fixed" and result["track"] == "slide"
        assert result["frames"] == 0  # analysed two, scored none: this is a caller regression only
