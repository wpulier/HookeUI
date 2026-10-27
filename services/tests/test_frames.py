from orchestrator.frames import extract_jpeg_frames, extract_jpeg_parts


def part(payload: bytes) -> bytes:
    jpeg = b"\xff\xd8" + payload + b"\xff\xd9"
    return b"--iphonebridge\r\nContent-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(jpeg) + jpeg + b"\r\n"


def test_frames_split_across_chunks_are_reassembled():
    stream = part(b"one") + part(b"two") + part(b"three")
    buffer = bytearray()
    found = []
    for i in range(0, len(stream), 7):  # arbitrary network chunking
        buffer.extend(stream[i : i + 7])
        found.extend(extract_jpeg_frames(buffer))
    assert found == [b"\xff\xd8one\xff\xd9", b"\xff\xd8two\xff\xd9", b"\xff\xd8three\xff\xd9"]


def test_incomplete_frame_waits_for_the_rest():
    buffer = bytearray(part(b"whole") + part(b"half")[:-8])
    assert extract_jpeg_frames(buffer) == [b"\xff\xd8whole\xff\xd9"]
    buffer.extend(part(b"half")[-8:])
    assert extract_jpeg_frames(buffer) == [b"\xff\xd8half\xff\xd9"]


def numbered(payload: bytes, seq: int) -> bytes:
    jpeg = b"\xff\xd8" + payload + b"\xff\xd9"
    return (b"--iphonebridge\r\nContent-Type: image/jpeg\r\nX-Frame-Seq: %d\r\nContent-Length: %d\r\n\r\n" % (seq, len(jpeg))
            + jpeg + b"\r\n")


def test_each_frame_keeps_the_bridges_number_for_it():
    # The bridge's X-Frame-Seq joins a frame to its line in a recording's sidecar (docs/datasets.md).
    stream = numbered(b"one", 41) + numbered(b"two", 42) + part(b"unnumbered")
    buffer, found = bytearray(), []
    for i in range(0, len(stream), 5):
        buffer.extend(stream[i : i + 5])
        found.extend(extract_jpeg_parts(buffer))
    assert found == [(41, b"\xff\xd8one\xff\xd9"), (42, b"\xff\xd8two\xff\xd9"), (None, b"\xff\xd8unnumbered\xff\xd9")]


def make_source():
    from orchestrator.frames import FrameSource

    return FrameSource("http://127.0.0.1:1", on_frame=lambda frame: None)


def test_fresh_frames_win_over_a_failed_health_call():
    import time

    source = make_source()
    source.last_frame_at = time.monotonic()
    source.ring[1] = object()
    source.health_failures = 5
    source.bridge_error = "ReadTimeout: "
    assert source.state()[0] == "live"


def test_bridge_offline_only_after_consecutive_failures():
    source = make_source()
    source.bridge_health = {"ok": False, "state": "requesting"}
    source.health_failures = 1
    source.bridge_error = "ReadTimeout: "
    assert source.state()[0] == "bridge_not_live"  # one slow answer is not an offline bridge
    source.health_failures = 2
    assert source.state()[0] != "bridge_offline"
    source.health_failures = 3
    assert source.state()[0] == "bridge_offline"


def test_health_passes_on_the_bridge_error_and_ffmpeg_lines():
    source = make_source()
    source.bridge_health = {"ok": False, "lastError": "FFmpeg exited with code 1: boom", "ffmpegStderr": ["boom"]}
    bridge = source.health()["bridge"]
    assert bridge["lastError"] == "FFmpeg exited with code 1: boom"
    assert bridge["ffmpegStderr"] == ["boom"]


def test_the_analysis_always_takes_the_newest_frame_and_never_falls_behind():
    """The audit of 2026-10-12: frames were analysed in order, one after another, so a busy app fell further and further
    behind the camera. Now the reader keeps only the newest frame for the analysis; the rest are skipped and counted."""
    import asyncio

    from orchestrator.frames import FrameSource

    async def run() -> tuple[list[int | None], int]:
        seen: list[int | None] = []

        async def slow(frame) -> None:
            seen.append(frame.seq)
            await asyncio.sleep(0.05)  # an analysis slower than the camera

        source = FrameSource("http://127.0.0.1:9", slow)
        worker = asyncio.create_task(source._process_loop())
        for seq in range(1, 31):  # a second of camera at 30 fps, arriving faster than the analysis
            source._offer(b"jpeg", seq, "boot")
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        worker.cancel()
        return seen, source.skipped

    seen, skipped = asyncio.run(run())
    assert seen[-1] == 30  # the newest is always what is analysed last
    assert seen == sorted(seen) and len(seen) < 30 and skipped == 30 - len(seen)


def test_the_bridge_time_of_each_frame_is_read_from_its_part():
    """X-Frame-At: when the bridge took the frame, so the true delay includes the time between the bridge and the app."""
    from orchestrator.frames import extract_jpeg_parts_at

    jpeg = b"\xff\xd8abc\xff\xd9"
    buffer = bytearray(b"--iphonebridge\r\nContent-Type: image/jpeg\r\nX-Frame-Seq: 7\r\nX-Frame-At: 1791790000.1234\r\n\r\n"
                       + jpeg + b"\r\n--iphonebridge\r\nX-Frame-Seq: 8\r\n\r\n" + jpeg + b"\r\n--iphonebridge\r\n")
    assert extract_jpeg_parts_at(buffer) == [(7, 1791790000.1234, jpeg), (8, None, jpeg)]
    assert bytes(buffer) == b"\r\n--iphonebridge\r\n"
