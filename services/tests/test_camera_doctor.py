"""The camera's checklist (experiments/iphone-camera-feed/scripts/camera_doctor.py, the bridge's /api/doctor).

Every failure met live is a case: from the facts the bridge read, the checklist must name the first thing wrong
and the one thing to do, so anyone with only the camera page gets the picture back.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

import pytest

from tests.test_bridge import SCRIPTS, SILENT, bridge_module, cleanup, make_bridge, serve  # noqa: F401


@pytest.fixture()
def doctor(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import importlib

    import camera_doctor

    return importlib.reload(camera_doctor)


LIVE = {"running": True, "devicePresent": True, "captureFrames": 900, "lastFrameAge": 0.03, "fps": 30.0,
        "frameSize": [1280, 720], "targetSize": [1280, 720], "frozenFor": 0.0, "readers": 1, "loadPerCore": 0.4}
PHONE = {"reachable": True, "name": "A", "addresses": {"wifi": "srt://192.0.2.10:9710", "usb": None},
         "server": "This Mac (Wi-Fi)", "quality": "Streaming High", "streamStatus": "Streaming", "streaming": True}


def first(doctor, **facts):
    result = doctor.checklist(facts)
    failing = next((item for item in result["items"] if item["id"] == result["first"]), None)
    return result, failing


def test_a_live_iphone_is_all_green(doctor):
    result, failing = first(doctor, source="continuity", **LIVE)
    assert result["live"] and failing is None and result["headline"] == "Live"


def test_a_frozen_picture_is_not_live_and_says_reset(doctor):
    """2026-10-12: Continuity repeated one picture at 30 fps while health said live."""
    result, failing = first(doctor, source="continuity", **{**LIVE, "frozenFor": 4.2})
    assert not result["live"] and failing["id"] == "moving" and failing["action"] == "reset"
    assert "for 4 s" in failing["evidence"] and "4.2 s" in failing["detail"]


def test_the_wrong_size_is_named(doctor):
    """2026-10-12: 1920x1080 frames slowed the studio to 19.5 fps."""
    result, failing = first(doctor, source="continuity", **{**LIVE, "frameSize": [1920, 1080]})
    assert failing["id"] == "size" and "1920x1080" in failing["evidence"] and failing["action"] == "reset"


def test_desk_view_or_blackmagic_holding_the_iphone(doctor):
    _, failing = first(doctor, source="continuity", **{**LIVE, "deskView": True})
    assert failing["id"] == "free" and "Desk View" in failing["fix"]
    _, failing = first(doctor, source="continuity", **{**LIVE, "blackmagicOpen": True})
    assert failing["id"] == "free" and "Close Blackmagic Camera" in failing["fix"]


def test_a_listed_but_silent_iphone_says_reconnect(doctor):
    _, failing = first(doctor, source="continuity", running=True, devicePresent=True, captureFrames=0, askedFor=21)
    assert failing["id"] == "arriving" and failing["action"] == "reconnect" and "21 s ago" in failing["evidence"]


def test_a_missing_iphone_and_camera_permission(doctor):
    _, failing = first(doctor, source="continuity", devicePresent=False)
    assert failing["id"] == "listed" and failing["action"] == "reset"
    _, failing = first(doctor, source="continuity", **{**LIVE, "permissionDenied": True})
    assert failing["id"] == "permission" and "Privacy & Security" in failing["fix"]


def test_blackmagic_closed_or_crashed(doctor):
    _, failing = first(doctor, source="blackmagic", phone={"reachable": False, "error": "the phone did not answer"})
    assert failing["id"] == "app" and "Open Blackmagic Camera" in failing["fix"]
    result, failing = first(doctor, source="blackmagic", appCrashedAt="01:17:44", phone={"reachable": False})
    assert failing["id"] == "crash" and "01:17:44" in failing["evidence"] and not result["live"]


def test_settings_missing_and_the_crashing_quality(doctor):
    _, failing = first(doctor, source="blackmagic", phone={**PHONE, "needsSettings": True})
    assert failing["id"] == "settings" and failing["action"] == "settings-file"
    _, failing = first(doctor, source="blackmagic", phone={**PHONE, "quality": "Microscope", "streamStatus": "Idle"})
    assert failing["id"] == "quality" and "choose Standard" in failing["fix"] and failing["action"] == "settings-file"


def test_a_streaming_phone_whose_picture_doesnt_arrive_says_reconnect(doctor):
    _, failing = first(doctor, source="blackmagic", phone=PHONE, running=True, captureFrames=0, askedFor=30)
    assert failing["id"] == "arriving" and failing["action"] == "reconnect"
    _, failing = first(doctor, source="blackmagic", phone={**PHONE, "streamStatus": "Idle", "streaming": False})
    assert failing["id"] == "streaming" and failing["action"] == "start-stream"


def test_a_waiting_feed_reader_does_not_claim_to_show_a_picture(doctor):
    result, _ = first(doctor, source='blackmagic', phone=PHONE, running=True,
                      captureFrames=0, askedFor=30, readers=1)
    reading = next(i for i in result['items'] if i['id'] == 'app-reading')
    assert 'showing the picture' not in reading['title']
    assert 'connected' in reading['title']


def test_blackmagic_live_and_the_report(doctor):
    result, failing = first(doctor, source="blackmagic", phone={**PHONE, "addresses": {"usb": "srt://172.20.10.3:9710"}}, **LIVE)
    assert result["live"] and failing is None
    text = doctor.report(result, [{"at": "01:20:00", "text": "Live source: Blackmagic over USB"}])
    assert "Camera: blackmagic - live" in text and "Live source: Blackmagic over USB" in text


def test_a_busy_mac_is_named_without_calling_the_camera_down(doctor):
    result, failing = first(doctor, source="continuity", **{**LIVE, "loadPerCore": 1.8})
    assert failing["id"] == "room" and result["live"]


def test_jpeg_size_is_read_from_the_header(bridge_module, tmp_path):
    for size in ("1280x720", "1920x1080"):
        path = tmp_path / f"{size}.jpg"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={size}", "-frames:v", "1", str(path)],
                       check=True)
        width, height = (int(n) for n in size.split("x"))
        assert bridge_module.jpeg_size(path.read_bytes()) == (width, height)
    assert bridge_module.jpeg_size(b"not a jpeg") is None


FROZEN = (
    "import sys,time,subprocess; sys.argv; "
    "frame=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','testsrc2=size=1280x720','-frames:v','1','-f','mjpeg','-'],"
    "capture_output=True).stdout; "
    "[ (sys.stdout.buffer.write(frame), sys.stdout.buffer.flush(), time.sleep(1/30)) for _ in range(300) ]  # {marker}"
)


def test_the_bridge_knows_a_frozen_picture_and_its_size(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, FROZEN)
    cleanup.append(bridge)
    bridge._launch("test", replace=False)
    deadline = time.monotonic() + 15
    facts = {}
    while time.monotonic() < deadline:
        facts = bridge.doctor_facts()
        if facts["frozenFor"] >= 1.5:
            break
        time.sleep(0.2)
    assert facts["frameSize"] == [1280, 720] and facts["frozenFor"] >= 1.5
    doctor = bridge.doctor()
    assert doctor["first"] == "moving" and not doctor["live"]
    assert any("Started the capture" in event["text"] for event in doctor["events"])


def test_doctor_endpoint_and_reset_needs_its_header(bridge_module, tmp_path, cleanup):
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    port: list[int] = []
    server = serve(bridge_module, bridge, port)
    try:
        body = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port[0]}/api/doctor", timeout=5))
        assert body["source"] == "continuity" and body["items"] and "report" in body
        request = urllib.request.Request(f"http://127.0.0.1:{port[0]}/api/restart?reset=1", method="POST")
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)
        assert refused.value.code == 403
    finally:
        server.shutdown()


def test_a_frozen_iphone_picture_heals_itself_once_a_minute(bridge_module, tmp_path, cleanup, monkeypatch):
    """Demo night: the frozen picture needed a hand. Now the bridge resets the camera by itself, at most once a minute."""
    monkeypatch.setattr(bridge_module, "FROZEN_HEAL_S", 0.5)
    bridge = make_bridge(bridge_module, tmp_path, FROZEN)
    cleanup.append(bridge)
    resets = []
    bridge.reset_camera = lambda: resets.append(time.monotonic())
    bridge._launch("test", replace=False)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and not resets:
        bridge._monitor_tick()
        time.sleep(0.2)
    time.sleep(0.2)
    assert len(resets) == 1 and any("froze" in event["text"] for event in bridge.events)
    for _ in range(5):
        bridge._monitor_tick()
    time.sleep(0.2)
    assert len(resets) == 1  # not again within the minute


def test_an_updated_app_asks_for_the_phone_check_without_stopping_the_picture(doctor):
    """Writes are proven per app version: after an update the settings wait for the Phone check, the picture doesn't."""
    phone = {**PHONE, "app": "3.6.0"}
    result, failing = first(doctor, source="blackmagic", phone=phone, provenVersions=["3.5.200045"], **LIVE)
    assert failing["id"] == "proven" and failing["action"] == "phone-check" and "3.6.0" in failing["detail"] and "updated" in failing["evidence"]
    assert result["live"] and result["headline"].startswith("Live over Wi-Fi, but")  # the picture works; the one thing to do is named
    result, failing = first(doctor, source="blackmagic", phone={**PHONE, "app": "3.5.200045"}, provenVersions=["3.5.200045"], **LIVE)
    assert failing is None and result["live"]


