"""Blackmagic Camera controlled from the bridge (experiments/iphone-camera-feed/scripts/phone_control.py).

A stand-in phone serves the REST API as Blackmagic Camera 3.5 did on 2026-10-11 (the calls the module uses,
with the app's own quirks: a partial /system/format is refused, a stream started only from a server in the
platform file). The bridge's three sources run against it: the phone is aimed and started for a Blackmagic
source, stopped for the iPhone, Wi-Fi and USB switch without restarting the capture, and each source says
what to do when there is no picture.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tests.test_bridge import SILENT, bridge_module, cleanup, make_bridge, serve  # noqa: F401
from tests.waiting import HANG_S


class StandInPhone:
    """Blackmagic Camera's control API, in memory."""

    def __init__(self) -> None:
        self.state = {
            "/system/product": {"deviceName": "A", "productName": "iPhone 17 Pro", "softwareVersion": "3.5.200045"},
            "/camera/power": {"batteries": [{"chargeRemainingPercent": 64}]},
            "/system/format": {"frameRate": "24", "codec": "HEVC (H.265):High", "recordResolution": {"width": 1214, "height": 2160},
                               "resolutionDescriptor": {"group": "4K"}},
            "/lens/focus/autoFocus": {"mode": "Continuous", "enabled": True, "state": "Idle"},
            "/lens/focus": {"normalised": 0.4},
            "/video/autoExposure": {"mode": "Continuous", "type": "Shutter,ISO"},
            "/video/iso": {"iso": 210},
            "/video/supportedISOs": {"supportedISOs": [100, 200, 400, 800]},
            "/video/shutter": {"shutterSpeed": 60},
            "/video/supportedShutters": {"shutterSpeeds": [30, 60, 120]},
            "/video/whiteBalance": {"whiteBalance": 6270},
            "/video/whiteBalanceTint": {"whiteBalanceTint": -6},
            "/livestreams/0": {"status": "Idle"},
            "/livestreams/0/activePlatform": {"platform": "Blackmagic Cloud BMC", "server": "Default", "quality": "Streaming High"},
            # As the real app answered on 2026-10-12, recording on the phone.
            "/transports/0/record": {"recording": True, "clipName": "A001_10120201_C003"},
            "/transports/0/timecode": {"display": "00:10:04:02", "timeline": "00:10:04:02"},
            "/media/workingset": {"workingset": [{"remainingRecordTime": 36441, "clipCount": 3, "remainingSpace": 191315197952,
                                                  "volume": "iPhone 17 Pro", "deviceName": "local", "index": 0}], "size": 1},
            "/lens/zoom": {"focalLength": 48, "normalised": 0},
        }
        # As imported on 2026-10-11 (one server); a new import lists both links.
        self.platform_servers: list[dict] = [{"server": "This Mac", "url": "srt://192.168.50.109:9710"}]
        self.puts: list[tuple[str, object]] = []
        # A write that "crashes" the app: it stops answering, as Blackmagic Camera did, until it is opened again.
        self.crash_on: set[str] = set()  # writes that crash it
        self.crash_on_read: set[str] = set()  # reads that crash it
        self.dead = False
        self.crashed_at: list[float] = []
        self.lenses = {13, 24, 48}  # a focal length the app has no lens for is ignored

    def get(self, path: str):
        if path in self.crash_on_read:  # reading it "crashes" the app, as the colour settings did (2026-10-12)
            self._crash()
            return 200, {}
        if path == "/livestreams/platforms/MicroscopeMind%20SRT":
            return 200, {"platform": "MicroscopeMind SRT", "servers": self.platform_servers} if self.platform_servers else None
        if path in self.state:
            return 200, self.state[path]
        return 404, {"code": "not_found"}

    def _crash(self) -> None:
        self.dead = True
        self.crashed_at.append(float(int(time.time())))  # named to the second, as the phone's reports are
        threading.Timer(0.3, lambda: setattr(self, "dead", False)).start()  # opened again a moment later

    def put(self, path: str, body: bytes):
        text = body.decode()
        value = json.loads(text) if text.strip().startswith("{") else text
        self.puts.append((path, value))
        if path in self.crash_on:
            self._crash()
            return 204, None
        if path == "/system/format":
            if "codec" not in value:
                return 500, {"message": "Unhandled server error", "details": "The data couldn’t be read because it is missing."}
            self.state[path] = {**self.state[path], **value}
        elif path == "/video/whiteBalance/doAuto":  # set once from the view, as the real app does (a trigger, no value)
            self.state["/video/whiteBalance"] = {"whiteBalance": 5350}
            self.state["/video/whiteBalanceTint"] = {"whiteBalanceTint": 3}
        elif path == "/lens/zoom" and "focalLength" in value:
            if value["focalLength"] in self.lenses:
                self.state[path] = {"focalLength": value["focalLength"], "normalised": 0}
        elif path == "/lens/focus/autoFocus":
            self.state[path] = {**self.state[path], "enabled": value["enabled"], "mode": "Continuous" if value["enabled"] else "OneShot"}
        elif path == "/video/autoExposure":
            self.state[path] = value
        elif path.startswith("/livestreams/customPlatforms/"):
            return 204, None  # accepted and ignored, as the real app did (it reads files when they are imported)
        elif path == "/livestreams/0/activePlatform":
            if value.get("server") not in {server["server"] for server in self.platform_servers}:
                self.state["/livestreams/0"] = {"status": "Interrupted"}  # what the real app's stream did
                return 204, None
            self.state[path] = value
        elif path == "/livestreams/0/start":
            self.state["/livestreams/0"] = {"status": "Streaming"}
        elif path == "/livestreams/0/stop":
            self.state["/livestreams/0"] = {"status": "Idle"}
        elif path in self.state:
            self.state[path] = {**self.state[path], **value}
        return 204, None


@pytest.fixture()
def phone_server():
    phone = StandInPhone()

    connections: set = set()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"  # keep-alive, as the real app's server

        def log_message(self, *args):
            pass

        def setup(self):
            super().setup()
            connections.add(self.client_address)

        def _answer(self, status, value):
            payload = b"" if value is None else json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            phone.gets = getattr(phone, "gets", 0) + (self.command == "GET")

        def do_GET(self):
            if phone.dead:
                self.close_connection = True
                self.connection.close()
                return
            self._answer(*phone.get(self.path.removeprefix("/control/api/v1")))

        def do_PUT(self):
            if phone.dead:
                self.close_connection = True
                self.connection.close()
                return
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._answer(*phone.put(self.path.removeprefix("/control/api/v1"), body))

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    phone.base = f"http://127.0.0.1:{server.server_address[1]}"
    phone.connections = connections
    yield phone
    server.shutdown()


