"""A microscope that exists only in a test: a real picture as the slide, a stage and a focus that a simulated person's
hands (or, later, motors) move, seen through an eyepiece. Its view is an MJPEG stream that the camera bridge's simulated
camera plays as the camera (`POST /sim/stage`), so the orchestrator, the models and the studio see it as they would the
iPhone on the eyepiece and nothing downstream can tell (experiments/iphone-camera-feed/AGENTS.md, test footage).

- **The optics:** the stage's (x, y) is the slide point under the eyepiece's centre, in slide px; the picture shows the
  slide around it magnified `zoom` times, inside the eyepiece disc. Moving the slide right moves the picture left (a
  compound microscope turns the picture round); `turn` quarter turns of the phone on the eyepiece turn it further.
  `z` is the distance from sharp focus: the picture is blurred by `blur_per_z * |z|`.
- **Hands** (`hand`): a person's move, smooth at hand speed; a later move adds to it. **Motors** (the OpenFlexure-like
  API below): a move the stage makes itself, at its own speed, reported as an action that runs then completes.
- **Truth** for the harness only (`truth`, `GET /truth`, `where`): where a slide point is in the picture, how far the
  focus is from sharp. Nothing else of the app ever reads it.
- **OpenFlexure's HTTP API, the part a client needs** (openflexure-microscope-server v2): `GET
  /api/v2/instrument/state/stage/position`, `POST /api/v2/actions/stage/move` ({x, y, z, absolute}, in steps), `GET
  /api/v2/actions/<id>`, `GET /api/v2/streams/mjpeg`, `GET /api/v2/streams/snapshot`, `POST
  /api/v2/actions/camera/ram-capture` (a picture taken now), and `POST
  /api/v2/extensions/org.openflexure.autofocus/fast_autofocus`. One step is one slide px across and 1/STEPS_PER_Z of z.

Standard library and the services environment (numpy, OpenCV); one render thread, one thread per connection.
"""
from __future__ import annotations

import itertools
import json
import math
import threading
import time
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import cv2
import numpy as np

STEPS_PER_Z = 100  # OpenFlexure's z steps per unit of z here
MOTOR_SPEED = 1200.0  # slide px a second, the motors' own pace
MOTOR_Z_SPEED = 4.0  # z units a second
HAND_SPEED = 250.0  # slide px a second, a person's hand on the stage knobs


@dataclass
class Move:
    start: float
    end: float
    dx: float
    dy: float
    dz: float


def slide_from_frame(frame: np.ndarray, centre: tuple[float, float], radius: float, pad: int = 2) -> tuple[np.ndarray, tuple[float, float]]:
    """A slide made of an eyepiece frame's own picture: the square inside its disc, mirrored around it `pad` times on
    each side so the stage has room to move. Returns the slide and where the frame's (0, 0) is on it."""
    half = int(radius / math.sqrt(2.0)) - 4
    cx, cy = int(round(centre[0])), int(round(centre[1]))
    square = frame[cy - half:cy + half, cx - half:cx + half]
    side = square.shape[0]
    slide = cv2.copyMakeBorder(square, pad * side, pad * side, pad * side, pad * side, cv2.BORDER_REFLECT)
    return slide, (pad * side - (cx - half), pad * side - (cy - half))