def test_polling_the_camera_leaves_the_log_alone(bridge_module, tmp_path, cleanup, capsys):
    """The camera page and the studio poll several times a second: that never fills the bridge's log (2026-10-12: 80%
    of a 22 MB log). A failed answer is still logged."""
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    real_log = bridge_module.IPhoneBridgeHandler.log_message  # serve() silences it for the other tests
    port: list[int] = []
    server = serve(bridge_module, bridge, port)
    bridge_module.IPhoneBridgeHandler.log_message = real_log
    try:
        capsys.readouterr()
        for _ in range(40):
            for path in ("/api/health", "/api/cadence", "/api/doctor", "/feed"):
                urllib.request.urlopen(f"http://127.0.0.1:{port[0]}{path}", timeout=5).read()
        quiet = capsys.readouterr().err
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port[0]}/nowhere", timeout=5)
        loud = capsys.readouterr().err
    finally:
        server.shutdown()
    assert quiet.count("GET ") == 0, quiet[:300]
    assert "GET /nowhere" in loud


def test_blackmagic_says_which_way_the_phone_reaches_this_mac(doctor):
    phone = {**PHONE, "addresses": {"wifi": "srt://192.0.2.10:9710", "usb": "srt://172.20.10.3:9710"}}
    result, failing = first(doctor, source="blackmagic", link="usb", phone=phone, **LIVE)
    item = next(i for i in result["items"] if i["id"] == "link")
    assert result["live"] and item["ok"] and item["evidence"] == "over the cable" and "172.20.10.3" in item["detail"]
    result, _ = first(doctor, source="blackmagic", link="wifi", phone={**PHONE}, **LIVE)
    item = next(i for i in result["items"] if i["id"] == "link")
    assert item["ok"] and item["evidence"] == "over Wi-Fi" and "192.0.2.10" in item["detail"]
    assert "Hotspot" not in json.dumps(result)  # never mentioned when it works


