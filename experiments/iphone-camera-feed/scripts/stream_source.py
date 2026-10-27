"""The phone's picture from a network stream (`iphone_bridge_server.py --source-url URL`).

An app on the iPhone (Blackmagic Camera, or any app that streams SRT) sends its picture to the
Mac, and the bridge reads it with FFmpeg instead of opening an AVFoundation device. Like test
footage (`simulated_camera.py`), it replaces only the bridge's three device touch points:

- Device listing: there is no device to look for. The stream's "device" is the bridge's own
  listener, which is always there; nothing scans AVFoundation.
- Capture command: FFmpeg reads the URL with low-delay input flags and makes the same MJPEG
  frames at `--size` and `--fps` as the camera does (scaled to fit, the rest black, so the
  picture is never stretched).
- Continuity reset: never. A stream that stops is listened for again, and the phone's app
  calls back.

Everything else (the frame cache, stall monitor, recordings and their sidecars, test footage
through /feed) runs unchanged, and nothing downstream can tell where the picture came from.

The URL can hold a secret (an SRT passphrase or streamid, an RTMP stream key). Only `label`
and `redacted` are ever shown or logged, and `redact` cleans FFmpeg's own words (its errors
quote the URL).

Standard library only: the bridge runs on the system Python.
"""
from __future__ import annotations

import os
import socket
import time
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

# Query values that are secret, by key (SRT's passphrase and streamid; anything key- or
# password-like for other protocols).
SECRET_KEYS = {"passphrase", "streamid", "srt_streamid", "key", "token", "password", "pass", "secret",
               "user", "username", "auth"}
WILDCARD_HOSTS = {"", "0.0.0.0", "::", "[::]"}
# Only network video protocols: a URL from the console must never make FFmpeg read a file or another
# local resource (file:, pipe:, concat: ...).
# RTSP is how network (IP) cameras and microscope cameras on the network serve their picture: the bridge calls them.
SCHEMES = {"srt", "rtmp", "rtmps", "rtsp", "rtsps"}
# The listener the console offers when nothing else was configured: the phone's app (Blackmagic Camera,
# "Custom SRT server") calls the Mac on this port. SRT latency is in microseconds: 20 ms, as little buffer as a
# home Wi-Fi keeps clean (SRT uses the larger of its own and the phone's).
DEFAULT_URL = "srt://0.0.0.0:9710?mode=listener&latency=20000"
# Probing only decides the stream's size and codec before the first frame; it adds nothing to the
# delay of later frames. Small enough to start within a second of the caller connecting.
PROBE_SIZE = 500_000  # bytes
ANALYZE_DURATION_US = 1_000_000


def default_url() -> str:
    """The installed channel owns its default listener; source choice stays independent."""
    port = int(os.environ.get("MICROMIND_BLACKMAGIC_PORT", "9710"))
    if not 1 <= port <= 65535:
        raise ValueError("Blackmagic listener port must be between 1 and 65535")
    return f"srt://0.0.0.0:{port}?mode=listener&latency=20000"


