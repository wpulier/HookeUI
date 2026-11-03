"""The scope bridge's HTTP API, on this Mac only (127.0.0.1), standard library only, as the camera bridge's is.

  GET  /api/state                 the backend, where the stage is, exposure, channel, light, the limits, what runs
  POST /api/do                    {"action": {...}, "approval": "y3"?}  ->  its receipt (bridge.py)
  POST /api/stop                  stop moving and acquiring now  ->  its receipt
  GET  /api/limits                the user's limits
  PUT  /api/limits                {"limits": {...}, "words": "...", "approval"?}  ->  done, or needs-yes to loosen them
  GET  /api/receipts?since=N      the receipts after N
  GET  /api/files/<path>          a picture it made (only under its own pictures folder)

`stop` is served while an action runs (each request has its own thread); everything else that acts waits its turn. A
request that carries an Origin (a web page's) is refused.
"""
from __future__ import annotations

import json
import mimetypes
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from .bridge import ScopeBridge


def serve(bridge: ScopeBridge, port: int = 0) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args) -> None:
            pass

        def _json(self, payload, status: HTTPStatus = HTTPStatus.OK) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _body(self) -> dict | None:
            n = int(self.headers.get("Content-Length") or 0)
            if n > 1_000_000:
                return None
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return None
            return data if isinstance(data, dict) else None

        def _from_a_page(self) -> bool:
            """A web page's request (browsers name its Origin) is refused: only Hooke's own programs ask the bridge."""
            if self.headers.get("Origin") is not None:
                self._json({"error": "the scope bridge answers Hooke's programs on this Mac, not web pages"},
                           HTTPStatus.FORBIDDEN)
                return True
            return False

        def do_GET(self) -> None:
            if self._from_a_page():
                return
            parts = urlsplit(self.path)
            if parts.path == "/api/state":
                self._json(bridge.wire())
            elif parts.path == "/api/limits":
                self._json(bridge.limits.wire())
            elif parts.path == "/api/receipts":
                try:
                    since = int(parse_qs(parts.query).get("since", ["0"])[0])
                except ValueError:
                    since = 0
                self._json({"receipts": bridge.receipts.since(since)})
            elif parts.path.startswith("/api/files/"):
                self._file(unquote(parts.path[len("/api/files/"):]))
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def _file(self, rel: str) -> None:
            root = (bridge.folder / "pictures").resolve()
            path = (bridge.folder / rel).resolve()
            if root not in path.parents or not path.is_file():
                self._json({"error": "no such picture"}, HTTPStatus.NOT_FOUND)
                return
            data = path.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            if self._from_a_page():
                return
            path = urlsplit(self.path).path
            if path == "/api/stop":
                self._json(bridge.stop())
                return
            body = self._body()
            if body is None:
                self._json({"error": "the body is a JSON object"}, HTTPStatus.BAD_REQUEST)
                return
            if path == "/api/do":
                approval = body.get("approval")
                self._json(bridge.do(body.get("action"), approval if isinstance(approval, str) else None))
            else:
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)

        def do_PUT(self) -> None:
            if self._from_a_page():
                return
            if urlsplit(self.path).path != "/api/limits":
                self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
                return
            body = self._body()
            if body is None or not isinstance(body.get("limits"), dict):
                self._json({"error": "send {limits: {...}, words: ...}"}, HTTPStatus.BAD_REQUEST)
                return
            approval = body.get("approval")
            try:
                self._json(bridge.set_limits(body["limits"], str(body.get("words") or ""),
                                             approval if isinstance(approval, str) else None))
            except ValueError as exc:
                self._json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, name="scope-bridge-http", daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> None:
    import argparse
    import signal
    import time

    from .backends import make

    parser = argparse.ArgumentParser(prog="python -m scope_bridge", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--backend", default="mm-demo", choices=("mm-demo", "mm", "openflexure"))
    parser.add_argument("--config", help="mm: the Micro-Manager configuration file")
    parser.add_argument("--adapters", help="mm: the Micro-Manager folder with the device adapters")
    parser.add_argument("--url", help="openflexure: the microscope's server, http://...")
    parser.add_argument("--um-per-step", type=float, help="openflexure: µm per motor step across (its calibration)")
    parser.add_argument("--um-per-z-step", type=float, help="openflexure: µm per motor step in focus (its calibration)")
    parser.add_argument("--folder", required=True, help="where its limits, receipts and pictures are kept")
    parser.add_argument("--port", type=int, default=8780)
    args = parser.parse_args(argv)
    if args.backend == "openflexure" and (not args.url or not args.um_per_step or not args.um_per_z_step):
        parser.error("an OpenFlexure needs --url and its stage's calibration: --um-per-step and --um-per-z-step "
                     "(limits are in µm; a guess would make them mean something else)")
    backend = make(args.backend, config=args.config, adapters=args.adapters, url=args.url, um_per_step=args.um_per_step,
                   um_per_z_step=args.um_per_z_step)
    bridge = ScopeBridge(backend, Path(args.folder))
    server = serve(bridge, args.port)
    print(f"Scope bridge ({backend.describe().get('backend')}) on http://127.0.0.1:{server.server_address[1]}", flush=True)
    done = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: done.set())
    while not done.wait(1.0):
        pass
    bridge.stop()
    server.shutdown()
    time.sleep(0.05)