def test_a_new_setup_starts_on_blackmagic_when_launched_so(bridge_module, tmp_path):
    args = make_bridge(bridge_module, tmp_path, SILENT, default_source="blackmagic").args
    assert bridge_module.initial_live_source(args) == ("blackmagic", None)
    args.default_source = "continuity"
    assert bridge_module.initial_live_source(args) == ("continuity", None)


def test_a_live_blackmagic_says_which_way_it_comes(doctor):
    result, failing = first(doctor, source="blackmagic", link="usb", phone={**PHONE, "addresses": {"usb": "srt://172.20.10.3:9710"}}, **LIVE)
    assert failing is None and result["via"] == "the cable" and result["headline"] == "Live, over the cable"


def test_the_settings_qr_code_reads_back_and_the_phone_gets_only_the_file(bridge_module, tmp_path, cleanup):
    """The camera page shows a QR code the phone scans to fetch the Blackmagic settings file (no AirDrop, which
    Android doesn't have). Its codes are decoded here with OpenCV's reader; the phone's server serves that file only."""
    import sys

    import cv2
    import numpy as np

    sys.path.insert(0, str(SCRIPTS))
    import qr

    for text in ("http://192.0.2.10:8767/MicroscopeMind.xml", "http://10.0.0.5:8767/" + "a" * 70):
        modules = np.array(qr.matrix(text), dtype=np.uint8)
        image = cv2.resize(np.pad((1 - modules) * 255, 4, constant_values=255).astype(np.uint8), None, fx=8, fy=8,
                           interpolation=cv2.INTER_NEAREST)
        assert cv2.QRCodeDetector().detectAndDecode(image)[0] == text
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    server = bridge_module.start_settings_server(bridge.args, port=0)
    try:
        port = server.server_address[1]
        body = urllib.request.urlopen(f"http://127.0.0.1:{port}/MicroscopeMind.xml", timeout=5).read().decode()
        assert "<name>MicroscopeMind</name>" in body and "<name>Standard</name>" in body
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=5)
    finally:
        server.shutdown()


