"""The camera bridge's HTTP side: its server, and its handler as a route table. Each route names the handler method
that answers it and, for the controls only a local page or program may press, the X-MicroscopeMind header it needs
and what a caller without it is told (403), checked in one place (`BridgeHandler._allowed`). The camera page is served
from its build (/camera/, --page-dir); nothing else is a file, so anything else is not found. The bridge's live state and the functions that act on it are the
main module's (iphone_bridge_server.py), reached through `main` (bridge_common.ModuleView), which its
IPhoneBridgeHandler sets."""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import sys
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import NamedTuple, Optional
from urllib.parse import parse_qs, urlparse

from bridge_common import CAMERA_PAGE_DIR, CAMERA_PAGE_MISSING, ModuleView
from bridge_recording import recording_owner
from bridge_sources import source_choices
from settings_server import settings_url

# Only pages served from this Mac (the studio on :8770, its dev server on :5180) may call
# the bridge from a browser.
# Blackmagic Camera on the App Store (free, by Blackmagic Design), for the setup's first step.
BLACKMAGIC_APP_STORE = "https://apps.apple.com/app/blackmagic-camera/id6449580241"

LOCAL_ORIGIN = re.compile(r"^http://(127\.0\.0\.1|localhost)(:\d+)?$")


class Guard(NamedTuple):
    """Who may press a control: a caller that sends `X-MicroscopeMind: <header>` (which also makes a browser ask
    first, so another site cannot), and with `local_origin` only from a page served from this Mac. Anyone else is
    told `refusal`, with 403."""

    header: str
    refusal: str
    local_origin: bool = False


class Route(NamedTuple):
    handler: str  # the BridgeHandler method that answers it
    guard: Optional[Guard] = None


CAMERA_CONTROL = "camera-control"
CAMERA_SOURCE = "camera-source"
# Reset camera is a POST /api/restart with reset=1: only the reset needs the header, a plain reconnect does not.
RESET_GUARD = Guard(CAMERA_CONTROL, "Reset camera is pressed in the camera console or the studio")

ROUTES: dict[tuple[str, str], Route] = {
    ("GET", "/"): Route("get_root"),
    ("GET", "/camera"): Route("get_root"),
    ("GET", "/web-camera-check.html"): Route("get_root"),  # the camera page's old address, still linked
    ("GET", "/favicon.ico"): Route("get_favicon"),
    ("GET", "/api/preflight"): Route("get_preflight"),
    ("GET", "/api/source"): Route("get_source"),
    ("GET", "/api/cameras"): Route("get_cameras"),
    ("GET", "/api/cadence"): Route("get_cadence"),
    ("GET", "/api/trials"): Route("get_trials"),
    ("GET", "/api/phone"): Route("get_phone"),
    ("GET", "/api/phone-check"): Route("get_phone_check"),
    ("GET", "/api/doctor"): Route("get_doctor"),
    ("GET", "/api/settings-qr.svg"): Route("get_settings_qr"),
    ("GET", "/api/app-store-qr.svg"): Route("get_app_store_qr"),
    ("GET", "/api/blackmagic.xml"): Route("get_settings_file"),
    ("GET", "/api/health"): Route("get_health"),
    ("GET", "/api/record/status"): Route("get_record_status"),
    ("GET", "/snapshot.jpg"): Route("send_snapshot"),
    ("GET", "/feed"): Route("get_feed"),
    ("GET", "/assets/library"): Route("send_library"),
    ("GET", "/assets/thumb"): Route("get_thumbnail"),
    ("GET", "/sim/status"): Route("get_sim_status"),
    ("GET", "/iphone.mjpeg"): Route("stream_iphone"),
    ("POST", "/feed"): Route("post_feed"),
    ("POST", "/feed/pause"): Route("post_feed_pause"),
    ("POST", "/feed/seek"): Route("post_feed_seek"),
    ("POST", "/assets/upload"): Route("post_upload", Guard("add-footage", "Recordings and pictures are added from the studio",
                                                           local_origin=True)),
    ("POST", "/api/restart"): Route("post_restart"),  # its reset is guarded by RESET_GUARD
    ("POST", "/api/photo"): Route("post_photo", Guard(CAMERA_CONTROL, "Photos are taken from the camera page or the studio")),
    ("POST", "/api/photo/reveal"): Route("post_photo_reveal", Guard(CAMERA_CONTROL, "Photos are shown from the camera page or the studio")),
    ("POST", "/api/phone-check"): Route("post_phone_check", Guard(CAMERA_CONTROL, "The Phone check is started from the camera page")),
    ("POST", "/api/trial"): Route("post_trial", Guard(CAMERA_SOURCE, "Source checks are started from the camera console")),
    # The console's camera controls (Blackmagic Camera's REST API through the phone link). Only from a local page or
    # program that says so, as the source switch.
    ("POST", "/api/phone"): Route("post_phone", Guard(CAMERA_CONTROL, "The camera is controlled from the camera console")),
    # The console's live-source switch. Only from a local page or a local program that says so (its own header, which
    # also makes a browser ask first, so another site cannot switch it); a URL is only ever srt:// or rtmp://
    # (stream_source.SCHEMES).
    ("POST", "/api/source"): Route("post_source", Guard(CAMERA_SOURCE, "The live source is chosen from the camera console")),
    ("POST", "/api/record/start"): Route("post_record_start"),
    ("POST", "/api/record/stop"): Route("post_record_stop"),
}
# Routes for every path under a prefix, looked at before the exact ones; their handler is given the path and the query.
PREFIX_ROUTES: dict[str, list[tuple[str, str]]] = {
    "GET": [("/camera/", "send_camera_page")],
    "POST": [("/sim/", "handle_simulation_control")],
}


