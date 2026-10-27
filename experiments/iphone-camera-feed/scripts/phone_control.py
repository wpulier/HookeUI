"""The phone's camera, controlled from the Mac: Blackmagic Camera's REST API (`iphone_bridge_server.py`).

Blackmagic Camera (iOS, 3.5) serves Blackmagic's camera-control REST API when its Settings > Network Access >
HTTP Server is on: https://<phone>:4444/control/api/v1 (a self-signed certificate), announced over Bonjour as
an `_http._tcp` service whose TXT record says `capabilities=cameraControl`. With it the bridge can do everything
the stream needs without anyone touching the phone: fix focus and exposure (the phone's hunting and auto-exposure
are what spoil a microscope view), set the frame rate, choose where the phone streams (This Mac over Wi-Fi or
over USB) and start or stop the stream.

Only calls measured on the phone are used (2026-10-11, iPhone 17 Pro, Blackmagic Camera 3.5.200045):
- GET  /lens/focus/autoFocus -> {"mode", "enabled", ...}; PUT {"enabled": false} stops autofocus (mode OneShot).
- PUT  /lens/focus {"normalised"}, PUT /lens/focus/doAutoFocus {"position": {"x", "y"}} (one focus, then held).
- GET/PUT /video/autoExposure {"mode": Off|Continuous|OneShot, "type"}; /video/iso {"iso"} (supportedISOs);
  /video/shutter {"shutterSpeed"} (supportedShutters); /video/whiteBalance {"whiteBalance"} 2500-10000 K;
  /video/whiteBalanceTint {"whiteBalanceTint"} -50..50.
- GET/PUT /system/format: PUT needs the whole format back (frameRate as a string, "30"), else 500.
- GET /livestreams/0 (status, bitrate, effectiveVideoFormat), PUT /livestreams/0/start|stop.
- GET /livestreams/platforms/{name}, PUT /livestreams/0/activePlatform {platform, server, quality}: only a server
  the platform lists. A "Custom" server with a URL stopped the app's HTTP server, and a server it doesn't list left
  its stream unable to connect ("Interrupted"), both 2026-10-11: never sent.
- PUT /livestreams/customPlatforms/{filename} answered 204 and changed nothing (the app reads its platform files
  when they are imported), so the settings file is imported once by hand (the camera page's QR code fetches it from
  the settings server on port 8767; or AirDrop).
- PUT /livestreams/0/start started the stream; nothing here stops and restarts a stream in a loop.
- PUT /livestreams/0/activePlatform with another quality (even one the platform lists, its "Microscope" profile)
  stopped the app's HTTP server (2026-10-11): the quality is never changed here, only chosen in the app.

Standard library only: the bridge runs on the system Python.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import re
import ssl
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from xml.sax.saxutils import escape

API_PATH = "/control/api/v1"
PLATFORM_FILE = "MicroscopeMind SRT"  # the app names a file by the service and its protocol
PLATFORM_NAME = "MicroscopeMind SRT"
SERVER_WIFI = "This Mac (Wi-Fi)"
SERVER_USB = "This Mac (USB)"
SERVERS = {"wifi": SERVER_WIFI, "usb": SERVER_USB}
# Every address the phone's Personal Hotspot can give this Mac over the cable (172.20.10.2-14): the settings file lists a
# server for each, so whichever it gets is there (2026-10-12: the file named .2, the Mac had .3, and the cable "had no
# server").
USB_HOSTS = tuple(f"172.20.10.{n}" for n in range(2, 15))
# A stream quality that crashed Blackmagic Camera 3.5.20 every time its stream started, from the bridge or the app's own
# switch (2026-10-12, 15 crash reports, one stack): the bridge never starts a stream at it.
UNSAFE_QUALITIES = ("Microscope", "Steady", "Steady low latency")  # each crashed the app as its stream started
# The quality the settings file offers, in Blackmagic's own form (resolutions named 2160p, 1080p, 720p, as its streaming
# files do; ours named them HD and 4K, and every one of ours crashed the app as its stream started).
STREAM_QUALITY = "Standard"
STREAM_CONFIGS = (("2160p", 20_000_000), ("1080p", 8_000_000), ("720p", 4_000_000))
STREAM_FPS = ("24", "25", "30", "50", "60")


def proven_lenses(app_version: str | None) -> list[int]:
    """Focal lengths the lens test switched to on this phone (blackmagic-api.json `lenses`): only those are offered."""
    from phone_lab import load_manifest

    entry = (load_manifest().get("versions") or {}).get(app_version or "") or {}
    return [int(f) for f in entry.get("lenses") or []]


def proven_write(app_version: str | None, route: str) -> bool:
    """Whether the Phone check proved this write safe on this app version (the controls offer only those)."""
    from phone_lab import load_manifest, safe_writes

    return route in safe_writes(load_manifest(), app_version)


def unsafe_qualities(app_version: str | None) -> list[str]:
    """Qualities the bridge never starts a stream at: those known to crash, and those this phone was seen crash on."""
    from phone_lab import crashed_qualities, load_manifest

    return list(dict.fromkeys([*UNSAFE_QUALITIES, *crashed_qualities(load_manifest(), app_version)]))
# Personal Hotspot over the cable: the phone is the network's router, always this address.
USB_PHONE_HOST = "172.20.10.1"
USB_NETWORK = ipaddress.ip_network("172.20.10.0/28")
TIMEOUT_S = 3.0
# Frames the live view is built for (contract/overlay.json's ideal is 30 fps; 24 looked choppy and adds delay).
MICROSCOPE_FPS = "30"


def _context() -> ssl.SSLContext:
    """The phone's certificate is its own (self-signed); the address is one found on the local network."""
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


class PhoneError(Exception):
    pass


class DiscoveryError(PhoneError):
    """A Mac-side discovery failure, distinct from a phone that did not answer."""