def test_the_checklist_speaks_plainly_and_keeps_the_details_for_whoever_helps(bridge_module):
    """What the page shows is plain words (Will, 2026-10-12: "uneven" confused him; so did an IP address, a server
    name, a version number and "load per core"); Copy report keeps the technical facts."""
    import camera_doctor

    facts = {"source": "blackmagic", "link": "usb", "captureFrames": 900, "lastFrameAge": 0.02, "fps": 30.1,
             "frameSize": [1280, 720], "targetSize": [1280, 720], "frozenFor": 0.0, "readers": 3, "loadPerCore": 0.5,
             "provenVersions": ["3.5.200045"],
             "phone": {"reachable": True, "name": "A", "app": "3.5.200045", "server": "This Mac (USB) .3",
                       "quality": "Streaming High", "streamStatus": "Streaming",
                       "addresses": {"usb": "srt://172.20.10.3:9710", "wifi": "srt://192.0.2.10:9710"}}}
    result = camera_doctor.checklist(facts)
    shown = " ".join(item["title"] + " " + item["evidence"] for item in result["items"])
    for jargon in ("172.20", "API", "server", "3.5.200045", "1280x720", "per core", "reader", "Streaming High", "fps",
                   "frames differ"):
        assert jargon not in shown, jargon
    report = camera_doctor.report(result, [])
    for detail in ("172.20.10.3", "3.5.200045", "load 0.5 per core", "Streaming High"):
        assert detail in report, detail


def test_the_bridge_serves_its_camera_page_and_nothing_else_of_its_folder(bridge_module, tmp_path, cleanup):
    """The camera page is the studio's build (apps/studio/dist-camera), served at /camera/; the old address redirects
    there. Before 2026-10-12 every file in the bridge's folder was served, recordings and the manifest included."""
    page = tmp_path / "dist-camera"
    (page / "assets").mkdir(parents=True)
    (page / "camera.html").write_text("<!doctype html><title>Camera</title>")
    (page / "assets" / "camera-1.js").write_text("console.log(1)")
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge.args.page_dir = str(page)
    ports: list[int] = []
    server = serve(bridge_module, bridge, ports)

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None

    opener = urllib.request.build_opener(NoRedirect)

    def get(path: str):
        try:
            with opener.open(f"http://127.0.0.1:{ports[0]}{path}", timeout=5) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    try:
        status, headers, body = get("/camera/")
        assert status == 200 and body.startswith(b"<!doctype html>") and headers["Content-Type"].startswith("text/html")
        status, headers, _ = get("/camera/assets/camera-1.js")
        assert status == 200 and "javascript" in headers["Content-Type"]
        for old in ("/", "/web-camera-check.html", "/camera"):
            status, headers, _ = get(old)
            assert status == 302 and headers["Location"] == "/camera/", old
        for private in ("/README.md", "/blackmagic-api.json", "/output/recordings/x.mp4", "/scripts/phone_control.py",
                        "/camera/../README.md", "/camera/%2e%2e/blackmagic-api.json"):
            assert get(private)[0] == 404, private
        bridge.args.page_dir = str(tmp_path / "not-built")
        status, _, body = get("/camera/")
        assert status == 200 and b"make start" in body  # not built yet: says how
    finally:
        server.shutdown()