@pytest.fixture()
def control(monkeypatch):
    from tests.test_bridge import SCRIPTS

    monkeypatch.syspath_prepend(str(SCRIPTS))
    import importlib

    import phone_control

    return importlib.reload(phone_control)


ADDRESSES = {"wifi": "srt://192.168.50.109:9710", "usb": "srt://172.20.10.2:9710"}
WIFI_ONLY = {"wifi": ADDRESSES["wifi"], "usb": None}  # no cable: Blackmagic streams over Wi-Fi


def test_lock_for_microscope_holds_focus_exposure_and_30_fps(control, phone_server):
    phone = control.Phone(phone_server.base)
    changed = phone.lock_for_microscope()
    assert changed == ["autofocus off", "auto exposure off", "30 fps"]
    status = phone.status()
    assert status["autofocus"] is False and status["autoExposure"] == "Off" and status["fps"] == "30"
    assert phone.locked(status)
    form = [body for path, body in phone_server.puts if path == "/system/format"][0]
    assert form["codec"] and "resolutionDescriptor" not in form  # the whole format back, as the app needs
    assert phone.lock_for_microscope() == []  # already locked: nothing sent again


def test_the_phone_is_aimed_only_at_a_server_it_lists(control, phone_server):
    phone = control.Phone(phone_server.base)
    # Over Wi-Fi: "This Mac" is the server whose address is this Mac's on Wi-Fi.
    assert phone.stream_to("wifi", ADDRESSES) == ["streaming to This Mac", "stream started"]
    assert phone_server.state["/livestreams/0"]["status"] == "Streaming"
    assert phone.stream_to("wifi", ADDRESSES) == []  # aimed and streaming: nothing sent
    # Over USB: the app has no server for it yet: nothing sent, the settings file must be imported.
    sent = len(phone_server.puts)
    with pytest.raises(control.NeedsSettings):
        phone.stream_to("usb", ADDRESSES)
    assert len(phone_server.puts) == sent
    # After importing the file (both links), USB is selected by its address.
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]},
                                     {"server": "This Mac (USB)", "url": ADDRESSES["usb"]}]
    # streaming over Wi-Fi, USB chosen: the server changes and the stream restarts once on it
    assert phone.stream_to("usb", ADDRESSES) == ["streaming to This Mac (USB)", "stream started"]
    assert phone_server.state["/livestreams/0/activePlatform"]["server"] == "This Mac (USB)"
    servers = {body.get("server") for path, body in phone_server.puts if path == "/livestreams/0/activePlatform"}
    assert servers <= {"This Mac", "This Mac (USB)"}  # never "Custom", never a name the app doesn't list
    assert not any(path.startswith("/livestreams/customPlatforms") for path, _ in phone_server.puts)
    assert [path for path, _ in phone_server.puts].count("/livestreams/0/stop") == 1  # only for the server change
    with pytest.raises(control.PhoneError):
        phone.stream_to("usb", {"wifi": ADDRESSES["wifi"], "usb": None})  # no USB address: refused


def test_the_settings_file_lists_each_link_with_an_address(control):
    from xml.dom.minidom import parseString

    text = control.platform_xml(ADDRESSES)
    assert "<name>This Mac (Wi-Fi)</name>" in text and "srt://172.20.10.2:9710" in text
    # one import covers both links: USB listed at the hotspot's first address while the cable link is down
    assert "srt://172.20.10.2:9710" in control.platform_xml({"wifi": ADDRESSES["wifi"], "usb": None})
    # well-formed even with a query in the URL ("&" escaped: the app refuses a file that doesn't parse)
    assert parseString(control.platform_xml({"wifi": "srt://h:1?a=1&b=2", "usb": None})).documentElement.tagName == "streaming"


def test_the_usb_link_and_bonjour_are_read_from_the_mac(control, monkeypatch):
    ifconfig = "en0: flags=...\n\tinet 192.168.50.109 netmask 0xffffff00\nen7: flags=...\n\tinet 172.20.10.2 netmask 0xfffffff0\n"
    monkeypatch.setattr(control.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout=ifconfig, returncode=0))
    assert control.usb_address() == "172.20.10.2"
    monkeypatch.setattr(control.subprocess, "run", lambda *a, **k: SimpleNamespace(stdout="\tinet 192.168.50.109\n", returncode=0))
    assert control.usb_address() is None

    said = {
        ("dns-sd", "-B"): "15:08:10.393  Add        2  14 local.               _http._tcp.          21FDEF01-CC83\n",
        ("dns-sd", "-L"): ("21FDEF01-CC83._http._tcp.local. can be reached at iPhoneWild.local.:4444 (interface 14)\n"
                           " connected\\ device= capabilities=cameraControl model=iPhone\\ 17\\ Pro path=/control/api/v1\n"),
    }
    monkeypatch.setattr(control, "_run_for", lambda command, seconds: said[tuple(command[:2])])
    assert control.discover() == [{"name": "21FDEF01-CC83", "host": "iPhoneWild.local", "port": "4444",
                                   "base": "https://iPhoneWild.local:4444", "model": "iPhone 17 Pro"}]


def test_the_link_gets_the_picture_live_and_steps_aside_for_the_iphone(control, phone_server):
    wanted = {"kind": "blackmagic", "live": False}
    addresses = dict(WIFI_ONLY)
    link = control.PhoneLink(lambda: wanted["kind"], lambda: dict(addresses), lambda: wanted["live"], log=lambda line: None)
    link.phone = control.Phone(phone_server.base)
    link.tick(now=100.0)  # Blackmagic over Wi-Fi, no picture: aimed and started
    assert phone_server.state["/livestreams/0"]["status"] == "Streaming"
    assert "stream started" in link.last_action["action"]

    puts = len(phone_server.puts)
    wanted["live"] = True
    link.tick(now=101.0)
    link.tick(now=103.0)
    assert len(phone_server.puts) == puts  # live: the phone is left alone
    wanted["live"] = False
    link.tick(now=104.0)
    link.tick(now=130.0)  # no picture for a while, aimed right and streaming: never stopped and restarted
    assert len(phone_server.puts) == puts

    addresses["usb"] = ADDRESSES["usb"]  # a cable the app has no server for: Wi-Fi stays, no setup problem
    link.tick(now=131.0)
    assert not link.snapshot()["needsSettings"] and phone_server.state["/livestreams/0"]["status"] == "Streaming"

    wanted["kind"] = "continuity"
    link.source_changed()
    link.tick(now=140.0)  # the iPhone chosen: the phone's stream stops, once, so the phone can be locked
    assert phone_server.state["/livestreams/0"]["status"] == "Idle"
    link.tick(now=142.0)
    assert [p for p, _ in phone_server.puts].count("/livestreams/0/stop") == 1