class StreamSource:
    def __init__(self, url: str) -> None:
        parts = urlsplit(url)
        if not parts.scheme or "://" not in url:
            raise ValueError(f"--source-url needs a stream URL such as srt://0.0.0.0:9710?mode=listener, not {url[:12]!r}...")
        self.url = url
        self.scheme = parts.scheme.lower()
        if self.scheme not in SCHEMES:
            raise ValueError(f"a stream address starts with srt://, rtmp:// or rtsp://, not {self.scheme}://")
        self.host = parts.hostname or ""
        try:
            self.port = parts.port
        except ValueError as exc:
            raise ValueError(f"--source-url has a bad port: {exc}") from None
        query = parse_qsl(parts.query, keep_blank_values=True)
        # RTMP: FFmpeg listens only with its `-listen 1` option (a `listen=1` in the URL is taken as
        # part of the stream name), so the URL says it as SRT's mode=listener does and it is moved.
        self.input_options: list[str] = []
        if self.scheme in {"rtmp", "rtmps"} and any(k == "listen" and v == "1" for k, v in query):
            self.input_options = ["-listen", "1"]
            self.ffmpeg_url = urlunsplit(parts._replace(query=urlencode([(k, v) for k, v in query if k != "listen"])))
        else:
            self.ffmpeg_url = url
        if self.scheme.startswith("rtsp"):
            # Over TCP: a camera's UDP packets lost on Wi-Fi tear its picture; TCP costs a little delay, never pieces.
            self.input_options = ["-rtsp_transport", "tcp"]
        self._secrets = sorted(
            {value for key, value in query if key.lower() in SECRET_KEYS and value}
            | {unquote(value) for key, value in query if key.lower() in SECRET_KEYS and value}
            | {part for part in (parts.username, parts.password) if part}
            # An RTMP URL's stream key is its path after the app name.
            | ({segment for segment in parts.path.split("/")[2:] if segment} if self.scheme.startswith("rtmp") else set()),
            key=len, reverse=True,
        )

    # --- what may be shown -------------------------------------------------------------
    @property
    def protocol(self) -> str:
        return self.scheme.upper()

    @property
    def where(self) -> str:
        """Host and port, the host left out when the bridge listens on every address."""
        host = "" if self.host in WILDCARD_HOSTS else self.host
        return f"{host}:{self.port}" if self.port else host

    @property
    def label(self) -> str:
        """The source's name where a camera's name would go ("SRT stream :9710")."""
        return f"{self.protocol} stream {self.where}".strip()

    @property
    def redacted(self) -> str:
        """The URL without its path and query: scheme, host and port only."""
        parts = urlsplit(self.url)
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        return f"{self.scheme}://{host}{':' + str(self.port) if self.port else ''}"

    def redact(self, text: str) -> str:
        """FFmpeg's words with the URL and its secrets taken out."""
        for url in (self.url, self.ffmpeg_url):
            text = text.replace(url, self.redacted)
        for secret in self._secrets:
            if len(secret) >= 3:
                text = text.replace(secret, "***")
        return text

    @property
    def connect_to(self) -> str:
        """What to type into the phone's app: this Mac's address on the local network and the port.
        For a bridge listening on every address that is the LAN address; for a specific host, that host."""
        host = self.host
        if host in WILDCARD_HOSTS:
            host = lan_address() or "this Mac's IP address"
        return f"{self.scheme}://{host}:{self.port}" if self.port else f"{self.scheme}://{host}"

    def describe(self) -> dict[str, object]:
        return {"kind": "stream", "protocol": self.protocol, "url": self.redacted, "connectTo": self.connect_to,
                "secured": bool(self._secrets)}

    def waiting_message(self) -> str:
        return f"Listening for the {self.protocol} stream on {self.where or self.redacted}: nothing is streaming to it yet"

    def runtime_failure(self, stderr) -> dict | None:
        """Recognized decoder failures outrank phone setup guesses; never expose the URL."""
        if any("Protocol not found" in line for line in stderr):
            return {"code": "stream-protocol-unavailable",
                    "text": f"This Hooke installation cannot receive {self.protocol} video.",
                    "steps": ["Use HookeUI › Check for Updates to install a corrected build.",
                              "Copy the camera report if no update is offered. Reconnecting the phone will not fix this installation."]}
        return None

    # --- the three device touch points ----------------------------------------------------
    def device_snapshot(self, target_device: str) -> dict[str, object]:
        """No AVFoundation scan: the bridge's listener is the device, and it is always there."""
        return {
            "ok": True,
            "targetDevice": target_device,
            "videoDevices": [],
            "audioDevices": [],
            "checkedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "raw": "",
            "source": self.describe(),
        }

    def capture_command(self, ffmpeg: str, args) -> list[str]:
        """The URL in, the camera's frames out: the same MJPEG settings, size and rate as live.
        `-nostdin` stays second: `make stop` finds the bridge's FFmpegs by it."""
        width, height = args.size.split("x")
        fit = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1"
        )
        # As little delay as the Mac can add: no input buffering; the decoder working one frame at a time (frame
        # threading holds a frame per extra thread, ~33 ms each at 30 fps; slice threading holds none); every
        # decoded frame passed straight on (an fps filter can hold one back to pace them; the phone's app already
        # sends its own rate, set to 30 by the bridge). Software decoding, on purpose: VideoToolbox took 13% of a
        # core against 22%, but in this pipeline it delivered 40 of 300 frames with a stream of -12909 errors
        # and grey frames (2026-10-11); 22% of one core is under 2% of the Mac.
        return [
            ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "warning",
            "-fflags", "nobuffer+discardcorrupt", "-flags", "low_delay", "-thread_type", "slice",
            "-probesize", str(PROBE_SIZE), "-analyzeduration", str(ANALYZE_DURATION_US),
            *self.input_options,
            "-i", self.ffmpeg_url,
            "-an", "-vf", fit, "-fps_mode", "passthrough",
            "-q:v", str(args.jpeg_quality), "-vcodec", "mjpeg", "-f", "image2pipe", "pipe:1",
        ]

    def reset_agent(self) -> dict[str, object]:
        """A stream has no Continuity Camera to reset."""
        return {"attempted": False, "ok": False, "reason": "the picture comes from a network stream, not Continuity Camera"}


def lan_address() -> str | None:
    """This Mac's address on the local network (the one a phone on the same Wi-Fi reaches), without
    sending anything: a UDP socket "connected" to a public address only picks the outgoing interface."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))  # TEST-NET-1: never routed, nothing is sent
            address = probe.getsockname()[0]
    except OSError:
        return None
    return None if address.startswith("127.") or address == "0.0.0.0" else address