class NeedsSettings(PhoneError):
    """The app has no server for this Mac on the chosen link: the settings file must be imported (once)."""


class Phone:
    """One phone's control API. `base` is https://host:port (http:// for tests)."""

    QUIET_AFTER_FAILURE_S = 5.0

    def __init__(self, base: str, name: str = "", via: str = "wifi", unchecked: bool = False) -> None:
        self.base = base.rstrip("/")
        # The Phone check alone writes without the manifest's leave: it is what proves a write safe.
        self.unchecked = unchecked
        self.app_version: str | None = None  # read from /system/product, for the manifest of what is safe
        parts = urllib.parse.urlsplit(self.base)
        self.name = name or parts.hostname or "phone"
        self.via = via  # how the bridge reaches it: "wifi" (Bonjour) or "usb" (Personal Hotspot)
        self._secure = parts.scheme == "https"
        self._host, self._port = parts.hostname or "", parts.port or (443 if self._secure else 80)
        self._context = _context() if self._secure else None
        # One kept-alive connection: the phone streams video over the same Wi-Fi, and a TLS handshake per request
        # (a dozen a poll) is what the control channel must not cost it.
        self._connection: http.client.HTTPConnection | None = None
        self._lock = threading.Lock()
        # After the phone didn't answer, it isn't asked again for a moment: an unreachable phone made every question
        # wait out its timeouts in turn, and a status read of a dozen questions held the link for over a minute while a
        # Stop recording waited behind it (live 2026-10-13).
        self._quiet_until = 0.0

    # --- transport -----------------------------------------------------------------------------------
    def _connect(self) -> http.client.HTTPConnection:
        if self._connection is None:
            if self._secure:
                self._connection = http.client.HTTPSConnection(self._host, self._port, timeout=TIMEOUT_S, context=self._context)
            else:
                self._connection = http.client.HTTPConnection(self._host, self._port, timeout=TIMEOUT_S)
        return self._connection

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    def _request(self, method: str, path: str, body: object = None, content_type: str = "application/json"):
        data = None
        if body is not None:
            data = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": content_type} if data is not None else {}
        with self._lock:
            if time.monotonic() < self._quiet_until:
                raise PhoneError("the phone did not answer a moment ago")
            for attempt in (1, 2):  # a kept-alive connection the phone closed is opened again, once
                try:
                    connection = self._connect()
                    connection.request(method, f"{API_PATH}{path}", body=data, headers=headers)
                    response = connection.getresponse()
                    raw, status = response.read(), response.status
                    break
                except (http.client.HTTPException, OSError, TimeoutError) as error:
                    if self._connection is not None:
                        self._connection.close()
                    self._connection = None
                    if attempt == 2:
                        self._quiet_until = time.monotonic() + self.QUIET_AFTER_FAILURE_S
                        raise PhoneError(f"the phone did not answer ({error})") from None
        text = raw.decode("utf-8", "replace")
        try:
            value = json.loads(text) if text.strip() else None
        except ValueError:
            value = text
        if status >= 400:
            message = value.get("message") if isinstance(value, dict) else text[:120]
            raise PhoneError(f"{method} {path}: {status} {message}")
        return value

    def get(self, path: str):
        value = self._request("GET", path)
        if path == "/system/product" and isinstance(value, dict):
            self.app_version = str(value.get("softwareVersion") or "")
        return value

    def put(self, path: str, body: object = None, content_type: str = "application/json"):
        """A write, only when the Phone check proved it safe on this version of Blackmagic Camera (blackmagic-api.json;
        streaming's own start, stop and server choice always). Every crash so far came from a write nobody had tried."""
        if not self.unchecked:
            from phone_lab import allowed, load_manifest

            if self.app_version is None:
                self.get("/system/product")
            if not allowed(load_manifest(), self.app_version, path):
                raise PhoneError(f"{path} isn't proven safe on this version of Blackmagic Camera ({self.app_version or 'unknown'}): "
                                 "run the Phone check on the camera page")
        return self._request("PUT", path, body if body is not None else "", content_type)

    # --- reading --------------------------------------------------------------------------------------
    def status(self, full: bool = True, previous: dict | None = None) -> dict[str, object]:
        """What the console shows and the diagnosis reads: one GET per fact, each optional. Without `full`, only
        what the link acts on (the stream, focus and exposure held, frame rate); the rest is kept from `previous`."""

        def read(path: str):
            try:
                return self.get(path)
            except PhoneError:
                return None

        previous = previous or {}
        product = read("/system/product") or {}
        if not product:  # the app doesn't answer: nothing else is asked (each question would wait out its timeouts)
            return {"reachable": False, "checkedAt": time.time()}
        if not full and product and previous.get("reachable"):
            stream = read("/livestreams/0") or {}
            autofocus = read("/lens/focus/autoFocus") or {}
            exposure = read("/video/autoExposure") or {}
            return {**previous, "autofocus": bool(autofocus.get("enabled")) if autofocus else previous.get("autofocus"),
                    "autoExposure": exposure.get("mode", previous.get("autoExposure")),
                    "streaming": stream.get("status") == "Streaming", "streamStatus": stream.get("status"),
                    "bitrate": stream.get("bitrate"), **self._recording(read), "checkedAt": time.time()}
        power = read("/camera/power") or {}
        form = read("/system/format") or {}
        autofocus = read("/lens/focus/autoFocus") or {}
        focus = read("/lens/focus") or {}
        exposure = read("/video/autoExposure") or {}
        iso = read("/video/iso") or {}
        shutter = read("/video/shutter") or {}
        balance = read("/video/whiteBalance") or {}
        tint = read("/video/whiteBalanceTint") or {}
        stream = read("/livestreams/0") or {}
        platform = read("/livestreams/0/activePlatform") or {}
        zoom = read("/lens/zoom") or {}
        stabilization = read("/lens/opticalImageStabilization") or {}
        media = ((read("/media/workingset") or {}).get("workingset") or [{}])[0]
        batteries = power.get("batteries") or [{}]
        return {
            "reachable": bool(product),
            "name": product.get("deviceName") or self.name,
            "model": product.get("productName"),
            "app": product.get("softwareVersion"),
            "via": self.via,
            "battery": batteries[0].get("chargeRemainingPercent"),
            "fps": form.get("frameRate"),
            "autofocus": bool(autofocus.get("enabled")) if autofocus else None,
            "focus": focus.get("normalised"),
            "autoExposure": exposure.get("mode"),
            "iso": iso.get("iso"),
            "shutter": shutter.get("shutterSpeed"),
            "whiteBalance": balance.get("whiteBalance"),
            "tint": tint.get("whiteBalanceTint"),
            "streaming": stream.get("status") == "Streaming",
            "streamStatus": stream.get("status"),
            "bitrate": stream.get("bitrate"),
            "streamFormat": stream.get("effectiveVideoFormat"),
            "platform": platform.get("platform"),
            "server": platform.get("server"),
            "quality": platform.get("quality"),
            "focalLength": zoom.get("focalLength"),
            "stabilization": next((v for v in stabilization.values() if isinstance(v, bool)), None),
            "zoom": zoom.get("normalised"),
            "clips": media.get("clipCount"),
            "recordTimeLeftS": media.get("remainingRecordTime"),
            "spaceLeftBytes": media.get("remainingSpace"),
            **self._recording(read),
            "checkedAt": time.time(),
        }

    @staticmethod
    def _recording(read) -> dict[str, object]:
        """Whether the phone is recording, its clip and timecode (reads only; measured safe 2026-10-12)."""
        record = read("/transports/0/record") or {}
        timecode = read("/transports/0/timecode") or {}
        return {"recording": bool(record.get("recording")), "clipName": record.get("clipName"),
                "timecode": timecode.get("display")}

    def locked(self, status: dict | None = None) -> bool:
        """Focus and exposure held, at the microscope's frame rate: nothing on the phone changes the view."""
        status = status or self.status()
        return status.get("autofocus") is False and status.get("autoExposure") == "Off" and status.get("fps") in (MICROSCOPE_FPS, "30.00")

    # --- the microscope's settings ----------------------------------------------------------------------
    def lock_for_microscope(self) -> list[str]:
        """Focus where it is, exposure where it is, white balance where it is, 30 fps. Returns what changed."""
        changed: list[str] = []
        autofocus = self.get("/lens/focus/autoFocus") or {}
        if autofocus.get("enabled"):
            self.put("/lens/focus/autoFocus", {"enabled": False})
            changed.append("autofocus off")
        exposure = self.get("/video/autoExposure") or {}
        if exposure.get("mode") not in ("Off", None):
            self.put("/video/autoExposure", {"mode": "Off", "type": exposure.get("type", "")})
            changed.append("auto exposure off")
        if self.set_fps(MICROSCOPE_FPS):
            changed.append(f"{MICROSCOPE_FPS} fps")
        return changed

    def set_autofocus(self, on: bool) -> None:
        self.put("/lens/focus/autoFocus", {"enabled": bool(on)})

    def focus_once(self, x: float = 0.5, y: float = 0.5) -> None:
        """Focus once on a point (the middle by default), then hold it."""
        self.put("/lens/focus/doAutoFocus", {"position": {"x": _unit(x), "y": _unit(y)}})

    def set_focus(self, normalised: float) -> None:
        self.put("/lens/focus", {"normalised": _unit(normalised)})

    def set_auto_exposure(self, on: bool) -> None:
        exposure = self.get("/video/autoExposure") or {}
        self.put("/video/autoExposure", {"mode": "Continuous" if on else "Off", "type": exposure.get("type", "")})

    def set_iso(self, iso: int) -> None:
        supported = (self.get("/video/supportedISOs") or {}).get("supportedISOs") or []
        self.put("/video/iso", {"iso": _nearest(int(iso), supported)})

    def set_shutter(self, speed: int) -> None:
        supported = (self.get("/video/supportedShutters") or {}).get("shutterSpeeds") or []
        self.put("/video/shutter", {"shutterSpeed": _nearest(int(speed), supported)})

    def set_white_balance(self, kelvin: int) -> None:
        self.put("/video/whiteBalance", {"whiteBalance": max(2500, min(10000, int(kelvin)))})

    def set_tint(self, tint: int) -> None:
        self.put("/video/whiteBalanceTint", {"whiteBalanceTint": max(-50, min(50, int(tint)))})

    def white_balance_once(self) -> None:
        """White balance and tint set once from what the camera sees now, then held (the app's API has no continuous
        auto white balance: /video/autoWhiteBalance isn't there). On a microscope: on an empty, bright part of the slide."""
        self.put("/video/whiteBalance/doAuto", "")

    def set_zoom(self, normalised: float) -> None:
        """Zoom within the lens in use (0: widest, 1: closest), as the app's zoom dial."""
        self.put("/lens/zoom", {"normalised": _unit(normalised)})

    def set_lens(self, focal_length: int) -> None:
        """Choose a lens by its focal length (13, 24, 48, 100, 200 mm on an iPhone 17 Pro), as the app's lens buttons."""
        self.put("/lens/zoom", {"focalLength": int(focal_length)})

    def set_stabilization(self, on: bool) -> None:
        """Lens stabilization: off is steadier on a fixed mount (the microscope's eyepiece)."""
        current = self.get("/lens/opticalImageStabilization") or {}
        key = next((k for k, v in current.items() if isinstance(v, bool)), "enabled")
        self.put("/lens/opticalImageStabilization", {**current, key: bool(on)})

    def set_recording(self, on: bool) -> None:
        """Record on the phone (its own file, at its own quality), or stop."""
        self.put("/transports/0/record", {"recording": bool(on)})

    def set_fps(self, fps: str) -> bool:
        """The whole format back with the frame rate changed (the app refuses a partial one). False when it
        already was."""
        form = dict(self.get("/system/format") or {})
        if str(form.get("frameRate")) in (str(fps), f"{float(fps):.2f}"):
            return False
        form["frameRate"] = str(fps)
        form.pop("resolutionDescriptor", None)
        self.put("/system/format", form)
        return True

    # --- the stream -----------------------------------------------------------------------------------
    def platform_servers(self) -> dict[str, str]:
        try:
            platform = self.get(f"/livestreams/platforms/{urllib.parse.quote(PLATFORM_NAME)}") or {}
        except PhoneError:
            return {}
        return {str(server.get("server")): str(server.get("url")) for server in platform.get("servers") or []}

    def server_for(self, link: str, addresses: dict[str, str | None], servers: dict[str, str] | None = None) -> str | None:
        """Match the listener endpoint, including its port: another app on this Mac is not this receiver."""
        servers = self.platform_servers() if servers is None else servers
        wanted = urllib.parse.urlsplit(addresses.get(link) or "")
        if not wanted.hostname:
            return None
        endpoint = (wanted.scheme, wanted.hostname, wanted.port)
        for name, url in servers.items():
            try:
                candidate = urllib.parse.urlsplit(url)
                matches = (candidate.scheme, candidate.hostname, candidate.port) == endpoint
            except ValueError:
                continue  # a malformed saved server cannot qualify this receiver
            if matches:
                return name
        return None

    def stream_to(self, link: str, addresses: dict[str, str | None]) -> list[str]:
        """Make the phone stream to this Mac over `link` ("wifi" or "usb"): select the platform's server for
        that link (only one the app lists) and start the stream if it isn't running. Raises NeedsSettings
        when the app has no server for this Mac on that link (the settings file must be imported)."""
        if not addresses.get(link):
            raise PhoneError("the USB link is not up" if link == "usb" else "this Mac has no Wi-Fi address")
        servers = self.platform_servers()
        target = self.server_for(link, addresses, servers)
        if target is None:
            raise NeedsSettings(f"Blackmagic Camera has no server for this Mac over {'USB' if link == 'usb' else 'Wi-Fi'}")
        steps: list[str] = []
        current = self.get("/livestreams/0/activePlatform") or {}
        # The quality stays the one chosen in the app: selecting a quality through the API ("Microscope", the settings
        # file's low-latency profile, which the app listed) stopped the app's HTTP server (2026-10-11). It is
        # chosen by hand in the app (Settings › Live Stream › Quality) and sent back unchanged here.
        quality = current.get("quality") or "Streaming High"
        changed = current.get("platform") != PLATFORM_NAME or current.get("server") != target
        if changed:
            self.put("/livestreams/0/activePlatform", {"platform": PLATFORM_NAME, "server": target, "quality": quality})
            steps.append(f"streaming to {target}")
            # The app keeps calling the old address until its stream starts again (2026-10-11: "Connecting" for
            # minutes after a switch): one stop and start, tied to this change, never repeated.
            if (self.get("/livestreams/0") or {}).get("status") in ("Streaming", "Connecting", "Interrupted"):
                self.put("/livestreams/0/stop")
                time.sleep(0.8)
        if (self.get("/livestreams/0") or {}).get("status") not in ("Streaming", "Connecting"):
            self._refuse_unsafe_quality(quality)
            self.put("/livestreams/0/start")
            steps.append("stream started")
        return steps

    def _refuse_unsafe_quality(self, quality: str | None = None) -> None:
        if quality is None:
            quality = (self.get("/livestreams/0/activePlatform") or {}).get("quality")
        if self.app_version is None:
            self.get("/system/product")  # the version a learned crash belongs to
        if quality in unsafe_qualities(self.app_version):
            raise PhoneError(f"its quality {quality} crashes Blackmagic Camera when the stream starts: choose {STREAM_QUALITY} "
                             "in the app (Settings › Live Stream › Quality)")

    def restart_stream(self) -> list[str]:
        """Reset camera, for a Blackmagic source: the phone's stream stopped and started once (never in a loop)."""
        self._refuse_unsafe_quality()
        if (self.get("/livestreams/0") or {}).get("status") in ("Streaming", "Connecting", "Interrupted"):
            self.put("/livestreams/0/stop")
            time.sleep(0.8)
        self.put("/livestreams/0/start")
        return ["stream restarted"]

    def stop_stream(self) -> bool:
        if (self.get("/livestreams/0") or {}).get("status") == "Streaming":
            self.put("/livestreams/0/stop")
            return True
        return False