def test_a_switch_during_a_conversation_never_starts_the_phone(control, phone_server):
    """Live 2026-10-13: the iPhone chosen while the link was talking to the phone; 2 s later it started Blackmagic's
    stream over Wi-Fi anyway, and the app kept the camera Continuity Camera needed."""
    wanted = {"kind": "blackmagic"}
    link = control.PhoneLink(lambda: wanted["kind"], lambda: dict(WIFI_ONLY), lambda: False, log=lambda line: None)
    link.phone = control.Phone(phone_server.base)
    phone_server.state["/livestreams/0"]["status"] = "Idle"
    wanted["kind"] = "continuity"  # chosen after this tick read the source, before it aimed the phone
    link._aim("wifi", 100.0)
    assert phone_server.state["/livestreams/0"]["status"] == "Idle"
    assert not [p for p, _ in phone_server.puts if p.startswith("/livestreams")]


def test_the_old_wifi_and_usb_choices_are_blackmagic_and_keep_the_capture(bridge_module, tmp_path, cleanup):
    """Until 2026-10-12 "wifi" and "usb" pinned a link; a saved choice or make camera-source of either is Blackmagic,
    which picks its link by itself, and choosing it again never restarts the capture."""
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge.args.source_file = str(tmp_path / "camera-source.json")
    bridge_module.switch_source(bridge.args, bridge, "wifi")
    assert bridge.health()["sourceKind"] == "blackmagic"
    pid, requests = bridge.health()["pid"], bridge.health()["requestCount"]
    state = bridge_module.switch_source(bridge.args, bridge, "usb")
    health = bridge.health()
    assert state["kept"] and health["pid"] == pid and health["requestCount"] == requests
    assert health["sourceKind"] == "blackmagic" and health["targetDevice"] == "Blackmagic Camera"
    state = bridge_module.switch_source(bridge.args, bridge, "continuity")
    assert not state["kept"] and bridge.health()["pid"] != pid and bridge.health()["targetDevice"] == "iPhoneWild Camera"


def test_each_source_says_how_to_get_live(bridge_module, tmp_path, cleanup, monkeypatch):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, silent_after=0.1)
    cleanup.append(bridge)
    bridge.args.source_file = str(tmp_path / "camera-source.json")
    snapshot = {"reachable": False, "addresses": {"wifi": "srt://192.168.50.109:9710", "usb": None}}
    bridge.phone_link = SimpleNamespace(snapshot=lambda: snapshot, source_changed=lambda: None, stop=lambda: None)

    bridge_module.switch_source(bridge.args, bridge, "blackmagic")
    diagnosis = bridge.health()["diagnosis"]
    assert diagnosis["code"] == "phone-app-unreachable" and diagnosis["settingsFile"] == "/api/blackmagic.xml"
    assert any("HTTP Server" in step for step in diagnosis["steps"])

    snapshot.update(reachable=True, streaming=False, error="")
    assert bridge.health()["diagnosis"]["code"] == "stream-starting"
    snapshot.update(streaming=True, server="This Mac (Wi-Fi)")
    assert bridge.health()["diagnosis"]["code"] == "stream-not-arriving"

    bridge_module.switch_source(bridge.args, bridge, "continuity")
    time.sleep(0.2)
    steps = bridge.health()["diagnosis"]["steps"]
    assert any(step.startswith("Close Blackmagic Camera and lock the iPhone") for step in steps)


def test_the_console_endpoints(bridge_module, tmp_path, cleanup, monkeypatch):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    monkeypatch.setattr(bridge_module, "stream_addresses", lambda args: ADDRESSES)
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/api/blackmagic.xml", timeout=5) as response:
            assert response.headers["Content-Disposition"].startswith("attachment")
            assert b"This Mac (USB)" in response.read()
        request = urllib.request.Request(f"http://127.0.0.1:{ports[0]}/api/phone?do=lock", method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)
        assert refused.value.code == 403
    finally:
        server.shutdown()


def test_the_phone_is_asked_over_one_kept_alive_connection_and_lightly_while_live(control, phone_server):
    phone = control.Phone(phone_server.base)
    full = phone.status()
    assert full["reachable"] and len(phone_server.connections) == 1  # a dozen reads, one connection
    phone_server.gets = 0
    light = phone.status(full=False, previous=full)
    # The stream, focus and exposure held, and the phone's recording; the rest kept from the last full read.
    assert phone_server.gets <= 6 and light["battery"] == full["battery"]
    phone.close()
    phone.status(full=False, previous=full)
    assert len(phone_server.connections) == 2  # closed, opened again


def test_the_quality_is_never_changed_through_the_api(control, phone_server):
    """Selecting a quality through the API stopped the real app's HTTP server (2026-10-11): it stays the app's."""
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]},
                                     {"server": "This Mac (USB)", "url": ADDRESSES["usb"]}]
    phone_server.state["/livestreams/0/activePlatform"] = {"platform": "MicroscopeMind SRT", "server": "This Mac (USB)",
                                                           "quality": "Streaming Medium"}
    control.Phone(phone_server.base).stream_to("wifi", ADDRESSES)
    sent = [body for path, body in phone_server.puts if path == "/livestreams/0/activePlatform"]
    assert sent and all(body["quality"] == "Streaming Medium" for body in sent)

def test_a_test_bridge_never_reaches_for_the_phone(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, no_phone=True)
    cleanup.append(bridge)
    assert bridge.ensure_phone_link() is None and bridge.phone_link is None
    bridge.args.no_phone, bridge.args.simulate = False, "footage.mp4"
    assert bridge.ensure_phone_link() is None  # a simulation bridge either
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}/api/phone", timeout=5) as response:
            assert json.loads(response.read()) == {"reachable": False, "disabled": True}
    finally:
        server.shutdown()


def test_unplugging_the_cable_moves_the_phone_to_wifi_even_while_live(control, phone_server):
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]},
                                     {"server": "This Mac (USB)", "url": ADDRESSES["usb"]}]
    wanted = {"live": False}
    addresses = dict(ADDRESSES)
    link = control.PhoneLink(lambda: "blackmagic", lambda: dict(addresses), lambda: wanted["live"], log=lambda line: None)
    link.phone = control.Phone(phone_server.base)
    link.tick(now=100.0)
    assert phone_server.state["/livestreams/0/activePlatform"]["server"] == "This Mac (USB)"
    wanted["live"] = True  # frames flowing over the cable
    addresses["usb"] = None  # and the cable is unplugged
    link.tick(now=101.0)
    assert phone_server.state["/livestreams/0/activePlatform"]["server"] == "This Mac (Wi-Fi)"
    puts = len(phone_server.puts)
    link.tick(now=102.0)  # once: live, aimed, left alone
    assert len(phone_server.puts) == puts