class BridgeServer(ThreadingHTTPServer):
    # Python's default backlog is 5: a studio loading thumbnails while it polls, streams frames and
    # uploads opens more connections than that at once, and the rest were refused (sim media-load).
    request_queue_size = 128
    daemon_threads = True

    def handle_error(self, request, client_address) -> None:
        # A page closed while it was being answered (the console's live picture, a poll) is not an error: its
        # traceback filled the log on every tab close. Anything else is still printed.
        if isinstance(sys.exc_info()[1], (BrokenPipeError, ConnectionResetError)):
            return
        super().handle_error(request, client_address)


class BridgeHandler(BaseHTTPRequestHandler):
    server_version = "IPhoneBridge/2.0"
    main: ModuleView  # the main module's names (set by iphone_bridge_server.IPhoneBridgeHandler)
    bridge_args: argparse.Namespace
    device_snapshot: dict[str, object]
    camera_bridge: object  # iphone_bridge_server.CameraBridge

    def log_message(self, format: str, *args: object) -> None:
        sys.stderr.write("%s - - [%s] %s\n" % (self.address_string(), self.log_date_time_string(), format % args))

    # What the camera page and the studio poll several times a second: answered without a log line (2026-10-12: 80% of
    # a 22 MB, 317,000-line bridge log). A failed answer is still logged, as is every other request.
    QUIET = ("/api/health", "/api/cadence", "/api/doctor", "/api/phone", "/api/phone-check", "/api/trials", "/api/source",
             "/feed", "/api/record/status", "/iphone.mjpeg", "/snapshot.jpg", "/assets/thumb", "/favicon.ico")

    def log_request(self, code: object = "-", size: object = "-") -> None:
        try:
            status = int(code)  # an HTTPStatus is an int
        except (TypeError, ValueError):
            status = 0
        if 0 < status < 400 and urlparse(self.path).path in self.QUIET:
            return
        super().log_request(code, size)

    cache_control = "no-store"  # a thumbnail sets its own (immutable: its URL changes with the file)
    # Small writes go out at once (the live frames: Nagle's algorithm would hold one back for an acknowledgement).
    disable_nagle_algorithm = True

    def end_headers(self) -> None:
        self.send_header("Cache-Control", self.cache_control)
        origin = self.headers.get("Origin", "")
        if LOCAL_ORIGIN.match(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        if LOCAL_ORIGIN.match(self.headers.get("Origin", "")):
            self.send_header("Access-Control-Allow-Methods", "GET, POST")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-MicroscopeMind")
        self.end_headers()

    # --- dispatch ------------------------------------------------------------------------------------------------
    def do_GET(self) -> None:
        self.cache_control = "no-store"  # per request: a kept-alive connection serves several
        if not self._dispatch("GET"):
            self.send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)

    def do_HEAD(self) -> None:
        # Nothing is served as a plain file (the camera page is GET /camera/...).
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_POST(self) -> None:
        self.cache_control = "no-store"
        if not self._dispatch("POST"):
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _dispatch(self, method: str) -> bool:
        """Answer the request from the route table; False when no route has its path."""
        path = urlparse(self.path).path
        for prefix, handler in PREFIX_ROUTES.get(method, ()):
            if path.startswith(prefix):
                getattr(self, handler)(path, self.query())
                return True
        route = ROUTES.get((method, path))
        if route is None:
            return False
        if self._allowed(route.guard):
            getattr(self, route.handler)()
        return True

    def _allowed(self, guard: Guard | None) -> bool:
        """Whether this caller may press the control `guard` keeps; if not, it is told so (403)."""
        if guard is None:
            return True
        if self.headers.get("X-MicroscopeMind") == guard.header and (
                not guard.local_origin or LOCAL_ORIGIN.match(self.headers.get("Origin", ""))):
            return True
        self.send_json({"ok": False, "error": guard.refusal}, HTTPStatus.FORBIDDEN)
        return False

    def query(self) -> dict[str, list[str]]:
        return parse_qs(urlparse(self.path).query)

    # --- GET -----------------------------------------------------------------------------------------------------
    def get_root(self) -> None:
        self.send_response(HTTPStatus.FOUND)
        self.send_header("Location", "/camera/")
        self.end_headers()

    def get_favicon(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.end_headers()

    def get_preflight(self) -> None:
        snapshot = self.camera_bridge.probe_device()
        self.device_snapshot = snapshot
        self.send_json({**snapshot, "bridge": self.camera_bridge.health()})

    def get_source(self) -> None:
        link = self.camera_bridge.phone_link
        self.send_json({"choices": source_choices(self.bridge_args, self.main.live_source_kind(self.bridge_args),
                                                  link.snapshot() if link else None),
                        "bridge": self.camera_bridge.health()})

    def get_cameras(self) -> None:
        # Other camera's picker: the cameras this Mac sees now (a device scan, so only when the picker asks).
        self.send_json({"cameras": self.main.camera_list(self.bridge_args)})

    def get_cadence(self) -> None:
        self.send_json(self.camera_bridge.cadence_trace())

    def get_trials(self) -> None:
        bridge = self.camera_bridge
        self.send_json({"running": bridge.trial_running, "trials": list(bridge.trials)})

    def get_phone(self) -> None:
        link = self.camera_bridge.ensure_phone_link()
        if link is None:
            self.send_json({"reachable": False, "disabled": True})
            return
        link.watch()  # the console is looking: keep the phone's state fresh
        self.send_json(link.snapshot())

    def get_phone_check(self) -> None:
        self.send_json(self.camera_bridge.phone_check())

    def get_doctor(self) -> None:
        quiet = self.query().get("quiet", ["0"])[0] in ("1", "true")
        if self.camera_bridge.phone_link and not quiet:
            # Someone is looking at it: keep the phone's facts fresh. Quiet is the studio's header dot, which
            # only colours itself and never makes the phone be asked more often.
            self.camera_bridge.phone_link.watch()
        self.send_json(self.camera_bridge.doctor())

    def get_app_store_qr(self) -> None:
        # The QR code a phone scans to get Blackmagic Camera from the App Store (the setup's first step).
        from qr import svg

        payload = svg(BLACKMAGIC_APP_STORE).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def get_settings_qr(self) -> None:
        # The QR code a phone scans to fetch the settings file from the settings server (start_settings_server).
        from qr import svg

        url = settings_url()
        if not url:
            self.send_error(HTTPStatus.NOT_FOUND, "This Mac has no network address the phone could reach")
            return
        payload = svg(url).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("X-Settings-Url", url)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def get_settings_file(self) -> None:
        # The settings file Blackmagic Camera imports (Settings › Live Stream › Platform › Import Stream Settings):
        # this Mac's listener over Wi-Fi and, when the cable link is up, over USB.
        payload = self.main.settings_file(self.bridge_args)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/xml; charset=utf-8")
        self.send_header("Content-Disposition", 'attachment; filename="MicroscopeMind.xml"')
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def get_health(self) -> None:
        self.send_json(self.camera_bridge.health())

    def get_record_status(self) -> None:
        self.send_json(self.camera_bridge.recording_status())

    def get_feed(self) -> None:
        query = self.query()
        self.send_json(self.main.feed_state(self.bridge_args, self.camera_bridge, probe_camera=query.get("probe", ["0"])[0] == "1"))

    def get_thumbnail(self) -> None:
        self.send_thumbnail(self.query().get("name", [""])[0])

    def get_sim_status(self) -> None:
        if not self.main.SIMULATED_CAMERA:
            self.send_error(HTTPStatus.NOT_FOUND, "Not simulating")
            return
        self.send_json(self.main.SIMULATED_CAMERA.status())

    # --- POST ----------------------------------------------------------------------------------------------------
    def post_feed(self) -> None:
        query = self.query()
        source = query.get("source", ["camera"])[0]
        try:
            if source not in ("camera", "footage", "recording", "asset"):
                raise ValueError("source is camera, footage, recording or asset")
            # Where to start and whether held there (footage, a session's recording, an asset): one call, so a
            # session opened on its recording is never seen playing first.
            paused = query.get("paused", ["0"])[0] in ("1", "true")
            at_text = query.get("at", [""])[0]
            try:
                at = float(at_text) if at_text else None
            except ValueError:
                raise ValueError("at is a number of seconds") from None
            if at is not None and not 0 <= at < 1e7:
                raise ValueError("at is a number of seconds")
            if source == "recording":
                recording = (query.get("dataset", [""])[0], query.get("part", ["1"])[0])
                self.send_json(self.main.switch_feed(self.bridge_args, self.camera_bridge, None, recording=recording,
                                                     paused=paused, at=at))
                return
            if source == "asset":
                asset = (query.get("path", [""])[0], query.get("title", [""])[0])
                self.send_json(self.main.switch_feed(self.bridge_args, self.camera_bridge, None, asset=asset,
                                                     paused=paused, at=at))
                return
            footage = query.get("footage", [""])[0] if source == "footage" else None
            self.send_json(self.main.switch_feed(self.bridge_args, self.camera_bridge, footage or None, paused=paused, at=at))
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def post_feed_pause(self) -> None:
        paused = self.query().get("paused", ["1"])[0] not in ("0", "false")
        self.send_json(self.main.pause_feed(self.bridge_args, self.camera_bridge, paused))

    def post_feed_seek(self) -> None:
        try:
            seconds = float(self.query().get("seconds", [""])[0])
        except ValueError:
            self.send_json({"ok": False, "error": "seconds is a number"}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json(self.main.seek_feed(self.bridge_args, self.camera_bridge, seconds))

    def post_upload(self) -> None:
        self.receive_upload(self.query())

    def post_restart(self) -> None:
        if self.query().get("reset", ["0"])[0] in ("1", "true"):
            if not self._allowed(RESET_GUARD):
                return
            self.send_json(self.camera_bridge.reset_camera())
            return
        self.send_json(self.camera_bridge.reconnect("manual iPhone reconnect requested"))

    def post_photo(self) -> None:
        result = self.camera_bridge.take_photo()
        self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT)

    def post_photo_reveal(self) -> None:
        result = self.camera_bridge.reveal_photo(self.query().get("name", [""])[0])
        self.send_json(result, HTTPStatus.OK if result.get("ok") else HTTPStatus.NOT_FOUND)

    def post_phone_check(self) -> None:
        self.send_json(self.camera_bridge.phone_check(start=True))

    def post_trial(self) -> None:
        seconds = self.query().get("seconds", ["60"])[0]
        try:
            self.send_json({"ok": True, "trial": self.camera_bridge.start_trial(float(seconds))})
        except ValueError:
            self.send_json({"ok": False, "error": "seconds is a number"}, HTTPStatus.BAD_REQUEST)

    def post_phone(self) -> None:
        from phone_control import PhoneError

        query = self.query()
        link = self.camera_bridge.ensure_phone_link()
        if link is None:
            self.send_json({"ok": False, "error": "This bridge doesn't control a phone (--no-phone)"}, HTTPStatus.FORBIDDEN)
            return
        try:
            state = link.act(query.get("do", [""])[0], query.get("value", [None])[0])
            self.send_json({"ok": True, "phone": state})
        except (PhoneError, ValueError, TypeError) as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_GATEWAY)

    def post_source(self) -> None:
        query = self.query()
        try:
            self.send_json(self.main.switch_source(self.bridge_args, self.camera_bridge, query.get("kind", [""])[0],
                                                   query.get("url", [""])[0].strip() or None,
                                                   query.get("device", [""])[0].strip() or None))
        except ValueError as exc:
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)

    def post_record_start(self) -> None:
        self.send_json(self.camera_bridge.start_recording(recording_owner(self.query())))

    def post_record_stop(self) -> None:
        query = self.query()
        force = query.get("force", ["0"])[0] in {"1", "true"}
        self.send_json(self.camera_bridge.stop_recording(recording_owner(query), force=force))

    def handle_simulation_control(self, path: str, query: dict[str, list[str]]) -> None:
        """Fault injection for test footage (scenario harness only; needs footage playing)."""
        sim = self.main.SIMULATED_CAMERA
        if not sim:
            self.send_error(HTTPStatus.NOT_FOUND, "Not simulating")
            return
        action = path[len("/sim/"):]
        if action == "freeze":
            sim.freeze(float(query.get("seconds", ["6"])[0]))
        elif action == "crash":
            sim.crash()
        elif action == "unplug":
            sim.unplug(recover_on_reset=query.get("recover", ["true"])[0] != "false")
        elif action == "plug":
            sim.plug()
        elif action == "repeat":
            sim.repeat(float(query.get("seconds", ["6"])[0]))
        elif action == "degrade":
            # Blur, vignetting, exposure... (simulated_camera.DEGRADATIONS); none: clean again.
            try:
                sim.degrade(**{name: values[0] for name, values in query.items()})
            except ValueError as exc:
                self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            sim.crash()  # FFmpeg applies it from the current world position
        elif action == "seek":
            # Moving the specimen to another part of the recording; the capture restarts
            # there, as if the slide jumped.
            sim.seek(int(query.get("frame", ["0"])[0]))
            sim.crash()
        else:
            self.send_error(HTTPStatus.NOT_FOUND, f"Unknown simulation action {action}")
            return
        self.camera_bridge.recording_event(f"sim-{action}", **{name: values[0] for name, values in query.items()})
        self.send_json({"ok": True, "simulation": sim.status()})

    # --- test footage for the camera menu and the Assets view (footage_media.py) --------------------
    def send_library(self) -> None:
        folder = Path(self.bridge_args.footage_dir)
        library = self.main.library_for(folder)
        version = library.version()
        if self.headers.get("If-None-Match", "") == f'"{version}"':
            self.send_response(HTTPStatus.NOT_MODIFIED)
            self.send_header("ETag", f'"{version}"')
            self.end_headers()
            return
        payload = json.dumps({"version": version, "folder": str(folder), "items": library.items()},
                             separators=(",", ":")).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("ETag", f'"{version}"')
        self.end_headers()
        self.wfile.write(payload)

    def send_thumbnail(self, name: str) -> None:
        library = self.main.library_for(Path(self.bridge_args.footage_dir))
        item = library.find(name)
        if item is None:
            self.send_error(HTTPStatus.NOT_FOUND, "No such recording")
            return
        image = library.thumbnail(item)
        if image is None:
            self.send_response(HTTPStatus.SERVICE_UNAVAILABLE)
            self.send_header("Retry-After", "2")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.cache_control = "public, max-age=31536000, immutable"  # its URL carries the file's mtime
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(image)))
        self.end_headers()
        self.wfile.write(image)

    def receive_upload(self, query: dict) -> None:
        """A recording or a picture added from the studio's Assets view. Only from the studio: its route's guard
        wants its own header, which also makes the browser ask first, so another page cannot post one."""
        library = self.main.library_for(Path(self.bridge_args.footage_dir))
        try:
            def number(key: str):
                try:
                    return float(query.get(key, [""])[0])
                except ValueError:
                    return None

            def receive() -> dict:
                return library.receive(self.rfile, int(self.headers.get("Content-Length") or 0), query.get("filename", [""])[0],
                                       query.get("title", [""])[0].strip(), query.get("specimen", [""])[0].strip(),
                                       query.get("microscope", ["1"])[0] != "0",
                                       {"objective": number("objective"), "eyepiece": number("eyepiece")})

            # The option --upload-priority background: the copy and its check on a thread of their own at background
            # priority (footage_media.in_background), as thumbnails run; this request thread only waits for it.
            from footage_media import in_background

            background = getattr(self.bridge_args, "upload_priority", "normal") == "background"
            item = in_background(receive) if background else receive()
        except ValueError as exc:
            self.close_connection = True
            self.send_json({"ok": False, "error": str(exc)}, HTTPStatus.BAD_REQUEST)
            return
        self.send_json({"ok": True, "item": item})

    def send_camera_page(self, path: str, query: dict[str, list[str]]) -> None:
        """The built camera page and its assets, from --page-dir only; a path outside it is not found."""
        name = path[len("/camera/"):]
        folder = Path(getattr(self.bridge_args, "page_dir", None) or CAMERA_PAGE_DIR).resolve()
        target = (folder / (name or "camera.html")).resolve()
        if folder not in target.parents or not target.is_file():
            if not name and not target.exists():
                self.send_bytes(CAMERA_PAGE_MISSING, "text/html; charset=utf-8")
                return
            self.send_json({"ok": False, "error": "not found"}, HTTPStatus.NOT_FOUND)
            return
        kind = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if kind.startswith("text/") or kind in ("application/javascript", "image/svg+xml"):
            kind += "; charset=utf-8"
        self.send_bytes(target.read_bytes(), kind)

    def send_bytes(self, payload: bytes, content_type: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(payload)

    def send_json(self, value: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        payload = json.dumps(value, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def send_snapshot(self) -> None:
        frame = self.camera_bridge.latest_snapshot()
        if not frame:
            self.send_json(
                {
                    "ok": False,
                    "error": "No fresh frame available",
                    "bridge": self.camera_bridge.health(),
                },
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(frame)))
        self.send_header("X-Target-Camera", self.bridge_args.target_device)
        self.end_headers()
        self.wfile.write(frame)

    def stream_iphone(self) -> None:
        # Start a capture only if none is running; a capture still waiting for the phone's
        # first frame is left alone (the monitor gives it first_frame_timeout).
        bridge = self.camera_bridge
        bridge.ensure_capture("MJPEG stream requested fresh iPhone frames")

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "multipart/x-mixed-replace;boundary=iphonebridge")
        self.send_header("X-Target-Camera", self.bridge_args.target_device)
        # This run of the bridge: the X-Frame-Seq numbers below count within it (a restarted
        # bridge starts again at 1, on a new connection).
        self.send_header("X-Bridge-Boot", bridge.boot_id)
        self.end_headers()

        last_seen = 0
        last_sent = time.monotonic()
        # Each part ends with the next boundary, so a browser shows a frame as soon as it arrives (it closes a part
        # only when it sees the next boundary: with the boundary sent before each frame, every frame showed one late).
        try:
            self.wfile.write(b"--iphonebridge\r\n")
        except (BrokenPipeError, ConnectionResetError):
            return
        with bridge.condition:
            bridge.readers += 1
        try:
            while not bridge.stop_event.is_set():
                last_seen, frame, taken_at = bridge.wait_for_frame_at(last_seen, timeout=2.0)
                if not frame:
                    # Without writes a departed client goes unnoticed, so after a while
                    # without frames the response ends and the client reconnects.
                    if time.monotonic() - last_sent >= self.bridge_args.stream_idle_timeout:
                        return
                    continue
                # X-Frame-Seq: the bridge's number for this frame (per bootId, in /api/health), the
                # same `seq` a recording's sidecar gives it, so a reader can join the two.
                header = (
                    b"Content-Type: image/jpeg\r\n"
                    + f"X-Frame-Seq: {last_seen}\r\nX-Frame-At: {taken_at:.4f}\r\nContent-Length: {len(frame)}\r\n\r\n".encode("ascii")
                )
                self.wfile.write(b"".join((header, frame, b"\r\n--iphonebridge\r\n")))  # one write a frame
                self.wfile.flush()
                last_sent = time.monotonic()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            with bridge.condition:
                bridge.readers -= 1