# The Mac's address on the iPhone's Personal Hotspot when it is the only one joined (the hotspot's first address).
USB_MAC_DEFAULT = "172.20.10.2"


CRASH_NAME = re.compile(r"BlackmagicCam-(\d{4}-\d\d-\d\d-\d{6})\.ips")


def app_crash_times(timeout: float = 20.0) -> list[float]:
    """When Blackmagic Camera crashed on the paired iPhone, from the phone's own crash reports (Xcode's devicectl,
    over the cable or the network). Empty when there is no paired phone or no devicectl."""
    def run(*args: str) -> str:
        try:
            return subprocess.run(["xcrun", "devicectl", *args], capture_output=True, text=True, timeout=timeout).stdout
        except (OSError, subprocess.SubprocessError):
            return ""

    devices = re.findall(r"([0-9A-F]{8}-[0-9A-F]{16}) \(UDID\)\s+available \(paired\)", run("list", "devices"))
    times: list[float] = []
    for udid in devices[:1]:
        for stamp in CRASH_NAME.findall(run("device", "info", "files", "--device", udid, "--domain-type", "systemCrashLogs")):
            try:
                times.append(time.mktime(time.strptime(stamp, "%Y-%m-%d-%H%M%S")))
            except ValueError:
                continue
    return sorted(times)