def test_a_picture_arriving_before_the_phone_answers_is_live(doctor):
    """Live 2026-10-13, after a restart: Blackmagic's picture was on the camera page while the phone link was still
    finding the app's API, and the headline said "Not live: Blackmagic Camera isn't open". The picture is the stronger
    fact: the app is "not checked yet" until it answers, never "isn't open" over a live picture."""
    unreachable = {"reachable": False, "addresses": PHONE["addresses"]}
    result, failing = first(doctor, source="blackmagic", phone=unreachable, **LIVE)
    app = next(item for item in result["items"] if item["id"] == "app")
    assert result["live"] and failing is None and app["ok"] is None and "picture arrives" in app["evidence"]
    assert {"arriving", "size", "moving"} <= {item["id"] for item in result["items"]}
    result, failing = first(doctor, source="blackmagic", phone=unreachable)  # no picture either: the app is the problem
    assert not result["live"] and failing["id"] == "app"


def test_the_link_is_said_even_when_something_else_needs_attention(doctor):
    """Live 2026-10-13: plugged in, the headline said only "Live, but this Mac is busy"."""
    phone = {**PHONE, "addresses": {"wifi": PHONE["addresses"]["wifi"], "usb": "srt://172.20.10.3:9710"}}
    result, _ = first(doctor, source="blackmagic", link="usb", phone=phone, provenVersions=["3.5.200045"],
                      **{**LIVE, "loadPerCore": 1.8})
    assert result["headline"] == "Live over the cable, but this Mac is busy"


def test_a_picture_arriving_is_streaming_whatever_the_app_says(doctor):
    """Live 2026-10-13: after the cable was unplugged the picture came over Wi-Fi at 30 fps, smooth, while the app
    reported "Interrupted", then "Connecting", and the headline said "Not live: the phone isn't streaming"."""
    for status in ("Interrupted", "Connecting"):
        result, failing = first(doctor, source="blackmagic", phone={**PHONE, "streamStatus": status, "streaming": False},
                                provenVersions=["3.5.200045"], **LIVE)
        streaming = next(item for item in result["items"] if item["id"] == "streaming")
        assert result["live"] and failing is None and streaming["ok"] and status in streaming["detail"], status
    result, failing = first(doctor, source="blackmagic", phone={**PHONE, "streamStatus": "Interrupted", "streaming": False},
                            provenVersions=["3.5.200045"])  # and no picture: then it is the problem
    assert not result["live"] and failing["id"] == "streaming"


def test_once_set_up_the_app_closed_is_only_open_it(bridge_module, tmp_path, cleanup):
    """Live 2026-10-13: closing Blackmagic Camera brought back the first-time walkthrough, and reopening it said "set up"
    again. The bridge remembers, beside the source choice, that the app was set up on this Mac (seen live, or its
    settings imported and chosen), across restarts."""
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    bridge.args.source_file = str(tmp_path / "camera-source.json")
    closed = {"source": "blackmagic", "live": False, "items": [{"id": "app", "ok": False}]}
    assert bridge.blackmagic_set_up(closed) is False  # never set up: the walkthrough is right
    assert bridge.blackmagic_set_up({"source": "blackmagic", "live": True, "items": []}) is True
    later = make_bridge(bridge_module, tmp_path, SILENT)  # a restart
    cleanup.append(later)
    later.args.source_file = bridge.args.source_file
    assert later.blackmagic_set_up(closed) is True


