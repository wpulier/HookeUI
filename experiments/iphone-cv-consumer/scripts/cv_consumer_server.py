#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import cv2
import numpy as np


ROOT_DIR = Path(__file__).resolve().parents[1]
INDEX_PATH = ROOT_DIR / "index.html"


def wall_timestamp(epoch_seconds: float) -> str | None:
    if not epoch_seconds:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(epoch_seconds))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Serve a separate live OpenCV consumer for the iPhone camera bridge."
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--bridge-url", default="http://127.0.0.1:8765")
    parser.add_argument("--stale-timeout", type=float, default=3.0)
    parser.add_argument("--bridge-health-interval", type=float, default=1.0)
    parser.add_argument("--mjpeg-reconnect-delay", type=float, default=1.0)
    parser.add_argument("--snapshot-fallback-interval", type=float, default=0.35)
    parser.add_argument("--jpeg-quality", type=int, default=86)
    return parser.parse_args()


def fetch_json(url: str, timeout: float = 2.0) -> dict[str, object]:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_bytes(url: str, timeout: float = 2.0) -> bytes:
    request = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class CVConsumer:
    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.bridge_url = args.bridge_url.rstrip("/")
        self.condition = threading.Condition()
        self.stop_event = threading.Event()
        self.capture_thread: threading.Thread | None = None
        self.health_thread: threading.Thread | None = None

        self.latest_raw_jpeg: bytes | None = None
        self.latest_processed_jpeg: bytes | None = None
        self.frame_count = 0
        self.processed_count = 0
        self.total_input_bytes = 0
        self.last_frame_at = 0.0
        self.last_frame_wall_at = 0.0
        self.started_at = time.monotonic()
        self.input_mode = "mjpeg"
        self.last_error = ""
        self.bridge_error = ""
        self.last_bridge_health: dict[str, object] | None = None
        self.mjpeg_connect_count = 0
        self.snapshot_fallback_count = 0
        self.width: int | None = None
        self.height: int | None = None
        self.brightness_pct: float | None = None
        self.contrast: float | None = None
        self.edge_pct: float | None = None
        self.frame_times: deque[float] = deque(maxlen=180)

    @property
    def health_url(self) -> str:
        return f"{self.bridge_url}/api/health"

    @property
    def mjpeg_url(self) -> str:
        return f"{self.bridge_url}/iphone.mjpeg"

    @property
    def snapshot_url(self) -> str:
        return f"{self.bridge_url}/snapshot.jpg"

    def start(self) -> None:
        self.capture_thread = threading.Thread(target=self._capture_loop, daemon=True)
        self.health_thread = threading.Thread(target=self._bridge_health_loop, daemon=True)
        self.capture_thread.start()
        self.health_thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        with self.condition:
            self.condition.notify_all()

    def health(self) -> dict[str, object]:
        with self.condition:
            now = time.monotonic()
            age = now - self.last_frame_at if self.last_frame_at else None
            fresh = age is not None and age < self.args.stale_timeout
            estimated_fps = self._estimated_fps_locked(now)

            if self.bridge_error:
                state = "bridge_offline"
                state_label = "Camera bridge is offline"
            elif fresh:
                state = "live"
                state_label = "Live frames are being decoded"
            elif self.last_bridge_health and not self.last_bridge_health.get("ok"):
                state = "bridge_not_live"
                state_label = "Camera bridge is reachable but not live"
            elif self.latest_processed_jpeg is None:
                state = "waiting"
                state_label = "Waiting for the first decoded frame"
            else:
                state = "stale"
                state_label = "No fresh CV frames are arriving"

            return {
                "ok": state == "live",
                "service": "iphone-cv-consumer",
                "state": state,
                "stateLabel": state_label,
                "bridge": {
                    "url": self.bridge_url,
                    "healthUrl": self.health_url,
                    "mjpegUrl": self.mjpeg_url,
                    "snapshotUrl": self.snapshot_url,
                    "ok": bool(self.last_bridge_health and self.last_bridge_health.get("ok")),
                    "state": self.last_bridge_health.get("state") if self.last_bridge_health else None,
                    "lastFrameAgeSeconds": self.last_bridge_health.get("lastFrameAgeSeconds")
                    if self.last_bridge_health
                    else None,
                    "error": self.bridge_error,
                },
                "inputMode": self.input_mode,
                "frameCount": self.frame_count,
                "processedCount": self.processed_count,
                "estimatedFps": round(estimated_fps, 2),
                "latestFrameAgeSeconds": round(age, 2) if age is not None else None,
                "latestFrameAt": wall_timestamp(self.last_frame_wall_at),
                "width": self.width,
                "height": self.height,
                "brightnessPct": round(self.brightness_pct, 1) if self.brightness_pct is not None else None,
                "contrast": round(self.contrast, 1) if self.contrast is not None else None,
                "edgePct": round(self.edge_pct, 2) if self.edge_pct is not None else None,
                "lastError": self.last_error,
                "mjpegConnectCount": self.mjpeg_connect_count,
                "snapshotFallbackCount": self.snapshot_fallback_count,
                "uptimeSeconds": round(now - self.started_at, 2),
                "settings": {
                    "staleTimeout": self.args.stale_timeout,
                    "jpegQuality": self.args.jpeg_quality,
                    "primaryInput": self.mjpeg_url,
                    "fallbackInput": self.snapshot_url,
                },
            }

    def latest_raw(self) -> bytes | None:
        with self.condition:
            if not self._latest_is_fresh_locked():
                return None
            return self.latest_raw_jpeg

    def latest_processed(self) -> bytes | None:
        with self.condition:
            if not self._latest_is_fresh_locked():
                return None
            return self.latest_processed_jpeg

    def wait_for_processed(self, last_seen_count: int, timeout: float = 4.0) -> tuple[int, bytes | None]:
        deadline = time.monotonic() + timeout
        with self.condition:
            while not self.stop_event.is_set():
                if self.processed_count > last_seen_count and self.latest_processed_jpeg:
                    return self.processed_count, self.latest_processed_jpeg
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self.processed_count, self.latest_processed_jpeg if self._latest_is_fresh_locked() else None
                self.condition.wait(timeout=remaining)
        return self.processed_count, None

    def _latest_is_fresh_locked(self) -> bool:
        return bool(self.last_frame_at and time.monotonic() - self.last_frame_at < self.args.stale_timeout)

    def _estimated_fps_locked(self, now: float) -> float:
        while self.frame_times and now - self.frame_times[0] > 4.0:
            self.frame_times.popleft()
        if len(self.frame_times) < 2:
            return 0.0
        elapsed = self.frame_times[-1] - self.frame_times[0]
        if elapsed <= 0:
            return 0.0
        return (len(self.frame_times) - 1) / elapsed

    def _bridge_health_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                health = fetch_json(self.health_url)
                with self.condition:
                    self.last_bridge_health = health
                    self.bridge_error = ""
                    self.condition.notify_all()
            except Exception as exc:
                with self.condition:
                    self.bridge_error = str(exc)
                    self.last_bridge_health = None
                    self.condition.notify_all()
            self.stop_event.wait(self.args.bridge_health_interval)

    def _capture_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self._read_mjpeg_forever()
            except Exception as exc:
                self._set_error(f"MJPEG read failed: {exc}")
                if self._try_snapshot_fallback():
                    self.stop_event.wait(self.args.snapshot_fallback_interval)
                else:
                    self.stop_event.wait(self.args.mjpeg_reconnect_delay)

    def _read_mjpeg_forever(self) -> None:
        request = urllib.request.Request(self.mjpeg_url, headers={"Cache-Control": "no-cache"})
        with self.condition:
            self.input_mode = "mjpeg"
            self.last_error = ""
            self.mjpeg_connect_count += 1
            self.condition.notify_all()

        with urllib.request.urlopen(request, timeout=8.0) as response:
            buffer = bytearray()
            while not self.stop_event.is_set():
                chunk = response.read(64 * 1024)
                if not chunk:
                    raise RuntimeError("MJPEG stream ended")
                buffer.extend(chunk)
                for frame in self._extract_jpeg_frames(buffer):
                    self._process_frame(frame, "mjpeg")

    def _try_snapshot_fallback(self) -> bool:
        try:
            frame = fetch_bytes(self.snapshot_url, timeout=2.0)
            if not frame.startswith(b"\xff\xd8"):
                raise RuntimeError("snapshot endpoint did not return JPEG data")
            self._process_frame(frame, "snapshot_fallback")
            with self.condition:
                self.snapshot_fallback_count += 1
                self.condition.notify_all()
            return True
        except Exception as exc:
            self._set_error(f"Snapshot fallback failed: {exc}")
            return False

    def _extract_jpeg_frames(self, buffer: bytearray) -> list[bytes]:
        frames: list[bytes] = []
        while True:
            start = buffer.find(b"\xff\xd8")
            if start < 0:
                if len(buffer) > 1024 * 1024:
                    del buffer[:-2]
                return frames
            end = buffer.find(b"\xff\xd9", start + 2)
            if end < 0:
                if start > 0:
                    del buffer[:start]
                return frames
            frame = bytes(buffer[start : end + 2])
            del buffer[: end + 2]
            frames.append(frame)

    def _process_frame(self, jpeg_bytes: bytes, input_mode: str) -> None:
        array = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        image = cv2.imdecode(array, cv2.IMREAD_COLOR)
        if image is None:
            self._set_error("OpenCV could not decode JPEG frame")
            return

        height, width = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 60, 140)
        processed = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
        processed[edges > 0] = (42, 235, 168)

        raw_ok, raw_encoded = cv2.imencode(
            ".jpg",
            image,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(self.args.jpeg_quality)],
        )
        processed_ok, processed_encoded = cv2.imencode(
            ".jpg",
            processed,
            [int(cv2.IMWRITE_JPEG_QUALITY), int(self.args.jpeg_quality)],
        )
        if not raw_ok or not processed_ok:
            self._set_error("OpenCV could not encode processed JPEG")
            return

        now = time.monotonic()
        with self.condition:
            self.latest_raw_jpeg = raw_encoded.tobytes()
            self.latest_processed_jpeg = processed_encoded.tobytes()
            self.frame_count += 1
            self.processed_count += 1
            self.total_input_bytes += len(jpeg_bytes)
            self.last_frame_at = now
            self.last_frame_wall_at = time.time()
            self.input_mode = input_mode
            self.last_error = ""
            self.width = int(width)
            self.height = int(height)
            self.brightness_pct = float(gray.mean() / 255.0 * 100.0)
            self.contrast = float(gray.std())
            self.edge_pct = float(np.count_nonzero(edges) / edges.size * 100.0)
            self.frame_times.append(now)
            self.condition.notify_all()

    def _set_error(self, message: str) -> None:
        with self.condition:
            self.last_error = message
            self.condition.notify_all()