def platform_xml(addresses: dict[str, str | None], port: int = 9710) -> str:
    """The Blackmagic streaming file to import once: a server per link, Wi-Fi first, and one quality, Standard, in
    Blackmagic's own form (STREAM_CONFIGS). USB is always listed (at the hotspot's usual first address while the cable
    link isn't up), so one import covers both."""
    addresses = {**addresses}
    if not addresses.get("usb"):
        addresses["usb"] = f"srt://{USB_MAC_DEFAULT}:{port}"
    listed = [(SERVER_WIFI, addresses.get("wifi"))] + [
        (f"{SERVER_USB} .{host.rsplit('.', 1)[1]}", f"srt://{host}:{port}") for host in USB_HOSTS]
    servers = "".join(
        f"\n            <server>\n                <name>{escape(name)}</name>\n"
        f"                <url>{escape(url)}</url>\n            </server>"
        for name, url in listed if url
    )
    configs = "".join(
        f'\n                <config resolution="{resolution}" fps="{fps}">\n'
        f"                    <bitrate>{bitrate}</bitrate>\n                    <audio-bitrate>128000</audio-bitrate>\n"
        "                    <keyframe-interval>2</keyframe-interval>\n                </config>"
        for resolution, bitrate in STREAM_CONFIGS for fps in STREAM_FPS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" ?>\n<streaming>\n    <service>\n        <name>MicroscopeMind</name>\n'
        f"        <servers>{servers}\n        </servers>\n"
        f"        <profiles>\n            <profile>\n                <name>{STREAM_QUALITY}</name>{configs}\n"
        "            </profile>\n        </profiles>\n    </service>\n</streaming>\n"
    )