def test_a_camera_just_chosen_is_starting_not_broken(doctor):
    """Will, 2026-10-13: switching to a camera showed "Not live", a red box and "unplug it" one second later: "it can't
    feel like you're causing a bug". For its first seconds a camera with no frame yet is starting: no fix, no red."""
    facts = {"source": "other", "otherDevice": "FaceTime HD Camera", "devicePresent": True, "running": True,
             "captureFrames": 0, "askedFor": 1.0}
    result, failing = first(doctor, **facts)
    arriving = next(item for item in result["items"] if item["id"] == "arriving")
    assert failing is None and arriving["ok"] is None and result["headline"] == "Starting the camera" and not result["live"]
    result, failing = first(doctor, **{**facts, "askedFor": 12.0})  # still nothing after its grace: now it is a problem
    assert failing["id"] == "arriving" and "plug it back in" in failing["fix"]
    # Blackmagic's stream gets the phone's own grace: starting for 20 s, its picture too.
    phone = {**PHONE, "streamStatus": "Idle", "streaming": False}
    result, failing = first(doctor, source="blackmagic", phone=phone, provenVersions=["3.5.200045"], running=True,
                            captureFrames=0, askedFor=8.0)
    assert failing is None and result["headline"] == "Starting the camera"


def test_not_connected_yet_is_a_guide_not_a_fault(doctor):
    """Will, 2026-10-13, switching to the iPhone: a red box "Not live: this Mac doesn't see the iPhone" — "you toggle a
    tab, that's not a fault ... nicely say: you need to connect, here's how". A camera not connected yet is a short
    guide (what to do, in order, and what the page waits for); red is kept for what broke."""
    result, failing = first(doctor, source="continuity", devicePresent=False, readers=1, loadPerCore=0.3)
    assert result["mode"] == "connect" and result["headline"] == "Connect your iPhone" and failing["id"] == "listed"
    assert "Not live" not in result["headline"] and result["waiting"] == "Waiting for the iPhone"
    assert any("Lock the iPhone" in step for step in result["steps"]) and any("Continuity Camera is on" in h for h in result["help"])
    result, _ = first(doctor, source="continuity", devicePresent=False, blackmagicOpen=True)
    assert result["mode"] == "connect" and result["headline"] == "Blackmagic Camera is using the iPhone's camera"

    result, _ = first(doctor, source="blackmagic", phone={"reachable": False, "addresses": PHONE["addresses"]})
    assert result["mode"] == "connect" and result["headline"] == "Open Blackmagic Camera on your iPhone"
    result, _ = first(doctor, source="other")
    assert result["mode"] == "connect" and result["headline"] == "Choose a camera"
    result, _ = first(doctor, source="other", otherDevice="USB Microscope", devicePresent=False)
    assert result["mode"] == "connect" and result["headline"] == "Plug in USB Microscope"

    # What broke stays a problem: a frozen picture, a crash, a denied permission.
    assert first(doctor, source="continuity", **{**LIVE, "frozenFor": 4.2})[0]["mode"] == "problem"
    assert first(doctor, source="continuity", **{**LIVE, "permissionDenied": True})[0]["mode"] == "problem"
    assert first(doctor, source="blackmagic", appCrashedAt="01:17:44", phone={"reachable": False})[0]["mode"] == "problem"
    assert first(doctor, source="continuity", **LIVE)[0]["mode"] == "live"


def test_the_guide_says_where_the_iphone_stops(doctor):
    """Will, 2026-10-13: "make it clearer to us what's happening, so what we build doesn't block users". Live that
    evening macOS listed the phone's microphone but no camera: found, its camera not offered. Each place it can stop
    has its own headline and steps, and the checks say which."""
    result, failing = first(doctor, source="continuity", devicePresent=False, usbCable=False, iphoneNearby=False)
    assert failing["id"] == "found" and failing["evidence"] == "not found" and result["headline"] == "Connect your iPhone"
    assert "cable" in result["steps"][0] and "its microphone not listed" in failing["detail"]
    result, failing = first(doctor, source="continuity", devicePresent=False, usbCable=False, iphoneNearby=True)
    found = next(item for item in result["items"] if item["id"] == "found")
    assert found["ok"] and found["evidence"] == "nearby, wirelessly" and failing["id"] == "listed"
    assert result["headline"] == "This Mac finds your iPhone, but not its camera yet" and result["mode"] == "connect"
    assert any("Continuity Camera off and on" in step for step in result["steps"])
    result, _ = first(doctor, source="continuity", devicePresent=False, usbCable=True, iphoneNearby=False)
    assert next(i for i in result["items"] if i["id"] == "found")["evidence"] == "on the cable"