def test_a_server_change_restarts_the_phone_stream_once_and_a_silent_stream_gets_a_fresh_listener(control, phone_server):
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]},
                                     {"server": "This Mac (USB)", "url": ADDRESSES["usb"]}]
    phone = control.Phone(phone_server.base)
    phone.stream_to("usb", ADDRESSES)
    stops = [p for p, _ in phone_server.puts].count("/livestreams/0/stop")
    phone.stream_to("wifi", ADDRESSES)  # streaming over USB, Wi-Fi chosen: one stop, one start
    assert [p for p, _ in phone_server.puts].count("/livestreams/0/stop") == stops + 1
    assert phone_server.state["/livestreams/0"]["status"] == "Streaming"
    assert phone.stream_to("wifi", ADDRESSES) == []  # aimed and streaming: nothing more

    restarts = []
    wanted = {"kind": "blackmagic", "live": False}
    link = control.PhoneLink(lambda: wanted["kind"], lambda: WIFI_ONLY, lambda: wanted["live"], log=lambda line: None,
                             restart_listener=lambda: restarts.append(1))
    link.phone = phone
    link.tick(now=100.0)  # aimed and streaming, nothing here yet: its grace begins (live 2026-10-12 09:41: a listener
    link.tick(now=113.0)  # replaced at once cut the session before its first frame, and the phone never came back)
    assert restarts == []
    link.tick(now=126.0)  # streaming the whole grace, nothing arrives: a fresh listener, then the phone's stream again
    assert restarts == [1] and link.last_action["action"] == "listening again for the phone's stream"
    assert [p for p, _ in phone_server.puts].count("/livestreams/0/stop") == stops + 2  # restarted once, to call it
    assert phone_server.state["/livestreams/0"]["status"] == "Streaming"
    link.tick(now=139.0)
    link.tick(now=152.0)  # within the minute: not again
    assert restarts == [1]


def test_the_phone_link_never_holds_its_lock_while_the_bridge_is_called(control, phone_server):
    """Live 2026-10-12 01:17: the bridge froze. Its health took the bridge's lock and then the link's (snapshot),
    while the link, holding its own lock, restarted the listener, which takes the bridge's lock. The link's state is
    read without its lock, and the bridge is called back only once the link has let go of it."""
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]}]
    phone = control.Phone(phone_server.base)
    phone.stream_to("wifi", ADDRESSES)  # aimed and streaming: the next silent tick restarts the listener
    bridge_lock = threading.Lock()
    snapshots = []

    def restart_listener():  # the bridge's _launch: its own lock
        with bridge_lock:
            pass

    link = control.PhoneLink(lambda: "blackmagic", lambda: WIFI_ONLY, lambda: False, log=lambda line: None,
                             restart_listener=restart_listener)
    link.phone = phone
    link.streaming_since = 0.0  # streaming, with nothing arriving, for longer than its grace

    def health():  # the bridge's health: its own lock, then the link's state
        with bridge_lock:
            time.sleep(0.2)
            snapshots.append(link.snapshot())

    with link.lock:  # the link is talking to the phone
        reader = threading.Thread(target=health, daemon=True)
        reader.start()
        reader.join(timeout=2)
        assert snapshots, "the link's state must be readable while it talks to the phone"
    ticker = threading.Thread(target=lambda: link.tick(now=100.0), daemon=True)
    with bridge_lock:  # health is running while the link restarts the listener
        ticker.start()
        time.sleep(0.3)
        reader = threading.Thread(target=lambda: snapshots.append(link.snapshot()), daemon=True)
        reader.start()
        reader.join(timeout=2)
        assert len(snapshots) == 2, "the link held its lock while waiting for the bridge"
    ticker.join(timeout=2)
    assert not ticker.is_alive()


def test_a_stream_is_never_started_at_the_quality_that_crashes_the_app(control, phone_server):
    """"Microscope" crashed Blackmagic Camera 3.5.20 every time its stream started (2026-10-12): the bridge refuses
    to start it, and says what to choose instead."""
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]}]
    phone_server.state["/livestreams/0/activePlatform"] = {"platform": "MicroscopeMind SRT", "server": "This Mac (Wi-Fi)",
                                                           "quality": "Microscope"}
    phone = control.Phone(phone_server.base)
    with pytest.raises(control.PhoneError, match="choose Standard"):
        phone.stream_to("wifi", ADDRESSES)
    with pytest.raises(control.PhoneError, match="choose Standard"):
        phone.restart_stream()
    assert "/livestreams/0/start" not in [path for path, _ in phone_server.puts]


def test_a_crash_report_on_the_phone_is_told(control):
    """The phone's own crash reports (devicectl) say when Blackmagic Camera crashed; the link reads them while a
    Blackmagic source is chosen, never under its lock, and says so for ten minutes."""
    lines = []
    link = control.PhoneLink(lambda: "blackmagic", lambda: WIFI_ONLY, lambda: False, log=lines.append)
    crashed = time.time() - 5
    link.crash_times = lambda: [crashed - 3600, crashed]
    link._watch_crashes(now=1000.0)
    assert link.snapshot()["appCrashedAt"] == time.strftime("%H:%M:%S", time.localtime(crashed))
    assert lines and "crashed at" in lines[0]
    link._watch_crashes(now=1001.0)  # within the check's gap: not read again
    assert len(lines) == 1
    iphone = control.PhoneLink(lambda: "continuity", lambda: ADDRESSES, lambda: False, log=lines.append)
    iphone.crash_times = lambda: (_ for _ in ()).throw(AssertionError("not read for the iPhone source"))
    iphone._watch_crashes(now=1000.0)


def test_the_phone_status_says_what_it_is_recording(control, phone_server):
    """Record state, clip, timecode, storage and zoom are read (reads only), for the studio's Phone panel."""
    status = control.Phone(phone_server.base).status()
    assert status["recording"] is True and status["clipName"] == "A001_10120201_C003" and status["timecode"] == "00:10:04:02"
    assert status["clips"] == 3 and status["recordTimeLeftS"] == 36441 and status["focalLength"] == 48
    light = control.Phone(phone_server.base).status(full=False, previous=status)
    assert light["recording"] is True and light["timecode"] == "00:10:04:02"