class VirtualScope:
    def __init__(self, slide: np.ndarray, *, size: tuple[int, int] = (1280, 720), disc: tuple[float, float, float] | None = None,
                 zoom: float = 1.6, start: tuple[float, float] | None = None, z: float = 0.0, blur_per_z: float = 1.6,
                 turn: int = 0, fps: float = 20.0, jpeg_quality: int = 85, noise: float = 2.0, seed: int = 7,
                 limits: tuple[float, float, float, float] | None = None, motor_speed: float = MOTOR_SPEED,
                 motor_z_speed: float = MOTOR_Z_SPEED) -> None:
        self.motor_speed, self.motor_z_speed = motor_speed, motor_z_speed  # the motors' own pace (OpenFlexure API)
        self.slide = slide if slide.ndim == 3 else cv2.cvtColor(slide, cv2.COLOR_GRAY2BGR)
        self.width, self.height = size
        self.cx, self.cy, self.r = disc or (self.width / 2, self.height / 2, 0.47 * self.height)
        self.zoom, self.blur_per_z, self.turn = zoom, blur_per_z, turn % 4
        self.fps, self.quality, self.noise = fps, jpeg_quality, noise
        h, w = self.slide.shape[:2]
        sx, sy = start if start is not None else (w / 2, h / 2)
        self._base = (float(sx), float(sy), float(z))  # where the stage is with no move under way
        self._moves: list[Move] = []
        self.limits = limits or (0.0, 0.0, float(w), float(h))  # (x0, y0, x1, y1) of the stage, slide px
        self.lock = threading.Lock()
        self.rng = np.random.default_rng(seed)
        yy, xx = np.mgrid[0:self.height, 0:self.width]
        d = np.hypot(xx - self.cx, yy - self.cy).astype(np.float32)
        lit = np.clip((self.r - d) / 3.0, 0, 1) * (1 - 0.22 * (d / self.r) ** 2)
        self._lit = lit[..., None].astype(np.float32)
        self._jpeg = b""
        self._seq = 0
        self._frame_ready = threading.Condition()
        self._stop = threading.Event()
        self._server: ThreadingHTTPServer | None = None
        self._actions: dict[str, dict] = {}
        self._ids = itertools.count(1)

    # --- the stage ---------------------------------------------------------------------------------------------------
    def position(self, t: float | None = None) -> tuple[float, float, float]:
        """(x, y, z) now: the base plus every move's share so far."""
        t = time.monotonic() if t is None else t
        with self.lock:
            x, y, z = self._base
            for m in self._moves:
                share = 1.0 if t >= m.end else 0.0 if t <= m.start else (t - m.start) / max(m.end - m.start, 1e-6)
                x, y, z = x + m.dx * share, y + m.dy * share, z + m.dz * share
            return x, y, z

    def _fold(self, t: float) -> None:
        """Moves that ended become the base (under the lock)."""
        done = [m for m in self._moves if m.end <= t]
        if done:
            x, y, z = self._base
            for m in done:
                x, y, z = x + m.dx, y + m.dy, z + m.dz
            self._base = (x, y, z)
            self._moves = [m for m in self._moves if m.end > t]

    def _clamp(self, x: float, y: float) -> tuple[float, float]:
        x0, y0, x1, y1 = self.limits
        return min(max(x, x0), x1), min(max(y, y0), y1)

    def hand(self, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, *, speed: float = HAND_SPEED,
             z_speed: float = 2.0, after: float = 0.0) -> float:
        """A person's move of the slide (dx, dy slide px in the hands' frame: +x right, +y toward them) and of the fine
        focus (dz), starting `after` s from now at hand speed. Returns when it will have ended (monotonic)."""
        now = time.monotonic()
        start = now + max(0.0, after)
        x, y, _ = self.position(start)
        tx, ty = self._clamp(x + dx, y + dy)
        dx, dy = tx - x, ty - y
        seconds = max(math.hypot(dx, dy) / max(speed, 1e-6), abs(dz) / max(z_speed, 1e-6), 0.05)
        with self.lock:
            self._fold(now)
            self._moves.append(Move(start, start + seconds, dx, dy, dz))
        return start + seconds

    def halt(self) -> None:
        """Stop every move where it is now ("stop")."""
        now = time.monotonic()
        x, y, z = self.position(now)
        with self.lock:
            self._moves = []
            self._base = (x, y, z)

    def move_to(self, x: float | None = None, y: float | None = None, z: float | None = None, *,
                speed: float | None = None, z_speed: float | None = None) -> float:
        """The motors' move to a place (None: that axis stays). Returns when it ends."""
        cx, cy, cz = self.position()
        tx, ty = self._clamp(cx if x is None else x, cy if y is None else y)
        tz = cz if z is None else z
        return self.hand(tx - cx, ty - cy, tz - cz, speed=speed or self.motor_speed, z_speed=z_speed or self.motor_z_speed)

    def moving(self) -> bool:
        now = time.monotonic()
        with self.lock:
            return any(m.end > now for m in self._moves)

    # --- what the camera sees ------------------------------------------------------------------------------------------
    def where(self, sx: float, sy: float) -> tuple[float, float]:
        """Where slide point (sx, sy) is in the picture now (camera px): the truth, for the harness."""
        x, y, _ = self.position()
        dx, dy = (sx - x) * self.zoom, (sy - y) * self.zoom
        for _ in range(self.turn):
            dx, dy = -dy, dx
        return self.cx + dx, self.cy + dy

    def slide_point(self, px: float, py: float) -> tuple[float, float]:
        """The slide point at picture point (px, py) now (the inverse of `where`)."""
        x, y, _ = self.position()
        dx, dy = px - self.cx, py - self.cy
        for _ in range(self.turn):
            dx, dy = dy, -dx
        return x + dx / self.zoom, y + dy / self.zoom

    def truth(self) -> dict:
        x, y, z = self.position()
        return {"x": x, "y": y, "z": z, "blur": abs(z) * self.blur_per_z, "moving": self.moving(),
                "disc": [self.cx, self.cy, self.r], "zoom": self.zoom, "turn": self.turn}

    def render(self) -> np.ndarray:
        x, y, z = self.position()
        half = int(math.ceil(self.r / self.zoom)) + 4
        m = np.float32([[1, 0, -(x - half)], [0, 1, -(y - half)]])
        view = cv2.warpAffine(self.slide, m, (2 * half, 2 * half), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        side = int(round(2 * half * self.zoom))
        view = cv2.resize(view, (side, side), interpolation=cv2.INTER_CUBIC)
        for _ in range(self.turn):
            view = cv2.rotate(view, cv2.ROTATE_90_CLOCKWISE)
        sigma = self.blur_per_z * abs(z)
        if sigma > 0.2:
            view = cv2.GaussianBlur(view, (0, 0), sigma)
        canvas = np.zeros((self.height, self.width, 3), np.float32)
        x0, y0 = int(round(self.cx)) - side // 2, int(round(self.cy)) - side // 2
        ax, ay = max(0, x0), max(0, y0)
        bx, by = min(self.width, x0 + side), min(self.height, y0 + side)
        canvas[ay:by, ax:bx] = view[ay - y0:by - y0, ax - x0:bx - x0]
        out = canvas * self._lit
        if self.noise > 0:
            out += self.rng.normal(0, self.noise, out.shape[:2])[..., None].astype(np.float32)
        return np.clip(out, 0, 255).astype(np.uint8)

    def jpeg(self) -> bytes:
        ok, buf = cv2.imencode(".jpg", self.render(), [cv2.IMWRITE_JPEG_QUALITY, self.quality])
        return buf.tobytes() if ok else b""

    def _render_loop(self) -> None:
        period = 1.0 / self.fps
        next_t = time.monotonic()
        while not self._stop.is_set():
            data = self.jpeg()
            with self._frame_ready:
                self._jpeg, self._seq = data, self._seq + 1
                self._frame_ready.notify_all()
            next_t += period
            self._stop.wait(max(0.0, next_t - time.monotonic()))
            if time.monotonic() - next_t > 1.0:
                next_t = time.monotonic()  # fell behind: never catch up in a burst

    def latest(self, after: int, timeout: float = 2.0) -> tuple[int, bytes]:
        with self._frame_ready:
            self._frame_ready.wait_for(lambda: self._seq > after or self._stop.is_set(), timeout)
            return self._seq, self._jpeg

    # --- OpenFlexure-like actions --------------------------------------------------------------------------------------
    def steps(self) -> dict:
        x, y, z = self.position()
        return {"x": int(round(x)), "y": int(round(y)), "z": int(round(z * STEPS_PER_Z))}

    def action(self, kind: str, ends: float, output: dict | None = None) -> dict:
        aid = f"a{next(self._ids)}"
        record = {"id": aid, "action": kind, "status": "running", "ends": ends, "output": output,
                  "href": f"/api/v2/actions/{aid}", "links": {"self": {"href": f"/api/v2/actions/{aid}"}}}
        self._actions[aid] = record
        return self.action_state(aid)

    def action_state(self, aid: str) -> dict | None:
        record = self._actions.get(aid)
        if record is None:
            return None
        if record["status"] == "running" and time.monotonic() >= record["ends"]:
            record["status"] = "completed"
            if record["action"] == "move":
                record["output"] = self.steps()
        return {k: v for k, v in record.items() if k != "ends"}

    def autofocus(self) -> float:
        """The motors find focus (the virtual one knows where it is): z to 0 at motor speed."""
        return self.move_to(z=0.0)

    # --- serving -------------------------------------------------------------------------------------------------------
    def serve(self, port: int = 0) -> int:
        scope = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _body(self) -> dict:
                n = int(self.headers.get("Content-Length") or 0)
                if not n:
                    return {}
                try:
                    data = json.loads(self.rfile.read(n))
                except ValueError:
                    return {}
                return data if isinstance(data, dict) else {}

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                if path in ("/stream.mjpeg", "/api/v2/streams/mjpeg"):
                    self._stream()
                elif path == "/api/v2/streams/snapshot":
                    _, data = scope.latest(-1, 0.5)
                    data = data or scope.jpeg()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif path == "/api/v2/instrument/state/stage/position":
                    self._json(scope.steps())
                elif path == "/api/v2/instrument/state/stage":
                    self._json({"position": scope.steps(), "type": "VirtualStage"})
                elif path.startswith("/api/v2/actions/"):
                    state = scope.action_state(path.rsplit("/", 1)[1])
                    self._json(state, HTTPStatus.OK) if state else self._json({"error": "no such action"}, HTTPStatus.NOT_FOUND)
                elif path == "/api/v2/":
                    self._json({"title": "Virtual microscope (Hooke test)", "version": "2.x-virtual"})
                elif path == "/truth":
                    self._json(scope.truth())
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

            def do_POST(self) -> None:
                path = self.path.split("?", 1)[0].rstrip("/") or "/"  # OpenFlexure's actions end in a slash
                body = self._body()
                if path == "/api/v2/actions/stage/move":
                    try:
                        x, y, z = (float(body.get(k, 0) or 0) for k in ("x", "y", "z"))
                    except (TypeError, ValueError):
                        self._json({"error": "x, y and z are numbers of steps"}, HTTPStatus.BAD_REQUEST)
                        return
                    if body.get("absolute"):
                        ends = scope.move_to(x, y, z / STEPS_PER_Z)
                    else:
                        ends = scope.hand(x, y, z / STEPS_PER_Z, speed=scope.motor_speed, z_speed=scope.motor_z_speed)
                    self._json(scope.action("move", ends), HTTPStatus.CREATED)
                elif path == "/api/v2/actions/camera/ram-capture":  # a new picture, rendered now
                    data = scope.jpeg()
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    self.wfile.write(data)
                elif path == "/api/v2/extensions/org.openflexure.autofocus/fast_autofocus":
                    self._json(scope.action("autofocus", scope.autofocus()), HTTPStatus.CREATED)
                elif path == "/hand":  # the harness's simulated person
                    if body.get("stop"):
                        scope.halt()
                        self._json(scope.truth())
                        return
                    scope.hand(float(body.get("dx", 0)), float(body.get("dy", 0)), float(body.get("dz", 0)),
                               after=float(body.get("after", 0)))
                    self._json(scope.truth())
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

            def _stream(self) -> None:
                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                seq = -1
                try:
                    while not scope._stop.is_set():
                        seq, data = scope.latest(seq)
                        if not data:
                            continue
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                                         + str(len(data)).encode() + b"\r\n\r\n" + data + b"\r\n")
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                self.close_connection = True

        self._server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._render_loop, name="virtual-scope-render", daemon=True).start()
        threading.Thread(target=self._server.serve_forever, name="virtual-scope-http", daemon=True).start()
        return self._server.server_address[1]

    @property
    def url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def close(self) -> None:
        self._stop.set()
        with self._frame_ready:
            self._frame_ready.notify_all()
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


