"""A stand-in camera bridge for looking at the camera page with no camera and no phone: it serves the built page
(apps/studio/dist-camera, `make camera-page`) at /camera/ and answers its API from a few made-up states.

    python services/tests/camera_page_standin.py [--port 8897] [--state live|busy|fresh|setup|connect|iphone|other|other-none|none|down]

Then open http://127.0.0.1:8897/camera/. Controls change the stand-in phone, so clicking through the page works.
Standard library only. Not a test itself: tests use the stand-in phone in test_camera_phone.py.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PAGE = Path(__file__).resolve().parents[2] / "apps" / "studio" / "dist-camera"

CHECKS = [("app", "Blackmagic Camera is open on the phone", "it answered"), ("link", "The phone can reach this Mac", "over Wi-Fi"),
          ("settings", "HookeUI is set up in the app", "it streams to this Mac"),
          ("quality", "Its stream quality is safe", "it doesn't crash the app"),
          ("proven", "Its settings can be changed from here", "checked on this version of the app"),
          ("streaming", "The phone is streaming", "yes"), ("arriving", "The picture is arriving", "30 a second"),
          ("size", "The picture is the right size", "the size the app is built for"),
          ("moving", "The picture isn't frozen", "each picture is new"),
          ("app-reading", "HookeUI is showing the picture", "yes"), ("room", "This Mac has room", "mostly idle")]


def make_state(name: str) -> dict:
    phone = {"reachable": True, "name": "A", "model": "iPhone 17 Pro", "via": "wifi", "link": "wifi", "autofocus": False,
             "focus": 0.42, "autoExposure": "Off", "iso": 200, "shutter": 60, "whiteBalance": 5600, "tint": -6, "fps": "30.00",
             "battery": 82, "recording": False, "clipName": None, "timecode": "00:10:04:02", "clips": 3, "recordTimeLeftS": 36441,
             "focalLength": 24, "zoom": 0.0, "lenses": [24, 48, 100, 200], "stabilization": False, "autoWhiteBalance": True,
             "streaming": True, "streamStatus": "Streaming", "server": "This Mac (Wi-Fi)",
             "addresses": {"wifi": "srt://192.168.50.109:9710", "usb": None}}
    items = [{"id": i, "title": t, "ok": True, "evidence": e, "detail": "", "fix": "", "action": None, "problem": ""} for i, t, e in CHECKS]
    doctor = {"source": "blackmagic", "chosen": "blackmagic", "via": "Wi-Fi", "live": True, "headline": "Live, over Wi-Fi",
              "first": None, "items": items, "events": [{"at": "17:56:19", "text": "Live source: Blackmagic"}],
              "report": "Camera: blackmagic - live"}
    health = {"ok": True, "sourceKind": "blackmagic", "targetDevice": "Blackmagic Camera", "stateLabel": "Live",
              "frameCount": 9000, "lastFrameAgeSeconds": 0.02, "phone": phone,
              "cadence": {"fps": 30.0, "p50Ms": 33.0, "p95Ms": 68.0, "maxGapMs": 120.0, "even": False}}
    if name == "busy":
        items[-1].update(ok=False, evidence="very busy", detail="load 1.8 per core", problem="this Mac is busy",
                         fix="Close apps you don't need (the studio's meter lists what is busiest): a very busy Mac slows the live view.")
        doctor.update(first="room", headline="Live over Wi-Fi, but this Mac is busy")
    if name == "setup":
        for item in items[2:]:
            item["ok"] = None
        items[2].update(ok=False, evidence="the app doesn't know where to send the picture", fix="Import the settings file.",
                        action="settings-file")
        del items[3:]
        doctor.update(live=False, first="settings", headline="Not live: HookeUI's connection isn't set up in the app")
        health.update(ok=False)
        phone.update(streaming=False, streamStatus="Idle")
    if name == "fresh":  # Blackmagic chosen, the app not installed or not reachable yet: the setup's first step
        items[0].update(ok=False, evidence="it doesn't answer", fix="Open Blackmagic Camera on the iPhone.", problem="Blackmagic Camera isn't open")
        del items[2:]
        items[1]["ok"] = True
        doctor.update(live=False, first="app", headline="Not live: Blackmagic Camera isn't open")
        health.update(ok=False, phone={"reachable": False})
    if name.startswith("other"):  # another camera: a USB microscope camera, chosen (other) or not yet (other-none)
        doctor.update(source="other", chosen="other", via=None)
        health.update(sourceKind="other", phone={"reachable": False})
        if name == "other-none":
            doctor.update(live=False, first="other-chosen", headline="Not live: no camera is chosen", items=[
                {"id": "other-chosen", "title": "A camera is chosen", "ok": False, "evidence": "none yet", "detail": "", "action": None,
                 "fix": "Choose a camera, or type its stream address, under Other camera on the camera page.", "problem": "no camera is chosen"}])
            health.update(ok=False)
    if name == "connect":  # the iPhone chosen: macOS finds it (its microphone) but doesn't offer its camera (live 2026-10-13)
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "iphone-camera-feed" / "scripts"))
        from camera_doctor import checklist

        doctor.clear()
        doctor.update(checklist({"source": "continuity", "devicePresent": False, "usbCable": False, "iphoneNearby": True,
                                 "readers": 1, "loadPerCore": 0.3}),
                      events=[{"at": "18:52:10", "text": "macOS finds the iPhone, but doesn't offer its camera"}],
                      report="Camera: continuity - not live")
        health.update(ok=False, sourceKind="continuity", phone={"reachable": False})
    if name == "none":  # No camera: pictures and videos only (2026-10-15), from the real checklist
        import sys
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "experiments" / "iphone-camera-feed" / "scripts"))
        from camera_doctor import checklist

        doctor.clear()
        doctor.update(checklist({"source": "none"}), events=[], report="Camera: none")
        health.update(ok=False, state="no_camera", sourceKind="none", phone={"reachable": False}, diagnosis=None)
    if name == "iphone":
        doctor.update(source="continuity", chosen="continuity", via=None)
        health.update(sourceKind="continuity", phone={"reachable": False})
    other = "USB Microscope" if name == "other" else None
    health["sourceChoices"] = [{"kind": "continuity", "title": "iPhone", "active": health["sourceKind"] == "continuity"},
                               {"kind": "blackmagic", "title": "Blackmagic Camera", "active": health["sourceKind"] == "blackmagic"},
                               {"kind": "other", "title": "Other camera", "active": health["sourceKind"] == "other", "device": other, "stream": False},
                               {"kind": "none", "title": "No camera", "active": health["sourceKind"] == "none"}]
    return {"phone": phone, "doctor": doctor, "health": health, "down": name == "down"}


class Handler(BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *args) -> None:
        pass

    def end_headers(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")  # the studio's Phone panel, run on its own dev server
        super().end_headers()

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Access-Control-Allow-Methods", "GET, POST")
        self.send_header("Access-Control-Allow-Headers", "X-MicroscopeMind")
        self.end_headers()

    def send(self, payload: bytes, kind: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def json(self, value: object, status: int = 200) -> None:
        self.send(json.dumps(value).encode(), "application/json", status)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path in ("/", "/camera"):
            self.send_response(302)
            self.send_header("Location", "/camera/")
            self.end_headers()
            return
        if path.startswith("/camera/"):
            target = (PAGE / (path[len("/camera/"):] or "camera.html")).resolve()
            if PAGE.resolve() in target.parents and target.is_file():
                return self.send(target.read_bytes(), mimetypes.guess_type(target.name)[0] or "application/octet-stream")
            return self.send(b"Build the page first: make camera-page", "text/plain", 404)
        if self.state["down"]:
            return self.json({"ok": False}, 503)
        s = self.state
        answers = {"/api/health": s["health"], "/api/doctor": s["doctor"], "/api/phone": s["phone"],
                   "/api/cadence": {"ago": [i * 33.3 + (40 if i % 9 == 0 else 0) for i in range(150)], "windowS": 5},
                   "/api/trials": {"trials": [{"title": "Blackmagic", "fps": 30.0, "p95Ms": 68.0, "gaps": 2, "startedAt": "2026-10-12T17:40:00"}]},
                   "/api/phone-check": {"running": False, "finished": True, "summary": ["Works from the Mac: focus, ISO, shutter."]},
                   "/api/cameras": {"cameras": [{"name": "FaceTime HD Camera", "iphone": False, "chosen": False},
                                                {"name": "USB Microscope", "iphone": False, "chosen": False},
                                                {"name": "iPhoneWild Camera", "iphone": True, "chosen": False}]}}
        if path in answers:
            return self.json(answers[path])
        if path in ("/api/settings-qr.svg", "/api/app-store-qr.svg"):
            return self.send(b"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 10 10'><rect width='10' height='10' fill='#fff'/>"
                             b"<rect x='1' y='1' width='3' height='3'/><rect x='6' y='1' width='3' height='3'/><rect x='1' y='6' width='3' height='3'/></svg>",
                             "image/svg+xml")
        self.json({"ok": False, "error": "not found"}, 404)

    def do_POST(self) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        phone = self.state["phone"]
        if url.path == "/api/photo":
            return self.json({"ok": True, "name": "Blackmagic-Camera-20261012-180542.jpg", "path": "/tmp/x.jpg"})
        if url.path == "/api/phone":
            action, value = query.get("do"), query.get("value")
            on = value in ("1", "true", "on")
            updates = {"record": {"recording": on, "clipName": "A001_C004" if on else None}, "lens": {"focalLength": int(float(value or 0)), "zoom": 0.0},
                       "zoom": {"zoom": float(value or 0)}, "focus": {"focus": float(value or 0)}, "autofocus": {"autofocus": on},
                       "exposure": {"autoExposure": "Continuous" if on else "Off"}, "iso": {"iso": int(float(value or 0))},
                       "shutter": {"shutter": int(float(value or 0))}, "white-balance": {"whiteBalance": int(float(value or 0))},
                       "white-balance-auto": {"whiteBalance": 5350, "tint": 3}, "tint": {"tint": int(float(value or 0))},
                       "fps": {"fps": f"{value}.00"}, "stabilization": {"stabilization": on},
                       "lock": {"autofocus": False, "autoExposure": "Off", "fps": "30.00"}}
            phone.update(updates.get(action, {}))
            return self.json({"ok": True, "phone": phone})
        if url.path == "/api/restart":  # a reconnect or a reset takes about a second on the real bridge
            import time
            time.sleep(1.2)
            return self.json({"ok": True})
        if url.path == "/api/source":
            kind = query.get("kind")
            if kind == "other":
                if not (query.get("device") or query.get("url") or self.state["health"]["sourceChoices"][2]["device"]):
                    return self.json({"ok": False, "error": "Choose a camera or a stream address for Other camera (on the camera page)"}, 400)
                self.state.update(make_state("other"))
                if query.get("device"):
                    self.state["health"]["sourceChoices"][2]["device"] = query["device"]
            else:
                self.state.update(make_state({"continuity": "iphone", "none": "none"}.get(kind, "live")))
            return self.json({"ok": True, "bridge": self.state["health"]})
        self.json({"ok": True})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", type=int, default=8897)
    parser.add_argument("--state", default="live", choices=("live", "busy", "fresh", "setup", "connect", "iphone", "other", "other-none", "none", "down"))
    args = parser.parse_args()
    Handler.state = make_state(args.state)
    print(f"Stand-in camera bridge: http://127.0.0.1:{args.port}/camera/ ({args.state})", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