def test_the_phone_check_proves_each_write_and_the_bridge_sends_only_those(control, phone_server, tmp_path, monkeypatch):
    """The Phone check (phone_lab.py): the reads the bridge knows taken together, every other read and every write
    tried one at a time with the app asked after it, a crash marked never to be tried again, and the manifest it
    writes is the bridge's allow-list."""
    import phone_lab

    phone_server.crash_on = {"/video/whiteBalanceTint"}
    phone_server.crash_on_read = {"/colorCorrection/gamma"}
    phone_server.state["/transports/0/record"] = {"recording": False, "clipName": None}
    phone_server.state["/colorCorrection/gamma"] = {"red": 0}
    manifest = tmp_path / "blackmagic-api.json"
    seed = json.loads(phone_lab.MANIFEST_PATH.read_text())
    seed["versions"].pop("3.5.200045", None)  # a phone never checked: everything is tried
    manifest.write_text(json.dumps(seed))
    old_report = float(int(time.time())) - 120  # a crash from two minutes ago, listed late by the phone

    def reports():
        listed = list(phone_server.crashed_at)
        if len(phone_server.puts) > 3:
            listed.append(old_report)  # appears in the middle of the run: not this step's
        return listed

    def check():
        return phone_lab.PhoneCheck(lambda: control.Phone(phone_server.base, unchecked=True), reports, manifest_path=manifest,
                                    sleep=lambda s: time.sleep(min(s, 0.05)), settle_s=0.0, reopen_wait_s=5.0)

    state = check().run()
    assert state["finished"] and not state["error"]
    proven = json.loads(manifest.read_text())["versions"]["3.5.200045"]
    writes, reads = proven["writes"], proven["reads"]
    assert proven["complete"] is True
    assert writes["/video/iso"]["safe"] is True and writes["/video/iso"]["changes"] is True  # not blamed for the old report
    assert writes["/video/whiteBalanceTint"]["safe"] is False  # it closed the app
    assert reads["/colorCorrection/gamma"] == "crashed" and reads["/video/ndFilter"] == "missing"
    assert writes["/video/ndFilter"]["safe"] is None  # this app has no ND
    assert writes["/transports/0/record"]["safe"] is True
    assert proven["lenses"] == [13, 24, 48]  # the lenses that switched; 100 and 200 mm didn't
    assert writes["/video/whiteBalance/doAuto"]["safe"] is True and writes["/video/whiteBalance/doAuto"]["changes"] is True
    assert phone_server.state["/video/whiteBalance"] == {"whiteBalance": 6270}  # what it was, put back
    assert phone_server.state["/video/whiteBalanceTint"] == {"whiteBalanceTint": 3}  # writing tint crashed it: left alone
    assert phone_server.state["/lens/zoom"]["focalLength"] == 48  # the lens in use, put back
    assert any("never read" in line and "gamma" in line for line in state["summary"])

    crashes = len(phone_server.crashed_at)
    again = check().run()  # carries on: nothing that crashed the app is tried again
    assert again["finished"] and len(phone_server.crashed_at) == crashes and again["total"] == 1

    monkeypatch.setattr(phone_lab, "MANIFEST_PATH", manifest)
    monkeypatch.setattr(phone_lab, "load_manifest", lambda path=manifest: json.loads(manifest.read_text()))
    phone = control.Phone(phone_server.base)
    phone.put("/video/iso", {"iso": 400})  # proven
    with pytest.raises(control.PhoneError, match="Phone check"):
        phone.put("/video/whiteBalanceTint", {"whiteBalanceTint": 3})  # crashed it: never sent
    phone_server.state["/system/product"]["softwareVersion"] = "3.6.0"  # the app updated
    updated = control.Phone(phone_server.base)
    with pytest.raises(control.PhoneError, match="3.6.0"):
        updated.put("/video/iso", {"iso": 200})
    updated.put("/livestreams/0/start")  # streaming itself keeps working after an update


def test_blackmagic_picks_its_link_and_moves_with_the_cable(control, phone_server):
    """One Blackmagic source: the cable when it shares a network, otherwise Wi-Fi, and the phone is moved by itself
    when the cable comes or goes (2026-10-12: nobody should have to choose, or know what Personal Hotspot is)."""
    assert control.link_for(WIFI_ONLY) == "wifi"
    assert control.link_for(ADDRESSES) == "usb"
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]},
                                     {"server": "This Mac (USB)", "url": ADDRESSES["usb"]}]
    addresses = {"wifi": ADDRESSES["wifi"], "usb": None}
    lines = []
    link = control.PhoneLink(lambda: "blackmagic", lambda: dict(addresses), lambda: False, log=lines.append)
    link.phone = control.Phone(phone_server.base)
    link.tick(now=100.0)
    assert phone_server.state["/livestreams/0/activePlatform"]["server"] == "This Mac (Wi-Fi)" and link.link == "wifi"
    addresses["usb"] = ADDRESSES["usb"]  # the cable is plugged in and shares its network
    link.tick(now=101.0)
    assert phone_server.state["/livestreams/0/activePlatform"]["server"] == "This Mac (USB)" and link.link == "usb"
    assert any("over the cable now" in line for line in lines)
    assert link.snapshot()["link"] == "usb"


def test_the_settings_file_uses_blackmagics_own_resolution_names(control):
    """Every profile of ours named its resolutions HD and 4K, and each crashed the app as its stream started;
    Blackmagic's own streaming files name them 2160p, 1080p and 720p."""
    import xml.etree.ElementTree as ET

    service = ET.fromstring(control.platform_xml(ADDRESSES)).find("service")
    (profile,) = service.find("profiles").findall("profile")
    assert profile.findtext("name") == "Standard" and profile.find("low-latency") is None
    assert {c.get("resolution") for c in profile.findall("config")} == {"2160p", "1080p", "720p"}
    assert all(c.findtext("keyframe-interval") == "2" for c in profile.findall("config"))


def test_a_quality_that_crashes_the_app_is_learned_and_never_started_again(control, phone_server, tmp_path, monkeypatch):
    """A crash report just after the bridge started the stream means that quality crashes the app: it is written to
    the manifest, the checklist names it, and the bridge never starts a stream at it again."""
    import shutil

    import phone_lab

    manifest = tmp_path / "blackmagic-api.json"
    shutil.copy(phone_lab.MANIFEST_PATH, manifest)
    monkeypatch.setattr(phone_lab, "MANIFEST_PATH", manifest)
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]}]
    phone_server.state["/livestreams/0/activePlatform"] = {"platform": "MicroscopeMind SRT", "server": "This Mac (Wi-Fi)",
                                                           "quality": "Standard"}
    crashes: list[float] = []
    lines = []
    link = control.PhoneLink(lambda: "blackmagic", lambda: {"wifi": ADDRESSES["wifi"], "usb": None}, lambda: False,
                             log=lines.append)
    link.crash_times = lambda: crashes
    link.phone = control.Phone(phone_server.base)
    link.tick(now=100.0)  # the bridge starts the stream at Standard
    assert link.started and link.started[0] == "Standard"
    crashes.append(float(int(time.time())))  # and the app crashes
    link._watch_crashes(now=1000.0)
    assert "Standard" in json.loads(manifest.read_text())["versions"]["3.5.200045"]["crashedQualities"]
    assert any("won't be used again" in line for line in lines)
    phone_server.state["/livestreams/0"] = {"status": "Idle"}
    with pytest.raises(control.PhoneError, match="crashes Blackmagic Camera"):
        control.Phone(phone_server.base).stream_to("wifi", {"wifi": ADDRESSES["wifi"], "usb": None})