class CVHandler(SimpleHTTPRequestHandler):
    consumer: CVConsumer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/":
            self._serve_file(INDEX_PATH, "text/html; charset=utf-8")
        elif path == "/api/health":
            self._serve_json(self.consumer.health())
        elif path == "/raw.jpg":
            self._serve_jpeg(self.consumer.latest_raw(), "No fresh raw frame")
        elif path == "/processed.jpg":
            self._serve_jpeg(self.consumer.latest_processed(), "No fresh processed frame")
        elif path == "/processed.mjpeg":
            self._serve_processed_mjpeg()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_file(self, path: Path, content_type: str) -> None:
        try:
            payload = path.read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "File not found")
            return
        self.send_response(HTTPStatus.OK)
        self._common_headers(content_type, len(payload), "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_json(self, data: dict[str, object]) -> None:
        payload = json.dumps(data, indent=2).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self._common_headers("application/json; charset=utf-8", len(payload), "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_jpeg(self, payload: bytes | None, empty_message: str) -> None:
        if not payload:
            body = empty_message.encode("utf-8")
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self._common_headers("text/plain; charset=utf-8", len(body), "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(HTTPStatus.OK)
        self._common_headers("image/jpeg", len(payload), "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def _serve_processed_mjpeg(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

        last_seen = 0
        while True:
            count, frame = self.consumer.wait_for_processed(last_seen, timeout=5.0)
            if not frame:
                continue
            last_seen = count
            part = (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame)}\r\n\r\n".encode("ascii")
                + frame
                + b"\r\n"
            )
            try:
                self.wfile.write(part)
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    def _common_headers(self, content_type: str, content_length: int, cache_control: str) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(content_length))
        self.send_header("Cache-Control", cache_control)
        self.send_header("Access-Control-Allow-Origin", "*")


def main() -> int:
    args = parse_args()
    consumer = CVConsumer(args)
    CVHandler.consumer = consumer
    server = ThreadingHTTPServer((args.host, args.port), CVHandler)
    server.daemon_threads = True

    consumer.start()
    print(f"CV consumer server: http://{args.host}:{args.port}", flush=True)
    print(f"Camera bridge dependency: {args.bridge_url.rstrip('/')}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        consumer.stop()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