def test_the_bridge_reads_the_iphone_nearby_from_its_microphone(bridge_module):
    listing = {"ok": False, "audioDevices": [{"name": "iPhoneWild Microphone"}], "videoDevices": [{"name": "FaceTime HD Camera"}]}
    assert bridge_module.iphone_nearby(listing, "iPhoneWild Camera") is True
    assert bridge_module.iphone_nearby({**listing, "audioDevices": []}, "iPhoneWild Camera") is False
    assert bridge_module.iphone_nearby(None, "iPhoneWild Camera") is None
    assert bridge_module.iphone_nearby(listing, "FaceTime HD Camera") is False


def test_no_camera_is_calm_and_footage_is_live(doctor):
    """No camera is the user's choice (Will, 2026-10-15): mode "off", nothing failing, never a guide to connect."""
    off = doctor.checklist({"source": "none"})
    assert off["mode"] == "off" and off["live"] is False and off["first"] is None and off["items"] == []
    assert off["headline"] == "No camera" and "pictures and videos" in off["line"]
    assert doctor.checklist({"source": "none", "footage": True})["mode"] == "live"  # a picture or video on stage plays


def test_missing_stream_protocol_is_app_failure_not_phone_setup(doctor, bridge_module, tmp_path, cleanup, monkeypatch):
    from stream_source import StreamSource
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    monkeypatch.setattr(bridge_module, 'STREAM_SOURCE', StreamSource('srt://0.0.0.0:9710?mode=listener'))
    bridge.args.live_kind = 'blackmagic'
    bridge.capture_stderr.append('Error opening input files: Protocol not found')
    facts = bridge.doctor_facts()
    result = doctor.checklist(facts)
    assert result['first'] == 'stream-runtime'
    assert result['mode'] == 'problem'
    failed = next(item for item in result['items'] if item['id'] == result['first'])
    assert 'Check for Updates' in failed['fix']
    assert failed['action'] is None  # Reconnecting the phone cannot install a decoder.
    diagnosis = bridge._diagnosis_locked('down', time.monotonic(), False, True)
    assert diagnosis['code'] == 'stream-protocol-unavailable'
    assert 'Hooke' in diagnosis['text']


def test_discovery_permission_has_mac_recovery_not_open_phone_instructions(doctor):
    phone = {'reachable': False, 'addresses': PHONE['addresses'],
             'discoveryIssue': 'Allow Hooke in Local Network, then Reconnect.'}
    result, failed = first(doctor, source='blackmagic', phone=phone)
    assert result['mode'] == 'problem' and failed['id'] == 'discovery'
    assert 'Local Network' in failed['fix']
    live, _ = first(doctor, source='blackmagic', phone=phone, **LIVE)
    assert live['live']  # already arriving video is stronger than control discovery


def test_channel_settings_qr_names_the_server_that_actually_serves_it(bridge_module, tmp_path, cleanup, monkeypatch):
    import socket
    import settings_server
    import stream_source
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    monkeypatch.setenv("MICROMIND_CAMERA_SETTINGS_PORT", str(port))
    monkeypatch.setenv("MICROMIND_BLACKMAGIC_PORT", "9810")
    monkeypatch.setattr(stream_source, "lan_address", lambda: "127.0.0.1")
    bridge = make_bridge(bridge_module, tmp_path, SILENT)
    cleanup.append(bridge)
    server = bridge_module.start_settings_server(bridge.args)
    try:
        assert server.server_address[1] == port
        url = settings_server.settings_url()
        assert url == f"http://127.0.0.1:{port}/MicroscopeMind.xml"
        body = urllib.request.urlopen(url, timeout=5).read().decode()
        assert ":9810" in body and ":9710" not in body
    finally:
        if server:
            server.shutdown()
            server.server_close()