def test_a_live_picture_clears_what_an_earlier_attempt_concluded(control):
    """2026-10-12: "MicroscopeMind isn't set up in the app" stayed over a live picture, left from a pinned USB link."""
    link = control.PhoneLink(lambda: "blackmagic", lambda: {"wifi": ADDRESSES["wifi"], "usb": None}, lambda: True,
                             log=lambda line: None)
    link.needs_settings, link.error = True, "Blackmagic Camera has no server for this Mac over USB"
    link.tick(now=100.0)
    assert link.snapshot()["needsSettings"] is False and link.snapshot()["error"] == ""


def test_the_settings_file_has_a_server_for_every_cable_address(control):
    """The phone's hotspot gives this Mac any of 172.20.10.2-14 over the cable: the file names them all (2026-10-12:
    it named .2, the Mac had .3)."""
    import xml.etree.ElementTree as ET

    servers = ET.fromstring(control.platform_xml({"wifi": ADDRESSES["wifi"], "usb": None})).find("service").find("servers")
    urls = [server.findtext("url") for server in servers]
    assert urls[0] == ADDRESSES["wifi"] and "srt://172.20.10.3:9710" in urls and "srt://172.20.10.14:9710" in urls
    assert len(urls) == 14


def test_a_cable_the_app_has_no_server_for_falls_back_to_wifi(control, phone_server):
    """Blackmagic picks the cable when it is up; when the app's settings have no server for the cable's address, it
    streams over Wi-Fi instead, and that is not shown as a setup problem."""
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": ADDRESSES["wifi"]},
                                     {"server": "This Mac (USB)", "url": "srt://172.20.10.2:9710"}]
    addresses = {"wifi": ADDRESSES["wifi"], "usb": "srt://172.20.10.3:9710"}
    lines = []
    link = control.PhoneLink(lambda: "blackmagic", lambda: dict(addresses), lambda: False, log=lines.append)
    link.phone = control.Phone(phone_server.base)
    link.tick(now=100.0)
    assert phone_server.state["/livestreams/0/activePlatform"]["server"] == "This Mac (Wi-Fi)"
    assert link.snapshot()["needsSettings"] is False and any("over Wi-Fi" in line for line in lines)
    link.tick(now=113.0)
    assert link.link == "wifi"  # it stays on Wi-Fi for a while
    phone_server.platform_servers.append({"server": "This Mac (USB .3)", "url": "srt://172.20.10.3:9710"})  # imported
    link.cable_without_server = (link.cable_without_server[0], time.monotonic() - 61)  # a minute later
    link.tick(now=126.0)
    assert link.link == "usb" and phone_server.state["/livestreams/0/activePlatform"]["server"] == "This Mac (USB .3)"


def test_the_phone_panel_records_zooms_switches_proven_lenses_and_sets_white_balance(control, phone_server, tmp_path, monkeypatch):
    """The studio's Phone panel (2026-10-12): record on the phone, zoom within a lens, stabilization, and a lens chosen
    only when the Phone check proved that lens switches from the Mac."""
    import shutil

    import phone_lab

    manifest = tmp_path / "blackmagic-api.json"
    shutil.copy(phone_lab.MANIFEST_PATH, manifest)
    monkeypatch.setattr(phone_lab, "load_manifest", lambda path=None: json.loads(manifest.read_text()))
    phone_server.state["/lens/opticalImageStabilization"] = {"enabled": True}
    link = control.PhoneLink(lambda: "blackmagic", lambda: WIFI_ONLY, lambda: True, log=lambda line: None)
    link.phone = control.Phone(phone_server.base)

    data = json.loads(manifest.read_text())
    data["versions"]["3.5.200045"].pop("lenses", None)
    manifest.write_text(json.dumps(data))
    with pytest.raises(control.PhoneError, match="lens test"):
        link.act("lens", "24")  # not proven yet: nothing sent
    assert not any(path == "/lens/zoom" for path, _ in phone_server.puts)

    data["versions"]["3.5.200045"]["lenses"] = [13, 24, 48]
    manifest.write_text(json.dumps(data))
    state = link.act("lens", "24")
    assert state["focalLength"] == 24 and state["lenses"] == [13, 24, 48]
    assert link.act("zoom", "0.5")["zoom"] == 0.5
    assert link.act("stabilization", "off")["stabilization"] is False
    phone_server.state["/transports/0/record"] = {"recording": False, "clipName": None}
    assert link.act("record", "on")["recording"] is True
    assert link.act("record", "off")["recording"] is False

    data["versions"]["3.5.200045"]["writes"].pop("/video/whiteBalance/doAuto", None)
    manifest.write_text(json.dumps(data))
    assert link.snapshot()["autoWhiteBalance"] is False
    with pytest.raises(control.PhoneError, match="Phone check"):
        link.act("white-balance-auto")  # not proven: nothing sent
    data["versions"]["3.5.200045"]["writes"]["/video/whiteBalance/doAuto"] = {"safe": True, "byCheck": True}
    manifest.write_text(json.dumps(data))
    state = link.act("white-balance-auto")
    assert state["autoWhiteBalance"] is True and (state["whiteBalance"], state["tint"]) == (5350, 3)


