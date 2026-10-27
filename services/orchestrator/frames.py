"""Frame intake from the camera bridge.

Ported from experiments/iphone-cv-consumer/scripts/cv_consumer_server.py: the same MJPEG
SOI/EOI parsing, snapshot fallback and bridge-health states, rewritten for asyncio. The
bridge's original JPEG bytes are kept untouched for the browser; analysis decodes them once.

Freshness contract (unchanged from the CV consumer): a frame older than `stale_seconds`
(contract/overlay.json feedStaleS, 3 s) is never served or drawn on; the state tells the UI why.
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

from .contract import FEED_STALE_S

# No camera chosen (Will, 2026-10-15): said as the user's choice everywhere a model or the studio hears there is no
# picture, never as a camera to fix ("the agent would tell users their camera is broken").
NO_CAMERA_LABEL = "No camera in use: pictures and videos only"
NO_CAMERA_WHY = "no camera is in use (the user chose pictures and videos only)"
NO_CAMERA_NEXT = "open a picture or video, or choose a camera in the camera menu, then ask again"


def no_picture(hub, why: str, next: str | None) -> tuple[str, str | None]:
    """The words for there being no picture: the caller's own, or, with No camera chosen, that no camera is in use."""
    source = getattr(hub, "source", hub)
    check = getattr(source, "no_camera", None)
    return (NO_CAMERA_WHY, NO_CAMERA_NEXT) if callable(check) and check() else (why, next)

@dataclass
class Frame:
    id: int
    jpeg: bytes
    received_at: float  # time.monotonic()
    wall_at: float  # time.time()
    # The bridge's own number for this frame (X-Frame-Seq) and its run (X-Bridge-Boot): the same
    # pair a recording's sidecar gives the frame, so what the hub measured joins the video.
    seq: int | None = None
    boot: str = ""
    # When the bridge took it (X-Frame-At, wall seconds): the true delay is measured from here, so time spent between
    # the bridge and the app (where demo night's lag built up unseen) is in it.
    bridge_at: float | None = None


SEQ_HEADER = re.compile(rb"X-Frame-Seq: *(\d+)", re.IGNORECASE)
AT_HEADER = re.compile(rb"X-Frame-At: *([0-9.]+)", re.IGNORECASE)


def extract_jpeg_parts_at(buffer: bytearray) -> list[tuple[int | None, float | None, bytes]]:
    """Pull complete JPEGs out of a multipart MJPEG byte buffer (consumed in place), each with the bridge's frame
    number (X-Frame-Seq) and when the bridge took it (X-Frame-At) from its part header when it has them. Bytes before
    an incomplete JPEG are only that part's small header, so they stay in the buffer until the frame completes."""
    parts: list[tuple[int | None, float | None, bytes]] = []
    while True:
        start = buffer.find(b"\xff\xd8")
        if start < 0:
            if len(buffer) > 1024 * 1024:
                del buffer[:-2]
            return parts
        end = buffer.find(b"\xff\xd9", start + 2)
        if end < 0:
            if start > 4096:
                del buffer[: start - 4096]
            return parts
        header = bytes(buffer[max(0, start - 4096) : start])
        seq, at = SEQ_HEADER.findall(header), AT_HEADER.findall(header)
        parts.append((int(seq[-1]) if seq else None, float(at[-1]) if at else None, bytes(buffer[start : end + 2])))
        del buffer[: end + 2]


def extract_jpeg_parts(buffer: bytearray) -> list[tuple[int | None, bytes]]:
    """extract_jpeg_parts_at() without the bridge's times."""
    return [(seq, jpeg) for seq, _, jpeg in extract_jpeg_parts_at(buffer)]


def extract_jpeg_frames(buffer: bytearray) -> list[bytes]:
    """extract_jpeg_parts() without the frame numbers."""
    return [jpeg for _, jpeg in extract_jpeg_parts(buffer)]