def _unit(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _nearest(value: int, supported: list) -> int:
    return min(supported, key=lambda option: abs(float(option) - value)) if supported else value


# --- finding the phone ------------------------------------------------------------------------------

def usb_address() -> str | None:
    """This Mac's address on the iPhone's Personal Hotspot over USB (172.20.10.x), when the cable link is up."""
    try:
        output = subprocess.run(["ifconfig"], capture_output=True, text=True, timeout=3, check=False).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    for match in re.finditer(r"inet (\d+\.\d+\.\d+\.\d+)", output):
        address = ipaddress.ip_address(match.group(1))
        if address in USB_NETWORK and str(address) != USB_PHONE_HOST:
            return str(address)
    return None


def _run_for(command: list[str], seconds: float) -> str:
    """dns-sd never exits by itself: run it for a moment and keep what it said."""
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    except OSError as exc:
        raise DiscoveryError("Camera discovery could not start. Copy the camera report and check for a Hooke update.") from exc
    timer = threading.Timer(seconds, process.terminate)
    timer.start()
    try:
        output, error = process.communicate()
    finally:
        timer.cancel()
    # Native macOS permission refusals must not look like an absent phone. DNS-SD
    # writes failures to either stream; never retain its hostnames or TXT records.
    reported = (output or "") + (error or "")
    if any(code in reported for code in ('PolicyDenied', 'NoAuth', '-65570', '-65555')):
        raise DiscoveryError('Local Network access was refused. In System Settings › Privacy & Security › Local Network, allow this Hooke app, then Reconnect.')
    if process.returncode not in (0, -15) and not output:
        raise DiscoveryError('Camera discovery could not start. Copy the camera report and check for a Hooke update.')
    return output or ""


def discover(seconds: float = 8.0) -> list[dict[str, str]]:
    """Phones running Blackmagic Camera with its HTTP Server on, found over Bonjour on the local network."""
    # Keep the first local-network operation alive long enough for macOS to present
    # its permission alert (Apple TN3179 / FB16131937). This runs on the phone thread.
    browse = _run_for(["dns-sd", "-B", "_http._tcp", "local."], seconds)
    # dns-sd prints the complete instance name after the service-type column.
    # Splitting on whitespace lost all but the last word of a phone's name.
    names = sorted({match.group(1).strip() for line in browse.splitlines()
                    if (match := re.search(r"\bAdd\s+\d+\s+\d+\s+local\.\s+_http\._tcp\.\s+(.+)$", line))})
    phones = []
    for name in names:
        resolved = _run_for(["dns-sd", "-L", name, "_http._tcp", "local."], seconds)
        if "capabilities=cameraControl" not in resolved:
            continue
        reach = re.search(r"can be reached at (\S+?)\.?:(\d+)", resolved)
        if not reach:
            continue
        host, port = reach.group(1).rstrip("."), reach.group(2)
        model = re.search(r"model=(.+?)(?: [a-z_]+=|$)", resolved)
        phones.append({"name": name, "host": host, "port": port, "base": f"https://{host}:{port}",
                       "model": model.group(1).replace("\\ ", " ") if model else ""})
    return phones


def find_phone(prefer: str = "wifi", observe=None) -> Phone | None:
    """The phone to control: over the USB link when it is up and preferred (or nothing else answers), else the
    first one Bonjour finds."""
    began = time.monotonic()
    def stage(name: str, count: int = 0) -> None:
        if observe:
            observe({"stage": name, "candidates": count, "elapsedMs": round((time.monotonic() - began) * 1000),
                     "updatedAt": time.time()})
    stage("discovering")
    usb = usb_address()
    candidates: list[Phone] = []
    if usb:
        candidates.append(Phone(f"https://{USB_PHONE_HOST}:4444", via="usb"))
    candidates += [Phone(found["base"], found["model"] or found["host"], via="wifi") for found in discover()]
    if prefer == "wifi":
        candidates.sort(key=lambda phone: phone.via != "wifi")
    stage("checking-control" if candidates else "no-camera-found", len(candidates))
    for phone in candidates:
        try:
            phone.get("/system/product")
            stage("control-ready", len(candidates))
            return phone
        except PhoneError:
            continue
    if candidates:
        stage("control-unavailable", len(candidates))
        raise DiscoveryError("Blackmagic Camera was found, but its control server did not answer. Check Local Network access for Hooke in System Settings, keep HTTP Server on in Blackmagic Camera, then Reconnect.")
    return None


# --- the bridge's link to the phone -------------------------------------------------------------------

STREAM_KINDS = ("blackmagic",)


def link_for(addresses: dict) -> str:
    """The link the phone streams over, chosen by itself: the cable when it shares a network (this Mac has an address
    on the phone's hotspot over USB), otherwise Wi-Fi; nobody is asked to choose (2026-10-12)."""
    return "usb" if addresses.get("usb") else "wifi"


class PhoneLink:
    """The bridge's view of the phone, kept by its own thread so no request and no frame ever waits on the
    phone. It finds the phone (Bonjour, or the USB link), reads its state, and gets the picture live by itself:
    with a Blackmagic source chosen and no frames arriving, it brings the phone's settings up to date, aims its
    stream at this Mac over the chosen link and starts it (at most every RESTART_GAP_S); with the iPhone
    (Continuity) chosen it stops the phone's stream once, so the phone can be locked.

    It asks little of the phone while the picture flows (every LIVE_POLL_S), more while the console is open or
    there is no picture (every POLL_S): the live feed is never slowed by the control channel."""

    POLL_S = 2.0
    LIVE_POLL_S = 10.0
    DISCOVER_S = 6.0
    RESTART_GAP_S = 12.0

    LISTENER_GAP_S = 15.0
    # A stream that has just started gets this long to deliver its first frames before anything is restarted: at
    # 09:41 on 2026-10-12 the listener was replaced the moment the phone began streaming, cutting the session before
    # its first frame, and the phone (still saying "Streaming") never reconnected by itself.
    STREAM_GRACE_S = 20.0
    RECOVER_GAP_S = 60.0  # at most one recovery (fresh listener, then the phone's stream again) a minute
    CRASH_CHECK_S = 30.0  # how often the phone's crash reports are read while a Blackmagic source is chosen
    CRASH_SHOWN_S = 600.0  # a crash is told for this long
    START_CRASH_S = 30.0  # a crash this soon after the bridge started the stream is that quality's
    CABLE_RETRY_S = 60.0  # a cable the app had no server for is tried again after this long

    def __init__(self, wanted, addresses, frames_flowing, log=print, restart_listener=None) -> None:
        self.restart_listener = restart_listener  # () -> None: the bridge starts its SRT listener again
        self.last_listener_restart = float("-inf")
        self.streaming_since: float | None = None  # when the phone was first seen streaming with no picture here
        self.phone_restart_due = False
        self.paused = False  # the Phone check is running: the link leaves the phone (and the stream) to it
        self.link: str | None = None  # the link the phone streams over now ("wifi" or "usb")
        self.cable_without_server: tuple[str | None, float] | None = None  # a cable address with no server, and when
        # The quality the bridge last started the phone's stream at, and when (wall): a crash report within
        # START_CRASH_S of it means that quality crashes the app, and it is never started again (learned on the phone).
        self.started: tuple[str, float] | None = None
        self.wanted = wanted  # () -> "continuity" | "wifi" | "usb"
        self.addresses = addresses  # () -> {"wifi": url | None, "usb": url | None}
        self.frames_flowing = frames_flowing  # () -> bool: fresh frames from the chosen source
        self.log = log
        self.lock = threading.RLock()  # one conversation with the phone at a time
        self.phone: Phone | None = None
        self.state: dict[str, object] = {"reachable": False}
        self.cached_addresses: dict[str, str | None] = {}
        self.error = ""
        self.last_action: dict[str, object] | None = None
        self.discovery_issue = ""
        self.discovery: dict[str, object] = {}
        self.last_discover_at = 0.0
        self.last_poll_at = 0.0
        self.last_stream_try = 0.0
        self.stopped_for_continuity = False
        self.needs_settings = False
        self.retarget = False
        self.listener_due = False
        self.started_wall = time.time()
        self.last_crash_check = 0.0
        self.app_crashed_at: float | None = None
        self.crash_times = app_crash_times  # () -> [epoch seconds]; a test gives its own
        self.watching_until = 0.0
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        if self.thread is None or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._loop, daemon=True, name="phone-link")
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()

    def watch(self, seconds: float = 20.0) -> None:
        """The console is open: keep the phone's state fresh even while the iPhone source is on."""
        self.watching_until = time.monotonic() + seconds
        self.wake.set()

    def source_changed(self) -> None:
        self.last_stream_try = 0.0
        self.stopped_for_continuity = False
        self.error = ""
        # A link chosen while the picture flows (Wi-Fi to USB, or back) still moves the phone to that link's
        # server: frames arriving over the other link are not the choice (2026-10-11, first live switch).
        self.retarget = True
        self.wake.set()

    def snapshot(self) -> dict[str, object]:
        # Read without `lock`: the bridge's health calls this holding its own lock, and the link holds `lock` for
        # the whole of a conversation with the phone (seconds). Each field is replaced whole, never changed in place.
        crashed = self.app_crashed_at
        recent = crashed is not None and time.time() - crashed < self.CRASH_SHOWN_S
        return {**self.state, "error": self.error, "lastAction": self.last_action, "needsSettings": self.needs_settings,
                "discoveryIssue": self.discovery_issue, "discovery": dict(self.discovery),
                "addresses": dict(self.cached_addresses), "link": self.link,
                "lenses": proven_lenses((self.phone.app_version if self.phone else None) or self.state.get("app")),
                "autoWhiteBalance": proven_write((self.phone.app_version if self.phone else None) or self.state.get("app"),
                                                 "/video/whiteBalance/doAuto"),
                "appCrashedAt": time.strftime("%H:%M:%S", time.localtime(crashed)) if recent else None}

    def _observe_discovery(self, facts: dict[str, object]) -> None:
        self.discovery = facts  # bounded facts only: no hostnames, addresses or provider errors

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.tick()
                self._watch_crashes()
            except Exception as exc:  # the link must outlive any one failure
                self.error = f"{type(exc).__name__}: {exc}"
            self.wake.wait(self.POLL_S)
            self.wake.clear()

    def tick(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if self.paused:
            return
        kind = self.wanted()
        self.cached_addresses = self.addresses()
        watching = now < self.watching_until
        live = self.frames_flowing()
        if kind not in STREAM_KINDS and not watching and not self.state.get("streaming"):
            return  # the iPhone source, nobody looking, and the phone is not streaming: leave the phone alone
        interval = self.LIVE_POLL_S if live and not watching else self.POLL_S
        self.listener_due = False
        if live:
            # The picture is arriving: whatever an earlier attempt concluded (no server for a link, a failed start) no
            # longer holds (2026-10-12: "isn't set up in the app" shown over a live picture, left from a pinned USB link).
            self.streaming_since = None
            self.needs_settings = False
            self.error = ""
        with self.lock:
            self._talk(kind, now, watching, live, interval)
        if self.listener_due:
            # The bridge is called back only once the link has let go of its lock: the bridge's own lock is taken
            # there, and its health takes that lock before reading the link (2026-10-12: both waited for good).
            self.listener_due = False
            self.restart_listener()
            self.log("Phone: listening again for the phone's stream")
            if self.phone_restart_due:
                # Then the phone's stream, so it calls the fresh listener: it doesn't reconnect a lost session itself.
                self.phone_restart_due = False
                if self.restart_stream():
                    self.log("Phone: its stream restarted, to call the fresh listener")
                else:
                    self.log("Phone: stream restart was not accepted; see camera recovery")
            self.last_action = {"action": "listening again for the phone's stream", "at": time.time()}

    def _watch_crashes(self, now: float | None = None) -> None:
        """Read the phone's crash reports (slow: a subprocess, never under `lock`) while Blackmagic is the source."""
        now = time.monotonic() if now is None else now
        if self.wanted() not in STREAM_KINDS or now - self.last_crash_check < self.CRASH_CHECK_S:
            return
        self.last_crash_check = now
        newest = max([t for t in self.crash_times() if t >= self.started_wall - 60], default=None)
        if newest is not None and newest != self.app_crashed_at:
            self.app_crashed_at = newest
            self.log(f"Phone: Blackmagic Camera crashed at {time.strftime('%H:%M:%S', time.localtime(newest))} "
                     "(its crash report on the phone)")
            started = self.started
            if started and started[1] - 1 <= newest <= started[1] + self.START_CRASH_S:
                from phone_lab import record_crashed_quality

                version = (self.phone.app_version if self.phone else None) or str(self.state.get("app") or "")
                if version:
                    record_crashed_quality(version, started[0])
                self.started = None
                self.log(f"Phone: its stream quality {started[0]} crashed the app as the stream started; it won't be used again")

    def restart_stream(self) -> bool:
        """Return whether the phone actually accepted a stream restart."""
        with self.lock:
            if self.phone is None:
                self.error = "Blackmagic Camera is not reachable yet. Keep HTTP Server on and check Hooke’s Local Network access, then Reconnect."
                return False
            try:
                self.phone.restart_stream()
                self.last_action = {"action": "restarted the phone's stream", "at": time.time()}
                self.error = ""
                return True
            except PhoneError as error:
                self.error = str(error)
                return False

    def _talk(self, kind: str, now: float, watching: bool, live: bool, interval: float) -> None:
        """One conversation with the phone, under `lock`."""
        if self.phone is None and now - self.last_discover_at >= self.DISCOVER_S:
            self.last_discover_at = now
            try:
                self.phone = find_phone(prefer=link_for(self.cached_addresses) if kind in STREAM_KINDS else "wifi", observe=self._observe_discovery)
                self.discovery_issue = ""
            except DiscoveryError as exc:
                self.discovery_issue = str(exc)
                raise
        if self.phone is None:
            self.state = {"reachable": False}
            return
        if now - self.last_poll_at >= interval or not live:
            self.last_poll_at = now
            state = self.phone.status(full=watching or not self.state.get("reachable"), previous=self.state)
            if not state.get("reachable"):
                self.phone, self.state = None, {"reachable": False}
                return
            self.state = state
        if kind in STREAM_KINDS:
            link = self._link()
            if self.link is not None and link != self.link:
                # The cable came or went: the phone streams over the other link now, as if it had been chosen.
                self.retarget = True
                self.streaming_since = None
                self.log(f"Phone: streaming over {'the cable' if link == 'usb' else 'Wi-Fi'} now")
            self.link = link
        if kind in STREAM_KINDS and (self.retarget or (not live and now - self.last_stream_try >= self.RESTART_GAP_S)):
            self.retarget = False
            self.last_stream_try = now
            self._aim(self._link(), now)
        elif kind not in STREAM_KINDS and self.state.get("streaming") and not self.stopped_for_continuity:
            # Another source chosen (the iPhone, or another camera): the phone's stream stops once, so the phone can
            # be locked for Continuity Camera and doesn't stream to nothing.
            self.stopped_for_continuity = True
            try:
                self.phone.stop_stream()
                self.last_action = {"action": "stopped the phone's stream for the iPhone source", "at": time.time()}
                self.state = {**self.state, "streaming": False, "streamStatus": "Idle"}
            except PhoneError as error:
                self.error = str(error)

    def _link(self) -> str:
        """The link to stream over now: link_for, except that a cable whose address the app had no server for leaves
        Blackmagic on Wi-Fi for CABLE_RETRY_S (then the cable is tried again: a settings file imported meanwhile has
        the server, 2026-10-12)."""
        link = link_for(self.cached_addresses)
        missing = self.cable_without_server
        if (link == "usb" and missing and missing[0] == self.cached_addresses.get("usb")
                and time.monotonic() - missing[1] < self.CABLE_RETRY_S):
            return "wifi"
        return link

    def _aim(self, link: str, now: float | None = None) -> None:
        """The picture is not arriving: aim the phone's stream at this Mac over `link` and start it if it
        isn't streaming. Never stops a stream (a stop-and-start loop broke the phone's stream on 2026-10-11)."""
        if self.wanted() not in STREAM_KINDS:
            # The source changed while this conversation with the phone went on (it takes seconds): the phone is not
            # started for a source no longer chosen (live 2026-10-13: switched to the iPhone, then the stream started
            # over Wi-Fi 2 s later, and Blackmagic Camera kept the camera Continuity needed).
            return
        self.needs_settings = False
        if not self.cached_addresses.get(link):
            self.error = "the USB link is not up" if link == "usb" else "this Mac has no Wi-Fi address"
            return
        now = time.monotonic() if now is None else now
        try:
            steps = self.phone.stream_to(link, self.cached_addresses)
            self.error = ""
            if steps:
                self.streaming_since = now  # just started: its grace begins now
                if "stream started" in steps:
                    quality = (self.phone.get("/livestreams/0/activePlatform") or {}).get("quality")
                    self.started = (str(quality), time.time()) if quality else None
            elif self.streaming_since is None:
                self.streaming_since = now  # first seen streaming without a picture here
            elif (self.restart_listener and now - self.streaming_since >= self.STREAM_GRACE_S
                  and now - self.last_listener_restart >= self.RECOVER_GAP_S):
                # Streaming for the whole grace, yet nothing arrives: the listener can hold a stale session (one caller
                # at a time), and the phone won't reconnect by itself. A fresh listener, then the phone's stream again,
                # done by `tick` once the link lets go of its lock.
                self.last_listener_restart = now
                self.streaming_since = now
                self.listener_due = True
                self.phone_restart_due = True
            if steps:
                self.last_action = {"action": ", ".join(steps), "at": time.time()}
                self.log(f"Phone: {', '.join(steps)}")
        except NeedsSettings as error:
            if link == "usb" and self.cached_addresses.get("wifi"):
                # The app has no server for the cable's address: the automatic link uses Wi-Fi instead, without
                # calling it a setup problem (the picture works). Tried again when the cable's address changes.
                self.cable_without_server = (self.cached_addresses.get("usb"), time.monotonic())
                self.log("Phone: the app has no server for the cable's address; streaming over Wi-Fi")
                self._aim("wifi", now)
                return
            self.needs_settings = True
            self.error = str(error)
        except PhoneError as error:
            self.error = str(error)
            self.log(f"Phone: could not start its stream: {error}")

    ACT_WAIT_S = 10.0

    def act(self, action: str, value: str | None = None) -> dict[str, object]:
        """The console's camera controls, done at once, the phone's state read back after. A control waits at most
        ACT_WAIT_S for the link (busy talking to a phone that went quiet), then says so instead of hanging."""
        if not self.lock.acquire(timeout=self.ACT_WAIT_S):
            raise PhoneError("the phone isn't answering right now (the Mac is still waiting on it): try again in a moment")
        try:
            return self._act_locked(action, value)
        finally:
            self.lock.release()

    def _act_locked(self, action: str, value: str | None) -> dict[str, object]:
        if self.phone is None:
            self.phone = find_phone(prefer=link_for(self.cached_addresses) if self.wanted() in STREAM_KINDS else "wifi", observe=self._observe_discovery)
        if self.phone is None:
            raise PhoneError("Blackmagic Camera isn't reachable: open it on the phone with its HTTP Server on")
        phone = self.phone
        done = ""
        if action == "lock":
            done = ", ".join(phone.lock_for_microscope()) or "already locked"
        elif action == "autofocus":
            phone.set_autofocus(value in ("1", "true", "on"))
        elif action == "focus-once":
            phone.focus_once()
        elif action == "focus":
            phone.set_focus(float(value))
        elif action == "exposure":
            phone.set_auto_exposure(value in ("1", "true", "on"))
        elif action == "iso":
            phone.set_iso(int(float(value)))
        elif action == "shutter":
            phone.set_shutter(int(float(value)))
        elif action == "white-balance":
            phone.set_white_balance(int(float(value)))
        elif action == "tint":
            phone.set_tint(int(float(value)))
        elif action == "white-balance-auto":
            phone.white_balance_once()
        elif action == "fps":
            phone.set_fps(str(value))
        elif action == "zoom":
            phone.set_zoom(float(value))
        elif action == "lens":
            if phone.app_version is None:
                phone.get("/system/product")  # which app version: lenses are proven per version
            if int(float(value)) not in proven_lenses(phone.app_version):
                raise PhoneError("switching lenses from the Mac isn't proven on this phone yet: run the lens test")
            phone.set_lens(int(float(value)))
        elif action == "stabilization":
            phone.set_stabilization(value in ("1", "true", "on"))
        elif action == "record":
            phone.set_recording(value in ("1", "true", "on"))
        elif action == "start":
            kind = self.wanted()
            addresses = self.addresses()
            done = ", ".join(phone.stream_to(link_for(addresses) if kind in STREAM_KINDS else "wifi", addresses)) or "already streaming"
        elif action == "stop":
            phone.stop_stream()
        else:
            raise PhoneError(f"unknown camera control {action!r}")
        self.last_action = {"action": done or action, "value": value, "at": time.time()}
        self.state = phone.status()
        return self.snapshot()