def test_the_photo_button_saves_the_newest_frame_as_it_arrived(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge.args.recording_dir = str(tmp_path / "recordings")
    assert bridge.take_photo()["ok"] is False  # no picture yet: said, nothing saved
    frame = b"\xff\xd8\xff\xc0\x00\x11\x08\x02\xd0\x05\x00\x03" + bytes(40) + b"\xff\xd9"
    with bridge.condition:
        bridge.latest_frame, bridge.last_frame_at = frame, time.monotonic()
    photo = bridge.take_photo()
    assert photo["ok"] and (photo["width"], photo["height"]) == (1280, 720)
    saved = tmp_path / "recordings" / photo["name"]
    assert saved.read_bytes() == frame  # exactly as it arrived, never re-encoded
    assert bridge.take_photo()["name"] != photo["name"]  # a second in the same second gets its own name


@pytest.mark.skipif(not __import__("tests.test_camera_link", fromlist=["ffmpeg_has_srt"]).ffmpeg_has_srt(), reason="FFmpeg without SRT")
def test_blackmagic_end_to_end_the_phone_is_aimed_its_stream_arrives_and_the_checklist_says_live(
        control, phone_server, bridge_module, tmp_path, cleanup):
    """The whole Blackmagic path on one Mac: the bridge listens, the phone link finds the stand-in phone, aims its stream
    at this Mac and starts it; the stand-in then streams as the app does (a real SRT stream into the real listener),
    the picture arrives, and the checklist the camera page shows reads live, every check passing."""
    from tests.test_camera_link import free_udp_port, push_stream, stream_args

    port = free_udp_port()
    addresses = {"wifi": f"srt://127.0.0.1:{port}", "usb": None}
    phone_server.platform_servers = [{"server": "This Mac (Wi-Fi)", "url": addresses["wifi"]}]
    pushers = []
    real_put = phone_server.put

    def put(path, body):  # the app starting its stream: it calls the server it was aimed at
        answer = real_put(path, body)
        if path == "/livestreams/0/start" and not pushers:
            pushers.append(push_stream(port))
        return answer

    phone_server.put = put
    bridge = make_bridge(bridge_module, tmp_path, SILENT, stall_timeout=2.0)
    cleanup.append(bridge)
    bridge_module.use_live_source(stream_args(bridge, tmp_path), "blackmagic", f"srt://127.0.0.1:{port}?mode=listener&latency=50000")
    del bridge.ffmpeg_command  # the real capture command: FFmpeg listening for the stream
    link = control.PhoneLink(lambda: bridge_module.live_source_kind(bridge.args), lambda: dict(addresses), bridge.frames_flowing,
                             log=bridge.event)
    link.phone = control.Phone(phone_server.base)
    bridge.phone_link = link
    bridge._launch("test", replace=False)
    try:
        time.sleep(0.5)
        link.tick(now=100.0)  # no picture: aimed at this Mac over Wi-Fi and started
        assert phone_server.state["/livestreams/0"]["status"] == "Streaming" and pushers
        _, frame = bridge.wait_for_frame(0, timeout=15)
        assert frame and frame.startswith(b"\xff\xd8")
        deadline = time.monotonic() + HANG_S
        doctor = bridge.doctor()
        clock = 100.0
        while time.monotonic() < deadline and not doctor["live"]:
            time.sleep(0.3)
            clock += 11.0  # the link reads the phone again (every 10 s while the picture flows)
            link.tick(now=clock)
            doctor = bridge.doctor()
        # Nothing is reading the feed in a test (no studio), and the Mac's load is the test machine's.
        failing = [(item["id"], item["evidence"]) for item in doctor["items"]
                   if item["ok"] is False and item["id"] not in ("room", "app-reading")]
        assert doctor["live"] and doctor["via"] == "Wi-Fi" and not failing, (doctor["headline"], failing)
    finally:
        for pusher in pushers:
            pusher.kill()


def test_the_phones_controls_over_http_need_their_header(control, phone_server, bridge_module, tmp_path, cleanup):
    """The camera page's and the Phone panel's calls, as they make them: every write needs its header (another site
    can't press it), a control reaches the phone, a refused one says why, and Photo saves the newest frame."""
    import shutil

    import phone_lab

    manifest = tmp_path / "blackmagic-api.json"
    shutil.copy(phone_lab.MANIFEST_PATH, manifest)
    original = phone_lab.load_manifest
    phone_lab.load_manifest = lambda path=None: json.loads(manifest.read_text())
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge.args.recording_dir = str(tmp_path / "recordings")
    link = control.PhoneLink(lambda: "blackmagic", lambda: WIFI_ONLY, lambda: True, log=lambda line: None)
    link.phone = control.Phone(phone_server.base)
    bridge.phone_link = link
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)
    base = f"http://127.0.0.1:{ports[0]}"

    def call(path: str, method: str = "POST", header: str | None = None):
        request = urllib.request.Request(base + path, method=method, headers={"X-MicroscopeMind": header} if header else {})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    try:
        for path in ("/api/phone?do=zoom&value=0.5", "/api/photo", "/api/phone-check", "/api/restart?reset=1"):
            status, _, body = call(path)
            assert status == 403 and json.loads(body)["ok"] is False, path  # no header: refused, and said why
        status, _, body = call("/api/phone?do=zoom&value=0.5", header="camera-control")
        assert status == 200 and json.loads(body)["phone"]["zoom"] == 0.5
        status, _, body = call("/api/phone?do=lens&value=13", header="camera-control")
        assert status == 502 and "lens" in json.loads(body)["error"]  # not a proven lens: nothing sent
        status, _, body = call("/api/photo", header="camera-control")
        assert status == 409 and "no live picture" in json.loads(body)["error"]
        frame = b"\xff\xd8\xff\xc0\x00\x11\x08\x02\xd0\x05\x00\x03" + bytes(40) + b"\xff\xd9"
        with bridge.condition:
            bridge.latest_frame, bridge.last_frame_at = frame, time.monotonic()
        status, _, body = call("/api/photo", header="camera-control")
        assert status == 200 and (tmp_path / "recordings" / json.loads(body)["name"]).read_bytes() == frame
        status, _, body = call("/api/phone-check", method="GET")
        assert status == 200 and "running" in json.loads(body)
        status, headers, body = call("/api/settings-qr.svg", method="GET")
        assert status == 200 and headers["Content-Type"].startswith("image/svg+xml") and body.startswith(b"<svg")
        status, headers, body = call("/api/app-store-qr.svg", method="GET")  # the setup's first step: get the app
        assert status == 200 and headers["Content-Type"].startswith("image/svg+xml") and body.startswith(b"<svg")
    finally:
        server.shutdown()
        phone_lab.load_manifest = original


def test_choosing_another_camera_stops_the_phones_stream_once(control, phone_server):
    """With the Other camera chosen, Blackmagic Camera doesn't keep streaming to a listener nobody runs."""
    wanted = {"kind": "blackmagic"}
    link = control.PhoneLink(lambda: wanted["kind"], lambda: WIFI_ONLY, lambda: False, log=lambda line: None)
    link.phone = control.Phone(phone_server.base)
    link.tick(now=100.0)  # Blackmagic: aimed and streaming
    link.tick(now=103.0)  # and read back as streaming
    assert link.snapshot()["streaming"] is True
    wanted["kind"] = "other"
    link.source_changed()
    link.tick(now=110.0)
    link.tick(now=113.0)
    assert phone_server.state["/livestreams/0"]["status"] == "Idle"
    assert [p for p, _ in phone_server.puts].count("/livestreams/0/stop") == 1