class FrameSource:
    def __init__(
        self,
        bridge_url: str,
        on_frame: Callable[[Frame], Awaitable[None] | None],
        stale_seconds: float = FEED_STALE_S,
        ring_seconds: float = 4.0,
        health_interval: float = 1.0,
        reconnect_delay: float = 1.0,
        offline_after_failures: int = 3,
    ) -> None:
        self.bridge_url = bridge_url.rstrip("/")
        self.on_frame = on_frame
        self.stale_seconds = stale_seconds
        self.ring_seconds = ring_seconds
        self.health_interval = health_interval
        self.reconnect_delay = reconnect_delay
        # One slow health call (the bridge busy for a moment) is not an offline bridge.
        self.offline_after_failures = offline_after_failures
        self.health_failures = 0  # consecutive
        self.ring: OrderedDict[int, Frame] = OrderedDict()
        self.next_id = 1
        self.last_frame_at = 0.0
        self.bridge_health: dict | None = None
        self.bridge_error = ""
        self.last_error = ""
        self.connect_count = 0
        self.frame_times: list[float] = []
        self._tasks: list[asyncio.Task] = []
        # Newest only (the audit of 2026-10-12): the reader drains the bridge's stream continuously and keeps only the
        # newest complete frame; the analysis takes it when it is free. A frame a newer one replaced before the
        # analysis was free is skipped and counted, so delay can never build up in a socket or a queue.
        self._newest: tuple[bytes, int | None, str, float | None] | None = None
        self._newest_ready = asyncio.Event()
        self.skipped = 0

    # --- lifecycle -------------------------------------------------------------------
    def start(self) -> None:
        self._tasks = [asyncio.create_task(self._read_loop()), asyncio.create_task(self._process_loop()),
                       asyncio.create_task(self._health_loop())]

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # --- queries ---------------------------------------------------------------------
    def get(self, frame_id: int) -> Frame | None:
        return self.ring.get(frame_id)

    def latest(self) -> Frame | None:
        if not self.ring or not self.is_fresh():
            return None
        return next(reversed(self.ring.values()))

    def is_fresh(self) -> bool:
        return bool(self.last_frame_at and time.monotonic() - self.last_frame_at < self.stale_seconds)

    def bridge_offline(self) -> bool:
        return self.health_failures >= self.offline_after_failures

    def no_camera(self) -> bool:
        """The user chose No camera (pictures and videos only) and nothing plays: a choice, never a fault."""
        return not self.is_fresh() and not self.bridge_offline() and (self.bridge_health or {}).get("state") == "no_camera"

    def state(self) -> tuple[str, str]:
        # Fresh frames win: they prove the bridge is up whatever its health endpoint says.
        if self.is_fresh():
            return "live", "Live frames are arriving"
        if self.bridge_offline():
            return "bridge_offline", "Camera bridge is offline"
        if self.no_camera():
            return "no_camera", NO_CAMERA_LABEL
        if self.bridge_health and not self.bridge_health.get("ok"):
            return "bridge_not_live", "Camera bridge is reachable but not live"
        if not self.ring:
            return "waiting", "Waiting for the first frame"
        return "stale", "No fresh frames are arriving"

    def fps(self) -> float:
        now = time.monotonic()
        self.frame_times = [t for t in self.frame_times if now - t < 3.0]
        if len(self.frame_times) < 2:
            return 0.0
        return (len(self.frame_times) - 1) / (self.frame_times[-1] - self.frame_times[0])

    def health(self) -> dict:
        state, label = self.state()
        age = time.monotonic() - self.last_frame_at if self.last_frame_at else None
        return {
            "state": state,
            "stateLabel": label,
            "fps": round(self.fps(), 1),
            "latestFrameAgeSeconds": round(age, 2) if age is not None else None,
            "latestFrameId": self.next_id - 1,
            "bridge": {
                "url": self.bridge_url,
                "ok": bool(self.bridge_health and self.bridge_health.get("ok")),
                "state": (self.bridge_health or {}).get("state"),
                "targetDevice": (self.bridge_health or {}).get("targetDevice"),
                "devicePresent": (self.bridge_health or {}).get("devicePresent"),
                # Why the bridge has no picture (FFmpeg's last stderr line when it failed),
                # and FFmpeg's last stderr lines for the current capture.
                "lastError": (self.bridge_health or {}).get("lastError") or "",
                "ffmpegStderr": (self.bridge_health or {}).get("ffmpegStderr") or [],
                "error": self.bridge_error,
                "healthFailures": self.health_failures,
            },
            "connectCount": self.connect_count,
            # Frames read but not analysed because a newer one was already waiting (the app was busy): the price of
            # never falling behind.
            "skipped": self.skipped,
            "lastError": self.last_error,
        }

    # --- loops -----------------------------------------------------------------------
    async def _read_loop(self) -> None:
        # The bridge ends a stream after 10 s without frames; the read timeout is longer, so
        # that is the normal way an idle stream closes and is reopened.
        timeout = httpx.Timeout(8.0, read=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            while True:
                try:
                    self.connect_count += 1
                    async with client.stream("GET", f"{self.bridge_url}/iphone.mjpeg", headers={"Cache-Control": "no-cache"}) as response:
                        response.raise_for_status()
                        self.last_error = ""
                        boot = response.headers.get("X-Bridge-Boot", "")  # one bridge run per connection
                        buffer = bytearray()
                        # As the bytes arrive (a chunk size would hold a frame's tail back until the next frame's).
                        async for chunk in response.aiter_bytes():
                            buffer.extend(chunk)
                            for seq, at, jpeg in extract_jpeg_parts_at(buffer):
                                self._offer(jpeg, seq, boot, at)
                    self.last_error = "MJPEG stream ended"
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.last_error = f"MJPEG read failed: {type(exc).__name__}: {exc}"[:300]
                await asyncio.sleep(self.reconnect_delay)

    def _offer(self, jpeg: bytes, seq: int | None, boot: str, bridge_at: float | None = None) -> None:
        """The reader's newest frame, for the analysis when it is free (replacing one it hadn't taken yet)."""
        if self._newest is not None:
            self.skipped += 1
        self._newest = (jpeg, seq, boot, bridge_at)
        self._newest_ready.set()

    async def _process_loop(self) -> None:
        while True:
            await self._newest_ready.wait()
            self._newest_ready.clear()
            item, self._newest = self._newest, None
            if item is None:
                continue
            try:
                await self._accept(*item)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # one frame's failure never stops the feed
                self.last_error = f"frame handling failed: {type(exc).__name__}: {exc}"[:300]

    async def _accept(self, jpeg: bytes, seq: int | None = None, boot: str = "", bridge_at: float | None = None) -> None:
        now = time.monotonic()
        frame = Frame(self.next_id, jpeg, now, time.time(), seq, boot, bridge_at)
        self.next_id += 1
        self.ring[frame.id] = frame
        while self.ring and now - next(iter(self.ring.values())).received_at > self.ring_seconds:
            self.ring.popitem(last=False)
        self.last_frame_at = now
        self.frame_times.append(now)
        result = self.on_frame(frame)
        if asyncio.iscoroutine(result):
            await result

    async def _health_loop(self) -> None:
        async with httpx.AsyncClient(timeout=2.0) as client:
            while True:
                try:
                    response = await client.get(f"{self.bridge_url}/api/health", headers={"Cache-Control": "no-cache"})
                    response.raise_for_status()
                    self.bridge_health = response.json()
                    self.bridge_error = ""
                    self.health_failures = 0
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.health_failures += 1
                    self.bridge_error = f"{type(exc).__name__}: {exc}"[:200]
                    if self.bridge_offline():
                        self.bridge_health = None  # until then the last answer stands
                await asyncio.sleep(self.health_interval)
