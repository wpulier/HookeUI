"""The Blackmagic settings file and the second, tiny server a phone fetches it from (the camera page's QR code).
Which addresses and port the file names is the main module's to say (iphone_bridge_server.py settings_file)."""
from __future__ import annotations

import os
import threading
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import urlparse


SETTINGS_PORT = 8767


def settings_port() -> int:
    port = int(os.environ.get("MICROMIND_CAMERA_SETTINGS_PORT", str(SETTINGS_PORT)))
    if not 1 <= port <= 65535:
        raise ValueError("Camera settings port must be between 1 and 65535")
    return port


def settings_xml(addresses: dict[str, str | None], port: int) -> bytes:
    """The settings file Blackmagic Camera imports, for this Mac's listener at `addresses` (Wi-Fi, USB)."""
    from phone_control import platform_xml

    return platform_xml(addresses, port).encode("utf-8")


def settings_url() -> str | None:
    """Where a phone on this Mac's network fetches the settings file (the QR code on the camera page)."""
    from stream_source import lan_address

    address = lan_address()
    return f"http://{address}:{settings_port()}/MicroscopeMind.xml" if address else None


def serve_settings(settings_file: Callable[[], bytes], port: int | None = None):
    """A second, tiny server reachable from the phone (the bridge itself answers this Mac only): it serves the
    Blackmagic settings file, made fresh for each request (`settings_file()`), and nothing else."""

    class SettingsHandler(SimpleHTTPRequestHandler):
        def log_message(self, format: str, *args_: object) -> None:
            pass

        def do_GET(self) -> None:
            if urlparse(self.path).path != "/MicroscopeMind.xml":
                self.send_error(HTTPStatus.NOT_FOUND, "Only the MicroscopeMind settings file is here")
                return
            payload = settings_file()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/xml; charset=utf-8")
            self.send_header("Content-Disposition", 'attachment; filename="MicroscopeMind.xml"')
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_HEAD = do_GET

    port = settings_port() if port is None else port
    try:
        server = ThreadingHTTPServer(("0.0.0.0", port), SettingsHandler)
    except OSError as exc:
        print(f"The settings file server could not start on :{port}: {exc}", flush=True)
        return None
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True, name="settings-server").start()
    return server