def test_a_saved_photo_is_shown_in_the_finder_and_nothing_else_is(bridge_module, tmp_path, cleanup, monkeypatch):
    """"Where did they save?" (2026-10-13): the page's Show in Finder reveals a photo of the photo folder by its name, and
    nothing outside it."""
    opened = []
    monkeypatch.setattr(bridge_module.subprocess, "run", lambda command, **kwargs: opened.append(command))
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge.args.recording_dir = str(tmp_path / "recordings")
    frame = b"\xff\xd8\xff\xc0\x00\x11\x08\x02\xd0\x05\x00\x03" + bytes(40) + b"\xff\xd9"
    with bridge.condition:
        bridge.latest_frame, bridge.last_frame_at = frame, time.monotonic()
    name = bridge.take_photo()["name"]
    assert bridge.reveal_photo(name)["ok"] and opened[-1][:2] == ["open", "-R"]
    for other in ("../secret.jpg", "x.mp4", "missing.jpg"):
        assert not bridge.reveal_photo(other)["ok"], other
    assert len(opened) == 1


def test_a_phone_that_stops_answering_never_holds_the_link(control, phone_server, wall_clock):
    """Live 2026-10-13: the app went quiet mid-recording; a status read asked it a dozen questions, each waiting out its
    timeouts, and Stop recording waited behind them for over a minute. Now the read stops at the first unanswered
    question, the phone isn't asked again for a moment, and a control waits a bounded time, then says so."""
    phone = control.Phone(phone_server.base)
    phone.status()  # answering
    phone_server.dead = True
    started = time.monotonic()
    assert phone.status()["reachable"] is False
    wall_clock.within(time.monotonic() - started, 2.0, "the status read")  # one question, not a dozen
    started = time.monotonic()
    with pytest.raises(control.PhoneError, match="a moment ago"):
        phone.get("/lens/zoom")  # within the quiet moment: not asked at all
    wall_clock.within(time.monotonic() - started, 0.2, "the read in the quiet moment")
    phone_server.dead = False

    link = control.PhoneLink(lambda: "blackmagic", lambda: WIFI_ONLY, lambda: True, log=lambda line: None)
    link.phone = control.Phone(phone_server.base)
    link.ACT_WAIT_S = 0.3
    held, release = threading.Event(), threading.Event()

    def busy():  # the link's own thread, talking to a phone that went quiet
        with link.lock:
            held.set()
            release.wait(5)

    threading.Thread(target=busy, daemon=True).start()
    held.wait(2)
    try:
        started = time.monotonic()
        with pytest.raises(control.PhoneError, match="isn't answering right now"):
            link.act("record", "off")
        wall_clock.within(time.monotonic() - started, 1.0, "the control")
    finally:
        release.set()
    time.sleep(0.1)
    assert link.act("zoom", "0.2")["zoom"] == 0.2  # and once free, controls work


def test_bonjour_denial_is_visible_without_disclosing_network_details(control, monkeypatch):
    process = SimpleNamespace(returncode=1, terminate=lambda: None,
                              communicate=lambda **kw: ('', 'DNSServiceBrowse failed -65570 PolicyDenied SENTINEL-private-host'))
    monkeypatch.setattr(control.subprocess, 'Popen', lambda *a, **kw: process)
    with pytest.raises(control.PhoneError, match='Local Network') as failure:
        control.discover()
    assert 'SENTINEL' not in str(failure.value)


def test_reset_does_not_claim_it_restarted_an_unreachable_phone(control):
    link = control.PhoneLink(lambda: 'blackmagic', lambda: WIFI_ONLY, lambda: False, log=lambda _: None)
    assert link.restart_stream() is False
    assert 'reachable' in link.error


def test_discovery_start_failure_is_not_an_absent_phone(control, monkeypatch):
    def missing(*args, **kwargs):
        raise FileNotFoundError('SENTINEL-private-path')
    monkeypatch.setattr(control.subprocess, 'Popen', missing)
    with pytest.raises(control.DiscoveryError, match='discovery') as failure:
        control.discover()
    assert 'SENTINEL' not in str(failure.value)


def test_found_phone_that_refuses_control_has_specific_recovery(control, monkeypatch):
    monkeypatch.setattr(control, 'usb_address', lambda: None)
    monkeypatch.setattr(control, 'discover', lambda: [{'base': 'https://SENTINEL.local:4444', 'host': 'SENTINEL.local', 'model': ''}])
    def refused(self, route):
        raise control.PhoneError('SENTINEL sensitive upstream error')
    monkeypatch.setattr(control.Phone, 'get', refused)
    with pytest.raises(control.DiscoveryError, match='found') as failure:
        control.find_phone()
    assert 'SENTINEL' not in str(failure.value)


def test_reset_reports_partial_failure_after_restarting_only_receiver(bridge_module, tmp_path):
    bridge = make_bridge(bridge_module, tmp_path, SILENT, live_kind='blackmagic')
    starts = []
    bridge._launch = lambda *args, **kwargs: starts.append(True)
    bridge.health = lambda: {'state': 'waiting'}
    bridge.phone_link = SimpleNamespace(restart_stream=lambda: False, error='Control unavailable')
    result = bridge.reset_camera()
    assert starts == [True] and result['ok'] is False
    assert 'phone stream did not' in result['error']


def test_discovery_receipt_distinguishes_no_service_from_failed_control(control, monkeypatch):
    monkeypatch.setattr(control, 'usb_address', lambda: None)
    monkeypatch.setattr(control, 'discover', lambda: [])
    facts = []
    assert control.find_phone(observe=facts.append) is None
    assert facts[-1]['stage'] == 'no-camera-found'
    assert facts[-1]['candidates'] == 0
    assert all(set(row) == {'stage', 'candidates', 'elapsedMs', 'updatedAt'} for row in facts)


def test_bonjour_resolves_the_complete_camera_instance_name(control, monkeypatch):
    name = "Will's iPhone Camera"
    commands = []
    def run(command, seconds):
        commands.append(command)
        if '-B' in command:
            return f'17:00:00.000 Add 2 14 local. _http._tcp. {name}\n'
        if command[2] == name:
            return f'{name} can be reached at camera.local.:4444\n capabilities=cameraControl model=iPhone\\ 17\\ Pro'
        return ''
    monkeypatch.setattr(control, '_run_for', run)
    phones = control.discover()
    assert len(phones) == 1
    assert phones[0]['name'] == name
    assert commands[1][2] == name


def test_same_mac_wrong_channel_port_requires_settings_without_touching_phone(control, phone_server):
    phone = control.Phone(phone_server.base)
    beta = {k: v.replace(":9710", ":9810") for k, v in ADDRESSES.items()}
    sent = len(phone_server.puts)
    with pytest.raises(control.NeedsSettings):
        phone.stream_to("wifi", beta)
    assert len(phone_server.puts) == sent
    phone_server.platform_servers = [
        {"server": "Stable", "url": ADDRESSES["wifi"]},
        {"server": "Broken", "url": "srt://127.0.0.1:invalid"},
        {"server": "Beta", "url": beta["wifi"]},
    ]
    assert phone.stream_to("wifi", beta) == ["streaming to Beta", "stream started"]
    assert phone_server.state["/livestreams/0/activePlatform"]["server"] == "Beta"