# --- the qualified leaf recording as a slide --------------------------------------------------------------------------
LEAF_FRAME = 360  # the hand-labelled 12-second frame of the default leaf recording (bench/questions.json)
LEAF_DISC = (697.0, 360.0, 392.0)  # its eyepiece disc, camera px
LEAF_STOMATA = {"right": (853.0, 387.0), "middle": (667.0, 496.0)}  # labelled ovals (bench/questions.json "shape")


def leaf_scope(video: Path, *, target: str = "right", off: tuple[float, float] = (0.45, -0.3), z: float = 0.9,
               zoom: float = 1.6, **kwargs) -> tuple[VirtualScope, tuple[float, float]]:
    """A virtual scope over the leaf recording's labelled frame, placed so the labelled stoma `target` shows `off`
    radii from the centre (+x right, +y down in the picture), the focus `z` from sharp. Returns it and the stoma's
    slide point (the truth)."""
    capture = cv2.VideoCapture(str(video))  # a file, not a camera: reading test footage
    try:
        capture.set(cv2.CAP_PROP_POS_FRAMES, LEAF_FRAME)
        ok, frame = capture.read()
    finally:
        capture.release()
    if not ok:
        raise RuntimeError(f"could not read frame {LEAF_FRAME} of {video}")
    slide, origin = slide_from_frame(frame, LEAF_DISC[:2], LEAF_DISC[2])
    sx, sy = LEAF_STOMATA[target][0] + origin[0], LEAF_STOMATA[target][1] + origin[1]
    radius = 0.47 * 720
    start = (sx - off[0] * radius / zoom, sy - off[1] * radius / zoom)
    scope = VirtualScope(slide, size=(1280, 720), disc=(640.0, 360.0, radius), zoom=zoom, start=start, z=z, **kwargs)
    return scope, (sx, sy)


def _demo() -> None:  # pragma: no cover - a person's look at it: python -m sim.virtual_scope VIDEO
    import sys

    scope, stoma = leaf_scope(Path(sys.argv[1]))
    port = scope.serve(int(sys.argv[2]) if len(sys.argv) > 2 else 18990)
    print(f"virtual scope on {scope.url}/stream.mjpeg; the stoma is at {scope.where(*stoma)}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        scope.close()


if __name__ == "__main__":  # pragma: no cover
    _demo()
